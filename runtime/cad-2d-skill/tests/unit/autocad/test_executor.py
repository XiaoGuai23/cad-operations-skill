from dataclasses import FrozenInstanceError
import hashlib
from contextlib import contextmanager
import os
import subprocess
from types import SimpleNamespace
from pathlib import Path

import ezdxf
import pytest
from pypdf import PdfWriter

import cad_skill_core.autocad as autocad_api
import cad_skill_core.autocad.executor as executor_module

from cad_skill_core.autocad.executor import (
    _ProcessOutcome,
    _capture_directory_identity,
    _capture_file_identity,
    _publish_atomic,
    _rollback_published,
    _render_script,
    _minimal_environment,
    _run_process,
    build_core_console_command,
    execute_autocad,
)
from cad_skill_core.autocad.models import (
    AutoCADErrorDetail,
    AutoCADExecutionError,
    ExecutableIdentity,
    SignatureEvidence,
    AutoCADInstallation,
    AutoCADNotInstalledError,
    AutoCADRequest,
    AutoCADResult,
)


def _installation(fake_installation):
    def identity(path):
        metadata = path.stat()
        return ExecutableIdentity(
            path.resolve(),
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
            'CN="Autodesk, Inc.", O="Autodesk, Inc.", C=US',
            "B0E1E4FEF69EABDAC44CB9AD1C30E59E1C9066D0",
        )

    return AutoCADInstallation(
        fake_installation.acad_exe,
        fake_installation.core_console_exe,
        "2023",
        fake_installation.fonts_dir,
        identity(fake_installation.acad_exe),
        identity(fake_installation.core_console_exe),
        True,
    )


@pytest.fixture(autouse=True)
def _trusted_fake_executable_metadata(monkeypatch):
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._read_version_metadata",
        lambda path: (
            ("AcCoreConsole", "24.2.53.0.0")
            if Path(path).name.casefold() == "accoreconsole.exe"
            else ("AutoCAD", "R24.2.53.0.0")
        ),
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._verify_authenticode",
        lambda _path: SignatureEvidence(
            "Valid",
            'CN="Autodesk, Inc.", O="Autodesk, Inc.", C=US',
            "B0E1E4FEF69EABDAC44CB9AD1C30E59E1C9066D0",
        ),
    )


def _valid_dxf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = ezdxf.new("R2018")
    document.modelspace().add_line((0, 0), (10, 10))
    document.saveas(path)
    return path


def _valid_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with path.open("wb") as stream:
        writer.write(stream)


def _valid_dwg(path: Path) -> None:
    path.write_bytes(b"AC1032" + bytes(range(128)))


def _write_fake_logs(stdout_path: Path, stderr_path: Path, *, verified=False) -> None:
    text = "fake core console output"
    if verified:
        text += "\nCAD_SKILL_DWG_VERIFIED\n"
    stdout_path.write_text(text, encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")


def test_command_uses_literal_paths_with_spaces(fake_installation, tmp_path):
    source = _valid_dxf(tmp_path / "cad task" / "drawing.dxf")
    request = AutoCADRequest(
        input_dxf=source,
        output_dir=tmp_path / "out files",
        task_work_dir=tmp_path / "task work",
    )

    command = build_core_console_command(_installation(fake_installation), request)

    assert command == [
        str(fake_installation.core_console_exe.resolve()),
        "/i",
        str(source.resolve()),
        "/s",
        str((tmp_path / "task work" / "autocad" / "core-console.scr").resolve()),
        "/l",
        "en-US",
    ]
    assert all(isinstance(argument, str) for argument in command)


def test_request_is_immutable_normalized_and_rejects_command_injection(tmp_path):
    source = tmp_path / "relative.dxf"
    request = AutoCADRequest(source, tmp_path / "out", tmp_path / "work")

    assert request.input_dxf.is_absolute()
    assert request.output_dir.is_absolute()
    assert request.task_work_dir.is_absolute()
    with pytest.raises(FrozenInstanceError):
        request.drawing_stem = "changed"
    with pytest.raises(ValueError, match="drawing_stem"):
        AutoCADRequest(source, tmp_path / "out", drawing_stem=r"..\escape")
    with pytest.raises(ValueError, match="plot_layout"):
        AutoCADRequest(source, tmp_path / "out", plot_layout="Layout1\n_.ERASE")
    with pytest.raises(ValueError, match="unsupported AutoCAD operation"):
        AutoCADRequest(source, tmp_path / "out", operations=("raw-command",))


def test_execution_stages_unicode_paths_and_publishes_only_validated_outputs(
    fake_installation, tmp_path, monkeypatch
):
    task = tmp_path / "工程 cad task"
    source = _valid_dxf(task / "input" / "原图 文件.dxf")
    original = source.read_bytes()
    request = AutoCADRequest(
        source,
        task / "output files",
        task / "work files",
        drawing_stem="法兰 图",
        plot_layout="Layout1",
    )
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )
    calls = []

    def fake_run(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        calls.append((argv, cwd, env, timeout_seconds, stdout_path, stderr_path))
        _write_fake_logs(stdout_path, stderr_path, verified=len(calls) > 1)
        if len(calls) == 1:
            _valid_dwg(cwd / "drawing.dwg")
            _valid_pdf(cwd / "drawing.pdf")
        return _ProcessOutcome(0, False)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fake_run)

    result = execute_autocad(request)

    assert result.status == "succeeded", result
    assert result.return_code == 0
    assert result.dwg_path == (request.output_dir / "法兰 图.dwg")
    assert result.pdf_path == (request.output_dir / "法兰 图.pdf")
    assert result.dwg_path.stat().st_size > 0
    assert result.pdf_path.read_bytes().startswith(b"%PDF-")
    assert source.read_bytes() == original
    assert result.script_path == request.task_work_dir / "autocad" / "core-console.scr"
    assert result.stdout_path == request.task_work_dir / "autocad" / "stdout.log"
    assert result.stderr_path == request.task_work_dir / "autocad" / "stderr.log"
    script = result.script_path.read_text(encoding="ascii")
    assert "原图" not in script and "法兰" not in script
    commands = [line for line in script.splitlines() if line.startswith("_.")]
    assert set(commands) <= {
        "_.AUDIT",
        "_.QSAVE",
        "_.SAVEAS",
        "_.-PLOT",
        "_.QUIT",
    }
    assert len(calls) == 2
    first_argv, first_cwd, first_env, *_ = calls[0]
    assert first_argv[0] == str(installation.core_console_exe)
    assert Path(first_argv[first_argv.index("/i") + 1]).name == "input.dxf"
    assert first_cwd.name.startswith("run-") and first_cwd.name.isascii()
    assert all(Path(item).name.isascii() for item in (first_cwd / "input.dxf",))
    assert "UNRELATED_SECRET" not in first_env


