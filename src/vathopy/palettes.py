# SPDX-License-Identifier: GPL-3.0-or-later
"""User palettes: extra ramp presets read from a folder, next to the built-in ones.

Drop files into the palette folder (``--palettes DIR``, ``$VATHOPY_PALETTES``, or by default
``~/Pictures/vathography/palettes``); each becomes a preset named after the file, upper-cased
(``sunset glow.png`` -> ``SUNSET_GLOW``):

* an image (.png .jpg .jpeg): a gradient or a strip of colour blocks, read along its long side,
  left to right (top to bottom when it's taller than wide) as near to far, the way the GUI shows
  swatches. Runs of one colour become one stop, so five blocks make a five-colour gradient; at
  most 32 stops are kept (Blender's limit).
* a .json file: one palette in presets.json's format (``{"name", "desc", "interpolation",
  "stops": [[pos, "#RRGGBB"], ...]}``, pos 0 = far, 1 = near), or several keyed by name.
* a .gpl file (a GIMP palette): its colours spread evenly from near to far.

Palette files are only ever read as data: JSON is parsed (never evaluated), .gpl lines are split
into numbers, and images are decoded to pixels; nothing in them is run. What could still do harm
is data, so every file is checked and rejected (with the reason) unless it passes:

* only regular files directly in the folder: symlinks and anything else are left alone, so a link
  can't make vathopy read a file from elsewhere;
* size limits (images 20 MB, text 256 KB) and at most 200 palette files, read in name order;
* images only as PNG or JPEG, whose width and height are read from the file header first and must
  stay within 25 megapixels, so a small file that claims a huge picture (a decompression bomb) is
  refused before it's decoded;
* 1 to 32 stops, positions finite numbers 0-1, colours exactly ``#RRGGBB``, an interpolation from
  the known list; names and descriptions are stripped of control characters (terminal escape codes
  and the like) and shortened, and keys are reduced to A-Z, 0-9 and _;
* a built-in palette's name is never replaced.

``vathopy check-palettes [DIR]`` runs the same checks and reports each file.
"""

import json
import math
import os
import re
import struct
import sys
import unicodedata
from pathlib import Path

from . import ramp as RAMP

IMAGE_EXTS = (".png", ".jpg", ".jpeg")
TEXT_EXTS = (".json", ".gpl")
MAX_STOPS = 32                   # Blender's colour ramp holds at most 32 elements
SAME_COLOUR = 6                  # 8-bit channels within this of each other are one colour block
MAX_FILES = 200
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_TEXT_BYTES = 256 * 1024
MAX_IMAGE_PIXELS = 25_000_000
MAX_NAME, MAX_DESC = 60, 200
DEFAULT_DIR = "~/Pictures/vathography/palettes"

BUILT_IN = dict(RAMP.PRESETS)
_user = {}                       # key -> source file, of the user palettes now in RAMP.PRESETS


def default_dir():
    return os.environ.get("VATHOPY_PALETTES") or DEFAULT_DIR


