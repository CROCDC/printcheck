#!/usr/bin/env python3
"""Writes every example file from code, so they can be rebuilt and checked.

    python examples/build.py            # rewrite examples/ in place
    python examples/build.py OUT_DIR    # write the same tree somewhere else

The shapes are chosen for the verdict they give, not for being useful
parts: a block that is GO, a tower that is GO WITH CAVEATS (tall on its
base, on a bed that moves in Y), a post that is NO-GO (it tips, sways and
peels). The same three appear as loose STLs, as Bambu Studio projects, as
a sliced project with synthetic G-code, and as the parts of a demo project
in the kailua layout (`workspace/demo`). `corpus.tsv` labels them the way
`corpus/outcomes.tsv` labels real prints, for `calibrate.py --corpus`.

Output is byte-for-byte deterministic (fixed zip timestamps, no random
sampling), so `tests/test_examples.py` can tell a stale example apart.
"""

import json
import math
import os
import sys
import zipfile

import numpy as np
import trimesh

HERE = os.path.dirname(os.path.abspath(__file__))
STAMP = (2026, 10, 20, 0, 0, 0)
PLATE_CENTRE = (165.0, 160.0)   # the A2L plate is 330 x 320


def box(sx, sy, sz, x=0.0, y=0.0, z=0.0):
    b = trimesh.creation.box((sx, sy, sz))
    b.apply_translation((x, y, z + sz / 2))
    return b


def block():
    return box(40, 30, 12)


def tower():
    return box(14, 14, 44)


def post():
    return box(3, 3, 100)


def label():
    """A plate with a bar of a second colour inlaid flush with its bed face."""
    plate = box(40, 20, 3)
    bar = box(24, 4, 0.4)
    body = plate.difference(bar, engine="manifold")
    return body, bar


SHAPES = {"go_block": block, "caveats_tower": tower, "nogo_post": post}

# What a Bambu Studio project carries for the A2L, reduced to the keys the
# engine reads; values as Bambu writes them (strings, per-filament lists).
SETTINGS = {
    "printer_settings_id": "Bambu Lab A2L 0.4 nozzle",
    "print_settings_id": "0.20mm Standard @BBL A2L",
    "filament_settings_id": ["Bambu PETG HF @BBL A2L"],
    "filament_type": ["PETG"],
    "filament_density": ["1.28"],
    "printer_structure": "i3",
    "curr_bed_type": "Textured PEI Plate",
    "textured_plate_temp": ["70"],
    "textured_plate_temp_initial_layer": ["70"],
    "nozzle_temperature": ["255"],
    "nozzle_temperature_initial_layer": ["255"],
    "layer_height": "0.2",
    "initial_layer_print_height": "0.2",
    "wall_loops": "2",
    "wall_generator": "arachne",
    "min_feature_size": "25%",
    "min_bead_width": "85%",
    "detect_thin_wall": "0",
    "bottom_shell_layers": "3",
    "top_shell_layers": "5",
    "sparse_infill_density": "15%",
    "sparse_infill_pattern": "grid",
    "brim_type": "auto_brim",
    "outer_wall_speed": ["200"],
    "initial_layer_speed": ["50"],
    "enable_support": "0",
    "enable_prime_tower": "0",
    "different_settings_to_system": ["", "", ""],
}


def two_colour(settings):
    st = dict(settings)
    for k in ("filament_settings_id", "filament_type", "filament_density", "textured_plate_temp",
              "textured_plate_temp_initial_layer", "nozzle_temperature", "nozzle_temperature_initial_layer"):
        st[k] = settings[k] * 2
    st["enable_prime_tower"] = "1"
    return st


# ----------------------------------------------------------------- 3MF

def _mesh_xml(oid, mesh):
    v = "".join(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in mesh.vertices)
    t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.faces)
    return (f'<object id="{oid}" type="model"><mesh><vertices>{v}</vertices>'
            f'<triangles>{t}</triangles></mesh></object>')


