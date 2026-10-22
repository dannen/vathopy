# SPDX-License-Identifier: GPL-3.0-or-later
# Ported from the Vathography Free Blender add-on (c) 2026 Karim Joseph Nassar, GPL-3.0-or-later:
# common.py, artistic.py, bands.py, sumchain.py and shading.py, with the Blender scene replaced by a
# look dict (see look.py). The arithmetic, including its float32/float64 choices, follows the add-on
# so that results match its numpy export.
"""Depth map + look -> artistic picture.

Arrays here are in Blender's row order: row 0 is the BOTTOM of the picture (io.py converts)."""

import math
import time
from types import SimpleNamespace

import numpy as np

from . import par as PAR
from . import remap
from .look import DEFAULT_AREA, DEFAULT_BAND, _merge, normalise
from .ramp import Ramp, apply_lut

# constants from the add-on (common.py / shading.py)
MIN_SCALE = 1e-3
MIN_LIGHT_RADIUS = 0.01
MIN_REACH_FACTOR = 0.01
MIN_REACH_M = MIN_LIGHT_RADIUS * MIN_REACH_FACTOR
DEFAULT_LIGHT_CORE_DETAIL = 1.0
MASK_EDGE_EPS = 1e-4
WINDOW_LOW, WINDOW_HIGH = 0.0, 1.0
BAND_STOPS_U = (0.2, 0.4, 0.6, 0.8)
BAND_SEG_FLOOR = 1e-6
MIN_BAND_SOFTNESS_M = 1e-6
BAND_T_MIN_M = 0.001
BAND_MIN_NZ = math.cos(math.radians(85.0))
ALL_BANDS, ALL_LIGHTS = "ALL_BANDS", "ALL_LIGHTS"
BAND_SOLO = "BAND:"


def _unit(v, fallback):
    v = np.asarray(v, dtype=np.float64)
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        return np.asarray(fallback, dtype=np.float64)
    return v if abs(n - 1.0) < 1e-6 else v / n


# --- plane geometry ------------------------------------------------------------------------

def plane_dims(lk, shape):
    h, w = shape
    pw = float(lk["plane"]["width"])
    ph = lk["plane"]["height"]
    ph = float(ph) if ph else pw / (w / h)
    return pw, ph


def plane_pixel_xy(pw, ph, h, w):
    px = ((np.arange(w, dtype=np.float32) + 0.5) / w - 0.5) * float(pw)
    py = ((np.arange(h, dtype=np.float32) + 0.5) / h - 0.5) * float(ph)
    return px, py


# --- ramp tilt (common.ramp_tilt / tilt_norm) ----------------------------------------------

RampTilt = SimpleNamespace


def tilt_fit_scale(nx, ny, nz, x0, y0, n_mid, sp, width, height):
    across = (abs(nx) * 0.5 * float(width) + abs(ny) * 0.5 * float(height)) / sp
    shift = (nx * x0 + ny * y0) / sp
    d_hi = max(-nz * n_mid, nz * (1.0 - n_mid)) + across - shift
    d_lo = min(-nz * n_mid, nz * (1.0 - n_mid)) - across - shift
    return max(1.0, d_hi / max(1.0 - n_mid, 1e-6), -d_lo / max(n_mid, 1e-6))


def resolve_tilt(t, lo, hi, s_eff, mid, pw, ph):
    if t is None:
        return None
    n = _unit(t["normal"], (0.0, 1.0, 0.0))
    span = max(float(hi) - float(lo), 1e-6)
    sp = s_eff * span
    nx, ny, nz = (float(c) for c in n)
    x0, y0 = float(t["x0"]), float(t["y0"])
    n_mid = (mid - float(lo)) / span
    fit = tilt_fit_scale(nx, ny, nz, x0, y0, n_mid, sp, pw, ph) if t.get("fit") else 1.0
    return RampTilt(nx=nx, ny=ny, nz=nz, x0=x0, y0=y0, n_mid=n_mid, sp=sp, fit=fit)


def tilt_norm(norm_u, X, Y, t):
    if t.fit == 1.0:
        return (t.n_mid + t.nz * (np.asarray(norm_u, dtype=np.float64) - t.n_mid)
                + (t.nx * (np.asarray(X, dtype=np.float64) - t.x0) + t.ny * (np.asarray(Y, dtype=np.float64) - t.y0)) / t.sp)
    dev = (t.nz * (np.asarray(norm_u, dtype=np.float64) - t.n_mid)
           + (t.nx * (np.asarray(X, dtype=np.float64) - t.x0) + t.ny * (np.asarray(Y, dtype=np.float64) - t.y0)) / t.sp)
    return t.n_mid + dev / t.fit


# --- bands (common.band_slice / band_u_slice, bands.band_unit_values) ------------------------

def band_values(b, s_eff, mid):
    n = _unit(b["normal"], (0.0, 0.0, 1.0))
    nx, ny, nz = (float(c) for c in n)
    cx, cy, cz = (float(c) for c in b["center"])
    w = max(float(b["width"]), 0.0)
    f = max(float(b["softness"]), MIN_BAND_SOFTNESS_M)
    h = w / 2.0 + f
    if nz == 1.0:
        gx, gy = -nx, -ny
        a = -nz * s_eff
        o = -(nx * cx + ny * cy + nz * cz + nz * mid * s_eff)
    else:
        d = nz if abs(nz) >= BAND_MIN_NZ else math.copysign(BAND_MIN_NZ, nz if nz != 0.0 else 1.0)
        gx, gy = -nx / d, -ny / d
        a = -(nz / d) * s_eff
        o = -((nx * cx + ny * cy) + nz * cz + nz * mid * s_eff) / d
    f32 = lambda v: float(np.float32(v))
    return {"Strength": f32(float(b["strength"])), "Grad": (f32(gx), f32(gy), 0.0), "A": f32(a), "O": f32(o),
            "M1": f32(-h), "M2": f32(-w / 2.0), "M3": f32(w / 2.0), "M4": f32(h), "T": f32(max(f, BAND_T_MIN_M))}


def band_u_slice(v, m, t):
    k = (float(m[0]) - float(t),) + tuple(float(x) for x in m) + (float(m[3]) + float(t),)
    x = np.asarray(v, dtype=np.float64)
    u = np.zeros_like(x)
    for i in range(5):
        seg = max(k[i + 1] - k[i], BAND_SEG_FLOOR)
        u += 0.2 * np.clip((x - k[i]) / seg, 0.0, 1.0)
    return u


_BAND_LUTS = {}


def band_lut(interp):
    if interp not in _BAND_LUTS:
        blk, wht = (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0, 1.0)
        p = BAND_STOPS_U
        _BAND_LUTS[interp] = Ramp([(p[0], blk), (p[1], wht), (p[2], wht), (p[3], blk)], interp).lut()
    return _BAND_LUTS[interp]


# --- areas (artistic.light_params / light_weight, shading.light_curve etc.) -----------------

def area_curve(a):
    if a["mode"] == "MASK":
        e = min(max(float(a["mask"]["edge"]), 0.0), 1.0)
        return 0.0, 1.0, 1.0 - e
    hard = 1.0 - min(max(float(a["edge"]), 0.0), 1.0)
    return float(DEFAULT_LIGHT_CORE_DETAIL), float(a["falloff"]), hard


def area_params(a, px, py, pw, ph, mid, s_eff):
    basis = []
    for row in a["basis"]:
        v = np.asarray(row, dtype=np.float64)
        n = float(np.linalg.norm(v))
        if n <= 1e-9:
            basis = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
            break
        basis.append(tuple(float(c) for c in (v if abs(n - 1.0) < 1e-6 else v / n)))
    f = MIN_REACH_FACTOR
    return {
        "name": a["name"],
        "pos": SimpleNamespace(x=float(a["position"][0]), y=float(a["position"][1]), z=float(a["position"][2])),
        "radius": np.array([max(abs(float(c)), MIN_LIGHT_RADIUS) for c in a["radius"]], dtype=np.float32),
        "px": px, "py": py, "plane_w": float(pw), "plane_h": float(ph),
        "basis": tuple(basis),
        "reach": (tuple(max(float(c), f) for c in a["reach_plus"]), tuple(max(float(c), f) for c in a["reach_minus"])),
        "is_box": a["shape"] == "BOX",
        "curve": area_curve(a),
        "midlevel": mid,
        "scale": s_eff,
    }


