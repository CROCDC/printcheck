#!/usr/bin/env python3
"""Synthetic shapes with a known outcome, run through every stage.

    <project>/.venv/bin/python printcheck/tests/test_shapes.py     # plain run
    <project>/.venv/bin/python -m pytest printcheck/tests          # when pytest is installed

Each shape says which codes it must produce and at which level. They are
the ad-hoc cases of 2026-10-15 (column, leaning beam, plate on legs, strip,
box, eight sticks) made permanent (research plan, A4), plus the section
frame bug and the moments of `gyration` against hand-computed values.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
import trimesh
from shapely.geometry import box as sbox

from printcheck import check_print
from printcheck.preflight import gyration, section


def box(sx, sy, sz, x=0.0, y=0.0, z=0.0):
    b = trimesh.creation.box((sx, sy, sz))
    b.apply_translation((x, y, z + sz / 2))
    return b


def union(meshes):
    try:
        out = meshes[0]
        for m in meshes[1:]:
            out = out.union(m, engine="manifold")
        return out
    except Exception:          # no manifold3d in this venv: bodies stay separate, the sim still judges them
        return trimesh.util.concatenate(meshes)


def run(mesh, settings=None):
    rep = check_print([{"name": "shape", "mesh": mesh, "extruder": 1}], settings or {"filament_type": ["PLA"]})
    return {(i["stage"].split(":")[0], i["code"]): i["level"] for i in rep.items}, rep


def levels(found, stage, code):
    return found.get((stage, code))


def test_box_is_go():
    found, rep = run(box(40, 40, 10))
    assert not rep.failed, [i for i in rep.items if i["level"] == "FAIL"]
    assert levels(found, "sim", "overturn") == "OK"
    assert levels(found, "sim", "sway") == "OK"
    assert levels(found, "members", "slender") == "OK"


def test_column_6x6x60():
    found, rep = run(box(6, 6, 60))
    assert levels(found, "adhesion", "aspect") == "FAIL"
    assert levels(found, "sim", "overturn") in ("WARN", "FAIL")   # margin ~1.2 with peel 0.1 MPa
    assert levels(found, "sim", "sway") == "OK"                    # f1 ~55 Hz, sway 0.03 mm


def test_post_3x3x100_fails_everywhere():
    found, rep = run(box(3, 3, 100))
    assert levels(found, "adhesion", "aspect") == "FAIL"
    assert levels(found, "members", "slender") == "FAIL"
    assert levels(found, "sim", "overturn") == "FAIL"
    assert levels(found, "sim", "sway") == "FAIL"


def test_post_10x10x100_does_not_sway():
    found, rep = run(box(10, 10, 100))
    assert levels(found, "sim", "sway") == "OK"                    # 0.02 mm
    assert levels(found, "sim", "overturn") != "FAIL"
    assert levels(found, "members", "slender") == "OK"             # slenderness 35


def test_eight_sticks_sway():
    """The cajonera_lijas frame reduced to its mechanism: a 5 mm base, eight
    2 x 10 mm sticks free for 80 mm, a cap. Came off the bed at 90 %."""
    base = box(56, 73, 5)
    sticks = [box(2, 10, 80, x, y, 4.9) for x in (-25, -8, 8, 25) for y in (-25, 25)]
    cap = box(56, 73, 3, 0, 0, 84.9)
    found, rep = run(union([base, *sticks, cap]))
    assert levels(found, "sim", "sway") == "FAIL", [i["msg"] for i in rep.items if i["stage"] == "sim"]
    if len(rep.items) and any(i["code"] == "slender" and i["level"] == "FAIL" for i in rep.items):
        pass  # one body (manifold union): check_members reads it too, as on the real frame (94)


def test_leaning_beam_tips():
    beam = box(10, 10, 80)
    beam.apply_transform(trimesh.transformations.rotation_matrix(np.radians(40), [1, 0, 0]))
    beam.apply_translation([0, 0, -beam.bounds[0][2]])
    found, rep = run(beam)
    assert levels(found, "adhesion", "com") == "FAIL"
    assert levels(found, "sim", "overturn") == "FAIL"


def test_plate_on_legs_pillar():
    legs = [box(3, 3, 30, x, y) for x in (-15, 15) for y in (-15, 15)]
    plate = box(40, 40, 3, 0, 0, 29.9)
    found, rep = run(union([*legs, plate]))
    assert levels(found, "adhesion", "pillar") == "WARN" or levels(found, "sim", "sway") in ("WARN", "FAIL")


def test_single_bead_strip():
    found, rep = run(box(0.4, 30, 3))
    assert levels(found, "adhesion", "strip") == "WARN" or levels(found, "mesh", "dropped_features") in ("WARN", "FAIL")


def test_sections_share_the_mesh_frame():
    """Two layers of one mesh (and two meshes) must come out in the same x, y:
    trimesh's to_2D() picked a frame per section and put a plate's letters
    4.8 mm off its body (2026-10-15)."""
    b = box(10, 20, 10, 30, 40)
    s1, s2 = section(b, 1.0), section(b, 9.0)
    assert abs(s1.centroid.x - 30) < 1e-6 and abs(s1.centroid.y - 40) < 1e-6
    assert s1.symmetric_difference(s2).area < 1e-6


def test_separate_objects_judged_apart():
    # Two mushrooms on one plate, each growing 224 mm2 at the same layer:
    # one object each is under the 300 limit; summed, they read 448.
    def mushroom(x):
        return [box(10, 10, 10, x), box(18, 18, 3, x, 0, 10)]
    parts = [{"name": f"m{i}", "mesh": union(mushroom(x)), "extruder": 1} for i, x in enumerate((-40, 40))]
    rep = check_print(parts, {"filament_type": ["PLA"]})
    over = [i for i in rep.items if i["code"] == "overhang"]
    assert len(over) == 2 and all(i["level"] == "OK" for i in over), over


def test_gyration_against_hand_values():
    A, r, r_y = gyration(sbox(-5, -10, 5, 10))             # 10 along x, 20 along y
    assert abs(A - 200) < 1e-6
    assert abs(r - 10 / math.sqrt(12)) < 1e-6              # least: about the y axis, width 10
    assert abs(r_y - 20 / math.sqrt(12)) < 1e-6            # bending along y: depth 20
    ring = sbox(-10, -10, 10, 10).difference(sbox(-8, -8, 8, 8))
    A, r, _ = gyration(ring)
    I = (20 ** 4 - 16 ** 4) / 12
    assert abs(A - 144) < 1e-6 and abs(r - math.sqrt(I / 144)) < 1e-6
    L = sbox(0, 0, 10, 2).union(sbox(0, 0, 2, 10))           # an L of 2 mm legs
    A, r, _ = gyration(L)
    assert abs(A - 36) < 1e-6 and 0 < r < 10 / math.sqrt(12)


if __name__ == "__main__":
    import inspect
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and inspect.isfunction(fn):
            try:
                fn()
                print(f"ok    {name}")
            except AssertionError as exc:
                fails += 1
                print(f"FAIL  {name}: {exc}")
            except Exception as exc:
                fails += 1
                print(f"ERROR {name}: {exc!r}")
    sys.exit(1 if fails else 0)
