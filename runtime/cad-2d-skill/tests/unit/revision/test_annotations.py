"""Native annotation changes must survive a genuine DXF round trip."""

import ezdxf
import pytest
from ezdxf.lldxf.tagwriter import TagCollector
from ezdxf.math import Vec2
from ezdxf.render.mleader import ConnectionSide

from cad_skill_core.revision.annotations import apply_annotation, check_annotation
from cad_skill_core.revision.common import RevisionError


TOL = 1e-6
LABEL = "4-Φ12 +0.2/0"


def sel(entity):
    return {"handle": entity.dxf.handle}


def serialized(doc):
    # Writing updates DXF save GUIDs itself.  Compare exported native tags for
    # every live database entity so resources and caches are covered as well.
    return {handle: list(TagCollector.dxftags(entity))
            for handle, entity in doc.entitydb.items() if entity.is_alive}


def reopen(doc, tmp_path):
    path = tmp_path / "annotations.dxf"
    doc.saveas(path)
    return ezdxf.readfile(path)


def dimension(doc, *, aligned=False):
    m = doc.modelspace()
    if aligned:
        override = m.add_aligned_dim((2, 3), (14, 19), distance=5, text=LABEL)
    else:
        override = m.add_linear_dim(base=(0, 5), p1=(2, 3), p2=(14, 3), text=LABEL)
    override.render()
    return override.dimension


def mleader(doc):
    builder = doc.modelspace().add_multileader_mtext()
    builder.set_content(LABEL, char_height=2.5)
    builder.set_arrow_properties(name="OPEN", size=1.5)
    builder.add_leader_line(ConnectionSide.left, [Vec2(1, 2), Vec2(4, 8)])
    builder.add_leader_line(ConnectionSide.right, [Vec2(40, 2)])
    builder.build(insert=Vec2(10, 10))
    builder.context.mtext.width = 23
    return builder.multileader


@pytest.mark.parametrize("aligned", [False, True])
def test_dimension_move_preserves_origins_value_exact_text_and_native_cache(tmp_path, aligned):
    doc = ezdxf.new("R2010")
    entity = dimension(doc, aligned=aligned)
    origins = [tuple(entity.dxf.defpoint2), tuple(entity.dxf.defpoint3)]
    value = entity.get_measurement()
    old_geometry = entity.dxf.geometry
    op = {"type": "dimension_move", "target": sel(entity), "position": [7, 26]}
    report = apply_annotation(doc, op, TOL)
    result = reopen(doc, tmp_path)
    edited = result.entitydb[entity.dxf.handle]
    assert edited.dxftype() == "DIMENSION"
    assert [tuple(edited.dxf.defpoint2), tuple(edited.dxf.defpoint3)] == origins
    assert edited.get_measurement() == pytest.approx(value)
    assert edited.dxf.text == LABEL
    assert edited.dxf.geometry != old_geometry
    assert len(result.blocks[edited.dxf.geometry]) > 0
    assert report["touched_handles"] == [entity.dxf.handle]
    assert check_annotation(result, report["verification"], TOL)["status"] == "passed"
    edited.dxf.defpoint2 = (999, 3, 0)
    assert check_annotation(result, report["verification"], TOL)["status"] == "failed"


@pytest.mark.parametrize("mode", [0, 1, 2])
def test_dimension_text_modes_preserve_exact_label_and_measurements(tmp_path, mode):
    doc = ezdxf.new("R2010")
    entity = dimension(doc)
    report = apply_annotation(doc, {"type": "dimension_text_mode", "target": sel(entity), "mode": mode}, TOL)
    result = reopen(doc, tmp_path)
    changed = result.entitydb[entity.dxf.handle]
    assert changed.override().get("dimtmove") == mode
    assert changed.dxf.text == LABEL
    assert changed.get_measurement() == 12
    assert check_annotation(result, report["verification"], TOL)["status"] == "passed"


def test_dimension_move_is_absolute_and_repeat_is_noop():
    doc = ezdxf.new("R2010")
    entity = dimension(doc)
    op = {"type": "dimension_move", "target": sel(entity), "position": [7, 26]}
    apply_annotation(doc, op, TOL)
    geometry = entity.dxf.geometry
    again = apply_annotation(doc, op, TOL)
    assert again["touched_handles"] == []
    assert entity.dxf.geometry == geometry