def _reach_pixel_box(px, py, pos, basis, r_plus, r_minus, plane_w, plane_h):
    m = np.array(basis, dtype=np.float64)
    try:
        minv = np.linalg.inv(m)
    except np.linalg.LinAlgError:
        return None
    if not np.all(np.isfinite(minv)):
        return None
    ext = [(-float(r_minus[i]), float(r_plus[i])) for i in range(3)]
    corners = np.array([[a, b, c] for a in ext[0] for b in ext[1] for c in ext[2]], dtype=np.float64)
    d = corners @ minv.T
    x0, x1 = float(pos.x) + d[:, 0].min(), float(pos.x) + d[:, 0].max()
    y0, y1 = float(pos.y) + d[:, 1].min(), float(pos.y) + d[:, 1].max()
    mx = 2.0 * float(plane_w) / max(px.size, 1)
    my = 2.0 * float(plane_h) / max(py.size, 1)
    c0 = int(np.searchsorted(px, x0 - mx, side="left"))
    c1 = int(np.searchsorted(px, x1 + mx, side="right"))
    r0 = int(np.searchsorted(py, y0 - my, side="left"))
    r1 = int(np.searchsorted(py, y1 + my, side="right"))
    return r0, r1, c0, c1


def area_weight(p, f):
    """Per-pixel weight 0..1 of one area over the remapped depth ``f``."""
    h, w_ = f.shape
    pos = p["pos"]
    radius = p["radius"]
    px, py = p["px"], p["py"]
    bx, by, bz = p["basis"]
    fp, fm = p["reach"]
    r_plus = np.maximum(radius * np.asarray(fp, dtype=np.float32), np.float32(MIN_REACH_M))
    r_minus = np.maximum(radius * np.asarray(fm, dtype=np.float32), np.float32(MIN_REACH_M))
    rows, cols = slice(0, h), slice(0, w_)
    box = _reach_pixel_box(px, py, pos, (bx, by, bz), r_plus, r_minus, p["plane_w"], p["plane_h"])
    if box is None:
        pass
    elif box[0] >= box[1] or box[2] >= box[3]:
        return np.zeros((h, w_), np.float32)
    else:
        rows, cols = slice(box[0], box[1]), slice(box[2], box[3])
    full_shape = (h, w_)
    f = f[rows, cols]
    px, py = px[cols], py[rows]
    h, w_ = f.shape
    _core, _fall, _hard = p["curve"]
    core = np.float32(_core)
    falloff = np.float32(_fall)
    hard = np.float32(_hard)
    edge_w = np.float32(max(np.float32(1.0) - hard, np.float32(MASK_EDGE_EPS)))

    def one_minus_s(dv):
        dv = ((np.asarray(dv, np.float32) - hard) / edge_w).astype(np.float32)
        d = np.clip(dv, 0.0, 1.0)
        sm = d * d * (3.0 - 2.0 * d)
        if core == 0.0:
            s_ = sm
        else:
            s_ = sm + core * (d * (2.0 - d) - sm)
        return np.maximum(1.0 - s_, 0.0)

    separable = bool(p["is_box"])
    mid32, scl32 = np.float32(p["midlevel"]), np.float32(p["scale"])

    def weight_rows(fr, X, Y):
        Z = ((fr - mid32) * scl32).astype(np.float32)
        dx = (X - np.float32(pos.x)).astype(np.float32)
        dy = (Y - np.float32(pos.y)).astype(np.float32)
        dz = (Z - np.float32(pos.z)).astype(np.float32)
        p_x = (dx * np.float32(bx[0]) + dy * np.float32(bx[1]) + dz * np.float32(bx[2])).astype(np.float32)
        p_y = (dx * np.float32(by[0]) + dy * np.float32(by[1]) + dz * np.float32(by[2])).astype(np.float32)
        p_z = (dx * np.float32(bz[0]) + dy * np.float32(bz[1]) + dz * np.float32(bz[2])).astype(np.float32)
        vx = np.maximum(p_x / r_plus[0], p_x / (-r_minus[0]))
        vy = np.maximum(p_y / r_plus[1], p_y / (-r_minus[1]))
        vz = np.maximum(p_z / r_plus[2], p_z / (-r_minus[2]))
        if separable:
            g = one_minus_s(vx) * one_minus_s(vy) * one_minus_s(vz)
        else:
            g = one_minus_s(np.sqrt(vx * vx + vy * vy + vz * vz).astype(np.float32))
        return np.power(g, falloff).astype(np.float32)

    def chunk(r0, r1):
        n = r1 - r0
        return weight_rows(f[r0:r1], np.broadcast_to(px[None, :], (n, w_)), np.broadcast_to(py[r0:r1, None], (n, w_)))
    fall = PAR.map_rows(chunk, h)
    if fall.shape != full_shape:
        out = np.zeros(full_shape, np.float32)
        out[rows, cols] = fall
        return out
    return fall.astype(np.float32)


# --- how bands and areas combine (sumchain.py) ------------------------------------------------

def _area_on(lk, a, areas):
    if not a["enabled"]:
        return False
    solo = lk["solo"]
    if not solo or a["mode"] == "MASK" or solo == a["name"]:
        return True
    sol = next((u for u in areas if u["name"] == solo), None)
    return (sol is not None and sol["mode"] == "MERGE" and a["mode"] == "MERGE"
            and sol["group"].strip() == a["group"].strip())


def _band_solo_allows(lk, b):
    return not lk["solo"] or lk["solo"] == BAND_SOLO + b["id"]


def _resolve(lk):
    areas, bands = lk["areas"], lk["bands"]
    terms = [("band", b) for b in bands]
    seen = set()
    for u in areas:
        if u["mode"] == "ADD":
            terms.append(("light", u))
        elif u["mode"] == "MERGE":
            g = u["group"].strip()
            if g and g not in seen:
                seen.add(g)
                terms.append(("group", g, [x for x in areas if x["mode"] == "MERGE" and x["group"].strip() == g]))
    masks, dead = [], []
    ids_b = {b["id"] for b in bands}
    ids_l = {u["id"] for u in areas if u["mode"] != "MASK"}
    groups = {t[1] for t in terms if t[0] == "group"}
    for u in areas:
        if u["mode"] != "MASK":
            continue
        tg = u["mask"]["target"] or ALL_BANDS
        ok = (tg in (ALL_BANDS, ALL_LIGHTS) or (tg.startswith("BAND:") and tg[5:] in ids_b)
              or (tg.startswith("LIGHT:") and tg[6:] in ids_l) or (tg.startswith("GROUP:") and tg[6:] in groups))
        (masks if ok else dead).append((u, tg))
    return {"terms": terms, "masks": masks, "dead": dead}


def _masks_for(plan, kind, key):
    out = []
    for m, tg in plan["masks"]:
        if kind == "band" and (tg == ALL_BANDS or tg == f"BAND:{key}"):
            out.append(m)
        elif kind == "light" and (tg == ALL_LIGHTS or tg == f"LIGHT:{key}"):
            out.append(m)
        elif kind == "member" and tg == f"LIGHT:{key}":
            out.append(m)
        elif kind == "group" and (tg == ALL_LIGHTS or tg == f"GROUP:{key}"):
            out.append(m)
    return out


def _soft_params(blend):
    b = min(max(float(blend), 0.0), 1.0)
    t = np.float32(1.0 if b > 0.0 else 0.0)
    p = np.float32(4.0 / b) if b > 0.0 else np.float32(4.0)
    return t, p, np.float32(np.float32(1.0) / p)


def _keep_constant(strength, enabled):
    return np.float32(1.0) - np.float32(strength) * np.float32(enabled)


def sum_prepare(lk, shape, px, py, pw, ph, mid, s_eff):
    plan = _resolve(lk)
    areas = lk["areas"]
    lights = {}

    def lp_of(u):
        if u["id"] not in lights:
            lights[u["id"]] = area_params(u, px, py, pw, ph, mid, s_eff) if _area_on(lk, u, areas) else None
        return u["id"]

    def mask_recs(masks):
        out = []
        for m in masks:
            keeps = m["mask"]["side"] == "KEEP"
            out.append({"w": lp_of(m), "strength": np.float32(m["mask"]["strength"]), "keeps": keeps,
                        "b": (_keep_constant(m["mask"]["strength"], 1.0 if _area_on(lk, m, areas) else 0.0)
                              if keeps else None)})
        return out

    recs = []
    for t in plan["terms"]:
        if t[0] == "band":
            b = t[1]
            if not b["enabled"] or not _band_solo_allows(lk, b):
                continue
            recs.append(("band", {"vals": band_values(b, s_eff, mid), "lut": band_lut(b["interpolation"]),
                                  "masks": mask_recs(_masks_for(plan, "band", b["id"]))}))
        elif t[0] == "light":
            u = t[1]
            if not _area_on(lk, u, areas):
                continue
            recs.append(("light", {"w": lp_of(u), "strength": np.float32(float(u["strength"])),
                                   "masks": mask_recs(_masks_for(plan, "light", u["id"]))}))
        else:
            gname, members = t[1], t[2]
            if not [mb for mb in members if _area_on(lk, mb, areas)]:
                continue
            rec = lk["groups"].get(gname)
            st, sp, sinv = _soft_params(rec["blend"] if rec is not None else 0.0)
            gs = np.float32(float(rec["strength"])) if rec is not None else np.float32(1.0)
            recs.append(("group", {"members": [(lp_of(mb), mask_recs(_masks_for(plan, "member", mb["id"])))
                                               for mb in members],
                                   "st": st, "sp": sp, "sinv": sinv, "gs": gs,
                                   "masks": mask_recs(_masks_for(plan, "group", gname))}))
    return {"recs": recs, "lights": lights, "px": px, "py": py, "dead": [m["name"] for m, _ in plan["dead"]]}


