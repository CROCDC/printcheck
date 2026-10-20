"""Bambu Studio presets, flattened, so the engine judges a part with the
numbers the slicer will actually use.

A preset is a chain of JSON files (`inherits`) under Bambu Studio's
resources; the A2L's own G-code lives in sibling "template" files. The
project suites used to pass the engine three keys (filament type, brim,
printer structure); everything else (speeds, cooling, the slow-down ramp,
accelerations) only reached it from a sliced 3MF. This module gives both
paths the same dictionary (research plan 2026-10-15, gap G2).
"""

import json
import os

BAMBU_APP = os.environ.get("BAMBU_STUDIO",
                           "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio")
PROFILES = os.path.join(os.path.dirname(os.path.dirname(BAMBU_APP)), "Resources", "profiles", "BBL")


def available():
    return os.path.isdir(PROFILES)


def flat(category, name):
    """One preset with its whole inherits chain merged; {} when not installed."""
    path = os.path.join(PROFILES, category, name + ".json")
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        preset = json.load(fh)
    parent = preset.get("inherits")
    merged = flat(category, parent) if parent else {}
    merged.update(preset)
    return merged


def resolve(bambu, default_material="PETG"):
    """The settings dictionary the engine reads, from a part's `spec.BAMBU`:
    {"machine", "process", "process_overrides", "filaments": [(preset,
    colour[, overrides]), ...]}. Per-filament keys come out as lists, one
    entry per filament, the way a project_settings.config has them.
    """
    bambu = bambu or {}
    st = {}
    machine = flat("machine", bambu.get("machine", "")) if bambu.get("machine") else {}
    process = flat("process", bambu.get("process", "")) if bambu.get("process") else {}
    for src in (machine, process):
        for k, v in src.items():
            if k in ("name", "inherits", "from", "instantiation", "type", "setting_id"):
                continue
            st[k] = v
    fils = []
    for f in bambu.get("filaments") or []:
        name = f[0] if isinstance(f, (list, tuple)) else f
        over = f[2] if isinstance(f, (list, tuple)) and len(f) > 2 else {}
        d = flat("filament", name)
        d.update({k: v for k, v in (over or {}).items()})
        d.setdefault("filament_settings_id", [name])
        fils.append(d)
    if not fils:
        fils = [{"filament_type": [default_material]}]
    per_fil = set().union(*(d.keys() for d in fils))
    for k in per_fil:
        if k in ("name", "inherits", "from", "instantiation", "type", "setting_id"):
            continue
        vals = []
        for d in fils:
            v = d.get(k, fils[0].get(k))
            vals.append(v[0] if isinstance(v, list) and len(v) == 1 else v)
        st[k] = vals
    st.update(bambu.get("process_overrides") or {})
    st.setdefault("printer_structure", "i3")
    st.setdefault("filament_type", [default_material])
    return st
