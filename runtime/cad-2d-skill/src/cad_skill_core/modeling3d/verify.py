"""Independent round-trip verification of exported 3D CAD artifacts."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
from math import isfinite
from pathlib import Path
import struct
from typing import Any

import ezdxf
import numpy as np

from .models import Model3DSpec


@dataclass(frozen=True, slots=True)
class Audit3DIssue:
    code: str
    severity: str  # "error" | "warning" | "info"
    message: str


@dataclass(frozen=True, slots=True)
class Audit3DReport:
    status: str  # "passed" | "failed"
    triangle_count: int
    vertex_count: int
    watertight: bool
    open_boundary_edges: int
    degenerate_faces: int
    actual_volume_mm3: float
    expected_volume_mm3: float | None
    volume_rel_error: float | None
    actual_bbox_mm: tuple[float, float, float, float, float, float]
    actual_spans_mm: tuple[float, float, float]
    issues: tuple[Audit3DIssue, ...]


def _read_binary_stl_from_disk(stl_path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Re-parse binary STL from disk and weld coincident vertices at 1e-5 mm grid."""

    raw = Path(stl_path).read_bytes()
    if len(raw) < 84:
        raise ValueError(f"STL file is truncated ({len(raw)} bytes): {stl_path}")
    (num_tris,) = struct.unpack("<I", raw[80:84])
    expected_bytes = 84 + num_tris * 50
    if len(raw) != expected_bytes or num_tris <= 0:
        raise ValueError(
            f"STL byte size {len(raw)} does not match declared {num_tris} triangles ({expected_bytes} bytes)"
        )
    stl_dtype = np.dtype(
        [
            ("normal", "<f4", (3,)),
            ("v0", "<f4", (3,)),
            ("v1", "<f4", (3,)),
            ("v2", "<f4", (3,)),
            ("attr", "<u2"),
        ]
    )
    records = np.frombuffer(raw, dtype=stl_dtype, offset=84, count=num_tris)
    tris = np.stack([records["v0"], records["v1"], records["v2"]], axis=1).astype(np.float64)  # (F, 3, 3)

    # Weld vertices quantized to 1e-4 mm (float32 STL precision is ~1e-5 * coordinate)
    flat_pts = tris.reshape(-1, 3)
    quantized = np.round(flat_pts * 10000.0).astype(np.int64)
    _, first_idx, inverse = np.unique(quantized, axis=0, return_index=True, return_inverse=True)
    unique_verts = flat_pts[first_idx]
    faces = inverse.reshape(-1, 3).astype(np.int32)
    return unique_verts, faces


def _verify_glb_on_disk(glb_path: Path, expected_tris: int) -> list[Audit3DIssue]:
    issues: list[Audit3DIssue] = []
    raw = Path(glb_path).read_bytes()
    if len(raw) < 28:
        return [Audit3DIssue("GLB_TRUNCATED", "error", f"GLB file is too short ({len(raw)} bytes)")]
    magic, version, total_len = struct.unpack("<III", raw[:12])
    if magic != 0x46546C67 or version != 2 or total_len != len(raw):
        issues.append(
            Audit3DIssue(
                "GLB_HEADER_INVALID",
                "error",
                f"invalid GLB header (magic={hex(magic)}, version={version}, len={total_len}/{len(raw)})",
            )
        )
        return issues
    json_len, json_type = struct.unpack("<II", raw[12:20])
    if json_type != 0x4E4F534A or 20 + json_len > len(raw):
        issues.append(Audit3DIssue("GLB_JSON_CHUNK_INVALID", "error", "corrupt GLB JSON chunk"))
        return issues
    gltf = json.loads(raw[20 : 20 + json_len].decode("utf-8"))
    idx_count = int(gltf["accessors"][0]["count"])
    if idx_count != expected_tris * 3:
        issues.append(
            Audit3DIssue(
                "GLB_TRIANGLE_COUNT_MISMATCH",
                "error",
                f"GLB index count {idx_count} != 3 * {expected_tris}",
            )
        )
    return issues