def test_existing_outputs_are_versioned_as_a_pair(fake_installation, tmp_path, monkeypatch):
    source = _valid_dxf(tmp_path / "input" / "drawing.dxf")
    output = tmp_path / "output"
    output.mkdir()
    (output / "drawing.dwg").write_bytes(b"old dwg")
    (output / "drawing.pdf").write_bytes(b"old pdf")
    request = AutoCADRequest(source, output, tmp_path / "work")
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )

    calls = 0

    def fake_run(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        nonlocal calls
        calls += 1
        _write_fake_logs(stdout_path, stderr_path, verified=calls > 1)
        if calls == 1:
            _valid_dwg(cwd / "drawing.dwg")
            _valid_pdf(cwd / "drawing.pdf")
        return _ProcessOutcome(0, False)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fake_run)

    result = execute_autocad(request)

    assert result.status == "succeeded", result
    assert result.dwg_path.name == "drawing-v01.dwg"
    assert result.pdf_path.name == "drawing-v01.pdf"
    assert (output / "drawing.dwg").read_bytes() == b"old dwg"
    assert (output / "drawing.pdf").read_bytes() == b"old pdf"


def test_missing_autocad_returns_structured_skipped_status(tmp_path, monkeypatch):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    detail = AutoCADErrorDetail("not-installed", "AutoCAD 2023 is absent")

    def absent(_path=None):
        raise AutoCADNotInstalledError(detail)

    monkeypatch.setattr("cad_skill_core.autocad.executor.discover_autocad", absent)

    result = execute_autocad(request)

    assert result.status == "skipped"
    assert result.error == detail
    assert result.dwg_path is None and result.pdf_path is None
    assert result.return_code is None


@pytest.mark.parametrize("suffix", [".dwg", ".txt", ""])
def test_unsupported_input_extension_is_rejected_before_any_write(tmp_path, suffix):
    source = tmp_path / ("drawing" + suffix)
    source.write_bytes(b"not a DXF")
    output = tmp_path / "output"
    work = tmp_path / "work"

    result = execute_autocad(AutoCADRequest(source, output, work))

    assert result.status == "failed"
    assert result.error.code == "invalid-request"
    assert ".dxf" in result.error.message
    assert not output.exists()
    assert result.stdout_path.is_file()
    assert result.stderr_path.is_file()


def test_missing_or_malformed_dxf_is_rejected_before_discovery(tmp_path, monkeypatch):
    discovered = False

    def must_not_discover(_path=None):
        nonlocal discovered
        discovered = True
        raise AssertionError("discovery must follow input validation")

    monkeypatch.setattr("cad_skill_core.autocad.executor.discover_autocad", must_not_discover)
    missing = tmp_path / "missing.dxf"
    malformed = tmp_path / "malformed.dxf"
    malformed.write_bytes(b"garbage")

    missing_result = execute_autocad(
        AutoCADRequest(missing, tmp_path / "out", tmp_path / "work-missing")
    )
    malformed_result = execute_autocad(
        AutoCADRequest(malformed, tmp_path / "out", tmp_path / "work-malformed")
    )

    assert missing_result.status == "failed"
    assert missing_result.error.code == "invalid-request"
    assert "missing" in missing_result.error.message
    assert malformed_result.status == "failed"
    assert malformed_result.error.code == "invalid-request"
    assert "malformed" in malformed_result.error.message
    assert discovered is False


def test_output_hardlink_alias_to_source_is_rejected(tmp_path):
    source = _valid_dxf(tmp_path / "input" / "drawing.dxf")
    output = tmp_path / "output"
    output.mkdir()
    try:
        (output / "drawing.dwg").hardlink_to(source)
    except OSError:
        pytest.skip("hard links are unavailable")

    result = execute_autocad(AutoCADRequest(source, output, tmp_path / "work"))

    assert result.status == "failed"
    assert result.error.code == "invalid-request"
    assert "aliases source" in result.error.message
    assert source.stat().st_size > 0


