from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path
import shutil
import subprocess

import pytest
from pypdf import PdfWriter

from cad_skill_core.autocad import AutoCADResult
from cad_skill_core import pipeline
from cad_skill_core.pipeline import PipelineInputError, PipelineOptions, run_task
from cad_skill_core.task import prepare_task
from tests.helpers import copy_example


def test_pipeline_produces_traceable_core_outputs(tmp_path):
    task_dir = copy_example("simple-flange", tmp_path)

    result = run_task(task_dir, PipelineOptions(run_autocad=False))

    assert result.status == "partial"
    assert result.audit_path == task_dir / "output" / "audit.json"
    for name in [
        "drawing.dxf",
        "drawing.svg",
        "drawing.png",
        "audit.json",
        "audit-report.md",
    ]:
        assert (task_dir / "output" / name).stat().st_size > 0
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["summary"]["errors"] == 0
    assert audit["summary"]["warnings"] == 1
    assert audit["issues"][0]["code"] == "AUTOCAD_DISABLED"
    assert audit["status"] == "partial"
    assert audit["autocad"]["status"] == "disabled"
    assert audit["provenance"]["spec_source_sha256"]
    assert audit["provenance"]["spec_canonical_sha256"]
    canonical_spec = task_dir / audit["provenance"]["canonical_spec"]
    fragments = task_dir / audit["provenance"]["source_fragments"]
    assert canonical_spec.is_file()
    assert audit["provenance"]["canonical_spec_file_sha256"] == hashlib.sha256(
        canonical_spec.read_bytes()
    ).hexdigest()
    assert audit["provenance"]["source_fragments_sha256"] == hashlib.sha256(
        fragments.read_bytes()
    ).hexdigest()
    assert audit["font_render"]["evidence"][0].startswith(
        "work/iterations/01/drawing.dxf reopened"
    )
    assert all(artifact["sha256"] for artifact in audit["artifacts"])
    assert {path.name for path in result.outputs} == {
        "drawing.dxf",
        "drawing.svg",
        "drawing.png",
        "audit.json",
        "audit-report.md",
    }


@pytest.mark.parametrize("value", [-1, 4, True, 1.5])
def test_pipeline_options_reject_unbounded_repair_counts(value):
    with pytest.raises((TypeError, ValueError), match="0 through 3"):
        PipelineOptions(max_repair_iterations=value)


def test_pipeline_options_accept_zero_and_require_absolute_autocad_path(tmp_path):
    assert PipelineOptions(max_repair_iterations=0).max_repair_iterations == 0
    with pytest.raises(ValueError, match="absolute"):
        PipelineOptions(autocad_path=Path("acad.exe"))
    explicit = tmp_path / "AutoCAD 2023" / "accoreconsole.exe"
    assert PipelineOptions(autocad_path=explicit).autocad_path == explicit.resolve()


def test_pipeline_versions_outputs_instead_of_overwriting_unowned_files(tmp_path):
    task_dir = copy_example("simple-flange", tmp_path)
    output = task_dir / "output"
    output.mkdir()
    sentinel = output / "drawing.dxf"
    sentinel.write_bytes(b"unrelated user drawing")

    result = run_task(task_dir, PipelineOptions(run_autocad=False))

    assert sentinel.read_bytes() == b"unrelated user drawing"
    assert (output / "drawing-v2.dxf") in result.outputs
    assert result.audit_path == output / "audit-v2.json"


def test_pipeline_rerun_preserves_first_owned_generation(tmp_path):
    task_dir = copy_example("simple-flange", tmp_path)
    first = run_task(task_dir, PipelineOptions(run_autocad=False))
    first_hashes = {path: path.read_bytes() for path in first.outputs}

    second = run_task(task_dir, PipelineOptions(run_autocad=False))

    assert all(path.read_bytes() == payload for path, payload in first_hashes.items())
    assert second.audit_path.name == "audit-v2.json"
    assert {path.name for path in second.outputs} >= {
        "drawing-v2.dxf",
        "drawing-v2.svg",
        "drawing-v2.png",
    }