def clean_text(text, limit):
    """Printable text only: control and format characters (escape codes, bidi overrides) become
    spaces, runs of space collapse, and it's cut to ``limit`` characters."""
    text = "".join(" " if unicodedata.category(c)[0] == "C" else c for c in str(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit - 1] + "…" if len(text) > limit else text


def make_key(text):
    return re.sub(r"[^A-Z0-9]+", "_", str(text).upper()).strip("_")[:MAX_NAME] or "PALETTE"


def key_for(path):
    return make_key(Path(path).stem)


def _display_name(path):
    name = clean_text(Path(path).stem.replace("_", " "), MAX_NAME)
    return name[:1].upper() + name[1:]


def _hex(rgb):
    return "#" + "".join(f"{min(max(int(round(float(v))), 0), 255):02X}" for v in rgb)


def from_colours(colours, name, desc):
    """sRGB 0-255 colours, near first -> a preset entry (stops spread evenly, pos 0 = far)."""
    n = len(colours)
    if n == 0:
        raise ValueError("no colours")
    if n > MAX_STOPS:
        raise ValueError(f"{n} colours; at most {MAX_STOPS}")
    if n == 1:
        return {"name": name, "desc": desc, "interpolation": "CONSTANT", "stops": [[0.0, _hex(colours[0])]]}
    stops = [[round(1.0 - i / (n - 1), 4), _hex(c)] for i, c in enumerate(colours)]
    return {"name": name, "desc": desc, "interpolation": "LINEAR", "stops": sorted(stops)}


def image_size(path):
    """(width, height) from a PNG or JPEG header, without decoding the picture."""
    with open(path, "rb") as fh:
        head = fh.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            return struct.unpack(">II", head[16:24])
        if head[:2] != b"\xff\xd8":
            raise ValueError("not a PNG or JPEG file")
        fh.seek(2)
        while True:                                           # JPEG: walk the segments to the frame header
            b = fh.read(1)
            while b == b"\xff":
                b = fh.read(1)
            if not b:
                raise ValueError("no JPEG frame header")
            marker = b[0]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                continue                                      # markers without a length
            seg = fh.read(2)
            if len(seg) < 2:
                raise ValueError("truncated JPEG")
            length = struct.unpack(">H", seg)[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                data = fh.read(5)
                if len(data) < 5:
                    raise ValueError("truncated JPEG")
                h, w = struct.unpack(">HH", data[1:5])
                return w, h
            if length < 2:
                raise ValueError("bad JPEG segment")
            fh.seek(length - 2, 1)


def from_image(path):
    """A gradient or colour-block image -> a preset entry."""
    w, h = image_size(path)
    if w < 1 or h < 1 or w * h > MAX_IMAGE_PIXELS:
        raise ValueError(f"{w} x {h} pixels; at most {MAX_IMAGE_PIXELS // 1_000_000} megapixels")
    import cv2
    import numpy as np
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None or img.shape[0] * img.shape[1] > MAX_IMAGE_PIXELS:
        raise ValueError("can't read the image")
    img = img[:, :, ::-1].astype(np.float32)                  # BGR -> RGB
    if img.shape[0] > img.shape[1]:
        img = img.transpose(1, 0, 2)                          # long side across: near -> far
    h, w = img.shape[:2]
    band = img[h // 4: max(h // 4 + 1, 3 * h // 4)]           # the middle half, clear of any border
    line = band.mean(axis=0)                                  # one colour per column
    samples = min(w, 256)
    line = line[np.linspace(0, w - 1, samples).round().astype(int)]
    # runs of (nearly) one colour -> one stop at the run's middle
    runs, start = [], 0
    for i in range(1, samples + 1):
        if i == samples or np.abs(line[i] - line[start]).max() > SAME_COLOUR:
            runs.append((start, i))
            start = i
    colours = [line[a:b].mean(axis=0) for a, b in runs]
    centres = [(a + b - 1) / 2 / max(samples - 1, 1) for a, b in runs]
    if len(colours) > MAX_STOPS:                               # a smooth gradient: even samples
        idx = np.linspace(0, samples - 1, MAX_STOPS).round().astype(int)
        colours, centres = [line[i] for i in idx], [i / (samples - 1) for i in idx]
    name, fname = _display_name(path), clean_text(Path(path).name, MAX_NAME)
    if len(colours) == 1:
        return from_colours(colours, name, f"From {fname}")
    stops = sorted([round(1.0 - c, 4), _hex(col)] for c, col in zip(centres, colours))
    return {"name": name, "desc": f"From {fname} (left/top = near)", "interpolation": "LINEAR", "stops": stops}


def from_gpl(path):
    colours = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 3 and all(p.isdigit() and int(p) <= 255 for p in parts[:3]):
            colours.append(tuple(int(p) for p in parts[:3]))
    return from_colours(colours, _display_name(path), f"From {clean_text(Path(path).name, MAX_NAME)} (GIMP palette)")


def _number(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError
    return float(v)


def check_entry(entry, fallback_name):
    """One JSON palette -> a clean preset entry, or ValueError saying what's wrong."""
    if not isinstance(entry, dict):
        raise ValueError("a palette must be an object with \"stops\"")
    stops = entry.get("stops")
    if not isinstance(stops, list) or not 1 <= len(stops) <= MAX_STOPS:
        raise ValueError(f"\"stops\" must be a list of 1 to {MAX_STOPS} [pos, \"#RRGGBB\"] pairs")
    clean = []
    for st in stops:
        try:
            pos, colour = st
            pos = _number(pos)
        except (TypeError, ValueError):
            raise ValueError("each stop must be [pos, \"#RRGGBB\"] with pos a number 0-1") from None
        if not (0.0 <= pos <= 1.0 and isinstance(colour, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", colour)):
            raise ValueError("each stop must be [pos 0-1, \"#RRGGBB\"]")
        clean.append([pos, colour.upper()])
    interp = entry.get("interpolation", "LINEAR")
    if interp not in RAMP.INTERPOLATIONS:
        raise ValueError(f"interpolation must be one of {', '.join(RAMP.INTERPOLATIONS)}")
    name = clean_text(entry.get("name") or fallback_name, MAX_NAME) or clean_text(fallback_name, MAX_NAME)
    return {"name": name, "desc": clean_text(entry.get("desc") or "", MAX_DESC), "interpolation": interp,
            "stops": clean}


def _read_one(path):
    """{key: entry} from one palette file, or ValueError with the reason."""
    ext = path.suffix.lower()
    size = path.stat().st_size
    limit = MAX_IMAGE_BYTES if ext in IMAGE_EXTS else MAX_TEXT_BYTES
    if size > limit:
        raise ValueError(f"{size // 1024} KB; at most {limit // 1024} KB")
    if ext in IMAGE_EXTS:
        return {key_for(path): from_image(path)}
    if ext == ".gpl":
        return {key_for(path): from_gpl(path)}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "stops" in data:
        return {key_for(path): check_entry(data, _display_name(path))}
    if isinstance(data, dict) and data and len(data) <= MAX_FILES:
        return {make_key(k): check_entry(v, str(k)) for k, v in data.items()}
    raise ValueError("expected a palette, or {name: palette, ...}")


def read_folder(folder):
    """Check every palette file in ``folder``. Returns ({key: entry}, report), report being
    [(file name, "ok" | "rejected" | "skipped", detail)] in file-name order."""
    found, report = {}, []
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        return found, report
    files = [p for p in sorted(folder.iterdir()) if p.suffix.lower() in IMAGE_EXTS + TEXT_EXTS]
    for i, path in enumerate(files):
        fname = clean_text(path.name, 80)
        if i >= MAX_FILES:
            report.append((fname, "skipped", f"more than {MAX_FILES} palette files"))
            continue
        if path.is_symlink() or not path.is_file():
            report.append((fname, "rejected", "not a regular file (symlinks aren't followed)"))
            continue
        try:
            entries = _read_one(path)
        except Exception as e:                                # one bad file doesn't stop the rest
            report.append((fname, "rejected", clean_text(e, 200) or type(e).__name__))
            continue
        for key, entry in entries.items():
            if key in BUILT_IN:
                report.append((fname, "rejected", f"{key} is a built-in palette's name (rename it)"))
            elif key in found:
                report.append((fname, "rejected", f"{key} already comes from {found[key]['source']}"))
            else:
                found[key] = dict(entry, user=True, source=fname)
                report.append((fname, "ok", f"{key}: {len(entry['stops'])} stop(s), {entry['interpolation']}"))
    return found, report


def register(folder=None, quiet=False):
    """(Re)load the user palettes from ``folder`` into the presets: the built-ins first, then these
    in file-name order. Returns the warnings (each rejected or skipped file)."""
    found, report = read_folder(folder or default_dir())
    for key in list(_user):
        RAMP.PRESETS.pop(key, None)
    _user.clear()
    for key, entry in found.items():
        RAMP.PRESETS[key] = entry
        _user[key] = entry["source"]
    warnings = [f"{f}: {status} ({detail})" for f, status, detail in report if status != "ok"]
    if warnings and not quiet:
        for w in warnings:
            print(f"palettes: {w}", file=sys.stderr)
    return warnings
