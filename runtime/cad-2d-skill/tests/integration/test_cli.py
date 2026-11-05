from __future__ import annotations

import json

from cad_skill_core.cli import main
from tests.helpers import copy_example


def test_validate_spec_json_output_is_stable_and_machine_readable(tmp_path, capsys):
    task_dir = copy_example("simple-flange", tmp_path)
    spec = task_dir / "work" / "drawing-spec.json"

    exit_code = main(["validate-spec", str(spec), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "command": "validate-spec",
        "path": str(spec.resolve()),
        "status": "valid",
    }


def test_validate_spec_expected_error_has_no_traceback(tmp_path, capsys):
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")

    exit_code = main(["validate-spec", str(invalid)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "invalid" in captured.err.lower()
    assert "traceback" not in captured.err.lower()


def test_run_no_autocad_returns_zero_for_partial_and_stable_json(tmp_path, capsys):
    task_dir = copy_example("simple-flange", tmp_path)

    exit_code = main(["run", str(task_dir), "--no-autocad", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "run"
    assert payload["status"] == "partial"
    assert payload["audit_path"].endswith("audit.json")


def test_generate_returns_one_for_nonrepairable_core_failure(tmp_path, capsys):
    task_dir = copy_example("simple-flange", tmp_path)
    spec_path = task_dir / "work" / "drawing-spec.json"
    value = json.loads(spec_path.read_text(encoding="utf-8"))
    value["validation"] = {"conflicts": ["outer diameter has conflicting sources"]}
    spec_path.write_text(json.dumps(value), encoding="utf-8")

    exit_code = main(["generate", str(task_dir)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "status: failed" in captured.out
    assert "traceback" not in (captured.out + captured.err).lower()