def sum_compute(prep, f, base, extra=()):
    """The bands and areas summed over ``base``; ``extra``: precomputed terms (vathopy's shape
    focuses, strength x weight) added to the same sum."""
    w_cache = {}

    def w_of(key):
        if key not in w_cache:
            p = prep["lights"][key]
            w_cache[key] = np.zeros(f.shape, np.float32) if p is None else area_weight(p, f)
        return w_cache[key]

    def masked(v, masks, r0, r1):
        for m in masks:
            w = w_cache[m["w"]][r0:r1]
            c = (m["strength"] * w).astype(np.float32)
            if m["keeps"]:
                v = (v * (m["b"] + c).astype(np.float32)).astype(np.float32)
            else:
                v = (v * (np.float32(1.0) - c).astype(np.float32)).astype(np.float32)
        return v

    def prefetch(keys):
        for k in keys:
            w_of(k)

    h_, w_ = f.shape
    px, py = prep["px"], prep["py"]
    acc = None
    for kind, r in prep["recs"]:
        if kind == "band":
            vals = r["vals"]
            gx, gy, _gz = vals["Grad"]
            pos_, vals_ = r["lut"]
            m_ = (vals["M1"], vals["M2"], vals["M3"], vals["M4"])
            prefetch(m["w"] for m in r["masks"])

            def chunk(r0, r1, gx=gx, gy=gy, vals=vals, m_=m_, pos_=pos_, vals_=vals_, masks=r["masks"]):
                v = (np.float64(gx) * px[None, :].astype(np.float64)
                     + np.float64(gy) * py[r0:r1, None].astype(np.float64)
                     + np.float64(vals["A"]) * f[r0:r1].astype(np.float64) - np.float64(vals["O"]))
                u = band_u_slice(v, m_, vals["T"]).astype(np.float32)
                rr = apply_lut(u, (pos_, vals_), True)
                return masked((rr * np.float32(vals["Strength"])).astype(np.float32), masks, r0, r1)
        elif kind == "light":
            prefetch([r["w"]] + [m["w"] for m in r["masks"]])

            def chunk(r0, r1, r=r):
                c = (r["strength"] * w_cache[r["w"]][r0:r1]).astype(np.float32)
                return masked(c, r["masks"], r0, r1)
        else:
            if not r["members"]:
                continue
            prefetch([k for k, mm in r["members"]] + [m["w"] for k, mm in r["members"] for m in mm]
                     + [m["w"] for m in r["masks"]])

            def chunk(r0, r1, r=r):
                a = None
                wvs = []
                for key, mmasks in r["members"]:
                    wv = masked(w_cache[key][r0:r1], mmasks, r0, r1)
                    wvs.append(wv)
                    a = wv if a is None else np.maximum(a, wv)
                st, sp, sinv = r["st"], r["sp"], r["sinv"]
                if len(wvs) >= 2 and st > 0:
                    ps = None
                    for wv in wvs:
                        pw_ = np.power(wv, sp).astype(np.float32)
                        ps = pw_ if ps is None else (ps + pw_).astype(np.float32)
                    a = np.power(ps, sinv).astype(np.float32)
                a = masked(a, r["masks"], r0, r1)
                return (a * r["gs"]).astype(np.float32)
        term = PAR.map_rows(chunk, h_)
        acc = term if acc is None else np.add(acc, term, out=acc)
    for term in extra:
        acc = term.copy() if acc is None else np.add(acc, term, out=acc)
    if acc is None:
        return base

    def final_chunk(r0, r1):
        p = acc[r0:r1]
        p = np.minimum(np.maximum(p, np.float32(-1.0)), np.float32(1.0))
        tp = np.maximum(p, np.float32(0.0))[..., None]
        tn = np.maximum(p * np.float32(-1.0), np.float32(0.0))[..., None]
        b_ = base[r0:r1]
        out = (b_ + tp * (np.float32(1.0) - b_)).astype(np.float32)
        return (out + tn * (np.float32(0.0) - out)).astype(np.float32)
    return PAR.map_rows(final_chunk, h_)


# --- the whole picture (artistic.artistic_prepare / artistic_compute_steps) -----------------

def remapped(depth, lk):
    """The depth after Flip and the depth controls (the relief's height field, in depth units)."""
    d = np.float32(1.0) - depth if lk["flip"] else depth
    cs = lk["controls"]
    args = dict(centers=[float(c["center"]) for c in cs], offsets=[float(c["offset"]) for c in cs],
                feathers=[float(c["feather"]) for c in cs],
                mins=[None if c["from_min"] is None else float(c["from_min"]) for c in cs],
                maxs=[None if c["from_max"] is None else float(c["from_max"]) for c in cs])
    return np.asarray(PAR.map_rows(lambda r0, r1: remap.transfer(d[r0:r1], **args), d.shape[0]), dtype=np.float32)


def _window(f, lk):
    if lk["window"] == "auto":
        lo, hi = float(f.min()), float(f.max())
    else:
        lo, hi = float(lk["window"][0]), float(lk["window"][1])
    return lo, (hi if hi - lo >= 1e-6 else lo + 1e-6)


def focus_depth_at(depth, lk, x, y, r=2):
    """The focus depth (0 = far .. 1 = near over the ramp window) under pixel (x, y), top-down
    coordinates; the median of a (2r+1)^2 patch, so one noisy pixel can't throw it."""
    f = remapped(depth, lk)
    lo, hi = _window(f, lk)
    h, w = f.shape
    x, row = min(max(int(round(x)), 0), w - 1), min(max(h - 1 - int(round(y)), 0), h - 1)
    v = float(np.median(f[max(row - r, 0):row + r + 1, max(x - r, 0):x + r + 1]))
    return min(max((v - lo) / (hi - lo), 0.0), 1.0)


def focus_bands(focus, lo, hi, s_eff, mid, pw, ph):
    """The look's ``focus`` entries as add-on bands through the relief point at that depth: flat
    (normal 0,0,1) unless leaned, the normal tipped by ``lean`` towards ``angle``. Width and
    softness are scaled from fractions of the window to metres; ``pw`` x ``ph`` is the plane."""
    span = (hi - lo) * s_eff
    out = []
    for i, fo in enumerate(focus):
        if is_shape(fo):
            continue                                     # shapes are weighed by focus_shape_weight
        z = (lo + float(fo["depth"]) * (hi - lo) - mid) * s_eff
        fx, fy = fo.get("pivot") or (0.5, 0.5)
        a, le = math.radians(float(fo.get("angle", 0.0))), math.radians(float(fo.get("lean", 0.0)))
        normal = [math.sin(le) * math.cos(a), math.sin(le) * math.sin(a), math.cos(le)] if le else [0.0, 0.0, 1.0]
        # The add-on measures a band's width along depth, so a leaned band's real thickness is width x cos(lean)
        # (held from 85 degrees on): divide it back out, so Width stays the thickness however far it leans.
        thick = 1.0 / max(math.cos(le), BAND_MIN_NZ)
        out.append(_merge(DEFAULT_BAND, {"id": f"focus{i + 1}", "name": f"focus{i + 1}", "enabled": bool(fo["enabled"]),
                                         "strength": float(fo["strength"]), "normal": normal,
                                         "center": [(float(fx) - 0.5) * pw, (0.5 - float(fy)) * ph, z],
                                         "width": float(fo["width"]) * span * thick,
                                         "softness": float(fo["softness"]) * span * thick}, "focus"))
    return out


def focus_reach_areas(focus, lo, hi, s_eff, mid, pw, ph):
    """For each focus with a reach: the add-on mask area (ellipsoid, Keep) that confines its band to
    an oval around the pivot at the focus depth; None for unlimited focuses. Its depth radius spans
    the band and its softness, so the band alone decides depth and the area only the place."""
    span = (hi - lo) * s_eff
    out = []
    for i, fo in enumerate(focus):
        rw, rh = float(fo.get("reach_w", 0.0)), float(fo.get("reach_h", 0.0))
        if rw <= 0.0 and rh <= 0.0:
            out.append(None)
            continue
        rw, rh = rw or rh * ph / pw, rh or rw * pw / ph                  # one given: a circle
        z = (lo + float(fo["depth"]) * (hi - lo) - mid) * s_eff
        fx, fy = fo.get("pivot") or (0.5, 0.5)
        # a band: deep enough for the band and its softness; a shape: every depth (it has its own extent)
        rz = max(span * 4.0 if is_shape(fo) else (float(fo["width"]) / 2 + float(fo["softness"])) * span * 4.0, 1e-3)
        out.append(_merge(DEFAULT_AREA, {
            "id": f"focusreach{i + 1}", "name": f"focusreach{i + 1}", "enabled": bool(fo["enabled"]),
            "mode": "MASK", "shape": "ELLIPSOID",
            "position": [(float(fx) - 0.5) * pw, (0.5 - float(fy)) * ph, z], "radius": [rw * pw, rh * ph, rz],
            "mask": {"strength": 1.0, "side": "KEEP", "edge": float(fo.get("reach_edge", 0.5)),
                     "target": f"BAND:focus{i + 1}"}}, "focus"))
    return out


