# vathopy

Vathography pictures from depth maps, without Blender.

`vathopy` is a Python port of the picture maths of the free **Vathography** Blender add-on
(v1.0.95, © 2026 Karim Joseph Nassar). It covers the colour ramp, the depth controls, the ramp
tilt, the Focus & Shape Bands and the Focus & Shape Areas (Add, Merge and Mask modes). Given the
same depth map and settings, it produces the same picture as the add-on's artistic export.

It runs on any Linux machine with Python 3.10+, numpy, OpenCV and OpenEXR. No GPU is needed: a
24-megapixel picture renders in a few seconds on the CPU.

## Install

```bash
pip install -e .          # from this folder; installs the `vathopy` command
pip install -e '.[test]'  # plus pytest
```

## Typical pipeline

```bash
# 1. Raw Marigold V2 prediction (.npy, float) -> clean depth map: near = bright, outliers clipped,
#    invalid pixels filled. Kinds: log (default), linear, disparity, near-bright, far-bright.
vathopy prep photo.npy --kind log -o photo_depth.exr

# 2. Render a look onto it (16-bit PNG by default; also png8, or exr for unclipped scene-linear).
vathopy init my_look.json --preset FRINGE_3_BW     # a starter look to edit
vathopy render photo_depth.exr -l my_look.json -o photo_vathograph.png

# Batch: several inputs into a folder; override the ramp without editing the look.
vathopy render shots/*_depth.exr -l my_look.json --preset VERMILION_INK_CYAN -o out/

vathopy presets    # 40 ramp presets: the add-on's 19, 8 complementary palettes, 13 colour-to-black
```

### Cycles and contour lines

Two render options that the add-on doesn't have (`N` is 1 to 10 in steps of 0.5):

```bash
vathopy render depth.exr --cycles 4 -o out/                                  # black/white 4-cycle fringe
vathopy render depth.exr --preset VERMILION_INK_CYAN --cycles 2.5 -o out/    # the colours repeat 2.5 times
vathopy render depth.exr --preset VERMILION_INK_CYAN --contours 5 -o out/    # gradient kept, 5 contour lines
vathopy render depth.exr --preset VERMILION_INK_CYAN --contours 5 --contour-strength 0.6 --contour-width 3 -o out/
```

- **`--cycles N`** repeats the ramp N times across the depth, mirroring every other repeat so there are no seams. One cycle is there and back, so whole numbers end on the ramp's far colour at the near end; use a half step (1.5, 2.5, …) to end on its near colour. With no `--preset` or look ramp it uses a black/white EASE ramp, which is the N-cycle fringe: more cycles means more contour bands. On smooth surfaces (floors, walls, open ground) 4–6 cycles read well; on noisy depth (sky, foliage, film grain) more cycles mostly magnify the noise.
- **`--contours N`** keeps the ramp as it is (so colour still means distance) and draws thin dark lines at N even steps through the depth, like a topographic map. `--contour-strength` (0–1, default 0.85) sets how dark they are, and `--contour-width` (pixels, default 2) their thickness. Lines keep that width everywhere: steep and gentle slopes alike, and on nearly flat surfaces where a depth-based width would smear into a wide band. Where many lines crowd into a pixel (steep depth edges) it gets their average darkness, and perfectly flat areas (such as the plateaus `prep` clipping leaves at the nearest and farthest values) get no line, just one around their rim.

Both are also look keys: `"cycles": 2.5` and `"contours": {"cycles": 5, "strength": 0.85, "width": 2}`. They're off by default, so other looks render exactly as before.

### Smoother contours

```bash
vathopy render depth.exr --preset VERMILION_INK_CYAN --contours 8 --smooth-depth 4 --supersample 3 -o out/
```

- **`--smooth-depth R`** smooths the depth map before rendering with an edge-preserving (bilateral) filter, radius R px (try 2–6). Small depth noise inside surfaces is evened out, so contour lines and fringes follow the surfaces in smooth curves instead of breaking up, while the steps between objects stay sharp. This is the main fix for ragged contours, especially on noisy depth (film scans, foliage, sky).
- **`--supersample N`** (1–4) anti-aliases: it renders at N× the resolution and averages back down, so line and edge pixels get in-between values instead of hard steps. It softens stair-stepped edges. It costs about N² in time, which is still well under a second per 1280 px picture at N = 3.

