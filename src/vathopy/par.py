# SPDX-License-Identifier: GPL-3.0-or-later
# Ported from the Vathography Free Blender add-on (c) 2026 Karim Joseph Nassar, GPL-3.0-or-later.
"""Runs the per-pixel picture steps on several CPU threads, block of rows by block of rows."""

import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np

WORKERS = max(1, min(24, os.cpu_count() or 1))
ROWS = 32
_POOL = None


def _pool():
    global _POOL
    if _POOL is None:
        _POOL = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="vfree_par")
    return _POOL


def shutdown():
    global _POOL
    if _POOL is not None:
        _POOL.shutdown(wait=False, cancel_futures=True)
        _POOL = None


def map_rows(fn, h, rows=ROWS):
    return drain(map_rows_steps(fn, h, rows))


def map_rows_steps(fn, h, rows=ROWS, batches=8):
    edges = list(range(0, h, rows)) + [h]
    first = np.asarray(fn(edges[0], edges[1]))
    out = np.empty((h,) + first.shape[1:], first.dtype)
    out[edges[0]:edges[1]] = first
    if len(edges) > 2:
        def job(k):
            out[edges[k]:edges[k + 1]] = fn(edges[k], edges[k + 1])
        futs = [_pool().submit(job, k) for k in range(1, len(edges) - 1)]
        step = max(1, len(futs) // max(1, batches))
        try:
            for i, fut in enumerate(futs):
                fut.result()
                if (i + 1) % step == 0 and i + 1 < len(futs):
                    yield (i + 1) / len(futs)
        finally:
            for fut in futs:
                fut.cancel()
            for fut in futs:
                if not fut.cancelled():
                    try:
                        fut.result()
                    except Exception:
                        pass
    return out


def map_rows_multi(fn, h, rows=ROWS):
    edges = list(range(0, h, rows)) + [h]
    first = tuple(np.asarray(a) for a in fn(edges[0], edges[1]))
    outs = tuple(np.empty((h,) + a.shape[1:], a.dtype) for a in first)
    for o, a in zip(outs, first):
        o[edges[0]:edges[1]] = a

    def job(k):
        for o, a in zip(outs, fn(edges[k], edges[k + 1])):
            o[edges[k]:edges[k + 1]] = a
    for fut in [_pool().submit(job, k) for k in range(1, len(edges) - 1)]:
        fut.result()
    return outs


def drain(gen):
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        return stop.value


def sub(gen, a, b, label):
    try:
        f = next(gen)
        while True:
            own = f[0] if isinstance(f, tuple) else ""
            frac = f if isinstance(f, (int, float)) else f[1]
            yield (own if label is None else label, a + (b - a) * frac)
            f = next(gen)
    except StopIteration as stop:
        return stop.value
