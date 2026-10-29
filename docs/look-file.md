# Look files, Python and Blender

A look holds every setting as JSON: the GUI saves them, `vathopy render -l` reads them, and a relief tuned in the Blender add-on can be exported to one.

## The look file (JSON)

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
| `orientation` | `{"turns": 0, "mirror": false}` | Turns the finished picture `turns` quarter turns counter-clockwise, then mirrors it left-right if `mirror` (vathopy only; the last step, so focus pivots and `at` stay in the depth map's coordinates) |
| `ramp` | `{"preset": "WHITE_TO_BLACK"}` | `{"preset": KEY, "mirror": bool, "shift": k}` or `{"stops": [[pos, [r,g,b,a]], …], "interpolation", "color_mode", "hue_interpolation", "shift": k}`; colours scene-linear. `shift` (whole number, default 0) moves the colours k stops towards position 0 (the far end, the right of the GUI's swatch), wrapping round, before `mirror` |
| `ramp_near_left` | `false` | Read the ramp with near at its left end (the add-on's default for new reliefs is `true`) |
| `base` | `"RAMP"` | What bands/areas sit on: `RAMP`, `BLACK` or `WHITE` |
| `bands` | `[]` | `{id, name, enabled, strength, interpolation, normal, center, width, softness, color, color_blend}` |
| `areas` | `[]` | `{id, name, enabled, mode: ADD/MERGE/MASK, shape: ELLIPSOID/BOX, position, radius[3], basis[3][3], reach_plus[3], reach_minus[3], strength, falloff, edge, group, mask: {strength, side: HIDE/KEEP, edge, target}, color, color_blend}` |
| `groups` | `{}` | Merge groups: `{"Group 1": {"strength": 1.0, "blend": 0.0, "color": null, "color_blend": "NORMAL"}}` |
| `solo` | `""` | Show only one area (its name) or one band (`"BAND:<id>"`) |
| `depth_scale` | `1.0` | vathopy: how deep the scene is relative to its width, for shape focuses and keep shapes |
| `cut` | `null` | vathopy: the deletion slabs, `{"depth": 0-1 (from far), "near": 0-1 (from near), "softness", "fill": "black"/"white", "keep": [shapes], "remove": [parts]}` (parts: `{"point", "step", "range", "floors", "grow", "softness", "size", "invert"}`) |
| `selections` | `[]` | vathopy: `[{"point": [fx, fy], "step", "range", "floors", "grow", "softness", "size", "ramp": {…} or null, "cycles": n or null, "invert"}]` (see [Selections](pictures.md#selections)) |
| `overlay` | `null` | vathopy: the photo over the picture, `{"opacity", "mode", "where", "grey", "invert", "enabled"}` (see [Photo overlay](pictures.md#photo-overlay)) |
| `view_shift` | `null` | vathopy: `{"x", "y", "strength", "pivot", "edges": "zoom"/"fill"}` (see [Viewpoint shift](pictures.md#viewpoint-shift)) |
| `exaggerate` | `null` | vathopy: `{"amount": 0-10, "size": 0.002-0.2, "enabled"}` (see [Exaggerated depth](pictures.md#exaggerated-depth)) |
| `local_contrast` | `null` | vathopy: `{"amount": 0-1, "range": 0.01-0.5, "compress": 0-0.9, "enabled"}` (see [Local contrast](pictures.md#local-contrast)) |
| `edge_cleanup` | `null` | vathopy: `{"width": 0.5-8 px, "step": 0.002-0.5, "enabled"}` (see [Clean edges](pictures.md#clean-edges)) |
| `reshape` | `[]` | vathopy: `[{"kind": "scale"/"depth"/"bulge"/"push", "point", …}]`, applied in order (see [Reshaping](pictures.md#reshaping)) |

Mask targets: `ALL_BANDS`, `ALL_LIGHTS`, `BAND:<id>`, `LIGHT:<area id>`, `GROUP:<name>`.
Colours (add-on 1.0.96): `color` null = off (strength pushes toward white / black), or `[r, g, b]` scene-linear
0–1: a positive strength pushes toward it and a negative one does nothing. `color_blend`: `NORMAL`, `MULTIPLY`,
`SCREEN`, `OVERLAY`, `SOFT_LIGHT` or `COLOR`. Coloured terms with the same blend are averaged by their amounts and
mixed in after the white / black sum, one blend after another in that order. The free add-on colours bands and
areas; a merge group's colour follows the same maths.
See `examples/demo_look.json` and `examples/template_look.json`. The latter was extracted from the
add-on's template file.
## Bringing a look over from Blender

Tune a relief interactively with the add-on, then dump its settings:

```bash
blender -b my_relief.blend --python tools/extract_look.py -- my_look.json
```

The extractor reads every value through the add-on's own functions: the ramp window (auto,
held or fitted), the scene depth, the pivot, and the area and band transforms. That look then
renders in `vathopy` identically on that depth map. It also works as a reusable preset on other
depth maps; window `"auto"` re-fits the ramp to each new map.
## From Python

```python
import vathopy
depth, info = vathopy.load_depth("photo_depth.exr")      # rows bottom-up, like Blender
rgb, rinfo = vathopy.render(depth, vathopy.load_look("my_look.json"))
vathopy.write_png(rgb, "photo_vathograph.png", bits=16)
```
