# Electrical Main SLD CAD

## Use this workflow for

电气主接线图、一次接线图、单线图、变电站主接线、配电系统图, SLD/one-line diagrams, bus arrangements, transformers, feeders, PT/CT, disconnectors, circuit breakers, earthing switches, reactors, capacitors, and title-block CAD/PDF deliverables.

## Standards and style basis

Use these as drafting references, not copied standard text:

- Symbol style: GB/T 4728 / IEC 60617.
- Documentation rules: GB/T 6988.1 / IEC 61082-1.
- Sheet/title block style: GB/T 14689 and GB/T 10609.1 when formal Chinese drafting style is requested.
- Default visual style: black lines, single-line conductors, open switch contacts, transformer circles, horizontal busbars, lower-right Chinese title block.

## Generation command

```powershell
python scripts/make_main_sld.py examples/electrical_sld_spec.json --outdir output/sld --stem main_sld
```

CLI options verified:

- `spec` positional JSON specification.
- `--outdir OUTDIR`
- `--stem STEM`
- `--no-dxf`, `--no-pdf`, `--no-svg`

Minimal spec:

```json
{
  "title": "电气主接线图",
  "sheet": {"width_mm": 440, "height_mm": 317},
  "title_block": {"专业": "电气", "比例": "示意", "日期": "2026-05-28"},
  "voltage_levels": [
    {"name": "110kV", "bus": "单母线", "feeders": ["出线1", "出线2", "出线3"], "transformers": ["1#主变", "2#主变"]},
    {"name": "35kV", "bus": "单母分段", "feeders": ["出线1", "出线2", "出线3", "出线4"], "pt": 2, "reactors": 2, "capacitors": 2}
  ]
}
```

## Requirement extraction

Capture or infer explicitly:

- Voltage levels: e.g. `330kV/110kV/35kV`.
- Bus arrangement per voltage: single bus, single bus sectionalized, double bus, double bus with bypass, ring bus, breaker-and-a-half, transformer low-voltage split bus.
- Bays: incoming lines, outgoing feeders, bus coupler/section, main transformers, reactors, capacitors, PT/VT, grounding switches.
- Labels: bay names, equipment numbers, bus conductor specs such as `35kV TMY-125×10`, title-block fields.
- Deliverables: DXF, PDF, SVG, PNG preview, or all.

Ask only when a missing value changes topology or engineering meaning. Otherwise proceed with visible assumptions.

## Topology patterns

- **Single bus / 单母线**: one horizontal bus, simple feeder bays.
- **Single bus sectionalized / 单母线分段**: two bus sections with section/coupler bay; split feeders and PT/reactive devices per section.
- **Double bus / 双母线**: two parallel busbars; use for 110kV/220kV+ when flexibility matters.
- **Double bus with bypass / 双母线带旁路**: draw only when requested; it clutters simplified diagrams.
- **Ring bus / 环形接线**: breakers form a ring; layout differs from horizontal bus drawings.
- **Breaker-and-a-half / 3/2 断路器接线**: two buses and vertical breaker strings; use when explicitly requested.
- **Transformer-linked multi-voltage**: highest voltage at top, transformers in transition area, lower-voltage split buses below.

Bay order heuristics: line bays left-to-right, transformers near center, PT near bus ends/sections, capacitors/reactors under 35kV buses, coupler near section split.

## Symbol conventions

- Busbar: horizontal line, thicker than branch conductors.
- Feeder/line: single vertical or horizontal conductor with terminal arrow/label.
- Circuit breaker: consistent contact/rectangle in series.
- Disconnector/isolator: open knife contact in series.
- Earthing switch: contact to ground symbol.
- Transformer: two/three overlapping circles, labelled `1#主变`, `2#主变`.
- PT/VT: transformer/circle symbol connected to bus, labelled `PT` or `母线PT`.
- Reactor: coil/zigzag/repeated loop marks.
- Capacitor bank: capacitor plate/stacked group.
- Surge arrester: arrester to ground if required.

## Connection audit checklist

Run after every non-trivial generation or coordinate edit:

- Bay counts exactly match the requirement.
- Every tap lands on the intended bus section, not in the physical sectionalizing gap or beyond bus endpoints.
- Different voltage levels are not directly wired except through intended transformer paths.
- Every intentional bus tap and branch join has an explicit conductor and visible junction dot.
- No junction dot appears where conductors only visually cross.
- Accidental non-connection crossings are removed or clearly bridged.
- Labels render in Chinese without raw newline squares, mojibake, or overlaps.
- If title/title block is removed, keep the right-bottom area intentionally blank or relayout deliberately.

## Delivery checklist

- DXF opens or is syntactically valid with `ezdxf.readfile`.
- PDF exists and page size matches the requested/assumed sheet.
- SVG/PNG preview exists when useful for visual review.
- Preview shows border, title block if requested, bus labels, bay labels, transformers/equipment, and no major overlaps.
- Assumptions about voltage levels, bay counts, topology, conductor specs, or title fields are stated.
