"""Unit tests that run without Blender (pytest)."""
import json
import os
import pathlib
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
    assert len(PRESETS) == 65  # the add-on's 19 + vathopy's 8 complementary, 13 colour-to-black, MIST, 5 metallic, 19 from colour maps
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
    # the GUI's one-pass weights are this band: on black with strength 1, the result is the weight
    from vathopy.engine import focus_weights
    (w,) = focus_weights(d, lk)
    assert np.allclose(w, out[..., 0], atol=1e-6)
    # strength -1 on a white base: a black subject on white
    out2, _ = vathopy.render(d, dict(lk, base="WHITE", focus=[dict(lk["focus"][0], strength=-1.0)]))
    assert out2[10, 100, 0] == pytest.approx(0.0, abs=1e-5) and out2[10, 20, 0] == pytest.approx(1.0, abs=1e-5)


def test_leaned_focus_is_a_tilted_band_through_the_pivot():
    import math
    from vathopy.engine import focus_weights
    h, w = 60, 90
    d = np.tile(np.linspace(0.2, 0.8, h, dtype=np.float32)[:, None], (1, w))   # a floor: nearer lower down
    fo = {"depth": 0.5, "width": 0.05, "softness": 0.02, "angle": 135.0, "lean": 60.0, "pivot": [0.3, 0.6]}
    lk = {"base": "BLACK", "focus": [fo]}
    out, _ = vathopy.render(d, lk)
    # the add-on band it stands for: normal tipped 60 degrees towards 135, through the pivot at that depth
    pw, ph = 1.5, 1.5 / (w / h)
    a, le = math.radians(135), math.radians(60)
    lo, hi = float(d.min()), float(d.max())
    band = {"normal": [math.sin(le) * math.cos(a), math.sin(le) * math.sin(a), math.cos(le)],
            "center": [(0.3 - 0.5) * pw, (0.5 - 0.6) * ph, (lo + 0.5 * (hi - lo) - 0.5) * 4.0],
            "width": 0.05 * (hi - lo) * 4.0 / math.cos(le), "softness": 0.02 * (hi - lo) * 4.0 / math.cos(le)}
    ref, _ = vathopy.render(d, {"base": "BLACK", "bands": [band]})                # width kept as the thickness
    assert np.array_equal(out, ref)
    flat, _ = vathopy.render(d, {"base": "BLACK", "focus": [dict(fo, lean=0.0)]})
    assert not np.allclose(out, flat)                                   # leaning changes which pixels light up
    lit = out[..., 0] > 0.5
    assert lit.any() and not lit.all(axis=1).any()                      # a diagonal: no row fully lit
    (wt,) = focus_weights(d, lk)
    assert np.allclose(wt, out[..., 0], atol=1e-6)                      # the GUI's highlight regions match
    with pytest.raises(ValueError):
        vathopy.normalise_look({"focus": [dict(fo, lean=91)]})
    with pytest.raises(ValueError):
        vathopy.normalise_look({"focus": [dict(fo, lean=-91)]})
    neg, _ = vathopy.render(d, {"base": "BLACK", "focus": [dict(fo, lean=-60.0)]})
    turned, _ = vathopy.render(d, {"base": "BLACK", "focus": [dict(fo, lean=60.0, angle=135.0 + 180.0)]})
    assert np.allclose(neg, turned, atol=1e-5) and not np.allclose(neg, out)   # the other way = angle + 180
    up_fo = dict(fo, lean=90.0, angle=0.0)
    up, _ = vathopy.render(d, {"base": "BLACK", "focus": [up_fo]})
    assert np.array_equal(up[0], up[-1])                                # upright: the same in every row,
    assert up[..., 0].max() > 0.99 and up[..., 0].min() < 1e-6          # a vertical stripe whatever the depth
    (wu,) = focus_weights(d, {"base": "BLACK", "focus": [up_fo]})
    assert np.allclose(wu, up[..., 0], atol=1e-6)
    with pytest.raises(ValueError):
        vathopy.normalise_look({"focus": [dict(fo, pivot=[0.5])]})
    # a pixel pick puts the pivot on that pixel
    at, _ = vathopy.render(d, {"base": "BLACK", "focus": [{"at": [27, 36], "width": 0.05, "softness": 0.02,
                                                           "angle": 135.0, "lean": 60.0}]})
    picked = dict(fo, pivot=[27.5 / w, 36.5 / h], depth=float((d[h - 1 - 36, 27] - lo) / (hi - lo)))
    piv, _ = vathopy.render(d, {"base": "BLACK", "focus": [picked]})
    assert np.allclose(at, piv, atol=1e-4)


def test_focus_reach_confines_it_to_an_oval():
    from vathopy.engine import focus_weights
    h, w = 60, 90
    d = np.tile(np.linspace(0.2, 0.8, h, dtype=np.float32)[:, None], (1, w))   # a floor: one depth per row
    fo = {"depth": 0.5, "width": 0.1, "softness": 0.02, "pivot": [0.3, 0.5], "reach_w": 0.15, "reach_h": 0.3}
    lk = {"base": "BLACK", "focus": [fo]}
    out, info = vathopy.render(d, lk)
    row = h - 1 - 30                                                         # bottom-up row through the pivot
    assert out[row, 27, 0] > 0.99                                            # at the pivot: lit
    assert out[row, 75, 0] < 1e-6                                            # same depth, outside the oval: dark
    unlimited, _ = vathopy.render(d, {"base": "BLACK", "focus": [dict(fo, reach_w=0.0, reach_h=0.0)]})
    assert unlimited[row, 75, 0] > 0.99                                      # which the plain focus lights
    # exactly the add-on band plus a Keep mask area aimed at it
    lo, hi, s_eff, pw, ph = 0.2, 0.8, 4.0, 1.5, 1.5 / (w / h)
    z, span = (lo + 0.5 * (hi - lo) - 0.5) * s_eff, (hi - lo) * s_eff
    ref, _ = vathopy.render(d, {"base": "BLACK",
        "bands": [{"id": "focus1", "normal": [0, 0, 1], "center": [0, 0, z], "width": 0.1 * span, "softness": 0.02 * span}],
        "areas": [{"mode": "MASK", "position": [(0.3 - 0.5) * pw, 0.0, z], "radius": [0.15 * pw, 0.3 * ph, (0.05 + 0.02) * span * 4],
                   "mask": {"strength": 1.0, "side": "KEEP", "edge": 0.5, "target": "BAND:focus1"}}]})
    assert np.allclose(out, ref, atol=1e-6)
    (wt,) = focus_weights(d, lk)
    assert np.allclose(wt, out[..., 0], atol=1e-5)                           # the GUI's highlight regions match
    with pytest.raises(ValueError):
        vathopy.normalise_look({"focus": [dict(fo, reach_edge=2)]})
    from vathopy.cli import focus_spec
    assert focus_spec("0.5:reach=0.2") == {"depth": 0.5, "reach_w": 0.2, "reach_h": 0.2}
    assert focus_spec("0.5:reach_w=0.1:reach_h=0.4:reach_edge=0.9") == {"depth": 0.5, "reach_w": 0.1, "reach_h": 0.4,
                                                                         "reach_edge": 0.9}


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
    assert focus_spec("@10,20:angle=135:lean=60:0.05") == {"at": [10.0, 20.0], "width": 0.05, "angle": 135.0, "lean": 60.0}
    assert focus_spec("0.5:lean=-30")["lean"] == -30.0
    for bad in ("0.5:lean=91", "0.5:lean=-91", "0.5:tilt=3", "0.5:angle="):
        with pytest.raises(argparse.ArgumentTypeError):
            focus_spec(bad)
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



