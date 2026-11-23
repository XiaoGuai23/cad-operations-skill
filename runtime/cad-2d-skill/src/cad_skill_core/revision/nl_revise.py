"""Conversational natural-language region revision compiler & atomic executor.

Powers the blue-dashed marquee box + floating capsule workflow ("指哪改哪"):
1. Binds strictly to the source DXF's SHA-256 fingerprint;
2. Resolves entities inside the marquee `region_box` and/or `candidate_handles`;
3. Translates Chinese/English natural-language edit prompts into verified,
   checkpoint-guarded DXF mutations (or non-mutating region inspection pins).
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from math import isclose, isfinite
from pathlib import Path
import re
from typing import Any

import ezdxf
from ezdxf import bbox
from ezdxf.math import Matrix44

from .common import RevisionError, sha256_file
from .edits import _DELETE
from .transaction import DocumentCheckpoint
from .workflow import apply_revision, inventory_drawing


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


def _is_question_only(prompt: str) -> bool:
    txt = prompt.strip()
    if not txt:
        return True
    if txt.endswith("?") or txt.endswith("？"):
        return True
    edit_verbs = (
        "删", "去掉", "移除", "清除", "改", "变", "设为", "设置", "调整", "放大", "缩小",
        "移", "偏", "替换", "delete", "remove", "change", "set", "resize", "move", "shift",
    )
    return not any(v in txt.lower() for v in edit_verbs)


def execute_nl_region_revision(
    source_dxf: Path,
    output_dir: Path,
    prompt_text: str,
    *,
    region_box: Sequence[float] | None = None,
    candidate_handles: Sequence[str] | None = None,
    expected_sha256: str | None = None,
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
    msp = doc.modelspace()
    by_handle = {str(e.dxf.handle).upper(): e for e in msp}
    inv_by_handle = {str(item["handle"]).upper(): item for item in inv["entities"]}

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

    # Exclude outer sheet FRAME border from accidental bulk edits unless explicitly named
    prompt_clean = prompt_text.strip()
    non_frame_handles = [
        h for h in selected_handles if str(by_handle[h].dxf.get("layer", "")).upper() != "FRAME"
    ]
    working_handles = non_frame_handles if non_frame_handles else selected_handles

    # ------------------------------------------------------------------
    # 0. Question / Inspection Pin Mode (no mutation)
    # ------------------------------------------------------------------
    if _is_question_only(prompt_clean):
        type_summary: dict[str, int] = {}
        radii: list[float] = []
        texts_found: list[str] = []
        for h in working_handles:
            ent = by_handle[h]
            dt = ent.dxftype()
            type_summary[dt] = type_summary.get(dt, 0) + 1
            if dt in {"CIRCLE", "ARC"}:
                radii.append(round(float(ent.dxf.radius), 2))
            elif dt == "TEXT":
                texts_found.append(str(ent.dxf.get("text", "")))
        parts_desc = [f"{k}×{v}" for k, v in sorted(type_summary.items())]
        extra = ""
        if radii:
            extra += f"；圆孔直径: {', '.join(f'⌀{2*r:g}' for r in sorted(set(radii))[:4])}"
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

    if not working_handles:
        raise RevisionError("框选区域内未命中可修改的 CAD 图元，请重新拉取蓝色虚线框覆盖目标特征。")

    out_dir = Path(output_dir).resolve()
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    out_dxf = out_dir / "revised.dxf"
    plan_file = out_dir.parent / "nl-revision-plan.json"

    # ------------------------------------------------------------------
    # 1. Delete / Remove Entities ("删掉虚线" / "删掉框里的孔" / "删除标注")
    # ------------------------------------------------------------------
    if any(k in prompt_clean.lower() for k in ("删", "去掉", "移除", "清除", "delete", "remove")):
        targets = list(working_handles)
        pl = prompt_clean.lower()
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

        # Use standard verified apply_revision when all targets are in _DELETE
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
            import json
            plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            rev_res = apply_revision(src_path, plan_file, out_dir, dry_run=dry_run)
            new_sha = sha256_file(Path(rev_res["output_path"])) if not dry_run else current_sha
            return {
                "mode": "revision",
                "action": "delete_entities",
                "status": rev_res["status"],
                "dry_run": dry_run,
                "prompt": prompt_clean,
                "baseline_sha256": current_sha,
                "new_sha256": new_sha,
                "modified_handles": targets,
                "output_path": rev_res.get("output_path"),
                "summary_zh": f"已精准删除框内 {len(targets)} 个目标图元",
            }

        # Atomic checkpoint deletion when HATCH or other entities are included
        return _atomic_doc_mutation(
            doc=doc,
            out_dxf=out_dxf,
            dry_run=dry_run,
            baseline_sha=current_sha,
            prompt=prompt_clean,
            action="delete_entities",
            handles=targets,
            mutator=lambda d, hs: [d.modelspace().delete_entity(e) for e in list(d.modelspace()) if str(e.dxf.handle).upper() in set(hs)],
            summary_zh=f"已精准删除框内 {len(targets)} 个目标图元",
        )

    # ------------------------------------------------------------------
    # 2. Move / Offset Entities ("往右移 15" / "向上平移 10")
    # ------------------------------------------------------------------
    m_move = re.search(
        r"(?:往|向)?\s*(?P<dir>左|右|上|下|left|right|up|down)\s*(?:平移|移动|移|偏(?:移)?)?\s*(?P<val>\d+(?:\.\d+)?)",
        prompt_clean,
        re.IGNORECASE,
    )
    if m_move:
        d_tok = m_move.group("dir").lower()
        dist = float(m_move.group("val"))
        dx = -dist if d_tok in {"左", "left"} else (dist if d_tok in {"右", "right"} else 0.0)
        dy = dist if d_tok in {"上", "up"} else (-dist if d_tok in {"下", "down"} else 0.0)
        mat = Matrix44.translate(dx, dy, 0.0)
        return _atomic_doc_mutation(
            doc=doc,
            out_dxf=out_dxf,
            dry_run=dry_run,
            baseline_sha=current_sha,
            prompt=prompt_clean,
            action="translate_entities",
            handles=working_handles,
            mutator=lambda d, hs: [e.transform(mat) for e in d.modelspace() if str(e.dxf.handle).upper() in set(hs)],
            summary_zh=f"已将框内 {len(working_handles)} 个图元平移 ({dx:+g}, {dy:+g}) mm",
        )

    # ------------------------------------------------------------------
    # 3. Change Hole / Circle Diameter or Radius ("把孔改成 Φ18" / "半径改为 12")
    # ------------------------------------------------------------------
    circles_in_box = [
        h for h in working_handles
        if by_handle[h].dxftype() in {"CIRCLE", "ARC"} and str(by_handle[h].dxf.get("layer", "")).upper() != "CENTER"
    ]
    m_num = re.search(r"[⌀∅ΦφRr]?\s*(?P<val>\d+(?:\.\d+)?)\s*(?:mm|毫米)?", prompt_clean)
    if circles_in_box and m_num and any(
        k in prompt_clean.lower() for k in ("孔", "圆", "径", "φ", "⌀", "∅", "r", "改", "设为", "diameter", "radius")
    ):
        raw_val = float(m_num.group("val"))
        if raw_val <= 0:
            raise RevisionError("目标孔径/半径必须大于 0")
        is_radius = bool(re.search(r"半径|radius|(?<![A-Za-z])R\s*\d", prompt_clean, re.IGNORECASE)) and not any(
            k in prompt_clean for k in ("直径", "孔径", "Φ", "⌀", "∅")
        )
        new_radius = raw_val if is_radius else raw_val / 2.0

        # If multiple concentric circles exist (e.g. flange outer circle + bolt holes) and user says "孔" / "螺栓孔",
        # target the smaller circle group (bolt holes or bore)!
        radii_map = {h: float(by_handle[h].dxf.radius) for h in circles_in_box}
        if len(set(round(r, 4) for r in radii_map.values())) > 1 and any(k in prompt_clean for k in ("螺栓孔", "安装孔", "小孔", "孔")):
            min_r = min(radii_map.values())
            target_circles = [h for h, r in radii_map.items() if isclose(r, min_r, abs_tol=1e-3)]
        else:
            target_circles = circles_in_box

        old_diam = round(2.0 * radii_map[target_circles[0]], 4)
        new_diam = round(2.0 * new_radius, 4)

        def _mutate_circles(d: ezdxf.document.Drawing, hs: Sequence[str]) -> None:
            hset = set(hs)
            for ent in d.modelspace():
                if str(ent.dxf.handle).upper() in hset:
                    ent.dxf.radius = new_radius
                elif ent.dxftype() == "DIMENSION":
                    txt = str(ent.dxf.get("text", ""))
                    old_s = f"{old_diam:g}"
                    new_s = f"{new_diam:g}"
                    if old_s in txt:
                        ent.dxf.text = txt.replace(old_s, new_s)
                        try:
                            ent.render()
                        except Exception:
                            pass

        return _atomic_doc_mutation(
            doc=doc,
            out_dxf=out_dxf,
            dry_run=dry_run,
            baseline_sha=current_sha,
            prompt=prompt_clean,
            action="resize_circle",
            handles=target_circles,
            mutator=_mutate_circles,
            summary_zh=f"已将框内 {len(target_circles)} 处圆弧/圆孔更新为 ⌀{new_diam:g} mm（原 ⌀{old_diam:g} mm）并同步关联标注",
        )

    # ------------------------------------------------------------------
    # 4. Change Line Length or Polyline Span ("长度改成 90" / "宽度改为 140")
    # ------------------------------------------------------------------
    if m_num and any(k in prompt_clean.lower() for k in ("长", "宽", "高", "跨度", "length", "width", "height")):
        new_span = float(m_num.group("val"))
        if new_span <= 0:
            raise RevisionError("目标长度/跨度必须大于 0")
        lines_in_box = [h for h in working_handles if by_handle[h].dxftype() == "LINE" and str(by_handle[h].dxf.get("layer", "")).upper() == "OUTLINE"]
        polys_in_box = [h for h in working_handles if by_handle[h].dxftype() == "LWPOLYLINE" and str(by_handle[h].dxf.get("layer", "")).upper() == "OUTLINE"]
        import json

        if polys_in_box:
            axis = "y" if any(k in prompt_clean.lower() for k in ("高", "竖", "height")) else "x"
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
            return {
                "mode": "revision",
                "action": "resize_fixed_edge",
                "status": rev_res["status"],
                "dry_run": dry_run,
                "prompt": prompt_clean,
                "baseline_sha256": current_sha,
                "new_sha256": new_sha,
                "modified_handles": [polys_in_box[0]],
                "output_path": rev_res.get("output_path"),
                "summary_zh": f"已将轮廓 #{polys_in_box[0]} 的 {axis.upper()} 向跨度调整为 {new_span:g} mm",
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
            return {
                "mode": "revision",
                "action": "set_length",
                "status": rev_res["status"],
                "dry_run": dry_run,
                "prompt": prompt_clean,
                "baseline_sha256": current_sha,
                "new_sha256": new_sha,
                "modified_handles": [lines_in_box[0]],
                "output_path": rev_res.get("output_path"),
                "summary_zh": f"已将线段 #{lines_in_box[0]} 长度调整为 {new_span:g} mm",
            }

    # ------------------------------------------------------------------
    # 5. Text / Material / Roughness / Tolerance / Label Replacement
    # ------------------------------------------------------------------
    texts_in_box = [h for h in working_handles if by_handle[h].dxftype() in {"TEXT", "MTEXT"}]
    m_ra = re.search(r"(?:粗糙度\s*(?:改成|改为|设为)?|(?:改成|改为|设为)\s*)Ra\s*(?P<ra>\d+(?:\.\d+)?)", prompt_clean, re.IGNORECASE)
    if not m_ra:
        m_ra = re.search(r"粗糙度\s*(?:改成|改为|设为)?\s*(?P<ra>\d+(?:\.\d+)?)", prompt_clean)
    if texts_in_box and m_ra:
        new_ra = f"Ra {m_ra.group('ra')}"
        ra_cands = [
            h for h in texts_in_box
            if "ra" in (str(by_handle[h].dxf.get("text", "")) if by_handle[h].dxftype() == "TEXT" else by_handle[h].plain_text()).lower()
        ] or [texts_in_box[0]]
        return _atomic_doc_mutation(
            doc=doc,
            out_dxf=out_dxf,
            dry_run=dry_run,
            baseline_sha=current_sha,
            prompt=prompt_clean,
            action="edit_text",
            handles=ra_cands,
            mutator=lambda d, hs: [setattr(e.dxf, "text", new_ra) for e in d.modelspace() if str(e.dxf.handle).upper() in set(hs) and e.dxftype() == "TEXT"],
            summary_zh=f"已将框内粗糙度数值更新为「{new_ra}」",
        )

    m_pair = re.search(r"把\s*(?P<old>.+?)\s*(?:改成|改为|替换成|换成)\s*(?P<new>.+)$", prompt_clean)
    m_mat = re.search(r"(?:材料|材质)\s*(?:改成|改为|设为|换成)[:：]?\s*(?P<new>.+)$", prompt_clean)
    m_set_txt = re.search(r"(?:文字|文本|标题|图名|公差|基准|改为|改成)\s*[:：]?\s*(?P<new>[^，。]+)$", prompt_clean)
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
                doc=doc,
                out_dxf=out_dxf,
                dry_run=dry_run,
                baseline_sha=current_sha,
                prompt=prompt_clean,
                action="edit_text",
                handles=matched_t,
                mutator=_rep_sub,
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
            doc=doc,
            out_dxf=out_dxf,
            dry_run=dry_run,
            baseline_sha=current_sha,
            prompt=prompt_clean,
            action="edit_text",
            handles=target_t,
            mutator=_set_txt,
            summary_zh=f"已将框内目标文字更新为「{new_val}」",
        )

    raise RevisionError(
        f"未能将「{prompt_clean}」解析为明确的局部改图动作。支持示例：『删掉虚线』、『把孔改成 Φ18』、『长度改成 90』、『往右移 15』、『材料改成 40Cr』。"
    )


def _atomic_doc_mutation(
    *,
    doc: ezdxf.document.Drawing,
    out_dxf: Path,
    dry_run: bool,
    baseline_sha: str,
    prompt: str,
    action: str,
    handles: list[str],
    mutator: Any,
    summary_zh: str,
) -> dict[str, Any]:
    scratch = deepcopy(doc)
    checkpoint = DocumentCheckpoint(scratch)
    try:
        mutator(scratch, handles)
        auditor = scratch.audit()
        if auditor.has_errors:
            raise RevisionError(f"改图后 DXF 结构校验未通过: {auditor.errors}")
    except Exception:
        checkpoint.restore()
        raise

    if dry_run:
        return {
            "mode": "revision",
            "action": action,
            "status": "verified",
            "dry_run": True,
            "prompt": prompt,
            "baseline_sha256": baseline_sha,
            "new_sha256": baseline_sha,
            "modified_handles": handles,
            "output_path": None,
            "summary_zh": f"[Dry-Run 预演通过] {summary_zh}",
        }

    out_dxf.parent.mkdir(parents=True, exist_ok=True)
    scratch.saveas(out_dxf)
    new_sha = sha256_file(out_dxf)
    return {
        "mode": "revision",
        "action": action,
        "status": "applied",
        "dry_run": False,
        "prompt": prompt,
        "baseline_sha256": baseline_sha,
        "new_sha256": new_sha,
        "modified_handles": handles,
        "output_path": str(out_dxf.resolve()),
        "summary_zh": summary_zh,
    }
