"""Parity test: random reliefs built with the real Vathography Free add-on (in headless Blender) are
exported with the add-on's own numpy export, extracted to a look with tools/extract_look.py, and
rendered with vathopy. The two pictures must agree.

    <python with bpy> tests/bpy_parity.py [n_scenes] [add-on zip]
"""
import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

import numpy as np
import bpy
from mathutils import Matrix

import vathopy
from vathopy import io as VIO
import extract_look

N = int(sys.argv[1]) if len(sys.argv) > 1 else 60
ZIP = sys.argv[2] if len(sys.argv) > 2 else None
TOL = 1e-5


def enable_addon():
    try:
        return extract_look.addon()
    except RuntimeError:
        if not ZIP:
            raise SystemExit("add-on not installed: pass the add-on zip as the 2nd argument")
        bpy.ops.extensions.package_install_files(filepath=ZIP, repo="user_default", enable_on_install=True)
        return extract_look.addon()


V = enable_addon()
C, SH, BD, SC, AR, PAR, RP = V.common, V.shading, V.bands, V.sumchain, V.artistic, V.par, V.ramp_presets
rng = np.random.default_rng(7)
crng = np.random.default_rng(11)          # colours (add-on 1.0.96): their own stream, so older scenes stay the same
work = tempfile.mkdtemp(prefix="vathopy_parity_")


def synth_depth(w, h, seed):
    r = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    u, v = xx / w, yy / h
    d = 0.15 + 0.5 * v + 0.1 * np.sin(6 * u + r.random() * 3)          # receding floor
    for _ in range(4):                                                  # a few "subjects"
        cx, cy, rad = r.random(), r.random(), 0.05 + 0.15 * r.random()
        m = ((u - cx) ** 2 + ((v - cy) * h / w) ** 2) < rad ** 2
        d[m] = np.minimum(d[m], 0.05 + 0.6 * r.random())
    near_bright = 1.0 - np.clip(d, 0.0, 1.0)
    return near_bright.astype(np.float32)  # top-down


def make_inputs():
    files = []
    for k, (w, h, ext) in enumerate([(320, 240, ".exr"), (257, 193, ".png"), (200, 300, ".exr"), (160, 160, ".png"), (180, 120, ".png8")]):
        top = synth_depth(w, h, k)
        if ext == ".png8":
            import cv2
            p = os.path.join(work, f"depth{k}_8bit.png")
            cv2.imwrite(p, np.round(top * 255).astype(np.uint8))
        else:
            p = os.path.join(work, f"depth{k}{ext}")
            VIO.write_depth(np.ascontiguousarray(top[::-1]), p)
        files.append(p)
    return files


def rand_ramp(node):
    cr = node.color_ramp
    if rng.random() < 0.5:
        keys = [e["key"] for e in RP.menu_items()]
        rec = RP.menu_record(keys[int(rng.integers(len(keys)))])
    else:
        n = int(rng.integers(1, 6))
        pos = np.sort(rng.random(n))
        rec = {"interpolation": str(rng.choice(["CONSTANT", "LINEAR", "EASE", "B_SPLINE", "CARDINAL"])),
               "color_mode": str(rng.choice(["RGB", "RGB", "HSV", "HSL"])),
               "hue_interpolation": str(rng.choice(["NEAR", "FAR", "CW", "CCW"])),
               "elements": [(float(p), tuple(rng.random(3)) + (1.0,)) for p in pos]}
    SH.restore_ramp(node, rec)


def rand_unit_rot():
    return tuple(float(a) for a in rng.uniform(-math.pi, math.pi, 3))


