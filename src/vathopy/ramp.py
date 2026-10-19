# SPDX-License-Identifier: GPL-3.0-or-later
"""Colour ramps: a numpy re-implementation of Blender's ColorRamp (ColorBand) evaluation, the
Vathography add-on's ramp presets, and the 65536-sample lookup used to apply a ramp per pixel.

Stops are (position, (r, g, b, a)) with colours in scene-linear, as Blender stores them."""

import numpy as np

INTERPOLATIONS = ("CONSTANT", "LINEAR", "EASE", "B_SPLINE", "CARDINAL")
COLOR_MODES = ("RGB", "HSV", "HSL")
HUE_MODES = ("NEAR", "FAR", "CW", "CCW")

RAMP_LUT = 65536


# --- Blender colour conversions (BLI math_color), vectorised, float32 -------------------------

def _rgb_to_hsv(c):
    r, g, b = (c[..., i].astype(np.float32) for i in range(3))
    k = np.zeros_like(r)
    sw = g < b
    g, b = np.where(sw, b, g), np.where(sw, g, b)
    k = np.where(sw, np.float32(-1.0), k)
    min_gb = b.copy()
    sw2 = r < g
    r, g = np.where(sw2, g, r), np.where(sw2, r, g)
    k = np.where(sw2, np.float32(-2.0 / 6.0) - k, k)
    min_gb = np.where(sw2, np.minimum(g, b), min_gb)
    chroma = r - min_gb
    h = np.abs(k + (g - b) / (np.float32(6.0) * chroma + np.float32(1e-20)))
    s = chroma / (r + np.float32(1e-20))
    return np.stack([h, s, r], axis=-1).astype(np.float32)


def _hsv_to_rgb(c):
    h, s, v = (c[..., i].astype(np.float32) for i in range(3))
    nr = np.clip(np.abs(h * 6.0 - 3.0) - 1.0, 0.0, 1.0)
    ng = np.clip(2.0 - np.abs(h * 6.0 - 2.0), 0.0, 1.0)
    nb = np.clip(2.0 - np.abs(h * 6.0 - 4.0), 0.0, 1.0)
    return np.stack([((nr - 1.0) * s + 1.0) * v, ((ng - 1.0) * s + 1.0) * v, ((nb - 1.0) * s + 1.0) * v],
                    axis=-1).astype(np.float32)


def _rgb_to_hsl(c):
    r, g, b = (c[..., i].astype(np.float32) for i in range(3))
    cmax = np.maximum(np.maximum(r, g), b)
    cmin = np.minimum(np.minimum(r, g), b)
    l = np.minimum(np.float32(1.0), (cmax + cmin) / np.float32(2.0))
    d = cmax - cmin
    same = cmax == cmin
    dd = np.where(same, np.float32(1.0), d)
    s = np.where(l > 0.5, dd / np.where(same, 1.0, 2.0 - cmax - cmin), dd / np.where(same, 1.0, cmax + cmin))
    h = np.where(cmax == r, (g - b) / dd + np.where(g < b, 6.0, 0.0),
                 np.where(cmax == g, (b - r) / dd + 2.0, (r - g) / dd + 4.0))
    h = np.where(same, 0.0, h / 6.0)
    s = np.where(same, 0.0, s)
    return np.stack([h, s, l], axis=-1).astype(np.float32)


def _hsl_to_rgb(c):
    h, s, l = (c[..., i].astype(np.float32) for i in range(3))
    nr = np.clip(np.abs(h * 6.0 - 3.0) - 1.0, 0.0, 1.0)
    ng = np.clip(2.0 - np.abs(h * 6.0 - 2.0), 0.0, 1.0)
    nb = np.clip(2.0 - np.abs(h * 6.0 - 4.0), 0.0, 1.0)
    chroma = (1.0 - np.abs(2.0 * l - 1.0)) * s
    return np.stack([(nr - 0.5) * chroma + l, (ng - 0.5) * chroma + l, (nb - 0.5) * chroma + l],
                    axis=-1).astype(np.float32)


def _hue_interp(mode, mfac, fac, h1, h2):
    hmod = lambda h: np.where(h < 1.0, h, h - 1.0)
    h1, h2 = hmod(h1), hmod(h2)
    if mode == "NEAR":
        m = np.where((h1 < h2) & ((h2 - h1) > 0.5), 1, np.where((h1 > h2) & ((h2 - h1) < -0.5), 2, 0))
    elif mode == "FAR":
        m = np.where(h1 == h2, 1, np.where((h1 < h2) & ((h2 - h1) < 0.5), 1,
                                           np.where((h1 > h2) & ((h2 - h1) > -0.5), 2, 0)))
    elif mode == "CCW":
        m = np.where(h1 > h2, 2, 0)
    else:  # CW
        m = np.where(h1 < h2, 1, 0)
    a = np.where(m == 1, h1 + 1.0, h1)
    b = np.where(m == 2, h2 + 1.0, h2)
    hi = mfac * a + fac * b
    return np.where(m == 0, hi, hmod(hi)).astype(np.float32)


