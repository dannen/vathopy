# SPDX-License-Identifier: GPL-3.0-or-later
"""``vathopy gui``: a small local web app for trying looks on depth maps with sliders.

Python standard library HTTP server + vathopy; the page is gui.html (plain JavaScript, no
external libraries). It lists the depth maps in one folder (raw Marigold/ComfyUI output,
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
from .engine import focus_depth_at, focus_weights, orient, render, smooth_depth
from .probe import probe
from .ramp import PRESETS, Ramp

HTML = Path(__file__).with_name("gui.html")
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


def look_from_settings(s, preview=False):
    """Page settings -> a vathopy look dict."""
    key = s.get("palette", FRINGE)
    if key != FRINGE and key not in PRESETS:
        raise ValueError(f"unknown palette {key!r}")
    if key == FRINGE:
        ramp = json.loads(json.dumps(FRINGE_RAMP))
        if s.get("invert"):
            ramp["stops"] = [[0.0, [1.0, 1.0, 1.0, 1.0]], [1.0, [0.0, 0.0, 0.0, 1.0]]]
    else:
        ramp = {"preset": key, "mirror": True} if s.get("invert") else {"preset": key}
    shift = int(round(float(s.get("shift", 0) or 0)))   # the palette's colours moved along its stops
    if shift:
        ramp["shift"] = shift
    lk = {"ramp": ramp, "equalize": bool(s.get("equalize"))}
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
    cut = float(s.get("cut_depth", 0) or 0)
    if cut > 0 or keep:
        lk["cut"] = {"depth": cut, "softness": float(s.get("cut_softness", 0) or 0),
                     "fill": s.get("cut_fill") or "black", "keep": keep}
    o = s.get("orientation") or {}
    lk["orientation"] = {"turns": int(o.get("turns", 0) or 0), "mirror": bool(o.get("mirror"))}
    style = s.get("focus_style", "off")
    if style != "off" and s.get("focus"):
        if style not in FOCUS_STYLES:
            raise ValueError(f"unknown focus style {style!r}")
        lk["base"], sign = FOCUS_STYLES[style]           # each focus's strength slider is 0-1; the style sets the sign
        lk["focus"] = [{"depth": float(fo["depth"]), "width": float(fo["width"]), "softness": float(fo["softness"]),
                        "strength": sign * min(max(float(fo.get("strength", 1.0)), 0.0), 1.0),
                        "enabled": bool(fo.get("enabled", True)),
                        "angle": float(fo.get("angle", 0.0)), "lean": float(fo.get("lean", 0.0)),
                        "reach_w": float(fo.get("reach_w", 0.0)), "reach_h": float(fo.get("reach_h", 0.0)),
                        "reach_edge": float(fo.get("reach_edge", 0.5)), "locked": bool(fo.get("locked", False)),
                        "pivot": [float(fo["x"]), float(fo["y"])] if "x" in fo and "y" in fo else None,
                        **_shape_from_page(fo, "plane")}
                       for fo in s["focus"][:MAX_FOCUS]]
    return LOOK.normalise(lk)


def highlight(depth, lk):
    """The look without its focus, desaturated, with each focus's region tinted in its colour:
    top-down 8-bit sRGB. A focus's region is its band weight (1 = fully in focus), the same
    band the result uses."""
    plain = LOOK.normalise({**lk, "focus": [], "base": "RAMP", "orientation": {"turns": 0, "mirror": False}})
    out, _ = render(depth, plain)
    rgb = _srgb8(out[..., :3]).astype(np.float32) / 255.0
    rgb = 0.85 * (0.35 * rgb + 0.65 * rgb.mean(axis=-1, keepdims=True))
    for i, w in enumerate(focus_weights(depth, lk)):
        if w is None:
            continue                                     # switched off: no tint (it keeps its colour slot)
        k = min(abs(float(lk["focus"][i]["strength"])), 1.0)
        a = HIGHLIGHT_ALPHA * (0.2 + 0.8 * k) * np.clip(w[..., None], 0.0, 1.0)   # strength 0: only a faint tint
        rgb = rgb * (1.0 - a) + (np.asarray(FOCUS_COLOURS[i % len(FOCUS_COLOURS)], np.float32) / 255.0) * a
    return orient(np.round(rgb[::-1] * 255).astype(np.uint8), lk["orientation"])   # the tints are in map space


def settings_from_look(lk):
    """A saved look -> page settings (best effort; unknown ramps fall back to the fringe)."""
    ramp = lk.get("ramp", {})
    if "preset" in ramp:
        palette, invert = ramp["preset"], bool(ramp.get("mirror"))
    else:
        stops = ramp.get("stops", [])
        palette = FRINGE
        invert = bool(stops) and stops[0][1][:3] == [1.0, 1.0, 1.0]
    c = lk.get("contours") or {}
    return {"palette": palette, "invert": invert, "shift": ramp.get("shift", 0), "cycles": lk.get("cycles") or 0,
            "contours": c.get("cycles", 0), "contour_strength": c.get("strength", 0.85) if c else 0,
            "contour_width": c.get("width", 2.0), "smooth": lk.get("smooth_depth", 0),
            "supersample": lk.get("supersample", 1), "equalize": bool(lk.get("equalize")),
            "orientation": dict(lk.get("orientation") or {"turns": 0, "mirror": False}),
            "depth_scale": float(lk.get("depth_scale", 1.0)), **_cut_settings(lk),
            **_focus_settings(lk)}


def _slider_lean_angle(lean, angle):
    """Any lean/angle as the page's sliders show it: angle -90..90, with the lean's sign giving the
    other half of the circle (lean L at angle A is the same band as -L at A + 180)."""
    lean, angle = float(lean), (float(angle) + 180.0) % 360.0 - 180.0      # -180 .. 180
    if angle > 90.0:
        lean, angle = -lean, angle - 180.0
    elif angle < -90.0:
        lean, angle = -lean, angle + 180.0
    return lean, angle


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
    return {"cut_depth": float(c.get("depth", 0.0)), "cut_softness": float(c.get("softness", 0.0)),
            "cut_fill": c.get("fill", "black"),
            "keep": [{"depth": float(k["depth"]), "x": k.get("pivot", [0.5, 0.5])[0], "y": k.get("pivot", [0.5, 0.5])[1],
                      "lean": float(k.get("lean", 0.0)), "angle": float(k.get("angle", 0.0)),
                      "softness": float(k.get("softness", 0.01)), "enabled": bool(k.get("enabled", True)),
                      "locked": bool(k.get("locked", False)), **_shape_to_page(k, "ellipsoid")}
                     for k in c.get("keep") or []]}


def _focus_settings(lk):
    focus = [fo for fo in lk.get("focus") or [] if fo.get("depth") is not None]
    if not focus:
        return {"focus_style": "off", "focus": []}
    key = (lk.get("base", "RAMP"), 1.0 if float(focus[0].get("strength", 1.0)) >= 0 else -1.0)
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
                                             **({"x": fo["pivot"][0], "y": fo["pivot"][1]} if fo.get("pivot") else {})}
                                            for fo in focus]}


class Maps:
    """Depth maps in one folder, prepped and smoothed on demand, cached in memory."""

    def __init__(self, folder):
        self.folder = Path(folder).expanduser()
        self._lock = threading.Lock()
        self._prepped = {}
        self._smoothed = {}

    def list(self):
        """Depth maps only: normals/albedo outputs and bench.py runs are left out."""
        skip = ("normals", "albedo", "bench_")
        return sorted(p.name for p in self.folder.glob("*.exr")      # regular files only: no symlinks
                      if p.is_file() and not p.is_symlink() and not any(k in p.name for k in skip))

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
                if path == "/api/maps":
                    return self._send(200, {"folder": str(maps.folder), "maps": maps.list()})
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
            if req.get("view") == "highlight" and lk["focus"]:
                img = highlight(d, lk)
            else:
                out, _ = render(d, lk)
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
            out, _ = render(d, look_from_settings(s))
            return self._send(200, _png16(out, req), "image/png",
                              {"Content-Disposition": f'attachment; filename="{_out_name(req)}.png"'})

        def _stl(self, req):
            """The map as a 3D-printable STL download: a relief of the depth, or a lithophane of the
            picture, cropped (if asked) and turned like the picture on the page."""
            from . import mesh
            s = req.get("settings", {})
            o = s.get("orientation") or {}
            o = {"turns": int(o.get("turns", 0) or 0), "mirror": bool(o.get("mirror"))}
            depth = maps.depth(req["map"], float(s.get("smooth", 0) or 0))          # rows bottom-up
            if req.get("mode") == "lithophane":
                lk = look_from_settings({**s, "orientation": {}})
                out, _ = render(depth, lk)
                td = np.clip(VIO.srgb_oetf(out[::-1, :, :3]), 0.0, 1.0)              # the picture, top-down
            else:
                td = depth[::-1]
            crop = req.get("crop") or {}
            t, r, b, l = (int(crop.get(k, 0) or 0) for k in ("top", "right", "bottom", "left"))
            h, w = td.shape[:2]
            if t + b >= h or l + r >= w:
                raise ValueError("the crop leaves nothing")
            td = orient(td[t:h - b, l:w - r], o)
            width_mm = float(req.get("width_mm", 100))
            cols = min(int(req.get("px") or round(width_mm / 0.2) + 1), td.shape[1])
            if req.get("mode") == "lithophane":
                lo, hi = float(req.get("min_mm", 0.8)), float(req.get("max_mm", 3.0))
                top, base = mesh.lithophane_heights(td, cols, lo, hi), lo
            else:
                top = mesh.relief_heights(td, cols, float(req.get("relief_mm", 8)), bool(req.get("invert")))
                base = float(req.get("base_mm", 2))
            if not (width_mm > 0 and base >= 0 and cols >= 2):
                raise ValueError("width and base must be above 0, and at least 2 points across")
            px_mm = width_mm / (top.shape[1] - 1)
            verts, faces = mesh.heightfield_mesh(top, px_mm, base)
            name = f"{_out_name(req)}_{'lithophane' if req.get('mode') == 'lithophane' else 'relief'}.stl"
            return self._send(200, mesh.stl_bytes(verts, faces), "model/stl",
                              {"Content-Disposition": f'attachment; filename="{name}"',
                               "X-Triangles": str(len(faces)), "X-Points": f"{top.shape[1]}x{top.shape[0]}",
                               "X-Size-MM": f"{width_mm:g}x{px_mm * (top.shape[0] - 1):.1f}x{float(verts[:, 2].max()):.1f}"})

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
            out, _ = render(d, look_from_settings(s))
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
            out, _ = render(maps.depth(mapname), LOOK.normalise(lk))
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


def serve(depth_dir, favourites, host="127.0.0.1", port=8770, preview_size=1280, palettes_dir=None):
    maps = Maps(depth_dir)
    palettes_dir = palettes_dir or PAL.default_dir()
    PAL.register(palettes_dir)
    auth = os.environ.get("VATHOPY_GUI_AUTH") or None
    if auth and ":" not in auth:
        raise SystemExit("VATHOPY_GUI_AUTH must be user:password")
    if host not in ("127.0.0.1", "localhost", "::1") and not auth:
        print("warning: listening on a network address with no VATHOPY_GUI_AUTH set", flush=True)
    httpd = ThreadingHTTPServer((host, port), make_handler(maps, favourites, preview_size, auth, palettes_dir))
    print(f"vathopy gui on http://{host}:{port}/  (depth maps: {maps.folder}, {len(maps.list())} found; "
          f"saves to {Path(favourites).expanduser()}; your palettes: {Path(palettes_dir).expanduser()}; "
          + ("password required)" if auth else "no password)"), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
