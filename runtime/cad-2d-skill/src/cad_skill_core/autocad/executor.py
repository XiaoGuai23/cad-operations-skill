"""Safe, headless AutoCAD Core Console execution and artifact publication."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import ctypes
import hashlib
import inspect
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path

import ezdxf
from pypdf import PdfReader

from .discovery import discover_autocad, revalidate_installation
from .models import (
    AutoCADErrorDetail,
    AutoCADExecutionError,
    AutoCADInaccessibleError,
    AutoCADInstallation,
    AutoCADNotInstalledError,
    AutoCADRequest,
    AutoCADResult,
    AutoCADUntrustedError,
    AutoCADWrongVersionError,
    _reject_reparse_components,
)
from .system_tools import trusted_system_directory as _trusted_system_directory


_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[3] / "templates" / "autocad" / "convert-and-plot.scr.j2"
)
_PRESERVED_ENVIRONMENT = {
    "PATHEXT",
    "PROGRAMDATA",
    "APPDATA",
    "LOCALAPPDATA",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
}
_VERIFY_SENTINEL = "CAD_SKILL_DWG_VERIFIED"
_VERIFY_SCRIPT = (
    "_.AUDIT\n_Yes\n_.QSAVE\n"
    '(princ "\\nCAD_SKILL_DWG_VERIFIED\\n")\n'
    "_.QUIT\n"
)


@dataclass(frozen=True, slots=True)
class _ProcessOutcome:
    return_code: int
    timed_out: bool


@dataclass(frozen=True, slots=True)
class _DirectoryIdentity:
    path: Path
    device: int
    file_id: int


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    volume: int
    file_id: int
    size: int
    mtime: int


@dataclass(frozen=True, slots=True)
class _PublishedFile:
    path: Path
    parent_identity: _DirectoryIdentity
    identity: _FileIdentity
    sha256: str
    cleanup_warnings: tuple[str, ...] = ()


def build_core_console_command(
    installation: AutoCADInstallation, request: AutoCADRequest
) -> list[str]:
    """Build a literal argv list; no shell quoting or interpolation is used."""

    script_path = request.task_work_dir / "autocad" / "core-console.scr"
    return [
        str(installation.core_console_exe),
        "/i",
        str(request.input_dxf),
        "/s",
        str(script_path),
        "/l",
        "en-US",
    ]


def execute_autocad(request: AutoCADRequest) -> AutoCADResult:
    """Return a structured result for every non-cancellation boundary failure."""
    started = time.monotonic()
    try:
        return _execute_autocad(request, started)
    except Exception as error:
        boundary = request.task_work_dir / "autocad"
        return _failure_result(
            request,
            started,
            None,
            boundary / "core-console.scr",
            boundary / "stdout.log",
            boundary / "stderr.log",
            "process-failed",
            f"unexpected AutoCAD execution boundary failure: {error}",
        )


def _execute_autocad(request: AutoCADRequest, started: float) -> AutoCADResult:
    """Convert and/or plot a validated DXF with Core Console only."""

    boundary = request.task_work_dir / "autocad"
    script_path = boundary / "core-console.scr"
    stdout_path = boundary / "stdout.log"
    stderr_path = boundary / "stderr.log"
    verify_script_path = boundary / "verify-dwg.scr"
    verify_stdout_path = boundary / "verify-stdout.log"
    verify_stderr_path = boundary / "verify-stderr.log"
    fixed_evidence_paths = (
        script_path,
        verify_script_path,
        stdout_path,
        stderr_path,
        verify_stdout_path,
        verify_stderr_path,
    )
    try:
        _reject_reparse_components(request.task_work_dir, "task_work_dir")
        _prepare_directory(boundary)
        _replace_evidence_files_atomic(
            tuple((path, b"") for path in fixed_evidence_paths)
        )
    except (OSError, ValueError, AutoCADExecutionError) as error:
        return _failure_result(
            request, started, None, None, None, None,
            "workdir-failed", f"could not initialize AutoCAD task evidence: {error}",
        )

    try:
        _validate_request(request)
    except (OSError, ValueError, AutoCADExecutionError) as error:
        return _failure_result(
            request, started, None, None, stdout_path, stderr_path,
            "invalid-request", str(error),
        )
    try:
        installation = discover_autocad(request.configured_path)
    except AutoCADNotInstalledError as error:
        return AutoCADResult(
            "skipped", None, None, None, None, stdout_path, stderr_path,
            time.monotonic() - started, error.detail,
            ("AutoCAD 2023 Core Console was not available; no DWG/PDF was claimed",),
        )
    except (
        AutoCADWrongVersionError,
        AutoCADInaccessibleError,
        AutoCADUntrustedError,
    ) as error:
        return AutoCADResult(
            "failed", None, None, None, None, stdout_path, stderr_path,
            time.monotonic() - started, error.detail,
            ("configured AutoCAD installation did not pass exact 2023 validation",),
        )

    try:
        stage = Path(tempfile.mkdtemp(prefix="run-", dir=boundary))
        staged_input = stage / "input.dxf"
        shutil.copyfile(request.input_dxf, staged_input)
        _validate_dxf(staged_input, "staged DXF is malformed")
        _write_ascii_atomic(script_path, _render_script(request))
        if "dxf-to-dwg" in request.operations:
            _write_ascii_atomic(verify_script_path, _VERIFY_SCRIPT)
    except (OSError, ValueError, AutoCADExecutionError) as error:
        return _failure_result(
            request, started, None, script_path, stdout_path, stderr_path,
            "staging-failed", f"could not stage AutoCAD request: {error}",
        )

    staged_request = replace(request, input_dxf=staged_input)
    command = build_core_console_command(installation, staged_request)
    try:
        environment = _minimal_environment(stage, installation)
        outcome = _run_trusted_process(
            installation,
            command,
            protected_inputs=(script_path, staged_input),
            absent_outputs=tuple(
                stage / name
                for operation, name in (
                    ("dxf-to-dwg", "drawing.dwg"),
                    ("plot-pdf", "drawing.pdf"),
                )
                if operation in request.operations
            ),
            cwd=stage,
            env=environment,
            timeout_seconds=request.timeout_seconds,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
    except AutoCADUntrustedError as error:
        return _failure_result(
            request, started, None, script_path, stdout_path, stderr_path,
            error.detail.code, error.detail.message,
        )
    except (OSError, ValueError, AutoCADExecutionError, subprocess.SubprocessError) as error:
        return _failure_result(
            request, started, None, script_path, stdout_path, stderr_path,
            "process-failed", f"could not start AutoCAD Core Console: {error}",
        )
    if outcome.timed_out:
        return _failure_result(
            request, started, outcome.return_code, script_path, stdout_path, stderr_path,
            "timeout", f"AutoCAD Core Console exceeded {request.timeout_seconds} seconds",
        )
    if outcome.return_code != 0:
        return _failure_result(
            request, started, outcome.return_code, script_path, stdout_path, stderr_path,
            "process-failed", f"AutoCAD Core Console exited with code {outcome.return_code}",
        )
    try:
        _validate_process_logs(stdout_path, stderr_path)
    except (OSError, ValueError, AutoCADExecutionError) as error:
        return _failure_result(
            request, started, outcome.return_code, script_path, stdout_path, stderr_path,
            "process-failed", f"could not validate AutoCAD process logs: {error}",
        )

    staged_dwg = stage / "drawing.dwg"
    staged_pdf = stage / "drawing.pdf"
    try:
        if "dxf-to-dwg" in request.operations:
            _validate_dwg(staged_dwg)
            verify_command = [
                str(installation.core_console_exe),
                "/i",
                str(staged_dwg),
                "/s",
                str(verify_script_path),
                "/l",
                "en-US",
            ]
            verification = _run_trusted_process(
                installation,
                verify_command,
                protected_inputs=(verify_script_path, staged_dwg),
                absent_outputs=(),
                cwd=stage,
                env=environment,
                timeout_seconds=request.timeout_seconds,
                stdout_path=verify_stdout_path,
                stderr_path=verify_stderr_path,
            )
            if verification.timed_out:
                return _failure_result(
                    request, started, verification.return_code, script_path,
                    stdout_path, stderr_path, "timeout",
                    "AutoCAD timed out while reopening the generated DWG",
                )
            if verification.return_code != 0:
                return _failure_result(
                    request, started, verification.return_code, script_path,
                    stdout_path, stderr_path, "output-invalid",
                    "AutoCAD could not reopen the generated DWG",
                )
            _validate_process_logs(
                verify_stdout_path,
                verify_stderr_path,
                required_sentinel=_VERIFY_SENTINEL,
            )
        if "plot-pdf" in request.operations:
            _validate_pdf(staged_pdf)
    except AutoCADUntrustedError as error:
        return _failure_result(
            request, started, outcome.return_code, script_path, stdout_path, stderr_path,
            error.detail.code, error.detail.message,
        )
    except (OSError, ValueError, AutoCADExecutionError) as error:
        return _failure_result(
            request, started, outcome.return_code, script_path, stdout_path, stderr_path,
            "output-invalid", str(error),
        )

    try:
        requested_dwg, requested_pdf, output_parent_identity = _allocate_outputs(request)
    except (OSError, ValueError, AutoCADExecutionError) as error:
        return _failure_result(
            request, started, outcome.return_code, script_path, stdout_path, stderr_path,
            "output-invalid", f"could not allocate AutoCAD outputs: {error}",
        )
    published: list[_PublishedFile] = []
    publication_warnings: list[str] = []
    try:
        if requested_dwg is not None:
            publication = _publish_atomic(
                staged_dwg, requested_dwg, output_parent_identity
            )
            published.append(publication)
            publication_warnings.extend(publication.cleanup_warnings)
        if requested_pdf is not None:
            publication = _publish_atomic(
                staged_pdf, requested_pdf, output_parent_identity
            )
            published.append(publication)
            publication_warnings.extend(publication.cleanup_warnings)
    except (OSError, ValueError, AutoCADExecutionError) as error:
        retained = _rollback_published(tuple(published))
        partial = (
            "; partial publication retained because handle-bound rollback "
            f"was unavailable: {', '.join(str(path) for path in retained)}"
            if retained
            else ""
        )
        return _failure_result(
            request, started, outcome.return_code, script_path, stdout_path, stderr_path,
            "output-invalid",
            f"could not atomically publish AutoCAD outputs: {error}{partial}",
            tuple(publication_warnings),
        )

    evidence = ["source DXF reopened and passed ezdxf audit before execution"]
    evidence.extend(publication_warnings)
    if requested_dwg is not None:
        evidence.append(
            f"DWG was non-empty and reopened by AutoCAD Core Console; logs: {verify_stdout_path}, {verify_stderr_path}"
        )
    if requested_pdf is not None:
        evidence.append("PDF header and at least one readable page were validated")
    return AutoCADResult(
        "succeeded",
        outcome.return_code,
        requested_dwg,
        requested_pdf,
        script_path,
        stdout_path,
        stderr_path,
        time.monotonic() - started,
        None,
        tuple(evidence),
    )


def _validate_request(request: AutoCADRequest) -> None:
    if request.input_dxf.suffix.casefold() != ".dxf":
        raise AutoCADExecutionError("AutoCAD input must use the .dxf extension")
    if not request.input_dxf.is_file():
        raise AutoCADExecutionError(f"missing input DXF: {request.input_dxf}")
    _validate_dxf(request.input_dxf, "malformed input DXF")
    for root, label in (
        (request.output_dir, "output_dir"),
        (request.task_work_dir, "task_work_dir"),
    ):
        try:
            _reject_reparse_components(root, label)
        except ValueError as error:
            raise AutoCADExecutionError(str(error)) from error
    for root, label in (
        (request.output_dir, "output_dir"),
        (request.task_work_dir, "task_work_dir"),
    ):
        if root.exists() and not root.is_dir():
            raise AutoCADExecutionError(f"{label} is not a directory: {root}")
    base_dwg = request.output_dir / f"{request.drawing_stem}.dwg"
    base_pdf = request.output_dir / f"{request.drawing_stem}.pdf"
    for candidate in (base_dwg, base_pdf):
        if candidate.exists() and _same_file(candidate, request.input_dxf):
            raise AutoCADExecutionError(f"output path aliases source DXF: {candidate}")
        if not _is_within(candidate, request.output_dir):
            raise AutoCADExecutionError(f"output path escapes output_dir: {candidate}")


def _validate_dxf(path: Path, message: str) -> None:
    try:
        document = ezdxf.readfile(path)
        audit = document.audit()
    except Exception as error:
        raise AutoCADExecutionError(f"{message}: {path}: {error}") from error
    if audit.has_errors:
        raise AutoCADExecutionError(f"{message}: ezdxf audit reported errors: {path}")


def _render_script(request: AutoCADRequest) -> str:
    try:
        template = _TEMPLATE_PATH.read_text(encoding="ascii")
    except OSError as error:
        raise AutoCADExecutionError(f"AutoCAD script template is unavailable: {error}") from error
    audit = "_.AUDIT\n_Yes\n_.QSAVE\n"
    save = "_.SAVEAS\n2018\ndrawing.dwg\n" if "dxf-to-dwg" in request.operations else ""
    plot = (
        f"_.-PLOT\n_No\n{request.plot_layout}\n\nDWG To PDF.pc3\n"
        "_Yes\ndrawing.pdf\n_No\n_Yes\n"
        if "plot-pdf" in request.operations
        else ""
    )
    rendered = (
        template.replace("{{AUDIT_BLOCK}}", audit)
        .replace("{{SAVE_DWG_BLOCK}}", save)
        .replace("{{PLOT_PDF_BLOCK}}", plot)
    )
    expected = audit + save + plot + "_.QUIT\n"
    if rendered != expected:
        raise AutoCADExecutionError("generated AutoCAD script violated command allowlist")
    commands = tuple(line for line in rendered.splitlines() if line.startswith("_."))
    if any(
        command
        not in {"_.AUDIT", "_.QSAVE", "_.SAVEAS", "_.-PLOT", "_.QUIT"}
        for command in commands
    ):
        raise AutoCADExecutionError("generated AutoCAD script violated command allowlist")
    return rendered


def _prepare_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise AutoCADExecutionError(f"could not create task work directory: {path}")


def _minimal_environment(
    stage: Path, installation: AutoCADInstallation
) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in _PRESERVED_ENVIRONMENT
        or key.upper().startswith("ADSKFLEX_")
        or key.upper().startswith("ADLM_")
        or key.upper() == "FLEXLM_TIMEOUT"
    }
    system_directory = _trusted_system_directory()
    system_root = system_directory.parent
    trusted_path: list[str] = []
    for directory in (
        system_directory,
        installation.core_console_exe.parent,
        installation.acad_exe.parent,
    ):
        value = str(directory)
        if value.casefold() not in {item.casefold() for item in trusted_path}:
            trusted_path.append(value)
    environment["PATH"] = os.pathsep.join(trusted_path)
    environment["SystemRoot"] = str(system_root)
    environment["WINDIR"] = str(system_root)
    environment["COMSPEC"] = str(system_directory / "cmd.exe")
    environment["TEMP"] = str(stage)
    environment["TMP"] = str(stage)
    return environment


def _run_process(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: float,
    stdout_path: Path,
    stderr_path: Path,
    absent_outputs: tuple[Path, ...] = (),
) -> _ProcessOutcome:
    _validate_evidence_targets((stdout_path, stderr_path))
    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    _validate_runner_outputs_absent(absent_outputs)
    process = subprocess.Popen(
        list(argv),
        shell=False,
        cwd=str(cwd),
        env=dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_tree(process, env)
        try:
            stdout, stderr = process.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except (OSError, subprocess.SubprocessError):
                pass
            _close_process_pipes(process)
            stdout, stderr = b"", b""
    _replace_evidence_files_atomic(
        (
            (stdout_path, stdout or b""),
            (stderr_path, stderr or b""),
        )
    )
    return _ProcessOutcome(
        int(process.returncode) if process.returncode is not None else -9,
        timed_out,
    )


def _run_trusted_process(
    installation: AutoCADInstallation,
    argv: list[str],
    *,
    protected_inputs: tuple[Path, ...],
    absent_outputs: tuple[Path, ...],
    **kwargs,
) -> _ProcessOutcome:
    with _hold_trusted_executables(installation):
        revalidate_installation(installation)
        _validate_runner_boundary(protected_inputs, absent_outputs)
        _validate_evidence_targets(protected_inputs)
        _validate_runner_outputs_absent(absent_outputs)
        with _hold_runner_files(protected_inputs):
            run_kwargs = dict(kwargs)
            if "absent_outputs" in inspect.signature(_run_process).parameters:
                run_kwargs["absent_outputs"] = absent_outputs
            return _run_process(argv, **run_kwargs)


def _validate_runner_boundary(
    protected_inputs: tuple[Path, ...], absent_outputs: tuple[Path, ...]
) -> None:
    _validate_evidence_targets(protected_inputs)
    _validate_runner_outputs_absent(absent_outputs)


def _validate_runner_outputs_absent(paths: tuple[Path, ...]) -> None:
    for path in paths:
        _reject_reparse_components(path, "AutoCAD runner output")
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        raise AutoCADExecutionError(
            f"AutoCAD runner output target already exists before launch: {path}"
        )


@contextmanager
def _hold_runner_files(paths: tuple[Path, ...]):
    """Prevent checked runner files from being replaced until the child exits."""

    _validate_evidence_targets(paths)
    expected = {path: _capture_file_identity(path) for path in paths}
    with ExitStack() as stack:
        if os.name == "nt":
            handles = {
                path: stack.enter_context(
                    _open_windows_handle(
                        path,
                        0x00000080,
                        share_mode=0x00000001 | 0x00000002,
                    )
                )
                for path in paths
            }
            for path, handle in handles.items():
                if _file_identity_from_handle(handle) != expected[path]:
                    raise AutoCADExecutionError(
                        f"AutoCAD runner file identity changed before launch: {path}"
                    )
        else:
            for path in paths:
                stack.enter_context(path.open("rb"))
                if _capture_file_identity(path) != expected[path]:
                    raise AutoCADExecutionError(
                        f"AutoCAD runner file identity changed before launch: {path}"
                    )
        _validate_evidence_targets(paths)
        yield


@contextmanager
def _hold_trusted_executables(installation: AutoCADInstallation):
    """Deny executable write/delete sharing across validation and Popen."""

    paths = (installation.core_console_exe, installation.acad_exe)
    if os.name != "nt":
        streams = [path.open("rb") for path in paths]
        try:
            yield
        finally:
            for stream in streams:
                stream.close()
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    handles: list[int] = []
    invalid = ctypes.c_void_p(-1).value
    try:
        for path in paths:
            handle = create_file(
                str(path),
                0x80000000,
                0x00000001,
                None,
                3,
                0x00000080,
                None,
            )
            if handle in {None, invalid}:
                raise OSError(
                    ctypes.get_last_error(),
                    f"could not lock trusted executable against replacement: {path}",
                )
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles):
            close_handle(handle)


def _terminate_process_tree(process: subprocess.Popen, env: dict[str, str]) -> None:
    if os.name == "nt":
        try:
            system_directory = _trusted_system_directory()
            system_root = system_directory.parent
            taskkill = system_directory / "taskkill.exe"
            taskkill_environment = {
                "SystemRoot": str(system_root),
                "WINDIR": str(system_root),
                "PATH": str(system_directory),
            }
            result = subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                shell=False,
                env=taskkill_environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
                check=False,
            )
            if result.returncode != 0:
                process.kill()
        except (OSError, subprocess.SubprocessError):
            process.kill()
    else:
        process.kill()


def _close_process_pipes(process: subprocess.Popen) -> None:
    for pipe_name in ("stdout", "stderr", "stdin"):
        pipe = getattr(process, pipe_name, None)
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass


def _validate_nonempty(path: Path, label: str) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"generated {label} is missing or empty: {path}")


def _validate_dwg(path: Path) -> None:
    _validate_nonempty(path, "DWG")
    if path.stat().st_size < 64:
        raise ValueError(f"generated DWG is too small to be valid: {path}")
    header = path.read_bytes()[:6]
    if header != b"AC1032":
        raise ValueError(
            f"generated DWG is not the requested AutoCAD 2018 AC1032 format: {path}"
        )


def _validate_process_logs(
    stdout_path: Path,
    stderr_path: Path,
    *,
    required_sentinel: str | None = None,
) -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (stdout_path, stderr_path)
    )
    bad_marker = re.search(
        r"\b(?:fatal(?:\s+error)?|unhandled|exception)\b|(?:^|\s)error\s*[:!]",
        combined,
        re.IGNORECASE | re.MULTILINE,
    )
    if bad_marker is not None:
        raise ValueError(f"AutoCAD log contains fatal/error marker: {bad_marker.group(0).strip()}")
    if required_sentinel is not None and required_sentinel not in combined.splitlines():
        raise ValueError(
            f"AutoCAD verification log lacks exact success sentinel {required_sentinel}"
        )


def _validate_pdf(path: Path) -> None:
    _validate_nonempty(path, "PDF")
    if not path.read_bytes()[:5] == b"%PDF-":
        raise ValueError(f"generated PDF has no PDF magic header: {path}")
    try:
        reader = PdfReader(path)
        page_count = len(reader.pages)
    except Exception as error:
        raise ValueError(f"generated PDF is unreadable: {path}: {error}") from error
    if page_count < 1:
        raise ValueError(f"generated PDF contains no pages: {path}")


def _allocate_outputs(
    request: AutoCADRequest,
) -> tuple[Path | None, Path | None, _DirectoryIdentity]:
    for root, label in (
        (request.output_dir, "output_dir"),
        (request.task_work_dir, "task_work_dir"),
    ):
        _reject_reparse_components(root, label)
    request.output_dir.mkdir(parents=True, exist_ok=True)
    parent_identity = _capture_directory_identity(request.output_dir)
    version = 0
    while True:
        suffix = "" if version == 0 else f"-v{version:02d}"
        dwg = (
            request.output_dir / f"{request.drawing_stem}{suffix}.dwg"
            if "dxf-to-dwg" in request.operations
            else None
        )
        pdf = (
            request.output_dir / f"{request.drawing_stem}{suffix}.pdf"
            if "plot-pdf" in request.operations
            else None
        )
        if all(path is None or not path.exists() for path in (dwg, pdf)):
            return dwg, pdf, parent_identity
        version += 1
        if version > 9999:
            raise OSError("could not allocate a free versioned AutoCAD output pair")


def _publish_atomic(
    source: Path,
    destination: Path,
    parent_identity: _DirectoryIdentity,
) -> _PublishedFile:
    if destination.parent.resolve(strict=False) != parent_identity.path:
        raise AutoCADExecutionError("output destination escapes recorded directory identity")
    with _hold_output_directory(parent_identity.path):
        _validate_directory_identity(parent_identity)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        published: _PublishedFile | None = None
        cleanup_warnings: list[str] = []
        try:
            shutil.copyfile(source, temporary)
            with temporary.open("rb+") as stream:
                os.fsync(stream.fileno())
            _validate_directory_identity(parent_identity)
            candidate = _PublishedFile(
                destination,
                parent_identity,
                _capture_file_identity(temporary),
                _sha256_file(temporary),
            )
            os.link(temporary, destination)
            try:
                current_identity = _capture_file_identity(destination)
            except (OSError, ValueError, AutoCADExecutionError) as error:
                partial = _cleanup_failed_publication(candidate)
                raise AutoCADExecutionError(
                    "could not verify published destination identity: "
                    f"{destination}{partial}"
                ) from error
            if current_identity != candidate.identity:
                partial = _cleanup_failed_publication(candidate)
                raise AutoCADExecutionError(
                    "published destination identity does not match the validated "
                    f"temporary file: {destination}{partial}"
                )
            published = candidate
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                cleanup_warnings.append(
                    "temporary publication hardlink cleanup was deferred: "
                    f"{temporary}: {error}"
                )
        if published is None:
            raise AutoCADExecutionError(
                "publication completed without recording destination ownership"
            )
        return replace(published, cleanup_warnings=tuple(cleanup_warnings))


def _capture_directory_identity(path: Path) -> _DirectoryIdentity:
    _reject_reparse_components(path, "output_dir")
    metadata = path.stat()
    if not path.is_dir():
        raise AutoCADExecutionError(f"output path is not a directory: {path}")
    return _DirectoryIdentity(
        path.resolve(strict=True), int(metadata.st_dev), int(metadata.st_ino)
    )


def _validate_directory_identity(expected: _DirectoryIdentity) -> None:
    _reject_reparse_components(expected.path, "output_dir")
    try:
        current = _capture_directory_identity(expected.path)
    except OSError as error:
        raise AutoCADExecutionError(
            f"output directory identity is inaccessible: {expected.path}: {error}"
        ) from error
    if current != expected:
        raise AutoCADExecutionError(
            f"output directory identity changed before publication: {expected.path}"
        )


def _rollback_published(files: tuple[_PublishedFile, ...]) -> tuple[Path, ...]:
    retained: list[Path] = []
    for published in reversed(files):
        try:
            _validate_directory_identity(published.parent_identity)
            outcome = _delete_owned_file_by_handle(published)
            if outcome in {"owned-retained", "unverified"}:
                retained.append(published.path)
        except (OSError, ValueError, AutoCADExecutionError):
            retained.append(published.path)
    return tuple(retained)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cleanup_failed_publication(published: _PublishedFile) -> str:
    try:
        outcome = _delete_owned_file_by_handle(published)
    except (OSError, ValueError, AutoCADExecutionError):
        outcome = "unverified"
    if outcome in {"owned-retained", "unverified"}:
        return (
            "; partial publication retained because handle-bound cleanup was "
            f"unavailable: {published.path}"
        )
    return ""


def _capture_file_identity(path: Path) -> _FileIdentity:
    if os.name == "nt":
        with _open_windows_handle(path, 0x00000080) as handle:
            return _file_identity_from_handle(handle)
    metadata = path.stat()
    return _FileIdentity(
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
    )


def _delete_owned_file_by_handle(published: _PublishedFile) -> str:
    """Delete only the file object opened and proven to be process-owned."""

    if os.name != "nt":
        return "unverified"
    try:
        with _open_delete_handle(published.path) as handle:
            if _file_identity_from_handle(handle) != published.identity:
                return "not-owned"
            try:
                current_sha256 = _sha256_file(published.path)
            except OSError:
                return "unverified"
            if current_sha256 != published.sha256:
                return "not-owned"
            try:
                _set_delete_disposition(handle)
            except OSError:
                return "owned-retained"
            return "deleted"
    except OSError:
        return "unverified"


def _delete_temporary_by_handle(
    path: Path, expected_identity: _FileIdentity, expected_sha256: str
) -> str:
    """Delete only the uniquely-created temporary inode, never a path substitute."""

    if os.name != "nt":
        return "unverified"
    try:
        if _sha256_file(path) != expected_sha256:
            return "not-owned"
        with _open_delete_handle(path) as handle:
            if _file_identity_from_handle(handle) != expected_identity:
                return "not-owned"
            try:
                _set_delete_disposition(handle)
            except OSError:
                return "owned-retained"
            return "deleted"
    except OSError:
        return "unverified"


@contextmanager
def _open_delete_handle(path: Path):
    with _open_windows_handle(
        path,
        0x00010000 | 0x00000080,
        share_mode=0x00000001,
    ) as handle:
        yield handle


@contextmanager
def _open_windows_handle(
    path: Path, desired_access: int, *, share_mode: int | None = None
):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    handle = create_file(
        str(path),
        desired_access,
        (
            0x00000001 | 0x00000002 | 0x00000004
            if share_mode is None
            else share_mode
        ),
        None,
        3,
        0x00000080 | 0x00200000,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in {None, invalid}:
        raise OSError(ctypes.get_last_error(), f"could not open file by handle: {path}")
    try:
        yield handle
    finally:
        close_handle(handle)


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("attributes", ctypes.c_uint32),
        ("creation_time", _FileTime),
        ("last_access_time", _FileTime),
        ("last_write_time", _FileTime),
        ("volume_serial", ctypes.c_uint32),
        ("size_high", ctypes.c_uint32),
        ("size_low", ctypes.c_uint32),
        ("link_count", ctypes.c_uint32),
        ("file_index_high", ctypes.c_uint32),
        ("file_index_low", ctypes.c_uint32),
    ]


def _file_identity_from_handle(handle: int) -> _FileIdentity:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ByHandleFileInformation)]
    get_information.restype = ctypes.c_int
    information = _ByHandleFileInformation()
    if not get_information(handle, ctypes.byref(information)):
        raise OSError(ctypes.get_last_error(), "GetFileInformationByHandle failed")
    return _FileIdentity(
        int(information.volume_serial),
        (int(information.file_index_high) << 32) | int(information.file_index_low),
        (int(information.size_high) << 32) | int(information.size_low),
        (int(information.last_write_time.high) << 32)
        | int(information.last_write_time.low),
    )


class _FileDispositionInformation(ctypes.Structure):
    _fields_ = [("delete_file", ctypes.c_int)]


def _set_delete_disposition(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    set_information.restype = ctypes.c_int
    disposition = _FileDispositionInformation(1)
    if not set_information(
        handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)
    ):
        raise OSError(ctypes.get_last_error(), "SetFileInformationByHandle failed")


@contextmanager
def _hold_output_directory(path: Path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            yield descriptor
        finally:
            os.close(descriptor)
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    handle = create_file(
        str(path),
        0,
        0x00000001 | 0x00000002,
        None,
        3,
        0x02000000,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in {None, invalid}:
        raise OSError(
            ctypes.get_last_error(),
            f"could not hold output directory against replacement: {path}",
        )
    try:
        yield handle
    finally:
        close_handle(handle)


def _directory_path_from_handle(handle: int) -> Path | None:
    """Resolve a held directory after a concurrent rename for temp cleanup."""

    if os.name != "nt":
        try:
            return Path(os.readlink(f"/proc/self/fd/{handle}"))
        except OSError:
            return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_name = kernel32.GetFinalPathNameByHandleW
    get_name.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    get_name.restype = ctypes.c_uint32
    required = get_name(handle, None, 0, 0)
    if required == 0:
        return None
    buffer = ctypes.create_unicode_buffer(required + 1)
    written = get_name(handle, buffer, len(buffer), 0)
    if written == 0 or written >= len(buffer):
        return None
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def _validate_evidence_parent_identity(expected: _DirectoryIdentity) -> None:
    """Recheck an evidence parent without relying on an injected validation hook."""

    try:
        current = _capture_directory_identity(expected.path)
    except (OSError, ValueError, AutoCADExecutionError) as error:
        raise AutoCADExecutionError(
            f"AutoCAD evidence parent changed during atomic replacement: {expected.path}"
        ) from error
    if current != expected:
        raise AutoCADExecutionError(
            f"AutoCAD evidence parent changed during atomic replacement: {expected.path}"
        )


def _write_ascii_atomic(path: Path, content: str) -> None:
    _replace_evidence_files_atomic(((path, content.encode("ascii")),))


def _validate_evidence_target(path: Path) -> None:
    """Reject aliases and special files before replacing task evidence."""

    _reject_reparse_components(path, "AutoCAD evidence path")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(metadata.st_mode):
        raise AutoCADExecutionError(
            f"AutoCAD evidence target is not a regular file: {path}"
        )
    if metadata.st_nlink != 1:
        raise AutoCADExecutionError(
            f"AutoCAD evidence target has multiple hard links: {path}"
        )


def _validate_evidence_targets(paths: tuple[Path, ...]) -> None:
    for path in paths:
        _validate_evidence_target(path)


def _replace_evidence_files_atomic(
    replacements: tuple[tuple[Path, bytes], ...],
) -> None:
    """Replace evidence entries without opening or truncating existing inodes."""

    paths = tuple(path for path, _payload in replacements)
    _validate_evidence_targets(paths)
    parents = {
        path.parent: _capture_directory_identity(path.parent) for path in paths
    }
    temporary_paths: list[Path] = []
    temporary_identities: dict[Path, _FileIdentity] = {}
    temporary_hashes: dict[Path, str] = {}
    pending_windows_cleanup: list[tuple[Path, _FileIdentity, str]] = []
    try:
        with ExitStack() as parent_holds:
            held_parents = {
                parent.path: parent_holds.enter_context(
                    _hold_output_directory(parent.path)
                )
                for parent in parents.values()
            }
            try:
                for parent in parents.values():
                    _validate_directory_identity(parent)
                    _validate_evidence_parent_identity(parent)

                for path, payload in replacements:
                    descriptor, temporary_name = tempfile.mkstemp(
                        prefix=f".{path.name}-", suffix=".partial", dir=path.parent
                    )
                    temporary = Path(temporary_name)
                    temporary_paths.append(temporary)
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                    temporary_identities[temporary] = _capture_file_identity(temporary)
                    temporary_hashes[temporary] = hashlib.sha256(payload).hexdigest()

                _validate_evidence_targets(paths)
                for parent in parents.values():
                    _validate_directory_identity(parent)
                    _validate_evidence_parent_identity(parent)
                for temporary, path in zip(temporary_paths, paths, strict=True):
                    try:
                        os.replace(temporary, path)
                    except OSError as error:
                        try:
                            _validate_directory_identity(parents[path.parent])
                        except (OSError, ValueError, AutoCADExecutionError):
                            raise AutoCADExecutionError(
                                "AutoCAD evidence parent changed during atomic "
                                f"replacement: {path.parent}"
                            ) from error
                        raise
                    _validate_directory_identity(parents[path.parent])
                    _validate_evidence_parent_identity(parents[path.parent])
            finally:
                for temporary in temporary_paths:
                    handle = held_parents[temporary.parent]
                    if os.name != "nt":
                        try:
                            os.unlink(temporary.name, dir_fd=handle)
                        except FileNotFoundError:
                            pass
                        continue
                    relocated_parent = _directory_path_from_handle(handle)
                    identity = temporary_identities.get(temporary)
                    digest = temporary_hashes.get(temporary)
                    if relocated_parent is None or identity is None or digest is None:
                        continue
                    pending_windows_cleanup.append(
                        (
                            relocated_parent / temporary.name,
                            identity,
                            digest,
                        )
                    )
    finally:
        if os.name == "nt":
            for temporary, identity, digest in pending_windows_cleanup:
                _delete_temporary_by_handle(temporary, identity, digest)


def _failure_result(
    request: AutoCADRequest,
    started: float,
    return_code: int | None,
    script_path: Path | None,
    stdout_path: Path | None,
    stderr_path: Path | None,
    code: str,
    message: str,
    additional_evidence: tuple[str, ...] = (),
) -> AutoCADResult:
    retained_script = _existing_evidence_path(script_path, require_nonempty=True)
    retained_stdout = _existing_evidence_path(stdout_path)
    retained_stderr = _existing_evidence_path(stderr_path)
    retained = tuple(
        str(path)
        for path in (retained_script, retained_stdout, retained_stderr)
        if path is not None
    )
    retained_evidence = (
        ("AutoCAD evidence retained in caller task work directory: " + ", ".join(retained),)
        if retained
        else ()
    )
    evidence = tuple(additional_evidence) + retained_evidence
    return AutoCADResult(
        "failed",
        return_code,
        None,
        None,
        retained_script,
        retained_stdout,
        retained_stderr,
        time.monotonic() - started,
        AutoCADErrorDetail(code, message),
        evidence,
    )


def _existing_evidence_path(
    path: Path | None, *, require_nonempty: bool = False
) -> Path | None:
    if path is None:
        return None
    try:
        if not path.is_file() or (require_nonempty and path.stat().st_size == 0):
            return None
    except OSError:
        return None
    return path


def _same_file(first: Path, second: Path) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False
