# SPDX-License-Identifier: GPL-3.0-or-later
"""Render the README's example pictures and contact sheet from the twilight depth maps.

    python tools/make_examples.py [DEPTH_DIR] [-o examples/twilight]

DEPTH_DIR holds raw Marigold V2 depth EXRs (near = bright) named twilight2_depth_*.exr,
twilight3_depth_*.exr, and so on (default ~/Pictures/vathography/depth). Each is prepped like the
GUI does (``prep --kind near-bright``). Needs Pillow for the labels, on top of vathopy's own
dependencies. Writes JPEGs; the panels are downscaled from full-size renders.
"""

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import vathopy
from vathopy import io as VIO
from vathopy.gui import FRINGE_RAMP
from vathopy.probe import probe

MAPS = ["twilight2", "twilight3", "twilight4", "twilight5"]
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
LABEL_H = 26
GAP = 6
BG = (250, 250, 250)


def font(size=15):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def load(folder, name):
    files = sorted(Path(folder).glob(f"{name}_depth_*.exr"))
    if not files:
        sys.exit(f"no {name}_depth_*.exr in {folder}")
    raw, _ = VIO.read_image_channel0(str(files[0]))
    d, _ = vathopy.prepare_depth(raw, "near-bright")
    return np.ascontiguousarray(d[::-1])                       # bottom-up, like load_depth


def look(preset=None, **kw):
    """A look dict: ``preset`` None = the black/white fringe ramp; kw = look keys."""
    lk = {"ramp": {"preset": preset} if preset else FRINGE_RAMP}
    lk.update(kw)
    return lk


def render(depth, lk):
    """Top-down 8-bit sRGB."""
    rgb, _ = vathopy.render(depth, lk)
    return np.round(VIO.srgb_oetf(rgb[::-1, :, :3]) * 255).astype(np.uint8)


def depth_view(depth):
    g = np.round(VIO.srgb_oetf(depth[::-1]) * 255).astype(np.uint8)
    return np.repeat(g[..., None], 3, axis=2)


def fit(img, h=None, w=None, square=False):
    """Resize to height ``h`` (or width ``w``); ``square`` centre-crops to 1:1 first, keeping
    more of the top of portrait frames."""
    if square:
        ih, iw = img.shape[:2]
        s = min(ih, iw)
        y = int((ih - s) * 0.3)
        x = (iw - s) // 2
        img = img[y:y + s, x:x + s]
    ih, iw = img.shape[:2]
    if h is None:
        h = round(ih * w / iw)
    if w is None:
        w = round(iw * h / ih)
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)


def labelled(img, text):
    im = Image.new("RGB", (img.shape[1], img.shape[0] + LABEL_H), BG)
    im.paste(Image.fromarray(img), (0, LABEL_H))
    ImageDraw.Draw(im).text((4, 5), text, fill=(30, 30, 30), font=font())
    return np.asarray(im)


def strip(panels):
    """[(image, label)] side by side, same height."""
    tiles = [labelled(p, t) for p, t in panels]
    h = max(t.shape[0] for t in tiles)
    out = np.full((h, sum(t.shape[1] for t in tiles) + GAP * (len(tiles) - 1), 3), BG, np.uint8)
    x = 0
    for t in tiles:
        out[:t.shape[0], x:x + t.shape[1]] = t
        x += t.shape[1] + GAP
    return out


