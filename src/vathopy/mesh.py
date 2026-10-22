# SPDX-License-Identifier: GPL-3.0-or-later
"""3D-printable meshes from a depth map: a relief (near raised) or a lithophane of a rendered
picture (thickness = darkness, so it shows the picture when lit from behind), flat or wrapped round
a cylinder or a sphere.

Both are height fields on a flat base, written as one watertight solid in binary STL: the top is
the triangulated grid of heights, walls run down from its border to the base, and the bottom is a
fan from its centre to the border, so every edge is shared by exactly two triangles and every
normal faces out. Units are millimetres. numpy only.
"""

import numpy as np

MAX_TRIANGLES = 4_000_000


def triangle_count(rows, cols):
    """Triangles in the mesh of a rows x cols height field."""
    border = 2 * (rows - 1) + 2 * (cols - 1)
    return 2 * (rows - 1) * (cols - 1) + 3 * border


def _border(rows, cols):
    """Grid indices (i, j) round the border, counter-clockwise seen from above (x right, y up = row 0)."""
    path = [(rows - 1, j) for j in range(cols - 1)]                 # bottom row, left to right
    path += [(i, cols - 1) for i in range(rows - 1, 0, -1)]         # right side, upwards
    path += [(0, j) for j in range(cols - 1, 0, -1)]                # top row, right to left
    path += [(i, 0) for i in range(rows - 1)]                       # left side, downwards
    return path


def heightfield_mesh(top, px_mm, base_mm):
    """``top``: heights in mm above the base, [rows, cols], rows top-down. Returns (vertices [n, 3],
    faces [m, 3] int) of the closed solid: x right, y up (row 0 at the top), z up from 0."""
    top = np.asarray(top, np.float64)
    rows, cols = top.shape
    if rows < 2 or cols < 2:
        raise ValueError("need at least 2 x 2 heights")
    if triangle_count(rows, cols) > MAX_TRIANGLES:
        raise ValueError(f"{rows} x {cols} heights make {triangle_count(rows, cols):,} triangles; "
                         f"at most {MAX_TRIANGLES:,}: use fewer pixels")
    jj, ii = np.meshgrid(np.arange(cols), np.arange(rows))
    grid = np.stack([jj * px_mm, (rows - 1 - ii) * px_mm, base_mm + top], axis=-1).reshape(-1, 3)
    idx = np.arange(rows * cols).reshape(rows, cols)
    a, b, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, :-1], idx[1:, 1:]   # a b / c d
    faces = [np.stack([c, d, b], -1).reshape(-1, 3), np.stack([c, b, a], -1).reshape(-1, 3)]
    loop = _border(rows, cols)
    top_ring = np.array([idx[i, j] for i, j in loop])
    n0 = len(grid)
    bottom = grid[top_ring].copy()
    bottom[:, 2] = 0.0
    centre = np.array([[(cols - 1) * px_mm / 2, (rows - 1) * px_mm / 2, 0.0]])
    verts = np.concatenate([grid, bottom, centre])
    bot_ring = n0 + np.arange(len(loop))
    cid = n0 + len(loop)
    p, q = top_ring, np.roll(top_ring, -1)
    pb, qb = bot_ring, np.roll(bot_ring, -1)
    faces += [np.stack([p, pb, qb], -1), np.stack([p, qb, q], -1),            # walls, facing out
              np.stack([np.full_like(pb, cid), qb, pb], -1)]                  # the base, facing down
    return verts.astype(np.float32), np.concatenate(faces).astype(np.int64)