def test_reparse_output_root_is_rejected_without_writes(tmp_path):
    real_output = tmp_path / "outside"
    real_output.mkdir()
    link = tmp_path / "task" / "output"
    link.parent.mkdir()
    try:
        link.symlink_to(real_output, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    with pytest.raises(ValueError, match="reparse|symlink"):
        AutoCADRequest(tmp_path / "input.dxf", link, tmp_path / "work")


def test_process_failure_preserves_script_and_logs_without_publication(
    fake_installation, tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )

    def fail(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        stdout_path.write_text("core output", encoding="utf-8")
        stderr_path.write_text("fatal conversion error", encoding="utf-8")
        return _ProcessOutcome(9, False)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fail)

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.return_code == 9
    assert result.error.code == "process-failed"
    assert result.script_path.stat().st_size > 0
    assert result.stdout_path.read_text(encoding="utf-8") == "core output"
    assert "fatal" in result.stderr_path.read_text(encoding="utf-8")
    assert not request.output_dir.exists()


def test_invalid_pdf_prevents_both_outputs_from_being_published(
    fake_installation, tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )

    def invalid_output(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        stdout_path.write_text("ok", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        (cwd / "drawing.dwg").write_bytes(b"nonempty dwg")
        (cwd / "drawing.pdf").write_bytes(b"not a pdf")
        return _ProcessOutcome(0, False)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", invalid_output)

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "output-invalid"
    assert not request.output_dir.exists()


def test_timeout_terminates_process_tree_and_records_logs(tmp_path, monkeypatch):
    stdout = tmp_path / "stdout.log"
    stderr = tmp_path / "stderr.log"
    terminated = []

    class FakeProcess:
        pid = 321
        returncode = None
        attempts = 0

        def communicate(self, timeout=None):
            self.attempts += 1
            if self.attempts == 1:
                raise subprocess.TimeoutExpired(["fake.exe"], timeout)
            self.returncode = -9
            return b"partial stdout", b"partial stderr"

        def kill(self):
            self.returncode = -9

    def fake_popen(argv, **kwargs):
        assert argv == ["fake.exe", "/safe"]
        assert kwargs["shell"] is False
        assert kwargs["stdout"] == subprocess.PIPE
        assert kwargs["stderr"] == subprocess.PIPE
        return FakeProcess()

    monkeypatch.setattr("cad_skill_core.autocad.executor.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._terminate_process_tree",
        lambda process, env: terminated.append((process.pid, env)),
    )

    outcome = _run_process(
        ["fake.exe", "/safe"],
        cwd=tmp_path,
        env={"SystemRoot": r"C:\Windows"},
        timeout_seconds=0.01,
        stdout_path=stdout,
        stderr_path=stderr,
    )

    assert outcome == _ProcessOutcome(-9, True)
    assert terminated == [(321, {"SystemRoot": r"C:\Windows"})]
    assert stdout.read_bytes() == b"partial stdout"
    assert stderr.read_bytes() == b"partial stderr"


def test_timeout_drain_is_bounded_when_child_keeps_pipes_open(tmp_path, monkeypatch):
    stdout = tmp_path / "stdout.log"
    stderr = tmp_path / "stderr.log"
    calls = []

    class Pipe:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class FakeProcess:
        pid = 654
        returncode = None
        stdout = Pipe()
        stderr = Pipe()

        def communicate(self, timeout=None):
            calls.append(timeout)
            raise subprocess.TimeoutExpired(["fake.exe"], timeout)

        def kill(self):
            self.returncode = -9

    process = FakeProcess()
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.subprocess.Popen", lambda *_a, **_k: process
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._terminate_process_tree",
        lambda child, env: child.kill(),
    )

    outcome = _run_process(
        ["fake.exe"],
        cwd=tmp_path,
        env={"SystemRoot": r"C:\Windows"},
        timeout_seconds=0.01,
        stdout_path=stdout,
        stderr_path=stderr,
    )

    assert outcome == _ProcessOutcome(-9, True)
    assert calls == [0.01, 5.0]
    assert process.stdout.closed and process.stderr.closed
    assert stdout.read_bytes() == b""
    assert stderr.read_bytes() == b""


@pytest.mark.parametrize("raced_name", ("stdout.log", "stderr.log"))
def test_process_log_capture_rejects_hardlink_swap_without_truncation(
    tmp_path, monkeypatch, raced_name
):
    stdout = tmp_path / "stdout.log"
    stderr = tmp_path / "stderr.log"
    stdout.write_bytes(b"")
    stderr.write_bytes(b"")
    valuable = tmp_path / f"valuable-{raced_name}"
    valuable_bytes = b"valuable external log must survive"
    valuable.write_bytes(valuable_bytes)

    class FakeProcess:
        returncode = 0

        def communicate(self, timeout=None):
            target = tmp_path / raced_name
            target.unlink()
            try:
                os.link(valuable, target)
            except OSError as error:
                pytest.skip(f"hard links are unavailable on this filesystem: {error}")
            assert target.stat().st_nlink >= 2
            return b"captured stdout", b"captured stderr"

    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.subprocess.Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )

    with pytest.raises(AutoCADExecutionError, match="multiple hard links"):
        _run_process(
            ["fake.exe"],
            cwd=tmp_path,
            env={"SystemRoot": r"C:\Windows"},
            timeout_seconds=1.0,
            stdout_path=stdout,
            stderr_path=stderr,
        )

    assert valuable.read_bytes() == valuable_bytes
    assert (tmp_path / raced_name).read_bytes() == valuable_bytes
    untouched = stderr if raced_name == "stdout.log" else stdout
    assert untouched.read_bytes() == b""


@pytest.mark.skipif(os.name != "nt", reason="Windows deny-delete directory handles")
def test_atomic_evidence_fails_safely_when_parent_changes_after_final_check(
    tmp_path, monkeypatch
):
    parent = tmp_path / "owned"
    parent.mkdir()
    target = parent / "stdout.log"
    target.write_bytes(b"old evidence")
    displaced = tmp_path / "displaced-owned"
    valuable = tmp_path / "valuable.log"
    valuable_bytes = b"valuable external evidence"
    valuable.write_bytes(valuable_bytes)
    external_partial_bytes = b"external same-name partial must survive"
    external_partials = []
    rename_attempts = []
    real_validate = executor_module._validate_directory_identity

    def try_replace_parent_after_validation(expected):
        real_validate(expected)
        if not any(path.name.endswith(".partial") for path in parent.iterdir()):
            return
        try:
            partial_name = next(
                path.name for path in parent.iterdir() if path.name.endswith(".partial")
            )
            parent.rename(displaced)
            parent.mkdir()
            external_partial = parent / partial_name
            external_partial.write_bytes(external_partial_bytes)
            external_partials.append(external_partial)
            os.link(valuable, target)
        except OSError:
            rename_attempts.append("blocked")
        else:
            rename_attempts.append("replaced")

    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._validate_directory_identity",
        try_replace_parent_after_validation,
    )
    with pytest.raises(AutoCADExecutionError, match="evidence parent changed"):
        executor_module._replace_evidence_files_atomic(((target, b"new evidence"),))

    assert rename_attempts == ["replaced"]
    assert valuable.read_bytes() == valuable_bytes
    assert target.read_bytes() == valuable_bytes
    assert len(external_partials) == 1
    assert external_partials[0].read_bytes() == external_partial_bytes
    assert (displaced / "stdout.log").read_bytes() == b"old evidence"
    assert not any(path.name.endswith(".partial") for path in displaced.iterdir())


def test_environment_replaces_caller_path_with_trusted_autocad_path(
    fake_installation, tmp_path, monkeypatch
):
    trusted_system = Path(r"C:\TrustedWindows\System32")
    monkeypatch.setenv("SystemRoot", r"C:\attacker-windows")
    monkeypatch.setenv("WINDIR", r"C:\attacker-windir")
    monkeypatch.setenv("PATH", r"C:\attacker-bin")
    monkeypatch.setenv("COMSPEC", r"C:\attacker-bin\cmd.exe")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._trusted_system_directory",
        lambda: trusted_system,
        raising=False,
    )
    installation = _installation(fake_installation)

    environment = _minimal_environment(tmp_path, installation)

    path_parts = environment["PATH"].split(os.pathsep)
    assert r"C:\attacker-bin" not in path_parts
    assert r"C:\attacker-windows" not in repr(environment)
    assert str(trusted_system) in path_parts
    assert environment["SystemRoot"] == str(trusted_system.parent)
    assert environment["COMSPEC"] == str(trusted_system / "cmd.exe")
    assert str(installation.core_console_exe.parent) in path_parts
    assert str(installation.acad_exe.parent) in path_parts
    assert "UNRELATED_SECRET" not in environment


