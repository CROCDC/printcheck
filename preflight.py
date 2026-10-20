#!/usr/bin/env python3
"""Pre-flight checks on a print file before it goes to the printer.

Takes any of: loose STLs (a main body plus optional inlay/companion STLs),
a Bambu Studio project (.3mf), a sliced project (.gcode.3mf) or a bare
.gcode. Every stage emits FAIL / WARN / INFO findings with the measured
numbers; the exit code is 1 when anything FAILed.

    preflight.py part.stl [inlay.stl ...]
    preflight.py project.3mf [--slice]
    preflight.py project.gcode.3mf
    preflight.py plate_1.gcode
    preflight.py ... --json report.json --nozzle 0.4 --layer 0.2

Needs trimesh, shapely and numpy (the project venv usually has them).
"""

import argparse
import collections
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import zipfile

import logging
import warnings

import numpy as np
import trimesh
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

# trimesh chatters about every unreferenced vertex it merges; the findings are the output.
logging.getLogger("trimesh").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

BAMBU = os.environ.get("BAMBU_STUDIO",
                       "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio")

# Every threshold lives here so a project can override it with --rules.
# Reasons and sources for the defaults are in docs/RULES.md.
RULES = {
    "max_sparse_infill_pct": 50,   # sparse infill at or above this is a FAIL (2026-10-15)
    "nozzle": 0.4,
    "layer": 0.2,
    "bead": 0.42,                 # default line width on a 0.4 nozzle
    "min_bead_pct": 0.85,         # Arachne min_bead_width: thinner is redrawn as one bead
    "min_feature_pct": 0.25,      # Arachne min_feature_size: thinner is not printed
    "min_layers": 4,              # a part under this is a film, not a part
    "min_wall": 0.8,              # two beads; service-bureau floor for FDM
    "min_island_mm2": 1.0,        # islands under this tend to be dropped or smeared
    "max_unsupported_mm2": 300.0, # per-layer growth past a 45 deg flare
    "min_contact_ratio": 0.5,     # first-layer area over the widest layer
    "min_base_layers_over_inlay": 2,  # continuous base-only layers above a two-colour inlay
    "min_layer_time_s": 8.0,      # under this the previous layer is still soft (PETG)
    "min_outer_wall_speed": 15.0, # mm/s; the slicer crawling means it is fighting heat
    "max_purge_ratio": 1.0,       # purge grams over part grams
    "same_material": True,        # all filaments in a part must share filament_type
    # Bed adhesion, per body as printed (references/rules.md, "Bed adhesion").
    "min_com_margin": 2.0,        # mm the centre of mass sits inside the footprint's hull
    "max_aspect_warn": 3.0,       # height over the footprint's narrowest width
    "max_aspect_fail": 6.0,
    "max_aspect_bedslinger_warn": 2.5,  # height over the footprint's depth along a moving bed (Y)
    "max_pillar_ratio": 10.0,     # height carried over the width of one of several contact islands
    # Free-standing members: a run of layers with nothing beside it, judged by
    # its slenderness, free height over the section's least radius of gyration
    # (references/rules.md, "Free-standing members").
    "member_step": 1.0,           # mm between the sections that follow each member
    "max_slenderness_warn": 40.0,
    "max_slenderness_fail": 60.0,
    "warp_length_mm": {"PLA": 250, "PETG": 180, "ABS": 120, "ASA": 120, "PA": 100,
                       "PC": 100, "TPU": 400},
    "thin_sample_points": 3000,
    # The layer simulation (simulate.py, docs/SIMULATION.md). Constants
    # marked provisional wait for the coupon prints of the research plan.
    "sim_max_layers": 250,        # sample every layer up to this many, then stride
    "sim_skip_first_layers": 3,   # the cooling floor is judged above these
    "peel_mpa": {"PLA": 0.1, "PETG": 0.1, "TPU": 0.1},   # provisional: first-layer peel strength on textured PEI
    "modulus_mpa": {"PLA": 2750.0, "PETG": 2050.0, "TPU": 100.0},  # flexural, Bambu data sheets
    "min_overturn_margin_warn": 3.0,  # resisting over overturning moment, per body (bed shake + nozzle push)
    "min_overturn_margin_fail": 1.0,
    "max_tip_sway_warn": 0.2,     # mm a free member's tip moves under the nozzle's drag: half a bead
    "max_tip_sway_fail": 0.5,     # more than a bead: the nozzle catches the top
    "max_short_layer_share": 0.3, # share of layers the cooling rule cannot stretch to its floor
    "max_printed_mass_g": 2000.0, # the A2L's limit (slicer error bit 11)
    "min_tower_height_mm": 1.0,   # a prime tower shorter than this is a loose film (2026-10-14, 10-15)
    "small_footprint_mm": 25.0,   # under this, a part with no brim laid is at the nozzle's mercy
    "max_small_part_wall_speed": 100.0,  # mm/s outer wall on a small footprint (2026-10-15 night)
}

JUNCTION_DEG = 30.0
JUNCTION_COS = math.cos(math.radians(JUNCTION_DEG))
FIL_RADIUS = 1.75 / 2


class Report:
    def __init__(self):
        self.items = []
        self.stage = ""

    def add(self, level, code, msg, **data):
        self.items.append({"stage": self.stage, "level": level, "code": code,
                           "msg": msg, **({"data": data} if data else {})})

    def fail(self, code, msg, **d): self.add("FAIL", code, msg, **d)
    def warn(self, code, msg, **d): self.add("WARN", code, msg, **d)
    def info(self, code, msg, **d): self.add("INFO", code, msg, **d)
    def ok(self, code, msg, **d): self.add("OK", code, msg, **d)

    @property
    def failed(self):
        return any(i["level"] == "FAIL" for i in self.items)


# ----------------------------------------------------------------- geometry

def geoms(p):
    if p is None or p.is_empty:
        return []
    return list(p.geoms) if p.geom_type == "MultiPolygon" else [p]


def section(mesh, z):
    # A vertex lying on the plane breaks trimesh's outline: the lowest vertex
    # of a horizontal bore sat exactly on a layer's mid-plane and the section
    # lost 711 mm2, read as a 685 mm2 overhang on the next layer (the lathe
    # copier's pie, 2026-10-17). A hair off the plane is the same layer.
    if np.any(np.abs(mesh.vertices[:, 2] - z) < 1e-6):
        z += 1e-3
    s = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    if s is None:
        return None
    # Keep the mesh's own x, y. to_2D() picks a planar frame per section, so
    # polygons of two parts (body and inlay) or of two layers could not be
    # compared: it put the plate's letters 4.8 mm off its body and read the
    # pockets as uncovered (2026-10-15).
    T = np.eye(4)
    T[2, 3] = -z
    p2, _ = s.to_planar(to_2D=T)
    polys = [p for p in p2.polygons_full if p is not None and not p.is_empty]
    return unary_union(polys) if polys else None


def thinnest(poly, keep=0.85, r_max=3.0):
    """Width of the thinnest stroke that still carries `keep` of the area.
    Corners always lose a little under an opening, hence 85 %."""
    lo, hi = 0.0, r_max
    for _ in range(20):
        r = (lo + hi) / 2
        if poly.buffer(-r).buffer(r).area >= keep * poly.area:
            lo = r
        else:
            hi = r
    return 2 * lo


def layer_zs(mesh, layer):
    lo, hi = mesh.bounds[0][2], mesh.bounds[1][2]
    return np.arange(lo + layer / 2, hi, layer)


def layer_profile(mesh, rules):
    """Per layer: islands, area, how many islands die under each erosion."""
    layer, bead = rules["layer"], rules["bead"]
    min_bead = rules["nozzle"] * rules["min_bead_pct"]
    rows = []
    for z in layer_zs(mesh, layer):
        p = section(mesh, z)
        g = geoms(p)
        if not g:
            rows.append({"z": round(float(z), 3), "islands": 0, "area": 0.0})
            continue
        rows.append({
            "z": round(float(z), 3),
            "islands": len(g),
            "area": round(float(p.area), 2),
            "tiny": sum(1 for q in g if q.area < rules["min_island_mm2"]),
            "dropped": sum(1 for q in g if q.buffer(-min_bead / 2).is_empty),
            "single_bead": sum(1 for q in g if q.buffer(-bead / 2).is_empty),
            "thinnest": round(min(thinnest(q) for q in g), 2),
            "smallest": round(min(q.area for q in g), 2),
            "poly": p,
        })
    return rows