def test_pipeline_writes_failure_audit_without_publishing_failed_core_files(
    tmp_path, monkeypatch
):
    task_dir = copy_example("simple-flange", tmp_path)
    spec_path = task_dir / "work" / "drawing-spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["validation"] = {"missing": ["critical geometry confirmation"]}
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    def drawing_must_not_start(*args, **kwargs):
        raise AssertionError("unresolved critical specification entered generation")

    monkeypatch.setattr("cad_skill_core.pipeline.run_repair_loop", drawing_must_not_start)

    result = run_task(task_dir, PipelineOptions(run_autocad=False))

    assert result.status == "failed"
    assert not (task_dir / "output" / "drawing.dxf").exists()
    assert {path.name for path in result.outputs} == {"audit.json", "audit-report.md"}
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["summary"]["errors"] > 0
    assert audit["issues"][0]["code"] == "SPEC_MISSING"
    assert "critical geometry confirmation" in audit["issues"][0]["message"]


def test_invalid_spec_is_exit_two_input_error_with_failure_audit(tmp_path):
    task_dir = copy_example("simple-flange", tmp_path)
    (task_dir / "work" / "drawing-spec.json").write_text("{}", encoding="utf-8")

    with pytest.raises(PipelineInputError, match="specification is invalid"):
        run_task(task_dir, PipelineOptions())

    audit_path = task_dir / "output" / "audit.json"
    assert audit_path.is_file()
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["status"] == "failed"
    assert audit["issues"][0]["code"] == "INPUT_SPEC_INVALID"


@pytest.mark.parametrize("fact", ["material", "note", "part"])
@pytest.mark.parametrize("source", [None, "unknown-source"])
def test_unsourced_manufacturing_fact_cannot_publish_core_outputs(tmp_path, fact, source):
    task = copy_example("flange-assembly", tmp_path)
    spec_path = task / "work" / "drawing-spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    target = {
        "material": spec["requirements"]["material"],
        "note": spec["requirements"]["technical_notes"][0],
        "part": spec["parts_list"][0],
    }[fact]
    if source is None:
        target.pop("source_id")
    else:
        target["source_id"] = source
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    before = spec_path.read_bytes()

    with pytest.raises(PipelineInputError, match="specification is invalid"):
        run_task(task, PipelineOptions(run_autocad=False))

    assert spec_path.read_bytes() == before
    assert not list((task / "output").glob("drawing.*"))
    audit = json.loads((task / "output" / "audit.json").read_text(encoding="utf-8"))
    assert audit["status"] == "failed"
    assert audit["issues"][0]["code"] == "INPUT_SPEC_INVALID"


def test_material_without_same_source_declaration_cannot_publish_core_outputs(tmp_path):
    task = copy_example("flange-assembly", tmp_path)
    spec_path = task / "work" / "drawing-spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["requirements"]["technical_notes"][2]["source_id"] = "drawing-rule"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")

    result = run_task(task, PipelineOptions(run_autocad=False))

    assert result.status == "failed"
    assert not any(path.suffix in {".dxf", ".svg", ".png"} for path in result.outputs)
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    issue = next(item for item in audit["issues"] if item["code"] == "SEMANTIC_MISSING_MATERIAL")
    assert issue["repairable"] is False
    assert "source-bound" in issue["message"]


def test_material_inventory_excludes_same_text_different_source_note(tmp_path):
    task = copy_example("flange-assembly", tmp_path)
    spec_path = task / "work" / "drawing-spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    material = spec["requirements"]["material"]["text"]
    spec["requirements"]["technical_notes"][0]["text"] = material
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")

    result = run_task(task, PipelineOptions(run_autocad=False))

    assert result.status == "partial"
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    material_record = next(
        item for item in audit["semantic_inventory"] if item["semantic_kind"] == "material"
    )
    source_note = next(
        item
        for item in audit["semantic_inventory"]
        if item["semantic_kind"] == "technical_note"
        and item["source_id"] == "demo-material"
    )
    assert material_record["handles"] == source_note["handles"]


