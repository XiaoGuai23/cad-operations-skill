# Existing drawing revision example

Run from the active task workspace with the installed
python environment. These files are disposable test inputs;
they are not dimensions or geometry for a real die.

```text
python examples/revision-tools/make_example.py output/revision-demo
python -m cad_skill_core inventory output/revision-demo/baseline.dxf --json
python -m cad_skill_core revise output/revision-demo/baseline.dxf output/revision-demo/plan.json --out output/revision-demo/revised --dry-run --json
python -m cad_skill_core revise output/revision-demo/baseline.dxf output/revision-demo/plan.json --out output/revision-demo/revised --json
```

The generator refuses an existing directory. A second identical `revise` call
reuses the verified result if its hashes still match. It does not repeat edits.
Changing the source requires a fresh inventory and a plan based on that file.

The example exercises alignment, finite-edge contact, fixed-bottom extension,
fixed-end line length, native dimension position and text mode, native leader
text movement, explicit callout linking, and selected crossing-line splitting.
A hatch and unrelated text remain present. Read `revision-report.json` and reopen
`drawing.dxf`; a successful structural report does not prove AutoCAD grip behavior
or constitute a DWG/PDF delivery. See [the tool guide](../../references/revision-tools-api.md).