def test_ramp_shift_rotates_the_stop_colours():
    from vathopy.ramp import Ramp
    base = Ramp.from_dict({"preset": "BLACK_WHITE_BLACK"})
    cols = [c for _, c in base.stops]
    one = Ramp.from_dict({"preset": "BLACK_WHITE_BLACK", "shift": 1})
    assert [p for p, _ in one.stops] == [p for p, _ in base.stops]            # the stops stay put
    assert [c for _, c in one.stops] == cols[1:] + cols[:1]                  # the colours move towards position 0
    assert Ramp.from_dict({"preset": "BLACK_WHITE_BLACK", "shift": -2}).stops == one.stops   # wraps round
    assert Ramp.from_dict({"preset": "BLACK_WHITE_BLACK", "shift": 3}).stops == base.stops
    # shift first, then mirror
    assert Ramp.from_dict({"preset": "BLACK_WHITE_BLACK", "shift": 1, "mirror": True}).stops == one.mirrored().stops
    with pytest.raises(ValueError):
        Ramp.from_dict({"preset": "BLACK_WHITE_BLACK", "shift": 0.5})


def test_gui_settings_round_trip_and_api(tmp_path):
    import threading, urllib.request
    from http.server import ThreadingHTTPServer
    from vathopy import gui
    # settings -> look -> settings
    s = {"palette": "EMBER_TO_BLACK", "invert": True, "shift": -1, "equalize": True, "cycles": 2.5, "contours": 6,
         "contour_strength": 0.5, "contour_width": 3, "smooth": 4, "supersample": 3,
         "focus_style": "black_on_white", "focus": [{"depth": 0.4, "width": 0.08, "softness": 0.04, "strength": 0.6,
                                                     "enabled": True, "angle": 45.0, "lean": 30.0, "x": 0.25, "y": 0.75,
                                                     "reach_w": 0.2, "reach_h": 0.4, "reach_edge": 0.7, "locked": True,
                                                     "shape": "cylinder", "fill": "solid", "size_x": 0.2, "size_y": 0.15,
                                                     "size_z": 0.15, "length": 0.5, "cone_angle": 30.0},
                                                    {"depth": 0.8, "width": 0.05, "softness": 0.02, "strength": 1.0,
                                                     "enabled": False, "angle": 0.0, "lean": 0.0,
                                                     "reach_w": 0.0, "reach_h": 0.0, "reach_edge": 0.5, "locked": False,
                                                     "shape": "plane", "fill": "shell", "size_x": 0.15, "size_y": 0.15,
                                                     "size_z": 0.15, "length": 0.0, "cone_angle": 30.0}],
         "depth_scale": 1.5, "cut_depth": 0.3, "cut_near": 0.2, "cut_softness": 0.02, "cut_fill": "white",
         "keep": [{"depth": 0.6, "x": 0.4, "y": 0.6, "lean": 10.0, "angle": 20.0, "softness": 0.01, "enabled": True,
                   "locked": False, "shape": "cone", "fill": "shell", "size_x": 0.1, "size_y": 0.1, "size_z": 0.1,
                   "length": 0.0, "cone_angle": 25.0}]}
    lk = gui.look_from_settings(s)
    lk["smooth_depth"] = 4.0
    back = gui.settings_from_look(lk)
    for k in s:
        assert back[k] == s[k], k
    assert gui.look_from_settings(s, preview=True)["supersample"] == 1
    assert lk["base"] == "WHITE" and lk["focus"][0]["strength"] == -0.6     # the style sets the sign
    assert len(gui.palettes()) == len(PRESETS) + 1
    for lean, angle, want in ((60, 135, (-60, -45)), (60, 270, (60, -90)), (60, -45, (60, -45)), (30, 0, (30, 0))):
        got = gui._slider_lean_angle(lean, angle)
        assert got == pytest.approx(want), (lean, angle)
        a, b = vathopy.engine.focus_bands([{"depth": 0.5, "width": 0.1, "softness": 0.05, "angle": angle, "lean": lean,
                                            "enabled": True, "strength": 1.0}], 0, 1, 4.0, 0.5, 1.5, 1.0)[0], \
               vathopy.engine.focus_bands([{"depth": 0.5, "width": 0.1, "softness": 0.05, "angle": got[1], "lean": got[0],
                                            "enabled": True, "strength": 1.0}], 0, 1, 4.0, 0.5, 1.5, 1.0)[0]
        assert np.allclose(np.abs(np.dot(a["normal"], b["normal"])), 1.0)   # the same plane
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
        code, body = post("/api/render", {"map": "d.exr", "settings": s, "quality": "preview", "view": "highlight"})
        assert code == 200 and body[:8] == b"\x89PNG\r\n\x1a\n"
        code, body = post("/api/depth_at", {"map": "d.exr", "settings": s, "x": 0.5, "y": 0.0})
        assert code == 200 and json.loads(body)["depth"] > 0.9               # top row = near (bright)
        code, body = post("/api/export", {"map": "d.exr", "settings": s, "name": "my look"})
        assert code == 200 and body[:8] == b"\x89PNG\r\n\x1a\n" and body[24] == 16      # 16-bit
        import cv2
        got = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_UNCHANGED)
        ref_path = tmp_path / "ref.png"
        out, _ = vathopy.render(gui.Maps(depth_dir).depth("d.exr", 4.0), gui.look_from_settings(s))
        vathopy.write_png(out, str(ref_path), 16)
        assert np.array_equal(got, cv2.imread(str(ref_path), cv2.IMREAD_UNCHANGED))  # the same as Save writes
        assert not (tmp_path / "fav").exists()                                     # nothing written on the server
        code, body = post("/api/save", {"map": "d.exr", "settings": s, "name": "my look"})
        assert code == 200 and (tmp_path / "fav" / "looks" / "my_look.json").exists()
        saved = json.loads(body)
        with urllib.request.urlopen(base + saved["png_url"]) as r:
            assert r.read()[:4] == b"\x89PNG"
        # crop: saved next to the map (and listed), or downloaded without writing anything
        code, body = post("/api/crop_download", {"map": "d.exr", "crop": {"top": 1, "left": 5}})
        assert code == 200 and body[:4] == b"v/1\x01" and not (depth_dir / "d-cropped.exr").exists()
        code, body = post("/api/crop_save", {"map": "d.exr", "crop": {"top": 1, "left": 5}})
        assert code == 200 and json.loads(body)["width"] == 115 and json.loads(body)["height"] == 89
        assert (depth_dir / "d-cropped.exr").read_bytes() == post("/api/crop_download", {"map": "d.exr", "crop": {"top": 1, "left": 5}})[1]
        with urllib.request.urlopen(base + "/api/maps") as r:
            assert json.load(r)["maps"] == ["d-cropped.exr", "d.exr"]
        # STL: downloaded (nothing written), or saved into the favourites folder without overwriting
        stl = {"map": "d.exr", "settings": s, "name": "my look", "mode": "relief", "width_mm": 50, "px": 40}
        code, body = post("/api/stl", stl)
        assert code == 200 and len(body) == 84 + 50 * int(np.frombuffer(body[80:84], "<u4")[0])
        first, second = (json.loads(post("/api/stl", {**stl, "save": True})[1]) for _ in range(2))
        assert first["name"] == "d_my_look_relief.stl" and second["name"] == "d_my_look_relief-001.stl"
        assert (tmp_path / "fav" / first["name"]).read_bytes() == body
        for bad in ({"map": "../d.exr", "crop": {"top": 1}}, {"map": "d.exr", "crop": {}},
                    {"map": "d.exr", "crop": {"top": 60, "bottom": 30}}, {"map": "d.exr", "crop": {"left": -1}}):
            assert post("/api/crop_save", bad)[0] == 400, bad
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


