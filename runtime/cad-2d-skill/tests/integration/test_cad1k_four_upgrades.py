from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import sys

import ezdxf
from fastapi.testclient import TestClient
import pytest

from cad_skill_core.benchmark.cad1k_harness import load_cad1k_task_bundle, run_cad1k_benchmark
from cad_skill_core.documents.spec_compiler import compile_spec_from_files
from cad_skill_core.geometry.parametric import verify_orthographic_consistency
from cad_skill_core.inspection.rubric_scorer import score_dxf_drawing
from cad_skill_core.mcp.http_app import create_mcp_http_app
from cad_skill_core.pipeline import PipelineOptions, run_task

RUNTIME_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_GEAR_BUNDLE = RUNTIME_ROOT / "tests" / "fixtures" / "cad1k_sample_tasks" / "gear_workflow_01"


@pytest.mark.parametrize(
    ("family_slug", "brief_md", "expected_kind"),
    [
        (
            "spur-gear",
            (
                "# 直齿圆柱齿轮\n"
                "图号：GEAR-01\n"
                "材料：45钢\n"
                "模数：3 mm\n"
                "齿数：28\n"
                "齿宽：30 mm\n"
                "轴孔：24 mm\n"
            ),
            "spur_gear",
        ),
        (
            "bearing-cap",
            (
                "# 减速器轴承透盖\n"
                "图号：CAP-02\n"
                "材料：HT200\n"
                "外径：130 mm\n"
                "止口直径：90 mm\n"
                "内孔：42 mm\n"
                "高度：32 mm\n"
                "厚度：12 mm\n"
                "节圆直径：110 mm\n"
                "安装孔：6×Φ9\n"
            ),
            "bearing_cap",
        ),
        (
            "ribbed-bracket",
            (
                "# 带加强筋L型直角支座\n"
                "图号：BRK-RIB-03\n"
                "材料：Q235B\n"
                "底板长：130 mm\n"
                "底板宽：84 mm\n"
                "总高：92 mm\n"
                "厚度：14 mm\n"
                "加强筋厚：10 mm\n"
                "轴承孔：28 mm\n"
                "安装孔径：12 mm\n"
            ),
            "ribbed_bracket",
        ),
    ],
)
def test_three_new_classic_mechanical_families_pass_orthographic_audit_and_score_s_grade(
    tmp_path, family_slug, brief_md, expected_kind
):
    task_dir = tmp_path / family_slug
    (task_dir / "input").mkdir(parents=True)
    (task_dir / "work").mkdir(parents=True)
    brief_path = task_dir / "input" / "brief.md"
    brief_path.write_text(brief_md, encoding="utf-8")

    spec = compile_spec_from_files([brief_path], output_path=task_dir / "work" / "drawing-spec.json")
    assert spec.validation.missing == []
    assert spec.validation.conflicts == []
    assert spec.geometry.parameters.get("part_family") == family_slug
    assert spec.requirements.views == ["front", "top", "left"]

    assert verify_orthographic_consistency(spec.geometry.entities, spec.geometry.view_entity_indices) == []

    result = run_task(task_dir, PipelineOptions(run_autocad=False))
    assert result.status in {"passed", "partial", "complete"}
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["summary"]["errors"] == 0

    rubric = score_dxf_drawing(task_dir / "output" / "drawing.dxf", audit_json_path=result.audit_path)
    assert rubric.total_score >= 95.0, f"{family_slug} scored {rubric.total_score}: {rubric.to_dict()}"
    assert rubric.grade == "S"


@pytest.mark.parametrize(
    "preset_id",
    ["gb-mechanical", "gb-heavy-machinery", "gb-civil-construction", "iso-international"],
)
def test_all_four_industry_standard_presets_render_cleanly(tmp_path, preset_id):
    task_dir = tmp_path / f"std_{preset_id}"
    (task_dir / "input").mkdir(parents=True)
    (task_dir / "work").mkdir(parents=True)
    brief_path = task_dir / "input" / "brief.md"
    brief_path.write_text(
        "# Spur Gear\nDrawing No: ISO-G01\nMaterial: 42CrMo\n模数：3 mm\n齿数：24\n齿宽：28 mm\n轴孔：22 mm\n",
        encoding="utf-8",
    )
    compile_spec_from_files([brief_path], output_path=task_dir / "work" / "drawing-spec.json")
    res = run_task(task_dir, PipelineOptions(run_autocad=False, standard_profile=preset_id))
    assert res.status in {"passed", "partial", "complete"}

    doc = ezdxf.readfile(task_dir / "output" / "drawing.dxf")
    texts = [
        str(e.dxf.text)
        for e in doc.modelspace()
        if e.dxftype() == "TEXT"
    ]
    joined = " | ".join(texts)
    if preset_id == "iso-international":
        assert "FRONT" in joined
        assert "TOP" in joined
        assert "LEFT" in joined
        assert "DWG NO." in joined
    else:
        assert "主视图" in joined
        assert "俯视图" in joined
        assert "左视图" in joined

    rubric = score_dxf_drawing(task_dir / "output" / "drawing.dxf", audit_json_path=res.audit_path)
    assert rubric.total_score >= 95.0
    assert rubric.grade == "S"


