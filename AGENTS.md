# AGENTS.md: working on printcheck as an AI agent

printcheck says whether a 3D print will fail on one specific printer
(Bambu Lab A2L, 0.4 nozzle, textured PEI, a bed slinger), with a measured
number behind every finding. Its thresholds come from real failed prints,
and the record of those prints (`corpus/`) is the test that every change
has to pass. Read this page before you touch anything; `README.md` is the
overview, `docs/` the detail.

## Orientation in one minute

| question | answer |
|---|---|
| where is a threshold? | `RULES` dict at the top of `preflight.py` |
| why does it have that value? | `docs/RULES.md` (table plus dated learnings) |
| which print taught it? | `docs/FAILURES.md`, `corpus/outcomes.tsv` |
| how does the layer simulation work? | `simulate.py`, `docs/SIMULATION.md` |
| what are the data contracts? | `docs/ARCHITECTURE.md` |
| how do I know I did not break it? | `pytest` (100 % coverage gate) and `printcheck-calibrate`, both green |
| files with a known verdict to try? | `examples/` (GO, GO WITH CAVEATS, NO-GO; rebuilt by `examples/build.py`) |
| who calls it? | the CLI, `layout.py` from sibling projects' `lib/run.py`, the `print-preflight` skill |

## Invariants: do not break these

1. **A threshold moves only with a reason, a date and a green
   `calibrate.py`.** The reason goes in `docs/RULES.md`, "Learnings". Never
   loosen a rule to make one file pass. If a part legitimately needs
   another value, that part sets it in its own `spec.PRINTCHECK` with a
   dated comment, and the default stays.
2. **Every finding carries its number.** The measured value, the
   threshold, and what to change. "Might be unstable" is not a finding;
   "slenderness 94 (> 60): tie the members together" is.
3. **Rules live here and only here.** Never copy a rule into a sibling
   project's `lib/` or into the `print-preflight` skill; they call this
   engine. A lesson from a failed print is written once, in this repo.
4. **The corpus is ground truth.** Do not edit an `outcome`, a `cause`
   or `rule_then` to match what the engine says. Rows change only when the
   owner reports something new. `rule_now` is updated after a replay.
5. **Never downgrade a FAIL silently.** Changing a FAIL to a WARN is a
   threshold change and follows invariant 1.
6. **A WARN is a decision for the owner, not noise.** Do not clear one
   with "same as last time": it has to be justified by evidence from the
   file at hand.
7. **The simulation never blocks the verdict.** An exception inside it
   becomes `INFO sim simulation skipped: ...`. Fix the bug; do not remove
   the guard.
8. **`views.py` is evidence, never a rule.** Nothing in it may FAIL.
9. **Use `section(mesh, z)` from `preflight.py` for every cut.** It
   guards against two bugs that cost real prints (see
   `docs/ARCHITECTURE.md`, "Invariants the code relies on").
10. **Do not rename finding codes** without updating the corpus
    `rule_now` column and noting it in `docs/RULES.md`: `calibrate.py`
    compares by `stage/code`.
11. **Coverage stays at 100 %, line and branch.** New code arrives with
    the tests that reach every branch. Do not add `# pragma: no cover` or
    lower `fail_under`; if a branch cannot be reached by any input, it is
    dead code: remove it and say so.
12. **`examples/` is generated.** Change `examples/build.py` and run it;
    never hand-edit a file under `examples/stl`, `examples/bambu` or
    `examples/workspace/demo/parts`. `tests/test_examples.py` fails on a
    stale file.
13. **The repository is self-contained.** Tests, examples and the corpus
    never read outside it. A corpus file goes into `corpus/files/`; paths
    in `outcomes.tsv` are relative to the repository root.

## Before and after any change

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # once
source .venv/bin/activate

