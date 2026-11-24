"""Extract layer-grouped 2D vector geometry and ACI colors from DXF for interactive CAD canvas."""

from __future__ import annotations

from math import cos, hypot, pi, sin
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf import bbox
from ezdxf.lldxf.tagwriter import TagCollector
from ezdxf.render import hatching

from cad_skill_core.revision.common import _plain, sha256_file, snapshot_modelspace, values_close

# Standard AutoCAD Color Index (ACI 1..9 + common indices) for Dark & Light CAD themes
_ACI_DARK_HEX: dict[int, str] = {
    1: "#ff4d4f",  # Red (Centerlines / Alerts)
    2: "#facc15",  # Yellow (Dimensions)
    3: "#4ade80",  # Green
    4: "#22d3ee",  # Cyan (Hidden lines)
    5: "#60a5fa",  # Blue
    6: "#e879f9",  # Magenta
    7: "#f8fafc",  # White on dark background (Outlines / Text)
    8: "#94a3b8",  # Grey (Hatch / Grid)
    9: "#cbd5e1",  # Light Grey
}

_ACI_LIGHT_HEX: dict[int, str] = {
    1: "#dc2626",
    2: "#b45309",
    3: "#15803d",
    4: "#0e7490",
    5: "#1d4ed8",
    6: "#a21caf",
    7: "#0f172a",  # Crisp black/slate on white paper
    8: "#64748b",
    9: "#94a3b8",
}


def _aci_colors(aci: int) -> tuple[str, str]:
    c = abs(int(aci)) if aci else 7
    return _ACI_DARK_HEX.get(c, "#38bdf8"), _ACI_LIGHT_HEX.get(c, "#1e293b")


