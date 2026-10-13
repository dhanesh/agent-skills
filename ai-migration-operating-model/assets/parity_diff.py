#!/usr/bin/env python3
"""parity_diff.py — the mechanical judge for behavioral parity.

Compares the OLD system's output against the NEW system's output and
reports every behavioral difference. This is what keeps a migration from
being vibes-based: "the code looks fine" is replaced by "the diff is zero
or explicitly explained".

Two modes:

  Single case:   python3 parity_diff.py old.json new.json
  Golden corpus: python3 parity_diff.py old-outputs/ new-outputs/
                 (matches *.json files by basename; a scenario present on
                  one side only is a parity failure, not a skip)

Options:
  --ignore k1,k2    drop these object keys (at any depth) before comparing
                    (timestamps, request ids — noise that is not behavior)
  --tolerance F     absolute tolerance for numeric comparisons (default 0)
  --require L=PATH  an effect class the judge must record (repeatable). Each
                    golden is checked for PATH; every place it is absent counts
                    as "unjudged" for label L: the differ cannot compare what
                    nobody recorded. PATH is dotted keys; `name[]` walks a
                    list, `name[k=v]` walks only list items whose k equals v.
                    Examples: `cache=cache_writes`,
                    `http-headers=effects[kind=http].headers`. A missing
                    intermediate key means "no such effect", not unjudged.
  --fail-on-unjudged  make any unjudged count a failure (exit 1)

Output: one "DIFF: ..." line per difference (with a $.path[i] locator),
"SCENARIO: <name> — PASS|FAIL" per scenario in corpus mode, then a final
"PARITY_RESULT: PASS (...)" or "PARITY_RESULT: FAIL (...)" line. With
--require, one "UNJUDGED: <label> <n>" line per class precedes the verdict, and
the verdict ends with ", unjudged: <total>" (a coverage gate wants 0).
Exit 0 iff parity holds; 1 on any diff; 2 on usage errors.
Stdlib-only, offline, deterministic.
"""

import argparse
import json
import os
import re
import sys


def normalize(value, ignore=frozenset()):
    """Strip ignored keys (any depth) so noise never counts as behavior."""
    if isinstance(value, dict):
        return {
            k: normalize(v, ignore)
            for k, v in value.items()
            if k not in ignore
        }
    if isinstance(value, list):
        return [normalize(v, ignore) for v in value]
    return value


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def diff_values(old, new, tolerance=0.0, path="$"):
    """Return a list of human-readable diff strings between two values."""
    diffs = []
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            sub = "%s.%s" % (path, key)
            if key not in new:
                diffs.append("%s — missing in new" % sub)
            elif key not in old:
                diffs.append("%s — unexpected in new" % sub)
            else:
                diffs.extend(diff_values(old[key], new[key], tolerance, sub))
        return diffs
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            diffs.append(
                "%s — length mismatch (old=%d, new=%d)"
                % (path, len(old), len(new))
            )
        for i in range(min(len(old), len(new))):
            diffs.extend(
                diff_values(old[i], new[i], tolerance, "%s[%d]" % (path, i))
            )
        return diffs
    if _is_number(old) and _is_number(new):
        if abs(old - new) > tolerance:
            diffs.append(
                "%s — value mismatch (old=%r, new=%r)" % (path, old, new)
            )
        return diffs
    if type(old) is not type(new):
        diffs.append(
            "%s — type mismatch (old=%s, new=%s)"
            % (path, type(old).__name__, type(new).__name__)
        )
        return diffs
    if old != new:
        diffs.append("%s — value mismatch (old=%r, new=%r)" % (path, old, new))
    return diffs


def diff_files(old_path, new_path, ignore=frozenset(), tolerance=0.0):
    """Diff two JSON files; unparseable JSON is itself a parity failure."""
    loaded = []
    for side, p in (("old", old_path), ("new", new_path)):
        try:
            with open(p, encoding="utf-8") as f:
                loaded.append(json.load(f))
        except ValueError as e:
            return ["$ — invalid JSON in %s: %s" % (side, e)]
        except OSError as e:
            return ["$ — unreadable %s output: %s" % (side, e)]
    old, new = (normalize(v, ignore) for v in loaded)
    return diff_values(old, new, tolerance)


def _scenario_names(d):
    return sorted(
        f for f in os.listdir(d)
        if f.endswith(".json") and os.path.isfile(os.path.join(d, f))
    )


