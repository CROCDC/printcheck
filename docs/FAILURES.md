# The prints that taught the validator

Every failure the owner's A2L has produced, in order: what was sent, what
came off the bed, why, which file carries the evidence, and the rule born
from it. The machine-readable version is `../corpus/outcomes.tsv` (one row
per print, held or not); `../calibrate.py` replays every row and checks
that each failure is still caught and no held print is refused. Read this
before judging a finding: most thresholds are a number because of one of
these prints.

The frame of the history: on 2026-10-15 the owner said the validator must
be cross-project and live at the root "so we never make these mistakes
again". It was reactive by construction (each rule arrived after its
failure). The layer simulation (`SIMULATION.md`) is the attempt to get
ahead of the next one.

## 2026-10-10: the curvature test set held (K01)

Eight TOMA USB POPA discs, radii 14 to 32, black PETG with white **PLA**
letters, face down, stock profile. Came out whole; one radius was chosen.
Kept because it is the one mixed-material print that held: the
`mixed_materials` rule that FAILs it today is the owner's policy (PLA and
PETG are a support pair because they do not bond), not a fit to this print.

## 2026-10-11: the label that fell apart in the hand (K02)

`v1_etiqueta_00_TOMA_USB_POPA.gcode.3mf`, sliced 18:26 and sent without any
validation. 19.3 mm disc, 0.8 mm thick, white PLA letters 0.6 mm deep in
black PETG, one 0.2 mm layer of body over the letters, six settings changed
at once from the set that had held (slower thinner concentric first layer,
white first, the A2L's real start G-code, +0.5 mm outline, bed 65).

Came off the bed deformed (oval, soft edges) and fell apart in the owner's
hand. Rebuilt from the model (`corpus/files/2026-10-11_etiqueta_00_rebuilt_*`,
`ESP=0.8 PROF_LETRA=0.6`) and replayed: **NO-GO**, mixed materials, one
layer of backing, wall thickness p05 0.24 mm, layer time 6.5 s median
(10 of 11 layers under 8 s), purge 17x the part.

Born here: `same_material`, `min_base_layers_over_inlay` (2), the thin-wall
FAIL under two layers, `min_layer_time_s` (8), and the discipline of one
change per print. The original G-code was not kept; the repo keeps only the
current design. Lesson for the corpus: copy the exact file that was sent.

## 2026-10-14: the plate of six held, illegible (K04)

Six labels, PETG + white at the HF preset (240/230), black first, outer wall
40 mm/s / 1000 mm/s2, 100 % zig-zag infill, no tower, factory first-layer
offset. Held to the end. The letters were illegible: white blobs, merged
letters, grey tint. Root cause in the lettering (strokes 0.30-0.61 and gaps
under the 0.34 mm Arachne floor, first layer with 0.50 mm lines), not in
adhesion. This is the recipe every later plate is measured against: it is
the one that stayed on the bed.

## 2026-10-14 and 2026-10-15: the prime tower film (K05)

Plate of seven plus coupon, PETG, stock speeds, white first, prime tower on
(Bambu's default). The second colour ends at layer 2, so the slicer prints
a tower two layers tall: a 0.4 mm, 20 x 19 mm film. It came off the
textured PEI, wrapped the nozzle and took labels with it; the same thing
had happened the day before. Read from the Bambu Studio source: the tower
is printed only up to the last colour change and has **no minimum
height**.

Born here: `tower_height` (FAIL under 1 mm, in the `gcode` stage since
2026-10-15 evening; until then it was a sentence in the learnings). The
part's recipe carries `enable_prime_tower: 0` for good; the A2L purges in
its rear chute.

## 2026-10-15: the frame of eight sticks (T01)

`cajonera_lijas` frame, taller. Printed on its back bars, black PLA,
textured PEI, auto brim. 90 mm tall on a 4100 mm2 base 56 wide, centre of
mass 28 mm inside the footprint: every adhesion rule green. Came off the
bed at 90 % and flew.

Sliced by height, above the 5 mm base it was eight separate sticks (rail,
post, diagonal, channel per side), 21-37 mm2 each, free for 80 mm until
the caps joined them. Slenderness 94 against under 30 for every print that
had held. Rebuilt from the model without the fix
(`corpus/files/2026-10-15_bastidor_v1_rebuilt.stl`, `COSTADO_T=0 MALLA_T=0`)
and replayed: `slender` FAIL and, in the simulation, each brace's tip
swaying 0.9 mm under the nozzle's 10 g push before the nozzle reaches it.

Born here: `check_members` (slenderness of every free-standing member,
WARN 40, FAIL 60), the move of the engine to `projects-3D/printcheck/` so
every project runs it, and today the member sway model. The fix in the
part: a 1.2 mm lattice panel per side tying rail and channel, brim
`outer_only` 5 mm.

## 2026-10-15, night: five plates, none held (K06-K09)

In order, after the owner recalibrated the A2L's first layer and flow that
afternoon:

| print | material | recipe | result |
|---|---|---|---|
| PLA plate of 7 + coupon, 100 % infill | PLA + PLA | stock speeds, white first, auto brim | discs off at layer 3-4 |
| PLA plate of 7 + coupon, 15 % | PLA + PLA | same | discs off at layer 3-4 |
| PETG plate of 7 | PETG HF + generic PETG at 255 | stock speeds, white first, 15 % | letters knocked off on layer 1, a disc off at 46 % |
| single NAV label (K09, `print/sliced/v1_etiqueta_01_TOMA_USB_NAV.gcode.3mf`, kept) | PETG HF + generic PETG | black first, outer wall 40/1000, 15 %, brim outer_only 5 | outer ring stayed, interior came apart at ~55 % |

What the first three share, PLA or PETG, 15 % or 100 %, and what the plate
that held (K04) did not: the stock outer wall at 200 mm/s / 5000 mm/s2 on
19 mm discs, the white letters laid first on layer 1 as lone islands (the
video shows the black's nozzle knocking them off), and `auto_brim` that
laid **nothing**: the sliced file said `brim_type = auto_brim` and the
G-code had zero Brim moves, because Bambu judges a 19 x 19 x 2.4 mm disc
stable. The validator had reported "brim auto_brim" as if a brim existed.
The 100 % infill was suspected first (`dense_infill`, born as a FAIL) and
cleared the same night: K04 held with it. It stays as a WARN.

The fourth print is the open one: the held recipe is back and the part
still came apart from the inside at half height. The engine's replay of
its G-code reads GO WITH CAVEATS; it is the one kept G-code of a missed
failure. Differences left against K04: the first-layer and flow
calibration of the afternoon, the white's 255 C preset, one label alone
instead of six. Next steps, one at a time, in kailua
`plans/etiquetas-calidad.md`: reset the first-layer offset, reprint
unchanged, then the white preset, then two labels per plate.

Born here: `brim_laid` (what the G-code actually lays), `small_part_speed`
(footprint under 25 mm with the outer wall over 100 mm/s), the demotion of
`dense_infill`, and the corpus itself: the owner had to be asked which
prints held because nothing recorded it.

## 2026-10-19: the frame on the cold rim (T07)

A plate of three for the sandpaper drawers: drawer 180, drawer 320 and a
frame, in a row along Y, placed by a one-off script
(`corpus/files/2026-10-19_build_plate.py`) with the frame last at y=265.
Its 57 mm footprint ran to y=293, 22 mm past the outer preconditioning zone
(49-271). The two plates before it (T05, T06, two frames and a drawer) had
every body inside the zone with only brims spilling 4 mm, and both held.

The drawers finished at layer 254; the frame came off its brim at layer 359
of 449 (z 71.8, 93 %), 18 mm short of its front, where the channels end.
The owner paused the job. Photo in the session of 2026-10-19.

A second loss rode on the first: the job stayed paused with the bed at
55 C, where PLA softens, and both finished drawers came out bowed along
their 1.2 mm front, while the drawers of the two earlier plates, cooled on
a bed switched off after FINISH, are straight. A pause keeps the bed hot;
a cancel switches it off. No file-side rule can see this: it is the
bambu-print skill's job to say "cancel, never pause" when a print is lost.

`heat_zone` had fired, as a WARN, and the session cleared it as "the same
warning as the two plates that held". It was not the same: there the brim
left the zone, here the most adhesion-sensitive body on the plate did.
Replayed today: **NO-GO**, `heat_zone` FAIL, body 3 (90 tall on 4101 mm2)
22 mm out.

Born here: `heat_zone` judged per body and without the brim, FAIL when a
body leaves the outer zone; the rule that a WARN is cleared only with
numbers measured on that file; and the plate layout moving into the
toolchain (`Plate` in the part manifest), which puts the delicate body at
the centre and refuses a layout that leaves the zone.

## What still has no rule

- A part that comes apart from the inside at half height with every rule
  green (K09). Candidates: first-layer squish (printer-side), the white's
  temperature, layer time of a single label. The coupon plan (research
  plan C5) and the one-change sequence decide.
- Everything the files cannot carry: a dirty plate (K06 came off an
  unwashed plate), wet filament, drafts, a recalibrated first layer. The
  verdict names them every time; the corpus records them in `cause`.

## How a failure becomes a rule

1. Photo of the bed and of the loose faces first. Then the row in
   `corpus/outcomes.tsv`, and the exact file into `corpus/files/` if the
   project will overwrite it (or the model parameters to rebuild it).
2. `printcheck-calibrate`: is it caught? By which code? If not,
   find the measurement on the file that separates it from the held prints
   (`--layers`, `--at Z` on the simulation; `--json` for the evidence).
3. The rule in `preflight.py` or `simulate.py`, its threshold in `RULES`,
   its reason in `RULES.md` (table and a dated learning), its story here.
4. `calibrate.py` green again: every failure caught, no held print
   refused. Then the project's `docs/WORKFLOW.md` gets the one-line lesson.
