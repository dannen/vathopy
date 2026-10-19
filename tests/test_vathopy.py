"""Unit tests that run without Blender (pytest)."""
import json
import os
import subprocess
import sys

import numpy as np
import pytest

import vathopy
from vathopy import io as VIO
from vathopy.ramp import Ramp, preset, PRESETS


def depth_field(h=90, w=120):
    yy, xx = np.mgrid[0:h, 0:w]
    return (0.2 + 0.6 * yy / h + 0.1 * np.sin(xx / 7.0)).astype(np.float32)  # bottom-up, near = bright


def test_presets_all_load():
    assert len(PRESETS) == 41  # the add-on's 19 + vathopy's 8 complementary, 13 colour-to-black and MIST
    for k in PRESETS:
        r = preset(k)
        v = r.evaluate(np.linspace(0, 1, 11))
        assert v.shape == (11, 4) and np.all(np.isfinite(v))


def test_linear_ramp_identity():
    r = Ramp([(0.0, (0, 0, 0, 1)), (1.0, (1, 1, 1, 1))])
    x = np.linspace(0, 1, 101, dtype=np.float32)
    assert np.allclose(r.evaluate(x)[:, 0], x, atol=1e-6)


def test_plain_render_is_the_ramped_depth():
    d = depth_field()
    out, info = vathopy.render(d, {"ramp": {"preset": "WHITE_TO_BLACK"}})
    lo, hi = d.min(), d.max()
    srgb_linear = np.asarray([vathopy.ramp.srgb_to_linear(v) for v in (0.0, 1.0)])
    expect = srgb_linear[0] + (d - lo) / (hi - lo) * (srgb_linear[1] - srgb_linear[0])
    assert info["window"] == [pytest.approx(lo), pytest.approx(hi)]
    assert np.abs(out[..., 0] - expect).max() < 1e-4


def test_flip_and_near_left_cancel():
    d = depth_field()
    a, _ = vathopy.render(d, {"ramp": {"preset": "FRINGE_2_WB"}})
    b, _ = vathopy.render(d, {"ramp": {"preset": "FRINGE_2_WB"}, "flip": True, "ramp_near_left": True})
    assert np.abs(a - b).max() < 1e-5


def test_add_area_brightens_only_inside():
    d = np.full((80, 80), 0.5, np.float32)
    base, _ = vathopy.render(d, {"ramp": {"preset": "WHITE_TO_BLACK"}, "window": [0, 1]})
    lk = {"ramp": {"preset": "WHITE_TO_BLACK"}, "window": [0, 1],
          "areas": [{"position": [0, 0, 0.0], "radius": [0.2, 0.2, 0.2], "strength": 1.0}]}
    out, info = vathopy.render(d, lk)
    assert info["terms"] == 1
    assert out[40, 40, 0] > base[40, 40, 0] + 0.3       # centre lifted
    assert abs(out[0, 0, 0] - base[0, 0, 0]) < 1e-6      # corner untouched


def test_band_strip_and_disabled_band():
    d = np.tile(np.linspace(0, 1, 100, dtype=np.float32), (40, 1))
    lk = {"window": [0, 1], "plane": {"scale": 1.0, "midlevel": 0.5},
          "bands": [{"center": [0, 0, 0.0], "width": 0.1, "softness": 0.02, "strength": -1.0}]}
    out, _ = vathopy.render(d, lk)
    assert out[20, 50, 0] < 0.02                         # black strip at mid depth
    lk["bands"][0]["enabled"] = False
    out2, info = vathopy.render(d, lk)
    assert info["terms"] == 0 and out2[20, 50, 0] > 0.1


def test_unknown_look_key_is_rejected():
    with pytest.raises(ValueError):
        vathopy.normalise_look({"rampp": {}})


def test_nonfinite_depth_rejected():
    d = depth_field()
    d[3, 3] = np.nan
    with pytest.raises(ValueError):
        vathopy.render(d, {})


def test_prepare_depth_kinds():
    raw = np.array([[1.0, 2.0], [3.0, np.inf]])
    log, st = vathopy.prepare_depth(raw, "log", 0, 100)
    assert log[0, 0] == pytest.approx(1.0) and log[1, 0] == pytest.approx(0.0)  # near bright
    assert log[1, 1] == 0.0 and st["invalid_px"] == 1                           # invalid -> far
    disp, _ = vathopy.prepare_depth(raw, "disparity", 0, 100)
    assert disp[1, 0] == pytest.approx(1.0)


