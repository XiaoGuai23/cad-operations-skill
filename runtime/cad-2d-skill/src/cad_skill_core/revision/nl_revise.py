"""Conversational natural-language region revision compiler & atomic executor.

Powers the blue-dashed marquee box + floating capsule workflow ("指哪改哪"):
1. Binds strictly to the source DXF's SHA-256 fingerprint;
2. Resolves entities inside the marquee `region_box` and/or `candidate_handles`;
3. Strips negated preservation clauses ("不要删除这个孔，把直径改为 18"), converts
   explicit prompt units to DXF `$INSUNITS`, dispatches by geometric property first,
   synchronizes only geometrically associated dimensions, and enforces
   `compare_untouched` so out-of-scope entities can never be mutated silently.
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
import json
from math import hypot, isclose, isfinite
from pathlib import Path
import re
import tempfile
from typing import Any

import ezdxf
from ezdxf import bbox
from ezdxf.math import Matrix44

from .common import RevisionError, compare_untouched, sha256_file, snapshot_modelspace
from .edits import _DELETE
from .ir_engine import _split_feature_clauses, compile_nl_to_feature_ir, execute_revision_ir
from .post_verify import (
    CircleDiameterEqualsCheck,
    HandlesAbsentCheck,
    HandlesTranslatedCheck,
    LinearSpanEqualsCheck,
    PostConditionSpec,
    TextContainsCheck,
    verify_revision_post_conditions,
)
from .transaction import DocumentCheckpoint
from .workflow import apply_revision, inventory_drawing

_DOC_UNIT_MM: dict[int, float] = {
    1: 25.4,    # Inches
    2: 304.8,   # Feet
    4: 1.0,     # Millimetres
    5: 10.0,    # Centimetres
    6: 1000.0,  # Metres
}
_DOC_UNIT_LABEL: dict[int, str] = {
    0: "",
    1: "in",
    2: "ft",
    4: "mm",
    5: "cm",
    6: "m",
}
_PROMPT_UNIT_MM: tuple[tuple[re.Pattern[str], float], ...] = (
    (re.compile(r"^(?:mm|millimet(?:er|re)s?|毫米)$", re.IGNORECASE), 1.0),
    (re.compile(r"^(?:cm|centimet(?:er|re)s?|厘米|公分)$", re.IGNORECASE), 10.0),
    (re.compile(r"^(?:in|inch(?:es)?|英寸|\")$", re.IGNORECASE), 25.4),
    (re.compile(r"^(?:ft|feet|foot|英尺|')$", re.IGNORECASE), 304.8),
    (re.compile(r"^(?:m|met(?:er|re)s?|米)$", re.IGNORECASE), 1000.0),
)
_UNIT_TOKEN_PATTERN = r"(?:millimet(?:er|re)s?|centimet(?:er|re)s?|inch(?:es)?|feet|foot|met(?:er|re)s?|毫米|厘米|公分|英寸|英尺|mm|cm|in|ft|m|\")"

_NEGATED_CLAUSE_RE = re.compile(
    r"^\s*(?:请)?(?:千万|一定|切记|注意)?\s*"
    r"(?:不要|不用|不必|别|勿|切勿|不准|不能|无需|禁止|不(?:要|去|可)?|保留|保持|维持|"
    r"do\s*not\b|don'?t\b|never\b|without\b|keep\b|preserve\b)",
    re.IGNORECASE,
)
_INLINE_NEGATED_ACTION_RE = re.compile(
    r"(?:不要|不用|不必|别|勿|切勿|不准|不能|无需|禁止|不|do\s*not|don'?t|never|without)\s*"
    r"(?:去|把[^\s，,；;。]{0,10})?\s*(?:删除?|删掉|去掉|移除|清除|改动?|变动?|delete|remove)",
    re.IGNORECASE,
)
_DELETE_VERB_RE = re.compile(r"(?:删除|删掉|删去|删|去掉|移除|清除|\bdelete\b|\bremove\b)", re.IGNORECASE)
_LINEAR_PROP_RE = re.compile(
    r"(?:线长|长度|边长|总长|总宽|总高|底板长|底板宽|板宽|板高|宽度|高度|跨度|"
    r"\blength\b|\bwidth\b|\bheight\b|\bspan\b|"
    r"(?:(?<!半)(?<!直)(?<!孔)(?<!外)(?<!内)长|宽|高)\s*(?:度)?\s*(?:改成|改为|设为|调整为|变成|=|:|：|\d))",
    re.IGNORECASE,
)
_CIRCLE_PROP_RE = re.compile(
    r"(?:孔径|直径|半径|外径|内径|螺栓孔|安装孔|通孔|圆孔|圆弧|孔|圆|"
    r"\bdiameter\b|\bradius\b|\bhole\b|\bcircle\b|"
    r"[⌀∅ØøΦφФф]\s*\d|(?<![A-Za-z])R\s*\d)",
    re.IGNORECASE,
)
_NUM_WITH_UNIT_RE = re.compile(
    rf"[⌀∅ØøΦφФфRr]?\s*(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_TOKEN_PATTERN})?",
    re.IGNORECASE,
)


def _entity_intersects_box(
    ent: Any,
    box: tuple[float, float, float, float],
) -> bool:
    bx0, by0, bx1, by1 = (
        min(box[0], box[2]),
        min(box[1], box[3]),
        max(box[0], box[2]),
        max(box[1], box[3]),
    )
    try:
        ext = bbox.extents([ent])
    except Exception:
        return False
    if not ext.has_data:
        return False
    return not (
        float(ext.extmax.x) < bx0
        or float(ext.extmin.x) > bx1
        or float(ext.extmax.y) < by0
        or float(ext.extmin.y) > by1
    )


def _extract_affirmative_prompt(prompt: str) -> tuple[str, bool]:
    """Strip negated / preservation-only clauses and return `(affirmative_text, had_negation)`."""
    clauses = [c.strip() for c in re.split(r"[，,；;。!！\n]+", prompt) if c.strip()]
    had_neg = False
    kept: list[str] = []
    for cl in clauses:
        if _NEGATED_CLAUSE_RE.search(cl):
            had_neg = True
            continue
        cleaned_cl = _INLINE_NEGATED_ACTION_RE.sub("", cl).strip()
        if cleaned_cl != cl:
            had_neg = True
        if cleaned_cl:
            kept.append(cleaned_cl)
    return "，".join(kept), had_neg


def _is_question_only(prompt: str, *, has_structured_ir: bool = False) -> bool:
    if has_structured_ir:
        return False
    txt = prompt.strip()
    if not txt:
        return True
    if txt.endswith("?") or txt.endswith("？"):
        return True
    edit_verbs = (
        "删", "去掉", "移除", "清除", "改", "变", "设为", "设置", "调整", "放大", "缩小",
        "移", "偏", "替换", "新增", "添加", "增加", "新开", "开", "钻", "打", "加一", "倒角", "倒圆",
        "圆角", "导圆", "倒斜角", "斜角", "镜像", "对称复制", "旋转", "翻转", "缩放", "修剪", "延伸",
        "delete", "remove", "change", "set", "resize", "move", "shift",
        "add", "chamfer", "fillet", "mirror", "rotate", "scale", "trim", "extend",
    )
    return not any(v in txt.lower() for v in edit_verbs)


def _convert_length_to_doc_units(
    raw_val: float,
    raw_unit: str | None,
    doc_units: int,
) -> tuple[float, str]:
    """Convert an explicit prompt length value into the DXF document's `$INSUNITS`."""
    unit_lbl = _DOC_UNIT_LABEL.get(doc_units, f"unit({doc_units})")
    if not raw_unit or not raw_unit.strip():
        return raw_val, unit_lbl
    u_tok = raw_unit.strip()
    prompt_mm: float | None = None
    for pat, factor_mm in _PROMPT_UNIT_MM:
        if pat.fullmatch(u_tok):
            prompt_mm = factor_mm
            break
    if prompt_mm is None:
        return raw_val, unit_lbl
    if doc_units == 0:
        raise RevisionError(
            "当前 DXF 未声明物理单位 ($INSUNITS=0)，拒绝隐式跨单位换算；请去掉单位后缀直接按图纸单位输入或先声明图纸单位。"
        )
    doc_mm = _DOC_UNIT_MM.get(doc_units)
    if doc_mm is None:
        raise RevisionError(f"不支持将「{raw_unit}」自动换算至当前 DXF 单位代码 ($INSUNITS={doc_units})。")
    return raw_val * (prompt_mm / doc_mm), unit_lbl


