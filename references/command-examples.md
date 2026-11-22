# Local command examples

## Quick commands

Electrical SLD:

```powershell
python scripts/make_main_sld.py examples/electrical_sld_spec.json --outdir output/sld --stem main_sld
```

Pavement structure DXF:

```powershell
python scripts/generate_pavement_cad_ezdxf.py examples/pavement_structure_spec.json --out output/pavement
```

Open a generated CAD file in AutoCAD when configured:

```powershell
& $env:AUTOCAD_EXE "output\pavement\pavement_structure.dxf"
```

Start a local SolidWorks MCP server for diagnostics when installed:

```powershell
Set-Location -LiteralPath $env:SOLIDWORKS_MCP_HOME
& ".\.venv\Scripts\python.exe" -m solidworks_mcp.server
```

## Unified `cad_skill_core.cli` commands (2D, 3D, Domestic CAD Drivers, MCP & Web Studio)

Compile an engineering brief / table into a validated `DrawingSpec` and learn standards from a sample DXF:

```powershell
$env:PYTHONPATH="runtime\cad-2d-skill\src"
python -B -m cad_skill_core.cli compile-spec examples/project_brief.md --out output/drawing-spec.json --json
python -B -m cad_skill_core.cli learn-standard sample.dxf --out output/custom-standard.yaml --json
```

Generate watertight 3D models (`.stl`/`.obj`/`.glb`/`model3d.dxf`/`iso.png`) + coupled GB/T 2D multi-view engineering sheet:

```powershell
$env:PYTHONPATH="runtime\cad-2d-skill\src"
python -B -m cad_skill_core.cli generate-3d model3d-spec.json --out output/part3d --with-2d-sheet --json
```

Probe local AutoCAD (2021–2026) and domestic CAD drivers (中望 ZWCAD / 浩辰 GstarCAD / CAXA / ODA) & verify DWG↔DXF round-trip:

```powershell
$env:PYTHONPATH="runtime\cad-2d-skill\src"
python -B -m cad_skill_core.cli drivers --json
python -B -m cad_skill_core.cli verify-roundtrip before.dxf after.dxf --json
```

Start the MCP Server (`stdio`) or Web Auto-Drafting Studio (`http://127.0.0.1:8765/studio`):

```powershell
$env:PYTHONPATH="runtime\cad-2d-skill\src"
python -B -m cad_skill_core.cli mcp-stdio --workspace output/mcp_workspace
python -B -m cad_skill_core.cli mcp-http --host 127.0.0.1 --port 8765 --workspace output/mcp_workspace
```
