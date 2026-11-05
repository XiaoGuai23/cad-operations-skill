"""Explicit native annotation edits, with round-trip verification evidence.

Dimension positions describe a point on a dimension line, not a text grip.
Only planar linear/aligned dimensions are rendered.  Leader movement supports
native MTEXT MULTILEADERs; conversion accepts one unbranched, straight chain
whose terminal is the selected text insertion.  No proximity selection or
numeric replacement is performed.
"""
from __future__ import annotations

import copy
import math

from ezdxf.colors import RGB
from ezdxf.math import Vec2, Vec3
from ezdxf.render import ARROWS
from ezdxf.render.mleader import ConnectionSide

from .common import RevisionError, resolve_entity, resolve_entities, validate_tolerance


_KINDS = {"dimension_move", "dimension_text_mode", "leader_move", "link_callout"}


def _validate_operation(operation, *, verification=False):
    fields = {
        'dimension_move': {'type', 'target', 'position'},
        'dimension_text_mode': {'type', 'target', 'mode'},
        'leader_move': {'type', 'target', 'position'},
        'link_callout': {'type', 'text', 'lines', 'arrow_target', 'arrow_name', 'arrow_size', 'position'},
    }
    if not isinstance(operation, dict) or not isinstance(operation.get('type'), str) or operation['type'] not in fields:
        raise RevisionError('unknown annotation operation')
    kind = operation['type']
    allowed = fields[kind] | ({'target', 'result_handle', 'expected', 'original', 'deleted_handles'} if verification else set())
    if set(operation) - allowed:
        raise RevisionError('unknown annotation operation fields')
    if kind in {'dimension_move', 'leader_move'}:
        _position(operation.get('position'))
    elif kind == 'dimension_text_mode':
        if type(operation.get('mode')) is not int or operation['mode'] not in (0, 1, 2):
            raise RevisionError('dimension text mode must be 0, 1 or 2')
    else:
        _position(operation.get('arrow_target'), 'arrow_target')


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RevisionError(f"{name} must be finite")
    return float(value)


def _position(value, name="position"):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise RevisionError(f"{name} requires exactly [x, y]")
    return Vec3(_number(value[0], name), _number(value[1], name), 0)


def _point(value, tolerance):
    point = Vec3(value)
    if not all(math.isfinite(v) for v in point) or abs(point.z) > tolerance:
        raise RevisionError("only finite annotations in the WCS XY plane are supported")
    return point


def _plain(value):
    if isinstance(value, dict):
        return {key: _plain(val) for key, val in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return [_plain(v) for v in value]


def _close(a, b, tolerance):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k], tolerance) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_close(x, y, tolerance) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= tolerance
    return a == b


def _normal(entity):
    if entity.dimtype == 1:
        axis = Vec2(entity.dxf.defpoint3) - Vec2(entity.dxf.defpoint2)
        if axis.magnitude == 0:
            raise RevisionError("aligned dimension has coincident measurement origins")
        axis = axis.normalize()
    else:
        axis = Vec2.from_deg_angle(entity.dxf.get("angle", 0))
    return Vec3(-axis.y, axis.x, 0)


def _dimension(entity, tolerance):
    if entity.dxftype() != "DIMENSION" or entity.dimtype not in (0, 1):
        raise RevisionError("only native linear/aligned DIMENSION types support this operation")
    if not _close(entity.dxf.get("extrusion", (0, 0, 1)), (0, 0, 1), tolerance):
        raise RevisionError("dimension extrusion must be WCS +Z")
    for key in ("defpoint", "defpoint2", "defpoint3"):
        if not entity.dxf.hasattr(key):
            raise RevisionError(f"dimension lacks {key}")
        _point(entity.dxf.get(key), tolerance)
    _number(entity.dxf.get("angle", 0), "dimension angle")
    if entity.dxf.get("oblique_angle", 0) not in (0, 90):
        raise RevisionError("oblique extension-line dimensions are unsupported")
    if entity.dxf.get("dimtype", 0) & 128:
        raise RevisionError("manually positioned dimension text is unsupported by cache regeneration")
    if entity.has_xdata("ACAD") and entity.has_xdata_list("ACAD", "DSTYLE"):
        # The renderer supports these modes but cannot reproduce all CAD-only
        # dimension families.  Linear/aligned types are the explicit boundary.
        mode = entity.override().get("dimtmove", 0)
        if mode not in (0, 1, 2):
            raise RevisionError("unsupported existing dimension text movement mode")


