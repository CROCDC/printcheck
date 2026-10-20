"""The slicing side of the engine: Bambu projects (.3mf) as they are read,
the project settings, the slicer's slice_info, and the G-code itself.

The G-code cases are written by hand, a few lines each, so every rule of
`check_gcode` is driven by exactly the move that should trip it.
"""

import json

import numpy as np

from printcheck.preflight import (_lst, _parse_meshes, _transform, _xml_attr, check_gcode,
                                  check_settings, check_slice_info, load_project)

from conftest import box, example, found, write_zip


# ----------------------------------------------------------------- 3MF

def _vertices(mesh):
    v = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in mesh.vertices)
    t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.faces)
    return f"<mesh><vertices>{v}</vertices><triangles>{t}</triangles></mesh>"


def test_xml_helpers():
    assert _xml_attr('<item objectid="3" transform="1"/>', "objectid") == "3"
    assert _xml_attr('<item objectid="3"/>', "transform") is None
    M, T = _transform("1 0 0 0 1 0 0 0 1 10 20 0.5")
    assert np.allclose(M, np.eye(3)) and np.allclose(T, [10, 20, 0.5])
    # An object with no mesh (a component holder) yields nothing.
    assert _parse_meshes('<object id="1" type="model"><components/></object>') == {}


def test_lst():
    assert _lst(["a", "b"], 1) == "b"
    assert _lst(["a", "b"], 5) == "b"          # past the end: the last filament's value
    assert _lst([], 0) is None
    assert _lst("x", 3) == "x"


def components_3mf(tmp_path):
    """The Bambu layout: meshes in 3D/Objects, the root holds components and
    the build items, model_settings names the parts and their extruders."""
    a, b, c = box(10, 10, 5), box(4, 4, 1), box(2, 2, 2)
    objects = (f'<model><resources><object id="1" type="model">{_vertices(a)}</object>'
               f'<object id="2" type="model">{_vertices(b)}</object></resources></model>')
    root = ('<model xmlns:p="x"><resources>'
            f'<object id="7" type="model">{_vertices(c)}</object>'
            '<object id="10" type="model"><components>'
            '<component p:path="/3D/Objects/object_1.model" objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
            '<component p:path="/3D/Objects/object_1.model" objectid="2"/>'
            '<component p:path="/3D/Objects/object_1.model" objectid="99"/>'
            '</components></object>'
            '<object id="11" type="model"><components>'
            '<component objectid="7"/>'
            '</components></object></resources>'
            '<build><item objectid="10" transform="1 0 0 0 1 0 0 0 1 100 50 0"/>'
            '<item objectid="11"/></build></model>')
    model_cfg = ('<config><object id="10"><metadata key="name" value="holder"/>'
                 '<metadata key="brim_type" value="no_brim"/>'
                 '<part id="1" subtype="normal_part"><metadata key="name" value="body"/>'
                 '<metadata key="extruder" value="2"/></part>'
                 '<part id="2" subtype="normal_part"><metadata key="extruder" value=""/></part>'
                 '<part id="42"/></object></config>')
    return write_zip(tmp_path / "comp.3mf", {
        "3D/3dmodel.model": root,
        "3D/Objects/object_1.model": objects,
        "Metadata/project_settings.config": json.dumps({"filament_type": ["PLA"]}),
        "Metadata/model_settings.config": model_cfg,
        "Metadata/plate_1.gcode": "; Z_HEIGHT: 0.2\n",
        "Metadata/slice_info.config": "<config/>",
        "Metadata/plate_1.json": json.dumps({"bbox_objects": []}),
    })


def test_load_project_components(tmp_path):
    proj = load_project(components_3mf(tmp_path))
    assert proj["settings"] == {"filament_type": ["PLA"]}
    assert proj["gcode"].startswith("; Z_HEIGHT") and proj["slice_info"] == "<config/>"
    assert proj["plate"] == {"bbox_objects": []}
    by = {p["name"]: p for p in proj["parts"]}
    # Object 99 has no mesh anywhere: skipped. Object 7 lives in the root file.
    assert set(by) == {"body", "part 2", "object 7"}
    body = by["body"]
    assert body["extruder"] == 2 and body["settings"] == {"brim_type": "no_brim"}
    # The build item moves the component onto the plate.
    assert np.allclose(body["mesh"].bounds[0], [95, 45, 0])
    assert by["part 2"]["extruder"] == 1                        # empty extruder value
    assert np.allclose(by["part 2"]["mesh"].bounds[0], [98, 48, 0])
    # The holder of object 7 has an item without a transform: left where it is.
    assert np.allclose(by["object 7"]["mesh"].bounds[0], [-1, -1, 0])
    assert by["object 7"]["settings"] == {}