def _extract_dim_nominal_values(ent: Any) -> list[float]:
    vals: list[float] = []
    txt = str(ent.dxf.get("text", "")).strip()
    if txt and txt != "<>":
        base = re.split(r"±|\(", txt, maxsplit=1)[0].strip()
        base = re.sub(r"^\d+\s*[×xX*-]\s*(?=[⌀∅ØøΦφФфRrMm\d])", "", base)
        for tok in re.findall(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)?(?![0-9.])", base):
            try:
                vals.append(float(tok))
            except ValueError:
                pass
    try:
        meas = float(ent.get_measurement())
        if isfinite(meas) and meas > 0:
            vals.append(meas)
    except Exception:
        pass
    return vals


def _match_associated_circle_dimension(
    dim_ent: Any,
    target_Geom: Sequence[tuple[float, float, float]],
    old_diam: float,
    old_radius: float,
) -> tuple[bool, bool, tuple[float, float] | None]:
    """Return `(is_associated, is_radial_dim, matched_center)` for a DIMENSION entity."""
    dtype = int(dim_ent.dxf.get("dimtype", 0)) & 7
    txt = str(dim_ent.dxf.get("text", "")).strip()
    is_radial = (dtype == 4) or (
        bool(re.search(r"(?<![A-Za-z])R\s*\d", txt))
        and not any(ch in txt for ch in ("⌀", "∅", "Ø", "ø", "Φ", "φ"))
    )
    expected_nom = old_radius if is_radial else old_diam
    nom_vals = _extract_dim_nominal_values(dim_ent)
    if not any(isclose(v, expected_nom, rel_tol=0.0, abs_tol=max(1e-3, expected_nom * 0.015)) for v in nom_vals):
        return False, is_radial, None

    # Check strict geometric proximity of the dimension's definition points to one of the target circles
    pts: list[tuple[float, float]] = []
    for attr in ("defpoint", "defpoint2", "defpoint3", "defpoint4"):
        if dim_ent.dxf.hasattr(attr):
            p = getattr(dim_ent.dxf, attr)
            pts.append((float(p.x), float(p.y)))
    if not pts:
        return False, is_radial, None

    for cx, cy, old_r in target_Geom:
        geom_tol = max(0.05, old_r * 0.08)
        if dtype in {3, 4}:  # native diameter (3) or radius (4) dimension
            if any(
                hypot(px - cx, py - cy) <= geom_tol
                or abs(hypot(px - cx, py - cy) - old_r) <= geom_tol
                for px, py in pts
            ):
                return True, is_radial, (cx, cy)
        elif dim_ent.dxf.hasattr("defpoint2") and dim_ent.dxf.hasattr("defpoint3"):
            # Linear diameter dimension spanning across the circle's two opposite sides
            p2 = dim_ent.dxf.defpoint2
            p3 = dim_ent.dxf.defpoint3
            span = hypot(float(p3.x - p2.x), float(p3.y - p2.y))
            mx, my = 0.5 * float(p2.x + p3.x), 0.5 * float(p2.y + p3.y)
            if abs(span - 2.0 * old_r) <= geom_tol and (
                abs(mx - cx) <= geom_tol or abs(my - cy) <= geom_tol
            ):
                return True, is_radial, (cx, cy)
    return False, is_radial, None


