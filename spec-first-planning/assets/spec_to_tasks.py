#!/usr/bin/env python3
"""spec_to_tasks.py — compile a spec into a verifier-anchored task plan.

Parses a spec (see references/spec-template.md, lint it first with
spec_lint.py) and derives one task per requirement. A task is derivable
only when its requirement has at least one acceptance criterion — the
criterion becomes the task's verify step. A requirement with no criterion
yields no task and is reported as UNCOVERED, and the exit code is
non-zero: a plan with a hole is not a plan.

Usage:
    python3 spec_to_tasks.py <spec.md>                       # markdown plan + coverage map
    python3 spec_to_tasks.py <spec.md> --json                # machine-readable plan only
    python3 spec_to_tasks.py <spec.md> --envelope <root>     # also write a skill-contract
                                                             # task-plan envelope under
                                                             # <root>/.skill-contract/envelopes/

JSON shape:
    {"tasks":    [{"id": "T1", "requirement_ids": ["R1"],
                   "title": "...", "verify": "..."}, ...],
     "coverage": {"R1": ["T1"], ...},
     "uncovered": []}

An optional "[where: path/or/area]" hint inside a requirement's text is
lifted into the task's Where field (and a "where" key in JSON).
Deterministic: same spec in, byte-identical plan out. Exit 0 iff every
requirement is covered; exit 2 on unreadable/requirement-free input.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec_lint  # noqa: E402  (shared parser lives beside this script)
# contract_check (the vendored skill-contract checker, same dir) is imported only
# on the --envelope path: it requires Python >= 3.10 and exits 2 below that, so a
# plain or --json run must not load it.

TASK_PLAN_KIND = "https://github.com/dhanesh/agent-skills/skill-contract/task-plan/v1"
SKILL_NAME = "spec-first-planning"
SKILL_VERSION = "1.1.1"  # keep in step with SKILL.md metadata.version (a unit test checks)
USAGE = "usage: spec_to_tasks.py <spec.md> [--json] [--envelope <repo-root>]"

WHERE_RE = re.compile(r"\s*\[where:\s*([^\]]+)\]", re.IGNORECASE)


def derive_plan(text):
    """Derive the task plan structure from spec markdown."""
    spec = spec_lint.parse_spec(text)
    crit_by_req = {}
    for ctext, refs, owner in spec["criteria"]:
        # Ownership drives coverage: a criterion belongs to the requirement in
        # its leading `R<n>:` prefix, not to every R<n> token that happens to
        # appear in it. Without this, "R1: run `grep R2 fixtures.txt`" reported
        # R2 as covered by a check that proves nothing about it — and, because
        # `refs` was iterated without deduping, appended the same verify step
        # twice with a foreign `R1:` prefix still attached.
        targets = [owner] if owner is not None else refs
        for ref in dict.fromkeys(targets):
            cleaned = re.sub(r"^(?:\*\*)?R%d(?:\*\*)?\s*[:.]\s*" % ref, "", ctext)
            crit_by_req.setdefault(ref, []).append(cleaned)

    tasks = []
    coverage = {}
    uncovered = []
    tnum = 0
    for num, rtext in spec["requirements"]:
        rid = "R%d" % num
        steps = crit_by_req.get(num, [])
        if not steps:
            coverage[rid] = []
            uncovered.append(rid)
            continue
        tnum += 1
        where_m = WHERE_RE.search(rtext)
        title = WHERE_RE.sub("", rtext).strip().rstrip(".")
        tasks.append(
            {
                "id": "T%d" % tnum,
                "requirement_ids": [rid],
                "title": title,
                "verify": "; ".join(steps),
                "_verify_steps": steps,
                "_where": where_m.group(1).strip() if where_m else "",
            }
        )
        coverage[rid] = ["T%d" % tnum]

    return {
        "title": spec["title"],
        "tasks": tasks,
        "coverage": coverage,
        "uncovered": uncovered,
    }


def to_json(plan):
    """Public JSON shape (internal underscore keys stripped)."""
    out_tasks = []
    for t in plan["tasks"]:
        jt = {
            "id": t["id"],
            "requirement_ids": t["requirement_ids"],
            "title": t["title"],
            "verify": t["verify"],
        }
        if t["_where"]:
            jt["where"] = t["_where"]
        out_tasks.append(jt)
    return {
        "tasks": out_tasks,
        "coverage": plan["coverage"],
        "uncovered": plan["uncovered"],
    }


def to_task_plan_payload(plan, spec_rel):
    """The task-plan/v1 payload (assets/schemas/task-plan.v1.json): verify steps stay a list."""
    tasks = []
    for t in plan["tasks"]:
        jt = {"id": t["id"], "requirement_ids": t["requirement_ids"], "title": t["title"],
              "verify": [{"text": s, "command": None} for s in t["_verify_steps"]]}
        if t["_where"]:
            jt["where"] = t["_where"]
        tasks.append(jt)
    return {"title": plan["title"], "spec": spec_rel, "tasks": tasks,
            "coverage": plan["coverage"], "uncovered": plan["uncovered"]}


def payload_errors(payload):
    """Structural check of a task-plan/v1 payload. [] means valid."""
    if not isinstance(payload, dict):
        return ["payload must be an object"]
    errs = []
    for key, typ in (("title", str), ("spec", str), ("tasks", list), ("coverage", dict),
                     ("uncovered", list)):
        if not isinstance(payload.get(key), typ):
            errs.append("payload.%s must be a %s" % (key, typ.__name__))
    for i, t in enumerate(payload.get("tasks") if isinstance(payload.get("tasks"), list) else []):
        if not isinstance(t, dict):
            errs.append("tasks[%d] must be an object" % i)
            continue
        if not isinstance(t.get("id"), str) or not isinstance(t.get("title"), str):
            errs.append("tasks[%d] needs string id and title" % i)
        rids = t.get("requirement_ids")
        if not (isinstance(rids, list) and rids and all(isinstance(r, str) for r in rids)):
            errs.append("tasks[%d].requirement_ids must be a non-empty list of strings" % i)
        verify = t.get("verify")
        if not (isinstance(verify, list) and verify):
            errs.append("tasks[%d].verify must be a non-empty list" % i)
            continue
        for j, item in enumerate(verify):
            cmd = item.get("command") if isinstance(item, dict) else None
            if not (isinstance(item, dict) and isinstance(item.get("text"), str)
                    and (cmd is None or (isinstance(cmd, list) and cmd
                                         and all(isinstance(a, str) for a in cmd)))):
                errs.append("tasks[%d].verify[%d] must be {text, command: list or null}" % (i, j))
    return errs


def write_task_plan_envelope(plan, spec_path, root):
    """Write the plan as a skill-contract task-plan/v1 envelope; return its path."""
    import contract_check  # lazy: needs Python >= 3.10 (see the note at the imports)

    root = os.path.abspath(root)
    rel = os.path.relpath(os.path.abspath(spec_path), root).replace(os.sep, "/")
    if rel == ".." or rel.startswith("../") or os.path.isabs(rel):
        raise ValueError("the spec %s is outside the repo root %s" % (spec_path, root))
    with open(spec_path, encoding="utf-8") as f:
        text = f.read()
    payload = to_task_plan_payload(plan, rel)
    errs = payload_errors(payload)
    if errs:
        raise ValueError("; ".join(errs))
    here = "{skill_dir:%s}/assets" % SKILL_NAME
    claims = [
        contract_check.assertion("spec-lint", SKILL_NAME,
                                 "passed" if not spec_lint.lint(text) else "failed",
                                 root, [rel], command=["{python}", here + "/spec_lint.py", rel]),
        contract_check.assertion("coverage-total", SKILL_NAME,
                                 "passed" if not plan["uncovered"] else "failed",
                                 root, [rel], command=["{python}", here + "/spec_to_tasks.py", rel]),
    ]
    statement = contract_check.build_statement(TASK_PLAN_KIND, SKILL_NAME, SKILL_VERSION, root,
                                               [rel], payload, claims)
    return contract_check.write_envelope(root, statement)


def render_markdown(plan, spec_name):
    lines = []
    lines.append("# Task plan — %s" % (plan["title"] or spec_name))
    lines.append("")
    lines.append(
        "Derived from `%s`. One task per requirement; each task names the "
        "check that proves it done." % spec_name
    )
    for t in plan["tasks"]:
        lines.append("")
        lines.append("## %s — %s" % (t["id"], t["title"]))
        lines.append("")
        lines.append("- Satisfies: %s" % ", ".join(t["requirement_ids"]))
        lines.append(
            "- Where: %s"
            % (t["_where"] or "unspecified — fill in during plan review")
        )
        lines.append("- Verify:")
        for step in t["_verify_steps"]:
            lines.append("  - [ ] %s" % step)
    lines.append("")
    lines.append("## Coverage")
    lines.append("")
    lines.append("| Requirement | Task(s) |")
    lines.append("|---|---|")
    for rid, tids in plan["coverage"].items():
        lines.append("| %s | %s |" % (rid, ", ".join(tids) or "NONE — UNCOVERED"))
    if plan["uncovered"]:
        lines.append("")
        lines.append("## Uncovered requirements")
        lines.append("")
        for rid in plan["uncovered"]:
            lines.append(
                "- %s has no derivable task (no acceptance criterion to anchor "
                "a verify step) — repair the spec, then re-derive." % rid
            )
    return "\n".join(lines) + "\n"


def main(argv):
    args = list(argv[1:])
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    envelope_root = None
    if "--envelope" in args:
        i = args.index("--envelope")
        if i + 1 >= len(args):
            print(USAGE, file=sys.stderr)
            return 2
        envelope_root = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        with open(args[0], encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        print("ERROR: cannot read %s: %s" % (args[0], exc), file=sys.stderr)
        return 2

    plan = derive_plan(text)
    if not plan["coverage"]:
        print(
            "ERROR: no 'R<n>:' requirements found in %s — lint the spec with "
            "spec_lint.py first" % args[0],
            file=sys.stderr,
        )
        return 2

    if as_json:
        print(json.dumps(to_json(plan), indent=2))
    else:
        sys.stdout.write(render_markdown(plan, os.path.basename(args[0])))
        for rid, tids in plan["coverage"].items():
            print("COVERAGE: %s -> %s" % (rid, ", ".join(tids) or "(none)"))
        for rid in plan["uncovered"]:
            print("UNCOVERED: %s" % rid)
        total = len(plan["coverage"])
        covered = total - len(plan["uncovered"])
        print(
            "TASKS_RESULT: %s (%d/%d requirements covered by %d task(s))"
            % (
                "PASS" if not plan["uncovered"] else "FAIL",
                covered,
                total,
                len(plan["tasks"]),
            )
        )
    if plan["uncovered"]:
        if envelope_root is not None:
            print("ERROR: not writing an envelope for a plan with uncovered requirements",
                  file=sys.stderr)
        return 1
    if envelope_root is not None:
        try:
            path = write_task_plan_envelope(plan, args[0], envelope_root)
        except (OSError, ValueError) as exc:
            print("ERROR: cannot write the envelope: %s" % exc, file=sys.stderr)
            return 2
        print("ENVELOPE: %s" % path, file=sys.stderr if as_json else sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
