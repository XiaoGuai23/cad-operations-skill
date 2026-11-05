"""Real geometric evidence must never be replaced by display text or stale sources."""
import hashlib
import importlib
import json
from pathlib import Path

import ezdxf
import pytest

from cad_skill_core.revision.common import RevisionError, snapshot_modelspace


def api():
    return importlib.import_module("cad_skill_core.revision.drawing_set")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def drawing(tmp_path, name, length, *, editable=False, units=4):
    doc = ezdxf.new("R2010")
    doc.units = units
    dimension = doc.modelspace().add_linear_dim(
        base=(0, 5), p1=(0, 0), p2=(length, 0), angle=0,
        text="20", override={"dimlfac": 10},
    )
    dimension.render()
    path = tmp_path / f"{name}.dxf"
    doc.saveas(path)
    return ({"id": name, "path": path.name, "sha256": digest(path), "editable": editable},
            {"drawing": name, "selector": {"handle": dimension.dimension.dxf.handle}, "measure": "dimension"})


def manifest(tmp_path, lengths=(20, 10)):
    pairs = [drawing(tmp_path, name, length, editable=name == "assembly")
             for name, length in zip(("assembly", "part"), lengths)]
    return {"schema": "cad-drawing-set/v1", "units": "mm", "drawings": [p[0] for p in pairs],
            "parameters": [{"id": "thickness", "expected": 20, "tolerance": 0.01,
                            "members": [p[1] for p in pairs]}]}


def run(tmp_path, data):
    path = tmp_path / "set.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return api().check_drawing_set(path)


def test_real_20_versus_10_dimensions_fail_despite_same_display_text(tmp_path):
    result = run(tmp_path, manifest(tmp_path))
    assert result["status"] == "needs_update"
    parameter = result["parameters"][0]
    assert parameter["status"] == "needs_update"
    assert [m["actual"] for m in parameter["members"]] == [20, 10]
    assert [m["status"] for m in parameter["members"]] == ["checked", "needs_update"]
    assert parameter["members"][1]["delta"] == -10
    assert parameter["members"][1]["editable"] is False
    assert parameter["members"][1]["scope"] == "out_of_scope"
    assert parameter["members"][0]["scope"] == "editable"


def test_all_members_must_match_explicit_expected_value_within_tolerance(tmp_path):
    data = manifest(tmp_path, (20, 20.005))
    assert run(tmp_path, data)["status"] == "checked"
    data["parameters"][0]["expected"] = 10
    result = run(tmp_path, data)
    assert result["status"] == "needs_update"
    assert all(m["status"] == "needs_update" for m in result["parameters"][0]["members"])


@pytest.mark.parametrize("failure,reason", [("missing", "missing_file"), ("changed", "hash_mismatch"),
                                           ("units", "unit_mismatch"), ("corrupt", "invalid_drawing")])
def test_unverified_sources_block_instead_of_passing(tmp_path, failure, reason):
    data = manifest(tmp_path, (20, 20))
    source = tmp_path / "part.dxf"
    if failure == "missing":
        source.unlink()
    elif failure == "changed":
        source.write_bytes(source.read_bytes() + b"\n")
    elif failure == "units":
        doc = ezdxf.readfile(source)
        doc.units = 1
        doc.saveas(source)
        data["drawings"][1]["sha256"] = digest(source)
    else:
        source.write_text("not a DXF", encoding="utf-8")
        data["drawings"][1]["sha256"] = digest(source)
    result = run(tmp_path, data)
    assert result["status"] == "blocked"
    assert any(b["code"] == reason for b in result["blockers"])
    member = result["parameters"][0]["members"][1]
    assert member["status"] == "blocked"
    assert member["actual"] is None


