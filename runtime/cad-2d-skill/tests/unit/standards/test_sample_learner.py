from __future__ import annotations

import ezdxf
import pytest

from cad_skill_core.cli import main as cli_main
from cad_skill_core.layout.plan import plan_layout
from cad_skill_core.schema.io import load_spec
from cad_skill_core.standards import (
    learn_standard_from_dxf,
    load_standard,
    save_standard_yaml,
)
from tests.helpers import RUNTIME_ROOT


def test_learn_standard_from_dxf_extracts_layers_dimstyle_sheet_and_title_block(tmp_path):
    sample_path = tmp_path / "company_sample.dxf"
    doc = ezdxf.new("R2010", setup=True)
    doc.layers.add("轮廓线", color=3, lineweight=50, linetype="CONTINUOUS")
    doc.layers.add("中心线", color=1, lineweight=18, linetype="CENTER")
    doc.layers.add("尺寸", color=4, lineweight=18, linetype="CONTINUOUS")
    doc.layers.add("图框", color=7, lineweight=35, linetype="CONTINUOUS")
    doc.styles.add("HZ", font="simsun.ttc", dxfattribs={"height": 4.0, "width": 0.85})
    doc.dimstyles.add(
        "COMPANY_DIM",
        dxfattribs={"dimtxt": 4.0, "dimgap": 2.0, "dimasz": 3.0},
    )
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (594, 0), (594, 420), (0, 420)], close=True, dxfattribs={"layer": "图框"})
    msp.add_lwpolyline([(394, 10), (584, 10), (584, 66), (394, 66)], close=True, dxfattribs={"layer": "图框"})
    doc.saveas(sample_path)

    learned = learn_standard_from_dxf(sample_path, profile_name="company-gb-a2")

    assert learned.detected_sheet == "A2"
    assert learned.matched_layers["OUTLINE"] == "轮廓线"
    assert learned.standard.layers["OUTLINE"].color == 3
    assert learned.standard.layers["OUTLINE"].lineweight_mm == pytest.approx(0.50)
    assert learned.standard.layers["DIM"].color == 4
    assert learned.standard.text_styles["normal"].height_mm == pytest.approx(4.0)
    assert learned.standard.text_styles["normal"].width_factor == pytest.approx(0.85)
    assert learned.standard.dimension.text_height_mm == pytest.approx(4.0)
    assert learned.standard.dimension.arrow_size_mm == pytest.approx(3.0)

    yaml_path = tmp_path / "learned-profile.yaml"
    save_standard_yaml(learned.standard, yaml_path)
    reloaded = load_standard(yaml_path)
    assert reloaded == learned.standard

    spec = load_spec(RUNTIME_ROOT / "examples/simple-flange/work/drawing-spec.json")
    plan = plan_layout(spec, reloaded)
    assert plan.views


@pytest.mark.parametrize(
    ("sheet_name", "w", "h"),
    [
        ("A0", 1189.0, 841.0),
        ("A1", 841.0, 594.0),
        ("A3", 420.0, 297.0),
        ("A4", 210.0, 297.0),
    ],
)
def test_learn_standard_detects_iso_a_series_sheets_in_landscape_or_portrait(tmp_path, sheet_name, w, h):
    sample = tmp_path / f"{sheet_name}.dxf"
    doc = ezdxf.new("R2010")
    doc.layers.add("BORDER", color=7, lineweight=-3)
    doc.modelspace().add_lwpolyline([(0, 0), (w, 0), (w, h), (0, h)], close=True, dxfattribs={"layer": "BORDER"})
    doc.saveas(sample)

    learned = learn_standard_from_dxf(sample)
    assert learned.detected_sheet == sheet_name
    assert learned.standard.layers["FRAME"].lineweight_mm > 0


def test_cli_learn_standard_writes_valid_profile_and_rejects_missing_file(tmp_path):
    sample = tmp_path / "sample_a3.dxf"
    doc = ezdxf.new("R2010", setup=True)
    doc.layers.add("OUTLINE", color=5, lineweight=60)
    doc.modelspace().add_lwpolyline([(0, 0), (420, 0), (420, 297), (0, 297)], close=True, dxfattribs={"layer": "OUTLINE"})
    doc.saveas(sample)

    out_yaml = tmp_path / "out.yaml"
    assert cli_main(["learn-standard", str(sample), "--out", str(out_yaml), "--name", "custom-a3", "--json"]) == 0
    profile = load_standard(out_yaml)
    assert profile.name == "custom-a3"
    assert profile.layers["OUTLINE"].color == 5

    assert cli_main(["learn-standard", str(tmp_path / "missing.dxf"), "--out", str(out_yaml), "--json"]) == 2
