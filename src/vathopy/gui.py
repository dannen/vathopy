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
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

from . import io as VIO
from . import look as LOOK
from . import palettes as PAL
from .engine import focus_depth_at, focus_weights, render, smooth_depth
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
    """[{key, label, colours: [hex near ... far]}], the black/white fringe ramp first."""
    out = []
    for key in [FRINGE] + list(PRESETS):
        ramp = Ramp.from_dict(FRINGE_RAMP if key == FRINGE else {"preset": key})
        rgb = ramp.evaluate(np.linspace(1.0, 0.0, 9, dtype=np.float32))[:, :3]   # near -> far
        label = "Black/white fringe (EASE)" if key == FRINGE else PRESETS[key]["name"]
        mine = key != FRINGE and bool(PRESETS[key].get("user"))
        out.append({"key": key, "label": label + (" ★" if mine else ""), "user": mine,
                    "colours": [_hex(c) for c in _srgb8(rgb)]})
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
    lk = {"ramp": ramp, "equalize": bool(s.get("equalize"))}
    cycles = float(s.get("cycles", 0) or 0)
    if cycles > 0:
        lk["cycles"] = cycles
    contours = float(s.get("contours", 0) or 0)
    if contours > 0:
        lk["contours"] = {"cycles": contours, "strength": float(s.get("contour_strength", 0.85)),
                          "width": float(s.get("contour_width", 2.0))}
    lk["supersample"] = 1 if preview else int(s.get("supersample", 1))
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
                        "pivot": [float(fo["x"]), float(fo["y"])] if "x" in fo and "y" in fo else None}
                       for fo in s["focus"][:MAX_FOCUS]]
    return LOOK.normalise(lk)


def highlight(depth, lk):
    """The look without its focus, desaturated, with each focus's region tinted in its colour:
    top-down 8-bit sRGB. A focus's region is its band weight (1 = fully in focus), the same
    band the result uses."""
    plain = LOOK.normalise({**lk, "focus": [], "base": "RAMP"})
    out, _ = render(depth, plain)
    rgb = _srgb8(out[..., :3]).astype(np.float32) / 255.0
    rgb = 0.85 * (0.35 * rgb + 0.65 * rgb.mean(axis=-1, keepdims=True))
    for i, w in enumerate(focus_weights(depth, lk)):
        if w is None:
            continue                                     # switched off: no tint (it keeps its colour slot)
        k = min(abs(float(lk["focus"][i]["strength"])), 1.0)
        a = HIGHLIGHT_ALPHA * (0.2 + 0.8 * k) * np.clip(w[..., None], 0.0, 1.0)   # strength 0: only a faint tint
        rgb = rgb * (1.0 - a) + (np.asarray(FOCUS_COLOURS[i % len(FOCUS_COLOURS)], np.float32) / 255.0) * a
    return np.round(rgb[::-1] * 255).astype(np.uint8)


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
    return {"palette": palette, "invert": invert, "cycles": lk.get("cycles") or 0,
            "contours": c.get("cycles", 0), "contour_strength": c.get("strength", 0.85),
            "contour_width": c.get("width", 2.0), "smooth": lk.get("smooth_depth", 0),
            "supersample": lk.get("supersample", 1), "equalize": bool(lk.get("equalize")),
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
                                             "locked": bool(fo.get("locked", False)),
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
        with self._lock:
            if name not in self._prepped:
                raw, _ = VIO.read_image_channel0(str(self.folder / name))
                d, _ = VIO.prepare_depth(raw, "near-bright")
                self._prepped[name] = np.ascontiguousarray(d[::-1])        # bottom-up, like load_depth
            key = (name, round(float(smooth), 2))
            if smooth > 0 and key not in self._smoothed:
                if len(self._smoothed) > 24:
                    self._smoothed.clear()
                self._smoothed[key] = smooth_depth(self._prepped[name], smooth)
            return self._smoothed[key] if smooth > 0 else self._prepped[name]


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
                req = self._json_body()
                path = self.path.split("?", 1)[0]
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
                              {"X-Render-Ms": f"{ms:.0f}", "X-Size": f"{d.shape[1]}x{d.shape[0]}"})

        def _export(self, req):
            """The full-quality result as a 16-bit PNG download (nothing is written on the server)."""
            import cv2
            s = req.get("settings", {})
            d = maps.depth(req["map"], float(s.get("smooth", 0) or 0))
            out, _ = render(d, look_from_settings(s))
            codes = np.round(VIO.srgb_oetf(out[::-1, :, :3]) * 65535).astype(np.uint16)
            ok, png = cv2.imencode(".png", np.ascontiguousarray(codes[:, :, ::-1]))
            if not ok:
                raise ValueError("could not encode the PNG")
            return self._send(200, png.tobytes(), "image/png",
                              {"Content-Disposition": f'attachment; filename="{_out_name(req)}.png"'})

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
            VIO.write_png(out, str(png), 16)
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
            VIO.write_png(out, str(png), 16)
            return self._send(200, {"png": str(png), "look": str(look_path),
                                    "png_url": f"/api/file?name={png.name}",
                                    "look_url": f"/api/file?name=looks/{look_path.name}"})

    return Handler


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