python -m pytest                 # every test, coverage gate at 100 % (line + branch)
printcheck-calibrate             # must end "OK: 0 corpus row(s) contradict the engine"
```

A change to a threshold, a stage or the simulation also gets
`printcheck-calibrate --report corpus/report-<YYYY-MM-DD>.md` so the numbers
are kept. A change to `examples/build.py` is followed by
`python examples/build.py` and a commit of what it rewrote.

## Recipes

### A print failed: turn it into a rule

1. Ask for the photo of the bed and of the loose part's faces first. The
   owner clears the bed fast; the photo is the evidence.
2. Add the row to `corpus/outcomes.tsv` (columns in `corpus/README.md`).
   Copy the exact file sent into `corpus/files/<date>_<name>.<ext>` if the
   source project will overwrite it.
3. `printcheck-calibrate --only <id>`. Caught? Then say by which code and stop.
4. Not caught: find the measurement that separates this file from the
   held prints. `printcheck <file> --layers all`, `--at <z of failure>`,
   `--json out.json`. A rule without a separating measurement is a guess.
5. Implement it (see `docs/ARCHITECTURE.md`, "Extension points"): the
   threshold in `RULES`, the check in its stage, a synthetic shape in
   `tests/test_shapes.py` if boxes can reproduce the mechanism.
6. Document: the reason in `docs/RULES.md`, the story in
   `docs/FAILURES.md`, `rule_now` in the corpus row.
7. `pytest` (100 %) and `printcheck-calibrate` green. If the new rule
   refuses a held print, it is wrong or too tight; do not ship it.

### Explain a finding to someone

Do not relay the line; judge it. Name the rule, the number against the
threshold, the mechanism (`docs/RULES.md`, `docs/SIMULATION.md`) and the
print that taught it (`docs/FAILURES.md`), then what it means for this
part and the cheapest change that clears it (orientation, a brim, tying
members together, a different split).

### Debug "the engine says X but the print did Y"

1. Reproduce with the exact file and orientation (`--rotate` for STLs).
2. `--layers all` and `--at Z` around the height where it failed.
3. `--views out/` and look at the PNG: is the geometry what you think it
   is? A green suite has missed a feature that was modelled wrong before.
4. Record the outcome in the corpus either way.

### Add an example

Add the shape and where it goes to `examples/build.py`, run it, add the row
to the tables in `examples/README.md` and `README.md`, and an assertion of
its verdict to `tests/test_examples.py`. An example exists to show one
verdict for one clear reason; keep it that small.

### Add a printer, a material or a stage

`docs/ARCHITECTURE.md`, "Extension points". A new material needs its
entries in every per-material dict (`warp_length_mm`, `peel_mpa`,
`modulus_mpa` in `RULES`; `MATERIAL` in `simulate.py`) with sources.

## Traps

- **Not installed.** `ModuleNotFoundError: printcheck`: run
  `pip install -e ".[dev]"` in the venv. (Without installing, the scripts
  only work from a checkout whose folder is named `printcheck`.)
- **Missing `manifold3d`.** `test_plate_on_legs_pillar` fails because
  the boolean union falls back to separate bodies. Install the
  requirements; do not change the test.
- **Bambu Studio absent.** `--slice` WARNs and preset flattening falls
  back to material plus brim only. Expected on Linux and CI. Set
  `BAMBU_STUDIO` when it lives elsewhere.
- **Origin-centred meshes.** A loose STL centred on (0, 0) has negative
  coordinates; the heat-soak rule then reports the zone as unknown
  instead of judging it. Plate coordinates come only from a `.3mf`.
- **Test helpers come from `helpers.py`, never `conftest`.** pytest runs
  in importlib mode (the repository root is the package, under whatever
  folder name it was cloned into), and in that mode `from conftest import`
  fails. Shared shapes and lookups go in `tests/helpers.py`; `conftest.py`
  keeps only fixtures.
- **Coverage numbers from a partial run.** `pytest tests/one_file.py`
  still applies `fail_under = 100` and fails on coverage alone; add
  `--cov-fail-under=0` when you only want that file's result.
- **Slow files.** 30-60 s on a large part is normal. Do not add a
  timeout that kills the simulation.
- **Plate 1 only.** `load_project` reads `plate_1`; a multi-plate
  project gives a partial verdict.
- **Two `K05` rows.** `corpus/outcomes.tsv` has two rows with id `K05`
  (the tower-film print and a later rerun). `--only K05` selects both.

## Style

- Comments explain *why* (usually a dated print), never *what*. Match the
  existing voice: short, concrete, numbers in the sentence.
- Every Markdown file and comment is in English.
- Dates are absolute (`2026-10-15`), never "yesterday".
- No new dependency without a reason in this file and in
  `requirements.txt`.
