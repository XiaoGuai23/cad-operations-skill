# New drawings and justified reconstruction

## Reproducible specification-driven generation

For new drawings or justified reconstruction, use the top-level package pipeline
for a task-directory delivery. It already
combines the real layout planner, deterministic writer, annotation application,
bounded inspection, fresh DXF audit, preview renderer, immutable canonical
specification materialization, and atomic publication:

```python
from pathlib import Path
from cad_skill_core.pipeline import PipelineOptions, run_task

result = run_task(Path(TASK_DIR), PipelineOptions(run_autocad=False))
```

The equivalent CLI is:

```text
cad-skill run ABSOLUTE_TASK_DIR --no-autocad
```

Place the validated authoritative file at `input/drawing-spec.json` or
`work/drawing-spec.json`. When complete signed-off values are supplied without
a file, serialize those exact values into the current `DrawingSpec` schema and
validate it once; do not add defaults or reinterpret the values. Record the
authoritative bytes and SHA-256 before running. The pipeline must leave those
bytes unchanged and records the canonical materialization in
`audit.json.provenance`.

Before the top-level run, load the real project profile and call `plan_layout`
against the validated spec as a feasibility preflight. A
`LayoutOverflowError` means the signed-off sheet/scale combination is not
deliverable: report the suggested scale or larger-sheet requirement. Never
monkeypatch spacing, protected zones, layers, colors, or other profile values
to force a pass. After a feasible preflight, run the package immediately rather
than rebuilding its orchestration by hand.

Map signed values to what the current schema can actually express. Keep the
flange diameters, hole pattern, and PCD as traceable numeric geometry parameters
and explicit 2D entities. `Dimension(kind, value, source_id)` currently lays out
a generic linear span; it cannot encode a diameter/quantity/PCD callout. Preserve
signed strings such as `Ø200` or `4×Ø16 EQ SP ON Ø120 PCD` as traceable
`Annotation` records instead of pretending they are generic linear dimensions.
This preserves content and provenance only; it does not satisfy a requested
native dimension or linked-leader interaction. Keep that behavior requirement
unfulfilled until the installed implementation can express and verify it.
Likewise, use a traceable `MATERIAL: ...` annotation when the current title-block
contract has no material slot. This is faithful interface mapping, not permission
to omit the underlying numeric parameters or provenance.

In the current schema, `requirements.material` and each `technical_notes` entry
must be `{"text": "exact supplied text", "source_id": "known-provenance-id"}`.
Every parts-list item also requires its known `source_id`. Preserve these
references when serializing or repairing; bare strings do not pass validation.
Fixed paper-sized text overflow reports `wrap-content-or-larger-sheet`; reducing
geometry scale cannot resolve that condition. Vertical reserved-zone capacity
reports `larger-sheet-required`.

For an embedded geometry-only caller that does not need task publication, use
the same real interfaces: `load_spec` and `load_standard`; obtain a verified
`Mapping[str, ResolvedFont]` through the font subsystem; run `plan_layout`;
call `write_dxf`; re-open its path with `ezdxf.readfile`; call
`apply_annotations(document, plan, spec, standard, fonts)`; persist through
`save_dxf_deterministic`; then call `render_preview` with the same font mapping.
Re-open and audit the final persisted DXF before claiming delivery. Do not
replace these calls with generated DXF syntax or one-off geometry code.

## Evidence from the delivered files

For existing-drawing revisions, inspect the saved outputs and report the baseline
and result hashes, changed target mapping, comment acceptance and preservation
checks. Do not invent a canonical spec or use a first-`N` entity grouping when none
exists. A task is complete when its requested outputs and checks are satisfied.

For specification-driven generation after the package returns, inspect the
published paths, not an in-memory document or planned counts:

1. Read the published `audit.json`. Use its `provenance.spec_source`, source
   hash, `canonical_spec`, and canonical file hash. Compare the authoritative
   spec's bytes with the pre-run snapshot.
