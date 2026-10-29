# SPDX-License-Identifier: GPL-3.0-or-later
"""``vathopy gui``: a small local web app for trying looks on depth maps with sliders.

Python standard library HTTP server + vathopy; the page is gui.html (plain JavaScript; its small 3D
view uses three.js, bundled in static/ with its MIT licence, so nothing is fetched from elsewhere). It lists the depth maps in one folder (raw Marigold/ComfyUI output,
near = bright), preps each once (``prep --kind near-bright``) and keeps it in memory, along
with the smoothed versions, so moving a slider only re-renders:

* while a slider moves the page asks for a fast preview (no supersampling, at most
  --preview-size px), and drops stale requests;
* when it's released it asks for the full-quality render (the map's own size, with the
  chosen supersampling).

Save writes the full-quality 16-bit PNG and the look JSON to the favourites folder, the same
layout as before (<favourites>/<map>_<name>.png and <favourites>/looks/<name>.json); saved
looks can be loaded back into the controls.

Saved files can be downloaded from the page (/api/file), which matters when the GUI runs on a
remote machine such as a RunPod pod.

Every PNG it writes carries the page's settings and the look (see meta.py); "Export settings"
downloads the same as JSON, and "Load from file" reads either back into the controls.

Crop cuts pixels off each side of a depth map's EXR: "Save cropped map" writes
<map>-cropped.exr into the depth-map folder (replacing an earlier crop of that map), "Download
EXR" sends the same file to the browser without writing anything on the server.

It listens on 127.0.0.1 by default. Set VATHOPY_GUI_AUTH="user:password" to require HTTP basic
authentication (do that whenever it's reachable from a network, e.g. through RunPod's public
proxy). Only depth maps listed from its own folder can be opened (normals/albedo/bench EXRs
are left out of the list), only saved files can be downloaded, and save names are reduced to
safe characters.
"""

import base64
import hmac
import json
import os
import re
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

from . import io as VIO
from . import look as LOOK
from . import meta as META
from . import palettes as PAL
from . import engine as E
from .engine import (deleted, exaggerate, focus_depth_at, focus_weights, local_contrast, orient, render, reshape,
                     smooth_depth, view_shift)
from .probe import probe
from .ramp import PRESETS, Ramp, srgb_to_linear

HTML = Path(__file__).with_name("gui.html")
STATIC = Path(__file__).with_name("static")      # three.js (MIT, see static/three-LICENSE) and the 3D view
STATIC_FILES = {"three.module.min.js", "OrbitControls.js", "view3d.js"}
MESH_PREVIEW_MAX_COLS = 200
PHOTO_SEARCH_TTL_S = 10
# A depth map's name -> the photo's stem: <stem>_<prediction>_<00001 | 1280px>[-001].exr (a crop isn't matched:
# its photo would need the same crop).
PHOTO_OF_MAP = re.compile(r"(.+?)_(?:depth|normals|albedo)(?:_\d{5}|_\d+px)?(?:-\d{3})?")
FRINGE = "FRINGE"  # the black/white EASE ramp that --cycles uses with no preset
FRINGE_RAMP = {"stops": [[0.0, [0.0, 0.0, 0.0, 1.0]], [1.0, [1.0, 1.0, 1.0, 1.0]]], "interpolation": "EASE"}
# Highlight colours for focus 1-16 (sRGB), the same order as in gui.html.
FOCUS_COLOURS = ((255, 122, 60), (46, 196, 255), (198, 75, 255), (125, 220, 58), (255, 214, 10), (255, 77, 166),
                 (0, 191, 165), (230, 40, 40), (70, 110, 255), (196, 150, 40), (130, 255, 190), (220, 0, 255),
                 (140, 220, 255), (255, 150, 130), (200, 240, 30), (180, 160, 255))
MAX_FOCUS = len(FOCUS_COLOURS)
HIGHLIGHT_ALPHA = 0.7
# Focus styles: (look base, band strength).
FOCUS_STYLES = {"white_on_black": ("BLACK", 1.0), "black_on_white": ("WHITE", -1.0),
                "light": ("RAMP", 1.0), "dark": ("RAMP", -1.0)}


def _srgb8(lin):
    """Scene-linear RGB (any shape, last axis 3) -> 8-bit sRGB codes."""
    return np.round(np.clip(VIO.srgb_oetf(np.clip(lin, 0.0, None)), 0.0, 1.0) * 255).astype(np.uint8)


def _hex(rgb):
    return "#" + "".join(f"{int(v):02X}" for v in rgb)


def palettes():
    """[{key, label, colours: [hex near ... far], stops: [[x, hex], ...], constant}], the black/white
    fringe ramp first. stops are the ramp's own stops, x = 0 near .. 1 far (as the swatch shows them),
    so the page can draw a shifted palette; constant = hard steps between them."""
    out = []
    for key in [FRINGE] + list(PRESETS):
        ramp = Ramp.from_dict(FRINGE_RAMP if key == FRINGE else {"preset": key})
        rgb = ramp.evaluate(np.linspace(1.0, 0.0, 9, dtype=np.float32))[:, :3]   # near -> far
        label = "Black/white fringe (EASE)" if key == FRINGE else PRESETS[key]["name"]
        mine = key != FRINGE and bool(PRESETS[key].get("user"))
        stops = [[round(1.0 - p, 4), _hex(_srgb8(np.asarray(c[:3], np.float32)))] for p, c in reversed(ramp.stops)]
        out.append({"key": key, "label": label + (" ★" if mine else ""), "user": mine,
                    "colours": [_hex(c) for c in _srgb8(rgb)], "stops": stops,
                    "constant": ramp.interpolation == "CONSTANT"})
    return out


def _page_ramp(key, invert, shift=0):
    """A page palette (swatch key, Invert, Shift colours) -> a look ramp."""
    if key != FRINGE and key not in PRESETS:
        raise ValueError(f"unknown palette {key!r}")
    if key == FRINGE:
        ramp = json.loads(json.dumps(FRINGE_RAMP))
        if invert:
            ramp["stops"] = [[0.0, [1.0, 1.0, 1.0, 1.0]], [1.0, [0.0, 0.0, 0.0, 1.0]]]
    else:
        ramp = {"preset": key, "mirror": True} if invert else {"preset": key}
    shift = int(round(float(shift or 0)))               # the palette's colours moved along its stops
    if shift:
        ramp["shift"] = shift
    return ramp


