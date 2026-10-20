"""The geometry stages one rule at a time: mesh, adhesion, members, inlay,
and how parts are grouped. Each test builds the smallest shape that makes
one finding fire, and asserts its code and level."""

import numpy as np
import pytest
import trimesh
from shapely.geometry import MultiPolygon, Polygon, box as sbox

import printcheck
from printcheck import preflight as P
from printcheck.preflight import (check_adhesion, check_inlay, check_members, check_mesh, geoms,
                                  groups, gyration, layer_profile, run_parts, section, thinnest)

from conftest import box, found, part


def union(*meshes):
    out = meshes[0]
    for m in meshes[1:]:
        out = out.union(m, engine="manifold")
    return out


def mushroom():
    """A 6 x 6 stem under a 30 x 30 cap: little on the bed, a roof in the air."""
    return union(box(6, 6, 10), box(30, 30, 3, z=10))


# ----------------------------------------------------------------- helpers

def test_geoms_and_section():
    assert geoms(None) == []
    assert geoms(Polygon()) == []
    two = MultiPolygon([sbox(0, 0, 1, 1), sbox(2, 0, 3, 1)])
    assert len(geoms(two)) == 2
    b = box(10, 20, 5, 30, 40)
    assert section(b, 50) is None                      # the plane misses the mesh
    on_vertex = section(b, 5.0)                        # top face: nudged off the vertices
    assert on_vertex is None or on_vertex.area < 1e-6
    assert abs(section(b, 0.0).area - 200) < 1e-6      # bottom face, nudged inside


def test_thinnest_and_layer_profile(rules):
    assert abs(thinnest(sbox(0, 0, 2, 20)) - 2.0) < 0.01
    # Two blocks with an empty gap between them: the gap layers have no islands.
    gap = trimesh.util.concatenate([box(10, 10, 1), box(10, 10, 1, z=2)])
    rows = layer_profile(gap, rules)
    empty = [r for r in rows if r["islands"] == 0]
    assert empty and all(r["area"] == 0.0 and "poly" not in r for r in empty)


def test_material_of():
    assert printcheck.material_of("Bambu PLA Basic @BBL A2L") == "PLA"
    assert printcheck.material_of("Generic PETG HF") == "PETG"
    assert printcheck.material_of(None) == "PETG"
    assert printcheck.material_of("mystery", default="TPU") == "TPU"


# ----------------------------------------------------------------- mesh

def without_top_triangle(b):
    top = int(np.argmax(b.face_normals[:, 2]))
    return trimesh.Trimesh(b.vertices, np.delete(b.faces, top, axis=0), process=False)


def test_mesh_open_side_wall_does_not_crash(rules, rep):
    # A cut side wall leaves no closed outline at any layer; this used to
    # raise from min() and take the rest of the verdict with it.
    b = box(10, 10, 4)
    side = int(np.argmax(b.face_normals[:, 0]))
    rows = check_mesh("open", trimesh.Trimesh(b.vertices, np.delete(b.faces, side, axis=0), process=False),
                      rules, rep)
    assert found(rep)[("mesh", "watertight")] == "FAIL"
    assert found(rep)[("mesh", "no_section")] == "FAIL"
    assert rows and all(r["islands"] == 0 for r in rows)


def test_mesh_integrity_failures(rules, rep):
    b = box(10, 10, 4)
    check_mesh("open", without_top_triangle(b), rules, rep)
    flipped = b.faces.copy()
    flipped[0] = flipped[0][::-1]
    check_mesh("flipped", trimesh.Trimesh(b.vertices, flipped, process=False), rules, rep)
    inside_out = b.copy()
    inside_out.invert()
    check_mesh("inside_out", inside_out, rules, rep)
    codes = {(i["stage"], i["code"]): i["level"] for i in rep.items}
    assert codes[("mesh:open", "watertight")] == "FAIL"
    assert codes[("mesh:flipped", "winding")] == "FAIL"
    assert codes[("mesh:inside_out", "volume")] == "FAIL"


def test_mesh_off_bed_partial_layers_thin_part(rules, rep):
    check_mesh("floating", box(20, 20, 10.1, z=1), rules, rep)
    assert found(rep)[("mesh", "bed")] == "WARN"
    # 50.5 layers: the top one is partial (the INFO "layers" line follows it)
    assert any(i["code"] == "layers" and i["level"] == "WARN" for i in rep.items)
    rep2 = P.Report()
    check_mesh("film", box(20, 20, 0.4), rules, rep2)
    assert found(rep2)[("mesh", "thin_part")] == "FAIL"


