"""The corpus replay: every outcome held against the verdict, on small
corpora so the suite stays quick."""

import csv
import os
import runpy
import stat
import sys

import pytest

from printcheck import calibrate

from conftest import REPO, example

FIELDS = open(os.path.join(REPO, "corpus", "outcomes.tsv")).readline().rstrip("\n").split("\t")


def tsv(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS, delimiter="\t", lineterminator="\n", restval="")
        w.writeheader()
        w.writerows(rows)
    return str(path)


def row(id_, file, outcome, **kw):
    return dict(id=id_, date="2026-10-20", project="t", part=id_, file=file, kept="yes",
                outcome=outcome, rule_now=kw.pop("rule_now", ""), **kw)


def test_example_corpus_is_green(capsys, tmp_path):
    report = tmp_path / "report.md"
    code = calibrate.main(["--corpus", example("corpus.tsv"), "--only", "E01,E03,E04,E05,E06",
                           "--report", str(report)])
    out = capsys.readouterr().out
    assert code == 0 and "OK: 0 corpus row(s)" in out
    assert "| E04 |" in out and "known miss (open)" in out
    assert "| E05 |" in out and "unlabelled" in out
    assert "| E06 |" in out and "not kept" in out
    assert "| E02 |" not in out                            # filtered by --only
    assert "| sim/sway | 1 | 0 | 0 |" in out
    assert report.read_text().startswith("# printcheck calibration report")


def test_contradictions_fail_the_run(capsys, tmp_path):
    corpus = tsv(tmp_path / "c.tsv", [
        row("M1", "examples/stl/go_block.stl", "came_off"),        # a failure called GO
        row("F1", "examples/stl/nogo_post.stl", "held"),           # a held print refused
        row("X1", "examples/stl/nowhere.stl", "held"),             # its file is gone
    ])
    assert calibrate.main(["--corpus", corpus]) == 1
    out = capsys.readouterr().out
    assert "**MISSED**" in out and "**FALSE ALARM**" in out and "FILE MISSING" in out
    assert "NOT OK: 3 corpus row(s)" in out


def test_sweep(capsys, tmp_path):
    corpus = tsv(tmp_path / "c.tsv", [row("S1", "examples/workspace/demo/parts/go_block/print/to_print/go_block.3mf",
                                          "held")])
    code = calibrate.main(["--corpus", corpus, "--sweep", example("workspace")])
    out = capsys.readouterr().out
    assert code == 0
    assert "| demo/parts/go_block/print/to_print/go_block.3mf (corpus) | GO |" in out
    assert "| demo/parts/nogo_post/print/to_print/nogo_post.3mf | NO-GO |" in out


def test_project_files_skips_strays(tmp_path):
    (tmp_path / "a_file.txt").write_text("")
    (tmp_path / "no_parts").mkdir()
    pdir = tmp_path / "proj" / "parts"
    (pdir / "empty_part").mkdir(parents=True)
    (pdir / "p" / "print" / "printed").mkdir(parents=True)
    (pdir / "p" / "print" / "printed" / "a.3mf").write_text("")
    (pdir / "p" / "print" / "printed" / "a.stl").write_text("")
    assert calibrate.project_files(str(tmp_path)) == [str(pdir / "p" / "print" / "printed" / "a.3mf")]


def test_engine_error_is_a_verdict(monkeypatch, tmp_path):
    broken = tmp_path / "engine.py"
    broken.write_text("import sys\nprint('boom')\nsys.exit(2)\n")
    monkeypatch.setattr(calibrate, "ENGINE", str(broken))
    data = calibrate.run_engine(["x.stl"], rotate="x,90")
    assert data["verdict"] == "ERROR" and data["findings"] == [] and "boom" in data["error"]


def test_script_entry_point(monkeypatch, tmp_path, capsys):
    corpus = tsv(tmp_path / "c.tsv", [dict(row("N1", "-", "held"), kept="no")])
    monkeypatch.setattr(sys, "argv", ["calibrate.py", "--corpus", corpus])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(os.path.join(REPO, "calibrate.py"), run_name="__main__")
    assert exc.value.code == 0 and "not kept" in capsys.readouterr().out
