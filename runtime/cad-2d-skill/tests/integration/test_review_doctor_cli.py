import hashlib
import json

import ezdxf
from PIL import Image

from cad_skill_core.cli import main


def test_review_publishes_matched_views_and_keeps_originals(tmp_path, capsys):
    doc = ezdxf.new('R2018', setup=True)
    doc.units = 4
    line = doc.modelspace().add_line((0, 0), (10, 0))
    hatch = doc.modelspace().add_hatch()
    path = hatch.paths.add_edge_path()
    for a, b in [((0, 0), (10, 0)), ((10, 0), (10, 4)), ((10, 4), (0, 4)), ((0, 4), (0, 0))]:
        path.add_line(a, b)
    before, after = tmp_path / 'before.dxf', tmp_path / 'after.dxf'
    doc.saveas(before)
    line.dxf.start = (0, -1)
    line.dxf.end = (10, -1)
    doc.saveas(after)
    hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (before, after)]
    out = tmp_path / 'review'
    assert main(['review', str(before), str(after), '--out', str(out), '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert any(i['code'] == 'hatch_boundary_detached' for i in result['issues'])
    assert (out / 'index.html').is_file()
    for label in ['before', 'after']:
        with Image.open(out / result['images'][label][0]) as image:
            assert image.size == (1120, 840)
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in (before, after)] == hashes
    sentinel = (out / 'index.html').read_bytes()
    assert main(['review', str(before), str(after), '--out', str(out), '--json']) == 2
    assert (out / 'index.html').read_bytes() == sentinel


def test_doctor_skip_native_is_explicit_and_does_not_write_cache(tmp_path, capsys):
    cache = tmp_path / 'env.json'
    assert main(['doctor', '--skip-native', '--cache', str(cache), '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['native_discovery']['status'] == 'not_requested'
    assert result['native_export_tested'] is False
    assert not cache.exists()
