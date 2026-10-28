# Palettes

The built-in palettes, and how to add your own.

## Built in

```bash
vathopy presets    # 65 ramp presets: the add-on's 19, 8 complementary palettes, 13 colour-to-black, MIST,
                   # 5 metallic, 19 from scientific and thermal-camera colour maps
```

The 65 built-in palettes live in `src/vathopy/presets.json`. Besides the add-on's and vathopy's own, there are
five metallic ramps (Bronze, Silver, Gold, Chrome, Mercury: shadow, body colour, a bright glint, and a darker
reflection near, so surfaces read as polished metal, rippling with `--cycles`), and colour maps fitted to at most
32 stops (within 4/255 of the originals, a little more for the hue wheels): the perceptually even Magma,
Inferno, Plasma, Viridis and Cividis, the cyclic Twilight, the Turbo rainbow, and thermal-camera false-colour
LUTs from [boson_lut](https://github.com/dannen/boson_lut) (Iron black, Lava, Arctic, Blue lila flame, Electric
blue, Neon, the isotherms Fire and ice, Black hot blue and Mid-range isotherm, Rainbow graded, Colour wheel 6,
Go 4-step). `tools/add_palettes.py BOSON_LUT_DIR` regenerates them.

## Your own palettes

To add your own, put files in a palette folder:
`~/Pictures/vathography/palettes` by default, or `$VATHOPY_PALETTES`, or `vathopy --palettes DIR ...` (before
the command). Each file becomes a palette named after it, upper-cased (`sunset glow.png` → `SUNSET_GLOW`),
listed after the built-ins in `vathopy presets` (marked `[yours: …]`) and in the GUI (marked ★). The GUI re-reads
the folder whenever the page loads, so a new file shows up on a reload.

```bash
mkdir -p ~/Pictures/vathography/palettes
cp examples/palettes/* ~/Pictures/vathography/palettes/     # DUSK (an image) and FOG_GREEN (JSON)
vathopy presets | tail -2
vathopy render depth.exr --preset DUSK -o out/
```

Three ways to make one:

- **An image** (`.png` or `.jpg`): the easiest. Paint or crop a strip and save it; vathopy
  reads it along its long side, **left = near, right = far** (top = near for a tall strip), the way the GUI's
  swatches run. It reads the middle half of the strip, so a border or a label at the edge doesn't count.
  - **Colour blocks** (like `examples/palettes/dusk.png`: five flat blocks): each block becomes one colour stop,
    and the ramp blends between them.
  - **A smooth gradient** (a screenshot of one, a sky, a photo's colour bar): it's sampled into at most 32
    stops (Blender's limit), so it keeps its shape.
- **JSON**, the built-in format, for exact control: positions run from 0 = far to 1 = near, colours are sRGB hex,
  and `interpolation` is `LINEAR`, `EASE`, `CONSTANT` (hard bands), `B_SPLINE` or `CARDINAL`:

  ```json
  {"name": "Fog green", "desc": "Near deep green, far pale fog", "interpolation": "EASE",
   "stops": [[0.0, "#E9EEE8"], [0.45, "#9FB3A2"], [1.0, "#16302A"]]}
  ```

  One file can also hold several, keyed by name: `{"warm": {...}, "cold": {...}}` → `WARM` and `COLD`.
- **A GIMP palette** (`.gpl`, which many palette sites and apps export): its colours are spread evenly, the first
  one near and the last one far.

A file whose name matches a built-in palette is skipped with a warning (rename it), as is a file vathopy
can't read; other file types in the folder are ignored. Looks saved with your palette refer to it by name, so
the palette has to be in the folder wherever the look is rendered again.

**Safety.** Palette files are only ever read as data: JSON is parsed, never evaluated; `.gpl` lines are split
into numbers; images are decoded to pixels. Nothing in them is run, and the GUI shows their names as plain
text, never as HTML. Data can still be hostile, so every file is checked first and rejected, with the reason,
unless it passes:

- only regular files in the folder itself (symlinks aren't followed, so a link can't pull in a file from elsewhere);
- at most 200 palette files; images up to 20 MB, `.json`/`.gpl` up to 256 KB;
- images only as PNG or JPEG, with the size read from the file header **before** decoding and capped at
  25 megapixels, so a tiny file claiming a huge picture (a decompression bomb) is refused;
- 1–32 stops, positions finite numbers from 0 to 1, colours exactly `#RRGGBB`, a known interpolation;
- names and descriptions stripped of control characters (terminal escape codes, hidden text-direction marks)
  and shortened; keys reduced to `A–Z`, `0–9` and `_`; built-in palettes are never replaced.

Test a folder before using it (it exits with an error if anything is rejected):

```bash
vathopy check-palettes ~/Downloads/new-palettes
vathopy check-palettes            # the palette folder itself
```

```
OK        dusk.png        DUSK: 5 stop(s), LINEAR
REJECTED  huge.png        60000 x 60000 pixels; at most 25 megapixels
REJECTED  link.json       not a regular file (symlinks aren't followed)
```
