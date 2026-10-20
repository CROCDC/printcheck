"""A digital reprint: the plate walked layer by layer, the way the A2L will.

At every layer the simulation knows what the nozzle lays (area, islands,
wall length, what is shell and what is sparse), what has been printed so
far (mass and where its centre sits), how hard the bed will shake it (the
A2L's own Y acceleration at that mass and height), whether the stack can
resist that (overturning against weight and peel at the leeward edge),
which members stand free and how far the nozzle's drag and the bed's
reversals push their tips, how long the layer takes and whether the
slicer's cooling rule can stretch it. The ledger is the output; the rules
in `check_simulation` read it, and `--layers` / `--at` print it so a bad
layer can be looked at before the printer finds it.

Physics and constants: docs/SIMULATION.md. Thresholds: RULES in
preflight.py, reasons in docs/RULES.md. Every constant marked provisional
there is waiting for the coupon prints of the research plan (phase 4).
"""

import math

import numpy as np
from shapely.geometry import Point
from shapely.ops import unary_union

from .preflight import geoms, gyration, section, thinnest

G = 9806.65                      # mm/s2
NOZZLE_PUSH_N = 0.098            # 10 g x g: a nozzle dragging across a wall (Orca SupportSpots)

# The A2L as a machine (Bambu Lab A2L 0.4 nozzle preset + Bambu Studio 02.08
# GCode.cpp, mass_load_limited_machine_acceleration).
MACHINE = {
    "max_acc_y": 8000.0,          # machine_max_acceleration_y
    "mass_acc_k": 29e6,           # a_y = k / (offset + printed grams), capped by max_acc_y
    "mass_acc_offset": 2700.0,
    "max_printed_mass_g": 2000.0, # error bit 11 in the slicer
    "heat_soak_inner": (102.0, 93.4, 228.0, 226.6),   # bed_heat_soak_area, level 0 inside
    "heat_soak_outer": (60.0, 49.0, 270.0, 271.0),    # level 2 outside
}

# Stock "0.20mm Standard @BBL A2L", used when the settings carry no value.
PROCESS = {
    "outer_wall_speed": 200.0, "inner_wall_speed": 300.0, "sparse_infill_speed": 270.0,
    "internal_solid_infill_speed": 250.0, "top_surface_speed": 200.0,
    "initial_layer_speed": 50.0, "initial_layer_infill_speed": 105.0,
    "wall_loops": 2, "line_width": 0.42, "initial_layer_line_width": 0.5,
    "sparse_infill_density": 15.0, "bottom_shell_layers": 3, "top_shell_layers": 5,
    "enable_height_slowdown": 1, "slowdown_start_height": 0.0, "slowdown_start_acc": 8000.0,
    "slowdown_end_height": 225.0, "slowdown_end_acc": 1000.0,
}

# Per material when the filament preset is unknown: Bambu PLA Basic and
# PETG HF @BBL A2L. Modulus from the manufacturer's data sheets (flexural).
MATERIAL = {
    "PLA": {"density": 1.26, "slow_down_layer_time": 6.0, "slow_down_min_speed": 20.0,
            "fan_min_speed": 60, "fan_max_speed": 80, "fan_cooling_layer_time": 80,
            "close_fan_the_first_x_layers": 1, "filament_max_volumetric_speed": 23.0,
            "modulus_mpa": 2750.0},
    "PETG": {"density": 1.28, "slow_down_layer_time": 7.0, "slow_down_min_speed": 10.0,
             "fan_min_speed": 30, "fan_max_speed": 50, "fan_cooling_layer_time": 15,
             "close_fan_the_first_x_layers": 3, "filament_max_volumetric_speed": 18.0,
             "modulus_mpa": 2050.0},
    "TPU": {"density": 1.21, "slow_down_layer_time": 8.0, "slow_down_min_speed": 20.0,
            "fan_min_speed": 80, "fan_max_speed": 100, "fan_cooling_layer_time": 60,
            "close_fan_the_first_x_layers": 1, "filament_max_volumetric_speed": 3.2,
            "modulus_mpa": 100.0},
}


