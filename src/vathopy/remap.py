# SPDX-License-Identifier: GPL-3.0-or-later
# Ported from the Vathography Free Blender add-on (c) 2026 Karim Joseph Nassar, GPL-3.0-or-later.
"""The depth transfer used by the depth controls, in numpy."""

import numpy as np

BIG = 1.0e6
F_MIN = 1.0e-6


def smoothstep(edge0, edge1, x):
    t = np.clip((x - edge0) / np.maximum(edge1 - edge0, 1e-12), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def resolve_bounds(centers, feathers, mins=None, maxs=None):
    n = len(centers)
    c = np.asarray(centers, dtype=np.float64)
    fe = np.asarray(feathers, dtype=np.float64)
    mins = [None] * n if mins is None else list(mins)
    maxs = [None] * n if maxs is None else list(maxs)
    lo = np.empty(n); hi = np.empty(n)
    for i in range(n):
        if maxs[i] is not None:
            hi[i] = maxs[i]
        elif i == n - 1:
            hi[i] = BIG
        elif mins[i + 1] is not None:
            hi[i] = mins[i + 1]
        else:
            hi[i] = 0.5 * (c[i] + c[i + 1])
        if mins[i] is not None:
            lo[i] = mins[i]
        elif i == 0:
            lo[i] = -BIG
        elif maxs[i - 1] is not None:
            lo[i] = maxs[i - 1]
        else:
            lo[i] = 0.5 * (c[i - 1] + c[i])
    Flo = fe.copy(); Fhi = fe.copy()
    for i in range(n - 1):
        if abs(hi[i] - lo[i + 1]) < 1e-9:
            m = 0.5 * (fe[i] + fe[i + 1])
            Fhi[i] = m; Flo[i + 1] = m
    return lo, hi, np.maximum(Flo, F_MIN), np.maximum(Fhi, F_MIN)


def transfer_global(d, centers, offsets, feathers, mins=None, maxs=None):
    d = np.asarray(d, dtype=np.float32)
    n = len(centers)
    if n == 0:
        return d.copy()
    order = np.argsort(np.asarray(centers, dtype=np.float64), kind="stable")
    c = np.asarray(centers, dtype=np.float64)[order]
    o = np.asarray(offsets, dtype=np.float64)[order]
    fe = np.asarray(feathers, dtype=np.float64)[order]
    mins = None if mins is None else [mins[k] for k in order]
    maxs = None if maxs is None else [maxs[k] for k in order]
    lo, hi, Flo, Fhi = resolve_bounds(c, fe, mins, maxs)

    dd = d.astype(np.float64)
    pos = np.clip(np.searchsorted(c, dd, side="left"), 0, n - 1)
    left = np.clip(pos - 1, 0, n - 1)
    i = np.where(np.abs(dd - c[left]) < np.abs(dd - c[pos]), left, pos)

    wsum = np.zeros_like(dd); wo = np.zeros_like(dd)
    for step in (-1, 0, 1):
        k = i + step
        valid = (k >= 0) & (k < n)
        kk = np.clip(k, 0, n - 1)
        w = smoothstep(lo[kk] - Flo[kk], lo[kk] + Flo[kk], dd) * (1.0 - smoothstep(hi[kk] - Fhi[kk], hi[kk] + Fhi[kk], dd))
        w = np.where(valid, w, 0.0)
        wsum += w; wo += w * o[kk]
    out = dd + wo / np.maximum(wsum, 1.0)
    return out.astype(np.float32)


def transfer(d, centers, offsets, feathers, mins=None, maxs=None):
    return transfer_global(d, centers, offsets, feathers, mins, maxs)