def test_template_injection_is_rejected_even_when_it_looks_like_prompt_data(
    tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    malicious = tmp_path / "malicious.scr.j2"
    malicious.write_text(
        "{{SAVE_DWG_BLOCK}}ERASE\nALL\n{{PLOT_PDF_BLOCK}}_.QUIT\n",
        encoding="ascii",
    )
    monkeypatch.setattr("cad_skill_core.autocad.executor._TEMPLATE_PATH", malicious)

    with pytest.raises(AutoCADExecutionError, match="allowlist"):
        _render_script(request)


def test_script_matches_autocad_2023_minimal_plot_prompt_order(tmp_path):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")

    assert _render_script(request).splitlines() == [
        "_.AUDIT",
        "_Yes",
        "_.QSAVE",
        "_.SAVEAS",
        "2018",
        "drawing.dwg",
        "_.-PLOT",
        "_No",
        "Layout1",
        "",
        "DWG To PDF.pc3",
        "_Yes",
        "drawing.pdf",
        "_No",
        "_Yes",
        "_.QUIT",
    ]


@pytest.mark.parametrize(
    ("operations", "expected_dwg", "expected_pdf", "expected_calls"),
    [
        (("dxf-to-dwg",), True, False, 2),
        (("plot-pdf",), False, True, 1),
    ],
)
def test_each_supported_operation_can_run_without_the_other(
    fake_installation,
    tmp_path,
    monkeypatch,
    operations,
    expected_dwg,
    expected_pdf,
    expected_calls,
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(
        source, tmp_path / "output", tmp_path / "work", operations=operations
    )
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )
    calls = 0

    def fake_run(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        nonlocal calls
        calls += 1
        _write_fake_logs(stdout_path, stderr_path, verified=calls > 1)
        if calls == 1:
            if expected_dwg:
                _valid_dwg(cwd / "drawing.dwg")
            if expected_pdf:
                _valid_pdf(cwd / "drawing.pdf")
        return _ProcessOutcome(0, False)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fake_run)

    result = execute_autocad(request)

    assert result.status == "succeeded", result
    assert (result.dwg_path is not None) is expected_dwg
    assert (result.pdf_path is not None) is expected_pdf
    assert calls == expected_calls


def test_result_rejects_incoherent_status_and_error_combinations(tmp_path):
    detail = AutoCADErrorDetail("not-installed", "absent")

    with pytest.raises(ValueError, match="succeeded"):
        AutoCADResult("succeeded", 0, tmp_path / "x.dwg", None, None, None, None, 0, detail)
    with pytest.raises(ValueError, match="skipped"):
        AutoCADResult("skipped", 1, None, None, None, None, None, 0, detail)
    with pytest.raises(ValueError, match="failed"):
        AutoCADResult("failed", 1, None, None, None, None, None, 0, None)


def test_public_api_exports_executor_interfaces():
    assert autocad_api.execute_autocad is execute_autocad
    assert autocad_api.build_core_console_command is build_core_console_command


def test_runtime_reparse_change_is_rejected_before_discovery(tmp_path, monkeypatch):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    checked = []

    def reject(path, field):
        checked.append((path, field))
        raise ValueError(f"{field} contains a reparse point")

    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._reject_reparse_components", reject
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad",
        lambda _path=None: (_ for _ in ()).throw(AssertionError("must not discover")),
    )

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "workdir-failed"
    assert "reparse" in result.error.message
    assert checked == [(request.task_work_dir, "task_work_dir")]


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), True])
def test_timeout_must_be_a_finite_number_not_a_boolean(tmp_path, timeout):
    with pytest.raises(ValueError, match="timeout_seconds"):
        AutoCADRequest(tmp_path / "input.dxf", tmp_path / "output", timeout_seconds=timeout)


@pytest.mark.parametrize("stem", ["CON", "nul", "COM1", "x" * 200])
def test_windows_reserved_or_overlong_drawing_stem_is_rejected(tmp_path, stem):
    with pytest.raises(ValueError, match="drawing_stem"):
        AutoCADRequest(tmp_path / "input.dxf", tmp_path / "output", drawing_stem=stem)


def test_failed_taskkill_falls_back_to_direct_process_kill(monkeypatch):
    killed = []
    invocations = []
    process = SimpleNamespace(pid=77, kill=lambda: killed.append(77))
    monkeypatch.setattr("cad_skill_core.autocad.executor.os.name", "nt")
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._trusted_system_directory",
        lambda: Path(r"C:\TrustedWindows\System32"),
        raising=False,
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.subprocess.run",
        lambda *args, **kwargs: (
            invocations.append((args, kwargs)) or SimpleNamespace(returncode=1)
        ),
    )

    from cad_skill_core.autocad.executor import _terminate_process_tree

    _terminate_process_tree(process, {"SystemRoot": r"C:\attacker-windows"})

    assert killed == [77]
    assert invocations[0][0][0][0] == str(
        Path(r"C:\TrustedWindows\System32") / "taskkill.exe"
    )
    assert invocations[0][1]["shell"] is False
    assert invocations[0][1]["env"] == {
        "SystemRoot": r"C:\TrustedWindows",
        "WINDIR": r"C:\TrustedWindows",
        "PATH": r"C:\TrustedWindows\System32",
    }


def test_output_allocation_error_is_structured_and_retains_execution_evidence(
    fake_installation, tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )
    calls = 0

    def fake_run(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        nonlocal calls
        calls += 1
        _write_fake_logs(stdout_path, stderr_path, verified=calls > 1)
        if calls == 1:
            _valid_dwg(cwd / "drawing.dwg")
            _valid_pdf(cwd / "drawing.pdf")
        return _ProcessOutcome(0, False)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fake_run)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._allocate_outputs",
        lambda _request: (_ for _ in ()).throw(OSError("output denied")),
    )

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "output-invalid"
    assert "output denied" in result.error.message
    assert result.script_path.stat().st_size > 0


@pytest.mark.parametrize(
    ("patch_target", "message"),
    [
        ("_allocate_outputs", "allocation identity rejected"),
        ("_publish_atomic", "publication identity rejected"),
    ],
)
def test_execution_translates_identity_boundary_errors(
    fake_installation, tmp_path, monkeypatch, patch_target, message
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(
        source, tmp_path / "output", tmp_path / "work", operations=("plot-pdf",)
    )
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )

    def fake_run(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        _write_fake_logs(stdout_path, stderr_path)
        _valid_pdf(cwd / "drawing.pdf")
        return _ProcessOutcome(0, False)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fake_run)
    monkeypatch.setattr(
        f"cad_skill_core.autocad.executor.{patch_target}",
        lambda *_args: (_ for _ in ()).throw(AutoCADExecutionError(message)),
    )

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "output-invalid"
    assert message in result.error.message


