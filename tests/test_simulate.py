"""The layer simulation: its helpers, the ledger it builds and the rules
that read it. Ledger-reading rules are fed crafted ledgers, so each branch
is pinned to the numbers that trigger it."""

import math

import pytest
import trimesh
from shapely.geometry import Polygon, box as sbox

from printcheck import simulate as S
from printcheck.preflight import Report

from conftest import box, found, part


# ----------------------------------------------------------------- helpers

def test_num_reads_bambu_values():
    assert S._num(None, "k", 3) == 3.0
    assert S._num({}, "k", 3) == 3.0
    assert S._num({"k": ["1", "2"]}, "k", 0, i=1) == 2.0
    assert S._num({"k": ["1", "2"]}, "k", 0, i=5) == 2.0      # past the end: the last filament
    assert S._num({"k": []}, "k", 7) == 7.0
    for blank in (None, "", "nil"):
        assert S._num({"k": blank}, "k", 4) == 4.0
    assert S._num({"k": "15%"}, "k", 0) == 15.0
    assert S._num({"k": "fast"}, "k", 9) == 9.0


def test_inside():
    assert S._inside(sbox(10, 10, 20, 20), (0, 0, 30, 30))
    assert not S._inside(sbox(10, 10, 40, 20), (0, 0, 30, 30))


def test_acc_y_ramps():
    # mass-limited: 29e6 / (2700 + 300) = 9667, capped by the machine's 8000
    assert S.acc_y(0, 300, {"enable_height_slowdown": "0"}) == 8000
    assert S.acc_y(0, 6300, {"enable_height_slowdown": "0"}) == pytest.approx(29e6 / 9000)
    # the height ramp, halfway from 8000 to 1000
    assert S.acc_y(112.5, 0, {}) == pytest.approx(4500)
    # a ramp that ends where it starts is ignored
    st = {"slowdown_start_height": "50", "slowdown_end_height": "50"}
    assert S.acc_y(100, 0, st) == 8000


def test_fan_for():
    st = {"close_fan_the_first_x_layers": "1", "fan_min_speed": "20", "fan_max_speed": "80",
          "slow_down_layer_time": "6", "fan_cooling_layer_time": "30"}
    assert S.fan_for(3, st, 0) == 0.0                    # fan off on the first layer
    assert S.fan_for(5, st, 2) == 80                     # short layer: full fan
    assert S.fan_for(40, st, 2) == 20                    # long layer: minimum
    assert S.fan_for(18, st, 2) == pytest.approx(50)     # halfway along the ramp
    assert S.fan_for(10, dict(st, fan_cooling_layer_time="4"), 2) == 20   # no ramp to follow


def test_material():
    m = S._material({"filament_type": ["PLA"], "filament_density": ["1.3"], "slow_down_layer_time": "9"})
    assert m["type"] == "PLA" and m["density"] == 1.3 and m["slow_down_layer_time"] == 9.0
    assert m["modulus_mpa"] == S.MATERIAL["PLA"]["modulus_mpa"]
    unknown = S._material({"filament_type": "NYLON-X"})
    assert unknown["type"] == "NYLON-X" and unknown["density"] == S.MATERIAL["PETG"]["density"]
    assert S._material({})["type"] == "PETG"


def test_lst():
    assert S._lst(("a", "b"), 1) == "b"
    assert S._lst(["a"], 3) == "a"
    assert S._lst([], 0) is None
    assert S._lst("x") == "x"


def test_ring_length_counts_holes():
    ring = sbox(0, 0, 10, 10).difference(sbox(2, 2, 8, 8))
    assert S._ring_length(ring) == pytest.approx(40 + 24)


def test_max_width():
    assert S._max_width(None) == 0.0
    assert S._max_width(Polygon()) == 0.0
    assert S._max_width(sbox(0, 0, 10, 4)) == pytest.approx(4, abs=0.01)


def test_follow_members_split_and_continue():
    base = [sbox(0, 0, 20, 4)]
    legs = [sbox(0, 0, 4, 4), sbox(16, 0, 20, 4), sbox(9, 0, 9.5, 0.5)]   # the last is under min_island
    out = S.follow_members([(0.1, base), (1.1, legs), (2.1, legs)], 1.0, 1.0)
    assert len(out[0][1]) == 1 and out[0][1][0]["siblings"] == 1
    z, alive = out[2]
    assert len(alive) == 2                                     # the sliver is dropped
    assert all(m["base"] == 1.1 and m["siblings"] == 2 for m in alive)   # the split starts new members
    assert all(m["L"] == pytest.approx(1.5) for m in alive)    # 2.1 - 1.1 + step / 2