def _shell(outer, inner, wrap, poles=None):
    """A closed shell between two grids of points [rows, cols, 3] (u = columns going round the axis
    counter-clockwise seen from above, v = rows going down): the outer surface, the inner one, and
    joining them the top and bottom (rims, or with ``poles`` = (outer top, inner top, outer bottom,
    inner bottom) points, fans to them) and, unless ``wrap`` (the columns go all the way round), the
    two side walls. Returns (vertices, faces), normals out."""
    rows, cols = outer.shape[:2]
    n = rows * cols
    o = np.arange(n).reshape(rows, cols)
    i = o + n
    verts = [outer.reshape(-1, 3), inner.reshape(-1, 3)]
    if poles is not None:
        verts.append(np.asarray(poles, np.float64).reshape(4, 3))
        pto, pti, pbo, pbi = 2 * n, 2 * n + 1, 2 * n + 2, 2 * n + 3
    jj = np.arange(cols if wrap else cols - 1)
    j1 = (jj + 1) % cols
    a, b, c, d = o[:-1][:, jj], o[:-1][:, j1], o[1:][:, jj], o[1:][:, j1]
    ia, ib, ic, id_ = i[:-1][:, jj], i[:-1][:, j1], i[1:][:, jj], i[1:][:, j1]
    faces = [np.stack([a, c, b], -1), np.stack([b, c, d], -1),                  # outer, facing out
             np.stack([ia, ib, ic], -1), np.stack([ib, id_, ic], -1)]           # inner, facing in
    ot, ot1, it, it1 = o[0, jj], o[0, j1], i[0, jj], i[0, j1]
    ob, ob1, ib_, ib1 = o[-1, jj], o[-1, j1], i[-1, jj], i[-1, j1]
    if poles is None:
        faces += [np.stack([ot, ot1, it], -1), np.stack([ot1, it1, it], -1),    # top rim
                  np.stack([ob1, ob, ib1], -1), np.stack([ob, ib_, ib1], -1)]   # bottom rim
    else:
        full = np.full_like(jj, 0)
        faces += [np.stack([ot, ot1, full + pto], -1), np.stack([it1, it, full + pti], -1),
                  np.stack([ob1, ob, full + pbo], -1), np.stack([ib_, ib1, full + pbi], -1)]
    if not wrap:
        for j, right in ((0, False), (cols - 1, True)):
            lo, li = list(o[:, j]), list(i[:, j])
            if poles is not None:
                lo, li = [pto] + lo + [pbo], [pti] + li + [pbi]
            lo, li = np.array(lo), np.array(li)
            k0, k1 = slice(None, -1), slice(1, None)
            t1 = np.stack([lo[k1], lo[k0], li[k0]], -1)
            t2 = np.stack([lo[k1], li[k0], li[k1]], -1)
            faces += [t1[:, ::-1], t2[:, ::-1]] if right else [t1, t2]
    return (np.concatenate(verts).astype(np.float32),
            np.concatenate([f.reshape(-1, 3) for f in faces]).astype(np.int64))


def _check_size(rows, cols):
    if rows < 2 or cols < 2:
        raise ValueError("need at least 2 x 2 heights")
    tri = 4 * rows * cols + 4 * rows + 4 * cols
    if tri > MAX_TRIANGLES:
        raise ValueError(f"{rows} x {cols} heights make about {tri:,} triangles; at most {MAX_TRIANGLES:,}: use fewer pixels")


def cylinder_mesh(top, radius_mm, thickness_mm, arc_deg=360.0):
    """``top`` (heights in mm, [rows, cols], rows top-down) wrapped round a cylinder standing on z = 0:
    the picture's width goes ``arc_deg`` round the axis (360 = all the way, the ends joined), its height
    up the axis at the same spacing, so it keeps its proportions. The heights stand out from
    ``radius_mm``; the wall behind them is ``thickness_mm`` thick (inside). Returns (vertices, faces)."""
    top = np.asarray(top, np.float64)
    rows, cols = top.shape
    _check_size(rows, cols)
    r, t, arc = float(radius_mm), float(thickness_mm), np.radians(min(max(float(arc_deg), 1.0), 360.0))
    if not (r > 0 and 0 < t < r):
        raise ValueError("the radius must be above 0 and the wall thickness between 0 and the radius")
    wrap = arc >= 2 * np.pi - 1e-9
    th = arc * np.arange(cols) / (cols if wrap else cols - 1)
    step = r * (th[1] - th[0])
    z = (rows - 1 - np.arange(rows)) * step
    cs, sn = np.cos(th)[None, :], np.sin(th)[None, :]
    R = r + top
    zz = np.broadcast_to(z[:, None], (rows, cols))
    outer = np.stack([R * cs, R * sn, zz], -1)
    ri = r - t
    inner = np.stack([np.broadcast_to(ri * cs, (rows, cols)), np.broadcast_to(ri * sn, (rows, cols)), zz], -1)
    return _shell(outer, inner, wrap)


