from pathlib import Path

from tests.helpers import copy_example


def test_example_copy_ignores_generated_artifacts_but_preserves_authored_inputs(tmp_path, monkeypatch):
    import tests.helpers as helper
    source_root = tmp_path / 'source'
    example = source_root / 'examples/sample'
    (example / 'input').mkdir(parents=True)
    (example / 'work/iterations').mkdir(parents=True)
    (example / 'output').mkdir()
    (example / 'input/project.md').write_text('authored input')
    (example / 'work/drawing-spec.json').write_text('{"authored":true}')
    generated = [example / 'output/drawing.dxf', example / 'work/iterations/stale.dxf',
                 example / 'work/drawing-spec.canonical.json']
    for p in generated:
        p.write_text('stale generated output')
    monkeypatch.setattr(helper, '__file__', str(source_root / 'tests/helpers.py'))
    result = copy_example('sample', tmp_path / 'copy')
    assert (result / 'input/project.md').read_text() == 'authored input'
    assert (result / 'work/drawing-spec.json').read_text() == '{"authored":true}'
    assert not (result / 'output').exists()
    assert sorted(p.name for p in (result / 'work').iterdir()) == ['drawing-spec.json']
    assert all(p.read_text() == 'stale generated output' for p in generated)
