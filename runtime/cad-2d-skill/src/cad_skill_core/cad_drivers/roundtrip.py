"""Lossless DWG <-> DXF structural equivalence verifier and round-trip audit builder."""

from __future__ import annotations

from collections import Counter
import json
from math import isclose
from pathlib import Path
from typing import Any

from .models import DWGRoundTripIssue, DWGRoundTripReport

_SYSTEM_LAYERS = frozenset({"0", "defpoints"})
_BUILTIN_STYLES = frozenset({"standard", "annotative"})
_BUILTIN_DIMSTYLES = frozenset({"standard", "iso-25", "annotative", "ezdxf"})


def _summarize_dxf_structure(path: Path) -> tuple[dict[str, Any], list[DWGRoundTripIssue]]:
    import ezdxf
    from ezdxf import bbox

    doc = ezdxf.readfile(path)
    audit = doc.audit()
    issues: list[DWGRoundTripIssue] = []
    if audit.has_errors:
        issues.append(
            DWGRoundTripIssue(
                code="DXF_AUDIT_ERRORS",
                severity="error",
                category="audit",
                message=f"DXF audit reported {len(audit.errors)} error(s) in {path.name}",
                details={"error_count": len(audit.errors)},
            )
        )

    layers: dict[str, dict[str, Any]] = {}
    for layer in doc.layers:
        name = str(layer.dxf.name)
        if name.casefold() in _SYSTEM_LAYERS:
            continue
        layers[name.upper()] = {
            "name": name,
            "color": int(layer.color),
            "linetype": str(layer.dxf.linetype).upper(),
            "lineweight": int(layer.dxf.lineweight),
        }

    user_blocks = sorted(
        str(blk.name).upper()
        for blk in doc.blocks
        if not str(blk.name).startswith("*")
    )
    layouts = sorted(str(layout.name) for layout in doc.layouts)
    text_styles = sorted(
        str(st.dxf.name).upper()
        for st in doc.styles
        if str(st.dxf.name).casefold() not in _BUILTIN_STYLES
    )
    dim_styles = sorted(
        str(ds.dxf.name).upper()
        for ds in doc.dimstyles
        if str(ds.dxf.name).casefold() not in _BUILTIN_DIMSTYLES
        and not str(ds.dxf.name).upper().startswith("EZ_")
    )

    msp = doc.modelspace()
    entity_counts = dict(sorted(Counter(ent.dxftype() for ent in msp).items()))
    ext = bbox.extents(msp)
    extents_mm: list[float] | None = None
    if ext.has_data:
        extents_mm = [
            round(float(ext.extmin.x), 4),
            round(float(ext.extmin.y), 4),
            round(float(ext.extmax.x), 4),
            round(float(ext.extmax.y), 4),
        ]

    summary = {
        "dxf_version": doc.dxfversion,
        "units": int(doc.header.get("$INSUNITS", 0)),
        "layers": layers,
        "user_blocks": user_blocks,
        "layouts": layouts,
        "text_styles": text_styles,
        "dim_styles": dim_styles,
        "entity_counts": entity_counts,
        "total_entities": sum(entity_counts.values()),
        "extents_mm": extents_mm,
    }
    return summary, issues