def test_failed_second_publication_reports_handle_rollback_retention(
    fake_installation, tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )
    calls = 0

    def fake_run(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        nonlocal calls
        calls += 1
        _write_fake_logs(stdout_path, stderr_path, verified=calls > 1)
        if calls == 1:
            _valid_dwg(cwd / "drawing.dwg")
            _valid_pdf(cwd / "drawing.pdf")
        return _ProcessOutcome(0, False)

    real_publish = _publish_atomic
    publications = 0

    def fail_second(source_path, destination, parent_identity):
        nonlocal publications
        publications += 1
        if publications == 2:
            raise AutoCADExecutionError("second publication failed")
        return real_publish(source_path, destination, parent_identity)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fake_run)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._publish_atomic", fail_second
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._rollback_published",
        lambda files: tuple(item.path for item in files),
    )

    result = execute_autocad(request)

    assert result.status == "failed"
    assert "partial publication retained" in result.error.message
    assert str(request.output_dir / "drawing.dwg") in result.error.message


def test_runtime_publication_value_error_rolls_back_and_is_structured(
    fake_installation, tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )
    calls = 0

    def fake_run(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        nonlocal calls
        calls += 1
        _write_fake_logs(stdout_path, stderr_path, verified=calls > 1)
        if calls == 1:
            _valid_dwg(cwd / "drawing.dwg")
            _valid_pdf(cwd / "drawing.pdf")
        return _ProcessOutcome(0, False)

    real_publish = _publish_atomic
    publications = 0

    def fail_second(source_path, destination, parent_identity):
        nonlocal publications
        publications += 1
        if publications == 2:
            raise ValueError("runtime publication reparse detected")
        return real_publish(source_path, destination, parent_identity)

    rolled_back = []

    def retain_for_evidence(files):
        rolled_back.extend(item.path for item in files)
        return tuple(item.path for item in files)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fake_run)
    monkeypatch.setattr("cad_skill_core.autocad.executor._publish_atomic", fail_second)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._rollback_published", retain_for_evidence
    )

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "output-invalid"
    assert "runtime publication reparse detected" in result.error.message
    assert rolled_back == [request.output_dir / "drawing.dwg"]
    assert "partial publication retained" in result.error.message


def test_verification_script_audits_and_emits_fixed_success_sentinel(
    fake_installation, tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(
        source, tmp_path / "output", tmp_path / "work", operations=("dxf-to-dwg",)
    )
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )
    calls = 0

    def fake_run(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        nonlocal calls
        calls += 1
        _write_fake_logs(stdout_path, stderr_path, verified=calls > 1)
        if calls == 1:
            _valid_dwg(cwd / "drawing.dwg")
        return _ProcessOutcome(0, False)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fake_run)

    result = execute_autocad(request)

    assert result.status == "succeeded", result
    assert (request.task_work_dir / "autocad" / "verify-dwg.scr").read_text(
        encoding="ascii"
    ).splitlines() == [
        "_.AUDIT",
        "_Yes",
        "_.QSAVE",
        '(princ "\\nCAD_SKILL_DWG_VERIFIED\\n")',
        "_.QUIT",
    ]


def test_zero_exit_without_exact_verification_sentinel_rejects_dwg(
    fake_installation, tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(
        source, tmp_path / "output", tmp_path / "work", operations=("dxf-to-dwg",)
    )
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )
    calls = 0

    def fake_run(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        nonlocal calls
        calls += 1
        _write_fake_logs(stdout_path, stderr_path, verified=False)
        if calls == 1:
            _valid_dwg(cwd / "drawing.dwg")
        return _ProcessOutcome(0, False)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fake_run)

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "output-invalid"
    assert "sentinel" in result.error.message
    assert not request.output_dir.exists()


@pytest.mark.parametrize(
    "payload",
    [b"not-a-dwg" * 20, b"AC1032"],
)
def test_dwg_requires_valid_header_and_nontrivial_size(
    fake_installation, tmp_path, monkeypatch, payload
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(
        source, tmp_path / "output", tmp_path / "work", operations=("dxf-to-dwg",)
    )
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )

    def fake_run(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        _write_fake_logs(stdout_path, stderr_path)
        (cwd / "drawing.dwg").write_bytes(payload)
        return _ProcessOutcome(0, False)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fake_run)

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "output-invalid"
    assert "DWG" in result.error.message


def test_dwg_requires_exact_autocad_2018_ac1032_header(
    fake_installation, tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(
        source, tmp_path / "output", tmp_path / "work", operations=("dxf-to-dwg",)
    )
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )
    calls = 0

    def fake_run(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        nonlocal calls
        calls += 1
        _write_fake_logs(stdout_path, stderr_path, verified=True)
        if calls == 1:
            (cwd / "drawing.dwg").write_bytes(b"AC1099" + bytes(range(128)))
        return _ProcessOutcome(0, False)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fake_run)

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "output-invalid"
    assert "AC1032" in result.error.message
    assert calls == 1
    assert not request.output_dir.exists()


def test_fatal_marker_in_zero_exit_log_rejects_outputs(
    fake_installation, tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )

    def fake_run(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        stdout_path.write_text("FATAL ERROR: drawing corrupt", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        _valid_dwg(cwd / "drawing.dwg")
        _valid_pdf(cwd / "drawing.pdf")
        return _ProcessOutcome(0, False)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fake_run)

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "process-failed"
    assert "fatal" in result.error.message.casefold()


def test_executable_replacement_after_discovery_is_rejected_before_launch(
    fake_installation, tmp_path, monkeypatch
):
    installation = _installation(fake_installation)
    fake_installation.core_console_exe.write_bytes(b"replacement executable")
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    launched = False
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )

    def must_not_launch(*args, **kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("identity drift must stop before Popen")

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", must_not_launch)

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "identity-drift"
    assert launched is False


def test_authenticode_is_revalidated_immediately_before_launch(
    fake_installation, tmp_path, monkeypatch
):
    installation = _installation(fake_installation)
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._verify_authenticode",
        lambda _path: SignatureEvidence("HashMismatch", "CN=Autodesk, Inc.", "BAD"),
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._run_process",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("untrusted executable must not launch")
        ),
    )

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "untrusted-executable"


