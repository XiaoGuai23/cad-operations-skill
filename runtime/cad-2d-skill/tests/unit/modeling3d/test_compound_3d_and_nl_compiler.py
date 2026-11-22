from __future__ import annotations

import pytest

from cad_skill_core.documents.models import SourceFragment
from cad_skill_core.documents.spec_compiler import compile_spec_from_fragments
from cad_skill_core.modeling3d import (
    Model3DSpec,
    build_solid_mesh,
    compile_model3d_from_text,
    run_3d_task,
)


def test_compound_3d_base_with_hollow_boss_and_four_counterbores_is_watertight_and_projects_2d(tmp_path):
    spec = Model3DSpec.model_validate(
        {
            "schema_version": "1.0",
            "project": {"name": "轴承凸台沉孔安装座", "drawing_number": "CMP-3D-01"},
            "material": {"text": "45钢", "source_id": "src"},
            "technical_notes": [{"text": "未注倒角 C1.5。", "source_id": "src"}],
            "body": {
                "kind": "compound_3d",
                "base_shape": "box",
                "width": 150.0,
                "depth": 96.0,
                "base_height": 18.0,
                "corner_radius": 8.0,
                "bosses": [
                    {
                        "kind": "cylinder_boss",
                        "x": 0.0,
                        "y": 0.0,
                        "diameter": 54.0,
                        "height": 24.0,
                        "bore_diameter": 26.0,
                    }
                ],
                "through_holes": [[-42.0, 0.0, 8.0], [42.0, 0.0, 8.0]],
                "counterbores": [
                    {"x": -56.0, "y": -30.0, "through_diameter": 9.0, "cbore_diameter": 15.0, "cbore_depth": 6.0},
                    {"x": 56.0, "y": -30.0, "through_diameter": 9.0, "cbore_diameter": 15.0, "cbore_depth": 6.0},
                    {"x": 56.0, "y": 30.0, "through_diameter": 9.0, "cbore_diameter": 15.0, "cbore_depth": 6.0},
                    {"x": -56.0, "y": 30.0, "through_diameter": 9.0, "cbore_diameter": 15.0, "cbore_depth": 6.0},
                ],
                "source_id": "src",
            },
            "provenance": [{"id": "src", "file": "spec", "locator": "L1", "confidence": 1.0}],
        }
    )

    mesh = build_solid_mesh(spec, circle_segments=48)
    assert mesh.signed_volume_mm3() > 0

    res = run_3d_task(spec, tmp_path / "compound_out", with_2d_sheet=True, circle_segments=48)
    assert res["status"] == "passed", res
    assert res["audit_3d"]["watertight"] is True
    assert res["audit_3d"]["volume_rel_error"] < 0.025
    assert res["sheet_2d"]["audit_summary"]["errors"] == 0


def test_compound_3d_rejects_overlapping_top_features():
    with pytest.raises(ValueError, match="overlap"):
        Model3DSpec.model_validate(
            {
                "schema_version": "1.0",
                "project": {"name": "干涉测试", "drawing_number": "BAD-01"},
                "body": {
                    "kind": "compound_3d",
                    "base_shape": "box",
                    "width": 120.0,
                    "depth": 80.0,
                    "base_height": 16.0,
                    "bosses": [{"kind": "cylinder_boss", "x": 0.0, "y": 0.0, "diameter": 40.0, "height": 15.0}],
                    "through_holes": [[15.0, 0.0, 16.0]],
                    "source_id": "s1",
                },
                "provenance": [{"id": "s1", "file": "f", "locator": "1", "confidence": 1.0}],
            }
        )


def test_conversational_chinese_2d_and_3d_compilers_parse_spoken_sentences():
    # 1. Conversational 2D flange sentence (no colons, inline material, reverse BCD, conversational holes)
    frag = SourceFragment(
        id="conv2d0011223344",
        file="chat.md",
        media_type="text/markdown",
        locator="line 1",
        text="帮我画个304不锈钢连接法兰，外径160内孔50厚22，在120的节圆上均布6个直径14的螺栓孔",
        confidence=1.0,
    )
    spec_2d = compile_spec_from_fragments([frag])
    assert spec_2d.validation.missing == []
    assert spec_2d.validation.conflicts == []
    assert spec_2d.requirements.material is not None
    assert "304不锈钢" in spec_2d.requirements.material.text
    assert spec_2d.geometry.parameters["outer_diameter"] == 160.0
    assert spec_2d.geometry.parameters["hole_count"] == 6

    # 2. Conversational 3D hubbed flange
    spec_3d_flange = compile_model3d_from_text(
        "做一个45钢带颈法兰，外径150内孔48底盘厚18颈径76颈高24，在112的分布圆上均布6个12的孔"
    )
    assert spec_3d_flange.validation.missing == []
    assert spec_3d_flange.body.kind == "flange_3d"
    assert spec_3d_flange.body.hub_diameter == 76.0
    assert spec_3d_flange.body.hub_height == 24.0
    assert spec_3d_flange.body.hole_count == 6

    # 3. Conversational 3D stepped shaft
    spec_3d_shaft = compile_model3d_from_text(
        "设计一根40Cr三段阶梯轴，第一段直径26长32，第二段40长50，第三段22长28，中心通孔10"
    )
    assert spec_3d_shaft.validation.missing == []
    assert spec_3d_shaft.body.kind == "stepped_shaft_3d"
    assert len(spec_3d_shaft.body.steps) == 3
    assert spec_3d_shaft.body.bore_diameter == 10.0
