"""Helpers the test modules import: shapes, finding lookups, the example
files and a writer for hand-made 3MF projects. Fixtures are in conftest.py."""

import os
import zipfile

import trimesh

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(REPO, "examples")


def box(sx, sy, sz, x=0.0, y=0.0, z=0.0):
    b = trimesh.creation.box((sx, sy, sz))
    b.apply_translation((x, y, z + sz / 2))
    return b


def part(mesh, name="shape", extruder=1, **extra):
    return {"name": name, "mesh": mesh, "extruder": extruder, **extra}


def found(rep, stage=None):
    """{(stage prefix, code): level} of a report, last one wins."""
    return {(i["stage"].split(":")[0], i["code"]): i["level"] for i in rep.items
            if stage is None or i["stage"].split(":")[0] == stage}


def example(*parts_):
    return os.path.join(EXAMPLES, *parts_)


def write_zip(path, files):
    """A 3MF (or any zip) from {name: text}."""
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return str(path)