def test_autocad_success_requires_verified_dwg_and_pdf(tmp_path, monkeypatch):
    task_dir = copy_example("simple-flange", tmp_path)

    def fake_execute(request):
        request.output_dir.mkdir(parents=True, exist_ok=True)
        dwg = request.output_dir / "drawing.dwg"
        pdf = request.output_dir / "drawing.pdf"
        dwg.write_bytes(b"AC1032" + b"\0" * 1024)
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with pdf.open("wb") as destination:
            writer.write(destination)
        return AutoCADResult(
            "succeeded",
            0,
            dwg,
            pdf,
            None,
            None,
            None,
            0.1,
            evidence=("DWG reopened by Core Console", "PDF parsed with one page"),
        )

    monkeypatch.setattr("cad_skill_core.pipeline.execute_autocad", fake_execute)

    result = run_task(task_dir, PipelineOptions(run_autocad=True))

    assert result.status == "complete"
    assert {path.suffix for path in result.outputs} >= {".dxf", ".svg", ".png", ".dwg", ".pdf"}
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["autocad"]["status"] == "verified"
    assert audit["summary"]["warnings"] == 0
    assert "return code" not in " ".join(audit["autocad"]["evidence"]).lower()


def test_autocad_exit_zero_with_invalid_outputs_cannot_complete(tmp_path, monkeypatch):
    task_dir = copy_example("simple-flange", tmp_path)

    def fake_execute(request):
        request.output_dir.mkdir(parents=True, exist_ok=True)
        dwg = request.output_dir / "drawing.dwg"
        pdf = request.output_dir / "drawing.pdf"
        dwg.write_bytes(b"not a dwg")
        pdf.write_bytes(b"not a pdf")
        return AutoCADResult(
            "succeeded", 0, dwg, pdf, None, None, None, 0.1,
            evidence=("process exited zero",),
        )

    monkeypatch.setattr("cad_skill_core.pipeline.execute_autocad", fake_execute)

    result = run_task(task_dir, PipelineOptions(run_autocad=True))

    assert result.status == "partial"
    assert not any(path.suffix in {".dwg", ".pdf"} for path in result.outputs)
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["autocad"]["status"] == "failed"
    assert audit["summary"]["warnings"] == 1
    assert any(issue["code"] == "AUTOCAD_FAILED" for issue in audit["issues"])


def test_pipeline_verifies_chinese_text_with_installed_ttc_fonts(tmp_path):
    task_dir = copy_example("simple-flange", tmp_path)
    spec_path = task_dir / "work" / "drawing-spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["project"]["name"] = "法兰"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")

    result = run_task(task_dir, PipelineOptions(run_autocad=False))

    assert result.status == "partial"
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["font_render"]["verified_roles"] == ["TEXT"]


def test_missing_spec_is_controlled_and_still_writes_audit(tmp_path):
    task_dir = copy_example("simple-flange", tmp_path)
    (task_dir / "work" / "drawing-spec.json").unlink()

    with pytest.raises(PipelineInputError, match="not found"):
        run_task(task_dir)

    audit = json.loads((task_dir / "output" / "audit.json").read_text(encoding="utf-8"))
    assert audit["issues"][0]["code"] == "INPUT_SPEC_MISSING"


def test_generation_exception_returns_failed_result_with_audit(tmp_path, monkeypatch):
    task_dir = copy_example("simple-flange", tmp_path)

    def fail_generation(*args, **kwargs):
        raise RuntimeError("controlled writer failure")

    monkeypatch.setattr("cad_skill_core.pipeline.run_repair_loop", fail_generation)

    result = run_task(task_dir)

    assert result.status == "failed"
    assert result.audit_path.is_file()
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["issues"][0]["code"] == "CORE_PIPELINE_FAILED"