def test_leader_move_preserves_native_content_width_style_targets_and_bends(tmp_path):
    doc = ezdxf.new("R2010")
    entity = mleader(doc)
    arrows = [[tuple(line.vertices[0]) for line in leader.lines] for leader in entity.context.leaders]
    paths = [[list(line.vertices) for line in leader.lines] for leader in entity.context.leaders]
    before_style = entity.context.mtext.style_handle
    before_arrow = entity.dxf.arrow_head_handle
    entity.proxy_graphic = b"stale graphic"
    report = apply_annotation(doc, {"type": "leader_move", "target": sel(entity), "position": [20, 30]}, TOL)
    result = reopen(doc, tmp_path)
    changed = result.entitydb[entity.dxf.handle]
    assert changed.dxftype() == "MULTILEADER"
    assert tuple(changed.context.mtext.insert) == (20, 30, 0)
    assert changed.context.mtext.default_content == LABEL
    assert changed.context.mtext.width == 23
    assert changed.context.mtext.style_handle == before_style
    assert changed.dxf.arrow_head_handle == before_arrow
    assert [[tuple(line.vertices[0]) for line in leader.lines] for leader in changed.context.leaders] == arrows
    assert [[list(line.vertices) for line in leader.lines] for leader in changed.context.leaders] == paths
    assert changed.proxy_graphic is None
    assert check_annotation(result, report["verification"], TOL)["status"] == "passed"
    changed.context.leaders[0].lines[0].vertices[0] = changed.context.mtext.insert
    assert check_annotation(result, report["verification"], TOL)["status"] == "failed"


@pytest.mark.parametrize("text_type", ["TEXT", "MTEXT"])
def test_link_callout_exact_text_native_type_style_width_and_explicit_target(tmp_path, text_type):
    doc = ezdxf.new("R2010")
    doc.styles.new("Callouts", dxfattribs={"font": "arial.ttf"})
    m = doc.modelspace()
    if text_type == "TEXT":
        text = m.add_text(LABEL, dxfattribs={"insert": (12, 10), "height": 2.5, "width": .8, "style": "Callouts"})
    else:
        text = m.add_mtext(LABEL, dxfattribs={"insert": (12, 10), "char_height": 2.5, "width": 31, "style": "Callouts"})
    line1 = m.add_line((1, 2), (6, 10))
    line2 = m.add_line((12, 10), (6, 10))  # selection order and direction are irrelevant
    unrelated = m.add_text("99", dxfattribs={"insert": (400, 300)})
    deleted = [text.dxf.handle, line2.dxf.handle, line1.dxf.handle]
    op = {"type": "link_callout", "text": sel(text), "lines": [sel(line2), sel(line1)],
          "arrow_target": [1, 2], "arrow_name": "OPEN", "arrow_size": 1.7, "position": [25, 40]}
    report = apply_annotation(doc, op, TOL)
    result = reopen(doc, tmp_path)
    created = result.entitydb[report["created_handles"][0]]
    assert created.dxftype() == "MULTILEADER"
    assert LABEL in created.context.mtext.default_content
    assert "<>" not in created.context.mtext.default_content
    assert created.context.mtext.style_handle == result.styles.get("Callouts").dxf.handle
    assert tuple(created.context.mtext.insert) == (25, 40, 0)
    assert tuple(created.context.leaders[0].lines[0].vertices[0]) == (1, 2, 0)
    assert created.context.arrow_head_size == 1.7
    if text_type == "TEXT":
        assert "\\W0.8;" in created.context.mtext.default_content
    else:
        assert created.context.mtext.width == 31
    assert set(report["deleted_handles"]) == set(deleted)
    assert all(result.entitydb.get(handle) is None for handle in deleted)
    assert result.entitydb[unrelated.dxf.handle].dxf.text == "99"
    assert check_annotation(result, report["verification"], TOL)["status"] == "passed"


def test_link_native_leader_preserves_arrow_style_size_and_suppression(tmp_path):
    for arrow in [True, False]:
        doc = ezdxf.new("R2010")
        m = doc.modelspace()
        text = m.add_text(LABEL, dxfattribs={"insert": (12, 10), "height": 2})
        leader = m.add_leader([(1, 2), (6, 10), (12, 10)], dxfattribs={"has_arrowhead": int(arrow)},
                              override={"dimldrblk": "OPEN", "dimasz": 1.25, "dimscale": 2})
        report = apply_annotation(doc, {"type": "link_callout", "text": sel(text), "lines": [sel(leader)], "arrow_target": [1, 2]}, TOL)
        result = reopen(doc, tmp_path)
        created = result.entitydb[report["created_handles"][0]]
        block = result.entitydb[created.dxf.arrow_head_handle]
        assert block.dxf.name == ("_OPEN" if arrow else "_NONE")
        assert created.context.arrow_head_size == 2.5
        assert check_annotation(result, report["verification"], TOL)["status"] == "passed"


