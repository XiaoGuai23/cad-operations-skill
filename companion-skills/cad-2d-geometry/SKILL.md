---
name: cad-2d-geometry
description: Use when 2D mechanical entities, orthographic or sectional views, dimensions, title blocks, parts lists, DXF files, or drawing layout are part of a requested CAD deliverable.
---

# CAD 2D Geometry

Use explicit 2D mechanical geometry. Revise the current authoritative CAD locally
by default; generate new drawings from a validated specification when appropriate.

## Choose the route

- **Existing drawing:** follow the local-revision steps below. Read the installed
  `cad-operations` skill's `references/mechanical-revision-design.md` for comment
  tracking, native annotation behavior and requested splitting.
- **New drawing or justified reconstruction:** read
  [new-drawing workflow](references/new-drawing-workflow.md). It contains the exact
  package interfaces, specification constraints and machine-readable handoff.
- **Environment or neighboring-change checks:** read the installed
  `cad-operations` skill's `references/review-and-environment.md`.
- **Related drawings, sample conventions, previews or common local edits:** read
  the installed `cad-operations/references/drawing-comparison-and-edits.md`.

## Existing drawing: preserve the current work

1. Use the user's designated or latest user-edited CAD; otherwise the last verified
   delivery. Relate it to the annotated PDF and earlier revisions, not just its
   timestamp. Preserve the baseline and hash, including any unsaved user changes.
2. Change only the authorized targets. Prefer native editing for DWG. A DXF round
   trip needs preservation checks for objects, layouts, styles and associations.
   Do not rerun an older generator over newer geometry, dimensions or layout.
3. Record comment IDs, targets, conditions and evidence. Recheck earlier active
   requirements; record supersession only where a later requirement replaces one.
   Synchronize a matching spec where faithful; otherwise mark it stale.
4. Save to a new revision, reopen outputs, check modified and neighboring areas,
   and derive requested previews from that result. Use `review` for local candidate
   diagnostics and matched views; it does not replace engineering or native checks.
5. Save the latest validated CAD/hash, completed/pending comments, unresolved
   issues and next action. Resume from that state rather than repeating changes.

Use `inventory` / `revise` for supported DXF edits; read the installed
`cad-operations/references/revision-tools.md` for field definitions and limits.
For repeated comments, use `cad-operations/references/comment-ledger.md` to carry
active requirements into each revision. Keep native DIMENSION/MULTILEADER when linked grip movement is required. Split
geometry only within requested scope; preserve HATCH and annotation semantics.
Approximate text-box warnings require visual confirmation.

When a change affects multiple drawings, bind explicit parameter relationships
and run `check-drawing-set`; mark out-of-scope drawings instead of editing them.
For sample-driven changes, use `compare-reference` to separate depiction from
preserved/adopted dimensions. Record existing user authorization for adoption.
Use shared `preview` for verified fonts, white masks and equal-scale crops;
inspect the images. Use `operations` to discover supported local edits before
writing a plan. All these checks preserve the authoritative source drawing.

## Boundaries

- No 3D model or 3D-derived views. Use native/DXF APIs, not raw hand-written DXF or
  a large ad hoc AutoLISP substitute for supported operations.
- Do not invent dimensions, fits, tolerances, materials or other engineering data.
  Resolve critical ambiguity; continue independent, unambiguous authorized work.
- Reconstruction requires explicit redraw intent, verified structural need, or an
  unrecoverable source. Rebuild the smallest necessary scope, reconciling current
  edits; executor limitations alone do not authorize lossy reconstruction.
- A structural DXF check does not prove native grip interaction or geometric
  associativity. Keep unsupported/unverified requirements visible.
- Preserve existing package status semantics. Requested DWG/PDF still need actual
  output verification; the generation-specific contract is in the linked reference.