def test_video_free_cad1k_loader_skips_mp4_and_runs_full_benchmark_and_http_api(tmp_path):
    # 1. Verify video-free bundle loader explicitly skips clip.mp4 and events.json
    bundle_copy = tmp_path / "ext_bundle"
    bundle_copy.mkdir(parents=True)
    for f in SAMPLE_GEAR_BUNDLE.iterdir():
        (bundle_copy / f.name).write_bytes(f.read_bytes())
    # Create dummy heavy filenames to verify they are skipped and never parsed
    (bundle_copy / "clip.mp4").write_bytes(b"NOT_A_REAL_VIDEO_SKIP_ME")
    (bundle_copy / "events.json").write_text("INVALID JSON THAT MUST BE SKIPPED", encoding="utf-8")

    loaded = load_cad1k_task_bundle(bundle_copy)
    assert loaded.task_id == "CAD1K-SAMPLE-GEAR-01"
    assert "clip.mp4" in loaded.skipped_heavy_assets
    assert "events.json" in loaded.skipped_heavy_assets
    assert len(loaded.narration_steps) == 3

    # 2. Run built-in 4-task benchmark (Gear + Bearing Cap + Ribbed Bracket + DXF Local Revision)
    bench_out = tmp_path / "bench_out"
    summary = run_cad1k_benchmark(bench_out)
    assert summary["task_count"] == 4
    assert summary["passed_count"] == 4
    assert summary["average_score"] >= 94.0

    # 3. Verify CLI `benchmark-cad1k --bundle ... --json`
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = str(RUNTIME_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    cli_out = tmp_path / "cli_bench_out"
    proc = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "cad_skill_core.cli",
            "benchmark-cad1k",
            "--bundle",
            str(bundle_copy),
            "--out",
            str(cli_out),
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=True,
    )
    cli_payload = json.loads(proc.stdout)
    assert cli_payload["command"] == "benchmark-cad1k"
    assert cli_payload["passed_count"] == 1
    assert cli_payload["tasks"][0]["grade"] == "S"

    # 4. Verify FastAPI HTTP Gateway (/api/v1/standards, /api/v1/tasks/generate, /api/v1/tasks/score, /api/v1/uploads, /studio)
    app = create_mcp_http_app(
        tmp_path / "http_ws",
        allowed_read_roots=[bundle_copy],
    )
    client = TestClient(app)
    try:
        stds = client.get("/api/v1/standards").json()["standards"]
        assert len(stds) == 4

        studio_html = client.get("/studio").text
        assert "直齿圆柱齿轮" in studio_html
        assert "减速器轴承透盖" in studio_html
        assert "带加强筋L型支座" in studio_html
        assert "iso-international" in studio_html
        assert "图纸评分:" in studio_html

        gen_resp = client.post(
            "/api/v1/tasks/generate",
            json={
                "domain": "mechanical_2d",
                "markdown_brief": "设计一个45钢直齿圆柱齿轮，模数3齿数28齿宽30轴孔24",
                "standard_profile": "gb-heavy-machinery",
            },
        )
        assert gen_resp.status_code == 200
        gen_data = gen_resp.json()
        assert gen_data["standard_profile"] == "gb-heavy-machinery"
        assert gen_data["rubric_score"]["total_score"] >= 95.0
        assert gen_data["rubric_score"]["grade"] == "S"

        score_resp = client.post(
            "/api/v1/tasks/score",
            json={"task_id": gen_data["task_id"], "artifact_name": "drawing.dxf"},
        )
        assert score_resp.status_code == 200
        assert score_resp.json()["grade"] == "S"

        # Upload DXF also returns rubric_score automatically
        sample_dxf_bytes = (cli_out / "CAD1K-SAMPLE-GEAR-01" / "output" / "drawing.dxf").read_bytes()
        up_resp = client.post(
            "/api/v1/uploads",
            json={
                "filename": "gear_check.dxf",
                "content_base64": base64.b64encode(sample_dxf_bytes).decode("ascii"),
            },
        )
        assert up_resp.status_code == 200
        assert up_resp.json()["rubric_score"]["total_score"] >= 95.0
    finally:
        app.state.job_manager.shutdown()