def test_member_response_regimes():
    E, rho = 2050.0, 1.28
    assert S.member_response({"L": 0, "area": 10, "r": 1, "r_y": 1}, 8000, E, rho)["sway"] == 0.0
    flat = S.member_response({"L": 50, "area": 10, "r": 1, "r_y": 0}, 8000, E, rho)
    assert flat["f1"] is None and flat["Q"] == 1.0 and flat["sway_bed"] == 0.0
    stiff = S.member_response({"L": 10, "area": 100, "r": 3, "r_y": 3}, 8000, E, rho)
    assert stiff["f1"] >= 80 and stiff["Q"] == 1.0
    floppy = S.member_response({"L": 150, "area": 4, "r": 0.5, "r_y": 0.5}, 8000, E, rho)
    assert floppy["f1"] <= 40 and floppy["Q"] == 15.0
    # find a length whose first frequency falls between 40 and 80 Hz
    mid = next(r for L in range(20, 200) if 40 < (r := S.member_response(
        {"L": L, "area": 9, "r": 0.87, "r_y": 0.87}, 8000, E, rho))["f1"] < 80)
    assert mid["Q"] == pytest.approx(15 - 14 * (mid["f1"] - 40) / 40)
    assert mid["sway"] == pytest.approx(mid["sway_bed"] + mid["sway_nozzle"])


# ----------------------------------------------------------------- simulate()

def test_simulate_box_ledger(rules):
    sim = S.simulate([part(box(20, 20, 4))], {}, rules)
    assert sim["stride"] == 1 and len(sim["rows"]) == 20 and sim["n_bodies"] == 1
    assert sim["material"] == "PETG" and sim["heat_zone"] is None        # origin-centred: not plate coordinates
    first, last = sim["rows"][0], sim["rows"][-1]
    assert first["n"] == 0 and first["area"] == 400 and first["mass_g"] > 0
    assert last["mass_g"] > first["mass_g"]
    assert last["bodies"][0]["margin"] > 100
    assert "member" in last["bodies"][0] and "member" not in first["bodies"][0]   # too short at first


def test_simulate_gap_and_floating_body(rules):
    # A slab on the bed and a slab floating 3 mm above it: nothing between.
    parts = [part(box(30, 30, 2, 160, 160)), part(box(30, 30, 2, 160, 160, 5))]
    sim = S.simulate(parts, {}, dict(rules, sim_max_layers=20))
    assert sim["stride"] == 2 and sim["step"] == pytest.approx(0.4)
    gap = [r for r in sim["rows"] if 2.2 < r["z"] < 5.0]
    assert gap and all(r["area"] == 0 and r["bodies"] == [] and r["growth_mm2"] == 0 for r in gap)
    above = [r for r in sim["rows"] if r["z"] > 5.2]
    assert above and all("margin" not in e for r in above for e in r["bodies"])   # not on the bed
    assert sim["heat_zone"] == 0 and sim["bodies_out"] == []


def test_simulate_thin_posts_and_brim(rules):
    # Two identical posts too thin for two walls, on one base: above the
    # base the plate has no inner region, and the two members tie for worst.
    base = box(20, 6, 2, 160, 200)
    body_ = base.union(trimesh.util.concatenate([box(1.6, 1.6, 6, x, 200, 2) for x in (154, 166)]),
                       engine="manifold")
    sim = S.simulate([part(body_)], {"brim_type": "outer_only", "brim_width": "4"}, rules)
    assert sim["brim_mm"] == 4.0 and sim["brim_type"] == "outer_only"
    top = sim["rows"][-1]
    assert top["area"] == pytest.approx(2 * 1.6 * 1.6, abs=0.1) and top["islands"] == 2
    m = top["bodies"][0]["member"]
    assert m["siblings"] == 2 and m["L"] > 5


def test_simulate_film_has_no_footprint(rules):
    # A film thinner than half a layer stands on the bed, yet no sampled
    # layer cuts it: no first layer, so no heat zone either.
    sim = S.simulate([part(box(100, 100, 0.05, 160, 160))], {}, rules)
    assert sim["heat_zone"] is None and [r["area"] for r in sim["rows"]] == [0.0]