def save(img, path):
    Image.fromarray(img).save(path, quality=88, optimize=True)
    print(f"{path}  {img.shape[1]} x {img.shape[0]}, {Path(path).stat().st_size // 1024} KB")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("depth_dir", nargs="?", default=str(Path.home() / "Pictures/vathography/depth"))
    ap.add_argument("-o", "--out", default=str(Path(__file__).resolve().parent.parent / "examples/twilight"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    d = {n: load(a.depth_dir, n) for n in MAPS}
    smooth = dict(smooth_depth=4, supersample=3)

    # Hero: one map, four looks.
    t2 = d["twilight2"]
    save(strip([
        (fit(depth_view(t2), h=300), "depth map"),
        (fit(render(t2, look(cycles=4, **smooth)), h=300), "--cycles 4"),
        (fit(render(t2, look("VERMILION_INK_CYAN", contours={"cycles": 6}, **smooth)), h=300),
         "VERMILION_INK_CYAN --contours 6"),
        (fit(render(t2, look("COPPER_TO_BLACK", cycles=2, **smooth)), h=300), "COPPER_TO_BLACK --cycles 2"),
    ]), out / "hero.jpg")

    # Cycles: the fringe count.
    t5 = d["twilight5"]
    save(strip([(fit(render(t5, look(cycles=c, **smooth)), h=260), f"--cycles {c}") for c in (1, 2, 4, 6)]),
         out / "cycles.jpg")

    # Contours: colour keeps meaning distance, lines mark even steps.
    pal = "CORAL_PAPER_TEAL"
    save(strip([
        (fit(render(t5, look(pal, **smooth)), h=300), pal),
        (fit(render(t5, look(pal, contours={"cycles": 5}, **smooth)), h=300), "--contours 5"),
        (fit(render(t5, look(pal, contours={"cycles": 10, "width": 3}, **smooth)), h=300),
         "--contours 10 --contour-width 3"),
        (fit(render(d["twilight3"], look(cycles=4, contours={"cycles": 8, "strength": 0.5}, **smooth)), h=300),
         "--cycles 4 --contours 8"),
    ]), out / "contours.jpg")

    # Smoothing and anti-aliasing, a 2x crop so the pixels show.
    y0, x0, s = 120, 80, 300                                    # trees and the walking figure's edge
    def crop(img):
        c = img[y0:y0 + s, x0:x0 + s]
        return cv2.resize(c, (2 * s, 2 * s), interpolation=cv2.INTER_NEAREST)
    save(strip([
        (crop(render(t2, look(cycles=4))), "--cycles 4"),
        (crop(render(t2, look(cycles=4, smooth_depth=4))), "+ --smooth-depth 4"),
        (crop(render(t2, look(cycles=4, smooth_depth=4, supersample=3))), "+ --supersample 3"),
    ]), out / "smoothing.jpg")

    # Probe + equalize: auto contour count, then spread by pixel count.
    t4 = d["twilight4"]
    p = probe(t4)
    n = p["contours"]
    pe = probe(t4, equalize=True)
    save(strip([
        (fit(render(t4, look("EMBER_TO_BLACK", contours={"cycles": n}, smooth_depth=p["smooth_depth"],
                             supersample=3)), h=300), f"--contours auto ({n:g})"),
        (fit(render(t4, look("EMBER_TO_BLACK", contours={"cycles": pe["contours"]},
                             smooth_depth=p["smooth_depth"], supersample=3, equalize=True)), h=300),
         f"--contours auto ({pe['contours']:g}) --equalize"),
    ]), out / "equalize.jpg")
    print(f"twilight4 probe: spread {p['spread']:.2f}, wobble {p['wobble']:.4f}, "
          f"contours {n:g} -> {pe['contours']:g} with equalize")

    # Creative focus: one or more depths lifted out (pixel picks, like clicking in the GUI).
    def at(m, fx, fy):
        h, w = d[m].shape
        return [fx * w, fy * h]
    save(strip([
        (fit(render(t2, look("WHITE_TO_BLACK", base="BLACK", focus=[{"at": at("twilight2", 0.24, 0.45), "width": 0.04,
                                                                     "softness": 0.03}], **smooth)), h=300),
         "--base BLACK --focus @X,Y"),
        (fit(render(t2, look("MIST", focus=[{"at": at("twilight2", 0.24, 0.45), "width": 0.05, "softness": 0.04}],
                             **smooth)), h=300), "MIST --focus @X,Y"),
        (fit(render(t5, look("WHITE_TO_BLACK", base="BLACK", focus=[
            {"at": at("twilight5", 0.92, 0.48), "width": 0.06, "softness": 0.03},
            {"at": at("twilight5", 0.64, 0.55), "width": 0.03, "softness": 0.02, "strength": 0.7}], **smooth)), h=300),
         "two distances"),
        (fit(render(d["twilight3"], look("WHITE_TO_BLACK", base="WHITE", focus=[
            {"at": at("twilight3", 0.45, 0.5), "width": 0.15, "softness": 0.05, "strength": -1}], **smooth)), h=300),
         "--base WHITE, strength -1"),
    ]), out / "focus.jpg")

    # Contact sheet: rows = maps, columns = features.
    cols = [
        ("depth map", None),
        ("--cycles 2", look(cycles=2, **smooth)),
        ("--cycles 4 --contours 8", look(cycles=4, contours={"cycles": 8, "strength": 0.5}, **smooth)),
        ("VERMILION_INK_CYAN --contours 6", look("VERMILION_INK_CYAN", contours={"cycles": 6}, **smooth)),
        ("ICE_TO_BLACK --cycles 4", look("ICE_TO_BLACK", cycles=4, **smooth)),
        ("EMBER_TO_BLACK --equalize", look("EMBER_TO_BLACK", equalize=True, contours={"cycles": 8}, **smooth)),
        ("GOLD_INK_VIOLET --cycles 1.5", look("GOLD_INK_VIOLET", cycles=1.5, **smooth)),
        ("CORAL_PAPER_TEAL", look("CORAL_PAPER_TEAL", **smooth)),
    ]
    tile = 240
    head = 34
    sheet = Image.new("RGB", (len(cols) * (tile + GAP) - GAP, head + len(MAPS) * (tile + GAP) - GAP), BG)
    draw = ImageDraw.Draw(sheet)
    for i, (name, _) in enumerate(cols):
        words, lines = name.split(" "), [""]
        for w in words:                                          # wrap the column label
            if draw.textlength((lines[-1] + " " + w).strip(), font=font(12)) > tile - 4:
                lines.append(w)
            else:
                lines[-1] = (lines[-1] + " " + w).strip()
        draw.multiline_text((i * (tile + GAP) + 2, 2), "\n".join(lines), fill=(30, 30, 30), font=font(12),
                            spacing=2)
    for r, m in enumerate(MAPS):
        for i, (_, lk) in enumerate(cols):
            img = depth_view(d[m]) if lk is None else render(d[m], lk)
            sheet.paste(Image.fromarray(fit(img, h=tile, square=True)), (i * (tile + GAP), head + r * (tile + GAP)))
    save(np.asarray(sheet), out / "contact_sheet.jpg")


if __name__ == "__main__":
    main()
