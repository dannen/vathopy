# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line: vathopy prep | render | probe | gui | init | presets."""

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from . import io as VIO
from . import look as LOOK
from . import palettes as PAL
from .engine import render
from .ramp import PRESETS


def _out_path(src, out, n_inputs, suffix, ext):
    stem = os.path.splitext(os.path.basename(src))[0]
    if out and (os.path.isdir(out) or n_inputs > 1 or out.endswith(os.sep)):
        os.makedirs(out, exist_ok=True)
        return os.path.join(out, f"{stem}{suffix}{ext}")
    if out:
        return out
    return os.path.join(os.path.dirname(src) or ".", f"{stem}{suffix}{ext}")


def cmd_prep(a):
    import numpy as np
    for src in a.inputs:
        raw, _info = VIO.read_image_channel0(src)
        depth, stats = VIO.prepare_depth(raw, a.kind, a.low, a.high)
        dst = _out_path(src, a.output, len(a.inputs), "_depth", "." + a.format)
        VIO.write_depth(np.ascontiguousarray(depth[::-1]), dst)
        print(f"{src} -> {dst}  ({depth.shape[1]}x{depth.shape[0]}, kind {stats['kind']}, "
              f"clipped {stats['clipped_px']} px, invalid {stats['invalid_px']} px)")


# --cycles with no ramp chosen: black/white with EASE, i.e. the N-cycle fringe.
FRINGE_RAMP = {"stops": [[0.0, [0.0, 0.0, 0.0, 1.0]], [1.0, [1.0, 1.0, 1.0, 1.0]]], "interpolation": "EASE"}


def quarter_steps(text):
    """argparse type: 1 to 10 in steps of 0.25."""
    v = float(text)
    if not (1.0 <= v <= 10.0 and (v * 4).is_integer()):
        raise argparse.ArgumentTypeError("must be 1 to 10 in steps of 0.25 (1, 1.25, 1.5, ... 10)")
    return v