def _extract_primitive_items(
    ent: Any,
    root_handle: str,
    fallback_layer: str,
    out: list[dict[str, Any]],
    depth: int = 0,
) -> None:
    if depth > 4 or len(out) >= 25_000:
        return
    dxftype = ent.dxftype()
    layer = str(ent.dxf.get("layer", fallback_layer) or fallback_layer)
    if layer == "0" and fallback_layer != "0":
        layer = fallback_layer

    if dxftype == "LINE":
        s, e = ent.dxf.start, ent.dxf.end
        out.append(
            {
                "k": "seg",
                "h": root_handle,
                "l": layer,
                "d": [round(float(s.x), 3), round(float(s.y), 3), round(float(e.x), 3), round(float(e.y), 3)],
            }
        )
    elif dxftype == "CIRCLE":
        c = ent.dxf.center
        r = float(ent.dxf.radius)
        out.append(
            {
                "k": "circle",
                "h": root_handle,
                "l": layer,
                "d": [round(float(c.x), 3), round(float(c.y), 3), round(r, 3)],
            }
        )
    elif dxftype == "ARC":
        c = ent.dxf.center
        r = float(ent.dxf.radius)
        sa = float(ent.dxf.start_angle)
        ea = float(ent.dxf.end_angle)
        out.append(
            {
                "k": "arc",
                "h": root_handle,
                "l": layer,
                "d": [round(float(c.x), 3), round(float(c.y), 3), round(r, 3), round(sa, 3), round(ea, 3)],
            }
        )
    elif dxftype == "LWPOLYLINE":
        has_bulge = any(abs(pt[4]) > 1e-7 for pt in ent.get_points("xyseb"))
        if has_bulge:
            for sub in ent.virtual_entities():
                _extract_primitive_items(sub, root_handle, layer, out, depth + 1)
        else:
            pts = [[round(float(x), 3), round(float(y), 3)] for x, y in ent.get_points("xy")]
            if len(pts) >= 2:
                out.append(
                    {
                        "k": "poly",
                        "h": root_handle,
                        "l": layer,
                        "pts": pts,
                        "c": bool(ent.closed),
                    }
                )
    elif dxftype == "POLYLINE":
        for sub in ent.virtual_entities():
            _extract_primitive_items(sub, root_handle, layer, out, depth + 1)
    elif dxftype in {"TEXT", "MTEXT"}:
        align_mode = ""
        if dxftype == "TEXT":
            txt = str(ent.dxf.get("text", ""))
            ins = ent.get_placement()[1] or ent.dxf.insert
            height = float(ent.dxf.get("height", 3.5))
            rot = float(ent.dxf.get("rotation", 0.0))
            halign = int(ent.dxf.get("halign", 0))
            valign = int(ent.dxf.get("valign", 0))
            if halign in {1, 4} and valign == 2:
                align_mode = "mc"
            elif halign in {1, 4}:
                align_mode = "c"
        else:
            txt = ent.plain_text()
            ins = ent.dxf.insert
            height = float(ent.dxf.get("char_height", 3.5))
            rot = float(ent.dxf.get("rotation", 0.0))
        if txt.strip():
            item_dict: dict[str, Any] = {
                "k": "text",
                "h": root_handle,
                "l": layer,
                "x": round(float(ins.x), 3),
                "y": round(float(ins.y), 3),
                "s": round(max(height, 1.2), 3),
                "r": round(rot, 2),
                "t": txt,
            }
            if align_mode:
                item_dict["a"] = align_mode
            out.append(item_dict)
    elif dxftype in {"DIMENSION", "INSERT", "LEADER", "MULTILEADER"}:
        try:
            for sub in ent.virtual_entities():
                _extract_primitive_items(sub, root_handle, layer, out, depth + 1)
        except Exception:
            pass
    elif dxftype == "HATCH":
        # Render hatch boundary + pattern lines cleanly
        try:
            for line_start, line_end in hatching.hatch_entity(ent):
                if len(out) >= 25_000:
                    break
                out.append(
                    {
                        "k": "seg",
                        "h": root_handle,
                        "l": layer,
                        "d": [
                            round(float(line_start.x), 3),
                            round(float(line_start.y), 3),
                            round(float(line_end.x), 3),
                            round(float(line_end.y), 3),
                        ],
                    }
                )
        except Exception:
            pass
    elif dxftype == "SOLID":
        pts = []
        for idx in ("vtx0", "vtx1", "vtx3", "vtx2"):
            if ent.dxf.hasattr(idx):
                v = getattr(ent.dxf, idx)
                pts.append([round(float(v.x), 3), round(float(v.y), 3)])
        if len(pts) >= 3:
            out.append({"k": "solid", "h": root_handle, "l": layer, "pts": pts})
    elif dxftype == "SPLINE":
        try:
            fl = ent.flattening(0.5)
            pts = [[round(float(p.x), 3), round(float(p.y), 3)] for p in fl]
            if len(pts) >= 2:
                out.append({"k": "poly", "h": root_handle, "l": layer, "pts": pts, "c": bool(ent.closed)})
        except Exception:
            pass
    elif dxftype == "ELLIPSE":
        try:
            pts = [
                [round(float(p.x), 3), round(float(p.y), 3)]
                for p in ent.vertices(ent.params(36))
            ]
            if len(pts) >= 2:
                out.append({"k": "poly", "h": root_handle, "l": layer, "pts": pts, "c": False})
        except Exception:
            pass


def _describe_root_entity(ent: Any) -> str:
    dt = ent.dxftype()
    if dt == "CIRCLE":
        c = ent.dxf.center
        r = float(ent.dxf.radius)
        return f"圆孔/圆 ⌀{2.0 * r:.2f} @ ({float(c.x):.1f}, {float(c.y):.1f})"
    if dt == "ARC":
        c = ent.dxf.center
        r = float(ent.dxf.radius)
        return f"圆弧 R{r:.2f} @ ({float(c.x):.1f}, {float(c.y):.1f})"
    if dt == "LINE":
        s, e = ent.dxf.start, ent.dxf.end
        ln = hypot(float(e.x) - float(s.x), float(e.y) - float(s.y))
        return f"直线 L={ln:.2f}"
    if dt == "LWPOLYLINE":
        pts = list(ent.get_points("xy"))
        return f"{'闭合' if ent.closed else '开放'}多段线 ({len(pts)} 顶点)"
    if dt == "DIMENSION":
        txt = str(ent.dxf.get("text", "")).strip()
        if not txt or txt == "<>":
            try:
                txt = f"{float(ent.get_measurement()):.2f}"
            except Exception:
                txt = "关联标注"
        return f"尺寸标注「{txt}」"
    if dt in {"TEXT", "MTEXT"}:
        txt = str(ent.dxf.get("text", "")).strip() if dt == "TEXT" else ent.plain_text().strip()
        return f"文字「{txt[:22]}」"
    return dt


