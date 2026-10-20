"""The command line: every input type and flag of preflight.main, the
Bambu Studio slicing hook, and the script entry point."""

import json
import os
import runpy
import stat
import sys
import zipfile

import pytest

from printcheck import preflight

from helpers import REPO, box, example, write_zip

ENGINE = os.path.join(REPO, "preflight.py")
FILM = example("bambu", "nogo_label_tower_film.gcode.3mf")


def run(capsys, *argv):
    code = preflight.main([str(a) for a in argv])
    return code, capsys.readouterr().out


def verdict(out):
    return out.strip().splitlines()[-1].split(":")[0]


def fake_bambu(tmp_path, body):
    """A stand-in for Bambu Studio's CLI: `body` is Python run with the same
    argv (argv[4] is the --export-3mf path), behind a launcher the OS can
    execute: a .cmd on Windows, a shell script elsewhere."""
    script = tmp_path / "fake_bambu.py"
    script.write_text("import shutil, sys\n" + body + "\n")
    if os.name == "nt":
        path = tmp_path / "BambuStudio.cmd"
        path.write_text(f'@"{sys.executable}" "{script}" %*\r\n@exit /b %errorlevel%\r\n')
    else:
        path = tmp_path / "BambuStudio"
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_stl_go_and_exit_code(capsys):
    code, out = run(capsys, example("stl", "go_block.stl"))
    assert code == 0 and verdict(out) == "GO"
    assert "INFO" in out


def test_stl_nogo_quiet_rotate_json_views_layers(capsys, tmp_path):
    js, views = tmp_path / "out.json", tmp_path / "views"
    code, out = run(capsys, example("stl", "nogo_post.stl"), "--rotate", "z,90", "--quiet-info",
                    "--json", js, "--views", views, "--layers", "5")
    assert code == 1 and verdict(out) == "NO-GO"
    body = out.split("\n\n")[0]
    assert "INFO" not in body                       # --quiet-info hides them from the findings
    assert "layers of 0.2 mm" in out                # the ledger, after the findings
    data = json.loads(js.read_text())
    assert data["verdict"] == "NO-GO" and data["simulation"]["rows"]
    assert any(f["stage"] == "views" for f in data["findings"])
    assert (views / "nogo_post_views.png").exists()


def test_rotate_keeps_companions_in_one_frame(capsys, tmp_path):
    js = tmp_path / "out.json"
    code, out = run(capsys, example("stl", "label_body.stl"), example("stl", "label_bar.stl"),
                    "--rotate", "x,180", "--json", js)
    data = json.loads(js.read_text())
    stages = {f["stage"] for f in data["findings"]}
    assert "inlay:label_bar.stl" in stages


def test_layers_all_and_at(capsys):
    _, out = run(capsys, example("stl", "caveats_tower.stl"), "--layers", "all")
    assert "one row per" not in out.split("layers of")[1].splitlines()[0]
    _, out = run(capsys, example("stl", "caveats_tower.stl"), "--at", "20")
    assert "layer n=" in out and "body 1:" in out


def test_rules_nozzle_layer_overrides(capsys, tmp_path):
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"max_aspect_warn": 10.0}))
    js = tmp_path / "out.json"
    code, out = run(capsys, example("stl", "caveats_tower.stl"), "--rules", rules,
                    "--nozzle", "0.6", "--layer", "0.25", "--json", js)
    data = json.loads(js.read_text())
    assert data["rules"]["max_aspect_warn"] == 10.0
    assert data["rules"]["nozzle"] == 0.6 and data["rules"]["bead"] == 0.63
    assert data["rules"]["layer"] == 0.25
    assert not any(f["code"] == "aspect" and f["level"] == "WARN" for f in data["findings"])


def test_3mf_unsliced_says_so(capsys):
    code, out = run(capsys, example("bambu", "go_block.3mf"))
    assert code == 0 and "pass --slice" in out


def test_3mf_with_views(capsys, tmp_path):
    code, out = run(capsys, example("bambu", "go_block.3mf"), "--views", tmp_path)
    assert (tmp_path / "go_block_views.png").exists()


def test_sliced_project_runs_the_gcode_stage(capsys):
    code, out = run(capsys, FILM)
    assert code == 1 and "tower_height" in out


def test_3mf_without_meshes(capsys, tmp_path):
    f = write_zip(tmp_path / "empty.3mf", {"3D/3dmodel.model": "<model><resources/></model>"})
    code, out = run(capsys, f)
    assert "no meshes found" in out and "no project_settings.config" in out


def test_bare_gcode(capsys, tmp_path):
    with zipfile.ZipFile(FILM) as z:
        text = z.read("Metadata/plate_1.gcode").decode()
    g = tmp_path / "plate_1.gcode"
    g.write_text(text)
    code, out = run(capsys, g)
    assert code == 1 and "gcode" in out and "tower_height" in out


def test_unknown_input(capsys, tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello")
    code, out = run(capsys, f)
    assert code == 0 and "not an STL, 3MF or G-code" in out


def test_slice_bambu_missing(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "BAMBU", str(tmp_path / "nowhere"))
    out, err = preflight.slice_project(example("bambu", "go_block.3mf"))
    assert out is None and "not found" in err
    code, text = run(capsys, example("bambu", "go_block.3mf"), "--slice")
    assert "Bambu Studio not found" in text


def test_slice_success(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "BAMBU", fake_bambu(tmp_path, f"shutil.copy({FILM!r}, sys.argv[4])"))
    out, err = preflight.slice_project(example("bambu", "go_block.3mf"))
    assert err is None and out.endswith("go_block.gcode.3mf") and os.path.exists(out)
    code, text = run(capsys, example("bambu", "go_block.3mf"), "--slice")
    assert "sliced with Bambu Studio CLI" in text and "tower_height" in text


def test_slice_failure(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "BAMBU", fake_bambu(tmp_path, "print('broken'); sys.exit(3)"))
    out, err = preflight.slice_project(example("bambu", "go_block.3mf"))
    assert out is None and "slicing failed (3)" in err and "broken" in err


def test_views_without_matplotlib(monkeypatch):
    from printcheck import views
    monkeypatch.setattr(views, "render", lambda parts, path: None)
    rep = preflight.Report()
    preflight._views("somewhere", "part.stl", [], rep)
    assert rep.items[-1]["level"] == "WARN" and "matplotlib missing" in rep.items[-1]["msg"]
    rep = preflight.Report()
    preflight._views(None, "part.stl", [], rep)
    assert rep.items == []


def test_script_mode_entry_point(monkeypatch, tmp_path, capsys):
    """`python preflight.py ...`: __package__ is empty, so the lazy imports of
    simulate and views take their sys.path branch."""
    monkeypatch.setattr(sys, "argv", [ENGINE, example("stl", "nogo_post.stl"), "--quiet-info",
                                      "--layers", "3", "--views", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(ENGINE, run_name="__main__")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "NO-GO" in out and "layers of 0.2 mm" in out
    assert (tmp_path / "nogo_post_views.png").exists()


def test_wrappers_in_package_mode(rep, rules):
    sim = preflight.check_simulation([{"name": "b", "mesh": box(10, 10, 4), "extruder": 1}], {}, rules, rep)
    assert sim["rows"] and "layers of" in preflight.format_ledger(sim, max_rows=3)
