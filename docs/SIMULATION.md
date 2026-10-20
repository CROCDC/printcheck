# The layer simulation: a digital reprint

`simulate.py` walks the plate the way the A2L will, one layer at a time,
and writes a ledger: what the nozzle lays on that layer, what has been
printed under it, how hard the moving bed will shake it, which members
stand free and how far the nozzle's drag pushes their tips, how long the
layer takes and whether the slicer's cooling rule can stretch it. The
rules in `check_simulation` read the ledger and the `sim` stage of the
report is their verdict; `--layers` prints the ledger and `--at Z` prints
everything the simulation knows about one height, so a bad layer can be
looked at before the printer finds it.

It runs on every path: a part's suite (`lib/run.py`, through
`printcheck.layout`), the CLI on a `.3mf` or loose STLs, and the
`print-preflight` skill. On a sliced file the `gcode` stage adds what the
slicer actually did (layer times from feed rates, the Y acceleration it
told the firmware per layer, the brim it laid, the tower's height).

## What it computes, per layer

The plate is sectioned at the middle of every layer (every `k`-th layer
when the part has more than `sim_max_layers`, 250 by default; the ledger
says so). Every body on the plate is sectioned on its own as well.

| column | how | why it matters |
|---|---|---|
| `mm2`, `isl` | area and islands of the plate's section | what the layer is |
| `path_mm` | wall loops x ring length, plus solid and sparse infill as area / line width. Solid where the part ends within `top_shell_layers` above or `bottom_shell_layers` below the layer; sparse elsewhere, at `sparse_infill_density` | the time and the mass come from it |
| `s` | path over the process speeds (outer / inner wall, solid, sparse, first layer), each capped by the filament's `filament_max_volumetric_speed` / (line x layer) | the raw layer time, no acceleration, so a little short |
| `s*` | the slicer's cooling rule exactly: `max(t, min(slow_down_layer_time, t_at_slow_down_min_speed))` (Bambu Studio `CoolingBuffer`) | what the layer will really last. A `!` marks a layer the rule cannot stretch to its floor even at the minimum speed: the real defect behind soft, dragged small parts (2026-10-11 label) |
| `fan` | fan_max under `slow_down_layer_time`, linear to fan_min at `fan_cooling_layer_time`, off for `close_fan_the_first_x_layers` | the cooling the layer gets |
| `g` | mass laid so far: walls and shells solid, sparse at its density, filament density from the preset | drives the acceleration limit and the overturning |
| `acc_y` | `min(machine_max_acceleration_y, 29e6 / (2700 + grams), slow-down-by-height ramp)`. The A2L emits this per layer as `M201 N1 Y`; the ramp is on by default on the A2L (8000 to 1000 mm/s2 from z 0 to 225) | the bed is what moves on this printer; this is how hard it shakes the part |
| `grow` | area of the layer not over the layer below, buffered by one layer | the overhang the `mesh` stage limits (300 mm2); `growth_w` is the widest unsupported step |
| `margin` | per body: resisting over overturning moment, see below | whether the body peels off its footprint |
| `free`, `sway`, `lam` | the worst free-standing member on the layer: how tall it is, how far its tip moves, its slenderness | whether the nozzle finds the top where it left it |

### Overturning, per body

The bed accelerates along Y; the printed mass above the bed wants to stay
where it is. Per body, with the mass `m` printed so far and its centre at
height `h`:

    M_ov  = m a_y h + F_nozzle z          (what tips it)
    M_res = m g d_y + sigma_peel W_x      (what holds it)

`F_nozzle` is 0.098 N (10 g, the constant PrusaSlicer's stability search
uses for a nozzle dragging a wall), applied at the current height. `d_y` is
the shorter distance from the body's centre to its footprint's edge along
Y (the bed reverses, so the short side counts). `W_x` is the section
modulus of the first-layer footprint about the X axis through that edge,
holes subtracted; a brim is added to the footprint when `brim_type` is
`outer_only` or `outer_and_inner` (`auto_brim` is not counted: it laid
nothing on a 19 mm disc, 2026-10-15). `sigma_peel` is the first-layer
peel strength on textured PEI: **provisional 0.1 MPa** for PLA and PETG
(literature has strand shear 0.3 MPa at 60 C for PLA on polyimide and a
53 N tear-off for PETG on PEI, no peel number; PrusaSlicer uses 0.02 MPa
as "bed adhesion yield" with a different lever). It is the constant the
coupon prints of the research plan (C1) are meant to pin down.

`margin = M_res / M_ov`; WARN under 3, FAIL under 1.

### Members: sway and stress

The member-following algorithm is the one `check_members` uses (one
island that continues exactly one island below is the same member; a
split or a merge starts a new one), run on the simulation's sections. For
each live member taller than two steps, with its weakest section (area
`A`, least radius of gyration `r`, radius along Y `r_y`), height `L`,
material modulus `E` and density `rho`:

    f1    = 0.56 sqrt(E I_y / (rho A L^4))        first natural frequency, bending along Y
    Q     = 1 above 80 Hz, 15 below 40 Hz, linear between   (the band the input shaper leaves)
    w     = rho A a_y                               inertial load per mm of height
    sway  = w L^4 / (8 E I_y) Q  +  F_nozzle L^3 / (3 E I_min)
    stress = (w L^2 / 2 Q + F_nozzle L) c / I_min,  c = r sqrt(3)

`sway` is how far the tip has moved when the nozzle arrives: over half a
bead (0.2 mm) the top layers go down on something that moves (WARN); over
a bead (0.5 mm) the nozzle catches the top and levers the member (FAIL).
Worked numbers with the 2026-10-15 constants, PLA, a_y 6000:

| member | f1 | Q | sway | verdict | real print |
|---|---|---|---|---|---|
| 10 x 10 x 100 post | 239 Hz | 1 | 0.02 mm | holds | holds (with a brim; the `aspect` rule still warns) |
| 2 x 10 x 80 stick (the frame's brace) | 376 Hz | 1 | 0.91 mm | FAIL | the frame came off at 90 % |
| 3 x 3 x 150 post | 32 Hz | 15 | 9.4 mm | FAIL | forum: knocked off |
| 6 x 6 x 60 column | 55 Hz | 10 | 0.03 mm (margin 1.2) | overturn WARN | not printed |

The nozzle term dominates on stocky members; the bed term and `Q` take
over on long thin ones. `check_members` (slenderness 40 / 60) stays
beside this: it is calibrated on 29 real prints, the simulation on
physics with one provisional constant.

### The rest

`layer_floor`: above the first `sim_skip_first_layers` (3), the share of
layers the cooling rule cannot stretch to `slow_down_layer_time` even at
`slow_down_min_speed`; WARN over 30 % (print two copies, or a sacrificial
post). `mass`: FAIL over 2000 g (the A2L's limit, slicer error bit 11).
`heat_zone`: judged per body and without the brim. FAIL when any body's
first layer leaves the outer preconditioning rectangle (60-270 x 49-271):
that body is laid on the cold rim of the bed, and the frame placed 22 mm
out on 2026-10-19 came off at layer 359 of 449. WARN when only a brim
spills past it (two such plates held), INFO when the footprint leaves the
inner rectangle (102-228 x 93-227): the A2L waits longer.
Plate coordinates are known only on the 3MF path; the layout path centres
the part, so the zone is not judged there. `watch`: the layer with the
worst score across margin, sway and floor, so the owner knows where to
look at the printer.

## Reading the ledger

```
python printcheck/preflight.py part.3mf --layers 30     # 30 rows, evenly thinned
python printcheck/preflight.py part.3mf --layers all
python printcheck/preflight.py part.3mf --at 45         # everything at z = 45 mm
python printcheck/preflight.py part.3mf --json out.json # "simulation": the whole ledger
```

A healthy part reads: `s*` at or above the floor with no `!` past the
first layers, `margin` in the thousands and falling slowly with height,
`sway` under 0.1 and `lam` under 30 everywhere. Trouble reads as: `!` on
most rows (a lone small part), a `margin` sinking under 10 (a tall narrow
body), `free` growing layer after layer while `sway` climbs past 0.2 (a
stick nothing ties), `grow` jumping (a roof).

## What it does not see

The same list as the rest of the engine: plate cleanliness, filament
moisture, drafts, hardware state, bonding between two materials, residual
stress warping. The simulation is geometry and the slicer's arithmetic;
`sigma_peel`, `Q` and the nozzle force are published constants waiting for
our own coupons. The G-code stage is the check on its time estimate: when
the two disagree by more than 20 % on short layers, the trapezoid planner
(`M204` stream) is the next step (research plan, B7).

## Sources

Bambu Studio 02.08 source: `GCode.cpp` (`mass_load_limited_machine_acceleration`,
slow-down-by-height), `CoolingBuffer.cpp`, `PartPlate.cpp`
(`get_heat_soak_level`); the A2L machine and `0.20mm Standard @BBL A2L`
presets; PrusaSlicer `SupportSpotsGenerator` constants (nozzle push 10 g,
reimplemented, not ported); Spoerk 2018 and Kascak 2024 for the adhesion
numbers; the owner's prints in `corpus/outcomes.tsv`. The research notes
with every link: `projects-3D/plans/printcheck-a2l-research/`.