def test_read_only_check_does_not_modify_or_create_any_files(tmp_path):
    data = manifest(tmp_path)
    path = tmp_path / "set.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    api().check_drawing_set(path)
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize("alias_kind", ["relative", "hardlink"])
def test_same_source_cannot_satisfy_two_distinct_drawings(tmp_path, alias_kind):
    data = manifest(tmp_path, (20, 20))
    if alias_kind == "hardlink":
        alias = tmp_path / "alias.dxf"
        alias.hardlink_to(tmp_path / "assembly.dxf")
        data["drawings"][1]["path"] = alias.name
    else:
        data["drawings"][1]["path"] = "./assembly.dxf"
    data["drawings"][1]["sha256"] = data["drawings"][0]["sha256"]
    result = run(tmp_path, data)
    assert result["status"] == "blocked"
    assert any(b["code"] == "aliased_path" for b in result["blockers"])
    assert all(d["status"] == "blocked" for d in result["drawings"])


@pytest.mark.parametrize("change", ["duplicate_drawing", "duplicate_parameter", "unknown_drawing", "empty_parameters",
                                  "empty_members", "unknown_field", "missing_editable", "boolean_expected",
                                  "negative_tolerance", "zero_tolerance", "nonfinite", "bad_hash", "bad_units"])
def test_invalid_manifests_are_rejected_before_checking(tmp_path, change):
    data = manifest(tmp_path)
    if change == "duplicate_drawing":
        data["drawings"][1]["id"] = "assembly"
    elif change == "duplicate_parameter":
        data["parameters"].append(data["parameters"][0].copy())
    elif change == "unknown_drawing":
        data["parameters"][0]["members"][1]["drawing"] = "absent"
    elif change == "empty_parameters":
        data["parameters"] = []
    elif change == "empty_members":
        data["parameters"][0]["members"] = []
    elif change == "unknown_field":
        data["drawings"][0]["editible"] = True
    elif change == "missing_editable":
        data["drawings"][0].pop("editable")
    elif change == "boolean_expected":
        data["parameters"][0]["expected"] = True
    elif change == "negative_tolerance":
        data["parameters"][0]["tolerance"] = -1
    elif change == "zero_tolerance":
        data["parameters"][0]["tolerance"] = 0
    elif change == "nonfinite":
        data["parameters"][0]["expected"] = float("nan")
    elif change == "bad_hash":
        data["drawings"][0]["sha256"] = "?" * 64
    else:
        data["units"] = []
    with pytest.raises(RevisionError):
        run(tmp_path, data)


def test_duplicate_json_keys_do_not_silently_change_authorization(tmp_path):
    path = tmp_path / "set.json"
    data = manifest(tmp_path)
    path.write_text(json.dumps(data).replace('"editable": true', '"editable": false, "editable": true'), encoding="utf-8")
    with pytest.raises(RevisionError, match="duplicate"):
        api().check_drawing_set(path)


@pytest.mark.parametrize("selector", [{"handle": "FFFFFF"}, {"id": "duplicated"}])
def test_missing_and_ambiguous_members_are_blocked(tmp_path, selector):
    data = manifest(tmp_path, (20, 20))
    path = tmp_path / "part.dxf"
    doc = ezdxf.readfile(path)
    doc.appids.add("CAD_SKILL_ID")
    for y in (1, 2):
        doc.modelspace().add_line((0, y), (20, y)).set_xdata("CAD_SKILL_ID", [(1000, "duplicated")])
    doc.saveas(path)
    data["drawings"][1]["sha256"] = digest(path)
    data["parameters"][0]["members"][1]["selector"] = selector
    result = run(tmp_path, data)
    assert result["status"] == "blocked"
    assert result["parameters"][0]["members"][1]["actual"] is None


def test_measure_feature_reads_geometry_without_mutating_document():
    doc = ezdxf.new()
    msp = doc.modelspace()
    line = msp.add_line((1, 2, 3), (4, 6, 3))
    circle = msp.add_circle((10, 10), 7)
    arc = msp.add_arc((20, 10), 3, 10, 40)
    before = snapshot_modelspace(doc)
    for entity, measure, expected in [(line, "length", 5), (circle, "radius", 7),
                                      (circle, "diameter", 14), (arc, "radius", 3), (arc, "diameter", 6)]:
        assert api().measure_feature(doc, {"handle": entity.dxf.handle}, measure) == expected
    assert snapshot_modelspace(doc) == before


