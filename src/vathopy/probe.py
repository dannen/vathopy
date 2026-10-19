# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe a depth map for the most detail it can carry (vathopy only; no add-on equivalent).

What limits contour lines and fringe cycles is the depth's medium-scale wobble: gentle bumps a
few to tens of pixels across in surfaces that should be smooth. A contour line drifts sideways
by (wobble / slope) pixels, and neighbouring lines are (level step / slope) pixels apart, so the
slope cancels: lines start to tangle once the step between levels nears a few times the wobble,
however steep the surface. Hence

    max levels = 1 / (K * wobble)          (wobble as a fraction of the depth range)

The wobble is measured at the 80th percentile of the surfaces, not the median: lines look
broken as soon as any sizeable region (a noisy sky, foliage) tangles, even if most of the
picture is clean. K is calibrated on test photos (2026-10-18): a grainy film scan broke up at
about 5 fringe cycles, a noisy sky at about 6 (the rest of that photo would carry far more),
a clean portrait went further. Contour lines are one per
level, and a fringe cycle has two light/dark edges, so cycles = levels / 2.

Pixel-to-pixel jitter is much smaller than that wobble and doesn't limit the level count, but it
makes lines jagged; ``smooth_depth`` removes it. Its radius is recommended in proportion to the
picture size (4 px at a 1280 px long edge).

The depth distribution is measured too: when the middle half of the pixels spans less than
40% of the range, most of the ramp's colours and contours fall on a small part of the picture,
and ``equalize`` (spread the ramp by pixel count) is suggested.
"""

import numpy as np

K = 34.0                 # levels = 1 / (K * wobble); see the docstring
WOBBLE_PERCENTILE = 80   # of |residual| on surfaces; 1.2816 sigma for a normal distribution
SMOOTH_PER_1280 = 4.0    # recommended smooth_depth radius at a 1280 px long edge
NARROW_SPREAD = 0.40     # middle-half spread below this -> suggest equalize
EDGE_PERCENTILE = 92     # gradients above this are object edges, left out of the wobble
REF_RADIUS = 12.0        # edge-preserving smoothing radius that stands for the "true" surfaces


def equalized(norm, bins=4096):
    """`norm` (0..1) remapped by its own cumulative histogram: each part of the ramp then covers
    about the same number of pixels (vathopy's ``equalize``)."""
    x = np.clip(np.asarray(norm, dtype=np.float64), 0.0, 1.0)
    hist, edges = np.histogram(x, bins=bins, range=(0.0, 1.0))
    cdf = np.concatenate([[0.0], np.cumsum(hist)]).astype(np.float64)
    cdf /= cdf[-1] if cdf[-1] > 0 else 1.0
    return np.interp(x, edges, cdf).astype(np.float32)


def wobble(depth):
    """The depth's medium-scale wobble as a fraction of its value range: a sigma estimated from
    the 80th percentile of the residual on surfaces (away from object edges), so that a noisy
    region covering a fifth of the picture or more counts."""
    import cv2
    from .engine import smooth_depth
    d = np.asarray(depth, dtype=np.float32)
    rng = float(d.max() - d.min()) or 1.0
    ref = smooth_depth(d, REF_RADIUS)
    resid = (d - ref).astype(np.float64)
    g = np.hypot(*np.gradient(ref.astype(np.float64)))
    edges = cv2.dilate((g > np.percentile(g, EDGE_PERCENTILE)).astype(np.uint8),
                       np.ones((7, 7), np.uint8)).astype(bool)
    r = resid[~edges] if (~edges).any() else resid.ravel()
    r = np.abs(r - np.median(r))
    return float(np.percentile(r, WOBBLE_PERCENTILE) / 1.2816) / rng


def _half(v, lo=1.0, hi=10.0):
    return float(min(hi, max(lo, round(v * 2) / 2)))


def probe(depth, smooth=None, equalize=False):
    """Recommended settings for a depth map (near = bright, any row order).

    ``smooth``: the smooth_depth radius you'll render with (None = use the recommended one).
    ``equalize``: measure as it will render with equalize on.
    Returns a dict: smooth_depth, wobble, max_levels, contours, cycles, spread, equalize."""
    from .engine import smooth_depth
    d = np.asarray(depth, dtype=np.float32)
    h, w = d.shape
    rec_smooth = round(max(1.0, SMOOTH_PER_1280 * max(h, w) / 1280.0), 1)
    radius = rec_smooth if smooth is None else float(smooth)
    ds = smooth_depth(d, radius) if radius > 0 else d
    lo, hi = float(ds.min()), float(ds.max())
    norm = (ds - lo) / ((hi - lo) or 1.0)
    p25, p75 = np.percentile(norm, [25, 75])
    spread = float(p75 - p25)
    if equalize:
        norm = equalized(norm)
    wb = max(wobble(norm), 1e-6)
    levels = 1.0 / (K * wb)
    return {
        "smooth_depth": radius,
        "recommended_smooth_depth": rec_smooth,
        "wobble": wb,
        "max_levels": levels,
        "contours": _half(levels),
        "cycles": _half(levels / 2.0),
        "spread": spread,
        "equalize": bool(equalize),
        "suggest_equalize": (not equalize) and spread < NARROW_SPREAD,
    }
