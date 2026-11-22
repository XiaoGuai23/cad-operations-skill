"""Conversational natural-language & document compiler for 3D Model3DSpec."""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
from pathlib import Path
import re
from typing import Any, Literal

from cad_skill_core.documents.extract import extract_sources
from cad_skill_core.documents.models import SourceFragment
from cad_skill_core.documents.spec_compiler import (
    _BOLT_PATTERN_RE,
    _CONV_HOLES_RE,
    _CONV_SHAFT_STEP_RE,
    _CONV_SLOT_PAIR_RE,
    _INLINE_MATERIAL_RE,
    _NUMBER_TOL_RE,
    _PARAM_PATTERNS,
    _REV_BCD_RE,
    _SHAFT_STEP_RE,
)

from .models import Model3DSpec

Part3DFamily = Literal[
    "auto",
    "flange_3d",
    "stepped_shaft_3d",
    "slotted_bracket_3d",
    "cylinder",
    "box",
]


def compile_model3d_from_text(
    text: str,
    *,
    part_family: Part3DFamily = "auto",
) -> Model3DSpec:
    """Compile a conversational Chinese/English brief or Markdown text into a Model3DSpec."""

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("cannot compile Model3DSpec from empty text")
    fragments = [
        SourceFragment(
            id=hashlib.sha256(f"inline:{idx}:{ln}".encode("utf-8")).hexdigest()[:16],
            file="inline-brief.md",
            media_type="text/markdown",
            locator=f"line {idx}",
            text=ln,
            confidence=1.0,
        )
        for idx, ln in enumerate(lines, start=1)
    ]
    return compile_model3d_from_fragments(fragments, part_family=part_family)


def compile_model3d_from_files(
    paths: Sequence[Path],
    *,
    part_family: Part3DFamily = "auto",
) -> Model3DSpec:
    """Extract fragments from .md/.txt/.csv/.docx/.pdf files and compile into a Model3DSpec."""

    fragments = extract_sources(paths)
    return compile_model3d_from_fragments(fragments, part_family=part_family)


