#!/usr/bin/env python3
"""Replay the engine over the outcome corpus and say which rule caught what.

    python printcheck/calibrate.py             # the corpus (corpus/outcomes.tsv)
    python printcheck/calibrate.py --sweep     # plus every print file of every project
    python printcheck/calibrate.py --report corpus/report-2026-10-15.md

For every corpus row that still has a file, the engine runs the way the
print was sent (3MF, sliced 3MF, or loose STLs in print orientation) and
the verdict is held against the outcome: a print that failed must be
NO-GO, a print that held must not be. The per-rule table at the end shows,
for each finding code, how often it fired on failed prints and on held
ones; a rule that fires on held prints is a false alarm to look at, a
failure with no FAIL is the next rule to write.

Exit code 1 when a failure is not caught or a held print is NO-GO. A miss
the corpus already admits (`rule_now` = "missed ...") is printed as the open
problem it is and does not fail the run; a new miss does.
A threshold change that flips either is rejected by this script, not
argued in a conversation (research plan 2026-10-15, A2).
"""

import argparse
import collections
import csv
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(HERE, "preflight.py")
FAILED = {"came_off", "deformed", "broke", "came_apart", "nozzle_wrap", "aborted"}


def run_engine(files, rotate=None, extra=()):
    """The engine as a subprocess, so the replay sees exactly what the CLI does."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        out = tmp.name
    cmd = [sys.executable, ENGINE, *files, "--quiet-info", "--json", out, *extra]
    if rotate:
        cmd += ["--rotate", rotate]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    try:
        with open(out) as fh:
            data = json.load(fh)
    except Exception:
        data = {"verdict": "ERROR", "findings": [], "error": (r.stdout + r.stderr)[-500:]}
    finally:
        os.unlink(out)
    return data


def codes(data, level):
    return sorted({f"{i['stage'].split(':')[0]}/{i['code']}" for i in data["findings"] if i["level"] == level})


def corpus_rows():
    with open(os.path.join(HERE, "corpus", "outcomes.tsv")) as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def project_files():
    out = []
    for proj in sorted(os.listdir(ROOT)):
        parts = os.path.join(ROOT, proj, "parts")
        if not os.path.isdir(parts):
            continue
        for part in sorted(os.listdir(parts)):
            for folder in ("to_print", "printed", "old"):
                d = os.path.join(parts, part, "print", folder)
                if os.path.isdir(d):
                    out += [os.path.relpath(os.path.join(d, n), ROOT) for n in sorted(os.listdir(d))
                            if n.endswith(".3mf")]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sweep", action="store_true", help="also run every print file of every project (unlabelled)")
    ap.add_argument("--report", help="write the tables as Markdown here")
    ap.add_argument("--only", help="comma-separated corpus ids")
    a = ap.parse_args(argv)

    lines, bad = [], 0
    per_rule = collections.defaultdict(lambda: {"failed": 0, "held": 0, "unknown": 0})
    rows = corpus_rows()
    if a.only:
        keep = set(a.only.split(","))
        rows = [r for r in rows if r["id"] in keep]
    lines.append("## Corpus replay\n")
    lines.append("| id | date | print | outcome | verdict | FAIL | WARN | ok? |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        if r["file"] == "-" or r["kept"] == "no":
            lines.append(f"| {r['id']} | {r['date']} | {r['part']} ({r['project']}) | {r['outcome']} | not kept | | | n/a |")
            continue
        files = r["file"].split("+")
        missing = [f for f in files if not os.path.exists(os.path.join(ROOT, f))]
        if missing:
            lines.append(f"| {r['id']} | {r['date']} | {r['part']} | {r['outcome']} | FILE MISSING {missing} | | | n/a |")
            bad += 1
            continue
        data = run_engine(files, r["rotate"] or None)
        fails, warns = codes(data, "FAIL"), codes(data, "WARN")
        outcomes = set(r["outcome"].split("+"))
        failed = bool(outcomes & FAILED)
        held = outcomes == {"held"}
        bucket = "failed" if failed else "held" if held else "unknown"
        for c in fails + warns:
            per_rule[c][bucket] += 1
        if failed:
            ok = data["verdict"] == "NO-GO"
        elif held:
            ok = data["verdict"] != "NO-GO"
        else:
            ok = None
        # A miss the corpus already admits (`rule_now` starts with "missed")
        # is the open problem, not a regression: shown loudly, not counted.
        known_miss = failed and not ok and r["rule_now"].lower().startswith("missed")
        bad += ok is False and not known_miss
        mark = ("yes" if ok else "unlabelled" if ok is None
                else "**known miss (open)**" if known_miss
                else "**MISSED**" if failed else "**FALSE ALARM**")
        lines.append(f"| {r['id']} | {r['date']} | {r['part']} ({r['project']}) | {r['outcome']} | {data['verdict']} | "
                     f"{', '.join(fails) or '-'} | {', '.join(warns) or '-'} | {mark} |")
    lines.append("\n## Per rule (corpus rows with a file)\n")
    lines.append("| finding | on failed prints | on held prints | on unlabelled |")
    lines.append("|---|---|---|---|")
    for c, n in sorted(per_rule.items()):
        lines.append(f"| {c} | {n['failed']} | {n['held']} | {n['unknown']} |")

    if a.sweep:
        lines.append("\n## Sweep: every print file of every project (outcome not in the corpus unless listed above)\n")
        lines.append("| file | verdict | FAIL | WARN |")
        lines.append("|---|---|---|---|")
        labelled = {f for r in rows for f in r["file"].split("+")}
        for f in project_files():
            data = run_engine([f])
            fails, warns = codes(data, "FAIL"), codes(data, "WARN")
            tag = " (corpus)" if f in labelled else ""
            lines.append(f"| {f}{tag} | {data['verdict']} | {', '.join(fails) or '-'} | {', '.join(warns) or '-'} |")

    text = "\n".join(lines)
    print(text)
    if a.report:
        with open(a.report, "w") as fh:
            fh.write(f"# printcheck calibration report\n\nGenerated by `printcheck/calibrate.py`.\n\n{text}\n")
    print(f"\n{'OK' if not bad else 'NOT OK'}: {bad} corpus row(s) contradict the engine")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