def _ramp_to_page(ramp):
    """A look ramp -> (palette key, invert, shift); ramps the page can't show fall back to the fringe."""
    if "preset" in ramp:
        return ramp["preset"], bool(ramp.get("mirror")), ramp.get("shift", 0)
    stops = ramp.get("stops", [])
    return FRINGE, bool(stops) and stops[0][1][:3] == [1.0, 1.0, 1.0], ramp.get("shift", 0)


EDGE_FINE = 0.0001      # one step of a target's Edge fine slider (-5..+5)


def _edge_from_page(sel):
    """Edge (0.0005 steps) plus Edge fine (0.0001 steps), as the page shows it."""
    fine = min(max(round(float(sel.get("step_fine", 0) or 0)), -5), 5)
    return max(EDGE_FINE, round(float(sel.get("step", 0.003)) + fine * EDGE_FINE, 4))


def _edge_to_page(step):
    """A look's step back onto the two sliders: the nearest 0.0005 step plus the rest in 0.0001s."""
    coarse = min(max(0.0005, round(step / 0.0005) * 0.0005), 0.03)     # the Edge slider's range
    return round(coarse, 4), min(max(round((step - coarse) / EDGE_FINE), -5), 5)


SIZE_STEP, SIZE_FINE = 0.01, 0.002      # a reshape's Size slider steps, and one step of its Size fine (-5..+5)


def _size_from_page(it):
    """Size (0 = unlimited) plus Size fine, as the page shows it."""
    size = float(it.get("size", 0) or 0)
    if size <= 0:
        return 0.0
    fine = min(max(round(float(it.get("size_fine", 0) or 0)), -5), 5)
    return min(max(round(size + fine * SIZE_FINE, 4), 0.001), 1.0)


def _size_to_page(size):
    """A look's size back onto the two sliders."""
    if size <= 0:
        return 0.0, 0
    coarse = min(max(SIZE_STEP, round(size / SIZE_STEP) * SIZE_STEP), 0.5)
    return round(coarse, 4), min(max(round((size - coarse) / SIZE_FINE), -5), 5)


RESHAPE_KEYS = ("range", "grow", "softness", "offset", "relief", "scale", "radius", "amount", "angle", "distance")


def _reshape_from_page(items):
    out = []
    for it in (items or [])[:MAX_FOCUS]:
        r = {k: float(it[k]) for k in RESHAPE_KEYS if it.get(k) is not None}
        r.update({"kind": it.get("shape") or "scale",
                  "point": [min(max(float(it.get("x", 0.5)), 0.0), 1.0), min(max(float(it.get("y", 0.5)), 0.0), 1.0)],
                  "step": _edge_from_page(it), "size": _size_from_page(it), "floors": bool(it.get("floors")),
                  "invert": bool(it.get("invert")), "anchor": it.get("anchor") or "centre", "only": bool(it.get("only")),
                  "enabled": bool(it.get("enabled", True)), "locked": bool(it.get("locked", False))})
        out.append(r)
    return out


def _reshape_to_page(lk):
    out = []
    for r in lk.get("reshape") or []:
        step, fine = _edge_to_page(float(r.get("step", 0.003)))
        size, size_fine = _size_to_page(float(r.get("size", 0) or 0))
        pt = r.get("point", [0.5, 0.5])
        out.append({"shape": r.get("kind", "depth"), "x": pt[0], "y": pt[1], "step": step, "step_fine": fine,
                    "size": size, "size_fine": size_fine, "floors": bool(r.get("floors")), "invert": bool(r.get("invert")),
                    "anchor": r.get("anchor", "centre"), "only": bool(r.get("only")), "enabled": bool(r.get("enabled", True)),
                    "locked": bool(r.get("locked", False)),
                    **{k: r[k] for k in RESHAPE_KEYS if k in r}})
    return out


def _selections_from_page(sels):
    return [{"point": [min(max(float(sel.get("x", 0.5)), 0.0), 1.0), min(max(float(sel.get("y", 0.5)), 0.0), 1.0)],
             "step": _edge_from_page(sel), "range": float(sel.get("range", 0.0) or 0.0),
             "floors": bool(sel.get("floors")), "grow": float(sel.get("grow", 1.0)), "softness": float(sel.get("softness", 1.0)),
             "ramp": _page_ramp(sel["palette"], sel.get("palette_invert")) if sel.get("palette") else None,
             "cycles": float(sel["cycles"]) if float(sel.get("cycles", 0) or 0) > 0 else None,
             "invert": bool(sel.get("invert")), "enabled": bool(sel.get("enabled", True)), "locked": bool(sel.get("locked", False))}
            for sel in (sels or [])[:MAX_FOCUS]]


def _removes_from_page(parts):
    return [{"point": [min(max(float(r.get("x", 0.5)), 0.0), 1.0), min(max(float(r.get("y", 0.5)), 0.0), 1.0)],
             "step": float(r.get("step", 0.003)), "range": float(r.get("range", 0.0) or 0.0),
             "floors": bool(r.get("floors")), "grow": float(r.get("grow", 3.0)), "softness": float(r.get("softness", 1.0)),
             "invert": bool(r.get("invert")), "enabled": bool(r.get("enabled", True)), "locked": bool(r.get("locked", False))}
            for r in (parts or [])[:MAX_FOCUS]]


def _removes_to_page(cut):
    return [{"x": r["point"][0], "y": r["point"][1], "step": r.get("step", 0.003), "range": r.get("range", 0.0),
             "floors": bool(r.get("floors")), "grow": r.get("grow", 1.0), "softness": r.get("softness", 1.0),
             "invert": bool(r.get("invert")), "enabled": bool(r.get("enabled", True)), "locked": bool(r.get("locked", False))}
            for r in cut.get("remove") or []]


def _selections_to_page(lk):
    out = []
    for sel in lk.get("selections") or []:
        palette, pinv, _ = _ramp_to_page(sel["ramp"]) if sel.get("ramp") else ("", False, 0)
        pt = sel.get("point", [0.5, 0.5])
        step, fine = _edge_to_page(float(sel.get("step", 0.003)))
        out.append({"x": pt[0], "y": pt[1], "step": step, "step_fine": fine, "range": sel.get("range", 0.0),
                    "floors": bool(sel.get("floors")), "grow": sel.get("grow", 1.0), "softness": sel.get("softness", 1.0),
                    "palette": palette, "palette_invert": pinv, "cycles": sel.get("cycles") or 0,
                    "invert": bool(sel.get("invert")), "enabled": bool(sel.get("enabled", True)),
                    "locked": bool(sel.get("locked", False))})
    return out


