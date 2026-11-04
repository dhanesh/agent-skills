#!/usr/bin/env python3
"""incident_testkit.py: a fixture repo for the incident-watch tests, eval and feature-map
recipe (stdlib only). It never touches a real endpoint.

fixture(dir) makes <dir> a git repo that holds a valid release-conductor recipe
(.release/recipe.json). Every command in the recipe runs one local stub script,
<dir>/stubs/stub.py, with the command's name as its argument. The stub:
  - appends its name as one line to <dir>/stubs/calls.log;
  - exits with the code in <dir>/stubs/behave/<name> (default 0).
So a test can make `health` fail with behave(dir, "health", 1), and can prove that
`rollback`, `deploy_prod` and the other non-probe commands were never called.

CLI: python3 incident_testkit.py fixture <dir> [--fail NAME ...]  (prints FIXTURE: <dir>)
"""
import argparse
import json
import os
import subprocess
import sys

STUB = r'''#!/usr/bin/env python3
import os, sys
HOME = os.path.dirname(os.path.abspath(__file__))
name = sys.argv[1] if len(sys.argv) > 1 else "?"
with open(os.path.join(HOME, "calls.log"), "a", encoding="utf-8") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
try:
    with open(os.path.join(HOME, "behave", name), encoding="utf-8") as f:
        code = int(f.read().strip() or 0)
except (OSError, ValueError):
    code = 0
print("stub %s exit %d" % (" ".join(sys.argv[1:]), code))
sys.exit(code)
'''

GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

# Recipe keys that are NOT probes. A call to any of them is a bug.
NON_PROBE = ("build", "deploy_staging", "deploy_prod", "rollback", "staging_check")


def recipe(d, smoke=2):
    """A valid release recipe whose every command runs the stub with its own name."""
    stub = os.path.join(d, "stubs", "stub.py")

    def cmd(name, *extra):
        return [sys.executable, stub, name] + list(extra)
    return {
        "build": cmd("build"), "deploy_staging": cmd("deploy_staging"),
        "deploy_prod": cmd("deploy_prod"), "rollback": cmd("rollback"),
        "health": cmd("health", "{env}"), "version_probe": cmd("version_probe"),
        "staging_checks": [cmd("staging_check")],
        "prod_smoke": [cmd("prod_smoke_%d" % i) for i in range(smoke)],
        "version": {"file": "package.json", "key": "version"},
        "bump": "patch", "artifact": "rebuild", "verify_skill": ".claude/skills/verify-x",
    }


def behave(d, name, code):
    os.makedirs(os.path.join(d, "stubs", "behave"), exist_ok=True)
    with open(os.path.join(d, "stubs", "behave", name), "w", encoding="utf-8") as f:
        f.write("%d\n" % code)


def calls(d):
    """The stub calls so far, one list of words per call."""
    try:
        with open(os.path.join(d, "stubs", "calls.log"), encoding="utf-8") as f:
            return [ln.split() for ln in f.read().splitlines() if ln.strip()]
    except OSError:
        return []


def write_recipe(d, data):
    os.makedirs(os.path.join(d, ".release"), exist_ok=True)
    with open(os.path.join(d, ".release", "recipe.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def fixture(d, fail=(), smoke=2):
    """Make d a git repo with the stub recipe. Each name in `fail` exits 1."""
    os.makedirs(os.path.join(d, "stubs"), exist_ok=True)
    if not os.path.isdir(os.path.join(d, ".git")):
        subprocess.run(["git", "init", "-q", "-b", "main", d], check=True,
                       stdout=subprocess.DEVNULL, env=dict(os.environ, **GIT))
    with open(os.path.join(d, "stubs", "stub.py"), "w", encoding="utf-8") as f:
        f.write(STUB)
    with open(os.path.join(d, "package.json"), "w", encoding="utf-8") as f:
        f.write('{"version": "1.0.0"}\n')
    write_recipe(d, recipe(d, smoke))
    for name in fail:
        behave(d, name, 1)
    return d


def main(argv=None):
    p = argparse.ArgumentParser(prog="incident_testkit.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fixture")
    f.add_argument("dir")
    f.add_argument("--fail", action="append", default=[],
                   help="a stub name that exits 1 (repeatable), such as health")
    args = p.parse_args(argv)
    d = fixture(os.path.abspath(args.dir), args.fail)
    print("FIXTURE: %s" % d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