def test_dimensions_use_endpoint_geometry_and_not_cached_measurement_or_label():
    doc = ezdxf.new()
    msp = doc.modelspace()
    linear = msp.add_linear_dim(base=(0, 10), p1=(1, 2), p2=(4, 6), angle=90).dimension
    aligned = msp.add_aligned_dim(p1=(1, 2), p2=(4, 6), distance=5).dimension
    radius = msp.add_radius_dim(center=(0, 0), mpoint=(3, 4)).dimension
    diameter = msp.add_diameter_dim(center=(0, 0), mpoint=(3, 4)).dimension
    for entity, expected in [(linear, 4), (aligned, 5), (radius, 5), (diameter, 10)]:
        entity.dxf.text = "999"
        entity.dxf.actual_measurement = 999
        assert api().measure_feature(doc, {"handle": entity.dxf.handle}, "dimension") == pytest.approx(expected)


@pytest.mark.parametrize("kind", ["nonplanar", "extrusion", "thickness", "spline", "angular", "wrong_measure", "zero_line"])
def test_unsupported_measurements_raise_instead_of_guessing(kind):
    doc = ezdxf.new()
    msp = doc.modelspace()
    measure = "length"
    if kind == "spline":
        entity = msp.add_spline([(0, 0), (1, 4), (5, 0)])
    elif kind == "angular":
        entity = msp.add_angular_dim_cra(center=(0, 0), radius=5, start_angle=0, end_angle=90, distance=7).dimension
        measure = "dimension"
    else:
        entity = msp.add_line((0, 0), (3, 4, 1 if kind == "nonplanar" else 0))
        if kind == "extrusion":
            entity.dxf.extrusion = (0, 1, 0)
        elif kind == "thickness":
            entity.dxf.thickness = 1
        elif kind == "wrong_measure":
            measure = "radius"
        elif kind == "zero_line":
            entity.dxf.end = entity.dxf.start
    with pytest.raises(RevisionError):
        api().measure_feature(doc, {"handle": entity.dxf.handle}, measure)


def test_nonplanar_dimension_cannot_pass_as_projected_2d_value():
    doc = ezdxf.new()
    entity = doc.modelspace().add_linear_dim(base=(0, 5), p1=(0, 0), p2=(20, 0)).dimension
    entity.dxf.defpoint3 = (20, 0, 1)
    with pytest.raises(RevisionError, match="planar"):
        api().measure_feature(doc, {"handle": entity.dxf.handle}, "dimension")


def test_duplicate_member_aliases_cannot_supply_multiple_independent_checks(tmp_path):
    data = manifest(tmp_path, (20, 20))
    path = tmp_path / "assembly.dxf"
    doc = ezdxf.readfile(path)
    doc.appids.add("CAD_SKILL_ID")
    entity = doc.entitydb[data["parameters"][0]["members"][0]["selector"]["handle"]]
    entity.set_xdata("CAD_SKILL_ID", [(1000, "thickness")])
    doc.saveas(path)
    data["drawings"][0]["sha256"] = digest(path)
    data["parameters"][0]["members"].append({"drawing": "assembly", "selector": {"id": "thickness"}, "measure": "dimension"})
    result = run(tmp_path, data)
    assert result["status"] == "blocked"
    assert any(b["code"] == "duplicate_member" for b in result["blockers"])