def look_from_settings(s, preview=False):
    """Page settings -> a vathopy look dict."""
    lk = {"ramp": _page_ramp(s.get("palette", FRINGE), s.get("invert"), s.get("shift", 0)),
          "equalize": bool(s.get("equalize"))}
    cycles = float(s.get("cycles", 0) or 0)
    if cycles > 0:
        lk["cycles"] = cycles
    contours = float(s.get("contours", 0) or 0)
    if contours > 0:
        lk["contours"] = {"cycles": contours, "strength": float(s.get("contour_strength", 0.85)),
                          "width": float(s.get("contour_width", 2.0))}
    lk["supersample"] = 1 if preview else int(s.get("supersample", 1))
    lk["depth_scale"] = float(s.get("depth_scale", 1.0) or 1.0)
    keep = [{"depth": float(k["depth"]), "pivot": [float(k.get("x", 0.5)), float(k.get("y", 0.5))],
             "lean": float(k.get("lean", 0.0)), "angle": float(k.get("angle", 0.0)), "softness": float(k.get("softness", 0.01)),
             "enabled": bool(k.get("enabled", True)), "locked": bool(k.get("locked", False)),
             **{key: v for key, v in _shape_from_page(k, "ellipsoid").items() if key != "fill"}}   # always solid
            for k in (s.get("keep") or [])[:MAX_FOCUS]]
    cut, near = float(s.get("cut_depth", 0) or 0), float(s.get("cut_near", 0) or 0)
    removes = _removes_from_page(s.get("removes"))
    if cut > 0 or near > 0 or keep or removes:
        lk["cut"] = {"depth": cut, "near": near, "softness": float(s.get("cut_softness", 0) or 0),
                     "fill": s.get("cut_fill") or "black", "keep": keep, "remove": removes}
    if s.get("selections"):
        lk["selections"] = _selections_from_page(s["selections"])
    if s.get("reshape"):
        lk["reshape"] = _reshape_from_page(s["reshape"])
    if s.get("edge_clean"):
        lk["edge_cleanup"] = {"width": float(s.get("edge_width", 4.0) or 4.0),
                              "specks": float(s.get("edge_specks", 9.0) if s.get("edge_specks") is not None else 9.0)}
    vx, vy = float(s.get("view_x", 0) or 0), float(s.get("view_y", 0) or 0)
    if vx or vy:
        lk["view_shift"] = {"x": vx, "y": vy, "strength": float(s.get("view_strength", 0.05) or 0.05)}
    if float(s.get("overlay_opacity", 0) or 0) > 0:
        lk["overlay"] = {"opacity": float(s["overlay_opacity"]), "mode": s.get("overlay_mode") or "normal",
                         "where": s.get("overlay_where") or "all", "grey": bool(s.get("overlay_grey")),
                         "invert": bool(s.get("overlay_invert")), "enabled": bool(s.get("overlay_on", True))}
    if float(s.get("exaggerate", 0) or 0) > 0:
        lk["exaggerate"] = {"amount": float(s["exaggerate"]), "size": float(s.get("exaggerate_size", 0.02) or 0.02),
                            "enabled": bool(s.get("exaggerate_on", True))}
    if float(s.get("local_contrast", 0) or 0) > 0 or float(s.get("lc_compress", 0) or 0) > 0:
        lk["local_contrast"] = {"amount": float(s.get("local_contrast", 0) or 0), "range": float(s.get("lc_range", 0.1) or 0.1),
                                "compress": float(s.get("lc_compress", 0) or 0)}
    o = s.get("orientation") or {}
    lk["orientation"] = {"turns": int(o.get("turns", 0) or 0), "mirror": bool(o.get("mirror"))}
    style = s.get("focus_style", "off")
    if style != "off" and s.get("focus"):
        if style not in FOCUS_STYLES:
            raise ValueError(f"unknown focus style {style!r}")
        lk["base"], sign = FOCUS_STYLES[style]           # each focus's strength slider is 0-1; the style sets the sign
        lk["focus"] = [{"depth": float(fo["depth"]), "width": float(fo["width"]), "softness": float(fo["softness"]),
                        # a coloured focus always pushes toward its colour (a negative one would do nothing)
                        "strength": (1.0 if fo.get("color") else sign) * min(max(float(fo.get("strength", 1.0)), 0.0), 1.0),
                        **_colour_from_page(fo),
                        "enabled": bool(fo.get("enabled", True)),
                        "angle": float(fo.get("angle", 0.0)), "lean": float(fo.get("lean", 0.0)),
                        "reach_w": float(fo.get("reach_w", 0.0)), "reach_h": float(fo.get("reach_h", 0.0)),
                        "reach_edge": float(fo.get("reach_edge", 0.5)), "locked": bool(fo.get("locked", False)),
                        "pivot": [float(fo["x"]), float(fo["y"])] if "x" in fo and "y" in fo else None,
                        **_shape_from_page(fo, "plane")}
                       for fo in s["focus"][:MAX_FOCUS]]
    return LOOK.normalise(lk)


RESHAPE_COLOURS = ((46, 196, 182), (244, 162, 97), (231, 111, 81), (138, 177, 125), (157, 78, 221), (255, 183, 3),
                   (76, 201, 240), (241, 91, 181))              # each reshape item's own colour, as gui.html


def _reshape_weights(depth, lk):
    """What each reshape item works on, 0..1 (rows bottom-up): the thing it picked, its brush, or both; None
    for one that's switched off (it keeps its colour slot)."""
    out = []
    for r in lk["reshape"]:
        if not r["enabled"]:
            out.append(None)
            continue
        if r["kind"] in ("depth", "scale"):
            out.append(E._region(depth, lk, r))
        else:
            h, w = depth.shape
            _, _, rad, _, _, dx, dy = E._brush(r, h, w)
            brush = 0.5 * np.clip(1.0 - (dx * dx + dy * dy) / np.float32(rad * rad), 0.0, 1.0) ** 0.5
            out.append(np.where(brush > 0, np.maximum(brush, E._region(depth, lk, r)), 0.0) if r["only"] else brush)
    return out


