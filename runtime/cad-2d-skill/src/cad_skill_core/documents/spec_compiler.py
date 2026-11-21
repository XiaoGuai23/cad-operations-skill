"""Deterministic compiler from extracted SourceFragments to validated DrawingSpec."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isclose
from pathlib import Path
import re
from typing import Any, Literal

from cad_skill_core.geometry.parametric import (
    FlangeParameters,
    RoundedRectangleParameters,
    SlottedBracketParameters,
    SteppedShaftParameters,
    build_flange_geometry,
    build_slotted_bracket_geometry,
    build_stepped_shaft_geometry,
    rounded_rectangle,
)
from cad_skill_core.schema.io import save_spec
from cad_skill_core.schema.models import DrawingSpec

from .extract import OCRCallable, extract_sources
from .models import SourceFragment

PartFamily = Literal["auto", "flange", "stepped-shaft", "slotted-bracket", "rounded-rect"]

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_NUMBER_TOL_RE = re.compile(
    r"(?P<val>\d+(?:\.\d+)?)\s*(?:mm|MM|毫米)?\s*"
    r"(?:±\s*(?P<sym>\d+(?:\.\d+)?)|"
    r"\(\s*\+?(?P<up>[+-]?\d+(?:\.\d+)?)\s*/\s*(?P<lo>[+-]?\d+(?:\.\d+)?)\s*\))?"
)
_BOLT_PATTERN_RE = re.compile(
    r"(?<![A-Za-z0-9\u00d8\u2300\u2205\u03a6\u03c6-])(?P<count>\d+)\s*[×xX*-]\s*[⌀∅Φφ]\s*(?P<dia>\d+(?:\.\d+)?)"
)
_SHAFT_STEP_RE = re.compile(
    r"(?:轴段|台阶|step)\s*\d*\s*[:：]?\s*[⌀∅ΦφDd]?\s*(?P<dia>\d+(?:\.\d+)?)\s*[×xX*]\s*[Ll]?\s*(?P<len>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

_PARAM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("outer_diameter", re.compile(r"(?:外径|outer\s*diameter|OD)\s*[:：=]?\s*[⌀∅Φφ]?\s*", re.IGNORECASE)),
    ("bore_diameter", re.compile(r"(?:内径|内孔(?:直径)?|中心孔|bore(?:\s*diameter)?|ID)\s*[:：=]?\s*[⌀∅Φφ]?\s*", re.IGNORECASE)),
    ("bolt_circle_diameter", re.compile(r"(?:节圆(?:直径)?|孔距圆|分布圆(?:直径)?|bolt\s*circle(?:\s*diameter)?|BCD|PCD)\s*[:：=]?\s*[⌀∅Φφ]?\s*", re.IGNORECASE)),
    ("hole_diameter", re.compile(r"(?:安装孔径|螺栓孔径|孔径|hole\s*diameter)\s*[:：=]?\s*[⌀∅Φφ]?\s*", re.IGNORECASE)),
    ("hole_count", re.compile(r"(?:孔数|螺栓孔数|安装孔数|hole\s*count)\s*[:：=]?\s*", re.IGNORECASE)),
    ("thickness", re.compile(r"(?:厚度|板厚|法兰厚度|thickness)\s*[:：=]?\s*", re.IGNORECASE)),
    ("width", re.compile(r"(?:宽度|总宽|板宽|width)\s*[:：=]?\s*", re.IGNORECASE)),
    ("height", re.compile(r"(?:高度|总高|板高|height)\s*[:：=]?\s*", re.IGNORECASE)),
    ("corner_radius", re.compile(r"(?:圆角(?:半径)?|倒圆|corner\s*radius|fillet\s*radius)\s*[:：=]?\s*[Rr]?\s*", re.IGNORECASE)),
    ("slot_length", re.compile(r"(?:腰形槽长|槽长|长槽长度|slot\s*length)\s*[:：=]?\s*", re.IGNORECASE)),
    ("slot_width", re.compile(r"(?:腰形槽宽|槽宽|slot\s*width)\s*[:：=]?\s*", re.IGNORECASE)),
    ("hole_inset", re.compile(r"(?:孔边距|安装孔边距|hole\s*inset)\s*[:：=]?\s*", re.IGNORECASE)),
)


@dataclass(frozen=True, slots=True)
class _SourcedParam:
    value: float
    tol_upper: float | None
    tol_lower: float | None
    source_id: str
    locator: str


def compile_spec_from_files(
    paths: Sequence[Path],
    *,
    part_family: PartFamily = "auto",
    title_block_language: Literal["auto", "en", "zh"] = "auto",
    ocr: OCRCallable | None = None,
    output_path: Path | None = None,
) -> DrawingSpec:
    """Extract fragments from source files and compile into a validated DrawingSpec."""

    fragments = extract_sources(paths, ocr=ocr)
    spec = compile_spec_from_fragments(
        fragments,
        part_family=part_family,
        title_block_language=title_block_language,
    )
    if output_path is not None:
        save_spec(spec, output_path)
    return spec


def compile_spec_from_fragments(
    fragments: Sequence[SourceFragment],
    *,
    part_family: PartFamily = "auto",
    title_block_language: Literal["auto", "en", "zh"] = "auto",
) -> DrawingSpec:
    """Analyze extracted SourceFragments, detect conflicts/missing dims, and emit DrawingSpec."""

    if not fragments:
        raise ValueError("cannot compile DrawingSpec from empty source fragments")

    provenance_map: dict[str, dict[str, Any]] = {}
    for frag in fragments:
        fid = frag.id[:16]
        if fid not in provenance_map:
            provenance_map[fid] = {
                "id": fid,
                "file": Path(frag.file).name,
                "locator": frag.locator,
                "confidence": frag.confidence,
            }

    first_source_id = fragments[0].id[:16]
    all_text = "\n".join(frag.text for frag in fragments)
    has_cjk = bool(_CJK_RE.search(all_text))
    resolved_lang: Literal["en", "zh"] = (
        ("zh" if has_cjk else "en") if title_block_language == "auto" else title_block_language
    )

    project_name: str | None = None
    drawing_number: str | None = None
    drawing_type: Literal["part", "assembly"] = "part"
    sheet_size: Literal["A0", "A1", "A2", "A3", "A4"] | None = None
    orientation: Literal["landscape", "portrait"] = "landscape"
    explicit_scale: str | None = None
    requested_views: list[str] = []

    materials: list[tuple[str, str, str]] = []
    technical_notes: list[dict[str, str]] = []
    parts_list: list[dict[str, Any]] = []
    params: dict[str, list[_SourcedParam]] = {}
    shaft_steps: list[tuple[float, float, str]] = []
    assumptions: list[str] = []
    conflicts: list[str] = []
    missing: list[str] = []

    bom_header_seen = False

    for frag in fragments:
        sid = frag.id[:16]
        line = frag.text.strip()
        if not line:
            continue

        # Heading or explicit title
        if line.startswith("#") and project_name is None:
            heading = line.lstrip("#").strip()
            if heading:
                project_name = heading
        m_title = re.search(r"^(?:项目名称|图名|零件名称|名称|title|project(?:\s*name)?)\s*[:：|]\s*(.+)$", line, re.IGNORECASE)
        if m_title and project_name is None:
            candidate_title = m_title.group(1).split("|")[0].strip()
            if candidate_title:
                project_name = candidate_title

        m_dno = re.search(r"(?:图号|drawing\s*(?:no|number))\s*[:：|]\s*([A-Za-z0-9_.-]+)", line, re.IGNORECASE)
        if m_dno and drawing_number is None:
            drawing_number = m_dno.group(1).strip()

        if re.search(r"装配图|总成|assembly", line, re.IGNORECASE):
            drawing_type = "assembly"

        m_sheet = re.search(r"\b(A[0-4])\b", line)
        if m_sheet and sheet_size is None:
            sheet_size = m_sheet.group(1)  # type: ignore[assignment]
        if re.search(r"竖排|纵向|portrait", line, re.IGNORECASE):
            orientation = "portrait"

        m_scale = re.search(r"(?:比例|scale)\s*[:：|]?\s*(\d+\s*:\s*\d+)", line, re.IGNORECASE)
        if m_scale and explicit_scale is None:
            explicit_scale = m_scale.group(1).replace(" ", "")

        # Views detection
        for token, vname in (
            ("主视图", "front"),
            ("front", "front"),
            ("俯视图", "top"),
            ("top", "top"),
            ("右视图", "right"),
            ("right", "right"),
            ("左视图", "left"),
            ("left", "left"),
            ("剖视图", "section-A"),
            ("section", "section-A"),
        ):
            if re.search(rf"(?:视图|views?).*{re.escape(token)}", line, re.IGNORECASE):
                if vname not in requested_views:
                    requested_views.append(vname)

        # Material extraction
        m_mat = re.search(r"^(?:材料|材质|material)\s*[:：|]\s*(.+)$", line, re.IGNORECASE)
        if m_mat:
            mat_val = m_mat.group(1).split("|")[0].strip().rstrip("。；;")
            if mat_val:
                materials.append((mat_val, sid, frag.locator))

        # Technical notes extraction
        if re.search(r"^(?:技术要求|注\s*[:：]|note\s*[:：]|\d+[.、]\s*(?:未注|去毛刺|热处理|表面|公差|调质|倒角))", line, re.IGNORECASE):
            cleaned_note = re.sub(r"^(?:技术要求\s*[:：]?\s*)", "", line).strip()
            if cleaned_note and not any(n["text"] == cleaned_note for n in technical_notes):
                technical_notes.append({"text": cleaned_note, "source_id": sid})

        # Table row parsing ("col1 | col2 | ...")
        if "|" in line:
            cols = [c.strip() for c in line.split("|") if c.strip()]
            joined_lower = " ".join(cols).lower()
            if any(h in joined_lower for h in ("序号", "零件名称", "数量", "qty", "part name")) and len(cols) >= 2:
                bom_header_seen = True
                continue
            if bom_header_seen and len(cols) >= 2:
                # Try parsing BOM row: [item_no?, name, qty]
                qty_match = re.search(r"^(\d+)\s*(?:件|个|套|pcs)?$", cols[-1], re.IGNORECASE)
                if qty_match:
                    item_name = cols[1] if len(cols) >= 3 and cols[0].isdigit() else cols[0]
                    if item_name and not any(k in item_name for k in ("参数", "外径", "内径", "宽度", "高度", "厚度")):
                        parts_list.append(
                            {"item": item_name, "quantity": int(qty_match.group(1)), "source_id": sid}
                        )
                        drawing_type = "assembly"
                        continue
            if len(cols) >= 2:
                key_col, val_col = cols[0], " ".join(cols[1:])
                _extract_parameters_from_pair(key_col, val_col, sid, frag.locator, params)

        # Inline parameter & bolt pattern extraction
        for m_bolt in _BOLT_PATTERN_RE.finditer(line):
            cnt = float(m_bolt.group("count"))
            hdia = float(m_bolt.group("dia"))
            _record_param(params, "hole_count", cnt, None, None, sid, frag.locator)
            _record_param(params, "hole_diameter", hdia, None, None, sid, frag.locator)

        for m_step in _SHAFT_STEP_RE.finditer(line):
            dia = float(m_step.group("dia"))
            length = float(m_step.group("len"))
            shaft_steps.append((length, dia, sid))

        for pname, pattern in _PARAM_PATTERNS:
            for match in pattern.finditer(line):
                tail = line[match.end() :]
                num_m = _NUMBER_TOL_RE.match(tail)
                if num_m:
                    val = float(num_m.group("val"))
                    tol_u, tol_l = _parse_tolerances(num_m)
                    _record_param(params, pname, val, tol_u, tol_l, sid, frag.locator)

    # Detect conflicts across materials & numeric parameters
    selected_material: dict[str, str] | None = None
    if materials:
        first_mat, first_sid, first_loc = materials[0]
        selected_material = {"text": first_mat, "source_id": first_sid}
        for other_mat, _, other_loc in materials[1:]:
            if other_mat.casefold() != first_mat.casefold():
                conflicts.append(
                    f"material conflict: '{first_mat}' ({first_loc}) vs '{other_mat}' ({other_loc})"
                )
        # Ensure material is echoed in technical_notes with matching source_id for semantic audit
        if not any(
            n["source_id"] == first_sid and first_mat.casefold() in n["text"].casefold()
            for n in technical_notes
        ):
            mat_prefix = "材料：" if resolved_lang == "zh" else "Material: "
            technical_notes.append({"text": f"{mat_prefix}{first_mat}", "source_id": first_sid})

    resolved_params: dict[str, _SourcedParam] = {}
    for pname, entries in sorted(params.items()):
        primary = entries[0]
        resolved_params[pname] = primary
        for extra in entries[1:]:
            if not isclose(primary.value, extra.value, rel_tol=1e-6, abs_tol=1e-6):
                conflicts.append(
                    f"parameter '{pname}' conflict: {primary.value:g} ({primary.locator}) vs {extra.value:g} ({extra.locator})"
                )

    # Infer part family if "auto"
    inferred_family = _infer_family(part_family, all_text, resolved_params, shaft_steps)

    if sheet_size is None:
        sheet_size = "A3"
        assumptions.append("sheet size defaulted to A3")

    entities_dump: list[dict[str, Any]] = []
    view_indices: dict[str, list[int]] = {}
    dimensions_dump: list[dict[str, Any]] = []
    geo_params: dict[str, Any] = {k: v.value for k, v in resolved_params.items()}

    # Build geometry & validate completeness/feasibility for the chosen family
    if inferred_family == "flange":
        if "outer_diameter" not in resolved_params:
            missing.append("missing required parameter for flange: outer_diameter")
        else:
            od = resolved_params["outer_diameter"]
            bore = resolved_params["bore_diameter"].value if "bore_diameter" in resolved_params else 0.0
            th = resolved_params["thickness"].value if "thickness" in resolved_params else 20.0
            if "thickness" not in resolved_params:
                assumptions.append("flange thickness defaulted to 20 mm")
            hc = int(round(resolved_params["hole_count"].value)) if "hole_count" in resolved_params else 0
            bcd = resolved_params["bolt_circle_diameter"].value if "bolt_circle_diameter" in resolved_params else None
            hd = resolved_params["hole_diameter"].value if "hole_diameter" in resolved_params else None
            if hc > 0 and bcd is None:
                missing.append("missing required parameter for flange bolt pattern: bolt_circle_diameter")
            elif hc > 0 and hd is None:
                missing.append("missing required parameter for flange bolt pattern: hole_diameter")
            else:
                try:
                    built = build_flange_geometry(
                        FlangeParameters(
                            outer_diameter=od.value,
                            bore_diameter=bore,
                            thickness=th,
                            bolt_circle_diameter=bcd,
                            hole_diameter=hd,
                            hole_count=hc,
                        ),
                        source_id=od.source_id,
                        include_right_section=("right" in requested_views or not requested_views),
                    )
                    requested_views = built.views
                    entities_dump = [e.model_dump(mode="json") for e in built.entities]
                    view_indices = built.view_entity_indices
                    dimensions_dump = _attach_tolerances(built.dimensions, resolved_params)
                    geo_params.update(built.parameters)
                except ValueError as exc:
                    conflicts.append(f"flange geometric constraint violation: {exc}")

    elif inferred_family == "stepped-shaft":
        if not shaft_steps:
            missing.append("missing required shaft steps (e.g. '轴段1: Φ30×50')")
        else:
            try:
                built = build_stepped_shaft_geometry(
                    SteppedShaftParameters(steps=[(l, d) for l, d, _ in shaft_steps]),
                    source_id=shaft_steps[0][2],
                    include_right_view=("right" in requested_views or not requested_views),
                )
                requested_views = built.views
                entities_dump = [e.model_dump(mode="json") for e in built.entities]
                view_indices = built.view_entity_indices
                dimensions_dump = built.dimensions
                geo_params.update(built.parameters)
            except ValueError as exc:
                conflicts.append(f"stepped-shaft geometric constraint violation: {exc}")

    elif inferred_family == "slotted-bracket":
        req_keys = ("width", "height", "slot_length", "slot_width")
        absent = [k for k in req_keys if k not in resolved_params]
        if absent:
            for k in absent:
                missing.append(f"missing required parameter for slotted-bracket: {k}")
        else:
            w_p = resolved_params["width"]
            th = resolved_params["thickness"].value if "thickness" in resolved_params else 12.0
            cr = resolved_params["corner_radius"].value if "corner_radius" in resolved_params else min(8.0, min(w_p.value, resolved_params["height"].value) * 0.15)
            hd = resolved_params["hole_diameter"].value if "hole_diameter" in resolved_params else None
            hi = resolved_params["hole_inset"].value if "hole_inset" in resolved_params else 16.0
            try:
                built = build_slotted_bracket_geometry(
                    SlottedBracketParameters(
                        width=w_p.value,
                        height=resolved_params["height"].value,
                        thickness=th,
                        corner_radius=cr,
                        slot_length=resolved_params["slot_length"].value,
                        slot_width=resolved_params["slot_width"].value,
                        hole_diameter=hd,
                        hole_inset=hi,
                    ),
                    source_id=w_p.source_id,
                    include_top_view=("top" in requested_views or not requested_views),
                )
                requested_views = built.views
                entities_dump = [e.model_dump(mode="json") for e in built.entities]
                view_indices = built.view_entity_indices
                dimensions_dump = _attach_tolerances(built.dimensions, resolved_params)
                geo_params.update(built.parameters)
            except ValueError as exc:
                conflicts.append(f"slotted-bracket geometric constraint violation: {exc}")

    elif inferred_family == "rounded-rect":
        req_keys = ("width", "height", "corner_radius")
        absent = [k for k in req_keys if k not in resolved_params]
        if absent:
            for k in absent:
                missing.append(f"missing required parameter for rounded-rect: {k}")
        else:
            w_p = resolved_params["width"]
            h_p = resolved_params["height"]
            r_p = resolved_params["corner_radius"]
            try:
                ent = rounded_rectangle(
                    RoundedRectangleParameters(
                        width=w_p.value,
                        height=h_p.value,
                        radius=r_p.value,
                        origin=(-w_p.value / 2.0, -h_p.value / 2.0),
                    ),
                    layer="OUTLINE",
                    source_id=w_p.source_id,
                )
                requested_views = ["front"]
                entities_dump = [ent.model_dump(mode="json")]
                view_indices = {"front": [0]}
                dimensions_dump = [
                    {
                        "kind": "linear",
                        "value": w_p.value,
                        "source_id": w_p.source_id,
                        "owner_view": "front",
                        "target_entity_index": 0,
                        "orientation": "horizontal",
                        "tolerance_upper": w_p.tol_upper,
                        "tolerance_lower": w_p.tol_lower,
                    },
                    {
                        "kind": "linear",
                        "value": h_p.value,
                        "source_id": h_p.source_id,
                        "owner_view": "front",
                        "target_entity_index": 0,
                        "orientation": "vertical",
                        "tolerance_upper": h_p.tol_upper,
                        "tolerance_lower": h_p.tol_lower,
                    },
                ]
            except ValueError as exc:
                conflicts.append(f"rounded-rect geometric constraint violation: {exc}")

    if not requested_views:
        requested_views = ["front"]

    scale_was_auto = explicit_scale is None
    if explicit_scale is None:
        explicit_scale = "1:1"

    spec_dict: dict[str, Any] = {
        "schema_version": "1.0",
        "project": {
            "name": project_name or ("工程零件图" if resolved_lang == "zh" else "Compiled Part"),
            "drawing_number": drawing_number,
            "drawing_type": drawing_type,
        },
        "standard": {
            "family": "GB/T",
            "units": "mm",
            "projection": "first-angle",
        },
        "sheet": {
            "size": sheet_size,
            "orientation": orientation,
            "scale": explicit_scale,
            "title_block_language": resolved_lang,
        },
        "requirements": {
            "material": selected_material,
            "views": requested_views,
            "technical_notes": technical_notes,
        },
        "geometry": {
            "parameters": geo_params,
            "entities": entities_dump,
            "view_entity_indices": view_indices,
        },
        "dimensions": dimensions_dump,
        "annotations": [],
        "parts_list": parts_list if drawing_type == "assembly" else [],
        "provenance": list(provenance_map.values()) or [
            {"id": first_source_id, "file": "input", "locator": "line 1", "confidence": 1.0}
        ],
        "validation": {
            "missing": missing,
            "conflicts": conflicts,
            "assumptions": assumptions,
        },
    }
    validated_spec = DrawingSpec.model_validate(spec_dict)
    if scale_was_auto:
        from cad_skill_core.layout.models import LayoutOverflowError
        from cad_skill_core.layout.plan import plan_layout
        from cad_skill_core.standards.profile import load_standard

        std_path = Path(__file__).resolve().parents[3] / "knowledge" / "standards" / "gb-mechanical.yaml"
        try:
            plan_layout(validated_spec, load_standard(std_path))
        except LayoutOverflowError as err:
            if ":" in err.suggested_scale:
                validated_spec.sheet.scale = err.suggested_scale
        validated_spec.validation.assumptions.append(
            f"drawing scale auto-selected as {validated_spec.sheet.scale}"
        )
    return validated_spec


def _parse_tolerances(match: re.Match[str]) -> tuple[float | None, float | None]:
    sym = match.group("sym")
    if sym is not None:
        v = float(sym)
        return v, -v
    up = match.group("up")
    lo = match.group("lo")
    if up is not None and lo is not None:
        return float(up), float(lo)
    return None, None


def _record_param(
    params: dict[str, list[_SourcedParam]],
    name: str,
    value: float,
    tol_u: float | None,
    tol_l: float | None,
    source_id: str,
    locator: str,
) -> None:
    entries = params.setdefault(name, [])
    if any(
        e.source_id == source_id and isclose(e.value, value, rel_tol=1e-9, abs_tol=1e-9)
        for e in entries
    ):
        return
    entries.append(_SourcedParam(value, tol_u, tol_l, source_id, locator))


def _extract_parameters_from_pair(
    key_text: str,
    val_text: str,
    source_id: str,
    locator: str,
    params: dict[str, list[_SourcedParam]],
) -> None:
    probe = f"{key_text}: {val_text}"
    for pname, pattern in _PARAM_PATTERNS:
        m = pattern.search(probe)
        if m:
            num_m = _NUMBER_TOL_RE.search(val_text)
            if num_m:
                val = float(num_m.group("val"))
                tol_u, tol_l = _parse_tolerances(num_m)
                _record_param(params, pname, val, tol_u, tol_l, source_id, locator)
                return


def _infer_family(
    requested: PartFamily,
    all_text: str,
    params: dict[str, _SourcedParam],
    shaft_steps: list[tuple[float, float, str]],
) -> str:
    if requested != "auto":
        return requested
    if shaft_steps or re.search(r"阶梯轴|传动轴|stepped\s*shaft", all_text, re.IGNORECASE):
        return "stepped-shaft"
    if "slot_length" in params or "slot_width" in params or re.search(r"腰形槽|长槽支架|slotted\s*bracket", all_text, re.IGNORECASE):
        return "slotted-bracket"
    if "outer_diameter" in params or re.search(r"法兰|flange", all_text, re.IGNORECASE):
        return "flange"
    if "width" in params and "height" in params and "corner_radius" in params:
        return "rounded-rect"
    return "generic"


def _attach_tolerances(
    dimensions: list[dict[str, Any]],
    params: dict[str, _SourcedParam],
) -> list[dict[str, Any]]:
    key_by_value: list[_SourcedParam] = list(params.values())
    enriched: list[dict[str, Any]] = []
    for dim in dimensions:
        copy_dim = dict(dim)
        matched = next(
            (
                p
                for p in key_by_value
                if isclose(p.value, float(copy_dim["value"]), rel_tol=1e-6, abs_tol=1e-6)
                and (p.tol_upper is not None or p.tol_lower is not None)
            ),
            None,
        )
        if matched is not None:
            copy_dim["source_id"] = matched.source_id
            copy_dim["tolerance_upper"] = matched.tol_upper
            copy_dim["tolerance_lower"] = matched.tol_lower
        enriched.append(copy_dim)
    return enriched
