# SolidWorks MCP / COM Workflow

## Use this workflow for

SolidWorks control from Codex, MCP, Windows COM, VBA macros, C# SolidWorks API, or creating/editing/validating/exporting `.SLDPRT`, `.SLDASM`, `.SLDDRW`, STEP, STL, DXF, PDF, or drawing files.

## Runtime setup

Set `SOLIDWORKS_MCP_HOME` to the local checkout of a SolidWorks MCP server such as `SolidworksMCP-python`:

```powershell
$env:SOLIDWORKS_MCP_HOME = "$env:USERPROFILE\.codex\mcp\SolidworksMCP-python"
Set-Location -LiteralPath $env:SOLIDWORKS_MCP_HOME
& ".\.venv\Scripts\python.exe" -m solidworks_mcp.server
```

If MCP tools are unavailable in the current session, use COM/VBA/script fallback and report that MCP is not exposed to this Codex session.

## Default design loop

1. Clarify or infer units; use millimeters unless the user specifies otherwise.
2. Inspect live SolidWorks state first: MCP server status/capabilities if available, active document, document type, path, rebuild status.
3. Plan small reversible steps: part/assembly/drawing, sketches, features, mates, materials, outputs.
4. Execute one stage at a time through MCP or a generated macro/script.
5. Rebuild after each major feature group and inspect errors/warnings.
6. Verify decisive geometry: dimensions, feature tree, mass properties, bounding box, faces/edges, mates/interference where applicable.
7. Save native files to an explicit path, preferably current workspace.
8. Export requested neutral formats after native save: STEP for CAD exchange, STL for printing/mesh, DXF/PDF for drawings.
9. Report exact paths and validation results.

## Tool categories

When using `SolidworksMCP-python`, open only the relevant catalog under:

`$env:SOLIDWORKS_MCP_HOME\docs\user-guide\tool-catalog\`

Typical catalog files:

- `modeling.md` — parts, features, assemblies.
- `sketching.md` — sketch geometry, constraints, dimensions.
- `drawing.md` — sheets, views, annotations.
- `analysis.md` — mass properties, interference/validation.
- `export.md` — STEP/IGES/STL/PDF/DWG/image export.
- `automation.md`, `vba-generation.md`, `macro-recording.md` — repeatable workflows and macro fallbacks.
- `docs-discovery.md` — discover/search installed SolidWorks API docs when method names drift.

Treat mock adapter output as simulated; only live COM/SolidWorks results prove CAD correctness.

## Related specialist assets

If the SolidWorks MCP checkout contains these local files, load them only when the task specifically matches:

- `.github\skills\feature-tree-reconstruction\SKILL.md` — reverse engineering/reconstructing an existing SolidWorks model from real feature-tree evidence.
- `.github\skills\printer-profile-tolerancing\SKILL.md` — 3D-printable parts, build-volume checks, clearances, tolerances, snap fits, clips, hinges, and material/nozzle/layer-height tradeoffs.
- `.github\skills\extract-and-test\SKILL.md` — modifying the MCP project's own `pywin32_adapter.py` implementation and tests.
- `.github\agents\solidworks-part-reconstructor.agent.md` — additional reconstruction runbook.
- `.github\agents\solidworks-print-architect.agent.md` — additional additive-manufacturing runbook.

## Fallback when MCP tools are unavailable

Use Windows-native automation; do not use WSL for direct SolidWorks COM.

1. Generate a small PowerShell/Python/VBA/C# script in the workspace.
2. Run it from Windows PowerShell with a Python environment that has `pywin32`.
3. Rebuild and verify in SolidWorks.
4. Export requested formats.

Probe COM support:

```powershell
& "$env:SOLIDWORKS_MCP_HOME\.venv\Scripts\python.exe" -c "import win32com.client; print('win32com OK')"
```

## Verification checklist

For parts:

- Active document is a part.
- Rebuild succeeds or errors are reported.
- Key dimensions match the request.
- `.sldprt` exists and requested exports exist.

For assemblies:

- Active document is an assembly.
- Components resolve; no missing references.
- Mates/interference status checked when relevant.
- `.sldasm` exists and requested exports exist.

For drawings:

- Sheet size/template confirmed.
- Views and annotations generated.
- PDF/DXF export exists when requested.
