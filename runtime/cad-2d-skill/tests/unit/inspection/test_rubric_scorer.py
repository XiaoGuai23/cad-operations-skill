from __future__ import annotations

import ezdxf

from cad_skill_core.documents.spec_compiler import compile_spec_from_files
from cad_skill_core.inspection.rubric_scorer import score_dxf_drawing
from cad_skill_core.pipeline import PipelineOptions, run_task


def test_rubric_scorer_awards_s_grade_to_clean_three_view_drawing(tmp_path):
    task_dir = tmp_path / "clean_flange"
    (task_dir / "input").mkdir(parents=True)
    (task_dir / "work").mkdir(parents=True)
    brief = task_dir / "input" / "brief.md"
    brief.write_text(
        "# 连接法兰盘\n图号：FLG-S01\n材料：45钢\n外径：140 mm\n内孔：48 mm\n厚度：20 mm\n节圆直径：102 mm\n安装孔：4×Φ14\n",
        encoding="utf-8",
    )
    compile_spec_from_files([brief], output_path=task_dir / "work" / "drawing-spec.json")
    res = run_task(task_dir, PipelineOptions(run_autocad=False))
    assert res.status in {"passed", "partial", "complete"}

    report = score_dxf_drawing(
        task_dir / "output" / "drawing.dxf",
        audit_json_path=res.audit_path,
    )
    assert report.total_score >= 95.0
    assert report.grade == "S"
    assert len(report.dimensions) == 5
    assert sum(d.max_score for d in report.dimensions) == 100.0


def test_rubric_scorer_penalizes_layer_zero_and_degenerate_geometry(tmp_path):
    bad_dxf = tmp_path / "sloppy.dxf"
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    # Everything dumped on layer '0', plus a zero-length degenerate line, no dimensions, no title block
    msp.add_line((0, 0), (100, 0))
    msp.add_line((10, 10), (10, 10))  # zero-length degenerate line
    doc.saveas(bad_dxf)

    report = score_dxf_drawing(bad_dxf)
    assert report.total_score < 60.0
    assert report.grade == "D"
    assert len(report.recommendations) >= 2


def test_rubric_scorer_blends_cad1k_custom_criteria(tmp_path):
    dxf_path = tmp_path / "custom_eval.dxf"
    doc = ezdxf.new("R2018")
    doc.layers.add("OUTLINE", lineweight=50)
    doc.layers.add("CENTER", lineweight=18)
    doc.layers.add("DIM", lineweight=25)
    doc.layers.add("TEXT", lineweight=25)
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (120, 0), (120, 80), (0, 80)], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_line((0, 40), (120, 40), dxfattribs={"layer": "CENTER"})
    msp.add_text("主视图", dxfattribs={"layer": "TEXT", "height": 3.5, "insert": (45, 86)})
    msp.add_text("图号 TST-01", dxfattribs={"layer": "TEXT", "height": 3.5, "insert": (80, 10)})
    dim = msp.add_linear_dim(base=(60, -12), p1=(0, 0), p2=(120, 0), dxfattribs={"layer": "DIM"})
    dim.render()
    doc.saveas(dxf_path)

    custom_rubric = {
        "criteria": [
            {"id": "C1", "description": "Required layers", "weight": 40, "required_layers": ["OUTLINE", "CENTER", "DIM"]},
            {"id": "C2", "description": "Span 120x80", "weight": 30, "type": "bbox_span", "expected_width": 120.0, "tolerance": 15.0},
            {"id": "C3", "description": "Contains 主视图", "weight": 30, "required_texts": ["主视图"]},
        ]
    }
    report = score_dxf_drawing(dxf_path, custom_rubric=custom_rubric)
    assert len(report.custom_criteria) == 3
    assert all(c.passed for c in report.custom_criteria)
    assert report.total_score >= 75.0