def _num(st, key, default, i=0):
    """A number out of Bambu's settings, which keeps them as strings, lists
    of strings and percentages."""
    v = st.get(key, default) if st else default
    if isinstance(v, (list, tuple)):
        v = v[i] if i < len(v) else (v[-1] if v else default)
    if v is None or v == "" or v == "nil":
        v = default
    try:
        return float(str(v).rstrip("%"))
    except (TypeError, ValueError):
        return float(default)


def _inside(p, rect):
    x0, y0, x1, y1 = rect
    b = p.bounds
    return b[0] >= x0 and b[1] >= y0 and b[2] <= x1 and b[3] <= y1


def acc_y(z, mass_g, st):
    """The bed's Y acceleration the slicer will allow at this height and mass:
    the machine's cap, the mass-limited ramp (M201 N1 Y per layer) and the
    slow-down-by-height ramp of the process (on by default on the A2L)."""
    a = min(_num(st, "machine_max_acceleration_y", MACHINE["max_acc_y"]),
            MACHINE["mass_acc_k"] / (MACHINE["mass_acc_offset"] + mass_g))
    if _num(st, "enable_height_slowdown", PROCESS["enable_height_slowdown"]):
        z0, z1 = (_num(st, "slowdown_start_height", PROCESS["slowdown_start_height"]),
                  _num(st, "slowdown_end_height", PROCESS["slowdown_end_height"]))
        a0, a1 = (_num(st, "slowdown_start_acc", PROCESS["slowdown_start_acc"]),
                  _num(st, "slowdown_end_acc", PROCESS["slowdown_end_acc"]))
        if z1 > z0:
            t = min(max((z - z0) / (z1 - z0), 0.0), 1.0)
            a = min(a, a0 + (a1 - a0) * t)
    return a


def fan_for(t_layer, st, n, i=0):
    """Fan duty the cooling buffer picks for a layer of this duration."""
    if n < _num(st, "close_fan_the_first_x_layers", 1, i):
        return 0.0
    f_min, f_max = _num(st, "fan_min_speed", 60, i), _num(st, "fan_max_speed", 80, i)
    t_slow, t_cool = (_num(st, "slow_down_layer_time", 6, i),
                      _num(st, "fan_cooling_layer_time", 80, i))
    if t_layer <= t_slow:
        return f_max
    if t_layer >= t_cool or t_cool <= t_slow:
        return f_min
    return f_max + (f_min - f_max) * (t_layer - t_slow) / (t_cool - t_slow)


def _material(st, i=0):
    typ = str(_lst(st.get("filament_type", "PETG"), i) or "PETG").upper()
    base = MATERIAL.get(typ, MATERIAL["PETG"])
    out = dict(base, type=typ)
    for k in ("density", "slow_down_layer_time", "slow_down_min_speed", "fan_min_speed",
              "fan_max_speed", "fan_cooling_layer_time", "close_fan_the_first_x_layers",
              "filament_max_volumetric_speed"):
        key = "filament_density" if k == "density" else k
        if st and key in st:
            out[k] = _num(st, key, base[k], i)
    return out


def _lst(v, i=0):
    if isinstance(v, (list, tuple)):
        return v[i] if i < len(v) else (v[-1] if v else None)
    return v


def _ring_length(p):
    return sum(g.exterior.length + sum(r.length for r in g.interiors) for g in geoms(p))


def _max_width(p):
    """Largest width an unsupported region spans (twice the deepest inscribed radius)."""
    if p is None or p.is_empty:
        return 0.0
    lo, hi = 0.0, 50.0
    for _ in range(14):
        r = (lo + hi) / 2
        if p.buffer(-r).is_empty:
            hi = r
        else:
            lo = r
    return 2 * lo


def follow_members(polys_by_z, step, min_island):
    """Members that stand free, section by section (the check_members algorithm).

    `polys_by_z`: [(z, [island polygons])] bottom up. An island that continues
    exactly one island below, and is its only continuation, keeps that member
    growing; a split or a merge starts a new one. Returns, per z, the list of
    members alive there: {"poly", "base", "r", "r_y", "area", "siblings", "L"}.
    """
    out, prev = [], []
    for z, islands in polys_by_z:
        cur = [g for g in islands if g.area > min_island]
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
                m = {"poly": I, "base": z, "r": r, "r_y": r_y, "area": A, "siblings": len(cur)}
            m["L"] = z - m["base"] + step / 2
            nxt.append(m)
        out.append((z, nxt))
        prev = nxt
    return out


