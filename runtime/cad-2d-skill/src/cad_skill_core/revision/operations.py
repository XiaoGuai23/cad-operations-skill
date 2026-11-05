"""Discoverable field contracts; execution remains in the verified revision workflow."""


def operation_catalog() -> dict:
    entries = [
        ('align', ['targets', 'anchor'], ['reference', 'reference_anchor', 'coordinate', 'offset'],
         'Provide reference + reference_anchor OR coordinate.'),
        ('contact', ['targets', 'anchor', 'reference', 'reference_anchor'], ['offset'],
         'Finite straight boundary overlap is required; contact offset is zero.'),
        ('resize_fixed_edge', ['targets', 'axis', 'fixed', 'size'], [],
         'size is the final span; fixed is min or max.'),
        ('set_length', ['target', 'length', 'fixed'], [], 'LINE only; fixed is start or end.'),
        ('dimension_move', ['target', 'position'], [], 'Native XY linear/aligned DIMENSION.'),
        ('dimension_text_mode', ['target', 'mode'], [], 'Native DIMENSION; mode is 0, 1 or 2.'),
        ('leader_move', ['target', 'position'], [], 'Native MULTILEADER with MTEXT content.'),
        ('link_callout', ['text', 'lines', 'arrow_target'], ['arrow_name', 'arrow_size', 'position'],
         'Explicit TEXT/MTEXT + connected LINE/LEADER chain; plain LINE chain needs arrow settings.'),
        ('split_intersections', [], ['targets', 'include_annotation_intersections'],
         'Last operation only; HATCH and native annotation semantics are preserved.'),
        ('delete_entities', ['targets'], [], 'Delete explicit native entities and their display; no adjacent geometry selection.'),
        ('replace_outline', ['targets', 'replacements'], [],
         'Explicit LINE/ARC/CIRCLE replacements each require id, source, type and geometry; unsupported metadata is rejected.'),
        ('dimension_remeasure', ['target', 'p1', 'p2'], [],
         'Native linear/aligned dimension endpoints; preserves text semantics, style and text movement mode.'),
    ]
    return {'schema': 'cad-operation-catalog/v1', 'status': 'available',
            'plan_schema': 'cad-revision/v1', 'common_required': ['id', 'type'],
            'selector': 'Exactly one handle or stable id, from the bound input DXF.',
            'operations': [{'type': name, 'required': required, 'optional': optional, 'limits': limits,
                            'documentation': 'docs/edit-operations.md' if name in
                            {'delete_entities', 'replace_outline', 'dimension_remeasure'} else 'docs/revision-tools.md'}
                           for name, required, optional, limits in entries],
            'native_interaction_tested': False}
