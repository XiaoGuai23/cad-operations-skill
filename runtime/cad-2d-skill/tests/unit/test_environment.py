import importlib
import json

import pytest


def module():
    return importlib.import_module('cad_skill_core.environment')


def assets(tmp_path):
    root = tmp_path / 'runtime'
    for rel in ['knowledge/standards/gb-mechanical.yaml',
                'knowledge/drawing-rules/font-policy.yaml',
                'templates/autocad/convert-and-plot.scr.j2']:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture', encoding='utf-8')
    return root


def test_unchanged_native_failure_reused_and_executable_change_retries(tmp_path, monkeypatch):
    m = module()
    root = assets(tmp_path)
    executable = tmp_path / 'acad.exe'
    executable.write_bytes(b'old executable')
    calls = []
    def probe(path):
        calls.append(path)
        return {'status': 'blocked', 'code': 'untrusted-executable', 'reason': 'test signature failure'}
    monkeypatch.setattr(m, '_probe_native', probe)
    cache = tmp_path / 'environment.json'
    first = m.check_environment(cache=cache, autocad_path=executable, package_root=root)
    second = m.check_environment(cache=cache, autocad_path=executable, package_root=root)
    assert not first['cache_reused'] and second['cache_reused']
    assert len(calls) == 1
    executable.write_bytes(b'new executable')
    third = m.check_environment(cache=cache, autocad_path=executable, package_root=root)
    assert not third['cache_reused'] and len(calls) == 2
    assert third['capabilities']['native_grips']['status'] == 'unverified'


def test_resource_change_explicit_refresh_and_expiry_invalidate_failure(tmp_path, monkeypatch):
    m = module()
    root = assets(tmp_path)
    calls = []
    monkeypatch.setattr(m, '_probe_native', lambda p: calls.append(p) or {'status': 'blocked', 'code': 'not-installed', 'reason': 'missing'})
    cache = tmp_path / 'environment.json'
    m.check_environment(cache=cache, package_root=root)
    m.check_environment(cache=cache, package_root=root, refresh=True)
    assert len(calls) == 2
    (root / 'knowledge/standards/gb-mechanical.yaml').write_text('changed')
    m.check_environment(cache=cache, package_root=root)
    assert len(calls) == 3
    data = json.loads(cache.read_text())
    data['checked_at'] = 0
    cache.write_text(json.dumps(data))
    m.check_environment(cache=cache, package_root=root)
    assert len(calls) == 4


def test_positive_probe_is_not_cached_or_misreported_as_native_verification(tmp_path, monkeypatch):
    m = module()
    calls = []
    monkeypatch.setattr(m, '_probe_native', lambda p: calls.append(p) or {'status': 'available', 'reason': 'discovery only'})
    root = assets(tmp_path)
    cache = tmp_path / 'env.json'
    a = m.check_environment(cache=cache, package_root=root)
    b = m.check_environment(cache=cache, package_root=root)
    assert len(calls) == 2 and not b['cache_reused']
    assert a['native_export_tested'] is False


def test_missing_templates_report_blocked_without_breaking_dxf_probe(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setattr(m, '_probe_native', lambda p: {'status': 'available', 'reason': 'discovery only'})
    result = m.check_environment(package_root=tmp_path)
    assert result['capabilities']['dxf_edit']['status'] == 'available'
    assert result['capabilities']['dwg_pdf']['status'] == 'blocked'
    assert result['resources']['missing']


def test_unrelated_cache_is_not_overwritten(tmp_path):
    m = module()
    cache = tmp_path / 'important.json'
    cache.write_text('{"user": "data"}')
    before = cache.read_bytes()
    with pytest.raises(ValueError, match='cache'):
        m.check_environment(cache=cache, package_root=assets(tmp_path))
    assert cache.read_bytes() == before


def test_cli_doctor_does_not_import_generation_pipeline(tmp_path):
    import subprocess
    import sys
    import os
    from pathlib import Path
    script = '''import sys
class Block:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "ezdxf" or fullname.startswith("cad_skill_core.pipeline"):
            raise ImportError("intentionally unavailable")
sys.meta_path.insert(0, Block())
from cad_skill_core.cli import main
raise SystemExit(main(["doctor", "--skip-native", "--json"]))
'''
    env = os.environ.copy()
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2] / 'src')
    result = subprocess.run([sys.executable, '-c', script], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['capabilities']['dxf_edit']['status'] == 'blocked'