def check_mesh(name, mesh, rules, rep, role="main", fill=()):
    """`fill`: layer profiles of the companion parts (inlays) that share this
    body's frame; the overhang test is judged on the union, since the body
    alone reads every inlay pocket as empty (2026-10-11, 2026-10-15)."""
    rep.stage = f"mesh:{name}"
    lo, hi = mesh.bounds
    size = hi - lo
    bodies = mesh.split(only_watertight=False)
    rep.info("bbox", f"{size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f} mm, "
                     f"{len(bodies)} body/ies, {len(mesh.faces)} faces, "
                     f"{mesh.volume / 1000:.3f} cm3",
             size=[round(float(v), 3) for v in size], bodies=len(bodies),
             volume_mm3=round(float(mesh.volume), 3))
    if not mesh.is_watertight:
        rep.fail("watertight", "mesh is not watertight: the slicer will guess where the inside is")
    elif not mesh.is_winding_consistent:
        rep.fail("winding", "faces are wound inconsistently: normals flip, inside/outside ambiguous")
    elif mesh.volume <= 0:
        rep.fail("volume", f"volume {mesh.volume:.3f} <= 0: inside-out mesh")
    else:
        rep.ok("integrity", "watertight, consistently wound, positive volume")
    if abs(lo[2]) > 0.01:
        rep.warn("bed", f"lowest point at z={lo[2]:.3f}, not on the bed (the slicer will drop it; "
                        f"check that is the face you meant down)")

    layer = rules["layer"]
    n_layers = size[2] / layer
    if abs(n_layers - round(n_layers)) > 0.1:
        rep.warn("layers", f"height {size[2]:.2f} is {n_layers:.2f} layers of {layer}: the top "
                           f"layer is partial and the part prints a layer short or tall")
    if role == "main" and n_layers < rules["min_layers"] - 0.1:
        rep.fail("thin_part", f"only {n_layers:.1f} layers tall (< {rules['min_layers']}): a film "
                              f"this thin warps, peels and snaps")
    else:
        rep.info("layers", f"{n_layers:.1f} layers of {layer} mm")

    rows = layer_profile(mesh, rules)
    rep.stage = f"mesh:{name}"
    if not rows:
        rep.fail("empty", "no layer intersects the mesh")
        return rows
    areas = [r["area"] for r in rows]
    first, widest = areas[0], max(areas)
    contact = first / widest if widest else 0
    if role == "main":
        if contact < rules["min_contact_ratio"]:
            rep.warn("contact", f"first layer covers {first:.1f} mm2, {contact:.0%} of the widest "
                                f"layer ({widest:.1f}): little adhesion for what sits on it")
        else:
            rep.ok("contact", f"first layer {first:.1f} mm2 = {contact:.0%} of the widest layer")
    dropped = [(r["z"], r["dropped"]) for r in rows if r.get("dropped")]
    single = [(r["z"], r["single_bead"]) for r in rows if r.get("single_bead")]
    tiny = [(r["z"], r["tiny"]) for r in rows if r.get("tiny")]
    min_bead = rules["nozzle"] * rules["min_bead_pct"]
    if dropped:
        lvl = rep.fail if role == "inlay" else rep.warn
        lvl("dropped_features", f"islands thinner than {min_bead:.2f} mm (slicer drops them) on "
                                f"{len(dropped)} layer(s): {dropped[:6]}", layers=dropped)
    if single:
        rep.info("single_bead", f"islands thinner than one bead ({rules['bead']}) on {len(single)} "
                                f"layer(s): printed as one reduced-flow line: {single[:6]}")
    if tiny:
        rep.info("tiny_islands", f"islands under {rules['min_island_mm2']} mm2 on {len(tiny)} "
                                 f"layer(s): {tiny[:6]}")
    thin_rows = [r for r in rows if r["islands"]]
    worst = min(thin_rows, key=lambda r: r["thinnest"])
    rep.info("thinnest", f"thinnest feature {worst['thinnest']:.2f} mm at z={worst['z']}")
    if role == "inlay":
        if worst["thinnest"] < min_bead:
            rep.fail("inlay_stroke", f"inlay stroke {worst['thinnest']:.2f} mm < {min_bead:.2f}: "
                                     f"not printable on a {rules['nozzle']} nozzle")
        elif worst["thinnest"] < rules["nozzle"]:
            rep.warn("inlay_stroke", f"inlay stroke {worst['thinnest']:.2f} mm under the nozzle "
                                     f"({rules['nozzle']}): soft, reduced-flow lines")

    # Growth past a 45 deg flare prints into air. Companion parts fill the
    # pockets they sit in, so each layer is judged on the union.
    fill_at = {}
    for fr in fill:
        for r in fr:
            if r.get("poly") is not None:
                fill_at.setdefault(round(r["z"], 3), []).append(r["poly"])
    prev, worst_free, worst_z = None, 0.0, None
    for r in rows:
        p = r.get("poly")
        if p is None:
            prev = None
            continue
        extra = fill_at.get(round(r["z"], 3))
        if extra:
            p = unary_union([p, *extra])
        if prev is not None:
            free = p.difference(prev.buffer(layer + 1e-6)).area
            if free > worst_free:
                worst_free, worst_z = free, r["z"]
        prev = p
    if worst_free > rules["max_unsupported_mm2"]:
        rep.fail("overhang", f"{worst_free:.1f} mm2 unsupported at z={worst_z} "
                             f"(limit {rules['max_unsupported_mm2']}): needs supports or a new orientation")
    else:
        rep.ok("overhang", f"worst unsupported growth {worst_free:.1f} mm2"
                           + (f" at z={worst_z}" if worst_z else "")
                           + (" (body and companions together)" if fill_at else ""))

    # Wall thickness by ray casting from sampled surface points inward.
    if role == "main" and len(mesh.faces) < 200000:
        try:
            pts, fid = trimesh.sample.sample_surface(mesh, rules["thin_sample_points"])
            th = trimesh.proximity.thickness(mesh, pts, normals=mesh.face_normals[fid],
                                             method="ray")
            th = th[np.isfinite(th) & (th > 0)]
            if len(th):
                p05, med = float(np.percentile(th, 5)), float(np.median(th))
                msg = f"wall thickness p05 {p05:.2f} mm, median {med:.2f} mm (ray sample)"
                if p05 < 2 * layer:
                    rep.fail("thin_wall", msg + f": a single layer of skin over 5 % of the surface, "
                                               f"it tears or hinges there")
                elif p05 < rules["min_wall"]:
                    rep.warn("thin_wall", msg + f": under {rules['min_wall']} on 5 % of the surface")
                else:
                    rep.ok("thin_wall", msg)
        except Exception as exc:  # thickness is a nicety; never block on it
            rep.info("thin_wall", f"thickness sampling skipped: {exc}")
    for r in rows:
        r.pop("poly", None)
    return rows