def test_load_project_plain_root(tmp_path):
    """Meshes straight in the root model, no metadata files at all."""
    a, b = box(10, 10, 5), box(6, 6, 3)
    root = (f'<model><resources><object id="1" type="model">{_vertices(a)}</object>'
            f'<object id="2" type="model">{_vertices(b)}</object></resources>'
            '<build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 20 30 0"/></build></model>')
    proj = load_project(write_zip(tmp_path / "plain.3mf", {"3D/3dmodel.model": root}))
    assert proj["settings"] == {} and proj["gcode"] is None
    assert proj["slice_info"] == "" and proj["plate"] == {}
    by = {p["name"]: p for p in proj["parts"]}
    assert set(by) == {"object 1", "object 2"}
    assert np.allclose(by["object 1"]["mesh"].bounds[0], [15, 25, 0])
    assert np.allclose(by["object 2"]["mesh"].bounds[0], [-3, -3, 0])


def test_load_project_example():
    proj = load_project(example("bambu", "nogo_label_tower_film.gcode.3mf"))
    assert [p["extruder"] for p in proj["parts"]] == [1, 2]
    assert proj["gcode"] and "layer_filament_list" in proj["slice_info"]


# ----------------------------------------------------------------- settings

BASE = {
    "printer_settings_id": "A2L", "print_settings_id": "0.20mm", "filament_settings_id": ["PETG HF"],
    "filament_type": ["PETG"], "curr_bed_type": "Textured PEI Plate", "textured_plate_temp": ["70"],
    "layer_height": "0.2", "sparse_infill_density": "15%", "wall_generator": "arachne",
    "different_settings_to_system": ["", "brim_type", ""],
}


def test_settings_empty(rep, rules):
    check_settings({}, {1}, rules, rep)
    assert found(rep) == {("settings", "settings"): "WARN"}


def test_settings_clean(rep, rules):
    check_settings(BASE, set(), rules, rep)          # no extruder used: the first one is assumed
    f = found(rep)
    assert f[("settings", "materials")] == "OK"
    assert f[("settings", "bed_temp")] == "INFO" and f[("settings", "arachne")] == "INFO"
    assert ("settings", "dense_infill") not in f and ("settings", "layer_height") not in f
    over = next(i for i in rep.items if i["code"] == "overrides")
    assert "brim_type" in over["msg"]


def test_settings_mixed_materials_and_beds(rep, rules):
    st = dict(BASE, filament_type=["PETG", "PLA"], textured_plate_temp=["70", "55"],
              filament_settings_id=["a", "b"], layer_height="0.16", sparse_infill_density="100%",
              wall_generator="classic", detect_thin_wall="0", different_settings_to_system="x")
    check_settings(st, {1, 2}, rules, rep)
    f = found(rep)
    assert f[("settings", "mixed_materials")] == "FAIL"
    assert f[("settings", "bed_temp")] == "WARN"
    assert f[("settings", "layer_height")] == "INFO"
    assert f[("settings", "dense_infill")] == "WARN"
    assert f[("settings", "thin_walls")] == "WARN"
    assert ("settings", "overrides") not in f        # not a list: nothing declared


def test_settings_policy_and_first_layer_bed(rep, rules):
    # Same main bed, different first-layer bed; the material rule switched off.
    st = dict(BASE, filament_type=["PETG", "PLA"], textured_plate_temp=["70", "70"],
              textured_plate_temp_initial_layer=["70", "60"], wall_generator="classic",
              detect_thin_wall="1")
    check_settings(st, {1, 2}, dict(rules, same_material=False), rep)
    f = found(rep)
    assert f[("settings", "materials")] == "OK"
    assert f[("settings", "bed_temp")] == "WARN"
    assert ("settings", "thin_walls") not in f and ("settings", "arachne") not in f


def test_settings_unknown_plate_and_missing_keys(rep, rules):
    st = {"filament_type": ["PLA"], "curr_bed_type": "Gold Plate"}
    check_settings(st, {1}, rules, rep)
    assert ("settings", "bed_temp") not in found(rep)
    rep2 = type(rep)()
    check_settings({"filament_type": ["PLA"], "curr_bed_type": "Cool Plate"}, {1}, rules, rep2)
    assert ("settings", "bed_temp") not in found(rep2)   # the plate has no temperature key here


