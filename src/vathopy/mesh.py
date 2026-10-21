# SPDX-License-Identifier: GPL-3.0-or-later
"""3D-printable meshes from a depth map: a relief (near raised) or a lithophane of a rendered
picture (thickness = darkness, so it shows the picture when lit from behind).

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