def extract_dxf_vector_scene(dxf_path: Path) -> dict[str, Any]:
    """Read a DXF and return layer metadata, millimetre extents, entity metadata, and interactive vector primitives."""

    resolved = Path(dxf_path).resolve(strict=True)
    doc = ezdxf.readfile(resolved)
    msp = doc.modelspace()

    layer_defs: dict[str, dict[str, Any]] = {}
    for lyr in doc.layers:
        lname = str(lyr.dxf.name)
        aci = int(lyr.color)
        dark_hex, light_hex = _aci_colors(aci)
        ltype = str(lyr.dxf.get("linetype", "CONTINUOUS")).upper()
        layer_defs[lname] = {
            "name": lname,
            "aci_color": aci,
            "hex_dark": dark_hex,
            "hex_light": light_hex,
            "linetype": ltype,
            "count": 0,
        }

    primitives: list[dict[str, Any]] = []
    entities_meta: dict[str, dict[str, Any]] = {}
    for root_ent in msp:
        if root_ent.dxftype() == "MESH":
            continue
        root_handle = str(root_ent.dxf.get("handle", "")).upper()
        root_layer = str(root_ent.dxf.get("layer", "0") or "0")
        before_len = len(primitives)
        _extract_primitive_items(root_ent, root_handle, root_layer, primitives)
        added = len(primitives) - before_len
        if added > 0:
            if root_handle:
                entities_meta[root_handle] = {
                    "t": root_ent.dxftype(),
                    "l": root_layer,
                    "desc": _describe_root_entity(root_ent),
                }
            for item in primitives[before_len:]:
                ln = item["l"]
                if ln not in layer_defs:
                    dh, lh = _aci_colors(7)
                    layer_defs[ln] = {
                        "name": ln,
                        "aci_color": 7,
                        "hex_dark": dh,
                        "hex_light": lh,
                        "linetype": "CONTINUOUS",
                        "count": 0,
                    }
                layer_defs[ln]["count"] += 1

    ext = bbox.extents(msp)
    if ext.has_data and ext.extmax.x > ext.extmin.x - 1e-6:
        bounds_mm = [
            round(float(ext.extmin.x), 3),
            round(float(ext.extmin.y), 3),
            round(float(ext.extmax.x), 3),
            round(float(ext.extmax.y), 3),
        ]
    else:
        bounds_mm = [0.0, 0.0, 420.0, 297.0]

    used_layers = [info for info in layer_defs.values() if info["count"] > 0]
    used_layers.sort(key=lambda item: (item["name"] == "FRAME", item["name"]))

    return {
        "dxf_name": resolved.name,
        "dxf_path": str(resolved),
        "sha256": sha256_file(resolved),
        "units": int(doc.header.get("$INSUNITS", 4)),
        "bounds_mm": bounds_mm,
        "layers": used_layers,
        "entity_count": len(entities_meta),
        "entities_meta": entities_meta,
        "primitive_count": len(primitives),
        "primitives": primitives,
    }


def _snapshot_block_tree(
    doc: ezdxf.document.Drawing,
    block_name: str,
    cache: dict[str, list[Any]],
    seen: set[str] | None = None,
) -> list[Any]:
    """Recursively snapshot entity tags of a referenced block definition (excluding volatile handles)."""
    if not block_name:
        return []
    key = block_name.casefold()
    if key in cache:
        return cache[key]
    visited = set(seen or ())
    if key in visited or block_name not in doc.blocks:
        return []
    visited.add(key)
    blk = doc.blocks.get(block_name)
    if blk is None:
        return []
    items: list[Any] = []
    for child in blk:
        if not child.is_alive:
            continue
        tags = [
            [tag.code, _plain(tag.value)]
            for tag in TagCollector.dxftags(child)
            if tag.code not in {5, 330}
        ]
        nested: list[Any] = []
        if child.dxftype() == "INSERT":
            nested = _snapshot_block_tree(doc, str(child.dxf.get("name", "")), cache, visited)
        elif child.dxftype() == "DIMENSION":
            nested = _snapshot_block_tree(doc, str(child.dxf.get("geometry", "")), cache, visited)
        items.append([child.dxftype(), tags, nested])
    cache[key] = items
    return items


def _referenced_block_signature(
    doc: ezdxf.document.Drawing,
    ent: Any,
    cache: dict[str, list[Any]],
) -> list[Any]:
    dt = ent.dxftype()
    if dt == "INSERT":
        return _snapshot_block_tree(doc, str(ent.dxf.get("name", "")), cache)
    if dt == "DIMENSION":
        return _snapshot_block_tree(doc, str(ent.dxf.get("geometry", "")), cache)
    return []


