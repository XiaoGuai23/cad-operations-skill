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