def test_mesh_without_layers(rules, rep):
    rows = check_mesh("sheet", box(20, 20, 0.05), rules, rep)
    assert rows == [] and found(rep)[("mesh", "empty")] == "FAIL"


def test_mesh_contact_and_overhang(rules, rep):
    check_mesh("mushroom", mushroom(), rules, rep)
    f = found(rep)
    assert f[("mesh", "contact")] == "WARN"            # 36 mm2 under a 900 mm2 cap
    assert f[("mesh", "overhang")] == "FAIL"


def test_mesh_dropped_single_bead_tiny(rules, rep):
    fin = box(0.3, 10, 2, x=15)                        # thinner than min_bead: dropped, one bead
    pin = box(0.6, 0.6, 2, x=-15)                      # wide enough to print, under 1 mm2
    mesh = trimesh.util.concatenate([box(10, 10, 2), fin, pin])
    check_mesh("main", mesh, rules, rep)
    f = found(rep)
    assert f[("mesh", "dropped_features")] == "WARN"
    assert f[("mesh", "single_bead")] == "INFO"
    assert f[("mesh", "tiny_islands")] == "INFO"
    rep2 = P.Report()
    check_mesh("inlay", fin, rules, rep2, role="inlay")
    f2 = found(rep2)
    assert f2[("mesh", "dropped_features")] == "FAIL"  # an inlay cannot lose strokes
    assert f2[("mesh", "inlay_stroke")] == "FAIL"


def test_inlay_stroke_under_the_nozzle(rules, rep):
    check_mesh("inlay", box(0.37, 10, 0.4), rules, rep, role="inlay")
    assert found(rep)[("mesh", "inlay_stroke")] == "WARN"


def test_overhang_judged_with_companions(rules, rep):
    # The body has a pocket the inlay fills; on its own the pocket's roof
    # would read as unsupported growth.
    pocket = box(20, 20, 2, z=0)
    body = box(30, 30, 4).difference(pocket, engine="manifold")
    fill = [layer_profile(pocket, rules), [{"z": 99.0}]]   # a companion row with no polygon
    check_mesh("body", body, rules, rep, fill=fill)
    ok = [i for i in rep.items if i["code"] == "overhang"][0]
    assert ok["level"] == "OK" and "companions together" in ok["msg"]
    rep2 = P.Report()
    check_mesh("body", body, rules, rep2)
    assert found(rep2)[("mesh", "overhang")] == "FAIL"


def test_overhang_resets_over_a_gap(rules, rep):
    gap = trimesh.util.concatenate([box(10, 10, 1), box(10, 10, 1, z=2)])
    check_mesh("gap", gap, rules, rep)
    assert found(rep)[("mesh", "overhang")] == "OK"    # the upper block is not growth over air


def test_thin_wall_levels(rules, monkeypatch):
    rep = P.Report()
    check_mesh("plate", box(40, 40, 0.6), rules, rep)
    assert found(rep)[("mesh", "thin_wall")] == "WARN"         # p05 0.6 < 0.8
    rep = P.Report()
    check_mesh("skin", box(40, 40, 0.3), rules, rep)
    assert found(rep)[("mesh", "thin_wall")] == "FAIL"         # under two layers
    monkeypatch.setattr(trimesh.proximity, "thickness", lambda *a, **k: np.array([np.nan, -1.0]))
    rep = P.Report()
    check_mesh("no_sample", box(10, 10, 4), rules, rep)
    assert ("mesh", "thin_wall") not in found(rep)

    def boom(*a, **k):
        raise RuntimeError("no rays")
    monkeypatch.setattr(trimesh.proximity, "thickness", boom)
    rep = P.Report()
    check_mesh("broken", box(10, 10, 4), rules, rep)
    msg = [i for i in rep.items if i["code"] == "thin_wall"][0]
    assert msg["level"] == "INFO" and "no rays" in msg["msg"]


# ----------------------------------------------------------------- adhesion

def test_adhesion_nothing_to_judge(rules, rep):
    assert check_adhesion([], {}, rules, rep) is None
    check_adhesion([part(box(0.5, 0.5, 0.5))], {}, rules, rep)   # under 1 mm3: no body at all
    assert rep.items == []


def test_adhesion_skips_bodies_off_the_bed(rules, rep):
    parts = [part(box(20, 20, 5)), part(box(20, 20, 5, x=40, z=3), name="floating")]
    check_adhesion(parts, {}, rules, rep)
    feet = [i for i in rep.items if i["code"] == "footprint"]
    assert len(feet) == 1 and found(rep)[("adhesion", "adhesion")] == "OK"