def test_crop_exr_keeps_channels_types_and_header(tmp_path):
    import OpenEXR
    rng = np.random.default_rng(1)
    rgb = rng.random((30, 40, 3), dtype=np.float32)
    z = rng.random((30, 40)).astype(np.float16)
    src, dst = tmp_path / "a.exr", tmp_path / "a-cropped.exr"
    hdr = {"compression": OpenEXR.PIZ_COMPRESSION, "type": OpenEXR.scanlineimage, "prompt": "{\"1\": {}}"}
    with OpenEXR.File(hdr, {"RGB": rgb, "Z": z}) as f:
        f.write(str(src))
    assert vathopy.crop_exr(src, dst, top=1, right=5, bottom=10, left=2) == (33, 19)
    with OpenEXR.File(str(dst)) as f:
        h, ch = f.header(), f.channels()
        assert np.array_equal(ch["RGB"].pixels, rgb[1:20, 2:35]) and np.array_equal(ch["Z"].pixels, z[1:20, 2:35])
        assert ch["Z"].pixels.dtype == np.float16 and h["prompt"] == '{"1": {}}'
        assert h["compression"] == OpenEXR.PIZ_COMPRESSION
        assert [list(v) for v in h["dataWindow"]] == [[0, 0], [32, 18]] == [list(v) for v in h["displayWindow"]]
    for bad in ({"top": 15, "bottom": 15}, {"left": 40}, {"top": -1}):
        with pytest.raises(ValueError):
            vathopy.crop_exr(src, tmp_path / "bad.exr", **bad)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a-cropped.exr", "a.exr"]   # no partial files left


def test_gui_highlight_tints_each_focus():
    from vathopy import gui
    d = np.tile(np.linspace(0, 1, 200, dtype=np.float32), (20, 1))
    lk = vathopy.normalise_look({"focus": [{"depth": 0.25, "width": 0.1, "softness": 0.02},
                                           {"depth": 0.75, "width": 0.1, "softness": 0.02}]})
    img = gui.highlight(d, lk).astype(int)                              # top-down 8-bit sRGB
    assert img.shape == (20, 200, 3)
    for x, colour in ((50, gui.FOCUS_COLOURS[0]), (150, gui.FOCUS_COLOURS[1])):
        px = img[10, x]
        assert np.argmax(px) == np.argmax(colour) and px.max() - px.min() > 60   # tinted with that colour
    assert img[10, 100].max() - img[10, 100].min() < 10                 # elsewhere: grey
    lk["focus"][1]["enabled"] = False
    off = gui.highlight(d, lk).astype(int)
    assert off[10, 150].max() - off[10, 150].min() < 10                 # switched off: no tint
    lk["focus"][1]["enabled"] = True
    lk["focus"][1]["strength"] = 0.0
    faint = gui.highlight(d, lk).astype(int)
    assert 0 < faint[10, 150].max() - faint[10, 150].min() < (img[10, 150].max() - img[10, 150].min()) / 2
    lk["focus"][1]["enabled"] = False                                  # strength 0: a faint tint only
    out, info = vathopy.render(d, dict(lk, base="BLACK"))
    assert info["terms"] == 1 and out[10, 150, 0] < 1e-6 and out[10, 50, 0] > 0.99   # nor in the result