def _dimension_snapshot(entity):
    return {
        "type": entity.dxftype(), "dimtype": entity.dimtype,
        "origins": [list(entity.dxf.defpoint2), list(entity.dxf.defpoint3)],
        "measurement": float(entity.get_measurement()),
        "text": entity.dxf.get("text", "<>"), "dimstyle": entity.dxf.dimstyle,
        "line_point": list(entity.dxf.defpoint),
        "text_mode": entity.override().get("dimtmove", 0),
        "overrides": _plain(entity.override().dimstyle_attribs),
    }


def _leader(entity, tolerance):
    if entity.dxftype() != "MULTILEADER" or entity.context.mtext is None or entity.context.block is not None:
        raise RevisionError("leader_move requires a native MULTILEADER with MTEXT content")
    context = entity.context
    if not _close(context.mtext.extrusion, (0, 0, 1), tolerance):
        raise RevisionError("MULTILEADER must use the WCS XY plane")
    for point in (context.mtext.insert, context.base_point, context.plane_origin):
        _point(point, tolerance)
    if not context.leaders:
        raise RevisionError("MULTILEADER has no explicit arrow targets")
    for leader in context.leaders:
        _point(leader.last_leader_point, tolerance)
        if leader.breaks:
            raise RevisionError("MULTILEADER with cached leader breaks is unsupported")
        if not leader.lines:
            raise RevisionError("MULTILEADER contains an empty leader")
        for line in leader.lines:
            if not line.vertices or line.breaks:
                raise RevisionError("MULTILEADER needs vertices and no cached line breaks")
            for point in line.vertices:
                _point(point, tolerance)


def _leader_snapshot(entity):
    context = entity.context
    return {
        "type": entity.dxftype(),
        "text": context.mtext.default_content,
        "text_properties": _plain(vars(context.mtext)),
        "char_height": context.char_height,
        "arrow_size": context.arrow_head_size,
        "arrow_handle": entity.dxf.get("arrow_head_handle", ""),
        "arrow_targets": [[list(line.vertices[0]) for line in leader.lines] for leader in context.leaders],
        "leader_paths": [[_plain(line.vertices) for line in leader.lines] for leader in context.leaders],
        "attachments": [list(leader.last_leader_point) for leader in context.leaders],
        "base_point": list(context.base_point),
        "style_handle": entity.dxf.get("style_handle", ""),
        "graphics": {key: entity.dxf.get(key, default) for key, default in
                     (("layer", "0"), ("color", 256), ("linetype", "BYLAYER"), ("lineweight", -1),
                      ("leader_line_color", 0), ("leader_lineweight", -2),
                      ("leader_linetype_handle", ""), ("leader_type", 1))},
    }


def _source_text(entity, tolerance):
    kind = entity.dxftype()
    if kind not in ("TEXT", "MTEXT"):
        raise RevisionError("link_callout text selector must name TEXT or MTEXT")
    insert = _point(entity.dxf.insert, tolerance)
    if not _close(entity.dxf.get("extrusion", (0, 0, 1)), (0, 0, 1), tolerance):
        raise RevisionError("callout text extrusion must be WCS +Z")
    if kind == "TEXT":
        if any(entity.dxf.get(key, 0) for key in ("halign", "valign", "oblique", "text_generation_flag")):
            raise RevisionError("only left-baseline, non-oblique TEXT is supported")
        content = entity.dxf.text
        width = _number(entity.dxf.get("width", 1), "text width factor")
        if width <= 0:
            raise RevisionError("text width factor must be positive")
        # TEXT's width is encoded as an MTEXT width command, with the complete
        # original annotation retained, including diameter/quantity/tolerance.
        if width != 1:
            content = "{\\W" + format(width, ".12g") + ";" + content + "}"
        height = _number(entity.dxf.height, "text height")
        default_insert = insert + Vec3(0, height, 0)
    else:
        if entity.dxf.get("attachment_point", 1) != 1:
            raise RevisionError("only top-left MTEXT attachment is supported")
        if entity.has_columns or entity.dxf.get("bg_fill", 0):
            raise RevisionError("MTEXT columns and background fills require an explicit conversion policy")
        content = entity.text
        height = _number(entity.dxf.char_height, "text height")
        default_insert = insert
    if height <= 0:
        raise RevisionError("text height must be positive")
    rotation = _number(entity.dxf.get("rotation", 0), "text rotation")
    if kind == "MTEXT" and entity.dxf.hasattr("text_direction"):
        direction = _point(entity.dxf.text_direction, tolerance)
        rotation = math.degrees(math.atan2(direction.y, direction.x))
    if kind == "TEXT":
        # Account for the baseline direction when translating to MTEXT's top-left.
        angle = math.radians(rotation)
        default_insert = insert + Vec3(-math.sin(angle) * height, math.cos(angle) * height, 0)
    return content, height, default_insert, rotation


