# Using printcheck from any project

printcheck is the one place that knows what makes a print fail on the
owner's printer (Bambu Lab A2L, 0.4 nozzle, textured PEI, bed slinger).
Every 3D project of the owner's runs it; nothing about printability
lives in a project. Install it once with `pip install -e ".[dev]"` from
the checkout (or point a project at a checkout named `printcheck`).
This page is the how-to. The rules and their reasons are in `RULES.md`,
the layer simulation in `SIMULATION.md`, the prints that taught them in
`FAILURES.md`.

## 0. printcheck is not the design target

**Read this before designing a part with printcheck in the loop.**

A part exists to do a job: fit the thing it was measured against, carry
its load, assemble, last. printcheck answers one narrower question: will
this file come off the A2L in one piece? A green verdict is necessary,
never sufficient. It does not know what the part is for, and it cannot
see a fit that is wrong, a wall too weak for its load, a feature that was
left out, or a clearance that was guessed.

So designing *to the validator* is wrong: reading `preflight.py`, its
thresholds or its tests to find the shape that passes, instead of
designing the part that works and then asking whether it prints. Every
one of these is that mistake, and none of them is allowed:

- **Landing just under a threshold.** Thickening a member until its
  slenderness reads 59 against a FAIL at 60, or a footprint until the
  aspect reads 2.9 against 3. The numbers are calibrated on a handful of
  prints; the threshold is where failures started, not a safe line. Fix
  the mechanism the finding names (tie the members together, widen the
  base, print it another way), with margin.
- **Overriding a threshold to go green.** A `PRINTCHECK` entry in
  `spec.py` (or `--rules`) is for a measured reason specific to that part,
  written next to it with a date and the evidence: a test print that held,
  a measurement. "So the suite passes" is not a reason. A finding you think
  is wrong goes to the owner and to the corpus (`corpus/outcomes.tsv`),
  where the threshold gets recalibrated for everyone.
- **Routing around what a rule can see.** Splitting one part into
  separate objects so the rules judge them apart, nudging bodies on the
  plate, adding tabs, ribs or sacrificial posts that serve only the
  number, or choosing a print orientation that loads the layers along
  the part's working stress, because that orientation reads green.
- **Deleting function the validator cannot see.** Dropping a groove,
  a fillet, a thread, a snap or a pocket because it raised a WARN, without
  saying so. The oval lid's groove (2026-10-20) went out modelled solid
  with every check green; the part did not fit.
- **Treating green as done.** A GO says nothing about whether the part
  does its job. The part's own checks (fits against the owner's
  measurements, loads, assembly) and the `views` figure come first.

The right order:

1. Design for the function: the measurements, the loads, the fits, how it
   assembles. Write those down as the part's own checks.
2. Run printcheck. A FAIL is information about the physics of the print:
   fix its cause in a way that keeps the function.
3. When the only fix that clears a finding would hurt the function, stop
   and bring the trade-off to the owner, with both numbers. Never resolve
   it silently in either direction.
4. Say what printcheck cannot judge for this part, every time.

## 1. From a project in the kailua layout (`lib/` + `parts/<part>/`)

