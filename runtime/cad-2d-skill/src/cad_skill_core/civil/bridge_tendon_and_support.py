"""Unified Sheet Manifest, Project Profile, Shared Support Chain & 3D Tendon Clearance Auditor.

Provides a single source of truth across DXF drawings, A3 PDF print albums, Markdown calculation
books, and formal Word graduation theses (.docx) for:
1. Sheet manifest (`wenchang_22` 22-sheet construction set `01..19, 19-1, 20, 21` & `ql_18` legacy set,
   plus arbitrary user additions/renames with automatic cross-reference updates);
2. Clean, non-leaking project & student cover metadata (never injecting another student's reference PII);
3. Shared longitudinal & vertical bearing support chain (`480 + 40 + 480 = 1000 mm` pier-top pitch,
   `24960 - 2×480 = 24000 mm` calc span, `20 + 74 + 150 = 244 mm` vertical stack) across sheets 06, 13, 15;
4. Dr.Bridge (`桥梁博士` `new.txt` & model JSON) tendon/section parser, exact 3D piecewise straight +
   circular-arc tendon curve generator (shared by sheets 08, 09, and 02 corrugated duct BOQ), and
   station-by-station 3D centroid / duct outer-edge / variable-web polygon clearance auditor
   (catching e.g. `N4` at `x = 2980 mm` from beam end protruding `10 mm` outside the `200 mm` web).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
from pathlib import Path
import re
from typing import Any


# Canonical role_key -> (wenchang_22_no, legacy_ql_18_no_or_None, category_id, drawing_type, sub_spec, default_title, scale_label)
_CANONICAL_SHEET_DEFS: tuple[tuple[str, str, str | None, str | None, str, dict[str, Any], str, str], ...] = (
    ("general_notes", "01", "QL-01", None, "notes_and_boq", {"variant": "notes"}, "设计总说明及全套施工图目录", "无比例"),
    ("boq_summary", "02", "QL-02", None, "notes_and_boq", {"variant": "boq"}, "全桥主要工程材料数量汇总表", "表格"),
    ("bridge_elevation", "03", "QL-03", "cat1_general_layout", "bridge_elevation", {}, "桥型总体布置图（一：立面图）", "1:400"),
    ("bridge_plan", "04", "QL-04", "cat1_general_layout", "bridge_plan_section", {}, "桥型总体布置图（二：平面及横剖面图）", "1:400/1:100"),
    ("deck_cross_section", "05", "QL-05", "cat5_standard_cross_section", "t_girder_section", {}, "主梁标准横断面及桥面铺装构造图", "1:40/1:25/1:10"),
    ("girder_longitudinal", "06", "QL-06", "cat2_girder_general", "t_girder_general", {"variant": "longitudinal"}, "25m预制T梁一般构造图（一：纵立面及平面）", "1:80/1:25"),
    ("girder_cross_details", "07", "QL-07", "cat2_girder_general", "t_girder_general", {"variant": "cross_details"}, "25m预制T梁一般构造图（二：边中梁变截面详图）", "1:25"),
    ("tendon_layout", "08", "QL-08", "cat3_tendon_layout", "tendon_layout", {"variant": "layout"}, "预制T梁预应力钢束构造及张拉布置图", "1:15/1:90"),
    ("tendon_coordinates", "09", None, "cat3_tendon_layout", "tendon_layout", {"variant": "coordinates"}, "预制T梁预应力钢束束心坐标及定位钢筋图", "1:10/1:70"),
    ("girder_web_rebar", "10", "QL-09", "cat4_girder_rebar", "rebar_detail", {"component": "t_girder_web"}, "主梁梁肋普通钢筋构造图", "1:20/1:80"),
    ("girder_flange_rebar", "11", "QL-10", "cat4_girder_rebar", "rebar_detail", {"component": "t_girder_flange"}, "主梁翼缘板及湿接缝钢筋构造图", "1:20/1:10"),
    ("diaphragm_rebar", "12", "QL-11", "cat4_girder_rebar", "rebar_detail", {"component": "diaphragm"}, "端横隔梁与中横隔梁钢筋构造图", "1:65/1:25"),
    ("bearing_layout", "13", "QL-12", "cat6_bearing_detail", "bearing_layout", {}, "板式橡胶支座及支座垫石构造布置图", "1:5/1:10/1:25"),
    ("deck_ancillary", "14", "QL-13", "cat7_deck_ancillary", "deck_ancillary", {}, "桥面附属结构构造图（护栏/伸缩缝/泄水管/搭板）", "1:10/1:15/1:40/1:60"),
    ("pier_general", "15", "QL-14", "cat8_substructure_general", "pier_and_abutment", {"variant": "pier"}, "双柱式桥墩一般构造图", "1:25/1:80"),
    ("abutment_general", "16", "QL-15", "cat8_substructure_general", "pier_and_abutment", {"variant": "abutment"}, "桩柱式桥台一般构造图", "1:80"),
    ("pier_cap_rebar", "17", "QL-16", "cat9_substructure_rebar", "rebar_detail", {"component": "pier_cap"}, "桥墩盖梁普通钢筋构造图（一：主筋及立面剖面）", "1:65/1:30/1:35"),
    ("pier_cap_secondary", "18", None, "cat9_substructure_rebar", "rebar_detail", {"component": "pier_cap_secondary"}, "桥墩盖梁普通钢筋构造图（二：弯起筋、挡块及大样）", "1:20/1:25/1:50"),
    ("pier_column_pile_rebar", "19", "QL-17", "cat9_substructure_rebar", "rebar_detail", {"component": "pier_column_pile"}, "桥墩墩柱及钻孔灌注桩钢筋构造图", "图示比例"),
    ("tie_beam_joint_rebar", "19-1", None, "cat9_substructure_rebar", "rebar_detail", {"component": "tie_beam_joint"}, "桩顶系梁钢筋构造及节点详图", "1:20/1:40"),
    ("abutment_rebar", "20", "QL-18", "cat9_substructure_rebar", "rebar_detail", {"component": "abutment_rebar"}, "桩柱式桥台台帽及钻孔灌注桩钢筋构造图", "图示比例"),
    ("abutment_ear_backwall_rebar", "21", None, "cat9_substructure_rebar", "rebar_detail", {"component": "abutment_ear_backwall"}, "桥台耳墙及背墙钢筋展开构造图", "1:25/1:65"),
)


WENCHANG_22_SHEET_INDEX: tuple[tuple[str, str, str], ...] = tuple(
    (dno_22, title, scale) for _, dno_22, _, _, _, _, title, scale in _CANONICAL_SHEET_DEFS
)


def build_bridge_sheet_manifest(
    *,
    sheet_scheme: str = "wenchang_22",
    custom_manifest: Sequence[Mapping[str, Any]] | None = None,
    sheet_title_overrides: Mapping[str, str] | None = None,
    extra_sheets: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a single authoritative sheet manifest shared by DXF, PDF album, Calc Book, and Word Thesis."""
    scheme_norm = str(sheet_scheme or "wenchang_22").strip().lower()
    title_over = {str(k): str(v) for k, v in (sheet_title_overrides or {}).items()}

    entries: list[dict[str, Any]] = []
    if custom_manifest:
        for idx, raw_e in enumerate(custom_manifest, start=1):
            dno = str(raw_e["drawing_no"]).strip()
            role_k = str(raw_e.get("role_key") or f"custom_{idx}")
            t_raw = title_over.get(dno) or title_over.get(role_k) or str(raw_e.get("title") or dno)
            entries.append(
                {
                    "seq_no": idx,
                    "role_key": role_k,
                    "drawing_no": dno,
                    "legacy_ql_no": raw_e.get("legacy_ql_no"),
                    "category_id": raw_e.get("category_id"),
                    "drawing_type": str(raw_e.get("drawing_type", "notes_and_boq")),
                    "sub_spec": dict(raw_e.get("sub_spec") or {}),
                    "title": t_raw,
                    "scale_label": str(raw_e.get("scale_label", "图示比例")),
                    "sheet_size": str(raw_e.get("sheet_size", "A3")),
                    "bookmark_name": f"bm_sheet_{re.sub(r'[^0-9A-Za-z_]', '_', dno)}",
                }
            )
    else:
        use_18 = scheme_norm in {"ql_18", "legacy_18", "standard_18", "18"}
        seq_i = 0
        for role_k, dno_22, dno_18, cat_id, dtype, sub_spec, def_title, scale_lbl in _CANONICAL_SHEET_DEFS:
            if use_18 and dno_18 is None:
                continue
            seq_i += 1
            dno = str(dno_18 if use_18 else dno_22)
            base_title = def_title
            if use_18 and role_k == "pier_cap_rebar":
                base_title = "桥墩盖梁普通钢筋构造图"
            elif use_18 and role_k == "pier_column_pile_rebar":
                base_title = "桥墩墩柱、系梁及钻孔桩钢筋构造图"
            elif use_18 and role_k == "abutment_rebar":
                base_title = "桥台台帽、耳背墙及桩基钢筋构造图"
            final_title = title_over.get(dno) or title_over.get(role_k) or base_title
            entries.append(
                {
                    "seq_no": seq_i,
                    "role_key": role_k,
                    "drawing_no": dno,
                    "legacy_ql_no": dno_18,
                    "wenchang_22_no": dno_22,
                    "category_id": cat_id,
                    "drawing_type": dtype,
                    "sub_spec": dict(sub_spec),
                    "title": final_title,
                    "scale_label": scale_lbl,
                    "sheet_size": "A3",
                    "bookmark_name": f"bm_sheet_{re.sub(r'[^0-9A-Za-z_]', '_', dno)}",
                }
            )

    if extra_sheets:
        for raw_ex in extra_sheets:
            seq_next = len(entries) + 1
            dno_ex = str(raw_ex["drawing_no"]).strip()
            role_ex = str(raw_ex.get("role_key") or f"extra_{seq_next}")
            t_ex = title_over.get(dno_ex) or title_over.get(role_ex) or str(raw_ex.get("title") or dno_ex)
            entries.append(
                {
                    "seq_no": seq_next,
                    "role_key": role_ex,
                    "drawing_no": dno_ex,
                    "legacy_ql_no": raw_ex.get("legacy_ql_no"),
                    "category_id": raw_ex.get("category_id"),
                    "drawing_type": str(raw_ex.get("drawing_type", "notes_and_boq")),
                    "sub_spec": dict(raw_ex.get("sub_spec") or {"variant": "notes"}),
                    "title": t_ex,
                    "scale_label": str(raw_ex.get("scale_label", "无比例")),
                    "sheet_size": str(raw_ex.get("sheet_size", "A3")),
                    "bookmark_name": f"bm_sheet_{re.sub(r'[^0-9A-Za-z_]', '_', dno_ex)}",
                }
            )

    by_role = {e["role_key"]: e for e in entries}
    by_no = {e["drawing_no"]: e for e in entries}
    first_no = entries[0]["drawing_no"] if entries else "01"
    last_no = entries[-1]["drawing_no"] if entries else "21"
    range_label = (
        f"{first_no}～{last_no}"
        if str(first_no).upper().startswith("QL")
        else f"图{first_no}～图{last_no}"
    )

    return {
        "sheet_scheme": "custom" if custom_manifest else ("ql_18" if scheme_norm in {"ql_18", "legacy_18", "standard_18", "18"} else "wenchang_22"),
        "sheet_count": len(entries),
        "range_label": range_label,
        "entries": entries,
        "by_role": by_role,
        "by_drawing_no": by_no,
        "index_tuples": [(e["drawing_no"], e["title"], e["scale_label"]) for e in entries],
    }