# --- shape focuses (vathopy only) ------------------------------------------------------------
#
# A focus can be a 3D shape instead of a plane: a sphere/ellipsoid, box, cylinder or (double) cone,
# placed in the relief at the picked point and depth. Space is normalised so shapes look like what
# they are: x and y in fractions of the picture's width (centred, y up), z = depth as a fraction of
# the window (0 far, 1 near) x the look's depth_scale (how deep the scene is relative to its width).
# Shell: the band's profile around the shape's surface (thickness = width, edge = softness, in
# fractions of the picture width): a focal plane bent into the shape. Solid: everything inside,
# fading out over softness.

SHAPES = ("plane", "ellipsoid", "box", "cylinder", "cone")


def is_shape(fo):
    return fo.get("shape", "plane") != "plane"


def shape_axes(lean, angle):
    """An orthonormal frame (x', y', z') from a focus's lean/angle: z' is the band normal's direction
    (towards the viewer when lean is 0); at lean 0, angle turns x'/y' in the picture's plane."""
    le, a = math.radians(lean), math.radians(angle)
    z = np.array([math.sin(le) * math.cos(a), math.sin(le) * math.sin(a), math.cos(le)])
    x = np.array([math.cos(le) * math.cos(a), math.cos(le) * math.sin(a), -math.sin(le)])
    return x, np.cross(z, x), z


def rod_axes(tilt, angle):
    """A cylinder's frame: its axis z' stands upright in the picture at tilt 0 / angle 0; angle turns
    it in the picture (counter-clockwise, 90 = lying left-right), tilt leans it towards the viewer
    (90 = pointing straight at the viewer)."""
    t, a = math.radians(tilt), math.radians(angle)
    z = np.array([-math.sin(a) * math.cos(t), math.cos(a) * math.cos(t), math.sin(t)])
    x = np.array([math.cos(a), math.sin(a), 0.0])
    return x, np.cross(z, x), z


def shape_distance(q, fo):
    """Signed distance (negative inside) of local points ``q`` (..., 3) to the focus's shape."""
    qx, qy, qz = q[..., 0], q[..., 1], q[..., 2]
    sx, sy, sz = (max(float(v), 1e-4) for v in fo["size"])
    kind = fo["shape"]
    if kind == "ellipsoid":                              # a scaled sphere: exact for a sphere
        k = np.sqrt((qx / sx) ** 2 + (qy / sy) ** 2 + (qz / sz) ** 2)
        return (k - 1.0) * min(sx, sy, sz)
    if kind == "box":                                    # size = half extents
        dx, dy, dz = np.abs(qx) - sx, np.abs(qy) - sy, np.abs(qz) - sz
        out = np.sqrt(np.maximum(dx, 0) ** 2 + np.maximum(dy, 0) ** 2 + np.maximum(dz, 0) ** 2)
        return out + np.minimum(np.maximum(np.maximum(dx, dy), dz), 0.0)
    if kind == "cylinder":                               # radius sx around its axis z'; length 0 = endless
        radial = np.sqrt(qx * qx + qy * qy) - sx
        half = float(fo["length"]) / 2.0
        if half <= 0:
            return radial
        along = np.abs(qz) - half
        return (np.sqrt(np.maximum(radial, 0) ** 2 + np.maximum(along, 0) ** 2)
                + np.minimum(np.maximum(radial, along), 0.0))
    if kind == "cone":                                   # double cone, apex at the point, axis z'
        t = math.radians(min(max(float(fo["cone_angle"]), 0.5), 89.5))
        return np.sqrt(qx * qx + qy * qy) * math.cos(t) - np.abs(qz) * math.sin(t)
    raise ValueError(f"unknown focus shape {kind!r}")


def focus_shape_weight(f, fo, reach, lo, hi, pw, ph, px, py, mid, s_eff, depth_scale):
    """A shape focus's weight 0..1 over the remapped depth ``f`` (rows bottom-up, like px/py)."""
    h, w = f.shape
    fx, fy = fo.get("pivot") or (0.5, 0.5)
    ds = float(depth_scale)
    centre = np.array([float(fx) - 0.5, (0.5 - float(fy)) * ph / pw, float(fo["depth"]) * ds])
    frame = rod_axes if fo["shape"] == "cylinder" else shape_axes        # a rod stands upright at tilt 0
    axes = np.stack(frame(float(fo.get("lean", 0.0)), float(fo.get("angle", 0.0))))
    X = (px / np.float32(pw)).astype(np.float64)
    width, soft = max(float(fo["width"]), 0.0), max(float(fo["softness"]), MIN_BAND_SOFTNESS_M)
    lut = band_lut("LINEAR")
    m = (-(width / 2 + soft), -width / 2, width / 2, width / 2 + soft)
    span = max(hi - lo, 1e-12)

    def chunk(r0, r1):
        Y = (py[r0:r1] / np.float32(pw)).astype(np.float64)
        Z = (f[r0:r1].astype(np.float64) - lo) / span * ds
        P = np.stack(np.broadcast_arrays(X[None, :], Y[:, None], Z), axis=-1) - centre
        d = shape_distance(P @ axes.T, fo)
        if fo["fill"] == "solid":
            u = np.clip(d / soft, 0.0, 1.0)
            return (1.0 - u * u * (3.0 - 2.0 * u)).astype(np.float32)
        return apply_lut(band_u_slice(d, m, max(soft, BAND_T_MIN_M)).astype(np.float32), lut, True)
    wt = PAR.map_rows(chunk, h)
    if reach is not None:
        wt = (wt * area_weight(area_params(reach, px, py, pw, ph, mid, s_eff), f)).astype(np.float32)
    return wt


def cut_weight(f, cut, lo, hi, pw, ph, px, py, mid, s_eff, depth_scale, px_scale=1):
    """How much of each pixel the deletion slab removes, 0..1: everything farther than cut["depth"],
    and everything nearer than 1 - cut["near"] (fading back over its softness), less what its keep
    shapes hold on to; plus the parts picked for removal (cut["remove"], grown like selections)."""
    c, n, soft = float(cut["depth"]), float(cut.get("near", 0.0)), float(cut["softness"])
    span = max(hi - lo, 1e-12)

    def side(t, edge):
        """Removed beyond ``edge`` (t measured so that the removed side is below it)."""
        if edge >= 1.0:
            return np.ones(t.shape, np.float32)
        if edge <= 0.0:
            return np.zeros(t.shape, np.float32)
        if soft <= 0.0:
            return (t < edge).astype(np.float32)
        u = np.clip((t - edge) / soft, 0.0, 1.0)
        return (1.0 - u * u * (3.0 - 2.0 * u)).astype(np.float32)

    def chunk(r0, r1):
        t = (f[r0:r1].astype(np.float64) - lo) / span                  # 0 far .. 1 near
        return np.maximum(side(t, c), side(1.0 - t, n))
    gone = PAR.map_rows(chunk, f.shape[0])
    for k in cut["keep"]:
        if k["enabled"]:
            fo = {**k, "fill": "solid", "width": 0.0}
            gone *= np.float32(1.0) - focus_shape_weight(f, fo, None, lo, hi, pw, ph, px, py, mid, s_eff, depth_scale)
    parts = [r for r in cut.get("remove", ()) if r["enabled"]]
    if parts:
        t = np.clip((f - np.float32(lo)) / np.float32(hi - lo), 0.0, 1.0).astype(np.float32)
        stop = gone > 0.5                                # only what's still showing: don't spread through the cut
        for r in parts:
            gone = np.maximum(gone, selection_mask(t, r, px_scale, stop))
    return gone


def apply_cut(out, f, cut, lo, hi, pw, ph, px, py, mid, s_eff, depth_scale, px_scale=1):
    """The picture with the deletion slab's pixels (and removed parts) turned black or white (alpha kept)."""
    if float(cut["depth"]) <= 0.0 and float(cut.get("near", 0.0)) <= 0.0 and not any(r["enabled"] for r in cut.get("remove", ())):
        return out
    g = cut_weight(f, cut, lo, hi, pw, ph, px, py, mid, s_eff, depth_scale, px_scale)[..., None]
    col = np.float32(1.0 if cut["fill"] == "white" else 0.0)
    rgb = out[..., :3] * (np.float32(1.0) - g) + col * g
    return np.concatenate([rgb, out[..., 3:]], axis=-1).astype(np.float32) if out.shape[-1] > 3 else rgb.astype(np.float32)