Nothing to install when the projects sit next to a checkout named
`printcheck` (the owner's `projects-3D/` workspace). `lib/run.py` already
ends with:

```python
sys.path.insert(0, <the folder that holds printcheck/>)   # or pip install it
from printcheck import layout as printcheck
...
printcheck.printability(ctx, report.Section(last + 1, "PRINTABILITY (projects-3D/printcheck)"))
```

It runs after the part's own checks on **every print file the part
declares** (`print_files` in `part.py`), outside the manifest so a part
cannot leave it out: the variant turned into print orientation, its
companion colours in the same frame, a cut piece as the bodies it splits
into. A FAIL there fails the suite.

What the part tells it, all optional, in `spec.py`:

| key | effect |
|---|---|
| `BAMBU` | `{"machine", "process", "process_overrides", "filaments": [(preset, colour[, overrides])]}`. When Bambu Studio is installed the presets are flattened (`printcheck/presets.py`) and the engine judges with the real speeds, cooling and slow-down ramp; without it only the material and the brim reach it |
| `LAYER` | layer height for every layer-based rule (default 0.2) |
| `MATERIAL` | filament type when `BAMBU` names none |
| `PRINTCHECK` | a dict overriding any `RULES` threshold, e.g. `{"max_unsupported_mm2": 400}`; say why next to it, dated, with the evidence (a test print that held, a measurement). Never to make the suite pass (section 0) |

A new project copied from `taller` gets the hook through `lib/run.py` and
`lib/part.py` (`Variant.rotation`, `Part.rotation_of`, `PrintFile.also`).
The smallest working example of the layout is `examples/workspace/demo`:
what the adapter needs from a project is `lib/printing.py` with
`orientation(spec)` and `pieces(mesh, cut)`, and a `ctx` with `part`,
`spec` and `meshes`.

## 2. From the command line, on any file

```bash
# `printcheck` is the installed command; `python preflight.py` is the same thing
P=printcheck

$P part.stl [letters.stl] --rotate x,180      # loose STLs, body first, into print orientation
$P project.3mf                                # a Bambu project: settings + geometry + simulation
$P project.3mf --slice                        # ... and slice it with Bambu Studio's CLI, judge the G-code
$P project.gcode.3mf                          # what the printer receives
$P plate_1.gcode                              # bare G-code

$P project.3mf --layers 30                    # the layer ledger, 30 rows
$P project.3mf --at 45                        # everything at z = 45 mm
$P project.3mf --json out.json --quiet-info   # keep the evidence, hide the INFO lines
$P project.3mf --layer 0.16 --nozzle 0.4 --rules my_rules.json
$P project.3mf --views out/                   # the figure: angle, plan, cut across y, cut across x
```

Exit code 1 on NO-GO (any FAIL), so a pipeline can gate on it.

## 2b. The figure: look at the shape, not only the numbers

Every print file a project verifies also gets
`parts/<part>/previews/<file>_views.png`, written by `printcheck/views.py`
from `layout.printability` (the CLI writes it with `--views DIR`): the
bodies from an angle and from above, and two cuts through the centre of the
main body, across y and across x, filled, on a 10 mm grid, with the bbox
of each cut in its title. Companion colours are drawn in their own colour.

Why: on 2026-10-20 the oval lid's groove, the one feature that fits the
deck ring, was modelled solid. Every check was green, because no check
knew the groove existed, and the print went out. The owner saw it in the
slicer, after the fact. The suite now puts the picture in front of whoever
runs it; the INFO line names the file and says "Look at it". It is
evidence, not a rule: nothing in the figure fails a print.

A part can choose its own cut planes by calling `views.render(parts, path,
cuts=[("y", 0), ("x", 30)])` from its own checks when the centre planes
miss the feature that matters.

## 3. From the `print-preflight` skill

`~/.claude/skills/print-preflight/scripts/preflight.py` is a launcher for
this engine with the same arguments. The skill is the review workflow (what
to run, how to judge a finding, what to say about the gaps); the rules are
here. A new rule goes in `printcheck/`, never in the skill.

## 4. Reading the verdict

```
LEVEL stage        code              message with the measured numbers
GO | GO WITH CAVEATS | NO-GO: n FAIL, n WARN, n OK, n INFO
```

| stage | judges |
|---|---|
| `settings` | the project's slicer settings: materials, bed temperature per filament, wall generator, infill, declared overrides |
| `mesh:<part>` | each part's mesh: integrity, layers, contact, features the slicer drops, overhang growth, wall thickness |
| `inlay:<part>` | a second colour inside the first: backing, overlap, counters |
| `adhesion` | per body on the bed: centre of mass, aspect, pillars, strips, warp length, plate temperature, brim, small part at full speed |
| `members` | free-standing members by slenderness |
| `sim` | the layer simulation: overturning margin, member sway, cooling floor, mass, heat zone, the layer to watch |
| `slice_info` | the slicer's own warnings, outside, support, which layers carry each filament |
| `gcode` | the real G-code: layer times, outer wall crawling, purge, no-perimeter layers, second colour, tower gap and height, brim laid, ironing, outside |

Every code with its meaning and typical fix: `RULES.md`, "Reading the
findings". Judge, do not relay: a FAIL names a rule and a number; say what
it means for this part. Never downgrade a FAIL silently.

## 5. Closing the loop after a print

1. Held or not, add a row to `corpus/outcomes.tsv` (columns in
   `corpus/README.md`). If the project will overwrite the file, copy the
   exact one that was sent into `corpus/files/`.
2. If it failed: ask for the photo of the bed and of the loose pieces'
   faces before anything else.
3. `printcheck-calibrate` replays every corpus row. A failure the
   engine calls GO is the next rule; a held print it calls NO-GO is a rule
   to look at. `--sweep DIR` adds every print file of every kailua-layout
   project under DIR.
4. Write the rule in `preflight.py` (or `simulate.py`), its threshold in
   `RULES`, its reason in `RULES.md`, the story in `FAILURES.md`. Run
   `printcheck-calibrate` again and `pytest` (synthetic shapes with a known
   outcome, 100 % coverage): green or it does not go in.

## 6. Troubleshooting

- `ModuleNotFoundError: trimesh` or `printcheck`: install the package in
  the venv you run with (`pip install -e ".[dev]"`).
- `Bambu Studio not found`: `--slice` and preset flattening need
  `/Applications/BambuStudio.app`; set `BAMBU_STUDIO` to another path.
- A big part takes 30-60 s: the ray-sampled wall thickness and the
  per-layer sections. The simulation samples every k-th layer past 250
  layers and says so in the `ledger` line.
- The simulation never blocks: on an internal error it reports `sim`
  `simulation skipped: ...` as INFO and the rest of the verdict stands.
  Report the message; it is a bug to fix here.
