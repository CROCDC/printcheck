"""Printability rules shared by every 3D-print project under projects-3D.

One copy of what makes a print fail on the owner's printer (Bambu Lab A2L, a
bed slinger, 0.4 nozzle): unsupported growth, thin walls, what pulls a body
off the bed, members that sway free while they print, and a layer-by-layer
simulation of the print (simulate.py). Every project runs it on every print
file, and the print-preflight skill runs the same code on any STL or 3MF. A
lesson from a failed print is written here once (corpus/, docs/FAILURES.md)
and reaches all of them.

    python printcheck/preflight.py file.stl|project.3mf [...]   # any file
    from printcheck import check_print                          # from a project
"""

# The repository root is this package, so pytest's collection also loads
# this file on its own, as a bare module with no package (to look for a
# setup_module); there is nothing to import then.
if __package__:
    from .preflight import (RULES, Report, check_adhesion, check_members,  # noqa: F401
                            check_mesh, check_simulation, run_parts)

MATERIALS = ("PETG", "PLA", "ABS", "ASA", "PA", "PC", "TPU")


def material_of(filament, default="PETG"):
    """The filament_type a Bambu filament profile name stands for."""
    up = (filament or "").upper()
    return next((m for m in MATERIALS if m in up), default)


def check_print(parts, settings=None, rules=None):
    """Every rule, on what one print file puts on the bed.

    `parts`: [{"name", "mesh", "extruder"}], already in print orientation
    with the bed at z=0; the largest is the body, the rest ride inside it
    (a second colour). `settings`: the slicer keys the rules read
    (printer_structure, filament_type, brim_type, curr_bed_type).
    """
    rules = dict(RULES, **(rules or {}))
    settings = dict({"printer_structure": "i3"}, **(settings or {}))
    rep = Report()
    run_parts(parts, rules, rep)
    check_adhesion(parts, settings, rules, rep)
    check_members(parts, settings, rules, rep)
    check_simulation(parts, settings, rules, rep)
    return rep
