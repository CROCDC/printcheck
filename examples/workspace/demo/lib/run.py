#!/usr/bin/env python3
"""The demo project's suite: printcheck on every print file of every part.

    python examples/workspace/demo/lib/run.py [part ...]

A project in the kailua layout keeps its parts under parts/<part>/ (here a
spec.py and the meshes; a real one also has the OpenSCAD source and its
own checks) and ends its suite with the PRINTABILITY section from
printcheck. Exit code 1 when any part FAILs, as in a real project.
"""

import importlib.util
import os
import sys

import trimesh

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)            # so the adapter finds this project's `lib`

from printcheck import layout       # noqa: E402


class Section:
    """The slice of a project report the adapter writes into."""

    def __init__(self, title):
        self.title, self.items = title, []

    def _add(self, level, msg):
        self.items.append((level, msg))

    def fail(self, msg): self._add("FAIL", msg)
    def warn(self, msg): self._add("WARN", msg)
    def ok(self, msg): self._add("OK", msg)
    def info(self, msg): self._add("INFO", msg)

    @property
    def failed(self):
        return any(level == "FAIL" for level, _ in self.items)


class PrintFile:
    def __init__(self, name, variant, rotation=None, cut=None, also=()):
        self.name, self.variant, self.rotation = name, variant, rotation
        self.cut, self.also = (tuple(cut) if cut else None), list(also)


class Part:
    def __init__(self, path, spec):
        self.dir, self.name = path, os.path.basename(path)
        self.previews_dir = os.path.join(path, "previews")
        self.print_files = [PrintFile(**pf) for pf in getattr(spec, "PRINT_FILES", [])]
        self._rotation = getattr(spec, "ROTATION", ("z", 0))

    def variant(self, name):
        return name

    def rotation_of(self, variant):
        return self._rotation


class Context:
    def __init__(self, path):
        loader = importlib.util.spec_from_file_location(f"spec_{os.path.basename(path)}",
                                                        os.path.join(path, "spec.py"))
        self.spec = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(self.spec)
        self.part = Part(path, self.spec)
        mdir = os.path.join(path, "meshes")
        self.meshes = {n[:-4]: trimesh.load(os.path.join(mdir, n), force="mesh")
                       for n in sorted(os.listdir(mdir)) if n.endswith(".stl")}


def run(names=None):
    """{part: Section} for the named parts, or every part."""
    root = os.path.join(HERE, "parts")
    out = {}
    for name in names or sorted(os.listdir(root)):
        ctx = Context(os.path.join(root, name))
        s = Section("PRINTABILITY (printcheck)")
        layout.printability(ctx, s)
        out[name] = s
    return out


def main(argv=None):
    results = run(argv if argv else None)
    for name, s in results.items():
        verdict = "NO-GO" if s.failed else "GO WITH CAVEATS" if any(lv == "WARN" for lv, _ in s.items) else "GO"
        print(f"== {name}: {verdict}")
        for level, msg in s.items:
            if level != "INFO":
                print(f"   {level:4s} {msg}")
    return 1 if any(s.failed for s in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
