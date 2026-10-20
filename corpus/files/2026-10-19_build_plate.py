"""Build one Bambu project holding several separate objects on one plate.

The repo's lib.bambu.project() fuses everything into a single object (its
companions share a frame); here each piece is its own object at its own
plate position, with the part's own presets and overrides.
"""
import json, os, subprocess, sys, tempfile
sys.path.insert(0, "/Users/cromero/projects/projects-3D/taller")
from lib import bambu
from parts.cajonera_lijas import spec

cfg = spec.BAMBU
STL = "/Users/cromero/projects/projects-3D/taller/parts/cajonera_lijas/print/stl/to_print"
# (stl, x, y): plate centre is 165,160; rows along Y with 10 mm between brims
CX = 165.0
# two drawers (84.8 deep) and one frame (57.2) in a row along Y, brims
# touching: 227 mm of parts, centred on the plate; wider than the heated
# zone, so the A2L heats the whole bed
Y0 = 160 - 226.8 / 2 + 42.4
pieces = [(f"{STL}/v2_cajon_180.stl", CX, Y0),
          (f"{STL}/v2_cajon_320.stl", CX, Y0 + 94.8),
          (f"{STL}/v2_bastidor.stl", CX, Y0 + 94.8 + 42.4 + 10 + 28.6)]
out = sys.argv[1]
with tempfile.TemporaryDirectory() as tmp:
    machine, _ = bambu._write_preset(tmp, "machine", cfg["machine"])
    process, _ = bambu._write_preset(tmp, "process", cfg["process"], cfg.get("process_overrides", {}))
    f = cfg["filaments"][0]
    filament, _ = bambu._write_preset(tmp, "filament", f[0], {"filament_colour": [f[1]]}, tag="0")
    listing = {"plates": [{"plate_name": "", "need_arrange": False,
        "objects": [{"path": stl, "count": 1, "filaments": [1],
                     "pos_x": [x], "pos_y": [y], "pos_z": [0]} for stl, x, y in pieces]}]}
    lst = os.path.join(tmp, "assemble.json")
    json.dump(listing, open(lst, "w"))
    if os.path.exists(out): os.remove(out)
    r = subprocess.run([bambu.APP, "--load-settings", f"{machine};{process}",
                        "--load-filaments", filament, "--load-assemble-list", lst,
                        "--export-3mf", out], cwd=tmp, capture_output=True, text=True)
    print("rc", r.returncode, (r.stdout + r.stderr)[-400:])
print(out, os.path.exists(out))
