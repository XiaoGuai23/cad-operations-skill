"""Extract layer-grouped 2D vector geometry and ACI colors from DXF for interactive CAD canvas."""

from __future__ import annotations

from math import cos, pi, sin
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf import bbox
from ezdxf.render import hatching

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
        if dxftype == "TEXT":
            txt = str(ent.dxf.get("text", ""))
            ins = ent.get_placement()[1] or ent.dxf.insert
            height = float(ent.dxf.get("height", 3.5))
            rot = float(ent.dxf.get("rotation", 0.0))
        else:
            txt = ent.plain_text()
            ins = ent.dxf.insert
            height = float(ent.dxf.get("char_height", 3.5))
            rot = float(ent.dxf.get("rotation", 0.0))
        if txt.strip():
            out.append(
                {
                    "k": "text",
                    "h": root_handle,
                    "l": layer,
                    "x": round(float(ins.x), 3),
                    "y": round(float(ins.y), 3),
                    "s": round(max(height, 1.2), 3),
                    "r": round(rot, 2),
                    "t": txt,
                }
            )
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


def extract_dxf_vector_scene(dxf_path: Path) -> dict[str, Any]:
    """Read a DXF and return layer metadata, millimetre extents, and interactive vector primitives."""

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
    for root_ent in msp:
        if root_ent.dxftype() == "MESH":
            continue
        root_handle = str(root_ent.dxf.get("handle", ""))
        root_layer = str(root_ent.dxf.get("layer", "0") or "0")
        before_len = len(primitives)
        _extract_primitive_items(root_ent, root_handle, root_layer, primitives)
        added = len(primitives) - before_len
        if added > 0:
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
        "units": int(doc.header.get("$INSUNITS", 4)),
        "bounds_mm": bounds_mm,
        "layers": used_layers,
        "primitive_count": len(primitives),
        "primitives": primitives,
    }
