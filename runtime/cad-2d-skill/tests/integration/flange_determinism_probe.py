"""Fresh-process no-AutoCAD flange run; retain real artifacts and hash evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
import sys

import ezdxf
from PIL import Image

from cad_skill_core.pipeline import PipelineOptions, run_task
from tests.helpers import copy_example


def probe(destination: Path, seed: int) -> dict[str, object]:
    random.seed(seed)
    task = copy_example("flange-assembly", destination)
    sources = {path: path.read_bytes() for path in task.rglob("*") if path.is_file()}
    result = run_task(task, PipelineOptions(run_autocad=False))
    assert result.status == "partial"
    assert all(path.read_bytes() == payload for path, payload in sources.items())
    document = ezdxf.readfile(task / "output" / "drawing.dxf")
    assert not document.audit().has_errors
    assert sum(entity.dxftype() == "HATCH" for entity in document.modelspace()) == 1
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["summary"]["errors"] == 0
    assert audit["autocad"]["status"] == "disabled"
    assert audit["font_render"]["verified_roles"] == ["DIM", "TEXT"]
    manifest_path = task / "output" / "pipeline-owned.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["commit_marker"] is True
    for artifact in manifest["artifacts"]:
        path = task / artifact["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
        assert path.stat().st_size == artifact["size"]
    for artifact in audit["artifacts"]:
        path = task / "output" / artifact["name"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [*result.outputs, manifest_path]
    }
    with Image.open(task / "output" / "drawing.png") as image:
        size = image.size
        hashes["png-rgba-pixels"] = hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest()
    return {"status": result.status, "seed": seed, "task": str(task.resolve()), "size": size, "hashes": hashes}


if __name__ == "__main__":
    evidence = probe(Path(sys.argv[1]), int(sys.argv[2]))
    print(json.dumps(evidence, ensure_ascii=True, sort_keys=True))