def test_io_roundtrips(tmp_path):
    d = depth_field()
    for ext in (".exr", ".png", ".npy"):
        p = str(tmp_path / f"d{ext}")
        vathopy.write_depth(d, p)
        back, info = vathopy.load_depth(p)
        tol = 1e-12 if ext != ".png" else 1.0 / 65535
        assert np.abs(back - d).max() <= tol, ext
    out, _ = vathopy.render(d, {"ramp": {"preset": "VERMILION_INK_CYAN"}})
    p = str(tmp_path / "o.png")
    codes = vathopy.write_png(out, p, 16)
    import cv2
    back = cv2.imread(p, cv2.IMREAD_UNCHANGED)[::-1, :, ::-1]
    assert np.array_equal(back, codes)



def test_io_accepts_pathlib(tmp_path):
    """Readers and writers take pathlib.Path as well as str (OpenEXR and OpenCV only accept str)."""
    d = depth_field()
    for ext in (".exr", ".png", ".npy"):
        p = tmp_path / f"d{ext}"
        vathopy.write_depth(d, p)
        back, _ = vathopy.load_depth(p)
        assert back.shape == d.shape, ext
    out, _ = vathopy.render(d, {"ramp": {"preset": "WHITE_TO_BLACK"}})
    vathopy.write_png(out, tmp_path / "o.png", 8)
    vathopy.write_exr(out, tmp_path / "o.exr")
    assert (tmp_path / "o.png").exists() and (tmp_path / "o.exr").exists()



def test_cycles_and_contours_off_by_default_change_nothing():
    d = depth_field()
    base, _ = vathopy.render(d, {"ramp": {"preset": "VERMILION_INK_CYAN"}})
    off, _ = vathopy.render(d, {"ramp": {"preset": "VERMILION_INK_CYAN"}, "cycles": None, "contours": None})
    assert np.array_equal(base, off)
    zero, _ = vathopy.render(d, {"ramp": {"preset": "VERMILION_INK_CYAN"}, "contours": {"cycles": 4, "strength": 0.0}})
    assert np.allclose(base, zero, atol=1e-6)


def test_cycles_match_the_fringe_ramp():
    """cycles=N over a black/white EASE ramp equals an explicit N-cycle black/white EASE fringe."""
    d = depth_field()
    bw = {"stops": [[0.0, [0.0, 0.0, 0.0, 1.0]], [1.0, [1.0, 1.0, 1.0, 1.0]]], "interpolation": "EASE"}
    for n in (1, 1.5, 3):
        k = round(2 * n)
        fringe = {"stops": [[i / k, [float(i % 2)] * 3 + [1.0]] for i in range(k + 1)], "interpolation": "EASE"}
        a, _ = vathopy.render(d, {"ramp": bw, "cycles": n})
        b, _ = vathopy.render(d, {"ramp": fringe})
        assert np.abs(a - b).max() < 2e-3, n


def test_contours_are_thin_lines():
    d = depth_field()
    base, _ = vathopy.render(d, {"ramp": {"preset": "VERMILION_INK_CYAN"}})
    c, _ = vathopy.render(d, {"ramp": {"preset": "VERMILION_INK_CYAN"},
                              "contours": {"cycles": 3, "strength": 0.8, "width": 2.0}})
    assert np.all(c <= base + 1e-6)                     # never brighter
    ratio = c.sum(-1) / np.maximum(base.sum(-1), 1e-6)
    assert ratio.min() < 0.3                             # dark at the lines
    assert (ratio > 0.999).mean() > 0.7                  # most of the picture untouched


def test_cycles_and_contours_validated():
    import pytest
    with pytest.raises(ValueError):
        vathopy.normalise_look({"cycles": 0})
    with pytest.raises(ValueError):
        vathopy.normalise_look({"contours": {"cycles": 3, "strength": 1.5}})
    with pytest.raises(ValueError):
        vathopy.normalise_look({"contours": {"cycles": 3, "width": 0}})
    with pytest.raises(ValueError):
        vathopy.normalise_look({"contours": {"cycles": 3, "width": 51}})
    from vathopy.cli import quarter_steps
    import argparse
    assert quarter_steps("2.5") == 2.5 and quarter_steps("2.25") == 2.25
    for bad in ("0.5", "10.25", "2.1"):
        with pytest.raises(argparse.ArgumentTypeError):
            quarter_steps(bad)


