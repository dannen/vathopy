# SPDX-License-Identifier: GPL-3.0-or-later
"""A *look*: every setting that turns a depth map into a Vathograph, as plain JSON.

Units follow the Vathography add-on, so a look extracted from Blender renders identically:

* depth values are the depth map's own values (near = bright, usually 0..1);
* the relief is a plane ``width`` x ``height`` metres (height = width / aspect when omitted),
  x to the right, y up, centred on 0;
* a pixel's relief height is ``z = (remapped_depth - midlevel) * scale`` metres;
* bands and areas live in that plane space (metres).

Bands, areas and merge groups take a colour (add-on 1.0.96): ``"color": [r, g, b]`` (0-1, linear; null =
off) makes a positive strength push toward that colour instead of white (a negative strength then does
nothing), combined with the picture by ``"color_blend"``: NORMAL, MULTIPLY, SCREEN, OVERLAY, SOFT_LIGHT or
COLOR (hue and saturation only). Coloured terms with the same blend are averaged, weighted by their
amounts; the blends apply in that order, after the white / black sum. A ``focus`` takes the same two keys.

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
  focus, solid, their edge soft over ``softness``). ``"remove": [parts]`` also deletes whole things
  picked on the picture, whatever their depth (keep shapes don't hold on to them): each part grows
  from its ``point`` up to its depth edges like a selection (``point``, ``step``, ``range``, ``floors``,
  ``grow``, ``softness``, ``invert``, ``enabled``, below; no palette).
* ``selections`` (vathopy only): parts of the picture picked by depth, each with its own palette,
  ``[{"point": [fx, fy], "step": s, "range": r, "floors": false, "grow": g, "softness": px,
  "ramp": {...} | null, "cycles": c | null, "invert": false}]``. A selection grows from ``point`` (fractions of the picture,
  top-down) over neighbouring pixels whose depth changes by at most ``step`` (a fraction of the window)
  from one pixel to the next, so it stops at depth edges such as a person's outline; ``range`` (0 =
  off) also keeps it within that much depth of the point (so it doesn't creep from someone's feet over
  the floor); ``floors`` stops it at surfaces that come nearer going down the picture (the ground
  under a person's feet). Small pockets left inside are filled. ``grow`` widens it by that many pixels (catching the soft edge pixels), ``softness``
  feathers its edge (px), ``invert`` selects everything else. Inside it the picture uses ``ramp`` (the
  look's own when null) and ``cycles`` (the look's when null) instead; contour lines, focuses and the cut
  go over it as usual. The growth runs on the depth at most 1536 px across, so previews match.
* ``overlay`` (vathopy only): the original photo over the picture, ``{"opacity": a, "mode":
  "normal"|"multiply"|"screen"|"soft_light", "where": "all"|"selections"|"outside", "grey": false,
  "invert": false, "enabled": true}`` (``invert``: the photo's negative; ``enabled`` false keeps the
  settings but shows no photo):
  blended in at ``opacity`` (0-1) everywhere, only inside the selections, or only outside them,
  under the cut. The photo isn't part of the look: it's given to the renderer (``vathopy render
  --photo``; the GUI finds it by the depth map's name), and without one the overlay is skipped.
* ``view_shift`` (vathopy only): look at the scene from a little to one side, ``{"x": sx, "y": sy,
  "strength": k, "pivot": p}``: every pixel moves by its depth, near ones one way and far ones the
  other, as if the camera moved right (x > 0; left < 0) or up (y > 0; down < 0), by up to ``k`` x the
  picture's width per unit of depth from ``pivot`` (0 = far .. 1 = near; that depth stays put). Gaps
  that open behind near things are filled from what's behind them. With ``"edges": "zoom"`` (the
  default) the picture is enlarged just enough to hide the strips the move uncovers along its borders;
  ``"fill"`` keeps the framing and fills them from the nearest pixels instead. It's applied to the depth map (and
  the overlay photo) first, so everything else follows the new view; focus points, keep shapes and
  selections stay where they were picked, in the map's own coordinates.
* ``exaggerate`` (vathopy only): more pronounced relief, ``{"amount": a, "size": s, "enabled": true}``
  (``enabled`` false keeps the settings but leaves the depth as it is): each pixel's
  difference from its smoothed surroundings (over ``s`` x the picture's width, 0.002-0.2) is
  multiplied by 1 + ``a`` (0-10), so bumps, folds and faces stand out more; the steps between things
  stay as they are. After ``smooth_depth``; the 3D print (``vathopy relief``) uses it too.
* ``local_contrast`` (vathopy only): ``{"amount": a, "range": r, "compress": c, "enabled": true}``, an
  edge-aware local Laplacian filter on the depth after ``exaggerate``: around each pixel, depth
  differences smaller than ``r`` (0.01-0.5 of the depth range) are boosted (more with ``a``, 0-1), so
  small shapes (folds, faces, carvings) stand out, with sharp edges and no halos. ``c`` (0-0.9, default
  0) also shrinks the bigger differences, the steps between things, which tilts the surfaces beside
  each step towards each other. Before the ramp, so colours stay on it; the 3D print uses it too.
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
    "selections": [],
    "overlay": None,
    "view_shift": None,
    "exaggerate": None,
    "local_contrast": None,
}

DEFAULT_CUT = {"depth": 0.0, "near": 0.0, "softness": 0.0, "fill": "black", "keep": [], "remove": []}
DEFAULT_KEEP = {"shape": "ellipsoid", "depth": 0.5, "at": None, "pivot": [0.5, 0.5], "size": [0.15, 0.15, 0.15], "length": 0.0,
                "cone_angle": 30.0, "lean": 0.0, "angle": 0.0, "softness": 0.01, "enabled": True, "locked": False}

DEFAULT_SELECTION = {"point": [0.5, 0.5], "step": 0.003, "range": 0.0, "grow": 1.0, "softness": 1.0, "floors": False, "ramp": None,
                     "cycles": None, "invert": False, "enabled": True, "locked": False}

DEFAULT_REMOVE = {"point": [0.5, 0.5], "step": 0.003, "range": 0.0, "grow": 3.0, "softness": 1.0, "floors": False,
                  "invert": False, "enabled": True, "locked": False}

DEFAULT_OVERLAY = {"opacity": 0.5, "mode": "normal", "where": "all", "grey": False, "invert": False, "enabled": True}
OVERLAY_MODES = ("normal", "multiply", "screen", "soft_light")

DEFAULT_VIEW_SHIFT = {"x": 0.0, "y": 0.0, "strength": 0.05, "pivot": 0.5, "edges": "zoom"}

DEFAULT_EXAGGERATE = {"amount": 1.0, "size": 0.02, "enabled": True}

DEFAULT_LOCAL_CONTRAST = {"amount": 0.5, "range": 0.1, "compress": 0.0, "enabled": True}

DEFAULT_CONTOURS = {"cycles": 4.0, "strength": 0.85, "width": 2.0}

DEFAULT_FOCUS = {"depth": None, "at": None, "width": 0.1, "softness": 0.05, "strength": 1.0, "enabled": True,
                 "angle": 0.0, "lean": 0.0, "pivot": None, "reach_w": 0.0, "reach_h": 0.0, "reach_edge": 0.5,
                 "locked": False,  # locked: the GUI's lock, kept with the look; no effect on the picture
                 "shape": "plane", "fill": "shell", "size": [0.15, 0.15, 0.15], "length": 0.0, "cone_angle": 30.0,
                 "color": None, "color_blend": "NORMAL"}

DEFAULT_CONTROL = {"center": 0.5, "offset": 0.0, "feather": 0.02, "from_min": None, "from_max": None}

DEFAULT_BAND = {"id": "", "name": "", "enabled": True, "strength": 1.0, "interpolation": "LINEAR",
                "normal": [0.0, 0.0, 1.0], "center": [0.0, 0.0, 0.0], "width": 0.06, "softness": 0.06,
                "color": None, "color_blend": "NORMAL"}

DEFAULT_AREA = {"id": "", "name": "", "enabled": True, "mode": "ADD", "shape": "ELLIPSOID",
                "position": [0.0, 0.0, 0.1], "radius": [0.5, 0.5, 0.5],
                "basis": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                "reach_plus": [1.0, 1.0, 1.0], "reach_minus": [1.0, 1.0, 1.0],
                "strength": 1.0, "falloff": 0.5, "edge": 0.5, "group": "Group 1",
                "mask": {"strength": 1.0, "side": "HIDE", "edge": 0.1, "target": "ALL_BANDS"},
                "color": None, "color_blend": "NORMAL"}

DEFAULT_GROUP = {"strength": 1.0, "blend": 0.0, "color": None, "color_blend": "NORMAL"}

REPLACED_WHOLE = ("ramp",)  # given as a whole: {"preset": KEY [, "mirror": true]} or {"stops": [...], ...}

AREA_MODES = ("ADD", "MERGE", "MASK")
# add-on 1.0.96: how a band's / area's colour combines with the picture, applied in this order
COLOR_BLENDS = ("NORMAL", "MULTIPLY", "SCREEN", "OVERLAY", "SOFT_LIGHT", "COLOR")
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


def _check_region(r, where):
    """A selection's or removed part's fields, checked and made floats/bools in place."""
    pt = r["point"]
    if not (isinstance(pt, (list, tuple)) and len(pt) == 2 and all(0.0 <= float(v) <= 1.0 for v in pt)):
        raise ValueError(f"{where}.point must be [fx, fy], fractions of the picture (0-1)")
    r["point"] = [float(v) for v in pt]
    for key in ("step", "range", "grow", "softness"):
        r[key] = float(r[key])
    if not (0.0 < r["step"] <= 1.0 and 0.0 <= r["range"] <= 1.0 and 0.0 <= r["grow"] <= 50.0 and 0.0 <= r["softness"] <= 50.0):
        raise ValueError(f"{where}: step above 0 to 1, range 0-1, grow and softness 0-50 px")
    r["invert"], r["enabled"], r["floors"] = bool(r["invert"]), bool(r["enabled"]), bool(r["floors"])


def _check_color(d, where):
    """``color``: null (off: pushes toward white / black) or [r, g, b] 0-1 (linear, clamped like the add-on)."""
    c = d["color"]
    if c is not None:
        if not (isinstance(c, (list, tuple)) and len(c) == 3):
            raise ValueError(f"{where}.color must be null or [r, g, b] (0-1)")
        d["color"] = [min(max(float(v), 0.0), 1.0) for v in c]
    if d["color_blend"] not in COLOR_BLENDS:
        raise ValueError(f"{where}.color_blend must be one of {COLOR_BLENDS}")


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
        _check_color(b, f"bands[{i}]")
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
        _check_color(a, f"areas[{i}]")
    lk["groups"] = {str(k): _merge(DEFAULT_GROUP, v, f"groups[{k!r}]") for k, v in lk["groups"].items()}
    for k, g in lk["groups"].items():
        _check_color(g, f"groups[{k!r}]")
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
        c["remove"] = [_merge(DEFAULT_REMOVE, r, f"cut.remove[{i}]") for i, r in enumerate(c["remove"] or [])]
        for i, r in enumerate(c["remove"]):
            _check_region(r, f"cut.remove[{i}]")
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
        _check_color(fo, f"focus[{i}]")
    lk["selections"] = [_merge(DEFAULT_SELECTION, sel, f"selections[{i}]") for i, sel in enumerate(lk["selections"] or [])]
    for i, sel in enumerate(lk["selections"]):
        _check_region(sel, f"selections[{i}]")
        if sel["cycles"] is not None:
            sel["cycles"] = float(sel["cycles"])
            if not sel["cycles"] > 0:
                raise ValueError(f"selections[{i}].cycles must be > 0 (or null for the look's)")
        if sel["ramp"] is not None and not isinstance(sel["ramp"], dict):
            raise ValueError(f"selections[{i}].ramp must be a ramp ({{\"preset\": ...}} or stops) or null")
    if lk["overlay"] is not None:
        ov = lk["overlay"] = _merge(DEFAULT_OVERLAY, lk["overlay"], "overlay")
        ov["opacity"], ov["grey"], ov["invert"], ov["enabled"] = (float(ov["opacity"]), bool(ov["grey"]), bool(ov["invert"]),
                                                                  bool(ov["enabled"]))
        if not 0.0 <= ov["opacity"] <= 1.0 or ov["mode"] not in OVERLAY_MODES or ov["where"] not in ("all", "selections", "outside"):
            raise ValueError(f"overlay: opacity 0-1, mode one of {OVERLAY_MODES}, where all, selections or outside")
    if lk["view_shift"] is not None:
        vs = lk["view_shift"] = _merge(DEFAULT_VIEW_SHIFT, lk["view_shift"], "view_shift")
        for key in ("x", "y", "strength", "pivot"):
            vs[key] = float(vs[key])
        if not (-1.0 <= vs["x"] <= 1.0 and -1.0 <= vs["y"] <= 1.0 and 0.0 <= vs["strength"] <= 0.5 and 0.0 <= vs["pivot"] <= 1.0
                and vs["edges"] in ("zoom", "fill")):
            raise ValueError("view_shift: x and y -1 to 1, strength 0-0.5, pivot 0-1, edges zoom or fill")
    if lk["local_contrast"] is not None:
        lc = lk["local_contrast"] = _merge(DEFAULT_LOCAL_CONTRAST, lk["local_contrast"], "local_contrast")
        lc["amount"], lc["range"], lc["compress"] = float(lc["amount"]), float(lc["range"]), float(lc["compress"])
        lc["enabled"] = bool(lc["enabled"])
        if not (0.0 <= lc["amount"] <= 1.0 and 0.01 <= lc["range"] <= 0.5 and 0.0 <= lc["compress"] <= 0.9):
            raise ValueError("local_contrast: amount 0-1, range 0.01-0.5 of the depth range, compress 0-0.9")
    if lk["exaggerate"] is not None:
        ex = lk["exaggerate"] = _merge(DEFAULT_EXAGGERATE, lk["exaggerate"], "exaggerate")
        ex["amount"], ex["size"], ex["enabled"] = float(ex["amount"]), float(ex["size"]), bool(ex["enabled"])
        if not (0.0 <= ex["amount"] <= 10.0 and 0.002 <= ex["size"] <= 0.2):
            raise ValueError("exaggerate: amount 0-10, size 0.002-0.2 of the width")
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
