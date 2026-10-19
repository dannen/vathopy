# SPDX-License-Identifier: GPL-3.0-or-later
"""vathopy: Vathography pictures from depth maps, without Blender.

A port of the picture maths of the Vathography Free Blender add-on (c) 2026 Karim Joseph Nassar,
GPL-3.0-or-later.

    import vathopy
    depth, _ = vathopy.load_depth("photo_depth.exr")
    rgb, info = vathopy.render(depth, vathopy.load_look("look.json"))
    vathopy.write_png(rgb, "photo_vathograph.png", bits=16)
"""

__version__ = "0.1.0"

from .engine import render, remapped
from .io import load_depth, prepare_depth, write_png, write_exr, write_depth
from .look import load as load_look, save as save_look, normalise as normalise_look
from .ramp import Ramp, preset, PRESETS

__all__ = ["render", "remapped", "load_depth", "prepare_depth", "write_png", "write_exr", "write_depth",
           "load_look", "save_look", "normalise_look", "Ramp", "preset", "PRESETS"]
