"""Stable command-line interface for CAD task validation and execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from .pipeline import PipelineResult


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cad-skill")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate-spec", help="validate one drawing specification")
    validate.add_argument("path", type=Path)
    validate.add_argument("--json", action="store_true", dest="as_json")

    inventory = commands.add_parser("inventory", help="list editable modelspace objects and the current DXF hash")
    inventory.add_argument("source", type=Path)
    inventory.add_argument("--json", action="store_true", dest="as_json")

    revise = commands.add_parser("revise", help="apply a verified incremental DXF revision plan")
    revise.add_argument("source", type=Path)
    revise.add_argument("plan", type=Path)
    revise.add_argument("--out", type=Path, required=True, dest="output_dir")
    revise.add_argument("--dry-run", action="store_true")
    revise.add_argument("--ledger", type=Path)
    revise.add_argument("--json", action="store_true", dest="as_json")

    review = commands.add_parser("review", help="read-only neighboring-change checks and matched before/after views")
    review.add_argument("before", type=Path)
    review.add_argument("after", type=Path)
    review.add_argument("--out", type=Path, required=True, dest="output_dir")
    review.add_argument("--box", nargs=4, type=float, dest="bounds")
    review.add_argument("--padding", type=float, default=5.)
    review.add_argument("--tolerance", type=float, default=1e-6)
    review.add_argument("--json", action="store_true", dest="as_json")

    doctor = commands.add_parser("doctor", help="report CAD capabilities and reuse unchanged native failure evidence")
    doctor.add_argument("--cache", type=Path)
    doctor.add_argument("--autocad-path", type=Path)
    doctor.add_argument("--refresh", action="store_true")
    doctor.add_argument("--skip-native", action="store_true")
    doctor.add_argument("--json", action="store_true", dest="as_json")

    comments = commands.add_parser('check-comments', help='check all active comments and render per-comment evidence')
    comments.add_argument('before', type=Path)
    comments.add_argument('after', type=Path)
    comments.add_argument('ledger', type=Path)
    comments.add_argument('--out', type=Path, required=True, dest='output_dir')
    comments.add_argument('--json', action='store_true', dest='as_json')

    related = commands.add_parser('check-drawing-set', help='read-only checks of explicit cross-drawing parameters')
    related.add_argument('manifest', type=Path)
    related.add_argument('--json', action='store_true', dest='as_json')

    reference = commands.add_parser('compare-reference', help='compare explicit sample features without changing either drawing')
    reference.add_argument('current', type=Path)
    reference.add_argument('reference', type=Path)
    reference.add_argument('mapping', type=Path)
    reference.add_argument('--out', type=Path, required=True, dest='output_dir')
    reference.add_argument('--json', action='store_true', dest='as_json')

    preview = commands.add_parser('preview', help='render DXF review images and an optional independent vector PDF')
    preview.add_argument('source', type=Path)
    preview.add_argument('--out', type=Path, required=True, dest='output_dir')
    preview.add_argument('--box', nargs=4, type=float, action='append', dest='regions')
    preview.add_argument('--font-map', type=Path)
    preview.add_argument('--pdf', action='store_true')
    preview.add_argument('--cache', type=Path)
    preview.add_argument('--json', action='store_true', dest='as_json')

    operations = commands.add_parser('operations', help='discover supported revision operations and required fields')
    operations.add_argument('--json', action='store_true', dest='as_json')

    compile_cmd = commands.add_parser('compile-spec', help='compile engineering documents/tables into a validated DrawingSpec')
    compile_cmd.add_argument('inputs', nargs='+', type=Path)
    compile_cmd.add_argument('--out', type=Path, required=True, dest='output_path')
    compile_cmd.add_argument(
        '--part-family',
        choices=(
            'auto',
            'flange',
            'stepped-shaft',
            'slotted-bracket',
            'rounded-rect',
            'spur-gear',
            'bearing-cap',
            'ribbed-bracket',
            'custom-profile',
        ),
        default='auto',
    )
    compile_cmd.add_argument('--lang', choices=('auto', 'en', 'zh'), default='auto')
    compile_cmd.add_argument('--json', action='store_true', dest='as_json')

    learn_cmd = commands.add_parser('learn-standard', help='extract a validated DrawingStandard YAML profile from a sample DXF')
    learn_cmd.add_argument('sample', type=Path)
    learn_cmd.add_argument('--out', type=Path, required=True, dest='output_path')
    learn_cmd.add_argument('--name', default=None, dest='profile_name')
    learn_cmd.add_argument('--json', action='store_true', dest='as_json')

    mcp_stdio = commands.add_parser('mcp-stdio', help='run standards-compliant MCP JSON-RPC server over stdio')
    mcp_stdio.add_argument('--workspace', type=Path, default=None, dest='workspace_root')

    mcp_http = commands.add_parser('mcp-http', help='run Streamable HTTP MCP server and Web Auto-Drafting FastAPI gateway')
    mcp_http.add_argument('--host', default='127.0.0.1')
    mcp_http.add_argument('--port', type=int, default=8765)
    mcp_http.add_argument('--workspace', type=Path, default=None, dest='workspace_root')

    val3d = commands.add_parser('validate-3d-spec', help='validate one 3D mechanical modeling specification')
    val3d.add_argument('path', type=Path)
    val3d.add_argument('--json', action='store_true', dest='as_json')

    gen3d = commands.add_parser('generate-3d', help='generate and verify watertight 3D CAD models (.stl/.obj/.glb/.dxf) and optional 2D sheet')
    gen3d.add_argument('spec_path', type=Path)
    gen3d.add_argument('--out', type=Path, required=True, dest='output_dir')
    gen3d.add_argument('--with-2d-sheet', action='store_true', dest='with_2d_sheet')
    gen3d.add_argument('--run-native-cad', action='store_true', dest='run_native_cad')
    gen3d.add_argument('--driver', choices=('auto', 'autocad', 'zwcad', 'gstarcad'), default='auto', dest='driver_preference')
    gen3d.add_argument('--segments', type=int, default=64)
    gen3d.add_argument('--json', action='store_true', dest='as_json')

    drivers_cmd = commands.add_parser('drivers', help='probe multi-version AutoCAD (2021-2026) and domestic CAD (ZWCAD/GstarCAD/CAXA/ODA) drivers')
    drivers_cmd.add_argument('--autocad-path', type=Path, default=None)
    drivers_cmd.add_argument('--zwcad-path', type=Path, default=None)
    drivers_cmd.add_argument('--gstarcad-path', type=Path, default=None)
    drivers_cmd.add_argument('--caxa-path', type=Path, default=None)
    drivers_cmd.add_argument('--oda-path', type=Path, default=None)
    drivers_cmd.add_argument('--json', action='store_true', dest='as_json')

    rt_cmd = commands.add_parser('verify-roundtrip', help='verify lossless DWG <-> DXF structural equivalence across layers, blocks, layouts, styles, and entities')
    rt_cmd.add_argument('before', type=Path)
    rt_cmd.add_argument('after', type=Path)
    rt_cmd.add_argument('--out', type=Path, default=None, dest='output_report')
    rt_cmd.add_argument('--tolerance', type=float, default=0.5, dest='bbox_tolerance')
    rt_cmd.add_argument('--json', action='store_true', dest='as_json')

    score_cmd = commands.add_parser('score-drawing', help='evaluate a DXF drawing on the 100-point engineering rubric (and optional cad-1000-hours rubrics.json)')
    score_cmd.add_argument('source', type=Path)
    score_cmd.add_argument('--audit-json', type=Path, default=None, dest='audit_json')
    score_cmd.add_argument('--rubrics', type=Path, default=None, dest='rubrics_path')
    score_cmd.add_argument('--json', action='store_true', dest='as_json')

    cad1k_cmd = commands.add_parser('benchmark-cad1k', help='run video-free cad-1000-hours offline engineering benchmark and 100-point rubric evaluation')
    cad1k_cmd.add_argument('--out', type=Path, default=Path('cad1k-eval-output'), dest='output_dir')
    cad1k_cmd.add_argument('--bundle', type=Path, action='append', default=None, dest='bundle_dirs')
    cad1k_cmd.add_argument('--standard-profile', default='gb-mechanical', dest='standard_profile')
    cad1k_cmd.add_argument('--json', action='store_true', dest='as_json')

    pref_cmd = commands.add_parser('preferences', help='list, teach, learn, or confirm project-level drafting preferences')
    pref_cmd.add_argument('action', choices=('list', 'teach', 'learn-sample', 'confirm', 'reject'))
    pref_cmd.add_argument('--memory', type=Path, default=Path('.cad-memory/project-preferences.json'), dest='memory_path')
    pref_cmd.add_argument('--text', default=None)
    pref_cmd.add_argument('--sample', type=Path, default=None)
    pref_cmd.add_argument('--pref-id', default=None, dest='pref_id')
    pref_cmd.add_argument('--part-family', default='auto', dest='part_family')
    pref_cmd.add_argument('--json', action='store_true', dest='as_json')

    rec_reg = commands.add_parser('record-regression', help='archive a DXF revision case + post-conditions into the permanent regression vault')
    rec_reg.add_argument('source', type=Path)
    rec_reg.add_argument('--case-id', required=True, dest='case_id')
    rec_reg.add_argument('--title', required=True)
    rec_reg.add_argument('--prompt', required=True)
    rec_reg.add_argument('--vault', type=Path, default=Path('tests/regressions'), dest='vault_dir')
    rec_reg.add_argument('--must-reject', action='store_true', dest='must_reject')
    rec_reg.add_argument('--json', action='store_true', dest='as_json')

    run_reg = commands.add_parser('run-regressions', help='replay all recorded regression cases and verify post-conditions')
    run_reg.add_argument('--vault', type=Path, default=Path('tests/regressions'), dest='vault_dir')
    run_reg.add_argument('--json', action='store_true', dest='as_json')

    bcalc = commands.add_parser('bridge-calc', help='run zero-dependency highway bridge calculations, ULS/SLS checks, calc book, thesis docx, figures, FEM cross-check, or change impact')
    bcalc.add_argument(
        '--mode',
        choices=(
            'full',
            'wenchang',
            'calc-book',
            'thesis-docx',
            'calc-figures',
            'fem-compare',
            'change-impact',
            'cross-check',
            'lookup-bar',
            'survey-ingest',
            'hydrology',
            'section',
            'lateral',
            'envelopes',
            'prestress',
            'cap-beam',
            'consistency',
            'table-audit',
        ),
        default='full',
    )
    bcalc.add_argument('--params', type=Path, default=None, dest='params_path')
    bcalc.add_argument('--survey', type=Path, default=None, dest='survey_path')
    bcalc.add_argument('--fem-results', type=Path, default=None, dest='fem_results_path')
    bcalc.add_argument('--baseline', type=Path, default=None, dest='baseline_path')
    bcalc.add_argument('--bar-id', default=None, dest='bar_id')
    bcalc.add_argument('--calc-book', type=Path, default=None, dest='calc_book_path')
    bcalc.add_argument('--table', type=Path, default=None, dest='table_path')
    bcalc.add_argument('--out', type=Path, default=None, dest='output_path')
    bcalc.add_argument('--json', action='store_true', dest='as_json')

    gcivil = commands.add_parser('generate-civil', help='generate audited Civil & Bridge CAD sheets or full 18-sheet A3 suite')
    gcivil.add_argument(
        'drawing_type',
        choices=(
            'bridge_elevation',
            'bridge_plan_section',
            't_girder_section',
            't_girder_general',
            'tendon_layout',
            'rebar_detail',
            'bearing_layout',
            'deck_ancillary',
            'pier_and_abutment',
            'notes_and_boq',
            'mechanics_diagrams',
            'full_set',
        ),
    )
    gcivil.add_argument('--spec', type=Path, default=None, dest='spec_path')
    gcivil.add_argument('--survey', type=Path, default=None, dest='survey_path')
    gcivil.add_argument('--out', type=Path, required=True, dest='output_dir')
    gcivil.add_argument('--stem', default=None)
    gcivil.add_argument('--memory', type=Path, default=None, dest='memory_path')
    gcivil.add_argument('--with-previews', action='store_true', dest='with_previews')
    gcivil.add_argument('--json', action='store_true', dest='as_json')

    for name, help_text in (
        ("generate", "generate and audit core DXF and previews"),
        ("inspect", "run a fresh zero-repair inspection generation"),
        ("run", "run the end-to-end task pipeline"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("task_dir", type=Path)
        command.add_argument("--json", action="store_true", dest="as_json")
        command.add_argument("--max-repair-iterations", type=int, default=3)
        command.add_argument("--standard-profile", default=None, dest="standard_profile")
        if name == "run":
            autocad = command.add_mutually_exclusive_group()
            autocad.add_argument("--autocad", action="store_true", dest="run_autocad")
            autocad.add_argument("--no-autocad", action="store_false", dest="run_autocad")
            command.set_defaults(run_autocad=False)
            command.add_argument("--autocad-path", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute one CLI command and return its documented process exit code."""

    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", None) or "").lower()
        if hasattr(stream, "reconfigure") and not enc.startswith("utf"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    arguments = _parser().parse_args(list(argv) if argv is not None else None)
    if arguments.command == "validate-spec":
        return _validate_spec(arguments.path, arguments.as_json)
    if arguments.command in {"doctor", "review", "check-comments"}:
        return _diagnostic_command(arguments)
    if arguments.command in {"inventory", "revise"}:
        return _revision_command(arguments)
    if arguments.command in {'check-drawing-set', 'compare-reference', 'preview', 'operations'}:
        return _upgrade_command(arguments)
    if arguments.command in {'compile-spec', 'learn-standard'}:
        return _phase2_command(arguments)
    if arguments.command in {'mcp-stdio', 'mcp-http'}:
        return _mcp_command(arguments)
    if arguments.command in {'validate-3d-spec', 'generate-3d'}:
        return _phase3_command(arguments)
    if arguments.command in {'drivers', 'verify-roundtrip'}:
        return _phase4_command(arguments)
    if arguments.command in {'score-drawing', 'benchmark-cad1k'}:
        return _cad1k_command(arguments)
    if arguments.command in {'preferences', 'record-regression', 'run-regressions'}:
        return _learning_command(arguments)
    if arguments.command in {'bridge-calc', 'generate-civil'}:
        return _civil_command(arguments)

    from .pipeline import PipelineInputError, PipelineOptions, run_task
    try:
        max_iterations = (
            0 if arguments.command == "inspect" else arguments.max_repair_iterations
        )
        options = PipelineOptions(
            run_autocad=getattr(arguments, "run_autocad", False),
            max_repair_iterations=max_iterations,
            autocad_path=getattr(arguments, "autocad_path", None),
            standard_profile=getattr(arguments, "standard_profile", None),
        )
        result = run_task(arguments.task_dir, options)
    except (PipelineInputError, TypeError, ValueError) as error:
        _write_error(f"user/validation error: {error}", arguments.as_json)
        return 2
    except Exception as error:
        _write_error(
            f"generation/audit failure: {type(error).__name__}: {error}",
            arguments.as_json,
        )
        return 1

    _write_result(arguments.command, result, arguments.as_json)
    return 1 if result.status == "failed" else 0


def _upgrade_command(arguments):
    from ezdxf.lldxf.const import DXFError
    try:
        if arguments.command == 'check-drawing-set':
            from .revision.drawing_set import check_drawing_set
            result = check_drawing_set(arguments.manifest)
        elif arguments.command == 'compare-reference':
            from .revision.reference import compare_reference
            result = compare_reference(arguments.current, arguments.reference, arguments.mapping, arguments.output_dir)
        elif arguments.command == 'preview':
            from .revision.preview import preview_file
            from .revision.workflow import load_revision_plan
            font_map = load_revision_plan(arguments.font_map) if arguments.font_map else None
            result = preview_file(arguments.source, arguments.output_dir, regions=arguments.regions,
                                  font_map=font_map, pdf=arguments.pdf, cache=arguments.cache)
        else:
            from .revision.operations import operation_catalog
            result = operation_catalog()
    except (ValueError, TypeError, OSError, DXFError) as error:
        _write_error(f'user/validation error: {error}', arguments.as_json)
        return 2
    except Exception as error:
        _write_error(f'CAD comparison/render failure: {type(error).__name__}: {error}', arguments.as_json)
        return 1
    print(_stable_json({'command': arguments.command, **result}) if arguments.as_json
          else json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] in {'checked', 'rendered', 'available'} else 1


def _revision_command(arguments: argparse.Namespace) -> int:
    from ezdxf.lldxf.const import DXFError

    from .revision.workflow import apply_revision, inventory_drawing

    try:
        if arguments.command == "inventory":
            result = inventory_drawing(arguments.source)
        else:
            result = apply_revision(arguments.source, arguments.plan, arguments.output_dir,
                                    dry_run=arguments.dry_run, ledger=arguments.ledger)
    except (OSError, UnicodeError, DXFError, TypeError, ValueError) as error:
        _write_error(f"user/validation error: {error}", arguments.as_json)
        return 2
    except Exception as error:
        _write_error(f"revision failure: {type(error).__name__}: {error}", arguments.as_json)
        return 1

    if arguments.as_json:
        print(_stable_json({"command": arguments.command, **result}))
    elif arguments.command == "inventory":
        print(f"source: {result['path']}")
        print(f"sha256: {result['sha256']}")
        print(f"units: {result['units']}")
        for entity in result["entities"]:
            print(f"{entity['handle']} {entity['type']} layer={entity['layer']} "
                  f"id={entity['id']} {_stable_json(entity['geometry'])}")
    else:
        print(f"status: {result['status']}")
        if result["dry_run"]:
            print("dry run: verified; no output published")
        else:
            print(f"output: {result['output_path']}")
            print(f"report: {Path(result['output_path']).parent / 'revision-report.json'}")
            print(f"reused: {result['reused']}")
    return 0


def _diagnostic_command(arguments):
    try:
        if arguments.command == "doctor":
            from .environment import check_environment
            result = check_environment(cache=arguments.cache, autocad_path=arguments.autocad_path,
                                       refresh=arguments.refresh, skip_native=arguments.skip_native)
        elif arguments.command == 'check-comments':
            from .revision.ledger import publish_comment_review
            result = publish_comment_review(arguments.before, arguments.after, arguments.ledger, arguments.output_dir)
        else:
            from .revision.local_review import review_files
            result = review_files(arguments.before, arguments.after, arguments.output_dir,
                                  bounds=arguments.bounds, padding=arguments.padding,
                                  tolerance=arguments.tolerance)
    except (ValueError, TypeError, OSError) as error:
        _write_error(str(error), arguments.as_json)
        return 2
    except Exception as error:
        _write_error(f'diagnostic failure: {type(error).__name__}: {error}', arguments.as_json)
        return 1
    print(_stable_json({'command': arguments.command, **result}) if arguments.as_json
          else json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if arguments.command == 'check-comments' and result['status'] == 'failed' else 0


def _phase2_command(arguments: argparse.Namespace) -> int:
    from ezdxf.lldxf.const import DXFError

    try:
        if arguments.command == "compile-spec":
            from .documents.spec_compiler import compile_spec_from_files

            spec = compile_spec_from_files(
                arguments.inputs,
                part_family=arguments.part_family,
                title_block_language=arguments.lang,
                output_path=arguments.output_path,
            )
            has_blocking = bool(spec.validation.missing or spec.validation.conflicts)
            payload = {
                "command": "compile-spec",
                "output_path": str(arguments.output_path.resolve()),
                "status": "incomplete" if has_blocking else "compiled",
                "missing": spec.validation.missing,
                "conflicts": spec.validation.conflicts,
                "assumptions": spec.validation.assumptions,
            }
            print(_stable_json(payload) if arguments.as_json else json.dumps(payload, ensure_ascii=False, indent=2))
            return 1 if has_blocking else 0

        from .standards.sample_learner import learn_standard_from_dxf, save_standard_yaml

        learned = learn_standard_from_dxf(
            arguments.sample,
            profile_name=arguments.profile_name,
        )
        saved = save_standard_yaml(learned.standard, arguments.output_path)
        payload = {
            "command": "learn-standard",
            "output_path": str(saved.resolve()),
            "status": "learned",
            "profile_name": learned.standard.name,
            "detected_sheet": learned.detected_sheet,
            "matched_layers": learned.matched_layers,
        }
        print(_stable_json(payload) if arguments.as_json else json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, TypeError, OSError, DXFError) as error:
        _write_error(f"user/validation error: {error}", arguments.as_json)
        return 2
    except Exception as error:
        _write_error(f"phase2 command failure: {type(error).__name__}: {error}", arguments.as_json)
        return 1


def _phase3_command(arguments: argparse.Namespace) -> int:
    try:
        from .modeling3d import load_model3d_spec, run_3d_task

        if arguments.command == "validate-3d-spec":
            resolved = arguments.path.expanduser().resolve(strict=True)
            spec_3d = load_model3d_spec(resolved)
            payload = {
                "command": "validate-3d-spec",
                "path": str(resolved),
                "status": "valid",
                "body_kind": spec_3d.body.kind,
            }
            print(_stable_json(payload) if arguments.as_json else f"valid 3d spec ({spec_3d.body.kind}): {resolved}")
            return 0

        resolved_spec = arguments.spec_path.expanduser().resolve(strict=True)
        spec_3d = load_model3d_spec(resolved_spec)
        result = run_3d_task(
            spec_3d,
            arguments.output_dir,
            with_2d_sheet=arguments.with_2d_sheet,
            circle_segments=arguments.segments,
            run_native_cad=arguments.run_native_cad,
            driver_preference=arguments.driver_preference,
        )
        result.pop("iso_png_path", None)
        payload = {"command": "generate-3d", **result}
        print(_stable_json(payload) if arguments.as_json else json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if result["status"] in {"passed", "partial"} else 1
    except (ValueError, TypeError, OSError) as error:
        _write_error(f"user/validation error: {error}", arguments.as_json)
        return 2
    except Exception as error:
        _write_error(f"3D generation/audit failure: {type(error).__name__}: {error}", arguments.as_json)
        return 1


def _phase4_command(arguments: argparse.Namespace) -> int:
    try:
        if arguments.command == "drivers":
            from .cad_drivers.registry import probe_all_cad_drivers

            matrix = probe_all_cad_drivers(
                autocad_path=arguments.autocad_path,
                zwcad_path=arguments.zwcad_path,
                gstarcad_path=arguments.gstarcad_path,
                caxa_path=arguments.caxa_path,
                oda_path=arguments.oda_path,
            )
            payload = {"command": "drivers", **matrix}
            print(_stable_json(payload) if arguments.as_json else json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        from .cad_drivers.roundtrip import verify_dxf_structural_equivalence

        report = verify_dxf_structural_equivalence(
            arguments.before,
            arguments.after,
            bbox_tolerance_mm=arguments.bbox_tolerance,
            output_report_path=arguments.output_report,
        )
        payload = {"command": "verify-roundtrip", **report.to_dict()}
        print(_stable_json(payload) if arguments.as_json else json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if report.status == "passed" else 1
    except (ValueError, TypeError, OSError) as error:
        _write_error(f"user/validation error: {error}", arguments.as_json)
        return 2
    except Exception as error:
        _write_error(f"CAD driver/roundtrip failure: {type(error).__name__}: {error}", arguments.as_json)
        return 1


def _cad1k_command(arguments: argparse.Namespace) -> int:
    try:
        if arguments.command == "score-drawing":
            from .inspection.rubric_scorer import score_dxf_drawing

            custom_rubric = None
            if arguments.rubrics_path is not None:
                custom_rubric = json.loads(arguments.rubrics_path.read_text(encoding="utf-8"))
            report = score_dxf_drawing(
                arguments.source,
                audit_json_path=arguments.audit_json,
                custom_rubric=custom_rubric,
            )
            payload = {"command": "score-drawing", **report.to_dict()}
            print(_stable_json(payload) if arguments.as_json else json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        from .benchmark.cad1k_harness import run_cad1k_benchmark

        summary = run_cad1k_benchmark(
            arguments.output_dir,
            external_bundle_dirs=arguments.bundle_dirs,
            standard_profile=arguments.standard_profile,
        )
        payload = {"command": "benchmark-cad1k", **summary}
        print(_stable_json(payload) if arguments.as_json else json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if summary["passed_count"] == summary["task_count"] else 1
    except (ValueError, TypeError, OSError) as error:
        _write_error(f"user/validation error: {error}", arguments.as_json)
        return 2
    except Exception as error:
        _write_error(f"rubric/benchmark failure: {type(error).__name__}: {error}", arguments.as_json)
        return 1


def _learning_command(arguments: argparse.Namespace) -> int:
    try:
        if arguments.command == "preferences":
            from .standards.preference_memory import (
                learn_preferences_from_sample_dxf,
                load_preference_store,
                set_preference_status,
                teach_preferences_from_text,
            )

            mem = arguments.memory_path
            if arguments.action == "list":
                store = load_preference_store(mem)
                payload = {"command": "preferences", "action": "list", **store.model_dump(mode="json")}
            elif arguments.action == "teach":
                if not arguments.text:
                    raise ValueError("--text is required for 'preferences teach'")
                items = teach_preferences_from_text(mem, arguments.text, part_family=arguments.part_family)
                payload = {
                    "command": "preferences",
                    "action": "teach",
                    "recorded": [e.model_dump(mode="json") for e in items],
                }
            elif arguments.action == "learn-sample":
                if arguments.sample is None:
                    raise ValueError("--sample is required for 'preferences learn-sample'")
                items = learn_preferences_from_sample_dxf(arguments.sample, mem, part_family=arguments.part_family)
                payload = {
                    "command": "preferences",
                    "action": "learn-sample",
                    "recorded": [e.model_dump(mode="json") for e in items],
                }
            else:
                if not arguments.pref_id:
                    raise ValueError("--pref-id is required for 'confirm' / 'reject'")
                target_st = "confirmed" if arguments.action == "confirm" else "rejected"
                updated = set_preference_status(mem, arguments.pref_id, target_st)
                payload = {"command": "preferences", "action": arguments.action, "entry": updated.model_dump(mode="json")}
            print(_stable_json(payload) if arguments.as_json else json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        from .benchmark.regression_flywheel import record_regression_case, replay_regression_vault

        if arguments.command == "record-regression":
            res = record_regression_case(
                arguments.vault_dir,
                case_id=arguments.case_id,
                title=arguments.title,
                source_dxf_path=arguments.source,
                prompt=arguments.prompt,
                must_reject=arguments.must_reject,
            )
            payload = {"command": "record-regression", **res}
            print(_stable_json(payload) if arguments.as_json else json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        rep = replay_regression_vault(arguments.vault_dir)
        payload = {"command": "run-regressions", **rep}
        print(_stable_json(payload) if arguments.as_json else json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if rep["all_passed"] else 1
    except (ValueError, KeyError, FileNotFoundError, FileExistsError, TypeError, OSError) as error:
        _write_error(f"user/validation error: {error}", arguments.as_json)
        return 2
    except Exception as error:
        _write_error(f"learning/regression failure: {type(error).__name__}: {error}", arguments.as_json)
        return 1


def _civil_command(arguments: argparse.Namespace) -> int:
    try:
        if arguments.command == "bridge-calc":
            from .civil.bridge_calc import (
                audit_bridge_design_consistency,
                compute_hydrology_and_span,
                compute_lateral_distribution,
                compute_prestress_estimation_and_loss,
                compute_superstructure_envelopes,
                compute_t_girder_section,
                ingest_and_audit_calc_table,
                ingest_bridge_survey_data,
                run_full_bridge_calculation,
                solve_continuous_cap_beam_1d,
            )
            from .civil.bridge_calc_book import (
                compute_independent_hand_cross_check,
                generate_bridge_calculation_book_markdown,
            )
            from .civil.bridge_checks import lookup_bar_or_tendon

            params = (
                json.loads(arguments.params_path.read_text(encoding="utf-8-sig"))
                if arguments.params_path is not None
                else {}
            )
            if arguments.survey_path is not None:
                params["survey_data"] = json.loads(arguments.survey_path.read_text(encoding="utf-8-sig"))
            if arguments.fem_results_path is not None:
                params["independent_fem_results"] = str(arguments.fem_results_path.resolve())

            mode = arguments.mode
            full_calc_for_book: dict[str, Any] | None = None
            if mode == "full":
                res = run_full_bridge_calculation(params)
                full_calc_for_book = res
            elif mode == "wenchang":
                res = run_full_bridge_calculation({"preset": "wenchang", **params})
                full_calc_for_book = res
            elif mode == "calc-book":
                base_p = {"preset": "wenchang", **params} if not params else params
                full_calc_for_book = run_full_bridge_calculation(base_p)
                md_book = generate_bridge_calculation_book_markdown(
                    full_calc_for_book,
                    output_path=arguments.calc_book_path or arguments.output_path,
                )
                res = {
                    "calculation_status": full_calc_for_book["calculation_status"],
                    "graduation_readiness_status": full_calc_for_book["graduation_readiness_status"],
                    "independent_cross_check": full_calc_for_book["independent_cross_check"],
                    "defense_verification_table": full_calc_for_book["defense_verification_table"],
                    "markdown_length_chars": len(md_book),
                    "calc_book_path": str((arguments.calc_book_path or arguments.output_path).resolve())
                    if (arguments.calc_book_path or arguments.output_path) is not None
                    else None,
                    "markdown": md_book if (arguments.calc_book_path is None and arguments.output_path is None) else None,
                }
            elif mode == "thesis-docx":
                from .civil.bridge_figures import generate_bridge_calculation_figures
                from .civil.bridge_thesis_docx import generate_college_graduation_thesis_docx

                out_docx = arguments.output_path or Path("output/graduation_thesis.docx")
                base_p = {"preset": "wenchang", **params} if not params else params
                full_calc_for_book = run_full_bridge_calculation(base_p)
                figs = generate_bridge_calculation_figures(full_calc_for_book, out_docx.resolve().parent / "figures")
                res = generate_college_graduation_thesis_docx(
                    full_calc_for_book,
                    out_docx,
                    figures_summary=figs,
                    student_metadata=params.get("student_metadata"),
                )
            elif mode == "calc-figures":
                from .civil.bridge_figures import generate_bridge_calculation_figures

                out_fig_dir = arguments.output_path or Path("output/figures")
                base_p = {"preset": "wenchang", **params} if not params else params
                full_calc_for_book = run_full_bridge_calculation(base_p)
                res = generate_bridge_calculation_figures(full_calc_for_book, out_fig_dir)
            elif mode == "fem-compare":
                base_p = {"preset": "wenchang", **params} if "preset" not in params and len(params) <= 1 else params
                full_calc_for_book = run_full_bridge_calculation(base_p)
                res = full_calc_for_book["defense_verification_table"]
            elif mode == "change-impact":
                from .civil.bridge_change_impact import compute_design_change_impact

                base_prev = (
                    json.loads(arguments.baseline_path.read_text(encoding="utf-8-sig"))
                    if arguments.baseline_path is not None
                    else run_full_bridge_calculation({"preset": "wenchang"})
                )
                base_p = {"preset": "wenchang", **params} if "preset" not in params else params
                full_calc_for_book = run_full_bridge_calculation(base_p)
                res = compute_design_change_impact(base_prev, full_calc_for_book)
            elif mode == "cross-check":
                base_p = {"preset": "wenchang", **params} if not params else params
                full_calc_for_book = run_full_bridge_calculation(base_p)
                res = compute_independent_hand_cross_check(full_calc_for_book)
            elif mode == "lookup-bar":
                target_id = arguments.bar_id or params.get("bar_id")
                if not target_id:
                    raise ValueError("--bar-id or params['bar_id'] is required for --mode lookup-bar")
                base_p = {k: v for k, v in params.items() if k != "bar_id"}
                if not base_p:
                    base_p = {"preset": "wenchang"}
                full_calc_for_book = run_full_bridge_calculation(base_p)
                res = lookup_bar_or_tendon(full_calc_for_book, str(target_id))
            elif mode == "survey-ingest":
                raw_sd = params.get("survey_data", params)
                res = ingest_bridge_survey_data(raw_sd)
            elif mode == "hydrology":
                res = compute_hydrology_and_span(**params)
            elif mode == "section":
                res = compute_t_girder_section(**params)
            elif mode == "lateral":
                res = compute_lateral_distribution(**params)
            elif mode == "envelopes":
                res = compute_superstructure_envelopes(**params)
            elif mode == "prestress":
                res = compute_prestress_estimation_and_loss(**params)
            elif mode == "cap-beam":
                res = solve_continuous_cap_beam_1d(**params)
            elif mode == "consistency":
                res = audit_bridge_design_consistency(params)
            else:
                if arguments.table_path is None and "table_text" not in params:
                    raise ValueError("--table or params['table_text'] is required for --mode table-audit")
                res = ingest_and_audit_calc_table(
                    table_text=params.get("table_text"),
                    file_path=arguments.table_path,
                )

            if arguments.calc_book_path is not None and full_calc_for_book is not None and mode != "calc-book":
                generate_bridge_calculation_book_markdown(full_calc_for_book, output_path=arguments.calc_book_path)

            payload = {"command": "bridge-calc", "mode": mode, "result": res}
            if arguments.output_path is not None and mode not in {"calc-book", "thesis-docx", "calc-figures"}:
                arguments.output_path.parent.mkdir(parents=True, exist_ok=True)
                arguments.output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(_stable_json(payload) if arguments.as_json else json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        from .civil.bridge_drawings import generate_civil_bridge_bundle, generate_full_bridge_drawing_set

        spec_obj = (
            json.loads(arguments.spec_path.read_text(encoding="utf-8-sig"))
            if arguments.spec_path is not None
            else {}
        )
        if arguments.survey_path is not None:
            spec_obj["survey_data"] = json.loads(arguments.survey_path.read_text(encoding="utf-8-sig"))
        if arguments.memory_path is not None:
            spec_obj.setdefault("preference_memory_path", str(arguments.memory_path))
        if arguments.drawing_type == "full_set":
            full_res = generate_full_bridge_drawing_set(
                spec_obj or {"preset": "wenchang"},
                arguments.output_dir,
                render_png=bool(arguments.with_previews),
                preference_memory_path=arguments.memory_path,
            )
            full_res.pop("calculation_report", None)
            payload = {"command": "generate-civil", "drawing_type": "full_set", **full_res}
            print(_stable_json(payload) if arguments.as_json else json.dumps(payload, ensure_ascii=False, indent=2))
            return 0 if full_res["status"] == "passed" else 1

        bundle = generate_civil_bridge_bundle(
            arguments.drawing_type,
            spec_obj,
            arguments.output_dir,
            stem=arguments.stem,
        )
        bundle.pop("preview_png_path", None)
        payload = {"command": "generate-civil", **bundle}
        print(_stable_json(payload) if arguments.as_json else json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if bundle["status"] == "passed" else 1
    except (ValueError, KeyError, FileNotFoundError, TypeError, OSError) as error:
        _write_error(f"user/validation error: {error}", arguments.as_json)
        return 2
    except Exception as error:
        _write_error(f"civil calculation/generation failure: {type(error).__name__}: {error}", arguments.as_json)
        return 1


def _mcp_command(arguments: argparse.Namespace) -> int:
    if arguments.command == "mcp-stdio":
        from .mcp.stdio_server import serve_stdio

        return serve_stdio(workspace_root=arguments.workspace_root)

    import uvicorn
    from .mcp.http_app import create_mcp_http_app

    app = create_mcp_http_app(arguments.workspace_root)
    uvicorn.run(app, host=arguments.host, port=arguments.port, log_level="info")
    return 0


def _validate_spec(path: Path, as_json: bool) -> int:
    try:
        from cad_skill_core.schema.io import load_spec
        resolved = path.expanduser().resolve(strict=True)
        load_spec(resolved)
    except Exception as error:
        _write_error(f"invalid drawing specification: {path}: {error}", as_json)
        return 2
    payload = {
        "command": "validate-spec",
        "path": str(resolved),
        "status": "valid",
    }
    if as_json:
        print(_stable_json(payload))
    else:
        print(f"valid: {resolved}")
    return 0


def _write_result(command: str, result: PipelineResult, as_json: bool) -> None:
    payload = {
        "audit_path": str(result.audit_path),
        "command": command,
        "outputs": [str(path) for path in result.outputs],
        "status": result.status,
        "task_dir": str(result.task_dir),
    }
    if as_json:
        print(_stable_json(payload))
        return
    print(f"status: {result.status}")
    print(f"task: {result.task_dir}")
    print(f"audit: {result.audit_path}")
    for path in result.outputs:
        print(f"output: {path}")


def _write_error(message: str, as_json: bool) -> None:
    if as_json:
        print(_stable_json({"error": message, "status": "error"}), file=sys.stderr)
    else:
        print(f"error: {message}", file=sys.stderr)


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    raise SystemExit(main())