# ----------------------------------------------------------------- slice_info

def test_slice_info_empty(rep):
    check_slice_info("", {}, rep)
    assert rep.items == []


def test_slice_info_everything(rep):
    xml = ('<warning msg="too close" level="1" error_code ="1000C001"/>'
           '<warning msg="note" level="3" error_code="0"/>'
           '<metadata key="outside" value="true"/><metadata key="support_used" value="false"/>'
           '<layer_filament_list filament_list="0 1" layer_ranges="0 3"/>')
    check_slice_info(xml, {"bbox_objects": [{"name": "a", "area": 12.5}, {"name": "b"}]}, rep)
    levels = [(i["code"], i["level"]) for i in rep.items]
    assert ("slicer_warning", "WARN") in levels and ("slicer_warning", "INFO") in levels
    assert ("outside", "FAIL") in levels and ("support_used", "INFO") in levels
    assert ("layer_filaments", "INFO") in levels
    areas = [i["msg"] for i in rep.items if i["code"] == "first_layer_area"]
    assert "12.5 mm2" in areas[0] and "0.0 mm2" in areas[1]


def test_slice_info_inside(rep):
    check_slice_info('<metadata key="outside" value="false"/>', {}, rep)
    assert found(rep) == {}


# ----------------------------------------------------------------- G-code

def square(x, y, s, feat="Outer wall", F=1800, e=0.05):
    """One closed square of extrusion: the feature marker, a travel, four sides."""
    return [f"; FEATURE: {feat}", f"G0 X{x} Y{y} F12000",
            f"G1 X{x + s} Y{y} E{e} F{F}", f"G1 X{x + s} Y{y + s} E{e}",
            f"G1 X{x} Y{y + s} E{e}", f"G1 X{x} Y{y} E{e}"]


def infill(x, y, length, rows, F=600):
    out = ["; FEATURE: Sparse infill"]
    for k in range(rows):
        a, b = (x, x + length) if k % 2 == 0 else (x + length, x)
        out += [f"G0 X{a} Y{y + k} F12000", f"G1 X{b} Y{y + k} E0.2 F{F}"]
    return out


def gcode(layers, header=(), config=(), pre=()):
    lines = ["; HEADER_BLOCK_START", *header, "; HEADER_BLOCK_END", *pre]
    for z, body in layers:
        lines += [f"; Z_HEIGHT: {z}", *body]
    lines += ["; CONFIG_BLOCK_START", *config, "; CONFIG_BLOCK_END"]
    return "\n".join(lines) + "\n"


def judge(text, rules, settings=None):
    from printcheck.preflight import Report
    r = Report()
    check_gcode(text, rules, r, settings)
    return r, found(r)


def test_gcode_without_layers(rules):
    r, f = judge("G28\nG1 X10 Y10 E1 F600\n", rules)
    assert f == {("gcode", "gcode"): "FAIL"}
    assert not hasattr(r, "layers")


def test_gcode_healthy_single_colour(rules):
    """Slow-enough layers, a wide footprint with a brim, inside the plate."""
    layer_body = square(100, 100, 40) + infill(102, 102, 36, 6)
    layers = [(0.2, square(97, 97, 46, "Brim") + layer_body)]
    layers += [(round(0.2 * k, 1), layer_body) for k in range(2, 12)]
    text = gcode(layers, header=["; model printing time: 5m; total estimated time: 6m",
                                 "; total filament weight [g] : 0.08", "; no colon here"],
                 config=["; printable_area = 0x0,330x0,330x320,0x320", "; filament_density = 1.25"],
                 pre=["M190 S70", "M190 S60", "M109 S250", "M109 S240", "M204 S5000"])
    r, f = judge(text, rules)
    assert f[("gcode", "layer_time")] == "OK"
    assert f[("gcode", "usage")] == "INFO" and f[("gcode", "brim_laid")] == "INFO"
    assert ("gcode", "outside") not in f and ("gcode", "outer_wall_speed") not in f
    temps = next(i["msg"] for i in r.items if i["code"] == "temps")
    assert "M190 70.0" in temps and "M109 250.0" in temps       # the first of each, not the last
    header = next(i["msg"] for i in r.items if i["code"] == "header")
    assert "5m" in header and "6m" in header and "0.08" in header
    assert len(r.layers) == 11 and r.layers[0]["z"] == 0.2


