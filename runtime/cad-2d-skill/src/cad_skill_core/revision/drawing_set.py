"""Read-only, hash-bound verification of explicit relationships between DXFs."""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
import re

import ezdxf
from ezdxf.filemanagement import dxf_stream_info
from ezdxf.lldxf.tagger import binary_tags_loader

from .common import RevisionError, resolve_entity, sha256_file, validate_tolerance


UNITS = {"in": 1, "ft": 2, "mm": 4, "cm": 5, "m": 6}
MEASURES = {"dimension", "length", "radius", "diameter"}


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RevisionError(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise RevisionError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise RevisionError(f"{name} must be a finite number")
    return number


def _point(entity, name):
    if not entity.dxf.hasattr(name):
        raise RevisionError(f"missing measurement geometry: {name}")
    return tuple(_number(v, name) for v in entity.dxf.get(name))


def _planar(entity, points):
    extrusion = tuple(_number(v, "extrusion") for v in entity.dxf.get("extrusion", (0, 0, 1)))
    if any(abs(a - b) > 1e-12 for a, b in zip(extrusion, (0, 0, 1))):
        raise RevisionError("unsupported extrusion for planar measurement")
    if entity.dxftype() != "DIMENSION" and _number(entity.dxf.get("thickness", 0), "thickness") != 0:
        raise RevisionError("nonzero thickness is unsupported for planar measurement")
    if any(len(p) != 3 for p in points) or max(p[2] for p in points) - min(p[2] for p in points) > 1e-9:
        raise RevisionError("nonplanar measurement is unsupported")


def measure_feature(doc, selector: dict, measure: str) -> float:
    """Measure one live modelspace entity in native drawing units without edits.

    ``dimension`` uses linear/aligned/radial/diametric definition geometry,
    ignoring display text, DIMLFAC and the cached actual_measurement attribute.
    Unsupported, ambiguous, nonplanar or degenerate geometry raises RevisionError.
    """
    if not isinstance(measure, str) or measure not in MEASURES:
        raise RevisionError(f"unsupported measure: {measure!r}")
    entity = resolve_entity(doc, selector)
    kind = entity.dxftype()
    if measure == "dimension":
        if kind != "DIMENSION" or entity.dimtype not in (0, 1, 3, 4):
            raise RevisionError("dimension requires native linear/aligned/radius/diameter DIMENSION")
        names = ("defpoint2", "defpoint3") if entity.dimtype in (0, 1) else ("defpoint", "defpoint4")
        points = [_point(entity, name) for name in names]
        _planar(entity, points)
        dx, dy = points[1][0] - points[0][0], points[1][1] - points[0][1]
        if entity.dimtype == 0:
            angle = math.radians(_number(entity.dxf.get("angle", 0), "dimension angle"))
            value = abs(dx * math.cos(angle) + dy * math.sin(angle))
        else:
            value = math.hypot(dx, dy)
    elif measure == "length" and kind == "LINE":
        points = [_point(entity, "start"), _point(entity, "end")]
        _planar(entity, points)
        value = math.hypot(points[1][0] - points[0][0], points[1][1] - points[0][1])
    elif measure in ("radius", "diameter") and kind in ("CIRCLE", "ARC"):
        _planar(entity, [_point(entity, "center")])
        if not entity.dxf.hasattr("radius"):
            raise RevisionError("missing measurement geometry: radius")
        if kind == "ARC":
            _number(entity.dxf.start_angle, "arc start angle")
            _number(entity.dxf.end_angle, "arc end angle")
        value = _number(entity.dxf.radius, "radius") * (2 if measure == "diameter" else 1)
    else:
        raise RevisionError(f"unsupported {kind} for {measure} measurement")
    value = _number(value, "measured value")
    if value <= 0:
        raise RevisionError("zero or degenerate measurement is unsupported")
    return value


def _object(value, fields, name):
    if not isinstance(value, dict) or set(value) != fields:
        raise RevisionError(f"{name} requires exactly these fields: {', '.join(sorted(fields))}")


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise RevisionError(f"{name} must be a nonempty trimmed string")


def _list(value, name):
    if not isinstance(value, list) or not value:
        raise RevisionError(f"{name} must be a nonempty list")


def _selector(value):
    if not isinstance(value, dict) or set(value) not in ({"handle"}, {"id"}):
        raise RevisionError("selector requires exactly one handle or id")
    _text(next(iter(value.values())), "selector value")
    if "handle" in value and not re.fullmatch(r"[0-9a-fA-F]+", value["handle"]):
        raise RevisionError("invalid entity handle")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RevisionError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise RevisionError(f"nonfinite JSON value: {value}")


def _validate(manifest):
    _object(manifest, {"schema", "units", "drawings", "parameters"}, "drawing set")
    if manifest["schema"] != "cad-drawing-set/v1":
        raise RevisionError("unsupported drawing set schema")
    if not isinstance(manifest["units"], str) or manifest["units"] not in UNITS:
        raise RevisionError("units must be mm, cm, m, in or ft; no implicit unit conversion")
    _list(manifest["drawings"], "drawings")
    _list(manifest["parameters"], "parameters")
    drawing_ids = set()
    for drawing in manifest["drawings"]:
        _object(drawing, {"id", "path", "sha256", "editable"}, "drawing")
        _text(drawing["id"], "drawing id")
        _text(drawing["path"], "drawing path")
        if "\x00" in drawing["path"]:
            raise RevisionError("invalid drawing path")
        if drawing["id"] in drawing_ids:
            raise RevisionError("duplicate drawing id")
        drawing_ids.add(drawing["id"])
        if not isinstance(drawing["sha256"], str) or not re.fullmatch(r"[0-9a-fA-F]{64}", drawing["sha256"]):
            raise RevisionError("drawing sha256 requires 64 hex digits")
        if not isinstance(drawing["editable"], bool):
            raise RevisionError("drawing editable must be an explicit boolean")
    parameter_ids = set()
    for parameter in manifest["parameters"]:
        _object(parameter, {"id", "expected", "tolerance", "members"}, "parameter")
        _text(parameter["id"], "parameter id")
        if parameter["id"] in parameter_ids:
            raise RevisionError("duplicate parameter id")
        parameter_ids.add(parameter["id"])
        if _number(parameter["expected"], "expected") < 0:
            raise RevisionError("expected measurement cannot be negative")
        validate_tolerance(parameter["tolerance"])
        _list(parameter["members"], "parameter members")
        for member in parameter["members"]:
            _object(member, {"drawing", "selector", "measure"}, "parameter member")
            if not isinstance(member["drawing"], str) or member["drawing"] not in drawing_ids:
                raise RevisionError("member refers to an unknown drawing")
            _selector(member["selector"])
            if not isinstance(member["measure"], str) or member["measure"] not in MEASURES:
                raise RevisionError("unsupported member measure")


def read_dxf_snapshot(data: bytes):
    """Read exactly the bound bytes; reject audit errors and automatic repairs."""
    if data.startswith(b"AutoCAD Binary DXF\r\n\x1a\x00"):
        doc = ezdxf.document.Drawing.load(binary_tags_loader(data, errors="surrogateescape"))
    else:
        info = dxf_stream_info(io.StringIO(data.decode("utf-8", errors="ignore"), newline=None))
        doc = ezdxf.read(io.StringIO(data.decode(info.encoding, errors="surrogateescape"), newline=None))
    audit = doc.audit()
    if audit.has_errors or audit.has_fixes:
        raise RevisionError('DXF audit found errors or automatic repairs; inspect a separate copy')
    return doc


def _status(items):
    states = {item["status"] for item in items}
    return "blocked" if "blocked" in states else "needs_update" if "needs_update" in states else "checked"


def check_drawing_set(manifest_path: Path) -> dict:
    """Return complete evidence; never write a source, manifest or sidecar file.

    Structural manifest errors raise RevisionError. Source, identity, unit and
    measurement failures return blocked entries and never count as checked.
    """
    manifest_path = Path(manifest_path).resolve()
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique, parse_constant=_invalid_constant)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RevisionError(f"cannot read drawing set manifest: {exc}") from exc
    _validate(manifest)
    report = {"schema": "cad-drawing-set-report/v1", "manifest_path": str(manifest_path),
              "manifest_sha256": hashlib.sha256(raw).hexdigest(), "units": manifest["units"],
              "status": "blocked", "drawings": [], "parameters": [], "blockers": []}
    docs, paths, identities = {}, {}, {}

    def block(entry, code, message, **context):
        issue = {"code": code, "message": message, **context}
        entry["status"] = "blocked"
        entry.setdefault("blockers", []).append(issue)
        report["blockers"].append(issue)

    for drawing in manifest["drawings"]:
        identifier = drawing["id"]
        entry = {**drawing, "expected_sha256": drawing["sha256"].lower(), "actual_sha256": None,
                 "actual_units": None, "scope": "editable" if drawing["editable"] else "out_of_scope",
                 "status": "checked", "blockers": []}
        report["drawings"].append(entry)
        try:
            path = (manifest_path.parent / drawing["path"]).resolve()
            entry["path"] = str(path)
            paths[identifier] = path
            stat = path.stat()
            identities[identifier] = (str(path).casefold(), (stat.st_dev, stat.st_ino))
            data = path.read_bytes()
        except OSError as exc:
            block(entry, "missing_file" if isinstance(exc, FileNotFoundError) else "unreadable_file",
                  str(exc), drawing=identifier)
            continue
        entry["actual_sha256"] = hashlib.sha256(data).hexdigest()
        if entry["actual_sha256"] != entry["expected_sha256"]:
            block(entry, "hash_mismatch", "drawing hash differs from manifest binding", drawing=identifier)
            continue
        try:
            doc = read_dxf_snapshot(data)
            entry["actual_units"] = int(doc.units)
        except (ezdxf.DXFError, ValueError, UnicodeError, OSError, IndexError) as exc:
            block(entry, "invalid_drawing", f"cannot read DXF: {exc}", drawing=identifier)
            continue
        if doc.units != UNITS[manifest["units"]]:
            block(entry, "unit_mismatch", "DXF units differ from manifest; no conversion performed", drawing=identifier)
            continue
        docs[identifier] = doc

    entries = {entry["id"]: entry for entry in report["drawings"]}
    identifiers = list(identities)
    for i, identifier in enumerate(identifiers):
        canonical, file_id = identities[identifier]
        for other in identifiers[:i]:
            other_path, other_id = identities[other]
            if canonical == other_path or (file_id[1] and file_id == other_id):
                for current, alias in ((identifier, other), (other, identifier)):
                    block(entries[current], "aliased_path", f"same physical source as drawing {alias}", drawing=current)

    for parameter in manifest["parameters"]:
        result = {"id": parameter["id"], "expected": parameter["expected"],
                  "tolerance": parameter["tolerance"], "members": [], "status": "blocked"}
        report["parameters"].append(result)
        seen_members = {}
        for member in parameter["members"]:
            drawing = entries[member["drawing"]]
            item = {**member, "editable": drawing["editable"], "scope": drawing["scope"],
                    "status": "blocked", "actual": None, "delta": None, "blockers": []}
            result["members"].append(item)
            if drawing["status"] == "blocked":
                item["blockers"] = list(drawing["blockers"])
                continue
            try:
                entity = resolve_entity(docs[member["drawing"]], member["selector"])
                item["handle"] = entity.dxf.handle
                identity = (member["drawing"], entity.dxf.handle, member["measure"])
                if identity in seen_members:
                    for repeated in (seen_members[identity], item):
                        block(repeated, "duplicate_member", "multiple members resolve to the same feature and measure",
                              drawing=member["drawing"], parameter=parameter["id"])
                    continue
                seen_members[identity] = item
                actual = measure_feature(docs[member["drawing"]], member["selector"], member["measure"])
                delta = _number(actual - parameter["expected"], "measurement difference")
            except (RevisionError, ezdxf.DXFError, AttributeError, TypeError, ValueError, OverflowError) as exc:
                block(item, "unmeasurable_member", str(exc), drawing=member["drawing"], parameter=parameter["id"])
                continue
            item.update(actual=actual, delta=delta,
                        status="checked" if abs(delta) <= parameter["tolerance"] else "needs_update")

    # Reading happens from verified snapshots. A final check also prevents a
    # concurrently replaced/deleted file from receiving a current checked state.
    for identifier, doc in docs.items():
        entry = entries[identifier]
        try:
            final_hash = sha256_file(paths[identifier])
        except OSError:
            final_hash = None
        entry["final_sha256"] = final_hash
        if final_hash != entry["expected_sha256"]:
            block(entry, "source_changed", "drawing changed or disappeared during verification", drawing=identifier)
            for parameter in report["parameters"]:
                for member in parameter["members"]:
                    if member["drawing"] == identifier:
                        member.update(status="blocked", blockers=list(entry["blockers"]))
    for parameter in report["parameters"]:
        parameter["status"] = _status(parameter["members"])
    report["status"] = _status(report["drawings"] + report["parameters"])
    return report