def sphere_mesh(top, radius_mm, thickness_mm, lon_deg=360.0, lat_deg=None):
    """``top`` (heights in mm, [rows, cols], rows top-down) wrapped round a sphere centred on the origin:
    the width goes ``lon_deg`` round (360 = all the way) and the height ``lat_deg`` from north to south,
    centred on the equator (default: the picture's proportions at the equator, at most 180 = pole to
    pole, where the top and bottom rows close at the poles). The heights stand out from ``radius_mm``;
    the shell behind them is ``thickness_mm`` thick. Returns (vertices, faces)."""
    top = np.asarray(top, np.float64)
    rows, cols = top.shape
    _check_size(rows, cols)
    r, t = float(radius_mm), float(thickness_mm)
    if not (r > 0 and 0 < t < r):
        raise ValueError("the radius must be above 0 and the shell thickness between 0 and the radius")
    lon = np.radians(min(max(float(lon_deg), 1.0), 360.0))
    wrap = lon >= 2 * np.pi - 1e-9
    lat = np.radians(min(float(lat_deg), 180.0)) if lat_deg else min(lon * rows / cols, np.pi)
    poles = lat >= np.pi - 1e-9
    th = lon * np.arange(cols) / (cols if wrap else cols - 1)
    ph = lat / 2 - lat * ((np.arange(rows) + 0.5) / rows if poles else np.arange(rows) / (rows - 1))
    cph, sph = np.cos(ph)[:, None], np.sin(ph)[:, None]
    unit = np.stack([cph * np.cos(th)[None, :], cph * np.sin(th)[None, :], np.broadcast_to(sph, (rows, cols))], -1)
    outer = unit * (r + top)[..., None]
    inner = unit * (r - t)
    pole_pts = None
    if poles:
        pole_pts = [(0, 0, r + top[0].mean()), (0, 0, r - t), (0, 0, -(r + top[-1].mean())), (0, 0, -(r - t))]
    return _shell(outer, inner, wrap, pole_pts)


def stl_bytes(verts, faces):
    """Binary STL of a triangle mesh (per-face normals from the winding)."""
    tri = verts[faces].astype(np.float32)                               # [m, 3, 3]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    rec = np.zeros(len(faces), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    rec["n"], rec["v"] = n, tri
    header = b"vathopy height field, mm".ljust(80, b" ")
    return header + np.uint32(len(faces)).tobytes() + rec.tobytes()


def write_stl(path, verts, faces):
    with open(path, "wb") as fh:
        fh.write(stl_bytes(verts, faces))


def _resize(a, cols):
    """``a`` resized to ``cols`` columns (aspect kept), averaging when it shrinks."""
    import cv2
    h, w = a.shape[:2]
    cols = max(2, int(cols))
    rows = max(2, round(h * cols / w))
    interp = cv2.INTER_AREA if cols < w else cv2.INTER_LINEAR
    return cv2.resize(np.ascontiguousarray(a, dtype=np.float32), (cols, rows), interpolation=interp)


def relief_heights(depth_td, cols, relief_mm, invert=False):
    """A near-bright depth map (rows top-down, 0..1) -> heights in mm, ``cols`` across: near raised
    ``relief_mm`` above far (or the other way with ``invert``)."""
    d = np.clip(_resize(depth_td, cols), 0.0, 1.0)
    return (1.0 - d if invert else d) * float(relief_mm)


def lithophane_heights(srgb_td, cols, min_mm, max_mm):
    """A picture (rows top-down, sRGB 0..1, RGB or grey) -> thickness above ``min_mm`` in mm: dark
    parts thick (up to ``max_mm`` in all), light parts thin, so it reads when lit from behind."""
    a = np.asarray(srgb_td, np.float32)
    lum = a[..., :3] @ np.array([0.2126, 0.7152, 0.0722], np.float32) if a.ndim == 3 else a
    return (1.0 - np.clip(_resize(lum, cols), 0.0, 1.0)) * (float(max_mm) - float(min_mm))


def check_closed(faces):
    """True if every edge is used by exactly two faces, once in each direction (closed, consistently wound)."""
    e = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    fwd = {tuple(x) for x in e.tolist()}
    return len(fwd) == len(e) and all((b, a) in fwd for a, b in fwd)


def volume(verts, faces):
    """Signed volume (mm³; positive when the normals face out)."""
    t = verts[faces].astype(np.float64)
    return float(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6.0)
