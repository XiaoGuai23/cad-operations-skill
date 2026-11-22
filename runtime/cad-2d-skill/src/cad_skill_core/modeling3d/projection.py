"""Deterministic 3D -> 2D GB/T multi-view orthographic & sectional projection."""

from __future__ import annotations

import re
from typing import Any, Literal

from cad_skill_core.geometry.entities import (
    CircularEntity,
    DrawingEntity,
    HatchEntity,
    LineEntity,
    PolylineEntity,
)
from cad_skill_core.geometry.parametric import (
    FlangeParameters,
    SlottedBracketParameters,
    SteppedShaftParameters,
    build_flange_geometry,
    build_slotted_bracket_geometry,
    build_stepped_shaft_geometry,
)
from cad_skill_core.layout.models import LayoutOverflowError
from cad_skill_core.layout.plan import plan_layout
from cad_skill_core.schema.models import DrawingSpec
from cad_skill_core.standards.profile import load_standard

from .models import (
    Box3D,
    BoxBoss3D,
    CompoundSolid3D,
    Cylinder3D,
    CylinderBoss3D,
    ExtrudePolygon3D,
    Flange3D,
    Model3DSpec,
    SlottedBracket3D,
    SteppedShaft3D,
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def project_model3d_to_drawing_spec(
    spec_3d: Model3DSpec,
    *,
    sheet_size: Literal["A0", "A1", "A2", "A3", "A4"] = "A3",
    scale: str = "auto",
    title_block_language: Literal["auto", "en", "zh"] = "auto",
) -> DrawingSpec:
    """Project a Model3DSpec into a validated multi-view 2D DrawingSpec."""

    body = spec_3d.body
    sid = body.source_id

    text_blob = " ".join(
        filter(
            None,
            [
                spec_3d.project.name,
                spec_3d.material.text if spec_3d.material else None,
                *(n.text for n in spec_3d.technical_notes),
            ],
        )
    )
    lang: Literal["en", "zh"] = (
        ("zh" if _CJK_RE.search(text_blob) else "en")
        if title_block_language == "auto"
        else title_block_language
    )

    views: list[str]
    entities: list[DrawingEntity]
    view_indices: dict[str, list[int]]
    dims: list[dict[str, Any]]
    params: dict[str, Any] = {"projected_from_3d": body.kind}

    if isinstance(body, Flange3D):
        if body.hub_height <= 0.0 or body.hub_diameter is None:
            part = build_flange_geometry(
                FlangeParameters(
                    outer_diameter=body.outer_diameter,
                    bore_diameter=body.bore_diameter,
                    thickness=body.thickness,
                    bolt_circle_diameter=body.bolt_circle_diameter,
                    hole_diameter=body.hole_diameter,
                    hole_count=body.hole_count,
                ),
                source_id=sid,
                include_right_section=True,
            )
            views, entities, view_indices, dims = (
                part.views,
                part.entities,
                part.view_entity_indices,
                part.dimensions,
            )
            params.update(part.parameters)
        else:
            views, entities, view_indices, dims = _project_hubbed_flange(body, sid)

    elif isinstance(body, SteppedShaft3D):
        part = build_stepped_shaft_geometry(
            SteppedShaftParameters(steps=list(body.steps)),
            source_id=sid,
            include_right_view=True,
        )
        views = list(part.views)
        entities = list(part.entities)
        view_indices = {k: list(v) for k, v in part.view_entity_indices.items()}
        dims = list(part.dimensions)
        if body.bore_diameter > 0:
            rb = body.bore_diameter / 2.0
            total_l = sum(l for l, _ in body.steps)
            for y_h in (-rb, rb):
                view_indices["front"].append(len(entities))
                entities.append(
                    LineEntity(kind="line", layer="HIDDEN", start=(0.0, y_h), end=(total_l, y_h), source_id=sid)
                )
                if "top" in view_indices:
                    view_indices["top"].append(len(entities))
                    entities.append(
                        LineEntity(kind="line", layer="HIDDEN", start=(0.0, y_h), end=(total_l, y_h), source_id=sid)
                    )
            end_view = "left" if "left" in view_indices else "right"
            bore_circle_idx = len(entities)
            view_indices[end_view].append(bore_circle_idx)
            entities.append(
                CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=rb, source_id=sid)
            )
            dims.append(
                {
                    "kind": "diameter",
                    "value": body.bore_diameter,
                    "source_id": sid,
                    "owner_view": end_view,
                    "target_entity_index": bore_circle_idx,
                    "orientation": "diameter",
                }
            )
        params.update(part.parameters)

    elif isinstance(body, SlottedBracket3D):
        part = build_slotted_bracket_geometry(
            SlottedBracketParameters(
                width=body.width,
                height=body.height,
                thickness=body.thickness,
                corner_radius=body.corner_radius,
                slot_length=body.slot_length,
                slot_width=body.slot_width,
                hole_diameter=body.hole_diameter,
                hole_inset=body.hole_inset,
            ),
            source_id=sid,
            include_top_view=True,
            flat_orientation=True,
        )
        views, entities, view_indices, dims = (
            part.views,
            part.entities,
            part.view_entity_indices,
            part.dimensions,
        )
        params.update(part.parameters)

    elif isinstance(body, Box3D):
        views, entities, view_indices, dims = _project_box(body, sid)

    elif isinstance(body, Cylinder3D):
        views, entities, view_indices, dims = _project_cylinder(body, sid)

    elif isinstance(body, ExtrudePolygon3D):
        views, entities, view_indices, dims = _project_extrude_polygon(body, sid)

    elif isinstance(body, CompoundSolid3D):
        views, entities, view_indices, dims = _project_compound_3d(body, sid)

    else:
        raise TypeError(f"unsupported 3D body kind for 2D projection: {type(body)!r}")

    notes_dump = [{"text": n.text, "source_id": n.source_id} for n in spec_3d.technical_notes]
    mat_dump = None
    if spec_3d.material is not None:
        mat_dump = {"text": spec_3d.material.text, "source_id": spec_3d.material.source_id}
        mat_label = f"材料：{spec_3d.material.text}" if lang == "zh" else f"Material: {spec_3d.material.text}"
        if not any(
            n["source_id"] == spec_3d.material.source_id
            and spec_3d.material.text.casefold() in n["text"].casefold()
            for n in notes_dump
        ):
            notes_dump.append({"text": mat_label, "source_id": spec_3d.material.source_id})

    initial_scale = "1:1" if scale == "auto" else scale
    assumptions = list(spec_3d.validation.assumptions)

    spec_2d = DrawingSpec.model_validate(
        {
            "schema_version": "1.0",
            "project": {
                "name": spec_3d.project.name,
                "drawing_number": spec_3d.project.drawing_number,
                "drawing_type": "part",
            },
            "standard": {"family": "GB/T", "units": "mm", "projection": "first-angle"},
            "sheet": {
                "size": sheet_size,
                "orientation": "landscape",
                "scale": initial_scale,
                "title_block_language": lang,
            },
            "requirements": {
                "material": mat_dump,
                "views": views,
                "technical_notes": notes_dump,
            },
            "geometry": {
                "parameters": params,
                "entities": [e.model_dump(mode="json") for e in entities],
                "view_entity_indices": view_indices,
            },
            "dimensions": dims,
            "annotations": [],
            "parts_list": [],
            "provenance": [p.model_dump(mode="json") for p in spec_3d.provenance],
            "validation": {
                "missing": list(spec_3d.validation.missing),
                "conflicts": list(spec_3d.validation.conflicts),
                "assumptions": assumptions,
            },
        }
    )

    if scale == "auto":
        from pathlib import Path

        std_path = Path(__file__).resolve().parents[3] / "knowledge" / "standards" / "gb-mechanical.yaml"
        standard = load_standard(std_path)
        try:
            plan_layout(spec_2d, standard)
        except LayoutOverflowError as err:
            if ":" in err.suggested_scale:
                spec_2d.sheet.scale = err.suggested_scale
        spec_2d.validation.assumptions.append(
            f"3D->2D projection auto-selected drawing scale {spec_2d.sheet.scale}"
        )
    return spec_2d


