# Architecture: data contracts and extension points

This page is for changing the code. Running it is in `USAGE.md`, the
thresholds in `RULES.md`, the physics in `SIMULATION.md`.

## Entry points

| entry point | used by | what it runs |
|---|---|---|
| `preflight.py` `main()` (CLI) | people, `calibrate.py` (as a subprocess), the `print-preflight` skill (via `runpy`) | load each file by extension, every stage that applies, print, `--json`, exit 1 on any FAIL |
| `printcheck.check_print(parts, settings, rules)` | `layout.py`, `tests/test_shapes.py`, any Python caller | the geometry stages (`mesh`, `inlay`, `adhesion`, `members`, `sim`) on meshes already in memory; no settings, slice_info or G-code stages |
| `layout.printability(ctx, section)` | `lib/run.py` of every kailua-layout project | `check_print` on every print file a part declares, then `views.render` |

Both import paths work: `preflight.py` checks `__package__` and, when it
runs as a script, puts the parent directory on `sys.path` and imports
`printcheck.simulate` / `printcheck.views`. Keep that pattern for any new
module the engine imports lazily.

## Data flow (CLI)

```
files ─┬─ *.stl ───► trimesh.load ─► [--rotate, drop to z=0] ─► parts ─┐
       │                                                               ├─► run_parts      (mesh, inlay; per group)
       ├─ *.3mf ───► load_project ─► settings, parts, slice_info,      │   check_adhesion
       │                             plate, gcode (plate_1 only)  ─────┤   check_members
       │                             └─► check_settings                │   check_simulation (simulate.py)
       │                                                               └─► _views (--views)
       │             slice_info ─► check_slice_info
       │             gcode ──────► check_gcode            (or --slice ─► slice_project ─► the same two)
       └─ *.gcode ──► check_gcode
                                        all of them write into ONE Report ─► print / --json / verdict
```

## Contracts

### `parts`

```python
[{"name": str,              # shown in stage names: "mesh:<name>"
  "mesh": trimesh.Trimesh,  # in PRINT orientation, bed at z = 0 (3MF: plate coordinates)
  "extruder": int}]         # 1-based filament slot
```

- `groups(parts)` unites parts whose XY bounding boxes overlap. In each
  group the largest volume is the **body** and the rest are its
  **companions** (inlays, a second colour). Separate objects on one plate
  are separate groups and are judged apart.
- A part with its lowest point above the plate sits on another part and
  is skipped by `check_adhesion`.
- Coordinates matter for the heat-soak rule: a mesh centred on the origin
  (negative x or y) is treated as "not plate coordinates" and the zone is
  not judged.

### `settings`

The keys of Bambu Studio's `project_settings.config`, with Bambu's own
names. Per-filament keys are **lists**, one entry per filament
(`filament_type: ["PETG", "PETG"]`); `_lst(v, i)` reads one entry from a
list or a scalar. `presets.resolve(spec.BAMBU)` produces the same shape
from installed presets. Missing keys fall back to the stock A2L process
and material values in `simulate.py` (`PROCESS`, `MATERIAL`), so an empty
dict is a valid input.

### `rules`

`preflight.RULES`: a flat dict, every threshold in one place. Overrides
merge on top (`dict(RULES, **override)`) from `--rules file.json`,
`--nozzle` / `--layer`, a part's `spec.PRINTCHECK`, or the `rules`
argument of `check_print`. A per-material threshold is a dict keyed by
filament type (`warp_length_mm`, `peel_mpa`, `modulus_mpa`).

### `Report`

```python
rep.stage = "adhesion"                         # set by each stage before it writes
rep.fail(code, msg, **data)                    # also .warn / .ok / .info
rep.items  -> [{"stage", "level", "code", "msg", "data"?}]
rep.failed -> bool                             # any FAIL
rep.simulation                                 # set by check_simulation: the ledger dict
rep.layers                                     # set by check_gcode: per-layer time, colour changes, acc_y, volume per tool
```

Rules for a finding:

- `code` is a short stable snake_case id (`slender`, `sway`,
  `layer_time`). `calibrate.py` keys its per-rule table on
  `<stage prefix>/<code>`; renaming a code breaks the comparison with old
  reports and with `rule_now` in the corpus.
- `msg` carries the **measured number, the threshold and what to do**:
  "slenderness 94 (> 60): ... tie the members together". A finding with no
  number is not a finding.
- `data` keeps the raw values for `--json`.
- Emit `OK` with the number when a rule passes, so a reader sees it ran.

### The simulation ledger

`simulate(parts, settings, rules)` returns:

```python
{"step", "stride", "layer",          # mm between sampled layers; every k-th layer past sim_max_layers
 "rows": [ {...per sampled layer: z, area, islands, time, mass_g, acc_y, bodies[{body, margin, ...}], members...} ],
 "n_bodies", "material", "modulus_mpa", "density", "peel_mpa",
 "heat_zone",                         # 0 inside the inner heat-soak area, 1 inner-outer, 2 outside, None unknown
 "bodies_out", "brim_mm", "brim_type", "cooling": {...}}
```

`check_simulation` reads the ledger and writes the `sim` findings;
`format_ledger(sim, max_rows=..., at=Z)` prints it. Column meanings are
in `SIMULATION.md`, "Reading the ledger".

### `--json`

`{"verdict", "rules", "findings": rep.items, "layers": rep.layers,
"simulation": rep.simulation}`, with `default=str` for anything numpy.
`calibrate.py` depends on `verdict` and on `findings[*].stage/level/code`.

## Extension points

### A new rule in an existing stage

1. The threshold goes in `RULES` with a one-line comment (and the date if
   it came from a print).
2. The check goes in the stage function, with `rep.fail/warn/ok` and the
   number in the message.
3. The reason goes in `docs/RULES.md` (table row plus a dated learning),
   the story in `docs/FAILURES.md` when a print taught it.
4. A synthetic shape in `tests/test_shapes.py` when the mechanism can be
   built from boxes.
5. `calibrate.py` green.

### A new stage

Write `check_<stage>(..., rules, rep)` that sets `rep.stage` first, add
it to `check_print` in `__init__.py` when it judges geometry, and to
`main()` in `preflight.py` for each input type it applies to. Add a row to
the stage tables in `README.md` and `docs/USAGE.md`.

### Another printer

The machine lives in `simulate.py` (`MACHINE`: accelerations, the
mass-limited acceleration curve, heat-soak rectangles, the mass limit;
`PROCESS` and `MATERIAL`: stock profile values) and in `RULES`
(`nozzle`, `bead`, `printer_structure` defaults). A second printer should
become a profile selected by `printer_model` from the settings rather than
edited constants, and needs its own corpus rows before its verdicts are
trusted.

## Invariants the code relies on

- **One `section(mesh, z)` for every cut.** It nudges the plane off a
  vertex that lies on it (a bore's lowest vertex on a layer's mid-plane
  once lost 711 mm² of section) and keeps every section in the mesh's own
  XY frame (trimesh's `to_2D()` used to pick a frame per section and put
  letters 4.8 mm off their body). Do not call trimesh's section directly.
- **The simulation never raises.** `check_simulation` catches any
  exception and reports `simulation skipped: ...` as INFO. A crash there
  is a bug to fix, but it must not hide the other stages' verdict.
- **`views.render` is evidence, never a rule.** It returns `None` without
  matplotlib and the caller WARNs; nothing in it may FAIL a print.
- **Companions are judged with their body.** The overhang test runs on
  the union of the body and its inlays (`fill=` in `check_mesh`), because
  the body alone reads every inlay pocket as empty.
