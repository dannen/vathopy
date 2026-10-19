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
from .look import DEFAULT_BAND, _merge, normalise
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


def sum_compute(prep, f, base):
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


def focus_bands(focus, lo, hi, s_eff, mid):
    """The look's ``focus`` entries as add-on bands: flat (normal 0,0,1) at the relief height of
    that depth, width and softness scaled from fractions of the window to metres."""
    span = (hi - lo) * s_eff
    out = []
    for i, fo in enumerate(focus):
        z = (lo + float(fo["depth"]) * (hi - lo) - mid) * s_eff
        out.append(_merge(DEFAULT_BAND, {"id": f"focus{i + 1}", "name": f"focus{i + 1}", "enabled": bool(fo["enabled"]),
                                         "strength": float(fo["strength"]), "normal": [0.0, 0.0, 1.0],
                                         "center": [0.0, 0.0, z], "width": float(fo["width"]) * span,
                                         "softness": float(fo["softness"]) * span}, "focus"))
    return out


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


def render(depth, look):
    """Render a look onto a depth map.

    ``depth``: float32 [H, W], near = bright, rows bottom-up (Blender order).
    Returns (rgb float32 [H, W, 3], info dict).

    Two vathopy options act around the add-on's maths (both off by default):
    ``smooth_depth`` filters the depth first (see smooth_depth), and ``supersample`` N renders
    at N x the resolution and averages back down, anti-aliasing contour lines, fringes, bands
    and area edges. The output is always the depth map's own size."""
    t0 = time.perf_counter()
    lk = normalise(look)
    depth = np.ascontiguousarray(depth, dtype=np.float32)
    if depth.ndim != 2:
        raise ValueError("depth must be a 2-D array")
    if not np.all(np.isfinite(depth)):
        raise ValueError("depth contains NaN or infinite values (use `vathopy prep` to clean it)")
    if lk["smooth_depth"] > 0:
        depth = smooth_depth(depth, lk["smooth_depth"])
    if any(fo["at"] is not None for fo in lk["focus"]):
        lk = dict(lk, focus=[dict(fo, depth=focus_depth_at(depth, lk, *fo["at"]), at=None) if fo["at"] is not None
                             else fo for fo in lk["focus"]])
    ss = lk["supersample"]
    if ss <= 1:
        out, info = _render(depth, lk)
    else:
        import cv2
        h_, w_ = depth.shape
        big = cv2.resize(depth, (w_ * ss, h_ * ss), interpolation=cv2.INTER_LINEAR)
        out_big, info = _render(big, dict(lk, _px_scale=ss))
        out = cv2.resize(out_big, (w_, h_), interpolation=cv2.INTER_AREA).astype(np.float32)
        info["size"] = [w_, h_]
    info["supersample"] = ss
    info["seconds"] = time.perf_counter() - t0
    return out, info


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
    if lk["focus"]:
        lk = dict(lk, bands=lk["bands"] + focus_bands(lk["focus"], lo, hi, s_eff, mid))
    tilt = resolve_tilt(lk["tilt"], lo, hi, s_eff, mid, pw, ph)
    px, py = plane_pixel_xy(pw, ph, h_, w_)

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
    if lk["contours"] is not None:
        cc, cs, cw = lk["contours"]["cycles"], lk["contours"]["strength"], lk["contours"]["width"]
        cw *= lk.get("_px_scale", 1)  # supersampling: keep the width in output pixels
        rate = contour_rate(norm, cc)
        ramped = PAR.map_rows(
            lambda r0, r1: ramped[r0:r1] * contour_factor(norm[r0:r1], cc, cs, cw, rate[r0:r1])[..., None], h_)

    any_on = any(b["enabled"] for b in lk["bands"]) or any(a["enabled"] for a in lk["areas"])
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
    out = sum_compute(prep, f, base)
    info = {"size": [w_, h_], "window": [lo, hi], "remapped_range": [float(f.min()), float(f.max())],
            "terms": len(prep["recs"]), "dead_masks": prep["dead"], "seconds": time.perf_counter() - t0}
    return out, info