def test_simulate_heat_zones(rules):
    inner = S.simulate([part(box(20, 20, 1, 165, 160))], {}, rules)
    assert inner["heat_zone"] == 0 and inner["bodies_out"] == []
    outer = S.simulate([part(box(20, 20, 1, 80, 160))], {}, rules)
    assert outer["heat_zone"] == 1
    # inside the outer zone itself, only its brim spills out
    brim = S.simulate([part(box(20, 20, 1, 72, 160))], {"brim_type": "outer_and_inner", "brim_width": "5"}, rules)
    assert brim["heat_zone"] == 2 and brim["bodies_out"] == []
    # a body 40 mm past the zone, plus one floating above it (no first layer)
    off = S.simulate([part(box(20, 20, 2, 30, 160)), part(box(10, 10, 1, 30, 160, 3))], {}, rules)
    assert off["heat_zone"] == 2
    assert off["bodies_out"] == [{"body": 1, "out_mm": 40.0, "height": 2.0, "foot_mm2": 400}]


# ----------------------------------------------------------------- check_simulation(), real meshes

def test_check_simulation_box_is_ok(rules):
    rep = Report()
    sim = S.check_simulation([part(box(20, 20, 10))], {}, rules, rep)
    f = found(rep, "sim")
    assert rep.simulation is sim
    assert f[("sim", "overturn")] == "OK" and f[("sim", "sway")] == "OK"
    assert f[("sim", "ledger")] == "INFO" and f[("sim", "mass")] == "INFO"


def test_check_simulation_post_fails(rules):
    rep = Report()
    S.check_simulation([part(box(3, 3, 60))], {"filament_type": ["PLA"]}, rules, rep)
    f = found(rep, "sim")
    assert f[("sim", "overturn")] == "FAIL"
    assert f[("sim", "layer_floor")] == "WARN"
    assert f[("sim", "watch")] == "INFO"


def test_check_simulation_never_crashes(rules, monkeypatch):
    def boom(*a):
        raise ValueError("bad mesh")
    monkeypatch.setattr(S, "simulate", boom)
    rep = Report()
    assert S.check_simulation([], {}, rules, rep) is None
    assert rep.items[0]["level"] == "INFO" and "simulation skipped" in rep.items[0]["msg"]


# ----------------------------------------------------------------- check_simulation(), crafted ledgers

def row(n, z, area=100.0, reach=True, bodies=(), growth_w=0.0, growth=0.0, t=10.0, mass=1.0):
    return {"n": n, "z": z, "area": area, "islands": 1, "path_mm": 100.0, "time_s": t,
            "time_after_s": t, "reach_floor": reach, "fan": 50.0, "mass_g": mass, "acc_y": 8000.0,
            "growth_mm2": growth, "growth_w": growth_w, "bodies": list(bodies)}


def member(sway, L=20.0, Q=1.0):
    return {"L": L, "area": 9.0, "r": 0.87, "lam": round(L / 0.87), "f1": 100.0, "Q": Q,
            "sway": sway, "stress": 1.0, "x": 1.0, "y": 2.0, "siblings": 1}


def body(n, margin=None, m=None):
    e = {"body": n, "area": 50.0, "islands": 1, "mass_g": 1.0}
    if margin is not None:
        e.update({"com_z": 5.0, "M_ov": 1.0, "M_res": margin, "margin": margin})
    if m is not None:
        e["member"] = m
    return e


def ledger(rows, **kw):
    sim = {"step": 0.2, "stride": 1, "layer": 0.2, "rows": rows, "n_bodies": 2, "material": "PETG",
           "modulus_mpa": 2050.0, "density": 1.28, "peel_mpa": 0.1, "heat_zone": None, "bodies_out": [],
           "brim_mm": 0.0, "brim_type": "auto_brim",
           "cooling": {"slow_down_layer_time": 7.0, "slow_down_min_speed": 10.0, "volumetric_cap_mm_s": 100}}
    sim.update(kw)
    return sim


def judge(monkeypatch, rules, sim):
    monkeypatch.setattr(S, "simulate", lambda *a: sim)
    rep = Report()
    S.check_simulation([], {}, rules, rep)
    return rep


def test_empty_ledger_says_nothing(monkeypatch, rules):
    rep = judge(monkeypatch, rules, ledger([]))
    assert rep.items == [] and rep.simulation["rows"] == []