def verify_3d_artifacts(
    spec_3d: Model3DSpec,
    *,
    stl_path: Path,
    glb_path: Path | None = None,
    dxf3d_path: Path | None = None,
    step_path: Path | None = None,
    expected_volume_mm3: float | None = None,
    expected_bbox_mm: tuple[float, float, float, float, float, float] | None = None,
    volume_rel_tol: float = 0.03,
    bbox_abs_tol_mm: float = 0.6,
) -> Audit3DReport:
    """Reopen 3D files from disk and verify manifold watertightness, volume, and dimensions."""

    issues: list[Audit3DIssue] = []

    try:
        verts, faces = _read_binary_stl_from_disk(stl_path)
    except Exception as exc:
        report = Audit3DReport(
            status="failed",
            triangle_count=0,
            vertex_count=0,
            watertight=False,
            open_boundary_edges=0,
            degenerate_faces=0,
            actual_volume_mm3=0.0,
            expected_volume_mm3=expected_volume_mm3,
            volume_rel_error=None,
            actual_bbox_mm=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            actual_spans_mm=(0.0, 0.0, 0.0),
            issues=(Audit3DIssue("STL_READ_FAILED", "error", str(exc)),),
        )
        return report

    # 1. Degenerate triangle check
    v0 = verts[faces[:, 0]]
    v1 = verts[faces[:, 1]]
    v2 = verts[faces[:, 2]]
    areas = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1)
    degenerate_count = int(np.sum(areas <= 1e-10))
    if degenerate_count > 0:
        issues.append(
            Audit3DIssue(
                "MESH_DEGENERATE_FACES",
                "error",
                f"mesh contains {degenerate_count} zero-area/degenerate triangle(s)",
            )
        )

    # 2. Manifold watertightness check (directed half-edge cancellation)
    directed_counts: Counter[tuple[int, int]] = Counter()
    for a, b, c in faces:
        ia, ib, ic = int(a), int(b), int(c)
        directed_counts[(ia, ib)] += 1
        directed_counts[(ib, ic)] += 1
        directed_counts[(ic, ia)] += 1

    open_edges = 0
    for (u, v), fwd_count in directed_counts.items():
        rev_count = directed_counts.get((v, u), 0)
        if fwd_count != rev_count:
            open_edges += abs(fwd_count - rev_count)
    watertight = open_edges == 0 and degenerate_count == 0
    if open_edges > 0:
        issues.append(
            Audit3DIssue(
                "MESH_NOT_WATERTIGHT",
                "error",
                f"mesh has {open_edges} unmatched boundary/non-manifold half-edge(s)",
            )
        )

    # 3. Signed volume & analytical comparison
    actual_vol = float(np.sum(np.einsum("ij,ij->i", v0, np.cross(v1, v2))) / 6.0)
    if not isfinite(actual_vol) or actual_vol <= 0.0:
        issues.append(
            Audit3DIssue(
                "MESH_NONPOSITIVE_VOLUME",
                "error",
                f"reopened mesh has non-positive signed volume ({actual_vol:.4f} mm^3); normals may be inverted",
            )
        )

    vol_rel_err: float | None = None
    if expected_volume_mm3 is not None and expected_volume_mm3 > 0:
        vol_rel_err = abs(actual_vol - expected_volume_mm3) / expected_volume_mm3
        if vol_rel_err > volume_rel_tol:
            issues.append(
                Audit3DIssue(
                    "VOLUME_MISMATCH",
                    "error",
                    f"reopened volume {actual_vol:.2f} mm^3 differs from expected {expected_volume_mm3:.2f} mm^3 by {vol_rel_err*100:.2f}%",
                )
            )

    # 4. 3D Bounding box check
    mins = verts.min(axis=0)
    maxs = verts.max(axis=0)
    actual_bbox = (
        round(float(mins[0]), 4),
        round(float(mins[1]), 4),
        round(float(mins[2]), 4),
        round(float(maxs[0]), 4),
        round(float(maxs[1]), 4),
        round(float(maxs[2]), 4),
    )
    spans = (
        round(float(maxs[0] - mins[0]), 4),
        round(float(maxs[1] - mins[1]), 4),
        round(float(maxs[2] - mins[2]), 4),
    )
    if expected_bbox_mm is not None:
        exp_spans = (
            expected_bbox_mm[3] - expected_bbox_mm[0],
            expected_bbox_mm[4] - expected_bbox_mm[1],
            expected_bbox_mm[5] - expected_bbox_mm[2],
        )
        for axis_name, actual_s, exp_s in zip(("X", "Y", "Z"), spans, exp_spans):
            if abs(actual_s - exp_s) > max(bbox_abs_tol_mm, exp_s * 0.015):
                issues.append(
                    Audit3DIssue(
                        "BBOX_DIMENSION_MISMATCH",
                        "error",
                        f"3D {axis_name}-span {actual_s:.3f} mm differs from spec {exp_s:.3f} mm",
                    )
                )

    # 5. GLB & 3D DXF verification
    if glb_path is not None:
        issues.extend(_verify_glb_on_disk(glb_path, int(faces.shape[0])))

    if dxf3d_path is not None:
        try:
            doc = ezdxf.readfile(dxf3d_path)
            auditor = doc.audit()
            if auditor.has_errors:
                issues.append(
                    Audit3DIssue(
                        "DXF3D_AUDIT_ERROR",
                        "error",
                        f"3D DXF reported {len(auditor.errors)} ezdxf audit error(s)",
                    )
                )
            meshes = list(doc.modelspace().query("MESH"))
            if not meshes:
                issues.append(Audit3DIssue("DXF3D_EMPTY", "error", "3D DXF contains no MESH entity"))
        except Exception as exc:
            issues.append(Audit3DIssue("DXF3D_OPEN_FAILED", "error", str(exc)))

    if step_path is not None:
        issues.extend(_verify_step_on_disk(step_path, int(faces.shape[0])))

    # 6. Spec validation blocking flags
    for m in spec_3d.validation.missing:
        issues.append(Audit3DIssue("SPEC3D_MISSING_PARAM", "error", m))
    for c in spec_3d.validation.conflicts:
        issues.append(Audit3DIssue("SPEC3D_CONFLICT", "error", c))

    has_errors = any(i.severity == "error" for i in issues)
    return Audit3DReport(
        status="failed" if has_errors else "passed",
        triangle_count=int(faces.shape[0]),
        vertex_count=int(verts.shape[0]),
        watertight=watertight,
        open_boundary_edges=open_edges,
        degenerate_faces=degenerate_count,
        actual_volume_mm3=round(actual_vol, 3),
        expected_volume_mm3=round(expected_volume_mm3, 3) if expected_volume_mm3 is not None else None,
        volume_rel_error=round(vol_rel_err, 6) if vol_rel_err is not None else None,
        actual_bbox_mm=actual_bbox,
        actual_spans_mm=spans,
        issues=tuple(issues),
    )


