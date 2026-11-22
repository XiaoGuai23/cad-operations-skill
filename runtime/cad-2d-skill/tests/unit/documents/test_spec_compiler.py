from __future__ import annotations

import json
from pathlib import Path

from docx import Document
import ezdxf
import pytest

from cad_skill_core.cli import main as cli_main
from cad_skill_core.documents import (
    compile_spec_from_files,
    compile_spec_from_fragments,
    extract_sources,
)
from cad_skill_core.pipeline import PipelineOptions, run_task


def test_extract_sources_supports_csv_and_deduplicates_merged_docx_cells(tmp_path):
    csv_path = tmp_path / "params.csv"
    csv_path.write_text("参数,数值\n外径,200 mm\n内径,80 mm\n厚度,24 mm\n", encoding="utf-8")

    docx_path = tmp_path / "brief.docx"
    doc = Document()
    table = doc.add_table(rows=2, cols=3)
    merged = table.cell(0, 0).merge(table.cell(0, 2))
    merged.text = "法兰技术参数表"
    table.cell(1, 0).text = "材料"
    table.cell(1, 1).merge(table.cell(1, 2)).text = "Q235B"
    doc.save(docx_path)

    fragments = extract_sources([csv_path, docx_path])
    texts = [f.text for f in fragments]
    assert "外径 | 200 mm" in texts
    assert "内径 | 80 mm" in texts
    assert "法兰技术参数表" in texts
    assert "法兰技术参数表 | 法兰技术参数表" not in " || ".join(texts)
    assert "材料 | Q235B" in texts


def test_spec_compiler_synthesizes_complete_flange_and_passes_full_pipeline(tmp_path):
    brief = tmp_path / "project.md"
    brief.write_text(
        "# 高压连接法兰\n"
        "图号：FLG-2026-01\n"
        "图幅：A3\n"
        "材料：45钢\n"
        "外径：160±0.1 mm\n"
        "内孔：60 mm\n"
        "节圆直径：115 mm\n"
        "安装孔：4×Φ14\n"
        "厚度：22 mm\n"
        "技术要求：1. 未注倒角 C1.5，去毛刺。\n",
        encoding="utf-8",
    )

    task_dir = tmp_path / "flange_task"
    (task_dir / "input").mkdir(parents=True)
    (task_dir / "work").mkdir(parents=True)
    (task_dir / "input" / "project.md").write_text(brief.read_text(encoding="utf-8"), encoding="utf-8")

    spec = compile_spec_from_files(
        [task_dir / "input" / "project.md"],
        output_path=task_dir / "work" / "drawing-spec.json",
    )

    assert spec.project.name == "高压连接法兰"
    assert spec.project.drawing_number == "FLG-2026-01"
    assert spec.sheet.title_block_language == "zh"
    assert spec.requirements.material is not None
    assert spec.requirements.material.text == "45钢"
    assert spec.validation.conflicts == []
    assert spec.validation.missing == []
    assert set(spec.requirements.views) == {"front", "top", "left"}
    assert any(d.tolerance_upper == pytest.approx(0.1) for d in spec.dimensions)

    result = run_task(task_dir, PipelineOptions(run_autocad=False))
    assert result.status == "partial"
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["summary"]["errors"] == 0

    reopened = ezdxf.readfile(task_dir / "output" / "drawing.dxf")
    all_texts = [e.dxf.text for e in reopened.modelspace().query("TEXT")]
    assert "图名" in all_texts
    assert "主视图" in all_texts
    assert "俯视图" in all_texts
    assert "左视图" in all_texts


def test_spec_compiler_detects_parameter_and_material_conflicts(tmp_path):
    md_file = tmp_path / "brief.md"
    md_file.write_text("# 法兰\n材料：Q235B\n外径：200 mm\n内径：80 mm\n", encoding="utf-8")
    csv_file = tmp_path / "override.csv"
    csv_file.write_text("参数,数值\n外径,240 mm\n材料,304不锈钢\n", encoding="utf-8")

    spec = compile_spec_from_files([md_file, csv_file])

    assert len(spec.validation.conflicts) >= 2
    assert any("outer_diameter" in c for c in spec.validation.conflicts)
    assert any("material conflict" in c for c in spec.validation.conflicts)


