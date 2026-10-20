# Changelog

## 0.1.0 (2026-10-20)

First release as a repository of its own (it lived inside a folder of 3D
projects until now).

- The engine: `settings`, `mesh`, `inlay`, `adhesion`, `members`, `sim`
  (layer-by-layer simulation), `slice_info` and `gcode` stages, the
  `views` figure, GO / GO WITH CAVEATS / NO-GO with exit code 1 on NO-GO.
- The corpus: 19 prints on a Bambu Lab A2L with their outcomes, every
  surviving file inside the repo (`corpus/files/`), replayed by
  `calibrate.py` with no contradictions.
- Installable: `pip install -e .` gives `printcheck` and
  `printcheck-calibrate`; the folder can have any name.
- `examples/`: generated GO, GO WITH CAVEATS and NO-GO files as STL,
  Bambu project and sliced project, a demo project in the kailua layout,
  and a labelled example corpus.
- Tests at 100 % line and branch coverage, enforced in CI.
- Documentation for people and for AI agents: `README.md`, `AGENTS.md`,
  `docs/`.
- `calibrate.py`: `--corpus FILE`, and `--sweep DIR` takes the directory
  to scan instead of assuming the parent folder.
