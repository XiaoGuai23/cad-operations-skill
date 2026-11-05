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

    for name, help_text in (
        ("generate", "generate and audit core DXF and previews"),
        ("inspect", "run a fresh zero-repair inspection generation"),
        ("run", "run the end-to-end task pipeline"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("task_dir", type=Path)
        command.add_argument("--json", action="store_true", dest="as_json")
        command.add_argument("--max-repair-iterations", type=int, default=3)
        if name == "run":
            autocad = command.add_mutually_exclusive_group()
            autocad.add_argument("--autocad", action="store_true", dest="run_autocad")
            autocad.add_argument("--no-autocad", action="store_false", dest="run_autocad")
            command.set_defaults(run_autocad=False)
            command.add_argument("--autocad-path", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute one CLI command and return its documented process exit code."""

    arguments = _parser().parse_args(list(argv) if argv is not None else None)
    if arguments.command == "validate-spec":
        return _validate_spec(arguments.path, arguments.as_json)
    if arguments.command in {"doctor", "review", "check-comments"}:
        return _diagnostic_command(arguments)
    if arguments.command in {"inventory", "revise"}:
        return _revision_command(arguments)
    if arguments.command in {'check-drawing-set', 'compare-reference', 'preview', 'operations'}:
        return _upgrade_command(arguments)

    from .pipeline import PipelineInputError, PipelineOptions, run_task
    try:
        max_iterations = (
            0 if arguments.command == "inspect" else arguments.max_repair_iterations
        )
        options = PipelineOptions(
            run_autocad=getattr(arguments, "run_autocad", False),
            max_repair_iterations=max_iterations,
            autocad_path=getattr(arguments, "autocad_path", None),
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
