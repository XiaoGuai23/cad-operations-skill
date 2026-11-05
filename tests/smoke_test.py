from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import ezdxf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "smoke"


def run(cmd: list[str]) -> None:
    print("$", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def assert_nonempty(path: Path) -> None:
    if not path.exists() or path.stat().st_size <= 0:
        raise AssertionError(f"missing or empty: {path}")


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "sld").mkdir(parents=True)
    (OUT / "pavement").mkdir(parents=True)

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
        "scripts/generate_pavement_cad_ezdxf.py",
        "examples/pavement_structure_spec.json",
        "--out",
        str(OUT / "pavement"),
    ])

    expected = [
        OUT / "sld" / "smoke_sld.dxf",
        OUT / "sld" / "smoke_sld.svg",
        OUT / "sld" / "smoke_sld.pdf",
        OUT / "pavement" / "pavement_structure.dxf",
    ]
    for path in expected:
        assert_nonempty(path)
        print("OK", path.relative_to(ROOT), path.stat().st_size)

    for path in [expected[0], expected[3]]:
        doc = ezdxf.readfile(path)
        print("DXF OK", path.relative_to(ROOT), len(doc.modelspace()))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
