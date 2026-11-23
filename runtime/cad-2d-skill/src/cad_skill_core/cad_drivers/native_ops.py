"""Hardened headless execution runner for multi-version AutoCAD, ZWCAD, GstarCAD, and ODA."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

from cad_skill_core.autocad.models import _reject_reparse_components
from cad_skill_core.autocad.system_tools import trusted_system_directory

from .models import NativeCADInstallation, NativeJobRequest, NativeJobResult
from .registry import select_preferred_installation
from .vendors import build_console_command, build_oda_command


def _get_base_exec():
    from cad_skill_core.autocad import executor as _base_exec

    return _base_exec

_DXFOUT_SENTINEL = "CAD_SKILL_DXFOUT_VERIFIED"
_SOLID3D_SENTINEL = "CAD_SKILL_SOLID3D_VERIFIED"
_ASSOC_SENTINEL = "CAD_SKILL_NATIVE_ASSOC_VERIFIED"

_SOLID3D_ALLOWED_HEADS = frozenset(
    {
        "FILEDIA",
        "CMDDIA",
        "INSUNITS",
        "_UNITS",
        "_.UNITS",
        "-LAYER",
        "_-LAYER",
        "_PLINE",
        "_.PLINE",
        "_BOX",
        "_.BOX",
        "_WEDGE",
        "_.WEDGE",
        "_CYLINDER",
        "_.CYLINDER",
        "_EXTRUDE",
        "_.EXTRUDE",
        "_REVOLVE",
        "_.REVOLVE",
        "_UNION",
        "_.UNION",
        "_SUBTRACT",
        "_.SUBTRACT",
        "_SETVAR",
        "_.SETVAR",
        "_VPOINT",
        "_.VPOINT",
        "_ZOOM",
        "_.ZOOM",
        "_SAVEAS",
        "_.SAVEAS",
        "_DXFOUT",
        "_.DXFOUT",
        "_QSAVE",
        "_.QSAVE",
        "_QUIT",
        "_.QUIT",
    }
)

_SOLID3D_CREATOR_HEADS = frozenset(
    {
        "_BOX",
        "_.BOX",
        "_WEDGE",
        "_.WEDGE",
        "_CYLINDER",
        "_.CYLINDER",
        "_EXTRUDE",
        "_.EXTRUDE",
        "_REVOLVE",
        "_.REVOLVE",
    }
)

_FORBIDDEN_SCRIPT_TOKENS = re.compile(
    r"\b(?:STARTAPP|SHELL|SH|AI_EDIT|NETLOAD|ARX|VL-LOAD|VLAX-|COMMAND-S|LOAD|OPENURL)\b",
    re.IGNORECASE,
)


def validate_3dsolid_script_allowlist(
    script_text: str,
    *,
    staged_dwg_name: str = "model3d.dwg",
    verify_dxf_name: str = "verify3d.dxf",
) -> str:
    """Validate every line of a 3DSOLID script against strict command allowlist, require >=1 solid creator, & rewrite save/verify targets."""

    if not script_text or not script_text.strip():
        raise ValueError("3DSOLID script must not be empty")
    if _FORBIDDEN_SCRIPT_TOKENS.search(script_text):
        raise ValueError("3DSOLID script contains forbidden external/LISP/system command token")
    if any(ord(ch) < 32 and ch not in ("\n", "\r", "\t") for ch in script_text):
        raise ValueError("3DSOLID script contains control characters")

    sanitized_lines: list[str] = []
    saw_save = False
    saw_solid_creator = False
    for raw_line in script_text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith(";"):
            sanitized_lines.append("; comment")
            continue
        if stripped.startswith("("):
            raise ValueError(f"AutoLISP expressions are disallowed in caller 3DSOLID script: {stripped!r}")
        head = stripped.split()[0].upper()
        if head not in _SOLID3D_ALLOWED_HEADS:
            raise ValueError(f"command {head!r} is not in the 3DSOLID script allowlist")
        if head in _SOLID3D_CREATOR_HEADS:
            saw_solid_creator = True
        if head in {"_SAVEAS", "_.SAVEAS"}:
            sanitized_lines.append(f"_.SAVEAS\n2018\n{staged_dwg_name}")
            saw_save = True
        elif head in {"_DXFOUT", "_.DXFOUT", "_QUIT", "_.QUIT"}:
            continue
        else:
            if not stripped.isascii():
                raise ValueError(f"non-ASCII token in controlled 3DSOLID command line: {stripped!r}")
            sanitized_lines.append(raw_line.rstrip("\r\n"))

    if not saw_solid_creator:
        raise ValueError("3DSOLID script does not create or import any 3D solid entity")
    if not saw_save:
        sanitized_lines.append(f"_.SAVEAS\n2018\n{staged_dwg_name}")
    sanitized_lines.append(f"_.DXFOUT\n{verify_dxf_name}\n_Version\n2010\n16")
    sanitized_lines.append(f'(princ "\\n{_SOLID3D_SENTINEL}\\n")')
    sanitized_lines.append("_.QUIT\n_Yes")
    return "\n".join(sanitized_lines) + "\n"


def verify_3dsolid_dxf_entities(path: Path) -> int:
    """Reopen native CAD DXF export and verify >= 1 real 3DSOLID/BODY/REGION entity exists."""
    import ezdxf

    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"3DSOLID verification DXF is missing or empty: {path}")
    doc = ezdxf.readfile(path)
    audit = doc.audit()
    if audit.has_errors:
        raise ValueError(f"3DSOLID verification DXF failed audit ({len(audit.errors)} errors): {path}")
    solid_count = sum(1 for e in doc.modelspace() if e.dxftype() in {"3DSOLID", "BODY", "REGION"})
    if solid_count < 1:
        raise ValueError(f"native DWG modelspace contains 0 3DSOLID entities ({path})")
    return solid_count


def _validate_dwg_header(path: Path) -> str:
    if not path.is_file() or path.stat().st_size < 64:
        raise ValueError(f"DWG file is missing or truncated (<64 bytes): {path}")
    magic = path.read_bytes()[:6]
    if not magic.startswith(b"AC10") or not magic[4:6].isdigit():
        raise ValueError(f"invalid DWG magic header {magic!r} in {path}")
    return magic.decode("ascii")


def _validate_dxf_file(path: Path) -> None:
    import ezdxf

    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"DXF file is missing or empty: {path}")
    doc = ezdxf.readfile(path)
    audit = doc.audit()
    if audit.has_errors:
        raise ValueError(f"DXF audit reported {len(audit.errors)} error(s): {path}")


def _minimal_driver_env(stage: Path, installation: NativeCADInstallation) -> dict[str, str]:
    base_exec = _get_base_exec()
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() in base_exec._PRESERVED_ENVIRONMENT
        or k.upper().startswith(("ADSKFLEX_", "ADLM_"))
        or k.upper() == "FLEXLM_TIMEOUT"
    }
    sys_dir = trusted_system_directory()
    sys_root = sys_dir.parent
    dirs = [str(sys_dir), str(installation.primary_exe.parent)]
    if installation.console_exe is not None:
        c_dir = str(installation.console_exe.parent)
        if c_dir.casefold() not in {d.casefold() for d in dirs}:
            dirs.append(c_dir)
    env["PATH"] = os.pathsep.join(dirs)
    env["SystemRoot"] = str(sys_root)
    env["WINDIR"] = str(sys_root)
    env["COMSPEC"] = str(sys_dir / "cmd.exe")
    env["TEMP"] = str(stage)
    env["TMP"] = str(stage)
    return env


@contextmanager
def _hold_installation_binaries(installation: NativeCADInstallation):
    paths = [installation.primary_exe]
    if installation.console_exe is not None and installation.console_exe != installation.primary_exe:
        paths.append(installation.console_exe)
    if os.name != "nt":
        streams = [p.open("rb") for p in paths]
        try:
            yield
        finally:
            for s in streams:
                s.close()
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
    invalid = ctypes.c_void_p(-1).value
    handles: list[int] = []
    try:
        for p in paths:
            h = create_file(str(p), 0x80000000, 0x00000001, None, 3, 0x00000080, None)
            if h in {None, invalid}:
                raise OSError(ctypes.get_last_error(), f"could not read-lock CAD binary: {p}")
            handles.append(h)
        yield
    finally:
        for h in reversed(handles):
            close_handle(h)


def _publish_output_safe(staged_file: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    final_dest = destination
    ver = 1
    while final_dest.exists():
        final_dest = destination.parent / f"{destination.stem}-v{ver:02d}{destination.suffix}"
        ver += 1
    fd, tmp_name = tempfile.mkstemp(prefix=".drv-", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copyfile(staged_file, tmp_path)
        os.replace(tmp_path, final_dest)
    finally:
        tmp_path.unlink(missing_ok=True)
    return final_dest


def execute_native_cad_job(request: NativeJobRequest) -> NativeJobResult:
    """Execute one allowlisted headless CAD job across AutoCAD (2021-2026), ZWCAD, GstarCAD, or ODA."""

    started = time.monotonic()
    assert request.task_work_dir is not None
    boundary = request.task_work_dir / "cad_driver"
    script_path = boundary / "job.scr"
    stdout_path = boundary / "stdout.log"
    stderr_path = boundary / "stderr.log"

    try:
        _reject_reparse_components(request.output_dir, "output_dir")
        _reject_reparse_components(request.task_work_dir, "task_work_dir")
        boundary.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return NativeJobResult(
            status="failed",
            job_kind=request.job_kind,
            driver_id=None,
            driver_version=None,
            return_code=None,
            outputs=(),
            script_path=None,
            stdout_path=None,
            stderr_path=None,
            duration_seconds=time.monotonic() - started,
            error_code="workdir-failed",
            error_message=str(exc),
        )

    # Validate inputs BEFORE driver discovery so malformed scripts/files fail deterministically
    try:
        if request.job_kind in {"convert-and-plot", "probe-native-associativity"}:
            if request.input_path is None:
                raise ValueError(f"{request.job_kind} requires input_path (.dxf)")
            _validate_dxf_file(request.input_path)
        elif request.job_kind == "dwg-to-dxf":
            if request.input_path is None:
                raise ValueError("dwg-to-dxf requires input_path (.dwg)")
            _validate_dwg_header(request.input_path)
        elif request.job_kind == "build-3dsolid":
            if not request.script_text:
                raise ValueError("build-3dsolid requires script_text")
            validate_3dsolid_script_allowlist(request.script_text)
    except Exception as exc:
        return NativeJobResult(
            status="failed",
            job_kind=request.job_kind,
            driver_id=None,
            driver_version=None,
            return_code=None,
            outputs=(),
            script_path=None,
            stdout_path=None,
            stderr_path=None,
            duration_seconds=time.monotonic() - started,
            error_code="invalid-request",
            error_message=str(exc),
        )

    installation, skip_reason, err_code = select_preferred_installation(
        driver_preference=request.driver_preference,
        allowed_versions=request.allowed_versions,
        configured_path=request.configured_path,
        require_3dsolid=(request.job_kind == "build-3dsolid"),
        allow_oda=(request.job_kind == "dwg-to-dxf"),
    )
    if installation is None:
        status = "skipped" if err_code == "not-installed" else "failed"
        return NativeJobResult(
            status=status,
            job_kind=request.job_kind,
            driver_id=None,
            driver_version=None,
            return_code=None,
            outputs=(),
            script_path=None,
            stdout_path=None,
            stderr_path=None,
            duration_seconds=time.monotonic() - started,
            error_code=err_code or "not-installed",
            error_message=skip_reason,
            evidence=(skip_reason,),
        )

    stage = Path(tempfile.mkdtemp(prefix="run-", dir=boundary))
    env = _minimal_driver_env(stage, installation)

    try:
        if installation.driver_id == "oda" and request.job_kind == "dwg-to-dxf":
            assert request.input_path is not None
            oda_in = stage / "in"
            oda_out = stage / "out"
            oda_in.mkdir()
            oda_out.mkdir()
            shutil.copyfile(request.input_path, oda_in / "input.dwg")
            argv = build_oda_command(
                installation.primary_exe,
                oda_in,
                oda_out,
                output_version="ACAD2018",
                output_format="DXF",
                audit=True,
                file_filter="input.dwg",
            )
            with _hold_installation_binaries(installation):
                outcome = _get_base_exec()._run_process(
                    argv,
                    cwd=stage,
                    env=env,
                    timeout_seconds=request.timeout_seconds,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                )
            if outcome.timed_out or outcome.return_code != 0:
                return NativeJobResult(
                    status="failed",
                    job_kind=request.job_kind,
                    driver_id=installation.driver_id,
                    driver_version=installation.version,
                    return_code=outcome.return_code,
                    outputs=(),
                    script_path=None,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    duration_seconds=time.monotonic() - started,
                    error_code="timeout" if outcome.timed_out else "process-failed",
                    error_message="ODA File Converter failed or timed out",
                )
            staged_dxf = oda_out / "input.dxf"
            _validate_dxf_file(staged_dxf)
            published_dxf = _publish_output_safe(staged_dxf, request.output_dir / f"{request.output_stem}.dxf")
            return NativeJobResult(
                status="succeeded",
                job_kind=request.job_kind,
                driver_id=installation.driver_id,
                driver_version=installation.version,
                return_code=0,
                outputs=(published_dxf,),
                script_path=None,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                duration_seconds=time.monotonic() - started,
                evidence=("DWG converted via ODA File Converter and passed ezdxf audit.",),
            )

        # Console / script-driven CAD execution (AutoCAD 2021-2026, ZWCAD, GstarCAD)
        sentinel: str | None = None
        if request.job_kind == "convert-and-plot":
            assert request.input_path is not None
            staged_in = stage / "input.dxf"
            shutil.copyfile(request.input_path, staged_in)
            plot_block = (
                f"_.-PLOT\n_No\n{request.plot_layout}\n\nDWG To PDF.pc3\n_Yes\ndrawing.pdf\n_No\n_Yes\n"
                if request.include_pdf
                else ""
            )
            scr = (
                "_.AUDIT\n_Yes\n_.QSAVE\n"
                "_.SAVEAS\n2018\ndrawing.dwg\n"
                + plot_block
                + '(princ "\\nCAD_SKILL_DWG_VERIFIED\\n")\n'
                + "_.QUIT\n"
            )
            sentinel = "CAD_SKILL_DWG_VERIFIED"
        elif request.job_kind == "dwg-to-dxf":
            assert request.input_path is not None
            staged_in = stage / "input.dwg"
            shutil.copyfile(request.input_path, staged_in)
            scr = (
                "_.AUDIT\n_Yes\n"
                "_.DXFOUT\noutput.dxf\n_Version\n2018\n16\n"
                f'(princ "\\n{_DXFOUT_SENTINEL}\\n")\n'
                "_.QUIT\n_Yes\n"
            )
            sentinel = _DXFOUT_SENTINEL
        elif request.job_kind == "build-3dsolid":
            assert request.script_text is not None
            import ezdxf

            seed_dxf = stage / "seed.dxf"
            doc = ezdxf.new("R2018")
            doc.header["$INSUNITS"] = 4
            doc.saveas(seed_dxf)
            staged_in = seed_dxf
            scr = validate_3dsolid_script_allowlist(request.script_text, staged_dwg_name="model3d.dwg")
            sentinel = _SOLID3D_SENTINEL
        else:  # probe-native-associativity
            assert request.input_path is not None
            staged_in = stage / "probe.dxf"
            shutil.copyfile(request.input_path, staged_in)
            scr = (
                "_.AUDIT\n_Yes\n"
                "_.REGENALL\n"
                f'(princ "\\n{_ASSOC_SENTINEL}\\n")\n'
                "_.QUIT\n_No\n"
            )
            sentinel = _ASSOC_SENTINEL

        script_path.write_text(scr, encoding="ascii")
        argv = build_console_command(installation, staged_in, script_path)
        base_exec = _get_base_exec()
        with _hold_installation_binaries(installation):
            outcome = base_exec._run_process(
                argv,
                cwd=stage,
                env=env,
                timeout_seconds=request.timeout_seconds,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )

        if outcome.timed_out:
            return NativeJobResult(
                status="failed",
                job_kind=request.job_kind,
                driver_id=installation.driver_id,
                driver_version=installation.version,
                return_code=outcome.return_code,
                outputs=(),
                script_path=script_path,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                duration_seconds=time.monotonic() - started,
                error_code="timeout",
                error_message=f"{installation.product_name} exceeded {request.timeout_seconds}s timeout",
            )
        if outcome.return_code != 0:
            return NativeJobResult(
                status="failed",
                job_kind=request.job_kind,
                driver_id=installation.driver_id,
                driver_version=installation.version,
                return_code=outcome.return_code,
                outputs=(),
                script_path=script_path,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                duration_seconds=time.monotonic() - started,
                error_code="process-failed",
                error_message=f"{installation.product_name} exited with code {outcome.return_code}",
            )

        base_exec._validate_process_logs(stdout_path, stderr_path, required_sentinel=sentinel)

        published_outputs: list[Path] = []
        evidence_items = [f"Executed via {installation.product_name} ({installation.release_code})."]

        if request.job_kind == "convert-and-plot":
            dwg_cand = stage / "drawing.dwg"
            _validate_dwg_header(dwg_cand)
            published_outputs.append(
                _publish_output_safe(dwg_cand, request.output_dir / f"{request.output_stem}.dwg")
            )
            if request.include_pdf:
                pdf_cand = stage / "drawing.pdf"
                base_exec._validate_pdf(pdf_cand)
                published_outputs.append(
                    _publish_output_safe(pdf_cand, request.output_dir / f"{request.output_stem}.pdf")
                )
            evidence_items.append("DWG magic header and PDF page structure verified.")

        elif request.job_kind == "dwg-to-dxf":
            dxf_cand = stage / "output.dxf"
            _validate_dxf_file(dxf_cand)
            published_outputs.append(
                _publish_output_safe(dxf_cand, request.output_dir / f"{request.output_stem}.dxf")
            )
            evidence_items.append("Extracted DXF reopened and passed ezdxf audit.")

        elif request.job_kind == "build-3dsolid":
            dwg_3d = stage / "model3d.dwg"
            hdr = _validate_dwg_header(dwg_3d)
            solid_count = verify_3dsolid_dxf_entities(stage / "verify3d.dxf")
            published_outputs.append(
                _publish_output_safe(dwg_3d, request.output_dir / f"{request.output_stem}.dwg")
            )
            evidence_items.append(f"Native 3DSOLID DWG ({hdr}) created and verified ({solid_count} 3DSOLID(s) reopened).")

        else:
            evidence_items.append("Disposable native CAD audit + regenall probe succeeded cleanly.")

        return NativeJobResult(
            status="succeeded",
            job_kind=request.job_kind,
            driver_id=installation.driver_id,
            driver_version=installation.version,
            return_code=0,
            outputs=tuple(published_outputs),
            script_path=script_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            duration_seconds=time.monotonic() - started,
            evidence=tuple(evidence_items),
        )
    except Exception as exc:
        return NativeJobResult(
            status="failed",
            job_kind=request.job_kind,
            driver_id=installation.driver_id,
            driver_version=installation.version,
            return_code=None,
            outputs=(),
            script_path=script_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            duration_seconds=time.monotonic() - started,
            error_code="output-invalid",
            error_message=str(exc),
        )