def test_gcode_short_layers_fail(rules):
    # Tiny fast squares: every layer far under 8 s, the median too.
    layers = [(round(0.2 * k, 1), square(150, 150, 5, F=6000)) for k in range(1, 6)]
    r, f = judge(gcode(layers, config=["; brim_type = no_brim"]), rules)
    assert f[("gcode", "layer_time")] == "FAIL"
    assert ("gcode", "brim_laid") not in f                    # no brim asked for, none laid


def test_gcode_some_short_layers_warn(rules):
    slow = square(100, 100, 40) + infill(102, 102, 36, 6)
    fast = square(150, 150, 5, F=6000)
    layers = [(0.2, slow), (0.4, slow), (0.6, slow), (0.8, fast)]
    r, f = judge(gcode(layers), rules)
    assert f[("gcode", "layer_time")] == "WARN"


def test_gcode_turns_and_short_segments(rules):
    """Straight continuations carry speed through a junction; a segment too
    short to reach its feed is timed as a triangle."""
    straight = ["; FEATURE: Outer wall", "G0 X0 Y0 F12000",
                "G1 X10 Y0 E0.1 F1800", "G1 X20 Y0 E0.1", "G1 X20.01 Y0 E0.001 F600"]
    r, _ = judge(gcode([(0.2, straight)], config=["; default_acceleration = 500"]), rules)
    t_straight = r.layers[0]["time_s"]
    turned = ["; FEATURE: Outer wall", "G0 X0 Y0 F12000",
              "G1 X10 Y0 E0.1 F1800", "G1 X10 Y10 E0.1", "G1 X10.01 Y10 E0.001 F600"]
    r2, _ = judge(gcode([(0.2, turned)], config=["; default_acceleration = 500"]), rules)
    assert r2.layers[0]["time_s"] > t_straight > 0


def test_gcode_skips_custom_flush_changes_and_travel(rules):
    """Moves inside a filament change, Custom and Flush blocks, moves with no
    feed yet, retractions and moves before the first layer add nothing."""
    pre = ["M620 S1A", "M621 S1A",                 # a change before any layer
           "M201 N1 Y8000",                        # acceleration before any layer: ignored
           "G1 X5 Y5 E1"]                          # no feed set yet, no layer yet
    body = ["G1 X1 Y1 E1",                         # first move of the layer: f is still 0
            "; FEATURE: Custom", "G1 X50 Y50 E5 F600",
            "; FEATURE: Flush", "G1 X60 Y60 E5 F600",
            "M620 S1A", "G1 X70 Y70 E5 F600", "M621 S1A",
            "; FEATURE: Outer wall", "G1 Z0.4", "G1 E-0.8 F1800",
            "G1 X80 Y80 E-0.5 F1800",              # moving while retracting: no volume
            *square(100, 100, 20)]
    r, f = judge(gcode([(0.2, body)], pre=pre), rules)
    # Only the feed-less first move (no feature yet) and the wall count.
    mm3 = r.layers[0]["mm3"]
    assert set(mm3) == {"T0/", "T0/Outer wall"} and mm3["T0/"] < 3
    assert r.layers[0]["changes"] == 1
    changes = next(i["msg"] for i in r.items if i["code"] == "changes")
    assert changes.startswith("2 filament change(s)")


def test_gcode_crawling_wall_and_missing_walls(rules):
    layers = [(0.2, square(100, 100, 40, F=300) + infill(102, 102, 36, 6)),
              (0.4, infill(102, 102, 36, 6)),
              (0.6, ["; FEATURE: Outer wall", "G0 X10 Y10 F12000", "G1 X10.1 Y10 E0.001 F1800"])]
    r, f = judge(gcode(layers), rules)
    assert f[("gcode", "outer_wall_speed")] == "WARN"
    assert f[("gcode", "no_outer_wall")] == "WARN"
    assert f[("gcode", "sparse_layers")] == "INFO"
    assert f[("gcode", "infill_in_thin_part")] == "INFO"


