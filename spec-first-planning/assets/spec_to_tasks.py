#!/usr/bin/env python3
"""spec_to_tasks.py — compile a spec into a verifier-anchored task plan.

Parses a spec (see references/spec-template.md, lint it first with
spec_lint.py) and derives one task per requirement. A task is derivable
only when its requirement has at least one acceptance criterion — the
criterion becomes the task's verify step. A requirement with no criterion
yields no task and is reported as UNCOVERED, and the exit code is
non-zero: a plan with a hole is not a plan.

Usage:
    python3 spec_to_tasks.py <spec.md>          # markdown plan + coverage map
    python3 spec_to_tasks.py <spec.md> --json   # machine-readable plan only

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

WHERE_RE = re.compile(r"\s*\[where:\s*([^\]]+)\]", re.IGNORECASE)


def derive_plan(text):
    """Derive the task plan structure from spec markdown."""
    spec = spec_lint.parse_spec(text)
    crit_by_req = {}
    for ctext, refs in spec["criteria"]:
        for ref in refs:
            cleaned = re.sub(r"^R%d\s*[:.]\s*" % ref, "", ctext)
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
    as_json = "--json" in argv[1:]
    args = [a for a in argv[1:] if a != "--json"]
    if len(args) != 1:
        print("usage: spec_to_tasks.py <spec.md> [--json]", file=sys.stderr)
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
    return 0 if not plan["uncovered"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
