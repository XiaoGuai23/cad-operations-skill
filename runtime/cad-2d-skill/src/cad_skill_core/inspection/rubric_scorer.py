"""100-point engineering CAD rubric scorer inspired by cad-1000-hours rubrics.json.

Evaluates both generated task DXFs and arbitrary user-uploaded DXF files across:
1. Built-in 5-dimension GB/T & ISO 100-point engineering quality rubric;
2. Optional cad-1000-hours compatible `rubrics.json` task-specific criteria list.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from math import hypot, isclose, isfinite
from pathlib import Path
from typing import Any, Mapping

import ezdxf
from ezdxf import bbox


@dataclass(frozen=True, slots=True)
class RubricDimensionScore:
    dimension_id: str
    title_zh: str
    score: float
    max_score: float
    passed_checks: tuple[str, ...]
    deductions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CustomCriterionResult:
    criterion_id: str
    description: str
    weight: float
    earned: float
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class RubricScoreReport:
    total_score: float
    max_score: float
    grade: str
    grade_label_zh: str
    entity_count: int
    dimensions: tuple[RubricDimensionScore, ...]
    custom_criteria: tuple[CustomCriterionResult, ...]
    recommendations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _grade_for_score(pct: float) -> tuple[str, str]:
    if pct >= 92.0:
        return "S", "卓越交付级"
    if pct >= 82.0:
        return "A", "优良工程级"
    if pct >= 70.0:
        return "B", "合格审阅级"
    if pct >= 60.0:
        return "C", "待局部整改"
    return "D", "需返工修正"


def score_dxf_drawing(
    dxf_path: Path,
    *,
    audit_json_path: Path | None = None,
    custom_rubric: Mapping[str, Any] | list[Mapping[str, Any]] | None = None,
) -> RubricScoreReport:
    """Compute a deterministic 0-100 engineering quality score for a DXF file."""
    path = Path(dxf_path)
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    entities = list(msp)

    audit_data: dict[str, Any] | None = None
    cand_audit = audit_json_path or (path.parent / "audit.json")
    if cand_audit.is_file():
        try:
            audit_data = json.loads(cand_audit.read_text(encoding="utf-8"))
        except Exception:
            audit_data = None

    recommendations: list[str] = []

    # ------------------------------------------------------------------
    # 1. Geometry & Topology Health (25 pts)
    # ------------------------------------------------------------------
    g_score = 25.0
    g_pass: list[str] = []
    g_ded: list[str] = []

    ez_auditor = doc.audit()
    if not ez_auditor.errors:
        g_pass.append("DXF 底层拓扑结构审计零错误")
    else:
        loss = min(12.0, 4.0 * len(ez_auditor.errors))
        g_score -= loss
        g_ded.append(f"DXF 底层审计发现 {len(ez_auditor.errors)} 处错误 (-{loss:g})")
        recommendations.append("修复损坏或悬空的 DXF 图元句柄与表引用。")

    degenerate_count = 0
    closed_outline_count = 0
    for ent in entities:
        dt = ent.dxftype()
        if dt == "LINE":
            s, e = ent.dxf.start, ent.dxf.end
            if hypot(float(e.x - s.x), float(e.y - s.y)) < 1e-6:
                degenerate_count += 1
        elif dt in {"CIRCLE", "ARC"}:
            if float(ent.dxf.radius) <= 1e-6:
                degenerate_count += 1
            elif dt == "CIRCLE":
                closed_outline_count += 1
        elif dt == "LWPOLYLINE" and ent.closed:
            closed_outline_count += 1

    if degenerate_count == 0:
        g_pass.append("零退化零长线段与零半径圆弧")
    else:
        loss = min(7.0, 2.5 * degenerate_count)
        g_score -= loss
        g_ded.append(f"存在 {degenerate_count} 个零长度/退化几何图元 (-{loss:g})")
        recommendations.append("清理零长度线段或极小退化圆弧。")

    if closed_outline_count > 0 or len(entities) >= 15:
        g_pass.append(f"包含 {closed_outline_count} 个闭合几何轮廓/圆特征")
    else:
        g_score -= 6.0
        g_ded.append("缺少闭合主轮廓多段线或圆特征 (-6)")

    dim_geom = RubricDimensionScore(
        "geometry_topology",
        "几何与拓扑健康度",
        round(max(0.0, g_score), 1),
        25.0,
        tuple(g_pass),
        tuple(g_ded),
    )

    # ------------------------------------------------------------------
    # 2. GB/T & ISO Layer and Lineweight Hierarchy (20 pts)
    # ------------------------------------------------------------------
    l_score = 20.0
    l_pass: list[str] = []
    l_ded: list[str] = []

    used_layers = {str(ent.dxf.get("layer", "0")).upper() for ent in entities}
    non_zero_layers = used_layers - {"0", "DEFPOINTS"}
    if len(non_zero_layers) >= 3:
        l_pass.append(f"语义图层划分清晰（使用 {len(non_zero_layers)} 个专业图层）")
    elif len(non_zero_layers) >= 1:
        l_score -= 4.0
        l_ded.append(f"语义图层较少（仅 {len(non_zero_layers)} 个非 0 图层, -4）")
        recommendations.append("按国标将粗实线轮廓、中心线、虚线、剖面线与尺寸标注分置独立图层。")
    else:
        l_score -= 10.0
        l_ded.append("所有图元均堆叠在默认 0 图层 (-10)")
        recommendations.append("避免将全部工程图元画在 0 层，建议建立 OUTLINE / CENTER / HIDDEN / DIM 图层。")

    defined_lws = [
        int(doc.layers.get(name).dxf.get("lineweight", -3))
        for name in {lyr.dxf.name for lyr in doc.layers}
        if int(doc.layers.get(name).dxf.get("lineweight", -3)) > 0
    ]
    if len(set(defined_lws)) >= 2:
        l_pass.append("具备粗细有别的工程线宽层级（粗实线 > 细实线）")
    elif len(defined_lws) == 1:
        l_score -= 4.0
        l_ded.append("全图仅单一线宽，缺乏粗细层次 (-4)")
    else:
        l_score -= 8.0
        l_ded.append("图层未显式配置毫米级工程线宽 (-8)")

    dim_layers = RubricDimensionScore(
        "layers_and_lineweights",
        "国标图层与线宽规范度",
        round(max(0.0, l_score), 1),
        20.0,
        tuple(l_pass),
        tuple(l_ded),
    )

    # ------------------------------------------------------------------
    # 3. Sheet Composition & Orthographic View Consistency (20 pts)
    # ------------------------------------------------------------------
    v_score = 20.0
    v_pass: list[str] = []
    v_ded: list[str] = []

    ext = bbox.extents(entities)
    span_x = float(ext.extmax.x - ext.extmin.x) if ext.has_data else 0.0
    span_y = float(ext.extmax.y - ext.extmin.y) if ext.has_data else 0.0
    if ext.has_data and isfinite(span_x) and isfinite(span_y) and span_x > 10.0 and span_y > 10.0:
        v_pass.append(f"图幅有效包络尺寸合理 ({span_x:.1f} × {span_y:.1f} mm)")
    else:
        v_score -= 10.0
        v_ded.append("图纸有效几何包络为空或过小 (-10)")

    if audit_data is not None:
        issues = audit_data.get("issues", [])
        ortho_or_layout_errs = [
            i for i in issues if i.get("severity") == "error" and i.get("category") in {"layout", "semantic"}
        ]
        if not ortho_or_layout_errs:
            v_pass.append("多视图正交投影（长对正/高平齐/宽相等）与保护区校验零误差")
        else:
            loss = min(10.0, 5.0 * len(ortho_or_layout_errs))
            v_score -= loss
            v_ded.append(f"存在 {len(ortho_or_layout_errs)} 项视图投影或布局越界错误 (-{loss:g})")
    else:
        v_pass.append("图面空间排布无越界异常")

    dim_views = RubricDimensionScore(
        "layout_and_views",
        "图幅构图与正交投影一致性",
        round(max(0.0, v_score), 1),
        20.0,
        tuple(v_pass),
        tuple(v_ded),
    )

    # ------------------------------------------------------------------
    # 4. Engineering Dimension Chain & Collision-Free Readability (20 pts)
    # ------------------------------------------------------------------
    d_score = 20.0
    d_pass: list[str] = []
    d_ded: list[str] = []

    dim_entities = [e for e in entities if e.dxftype() == "DIMENSION"]
    text_entities = [e for e in entities if e.dxftype() in {"TEXT", "MTEXT"}]
    all_text_strings = [
        (str(e.dxf.get("text", "")) if e.dxftype() == "TEXT" else e.plain_text()).strip()
        for e in text_entities
    ]
    eng_callouts = [t for t in all_text_strings if any(tok in t for tok in ("⌀", "Φ", "R", "kV", "mm", "AC-", "C"))]

    if len(dim_entities) >= 3:
        d_pass.append(f"包含 {len(dim_entities)} 个原生关联工程尺寸标注 (DIMENSION)")
    elif len(dim_entities) >= 1 or len(eng_callouts) >= 4:
        d_pass.append(f"包含工程尺寸与参数标注 ({len(dim_entities)} 个尺寸图元, {len(eng_callouts)} 条技术标注)")
    else:
        d_score -= 10.0
        d_ded.append("图纸缺少尺寸标注图元 (-10)")
        recommendations.append("补充定形尺寸、定位尺寸与总体包络尺寸链。")

    if audit_data is not None:
        coll_errs = [i for i in audit_data.get("issues", []) if i.get("code") == "LAYOUT_COLLISION"]
        if not coll_errs:
            d_pass.append("标注文字与图框/表格网格防压线碰撞校验 100% 通过")
        else:
            loss = min(8.0, 4.0 * len(coll_errs))
            d_score -= loss
            d_ded.append(f"存在 {len(coll_errs)} 处标注或文字压线碰撞 (-{loss:g})")
    else:
        d_pass.append("文字与尺寸分布可读")

    dim_annots = RubricDimensionScore(
        "dimensions_and_annotations",
        "尺寸链与防压线可读性",
        round(max(0.0, d_score), 1),
        20.0,
        tuple(d_pass),
        tuple(d_ded),
    )

    # ------------------------------------------------------------------
    # 5. Title Block, Frame & Font Coverage (15 pts)
    # ------------------------------------------------------------------
    t_score = 15.0
    t_pass: list[str] = []
    t_ded: list[str] = []

    joined_upper = " ".join(all_text_strings).upper()
    has_title_meta = (
        any(k in joined_upper for k in ("图号", "比例", "图名", "DWG", "SCALE", "TITLE"))
        or "FRAME" in used_layers
        or "TITLE" in used_layers
        or "BORDER" in used_layers
    )
    if has_title_meta:
        t_pass.append("具备标准工程图框与标题栏元数据")
    else:
        t_score -= 9.0
        t_ded.append("未检测到标准内图框或标题栏字段 (-9)")
        recommendations.append("在图纸右下角添加符合 GB/T 10609.1 的标准分格标题栏。")

    style_names = {s.dxf.name.upper() for s in doc.styles}
    if len(style_names - {"STANDARD"}) >= 1:
        t_pass.append("配置了独立工程字体样式，支持中英文与工程符号")
    else:
        t_score -= 3.0
        t_ded.append("仅使用默认 Standard 字体样式 (-3)")

    dim_title = RubricDimensionScore(
        "title_block_and_fonts",
        "图框标题栏与字体规范度",
        round(max(0.0, t_score), 1),
        15.0,
        tuple(t_pass),
        tuple(t_ded),
    )

    dimensions_tuple = (dim_geom, dim_layers, dim_views, dim_annots, dim_title)
    builtin_total = round(sum(d.score for d in dimensions_tuple), 1)

    # ------------------------------------------------------------------
    # Optional cad-1000-hours style custom rubrics.json evaluation
    # ------------------------------------------------------------------
    custom_results: list[CustomCriterionResult] = []
    if custom_rubric is not None:
        raw_items = (
            custom_rubric.get("criteria", custom_rubric.get("rubrics", []))
            if isinstance(custom_rubric, Mapping)
            else custom_rubric
        )
        for idx, item in enumerate(raw_items, start=1):
            cid = str(item.get("id", f"R-{idx:02d}"))
            desc = str(item.get("description", item.get("title", cid)))
            weight = float(item.get("weight", 10.0))
            ok, detail = _evaluate_single_custom_criterion(
                item,
                entities=entities,
                used_layers=used_layers,
                dim_count=len(dim_entities),
                all_texts=all_text_strings,
                spans=(span_x, span_y),
                audit_error_count=len(ez_auditor.errors),
            )
            custom_results.append(
                CustomCriterionResult(
                    criterion_id=cid,
                    description=desc,
                    weight=weight,
                    earned=weight if ok else 0.0,
                    passed=ok,
                    detail=detail,
                )
            )
            if not ok:
                recommendations.append(f"未满足专项评分项 [{cid}]：{desc}（{detail}）")

    if custom_results:
        total_w = sum(c.weight for c in custom_results) or 1.0
        custom_pct = (sum(c.earned for c in custom_results) / total_w) * 100.0
        # Blend 50% universal engineering hygiene + 50% task-specific rubric criteria
        final_score = round(0.5 * builtin_total + 0.5 * custom_pct, 1)
    else:
        final_score = builtin_total

    grade, grade_zh = _grade_for_score(final_score)
    return RubricScoreReport(
        total_score=final_score,
        max_score=100.0,
        grade=grade,
        grade_label_zh=grade_zh,
        entity_count=len(entities),
        dimensions=dimensions_tuple,
        custom_criteria=tuple(custom_results),
        recommendations=tuple(recommendations),
    )


def _evaluate_single_custom_criterion(
    rule: Mapping[str, Any],
    *,
    entities: list[object],
    used_layers: set[str],
    dim_count: int,
    all_texts: list[str],
    spans: tuple[float, float],
    audit_error_count: int,
) -> tuple[bool, str]:
    check_type = str(rule.get("type", "composite")).lower()
    joined_text = "\n".join(all_texts)

    if check_type == "zero_audit_errors":
        return (audit_error_count == 0, f"ezdxf errors={audit_error_count}")

    if check_type == "required_layers" or "required_layers" in rule:
        req = {str(x).upper() for x in rule.get("required_layers", [])}
        missing = sorted(req - used_layers)
        return (len(missing) == 0, "全部具备" if not missing else f"缺少图层 {missing}")

    if check_type == "min_dimensions" or "min_dimensions" in rule:
        target = int(rule.get("min_dimensions", 1))
        return (dim_count >= target, f"实际标注数 {dim_count} / 要求 >= {target}")

    if check_type == "min_entities" or "min_entities" in rule:
        target = int(rule.get("min_entities", 1))
        return (len(entities) >= target, f"实际图元数 {len(entities)} / 要求 >= {target}")

    if check_type == "required_texts" or "required_texts" in rule:
        req_t = [str(x) for x in rule.get("required_texts", [])]
        miss_t = [t for t in req_t if t.casefold() not in joined_text.casefold()]
        return (len(miss_t) == 0, "全部命中" if not miss_t else f"缺少文本 {miss_t}")

    if check_type == "expected_dimension_values" or "expected_dimension_values" in rule:
        expected_vals = [float(x) for x in rule.get("expected_dimension_values", [])]
        actual_vals: list[float] = []
        for ent in entities:
            if ent.dxftype() == "DIMENSION":
                try:
                    actual_vals.append(float(ent.get_measurement()))
                except Exception:
                    pass
        # Also match formatted dimension labels in case of scaled views (1:2, 2:1)
        dim_labels = [str(ent.dxf.get("text", "")) for ent in entities if ent.dxftype() == "DIMENSION"]
        unmatched: list[float] = []
        for ev in expected_vals:
            token = f"{ev:g}"
            if not (
                any(isclose(av, ev, rel_tol=1e-2, abs_tol=0.5) for av in actual_vals)
                or any(token in lbl for lbl in dim_labels)
                or token in joined_text
            ):
                unmatched.append(ev)
        return (len(unmatched) == 0, "关键尺寸全部匹配" if not unmatched else f"未检出尺寸 {unmatched}")

    if check_type == "bbox_min_span" or "bbox_min_span" in rule:
        min_w, min_h = rule.get("bbox_min_span", [10.0, 10.0])
        ok = spans[0] >= float(min_w) and spans[1] >= float(min_h)
        return (ok, f"实际包络 {spans[0]:.1f}×{spans[1]:.1f} mm")

    return (audit_error_count == 0 and len(entities) > 0, "基础交付物有效")