def test_gcode_second_colour_tower_and_purge(rules):
    """Two colours: the second only on layers 2-3 and off the bed face, a
    prime tower 0.6 mm tall right next to the part, purge billed far over
    what the part holds, ironing on, and an extrusion off the plate."""
    def lay(z, second):
        body = square(100, 100, 20)
        if second:
            body += ["M620 S1A", "T1", "M621 S1A", *square(105, 105, 5, e=0.01),
                     "M620 S0A", "T0", "M621 S0A"]
        body += square(122, 100, 10, "Prime tower")
        return (z, body)
    layers = [lay(0.2, False), lay(0.4, True), lay(0.6, True), (0.8, square(100, 100, 20)),
              (1.0, square(100, 100, 20) + ["M1031", "T255", "M1031"]),
              (1.2, square(400, 100, 5))]
    text = gcode(layers, header=["; total filament weight [g] : 1.00,5.00"],
                 config=["; printable_area = 0x0,330x0,330x320,0x320",
                         "; filament_density = 1.25", "; brim_type = auto_brim"])
    r, f = judge(text, rules)
    assert f[("gcode", "second_colour_face")] == "WARN"
    assert f[("gcode", "tower_gap")] == "WARN"
    assert f[("gcode", "tower_height")] == "FAIL"
    assert f[("gcode", "purge")] == "WARN"
    assert f[("gcode", "ironing")] == "FAIL"
    assert f[("gcode", "outside")] == "FAIL"
    # auto_brim laid nothing on a footprint 20 mm across: the slicer's call is a risk.
    assert f[("gcode", "brim_laid")] == "WARN"
    second = next(i["msg"] for i in r.items if i["code"] == "second_colour")
    assert "2 layer(s) z 0.4-0.6" in second
    # T1 uses the second density entry; only T0 and T1 are billed.
    usage = [i["msg"] for i in r.items if i["code"] in ("usage", "purge")]
    assert any(m.startswith("T1:") and "billed 5.00" in m for m in usage)


def test_gcode_second_colour_on_face_tall_tower(rules):
    def lay(z):
        return (z, square(100, 100, 30) + ["M620 S1A", "T1", "M621 S1A", *square(105, 105, 5),
                                           "M620 S0A", "T0", "M621 S0A",
                                           *square(200, 200, 10, "Prime tower")])
    layers = [lay(round(0.2 * k, 1)) for k in range(1, 8)]
    text = gcode(layers, header=["; total filament weight [g] : 50.0"],
                 config=["; filament_density = 1.25"])
    r, f = judge(text, rules, settings={"brim_type": "outer_only"})
    assert ("gcode", "second_colour_face") not in f
    assert f[("gcode", "tower_gap")] == "INFO" and f[("gcode", "tower_height")] == "INFO"
    # T1 is past the one billed weight and the one density: no purge figure for it.
    usage = {i["msg"].split(":")[0]: i for i in r.items if i["code"] in ("usage", "purge")}
    assert "billed" not in usage["T1"]["msg"]
    # outer_only asked for, nothing laid, footprint 100+ mm across: the slicer's call.
    assert f[("gcode", "brim_laid")] == "INFO"


def test_gcode_brim_missing_without_first_layer_part(rules):
    # The first layer lays only the tower: no footprint to measure.
    layers = [(0.2, square(200, 200, 10, "Prime tower")), (0.4, square(100, 100, 20))]
    r, f = judge(gcode(layers), rules)
    msg = next(i for i in r.items if i["code"] == "brim_laid")
    assert msg["level"] == "INFO" and "judged the footprint stable" in msg["msg"]


def test_gcode_acceleration_markers(rules):
    body = ["M201 N1 Y6500", "M204 S1000", *square(100, 100, 20)]
    r, _ = judge(gcode([(0.2, body), (0.4, square(100, 100, 20))]), rules)
    assert r.layers[0]["acc_y"] == 6500.0 and r.layers[1]["acc_y"] is None


def test_gcode_of_the_sliced_example(rules):
    from printcheck.preflight import Report
    proj = load_project(example("bambu", "nogo_label_tower_film.gcode.3mf"))
    r = Report()
    check_gcode(proj["gcode"], rules, r, proj["settings"])
    f = found(r)
    assert f[("gcode", "tower_height")] == "FAIL"
    assert f[("gcode", "brim_laid")] == "INFO"


def test_gcode_empty_default_acceleration(rules):
    # Bambu leaves the key empty on some machines: the engine falls back to 6000.
    r, _ = judge(gcode([(0.2, square(100, 100, 20))], config=["; default_acceleration = "]), rules)
    assert r.layers[0]["time_s"] > 0