def build_scene(depth_path, i):
    bpy.ops.wm.read_homefile(use_empty=True)
    ctx = bpy.context
    plane = V.ops_setup.setup_from_files(ctx, depth_path, float(rng.choice([1.5, 1.0, 2.3])))
    s = plane.vfree
    ng = bpy.data.node_groups.get(C.SHADER_NG_NAME)
    node = ng.nodes.get(SH.RAMP_NODE_NAME)
    rand_ramp(node)
    ng[SH.RAMP_DIR_KEY] = int(rng.random() < 0.5)
    if rng.random() < 0.25:
        s.flip = True
    if rng.random() < 0.4:
        s.scale = float(rng.uniform(0.3, 3.0))
    if rng.random() < 0.3:
        s.midlevel = float(rng.uniform(0.0, 1.0))
    d_emp = C.depth_empty(plane)
    if d_emp is not None and rng.random() < 0.5:
        d_emp.scale.z = float(rng.uniform(0.5, 8.0))

    # depth controls (empties in the controls collection; the free UI has no button for them)
    coll = C.controls_collection(plane)
    for k in range(int(rng.choice([0, 0, 1, 2, 3]))):
        o = bpy.data.objects.new(f"ctl{k}", None)
        coll.objects.link(o)
        o.parent = plane
        o.matrix_parent_inverse = Matrix.Identity(4)
        o.location = (float(rng.uniform(-0.5, 0.5)) * s.plane_width, float(rng.uniform(-0.5, 0.5)) * s.plane_height,
                      float(rng.uniform(-0.6, 0.6)))
        C.set_control_feather(o, float(rng.uniform(0.005, 0.15)))
        if rng.random() < 0.3:
            C.set_control_bounds(o, mn=float(rng.uniform(0, 0.4)) if rng.random() < 0.5 else None,
                                 mx=float(rng.uniform(0.6, 1)) if rng.random() < 0.5 else None)

    # ramp tilt (an empty the free UI does not create either)
    if rng.random() < 0.3:
        t = bpy.data.objects.new("tilt", None)
        ctx.scene.collection.objects.link(t)
        t.parent = plane
        t.matrix_parent_inverse = Matrix.Identity(4)
        t[C.TILT_KEY] = plane.name
        t.location = (float(rng.uniform(-0.3, 0.3)), float(rng.uniform(-0.3, 0.3)), 0.0)
        t.rotation_euler = tuple(float(a) for a in rng.uniform(-0.6, 0.6, 3))
        s.tilt_empty = t.name
        s.tilt_enabled = True
        s.tilt_fit = bool(rng.random() < 0.5)

    for k in range(int(rng.choice([0, 1, 2, 3]))):
        b = BD.create_band(plane)
        b.location = (float(rng.uniform(-0.5, 0.5)), float(rng.uniform(-0.6, 0.6)), float(rng.uniform(-0.3, 0.3)))
        b.rotation_euler = (float(rng.uniform(-1.2, 1.2)), 0.0, float(rng.uniform(-1.2, 1.2)))
        b.vfree_band_width_m = float(rng.uniform(0.0, 0.3))
        b.vfree_band_softness_m = float(rng.uniform(0.001, 0.2))
        b.vfree_band_strength = float(rng.uniform(-1.5, 1.5))
        b.vfree_band_interp = str(rng.choice(BD.INTERPS))
        b.vfree_band_enabled = bool(rng.random() < 0.85)

    units = []
    for k in range(int(rng.choice([0, 1, 2, 3, 4, 5]))):
        with ctx.temp_override(object=plane, active_object=plane):
            bpy.ops.vfree.add_light()
        u = SH.light_units(plane)[-1]
        units.append(u)
        u.location = (float(rng.uniform(-0.5, 0.5)) * s.plane_width, float(rng.uniform(-0.5, 0.5)) * s.plane_height,
                      float(rng.uniform(-0.8, 0.8)))
        u.scale = tuple(float(v) * float(rng.choice([1, 1, -1])) for v in rng.uniform(0.05, 0.6, 3))
        if rng.random() < 0.5:
            u.rotation_euler = rand_unit_rot()
        u.vfree_light_shape = str(rng.choice(["ELLIPSOID", "BOX"]))
        u.vfree_light_strength = float(rng.uniform(-1.5, 1.5))
        u.vfree_light_falloff = float(rng.uniform(0.05, 3.0))
        u.vfree_light_edge = float(rng.uniform(0.0, 1.0))
        u.vfree_reach_plus = tuple(float(v) for v in rng.uniform(0.2, 2.0, 3))
        u.vfree_reach_minus = tuple(float(v) for v in rng.uniform(0.2, 2.0, 3))
        u.vfree_light_mode = str(rng.choice(["ADD", "ADD", "MERGE", "MERGE", "MASK"]))
        u.vfree_merge_group = str(rng.choice(["Group 1", "Group 2"]))
        u.vfree_light_enabled = bool(rng.random() < 0.9)
        u.vfree_mask_strength = float(rng.uniform(0, 1))
        u.vfree_mask_side = str(rng.choice(["HIDE", "KEEP"]))
        u.vfree_mask_edge = float(rng.uniform(0, 1))
    SC.sync_groups(plane)
    for g in s.merge_groups:
        g.strength = float(rng.uniform(-1.5, 1.5))
        g.blend = float(rng.choice([0.0, rng.uniform(0.05, 1.0)]))
    for u in units:
        if u.vfree_light_mode == "MASK":
            items = [it[0] for it in SC._target_list(u)]
            u.vfree_mask_target = str(rng.choice(items))
    if hasattr(C, "COLOR_BLENDS"):
        for ob, pre in [(b, "vfree_band") for b in BD.band_units(plane)] + [(u, "vfree_light") for u in units]:
            if crng.random() < 0.5:
                setattr(ob, pre + "_use_color", True)
                col = crng.random(3)
                if crng.random() < 0.15:
                    col = np.full(3, crng.choice([0.0, 0.5, 1.0]))          # grey: COLOR does nothing
                setattr(ob, pre + "_color", tuple(float(c) for c in col))
                setattr(ob, pre + "_color_blend", str(crng.choice(C.COLOR_BLENDS)))
    if rng.random() < 0.4:
        s.band_base = str(rng.choice(["BLACK", "WHITE"]))
    if rng.random() < 0.1 and units:
        s.light_solo = units[0].name
    if rng.random() < 0.2:
        SH.fit_ramp(plane)                       # a held ramp range
    SH.push_display_values(plane)
    return plane