def test_spec_compiler_reports_missing_required_parameters_and_cli_exit_code(tmp_path):
    incomplete = tmp_path / "incomplete_bracket.md"
    incomplete.write_text("# 腰形槽安装支架\n宽度：120 mm\n槽长：40 mm\n", encoding="utf-8")
    out_spec = tmp_path / "spec.json"

    exit_code = cli_main(["compile-spec", str(incomplete), "--out", str(out_spec), "--json"])

    assert exit_code == 1
    saved = json.loads(out_spec.read_text(encoding="utf-8"))
    assert any("height" in item for item in saved["validation"]["missing"])
    assert any("slot_width" in item for item in saved["validation"]["missing"])


def test_spec_compiler_compiles_stepped_shaft_and_bom_assembly(tmp_path):
    shaft_doc = tmp_path / "shaft.md"
    shaft_doc.write_text(
        "# 减速器阶梯轴\n材料：40Cr\n轴段1: Φ25×40\n轴段2: Φ36×60\n轴段3: Φ25×35\n",
        encoding="utf-8",
    )
    shaft_spec = compile_spec_from_fragments(extract_sources([shaft_doc]))
    assert shaft_spec.validation.missing == []
    assert shaft_spec.validation.conflicts == []
    assert shaft_spec.geometry.parameters["total_length_mm"] == pytest.approx(135.0)
    assert shaft_spec.geometry.parameters["max_diameter_mm"] == pytest.approx(36.0)

    bom_csv = tmp_path / "bom.csv"
    bom_csv.write_text("序号,零件名称,数量\n1,法兰盘,2\n2,密封垫,1\n3,螺栓M12,4\n", encoding="utf-8")
    asm_spec = compile_spec_from_files([bom_csv])
    assert asm_spec.project.drawing_type == "assembly"
    assert [(p.item, p.quantity) for p in asm_spec.parts_list] == [
        ("法兰盘", 2),
        ("密封垫", 1),
        ("螺栓M12", 4),
    ]


@pytest.mark.parametrize(
    ("doc_text", "family", "expected_missing"),
    [
        ("# 法兰零件\n内径：50 mm\n", "flange", "outer_diameter"),
        ("# 传动阶梯轴\n材料：45钢\n", "stepped-shaft", "shaft steps"),
        ("# 圆角垫板\n宽度：80 mm\n高度：50 mm\n", "rounded-rect", "corner_radius"),
    ],
)
def test_spec_compiler_flags_missing_parameters_per_family(tmp_path, doc_text, family, expected_missing):
    src = tmp_path / "doc.md"
    src.write_text(doc_text, encoding="utf-8")
    spec = compile_spec_from_files([src], part_family=family)
    assert any(expected_missing in m for m in spec.validation.missing)


def test_spec_compiler_compiles_rounded_rect_and_flags_infeasible_flange(tmp_path):
    rr = tmp_path / "plate.md"
    rr.write_text(
        "# 圆角盖板\n宽度：90 ( +0.05 / -0.02 )\n高度：60 mm\n圆角半径：R8\n",
        encoding="utf-8",
    )
    spec = compile_spec_from_files([rr])
    assert spec.validation.missing == []
    assert spec.validation.conflicts == []
    assert len(spec.geometry.entities) == 1
    assert spec.dimensions[0].tolerance_upper == pytest.approx(0.05)
    assert spec.dimensions[0].tolerance_lower == pytest.approx(-0.02)

    bad_flange = tmp_path / "bad_flange.md"
    bad_flange.write_text("# 冲突法兰\n外径：80 mm\n内径：100 mm\n", encoding="utf-8")
    bad_spec = compile_spec_from_files([bad_flange])
    assert any("constraint violation" in c for c in bad_spec.validation.conflicts)