def test_overturn_and_sway_levels(monkeypatch, rules):
    rows = [row(n, 0.2 * (n + 1), bodies=[body(1, margin=10.0 - n, m=member(0.04 * n)),
                                          body(2, margin=2.0, m=member(0.3)),
                                          body(3, margin=0.5, m=member(0.6)),
                                          body(4)])
            for n in range(5)]
    sim = ledger(rows, stride=3, heat_zone=1)
    rep = judge(monkeypatch, rules, sim)
    msgs = {(i["code"], i["level"]): i["msg"] for i in rep.items}
    assert "(every 3 layers)" in msgs[("ledger", "INFO")]
    assert "body 2: overturning margin 2.0" in msgs[("overturn", "WARN")]
    assert "body 3: overturning margin 0.5" in msgs[("overturn", "FAIL")]
    assert ("overturn", "OK") not in msgs                     # the least margin is under the warn line
    sways = [i for i in rep.items if i["code"] == "sway"]
    assert [(i["level"], i["msg"][:6]) for i in sways] == [("WARN", "body 2"), ("FAIL", "body 3")]
    assert not any(i["code"] == "sway" and i["level"] == "OK" for i in rep.items)   # not every body is fine
    assert msgs[("heat_zone", "INFO")].startswith("footprint outside the inner")


def test_all_fine_and_quiet(monkeypatch, rules):
    # one body with no margin anywhere, no member: no overturn or sway line,
    # every layer under the skip, nothing to watch
    rows = [row(n, 0.2 * (n + 1), bodies=[body(1)]) for n in range(3)]
    rep = judge(monkeypatch, rules, ledger(rows, heat_zone=0))
    codes = {i["code"] for i in rep.items}
    assert codes == {"ledger", "mass"}


def test_mass_floor_growth_heat_and_watch(monkeypatch, rules):
    rows = [row(n, 0.2 * (n + 1), reach=n % 2 == 0, growth_w=1.5 if n == 4 else 0.0,
                growth=20.0 if n == 4 else 0.0, mass=3000.0,
                bodies=[body(1, margin=50.0, m=member(0.01))]) for n in range(10)]
    out = [{"body": 1, "out_mm": 22.0, "height": 90.0, "foot_mm2": 4000.0}]
    rep = judge(monkeypatch, rules, ledger(rows, bodies_out=out, heat_zone=2))
    f = found(rep)
    assert f[("sim", "mass")] == "FAIL"
    assert f[("sim", "overturn")] == "OK" and f[("sim", "sway")] == "OK"
    assert f[("sim", "layer_floor")] == "WARN"               # 4 of 7 judged layers cannot reach the floor
    assert f[("sim", "growth")] == "INFO"
    assert f[("sim", "heat_zone")] == "FAIL"
    assert f[("sim", "watch")] == "INFO"


def test_floor_within_share_and_brim_only_zone(monkeypatch, rules):
    rows = [row(n, 0.2 * (n + 1), reach=n != 5) for n in range(10)]
    rep = judge(monkeypatch, rules, ledger(rows, heat_zone=2))
    f = found(rep)
    assert f[("sim", "layer_floor")] == "INFO"
    assert f[("sim", "heat_zone")] == "WARN"
    assert f[("sim", "watch")] == "INFO"                      # the one short layer scores 0.5


# ----------------------------------------------------------------- format_ledger()

def test_format_ledger_empty():
    assert S.format_ledger({"rows": []}) == "no layers"


def test_format_ledger_at_height():
    rows = [row(0, 0.2), row(1, 0.4, reach=False, bodies=[body(1, margin=12.0, m=member(0.05)), body(2)])]
    text = S.format_ledger(ledger(rows), at=0.45)
    assert text.startswith("layer n=1 z=0.4 mm") and "cannot reach the floor" in text
    assert "overturning margin 12.0" in text and "free member 9.0 mm2" in text
    assert "  body 2: 50.0 mm2" in text
    assert "cannot reach" not in S.format_ledger(ledger(rows), at=0.2)


def test_format_ledger_table():
    rows = [row(n, 0.2 * (n + 1), reach=n != 1,
                bodies=[body(1, margin=5.0, m=member(0.1))] if n % 2 else []) for n in range(5)]
    sim = ledger(rows, stride=2)
    full = S.format_ledger(sim, max_rows=None).splitlines()
    assert "one row per 2 layers" in full[0] and len(full) == 2 + 5
    assert "!" in full[3] and full[2].split()[-3:] == ["-", "-", "-"]
    thinned = S.format_ledger(sim, max_rows=2).splitlines()
    assert len(thinned) == 2 + 2 + 1                           # every 3rd row, plus the last
    even = S.format_ledger(sim, max_rows=3).splitlines()
    assert len(even) == 2 + 3                                  # every 2nd row ends on the last
    assert math.isclose(float(even[-1].split()[1]), 1.0)
