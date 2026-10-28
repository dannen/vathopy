# vathopy manual

The [README](../README.md) covers installing vathopy and a first picture. This manual has the details.

- [Pictures](pictures.md): how a depth map becomes a picture, and every setting that shapes it, with its
  `vathopy render` options and look keys:
  - [How it works: the relief and the focal planes](pictures.md#how-it-works-the-relief-and-the-focal-planes)
  - [Cycles and contour lines](pictures.md#cycles-and-contour-lines), [Smoother contours](pictures.md#smoother-contours),
    [Probing a depth map for the most detail](pictures.md#probing-a-depth-map-for-the-most-detail)
  - [Creative focus](pictures.md#creative-focus), [Shape focuses](pictures.md#shape-focuses)
  - [The deletion slab](pictures.md#the-deletion-slab), [Selections](pictures.md#selections)
  - [Photo overlay](pictures.md#photo-overlay), [Viewpoint shift](pictures.md#viewpoint-shift)
  - [Exaggerated depth](pictures.md#exaggerated-depth), [Local contrast](pictures.md#local-contrast)
- [Palettes](palettes.md): the 65 built-in palettes, and adding your own (images, JSON, GIMP palettes), with the
  checks they go through.
- [The GUI](gui.md): starting and stopping it, its folders and options, a password, and every control on the page.
- [3D printing](3d-printing.md): STL reliefs and lithophanes, flat or wrapped round a cylinder or a sphere.
- [Look files, Python and Blender](look-file.md): the look JSON key by key, exporting a look from the Blender
  add-on, and using vathopy from Python.
- [Faithfulness, safety and development](technical.md): the parity tests against the add-on, how input files are
  checked, tests and the example pictures.

All the options of each command are also in `vathopy <command> --help`.
