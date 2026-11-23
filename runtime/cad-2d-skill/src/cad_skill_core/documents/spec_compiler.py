"""Deterministic compiler from extracted SourceFragments to validated DrawingSpec."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isclose
from pathlib import Path
import re
from typing import Any, Literal

from cad_skill_core.geometry.parametric import (
    BearingCapParameters,
    FlangeParameters,
    RibbedBracketParameters,
    RoundedRectangleParameters,
    SlottedBracketParameters,
    SpurGearParameters,
    SteppedShaftParameters,
    build_bearing_cap_geometry,
    build_flange_geometry,
    build_ribbed_bracket_geometry,
    build_slotted_bracket_geometry,
    build_spur_gear_geometry,
    build_stepped_shaft_geometry,
    rounded_rectangle,
)
from cad_skill_core.schema.io import save_spec
from cad_skill_core.schema.models import DrawingSpec

from .extract import OCRCallable, extract_sources
from .models import SourceFragment

PartFamily = Literal[
    "auto",
    "flange",
    "stepped-shaft",
    "slotted-bracket",
    "rounded-rect",
    "spur-gear",
    "bearing-cap",
    "ribbed-bracket",
]

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_DIA_CHARS = "\u2300\u2205\u00d8\u00f8\u03a6\u03c6\u0424\u0444"  # ⌀ ∅ Ø ø Φ φ Ф ф
_DIA_SYM = f"[{_DIA_CHARS}]"
_NUMBER_TOL_RE = re.compile(
    r"(?P<val>\d+(?:\.\d+)?)\s*(?:mm|MM|毫米)?\s*"
    r"(?:±\s*(?P<sym>\d+(?:\.\d+)?)|"
    r"\(\s*\+?(?P<up>[+-]?\d+(?:\.\d+)?)\s*/\s*(?P<lo>[+-]?\d+(?:\.\d+)?)\s*\))?"
)
_BOLT_PATTERN_RE = re.compile(
    rf"(?<![A-Za-z0-9{_DIA_CHARS}-])(?P<count>\d+)\s*[×xX*-]\s*{_DIA_SYM}\s*(?P<dia>\d+(?:\.\d+)?)"
)
_HOLE_KV_PATTERN_RE = re.compile(
    rf"(?:螺栓孔|安装孔|均布孔|mounting\s*holes?|bolt\s*holes?)\s*[:：=|]\s*(?P<count>\d+)\s*[×xX*-]\s*(?:{_DIA_SYM}|[MmDd])?\s*(?P<dia>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_CONV_HOLES_RE = re.compile(
    rf"(?:均布|打|开|带|分布|钻)?\s*(?P<count>\d+)\s*个\s*(?:直径\s*|(?:{_DIA_SYM}|[Dd])\s*)?(?P<dia>\d+(?:\.\d+)?)\s*(?:mm|毫米)?\s*的?\s*(?:螺栓|安装|通)?孔"
)
_REV_BCD_RE = re.compile(
    rf"(?:在\s*)?{_DIA_SYM}?\s*(?P<val>\d+(?:\.\d+)?)\s*(?:mm|毫米)?\s*的?\s*(?:节圆|孔距圆|分布圆)"
)
_SHAFT_STEP_RE = re.compile(
    rf"(?:轴段|台阶|阶梯段|step)\s*\d*\s*[:：]?\s*(?:{_DIA_SYM}|[Dd])?\s*(?P<dia>\d+(?:\.\d+)?)\s*[×xX*]\s*[Ll]?\s*(?P<len>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_CONV_SHAFT_STEP_RE = re.compile(
    rf"(?:第[一二三四五六1-9]段|(?:轴|阶梯)段\s*[1-9])\s*[:：]?\s*(?:外径|直径|{_DIA_SYM}|[Dd])?\s*(?P<dia>\d+(?:\.\d+)?)\s*(?:mm|毫米)?\s*[,，\s]*?(?:长(?:度)?|[Ll])\s*[:：]?\s*(?P<len>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_CONV_SLOT_PAIR_RE = re.compile(
    r"(?P<slen>\d+(?:\.\d+)?)\s*[×xX*]\s*(?P<swid>\d+(?:\.\d+)?)\s*(?:mm|毫米)?\s*的?\s*(?:腰形槽|长圆槽|长槽)"
)
_MOUNTING_HOLE_MENTION_RE = re.compile(
    r"(?:螺栓孔|安装孔|均布[^\n]{0,12}孔|节圆|孔距圆|分布圆|bolt\s*holes?|mounting\s*holes?|bolt\s*circle|\bBCD\b|\bPCD\b)",
    re.IGNORECASE,
)
_INLINE_MATERIAL_RE = re.compile(
    r"(?<![A-Za-z0-9])(304不锈钢|316L?不锈钢|45#?钢|40Cr|20CrMnTi|Q235[AB]?|Q345[AB]?|6061(?:-T6)?铝合金|HT200|HT250|QT450|黄铜)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

_PARAM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("spigot_diameter", re.compile(rf"(?:止口(?:外径|直径)?|嵌入式外径|spigot(?:\s*diameter)?)\s*(?:为|是)?\s*[:：=]?\s*{_DIA_SYM}?\s*", re.IGNORECASE)),
    ("boss_hole_diameter", re.compile(rf"(?:轴承孔(?:径)?|立板孔(?:径)?|boss\s*hole)\s*(?:为|是)?\s*[:：=]?\s*{_DIA_SYM}?\s*", re.IGNORECASE)),
    ("outer_diameter", re.compile(rf"(?<!止口)(?<!凸台)(?<!法兰颈)(?:外径|outer\s*diameter|OD)\s*(?:为|是)?\s*[:：=]?\s*{_DIA_SYM}?\s*", re.IGNORECASE)),
    ("bore_diameter", re.compile(rf"(?:内径|内孔(?:直径)?|中心(?:通)?孔|轴孔(?:径)?|bore(?:\s*diameter)?|ID)\s*(?:为|是)?\s*[:：=]?\s*{_DIA_SYM}?\s*", re.IGNORECASE)),
    ("bolt_circle_diameter", re.compile(rf"(?:节圆(?:直径)?|孔距圆|分布圆(?:直径)?|bolt\s*circle(?:\s*diameter)?|BCD|PCD)\s*(?:为|是)?\s*[:：=]?\s*{_DIA_SYM}?\s*", re.IGNORECASE)),
    ("hole_diameter", re.compile(rf"(?:安装孔径|螺栓孔径|(?<!轴)(?<!内)(?<!中心)(?<!轴承)(?<!立板)孔径|hole\s*diameter)\s*(?:为|是)?\s*[:：=]?\s*{_DIA_SYM}?\s*", re.IGNORECASE)),
    ("hole_count", re.compile(r"(?:孔数|螺栓孔数|安装孔数|hole\s*count)\s*(?:为|是)?\s*[:：=]?\s*", re.IGNORECASE)),
    ("hub_diameter", re.compile(rf"(?:颈径|法兰颈(?:外)?径|凸台(?:外)?径|hub\s*diameter)\s*(?:为|是)?\s*[:：=]?\s*{_DIA_SYM}?\s*", re.IGNORECASE)),
    ("hub_height", re.compile(r"(?:颈高|法兰颈高|凸台高(?:度)?|hub\s*height)\s*(?:为|是)?\s*[:：=]?\s*", re.IGNORECASE)),
    ("module_mm", re.compile(r"(?:法向模数|模数|module)\s*(?:[mM]\s*)?(?:为|是)?\s*[:：=]?\s*", re.IGNORECASE)),
    ("teeth", re.compile(r"(?:齿数|teeth)\s*(?:[zZ]\s*)?(?:为|是)?\s*[:：=]?\s*", re.IGNORECASE)),
    ("face_width", re.compile(r"(?:齿宽|齿轮宽(?:度)?|face\s*width)\s*(?:[bB]\s*)?(?:为|是)?\s*[:：=]?\s*", re.IGNORECASE)),
    ("rib_thickness", re.compile(r"(?:肋板厚(?:度)?|加强筋厚(?:度)?|筋厚|rib\s*thickness)\s*(?:为|是)?\s*[:：=]?\s*", re.IGNORECASE)),
    ("wall_thickness", re.compile(r"(?:立板厚(?:度)?|竖板厚|壁厚|wall\s*thickness)\s*(?:为|是)?\s*[:：=]?\s*", re.IGNORECASE)),
    ("slot_length", re.compile(r"(?:腰形槽长|槽长|长槽长度|slot\s*length)\s*(?:为|是)?\s*[:：=]?\s*", re.IGNORECASE)),
    ("slot_width", re.compile(r"(?:腰形槽宽|槽宽|slot\s*width)\s*(?:为|是)?\s*[:：=]?\s*", re.IGNORECASE)),
    ("hole_inset", re.compile(r"(?:安装孔边距|孔边距|距边|边距|hole\s*inset)\s*(?:为|是)?\s*[:：=]?\s*", re.IGNORECASE)),
    ("depth", re.compile(r"(?:底板宽(?:度)?|进深|深度|深|depth)\s*(?:为|是)?\s*[:：=]?\s*", re.IGNORECASE)),
    ("corner_radius", re.compile(r"(?:(?:四角)?倒圆|圆角(?:半径)?|corner\s*radius|fillet\s*radius)\s*(?:为|是)?\s*[:：=]?\s*[Rr]?\s*", re.IGNORECASE)),
    ("thickness", re.compile(r"(?<!筋)(?<!肋板)(?<!立板)(?<!竖板)(?<!壁)(?:底盘厚(?:度)?|法兰厚(?:度)?|底板厚(?:度)?|板厚|厚度|厚|thickness)\s*(?:为|是)?\s*[:：=]?\s*", re.IGNORECASE)),
    ("width", re.compile(r"(?<!底)(?<!齿)(?<!齿轮)(?<!槽)(?:底板长(?:度)?|总长|总宽|(?<!底)板宽|(?<!底板)(?<!齿轮)宽度|(?<!底板)(?<!齿)(?<!槽)宽|(?<!槽)长|width)\s*(?:为|是)?\s*[:：=]?\s*", re.IGNORECASE)),
    ("height", re.compile(r"(?<!颈)(?<!凸台)(?:总高|板高|高度|高|height)\s*(?:为|是)?\s*[:：=]?\s*", re.IGNORECASE)),
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
    include_pro_symbols: bool = True,
    ocr: OCRCallable | None = None,
    output_path: Path | None = None,
) -> DrawingSpec:
    """Extract fragments from source files and compile into a validated DrawingSpec."""

    fragments = extract_sources(paths, ocr=ocr)
    spec = compile_spec_from_fragments(
        fragments,
        part_family=part_family,
        title_block_language=title_block_language,
        include_pro_symbols=include_pro_symbols,
    )
    if output_path is not None:
        save_spec(spec, output_path)
    return spec


def compile_spec_from_fragments(
    fragments: Sequence[SourceFragment],
    *,
    part_family: PartFamily = "auto",
    title_block_language: Literal["auto", "en", "zh"] = "auto",
    include_pro_symbols: bool = True,
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
        elif "|" not in line:
            m_inl_mat = _INLINE_MATERIAL_RE.search(line)
            if m_inl_mat and not materials:
                materials.append((m_inl_mat.group(1), sid, frag.locator))

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
                # Try parsing BOM row: [item_no?, name, qty, material?]
                qty_match = re.search(r"^(\d+)\s*(?:件|个|套|pcs)?$", cols[-1], re.IGNORECASE)
                mat_col: str | None = None
                if not qty_match and len(cols) >= 3:
                    qty_match = re.search(r"^(\d+)\s*(?:件|个|套|pcs)?$", cols[-2], re.IGNORECASE)
                    if qty_match:
                        mat_col = cols[-1].strip() or None
                if qty_match:
                    item_name = cols[1] if len(cols) >= 3 and cols[0].isdigit() else cols[0]
                    if item_name and not any(k in item_name for k in ("参数", "外径", "内径", "宽度", "高度", "厚度")):
                        part_entry: dict[str, Any] = {
                            "item": item_name,
                            "quantity": int(qty_match.group(1)),
                            "source_id": sid,
                        }
                        if mat_col:
                            part_entry["material"] = mat_col
                        parts_list.append(part_entry)
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

        for m_hkv in _HOLE_KV_PATTERN_RE.finditer(line):
            if "hole_count" not in params:
                _record_param(params, "hole_count", float(m_hkv.group("count")), None, None, sid, frag.locator)
            if "hole_diameter" not in params:
                _record_param(params, "hole_diameter", float(m_hkv.group("dia")), None, None, sid, frag.locator)

        for m_ch in _CONV_HOLES_RE.finditer(line):
            cnt = float(m_ch.group("count"))
            hdia = float(m_ch.group("dia"))
            if "hole_count" not in params:
                _record_param(params, "hole_count", cnt, None, None, sid, frag.locator)
            if "hole_diameter" not in params:
                _record_param(params, "hole_diameter", hdia, None, None, sid, frag.locator)

        for m_rbcd in _REV_BCD_RE.finditer(line):
            if "bolt_circle_diameter" not in params:
                _record_param(params, "bolt_circle_diameter", float(m_rbcd.group("val")), None, None, sid, frag.locator)

        for m_slp in _CONV_SLOT_PAIR_RE.finditer(line):
            _record_param(params, "slot_length", float(m_slp.group("slen")), None, None, sid, frag.locator)
            _record_param(params, "slot_width", float(m_slp.group("swid")), None, None, sid, frag.locator)

        step_matched_spans: list[tuple[int, int]] = []
        for m_step in _SHAFT_STEP_RE.finditer(line):
            dia = float(m_step.group("dia"))
            length = float(m_step.group("len"))
            shaft_steps.append((length, dia, sid))
            step_matched_spans.append(m_step.span())

        for m_cstep in _CONV_SHAFT_STEP_RE.finditer(line):
            sp = m_cstep.span()
            if not any(not (sp[1] <= s0 or sp[0] >= s1) for s0, s1 in step_matched_spans):
                shaft_steps.append((float(m_cstep.group("len")), float(m_cstep.group("dia")), sid))
                step_matched_spans.append(sp)

        claimed_num_spans: list[tuple[int, int]] = []
        for pname, pattern in _PARAM_PATTERNS:
            for match in pattern.finditer(line):
                if step_matched_spans and pname in {"width", "height"}:
                    continue
                tail = line[match.end() :]
                num_m = _NUMBER_TOL_RE.match(tail)
                if num_m:
                    abs_span = (match.end() + num_m.start(), match.end() + num_m.end())
                    if any(not (abs_span[1] <= c0 or abs_span[0] >= c1) for c0, c1 in claimed_num_spans):
                        continue
                    claimed_num_spans.append(abs_span)
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
            mentions_holes = bool(_MOUNTING_HOLE_MENTION_RE.search(all_text)) or bcd is not None or hd is not None
            if mentions_holes and hc <= 0:
                missing.append("missing or unparsed mounting hole_count despite bolt/mounting hole description")
            if (hc > 0 or mentions_holes) and bcd is None:
                missing.append("missing required parameter for flange bolt pattern: bolt_circle_diameter")
            if (hc > 0 or mentions_holes) and hd is None:
                missing.append("missing required parameter for flange bolt pattern: hole_diameter")
            if not missing:
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
                        include_right_section=(not requested_views or any(v in requested_views for v in ("left", "right", "top", "section-A"))),
                        three_views=(not requested_views or "top" in requested_views or "left" in requested_views),
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
                    include_right_view=(not requested_views or any(v in requested_views for v in ("left", "right", "top"))),
                    three_views=(not requested_views or "top" in requested_views or "left" in requested_views),
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
                    include_top_view=(not requested_views or any(v in requested_views for v in ("top", "left", "right"))),
                    three_views=(not requested_views or "left" in requested_views or ("top" not in requested_views)),
                    flat_orientation=True,
                )
                requested_views = built.views
                entities_dump = [e.model_dump(mode="json") for e in built.entities]
                view_indices = built.view_entity_indices
                dimensions_dump = _attach_tolerances(built.dimensions, resolved_params)
                geo_params.update(built.parameters)
            except ValueError as exc:
                conflicts.append(f"slotted-bracket geometric constraint violation: {exc}")

    elif inferred_family == "spur-gear":
        m_p = resolved_params.get("module_mm")
        z_p = resolved_params.get("teeth")
        if m_p is None:
            missing.append("missing required parameter for spur-gear: module_mm")
        if z_p is None:
            missing.append("missing required parameter for spur-gear: teeth")
        if m_p is not None and z_p is not None:
            fw = resolved_params["face_width"].value if "face_width" in resolved_params else (resolved_params["thickness"].value if "thickness" in resolved_params else round(m_p.value * 9.0, 1))
            bd = resolved_params["bore_diameter"].value if "bore_diameter" in resolved_params else round(max(16.0, m_p.value * z_p.value * 0.26), 1)
            try:
                built = build_spur_gear_geometry(
                    SpurGearParameters(
                        module_mm=m_p.value,
                        teeth=int(round(z_p.value)),
                        face_width=fw,
                        bore_diameter=bd,
                    ),
                    source_id=m_p.source_id,
                )
                requested_views = built.views
                entities_dump = [e.model_dump(mode="json") for e in built.entities]
                view_indices = built.view_entity_indices
                dimensions_dump = _attach_tolerances(built.dimensions, resolved_params)
                geo_params.update(built.parameters)
                if project_name is None:
                    project_name = "直齿圆柱齿轮" if resolved_lang == "zh" else "Spur Gear"
            except ValueError as exc:
                conflicts.append(f"spur-gear geometric constraint violation: {exc}")

    elif inferred_family == "bearing-cap":
        od_p = resolved_params.get("outer_diameter")
        if od_p is None:
            missing.append("missing required parameter for bearing-cap: outer_diameter")
        else:
            fd = od_p.value
            spigot = resolved_params["spigot_diameter"].value if "spigot_diameter" in resolved_params else round(fd * 0.68, 1)
            bore = resolved_params["bore_diameter"].value if "bore_diameter" in resolved_params else round(spigot * 0.46, 1)
            th_tot = resolved_params["height"].value if "height" in resolved_params else round(fd * 0.25, 1)
            tf = resolved_params["thickness"].value if "thickness" in resolved_params else round(th_tot * 0.36, 1)
            hc = int(round(resolved_params["hole_count"].value)) if "hole_count" in resolved_params else 6
            hd = resolved_params["hole_diameter"].value if "hole_diameter" in resolved_params else 9.0
            bcd = resolved_params["bolt_circle_diameter"].value if "bolt_circle_diameter" in resolved_params else round((fd + spigot) / 2.0, 1)
            try:
                built = build_bearing_cap_geometry(
                    BearingCapParameters(
                        flange_diameter=fd,
                        spigot_diameter=spigot,
                        bore_diameter=bore,
                        total_height=th_tot,
                        flange_thickness=tf,
                        bolt_circle_diameter=bcd,
                        hole_diameter=hd,
                        hole_count=hc,
                    ),
                    source_id=od_p.source_id,
                )
                requested_views = built.views
                entities_dump = [e.model_dump(mode="json") for e in built.entities]
                view_indices = built.view_entity_indices
                dimensions_dump = _attach_tolerances(built.dimensions, resolved_params)
                geo_params.update(built.parameters)
                if project_name is None:
                    project_name = "轴承透盖" if resolved_lang == "zh" else "Bearing Cap"
            except ValueError as exc:
                conflicts.append(f"bearing-cap geometric constraint violation: {exc}")

    elif inferred_family == "ribbed-bracket":
        w_p = resolved_params.get("width")
        h_p = resolved_params.get("height")
        if w_p is None:
            missing.append("missing required parameter for ribbed-bracket: width (base_length)")
        if h_p is None:
            missing.append("missing required parameter for ribbed-bracket: height (total_height)")
        if w_p is not None and h_p is not None:
            bw = resolved_params["depth"].value if "depth" in resolved_params else round(w_p.value * 0.65, 1)
            t_base = resolved_params["thickness"].value if "thickness" in resolved_params else 14.0
            t_wall = resolved_params["wall_thickness"].value if "wall_thickness" in resolved_params else t_base
            t_rib = resolved_params["rib_thickness"].value if "rib_thickness" in resolved_params else 10.0
            boss_d = resolved_params["boss_hole_diameter"].value if "boss_hole_diameter" in resolved_params else (resolved_params["bore_diameter"].value if "bore_diameter" in resolved_params else 26.0)
            mnt_d = resolved_params["hole_diameter"].value if "hole_diameter" in resolved_params else 12.0
            try:
                built = build_ribbed_bracket_geometry(
                    RibbedBracketParameters(
                        base_length=w_p.value,
                        base_width=bw,
                        total_height=h_p.value,
                        base_thickness=t_base,
                        wall_thickness=t_wall,
                        rib_thickness=t_rib,
                        boss_hole_diameter=boss_d,
                        mount_hole_diameter=mnt_d,
                    ),
                    source_id=w_p.source_id,
                )
                requested_views = built.views
                entities_dump = [e.model_dump(mode="json") for e in built.entities]
                view_indices = built.view_entity_indices
                dimensions_dump = _attach_tolerances(built.dimensions, resolved_params)
                geo_params.update(built.parameters)
                if project_name is None:
                    project_name = "带筋直角支座" if resolved_lang == "zh" else "Ribbed L-Bracket"
            except ValueError as exc:
                conflicts.append(f"ribbed-bracket geometric constraint violation: {exc}")

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

    surface_finishes_dump, datums_dump, gdt_frames_dump = _build_pro_engineering_symbols(
        all_text=all_text,
        family=inferred_family,
        drawing_type=drawing_type,
        requested_views=requested_views,
        source_id=first_source_id,
        enabled=include_pro_symbols and not missing and not conflicts,
    )

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
        "surface_finishes": surface_finishes_dump,
        "datums": datums_dump,
        "gdt_frames": gdt_frames_dump,
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
    if "module_mm" in params or "teeth" in params or re.search(r"齿轮|spur\s*gear|圆柱齿轮", all_text, re.IGNORECASE):
        return "spur-gear"
    if "spigot_diameter" in params or re.search(r"轴承(?:透|闷|端)?盖|端盖|bearing\s*cap", all_text, re.IGNORECASE):
        return "bearing-cap"
    if "rib_thickness" in params or re.search(r"加强筋|肋板|直角支座|L型支座|ribbed\s*bracket", all_text, re.IGNORECASE):
        return "ribbed-bracket"
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
    """Bind tolerances & provenance strictly by semantic `param_key` so equal numeric values never cross-contaminate."""
    key_by_value: list[_SourcedParam] = list(params.values())
    enriched: list[dict[str, Any]] = []
    for dim in dimensions:
        copy_dim = dict(dim)
        pkey = copy_dim.get("param_key")
        if pkey is not None:
            matched = params.get(str(pkey))
            if matched is not None:
                copy_dim["source_id"] = matched.source_id
                copy_dim["tolerance_upper"] = matched.tol_upper
                copy_dim["tolerance_lower"] = matched.tol_lower
            enriched.append(copy_dim)
            continue
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


def _build_pro_engineering_symbols(
    *,
    all_text: str,
    family: str,
    drawing_type: str,
    requested_views: list[str],
    source_id: str,
    enabled: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract or synthesize GB/T 131 surface roughness, datum [A], and GD&T feature control frames."""
    if not enabled or not requested_views:
        return [], [], []
    if re.search(r"关闭工程符号|不标粗糙度|pro_symbols\s*[:：=]\s*false", all_text, re.IGNORECASE):
        return [], [], []
    if family == "generic":
        return [], [], []

    # Parse user-specified Ra values if mentioned in text
    m_gen_ra = re.search(r"其余\s*(?:表面)?(?:粗糙度)?\s*Ra\s*(\d+(?:\.\d+)?)", all_text, re.IGNORECASE)
    gen_ra = f"Ra {m_gen_ra.group(1)}" if m_gen_ra else ("Ra 12.5" if family in {"bearing-cap", "ribbed-bracket"} else "Ra 6.3")

    all_ras = [m.group(1) for m in re.finditer(r"(?<!其余)Ra\s*(\d+(?:\.\d+)?)", all_text, re.IGNORECASE)]
    if all_ras:
        mating_ra = f"Ra {all_ras[0]}"
    else:
        mating_ra = "Ra 1.6" if family in {"spur-gear", "stepped-shaft", "bearing-cap"} else "Ra 3.2"

    finishes: list[dict[str, Any]] = [
        {
            "value": gen_ra,
            "source_id": source_id,
            "machining_required": family not in {"bearing-cap"},
            "is_general": True,
        }
    ]
    if "front" in requested_views:
        finishes.append(
            {
                "value": mating_ra,
                "source_id": source_id,
                "machining_required": True,
                "is_general": False,
                "owner_view": "front",
                "placement": "top-left",
            }
        )

    if drawing_type == "assembly":
        return finishes, [], []

    datums: list[dict[str, Any]] = []
    gdt_frames: list[dict[str, Any]] = []
    primary_view = "front" if "front" in requested_views else requested_views[0]
    gdt_view = "left" if "left" in requested_views else primary_view

    datums.append(
        {
            "letter": "A",
            "source_id": source_id,
            "owner_view": primary_view,
            "placement": "left-center",
        }
    )

    # Check if user explicitly specified a geometric tolerance in text
    gdt_sym: str
    gdt_tol: str
    m_coax = re.search(r"(?:同轴度|同心度|coaxiality)\s*[⌀∅Φφ]?\s*(\d+(?:\.\d+)?)", all_text, re.IGNORECASE)
    m_perp = re.search(r"(?:垂直度|perpendicularity)\s*[⌀∅Φφ]?\s*(\d+(?:\.\d+)?)", all_text, re.IGNORECASE)
    m_para = re.search(r"(?:平行度|parallelism)\s*(\d+(?:\.\d+)?)", all_text, re.IGNORECASE)
    m_run = re.search(r"(?:圆?跳动|runout)\s*(\d+(?:\.\d+)?)", all_text, re.IGNORECASE)
    if m_coax:
        gdt_sym, gdt_tol = "coaxiality", f"⌀{m_coax.group(1)}"
    elif m_perp:
        gdt_sym, gdt_tol = "perpendicularity", m_perp.group(1)
    elif m_para:
        gdt_sym, gdt_tol = "parallelism", m_para.group(1)
    elif m_run:
        gdt_sym, gdt_tol = "runout", m_run.group(1)
    elif family in {"spur-gear", "bearing-cap", "stepped-shaft"}:
        gdt_sym, gdt_tol = "coaxiality", "⌀0.02"
    else:
        gdt_sym, gdt_tol = "perpendicularity", "0.03"

    gdt_frames.append(
        {
            "symbol": gdt_sym,
            "tolerance": gdt_tol,
            "datum_refs": ["A"],
            "source_id": source_id,
            "owner_view": gdt_view,
            "placement": "above-label",
        }
    )
    return finishes, datums, gdt_frames
