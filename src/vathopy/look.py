# SPDX-License-Identifier: GPL-3.0-or-later
"""A *look*: every setting that turns a depth map into a Vathograph, as plain JSON.

Units follow the Vathography add-on, so a look extracted from Blender renders identically:

* depth values are the depth map's own values (near = bright, usually 0..1);
* the relief is a plane ``width`` x ``height`` metres (height = width / aspect when omitted),
  x to the right, y up, centred on 0;
* a pixel's relief height is ``z = (remapped_depth - midlevel) * scale`` metres;
* bands and areas live in that plane space (metres).

Two keys are vathopy extensions with no add-on equivalent (both off by default, so looks
extracted from Blender render exactly as before):

* ``cycles``: repeat the ramp N times across the depth, mirroring every other repeat so there
  are no seams; one cycle is there and back (far -> near -> far ends of the ramp). With a black/
  white EASE ramp, N cycles is the N-cycle fringe.
* ``contours``: ``{"cycles": N, "strength": s, "width": w}`` draws dark contour lines at N even
  steps through the depth over the ramp's colours (strength 0..1 = how dark; width = line
  thickness in output pixels, the same everywhere), keeping the ramp, e.g. a near-to-far
  colour gradient.
* ``supersample``: render at N x (1-4) the resolution and average back down: anti-aliasing for
  contour lines, fringes and band/area edges.
* ``smooth_depth``: edge-preserving smoothing of the depth map first (spatial radius in px, 0 =
  off), so contours follow surfaces instead of depth noise.
* ``equalize``: spread the ramp by pixel count (histogram equalisation of the depth), so each
  part of the ramp, and each contour level, covers about the same share of the picture.
* ``focus``: the creative focus in plain depth terms, ``[{"depth": d, "width": w, "softness": s,
  "strength": k}]`` with d, w and s as fractions of the ramp window (0 = far, 1 = near). Each
  becomes an add-on Focus & Shape Band lying flat in the relief (normal 0,0,1) at that depth, so it
  renders exactly like one placed in Blender. strength 1 lifts that depth to white, -1 sinks it to
  black; with ``"base": "BLACK"`` the result is a white subject on black. ``"at": [x, y]`` instead of
  ``depth`` picks the depth under that pixel (top-down pixel coordinates of the depth map).
  ``"lean"`` (-90 to 90 degrees; negative tilts the other way) tilts the band's plane away from flat, so it cuts through the scene at an
  angle (a shaft of light across a floor) instead of following one depth; at 90 it stands upright and
  ignores depth, a straight stripe across the picture; ``"angle"`` (degrees,
  counter-clockwise from the right) turns that tilt around the picture. The tilted plane passes
  through ``"pivot": [fx, fy]`` (fractions of the picture, top-down; default the centre, or the
  picked pixel with ``at``) at the focus depth.
  ``"reach_w"`` / ``"reach_h"`` (half-width and half-height as fractions of the picture's width and
  height, 0 = unlimited) confine the focus to an oval around the pivot, soft over ``"reach_edge"``
  (0-1): an isolated subject with a pool of light at its feet instead of everything at that depth.
  It's the add-on's mask area (ellipsoid, Keep) aimed at that focus's band.
* A focus's ``"shape"`` (vathopy only) makes it a 3D shape instead of a plane: ``"ellipsoid"`` (a
  sphere when its three ``"size"`` values match), ``"box"`` (``size`` = half extents), ``"cylinder"``
  (a rod: radius ``size[0]`` around an axis that stands upright in the picture at lean 0 / angle 0;
  ``angle`` turns it in the picture, ``lean`` tilts it towards the viewer; ``"length"`` 0 = endless) or ``"cone"`` (a double cone with its
  apex at the focus point, half-angle ``"cone_angle"``), centred on ``pivot`` at ``depth``, turned by
  ``lean``/``angle``. ``"fill": "shell"`` lights a skin ``width`` thick (soft over ``softness``):
  the focal plane bent into the shape; ``"solid"`` everything inside. Sizes, width and softness are
  fractions of the picture's width; depth runs 0-1 over the window times the look's ``depth_scale``
  (how deep the scene is relative to its width).
* ``cut`` (vathopy only): the deletion slab, ``{"depth": c, "near": n, "softness": s, "fill":
  "black"|"white", "keep": [shapes]}``: everything farther than ``c`` (0 = far .. 1 = near, as a
  fraction of the window; 1 deletes everything), and everything nearer than ``1 - n`` (``n``
  measured from the nearest end: 0 = none, 1 = everything), becomes black or white, fading back
  over ``s``, except inside the
  ``keep`` shapes (``shape``, ``size``, ``depth``, ``pivot``, ``lean``/``angle`` etc. as for a shape
  focus, solid, their edge soft over ``softness``).
* ``orientation``: ``{"turns": k, "mirror": m}`` turns the finished picture k quarter turns
  counter-clockwise, then mirrors it left-right if m; the last step, so focus pivots, ``at`` and
  everything else stay in the depth map's own coordinates.
"""