def focus_weights(depth, look):
    """Each focus's band weight (0..1, 1 = fully in focus) at the depth map's resolution, rows
    bottom-up: the band maths of the result in one pass over the remapped depth, without
    rendering the look. Switched-off focuses give None. ``depth`` is used as given (no
    smooth_depth); "at" focuses must already be resolved to a depth."""
    lk = normalise(look)
    f = remapped(np.ascontiguousarray(depth, dtype=np.float32), lk)
    lo, hi = _window(f, lk)
    s_eff, mid = max(float(lk["plane"]["scale"]), MIN_SCALE), float(lk["plane"]["midlevel"])
    pw, ph = plane_dims(lk, f.shape)
    px, py = plane_pixel_xy(pw, ph, *f.shape)
    out = []
    reaches = focus_reach_areas(lk["focus"], lo, hi, s_eff, mid, pw, ph)
    bands = iter(focus_bands(lk["focus"], lo, hi, s_eff, mid, pw, ph))
    for fo, ra in zip(lk["focus"], reaches):
        if is_shape(fo):
            out.append(focus_shape_weight(f, fo, ra, lo, hi, pw, ph, px, py, mid, s_eff, lk["depth_scale"])
                       if fo["enabled"] else None)
            continue
        b = next(bands)
        if not b["enabled"]:
            out.append(None)
            continue
        vals = band_values(b, s_eff, mid)
        gx, gy, _ = vals["Grad"]
        v = (np.float64(gx) * px[None, :].astype(np.float64) + np.float64(gy) * py[:, None].astype(np.float64)
             + np.float64(vals["A"]) * f.astype(np.float64) - np.float64(vals["O"]))
        u = band_u_slice(v, (vals["M1"], vals["M2"], vals["M3"], vals["M4"]), vals["T"]).astype(np.float32)
        wt = apply_lut(u, band_lut(b["interpolation"]), True)
        if ra is not None:                               # Keep mask at strength 1: band x area weight
            wt = (wt * area_weight(area_params(ra, px, py, pw, ph, mid, s_eff), f)).astype(np.float32)
        out.append(wt)
    return out


# --- selections (vathopy only) ----------------------------------------------------------------

SELECT_WORK_PX = 1536  # selections grow on the depth at most this many px across: previews and full renders match
FLOOR_SLOPE = 0.4      # "floors": depth rising towards the viewer faster than this per picture height, going down
SLIVER_PX = 12            # removed parts take in thin slivers of what's left within this many px (at the work size)
SELECT_HOLE_AREA = 0.002  # unselected pockets smaller than this share of the picture, inside a selection, are filled


def selection_mask(t, sel, px_scale=1, stop=None):
    """A selection's weight 0..1 over ``t`` (the depth as 0 far .. 1 near over the window, rows
    bottom-up): a flood fill from its point over 8-connected neighbours that differ by at most
    ``step`` (so it stops at depth edges), within ``range`` of the point's depth when range > 0,
    with ``floors`` stopping at surfaces that come nearer going down the picture (the ground a
    person stands on), small pockets inside filled, widened by ``grow`` px and feathered by
    ``softness`` px (output px). It doesn't spread where ``stop`` (a bool mask like ``t``) is set."""
    import cv2
    h, w = t.shape
    k = min(1.0, SELECT_WORK_PX / max(h, w))
    small = np.ascontiguousarray(cv2.resize(t, (max(2, round(w * k)), max(2, round(h * k))), interpolation=cv2.INTER_AREA)
                                 if k < 1.0 else t, dtype=np.float32)
    sh, sw = small.shape
    x = min(max(int(sel["point"][0] * sw), 0), sw - 1)
    row = sh - 1 - min(max(int(sel["point"][1] * sh), 0), sh - 1)          # point is top-down; rows bottom-up
    seed = float(np.median(small[max(row - 2, 0):row + 3, max(x - 2, 0):x + 3]))
    block = np.zeros((sh + 2, sw + 2), np.uint8)                           # floodFill's mask: non-zero = don't fill
    if sel["range"] > 0:
        block[1:-1, 1:-1][np.abs(small - seed) > sel["range"]] = 1
    if sel["floors"]:
        ts = cv2.medianBlur(small, 5)                                       # quiet, but edges stay edges
        gy = -cv2.Sobel(ts, cv2.CV_32F, 0, 1, ksize=3) / 8.0               # rows bottom-up: going down the picture
        gx = cv2.Sobel(ts, cv2.CV_32F, 1, 0, ksize=3) / 8.0
        block[1:-1, 1:-1][(gy > FLOOR_SLOPE / sh) & (gy > 2.0 * np.abs(gx))] = 1
    if stop is not None:                                 # the share of each work pixel that's still showing
        shown = (cv2.resize((~stop).astype(np.float32), (sw, sh), interpolation=cv2.INTER_AREA)
                 if stop.shape != (sh, sw) else (~stop).astype(np.float32))
        block[1:-1, 1:-1][shown < 0.5] = 1
    block[row + 1, x + 1] = 0
    step = float(sel["step"])
    cv2.floodFill(small.copy(), block, (x, row), 0, loDiff=step, upDiff=step,
                  flags=8 | cv2.FLOODFILL_MASK_ONLY | (2 << 8))
    m = (block[1:-1, 1:-1] == 2).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(1 - m, connectivity=4)   # unselected pockets
    small_holes = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] < SELECT_HOLE_AREA * sh * sw
                   and stats[i, cv2.CC_STAT_LEFT] > 0 and stats[i, cv2.CC_STAT_TOP] > 0
                   and stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH] < sw
                   and stats[i, cv2.CC_STAT_TOP] + stats[i, cv2.CC_STAT_HEIGHT] < sh]
    if small_holes:
        m[np.isin(lab, small_holes)] = 1
    if stop is not None:                                 # slivers left between it and what's stopped it (pixels that
        rest = ((m == 0) & (shown > 0.0)).astype(np.uint8)     # flicker either side of a cut): taken in if thin and close
        el = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        thin = (rest > 0) & (cv2.morphologyEx(rest, cv2.MORPH_OPEN, el) == 0)
        near = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * SLIVER_PX + 1, 2 * SLIVER_PX + 1))) > 0
        m[thin & near] = 1
    m = cv2.resize(m.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR) if (sh, sw) != (h, w) else m.astype(np.float32)
    m = (m >= 0.5).astype(np.float32)
    g = sel["grow"] * px_scale
    if g >= 0.5:
        r = int(round(g))
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    soft = sel["softness"] * px_scale
    if soft > 0:
        m = cv2.GaussianBlur(m, (0, 0), soft)
    if sel["invert"]:
        m = np.float32(1.0) - m
    return np.clip(m, 0.0, 1.0).astype(np.float32)


def selection_masks(f, lk, lo, hi):
    """Each switched-on selection's (selection, mask) over the remapped depth ``f``."""
    sels = [s for s in lk["selections"] if s["enabled"]]
    if not sels:
        return []
    t = np.clip((f - np.float32(lo)) / np.float32(hi - lo), 0.0, 1.0).astype(np.float32)
    return [(s, selection_mask(t, s, lk.get("_px_scale", 1))) for s in sels]


# --- view shift (vathopy only) ---------------------------------------------------------------

VIEW_EDGE_PX = 8.0     # a pixel whose neighbourhood's depths would move further apart than this (px) is on a depth edge
VIEW_EDGE_SIZE = 0.01  # that neighbourhood, as a share of the picture's width (the depth model's soft edges are about this wide)
VIEW_VOTE_SMOOTH = 0.1  # gaps are told apart (background or a hole in a surface) by a vote over this share of the largest move
VIEW_FILL_TOL = 0.05   # pixels beside a gap more than this (0-1 of the depth) nearer than the farthest there aren't background