def test_task_rejects_output_reparse_escape_without_touching_target(tmp_path):
    task_dir = copy_example("simple-flange", tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("unrelated", encoding="utf-8")
    output = task_dir / "output"
    try:
        os.symlink(external, output, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")

    with pytest.raises(PipelineInputError, match="symlink or reparse"):
        run_task(task_dir)

    assert sentinel.read_text(encoding="utf-8") == "unrelated"
    assert list(external.iterdir()) == [sentinel]


def test_atomic_publish_never_overwrites_competitor_created_after_allocation(
    tmp_path, monkeypatch
):
    task_dir = copy_example("simple-flange", tmp_path)
    paths = prepare_task(task_dir)
    source = paths.work / "verified.txt"
    source.write_text("pipeline", encoding="utf-8")
    destination = paths.output / "drawing.md"
    original_link = os.link

    def competitor_wins(source_path, destination_path):
        Path(destination_path).write_text("competitor", encoding="utf-8")
        return original_link(source_path, destination_path)

    monkeypatch.setattr(pipeline.os, "link", competitor_wins)

    with pytest.raises(PipelineInputError, match="competitor"):
        pipeline._publish_file(source, destination, paths)

    assert destination.read_text(encoding="utf-8") == "competitor"


@pytest.mark.skipif(os.name != "nt", reason="junction replacement is Windows-specific")
def test_pipeline_rejects_output_junction_replacement_after_preparation(
    tmp_path, monkeypatch
):
    task_dir = copy_example("simple-flange", tmp_path)
    external = tmp_path / "external-output"
    external.mkdir()
    real_repair = pipeline.run_repair_loop

    def replace_prepared_output(*args, **kwargs):
        result = real_repair(*args, **kwargs)
        output = task_dir / "output"
        output.rmdir()
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(output), str(external)],
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
        )
        if created.returncode != 0:
            pytest.skip(f"could not create test junction: {created.stderr}")
        return result

    monkeypatch.setattr(pipeline, "run_repair_loop", replace_prepared_output)

    result = run_task(task_dir, PipelineOptions(run_autocad=False))

    assert result.status == "failed"
    assert result.audit_path.is_file()
    assert task_dir in result.audit_path.parents
    assert list(external.iterdir()) == []


def test_validated_source_change_aborts_whole_publication(tmp_path):
    task_dir = copy_example("simple-flange", tmp_path)
    paths = prepare_task(task_dir)
    source = paths.work / "validated.md"
    source.write_text("validated", encoding="utf-8")
    artifact = pipeline._snapshot_artifact(source, "markdown", "drawing.md")
    source.write_text("changed after validation", encoding="utf-8")

    with pytest.raises(PipelineInputError, match="changed after validation"):
        pipeline._publish_transaction(
            paths,
            (artifact,),
            {"drawing.md": "drawing.md", "manifest.json": "manifest.json"},
        )

    assert not (paths.output / "drawing.md").exists()
    assert not (paths.output / "manifest.json").exists()


def test_multi_artifact_publication_rolls_back_before_commit_marker(
    tmp_path, monkeypatch
):
    task_dir = copy_example("simple-flange", tmp_path)
    paths = prepare_task(task_dir)
    first = paths.work / "first.md"
    second = paths.work / "second.md"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    artifacts = (
        pipeline._snapshot_artifact(first, "markdown", "first.md"),
        pipeline._snapshot_artifact(second, "markdown", "second.md"),
    )
    real_link = os.link

    def fail_second(source_path, destination_path):
        if Path(destination_path).name == "second.md":
            raise OSError("injected second publication failure")
        return real_link(source_path, destination_path)

    monkeypatch.setattr(pipeline.os, "link", fail_second)

    with pytest.raises(PipelineInputError, match="second publication failure") as failure:
        pipeline._publish_transaction(
            paths,
            artifacts,
            {
                "first.md": "first.md",
                "second.md": "second.md",
                "manifest.json": "manifest.json",
            },
        )

    assert not (paths.output / "first.md").exists(), str(failure.value)
    assert not (paths.output / "second.md").exists()
    assert not (paths.output / "manifest.json").exists()


def test_post_link_competitor_is_not_deleted_and_manifest_never_commits(
    tmp_path, monkeypatch
):
    task_dir = copy_example("simple-flange", tmp_path)
    paths = prepare_task(task_dir)
    source = paths.work / "validated.md"
    source.write_text("validated", encoding="utf-8")
    artifact = pipeline._snapshot_artifact(source, "markdown", "drawing.md")
    real_link = os.link

    def replace_after_no_clobber(source_path, destination_path):
        real_link(source_path, destination_path)
        destination = Path(destination_path)
        destination.unlink()
        destination.write_text("competitor", encoding="utf-8")

    monkeypatch.setattr(pipeline.os, "link", replace_after_no_clobber)

    with pytest.raises(PipelineInputError, match="identity changed"):
        pipeline._publish_transaction(
            paths,
            (artifact,),
            {"drawing.md": "drawing.md", "manifest.json": "manifest.json"},
        )

    assert (paths.output / "drawing.md").read_text(encoding="utf-8") == "competitor"
    assert not (paths.output / "manifest.json").exists()


