#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Add vathopy's metallic, perceptual, thermal and banded palettes to presets.json.

    python tools/add_palettes.py BOSON_LUT_DIR

* Metallic ramps (bronze, silver, gold, chrome, mercury) are written out below by hand.
* Of the boson_lut tables, those that fit 32 stops well and add something: its double/triple
  rainbows are what ``cycles`` does to one, and colorwheel12_3/rainbowsinus256 don't fit closely.
* Perceptual and thermal-camera colour maps come as 256-colour tables: the matplotlib/OpenCV maps
  (magma, inferno, plasma, viridis, cividis, twilight; turbo) from OpenCV, and the false-colour
  LUTs of github.com/dannen/boson_lut (its .lut files: 256 (B, G, R) tuples, coldest first).
  Each is fitted with at most 32 stops (Blender's limit): starting from both ends, a stop goes where
  the ramp, interpolated in linear light as Blender does, is furthest from the table in 8-bit sRGB,
  until it's within 4 everywhere; tables that need more than 32 that way get their stops slid to
  where they do most good. Hot (the table's end) is near, as bright is near in a depth map.

Rerunning replaces these entries and leaves the others alone. Needs opencv-python and numpy.
"""

import ast
import json
import sys
from pathlib import Path

import cv2
import numpy as np

PRESETS = Path(__file__).resolve().parent.parent / "src" / "vathopy" / "presets.json"
MAX_STOPS, TOLERANCE = 32, 4.0

# Hand-made metallics: positions 0 = far .. 1 = near, sRGB. Each runs from shadow through the
# metal's body colour to a bright specular band and back into a darker reflected tone near the
# viewer, so surfaces read as polished metal; with cycles the sheen repeats like ripples.
METALS = {
    "BRONZE": ("Bronze", "Dark brown shadow, warm bronze, a pale gold glint, then deep bronze near (vathopy)",
               [(0.0, "#160B04"), (0.22, "#4A2A12"), (0.45, "#8C5527"), (0.64, "#C68746"), (0.76, "#EDC28A"),
                (0.84, "#F8DDB4"), (0.92, "#B57638"), (1.0, "#5E3818")]),
    "SILVER": ("Silver", "Graphite shadow, cool grey, a white glint, then mid silver near (vathopy)",
               [(0.0, "#121417"), (0.25, "#41464C"), (0.5, "#8D939A"), (0.68, "#C9CED3"), (0.8, "#F4F6F8"),
                (0.86, "#FFFFFF"), (0.93, "#A8AEB5"), (1.0, "#646A72")]),
    "GOLD": ("Gold", "Umber shadow, rich gold, a pale yellow glint, then deep gold near (vathopy)",
             [(0.0, "#1A0F02"), (0.22, "#5C3A07"), (0.45, "#A87412"), (0.63, "#DDAA2C"), (0.75, "#F7D96B"),
              (0.83, "#FFF3B8"), (0.91, "#D19D2A"), (1.0, "#7E560D")]),
    "CHROME": ("Chrome", "The classic chrome gradient: dark blue, sky blue to white, a hard dark horizon, "
               "tan ground, a white glint near (vathopy)",
               [(0.0, "#07121F"), (0.18, "#24508A"), (0.36, "#7FB4E6"), (0.49, "#F2FAFF"), (0.5, "#2B1E14"),
                (0.6, "#5E4329"), (0.76, "#B79A73"), (0.88, "#F1E4CC"), (0.94, "#FFFFFF"), (1.0, "#A9C2DA")]),
    "MERCURY": ("Mercury", "Liquid metal: near-black, blue-grey, two bright glints with a dark band between (vathopy)",
                [(0.0, "#08090B"), (0.2, "#2C3138"), (0.38, "#6E7783"), (0.52, "#C3CBD4"), (0.58, "#FBFDFF"),
                 (0.66, "#5D6570"), (0.74, "#232830"), (0.84, "#AEB7C2"), (0.9, "#FFFFFF"), (1.0, "#8A939E")]),
}

# 256-colour tables: key -> (name, desc, source). Source "cv2:NAME" or "boson:FILE".
TABLES = {
    "MAGMA": ("Magma", "Black, purple, coral, pale yellow near: perceptually even (matplotlib, CC0)", "cv2:MAGMA"),
    "INFERNO": ("Inferno", "Black, purple, orange, pale yellow near: perceptually even (matplotlib, CC0)", "cv2:INFERNO"),
    "PLASMA": ("Plasma", "Blue, purple, orange, yellow near: perceptually even (matplotlib, CC0)", "cv2:PLASMA"),
    "VIRIDIS": ("Viridis", "Purple, teal, green, yellow near: perceptually even (matplotlib, CC0)", "cv2:VIRIDIS"),
    "CIVIDIS": ("Cividis", "Navy far to yellow near, even for colour-blind viewers (matplotlib)", "cv2:CIVIDIS"),
    "TWILIGHT": ("Twilight", "Pale grey, blue, dark purple, red, back to pale: cyclic (matplotlib)", "cv2:TWILIGHT"),
    "TURBO": ("Turbo", "A smooth rainbow: dark blue, cyan, green, yellow, red near (Google, Apache-2.0)", "cv2:TURBO"),
    "IRON_BLACK": ("Iron black", "Thermal: white far, grey to black, then navy, magenta and orange near (boson_lut ironblack)", "boson:ironblack"),
    "LAVA": ("Lava", "Thermal: teal and violet far, through green to gold near (boson_lut lava)", "boson:lava"),
    "ARCTIC": ("Arctic", "Thermal: blue to gold, then a burst of colour nearest (boson_lut arctic)", "boson:arctic"),
    "BLUE_LILA_FLAME": ("Blue lila flame", "Blue, lilac, pink to a flame near (boson_lut blue_lila_flame)", "boson:blue_lila_flame"),
    "ELECTRIC_BLUE": ("Electric blue", "White and grey far, black, then deep red and violet to electric blue near (boson_lut electricblue)", "boson:electricblue"),
    "NEON": ("Neon", "Shifting neon hues through the depth (boson_lut neon_sin)", "boson:neon_sin"),
    "FIRE_AND_ICE": ("Fire and ice", "Isotherm: flat cyan nearest, a grey ramp, red farthest (boson_lut fire_and_ice_1)", "boson:fire_and_ice_1"),
    "BLACK_HOT_BLUE": ("Black hot, blue", "Isotherm: grey ramp, the nearest third flat blue (boson_lut gf_black_hot_1)", "boson:gf_black_hot_1"),
    "MIDRANGE_ISO": ("Mid-range isotherm", "Grey ramp with a band of colour across the middle depths (boson_lut colorwheel5midrangeiso)", "boson:colorwheel5midrangeiso"),
    "RAINBOW_GRADED": ("Rainbow graded", "Pale cyan near, through blue, green, yellow and red to magenta far (boson_lut rainbowgraded)", "boson:rainbowgraded"),
    "COLORWHEEL_6": ("Colour wheel 6", "Six hues round the colour wheel (boson_lut colorwheel6_3)", "boson:colorwheel6_3"),
    "GO_4STEP": ("Go 4-step", "Thermal: four soft steps of colour (boson_lut go_4step)", "boson:go_4step"),
}


def srgb_to_linear(c):
    c = np.asarray(c, np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return 255.0 * np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def _errors(colours, lin, idx):
    x = np.arange(len(colours))
    approx = linear_to_srgb(np.stack([np.interp(x, idx, lin[idx, k]) for k in range(3)], 1))
    return np.abs(approx - colours).max(axis=1)


def _seg_err(colours, lin, a, b):
    """Worst error between stops a and b (inclusive), interpolating linearly in linear light."""
    t = (np.arange(a, b + 1) - a)[:, None] / max(b - a, 1)
    approx = linear_to_srgb(lin[a] * (1 - t) + lin[b] * t)
    return float(np.abs(approx - colours[a:b + 1]).max())


def fit(colours):
    """256 sRGB colours (coldest first) -> stop indices, linear-light interpolation. Greedy (a stop at
    the worst point) until within TOLERANCE or MAX_STOPS, then, if still short, each inner stop is slid
    to where its two segments' worse error is least, a few passes over all of them."""
    lin = srgb_to_linear(colours)
    idx = [0, len(colours) - 1]
    while True:
        err = _errors(colours, lin, idx)
        if err.max() <= TOLERANCE or len(idx) >= MAX_STOPS:
            break
        idx = sorted(set(idx + [int(err.argmax())]))
    for _ in range(6):
        if _errors(colours, lin, idx).max() <= TOLERANCE:
            break
        for j in range(1, len(idx) - 1):
            lo, hi = idx[j - 1], idx[j + 1]
            idx[j] = min(range(lo + 1, hi), key=lambda m: max(_seg_err(colours, lin, lo, m), _seg_err(colours, lin, m, hi)))
    return idx, float(_errors(colours, lin, idx).max())


def table(source, lut_dir):
    kind, name = source.split(":")
    if kind == "cv2":
        bgr = cv2.applyColorMap(np.arange(256, dtype=np.uint8)[None], getattr(cv2, "COLORMAP_" + name))[0]
    else:
        bgr = np.array(ast.literal_eval((lut_dir / f"{name}.lut").read_text().strip()), np.uint8)
    return bgr[:, ::-1].astype(np.float64)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    lut_dir = Path(sys.argv[1])
    presets = json.loads(PRESETS.read_text())
    for key, (name, desc, stops) in METALS.items():
        presets[key] = {"name": name, "desc": desc, "interpolation": "LINEAR", "stops": [[p, c] for p, c in stops]}
    for key, (name, desc, source) in TABLES.items():
        c = table(source, lut_dir)
        idx, err = fit(c)
        stops = [[round(i / 255, 4), "#%02X%02X%02X" % tuple(int(round(v)) for v in c[i])] for i in idx]
        presets[key] = {"name": name, "desc": desc, "interpolation": "LINEAR", "stops": stops}
        print(f"{key:16s} {len(idx):2d} stops, max error {err:.1f}/255")
    PRESETS.write_text(json.dumps(presets, indent=1) + "\n")
    print(f"{len(presets)} presets in {PRESETS}")


if __name__ == "__main__":
    main()
