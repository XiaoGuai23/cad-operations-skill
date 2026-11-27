"""MCP Tool, Resource, and Prompt registry backed by verified 2D CAD pipelines."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from cad_skill_core.benchmark.cad1k_harness import run_cad1k_benchmark
from cad_skill_core.benchmark.regression_flywheel import (
    list_regression_cases,
    record_regression_case,
    replay_regression_vault,
)
from cad_skill_core.civil.bridge_calc import (
    audit_bridge_design_consistency,
    compute_hydrology_and_span,
    compute_lateral_distribution,
    compute_polygon_section_properties,
    compute_prestress_estimation_and_loss,
    compute_superstructure_envelopes,
    compute_t_girder_section,
    ingest_and_audit_calc_table,
    run_full_bridge_calculation,
    solve_continuous_cap_beam_1d,
)
from cad_skill_core.civil.bridge_drawings import CivilDrawingType, generate_civil_bridge_bundle
from cad_skill_core.documents.spec_compiler import (
    PartFamily,
    compile_spec_from_feature_graph,
    compile_spec_from_files,
)
from cad_skill_core.geometry.feature_graph import FeatureGraphSpec
from cad_skill_core.environment import check_environment
from cad_skill_core.inspection.legacy_audit import audit_legacy_dxf, clean_legacy_dxf
from cad_skill_core.inspection.rubric_scorer import score_dxf_drawing
from cad_skill_core.modeling3d import (
    Model3DSpec,
    Part3DFamily,
    compile_model3d_from_files,
    compile_model3d_from_text,
    load_model3d_spec,
    probe_3d_capabilities,
    project_model3d_to_drawing_spec,
    run_3d_task,
)
from cad_skill_core.pipeline import PipelineOptions, run_task
from cad_skill_core.revision.common import sha256_file
from cad_skill_core.revision.ir_engine import StructuredRevisionIR, execute_revision_ir
from cad_skill_core.revision.local_review import review_files
from cad_skill_core.revision.nl_revise import execute_nl_region_revision
from cad_skill_core.revision.operations import operation_catalog
from cad_skill_core.revision.preview import preview_file
from cad_skill_core.revision.workflow import apply_revision, inventory_drawing
from cad_skill_core.schema.io import load_spec, save_spec
from cad_skill_core.schema.models import DrawingSpec
from cad_skill_core.standards.preference_memory import (
    default_preference_memory_path,
    learn_preferences_from_sample_dxf,
    load_preference_store,
    set_preference_status,
    teach_preferences_from_text,
)
from cad_skill_core.standards.profile import list_builtin_standards
from cad_skill_core.standards.sample_learner import (
    learn_standard_from_dxf,
    save_standard_yaml,
)

from .cad_scene import compute_dxf_vector_diff, extract_dxf_vector_scene
from .industry_adapters import (
    build_auto_style_font_map,
    generate_electrical_sld_bundle,
    generate_pavement_bundle,
)
from .mcp_app_ui import MCP_APP_MIME_TYPE, MCP_APP_RESOURCE_URI, get_mcp_app_viewer_html
from .sandbox import (
    MCPSandboxError,
    MCPWorkspaceSandbox,
    default_runtime_root,
    resolve_safe_child_path,
    validate_safe_single_name,
)


class StrictInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DoctorArgs(StrictInputModel):
    skip_native: bool = Field(
        default=True,
        description="Skip probing local AutoCAD installation when only headless 2D check is needed.",
    )
    refresh: bool = Field(default=False, description="Force fresh environment probe.")


class CompileSpecArgs(StrictInputModel):
    markdown_text: str | None = Field(
        default=None,
        description="Inline project brief, manual excerpt, or Markdown parameter table.",
    )
    input_paths: list[str] = Field(
        default_factory=list,
        description="Paths to .md, .txt, .csv, .tsv, .docx, or .pdf files inside allowed workspace roots.",
    )
    part_family: PartFamily = Field(
        default="auto",
        description="Target mechanical part family or 'auto' for automatic detection.",
    )
    language: Literal["auto", "en", "zh"] = Field(
        default="auto",
        description="Title block and view label language ('auto', 'zh', or 'en').",
    )


class ValidateSpecArgs(StrictInputModel):
    spec: dict[str, Any] | None = Field(default=None, description="Inline DrawingSpec JSON object.")
    spec_path: str | None = Field(default=None, description="Path to a DrawingSpec JSON file.")


class LearnStandardArgs(StrictInputModel):
    sample_dxf_path: str | None = Field(default=None, description="Optional path to a sample DXF file inside allowed roots.")
    sample_dxf_dir: str | None = Field(default=None, description="Optional path to a directory of sample DXF sheets (e.g. reference construction album).")
    sample_docx_path: str | None = Field(default=None, description="Optional path to a reference .docx specification/thesis template inside allowed roots.")
    profile_name: str | None = Field(default=None, description="Optional custom profile name.")
    confirm_preferences: bool = Field(default=True, description="Mark learned drafting preferences as confirmed in .cad-memory.")


class GenerateMechanical2DArgs(StrictInputModel):
    spec: dict[str, Any] | None = Field(default=None, description="Validated DrawingSpec JSON object.")
    spec_path: str | None = Field(default=None, description="Path to a DrawingSpec JSON file.")
    markdown_brief: str | None = Field(
        default=None,
        description="Optional inline engineering brief to auto-compile before generating.",
    )
    standard_profile: str | None = Field(
        default=None,
        description="Optional industry drawing standard preset ('gb-mechanical', 'gb-heavy-machinery', 'gb-civil-construction', 'iso-international').",
    )
    include_pro_symbols: bool = Field(
        default=True,
        description="Automatically synthesize GB/T 131 surface roughness Ra ticks, datum [A], and GD&T feature control frames on compiled mechanical parts.",
    )
    max_repair_iterations: int = Field(default=3, ge=0, le=3)
    include_image_base64: bool = Field(
        default=False,
        description="Attach rendered drawing.png as an MCP ImageContent block for visual inspection.",
    )


class GenerateElectricalSLDArgs(StrictInputModel):
    spec: dict[str, Any] = Field(description="Electrical single-line diagram JSON specification.")
    stem: str = Field(default="electrical_sld", description="Output filename stem.")
    expect_text: dict[str, int] = Field(
        default_factory=dict,
        description="Optional pattern->expected_count assertions verified on the generated DXF.",
    )
    include_image_base64: bool = Field(default=False)


class GeneratePavementArgs(StrictInputModel):
    spec: dict[str, Any] = Field(description="Pavement structure cross-section JSON specification.")
    include_image_base64: bool = Field(default=False)


class InventoryDXFArgs(StrictInputModel):
    dxf_path: str | None = Field(default=None, description="Path to a DXF file inside allowed roots.")
    task_id: str | None = Field(default=None, description="Existing MCP task_id to inspect.")
    artifact_name: str = Field(default="drawing.dxf", description="Artifact filename inside task_id.")


class ReviseDXFArgs(StrictInputModel):
    dxf_path: str | None = Field(default=None, description="Source DXF path inside allowed roots.")
    task_id: str | None = Field(default=None, description="Existing MCP task_id containing source DXF.")
    artifact_name: str = Field(default="drawing.dxf")
    plan: dict[str, Any] = Field(
        description=(
            "Verified incremental revision plan JSON object with keys: "
            "'schema' ('cad-revision/v1'), 'baseline_sha256', 'units' (matching inventory units), "
            "'tolerance', and 'operations'."
        )
    )
    dry_run: bool = Field(default=False, description="Validate and simulate without writing output DXF.")
    include_image_base64: bool = Field(default=False)


class PreviewAndDiffArgs(StrictInputModel):
    dxf_path: str = Field(description="Primary (or 'before') DXF path inside allowed roots.")
    after_dxf_path: str | None = Field(
        default=None,
        description="Optional 'after' DXF path to produce matched before/after diff review.",
    )
    regions: list[list[float]] | None = Field(
        default=None,
        description="Optional list of [xmin, ymin, xmax, ymax] crop boxes in CAD coordinates.",
    )
    pdf: bool = Field(default=False)
    include_image_base64: bool = Field(default=False)


class Generate3DArgs(StrictInputModel):
    spec: dict[str, Any] | None = Field(default=None, description="Validated Model3DSpec JSON object.")
    spec_path: str | None = Field(default=None, description="Path to a Model3DSpec JSON file.")
    markdown_brief: str | None = Field(
        default=None,
        description="Conversational natural-language or Markdown engineering brief to auto-compile into a 3D model.",
    )
    with_2d_sheet: bool = Field(
        default=False,
        description="Also project and render an audited multi-view GB/T 2D engineering drawing alongside 3D outputs.",
    )
    run_native_cad: bool = Field(
        default=False,
        description="Also invoke an installed local CAD console (AutoCAD/ZWCAD/GstarCAD) to build a native 3DSOLID .dwg.",
    )
    driver_preference: Literal["auto", "autocad", "zwcad", "gstarcad"] = Field(
        default="auto",
        description="Preferred local CAD driver when run_native_cad=true.",
    )
    circle_segments: int = Field(default=64, ge=24, le=256)
    include_image_base64: bool = Field(
        default=False,
        description="Attach shaded isometric iso.png as an MCP ImageContent block.",
    )


class Compile3DSpecArgs(StrictInputModel):
    markdown_text: str | None = Field(
        default=None,
        description="Conversational engineering description or Markdown brief for 3D modeling.",
    )
    input_paths: list[str] = Field(
        default_factory=list,
        description="Paths to .md, .txt, .csv, .tsv, .docx, or .pdf files inside allowed workspace roots.",
    )
    part_family: Part3DFamily = Field(
        default="auto",
        description="Target 3D mechanical part family or 'auto' for automatic inference.",
    )


class Project3DTo2DArgs(StrictInputModel):
    spec: dict[str, Any] | None = Field(default=None, description="Model3DSpec JSON object.")
    spec_path: str | None = Field(default=None, description="Path to a Model3DSpec JSON file.")
    sheet_size: Literal["A0", "A1", "A2", "A3", "A4"] = Field(default="A3")
    scale: str = Field(default="auto")


class ListDriversArgs(StrictInputModel):
    allowed_autocad_versions: list[str] | None = Field(
        default=None,
        description="Optional subset of AutoCAD versions to accept, e.g. ['2023', '2024'].",
    )


class VerifyRoundTripArgs(StrictInputModel):
    before_dxf_path: str = Field(description="Baseline DXF path inside allowed sandbox roots.")
    after_dxf_path: str = Field(description="Round-tripped DXF path inside allowed sandbox roots.")
    bbox_tolerance_mm: float = Field(default=0.5, gt=0.0, le=50.0)


class OpenViewerArgs(StrictInputModel):
    task_id: str | None = Field(
        default=None,
        description="Optional MCP task_id to display immediately in the interactive CAD blueprint & 3D viewer.",
    )
    dxf_path: str | None = Field(
        default=None,
        description="Optional DXF path inside allowed sandbox roots to inspect and region-comment.",
    )


class AppLoadSceneArgs(StrictInputModel):
    task_id: str | None = Field(default=None, description="Task ID containing 2D DXF and/or 3D OBJ artifacts.")
    dxf_path: str | None = Field(default=None, description="Direct DXF path inside allowed roots.")


class ScoreDrawingArgs(StrictInputModel):
    dxf_path: str | None = Field(default=None, description="Path to a DXF file inside allowed roots.")
    task_id: str | None = Field(default=None, description="Existing MCP task_id containing a DXF artifact.")
    artifact_name: str = Field(default="drawing.dxf", description="DXF artifact filename inside task_id.")
    custom_rubric: dict[str, Any] | list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional cad-1000-hours compatible rubrics.json criteria dictionary or list.",
    )


class RunCAD1KEvalArgs(StrictInputModel):
    bundle_dirs: list[str] | None = Field(
        default=None,
        description="Optional list of local cad-1000-hours task bundle directories (video files are automatically skipped).",
    )
    standard_profile: str = Field(
        default="gb-mechanical",
        description="Standard preset profile used during benchmark generation.",
    )


class ReviseByNLArgs(StrictInputModel):
    dxf_path: str | None = Field(default=None, description="Source DXF path inside allowed roots.")
    task_id: str | None = Field(default=None, description="Existing MCP task_id containing source DXF.")
    artifact_name: str = Field(default="drawing.dxf", description="Artifact filename inside task_id.")
    prompt_text: str = Field(
        description="Natural-language marquee region edit or inspection instruction (e.g. '把孔改成 Φ18', '删掉虚线', '材料改成 40Cr', '往右移 15')."
    )
    region_box: list[float] | None = Field(
        default=None,
        description="Optional [xmin, ymin, xmax, ymax] marquee box in CAD millimetre coordinates.",
    )
    candidate_handles: list[str] | None = Field(
        default=None,
        description="Optional list of DXF entity handles inside the marquee selection.",
    )
    expected_sha256: str | None = Field(
        default=None,
        description="Optional baseline SHA-256 fingerprint guard.",
    )
    structured_ir: dict[str, Any] | None = Field(
        default=None,
        description="Optional pre-compiled StructuredRevisionIR ('cad-revision-ir/v2') JSON object from an LLM/Agent.",
    )
    dry_run: bool = Field(default=False, description="Simulate without writing revised DXF.")


class AuditLegacyDrawingArgs(StrictInputModel):
    dxf_path: str | None = Field(default=None, description="Path to an external/legacy DXF file inside allowed roots.")
    task_id: str | None = Field(default=None, description="Existing MCP task_id containing a DXF artifact.")
    artifact_name: str = Field(default="drawing.dxf", description="Artifact filename inside task_id.")
    gap_tolerance_mm: float = Field(
        default=2.0,
        gt=0.0,
        le=25.0,
        description="Maximum endpoint micro-gap distance (mm) flagged as an unclosed contour gap.",
    )
    auto_clean: bool = Field(
        default=False,
        description="When true, also write a cleaned DXF copy removing zero-length entities, duplicates, and micro-gaps.",
    )
    close_micro_gaps_tol: float = Field(
        default=0.8,
        ge=0.0,
        le=5.0,
        description="Endpoint micro-gap snap threshold (mm) used when auto_clean=true.",
    )


class ApplyRevisionIRArgs(StrictInputModel):
    dxf_path: str | None = Field(default=None, description="Source DXF path inside allowed roots.")
    task_id: str | None = Field(default=None, description="Existing MCP task_id containing source DXF.")
    artifact_name: str = Field(default="drawing.dxf", description="Artifact filename inside task_id.")
    ir: dict[str, Any] = Field(
        description=(
            "Typed StructuredRevisionIR ('cad-revision-ir/v2') JSON object supporting "
            "add_circle_hole, add_bolt_circle, chamfer_corner, fillet_corner, and mirror_entities."
        )
    )
    region_box: list[float] | None = Field(default=None, description="Optional [xmin, ymin, xmax, ymax] marquee box.")
    candidate_handles: list[str] | None = Field(default=None, description="Optional target entity handles.")
    expected_sha256: str | None = Field(default=None, description="Optional baseline SHA-256 guard.")
    dry_run: bool = Field(default=False, description="Validate and simulate without writing output DXF.")


class VectorDiffArgs(StrictInputModel):
    before_dxf_path: str = Field(description="Baseline ('before') DXF path inside allowed roots.")
    after_dxf_path: str = Field(description="Revised ('after') DXF path inside allowed roots.")
    tolerance: float = Field(default=1e-4, gt=0.0, le=1.0)


class ManagePreferencesArgs(StrictInputModel):
    action: Literal["list", "teach", "learn_sample", "confirm", "reject"] = Field(
        default="list",
        description="Action on project-level drafting preferences ('.cad-memory/project-preferences.json').",
    )
    instruction_text: str | None = Field(
        default=None,
        description="Natural-language preference instruction when action='teach' (e.g., '法兰图孔阵列改成数量×孔径另标节圆，引线放视图右侧，确认以后沿用').",
    )
    sample_dxf_path: str | None = Field(
        default=None,
        description="Sample DXF path inside allowed roots when action='learn_sample'.",
    )
    pref_id: str | None = Field(
        default=None,
        description="Target preference ID when action in {'confirm', 'reject'}.",
    )
    part_family: str = Field(default="auto", description="Target part family or 'auto'.")
    confirm: bool = Field(default=False, description="Immediately mark learned sample habits as confirmed.")


class GenerateFeatureGraphArgs(StrictInputModel):
    feature_graph: dict[str, Any] | None = Field(
        default=None,
        description="Composable FeatureGraphSpec ('cad-feature-graph/v1') JSON object with base_profile, thickness_mm, and features.",
    )
    markdown_brief: str | None = Field(
        default=None,
        description="Natural-language non-standard plate/profile brief when feature_graph is omitted.",
    )
    project_name: str | None = Field(default=None)
    drawing_number: str | None = Field(default=None)
    material: str | None = Field(default="Q235B")
    standard_profile: str | None = Field(default=None)
    include_pro_symbols: bool = Field(default=True)
    include_image_base64: bool = Field(default=False)


class ManageRegressionsArgs(StrictInputModel):
    action: Literal["list", "record", "replay"] = Field(default="list")
    case_id: str | None = Field(default=None, description="Unique slug when action='record'.")
    title: str | None = Field(default=None, description="Human-readable case title when action='record'.")
    dxf_path: str | None = Field(default=None, description="Baseline DXF path when action='record'.")
    task_id: str | None = Field(default=None, description="Existing MCP task_id when action='record'.")
    artifact_name: str = Field(default="drawing.dxf")
    prompt: str | None = Field(default=None, description="Revision prompt when action='record'.")
    candidate_handles: list[str] | None = Field(default=None)
    region_box: list[float] | None = Field(default=None)
    structured_ir: dict[str, Any] | None = Field(default=None)
    must_reject: bool = Field(default=False)
    expected_post_conditions: list[dict[str, Any]] = Field(default_factory=list)
    bug_note: str = Field(default="")


class CivilBridgeCalcArgs(StrictInputModel):
    mode: Literal[
        "full",
        "wenchang",
        "calc_book",
        "thesis_docx",
        "calc_figures",
        "fem_compare",
        "change_impact",
        "cross_check",
        "lookup_bar",
        "survey_ingest",
        "hydrology",
        "polygon_section",
        "t_girder_section",
        "lateral_distribution",
        "superstructure_envelopes",
        "prestress",
        "cap_beam_1d",
        "consistency_audit",
        "table_audit",
    ] = Field(
        default="full",
        description="Calculation, calculation-book, formal college Word thesis, Word/PPT figures, FEM defense comparison, design change impact, hand cross-check, rebar lookup, survey ingestion, or QA audit mode.",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Input engineering parameters dictionary for the selected calculation mode.",
    )
    bar_id: str | None = Field(
        default=None,
        description="Target tendon or rebar ID (e.g. 'N1', 'R1', 'GJ1', 'GL1', 'DZ1', 'QT3') when mode='lookup_bar'.",
    )
    table_text: str | None = Field(
        default=None,
        description="Inline Markdown/CSV/TSV limit-state table when mode='table_audit'.",
    )
    table_path: str | None = Field(
        default=None,
        description="Path to a CSV/TSV/Markdown calculation check table inside allowed roots when mode='table_audit'.",
    )


class GenerateCivilBridgeArgs(StrictInputModel):
    drawing_type: CivilDrawingType = Field(
        description=(
            "Target Civil & Bridge CAD sheet type: 'bridge_elevation' (桥型总体布置图), "
            "'t_girder_section' (标准横断面及T梁一般构造图), 'tendon_layout' (预应力钢束布置图), "
            "'pier_and_abutment' (桥墩/桥台一般构造及支座布置图), or 'mechanics_diagrams' (横向影响线及内力包络图)."
        )
    )
    spec: dict[str, Any] = Field(
        default_factory=dict,
        description="Parametric dimensions and labels for the requested civil/bridge sheet.",
    )
    stem: str | None = Field(default=None, description="Optional safe output filename stem.")
    include_image_base64: bool = Field(
        default=False,
        description="Attach rendered high-DPI sheet PNG as an MCP ImageContent block.",
    )


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[StrictInputModel]
    handler: Callable[[MCPWorkspaceSandbox, Any], tuple[dict[str, Any], Path | None, bool]]
    meta: dict[str, Any] | None = None


def _resolve_dxf_source(
    sandbox: MCPWorkspaceSandbox,
    dxf_path: str | None,
    task_id: str | None,
    artifact_name: str = "drawing.dxf",
) -> Path:
    if bool(dxf_path) == bool(task_id):
        raise ValueError("provide exactly one of dxf_path or task_id")
    if task_id is not None:
        return sandbox.resolve_task_artifact(task_id, artifact_name)
    assert dxf_path is not None
    return sandbox.resolve_readable_file(dxf_path)


def _handle_doctor(
    sandbox: MCPWorkspaceSandbox, args: DoctorArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    report = check_environment(
        package_root=default_runtime_root(),
        refresh=args.refresh,
        skip_native=args.skip_native,
    )
    report["mcp_workspace"] = str(sandbox.workspace_root)
    report["capabilities_3d"] = probe_3d_capabilities()
    return report, None, False


def _handle_compile_spec(
    sandbox: MCPWorkspaceSandbox, args: CompileSpecArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    if not args.markdown_text and not args.input_paths:
        raise ValueError("provide markdown_text, input_paths, or both")
    task_id, task_dir = sandbox.allocate_task_dir("compile")
    input_dir = task_dir / "input"
    output_dir = task_dir / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    resolved_files: list[Path] = []
    if args.markdown_text:
        inline_md = input_dir / "brief.md"
        inline_md.write_text(args.markdown_text, encoding="utf-8")
        resolved_files.append(inline_md)
    for raw in args.input_paths:
        resolved_files.append(sandbox.resolve_readable_file(raw))

    spec_out = output_dir / "drawing-spec.json"
    pref_mem = default_preference_memory_path(sandbox.workspace_root)
    spec = compile_spec_from_files(
        resolved_files,
        part_family=args.part_family,
        title_block_language=args.language,
        preference_memory_path=pref_mem,
        output_path=spec_out,
    )
    has_blocking = bool(spec.validation.missing or spec.validation.conflicts)
    status = "incomplete" if has_blocking else "compiled"
    meta = {
        "domain": "compile_spec",
        "status": status,
        "artifacts": [
            {
                "name": "drawing-spec.json",
                "path": str(spec_out),
                "size": spec_out.stat().st_size,
                "sha256": sha256_file(spec_out),
            }
        ],
        "validation": spec.validation.model_dump(mode="json"),
        "spec": spec.model_dump(mode="json"),
    }
    sandbox.write_task_metadata(task_id, meta)
    return {"task_id": task_id, **meta}, None, False


def _handle_validate_spec(
    sandbox: MCPWorkspaceSandbox, args: ValidateSpecArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    if (args.spec is None) == (args.spec_path is None):
        raise ValueError("provide exactly one of spec or spec_path")
    if args.spec is not None:
        validated = DrawingSpec.model_validate(args.spec)
    else:
        assert args.spec_path is not None
        validated = load_spec(sandbox.resolve_readable_file(args.spec_path))
    return (
        {
            "status": "valid",
            "project": validated.project.model_dump(mode="json"),
            "views": validated.requirements.views,
            "entity_count": len(validated.geometry.entities),
            "dimension_count": len(validated.dimensions),
            "validation": validated.validation.model_dump(mode="json"),
        },
        None,
        False,
    )


def _handle_learn_standard(
    sandbox: MCPWorkspaceSandbox, args: LearnStandardArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    from cad_skill_core.standards.template_learner import learn_dxf_album_profile, learn_template_styles, save_album_template

    if not args.sample_dxf_path and not args.sample_dxf_dir and not args.sample_docx_path:
        raise ValueError("provide at least one of sample_dxf_path, sample_dxf_dir, or sample_docx_path")

    resolved_dxf_file = sandbox.resolve_readable_file(args.sample_dxf_path) if args.sample_dxf_path else None
    resolved_dxf_dir = sandbox.resolve_readable_dir(args.sample_dxf_dir) if args.sample_dxf_dir else None
    resolved_docx = sandbox.resolve_readable_file(args.sample_docx_path) if args.sample_docx_path else None

    task_id, task_dir = sandbox.allocate_task_dir("standard")
    out_dir = task_dir / "output"
    out_dir.mkdir()
    mem_dir = sandbox.workspace_root / ".cad-memory"
    pref_mem = default_preference_memory_path(sandbox.workspace_root)

    # Single-DXF backward-compatible path + album & optional .docx learning
    if resolved_dxf_file is not None and resolved_dxf_dir is None and resolved_docx is None:
        learned = learn_standard_from_dxf(
            resolved_dxf_file,
            profile_name=args.profile_name,
            preference_memory_path=pref_mem,
            confirm_preferences=args.confirm_preferences,
        )
        yaml_path = save_standard_yaml(learned.standard, out_dir / "learned-standard.yaml")
        album_prof = learn_dxf_album_profile(resolved_dxf_file, profile_name=args.profile_name)
        album_path = save_album_template(album_prof, mem_dir / "album-template.json")
        payload: dict[str, Any] = {
            "task_id": task_id,
            "status": "learned",
            "profile_name": learned.standard.name,
            "detected_sheet": learned.detected_sheet,
            "sample_extents_mm": learned.sample_extents_mm,
            "matched_layers": learned.matched_layers,
            "detected_habits": learned.detected_habits,
            "album_template": album_prof.model_dump(mode="json"),
            "artifacts": [
                {
                    "name": yaml_path.name,
                    "path": str(yaml_path),
                    "size": yaml_path.stat().st_size,
                    "sha256": sha256_file(yaml_path),
                },
                {
                    "name": album_path.name,
                    "path": str(album_path),
                    "size": album_path.stat().st_size,
                    "sha256": sha256_file(album_path),
                },
            ],
            "standard": learned.standard.model_dump(mode="json"),
        }
        sandbox.write_task_metadata(task_id, payload)
        return payload, None, False

    learned_bundle = learn_template_styles(
        docx_path=resolved_docx,
        dxf_path_or_dir=resolved_dxf_dir or resolved_dxf_file,
        output_dir=mem_dir,
        profile_name=args.profile_name,
        confirm_preferences=args.confirm_preferences,
    )
    artifacts_out: list[dict[str, Any]] = []
    for key_p in ("thesis_template_path", "album_template_path", "standard_yaml_path", "bundle_path"):
        fp_s = learned_bundle.get(key_p)
        if fp_s and Path(fp_s).is_file():
            fp = Path(fp_s)
            dst_copy = out_dir / fp.name
            dst_copy.write_bytes(fp.read_bytes())
            artifacts_out.append(
                {
                    "name": dst_copy.name,
                    "path": str(dst_copy),
                    "size": dst_copy.stat().st_size,
                    "sha256": sha256_file(dst_copy),
                }
            )
    payload = {
        "task_id": task_id,
        **learned_bundle,
        "artifacts": artifacts_out,
    }
    sandbox.write_task_metadata(task_id, payload)
    return payload, None, False


def _handle_generate_mechanical_2d(
    sandbox: MCPWorkspaceSandbox, args: GenerateMechanical2DArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    sources_given = sum(x is not None for x in (args.spec, args.spec_path, args.markdown_brief))
    if sources_given != 1:
        raise ValueError("provide exactly one of spec, spec_path, or markdown_brief")

    task_id, task_dir = sandbox.allocate_task_dir("mech2d")
    (task_dir / "input").mkdir()
    (task_dir / "work").mkdir()

    if args.markdown_brief is not None:
        brief_path = task_dir / "input" / "project.md"
        brief_path.write_text(args.markdown_brief, encoding="utf-8")
        compiled = compile_spec_from_files(
            [brief_path],
            include_pro_symbols=args.include_pro_symbols,
            preference_memory_path=default_preference_memory_path(sandbox.workspace_root),
            output_path=task_dir / "work" / "drawing-spec.json",
        )
        if compiled.validation.missing or compiled.validation.conflicts:
            payload = {
                "task_id": task_id,
                "domain": "mechanical_2d",
                "status": "blocked_by_spec_validation",
                "validation": compiled.validation.model_dump(mode="json"),
                "artifacts": [],
            }
            sandbox.write_task_metadata(task_id, payload)
            return payload, None, True
    elif args.spec is not None:
        validated = DrawingSpec.model_validate(args.spec)
        save_spec(validated, task_dir / "work" / "drawing-spec.json")
    else:
        assert args.spec_path is not None
        src_file = sandbox.resolve_readable_file(args.spec_path)
        validated = load_spec(src_file)
        save_spec(validated, task_dir / "work" / "drawing-spec.json")

    with sandbox.render_lock:
        result = run_task(
            task_dir,
            PipelineOptions(
                run_autocad=False,
                max_repair_iterations=args.max_repair_iterations,
                standard_profile=args.standard_profile,
            ),
        )

    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    artifacts = [
        {
            "name": p.name,
            "path": str(p.resolve()),
            "size": p.stat().st_size,
            "sha256": sha256_file(p),
        }
        for p in sorted(result.outputs, key=lambda item: item.name)
    ]
    dxf_out = task_dir / "output" / "drawing.dxf"
    rubric_dict = (
        score_dxf_drawing(dxf_out, audit_json_path=result.audit_path).to_dict()
        if dxf_out.is_file()
        else None
    )
    png_path = task_dir / "output" / "drawing.png"
    preview_png = png_path if png_path.is_file() and args.include_image_base64 else None
    payload = {
        "task_id": task_id,
        "domain": "mechanical_2d",
        "status": result.status,
        "standard_profile": args.standard_profile or "gb-mechanical",
        "audit_summary": audit.get("summary", {}),
        "rubric_score": rubric_dict,
        "issues": audit.get("issues", []),
        "artifacts": artifacts,
    }
    sandbox.write_task_metadata(task_id, payload)
    return payload, preview_png, (result.status == "failed")


def _handle_generate_electrical_sld(
    sandbox: MCPWorkspaceSandbox, args: GenerateElectricalSLDArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    task_id, task_dir = sandbox.allocate_task_dir("sld")
    out_dir = task_dir / "output"
    expectations = [(k, int(v)) for k, v in args.expect_text.items()]
    clean_stem = validate_safe_single_name(args.stem, label="stem")
    bundle = generate_electrical_sld_bundle(
        args.spec,
        out_dir,
        stem=clean_stem,
        expect_text=expectations,
        render_lock=sandbox.render_lock,
    )
    png_path = bundle.pop("preview_png_path")
    sld_dxf = resolve_safe_child_path(out_dir, f"{clean_stem}.dxf", label="stem")
    rubric_dict = score_dxf_drawing(sld_dxf).to_dict() if sld_dxf.is_file() else None
    payload = {
        "task_id": task_id,
        "domain": "electrical_sld",
        "rubric_score": rubric_dict,
        **bundle,
    }
    sandbox.write_task_metadata(task_id, payload)
    return (
        payload,
        (png_path if args.include_image_base64 and png_path.is_file() else None),
        bundle["status"] == "failed",
    )


def _handle_generate_pavement(
    sandbox: MCPWorkspaceSandbox, args: GeneratePavementArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    task_id, task_dir = sandbox.allocate_task_dir("pavement")
    out_dir = task_dir / "output"
    bundle = generate_pavement_bundle(
        args.spec,
        out_dir,
        render_lock=sandbox.render_lock,
    )
    png_path = bundle.pop("preview_png_path")
    dxf_cands = sorted(out_dir.glob("*.dxf"))
    rubric_dict = score_dxf_drawing(dxf_cands[0]).to_dict() if dxf_cands else None
    payload = {
        "task_id": task_id,
        "domain": "pavement",
        "rubric_score": rubric_dict,
        **bundle,
    }
    sandbox.write_task_metadata(task_id, payload)
    return (
        payload,
        (png_path if args.include_image_base64 and png_path.is_file() else None),
        bundle["status"] == "failed",
    )


def _handle_inventory_dxf(
    sandbox: MCPWorkspaceSandbox, args: InventoryDXFArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    dxf_file = _resolve_dxf_source(sandbox, args.dxf_path, args.task_id, args.artifact_name)
    inv = inventory_drawing(dxf_file)
    return inv, None, False


def _handle_revise_dxf(
    sandbox: MCPWorkspaceSandbox, args: ReviseDXFArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    source_dxf = _resolve_dxf_source(sandbox, args.dxf_path, args.task_id, args.artifact_name)
    task_id, task_dir = sandbox.allocate_task_dir("revise")
    plan_path = task_dir / "revision-plan.json"
    plan_path.write_text(json.dumps(args.plan, ensure_ascii=False, indent=2), encoding="utf-8")
    out_dir = task_dir / "output"

    rev_result = apply_revision(
        source_dxf,
        plan_path,
        out_dir,
        dry_run=args.dry_run,
    )
    preview_png: Path | None = None
    rubric_dict: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = []
    if not args.dry_run and rev_result.get("output_path"):
        out_dxf = Path(rev_result["output_path"])
        rubric_dict = score_dxf_drawing(out_dxf).to_dict()
        font_map = build_auto_style_font_map(out_dxf)
        with sandbox.render_lock:
            prev = preview_file(out_dxf, task_dir / "preview", font_map=font_map)
        png_src = task_dir / "preview" / prev["images"][0]
        png_out = out_dir / "revised.png"
        png_out.write_bytes(png_src.read_bytes())
        if args.include_image_base64:
            preview_png = png_out
        for p in sorted(out_dir.glob("*")):
            if p.is_file():
                artifacts.append(
                    {
                        "name": p.name,
                        "path": str(p.resolve()),
                        "size": p.stat().st_size,
                        "sha256": sha256_file(p),
                    }
                )

    payload = {
        "task_id": task_id,
        "domain": "revision",
        "status": rev_result["status"],
        "dry_run": args.dry_run,
        "revision_result": rev_result,
        "rubric_score": rubric_dict,
        "artifacts": artifacts,
    }
    sandbox.write_task_metadata(task_id, payload)
    return payload, preview_png, False


def _handle_preview_and_diff(
    sandbox: MCPWorkspaceSandbox, args: PreviewAndDiffArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    primary = sandbox.resolve_readable_file(args.dxf_path)
    task_id, task_dir = sandbox.allocate_task_dir("review")
    out_dir = task_dir / "output"

    if args.after_dxf_path is not None:
        after = sandbox.resolve_readable_file(args.after_dxf_path)
        font_map = {**build_auto_style_font_map(primary), **build_auto_style_font_map(after)}
        bounds = args.regions[0] if args.regions else None
        with sandbox.render_lock:
            report = review_files(primary, after, out_dir, bounds=bounds, font_map=font_map)
        after_imgs = report.get("images", {}).get("after", [])
        png_candidate = out_dir / after_imgs[0] if after_imgs else None
        payload = {
            "task_id": task_id,
            "mode": "diff_review",
            "status": report["status"],
            "changed_handles": report["changed_handles"],
            "issues": report["issues"],
            "artifacts": [
                {
                    "name": p.name,
                    "path": str(p.resolve()),
                    "size": p.stat().st_size,
                    "sha256": sha256_file(p),
                }
                for p in sorted(out_dir.glob("*"))
                if p.is_file()
            ],
        }
        sandbox.write_task_metadata(task_id, payload)
        return (
            payload,
            (png_candidate if args.include_image_base64 and png_candidate and png_candidate.is_file() else None),
            False,
        )

    font_map = build_auto_style_font_map(primary)
    with sandbox.render_lock:
        report = preview_file(
            primary,
            out_dir,
            regions=args.regions,
            font_map=font_map,
            pdf=args.pdf,
        )
    first_img = out_dir / report["images"][0] if report.get("images") else None
    payload = {
        "task_id": task_id,
        "mode": "preview",
        "status": report["status"],
        "images": report["images"],
        "pdf": report["pdf"],
        "artifacts": [
            {
                "name": p.name,
                "path": str(p.resolve()),
                "size": p.stat().st_size,
                "sha256": sha256_file(p),
            }
            for p in sorted(out_dir.glob("*"))
            if p.is_file()
        ],
    }
    sandbox.write_task_metadata(task_id, payload)
    return (
        payload,
        (first_img if args.include_image_base64 and first_img and first_img.is_file() else None),
        False,
    )


def _resolve_model3d_spec(
    sandbox: MCPWorkspaceSandbox,
    spec: dict[str, Any] | None,
    spec_path: str | None,
    markdown_brief: str | None = None,
) -> Model3DSpec:
    given = sum(x is not None for x in (spec, spec_path, markdown_brief))
    if given != 1:
        raise ValueError("provide exactly one of spec, spec_path, or markdown_brief")
    if spec is not None:
        return Model3DSpec.model_validate(spec)
    if markdown_brief is not None:
        compiled = compile_model3d_from_text(markdown_brief)
        if compiled.validation.missing or compiled.validation.conflicts:
            raise ValueError(
                f"3D brief is incomplete or conflicting: missing={compiled.validation.missing}, conflicts={compiled.validation.conflicts}"
            )
        return compiled
    assert spec_path is not None
    return load_model3d_spec(sandbox.resolve_readable_file(spec_path))


def _handle_compile_3d_spec(
    sandbox: MCPWorkspaceSandbox, args: Compile3DSpecArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    if not args.markdown_text and not args.input_paths:
        raise ValueError("provide markdown_text, input_paths, or both")
    task_id, task_dir = sandbox.allocate_task_dir("comp3d")
    in_dir = task_dir / "input"
    out_dir = task_dir / "output"
    in_dir.mkdir()
    out_dir.mkdir()

    files: list[Path] = []
    if args.markdown_text:
        md_file = in_dir / "brief3d.md"
        md_file.write_text(args.markdown_text, encoding="utf-8")
        files.append(md_file)
    for raw in args.input_paths:
        files.append(sandbox.resolve_readable_file(raw))

    spec_3d = compile_model3d_from_files(files, part_family=args.part_family)
    out_file = out_dir / "model-3d-spec.json"
    out_file.write_text(
        json.dumps(spec_3d.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    incomplete = bool(spec_3d.validation.missing or spec_3d.validation.conflicts)
    payload = {
        "task_id": task_id,
        "domain": "compile_3d_spec",
        "status": "incomplete" if incomplete else "compiled",
        "validation": spec_3d.validation.model_dump(mode="json"),
        "artifacts": [
            {
                "name": out_file.name,
                "path": str(out_file.resolve()),
                "size": out_file.stat().st_size,
                "sha256": sha256_file(out_file),
            }
        ],
        "spec": spec_3d.model_dump(mode="json"),
    }
    sandbox.write_task_metadata(task_id, payload)
    return payload, None, False


def _handle_generate_3d(
    sandbox: MCPWorkspaceSandbox, args: Generate3DArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    spec_3d = _resolve_model3d_spec(sandbox, args.spec, args.spec_path, args.markdown_brief)
    task_id, task_dir = sandbox.allocate_task_dir("model3d")
    out_dir = task_dir / "output"
    bundle = run_3d_task(
        spec_3d,
        out_dir,
        with_2d_sheet=args.with_2d_sheet,
        circle_segments=args.circle_segments,
        run_native_cad=args.run_native_cad,
        driver_preference=args.driver_preference,
        render_lock=sandbox.render_lock,
    )
    iso_png: Path | None = bundle.pop("iso_png_path", None)
    sheet_dxf = out_dir / "sheet2d.dxf"
    sheet_audit = out_dir / "sheet2d-audit.json"
    rubric_dict = (
        score_dxf_drawing(
            sheet_dxf,
            audit_json_path=(sheet_audit if sheet_audit.is_file() else None),
        ).to_dict()
        if sheet_dxf.is_file()
        else None
    )
    payload = {
        "task_id": task_id,
        "domain": "model_3d",
        "rubric_score": rubric_dict,
        **bundle,
    }
    sandbox.write_task_metadata(task_id, payload)
    return (
        payload,
        (iso_png if args.include_image_base64 and iso_png and iso_png.is_file() else None),
        bundle["status"] == "failed",
    )


def _handle_project_3d_to_2d(
    sandbox: MCPWorkspaceSandbox, args: Project3DTo2DArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    spec_3d = _resolve_model3d_spec(sandbox, args.spec, args.spec_path)
    task_id, task_dir = sandbox.allocate_task_dir("proj3d2d")
    out_dir = task_dir / "output"
    out_dir.mkdir()
    spec_2d = project_model3d_to_drawing_spec(
        spec_3d,
        sheet_size=args.sheet_size,
        scale=args.scale,
    )
    spec_out = out_dir / "drawing-spec.json"
    save_spec(spec_2d, spec_out)
    payload = {
        "task_id": task_id,
        "domain": "project_3d_to_2d",
        "status": "projected",
        "views": spec_2d.requirements.views,
        "scale": spec_2d.sheet.scale,
        "artifacts": [
            {
                "name": spec_out.name,
                "path": str(spec_out.resolve()),
                "size": spec_out.stat().st_size,
                "sha256": sha256_file(spec_out),
            }
        ],
        "drawing_spec": spec_2d.model_dump(mode="json"),
    }
    sandbox.write_task_metadata(task_id, payload)
    return payload, None, False


def _handle_list_drivers(
    sandbox: MCPWorkspaceSandbox, args: ListDriversArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    del sandbox
    from cad_skill_core.cad_drivers import probe_all_cad_drivers

    versions = tuple(args.allowed_autocad_versions) if args.allowed_autocad_versions else None
    matrix = probe_all_cad_drivers(allowed_autocad_versions=versions)
    return matrix, None, False


def _handle_verify_roundtrip(
    sandbox: MCPWorkspaceSandbox, args: VerifyRoundTripArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    from cad_skill_core.cad_drivers import verify_dxf_structural_equivalence

    before_path = sandbox.resolve_readable_file(args.before_dxf_path)
    after_path = sandbox.resolve_readable_file(args.after_dxf_path)
    task_id, task_dir = sandbox.allocate_task_dir("roundtrip")
    out_dir = task_dir / "output"
    out_dir.mkdir()
    report_path = out_dir / "dwg-roundtrip-audit.json"
    report = verify_dxf_structural_equivalence(
        before_path,
        after_path,
        bbox_tolerance_mm=args.bbox_tolerance_mm,
        output_report_path=report_path,
    )
    payload = {
        "task_id": task_id,
        "domain": "verify_roundtrip",
        **report.to_dict(),
        "artifacts": [
            {
                "name": report_path.name,
                "path": str(report_path.resolve()),
                "size": report_path.stat().st_size,
                "sha256": sha256_file(report_path),
            }
        ],
    }
    sandbox.write_task_metadata(task_id, payload)
    return payload, None, report.status == "failed"


def _handle_open_viewer(
    sandbox: MCPWorkspaceSandbox, args: OpenViewerArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    payload: dict[str, Any] = {
        "status": "ready",
        "viewer_uri": MCP_APP_RESOURCE_URI,
        "task_id": args.task_id,
        "dxf_path": args.dxf_path,
    }
    if args.task_id:
        meta = sandbox.read_task_metadata(args.task_id)
        payload["domain"] = meta.get("domain")
    return payload, None, False


def _handle_app_load_scene(
    sandbox: MCPWorkspaceSandbox, args: AppLoadSceneArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    cad_scene_data: dict[str, Any] | None = None
    obj_text: str | None = None
    resolved_dxf_str: str | None = None

    if args.task_id:
        meta = sandbox.read_task_metadata(args.task_id)
        artifacts = meta.get("artifacts", [])
        dxf_art = next(
            (a for a in artifacts if str(a.get("name", "")).endswith(".dxf") and a.get("name") != "model3d.dxf"),
            None,
        )
        if dxf_art is not None:
            dxf_file = sandbox.resolve_task_artifact(args.task_id, str(dxf_art["name"]))
            resolved_dxf_str = str(dxf_file)
            cad_scene_data = extract_dxf_vector_scene(dxf_file)
        obj_art = next((a for a in artifacts if str(a.get("name", "")).endswith(".obj")), None)
        if obj_art is not None:
            obj_file = sandbox.resolve_task_artifact(args.task_id, str(obj_art["name"]))
            obj_text = obj_file.read_text(encoding="utf-8", errors="replace")
    elif args.dxf_path:
        dxf_file = sandbox.resolve_readable_file(args.dxf_path)
        resolved_dxf_str = str(dxf_file)
        cad_scene_data = extract_dxf_vector_scene(dxf_file)
    else:
        raise ValueError("provide task_id or dxf_path")

    return (
        {
            "status": "ok",
            "task_id": args.task_id,
            "dxf_path": resolved_dxf_str,
            "cad_scene": cad_scene_data,
            "obj_text": obj_text,
        },
        None,
        False,
    )


def _handle_score_drawing(
    sandbox: MCPWorkspaceSandbox, args: ScoreDrawingArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    dxf_file = _resolve_dxf_source(sandbox, args.dxf_path, args.task_id, args.artifact_name)
    audit_candidate = dxf_file.parent / "audit.json"
    report = score_dxf_drawing(
        dxf_file,
        audit_json_path=(audit_candidate if audit_candidate.is_file() else None),
        custom_rubric=args.custom_rubric,
    )
    payload = {
        "status": "scored",
        "dxf_path": str(dxf_file),
        **report.to_dict(),
    }
    return payload, None, False


def _handle_run_cad1k_eval(
    sandbox: MCPWorkspaceSandbox, args: RunCAD1KEvalArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    resolved_bundles: list[Path] | None = None
    if args.bundle_dirs:
        resolved_bundles = [
            sandbox.resolve_readable_file(Path(raw_dir) / "task_desc.json").parent
            for raw_dir in args.bundle_dirs
        ]
    task_id, task_dir = sandbox.allocate_task_dir("cad1k")
    out_dir = task_dir / "output"
    with sandbox.render_lock:
        summary = run_cad1k_benchmark(
            out_dir,
            external_bundle_dirs=resolved_bundles,
            standard_profile=args.standard_profile,
        )
    report_path = out_dir / "cad1k-benchmark-report.json"
    payload = {
        "task_id": task_id,
        "domain": "cad1k_benchmark",
        "status": "passed" if summary["passed_count"] == summary["task_count"] else "partial",
        **summary,
        "artifacts": (
            [
                {
                    "name": report_path.name,
                    "path": str(report_path.resolve()),
                    "size": report_path.stat().st_size,
                    "sha256": sha256_file(report_path),
                }
            ]
            if report_path.is_file()
            else []
        ),
    }
    sandbox.write_task_metadata(task_id, payload)
    return payload, None, False


def _handle_revise_by_nl(
    sandbox: MCPWorkspaceSandbox, args: ReviseByNLArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    source_dxf = _resolve_dxf_source(sandbox, args.dxf_path, args.task_id, args.artifact_name)
    task_id, task_dir = sandbox.allocate_task_dir("nlrev")
    out_dir = task_dir / "output"

    nl_res = execute_nl_region_revision(
        source_dxf,
        out_dir,
        args.prompt_text,
        region_box=args.region_box,
        candidate_handles=args.candidate_handles,
        expected_sha256=args.expected_sha256,
        structured_ir=args.structured_ir,
        dry_run=args.dry_run,
    )

    if nl_res.get("mode") == "question_pin" or args.dry_run or not nl_res.get("output_path"):
        payload = {
            "task_id": task_id,
            "domain": "nl_revision",
            "source_dxf_path": str(source_dxf),
            **nl_res,
            "artifacts": [],
        }
        sandbox.write_task_metadata(task_id, payload)
        return payload, None, False

    out_dxf = Path(nl_res["output_path"])
    rubric_dict = score_dxf_drawing(out_dxf).to_dict()
    scene_data = extract_dxf_vector_scene(out_dxf)
    vec_diff = compute_dxf_vector_diff(source_dxf, out_dxf)
    inv_after = inventory_drawing(out_dxf)
    font_map = build_auto_style_font_map(out_dxf)
    with sandbox.render_lock:
        prev = preview_file(out_dxf, task_dir / "preview", font_map=font_map)
    png_src = task_dir / "preview" / prev["images"][0]
    png_out = out_dir / "revised.png"
    png_out.write_bytes(png_src.read_bytes())

    artifacts = [
        {
            "name": p.name,
            "path": str(p.resolve()),
            "size": p.stat().st_size,
            "sha256": sha256_file(p),
        }
        for p in sorted(out_dir.glob("*"))
        if p.is_file()
    ]
    meta_payload = {
        "task_id": task_id,
        "domain": "nl_revision",
        "source_dxf_path": str(source_dxf),
        **nl_res,
        "rubric_score": rubric_dict,
        "vector_diff": vec_diff,
        "artifacts": artifacts,
    }
    sandbox.write_task_metadata(task_id, meta_payload)
    return (
        {
            **meta_payload,
            "scene": scene_data,
            "inventory": inv_after,
        },
        None,
        False,
    )


def _handle_audit_legacy_drawing(
    sandbox: MCPWorkspaceSandbox, args: AuditLegacyDrawingArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    source_dxf = _resolve_dxf_source(sandbox, args.dxf_path, args.task_id, args.artifact_name)
    report = audit_legacy_dxf(source_dxf, gap_tolerance_mm=args.gap_tolerance_mm)
    if not args.auto_clean:
        return report.to_dict(), None, False

    task_id, task_dir = sandbox.allocate_task_dir("legclean")
    out_dir = task_dir / "output"
    out_dxf = out_dir / "cleaned.dxf"
    clean_res = clean_legacy_dxf(
        source_dxf,
        out_dxf,
        close_micro_gaps_tol=args.close_micro_gaps_tol,
    )
    scene_data = extract_dxf_vector_scene(out_dxf)
    vec_diff = compute_dxf_vector_diff(source_dxf, out_dxf)
    inv_after = inventory_drawing(out_dxf)
    artifacts = [
        {
            "name": out_dxf.name,
            "path": str(out_dxf.resolve()),
            "size": out_dxf.stat().st_size,
            "sha256": sha256_file(out_dxf),
        }
    ]
    meta_payload = {
        "task_id": task_id,
        "domain": "legacy_clean",
        "source_dxf_path": str(source_dxf),
        "before_audit": report.to_dict(),
        **clean_res,
        "vector_diff": vec_diff,
        "artifacts": artifacts,
    }
    sandbox.write_task_metadata(task_id, meta_payload)
    return {**meta_payload, "scene": scene_data, "inventory": inv_after}, None, False


def _handle_apply_revision_ir(
    sandbox: MCPWorkspaceSandbox, args: ApplyRevisionIRArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    source_dxf = _resolve_dxf_source(sandbox, args.dxf_path, args.task_id, args.artifact_name)
    task_id, task_dir = sandbox.allocate_task_dir("irrev")
    out_dir = task_dir / "output"
    ir_res = execute_revision_ir(
        source_dxf,
        out_dir,
        args.ir,
        region_box=args.region_box,
        candidate_handles=args.candidate_handles,
        expected_sha256=args.expected_sha256,
        dry_run=args.dry_run,
    )
    if args.dry_run or not ir_res.get("output_path"):
        payload = {
            "task_id": task_id,
            "domain": "ir_revision",
            "source_dxf_path": str(source_dxf),
            **ir_res,
            "artifacts": [],
        }
        sandbox.write_task_metadata(task_id, payload)
        return payload, None, False

    out_dxf = Path(ir_res["output_path"])
    scene_data = extract_dxf_vector_scene(out_dxf)
    vec_diff = compute_dxf_vector_diff(source_dxf, out_dxf)
    inv_after = inventory_drawing(out_dxf)
    rubric_dict = score_dxf_drawing(out_dxf).to_dict()
    artifacts = [
        {
            "name": out_dxf.name,
            "path": str(out_dxf.resolve()),
            "size": out_dxf.stat().st_size,
            "sha256": sha256_file(out_dxf),
        }
    ]
    meta_payload = {
        "task_id": task_id,
        "domain": "ir_revision",
        "source_dxf_path": str(source_dxf),
        **ir_res,
        "rubric_score": rubric_dict,
        "vector_diff": vec_diff,
        "artifacts": artifacts,
    }
    sandbox.write_task_metadata(task_id, meta_payload)
    return {**meta_payload, "scene": scene_data, "inventory": inv_after}, None, False


def _handle_vector_diff(
    sandbox: MCPWorkspaceSandbox, args: VectorDiffArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    before_p = sandbox.resolve_readable_file(args.before_dxf_path)
    after_p = sandbox.resolve_readable_file(args.after_dxf_path)
    return compute_dxf_vector_diff(before_p, after_p, tolerance=args.tolerance), None, False


def _handle_manage_preferences(
    sandbox: MCPWorkspaceSandbox, args: ManagePreferencesArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    mem_path = default_preference_memory_path(sandbox.workspace_root)
    if args.action == "list":
        store = load_preference_store(mem_path)
        return {"status": "ok", "memory_path": str(mem_path), **store.model_dump(mode="json")}, None, False
    if args.action == "teach":
        if not args.instruction_text:
            raise ValueError("instruction_text is required when action='teach'")
        recorded = teach_preferences_from_text(
            mem_path,
            args.instruction_text,
            part_family=args.part_family,
            force_confirm=True if args.confirm else None,
        )
        return {
            "status": "recorded",
            "memory_path": str(mem_path),
            "entries": [e.model_dump(mode="json") for e in recorded],
        }, None, False
    if args.action == "learn_sample":
        if not args.sample_dxf_path:
            raise ValueError("sample_dxf_path is required when action='learn_sample'")
        sample = sandbox.resolve_readable_file(args.sample_dxf_path)
        entries = learn_preferences_from_sample_dxf(
            sample,
            mem_path,
            part_family=args.part_family,
            confirm=args.confirm,
        )
        return {
            "status": "learned",
            "memory_path": str(mem_path),
            "entries": [e.model_dump(mode="json") for e in entries],
        }, None, False
    if not args.pref_id:
        raise ValueError("pref_id is required when action in {'confirm', 'reject'}")
    target_st = "confirmed" if args.action == "confirm" else "rejected"
    updated = set_preference_status(mem_path, args.pref_id, target_st)
    return {"status": "updated", "entry": updated.model_dump(mode="json")}, None, False


def _handle_generate_feature_graph(
    sandbox: MCPWorkspaceSandbox, args: GenerateFeatureGraphArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    if (args.feature_graph is None) == (args.markdown_brief is None):
        raise ValueError("provide exactly one of feature_graph or markdown_brief")
    pref_mem = default_preference_memory_path(sandbox.workspace_root)
    if args.feature_graph is not None:
        spec = compile_spec_from_feature_graph(
            args.feature_graph,
            project_name=args.project_name,
            drawing_number=args.drawing_number,
            material=args.material,
            include_pro_symbols=args.include_pro_symbols,
            preference_memory_path=pref_mem,
        )
    else:
        assert args.markdown_brief is not None
        task_tmp_id, tmp_dir = sandbox.allocate_task_dir("fgbrief")
        del task_tmp_id
        brief_md = tmp_dir / "brief.md"
        brief_md.write_text(args.markdown_brief, encoding="utf-8")
        spec = compile_spec_from_files(
            [brief_md],
            part_family="custom-profile",
            include_pro_symbols=args.include_pro_symbols,
            preference_memory_path=pref_mem,
        )
    return _handle_generate_mechanical_2d(
        sandbox,
        GenerateMechanical2DArgs(
            spec=spec.model_dump(mode="json"),
            standard_profile=args.standard_profile,
            include_pro_symbols=args.include_pro_symbols,
            include_image_base64=args.include_image_base64,
        ),
    )


def _handle_manage_regressions(
    sandbox: MCPWorkspaceSandbox, args: ManageRegressionsArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    vault = sandbox.workspace_root / ".cad-memory" / "regressions"
    if args.action == "list":
        return {"status": "ok", "vault_dir": str(vault), "cases": list_regression_cases(vault)}, None, False
    if args.action == "replay":
        rep = replay_regression_vault(vault)
        return {"status": "replayed", **rep}, None, not rep["all_passed"]
    if not args.case_id or not args.prompt:
        raise ValueError("case_id and prompt are required when action='record'")
    src_dxf = _resolve_dxf_source(sandbox, args.dxf_path, args.task_id, args.artifact_name)
    recorded = record_regression_case(
        vault,
        case_id=args.case_id,
        title=args.title or args.case_id,
        source_dxf_path=src_dxf,
        prompt=args.prompt,
        candidate_handles=args.candidate_handles,
        region_box=args.region_box,
        structured_ir=args.structured_ir,
        must_reject=args.must_reject,
        expected_post_conditions=args.expected_post_conditions,
        bug_note=args.bug_note,
    )
    return recorded, None, False


def _handle_civil_bridge_calc(
    sandbox: MCPWorkspaceSandbox, args: CivilBridgeCalcArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    from cad_skill_core.civil.bridge_calc import ingest_bridge_survey_data
    from cad_skill_core.civil.bridge_calc_book import (
        compute_independent_hand_cross_check,
        generate_bridge_calculation_book_markdown,
    )
    from cad_skill_core.civil.bridge_checks import lookup_bar_or_tendon

    mode = args.mode
    p = args.params
    if mode == "full":
        result: Any = run_full_bridge_calculation(p)
    elif mode == "wenchang":
        result = run_full_bridge_calculation({"preset": "wenchang", **p})
    elif mode == "calc_book":
        calc_rep = run_full_bridge_calculation(p or {"preset": "wenchang"})
        md_text = generate_bridge_calculation_book_markdown(calc_rep)
        result = {
            "calculation_status": calc_rep["calculation_status"],
            "graduation_readiness_status": calc_rep["graduation_readiness_status"],
            "independent_cross_check": calc_rep["independent_cross_check"],
            "defense_verification_table": calc_rep["defense_verification_table"],
            "markdown": md_text,
        }
    elif mode == "thesis_docx":
        from cad_skill_core.civil.bridge_figures import generate_bridge_calculation_figures
        from cad_skill_core.civil.bridge_thesis_docx import generate_college_graduation_thesis_docx

        task_id, task_dir = sandbox.allocate_task_dir("thesis")
        mem_dir = sandbox.workspace_root / ".cad-memory"
        eff_p = dict(p or {"preset": "wenchang"})
        if mem_dir.is_dir() and "template_memory_dir" not in eff_p:
            eff_p["template_memory_dir"] = str(mem_dir)
        calc_rep = run_full_bridge_calculation(eff_p)
        figs = generate_bridge_calculation_figures(calc_rep, task_dir / "figures")
        result = {
            "task_id": task_id,
            **generate_college_graduation_thesis_docx(
                calc_rep,
                task_dir / "graduation_thesis.docx",
                figures_summary=figs,
                student_metadata=eff_p.get("student_metadata"),
                thesis_template=eff_p.get("thesis_template") or (mem_dir if mem_dir.is_dir() else None),
            ),
        }
    elif mode == "calc_figures":
        from cad_skill_core.civil.bridge_figures import generate_bridge_calculation_figures

        task_id, task_dir = sandbox.allocate_task_dir("bfigs")
        calc_rep = run_full_bridge_calculation(p or {"preset": "wenchang"})
        result = {"task_id": task_id, **generate_bridge_calculation_figures(calc_rep, task_dir / "figures")}
    elif mode == "fem_compare":
        base_p = {"preset": "wenchang", **p} if "preset" not in p else p
        calc_rep = run_full_bridge_calculation(base_p)
        result = calc_rep["defense_verification_table"]
    elif mode == "change_impact":
        from cad_skill_core.civil.bridge_change_impact import compute_design_change_impact

        before_cfg = dict(p.get("before_params") or {"preset": "wenchang"})
        after_cfg = dict(p.get("after_params") or {"preset": "wenchang", **{k: v for k, v in p.items() if k not in {"before_params", "after_params"}}})
        result = compute_design_change_impact(
            run_full_bridge_calculation(before_cfg),
            run_full_bridge_calculation(after_cfg),
        )
    elif mode == "cross_check":
        calc_rep = run_full_bridge_calculation(p or {"preset": "wenchang"})
        result = compute_independent_hand_cross_check(calc_rep)
    elif mode == "lookup_bar":
        bid = args.bar_id or p.get("bar_id")
        if not bid:
            raise ValueError("bar_id is required when mode='lookup_bar'")
        clean_p = {k: v for k, v in p.items() if k != "bar_id"}
        calc_rep = run_full_bridge_calculation(clean_p or {"preset": "wenchang"})
        result = lookup_bar_or_tendon(calc_rep, str(bid))
    elif mode == "survey_ingest":
        result = ingest_bridge_survey_data(p.get("survey_data", p))
    elif mode == "hydrology":
        result = compute_hydrology_and_span(**p)
    elif mode == "polygon_section":
        if "vertices" not in p:
            raise ValueError("params['vertices'] is required when mode='polygon_section'")
        result = compute_polygon_section_properties(p["vertices"])
    elif mode == "t_girder_section":
        result = compute_t_girder_section(**p)
    elif mode == "lateral_distribution":
        result = compute_lateral_distribution(**p)
    elif mode == "superstructure_envelopes":
        result = compute_superstructure_envelopes(**p)
    elif mode == "prestress":
        result = compute_prestress_estimation_and_loss(**p)
    elif mode == "cap_beam_1d":
        result = solve_continuous_cap_beam_1d(**p)
    elif mode == "consistency_audit":
        result = audit_bridge_design_consistency(p)
    else:
        resolved_table = sandbox.resolve_readable_file(args.table_path) if args.table_path else None
        result = ingest_and_audit_calc_table(
            table_text=args.table_text or p.get("table_text"),
            file_path=resolved_table,
        )
    return {"domain": "civil_bridge_calc", "mode": mode, "result": result}, None, False


def _handle_generate_civil_bridge(
    sandbox: MCPWorkspaceSandbox, args: GenerateCivilBridgeArgs
) -> tuple[dict[str, Any], Path | None, bool]:
    task_id, task_dir = sandbox.allocate_task_dir("civil")
    out_dir = task_dir / "output"
    clean_stem = validate_safe_single_name(args.stem, label="stem") if args.stem else None
    bundle = generate_civil_bridge_bundle(
        args.drawing_type,
        args.spec,
        out_dir,
        stem=clean_stem,
        render_lock=sandbox.render_lock,
    )
    png_path = bundle.pop("preview_png_path")
    payload = {
        "task_id": task_id,
        "domain": "civil_bridge",
        **bundle,
    }
    sandbox.write_task_metadata(task_id, payload)
    return (
        payload,
        (png_path if args.include_image_base64 and png_path.is_file() else None),
        bundle["status"] == "failed",
    )


TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        "cad_doctor",
        "Check CAD Python runtime dependencies, Chinese font coverage, and local AutoCAD readiness.",
        DoctorArgs,
        _handle_doctor,
    ),
    ToolDefinition(
        "cad_compile_spec",
        "Compile engineering project briefs, manuals, or CSV/DOCX/PDF tables into a validated DrawingSpec and detect missing/conflicting parameters.",
        CompileSpecArgs,
        _handle_compile_spec,
    ),
    ToolDefinition(
        "cad_validate_spec",
        "Validate a 2D mechanical DrawingSpec JSON object or file for provenance and geometric schema compliance.",
        ValidateSpecArgs,
        _handle_validate_spec,
    ),
    ToolDefinition(
        "cad_learn_standard",
        "Extract a validated DrawingStandard profile (layers, lineweights, text/dimension styles, sheet size) from a sample DXF.",
        LearnStandardArgs,
        _handle_learn_standard,
    ),
    ToolDefinition(
        "cad_generate_mechanical_2d",
        "Generate an audited GB/T 2D mechanical drawing (DXF, SVG, PNG, audit.json) with automatic collision repair and font verification.",
        GenerateMechanical2DArgs,
        _handle_generate_mechanical_2d,
        meta={"ui": {"resourceUri": MCP_APP_RESOURCE_URI}},
    ),
    ToolDefinition(
        "cad_generate_electrical_sld",
        "Generate an audited electrical main single-line diagram (single/split/double busbar) as DXF, SVG, PDF, and PNG.",
        GenerateElectricalSLDArgs,
        _handle_generate_electrical_sld,
        meta={"ui": {"resourceUri": MCP_APP_RESOURCE_URI}},
    ),
    ToolDefinition(
        "cad_generate_pavement",
        "Generate an audited road pavement structure cross-section CAD drawing with native HATCH and DIMENSION entities.",
        GeneratePavementArgs,
        _handle_generate_pavement,
        meta={"ui": {"resourceUri": MCP_APP_RESOURCE_URI}},
    ),
    ToolDefinition(
        "cad_inventory_dxf",
        "List editable modelspace entity handles, types, layers, coordinates, and SHA-256 hash for a DXF file.",
        InventoryDXFArgs,
        _handle_inventory_dxf,
    ),
    ToolDefinition(
        "cad_revise_dxf",
        "Apply a hash-bound, atomic incremental revision plan to an existing DXF (supports dry_run simulation).",
        ReviseDXFArgs,
        _handle_revise_dxf,
        meta={"ui": {"resourceUri": MCP_APP_RESOURCE_URI}},
    ),
    ToolDefinition(
        "cad_preview_and_diff",
        "Render high-DPI PNG/PDF crops of a DXF or matched before/after local revision diff views.",
        PreviewAndDiffArgs,
        _handle_preview_and_diff,
        meta={"ui": {"resourceUri": MCP_APP_RESOURCE_URI}},
    ),
    ToolDefinition(
        "cad_generate_3d",
        "Generate and verify watertight 3D CAD models (.stl, .obj, .glb, model3d.dxf, iso.png, audit-3d.json) and optional 3D->2D GB/T sheet.",
        Generate3DArgs,
        _handle_generate_3d,
        meta={"ui": {"resourceUri": MCP_APP_RESOURCE_URI}},
    ),
    ToolDefinition(
        "cad_project_3d_to_2d",
        "Project a Model3DSpec into an orthographic/sectional multi-view 2D DrawingSpec ready for 2D drafting.",
        Project3DTo2DArgs,
        _handle_project_3d_to_2d,
    ),
    ToolDefinition(
        "cad_list_drivers",
        "Probe local CAD software matrix: AutoCAD 2021-2026 (R24.0-R25.1), ZWCAD (中望), GstarCAD (浩辰), CAXA, and ODA File Converter.",
        ListDriversArgs,
        _handle_list_drivers,
    ),
    ToolDefinition(
        "cad_verify_roundtrip",
        "Verify lossless DWG <-> DXF structural equivalence (layers, user blocks, layouts, styles, entity counts, and extents).",
        VerifyRoundTripArgs,
        _handle_verify_roundtrip,
    ),
    ToolDefinition(
        "cad_compile_3d_spec",
        "Compile conversational Chinese/English descriptions or documents (.md/.docx/.pdf/.csv) into a validated 3D Model3DSpec.",
        Compile3DSpecArgs,
        _handle_compile_3d_spec,
    ),
    ToolDefinition(
        "cad_open_viewer",
        "Open the interactive CAD Blueprint & 3D Review Pane (supports blue-dashed marquee region commenting & revision).",
        OpenViewerArgs,
        _handle_open_viewer,
        meta={
            "ui": {"resourceUri": MCP_APP_RESOURCE_URI},
            "com.google.antigravity/ui": {
                "entrypoints": [
                    {"type": "aux_pane", "title": "CAD 交互审图台"},
                ]
            },
        },
    ),
    ToolDefinition(
        "cad_app_load_scene",
        "App-only internal scene loader returning 2D vector primitives and 3D OBJ mesh for the MCP App canvas.",
        AppLoadSceneArgs,
        _handle_app_load_scene,
        meta={"ui": {"visibility": ["app"]}},
    ),
    ToolDefinition(
        "cad_score_drawing",
        "Score a 2D CAD DXF drawing on the 100-point GB/T & ISO 5-dimension engineering rubric (supports cad-1000-hours rubrics.json criteria).",
        ScoreDrawingArgs,
        _handle_score_drawing,
    ),
    ToolDefinition(
        "cad_run_cad1k_eval",
        "Run the video-free cad-1000-hours offline engineering benchmark suite and return 100-point rubric scorecards.",
        RunCAD1KEvalArgs,
        _handle_run_cad1k_eval,
    ),
    ToolDefinition(
        "cad_revise_by_nl",
        "Apply a conversational natural-language marquee region revision or inspection pin onto an existing DXF drawing.",
        ReviseByNLArgs,
        _handle_revise_by_nl,
        meta={"ui": {"resourceUri": MCP_APP_RESOURCE_URI}},
    ),
    ToolDefinition(
        "cad_audit_legacy_drawing",
        "Audit any arbitrary external/legacy DXF drawing for tampered dimensions, exploded fake dimensions, open contour gaps, duplicates, degenerate entities, and text collisions (with optional auto_clean).",
        AuditLegacyDrawingArgs,
        _handle_audit_legacy_drawing,
    ),
    ToolDefinition(
        "cad_apply_revision_ir",
        "Execute a typed StructuredRevisionIR ('cad-revision-ir/v2') supporting add_circle_hole, add_bolt_circle, chamfer_corner, fillet_corner, and mirror_entities under strict compare_untouched guardrails.",
        ApplyRevisionIRArgs,
        _handle_apply_revision_ir,
        meta={"ui": {"resourceUri": MCP_APP_RESOURCE_URI}},
    ),
    ToolDefinition(
        "cad_vector_diff",
        "Compute handle-level red/green vector primitive overlay diff between before and after DXF drawings.",
        VectorDiffArgs,
        _handle_vector_diff,
    ),
    ToolDefinition(
        "cad_manage_preferences",
        "List, teach, learn from sample DXFs, confirm, or reject project-level drafting preferences (hole callout style, leader side, default Ra, notes) with provenance.",
        ManagePreferencesArgs,
        _handle_manage_preferences,
    ),
    ToolDefinition(
        "cad_generate_feature_graph",
        "Generate an audited non-standard 2D/3-view GB/T drawing from a composable FeatureGraphSpec ('cad-feature-graph/v1': polygon/rect/stadium + corner mods + holes/grids/slots).",
        GenerateFeatureGraphArgs,
        _handle_generate_feature_graph,
        meta={"ui": {"resourceUri": MCP_APP_RESOURCE_URI}},
    ),
    ToolDefinition(
        "cad_manage_regressions",
        "Record failed/corrected DXF revision cases + post-conditions into a permanent regression vault and replay all cases automatically.",
        ManageRegressionsArgs,
        _handle_manage_regressions,
    ),
    ToolDefinition(
        "cad_civil_bridge_calc",
        "Zero-dependency highway bridge & civil structural calculation and QA engine (hydrology, polygonal/T-girder section properties, Lever/Courbon lateral distribution, Highway-I M/Q envelopes, prestress loss, 1D cap-beam FEM, and cross-sheet consistency/table audit).",
        CivilBridgeCalcArgs,
        _handle_civil_bridge_calc,
    ),
    ToolDefinition(
        "cad_generate_civil_bridge",
        "Generate audited Civil & Bridge Engineering CAD drawings (bridge_elevation, t_girder_section, tendon_layout, pier_and_abutment, mechanics_diagrams) with native ARCHTICK dimensions, pile S-breaks, ground lines, and 100-point rubric score.",
        GenerateCivilBridgeArgs,
        _handle_generate_civil_bridge,
        meta={"ui": {"resourceUri": MCP_APP_RESOURCE_URI}},
    ),
)

TOOL_BY_NAME: dict[str, ToolDefinition] = {tool.name: tool for tool in TOOLS}


def list_mcp_tools() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for tool in TOOLS:
        entry: dict[str, Any] = {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_model.model_json_schema(),
        }
        if tool.meta is not None:
            entry["_meta"] = tool.meta
        items.append(entry)
    return items


def call_mcp_tool(
    sandbox: MCPWorkspaceSandbox,
    name: str,
    arguments: dict[str, Any] | None,
) -> dict[str, Any]:
    if name not in TOOL_BY_NAME:
        raise KeyError(f"unknown tool: {name}")
    tool = TOOL_BY_NAME[name]
    try:
        validated_args = tool.input_model.model_validate(arguments or {})
        payload, image_path, is_error = tool.handler(sandbox, validated_args)
    except (MCPSandboxError, ValueError, TypeError) as exc:
        err_payload = {"status": "error", "error": str(exc), "error_type": type(exc).__name__}
        return {
            "content": [{"type": "text", "text": json.dumps(err_payload, ensure_ascii=False, indent=2)}],
            "structuredContent": err_payload,
            "isError": True,
        }
    except Exception as exc:
        err_payload = {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "error_type": type(exc).__name__,
        }
        return {
            "content": [{"type": "text", "text": json.dumps(err_payload, ensure_ascii=False, indent=2)}],
            "structuredContent": err_payload,
            "isError": True,
        }

    content: list[dict[str, Any]] = [
        {"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}
    ]
    if image_path is not None and image_path.is_file():
        b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        content.append({"type": "image", "data": b64, "mimeType": "image/png"})
    return {
        "content": content,
        "structuredContent": payload,
        "isError": is_error,
    }


def list_mcp_resources() -> list[dict[str, Any]]:
    return [
        {
            "uri": MCP_APP_RESOURCE_URI,
            "name": "CAD Interactive Blueprint & 3D Review App",
            "description": "Interactive 2D GB/T blueprint viewer with blue-dashed marquee region commenting & 3D crease-edge viewport.",
            "mimeType": MCP_APP_MIME_TYPE,
            "_meta": {"ui": {"csp": {}}},
        },
        {
            "uri": "cad://standards/gb-mechanical",
            "name": "GB/T Mechanical Default Profile",
            "description": "Default configurable GB/T layer, font, dimension, and sheet standard YAML.",
            "mimeType": "application/x-yaml",
        },
        {
            "uri": "cad://standards/presets",
            "name": "Built-in Industry CAD Standard Presets",
            "description": "Catalog of 4 switchable industry CAD standard profiles (GB/T Mechanical, Heavy Machinery, Civil/Municipal, ISO International).",
            "mimeType": "application/json",
        },
        {
            "uri": "cad://schemas/drawing-spec",
            "name": "DrawingSpec JSON Schema",
            "description": "Strict JSON Schema for 2D mechanical DrawingSpec v1.0.",
            "mimeType": "application/schema+json",
        },
        {
            "uri": "cad://schemas/model-3d-spec",
            "name": "Model3DSpec JSON Schema",
            "description": "Strict JSON Schema for parametric 3D mechanical Model3DSpec v1.0.",
            "mimeType": "application/schema+json",
        },
        {
            "uri": "cad://revision/operations",
            "name": "Supported Incremental Revision Operations",
            "description": "Discoverable catalog of verified DXF revision primitives and required fields.",
            "mimeType": "application/json",
        },
        {
            "uri": "cad://schemas/revision-ir",
            "name": "StructuredRevisionIR v2 JSON Schema",
            "description": "Strict JSON Schema for LLM/Agent StructuredRevisionIR ('cad-revision-ir/v2') feature primitives.",
            "mimeType": "application/schema+json",
        },
    ]


def read_mcp_resource(sandbox: MCPWorkspaceSandbox, uri: str) -> dict[str, Any]:
    if uri == MCP_APP_RESOURCE_URI:
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": MCP_APP_MIME_TYPE,
                    "text": get_mcp_app_viewer_html(),
                    "_meta": {"ui": {"csp": {}}},
                }
            ]
        }
    if uri == "cad://standards/gb-mechanical":
        path = default_runtime_root() / "knowledge" / "standards" / "gb-mechanical.yaml"
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/x-yaml",
                    "text": path.read_text(encoding="utf-8"),
                }
            ]
        }
    if uri == "cad://standards/presets":
        text = json.dumps({"standards": list_builtin_standards()}, ensure_ascii=False, indent=2)
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": text,
                }
            ]
        }
    if uri == "cad://schemas/drawing-spec":
        schema_text = json.dumps(DrawingSpec.model_json_schema(), ensure_ascii=False, indent=2)
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/schema+json",
                    "text": schema_text,
                }
            ]
        }
    if uri == "cad://schemas/model-3d-spec":
        schema_text = json.dumps(Model3DSpec.model_json_schema(), ensure_ascii=False, indent=2)
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/schema+json",
                    "text": schema_text,
                }
            ]
        }
    if uri == "cad://revision/operations":
        text = json.dumps(operation_catalog(), ensure_ascii=False, indent=2)
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": text,
                }
            ]
        }
    if uri == "cad://schemas/revision-ir":
        schema_text = json.dumps(StructuredRevisionIR.model_json_schema(), ensure_ascii=False, indent=2)
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/schema+json",
                    "text": schema_text,
                }
            ]
        }
    if uri.startswith("cad://tasks/") and uri.endswith("/audit"):
        task_id = uri.removeprefix("cad://tasks/").removesuffix("/audit").strip("/")
        audit_file = sandbox.resolve_task_artifact(task_id, "audit.json")
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": audit_file.read_text(encoding="utf-8"),
                }
            ]
        }
    raise KeyError(f"unknown resource URI: {uri}")


def list_mcp_prompts() -> list[dict[str, Any]]:
    return [
        {
            "name": "draft-from-brief",
            "description": "Standard workflow to compile an engineering brief, clarify missing/conflicting dimensions, and generate an audited 2D CAD drawing.",
            "arguments": [
                {"name": "brief", "description": "Raw user engineering brief or requirements.", "required": True}
            ],
        },
        {
            "name": "safe-dxf-revision",
            "description": "Zero-corruption workflow to inspect handles, simulate a dry-run revision, apply changes, and review before/after crops.",
            "arguments": [
                {"name": "dxf_path", "description": "Target DXF path.", "required": True},
                {"name": "change_request", "description": "Requested engineering modification.", "required": True},
            ],
        },
        {
            "name": "visual-self-check-and-revise",
            "description": "Closed-loop visual quality self-inspection workflow after CAD generation.",
            "arguments": [
                {"name": "task_id", "description": "Generated task_id to visually inspect and refine.", "required": True},
            ],
        },
    ]


def get_mcp_prompt(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = arguments or {}
    if name == "draft-from-brief":
        brief = str(args.get("brief", "")).strip()
        if not brief:
            raise ValueError("missing required prompt argument: brief")
        return {
            "description": "Draft audited 2D CAD from engineering brief",
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            "请按以下受控工程制图流程执行：\n"
                            "1. 调用 `cad_compile_spec` 解析下方项目需求；\n"
                            "2. 若返回 `missing` 或 `conflicts` 非空，立即列出缺失尺寸或冲突项向用户确认，严禁臆造工程尺寸；\n"
                            "3. 参数完整后调用 `cad_generate_mechanical_2d`（设置 `include_image_base64=true`）生成图纸并审阅预览图与 `audit_summary`。\n\n"
                            f"项目需求：\n{brief}"
                        ),
                    },
                }
            ],
        }
    if name == "safe-dxf-revision":
        dxf_path = str(args.get("dxf_path", "")).strip()
        change_request = str(args.get("change_request", "")).strip()
        if not dxf_path or not change_request:
            raise ValueError("missing required prompt arguments: dxf_path, change_request")
        return {
            "description": "Safe hash-locked incremental DXF revision",
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            f"目标图纸：{dxf_path}\n修改要求：{change_request}\n\n"
                            "请严格按以下步骤操作：\n"
                            "1. 读取 `cad://revision/operations` 确认可用原语；\n"
                            "2. 调用 `cad_inventory_dxf` 获取目标图元 handle 与当前 `sha256`；\n"
                            "3. 先以 `dry_run=true` 调用 `cad_revise_dxf` 验证计划无误；\n"
                            "4. 再以 `dry_run=false` 正式生成，并调用 `cad_preview_and_diff` 进行修改前后对比复核。"
                        ),
                    },
                }
            ],
        }
    if name == "visual-self-check-and-revise":
        task_id = str(args.get("task_id", "")).strip()
        if not task_id:
            raise ValueError("missing required prompt argument: task_id")
        return {
            "description": "Closed-loop visual self-check and surgical DXF refinement",
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            f"请对任务 `{task_id}` 产出的图纸执行闭环视觉复检：\n"
                            "1. 读取 `cad://tasks/" + task_id + "/audit` 检查是否存在任何 warning/info 假设；\n"
                            "2. 调用 `cad_inventory_dxf(task_id='" + task_id + "')` 获取关键标注与轮廓包围盒；\n"
                            "3. 调用 `cad_preview_and_diff`（开启 `include_image_base64=true`）放大审阅标注密集区域；\n"
                            "4. 如需微调尺寸文字位置或轮廓，先 `dry_run=true` 再正式执行 `cad_revise_dxf`。"
                        ),
                    },
                }
            ],
        }
    raise KeyError(f"unknown prompt: {name}")
