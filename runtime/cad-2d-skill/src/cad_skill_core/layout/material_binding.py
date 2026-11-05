"""Resolve material declarations from finalized, source-bound annotations."""

from __future__ import annotations

import ezdxf

from cad_skill_core.layout.models import LayoutPlan
from cad_skill_core.schema.models import DrawingSpec


def resolve_material_handles(
    document: ezdxf.document.Drawing,
    spec: DrawingSpec,
    plan: LayoutPlan,
) -> tuple[str, ...]:
    """Return reopened handles that explicitly declare the sourced material.

    Material provenance is represented only by a same-source technical note or
    free annotation body.  The finalized layout is the handle authority, so
    unrelated drawing text, titles, parts rows, and annotation leaders cannot
    be claimed as material evidence.
    """

    material = spec.requirements.material
    if material is None:
        return ()
    annotation_ids = [
        f"technical-note-{index}"
        for index, note in enumerate(spec.requirements.technical_notes, start=1)
        if note.source_id == material.source_id
        and _declares_material(note.text, material.text)
    ]
    annotation_ids.extend(
        f"free-{index}-text"
        for index, annotation in enumerate(spec.annotations, start=1)
        if annotation.source_id == material.source_id
        and _declares_material(annotation.text, material.text)
    )
    annotations = {item.annotation_id: item for item in plan.annotations}
    handles: list[str] = []
    for annotation_id in annotation_ids:
        annotation = annotations.get(annotation_id)
        if annotation is None:
            continue
        for handle in annotation.entity_handles:
            entity = document.entitydb.get(handle)
            if entity is not None and _declares_material(_entity_text(entity), material.text):
                handles.append(handle)
    return tuple(handles)


def _declares_material(text: str, material: str) -> bool:
    return text == material or text.casefold() == f"MATERIAL: {material}".casefold()


def _entity_text(entity: object) -> str:
    if entity.dxftype() == "TEXT":
        return str(entity.dxf.get("text", ""))
    if entity.dxftype() == "MTEXT":
        try:
            return str(entity.plain_text())
        except Exception:
            return str(entity.dxf.get("text", ""))
    return ""