def member_response(m, a_y, E, rho):
    """How a free-standing member answers the bed's shake and the nozzle's drag.

    Cantilever of height L with the member's weakest section: first natural
    frequency (bending along Y, the bed's axis), the amplification Q the
    input shaper leaves it (1 above 80 Hz, ~15 below 40), the tip deflection
    under the bed's acceleration times Q plus the nozzle pushing at the tip,
    and the bending stress at its base from both.
    """
    L, A, r, r_y = m["L"], m["area"], m["r"], m["r_y"]
    if A <= 0 or r <= 0 or L <= 0:
        return {"f1": None, "Q": 1.0, "sway": 0.0, "stress": 0.0}
    I_min, I_y = A * r * r, A * r_y * r_y            # mm4
    rho_a_kg_per_mm = rho * 1e-3 * A * 1e-3          # g/cm3 -> g/mm3, x mm2 = g/mm, -> kg/mm
    # f1 = 0.56 sqrt(E I / (rho A L^4)), SI inside the root
    EI = E * 1e6 * I_y * 1e-12                      # N m2
    f1 = 0.56 * math.sqrt(EI / (rho_a_kg_per_mm * 1e3 * (L * 1e-3) ** 4)) if EI > 0 else None
    if f1 is None:
        Q = 1.0
    elif f1 >= 80:
        Q = 1.0
    elif f1 <= 40:
        Q = 15.0
    else:
        Q = 15.0 - 14.0 * (f1 - 40) / 40
    # distributed inertial load w [N/mm] = (kg/mm) * a [mm/s2 -> m/s2]
    w = rho_a_kg_per_mm * a_y * 1e-3
    E_nmm = E                                        # MPa = N/mm2
    sway_bed = w * L ** 4 / (8 * E_nmm * I_y) * Q if I_y > 0 else 0.0
    sway_nozzle = NOZZLE_PUSH_N * L ** 3 / (3 * E_nmm * I_min)
    c = r * math.sqrt(3)                             # extreme fibre of the least section
    M_bed = w * L ** 2 / 2 * Q
    M_nozzle = NOZZLE_PUSH_N * L
    stress = (M_bed + M_nozzle) * c / I_min
    return {"f1": f1, "Q": Q, "sway": sway_bed + sway_nozzle, "sway_bed": sway_bed,
            "sway_nozzle": sway_nozzle, "stress": stress}