def test_focus_is_a_flat_band_at_that_depth():
    d = np.tile(np.linspace(0, 1, 200, dtype=np.float32), (20, 1))          # far left .. near right
    lk = {"ramp": {"preset": "WHITE_TO_BLACK"}, "base": "BLACK",
          "focus": [{"depth": 0.5, "width": 0.1, "softness": 0.05}]}
    out, info = vathopy.render(d, lk)
    row = out[10, :, 0]
    assert info["terms"] == 1
    assert row[100] == pytest.approx(1.0, abs=1e-5)                          # in focus: white
    assert row[20] == pytest.approx(0.0, abs=1e-6) and row[180] == pytest.approx(0.0, abs=1e-6)  # black
    assert 0.0 < row[85] < 1.0                                              # the soft edge
    # exactly the add-on band it stands for: flat, at the relief height of that depth
    s_eff, mid = 4.0, 0.5
    band = {"normal": [0, 0, 1], "center": [0, 0, (0.5 - mid) * s_eff], "width": 0.1 * s_eff, "softness": 0.05 * s_eff}
    ref, _ = vathopy.render(d, {"ramp": lk["ramp"], "base": "BLACK", "bands": [band]})
    assert np.array_equal(out, ref)
    # strength -1 on a white base: a black subject on white
    out2, _ = vathopy.render(d, dict(lk, base="WHITE", focus=[dict(lk["focus"][0], strength=-1.0)]))
    assert out2[10, 100, 0] == pytest.approx(0.0, abs=1e-5) and out2[10, 20, 0] == pytest.approx(1.0, abs=1e-5)


def test_focus_at_a_pixel_and_validation():
    d = np.tile(np.linspace(0, 1, 200, dtype=np.float32), (20, 1))
    at, _ = vathopy.render(d, {"base": "BLACK", "focus": [{"at": [150, 5], "width": 0.05, "softness": 0.02}]})
    by_depth, _ = vathopy.render(d, {"base": "BLACK", "focus": [{"depth": 150 / 199, "width": 0.05, "softness": 0.02}]})
    assert np.allclose(at, by_depth, atol=1e-4)
    for bad in ({}, {"depth": 0.5, "at": [1, 1]}, {"depth": 1.5}, {"depth": 0.5, "strength": 2}):
        with pytest.raises(ValueError):
            vathopy.normalise_look({"focus": [bad]})
    from vathopy.cli import focus_spec
    import argparse
    assert focus_spec("0.8") == {"depth": 0.8}
    assert focus_spec("0.8:0.1:0.05") == {"depth": 0.8, "width": 0.1, "softness": 0.05}
    assert focus_spec("@10,20:0.2") == {"at": [10.0, 20.0], "width": 0.2}
    assert focus_spec("0.8:0.1:0.05:-0.6") == {"depth": 0.8, "width": 0.1, "softness": 0.05, "strength": -0.6}
    for bad in ("x", "1.5", "@10", "0.5:0.1:0.1:0.1:0.1", "0.5:0.1:0.1:2"):
        with pytest.raises(argparse.ArgumentTypeError):
            focus_spec(bad)



def test_supersample_keeps_size_and_smooth_gradients():
    d = depth_field()
    plain, _ = vathopy.render(d, {"ramp": {"preset": "VERMILION_INK_CYAN"}})
    ss, info = vathopy.render(d, {"ramp": {"preset": "VERMILION_INK_CYAN"}, "supersample": 2})
    assert ss.shape == plain.shape and info["size"] == [d.shape[1], d.shape[0]]
    assert np.abs(ss - plain).mean() < 5e-3          # a plain gradient barely changes


def test_supersample_softens_contour_edges():
    """Anti-aliasing: pixels part-covered by a line get in-between values instead of a hard step."""
    d = depth_field()
    lk = {"ramp": {"preset": "WHITE_TO_BLACK"}, "contours": {"cycles": 6, "strength": 1.0, "width": 1.5}}
    hard, _ = vathopy.render(d, lk)
    soft, _ = vathopy.render(d, dict(lk, supersample=4))
    jump = lambda img: np.abs(np.diff(img[..., 0], axis=1)).max()
    assert jump(soft) < jump(hard)


def test_smooth_depth_keeps_edges_and_removes_noise():
    rng = np.random.default_rng(0)
    d = np.zeros((64, 64), np.float32)
    d[:, 32:] = 1.0                                  # a sharp step between two surfaces
    noisy = d + rng.normal(0, 0.01, d.shape).astype(np.float32)
    sm = vathopy.engine.smooth_depth(noisy, 3.0)
    assert sm[:, :28].std() < noisy[:, :28].std() / 2      # noise inside a surface evened out
    assert sm[:, 34:].mean() - sm[:, :30].mean() > 0.9     # the step survives


