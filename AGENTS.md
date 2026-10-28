# AGENTS.md: vathopy

For an AI agent working on this repo. `README.md` is the user guide and the reference for every look key and
CLI option; this file says how the code fits together and what to keep true when you change it.

## What it is

A Python port of the picture maths of the free Vathography Blender add-on (GPL-3.0-or-later), plus extras
the add-on doesn't have. Input: a depth map (near = bright, usually Marigold V2 output from ComfyUI).
Output: pictures (PNG/EXR/JPG), 3D-printable STL reliefs and lithophanes, and a local web GUI. CPU only:
numpy, OpenCV (headless) and OpenEXR; no GPU, no Blender at run time.

## Layout

- `src/vathopy/`
  - `cli.py`: `vathopy prep | render | relief | probe | gui | init | check-palettes | presets`.
  - `look.py`: the look (all settings as JSON). `DEFAULT_*` dicts, `normalise()` merges a look onto the
    defaults and validates it; every key the engine reads goes through here.
  - `engine.py`: depth map + look -> picture (add-on maths first, then the vathopy-only stages).
  - `ramp.py`: Blender's ColorRamp evaluation in numpy, the add-on's presets, the 65536-sample lookup.
  - `remap.py`: the add-on's depth transfer (depth controls).
  - `io.py`: reading/checking depth maps, `prep` of raw predictions, writing pictures.
  - `mesh.py`: watertight binary STL (relief, lithophane; flat, cylinder, sphere; deleted parts as holes).
  - `probe.py`: measures a map's wobble and recommends cycles/contours/smoothing.
  - `palettes.py`: user palette files (PNG/JPEG gradients or blocks, presets.json-style JSON, GIMP .gpl) checked and turned into presets.
  - `meta.py`: GUI settings embedded in saved PNGs (tEXt + eXIf + raw EXIF profile) and read back.
  - `par.py`: runs per-pixel steps on several threads, block of rows by block of rows.
  - `gui.py` + `gui.html`: standard-library HTTP server and one plain-JavaScript page; `static/` holds the
    vendored three.js (MIT, `static/three-LICENSE`) and `view3d.js` for the 3D view.
  - `presets.json`: ramp presets (the add-on's plus vathopy's, see `tools/add_palettes.py`).
- `tests/test_vathopy.py`: unit tests, no Blender. `tests/bpy_parity.py`, `tests/bpy_ramp_check.py`:
  parity against the real add-on (need the `bpy` module, see below).
- `tools/`: `extract_look.py` (run in Blender: add-on relief -> look JSON), `make_examples.py` (README
  example pictures and contact sheet), `add_palettes.py` (regenerates the extra presets).
- `examples/`: README pictures, demo/template looks, example palettes.

## Rules that keep it correct

- **Faithfulness first.** For the add-on's own features, vathopy must give the same picture as the add-on's
  artistic export for the same depth map and settings (worst difference so far: one float32 rounding step).
  When the add-on changes, port the maths, extend `tools/extract_look.py` to read the new settings, and make
  `tests/bpy_parity.py` randomize them. Don't "improve" ported maths; put new behaviour in a separate,
  opt-in stage.
- **Extras are off by default.** A look without a key must render exactly as before; new keys get a
  default in `look.py` that does nothing.
- **Row order:** arrays in memory are bottom-up (Blender's order: row 0 = the bottom of the picture), so the
  engine's plane coordinates match the add-on's. `io.load_depth` and the `write_*` functions flip; files on
  disk are normal. Anything that writes a picture from an array (`io.write_png` and friends) expects
  bottom-up input: a top-down array saved directly comes out upside down.
- **Units follow the add-on** (metres on the relief plane, depth in the map's own values). STL is in mm.
- **Untrusted files.** Depth maps, palettes and settings files are data: check sizes from headers before
  decoding (caps in `io.py`), regular files only, `.npy` without pickles, no `eval`. Keep those checks when
  adding formats. The GUI may run on a public URL behind a password; don't make it depend on that.
- **No network at run time.** The GUI page loads nothing from elsewhere (three.js is vendored).
- **Licence:** GPL-3.0-or-later, as a port of a GPL add-on. Only add code you wrote or that has a
  compatible licence, and credit it in the README.

## The GUI

- The page keeps its own settings shape; `gui.py` converts both ways: `look_from_settings()` (page ->
  look) and `settings_from_look()` (look -> page, for loading saved looks and PNGs). Add a control in
  both directions and extend the round-trip test in `tests/test_vathopy.py`.
- Paired coarse/fine sliders (Cycles + Cycles fine, a selection's Edge + Edge fine) are added up into one
  look value; loading a look splits it back. The look format never stores the fine part.
- While a slider moves the page asks for a fast preview; on release, a full render. Per-panel sliders
  (focuses, keep shapes, selections, deleted parts) are built by `buildPanels()` from slider tables.
- The page remembers its settings, theme, sort order and folded sections in `localStorage` (every access
  in try/catch); Reset forgets the settings. Saved looks/pictures go to the folders given on the command
  line, never overwriting.
- Wording on the page: short plain English, tooltips on every control, sizes in pixels.

## Running things

```bash
python -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest -q tests/test_vathopy.py      # unit tests, a few seconds
.venv/bin/vathopy gui --depth-dir DEPTH_DIR               # GUI on 127.0.0.1:8770
```

- Parity tests: a separate venv with `pip install bpy==5.2.2` (the version last checked) and the add-on zip:
  `<that python> tests/bpy_parity.py [n_scenes] [add-on zip]`, `<that python> tests/bpy_ramp_check.py`.
  Run them whenever the ported maths, `look.py` defaults or `extract_look.py` change.
- A check of the page's JavaScript without a browser: extract the main `<script>` from `gui.html` and run
  `node --check` on it. Test GUI changes in a real browser before calling them done.
- The README's example pictures come from `tools/make_examples.py DEPTH_DIR` (needs `.[examples]`).

## Publishing

- This repo is public. Keep personal data out of code, docs, tests, examples and screenshots: no local
  paths or usernames, account or volume IDs, names of private projects, or descriptions of anyone's own
  photos. Test fixtures are synthetic arrays; example pictures use only images whose use is cleared.
  Copyrighted source images stay out (only depth-map renders of them, where that's allowed).
- Other projects may vendor vathopy by commit (e.g. a container image with the GUI); changes reach them only
  when they rebuild, so mention user-visible changes in the commit message.
- The README documents every look key, CLI option and GUI control; update it with the code.
