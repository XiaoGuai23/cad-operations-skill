"""Deterministic watertight manifold 3D triangle mesh kernel and analytical properties."""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, hypot, pi, sin
from typing import Sequence

import numpy as np

from .models import (
    BearingCap3D,
    Body3D,
    Box3D,
    BoxBoss3D,
    CompoundSolid3D,
    CounterboreHole3D,
    Cylinder3D,
    CylinderBoss3D,
    ExtrudePolygon3D,
    Flange3D,
    Model3DSpec,
    RibbedBracket3D,
    SlottedBracket3D,
    SpurGear3D,
    SteppedShaft3D,
)


@dataclass(frozen=True, slots=True)
class SolidMesh3D:
    """Watertight indexed triangle mesh in millimetres plus exact analytical expectations."""

    vertices: np.ndarray  # shape (V, 3), float64
    faces: np.ndarray  # shape (F, 3), int32
    expected_volume_mm3: float
    expected_bbox_mm: tuple[float, float, float, float, float, float]  # xmin, ymin, zmin, xmax, ymax, zmax

    @property
    def face_normals(self) -> np.ndarray:
        v0 = self.vertices[self.faces[:, 0]]
        v1 = self.vertices[self.faces[:, 1]]
        v2 = self.vertices[self.faces[:, 2]]
        cross = np.cross(v1 - v0, v2 - v0)
        norms = np.linalg.norm(cross, axis=1, keepdims=True)
        norms = np.where(norms > 1e-15, norms, 1.0)
        return cross / norms

    def signed_volume_mm3(self) -> float:
        v0 = self.vertices[self.faces[:, 0]]
        v1 = self.vertices[self.faces[:, 1]]
        v2 = self.vertices[self.faces[:, 2]]
        return float(np.sum(np.einsum("ij,ij->i", v0, np.cross(v1, v2))) / 6.0)

    def surface_area_mm2(self) -> float:
        v0 = self.vertices[self.faces[:, 0]]
        v1 = self.vertices[self.faces[:, 1]]
        v2 = self.vertices[self.faces[:, 2]]
        return float(0.5 * np.sum(np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1)))

    def actual_bbox_mm(self) -> tuple[float, float, float, float, float, float]:
        mins = self.vertices.min(axis=0)
        maxs = self.vertices.max(axis=0)
        return (
            float(mins[0]),
            float(mins[1]),
            float(mins[2]),
            float(maxs[0]),
            float(maxs[1]),
            float(maxs[2]),
        )


