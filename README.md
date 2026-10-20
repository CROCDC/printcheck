# printcheck

**A printability validator for FDM 3D prints, calibrated on real failures.**
Give it an STL, a Bambu Studio project (`.3mf`), a sliced project
(`.gcode.3mf`) or bare G-code. It answers **GO**, **GO WITH CAVEATS** or
**NO-GO**, and backs every finding with a measured number: the layer, the
area, the margin, the threshold it crossed. It exits 1 on NO-GO, so a
build can gate on it.

It is not a generic slicer lint. Every threshold is there because a real
print on one real printer failed, or nearly did, and the corpus of those
prints is in this repo. `calibrate.py` replays the corpus and refuses any
change that lets a known failure through or blocks a print that held.

> **Coming here as an AI agent?** Read [`AGENTS.md`](AGENTS.md) first. It
> has the invariants, the task recipes and the traps, in that order.

---

## Contents

- [What it catches](#what-it-catches)
- [Quick start](#quick-start)
- [Reading the output](#reading-the-output)
- [How it works](#how-it-works)
- [Using it from Python](#using-it-from-python)
- [Using it from a project](#using-it-from-a-project-the-kailua-layout)
- [Calibration: the corpus loop](#calibration-the-corpus-loop)
- [Repository map](#repository-map)
- [Documentation map](#documentation-map)
- [Assumptions and limits](#assumptions-and-limits)

---

## What it catches

The target machine is a **Bambu Lab A2L**: 330 x 320 x 325 mm, 0.4 mm
nozzle, textured PEI plate, a **bed slinger** (the bed moves in Y,
`printer_structure = i3`), PLA and PETG. Bed slingers shake tall parts
on every Y reversal, and many of the rules exist because of that.

| stage | what it judges |
|---|---|
| `settings` | one material across every filament of a part, a bed temperature per filament, the wall generator, infill density, declared overrides |
| `mesh:<part>` | watertight and wound, a whole number of layers, first-layer contact, features thinner than the slicer can lay, unsupported growth per layer, wall thickness |
| `inlay:<part>` | a second colour inside the first: what backs it, the body left over it, overlap, counters |
| `adhesion` | per body on the bed: centre of mass inside the footprint, height over the narrowest base and over the depth along Y, separate feet, single-bead strips, warp length per material, a plate temperature, a brim where any of that is at risk |
| `members` | every member that stands free while it prints, by slenderness (free height over the least radius of gyration of its section) |
| `sim` | **a layer-by-layer reprint**: mass and the bed's Y acceleration at every height, overturning margin per body, tip sway and base stress of each free member under the nozzle's drag, layer time against the cooling floor, printed mass, the bed's heat-soak zone, the layer to watch |
| `slice_info` | the slicer's own warnings, parts outside the plate, support, which layers carry each filament |
| `gcode` | layer times, outer walls crawling, purge against part mass, layers with no perimeter, the second colour's layers, prime tower gap and height, the brim actually laid, ironing, moves outside the printable area |
| `views` | not a rule: a PNG of the print from an angle, from above, and cut twice through its centre on a mm grid. A green verdict says the numbers are right, not the shape |

Every code, its threshold, its reason and its source:
[`docs/RULES.md`](docs/RULES.md).

## Quick start

Requirements: Python 3.10+ (developed on 3.12). Bambu Studio is optional:
without it `--slice` and preset flattening are skipped and the rest still
runs.

```bash
git clone https://github.com/CROCDC/printcheck.git
cd printcheck
python -m venv .venv && .venv/bin/pip install -r requirements.txt
```

The CLI must run from the **directory that contains `printcheck/`**,
because the package imports itself as `printcheck` and corpus paths are
relative to that parent (see [Assumptions](#assumptions-and-limits)):

```bash
cd ..                                   # the parent of printcheck/
PY=printcheck/.venv/bin/python
P=printcheck/preflight.py

$PY $P part.stl                                   # one STL, already in print orientation
$PY $P body.stl letters.stl --rotate x,180        # body + inlay colour, flipped face down
$PY $P project.3mf                                # settings + geometry + simulation
$PY $P project.3mf --slice                        # ... plus Bambu Studio's CLI slice, then the G-code
$PY $P project.gcode.3mf                          # exactly what the printer receives
$PY $P plate_1.gcode                              # bare G-code

$PY $P project.3mf --layers 30                    # the simulation ledger, 30 rows (or `all`)
$PY $P project.3mf --at 45                        # everything the simulation knows at z = 45 mm
$PY $P project.3mf --views out/                   # write out/project_views.png
$PY $P project.3mf --json out.json --quiet-info   # machine-readable evidence, no INFO lines
$PY $P project.3mf --layer 0.16 --nozzle 0.4 --rules my_rules.json   # override thresholds
```

Run the tests:

```bash
printcheck/.venv/bin/python printcheck/tests/test_shapes.py      # or: python -m pytest printcheck/tests
```

## Reading the output

A real run, on the frame that came off the bed at 90 % on 2026-10-15
(corpus row `T01`; trimmed):

```
OK   mesh:2026-10-15_bastidor_v1_rebuilt.stl overhang   worst unsupported growth 9.1 mm2 at z=77.9
OK   adhesion                                adhesion   every body stands on its footprint (brim ?)
FAIL members   slender   body 1: a member 17 mm2 at (79, -1) stands free from z=2 to 83 (82 mm),
                         least radius of gyration 0.87 mm: slenderness 94 (> 60): it sways under the
                         nozzle and is levered off the bed; tie the members together or print it another way
OK   sim       overturn  every body resists the bed's shake and the nozzle's push (least margin 469)
FAIL sim       sway      body 1: a member 17 mm2 at (-79, -2), free for 82 mm at z=83.8, sways 0.70 mm
                         at the tip under the nozzle's drag (f1 92.0 Hz, Q 1, base stress 0.98 MPa) (> 0.5 mm,
                         more than a bead): the nozzle catches the top and levers the member

    n       z      mm2  isl      s    s*  fan       g  acc_y    grow  margin  free   sway  lam
    0    0.20     4102    1   97.1    97    0     2.1   7997       0  548998     -      -    -
  290   58.20      215    8    2.2     7   80    24.3   6192       0     756    56   0.23   65
  406   81.40      144    6    1.5     7   80    29.3   5471       0     517    79   0.64   92

NO-GO: 2 FAIL, 0 WARN, 6 OK, 9 INFO
```

Every line is `LEVEL stage code message`:

| level | meaning |
|---|---|
| `FAIL` | the print is expected to fail; any FAIL makes the verdict **NO-GO** and the exit code 1 |
| `WARN` | a risk that needs a decision before printing; WARNs alone give **GO WITH CAVEATS** |
| `OK` | the rule ran and passed, with its number |
| `INFO` | context: what was measured, what was skipped and why (hidden by `--quiet-info`) |

Every ledger column is explained in
[`docs/SIMULATION.md`](docs/SIMULATION.md#reading-the-ledger).

`--json out.json` writes:

```jsonc
{
  "verdict": "NO-GO",                 // "GO" | "GO WITH CAVEATS" | "NO-GO"
  "rules": { "nozzle": 0.4, ... },    // the thresholds actually used, after overrides
  "findings": [
    { "stage": "sim", "level": "FAIL", "code": "sway",
      "msg": "body 1: a member ...",
      "data": { ... } }               // optional: the raw numbers behind the message
  ],
  "layers": [...],                    // per-layer G-code timings, when a G-code was judged
  "simulation": {...}                 // the full ledger
}
```

## How it works

```
 input file ──► loader ──► parts + settings ──► stages ──► Report ──► verdict / exit code
                                                                 └──► --json, --layers, --views
```

1. **Load.** STLs are loaded as they are (optionally rotated with
   `--rotate` and dropped onto z = 0 as one set). A `.3mf` is unzipped:
   meshes with their build transforms, `project_settings.config`,
   `slice_info.config` and, in a sliced project, the plate's G-code
   (`load_project`).
2. **Group.** Parts whose footprints overlap in XY are one group: the
   largest body and the inlays inside it. Separate objects on one plate
   are judged apart (`groups`), so two small roofs are not summed into
   one large overhang.
3. **Stages.** Each stage writes findings into one shared `Report`:
   `check_settings`, `check_mesh`, `check_inlay`, `check_adhesion`,
   `check_members`, `check_simulation` (in `simulate.py`),
   `check_slice_info`, `check_gcode`. The simulation never blocks: on an
   internal error it reports `simulation skipped` as INFO and the rest of
   the verdict stands.
4. **Verdict.** Any FAIL is NO-GO; otherwise any WARN is GO WITH CAVEATS;
   otherwise GO.

The simulation (`simulate.py`) is the part that tries to get ahead of the
next failure instead of encoding the last one. It slices the meshes
itself, and for every layer estimates the mass printed so far, the A2L's
mass-limited Y acceleration, the overturning moment against weight plus
first-layer peel, and each free member's sway as a cantilever excited by
the nozzle and the bed. Its physics, constants and sources are in
[`docs/SIMULATION.md`](docs/SIMULATION.md); the constants still marked
*provisional* wait for calibration coupons.

## Using it from Python

```python
import trimesh
from printcheck import check_print

mesh = trimesh.load("part.stl", force="mesh")          # already in print orientation, bed at z = 0
rep = check_print(
    parts=[{"name": "part", "mesh": mesh, "extruder": 1}],
    settings={"filament_type": ["PETG"], "printer_structure": "i3"},
    rules={"layer": 0.2},                                 # any key of preflight.RULES
)
rep.failed                  # True when any FAIL
rep.items                   # [{"stage", "level", "code", "msg", "data"?}, ...]
rep.simulation              # the layer ledger, when the simulation ran
```

`parts`: the largest mesh of each group is the body; the rest are
companions (a second colour) in the same frame. `settings` uses Bambu's
own key names; per-filament keys are lists, one entry per filament, as in
`project_settings.config`. `presets.resolve(spec_bambu)` builds that
dictionary from Bambu Studio's installed presets.

## Using it from a project (the kailua layout)

Projects laid out as `lib/` + `parts/<part>/` (the "kailua layout") call
`printcheck.layout.printability` from their `lib/run.py` after the part's
own checks, on **every print file the part declares**, outside the part's
manifest so a part cannot opt out. A FAIL there fails the project's suite.
A part can tune it from its `spec.py`:

| key | effect |
|---|---|
| `BAMBU` | `{"machine", "process", "process_overrides", "filaments": [(preset, colour[, overrides])]}`: the presets are flattened so the engine judges with the real speeds, cooling and slow-down ramp |
| `LAYER` | layer height for every layer-based rule (default 0.2) |
| `MATERIAL` | filament type when `BAMBU` names none |
| `PRINTCHECK` | a dict overriding any `RULES` threshold, e.g. `{"max_unsupported_mm2": 400}`, with the reason and date in a comment next to it |

The `print-preflight` Claude Code skill is a thin launcher over
`preflight.py` (environment variable `PRINTCHECK` points at it). It holds
the review workflow, never a copy of the rules. Full how-to:
[`docs/USAGE.md`](docs/USAGE.md).

## Calibration: the corpus loop

`corpus/outcomes.tsv` has one row per print sent to the printer, held or
not: the file, the recipe, the outcome, the observed failure, the cause,
what the validator said then and what it says now. `corpus/files/` keeps
the exact files that failed when the source project would overwrite them,
or their geometry rebuilt from the model of the day.

```bash
python printcheck/calibrate.py               # replay every corpus row
python printcheck/calibrate.py --sweep       # plus every print file of every sibling project
python printcheck/calibrate.py --report printcheck/corpus/report-<date>.md
```

It holds every verdict against the outcome. A failed print must be NO-GO;
a held print must not be. It exits 1 on a **new miss** or a **false
alarm**. A miss the corpus already admits (`rule_now` starting with
`missed`) is printed as the open problem it is and does not fail the run.

State on 2026-10-20, replayed inside the workspace: **0 contradictions**.
Of the 4 failed prints with a file, 3 are NO-GO (K02, T01, T07) and 1 is
an admitted miss (K09, a label that came apart from the inside with every
rule green); none of the 7 held prints with a file is refused. Rows with
no surviving file (K01, K04-K08) are listed but cannot be replayed.

When a print fails, the loop is:

1. Photo of the bed and of the loose part's faces, before anything else.
2. A row in `corpus/outcomes.tsv`; the exact file into `corpus/files/`.
3. `calibrate.py`: is it caught? If not, find the measurement that
   separates it from the held prints (`--layers`, `--at Z`, `--json`).
4. The rule in `preflight.py` or `simulate.py`, its threshold in `RULES`,
   its reason (dated) in `docs/RULES.md`, its story in `docs/FAILURES.md`.
5. `calibrate.py` and `tests/test_shapes.py` green, or it does not go in.

## Repository map

```
printcheck/
├── __init__.py       public API: check_print(), material_of(), RULES, Report
├── preflight.py      the engine and CLI: RULES (every threshold), Report, loaders,
│                     the settings/mesh/inlay/adhesion/members/slice_info/gcode stages
├── simulate.py       the layer-by-layer simulation (the `sim` stage, --layers, --at)
├── views.py          the figure: angle, plan, two cuts (evidence, never a rule)
├── presets.py        Bambu Studio presets flattened along their `inherits` chain
├── layout.py         adapter for projects in the kailua layout (lib/run.py calls printability)
├── calibrate.py      replays the corpus; per-rule table; exit 1 on a new miss or false alarm
├── tests/
│   └── test_shapes.py   synthetic shapes with known outcomes (plain script or pytest)
├── corpus/
│   ├── outcomes.tsv     ground truth: every print and what became of it
│   ├── files/           the failed files, as sent or rebuilt
│   ├── report-*.md      dated calibration reports
│   └── README.md        the column dictionary
├── docs/
│   ├── USAGE.md         how-to: project hook, CLI, skill, reading a verdict, closing the loop
│   ├── RULES.md         every threshold: default, why, source; dated learnings
│   ├── SIMULATION.md    the simulation's models, ledger columns, constants and sources
│   ├── FAILURES.md      the prints that failed, why, and the rule each one produced
│   └── ARCHITECTURE.md  data contracts and extension points, for changing the code
├── AGENTS.md         the guide for AI agents working on this repo
└── requirements.txt
```

## Documentation map

| if you want to | read |
|---|---|
| run it on a file or wire it into a project | [`docs/USAGE.md`](docs/USAGE.md) |
| know why a threshold has its value | [`docs/RULES.md`](docs/RULES.md) |
| understand a `sim` finding or a ledger column | [`docs/SIMULATION.md`](docs/SIMULATION.md) |
| learn which print taught which rule | [`docs/FAILURES.md`](docs/FAILURES.md) |
| change the code: contracts, data flow, adding a stage | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| add or read a corpus row | [`corpus/README.md`](corpus/README.md) |
| work on it as an AI agent | [`AGENTS.md`](AGENTS.md) |

## Assumptions and limits

- **One printer.** The defaults describe a Bambu Lab A2L with a 0.4 mm
  nozzle on textured PEI (`simulate.py` `MACHINE`, `PROCESS`,
  `MATERIAL`). Another machine needs its own numbers and its own corpus
  before the verdicts mean anything.
- **It lives inside a workspace.** It was split out of a folder of 3D
  projects (`projects-3D/`) and still sits in it: the package is imported
  as `printcheck` from that parent directory, `corpus/outcomes.tsv` paths
  are relative to it, and some rows point at files in sibling projects
  (`taller/…`, `kailua-tablero/…`). In a bare clone those rows report
  `FILE MISSING` and `calibrate.py` exits `NOT OK` (8 rows on
  2026-10-20); the rows under `printcheck/corpus/files/` replay
  anywhere. `--sweep` scans sibling folders that have `parts/`.
- **Bambu Studio** is found at
  `/Applications/BambuStudio.app/Contents/MacOS/BambuStudio`; set
  `BAMBU_STUDIO` for another path. Without it, `--slice` and preset
  flattening are skipped.
- **`manifold3d`** is needed for boolean unions in the tests; without it
  `test_plate_on_legs_pillar` fails because the legs and the plate stay
  separate bodies.
- **One plate per project.** A `.3mf` is read for `plate_1` only (its
  G-code, `plate_1.json`); a multi-plate project needs one file per plate.
- **What the files cannot carry** is never judged: a dirty plate, wet
  filament, drafts, a recalibrated first layer. The verdict cannot see
  them, and the corpus records them in `cause`.
- **Speed.** A large part takes 30-60 s (ray-sampled wall thickness and a
  section per layer). Past 250 layers the simulation samples every k-th
  layer and says so in its `ledger` line.
