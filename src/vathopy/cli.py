# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line: vathopy prep | render | probe | gui | init | presets."""

import argparse
import json
import os
import sys

from . import __version__
from . import io as VIO
from . import look as LOOK
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


def half_steps(text):
    """argparse type: 1 to 10 in steps of 0.5."""
    v = float(text)
    if not (1.0 <= v <= 10.0 and (v * 2).is_integer()):
        raise argparse.ArgumentTypeError("must be 1 to 10 in steps of 0.5 (1, 1.5, 2, ... 10)")
    return v


def half_steps_or_auto(text):
    """argparse type: 'auto' (probe the depth map) or 1 to 10 in steps of 0.5."""
    if text == "auto":
        return text
    try:
        return half_steps(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be 'auto' or 1 to 10 in steps of 0.5") from None


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
        with open(a.look, encoding="utf-8") as fh:
            look_has_ramp = "ramp" in json.load(fh)
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
    if a.base:
        lk["base"] = a.base
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
    for src in a.inputs:
        depth, info = VIO.load_depth(src)
        if info["eight_bit"]:
            print(f"warning: {src} is 8-bit; depth steps can show as bands (use a 16-bit PNG or float EXR)",
                  file=sys.stderr)
        lk, auto_note = _resolve_auto(a, lk_args, depth)
        if auto_note:
            print(f"{src}: {auto_note}")
        out, rinfo = render(depth, lk)
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


def cmd_init(a):
    lk = LOOK.normalise({"ramp": {"preset": a.preset}} if a.preset else {})
    if os.path.exists(a.path) and not a.force:
        sys.exit(f"{a.path} exists (use --force to overwrite)")
    LOOK.save(lk, a.path)
    print(f"wrote {a.path}")


def cmd_presets(a):
    for k, e in PRESETS.items():
        print(f"{k:24s} {e['interpolation']:9s} {e['desc']}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="vathopy", description="Vathography without Blender: depth map + look -> picture")
    p.add_argument("--version", action="version", version=__version__)
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
    r.add_argument("-l", "--look", help="look JSON (default: a plain ramp)")
    r.add_argument("-o", "--output", help="output file, or a folder for several inputs")
    r.add_argument("-f", "--format", default="png16", choices=("png16", "png8", "exr"))
    r.add_argument("--preset", choices=list(PRESETS), help="override the look's ramp with a preset")
    r.add_argument("--window", help="'auto' or 'lo,hi' to override the look's ramp window")
    r.add_argument("--base", choices=LOOK.BASES, help="override what bands/areas sit on")
    r.add_argument("--cycles", type=half_steps_or_auto, metavar="N",
                   help="repeat the ramp N times across the depth (1-10 in steps of 0.5, or 'auto' "
                        "to probe each depth map); with no --preset or look ramp this is the "
                        "black/white N-cycle fringe")
    r.add_argument("--contours", type=half_steps_or_auto, metavar="N",
                   help="draw dark contour lines at N even steps through the depth (1-10 in steps of "
                        "0.5, or 'auto' to probe each depth map) over the ramp's colours")
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
    r.set_defaults(fn=cmd_render)

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
    g.set_defaults(fn=lambda a: __import__("vathopy.gui", fromlist=["serve"]).serve(
        a.depth_dir, a.favourites, a.host, a.port, a.preview_size))

    i = sub.add_parser("init", help="write a starter look JSON")
    i.add_argument("path")
    i.add_argument("--preset", choices=list(PRESETS))
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_init)

    s = sub.add_parser("presets", help="list the ramp presets")
    s.set_defaults(fn=cmd_presets)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
