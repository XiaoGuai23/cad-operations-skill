# Pavement Structure CAD

## Use this workflow for

路面结构图, pavement cross-section diagrams, road layer drawings, CAD/DWG/DXF road structure details, layered cross-sections, thickness labels, dimensions, hatching, and title notes.

## Ground rules

- Treat all dimensions as millimeters unless the user says otherwise.
- Preserve true layer thickness in labels even when vertical exaggeration is used for readability.
- Generate files in the active workspace, not in the skill directory.
- For Chinese road drawings, use Chinese labels by default and include material + thickness, e.g. `AC-13C 细粒式沥青混凝土 4cm`.
- If requirements are incomplete, make explicit assumptions and keep them editable in the JSON spec.

## DXF-first workflow

1. Extract road type, width, layer order top-to-bottom, material names, thicknesses, slope/crown, base/subbase/subgrade notes, drawing scale, and title block text.
2. Create a JSON spec in the workspace, e.g. `路面结构图_spec.json`.
3. Generate DXF:

```powershell
python scripts/generate_pavement_cad_ezdxf.py examples/pavement_structure_spec.json --out output/pavement
```

4. Inspect in AutoCAD/FreeCAD/viewer. If `AUTOCAD_EXE` is configured, add `--open --acad $env:AUTOCAD_EXE`.
5. If edits are needed, change the JSON spec and regenerate.

CLI options verified for both pavement generators:

- `spec` positional JSON file.
- `--out OUT`
- `--open`
- `--acad ACAD`

## JSON schema

All dimensions are millimeters unless otherwise stated.

```json
{
  "title": "路面结构图",
  "project": "工程名称",
  "designer": "设计",
  "reviewer": "复核",
  "date": "2026-05-14",
  "drawing_no": "LM-01",
  "output_basename": "pavement_structure",
  "units": "mm",
  "width": 10000,
  "vertical_exaggeration": 5,
  "scale_note": "水平按实际尺寸，竖向放大5倍示意",
  "centerline": true,
  "surface_slope_percent": 1.5,
  "layers": [
    {"name": "细粒式沥青混凝土", "material": "AC-13C", "thickness": 40, "color": 1, "hatch": "diagonal", "note": "上面层"}
  ],
  "annotations": ["未注明单位均为 mm", "结构层厚度以施工图为准"]
}
```

Common defaults:

- `width`: `10000`
- `vertical_exaggeration`: `5`
- `output_basename`: derived from `title`
- `layers[].color`: cycles through AutoCAD color indexes
- `hatch`: `diagonal`
- `units`: `mm`
- `centerline`: `true`

## Layer examples

Asphalt pavement:

```json
"layers": [
  {"name": "细粒式沥青混凝土", "material": "AC-13C", "thickness": 40},
  {"name": "中粒式沥青混凝土", "material": "AC-20C", "thickness": 60},
  {"name": "粗粒式沥青混凝土", "material": "AC-25C", "thickness": 80},
  {"name": "水泥稳定碎石基层", "material": "5% 水泥稳定碎石", "thickness": 200},
  {"name": "级配碎石底基层", "material": "级配碎石", "thickness": 180}
]
```

Cement concrete pavement:

```json
"layers": [
  {"name": "水泥混凝土面板", "material": "C35 水泥混凝土", "thickness": 260},
  {"name": "水泥稳定碎石基层", "material": "5% 水泥稳定碎石", "thickness": 180},
  {"name": "级配碎石底基层", "material": "级配碎石", "thickness": 150}
]
```

## Quality checklist

- Layer order is top-to-bottom.
- Thickness labels match the source requirements.
- Total structural thickness is shown if requested or useful.
- Width dimension is shown.
- Vertical exaggeration note is present when not 1:1.
- DXF exists, is non-empty, and opens in AutoCAD/FreeCAD/viewer.