def highlight(depth, lk):
    """The look without its focus or reshaping, desaturated, with each focus's region tinted in its colour
    and what each reshape item works on in teal: top-down 8-bit sRGB. A focus's region is its band weight
    (1 = fully in focus), the same band the result uses."""
    depth, _ = view_shift(depth, lk["view_shift"])          # the tints on the shifted view, like the picture
    lk = {**lk, "view_shift": None}
    plain = LOOK.normalise({**lk, "focus": [], "reshape": [], "base": "RAMP", "orientation": {"turns": 0, "mirror": False}})
    out, _ = render(depth, plain)
    rgb = _srgb8(out[..., :3]).astype(np.float32) / 255.0
    rgb = 0.85 * (0.35 * rgb + 0.65 * rgb.mean(axis=-1, keepdims=True))
    for i, w in enumerate(focus_weights(depth, lk)):
        if w is None:
            continue                                     # switched off: no tint (it keeps its colour slot)
        k = min(abs(float(lk["focus"][i]["strength"])), 1.0)
        a = HIGHLIGHT_ALPHA * (0.2 + 0.8 * k) * np.clip(w[..., None], 0.0, 1.0)   # strength 0: only a faint tint
        rgb = rgb * (1.0 - a) + (np.asarray(FOCUS_COLOURS[i % len(FOCUS_COLOURS)], np.float32) / 255.0) * a
    for i, w in enumerate(_reshape_weights(depth, lk)):
        if w is None:
            continue
        a = HIGHLIGHT_ALPHA * np.clip(w[..., None], 0.0, 1.0)
        rgb = rgb * (1.0 - a) + (np.asarray(RESHAPE_COLOURS[i % len(RESHAPE_COLOURS)], np.float32) / 255.0) * a
    return orient(np.round(rgb[::-1] * 255).astype(np.uint8), lk["orientation"])   # the tints are in map space


def settings_from_look(lk):
    """A saved look -> page settings (best effort; unknown ramps fall back to the fringe)."""
    palette, invert, shift = _ramp_to_page(lk.get("ramp", {}))
    c = lk.get("contours") or {}
    return {"palette": palette, "invert": invert, "shift": shift, "cycles": lk.get("cycles") or 0,
            "contours": c.get("cycles", 0), "contour_strength": c.get("strength", 0.85) if c else 0,
            "contour_width": c.get("width", 2.0), "smooth": lk.get("smooth_depth", 0),
            "supersample": lk.get("supersample", 1), "equalize": bool(lk.get("equalize")),
            "orientation": dict(lk.get("orientation") or {"turns": 0, "mirror": False}),
            "depth_scale": float(lk.get("depth_scale", 1.0)), **_cut_settings(lk),
            **_focus_settings(lk), "selections": _selections_to_page(lk), **_overlay_settings(lk),
            "reshape": _reshape_to_page(lk), "edge_clean": bool(lk.get("edge_cleanup") and lk["edge_cleanup"].get("enabled", True)),
            "edge_width": float((lk.get("edge_cleanup") or {}).get("width", 4.0)),
            "edge_specks": float((lk.get("edge_cleanup") or {}).get("specks", 9.0))}


def _overlay_settings(lk):
    ov, vs, ex = lk.get("overlay") or {}, lk.get("view_shift") or {}, lk.get("exaggerate") or {}
    lc = lk.get("local_contrast") or {}
    return {"view_x": float(vs.get("x", 0.0)), "view_y": float(vs.get("y", 0.0)), "view_strength": float(vs.get("strength", 0.05)),
            "overlay_opacity": float(ov.get("opacity", 0.0)), "overlay_mode": ov.get("mode", "normal"),
            "overlay_where": ov.get("where", "all"), "overlay_grey": bool(ov.get("grey")),
            "overlay_invert": bool(ov.get("invert")), "overlay_on": bool(ov.get("enabled", True)),
            "exaggerate": float(ex.get("amount", 0.0)), "exaggerate_size": float(ex.get("size", 0.02)),
            "exaggerate_on": bool(ex.get("enabled", True)),
            "local_contrast": float(lc.get("amount", 0.0)), "lc_range": float(lc.get("range", 0.1)),
            "lc_compress": float(lc.get("compress", 0.0))}


def _slider_lean_angle(lean, angle):
    """Any lean/angle as the page's sliders show it: angle -90..90, with the lean's sign giving the
    other half of the circle (lean L at angle A is the same band as -L at A + 180)."""
    lean, angle = float(lean), (float(angle) + 180.0) % 360.0 - 180.0      # -180 .. 180
    if angle > 90.0:
        lean, angle = -lean, angle - 180.0
    elif angle < -90.0:
        lean, angle = -lean, angle + 180.0
    return lean, angle


def _colour_from_page(fo):
    """A focus's colour, page (#rrggbb sRGB or null) -> look (linear [r, g, b] or None)."""
    h = fo.get("color")
    if not h:
        return {"color": None, "color_blend": "NORMAL"}
    if not (isinstance(h, str) and len(h) == 7 and h[0] == "#"):
        raise ValueError(f"focus colour must be #rrggbb, not {h!r}")
    return {"color": [srgb_to_linear(int(h[i:i + 2], 16) / 255.0) for i in (1, 3, 5)],
            "color_blend": fo.get("color_blend") or "NORMAL"}


def _colour_to_page(fo):
    c = fo.get("color")
    return {"color": _hex(_srgb8(np.asarray(c, np.float32))).lower(), "color_blend": fo.get("color_blend", "NORMAL")} if c else {}


def _shape_from_page(fo, default):
    """A focus's or keep shape's shape keys, page -> look (size_x/y/z sliders -> size)."""
    return {"shape": fo.get("shape") or default, "fill": fo.get("fill") or "shell",
            "size": [float(fo.get(k, 0.15) or 0.15) for k in ("size_x", "size_y", "size_z")],
            "length": float(fo.get("length", 0.0) or 0.0), "cone_angle": float(fo.get("cone_angle", 30.0) or 30.0)}


def _shape_to_page(fo, default):
    sz = fo.get("size", 0.15)
    sz = [sz] * 3 if isinstance(sz, (int, float)) else sz
    return {"shape": fo.get("shape", default), "fill": fo.get("fill", "shell"),
            **{k: float(v) for k, v in zip(("size_x", "size_y", "size_z"), sz)},
            "length": float(fo.get("length", 0.0)), "cone_angle": float(fo.get("cone_angle", 30.0))}