def test_supersample_and_smooth_validated():
    import pytest
    for bad in ({"supersample": 5}, {"supersample": 0}, {"smooth_depth": -1}):
        with pytest.raises(ValueError):
            vathopy.normalise_look(bad)



def test_contour_width_is_constant_in_pixels():
    """A steep and a shallow ramp get contour lines of the same pixel width (no smears)."""
    from vathopy.engine import contour_factor, contour_rate
    widths = []
    for slope in (1.0, 0.4):                       # depth span across a 600 px row
        norm = np.tile(np.linspace(0.0, slope, 600, dtype=np.float32), (3, 1))
        dark = (contour_factor(norm, 6.0, 1.0, 4.0, contour_rate(norm, 6.0))[1] < 0.5).astype(int)
        starts, ends = np.flatnonzero(np.diff(dark) == 1), np.flatnonzero(np.diff(dark) == -1)
        runs = [e - b for b in starts for e in ends if e > b][:1] + \
               [ends[i + 1] - starts[i] for i in range(min(len(starts), len(ends) - 1))]
        widths.append(np.median([r for r in runs if r > 0]))
        assert len(starts) >= 2, slope            # whole lines inside the row, not just the ends
    assert abs(widths[0] - widths[1]) <= 1.0, widths   # same width despite 2.5x the slope



def test_contours_skip_flat_plateaus():
    """A clipped plateau at exactly 1.0 (a contour level) must not turn solid dark."""
    from vathopy.engine import contour_factor, contour_rate
    norm = np.tile(np.concatenate([np.linspace(0.0, 1.0, 300), np.ones(100)]).astype(np.float32), (5, 1))
    f = contour_factor(norm, 4.0, 1.0, 2.0, contour_rate(norm, 4.0))[2]
    assert f[320:].min() > 0.99          # inside the plateau: untouched
    assert f[:300].min() < 0.5           # the slope still has its lines