def _project_hubbed_flange(
    body: Flange3D, sid: str
) -> tuple[list[str], list[DrawingEntity], dict[str, list[int]], list[dict[str, Any]]]:
    assert body.hub_diameter is not None
    r_out = body.outer_diameter / 2.0
    r_hub = body.hub_diameter / 2.0
    r_bore = body.bore_diameter / 2.0
    th = body.thickness
    total_h = th + body.hub_height
    ext = r_out * 1.06

    entities: list[DrawingEntity] = []
    front_indices: list[int] = []
    top_indices: list[int] = []
    left_indices: list[int] = []
    dims: list[dict[str, Any]] = []

    # 1. Front View (主视图 全剖: horizontal stepped section, width = 2*r_out, height = total_h)
    sec_outline_pts = [
        (-r_out, 0.0),
        (r_out, 0.0),
        (r_out, th),
        (r_hub, th),
        (r_hub, total_h),
        (-r_hub, total_h),
        (-r_hub, th),
        (-r_out, th),
    ]
    sec_front_idx = len(entities)
    front_indices.append(sec_front_idx)
    entities.append(
        PolylineEntity(kind="polyline", layer="OUTLINE", vertices=sec_outline_pts, closed=True, source_id=sid)
    )
    front_indices.append(len(entities))
    entities.append(LineEntity(kind="line", layer="CENTER", start=(0.0, -2.0), end=(0.0, total_h + 2.0), source_id=sid))

    has_sec_holes = bool(body.hole_count >= 2 and body.bolt_circle_diameter and body.hole_diameter)
    r_bcd = (body.bolt_circle_diameter or 0.0) / 2.0
    r_hole = (body.hole_diameter or 0.0) / 2.0

    if r_bore > 0:
        for xb in (-r_bore, r_bore):
            front_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="OUTLINE", start=(xb, 0.0), end=(xb, total_h), source_id=sid))

    if has_sec_holes and r_bcd - r_hole > r_hub + 0.5:
        for sign in (-1.0, 1.0):
            hc_x = sign * r_bcd
            front_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="OUTLINE", start=(hc_x - r_hole, 0.0), end=(hc_x - r_hole, th), source_id=sid))
            front_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="OUTLINE", start=(hc_x + r_hole, 0.0), end=(hc_x + r_hole, th), source_id=sid))
            front_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="CENTER", start=(hc_x, -1.8), end=(hc_x, th + 1.8), source_id=sid))
        pitch_idx = len(entities)
        front_indices.append(pitch_idx)
        entities.append(LineEntity(kind="line", layer="CENTER", start=(-r_bcd, 0.0), end=(r_bcd, 0.0), source_id=sid))

        front_indices.append(len(entities))
        entities.append(HatchEntity(kind="hatch", layer="HATCH", boundary=[(-r_out, 0.0), (-r_bcd - r_hole, 0.0), (-r_bcd - r_hole, th), (-r_out, th)], pattern="ANSI31", source_id=sid))
        front_indices.append(len(entities))
        entities.append(HatchEntity(kind="hatch", layer="HATCH", boundary=[(r_bcd + r_hole, 0.0), (r_out, 0.0), (r_out, th), (r_bcd + r_hole, th)], pattern="ANSI31", source_id=sid))
        if r_bore > 0:
            front_indices.append(len(entities))
            entities.append(HatchEntity(kind="hatch", layer="HATCH", boundary=[(-r_bcd + r_hole, 0.0), (-r_bore, 0.0), (-r_bore, total_h), (-r_hub, total_h), (-r_hub, th), (-r_bcd + r_hole, th)], pattern="ANSI31", source_id=sid))
            front_indices.append(len(entities))
            entities.append(HatchEntity(kind="hatch", layer="HATCH", boundary=[(r_bore, 0.0), (r_bcd - r_hole, 0.0), (r_bcd - r_hole, th), (r_hub, th), (r_hub, total_h), (r_bore, total_h)], pattern="ANSI31", source_id=sid))
        dims.append(
            {
                "kind": "diameter",
                "value": body.bolt_circle_diameter,
                "source_id": sid,
                "owner_view": "front",
                "target_entity_index": pitch_idx,
                "orientation": "horizontal",
                "prefix": "⌀",
            }
        )
    elif r_bore > 0:
        front_indices.append(len(entities))
        entities.append(
            HatchEntity(
                kind="hatch",
                layer="HATCH",
                boundary=[(-r_out, 0.0), (-r_bore, 0.0), (-r_bore, total_h), (-r_hub, total_h), (-r_hub, th), (-r_out, th)],
                pattern="ANSI31",
                source_id=sid,
            )
        )
        front_indices.append(len(entities))
        entities.append(
            HatchEntity(
                kind="hatch",
                layer="HATCH",
                boundary=[(r_bore, 0.0), (r_out, 0.0), (r_out, th), (r_hub, th), (r_hub, total_h), (r_bore, total_h)],
                pattern="ANSI31",
                source_id=sid,
            )
        )

    dims.append(
        {
            "kind": "linear",
            "value": total_h,
            "source_id": sid,
            "owner_view": "front",
            "target_entity_index": sec_front_idx,
            "orientation": "vertical",
        }
    )

    # 2. Top View (俯视图: circular plan view below front, width = 2*r_out 长对正!)
    outer_top_idx = len(entities)
    top_indices.append(outer_top_idx)
    entities.append(CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r_out, source_id=sid))
    top_indices.append(len(entities))
    entities.append(CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r_hub, source_id=sid))
    if r_bore > 0:
        bore_top_idx = len(entities)
        top_indices.append(bore_top_idx)
        entities.append(CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r_bore, source_id=sid))
        dims.append(
            {
                "kind": "diameter",
                "value": body.bore_diameter,
                "source_id": sid,
                "owner_view": "top",
                "target_entity_index": bore_top_idx,
                "orientation": "diameter",
            }
        )
    top_indices.append(len(entities))
    entities.append(LineEntity(kind="line", layer="CENTER", start=(-ext, 0.0), end=(ext, 0.0), source_id=sid))
    top_indices.append(len(entities))
    entities.append(LineEntity(kind="line", layer="CENTER", start=(0.0, -ext), end=(0.0, ext), source_id=sid))

    hole_ys: list[float] = []
    if body.hole_count > 0 and body.bolt_circle_diameter and body.hole_diameter:
        from math import cos, pi, sin

        top_indices.append(len(entities))
        entities.append(CircularEntity(kind="circular", layer="CENTER", center=(0.0, 0.0), radius=r_bcd, source_id=sid))
        first_h: int | None = None
        for k in range(body.hole_count):
            ang = 2.0 * pi * k / body.hole_count
            hx = round(r_bcd * cos(ang), 6)
            hy = round(r_bcd * sin(ang), 6)
            hole_ys.append(hy)
            h_i = len(entities)
            top_indices.append(h_i)
            entities.append(CircularEntity(kind="circular", layer="OUTLINE", center=(hx, hy), radius=r_hole, source_id=sid))
            if first_h is None:
                first_h = h_i
        if first_h is not None:
            dims.append(
                {
                    "kind": "diameter",
                    "value": body.hole_diameter,
                    "source_id": sid,
                    "owner_view": "top",
                    "target_entity_index": first_h,
                    "orientation": "diameter",
                    "prefix": f"{body.hole_count}×⌀" if body.hole_count > 1 else "⌀",
                }
            )
    dims.append(
        {
            "kind": "diameter",
            "value": body.outer_diameter,
            "source_id": sid,
            "owner_view": "top",
            "target_entity_index": outer_top_idx,
            "orientation": "horizontal",
            "prefix": "⌀",
        }
    )

    # 3. Left View (左视图: external stepped side elevation, width = 2*r_out 宽相等, height = total_h 高平齐!)
    left_sec_idx = len(entities)
    left_indices.append(left_sec_idx)
    entities.append(PolylineEntity(kind="polyline", layer="OUTLINE", vertices=sec_outline_pts, closed=True, source_id=sid))
    left_indices.append(len(entities))
    entities.append(LineEntity(kind="line", layer="OUTLINE", start=(-r_hub, th), end=(r_hub, th), source_id=sid))
    left_indices.append(len(entities))
    entities.append(LineEntity(kind="line", layer="CENTER", start=(0.0, -2.0), end=(0.0, total_h + 2.0), source_id=sid))
    if r_bore > 0:
        for xb in (-r_bore, r_bore):
            left_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="HIDDEN", start=(xb, 0.0), end=(xb, total_h), source_id=sid))
    for hy_u in sorted({round(v, 4) for v in hole_ys if abs(v) > 1e-3}):
        left_indices.append(len(entities))
        entities.append(LineEntity(kind="line", layer="HIDDEN", start=(hy_u - r_hole, 0.0), end=(hy_u - r_hole, th), source_id=sid))
        left_indices.append(len(entities))
        entities.append(LineEntity(kind="line", layer="HIDDEN", start=(hy_u + r_hole, 0.0), end=(hy_u + r_hole, th), source_id=sid))
    dims.append(
        {
            "kind": "diameter",
            "value": body.outer_diameter,
            "source_id": sid,
            "owner_view": "left",
            "target_entity_index": left_sec_idx,
            "orientation": "horizontal",
            "prefix": "⌀",
        }
    )
    return ["front", "top", "left"], entities, {"front": front_indices, "top": top_indices, "left": left_indices}, dims


