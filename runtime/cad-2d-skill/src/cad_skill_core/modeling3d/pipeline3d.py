"""End-to-end 3D generation, verification, and optional 3D->2D engineering sheet orchestration."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from cad_skill_core.pipeline import PipelineOptions, run_task
from cad_skill_core.revision.common import sha256_file
from cad_skill_core.schema.io import save_spec

from .adapters import generate_autocad_3d_script, probe_3d_capabilities
from .exporters import (
    export_3d_dxf,
    export_binary_stl,
    export_glb,
    export_obj,
    render_isometric_png,
)
from .mesh_kernel import build_solid_mesh
from .models import Model3DSpec
from .projection import project_model3d_to_drawing_spec
from .verify import verify_3d_artifacts, write_audit_3d_reports


def load_model3d_spec(path: Path) -> Model3DSpec:
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return Model3DSpec.model_validate(raw)


def run_3d_task(
    spec_3d: Model3DSpec,
    output_dir: Path,
    *,
    with_2d_sheet: bool = False,
    circle_segments: int = 64,
    render_lock=None,
) -> dict[str, Any]:
    """Generate 3D artifacts (.stl, .obj, .glb, model3d.dxf, iso.png, audit-3d.json) and optional 2D sheet."""

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    spec_snapshot = out / "model-3d-spec.json"
    spec_snapshot.write_text(
        json.dumps(spec_3d.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    mesh = build_solid_mesh(spec_3d, circle_segments=circle_segments)

    stl_path = export_binary_stl(mesh, out / "model.stl", header_label=spec_3d.project.name)
    obj_path = export_obj(mesh, out / "model.obj", object_name=spec_3d.body.kind)
    glb_path = export_glb(mesh, out / "model.glb", name=spec_3d.project.name)
    dxf3d_path = export_3d_dxf(mesh, out / "model3d.dxf")
    iso_png_path = render_isometric_png(
        mesh,
        out / "iso.png",
        title=spec_3d.project.drawing_number or spec_3d.body.kind,
        render_lock=render_lock,
    )
    scr_path = out / "autocad-3d.scr"
    scr_path.write_text(
        generate_autocad_3d_script(spec_3d, out / "model3d.dwg"),
        encoding="utf-8",
    )

    audit_report = verify_3d_artifacts(
        spec_3d,
        stl_path=stl_path,
        glb_path=glb_path,
        dxf3d_path=dxf3d_path,
        expected_volume_mm3=mesh.expected_volume_mm3,
        expected_bbox_mm=mesh.expected_bbox_mm,
    )
    audit_json_path = out / "audit-3d.json"
    audit_md_path = out / "audit-3d-report.md"
    write_audit_3d_reports(
        audit_report,
        audit_json_path,
        audit_md_path,
        project_name=spec_3d.project.name,
    )

    published_files = [
        spec_snapshot,
        stl_path,
        obj_path,
        glb_path,
        dxf3d_path,
        iso_png_path,
        scr_path,
        audit_json_path,
        audit_md_path,
    ]
    artifacts = [
        {
            "name": p.name,
            "path": str(p.resolve()),
            "size": p.stat().st_size,
            "sha256": sha256_file(p),
        }
        for p in published_files
    ]

    sheet_2d_summary: dict[str, Any] | None = None
    overall_status = audit_report.status

    if with_2d_sheet and audit_report.status == "passed":
        sheet_task_dir = out / "sheet_2d"
        (sheet_task_dir / "input").mkdir(parents=True, exist_ok=True)
        (sheet_task_dir / "work").mkdir(parents=True, exist_ok=True)
        # Copy 3D spec into input/ for provenance record
        (sheet_task_dir / "input" / "model-3d-spec.md").write_text(
            f"# {spec_3d.project.name}\nProjected from 3D {spec_3d.body.kind}\n",
            encoding="utf-8",
        )
        spec_2d = project_model3d_to_drawing_spec(spec_3d)
        save_spec(spec_2d, sheet_task_dir / "work" / "drawing-spec.json")
        if render_lock is not None:
            with render_lock:
                res_2d = run_task(sheet_task_dir, PipelineOptions(run_autocad=False))
        else:
            res_2d = run_task(sheet_task_dir, PipelineOptions(run_autocad=False))
        audit_2d = json.loads(res_2d.audit_path.read_text(encoding="utf-8"))
        # Copy primary 2D outputs to top-level with distinct 2d-* names for single-folder convenience
        for src_name, dst_name in (
            ("drawing.dxf", "sheet2d.dxf"),
            ("drawing.svg", "sheet2d.svg"),
            ("drawing.png", "sheet2d.png"),
            ("audit.json", "sheet2d-audit.json"),
        ):
            cand = sheet_task_dir / "output" / src_name
            if cand.is_file():
                dst = out / dst_name
                dst.write_bytes(cand.read_bytes())
                artifacts.append(
                    {
                        "name": dst.name,
                        "path": str(dst.resolve()),
                        "size": dst.stat().st_size,
                        "sha256": sha256_file(dst),
                    }
                )
        sheet_2d_summary = {
            "status": res_2d.status,
            "views": spec_2d.requirements.views,
            "scale": spec_2d.sheet.scale,
            "audit_summary": audit_2d.get("summary", {}),
        }
        if res_2d.status == "failed":
            overall_status = "failed"

    return {
        "status": overall_status,
        "audit_3d": {
            "status": audit_report.status,
            "watertight": audit_report.watertight,
            "triangle_count": audit_report.triangle_count,
            "vertex_count": audit_report.vertex_count,
            "actual_volume_mm3": audit_report.actual_volume_mm3,
            "expected_volume_mm3": audit_report.expected_volume_mm3,
            "volume_rel_error": audit_report.volume_rel_error,
            "actual_spans_mm": list(audit_report.actual_spans_mm),
            "issues": [asdict(i) for i in audit_report.issues],
        },
        "sheet_2d": sheet_2d_summary,
        "capabilities": probe_3d_capabilities(),
        "artifacts": artifacts,
        "iso_png_path": iso_png_path,
    }