def _chain(entities, target, terminal, tolerance):
    nodes, edges, native = [], [], []

    def node(point):
        matches = [i for i, existing in enumerate(nodes) if existing.distance(point) <= tolerance]
        if len(matches) > 1:
            raise RevisionError("chain endpoint matching is ambiguous at this tolerance")
        if matches:
            return matches[0]
        nodes.append(point)
        return len(nodes) - 1

    for entity in entities:
        kind = entity.dxftype()
        if kind == "LINE":
            points = [_point(entity.dxf.start, tolerance), _point(entity.dxf.end, tolerance)]
        elif kind == "LEADER":
            if entity.dxf.get("path_type", 0) != 0 or entity.dxf.get("has_hookline", 0):
                raise RevisionError("only straight LEADERs without implicit hooklines can be converted")
            points = [_point(p, tolerance) for p in entity.vertices]
            native.append(entity)
        else:
            raise RevisionError("callout chains support LINE and straight LEADER entities only")
        if len(points) < 2:
            raise RevisionError("callout chain contains an empty leader")
        for start, end in zip(points, points[1:]):
            if start.distance(end) <= tolerance:
                raise RevisionError("callout chain contains a zero-length segment")
            a, b = node(start), node(end)
            if (a, b) in edges or (b, a) in edges:
                raise RevisionError("callout chain contains overlapping duplicate segments")
            edges.append((a, b))
    if len(native) > 1:
        raise RevisionError("multiple native LEADER arrow semantics are ambiguous")
    adjacency = [[] for _ in nodes]
    for a, b in edges:
        adjacency[a].append(b)
        adjacency[b].append(a)
    endpoints = [i for i, neighbors in enumerate(adjacency) if len(neighbors) == 1]
    if len(endpoints) != 2 or any(len(neighbors) > 2 for neighbors in adjacency):
        raise RevisionError("selected chain must have two endpoints and no branches")
    starts = [i for i in endpoints if nodes[i].distance(target) <= tolerance]
    if len(starts) != 1:
        raise RevisionError("arrow_target must identify exactly one chain endpoint")
    ordered, previous, current = [], None, starts[0]
    while True:
        if current in ordered:
            raise RevisionError("callout chain is cyclic")
        ordered.append(current)
        neighbors = [n for n in adjacency[current] if n != previous]
        if not neighbors:
            break
        previous, current = current, neighbors[0]
    if len(ordered) != len(nodes) or len(edges) != len(nodes) - 1:
        raise RevisionError("selected chain is disconnected")
    if nodes[ordered[-1]].distance(terminal) > tolerance:
        raise RevisionError("chain terminal must connect to the selected text insertion")
    if native and native[0].dxf.get("has_arrowhead", 1):
        if Vec3(native[0].vertices[0]).distance(target) > tolerance:
            raise RevisionError("explicit arrow_target contradicts the native LEADER arrow")
    return [nodes[i] for i in ordered], native


def _arrow(doc, operation, native):
    if native:
        entity = native[0]
        style = entity.override()
        name = style.get("dimldrblk", "") if entity.dxf.get("has_arrowhead", 1) else "NONE"
        size = style.get("dimasz", 2.5) * (style.get("dimscale", 1) or 1)
    else:
        if "arrow_name" not in operation or "arrow_size" not in operation:
            raise RevisionError("LINE chains require explicit arrow_name and arrow_size")
        name, size = operation["arrow_name"], operation["arrow_size"]
    name = operation.get("arrow_name", name)
    size = _number(operation.get("arrow_size", size), "arrow_size")
    if not isinstance(name, str) or size <= 0:
        raise RevisionError("arrow_name must be a string and arrow_size must be positive")
    if name and name not in ARROWS and name not in doc.blocks:
        raise RevisionError(f"unknown arrow block: {name}")
    return name, size


def _color(entity):
    if entity.dxf.hasattr("true_color"):
        return RGB.from_hex(f"{entity.dxf.true_color:06x}")
    color = entity.dxf.get("color", 256)
    if color == 256:
        layer = entity.doc.layers.get(entity.dxf.layer)
        if layer.dxf.hasattr("true_color"):
            return RGB.from_hex(f"{layer.dxf.true_color:06x}")
        return abs(layer.dxf.color)
    return color