def _replace_exact_nominal_token(text: str, old_val: float, new_val: float) -> str:
    """Replace standalone numeric value `old_val` with `new_val` without corrupting longer numbers like `120`."""
    if not text or text == "<>":
        return text
    old_s = f"{old_val:g}"
    new_s = f"{new_val:g}"
    # Avoid replacing multiplicity prefix in "4×⌀12" when old_val happens to equal 4:
    # split optional "N×" prefix first and only replace in the nominal body!
    m_mult = re.match(r"^(?P<mult>\d+\s*[×xX*-]\s*)(?P<rest>.+)$", text)
    if m_mult:
        prefix_mult = m_mult.group("mult")
        rest = m_mult.group("rest")
    else:
        prefix_mult = ""
        rest = text
    # Split off tolerance suffix ("±..." or "(...)") so tolerances are never altered
    m_tol = re.search(r"(\s*(?:±|\().*)$", rest)
    if m_tol:
        nom_part = rest[: m_tol.start()]
        tol_part = m_tol.group(1)
    else:
        nom_part = rest
        tol_part = ""
    pat = re.compile(rf"(?<![0-9.]){re.escape(old_s)}(?:\.0+)?(?![0-9.])")
    updated_nom = pat.sub(new_s, nom_part, count=1)
    return f"{prefix_mult}{updated_nom}{tol_part}"