def _weights(t, kind):
    t2 = t * t
    t3 = t2 * t
    if kind == "CARDINAL":
        fc = np.float32(0.71)
        return (-fc * t3 + 2.0 * fc * t2 - fc * t,
                (2.0 - fc) * t3 + (fc - 3.0) * t2 + 1.0,
                (fc - 2.0) * t3 + (3.0 - 2.0 * fc) * t2 + fc * t,
                fc * t3 - fc * t2)
    return (np.float32(-0.16666666) * t3 + 0.5 * t2 - 0.5 * t + np.float32(0.16666666),
            0.5 * t3 - t2 + np.float32(0.66666666),
            -0.5 * t3 + 0.5 * t2 + 0.5 * t + np.float32(0.16666666),
            np.float32(0.16666666) * t3)


class Ramp:
    """A colour ramp. ``stops`` is a list of (position, (r, g, b, a)), scene-linear."""

    def __init__(self, stops, interpolation="LINEAR", color_mode="RGB", hue_interpolation="NEAR"):
        if not stops:
            raise ValueError("a ramp needs at least one stop")
        if interpolation not in INTERPOLATIONS:
            raise ValueError(f"unknown interpolation {interpolation!r}")
        if color_mode not in COLOR_MODES:
            raise ValueError(f"unknown colour mode {color_mode!r}")
        if hue_interpolation not in HUE_MODES:
            raise ValueError(f"unknown hue interpolation {hue_interpolation!r}")
        st = []
        for p, c in stops:
            c = tuple(float(v) for v in c)
            if len(c) == 3:
                c = c + (1.0,)
            if len(c) != 4:
                raise ValueError("a stop colour needs 3 or 4 components")
            st.append((float(p), c))
        st.sort(key=lambda e: e[0])  # Blender keeps its elements sorted by position
        self.stops = st
        self.interpolation = interpolation
        self.color_mode = color_mode
        self.hue_interpolation = hue_interpolation

    # -- serialisation --
    def to_dict(self):
        return {"interpolation": self.interpolation, "color_mode": self.color_mode,
                "hue_interpolation": self.hue_interpolation,
                "stops": [[p, list(c)] for p, c in self.stops]}

    @classmethod
    def from_dict(cls, d):
        if "preset" in d:
            r = preset(d["preset"])
            return r.mirrored() if d.get("mirror") else r
        return cls([(p, c) for p, c in d["stops"]], d.get("interpolation", "LINEAR"),
                   d.get("color_mode", "RGB"), d.get("hue_interpolation", "NEAR"))

    def mirrored(self):
        """The add-on's mirror (used when a ramp is flipped to read near-left)."""
        el = self.stops
        hue = {"CW": "CCW", "CCW": "CW"}.get(self.hue_interpolation, self.hue_interpolation)
        if self.interpolation != "CONSTANT" or len(el) == 1:
            new = [(1.0 - p, c) for p, c in reversed(el)]
        else:
            new = [(1.0 - el[i + 1][0], el[i][1]) for i in range(len(el) - 2, -1, -1)]
            if new[0][0] <= 1e-6:
                new[0] = (1e-6, new[0][1])
            if len(new) < 32:
                new = [(0.0, el[-1][1])] + new
        return Ramp(new, self.interpolation, self.color_mode, hue)

    # -- evaluation --
    def evaluate(self, x):
        """RGBA at positions ``x`` (any shape), matching Blender's ColorRamp.evaluate()."""
        x = np.asarray(x, dtype=np.float32)
        shape = x.shape
        x = x.ravel()
        pos = np.array([p for p, _ in self.stops], dtype=np.float32)
        col = np.array([c for _, c in self.stops], dtype=np.float32)
        tot = len(pos)
        if tot == 1:
            return np.broadcast_to(col[0], shape + (4,)).astype(np.float32).copy()
        ipo = self.interpolation if self.color_mode == "RGB" else "LINEAR"
        simple = ipo in ("LINEAR", "EASE", "CONSTANT")

        a = np.searchsorted(pos, x, side="right")  # index of the first stop with pos > x
        at_end = a == tot
        at_start = a == 0
        i1 = np.clip(a, 0, tot - 1)            # cbd1: right stop
        i2 = np.clip(a - 1, 0, tot - 1)        # cbd2: left stop
        p1 = np.where(at_end, np.float32(1.0), pos[i1])
        c1 = np.where(at_end[:, None], col[tot - 1], col[i1])
        p2 = np.where(at_start, np.float32(0.0), pos[i2])
        c2 = np.where(at_start[:, None], col[0], col[i2])

        out = np.empty((x.size, 4), dtype=np.float32)
        hold_left = (at_end & simple) | (ipo == "CONSTANT")
        if ipo == "CONSTANT":
            out[:] = c2
        else:
            same = p2 == p1
            denom = np.where(same, np.float32(1.0), p2 - p1)
            fac = np.where(same, np.where(at_end, np.float32(1.0), np.float32(0.0)), (x - p1) / denom)
            fac = fac.astype(np.float32)
            if ipo in ("B_SPLINE", "CARDINAL"):
                i0 = np.where(a >= tot - 1, i1, np.clip(a + 1, 0, tot - 1))
                c0 = np.where((a >= tot - 1)[:, None], c1, col[i0])
                i3 = np.where(a < 2, i2, np.clip(a - 2, 0, tot - 1))
                c3 = np.where((a < 2)[:, None], c2, col[i3])
                t = np.clip(fac, 0.0, 1.0).astype(np.float32)
                w0, w1, w2, w3 = (np.asarray(w, dtype=np.float32)[:, None] for w in _weights(t, ipo))
                out[:] = np.clip(w3 * c3 + w2 * c2 + w1 * c1 + w0 * c0, 0.0, 1.0)
            else:
                if ipo == "EASE":
                    f2 = fac * fac
                    fac = (3.0 * f2 - 2.0 * f2 * fac).astype(np.float32)
                mfac = (np.float32(1.0) - fac).astype(np.float32)
                if self.color_mode == "RGB":
                    out[:] = mfac[:, None] * c1 + fac[:, None] * c2
                else:
                    to_, from_ = ((_rgb_to_hsv, _hsv_to_rgb) if self.color_mode == "HSV"
                                  else (_rgb_to_hsl, _hsl_to_rgb))
                    h1, h2 = to_(c1[:, :3]), to_(c2[:, :3])
                    hh = np.empty_like(h1)
                    hh[:, 0] = _hue_interp(self.hue_interpolation, mfac, fac, h1[:, 0], h2[:, 0])
                    hh[:, 1] = mfac * h1[:, 1] + fac * h2[:, 1]
                    hh[:, 2] = mfac * h1[:, 2] + fac * h2[:, 2]
                    out[:, :3] = from_(hh)
                    out[:, 3] = mfac * c1[:, 3] + fac * c2[:, 3]
            if simple:
                out[hold_left] = c2[hold_left]
        if simple:
            below = x <= pos[0]
            out[below] = col[0]
        return out.reshape(shape + (4,))

    def lut(self):
        """The add-on's per-pixel ramp table: 65536 even samples plus every stop position."""
        pos = np.union1d(np.linspace(0.0, 1.0, RAMP_LUT), np.array([p for p, _ in self.stops]))
        pos = np.clip(pos, 0.0, 1.0)
        vals = self.evaluate(pos.astype(np.float32))[:, :3].astype(np.float64)
        return pos, vals


