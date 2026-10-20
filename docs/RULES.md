# printcheck thresholds and where they come from

`RULES` in `preflight.py` holds the numbers; this file holds the reasons.
A threshold changes only with a reason and a date in the learnings below,
and only if `calibrate.py` stays green on the corpus (`corpus/outcomes.tsv`).
This is the canonical copy; the `print-preflight` skill points here.

## Geometry (STL / 3MF parts)

| rule | default | why | source |
|---|---|---|---|
| `nozzle` / `bead` | 0.4 / 0.42 mm | Bambu's default line width on a 0.4 nozzle is 0.42 | project_settings `line_width` |
| `min_bead_pct` | 0.85 of nozzle (0.34 mm) | Arachne `min_bead_width`: anything thinner than two beads is redrawn as one bead this wide, and nothing thinner than this is laid | [Bambu wiki, wall generator](https://wiki.bambulab.com/en/software/bambu-studio/wall-generator) |
| `min_feature_pct` | 0.25 of nozzle (0.10 mm) | Arachne `min_feature_size`: below it the feature is not printed at all | same |
| `min_layers` | 4 | under four layers a part is a film: it warps, peels and snaps. Service bureaus put the FDM floor at 0.8 mm | [Protolabs FDM design](https://www.protolabs.com/en-gb/services/3d-printing/fused-deposition-modelling/), [Hubs key considerations](https://www.hubs.com/knowledge-base/key-design-considerations-3d-printing/) |
| `min_wall` | 0.8 mm | two perimeters; one perimeter is 0.45 and a real wall is two | [Prusa KB, modeling for 3D printing](https://help.prusa3d.com/article/modeling-with-3d-printing-in-mind_164135) |
| thin-wall FAIL | p05 under 2 layers | one layer of skin is a hinge; the 0.8 mm label had 0.24 mm over its letters and bent there | learnings, 2026-10-11 |
| `min_island_mm2` | 1.0 mm2 | smaller islands are dropped or smeared; the counters of A, O, B in small text live here | measured on the labels: counters 0.3-0.9 mm2 |
| `max_unsupported_mm2` | 300 mm2 per layer | growth past a 45 degree flare prints into air; 45 is the unsupported limit without special cooling | [Prusa KB](https://help.prusa3d.com/article/modeling-with-3d-printing-in-mind_164135), [Hubs FDM design](https://www.hubs.com/knowledge-base/how-design-parts-fdm-3d-printing/); Kailua `OVERHANG_AREA_PER_LEVEL` |
| `min_contact_ratio` | 0.5 | first layer under half the widest layer: little holding what sits on it | warping guides: [Bambu wiki warping](https://wiki.bambulab.com/en/knowledge-sharing/printed-model-warping) |
| `min_base_layers_over_inlay` | 2 | the body over a two-colour inlay is the only thing holding the colours together; one layer failed | learnings, 2026-10-11 |
| text strokes | >= nozzle, better 2 beads | horizontal features in multiples of the extrusion width; bold fonts for small text; the 0.2 nozzle halves the floor | [Reiman FDM guidelines](https://additive.reiman.pt/wp-content/uploads/2025/09/Design-Guidelines-FDM.pdf), [Bambu forum, thin text](https://forum.bambulab.com/t/need-help-with-slicing-settings-for-thin-walls/205548) |
| elephant foot | compensation 0.15, chamfer the bed edge | the first layer squishes outward; a chamfer hides it | [Hubs FDM design](https://www.hubs.com/knowledge-base/how-design-parts-fdm-3d-printing/) |

## Bed adhesion (per body, as printed)

`check_adhesion` splits the plate into bodies and judges each one standing on
the bed by itself: a small part next to a big one has to hold on its own.
Bodies whose lowest point is above the plate sit on another part (an inlay)
and are skipped.

| rule | default | why | source |
|---|---|---|---|
| `min_com_margin` | 2 mm inside the footprint's convex hull | a centre of mass outside the footprint leans; every nozzle pass pushes it further until it pops off | statics; the leaning-beam test case |
| `max_aspect_warn` / `_fail` | 3:1 / 6:1, height over the footprint's narrowest width | tall parts on narrow bases rock as they grow and the nozzle knocks them off; past 3:1 add a brim, past 6:1 change the orientation or add a raft/support | [Bambu wiki, warping and adhesion](https://wiki.bambulab.com/en/knowledge-sharing/printed-model-warping); rule of thumb, to be trained on prints |
| `max_aspect_bedslinger_warn` | 2.5:1, height over the footprint's depth along Y | on an i3 printer (A1, A2L: `printer_structure` = i3) the bed itself moves in Y; every reversal is a shake at the top of the part | machine profile `printer_structure` |
| `max_pillar_ratio` | 10:1, height carried over the width of one of several feet | a part standing on separate small feet peels at one foot and then the others go | derived; the table test case |
| single-bead strip | island narrower than one bead carrying > 4 layers | one line touching the bed lifts from its ends | derived |
| `min_contact_ratio` per body | 0.5 | the plate-level `contact` mixes bodies; this is the same rule per body | as above |
| `warp_length_mm` | PLA 250, PETG 180, ABS/ASA 120, PA/PC 100, TPU 400 | the corners of a long footprint lift as it cools; the more shrinkage the shorter the length that does it | [Bambu wiki, warping](https://wiki.bambulab.com/en/knowledge-sharing/printed-model-warping); conservative defaults |
| `bed_plate` | the chosen plate has a temperature for the filament | Bambu puts 0 for a filament not usable on that plate: PETG on the Cool Plate does not even slice | taller, 2026-10-14 |
| `brim` | FAIL if any risk above and `brim_type=no_brim` | the cheapest fix for every one of them is a brim | Bambu default is `auto_brim` |
| `small_part_speed` (`max_small_part_wall_speed`) | WARN: footprint under `small_footprint_mm` (25) with `outer_wall_speed` over 100 mm/s | every plate of 19 mm discs that came off (PLA or PETG, 2026-10-15) ran the stock outer wall at 200 mm/s / 5000 mm/s2 and detached at layer 3-4, the first full-speed layers; the plate that held ran 40 / 1000 | learnings 2026-10-15, evening |

## Free-standing members (per body, as printed)

`check_members` follows every island of every body up through the print,
one section per `member_step` (1 mm). An island that continues exactly one
island below, and is its only continuation, is the same member and its free
height grows; a split or a merge starts a new one. Each member is a
cantilever from where it left the rest, and its **slenderness** is that free
height over the least radius of gyration of its weakest section
(r = sqrt(I_min / A), holes subtracted, so a closed box of thin walls is
stiff and a strip of the same wall is not).

| rule | default | why | source |
|---|---|---|---|
| `max_slenderness_warn` | 40 | it sways as it grows; the top layers print onto something moving | calibrated 2026-10-15 on the 28 print files of spiches, wincher, kailua and taller: every one that held was under 30; a 120 mm lathe pattern standing alone read 44 |
| `max_slenderness_fail` | 60 | the nozzle's drag and the bed's reversals lever the body off the bed | the cajonera_lijas frame came off at 90 % with 94 |

The footprint rules cannot see this: they judge a body by its outline on the
bed and its total height, and a wide base stuck well under eight thin sticks
passes all of them.

## The layer simulation (`simulate.py`, `sim` stage)

The physics and the formulas are in `SIMULATION.md`; the thresholds:

| rule | default | why | source |
|---|---|---|---|
| `peel_mpa` | PLA 0.1, PETG 0.1, TPU 0.1 MPa, **provisional** | first-layer peel strength on textured PEI, the one constant in the overturning model nobody has measured; strand shear 0.3 MPa at 60 C (PLA on polyimide, Spoerk 2018), PETG tear-off 53 N (Kascak 2024), PrusaSlicer 0.02 MPa with another lever. To be fit on coupon C1 of the research plan | research notes 02-physics |
| `modulus_mpa` | PLA 2750, PETG 2050, TPU 100 | flexural modulus of Bambu PLA Basic and PETG HF | Bambu data sheets |
| `min_overturn_margin_warn` / `_fail` | 3 / 1 | resisting over overturning moment per body (weight x arm + peel x section modulus against bed shake x height + nozzle push x height) | statics; a 6 x 6 x 60 column reads 1.2 |
| `max_tip_sway_warn` / `_fail` | 0.2 / 0.5 mm | a free member's tip displacement when the nozzle arrives: half a bead means the layer lands on something that moves, a bead means the nozzle catches the top. The frame's 2 x 10 x 80 brace reads 0.91; a 10 x 10 x 100 post 0.02 | nozzle push 10 g (PrusaSlicer SupportSpots constant); the frame, 2026-10-15 |
| `max_short_layer_share` | 0.3 | share of layers above the first 3 that the slicer's cooling rule cannot stretch to `slow_down_layer_time` even at `slow_down_min_speed`; the real defect behind a lone small part's soft edges | Bambu `CoolingBuffer`; the 2026-10-11 label |
| `sim_max_layers` / `sim_skip_first_layers` | 250 / 3 | sampling: every layer up to 250, then every k-th; the floor is judged above the slow first layers | runtime; first layers run at `initial_layer_speed` anyway |
| `max_printed_mass_g` | 2000 | the A2L's limit; the slicer sets error bit 11 past it | Bambu Studio `GCode.cpp` |
| heat zone | judged per body, without its brim: FAIL when any body's first layer leaves 60-270 x 49-271; WARN when only a brim does; INFO outside 102-228 x 93-227 | `PartPlate::get_heat_soak_level`: the firmware holds the bed until the zone is stable (HMS 0300-2D00-0003-0001, 5-15 min) and the first layer on the rim is laid cold. The frame placed 22 mm out came off at layer 359 of 449 (T07); two plates whose brims alone spilled 4 mm held (T05, T06) | A2L machine preset `bed_heat_soak_area`; the frame of 2026-10-19 |

## Slicing (project_settings.config, slice_info, G-code)

| rule | default | why | source |
|---|---|---|---|
| `same_material` | true | PLA and PETG are used as a support pair precisely because they do not bond; an inlay of one in the other is held by fit alone | Bambu's PLA/PETG mutual-support guide; learnings 2026-10-11 |
| bed temperature per filament | equal | Bambu sets the bed by the first filament printed; the other filament runs at a bed it was not tuned for | Kailua `kailua-printer` notes, 2026-10-10 |
| `min_layer_time_s` | 8 s | PETG needs the previous layer to set; the slicer's own `slow_down_layer_time` defaults are 6-12 s and when it cannot stretch the layer it crawls the outer wall to `slow_down_min_speed` | Bambu PETG HF preset (`slow_down_layer_time`), learnings 2026-10-11 |
| `min_outer_wall_speed` | 15 mm/s | an outer wall at 10 mm/s is the slicer stretching a too-short layer; hot plastic dragged slowly rounds corners and ovalises circles | learnings 2026-10-11 |
| `max_purge_ratio` | 1.0 | purge above the part's own weight: too many colour changes for too little part; copies per plate pay the purge once per layer | A2L flush matrix 608/128 mm3 per change |
| classic walls + `detect_thin_wall=0` | warn | features under two beads are dropped by classic walls | [Bambu wiki, wall generator](https://wiki.bambulab.com/en/software/bambu-studio/wall-generator) |
| `tower_height` (`min_tower_height_mm`) | FAIL under 1.0 mm | the prime tower is printed only up to the last layer with a colour change and has no minimum height in the slicer; on a job whose second colour ends at layer 2 it is a 0.4 mm film. It came off and wrapped the nozzle twice (2026-10-14, 2026-10-15) | Bambu Studio `Print.cpp`, `ToolOrdering.cpp`; learnings |
| `brim_laid` | WARN when no Brim feature is in the G-code and the first layer's footprint is under `small_footprint_mm`; INFO otherwise | `auto_brim` judged a 19 x 19 x 2.4 mm disc stable and laid nothing while the settings said "brim"; the labels kept coming off | learnings 2026-10-15, night |
| `ironing` | FAIL on any `M1031` | ironing on the A2L under-extrudes and clicks the extruder (Bambu Studio issue 11690, open); one file had 8038 toggles | forum 260651, 258840 |
| shells vs layers | info | a part thinner than bottom+top shells is all shell; sparse infill inside a 6-layer part is a slicer artefact worth knowing about | derived |

## Learnings (trained over time)

Append dated bullets; convert relative dates to absolute. Merge duplicates.

- **2026-10-11, TOMA USB POPA label, Kailua.** 19.3 mm disc, 0.8 mm thick,
  white PLA letters 0.6 mm deep inlaid in black PETG, face down, stock 0.20mm
  Standard A2L profile, six settings changed at once from the R14-R32 test
  that had held. Came off the bed deformed (oval, soft edges) and fell apart in
  the owner's hand. Replayed through pre-flight: NO-GO, four FAILs
  (`mixed_materials`, `inlay_backing` 1 layer, `thin_wall` p05 0.24, `layer_time`
  median 6.5 s) and purge 17x the part. Rules born here: same material for
  every colour; at least two body layers covering the inlay; a thin-wall FAIL
  under two layers; a layer-time floor; and the one-change-per-test discipline.
  The fix in the repo: 1.2 mm disc, 0.4 mm letters, three copies per plate so
  layers last 12 s, fan 60-80 % from layer 2, outer wall 40 mm/s.
- **2026-10-11, inlay backing on a hollow back.** The first version of the
  backing check counted any body layer above the inlay and read 8 on a part
  that had 1: a spoon-shaped back has material at the rim long after the
  centre has run out. The check now counts only layers that cover 95 % of the
  inlay's footprint. Generalises: measure over the feature, not anywhere.
- **2026-10-11, what pre-flight cannot see.** Bonding between materials, bed
  adhesion, residual-stress warping, flow and temperature effects, and what
  the Bambu Studio GUI shows (it rebuilt system presets with the overrides
  dropped until `different_settings_to_system` was filled). The owner's
  question "¿vos estás validando lo que hacés?" is the bar: say what was
  checked and what was not.

- **2026-10-11, definitive label plate (Kailua, 7 labels, PETG + PETG, face
  down): two FAILs measured false from the G-code.**
  - `overhang` 530 mm2 at z 0.5: computed on the body mesh alone, so the
    letter pockets read as empty; the companion (letters) part fills them.
    The sliced G-code has no Bridge / Overhang wall feature at z 0.6. When a
    project has companion parts, judge overhang on the union, not the body.
  - `inlay_backing` 0 layers: wrong. Rasterising the G-code, black (T0)
    covers 99.5-100 % of the letters' footprint on every layer z 0.6-1.4,
    i.e. 5 layers (1.0 mm), as designed. The section() polygons go wrong on
    disc/letter/counter rings (it also reported body/letter plan overlap
    where the manifold boolean gives 0.000 mm3). Until fixed, confirm
    backing from the G-code and say so in the verdict.
  Neither threshold was loosened; the verdict still reads NO-GO and the
  owner is told why each FAIL does not apply.

- **2026-10-15, plate of seven plus coupon (Kailua, stock A2L recipe).**
  Two findings were artefacts of the checks, now fixed in the script:
  - `second_colour` first-layer share read 68 %: the prime tower is printed
    in whichever colour just loaded, and its white layer-2 pass was counted
    as inlay footprint. The tower is now excluded; the real figure is 98 %.
    Same fix in Kailua's `lib/bambu.py`.
  - `overhang` on the body alone (975 mm2 at z 0.5) is the inlay pockets
    read as empty. `check_mesh` now judges the body on the union with its
    companion parts (0 mm2); Kailua's `lib/printing.py` does the same.
  - `inlay_backing` 0 layers and the 2026-10-11 "section rings" puzzle had
    one cause: `section()` used trimesh's `to_2D()`, which picks a planar
    frame per section, so body and inlay polygons (and even consecutive
    layers of one mesh) sat in different frames, 4.8 mm apart on this plate.
    `section()` now keeps the mesh's own x, y (`to_planar(to_2D=T)`), and
    the backing reads 5 layers (1.0 mm), matching `layer_filaments` in
    slice_info. The overhang figures of every earlier run were also taken
    in drifting frames; re-run old files before trusting their numbers.
  Refinement from reading Bambu Studio's source (2026-10-14 research):
  Arachne drops features under `min_feature_size` (25 % of the nozzle,
  0.10 mm) and widens anything between that and `min_bead_width` (85 %,
  0.34 mm) to one 0.34 mm bead; `dropped_features` therefore means "widened
  to 0.34" for 0.10-0.34 mm islands, dropped only under 0.10. A calibration
  coupon carries sub-threshold features on purpose (bar ladder 0.30-1.00,
  inverted text): its FAILs are the measurement, not a defect; say which
  features belong to the coupon and which to the part.

- **2026-10-15, prime tower on a two-layer colour job (Kailua A2L, textured
  PEI, PETG HF).** The plate of seven plus coupon went out with Bambu's
  stock prime tower (on by default): with the second colour only in layers
  1-2 the tower is a 0.4 mm, 20 x 19 mm plate. It came off the bed and
  wrapped the nozzle, the second time in two days (same on 2026-10-14).
  Rule: when the colour changes stop at layer 2 or 3, a prime tower is a
  loose film; turn it off on a printer that purges off the bed (the A2L's
  rear chute) or make it taller than ~1 mm on purpose. The pre-flight should
  warn when the tower's height (last change layer) is under 1 mm.
- **2026-10-15, bed adhesion check added (taller, cajonera_lijas).** The owner
  asked to validate that nothing makes a print come off the bed and to add it
  to the validations. Tested on synthetic cases: a 6 x 6 x 60 column (aspect
  FAIL), a 45-degree leaning beam (centre of mass 20 mm outside: FAIL), a
  plate on four 3 mm legs (each foot 17:1: pillar WARN), a 0.4 mm strip
  (single bead) and a plain box (GO). The first real plate judged by it, the
  drawer and its frame in PLA on a bed slinger, passed: 0.6:1 and 1.6:1 in Y.

- **2026-10-15, adhesion check vs inlaid letters.** The new per-body
  adhesion test (centre of mass against the footprint) judged each of the
  93 glyphs of a face-down two-colour plate as a free-standing body that
  "tips off the bed". A glyph inlaid in the first two layers is ringed by
  its disc at those layers and cannot tip; `check_adhesion` now skips a
  body whose one-bead ring around its footprint is at least 60 % covered
  by other bodies' first layer, and says how many it skipped. The `strip`
  WARN on the body's own tiny first-layer islands (the counters of A, O, B,
  the black between the coupon's bars) still fires: those islands merge
  into the body from layer 3, so "carries 1.3 mm" overstates them.
- **2026-10-15, cajonera_lijas frame, taller.** Printed on its back bars,
  black PLA, Textured PEI, auto brim: 90 tall on a 4100 mm2 base 56 wide,
  centre of mass 28 inside, every adhesion rule green. Came off the bed at
  90 % and flew. Sliced by height, above the 5 mm base it was eight separate
  sticks (rail, post, diagonal, channel per side), 21-37 mm2 each, free for
  80 mm until the caps joined them. Rule born here: `check_members`
  (slenderness of free-standing members). The fix in the part: a 1.2 mm
  lattice panel per side tying rail and channel, and a fixed 5 mm brim. The
  engine moved to `projects-3D/printcheck/` the same day so every project's
  suite runs it, not only this skill.

- **2026-10-15, PLA plate of seven plus coupon, Kailua: discs off the bed
  twice at the same moment.** The recipe carried a 100 % zig-zag "sparse"
  infill kept from an older set as a harmless exception. In the G-code the
  detachments coincide with z 0.8 (half the print time), the first layer
  with that infill: 131 mm3 of dense zig-zag in the rim rings at infill
  speed, over-stuffed, and the nozzle snagged the discs. Pre-flight had no
  rule and printed "infill 100%" as information, twice. Rule born here:
  `dense_infill` FAILs any sparse infill density at or above 50 %
  (`max_sparse_infill_pct`); a solid part gets its solidity from shells.
  The process lesson: "stock plus one named exception" means one; an
  exception kept because it "does not touch the face" still touches the
  print.

- **2026-10-15, evening, correction.** The 100 % infill was not what pulled
  the discs off: the plate of six of 2026-10-14 held with the same 100 %
  zig-zag. `dense_infill` is downgraded to a WARN (still a real
  over-stuffing risk, not proven here). What all the plates that came off
  shared, PLA or PETG, tower or not, 15 % or 100 %: Bambu's stock outer
  wall at 200 mm/s / 5000 mm/s2 on 19 mm discs, and the white letters laid
  first on layer 1 as lone islands (video: the black's nozzle knocked them
  off). The plate that held ran the outer wall at 40 / 1000 with black
  first. Detachment at layer 3-4, the first full-speed layer, is the
  signature: a small part that survives the slow first layers and comes off
  when the speed comes in wants slower walls, not a cleaner plate. Rule to
  add: on footprints under ~25 mm, warn when outer wall speed > 100 mm/s.

- **2026-10-15, night, Kailua labels in PETG: auto_brim lays nothing on a
  19 mm disc.** The owner's labels kept coming off and the plate was ruled
  out (wiped with alcohol every time). The sliced file carried
  `brim_type = auto_brim`, yet the G-code had zero `FEATURE: Brim` moves:
  Bambu's auto brim judges a 19 x 19 x 2.4 mm disc stable and skips it, so
  every label plate went out with nothing holding the rim against the
  nozzle's drag. The `adhesion` check reported "brim auto_brim" as if a
  brim existed. Fix in the recipe: `brim_type = outer_only`, `brim_width`
  5 (the one change from the held recipe). Rule to add: when `brim_type`
  is `auto_brim`, count the Brim feature in the G-code and report the real
  brim (none / width); on a footprint under 25 mm with no brim laid, WARN.

- **2026-10-15, evening: the layer simulation, the corpus and the docs
  (session 2 of the research plan).** The owner asked for the validator to
  simulate the print layer by layer, to keep the history of every failure
  with its cause and file, and to have .md docs every project can follow.
  Added `simulate.py` (`sim` stage, `--layers`, `--at Z`; physics in
  `SIMULATION.md`), `presets.py` (a part's `spec.BAMBU` flattened into the
  settings the engine reads, so the layout path judges with the real
  speeds, cooling and slow-down ramp), `corpus/outcomes.tsv` (12 prints),
  `calibrate.py`, `tests/test_shapes.py`, and the four docs. Rules born
  from the night's prints: `tower_height`, `brim_laid`, `small_part_speed`,
  `ironing`. Two bugs found on the way: filament density is g/cm3 and the
  first member model used it as g/mm3 (a 10 x 10 x 100 post read 68 mm of
  sway; 0.02 mm after the fix); `load_project` ignored the 3MF build items,
  so meshes sat centred at the origin instead of where the plate prints
  them (the heat-zone rule needs plate coordinates; the G-code did match
  the mesh only by luck of symmetry).
- **2026-10-15, evening: calibration of the simulation.** Corpus replay:
  the rebuilt 2026-10-11 label is NO-GO (`inlay_backing`, `thin_wall`), the
  rebuilt frame is NO-GO (`slender` 94, `sway` 0.9 mm per brace), the
  printed panel (P01) is GO WITH CAVEATS, and K09 (the single NAV label
  that came apart at 55 %) is the admitted miss: GO WITH CAVEATS with every
  rule green, cause open. Project suites with the new stage: taller
  cajonera_lijas 224 passed / 0 failed, kailua etiquetas 222 / 0; no `sim`
  WARN or FAIL on any current print file, so the provisional constants
  (peel 0.1 MPa, Q 1-15, nozzle 10 g, sway 0.2 / 0.5 mm, margin 3 / 1) do
  not over-warn on the parts that printed or are about to. Smallest
  overturning margins on current parts: the spiches v4 bushing and finger
  (`dedo_completo`), 3.0 and 3.1 at z 10, tall on small round footprints (their
  `aspect` WARN already says so); the carrier and knob 39-41; everything
  else over 70. The only files past a `sim` threshold are the superseded
  `old/` rail segments (aspect, slender and sway 0.98 mm together) and the
  synthetic shapes. The constants stay provisional until the coupon
  afternoon; the bushing and the finger are the first real parts to watch
  against the margin threshold when they print.
- **2026-10-17, the lathe copier and its mount (taller): three engine fixes,
  no threshold moved.** (1) `section()` on a plane through a vertex lost
  711 mm2 of the mount's foot and FAILed it for a 685 mm2 overhang that was
  not there; the plane now moves 0.001 mm off any vertex. (2) A plate of
  separate objects (the mount kit: 15 pieces) was judged as one body with
  14 inlays: two cabezals' 169 mm2 roofs at the same height summed to a
  338 mm2 FAIL. `groups()` now judges parts that share floor space together
  (a body and its inlays) and every other object apart. (3) An object's own
  settings in model_settings.config (a brim on the knobs, an outer wall at
  40 mm/s) were ignored and the knobs read `small_part_speed` at the plate's
  200; `check_adhesion` now reads each body with its object's settings, and
  `brim` FAILs only the bodies whose own brim is off. Corpus replay green
  after each; `test_separate_objects_judged_apart` covers (2).
- **2026-10-19, the frame on the cold rim (T07).** A plate of two drawers and
  one frame, in a row along Y with the frame last, at y=265: its 57 mm
  footprint reached y=293, 22 mm past the outer preconditioning zone. The
  drawers (7700 mm2 base, 50 tall) finished; the frame (4100 mm2, 90 tall,
  the body the whole `slender` rule was written for) came off at layer 359.
  `heat_zone` had fired as a WARN and was waved through as "the same warning
  as the two plates that held", where only brims had spilled 4 mm. Rule
  change: the zone is judged per body and without the brim, FAIL when a body
  leaves it. Process change: a WARN is cleared only with numbers from the
  file at hand, and the plate layout is the toolchain's job (delicate body
  at the centre), not a hand-placed script.
- **2026-10-19, layer time with acceleration.** The G-code timer summed
  distance over feed rate and read the frame's lattice at 5.4 s a layer
  (`layer_time` FAIL on `v2_bastidor.3mf`, a print that held) where the
  slicer's own M73 markers said 12-18 s. Every segment is now a trapezoid
  (or a triangle when it cannot reach its feed) between junctions, with the
  acceleration from `M204 S` and the profile's `default_acceleration`; a
  junction turning more than 30 degrees is a stop, a gentler one carries the
  speed. On the three-piece plates the engine reads 39 s a layer against
  the slicer's 49-54: still under, never over, so the `min_layer_time_s`
  floor keeps its margin. The one FAIL on a held print is gone.
- **2026-10-20, an open side wall crashed the mesh stage.** A mesh with a
  side face missing gives no closed outline at any layer; `check_mesh`
  called `min()` on no rows and the exception took the whole verdict with
  it (found by the coverage suite, `tests/test_geometry.py`). New code
  `mesh/no_section` FAIL: "no layer has a closed outline", and the stage
  stops there, next to the `watertight` FAIL that names the cause.
