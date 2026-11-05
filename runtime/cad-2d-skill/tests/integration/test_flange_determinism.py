import json
import os
from pathlib import Path
import subprocess
import sys


def test_full_flange_pipeline_hashes_and_manifest_match_across_fresh_processes(tmp_path):
    root = Path(__file__).resolve().parents[2]
    outputs = []
    for seed in (11, 29, 47, 83):
        env = dict(os.environ, PYTHONHASHSEED=str(seed), PYTHONPATH=str(root / "src"))
        result = subprocess.run(
            [sys.executable, "-W", "error", "-m", "tests.integration.flange_determinism_probe", str(tmp_path / str(seed)), str(seed)],
            cwd=root, env=env, capture_output=True, text=True, check=True, timeout=60,
        )
        outputs.append(json.loads(result.stdout))
    assert all(item["status"] == "partial" for item in outputs)
    assert all(item["hashes"] == outputs[0]["hashes"] for item in outputs)
    assert all(item["size"] == outputs[0]["size"] for item in outputs)