def test_unsafe_rollback_is_disclosed_in_external_transaction_failure_audit(
    tmp_path, monkeypatch
):
    task_dir = copy_example("simple-flange", tmp_path)
    real_publish = pipeline._publish_validated_artifact

    def fail_audit(paths, artifact, destination):
        if artifact.logical_name == "audit.json":
            raise pipeline._PublicationStepError("injected audit publication failure")
        return real_publish(paths, artifact, destination)

    monkeypatch.setattr(pipeline, "_publish_validated_artifact", fail_audit)
    monkeypatch.setattr(
        pipeline, "_delete_owned_publication", lambda _publication: "owned-retained"
    )

    result = run_task(task_dir, PipelineOptions(run_autocad=False))

    assert result.status == "failed"
    assert result.outputs == ()
    assert result.audit_path.parent == task_dir / "work" / "iterations"
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    message = audit["issues"][0]["message"]
    assert "retained partials" in message
    assert "drawing.dxf" in message
    assert ".run-" not in message
    assert str(task_dir) not in message
    assert not (task_dir / "output" / "pipeline-owned.json").exists()


def test_invalid_core_artifact_is_a_deterministic_error(tmp_path, monkeypatch):
    task_dir = copy_example("simple-flange", tmp_path)
    real_repair = pipeline.run_repair_loop

    def corrupt_png(*args, **kwargs):
        result = real_repair(*args, **kwargs)
        result.last_dxf.with_name("drawing.png").write_bytes(b"not a png")
        return result

    monkeypatch.setattr(pipeline, "run_repair_loop", corrupt_png)

    result = run_task(task_dir, PipelineOptions(run_autocad=False))

    assert result.status == "failed"
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["summary"]["errors"] >= 1
    issue = next(item for item in audit["issues"] if item["code"] == "PIPELINE_ARTIFACT_INVALID")
    assert issue["message"] == "core artifact failed PNG validation: drawing.png"
    assert not any(path.suffix in {".dxf", ".svg", ".png"} for path in result.outputs)


def test_equivalent_task_copies_publish_identical_deterministic_evidence(tmp_path):
    first_task = copy_example("simple-flange", tmp_path / "first")
    second_task = copy_example("simple-flange", tmp_path / "second")

    first = run_task(first_task, PipelineOptions(run_autocad=False))
    second = run_task(second_task, PipelineOptions(run_autocad=False))

    for name in (
        "drawing.dxf",
        "drawing.svg",
        "drawing.png",
        "audit.json",
        "audit-report.md",
        "pipeline-owned.json",
    ):
        assert (first_task / "output" / name).read_bytes() == (
            second_task / "output" / name
        ).read_bytes()
    for result, task_dir in ((first, first_task), (second, second_task)):
        audit_bytes = result.audit_path.read_bytes()
        report_bytes = result.audit_path.with_name("audit-report.md").read_bytes()
        assert b".run-" not in audit_bytes + report_bytes
        assert str(task_dir).encode() not in audit_bytes + report_bytes