def simulate(parts, settings, rules):
    """The ledger: one row per sampled layer of the plate.

    `parts` as in check_print (meshes in print orientation, bed at z=0;
    coordinates are the plate's when they come from a 3MF). Returns
    {"step", "rows", "bodies", "notes"}.
    """
    import trimesh

    st = settings or {}
    layer = rules["layer"]
    meshes = [p["mesh"] for p in parts]
    whole = trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]
    z0, z1 = float(whole.bounds[0][2]), float(whole.bounds[1][2])
    H = z1 - z0
    n_layers = max(1, int(round(H / layer)))
    stride = max(1, math.ceil(n_layers / rules.get("sim_max_layers", 250)))
    step = layer * stride
    zs = [z0 + layer / 2 + k * layer for k in range(0, n_layers, stride)]
    mat = _material(st, 0)
    E = rules.get("modulus_mpa", {}).get(mat["type"], mat["modulus_mpa"])
    rho = mat["density"]
    walls = int(_num(st, "wall_loops", PROCESS["wall_loops"]))
    line = _num(st, "line_width", PROCESS["line_width"])
    line0 = _num(st, "initial_layer_line_width", PROCESS["initial_layer_line_width"])
    dens = _num(st, "sparse_infill_density", PROCESS["sparse_infill_density"]) / 100.0
    bottom, top = (int(_num(st, "bottom_shell_layers", PROCESS["bottom_shell_layers"])),
                   int(_num(st, "top_shell_layers", PROCESS["top_shell_layers"])))
    v_out, v_in = (_num(st, "outer_wall_speed", PROCESS["outer_wall_speed"]),
                   _num(st, "inner_wall_speed", PROCESS["inner_wall_speed"]))
    v_sparse, v_solid = (_num(st, "sparse_infill_speed", PROCESS["sparse_infill_speed"]),
                         _num(st, "internal_solid_infill_speed", PROCESS["internal_solid_infill_speed"]))
    v_first, v_first_fill = (_num(st, "initial_layer_speed", PROCESS["initial_layer_speed"]),
                             _num(st, "initial_layer_infill_speed", PROCESS["initial_layer_infill_speed"]))
    v_cap = mat["filament_max_volumetric_speed"] / (line * layer)   # mm/s the filament allows
    t_slow, v_min = mat["slow_down_layer_time"], mat["slow_down_min_speed"]
    sigma = rules.get("peel_mpa", {}).get(mat["type"], 0.1)
    brim_type = str(st.get("brim_type", "auto_brim"))
    brim_w = _num(st, "brim_width", 5.0) if brim_type in ("outer_only", "outer_and_inner") else 0.0

    bodies = sorted([b for b in whole.split(only_watertight=False) if b.volume > 1.0],
                    key=lambda b: -b.volume)
    on_bed = [b.bounds[0][2] <= z0 + layer / 2 for b in bodies]
    # Sections of every body at every sampled height, taken once.
    secs = [[section(b, z) if b.bounds[0][2] - 1e-6 <= z <= b.bounds[1][2] + 1e-6 else None
             for z in zs] for b in bodies]
    members = [follow_members([(z, geoms(s)) for z, s in zip(zs, bs)], step, rules["min_island_mm2"])
               for bs in secs]
    first = [secs[i][0] if on_bed[i] else None for i in range(len(bodies))]
    foot = []
    for F in first:
        if F is None or F.is_empty:
            foot.append(None)
            continue
        foot.append(F.buffer(brim_w) if brim_w else F)

    rows, cum_mass, cum_mz = [], 0.0, 0.0
    body_mass = [0.0] * len(bodies)
    body_mz = [0.0] * len(bodies)
    prev_plate = None
    plate_notes = []
    for k, z in enumerate(zs):
        n = int(round((z - z0 - layer / 2) / layer))
        polys = [s for s in (bs[k] for bs in secs) if s is not None and not s.is_empty]
        plate = unary_union(polys) if polys else None
        area = float(plate.area) if plate is not None else 0.0
        # --- what the nozzle lays on this layer, and how long it takes
        path_out = path_in = path_solid = path_sparse = 0.0
        if plate is not None and area > 0:
            ring = _ring_length(plate)
            path_out, path_in = ring, ring * max(walls - 1, 0)
            lw = line0 if n == 0 else line
            inner = plate.buffer(-walls * lw)
            if not inner.is_empty:
                # Shell or sparse: the inner region is solid where the part
                # ends within `top` layers above or `bottom` layers below.
                solid = inner
                if n >= bottom:
                    above_i, below_i = k + math.ceil(top / stride), k - math.ceil(bottom / stride)
                    covered = inner
                    for idx in (above_i, below_i):
                        if 0 <= idx < len(zs):
                            cov = [s for s in (bs[idx] for bs in secs) if s is not None and not s.is_empty]
                            covered = covered.intersection(unary_union(cov)) if cov else covered.difference(covered)
                        else:
                            covered = covered.difference(covered)
                    solid = inner.difference(covered)
                    path_sparse = covered.area * dens / lw
                path_solid = solid.area / lw
        if n == 0:
            t = (path_out + path_in) / min(v_first, v_cap) + (path_solid + path_sparse) / min(v_first_fill, v_cap)
        else:
            t = (path_out / min(v_out, v_cap) + path_in / min(v_in, v_cap)
                 + path_solid / min(v_solid, v_cap) + path_sparse / min(v_sparse, v_cap))
        path = path_out + path_in + path_solid + path_sparse
        t_at_min = path / v_min if v_min > 0 else t
        t_after = max(t, min(t_slow, t_at_min))
        reach = t_at_min >= t_slow or t >= t_slow
        # --- mass laid: walls and shells solid, the rest at the infill density
        lw = line0 if n == 0 else line
        laid_vol = ((path_out + path_in + path_solid) * lw + path_sparse * line) * layer
        laid_g = laid_vol * rho / 1000.0 * stride
        cum_mass += laid_g
        cum_mz += laid_g * z
        # --- growth past the layer below (overhang) on the plate as a whole
        growth = growth_w = 0.0
        if plate is not None and prev_plate is not None:
            free = plate.difference(prev_plate.buffer(step + 1e-6))
            growth, growth_w = float(free.area), _max_width(free)
        prev_plate = plate
        a = acc_y(z, cum_mass, st)
        row = {"n": n, "z": round(z - z0 + layer / 2, 3), "area": round(area, 1),
               "islands": len(geoms(plate)) if plate is not None else 0,
               "path_mm": round(path, 0), "time_s": round(t, 1), "time_after_s": round(t_after, 1),
               "reach_floor": reach, "fan": round(fan_for(t_after, st, n), 0),
               "mass_g": round(cum_mass, 2), "acc_y": round(a, 0),
               "growth_mm2": round(growth, 1), "growth_w": round(growth_w, 2), "bodies": []}
        # --- per body: overturning and the free members
        for i, b in enumerate(bodies):
            s = secs[i][k]
            if s is None or s.is_empty:
                continue
            bi_vol = s.area * layer * stride
            # body mass: same shell/infill estimate, approximated by its share of the plate's layer
            share = s.area / area if area else 0
            bg = laid_g * share
            body_mass[i] += bg
            body_mz[i] += bg * z
            entry = {"body": i + 1, "area": round(float(s.area), 1), "islands": len(geoms(s)),
                     "mass_g": round(body_mass[i], 2)}
            if on_bed[i] and foot[i] is not None:
                F = foot[i]
                hull = F.convex_hull
                b0, b1 = hull.bounds[1], hull.bounds[3]
                depth = b1 - b0
                cz = body_mz[i] / body_mass[i] if body_mass[i] else z
                com = s.centroid                  # xy of the mass so far ~ this section's centroid
                A_f, r_f, r_fy = gyration(F)
                cy = F.centroid.y
                # leeward edge: the bed reverses, so the shorter arm counts
                d_y = min(com.y - b0, b1 - com.y)
                Ixx_c = A_f * r_fy * r_fy
                W_x = min((Ixx_c + A_f * (cy - b0) ** 2), (Ixx_c + A_f * (b1 - cy) ** 2)) / depth if depth else 0.0
                m_kg = body_mass[i] / 1000.0
                M_ov = m_kg * a * 1e-3 * cz + NOZZLE_PUSH_N * (z - z0)       # N mm
                M_res = m_kg * G * 1e-3 * max(d_y, 0.0) + sigma * W_x
                entry.update({"com_z": round(cz, 1), "M_ov": round(M_ov, 2), "M_res": round(M_res, 1),
                              "margin": round(M_res / M_ov, 1) if M_ov > 0 else None})
            alive = members[i][k][1]
            worst = None
            for m in alive:
                if m["L"] < 2 * step:
                    continue
                r_ = member_response(m, a, E, rho)
                lam = m["L"] / m["r"] if m["r"] else float("inf")
                cand = {"L": round(m["L"], 1), "area": round(m["area"], 1), "r": round(m["r"], 2),
                        "lam": round(lam, 0), "f1": round(r_["f1"], 0) if r_["f1"] else None,
                        "Q": round(r_["Q"], 1), "sway": round(r_["sway"], 3),
                        "stress": round(r_["stress"], 3), "x": round(m["poly"].centroid.x, 1),
                        "y": round(m["poly"].centroid.y, 1), "siblings": m["siblings"]}
                if worst is None or cand["sway"] > worst["sway"]:
                    worst = cand
            if worst:
                entry["member"] = worst
            row["bodies"].append(entry)
        rows.append(row)
    # heat-soak zone of the plate footprint (plate coordinates only). The
    # bodies are judged one by one and without their brim: a frame sitting
    # 22 mm past the outer zone came off at layer 359 of 449 (2026-10-19)
    # while two plates whose brims alone spilled 4 mm held.
    zone = None
    bodies_out = []
    foots = [f for f in foot if f is not None]
    if foots:
        all_foot = unary_union(foots)
        if _inside(all_foot, MACHINE["heat_soak_inner"]):
            zone = 0
        elif _inside(all_foot, MACHINE["heat_soak_outer"]):
            zone = 1
        else:
            zone = 2
        b = all_foot.bounds
        if b[0] < 0 or b[1] < 0:
            zone = None          # not plate coordinates (a mesh centred on the origin)
        else:
            x0, y0, x1, y1 = MACHINE["heat_soak_outer"]
            for i, F in enumerate(first):
                if F is None or F.is_empty:
                    continue
                fb = F.bounds
                out = max(x0 - fb[0], y0 - fb[1], fb[2] - x1, fb[3] - y1, 0.0)
                if out > 0:
                    bodies_out.append({"body": i + 1, "out_mm": round(out, 1),
                                       "height": round(float(bodies[i].bounds[1][2] - bodies[i].bounds[0][2]), 1),
                                       "foot_mm2": round(F.area, 0)})
    return {"step": step, "stride": stride, "layer": layer, "rows": rows, "n_bodies": len(bodies),
            "material": mat["type"], "modulus_mpa": E, "density": rho, "peel_mpa": sigma,
            "heat_zone": zone, "bodies_out": bodies_out, "brim_mm": brim_w, "brim_type": brim_type,
            "cooling": {"slow_down_layer_time": t_slow, "slow_down_min_speed": v_min,
                        "volumetric_cap_mm_s": round(v_cap, 0)}}