def test_adhesion_footprint_missing(rules, rep, monkeypatch):
    monkeypatch.setattr(P, "section", lambda mesh, z: None)
    check_adhesion([part(box(20, 20, 5))], {}, rules, rep)
    assert found(rep)[("adhesion", "footprint")] == "FAIL"
    assert found(rep)[("adhesion", "brim")] == "INFO"


def test_adhesion_embedded_glyph(rules, rep):
    bar = box(24, 4, 0.4)
    body = box(40, 30, 12).difference(bar, engine="manifold")
    check_adhesion([part(body), part(bar, name="bar", extruder=2)], {"printer_structure": "i3"}, rules, rep)
    f = found(rep)
    assert f[("adhesion", "embedded")] == "INFO" and f[("adhesion", "adhesion")] == "OK"
    assert "bed slinger" in rep.items[-1]["msg"]


def test_adhesion_tall_and_bed_slinger(rules, rep):
    check_adhesion([part(box(14, 14, 44))], {"printer_structure": "i3"}, rules, rep)
    f = found(rep)
    assert f[("adhesion", "aspect")] == "WARN" and f[("adhesion", "aspect_y")] == "WARN"
    assert f[("adhesion", "brim")] == "INFO"


def test_adhesion_contact_and_pillar(rules, rep):
    check_adhesion([part(mushroom())], {}, rules, rep)
    assert found(rep)[("adhesion", "contact_body")] == "WARN"
    legs = [box(3, 3, 30, x, y) for x in (-15, 15) for y in (-15, 15)]
    rep2 = P.Report()
    check_adhesion([part(union(*legs, box(40, 40, 3, z=29.9)))], {}, rules, rep2)
    assert found(rep2)[("adhesion", "pillar")] == "WARN"


def test_adhesion_strip_and_warp(rules, rep):
    check_adhesion([part(box(0.3, 10, 5))], {}, rules, rep)
    assert found(rep)[("adhesion", "strip")] == "WARN"
    rep2 = P.Report()
    check_adhesion([part(box(200, 20, 3))], {"filament_type": ["PETG"]}, rules, rep2)
    assert found(rep2)[("adhesion", "warp")] == "WARN"         # 200 mm > 180 for PETG


@pytest.mark.parametrize("speed, warned", [(["200"], True), (["50%"], False), (["fast"], False)])
def test_adhesion_small_part_speed(rules, rep, speed, warned):
    check_adhesion([part(box(10, 10, 5))], {"outer_wall_speed": speed}, rules, rep)
    assert (found(rep).get(("adhesion", "small_part_speed")) == "WARN") is warned


def test_adhesion_no_brim_fails_a_risky_body(rules, rep):
    check_adhesion([part(box(14, 14, 44))], {"brim_type": "no_brim"}, rules, rep)
    assert found(rep)[("adhesion", "brim")] == "FAIL"


def test_adhesion_object_settings_override_the_project(rules, rep):
    # The project has a brim; this object turned it off for itself.
    tall = part(box(14, 14, 44), settings={"brim_type": "no_brim"})
    check_adhesion([tall], {"brim_type": "auto_brim"}, rules, rep)
    assert found(rep)[("adhesion", "brim")] == "FAIL"


@pytest.mark.parametrize("temp, level", [(["0"], "FAIL"), (["70"], "OK"), ("65", "OK")])
def test_adhesion_bed_plate_temperature(rules, rep, temp, level):
    st = {"curr_bed_type": "Textured PEI Plate", "textured_plate_temp": temp, "filament_type": ["PETG"]}
    check_adhesion([part(box(20, 20, 5))], st, rules, rep)
    assert found(rep)[("adhesion", "bed_plate")] == level


# ----------------------------------------------------------------- members

def test_gyration_of_nothing():
    assert gyration(Polygon()) == (0.0, 0.0, 0.0)


def test_members_nothing_to_judge(rules, rep):
    assert check_members([], {}, rules, rep) is None
    # Off the bed, and too thin to count as an island: no member at all.
    parts = [part(box(20, 20, 5, z=3)), part(box(0.8, 0.8, 5, x=40), name="hair")]
    assert check_members(parts, {}, rules, rep) == []
    assert rep.items == []


