# SPDX-License-Identifier: GPL-3.0-or-later
"""The GUI's settings embedded in the pictures it writes, and read back from PNGs, JPGs and JSON.

A PNG gets the same JSON twice (the way ComfyUI keeps its workflow in its PNGs, plus EXIF):

* a ``tEXt`` chunk with the keyword ``vathopy``;
* an ``eXIf`` chunk whose EXIF UserComment holds it, and the same EXIF again as a "Raw profile type
  exif" zTXt chunk (the older convention ImageMagick 6 and exiv2 read), so converting the PNG to a JPG
  with ImageMagick or GIMP carries it over as the JPG's EXIF (a text chunk gets lost on the way).

The JSON is ``{"vathopy_settings": 1, "map": <depth map>, "name": <look name>, "settings": {the
GUI's controls}, "look": {the vathopy look}}``; "Export settings" in the GUI writes the same as a
file. :func:`read` finds it in any of those, and also takes a plain look JSON (``looks/*.json``).
Standard library only.
"""

import json
import struct
import zlib

KEYWORD = b"vathopy"
RAW_EXIF = b"Raw profile type exif"
FORMAT = 1
PNG_SIG = b"\x89PNG\r\n\x1a\n"
_USER_COMMENT, _IMAGE_DESCRIPTION, _EXIF_IFD = 0x9286, 0x010E, 0x8769


def payload(settings, look, map_name="", name=""):
    """The dict that's embedded (and exported); "vathopy_settings" first, so it's findable as text."""
    return {"vathopy_settings": FORMAT, "map": map_name or "", "name": name or "",
            "settings": settings, "look": look}


def _text(data):
    return json.dumps(data, separators=(",", ":"), ensure_ascii=True)


def _chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def _exif(text):
    """A little-endian TIFF block: IFD0 -> Exif IFD -> UserComment ("ASCII" + the text)."""
    comment = b"ASCII\0\0\0" + text.encode("ascii")
    ifd0 = struct.pack("<H", 1) + struct.pack("<HHII", _EXIF_IFD, 4, 1, 26) + struct.pack("<I", 0)
    exif_ifd = struct.pack("<H", 1) + struct.pack("<HHII", _USER_COMMENT, 7, len(comment), 44) + struct.pack("<I", 0)
    return b"II*\0" + struct.pack("<I", 8) + ifd0 + exif_ifd + comment


def _raw_profile(data):
    """ImageMagick's text form of a profile: a newline, "exif", its length in bytes, then hex, 72 digits a line."""
    h = data.hex()
    return b"\nexif\n%8d\n" % len(data) + "\n".join(h[i:i + 72] for i in range(0, len(h), 72)).encode() + b"\n"


def embed_png(png, data):
    """``png`` (bytes) with ``data`` added right after IHDR: tEXt, eXIf and the raw EXIF profile."""
    if not png.startswith(PNG_SIG):
        raise ValueError("not a PNG")
    text = _text(data)
    ihdr_end = 8 + 12 + struct.unpack(">I", png[8:12])[0]
    exif = _exif(text)
    extra = (_chunk(b"tEXt", KEYWORD + b"\0" + text.encode("latin-1")) + _chunk(b"eXIf", exif)
             + _chunk(b"zTXt", RAW_EXIF + b"\0\0" + zlib.compress(_raw_profile(b"Exif\0\0" + exif))))
    return png[:ihdr_end] + extra + png[ihdr_end:]


def _from_tiff(t):
    """Texts in an EXIF/TIFF block that might hold the JSON: UserComment, ImageDescription."""
    if len(t) < 8 or t[:2] not in (b"II", b"MM"):
        return []
    e = "<" if t[:2] == b"II" else ">"
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}
    found = []

    def ifd(off, depth=0):
        if depth > 2 or off + 2 > len(t):
            return
        (n,) = struct.unpack(e + "H", t[off:off + 2])
        for i in range(min(n, 500)):
            p = off + 2 + 12 * i
            if p + 12 > len(t):
                return
            tag, kind, count = struct.unpack(e + "HHI", t[p:p + 8])
            size = sizes.get(kind, 1) * count
            val = t[p + 8:p + 8 + size] if size <= 4 else t[struct.unpack(e + "I", t[p + 8:p + 12])[0]:][:size]
            if tag == _EXIF_IFD:
                ifd(struct.unpack(e + "I", t[p + 8:p + 12])[0], depth + 1)
            elif tag == _USER_COMMENT and len(val) >= 8:
                head, body = val[:8], val[8:]
                found.append(body.decode("utf-16-le" if e == "<" else "utf-16-be", "replace")
                             if head.startswith(b"UNICODE") else body.decode("latin-1"))
            elif tag == _IMAGE_DESCRIPTION:
                found.append(val.rstrip(b"\0").decode("latin-1"))

    ifd(struct.unpack(e + "I", t[4:8])[0])
    return found