def _project_box(
    body: Box3D, sid: str
) -> tuple[list[str], list[DrawingEntity], dict[str, list[int]], list[dict[str, Any]]]:
    hw, hd = body.width / 2.0, body.depth / 2.0
    entities: list[DrawingEntity] = []
    # Front view: X width x Z height; Top view: X width x Y depth (长对正!)
    front_idx = len(entities)
    entities.append(
        PolylineEntity(
            kind="polyline",
            layer="OUTLINE",
            vertices=[(-hw, 0.0), (hw, 0.0), (hw, body.height), (-hw, body.height)],
            closed=True,
            source_id=sid,
        )
    )
    front_indices = [front_idx]
    for hx, _, hd_i in body.through_holes:
        r = hd_i / 2.0
        for x_edge in (hx - r, hx + r):
            front_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="HIDDEN", start=(x_edge, 0.0), end=(x_edge, body.height), source_id=sid))

    top_idx = len(entities)
    entities.append(
        PolylineEntity(
            kind="polyline",
            layer="OUTLINE",
            vertices=[(-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)],
            closed=True,
            source_id=sid,
        )
    )
    top_indices = [top_idx]
    first_hole_top_idx: int | None = None
    for hx, hy, hd_i in body.through_holes:
        h_idx = len(entities)
        top_indices.append(h_idx)
        entities.append(
            CircularEntity(kind="circular", layer="OUTLINE", center=(hx, hy), radius=hd_i / 2.0, source_id=sid)
        )
        if first_hole_top_idx is None:
            first_hole_top_idx = h_idx

    left_idx = len(entities)
    entities.append(
        PolylineEntity(
            kind="polyline",
            layer="OUTLINE",
            vertices=[(-hd, 0.0), (hd, 0.0), (hd, body.height), (-hd, body.height)],
            closed=True,
            source_id=sid,
        )
    )
    left_indices = [left_idx]
    for _, hy, hd_i in body.through_holes:
        r = hd_i / 2.0
        for y_edge in (hy - r, hy + r):
            left_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="HIDDEN", start=(y_edge, 0.0), end=(y_edge, body.height), source_id=sid))

    dims = [
        {
            "kind": "linear",
            "value": body.width,
            "source_id": sid,
            "owner_view": "top",
            "target_entity_index": top_idx,
            "orientation": "horizontal",
        },
        {
            "kind": "linear",
            "value": body.height,
            "source_id": sid,
            "owner_view": "front",
            "target_entity_index": front_idx,
            "orientation": "vertical",
        },
        {
            "kind": "linear",
            "value": body.depth,
            "source_id": sid,
            "owner_view": "left",
            "target_entity_index": left_idx,
            "orientation": "horizontal",
        },
    ]
    if first_hole_top_idx is not None and body.through_holes:
        hole_cnt = len(body.through_holes)
        dims.append(
            {
                "kind": "diameter",
                "value": body.through_holes[0][2],
                "source_id": sid,
                "owner_view": "top",
                "target_entity_index": first_hole_top_idx,
                "orientation": "diameter",
                "prefix": f"{hole_cnt}×⌀" if hole_cnt > 1 else "⌀",
            }
        )
    return ["front", "top", "left"], entities, {"front": front_indices, "top": top_indices, "left": left_indices}, dims


