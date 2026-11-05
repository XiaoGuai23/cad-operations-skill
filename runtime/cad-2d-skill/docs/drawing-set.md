# Related drawing verification

`cad_skill_core.revision.drawing_set.check_drawing_set(manifest_path: Path) -> dict`
checks explicit shared parameters against real geometry in bound DXF files. It
does not create, save, repair, rebase or modify drawings, the manifest or sidecars.
The caller may serialize the returned report elsewhere.

CLI: `python -m cad_skill_core check-drawing-set MANIFEST.json --json`.
Exit 0 means checked; 1 means a needs_update/blocked report; 2 means invalid input.

## Manifest contract

Use strict JSON with schema `cad-drawing-set/v1`. All fields shown below are
required; unknown fields, duplicate JSON keys and nonfinite numbers are rejected.
Replace the example hashes with SHA-256 digests of the actual source files, and
use selectors inspected from those files.

```json
{
  "schema": "cad-drawing-set/v1",
  "units": "mm",
  "drawings": [
    {
      "id": "assembly",
      "path": "assembly.dxf",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "editable": true
    },
    {
      "id": "part",
      "path": "part.dxf",
      "sha256": "1111111111111111111111111111111111111111111111111111111111111111",
      "editable": false
    }
  ],
  "parameters": [
    {
      "id": "thickness",
      "expected": 20,
      "tolerance": 0.01,
      "members": [
        {"drawing": "assembly", "selector": {"handle": "2F"}, "measure": "dimension"},
        {"drawing": "part", "selector": {"id": "plate.thickness"}, "measure": "length"}
      ]
    }
  ]
}
```

- `units`: one of `mm`, `cm`, `m`, `in`, `ft`, matching each DXF's `$INSUNITS`
  exactly. There is no conversion or inferred unit assignment; unitless drawings
  are blocked.
- `drawings`: nonempty list with unique, nonempty IDs. Relative paths are resolved
  against the manifest directory. Paths may be absolute. File identity is checked
  for aliases, including relative aliases, symlinks and hard links. Two drawing
  IDs referring to one physical file are both blocked. Distinct copied files may
  have identical content and hashes.
- `sha256`: exactly 64 hexadecimal characters, case insensitive. Parsing uses the
  exact verified byte snapshot, including binary DXF and legacy text encodings.
  DXF audit errors or automatic fixes block the file; repairs are never accepted
  as the bound source. The shared `read_dxf_snapshot(data: bytes)` helper returns
  this audited in-memory document or raises `RevisionError`.
- `editable`: explicit boolean recording the task's scope for that entire drawing.
  Reports label this `editable` or `out_of_scope`. An out-of-scope mismatch remains
  a mismatch; this checker never edits either category.
- `parameters`: nonempty list with unique IDs, a finite nonnegative `expected`
  value, finite positive absolute `tolerance`, and a nonempty `members` list.
  Every member is compared with the expected value, rather than only with its
  neighbors. Agreement on an incorrect value therefore does not pass.
- `selector`: exactly `{"handle": "HEX"}` or `{"id": "stable.id"}`. Handles are
  case insensitive. IDs match the first string in `CAD_SKILL_ID` XDATA. Exactly one
  live modelspace entity must match. Two members resolving to the same drawing,
  entity and measurement are blocked, even when one uses a handle and the other
  uses a stable ID.

## Supported measurements

The reusable API is
`measure_feature(doc, selector: dict, measure: str) -> float`.
It reads the supplied document without modifying it and returns native drawing
units. Invalid or unsupported input raises `RevisionError`.

| `measure` | Supported entity | Measurement |
| --- | --- | --- |
| `dimension` | Native DIMENSION types 0, 1, 3, 4 | Linear projection along the dimension angle; aligned endpoint distance; diameter or radius endpoint distance |
| `length` | LINE | Endpoint distance |
| `radius` | CIRCLE, ARC | Geometric radius |
| `diameter` | CIRCLE, ARC | Twice the geometric radius |

DIMENSION text, cached `actual_measurement`, dimension styles and `DIMLFAC` do not
replace the geometric reading. A dimension whose endpoints span 10 remains 10 even
when its label says 20. Conversely, this API is not a displayed-label validation
or a proof that dimension definition points are associated with the intended
contour; include the contour as a separate explicit member when that matters.

Only finite, nondegenerate geometry parallel to the XY plane is supported. A
constant nonzero elevation is accepted. Non-default extrusion, nonzero thickness,
varying endpoint elevations (greater than `1e-9` drawing units), zero measurements,
and missing required point/radius data are rejected. Extrusion comparison uses
`1e-12` numerical tolerance, independently of the parameter's tolerance.
Angular and ordinate dimensions, polylines, splines, block contents, XREFs and
paperspace entities are outside this version's measurement contract.

## Report and status

The report schema is `cad-drawing-set-report/v1`. It includes `manifest_path`,
`manifest_sha256`, `units`, `status`, `drawings`, `parameters` and `blockers`.

- A drawing entry records its ID, resolved path, expected and actual SHA-256,
  numeric DXF unit code, editable scope, status and blocker evidence. Successfully
  loaded drawings also get `final_sha256` after measurements.
- A parameter entry records `id`, `expected`, `tolerance`, `status` and `members`.
  Members retain their selector and measure, resolved `handle` when available,
  `actual`, signed `delta = actual - expected`, editable scope and status.
- A blocker has `code`, `message` and applicable `drawing`/`parameter` IDs.
  Codes include `missing_file`, `unreadable_file`, `hash_mismatch`,
  `invalid_drawing`, `unit_mismatch`, `aliased_path`, `unmeasurable_member`,
  `duplicate_member` and `source_changed`.

`checked` means all required evidence was available and every absolute delta was
within tolerance. `needs_update` means verified readings differ from expectations.
`blocked` means at least one required source or reading could not be verified, and
takes precedence over `needs_update`. Even an unreferenced drawing listed in the
manifest can block the entire set if its source is unavailable or invalid.

Unmeasured members have `actual: null` and `delta: null`; they never count as passed.
If a source changes during the check, its previously measured snapshot values are
retained as evidence but its drawing and member statuses become `blocked`.
The final hash check detects changes through the check's completion; it does not
lock files or guarantee their state after the call returns.

Malformed manifests (including duplicate drawing/parameter IDs, invalid selectors,
unknown drawing references or unsupported measurement names) raise `RevisionError`.
Valid manifests with missing, changed, unreadable or unsupported drawing evidence
return a blocked report so remaining verified readings are still reviewable.

This is DXF relationship evidence only. It does not claim native DWG reopening,
font fidelity, visual approval, grip editing or cross-file automatic updates.