def apply_lut(norm, lut, ch0=False):
    pos, vals = lut
    norm = np.asarray(norm)
    x = norm.astype(np.float64).ravel()
    if ch0:
        return np.interp(x, pos, vals[:, 0]).astype(np.float32).reshape(norm.shape)
    out = np.empty((x.size, 3), dtype=np.float32)
    for k in range(3):
        out[:, k] = np.interp(x, pos, vals[:, k])
    return out.reshape(norm.shape + (3,))


# --- presets (the add-on's Load preset menu) ------------------------------------------------

def srgb_to_linear(c):
    c = float(c)
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _hex(h):
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def _load_presets():
    import json
    import os
    with open(os.path.join(os.path.dirname(__file__), "presets.json"), encoding="utf-8") as fh:
        return json.load(fh)


PRESETS = _load_presets()  # the add-on's preset menu in its order, then vathopy's additions


def preset(key):
    try:
        e = PRESETS[key]
    except KeyError:
        raise KeyError(f"unknown ramp preset {key!r}; known: {', '.join(PRESETS)}") from None
    interp, stops = e["interpolation"], e["stops"]
    el = []
    for p, c in stops:
        rgb = _hex(c) if isinstance(c, str) else (float(c),) * 3
        el.append((float(p), tuple(srgb_to_linear(v) for v in rgb) + (1.0,)))
    return Ramp(el, interp, "RGB", "NEAR")