def test_executable_handles_are_held_across_process_creation(
    fake_installation, tmp_path, monkeypatch
):
    installation = _installation(fake_installation)
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(
        source, tmp_path / "output", tmp_path / "work", operations=("plot-pdf",)
    )
    held = False
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )

    @contextmanager
    def fake_hold(_installation):
        nonlocal held
        held = True
        try:
            yield
        finally:
            held = False

    def fake_run(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        assert held is True
        _write_fake_logs(stdout_path, stderr_path)
        _valid_pdf(cwd / "drawing.pdf")
        return _ProcessOutcome(0, False)

    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._hold_trusted_executables", fake_hold
    )
    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fake_run)

    result = execute_autocad(request)

    assert result.status == "succeeded", result
    assert held is False


def test_atomic_publish_never_overwrites_racing_competitor(tmp_path, monkeypatch):
    source = tmp_path / "validated.dwg"
    _valid_dwg(source)
    output = tmp_path / "output"
    output.mkdir()
    destination = output / "drawing.dwg"
    parent_identity = _capture_directory_identity(output)
    real_link = os.link

    def racing_link(source_path, destination_path):
        Path(destination_path).write_bytes(b"competitor")
        return real_link(source_path, destination_path)

    monkeypatch.setattr("cad_skill_core.autocad.executor.os.link", racing_link)

    with pytest.raises(FileExistsError):
        _publish_atomic(source, destination, parent_identity)

    assert destination.read_bytes() == b"competitor"


def test_publish_rejects_destination_replaced_after_successful_hardlink(
    tmp_path, monkeypatch
):
    source = tmp_path / "validated.dwg"
    _valid_dwg(source)
    output = tmp_path / "output"
    output.mkdir()
    destination = output / "drawing.dwg"
    parent_identity = _capture_directory_identity(output)
    real_link = os.link

    def replace_after_link(source_path, destination_path):
        real_link(source_path, destination_path)
        Path(destination_path).unlink()
        Path(destination_path).write_bytes(b"competitor-after-link")

    monkeypatch.setattr("cad_skill_core.autocad.executor.os.link", replace_after_link)

    with pytest.raises(AutoCADExecutionError, match="identity"):
        _publish_atomic(source, destination, parent_identity)

    assert destination.read_bytes() == b"competitor-after-link"


def test_publish_reports_owned_output_when_post_link_identity_check_fails(
    tmp_path, monkeypatch
):
    source = tmp_path / "validated.dwg"
    _valid_dwg(source)
    output = tmp_path / "output"
    output.mkdir()
    destination = output / "drawing.dwg"
    parent_identity = _capture_directory_identity(output)
    real_capture = _capture_file_identity
    captures = 0

    def fail_destination_capture(path):
        nonlocal captures
        captures += 1
        if captures == 2:
            raise OSError("destination identity unavailable")
        return real_capture(path)

    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._capture_file_identity",
        fail_destination_capture,
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._delete_owned_file_by_handle",
        lambda _published: "owned-retained",
    )

    with pytest.raises(AutoCADExecutionError, match="partial publication retained"):
        _publish_atomic(source, destination, parent_identity)

    assert destination.is_file()


def test_successful_publication_survives_deferred_temp_hardlink_cleanup(
    fake_installation, tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(
        source,
        tmp_path / "output",
        tmp_path / "work",
        operations=("plot-pdf",),
    )
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad",
        lambda _path=None: installation,
    )

    def fake_run(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        _write_fake_logs(stdout_path, stderr_path)
        _valid_pdf(cwd / "drawing.pdf")
        return _ProcessOutcome(0, False)

    real_unlink = Path.unlink

    def deny_partial_cleanup(path, *args, **kwargs):
        if path.parent == request.output_dir and path.name.endswith(".partial"):
            raise PermissionError("temporary hardlink is still busy")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fake_run)
    monkeypatch.setattr(Path, "unlink", deny_partial_cleanup)

    result = execute_autocad(request)

    assert result.status == "succeeded", result
    assert result.pdf_path == request.output_dir / "drawing.pdf"
    assert result.pdf_path.is_file()
    assert any(
        "temporary publication hardlink cleanup was deferred" in item
        for item in result.evidence
    )


def test_later_publication_failure_rolls_back_owned_output_after_cleanup_warning(
    fake_installation, tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad",
        lambda _path=None: installation,
    )
    process_calls = 0

    def fake_run(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        nonlocal process_calls
        process_calls += 1
        _write_fake_logs(stdout_path, stderr_path, verified=process_calls > 1)
        if process_calls == 1:
            _valid_dwg(cwd / "drawing.dwg")
            _valid_pdf(cwd / "drawing.pdf")
        return _ProcessOutcome(0, False)

    real_publish = _publish_atomic
    publication_calls = 0

    def fail_second_publication(source_path, destination, parent_identity):
        nonlocal publication_calls
        publication_calls += 1
        if publication_calls == 2:
            raise AutoCADExecutionError("second publication failed")
        return real_publish(source_path, destination, parent_identity)

    real_unlink = Path.unlink

    def deny_partial_cleanup(path, *args, **kwargs):
        if path.parent == request.output_dir and path.name.endswith(".partial"):
            raise PermissionError("temporary hardlink is still busy")
        return real_unlink(path, *args, **kwargs)

    rollback_inputs = []

    def retain_owned_outputs(files):
        rollback_inputs.extend(item.path for item in files)
        return tuple(item.path for item in files)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fake_run)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._publish_atomic",
        fail_second_publication,
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._rollback_published",
        retain_owned_outputs,
    )
    monkeypatch.setattr(Path, "unlink", deny_partial_cleanup)

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "output-invalid"
    assert rollback_inputs == [request.output_dir / "drawing.dwg"]
    assert str(request.output_dir / "drawing.dwg") in result.error.message
    assert "partial publication retained" in result.error.message
    assert any(
        "temporary publication hardlink cleanup was deferred" in item
        for item in result.evidence
    )


def test_rollback_does_not_unlink_competitor_that_replaced_published_file(tmp_path):
    source = tmp_path / "validated.dwg"
    _valid_dwg(source)
    output = tmp_path / "output"
    output.mkdir()
    destination = output / "drawing.dwg"
    parent_identity = _capture_directory_identity(output)
    published = _publish_atomic(source, destination, parent_identity)
    destination.unlink()
    destination.write_bytes(b"competitor replacement")

    _rollback_published((published,))

    assert destination.read_bytes() == b"competitor replacement"


