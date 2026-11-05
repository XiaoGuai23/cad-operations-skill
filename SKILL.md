---
name: cad-operations
description: Unified local CAD operations skill for creating, editing, validating, and exporting CAD deliverables. Use when the user asks for CAD/DWG/DXF/PDF/SVG/PNG drafting, AutoCAD/Core Console, FreeCAD/FreeCAD MCP, SolidWorks/SolidWorks MCP/COM/VBA/C# automation, SLDPRT/SLDASM/STEP/STL files, electrical main single-line diagrams/电气主接线图/一次接线图/SLD, pavement structure drawings/路面结构图, or any local CAD workflow that should use installed CAD tools, scripts, and MCP assets.
---

# CAD Operations

Use the narrowest reproducible workflow for the requested CAD task. For revisions,
edit the current authoritative drawing; for new drawings use a validated spec.

## Route by task

| Task | Read when needed |
| --- | --- |
| Mechanical comments, movable annotations, splitting | [Revision method](references/mechanical-revision-design.md) and [revision commands](references/revision-tools.md) |
| Cross-revision comment checks | [Comment ledger](references/comment-ledger.md) |
| Related drawings, sample comparisons, shared preview and common edits | [Comparison and editing tools](references/drawing-comparison-and-edits.md) |
| Local before/after checks or environment readiness | [Review and environment commands](references/review-and-environment.md) |
| General AutoCAD/FreeCAD editing, plotting, conversion | [AutoCAD / FreeCAD](references/autocad-freecad.md) |
| Electrical main wiring / SLD | [Electrical workflow](references/electrical-main-sld.md) |
| Pavement / road layer sections | [Pavement workflow](references/pavement-structure-cad.md) |
| SolidWorks parts, assemblies or drawings | [SolidWorks workflow](references/solidworks-mcp.md) |
| Machine paths, MCP availability or setup | [Local environment](references/local-cad-environment.md) |

Read only the branch needed for the task. Existing command examples are in
[command examples](references/command-examples.md); task-specific scripts remain
in this skill's `scripts/` directory.

## Preserve the baseline

- Select the user's designated/latest edited CAD or the last verified delivery;
  check its relation to comments instead of selecting by timestamp alone.
- Preserve a copy and hash. Edit locally and export from that revision; an old
  generator must not overwrite newer geometry or layout. Mark stale specs.
- Prefer native DWG editing. Use DXF interchange when appropriate and verify
  conversion preservation. Keep native SolidWorks formats when requested.
- Write task inputs, generated files and progress in the active workspace.
  Keep originals recoverable, including when in-place editing was requested.

## Check the affected work

- At first use or after an environment change, run `doctor` with a task-local
  cache. Native failure evidence may be reused while unchanged; do not repeatedly
  retry the same blocked installation. Continue independent available work.
- After local geometry/annotation edits, run `review` on the baseline and result
  for changed neighborhoods, then inspect its paired views. Candidates include
  straight/circular gaps and overlaps, detached hatch boundaries and text collisions.
  Review intent and coverage limits before accepting or repairing a finding.
- Reopen final CAD; check active comments and untouched/neighboring content.
  Inspect final previews when appearance matters. Native grip behavior and
  geometric associativity require separate representative checks.
- Confirm requested output files exist and are nonempty. DXF syntax or a script's
  exit code alone does not establish visual or engineering correctness. Native
  SolidWorks requires real rebuild/status evidence; mock results are not proof.
- Report actual output paths, validation scope and unresolved assumptions.
  Retain the existing delivery/status contracts; do not silently mark native
  checks passed when only structural checks were performed.