def test_file_change_during_measurement_invalidates_current_success(tmp_path, monkeypatch):
    data = manifest(tmp_path, (20, 20))
    original_measure = api().measure_feature

    def change_after_measure(doc, selector, measure):
        actual = original_measure(doc, selector, measure)
        source = tmp_path / "part.dxf"
        source.write_bytes(source.read_bytes() + b"\n")
        return actual

    monkeypatch.setattr(api(), "measure_feature", change_after_measure)
    result = run(tmp_path, data)
    assert result["status"] == "blocked"
    member = result["parameters"][0]["members"][1]
    assert member["actual"] == 20  # Preserve the inspected snapshot's evidence.
    assert member["status"] == "blocked"
    assert any(b["code"] == "source_changed" for b in result["blockers"])


@pytest.mark.parametrize("fmt", ["bin", "asc"])
def test_snapshot_reader_preserves_binary_and_legacy_encoded_selectors(tmp_path, fmt):
    doc = ezdxf.new("R2000")
    doc.units = 4
    doc.encoding = "gbk"
    doc.appids.add("CAD_SKILL_ID")
    entity = doc.modelspace().add_line((0, 0), (12, 5))
    entity.set_xdata("CAD_SKILL_ID", [(1000, "零件.斜边")])
    path = tmp_path / "legacy.dxf"
    doc.saveas(path, fmt=fmt)
    data = {"schema": "cad-drawing-set/v1", "units": "mm", "drawings": [
        {"id": "legacy", "path": path.name, "sha256": digest(path), "editable": False}],
        "parameters": [{"id": "length", "expected": 13, "tolerance": 0.001, "members": [
            {"drawing": "legacy", "selector": {"id": "零件.斜边"}, "measure": "length"}]}]}
    result = run(tmp_path, data)
    assert result["status"] == "checked"
    assert result["parameters"][0]["members"][0]["actual"] == 13


def test_unitless_drawing_does_not_inherit_manifest_units(tmp_path):
    data = manifest(tmp_path, (20, 20))
    path = tmp_path / "part.dxf"
    doc = ezdxf.readfile(path)
    doc.units = 0
    doc.saveas(path)
    data["drawings"][1]["sha256"] = digest(path)
    result = run(tmp_path, data)
    assert result["status"] == "blocked"
    assert result["drawings"][1]["actual_units"] == 0


def test_unused_missing_drawing_still_blocks_whole_set(tmp_path):
    data = manifest(tmp_path, (20, 20))
    data["drawings"].append({"id": "unverified", "path": "missing.dxf", "sha256": "0" * 64, "editable": False})
    result = run(tmp_path, data)
    assert result["parameters"][0]["status"] == "checked"
    assert result["status"] == "blocked"


def test_missing_circle_radius_is_not_replaced_by_a_dxf_default():
    doc = ezdxf.new()
    entity = doc.modelspace().add_circle((0, 0), 5)
    entity.dxf.discard("radius")
    with pytest.raises(RevisionError, match="missing"):
        api().measure_feature(doc, {"handle": entity.dxf.handle}, "radius")


def test_drawing_that_needs_automatic_repair_is_blocked_without_source_write(tmp_path):
    data = manifest(tmp_path, (20, 20))
    path = tmp_path / 'part.dxf'
    doc = ezdxf.readfile(path)
    doc.modelspace().add_line((0, 0), (1, 1), dxfattribs={'linetype': 'MISSING_TYPE'})
    doc.saveas(path)
    original = path.read_bytes()
    data['drawings'][1]['sha256'] = digest(path)
    result = run(tmp_path, data)
    assert result['status'] == 'blocked'
    assert any(b['code'] == 'invalid_drawing' for b in result['blockers'])
    assert path.read_bytes() == original


def test_shared_snapshot_reader_rejects_automatic_repairs(tmp_path):
    doc = ezdxf.new('R2018')
    doc.modelspace().add_line((0, 0), (1, 1), dxfattribs={'linetype': 'MISSING_TYPE'})
    path = tmp_path / 'invalid.dxf'
    doc.saveas(path)
    with pytest.raises(RevisionError, match='audit'):
        api().read_dxf_snapshot(path.read_bytes())