def _cut_settings(lk):
    c = lk.get("cut") or {}
    return {"cut_depth": float(c.get("depth", 0.0)), "cut_near": float(c.get("near", 0.0)),
            "cut_softness": float(c.get("softness", 0.0)),
            "cut_fill": c.get("fill", "black"), "removes": _removes_to_page(c),
            "keep": [{"depth": float(k["depth"]), "x": k.get("pivot", [0.5, 0.5])[0], "y": k.get("pivot", [0.5, 0.5])[1],
                      "lean": float(k.get("lean", 0.0)), "angle": float(k.get("angle", 0.0)),
                      "softness": float(k.get("softness", 0.01)), "enabled": bool(k.get("enabled", True)),
                      "locked": bool(k.get("locked", False)), **_shape_to_page(k, "ellipsoid")}
                     for k in c.get("keep") or []]}


def _focus_settings(lk):
    focus = [fo for fo in lk.get("focus") or [] if fo.get("depth") is not None]
    if not focus:
        return {"focus_style": "off", "focus": []}
    plain = [fo for fo in focus if not fo.get("color")] or [{}]     # coloured focuses are always positive
    base = lk.get("base", "RAMP")
    key = (base, 1.0 if float(plain[0].get("strength", -1.0 if base == "WHITE" else 1.0)) >= 0 else -1.0)
    style = next((k for k, v in FOCUS_STYLES.items() if v == key), "light")
    return {"focus_style": style, "focus": [{"depth": fo["depth"], "width": fo.get("width", 0.1),
                                             "softness": fo.get("softness", 0.05),
                                             "strength": abs(float(fo.get("strength", 1.0))),
                                             "enabled": bool(fo.get("enabled", True)),
                                             **dict(zip(("lean", "angle"), _slider_lean_angle(fo.get("lean", 0.0),
                                                                                              fo.get("angle", 0.0)))),
                                             "reach_w": float(fo.get("reach_w", 0.0)),
                                             "reach_h": float(fo.get("reach_h", 0.0)),
                                             "reach_edge": float(fo.get("reach_edge", 0.5)),
                                             "locked": bool(fo.get("locked", False)), **_shape_to_page(fo, "plane"),
                                             **_colour_to_page(fo),
                                             **({"x": fo["pivot"][0], "y": fo["pivot"][1]} if fo.get("pivot") else {})}
                                            for fo in focus]}


class Maps:
    """Depth maps in one folder, prepped and smoothed on demand, cached in memory."""

    def __init__(self, folder, photos=None):
        self.folder = Path(folder).expanduser()
        self.photos = Path(photos).expanduser() if photos else self.folder.parent / "input"
        self._lock = threading.Lock()
        self._prepped = {}
        self._smoothed = {}
        self._photo = (None, None)                         # ((path, mtime, map size), the photo at the map's size)
        self._photo_paths = {}                             # map name -> (when looked, photo path or None)
        self._sizes = {}                                   # map name -> (mtime, [width, height] or None)

    def list(self):
        """Depth maps only: normals/albedo outputs and bench.py runs are left out."""
        skip = ("normals", "albedo", "bench_")
        return sorted(p.name for p in self.folder.glob("*.exr")      # regular files only: no symlinks
                      if p.is_file() and not p.is_symlink() and not any(k in p.name for k in skip))

    def info(self, names):
        """For sorting the list: each map's modified time (s), file size (bytes) and [width, height]
        from its header (None when unreadable). Headers are read once per file version."""
        out = {}
        for name in names:
            try:
                st = (self.folder / name).stat()
            except OSError:
                continue
            cached = self._sizes.get(name)
            if cached is None or cached[0] != st.st_mtime_ns:
                try:
                    import OpenEXR
                    with OpenEXR.File(str(self.folder / name), header_only=True) as f:
                        (x0, y0), (x1, y1) = f.header()["dataWindow"]
                    cached = (st.st_mtime_ns, [int(x1) - int(x0) + 1, int(y1) - int(y0) + 1])
                except Exception:
                    cached = (st.st_mtime_ns, None)
                self._sizes[name] = cached
            out[name] = {"mtime": st.st_mtime, "bytes": st.st_size, "size": cached[1]}
        return out

    def depth(self, name, smooth=0.0):
        if name not in self.list():
            raise ValueError(f"no depth map {name!r} in {self.folder}")
        mtime = (self.folder / name).stat().st_mtime_ns     # a rerun replaced the file: read it again
        with self._lock:
            if self._prepped.get(name, (None,))[0] != mtime:
                raw, _ = VIO.read_image_channel0(str(self.folder / name))
                d, _ = VIO.prepare_depth(raw, "near-bright")
                self._prepped[name] = (mtime, np.ascontiguousarray(d[::-1]))   # bottom-up, like load_depth
            depth = self._prepped[name][1]
            key = (name, mtime, round(float(smooth), 2))
            if smooth > 0 and key not in self._smoothed:
                if len(self._smoothed) > 24:
                    self._smoothed.clear()
                self._smoothed[key] = smooth_depth(depth, smooth)
            return self._smoothed[key] if smooth > 0 else depth

    def photo_path(self, name):
        """The photo a depth map was made from: <stem>.<jpg|png|...> anywhere under the photos folder
        (batch.sh uploads into batch-<time>/ subfolders; the newest wins), where the map is named
        <stem>_<depth|normals|albedo>_<00001|1280px>[-001].exr. None if there's none."""
        hit = self._photo_paths.get(name)
        if hit and time.monotonic() - hit[0] < PHOTO_SEARCH_TTL_S:      # previews ask often; the search walks the folder
            return hit[1]
        m = PHOTO_OF_MAP.fullmatch(Path(name).stem)
        found = []
        if m and self.photos.is_dir():
            stem = m.group(1).lower()
            found = [p for p in self.photos.rglob("*") if p.suffix.lower() in VIO.PHOTO_EXTS and p.stem.lower() == stem
                     and p.is_file()]
        path = max(found, key=lambda p: p.stat().st_mtime) if found else None
        self._photo_paths[name] = (time.monotonic(), path)
        return path

    def photo(self, name, lk):
        """The photo for the look's overlay (sRGB, top-down, at the map's full size), or None when the look
        has no overlay or there's no photo. The last one is kept in memory."""
        if not lk.get("overlay") or not lk["overlay"]["opacity"] > 0:
            return None
        path = self.photo_path(name)
        if path is None:
            return None
        h, w = self.depth(name).shape
        key = (str(path), path.stat().st_mtime_ns, (w, h))
        with self._lock:
            if self._photo[0] != key:
                self._photo = (key, VIO.read_photo(path, (w, h)))
            return self._photo[1]


