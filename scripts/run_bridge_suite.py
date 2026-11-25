#!/usr/bin/env python3
"""Zero-config Windows/macOS/Linux one-command reproduction entrypoint for the Civil Bridge Suite.

Usage examples (works directly on Windows CMD / PowerShell without setting PYTHONPATH or PYTHONUTF8):
  python scripts/run_bridge_suite.py --out output/wenchang_18_sheets
  python scripts/run_bridge_suite.py --out output/wenchang_18_sheets --survey survey.json --with-previews
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
        description="One-command reproduction of 18-sheet A3 bridge drawings, step-by-step calculation book, and rebar provenance.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("output/wenchang_18_sheets"),
        dest="output_dir",
        help="Output directory for QL-01..QL-18 DXFs, calculation report JSON, and calculation book Markdown.",
    )
    parser.add_argument(
        "--preset",
        default="wenchang",
        help="Bridge preset name (default: 'wenchang'; pass '' for raw custom params).",
    )
    parser.add_argument("--params", type=Path, default=None, dest="params_path", help="Optional JSON parameter overrides.")
    parser.add_argument("--survey", type=Path, default=None, dest="survey_path", help="Optional site hydrology/borehole survey JSON.")
    parser.add_argument("--lookup-bar", default=None, dest="lookup_bar", help="Query full provenance for a single tendon/rebar ID (e.g. N1, GJ1, DZ1, QT3).")
    parser.add_argument("--with-previews", action="store_true", dest="with_previews", help="Also render 1:1 A3 B&W PDF + high-DPI PNG for all 18 sheets.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit machine-readable JSON output.")

    args = parser.parse_args(list(argv) if argv is not None else None)

    cfg: dict = {}
    if args.preset:
        cfg["preset"] = args.preset
    if args.params_path is not None:
        cfg.update(json.loads(args.params_path.read_text(encoding="utf-8-sig")))
    if args.survey_path is not None:
        cfg["survey_data"] = json.loads(args.survey_path.read_text(encoding="utf-8-sig"))

    if args.lookup_bar:
        calc = run_full_bridge_calculation(cfg)
        traced = lookup_bar_or_tendon(calc, args.lookup_bar)
        print(json.dumps(traced, ensure_ascii=False, indent=2))
        return 0

    suite = generate_full_bridge_drawing_set(
        cfg,
        args.output_dir,
        render_png=bool(args.with_previews),
    )
    calc = suite.pop("calculation_report")
    # Ensure calculation book is written even if custom path needed
    book_path = Path(suite["calculation_book_path"])
    if not book_path.is_file():
        generate_bridge_calculation_book_markdown(calc, output_path=book_path)

    if args.as_json:
        print(json.dumps(suite, ensure_ascii=False, indent=2))
    else:
        ps = suite["project_summary"]
        cc = suite["independent_cross_check"]
        print(f"项目名称: {ps['project_name']}")
        print(f"1. 当前参数结构计算状态 (calculation_status): {suite['calculation_status']}")
        print(f"2. 毕业设计资料齐套状态 (graduation_readiness_status): {suite['graduation_readiness_status']}")
        print(f"   状态说明: {suite['status_summary_zh']}")
        print(
            f"3. 独立桩长核定: 桥墩桩长 = {ps['pier_pile_length_m']:g} m | "
            f"桥台桩长 = {ps['abutment_pile_length_m']:g} m (由桥台荷载与土层独立求得)"
        )
        print(f"4. 手算与电算对算校核: {cc['status']} (最大相对偏差 {cc['max_rel_diff_pct']}%)")
        print(
            f"5. 施工图册验收: {len(suite['sheets'])} 张 A3 图纸, "
            f"失败图纸数 = {len(suite['failed_drawings'])}, "
            f"最低评分 = {min(s['rubric_score'] for s in suite['sheets']):.1f}/100"
        )
        print(f"   - 图册清单: {suite['manifest_path']}")
        print(f"   - 结构计算JSON: {suite['calculation_report_path']}")
        print(f"   - 导师复核计算书(MD): {suite['calculation_book_path']}")
    return 0 if suite["calculation_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