import copy
import json

LOOK_VERSION = 1

DEFAULT_LOOK = {
    "vathopy_look": LOOK_VERSION,
    "plane": {"width": 1.5, "height": None, "scale": 4.0, "midlevel": 0.5},
    "flip": False,
    "controls": [],
    "window": "auto",
    "tilt": None,
    "ramp": {"preset": "WHITE_TO_BLACK"},
    "ramp_near_left": False,
    "base": "RAMP",
    "bands": [],
    "areas": [],
    "groups": {},
    "solo": "",
    "cycles": None,
    "contours": None,
    "supersample": 1,
    "smooth_depth": 0.0,
    "equalize": False,
    "focus": [],
    "orientation": {"turns": 0, "mirror": False},
    "depth_scale": 1.0,
    "cut": None,
}

DEFAULT_CUT = {"depth": 0.0, "near": 0.0, "softness": 0.0, "fill": "black", "keep": []}
DEFAULT_KEEP = {"shape": "ellipsoid", "depth": 0.5, "at": None, "pivot": [0.5, 0.5], "size": [0.15, 0.15, 0.15], "length": 0.0,
                "cone_angle": 30.0, "lean": 0.0, "angle": 0.0, "softness": 0.01, "enabled": True, "locked": False}

DEFAULT_CONTOURS = {"cycles": 4.0, "strength": 0.85, "width": 2.0}

DEFAULT_FOCUS = {"depth": None, "at": None, "width": 0.1, "softness": 0.05, "strength": 1.0, "enabled": True,
                 "angle": 0.0, "lean": 0.0, "pivot": None, "reach_w": 0.0, "reach_h": 0.0, "reach_edge": 0.5,
                 "locked": False,  # locked: the GUI's lock, kept with the look; no effect on the picture
                 "shape": "plane", "fill": "shell", "size": [0.15, 0.15, 0.15], "length": 0.0, "cone_angle": 30.0}

DEFAULT_CONTROL = {"center": 0.5, "offset": 0.0, "feather": 0.02, "from_min": None, "from_max": None}

DEFAULT_BAND = {"id": "", "name": "", "enabled": True, "strength": 1.0, "interpolation": "LINEAR",
                "normal": [0.0, 0.0, 1.0], "center": [0.0, 0.0, 0.0], "width": 0.06, "softness": 0.06}

DEFAULT_AREA = {"id": "", "name": "", "enabled": True, "mode": "ADD", "shape": "ELLIPSOID",
                "position": [0.0, 0.0, 0.1], "radius": [0.5, 0.5, 0.5],
                "basis": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                "reach_plus": [1.0, 1.0, 1.0], "reach_minus": [1.0, 1.0, 1.0],
                "strength": 1.0, "falloff": 0.5, "edge": 0.5, "group": "Group 1",
                "mask": {"strength": 1.0, "side": "HIDE", "edge": 0.1, "target": "ALL_BANDS"}}

DEFAULT_GROUP = {"strength": 1.0, "blend": 0.0}

REPLACED_WHOLE = ("ramp",)  # given as a whole: {"preset": KEY [, "mirror": true]} or {"stops": [...], ...}

AREA_MODES = ("ADD", "MERGE", "MASK")
BASES = ("RAMP", "BLACK", "WHITE")


def _merge(defaults, given, where):
    out = copy.deepcopy(defaults)
    for k, v in (given or {}).items():
        if k not in defaults:
            raise ValueError(f"{where}: unknown key {k!r}")
        if isinstance(defaults[k], dict) and isinstance(v, dict) and defaults[k] and k not in REPLACED_WHOLE:
            out[k] = _merge(defaults[k], v, f"{where}.{k}")
        else:
            out[k] = v
    return out