def test_provenance_distinguishes_source_and_canonical_spec_hashes(tmp_path):
    task_dir = copy_example("simple-flange", tmp_path)
    source = task_dir / "work" / "drawing-spec.json"
    source_bytes = source.read_bytes()
    parsed = json.loads(source_bytes)

    result = run_task(task_dir, PipelineOptions(run_autocad=False))

    provenance = json.loads(result.audit_path.read_text(encoding="utf-8"))["provenance"]
    assert provenance["spec_source"] == "work/drawing-spec.json"
    assert provenance["spec_source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    canonical = pipeline._canonical_json(parsed)
    assert provenance["spec_canonical_sha256"] == hashlib.sha256(canonical).hexdigest()
    assert source.read_bytes() == source_bytes
    canonical_path = task_dir / provenance["canonical_spec"]
    assert canonical_path == task_dir / "work" / "drawing-spec.canonical.json"
    assert canonical_path.read_bytes() == canonical
    manifest = json.loads(
        (task_dir / "output" / "pipeline-owned.json").read_text(encoding="utf-8")
    )
    assert any(
        item["path"] == "work/drawing-spec.canonical.json"
        and item["sha256"] == hashlib.sha256(canonical).hexdigest()
        for item in manifest["artifacts"]
    )


def test_input_spec_is_unchanged_and_canonical_work_copy_is_owned(tmp_path):
    task_dir = copy_example("simple-flange", tmp_path)
    source = task_dir / "input" / "drawing-spec.json"
    shutil.move(task_dir / "work" / "drawing-spec.json", source)
    source.write_text(
        json.dumps(json.loads(source.read_text(encoding="utf-8")), ensure_ascii=False, indent=4),
        encoding="utf-8",
    )
    original = source.read_bytes()

    result = run_task(task_dir, PipelineOptions(run_autocad=False))

    assert result.status == "partial"
    assert source.read_bytes() == original
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    canonical = task_dir / audit["provenance"]["canonical_spec"]
    assert canonical.read_bytes() == pipeline._canonical_json(json.loads(original))
    manifest = json.loads((task_dir / "output" / "pipeline-owned.json").read_text(encoding="utf-8"))
    assert manifest["commit_marker"] is True
    assert any(
        item["path"] == audit["provenance"]["canonical_spec"]
        for item in manifest["artifacts"]
    )


def test_work_spec_canonical_copy_versions_around_unowned_collision(tmp_path):
    task_dir = copy_example("simple-flange", tmp_path)
    source = task_dir / "work" / "drawing-spec.json"
    source_bytes = source.read_bytes()
    collision = task_dir / "work" / "drawing-spec.canonical.json"
    collision.write_text('{"unrelated":true}\n', encoding="utf-8")

    result = run_task(task_dir, PipelineOptions(run_autocad=False))

    assert result.status == "partial"
    assert source.read_bytes() == source_bytes
    assert collision.read_text(encoding="utf-8") == '{"unrelated":true}\n'
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    canonical = pipeline._canonical_json(json.loads(source_bytes))
    digest = hashlib.sha256(canonical).hexdigest()
    assert audit["provenance"]["canonical_spec"] == (
        f"work/drawing-spec.canonical-{digest}.json"
    )
    assert (task_dir / audit["provenance"]["canonical_spec"]).read_bytes() == (
        canonical
    )


def test_explicit_input_preserves_different_unowned_work_spec_and_uses_sibling(
    tmp_path
):
    task_dir = copy_example("simple-flange", tmp_path)
    work_source = task_dir / "work" / "drawing-spec.json"
    work_bytes = work_source.read_bytes()
    input_source = task_dir / "input" / "drawing-spec.json"
    input_value = json.loads(work_bytes)
    input_value["project"]["name"] = "Authoritative input"
    input_source.write_text(
        json.dumps(input_value, ensure_ascii=False, indent=4), encoding="utf-8"
    )
    input_bytes = input_source.read_bytes()

    result = run_task(task_dir, PipelineOptions(run_autocad=False))

    assert result.status == "partial"
    assert input_source.read_bytes() == input_bytes
    assert work_source.read_bytes() == work_bytes
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    provenance = audit["provenance"]
    assert provenance["spec_source"] == "input/drawing-spec.json"
    assert provenance["canonical_spec"] == "work/drawing-spec.canonical.json"
    assert provenance["canonical_spec_owned"] is True
    canonical = pipeline._canonical_json(input_value)
    canonical_path = task_dir / provenance["canonical_spec"]
    assert canonical_path.read_bytes() == canonical
    manifest = json.loads(
        (task_dir / "output" / "pipeline-owned.json").read_text(encoding="utf-8")
    )
    record = next(
        item for item in manifest["artifacts"] if item["path"] == provenance["canonical_spec"]
    )
    assert record["owned"] is True
    assert record["sha256"] == hashlib.sha256(canonical).hexdigest()


def test_changed_authoritative_input_creates_new_immutable_canonical_target(
    tmp_path
):
    task_dir = copy_example("simple-flange", tmp_path)
    input_source = task_dir / "input" / "drawing-spec.json"
    shutil.move(task_dir / "work" / "drawing-spec.json", input_source)
    first = run_task(task_dir, PipelineOptions(run_autocad=False))
    first_audit = json.loads(first.audit_path.read_text(encoding="utf-8"))
    canonical_path = task_dir / first_audit["provenance"]["canonical_spec"]
    original_canonical = canonical_path.read_bytes()
    changed = json.loads(input_source.read_text(encoding="utf-8"))
    changed["project"]["name"] = "Changed authoritative input"
    input_source.write_text(
        json.dumps(changed, ensure_ascii=False, indent=3), encoding="utf-8"
    )
    changed_source = input_source.read_bytes()

    second = run_task(task_dir, PipelineOptions(run_autocad=False))

    assert second.status == "partial"
    assert input_source.read_bytes() == changed_source
    second_audit = json.loads(second.audit_path.read_text(encoding="utf-8"))
    assert second_audit["provenance"]["canonical_spec"] != (
        first_audit["provenance"]["canonical_spec"]
    )
    assert canonical_path.read_bytes() == original_canonical
    assert (
        task_dir / second_audit["provenance"]["canonical_spec"]
    ).read_bytes() == pipeline._canonical_json(changed)
    assert second_audit["provenance"]["canonical_spec_owned"] is True


def test_equal_canonical_snapshot_is_reused_without_claiming_run_ownership(tmp_path):
    task_dir = copy_example("simple-flange", tmp_path)
    first = run_task(task_dir, PipelineOptions(run_autocad=False))
    first_audit = json.loads(first.audit_path.read_text(encoding="utf-8"))
    canonical_path = task_dir / first_audit["provenance"]["canonical_spec"]
    canonical_bytes = canonical_path.read_bytes()

    second = run_task(task_dir, PipelineOptions(run_autocad=False))

    assert second.status == "partial"
    assert canonical_path.read_bytes() == canonical_bytes
    second_audit = json.loads(second.audit_path.read_text(encoding="utf-8"))
    provenance = second_audit["provenance"]
    assert provenance["canonical_spec"] == first_audit["provenance"]["canonical_spec"]
    assert provenance["canonical_spec_owned"] is False
    assert provenance["canonical_spec_reused"] is True
    manifest = json.loads(
        (task_dir / "output" / "pipeline-owned-v2.json").read_text(encoding="utf-8")
    )
    record = next(
        item for item in manifest["artifacts"] if item["path"] == provenance["canonical_spec"]
    )
    assert record["owned"] is False
    assert record["reused"] is True


def test_modified_owned_canonical_is_preserved_and_hash_sibling_is_published(
    tmp_path
):
    task_dir = copy_example("simple-flange", tmp_path)
    input_source = task_dir / "input" / "drawing-spec.json"
    shutil.move(task_dir / "work" / "drawing-spec.json", input_source)
    first = run_task(task_dir, PipelineOptions(run_autocad=False))
    first_audit = json.loads(first.audit_path.read_text(encoding="utf-8"))
    owned_target = task_dir / first_audit["provenance"]["canonical_spec"]
    user_bytes = b'{"user_modified":true}\n'
    owned_target.write_bytes(user_bytes)
    changed = json.loads(input_source.read_text(encoding="utf-8"))
    changed["project"]["name"] = "Changed after user edit"
    input_source.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    canonical = pipeline._canonical_json(changed)
    digest = hashlib.sha256(canonical).hexdigest()

    second = run_task(task_dir, PipelineOptions(run_autocad=False))

    assert second.status == "partial"
    assert owned_target.read_bytes() == user_bytes
    audit = json.loads(second.audit_path.read_text(encoding="utf-8"))
    expected = f"work/drawing-spec.canonical-{digest}.json"
    assert audit["provenance"]["canonical_spec"] == expected
    assert audit["provenance"]["canonical_spec_owned"] is True
    assert (task_dir / expected).read_bytes() == canonical


@pytest.mark.skipif(os.name != "nt", reason="junction replacement is Windows-specific")
def test_canonical_writer_rejects_work_directory_replacement(tmp_path):
    task_dir = copy_example("simple-flange", tmp_path)
    paths = prepare_task(task_dir)
    original_work = task_dir / "original-work"
    paths.work.rename(original_work)
    external = tmp_path / "external-work"
    external.mkdir()
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(paths.work), str(external)],
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        errors="replace",
    )
    if created.returncode != 0:
        pytest.skip(f"could not create test junction: {created.stderr}")

    with pytest.raises(
        PipelineInputError,
        match="symlink or reparse|identity changed|escapes task directory",
    ):
        pipeline._write_canonical_work_variant(paths, b'{"valid":true}\n')

    assert list(external.iterdir()) == []


