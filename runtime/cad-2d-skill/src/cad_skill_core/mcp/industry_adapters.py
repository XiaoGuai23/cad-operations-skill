"""Unified adapters wrapping electrical SLD and pavement structure CAD generators."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Any

import ezdxf
from matplotlib.font_manager import findfont

from cad_skill_core.revision.common import sha256_file
from cad_skill_core.revision.preview import preview_file

from .sandbox import default_repo_root, resolve_safe_child_path, validate_safe_single_name


def _load_script_module(script_name: str, module_alias: str):
    if module_alias in sys.modules:
        return sys.modules[module_alias]
    script_path = default_repo_root() / "scripts" / script_name
    if not script_path.is_file():
        raise FileNotFoundError(f"required script not found: {script_path}")
    spec = importlib.util.spec_from_file_location(module_alias, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load module spec for {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_alias] = module
    spec.loader.exec_module(module)
    return module


def build_auto_style_font_map(dxf_path: Path) -> dict[str, str]:
    """Build a verified style->TTF/TTC path mapping for every STYLE in a DXF."""

    doc = ezdxf.readfile(dxf_path)
    win_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    cjk_candidates = [
        win_fonts / "Deng.ttf",
        win_fonts / "simhei.ttf",
        win_fonts / "msyh.ttc",
        win_fonts / "NotoSansSC-VF.ttf",
        win_fonts / "simsun.ttc",
        win_fonts / "simfang.ttf",
        win_fonts / "cad-ci-unifont-16.0.04.otf",
    ]
    default_cjk = next((p for p in cjk_candidates if p.is_file()), None)
    default_latin = Path(findfont("DejaVu Sans")).resolve()

    mapping: dict[str, str] = {}
    for style in doc.styles:
        sname = style.dxf.name.casefold()
        requested = str(style.dxf.get("font", "")).strip()
        chosen: Path | None = None
        if requested:
            req_name = Path(requested).name
            if (win_fonts / req_name).is_file():
                chosen = (win_fonts / req_name).resolve()
            elif not req_name.lower().endswith(".ttf") and (win_fonts / f"{req_name}.ttf").is_file():
                chosen = (win_fonts / f"{req_name}.ttf").resolve()
        if chosen is None:
            chosen = default_cjk if default_cjk is not None else default_latin
        mapping[sname] = str(chosen)
    return mapping


def generate_electrical_sld_bundle(
    spec: dict[str, Any],
    output_dir: Path,
    *,
    stem: str = "electrical_sld",
    expect_text: list[tuple[str, int]] | None = None,
    render_lock=None,
) -> dict[str, Any]:
    """Generate electrical SLD DXF/SVG/PDF + PNG preview + SLD audit report."""

    sld_mod = _load_script_module("make_main_sld.py", "_cad_mcp_make_main_sld")
    audit_mod = _load_script_module("audit_sld.py", "_cad_mcp_audit_sld")

    clean_stem = validate_safe_single_name(stem, label="stem")
    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    diagram = sld_mod.draw_diagram(spec)
    dxf_path = resolve_safe_child_path(out_root, f"{clean_stem}.dxf", label="stem")
    svg_path = resolve_safe_child_path(out_root, f"{clean_stem}.svg", label="stem")
    pdf_path = resolve_safe_child_path(out_root, f"{clean_stem}.pdf", label="stem")

    sld_mod.export_dxf(diagram, dxf_path)
    sld_mod.export_svg(diagram, svg_path)
    sld_mod.export_pdf(diagram, pdf_path)

    # Render PNG preview via thread-safe region renderer
    preview_dir = out_root / "preview"
    font_map = build_auto_style_font_map(dxf_path)
    if render_lock is not None:
        with render_lock:
            prev = preview_file(dxf_path, preview_dir, font_map=font_map)
    else:
        prev = preview_file(dxf_path, preview_dir, font_map=font_map)
    png_src = preview_dir / prev["images"][0]
    png_path = resolve_safe_child_path(out_root, f"{clean_stem}.png", label="stem")
    png_path.write_bytes(png_src.read_bytes())

    audit_report = audit_mod.audit(dxf_path, expect_text or [], 40)
    reopened = ezdxf.readfile(dxf_path)
    ez_audit = reopened.audit()
    failed_expectations = [k for k, v in audit_report["text_checks"].items() if not v["ok"]]
    status = "passed" if (not ez_audit.has_errors and not failed_expectations) else "failed"

    audit_json_path = output_dir / "audit.json"
    audit_payload = {
        "domain": "electrical_sld",
        "status": status,
        "ezdxf_audit_errors": len(ez_audit.errors),
        "failed_text_expectations": failed_expectations,
        "sld_audit": audit_report,
    }
    audit_json_path.write_text(json.dumps(audit_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    artifacts = [
        {
            "name": p.name,
            "path": str(p.resolve()),
            "size": p.stat().st_size,
            "sha256": sha256_file(p),
        }
        for p in (dxf_path, svg_path, pdf_path, png_path, audit_json_path)
    ]
    return {
        "status": status,
        "artifacts": artifacts,
        "preview_png_path": png_path,
        "audit_summary": {
            "errors": len(ez_audit.errors) + len(failed_expectations),
            "entity_count": audit_report["entity_count"],
            "axis_crossings_count": audit_report["axis_crossings_count"],
        },
    }


def generate_pavement_bundle(
    spec: dict[str, Any],
    output_dir: Path,
    *,
    render_lock=None,
) -> dict[str, Any]:
    """Generate audited pavement structure DXF + PNG preview + audit report."""

    pav_mod = _load_script_module("generate_pavement_cad_ezdxf.py", "_cad_mcp_pavement_ezdxf")
    output_dir.mkdir(parents=True, exist_ok=True)
    dxf_path = Path(pav_mod.make_dxf(spec, output_dir))

    preview_dir = output_dir / "preview"
    font_map = build_auto_style_font_map(dxf_path)
    if render_lock is not None:
        with render_lock:
            prev = preview_file(dxf_path, preview_dir, font_map=font_map)
    else:
        prev = preview_file(dxf_path, preview_dir, font_map=font_map)
    png_src = preview_dir / prev["images"][0]
    png_path = output_dir / f"{dxf_path.stem}.png"
    png_path.write_bytes(png_src.read_bytes())

    reopened = ezdxf.readfile(dxf_path)
    ez_audit = reopened.audit()
    type_counts: dict[str, int] = {}
    for ent in reopened.modelspace():
        t = ent.dxftype()
        type_counts[t] = type_counts.get(t, 0) + 1

    status = "passed" if not ez_audit.has_errors else "failed"
    audit_json_path = output_dir / "audit.json"
    audit_payload = {
        "domain": "pavement",
        "status": status,
        "ezdxf_audit_errors": len(ez_audit.errors),
        "entity_count": sum(type_counts.values()),
        "type_counts": type_counts,
    }
    audit_json_path.write_text(json.dumps(audit_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    artifacts = [
        {
            "name": p.name,
            "path": str(p.resolve()),
            "size": p.stat().st_size,
            "sha256": sha256_file(p),
        }
        for p in (dxf_path, png_path, audit_json_path)
    ]
    return {
        "status": status,
        "artifacts": artifacts,
        "preview_png_path": png_path,
        "audit_summary": {
            "errors": len(ez_audit.errors),
            "entity_count": sum(type_counts.values()),
            "type_counts": type_counts,
        },
    }