def mesh_preview(maps, req):
    """The 3D view's surface: the map's windowed depth (0 far .. 1 near, as shapes measure it) on a
    ``cols``-wide grid, rows top-down; ``aspect`` = height / width of the picture, ``zk`` = how much
    the add-on's relief stretches depth relative to width (to place plane focuses)."""
    from . import mesh
    from .engine import MIN_SCALE, _window, plane_dims, remapped
    s = req.get("settings", {})
    d = maps.depth(req["map"], float(s.get("smooth", 0) or 0))
    lk = look_from_settings(s, preview=True)
    if s.get("stl_follow", True):                         # the 3D view follows the reshaping, like the print
        d, _ = reshape(d, lk)
    d = local_contrast(exaggerate(d, lk["exaggerate"]), lk["local_contrast"])
    f = remapped(d, lk)
    lo, hi = _window(f, lk)
    t = np.clip((f[::-1] - lo) / (hi - lo), 0.0, 1.0)
    cols = min(max(int(req.get("cols", 48)), 2), MESH_PREVIEW_MAX_COLS)
    small = mesh._resize(t, cols)
    pw, ph = plane_dims(lk, f.shape)
    return {"cols": int(small.shape[1]), "rows": int(small.shape[0]), "aspect": ph / pw,
            "zk": (hi - lo) * max(float(lk["plane"]["scale"]), MIN_SCALE) / pw,
            "t": np.round(small, 3).ravel().tolist()}


def _preview_depth(d, max_size):
    import cv2
    h, w = d.shape
    s = max_size / max(h, w)
    if s >= 1.0:
        return d
    return cv2.resize(d, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)