def test_members_levels(rules):
    rep = P.Report()
    worst = check_members([part(box(3, 3, 45))], {"printer_structure": "i3"}, rules, rep)
    assert found(rep)[("members", "slender")] == "WARN" and 40 < worst[0]["lam"] < 60
    rep = P.Report()
    check_members([part(box(3, 3, 100))], {"printer_structure": "corexy"}, rules, rep)
    fail = [i for i in rep.items if i["code"] == "slender"][0]
    assert fail["level"] == "FAIL" and "bed moves in Y" not in fail["msg"]
    assert fail["data"]["lam"] > 60
    rep = P.Report()
    check_members([part(box(20, 20, 10))], {}, rules, rep)
    assert found(rep)[("members", "slender")] == "OK"


# ----------------------------------------------------------------- inlay

def test_inlay_flush_backed_and_clean(rules, rep):
    bar = box(24, 4, 0.4)
    body = box(40, 20, 3).difference(bar, engine="manifold")
    check_inlay("body", body, "bar", bar, rules, rep)
    f = found(rep, "inlay")
    assert f[("inlay", "inlay_backing")] == "OK" and f[("inlay", "inlay_overlap")] == "OK"
    face = [i for i in rep.items if i["code"] == "inlay_face"][0]["msg"]
    assert "flush with the bed face" in face


def test_inlay_one_skin_proud_and_overlapping(rules, rep):
    # A solid body with the inlay inside it and standing above it: they
    # share volume, and nothing of the body covers the inlay from above.
    body = box(40, 20, 3)
    inlay = box(10, 4, 3.6, z=0.2)
    check_inlay("body", body, "inlay", inlay, rules, rep)
    f = found(rep, "inlay")
    assert f[("inlay", "inlay_proud")] == "WARN"
    assert f[("inlay", "inlay_backing")] == "FAIL"
    assert f[("inlay", "inlay_overlap")] == "WARN"
    assert "not on the bed face" in [i for i in rep.items if i["code"] == "inlay_face"][0]["msg"]


def test_inlay_hollow_back_breaks_the_count(rules, rep):
    # A through hole over the inlay: the body runs out above it at once.
    bar = box(10, 4, 0.4)
    body = box(30, 20, 3).difference(box(10, 4, 3, z=0.4), engine="manifold").difference(bar, engine="manifold")
    check_inlay("body", body, "bar", bar, rules, rep)
    assert found(rep, "inlay")[("inlay", "inlay_backing")] == "FAIL"


def test_inlay_below_the_body_and_boolean_failure(rules, rep):
    body = box(20, 20, 2)
    inlay = box(4, 4, 0.4, z=-1)          # under the body: no body section at its first layer

    def boom(*a, **k):
        raise RuntimeError("no manifold")
    body.intersection = boom
    check_inlay("body", body, "inlay", inlay, rules, rep)
    f = found(rep, "inlay")
    assert f[("inlay", "inlay_below")] == "WARN"
    assert f[("inlay", "inlay_overlap")] == "INFO"
    assert ("inlay", "counters") not in f


def test_inlay_counters(rules, rep):
    # The letter O: its counter is a body island smaller than 1 mm2.
    ring = trimesh.creation.annulus(r_min=0.5, r_max=3.0, height=0.4)
    ring.apply_translation([0, 0, 0.2])
    body = box(20, 20, 2).difference(ring, engine="manifold")
    check_inlay("body", body, "O", ring, rules, rep)
    assert found(rep, "inlay")[("inlay", "counters")] == "INFO"


# ----------------------------------------------------------------- groups

def test_groups_chain_overlapping_parts():
    a, b, c = (part(box(10, 10, 2, x=x), name=n) for x, n in ((0, "a"), (6, "b"), (3, "c")))
    far = part(box(10, 10, 2, x=100), name="far")
    gs = groups([a, b, c, far])
    assert sorted(len(g) for g in gs) == [1, 3]


def test_run_parts_judges_inlays(rules, rep):
    bar = box(24, 4, 0.4)
    body = box(40, 20, 3).difference(bar, engine="manifold")
    rows = run_parts([part(body, "body"), part(bar, "bar", extruder=2)], rules, rep)
    assert set(rows) == {"body", "bar"}
    assert any(i["stage"] == "inlay:bar" for i in rep.items)
    # Three parts on one extruder: the small ones are not a second colour.
    rep2 = P.Report()
    run_parts([part(box(30, 30, 3), "a"), part(box(10, 10, 3), "b"), part(box(5, 5, 3), "c")], rules, rep2)
    assert not any(i["stage"].startswith("inlay:") for i in rep2.items)


def test_check_print_end_to_end():
    rep = printcheck.check_print([part(box(40, 30, 12))], {"filament_type": ["PETG"]}, {"layer": 0.2})
    assert not rep.failed and rep.simulation["rows"]