def _push_pull(a, wt):
    """``a`` (h, w, c) filled in where the weights ``wt`` (h, w, 0..1) are low, from the weighted
    average of what's around at ever coarser scales (push-pull): smooth, with no streaks."""
    import cv2

    def down(x):
        return cv2.pyrDown(x).reshape((x.shape[0] + 1) // 2, (x.shape[1] + 1) // 2, *x.shape[2:])
    num, den = [a * wt[..., None]], [wt]
    while min(den[-1].shape) > 2:
        num.append(down(num[-1]))
        den.append(down(den[-1]))
    est = num[-1] / np.maximum(den[-1], 1e-12)[..., None]
    for i in range(len(den) - 2, -1, -1):
        h, w = den[i].shape
        up = cv2.resize(est, (w, h), interpolation=cv2.INTER_LINEAR).reshape(h, w, -1)
        k = np.clip(den[i] * 4.0, 0.0, 1.0)[..., None]                            # trust the finer level where it had data
        est = k * num[i] / np.maximum(den[i], 1e-12)[..., None] + (1.0 - k) * up
    return est.astype(np.float32)


def _gap_sides(z, valid, axis):
    """For each pixel, the nearest valid pixels before and after it along ``axis``: their depths
    (inf where there's none) and the length of the run of gaps between them."""
    if axis == 0:
        zl, zr, run = _gap_sides(z.T, valid.T, 1)
        return zl.T, zr.T, run.T
    h, w = z.shape
    cols = np.arange(w)[None, :]
    left = np.maximum.accumulate(np.where(valid, cols, -1), axis=1)
    right = np.minimum.accumulate(np.where(valid, cols, w)[:, ::-1], axis=1)[:, ::-1]
    zl = np.where(left >= 0, np.take_along_axis(z, np.clip(left, 0, w - 1), axis=1), np.inf)
    zr = np.where(right < w, np.take_along_axis(z, np.clip(right, 0, w - 1), axis=1), np.inf)
    return zl, zr, (right - left - 1).astype(np.float32)


def _fill_far(z, valid, extra, reach, axis):
    """Fill the gaps (not ``valid``) in ``z`` (and the arrays in ``extra``, alike). A gap whose sides
    (along the move, ``axis``) differ in depth by about as much as it is wide is where a near thing
    moved off the background: it's filled only from the farthest pixels round it (anything nearer
    than the farthest within about the widest such gap is left out), so it gets the background, smoothly. Other gaps (holes in a
    surface) are filled from everything round them. Returns the filled copies."""
    import cv2
    h, w = z.shape
    if valid.all() or not valid.any():
        return z.copy(), [e.copy() for e in extra]
    stack = np.dstack([z[..., None]] + [e.reshape(h, w, -1) for e in extra]).astype(np.float32)
    hole = ~valid
    zl, zr, run = _gap_sides(z, valid, axis)
    jump = np.where(np.isfinite(zl) & np.isfinite(zr), np.abs(zl - zr) * reach, np.inf)
    vote = (hole & (jump > 0.5 * run)).astype(np.float32)                       # uncovered background, row by row:
    sigma = max(1.0, VIEW_VOTE_SMOOTH * reach)                                   # a neighbourhood vote, so it's
    m = hole.astype(np.float32)                                                  # the same from row to row, blending
    share = cv2.GaussianBlur(vote, (0, 0), sigma) / np.maximum(cv2.GaussianBlur(m, (0, 0), sigma), 1e-6)   # the two fills
    b = np.clip((share - 0.3) / 0.4, 0.0, 1.0)[..., None]                       # 0: a hole in a surface, 1: background
    out = stack.copy()
    surface = hole & (b[..., 0] < 1.0)
    behind = hole & (b[..., 0] > 0.0)
    if surface.any():
        out[surface] = _push_pull(stack, valid.astype(np.float32))[surface]
    if behind.any():
        q = 4                                                                    # at a quarter of the size: quick
        zq = cv2.resize(np.where(valid, z, np.float32(1e9)).astype(np.float32), ((w + q - 1) // q, (h + q - 1) // q),
                        interpolation=cv2.INTER_NEAREST)
        r = max(3, int(2 * reach / q) | 1)                                       # a gap is at most ``reach`` wide
        far = cv2.erode(zq, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (r, r)))
        far = cv2.resize(cv2.GaussianBlur(far, (0, 0), r / 4), (w, h), interpolation=cv2.INTER_LINEAR)
        src = valid & (z <= far + np.float32(VIEW_FILL_TOL))                  # the farthest things about
        out[behind] = (b * _push_pull(stack, src.astype(np.float32)) + (1.0 - b) * out)[behind]
    return np.ascontiguousarray(out[..., 0]), [np.ascontiguousarray(out[..., 1:]).reshape(e.shape) for e in extra]


def _zoom_box(tx, ty, w, h):
    """The biggest box with the picture's proportions, centred as far as it can be, that leaves out
    the strips along the borders the moved view uncovers (no data comes from outside the frame):
    ``tx``/``ty`` are where each pixel lands."""
    tx, ty = np.broadcast_to(tx, (h, w)), np.broadcast_to(ty, (h, w))
    left = int(max(0, np.max(np.min(tx, axis=1))))                              # per row: the leftmost pixel's place
    right = int(max(0, w - 1 - np.min(np.max(tx, axis=1))))
    top = int(max(0, np.max(np.min(ty, axis=0))))
    bottom = int(max(0, h - 1 - np.min(np.max(ty, axis=0))))
    s = max(w / max(w - left - right, 1), h / max(h - top - bottom, 1))
    bw, bh = w / s, h / s
    x0 = min(max(left, (w - bw) / 2), w - right - bw)
    y0 = min(max(top, (h - bh) / 2), h - bottom - bh)
    return x0, y0, bw, bh


def view_shift(depth, vs, photo=None):
    """The depth map (rows bottom-up, near = bright) as seen from a moved camera (a look's
    ``view_shift``), and the photo (sRGB, rows bottom-up, the same size) moved with it. Each pixel is
    splatted to its new place, nearest on top. The soft in-between pixels along depth edges are left
    out (they would smear across the gap), gaps are filled smoothly from the background beside them,
    and with ``edges`` "zoom" the picture is enlarged just enough that the strips the move uncovers
    along the borders fall outside it ("fill" fills them like the gaps)."""
    if vs is None or (vs["x"] == 0.0 and vs["y"] == 0.0) or vs["strength"] == 0.0:
        return depth, photo
    import cv2
    d = np.ascontiguousarray(depth[::-1], dtype=np.float32)                      # top-down from here
    h, w = d.shape
    k = float(vs["strength"]) * w
    reach = k * float(np.hypot(vs["x"], vs["y"]))                                # px moved per unit of depth
    off = d - np.float32(vs["pivot"])
    tx = np.rint(np.arange(w, dtype=np.float32)[None, :] - np.float32(vs["x"] * k) * off).astype(np.int64)
    ty = np.rint(np.arange(h, dtype=np.float32)[:, None] + np.float32(vs["y"] * k) * off).astype(np.int64)
    n = max(5, int(VIEW_EDGE_SIZE * w) | 1)
    el = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (n, n))
    hi_, lo_ = cv2.dilate(d, el), cv2.erode(d, el)
    edge = ((hi_ - lo_) * reach > VIEW_EDGE_PX) & ((hi_ - d) * reach > VIEW_EDGE_PX / 2)   # on an edge, not its near side
    inside = ((tx >= 0) & (tx < w) & (ty >= 0) & (ty < h) & ~edge).ravel()
    src = np.flatnonzero(inside)
    tgt = (ty * w + tx).ravel()[inside]
    dv = d.ravel()[src]
    order = np.lexsort((dv, tgt))                                                # by target, nearest last
    ts = tgt[order]
    win = order[np.r_[ts[1:] != ts[:-1], True]]                                  # the nearest pixel at each target
    z = np.zeros(h * w, np.float32)
    valid = np.zeros(h * w, bool)
    z[tgt[win]], valid[tgt[win]] = dv[win], True
    z, valid = z.reshape(h, w), valid.reshape(h, w)
    extra = []
    if photo is not None:
        p = np.ascontiguousarray(photo[::-1], dtype=np.float32).reshape(h * w, -1)
        q = np.zeros_like(p)
        q[tgt[win]] = p[src[win]]
        extra = [q.reshape(h, w, -1)]
    z, extra = _fill_far(z, valid, extra, reach, 1 if abs(vs["x"]) >= abs(vs["y"]) else 0)
    if vs.get("edges", "zoom") == "zoom":
        x0, y0, bw, bh = _zoom_box(tx, ty, w, h)
        if bw < w - 0.5 or bh < h - 0.5:
            sx, sy = bw / w, bh / h                                              # output px -> source px
            M = np.float32([[sx, 0, x0 + 0.5 * sx - 0.5], [0, sy, y0 + 0.5 * sy - 0.5]])

            def warp(a):
                return cv2.warpAffine(np.ascontiguousarray(a), M, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                      borderMode=cv2.BORDER_REPLICATE)
            z = warp(z)
            extra = [warp(e).reshape(h, w, -1) for e in extra]
    return np.ascontiguousarray(z[::-1]), (np.ascontiguousarray(extra[0][::-1]) if extra else None)