def _safe_name(text):
    """A file name from user text: letters, digits, - and _ only (no dots, no slashes)."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(text or "")).strip("_-")[:80]


def make_handler(maps, favourites, preview_size, auth=None, palettes_dir=None):
    favourites = Path(favourites).expanduser()

    class Handler(BaseHTTPRequestHandler):
        server_version = "vathopy-gui"

        def log_message(self, fmt, *args):  # quiet: one line per error only
            if args and str(args[1])[:1] in "45":
                super().log_message(fmt, *args)

        def _send(self, code, body, ctype="application/json", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json_body(self):
            n = int(self.headers.get("Content-Length", 0))
            if n > 1_000_000:
                raise ValueError("request too large")
            return json.loads(self.rfile.read(n) or b"{}")

        def _authorised(self):
            if not auth:
                return True
            given = self.headers.get("Authorization", "")
            if given.startswith("Basic "):
                try:
                    if hmac.compare_digest(base64.b64decode(given[6:]).decode(), auth):
                        return True
                except Exception:
                    pass
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="vathopy"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def do_GET(self):
            if not self._authorised():
                return
            try:
                path = self.path.split("?", 1)[0]
                if path == "/api/file":
                    return self._file()
                if path == "/":
                    return self._send(200, HTML.read_bytes(), "text/html; charset=utf-8")
                if path.startswith("/static/") and path[8:] in STATIC_FILES:
                    return self._send(200, (STATIC / path[8:]).read_bytes(), "text/javascript; charset=utf-8")
                if path == "/api/maps":
                    names = maps.list()
                    return self._send(200, {"folder": str(maps.folder), "maps": names, "info": maps.info(names)})
                if path == "/api/palettes":
                    PAL.register(palettes_dir, quiet=True)      # files added since: in; removed: out
                    return self._send(200, palettes())
                if path == "/api/looks":
                    looks = sorted(p.stem for p in (favourites / "looks").glob("*.json"))
                    return self._send(200, {"folder": str(favourites), "looks": looks})
                return self._send(404, {"error": "not found"})
            except Exception as e:
                return self._send(500, {"error": str(e)})

        def do_POST(self):
            if not self._authorised():
                return
            try:
                path = self.path.split("?", 1)[0]
                if path == "/api/read_settings":                 # the file itself as the body, not JSON
                    return self._read_settings()
                req = self._json_body()
                if path == "/api/render":
                    return self._render(req)
                if path == "/api/probe":
                    s = req.get("settings", {})
                    d = maps.depth(req["map"])
                    pr = probe(d, smooth=None if s.get("smooth_auto") else float(s.get("smooth", 0) or 0),
                               equalize=bool(s.get("equalize")))
                    return self._send(200, pr)
                if path == "/api/depth_at":
                    s = req.get("settings", {})
                    d = maps.depth(req["map"], float(s.get("smooth", 0) or 0))
                    h, w = d.shape
                    x, y = float(req["x"]), float(req["y"])           # fractions of the picture, top-down
                    return self._send(200, {"depth": focus_depth_at(d, look_from_settings(s), x * w, y * h)})
                if path == "/api/photo":
                    p = maps.photo_path(req["map"])
                    return self._send(200, {"photo": p.name if p else None, "folder": str(maps.photos)})
                if path == "/api/mesh_preview":
                    return self._send(200, mesh_preview(maps, req))
                if path == "/api/export":
                    return self._export(req)
                if path == "/api/save":
                    return self._save(req)
                if path == "/api/save_local":
                    return self._save_local(req)
                if path == "/api/crop_save":
                    return self._crop_save(req)
                if path == "/api/crop_download":
                    return self._crop_download(req)
                if path == "/api/stl":
                    return self._stl(req)
                if path == "/api/export_settings":
                    body = json.dumps(_payload(req), indent=1) + "\n"
                    return self._send(200, body, "application/json",
                                      {"Content-Disposition": f'attachment; filename="{_out_name(req)}.vathopy.json"'})
                if path == "/api/look":
                    name = _safe_name(req.get("name", ""))
                    lk = json.loads((favourites / "looks" / f"{name}.json").read_text())
                    return self._send(200, settings_from_look(lk))
                return self._send(404, {"error": "not found"})
            except (ValueError, KeyError, FileNotFoundError) as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def _render(self, req):
            import cv2
            s = req.get("settings", {})
            preview = req.get("quality") == "preview"
            d = maps.depth(req["map"], float(s.get("smooth", 0) or 0))
            map_size = f"{d.shape[1]}x{d.shape[0]}"
            if preview:
                d = _preview_depth(d, preview_size)
            lk = look_from_settings(s, preview=preview)
            t0 = time.perf_counter()
            if req.get("view") == "highlight" and (lk["focus"] or lk["reshape"]):
                img = highlight(d, lk)
            else:
                out, _ = render(d, lk, maps.photo(req["map"], lk))
                img = _srgb8(out[::-1, :, :3])
            ms = (time.perf_counter() - t0) * 1000
            ok, png = cv2.imencode(".png", img[:, :, ::-1])
            return self._send(200, png.tobytes(), "image/png",
                              {"X-Render-Ms": f"{ms:.0f}", "X-Size": f"{img.shape[1]}x{img.shape[0]}",
                               "X-Map-Size": map_size})

        def _export(self, req):
            """The full-quality result as a 16-bit PNG download (nothing is written on the server)."""
            import cv2
            s = req.get("settings", {})
            d = maps.depth(req["map"], float(s.get("smooth", 0) or 0))
            lk = look_from_settings(s)
            out, _ = render(d, lk, maps.photo(req["map"], lk))
            return self._send(200, _png16(out, req), "image/png",
                              {"Content-Disposition": f'attachment; filename="{_out_name(req)}.png"'})

        def _stl(self, req):
            """The map as a 3D-printable STL: a relief of the depth, or a lithophane of the picture, cropped
            (if asked) and turned like the picture on the page. A download, or with "save" written to
            the favourites folder (<map>_<look>_relief.stl, -001 ... rather than overwrite)."""
            from . import mesh
            s = req.get("settings", {})
            o = s.get("orientation") or {}
            o = {"turns": int(o.get("turns", 0) or 0), "mirror": bool(o.get("mirror"))}
            depth = maps.depth(req["map"], float(s.get("smooth", 0) or 0))          # rows bottom-up
            lk = look_from_settings({**s, "orientation": {}})
            if not req.get("follow", True):              # Follow reshaping off: the map as it is
                lk = {**lk, "reshape": [], "edge_cleanup": None}
            seen, gone = deleted(depth, lk)              # the depth as the picture sees it, and what its cut takes
            if req.get("mode") == "lithophane":
                out, _ = render(depth, lk, maps.photo(req["map"], lk))
                td = np.clip(VIO.srgb_oetf(out[::-1, :, :3]), 0.0, 1.0)              # the picture, top-down
            else:
                td = seen[::-1]
            dropped = req.get("deleted") or "out"        # deleted parts: left out (holes), flat, or kept
            if dropped not in ("out", "flat", "keep"):
                raise ValueError("deleted must be out, flat or keep")
            if gone is None or dropped == "keep" or not (gone > 0.5).any():
                gone = None
            crop = req.get("crop") or {}
            t, r, b, l = (int(crop.get(k, 0) or 0) for k in ("top", "right", "bottom", "left"))
            h, w = td.shape[:2]
            if t + b >= h or l + r >= w:
                raise ValueError("the crop leaves nothing")
            td = orient(td[t:h - b, l:w - r], o)
            if gone is not None:
                gone = orient(gone[::-1][t:h - b, l:w - r], o)
            wrap = req.get("wrap") or "flat"
            if wrap not in ("flat", "cylinder", "sphere"):
                raise ValueError("wrap must be flat, cylinder or sphere")
            radius, arc = float(req.get("radius_mm", 40)), float(req.get("arc_deg", 360))
            width_mm = float(req.get("width_mm", 100)) if wrap == "flat" else radius * np.radians(arc)   # round the outside
            px = req.get("px")                           # a number, or a share of the map's own pixels
            share = {"half": 0.5, "three_quarters": 0.75, "all": 1.0}.get(px)
            cols = (max(2, round(td.shape[1] * share)) if share
                    else min(int(px or round(width_mm / 0.2) + 1), td.shape[1]))
            if req.get("mode") == "lithophane":
                lo, hi = float(req.get("min_mm", 0.8)), float(req.get("max_mm", 3.0))
                top, base = mesh.lithophane_heights(td, cols, lo, hi), lo
            else:
                top = mesh.relief_heights(td, cols, float(req.get("relief_mm", 8)), bool(req.get("invert")))
                base = float(req.get("base_mm", 2))
            if gone is not None:
                g = mesh._resize(gone, cols)
                if dropped == "flat" or wrap != "flat":  # wrapped prints can't have holes: deleted parts lie flat
                    top = top * (1.0 - np.clip(g, 0.0, 1.0))
            if not (width_mm > 0 and base >= 0 and cols >= 2):
                raise ValueError("width and base must be above 0, and at least 2 points across")
            if wrap == "cylinder":                       # the base is the wall behind the relief
                verts, faces = mesh.cylinder_mesh(top, radius, base, arc)
            elif wrap == "sphere":
                verts, faces = mesh.sphere_mesh(top, radius, base, arc, float(req.get("lat_deg", 0) or 0) or None)
            elif gone is not None and dropped == "out":
                verts, faces = mesh.masked_heightfield_mesh(top, width_mm / (top.shape[1] - 1), base, g < 0.5)
            else:
                verts, faces = mesh.heightfield_mesh(top, width_mm / (top.shape[1] - 1), base)
            stem = (f"{_out_name(req)}_{'lithophane' if req.get('mode') == 'lithophane' else 'relief'}"
                    + ("" if wrap == "flat" else f"_{wrap}"))
            data = mesh.stl_bytes(verts, faces)
            ext = verts.max(axis=0) - verts.min(axis=0)
            info = {"X-Triangles": str(len(faces)), "X-Points": f"{top.shape[1]}x{top.shape[0]}",
                    "X-Size-MM": f"{ext[0]:.1f}x{ext[1]:.1f}x{ext[2]:.1f}"}
            if req.get("save"):                          # Save local: into the favourites folder, never overwriting
                favourites.mkdir(parents=True, exist_ok=True)
                path, n = favourites / f"{stem}.stl", 1
                while path.exists():
                    path, n = favourites / f"{stem}-{n:03d}.stl", n + 1
                path.write_bytes(data)
                return self._send(200, {"path": str(path), "name": path.name, "bytes": len(data),
                                        "triangles": len(faces), "size_mm": info["X-Size-MM"]})
            return self._send(200, data, "model/stl", {"Content-Disposition": f'attachment; filename="{stem}.stl"', **info})

        def _read_settings(self):
            """Settings from an uploaded PNG/JPEG/JSON (the request body): the page's own if it carries
            them, else the look's as page settings; plus the depth map it was made from."""
            n = int(self.headers.get("Content-Length", 0))
            if n > 300_000_000:
                raise ValueError("file too large")
            d = META.read(self.rfile.read(n))
            if "vathopy_settings" in d and isinstance(d.get("settings"), dict):
                settings, kind = d["settings"], "settings"
            else:
                settings, kind = settings_from_look(LOOK.normalise(META.look_of(d))), "look"   # checked: a real look
            m = d.get("map") or ""
            return self._send(200, {"settings": settings, "kind": kind, "source": d["source"], "map": m,
                                    "name": d.get("name") or "", "map_listed": m in maps.list()})

        def _crop_save(self, req):
            """<map>-cropped.exr in the depth-map folder (an earlier crop of the same map is replaced)."""
            src, sides = _crop_request(maps, req)
            dst = maps.folder / f"{src.stem}-cropped.exr"
            replaced = dst.exists()
            w, h = VIO.crop_exr(src, dst, *sides)
            return self._send(200, {"path": str(dst), "name": dst.name, "width": w, "height": h, "replaced": replaced})

        def _crop_download(self, req):
            """The cropped EXR as a download; nothing is kept on the server."""
            src, sides = _crop_request(maps, req)
            with tempfile.TemporaryDirectory(prefix="vathopy-crop-") as tmp:
                VIO.crop_exr(src, Path(tmp) / "crop.exr", *sides)
                data = (Path(tmp) / "crop.exr").read_bytes()
            return self._send(200, data, "image/x-exr",
                              {"Content-Disposition": f'attachment; filename="{src.stem}-cropped.exr"'})

        def _save_local(self, req):
            """The full-quality result (as Download PNG gives it) written to the favourites folder, never overwriting."""
            s = req.get("settings", {})
            d = maps.depth(req["map"], float(s.get("smooth", 0) or 0))
            lk = look_from_settings(s)
            out, _ = render(d, lk, maps.photo(req["map"], lk))
            favourites.mkdir(parents=True, exist_ok=True)
            stem = _out_name(req)
            png = favourites / f"{stem}.png"
            n = 1
            while png.exists():
                png = favourites / f"{stem}-{n:03d}.png"
                n += 1
            png.write_bytes(_png16(out, req))
            return self._send(200, {"png": str(png), "png_url": f"/api/file?name={png.name}"})

        def _file(self):
            """A saved PNG or look from the favourites folder, as a download."""
            from urllib.parse import parse_qs, urlsplit
            name = parse_qs(urlsplit(self.path).query).get("name", [""])[0]
            if not re.fullmatch(r"(looks/)?[A-Za-z0-9_.-]+\.(png|json)", name) or ".." in name:
                return self._send(400, {"error": "bad file name"})
            path = favourites / name
            if not path.is_file():
                return self._send(404, {"error": "no such saved file"})
            ctype = "image/png" if name.endswith(".png") else "application/json"
            return self._send(200, path.read_bytes(), ctype,
                              {"Content-Disposition": f'attachment; filename="{path.name}"'})

        def _save(self, req):
            s = req.get("settings", {})
            name = _safe_name(req.get("name"))
            if not name:
                raise ValueError("give the look a name")
            mapname = req["map"]
            smooth = float(s.get("smooth", 0) or 0)
            lk = look_from_settings(s)
            lk["smooth_depth"] = smooth
            (favourites / "looks").mkdir(parents=True, exist_ok=True)
            look_path = favourites / "looks" / f"{name}.json"
            look_path.write_text(json.dumps({"vathopy_look": 1, "comment": f"saved from vathopy gui ({mapname})",
                                             **{k: v for k, v in lk.items() if k != "vathopy_look"}}, indent=1) + "\n")
            lk = LOOK.normalise(lk)
            out, _ = render(maps.depth(mapname), lk, maps.photo(mapname, lk))
            stem = Path(mapname).stem
            png = favourites / f"{stem}_{name}.png"
            n = 1
            while png.exists():                       # never overwrite an earlier save
                png = favourites / f"{stem}_{name}-{n:03d}.png"
                n += 1
            png.write_bytes(_png16(out, req))
            return self._send(200, {"png": str(png), "look": str(look_path),
                                    "png_url": f"/api/file?name={png.name}",
                                    "look_url": f"/api/file?name=looks/{look_path.name}"})

    return Handler