def _project_cylinder(
    body: Cylinder3D, sid: str
) -> tuple[list[str], list[DrawingEntity], dict[str, list[int]], list[dict[str, Any]]]:
    r_out = body.outer_diameter / 2.0
    r_in = body.bore_diameter / 2.0
    entities: list[DrawingEntity] = []
    # Front view (主视图): upright elevation (2*r_out x height)
    # Top view   (俯视图): circular plan view (2*r_out x 2*r_out, 长对正)
    # Left view  (左视图): side elevation (2*r_out x height, 高平齐 & 宽相等)
    front_idx = len(entities)
    entities.append(
        PolylineEntity(
            kind="polyline",
            layer="OUTLINE",
            vertices=[(-r_out, 0.0), (r_out, 0.0), (r_out, body.height), (-r_out, body.height)],
            closed=True,
            source_id=sid,
        )
    )
    front_indices = [front_idx]
    if r_in > 0:
        for xh in (-r_in, r_in):
            front_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="HIDDEN", start=(xh, 0.0), end=(xh, body.height), source_id=sid))
    front_indices.append(len(entities))
    entities.append(LineEntity(kind="line", layer="CENTER", start=(0.0, -2.0), end=(0.0, body.height + 2.0), source_id=sid))

    c_idx = len(entities)
    entities.append(CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r_out, source_id=sid))
    top_indices = [c_idx]
    bore_idx: int | None = None
    if r_in > 0:
        bore_idx = len(entities)
        top_indices.append(bore_idx)
        entities.append(CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r_in, source_id=sid))
    for s_pt, e_pt in (((-r_out * 1.06, 0.0), (r_out * 1.06, 0.0)), ((0.0, -r_out * 1.06), (0.0, r_out * 1.06))):
        top_indices.append(len(entities))
        entities.append(LineEntity(kind="line", layer="CENTER", start=s_pt, end=e_pt, source_id=sid))

    side_idx = len(entities)
    entities.append(
        PolylineEntity(
            kind="polyline",
            layer="OUTLINE",
            vertices=[(-r_out, 0.0), (r_out, 0.0), (r_out, body.height), (-r_out, body.height)],
            closed=True,
            source_id=sid,
        )
    )
    left_indices = [side_idx]
    if r_in > 0:
        for yh in (-r_in, r_in):
            left_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="HIDDEN", start=(yh, 0.0), end=(yh, body.height), source_id=sid))
    left_indices.append(len(entities))
    entities.append(LineEntity(kind="line", layer="CENTER", start=(0.0, -2.0), end=(0.0, body.height + 2.0), source_id=sid))

    dims: list[dict[str, Any]] = [
        {
            "kind": "linear",
            "value": body.height,
            "source_id": sid,
            "owner_view": "front",
            "target_entity_index": front_idx,
            "orientation": "vertical",
        },
        {
            "kind": "diameter",
            "value": body.outer_diameter,
            "source_id": sid,
            "owner_view": "top",
            "target_entity_index": c_idx,
            "orientation": "horizontal",
            "prefix": "⌀",
        },
        {
            "kind": "diameter",
            "value": body.outer_diameter,
            "source_id": sid,
            "owner_view": "left",
            "target_entity_index": side_idx,
            "orientation": "horizontal",
            "prefix": "⌀",
        },
    ]
    if bore_idx is not None:
        dims.append(
            {
                "kind": "diameter",
                "value": body.bore_diameter,
                "source_id": sid,
                "owner_view": "top",
                "target_entity_index": bore_idx,
                "orientation": "diameter",
            }
        )
    return ["front", "top", "left"], entities, {"front": front_indices, "top": top_indices, "left": left_indices}, dims