Both apply to everything (contours, cycles, bands, area edges), are look keys (`"smooth_depth": 4`, `"supersample": 3`), and are off by default. The output is always the depth map's own size.

### GUI: try looks with sliders

```bash
vathopy gui                     # then open http://127.0.0.1:8770/
vathopy gui --depth-dir DIR --favourites DIR --port 8770
```

A small local web app (Python standard library server + one HTML page, no extra dependencies):

- **Depth map:** picks from the `.exr` files in `--depth-dir` (default `~/Pictures/vathography/depth`, raw Marigold output); each is prepped (`--kind near-bright`) once and kept in memory.
- **Palette:** swatches of all presets plus the black/white fringe ramp, with **Invert** (mirror the ramp) and **Equalize**.
- **Sliders:** cycles and contour lines (0 = off, 0.5 steps), line darkness and width, smoothing and anti-aliasing.
- **Speed:** while a slider moves, a fast preview renders (no anti-aliasing, at most `--preview-size` px, typically 50–100 ms); on release, the full-quality render (about 0.2–0.7 s at ~1200 px). Smoothing is computed once per radius. Click the picture for 100% view.
- **Auto (probe):** sets smoothing and the cycle or contour count to the most the map carries cleanly (see Probing below).
- **Looks:** **Save** writes the full-quality 16-bit PNG to `--favourites` (default `~/Pictures/vathography/favorites`, never overwriting) and the look to `looks/<name>.json`; saved looks load back into the controls, and `vathopy render -l looks/<name>.json` reproduces the PNG exactly.

- **Downloads:** after a save, the page links the PNG and the look, so they can be downloaded when the GUI runs on another machine (such as a RunPod pod).
- **Listed maps:** depth EXRs only; files with `normals`, `albedo` or `bench_` in the name are left out.

It listens on 127.0.0.1 by default. To require a password, set `VATHOPY_GUI_AUTH=user:password` (HTTP basic authentication). Always do that when it's reachable from a network: it warns if started on a network address without one.

### Probing a depth map for the most detail

```bash
vathopy probe depth.exr          # measure it and print recommended settings
vathopy render depth.exr --cycles auto --smooth-depth auto --supersample 3 -o out/
vathopy render depth.exr --preset EMBER_TO_BLACK --contours auto --smooth-depth auto --equalize -o out/
```

`vathopy probe` measures what limits detail and recommends the most contour lines or fringe cycles the map can carry cleanly:

- **Depth wobble:** gentle bumps a few to tens of pixels across in surfaces that should be smooth. A contour line drifts sideways by (wobble ÷ slope) pixels, and lines are (level step ÷ slope) pixels apart, so the slope cancels: lines tangle once the step between levels nears a few times the wobble. The number of clean levels is about 1 ÷ (34 × wobble). It's measured at the 80th percentile of the surfaces rather than the median, because lines look broken as soon as a sizeable region (a noisy sky, foliage) tangles, even if the rest is clean. One contour line per level; two light/dark edges per fringe cycle.
- **Smoothing:** recommended in proportion to the picture size (4 px at a 1280 px long edge).
- **Spread:** how much of the depth range the middle half of the pixels covers. Below 40%, most colours and contours land on a small part of the picture, and it suggests `--equalize`.

Calibrated on test photos: a grainy film scan comes out at about 5 cycles (where it visibly broke up), a photo with a noisy sky at 6.5, a clean portrait at 9. Every map tested supports at least 10 contour lines, the option's maximum. It's a ceiling for detail, not a style choice: fewer cycles can look better.

- **`auto`** for `--cycles`, `--contours` or `--smooth-depth` probes each depth map separately and prints what it chose (`auto: smooth-depth 4, cycles 6.5`).
- **`--equalize`** spreads the ramp by pixel count (histogram equalisation), so each colour band and contour level covers about the same share of the picture. It helps most on maps the probe flags, such as a dark room where most pixels sit in a narrow depth band. Look key: `"equalize": true`.

From Python:

```python
import vathopy
depth, info = vathopy.load_depth("photo_depth.exr")      # rows bottom-up, like Blender
rgb, rinfo = vathopy.render(depth, vathopy.load_look("my_look.json"))
vathopy.write_png(rgb, "photo_vathograph.png", bits=16)
```

## Bringing a look over from Blender

Tune a relief interactively with the add-on, then dump its settings:

