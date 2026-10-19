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
}

DEFAULT_CONTOURS = {"cycles": 4.0, "strength": 0.85, "width": 2.0}

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
    if lk["tilt"] is not None:
        lk["tilt"] = _merge({"normal": [0.0, 1.0, 0.0], "x0": 0.0, "y0": 0.0, "fit": False}, lk["tilt"], "tilt")
    return lk


def load(path):
    with open(path, encoding="utf-8") as fh:
        return normalise(json.load(fh))


def save(look, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(look, fh, indent=2)
        fh.write("\n")
