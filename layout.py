"""printcheck for projects in the kailua layout (lib/ + parts/<part>/).

Every project's lib/run.py calls `printability` after the part's own checks,
whatever its manifest lists, so a part cannot leave the shared rules out.
It judges each print file the way the export writes it: the variant turned
into print orientation, its companions in the same frame, a cut piece as the
bodies it splits into.
"""

import os

from . import check_print, material_of, presets, views


def _settings(spec):
    """The slicer settings the part will print with: the flattened Bambu
    presets of `spec.BAMBU` when Bambu Studio is installed (speeds, cooling,
    the slow-down ramp reach the simulation), the material and brim alone
    when it is not."""
    bambu = getattr(spec, "BAMBU", {}) or {}
    fil = (bambu.get("filaments") or [("",)])[0][0]
    material = material_of(fil, getattr(spec, "MATERIAL", "PETG"))
    if presets.available() and bambu.get("process"):
        return presets.resolve(bambu, material)
    st = {"filament_type": [material], "printer_structure": "i3"}
    over = bambu.get("process_overrides", {})
    for k in ("brim_type", "brim_width", "curr_bed_type"):
        if k in over:
            st[k] = over[k]
    return st


def _on_bed(meshes):
    z0 = min(float(m.bounds[0][2]) for m in meshes)
    for m in meshes:
        m.apply_translation([0, 0, -z0])
    return meshes


def printability(ctx, s):
    """Each print file against projects-3D/printcheck; FAIL there fails here."""
    from lib.printing import orientation, pieces

    part = ctx.part
    rules = dict(getattr(ctx.spec, "PRINTCHECK", {}))
    if hasattr(ctx.spec, "LAYER"):
        rules.setdefault("layer", ctx.spec.LAYER)
    settings = _settings(ctx.spec)
    if not part.print_files:
        s.info("no print files declared: nothing to judge")
        return
    for pf in part.print_files:
        mesh = ctx.meshes.get(pf.variant)
        if mesh is None:
            s.fail(f"{pf.name}: variant {pf.variant!r} has no mesh")
            continue
        R = orientation(pf.rotation or part.rotation_of(part.variant(pf.variant)))
        body = mesh.copy()
        body.apply_transform(R)
        runs = [[b] for b in pieces(body, pf.cut)] if pf.cut else [[body]]
        if not pf.cut:
            for name in pf.also:
                c = ctx.meshes[name].copy()
                c.apply_transform(R)
                runs[0].append(c)
        for k, meshes in enumerate(runs):
            label = pf.name + (f" piece {k + 1}" if len(runs) > 1 else "")
            meshes = _on_bed([m.copy() for m in meshes])
            parts = [{"name": label if i == 0 else f"{label}+{pf.also[i - 1]}",
                      "mesh": m, "extruder": i + 1} for i, m in enumerate(meshes)]
            rep = check_print(parts, settings, rules)
            for it in rep.items:
                msg = f"{label}: [{it['stage']}/{it['code']}] {it['msg']}"
                {"FAIL": s.fail, "WARN": s.warn, "OK": s.ok}.get(it["level"], s.info)(msg)
            # The figure the owner looks at before anything is sent: the
            # print as it stands on the bed and two cuts through it.
            fig = views.render(parts, os.path.join(part.previews_dir, f"{label.replace(' ', '_')}_views.png"))
            if fig:
                s.info(f"{label}: [views] {os.path.relpath(fig, os.path.dirname(part.dir))}: "
                       f"from an angle, from above, cut across y and across x. Look at it.")
            else:
                s.warn(f"{label}: [views] matplotlib missing in this venv, no figure written")