def _forge_work_ownership_manifest(task_dir, work_path):
    payload = work_path.read_bytes()
    forged = {
        "schema_version": "1.0",
        "commit_marker": True,
        "artifacts": [
            {
                "path": work_path.relative_to(task_dir).as_posix(),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
                "type": "json",
                "validation": "forged",
                "owned": True,
            }
        ],
    }
    (task_dir / "output").mkdir(exist_ok=True)
    (task_dir / "output" / "pipeline-owned.json").write_bytes(
        pipeline._canonical_json(forged, pretty=True)
    )


def test_forged_manifest_before_prepare_cannot_authorize_work_spec_overwrite(tmp_path):
    task_dir = copy_example("simple-flange", tmp_path)
    work_source = task_dir / "work" / "drawing-spec.json"
    original = work_source.read_bytes()
    input_value = json.loads(original)
    input_value["project"]["name"] = "Authoritative input"
    (task_dir / "input" / "drawing-spec.json").write_text(
        json.dumps(input_value, ensure_ascii=False), encoding="utf-8"
    )
    _forge_work_ownership_manifest(task_dir, work_source)

    result = run_task(task_dir, PipelineOptions(run_autocad=False))

    assert result.status == "partial"
    assert work_source.read_bytes() == original
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["provenance"]["canonical_spec"] != "work/drawing-spec.json"