def apply_overlay(out, photo, ov, sel_mask=None):
    """The photo (sRGB 0..1, rows bottom-up, the picture's size) blended over ``out`` (linear) as the
    look's ``overlay`` says, in sRGB terms as an image editor would; ``sel_mask`` is the selections' union."""
    from .io import srgb_eotf, srgb_oetf
    a = np.float32(ov["opacity"])
    if ov["where"] != "all":
        m = sel_mask if sel_mask is not None else np.zeros(out.shape[:2], np.float32)
        a = a * (m if ov["where"] == "selections" else np.float32(1.0) - m)[..., None]
    p = np.float32(1.0) - photo if ov["invert"] else photo
    if ov["grey"]:
        p = np.repeat((p @ np.array([0.2126, 0.7152, 0.0722], np.float32))[..., None], 3, axis=-1)
    o = srgb_oetf(out[..., :3]).astype(np.float32)
    mode = ov["mode"]
    blend = (p if mode == "normal" else o * p if mode == "multiply" else 1.0 - (1.0 - o) * (1.0 - p) if mode == "screen"
             else (1.0 - 2.0 * p) * o * o + 2.0 * p * o)                       # soft light (pegtop)
    rgb = srgb_eotf(o * (np.float32(1.0) - a) + blend * a)
    return np.concatenate([rgb, out[..., 3:]], axis=-1).astype(np.float32) if out.shape[-1] > 3 else rgb


def pingpong(x, cycles):
    """Fold 0..1 positions so a ramp runs there and back `cycles` times (vathopy's ``cycles``):
    position 0 stays 0, and each half-cycle alternates forward and backward."""
    u = np.asarray(x, dtype=np.float64) * (2.0 * cycles)
    k = np.floor(u)
    fr = u - k
    return np.where(np.mod(k, 2.0) == 0.0, fr, 1.0 - fr).astype(np.float32)


def contour_rate(norm, cycles):
    """How many contour levels each pixel spans (a shader's fwidth: |du/dx| + |du/dy| of the
    contour coordinate u = norm * cycles). Needs the whole picture, for the neighbours."""
    u = np.asarray(norm, dtype=np.float64) * cycles
    gy, gx = np.gradient(u)
    return (np.abs(gx) + np.abs(gy)).astype(np.float32)


def contour_factor(norm, cycles, strength, width_px, rate):
    """Brightness factor for ``contours``: dark lines at `cycles` even steps through the depth
    (one every 1/cycles of the ramp, including both ends), 1 between them. Lines are `width_px`
    pixels wide wherever they are, eased at the edges, reaching 1 - strength at the centre: the
    distance to the nearest level is converted to pixels with `rate` (contour_rate). Where
    levels crowd closer than a line width (steep depth edges), the pixel gets the lines'
    average darkness instead of aliasing."""
    u = np.asarray(norm, dtype=np.float64) * cycles
    g = np.asarray(rate, dtype=np.float64)
    half = width_px / 2.0
    # Pixels to the nearest contour level. A perfectly flat area (g = 0, e.g. the plateau that
    # `prep` clipping leaves at 0 or 1, which sits exactly on a level) gets no line; its rim,
    # where the depth does change, still does.
    flat = g < 1e-7
    d_px = np.where(flat, np.inf, np.abs(u - np.round(u)) / np.where(flat, 1.0, g))
    t = np.clip(1.0 - d_px / half, 0.0, 1.0)
    line = t * t * (3.0 - 2.0 * t)
    avg = np.clip(half * g, 0.0, 1.0)                     # mean darkness of lines this dense
    crowd = np.clip(2.0 * width_px * g - 1.0, 0.0, 1.0)   # 0: lines apart, 1: lines overlap
    dark = line * (1.0 - crowd) + avg * crowd
    return (1.0 - strength * dark).astype(np.float32)


SMOOTH_RANGE = 0.04  # bilateral range sigma, as a fraction of the depth map's value range


def smooth_depth(depth, radius):
    """Edge-preserving smoothing (vathopy's ``smooth_depth``): a bilateral filter with spatial
    sigma `radius` px, so small depth noise inside surfaces is evened out (smoother contours)
    while steps between objects stay sharp."""
    import cv2
    rng = float(depth.max() - depth.min()) or 1.0
    return cv2.bilateralFilter(np.ascontiguousarray(depth, dtype=np.float32), -1,
                               SMOOTH_RANGE * rng, float(radius)).astype(np.float32)


EXAGGERATE_EDGE = 0.05  # exaggerate: depth differences from the surroundings bigger than this (0-1 of the range) are edges, left alone
EXAGGERATE_WORK_PX = 1024  # exaggerate: the surroundings are worked out on the depth at most this many px across


def exaggerate(depth, ex):
    """The depth with its relief made more pronounced (a look's ``exaggerate``): each pixel's
    difference from its smoothed surroundings (over ``size`` x the width, edge-preserving) is multiplied
    by 1 + ``amount``, so bumps, folds and faces stand out more while the steps between things (bigger
    than EXAGGERATE_EDGE of the depth range) stay as they are, without halos. It's scaled back into the
    depth's own range."""
    if ex is None or ex["amount"] <= 0 or not ex.get("enabled", True):
        return depth
    import cv2
    d = np.ascontiguousarray(depth, dtype=np.float32)
    h, w = d.shape
    lo, hi = float(d.min()), float(d.max())
    rng = (hi - lo) or 1.0
    k = min(1.0, EXAGGERATE_WORK_PX / max(h, w))
    small = cv2.resize(d, (max(2, round(w * k)), max(2, round(h * k))), interpolation=cv2.INTER_AREA) if k < 1.0 else d
    sigma = max(1.0, float(ex["size"]) * small.shape[1])
    base = cv2.bilateralFilter(small, -1, EXAGGERATE_EDGE * rng, sigma) if sigma < 12 else small
    if sigma >= 12:                                      # bilateral filters get slow when wide: repeat a narrower one
        for _ in range(int(np.ceil((sigma / 8.0) ** 2))):
            base = cv2.bilateralFilter(base, -1, EXAGGERATE_EDGE * rng, 8.0)
    base = cv2.resize(base, (w, h), interpolation=cv2.INTER_LINEAR) if k < 1.0 else base
    detail = d - base
    edge = np.float32(EXAGGERATE_EDGE * rng)
    detail *= np.exp(-(detail / edge) ** 2)               # big differences are edges (or upsampling misses): no boost
    out = d + np.float32(ex["amount"]) * detail
    olo, ohi = float(out.min()), float(out.max())
    return (lo + (out - olo) * np.float32(rng / ((ohi - olo) or 1.0))).astype(np.float32)   # back into the old range


def _prepared(depth, lk, photo_bu=None):
    """The depth as a look sees it (moved by its view shift, smoothed, exaggerated), the look with
    its focus and keep points given in pixels resolved to depths and pivots, and the photo moved
    alike: what render and deleted work from."""
    depth, photo_bu = view_shift(depth, lk["view_shift"], photo_bu)
    if lk["smooth_depth"] > 0:
        depth = smooth_depth(depth, lk["smooth_depth"])
    depth = exaggerate(depth, lk["exaggerate"])
    if any(fo["at"] is not None for fo in lk["focus"]):
        h_, w_ = depth.shape
        lk = dict(lk, focus=[dict(fo, depth=focus_depth_at(depth, lk, *fo["at"]), at=None,
                                  pivot=[min(max((fo["at"][0] + 0.5) / w_, 0.0), 1.0),
                                         min(max((fo["at"][1] + 0.5) / h_, 0.0), 1.0)])
                             if fo["at"] is not None else fo for fo in lk["focus"]])
    if lk["cut"] is not None and any(k["at"] is not None for k in lk["cut"]["keep"]):
        h_, w_ = depth.shape                             # keep shapes given by a pixel: its depth and place
        lk = dict(lk, cut=dict(lk["cut"], keep=[dict(k, depth=focus_depth_at(depth, lk, *k["at"]), at=None,
                                                     pivot=[min(max((k["at"][0] + 0.5) / w_, 0.0), 1.0),
                                                            min(max((k["at"][1] + 0.5) / h_, 0.0), 1.0)])
                                                if k["at"] is not None else k for k in lk["cut"]["keep"]]))
    return depth, lk, photo_bu


def deleted(depth, look):
    """What a look's cut takes away, for 3D prints that leave it out: (the depth as the look sees it,
    0..1 how much of each pixel is deleted, or None without a cut), rows bottom-up, the map's size,
    before the look's orientation."""
    lk = normalise(look)
    depth = np.ascontiguousarray(depth, dtype=np.float32)
    depth, lk, _ = _prepared(depth, lk)
    if lk["cut"] is None:
        return depth, None
    h_, w_ = depth.shape
    pw, ph = plane_dims(lk, depth.shape)
    f = remapped(depth, lk)
    lo, hi = _window(f, lk)
    px, py = plane_pixel_xy(pw, ph, h_, w_)
    return depth, cut_weight(f, lk["cut"], lo, hi, pw, ph, px, py, float(lk["plane"]["midlevel"]),
                             max(float(lk["plane"]["scale"]), MIN_SCALE), lk["depth_scale"])


