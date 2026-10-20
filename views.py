"""Renders of what the engine judged: the part as it stands on the bed, and
cuts through it.

A green suite says the numbers are right; it does not say the shape is. The
oval lid's groove was modelled solid, every check passed, and the print
went out before the owner saw the slicer's view (2026-10-20). So every
verification now writes one figure per print file: the body from an angle
and from above, and two cuts through its centre with a millimetre grid,
so a missing channel, a filled pocket or a wrong floor height is seen,
not inferred. The figure is evidence, not a rule: nothing here fails a
print.
"""

import os

import numpy as np


# Rotations that bring the cut axis onto z, so the engine's own `section`
# (vertex-on-plane guard, the mesh's own frame) does the cutting. Both are
# proper rotations; the 2D frame they leave is noted so the plot reads in
# the part's own coordinates.
_CUTS = {
    # cut across y: 2D (x, z); the rotation gives (-x, z, y), so x is undone in the plot
    "y": (np.array([[-1, 0, 0], [0, 0, 1], [0, 1, 0]], float), "x", -1, "z"),
    # cut across x: 2D (y, z); the rotation gives (y, z, x)
    "x": (np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], float), "y", 1, "z"),
}


def cut(mesh, axis, value):
    """The section of `mesh` by the plane axis = value, as a shapely polygon
    in the part's own remaining coordinates, or None."""
    from .preflight import section
    R, _, sign, _ = _CUTS[axis]
    m = mesh.copy()
    T = np.eye(4)
    T[:3, :3] = R
    m.apply_transform(T)
    z = float((R @ np.array([value if axis == "x" else 0,
                             value if axis == "y" else 0, 0]))[2])
    p = section(m, z)
    if p is None or sign == 1:
        return p
    from shapely import affinity
    return affinity.scale(p, xfact=-1, yfact=1, origin=(0, 0))


def _shade(ax, meshes, elev, azim, title):
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    light = np.array([0.3, -0.5, 0.8])
    light /= np.linalg.norm(light)
    lo = np.min([m.bounds[0] for m in meshes], axis=0)
    hi = np.max([m.bounds[1] for m in meshes], axis=0)
    cmaps = [plt.cm.Blues, plt.cm.Oranges, plt.cm.Greens]
    for i, m in enumerate(meshes):
        tri, n = m.triangles, m.face_normals
        sh = np.clip(np.abs(n @ light), 0.25, 1.0)
        order = np.argsort(tri[:, :, 2].mean(1))
        ax.add_collection3d(Poly3DCollection(
            tri[order], facecolors=cmaps[i % 3](0.35 + 0.55 * sh[order]), linewidths=0))
    ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_zlim(lo[2], hi[2])
    ax.set_box_aspect(tuple(hi - lo))
    ax.view_init(elev=elev, azim=azim)
    ax.set_axis_off()
    ax.set_title(title, fontsize=10)


def _plot_cut(ax, polys, labels, title):
    """Filled sections (body first, companions in other colours) on a 10 mm grid."""
    from shapely.plotting import plot_polygon
    colours = ["#3b6fb6", "#e07b39", "#4a9c5d"]
    lo = np.array([np.inf, np.inf]); hi = -lo
    for i, p in enumerate(polys):
        if p is None or p.is_empty:
            continue
        plot_polygon(p, ax=ax, add_points=False, facecolor=colours[i % 3],
                     edgecolor="black", linewidth=0.6, alpha=0.9)
        b = np.array(p.bounds)
        lo = np.minimum(lo, b[:2]); hi = np.maximum(hi, b[2:])
    if not np.isfinite(lo).all():
        ax.set_title(title + ": nothing on this plane", fontsize=10)
        ax.set_axis_off()
        return
    pad = 5
    ax.set_xlim(lo[0] - pad, hi[0] + pad); ax.set_ylim(lo[1] - pad, hi[1] + pad)
    ax.set_aspect("equal")
    ax.set_xticks(np.arange(np.floor((lo[0] - pad) / 10) * 10, hi[0] + pad + 1, 10))
    ax.set_yticks(np.arange(np.floor((lo[1] - pad) / 10) * 10, hi[1] + pad + 1, 10))
    ax.grid(True, linewidth=0.4, alpha=0.5)
    ax.tick_params(labelsize=7)
    ax.set_xlabel(labels[0] + " (mm)", fontsize=8); ax.set_ylabel(labels[1] + " (mm)", fontsize=8)
    ax.set_title(f"{title}: {hi[0] - lo[0]:.1f} x {hi[1] - lo[1]:.1f} mm", fontsize=10)


def render(parts, path, cuts=None):
    """One figure for one print file: the bodies from an angle and from
    above, and the cuts through the centre of the main body across y and
    across x (or the `cuts` given as [("x"|"y", value), ...]). Returns the
    path, or None when matplotlib is not available."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    meshes = [p["mesh"] for p in parts]
    main = max(meshes, key=lambda m: m.volume)
    c = main.bounding_box.centroid
    cuts = cuts or [("y", float(c[1])), ("x", float(c[0]))]
    names = ", ".join(p["name"] for p in parts)
    fig = plt.figure(figsize=(16, 10))
    fig.suptitle(f"{names}: as printed, bed at z=0", fontsize=12)
    _shade(fig.add_subplot(2, 2, 1, projection="3d"), meshes, 28, -60, "from an angle")
    _shade(fig.add_subplot(2, 2, 2, projection="3d"), meshes, 90, -90, "from above")
    for k, (axis, value) in enumerate(cuts[:2]):
        _, hname, _, vname = _CUTS[axis]
        polys = [cut(m, axis, value) for m in meshes]
        _plot_cut(fig.add_subplot(2, 2, 3 + k), polys, (hname, vname),
                  f"cut at {axis} = {value:.1f}")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path