def test_user_palettes_from_a_folder(tmp_path):
    import cv2
    from vathopy import palettes as PAL
    folder = tmp_path / "palettes"; folder.mkdir()
    grad = np.zeros((20, 200, 3), np.uint8)
    grad[..., 2] = np.linspace(255, 0, 200)[None, :]             # red (BGR) on the left = near
    grad[..., 0] = np.linspace(0, 255, 200)[None, :]             # blue on the right = far
    cv2.imwrite(str(folder / "red to blue.png"), grad)
    blocks = np.zeros((10, 90, 3), np.uint8)
    blocks[:, :30] = (0, 0, 255); blocks[:, 30:60] = (0, 255, 0); blocks[:, 60:] = (255, 0, 0)   # red, green, blue
    cv2.imwrite(str(folder / "blocks.png"), blocks.transpose(1, 0, 2))   # tall: read top to bottom
    (folder / "two.json").write_text(json.dumps({"warm": {"name": "Warm", "stops": [[0, "#000000"], [1, "#FFAA00"]]},
                                                 "cold": {"stops": [[0, "#000000"], [1, "#00AAFF"]]}}))
    (folder / "gimp.gpl").write_text("GIMP Palette\nName: g\n#\n255 255 255 white\n0 0 0 black\n")
    (folder / "mist.png").write_bytes((folder / "red to blue.png").read_bytes())   # clashes with a built-in
    (folder / "broken.json").write_text("{nope")
    (folder / "notes.txt").write_text("ignored")
    try:
        warnings = PAL.register(folder, quiet=True)
        assert any("MIST" in w for w in warnings) and any("broken.json" in w for w in warnings)
        assert PRESETS["MIST"]["desc"].startswith("Near dark")                 # the built-in is untouched
        for key in ("RED_TO_BLUE", "BLOCKS", "WARM", "COLD", "GIMP"):
            assert PRESETS[key]["user"], key
        near, far = preset("RED_TO_BLUE").evaluate(np.array([1.0, 0.0]))     # pos 1 = near
        assert near[0] > 0.9 and near[2] < 0.1 and far[2] > 0.9 and far[0] < 0.1
        st = PRESETS["BLOCKS"]["stops"]
        assert [c for _, c in sorted(st, reverse=True)] == ["#FF0000", "#00FF00", "#0000FF"]   # top = near
        assert len(PRESETS["RED_TO_BLUE"]["stops"]) <= PAL.MAX_STOPS
        assert PRESETS["GIMP"]["stops"] == [[0.0, "#000000"], [1.0, "#FFFFFF"]]  # first colour = near
        out, _ = vathopy.render(depth_field(), {"ramp": {"preset": "WARM"}})
        assert np.isfinite(out).all()
        (folder / "warm.json").unlink() if (folder / "warm.json").exists() else None
        (folder / "gimp.gpl").unlink()
        PAL.register(folder, quiet=True)
        assert "GIMP" not in PRESETS                                            # removed files drop out
        r = subprocess.run([sys.executable, "-m", "vathopy.cli", "--palettes", str(folder), "presets"],
                           check=True, capture_output=True, text=True)
        assert "RED_TO_BLUE" in r.stdout and "yours: red to blue.png" in r.stdout
    finally:
        PAL.register(tmp_path / "none", quiet=True)                           # leave only the built-ins
    assert "RED_TO_BLUE" not in PRESETS and len(PRESETS) == 65


def test_user_palettes_reject_hostile_files(tmp_path):
    import struct, zlib
    from vathopy import palettes as PAL
    folder = tmp_path / "palettes"; folder.mkdir()
    # a few hundred bytes claiming 60000 x 60000 pixels: refused from the header, never decoded
    ihdr = struct.pack(">IIBBBBB", 60000, 60000, 8, 2, 0, 0, 0)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    (folder / "bomb.png").write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\0" * 100))
                                      + chunk(b"IEND", b""))
    (folder / "fake.png").write_text("not really a png")
    good = {"stops": [[0, "#000000"], [1, "#FFFFFF"]]}
    (folder / "escape.json").write_text(json.dumps(dict(good, name="\x1b[2J\x1b]0;pwned\x07Evil <script>x</script>",
                                                         desc="line1\nline2\u202e" + "x" * 500)))
    (folder / "nan.json").write_text('{"stops": [[NaN, "#000000"], [1, "#FFFFFF"]]}')
    (folder / "many.json").write_text(json.dumps({"stops": [[i / 99, "#000000"] for i in range(100)]}))
    (folder / "colour.json").write_text(json.dumps({"stops": [[0, "red;background:url(x)"]]}))
    (folder / "boolpos.json").write_text(json.dumps({"stops": [[True, "#000000"]]}))
    (folder / "interp.json").write_text(json.dumps(dict(good, interpolation="__import__('os')")))
    (folder / "big.json").write_text(json.dumps(dict(good, desc="x" * (PAL.MAX_TEXT_BYTES + 10))))
    (folder / "deep.json").write_text("[" * 100000 + "]" * 100000)
    (folder / "gimp.gpl").write_text("GIMP Palette\n" + "1 2 3 c\n" * 40)
    secret = tmp_path / "secret.json"; secret.write_text(json.dumps(good))
    (folder / "link.json").symlink_to(secret)
    found, report = PAL.read_folder(folder)
    status = {f: (st, detail) for f, st, detail in report}
    assert list(found) == ["ESCAPE"], found                                  # only the clean one loads
    for f in ("bomb.png", "fake.png", "nan.json", "many.json", "colour.json", "boolpos.json", "interp.json",
              "big.json", "deep.json", "gimp.gpl", "link.json"):
        assert status[f][0] == "rejected", (f, status.get(f))
    assert "megapixels" in status["bomb.png"][1] and "symlink" in status["link.json"][1]
    e = found["ESCAPE"]
    assert not any(ord(c) < 32 or ord(c) == 0x7F or c == "\u202e" for c in e["name"] + e["desc"])
    assert len(e["desc"]) <= PAL.MAX_DESC and len(e["name"]) <= PAL.MAX_NAME
    # the GUI puts names on the page as text, never as HTML
    html = (pathlib.Path(vathopy.__file__).with_name("gui.html")).read_text()
    assert 'querySelector(".name").textContent = p.label' in html and "${p.label}" not in html
    # check-palettes reports every file and fails when any is rejected
    r = subprocess.run([sys.executable, "-m", "vathopy.cli", "check-palettes", str(folder)], capture_output=True, text=True)
    assert r.returncode == 1 and "REJECTED  bomb.png" in r.stdout and "OK" in r.stdout and "\x1b" not in r.stdout
    ok_dir = tmp_path / "ok"; ok_dir.mkdir(); (ok_dir / "fine.json").write_text(json.dumps(good))
    r = subprocess.run([sys.executable, "-m", "vathopy.cli", "check-palettes", str(ok_dir)], capture_output=True, text=True)
    assert r.returncode == 0 and "1 ok, 0 rejected" in r.stdout


def test_depth_maps_are_checked_before_decoding(tmp_path, monkeypatch):
    import struct, zlib
    from vathopy import gui
    d = depth_field()
    vathopy.write_depth(d, str(tmp_path / "ok.exr"))
    raw, _ = VIO.read_image_channel0(str(tmp_path / "ok.exr"))
    assert raw.shape == d.shape
    monkeypatch.setattr(VIO, "MAX_DEPTH_PIXELS", 100)                      # pretend 90 x 120 is too big
    called = []
    import OpenEXR
    real = OpenEXR.File
    monkeypatch.setattr(OpenEXR, "File", lambda *a, **k: (called.append(k.get("header_only", False)), real(*a, **k))[1])
    with pytest.raises(ValueError, match="too big"):
        VIO.read_image_channel0(str(tmp_path / "ok.exr"))
    assert called == [True]                                                # refused from the header alone
    monkeypatch.undo()
    # a PNG that claims 60000 x 60000 pixels in a few hundred bytes
    ihdr = struct.pack(">IIBBBBB", 60000, 60000, 16, 0, 0, 0, 0)
    chunk = lambda t, data: struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data))
    (tmp_path / "bomb.png").write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b""))
    with pytest.raises(ValueError, match="too big"):
        VIO.read_image_channel0(str(tmp_path / "bomb.png"))
    # .npy: numbers only, and never unpickled
    np.save(tmp_path / "obj.npy", np.array([{"a": 1}], dtype=object), allow_pickle=True)
    with pytest.raises(ValueError):
        VIO.read_image_channel0(str(tmp_path / "obj.npy"))
    np.save(tmp_path / "ok.npy", d)
    assert VIO.read_image_channel0(str(tmp_path / "ok.npy"))[0].shape == d.shape
    # the GUI lists regular files only
    (tmp_path / "link.exr").symlink_to(tmp_path / "ok.exr")
    assert gui.Maps(tmp_path).list() == ["ok.exr"]


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


