"""Failure-to-Regression Flywheel (`cad-regression-case/v1`).

Turns real-world revision bugs, unit-scaling pitfalls, and user corrections into
permanent, self-contained regression bundles (`baseline.dxf` + `case.json`) that
can be recorded in one call and replayed automatically in pytest / CI / MCP.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from cad_skill_core.revision.common import RevisionError, sha256_file
from cad_skill_core.revision.nl_revise import execute_nl_region_revision
from cad_skill_core.revision.post_verify import PostConditionSpec, verify_revision_post_conditions


class RegressionCaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["cad-regression-case/v1"] = "cad-regression-case/v1"
    case_id: str
    title: str
    baseline_dxf: str = "baseline.dxf"
    baseline_sha256: str
    prompt: str
    candidate_handles: list[str] = Field(default_factory=list)
    region_box: list[float] | None = None
    structured_ir: dict[str, Any] | None = None
    must_reject: bool = False
    expected_post_conditions: list[PostConditionSpec] = Field(default_factory=list)
    bug_note: str = ""
    recorded_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _slugify_case_id(raw: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw.strip()).strip("-").lower()
    if not s:
        raise ValueError("case_id cannot be empty")
    return s[:64]


def record_regression_case(
    vault_dir: Path | str,
    *,
    case_id: str,
    title: str,
    source_dxf_path: Path | str,
    prompt: str,
    candidate_handles: Sequence[str] | None = None,
    region_box: Sequence[float] | None = None,
    structured_ir: dict[str, Any] | None = None,
    must_reject: bool = False,
    expected_post_conditions: Sequence[PostConditionSpec | dict[str, Any]] = (),
    bug_note: str = "",
    overwrite: bool = True,
) -> dict[str, Any]:
    """Archive `source_dxf_path` + `prompt` + `expected_post_conditions` into `vault_dir/<case_id>`."""
    vault = Path(vault_dir).expanduser().resolve()
    slug = _slugify_case_id(case_id)
    case_dir = vault / slug
    if case_dir.exists() and not overwrite:
        raise FileExistsError(f"regression case already exists: {case_dir}")
    case_dir.mkdir(parents=True, exist_ok=True)

    src = Path(source_dxf_path).expanduser().resolve(strict=True)
    dst_dxf = case_dir / "baseline.dxf"
    if src != dst_dxf:
        shutil.copy2(src, dst_dxf)
    digest = sha256_file(dst_dxf)

    from pydantic import TypeAdapter

    adapter: TypeAdapter[list[PostConditionSpec]] = TypeAdapter(list[PostConditionSpec])
    parsed_checks = adapter.validate_python(list(expected_post_conditions))

    manifest = RegressionCaseManifest(
        case_id=slug,
        title=title.strip() or slug,
        baseline_dxf="baseline.dxf",
        baseline_sha256=digest,
        prompt=prompt,
        candidate_handles=[str(h).upper() for h in (candidate_handles or []) if h],
        region_box=[float(v) for v in region_box] if region_box is not None else None,
        structured_ir=structured_ir,
        must_reject=must_reject,
        expected_post_conditions=parsed_checks,
        bug_note=bug_note,
    )
    manifest_path = case_dir / "case.json"
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "status": "recorded",
        "case_id": slug,
        "case_dir": str(case_dir),
        "manifest_path": str(manifest_path),
        "baseline_sha256": digest,
        "post_condition_count": len(parsed_checks),
        "must_reject": must_reject,
    }


def list_regression_cases(vault_dir: Path | str) -> list[dict[str, Any]]:
    vault = Path(vault_dir).expanduser().resolve()
    if not vault.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for p in sorted(vault.glob("*/case.json")):
        data = RegressionCaseManifest.model_validate(json.loads(p.read_text(encoding="utf-8")))
        items.append(
            {
                "case_id": data.case_id,
                "title": data.title,
                "must_reject": data.must_reject,
                "post_condition_count": len(data.expected_post_conditions),
                "case_dir": str(p.parent),
            }
        )
    return items


def replay_regression_case(case_dir: Path | str) -> dict[str, Any]:
    """Replay one recorded regression case in an isolated workspace and verify post-conditions."""
    cdir = Path(case_dir).expanduser().resolve(strict=True)
    manifest_path = cdir / "case.json"
    manifest = RegressionCaseManifest.model_validate(
        json.loads(manifest_path.read_text(encoding="utf-8"))
    )
    base_dxf = cdir / manifest.baseline_dxf
    actual_sha = sha256_file(base_dxf)
    if actual_sha.lower() != manifest.baseline_sha256.lower():
        return {
            "case_id": manifest.case_id,
            "passed": False,
            "reason": f"baseline DXF sha256 mismatch: expected {manifest.baseline_sha256[:12]}, got {actual_sha[:12]}",
        }

    with tempfile.TemporaryDirectory() as tmp_d:
        work_dxf = Path(tmp_d) / "work_baseline.dxf"
        shutil.copy2(base_dxf, work_dxf)
        out_dir = Path(tmp_d) / "out"

        if manifest.must_reject:
            try:
                execute_nl_region_revision(
                    work_dxf,
                    out_dir,
                    manifest.prompt,
                    region_box=manifest.region_box,
                    candidate_handles=manifest.candidate_handles or None,
                    structured_ir=manifest.structured_ir,
                )
            except RevisionError as exc:
                untouched = sha256_file(work_dxf) == actual_sha and not (out_dir / "revised.dxf").exists()
                return {
                    "case_id": manifest.case_id,
                    "title": manifest.title,
                    "passed": untouched,
                    "mode": "must_reject",
                    "detail": f"expected rejection verified ({exc})",
                }
            return {
                "case_id": manifest.case_id,
                "title": manifest.title,
                "passed": False,
                "mode": "must_reject",
                "detail": "expected RevisionError rejection, but revision succeeded!",
            }

        try:
            res = execute_nl_region_revision(
                work_dxf,
                out_dir,
                manifest.prompt,
                region_box=manifest.region_box,
                candidate_handles=manifest.candidate_handles or None,
                structured_ir=manifest.structured_ir,
            )
        except Exception as exc:
            return {
                "case_id": manifest.case_id,
                "title": manifest.title,
                "passed": False,
                "mode": "apply",
                "detail": f"execution raised {type(exc).__name__}: {exc}",
            }

        rev_dxf = Path(res["output_path"])
        ver = verify_revision_post_conditions(work_dxf, rev_dxf, manifest.expected_post_conditions)
        return {
            "case_id": manifest.case_id,
            "title": manifest.title,
            "passed": bool(ver["passed"]),
            "mode": "apply",
            "verification": ver,
            "detail": res.get("summary_zh", ""),
        }


def replay_regression_vault(vault_dir: Path | str) -> dict[str, Any]:
    """Replay all recorded regression cases inside `vault_dir`."""
    vault = Path(vault_dir).expanduser().resolve()
    case_manifests = sorted(vault.glob("*/case.json")) if vault.is_dir() else []
    results = [replay_regression_case(m.parent) for m in case_manifests]
    passed_cnt = sum(1 for r in results if r["passed"])
    return {
        "vault_dir": str(vault),
        "total": len(results),
        "passed": passed_cnt,
        "failed": len(results) - passed_cnt,
        "all_passed": passed_cnt == len(results),
        "cases": results,
    }
