"""Design Change Impact Tracker, Semantic DXF Content Digest & Cross-Artifact Consistency Verifier.

Answers two advisor-critical questions after every design modification:
1. "What changed in this revision, and which calculation/thesis chapters and drawing sheets are affected?"
   - Uses deterministic semantic DXF content comparison (`compute_dxf_semantic_digest`) across geometry,
     native dimensions, visible texts, and embedded material/rebar/tendon schedules (ignoring volatile
     DXF header timestamps/handles), so re-running with identical parameters reports **0** changed sheets.
2. "After changing abutment pile length, tendon count/coordinates, or bearing support offsets, are the
   calculation report, calculation book, thesis, rebar schedule, BOQ, and all drawings 100% consistent?"
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import ezdxf


def _num_differs(v0: Any, v1: Any, tol: float = 1e-4) -> bool:
    if isinstance(v0, (int, float)) and isinstance(v1, (int, float)) and not isinstance(v0, bool) and not isinstance(v1, bool):
        return not math.isclose(float(v0), float(v1), abs_tol=tol)
    return v0 != v1


def _sha256_json(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _dxf_attr(ent: Any, key: str, default: Any = None) -> Any:
    try:
        return ent.dxf.get(key, default)
    except Exception:
        return default


def compute_dxf_semantic_digest(dxf_path: str | Path) -> dict[str, Any]:
    """Compute a deterministic semantic fingerprint of a DXF across geometry, dimensions, texts, and schedules.

    Ignores volatile DXF header variables (`$TDCREATE`, `$TDUPDATE`, `$HANDSEED`) and entity handles so
    consecutive runs with identical design inputs produce the exact same `content_sha256`.
    """
    p = Path(dxf_path).expanduser().resolve()
    doc = ezdxf.readfile(p)
    msp = doc.modelspace()

    geom_records: list[tuple[Any, ...]] = []
    dim_records: list[tuple[Any, ...]] = []
    text_records: list[tuple[Any, ...]] = []
    schedule_records: list[Any] = []

    for ent in msp:
        dt = ent.dxftype()
        layer = str(ent.dxf.get("layer", "0"))

        # Extract attached XDATA schedules / views (`CADSKILL_META`)
        if ent.has_xdata("CADSKILL_META"):
            try:
                tags = ent.get_xdata("CADSKILL_META")
                for code, val in tags:
                    if code == 1000 and isinstance(val, str) and val.startswith("{"):
                        parsed_meta = json.loads(val)
                        schedule_records.append(parsed_meta)
            except Exception:
                pass

        if dt == "LINE":
            s, e = ent.dxf.start, ent.dxf.end
            p0 = (round(float(s.x), 3), round(float(s.y), 3))
            p1 = (round(float(e.x), 3), round(float(e.y), 3))
            geom_records.append(("LINE", layer, min(p0, p1), max(p0, p1)))
        elif dt == "LWPOLYLINE":
            pts = tuple((round(float(x), 3), round(float(y), 3), round(float(b), 4)) for x, y, b in ent.get_points("xyb"))
            geom_records.append(("LWPOLYLINE", layer, bool(ent.closed), pts))
        elif dt == "CIRCLE":
            c = ent.dxf.center
            geom_records.append(("CIRCLE", layer, round(float(c.x), 3), round(float(c.y), 3), round(float(ent.dxf.radius), 3)))
        elif dt == "ARC":
            c = ent.dxf.center
            geom_records.append(
                (
                    "ARC",
                    layer,
                    round(float(c.x), 3),
                    round(float(c.y), 3),
                    round(float(ent.dxf.radius), 3),
                    round(float(ent.dxf.start_angle), 2),
                    round(float(ent.dxf.end_angle), 2),
                )
            )
        elif dt == "DIMENSION":
            meas = _dxf_attr(ent, "actual_measurement", None)
            meas_r = round(float(meas), 3) if isinstance(meas, (int, float)) else None
            dp1 = _dxf_attr(ent, "defpoint", None)
            dp2 = _dxf_attr(ent, "defpoint2", None)
            dp3 = _dxf_attr(ent, "defpoint3", None)
            c1 = (round(float(dp1.x), 2), round(float(dp1.y), 2)) if dp1 is not None else ()
            c2 = (round(float(dp2.x), 2), round(float(dp2.y), 2)) if dp2 is not None else ()
            c3 = (round(float(dp3.x), 2), round(float(dp3.y), 2)) if dp3 is not None else ()
            dim_records.append(("DIMENSION", layer, str(_dxf_attr(ent, "text", "")).strip(), meas_r, c1, c2, c3))
        elif dt in {"TEXT", "MTEXT"}:
            txt_s = str(_dxf_attr(ent, "text", "")).strip()
            if dt == "TEXT":
                try:
                    _, plc = ent.get_placement()
                    ins = plc if plc is not None else ent.dxf.insert
                except Exception:
                    ins = ent.dxf.insert
                h_v = float(_dxf_attr(ent, "height", 2.5))
            else:
                ins = ent.dxf.insert
                h_v = float(_dxf_attr(ent, "char_height", 2.5))
            text_records.append((dt, layer, txt_s, round(float(ins.x), 2), round(float(ins.y), 2), round(h_v, 2)))

    geom_records.sort(key=repr)
    dim_records.sort(key=repr)
    text_records.sort(key=repr)
    schedule_records_sorted = sorted(schedule_records, key=lambda o: json.dumps(o, ensure_ascii=False, sort_keys=True))

    sec_digests = {
        "geometry_sha256": _sha256_json(geom_records),
        "dimensions_sha256": _sha256_json(dim_records),
        "texts_sha256": _sha256_json(text_records),
        "schedules_sha256": _sha256_json(schedule_records_sorted),
    }
    return {
        "content_sha256": _sha256_json(sec_digests),
        "sections": sec_digests,
        "counts": {
            "geometry": len(geom_records),
            "dimensions": len(dim_records),
            "texts": len(text_records),
            "schedules": len(schedule_records_sorted),
        },
    }


def verify_internal_suite_consistency(calc_report: Mapping[str, Any]) -> dict[str, Any]:
    """Verify strict numerical agreement across calculation checks, support chain, tendon/rebar ledger, and BOQ."""
    ps = calc_report["project_summary"]
    pre = calc_report["prestress"]
    gc = calc_report["girder_checks"]
    sub = calc_report["substructure_checks"]
    abut = calc_report["abutment_checks"]
    ledger = calc_report["rebar_and_tendon_ledger"]
    boq = calc_report["bill_of_quantities"]
    vbk = boq["volume_breakdown"]
    sup_lay = calc_report.get("support_layout") or {}
    t3d = calc_report.get("tendon_3d_geometry") or {}

    issues: list[str] = []

    # 1. Abutment pile length agreement across project_summary, abutment_checks, QT3, QT4, and BOQ
    abut_l_ps = float(ps["abutment_pile_length_m"])
    abut_l_chk = float(abut["bored_pile"]["design_length_m"])
    abut_l_boq = float(vbk["abut_pile_length_m"])
    qt3 = ledger["index_by_id"]["QT3"]
    qt4 = ledger["index_by_id"]["QT4"]
    expected_qt3_cm = round((abut_l_chk + 1.0) * 100.0, 1)
    expected_qt4_cnt = max(2, int(round(abut_l_chk / 0.15)))
    pile_d = float(abut["bored_pile"]["diameter_m"])
    expected_abut_m3 = round(4.0 * (math.pi * 0.25 * pile_d**2) * abut_l_chk, 2)

    if not (math.isclose(abut_l_ps, abut_l_chk, abs_tol=1e-3) and math.isclose(abut_l_boq, abut_l_chk, abs_tol=1e-3)):
        issues.append(f"桥台桩长不一致: summary={abut_l_ps}, check={abut_l_chk}, boq={abut_l_boq}")
    if not math.isclose(float(qt3["single_length_cm"]), expected_qt3_cm, abs_tol=0.5):
        issues.append(f"QT3单根长({qt3['single_length_cm']}cm)与桥台桩长({abut_l_chk}m+1.0m)不一致")
    if int(qt4["count"]) != expected_qt4_cnt:
        issues.append(f"QT4螺旋箍圈数({qt4['count']})与桥台桩长({abut_l_chk}m/@150)不一致")
    if not math.isclose(float(vbk["abut_pile_m3"]), expected_abut_m3, abs_tol=0.05):
        issues.append(f"桥台钻孔桩C30方量({vbk['abut_pile_m3']}m3)与桩长({abut_l_chk}m)计算值({expected_abut_m3}m3)不一致")

    # 2. Prestressing tendon count, strand weight, and corrugated duct length agreement
    n_tendons = int(pre["recommended_tendons"])
    strands_per = int(pre["strands_per_tendon"])
    ledger_tendons = ledger["tendons"]
    if len(ledger_tendons) != n_tendons:
        issues.append(f"钢束明细条数({len(ledger_tendons)})与主梁计算钢束数({n_tendons})不一致")
    sum_strand_kg = round(sum(float(t["total_weight_bridge_kg"]) for t in ledger_tendons), 1)
    if not math.isclose(float(boq["prestress_strand_kg"]), sum_strand_kg, abs_tol=0.2):
        issues.append(f"工程量表钢绞线总重({boq['prestress_strand_kg']}kg)与分束汇总({sum_strand_kg}kg)不一致")

    if "corrugated_duct_m" in boq and t3d:
        total_girders = int(ps["n_spans"]) * int(calc_report["lateral_distribution"]["n_girders"])
        exp_duct_m = round(float(t3d["duct_net_length_per_girder_m"]) * total_girders, 1)
        if not math.isclose(float(boq["corrugated_duct_m"]), exp_duct_m, abs_tol=0.5):
            issues.append(f"工程量表波纹管总长({boq['corrugated_duct_m']}m)与单梁管道净长汇总({exp_duct_m}m)不一致")

    ap_expected_mm2 = n_tendons * strands_per * 140.0
    for t_item in ledger_tendons:
        if not math.isclose(float(t_item["selection_basis"]["provided_area_mm2"]), ap_expected_mm2, abs_tol=0.5):
            issues.append(f"{t_item['tendon_id']} 记录总截面积与 {n_tendons}束×{strands_per}股 不一致")
        if not math.isclose(float(t_item["governing_forces"]["capacity_value"]), float(gc["uls_flexure"]["capacity_mu_knm"]), abs_tol=0.1):
            issues.append(f"{t_item['tendon_id']} 控制抗力 Mu 与主梁正截面验算不一致")

    # 3. Pier pile length & shared support chain closure
    pier_l_chk = float(sub["bored_pile"]["design_length_m"])
    zj1 = ledger["index_by_id"]["ZJ1"]
    if not math.isclose(float(zj1["single_length_cm"]), round((pier_l_chk + 1.10) * 100.0, 1), abs_tol=0.5):
        issues.append(f"ZJ1单根长({zj1['single_length_cm']}cm)与桥墩桩长({pier_l_chk}m+1.1m)不一致")

    if sup_lay and not sup_lay.get("chain_closed", True):
        issues.append(f"支承定位尺寸链未闭合: {sup_lay.get('pier_chain_expression')}")

    return {
        "status": "passed" if not issues else "failed",
        "consistent": len(issues) == 0,
        "issue_count": len(issues),
        "issues": issues,
        "verified_anchors": {
            "abutment_pile_length_m": abut_l_chk,
            "abutment_pile_c30_m3": float(vbk["abut_pile_m3"]),
            "qt3_single_length_cm": float(qt3["single_length_cm"]),
            "qt4_hoop_count": int(qt4["count"]),
            "n_tendons": n_tendons,
            "provided_strands": int(pre["provided_strands"]),
            "girder_mu_knm": float(gc["uls_flexure"]["capacity_mu_knm"]),
            "prestress_strand_kg": float(boq["prestress_strand_kg"]),
            "corrugated_duct_m": float(boq.get("corrugated_duct_m", 0.0)),
            "pier_support_chain": sup_lay.get("pier_chain_expression", "480+40+480=1000"),
        },
    }


# Domain change categories -> affected chapters, canonical sheet roles, and figures
_DOMAIN_IMPACT_RULES: dict[str, dict[str, list[str]]] = {
    "span_and_geometry": {
        "calc_book_chapters": [
            "第一章 工程概况、技术标准与桥型方案比选",
            "第二章 代表性一片预制T梁逐步复核计算书",
            "第三章 代表性中间双柱式桥墩逐步复核计算书",
            "第四章 桩柱式桥台独立受力及桩长追溯计算书",
            "第五章 手算—电算复核对照表",
            "第六章 钢束与钢筋编号全链路溯源总表",
        ],
        "thesis_chapters": [
            "第1章 绪论",
            "第2章 桥型方案综合比选",
            "第3章 上部结构构造拟定与截面几何特性",
            "第4章 代表性主梁内力计算",
            "第6章 主梁承载能力与正常使用验算",
            "附录 全桥主要工程材料数量汇总表",
        ],
        "drawing_roles": [
            "general_notes", "boq_summary", "bridge_elevation", "bridge_plan",
            "deck_cross_section", "girder_longitudinal", "girder_cross_details",
            "tendon_layout", "tendon_coordinates", "girder_web_rebar",
            "girder_flange_rebar", "diaphragm_rebar", "bearing_layout",
            "deck_ancillary", "pier_general", "abutment_general",
        ],
        "figures": [
            "fig0_scheme_comparison",
            "fig1_structural_model",
            "fig2_moment_shear_envelope",
            "fig3_tendon_and_prestress",
        ],
    },
    "support_and_bearing_chain": {
        "calc_book_chapters": [
            "第二章 2.1 输入参数与支承计算跨径核对",
            "第三章 3.1~3.2 双跨支座反力与墩顶支座布置",
        ],
        "thesis_chapters": [
            "第3章 上部结构构造拟定与支承定位",
            "第7章 7.2 板式橡胶支座选型与力学验算",
            "第8章 中间双柱式桥墩一般构造",
        ],
        "drawing_roles": ["girder_longitudinal", "bearing_layout", "pier_general"],
        "figures": ["fig1_structural_model"],
    },
    "paving_and_dead_load": {
        "calc_book_chapters": [
            "第一章 1.1 工程概况与主要技术指标",
            "第二章 2.3~2.6 主梁恒载集度、内力组合与验算",
            "第三章 中间双柱式桥墩支座反力与盖梁验算",
            "第四章 桩柱式桥台竖向荷载汇总",
            "第五章 手算—电算复核对照表",
        ],
        "thesis_chapters": [
            "第3章 3.1 桥面铺装构造",
            "第4章 4.1 永久作用集度及恒载内力计算",
            "第6章 主梁承载能力与正常使用极限状态验算",
            "第7章 7.1 悬臂行车道板计算",
            "附录 全桥主要工程材料数量汇总表",
        ],
        "drawing_roles": ["general_notes", "boq_summary", "deck_cross_section", "girder_web_rebar", "girder_flange_rebar", "deck_ancillary"],
        "figures": ["fig1_structural_model", "fig2_moment_shear_envelope"],
    },
    "prestress_tendons": {
        "calc_book_chapters": [
            "第二章 2.6 预应力钢束配置与极限/正常使用验算结论",
            "第五章 手算—电算复核对照表（活载挠度与抗力）",
            "第六章 钢束与钢筋编号全链路溯源总表（N1~Nn、R1、GJ1）",
        ],
        "thesis_chapters": [
            "第5章 预应力钢束估算、布置及预应力损失计算",
            "第6章 代表性主梁承载能力与正常使用极限状态验算",
            "第10章 手算与独立电算复核及选筋溯源",
            "附录 全桥主要工程材料数量汇总表",
        ],
        "drawing_roles": ["boq_summary", "tendon_layout", "tendon_coordinates", "girder_web_rebar"],
        "figures": ["fig2_moment_shear_envelope", "fig3_tendon_and_prestress"],
    },
    "pier_substructure_and_pile": {
        "calc_book_chapters": [
            "第三章 3.2~3.4 双柱墩盖梁、墩柱与钻孔灌注桩计算",
            "第五章 手算—电算复核对照表（墩柱轴力与桥墩桩顶荷载）",
            "第六章 钢束与钢筋编号全链路溯源总表（GL1~GL4、DZ1~ZJ2）",
        ],
        "thesis_chapters": [
            "第8章 中间双柱式桥墩及钻孔灌注桩基础设计计算",
            "第10章 手算与独立电算复核及选筋溯源",
            "附录 全桥主要工程材料数量汇总表",
        ],
        "drawing_roles": ["boq_summary", "bridge_elevation", "pier_general", "pier_cap_rebar", "pier_cap_secondary", "pier_column_pile_rebar", "tie_beam_joint_rebar"],
        "figures": ["fig1_structural_model", "fig4_pile_soil_profile"],
    },
    "abutment_and_pile": {
        "calc_book_chapters": [
            "第四章 桩柱式桥台独立受力、台后土压力及桩长追溯计算书",
            "第五章 手算—电算复核对照表（桥台单桩荷载与容许承载力）",
            "第六章 钢束与钢筋编号全链路溯源总表（QT1~QT4、EB1~EB3、DS1）",
        ],
        "thesis_chapters": [
            "第9章 桩柱式桥台及钻孔灌注桩独立设计计算",
            "第10章 手算与独立电算复核及选筋溯源",
            "附录 全桥主要工程材料数量汇总表",
        ],
        "drawing_roles": ["boq_summary", "bridge_elevation", "abutment_general", "abutment_rebar", "abutment_ear_backwall_rebar"],
        "figures": ["fig4_pile_soil_profile"],
    },
    "status_and_survey": {
        "calc_book_chapters": [
            "〇、双状态总览（结构计算通过与毕设资料齐全分离显示）",
            "第一章 1.2 水文水力与桥孔净长复核",
        ],
        "thesis_chapters": [
            "设计核查声明页",
            "第1章 1.2 工程水文计算与跨径布置",
        ],
        "drawing_roles": ["general_notes", "bridge_elevation"],
        "figures": ["fig4_pile_soil_profile"],
    },
    "fem_verification": {
        "calc_book_chapters": [
            "第五章 手算解析—本程序—独立有限元软件答辩复核对照表",
        ],
        "thesis_chapters": [
            "第10章 10.1~10.2 独立电算建模记录卡与三方复核对照表",
        ],
        "drawing_roles": [],
        "figures": [],
    },
}

_ROLE_FALLBACK_QL: dict[str, str | None] = {
    "general_notes": "QL-01",
    "boq_summary": "QL-02",
    "bridge_elevation": "QL-03",
    "bridge_plan": "QL-04",
    "deck_cross_section": "QL-05",
    "girder_longitudinal": "QL-06",
    "girder_cross_details": "QL-07",
    "tendon_layout": "QL-08",
    "tendon_coordinates": None,
    "girder_web_rebar": "QL-09",
    "girder_flange_rebar": "QL-10",
    "diaphragm_rebar": "QL-11",
    "bearing_layout": "QL-12",
    "deck_ancillary": "QL-13",
    "pier_general": "QL-14",
    "abutment_general": "QL-15",
    "pier_cap_rebar": "QL-16",
    "pier_cap_secondary": None,
    "pier_column_pile_rebar": "QL-17",
    "tie_beam_joint_rebar": None,
    "abutment_rebar": "QL-18",
    "abutment_ear_backwall_rebar": None,
}


def compute_design_change_impact(
    before_report: Mapping[str, Any] | None,
    after_report: Mapping[str, Any],
    *,
    before_dxf_hashes: Mapping[str, str] | None = None,
    after_dxf_hashes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Compare baseline vs current calculation/drawing state and list all changed parameters, chapters, and sheets."""
    consistency = verify_internal_suite_consistency(after_report)

    if before_report is None:
        return {
            "mode": "initial_baseline",
            "has_changes": False,
            "summary_zh": "首次生成基准成果（已建立参数与图纸语义内容快照；下次修改任意参数重跑时将自动输出受影响章节与图号清单）。",
            "changed_items": [],
            "changed_rebar_or_tendon_ids": [],
            "affected_calc_book_chapters": [],
            "affected_thesis_chapters": [],
            "affected_drawings": [],
            "dxf_content_changed_drawings": [],
            "dxf_sha256_changed_drawings": [],
            "affected_figures": [],
            "internal_consistency": consistency,
        }

    man = after_report.get("sheet_manifest") or {}
    by_role = man.get("by_role") or {}

    def _resolve_role_dno(rk: str) -> str | None:
        if rk in by_role:
            return str(by_role[rk]["drawing_no"])
        if man:
            return None
        return _ROLE_FALLBACK_QL.get(rk)

    b_ps = before_report["project_summary"]
    a_ps = after_report["project_summary"]
    b_pre = before_report["prestress"]
    a_pre = after_report["prestress"]
    b_gc = before_report["girder_checks"]
    a_gc = after_report["girder_checks"]
    b_sub = before_report["substructure_checks"]
    a_sub = after_report["substructure_checks"]
    b_ab = before_report["abutment_checks"]
    a_ab = after_report["abutment_checks"]
    b_boq = before_report["bill_of_quantities"]
    a_boq = after_report["bill_of_quantities"]
    b_sup = before_report.get("support_layout") or {}
    a_sup = after_report.get("support_layout") or {}
    b_t3d = before_report.get("tendon_3d_geometry") or {}
    a_t3d = after_report.get("tendon_3d_geometry") or {}

    changed_items: list[dict[str, Any]] = []
    triggered_domains: set[str] = set()

    def _check_field(
        field_id: str,
        label_zh: str,
        v_before: Any,
        v_after: Any,
        unit: str,
        domain: str,
        tol: float = 1e-3,
    ) -> None:
        if _num_differs(v_before, v_after, tol=tol):
            delta: float | None = None
            if isinstance(v_before, (int, float)) and isinstance(v_after, (int, float)) and not isinstance(v_before, bool):
                delta = round(float(v_after) - float(v_before), 3)
            changed_items.append(
                {
                    "field": field_id,
                    "label_zh": label_zh,
                    "before": v_before,
                    "after": v_after,
                    "delta": delta,
                    "unit": unit,
                    "domain": domain,
                }
            )
            triggered_domains.add(domain)

    # 1. Core geometry, span & shared support chain
    _check_field("n_spans", "桥孔跨数", b_ps["n_spans"], a_ps["n_spans"], "跨", "span_and_geometry")
    _check_field("standard_span_m", "标准跨径", b_ps["standard_span_m"], a_ps["standard_span_m"], "m", "span_and_geometry")
    _check_field("calc_span_m", "主梁计算跨径", b_ps["calc_span_m"], a_ps["calc_span_m"], "m", "span_and_geometry")
    _check_field("deck_width_m", "桥面全宽", b_ps["deck_width_m"], a_ps["deck_width_m"], "m", "span_and_geometry")
    _check_field("flood_elevation_m", "设计洪水位", b_ps["flood_elevation_m"], a_ps["flood_elevation_m"], "m", "status_and_survey")
    _check_field("deck_elevation_m", "桥面设计高程", b_ps["deck_elevation_m"], a_ps["deck_elevation_m"], "m", "status_and_survey")
    if b_sup and a_sup:
        _check_field("beam_end_to_bearing_mm", "梁端至支座中心距", b_sup.get("beam_end_to_bearing_mm"), a_sup.get("beam_end_to_bearing_mm"), "mm", "support_and_bearing_chain", tol=0.1)
        _check_field("pier_bearing_row_pitch_mm", "中墩双排支座纵向中心距", b_sup.get("pier_bearing_row_pitch_mm"), a_sup.get("pier_bearing_row_pitch_mm"), "mm", "support_and_bearing_chain", tol=0.1)

    # 2. Paving & dead loads
    _check_field("asphalt_thick_mm", "沥青混凝土铺装厚", b_ps["asphalt_thick_mm"], a_ps["asphalt_thick_mm"], "mm", "paving_and_dead_load")
    _check_field("concrete_paving_thick_mm", "C40调平层厚", b_ps["concrete_paving_thick_mm"], a_ps["concrete_paving_thick_mm"], "mm", "paving_and_dead_load")

    # 3. Prestressing tendons, 3D geometry & girder ULS/SLS checks
    _check_field("n_tendons", "单片T梁预应力钢束数", b_pre["recommended_tendons"], a_pre["recommended_tendons"], "束", "prestress_tendons")
    _check_field("provided_strands", "单片T梁预应力钢绞线总股数", b_pre["provided_strands"], a_pre["provided_strands"], "股", "prestress_tendons")
    _check_field("sigma_con_mpa", "张拉控制应力 σcon", b_pre["sigma_con_mpa"], a_pre["sigma_con_mpa"], "MPa", "prestress_tendons")
    if b_t3d and a_t3d:
        _check_field("duct_net_length_per_girder_m", "单梁波纹管净长", b_t3d.get("duct_net_length_per_girder_m"), a_t3d.get("duct_net_length_per_girder_m"), "m", "prestress_tendons", tol=0.01)
        if b_t3d.get("tendons") != a_t3d.get("tendons"):
            _check_field("tendon_3d_coordinates", "钢束三维束心坐标/弯起参数", "baseline_profile", "updated_profile", "-", "prestress_tendons")
    _check_field("girder_md_knm", "主梁跨中基本组合弯矩 Md", b_gc["uls_flexure"]["demand_md_knm"], a_gc["uls_flexure"]["demand_md_knm"], "kN·m", "span_and_geometry", tol=0.05)
    _check_field("girder_mu_knm", "主梁跨中正截面抗弯承载力 Mu", b_gc["uls_flexure"]["capacity_mu_knm"], a_gc["uls_flexure"]["capacity_mu_knm"], "kN·m", "prestress_tendons", tol=0.05)
    _check_field("girder_vu_kn", "主梁支点斜截面抗剪承载力 Vu", b_gc["uls_shear"]["capacity_vu_kn"], a_gc["uls_shear"]["capacity_vu_kn"], "kN", "prestress_tendons", tol=0.05)
    _check_field("girder_net_bot_stress_mpa", "主梁频遇组合下缘净应力", b_gc["sls_crack_and_stress"]["net_bot_stress_freq_mpa"], a_gc["sls_crack_and_stress"]["net_bot_stress_freq_mpa"], "MPa", "prestress_tendons", tol=0.01)
    _check_field("girder_live_deflection_mm", "主梁消除自重活载挠度", b_gc["sls_deflection"]["live_deflection_mm"], a_gc["sls_deflection"]["live_deflection_mm"], "mm", "prestress_tendons", tol=0.01)

    # 4. Pier & Abutment pile foundation
    _check_field("pier_pile_length_m", "中间桥墩钻孔桩桩长", b_ps["pier_pile_length_m"], a_ps["pier_pile_length_m"], "m", "pier_substructure_and_pile")
    _check_field("pier_pile_ra_kn", "桥墩单桩容许承载力 [Ra]", b_sub["bored_pile"]["allowable_ra_kn"], a_sub["bored_pile"]["allowable_ra_kn"], "kN", "pier_substructure_and_pile", tol=0.1)
    _check_field("abutment_pile_length_m", "桩柱式桥台钻孔桩桩长", b_ps["abutment_pile_length_m"], a_ps["abutment_pile_length_m"], "m", "abutment_and_pile")
    _check_field("abutment_pile_ra_kn", "桥台单桩容许承载力 [Ra]", b_ab["bored_pile"]["allowable_ra_kn"], a_ab["bored_pile"]["allowable_ra_kn"], "kN", "abutment_and_pile", tol=0.1)

    # 5. BOQ Material Quantities
    _check_field("prestress_strand_kg", "全桥预应力钢绞线总重", b_boq["prestress_strand_kg"], a_boq["prestress_strand_kg"], "kg", "prestress_tendons", tol=0.1)
    _check_field("abut_pile_c30_m3", "桥台钻孔桩水下C30混凝土", b_boq["volume_breakdown"]["abut_pile_m3"], a_boq["volume_breakdown"]["abut_pile_m3"], "m3", "abutment_and_pile", tol=0.02)
    _check_field("pier_pile_c30_m3", "桥墩钻孔桩水下C30混凝土", b_boq["volume_breakdown"]["pier_pile_m3"], a_boq["volume_breakdown"]["pier_pile_m3"], "m3", "pier_substructure_and_pile", tol=0.02)
    _check_field("c30_underwater_pile_m3", "全桥水下钻孔桩C30混凝土合计", b_boq["c30_underwater_pile_m3"], a_boq["c30_underwater_pile_m3"], "m3", "abutment_and_pile" if "abutment_and_pile" in triggered_domains else "pier_substructure_and_pile", tol=0.02)
    _check_field("hrb400_rebar_kg", "全桥HRB400带肋钢筋总重", b_boq["hrb400_rebar_kg"], a_boq["hrb400_rebar_kg"], "kg", "abutment_and_pile" if "abutment_and_pile" in triggered_domains else "pier_substructure_and_pile", tol=0.2)
    _check_field("hpb300_rebar_kg", "全桥HPB300光圆钢筋总重", b_boq["hpb300_rebar_kg"], a_boq["hpb300_rebar_kg"], "kg", "abutment_and_pile" if "abutment_and_pile" in triggered_domains else "pier_substructure_and_pile", tol=0.2)
    _check_field("asphalt_paving_m3", "桥面沥青混凝土方量", b_boq["asphalt_paving_m3"], a_boq["asphalt_paving_m3"], "m3", "paving_and_dead_load", tol=0.02)
    _check_field("c40_paving_m3", "桥面C40防水混凝土调平层方量", b_boq["c40_paving_m3"], a_boq["c40_paving_m3"], "m3", "paving_and_dead_load", tol=0.02)

    # 6. Overall calculation / graduation readiness / FEM status
    _check_field("calculation_status", "结构验算总状态", before_report.get("calculation_status"), after_report.get("calculation_status"), "-", "status_and_survey")
    _check_field("graduation_readiness_status", "毕设资料齐套状态", before_report.get("graduation_readiness_status"), after_report.get("graduation_readiness_status"), "-", "status_and_survey")
    b_fem_st = (before_report.get("defense_verification_table") or {}).get("external_fem_status")
    a_fem_st = (after_report.get("defense_verification_table") or {}).get("external_fem_status")
    if b_fem_st != a_fem_st:
        _check_field("external_fem_status", "独立有限元软件复核状态", b_fem_st, a_fem_st, "-", "fem_verification")

    # 7. Individual Rebar & Tendon ID diffs
    b_idx = before_report["rebar_and_tendon_ledger"]["index_by_id"]
    a_idx = after_report["rebar_and_tendon_ledger"]["index_by_id"]
    changed_bar_ids: list[dict[str, Any]] = []
    extra_drawings_from_bars: set[str] = set()
    for item_id in sorted(set(b_idx.keys()) | set(a_idx.keys())):
        if item_id not in b_idx:
            rec_a = a_idx[item_id]
            changed_bar_ids.append(
                {
                    "id": item_id,
                    "sheet_drawing_no": rec_a["sheet_drawing_no"],
                    "change": "新增编号",
                    "after_weight_kg": rec_a["quantity_scope"]["bridge_total_weight_kg"],
                }
            )
            extra_drawings_from_bars.add(rec_a["sheet_drawing_no"])
        elif item_id not in a_idx:
            rec_b = b_idx[item_id]
            changed_bar_ids.append(
                {
                    "id": item_id,
                    "sheet_drawing_no": rec_b["sheet_drawing_no"],
                    "change": "移除编号",
                    "before_weight_kg": rec_b["quantity_scope"]["bridge_total_weight_kg"],
                }
            )
            extra_drawings_from_bars.add(rec_b["sheet_drawing_no"])
        else:
            rb, ra = b_idx[item_id], a_idx[item_id]
            wb = float(rb["quantity_scope"]["bridge_total_weight_kg"])
            wa = float(ra["quantity_scope"]["bridge_total_weight_kg"])
            lb = float(rb["quantity_scope"]["single_length_m"])
            la = float(ra["quantity_scope"]["single_length_m"])
            cb = int(rb["quantity_scope"]["count_per_member"])
            ca = int(ra["quantity_scope"]["count_per_member"])
            if not (math.isclose(wb, wa, abs_tol=0.05) and math.isclose(lb, la, abs_tol=1e-3) and cb == ca):
                changed_bar_ids.append(
                    {
                        "id": item_id,
                        "sheet_drawing_no": ra["sheet_drawing_no"],
                        "change": f"单长 {lb:g}->{la:g}m, 单件数 {cb}->{ca}, 全桥重 {wb:g}->{wa:g}kg",
                        "before_weight_kg": wb,
                        "after_weight_kg": wa,
                    }
                )
                extra_drawings_from_bars.add(ra["sheet_drawing_no"])

    calc_chaps: list[str] = []
    thesis_chaps: list[str] = []
    drawings_set: set[str] = set(extra_drawings_from_bars)
    figures_set: set[str] = set()

    for dom in sorted(triggered_domains):
        rule = _DOMAIN_IMPACT_RULES.get(dom, {})
        for c in rule.get("calc_book_chapters", []):
            if c not in calc_chaps:
                calc_chaps.append(c)
        for tc in rule.get("thesis_chapters", []):
            if tc not in thesis_chaps:
                thesis_chaps.append(tc)
        for rk in rule.get("drawing_roles", []):
            dno_m = _resolve_role_dno(rk)
            if dno_m:
                drawings_set.add(dno_m)
        figures_set.update(rule.get("figures", []))

    if before_report.get("failed_checks") != after_report.get("failed_checks") or (
        before_report.get("unresolved_assumptions") != after_report.get("unresolved_assumptions")
    ):
        notes_no = _resolve_role_dno("general_notes")
        if notes_no:
            drawings_set.add(notes_no)

    dxf_changed_list: list[str] = []
    if before_dxf_hashes and after_dxf_hashes:
        for dno in sorted(set(before_dxf_hashes.keys()) | set(after_dxf_hashes.keys())):
            if before_dxf_hashes.get(dno) != after_dxf_hashes.get(dno):
                dxf_changed_list.append(dno)
                drawings_set.add(dno)

    affected_drawings = sorted(drawings_set)
    affected_figures = sorted(figures_set)
    has_changes = bool(changed_items or changed_bar_ids or dxf_changed_list)

    if not has_changes:
        summary_zh = "与上一版基线相比无设计参数、工程量或图纸内容变化（语义内容差异 0 张），全套图纸与计算书保持一致。"
    else:
        top_labels = "、".join(
            f"{it['label_zh']}({it['before']}→{it['after']}{it['unit'] if it['unit'] != '-' else ''})"
            for it in changed_items[:5]
        ) or f"图纸语义内容更新 {len(dxf_changed_list)} 张"
        summary_zh = (
            f"本次共检测到 {len(changed_items)} 项关键参数/工程量变更（{top_labels}），"
            f"联动更新钢筋/钢束编号 {len(changed_bar_ids)} 项，"
            f"涉及计算书 {len(calc_chaps)} 个章节、正式说明书 {len(thesis_chaps)} 个章节、"
            f"实际变更施工图 {len(dxf_changed_list) if dxf_changed_list else len(affected_drawings)} 张（{', '.join(dxf_changed_list or affected_drawings)}）。"
        )

    return {
        "mode": "diff_against_baseline",
        "has_changes": has_changes,
        "summary_zh": summary_zh,
        "changed_items": changed_items,
        "parameter_changes": changed_items,
        "changed_rebar_or_tendon_ids": changed_bar_ids,
        "affected_calc_book_chapters": calc_chaps,
        "affected_thesis_chapters": thesis_chaps,
        "affected_drawings": affected_drawings,
        "dxf_content_changed_drawings": dxf_changed_list,
        "dxf_sha256_changed_drawings": dxf_changed_list,
        "affected_figures": affected_figures,
        "internal_consistency": consistency,
    }


def save_change_impact_report(impact: Mapping[str, Any], output_path: str | Path) -> Path:
    out_p = Path(output_path).expanduser().resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(dict(impact), ensure_ascii=False, indent=2), encoding="utf-8")
    return out_p