def test_rollback_does_not_delete_published_file_modified_in_place(
    tmp_path, monkeypatch
):
    source = tmp_path / "validated.dwg"
    _valid_dwg(source)
    output = tmp_path / "output"
    output.mkdir()
    destination = output / "drawing.dwg"
    parent_identity = _capture_directory_identity(output)
    published = _publish_atomic(source, destination, parent_identity)
    destination.write_bytes(b"modified in place")
    dispositions = []

    @contextmanager
    def fake_open(_path):
        yield 123

    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._open_delete_handle", fake_open
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._file_identity_from_handle",
        lambda _handle: published.identity,
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._set_delete_disposition",
        lambda handle: dispositions.append(handle),
    )

    _rollback_published((published,))

    assert dispositions == []
    assert destination.read_bytes() == b"modified in place"


def test_rollback_deletes_by_owned_handle_not_replaced_path(tmp_path, monkeypatch):
    source = tmp_path / "validated.dwg"
    _valid_dwg(source)
    output = tmp_path / "output"
    output.mkdir()
    destination = output / "drawing.dwg"
    parent_identity = _capture_directory_identity(output)
    published = _publish_atomic(source, destination, parent_identity)
    disposition_handles = []

    @contextmanager
    def fake_open(_path):
        yield 123

    def replace_after_handle_identity(_handle):
        destination.unlink()
        destination.write_bytes(b"competitor-after-handle-check")
        return published.identity

    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._open_delete_handle",
        fake_open,
        raising=False,
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._file_identity_from_handle",
        replace_after_handle_identity,
        raising=False,
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._set_delete_disposition",
        lambda handle: disposition_handles.append(handle),
        raising=False,
    )

    retained = _rollback_published((published,))

    assert destination.exists() and destination.read_bytes() == (
        b"competitor-after-handle-check"
    )
    assert disposition_handles == []
    assert retained == ()


def test_rollback_reports_owned_output_when_safe_handle_delete_fails(
    tmp_path, monkeypatch
):
    source = tmp_path / "validated.dwg"
    _valid_dwg(source)
    output = tmp_path / "output"
    output.mkdir()
    destination = output / "drawing.dwg"
    parent_identity = _capture_directory_identity(output)
    published = _publish_atomic(source, destination, parent_identity)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._delete_owned_file_by_handle",
        lambda _published: "owned-retained",
        raising=False,
    )

    retained = _rollback_published((published,))

    assert retained == (destination,)
    assert destination.is_file()


def test_rollback_reports_retained_output_when_runtime_reparse_check_fails(
    tmp_path, monkeypatch
):
    source = tmp_path / "validated.dwg"
    _valid_dwg(source)
    output = tmp_path / "output"
    output.mkdir()
    destination = output / "drawing.dwg"
    parent_identity = _capture_directory_identity(output)
    published = _publish_atomic(source, destination, parent_identity)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._validate_directory_identity",
        lambda _identity: (_ for _ in ()).throw(
            ValueError("runtime output reparse detected")
        ),
    )

    retained = _rollback_published((published,))

    assert retained == (destination,)
    assert destination.is_file()


def test_publish_rejects_output_directory_replacement_after_allocation(tmp_path):
    source = tmp_path / "validated.dwg"
    _valid_dwg(source)
    output = tmp_path / "output"
    output.mkdir()
    expected = _capture_directory_identity(output)
    output.rename(tmp_path / "old-output")
    output.mkdir()

    with pytest.raises(AutoCADExecutionError, match="directory identity"):
        _publish_atomic(source, output / "drawing.dwg", expected)

    assert not (output / "drawing.dwg").exists()


def test_work_directory_failure_returns_only_paths_that_exist(
    tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._prepare_directory",
        lambda _path: (_ for _ in ()).throw(PermissionError("work denied")),
    )

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "workdir-failed"
    assert result.script_path is None
    assert result.stdout_path is None
    assert result.stderr_path is None
    assert not any("retained" in item for item in result.evidence)


@pytest.mark.parametrize(
    "log_name",
    (
        "core-console.scr",
        "verify-dwg.scr",
        "stdout.log",
        "stderr.log",
        "verify-stdout.log",
        "verify-stderr.log",
    ),
)
def test_fixed_evidence_initialization_rejects_hardlink_after_directory_prepare(
    fake_installation, tmp_path, monkeypatch, log_name
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    installation = _installation(fake_installation)
    valuable = tmp_path / f"valuable-{log_name}"
    valuable_bytes = b"valuable external evidence must survive"
    valuable.write_bytes(valuable_bytes)
    real_prepare_directory = executor_module._prepare_directory
    injected_snapshot = {}

    def inject_hardlink_after_prepare(boundary):
        real_prepare_directory(boundary)
        target = boundary / log_name
        try:
            os.link(valuable, target)
        except OSError as error:
            pytest.skip(f"hard links are unavailable on this filesystem: {error}")
        assert target.stat().st_nlink >= 2
        injected_snapshot.update(
            (path.relative_to(boundary), path.read_bytes())
            for path in boundary.iterdir()
            if path.is_file()
        )

    runner_calls = []

    def must_not_run(*_args, **_kwargs):
        runner_calls.append(True)
        raise AssertionError("unsafe work evidence must fail before process launch")

    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._prepare_directory",
        inject_hardlink_after_prepare,
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad",
        lambda _path=None: installation,
    )
    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", must_not_run)

    result = execute_autocad(request)

    boundary = request.task_work_dir / "autocad"
    final_snapshot = {
        path.relative_to(boundary): path.read_bytes()
        for path in boundary.iterdir()
        if path.is_file()
    }
    assert result.status == "failed"
    assert result.error.code == "workdir-failed"
    assert runner_calls == []
    assert valuable.read_bytes() == valuable_bytes
    assert final_snapshot == injected_snapshot


@pytest.mark.parametrize(
    ("raced_name", "location"),
    (
        ("core-console.scr", "boundary"),
        ("input.dxf", "stage"),
        ("drawing.dwg", "stage"),
        ("drawing.pdf", "stage"),
    ),
)
def test_runner_boundary_rejects_hardlinked_inputs_and_outputs_before_launch(
    fake_installation, tmp_path, monkeypatch, raced_name, location
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    installation = _installation(fake_installation)
    valuable = tmp_path / f"valuable-{raced_name}"
    if raced_name == "input.dxf":
        _valid_dxf(valuable)
    else:
        valuable.write_bytes(b"valuable runner boundary file")
    valuable_bytes = valuable.read_bytes()

    def inject_before_launch(stage, _installation):
        target = (
            request.task_work_dir / "autocad" / raced_name
            if location == "boundary"
            else stage / raced_name
        )
        if target.exists():
            target.unlink()
        try:
            os.link(valuable, target)
        except OSError as error:
            pytest.skip(f"hard links are unavailable on this filesystem: {error}")
        assert target.stat().st_nlink >= 2
        return {"SystemRoot": r"C:\Windows"}

    runner_calls = []

    def must_not_run(*_args, **_kwargs):
        runner_calls.append(True)
        raise AssertionError("unsafe runner boundary must fail before process launch")

    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad",
        lambda _path=None: installation,
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._minimal_environment",
        inject_before_launch,
    )
    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", must_not_run)

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "process-failed"
    assert runner_calls == []
    assert valuable.read_bytes() == valuable_bytes