def check_adhesion(parts, settings, rules, rep):
    """What could pull each body off the bed, measured in print orientation.

    Every body standing on the plate is judged on its own: a part next to a
    big neighbour must hold by itself. Bodies whose lowest point is above the
    plate rest on another part (an inlay) and are skipped.
    """
    rep.stage = "adhesion"
    meshes = [p["mesh"] for p in parts]
    if not meshes:
        return
    whole = trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]
    z0 = float(whole.bounds[0][2])
    layer, bead = rules["layer"], rules["bead"]
    st = settings or {}
    i3 = st.get("printer_structure") == "i3"
    brim = st.get("brim_type", "?")
    material = _lst(st.get("filament_type", [])) or "?"
    warp_len = rules["warp_length_mm"].get(str(material).upper())
    risky = False
    bodies = sorted([b for b in whole.split(only_watertight=False) if b.volume > 1.0],
                    key=lambda b: -b.volume)
    # First-layer footprints of every body, to tell an inlaid glyph (ringed
    # by its disc on the bed, held sideways) from a free-standing body: 93
    # letters of a plate read as "tips off the bed" before this (2026-10-15).
    first = [section(b, z0 + layer / 2) if b.bounds[0][2] <= z0 + layer / 2 else None for b in bodies]
    # Which object a body belongs to, for that object's own brim and speeds.
    def own_settings(b):
        c = b.bounds.mean(axis=0)
        for p in parts:
            lo_, hi_ = p["mesh"].bounds
            if np.all(c >= lo_ - 0.5) and np.all(c <= hi_ + 0.5):
                return {**st, **p.get("settings", {})}
        return st
    bare = []
    all_first = unary_union([f for f in first if f is not None and not f.is_empty])
    embedded = []
    for n, b in enumerate(bodies, start=1):
        if b.bounds[0][2] > z0 + layer / 2:
            continue
        name = f"body {n}"
        F = first[n - 1]
        if F is None or F.is_empty:
            rep.fail("footprint", f"{name}: nothing on the first layer")
            risky = True
            continue
        ring = F.buffer(bead).difference(F)
        others = all_first.difference(F.buffer(1e-3))
        if ring.area > 0 and ring.intersection(others).area >= 0.6 * ring.area:
            embedded.append(n)
            continue
        H = float(b.bounds[1][2] - z0)
        bst = own_settings(b)
        risky_before, risky = risky, False
        hull = F.convex_hull
        rect = hull.minimum_rotated_rectangle
        xs, ys = rect.exterior.coords.xy
        sides = sorted({round(math.dist((xs[i], ys[i]), (xs[i + 1], ys[i + 1])), 3) for i in range(4)})
        narrow, longest = sides[0], sides[-1]
        depth_y = hull.bounds[3] - hull.bounds[1]
        com = b.center_mass if b.is_watertight else b.vertices.mean(axis=0)
        inside = hull.contains(Point(com[0], com[1]))
        margin = hull.exterior.distance(Point(com[0], com[1])) * (1 if inside else -1)
        widest = max((q.area for z in np.arange(z0 + layer / 2, z0 + H, max(layer, H / 60))
                      for q in [section(b, z)] if q is not None), default=F.area)
        rep.info("footprint", f"{name}: {H:.1f} tall on {F.area:.0f} mm2 "
                              f"({len(geoms(F))} island/s), base {narrow:.0f} x {longest:.0f}, "
                              f"{F.area / widest:.0%} of its widest layer",
                 height=round(H, 2), area=round(float(F.area), 1), narrow=narrow, longest=longest)
        if margin < rules["min_com_margin"]:
            rep.fail("com", f"{name}: centre of mass {margin:+.1f} mm from the edge of its footprint: "
                            f"the nozzle's push tips it off the bed")
            risky = True
        aspect = H / narrow if narrow else float("inf")
        if aspect > rules["max_aspect_fail"]:
            rep.fail("aspect", f"{name}: {H:.0f} tall on a base {narrow:.1f} wide ({aspect:.1f}:1)")
            risky = True
        elif aspect > rules["max_aspect_warn"]:
            rep.warn("aspect", f"{name}: {H:.0f} tall on a base {narrow:.1f} wide ({aspect:.1f}:1): "
                               f"it rocks as it grows")
            risky = True
        if i3 and depth_y and H / depth_y > rules["max_aspect_bedslinger_warn"]:
            rep.warn("aspect_y", f"{name}: {H:.0f} tall on {depth_y:.1f} along Y, the axis the bed "
                                 f"moves on ({H / depth_y:.1f}:1): every bed reversal shakes it")
            risky = True
        if F.area / widest < rules["min_contact_ratio"]:
            rep.warn("contact_body", f"{name}: first layer {F.area:.0f} mm2 holds a body whose widest "
                                     f"layer is {widest:.0f} mm2 ({F.area / widest:.0%})")
            risky = True
        # Small islands carrying height, and strips one bead wide.
        for k, I in enumerate(geoms(F)):
            col = max((z - z0 for z in np.arange(z0 + layer / 2, z0 + H, max(layer, H / 60))
                       for q in [section(b, z)] if q is not None and q.intersects(I.buffer(0.5))),
                      default=layer)
            w = thinnest(I)
            # One island is the whole footprint, judged by `aspect`; with
            # several, each foot carries its own column.
            if len(geoms(F)) > 1 and w and col / w > rules["max_pillar_ratio"]:
                rep.warn("pillar", f"{name}: island {k + 1}, {I.area:.0f} mm2 and {w:.1f} wide, carries "
                                   f"{col:.0f} mm ({col / w:.0f}:1): it peels at that foot")
                risky = True
            if I.buffer(-bead / 2).is_empty and col > 4 * layer:
                rep.warn("strip", f"{name}: island {k + 1} touches the bed with a single bead "
                                  f"({I.area:.1f} mm2) and carries {col:.1f} mm")
                risky = True
        if warp_len and longest > warp_len:
            rep.warn("warp", f"{name}: {longest:.0f} mm long footprint in {material} "
                             f"(> {warp_len}): the corners lift as it cools")
            risky = True
        # A small footprint at the stock outer-wall speed: every plate of 19 mm
        # discs that came off (PLA or PETG, 2026-10-15) ran the outer wall at
        # 200 mm/s / 5000 mm/s2; the one that held ran 40 / 1000. The nozzle's
        # drag on a part that small is not negligible once the slow first
        # layers are over (they came off at layer 3-4).
        v_wall = _lst(bst.get("outer_wall_speed"))
        if v_wall and longest < rules["small_footprint_mm"]:
            try:
                v_wall = float(str(v_wall).rstrip("%"))
            except ValueError:
                v_wall = None
            if v_wall and v_wall > rules["max_small_part_wall_speed"]:
                rep.warn("small_part_speed", f"{name}: {longest:.0f} mm footprint with the outer wall at "
                                             f"{v_wall:.0f} mm/s (> {rules['max_small_part_wall_speed']:.0f}): "
                                             f"the nozzle's drag knocks a part this small off once the slow "
                                             f"first layers are over; 40 mm/s / 1000 mm/s2 held")
                risky = True
        if risky and bst.get("brim_type", brim) == "no_brim":
            bare.append(name)
        risky = risky or risky_before
    # The plate has to have a temperature for this filament at all.
    bed_key = {"Textured PEI Plate": "textured_plate_temp", "High Temp Plate": "hot_plate_temp",
               "Cool Plate": "cool_plate_temp", "Engineering Plate": "eng_plate_temp",
               "Cool Plate (SuperTack)": "supertack_plate_temp"}.get(st.get("curr_bed_type"), "")
    if bed_key and bed_key in st:
        temps = st[bed_key] if isinstance(st[bed_key], list) else [st[bed_key]]
        if any(str(t) in ("0", "") for t in temps):
            rep.fail("bed_plate", f"{st.get('curr_bed_type')} has no temperature for "
                                  f"{st.get('filament_type')}: that filament does not stick there")
        else:
            rep.ok("bed_plate", f"{material} on {st.get('curr_bed_type')} at {temps} C")
    if bare:
        rep.fail("brim", f"an adhesion risk above and brim_type=no_brim on {', '.join(bare)}")
    elif risky:
        rep.info("brim", f"brim_type={brim}: check the slicer actually adds one where it is needed")
    elif bodies:
        if embedded:
            rep.info("embedded", f"{len(embedded)} body/ies inlaid in a neighbour's first layer, held "
                                 f"sideways by it: not judged on their own")
        rep.ok("adhesion", f"every body stands on its footprint (brim {brim}"
                           f"{', bed slinger' if i3 else ''})")


def gyration(poly):
    """(area, least radius of gyration, radius for bending along Y) of a section.

    From the second moments of every ring about the section's centroid, holes
    subtracted: a closed box of thin walls is stiff, a strip of the same
    thinness is not, and only the moments tell them apart.
    """
    from shapely.geometry.polygon import orient
    A = Sx = Sy = Ixx = Iyy = Ixy = 0.0
    for g in geoms(poly):
        g = orient(g, 1.0)
        for ring in [g.exterior, *g.interiors]:
            x, y = np.asarray(ring.coords.xy)
            x0, y0, x1, y1 = x[:-1], y[:-1], x[1:], y[1:]
            c = x0 * y1 - x1 * y0
            A += c.sum() / 2
            Sx += ((x0 + x1) * c).sum() / 6
            Sy += ((y0 + y1) * c).sum() / 6
            Iyy += ((x0 ** 2 + x0 * x1 + x1 ** 2) * c).sum() / 12
            Ixx += ((y0 ** 2 + y0 * y1 + y1 ** 2) * c).sum() / 12
            Ixy += ((x0 * y1 + 2 * x0 * y0 + 2 * x1 * y1 + x1 * y0) * c).sum() / 24
    if A <= 0:
        return 0.0, 0.0, 0.0
    cx, cy = Sx / A, Sy / A
    Ixx, Iyy, Ixy = Ixx - A * cy ** 2, Iyy - A * cx ** 2, Ixy - A * cx * cy
    i_min = (Ixx + Iyy) / 2 - math.hypot((Ixx - Iyy) / 2, Ixy)
    return A, math.sqrt(max(i_min, 0.0) / A), math.sqrt(max(Ixx, 0.0) / A)