def _crop_request(maps, req):
    """The listed map's path and its (top, right, bottom, left) crop, at least one side above 0."""
    name = req.get("map")
    if name not in maps.list():
        raise ValueError(f"no depth map {name!r} in {maps.folder}")
    crop = req.get("crop") or {}
    sides = tuple(int(crop.get(k, 0) or 0) for k in ("top", "right", "bottom", "left"))
    if not any(sides):
        raise ValueError("nothing to crop: every side is 0")
    return maps.folder / name, sides


def _payload(req):
    """What a saved PNG carries and Export settings writes: the page's settings, the look (with its
    smoothing, so ``vathopy render -l`` reproduces it), the depth map and the look's name."""
    s = req.get("settings", {})
    lk = look_from_settings(s)
    lk["smooth_depth"] = float(s.get("smooth", 0) or 0)
    return META.payload(s, lk, req.get("map", ""), req.get("name", ""))


def _png16(out, req):
    """A bottom-up scene-linear render as 16-bit sRGB PNG bytes (as io.write_png writes it), with the
    settings embedded."""
    import cv2
    codes = np.round(VIO.srgb_oetf(out[::-1, :, :3]) * 65535).astype(np.uint16)
    ok, png = cv2.imencode(".png", np.ascontiguousarray(codes[:, :, ::-1]))
    if not ok:
        raise ValueError("could not encode the PNG")
    return META.embed_png(png.tobytes(), _payload(req))


def _out_name(req):
    """<depth map>_<look name or "vathopy">, for downloaded and locally saved PNGs."""
    return (_safe_name(Path(req["map"]).stem) or "depth") + "_" + (_safe_name(req.get("name")) or "vathopy")


def serve(depth_dir, favourites, host="127.0.0.1", port=8770, preview_size=1280, palettes_dir=None, photos=None):
    maps = Maps(depth_dir, photos)
    palettes_dir = palettes_dir or PAL.default_dir()
    PAL.register(palettes_dir)
    auth = os.environ.get("VATHOPY_GUI_AUTH") or None
    if auth and ":" not in auth:
        raise SystemExit("VATHOPY_GUI_AUTH must be user:password")
    if host not in ("127.0.0.1", "localhost", "::1") and not auth:
        print("warning: listening on a network address with no VATHOPY_GUI_AUTH set", flush=True)
    httpd = ThreadingHTTPServer((host, port), make_handler(maps, favourites, preview_size, auth, palettes_dir))
    print(f"vathopy gui on http://{host}:{port}/  (depth maps: {maps.folder}, {len(maps.list())} found; "
          f"photos: {maps.photos}; saves to {Path(favourites).expanduser()}; your palettes: {Path(palettes_dir).expanduser()}; "
          + ("password required)" if auth else "no password)"), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