def _skewed_depth(n=160, seed=0, noise=0.0):
    """Most pixels near the far end, a few near: a narrow depth spread."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:n, 0:n] / (n - 1.0)
    d = (x ** 4 * 0.9 + y * 0.1).astype(np.float32)
    if noise:
        import cv2
        bumps = cv2.GaussianBlur(rng.normal(0, 1, (n, n)).astype(np.float32), (0, 0), 4)
        d = d + noise * bumps / bumps.std()
    return d


def test_equalize_spreads_the_ramp_by_pixel_count():
    d = _skewed_depth()
    plain, _ = vathopy.render(d, {"ramp": {"preset": "WHITE_TO_BLACK"}})
    eq, _ = vathopy.render(d, {"ramp": {"preset": "WHITE_TO_BLACK"}, "equalize": True})
    off, _ = vathopy.render(d, {"ramp": {"preset": "WHITE_TO_BLACK"}, "equalize": False})
    assert np.array_equal(plain, off)
    spread = lambda img: np.subtract(*np.percentile(img[..., 0], [75, 25]))
    assert abs(spread(eq) - 0.5) < 0.05             # equalised: the middle half spans half the ramp
    assert spread(plain) < 0.3                      # (it was squeezed into well under a third)


def test_probe_recommends_fewer_levels_for_noisier_depth():
    from vathopy.probe import probe
    clean = probe(_skewed_depth(noise=0.001))
    noisy = probe(_skewed_depth(noise=0.006))
    assert noisy["max_levels"] < 0.7 * clean["max_levels"]
    for pr in (clean, noisy):
        assert 1 <= pr["cycles"] <= 10 and 1 <= pr["contours"] <= 10
        assert (pr["cycles"] * 2).is_integer() and (pr["contours"] * 2).is_integer()
    assert clean["suggest_equalize"]                # most of this map sits at the far end
    big = probe(np.tile(_skewed_depth(noise=0.001), (8, 8)))
    assert big["recommended_smooth_depth"] > clean["recommended_smooth_depth"]


def test_cli_probe_and_auto(tmp_path):
    d = _skewed_depth(noise=0.002)
    src = str(tmp_path / "d.exr")
    vathopy.write_depth(d, src)
    run = lambda *args: subprocess.run([sys.executable, "-m", "vathopy.cli", *args],
                                       capture_output=True, text=True)
    r = run("probe", src)
    assert r.returncode == 0 and "--contours" in r.stdout and "--cycles" in r.stdout, r.stderr
    r = run("render", src, "--contours", "auto", "--smooth-depth", "auto", "--equalize", "-o", str(tmp_path / "out") + os.sep)
    assert r.returncode == 0, r.stderr
    assert "auto: smooth-depth" in r.stdout and "contours" in r.stdout
    assert any(f.endswith("_autocontours.png") for f in os.listdir(tmp_path / "out"))
    assert run("render", src, "--cycles", "11").returncode != 0



def test_gui_settings_round_trip_and_api(tmp_path):
    import threading, urllib.request
    from http.server import ThreadingHTTPServer
    from vathopy import gui
    # settings -> look -> settings
    s = {"palette": "EMBER_TO_BLACK", "invert": True, "equalize": True, "cycles": 2.5, "contours": 6,
         "contour_strength": 0.5, "contour_width": 3, "smooth": 4, "supersample": 3,
         "focus_style": "black_on_white", "focus": [{"depth": 0.4, "width": 0.08, "softness": 0.04, "strength": 0.6}]}
    lk = gui.look_from_settings(s)
    lk["smooth_depth"] = 4.0
    back = gui.settings_from_look(lk)
    for k in s:
        assert back[k] == s[k], k
    assert gui.look_from_settings(s, preview=True)["supersample"] == 1
    assert lk["base"] == "WHITE" and lk["focus"][0]["strength"] == -0.6     # the style sets the sign
    assert len(gui.palettes()) == len(PRESETS) + 1
    assert gui._safe_name("a b/../c") == "a_b_c"
    # the server: list, render, refuse paths outside its folder
    depth_dir = tmp_path / "depth"; depth_dir.mkdir()
    vathopy.write_depth(depth_field(), str(depth_dir / "d.exr"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), gui.make_handler(gui.Maps(depth_dir), tmp_path / "fav", 256))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    def post(path, body):
        req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
    try:
        with urllib.request.urlopen(base + "/api/maps") as r:
            assert json.load(r)["maps"] == ["d.exr"]
        code, body = post("/api/render", {"map": "d.exr", "settings": s, "quality": "preview"})
        assert code == 200 and body[:8] == b"\x89PNG\r\n\x1a\n"
        assert post("/api/render", {"map": "../d.exr", "settings": s, "quality": "full"})[0] == 400
        code, body = post("/api/depth_at", {"map": "d.exr", "settings": s, "x": 0.5, "y": 0.0})
        assert code == 200 and json.loads(body)["depth"] > 0.9               # top row = near (bright)
        code, body = post("/api/save", {"map": "d.exr", "settings": s, "name": "my look"})
        assert code == 200 and (tmp_path / "fav" / "looks" / "my_look.json").exists()
        saved = json.loads(body)
        with urllib.request.urlopen(base + saved["png_url"]) as r:
            assert r.read()[:4] == b"\x89PNG"
        for bad in ("/api/file?name=../d.exr", "/api/file?name=looks/../../x.png"):
            try:
                urllib.request.urlopen(base + bad); assert False, bad
            except urllib.error.HTTPError as e:
                assert e.code in (400, 404)
    finally:
        httpd.shutdown()
    # with a password: refused without it, allowed with it
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), gui.make_handler(gui.Maps(depth_dir), tmp_path / "fav", 256, "u:secret"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        try:
            urllib.request.urlopen(base + "/api/maps"); assert False
        except urllib.error.HTTPError as e:
            assert e.code == 401
        import base64 as b64
        req = urllib.request.Request(base + "/api/maps", headers={"Authorization": "Basic " + b64.b64encode(b"u:secret").decode()})
        with urllib.request.urlopen(req) as r:
            assert r.status == 200
    finally:
        httpd.shutdown()


def test_cli(tmp_path):
    raw = np.exp(np.linspace(0, 2, 64 * 48)).reshape(48, 64).astype(np.float32)
    np.save(tmp_path / "raw.npy", raw)
    look = tmp_path / "look.json"
    run = lambda *a: subprocess.run([sys.executable, "-m", "vathopy.cli", *a], check=True, capture_output=True, text=True)
    run("prep", str(tmp_path / "raw.npy"), "-o", str(tmp_path / "depth.exr"))
    run("init", str(look), "--preset", "FRINGE_3_BW")
    run("render", str(tmp_path / "depth.exr"), "-l", str(look), "-o", str(tmp_path / "out.png"))
    assert (tmp_path / "out.png").exists()
    assert json.loads(look.read_text())["ramp"] == {"preset": "FRINGE_3_BW"}
    r = run("render", str(tmp_path / "depth.exr"), "--preset", "MIST", "--base", "BLACK", "--focus", "@32,24:0.1",
            "--focus", "0.9:0.1:0.05:0.5", "-o", str(tmp_path))
    assert (tmp_path / "depth_mist_focus.png").exists() and "2 band/area term(s)" in r.stdout
