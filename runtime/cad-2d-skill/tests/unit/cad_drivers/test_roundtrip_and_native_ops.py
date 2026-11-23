from __future__ import annotations

import hashlib
from pathlib import Path

import ezdxf
from pypdf import PdfWriter
import pytest

from cad_skill_core.autocad.executor import _ProcessOutcome
from cad_skill_core.autocad.models import ExecutableIdentity
from cad_skill_core.cad_drivers import (
    NativeCADInstallation,
    NativeJobRequest,
    execute_native_cad_job,
    validate_3dsolid_script_allowlist,
    verify_dxf_structural_equivalence,
)
from cad_skill_core.modeling3d import Model3DSpec, generate_autocad_3d_script


def _build_rich_dxf(path: Path) -> Path:
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.layers.add("OUTLINE", color=7, linetype="CONTINUOUS", lineweight=50)
    doc.layers.add("CENTER", color=1, linetype="CENTER", lineweight=18)
    doc.styles.add("GB_TEXT", font="msyh.ttc")
    doc.dimstyles.add("GB_DIM", dxfattribs={"dimtxsty": "GB_TEXT", "dimtxt": 3.5})
    blk = doc.blocks.new(name="VALVE_SYM")
    blk.add_circle((0, 0), 5.0)
    doc.layouts.new("Sheet_A3")

    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 60), (0, 60)], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_circle((50, 30), 15.0, dxfattribs={"layer": "OUTLINE"})
    msp.add_line((-5, 30), (105, 30), dxfattribs={"layer": "CENTER"})
    msp.add_blockref("VALVE_SYM", (20, 20), dxfattribs={"layer": "OUTLINE"})
    doc.saveas(path)
    return path


def test_roundtrip_structural_equivalence_passes_and_catches_layer_block_layout_entity_and_extent_loss(tmp_path):
    before = _build_rich_dxf(tmp_path / "before.dxf")
    after_ok = _build_rich_dxf(tmp_path / "after_ok.dxf")

    report_path = tmp_path / "rt-audit.json"
    ok_rep = verify_dxf_structural_equivalence(before, after_ok, output_report_path=report_path)
    assert ok_rep.status == "passed"
    assert ok_rep.issues == ()
    assert report_path.is_file()

    # Mutate copy to simulate lossy conversion (drop layer, block, layout, entity, and shift extents)
    lossy_path = tmp_path / "after_lossy.dxf"
    doc = ezdxf.readfile(after_ok)
    msp = doc.modelspace()
    for ent in list(msp.query('LINE[layer=="CENTER"]')):
        msp.delete_entity(ent)
    doc.layers.remove("CENTER")
    for ref in list(msp.query('INSERT[name=="VALVE_SYM"]')):
        msp.delete_entity(ref)
    doc.blocks.delete_block("VALVE_SYM", safe=False)
    doc.layouts.delete("Sheet_A3")
    # Shift remaining circle outside tolerance
    circle = next(iter(msp.query("CIRCLE")))
    circle.dxf.center = (250.0, 30.0)
    doc.saveas(lossy_path)

    bad_rep = verify_dxf_structural_equivalence(before, lossy_path)
    assert bad_rep.status == "failed"
    codes = {i.code for i in bad_rep.issues}
    assert "ROUNDTRIP_MISSING_LAYERS" in codes
    assert "ROUNDTRIP_MISSING_BLOCKS" in codes
    assert "ROUNDTRIP_MISSING_LAYOUTS" in codes
    assert "ROUNDTRIP_ENTITY_COUNT_CHANGED" in codes
    assert "ROUNDTRIP_EXTENTS_DRIFT" in codes


def test_3dsolid_script_allowlist_accepts_phase3_scripts_and_blocks_command_injection(tmp_path):
    spec = Model3DSpec.model_validate(
        {
            "schema_version": "1.0",
            "project": {"name": "法兰", "drawing_number": "FLG-01"},
            "body": {
                "kind": "flange_3d",
                "outer_diameter": 120.0,
                "bore_diameter": 40.0,
                "thickness": 18.0,
                "bolt_circle_diameter": 88.0,
                "hole_diameter": 12.0,
                "hole_count": 4,
                "source_id": "s1",
            },
            "provenance": [{"id": "s1", "file": "a.md", "locator": "L1", "confidence": 1.0}],
        }
    )
    scr = generate_autocad_3d_script(spec, tmp_path / "out.dwg")
    sanitized = validate_3dsolid_script_allowlist(scr)
    assert "CAD_SKILL_SOLID3D_VERIFIED" in sanitized
    assert "_.SAVEAS\n2018\nmodel3d.dwg" in sanitized

    for malicious in (
        '_BOX 0,0,0 10,10,10\n(startapp "calc.exe")\n',
        "_CYLINDER 0,0,0 10 20\nSHELL dir\n",
        "_BOX 0,0,0 10,10,10\n_ERASE _ALL \n",
        "_BOX 0,0,0 10,10,10\nNETLOAD bad.dll\n",
    ):
        with pytest.raises(ValueError):
            validate_3dsolid_script_allowlist(malicious)


