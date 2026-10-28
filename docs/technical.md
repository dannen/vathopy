# Faithfulness, safety and development

How vathopy is checked against the add-on, how it treats input files, and how to work on it.

## Faithfulness

* `tests/bpy_parity.py` builds random reliefs with the real add-on running in headless Blender
  (`pip install bpy==5.2.2`). The random settings cover ramps, flip, scale, scene depth, held
  ranges, controls, tilt, bands and all area modes and masks, and (add-on 1.0.96) band and area
  colours in every blend mode. Each relief is exported with the add-on's own export code,
  extracted, and rendered with vathopy. Over 150 scenes (128 with colours) the worst difference
  was 6e-8, one float32 rounding step. Depth maps load bit-identically from EXR and from 16-bit
  and 8-bit PNG.
* `tests/bpy_ramp_check.py` compares the colour-ramp evaluator with Blender's on 578 ramps:
  every interpolation, RGB/HSV/HSL, all hue modes, and every preset plus its mirror.
* `tests/test_vathopy.py` holds the unit tests that run without Blender: `pytest tests/test_vathopy.py`.

## Notes

* Arrays are bottom-up in memory (Blender's order) so plane coordinates match the add-on.
  `load_depth`/`write_*` handle the flip, and files on disk are normal.
* Prefer float EXR or 16-bit PNG depth maps; 8-bit maps show stepped bands (the CLI warns).
* Depth maps are checked before they're decoded, since a small compressed file can declare a huge picture and
  the decoders are C/C++ code: an EXR's header is read first (one part, flat, at most 64 channels), PNG and
  JPEG sizes come from their headers, and every format is capped at 100 megapixels and 32768 px a side
  (OpenCV's own cap is set to match, which covers TIFF). `.npy` files load with pickling off, so they can't run
  code, and must hold numbers. The GUI lists regular files only, not symlinks. Keep the `OpenEXR` package up to
  date: it's the parser that sees the file.
* EXRs written by ComfyUI carry its whole workflow in their metadata, including the source photo's file name.
* `prep` clips at the 0.1/99.9 percentiles so a few stray pixels can't flatten the ramp, and
  fills NaN/inf as farthest. The add-on itself uses the raw min/max and would fail on NaN.

## Example pictures

![Contact sheet: four depth maps, eight looks](../examples/twilight/contact_sheet.jpg)

Each row of the contact sheet is one depth map, each column one setting (all with `--smooth-depth 4
--supersample 3`). The depth maps were estimated from film stills with Marigold V2. Regenerate every picture
in `examples/twilight/` (the README's and this manual's) from such maps with:

```bash
pip install -e '.[examples]'
python tools/make_examples.py DEPTH_DIR    # DEPTH_DIR holds twilight2_depth_*.exr ... twilight5_depth_*.exr
```

## Tests

```bash
pip install -e '.[test]'
pytest tests/test_vathopy.py
```

The parity tests above need the `bpy` module (`pip install bpy==5.2.2`, best in a venv of its own) and are
run with that Python: `python tests/bpy_parity.py [n_scenes] [add-on zip]` and `python tests/bpy_ramp_check.py`.