def write_3mf(path, parts, settings, gcode=None, slice_info="", plate=None, object_settings=None):
    """A Bambu Studio project: one object made of `parts` [(name, mesh,
    extruder)], its meshes in 3D/Objects/object_1.model as components, the
    object placed on the plate by its build item (as Bambu does), the
    settings in Metadata/. With `gcode`, a sliced project (.gcode.3mf)."""
    cx, cy = PLATE_CENTRE
    objects = "".join(_mesh_xml(i + 1, m) for i, (_, m, _) in enumerate(parts))
    comps = "".join(f'<component p:path="/3D/Objects/object_1.model" objectid="{i + 1}" '
                    f'transform="1 0 0 0 1 0 0 0 1 0 0 0"/>' for i in range(len(parts)))
    oid = len(parts) + 1
    root = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
            'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"><resources>'
            f'<object id="{oid}" type="model"><components>{comps}</components></object></resources>'
            f'<build><item objectid="{oid}" transform="1 0 0 0 1 0 0 0 1 {cx} {cy} 0" printable="1"/>'
            '</build></model>')
    own = "".join(f'<metadata key="{k}" value="{v}"/>' for k, v in (object_settings or {}).items())
    meta = "".join(f'<part id="{i + 1}" subtype="normal_part"><metadata key="name" value="{n}"/>'
                   f'<metadata key="extruder" value="{e}"/></part>' for i, (n, _, e) in enumerate(parts))
    model_cfg = (f'<?xml version="1.0" encoding="UTF-8"?><config><object id="{oid}">'
                 f'<metadata key="name" value="{parts[0][0]}"/>{own}{meta}</object></config>')
    files = {
        "3D/3dmodel.model": root,
        "3D/Objects/object_1.model": ('<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter">'
                                      f'<resources>{objects}</resources></model>'),
        "Metadata/project_settings.config": json.dumps(settings, indent=1, sort_keys=True),
        "Metadata/model_settings.config": model_cfg,
    }
    if gcode is not None:
        files["Metadata/plate_1.gcode"] = gcode
        files["Metadata/slice_info.config"] = slice_info
        files["Metadata/plate_1.json"] = json.dumps(plate or {}, sort_keys=True)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(zipfile.ZipInfo(name, STAMP), text)
    return path


# ----------------------------------------------------------------- G-code

def _loop(poly):
    return list(poly.exterior.coords)