def render(depth, look, photo=None):
    """Render a look onto a depth map.

    ``depth``: float32 [H, W], near = bright, rows bottom-up (Blender order).
    Returns (rgb float32 [H, W, 3], info dict).

    Two vathopy options act around the add-on's maths (both off by default):
    ``smooth_depth`` filters the depth first (see smooth_depth), and ``supersample`` N renders
    at N x the resolution and averages back down, anti-aliasing contour lines, fringes, bands
    and area edges. The output is always the depth map's own size.

    ``photo``: the original picture for the look's ``overlay`` (sRGB, rows top-down, any size: it's
    resized to the map), or None."""
    t0 = time.perf_counter()
    lk = normalise(look)
    depth = np.ascontiguousarray(depth, dtype=np.float32)
    if depth.ndim != 2:
        raise ValueError("depth must be a 2-D array")
    if not np.all(np.isfinite(depth)):
        raise ValueError("depth contains NaN or infinite values (use `vathopy prep` to clean it)")
    photo_bu = None
    if lk["overlay"] is not None and lk["overlay"]["enabled"] and photo is not None:                 # the photo at the map's size, bottom-up
        import cv2
        p = np.asarray(photo, np.float32)[..., :3]
        if p.shape[:2] != depth.shape:
            p = cv2.resize(p, (depth.shape[1], depth.shape[0]), interpolation=cv2.INTER_AREA if p.shape[1] > depth.shape[1] else cv2.INTER_LINEAR)
        photo_bu = np.ascontiguousarray(p[::-1])
    depth, lk, photo_bu = _prepared(depth, lk, photo_bu)
    ss = lk["supersample"]
    if photo_bu is not None:
        if ss > 1:
            import cv2
            photo_bu = cv2.resize(photo_bu, (depth.shape[1] * ss, depth.shape[0] * ss), interpolation=cv2.INTER_LINEAR)
        lk = dict(lk, _photo=photo_bu)
    if ss <= 1:
        out, info = _render(depth, lk)
    else:
        import cv2
        h_, w_ = depth.shape
        big = cv2.resize(depth, (w_ * ss, h_ * ss), interpolation=cv2.INTER_LINEAR)
        out_big, info = _render(big, dict(lk, _px_scale=ss))
        out = cv2.resize(out_big, (w_, h_), interpolation=cv2.INTER_AREA).astype(np.float32)
        info["size"] = [w_, h_]
    o = lk["orientation"]
    if o["turns"] or o["mirror"]:
        out = orient(out[::-1], o)[::-1]                 # out is bottom-up; orient works top-down
        info["size"] = [out.shape[1], out.shape[0]]
    info["supersample"] = ss
    info["seconds"] = time.perf_counter() - t0
    return out, info


def orient(img, o):
    """A top-down image (H, W, ...) turned ``o["turns"]`` quarter turns counter-clockwise, then
    mirrored left-right if ``o["mirror"]`` (a look's ``orientation``)."""
    k = int(o.get("turns", 0)) % 4
    if k:
        img = np.rot90(img, k)
    if o.get("mirror"):
        img = img[:, ::-1]
    return np.ascontiguousarray(img)


def _render(depth, lk):
    """The add-on's picture maths for one depth map at its own resolution (see render)."""
    t0 = time.perf_counter()
    depth = np.ascontiguousarray(depth, dtype=np.float32)
    if depth.ndim != 2:
        raise ValueError("depth must be a 2-D array")
    if not np.all(np.isfinite(depth)):
        raise ValueError("depth contains NaN or infinite values (use `vathopy prep` to clean it)")
    h_, w_ = depth.shape
    pw, ph = plane_dims(lk, depth.shape)
    s_eff = max(float(lk["plane"]["scale"]), MIN_SCALE)
    mid = float(lk["plane"]["midlevel"])

    f = remapped(depth, lk)
    lo, hi = _window(f, lk)
    px, py = plane_pixel_xy(pw, ph, h_, w_)
    extra = []                                           # shape focuses: strength x weight, summed with the bands
    if lk["focus"]:
        all_reaches = focus_reach_areas(lk["focus"], lo, hi, s_eff, mid, pw, ph)
        reaches = [a for a, fo in zip(all_reaches, lk["focus"]) if a is not None and not is_shape(fo)]
        for fo, ra in zip(lk["focus"], all_reaches):
            if is_shape(fo) and fo["enabled"] and float(fo["strength"]):
                w = focus_shape_weight(f, fo, ra, lo, hi, pw, ph, px, py, mid, s_eff, lk["depth_scale"])
                extra.append((w * np.float32(fo["strength"])).astype(np.float32))
        lk = dict(lk, bands=lk["bands"] + focus_bands(lk["focus"], lo, hi, s_eff, mid, pw, ph),
                  areas=lk["areas"] + reaches)
    tilt = resolve_tilt(lk["tilt"], lo, hi, s_eff, mid, pw, ph)

    if tilt is None:
        def norm_chunk(r0, r1):
            return np.clip((f[r0:r1] - np.float32(lo)) / np.float32(hi - lo), 0.0, 1.0).astype(np.float32)
    else:
        def norm_chunk(r0, r1):
            tn = tilt_norm((f[r0:r1].astype(np.float64) - lo) / (hi - lo), px[None, :], py[r0:r1, None], tilt)
            return np.clip(tn, 0.0, 1.0).astype(np.float32)
    norm = PAR.map_rows(norm_chunk, h_)
    if lk["equalize"]:
        from .probe import equalized
        norm = equalized(norm)

    ramp = Ramp.from_dict(lk["ramp"])
    lut = ramp.lut()
    rin = PAR.map_rows(lambda r0, r1: np.float32(1.0) - norm[r0:r1], h_) if lk["ramp_near_left"] else norm
    if lk["cycles"] is not None:
        rin = PAR.map_rows(lambda r0, r1: pingpong(rin[r0:r1], lk["cycles"]), h_)
    ramped = PAR.map_rows(lambda r0, r1: apply_lut(rin[r0:r1], lut), h_)
    sel_masks = selection_masks(f, lk, lo, hi)
    for sel, m in sel_masks:        # its own palette and cycles inside each selection
        sramp = Ramp.from_dict(sel["ramp"]) if sel["ramp"] is not None else ramp
        sc = sel["cycles"] if sel["cycles"] is not None else lk["cycles"]
        sin = PAR.map_rows(lambda r0, r1: np.float32(1.0) - norm[r0:r1], h_) if lk["ramp_near_left"] else norm
        if sc is not None:
            sin = PAR.map_rows(lambda r0, r1, sin=sin, sc=sc: pingpong(sin[r0:r1], sc), h_)
        slut, mm = sramp.lut(), m[..., None]
        ramped = PAR.map_rows(lambda r0, r1, sin=sin, slut=slut, mm=mm:
                              ramped[r0:r1] * (np.float32(1.0) - mm[r0:r1]) + apply_lut(sin[r0:r1], slut) * mm[r0:r1], h_)
    if lk["contours"] is not None:
        cc, cs, cw = lk["contours"]["cycles"], lk["contours"]["strength"], lk["contours"]["width"]
        cw *= lk.get("_px_scale", 1)  # supersampling: keep the width in output pixels
        rate = contour_rate(norm, cc)
        ramped = PAR.map_rows(
            lambda r0, r1: ramped[r0:r1] * contour_factor(norm[r0:r1], cc, cs, cw, rate[r0:r1])[..., None], h_)

    any_on = (any(b["enabled"] for b in lk["bands"]) or any(a["enabled"] for a in lk["areas"])
              or any(is_shape(fo) and fo["enabled"] for fo in lk["focus"]))
    black = np.float32(1.0 if lk["base"] in ("BLACK", "WHITE") and any_on else 0.0)
    col = np.float32(1.0 if lk["base"] == "WHITE" else 0.0)
    wlo, whi = np.float32(WINDOW_LOW), np.float32(WINDOW_HIGH)

    def base_chunk(r0, r1):
        comp = (wlo + np.clip(ramped[r0:r1], 0.0, 1.0) * (whi - wlo)).astype(np.float32)
        if not black:
            return comp
        return (comp * (np.float32(1.0) - black) + black * col).astype(np.float32)
    base = PAR.map_rows(base_chunk, h_)
    del norm, rin, ramped

    prep = sum_prepare(lk, depth.shape, px, py, pw, ph, mid, s_eff)
    out = sum_compute(prep, f, base, extra)
    ov = lk["overlay"]
    if ov is not None and lk.get("_photo") is not None and ov["opacity"] > 0 and ov["enabled"]:
        union = np.maximum.reduce([m for _, m in sel_masks]) if sel_masks else None
        out = apply_overlay(out, lk["_photo"], ov, union)
    if lk["cut"] is not None:
        out = apply_cut(out, f, lk["cut"], lo, hi, pw, ph, px, py, mid, s_eff, lk["depth_scale"], lk.get("_px_scale", 1))
    info = {"size": [w_, h_], "window": [lo, hi], "remapped_range": [float(f.min()), float(f.max())],
            "overlay": ("off" if ov is None or not ov["enabled"] else "on" if lk.get("_photo") is not None else "no photo"),
            "terms": len(prep["recs"]), "dead_masks": prep["dead"], "seconds": time.perf_counter() - t0}
    return out, info