2. Re-open the published DXF and run its auditor. Read `$INSUNITS`, enumerate
   modelspace once in file order, and record each entity's `dxftype()` and
   handle. Counts must be derived from that enumeration.
3. For an unchanged one-to-one generation, let `N` be the number of canonical `geometry.entities`. The writer preserves
   specification order and layout transforms those source entities in place,
   so the first `N` delivered modelspace handles form the requested view-source
   group. For this Skill's single-front-view handoff, name it
   `view:front-source` with role `front-view-source`. If an authorized operation
   splits source entities, use its explicit source-to-output handle mapping
   instead of this first-`N` shortcut; do not claim the single-front-view handoff
   contract below until its grouping requirements can actually be satisfied.
4. Partition every remaining handle into ordered, non-empty annotation groups.
   A stable auditable choice is each contiguous run of `(layer, DXF type)`, with
   IDs such as `annotation:1:FRAME:LWPOLYLINE` and role `sheet-annotation`.
   Groups must not overlap; concatenating their handles must reproduce the full
   delivered handle list exactly.
5. Hash the published SVG and PNG. Open the PNG to record its pixel dimensions.
   Report the exact absolute paths returned by the package, including audit
   JSON and Markdown report. If AutoCAD was not requested, the correct package
   status is `partial`, not `complete`.

## Geometry delivery handoff

For a specification-generated single-front-view handoff, when a machine-readable
release is requested, return exactly one strict JSON object—no Markdown fence or
prose—with this shape. Existing-drawing revisions use their actual baseline/result
provenance and target mappings instead; do not fabricate a specification to fit
this generation-specific contract:

```json
{
  "schema": "GeometryDeliveryEvidence/v1",
  "scope": "2d",
  "status": "partial",
  "task_dir": "absolute task path",
  "specification": {
    "authoritative_path": "absolute drawing-spec.json path",
    "authoritative_sha256": "64 lowercase hex characters",
    "canonical_path": "absolute canonical JSON path",
    "canonical_sha256": "64 lowercase hex characters",
    "authoritative_bytes_preserved": true
  },
  "dxf": {
    "path": "absolute published DXF path",
    "sha256": "64 lowercase hex characters",
    "units": "mm",
    "audit_errors": 0,
    "entity_counts": {"CIRCLE": 1},
    "handles": ["one handle per delivered modelspace entity, in file order"],
    "groups": [
      {
        "id": "view:front-source",
        "role": "front-view-source",
        "handles": ["source-mapped delivered handles; first N only for unchanged one-to-one generation"]
      },
      {
        "id": "annotation:1:FRAME:LWPOLYLINE",
        "role": "sheet-annotation",
        "handles": ["a non-empty contiguous remainder"]
      }
    ]
  },
  "previews": {
    "svg": {"path": "absolute SVG path", "sha256": "64 lowercase hex characters"},
    "png": {
      "path": "absolute PNG path",
      "sha256": "64 lowercase hex characters",
      "width_px": 1,
      "height_px": 1
    }
  },
  "outputs": ["every exact published core artifact and audit/report path"]
}
```

Emit standard JSON only: no duplicate keys, `NaN`, or infinities. A proved
successful handoff has `scope: "2d"`, preserved authoritative bytes, a distinct
canonical path, zero DXF audit errors, positive entity counts whose sum equals
the unique handle count, exactly one front-source group, an exhaustive ordered
group partition, valid previews, and all published paths. Use `null` or `false`
for absent evidence and do not claim success when any required evidence is
absent.

## Common mistakes

| Mistake | Correct action |
|---|---|
| Counting the pre-layout source DXF | Count the published, reopened DXF |
| Reporting intended entities without handles | Enumerate actual modelspace handles |
| For specification-driven generation: treating the user's input spec as the pipeline canonical file | Report both spec identities; existing-CAD revisions instead record their actual CAD baseline and result |
| Calling DXF/SVG/PNG delivery complete | Report `partial` until requested DWG/PDF are verified |
| Regenerating an existing revised drawing from an older spec | Edit the current authoritative CAD locally; synchronize or mark the spec stale |