def check_members(parts, settings, rules, rep):
    """Members that stand on their own while they print, judged by slenderness.

    A body can sit on a wide, well-stuck base and still grow, above it, as
    several thin members that nothing ties together until near the top. Each
    one is a cantilever from where it left the rest: the nozzle's drag and,
    on a bed slinger, every reversal of the bed bend it, and at the top it
    whips into the nozzle. The bastidor of cajonera_lijas (taller,
    2026-10-15) passed every footprint rule, 90 tall on a base 56 wide, and
    came off the bed at 90 %: above a 5 mm plate it was eight sticks 2 x 10
    mm, free for 80 mm.

    Members are followed section by section: an island that continues one
    island below, and is that island's only continuation, keeps its free
    height growing; a split or a merge starts a new member. Slenderness is
    free height over the least radius of gyration of the member's weakest
    section, the measure a column's buckling and a cantilever's sway both
    scale with.
    """
    rep.stage = "members"
    meshes = [p["mesh"] for p in parts]
    if not meshes:
        return
    whole = trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]
    z0 = float(whole.bounds[0][2])
    layer, step = rules["layer"], max(rules["layer"], rules["member_step"])
    st = settings or {}
    i3 = st.get("printer_structure", "i3") == "i3"
    worst_all = []
    bodies = sorted([b for b in whole.split(only_watertight=False) if b.volume > 1.0],
                    key=lambda b: -b.volume)
    for n, b in enumerate(bodies, start=1):
        if b.bounds[0][2] > z0 + layer / 2:
            continue
        name = f"body {n}"
        prev, worst = [], None
        for z in np.arange(z0 + layer / 2, float(b.bounds[1][2]), step):
            cur = [g for g in geoms(section(b, z)) if g.area > rules["min_island_mm2"]]
            nxt = []
            for I in cur:
                A, r, r_y = gyration(I)
                below = [m for m in prev if m["poly"].intersects(I)]
                one_to_one = (len(below) == 1 and
                              sum(1 for J in cur if J.intersects(below[0]["poly"])) == 1)
                if one_to_one:
                    m = below[0]
                    m = {"poly": I, "base": m["base"], "r": min(m["r"], r),
                         "r_y": min(m["r_y"], r_y), "area": min(m["area"], A),
                         "siblings": m["siblings"]}
                else:
                    m = {"poly": I, "base": z, "r": r, "r_y": r_y, "area": A,
                         "siblings": len(cur)}
                L = z - m["base"] + step / 2
                lam = L / m["r"] if m["r"] else float("inf")
                if worst is None or lam > worst["lam"]:
                    c = I.centroid
                    worst = {"lam": lam, "L": L, "r": m["r"], "r_y": m["r_y"], "area": m["area"],
                             "from": m["base"] - z0, "to": z - z0, "x": c.x, "y": c.y,
                             "siblings": m["siblings"]}
                nxt.append(m)
            prev = nxt
        if worst is None:
            continue
        worst["name"] = name
        worst_all.append(worst)
    # Every printed piece that held so far (spiches, wincher, kailua, taller,
    # 28 files on 2026-10-15) is under 30; the bastidor that came off was 94.
    for w in worst_all:
        where = (f"{w['name']}: a member {w['area']:.0f} mm2 at ({w['x']:.0f}, {w['y']:.0f}) "
                 f"stands free from z={w['from']:.0f} to {w['to']:.0f} ({w['L']:.0f} mm), least "
                 f"radius of gyration {w['r']:.2f} mm: slenderness {w['lam']:.0f}")
        data = {k: (round(float(v), 2) if not isinstance(v, str) else v) for k, v in w.items()}
        if w["lam"] > rules["max_slenderness_fail"]:
            rep.fail("slender", where + f" (> {rules['max_slenderness_fail']:.0f}): it sways under "
                     f"the nozzle and is levered off the bed; tie the members together or print "
                     f"it another way" + (" (the bed moves in Y and shakes it every reversal)"
                                          if i3 else ""), **data)
        elif w["lam"] > rules["max_slenderness_warn"]:
            rep.warn("slender", where + f" (> {rules['max_slenderness_warn']:.0f}): it sways as "
                                        f"it grows; tie it or slow the top", **data)
    ok = [w for w in worst_all if w["lam"] <= rules["max_slenderness_warn"]]
    if ok:
        w = max(ok, key=lambda w: w["lam"])
        rep.ok("slender", f"no free-standing member past slenderness "
                          f"{rules['max_slenderness_warn']:.0f} in {len(ok)} body/ies "
                          f"(worst {w['lam']:.0f}, {w['name']}, {w['L']:.0f} mm free)")
    return worst_all


def check_inlay(main_name, main, inlay_name, inlay, rules, rep):
    """A second colour printed inside the first: what holds it, what prints."""
    rep.stage = f"inlay:{inlay_name}"
    layer = rules["layer"]
    m_lo, m_hi = main.bounds[0][2], main.bounds[1][2]
    i_lo, i_hi = inlay.bounds[0][2], inlay.bounds[1][2]
    if i_hi > m_hi + 0.01:
        rep.warn("inlay_proud", f"inlay top z={i_hi:.2f} stands above the body ({m_hi:.2f})")
    if i_lo < m_lo - 0.01:
        rep.warn("inlay_below", f"inlay bottom z={i_lo:.2f} is under the body ({m_lo:.2f})")
    flush = abs(i_lo - m_lo) < 0.01
    rep.info("inlay_face", f"inlay from z={i_lo:.2f} to {i_hi:.2f}, "
                           f"{'flush with the bed face' if flush else 'not on the bed face'}; "
                           f"{(i_hi - i_lo) / layer:.1f} layers deep")
    # What holds the two colours together is the body directly over the inlay,
    # so count the layers above it that still cover the inlay's footprint. A
    # hollow back has material at the rim long after the centre has run out:
    # counting any body material there read 8 layers on a part that had one.
    foot = unary_union([s for s in (section(inlay, z) for z in layer_zs(inlay, layer)) if s is not None])
    n = 0
    for z in layer_zs(main, layer):
        if z < i_hi:
            continue
        s = section(main, z)
        if s is None or foot.is_empty or s.intersection(foot).area < 0.95 * foot.area:
            break
        n += 1
    if n < rules["min_base_layers_over_inlay"]:
        rep.fail("inlay_backing", f"only {n} layer(s) ({n * layer:.1f} mm) of body cover the whole inlay "
                                  f"from above (< {rules['min_base_layers_over_inlay']}): two colours held "
                                  f"by one skin, the part hinges and comes apart there")
    else:
        rep.ok("inlay_backing", f"{n} layers ({n * layer:.1f} mm) of body cover the whole inlay from above")
    # Inlay never shares volume with the body: the slicer resolves overlaps by part order.
    try:
        inter = main.intersection(inlay, engine="manifold")
        over = float(inter.volume) if inter is not None and not inter.is_empty else 0.0
        if over > 0.05:
            rep.warn("inlay_overlap", f"inlay and body overlap by {over:.2f} mm3: one wins per layer")
        else:
            rep.ok("inlay_overlap", f"inlay and body overlap {over:.3f} mm3")
    except Exception as exc:
        rep.info("inlay_overlap", f"boolean skipped: {exc}")
    # Counters: body islands inside the inlay's footprint that are small.
    p_in = section(inlay, i_lo + layer / 2)
    p_main = section(main, i_lo + layer / 2)
    if p_in is not None and p_main is not None:
        hull = p_in.convex_hull
        counters = [g for g in geoms(p_main) if hull.contains(g.representative_point())
                    and g.area < rules["min_island_mm2"]]
        if counters:
            rep.info("counters", f"{len(counters)} body island(s) under {rules['min_island_mm2']} mm2 "
                                 f"inside the lettering (counters of A, O, B...): the slicer may drop "
                                 f"them and the body shows through from the layer above")


# ----------------------------------------------------------------- 3MF

def _xml_attr(tag, name):
    m = re.search(rf'\b{name}="([^"]*)"', tag)
    return m.group(1) if m else None