def build_solid_mesh(spec: Model3DSpec | Body3D, *, circle_segments: int = 64) -> SolidMesh3D:
    """Compile a Model3DSpec or Body3D into an exact-indexed watertight SolidMesh3D."""

    body = spec.body if isinstance(spec, Model3DSpec) else spec
    n_seg = max(24, int(circle_segments))

    if isinstance(body, Box3D):
        hw, hd = body.width / 2.0, body.depth / 2.0
        outer = [(-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)]
        holes = [_circle_loop(hx, hy, hd_i / 2.0, max(24, n_seg // 2), ccw=False) for hx, hy, hd_i in body.through_holes]
        vol = body.width * body.depth * body.height - sum(
            pi * (hd_i / 2.0) ** 2 * body.height for _, _, hd_i in body.through_holes
        )
        bbox = (-hw, -hd, 0.0, hw, hd, body.height)
        return _extrude_with_holes(outer, holes, 0.0, body.height, vol, bbox)

    if isinstance(body, Cylinder3D):
        r_out = body.outer_diameter / 2.0
        r_in = body.bore_diameter / 2.0
        if r_in == 0.0:
            meridian = [(0.0, 0.0), (r_out, 0.0), (r_out, body.height), (0.0, body.height)]
        else:
            meridian = [(r_in, 0.0), (r_out, 0.0), (r_out, body.height), (r_in, body.height)]
        vol = pi * (r_out**2 - r_in**2) * body.height
        bbox = (-r_out, -r_out, 0.0, r_out, r_out, body.height)
        return _revolve_meridian(meridian, n_seg, vol, bbox)

    if isinstance(body, ExtrudePolygon3D):
        outer = _ensure_winding(body.outer_loop, ccw=True)
        holes = [_ensure_winding(loop, ccw=False) for loop in body.inner_loops]
        area = abs(_signed_area_2d(outer)) - sum(abs(_signed_area_2d(h)) for h in holes)
        if area <= 0:
            raise ValueError("extrude_polygon net 2D area must be positive")
        vol = area * body.height
        xs = [p[0] for p in outer]
        ys = [p[1] for p in outer]
        bbox = (min(xs), min(ys), 0.0, max(xs), max(ys), body.height)
        return _extrude_with_holes(outer, holes, 0.0, body.height, vol, bbox)

    if isinstance(body, SteppedShaft3D):
        r_in = body.bore_diameter / 2.0
        meridian_pts: list[tuple[float, float]] = []
        z = 0.0
        vol = 0.0
        max_r = 0.0
        if r_in == 0.0:
            meridian_pts.append((0.0, 0.0))
        else:
            meridian_pts.append((r_in, 0.0))
        for length, dia in body.steps:
            r = dia / 2.0
            max_r = max(max_r, r)
            meridian_pts.append((r, z))
            meridian_pts.append((r, z + length))
            vol += pi * (r**2 - r_in**2) * length
            z += length
        if r_in == 0.0:
            meridian_pts.append((0.0, z))
        else:
            meridian_pts.append((r_in, z))
        # Remove consecutive duplicates (when two adjacent steps have identical diameter)
        cleaned: list[tuple[float, float]] = []
        for pt in meridian_pts:
            if not cleaned or hypot(pt[0] - cleaned[-1][0], pt[1] - cleaned[-1][1]) > 1e-9:
                cleaned.append(pt)
        bbox = (-max_r, -max_r, 0.0, max_r, max_r, z)
        return _revolve_meridian(cleaned, n_seg, vol, bbox)

    if isinstance(body, SlottedBracket3D):
        outer = _rounded_rect_loop(body.width, body.height, body.corner_radius, arc_steps=max(8, n_seg // 6))
        holes: list[list[tuple[float, float]]] = [
            _obround_slot_loop(body.slot_length, body.slot_width, arc_steps=max(12, n_seg // 3), ccw=False)
        ]
        hole_area = (body.slot_length - body.slot_width) * body.slot_width + pi * (body.slot_width / 2.0) ** 2
        if body.hole_diameter is not None:
            hr = body.hole_diameter / 2.0
            hx = body.width / 2.0 - body.hole_inset
            hy = body.height / 2.0 - body.hole_inset
            for sx in (-1.0, 1.0):
                for sy in (-1.0, 1.0):
                    holes.append(_circle_loop(sx * hx, sy * hy, hr, max(20, n_seg // 2), ccw=False))
                    hole_area += pi * hr * hr
        plate_area = (
            body.width * body.height
            - (4.0 - pi) * body.corner_radius * body.corner_radius
        )
        vol = (plate_area - hole_area) * body.thickness
        hw, hh = body.width / 2.0, body.height / 2.0
        bbox = (-hw, -hh, 0.0, hw, hh, body.thickness)
        return _extrude_with_holes(outer, holes, 0.0, body.thickness, vol, bbox)

    if isinstance(body, Flange3D):
        return _build_flange_3d_mesh(body, n_seg)

    if isinstance(body, CompoundSolid3D):
        return _build_compound_3d_mesh(body, n_seg)

    if isinstance(body, SpurGear3D):
        return _build_spur_gear_3d_mesh(body, n_seg)

    if isinstance(body, BearingCap3D):
        return _build_bearing_cap_3d_mesh(body, n_seg)

    if isinstance(body, RibbedBracket3D):
        return _build_ribbed_bracket_3d_mesh(body, n_seg)

    raise TypeError(f"unsupported 3D body kind: {type(body)!r}")


def _build_flange_3d_mesh(body: Flange3D, n_seg: int) -> SolidMesh3D:
    r_out = body.outer_diameter / 2.0
    r_bore = body.bore_diameter / 2.0
    th = body.thickness
    bolt_holes: list[list[tuple[float, float]]] = []
    bolt_area = 0.0
    if body.hole_count > 0 and body.bolt_circle_diameter and body.hole_diameter:
        r_bcd = body.bolt_circle_diameter / 2.0
        r_h = body.hole_diameter / 2.0
        h_steps = max(20, n_seg // 2)
        for k in range(body.hole_count):
            ang = 2.0 * pi * k / body.hole_count
            bolt_holes.append(_circle_loop(r_bcd * cos(ang), r_bcd * sin(ang), r_h, h_steps, ccw=False))
            bolt_area += pi * r_h * r_h

    # Simple flat flange (no raised hub)
    if body.hub_height <= 0.0 or body.hub_diameter is None:
        outer = _circle_loop(0.0, 0.0, r_out, n_seg, ccw=True)
        holes = list(bolt_holes)
        if r_bore > 0:
            holes.append(_circle_loop(0.0, 0.0, r_bore, n_seg, ccw=False))
        vol = (pi * (r_out**2 - r_bore**2) - bolt_area) * th
        bbox = (-r_out, -r_out, 0.0, r_out, r_out, th)
        return _extrude_with_holes(outer, holes, 0.0, th, vol, bbox)

    # Raised-hub bolted flange: single watertight manifold shell
    r_hub = body.hub_diameter / 2.0
    z_top = th + body.hub_height
    outer_pts = _circle_loop(0.0, 0.0, r_out, n_seg, ccw=True)
    hub_cw = _circle_loop(0.0, 0.0, r_hub, n_seg, ccw=False)
    bore_cw = _circle_loop(0.0, 0.0, r_bore, n_seg, ccw=False) if r_bore > 0 else []

    verts_3d: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []

    def add_ring_3d(pts_2d: Sequence[tuple[float, float]], z_val: float) -> list[int]:
        base = len(verts_3d)
        for x, y in pts_2d:
            verts_3d.append((x, y, z_val))
        return list(range(base, base + len(pts_2d)))

    # 1. Bottom cap at z=0: outer_pts (CCW) minus [bolt_holes + optional bore_cw]
    bot_holes = list(bolt_holes) + ([bore_cw] if r_bore > 0 else [])
    bot_outer_idx = add_ring_3d(outer_pts, 0.0)
    bot_hole_indices = [add_ring_3d(h, 0.0) for h in bot_holes]
    bot_2d = outer_pts + [pt for h in bot_holes for pt in h]
    bot_idx_map = bot_outer_idx + [idx for ring in bot_hole_indices for idx in ring]
    for i0, i1, i2 in _triangulate_polygon_with_holes(outer_pts, bot_holes):
        # Bottom normal is -Z -> reverse triangle winding
        faces.append((bot_idx_map[i0], bot_idx_map[i2], bot_idx_map[i1]))
    del bot_2d

    # 2. Flange step cap at z=th: outer_pts (CCW) minus [bolt_holes + hub_cw]
    mid_outer_idx = add_ring_3d(outer_pts, th)
    mid_bolt_indices = [add_ring_3d(h, th) for h in bolt_holes]
    mid_hub_idx = add_ring_3d(hub_cw, th)
    mid_holes = list(bolt_holes) + [hub_cw]
    mid_idx_map = mid_outer_idx + [idx for ring in mid_bolt_indices for idx in ring] + mid_hub_idx
    for i0, i1, i2 in _triangulate_polygon_with_holes(outer_pts, mid_holes):
        faces.append((mid_idx_map[i0], mid_idx_map[i1], mid_idx_map[i2]))

    # 3. Outer rim vertical wall (z=0 -> z=th) & bolt hole vertical walls (z=0 -> z=th)
    _stitch_rings(faces, bot_outer_idx, mid_outer_idx)
    for b_ring, m_ring in zip(bot_hole_indices[: len(bolt_holes)], mid_bolt_indices):
        _stitch_rings(faces, b_ring, m_ring)

    # 4. Hub outer cylindrical wall: mid_hub_idx is CW, so reverse it to CCW for outward normal from z=th -> z=z_top!
    hub_ccw = list(reversed(hub_cw))
    mid_hub_ccw_idx = list(reversed(mid_hub_idx))
    top_hub_ccw_idx = add_ring_3d(hub_ccw, z_top)
    _stitch_rings(faces, mid_hub_ccw_idx, top_hub_ccw_idx)

    # 5. Top of hub at z=z_top (either solid disk or annulus down to bore)
    if r_bore > 0:
        top_bore_cw_idx = add_ring_3d(bore_cw, z_top)
        top_idx_map = top_hub_ccw_idx + top_bore_cw_idx
        for i0, i1, i2 in _triangulate_polygon_with_holes(hub_ccw, [bore_cw]):
            faces.append((top_idx_map[i0], top_idx_map[i1], top_idx_map[i2]))
        # Bore wall from z=0 (bot_hole_indices[-1], CW) up to z=z_top (top_bore_cw_idx, CW)
        _stitch_rings(faces, bot_hole_indices[-1], top_bore_cw_idx)
    else:
        pole_idx = len(verts_3d)
        verts_3d.append((0.0, 0.0, z_top))
        m_len = len(top_hub_ccw_idx)
        for k in range(m_len):
            faces.append((pole_idx, top_hub_ccw_idx[k], top_hub_ccw_idx[(k + 1) % m_len]))

    vol = (
        (pi * (r_out**2 - r_bore**2) - bolt_area) * th
        + pi * (r_hub**2 - r_bore**2) * body.hub_height
    )
    bbox = (-r_out, -r_out, 0.0, r_out, r_out, z_top)
    return SolidMesh3D(
        vertices=np.asarray(verts_3d, dtype=np.float64),
        faces=np.asarray(faces, dtype=np.int32),
        expected_volume_mm3=vol,
        expected_bbox_mm=bbox,
    )


def _build_compound_3d_mesh(body: CompoundSolid3D, n_seg: int) -> SolidMesh3D:
    """Stitch base + arbitrary top bosses + through-holes + counterbores into one watertight manifold shell."""

    h_base = body.base_height
    c_steps = max(20, n_seg // 2)

    if body.base_shape == "box":
        hw, hd = body.width / 2.0, body.depth / 2.0
        if body.corner_radius > 0:
            base_outer_ccw = _rounded_rect_loop(body.width, body.depth, body.corner_radius, arc_steps=max(8, n_seg // 6))
            base_area = body.width * body.depth - (4.0 - pi) * body.corner_radius**2
        else:
            base_outer_ccw = [(-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)]
            base_area = body.width * body.depth
        min_x, min_y, max_x, max_y = -hw, -hd, hw, hd
    else:
        r_base = body.outer_diameter / 2.0
        base_outer_ccw = _circle_loop(0.0, 0.0, r_base, n_seg, ccw=True)
        base_area = pi * r_base * r_base
        min_x, min_y, max_x, max_y = -r_base, -r_base, r_base, r_base

    verts_3d: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []

    def add_ring(pts_2d: Sequence[tuple[float, float]], z_val: float) -> list[int]:
        start = len(verts_3d)
        for x, y in pts_2d:
            verts_3d.append((x, y, z_val))
        return list(range(start, start + len(pts_2d)))

    bot_outer_idx = add_ring(base_outer_ccw, 0.0)
    top_outer_idx = add_ring(base_outer_ccw, h_base)
    _stitch_rings(faces, bot_outer_idx, top_outer_idx)

    bot_holes_pts: list[list[tuple[float, float]]] = []
    bot_holes_idx: list[list[int]] = []
    top_holes_pts: list[list[tuple[float, float]]] = []
    top_holes_idx: list[list[int]] = []

    net_vol = base_area * h_base
    max_z = h_base

    # 1. Through-holes (z=0 -> z=h_base)
    for hx, hy, hd_i in body.through_holes:
        rh = hd_i / 2.0
        h_cw = _circle_loop(hx, hy, rh, c_steps, ccw=False)
        b_idx = add_ring(h_cw, 0.0)
        t_idx = add_ring(h_cw, h_base)
        _stitch_rings(faces, b_idx, t_idx)
        bot_holes_pts.append(h_cw)
        bot_holes_idx.append(b_idx)
        top_holes_pts.append(h_cw)
        top_holes_idx.append(t_idx)
        net_vol -= pi * rh * rh * h_base

    # 2. Counterbore holes (through shaft z=0..z_step, shoulder at z_step, cbore z_step..h_base)
    for cb in body.counterbores:
        assert isinstance(cb, CounterboreHole3D)
        r_th = cb.through_diameter / 2.0
        r_cb = cb.cbore_diameter / 2.0
        z_step = h_base - cb.cbore_depth

        th_cw = _circle_loop(cb.x, cb.y, r_th, c_steps, ccw=False)
        cb_cw = _circle_loop(cb.x, cb.y, r_cb, c_steps, ccw=False)
        cb_ccw = list(reversed(cb_cw))

        b_th_idx = add_ring(th_cw, 0.0)
        m_th_idx = add_ring(th_cw, z_step)
        m_cb_ccw_idx = add_ring(cb_ccw, z_step)
        m_cb_cw_idx = list(reversed(m_cb_ccw_idx))
        t_cb_cw_idx = add_ring(cb_cw, h_base)

        _stitch_rings(faces, b_th_idx, m_th_idx)
        shoulder_map = m_cb_ccw_idx + m_th_idx
        for i0, i1, i2 in _triangulate_polygon_with_holes(cb_ccw, [th_cw]):
            faces.append((shoulder_map[i0], shoulder_map[i1], shoulder_map[i2]))
        _stitch_rings(faces, m_cb_cw_idx, t_cb_cw_idx)

        bot_holes_pts.append(th_cw)
        bot_holes_idx.append(b_th_idx)
        top_holes_pts.append(cb_cw)
        top_holes_idx.append(t_cb_cw_idx)
        net_vol -= pi * r_th * r_th * z_step + pi * r_cb * r_cb * cb.cbore_depth

    # 3. Additive top bosses (z=h_base -> z=h_base + boss.height)
    for boss in body.bosses:
        z_boss_top = h_base + boss.height
        max_z = max(max_z, z_boss_top)
        if isinstance(boss, CylinderBoss3D):
            rb = boss.diameter / 2.0
            boss_cw = _circle_loop(boss.x, boss.y, rb, c_steps, ccw=False)
            boss_ccw = list(reversed(boss_cw))
            base_boss_cw_idx = add_ring(boss_cw, h_base)
            base_boss_ccw_idx = list(reversed(base_boss_cw_idx))
            top_boss_ccw_idx = add_ring(boss_ccw, z_boss_top)

            _stitch_rings(faces, base_boss_ccw_idx, top_boss_ccw_idx)
            top_holes_pts.append(boss_cw)
            top_holes_idx.append(base_boss_cw_idx)

            if boss.bore_diameter > 0:
                r_bore = boss.bore_diameter / 2.0
                bore_cw = _circle_loop(boss.x, boss.y, r_bore, c_steps, ccw=False)
                bot_bore_cw_idx = add_ring(bore_cw, 0.0)
                top_bore_cw_idx = add_ring(bore_cw, z_boss_top)
                _stitch_rings(faces, bot_bore_cw_idx, top_bore_cw_idx)

                boss_cap_map = top_boss_ccw_idx + top_bore_cw_idx
                for i0, i1, i2 in _triangulate_polygon_with_holes(boss_ccw, [bore_cw]):
                    faces.append((boss_cap_map[i0], boss_cap_map[i1], boss_cap_map[i2]))

                bot_holes_pts.append(bore_cw)
                bot_holes_idx.append(bot_bore_cw_idx)
                net_vol += pi * rb * rb * boss.height - pi * r_bore * r_bore * z_boss_top
            else:
                for i0, i1, i2 in _triangulate_polygon_with_holes(boss_ccw, []):
                    faces.append((top_boss_ccw_idx[i0], top_boss_ccw_idx[i1], top_boss_ccw_idx[i2]))
                net_vol += pi * rb * rb * boss.height
        else:
            assert isinstance(boss, BoxBoss3D)
            bhw, bhd = boss.width / 2.0, boss.depth / 2.0
            boss_ccw = [
                (boss.x - bhw, boss.y - bhd),
                (boss.x + bhw, boss.y - bhd),
                (boss.x + bhw, boss.y + bhd),
                (boss.x - bhw, boss.y + bhd),
            ]
            boss_cw = list(reversed(boss_ccw))
            base_boss_cw_idx = add_ring(boss_cw, h_base)
            base_boss_ccw_idx = list(reversed(base_boss_cw_idx))
            top_boss_ccw_idx = add_ring(boss_ccw, z_boss_top)

            _stitch_rings(faces, base_boss_ccw_idx, top_boss_ccw_idx)
            for i0, i1, i2 in _triangulate_polygon_with_holes(boss_ccw, []):
                faces.append((top_boss_ccw_idx[i0], top_boss_ccw_idx[i1], top_boss_ccw_idx[i2]))

            top_holes_pts.append(boss_cw)
            top_holes_idx.append(base_boss_cw_idx)
            net_vol += boss.width * boss.depth * boss.height

    # 4. Cap bottom face (z=0, -Z normal) and base top face (z=h_base, +Z normal)
    bot_map = bot_outer_idx + [idx for ring in bot_holes_idx for idx in ring]
    for i0, i1, i2 in _triangulate_polygon_with_holes(base_outer_ccw, bot_holes_pts):
        faces.append((bot_map[i0], bot_map[i2], bot_map[i1]))

    top_map = top_outer_idx + [idx for ring in top_holes_idx for idx in ring]
    for i0, i1, i2 in _triangulate_polygon_with_holes(base_outer_ccw, top_holes_pts):
        faces.append((top_map[i0], top_map[i1], top_map[i2]))

    bbox = (min_x, min_y, 0.0, max_x, max_y, max_z)
    return SolidMesh3D(
        vertices=np.asarray(verts_3d, dtype=np.float64),
        faces=np.asarray(faces, dtype=np.int32),
        expected_volume_mm3=net_vol,
        expected_bbox_mm=bbox,
    )


def _revolve_meridian(
    meridian: Sequence[tuple[float, float]],
    n_seg: int,
    expected_volume: float,
    expected_bbox: tuple[float, float, float, float, float, float],
) -> SolidMesh3D:
    # Ensure meridian encloses positive area in (r, z) so outward normals point outside
    if _signed_area_2d(meridian) < 0:
        meridian = list(reversed(meridian))

    verts: list[tuple[float, float, float]] = []
    rings: list[list[int]] = []
    angles = [2.0 * pi * k / n_seg for k in range(n_seg)]

    for r, z in meridian:
        if r <= 1e-11:
            idx = len(verts)
            verts.append((0.0, 0.0, z))
            rings.append([idx])
        else:
            base = len(verts)
            for ang in angles:
                verts.append((r * cos(ang), r * sin(ang), z))
            rings.append(list(range(base, base + n_seg)))

    faces: list[tuple[int, int, int]] = []
    m = len(rings)
    for i in range(m):
        r_curr = rings[i]
        r_next = rings[(i + 1) % m]
        if len(r_curr) == 1 and len(r_next) == 1:
            continue
        if len(r_curr) == 1:
            pole = r_curr[0]
            for k in range(n_seg):
                faces.append((pole, r_next[(k + 1) % n_seg], r_next[k]))
        elif len(r_next) == 1:
            pole = r_next[0]
            for k in range(n_seg):
                faces.append((r_curr[k], r_curr[(k + 1) % n_seg], pole))
        else:
            for k in range(n_seg):
                k2 = (k + 1) % n_seg
                a0, a1 = r_curr[k], r_curr[k2]
                b0, b1 = r_next[k], r_next[k2]
                faces.append((a0, a1, b1))
                faces.append((a0, b1, b0))

    mesh = SolidMesh3D(
        vertices=np.asarray(verts, dtype=np.float64),
        faces=np.asarray(faces, dtype=np.int32),
        expected_volume_mm3=expected_volume,
        expected_bbox_mm=expected_bbox,
    )
    if mesh.signed_volume_mm3() < 0:
        mesh = SolidMesh3D(
            vertices=mesh.vertices,
            faces=mesh.faces[:, [0, 2, 1]].copy(),
            expected_volume_mm3=expected_volume,
            expected_bbox_mm=expected_bbox,
        )
    return mesh


def _extrude_with_holes(
    outer_ccw: Sequence[tuple[float, float]],
    holes_cw: Sequence[Sequence[tuple[float, float]]],
    z0: float,
    z1: float,
    expected_volume: float,
    expected_bbox: tuple[float, float, float, float, float, float],
) -> SolidMesh3D:
    outer = _ensure_winding(outer_ccw, ccw=True)
    holes = [_ensure_winding(h, ccw=False) for h in holes_cw]
    cap_tris = _triangulate_polygon_with_holes(outer, holes)

    pts_2d: list[tuple[float, float]] = list(outer)
    loop_Menu: list[tuple[int, int]] = [(0, len(outer))]
    for h in holes:
        start = len(pts_2d)
        pts_2d.extend(h)
        loop_Menu.append((start, len(h)))

    n2d = len(pts_2d)
    verts_3d = [(x, y, z0) for x, y in pts_2d] + [(x, y, z1) for x, y in pts_2d]
    faces: list[tuple[int, int, int]] = []

    # Bottom cap (-Z normal -> reverse) and Top cap (+Z normal)
    for i0, i1, i2 in cap_tris:
        faces.append((i0, i2, i1))
        faces.append((n2d + i0, n2d + i1, n2d + i2))

    # Side walls for outer loop (CCW) and every inner hole loop (CW)
    for start, length in loop_Menu:
        bot_ring = [start + k for k in range(length)]
        top_ring = [n2d + start + k for k in range(length)]
        _stitch_rings(faces, bot_ring, top_ring)

    return SolidMesh3D(
        vertices=np.asarray(verts_3d, dtype=np.float64),
        faces=np.asarray(faces, dtype=np.int32),
        expected_volume_mm3=expected_volume,
        expected_bbox_mm=expected_bbox,
    )


def _stitch_rings(
    faces: list[tuple[int, int, int]],
    bot_ring: Sequence[int],
    top_ring: Sequence[int],
) -> None:
    m = len(bot_ring)
    for k in range(m):
        k2 = (k + 1) % m
        b0, b1 = bot_ring[k], bot_ring[k2]
        t0, t1 = top_ring[k], top_ring[k2]
        faces.append((b0, b1, t1))
        faces.append((b0, t1, t0))


def _circle_loop(cx: float, cy: float, radius: float, segments: int, *, ccw: bool) -> list[tuple[float, float]]:
    pts = [
        (cx + radius * cos(2.0 * pi * k / segments), cy + radius * sin(2.0 * pi * k / segments))
        for k in range(segments)
    ]
    return pts if ccw else list(reversed(pts))


def _rounded_rect_loop(width: float, height: float, radius: float, *, arc_steps: int = 10) -> list[tuple[float, float]]:
    hw, hh = width / 2.0, height / 2.0
    corners = [
        (hw - radius, -hh + radius, -pi / 2.0),
        (hw - radius, hh - radius, 0.0),
        (-hw + radius, hh - radius, pi / 2.0),
        (-hw + radius, -hh + radius, pi),
    ]
    pts: list[tuple[float, float]] = []
    for cx, cy, start_ang in corners:
        for k in range(arc_steps + 1):
            ang = start_ang + (pi / 2.0) * k / arc_steps
            pts.append((cx + radius * cos(ang), cy + radius * sin(ang)))
    return pts


def _obround_slot_loop(length: float, width: float, *, arc_steps: int = 16, ccw: bool = False) -> list[tuple[float, float]]:
    r = width / 2.0
    dx = (length - width) / 2.0
    pts: list[tuple[float, float]] = []
    # Right semicircle (-pi/2 -> +pi/2)
    for k in range(arc_steps + 1):
        ang = -pi / 2.0 + pi * k / arc_steps
        pts.append((dx + r * cos(ang), r * sin(ang)))
    # Left semicircle (+pi/2 -> +3pi/2)
    for k in range(arc_steps + 1):
        ang = pi / 2.0 + pi * k / arc_steps
        pts.append((-dx + r * cos(ang), r * sin(ang)))
    return pts if ccw else list(reversed(pts))


def _signed_area_2d(pts: Sequence[tuple[float, float]]) -> float:
    area = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return 0.5 * area


def _ensure_winding(pts: Sequence[tuple[float, float]], *, ccw: bool) -> list[tuple[float, float]]:
    area = _signed_area_2d(pts)
    if (area > 0) == ccw:
        return list(pts)
    return list(reversed(pts))


def _triangulate_polygon_with_holes(
    outer_ccw: Sequence[tuple[float, float]],
    holes_cw: Sequence[Sequence[tuple[float, float]]],
) -> list[tuple[int, int, int]]:
    """Bridge disjoint CW holes into CCW outer loop (Eberly rightmost visibility) and ear-clip."""

    all_pts: list[tuple[float, float]] = list(outer_ccw)
    ring: list[int] = list(range(len(outer_ccw)))

    indexed_holes: list[tuple[float, list[int]]] = []
    for h in holes_cw:
        base = len(all_pts)
        all_pts.extend(h)
        h_indices = list(range(base, base + len(h)))
        max_x = max(all_pts[idx][0] for idx in h_indices)
        indexed_holes.append((max_x, h_indices))

    # Process holes from rightmost max-X descending
    indexed_holes.sort(key=lambda item: item[0], reverse=True)
    for _, h_ring in indexed_holes:
        ring = _bridge_hole_into_ring(ring, h_ring, all_pts)

    return _ear_clip_ring(ring, all_pts)


def _bridge_hole_into_ring(
    outer_ring: list[int],
    hole_ring: list[int],
    pts: Sequence[tuple[float, float]],
) -> list[int]:
    # Find rightmost vertex M on hole_ring
    m_pos = max(range(len(hole_ring)), key=lambda i: (pts[hole_ring[i]][0], pts[hole_ring[i]][1]))
    m_idx = hole_ring[m_pos]
    mx, my = pts[m_idx]

    # Cast horizontal ray (mx, my) -> (+inf, my) against outer_ring segments
    best_x = float("inf")
    best_seg_pos = -1
    n = len(outer_ring)
    for i in range(n):
        u_idx = outer_ring[i]
        v_idx = outer_ring[(i + 1) % n]
        ux, uy = pts[u_idx]
        vx, vy = pts[v_idx]
        # Skip horizontal segments that don't bracket my
        if (uy > my) == (vy > my) and uy != my and vy != my:
            continue
        if abs(uy - vy) <= 1e-12:
            if abs(uy - my) <= 1e-12:
                ix = max(min(ux, vx), mx)
                if ix >= mx - 1e-12 and ix < best_x:
                    best_x = ix
                    best_seg_pos = i
            continue
        # Require strictly downward-crossing or upward-crossing at my (standard half-open rule)
        if not (min(uy, vy) - 1e-12 <= my <= max(uy, vy) + 1e-12):
            continue
        t = (my - uy) / (vy - uy)
        if not (-1e-12 <= t <= 1.0 + 1e-12):
            continue
        ix = ux + t * (vx - ux)
        if ix >= mx - 1e-9 and ix < best_x:
            best_x = ix
            best_seg_pos = i

    if best_seg_pos < 0:
        raise ValueError("hole is not enclosed by outer polygon boundary")

    u_pos = best_seg_pos
    v_pos = (best_seg_pos + 1) % n
    p_pos = u_pos if pts[outer_ring[u_pos]][0] >= pts[outer_ring[v_pos]][0] else v_pos
    px, py = pts[outer_ring[p_pos]]

    if abs(px - best_x) <= 1e-9 and abs(py - my) <= 1e-9:
        chosen_outer_pos = p_pos
    else:
        # Check reflex vertices inside triangle (M, I, P) to ensure unobstructed visibility
        tri_i = (best_x, my)
        tri_p = (px, py)
        best_tan = float("inf")
        chosen_outer_pos = p_pos
        for cand_pos in range(n):
            cx, cy = pts[outer_ring[cand_pos]]
            prev_pt = pts[outer_ring[(cand_pos - 1) % n]]
            next_pt = pts[outer_ring[(cand_pos + 1) % n]]
            # Only reflex vertices can block visibility
            if _cross_2d(prev_pt, (cx, cy), next_pt) >= -1e-12:
                continue
            if _point_in_triangle_inclusive((cx, cy), (mx, my), tri_i, tri_p):
                dist = hypot(cx - mx, cy - my)
                if dist <= 1e-12:
                    continue
                tan_angle = abs(cy - my) / max(1e-12, cx - mx)
                if tan_angle < best_tan or (abs(tan_angle - best_tan) <= 1e-9 and dist < hypot(pts[outer_ring[chosen_outer_pos]][0] - mx, pts[outer_ring[chosen_outer_pos]][1] - my)):
                    best_tan = tan_angle
                    chosen_outer_pos = cand_pos

    # If multiple occurrences of outer_ring[chosen_outer_pos] exist due to earlier bridges,
    # pick the occurrence whose angular sector contains M!
    target_v = outer_ring[chosen_outer_pos]
    candidate_positions = [pos for pos, vid in enumerate(outer_ring) if vid == target_v]
    if len(candidate_positions) > 1:
        for pos in candidate_positions:
            prev_pt = pts[outer_ring[(pos - 1) % n]]
            curr_pt = pts[outer_ring[pos]]
            next_pt = pts[outer_ring[(pos + 1) % n]]
            if _in_ccw_cone(prev_pt, curr_pt, next_pt, (mx, my)):
                chosen_outer_pos = pos
                break

    ordered_hole = hole_ring[m_pos:] + hole_ring[:m_pos]
    return (
        outer_ring[: chosen_outer_pos + 1]
        + ordered_hole
        + [m_idx, outer_ring[chosen_outer_pos]]
        + outer_ring[chosen_outer_pos + 1 :]
    )


def _in_ccw_cone(
    prev_pt: tuple[float, float],
    curr_pt: tuple[float, float],
    next_pt: tuple[float, float],
    query_pt: tuple[float, float],
) -> bool:
    c_turn = _cross_2d(prev_pt, curr_pt, next_pt)
    q_from_prev = _cross_2d(prev_pt, curr_pt, query_pt)
    q_to_next = _cross_2d(curr_pt, next_pt, query_pt)
    if c_turn >= 0:  # convex
        return q_from_prev >= -1e-11 and q_to_next >= -1e-11
    # reflex
    return not (q_from_prev < -1e-11 and q_to_next < -1e-11)


def _ear_clip_ring(ring: list[int], pts: Sequence[tuple[float, float]]) -> list[tuple[int, int, int]]:
    work = list(ring)
    tris: list[tuple[int, int, int]] = []
    guard = 0
    max_iters = len(work) * len(work) + 100

    while len(work) > 3 and guard < max_iters:
        guard += 1
        clipped = False
        m = len(work)
        # Remove adjacent duplicate vertex indices if created by degenerate step
        deduped = [work[i] for i in range(m) if work[i] != work[(i + 1) % m]]
        if len(deduped) != m:
            work = deduped
            continue

        for eps in (1e-11, -1e-9):
            for i in range(m):
                i_prev = work[(i - 1) % m]
                i_curr = work[i]
                i_next = work[(i + 1) % m]
                if i_prev == i_curr or i_curr == i_next or i_prev == i_next:
                    continue
                a, b, c = pts[i_prev], pts[i_curr], pts[i_next]
                area2 = _cross_2d(a, b, c)
                if area2 <= eps:
                    continue
                if area2 <= 1e-13:
                    # Collinear vertex: drop without emitting zero-area triangle
                    del work[i]
                    clipped = True
                    break
                blocked = False
                for other_idx in work:
                    if other_idx in (i_prev, i_curr, i_next):
                        continue
                    p = pts[other_idx]
                    if p in (a, b, c):
                        continue
                    if _point_in_triangle_inclusive(p, a, b, c):
                        blocked = True
                        break
                if not blocked:
                    tris.append((i_prev, i_curr, i_next))
                    del work[i]
                    clipped = True
                    break
            if clipped:
                break
        if not clipped:
            raise ValueError("polygon triangulation could not resolve non-simple or intersecting loop")

    if len(work) == 3:
        a, b, c = pts[work[0]], pts[work[1]], pts[work[2]]
        if abs(_cross_2d(a, b, c)) > 1e-13:
            tris.append((work[0], work[1], work[2]))
    return tris


def _cross_2d(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _point_in_triangle_inclusive(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> bool:
    # Works for either CCW or CW triangle (a, b, c)
    c1 = _cross_2d(a, b, p)
    c2 = _cross_2d(b, c, p)
    c3 = _cross_2d(c, a, p)
    has_neg = (c1 < -1e-11) or (c2 < -1e-11) or (c3 < -1e-11)
    has_pos = (c1 > 1e-11) or (c2 > 1e-11) or (c3 > 1e-11)
    return not (has_neg and has_pos)


def _build_spur_gear_3d_mesh(body: SpurGear3D, n_seg: int) -> SolidMesh3D:
    from math import asin

    m = body.module_mm
    z = body.teeth
    b = body.face_width
    r_p = m * z / 2.0
    r_a = m * (z + 2.0) / 2.0
    r_f = m * (z - 2.5) / 2.0
    r_bore = body.bore_diameter / 2.0
    dtheta = 2.0 * pi / z

    outer_ccw: list[tuple[float, float]] = []
    for k in range(z):
        tc = k * dtheta
        profile_steps = (
            (tc - 0.36 * dtheta, r_f),
            (tc - 0.25 * dtheta, r_p),
            (tc - 0.13 * dtheta, r_a),
            (tc, r_a),
            (tc + 0.13 * dtheta, r_a),
            (tc + 0.25 * dtheta, r_p),
            (tc + 0.36 * dtheta, r_f),
            (tc + 0.50 * dtheta, r_f),
        )
        for ang, rad in profile_steps:
            outer_ccw.append((rad * cos(ang), rad * sin(ang)))

    holes_cw: list[list[tuple[float, float]]] = []
    if r_bore > 0.0:
        kw = body.keyway_width if body.keyway_width is not None else round(max(4.0, body.bore_diameter * 0.28), 1)
        kd = body.keyway_depth if body.keyway_depth is not None else round(max(1.8, kw * 0.42), 1)
        half_ratio = min(0.82, (kw / 2.0) / r_bore)
        alpha = asin(half_ratio)
        x_int = r_bore * cos(alpha)
        y_kw = r_bore * sin(alpha)
        x_back = min(r_f - 1.5, r_bore + kd)
        bore_loop_cw: list[tuple[float, float]] = [
            (x_int, y_kw),
            (x_back, y_kw),
            (x_back, -y_kw),
            (x_int, -y_kw),
        ]
        arc_n = max(24, n_seg)
        span = 2.0 * pi - 2.0 * alpha
        for j in range(1, arc_n):
            ang = -alpha - span * (j / arc_n)
            bore_loop_cw.append((r_bore * cos(ang), r_bore * sin(ang)))
        holes_cw.append(bore_loop_cw)

    net_area = abs(_signed_area_2d(outer_ccw)) - sum(abs(_signed_area_2d(h)) for h in holes_cw)
    vol = net_area * b
    xs = [p[0] for p in outer_ccw]
    ys = [p[1] for p in outer_ccw]
    bbox = (min(xs), min(ys), 0.0, max(xs), max(ys), b)
    return _extrude_with_holes(outer_ccw, holes_cw, 0.0, b, vol, bbox)


def _build_bearing_cap_3d_mesh(body: BearingCap3D, n_seg: int) -> SolidMesh3D:
    r_flg = body.flange_diameter / 2.0
    r_sp = body.spigot_diameter / 2.0
    r_bore = body.bore_diameter / 2.0
    h_tot = body.total_height
    t_flg = body.flange_thickness
    r_cham = round(min(r_sp - 5.0, (r_sp + r_bore) / 2.0), 2)
    z_floor = round(max(4.0, t_flg * 0.65), 2)

    bolt_holes_cw: list[list[tuple[float, float]]] = []
    if body.hole_count > 0:
        r_bcd = body.bolt_circle_diameter / 2.0
        r_h = body.hole_diameter / 2.0
        h_steps = max(20, n_seg // 2)
        for k in range(body.hole_count):
            ang = 2.0 * pi * k / body.hole_count
            bolt_holes_cw.append(_circle_loop(r_bcd * cos(ang), r_bcd * sin(ang), r_h, h_steps, ccw=False))

    flg_ccw = _circle_loop(0.0, 0.0, r_flg, n_seg, ccw=True)
    sp_cw = _circle_loop(0.0, 0.0, r_sp, n_seg, ccw=False)
    sp_ccw = list(reversed(sp_cw))
    bore_cw = _circle_loop(0.0, 0.0, r_bore, n_seg, ccw=False)
    cham_cw = _circle_loop(0.0, 0.0, r_cham, n_seg, ccw=False)
    cham_ccw = list(reversed(cham_cw))

    verts_3d: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []

    def add_ring(pts_2d: Sequence[tuple[float, float]], z_val: float) -> list[int]:
        base = len(verts_3d)
        for x, y in pts_2d:
            verts_3d.append((x, y, z_val))
        return list(range(base, base + len(pts_2d)))

    # 1. Bottom cap at z=0: flg_ccw minus [bolt_holes_cw + bore_cw]
    bot_outer_idx = add_ring(flg_ccw, 0.0)
    bot_bolts_idx = [add_ring(h, 0.0) for h in bolt_holes_cw]
    bot_bore_idx = add_ring(bore_cw, 0.0)
    bot_holes = [*bolt_holes_cw, bore_cw]
    bot_map = bot_outer_idx + [i for r in bot_bolts_idx for i in r] + bot_bore_idx
    for i0, i1, i2 in _triangulate_polygon_with_holes(flg_ccw, bot_holes):
        faces.append((bot_map[i0], bot_map[i2], bot_map[i1]))

    # 2. Flange step shoulder at z=t_flg: flg_ccw minus [bolt_holes_cw + sp_cw]
    mid_outer_idx = add_ring(flg_ccw, t_flg)
    mid_bolts_idx = [add_ring(h, t_flg) for h in bolt_holes_cw]
    mid_sp_cw_idx = add_ring(sp_cw, t_flg)
    mid_holes = [*bolt_holes_cw, sp_cw]
    mid_map = mid_outer_idx + [i for r in mid_bolts_idx for i in r] + mid_sp_cw_idx
    for i0, i1, i2 in _triangulate_polygon_with_holes(flg_ccw, mid_holes):
        faces.append((mid_map[i0], mid_map[i1], mid_map[i2]))

    _stitch_rings(faces, bot_outer_idx, mid_outer_idx)
    for b_r, m_r in zip(bot_bolts_idx, mid_bolts_idx):
        _stitch_rings(faces, b_r, m_r)

    # 3. Spigot outer wall (z=t_flg -> z=h_tot)
    mid_sp_ccw_idx = list(reversed(mid_sp_cw_idx))
    top_sp_ccw_idx = add_ring(sp_ccw, h_tot)
    _stitch_rings(faces, mid_sp_ccw_idx, top_sp_ccw_idx)

    # 4. Internal stepped bearing chamber: bore (z=0..z_floor) -> +Z step annulus -> chamber (z=z_floor..h_tot)
    step_bore_cw_idx = add_ring(bore_cw, z_floor)
    _stitch_rings(faces, bot_bore_idx, step_bore_cw_idx)

    step_cham_ccw_idx = add_ring(cham_ccw, z_floor)
    step_cham_cw_idx = list(reversed(step_cham_ccw_idx))
    step_map = step_cham_ccw_idx + step_bore_cw_idx
    for i0, i1, i2 in _triangulate_polygon_with_holes(cham_ccw, [bore_cw]):
        faces.append((step_map[i0], step_map[i1], step_map[i2]))

    top_cham_cw_idx = add_ring(cham_cw, h_tot)
    _stitch_rings(faces, step_cham_cw_idx, top_cham_cw_idx)

    # 5. Top spigot rim annulus at z=h_tot: sp_ccw minus [cham_cw]
    top_map = top_sp_ccw_idx + top_cham_cw_idx
    for i0, i1, i2 in _triangulate_polygon_with_holes(sp_ccw, [cham_cw]):
        faces.append((top_map[i0], top_map[i1], top_map[i2]))

    # Exact polygon area volume matching discretized circles
    flg_area = abs(_signed_area_2d(flg_ccw))
    sp_area = abs(_signed_area_2d(sp_ccw))
    bore_area = abs(_signed_area_2d(bore_cw))
    cham_area = abs(_signed_area_2d(cham_ccw))
    bolts_area = sum(abs(_signed_area_2d(h)) for h in bolt_holes_cw)
    vol = (
        (flg_area - bolts_area) * t_flg
        + sp_area * (h_tot - t_flg)
        - bore_area * z_floor
        - cham_area * (h_tot - z_floor)
    )
    bbox = (-r_flg, -r_flg, 0.0, r_flg, r_flg, h_tot)
    return SolidMesh3D(
        vertices=np.asarray(verts_3d, dtype=np.float64),
        faces=np.asarray(faces, dtype=np.int32),
        expected_volume_mm3=vol,
        expected_bbox_mm=bbox,
    )


def _build_ribbed_bracket_3d_mesh(body: RibbedBracket3D, n_seg: int) -> SolidMesh3D:
    L = body.base_length
    W = body.base_width
    H = body.total_height
    t1 = body.base_thickness
    t2 = body.wall_thickness
    tr = body.rib_thickness
    r_boss = body.boss_hole_diameter / 2.0
    r_mnt = body.mount_hole_diameter / 2.0

    z_hole = round(H - max(r_boss + 12.0, H * 0.30), 2)
    x_rib_end = round(L - max(22.0, r_mnt * 3.0), 2)
    z_rib_top = round(min(H - 8.0, z_hole - r_boss - 2.5) if z_hole - r_boss - 2.5 > t1 + 10.0 else t1 + (H - t1) * 0.45, 2)
    x_mnt = round(L - max(18.0, r_mnt * 2.2), 2)
    y_mnt = round(W / 2.0 - max(15.0, r_mnt * 1.8), 2)

    c_steps = max(24, n_seg // 2)
    vert_map: dict[tuple[int, int, int], int] = {}
    verts_3d: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []

    def vid(x: float, y: float, z: float) -> int:
        key = (round(x * 1e6), round(y * 1e6), round(z * 1e6))
        if key not in vert_map:
            vert_map[key] = len(verts_3d)
            verts_3d.append((float(x), float(y), float(z)))
        return vert_map[key]

    def emit_planar(
        outer_3d: Sequence[tuple[float, float, float]],
        holes_3d: Sequence[Sequence[tuple[float, float, float]]],
        u_axis: tuple[float, float, float],
        v_axis: tuple[float, float, float],
    ) -> None:
        # (u_axis x v_axis) MUST point in desired outward normal direction!
        def proj(pt: tuple[float, float, float]) -> tuple[float, float]:
            return (
                pt[0] * u_axis[0] + pt[1] * u_axis[1] + pt[2] * u_axis[2],
                pt[0] * v_axis[0] + pt[1] * v_axis[1] + pt[2] * v_axis[2],
            )

        out_2d = _ensure_winding([proj(p) for p in outer_3d], ccw=True)
        # Reorder outer_3d to match out_2d if reversed
        raw_out_2d = [proj(p) for p in outer_3d]
        out_3d_ord = list(outer_3d) if _signed_area_2d(raw_out_2d) > 0 else list(reversed(outer_3d))

        holes_2d: list[list[tuple[float, float]]] = []
        holes_3d_ord: list[list[tuple[float, float, float]]] = []
        for h3 in holes_3d:
            rh2 = [proj(p) for p in h3]
            if _signed_area_2d(rh2) < 0:
                holes_2d.append(rh2)
                holes_3d_ord.append(list(h3))
            else:
                holes_2d.append(list(reversed(rh2)))
                holes_3d_ord.append(list(reversed(h3)))

        idx_map = [vid(*p) for p in out_3d_ord] + [vid(*p) for h in holes_3d_ord for p in h]
        for i0, i1, i2 in _triangulate_polygon_with_holes(out_2d, holes_2d):
            faces.append((idx_map[i0], idx_map[i1], idx_map[i2]))

    # Mounting holes in XY plane (CCW in +X,+Y)
    mnt_loops_xy = [
        _circle_loop(x_mnt, y_mnt, r_mnt, c_steps, ccw=True),
        _circle_loop(x_mnt, -y_mnt, r_mnt, c_steps, ccw=True),
    ]
    # Boss hole in YZ plane centered at (0, z_hole)
    boss_loop_yz = _circle_loop(0.0, z_hole, r_boss, c_steps, ccw=True)

    hw = W / 2.0
    hr = tr / 2.0

    # 1. Bottom face z=0, normal (0, 0, -1): u=(1,0,0), v=(0,-1,0) -> u x v = (0,0,-1)
    emit_planar(
        [(0, -hw, 0), (L, -hw, 0), (L, hw, 0), (0, hw, 0)],
        [[(x, y, 0.0) for x, y in lp] for lp in mnt_loops_xy],
        (1.0, 0.0, 0.0),
        (0.0, -1.0, 0.0),
    )

    # 2. Front toe x=L, normal (+1, 0, 0): u=(0,1,0), v=(0,0,1) -> u x v = (+1,0,0)
    emit_planar([(L, -hw, 0), (L, hw, 0), (L, hw, t1), (L, -hw, t1)], [], (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

    # 3. Top of vertical wall z=H, normal (0, 0, +1): u=(1,0,0), v=(0,1,0)
    emit_planar([(0, -hw, H), (t2, -hw, H), (t2, hw, H), (0, hw, H)], [], (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))

    # 4. -Y outer L-face y=-hw, normal (0, -1, 0): u=(1,0,0), v=(0,0,1) -> u x v = (0,-1,0)
    emit_planar(
        [(0, -hw, 0), (L, -hw, 0), (L, -hw, t1), (t2, -hw, t1), (t2, -hw, H), (0, -hw, H)],
        [],
        (1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0),
    )

    # 5. +Y outer L-face y=+hw, normal (0, +1, 0): u=(-1,0,0), v=(0,0,1) -> u x v = (0,+1,0)
    emit_planar(
        [(0, hw, 0), (L, hw, 0), (L, hw, t1), (t2, hw, t1), (t2, hw, H), (0, hw, H)],
        [],
        (-1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0),
    )

    # 6. Back wall x=0, normal (-1, 0, 0): u=(0,-1,0), v=(0,0,1) -> u x v = (-1,0,0)
    emit_planar(
        [(0, -hw, 0), (0, hw, 0), (0, hw, H), (0, -hw, H)],
        [[(0.0, y, z) for y, z in boss_loop_yz]],
        (0.0, -1.0, 0.0),
        (0.0, 0.0, 1.0),
    )

    # 7. Exposed base top deck z=t1, normal (0, 0, +1): u=(1,0,0), v=(0,1,0)
    emit_planar(
        [
            (t2, -hw, t1),
            (L, -hw, t1),
            (L, hw, t1),
            (t2, hw, t1),
            (t2, hr, t1),
            (x_rib_end, hr, t1),
            (x_rib_end, -hr, t1),
            (t2, -hr, t1),
        ],
        [[(x, y, t1) for x, y in lp] for lp in mnt_loops_xy],
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
    )

    # 8. Exposed inner vertical wall x=t2, normal (+1, 0, 0): u=(0,1,0), v=(0,0,1)
    emit_planar(
        [
            (t2, -hw, t1),
            (t2, -hr, t1),
            (t2, -hr, z_rib_top),
            (t2, hr, z_rib_top),
            (t2, hr, t1),
            (t2, hw, t1),
            (t2, hw, H),
            (t2, -hw, H),
        ],
        [[(t2, y, z) for y, z in boss_loop_yz]],
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )

    # 9. Rib -Y triangle (y=-hr, normal (0, -1, 0)) & +Y triangle (y=+hr, normal (0, +1, 0))
    emit_planar([(t2, -hr, t1), (x_rib_end, -hr, t1), (t2, -hr, z_rib_top)], [], (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    emit_planar([(t2, hr, t1), (x_rib_end, hr, t1), (t2, hr, z_rib_top)], [], (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0))

    # 10. Rib slanted hypotenuse quad: outward normal in (+X, +Z)
    dx_r = x_rib_end - t2
    dz_r = z_rib_top - t1
    r_len = hypot(dx_r, dz_r)
    # u = (0, 1, 0), v = (-dx_r/r_len, 0, dz_r/r_len) -> u x v = (dz_r/r_len, 0, dx_r/r_len) (+X, +Z outward!)
    emit_planar(
        [(x_rib_end, -hr, t1), (x_rib_end, hr, t1), (t2, hr, z_rib_top), (t2, -hr, z_rib_top)],
        [],
        (0.0, 1.0, 0.0),
        (-dx_r / r_len, 0.0, dz_r / r_len),
    )

    # 11. Cylindrical inner walls for 2 vertical mounting holes (CW in XY so normal points inward toward hole center)
    for lp in mnt_loops_xy:
        cw_lp = list(reversed(lp))
        b_ring = [vid(x, y, 0.0) for x, y in cw_lp]
        t_ring = [vid(x, y, t1) for x, y in cw_lp]
        _stitch_rings(faces, b_ring, t_ring)

    # 12. Cylindrical inner wall for horizontal boss hole from x=0 -> x=t2
    # In YZ plane with +X extrusion (u=(0,1,0), v=(0,0,1)), CW in YZ gives inward hole normal!
    cw_boss_yz = list(reversed(boss_loop_yz))
    x0_ring = [vid(0.0, y, z) for y, z in cw_boss_yz]
    xt2_ring = [vid(t2, y, z) for y, z in cw_boss_yz]
    _stitch_rings(faces, x0_ring, xt2_ring)

    mnt_poly_area = sum(abs(_signed_area_2d(lp)) for lp in mnt_loops_xy)
    boss_poly_area = abs(_signed_area_2d(boss_loop_yz))
    vol = (
        L * W * t1
        + t2 * W * (H - t1)
        + 0.5 * (x_rib_end - t2) * (z_rib_top - t1) * tr
        - mnt_poly_area * t1
        - boss_poly_area * t2
    )
    bbox = (0.0, -hw, 0.0, L, hw, H)
    return SolidMesh3D(
        vertices=np.asarray(verts_3d, dtype=np.float64),
        faces=np.asarray(faces, dtype=np.int32),
        expected_volume_mm3=vol,
        expected_bbox_mm=bbox,
    )
