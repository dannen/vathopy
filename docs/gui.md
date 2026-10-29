# The GUI

Try looks on depth maps with sliders, in a browser.

A small local web app (Python standard library server + one HTML page, no extra dependencies):

![The vathopy GUI: the depth-map list, palette swatches, sliders, and the rendered picture with its save, view and orientation buttons](../examples/webui-example.jpg)

## Starting it

From the vathopy folder, with the venv from the [README](../README.md#install):

```bash
.venv/bin/vathopy gui           # or `vathopy gui` with the venv activated
```

Then open http://127.0.0.1:8770/ in a browser. It prints the folders it uses and the address, and keeps
running in that terminal.

## Stopping it

Press **Ctrl+C** in its terminal. If you started it in the background (below), stop it with
`pkill -f 'bin/vathopy gui'`. Closing the browser tab doesn't stop it.

To run it in the background, with its messages in a log file:

```bash
nohup .venv/bin/vathopy gui > ~/vathopy-gui.log 2>&1 &
```

## Folders and options

| Option | Default | What it is |
|---|---|---|
| `--depth-dir DIR` | `~/Pictures/vathography/depth` | Where it reads depth maps (`.exr`, raw Marigold output) |
| `--favourites DIR` | `~/Pictures/vathography/favorites` | Where **Save** and **Save local** write PNGs, and **Save** writes looks (`looks/<name>.json`); saved looks are listed from here |
| `--palettes DIR` | `~/Pictures/vathography/palettes` | Your own palettes (see [palettes.md](palettes.md#your-own-palettes)). It goes **before** `gui`; `$VATHOPY_PALETTES` works too |
| `--port N` | `8770` | The port; use another to run two at once |
| `--host ADDR` | `127.0.0.1` | The listen address; `0.0.0.0` makes it reachable from the network (set a password) |
| `--preview-size N` | `1280` | The largest preview, in px, while a slider is dragged |
| `--photos DIR` | `input` beside `--depth-dir` | Where the photos the depth maps were made from are, for **Photo** (searched with subfolders) |

For example, another set of depth maps, saving somewhere else, on a second port:

```bash
.venv/bin/vathopy --palettes ~/my-palettes gui --depth-dir ~/scans/depth --favourites ~/scans/looks --port 8771
```

The folders are fixed while it runs: to change them, stop it and start it again with new options. New depth
maps added to `--depth-dir` show up when you reload the page; so do new palettes.

## Password and other machines

It listens on 127.0.0.1 by default, so only this machine can reach it. To require a password, set
`VATHOPY_GUI_AUTH=user:password` (HTTP basic authentication) when starting it:

```bash
VATHOPY_GUI_AUTH="me:$(cat ~/.vathopy-gui-password)" .venv/bin/vathopy gui --host 0.0.0.0
```

Always do that when it's reachable from a network: it warns if started on a network address without one. To use
a GUI on another machine without opening it to the network, tunnel the port over SSH instead
(`ssh -L 8770:127.0.0.1:8770 thatmachine`, then open http://127.0.0.1:8770/ here).

## Updating

After pulling a new vathopy, stop it and start it again; then reload the page. The editable install (`pip
install -e .`) means no reinstall is needed unless `requirements.txt` changed.

## Using it

- **Depth maps:** a ten-row list at the top of the panel; click one, or click into the list and use the up and
  down arrow keys to step through them. The selected map's name shows above the picture. Only depth EXRs are
  listed; files with `normals`, `albedo` or `bench_` in the name are left out. Each map is prepped (`--kind
  near-bright`) once and kept in memory, and read again if its file changes. **Refresh**, next to the folder
  path (or the R key), picks up maps added to (or removed from) the folder since the page loaded, keeping the selected map if
  it's still there. The menu above the list sorts it by name (A–Z or Z–A), date (newest or oldest first), file
  size or pixel count; the choice is remembered in that browser. Hover over a map for its size in px, file size and date.
- **Panel and theme:** the buttons next to the vathopy title switch between a dark and a light theme (it follows
  the system's setting until you choose; the choice is remembered in that browser) and move the panel to the
  right or left of the picture (kept for the browser session).
- **Remembered across a refresh:** the page keeps its state in the browser (`localStorage`): the depth map, every
  setting (focuses and keep shapes included), the look name, crop and locks, Side by side and the 3D print
  fields, saved on every render. Reloading the page puts it all back; if that map is gone, the settings go onto
  the first map without its focuses and keep shapes. **Reset** (next to the title) asks first, then forgets it all
  and starts from the defaults; the theme, panel side and collapsed sections stay.
- **Sections:** click a heading's ▾ (Palette, Viewpoint, Focus, Cut, Selections, 3D view, …) to collapse it; the browser
  remembers which are collapsed. Each focus panel folds the same way from the ▾ on its left.
- **Palette:** a box three rows of swatches high (scroll for more) with all presets plus the black/white fringe ramp, then your own palettes (marked ★, re-read
  from the palette folder on every page load), with **Invert** (mirror the ramp) and **Equalize**. **Shift
  colours** moves the selected palette's colours along its stops, one stop per step (◀ left, ▶ right); the colour
  pushed off one end comes back in at the other, and the selected swatch shows the result. It has one position
  per colour stop, centred on 0 (a 3-stop palette: −1, 0, +1); **Reset** sets it back to 0. Invert mirrors the
  shifted palette. It's saved in looks as the ramp's `shift`.
- **Sliders:** cycles and contour lines (0 = off, 0.25 steps), line darkness and width, smoothing and
  anti-aliasing. It starts at 2 cycles, 1 px lines, smoothing off and 2× anti-aliasing. Line darkness only
  affects contour lines, so it starts at 0 like them (raise both to draw lines). **Cycles fine** adds
  or subtracts up to 0.25 to the cycles in 0.05 steps (its readout shows the total, e.g. `+0.10 → 2.1`); a
  saved look with, say, 3.4 cycles loads as 3.5 and −0.10. Moving Cycles sets Cycles fine back to 0, unless its
  **Lock** box is ticked, which also stops Cycles fine itself being moved.
- **Step buttons:** every slider has a ◀ and a ▶ either side that move it down or up by one step in its own
  units (0.25 cycles, 1° of lean, 0.005 of depth, …). Clicks only move the value; nothing renders until they've
  stopped for 0.25 s, then the last setting renders once at full quality. Locked focuses disable them too.
- **Typed values:** click a slider's value (the number above its ▶) to type one instead. Enter, or clicking
  elsewhere, sets it as if the slider had been dragged there: kept within the slider's range and rounded to its
  steps (0.4523 in Delete from far becomes 0.450). Esc leaves it as it was. Locked panels don't open the box.
- **Focus:** choose a style (white on black, black on white, or lift/sink over the palette), press **Pick a depth**
  and click the picture: that distance becomes a focus with depth, width, softness and strength sliders (so a
  second distance can come forward less than the subject; at 0 it reads "no effect"), reach width/height/edge to
  isolate it, and lean and angle to tilt it (both centred on 0 and running −90 to 90°: the sign of lean picks
  which way it tips, angle turns it). Shift-click to keep picking, up to 16. Each focus has a checkbox to switch
  it off and on without losing its settings, a **Lock** checkbox that disables its sliders and its remove button
  so they can't be changed by accident, and its depth value folds its sliders away to keep a long list short.
  **⧉ Duplicate** adds a copy right after it with the same settings at the same spot (move it to see it), as the
  add-on's Duplicate does for bands and areas; keep shapes, targets and deleted parts have it too.
  **Colour** pushes a focus toward a colour of your choice instead of lighter or darker, with a blend menu
  (Normal, Multiply, Screen, Overlay, Soft light, Colour only); it works the same whatever the focus style.
  Choosing another depth map clears the focuses, since their depths belong to the old map.
- **Highlights / Result:** while you pick and adjust, the picture shows the look without its focus, greyed,
  with each focus's region tinted in its own colour (its panel's colour), fainter the lower its strength, so
  several focuses and the rest of the picture stay visible. The tabs at the top right (or the T key) switch to
  the result and back.
- **Speed:** while a slider moves, a fast preview renders (no anti-aliasing, at most `--preview-size` px,
  typically 50–100 ms); on release, the full-quality render (about 0.2–0.7 s at ~1200 px). The time of the last
  render shows at the bottom left. Smoothing is computed once per radius.
- **Clean edges** (Quality): cleans up the ragged run of stray colours along the edges between depths (see
  [Clean edges](pictures.md#clean-edges)); on to start with. **On** switches it off and on; the slider sets how
  far either side of an edge it cleans (4 px to start with). **Remove specks** (9 px to start with, 0 = keep them)
  clears the little specks of depth that stand out from what's round them, such as stray hairs.
- **Magnifier** (the Z key, either case, or the lens button at the top left of the picture area turns it on and
  off; off to start with): a 200 px circle follows the mouse
  over the picture (either one in Side by side) and shows it 3× larger, pixels unsmoothed; the scroll wheel
  sets 1.5× to 16× while it's on. Clicking still picks spots.
- **Auto (probe):** sets smoothing and the cycle or contour count to the most the map carries cleanly (see
  [Probing](pictures.md#probing-a-depth-map-for-the-most-detail)).
- **Save local** (under the picture): writes the full-quality result as a 16-bit PNG to `--favourites` on the
  machine running vathopy, named `<depth map>_<look name or "vathopy">.png` and never overwriting (`-001`,
  `-002`, …). It saves the picture only, not the look.
- **Download PNG** (under the picture, right of Save local): the same 16-bit PNG, downloaded straight to the
  browser, without saving anything on the server. Both are always the result, even while the Highlights view is
  showing.
- **Side by side** (under the picture): shows the inverse (Invert flipped) to the right of the picture, and adds
  **Save inverse** and **Download inverse**, which work like Save local and Download PNG for the right-hand
  picture, named `<depth map>_<look name or "vathopy">_inverse.png`. Each render then makes both pictures, so
  it's a little slower. Picking a depth works on either picture.
- **Orientation** (the row under the picture): ⟲ 90° and ⟳ 90° turn the picture, ⇆ Flip mirrors it left to
  right, ⇅ Flip turns it upside down, Reset puts it back. It applies to both pictures in Side by side, to every
  save and download, and is saved in looks and embedded settings (look key `orientation`). Choosing another
  depth map resets it. Focus points stay on the same spot of the scene, and the crop follows the picture as
  shown: Top cuts the top of the picture you see, with the red shade and the loupes to match. The cropped EXR
  itself keeps the depth map's own orientation.
- **Looks:** type a name and press **Save** to write the full-quality PNG to `--favourites` (never overwriting)
  and the look to `looks/<name>.json`. Saved looks load back into the controls from the list, and `vathopy
  render -l looks/<name>.json` reproduces the PNG exactly. After a save, the page links the PNG and the look,
  so they can be downloaded when the GUI runs on another machine (such as a RunPod pod).
- **Settings in the pictures:** every PNG the GUI writes (Save, Save local, Download PNG and the inverse ones)
  carries all of the page's settings, the look and the depth map's name, the way ComfyUI keeps its workflow in
  its PNGs: in a `vathopy` text chunk and in EXIF (UserComment, as an `eXIf` chunk and as the "Raw profile type
  exif" chunk ImageMagick 6 and exiv2 read). Converting the PNG to a JPG with ImageMagick (`convert a.png
  a.jpg`, resized or not) keeps the EXIF copy; tools that strip metadata (`-strip`, most web uploads) lose it.
- **Export settings / Load from file…** (Looks section): Export downloads the same as
  `<map>_<name>.vathopy.json`. Load takes a PNG or JPG vathopy saved, such a `.vathopy.json`, or a look JSON
  (several files at once: the first that has settings wins) and puts every control back, focuses included,
  so you can carry on editing. It switches to the depth map the file was made from when that's in the list;
  otherwise the settings go onto the current map. `vathopy render -l` takes the same files, so `vathopy render
  prepped.exr -l picture.jpg` remakes the picture.
- **Crop:** trims the depth map itself, e.g. to drop edge pixels the depth model got wrong. Each side (and
  **All**, every side at once) has −10, −5, −1, +1, +5 and +10 px buttons; at least 1 px always stays. The part
  that goes is shaded red on the picture, and four loupes show each corner of the crop, 24 px of the map across,
  so single pixels are visible. **Save local** writes `<map>-cropped.exr` into the depth-map folder (replacing an
  earlier crop of that map) and lists it; **Download EXR** sends the same file to the browser and writes nothing
  on the server, which is the way to get it from a pod. The crop keeps every channel, the pixel type, the
  compression and all header attributes (such as ComfyUI's prompt and workflow), and the file is read back and
  compared before it's kept. Choosing another depth map sets the crop back to 0, unless the **Lock** box next to
  Reset is ticked; then it's kept (fitted to the new map's size). From Python:
  `vathopy.crop_exr(src, dst, top=1, right=1, bottom=1, left=1)`.
- **Shapes:** each focus has a shape menu (Plane, Sphere / ellipsoid, Box, Cylinder (rod), Cone) and Shell or
  Solid; the sliders change with the shape (sizes, radius and length, cone angle). **Depth scale** under the
  focuses stretches depth for shapes. Highlights tint a shape's region like a plane's.
- **Cut:** **Delete from far** drags the deletion slab from the farthest depth (0, off) to the nearest (1,
  everything); **Delete from near** does the same from the nearest depth, so both together leave a band (the
  section's **Reset** sets both back to 0). Edge
  softness fades them, and deleted parts become black or white. **Keep a shape**, then click
  the picture: a sphere there (change it to a box, rod or cone, size and turn it) survives the cut. **Delete a
  part**, then click the picture: the thing there, up to its depth edges, is deleted too (black or white, like
  the cut), shown by a red Ⓓ. It spreads only over what's still showing, so with Delete from far at 0.210 a
  click on a leftover patch at the top takes just that patch. Each has Edge, Depth range, Grow and Soft edge
  sliders, Stop at floors and Everything else. Keep shapes and deleted parts are cleared when another depth map
  is chosen, like focuses.
- **Points** (under the picture, or the P key): a dot on the picture for each focus (F1…), keep shape (K1…),
  target (T1…) and deleted part (D). Drag one to move it: a focus or keep shape takes the depth under its new
  place (hold Shift as you let go to keep its depth), a target or deleted part grows again from there. A click opens its panel; locked ones don't
  move.
- **Viewpoint:** Left / right and Down / up shift the view (see [Viewpoint shift](pictures.md#viewpoint-shift)), Amount sets how far.
  The picture is enlarged a little so the uncovered strips along its borders don't show.
- **Exaggerate depth** (under Shape): makes bumps, folds and faces stand out (see [Exaggerated depth](pictures.md#exaggerated-depth)); **On**
  switches it off and on without losing its settings. Detail size says how big the things it brings out are
  (Reset puts it back to 2%). The 3D view and 3D print use it too. **Local contrast**, **Detail depth** and
  **Shrink steps** below it are the edge-aware local contrast (see [Local contrast](pictures.md#local-contrast)).
- **Selections:** **Pick a target**, then click a person or thing: it's selected up to its depth edges and gets a
  palette of its own (a colour, to start with). Each target has a palette menu ("Same palette as the picture"
  keeps the picture's), Reverse palette, **Stop at floors**, Everything else, and sliders for Edge (the step
  that stops it: lower if it spills onto what's behind), Edge fine (−5…+5 steps of 0.0001 added to Edge), Depth
  range, Grow, Soft edge and its own Cycles.
  Targets are cleared when another depth map is chosen.
- **Reshape:** **Pick a spot**, then click the picture. Each spot gets a panel (R1… on the picture) with a menu:
  **Grow / shrink** (Scale, and whether it grows from its middle or its bottom), **Nearer / farther** (how much,
  and Relief: deeper or flatter), **Bulge / pinch** (Amount) and **Push / stretch** (Direction and Distance);
  the last two work on a round brush (Radius), and with **Only this thing** on just the thing under the spot,
  leaving what's round it alone. Grow / shrink, Nearer / farther and Only this thing pick the thing round the
  spot: **Size** says how far round it they reach along the surface, so a small one takes one arm and not the
  other (scroll over its pin to change it), with **Size fine** (−5…+5 steps of 0.002), Edge and Edge fine, Depth
  range, Grow, Soft edge, Stop at floors and Everything else. While you move those sliders the picture switches
  to Highlights, with what it picked (or the brush) tinted teal, before the reshaping; the other sliders show
  the result. Drag a pin to move it. **Reset** sets a panel's sliders and boxes back to the defaults; the spot
  and the kind stay. See [Reshaping](pictures.md#reshaping). Cleared when another depth map is
  chosen.
- **Photo:** finds the photo each depth map was made from in `--photos` by name (`<photo>_depth_00001.exr` comes
  from `<photo>.jpg`, `.png` or another picture type; batch uploads in `batch-*` subfolders count; the newest
  wins) and says which, or where it looked. **Photo over the picture** blends it in, with a blend mode, where it
  shows (everywhere, only on the targets, or outside them), Black and white and Invert (its negative). **On**
  (or the O key) shows or hides it without losing its settings.
- **3D view:** a small low-poly wireframe of the depth map's surface with the keep shapes, shape focuses, focus
  planes and the cut's slabs in it, to see where they sit in space. Drag to turn it, scroll to zoom, right-drag
  to move it, double-click (or Start view) to go back. With 3D print set to a cylinder or sphere it shows the
  picture wrapped round that instead. It uses three.js, which comes with vathopy; nothing is fetched from
  elsewhere.
- **3D print:** Flat, or wrapped round a cylinder or a sphere (radius, how far round, and on a sphere how far
  from top to bottom; the base becomes the wall inside), Relief or Lithophane, width, points across, relief height and base (or thinnest/thickest),
  Far raised, Use the crop, **Detail**: Points across as typed, **medium** (50% of the map's pixels),
  **large** (75%) or **maximum** (every pixel: the most detail, big files), and **Deleted parts**: what Delete
  from far / near and deleted parts take away is **left out** (holes, and separate pieces; the default),
  **flat on the base**, or printed anyway. The print follows the picture's viewpoint and exaggeration too, and
  with **Follow reshaping** (on to start with) the Reshape panels and Clean edges; off, it prints the depth
  map's own shapes, and the 3D view shows them too. **Save local** writes the STL into `--favourites` on the machine running
  vathopy (`<map>_<look name or "vathopy">_relief.stl` or `…_lithophane.stl`, never overwriting: `-001`, …);
  **Download STL** sends the same file to the browser and writes nothing on the server. Both build it from the
  map (cropped and turned as the picture is shown) and say its size and triangle count. PrusaSlicer reads the
  files as one manifold part and slices them as they are.
