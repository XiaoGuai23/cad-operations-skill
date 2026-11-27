#!/usr/bin/env python3
"""Zero-config Windows/macOS/Linux one-command reproduction entrypoint for the Civil Bridge Suite.

Usage examples (works directly on Windows CMD / PowerShell without setting PYTHONPATH or PYTHONUTF8):
  python scripts/run_bridge_suite.py --out output/wenchang_18_sheets
  python scripts/run_bridge_suite.py --out output/wenchang_18_sheets --fem-results fem_drbridge.json
  python scripts/run_bridge_suite.py --out output/wenchang_18_sheets --params mod.json --baseline output/baseline_suite/bridge_calculation_report.json
  python scripts/run_bridge_suite.py --lookup-bar QT3
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


def _bootstrap_runtime_and_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", None) or "").lower()
        if hasattr(stream, "reconfigure") and not enc.startswith("utf"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "runtime" / "cad-2d-skill" / "src"
    if src_dir.is_dir() and str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def main(argv: Sequence[str] | None = None) -> int:
    _bootstrap_runtime_and_utf8()

    from cad_skill_core.civil import (
        generate_bridge_calculation_book_markdown,
        generate_full_bridge_drawing_set,
        lookup_bar_or_tendon,
        run_full_bridge_calculation,
    )

    parser = argparse.ArgumentParser(
        description=(
            "One-command reproduction of 22-sheet (or 18-sheet) A3 bridge drawings, Word/PPT figures, "
            "formal college Word thesis (.docx), FEM defense verification table, and semantic change tracker."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("output/wenchang_22_sheets"),
        dest="output_dir",
        help="Output directory for 22-sheet A3 DXFs/PDFs, figures, calculation book MD, and formal Word thesis DOCX.",
    )
    parser.add_argument(
        "--preset",
        default="wenchang",
        help="Bridge preset name (default: 'wenchang'; pass '' for raw custom params).",
    )
    parser.add_argument(
        "--sheet-scheme",
        choices=("wenchang_22", "ql_18"),
        default=None,
        dest="sheet_scheme",
        help="Sheet manifest scheme ('wenchang_22' 22-sheet set [default for wenchang] or 'ql_18' legacy 18-sheet set).",
    )
    parser.add_argument("--params", type=Path, default=None, dest="params_path", help="Optional JSON parameter overrides.")
    parser.add_argument("--survey", type=Path, default=None, dest="survey_path", help="Optional site hydrology/borehole survey JSON.")
    parser.add_argument(
        "--drbridge-new-txt",
        "--drbridge-tendon-model",
        type=Path,
        default=None,
        dest="drbridge_new_txt_path",
        help="Optional Dr.Bridge new.txt or tendon/section model JSON for 3D tendon centroid/duct clearance audit.",
    )
    parser.add_argument(
        "--fem-results",
        type=Path,
        default=None,
        dest="fem_results_path",
        help="Optional independent FEM software results (桥梁博士上部分册 / Midas Civil JSON, CSV, TSV, or Markdown table).",
    )
    parser.add_argument(
        "--substructure-fem",
        type=Path,
        default=None,
        dest="sub_fem_results_path",
        help="Optional Dr.Bridge substructure (桥梁博士下部分册：盖梁、墩柱、系梁) FEM results file.",
    )
    parser.add_argument(
        "--fem-solver",
        choices=("none", "builtin_frame_fem"),
        default="none",
        dest="fem_solver",
        help="Optional built-in independent 2D Timoshenko beam & pier-frame FEM solver ('none' keeps pending_external_import unless --fem-results is given).",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        dest="baseline_path",
        help="Optional previous bridge_calculation_report.json to compare and report affected chapters & sheets.",
    )
    parser.add_argument(
        "--student-info",
        type=Path,
        default=None,
        dest="student_info_path",
        help="Optional JSON metadata for formal college thesis cover (school_name, student_name, student_id, advisor_name).",
    )
    parser.add_argument("--lookup-bar", default=None, dest="lookup_bar", help="Query full provenance for a single tendon/rebar ID (e.g. N1, GJ1, DZ1, QT3).")
    parser.add_argument("--with-previews", action="store_true", dest="with_previews", help="Render 1:1 A3 B&W PDFs, merged A3 print album, and high-DPI PNGs (enabled by default unless --no-previews is passed).")
    parser.add_argument("--no-previews", action="store_true", dest="no_previews", help="Skip rendering A3 PDFs and PNG previews (faster DXF-only mode).")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit machine-readable JSON output.")

    args = parser.parse_args(list(argv) if argv is not None else None)

    cfg: dict = {}
    if args.preset:
        cfg["preset"] = args.preset
    if args.sheet_scheme:
        cfg["sheet_scheme"] = args.sheet_scheme
    if args.params_path is not None:
        cfg.update(json.loads(args.params_path.read_text(encoding="utf-8-sig")))
    if args.survey_path is not None:
        cfg["survey_data"] = json.loads(args.survey_path.read_text(encoding="utf-8-sig"))
    if args.drbridge_new_txt_path is not None:
        cfg["drbridge_new_txt"] = str(args.drbridge_new_txt_path.expanduser().resolve())
    if args.fem_results_path is not None:
        cfg["independent_fem_results"] = str(args.fem_results_path.expanduser().resolve())
    if args.sub_fem_results_path is not None:
        cfg["substructure_fem_results"] = str(args.sub_fem_results_path.expanduser().resolve())
    if args.fem_solver != "none":
        cfg["fem_solver"] = args.fem_solver
    student_meta = (
        json.loads(args.student_info_path.read_text(encoding="utf-8-sig"))
        if args.student_info_path is not None
        else None
    )

    if args.lookup_bar:
        calc = run_full_bridge_calculation(cfg)
        traced = lookup_bar_or_tendon(calc, args.lookup_bar)
        print(json.dumps(traced, ensure_ascii=False, indent=2))
        return 0

    render_previews = False if args.no_previews else True
    suite = generate_full_bridge_drawing_set(
        cfg,
        args.output_dir,
        render_png=render_previews,
        baseline_report=args.baseline_path,
        student_metadata=student_meta,
    )
    calc = suite.pop("calculation_report")
    book_path = Path(suite["calculation_book_path"])
    if not book_path.is_file():
        generate_bridge_calculation_book_markdown(calc, output_path=book_path)

    if args.as_json:
        print(json.dumps(suite, ensure_ascii=False, indent=2))
    else:
        ps = suite["project_summary"]
        cc = suite["independent_cross_check"]
        fem_t = suite["defense_verification_table"]
        chg = suite["change_impact"]
        refs_a = suite["references_audit"]
        figs = suite.get("calculation_figures") or {}
        thesis = suite.get("graduation_thesis") or {}
        print(f"项目名称: {ps['project_name']}")
        print(f"1. 当前参数结构计算状态 (calculation_status): {suite['calculation_status']}")
        print(f"2. 毕业设计资料齐套状态 (graduation_readiness_status): {suite['graduation_readiness_status']}")
        print(f"   状态说明: {suite['status_summary_zh']}")
        print(
            f"3. 独立桩长与配束核定: 桥墩桩长 = {ps['pier_pile_length_m']:g} m | "
            f"桥台桩长 = {ps['abutment_pile_length_m']:g} m | 主梁钢束 = {ps['n_tendons']}束×{ps['strands_per_tendon']}股"
        )
        print(
            f"4. 手算与独立电算复核: 程序解析对算 {cc['status']} (最大偏差 {cc['max_rel_diff_pct']}%) | "
            f"外部软件对照表状态 = {fem_t['external_fem_status']}"
        )
        print(
            f"5. 参考文献双口径核验: 共 {refs_a['total_count']} 篇 "
            f"(中文 {refs_a['chinese_count']} 篇 + 外文 {refs_a['foreign_count']} 篇，任务书与开题报告均合规)"
        )
        print(f"6. 设计变更影响追踪: {chg['summary_zh']}")
        if chg.get("has_changes"):
            print(f"   - 涉及计算书章节: {'；'.join(chg['affected_calc_book_chapters'])}")
            print(f"   - 涉及施工图图号: {', '.join(chg['affected_drawings'])}")
        print(
            f"7. 交付成果汇总: {len(suite['sheets'])} 张 A3 施工图 (最低评分 {min(s['rubric_score'] for s in suite['sheets']):.1f}/100) + "
            f"{figs.get('figure_count', 0)} 幅 Word/PPT 高清计算图 + 正式学院模板 Word 说明书"
        )
        print(f"   - 图册清单: {suite['manifest_path']}")
        print(f"   - 导师复核计算书(MD): {suite['calculation_book_path']}")
        print(f"   - 学院模板正式说明书(DOCX): {thesis.get('docx_path')}")
        if suite.get("print_album_pdf_path"):
            print(f"   - {len(suite['sheets'])}张A3施工图合订打印图册(PDF): {suite['print_album_pdf_path']}")
        if suite.get("drbridge_modeling_guide_path"):
            print(f"   - 桥梁博士/Midas独立电算建模卡(MD): {suite['drbridge_modeling_guide_path']}")
        print(f"   - 计算图目录(300DPI PNG/SVG): {figs.get('output_dir')}")
        print(f"   - 设计变更影响报告(JSON): {suite['change_impact_report_path']}")
    return 0 if suite["calculation_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
