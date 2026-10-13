#!/usr/bin/env python3
"""impact.py — which golden suites a change can affect, failing closed to ALL.

    python3 impact.py --config impact.json record --status <run>/status.json
    python3 impact.py --config impact.json suites        # ALL | NONE | suite names
    python3 impact.py --config impact.json changed-units # units for race/sanitiser runs

The baseline is a content-hash manifest, not git history, so it works in a tree
with no commits and in worker copies. `record` writes it, and only after a green
FULL run: it reads the e2e_shard.py status.json and refuses unless the verdict
is PASS and the run covered all suites (--force overrides, and says so).

Config (JSON; every path is relative to "root", default the config's directory):

  {
    "manifest":    "out/e2e/manifest.json",
    "track":       ["internal/*", "cmd/*", "go.mod", "go.sum", "migration/*"],
    "exclude":     ["*/__pycache__/*", "*/.venv/*", "migration/judge/out/*"],
    "full_on":     ["internal/app/", "internal/platform/", "go.mod", "go.sum",
                    "migration/judge/judge.py", "migration/parity_check.sh"],
    "suite_files": ["^migration/judge/(golden|scenarios|mutants)/(?P<suite>[a-z0-9_]+?)(__|\\\\.json)"],
    "units":       ["^(?P<unit>internal/.+)/[^/]+\\\\.go$"],
    "golden":      "migration/judge/golden",
    "sep":         "__",
    "deps_cmd":    "go list -deps -f '{{.ImportPath}}' ./internal/{suite}/... | sed -n 's#^example.com/svc/##p'"
  }

Globs use fnmatch, so `*` also matches `/`. Selection rules, in order:
- no manifest                         -> ALL
- nothing changed                     -> NONE
- a changed file under any "full_on"  -> ALL  (routing/wiring, shared platform,
  config, build files, sidecars, the judge's own tooling)
- a changed file matching "suite_files" selects that suite directly; an
  unknown suite name -> ALL
- a changed file matching "units" is a dependency-graph unit; a suite is
  selected when deps_cmd (run once per suite, {suite} substituted) lists that
  unit. deps_cmd failing, or missing while units changed -> ALL
- any other changed file              -> ALL  (unmappable)

Point deps_cmd at each suite's own package, never at the app-wiring package
that imports everything, or every change selects every suite. Selection is an
optimisation: keep a nightly FULL run. Stdlib-only.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules"}


def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("root", ".")
    cfg["root"] = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(path)), cfg["root"]))
    cfg.setdefault("manifest", "out/e2e/manifest.json")
    for k in ("track", "exclude", "full_on", "suite_files", "units"):
        cfg.setdefault(k, [])
    cfg.setdefault("sep", "__")
    return cfg


def _rel_files(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for f in sorted(files):
            yield os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/")


def tracked(cfg):
    out = {}
    manifest = os.path.normpath(cfg["manifest"]).replace(os.sep, "/")
    for rel in _rel_files(cfg["root"]):
        if rel == manifest or rel.startswith(manifest + "."):
            continue
        if not any(fnmatch.fnmatch(rel, g) for g in cfg["track"]):
            continue
        if any(fnmatch.fnmatch(rel, g) for g in cfg["exclude"]):
            continue
        with open(os.path.join(cfg["root"], rel), "rb") as f:
            out[rel] = hashlib.sha256(f.read()).hexdigest()
    return out


def _manifest_path(cfg):
    return os.path.join(cfg["root"], cfg["manifest"])


def changed(cfg):
    try:
        with open(_manifest_path(cfg), encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        return None
    new = tracked(cfg)
    return sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))


def known_suites(cfg):
    if "suites" in cfg:
        return set(cfg["suites"])
    gdir = os.path.join(cfg["root"], cfg.get("golden", ""))
    if not cfg.get("golden") or not os.path.isdir(gdir):
        return set()
    return {f.split(cfg["sep"], 1)[0] for f in os.listdir(gdir)
            if f.endswith(".json") and cfg["sep"] in f}


def _match(patterns, rel, group):
    for p in patterns:
        m = re.search(p, rel)
        if m and m.groupdict().get(group):
            return m.group(group)
    return None


def select(cfg, run_cmd=None):
    """Returns (selection, reasons): selection is "ALL" or a sorted list."""
    run_cmd = run_cmd or _run_deps
    ch = changed(cfg)
    if ch is None:
        return "ALL", ["no manifest: run a full e2e, then `impact.py record`"]
    if not ch:
        return [], []
    known = known_suites(cfg)
    direct, units = set(), set()
    for f in ch:
        if any(f == p or f.startswith(p) for p in cfg["full_on"]):
            return "ALL", ["shared, wiring or tooling change: %s" % f]
        s = _match(cfg["suite_files"], f, "suite")
        if s is not None:
            if s not in known:
                return "ALL", ["file of unknown suite %r: %s" % (s, f)]
            direct.add(s)
            continue
        u = _match(cfg["units"], f, "unit")
        if u is not None:
            units.add(u)
            continue
        return "ALL", ["unmappable file: %s" % f]
    sel, used, why = set(direct), set(), []
    if units:
        if not cfg.get("deps_cmd"):
            return "ALL", ["units changed but no deps_cmd to map them"]
        for s in sorted(known):
            deps = run_cmd(cfg["deps_cmd"].replace("{suite}", s), cfg["root"])
            if deps is None:
                return "ALL", ["deps_cmd failed for suite %s" % s]
            hit = deps & units
            if hit:
                sel.add(s)
                used |= hit
        why += ["changed unit %s is used by no suite (unit tests only)" % u
                for u in sorted(units - used)]
    return sorted(sel), why


def changed_units(cfg):
    ch = changed(cfg)
    if ch is None:
        return None
    return sorted({u for u in (_match(cfg["units"], f, "unit") for f in ch) if u})


def _run_deps(cmd, root):
    p = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True)
    if p.returncode != 0:
        return None
    return {line.strip() for line in p.stdout.splitlines() if line.strip()}


def record(cfg, status_path, force=False):
    """Write the manifest; returns its file count. Raises ValueError when refused."""
    if not force:
        try:
            with open(status_path, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError, TypeError):
            raise ValueError("IMPACT: refusing to record: no readable status.json (%s)" % status_path)
        if not str(st.get("verdict", "")).startswith("PARITY_RESULT: PASS"):
            raise ValueError("IMPACT: refusing to record: last run is not green (%s)" % st.get("verdict"))
        if st.get("suites") != "all":
            raise ValueError("IMPACT: refusing to record: last run was impact-selected, not FULL")
    path = _manifest_path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    m = tracked(cfg)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=0, sort_keys=True)
    os.replace(tmp, path)
    return len(m)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Golden suites a change can affect (fails closed).")
    ap.add_argument("--config", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--status", help="status.json of the green FULL e2e run")
    r.add_argument("--force", action="store_true")
    s = sub.add_parser("suites")
    s.add_argument("--json", action="store_true")
    sub.add_parser("changed-units")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)

    if a.cmd == "record":
        try:
            n = record(cfg, a.status, a.force)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        print("IMPACT: manifest recorded (%d files)%s" % (n, " [forced]" if a.force else ""))
        return 0
    if a.cmd == "changed-units":
        u = changed_units(cfg)
        print("ALL" if u is None else " ".join(u))
        return 0
    sel, why = select(cfg)
    for w in why:
        print("# %s" % w, file=sys.stderr)
    if a.json:
        print(json.dumps({"selection": sel, "reasons": why}))
    else:
        print("ALL" if sel == "ALL" else (" ".join(sel) if sel else "NONE"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