def run(old, new, ignore=frozenset(), tolerance=0.0, out=sys.stdout):
    """Run the comparison; returns the total number of diffs found."""
    if os.path.isdir(old) and os.path.isdir(new):
        old_names, new_names = _scenario_names(old), _scenario_names(new)
        names = sorted(set(old_names) | set(new_names))
        total = 0
        for name in names:
            if name not in new_names:
                print("DIFF: %s — scenario missing in new" % name, file=out)
                print("SCENARIO: %s — FAIL (1 diff(s))" % name, file=out)
                total += 1
                continue
            if name not in old_names:
                print(
                    "DIFF: %s — scenario missing in old "
                    "(unexpected new output)" % name, file=out
                )
                print("SCENARIO: %s — FAIL (1 diff(s))" % name, file=out)
                total += 1
                continue
            diffs = diff_files(
                os.path.join(old, name), os.path.join(new, name),
                ignore, tolerance,
            )
            for d in diffs:
                print("DIFF: %s:%s" % (name, d), file=out)
            if diffs:
                print(
                    "SCENARIO: %s — FAIL (%d diff(s))" % (name, len(diffs)),
                    file=out,
                )
            else:
                print("SCENARIO: %s — PASS" % name, file=out)
            total += len(diffs)
        return len(names), total
    diffs = diff_files(old, new, ignore, tolerance)
    for d in diffs:
        print("DIFF: %s" % d, file=out)
    return 1, len(diffs)


def _walk(nodes, token):
    m = re.match(r"^([^\[\]]+)(?:\[([^\]]*)\])?$", token)
    if not m:
        raise ValueError("bad --require path token: %r" % token)
    name, sel = m.group(1), m.group(2)
    out = []
    for n in nodes:
        if not isinstance(n, dict) or name not in n:
            continue
        v = n[name]
        if sel is None:
            out.append(v)
            continue
        items = v if isinstance(v, list) else []
        if sel:
            k, _, want = sel.partition("=")
            items = [i for i in items if isinstance(i, dict) and str(i.get(k)) == want]
        out.extend(items)
    return out


def missing_at(value, path):
    """How many places in `value` lack the leaf of `path` (see --require)."""
    tokens = path.split(".")
    nodes = [value]
    for t in tokens[:-1]:
        nodes = _walk(nodes, t)
    leaf = tokens[-1]
    return sum(1 for n in nodes if isinstance(n, dict) and leaf not in n)


def parse_requires(specs):
    out = []
    for spec in specs or ():
        label, sep, path = spec.partition("=")
        if not sep or not label or not path:
            raise ValueError("--require wants LABEL=PATH, got %r" % spec)
        missing_at({}, path)  # validates the token grammar early
        out.append((label, path))
    return out


def count_unjudged(old, requires):
    """{label: places missing} over the golden side (a file or a directory)."""
    files = ([os.path.join(old, f) for f in _scenario_names(old)]
             if os.path.isdir(old) else [old])
    counts = {label: 0 for label, _ in requires}
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                v = json.load(fh)
        except (OSError, ValueError):
            continue  # already reported as a diff by run()
        for label, path in requires:
            counts[label] += missing_at(v, path)
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Behavioral parity diff between old and new outputs."
    )
    ap.add_argument("old", help="old system output: a .json file or a dir")
    ap.add_argument("new", help="new system output: a .json file or a dir")
    ap.add_argument(
        "--ignore", default="",
        help="comma-separated object keys to drop at any depth",
    )
    ap.add_argument(
        "--tolerance", type=float, default=0.0,
        help="absolute tolerance for numeric comparisons (default 0)",
    )
    ap.add_argument(
        "--require", action="append", default=[], metavar="LABEL=PATH",
        help="effect class the goldens must record; absences count as unjudged",
    )
    ap.add_argument(
        "--fail-on-unjudged", action="store_true",
        help="exit 1 when any --require class has unjudged places",
    )
    args = ap.parse_args(argv)
    try:
        requires = parse_requires(args.require)
    except ValueError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 2

    for p in (args.old, args.new):
        if not os.path.exists(p):
            print("ERROR: no such path: %s" % p, file=sys.stderr)
            return 2
    if os.path.isdir(args.old) != os.path.isdir(args.new):
        print(
            "ERROR: old and new must both be files or both be directories",
            file=sys.stderr,
        )
        return 2

    ignore = frozenset(k for k in args.ignore.split(",") if k)
    scenarios, diffs = run(args.old, args.new, ignore, args.tolerance)
    tail, unjudged = "", 0
    if requires:
        counts = count_unjudged(args.old, requires)
        for label, n in counts.items():
            print("UNJUDGED: %s %d" % (label, n))
        unjudged = sum(counts.values())
        tail = ", unjudged: %d" % unjudged
    failed = diffs > 0 or (args.fail_on_unjudged and unjudged > 0)
    if not failed:
        print("PARITY_RESULT: PASS (%d scenario(s), 0 diff(s)%s)" % (scenarios, tail))
        return 0
    print(
        "PARITY_RESULT: FAIL (%d diff(s) across %d scenario(s)%s)"
        % (diffs, scenarios, tail)
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
