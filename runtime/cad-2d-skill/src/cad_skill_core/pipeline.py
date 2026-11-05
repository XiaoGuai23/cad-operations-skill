"""End-to-end orchestration for validated 2D mechanical drawing tasks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import ctypes
from contextlib import ExitStack
import hashlib
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Mapping
from xml.etree import ElementTree

import ezdxf
from PIL import Image
from pypdf import PdfReader

from cad_skill_core.autocad import AutoCADRequest, execute_autocad
from cad_skill_core.autocad.executor import (
    _PublishedFile,
    _FileIdentity,
    _capture_file_identity,
    _file_identity_from_handle,
    _hold_output_directory,
    _open_windows_handle,
    _set_delete_disposition,
)
from cad_skill_core.documents.extract import extract_sources
from cad_skill_core.fonts import (
    ResolvedFont,
    build_font_search_roots,
    discover_fonts,
    load_font_policy,
    resolve_font,
)
from cad_skill_core.inspection import GenerationContext, run_repair_loop
from cad_skill_core.layout.material_binding import resolve_material_handles
from cad_skill_core.schema.io import load_spec
from cad_skill_core.schema.models import DrawingSpec
from cad_skill_core.standards.profile import load_standard

from .task import (
    PipelineInputError,
    PipelineOptions,
    PipelineResult,
    TaskPaths,
    prepare_task,
    require_task_file,
    validate_output_identity,
    validate_work_identity,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STANDARD_PATH = _PROJECT_ROOT / "knowledge" / "standards" / "gb-mechanical.yaml"
_FONT_POLICY_PATH = _PROJECT_ROOT / "knowledge" / "drawing-rules" / "font-policy.yaml"
_SOURCE_SUFFIXES = {".md", ".txt", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


class ArtifactValidationError(ValueError):
    """An artifact failed immutable, type-specific validation."""


@dataclass(frozen=True, slots=True)
class _SourceIdentity:
    device: int
    file_id: int
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class ValidatedArtifact:
    source: Path
    logical_name: str
    artifact_type: str
    sha256: str
    size: int
    source_identity: _SourceIdentity
    file_identity: _FileIdentity
    validation: str
    payload: bytes


@dataclass(slots=True)
class CanonicalMaterialization:
    """Immutable canonical payload evidence held against replacement on Windows."""

    artifact: ValidatedArtifact
    owned: bool
    reused: bool
    _holds: ExitStack

    @property
    def path(self) -> Path:
        return self.artifact.source

    def close(self) -> None:
        self._holds.close()

    def __del__(self) -> None:
        self.close()


class _PublicationStepError(PipelineInputError):
    def __init__(self, message: str, published: _PublishedFile | None = None):
        super().__init__(message)
        self.published = published


def run_task(task_dir: Path, options: PipelineOptions | None = None) -> PipelineResult:
    """Generate, inspect, repair, preview, and safely publish one task."""

    selected = options if options is not None else PipelineOptions()
    if not isinstance(selected, PipelineOptions):
        raise TypeError("options must be PipelineOptions")
    paths = prepare_task(task_dir)
    try:
        spec_path = _find_spec(paths)
    except PipelineInputError as error:
        _write_controlled_input_audit(paths, "INPUT_SPEC_MISSING", str(error))
        raise
    try:
        spec_source = _snapshot_artifact(
            spec_path, "json", _posix_relative(spec_path, paths.root)
        )
        with tempfile.TemporaryDirectory(prefix=".spec-", dir=paths.iterations) as temporary:
            immutable_spec = Path(temporary) / "drawing-spec.json"
            immutable_spec.write_bytes(spec_source.payload)
            spec = load_spec(immutable_spec)
    except Exception as error:
        _write_controlled_input_audit(
            paths,
            "INPUT_SPEC_INVALID",
            _sanitize_text(
                f"drawing specification is invalid: {type(error).__name__}: {error}",
                paths,
            ),
        )
        raise PipelineInputError(
            f"drawing specification is invalid: {_posix_relative(spec_path, paths.root)}: {error}"
        ) from error
    canonical_spec_bytes = _canonical_json(json.loads(spec_source.payload))
    try:
        canonical_spec = _materialize_canonical_spec(
            paths, spec_path, spec_source.payload, canonical_spec_bytes
        )
    except (OSError, ValueError, PipelineInputError) as error:
        return _publication_failure_result(
            paths,
            f"canonical specification materialization failed: "
            f"{type(error).__name__}: {error}",
        )
    preflight_issues = _spec_validation_issues(spec)
    if any(issue["severity"] == "error" for issue in preflight_issues):
        return _failed_spec_validation_result(
            paths,
            spec_path,
            preflight_issues,
            spec_source_sha256=spec_source.sha256,
            spec_canonical_sha256=hashlib.sha256(canonical_spec_bytes).hexdigest(),
        )
    try:
        standard = load_standard(_STANDARD_PATH)
        fonts = _resolve_fonts(spec, selected)
    except Exception as error:
        return _failed_result(
            paths,
            "FONT_OR_STANDARD_FAILED",
            f"standard/font preparation failed: {type(error).__name__}: {error}",
            category="font",
        )
    try:
        fragments = _extract_inputs(paths)
    except Exception as error:
        _write_controlled_input_audit(
            paths,
            "INPUT_EXTRACTION_FAILED",
            f"source extraction failed: {type(error).__name__}: {error}",
        )
        raise PipelineInputError(f"source extraction failed: {error}") from error
    extraction_path = _write_extraction(paths, fragments)

    run_root = Path(tempfile.mkdtemp(prefix=".run-", dir=paths.iterations))
    try:
        repair = run_repair_loop(
            GenerationContext(spec, standard, fonts, run_root),
            max_iterations=max(1, selected.max_repair_iterations),
        )
    except Exception as error:
        return _failed_result(
            paths,
            "CORE_PIPELINE_FAILED",
            f"core generation/audit failed: {type(error).__name__}: {error}",
        )
    issues = [*preflight_issues, *(asdict(issue) for issue in repair.report.issues)]
    core_passed = repair.status == "passed" and repair.last_dxf is not None
    source_artifacts: dict[str, ValidatedArtifact] = {}
    if core_passed:
        assert repair.last_dxf is not None
        candidates = (
            ("drawing.dxf", "dxf", repair.last_dxf),
            ("drawing.svg", "svg", repair.last_dxf.with_name("drawing.svg")),
            ("drawing.png", "png", repair.last_dxf.with_name("drawing.png")),
        )
        for logical_name, artifact_type, candidate in candidates:
            try:
                source_artifacts[logical_name] = _snapshot_artifact(
                    candidate, artifact_type, logical_name
                )
            except (OSError, ValueError, ArtifactValidationError):
                issues.append(
                    {
                        "code": "PIPELINE_ARTIFACT_INVALID",
                        "severity": "error",
                        "category": "structure",
                        "message": (
                            f"core artifact failed {artifact_type.upper()} validation: "
                            f"{logical_name}"
                        ),
                        "entity_handles": [],
                        "repairable": False,
                    }
                )
                core_passed = False
                source_artifacts.clear()
                break
    elif not any(issue["severity"] == "error" for issue in issues):
        issues.append(
            {
                "code": "PIPELINE_ARTIFACT_INVALID",
                "severity": "error",
                "category": "structure",
                "message": "core DXF did not reach an audit-clean passed iteration",
                "entity_handles": [],
                "repairable": False,
            }
        )

    autocad = {"status": "disabled", "evidence": ["AutoCAD execution was not requested"]}
    if core_passed and selected.run_autocad:
        autocad_output = run_root / "autocad-output"
        autocad_output.mkdir()
        autocad_result = execute_autocad(
            AutoCADRequest(
                input_dxf=source_artifacts["drawing.dxf"].source,
                output_dir=autocad_output,
                task_work_dir=run_root,
                configured_path=selected.autocad_path,
            )
        )
        if (
            autocad_result.status == "succeeded"
            and autocad_result.dwg_path is not None
            and autocad_result.pdf_path is not None
        ):
            try:
                dwg = _snapshot_artifact(
                    autocad_result.dwg_path, "dwg", "drawing.dwg"
                )
                pdf = _snapshot_artifact(
                    autocad_result.pdf_path, "pdf", "drawing.pdf"
                )
            except (OSError, ValueError, ArtifactValidationError) as error:
                autocad = {
                    "status": "failed",
                    "evidence": [
                        "AutoCAD returned files that failed fresh DWG/PDF type validation",
                        _sanitize_text(str(error), paths),
                    ],
                }
            else:
                source_artifacts["drawing.dwg"] = dwg
                source_artifacts["drawing.pdf"] = pdf
                autocad = {
                    "status": "verified",
                    "evidence": [
                        _sanitize_text(str(item), paths)
                        for item in autocad_result.evidence
                    ],
                }
        else:
            detail = autocad_result.error.message if autocad_result.error else "returned outputs failed fresh DWG/PDF validation"
            autocad = {
                "status": "skipped" if autocad_result.status == "skipped" else "failed",
                "evidence": [
                    *(
                        _sanitize_text(str(item), paths)
                        for item in autocad_result.evidence
                    ),
                    _sanitize_text(detail, paths),
                ],
            }
    elif not core_passed:
        autocad = {
            "status": "not-run",
            "evidence": ["AutoCAD was not run because the core drawing did not pass audit"],
        }
    status = "partial" if core_passed else "failed"
    if core_passed and autocad["status"] == "verified":
        status = "complete"
    autocad_issue = _autocad_audit_issue(autocad)
    if core_passed and autocad_issue is not None:
        issues.append(autocad_issue)
    severity_order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(
        key=lambda issue: (
            severity_order[str(issue["severity"])],
            str(issue["category"]),
            str(issue["code"]),
            str(issue["message"]),
        )
    )
    issues = [_sanitize_issue(issue, paths) for issue in issues]
    try:
        names = _allocate_output_names(
            paths, tuple(source_artifacts), include_autocad=False
        )
    except PipelineInputError as error:
        return _publication_failure_result(paths, str(error))
    artifacts = [
        _artifact_record_from_snapshot(artifact, names[logical_name])
        for logical_name, artifact in source_artifacts.items()
    ]
    font_evidence = _font_evidence(repair)
    try:
        _revalidate_canonical_materialization(paths, canonical_spec)
        semantic_inventory = (
            _semantic_inventory(repair.spec, repair.plan, repair.last_dxf)
            if core_passed
            else []
        )
    except (OSError, ValueError, PipelineInputError) as error:
        canonical_spec.close()
        return _publication_failure_result(paths, str(error))
    audit_value = {
        "schema_version": "1.0",
        "status": status,
        "summary": {
            "errors": sum(issue["severity"] == "error" for issue in issues),
            "warnings": sum(issue["severity"] == "warning" for issue in issues),
            "info": sum(issue["severity"] == "info" for issue in issues),
        },
        "issues": issues,
        "font_render": {
            "verified_roles": list(repair.verified_roles),
            "evidence": font_evidence,
        },
        "semantic_inventory": semantic_inventory,
        "autocad": autocad,
        "provenance": {
            "spec_source": _posix_relative(spec_path, paths.root),
            "spec_source_sha256": spec_source.sha256,
            "spec_canonical_sha256": hashlib.sha256(canonical_spec_bytes).hexdigest(),
            "canonical_spec": _posix_relative(canonical_spec.path, paths.root),
            "canonical_spec_file_sha256": canonical_spec.artifact.sha256,
            "canonical_spec_owned": canonical_spec.owned,
            "canonical_spec_reused": canonical_spec.reused,
            "source_fragments": _posix_relative(extraction_path, paths.root),
            "source_fragments_sha256": _sha256(extraction_path),
            "source_files": _source_records(paths),
        },
        "artifacts": artifacts,
    }
    evidence_stage = run_root / "publication-evidence"
    evidence_stage.mkdir()
    audit_source = evidence_stage / "audit.json"
    report_source = evidence_stage / "audit-report.md"
    audit_source.write_bytes(_canonical_json(audit_value, pretty=True))
    report_source.write_text(
        _chinese_report(status, audit_value), encoding="utf-8", newline="\n"
    )
    publication_artifacts = (
        *source_artifacts.values(),
        _snapshot_artifact(audit_source, "json", "audit.json"),
        _snapshot_artifact(report_source, "markdown", "audit-report.md"),
    )
    canonical_record = {
        "path": _posix_relative(canonical_spec.path, paths.root),
        "sha256": canonical_spec.artifact.sha256,
        "size": canonical_spec.artifact.size,
        "type": "json",
        "validation": "validated canonical specification",
        "owned": canonical_spec.owned,
        "reused": canonical_spec.reused,
    }
    extraction_record = {
        "path": _posix_relative(extraction_path, paths.root),
        "sha256": _sha256(extraction_path),
        "size": extraction_path.stat().st_size,
        "type": "json",
        "validation": "canonical source fragments",
    }
    additional_records = (canonical_record, extraction_record)
    try:
        _revalidate_canonical_materialization(paths, canonical_spec)
        destinations = _publish_transaction(
            paths,
            publication_artifacts,
            names,
            additional_manifest_records=additional_records,
            guard_artifacts=(canonical_spec.artifact,),
        )
    except (OSError, ValueError, PipelineInputError) as error:
        canonical_spec.close()
        return _publication_failure_result(paths, str(error))
    canonical_spec.close()
    result_names = (*source_artifacts, "audit.json", "audit-report.md")
    published = tuple(destinations[name] for name in result_names)
    return PipelineResult(
        status, paths.root, published, destinations["audit.json"]
    )


def _find_spec(paths: TaskPaths) -> Path:
    candidates = (paths.input / "drawing-spec.json", paths.work / "drawing-spec.json")
    for candidate in candidates:
        if candidate.exists() or candidate.is_symlink():
            return require_task_file(candidate, paths.root, "drawing specification")
    raise PipelineInputError(
        f"drawing specification not found at {candidates[0]} or {candidates[1]}"
    )


def _spec_validation_issues(spec: DrawingSpec) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    for label, values in (
        ("missing", spec.validation.missing),
        ("conflict", spec.validation.conflicts),
    ):
        for value in values:
            issues.append(
                {
                    "code": f"SPEC_{label.upper()}",
                    "severity": "error",
                    "category": "semantic",
                    "message": f"drawing specification has unresolved {label}: {value}",
                    "entity_handles": [],
                    "repairable": False,
                }
            )
    for value in spec.validation.assumptions:
        issues.append(
            {
                "code": "SPEC_ASSUMPTION",
                "severity": "info",
                "category": "semantic",
                "message": f"drawing specification records assumption: {value}",
                "entity_handles": [],
                "repairable": False,
            }
        )
    return issues


def _autocad_audit_issue(autocad: dict[str, object]) -> dict[str, object] | None:
    state = str(autocad["status"])
    if state == "verified" or state == "not-run":
        return None
    code = {
        "disabled": "AUTOCAD_DISABLED",
        "skipped": "AUTOCAD_SKIPPED",
        "failed": "AUTOCAD_FAILED",
    }[state]
    evidence = "; ".join(str(item) for item in autocad["evidence"])
    return {
        "code": code,
        "severity": "warning",
        "category": "structure",
        "message": f"DWG/PDF were not verified ({state}): {evidence}",
        "entity_handles": [],
        "repairable": False,
    }


def _extract_inputs(paths: TaskPaths) -> list[object]:
    sources = tuple(
        path
        for path in _validated_input_files(paths)
        if path.suffix.casefold() in _SOURCE_SUFFIXES
    )
    return extract_sources(sources) if sources else []


def _write_extraction(paths: TaskPaths, fragments: list[object]) -> Path:
    value = []
    for fragment in fragments:
        record = asdict(fragment)
        source_path = Path(str(record["file"]))
        try:
            record["file"] = _posix_relative(
                source_path.resolve(strict=True), paths.root
            )
        except (OSError, ValueError):
            record["file"] = f"input/{source_path.name}"
        identity_value = {key: value for key, value in record.items() if key != "id"}
        record["id"] = hashlib.sha256(
            _canonical_json(identity_value).rstrip(b"\n")
        ).hexdigest()[:16]
        value.append(record)
    payload = _canonical_json(value)
    digest = hashlib.sha256(payload).hexdigest()[:16]
    destination = paths.extracted / f"source-fragments-{digest}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise PipelineInputError(f"existing extracted provenance differs: {destination}")
        return destination
    _write_new_file(destination, payload)
    return destination


def _resolve_fonts(spec: DrawingSpec, options: PipelineOptions) -> Mapping[str, ResolvedFont]:
    autocad_roots: list[Path] = []
    if options.autocad_path is not None:
        root = options.autocad_path.parent if options.autocad_path.suffix else options.autocad_path
        autocad_roots.append(root / "Fonts")
    windows_root = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    inventory = discover_fonts(
        build_font_search_roots(autocad_roots=autocad_roots, windows_roots=[windows_root])
    )
    policy = load_font_policy(_FONT_POLICY_PATH)
    text = " ".join(
        filter(
            None,
            (
                spec.project.name,
                spec.project.drawing_number,
                (
                    spec.requirements.material.text
                    if spec.requirements.material is not None
                    else None
                ),
                *spec.requirements.views,
                *(item.text for item in spec.requirements.technical_notes),
                *(item.text for item in spec.annotations),
                *(item.item for item in spec.parts_list),
                "TITLE DRAWING SCALE PROJECTION TECHNICAL NOTES PARTS LIST ITEM DESCRIPTION QTY",
            ),
        )
    )
    dimensions = " ".join(f"{item.value:g}" for item in spec.dimensions) or "0"
    try:
        return {
            "TEXT": resolve_font(text, inventory, policy),
            "DIM": resolve_font(dimensions, inventory, policy),
        }
    except Exception as error:
        raise PipelineInputError(f"font resolution failed: {error}") from error


def _source_records(paths: TaskPaths) -> list[dict[str, object]]:
    records = []
    for path in _validated_input_files(paths):
        records.append(
            {
                "path": _posix_relative(path, paths.root),
                "sha256": _sha256(path),
                "size": path.stat().st_size,
            }
        )
    return records


def _validated_input_files(paths: TaskPaths) -> tuple[Path, ...]:
    files: list[Path] = []
    for path in sorted(paths.input.iterdir(), key=lambda item: item.name.casefold()):
        if path.is_dir() and not path.is_symlink():
            continue
        files.append(require_task_file(path, paths.root, f"input file {path.name}"))
    return tuple(files)


def _allocate_output_names(
    paths: TaskPaths, drawing_names: tuple[str, ...], *, include_autocad: bool
) -> dict[str, str]:
    logical = [*drawing_names]
    if include_autocad:
        logical.extend(("drawing.dwg", "drawing.pdf"))
    logical.extend(("audit.json", "audit-report.md", "pipeline-owned.json"))
    validate_output_identity(paths)
    if not any((paths.output / name).exists() for name in logical):
        validate_output_identity(paths)
        return {name: name for name in logical}
    version = 2
    while True:
        mapped = {
            name: (
                f"audit-report-v{version}.md"
                if name == "audit-report.md"
                else f"pipeline-owned-v{version}.json"
                if name == "pipeline-owned.json"
                else f"{Path(name).stem}-v{version}{Path(name).suffix}"
            )
            for name in logical
        }
        if not any((paths.output / name).exists() for name in mapped.values()):
            validate_output_identity(paths)
            return mapped
        version += 1


def _publish_file(source: Path, destination: Path, paths: TaskPaths) -> None:
    artifact_type = _artifact_type_for_name(destination.name)
    artifact = _snapshot_artifact(source, artifact_type, destination.name)
    mapping = {
        destination.name: destination.name,
        "pipeline-owned.json": "pipeline-owned.json",
    }
    _publish_transaction(paths, (artifact,), mapping)


def _write_new_file(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise PipelineInputError(f"refusing to overwrite existing owned record: {path}") from error
        except OSError as error:
            raise PipelineInputError(
                f"could not atomically create owned record: {path}: {error}"
            ) from error
    finally:
        temporary.unlink(missing_ok=True)


def _snapshot_artifact(
    source: Path, artifact_type: str, logical_name: str
) -> ValidatedArtifact:
    """Validate bytes read from one stable source identity and freeze expectations."""

    source = Path(source)
    _reject_file_reparse(source, logical_name)
    try:
        with source.open("rb") as stream:
            before = _source_identity(stream)
            payload = stream.read()
            after = _source_identity(stream)
    except OSError as error:
        raise ArtifactValidationError(
            f"artifact is unreadable: {logical_name}: {error.strerror or type(error).__name__}"
        ) from error
    if before != after or _path_source_identity(source) != before:
        raise ArtifactValidationError(
            f"artifact changed during validation: {logical_name}"
        )
    _validate_artifact_payload(payload, artifact_type, logical_name)
    file_identity = _capture_file_identity(source)
    if _path_source_identity(source) != before:
        raise ArtifactValidationError(
            f"artifact changed during validation: {logical_name}"
        )
    return ValidatedArtifact(
        source=source,
        logical_name=logical_name,
        artifact_type=artifact_type,
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        source_identity=before,
        file_identity=file_identity,
        validation=f"{artifact_type.upper()} reopened and type-validated",
        payload=payload,
    )


def _publish_transaction(
    paths: TaskPaths,
    artifacts: tuple[ValidatedArtifact, ...],
    names: Mapping[str, str],
    *,
    additional_manifest_records: tuple[dict[str, object], ...] = (),
    guard_artifacts: tuple[ValidatedArtifact, ...] = (),
) -> dict[str, Path]:
    """Publish an owned output set atomically, with its manifest committed last."""

    artifact_names = {artifact.logical_name for artifact in artifacts}
    manifest_keys = tuple(name for name in names if name not in artifact_names)
    if len(manifest_keys) != 1:
        raise PipelineInputError("publication transaction requires one manifest commit marker")
    manifest_key = manifest_keys[0]
    published: list[_PublishedFile] = []
    destinations: dict[str, Path] = {}
    retained: tuple[str, ...] = ()
    try:
        with _hold_output_directory(paths.output), ExitStack() as holds:
            validate_output_identity(paths)
            for artifact in guard_artifacts:
                _hold_validated_source(holds, artifact)
            for artifact in artifacts:
                _hold_validated_source(holds, artifact)
            for artifact in guard_artifacts:
                # This is the post-publication validation boundary.  It remains
                # inside the rollback scope, after the commit marker is linked.
                _revalidate_validated_artifact(artifact)
            for artifact in artifacts:
                destination = paths.output / names[artifact.logical_name]
                publication = _publish_validated_artifact(paths, artifact, destination)
                published.append(publication)
                _hold_published_destination(holds, publication)
                destinations[artifact.logical_name] = destination
            for artifact in guard_artifacts:
                _revalidate_validated_artifact(artifact)
            manifest_value = {
                "schema_version": "1.0",
                "commit_marker": True,
                "artifacts": [
                    *additional_manifest_records,
                    *(
                        {
                            "path": _posix_relative(
                                paths.output / names[artifact.logical_name], paths.root
                            ),
                            "sha256": artifact.sha256,
                            "size": artifact.size,
                            "type": artifact.artifact_type,
                            "validation": artifact.validation,
                        }
                        for artifact in artifacts
                    ),
                ],
            }
            with tempfile.TemporaryDirectory(prefix=".manifest-", dir=paths.iterations) as temporary:
                manifest_source = Path(temporary) / "manifest.json"
                manifest_source.write_bytes(_canonical_json(manifest_value, pretty=True))
                manifest = _snapshot_artifact(
                    manifest_source, "manifest", manifest_key
                )
                manifest_destination = paths.output / names[manifest_key]
                publication = _publish_validated_artifact(
                    paths, manifest, manifest_destination
                )
                published.append(publication)
                _hold_published_destination(holds, publication)
                destinations[manifest_key] = manifest_destination
            validate_output_identity(paths)
            for publication in published:
                if _capture_file_identity(publication.path) != publication.identity:
                    raise _PublicationStepError(
                        f"published output changed before transaction commit: "
                        f"{publication.path.name}",
                        publication,
                    )
            for artifact in guard_artifacts:
                _revalidate_validated_artifact(artifact)
    except _PublicationStepError as error:
        if error.published is not None and error.published not in published:
            published.append(error.published)
        retained = _rollback_transaction(paths, tuple(published))
        suffix = (
            "; retained partials: " + ", ".join(retained) if retained else ""
        )
        raise PipelineInputError(f"{error}{suffix}") from error
    except (OSError, ValueError, PipelineInputError) as error:
        retained = _rollback_transaction(paths, tuple(published))
        suffix = (
            "; retained partials: " + ", ".join(retained) if retained else ""
        )
        raise PipelineInputError(f"publication transaction failed: {error}{suffix}") from error
    return destinations


def _hold_validated_source(holds: ExitStack, artifact: ValidatedArtifact) -> None:
    if os.name != "nt":
        return
    try:
        handle = holds.enter_context(
            _open_windows_handle(
                artifact.source,
                0x80000000 | 0x00000080,
                share_mode=0x00000001,
            )
        )
        if (
            _file_identity_from_handle(handle) != artifact.file_identity
            or _sha256_windows_handle(handle) != artifact.sha256
        ):
            raise ArtifactValidationError(
                f"artifact changed after validation: {artifact.logical_name}"
            )
    except OSError as error:
        raise ArtifactValidationError(
            f"artifact could not be held against replacement: {artifact.logical_name}: {error}"
        ) from error


def _hold_published_destination(
    holds: ExitStack, publication: _PublishedFile
) -> None:
    if os.name != "nt":
        return
    try:
        handle = holds.enter_context(
            _open_windows_handle(
                publication.path,
                0x80000000 | 0x00000080,
                share_mode=0x00000001,
            )
        )
        if (
            _file_identity_from_handle(handle) != publication.identity
            or _sha256_windows_handle(handle) != publication.sha256
        ):
            raise _PublicationStepError(
                f"published output changed before transaction commit: "
                f"{publication.path.name}",
                publication,
            )
    except OSError as error:
        raise _PublicationStepError(
            f"published output could not be held against replacement: "
            f"{publication.path.name}: {error}",
            publication,
        ) from error


def _publish_validated_artifact(
    paths: TaskPaths, artifact: ValidatedArtifact, destination: Path
) -> _PublishedFile:
    if destination.parent != paths.output:
        raise _PublicationStepError("output destination escapes prepared directory")
    temporary: Path | None = None
    publication: _PublishedFile | None = None
    try:
        validate_output_identity(paths)
        if destination.exists() or destination.is_symlink():
            raise _PublicationStepError(
                f"output competitor appeared during atomic publication: {destination.name}"
            )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".partial", dir=paths.output
        )
        temporary = Path(temporary_name)
        validate_output_identity(paths)
        digest = hashlib.sha256()
        copied = 0
        try:
            with artifact.source.open("rb") as source, os.fdopen(descriptor, "wb") as target:
                before = _source_identity(source)
                if before != artifact.source_identity:
                    raise ArtifactValidationError(
                        f"artifact changed after validation: {artifact.logical_name}"
                    )
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    target.write(block)
                    digest.update(block)
                    copied += len(block)
                target.flush()
                os.fsync(target.fileno())
                after = _source_identity(source)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        if (
            before != after
            or _path_source_identity(artifact.source) != before
            or copied != artifact.size
            or digest.hexdigest() != artifact.sha256
        ):
            raise ArtifactValidationError(
                f"artifact changed after validation: {artifact.logical_name}"
            )
        validate_output_identity(paths)
        identity = _capture_file_identity(temporary)
        publication = _PublishedFile(
            destination,
            paths.output_identity,  # compatible stable identity contract from Task 9
            identity,
            artifact.sha256,
        )
        try:
            os.link(temporary, destination)
        except FileExistsError as error:
            raise _PublicationStepError(
                f"output competitor appeared during atomic publication: {destination.name}"
            ) from error
        except OSError as error:
            raise _PublicationStepError(
                f"could not atomically publish {destination.name}: {error}"
            ) from error
        validate_output_identity(paths)
        if _capture_file_identity(destination) != identity:
            raise _PublicationStepError(
                f"published output identity changed: {destination.name}", publication
            )
        reopened = _snapshot_artifact(
            destination, artifact.artifact_type, artifact.logical_name
        )
        if reopened.sha256 != artifact.sha256 or reopened.size != artifact.size:
            raise _PublicationStepError(
                f"published output bytes changed: {destination.name}", publication
            )
        validate_output_identity(paths)
        return publication
    except _PublicationStepError:
        raise
    except (OSError, ValueError, ArtifactValidationError) as error:
        raise _PublicationStepError(str(error), publication) from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _rollback_transaction(
    paths: TaskPaths, publications: tuple[_PublishedFile, ...]
) -> tuple[str, ...]:
    retained: list[str] = []
    for publication in reversed(publications):
        try:
            validate_output_identity(paths)
            if os.name == "nt":
                outcome = _delete_owned_publication(publication)
            else:
                current = _capture_file_identity(publication.path)
                if current != publication.identity or _sha256(publication.path) != publication.sha256:
                    outcome = "not-owned"
                else:
                    publication.path.unlink()
                    outcome = "deleted"
            if outcome in {"owned-retained", "unverified"}:
                retained.append(publication.path.name)
        except (OSError, ValueError, PipelineInputError):
            retained.append(publication.path.name)
    return tuple(sorted(set(retained)))


def _delete_owned_publication(publication: _PublishedFile) -> str:
    """Delete only the exact file object whose bytes and identity we published."""

    try:
        with _open_windows_handle(
            publication.path,
            0x80000000 | 0x00010000 | 0x00000080,
            share_mode=0x00000001,
        ) as handle:
            if _file_identity_from_handle(handle) != publication.identity:
                return "not-owned"
            if _sha256_windows_handle(handle) != publication.sha256:
                return "not-owned"
            try:
                _set_delete_disposition(handle)
            except OSError:
                return "owned-retained"
            return "deleted"
    except OSError:
        return "unverified"


def _sha256_windows_handle(handle: int) -> str:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_pointer = kernel32.SetFilePointerEx
    set_pointer.argtypes = [
        ctypes.c_void_p,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        ctypes.c_uint32,
    ]
    set_pointer.restype = ctypes.c_int
    if not set_pointer(handle, 0, None, 0):
        raise OSError(ctypes.get_last_error(), "SetFilePointerEx failed")
    read_file = kernel32.ReadFile
    read_file.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
    ]
    read_file.restype = ctypes.c_int
    digest = hashlib.sha256()
    buffer = ctypes.create_string_buffer(1024 * 1024)
    while True:
        count = ctypes.c_uint32()
        if not read_file(handle, buffer, len(buffer), ctypes.byref(count), None):
            raise OSError(ctypes.get_last_error(), "ReadFile failed")
        if count.value == 0:
            return digest.hexdigest()
        digest.update(buffer.raw[: count.value])


def _source_identity(stream) -> _SourceIdentity:
    metadata = os.fstat(stream.fileno())
    return _identity_from_stat(metadata)


def _path_source_identity(path: Path) -> _SourceIdentity:
    return _identity_from_stat(path.stat())


def _identity_from_stat(metadata: os.stat_result) -> _SourceIdentity:
    return _SourceIdentity(
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
    )


def _reject_file_reparse(path: Path, logical_name: str) -> None:
    metadata = path.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    if stat.S_ISLNK(metadata.st_mode) or attributes & 0x400:
        raise ArtifactValidationError(
            f"artifact source is a symlink or reparse point: {logical_name}"
        )


def _validate_artifact_payload(payload: bytes, artifact_type: str, name: str) -> None:
    if not payload:
        raise ArtifactValidationError(f"artifact is empty: {name}")
    try:
        if artifact_type == "dxf":
            document = ezdxf.read(StringIO(payload.decode("utf-8-sig")))
            if document.audit().has_errors:
                raise ValueError("ezdxf audit reported errors")
        elif artifact_type == "svg":
            root = ElementTree.fromstring(payload)
            if not root.tag.casefold().endswith("svg"):
                raise ValueError("root element is not SVG")
            dimensions = [_svg_number(root.get(key)) for key in ("width", "height")]
            if any(value is not None and not 0 < value <= 2048 for value in dimensions):
                raise ValueError("SVG dimensions exceed validated bounds")
            view_box = root.get("viewBox")
            if view_box:
                values = [float(item) for item in re.split(r"[ ,]+", view_box.strip())]
                if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
                    raise ValueError("SVG viewBox is invalid")
        elif artifact_type == "png":
            with Image.open(BytesIO(payload)) as image:
                image.verify()
            with Image.open(BytesIO(payload)) as image:
                image.load()
                if image.width <= 0 or image.height <= 0 or image.width > 2048 or image.height > 2048:
                    raise ValueError("PNG dimensions exceed validated bounds")
        elif artifact_type == "pdf":
            if payload[:5] != b"%PDF-" or len(PdfReader(BytesIO(payload)).pages) < 1:
                raise ValueError("PDF is unreadable or has no pages")
        elif artifact_type == "dwg":
            if len(payload) <= 256 or payload[:6] != b"AC1032":
                raise ValueError("DWG is not a validated AC1032 file")
        elif artifact_type in {"json", "manifest"}:
            value = json.loads(payload.decode("utf-8"))
            if artifact_type == "manifest" and (
                not isinstance(value, dict)
                or value.get("commit_marker") is not True
                or not isinstance(value.get("artifacts"), list)
            ):
                raise ValueError("manifest commit marker is invalid")
        elif artifact_type == "markdown":
            if not payload.decode("utf-8").strip():
                raise ValueError("Markdown report is empty")
        else:
            raise ValueError(f"unsupported artifact type: {artifact_type}")
    except ArtifactValidationError:
        raise
    except Exception as error:
        raise ArtifactValidationError(
            f"{artifact_type.upper()} validation failed for {name}: {type(error).__name__}: {error}"
        ) from error


def _svg_number(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(?:px)?\s*", value)
    return float(match.group(1)) if match else None


def _artifact_type_for_name(name: str) -> str:
    suffix = Path(name).suffix.casefold()
    return {
        ".dxf": "dxf",
        ".svg": "svg",
        ".png": "png",
        ".dwg": "dwg",
        ".pdf": "pdf",
        ".json": "json",
        ".md": "markdown",
    }.get(suffix, "markdown")


def _artifact_record_from_snapshot(
    artifact: ValidatedArtifact, destination_name: str
) -> dict[str, object]:
    return {
        "path": f"output/{destination_name}",
        "name": destination_name,
        "sha256": artifact.sha256,
        "size": artifact.size,
        "type": artifact.artifact_type,
        "validation": artifact.validation,
    }


def _materialize_canonical_spec(
    paths: TaskPaths,
    spec_path: Path,
    source_payload: bytes,
    canonical_payload: bytes,
) -> CanonicalMaterialization:
    """Materialize an immutable canonical snapshot without trusting manifests."""

    expected_sha256 = hashlib.sha256(canonical_payload).hexdigest()
    expected_size = len(canonical_payload)
    source_now = _snapshot_artifact(
        spec_path, "json", _posix_relative(spec_path, paths.root)
    )
    if source_now.payload != source_payload:
        raise PipelineInputError("drawing specification changed during canonicalization")
    path, owned, reused = _write_canonical_work_variant(paths, canonical_payload)
    artifact = _snapshot_artifact(
        path, "json", _posix_relative(path, paths.root)
    )
    if (
        artifact.sha256 != expected_sha256
        or artifact.size != expected_size
        or artifact.payload != canonical_payload
    ):
        raise PipelineInputError(
            "canonical work specification does not match the validated payload snapshot"
        )
    holds = ExitStack()
    try:
        _hold_validated_source(holds, artifact)
    except Exception:
        holds.close()
        raise
    return CanonicalMaterialization(artifact, owned, reused, holds)


def _write_canonical_work_variant(
    paths: TaskPaths, canonical_payload: bytes
) -> tuple[Path, bool, bool]:
    """No-clobber an immutable canonical sibling; never consult a manifest."""

    with _hold_output_directory(paths.work):
        validate_work_identity(paths)
        return _write_canonical_work_variant_locked(paths, canonical_payload)


def _write_canonical_work_variant_locked(
    paths: TaskPaths, canonical_payload: bytes
) -> tuple[Path, bool, bool]:
    for candidate in _canonical_work_candidates(paths, canonical_payload):
        if not candidate.exists() and not candidate.is_symlink():
            try:
                created = _write_new_canonical_work_file(
                    paths, candidate, canonical_payload
                )
            except PipelineInputError:
                validate_work_identity(paths)
                if not candidate.exists() and not candidate.is_symlink():
                    raise
            else:
                return created, True, False
        if candidate.exists() and not candidate.is_symlink():
            logical = _posix_relative(candidate, paths.root)
            try:
                validated = _snapshot_artifact(candidate, "json", logical)
            except (OSError, ValueError, ArtifactValidationError):
                continue
            if validated.payload == canonical_payload:
                validate_work_identity(paths)
                return candidate, False, True
    raise PipelineInputError("could not allocate a canonical work specification")


def _canonical_work_candidates(
    paths: TaskPaths, canonical_payload: bytes
):
    yield paths.work / "drawing-spec.canonical.json"
    digest = hashlib.sha256(canonical_payload).hexdigest()
    yield paths.work / f"drawing-spec.canonical-{digest}.json"
    counter = 2
    while True:
        yield paths.work / f"drawing-spec.canonical-{digest}-{counter}.json"
        counter += 1


def _write_new_canonical_work_file(
    paths: TaskPaths, destination: Path, canonical_payload: bytes
) -> Path:
    validate_work_identity(paths)
    _write_new_file(destination, canonical_payload)
    validate_work_identity(paths)
    logical = _posix_relative(destination, paths.root)
    validated = _snapshot_artifact(destination, "json", logical)
    if validated.payload != canonical_payload:
        raise PipelineInputError(
            "canonical work specification changed after publication"
        )
    return destination


def _revalidate_validated_artifact(artifact: ValidatedArtifact) -> None:
    current = _snapshot_artifact(
        artifact.source, artifact.artifact_type, artifact.logical_name
    )
    if (
        current.file_identity != artifact.file_identity
        or current.source_identity != artifact.source_identity
        or current.sha256 != artifact.sha256
        or current.size != artifact.size
        or current.payload != artifact.payload
    ):
        raise PipelineInputError(
            f"artifact changed after immutable validation: {artifact.logical_name}"
        )


def _revalidate_canonical_materialization(
    paths: TaskPaths,
    canonical: CanonicalMaterialization,
) -> None:
    validate_work_identity(paths)
    _revalidate_validated_artifact(canonical.artifact)
    validate_work_identity(paths)


def _posix_relative(path: Path, root: Path) -> str:
    return Path(path).resolve(strict=False).relative_to(root).as_posix()


def _sanitize_text(message: str, paths: TaskPaths) -> str:
    value = str(message).replace("\\", "/")
    roots = {
        str(paths.root).replace("\\", "/"),
        paths.root.as_posix(),
    }
    for root in sorted(roots, key=len, reverse=True):
        value = value.replace(root + "/", "")
        value = value.replace(root, ".")
    value = re.sub(
        r"work/iterations/\.run-[^/]+/iterations/", "work/iterations/", value
    )
    value = re.sub(r"\.run-[^/\s:;,]+", "run", value)
    value = re.sub(
        r"[A-Za-z]:/(?:[^\s:;,]+/)*([^\s:;,/]+)", r"\1", value
    )
    return value


def _sanitize_issue(
    issue: Mapping[str, object], paths: TaskPaths
) -> dict[str, object]:
    return {
        "code": str(issue["code"]),
        "severity": str(issue["severity"]),
        "category": str(issue["category"]),
        "message": _sanitize_text(str(issue["message"]), paths),
        "entity_handles": sorted(str(item) for item in issue.get("entity_handles", [])),
        "repairable": bool(issue.get("repairable", False)),
    }


def _font_evidence(repair) -> list[str]:
    evidence = [
        f"work/iterations/{repair.iterations:02d}/drawing.dxf reopened and ezdxf audit passed"
    ]
    evidence.extend(
        f"{role} font rendering verified in drawing.svg and drawing.png without "
        "backend warnings or missing-glyph markers"
        for role in sorted(repair.verified_roles)
    )
    return evidence


def _semantic_inventory(spec: DrawingSpec, plan, dxf_path: Path) -> list[dict[str, object]]:
    if plan is None:
        return []
    annotations = {item.annotation_id: item for item in plan.annotations}
    records: list[dict[str, object]] = []

    def add(
        semantic_kind: str,
        semantic_id: str,
        handles: tuple[str, ...],
        *,
        annotation_id: str | None = None,
        source_id: str | None = None,
        item_no: int | None = None,
        source_membership: list[dict[str, object]] | None = None,
        normalized_source_bounds_mm: list[float] | None = None,
    ) -> None:
        if not handles:
            raise PipelineInputError(
                f"semantic inventory record has no committed handles: {semantic_id}"
            )
        record: dict[str, object] = {
            "semantic_kind": semantic_kind,
            "semantic_id": semantic_id,
            "handles": list(handles),
        }
        if annotation_id is not None:
            record["annotation_id"] = annotation_id
        if source_id is not None:
            record["source_id"] = source_id
        if item_no is not None:
            record["item_no"] = item_no
        if source_membership is not None:
            record["source_membership"] = source_membership
        if normalized_source_bounds_mm is not None:
            record["normalized_source_bounds_mm"] = normalized_source_bounds_mm
        records.append(record)

    for view in plan.views:
        if view.geometry_bounds is None:
            raise PipelineInputError(
                f"semantic view has no finalized source bounds: {view.name}"
            )
        add(
            "view_source",
            view.name,
            view.source_handles,
            source_membership=[
                {
                    "entity_index": index,
                    "source_id": spec.geometry.entities[index].source_id,
                }
                for index in view.source_indices
            ],
            normalized_source_bounds_mm=[
                _stable_audit_number(value)
                for value in view.geometry_bounds.as_tuple()
            ],
        )
        label_id = f"view-label-{view.name}"
        label = annotations[label_id]
        add(
            "view_label",
            view.name,
            label.entity_handles,
            annotation_id=label_id,
        )
    for index, dimension in enumerate(spec.dimensions, start=1):
        annotation_id = f"dimension-{index}"
        annotation = annotations[annotation_id]
        add(
            "dimension",
            annotation_id,
            annotation.entity_handles,
            annotation_id=annotation_id,
            source_id=dimension.source_id,
        )
    for item_no, part in enumerate(spec.parts_list, start=1):
        for semantic_kind, suffix in (
            ("part_description", "description"),
            ("part_quantity", "qty"),
        ):
            annotation_id = f"parts-{item_no}-{suffix}"
            annotation = annotations[annotation_id]
            add(
                semantic_kind,
                f"part-{item_no}",
                annotation.entity_handles,
                annotation_id=annotation_id,
                source_id=part.source_id,
                item_no=item_no,
            )
        balloon_id = f"balloon-{item_no}-text"
        balloon = annotations[balloon_id]
        add(
            "balloon_number",
            f"part-{item_no}",
            balloon.entity_handles,
            annotation_id=balloon_id,
            source_id=part.source_id,
            item_no=item_no,
        )
    for index, note in enumerate(spec.requirements.technical_notes, start=1):
        annotation_id = f"technical-note-{index}"
        annotation = annotations[annotation_id]
        add(
            "technical_note",
            annotation_id,
            annotation.entity_handles,
            annotation_id=annotation_id,
            source_id=note.source_id,
        )
    material = spec.requirements.material
    if material is not None:
        document = ezdxf.readfile(dxf_path)
        handles = resolve_material_handles(document, spec, plan)
        add("material", "material", handles, source_id=material.source_id)
    return records


def _stable_audit_number(value: float) -> float:
    rounded = round(float(value), 6)
    return 0.0 if rounded == 0.0 else rounded


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: object, *, pretty: bool = False) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _write_controlled_input_audit(
    paths: TaskPaths,
    code: str,
    message: str,
    *,
    category: str = "semantic",
) -> tuple[Path, Path]:
    audit = {
        "schema_version": "1.0",
        "status": "failed",
        "summary": {"errors": 1, "warnings": 0, "info": 0},
        "issues": [
            {
                "code": code,
                "severity": "error",
                "category": category,
                "message": _sanitize_text(message, paths),
                "entity_handles": [],
                "repairable": False,
            }
        ],
        "font_render": {"verified_roles": [], "evidence": []},
        "autocad": {"status": "not-run", "evidence": []},
        "provenance": {"source_files": _source_records(paths)},
        "artifacts": [],
    }
    return _publish_audit_value(paths, audit)


def _publish_audit_value(
    paths: TaskPaths, audit: dict[str, object]
) -> tuple[Path, Path]:
    try:
        names = _allocate_output_names(paths, (), include_autocad=False)
    except PipelineInputError:
        return _write_failure_evidence_in_work(paths, audit)
    with tempfile.TemporaryDirectory(prefix=".audit-", dir=paths.iterations) as temporary:
        temporary_path = Path(temporary)
        audit_source = temporary_path / "audit.json"
        report_source = temporary_path / "audit-report.md"
        audit_source.write_bytes(_canonical_json(audit, pretty=True))
        report_source.write_text(
            _chinese_report("failed", audit), encoding="utf-8", newline="\n"
        )
        artifacts = (
            _snapshot_artifact(audit_source, "json", "audit.json"),
            _snapshot_artifact(report_source, "markdown", "audit-report.md"),
        )
        try:
            destinations = _publish_transaction(paths, artifacts, names)
        except PipelineInputError:
            return _write_failure_evidence_in_work(paths, audit)
    return destinations["audit.json"], destinations["audit-report.md"]


def _write_failure_evidence_in_work(
    paths: TaskPaths, audit: dict[str, object]
) -> tuple[Path, Path]:
    """Retain a controlled failure audit outside an uncommitted output set."""

    version = 1
    while True:
        suffix = "" if version == 1 else f"-v{version}"
        audit_path = paths.iterations / f"pipeline-failure-audit{suffix}.json"
        report_path = paths.iterations / f"pipeline-failure-report{suffix}.md"
        if not audit_path.exists() and not report_path.exists():
            break
        version += 1
    _write_new_file(audit_path, _canonical_json(audit, pretty=True))
    _write_new_file(
        report_path, _chinese_report("failed", audit).encode("utf-8")
    )
    return audit_path, report_path


def _publication_failure_result(paths: TaskPaths, message: str) -> PipelineResult:
    issue = {
        "code": "PIPELINE_PUBLICATION_FAILED",
        "severity": "error",
        "category": "structure",
        "message": _sanitize_text(message, paths),
        "entity_handles": [],
        "repairable": False,
    }
    audit = {
        "schema_version": "1.0",
        "status": "failed",
        "summary": {"errors": 1, "warnings": 0, "info": 0},
        "issues": [issue],
        "font_render": {"verified_roles": [], "evidence": []},
        "autocad": {
            "status": "not-run",
            "evidence": ["publication transaction did not commit"],
        },
        "provenance": {"source_files": _source_records(paths)},
        "artifacts": [],
    }
    audit_path, _ = _write_failure_evidence_in_work(paths, audit)
    return PipelineResult("failed", paths.root, (), audit_path)


def _failed_result(
    paths: TaskPaths,
    code: str,
    message: str,
    *,
    category: str = "structure",
) -> PipelineResult:
    audit, report = _write_controlled_input_audit(
        paths, code, message, category=category
    )
    return PipelineResult("failed", paths.root, (audit, report), audit)


def _failed_spec_validation_result(
    paths: TaskPaths,
    spec_path: Path,
    issues: list[dict[str, object]],
    *,
    spec_source_sha256: str,
    spec_canonical_sha256: str,
) -> PipelineResult:
    audit_value = {
        "schema_version": "1.0",
        "status": "failed",
        "summary": {
            "errors": sum(issue["severity"] == "error" for issue in issues),
            "warnings": sum(issue["severity"] == "warning" for issue in issues),
            "info": sum(issue["severity"] == "info" for issue in issues),
        },
        "issues": issues,
        "font_render": {"verified_roles": [], "evidence": []},
        "autocad": {
            "status": "not-run",
            "evidence": ["core generation was blocked by unresolved specification validation"],
        },
        "provenance": {
            "spec_source": _posix_relative(spec_path, paths.root),
            "spec_source_sha256": spec_source_sha256,
            "spec_canonical_sha256": spec_canonical_sha256,
            "source_files": _source_records(paths),
        },
        "artifacts": [],
    }
    audit, report = _publish_audit_value(paths, audit_value)
    return PipelineResult("failed", paths.root, (audit, report), audit)


def _chinese_report(status: str, audit: dict[str, object]) -> str:
    summary = audit["summary"]
    status_zh = {"complete": "完整", "partial": "部分完成", "failed": "失败"}[status]
    lines = [
        "# 二维机械图审图报告",
        "",
        f"- 状态：{status_zh}（`{status}`）",
        f"- 错误：{summary['errors']}",
        f"- 警告：{summary['warnings']}",
        f"- 信息：{summary['info']}",
        f"- AutoCAD：{audit['autocad']['status']}",
        "",
        "## 问题明细",
        "",
    ]
    severity_order = {"error": 0, "warning": 1, "info": 2}
    issues = sorted(
        audit.get("issues", []),
        key=lambda issue: (
            severity_order.get(str(issue["severity"]), 99),
            str(issue["category"]),
            str(issue["code"]),
            str(issue["message"]),
        ),
    )
    if issues:
        for issue in issues:
            handles = ",".join(issue.get("entity_handles", [])) or "无"
            lines.append(
                "- "
                f"{issue['code']} | severity={issue['severity']} | "
                f"category={issue['category']} | message={issue['message']} | "
                f"handles={handles} | repairable={issue['repairable']}"
            )
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## AutoCAD 状态与证据",
            "",
            f"- 状态：{audit['autocad']['status']}",
        ]
    )
    lines.extend(f"- {item}" for item in audit["autocad"].get("evidence", []))
    lines.extend(["", "## 来源追溯", ""])
    provenance = audit.get("provenance", {})
    for key in (
        "spec_source",
        "spec_source_sha256",
        "spec_canonical_sha256",
        "canonical_spec",
        "canonical_spec_file_sha256",
        "canonical_spec_owned",
        "canonical_spec_reused",
        "source_fragments",
        "source_fragments_sha256",
    ):
        if key in provenance:
            lines.append(f"- {key}: {provenance[key]}")
    lines.extend(["", "## 发布产物", ""])
    artifacts = sorted(audit.get("artifacts", []), key=lambda item: str(item["path"]))
    if artifacts:
        for artifact in artifacts:
            lines.append(
                f"- {artifact['name']} | size={artifact['size']} bytes | "
                f"SHA-256={artifact['sha256']} | validation={artifact['validation']}"
            )
    else:
        lines.append("- 无已验证发布产物")
    lines.extend([
        "",
        "## 字体渲染证据",
        "",
    ])
    evidence = audit["font_render"]["evidence"]
    lines.extend(f"- {item}" for item in evidence)
    lines.extend(["", "## 结论", ""])
    if status == "partial":
        lines.append("DXF、SVG 和 PNG 已通过复开与审图；未验证 DWG/PDF，因此不声称完整完成。")
    elif status == "complete":
        lines.append("全部请求文件均已重新验证。")
    else:
        lines.append("核心图纸仍有未解决问题，未发布未通过的 DXF 或预览。")
    return "\n".join(lines) + "\n"


__all__ = ["PipelineInputError", "PipelineOptions", "PipelineResult", "run_task"]
