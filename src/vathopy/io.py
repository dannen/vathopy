# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading depth maps, preparing raw Marigold V2 predictions, writing pictures.

In memory every array is in Blender's row order (row 0 = bottom of the picture) so the engine's
plane coordinates match the add-on's. Files are written top-down as usual."""

import os

import numpy as np

os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

DEPTH_EXTENSIONS = (".exr", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".npy")

# Depth maps are checked before they're decoded: a small (compressed) file can declare a huge
# picture and exhaust memory, and the decoders are C/C++ code best handed sane input.
MAX_DEPTH_PIXELS = 100_000_000    # 10000 x 10000; Marigold runs well below 4000 px
MAX_DEPTH_SIDE = 32768
MAX_EXR_CHANNELS = 64
# OpenCV's own cap for anything it decodes (PNG, JPEG, TIFF): read when cv2 is first imported.
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(MAX_DEPTH_PIXELS))


def check_size(w, h, path):
    """Refuse a declared picture size that's empty or too big, before decoding it."""
    w, h = int(w), int(h)
    if w < 1 or h < 1:
        raise ValueError(f"{path}: empty picture ({w} x {h})")
    if w > MAX_DEPTH_SIDE or h > MAX_DEPTH_SIDE or w * h > MAX_DEPTH_PIXELS:
        raise ValueError(f"{path}: {w} x {h} pixels is too big (at most {MAX_DEPTH_PIXELS // 1_000_000} "
                         f"megapixels and {MAX_DEPTH_SIDE} px a side)")


def _cv2():
    import cv2
    return cv2


def check_exr_header(path):
    """Read only an EXR's header and refuse files vathopy shouldn't decode: several parts, deep or
    unknown storage, too many channels, or a data window that's empty or too big."""
    import OpenEXR
    with OpenEXR.File(os.fspath(path), header_only=True) as f:   # read it all before the file closes
        parts = len(f.parts)
        hdr = f.header()
        kind, n_channels = hdr.get("type"), len(hdr.get("channels", []))
        window = hdr.get("dataWindow")
        (x0, y0), (x1, y1) = window if window is not None else ((0, 0), (-1, -1))
    if parts != 1:
        raise ValueError(f"{path}: multi-part EXR ({parts} parts) isn't supported")
    if kind is not None and kind not in (OpenEXR.scanlineimage, OpenEXR.tiledimage):   # absent: a flat image
        raise ValueError(f"{path}: only flat (scanline or tiled) EXR images are supported, not {kind}")
    if n_channels > MAX_EXR_CHANNELS:
        raise ValueError(f"{path}: {n_channels} channels; at most {MAX_EXR_CHANNELS}")
    check_size(int(x1) - int(x0) + 1, int(y1) - int(y0) + 1, path)


def _read_exr(path):
    import OpenEXR
    check_exr_header(path)
    with OpenEXR.File(os.fspath(path)) as f:
        ch = f.channels()
        for name in ("R", "Y", "V", "Z", "depth"):
            if name in ch:
                return np.asarray(ch[name].pixels, dtype=np.float32)
        if "RGB" in ch or "RGBA" in ch:
            px = np.asarray(ch.get("RGB", ch.get("RGBA")).pixels, dtype=np.float32)
            return px[..., 0]
        name = sorted(ch)[0]
        px = np.asarray(ch[name].pixels, dtype=np.float32)
        return px if px.ndim == 2 else px[..., 0]


def read_image_channel0(path):
    """First colour channel (red, or grey) as float32, top-down, scaled to 0..1 for integer files —
    the value Blender reads for a Non-Color image."""
    path = os.fspath(path)  # OpenEXR and OpenCV take str only, not pathlib.Path
    ext = os.path.splitext(path)[1].lower()
    if ext == ".npy":
        # allow_pickle=False: a pickled array could run code when loaded. Look at the declared
        # shape and type (memory-mapped, nothing read yet) before loading the data.
        m = np.load(path, mmap_mode="r", allow_pickle=False)
        if m.dtype.kind not in "biuf":
            raise ValueError(f"{path}: expected numbers, got {m.dtype}")
        if m.size > MAX_DEPTH_PIXELS * 4:
            raise ValueError(f"{path}: {m.shape} is too big")
        del m
        a = np.load(path, allow_pickle=False)
        a = np.squeeze(a)
        if a.ndim == 3:
            a = a[..., 0] if a.shape[-1] <= 4 else a[0]
        return a.astype(np.float32), {"format": "npy", "bits": 32}
    if ext == ".exr":
        return _read_exr(path), {"format": "exr", "bits": 32}
    if ext in (".png", ".jpg", ".jpeg"):
        from .palettes import image_size
        check_size(*image_size(path), path)                    # from the header, before decoding
    cv2 = _cv2()
    a = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if a is None:
        raise ValueError(f"cannot read image {path!r}")
    if a.ndim == 3:
        a = a[..., 2] if a.shape[2] >= 3 else a[..., 0]  # OpenCV is BGR(A): red is index 2
    # multiply by the float32 reciprocal: bit-identical to Blender's own integer -> float conversion
    if a.dtype == np.uint8:
        return a.astype(np.float32) * np.float32(1.0 / 255.0), {"format": ext[1:], "bits": 8}
    if a.dtype == np.uint16:
        return a.astype(np.float32) * np.float32(1.0 / 65535.0), {"format": ext[1:], "bits": 16}
    return a.astype(np.float32), {"format": ext[1:], "bits": 32}


