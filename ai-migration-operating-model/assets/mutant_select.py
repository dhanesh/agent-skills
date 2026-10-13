#!/usr/bin/env python3
"""mutant_select.py — run each mutant only against the scenarios that reach it.

A mutation proof shows the judge can fail: break the legacy code on purpose,
replay the suite, expect a diff. Replaying the WHOLE suite per mutant is the
expensive part: a handful of mutants over a few hundred scenarios took
more than 6 h for one suite. A scenario
that never executes the mutated lines produces the same output with or without
the mutant, so replaying it proves nothing. This tool picks, per mutant, the
scenarios whose coverage context executed the mutated lines.

    python3 mutant_select.py --coverage cov.json --mutants mutants/payments.json \\
        --src-root legacy/ [--plan plan.json]

Inputs:
- --coverage: per-scenario line coverage, either
    * coverage.py JSON with contexts (`coverage json --show-contexts`): the
      legacy rerun switches the context per request, for example a middleware
      calling `coverage.Coverage.current().switch_context(<scenario id>)`; or
    * a plain map {"<file>": {"<line>": ["<scenario>", ...]}} from any tool.
  The empty context "" means "ran outside any scenario": import time.
- --mutants: {"<name>": {"file": "<rel path>", "old": "<anchor>", "new": "..."}}
  ("lines": [n, ...] may replace "old"). The anchor must occur exactly once.

Per mutant, one mode:
- subset    scenarios that executed a mutated line; replay only those.
- full      a mutated line ran at import time (settings, URL tables, module
            constants), so it shapes every scenario: replay the full suite.
- unreached no scenario and no import-time run executed it. The mutant
            cannot be caught by this suite, so it is rejected (exit 1), never
            reported as a CAUGHT or MISSED result.

Why a subset is safe: dropping a scenario that does not reach the mutant can
only turn CAUGHT into MISSED, never MISSED into CAUGHT, so the proof gets no
easier. With coverage.py input, a mutated line that is part of a multi-line
statement maps to the nearest preceding statement line (the statement's first
line). A plain map has no statement data, so anchor such mutants on the
statement's first line.

Output: one "MUTANT: <name> <mode> <n> scenario(s)" line each, the JSON plan
with --plan, and a final "MUTANT_SELECT: PASS|FAIL (...)" line.
Stdlib-only, offline.
"""

import argparse
import json
import os
import sys


def load_coverage(path):
    """Returns ({file: {line: set(contexts)}}, {file: set(statement lines) | None}).

    Statement lines are known only for coverage.py JSON (executed + missing). A
    plain map lists executed lines only, so it cannot tell an unexecuted line
    from a continuation line: its entry is None and no line is remapped."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    ctx, measured = {}, {}
    if isinstance(raw, dict) and isinstance(raw.get("files"), dict):
        for fname, data in raw["files"].items():
            lines = {int(k): set(v) for k, v in (data.get("contexts") or {}).items()}
            ctx[fname] = lines
            measured[fname] = (set(data.get("executed_lines") or ()) |
                               set(data.get("missing_lines") or ()) | set(lines))
    else:
        for fname, lines in raw.items():
            ctx[fname] = {int(k): set(v) for k, v in lines.items()}
            measured[fname] = None
    return ctx, measured


def _find_file(cov_files, rel):
    rel = rel.replace(os.sep, "/").lstrip("./")
    hits = [f for f in cov_files
            if f.replace(os.sep, "/") == rel or f.replace(os.sep, "/").endswith("/" + rel)]
    return hits[0] if len(hits) == 1 else None


def anchor_lines(src_root, spec):
    if spec.get("lines"):
        return sorted(int(x) for x in spec["lines"])
    with open(os.path.join(src_root, spec["file"]), encoding="utf-8") as f:
        text = f.read()
    old = spec.get("old", "")
    if not old or text.count(old) != 1:
        raise ValueError("anchor not found exactly once in %s" % spec["file"])
    start = text[:text.index(old)].count("\n") + 1
    return list(range(start, start + old.count("\n") + 1))


def classify(lines, file_ctx, measured):
    """Returns (mode, scenarios) for one mutant's lines."""
    hit = set()
    for ln in lines:
        target = ln
        if measured is not None and target not in measured:
            earlier = [m for m in measured if m < ln]
            target = max(earlier) if earlier else ln
        hit |= file_ctx.get(target, set())
    if "" in hit:
        return "full", []
    if hit:
        return "subset", sorted(hit)
    return "unreached", []


def load_mutants(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out = {}
    for name, spec in raw.items():
        if isinstance(spec, list) and len(spec) == 3:  # [file, old, new]
            spec = {"file": spec[0], "old": spec[1], "new": spec[2]}
        out[name] = spec
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Per-mutant scenario selection from coverage contexts.")
    ap.add_argument("--coverage", required=True)
    ap.add_argument("--mutants", required=True)
    ap.add_argument("--src-root", default=".")
    ap.add_argument("--plan", help="write the JSON plan here")
    a = ap.parse_args(argv)

    ctx, measured = load_coverage(a.coverage)
    plan, bad, modes = {}, [], {"subset": 0, "full": 0, "unreached": 0}
    for name, spec in sorted(load_mutants(a.mutants).items()):
        try:
            lines = anchor_lines(a.src_root, spec)
        except (OSError, ValueError) as e:
            print("MUTANT: %s error (%s)" % (name, e))
            bad.append(name)
            continue
        cf = _find_file(ctx, spec["file"])
        if cf is None:
            mode, scen = "unreached", []
        else:
            mode, scen = classify(lines, ctx[cf], measured[cf])
        modes[mode] += 1
        plan[name] = {"mode": mode, "file": spec["file"], "lines": lines, "scenarios": scen}
        print("MUTANT: %s %s %s" % (name, mode, "%d scenario(s)" % len(scen)
                                     if mode == "subset" else
                                     ("all scenarios" if mode == "full" else
                                      "REJECTED: no scenario reaches lines %s" % lines)))
        if mode == "unreached":
            bad.append(name)
    if a.plan:
        tmp = a.plan + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=1, sort_keys=True)
        os.replace(tmp, a.plan)
    summary = "%d subset, %d full, %d unreached" % (modes["subset"], modes["full"], modes["unreached"])
    if bad:
        print("MUTANT_SELECT: FAIL (%s; rejected: %s)" % (summary, ", ".join(bad)))
        return 1
    print("MUTANT_SELECT: PASS (%s)" % summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
