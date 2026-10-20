"""What printcheck's layout adapter asks of a project: how a part turns into
print orientation, and how a cut piece splits into the bodies on the bed.

A real project's lib/printing.py also exports the files; the adapter only
imports these two.
"""

import numpy as np
import trimesh

AXES = {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}


def orientation(spec):
    """Authored -> printed, as a proper rotation so winding and normals survive."""
    axis, deg = spec
    return trimesh.transformations.rotation_matrix(np.radians(deg), AXES[axis])


def pieces(mesh, cut):
    """One mesh per piece, the part split by the plane axis = value and capped,
    each piece standing on z = 0."""
    if not cut:
        return [mesh]
    axis, value = cut
    i = "xyz".index(axis)
    normal, origin = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    origin[i] = value
    out = []
    for sign in (-1.0, 1.0):
        normal[i] = sign
        piece = trimesh.intersections.slice_mesh_plane(mesh, normal, origin, cap=True)
        piece.apply_translation([0, 0, -piece.bounds[0][2]])
        out.append(piece)
    return out