def _line_properties(entity):
    layer = entity.doc.layers.get(entity.dxf.layer)
    linetype = entity.dxf.get("linetype", "BYLAYER")
    weight = entity.dxf.get("lineweight", -1)
    return (_color(entity), layer.dxf.linetype if linetype.upper() == "BYLAYER" else linetype,
            layer.dxf.lineweight if weight == -1 else weight)


def _convert(doc, operation, tolerance):
    if doc.dxfversion < "AC1021":
        raise RevisionError("MULTILEADER requires DXF R2007 or newer")
    text = resolve_entity(doc, operation.get("text"))
    entities = resolve_entities(doc, operation.get("lines"))
    for source in [text, *entities]:
        known_apps = {'CAD_SKILL_ID'} | ({'ACAD'} if source.dxftype() == 'LEADER' else set())
        apps = set(source.xdata.data) if source.xdata is not None else set()
        if source.extension_dict or source.appdata or source.reactors or apps - known_apps:
            raise RevisionError('callout metadata requires an explicit preservation policy before linking')
    target = _position(operation.get("arrow_target"), "arrow_target")
    content, height, position, rotation = _source_text(text, tolerance)
    points, native = _chain(entities, target, Vec3(text.dxf.insert), tolerance)
    name, size = _arrow(doc, operation, native)
    if "position" in operation:
        position = _position(operation["position"])
    properties = [_line_properties(entity) for entity in entities]
    if any(properties[0] != prop for prop in properties[1:]):
        raise RevisionError("chain segments have differing graphics; conversion would lose their styles")
    if text.dxf.style not in doc.styles:
        raise RevisionError("callout text style is missing")
    before = {"text": text.dxf.text if text.dxftype() == "TEXT" else text.text,
              "type": text.dxftype(), "text_insert": list(text.dxf.insert),
              "style": text.dxf.style, "chain": _plain(points), "arrow_target": list(target),
              "arrow_name": name, "arrow_size": size}
    deleted = [text.dxf.handle] + [entity.dxf.handle for entity in entities]
    layout = doc.modelspace()
    builder = layout.add_multileader_mtext(dxfattribs={"layer": text.dxf.layer})
    builder.set_content(content, char_height=height, style=text.dxf.style, color=_color(text))
    builder.set_arrow_properties(name=name, size=size)
    builder.set_leader_properties(color=properties[0][0], linetype=properties[0][1], lineweight=properties[0][2])
    builder.set_connection_properties(landing_gap=0, dogleg_length=0)
    if text.dxftype() == "MTEXT":
        builder.context.mtext.width = text.dxf.get("width", 0)
        builder.context.mtext.line_spacing_factor = text.dxf.get("line_spacing_factor", 1)
        builder.context.mtext.line_spacing_style = text.dxf.get("line_spacing_style", 1)
        builder.context.mtext.flow_direction = text.dxf.get("flow_direction", 1)
    axis = Vec2.from_deg_angle(rotation)
    side = ConnectionSide.left if (Vec2(points[-2]) - Vec2(position)).dot(axis) <= 0 else ConnectionSide.right
    builder.add_leader_line(side, [Vec2(point) for point in points[:-1]])
    builder.build(insert=Vec2(position), rotation=rotation)
    result = builder.multileader
    # Keep the text's stable identity usable after linking if it already has one.
    if text.has_xdata("CAD_SKILL_ID"):
        result.set_xdata("CAD_SKILL_ID", text.get_xdata("CAD_SKILL_ID"))
    for entity in [text, *entities]:
        layout.delete_entity(entity)
    return result, before, deleted