def _parse_meshes(xml):
    """{object id: Trimesh} from a 3MF model file; no lxml needed."""
    out = {}
    for m in re.finditer(r'<object\s[^>]*?\bid="(\d+)"[^>]*>(.*?)</object>', xml, re.S):
        oid, body = m.group(1), m.group(2)
        v = re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', body)
        t = re.findall(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"', body)
        if v and t:
            out[oid] = trimesh.Trimesh(np.array(v, dtype=float), np.array(t, dtype=int),
                                       process=True)
    return out


def _transform(s):
    """3MF transform: 9 matrix values then a translation; row-vector convention."""
    v = [float(x) for x in s.split()]
    M = np.array(v[:9]).reshape(3, 3)
    T = np.array(v[9:12])
    return M, T


def load_project(path):
    """Settings, parts (name, extruder, mesh in the object's frame) and the
    plate files of a Bambu project, sliced or not."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        read = lambda n: z.read(n).decode(errors="replace")
        settings = json.loads(read("Metadata/project_settings.config")) \
            if "Metadata/project_settings.config" in names else {}
        model_cfg = read("Metadata/model_settings.config") \
            if "Metadata/model_settings.config" in names else ""
        root = read("3D/3dmodel.model")
        meshes = {}
        for n in names:
            if n.startswith("3D/Objects/") and n.endswith(".model"):
                meshes[n] = _parse_meshes(read(n))
        meshes["3D/3dmodel.model"] = _parse_meshes(root)
        gcode = read("Metadata/plate_1.gcode") if "Metadata/plate_1.gcode" in names else None
        slice_info = read("Metadata/slice_info.config") \
            if "Metadata/slice_info.config" in names else ""
        plate = json.loads(read("Metadata/plate_1.json")) if "Metadata/plate_1.json" in names else {}
    # Part id -> extruder and name, from model_settings.config.
    parts_meta = {}
    for pm in re.finditer(r'<part id="(\d+)"[^>]*>(.*?)</part>', model_cfg, re.S):
        body = pm.group(2)
        parts_meta[pm.group(1)] = {
            "name": _xml_attr(re.search(r'<metadata key="name"[^>]*>', body).group(0), "value")
            if re.search(r'<metadata key="name"[^>]*>', body) else f"part {pm.group(1)}",
            "extruder": int(_xml_attr(re.search(r'<metadata key="extruder"[^>]*>', body).group(0),
                                      "value") or 1)
            if re.search(r'<metadata key="extruder"[^>]*>', body) else 1,
        }
    # An object's own settings (a brim on one piece, none on its neighbour)
    # sit on its <object> block over the project's; its parts inherit them.
    for om in re.finditer(r'<object id="\d+">(.*?)</object>', model_cfg, re.S):
        head = om.group(1).split("<part", 1)[0]
        own = {_xml_attr(m, "key"): _xml_attr(m, "value")
               for m in re.findall(r'<metadata key="[^"]+" value="[^"]*"\s*/>', head)}
        own.pop("name", None)
        for pid in re.findall(r'<part id="(\d+)"', om.group(1)):
            if pid in parts_meta:
                parts_meta[pid]["settings"] = own
    # Where the plate puts each object: the build items. Without them the
    # meshes sit where the object file has them (centred), not where the
    # nozzle will print them, and nothing about zones or the G-code matches.
    items = {}
    for it in re.findall(r'<item [^>]*>', root):
        oid, tr = _xml_attr(it, "objectid"), _xml_attr(it, "transform")
        if oid and tr:
            items[oid] = _transform(tr)
    parts = []
    comps = re.findall(r'<component [^>]*>', root)
    if comps:
        objs = {m.group(1): m.group(0) + m.group(2) for m in
                re.finditer(r'<object\s[^>]*?\bid="(\d+)"[^>]*>(.*?)</object>', root, re.S)}
        place = {}
        for oid, body in objs.items():
            for c in re.findall(r'<component [^>]*>', body):
                place[_xml_attr(c, "objectid")] = items.get(oid)
        for c in comps:
            ppath = (_xml_attr(c, "p:path") or "").lstrip("/")
            oid = _xml_attr(c, "objectid")
            mesh = meshes.get(ppath, {}).get(oid) or meshes["3D/3dmodel.model"].get(oid)
            if mesh is None:
                continue
            mesh = mesh.copy()
            tr = _xml_attr(c, "transform")
            if tr:
                M, T = _transform(tr)
                mesh.vertices = mesh.vertices @ M + T
            if place.get(oid):
                M, T = place[oid]
                mesh.vertices = mesh.vertices @ M + T
            meta = parts_meta.get(oid, {"name": f"object {oid}", "extruder": 1})
            parts.append({"name": meta["name"], "extruder": meta["extruder"], "mesh": mesh,
                          "settings": meta.get("settings", {})})
    else:
        for oid, mesh in meshes["3D/3dmodel.model"].items():
            mesh = mesh.copy()
            if oid in items:
                M, T = items[oid]
                mesh.vertices = mesh.vertices @ M + T
            meta = parts_meta.get(oid, {"name": f"object {oid}", "extruder": 1})
            parts.append({"name": meta["name"], "extruder": meta["extruder"], "mesh": mesh,
                          "settings": meta.get("settings", {})})
    return {"settings": settings, "parts": parts, "gcode": gcode,
            "slice_info": slice_info, "plate": plate}


def _lst(v, i=0):
    """Bambu stores per-filament settings as lists of strings."""
    if isinstance(v, list):
        return v[i] if i < len(v) else v[-1] if v else None
    return v


def check_settings(st, used_extruders, rules, rep):
    rep.stage = "settings"
    if not st:
        rep.warn("settings", "no project_settings.config: nothing known about the slicing")
        return
    used = sorted(used_extruders) or [1]
    types = [_lst(st.get("filament_type", []), e - 1) for e in used]
    names = [_lst(st.get("filament_settings_id", []), e - 1) for e in used]
    rep.info("presets", f"printer {st.get('printer_settings_id')}; process {st.get('print_settings_id')}; "
                        f"filaments {dict(zip(used, names))}")
    if rules["same_material"] and len(set(types)) > 1:
        rep.fail("mixed_materials", f"filaments are {dict(zip(used, types))}: different materials "
                                    f"barely bond, the two colours come apart")
    else:
        rep.ok("materials", f"filament types {dict(zip(used, types))}")
    bed_key = {"Textured PEI Plate": "textured_plate_temp", "High Temp Plate": "hot_plate_temp",
               "Cool Plate": "cool_plate_temp", "Engineering Plate": "eng_plate_temp",
               "Cool Plate (SuperTack)": "supertack_plate_temp"}.get(st.get("curr_bed_type"), "")
    if bed_key and bed_key in st:
        beds = [_lst(st[bed_key], e - 1) for e in used]
        beds0 = [_lst(st.get(bed_key + "_initial_layer", st[bed_key]), e - 1) for e in used]
        if len(set(beds)) > 1 or len(set(beds0)) > 1:
            rep.warn("bed_temp", f"bed temps differ per filament {dict(zip(used, beds))}: Bambu takes "
                                 f"the first filament printed, the other runs at a bed it was not tuned for")
        else:
            rep.info("bed_temp", f"bed {beds[0]} C on {st.get('curr_bed_type')}")
    noz = [(_lst(st.get("nozzle_temperature_initial_layer", []), e - 1),
            _lst(st.get("nozzle_temperature", []), e - 1)) for e in used]
    rep.info("nozzle_temp", f"nozzle temps (first, rest) {dict(zip(used, noz))}")
    lh = float(st.get("layer_height", rules["layer"]))
    if abs(lh - rules["layer"]) > 1e-6:
        rep.info("layer_height", f"process layer height {lh} differs from the rules' {rules['layer']}; "
                                 f"rerun with --layer {lh} for layer-based checks")
    rep.info("shells", f"walls {st.get('wall_loops')}, bottom shells {st.get('bottom_shell_layers')}, "
                       f"top shells {st.get('top_shell_layers')}, infill {st.get('sparse_infill_density')}, "
                       f"generator {st.get('wall_generator')}")
    # Dense "sparse" infill over-stuffs: at 100 % a zig-zag laid at infill
    # speed piles up in the rim rings and the nozzle snags the part off the
    # bed. Seven PLA discs came off twice, both times at the layer where that
    # infill began (2026-10-15). Solid wants shells, not a 100 % infill.
    dens = re.sub(r"[^\d.]", "", str(st.get("sparse_infill_density", "") or ""))
    if dens and float(dens) >= rules.get("max_sparse_infill_pct", 50):
        rep.warn("dense_infill", f"sparse infill at {dens}% ({st.get('sparse_infill_pattern')}): over-stuffed "
                                 f"layers the nozzle can snag on; shells make a solid part more safely")
    if st.get("wall_generator") == "arachne":
        rep.info("arachne", f"min_feature_size {st.get('min_feature_size')}, min_bead_width "
                            f"{st.get('min_bead_width')} of the nozzle; detect_thin_wall="
                            f"{st.get('detect_thin_wall')}")
    elif str(st.get("detect_thin_wall")) == "0":
        rep.warn("thin_walls", "classic walls with detect_thin_wall=0: features under two beads are dropped")
    rep.info("first_layer", f"first layer {st.get('initial_layer_print_height')} mm at "
                            f"{_lst(st.get('initial_layer_speed'))} mm/s, line {st.get('initial_layer_line_width')}, "
                            f"elephant foot comp {st.get('elefant_foot_compensation')}, brim {st.get('brim_type')}")
    rep.info("cooling", f"fan min/max {[_lst(st.get('fan_min_speed'), e - 1) for e in used]}/"
                        f"{[_lst(st.get('fan_max_speed'), e - 1) for e in used]}, fan off first "
                        f"{[_lst(st.get('close_fan_the_first_x_layers'), e - 1) for e in used]} layer(s), "
                        f"slow_down_layer_time {[_lst(st.get('slow_down_layer_time'), e - 1) for e in used]} s, "
                        f"slow_down_min_speed {[_lst(st.get('slow_down_min_speed'), e - 1) for e in used]} mm/s")
    rep.info("support", f"support {st.get('enable_support')} ({st.get('support_type')}), prime tower "
                        f"{st.get('enable_prime_tower')}, flush into objects {st.get('flush_into_objects')}")
    diffs = st.get("different_settings_to_system")
    if isinstance(diffs, list):
        listed = [d for d in diffs if d]
        rep.info("overrides", f"declared changes from the system presets: {listed or 'none'}")


def check_slice_info(xml, plate, rep):
    rep.stage = "slice_info"
    if not xml:
        return
    for w in re.finditer(r'<warning msg="([^"]*)" level="(\d+)" error_code ?="([^"]*)"', xml):
        msg, level, code = w.groups()
        (rep.warn if int(level) <= 1 else rep.info)("slicer_warning",
                                                     f"slicer warning level {level} {code}: {msg}")
    out = re.search(r'key="outside" value="(\w+)"', xml)
    if out and out.group(1) == "true":
        rep.fail("outside", "the slicer reports the object outside the printable area")
    sup = re.search(r'key="support_used" value="(\w+)"', xml)
    if sup:
        rep.info("support_used", f"support used: {sup.group(1)}")
    for lf in re.finditer(r'<layer_filament_list filament_list="([^"]*)" layer_ranges="([^"]*)"', xml):
        rep.info("layer_filaments", f"filaments [{lf.group(1)}] on layers {lf.group(2)}")
    if plate.get("bbox_objects"):
        for o in plate["bbox_objects"]:
            rep.info("first_layer_area", f"{o.get('name')}: first-layer contact {o.get('area', 0):.1f} mm2 "
                                         f"(slicer)")


# ----------------------------------------------------------------- G-code

def check_gcode(text, rules, rep, settings=None):
    rep.stage = "gcode"
    # Header lines are "; key: value", some with a space before the colon and
    # two on one line separated by ";".
    hdr = {}
    for line in text.split("; HEADER_BLOCK_END")[0].splitlines():
        for piece in line.lstrip("; ").split(";"):
            if ":" in piece:
                k, v = piece.split(":", 1)
                hdr[k.strip()] = v.strip()
    cfg = dict(re.findall(r"^; (\w+) = (.*)$", text, re.M))
    layer = rules["layer"]
    dens = [float(x) for x in cfg.get("filament_density", "1.25").split(",")]
    area = math.pi * FIL_RADIUS ** 2
    layers = collections.OrderedDict()
    tool, feat, z, f, x, y = 0, "", None, 0.0, None, None
    changing, cur, changes = False, None, 0
    # Time with acceleration: every segment is a trapezoid (or a triangle
    # when it cannot reach its feed) between junctions; a junction that
    # turns more than JUNCTION_DEG is a stop, a gentler one carries the
    # speed through. Feed rates alone read the frame's lattice at 5.4 s a
    # layer where the slicer said 12-18 (2026-10-15).
    acc = float(cfg.get("default_acceleration", "6000").split(",")[0] or 6000)
    pending = None            # (layer, d, v, start_stop, (dx, dy))

    def settle(end_stop):
        nonlocal pending
        if pending is None:
            return
        L, d, v, start_stop, _ = pending
        pending = None
        if v <= 0 or d <= 0:
            return
        stops = start_stop + end_stop
        reach = v * v / acc * (stops / 2.0)          # distance to speed up and down
        if stops == 0 or d >= reach:
            L["time"] += d / v + stops * v / (2 * acc)
        else:
            L["time"] += math.sqrt(2 * d * stops / acc)
    second = {}
    tower, part = [], []
    bed = nozzle_t = None
    ironing = 0
    first_xy = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("; Z_HEIGHT:"):
            settle(1)
            z = round(float(line.split(":")[1]), 3)
            cur = layers.setdefault(z, {"vol": collections.defaultdict(float), "time": 0.0,
                                        "min_f": collections.defaultdict(lambda: 1e9),
                                        "changes": 0, "tools": set(), "acc_y": None})
            continue
        if line.startswith("M201 N1 Y") and cur is not None:
            # the mass-limited Y acceleration the firmware is told per layer
            cur["acc_y"] = float(line.split("Y")[1].split()[0])
            continue
        if line.startswith("M1031"):
            ironing += 1
        if line.startswith("M204 S"):
            settle(1)
            acc = float(line.split("S")[1].split()[0])
            continue
        if line.startswith("; FEATURE:"):
            feat = line.split(":", 1)[1].strip()
            continue
        if line.startswith("; LINE_WIDTH:"):
            width = float(line.split(":")[1])
            continue
        if line.startswith("M620 S") and "A" in line:
            changing = True
            changes += 1
            if cur:
                cur["changes"] += 1
        elif line.startswith("M621"):
            changing = False
        m = re.match(r"^T(\d+)\s*$", line)
        if m and int(m.group(1)) < 16:
            tool = int(m.group(1))
        if bed is None and line.startswith("M190"):
            bed = float(re.search(r"S([\d.]+)", line).group(1))
        if nozzle_t is None and line.startswith("M109"):
            nozzle_t = float(re.search(r"S([\d.]+)", line).group(1))
        if not re.match(r"G[0123] ", line):
            continue
        nf = re.search(r"F([\d.]+)", line)
        if nf:
            f = float(nf.group(1))
        nx, ny, ne = (re.search(k + r"(-?[\d.]+)", line) for k in "XYE")
        px, py = x, y
        if nx:
            x = float(nx.group(1))
        if ny:
            y = float(ny.group(1))
        if cur is None or changing or feat in ("Custom", "Flush"):
            continue
        d = math.hypot(x - px, y - py) if (px is not None and (nx or ny)) else 0.0
        if f > 0 and d > 0:
            u = ((x - px) / d, (y - py) / d)
            if pending is None:
                start_stop = 1
            else:
                pu = pending[4]
                cos = pu[0] * u[0] + pu[1] * u[1]
                turn = cos < JUNCTION_COS or abs(pending[2] - f / 60) > 1e-6
                settle(1 if turn else 0)
                start_stop = 1 if turn else 0
            pending = (cur, d, f / 60, start_stop, u)
        if ne and float(ne.group(1)) > 0 and d > 0:
            e = float(ne.group(1))
            cur["vol"][(tool, feat)] += e * area
            cur["min_f"][(tool, feat)] = min(cur["min_f"][(tool, feat)], f / 60)
            cur["tools"].add(tool)
            (tower if feat == "Prime tower" else part).append((x, y))
            if len(layers) == 1 and feat != "Prime tower":
                first_xy.append((x, y))
            # The tower takes whichever colour just loaded: not the inlay's
            # footprint (it read a plate as 68 % covered, 2026-10-15).
            if tool == 1 and px is not None and feat != "Prime tower":
                second.setdefault(z, []).append(((px, py), (x, y)))
    settle(1)
    if not layers:
        rep.fail("gcode", "no layers found: not a Bambu/Orca-style G-code (no ; Z_HEIGHT markers)")
        return
    n = len(layers)
    minutes = hdr.get("model printing time", "?")
    rep.info("header", f"{n} layers to z={max(layers):.2f}; {minutes}; total {hdr.get('total estimated time', '?')}; "
                       f"filament g {hdr.get('total filament weight [g]', '?')}")
    rep.info("temps", f"first bed M190 {bed}, first nozzle M109 {nozzle_t}")
    # Per-filament volume actually laid in the part, against what is billed.
    by_tool = collections.defaultdict(float)
    for L in layers.values():
        for (t, _), v in L["vol"].items():
            by_tool[t] += v
    billed = [float(v) for v in hdr.get("total filament weight [g]", "").split(",") if v.strip()]
    for t, v in sorted(by_tool.items()):
        g = v * (dens[t] if t < len(dens) else dens[0]) / 1000
        b = billed[t] if t < len(billed) else None
        purge = (b - g) if b is not None else None
        msg = f"T{t}: {v:.0f} mm3 = {g:.2f} g in the part" + (f", billed {b:.2f} g, purge ~{purge:.2f} g" if b is not None else "")
        if purge is not None and g > 0 and purge / g > rules["max_purge_ratio"]:
            rep.warn("purge", msg + f" ({purge / g:.1f}x the part: fewer colour changes, or more copies per plate)")
        else:
            rep.info("usage", msg)
    rep.info("changes", f"{changes} filament change(s); layers with a change: "
                        f"{[z for z, L in layers.items() if L['changes']]}")
    # Layer time: the deformation lever. Trapezoids between junctions, see
    # `settle`; within ~20 % of the slicer's own M73 estimate (2026-10-19).
    times = [(z, L["time"]) for z, L in layers.items()]
    short = [(z, round(t, 1)) for z, t in times if t < rules["min_layer_time_s"]]
    med = float(np.median([t for _, t in times]))
    if short and med < rules["min_layer_time_s"]:
        rep.fail("layer_time", f"median layer {med:.1f} s, {len(short)}/{n} layers under "
                               f"{rules['min_layer_time_s']} s: each layer goes down on one still soft, "
                               f"the part deforms (more copies on the plate, or a slow-down floor)")
    elif short:
        rep.warn("layer_time", f"{len(short)}/{n} layers under {rules['min_layer_time_s']} s "
                               f"(median {med:.1f} s): {short[:8]}")
    else:
        rep.ok("layer_time", f"layer time min {min(t for _, t in times):.1f} s, median {med:.1f} s")
    # Outer walls crawling means the slicer is stretching a too-fast layer.
    slow = [(z, round(L["min_f"][(t, "Outer wall")], 1)) for z, L in layers.items()
            for t in L["tools"] if L["min_f"][(t, "Outer wall")] < rules["min_outer_wall_speed"]]
    if slow:
        rep.warn("outer_wall_speed", f"outer wall under {rules['min_outer_wall_speed']} mm/s on "
                                     f"{len(slow)} layer(s): {slow[:8]} (hot plastic dragged slowly rounds "
                                     f"off corners and ovalises circles)")
    no_wall = [z for z, L in layers.items()
               if L["vol"] and not any(ft == "Outer wall" for _, ft in L["vol"])]
    if no_wall:
        rep.warn("no_outer_wall", f"{len(no_wall)} layer(s) with extrusion but no outer wall: {no_wall[:8]}")
    thin_layers = [(z, round(sum(L["vol"].values()), 1)) for z, L in layers.items()
                   if 0 < sum(L["vol"].values()) < 1.0]
    if thin_layers:
        rep.info("sparse_layers", f"{len(thin_layers)} layer(s) under 1 mm3: {thin_layers[:8]}")
    feats = sorted({ft for L in layers.values() for _, ft in L["vol"]})
    rep.info("features", f"feature types: {feats}")
    if "Sparse infill" in feats and n <= 8:
        rep.info("infill_in_thin_part", "sparse infill inside a part this thin: consider all-solid "
                                        "(bottom/top shells >= layers)")
    # Second colour: where it is, and how whole its first layer is.
    if second:
        zs = sorted(second)
        w = rules["bead"]
        first = unary_union([LineString([a, b]).buffer(w / 2, cap_style=2) for a, b in second[zs[0]] if a != b])
        every = unary_union([LineString([a, b]).buffer(w / 2, cap_style=2) for k in zs for a, b in second[k] if a != b])
        share = first.area / every.area if every.area else 0
        rep.info("second_colour", f"second colour on {len(zs)} layer(s) z {zs[0]}-{zs[-1]}; its first layer "
                                  f"covers {share:.0%} of its footprint")
        if zs[0] > layer * 1.5:
            rep.warn("second_colour_face", f"second colour starts at z={zs[0]}, not on the bed face")
    if tower and part:
        from scipy.spatial import cKDTree
        gap = float(cKDTree(np.array(part)).query(np.array(tower))[0].min())
        (rep.warn if gap < 5 else rep.info)("tower_gap", f"prime tower {gap:.1f} mm from the part")
    # The tower is printed only up to the last layer with a colour change;
    # on a job whose second colour ends at layer 2 it is a 0.4 mm film. It
    # came off the bed and wrapped the nozzle twice (2026-10-14, 2026-10-15).
    tower_layers = [z for z, L in layers.items() if any(ft == "Prime tower" for _, ft in L["vol"])]
    if tower_layers:
        th = max(tower_layers)
        if th < rules["min_tower_height_mm"]:
            rep.fail("tower_height", f"prime tower only {th:.1f} mm tall ({len(tower_layers)} layer(s)): a loose "
                                     f"film the nozzle drags off; turn the tower off (the A2L purges in its "
                                     f"chute) or make the colour job taller")
        else:
            rep.info("tower_height", f"prime tower {th:.1f} mm tall ({len(tower_layers)} layers)")
    # What the slicer actually laid as brim. auto_brim judged a 19 mm disc
    # stable and laid nothing, while the settings said "brim" (2026-10-15).
    brim_vol = sum(v for L in layers.values() for (_, ft), v in L["vol"].items() if ft == "Brim")
    brim_type = (settings or {}).get("brim_type", cfg.get("brim_type", "?"))
    if first_xy:
        xs_, ys_ = np.array(first_xy).T
        foot_min = float(min(xs_.max() - xs_.min(), ys_.max() - ys_.min()))
    else:
        foot_min = None
    if brim_vol > 0:
        rep.info("brim_laid", f"brim laid: {brim_vol:.0f} mm3 ({brim_type})")
    elif brim_type != "no_brim":
        msg = f"brim_type={brim_type} but no Brim feature in the G-code: nothing holds the rim"
        if foot_min is not None and foot_min < rules["small_footprint_mm"]:
            rep.warn("brim_laid", msg + f"; the footprint is {foot_min:.0f} mm across, the nozzle's drag "
                                        f"on it is not negligible (set outer_only, 5 mm)")
        else:
            rep.info("brim_laid", msg + " (the slicer judged the footprint stable)")
    if ironing:
        rep.fail("ironing", f"{ironing} M1031 ironing toggles: ironing on the A2L under-extrudes and clicks "
                            f"the extruder (Bambu Studio issue 11690); turn ironing off")
    # Printable area from the config block, when there is one.
    pa = cfg.get("printable_area")
    if pa and part:
        xs = [float(p.split("x")[0]) for p in pa.split(",")]
        ys = [float(p.split("x")[1]) for p in pa.split(",")]
        px_, py_ = np.array(part).T
        if px_.min() < min(xs) or px_.max() > max(xs) or py_.min() < min(ys) or py_.max() > max(ys):
            rep.fail("outside", f"extrusion outside the printable area {min(xs)}-{max(xs)} x {min(ys)}-{max(ys)}")
    rep.layers = [{"z": z, "time_s": round(L["time"], 1), "changes": L["changes"], "acc_y": L["acc_y"],
                   "mm3": {f"T{t}/{ft}": round(v, 1) for (t, ft), v in L["vol"].items()}}
                  for z, L in layers.items()]


# ----------------------------------------------------------------- drivers

def check_simulation(parts, settings, rules, rep):
    """The layer-by-layer simulation (simulate.py); imported here so the
    engine stays one entry point for every path."""
    if __package__:
        from .simulate import check_simulation as _cs
    else:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from printcheck.simulate import check_simulation as _cs
    return _cs(parts, settings, rules, rep)


def format_ledger(sim, **kw):
    if __package__:
        from .simulate import format_ledger as _fl
    else:
        from printcheck.simulate import format_ledger as _fl
    return _fl(sim, **kw)


def slice_project(path):
    """Slice with Bambu Studio's CLI into a temp .gcode.3mf; None if unavailable."""
    if not os.path.exists(BAMBU):
        return None, "Bambu Studio not found at " + BAMBU
    tmp = tempfile.mkdtemp(prefix="preflight-")
    out = os.path.join(tmp, os.path.basename(path).removesuffix(".3mf") + ".gcode.3mf")
    r = subprocess.run([BAMBU, "--slice", "1", "--export-3mf", out, os.path.abspath(path)],
                       capture_output=True, text=True, cwd=tmp)
    if r.returncode != 0 or not os.path.exists(out):
        return None, f"slicing failed ({r.returncode}): {(r.stdout + r.stderr)[-400:]}"
    return out, None


def groups(parts):
    """Parts that share floor space belong together, a body and the inlays
    inside it; the separate objects of a plate are each a group of their own.
    Judged as one, the plate summed two cabezals' 169 mm2 roofs into one
    338 mm2 FAIL (2026-10-17)."""
    boxes = [p["mesh"].bounds for p in parts]
    owner = list(range(len(parts)))

    def root(i):
        while owner[i] != i:
            i = owner[i]
        return i
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            (a0, a1), (b0, b1) = boxes[i], boxes[j]
            if np.all(a0[:2] < b1[:2]) and np.all(b0[:2] < a1[:2]):
                owner[root(j)] = root(i)
    out = {}
    for i, p in enumerate(parts):
        out.setdefault(root(i), []).append(p)
    return list(out.values())


def run_parts(parts, rules, rep):
    """Mesh checks per group of parts (see groups): in each, the largest body
    is the main and the rest its inlays."""
    rows = {}
    for g in groups(parts):
        rows.update(_run_group(g, rules, rep))
    return rows


def _run_group(parts, rules, rep):
    parts = sorted(parts, key=lambda p: -p["mesh"].volume)
    rows = {}
    for p in parts[1:]:
        rows[p["name"]] = check_mesh(p["name"], p["mesh"], rules, rep, role="inlay")
    # check_mesh strips the polygons from the rows it returns; the body's
    # overhang test needs them, so the companions are profiled again.
    fill = [layer_profile(p["mesh"], rules) for p in parts[1:]]
    rows[parts[0]["name"]] = check_mesh(parts[0]["name"], parts[0]["mesh"], rules, rep, role="main",
                                        fill=fill)
    for p in parts[1:]:
        if p.get("extruder") != parts[0].get("extruder") or len(parts) == 2:
            check_inlay(parts[0]["name"], parts[0]["mesh"], p["name"], p["mesh"], rules, rep)
    return rows


def _views(out_dir, source, parts, rep):
    """The figure of what was judged, when --views asks for it."""
    if not out_dir:
        return
    if __package__:
        from .views import render
    else:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from printcheck.views import render
    base = os.path.basename(source).split(".")[0]
    path = render(parts, os.path.join(out_dir, f"{base}_views.png"))
    rep.stage = "views"
    if path:
        rep.info("views", f"{path}: from an angle, from above, cut across y and across x")
    else:
        rep.warn("views", "matplotlib missing, no figure written")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--slice", action="store_true", help="slice a .3mf project with Bambu Studio and check the G-code too")
    ap.add_argument("--json", help="write the findings here")
    ap.add_argument("--rules", help="JSON file overriding the default thresholds")
    ap.add_argument("--nozzle", type=float)
    ap.add_argument("--layer", type=float)
    ap.add_argument("--quiet-info", action="store_true", help="print only OK/WARN/FAIL")
    ap.add_argument("--layers", nargs="?", const="40", metavar="N|all",
                    help="print the layer ledger of the simulation (N rows, default 40, or all)")
    ap.add_argument("--at", type=float, metavar="Z", help="print everything the simulation knows at height Z (mm)")
    ap.add_argument("--rotate", metavar="AXIS,DEG",
                    help="STLs only: authored -> print orientation, e.g. x,180 for a part printed face down; "
                         "the set is then dropped onto z=0 together")
    ap.add_argument("--views", metavar="DIR",
                    help="write <DIR>/<file>_views.png per input: the print from an angle and from above, "
                         "two cuts through its centre on a mm grid")
    a = ap.parse_args(argv)
    rules = dict(RULES)
    if a.rules:
        with open(a.rules) as fh:
            rules.update(json.load(fh))
    if a.nozzle:
        rules["nozzle"] = a.nozzle
        rules["bead"] = round(a.nozzle * 1.05, 2)
    if a.layer:
        rules["layer"] = a.layer

    rep = Report()
    stls = [f for f in a.files if f.lower().endswith(".stl")]
    others = [f for f in a.files if not f.lower().endswith(".stl")]
    if stls:
        parts = [{"name": os.path.basename(f), "extruder": i + 1,
                  "mesh": trimesh.load(f, force="mesh", process=True)} for i, f in enumerate(stls)]
        if a.rotate:
            axis, deg = a.rotate.split(",")
            R = trimesh.transformations.rotation_matrix(
                math.radians(float(deg)), {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}[axis])
            for p in parts:
                p["mesh"].apply_transform(R)
            # One frame for the set: companions keep their place inside the main body.
            z0 = min(p["mesh"].bounds[0][2] for p in parts)
            for p in parts:
                p["mesh"].apply_translation([0, 0, -z0])
        run_parts(parts, rules, rep)
        check_adhesion(parts, {}, rules, rep)
        check_members(parts, {}, rules, rep)
        check_simulation(parts, {}, rules, rep)
        _views(a.views, stls[0], parts, rep)
    for f in others:
        low = f.lower()
        if low.endswith(".3mf"):
            proj = load_project(f)
            used = {p["extruder"] for p in proj["parts"]}
            check_settings(proj["settings"], used, rules, rep)
            if proj["parts"]:
                run_parts(proj["parts"], rules, rep)
                check_adhesion(proj["parts"], proj["settings"], rules, rep)
                check_members(proj["parts"], proj["settings"], rules, rep)
                check_simulation(proj["parts"], proj["settings"], rules, rep)
                _views(a.views, f, proj["parts"], rep)
            else:
                rep.warn("parts", "no meshes found in the project")
            check_slice_info(proj["slice_info"], proj["plate"], rep)
            if proj["gcode"]:
                check_gcode(proj["gcode"], rules, rep, proj["settings"])
            elif a.slice:
                out, err = slice_project(f)
                if out:
                    sliced = load_project(out)
                    rep.stage = "slice"
                    rep.info("sliced", f"sliced with Bambu Studio CLI -> {out}")
                    check_slice_info(sliced["slice_info"], sliced["plate"], rep)
                    check_gcode(sliced["gcode"], rules, rep, sliced["settings"])
                else:
                    rep.warn("slice", err)
            else:
                rep.info("unsliced", "project not sliced; pass --slice to check the G-code too")
        elif low.endswith(".gcode"):
            with open(f, errors="replace") as fh:
                check_gcode(fh.read(), rules, rep)
        else:
            rep.warn("input", f"{f}: not an STL, 3MF or G-code")

    width = max(len(i["stage"]) for i in rep.items) if rep.items else 10
    for i in rep.items:
        if a.quiet_info and i["level"] == "INFO":
            continue
        print(f"{i['level']:4s} {i['stage']:{width}s} {i['code']:22s} {i['msg']}")
    sim = getattr(rep, "simulation", None)
    if sim and (a.layers or a.at is not None):
        print()
        if a.at is not None:
            print(format_ledger(sim, at=a.at))
        else:
            print(format_ledger(sim, max_rows=None if a.layers == "all" else int(a.layers)))
    counts = collections.Counter(i["level"] for i in rep.items)
    verdict = "NO-GO" if rep.failed else ("GO WITH CAVEATS" if counts["WARN"] else "GO")
    print(f"\n{verdict}: {counts['FAIL']} FAIL, {counts['WARN']} WARN, {counts['OK']} OK, {counts['INFO']} INFO")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump({"verdict": verdict, "rules": rules, "findings": rep.items,
                       "layers": getattr(rep, "layers", None), "simulation": sim}, fh, indent=1, default=str)
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