def main():
    files = make_inputs()
    worst = 0.0
    fails = []
    feats = {"bands": 0, "areas": 0, "controls": 0, "tilt": 0, "held": 0, "near_left": 0, "colours": 0}
    for i in range(N):
        path = files[i % len(files)]
        plane = build_scene(path, i)
        out, info = PAR.drain(AR.artistic_steps(plane, verbose=False))
        out = np.asarray(out, dtype=np.float32)
        look = extract_look.extract(plane, V)
        img = bpy.data.images.get(plane.vfree.depth_image)
        bl_depth = C.image_pixels(img)
        my_depth, _ = vathopy.load_depth(path)
        io_err = float(np.abs(bl_depth - my_depth).max())
        mine, _ = vathopy.render(my_depth, look)
        err = float(np.abs(mine - out).max())
        worst = max(worst, err)
        feats["bands"] += len(look["bands"]) > 0
        feats["areas"] += len(look["areas"]) > 0
        feats["controls"] += len(look["controls"]) > 0
        feats["tilt"] += look["tilt"] is not None
        feats["held"] += C.ramp_held_range(plane) is not None
        feats["near_left"] += look["ramp_near_left"]
        cols = [x for x in look["bands"] + look["areas"] + list(look["groups"].values()) if x.get("color")]
        feats["colours"] += len(cols) > 0
        status = "OK " if err <= TOL and io_err == 0.0 else "BAD"
        if status == "BAD":
            fails.append(i)
            import json
            with open(os.path.join(work, f"fail{i}.json"), "w") as fh:
                json.dump(look, fh, indent=1)
        print(f"{status} scene {i:3d} {os.path.basename(path):16s} max |diff| {err:.2e}  depth read {io_err:.1e}  "
              f"bands {len(look['bands'])} areas {len(look['areas'])} controls {len(look['controls'])} "
              f"tilt {'y' if look['tilt'] else 'n'} ramp {look['ramp']['interpolation']}/{look['ramp']['color_mode']}"
              + (f" colours {','.join(x['color_blend'] for x in cols)}" if cols else ""))
    print(f"\n{N} scenes, worst max |diff| {worst:.2e}, failing: {fails or 'none'}; scenes with: {feats}")
    print("work dir:", work)
    sys.exit(1 if fails else 0)


main()