def _apply(doc, operation, tolerance):
    if not isinstance(operation, dict) or operation.get("type") not in _KINDS:
        raise RevisionError("unknown annotation operation")
    kind = operation["type"]
    deleted, created, touched = [], [], []
    if kind == "link_callout":
        entity, before, deleted = _convert(doc, operation, tolerance)
        created = [entity.dxf.handle]
        after = _leader_snapshot(entity)
    else:
        entity = resolve_entity(doc, operation.get("target"))
        if kind.startswith("dimension_"):
            _dimension(entity, tolerance)
            before = _dimension_snapshot(entity)
            if kind == "dimension_move":
                position = _position(operation.get("position"))
                normal = _normal(entity)
                delta = normal * (position - entity.dxf.defpoint).dot(normal)
                changed = delta.magnitude > tolerance
                if changed:
                    entity.dxf.defpoint = entity.dxf.defpoint + delta
            else:
                mode = operation.get("mode")
                if type(mode) is not int or mode not in (0, 1, 2):
                    raise RevisionError("dimension text mode must be 0, 1 or 2")
                changed = entity.override().get("dimtmove", 0) != mode
            if changed:
                override = entity.override()
                if kind == "dimension_text_mode":
                    override.update({"dimtmove": operation["mode"]})
                override.render()
                touched = [entity.dxf.handle]
            after = _dimension_snapshot(entity)
            for key in ("origins", "measurement", "text", "dimstyle", "dimtype"):
                if not _close(before[key], after[key], tolerance):
                    raise RevisionError(f"native renderer changed protected dimension {key}")
        else:
            _leader(entity, tolerance)
            position = _position(operation.get("position"))
            before = _leader_snapshot(entity)
            delta = position - entity.context.mtext.insert
            if delta.magnitude > tolerance:
                entity.context.mtext.insert = position
                entity.context.base_point += delta
                for leader in entity.context.leaders:
                    leader.last_leader_point += delta
                entity.update_proxy_graphic()
                touched = [entity.dxf.handle]
            after = _leader_snapshot(entity)
    verification = copy.deepcopy(operation)
    verification.update({"result_handle": entity.dxf.handle, "expected": after,
                         "original": before, "deleted_handles": deleted})
    check = check_annotation(doc, verification, tolerance)
    if check["status"] != "passed":
        raise RevisionError(f"annotation verification failed: {check['checks']}")
    return {"status": "passed", "touched_handles": touched, "created_handles": created,
            "deleted_handles": deleted, "before": before, "after": after,
            "checks": check["checks"], "verification": verification}


def apply_annotation(doc, operation, tolerance):
    """Apply a validated annotation edit, returning a complete verification op.

    Stage on a private document first: renderer/builder errors and unsupported
    combinations must not leave partial changes, resources or deleted entities
    in the caller's document.
    """
    tolerance = validate_tolerance(tolerance)
    try:
        _validate_operation(operation)
        _apply(copy.deepcopy(doc), operation, tolerance)
        return _apply(doc, operation, tolerance)
    except RevisionError:
        raise
    except Exception as error:
        raise RevisionError(f"annotation operation is unsupported: {error}") from error


def check_annotation(doc, operation, tolerance):
    """Check desired state and the full protected snapshot after save/reopen."""
    tolerance = validate_tolerance(tolerance)
    checks = {}
    after = None
    try:
        _validate_operation(operation, verification=True)
        kind = operation.get("type")
        if kind not in _KINDS:
            raise RevisionError("unknown annotation operation")
        selector = {"handle": operation["result_handle"]} if "result_handle" in operation else operation.get("target")
        entity = resolve_entity(doc, selector)
        if kind.startswith("dimension_"):
            _dimension(entity, tolerance)
            after = _dimension_snapshot(entity)
            if kind == "dimension_move":
                normal = _normal(entity)
                checks["dimension_line_position"] = abs((_position(operation.get("position")) - entity.dxf.defpoint).dot(normal)) <= tolerance
            else:
                checks["text_movement_mode"] = after["text_mode"] == operation.get("mode")
            block = doc.blocks.get(entity.dxf.get("geometry", ""))
            checks["native_dimension_cache"] = block is not None and len(block) > 0
        else:
            _leader(entity, tolerance)
            after = _leader_snapshot(entity)
            if "position" in operation:
                checks["text_position"] = _close(entity.context.mtext.insert, _position(operation["position"]), tolerance)
            if kind == "link_callout":
                targets = [point for group in after["arrow_targets"] for point in group]
                checks["arrow_target"] = len(targets) == 1 and _close(targets[0], _position(operation["arrow_target"]), tolerance)
                checks["sources_removed"] = all(doc.entitydb.get(h) is None or not doc.entitydb[h].is_alive
                                                for h in operation.get("deleted_handles", []))
        if "expected" in operation:
            expected = operation["expected"]
            for key, value in expected.items():
                checks[f"preserved_{key}"] = key in after and _close(after[key], value, tolerance)
        checks["native_entity"] = True
    except (RevisionError, AttributeError, KeyError, TypeError, ValueError) as error:
        checks["valid_annotation"] = False
        return {"status": "failed", "checks": checks, "after": after, "error": str(error)}
    return {"status": "passed" if all(checks.values()) else "failed", "checks": checks, "after": after}