def compile_model3d_from_fragments(
    fragments: Sequence[SourceFragment],
    *,
    part_family: Part3DFamily = "auto",
) -> Model3DSpec:
    if not fragments:
        raise ValueError("cannot compile Model3DSpec from empty fragments")

    provenance: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for frag in fragments:
        fid = frag.id[:16]
        if fid not in seen_ids:
            seen_ids.add(fid)
            provenance.append(
                {
                    "id": fid,
                    "file": Path(frag.file).name,
                    "locator": frag.locator,
                    "confidence": frag.confidence,
                }
            )

    primary_sid = provenance[0]["id"]
    all_text = "\n".join(f.text for f in fragments)

    project_name: str | None = None
    drawing_no: str | None = None
    material_text: str | None = None
    material_sid: str = primary_sid
    notes: list[dict[str, str]] = []
    params: dict[str, float] = {}
    shaft_steps: list[tuple[float, float]] = []
    missing: list[str] = []
    conflicts: list[str] = []
    assumptions: list[str] = []

    for frag in fragments:
        sid = frag.id[:16]
        line = frag.text.strip()
        if not line:
            continue

        if line.startswith("#") and project_name is None:
            project_name = line.lstrip("#").strip() or None
        m_title = re.search(r"^(?:项目名称|图名|零件名称|名称|title)\s*[:：|]\s*(.+)$", line, re.IGNORECASE)
        if m_title and project_name is None:
            project_name = m_title.group(1).split("|")[0].strip() or None

        m_dno = re.search(r"(?:图号|drawing\s*(?:no|number))\s*[:：|]\s*([A-Za-z0-9_.-]+)", line, re.IGNORECASE)
        if m_dno and drawing_no is None:
            drawing_no = m_dno.group(1).strip()

        m_mat = re.search(r"^(?:材料|材质|material)\s*[:：|]\s*(.+)$", line, re.IGNORECASE)
        if m_mat and material_text is None:
            material_text = m_mat.group(1).split("|")[0].strip().rstrip("。；;")
            material_sid = sid
        elif material_text is None:
            m_inl = _INLINE_MATERIAL_RE.search(line)
            if m_inl:
                material_text = m_inl.group(1)
                material_sid = sid

        if re.search(r"^(?:技术要求|注\s*[:：]|note\s*[:：])", line, re.IGNORECASE):
            cleaned = re.sub(r"^(?:技术要求\s*[:：]?\s*)", "", line).strip()
            if cleaned:
                notes.append({"text": cleaned, "source_id": sid})

        for m_b in _BOLT_PATTERN_RE.finditer(line):
            params.setdefault("hole_count", float(m_b.group("count")))
            params.setdefault("hole_diameter", float(m_b.group("dia")))

        for m_ch in _CONV_HOLES_RE.finditer(line):
            params.setdefault("hole_count", float(m_ch.group("count")))
            params.setdefault("hole_diameter", float(m_ch.group("dia")))

        for m_rbcd in _REV_BCD_RE.finditer(line):
            params.setdefault("bolt_circle_diameter", float(m_rbcd.group("val")))

        for m_slp in _CONV_SLOT_PAIR_RE.finditer(line):
            params.setdefault("slot_length", float(m_slp.group("slen")))
            params.setdefault("slot_width", float(m_slp.group("swid")))

        step_spans: list[tuple[int, int]] = []
        for m_s in _SHAFT_STEP_RE.finditer(line):
            shaft_steps.append((float(m_s.group("len")), float(m_s.group("dia"))))
            step_spans.append(m_s.span())
        for m_cs in _CONV_SHAFT_STEP_RE.finditer(line):
            sp = m_cs.span()
            if not any(not (sp[1] <= a or sp[0] >= b) for a, b in step_spans):
                shaft_steps.append((float(m_cs.group("len")), float(m_cs.group("dia"))))
                step_spans.append(sp)

        for pname, pat in _PARAM_PATTERNS:
            for match in pat.finditer(line):
                if step_spans and pname in {"width", "height"}:
                    continue
                nm = _NUMBER_TOL_RE.match(line[match.end() :])
                if nm:
                    val = float(nm.group("val"))
                    if pname in params and abs(params[pname] - val) > 1e-6:
                        conflicts.append(f"3D parameter '{pname}' conflict: {params[pname]:g} vs {val:g}")
                    else:
                        params[pname] = val

    # Infer 3D body kind
    fam = part_family
    if fam == "auto":
        if shaft_steps or re.search(r"阶梯轴|传动轴|stepped\s*shaft", all_text, re.IGNORECASE):
            fam = "stepped_shaft_3d"
        elif any(k in params for k in ("slot_length", "slot_width")) or re.search(r"腰形槽|支架", all_text):
            fam = "slotted_bracket_3d"
        elif (
            any(k in params for k in ("bolt_circle_diameter", "hub_diameter", "hub_height"))
            or re.search(r"法兰|flange", all_text, re.IGNORECASE)
        ):
            fam = "flange_3d"
        elif "outer_diameter" in params:
            fam = "cylinder"
        else:
            fam = "box"

    default_titles = {
        "flange_3d": "三维连接法兰",
        "stepped_shaft_3d": "三维阶梯传动轴",
        "slotted_bracket_3d": "三维腰形槽安装板",
        "cylinder": "三维轴套零件",
        "box": "三维矩形基座",
    }
    if project_name is None:
        project_name = default_titles[fam]
    if drawing_no is None:
        drawing_no = "CAD3D-AUTO-01"

    body_dict: dict[str, Any]
    if fam == "flange_3d":
        if "outer_diameter" not in params:
            missing.append("missing outer_diameter for flange_3d")
        od = params.get("outer_diameter", 140.0)
        bore = params.get("bore_diameter", 0.0)
        th = params.get("thickness", 20.0)
        if "thickness" not in params:
            assumptions.append("flange thickness defaulted to 20 mm")
        hc = int(round(params.get("hole_count", 0.0)))
        bcd = params.get("bolt_circle_diameter")
        hd = params.get("hole_diameter")
        if hc > 0 and (bcd is None or hd is None):
            missing.append("missing bolt_circle_diameter or hole_diameter for flange bolt holes")
            hc = 0
        hub_h = params.get("hub_height", 0.0)
        hub_d = params.get("hub_diameter")
        if hub_h > 0 and hub_d is None:
            missing.append("missing hub_diameter when hub_height > 0")
            hub_h = 0.0
        body_dict = {
            "kind": "flange_3d",
            "outer_diameter": od,
            "bore_diameter": bore,
            "thickness": th,
            "hub_diameter": hub_d,
            "hub_height": hub_h,
            "bolt_circle_diameter": bcd,
            "hole_diameter": hd,
            "hole_count": hc,
            "source_id": primary_sid,
        }
    elif fam == "stepped_shaft_3d":
        if not shaft_steps:
            missing.append("missing shaft steps (e.g. 第一段直径25长30，第二段40长50)")
            shaft_steps = [(30.0, 24.0), (45.0, 38.0)]
        body_dict = {
            "kind": "stepped_shaft_3d",
            "steps": [list(s) for s in shaft_steps],
            "bore_diameter": params.get("bore_diameter", 0.0),
            "source_id": primary_sid,
        }
    elif fam == "slotted_bracket_3d":
        for req_k in ("width", "height", "slot_length", "slot_width"):
            if req_k not in params:
                missing.append(f"missing {req_k} for slotted_bracket_3d")
        hc_val = int(round(params.get("hole_count", 0.0)))
        hd_val = params.get("hole_diameter") if ("hole_diameter" in params and hc_val in {0, 4}) else None
        body_dict = {
            "kind": "slotted_bracket_3d",
            "width": params.get("width", 140.0),
            "height": params.get("height", 80.0),
            "thickness": params.get("thickness", 12.0),
            "corner_radius": params.get("corner_radius", 8.0),
            "slot_length": params.get("slot_length", 44.0),
            "slot_width": params.get("slot_width", 14.0),
            "hole_diameter": hd_val,
            "hole_inset": params.get("hole_inset", 15.0),
            "source_id": primary_sid,
        }
    elif fam == "cylinder":
        if "outer_diameter" not in params:
            missing.append("missing outer_diameter for cylinder")
        h_cyl = params.get("height") or params.get("thickness") or 40.0
        body_dict = {
            "kind": "cylinder",
            "outer_diameter": params.get("outer_diameter", 60.0),
            "bore_diameter": params.get("bore_diameter", 0.0),
            "height": h_cyl,
            "source_id": primary_sid,
        }
    else:
        for req_k in ("width",):
            if req_k not in params:
                missing.append(f"missing {req_k} for box")
        body_dict = {
            "kind": "box",
            "width": params.get("width", 100.0),
            "depth": params.get("depth") or params.get("height") or 60.0,
            "height": params.get("thickness") or params.get("height") or 20.0,
            "through_holes": [],
            "source_id": primary_sid,
        }

    try:
        return Model3DSpec.model_validate(
            {
                "schema_version": "1.0",
                "project": {"name": project_name, "drawing_number": drawing_no},
                "material": ({"text": material_text, "source_id": material_sid} if material_text else None),
                "technical_notes": notes,
                "body": body_dict,
                "provenance": provenance,
                "validation": {
                    "missing": missing,
                    "conflicts": conflicts,
                    "assumptions": assumptions,
                },
            }
        )
    except ValueError as exc:
        conflicts.append(f"3D geometric constraint violation: {exc}")
        return Model3DSpec.model_validate(
            {
                "schema_version": "1.0",
                "project": {"name": project_name, "drawing_number": drawing_no},
                "material": ({"text": material_text, "source_id": material_sid} if material_text else None),
                "technical_notes": notes,
                "body": {
                    "kind": "box",
                    "width": 100.0,
                    "depth": 60.0,
                    "height": 20.0,
                    "through_holes": [],
                    "source_id": primary_sid,
                },
                "provenance": provenance,
                "validation": {
                    "missing": missing,
                    "conflicts": conflicts,
                    "assumptions": assumptions,
                },
            }
        )