def normalise(look):
    """Fill defaults and check a look dict; returns a new dict."""
    lk = _merge({k: v for k, v in DEFAULT_LOOK.items() if k != "vathopy_look"},
                {k: v for k, v in look.items() if k not in ("vathopy_look", "comment", "source")}, "look")
    lk["vathopy_look"] = look.get("vathopy_look", LOOK_VERSION)
    if lk["vathopy_look"] != LOOK_VERSION:
        raise ValueError(f"look version {lk['vathopy_look']} is not supported (this is {LOOK_VERSION})")
    lk["controls"] = [_merge(DEFAULT_CONTROL, c, f"controls[{i}]") for i, c in enumerate(lk["controls"])]
    lk["bands"] = [_merge(DEFAULT_BAND, b, f"bands[{i}]") for i, b in enumerate(lk["bands"])]
    for i, b in enumerate(lk["bands"]):
        b["id"] = b["id"] or f"band{i + 1}"
        b["name"] = b["name"] or b["id"]
    lk["areas"] = [_merge(DEFAULT_AREA, a, f"areas[{i}]") for i, a in enumerate(lk["areas"])]
    for i, a in enumerate(lk["areas"]):
        a["id"] = a["id"] or f"area{i + 1}"
        a["name"] = a["name"] or a["id"]
        if a["mode"] not in AREA_MODES:
            raise ValueError(f"areas[{i}].mode must be one of {AREA_MODES}")
        if a["shape"] not in ("ELLIPSOID", "BOX"):
            raise ValueError(f"areas[{i}].shape must be ELLIPSOID or BOX")
        if a["mask"]["side"] not in ("HIDE", "KEEP"):
            raise ValueError(f"areas[{i}].mask.side must be HIDE or KEEP")
    lk["groups"] = {str(k): _merge(DEFAULT_GROUP, v, f"groups[{k!r}]") for k, v in lk["groups"].items()}
    if lk["base"] not in BASES:
        raise ValueError(f"base must be one of {BASES}")
    w = lk["window"]
    if not (w == "auto" or (isinstance(w, (list, tuple)) and len(w) == 2)):
        raise ValueError("window must be \"auto\" or [lo, hi]")
    if lk["cycles"] is not None:
        lk["cycles"] = float(lk["cycles"])
        if not lk["cycles"] > 0:
            raise ValueError("cycles must be > 0 (or null for off)")
    lk["equalize"] = bool(lk["equalize"])
    lk["depth_scale"] = float(lk["depth_scale"])
    if lk["cut"] is not None:
        c = lk["cut"] = _merge(DEFAULT_CUT, lk["cut"], "cut")
        c["depth"], c["near"], c["softness"] = float(c["depth"]), float(c["near"]), float(c["softness"])
        if not (0.0 <= c["depth"] <= 1.0 and 0.0 <= c["near"] <= 1.0 and 0.0 <= c["softness"] <= 1.0) \
                or c["fill"] not in ("black", "white"):
            raise ValueError("cut: depth, near and softness must be 0-1, fill black or white")
        c["keep"] = [_merge(DEFAULT_KEEP, k, f"cut.keep[{i}]") for i, k in enumerate(c["keep"] or [])]
        for i, k in enumerate(c["keep"]):
            if k["shape"] not in ("ellipsoid", "box", "cylinder", "cone"):
                raise ValueError(f"cut.keep[{i}].shape must be ellipsoid, box, cylinder or cone")
            sz = k["size"]
            k["size"] = [float(sz)] * 3 if isinstance(sz, (int, float)) else [float(v) for v in sz]
            if k["at"] is not None and not (isinstance(k["at"], (list, tuple)) and len(k["at"]) == 2):
                raise ValueError(f"cut.keep[{i}].at must be [x, y] pixels")
            for key in ("depth", "length", "cone_angle", "lean", "angle", "softness"):
                k[key] = float(k[key])
            if (len(k["size"]) != 3 or not all(0.0 < v <= 4.0 for v in k["size"]) or not 0.0 <= k["depth"] <= 1.0
                    or not 0.5 <= k["cone_angle"] <= 89.5 or not 0.0 <= k["softness"] <= 1.0):
                raise ValueError(f"cut.keep[{i}]: size above 0 to 4, depth 0-1, cone_angle 0.5-89.5, softness 0-1")
    if not 0.05 <= lk["depth_scale"] <= 20.0:
        raise ValueError("depth_scale must be 0.05 to 20")
    o = lk["orientation"]
    if isinstance(o["turns"], bool) or not isinstance(o["turns"], int):
        raise ValueError("orientation.turns must be a whole number of quarter turns")
    lk["orientation"] = {"turns": o["turns"] % 4, "mirror": bool(o["mirror"])}
    if lk["supersample"] not in (1, 2, 3, 4):
        raise ValueError("supersample must be 1, 2, 3 or 4")
    lk["smooth_depth"] = float(lk["smooth_depth"])
    if not 0.0 <= lk["smooth_depth"] <= 50.0:
        raise ValueError("smooth_depth must be 0 (off) to 50 px")
    if lk["contours"] is not None:
        lk["contours"] = _merge(DEFAULT_CONTOURS, lk["contours"], "contours")
        c = lk["contours"]
        c["cycles"], c["strength"], c["width"] = float(c["cycles"]), float(c["strength"]), float(c["width"])
        if not c["cycles"] > 0 or not 0.0 <= c["strength"] <= 1.0 or not 0.0 < c["width"] <= 50.0:
            raise ValueError("contours: cycles must be > 0, strength 0..1 and width 0..50 px")
    lk["focus"] = [_merge(DEFAULT_FOCUS, fo, f"focus[{i}]") for i, fo in enumerate(lk["focus"] or [])]
    for i, fo in enumerate(lk["focus"]):
        if (fo["depth"] is None) == (fo["at"] is None):
            raise ValueError(f"focus[{i}]: give either depth (0-1) or at ([x, y] pixels)")
        if fo["depth"] is not None and not 0.0 <= float(fo["depth"]) <= 1.0:
            raise ValueError(f"focus[{i}].depth must be 0 (far) to 1 (near)")
        if not (0.0 <= float(fo["width"]) <= 1.0 and 0.0 <= float(fo["softness"]) <= 1.0
                and -1.0 <= float(fo["strength"]) <= 1.0):
            raise ValueError(f"focus[{i}]: width and softness must be 0-1, strength -1 to 1")
        fo["angle"], fo["lean"] = float(fo["angle"]) % 360.0, float(fo["lean"])
        if not -90.0 <= fo["lean"] <= 90.0:
            raise ValueError(f"focus[{i}].lean must be -90 to 90 degrees (0 = flat, ±90 = upright)")
        fo["reach_w"], fo["reach_h"], fo["reach_edge"] = float(fo["reach_w"]), float(fo["reach_h"]), float(fo["reach_edge"])
        if not (0.0 <= fo["reach_w"] <= 2.0 and 0.0 <= fo["reach_h"] <= 2.0 and 0.0 <= fo["reach_edge"] <= 1.0):
            raise ValueError(f"focus[{i}]: reach_w and reach_h must be 0 (unlimited) to 2, reach_edge 0-1")
        pv = fo["pivot"]
        if pv is not None and not (isinstance(pv, (list, tuple)) and len(pv) == 2
                                   and all(0.0 <= float(v) <= 1.0 for v in pv)):
            raise ValueError(f"focus[{i}].pivot must be [fx, fy], fractions of the picture (0-1)")
        if fo["shape"] not in ("plane", "ellipsoid", "box", "cylinder", "cone") or fo["fill"] not in ("shell", "solid"):
            raise ValueError(f"focus[{i}]: shape must be plane, ellipsoid, box, cylinder or cone; fill shell or solid")
        sz = fo["size"]
        sz = [float(sz)] * 3 if isinstance(sz, (int, float)) else [float(v) for v in sz]
        if len(sz) != 3 or not all(0.0 < v <= 4.0 for v in sz):
            raise ValueError(f"focus[{i}].size must be one number or [x, y, z], each above 0 and at most 4")
        fo["size"], fo["length"], fo["cone_angle"] = sz, float(fo["length"]), float(fo["cone_angle"])
        if not (0.0 <= fo["length"] <= 8.0 and 0.5 <= fo["cone_angle"] <= 89.5):
            raise ValueError(f"focus[{i}]: length must be 0 (endless) to 8, cone_angle 0.5-89.5 degrees")
    if lk["tilt"] is not None:
        lk["tilt"] = _merge({"normal": [0.0, 1.0, 0.0], "x0": 0.0, "y0": 0.0, "fit": False}, lk["tilt"], "tilt")
    return lk


def read_raw(path):
    """The look in a file, as given (not normalised): a look JSON, the GUI's exported settings JSON,
    or a PNG/JPEG the GUI saved (its embedded settings)."""
    from . import meta
    with open(path, "rb") as fh:
        data = fh.read()
    if data.lstrip()[:1] == b"{":
        d = json.loads(data)
        return meta.look_of(d) if "vathopy_settings" in d else d
    return meta.look_of(meta.read(data))


def load(path):
    return normalise(read_raw(path))


def save(look, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(look, fh, indent=2)
        fh.write("\n")