def synth_gcode(parts, layer=0.2, wall_mm_s=40.0, infill_mm_s=60.0, tower_until=None, ironing=False):
    """G-code shaped like Bambu Studio's, enough for the engine's `gcode`
    stage: header block, ; Z_HEIGHT and ; FEATURE markers, one outer wall
    per layer from the mesh's own section, a zig-zag infill inside it, a
    brim on the first layer, tool changes (M620/M621) for a second
    filament, a prime tower on the layers up to `tower_until` mm."""
    from printcheck.preflight import section

    cx, cy = PLATE_CENTRE
    area = math.pi * (1.75 / 2) ** 2
    e_per_mm = 0.42 * layer / area
    out = ["; HEADER_BLOCK_START", "; model printing time: 12m 0s; total estimated time: 15m 0s",
           "; total filament weight [g] : " + ",".join("2.00" for _ in parts), "; HEADER_BLOCK_END",
           "M190 S70", "M109 S255", "M204 S5000", "G28"]
    top = max(float(m.bounds[1][2]) for _, m, _ in parts)
    tool = 0
    for k in range(int(round(top / layer))):
        z = round((k + 1) * layer, 3)
        out += [f"; Z_HEIGHT: {z}", f"M201 N1 Y{int(8000 - 10 * k)}"]
        for _, mesh, extruder in parts:
            if z > float(mesh.bounds[1][2]) + 1e-6:
                continue
            p = section(mesh, z - layer / 2)
            if p is None:
                continue
            if extruder - 1 != tool:
                tool = extruder - 1
                out += ["M620 S{}A".format(tool), f"T{tool}", "M621 S{}A".format(tool)]
            polys = list(p.geoms) if p.geom_type == "MultiPolygon" else [p]
            for q in polys:
                q = q.buffer(0)
                pts = [(x + cx, y + cy) for x, y in _loop(q)]
                if k == 0:
                    out.append("; FEATURE: Brim")
                    bpts = [(x + cx, y + cy) for x, y in _loop(q.buffer(3, join_style=2))]
                    out.append(f"G0 X{bpts[0][0]:.3f} Y{bpts[0][1]:.3f} F12000")
                    out += [f"G1 X{x:.3f} Y{y:.3f} E{e_per_mm * 3:.5f} F1800" for x, y in bpts[1:]]
                out += ["; FEATURE: Outer wall", "; LINE_WIDTH: 0.42",
                        f"G0 X{pts[0][0]:.3f} Y{pts[0][1]:.3f} F12000"]
                prev = pts[0]
                for x, y in pts[1:]:
                    d = math.dist(prev, (x, y))
                    out.append(f"G1 X{x:.3f} Y{y:.3f} E{e_per_mm * d:.5f} F{wall_mm_s * 60:.0f}")
                    prev = (x, y)
                inner = q.buffer(-0.84)
                if not inner.is_empty and inner.geom_type == "Polygon":
                    out.append("; FEATURE: Sparse infill")
                    x0, y0, x1, y1 = inner.bounds
                    ys = np.arange(y0, y1, 1.0)
                    for i, yy in enumerate(ys):
                        a, b = (x0, x1) if i % 2 == 0 else (x1, x0)
                        out.append(f"G0 X{a + cx:.3f} Y{yy + cy:.3f} F12000")
                        out.append(f"G1 X{b + cx:.3f} Y{yy + cy:.3f} E{e_per_mm * (x1 - x0):.5f} "
                                   f"F{infill_mm_s * 60:.0f}")
        if tower_until is not None and z <= tower_until + 1e-6:
            tx, ty = cx + 60, cy + 60
            out += ["; FEATURE: Prime tower", f"G0 X{tx:.3f} Y{ty:.3f} F12000"]
            for dx, dy in ((20, 0), (20, 20), (0, 20), (0, 0)):
                out.append(f"G1 X{tx + dx:.3f} Y{ty + dy:.3f} E{e_per_mm * 20:.5f} F3000")
        if ironing and k == int(round(top / layer)) - 1:
            out += ["M1031", "; FEATURE: Ironing", "M1031"]
    out += ["; CONFIG_BLOCK_START", "; printable_area = 0x0,330x0,330x320,0x320",
            "; filament_density = " + ",".join("1.28" for _ in parts), "; default_acceleration = 5000",
            "; brim_type = outer_only", "; CONFIG_BLOCK_END"]
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------- the tree

DEMO_SPEC = '''"""{doc}"""

BAMBU = {{"filaments": [("Bambu PETG HF @BBL A2L", "black")]}}
LAYER = 0.2
MATERIAL = "PETG"
{extra}
PRINT_FILES = {files}
'''

DEMO_PARTS = {
    "go_block": dict(
        doc="A block 40 x 30 x 12 with a bar of a second colour inlaid in its bed face: GO.",
        meshes={"go_block": None, "go_block_bar": None},
        files=[{"name": "go_block", "variant": "go_block", "also": ["go_block_bar"]}],
        extra=""),
    "caveats_tower": dict(
        doc="A tower 14 x 14 x 44, modelled lying down and stood up to print: GO WITH CAVEATS.",
        meshes={"caveats_tower": None},
        files=[{"name": "caveats_tower", "variant": "caveats_tower"}],
        extra='ROTATION = ("x", 90)          # authored lying along y, printed standing\n'),
    "nogo_post": dict(
        doc="A post 3 x 3 x 100, printed in two pieces cut at z = 50: each piece is NO-GO.",
        meshes={"nogo_post": None},
        files=[{"name": "nogo_post", "variant": "nogo_post", "cut": ["z", 50.0]}],
        extra='PRINTCHECK = {"max_slenderness_warn": 40.0}   # the default, stated to show the hook\n'),
}


