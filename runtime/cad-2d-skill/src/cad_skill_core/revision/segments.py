"""Split finite planar CAD curves at analytic intersections, without merging.

Only modelspace XY geometry with the default extrusion, no thickness and no
polyline width is supported. Native annotations and hatches remain native.
Unsupported entities are retained and reported; an explicit unsupported target
rejects the entire operation before any modelspace entity is changed.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
import math

from ezdxf.entities import factory
from ezdxf.entities.copy import CopySettings, CopyStrategy
from ezdxf.math import arc_angle_span_deg

from .common import RevisionError, resolve_entities, stable_id, validate_tolerance


TAU = math.tau
SUPPORTED = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE"}
PROTECTED = {"HATCH", "DIMENSION", "MULTILEADER", "MLEADER", "LEADER", "TEXT", "MTEXT", "ATTRIB", "ATTDEF", "POINT"}
ANNOTATIONS = {"DIMENSION", "MULTILEADER", "MLEADER", "LEADER"}
_COPY = CopyStrategy(CopySettings(ignore_copy_errors_in_linked_entities=False))


def _sub(a, b):
    return a[0] - b[0], a[1] - b[1]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


@dataclass
class _Curve:
    kind: str
    a: tuple[float, float]
    b: tuple[float, float] | None = None
    radius: float = 0.0
    start: float = 0.0
    span: float = 1.0
    full: bool = False
    cuts: list[float] = field(default_factory=list)

    @property
    def scale(self):
        return math.dist(self.a, self.b) if self.kind == "line" else self.radius

    @property
    def bounds(self):
        if self.kind == "line":
            return min(self.a[0], self.b[0]), min(self.a[1], self.b[1]), max(self.a[0], self.b[0]), max(self.a[1], self.b[1])
        return self.a[0] - self.radius, self.a[1] - self.radius, self.a[0] + self.radius, self.a[1] + self.radius

    def point(self, t):
        if self.kind == "line":
            return self.a[0] + t * (self.b[0] - self.a[0]), self.a[1] + t * (self.b[1] - self.a[1])
        return self.a[0] + self.radius * math.cos(self.start + t), self.a[1] + self.radius * math.sin(self.start + t)

    def parameter(self, point, tolerance):
        """Parameter only when the point is on the finite curve, in length units of tolerance."""
        epsilon = tolerance / self.scale
        if self.kind == "line":
            delta = _sub(self.b, self.a)
            offset = _sub(point, self.a)
            t = _dot(offset, delta) / self.scale**2
            if abs(_cross(offset, delta)) / self.scale > tolerance or t < -epsilon or t > 1 + epsilon:
                return None
        else:
            if abs(math.dist(self.a, point) - self.radius) > tolerance:
                return None
            t = (math.atan2(point[1] - self.a[1], point[0] - self.a[0]) - self.start) % TAU
            if TAU - t <= epsilon:
                t = 0.0
            if not self.full and t > self.span + epsilon:
                return None
        if t <= epsilon:
            return 0.0
        if not self.full and self.span - t <= epsilon:
            return self.span
        return t


def _finite(values):
    return all(math.isfinite(float(v)) for v in values)


def _validate_entity(entity, tolerance):
    kind = entity.dxftype()
    if kind not in SUPPORTED:
        raise RevisionError(f"unsupported entity type: {kind}")
    extrusion = entity.dxf.get("extrusion", (0, 0, 1))
    if not _finite(extrusion) or math.dist(extrusion, (0, 0, 1)) > 1e-12:
        raise RevisionError("only the default +Z extrusion is supported")
    thickness = entity.dxf.get("thickness", 0)
    if not math.isfinite(thickness) or thickness != 0:
        raise RevisionError("nonzero thickness is unsupported")
    if entity.reactors is not None and len(entity.reactors):
        raise RevisionError("associated entities with reactors cannot be safely split")
    if kind == "LINE":
        points = [entity.dxf.start, entity.dxf.end]
    elif kind in {"ARC", "CIRCLE"}:
        points = [entity.dxf.center]
        if not math.isfinite(entity.dxf.radius) or entity.dxf.radius <= tolerance:
            raise RevisionError("radius must exceed the splitting tolerance")
        if kind == "ARC" and not _finite([entity.dxf.start_angle, entity.dxf.end_angle]):
            raise RevisionError("nonfinite arc angle")
    else:
        if entity.has_width:
            raise RevisionError("polyline width cannot be preserved by primitive splitting")
        if kind == "POLYLINE":
            if not entity.is_2d_polyline or entity.dxf.flags & ~129:
                raise RevisionError("only ordinary 2D polylines are supported")
            points = [entity.dxf.elevation, *(v.dxf.location for v in entity.vertices)]
            for vertex in entity.vertices:
                if vertex.xdata or vertex.appdata or vertex.extension_dict or vertex.reactors:
                    raise RevisionError("per-vertex metadata cannot be transferred to primitives")
                if not math.isfinite(vertex.dxf.get("bulge", 0)):
                    raise RevisionError("nonfinite polyline bulge")
        else:
            if entity.dxf.flags & ~129:
                raise RevisionError("unsupported polyline flags")
            vertices = list(entity.get_points("xyseb"))
            if not all(_finite(v) for v in vertices):
                raise RevisionError("nonfinite polyline vertex")
            points = [(v[0], v[1], entity.dxf.elevation) for v in vertices]
        if len(entity) < 2:
            raise RevisionError("polyline requires at least two vertices")
    if any(not _finite(p) or abs(p[2]) > tolerance for p in points):
        raise RevisionError("only finite geometry in the XY plane is supported")


def _primitive(entity, tolerance):
    _validate_entity(entity, tolerance)
    if entity.dxftype() == "LINE":
        g = _Curve("line", tuple(entity.dxf.start)[:2], tuple(entity.dxf.end)[:2])
    else:
        full = entity.dxftype() == "CIRCLE"
        start = 0 if full else math.radians(entity.dxf.start_angle) % TAU
        span = TAU if full else math.radians(arc_angle_span_deg(entity.dxf.start_angle, entity.dxf.end_angle))
        g = _Curve("arc", tuple(entity.dxf.center)[:2], radius=entity.dxf.radius, start=start, span=span, full=full or span == TAU)
    if g.scale * g.span <= tolerance:
        raise RevisionError("degenerate curve or curve shorter than tolerance")
    g.cuts = [] if g.full else [0.0, g.span]
    return g


def _curves(entity, tolerance):
    _validate_entity(entity, tolerance)
    if entity.dxftype() not in {"LWPOLYLINE", "POLYLINE"}:
        return [_primitive(entity, tolerance)]
    primitives = list(entity.virtual_entities())
    expected = len(entity) if entity.is_closed else len(entity) - 1
    if len(primitives) != expected:
        raise RevisionError("polyline contains omitted or degenerate segments")
    return [_primitive(p, tolerance) for p in primitives]


def _intersections(a, b, tolerance):
    points = []
    if a.kind == b.kind == "line":
        v, w, offset = _sub(a.b, a.a), _sub(b.b, b.a), _sub(b.a, a.a)
        denominator = _cross(v, w)
        if abs(denominator) > 1e-14 * a.scale * b.scale:
            t = _cross(offset, w) / denominator
            points = [a.point(t)]
        else:
            points = [a.a, a.b, b.a, b.b]
    elif a.kind == "line" or b.kind == "line":
        line, circle = (a, b) if a.kind == "line" else (b, a)
        vector = _sub(line.b, line.a)
        unit = (vector[0] / line.scale, vector[1] / line.scale)
        offset = _sub(circle.a, line.a)
        projection = _dot(offset, unit)
        perpendicular = abs(_cross(offset, unit))
        if perpendicular <= circle.radius + tolerance:
            distance = math.sqrt(max(0, (circle.radius - perpendicular) * (circle.radius + perpendicular)))
            points = [line.point((projection + sign * distance) / line.scale) for sign in (-1, 1)]
    else:
        delta = _sub(b.a, a.a)
        distance = math.hypot(*delta)
        if distance <= tolerance and abs(a.radius - b.radius) <= tolerance:
            for curve in (a, b):
                if not curve.full:
                    points.extend([curve.point(0), curve.point(curve.span)])
        elif distance > tolerance and abs(a.radius - b.radius) - tolerance <= distance <= a.radius + b.radius + tolerance:
            along = (a.radius**2 - b.radius**2 + distance**2) / (2 * distance)
            height_sq = (a.radius - along) * (a.radius + along)
            if height_sq >= -tolerance * (2 * a.radius + tolerance):
                height = math.sqrt(max(0, height_sq))
                unit = delta[0] / distance, delta[1] / distance
                middle = a.a[0] + along * unit[0], a.a[1] + along * unit[1]
                points = [(middle[0] - sign * height * unit[1], middle[1] + sign * height * unit[0]) for sign in (-1, 1)]
    result = []
    for p in points:
        if a.parameter(p, tolerance) is not None and b.parameter(p, tolerance) is not None and all(math.dist(p, q) > tolerance for q in result):
            result.append(p)
    return result


def _pairs(curves, tolerance):
    """Bounding-box sweep keeps ordinary drawings from requiring all pairs."""
    bounds = [c.bounds for c in curves]
    active = []
    for i in sorted(range(len(curves)), key=lambda n: bounds[n][0]):
        x0, y0, _, y1 = bounds[i]
        active = [j for j in active if bounds[j][2] >= x0 - tolerance]
        for j in active:
            if bounds[j][1] <= y1 + tolerance and bounds[j][3] >= y0 - tolerance:
                yield j, i
        active.append(i)


def _unique(values, tolerance):
    result = []
    for value in sorted(values):
        if not result or value - result[-1] > tolerance:
            result.append(value)
    return result


def _intervals(curve, tolerance):
    cuts = _unique(curve.cuts, tolerance / curve.scale)
    if curve.full:
        if not cuts:
            return []
        if len(cuts) == 1:
            cuts = sorted([cuts[0], (cuts[0] + math.pi) % TAU])
        cuts.append(cuts[0] + TAU)
    return list(zip(cuts, cuts[1:]))


def _new_piece(parent, curve, start, end):
    """Copy all transferable CAD attributes and application metadata."""
    kind = "LINE" if curve.kind == "line" else "ARC"
    copied = parent.copy(copy_strategy=_COPY)
    if copied.dxftype() == kind:
        child = copied
    else:
        child = factory.new(kind)
        child.doc = parent.doc
        child.update_dxf_attribs({k: v for k, v in copied.dxfattribs().items() if k not in {"handle", "owner"} and child.dxf.is_supported(k)})
        for attr in ("xdata", "appdata", "extension_dict", "reactors", "proxy_graphic"):
            setattr(child, attr, getattr(copied, attr))
    if curve.kind == "line":
        child.dxf.start = (*curve.point(start), 0)
        child.dxf.end = (*curve.point(end), 0)
    else:
        child.dxf.center = (*curve.a, 0)
        child.dxf.radius = curve.radius
        child.dxf.start_angle = math.degrees(curve.start + start) % 360
        child.dxf.end_angle = math.degrees(curve.start + end) % 360
    return child


def _annotation_curves(doc, tolerance):
    # Virtual entities read native cached geometry. The caller must regenerate
    # stale native annotation caches before requesting this optional behavior.
    from ezdxf.disassemble import recursive_decompose

    result, unsupported = [], []
    for entity in doc.modelspace():
        if entity.dxftype() not in ANNOTATIONS:
            continue
        try:
            for child in recursive_decompose([entity]):
                if child.dxftype() in SUPPORTED:
                    result.extend(_curves(child, tolerance))
        except Exception as exc:
            unsupported.append({"handle": entity.dxf.handle, "type": entity.dxftype(), "reason": f"annotation cutter unavailable: {exc}"})
    return result, unsupported


def split_intersections(doc, selectors, tolerance, *, include_annotation_intersections=False):
    """Replace selected supported curves by independent exact LINE/ARC pieces.

    A ``None`` selection considers all modelspace geometry. Explicit selections
    intersect only one another (and, optionally, native annotation caches).
    No coincident edges are merged, even when their styles are identical.
    """
    tolerance = validate_tolerance(tolerance)
    if not isinstance(include_annotation_intersections, bool):
        raise RevisionError("include_annotation_intersections must be boolean")
    original = list(doc.modelspace())
    selected = original if selectors is None else resolve_entities(doc, selectors)
    records, unsupported = [], []
    for entity in selected:
        if selectors is None and entity.dxftype() in PROTECTED:
            continue
        try:
            curves = _curves(entity, tolerance)
        except (RevisionError, ValueError, TypeError, ZeroDivisionError) as exc:
            if selectors is not None:
                raise RevisionError(f"cannot split {entity.dxftype()} {entity.dxf.handle}: {exc}") from exc
            unsupported.append({"handle": entity.dxf.handle, "type": entity.dxftype(), "reason": str(exc)})
        else:
            records.append((entity, curves))
    physical = [g for _, curves in records for g in curves]
    cutters = []
    if include_annotation_intersections:
        cutters, cutter_errors = _annotation_curves(doc, tolerance)
        unsupported.extend(cutter_errors)
    curves = physical + cutters
    junctions = 0
    for i, j in _pairs(curves, tolerance):
        if i >= len(physical) and j >= len(physical):
            continue
        for p in _intersections(curves[i], curves[j], tolerance):
            for index in (i, j):
                if index < len(physical):
                    curves[index].cuts.append(curves[index].parameter(p, tolerance))
            junctions += 1
    used_ids = {stable_id(e) for e in original if stable_id(e) is not None}
    replacements = []
    mapping, source_ids = {}, {}
    for entity, source_curves in records:
        handle = entity.dxf.handle
        source_ids[handle] = stable_id(entity)
        pieces = [(g, a, b) for g in source_curves for a, b in _intervals(g, tolerance)]
        unchanged = entity.dxftype() not in {"LWPOLYLINE", "POLYLINE"} and (not pieces or len(pieces) == 1)
        if unchanged:
            mapping[handle] = [handle]
            continue
        children = [_new_piece(entity, g, a, b) for g, a, b in pieces]
        for index, child in enumerate(children, 1):
            if source_ids[handle] is not None:
                child_id = f"{source_ids[handle]}/segment/{handle}/{index}"
                suffix = 1
                while child_id in used_ids:
                    child_id = f"{source_ids[handle]}/segment/{handle}/{index}/{suffix}"
                    suffix += 1
                tags = [(tag.code, tag.value) for tag in child.get_xdata("CAD_SKILL_ID")]
                tags[0] = (1000, child_id)
                child.set_xdata("CAD_SKILL_ID", tags)
                used_ids.add(child_id)
        replacements.append((entity, handle, children))
    layout = doc.modelspace()
    added = []
    try:
        for _, handle, children in replacements:
            for child in children:
                layout.add_entity(child)
                added.append(child)
            mapping[handle] = [child.dxf.handle for child in children]
    except Exception:
        for child in added:
            layout.delete_entity(child)
        raise
    touched = [handle for _, handle, _ in replacements]
    for entity, _, _ in replacements:
        layout.delete_entity(entity)
    return {
        "status": "partial" if unsupported else "passed",
        "scope": "all" if selectors is None else "selected",
        "selected_handles": [e.dxf.handle for e, _ in records if e.is_alive] + touched,
        "mapping": mapping,
        "source_ids": source_ids,
        "touched_handles": touched,
        "created_handles": [e.dxf.handle for e in added],
        "deleted_handles": touched.copy(),
        "unsupported": unsupported,
        "protected_count": sum(e.dxftype() in PROTECTED for e in original if e.is_alive),
        "intersections_found": junctions,
        "include_annotation_intersections": include_annotation_intersections,
        "tolerance": tolerance,
    }


def _same_carrier(a, b, tolerance):
    if a.kind != b.kind:
        return False
    if a.kind == "arc":
        return math.dist(a.a, b.a) <= tolerance and abs(a.radius - b.radius) <= tolerance
    unit = _sub(a.b, a.a)
    return all(abs(_cross(_sub(p, a.a), unit)) / a.scale <= tolerance for p in (b.a, b.b))


def _carrier_intervals(reference, curve):
    if reference.kind == "line":
        vector = _sub(reference.b, reference.a)
        ends = [_dot(_sub(p, reference.a), vector) / reference.scale for p in (curve.a, curve.b)]
        return [tuple(sorted(ends))]
    circumference = TAU * reference.radius
    if curve.full:
        return [(0, circumference)]
    start = curve.start % TAU * reference.radius
    end = start + curve.span * reference.radius
    return [(start, end)] if end <= circumference else [(start, circumference), (0, end - circumference)]


def _coverage_errors(expected, actual, tolerance):
    """Compare analytic interval coverage with multiplicity on each carrier.

    This verifier does not use the splitter's cuts or its reported lengths:
    independently reconstructed saved output must cover the same positions.
    """
    groups = []
    for curve in expected:
        group = next((g for g in groups if _same_carrier(g[0], curve, tolerance)), None)
        if group is None:
            group = [curve, [], []]
            groups.append(group)
        group[1].extend(_carrier_intervals(group[0], curve))
    errors = []
    for curve in actual:
        group = next((g for g in groups if _same_carrier(g[0], curve, tolerance)), None)
        if group is None:
            errors.append("output is on a different geometric carrier")
        else:
            group[2].extend(_carrier_intervals(group[0], curve))
    for _, before, after in groups:
        boundaries = _unique([v for interval in before + after for v in interval], tolerance)
        for left, right in zip(boundaries, boundaries[1:]):
            if right - left <= tolerance:
                continue
            middle = (left + right) / 2
            if sum(a < middle < b for a, b in before) != sum(a < middle < b for a, b in after):
                errors.append("spatial coverage or multiplicity differs")
                break
    return errors


def _extension_content(entity):
    """Compare owned dictionary content rather than newly allocated handles.

    Identity and ownership before the first subclass marker change on copying;
    pointers in XRECORD payloads remain significant and are not stripped.
    """
    from ezdxf.lldxf.tagwriter import TagCollector

    def content(obj, ancestors):
        if isinstance(obj, str):
            return ('reference', obj)
        identity = id(obj)
        if identity in ancestors:
            return ('cycle', ancestors.index(identity))
        if obj.dxftype() in {'DICTIONARY', 'ACDBDICTIONARYWDFLT'}:
            attrs = {key: value for key, value in obj.dxfattribs().items()
                     if key not in {'handle', 'owner'}}
            # DXF readers materialize defaults that freshly copied dictionaries
            # may omit in memory. Compare their effective values.
            attrs.setdefault('hard_owned', 0)
            attrs.setdefault('cloning', 1)
            return (obj.dxftype(), attrs, {key: content(value, ancestors + [identity])
                                         for key, value in obj.items()})
        result, subclass = [], False
        for tag in TagCollector.dxftags(obj):
            if tag.code == 100:
                subclass = True
            if not subclass and tag.code in {5, 330}:
                continue
            result.append(tag)
        return result

    return content(entity.get_extension_dict().dictionary, []) if entity.extension_dict else None


def _metadata_errors(source, child, changed):
    errors = []
    # Include visibility and other AcDbEntity fields not in graphic_properties().
    geometry = {"handle", "owner", "start", "end", "center", "radius", "start_angle", "end_angle", "flags", "count", "elevation", "const_width", "default_start_width", "default_end_width", "m_count", "n_count", "m_smooth_density", "n_smooth_density", "smooth_type", "vertices_follow"}
    for key, value in source.dxfattribs().items():
        if key not in geometry and child.dxf.is_supported(key) and child.dxf.get(key) != value:
            errors.append(f"attribute differs: {key}")
    source_data = {} if source.xdata is None else source.xdata.data
    child_data = {} if child.xdata is None else child.xdata.data
    if source_data.keys() != child_data.keys():
        errors.append("XDATA application set differs")
    for appid in source_data.keys() & child_data.keys():
        if appid == "CAD_SKILL_ID" and changed and stable_id(source) is not None:
            # XData.data includes the 1001 application tag before the ID.
            left, right = list(source_data[appid]), list(child_data[appid])
            if len(left) != len(right) or left[2:] != right[2:] or not stable_id(child) or stable_id(source) == stable_id(child):
                errors.append("stable child ID or remaining ID metadata differs")
        elif source_data[appid] != child_data[appid]:
            errors.append(f"XDATA differs: {appid}")
    if (None if source.appdata is None else source.appdata.data) != (None if child.appdata is None else child.appdata.data):
        errors.append("application data differs")
    if _extension_content(source) != _extension_content(child):
        errors.append("extension dictionary content differs")
    return errors


def _minimum_covering_multiplicity(reference, curves, tolerance):
    """Count baseline coverage across the whole output segment, including parents."""
    domain = _carrier_intervals(reference, reference)
    intervals = [interval for curve in curves if _same_carrier(reference, curve, tolerance)
                 for interval in _carrier_intervals(reference, curve)]
    boundaries = _unique([v for interval in domain + intervals for v in interval], tolerance)
    counts = []
    for left, right in zip(boundaries, boundaries[1:]):
        if right - left <= tolerance:
            continue
        middle = (left + right) / 2
        if any(a < middle < b for a, b in domain):
            counts.append(sum(a < middle < b for a, b in intervals))
    return min(counts, default=0)


def _duplicate_counts(before, after, tolerance):
    # Inspect actual drawings, not just claimed outputs: an unreported copied
    # entity must still be counted. Unselected existing coincidences are allowed.
    def supported(entities):
        result = []
        for entity in entities:
            try:
                result.extend(_curves(entity, tolerance))
            except (RevisionError, ValueError, TypeError, ZeroDivisionError):
                continue
        return result

    baseline = supported(before)
    actual = supported(after)
    groups = []
    for curve in actual:
        group = next((g for g in groups if not _coverage_errors([g[0]], [curve], tolerance)), None)
        if group is None:
            groups.append([curve, 1])
        else:
            group[1] += 1
    coincident = introduced = 0
    for curve, count in groups:
        if count < 2:
            continue
        pairs = count * (count - 1) // 2
        existing = _minimum_covering_multiplicity(curve, baseline, tolerance)
        coincident += pairs
        introduced += max(0, pairs - existing * (existing - 1) // 2)
    return coincident, introduced


def _has_interior(curve, p, tolerance):
    t = curve.parameter(p, tolerance)
    if t is None:
        return False
    return curve.full or tolerance < t * curve.scale < curve.span * curve.scale - tolerance


def verify_split(before_doc, after_doc, report, tolerance):
    """Independently check saved geometry, mapping, metadata and true junctions."""
    tolerance = validate_tolerance(tolerance)
    if not isinstance(report, dict) or not isinstance(report.get("mapping"), dict):
        raise RevisionError("split report requires a source-to-output mapping")
    before = {e.dxf.handle: e for e in before_doc.modelspace()}
    after = {e.dxf.handle: e for e in after_doc.modelspace()}
    mapping = report["mapping"]
    mapping_errors, coverage_errors, metadata_errors = [], [], []
    expected_sources, unsupported = {}, []
    selected = before if report.get("scope") == "all" else {h: before[h] for h in report.get("selected_handles", []) if h in before}
    for handle, entity in selected.items():
        if report.get("scope") == "all" and entity.dxftype() in PROTECTED:
            continue
        try:
            expected_sources[handle] = _curves(entity, tolerance)
        except (RevisionError, ValueError, TypeError, ZeroDivisionError):
            unsupported.append(handle)
    if set(mapping) != set(expected_sources):
        mapping_errors.append("source mapping does not cover the requested supported geometry")
    if report.get("scope") not in {"all", "selected"}:
        mapping_errors.append("missing or invalid selection scope")
    if report.get("scope") == "selected" and any(h not in before for h in report.get("selected_handles", [])):
        mapping_errors.append("selected source does not exist")
    owners, output_curves = {}, []
    actual_created, actual_deleted = set(after) - set(before), set(before) - set(after)
    claimed_created = set(report.get("created_handles", []))
    claimed_deleted = set(report.get("deleted_handles", []))
    if actual_created != claimed_created or actual_deleted != claimed_deleted:
        mapping_errors.append("created/deleted handles disagree with actual drawings")
    expected_touched = {source for source, children in mapping.items() if children != [source]}
    if set(report.get("touched_handles", [])) != expected_touched or claimed_deleted != expected_touched:
        mapping_errors.append("touched/deleted handles disagree with source mapping")
    for source, handles in mapping.items():
        if source not in expected_sources:
            continue
        if not isinstance(handles, list) or not handles or len(handles) != len(set(handles)):
            mapping_errors.append(f"{source}: empty, invalid or repeated outputs")
            continue
        actual = []
        if report.get("source_ids", {}).get(source) != stable_id(before[source]):
            mapping_errors.append(f"{source}: source ID differs from baseline")
        for handle in handles:
            if handle in owners:
                mapping_errors.append(f"{handle}: output assigned to multiple sources")
            owners[handle] = source
            entity = after.get(handle)
            if entity is None:
                mapping_errors.append(f"{source}: output {handle} is missing")
                continue
            try:
                curves = _curves(entity, tolerance)
            except (RevisionError, ValueError, TypeError, ZeroDivisionError) as exc:
                coverage_errors.append({"source": source, "output": handle, "reason": str(exc)})
                continue
            actual.extend(curves)
            output_curves.extend((handle, source, g) for g in curves)
            metadata_errors.extend({"source": source, "output": handle, "reason": reason} for reason in _metadata_errors(before[source], entity, handles != [source]))
        coverage_errors.extend({"source": source, "reason": reason} for reason in _coverage_errors(expected_sources[source], actual, tolerance))
    if claimed_created != set(owners) - set(before):
        mapping_errors.append("created output is absent from source mapping")
    ids = [stable_id(e) for e in after.values() if stable_id(e) is not None]
    for entity_id, count in Counter(ids).items():
        if count > 1 and any(stable_id(after[h]) == entity_id for h in claimed_created if h in after):
            mapping_errors.append(f"created output has a nonunique stable ID: {entity_id}")
    # Exact serialized evidence protects native annotations and other unselected
    # entities, including HATCH paths, which dxfattribs() alone does not cover.
    from .common import compare_untouched, snapshot_modelspace
    preservation_errors = compare_untouched(snapshot_modelspace(before_doc), snapshot_modelspace(after_doc), expected_touched | claimed_created, tolerance)
    geometries = [g for _, _, g in output_curves]
    cutters, cutter_errors = _annotation_curves(after_doc, tolerance) if report.get("include_annotation_intersections") else ([], [])
    unsplit = []
    for i, j in _pairs(geometries + cutters, tolerance):
        if i >= len(geometries) and j >= len(geometries):
            continue
        a, b = (geometries + cutters)[i], (geometries + cutters)[j]
        points = _intersections(a, b, tolerance)
        if any((i < len(geometries) and _has_interior(a, p, tolerance)) or (j < len(geometries) and _has_interior(b, p, tolerance)) for p in points):
            unsplit.append([output_curves[k][0] if k < len(geometries) else "annotation" for k in (i, j)])
    coincident, introduced = _duplicate_counts(before.values(), after.values(), tolerance)
    failed = any((mapping_errors, coverage_errors, metadata_errors, preservation_errors, unsplit, introduced))
    return {
        "status": "failed" if failed else "partial" if unsupported or cutter_errors or report.get("unsupported") else "passed",
        "mapping_errors": mapping_errors,
        "coverage_errors": coverage_errors,
        "metadata_errors": metadata_errors,
        "preservation_errors": preservation_errors,
        "unsplit_intersections": unsplit,
        "coincident_output_pairs": coincident,
        "introduced_duplicate_segments": introduced,
        "unsupported": sorted(set(unsupported + [x["handle"] for x in cutter_errors])),
        "checked_sources": len(expected_sources),
    }