@pytest.mark.parametrize("raced_target", ("script", "input", "output"))
def test_runner_boundary_closes_post_validation_hardlink_window(
    fake_installation, tmp_path, monkeypatch, raced_target
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(
        source, tmp_path / "output", tmp_path / "work", operations=("plot-pdf",)
    )
    installation = _installation(fake_installation)
    valuable = tmp_path / f"valuable-{raced_target}"
    if raced_target == "input":
        _valid_dxf(valuable)
    else:
        valuable.write_bytes(b"valuable post-validation file")
    valuable_bytes = valuable.read_bytes()
    real_validate = executor_module._validate_runner_boundary

    def inject_after_validation(protected_inputs, absent_outputs):
        real_validate(protected_inputs, absent_outputs)
        target = {
            "script": protected_inputs[0],
            "input": protected_inputs[1],
            "output": absent_outputs[0],
        }[raced_target]
        if target.exists():
            target.unlink()
        try:
            os.link(valuable, target)
        except OSError as error:
            pytest.skip(f"hard links are unavailable on this filesystem: {error}")
        assert target.stat().st_nlink >= 2

    runner_calls = []

    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad",
        lambda _path=None: installation,
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._validate_runner_boundary",
        inject_after_validation,
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._run_process",
        lambda *_args, **_kwargs: runner_calls.append(True),
    )

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "process-failed"
    assert runner_calls == []
    assert valuable.read_bytes() == valuable_bytes


@pytest.mark.parametrize(
    ("output_name", "operations"),
    (
        ("drawing.dwg", ("dxf-to-dwg",)),
        ("drawing.pdf", ("plot-pdf",)),
    ),
)
def test_innermost_process_boundary_rechecks_outputs_after_runner_file_hold(
    fake_installation, tmp_path, monkeypatch, output_name, operations
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(
        source,
        tmp_path / "output",
        tmp_path / "work",
        operations=operations,
    )
    installation = _installation(fake_installation)
    valuable = tmp_path / f"valuable-{output_name}"
    valuable_bytes = b"valuable external runner output"
    valuable.write_bytes(valuable_bytes)
    real_hold = executor_module._hold_runner_files

    @contextmanager
    def inject_after_outer_output_checks(paths):
        with real_hold(paths):
            stage = next(path.parent for path in paths if path.name == "input.dxf")
            target = stage / output_name
            try:
                os.link(valuable, target)
            except OSError as error:
                pytest.skip(f"hard links are unavailable on this filesystem: {error}")
            assert target.stat().st_nlink >= 2
            yield

    popen_calls = []

    def must_not_popen(*_args, **_kwargs):
        popen_calls.append(True)
        raise AssertionError("unsafe output must fail before Popen")

    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad",
        lambda _path=None: installation,
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._hold_runner_files",
        inject_after_outer_output_checks,
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.subprocess.Popen",
        must_not_popen,
    )

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "process-failed"
    assert popen_calls == []
    assert valuable.read_bytes() == valuable_bytes


def test_staging_copy_failure_is_structured_with_precreated_logs(
    fake_installation, tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.shutil.copyfile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("copy denied")),
    )

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "staging-failed"
    assert result.stdout_path.is_file() and result.stdout_path.stat().st_size == 0
    assert result.stderr_path.is_file() and result.stderr_path.stat().st_size == 0
    assert result.script_path is None


def test_logs_exist_and_are_empty_before_subprocess_spawn(
    fake_installation, tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(
        source, tmp_path / "output", tmp_path / "work", operations=("plot-pdf",)
    )
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )

    def fake_run(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        assert stdout_path.is_file() and stdout_path.stat().st_size == 0
        assert stderr_path.is_file() and stderr_path.stat().st_size == 0
        _write_fake_logs(stdout_path, stderr_path)
        _valid_pdf(cwd / "drawing.pdf")
        return _ProcessOutcome(0, False)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fake_run)

    result = execute_autocad(request)

    assert result.status == "succeeded", result


def test_missing_system_root_is_a_structured_process_failure(
    fake_installation, tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )
    monkeypatch.delenv("SystemRoot", raising=False)
    monkeypatch.delenv("WINDIR", raising=False)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor._trusted_system_directory",
        lambda: (_ for _ in ()).throw(OSError("trusted system directory unavailable")),
        raising=False,
    )

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "process-failed"
    assert "trusted system directory" in result.error.message
    assert result.script_path.is_file()
    assert result.stdout_path.is_file()
    assert result.stderr_path.is_file()


def test_missing_process_log_is_a_structured_validation_failure(
    fake_installation, tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(
        source, tmp_path / "output", tmp_path / "work", operations=("plot-pdf",)
    )
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )

    def remove_log(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path):
        stdout_path.unlink()
        _valid_pdf(cwd / "drawing.pdf")
        return _ProcessOutcome(0, False)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", remove_log)

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "process-failed"
    assert "log" in result.error.message.casefold()
    assert result.stdout_path is None
    assert result.stderr_path.is_file()


def test_runtime_value_error_between_staging_and_spawn_is_structured(
    fake_installation, tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("runtime replace validation failed")
        ),
    )

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "process-failed"
    assert "runtime replace validation failed" in result.error.message
    assert result.script_path.is_file()
    assert result.stdout_path.is_file()
    assert result.stderr_path.is_file()


def test_unexpected_execute_phase_exception_never_escapes_boundary(
    fake_installation, tmp_path, monkeypatch
):
    source = _valid_dxf(tmp_path / "input.dxf")
    request = AutoCADRequest(source, tmp_path / "output", tmp_path / "work")
    installation = _installation(fake_installation)
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.discover_autocad", lambda _path=None: installation
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.executor.build_core_console_command",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("unexpected boundary fault")),
    )

    result = execute_autocad(request)

    assert result.status == "failed"
    assert result.error.code == "process-failed"
    assert "unexpected boundary fault" in result.error.message
