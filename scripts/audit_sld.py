#!/usr/bin/env python3
"""Lightweight DXF audit helper for generated electrical single-line diagrams.

Checks that a DXF is readable, summarizes layers/entity counts, optionally checks
text-label occurrences, and reports axis-aligned line crossings as a visual-review
heuristic. It is intentionally conservative: crossings can be intentional bus taps,
so inspect reported points before treating them as defects.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import ezdxf


def text_value(entity: Any) -> str:
    if entity.dxftype() in {"TEXT", "ATTRIB", "ATTDEF"}:
        return str(entity.dxf.text)
    if entity.dxftype() == "MTEXT":
        return str(entity.text)
    return ""


def line_segment(entity: Any) -> tuple[float, float, float, float] | None:
    if entity.dxftype() != "LINE":
        return None
    start = entity.dxf.start
    end = entity.dxf.end
    return float(start.x), float(start.y), float(end.x), float(end.y)


def find_axis_crossings(segments: list[tuple[float, float, float, float]]) -> list[tuple[float, float]]:
    crossings: list[tuple[float, float]] = []
    for i, (x1, y1, x2, y2) in enumerate(segments):
        h1 = abs(y1 - y2) < 1e-6
        v1 = abs(x1 - x2) < 1e-6
        if not (h1 or v1):
            continue
        for x3, y3, x4, y4 in segments[i + 1 :]:
            h2 = abs(y3 - y4) < 1e-6
            v2 = abs(x3 - x4) < 1e-6
            if not (h2 or v2) or h1 == h2:
                continue
            if h1:
                hx1, hx2 = sorted([x1, x2])
                vy1, vy2 = sorted([y3, y4])
                vx = x3
                hy = y1
            else:
                hx1, hx2 = sorted([x3, x4])
                vy1, vy2 = sorted([y1, y2])
                vx = x1
                hy = y3
            if hx1 < vx < hx2 and vy1 < hy < vy2:
                crossings.append((round(vx, 3), round(hy, 3)))
    return crossings


def parse_expectation(raw: str) -> tuple[str, int]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError("expectation must be PATTERN=COUNT")
    pattern, count = raw.rsplit("=", 1)
    pattern = pattern.strip()
    if not pattern:
        raise argparse.ArgumentTypeError("empty text pattern")
    try:
        expected = int(count)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("COUNT must be an integer") from exc
    return pattern, expected


def audit(path: Path, expectations: list[tuple[str, int]], crossing_limit: int) -> dict[str, Any]:
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    entities = list(msp)
    type_counts = Counter(e.dxftype() for e in entities)
    layer_counts = Counter(str(getattr(e.dxf, "layer", "0")) for e in entities)
    texts = [text_value(e) for e in entities if text_value(e)]
    segments = [seg for e in entities if (seg := line_segment(e)) is not None]
    crossings = find_axis_crossings(segments)

    text_checks = {
        pattern: {
            "expected": expected,
            "actual": sum(pattern in text for text in texts),
            "ok": sum(pattern in text for text in texts) == expected,
        }
        for pattern, expected in expectations
    }

    return {
        "path": str(path),
        "entity_count": len(entities),
        "type_counts": dict(sorted(type_counts.items())),
        "layer_counts": dict(sorted(layer_counts.items())),
        "text_count": len(texts),
        "text_checks": text_checks,
        "axis_crossings_count": len(crossings),
        "axis_crossings_sample": crossings[:crossing_limit],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a generated SLD DXF file")
    parser.add_argument("dxf", type=Path, help="DXF file to audit")
    parser.add_argument(
        "--expect-text",
        action="append",
        default=[],
        type=parse_expectation,
        metavar="PATTERN=COUNT",
        help="Require a text pattern to occur COUNT times; can be repeated",
    )
    parser.add_argument("--crossing-limit", type=int, default=40)
    args = parser.parse_args()

    report = audit(args.dxf, args.expect_text, args.crossing_limit)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    failed = [k for k, v in report["text_checks"].items() if not v["ok"]]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