def _stl(mesh, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mesh.export(path, file_type="stl")


def build(out=HERE):
    written = []

    def put(path):
        written.append(os.path.relpath(path, out))
        return path

    # Loose STLs: the CLI's simplest input.
    for name, make in SHAPES.items():
        _stl(make(), put(os.path.join(out, "stl", f"{name}.stl")))
    body, bar = label()
    _stl(body, put(os.path.join(out, "stl", "label_body.stl")))
    _stl(bar, put(os.path.join(out, "stl", "label_bar.stl")))

    # Bambu Studio projects: settings, plate placement, one sliced job.
    write_3mf(put(os.path.join(out, "bambu", "go_block.3mf")), [("go_block", block(), 1)], SETTINGS)
    write_3mf(put(os.path.join(out, "bambu", "caveats_tower.3mf")), [("caveats_tower", tower(), 1)],
              SETTINGS, object_settings={"brim_type": "outer_only"})
    write_3mf(put(os.path.join(out, "bambu", "nogo_post.3mf")), [("nogo_post", post(), 1)],
              dict(SETTINGS, brim_type="no_brim"))
    # A two-colour label whose second colour ends at layer 2: the prime
    # tower stops there too, a 0.4 mm film (the K05 failure, 2026-10-15).
    parts = [("label_body", body, 1), ("label_bar", bar, 2)]
    info = ('<?xml version="1.0" encoding="UTF-8"?><config><plate><metadata key="index" value="1"/>'
            '<metadata key="outside" value="false"/><metadata key="support_used" value="false"/>'
            '<layer_filament_lists><layer_filament_list filament_list="0 1" layer_ranges="0 1"/>'
            '</layer_filament_lists></plate></config>')
    plate = {"bbox_objects": [{"name": "label_body", "area": 800.0}, {"name": "label_bar", "area": 96.0}]}
    write_3mf(put(os.path.join(out, "bambu", "nogo_label_tower_film.gcode.3mf")), parts, two_colour(SETTINGS),
              gcode=synth_gcode(parts, tower_until=0.4), slice_info=info, plate=plate)

    # A project in the kailua layout: lib/ + parts/<part>/, run by lib/run.py.
    demo = os.path.join(out, "workspace", "demo")
    meshes = {"go_block": block(), "go_block_bar": None, "caveats_tower": tower(), "nogo_post": post()}
    pocketed = block().difference(box(24, 4, 0.4), engine="manifold")
    meshes["go_block"], meshes["go_block_bar"] = pocketed, box(24, 4, 0.4)
    lying = tower()
    lying.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))
    meshes["caveats_tower"] = lying
    for part, d in DEMO_PARTS.items():
        pdir = os.path.join(demo, "parts", part)
        os.makedirs(pdir, exist_ok=True)
        with open(put(os.path.join(pdir, "spec.py")), "w") as fh:
            fh.write(DEMO_SPEC.format(doc=d["doc"], extra=d["extra"], files=json.dumps(d["files"])))
        for variant in d["meshes"]:
            _stl(meshes[variant], put(os.path.join(pdir, "meshes", f"{variant}.stl")))
        printed = [(variant, meshes[variant], i + 1) for i, variant in enumerate(d["meshes"])]
        if part == "caveats_tower":
            printed = [("caveats_tower", tower(), 1)]
        write_3mf(put(os.path.join(pdir, "print", "to_print", f"{part}.3mf")), printed, SETTINGS)
    return written


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else HERE
    for f in build(target):
        print(f)
