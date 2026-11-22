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
    Cylinder3D,
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
            bore_circle_idx = len(entities)
            view_indices["right"].append(bore_circle_idx)
            entities.append(
                CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=rb, source_id=sid)
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
        include_right_section=False,
    )
    entities = list(part.entities)
    front_indices = list(part.view_entity_indices["front"])
    dims = list(part.dimensions)

    assert body.hub_diameter is not None
    r_out = body.outer_diameter / 2.0
    r_hub = body.hub_diameter / 2.0
    r_bore = body.bore_diameter / 2.0
    th = body.thickness
    total_h = th + body.hub_height

    # Add hub circle on front view
    front_indices.append(len(entities))
    entities.append(
        CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r_hub, source_id=sid)
    )

    # Stepped right section view (width = total_h, height = 2*r_out -> 高平齐 with front!)
    right_indices: list[int] = []
    sec_outline_pts = [
        (0.0, -r_out),
        (th, -r_out),
        (th, -r_hub),
        (total_h, -r_hub),
        (total_h, r_hub),
        (th, r_hub),
        (th, r_out),
        (0.0, r_out),
    ]
    sec_idx = len(entities)
    right_indices.append(sec_idx)
    entities.append(
        PolylineEntity(kind="polyline", layer="OUTLINE", vertices=sec_outline_pts, closed=True, source_id=sid)
    )
    if r_bore > 0:
        for y_b in (-r_bore, r_bore):
            right_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="OUTLINE", start=(0.0, y_b), end=(total_h, y_b), source_id=sid))
        right_indices.append(len(entities))
        entities.append(
            HatchEntity(
                kind="hatch",
                layer="HATCH",
                boundary=[(0.0, r_bore), (total_h, r_bore), (total_h, r_hub), (th, r_hub), (th, r_out), (0.0, r_out)],
                pattern="ANSI31",
                source_id=sid,
            )
        )
        right_indices.append(len(entities))
        entities.append(
            HatchEntity(
                kind="hatch",
                layer="HATCH",
                boundary=[(0.0, -r_out), (th, -r_out), (th, -r_hub), (total_h, -r_hub), (total_h, -r_bore), (0.0, -r_bore)],
                pattern="ANSI31",
                source_id=sid,
            )
        )
    right_indices.append(len(entities))
    entities.append(LineEntity(kind="line", layer="CENTER", start=(-2.0, 0.0), end=(total_h + 2.0, 0.0), source_id=sid))

    dims.append(
        {
            "kind": "linear",
            "value": total_h,
            "source_id": sid,
            "owner_view": "right",
            "target_entity_index": sec_idx,
            "orientation": "horizontal",
        }
    )
    return ["front", "right"], entities, {"front": front_indices, "right": right_indices}, dims


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
    for hx, hy, hd_i in body.through_holes:
        top_indices.append(len(entities))
        entities.append(
            CircularEntity(kind="circular", layer="OUTLINE", center=(hx, hy), radius=hd_i / 2.0, source_id=sid)
        )

    dims = [
        {
            "kind": "linear",
            "value": body.width,
            "source_id": sid,
            "owner_view": "front",
            "target_entity_index": front_idx,
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
    ]
    return ["front", "top"], entities, {"front": front_indices, "top": top_indices}, dims


def _project_cylinder(
    body: Cylinder3D, sid: str
) -> tuple[list[str], list[DrawingEntity], dict[str, list[int]], list[dict[str, Any]]]:
    r_out = body.outer_diameter / 2.0
    r_in = body.bore_diameter / 2.0
    entities: list[DrawingEntity] = []
    # Front view: circular face; Right view: side elevation (height == 2*r_out, width == body.height)
    c_idx = len(entities)
    entities.append(CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r_out, source_id=sid))
    front_indices = [c_idx]
    if r_in > 0:
        front_indices.append(len(entities))
        entities.append(CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r_in, source_id=sid))
    for s_pt, e_pt in (((-r_out, 0.0), (r_out, 0.0)), ((0.0, -r_out), (0.0, r_out))):
        front_indices.append(len(entities))
        entities.append(LineEntity(kind="line", layer="CENTER", start=s_pt, end=e_pt, source_id=sid))

    side_idx = len(entities)
    entities.append(
        PolylineEntity(
            kind="polyline",
            layer="OUTLINE",
            vertices=[(0.0, -r_out), (body.height, -r_out), (body.height, r_out), (0.0, r_out)],
            closed=True,
            source_id=sid,
        )
    )
    right_indices = [side_idx]
    if r_in > 0:
        for yh in (-r_in, r_in):
            right_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="HIDDEN", start=(0.0, yh), end=(body.height, yh), source_id=sid))
    right_indices.append(len(entities))
    entities.append(LineEntity(kind="line", layer="CENTER", start=(-2.0, 0.0), end=(body.height + 2.0, 0.0), source_id=sid))

    dims = [
        {
            "kind": "diameter",
            "value": body.outer_diameter,
            "source_id": sid,
            "owner_view": "front",
            "target_entity_index": c_idx,
            "orientation": "horizontal",
            "prefix": "⌀",
        },
        {
            "kind": "linear",
            "value": body.height,
            "source_id": sid,
            "owner_view": "right",
            "target_entity_index": side_idx,
            "orientation": "horizontal",
        },
    ]
    return ["front", "right"], entities, {"front": front_indices, "right": right_indices}, dims


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
