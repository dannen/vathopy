# 3D printing

STL reliefs and lithophanes, flat or wrapped round a cylinder or a sphere.

`vathopy relief` writes a binary STL (millimetres) of a closed solid on a flat base, ready for a slicer:

```bash
vathopy relief depth.exr -o relief.stl --width-mm 100 --relief-mm 8 --base-mm 2 --smooth-depth 2
vathopy relief depth.exr -o mould.stl --invert                                # far raised
vathopy relief depth.exr -o lithophane.stl --lithophane -l saved.png --min-mm 0.8 --max-mm 3
```

A **relief** raises the near parts `--relief-mm` above the far ones. A **lithophane** prints a rendered look
(from `-l`: a look, or a PNG the GUI saved) as thickness, dark parts thick and light parts thin, so it shows
the picture when lit from behind. Points are 0.2 mm apart by default (`--px` sets how many across, never more
than the map has; `--px 0` uses every pixel); a 100 mm print is about 450,000 triangles, 22 MB, and every pixel
of a 2496 px map about 9.4 million, 470 MB (the most is 20 million). More points only help if the printer can
show them (resin, or a bigger print); `--exaggerate` brings out detail that's there but shallow. The look's
orientation is used.

`--wrap cylinder` or `--wrap sphere` wraps it round instead: the picture's width goes `--arc-deg` round the axis
(360, the default, joins its two ends), its height keeps the picture's proportions (on a sphere `--lat-deg` sets
it; 180 runs pole to pole, closed at the poles), and the relief stands out from `--radius-mm` with a
`--base-mm` thick wall inside it. A lithophane wrapped round a cylinder is a lamp shade.

What the look's cut deletes (`cut`: the slabs and removed parts) is left out of the print by default: holes
where it was, separate pieces where it splits the picture, each on its own base. `--deleted flat` lays it
flat on the base instead, `--deleted keep` prints it anyway. Wrapped prints can't have holes, so there it lies
flat. The relief follows the look's viewpoint shift and exaggeration as well, like the picture, and its
[reshaping](pictures.md#reshaping) and [clean edges](pictures.md#clean-edges); `--no-reshape` leaves those two
out and prints the depth map's own shapes.

```bash
vathopy relief depth.exr -o vase.stl --wrap cylinder --radius-mm 40 --relief-mm 5 --base-mm 2
vathopy relief depth.exr -o globe.stl --wrap sphere --radius-mm 30 --arc-deg 180
vathopy relief depth.exr -o shade.stl --wrap cylinder --lithophane -l saved.png --min-mm 0.8 --max-mm 3
```
