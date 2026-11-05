"""Stable semantic snapshots for reviewed CAD acceptance drawings."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf import bbox


REQUIRED_TEXT_TOKENS = (
    "法兰",
    "⌀100",
    "90°",
    "±0.10",
    "FRONT",
    "PART-DETAIL",
    "SECTION A-A",
    "PARTS LIST",
)
_SINGLETON_SEMANTIC_KINDS = {
    "view_label",
    "dimension",
    "part_description",
    "part_quantity",
    "balloon_number",
    "technical_note",
}


def semantic_snapshot(dxf_path: Path, audit_path: Path) -> dict[str, object]:
    """Return reviewed semantics while excluding volatile CAD representation.

    Handles, timestamps, absolute paths, temporary roots, and binary DWG/PDF
    bytes are intentionally absent. Audit inventory handles route extraction
    from the reopened final DXF but never enter this stable payload.
    """

    dxf_path = Path(dxf_path)
    audit_path = Path(audit_path)
    document = ezdxf.readfile(dxf_path)
    dxf_audit = document.audit()
    if dxf_audit.has_errors:
        raise ValueError("golden DXF has ezdxf audit errors")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))

    entities = tuple(document.modelspace())
    modelspace_by_handle = {str(entity.dxf.handle): entity for entity in entities}
    entity_counts = Counter(entity.dxftype() for entity in entities)
    layer_counts = Counter(str(entity.dxf.get("layer", "0")) for entity in entities)
    style_counts: Counter[str] = Counter()
    drawing_text: list[str] = []
    for entity in entities:
        if entity.dxftype() == "TEXT":
            drawing_text.append(str(entity.dxf.get("text", "")))
            style_counts[str(entity.dxf.get("style", "Standard"))] += 1
        elif entity.dxftype() == "MTEXT":
            drawing_text.append(entity.plain_text())
            style_counts[str(entity.dxf.get("style", "Standard"))] += 1
        elif entity.dxftype() == "DIMENSION":
            drawing_text.append(str(entity.dxf.get("text", "")))
            style_counts[str(entity.dxf.get("dimstyle", "Standard"))] += 1

    extents = bbox.extents(entities)
    if not extents.has_data:
        raise ValueError("golden DXF has no measurable extents")
    normalized_extents = [
        _stable_number(value)
        for value in (
            extents.extmin.x,
            extents.extmin.y,
            extents.extmax.x,
            extents.extmax.y,
        )
    ]
    text = "\n".join(drawing_text)
    inventory = audit["semantic_inventory"]
    _reject_singleton_handle_aliases(inventory)
    routed = [
        (record, _routed_entities(record, modelspace_by_handle))
        for record in inventory
    ]
    views = []
    source_views = {
        record["semantic_id"]: (record, actual)
        for record, actual in routed
        if record["semantic_kind"] == "view_source"
    }
    for record, actual in routed:
        if record["semantic_kind"] != "view_label":
            continue
        view_id = record["semantic_id"]
        source_route = source_views.get(view_id)
        if source_route is None:
            raise ValueError(f"view label has no routed source entities: {view_id}")
        source_record, source_entities = source_route
        source_bounds = _normalized_bounds(source_entities)
        expected_bounds = source_record.get("normalized_source_bounds_mm")
        if source_bounds != expected_bounds:
            raise ValueError(
                f"view source bounds do not match committed inventory: {view_id}"
            )
        views.append(
            {
                "id": view_id,
                "label": _single_text(actual, record),
                "source_membership": source_record["source_membership"],
                "normalized_source_bounds_mm": source_bounds,
                "source_entity_counts": dict(
                    sorted(Counter(entity.dxftype() for entity in source_entities).items())
                ),
            }
        )
    dimensions = []
    for record, actual in routed:
        if record["semantic_kind"] != "dimension":
            continue
        if len(actual) != 1 or actual[0].dxftype() != "DIMENSION":
            raise ValueError(f"dimension inventory route is invalid: {record['semantic_id']}")
        entity = actual[0]
        dimensions.append(
            {
                "id": record["semantic_id"],
                "display": str(entity.dxf.get("text", "")),
                "measurement_mm": _stable_number(entity.get_measurement()),
            }
        )
    parts_by_number: dict[int, dict[str, object]] = {}
    balloon_numbers: list[str] = []
    technical_notes: list[str] = []
    for record, actual in routed:
        kind = record["semantic_kind"]
        if kind in {"part_description", "part_quantity"}:
            item_no = int(record["item_no"])
            part = parts_by_number.setdefault(item_no, {"item_no": item_no})
            value = _single_text(actual, record)
            if kind == "part_description":
                part["description"] = value
            else:
                try:
                    part["quantity"] = int(value)
                except ValueError as error:
                    raise ValueError(
                        f"part quantity is not an integer in final DXF: {value!r}"
                    ) from error
        elif kind == "balloon_number":
            balloon_numbers.append(_single_text(actual, record))
        elif kind == "technical_note":
            technical_notes.append(_single_text(actual, record))
    parts = [parts_by_number[number] for number in sorted(parts_by_number)]
    if any(set(part) != {"item_no", "description", "quantity"} for part in parts):
        raise ValueError("parts list inventory is incomplete")
    artifact_suffixes = sorted(
        {Path(item["name"]).suffix for item in audit["artifacts"]}
    )
    verified_roles = sorted(audit["font_render"]["verified_roles"])
    evidence = "\n".join(audit["font_render"]["evidence"])

    return {
        "schema": "Cad2DGoldenSemanticSnapshot/v2",
        "dxf": {
            "version": document.dxfversion,
            "entity_counts": dict(sorted(entity_counts.items())),
            "layer_entity_counts": dict(sorted(layer_counts.items())),
            "bounded_extents_mm": normalized_extents,
        },
        "text": {
            "required_tokens": {
                token: token in text for token in REQUIRED_TEXT_TOKENS
            },
            "style_entity_counts": dict(sorted(style_counts.items())),
        },
        "semantics": {
            "views": views,
            "dimensions": dimensions,
            "parts_list": parts,
            "balloon_numbers": balloon_numbers,
            "technical_notes": technical_notes,
        },
        "audit": {
            "status": audit["status"],
            "counts": audit["summary"],
            "issue_codes": sorted(item["code"] for item in audit["issues"]),
        },
        "font_render": {
            "verified_roles": verified_roles,
            "all_roles_have_exact_preview_evidence": all(
                f"{role} font rendering verified" in evidence
                for role in verified_roles
            ),
            "fallback_fields": {
                "inline_font_overrides": sum(
                    "\\F" in value or "\\f" in value for value in drawing_text
                ),
                "unverified_roles": sorted(
                    {"TEXT", "DIM"}.intersection(style_counts) - set(verified_roles)
                ),
            },
        },
        "provenance": {
            "canonical_spec_sha256": audit["provenance"][
                "canonical_spec_file_sha256"
            ],
            "source_files": [
                {
                    "path": item["path"],
                    "sha256": item["sha256"],
                }
                for item in audit["provenance"]["source_files"]
            ],
        },
        "published_suffixes": artifact_suffixes,
    }


def _stable_number(value: Any) -> float:
    rounded = round(float(value), 6)
    return 0.0 if rounded == 0.0 else rounded


def _routed_entities(
    record: dict[str, object],
    modelspace_by_handle: dict[str, object],
) -> tuple[object, ...]:
    handles = tuple(str(handle) for handle in record.get("handles", ()))
    if not handles or len(handles) != len(set(handles)):
        raise ValueError(
            f"semantic inventory handles are empty or duplicated: {record!r}"
        )
    try:
        return tuple(modelspace_by_handle[handle] for handle in handles)
    except KeyError as error:
        raise ValueError(
            f"semantic inventory handle is absent from final DXF: {error.args[0]}"
        ) from error


def _single_text(actual: tuple[object, ...], record: dict[str, object]) -> str:
    if len(actual) != 1 or actual[0].dxftype() not in {"TEXT", "MTEXT"}:
        raise ValueError(f"text inventory route is invalid: {record['semantic_id']}")
    entity = actual[0]
    return (
        entity.plain_text()
        if entity.dxftype() == "MTEXT"
        else str(entity.dxf.get("text", ""))
    )


def _reject_singleton_handle_aliases(inventory: list[dict[str, object]]) -> None:
    owner_by_handle: dict[str, str] = {}
    for record in inventory:
        if record.get("semantic_kind") not in _SINGLETON_SEMANTIC_KINDS:
            continue
        handles = tuple(str(handle) for handle in record.get("handles", ()))
        if len(handles) != 1:
            raise ValueError(
                f"singleton semantic record must route one handle: {record!r}"
            )
        handle = handles[0]
        identity = f"{record['semantic_kind']}:{record['semantic_id']}"
        prior = owner_by_handle.get(handle)
        if prior is not None:
            raise ValueError(
                f"handle {handle} aliases singleton semantic records: "
                f"{prior}, {identity}"
            )
        owner_by_handle[handle] = identity


def _normalized_bounds(actual: tuple[object, ...]) -> list[float]:
    extents = bbox.extents(actual)
    if not extents.has_data:
        raise ValueError("semantic view source has no measurable extents")
    return [
        _stable_number(value)
        for value in (
            extents.extmin.x,
            extents.extmin.y,
            extents.extmax.x,
            extents.extmax.y,
        )
    ]
