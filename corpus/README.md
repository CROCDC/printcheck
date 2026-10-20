# corpus: every print and what became of it

`outcomes.tsv` is the ground truth the rules are calibrated on: one row per
print sent to the A2L, held or not. `calibrate.py` (one level up) replays
the engine over every row that still has a file and reports which rule
fired on which outcome. A threshold may move only if the replay stays
green: every failure still caught by at least one FAIL, no held print
turned into a NO-GO.

`files/` keeps what the projects no longer have: the geometry of a failed
print rebuilt from its model with the parameters of the day (`rebuilt` in
the `kept` column), because the repos keep only the current version and
there is no git. A rebuilt file is a reconstruction: it says so in `notes`.

## Columns

| column | meaning |
|---|---|
| `id` | K = kailua-tablero, T = taller, P = the panel; numbered in print order |
| `date` | when it was printed (absolute) |
| `file` | path relative to `projects-3D/`; `+` joins the STLs of one print (body first); `-` when nothing survives |
| `kept` | `yes` the file as sent, `rebuilt` re-rendered from the model, `no` nothing |
| `rotate` | for loose STLs only: `--rotate` that puts them in print orientation |
| `material` | what was loaded, as the owner reported it |
| `recipe` | what differed from the stock profile, in words |
| `outcome` | `held`, `came_off`, `deformed`, `broke`, `came_apart`, `nozzle_wrap`, `aborted`, `unknown` (owner to confirm); `+` joins two |
| `failed_at` | layer, height or percent when known |
| `hms` | the printer's HMS code when known or inferred |
| `observed` | what the owner saw |
| `cause` | the best explanation so far, or `open` |
| `rule_then` | what the validator said at the time |
| `rule_now` | what it says on replay today |
| `notes` | provenance, what was born here, what to ask |

## Adding a row

After every print, held or not. If it failed: ask for the photo of the bed
and of the loose pieces' faces first (the owner clears the bed fast), copy
the exact file that was sent into `files/` if the project will overwrite
it, fill the row, run `python printcheck/calibrate.py`. If the replay does
not catch it, that is the next rule (`docs/FAILURES.md`, "how a failure
becomes a rule").