def check_simulation(parts, settings, rules, rep):
    """The rules that read the ledger."""
    rep.stage = "sim"
    try:
        sim = simulate(parts, settings, rules)
    except Exception as exc:        # the simulation is an extra pair of eyes, never a crash
        rep.info("sim", f"simulation skipped: {exc!r}")
        return None
    rep.simulation = sim
    rows = sim["rows"]
    if not rows:
        return sim
    layer = rules["layer"]
    stride_note = f" (every {sim['stride']} layers)" if sim["stride"] > 1 else ""
    rep.info("ledger", f"{len(rows)} layers simulated{stride_note}: {sim['material']}, E {sim['modulus_mpa']:.0f} MPa, "
                       f"peel {sim['peel_mpa']} MPa, brim {sim['brim_type']} {sim['brim_mm']:.0f} mm; "
                       f"print `--layers` to read it, `--at Z` for one height")
    # mass and acceleration
    m_end = rows[-1]["mass_g"]
    a_min = min(r["acc_y"] for r in rows)
    if m_end > rules.get("max_printed_mass_g", MACHINE["max_printed_mass_g"]):
        rep.fail("mass", f"{m_end:.0f} g printed: over the A2L's {MACHINE['max_printed_mass_g']:.0f} g limit")
    else:
        rep.info("mass", f"~{m_end:.1f} g on the plate at the end (walls and shells solid, infill at density); "
                         f"bed acceleration {rows[0]['acc_y']:.0f} -> {a_min:.0f} mm/s2 with height")
    # overturning per body
    worst_m = {}
    for r in rows:
        for e in r["bodies"]:
            if e.get("margin") is not None and (e["body"] not in worst_m or e["margin"] < worst_m[e["body"]][0]):
                worst_m[e["body"]] = (e["margin"], r["z"], e)
    for body, (mg, z, e) in sorted(worst_m.items()):
        msg = (f"body {body}: overturning margin {mg:.1f} at z={z} (resists {e['M_res']:.0f} N mm against "
               f"{e['M_ov']:.1f} from the bed's shake and the nozzle's push)")
        if mg < rules["min_overturn_margin_fail"]:
            rep.fail("overturn", msg + ": peels off its footprint; brim, a wider base or another face down")
        elif mg < rules["min_overturn_margin_warn"]:
            rep.warn("overturn", msg + ": little in hand on a moving bed; a brim doubles it")
    if worst_m:
        body, (mg, z, _) = min(worst_m.items(), key=lambda kv: kv[1][0])
        if mg >= rules["min_overturn_margin_warn"]:
            rep.ok("overturn", f"every body resists the bed's shake and the nozzle's push (least margin {mg:.0f}, "
                               f"body {body} at z={z})")
    # members: sway and stress
    worst_s = {}
    for r in rows:
        for e in r["bodies"]:
            m = e.get("member")
            if m and (e["body"] not in worst_s or m["sway"] > worst_s[e["body"]][0]["sway"]):
                worst_s[e["body"]] = (m, r["z"])
    for body, (m, z) in sorted(worst_s.items()):
        where = (f"body {body}: a member {m['area']:.0f} mm2 at ({m['x']:.0f}, {m['y']:.0f}), free for "
                 f"{m['L']:.0f} mm at z={z}, sways {m['sway']:.2f} mm at the tip under the nozzle's drag"
                 f"{' and the bed' if m['Q'] > 1 else ''} (f1 {m['f1']} Hz, Q {m['Q']:.0f}, base stress "
                 f"{m['stress']:.2f} MPa, slenderness {m['lam']:.0f})")
        data = dict(m, z=z)
        if m["sway"] > rules["max_tip_sway_fail"]:
            rep.fail("sway", where + f" (> {rules['max_tip_sway_fail']} mm, more than a bead): the nozzle "
                             f"catches the top and levers the member; tie it to its neighbours", **data)
        elif m["sway"] > rules["max_tip_sway_warn"]:
            rep.warn("sway", where + f" (> {rules['max_tip_sway_warn']} mm): the top layers go down on "
                             f"something that moves", **data)
    ok_s = [(b, m, z) for b, (m, z) in worst_s.items() if m["sway"] <= rules["max_tip_sway_warn"]]
    if ok_s and len(ok_s) == len(worst_s):
        b, m, z = max(ok_s, key=lambda t: t[1]["sway"])
        rep.ok("sway", f"no member sways past {rules['max_tip_sway_warn']} mm (worst {m['sway']:.2f} mm, "
                       f"body {b}, {m['L']:.0f} mm free at z={z})")
    # layer time floor: layers the cooling rule cannot stretch
    c = sim["cooling"]
    skip = int(rules.get("sim_skip_first_layers", 3))
    cannot = [r for r in rows if r["n"] >= skip and not r["reach_floor"] and r["area"] > 0]
    body_rows = [r for r in rows if r["n"] >= skip and r["area"] > 0]
    if body_rows:
        share = len(cannot) / len(body_rows)
        est = [r["time_after_s"] for r in body_rows]
        msg = (f"estimated layer {min(est):.0f}-{max(est):.0f} s (median {float(np.median(est)):.0f}); "
               f"{len(cannot)}/{len(body_rows)} layers cannot reach the {c['slow_down_layer_time']:.0f} s "
               f"cooling floor even at {c['slow_down_min_speed']:.0f} mm/s")
        if share > rules["max_short_layer_share"]:
            rep.warn("layer_floor", msg + f" (> {rules['max_short_layer_share']:.0%} of the layers): each layer "
                                    f"goes down on one still soft; print 2+ copies or add a sacrificial post",
                     layers=[(r["z"], r["time_s"]) for r in cannot[:10]])
        else:
            rep.info("layer_floor", msg)
    # overhang width on one layer
    g = max(rows, key=lambda r: r["growth_w"])
    if g["growth_w"] > 0:
        rep.info("growth", f"widest unsupported step on one layer {g['growth_w']:.1f} mm at z={g['z']} "
                           f"({g['growth_mm2']:.0f} mm2)")
    # heat-soak zone
    if sim.get("bodies_out"):
        worst = max(sim["bodies_out"], key=lambda d: d["out_mm"])
        rep.fail("heat_zone", f"body {worst['body']} ({worst['height']:.0f} tall on {worst['foot_mm2']:.0f} mm2) "
                              f"sits {worst['out_mm']:.0f} mm outside the bed's outer preconditioning zone "
                              f"(60-270 x 49-271): the first layer there is laid on the cold rim of the bed "
                              f"(the frame of 2026-10-19 came off at layer 359 of 449 from 22 mm out); "
                              f"move the bodies inside the zone, the delicate one to the centre",
                 bodies=sim["bodies_out"])
    elif sim["heat_zone"] == 2:
        rep.warn("heat_zone", "only the brim leaves the bed's outer preconditioning zone (60-270 x 49-271): "
                              "every body is inside; the A2L waits longer to start")
    elif sim["heat_zone"] == 1:
        rep.info("heat_zone", "footprint outside the inner heat-soak zone (102-228 x 93-227): a longer wait")
    # the layer to watch
    watch = None
    for r in rows:
        score = 0.0
        for e in r["bodies"]:
            if e.get("margin"):
                score = max(score, 3.0 / max(e["margin"], 1e-6))
            if e.get("member"):
                score = max(score, e["member"]["sway"] / max(rules["max_tip_sway_warn"], 1e-6))
        if not r["reach_floor"] and r["n"] >= skip:
            score = max(score, 0.5)
        if watch is None or score > watch[0]:
            watch = (score, r)
    if watch and watch[0] > 0.3:
        r = watch[1]
        rep.info("watch", f"layer to watch: n={r['n']} z={r['z']} ({r['area']:.0f} mm2, {r['time_after_s']:.0f} s, "
                          f"{r['mass_g']:.0f} g so far, bed {r['acc_y']:.0f} mm/s2)")
    return sim


