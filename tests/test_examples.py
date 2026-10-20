"""The examples are current (rebuilt byte for byte) and each gives the
verdict its name promises."""

import importlib.util
import os

import pytest

from printcheck import preflight

from conftest import EXAMPLES, example


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_examples_are_not_stale(tmp_path):
    build = load(os.path.join(EXAMPLES, "build.py"), "examples_build")
    written = build.build(str(tmp_path))
    assert written
    for rel in written:
        with open(tmp_path / rel, "rb") as a, open(os.path.join(EXAMPLES, rel), "rb") as b:
            assert a.read() == b.read(), f"{rel} is stale: run python examples/build.py"


def expected(name):
    stem = os.path.basename(name)
    return ("NO-GO" if stem.startswith("nogo") else "GO WITH CAVEATS" if stem.startswith("caveats")
            else "GO")


FILES = [f"stl/{n}" for n in ("go_block.stl", "caveats_tower.stl", "nogo_post.stl")] + \
        [f"bambu/{n}" for n in sorted(os.listdir(os.path.join(EXAMPLES, "bambu")))]


@pytest.mark.parametrize("rel", FILES)
def test_verdict_matches_the_name(rel, capsys):
    code = preflight.main([example(rel), "--quiet-info"])
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.split(":")[0] == expected(rel)
    assert code == (1 if expected(rel) == "NO-GO" else 0)


def test_two_colour_stl_pair_is_go(capsys):
    code = preflight.main([example("stl", "label_body.stl"), example("stl", "label_bar.stl"), "--quiet-info"])
    assert code == 0 and capsys.readouterr().out.strip().splitlines()[-1].startswith("GO:")


def test_demo_project_suite(capsys):
    run = load(os.path.join(EXAMPLES, "workspace", "demo", "lib", "run.py"), "demo_run_examples")
    assert run.main([]) == 1
    out = capsys.readouterr().out
    assert "== go_block: GO\n" in out
    assert "== caveats_tower: GO WITH CAVEATS\n" in out
    assert "== nogo_post: NO-GO\n" in out
