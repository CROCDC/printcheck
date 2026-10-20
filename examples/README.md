# examples: one file per verdict

Every file here is written by [`build.py`](build.py) from a few lines of
code, so it can be rebuilt and checked (`tests/test_examples.py` fails when
a committed file differs from what `build.py` writes). They are chosen for
the verdict they give, not for being useful parts.

| file | verdict | why |
|---|---|---|
| `stl/go_block.stl` | GO | a block 40 x 30 x 12: nothing to fail |
| `stl/caveats_tower.stl` | GO WITH CAVEATS | 44 tall on a 14 mm base: `adhesion/aspect` WARN (3.1:1) |
| `stl/nogo_post.stl` | NO-GO | 100 tall on 3 x 3: tips (`com`), too tall (`aspect`), sways (`members/slender`, `sim/sway`), peels (`sim/overturn`) |
| `stl/label_body.stl` + `stl/label_bar.stl` | GO | a plate with a second colour inlaid flush with its bed face (pass both, body first) |
| `bambu/go_block.3mf` | GO | the block as a Bambu Studio project for the A2L |
| `bambu/caveats_tower.3mf` | GO WITH CAVEATS | adds `aspect_y` (the bed moves in Y) and `small_part_speed` (200 mm/s outer wall on a 14 mm footprint) |
| `bambu/nogo_post.3mf` | NO-GO | the post, with `brim_type = no_brim` on top: `adhesion/brim` FAIL |
| `bambu/nogo_label_tower_film.gcode.3mf` | NO-GO | sliced, synthetic G-code: the second colour ends at layer 2 and the prime tower with it, a 0.4 mm film (`gcode/tower_height`; the K05 failure of 2026-10-15) |
| `workspace/demo/` | one part per verdict | a project in the kailua layout: `python examples/workspace/demo/lib/run.py` |
| `corpus.tsv` | 0 contradictions | labelled rows over these files, one per kind of row `calibrate.py` knows |

```bash
printcheck examples/stl/nogo_post.stl --layers 10
printcheck examples/bambu/nogo_label_tower_film.gcode.3mf
python examples/workspace/demo/lib/run.py                 # exit 1: nogo_post FAILs
printcheck-calibrate --corpus examples/corpus.tsv --sweep examples/workspace
python examples/build.py                                  # rewrite every file here
```

## The demo project

`workspace/demo` is the smallest project in the kailua layout that
`printcheck.layout` can drive: `lib/printing.py` gives the two functions
the adapter imports (`orientation`, `pieces`), `lib/run.py` loads each
`parts/<part>/spec.py` and its meshes and runs `layout.printability` on
every print file. The three parts show the three hooks a real part uses:

| part | hook | verdict |
|---|---|---|
| `go_block` | `also`: a companion colour printed with the body | GO |
| `caveats_tower` | `ROTATION`: authored lying down, printed standing | GO WITH CAVEATS |
| `nogo_post` | `cut`: printed as two pieces, each judged on its own; `PRINTCHECK` override | NO-GO |

Each run writes `parts/<part>/previews/<file>_views.png` (ignored by git).

## The example corpus

`corpus.tsv` has the columns of `corpus/outcomes.tsv` and one row for each
case `calibrate.py` handles: a held print (GO), a held print with WARNs, a
caught failure, an admitted miss (`rule_now` starting with `missed`), an
unlabelled print and a row with no file. Paths are relative to the
repository root.