def test_settings_embedded_in_pngs_and_read_back(tmp_path):
    import struct
    from vathopy import gui, meta
    s = {"palette": "BLACK_WHITE_BLACK", "shift": 1, "invert": False, "equalize": False, "cycles": 2.1,
         "contours": 0, "contour_strength": 0, "contour_width": 1, "smooth": 2, "supersample": 1, "focus": []}
    req = {"map": "scene_depth.exr", "name": "mine", "settings": s}
    out = np.zeros((6, 8, 4), np.float32); out[..., 0] = np.linspace(0, 1, 8)
    png = gui._png16(out, req)
    import cv2
    assert cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED).shape == (6, 8, 3)   # still a good PNG
    d = meta.read(png)
    assert d["source"] == "PNG" and d["settings"] == s and d["map"] == "scene_depth.exr" and d["name"] == "mine"
    assert d["look"]["ramp"] == {"preset": "BLACK_WHITE_BLACK", "shift": 1} and d["look"]["smooth_depth"] == 2.0
    # only the eXIf copy left (the text chunk dropped, as a converter might)
    i = png.index(b"tEXt") - 4
    n = struct.unpack(">I", png[i:i + 4])[0]
    assert meta.read(png[:i] + png[i + 12 + n:])["settings"] == s
    # a JPEG with the same EXIF block in APP1, and one with the JSON only in a comment
    exif = b"Exif\0\0" + meta._exif(meta._text(d))
    jpg = b"\xff\xd8" + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + b"\xff\xd9"
    assert meta.read(jpg)["source"] == "JPEG" and meta.read(jpg)["settings"] == s
    com = meta._text(d).encode()
    assert meta.read(b"\xff\xd8\xff\xfe" + struct.pack(">H", len(com) + 2) + com + b"\xff\xd9")["settings"] == s
    with pytest.raises(ValueError):
        meta.read(b"\xff\xd8\xff\xd9")
    # the CLI reads the look from the PNG, an exported JSON and a plain look alike
    (tmp_path / "a.png").write_bytes(png)
    (tmp_path / "a.json").write_text(json.dumps(meta.payload(s, d["look"])))
    (tmp_path / "b.json").write_text(json.dumps({"ramp": {"preset": "BLACK_WHITE_BLACK", "shift": 1}}))
    for f in ("a.png", "a.json", "b.json"):
        assert vathopy.look.load(tmp_path / f)["ramp"] == {"preset": "BLACK_WHITE_BLACK", "shift": 1}, f
    # a look JSON read as a file goes back to page settings
    assert gui.settings_from_look(meta.look_of(meta.read((tmp_path / "b.json").read_bytes())))["shift"] == 1


def test_orientation_turns_and_mirrors_the_finished_picture():
    from vathopy import gui
    from vathopy.engine import render
    rng = np.random.default_rng(3)
    d = rng.random((5, 8)).astype(np.float32)
    base = {"cycles": 2.0, "focus": [{"depth": 0.5, "width": 0.1, "softness": 0.05, "pivot": [0.2, 0.3], "lean": 30.0}]}
    plain, _ = render(d, vathopy.look.normalise(base))
    td = plain[::-1]                                                  # top-down, as the picture is seen
    for turns, mirror in ((1, False), (3, False), (2, True), (1, True), (-1, False)):
        out, info = render(d, vathopy.look.normalise({**base, "orientation": {"turns": turns, "mirror": mirror}}))
        want = np.rot90(td, turns % 4)
        want = want[:, ::-1] if mirror else want
        assert np.array_equal(out[::-1], want), (turns, mirror)
        assert info["size"] == [want.shape[1], want.shape[0]]
    with pytest.raises(ValueError):
        vathopy.look.normalise({"orientation": {"turns": 0.5}})
    s = {"palette": "FRINGE", "cycles": 2, "orientation": {"turns": 3, "mirror": True}, "focus_style": "white_on_black",
         "focus": [{"depth": 0.5, "width": 0.1, "softness": 0.05, "x": 0.2, "y": 0.3}]}
    lk = gui.look_from_settings(s)
    assert lk["orientation"] == {"turns": 3, "mirror": True}
    assert gui.settings_from_look(lk)["orientation"] == {"turns": 3, "mirror": True}
    assert gui.highlight(d, lk).shape[:2] == (8, 5)                   # the tints turn with the picture


