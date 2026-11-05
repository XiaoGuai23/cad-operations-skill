# AutoCAD / FreeCAD Notes

## AutoCAD paths

Set paths through environment variables:

```powershell
$env:AUTOCAD_EXE = "C:\Program Files\Autodesk\AutoCAD 2025\acad.exe"
$env:ACCORECONSOLE_EXE = "C:\Program Files\Autodesk\AutoCAD 2025\accoreconsole.exe"
```

Open a DXF/DWG:

```powershell
& $env:AUTOCAD_EXE "output\drawing.dxf"
```

Use Core Console for scriptable operations only when the command sequence is simple and tested. AutoCAD command names/localization/profile behavior can vary. For an existing drawing, use a supported native edit/save route; for new drawings, DXF generation is an option. Inspect the saved result with a rendered preview or GUI as the task requires.

## DXF/DWG strategy

- For revisions, the current authoritative CAD file is the editing baseline: prefer the user's designated/latest edited file, otherwise the verified last delivery. Preserve a copy and its hash, make local changes, then export from the revised file.
- For new specification-driven drawings, the validated specification is the parameter source and DXF is an output/interchange format. Never rebuild an existing revised CAD file from a stale specification or script.
- Edit existing DWG natively when supported. A DWG-to-DXF round trip is not mandatory; if used, verify that annotations, hatches, blocks, layouts and other relevant native data survive it. Tool limitations do not authorize silent flattening or global reconstruction.
- Validate DXF with `ezdxf.readfile` when possible.
- Convert/save as DWG only when the user explicitly requires DWG.
- If a file is locked/open and cannot be overwritten, write a versioned filename and report the new canonical file.
- For mechanical revision scope, baseline records, regeneration exceptions and resumable progress, read [the revision design method](mechanical-revision-design.md), section 4.

## FreeCAD discovery

Locate FreeCAD before use:

```powershell
Get-ChildItem -Path 'C:\Program Files','D:\Program Files',$env:LOCALAPPDATA -Recurse -Include FreeCAD.exe,FreeCADCmd.exe -ErrorAction SilentlyContinue | Select-Object FullName
```

Use FreeCAD when the deliverable should be parametric, model-based, `.FCStd`, or when an MCP-controlled FreeCAD session is explicitly requested. For simple 2D construction details, generated DXF is usually faster and more reproducible.

## FreeCAD / FreeCAD MCP options

Known public MCP options:

- `neka-nat/freecad-mcp`: Python MCP server for controlling FreeCAD, object creation/modification, and FreeCAD Python execution.
- `contextform/freecad-mcp`: FreeCAD MCP bridge intended for MCP clients.

Exact setup can change; check the repository README before installation/configuration. If MCP is unavailable but FreeCAD is installed, use `FreeCADCmd.exe` with a generated Python script.

FreeCADCmd fallback pattern:

```powershell
& "C:\Program Files\FreeCAD 1.0\bin\FreeCADCmd.exe" "scripts\make_model.py"
```

The script should read the same JSON/spec source, create geometry, add labels/dimensions if Draft/TechDraw is available, save `.FCStd`, and export DXF/SVG/PDF if requested.