@pytest.mark.parametrize("fault", ["disconnected", "branch", "wrong_target", "ambiguous_arrow", "text_disconnected"])
def test_invalid_callout_fails_without_mutation(fault):
    doc = ezdxf.new("R2010")
    m = doc.modelspace()
    text = m.add_text(LABEL, dxfattribs={"insert": (12, 10)})
    lines = [m.add_line((1, 2), (6, 10)), m.add_line((6, 10), (12, 10))]
    op = {"type": "link_callout", "text": sel(text), "lines": [sel(e) for e in lines],
          "arrow_target": [1, 2], "arrow_name": "", "arrow_size": 1}
    if fault == "disconnected":
        lines[1].dxf.start = (7, 10)
    elif fault == "branch":
        op["lines"].append(sel(m.add_line((6, 10), (8, 12))))
    elif fault == "wrong_target":
        op["arrow_target"] = [6, 10]
    elif fault == "ambiguous_arrow":
        del op["arrow_name"]
    elif fault == "text_disconnected":
        text.dxf.insert = (15, 13)
    before = serialized(doc)
    with pytest.raises(RevisionError):
        apply_annotation(doc, op, TOL)
    assert serialized(doc) == before


@pytest.mark.parametrize("fault", ["unsupported_dimension", "bad_mode", "nonplanar", "nonfinite", "bool_mode"])
def test_invalid_dimension_operation_never_mutates(fault):
    doc = ezdxf.new("R2010")
    entity = dimension(doc)
    op = {"type": "dimension_move", "target": sel(entity), "position": [5, 20]}
    if fault == "unsupported_dimension":
        entity.dxf.dimtype = 3
    elif fault == "bad_mode":
        op = {"type": "dimension_text_mode", "target": sel(entity), "mode": 3}
    elif fault == "bool_mode":
        op = {"type": "dimension_text_mode", "target": sel(entity), "mode": True}
    elif fault == "nonplanar":
        entity.dxf.defpoint2 = (1, 2, 3)
    else:
        op["position"] = [float("nan"), 4]
    before = serialized(doc)
    with pytest.raises(RevisionError):
        apply_annotation(doc, op, TOL)
    assert serialized(doc) == before


def test_checker_detects_exact_text_corruption_after_reload(tmp_path):
    doc = ezdxf.new("R2010")
    entity = mleader(doc)
    report = apply_annotation(doc, {"type": "leader_move", "target": sel(entity), "position": [20, 30]}, TOL)
    result = reopen(doc, tmp_path)
    result.entitydb[entity.dxf.handle].context.mtext.default_content = "<>-Φ12 +0.2/0"
    assert check_annotation(result, report["verification"], TOL)["status"] == "failed"


def test_unknown_annotation_fields_do_not_silently_ignore_user_intent():
    doc = ezdxf.new('R2010')
    entity = dimension(doc)
    before = serialized(doc)
    with pytest.raises(RevisionError, match='unknown'):
        apply_annotation(doc, {'type': 'dimension_move', 'target': sel(entity),
                               'position': [0, 20], 'preserve_arrow': False}, TOL)
    assert serialized(doc) == before


@pytest.mark.parametrize('fields', [{}, {'postion': [1000, 1000]}])
def test_position_check_rejects_missing_or_misspelled_target_position(fields):
    doc = ezdxf.new('R2010')
    entity = mleader(doc)
    checked = check_annotation(doc, {'type': 'leader_move', 'target': sel(entity), **fields}, TOL)
    assert checked['status'] == 'failed'


@pytest.mark.parametrize('metadata', ['text_xdata', 'text_dictionary', 'line_xdata'])
def test_callout_conversion_rejects_metadata_it_cannot_preserve(metadata):
    doc = ezdxf.new('R2010')
    doc.appids.add('USER_DATA')
    m = doc.modelspace()
    text = m.add_text(LABEL, dxfattribs={'insert': (10, 10)})
    line = m.add_line((0, 0), (10, 10))
    if metadata == 'text_xdata':
        text.set_xdata('USER_DATA', [(1000, 'asset record')])
    elif metadata == 'line_xdata':
        line.set_xdata('USER_DATA', [(1000, 'asset record')])
    else:
        text.new_extension_dict().add_xrecord('AUDIT').extend([(1, 'retain')])
    before = serialized(doc)
    with pytest.raises(RevisionError, match='metadata'):
        apply_annotation(doc, {'type': 'link_callout', 'text': sel(text), 'lines': [sel(line)],
                               'arrow_target': [0, 0], 'arrow_name': '', 'arrow_size': 1}, TOL)
    assert serialized(doc) == before