def test_shape_focuses_cut_the_relief_where_geometry_says():
    from vathopy.engine import focus_weights, render
    H, W = 120, 160
    d = np.full((H, W), 0.5, np.float32)                       # a flat wall halfway into the window
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.hypot(xx + 0.5 - W / 2, yy + 0.5 - H / 2) / W       # distance from the centre, in picture widths

    def weight(**fo):
        fo = {"depth": 0.5, "width": 0.02, "softness": 0.001, "pivot": [0.5, 0.5], **fo}
        return focus_weights(d, {"window": [0.0, 1.0], "focus": [fo]})[0][::-1]   # top-down

    ring = weight(shape="ellipsoid", size=0.2)                 # a sphere centred on the wall: a ring of radius 0.2
    assert ring[np.abs(r - 0.2) < 0.004].min() > 0.9
    assert ring[(r < 0.17) | (r > 0.23)].max() < 0.05
    disc = weight(shape="ellipsoid", size=0.2, fill="solid")
    assert disc[r < 0.19].min() > 0.99 and disc[r > 0.21].max() < 0.01
    box = weight(shape="box", size=[0.1, 0.05, 0.5], fill="solid")   # half extents: a 0.2 x 0.1 rectangle
    inside = (np.abs(xx + 0.5 - W / 2) / W < 0.095) & (np.abs(yy + 0.5 - H / 2) / W < 0.045)
    outside = (np.abs(xx + 0.5 - W / 2) / W > 0.105) | (np.abs(yy + 0.5 - H / 2) / W > 0.055)
    assert box[inside].min() > 0.99 and box[outside].max() < 0.01
    rod = weight(shape="cylinder", size=0.1, fill="solid", lean=90.0)   # tilted to point at the viewer: a disc
    assert rod[r < 0.09].min() > 0.99 and rod[r > 0.11].max() < 0.01
    dx, dy = np.abs(xx + 0.5 - W / 2) / W, np.abs(yy + 0.5 - H / 2) / W
    column = weight(shape="cylinder", size=0.1, fill="solid")           # upright at tilt 0: a full-height stripe
    assert column[dx < 0.09].min() > 0.99 and column[dx > 0.11].max() < 0.01
    short = weight(shape="cylinder", size=0.1, fill="solid", length=0.3)   # length: its height on the picture
    assert short[(dx < 0.09) & (dy < 0.14)].min() > 0.99 and short[dy > 0.16].max() < 0.01
    lying = weight(shape="cylinder", size=0.1, fill="solid", angle=90.0)   # turned 90: lying left-right
    assert lying[dy < 0.09].min() > 0.99 and lying[dy > 0.11].max() < 0.01
    behind = weight(shape="cylinder", size=0.1, fill="solid", depth=0.7)   # moved 0.2 back: misses the wall
    assert behind.max() < 0.01
    cone = weight(shape="cone", depth=0.3, cone_angle=45.0)    # apex 0.2 behind the wall, 45 degrees: ring 0.2
    assert cone[np.abs(r - 0.2) < 0.004].min() > 0.9 and cone[r < 0.15].max() < 0.05
    # depth_scale stretches depth: the same cone with depth twice as deep meets the wall at radius 0.4
    lk = {"window": [0.0, 1.0], "depth_scale": 2.0,
          "focus": [{"depth": 0.3, "width": 0.02, "softness": 0.001, "shape": "cone", "cone_angle": 45.0}]}
    far_ring = focus_weights(d, lk)[0][::-1]
    assert far_ring[np.abs(r - 0.4) < 0.004].min() > 0.9
    # in the picture: lifted to white on black where the ring is
    out, _ = render(d, {"window": [0.0, 1.0], "base": "BLACK", "focus": [{"depth": 0.5, "width": 0.02, "softness": 0.001,
                                                                          "shape": "ellipsoid", "size": 0.2}]})
    assert out[::-1][np.abs(r - 0.2) < 0.003, 0].min() > 0.9 and out[::-1][r < 0.15, 0].max() < 0.05
    with pytest.raises(ValueError):
        vathopy.look.normalise({"focus": [{"depth": 0.5, "shape": "torus"}]})


def test_deletion_slab_removes_far_depths_except_kept_shapes():
    from vathopy.engine import render
    H, W = 100, 100
    d = np.tile(np.linspace(0.0, 1.0, W, dtype=np.float32), (H, 1))     # far on the left, near on the right
    base = {"window": [0.0, 1.0], "ramp": {"preset": "BLACK_TO_WHITE"}}
    plain, _ = render(d, base)
    half, _ = render(d, {**base, "cut": {"depth": 0.5, "fill": "white"}})
    t = (np.arange(W) + 0.0) / (W - 1)
    assert np.all(half[:, t < 0.5, :3] == 1.0) and np.array_equal(half[:, t > 0.5], plain[:, t > 0.5])
    all_gone, _ = render(d, {**base, "cut": {"depth": 1.0}})
    assert np.all(all_gone[..., :3] == 0.0)                             # dragged to the nearest: nothing left
    # a kept sphere around the left-middle survives the full cut
    keep = {"shape": "ellipsoid", "depth": 0.25, "pivot": [0.25, 0.5], "size": 0.1, "softness": 0.001}
    kept, _ = render(d, {**base, "cut": {"depth": 1.0, "keep": [keep]}})
    yy, xx = np.mgrid[0:H, 0:W]
    near_centre = np.hypot(xx + 0.5 - 25, yy + 0.5 - 50) < 3            # where the wall passes through the sphere
    assert np.array_equal(kept[near_centre], plain[near_centre]) and np.all(kept[:, 70:, :3] == 0.0)
    nothing, _ = render(d, {**base, "cut": {"depth": 0.0}})
    assert np.array_equal(nothing, plain)
    # from the near side: everything nearer than 1 - near goes; with both, only the band between stays
    near, _ = render(d, {**base, "cut": {"near": 0.3, "fill": "white"}})
    assert np.all(near[:, t > 0.7, :3] == 1.0) and np.array_equal(near[:, t < 0.7], plain[:, t < 0.7])
    band, _ = render(d, {**base, "cut": {"depth": 0.4, "near": 0.4}})
    assert np.all(band[:, (t < 0.4) | (t > 0.6), :3] == 0.0)
    assert np.array_equal(band[:, (t > 0.4) & (t < 0.6)], plain[:, (t > 0.4) & (t < 0.6)])
    gone, _ = render(d, {**base, "cut": {"near": 1.0}})
    assert np.all(gone[..., :3] == 0.0)