def load_depth(path):
    """A depth map (near = bright) as float32 [H, W], rows bottom-up. Returns (array, info)."""
    a, info = read_image_channel0(path)
    if a.ndim != 2:
        raise ValueError(f"{path}: expected a single-channel depth map, got shape {a.shape}")
    info["eight_bit"] = info["bits"] == 8 or info["format"] in ("jpg", "jpeg")
    return np.ascontiguousarray(a[::-1]), info


# --- raw predictions -> a clean near-bright depth map ---------------------------------------

KINDS = ("log", "linear", "disparity", "near-bright", "far-bright")


def prepare_depth(raw, kind="log", low_pct=0.1, high_pct=99.9):
    """Turn a raw prediction (top-down array) into a near = bright map in 0..1 that the add-on and
    the engine read well: invalid pixels filled, outliers clipped at the given percentiles.

    kind: "log" / "linear" (Marigold depth: grows with distance), "disparity" (inverse depth:
    shrinks with distance), "far-bright" (any map where far is bright), "near-bright" (already
    near = bright)."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    d = np.asarray(raw, dtype=np.float64)
    d = np.squeeze(d)
    if d.ndim != 2:
        raise ValueError(f"expected a 2-D prediction, got shape {d.shape}")
    ok = np.isfinite(d)
    if not ok.any():
        raise ValueError("the prediction has no finite values")
    near_bright = kind in ("disparity", "near-bright")
    v = d[ok]
    lo, hi = np.percentile(v, [low_pct, high_pct]) if (low_pct > 0 or high_pct < 100) else (v.min(), v.max())
    if hi - lo <= 0:
        lo, hi = float(v.min()), float(v.max()) + 1e-12
    n = np.clip((d - lo) / (hi - lo), 0.0, 1.0)
    if not near_bright:
        n = 1.0 - n
    n[~ok] = 0.0  # unknown -> farthest
    stats = {"kind": kind, "clip_low": float(lo), "clip_high": float(hi), "percentiles": [low_pct, high_pct],
             "invalid_px": int((~ok).sum()), "clipped_px": int(((d < lo) | (d > hi))[ok].sum())}
    return n.astype(np.float32), stats


# --- writing -------------------------------------------------------------------------------

def srgb_oetf(x):
    x = np.clip(np.asarray(x, dtype=np.float64), 0.0, 1.0)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1.0 / 2.4) - 0.055)


def _rgb(out):
    out = np.asarray(out, dtype=np.float32)
    return out[..., :3] if out.ndim == 3 else np.repeat(out[..., None], 3, axis=2)


def write_png(out, path, bits=16):
    """The picture as the add-on's PNG export writes it: sRGB-encoded (Standard view), 8 or 16 bit.
    ``out`` is bottom-up scene-linear RGB."""
    if bits not in (8, 16):
        raise ValueError("bits must be 8 or 16")
    q = float(2 ** bits - 1)
    codes = np.round(srgb_oetf(_rgb(out)) * q).astype(np.uint8 if bits == 8 else np.uint16)
    cv2 = _cv2()
    path = os.fspath(path)
    if not cv2.imwrite(path, np.ascontiguousarray(codes[::-1, :, ::-1])):
        raise OSError(f"could not write {path}")
    return codes


def write_exr(out, path, channels="RGB"):
    """Scene-linear float32 EXR (ZIP), unclipped. ``channels``: "RGB", or "Y" for one grey channel
    (use "RGB" for depth maps meant for Blender)."""
    import OpenEXR
    rgb = np.ascontiguousarray(_rgb(out)[::-1])
    hdr = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
    if channels == "Y":
        chans = {"Y": np.ascontiguousarray(rgb[..., 0])}
    else:
        chans = {"RGB": rgb}
    path = os.fspath(path)
    with OpenEXR.File(hdr, chans) as f:
        f.write(path)
    return path


def write_depth(depth_bottom_up, path):
    """Write a prepared depth map (bottom-up) as float EXR or 16-bit PNG, chosen by extension."""
    path = os.fspath(path)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".exr":
        return write_exr(depth_bottom_up, path)
    if ext == ".png":
        q = np.round(np.clip(depth_bottom_up, 0.0, 1.0) * 65535.0).astype(np.uint16)
        if not _cv2().imwrite(path, np.ascontiguousarray(q[::-1])):
            raise OSError(f"could not write {path}")
        return path
    if ext == ".npy":
        np.save(path, np.ascontiguousarray(depth_bottom_up[::-1]).astype(np.float32))
        return path
    raise ValueError("write the depth map as .exr, .png (16-bit) or .npy")
