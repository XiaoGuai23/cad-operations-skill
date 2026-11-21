"""MCP Tool, Resource, and Prompt registry backed by verified 2D CAD pipelines."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from cad_skill_core.documents.spec_compiler import (
    PartFamily,
    compile_spec_from_files,
)
from cad_skill_core.environment import check_environment
from cad_skill_core.pipeline import PipelineOptions, run_task
from cad_skill_core.revision.common import sha256_file
from cad_skill_core.revision.local_review import review_files
from cad_skill_core.revision.operations import operation_catalog
from cad_skill_core.revision.preview import preview_file
from cad_skill_core.revision.workflow import apply_revision, inventory_drawing
from cad_skill_core.schema.io import load_spec, save_spec
from cad_skill_core.schema.models import DrawingSpec
from cad_skill_core.standards.sample_learner import (
    learn_standard_from_dxf,
    save_standard_yaml,
)

from .industry_adapters import (
    build_auto_style_font_map,
    generate_electrical_sld_bundle,
    generate_pavement_bundle,
)
from .sandbox import MCPSandboxError, MCPWorkspaceSandbox, default_runtime_root


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
    sample_dxf_path: str = Field(description="Path to a sample DXF file inside allowed roots.")
    profile_name: str | None = Field(default=None, description="Optional custom profile name.")


class GenerateMechanical2DArgs(StrictInputModel):
    spec: dict[str, Any] | None = Field(default=None, description="Validated DrawingSpec JSON object.")
    spec_path: str | None = Field(default=None, description="Path to a DrawingSpec JSON file.")
    markdown_brief: str | None = Field(
        default=None,
        description="Optional inline engineering brief to auto-compile before generating.",
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


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[StrictInputModel]
    handler: Callable[[MCPWorkspaceSandbox, Any], tuple[dict[str, Any], Path | None, bool]]


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
    spec = compile_spec_from_files(
        resolved_files,
        part_family=args.part_family,
        title_block_language=args.language,
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
    sample = sandbox.resolve_readable_file(args.sample_dxf_path)
    task_id, task_dir = sandbox.allocate_task_dir("standard")
    out_dir = task_dir / "output"
    out_dir.mkdir()
    learned = learn_standard_from_dxf(sample, profile_name=args.profile_name)
    yaml_path = save_standard_yaml(learned.standard, out_dir / "learned-standard.yaml")
    payload = {
        "task_id": task_id,
        "status": "learned",
        "profile_name": learned.standard.name,
        "detected_sheet": learned.detected_sheet,
        "sample_extents_mm": learned.sample_extents_mm,
        "matched_layers": learned.matched_layers,
        "artifacts": [
            {
                "name": yaml_path.name,
                "path": str(yaml_path),
                "size": yaml_path.stat().st_size,
                "sha256": sha256_file(yaml_path),
            }
        ],
        "standard": learned.standard.model_dump(mode="json"),
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
    png_path = task_dir / "output" / "drawing.png"
    preview_png = png_path if png_path.is_file() and args.include_image_base64 else None
    payload = {
        "task_id": task_id,
        "domain": "mechanical_2d",
        "status": result.status,
        "audit_summary": audit.get("summary", {}),
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
    bundle = generate_electrical_sld_bundle(
        args.spec,
        out_dir,
        stem=args.stem,
        expect_text=expectations,
        render_lock=sandbox.render_lock,
    )
    png_path = bundle.pop("preview_png_path")
    payload = {
        "task_id": task_id,
        "domain": "electrical_sld",
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
    payload = {
        "task_id": task_id,
        "domain": "pavement",
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
    artifacts: list[dict[str, Any]] = []
    if not args.dry_run and rev_result.get("output_path"):
        out_dxf = Path(rev_result["output_path"])
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
    ),
    ToolDefinition(
        "cad_generate_electrical_sld",
        "Generate an audited electrical main single-line diagram (single/split/double busbar) as DXF, SVG, PDF, and PNG.",
        GenerateElectricalSLDArgs,
        _handle_generate_electrical_sld,
    ),
    ToolDefinition(
        "cad_generate_pavement",
        "Generate an audited road pavement structure cross-section CAD drawing with native HATCH and DIMENSION entities.",
        GeneratePavementArgs,
        _handle_generate_pavement,
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
    ),
    ToolDefinition(
        "cad_preview_and_diff",
        "Render high-DPI PNG/PDF crops of a DXF or matched before/after local revision diff views.",
        PreviewAndDiffArgs,
        _handle_preview_and_diff,
    ),
)

TOOL_BY_NAME: dict[str, ToolDefinition] = {tool.name: tool for tool in TOOLS}


def list_mcp_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_model.model_json_schema(),
        }
        for tool in TOOLS
    ]


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
            "uri": "cad://standards/gb-mechanical",
            "name": "GB/T Mechanical Default Profile",
            "description": "Default configurable GB/T layer, font, dimension, and sheet standard YAML.",
            "mimeType": "application/x-yaml",
        },
        {
            "uri": "cad://schemas/drawing-spec",
            "name": "DrawingSpec JSON Schema",
            "description": "Strict JSON Schema for 2D mechanical DrawingSpec v1.0.",
            "mimeType": "application/schema+json",
        },
        {
            "uri": "cad://revision/operations",
            "name": "Supported Incremental Revision Operations",
            "description": "Discoverable catalog of verified DXF revision primitives and required fields.",
            "mimeType": "application/json",
        },
    ]


def read_mcp_resource(sandbox: MCPWorkspaceSandbox, uri: str) -> dict[str, Any]:
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
    raise KeyError(f"unknown prompt: {name}")
