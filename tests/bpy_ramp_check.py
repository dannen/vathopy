"""Compare vathopy.ramp.Ramp.evaluate with Blender's ColorRamp.evaluate on random ramps.
Run with the Python that has the `bpy` module."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import numpy as np
import bpy
from vathopy.ramp import Ramp, INTERPOLATIONS, HUE_MODES, PRESETS, preset

rng = np.random.default_rng(1)
ng = bpy.data.node_groups.new("t", "ShaderNodeTree")
node = ng.nodes.new("ShaderNodeValToRGB")
cr = node.color_ramp
xs = np.concatenate([np.linspace(-0.1, 1.1, 2401), rng.random(500)]).astype(np.float32)


def set_ramp(r):
    cr.color_mode = r.color_mode
    cr.hue_interpolation = r.hue_interpolation
    cr.interpolation = r.interpolation
    while len(cr.elements) > 1:
        cr.elements.remove(cr.elements[-1])
    cr.elements[0].position, cr.elements[0].color = r.stops[0][0], r.stops[0][1]
    for p, c in r.stops[1:]:
        e = cr.elements.new(p)
        e.color = c


def blender_eval(xs):
    return np.array([cr.evaluate(float(x)) for x in xs], dtype=np.float32)


worst = {}
cases = []
for mode in ("RGB", "HSV", "HSL"):
    for ipo in INTERPOLATIONS:
        for hue in (HUE_MODES if mode != "RGB" else ("NEAR",)):
            for trial in range(12):
                n = int(rng.integers(1, 7))
                pos = np.sort(rng.random(n)).astype(np.float32)
                if trial % 3 == 0 and n >= 2:
                    pos[1] = pos[0]  # duplicate positions
                if trial % 4 == 0:
                    pos[0] = 0.0
                    pos[-1] = 1.0
                stops = [(float(p), tuple(rng.random(4).astype(float))) for p in pos]
                cases.append(Ramp(stops, ipo, mode, hue))
for k in PRESETS:
    cases.append(preset(k))
    cases.append(preset(k).mirrored())

for r in cases:
    set_ramp(r)
    # Blender may reorder equal positions; read back what it holds so both sides use the same stops
    r = Ramp([(e.position, tuple(e.color)) for e in cr.elements], r.interpolation, r.color_mode, r.hue_interpolation)
    a = r.evaluate(xs)
    b = blender_eval(xs)
    err = float(np.abs(a - b).max())
    key = (r.color_mode, r.interpolation, r.hue_interpolation)
    if err > worst.get(key, (-1,))[0]:
        worst[key] = (err, r.to_dict())

bad = 0
for k, (e, d) in sorted(worst.items()):
    flag = "OK " if e < 1e-5 else "BAD"
    bad += e >= 1e-5
    print(f"{flag} {k}: max abs diff {e:.2e}")
    if e >= 1e-5:
        print("    ", d)
print("cases:", len(cases), "failing groups:", bad)
sys.exit(1 if bad else 0)
