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

CLI: python3 pipeline_testkit.py stubs <dir>   (prints STUBS: <dir>)
"""
import json
import os
import sys

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
    for (skill, key), b in DEFAULTS.items():
        behave(d, skill, key, **b)
    return d


def calls(d):
    try:
        with open(os.path.join(d, "calls.jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except OSError:
        return []


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "stubs":
        print("usage: pipeline_testkit.py stubs <dir>")
        sys.exit(2)
    print("STUBS: %s" % stubs(os.path.abspath(sys.argv[2])))
