"""The adapter for projects in the kailua layout, driven through the demo
project in examples/workspace/demo."""

import importlib.util
import os
import types

import pytest

from printcheck import layout, presets, views

from conftest import box, example

DEMO = example("workspace", "demo")


def demo_run():
    spec = importlib.util.spec_from_file_location("demo_run", os.path.join(DEMO, "lib", "run.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def run():
    return demo_run()


def levels(section):
    return [lv for lv, _ in section.items]


def test_settings_without_presets(monkeypatch):
    monkeypatch.setattr(presets, "available", lambda: False)
    spec = types.SimpleNamespace(BAMBU={"filaments": [("Bambu PLA Basic @BBL A2L", "white")],
                                        "process": "0.20mm Standard @BBL A2L",
                                        "process_overrides": {"brim_type": "outer_only", "brim_width": "5",
                                                              "wall_loops": "3"}})
    st = layout._settings(spec)
    assert st == {"filament_type": ["PLA"], "printer_structure": "i3",
                  "brim_type": "outer_only", "brim_width": "5"}
    # no BAMBU at all: the spec's MATERIAL, or PETG
    assert layout._settings(types.SimpleNamespace(MATERIAL="PLA"))["filament_type"] == ["PLA"]
    assert layout._settings(types.SimpleNamespace())["filament_type"] == ["PETG"]


def test_settings_with_presets(monkeypatch):
    seen = {}
    monkeypatch.setattr(presets, "available", lambda: True)
    monkeypatch.setattr(presets, "resolve", lambda bambu, material: seen.update(b=bambu, m=material) or {"ok": 1})
    spec = types.SimpleNamespace(BAMBU={"process": "p", "filaments": [("Bambu PETG HF @BBL A2L", "black")]})
    assert layout._settings(spec) == {"ok": 1} and seen["m"] == "PETG"
    # presets installed but no process named: the short path
    assert "filament_type" in layout._settings(types.SimpleNamespace(BAMBU={"filaments": []}))


def test_demo_go_block_with_companion(run, monkeypatch):
    monkeypatch.setattr(presets, "available", lambda: False)
    s = run.run(["go_block"])["go_block"]
    assert not s.failed and "WARN" not in levels(s)
    msgs = " ".join(m for _, m in s.items)
    assert "inlay:go_block+go_block_bar" in msgs          # the companion rides with its body
    assert "[views]" in msgs and "Look at it" in msgs
    assert os.path.exists(os.path.join(DEMO, "parts", "go_block", "previews", "go_block_views.png"))


def test_demo_cut_pieces(run, monkeypatch):
    monkeypatch.setattr(presets, "available", lambda: False)
    s = run.run(["nogo_post"])["nogo_post"]
    assert s.failed
    msgs = [m for _, m in s.items]
    assert any(m.startswith("nogo_post piece 1:") for m in msgs)
    assert any(m.startswith("nogo_post piece 2:") for m in msgs)


def fake_ctx(tmp_path, print_files, meshes, spec=None):
    part = types.SimpleNamespace(
        dir=str(tmp_path / "part"), previews_dir=str(tmp_path / "part" / "previews"),
        print_files=print_files, variant=lambda name: name, rotation_of=lambda variant: ("z", 0))
    return types.SimpleNamespace(part=part, meshes=meshes, spec=spec or types.SimpleNamespace())


def test_no_print_files(run, tmp_path):
    s = run.Section("t")
    layout.printability(fake_ctx(tmp_path, [], {}), s)
    assert s.items == [("INFO", "no print files declared: nothing to judge")]


def test_variant_without_mesh_and_explicit_rotation(run, tmp_path, monkeypatch):
    monkeypatch.setattr(presets, "available", lambda: False)
    monkeypatch.setattr(views, "render", lambda parts, path: None)
    pfs = [run.PrintFile("ghost", "ghost"), run.PrintFile("cube", "cube", rotation=("x", 180))]
    s = run.Section("t")
    layout.printability(fake_ctx(tmp_path, pfs, {"cube": box(20, 20, 10)}), s)
    assert s.items[0] == ("FAIL", "ghost: variant 'ghost' has no mesh")
    assert ("WARN", "cube: [views] matplotlib missing in this venv, no figure written") in s.items
    assert any(lv == "OK" for lv, _ in s.items)