def _png_texts(b):
    out, p = [], 8
    while p + 12 <= len(b):
        n, kind = struct.unpack(">I", b[p:p + 4])[0], b[p + 4:p + 8]
        body = b[p + 8:p + 8 + n]
        if kind == b"tEXt" and body.startswith(KEYWORD + b"\0"):
            out.append(body[len(KEYWORD) + 1:].decode("latin-1"))
        elif kind == b"zTXt" and body.startswith(KEYWORD + b"\0"):
            out.append(zlib.decompress(body[len(KEYWORD) + 2:]).decode("latin-1"))
        elif kind == b"iTXt" and body.startswith(KEYWORD + b"\0"):
            flag, rest = body[len(KEYWORD) + 1], body[len(KEYWORD) + 3:]
            rest = rest.split(b"\0", 2)[2]                       # language tag, translated keyword
            out.append((zlib.decompress(rest) if flag else rest).decode("utf-8"))
        elif kind == b"eXIf":
            out += _from_tiff(body)
        elif kind == b"zTXt" and body.startswith(RAW_EXIF + b"\0"):
            lines = zlib.decompress(body[len(RAW_EXIF) + 2:]).split(b"\n")
            raw = bytes.fromhex(b"".join(lines[3:]).decode())
            out += _from_tiff(raw[6:] if raw.startswith(b"Exif\0\0") else raw)
        elif kind == b"IEND":
            break
        p += 12 + n
    return out


def _jpeg_texts(b):
    out, p = [], 2
    while p + 4 <= len(b) and b[p] == 0xFF:
        marker = b[p + 1]
        if marker in (0xD9, 0xDA):                               # end of image / start of scan: no more metadata
            break
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            p += 2
            continue
        n = struct.unpack(">H", b[p + 2:p + 4])[0]
        body = b[p + 4:p + 2 + n]
        if marker == 0xE1 and body.startswith(b"Exif\0\0"):
            out += _from_tiff(body[6:])
        elif marker == 0xFE:
            out.append(body.decode("latin-1"))
        p += 2 + n
    return out


def _parse(text):
    """The first JSON object in ``text`` that is ours (settings or a look), else None."""
    text = text.strip()
    for start in [0] + [i for i in range(len(text)) if text.startswith('{"vathopy_', i)][:5]:
        if text[start:start + 1] != "{":
            continue
        try:
            d, _ = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            continue
        if isinstance(d, dict) and ("vathopy_settings" in d or "vathopy_look" in d):
            return d
    return None


def read(data):
    """Our settings from a file's bytes (PNG, JPEG or JSON), with "source" saying where they were found:
    the embedded/exported dict (has "vathopy_settings"), or else a look dict (a JSON file without
    "vathopy_settings" is taken as a look, for the caller to check). ValueError if there's none."""
    if data.startswith(PNG_SIG):
        candidates, kind = _png_texts(data), "PNG"
    elif data.startswith(b"\xff\xd8"):
        candidates, kind = _jpeg_texts(data), "JPEG"
    else:
        try:
            d = json.loads(data)
        except ValueError:
            raise ValueError("not a PNG, JPEG or JSON file") from None
        if not isinstance(d, dict):
            raise ValueError("not vathopy settings or a look (JSON)")
        return {**d, "source": "JSON"}
    for text in candidates:
        d = _parse(text)
        if d is not None:
            return {**d, "source": kind}
    i = data.find(b'{"vathopy_settings"')                     # last resort: the JSON anywhere in the file (XMP, ...)
    if i >= 0 and (d := _parse(data[i:i + 1_000_000].decode("latin-1"))) is not None:
        return {**d, "source": kind}
    raise ValueError(f"no vathopy settings found in this {kind}")


def look_of(d):
    """The vathopy look in what :func:`read` returned (or a look dict itself)."""
    if "vathopy_settings" not in d:
        return {k: v for k, v in d.items() if k != "source"}
    if not isinstance(d.get("look"), dict):
        raise ValueError("these settings carry no look")
    return d["look"]
