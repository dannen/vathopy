# SPDX-License-Identifier: GPL-3.0-or-later
"""Dump a relief tuned with the Vathography Free add-on to a vathopy look JSON.

Run inside Blender, with the add-on enabled:

    blender -b my_relief.blend --python extract_look.py -- my_look.json [relief-object-name]

Every value is read through the add-on's own functions, so the look renders in vathopy exactly as
the add-on's artistic export does (same depth map, same picture).
"""

import json
import sys

import bpy


def addon():
    for name, mod in list(sys.modules.items()):
        if name.endswith("vathography_free") and hasattr(mod, "artistic"):
            return mod
    for name in ("bl_ext.user_default.vathography_free",):
        try:
            import addon_utils
            addon_utils.enable(name, default_set=False)
            return sys.modules[name]
        except Exception:
            pass
    raise RuntimeError("the Vathography Free add-on is not enabled in this Blender")


def reliefs(V):
    return [o for o in bpy.data.objects if V.common.plane_settings(o) is not None]


def _vec(v):
    return [float(c) for c in v]


def extract(plane, V=None):
    V = V or addon()
    C, SH, BD, SC, AR, PAR = V.common, V.shading, V.bands, V.sumchain, V.artistic, V.par
    s = C.plane_settings(plane)
    if s is None:
        raise ValueError(f"{plane.name!r} is not a Vathography relief")
    SH.push_display_values(plane)

    prep = AR.artistic_prepare(plane)  # the add-on's own window resolution (held / fitted / auto)
    lo, hi = float(prep["lo"]), float(prep["hi"])

    controls = [{"center": float(r.center), "offset": float(r.offset), "feather": float(r.feather),
                 "from_min": None if r.from_min is None else float(r.from_min),
                 "from_max": None if r.from_max is None else float(r.from_max)} for r in C.control_state(plane)]

    tilt = None
    if getattr(s, "tilt_enabled", False):
        t = C.tilt_empty(plane)
        if t is not None:
            from mathutils import Vector
            rel = plane.matrix_world.inverted() @ t.matrix_world
            n = rel.to_quaternion() @ Vector((0.0, 1.0, 0.0))
            if n.length >= 1e-12:
                n.normalize()
                tilt = {"normal": _vec(n), "x0": float(rel.translation.x), "y0": float(rel.translation.y),
                        "fit": bool(getattr(s, "tilt_fit", False))}

    ng = bpy.data.node_groups.get(C.SHADER_NG_NAME)
    rec = SH.capture_ramp(ng)
    ramp = {"interpolation": rec["interpolation"], "color_mode": rec["color_mode"],
            "hue_interpolation": rec["hue_interpolation"],
            "stops": [[float(p), [float(c) for c in col]] for p, col in rec["elements"]]}

    bands = []
    for b in BD.band_units(plane):
        sl = C.band_slice(plane, cross=b)
        bands.append({"id": BD.band_id(b), "name": b.name, "enabled": bool(b.vfree_band_enabled),
                      "strength": float(b.vfree_band_strength), "interpolation": b.vfree_band_interp,
                      "normal": [sl.nx, sl.ny, sl.nz], "center": [sl.cx, sl.cy, sl.cz],
                      "width": float(b.vfree_band_width_m), "softness": float(b.vfree_band_softness_m)})

    areas = []
    inv = plane.matrix_world.inverted()
    for u in SH.light_units(plane):
        lid = SC.light_id(u) or u.name
        areas.append({
            "id": lid, "name": u.name, "enabled": bool(u.vfree_light_enabled), "mode": SC.mode(u),
            "shape": "BOX" if SH.light_is_box(u) else "ELLIPSOID",
            "position": _vec(inv @ u.matrix_world.translation),
            "radius": [abs(float(c)) for c in u.scale],
            "basis": [list(r) for r in SH.light_basis(plane, u)],
            "reach_plus": _vec(u.vfree_reach_plus), "reach_minus": _vec(u.vfree_reach_minus),
            "strength": float(u.vfree_light_strength), "falloff": float(u.vfree_light_falloff),
            "edge": float(getattr(u, "vfree_light_edge", C.DEFAULT_LIGHT_EDGE)),
            "group": u.vfree_merge_group,
            "mask": {"strength": float(u.vfree_mask_strength), "side": u.vfree_mask_side,
                     "edge": float(u.vfree_mask_edge), "target": u.vfree_mask_target or SC.ALL_BANDS},
        })

    groups = {g.name: {"strength": float(g.strength), "blend": float(getattr(g, "blend", 0.0))}
              for g in s.merge_groups}

    img = bpy.data.images.get(s.depth_image)
    return {
        "vathopy_look": 1,
        "source": {"blend": bpy.data.filepath, "relief": plane.name, "addon_version": C.VERSION,
                   "depth_map": bpy.path.abspath(img.filepath_raw) if img is not None else ""},
        "plane": {"width": float(s.plane_width), "height": float(s.plane_height),
                  "scale": float(C.effective_scale(plane)), "midlevel": float(C.effective_midlevel(plane))},
        "flip": bool(s.flip),
        "controls": controls,
        "window": [lo, hi],
        "tilt": tilt,
        "ramp": ramp,
        "ramp_near_left": bool(SH.ramp_near_left(ng)),
        "base": getattr(s, "band_base", "RAMP"),
        "bands": bands,
        "areas": areas,
        "groups": groups,
        "solo": s.light_solo or "",
    }


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        sys.exit("usage: blender -b file.blend --python extract_look.py -- out.json [relief-name]")
    V = addon()
    planes = reliefs(V)
    if len(argv) > 1:
        planes = [p for p in planes if p.name == argv[1]]
    if not planes:
        sys.exit("no Vathography relief found" + (f" named {argv[1]!r}" if len(argv) > 1 else ""))
    if len(planes) > 1:
        sys.exit("several reliefs: " + ", ".join(p.name for p in planes) + " -- name one after the output path")
    look = extract(planes[0], V)
    with open(argv[0], "w", encoding="utf-8") as fh:
        json.dump(look, fh, indent=2)
    print(f"wrote {argv[0]} from relief {planes[0].name!r}: {len(look['controls'])} control(s), "
          f"{len(look['bands'])} band(s), {len(look['areas'])} area(s)")


if __name__ == "__main__":
    main()