def format_ledger(sim, max_rows=40, at=None):
    """The ledger as text: one line per layer (thinned to `max_rows`), or
    everything about the layer nearest `at` mm."""
    rows = sim["rows"]
    if not rows:
        return "no layers"
    if at is not None:
        r = min(rows, key=lambda r: abs(r["z"] - at))
        lines = [f"layer n={r['n']} z={r['z']} mm  area {r['area']} mm2 in {r['islands']} island(s)  "
                 f"path {r['path_mm']:.0f} mm  time {r['time_s']} s (after cooling rule {r['time_after_s']} s"
                 f"{'' if r['reach_floor'] else ', cannot reach the floor'})  fan {r['fan']:.0f} %",
                 f"  printed so far {r['mass_g']} g; bed acceleration {r['acc_y']:.0f} mm/s2; growth "
                 f"{r['growth_mm2']} mm2, widest step {r['growth_w']} mm"]
        for e in r["bodies"]:
            s = f"  body {e['body']}: {e['area']} mm2, {e['islands']} island(s), {e['mass_g']} g so far"
            if e.get("margin") is not None:
                s += f"; overturning margin {e['margin']} (M_res {e['M_res']} / M_ov {e['M_ov']} N mm, com z {e['com_z']})"
            lines.append(s)
            m = e.get("member")
            if m:
                lines.append(f"    free member {m['area']} mm2 at ({m['x']}, {m['y']}): {m['L']} mm free, r {m['r']}, "
                             f"slenderness {m['lam']:.0f}, f1 {m['f1']} Hz, Q {m['Q']}, tip sway {m['sway']} mm, "
                             f"base stress {m['stress']} MPa, {m['siblings']} sibling(s)")
        return "\n".join(lines)
    stride = max(1, math.ceil(len(rows) / max_rows)) if max_rows else 1
    hdr = f"{'n':>5} {'z':>7} {'mm2':>8} {'isl':>4} {'s':>6} {'s*':>5} {'fan':>4} {'g':>7} {'acc_y':>6} {'grow':>7} {'margin':>7} {'free':>5} {'sway':>6} {'lam':>4}"
    out = [f"{len(rows)} layers of {sim['layer']} mm" + (f", one row per {sim['stride']} layers" if sim['stride'] > 1 else "")
           + f"; s = estimated layer time, s* = after the slicer's cooling rule; margin = overturning, "
             f"free/sway/lam = worst free member (mm, mm, slenderness)", hdr]
    for r in rows[::stride] + ([rows[-1]] if (len(rows) - 1) % stride else []):
        mg = min((e["margin"] for e in r["bodies"] if e.get("margin") is not None), default=None)
        mem = max((e["member"] for e in r["bodies"] if e.get("member")), key=lambda m: m["sway"], default=None)
        free = "-" if mem is None else f"{mem['L']:.0f}"
        sway = "-" if mem is None else f"{mem['sway']:.2f}"
        lam = "-" if mem is None else f"{mem['lam']:.0f}"
        out.append(f"{r['n']:>5} {r['z']:>7.2f} {r['area']:>8.0f} {r['islands']:>4} {r['time_s']:>6.1f} "
                   f"{r['time_after_s']:>5.0f}{'' if r['reach_floor'] else '!'}{'' if not r['reach_floor'] else ' '} "
                   f"{r['fan']:>3.0f} {r['mass_g']:>7.1f} {r['acc_y']:>6.0f} {r['growth_mm2']:>7.0f} "
                   f"{('-' if mg is None else f'{mg:.0f}'):>7} "
                   f"{free:>5} {sway:>6} {lam:>4}")
    return "\n".join(out)