def compute_dxf_vector_diff(
    before_dxf_path: Path | str,
    after_dxf_path: Path | str,
    *,
    tolerance: float = 1e-4,
) -> dict[str, Any]:
    """Compute handle-level & vector-primitive red/green overlay diff between two DXF files (including referenced block edits)."""
    before_resolved = Path(before_dxf_path).expanduser().resolve(strict=True)
    after_resolved = Path(after_dxf_path).expanduser().resolve(strict=True)

    before_doc = ezdxf.readfile(before_resolved)
    after_doc = ezdxf.readfile(after_resolved)

    before_snap = {k.upper(): v for k, v in snapshot_modelspace(before_doc).items()}
    after_snap = {k.upper(): v for k, v in snapshot_modelspace(after_doc).items()}

    before_ents = {str(e.dxf.handle).upper(): e for e in before_doc.modelspace() if e.is_alive}
    after_ents = {str(e.dxf.handle).upper(): e for e in after_doc.modelspace() if e.is_alive}

    before_blk_cache: dict[str, list[Any]] = {}
    after_blk_cache: dict[str, list[Any]] = {}

    added_handles = sorted(h for h in after_snap if h not in before_snap)
    deleted_handles = sorted(h for h in before_snap if h not in after_snap)
    modified_handles = sorted(
        h
        for h in (before_snap.keys() & after_snap.keys())
        if (
            not values_close(before_snap[h], after_snap[h], tolerance)
            or not values_close(
                _referenced_block_signature(before_doc, before_ents[h], before_blk_cache),
                _referenced_block_signature(after_doc, after_ents[h], after_blk_cache),
                tolerance,
            )
        )
    )
    untouched_count = len(before_snap.keys() & after_snap.keys()) - len(modified_handles)

    # Extract red ghost primitives for deleted + pre-edit modified entities
    ghost_before_primitives: list[dict[str, Any]] = []
    for h in (*deleted_handles, *modified_handles):
        ent = before_ents.get(h)
        if ent is None or ent.dxftype() == "MESH":
            continue
        _extract_primitive_items(ent, h, str(ent.dxf.get("layer", "0") or "0"), ghost_before_primitives)

    change_items: list[dict[str, Any]] = []
    for h in modified_handles:
        b_ent, a_ent = before_ents.get(h), after_ents.get(h)
        change_items.append(
            {
                "handle": h,
                "status": "modified",
                "dxftype": a_ent.dxftype() if a_ent is not None else (b_ent.dxftype() if b_ent is not None else ""),
                "layer": str(a_ent.dxf.get("layer", "0")) if a_ent is not None else "0",
                "before_desc": _describe_root_entity(b_ent) if b_ent is not None else "",
                "after_desc": _describe_root_entity(a_ent) if a_ent is not None else "",
            }
        )
    for h in added_handles:
        a_ent = after_ents.get(h)
        change_items.append(
            {
                "handle": h,
                "status": "added",
                "dxftype": a_ent.dxftype() if a_ent is not None else "",
                "layer": str(a_ent.dxf.get("layer", "0")) if a_ent is not None else "0",
                "before_desc": "",
                "after_desc": _describe_root_entity(a_ent) if a_ent is not None else "",
            }
        )
    for h in deleted_handles:
        b_ent = before_ents.get(h)
        change_items.append(
            {
                "handle": h,
                "status": "deleted",
                "dxftype": b_ent.dxftype() if b_ent is not None else "",
                "layer": str(b_ent.dxf.get("layer", "0")) if b_ent is not None else "0",
                "before_desc": _describe_root_entity(b_ent) if b_ent is not None else "",
                "after_desc": "",
            }
        )

    return {
        "before_dxf": str(before_resolved),
        "after_dxf": str(after_resolved),
        "before_sha256": sha256_file(before_resolved),
        "after_sha256": sha256_file(after_resolved),
        "summary": {
            "added": len(added_handles),
            "deleted": len(deleted_handles),
            "modified": len(modified_handles),
            "untouched": untouched_count,
            "total_changed": len(added_handles) + len(deleted_handles) + len(modified_handles),
        },
        "added_handles": added_handles,
        "deleted_handles": deleted_handles,
        "modified_handles": modified_handles,
        "ghost_before_primitives": ghost_before_primitives,
        "changes": change_items,
    }