def _project_extrude_polygon(
    body: ExtrudePolygon3D, sid: str
) -> tuple[list[str], list[DrawingEntity], dict[str, list[int]], list[dict[str, Any]]]:
    entities: list[DrawingEntity] = []
    outer_idx = len(entities)
    entities.append(
        PolylineEntity(kind="polyline", layer="OUTLINE", vertices=list(body.outer_loop), closed=True, source_id=sid)
    )
    front_indices = [outer_idx]
    for loop in body.inner_loops:
        front_indices.append(len(entities))
        entities.append(
            PolylineEntity(kind="polyline", layer="OUTLINE", vertices=list(loop), closed=True, source_id=sid)
        )

    xs = [p[0] for p in body.outer_loop]
    min_x, max_x = min(xs), max(xs)
    top_idx = len(entities)
    entities.append(
        PolylineEntity(
            kind="polyline",
            layer="OUTLINE",
            vertices=[(min_x, 0.0), (max_x, 0.0), (max_x, body.height), (min_x, body.height)],
            closed=True,
            source_id=sid,
        )
    )
    dims = [
        {
            "kind": "linear",
            "value": max_x - min_x,
            "source_id": sid,
            "owner_view": "front",
            "target_entity_index": outer_idx,
            "orientation": "horizontal",
        },
        {
            "kind": "linear",
            "value": body.height,
            "source_id": sid,
            "owner_view": "top",
            "target_entity_index": top_idx,
            "orientation": "vertical",
        },
    ]
    return ["front", "top"], entities, {"front": front_indices, "top": [top_idx]}, dims


