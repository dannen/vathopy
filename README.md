# vathopy

Vathography pictures from depth maps, without Blender. [Vathography](https://vathography.com/) is a practice
by Karim Joseph Nassar: a photograph's inferred depth becomes the artwork, with a *creative focus* deciding
which distances become the subject.

`vathopy` is a Python port of the picture maths of the free **Vathography** Blender add-on
(v1.0.96, © 2026 Karim Joseph Nassar). Given the same depth map and settings, it produces the same picture as
the add-on's artistic export. On top of that it adds things the add-on doesn't have, all off by default.

It runs on any Linux machine with Python 3.10+, numpy, OpenCV and OpenEXR. No GPU is needed: a
24-megapixel picture renders in a few seconds on the CPU.

![A depth map and three vathopy looks](examples/twilight/hero.jpg)

## What it does

You give it a depth map (near = bright), such as one [Marigold V2](https://github.com/huawei-bayerlab/marigold-v2)
estimated from a photo, and a *look*: a JSON file with every setting. It gives you a picture.

- **The add-on's maths:** the colour ramp, the depth controls, the ramp tilt, the Focus & Shape Bands and the
  Focus & Shape Areas (Add, Merge and Mask modes), with their colours and blend modes.
- **vathopy's extras:** fringe cycles and contour lines, smoothing and anti-aliasing, a creative focus picked by
  depth (tilted planes or 3D shapes), cutting away the far or near depths and deleting parts, selections with a
  palette of their own, the photo over the picture, a viewpoint shift, exaggerated depth, edge-aware local
  contrast, and 65 palettes plus your own.
- **A GUI** in the browser to try all of it with sliders, with a 3D view, saving pictures and looks.
- **3D printing:** STL reliefs and lithophanes, flat or wrapped round a cylinder or a sphere.
- **Blender:** a look tuned in the add-on can be exported and rendered here unchanged.

![Contact sheet: four depth maps, eight looks](examples/twilight/contact_sheet.jpg)

## Install

```bash
git clone https://github.com/dannen/vathopy && cd vathopy
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt   # numpy, OpenCV (headless), OpenEXR
pip install -e .                  # installs the `vathopy` command
```

Optional: `pip install -e '.[test]'` (pytest) and `pip install -e '.[examples]'` (Pillow, to remake the
example pictures).

## Quick start

```bash
# A raw Marigold V2 prediction -> a clean depth map (near = bright). ComfyUI's depth EXRs are already
# near = bright: use --kind near-bright for them.
vathopy prep photo.npy --kind log -o photo_depth.exr

# Write a starter look, edit it if you like, render it (a 16-bit PNG).
vathopy init my_look.json --preset FRINGE_3_BW
vathopy render photo_depth.exr -l my_look.json -o photo_vathograph.png

# Or skip the look file: a black-and-white fringe with 4 cycles, smoothed.
vathopy render photo_depth.exr --cycles 4 --smooth-depth 4 --supersample 2 -o out/

# Try looks with sliders in the browser: open http://127.0.0.1:8770/ (Ctrl+C stops it).
vathopy gui --depth-dir DIR_OF_DEPTH_MAPS
```

`vathopy presets` lists the palettes, and `vathopy <command> --help` lists each command's options.

## Documentation

The [manual](docs/README.md) has the details:

- [Pictures](docs/pictures.md): every setting, its `render` option and its look key.
- [Palettes](docs/palettes.md): the built-in ones and your own.
- [The GUI](docs/gui.md): starting it, its options, a password, and every control.
- [3D printing](docs/3d-printing.md): reliefs and lithophanes.
- [Look files, Python and Blender](docs/look-file.md): the look JSON, exporting from the add-on, the Python API.
- [Faithfulness, safety and development](docs/technical.md): parity with the add-on, input checks, tests.

## Credits

- **Vathography**, the practice and the free Blender add-on this ports, by Karim Joseph Nassar:
  [vathography.com](https://vathography.com/), [doi:10.5281/zenodo.23103262](https://doi.org/10.5281/zenodo.23103262).
- **Marigold V2** (Apache-2.0), the depth model the practice is built on:
  [huawei-bayerlab/marigold-v2](https://github.com/huawei-bayerlab/marigold-v2).
- **Local contrast** is the local Laplacian filter of Paris, Hasinoff and Kautz, "Local Laplacian Filters:
  Edge-aware Image Processing with a Laplacian Pyramid" (SIGGRAPH 2011), in the fast sampled form of Aubry,
  Paris, Hasinoff, Kautz and Durand, "Fast Local Laplacian Filters" (ACM TOG 2014).
- The example depth maps were estimated from film stills with Marigold V2.

## Licence

GPL-3.0-or-later (see `LICENSE`), as required for a port of the GPL add-on. The add-on's ramp presets and
algorithms © 2026 Karim Joseph Nassar. Colour maps: Magma, Inferno, Plasma and Viridis by Nathaniel Smith and
Stéfan van der Walt (CC0), Cividis by Jamie R. Nuñez, Christopher R. Anderton and Ryan S. Renslow and Twilight
by Bastian Bechtold (both under matplotlib's licence), Turbo by Anton Mikhailov, © Google (Apache-2.0); the thermal LUTs are from
[boson_lut](https://github.com/dannen/boson_lut). The GUI's 3D view uses [three.js](https://threejs.org/) r170
(MIT, © 2010-2024 three.js authors; `src/vathopy/static/three-LICENSE`), shipped with the package.