def test_forged_manifest_after_prepare_cannot_authorize_work_spec_overwrite(
    tmp_path, monkeypatch
):
    task_dir = copy_example("simple-flange", tmp_path)
    work_source = task_dir / "work" / "drawing-spec.json"
    original = work_source.read_bytes()
    input_value = json.loads(original)
    input_value["project"]["name"] = "Authoritative input"
    (task_dir / "input" / "drawing-spec.json").write_text(
        json.dumps(input_value, ensure_ascii=False), encoding="utf-8"
    )
    real_prepare = pipeline.prepare_task

    def prepare_then_forge(path):
        paths = real_prepare(path)
        _forge_work_ownership_manifest(task_dir, work_source)
        return paths

    monkeypatch.setattr(pipeline, "prepare_task", prepare_then_forge)

    result = run_task(task_dir, PipelineOptions(run_autocad=False))

    assert result.status == "partial"
    assert work_source.read_bytes() == original
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["provenance"]["canonical_spec"] != "work/drawing-spec.json"


def test_canonical_mutation_after_materialization_fails_without_success_manifest(
    tmp_path, monkeypatch
):
    task_dir = copy_example("simple-flange", tmp_path)
    real_materialize = pipeline._materialize_canonical_spec
    injected = b'{"injected":true}\n'

    def materialize_then_mutate(*args, **kwargs):
        materialized = real_materialize(*args, **kwargs)
        path = getattr(materialized, "path", materialized)
        path.write_bytes(injected)
        return materialized

    monkeypatch.setattr(pipeline, "_materialize_canonical_spec", materialize_then_mutate)

    result = run_task(task_dir, PipelineOptions(run_autocad=False))

    assert result.status == "failed"
    assert not list((task_dir / "output").glob("pipeline-owned*.json"))
    assert result.audit_path.exists()
    assert injected not in result.audit_path.read_bytes()


def test_chinese_report_contains_complete_sorted_audit_evidence(tmp_path):
    task_dir = copy_example("simple-flange", tmp_path)

    result = run_task(task_dir, PipelineOptions(run_autocad=False))

    report = result.audit_path.with_name("audit-report.md").read_text(encoding="utf-8")
    for expected in (
        "## 问题明细",
        "AUTOCAD_DISABLED",
        "severity=warning",
        "category=structure",
        "repairable=False",
        "## AutoCAD 状态与证据",
        "## 来源追溯",
        "spec_source_sha256",
        "## 发布产物",
        "SHA-256",
        "## 字体渲染证据",
        "## 结论",
    ):
        assert expected in report
    assert ".run-" not in report
    assert str(task_dir) not in report
