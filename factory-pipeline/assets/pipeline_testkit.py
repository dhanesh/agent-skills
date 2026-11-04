#!/usr/bin/env python3
"""pipeline_testkit.py: stub sibling skills for the factory-pipeline tests, eval and
feature-map recipe (stdlib only).

stubs(dir) writes ops-intake, growth-review and spec-first-planning stubs under dir.
Each stub tool:
  - appends {"skill", "key", "argv", "stdin"} as one JSON line to <dir>/calls.jsonl;
  - prints the stdout and stderr, and exits with the code, that
    <dir>/behave/<skill>.<key>.json holds (key: the command, such as `sweep`, `auto`,
    `import`, `autopick` or `under-pipeline`).
behave(dir, skill, key, stdout=..., stderr=..., exit=...) changes one answer, and
calls(dir) reads the log back. The stubs change nothing else.

The stub spec-first-planning also gets a copy of the real `assets/contract_check.py` of the
spec-first-planning skill beside factory-pipeline, so the tick's pipeline-grant check (R13)
runs the real checker. pipeline_grant(repo) writes a real pipeline grant into a fixture
repo with that skill's own writer (`write_grant.build_pipeline_grant`, the code behind
`write_grant.py --pipeline`), after it writes a pick policy and a command allowlist for the
grant to pin. `now` moves the grant's clock back, so a test can write an expired grant.

CLI: python3 pipeline_testkit.py stubs <dir>   (prints STUBS: <dir>)
     python3 pipeline_testkit.py grant <repo>  (prints GRANT: <path>)
"""
import datetime
import json
import os
import shutil
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLANNING_ASSETS = os.path.join(os.path.dirname(SKILL_DIR), "spec-first-planning", "assets")
CHECKER = os.path.join(PLANNING_ASSETS, "contract_check.py")

STUB = r'''#!/usr/bin/env python3
import json, os, sys
SKILL = %(skill)r
HOME = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
args = sys.argv[1:]
if args[:1] == ["--root"]:
    args = args[2:]
key = args[0].lstrip("-") if args else ""
data = sys.stdin.buffer.read().decode("utf-8", "replace")
with open(os.path.join(HOME, "calls.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"skill": SKILL, "key": key, "argv": sys.argv[1:], "stdin": data}) + "\n")
try:
    with open(os.path.join(HOME, "behave", "%%s.%%s.json" %% (SKILL, key)), encoding="utf-8") as f:
        b = json.load(f)
except OSError:
    b = {"stdout": "", "stderr": "", "exit": 0}
sys.stdout.write(b.get("stdout", ""))
sys.stderr.write(b.get("stderr", ""))
sys.exit(b.get("exit", 0))
'''

TOOLS = {
    "ops-intake": "assets/intake.py",
    "growth-review": "assets/growth.py",
    "spec-first-planning": "assets/write_grant.py",
}

SIGNAL = json.dumps({"source": "growth", "source_id": "signups", "severity": 3,
                     "title": "metric: signups breached"})

DEFAULTS = {
    ("ops-intake", "sweep"): {"stdout": "IMPORT: added 1\nSWEEP: 1 commands ok 1 failed 0\n"},
    ("growth-review", "auto"): {"stdout": SIGNAL + "\n", "stderr": "SIGNALS: 1\n"},
    ("ops-intake", "import"): {"stdout": "IMPORT: added 1 updated 0 problems 0\n"},
    ("ops-intake", "autopick"): {"stdout": (
        "POLICY: abc by `Owner` at 2026-11-04T00:00:00Z\n"
        "MATCH: 3 new 5 `ci red`\n"
        "ITEM: 3 new 5 `ci red`\n"
        "NEXT: run spec-first-planning with .skill-contract/intake/envelopes/item-3.json\n"
        "AUTOPICK: matched 1 picked 1\n")},
    ("spec-first-planning", "under-pipeline"): {
        "stdout": "GRANT: .skill-contract/envelopes/autonomy-grant-v1-x.json\n"},
}


def behave(d, skill, key, stdout="", stderr="", exit=0):
    os.makedirs(os.path.join(d, "behave"), exist_ok=True)
    with open(os.path.join(d, "behave", "%s.%s.json" % (skill, key)), "w",
              encoding="utf-8") as f:
        json.dump({"stdout": stdout, "stderr": stderr, "exit": exit}, f)


def stubs(d, skills=None):
    """Write the stub skills under d (all of them, or only those named) and return d."""
    for skill, rel in TOOLS.items():
        if skills is not None and skill not in skills:
            continue
        tool = os.path.join(d, skill, rel)
        os.makedirs(os.path.dirname(tool), exist_ok=True)
        with open(os.path.join(d, skill, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: %s\ndescription: stub for the pipeline tests\n---\n" % skill)
        with open(tool, "w", encoding="utf-8") as f:
            f.write(STUB % {"skill": skill})
        if skill == "spec-first-planning":
            shutil.copyfile(CHECKER, os.path.join(d, skill, "assets", "contract_check.py"))
    for (skill, key), b in DEFAULTS.items():
        behave(d, skill, key, **b)
    return d


GRANT_ANSWERS = {
    "branch_pattern": "factory/*",
    "gate_policy": {"read_only": "auto", "local_reversible": "auto",
                    "push_branch": "grant", "open_pr": "grant",
                    "merge": "ask", "deploy": "ask", "spend": "ask",
                    "external_message": "ask", "delete": "ask"},
    "caps": {"max_plans_per_day": 3, "max_open_runs": 1, "max_dispatches_per_day": 40},
    "budget": {"max_parallel": 2, "max_repairs_per_task": 2, "wall_clock_min": 1440},
    "stop_on": ["a new dependency is needed"],
}


def pipeline_grant(repo, now=None, days=3, accepted_by="Owner"):
    """Write a real pipeline-grant/v1 into repo and return its path. The grant is
    generated at `now` (default: the current second) and expires `days` later."""
    for rel, text in ((".intake/pick-policy.json", '{"kinds": ["bug"]}\n'),
                      (".factory/check-commands.txt", "make test\n")):
        path = os.path.join(repo, *rel.split("/"))
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
    if PLANNING_ASSETS not in sys.path:
        sys.path.insert(0, PLANNING_ASSETS)
    import contract_check  # noqa: E402  (spec-first-planning's vendored checker)
    import write_grant  # noqa: E402
    now = now or contract_check.utc_now()
    answers = dict(GRANT_ANSWERS, expires_at=(now + datetime.timedelta(days=days))
                   .strftime("%Y-%m-%dT%H:%M:%SZ"))
    st = write_grant.build_pipeline_grant(repo, answers, accepted_by, now=now)
    return contract_check.write_envelope(repo, st)


def calls(d):
    try:
        with open(os.path.join(d, "calls.jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except OSError:
        return []


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("stubs", "grant"):
        print("usage: pipeline_testkit.py stubs <dir> | grant <repo>")
        sys.exit(2)
    if sys.argv[1] == "stubs":
        print("STUBS: %s" % stubs(os.path.abspath(sys.argv[2])))
    else:
        print("GRANT: %s" % pipeline_grant(os.path.abspath(sys.argv[2])))