def quarter_steps_or_auto(text):
    """argparse type: 'auto' (probe the depth map) or 1 to 10 in steps of 0.25."""
    if text == "auto":
        return text
    try:
        return quarter_steps(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be 'auto' or 1 to 10 in steps of 0.25") from None


SHAPE_FIELDS = ("size", "size_x", "size_y", "size_z", "length", "cone")
TEXT_FIELDS = ("shape", "fill")


def _shape_fields(fo):
    """size=/size_x= ... and cone= from a spec -> the look's size list and cone_angle."""
    if any(k in fo for k in ("size", "size_x", "size_y", "size_z")):
        s = fo.pop("size", 0.15)
        fo["size"] = [fo.pop(k, s) for k in ("size_x", "size_y", "size_z")]
    if "cone" in fo:
        fo["cone_angle"] = fo.pop("cone")
    return fo


def focus_spec(text):
    """argparse type: DEPTH[:WIDTH[:SOFTNESS[:STRENGTH]]] (fractions of the depth range, 0 = far,
    1 = near; strength -1 to 1) or @X,Y[:...] (the depth under that pixel, top-down). Any field can
    also be given by name, in any order after the first, e.g. 0.6:angle=135:lean=60:width=0.05, or
    0.5:shape=ellipsoid:size=0.2:fill=solid for a 3D shape."""
    head, *rest = text.split(":")
    names = ("width", "softness", "strength", "angle", "lean", "reach_w", "reach_h", "reach_edge", "reach") + SHAPE_FIELDS
    try:
        fo = {"at": [float(v) for v in head[1:].split(",")]} if head.startswith("@") else {"depth": float(head)}
        if "at" in fo and len(fo["at"]) != 2:
            raise ValueError
        positional = [t for t in rest if "=" not in t]
        if len(positional) > 3:
            raise ValueError
        fo.update(zip(names, (float(v) for v in positional)))
        for t in rest:
            if "=" in t:
                k, v = t.split("=", 1)
                if k not in names + TEXT_FIELDS:
                    raise ValueError
                fo[k] = v if k in TEXT_FIELDS else float(v)
        if "reach" in fo:                                    # reach= : the same fraction both ways
            r = fo.pop("reach")
            fo.setdefault("reach_w", r); fo.setdefault("reach_h", r)
        _shape_fields(fo)
        LOOK.normalise({"focus": [fo]})
    except ValueError:
        raise argparse.ArgumentTypeError(
            "must be DEPTH[:WIDTH[:SOFTNESS[:STRENGTH]]] (e.g. 0.8:0.1:0.05:0.6) or @X,Y[:...] (a pixel); "
            "fields can be named, e.g. @410,480:angle=135:lean=60") from None
    return fo


def keep_spec(text):
    """argparse type for --keep: DEPTH or @X,Y (the depth and place under that pixel), then named
    fields (shape=, size=, size_x/y/z=, length=, cone=, lean=, angle=, softness=, x=, y=): a shape
    the deletion slab leaves alone."""
    head, *rest = text.split(":")
    names = SHAPE_FIELDS + ("lean", "angle", "softness", "x", "y")
    try:
        k = {"at": [float(v) for v in head[1:].split(",")]} if head.startswith("@") else {"depth": float(head)}
        if "at" in k and len(k["at"]) != 2:
            raise ValueError
        for t in rest:
            key, v = t.split("=", 1)
            if key not in names + ("shape",):
                raise ValueError
            k[key] = v if key == "shape" else float(v)
        if "x" in k or "y" in k:
            k["pivot"] = [k.pop("x", 0.5), k.pop("y", 0.5)]
        _shape_fields(k)
        LOOK.normalise({"cut": {"keep": [k]}})
    except ValueError:
        raise argparse.ArgumentTypeError("must be DEPTH or @X,Y followed by named fields, "
                                         "e.g. @410,480:shape=ellipsoid:size=0.2") from None
    return k


def radius_or_auto(text):
    if text == "auto":
        return text
    try:
        return float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be 'auto' or a radius in px") from None



def _look_from_args(a):
    lk = LOOK.load(a.look) if a.look else LOOK.normalise({})
    look_has_ramp = False
    if a.look:
        look_has_ramp = "ramp" in LOOK.read_raw(a.look)
    if a.preset:
        lk["ramp"] = {"preset": a.preset}
    # "auto" values are filled in per depth map by _resolve_auto; placeholders here.
    if a.cycles is not None:
        lk["cycles"] = 1.0 if a.cycles == "auto" else a.cycles
        if not a.preset and not look_has_ramp:
            lk["ramp"] = FRINGE_RAMP
    if a.contours is not None:
        lk["contours"] = {"cycles": 1.0 if a.contours == "auto" else a.contours,
                          "strength": a.contour_strength, "width": a.contour_width}
    if a.supersample is not None:
        lk["supersample"] = a.supersample
    if a.smooth_depth is not None and a.smooth_depth != "auto":
        lk["smooth_depth"] = a.smooth_depth
    if a.equalize:
        lk["equalize"] = True
    if a.window:
        lk["window"] = "auto" if a.window == "auto" else [float(v) for v in a.window.split(",")]
    if a.focus:
        lk["focus"] = [{"strength": a.focus_strength, **fo} for fo in a.focus]   # a 4th field wins
    if a.base:
        lk["base"] = a.base
    if a.depth_scale is not None:
        lk["depth_scale"] = a.depth_scale
    if a.cut is not None or a.cut_near is not None or a.keep:
        far = a.cut if a.cut is not None else (1.0 if a.cut_near is None else 0.0)   # --keep alone: keep only those
        lk["cut"] = {"depth": far, "near": a.cut_near or 0.0, "softness": a.cut_softness,
                     "fill": a.cut_fill, "keep": a.keep or []}
    return LOOK.normalise(lk)


def _resolve_auto(a, lk, depth):
    """Fill 'auto' settings for one depth map from vathopy.probe. Returns (look, notes)."""
    from .probe import probe
    if "auto" not in (a.cycles, a.contours, a.smooth_depth):
        return lk, ""
    smooth = None if a.smooth_depth == "auto" else lk["smooth_depth"]
    pr = probe(depth, smooth=smooth, equalize=lk["equalize"])
    lk = json.loads(json.dumps(lk))
    notes = []
    if a.smooth_depth == "auto":
        lk["smooth_depth"] = pr["smooth_depth"]
        notes.append(f"smooth-depth {pr['smooth_depth']:g}")
    if a.cycles == "auto":
        lk["cycles"] = pr["cycles"]
        notes.append(f"cycles {pr['cycles']:g}")
    if a.contours == "auto":
        lk["contours"]["cycles"] = pr["contours"]
        notes.append(f"contours {pr['contours']:g}")
    return LOOK.normalise(lk), "auto: " + ", ".join(notes)


def cmd_probe(a):
    from .probe import probe
    for src in a.inputs:
        depth, _info = VIO.load_depth(src)
        pr = probe(depth, smooth=a.smooth_depth, equalize=a.equalize)
        print(f"{src}  ({depth.shape[1]}x{depth.shape[0]})")
        print(f"  depth wobble        {pr['wobble']:.5f} of the range (after smooth-depth {pr['smooth_depth']:g})")
        print(f"  most levels         {pr['max_levels']:.1f} before contour lines start to tangle")
        print(f"  recommended         --smooth-depth {pr['smooth_depth']:g}  --contours {pr['contours']:g}"
              f"  or  --cycles {pr['cycles']:g}")
        print(f"  depth spread        middle half of the pixels spans {100 * pr['spread']:.0f}% of the range"
              + ("  -> try --equalize" if pr["suggest_equalize"] else ""))


def cmd_render(a):
    lk_args = _look_from_args(a)
    ext = ".exr" if a.format == "exr" else ".png"
    suffix = "_" + (os.path.splitext(os.path.basename(a.look))[0] if a.look else (a.preset or "vathograph").lower())
    if a.cycles is not None:
        suffix += "_autocyc" if a.cycles == "auto" else f"_{a.cycles:g}cyc"
    if a.contours is not None:
        suffix += "_autocontours" if a.contours == "auto" else f"_{a.contours:g}contours"
    if a.focus:
        suffix += "_focus"
    photo = VIO.read_photo(a.photo) if a.photo else None
    for src in a.inputs:
        depth, info = VIO.load_depth(src)
        if info["eight_bit"]:
            print(f"warning: {src} is 8-bit; depth steps can show as bands (use a 16-bit PNG or float EXR)",
                  file=sys.stderr)
        lk, auto_note = _resolve_auto(a, lk_args, depth)
        if auto_note:
            print(f"{src}: {auto_note}")
        if a.overlay is not None:
            lk = LOOK.normalise({**lk, "overlay": {**(lk["overlay"] or LOOK.DEFAULT_OVERLAY), "opacity": a.overlay}})
        if lk["overlay"] is not None and lk["overlay"]["opacity"] > 0 and photo is None:
            print(f"{src}: the look has a photo overlay; give the photo with --photo to see it", file=sys.stderr)
        out, rinfo = render(depth, lk, photo)
        dst = _out_path(src, a.output, len(a.inputs), suffix, ext)
        if a.format == "exr":
            VIO.write_exr(out, dst)
        else:
            VIO.write_png(out, dst, 8 if a.format == "png8" else 16)
        print(f"{src} -> {dst}  ({rinfo['size'][0]}x{rinfo['size'][1]}, window "
              f"[{rinfo['window'][0]:.4f}, {rinfo['window'][1]:.4f}], {rinfo['terms']} band/area term(s), "
              f"{rinfo['seconds']:.2f} s)")
        if rinfo["dead_masks"]:
            print(f"warning: masks with no target: {rinfo['dead_masks']}", file=sys.stderr)


def cmd_relief(a):
    """Depth maps -> 3D-printable STL: a relief (near raised) or, with --lithophane, the rendered look as
    a lithophane (dark = thick)."""
    import numpy as np
    from . import mesh
    from .engine import orient, render, smooth_depth
    lk = LOOK.load(a.look) if a.look else LOOK.normalise({})
    if a.preset:
        lk["ramp"] = {"preset": a.preset}
    for src in a.inputs:
        depth, _ = VIO.load_depth(src)                                  # rows bottom-up
        w_src = depth.shape[1] if not lk["orientation"]["turns"] % 2 else depth.shape[0]
        width_mm = a.width_mm if a.wrap == "flat" else a.radius_mm * np.radians(a.arc_deg)   # wrapped: round the outside
        cols = min(a.px or round(width_mm / 0.2) + 1, w_src)            # 0.2 mm a point; never more than the map has
        if a.lithophane:
            out, _ = render(depth, lk)                                  # the picture, turned as the look says
            srgb = np.clip(VIO.srgb_oetf(out[::-1, :, :3]), 0.0, 1.0)
            top, base = mesh.lithophane_heights(srgb, cols, a.min_mm, a.max_mm), a.min_mm
        else:
            sm = lk["smooth_depth"] if a.smooth_depth is None else a.smooth_depth
            if sm > 0:
                depth = smooth_depth(depth, sm)
            top = mesh.relief_heights(orient(depth[::-1], lk["orientation"]), cols, a.relief_mm, a.invert)
            base = a.base_mm
        if a.wrap == "cylinder":                                        # --base-mm is the wall behind the relief
            verts, faces = mesh.cylinder_mesh(top, a.radius_mm, base, a.arc_deg)
        elif a.wrap == "sphere":
            verts, faces = mesh.sphere_mesh(top, a.radius_mm, base, a.arc_deg, a.lat_deg)
        else:
            verts, faces = mesh.heightfield_mesh(top, a.width_mm / (top.shape[1] - 1), base)
        suffix = ("_lithophane" if a.lithophane else "_relief") + ("" if a.wrap == "flat" else f"_{a.wrap}")
        dst = _out_path(src, a.output, len(a.inputs), suffix, ".stl")
        mesh.write_stl(dst, verts, faces)
        ext = verts.max(axis=0) - verts.min(axis=0)
        print(f"{src} -> {dst}  ({ext[0]:.1f} x {ext[1]:.1f} x {ext[2]:.1f} mm, "
              f"{top.shape[1]}x{top.shape[0]} points, {len(faces):,} triangles, {os.path.getsize(dst) / 1e6:.1f} MB)")


def cmd_init(a):
    lk = LOOK.normalise({"ramp": {"preset": a.preset}} if a.preset else {})
    if os.path.exists(a.path) and not a.force:
        sys.exit(f"{a.path} exists (use --force to overwrite)")
    LOOK.save(lk, a.path)
    print(f"wrote {a.path}")


def cmd_presets(a):
    for k, e in PRESETS.items():
        mine = f"  [yours: {e['source']}]" if e.get("user") else ""
        print(f"{k:24s} {e['interpolation']:9s} {e['desc']}{mine}")


def cmd_check_palettes(a):
    folder = Path(a.folder or a.palettes).expanduser()
    if not folder.is_dir():
        sys.exit(f"no palette folder at {folder}")
    _found, report = PAL.read_folder(folder)
    if not report:
        print(f"{folder}: no palette files (.png .jpg .jpeg .json .gpl)")
    width = max((len(f) for f, _, _ in report), default=0)
    for fname, status, detail in report:
        print(f"{status.upper():9s} {fname:{width}s}  {detail}")
    bad = sum(status != "ok" for _, status, _ in report)
    print(f"{len(report) - bad} ok, {bad} rejected or skipped")
    if bad:
        sys.exit(1)


def main(argv=None):
    # Your palettes join the presets before the parser lists them as choices.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--palettes", default=PAL.default_dir())
    PAL.register(pre.parse_known_args(argv)[0].palettes)

    p = argparse.ArgumentParser(prog="vathopy", description="Vathography without Blender: depth map + look -> picture")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--palettes", default=PAL.default_dir(), metavar="DIR",
                   help="folder of your own palettes (images, .json, .gpl), added to the presets; default "
                        "$VATHOPY_PALETTES or ~/Pictures/vathography/palettes (give it before the command)")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("prep", help="raw Marigold prediction (.npy/.exr/.png) -> clean near-bright depth map")
    q.add_argument("inputs", nargs="+")
    q.add_argument("-o", "--output", help="output file, or a folder for several inputs")
    q.add_argument("--kind", default="log", choices=VIO.KINDS,
                   help="what the values mean: log/linear depth (default log), disparity, near-bright, far-bright")
    q.add_argument("--low", type=float, default=0.1, help="low clip percentile (default 0.1)")
    q.add_argument("--high", type=float, default=99.9, help="high clip percentile (default 99.9)")
    q.add_argument("--format", default="exr", choices=("exr", "png", "npy"), help="exr (float, default) or 16-bit png")
    q.set_defaults(fn=cmd_prep)

    r = sub.add_parser("render", help="render a look onto depth maps")
    r.add_argument("inputs", nargs="+", help="depth maps, near = bright (.exr/.png/.tif/.npy)")
    r.add_argument("-l", "--look", help="look JSON, settings exported from the GUI, or a PNG/JPG the GUI saved (default: a plain ramp)")
    r.add_argument("-o", "--output", help="output file, or a folder for several inputs")
    r.add_argument("-f", "--format", default="png16", choices=("png16", "png8", "exr"))
    r.add_argument("--preset", choices=list(PRESETS), help="override the look's ramp with a preset")
    r.add_argument("--window", help="'auto' or 'lo,hi' to override the look's ramp window")
    r.add_argument("--base", choices=LOOK.BASES, help="override what bands/areas sit on")
    r.add_argument("--cycles", type=quarter_steps_or_auto, metavar="N",
                   help="repeat the ramp N times across the depth (1-10 in steps of 0.25, or 'auto' "
                        "to probe each depth map); with no --preset or look ramp this is the "
                        "black/white N-cycle fringe")
    r.add_argument("--contours", type=quarter_steps_or_auto, metavar="N",
                   help="draw dark contour lines at N even steps through the depth (1-10 in steps of "
                        "0.25, or 'auto' to probe each depth map) over the ramp's colours")
    r.add_argument("--contour-strength", type=float, default=0.85, metavar="S",
                   help="how dark the contour lines are, 0-1 (default 0.85)")
    r.add_argument("--contour-width", type=float, default=2.0, metavar="PX",
                   help="contour line thickness in pixels, the same everywhere (default 2)")
    r.add_argument("--supersample", type=int, choices=(1, 2, 3, 4), metavar="N",
                   help="anti-aliasing: render at N x the resolution (1-4) and average down; smoother "
                        "contour lines and edges, about N*N x the time")
    r.add_argument("--smooth-depth", type=radius_or_auto, metavar="R",
                   help="edge-preserving smoothing of the depth first, radius R px (e.g. 2-6, or 'auto' "
                        "for one in proportion to the picture size); contours follow surfaces instead "
                        "of depth noise")
    r.add_argument("--equalize", action="store_true",
                   help="spread the ramp by pixel count, so each colour band and contour level covers "
                        "about the same share of the picture")
    r.add_argument("--focus", type=focus_spec, action="append", metavar="SPEC",
                   help="creative focus: lift one depth to white. DEPTH[:WIDTH[:SOFTNESS[:STRENGTH]]] as "
                        "fractions of the depth range (0 = far, 1 = near; default width 0.1, softness 0.05; "
                        "strength -1 to 1, default --focus-strength), or @X,Y[:...] for the depth under "
                        "that pixel. Fields can be named (width=, softness=, strength=, angle=, lean=, "
                        "reach_w=, reach_h=, reach=, reach_edge=); reach confines the focus to an oval "
                        "around the point (half-size as a fraction of the picture), isolating one subject; "
                        "lean (-90 to 90 degrees) tilts the band so it cuts across the scene at an angle, angle "
                        "(degrees, counter-clockwise from the right) turns it. shape=ellipsoid|box|cylinder|cone "
                        "with size=, size_x/y/z=, length=, cone=, fill=shell|solid makes it a 3D shape. "
                        "Repeat for several distances. "
                        "With --base BLACK: a white subject on black")
    r.add_argument("--depth-scale", type=float, metavar="S",
                   help="how deep the scene is relative to its width, for shape focuses and --keep (default 1)")
    r.add_argument("--cut", type=float, metavar="C",
                   help="the deletion slab: remove everything farther than C (0 = far .. 1 = near, as a fraction "
                        "of the depth range; 1 removes everything but --keep shapes)")
    r.add_argument("--cut-near", type=float, metavar="N",
                   help="the same from the near side: remove everything nearer than 1 - N (0 = nothing .. 1 = everything)")
    r.add_argument("--cut-softness", type=float, default=0.0, metavar="S", help="fade the cut's edges over S (default 0)")
    r.add_argument("--cut-fill", choices=("black", "white"), default="black", help="what removed parts become")
    r.add_argument("--keep", type=keep_spec, action="append", metavar="SPEC",
                   help="a shape the cut leaves alone: DEPTH or @X,Y, then shape=ellipsoid|box|cylinder|cone, "
                        "size= (or size_x/y/z=), length=, cone=, lean=, angle=, softness=; sizes are fractions of "
                        "the picture's width. Repeatable; without --cut, everything else is removed")
    r.add_argument("--photo", metavar="FILE", help="the photo the depth map was made from, for the look's overlay "
                   "(resized to the map; give one depth map per run)")
    r.add_argument("--overlay", type=float, metavar="A", help="blend the --photo over the picture at opacity A (0-1)")
    r.add_argument("--focus-strength", type=float, default=1.0, metavar="K",
                   help="-1 to 1 (default 1) for every --focus without its own STRENGTH: 1 lifts the "
                        "focus to white, -1 sinks it to black (use with --base WHITE for black on white)")
    r.set_defaults(fn=cmd_render)

    rl = sub.add_parser("relief", help="make a 3D-printable STL: a depth relief, or a lithophane of a look")
    rl.add_argument("inputs", nargs="+", help="depth maps, near = bright (as for render)")
    rl.add_argument("-o", "--output", help="output .stl, or a folder for several inputs")
    rl.add_argument("-l", "--look", help="a look (or a PNG/JPG the GUI saved): its orientation and smoothing, "
                                         "and for --lithophane the picture itself")
    rl.add_argument("--preset", choices=list(PRESETS), help="override the look's ramp (for --lithophane)")
    rl.add_argument("--width-mm", type=float, default=100.0, metavar="MM", help="the print's width (default 100)")
    rl.add_argument("--px", type=int, metavar="N",
                    help="points across the width (default one every 0.2 mm, at most the map's own pixels)")
    rl.add_argument("--relief-mm", type=float, default=8.0, metavar="MM",
                    help="height from the farthest to the nearest point (default 8)")
    rl.add_argument("--base-mm", type=float, default=2.0, metavar="MM",
                    help="solid base under the relief, or with --wrap the wall behind it (default 2)")
    rl.add_argument("--wrap", choices=("flat", "cylinder", "sphere"), default="flat",
                    help="wrap the picture round a cylinder (its width round the axis, ends joined at 360) or a sphere")
    rl.add_argument("--radius-mm", type=float, default=40.0, metavar="MM",
                    help="--wrap: the radius the relief stands out from (default 40); the wall is inside it")
    rl.add_argument("--arc-deg", type=float, default=360.0, metavar="DEG",
                    help="--wrap: how far round the picture's width goes (default 360, all the way)")
    rl.add_argument("--lat-deg", type=float, metavar="DEG",
                    help="--wrap sphere: how far from top to bottom (180 = pole to pole; default the picture's proportions)")
    rl.add_argument("--invert", action="store_true", help="far raised instead of near (a mould)")
    rl.add_argument("--smooth-depth", type=float, metavar="R", help="smooth the depth first (px; default the look's)")
    rl.add_argument("--lithophane", action="store_true",
                    help="print the rendered look as a lithophane: dark parts thick, light thin, seen lit from behind")
    rl.add_argument("--min-mm", type=float, default=0.8, metavar="MM", help="lithophane: thinnest (lightest), default 0.8")
    rl.add_argument("--max-mm", type=float, default=3.0, metavar="MM", help="lithophane: thickest (darkest), default 3")
    rl.set_defaults(fn=cmd_relief)

    pb = sub.add_parser("probe", help="measure a depth map and recommend contour/cycle/smoothing settings")
    pb.add_argument("inputs", nargs="+", help="depth maps, near = bright")
    pb.add_argument("--smooth-depth", type=float, metavar="R",
                    help="the smoothing you'll render with (default: the recommended radius)")
    pb.add_argument("--equalize", action="store_true", help="measure as it will render with --equalize")
    pb.set_defaults(fn=cmd_probe)

    g = sub.add_parser("gui", help="local web app: try palettes, cycles and contours on depth maps with sliders")
    g.add_argument("--depth-dir", default="~/Pictures/vathography/depth",
                   help="folder of depth maps (.exr, raw Marigold output, near = bright); default ~/Pictures/vathography/depth")
    g.add_argument("--favourites", default="~/Pictures/vathography/favorites",
                   help="where Save writes PNGs and looks/*.json; default ~/Pictures/vathography/favorites")
    g.add_argument("--host", default="127.0.0.1", help="listen address (default 127.0.0.1; no authentication)")
    g.add_argument("--port", type=int, default=8770, help="port (default 8770)")
    g.add_argument("--preview-size", type=int, default=1280, help="max preview size in px while dragging (default 1280)")
    g.add_argument("--photos", metavar="DIR",
                   help="where the photos the depth maps were made from are (searched with subfolders, for Photo overlay); "
                        "default: the folder 'input' beside the depth-map folder")
    g.set_defaults(fn=lambda a: __import__("vathopy.gui", fromlist=["serve"]).serve(
        a.depth_dir, a.favourites, a.host, a.port, a.preview_size, a.palettes, a.photos))

    i = sub.add_parser("init", help="write a starter look JSON")
    i.add_argument("path")
    i.add_argument("--preset", choices=list(PRESETS))
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_init)

    c = sub.add_parser("check-palettes", help="check your palette files (safe, readable) and report each one")
    c.add_argument("folder", nargs="?", help="folder to check (default: the --palettes folder)")
    c.set_defaults(fn=cmd_check_palettes)

    s = sub.add_parser("presets", help="list the ramp presets")
    s.set_defaults(fn=cmd_presets)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