def write_audit_3d_reports(report: Audit3DReport, json_path: Path, md_path: Path, *, project_name: str) -> None:
    """Persist machine-readable audit-3d.json and Chinese audit-3d-report.md."""

    payload: dict[str, Any] = asdict(report)
    payload["issues"] = [asdict(i) for i in report.issues]
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md_lines = [
        f"# 三维模型质量审计报告：{project_name}",
        "",
        f"- **综合结论**：`{report.status}`",
        f"- **流形水密性（Watertight）**：`{report.watertight}`（未闭合半边数：{report.open_boundary_edges}，退化面数：{report.degenerate_faces}）",
        f"- **网格规模**：顶点数 `{report.vertex_count}`，三角面片数 `{report.triangle_count}`",
        f"- **实测体积**：`{report.actual_volume_mm3} mm³`"
        + (
            f"（理论解析体积：`{report.expected_volume_mm3} mm³`，相对偏差：`{(report.volume_rel_error or 0)*100:.3f}%`）"
            if report.expected_volume_mm3 is not None
            else ""
        ),
        f"- **三维外廓尺寸 (X × Y × Z)**：`{report.actual_spans_mm[0]} × {report.actual_spans_mm[1]} × {report.actual_spans_mm[2]} mm`",
        "",
        "## 审计问题明细",
    ]
    if not report.issues:
        md_lines.append("- 无错误或警告，三维水密拓扑与几何尺寸核验全部通过。")
    else:
        for issue in report.issues:
            md_lines.append(f"- **[{issue.severity.upper()}] `{issue.code}`**：{issue.message}")
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")


def _verify_step_on_disk(step_path: Path, expected_faces: int) -> list[Audit3DIssue]:
    import re

    issues: list[Audit3DIssue] = []
    try:
        txt = Path(step_path).read_text(encoding="ascii")
    except Exception as exc:
        return [Audit3DIssue("STEP_READ_FAILED", "error", str(exc))]

    if not txt.startswith("ISO-10303-21;") or "END-ISO-10303-21;" not in txt:
        issues.append(Audit3DIssue("STEP_HEADER_INVALID", "error", "missing ISO-10303-21 envelope"))
        return issues
    if "FACETED_BREP(" not in txt or "CLOSED_SHELL(" not in txt or "SI_UNIT(.MILLI.,.METRE.)" not in txt:
        issues.append(Audit3DIssue("STEP_TOPOLOGY_MISSING", "error", "STEP missing FACETED_BREP, CLOSED_SHELL, or mm SI_UNIT"))
        return issues

    defined_ids = {int(m.group(1)) for m in re.finditer(r"^#(\d+)\s*=", txt, re.MULTILINE)}
    ref_ids = {int(m.group(1)) for m in re.finditer(r"(?<!^)#(\d+)", txt, re.MULTILINE)}
    dangling = ref_ids - defined_ids
    if dangling:
        issues.append(Audit3DIssue("STEP_DANGLING_REF", "error", f"STEP contains {len(dangling)} unresolved #id reference(s)"))

    face_count = txt.count("= FACE_SURFACE(")
    if face_count != expected_faces:
        issues.append(
            Audit3DIssue(
                "STEP_FACE_COUNT_MISMATCH",
                "error",
                f"STEP FACE_SURFACE count {face_count} != expected {expected_faces}",
            )
        )
    return issues
