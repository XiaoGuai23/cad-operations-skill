from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import ezdxf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "smoke"


def run(cmd: list[str]) -> None:
    print("$", " ".join(str(c) for c in cmd))
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)


def assert_nonempty(path: Path) -> None:
    if not path.exists() or path.stat().st_size <= 0:
        raise AssertionError(f"missing or empty: {path}")


def assert_audited_dxf(path: Path) -> ezdxf.document.Drawing:
    doc = ezdxf.readfile(path)
    auditor = doc.audit()
    if auditor.has_errors:
        raise AssertionError(f"DXF audit errors in {path}: {auditor.errors}")
    if len(doc.modelspace()) == 0:
        raise AssertionError(f"DXF modelspace is empty: {path}")
    print("DXF AUDIT OK", path.relative_to(ROOT), "entities=", len(doc.modelspace()))
    return doc


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "sld").mkdir(parents=True)
    (OUT / "pavement_ezdxf").mkdir(parents=True)
    (OUT / "pavement_model").mkdir(parents=True)
    (OUT / "pavement_r12").mkdir(parents=True)

    # 1. Single-bus / split-bus electrical SLD + audit_sld.py
    run([
        sys.executable,
        "scripts/make_main_sld.py",
        "examples/electrical_sld_spec.json",
        "--outdir",
        str(OUT / "sld"),
        "--stem",
        "smoke_sld",
    ])
    run([
        sys.executable,
        "scripts/audit_sld.py",
        str(OUT / "sld" / "smoke_sld.dxf"),
        "--expect-text",
        "110kV=3",
        "--expect-text",
        "35kV=6",
        "--expect-text",
        "主变=2",
    ])

    # 2. Double-busbar ("双母线") electrical SLD topology smoke check
    double_bus_spec = OUT / "sld" / "double_bus_spec.json"
    double_bus_spec.write_text(
        json.dumps(
            {
                "title": "110kV双母线主接线图",
                "voltage_levels": [
                    {
                        "name": "110kV",
                        "bus": "双母线",
                        "feeders": ["110kV进线1", "110kV进线2"],
                        "transformers": ["1#主变", "2#主变"],
                        "pt": 2,
                    },
                    {
                        "name": "10kV",
                        "bus": "单母分段",
                        "feeders": ["10kV出线1", "10kV出线2", "10kV出线3", "10kV出线4"],
                        "pt": 2,
                        "capacitors": 2,
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    run([
        sys.executable,
        "scripts/make_main_sld.py",
        str(double_bus_spec),
        "--outdir",
        str(OUT / "sld"),
        "--stem",
        "smoke_double_bus_sld",
    ])
    run([
        sys.executable,
        "scripts/audit_sld.py",
        str(OUT / "sld" / "smoke_double_bus_sld.dxf"),
        "--expect-text",
        "110kV I母=1",
        "--expect-text",
        "110kV II母=1",
        "--expect-text",
        "母联=1",
        "--expect-text",
        "主变=2",
    ])

    # 3. Pavement sheet generator (ezdxf native HATCH + DIMENSION + neutral default org)
    run([
        sys.executable,
        "scripts/generate_pavement_cad_ezdxf.py",
        "examples/pavement_structure_spec.json",
        "--out",
        str(OUT / "pavement_ezdxf"),
    ])

    # 4. Pavement model-space generator (both ezdxf R2010 and stdlib AC1009 R12 fallback)
    run([
        sys.executable,
        "scripts/generate_pavement_cad.py",
        "examples/pavement_structure_spec.json",
        "--out",
        str(OUT / "pavement_model"),
    ])

    sys.path.insert(0, str(ROOT / "scripts"))
    import generate_pavement_cad

    spec_data = json.loads((ROOT / "examples" / "pavement_structure_spec.json").read_text(encoding="utf-8-sig"))
    r12_dxf = generate_pavement_cad.make_drawing(spec_data, OUT / "pavement_r12", force_stdlib=True)

    expected = [
        OUT / "sld" / "smoke_sld.dxf",
        OUT / "sld" / "smoke_sld.svg",
        OUT / "sld" / "smoke_sld.pdf",
        OUT / "sld" / "smoke_double_bus_sld.dxf",
        OUT / "pavement_ezdxf" / "pavement_structure.dxf",
        OUT / "pavement_model" / "pavement_structure.dxf",
        r12_dxf,
    ]
    for path in expected:
        assert_nonempty(path)
        print("OK", path.relative_to(ROOT), path.stat().st_size)

    pavement_sheet_doc = assert_audited_dxf(OUT / "pavement_ezdxf" / "pavement_structure.dxf")
    types = {entity.dxftype() for entity in pavement_sheet_doc.modelspace()}
    if not {"HATCH", "DIMENSION", "LWPOLYLINE"} <= types:
        raise AssertionError(f"Expected native HATCH/DIMENSION/LWPOLYLINE in pavement sheet DXF, got {types}")
    texts = [e.dxf.text for e in pavement_sheet_doc.modelspace().query("TEXT")]
    if any("青海大学" in t for t in texts):
        raise AssertionError("Hardcoded school name was not removed from default pavement sheet output")

    for dxf_path in [
        OUT / "sld" / "smoke_sld.dxf",
        OUT / "sld" / "smoke_double_bus_sld.dxf",
        OUT / "pavement_model" / "pavement_structure.dxf",
        r12_dxf,
    ]:
        assert_audited_dxf(dxf_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
