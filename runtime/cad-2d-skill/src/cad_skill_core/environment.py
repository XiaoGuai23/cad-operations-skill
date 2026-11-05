"""Advisory environment checks; never launch CAD or cache authorization."""
from __future__ import annotations

import hashlib
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time

SCHEMA = 'cad-environment-cache/v1'
MAX_CACHE_AGE = 24 * 60 * 60
RESOURCES = ('knowledge/standards/gb-mechanical.yaml',
             'knowledge/drawing-rules/font-policy.yaml',
             'templates/autocad/convert-and-plot.scr.j2')


def _file_identity(path):
    try:
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        return {'path': str(path.resolve()), 'sha256': digest}
    except OSError as error:
        return {'path': str(path.resolve()), 'error': type(error).__name__}


def _candidate_files(configured):
    from .autocad import discovery
    if configured is not None:
        candidates = [Path(configured)]
    else:
        candidates = [Path(value) for key in ('ACCORECONSOLE_EXE', 'AUTOCAD_EXE') if (value := os.environ.get(key))]
        candidates += [discovery._fixed_install_root(), *discovery._registry_install_roots(), *discovery._program_files_install_roots()]
    files = set()
    for path in candidates:
        if path is None:
            continue
        root = path.parent if path.name.lower() in {'acad.exe', 'accoreconsole.exe'} else path
        files.update([root / 'acad.exe', root / 'accoreconsole.exe'])
    return sorted(files, key=str)


def _probe_native(configured):
    try:
        from .autocad.discovery import discover_autocad
        install = discover_autocad(configured)
        return {'status': 'available', 'reason': 'Exact-version signature-checked installation found; export/license/session not exercised.',
                'acad_path': str(install.acad_exe), 'core_console_path': str(install.core_console_exe), 'version': install.version}
    except Exception as error:
        detail = getattr(error, 'detail', None)
        return {'status': 'blocked', 'code': getattr(detail, 'code', type(error).__name__),
                'reason': str(getattr(detail, 'message', error)),
                'path': str(detail.path) if detail is not None and detail.path is not None else None}


def check_environment(*, cache=None, autocad_path=None, package_root=None, refresh=False, skip_native=False):
    root = Path(package_root) if package_root is not None else Path(__file__).resolve().parents[2]
    configured = Path(autocad_path).expanduser().resolve() if autocad_path is not None else None
    cache = Path(cache).expanduser().resolve() if cache is not None else None
    previous_bytes = cache.read_bytes() if cache is not None and cache.exists() else None
    previous = None
    if previous_bytes is not None:
        try:
            previous = json.loads(previous_bytes)
        except (ValueError, UnicodeError) as error:
            raise ValueError('invalid environment cache; preserve it and choose a new cache path') from error
        if not isinstance(previous, dict) or previous.get('schema') != SCHEMA:
            raise ValueError('unrelated cache file; refusing to overwrite')

    dependencies = {}
    for name, distribution in [('ezdxf', 'ezdxf'), ('matplotlib', 'matplotlib'), ('PIL', 'Pillow')]:
        try:
            importlib.import_module(name)
            dependencies[name] = {'status': 'available', 'version': metadata.version(distribution)}
        except Exception as error:
            dependencies[name] = {'status': 'blocked', 'reason': f'{type(error).__name__}: {error}'}
    resources = [_file_identity(root / rel) for rel in RESOURCES]
    missing = [item['path'] for item in resources if 'error' in item]
    candidate_error = None
    try:
        candidates = [_file_identity(path) for path in _candidate_files(configured)] if not skip_native else []
    except Exception as error:
        candidates = []
        candidate_error = f'{type(error).__name__}: {error}'
    state = {'python': sys.version, 'executable': sys.executable, 'platform': platform.platform(),
             'dependencies': dependencies, 'resources': resources, 'candidates': candidates,
             'configured_path': str(configured), 'candidate_error': candidate_error,
             'implementation': [_file_identity(p) for p in sorted((root / 'src/cad_skill_core').rglob('*.py'))],
             'cache_protocol': SCHEMA}
    fingerprint = hashlib.sha256(json.dumps(state, sort_keys=True).encode('utf-8')).hexdigest()
    now = time.time()
    reused = False
    native = {'status': 'not_requested', 'reason': 'Native discovery explicitly skipped.'}
    if not skip_native:
        stamp = previous.get('checked_at') if previous else None
        saved = previous.get('native') if previous else None
        if (not refresh and candidate_error is None and previous and previous.get('fingerprint') == fingerprint
                and isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
                and 0 <= now - stamp < MAX_CACHE_AGE and isinstance(saved, dict)
                and saved.get('status') == 'blocked' and isinstance(saved.get('reason'), str)):
            native, reused = saved, True
        else:
            native = _probe_native(configured)
    dxf = dict(dependencies['ezdxf'])
    preview_ready = all(x['status'] == 'available' for x in dependencies.values())
    export = dict(native)
    if missing:
        export = {'status': 'blocked', 'reason': 'Required installed profiles/export template are missing.', 'missing': missing}
    report = {'schema': 'cad-environment-report/v1', 'fingerprint': fingerprint, 'checked_at': now,
              'cache_reused': reused, 'package_root': str(root), 'python': sys.executable,
              'capabilities': {'dxf_edit': dxf,
                               'local_review': {'status': 'available' if preview_ready else 'blocked'},
                               'dwg_pdf': export,
                               'native_grips': {'status': 'unverified', 'reason': 'Representative interaction in a separate CAD copy is still required.'}},
              'dependencies': dependencies, 'resources': {'files': resources, 'missing': missing},
              'native_discovery': native, 'native_export_tested': False,
              'retry': 'Use --refresh after repairing configuration/certificates; changed executable/resource fingerprints retry automatically. Failure cache expires after 24 hours.'}
    if cache is not None and not skip_native and not reused:
        payload = {'schema': SCHEMA, 'checked_at': now, 'fingerprint': fingerprint, 'native': native}
        cache.parent.mkdir(parents=True, exist_ok=True)
        current_bytes = cache.read_bytes() if cache.exists() else None
        if current_bytes != previous_bytes:
            raise ValueError('environment cache changed during probe; refusing to overwrite')
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=cache.parent, prefix='.cad-env-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        try:
            os.replace(temporary, cache)
        finally:
            temporary.unlink(missing_ok=True)
    return report
