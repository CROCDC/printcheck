# Using printcheck from any project

printcheck is the one place that knows what makes a print fail on the
owner's printer (Bambu Lab A2L, 0.4 nozzle, textured PEI, bed slinger).
Every 3D project under `projects-3D/` runs it; nothing about printability
lives in a project. This page is the how-to. The rules and their reasons
are in `RULES.md`, the layer simulation in `SIMULATION.md`, the prints
that taught them in `FAILURES.md`.

## 1. From a project in the kailua layout (`lib/` + `parts/<part>/`)

Nothing to install. `lib/run.py` already ends with:

```python
sys.path.insert(0, <projects-3D/>)          # where printcheck lives
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
| `PRINTCHECK` | a dict overriding any `RULES` threshold, e.g. `{"max_unsupported_mm2": 400}`; say why next to it, dated. A threshold moves only with a reason |

A new project copied from `taller` gets the hook through `lib/run.py` and
`lib/part.py` (`Variant.rotation`, `Part.rotation_of`, `PrintFile.also`).

## 2. From the command line, on any file

```bash
PY=taller/.venv/bin/python           # any project venv: trimesh, shapely, numpy, scipy
P=printcheck/preflight.py

$PY $P part.stl [letters.stl] --rotate x,180      # loose STLs, body first, into print orientation
$PY $P project.3mf                                # a Bambu project: settings + geometry + simulation
$PY $P project.3mf --slice                        # ... and slice it with Bambu Studio's CLI, judge the G-code
$PY $P project.gcode.3mf                          # what the printer receives
$PY $P plate_1.gcode                              # bare G-code

$PY $P project.3mf --layers 30                    # the layer ledger, 30 rows
$PY $P project.3mf --at 45                        # everything at z = 45 mm
$PY $P project.3mf --json out.json --quiet-info   # keep the evidence, hide the INFO lines
$PY $P project.3mf --layer 0.16 --nozzle 0.4 --rules my_rules.json
$PY $P project.3mf --views out/                   # the figure: angle, plan, cut across y, cut across x
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
3. `python printcheck/calibrate.py` replays every corpus row. A failure the
   engine calls GO is the next rule; a held print it calls NO-GO is a rule
   to look at. `--sweep` adds every print file of every project.
4. Write the rule in `preflight.py` (or `simulate.py`), its threshold in
   `RULES`, its reason in `RULES.md`, the story in `FAILURES.md`. Run
   `calibrate.py` again and `tests/test_shapes.py` (synthetic shapes with a
   known outcome): green or it does not go in.

## 6. Troubleshooting

- `ModuleNotFoundError: trimesh`: use a project venv (`*/.venv/bin/python`).
- `Bambu Studio not found`: `--slice` and preset flattening need
  `/Applications/BambuStudio.app`; set `BAMBU_STUDIO` to another path.
- A big part takes 30-60 s: the ray-sampled wall thickness and the
  per-layer sections. The simulation samples every k-th layer past 250
  layers and says so in the `ledger` line.
- The simulation never blocks: on an internal error it reports `sim`
  `simulation skipped: ...` as INFO and the rest of the verdict stands.
  Report the message; it is a bug to fix here.
