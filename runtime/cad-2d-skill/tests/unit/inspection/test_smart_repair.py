from __future__ import annotations

from dataclasses import replace

import ezdxf

from cad_skill_core.inspection.models import RepairAction
from cad_skill_core.inspection.repair import (
    _RepairState,
    _apply_action,
    _apply_plan_state,
    _solve_text_avoidance_shift,
)
from cad_skill_core.layout.models import Bounds


def test_smart_text_avoidance_separates_colliding_annotations_instead_of_moving_both_together(
    valid_context,
):
    plan = valid_context.plan
    header = next(a for a in plan.annotations if a.annotation_id == "notes-header")
    note = next(a for a in plan.annotations if a.annotation_id == "technical-note-1")
    overlapping_note = replace(
        note,
        bounds=Bounds(
            header.bounds.min_x,
            header.bounds.min_y,
            header.bounds.min_x + note.bounds.width,
            header.bounds.max_y,
        ),
        points=header.points,
    )
    collided_plan = replace(
        plan,
        annotations=tuple(
            overlapping_note if a.annotation_id == note.annotation_id else a
            for a in plan.annotations
        ),
    )
    assert header.bounds.intersects(overlapping_note.bounds)

    state = _RepairState(
        valid_context.standard.model_copy(deep=True),
        dict(valid_context.font_result),
        {},
        {},
    )
    both_handles = ",".join(sorted((*header.entity_handles, *overlapping_note.entity_handles)))
    _apply_action(
        valid_context.spec.model_copy(deep=True),
        state,
        RepairAction("SHIFT_TEXT", both_handles, {"distance_mm": 1.0}),
        collided_plan,
        ezdxf.readfile(valid_context.dxf_path),
    )

    assert header.annotation_id not in state.text_shifts
    assert note.annotation_id in state.text_shifts

    repaired_plan = _apply_plan_state(collided_plan, state.text_shifts)
    repaired_header = next(a for a in repaired_plan.annotations if a.annotation_id == "notes-header")
    repaired_note = next(a for a in repaired_plan.annotations if a.annotation_id == "technical-note-1")

    assert not repaired_header.bounds.intersects(repaired_note.bounds)
    assert repaired_plan.technical_note_bounds.contains(repaired_note.bounds)
    assert repaired_plan.printable_bounds.contains(repaired_note.bounds)


def test_smart_text_avoidance_avoids_views_and_protected_zones_for_outside_labels(valid_context):
    plan = valid_context.plan
    label = next(a for a in plan.annotations if a.kind == "view-label")
    dx, dy = _solve_text_avoidance_shift(label, plan, {}, 1.0)
    shifted = Bounds(
        label.bounds.min_x + dx,
        label.bounds.min_y + dy,
        label.bounds.max_x + dx,
        label.bounds.max_y + dy,
    )
    assert plan.printable_bounds.contains(shifted)
    assert not shifted.intersects(plan.title_block_bounds)
    assert all(not shifted.intersects(v.bounds) for v in plan.views)