def test_heightfield_mesh_is_a_closed_solid_with_the_right_volume(tmp_path):
    from vathopy import mesh
    rng = np.random.default_rng(5)
    top = rng.random((7, 9)) * 3.0
    v, f = mesh.heightfield_mesh(top, px_mm=0.5, base_mm=2.0)
    assert len(f) == mesh.triangle_count(7, 9) and mesh.check_closed(f)
    # each top triangle stands on the base as a prism: projected area x mean height
    tri = v[f[: 2 * 6 * 8]].astype(np.float64)
    e1, e2 = tri[:, 1, :2] - tri[:, 0, :2], tri[:, 2, :2] - tri[:, 0, :2]
    area = 0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
    assert mesh.volume(v, f) == pytest.approx((area * tri[:, :, 2].mean(axis=1)).sum(), rel=1e-5)
    assert mesh.volume(*mesh.heightfield_mesh(np.zeros((3, 4)), 1.0, 2.0)) == pytest.approx(2.0 * 2 * 3)
    data = mesh.stl_bytes(v, f)
    assert len(data) == 84 + 50 * len(f) and int(np.frombuffer(data[80:84], "<u4")[0]) == len(f)
    back = np.frombuffer(data[84:], dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    assert np.allclose(back["v"], v[f]) and np.allclose(np.linalg.norm(back["n"], axis=1), 1.0, atol=1e-5)
    # a depth relief: near raised; a lithophane: dark thick, within its range
    depth = np.tile(np.linspace(0, 1, 40, dtype=np.float32), (20, 1))
    h = mesh.relief_heights(depth, 20, relief_mm=5.0)
    assert h.shape == (10, 20) and h[:, 0].max() < 0.5 and h[:, -1].min() > 4.5
    lit = mesh.lithophane_heights(np.dstack([depth] * 3), 20, 0.8, 3.0)
    assert lit.min() >= 0 and lit.max() <= 2.2 + 1e-6 and lit[:, 0].mean() > lit[:, -1].mean()
    with pytest.raises(ValueError):
        mesh.heightfield_mesh(np.zeros((3000, 2500)), 0.1, 1.0)


def two_planes(h=120, w=160):
    """Far floor-free background at 0.2 with a near block (0.8) in the middle; rows bottom-up."""
    d = np.full((h, w), 0.2, np.float32)
    d[30:90, 50:110] = 0.8
    return d


def test_new_look_keys_default_off_and_render_as_before():
    from vathopy.look import normalise
    lk = normalise({})
    assert lk["selections"] == [] and lk["overlay"] is None and lk["view_shift"] is None
    d = depth_field()
    plain, _ = vathopy.render(d, {"ramp": {"preset": "MAGMA"}, "cycles": 2})
    photo = np.random.default_rng(1).random((45, 60, 3)).astype(np.float32)
    again, info = vathopy.render(d, {"ramp": {"preset": "MAGMA"}, "cycles": 2, "selections": [], "view_shift": None}, photo)
    assert np.array_equal(plain, again) and info["overlay"] == "off"


def test_selection_grows_to_depth_edges_and_gets_its_own_palette():
    from vathopy import engine
    from vathopy.look import normalise
    d = two_planes()
    sel = normalise({"selections": [{"point": [0.5, 0.5], "grow": 0, "softness": 0}]})["selections"][0]
    t = (d - d.min()) / (d.max() - d.min())
    m = engine.selection_mask(t, sel)
    assert m[30:90, 50:110].min() == 1.0 and m.sum() == 60 * 60                 # the block, nothing else
    assert engine.selection_mask(t, {**sel, "invert": True}).sum() == d.size - 60 * 60
    base = {"ramp": {"preset": "WHITE_TO_BLACK"}}
    out, _ = vathopy.render(d, {**base, "selections": [{"point": [0.5, 0.5], "ramp": {"preset": "BLACK_TO_WHITE"},
                                                        "grow": 0, "softness": 0}]})
    plain, _ = vathopy.render(d, base)
    assert np.array_equal(out[0, 0], plain[0, 0])                               # outside: as before
    assert plain[60, 80, 0] > 0.9 and out[60, 80, 0] < 0.1                      # inside: the other palette
    # a figure standing on a floor that comes nearer going down the picture: "floors" keeps it off the floor
    h, w = 120, 160
    rows = np.arange(h)[:, None]                                                # bottom-up
    scene = np.where(rows < 60, 0.5 + 0.4 * (59 - rows) / 59, 0.2) * np.ones((1, w))
    scene[40:100, 60:100] = 0.5 + 0.4 * 19 / 59                                 # its feet at the floor's depth
    scene = scene.astype(np.float32)
    t = (scene - scene.min()) / (scene.max() - scene.min())
    pick = {**sel, "point": [0.5, 0.42], "step": 0.02}
    both = engine.selection_mask(t, pick)
    only = engine.selection_mask(t, {**pick, "floors": True})
    assert both[:30].mean() > 0.9 and only[:30].mean() < 0.1                     # the floor near the bottom
    assert both[50:95, 65:95].min() == 1.0 and only[50:95, 65:95].min() == 1.0   # the figure in both


def test_photo_overlay_blends_where_asked():
    d = two_planes()
    photo = np.ones((60, 80, 3), np.float32)                                    # white, half size: resized
    base = {"ramp": {"preset": "WHITE_TO_BLACK"}, "base": "RAMP"}
    out, info = vathopy.render(d, {**base, "overlay": {"opacity": 1.0}}, photo)
    assert info["overlay"] == "on" and np.allclose(out, 1.0, atol=1e-5)
    half, _ = vathopy.render(d, {**base, "overlay": {"opacity": 0.5}}, photo)
    plain, _ = vathopy.render(d, base)
    srgb = VIO.srgb_oetf
    assert np.allclose(srgb(half[..., :3]), 0.5 * srgb(plain[..., :3]) + 0.5, atol=1e-3)
    sel = [{"point": [0.5, 0.5], "grow": 0, "softness": 0}]
    inside, _ = vathopy.render(d, {**base, "selections": sel, "overlay": {"opacity": 1.0, "where": "selections"}}, photo)
    assert np.allclose(inside[60, 80], 1.0, atol=1e-5) and np.allclose(inside[5, 5], plain[5, 5], atol=1e-5)
    _, info = vathopy.render(d, {**base, "overlay": {"opacity": 1.0}})
    assert info["overlay"] == "no photo"
    off, info = vathopy.render(d, {**base, "overlay": {"opacity": 1.0, "enabled": False}}, photo)
    assert info["overlay"] == "off" and np.array_equal(off, plain)                # switched off: kept, not shown
    neg, _ = vathopy.render(d, {**base, "overlay": {"opacity": 1.0, "invert": True}}, photo)
    assert np.allclose(neg, 0.0, atol=1e-5)                                     # white's negative


def test_view_shift_moves_near_things_against_far_ones():
    from vathopy import engine
    from vathopy.look import normalise
    d = two_planes()
    vs = normalise({"view_shift": {"x": 0.0}})["view_shift"]
    same, _ = engine.view_shift(d, vs)
    assert same is d
    vs = normalise({"view_shift": {"x": 1.0, "strength": 0.05, "edges": "fill"}})["view_shift"]
    z, p = engine.view_shift(d, vs, np.dstack([d] * 3))
    k = 0.05 * 160
    near = np.where(z[60] > 0.5)[0]
    assert near.min() == 50 - round(k * 0.3) and near.max() == 109 - round(k * 0.3)   # near: left by 0.3 k
    assert z.min() == pytest.approx(0.2) and np.all(np.isfinite(z))                   # gaps filled from behind
    assert np.array_equal(p[..., 0], z)                                               # the photo moved alike
    up = engine.view_shift(d, normalise({"view_shift": {"y": 1.0, "strength": 0.05, "edges": "fill"}})["view_shift"])[0]
    assert np.where(up[:, 80] > 0.5)[0].min() < 30                                    # up: near things lower
    zoom = engine.view_shift(d, normalise({"view_shift": {"x": 1.0, "strength": 0.05}})["view_shift"])[0]
    assert (zoom[60] > 0.5).sum() > (z[60] > 0.5).sum()                               # zoomed past the uncovered border
    # a noisy background: the gap a near thing leaves is filled smoothly, not with streaks of single pixels
    rng = np.random.default_rng(3)
    noisy = (0.2 + 0.01 * rng.standard_normal(d.shape)).astype(np.float32)
    noisy[30:90, 50:110] = 0.8
    g, _ = engine.view_shift(noisy, normalise({"view_shift": {"x": -1.0, "strength": 0.1, "edges": "fill"}})["view_shift"])
    gap = g[35:85, 46:52]                                                             # where the block was, uncovered
    assert gap.max() < 0.3 and gap.std() < 0.004                                      # background, evened out


def test_wrapped_meshes_are_closed_solids_with_the_right_volume():
    from vathopy import mesh
    rng = np.random.default_rng(2)
    for kw in ({"arc_deg": 360}, {"arc_deg": 120}):
        v, f = mesh.cylinder_mesh(rng.random((12, 30)) * 2, 40, 3, **kw)
        assert mesh.check_closed(f) and mesh.volume(v, f) > 0
    v, f = mesh.cylinder_mesh(np.zeros((30, 600)), 40, 3)
    assert mesh.volume(v, f) == pytest.approx(np.pi * (40 ** 2 - 37 ** 2) * v[:, 2].max(), rel=1e-3)
    for kw in ({"lon_deg": 360, "lat_deg": 180}, {"lon_deg": 200, "lat_deg": 180}, {"lon_deg": 90}):
        v, f = mesh.sphere_mesh(rng.random((16, 24)) * 2, 40, 3, **kw)
        assert mesh.check_closed(f) and mesh.volume(v, f) > 0
    v, f = mesh.sphere_mesh(np.zeros((150, 300)), 40, 3, 360, 180)
    assert mesh.volume(v, f) == pytest.approx(4 / 3 * np.pi * (40 ** 3 - 37 ** 3), rel=2e-3)
    with pytest.raises(ValueError):
        mesh.cylinder_mesh(np.zeros((4, 4)), 10, 10)                            # the wall can't fill the radius


def test_gui_finds_photos_and_round_trips_the_new_settings(tmp_path):
    from vathopy import gui
    (tmp_path / "depth").mkdir()
    vathopy.write_depth(depth_field(), str(tmp_path / "depth" / "IMG_7_depth_00001.exr"))
    (tmp_path / "input" / "batch-20260101-000000").mkdir(parents=True)
    import cv2
    cv2.imwrite(str(tmp_path / "input" / "batch-20260101-000000" / "IMG_7.jpg"), np.full((30, 40, 3), 200, np.uint8))
    maps = gui.Maps(tmp_path / "depth")
    assert maps.photo_path("IMG_7_depth_00001.exr").name == "IMG_7.jpg"
    assert maps.photo_path("IMG_8_depth_00001.exr") is None
    s = {"palette": "FRINGE", "selections": [{"x": 0.3, "y": 0.6, "step": 0.004, "range": 0.1, "floors": True, "grow": 2,
                                             "softness": 1.5, "palette": "MAGMA", "palette_invert": True, "cycles": 3,
                                             "invert": False, "enabled": True, "locked": False}],
         "overlay_opacity": 0.4, "overlay_mode": "screen", "overlay_where": "outside", "overlay_grey": True,
         "view_x": -0.5, "view_y": 0.25, "view_strength": 0.08}
    lk = gui.look_from_settings(s)
    assert lk["selections"][0]["ramp"] == {"preset": "MAGMA", "mirror": True} and lk["view_shift"]["x"] == -0.5
    back = gui.settings_from_look(lk)
    for k in ("overlay_opacity", "overlay_mode", "overlay_where", "overlay_grey", "view_x", "view_y", "view_strength"):
        assert back[k] == s[k], k
    assert back["selections"] == [{k: v for k, v in s["selections"][0].items()}]
    photo = maps.photo("IMG_7_depth_00001.exr", lk)
    assert photo.shape == (90, 120, 3) and photo.max() == pytest.approx(200 / 255, abs=1e-3)
    grid = gui.mesh_preview(maps, {"map": "IMG_7_depth_00001.exr", "settings": s, "cols": 24})
    assert grid["cols"] == 24 and len(grid["t"]) == 24 * grid["rows"] and grid["aspect"] == pytest.approx(0.75)


def test_removed_parts_go_with_the_cut_and_stop_at_it():
    from vathopy.look import normalise
    d = np.full((120, 160), 0.2, np.float32)
    d[20:50, 20:60] = d[70:100, 100:140] = 0.8                                  # two blocks, apart
    base = {"ramp": {"preset": "WHITE_TO_BLACK"}, "base": "RAMP"}
    with pytest.raises(ValueError):
        normalise({"cut": {"remove": [{"point": [2, 0.5]}]}})
    plain, _ = vathopy.render(d, base)
    one, _ = vathopy.render(d, {**base, "cut": {"remove": [{"point": [40 / 160, 1 - 35 / 120], "softness": 0}]}})
    assert one[20:50, 20:60].max() == 0.0 and np.array_equal(one[70:100, 100:140], plain[70:100, 100:140])
    # with the background cut away, a part grows only over what's still showing
    cut = {"depth": 0.5, "remove": [{"point": [40 / 160, 1 - 35 / 120], "softness": 0, "step": 0.9}]}
    out, _ = vathopy.render(d, {**base, "cut": cut})
    assert out[20:50, 20:60].max() == 0.0 and np.array_equal(out[75:95, 105:135], plain[75:95, 105:135])
    off, _ = vathopy.render(d, {**base, "cut": {"remove": [{"point": [0.25, 0.7], "enabled": False}]}})
    assert np.array_equal(off, plain)


def test_exaggerate_lifts_details_and_keeps_steps():
    from vathopy import engine
    from vathopy.look import normalise
    h, w = 200, 200
    yy, xx = np.mgrid[:h, :w]
    d = np.where(xx < 100, 0.2, 0.8).astype(np.float32)                          # a step
    d += (0.01 * np.exp(-((xx - 150) ** 2 + (yy - 100) ** 2) / 50.0)).astype(np.float32)   # a small bump on the near side
    assert engine.exaggerate(d, None) is d
    ex = normalise({"exaggerate": {"amount": 3, "size": 0.05}})["exaggerate"]
    e = engine.exaggerate(d, ex)
    bump, bump_e = d[100, 150] - d[100, 180], e[100, 150] - e[100, 180]
    assert bump_e > 2.5 * bump                                                   # the bump stands out more
    assert abs((e[100, 120] - e[100, 50]) - 0.6) < 0.03                          # the step: about as it was
    assert e.min() == pytest.approx(d.min()) and e.max() == pytest.approx(d.max())   # the same range
    out, _ = vathopy.render(d, {"cycles": 2, "exaggerate": {"amount": 3, "size": 0.05}})
    assert out.shape[:2] == d.shape