def format_sheet_cite(drawing_no: str) -> str:
    """Format a drawing number cleanly for prose citation ('QL-08' -> 'QL-08', '08' -> '图08', '19-1' -> '图19-1')."""
    s = str(drawing_no).strip()
    if not s:
        return ""
    if s.upper().startswith(("QL", "JS", "图")):
        return s
    return f"图{s}"


def resolve_sheet_no(
    manifest_or_calc: Mapping[str, Any] | None,
    role_key: str,
    fallback: str = "",
) -> str:
    """Resolve current drawing_no by canonical role_key from a manifest or calculation report."""
    if not manifest_or_calc:
        return fallback
    man = manifest_or_calc.get("sheet_manifest") if "sheet_manifest" in manifest_or_calc else manifest_or_calc
    if isinstance(man, Mapping):
        by_r = man.get("by_role") or {}
        if role_key in by_r:
            return str(by_r[role_key]["drawing_no"])
        for e in man.get("entries") or []:
            if e.get("role_key") == role_key:
                return str(e["drawing_no"])
    return fallback


def resolve_project_and_student_profile(
    calc_report: Mapping[str, Any],
    student_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve cover/project profile WITHOUT ever injecting another student's reference sample PII."""
    ps = calc_report.get("project_summary") or {}
    base_profile = dict(calc_report.get("project_profile") or {})
    explicit_meta = dict(student_metadata or {})
    merged = {**base_profile, **explicit_meta}

    is_wenchang = "文昌" in str(ps.get("project_name", ""))
    school_name = str(merged.get("school_name") or ("郑州升达经贸管理学院" if is_wenchang else "高等院校土木工程专业")).strip()
    college_name = str(merged.get("college_name") or ("建筑工程学院" if is_wenchang else "土木与交通工程学院")).strip()
    major_class = str(merged.get("major_class") or "").strip()
    student_name = str(merged.get("student_name") or "").strip()
    student_id = str(merged.get("student_id") or "").strip()
    advisor_name = str(merged.get("advisor_name") or "").strip()
    completion_date = str(merged.get("completion_date") or ("2027 年 4 月" if is_wenchang else "")).strip()
    thesis_title = str(merged.get("thesis_title") or ps.get("project_name", "装配式预应力混凝土简支T梁桥设计")).strip()
    thesis_title_en = str(
        merged.get("thesis_title_en")
        or f"Design of {str(ps.get('project_name', 'Bridge')).split('（')[0]} ({ps.get('n_spans', 4)}×{ps.get('standard_span_m', 25):g} m Precast PC Simply Supported T-Girder Bridge)"
    ).strip()

    return {
        "school_name": school_name,
        "college_name": college_name,
        "major_class": major_class,
        "student_name": student_name,
        "student_id": student_id,
        "advisor_name": advisor_name,
        "completion_date": completion_date,
        "thesis_title": thesis_title,
        "thesis_title_en": thesis_title_en,
        "school_logo_path": merged.get("school_logo_path"),
        "identity_supplied": bool(student_name and advisor_name),
    }


# ======================================================================
# Shared Support Coordinate & Dimension Chain (Sheets 06, 13, 15 / QL-06, QL-12, QL-14)
# ======================================================================
def compute_shared_support_layout(
    *,
    standard_span_m: float = 25.0,
    beam_length_m: float = 24.96,
    calc_span_m: float | None = None,
    bearing_center_offset_m: float | None = None,
    bearing_model: str = "GJZ 300×400×74",
    pedestal_width_mm: float = 520.0,
    pedestal_length_mm: float = 600.0,
    pedestal_height_mm: float = 150.0,
    wedge_plate_thick_mm: float = 20.0,
    n_spans: int = 4,
    n_girders: int = 5,
) -> dict[str, Any]:
    """Compute a single consistent support coordinate chain (`480 + 40 + 480 = 1000 mm` & `20 + 74 + 150 = 244 mm`)."""
    std_mm = round(float(standard_span_m) * 1000.0, 1)
    beam_mm = round(float(beam_length_m) * 1000.0, 1)
    gap_mm = round(max(20.0, std_mm - beam_mm), 1)

    if bearing_center_offset_m is not None:
        d_end_mm = round(float(bearing_center_offset_m) * 1000.0, 1)
        c_span_mm = round(beam_mm - 2.0 * d_end_mm, 1)
    elif calc_span_m is not None:
        c_span_mm = round(float(calc_span_m) * 1000.0, 1)
        d_end_mm = round(0.5 * (beam_mm - c_span_mm), 1)
    else:
        d_end_mm = 480.0
        c_span_mm = round(beam_mm - 2.0 * d_end_mm, 1)

    pier_row_pitch_mm = round(d_end_mm + gap_mm + d_end_mm, 1)

    parts = str(bearing_model).replace("GJZ", "").replace("GYZ", "").strip().split("×")
    brg_w_mm = float(parts[0]) if len(parts) == 3 else 300.0
    brg_l_mm = float(parts[1]) if len(parts) == 3 else 400.0
    brg_t_mm = float(parts[2]) if len(parts) == 3 else 74.0

    vert_total_mm = round(wedge_plate_thick_mm + brg_t_mm + pedestal_height_mm, 1)
    total_pedestals = n_spans * n_girders * 2
    single_ped_m3 = (pedestal_width_mm * 1e-3) * (pedestal_length_mm * 1e-3) * (pedestal_height_mm * 1e-3)
    total_pedestal_c40_m3 = round(total_pedestals * single_ped_m3, 2)

    chain_closed = (
        math.isclose(c_span_mm + 2.0 * d_end_mm, beam_mm, abs_tol=0.5)
        and math.isclose(beam_mm + gap_mm, std_mm, abs_tol=0.5)
        and math.isclose(2.0 * d_end_mm + gap_mm, pier_row_pitch_mm, abs_tol=0.5)
    )

    return {
        "standard_span_mm": std_mm,
        "beam_length_mm": beam_mm,
        "calc_span_mm": c_span_mm,
        "beam_end_to_bearing_mm": d_end_mm,
        "expansion_gap_mm": gap_mm,
        "pier_bearing_row_pitch_mm": pier_row_pitch_mm,
        "pier_bearing_c2c_mm": pier_row_pitch_mm,
        "pier_top_chain_mm": [d_end_mm, gap_mm, d_end_mm],
        "pier_left_bearing_x_mm": round(-0.5 * pier_row_pitch_mm, 1),
        "pier_right_bearing_x_mm": round(0.5 * pier_row_pitch_mm, 1),
        "pier_chain_expression": f"{d_end_mm:g}+{gap_mm:g}+{d_end_mm:g}={pier_row_pitch_mm:g}",
        "bearing_plan_mm": {"transverse_mm": brg_w_mm, "longitudinal_mm": brg_l_mm, "thickness_mm": brg_t_mm},
        "total_support_stack_mm": vert_total_mm,
        "vertical_stack_mm": {
            "wedge_steel_plate_mm": wedge_plate_thick_mm,
            "bearing_thickness_mm": brg_t_mm,
            "pedestal_height_mm": pedestal_height_mm,
            "total_support_height_mm": vert_total_mm,
            "stack_expression": f"{wedge_plate_thick_mm:g}+{brg_t_mm:g}+{pedestal_height_mm:g}={vert_total_mm:g}",
        },
        "pedestal_geometry": {
            "transverse_width_mm": pedestal_width_mm,
            "longitudinal_length_mm": pedestal_length_mm,
            "height_mm": pedestal_height_mm,
            "concrete_grade": "C40",
            "total_count_bridge": total_pedestals,
            "single_volume_m3": round(single_ped_m3, 4),
            "total_volume_m3": total_pedestal_c40_m3,
        },
        "chain_closed": chain_closed,
    }


# ======================================================================
# Dr.Bridge `new.txt` / JSON Parser + Exact 3D Tendon Curve & Section Clearance Auditor
# ======================================================================
def _canonical_tendon_id(raw_name: str) -> str:
    s = str(raw_name).strip().upper()
    # Normalize Dr.Bridge 'N4-1' / 'N4_1' -> 'N4'
    m = re.match(r"^(N\d+)(?:[-_]\d+)?$", s)
    if m:
        return m.group(1)
    return s


def parse_drbridge_tendon_and_section_model(
    model_input: Mapping[str, Any] | str | Path | None,
) -> dict[str, Any] | None:
    """Parse Dr.Bridge `new.txt` plain-text tendon/section file or JSON model dict."""
    if model_input is None:
        return None
    if isinstance(model_input, Mapping):
        raw = dict(model_input)
        return _normalize_parsed_tendon_dict(raw)

    text: str
    src_name = "inline_drbridge_new_txt"
    if isinstance(model_input, Path) or (
        isinstance(model_input, str) and "\n" not in model_input and Path(model_input).expanduser().is_file()
    ):
        p = Path(model_input).expanduser().resolve()
        src_name = p.name
        text = p.read_text(encoding="utf-8-sig")
        if p.suffix.lower() == ".json":
            return _normalize_parsed_tendon_dict(json.loads(text), source_name=src_name)
    elif isinstance(model_input, str):
        text = model_input.strip()
        if text.startswith("{"):
            return _normalize_parsed_tendon_dict(json.loads(text), source_name="inline_json")
    else:
        raise TypeError(f"unsupported tendon model input type: {type(model_input).__name__}")

    # Plain-text Dr.Bridge `new.txt` parser
    unit_scale = 1.0  # default mm; switches to 1000.0 if unit=m declared
    section_over: dict[str, Any] = {}
    tendons_by_id: dict[str, dict[str, Any]] = {}
    cur_tid: str | None = None
    cur_mode: str = "vert"  # 'vert' | 'plan' | 'station3d'

    for raw_ln in text.splitlines():
        ln = raw_ln.split("#", 1)[0].split("//", 1)[0].strip()
        if not ln:
            continue
        low = ln.lower()
        if low.startswith(("unit=", "unit:", "单位=", "单位:")):
            u_tok = low.split("=", 1)[-1].split(":", 1)[-1].strip()
            unit_scale = 1000.0 if u_tok in {"m", "meter", "meters", "米"} else 1.0
            continue
        if low.startswith(("duct_diameter", "管道直径", "波纹管直径")):
            nums = [float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?", ln)]
            if nums:
                section_over["duct_diameter_mm"] = nums[0] * (unit_scale if nums[0] < 1.0 else 1.0)
            continue
        if low.startswith(("end_thick_len", "加厚段", "web_taper")):
            nums = [float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?", ln)]
            if len(nums) >= 2:
                section_over["end_thick_zone_end_mm"] = nums[0] * unit_scale
                section_over["taper_zone_end_mm"] = nums[1] * unit_scale
            continue

        # Tendon header e.g. `[N4]`, `[N4-1]`, `TENDON N4`, `钢束 N4-1`
        m_hdr = re.match(r"^(?:\[\s*(N\d+(?:[-_]\d+)?)\s*\]|(?:tendon|钢束|束号)\s*[:=]?\s*(N\d+(?:[-_]\d+)?))(.*)$", ln, re.IGNORECASE)
        if m_hdr:
            raw_tid = m_hdr.group(1) or m_hdr.group(2)
            cur_tid = _canonical_tendon_id(raw_tid)
            tendons_by_id.setdefault(
                cur_tid,
                {
                    "tendon_id": cur_tid,
                    "raw_id": raw_tid,
                    "vertical_points": [],
                    "plan_points": [],
                    "stations_3d": [],
                },
            )
            rest_hdr = (m_hdr.group(3) or "").lower()
            if "plan" in rest_hdr or "平弯" in rest_hdr:
                cur_mode = "plan"
            elif "3d" in rest_hdr or "station" in rest_hdr:
                cur_mode = "station3d"
            else:
                cur_mode = "vert"
            continue

        if low in {"[竖弯]", "竖弯", "vertical", "[vertical]", "vert:"}:
            cur_mode = "vert"
            continue
        if low in {"[平弯]", "平弯", "plan", "[plan]", "plan:"}:
            cur_mode = "plan"
            continue
        if low in {"[三维坐标]", "[stations]", "stations:"}:
            cur_mode = "station3d"
            continue

        # Inline prefixed row: `N4 2980 680 110` or `N4-1: x=2980, y=680, z=110`
        m_inline_tid = re.match(r"^(N\d+(?:[-_]\d+)?)\b[\s,:;|]*(.+)$", ln, re.IGNORECASE)
        if m_inline_tid:
            raw_tid2 = m_inline_tid.group(1)
            cur_tid = _canonical_tendon_id(raw_tid2)
            tendons_by_id.setdefault(
                cur_tid,
                {
                    "tendon_id": cur_tid,
                    "raw_id": raw_tid2,
                    "vertical_points": [],
                    "plan_points": [],
                    "stations_3d": [],
                },
            )
            ln_body = m_inline_tid.group(2)
            if any(k in ln_body.lower() for k in ("z=", "z:")):
                kv_nums = {
                    k_m.lower(): float(v_m) * unit_scale
                    for k_m, v_m in re.findall(r"([xyzr])\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", ln_body, re.IGNORECASE)
                }
                if "x" in kv_nums and ("y" in kv_nums or "z" in kv_nums):
                    tendons_by_id[cur_tid]["stations_3d"].append(
                        {
                            "x_mm": kv_nums["x"],
                            "y_mm": kv_nums.get("y", 680.0),
                            "z_mm": kv_nums.get("z", 0.0),
                        }
                    )
                    continue
            nums = [float(x) * unit_scale for x in re.findall(r"[-+]?\d+(?:\.\d+)?", ln_body)]
            if len(nums) >= 3:
                tendons_by_id[cur_tid]["stations_3d"].append({"x_mm": nums[0], "y_mm": nums[1], "z_mm": nums[2]})
            elif len(nums) == 2:
                tendons_by_id[cur_tid]["vertical_points"].append({"x_mm": nums[0], "y_mm": nums[1], "r_mm": 0.0})
            continue

        if cur_tid is not None:
            nums = [float(x) * unit_scale for x in re.findall(r"[-+]?\d+(?:\.\d+)?", ln)]
            if not nums:
                continue
            t_rec = tendons_by_id[cur_tid]
            if cur_mode == "station3d" and len(nums) >= 3:
                t_rec["stations_3d"].append({"x_mm": nums[0], "y_mm": nums[1], "z_mm": nums[2]})
            elif cur_mode == "plan" and len(nums) >= 2:
                t_rec["plan_points"].append({"x_mm": nums[0], "z_mm": nums[1], "r_mm": nums[2] if len(nums) >= 3 else 0.0})
            elif len(nums) >= 2:
                t_rec["vertical_points"].append(
                    {
                        "x_mm": nums[0],
                        "y_mm": nums[1],
                        "r_mm": nums[2] if len(nums) >= 3 else 0.0,
                        "_has_col3": len(nums) >= 3,
                    }
                )

    # Disambiguate 3-column `(x, y, z)` 3D coordinate tables from `(x, y, R)` vertical bend tables:
    # vertical bend radii in JTG 3362 are >= 1000 mm (or 0 at ends), whereas lateral z offsets have |z| < 800 mm or z < 0.
    for t_rec in tendons_by_id.values():
        vpts = t_rec["vertical_points"]
        if vpts and not t_rec["stations_3d"] and all(p.get("_has_col3") for p in vpts):
            col3s = [float(p["r_mm"]) for p in vpts]
            if any(c < 0.0 or (0.0 < abs(c) < 800.0) for c in col3s) or all(c == 0.0 for c in col3s):
                t_rec["stations_3d"] = [
                    {"x_mm": float(p["x_mm"]), "y_mm": float(p["y_mm"]), "z_mm": float(p["r_mm"])}
                    for p in vpts
                ]
                t_rec["vertical_points"] = []
        for p in t_rec["vertical_points"]:
            p.pop("_has_col3", None)

    tendons_list = list(tendons_by_id.values())
    return {
        "source_name": src_name,
        "section_overrides": section_over,
        "tendons": tendons_list,
        "tendons_by_id": {t["tendon_id"]: t for t in tendons_list},
    }


def _normalize_parsed_tendon_dict(raw: Mapping[str, Any], *, source_name: str = "drbridge_model_json") -> dict[str, Any]:
    tendons_out: list[dict[str, Any]] = []
    raw_tendons = raw.get("tendons") or []
    if isinstance(raw_tendons, Mapping):
        raw_iter = [{"tendon_id": k, **(dict(v) if isinstance(v, Mapping) else {})} for k, v in raw_tendons.items()]
    else:
        raw_iter = [dict(x) for x in raw_tendons if isinstance(x, Mapping)]

    for item in raw_iter:
        raw_id = str(item.get("tendon_id") or item.get("id") or item.get("name") or "N1")
        cid = _canonical_tendon_id(raw_id)
        tendons_out.append(
            {
                **item,
                "tendon_id": cid,
                "raw_id": raw_id,
                "vertical_points": list(item.get("vertical_points") or []),
                "plan_points": list(item.get("plan_points") or []),
                "stations_3d": list(item.get("stations_3d") or item.get("stations") or []),
            }
        )

    return {
        "source_name": str(raw.get("source_name") or source_name),
        "section_overrides": dict(raw.get("section_overrides") or raw.get("section") or {}),
        "tendons": tendons_out,
        "tendons_by_id": {t["tendon_id"]: t for t in tendons_out},
    }


def _eval_piecewise_rounded_polyline(
    pts_x_v_r: Sequence[tuple[float, float, float]],
    x_query: float,
) -> tuple[float, float]:
    """Evaluate value v(x) and slope dv/dx on a Dr.Bridge piecewise linear + circular fillet profile."""
    n = len(pts_x_v_r)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        return float(pts_x_v_r[0][1]), 0.0

    # Clamp x_query
    if x_query <= pts_x_v_r[0][0]:
        dx0 = max(1e-6, pts_x_v_r[1][0] - pts_x_v_r[0][0])
        m0 = (pts_x_v_r[1][1] - pts_x_v_r[0][1]) / dx0
        return float(pts_x_v_r[0][1]), m0
    if x_query >= pts_x_v_r[-1][0]:
        dxn = max(1e-6, pts_x_v_r[-1][0] - pts_x_v_r[-2][0])
        mn = (pts_x_v_r[-1][1] - pts_x_v_r[-2][1]) / dxn
        return float(pts_x_v_r[-1][1]), mn

    # Check interior vertex fillets first (exact tangent circular/parabolic transition of radius R)
    for idx in range(1, n - 1):
        xi, vi, ri = pts_x_v_r[idx]
        if ri <= 1.0:
            continue
        x_prev, v_prev, _ = pts_x_v_r[idx - 1]
        x_next, v_next, _ = pts_x_v_r[idx + 1]
        m1 = (vi - v_prev) / max(1e-6, xi - x_prev)
        m2 = (v_next - vi) / max(1e-6, x_next - xi)
        dm = m2 - m1
        if abs(dm) < 1e-8:
            continue
        # Tangent horizontal half-length t_x = R * |atan(m2)-atan(m1)| / 2 (clamped to 45% of adjacent segments)
        d_theta = abs(math.atan(m2) - math.atan(m1))
        tx = min(ri * math.tan(0.5 * d_theta), 0.45 * (xi - x_prev), 0.45 * (x_next - xi))
        if tx > 1.0 and (xi - tx) <= x_query <= (xi + tx):
            x_a = xi - tx
            v_a = vi - m1 * tx
            u = (x_query - x_a) / (2.0 * tx)  # u in [0, 1]
            # Exact C1 tangent transition matching (x_a, v_a, m1) -> (xi+tx, vi+m2*tx, m2)
            val = v_a + (x_query - x_a) * m1 + 0.5 * (m2 - m1) * ((x_query - x_a) ** 2) / (2.0 * tx)
            slope = m1 + u * (m2 - m1)
            return val, slope

    # Otherwise on straight segment between vertices
    for idx in range(n - 1):
        x0, v0, _ = pts_x_v_r[idx]
        x1, v1, _ = pts_x_v_r[idx + 1]
        if x0 <= x_query <= x1 + 1e-9:
            m = (v1 - v0) / max(1e-6, x1 - x0)
            return v0 + m * (x_query - x0), m
    return float(pts_x_v_r[-1][1]), 0.0


def _interp_stations_3d(stations: Sequence[Mapping[str, Any]], x_query: float) -> tuple[float, float, float, float]:
    pts = sorted(
        [(float(s["x_mm"]), float(s.get("y_mm", 0.0)), float(s.get("z_mm", 0.0))) for s in stations],
        key=lambda t: t[0],
    )
    if not pts:
        return 0.0, 0.0, 0.0, 0.0
    if len(pts) == 1:
        return pts[0][1], 0.0, pts[0][2], 0.0
    if x_query <= pts[0][0]:
        dx = max(1e-6, pts[1][0] - pts[0][0])
        return pts[0][1], (pts[1][1] - pts[0][1]) / dx, pts[0][2], (pts[1][2] - pts[0][2]) / dx
    if x_query >= pts[-1][0]:
        dx = max(1e-6, pts[-1][0] - pts[-2][0])
        return pts[-1][1], (pts[-1][1] - pts[-2][1]) / dx, pts[-1][2], (pts[-1][2] - pts[-2][2]) / dx
    for i in range(len(pts) - 1):
        x0, y0, z0 = pts[i]
        x1, y1, z1 = pts[i + 1]
        if x0 <= x_query <= x1 + 1e-9:
            dx = max(1e-6, x1 - x0)
            t = (x_query - x0) / dx
            return y0 + t * (y1 - y0), (y1 - y0) / dx, z0 + t * (z1 - z0), (z1 - z0) / dx
    return pts[-1][1], 0.0, pts[-1][2], 0.0


def build_bridge_tendon_3d_geometry(
    *,
    n_tendons: int,
    strands_per_tendon: int,
    beam_length_mm: float = 24960.0,
    calc_span_mm: float = 24000.0,
    girder_height_mm: float = 1750.0,
    bulb_width_mm: float = 560.0,
    duct_diameter_mm: float = 70.0,
    anchorage_working_length_mm: float = 700.0,
    parsed_model: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate unified 3D tendon curves, station coordinates, tangent angles, and corrugated duct lengths."""
    half_l = 0.5 * beam_length_mm
    brg_off = round(0.5 * (beam_length_mm - calc_span_mm), 1)
    bulb_dx = min(130.0, max(70.0, round(0.23 * bulb_width_mm, 1)))

    parsed_by_id: dict[str, Mapping[str, Any]] = {}
    if parsed_model and parsed_model.get("tendons"):
        for t_p in parsed_model["tendons"]:
            parsed_by_id[str(t_p["tendon_id"])] = t_p
    max_parsed_n = max(
        (int(m.group(1)) for tid_k in parsed_by_id if (m := re.match(r"^N(\d+)$", tid_k))),
        default=0,
    )
    n_tendons = max(int(n_tendons), max_parsed_n)

    # Default mid-span & beam-end coordinates
    if n_tendons == 1:
        def_mid: list[tuple[str, float, float]] = [("N1", 0.0, 100.0)]
        def_end_ys = [round(0.35 * girder_height_mm, 1)]
    elif n_tendons == 2:
        def_mid = [("N1", -bulb_dx, 100.0), ("N2", bulb_dx, 100.0)]
        def_end_ys = [240.0, 540.0]
    else:
        def_mid = [("N1", -bulb_dx, 100.0), ("N2", bulb_dx, 100.0), ("N3", 0.0, 100.0)]
        for extra_idx in range(4, n_tendons + 1):
            def_mid.append((f"N{extra_idx}", 0.0, 100.0 + (extra_idx - 3) * 115.0))
        avail_end = girder_height_mm - 420.0
        step_e = 300.0 if (n_tendons - 1) * 300.0 <= avail_end else math.floor((avail_end / (n_tendons - 1)) / 10.0) * 10.0
        def_end_ys = [240.0 + i * step_e for i in range(n_tendons)]

    key_stations_mm = sorted(
        {
            0.0,
            brg_off,
            round(brg_off + 1200.0, 1),
            round(brg_off + 2200.0, 1),
            2980.0,
            round(brg_off + 0.125 * calc_span_mm, 1),
            round(brg_off + 0.250 * calc_span_mm, 1),
            round(brg_off + 0.375 * calc_span_mm, 1),
            half_l,
        }
    )

    tendon_profiles: list[dict[str, Any]] = []
    for idx in range(n_tendons):
        tid, mid_z_def, mid_y_def = def_mid[idx]
        end_y_def = def_end_ys[idx]
        end_z_def = 0.0  # Default centered at beam end; N1/N2 transition smoothly in bulb/thickened zone

        # Default confirmed piecewise tangent circular-arc vertical & plan control vertices (symmetric about half_l)
        bend_x_mm = round(brg_off + (1900.0 if idx < 2 else 3200.0) + idx * 450.0, 1)
        r_vert_mm = round(10000.0 + idx * 1500.0, 1)
        vert_pts: list[tuple[float, float, float]] = [
            (0.0, end_y_def, 0.0),
            (bend_x_mm, mid_y_def, r_vert_mm),
            (beam_length_mm - bend_x_mm, mid_y_def, r_vert_mm),
            (beam_length_mm, end_y_def, 0.0),
        ]
        if abs(mid_z_def) > 1e-6:
            # Transition z from 0 at beam end to mid_z_def inside bottom bulb [x >= 2400 mm where y <= 220 mm]!
            # Even better: keep z = mid_z_def at beam end (inside 560mm thickened end web: |130|+35+30 = 195 <= 280!)
            # OR transition within [0 .. 1600 mm] while inside the 560mm thickened web!
            end_z_def = mid_z_def if idx < 2 else 0.0
            plan_pts: list[tuple[float, float, float]] = [
                (0.0, end_z_def, 0.0),
                (1500.0, mid_z_def, 8000.0),
                (beam_length_mm - 1500.0, mid_z_def, 8000.0),
                (beam_length_mm, end_z_def, 0.0),
            ]
        else:
            plan_pts = [(0.0, 0.0, 0.0), (beam_length_mm, 0.0, 0.0)]

        override = parsed_by_id.get(tid)
        use_stations_3d: list[Mapping[str, Any]] = []
        if override:
            if override.get("stations_3d"):
                use_stations_3d = list(override["stations_3d"])
                # Mirror symmetric half-span stations if user only supplied [0 .. half_l]
                max_sx = max(float(s["x_mm"]) for s in use_stations_3d)
                if max_sx <= half_l + 1.0 and len(use_stations_3d) >= 2:
                    mirrored = [
                        {
                            "x_mm": round(beam_length_mm - float(s["x_mm"]), 3),
                            "y_mm": float(s.get("y_mm", mid_y_def)),
                            "z_mm": float(s.get("z_mm", 0.0)),
                        }
                        for s in reversed(use_stations_3d)
                        if float(s["x_mm"]) < half_l - 1.0
                    ]
                    use_stations_3d = use_stations_3d + mirrored
            else:
                if override.get("vertical_points"):
                    vp_raw = [(float(p["x_mm"]), float(p["y_mm"]), float(p.get("r_mm", 0.0))) for p in override["vertical_points"]]
                    if vp_raw[-1][0] <= half_l + 1.0:
                        vp_mir = [(beam_length_mm - px, py, pr) for px, py, pr in reversed(vp_raw) if px < half_l - 1.0]
                        vp_raw = vp_raw + vp_mir
                    vert_pts = vp_raw
                    r_vert_mm = max((p[2] for p in vert_pts), default=r_vert_mm)
                elif "end_y_mm" in override or "mid_y_mm" in override:
                    ey_o = float(override.get("end_y_mm", end_y_def))
                    my_o = float(override.get("mid_y_mm", mid_y_def))
                    bx_o = float(override.get("bend_x_mm", bend_x_mm))
                    rv_o = float(override.get("bend_radius_mm", r_vert_mm))
                    r_vert_mm = rv_o
                    vert_pts = [(0.0, ey_o, 0.0), (bx_o, my_o, rv_o), (beam_length_mm - bx_o, my_o, rv_o), (beam_length_mm, ey_o, 0.0)]

                if override.get("plan_points"):
                    pp_raw = [(float(p["x_mm"]), float(p["z_mm"]), float(p.get("r_mm", 0.0))) for p in override["plan_points"]]
                    if pp_raw[-1][0] <= half_l + 1.0:
                        pp_mir = [(beam_length_mm - px, pz, pr) for px, pz, pr in reversed(pp_raw) if px < half_l - 1.0]
                        pp_raw = pp_raw + pp_mir
                    plan_pts = pp_raw
                elif "end_z_mm" in override or "mid_z_mm" in override:
                    ez_o = float(override.get("end_z_mm", end_z_def))
                    mz_o = float(override.get("mid_z_mm", mid_z_def))
                    px_o = float(override.get("plan_bend_end_x_mm", 1500.0))
                    rp_o = float(override.get("plan_bend_radius_mm", 8000.0))
                    plan_pts = [(0.0, ez_o, 0.0), (px_o, mz_o, rp_o), (beam_length_mm - px_o, mz_o, rp_o), (beam_length_mm, ez_o, 0.0)]

        def _eval_3d(
            xq: float,
            _st: tuple[Mapping[str, Any], ...] = tuple(use_stations_3d),
            _vp: tuple[tuple[float, float, float], ...] = tuple(vert_pts),
            _pp: tuple[tuple[float, float, float], ...] = tuple(plan_pts),
        ) -> tuple[float, float, float, float]:
            if _st:
                return _interp_stations_3d(_st, xq)
            yv, dy = _eval_piecewise_rounded_polyline(_vp, xq)
            zv, dz = _eval_piecewise_rounded_polyline(_pp, xq)
            return yv, dy, zv, dz

        # Integrate exact 3D corrugated duct net length along [0 .. beam_length_mm] (400 fine segments)
        n_seg = 400
        dx_seg = beam_length_mm / n_seg
        arc_len_mm = 0.0
        prev_x = 0.0
        prev_y, dy0, prev_z, dz0 = _eval_3d(0.0)
        end_theta_vert_deg = round(abs(math.degrees(math.atan(dy0))), 2)
        end_theta_plan_deg = round(abs(math.degrees(math.atan(dz0))), 2)

        dense_samples: list[dict[str, float]] = [{"x_mm": 0.0, "y_mm": round(prev_y, 2), "z_mm": round(prev_z, 2)}]
        for s_i in range(1, n_seg + 1):
            cx = s_i * dx_seg
            cy, _, cz, _ = _eval_3d(cx)
            arc_len_mm += math.sqrt((cx - prev_x) ** 2 + (cy - prev_y) ** 2 + (cz - prev_z) ** 2)
            if s_i % 8 == 0 or s_i == n_seg:
                dense_samples.append({"x_mm": round(cx, 1), "y_mm": round(cy, 2), "z_mm": round(cz, 2)})
            prev_x, prev_y, prev_z = cx, cy, cz

        station_table: list[dict[str, float]] = []
        for kx in key_stations_mm:
            ky, kdy, kz, kdz = _eval_3d(kx)
            station_table.append(
                {
                    "x_mm": round(kx, 1),
                    "y_mm": round(ky, 1),
                    "z_mm": round(kz, 1),
                    "theta_vert_deg": round(abs(math.degrees(math.atan(kdy))), 2),
                    "theta_plan_deg": round(abs(math.degrees(math.atan(kdz))), 2),
                }
            )

        y_end, _, z_end, _ = _eval_3d(0.0)
        y_mid, _, z_mid, _ = _eval_3d(half_l)
        duct_net_len_m = round(arc_len_mm / 1000.0, 3)
        cutting_len_m = round((arc_len_mm + 2.0 * anchorage_working_length_mm) / 1000.0, 3)

        tendon_profiles.append(
            {
                "tendon_id": tid,
                "strands": strands_per_tendon,
                "duct_diameter_mm": float((override or {}).get("duct_diameter_mm", duct_diameter_mm)),
                "end_y_mm": round(y_end, 1),
                "end_z_mm": round(z_end, 1),
                "mid_y_mm": round(y_mid, 1),
                "mid_z_mm": round(z_mid, 1),
                "vertical_bend_radius_mm": r_vert_mm,
                "end_angle_vert_deg": end_theta_vert_deg,
                "end_angle_plan_deg": end_theta_plan_deg,
                "duct_net_length_m": duct_net_len_m,
                "strand_cutting_length_m": cutting_len_m,
                "anchorage_working_length_m": round(2.0 * anchorage_working_length_mm / 1000.0, 3),
                "station_table": station_table,
                "half_span_curve_mm": [s for s in dense_samples if s["x_mm"] <= half_l + 1e-3],
                "_eval_fn": _eval_3d,
            }
        )

    duct_per_girder_m = round(sum(t["duct_net_length_m"] for t in tendon_profiles), 2)
    return {
        "source": (parsed_model or {}).get("source_name", "parametric_confirmed_3d_curve"),
        "beam_length_mm": beam_length_mm,
        "calc_span_mm": calc_span_mm,
        "duct_diameter_mm": duct_diameter_mm,
        "duct_net_length_per_girder_m": duct_per_girder_m,
        "key_stations_mm": key_stations_mm,
        "tendons": tendon_profiles,
    }


def section_half_width_mm_at(
    x_from_beam_end_mm: float,
    y_mm: float,
    *,
    beam_length_mm: float = 24960.0,
    calc_span_mm: float = 24000.0,
    girder_height_mm: float = 1750.0,
    flange_width_mm: float = 1550.0,
    flange_tip_thick_mm: float = 200.0,
    haunch_width_mm: float = 210.0,
    haunch_height_mm: float = 70.0,
    web_thick_mm: float = 200.0,
    end_web_thick_mm: float = 560.0,
    bulb_width_mm: float = 560.0,
    bulb_height_mm: float = 200.0,
    bulb_taper_height_mm: float = 200.0,
    end_thick_inside_support_mm: float = 1500.0,
    web_taper_length_mm: float = 1000.0,
) -> float:
    """Return exact concrete half-width `b(x, y)/2` (mm) of the variable-web T-girder at `(x, y)`."""
    if y_mm < 0.0 or y_mm > girder_height_mm:
        return -1.0

    brg_off = 0.5 * (beam_length_mm - calc_span_mm)
    d_end = min(x_from_beam_end_mm, beam_length_mm - x_from_beam_end_mm)
    x_thick_end = brg_off + end_thick_inside_support_mm  # e.g. 480 + 1200 = 1680 mm
    x_taper_end = x_thick_end + web_taper_length_mm      # e.g. 1680 + 1000 = 2680 mm

    if d_end <= x_thick_end:
        cur_web_t = end_web_thick_mm
    elif d_end < x_taper_end:
        frac = (d_end - x_thick_end) / max(1.0, web_taper_length_mm)
        cur_web_t = end_web_thick_mm + frac * (web_thick_mm - end_web_thick_mm)
    else:
        cur_web_t = web_thick_mm

    hw_web = 0.5 * cur_web_t
    hw_bulb = max(hw_web, 0.5 * bulb_width_mm)
    hw_flange = 0.5 * flange_width_mm
    y_fl_bot = girder_height_mm - flange_tip_thick_mm
    y_haunch_bot = y_fl_bot - haunch_height_mm

    if y_mm >= y_fl_bot:
        return hw_flange
    if y_mm >= y_haunch_bot:
        t_h = (y_mm - y_haunch_bot) / max(1.0, haunch_height_mm)
        return hw_web + t_h * haunch_width_mm
    if y_mm <= bulb_height_mm:
        return hw_bulb
    if y_mm <= bulb_height_mm + bulb_taper_height_mm:
        t_b = (y_mm - bulb_height_mm) / max(1.0, bulb_taper_height_mm)
        return hw_bulb + t_b * (hw_web - hw_bulb)
    return hw_web


def audit_tendon_3d_section_clearance(
    tendon_geom: Mapping[str, Any] | str | Path,
    *,
    girder_height_mm: float = 1750.0,
    flange_width_mm: float = 1550.0,
    web_thick_mm: float = 200.0,
    end_web_thick_mm: float = 560.0,
    bulb_width_mm: float = 560.0,
    bulb_height_mm: float = 200.0,
    min_cover_mm: float = 30.0,
    step_mm: float = 20.0,
    section_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Station-by-station 3D audit of tendon centroid, corrugated duct outer edge, and T-girder outline."""
    if isinstance(tendon_geom, (str, Path)) or (
        isinstance(tendon_geom, Mapping) and "beam_length_mm" not in tendon_geom
    ):
        parsed_m = parse_drbridge_tendon_and_section_model(tendon_geom)
        if section_overrides is None and parsed_m is not None:
            section_overrides = parsed_m.get("section_overrides")
        n_t = max(4, len((parsed_m or {}).get("tendons") or []))
        tendon_geom = build_bridge_tendon_3d_geometry(
            n_tendons=n_t,
            strands_per_tendon=7,
            girder_height_mm=girder_height_mm,
            bulb_width_mm=bulb_width_mm,
            parsed_model=parsed_m,
        )
    beam_len = float(tendon_geom["beam_length_mm"])
    calc_span = float(tendon_geom["calc_span_mm"])
    brg_off = 0.5 * (beam_len - calc_span)
    sec_ov = dict(section_overrides or {})
    end_thick_in = float(sec_ov.get("end_thick_inside_support_mm", 1500.0))
    taper_len = float(sec_ov.get("web_taper_length_mm", 1000.0))
    if "end_thick_zone_end_mm" in sec_ov:
        end_thick_in = max(0.0, float(sec_ov["end_thick_zone_end_mm"]) - brg_off)
    if "taper_zone_end_mm" in sec_ov:
        taper_len = max(100.0, float(sec_ov["taper_zone_end_mm"]) - (brg_off + end_thick_in))

    n_steps = max(20, int(round(beam_len / step_mm)))
    sample_xs = sorted(
        {round(i * (beam_len / n_steps), 2) for i in range(n_steps + 1)}
        | {0.0, brg_off, brg_off + end_thick_in, brg_off + end_thick_in + taper_len, 2980.0, 0.5 * beam_len}
    )

    violations: list[dict[str, Any]] = []
    per_tendon_summary: list[dict[str, Any]] = []

    for t_prof in tendon_geom["tendons"]:
        tid = str(t_prof["tendon_id"])
        r_duct = 0.5 * float(t_prof.get("duct_diameter_mm", 70.0))
        eval_fn = t_prof.get("_eval_fn")

        worst_centroid_margin = float("inf")
        worst_duct_cover = float("inf")
        worst_station_x = 0.0
        worst_y = 0.0
        worst_z = 0.0
        worst_hw = 0.0
        first_centroid_out: dict[str, Any] | None = None
        first_cover_short: dict[str, Any] | None = None

        for x_s in sample_xs:
            if callable(eval_fn):
                yv, _, zv, _ = eval_fn(x_s)
            else:
                yv, _, zv, _ = _interp_stations_3d(t_prof.get("station_table") or [], x_s)

            hw = section_half_width_mm_at(
                x_s,
                yv,
                beam_length_mm=beam_len,
                calc_span_mm=calc_span,
                girder_height_mm=girder_height_mm,
                flange_width_mm=flange_width_mm,
                web_thick_mm=web_thick_mm,
                end_web_thick_mm=end_web_thick_mm,
                bulb_width_mm=bulb_width_mm,
                bulb_height_mm=bulb_height_mm,
                end_thick_inside_support_mm=end_thick_in,
                web_taper_length_mm=taper_len,
            )

            # Vertical margin to [0, girder_height_mm] & lateral margin to [-hw, +hw]
            vert_margin = min(yv, girder_height_mm - yv)
            lat_margin = (hw - abs(zv)) if hw >= 0.0 else vert_margin
            centroid_margin = min(vert_margin, lat_margin)
            duct_cover = centroid_margin - r_duct

            if centroid_margin < worst_centroid_margin:
                worst_centroid_margin = centroid_margin
                worst_duct_cover = duct_cover
                worst_station_x = x_s
                worst_y = yv
                worst_z = zv
                worst_hw = max(0.0, hw)

            if centroid_margin < -1e-6:
                overrun_mm = round(-centroid_margin, 2)
                rec_out = {
                    "code": "TENDON_CENTROID_OUT_OF_SECTION",
                    "tendon_id": tid,
                    "x_from_beam_end_mm": round(x_s, 1),
                    "y_mm": round(yv, 2),
                    "z_mm": round(zv, 2),
                    "section_half_width_mm": round(max(0.0, hw), 2),
                    "centroid_overrun_mm": overrun_mm,
                    "duct_outer_overrun_mm": round(overrun_mm + r_duct, 2),
                    "message": (
                        f"钢束 {tid} 在距梁端 x={x_s:g} mm 处束心越出混凝土截面边界 {overrun_mm:g} mm "
                        f"（|z|={abs(zv):.1f} mm, y={yv:.1f} mm, 截面半宽={max(0.0, hw):.1f} mm；孔道外缘越界 {overrun_mm + r_duct:.1f} mm）"
                    ),
                }
                if first_centroid_out is None or overrun_mm > first_centroid_out["centroid_overrun_mm"]:
                    first_centroid_out = rec_out
            elif duct_cover < min_cover_mm - 1e-6:
                short_mm = round(min_cover_mm - duct_cover, 2)
                rec_cov = {
                    "code": "TENDON_DUCT_CLEARANCE_VIOLATION",
                    "tendon_id": tid,
                    "x_from_beam_end_mm": round(x_s, 1),
                    "y_mm": round(yv, 2),
                    "z_mm": round(zv, 2),
                    "section_half_width_mm": round(max(0.0, hw), 2),
                    "duct_cover_mm": round(duct_cover, 2),
                    "min_required_cover_mm": min_cover_mm,
                    "shortfall_mm": short_mm,
                    "message": (
                        f"钢束 {tid} 在距梁端 x={x_s:g} mm 处波纹管外缘净保护层仅 {duct_cover:.1f} mm < {min_cover_mm:g} mm "
                        f"（不足 {short_mm:g} mm；|z|={abs(zv):.1f} mm, y={yv:.1f} mm, 截面半宽={max(0.0, hw):.1f} mm）"
                    ),
                }
                if first_cover_short is None or short_mm > first_cover_short["shortfall_mm"]:
                    first_cover_short = rec_cov

        if first_centroid_out is not None:
            violations.append(first_centroid_out)
        elif first_cover_short is not None:
            violations.append(first_cover_short)

        per_tendon_summary.append(
            {
                "tendon_id": tid,
                "min_centroid_margin_mm": round(worst_centroid_margin, 2),
                "min_duct_cover_mm": round(worst_duct_cover, 2),
                "governing_x_mm": round(worst_station_x, 1),
                "governing_y_mm": round(worst_y, 1),
                "governing_z_mm": round(worst_z, 1),
                "governing_half_width_mm": round(worst_hw, 1),
                "passed": (first_centroid_out is None and first_cover_short is None),
            }
        )

    # Strip internal callable before JSON serialization
    for t_prof in tendon_geom["tendons"]:
        t_prof.pop("_eval_fn", None)

    return {
        "status": "passed" if not violations else "failed",
        "passed": len(violations) == 0,
        "min_required_cover_mm": min_cover_mm,
        "station_count_checked": len(sample_xs),
        "violation_count": len(violations),
        "violations": violations,
        "issues": violations,
        "per_tendon_summary": per_tendon_summary,
    }