def _project_compound_3d(
    body: CompoundSolid3D, sid: str
) -> tuple[list[str], list[DrawingEntity], dict[str, list[int]], list[dict[str, Any]]]:
    entities: list[DrawingEntity] = []
    front_indices: list[int] = []
    top_indices: list[int] = []
    h_base = body.base_height

    if body.base_shape == "box":
        half_x = body.width / 2.0
        half_y = body.depth / 2.0
    else:
        half_x = body.outer_diameter / 2.0
        half_y = half_x

    # 1. Front View (X-Z elevation): base outline + bosses + hidden holes/counterbores
    base_front_idx = len(entities)
    front_indices.append(base_front_idx)
    entities.append(
        PolylineEntity(
            kind="polyline",
            layer="OUTLINE",
            vertices=[(-half_x, 0.0), (half_x, 0.0), (half_x, h_base), (-half_x, h_base)],
            closed=True,
            source_id=sid,
        )
    )
    for boss in body.bosses:
        z_top = h_base + boss.height
        bx_half = boss.diameter / 2.0 if isinstance(boss, CylinderBoss3D) else boss.width / 2.0
        front_indices.append(len(entities))
        entities.append(
            PolylineEntity(
                kind="polyline",
                layer="OUTLINE",
                vertices=[(boss.x - bx_half, h_base), (boss.x + bx_half, h_base), (boss.x + bx_half, z_top), (boss.x - bx_half, z_top)],
                closed=True,
                source_id=sid,
            )
        )
        if isinstance(boss, CylinderBoss3D) and boss.bore_diameter > 0:
            rb = boss.bore_diameter / 2.0
            for x_e in (boss.x - rb, boss.x + rb):
                front_indices.append(len(entities))
                entities.append(LineEntity(kind="line", layer="HIDDEN", start=(x_e, 0.0), end=(x_e, z_top), source_id=sid))

    for hx, _, hd_i in body.through_holes:
        rh = hd_i / 2.0
        for x_e in (hx - rh, hx + rh):
            front_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="HIDDEN", start=(x_e, 0.0), end=(x_e, h_base), source_id=sid))

    for cb in body.counterbores:
        r_th = cb.through_diameter / 2.0
        r_cb = cb.cbore_diameter / 2.0
        z_step = h_base - cb.cbore_depth
        for x_e in (cb.x - r_th, cb.x + r_th):
            front_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="HIDDEN", start=(x_e, 0.0), end=(x_e, z_step), source_id=sid))
        for x_e in (cb.x - r_cb, cb.x + r_cb):
            front_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="HIDDEN", start=(x_e, z_step), end=(x_e, h_base), source_id=sid))
        front_indices.append(len(entities))
        entities.append(LineEntity(kind="line", layer="HIDDEN", start=(cb.x - r_cb, z_step), end=(cb.x + r_cb, z_step), source_id=sid))

    # 2. Top View (X-Y plan, exact X width == 2*half_x -> 长对正!)
    top_base_idx = len(entities)
    top_indices.append(top_base_idx)
    if body.base_shape == "cylinder":
        entities.append(CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=half_x, source_id=sid))
    else:
        entities.append(
            PolylineEntity(
                kind="polyline",
                layer="OUTLINE",
                vertices=[(-half_x, -half_y), (half_x, -half_y), (half_x, half_y), (-half_x, half_y)],
                closed=True,
                source_id=sid,
            )
        )

    first_boss_circle_idx: int | None = None
    first_boss_dia: float | None = None
    for boss in body.bosses:
        if isinstance(boss, CylinderBoss3D):
            b_idx = len(entities)
            top_indices.append(b_idx)
            entities.append(CircularEntity(kind="circular", layer="OUTLINE", center=(boss.x, boss.y), radius=boss.diameter / 2.0, source_id=sid))
            if first_boss_circle_idx is None:
                first_boss_circle_idx = b_idx
                first_boss_dia = boss.diameter
            if boss.bore_diameter > 0:
                top_indices.append(len(entities))
                entities.append(CircularEntity(kind="circular", layer="OUTLINE", center=(boss.x, boss.y), radius=boss.bore_diameter / 2.0, source_id=sid))
        else:
            assert isinstance(boss, BoxBoss3D)
            bhw, bhd = boss.width / 2.0, boss.depth / 2.0
            top_indices.append(len(entities))
            entities.append(
                PolylineEntity(
                    kind="polyline",
                    layer="OUTLINE",
                    vertices=[(boss.x - bhw, boss.y - bhd), (boss.x + bhw, boss.y - bhd), (boss.x + bhw, boss.y + bhd), (boss.x - bhw, boss.y + bhd)],
                    closed=True,
                    source_id=sid,
                )
            )

    for hx, hy, hd_i in body.through_holes:
        top_indices.append(len(entities))
        entities.append(CircularEntity(kind="circular", layer="OUTLINE", center=(hx, hy), radius=hd_i / 2.0, source_id=sid))

    for cb in body.counterbores:
        top_indices.append(len(entities))
        entities.append(CircularEntity(kind="circular", layer="OUTLINE", center=(cb.x, cb.y), radius=cb.cbore_diameter / 2.0, source_id=sid))
        top_indices.append(len(entities))
        entities.append(CircularEntity(kind="circular", layer="OUTLINE", center=(cb.x, cb.y), radius=cb.through_diameter / 2.0, source_id=sid))

    # 3. Left View (Y-Z side elevation: width == 2*half_y 宽相等 with top, height == total_z 高平齐 with front!)
    left_indices: list[int] = []
    left_base_idx = len(entities)
    left_indices.append(left_base_idx)
    entities.append(
        PolylineEntity(
            kind="polyline",
            layer="OUTLINE",
            vertices=[(-half_y, 0.0), (half_y, 0.0), (half_y, h_base), (-half_y, h_base)],
            closed=True,
            source_id=sid,
        )
    )
    max_z = h_base
    for boss in body.bosses:
        z_top = h_base + boss.height
        max_z = max(max_z, z_top)
        by_half = boss.diameter / 2.0 if isinstance(boss, CylinderBoss3D) else boss.depth / 2.0
        left_indices.append(len(entities))
        entities.append(
            PolylineEntity(
                kind="polyline",
                layer="OUTLINE",
                vertices=[(boss.y - by_half, h_base), (boss.y + by_half, h_base), (boss.y + by_half, z_top), (boss.y - by_half, z_top)],
                closed=True,
                source_id=sid,
            )
        )
        if isinstance(boss, CylinderBoss3D) and boss.bore_diameter > 0:
            rb = boss.bore_diameter / 2.0
            for y_e in (boss.y - rb, boss.y + rb):
                left_indices.append(len(entities))
                entities.append(LineEntity(kind="line", layer="HIDDEN", start=(y_e, 0.0), end=(y_e, z_top), source_id=sid))

    for _, hy, hd_i in body.through_holes:
        rh = hd_i / 2.0
        for y_e in (hy - rh, hy + rh):
            left_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="HIDDEN", start=(y_e, 0.0), end=(y_e, h_base), source_id=sid))

    for cb in body.counterbores:
        r_th = cb.through_diameter / 2.0
        r_cb = cb.cbore_diameter / 2.0
        z_step = h_base - cb.cbore_depth
        for y_e in (cb.y - r_th, cb.y + r_th):
            left_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="HIDDEN", start=(y_e, 0.0), end=(y_e, z_step), source_id=sid))
        for y_e in (cb.y - r_cb, cb.y + r_cb):
            left_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="HIDDEN", start=(y_e, z_step), end=(y_e, h_base), source_id=sid))

    dims: list[dict[str, Any]] = [
        {
            "kind": "linear" if body.base_shape == "box" else "diameter",
            "value": 2.0 * half_x,
            "source_id": sid,
            "owner_view": "top",
            "target_entity_index": top_base_idx,
            "orientation": "horizontal",
            **({"prefix": "⌀"} if body.base_shape == "cylinder" else {}),
        },
        {
            "kind": "linear",
            "value": h_base,
            "source_id": sid,
            "owner_view": "front",
            "target_entity_index": base_front_idx,
            "orientation": "vertical",
        },
        {
            "kind": "linear",
            "value": 2.0 * half_y,
            "source_id": sid,
            "owner_view": "left",
            "target_entity_index": left_base_idx,
            "orientation": "horizontal",
        },
    ]
    if first_boss_circle_idx is not None and first_boss_dia is not None:
        dims.append(
            {
                "kind": "diameter",
                "value": first_boss_dia,
                "source_id": sid,
                "owner_view": "top",
                "target_entity_index": first_boss_circle_idx,
                "orientation": "diameter",
            }
        )
    return ["front", "top", "left"], entities, {"front": front_indices, "top": top_indices, "left": left_indices}, dims