def verify_dxf_structural_equivalence(
    before_dxf: Path,
    after_dxf: Path,
    *,
    bbox_tolerance_mm: float = 0.5,
    output_report_path: Path | None = None,
) -> DWGRoundTripReport:
    """Verify that layers, blocks, layouts, styles, entities, and extents survive DWG <-> DXF round-trip."""

    before_resolved = Path(before_dxf).expanduser().resolve(strict=True)
    after_resolved = Path(after_dxf).expanduser().resolve(strict=True)

    before_sum, before_issues = _summarize_dxf_structure(before_resolved)
    after_sum, after_issues = _summarize_dxf_structure(after_resolved)
    issues: list[DWGRoundTripIssue] = [*before_issues, *after_issues]

    # 1. Layer table check
    b_layers: dict[str, dict[str, Any]] = before_sum["layers"]
    a_layers: dict[str, dict[str, Any]] = after_sum["layers"]
    missing_layers = sorted(set(b_layers) - set(a_layers))
    if missing_layers:
        issues.append(
            DWGRoundTripIssue(
                code="ROUNDTRIP_MISSING_LAYERS",
                severity="error",
                category="layers",
                message=f"Round-tripped DXF lost {len(missing_layers)} user layer(s): {missing_layers}",
                details={"missing_layers": missing_layers},
            )
        )
    for lyr_key in sorted(set(b_layers) & set(a_layers)):
        b_l, a_l = b_layers[lyr_key], a_layers[lyr_key]
        if b_l["color"] != a_l["color"] or b_l["linetype"] != a_l["linetype"]:
            issues.append(
                DWGRoundTripIssue(
                    code="ROUNDTRIP_LAYER_STYLE_DRIFT",
                    severity="warning",
                    category="layers",
                    message=f"Layer {b_l['name']} color/linetype drifted: before={b_l} vs after={a_l}",
                    details={"layer": b_l["name"], "before": b_l, "after": a_l},
                )
            )

    # 2. User block definitions
    missing_blocks = sorted(set(before_sum["user_blocks"]) - set(after_sum["user_blocks"]))
    if missing_blocks:
        issues.append(
            DWGRoundTripIssue(
                code="ROUNDTRIP_MISSING_BLOCKS",
                severity="error",
                category="blocks",
                message=f"Round-tripped DXF lost user block definition(s): {missing_blocks}",
                details={"missing_blocks": missing_blocks},
            )
        )

    # 3. Paperspace / modelspace layouts
    missing_layouts = sorted(set(before_sum["layouts"]) - set(after_sum["layouts"]))
    if missing_layouts:
        issues.append(
            DWGRoundTripIssue(
                code="ROUNDTRIP_MISSING_LAYOUTS",
                severity="error",
                category="layouts",
                message=f"Round-tripped DXF lost layout(s): {missing_layouts}",
                details={"missing_layouts": missing_layouts},
            )
        )

    # 4. Text styles & DimStyles
    missing_styles = sorted(set(before_sum["text_styles"]) - set(after_sum["text_styles"]))
    missing_dimstyles = sorted(set(before_sum["dim_styles"]) - set(after_sum["dim_styles"]))
    if missing_styles or missing_dimstyles:
        issues.append(
            DWGRoundTripIssue(
                code="ROUNDTRIP_MISSING_STYLES",
                severity="warning",
                category="styles",
                message=f"Style drift across round-trip: missing text_styles={missing_styles}, dim_styles={missing_dimstyles}",
                details={"missing_text_styles": missing_styles, "missing_dim_styles": missing_dimstyles},
            )
        )

    # 5. Modelspace entity counts per DXF type
    b_counts: dict[str, int] = before_sum["entity_counts"]
    a_counts: dict[str, int] = after_sum["entity_counts"]
    all_types = sorted(set(b_counts) | set(a_counts))
    count_diffs = {
        t: {"before": b_counts.get(t, 0), "after": a_counts.get(t, 0)}
        for t in all_types
        if b_counts.get(t, 0) != a_counts.get(t, 0)
    }
    if count_diffs:
        # Losing primary entities is an error; pure auxiliary additions are warnings
        lost_any = any(v["after"] < v["before"] for v in count_diffs.values())
        issues.append(
            DWGRoundTripIssue(
                code="ROUNDTRIP_ENTITY_COUNT_CHANGED",
                severity="error" if lost_any else "warning",
                category="entities",
                message=f"Modelspace entity counts changed across round-trip: {count_diffs}",
                details={"differences": count_diffs},
            )
        )

    # 6. Bounding box extents check
    b_ext = before_sum["extents_mm"]
    a_ext = after_sum["extents_mm"]
    if b_ext is not None and a_ext is not None:
        max_delta = max(abs(bv - av) for bv, av in zip(b_ext, a_ext, strict=True))
        if not isclose(max_delta, 0.0, abs_tol=bbox_tolerance_mm) and max_delta > bbox_tolerance_mm:
            issues.append(
                DWGRoundTripIssue(
                    code="ROUNDTRIP_EXTENTS_DRIFT",
                    severity="error",
                    category="extents",
                    message=(
                        f"Modelspace extents drifted by {max_delta:.4f} mm "
                        f"(limit {bbox_tolerance_mm} mm): before={b_ext}, after={a_ext}"
                    ),
                    details={"before_extents_mm": b_ext, "after_extents_mm": a_ext, "max_delta_mm": round(max_delta, 4)},
                )
            )
    elif (b_ext is None) != (a_ext is None):
        issues.append(
            DWGRoundTripIssue(
                code="ROUNDTRIP_EXTENTS_MISSING",
                severity="error",
                category="extents",
                message=f"Modelspace extents presence changed: before={b_ext}, after={a_ext}",
            )
        )

    has_error = any(i.severity == "error" for i in issues)
    report = DWGRoundTripReport(
        status="failed" if has_error else "passed",
        before_path=str(before_resolved),
        after_path=str(after_resolved),
        before_summary=before_sum,
        after_summary=after_sum,
        issues=tuple(issues),
    )

    if output_report_path is not None:
        out = Path(output_report_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return report