```bash
blender -b my_relief.blend --python tools/extract_look.py -- my_look.json
```

The extractor reads every value through the add-on's own functions: the ramp window (auto,
held or fitted), the scene depth, the pivot, and the area and band transforms. That look then
renders in `vathopy` identically on that depth map. It also works as a reusable preset on other
depth maps; window `"auto"` re-fits the ramp to each new map.

## Look file (JSON)

Units are the add-on's. Depth values are the map's own values (near = bright). The relief is a
plane `width` × `height` metres, centred on 0, with x to the right and y up. A pixel's height is
`z = (remapped_depth − midlevel) × scale` metres, and bands and areas are placed in that space.
Every key is optional; unknown keys are rejected so that typos don't pass silently.

| Key | Default | Meaning |
|---|---|---|
| `plane` | `{"width": 1.5, "height": null, "scale": 4.0, "midlevel": 0.5}` | Relief size and depth scale. `height` null = width ÷ aspect. `scale`/`midlevel` are the add-on's *effective* values (Scale × scene depth, pivot-adjusted midlevel) |
| `flip` | `false` | Invert the depth (for far = bright maps) |
| `controls` | `[]` | Depth remap controls: `{center, offset, feather, from_min, from_max}` in depth units |
| `window` | `"auto"` | Ramp range `[lo, hi]` in remapped depth, or `"auto"` = the remapped map's min..max |
| `tilt` | `null` | Ramp tilt: `{normal: [x,y,z], x0, y0, fit}` |
| `ramp` | `{"preset": "WHITE_TO_BLACK"}` | `{"preset": KEY, "mirror": bool}` or `{"stops": [[pos, [r,g,b,a]], …], "interpolation", "color_mode", "hue_interpolation"}`; colours scene-linear |
| `ramp_near_left` | `false` | Read the ramp with near at its left end (the add-on's default for new reliefs is `true`) |
| `base` | `"RAMP"` | What bands/areas sit on: `RAMP`, `BLACK` or `WHITE` |
| `bands` | `[]` | `{id, name, enabled, strength, interpolation, normal, center, width, softness}` |
| `areas` | `[]` | `{id, name, enabled, mode: ADD/MERGE/MASK, shape: ELLIPSOID/BOX, position, radius[3], basis[3][3], reach_plus[3], reach_minus[3], strength, falloff, edge, group, mask: {strength, side: HIDE/KEEP, edge, target}}` |
| `groups` | `{}` | Merge groups: `{"Group 1": {"strength": 1.0, "blend": 0.0}}` |
| `solo` | `""` | Show only one area (its name) or one band (`"BAND:<id>"`) |

Mask targets: `ALL_BANDS`, `ALL_LIGHTS`, `BAND:<id>`, `LIGHT:<area id>`, `GROUP:<name>`.
See `examples/demo_look.json` and `examples/template_look.json`. The latter was extracted from the
add-on's template file.

## Faithfulness

* `tests/bpy_parity.py` builds random reliefs with the real add-on running in headless Blender
  (`pip install bpy==5.2.2`). The random settings cover ramps, flip, scale, scene depth, held
  ranges, controls, tilt, bands and all area modes and masks. Each relief is exported with the
  add-on's own export code, extracted, and rendered with vathopy. Over 100 scenes the worst
  difference was 6e-8, one float32 rounding step. Depth maps load bit-identically from EXR and
  from 16-bit and 8-bit PNG.
* `tests/bpy_ramp_check.py` compares the colour-ramp evaluator with Blender's on 578 ramps:
  every interpolation, RGB/HSV/HSL, all hue modes, and every preset plus its mirror.
* `tests/test_vathopy.py` holds the unit tests that run without Blender: `pytest tests/test_vathopy.py`.

## Notes

* Arrays are bottom-up in memory (Blender's order) so plane coordinates match the add-on.
  `load_depth`/`write_*` handle the flip, and files on disk are normal.
* Prefer float EXR or 16-bit PNG depth maps; 8-bit maps show stepped bands (the CLI warns).
* `prep` clips at the 0.1/99.9 percentiles so a few stray pixels can't flatten the ramp, and
  fills NaN/inf as farthest. The add-on itself uses the raw min/max and would fail on NaN.

## Licence

GPL-3.0-or-later (see `LICENSE`), as required for a port of the GPL add-on. Ramp presets and
algorithms © 2026 Karim Joseph Nassar.