def _fake_installation(tmp_path: Path, driver_id: str = "zwcad") -> NativeCADInstallation:
    root = tmp_path / f"fake_{driver_id}"
    root.mkdir(parents=True, exist_ok=True)
    if driver_id == "zwcad":
        p_exe = root / "ZWCAD.exe"
        c_exe = root / "ZWCoConsole.exe"
        vendor = "zwsoft"
    elif driver_id == "oda":
        p_exe = root / "ODAFileConverter.exe"
        c_exe = p_exe
        vendor = "oda"
    else:
        p_exe = root / "acad.exe"
        c_exe = root / "accoreconsole.exe"
        vendor = "autodesk"
    p_exe.write_bytes(b"FAKE_PRIMARY_BINARY")
    c_exe.write_bytes(b"FAKE_CONSOLE_BINARY")

    def mk_id(p: Path) -> ExecutableIdentity:
        st = p.stat()
        return ExecutableIdentity(
            p.resolve(),
            st.st_dev,
            st.st_ino,
            st.st_size,
            st.st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest(),
            'CN="Trusted Vendor"',
            "AABBCCDDEEFF",
        )

    return NativeCADInstallation(
        driver_id=driver_id,
        vendor=vendor,  # type: ignore[arg-type]
        product_name=f"{driver_id.upper()} 2025",
        version="2025",
        release_code="R25.0",
        root_dir=root,
        primary_exe=p_exe,
        console_exe=c_exe,
        fonts_dir=None,
        primary_identity=mk_id(p_exe),
        console_identity=mk_id(c_exe),
        trusted=True,
    )


def test_execute_native_cad_job_supports_zwcad_console_across_all_four_job_kinds(tmp_path, monkeypatch):
    zw_inst = _fake_installation(tmp_path, "zwcad")
    monkeypatch.setattr(
        "cad_skill_core.cad_drivers.native_ops.select_preferred_installation",
        lambda **_kw: (zw_inst, "mock zwcad", None),
    )

    src_dxf = _build_rich_dxf(tmp_path / "src.dxf")
    src_dwg = tmp_path / "src.dwg"
    src_dwg.write_bytes(b"AC1032" + bytes(128))

    def fake_run_process(argv, *, cwd, env, timeout_seconds, stdout_path, stderr_path, **_kw):
        assert "ZWCoConsole.exe" in argv[0]
        assert "UNRELATED_ENV" not in env
        scr_file = Path(argv[argv.index("/s") + 1]).read_text(encoding="ascii")
        if "CAD_SKILL_DWG_VERIFIED" in scr_file:
            (cwd / "drawing.dwg").write_bytes(b"AC1032" + bytes(100))
            writer = PdfWriter()
            writer.add_blank_page(width=200, height=200)
            with (cwd / "drawing.pdf").open("wb") as f:
                writer.write(f)
            stdout_path.write_text("ok\nCAD_SKILL_DWG_VERIFIED\n", encoding="utf-8")
        elif "CAD_SKILL_DXFOUT_VERIFIED" in scr_file:
            _build_rich_dxf(cwd / "output.dxf")
            stdout_path.write_text("ok\nCAD_SKILL_DXFOUT_VERIFIED\n", encoding="utf-8")
        elif "CAD_SKILL_SOLID3D_VERIFIED" in scr_file:
            (cwd / "model3d.dwg").write_bytes(b"AC1032" + bytes(100))
            vdoc = ezdxf.new("R2010")
            solid = vdoc.modelspace().add_3dsolid()
            solid.sat = [
                "400 0 1 0",
                "7 Unknown 12 ACIS 4.0 NT 24 Thu Jan 01 00:00:00 2026",
                "1 1e-6 1e-10",
                "-0 body $-1 $-1 $-1 $-1 #",
                "End-of-ACIS-data",
            ]
            vdoc.saveas(cwd / "verify3d.dxf")
            stdout_path.write_text("ok\nCAD_SKILL_SOLID3D_VERIFIED\n", encoding="utf-8")
        else:
            stdout_path.write_text("ok\nCAD_SKILL_NATIVE_ASSOC_VERIFIED\n", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return _ProcessOutcome(0, False)

    monkeypatch.setattr("cad_skill_core.autocad.executor._run_process", fake_run_process)

    # 1. convert-and-plot
    r1 = execute_native_cad_job(
        NativeJobRequest(
            job_kind="convert-and-plot",
            input_path=src_dxf,
            output_dir=tmp_path / "out1",
            task_work_dir=tmp_path / "w1",
            driver_preference="zwcad",
        )
    )
    assert r1.status == "succeeded" and r1.driver_id == "zwcad"
    assert len(r1.outputs) == 2

    # 2. dwg-to-dxf
    r2 = execute_native_cad_job(
        NativeJobRequest(
            job_kind="dwg-to-dxf",
            input_path=src_dwg,
            output_dir=tmp_path / "out2",
            task_work_dir=tmp_path / "w2",
            output_stem="extracted",
        )
    )
    assert r2.status == "succeeded"
    assert r2.outputs[0].name == "extracted.dxf"

    # 3. build-3dsolid
    r3 = execute_native_cad_job(
        NativeJobRequest(
            job_kind="build-3dsolid",
            script_text="_BOX 0,0,0 20,20,10\n_SAVEAS 2018 \"ignored.dwg\"\n_QUIT _Y\n",
            output_dir=tmp_path / "out3",
            task_work_dir=tmp_path / "w3",
            output_stem="part3d",
        )
    )
    assert r3.status == "succeeded"
    assert r3.outputs[0].name == "part3d.dwg"

    # 4. probe-native-associativity
    r4 = execute_native_cad_job(
        NativeJobRequest(
            job_kind="probe-native-associativity",
            input_path=src_dxf,
            output_dir=tmp_path / "out4",
            task_work_dir=tmp_path / "w4",
        )
    )
    assert r4.status == "succeeded"
