# Installed CAD revision tools

The original `cad-operations` and `cad-2d-geometry` skills use the reviewed
`inventory` and `revise` implementation. Read the [complete operation guide](revision-tools-api.md)
before constructing a plan. It defines selectors, baseline hashes, units, supported
geometry, native annotations and saved-file verification.

Use Python 3.12 from the environment created by the repository README.
The installed source and required styles/templates are bundled at
`runtime/cad-2d-skill`.
Runtime use does not depend on the development worktree. Keep the bundled runtime
with this skill: the existing generation and export paths also need its
`knowledge` and `templates` directories.

```text
python -m cad_skill_core inventory BASELINE.dxf --json
python -m cad_skill_core revise BASELINE.dxf PLAN.json --out NEW_OUTPUT --dry-run --json
python -m cad_skill_core revise BASELINE.dxf PLAN.json --out NEW_OUTPUT --json
```

From the repository root, install the bundled runtime with:

```powershell
python -m pip install -e './runtime/cad-2d-skill'
```

The [synthetic example](../examples/revision-tools/README.md) creates disposable
inputs for the original nine operations. Run `python -m cad_skill_core operations --json`
for the complete current operation list. Put generated examples in the active task's
output directory. Its values are test fixtures, not manufacturing dimensions.

This backend edits DXF modelspace; DWG/PDF use the native CAD path and need their
own checks. Keep native DIMENSION/MULTILEADER. A passed structural report does
not establish grip interaction or geometric associativity. Native verification
still requires a working, trusted CAD installation.

After local edits, use [neighbor review and environment checks](review-and-environment.md).

批注逐条核验和跨轮次自动重查已接入工具，见 [批注台账](comment-ledger.md)。