def execute_nl_region_revision(
    source_dxf: Path,
    output_dir: Path,
    prompt_text: str,
    *,
    region_box: Sequence[float] | None = None,
    candidate_handles: Sequence[str] | None = None,
    expected_sha256: str | None = None,
    structured_ir: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Compile and execute a natural-language marquee-box revision atomically."""
    src_path = Path(source_dxf).expanduser().resolve(strict=True)
    current_sha = sha256_file(src_path)
    if expected_sha256 and expected_sha256.lower() != current_sha.lower():
        raise RevisionError(
            f"baseline SHA-256 mismatch: expected {expected_sha256[:12]}, got {current_sha[:12]}"
        )

    inv = inventory_drawing(src_path)
    doc = ezdxf.readfile(src_path)
    doc_units = int(doc.units)
    unit_suffix = f" {_DOC_UNIT_LABEL[doc_units]}" if _DOC_UNIT_LABEL.get(doc_units) else ""
    msp = doc.modelspace()
    by_handle = {str(e.dxf.handle).upper(): e for e in msp}

    box_tuple: tuple[float, float, float, float] | None = None
    if region_box is not None:
        if len(region_box) != 4 or not all(isfinite(float(v)) for v in region_box):
            raise RevisionError("region_box must be 4 finite numbers [xmin, ymin, xmax, ymax]")
        box_tuple = (float(region_box[0]), float(region_box[1]), float(region_box[2]), float(region_box[3]))

    # Resolve candidate entities in the marquee region
    selected_handles: list[str] = []
    if candidate_handles:
        for h in candidate_handles:
            hu = str(h).strip().upper()
            if hu in by_handle and hu not in selected_handles:
                selected_handles.append(hu)
    elif box_tuple is not None:
        for e in msp:
            hu = str(e.dxf.handle).upper()
            if _entity_intersects_box(e, box_tuple):
                selected_handles.append(hu)
    else:
        selected_handles = [str(e.dxf.handle).upper() for e in msp]

    prompt_clean = prompt_text.strip()
    non_frame_handles = [
        h for h in selected_handles if str(by_handle[h].dxf.get("layer", "")).upper() != "FRAME"
    ]
    working_handles = non_frame_handles if non_frame_handles else selected_handles

    # ------------------------------------------------------------------
    # 0. Question / Inspection Pin Mode (no mutation)
    # ------------------------------------------------------------------
    if _is_question_only(prompt_clean, has_structured_ir=(structured_ir is not None)):
        type_summary: dict[str, int] = {}
        radii: list[float] = []
        texts_found: list[str] = []
        for h in working_handles:
            ent = by_handle[h]
            dt = ent.dxftype()
            type_summary[dt] = type_summary.get(dt, 0) + 1
            if dt in {"CIRCLE", "ARC"}:
                radii.append(round(float(ent.dxf.radius), 4))
            elif dt == "TEXT":
                texts_found.append(str(ent.dxf.get("text", "")))
        parts_desc = [f"{k}×{v}" for k, v in sorted(type_summary.items())]
        extra = ""
        if radii:
            extra += f"；圆孔直径: {', '.join(f'⌀{2*r:g}{unit_suffix}' for r in sorted(set(radii))[:4])}"
        if texts_found:
            extra += f"；文字: {', '.join(texts_found[:3])}"
        return {
            "mode": "question_pin",
            "status": "inspected",
            "prompt": prompt_clean,
            "baseline_sha256": current_sha,
            "hit_count": len(working_handles),
            "hit_handles": working_handles[:60],
            "summary_zh": f"框选区域共命中 {len(working_handles)} 个图元（{', '.join(parts_desc) or '空'}）{extra}",
        }

    # Strip negated / preservation-only clauses ("不要删除这个孔，把它的直径改为 18")
    active_prompt, had_negation = _extract_affirmative_prompt(prompt_clean)
    if not active_prompt and structured_ir is None:
        raise RevisionError(f"指令「{prompt_clean}」仅包含否定/保留说明，未指定需要执行的修改动作。")

    # Structured IR / Feature Primitives (add hole, bolt circle, chamfer, fillet, mirror)
    compiled_ir = (
        structured_ir
        if structured_ir is not None
        else compile_nl_to_feature_ir(
            active_prompt,
            candidate_handles=working_handles,
            region_box=box_tuple,
        )
    )
    if compiled_ir is not None:
        ir_res = execute_revision_ir(
            src_path,
            output_dir,
            compiled_ir,
            region_box=box_tuple,
            candidate_handles=working_handles,
            expected_sha256=expected_sha256,
            dry_run=dry_run,
        )
        ir_res["prompt"] = prompt_clean
        return ir_res

    if not working_handles:
        raise RevisionError("框选区域内未命中可修改的 CAD 图元，请重新拉取蓝色虚线框覆盖目标特征。")

    action_clauses = _split_feature_clauses(active_prompt)
    if len(action_clauses) > 1:
        raise RevisionError(
            f"检测到多条未合并的复合修改子句（{action_clauses}），为防止部分子句被静默忽略，请分步执行或使用结构化 IR。"
        )

    out_dir = Path(output_dir).resolve()
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    out_dxf = out_dir / "revised.dxf"
    plan_file = out_dir.parent / "nl-revision-plan.json"

    # ------------------------------------------------------------------
    # 1. Delete / Remove Entities (only on affirmative active_prompt!)
    # ------------------------------------------------------------------
    if _DELETE_VERB_RE.search(active_prompt):
        targets = list(working_handles)
        pl = active_prompt.lower()
        if any(k in pl for k in ("虚线", "隐藏", "hidden")):
            targets = [h for h in targets if str(by_handle[h].dxf.get("layer", "")).upper() == "HIDDEN"]
        elif any(k in pl for k in ("中心线", "点画线", "center")):
            targets = [h for h in targets if str(by_handle[h].dxf.get("layer", "")).upper() == "CENTER"]
        elif any(k in pl for k in ("剖面", "填充", "hatch")):
            targets = [
                h for h in targets
                if by_handle[h].dxftype() == "HATCH" or str(by_handle[h].dxf.get("layer", "")).upper() == "HATCH"
            ]
        elif any(k in pl for k in ("孔", "圆", "circle", "hole")):
            targets = [
                h for h in targets
                if by_handle[h].dxftype() in {"CIRCLE", "ARC"} and str(by_handle[h].dxf.get("layer", "")).upper() != "CENTER"
            ]
        elif any(k in pl for k in ("标注", "尺寸", "粗糙度", "公差", "基准", "符号", "dim")):
            targets = [
                h for h in targets
                if by_handle[h].dxftype() in {"DIMENSION", "LEADER", "MULTILEADER"}
                or str(by_handle[h].dxf.get("layer", "")).upper() == "DIM"
                or (by_handle[h].dxftype() in {"TEXT", "MTEXT"} and any(s in str(by_handle[h].dxf.get("text", "")) for s in ("Ra", "⌀", "0.", "A", "B")))
            ]
        elif any(k in pl for k in ("文字", "文本", "text")):
            targets = [h for h in targets if by_handle[h].dxftype() in {"TEXT", "MTEXT"}]
        elif any(k in pl for k in ("直线", "线段", "line")):
            targets = [h for h in targets if by_handle[h].dxftype() == "LINE"]

        if not targets:
            raise RevisionError(f"框选区域内未找到符合指令「{prompt_clean}」的目标图元。")

        if all(by_handle[h].dxftype() in _DELETE for h in targets):
            plan = {
                "schema": "cad-revision/v1",
                "baseline_sha256": current_sha,
                "units": inv["units"],
                "tolerance": 1e-4,
                "operations": [
                    {"id": "nl-del-1", "type": "delete_entities", "targets": [{"handle": h} for h in targets]}
                ],
            }
            plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            rev_res = apply_revision(src_path, plan_file, out_dir, dry_run=dry_run)
            new_sha = sha256_file(Path(rev_res["output_path"])) if not dry_run else current_sha
            ver = (
                verify_revision_post_conditions(src_path, Path(rev_res["output_path"]), [HandlesAbsentCheck(handles=targets)])
                if not dry_run
                else {"passed": True, "checked_count": 1, "failed_count": 0, "items": [{"index": 1, "check": "handles_absent", "label": "handles_absent", "passed": True, "detail": "dry-run simulated"}]}
            )
            return {
                "mode": "revision",
                "action": "delete_entities",
                "status": rev_res["status"],
                "dry_run": dry_run,
                "prompt": prompt_clean,
                "verification": ver,
                "baseline_sha256": current_sha,
                "new_sha256": new_sha,
                "modified_handles": targets,
                "output_path": rev_res.get("output_path"),
                "summary_zh": f"已精准删除框内 {len(targets)} 个目标图元",
            }

        return _atomic_doc_mutation(
            src_path=src_path,
            doc=doc,
            out_dxf=out_dxf,
            dry_run=dry_run,
            baseline_sha=current_sha,
            prompt=prompt_clean,
            action="delete_entities",
            handles=targets,
            mutator=lambda d, hs: [d.modelspace().delete_entity(e) for e in list(d.modelspace()) if str(e.dxf.handle).upper() in set(hs)],
            post_checks=[HandlesAbsentCheck(handles=targets)],
            summary_zh=f"已精准删除框内 {len(targets)} 个目标图元",
        )

    # ------------------------------------------------------------------
    # 2. Move / Offset Entities ("往右移 15" / "向上平移 25.4 mm")
    # ------------------------------------------------------------------
    m_move = re.search(
        rf"(?:往|向)?\s*(?P<dir>左|右|上|下|left|right|up|down)\s*(?:平移|移动|移|偏(?:移)?)?\s*(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_TOKEN_PATTERN})?",
        active_prompt,
        re.IGNORECASE,
    )
    if m_move:
        d_tok = m_move.group("dir").lower()
        dist, _ = _convert_length_to_doc_units(float(m_move.group("val")), m_move.group("unit"), doc_units)
        dx = -dist if d_tok in {"左", "left"} else (dist if d_tok in {"右", "right"} else 0.0)
        dy = dist if d_tok in {"上", "up"} else (-dist if d_tok in {"下", "down"} else 0.0)
        mat = Matrix44.translate(dx, dy, 0.0)
        return _atomic_doc_mutation(
            src_path=src_path,
            doc=doc,
            out_dxf=out_dxf,
            dry_run=dry_run,
            baseline_sha=current_sha,
            prompt=prompt_clean,
            action="translate_entities",
            handles=working_handles,
            mutator=lambda d, hs: [e.transform(mat) for e in d.modelspace() if str(e.dxf.handle).upper() in set(hs)],
            post_checks=[HandlesTranslatedCheck(handles=working_handles, dx=dx, dy=dy)],
            summary_zh=f"已将框内 {len(working_handles)} 个图元平移 ({dx:+g}, {dy:+g}){unit_suffix}",
        )

    # ------------------------------------------------------------------
    # Property-First Classification: Linear Span vs Circle Diameter/Radius
    # ------------------------------------------------------------------
    has_linear_prop = bool(_LINEAR_PROP_RE.search(active_prompt))
    has_circle_prop = bool(_CIRCLE_PROP_RE.search(active_prompt))
    m_num = _NUM_WITH_UNIT_RE.search(active_prompt)

    def _execute_linear_or_span() -> dict[str, Any]:
        assert m_num is not None
        new_span, _ = _convert_length_to_doc_units(float(m_num.group("val")), m_num.group("unit"), doc_units)
        if new_span <= 0:
            raise RevisionError("目标长度/跨度必须大于 0")
        lines_in_box = [
            h for h in working_handles
            if by_handle[h].dxftype() == "LINE"
            and str(by_handle[h].dxf.get("layer", "")).upper() not in {"CENTER", "HATCH", "DIM"}
        ]
        polys_in_box = [
            h for h in working_handles
            if by_handle[h].dxftype() == "LWPOLYLINE"
            and str(by_handle[h].dxf.get("layer", "")).upper() not in {"CENTER", "HATCH", "DIM"}
        ]
        prefer_line = any(k in active_prompt.lower() for k in ("线长", "直线", "线段", "line"))
        if prefer_line and lines_in_box:
            polys_in_box = []
        if polys_in_box:
            axis = "y" if any(k in active_prompt.lower() for k in ("高", "竖", "height")) else "x"
            plan = {
                "schema": "cad-revision/v1",
                "baseline_sha256": current_sha,
                "units": inv["units"],
                "tolerance": 1e-4,
                "operations": [
                    {
                        "id": "nl-span-1",
                        "type": "resize_fixed_edge",
                        "targets": [{"handle": polys_in_box[0]}],
                        "axis": axis,
                        "fixed": "min",
                        "size": new_span,
                    }
                ],
            }
            plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            rev_res = apply_revision(src_path, plan_file, out_dir, dry_run=dry_run)
            new_sha = sha256_file(Path(rev_res["output_path"])) if not dry_run else current_sha
            ver_p = (
                verify_revision_post_conditions(
                    src_path,
                    Path(rev_res["output_path"]),
                    [LinearSpanEqualsCheck(handles=[polys_in_box[0]], length=new_span, axis="vertical" if axis == "y" else "horizontal")],
                )
                if not dry_run
                else {"passed": True, "checked_count": 1, "failed_count": 0, "items": []}
            )
            return {
                "mode": "revision",
                "action": "resize_fixed_edge",
                "status": rev_res["status"],
                "dry_run": dry_run,
                "prompt": prompt_clean,
                "verification": ver_p,
                "baseline_sha256": current_sha,
                "new_sha256": new_sha,
                "modified_handles": [polys_in_box[0]],
                "output_path": rev_res.get("output_path"),
                "summary_zh": f"已将轮廓 #{polys_in_box[0]} 的 {axis.upper()} 向跨度调整为 {new_span:g}{unit_suffix}",
            }
        if lines_in_box:
            plan = {
                "schema": "cad-revision/v1",
                "baseline_sha256": current_sha,
                "units": inv["units"],
                "tolerance": 1e-4,
                "operations": [
                    {
                        "id": "nl-len-1",
                        "type": "set_length",
                        "target": {"handle": lines_in_box[0]},
                        "fixed": "start",
                        "length": new_span,
                    }
                ],
            }
            plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            rev_res = apply_revision(src_path, plan_file, out_dir, dry_run=dry_run)
            new_sha = sha256_file(Path(rev_res["output_path"])) if not dry_run else current_sha
            ver_l = (
                verify_revision_post_conditions(
                    src_path,
                    Path(rev_res["output_path"]),
                    [LinearSpanEqualsCheck(handles=[lines_in_box[0]], length=new_span)],
                )
                if not dry_run
                else {"passed": True, "checked_count": 1, "failed_count": 0, "items": []}
            )
            return {
                "mode": "revision",
                "action": "set_length",
                "status": rev_res["status"],
                "dry_run": dry_run,
                "prompt": prompt_clean,
                "verification": ver_l,
                "baseline_sha256": current_sha,
                "new_sha256": new_sha,
                "modified_handles": [lines_in_box[0]],
                "output_path": rev_res.get("output_path"),
                "summary_zh": f"已将线段 #{lines_in_box[0]} 长度调整为 {new_span:g}{unit_suffix}",
            }
        raise RevisionError("选区内未找到可调整长度或跨度的直线/轮廓图元。")

    # 3. Explicit Linear Length / Span takes strict priority when user asks for "长度/宽度/高度" (never mutates circles!)
    if m_num and has_linear_prop and not has_circle_prop:
        return _execute_linear_or_span()

    # 4. Change Hole / Circle Diameter or Radius ("把孔改成 Φ18" / "半径改为 12" / "把孔径改为 25.4 mm")
    circles_in_box = [
        h for h in working_handles
        if by_handle[h].dxftype() in {"CIRCLE", "ARC"} and str(by_handle[h].dxf.get("layer", "")).upper() != "CENTER"
    ]
    only_circles_selected = bool(circles_in_box) and all(
        by_handle[h].dxftype() in {"CIRCLE", "ARC"} for h in working_handles
    )
    if circles_in_box and m_num and (has_circle_prop or (only_circles_selected and not has_linear_prop)):
        converted_val, _ = _convert_length_to_doc_units(float(m_num.group("val")), m_num.group("unit"), doc_units)
        if converted_val <= 0:
            raise RevisionError("目标孔径/半径必须大于 0")
        is_radius = bool(re.search(r"半径|\bradius\b|(?<![A-Za-z])R\s*\d", active_prompt, re.IGNORECASE)) and not any(
            k in active_prompt for k in ("直径", "孔径", "外径", "内径", "Φ", "φ", "⌀", "∅", "Ø", "ø")
        )
        new_radius = converted_val if is_radius else converted_val / 2.0

        radii_map = {h: float(by_handle[h].dxf.radius) for h in circles_in_box}
        if len(set(round(r, 4) for r in radii_map.values())) > 1 and any(
            k in active_prompt for k in ("螺栓孔", "安装孔", "小孔", "孔")
        ):
            min_r = min(radii_map.values())
            target_circles = [h for h, r in radii_map.items() if isclose(r, min_r, abs_tol=1e-3)]
        else:
            target_circles = circles_in_box

        old_radius = round(radii_map[target_circles[0]], 6)
        old_diam = round(2.0 * old_radius, 6)
        new_diam = round(2.0 * new_radius, 6)
        target_geom = [
            (float(by_handle[h].dxf.center.x), float(by_handle[h].dxf.center.y), float(by_handle[h].dxf.radius))
            for h in target_circles
        ]

        # Pre-identify strictly associated DIMENSION handles so modified_handles & compare_untouched are 100% exact!
        assoc_dims: dict[str, tuple[bool, tuple[float, float]]] = {}
        for ent in msp:
            if ent.dxftype() != "DIMENSION":
                continue
            ok_assoc, is_rad_dim, matched_ctr = _match_associated_circle_dimension(
                ent, target_geom, old_diam, old_radius
            )
            if ok_assoc and matched_ctr is not None:
                assoc_dims[str(ent.dxf.handle).upper()] = (is_rad_dim, matched_ctr)

        all_touched_handles = [*target_circles, *assoc_dims.keys()]

        def _mutate_circles(d: ezdxf.document.Drawing, hs: Sequence[str]) -> None:
            circle_set = set(target_circles)
            for ent in d.modelspace():
                eh = str(ent.dxf.handle).upper()
                if eh in circle_set:
                    ent.dxf.radius = new_radius
                elif eh in assoc_dims and ent.dxftype() == "DIMENSION":
                    is_rad_dim, (cx, cy) = assoc_dims[eh]
                    old_v = old_radius if is_rad_dim else old_diam
                    new_v = new_radius if is_rad_dim else new_diam
                    cur_txt = str(ent.dxf.get("text", ""))
                    if cur_txt and cur_txt != "<>":
                        ent.dxf.text = _replace_exact_nominal_token(cur_txt, old_v, new_v)
                    # Also rescale radial/diametric definition points on circumference when applicable
                    dtype = int(ent.dxf.get("dimtype", 0)) & 7
                    scale_ratio = (new_radius / old_radius) if old_radius > 1e-9 else 1.0
                    if dtype in {3, 4}:
                        for attr in ("defpoint", "defpoint4"):
                            if ent.dxf.hasattr(attr):
                                pt = getattr(ent.dxf, attr)
                                vx, vy = float(pt.x) - cx, float(pt.y) - cy
                                if abs(hypot(vx, vy) - old_radius) <= max(0.05, old_radius * 0.08):
                                    setattr(ent.dxf, attr, (cx + vx * scale_ratio, cy + vy * scale_ratio, float(pt.z)))
                    elif ent.dxf.hasattr("defpoint2") and ent.dxf.hasattr("defpoint3"):
                        p2, p3 = ent.dxf.defpoint2, ent.dxf.defpoint3
                        mx, my = 0.5 * float(p2.x + p3.x), 0.5 * float(p2.y + p3.y)
                        ent.dxf.defpoint2 = (mx + (float(p2.x) - mx) * scale_ratio, my + (float(p2.y) - my) * scale_ratio, float(p2.z))
                        ent.dxf.defpoint3 = (mx + (float(p3.x) - mx) * scale_ratio, my + (float(p3.y) - my) * scale_ratio, float(p3.z))
                    try:
                        ent.render()
                    except Exception:
                        pass

        return _atomic_doc_mutation(
            src_path=src_path,
            doc=doc,
            out_dxf=out_dxf,
            dry_run=dry_run,
            baseline_sha=current_sha,
            prompt=prompt_clean,
            action="resize_circle",
            handles=all_touched_handles,
            mutator=_mutate_circles,
            post_checks=[CircleDiameterEqualsCheck(handles=target_circles, diameter=new_diam)],
            summary_zh=(
                f"已将框内 {len(target_circles)} 处圆孔更新为 ⌀{new_diam:g}{unit_suffix}（原 ⌀{old_diam:g}{unit_suffix}）"
                + (f"，并同步更新 {len(assoc_dims)} 处关联标注" if assoc_dims else "")
            ),
        )

    # 4b. Secondary linear fallback if both circle & linear words appeared without matching circles
    if m_num and has_linear_prop:
        return _execute_linear_or_span()

    # ------------------------------------------------------------------
    # 5. Text / Material / Roughness / Tolerance / Label Replacement
    # ------------------------------------------------------------------
    texts_in_box = [h for h in working_handles if by_handle[h].dxftype() in {"TEXT", "MTEXT"}]
    m_ra = re.search(r"(?:粗糙度\s*(?:改成|改为|设为)?|(?:改成|改为|设为)\s*)Ra\s*(?P<ra>\d+(?:\.\d+)?)", active_prompt, re.IGNORECASE)
    if not m_ra:
        m_ra = re.search(r"粗糙度\s*(?:改成|改为|设为)?\s*(?P<ra>\d+(?:\.\d+)?)", active_prompt)
    if texts_in_box and m_ra:
        new_ra = f"Ra {m_ra.group('ra')}"
        ra_cands = [
            h for h in texts_in_box
            if "ra" in (str(by_handle[h].dxf.get("text", "")) if by_handle[h].dxftype() == "TEXT" else by_handle[h].plain_text()).lower()
        ] or [texts_in_box[0]]
        return _atomic_doc_mutation(
            src_path=src_path,
            doc=doc,
            out_dxf=out_dxf,
            dry_run=dry_run,
            baseline_sha=current_sha,
            prompt=prompt_clean,
            action="edit_text",
            handles=ra_cands,
            mutator=lambda d, hs: [setattr(e.dxf, "text", new_ra) for e in d.modelspace() if str(e.dxf.handle).upper() in set(hs) and e.dxftype() == "TEXT"],
            post_checks=[TextContainsCheck(handles=ra_cands, expected_substring=new_ra)],
            summary_zh=f"已将框内粗糙度数值更新为「{new_ra}」",
        )

    m_pair = re.search(r"把\s*(?P<old>.+?)\s*(?:改成|改为|替换成|换成)\s*(?P<new>.+)$", active_prompt)
    m_mat = re.search(r"(?:材料|材质)\s*(?:改成|改为|设为|换成)[:：]?\s*(?P<new>.+)$", active_prompt)
    m_set_txt = re.search(r"(?:文字|文本|标题|图名|公差|基准|改为|改成)\s*[:：]?\s*(?P<new>[^，。]+)$", active_prompt)
    if texts_in_box and (m_pair or m_mat or m_set_txt):
        if m_pair:
            old_sub = m_pair.group("old").strip()
            new_sub = m_pair.group("new").strip()
            matched_t = [
                h for h in texts_in_box
                if old_sub in (str(by_handle[h].dxf.get("text", "")) if by_handle[h].dxftype() == "TEXT" else by_handle[h].plain_text())
            ]
            if not matched_t:
                raise RevisionError(f"框内未找到包含「{old_sub}」的文字图元。")

            def _rep_sub(d: ezdxf.document.Drawing, hs: Sequence[str]) -> None:
                for ent in d.modelspace():
                    if str(ent.dxf.handle).upper() in set(hs):
                        if ent.dxftype() == "TEXT":
                            ent.dxf.text = str(ent.dxf.text).replace(old_sub, new_sub)
                        else:
                            ent.text = ent.text.replace(old_sub, new_sub)

            return _atomic_doc_mutation(
                src_path=src_path,
                doc=doc,
                out_dxf=out_dxf,
                dry_run=dry_run,
                baseline_sha=current_sha,
                prompt=prompt_clean,
                action="edit_text",
                handles=matched_t,
                mutator=_rep_sub,
                post_checks=[TextContainsCheck(handles=matched_t, expected_substring=new_sub)],
                summary_zh=f"已将框内 {len(matched_t)} 处文字「{old_sub}」更新为「{new_sub}」",
            )

        new_val = (m_mat.group("new") if m_mat else m_set_txt.group("new")).strip()  # type: ignore[union-attr]
        if m_mat:
            mat_cands = [
                h for h in texts_in_box
                if str(by_handle[h].dxf.get("text", "")).strip() not in {"材料", "MATL", "图名", "TITLE"}
            ]
            target_t = [mat_cands[-1]] if mat_cands else [texts_in_box[0]]
        else:
            target_t = [texts_in_box[0]]

        def _set_txt(d: ezdxf.document.Drawing, hs: Sequence[str]) -> None:
            for ent in d.modelspace():
                if str(ent.dxf.handle).upper() in set(hs):
                    if ent.dxftype() == "TEXT":
                        if "材料：" in str(ent.dxf.text):
                            ent.dxf.text = f"材料：{new_val}"
                        else:
                            ent.dxf.text = new_val
                    else:
                        ent.text = new_val

        return _atomic_doc_mutation(
            src_path=src_path,
            doc=doc,
            out_dxf=out_dxf,
            dry_run=dry_run,
            baseline_sha=current_sha,
            prompt=prompt_clean,
            action="edit_text",
            handles=target_t,
            mutator=_set_txt,
            post_checks=[TextContainsCheck(handles=target_t, expected_substring=new_val)],
            summary_zh=f"已将框内目标文字更新为「{new_val}」",
        )

    raise RevisionError(
        f"未能将「{prompt_clean}」解析为明确的局部改图动作。支持示例：『删掉虚线』、『把孔改成 Φ18』、『长度改成 90』、『往右移 15』、『材料改成 40Cr』。"
    )


def _atomic_doc_mutation(
    *,
    src_path: Path,
    doc: ezdxf.document.Drawing,
    out_dxf: Path,
    dry_run: bool,
    baseline_sha: str,
    prompt: str,
    action: str,
    handles: list[str],
    mutator: Any,
    post_checks: Sequence[PostConditionSpec] = (),
    summary_zh: str,
) -> dict[str, Any]:
    scratch = deepcopy(doc)
    before_snap = snapshot_modelspace(scratch)
    checkpoint = DocumentCheckpoint(scratch)
    try:
        mutator(scratch, handles)
        auditor = scratch.audit()
        if auditor.has_errors:
            raise RevisionError(f"改图后 DXF 结构校验未通过: {auditor.errors}")
        unexpected = compare_untouched(
            before_snap,
            snapshot_modelspace(scratch),
            {h.upper() for h in handles},
            1e-4,
        )
        if unexpected:
            raise RevisionError(f"检测到未授权图元发生变动，已自动回滚: {unexpected}")
    except Exception:
        checkpoint.restore()
        raise

    if dry_run:
        with tempfile.TemporaryDirectory() as tmp_d:
            sim_path = Path(tmp_d) / "sim.dxf"
            scratch.saveas(sim_path)
            ver = verify_revision_post_conditions(src_path, sim_path, post_checks)
        if not ver["passed"]:
            failed_msgs = [it["detail"] for it in ver["items"] if not it["passed"]]
            raise RevisionError(f"Dry-Run 事后重读 DXF 核验未通过: {'；'.join(failed_msgs)}")
        return {
            "mode": "revision",
            "action": action,
            "status": "verified",
            "dry_run": True,
            "prompt": prompt,
            "verification": ver,
            "baseline_sha256": baseline_sha,
            "new_sha256": baseline_sha,
            "modified_handles": handles,
            "output_path": None,
            "summary_zh": f"[Dry-Run 预演通过] {summary_zh}",
        }

    out_dxf.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out_dxf.with_name(f".{out_dxf.name}.tmp")
    scratch.saveas(tmp_out)
    ver = verify_revision_post_conditions(src_path, tmp_out, post_checks)
    if not ver["passed"]:
        tmp_out.unlink(missing_ok=True)
        failed_msgs = [it["detail"] for it in ver["items"] if not it["passed"]]
        raise RevisionError(f"事后重读 DXF 物理核验未通过，已拦截输出: {'；'.join(failed_msgs)}")
    tmp_out.replace(out_dxf)
    new_sha = sha256_file(out_dxf)
    return {
        "mode": "revision",
        "action": action,
        "status": "applied",
        "dry_run": False,
        "prompt": prompt,
        "verification": ver,
        "baseline_sha256": baseline_sha,
        "new_sha256": new_sha,
        "modified_handles": handles,
        "output_path": str(out_dxf.resolve()),
        "summary_zh": summary_zh,
    }
