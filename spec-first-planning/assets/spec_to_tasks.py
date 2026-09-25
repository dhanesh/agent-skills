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
    python3 spec_to_tasks.py <spec.md> --waves                # WAVE/CRITICAL_PATH lines only
    python3 spec_to_tasks.py <spec.md> --envelope <root>     # also write a skill-contract
                                                             # task-plan envelope under
                                                             # <root>/.skill-contract/envelopes/

JSON shape:
    {"tasks":    [{"id": "T1", "requirement_ids": ["R1"],
                   "title": "...", "verify": "...", "depends_on": ["T0"],
                   "verify_commands": [["{python}", "-m", "pytest"], null]}, ...],
     "coverage": {"R1": ["T1"], ...},
     "uncovered": []}

An optional "[where: path/or/area]" hint inside a requirement's text is
lifted into the task's Where field (and a "where" key in JSON). An optional
"[after: R2, R3]" hint becomes the derived task's "depends_on" — the ids of
the task(s) that cover R2 and R3 — and is stripped from the title exactly
like "[where: ...]" is. depends_on is omitted from a task with no hint, so a
spec with no hints derives a byte-identical plan to before this existed.

An optional trailing "[cmd: <argv>]" hint on an acceptance criterion (see
spec_lint.py) is split off the step's text and becomes that verify step's
"command" in the task-plan/v1 envelope; a criterion without one gets
"command": null. --json lists the commands as "verify_commands" (aligned with
the steps; omitted when no step has one) and markdown shows each after its step.

--waves groups the derived tasks into waves from their depends_on and prints
"WAVE <n>: <ids>" lines (one per wave, ids sorted numerically — T10 after
T2, not before), then "CRITICAL_PATH: T1 -> T2 -> T4" (the longest
depends_on chain; a tie is broken by the chain whose ids sort first
numerically; with no dependencies at all it is a single task), then
"WAVES_RESULT: PASS (n wave(s))" and exits 0. A schedule error (an unknown
or cyclic dependency) prints "ERROR: ..." to stderr and exits non-zero — see
waves() below, a copy of factory-conductor/assets/conductor.py's waves().

Deterministic: same spec in, byte-identical plan out. Exit 0 iff every
requirement is covered; exit 2 on unreadable/requirement-free input.
"""

import json
import math
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec_lint  # noqa: E402  (shared parser lives beside this script)
# contract_check (the vendored skill-contract checker, same dir) is imported only
# on the --envelope path: it requires Python >= 3.10 and exits 2 below that, so a
# plain or --json run must not load it.

TASK_PLAN_KIND = "https://github.com/dhanesh/agent-skills/skill-contract/task-plan/v1"
SKILL_NAME = "spec-first-planning"
SKILL_VERSION = "2.2.1"  # keep in step with SKILL.md metadata.version (a unit test checks)
USAGE = "usage: spec_to_tasks.py <spec.md> [--json] [--waves] [--envelope <repo-root>]"

WHERE_RE = re.compile(r"\s*\[where:\s*([^\]]+)\]", re.IGNORECASE)


class PlanError(Exception):
    """The plan cannot be scheduled: a depends_on cycle, or a dependency that does
    not exist."""


def _tid_key(tid):
    """Sort key for a "T<n>" task id: numeric, so T10 sorts after T2."""
    return int(tid[1:])


# waves() below is copied from factory-conductor/assets/conductor.py's waves(tasks)
# (not imported — skills stay self-contained, no cross-skill imports) so --waves can
# schedule the derived plan the same way factory-conductor would run it. The one
# deliberate difference: this copy sorts task ids numerically (_tid_key, so T10 comes
# after T2) everywhere conductor.py's copy uses a plain sorted() — see the comment on
# conductor.py's waves() for that half of the pair.
def waves(tasks):
    """Group task ids into waves. Every id in a wave is independent of the others."""
    for k, t in tasks.items():
        deps = t.get("depends_on") or []
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise PlanError("task %s: depends_on must be a list of task id strings" % k)
    unknown = {d for t in tasks.values() for d in t.get("depends_on") or []} - set(tasks)
    if unknown:
        raise PlanError("depends_on names unknown task(s): %s" % ", ".join(sorted(unknown, key=_tid_key)))
    left = {k: set(v.get("depends_on") or []) for k, v in tasks.items()}
    out = []
    while left:
        wave = sorted((k for k, deps in left.items() if not deps), key=_tid_key)
        if not wave:
            raise PlanError("depends_on has a cycle among: %s" % ", ".join(sorted(left, key=_tid_key)))
        out.append(wave)
        for k in wave:
            del left[k]
        for deps in left.values():
            deps.difference_update(wave)
    return out


def critical_path(tasks):
    """The longest depends_on chain, as a list of task ids from root to leaf.

    A tie (equal-length chains) is broken by the chain whose ids sort first
    numerically, compared element by element. With no dependencies at all, every
    chain has length 1 and the result is the single lowest-numbered task id.
    Assumes `tasks` is already known acyclic (call waves() first).
    """
    best = {}

    def chain(tid):
        if tid not in best:
            deps = tasks[tid].get("depends_on") or []
            if not deps:
                best[tid] = (1, [tid])
            else:
                candidates = [chain(d) for d in deps]
                longest = max(length for length, _ in candidates)
                top = sorted(
                    (path for length, path in candidates if length == longest),
                    key=lambda path: [_tid_key(t) for t in path],
                )[0]
                best[tid] = (longest + 1, top + [tid])
        return best[tid]

    chains = [chain(tid) for tid in tasks]
    longest = max(length for length, _ in chains)
    return sorted(
        (path for length, path in chains if length == longest),
        key=lambda path: [_tid_key(t) for t in path],
    )[0]


def derive_plan(text):
    """Derive the task plan structure from spec markdown."""
    spec = spec_lint.parse_spec(text)
    crit_by_req = {}
    for (ctext, refs, owner), raw in zip(spec["criteria"], spec["commands"]):
        # A [cmd: ...] hint is the step's command; one that does not parse is left
        # null here (spec_lint reports it, and the envelope's spec-lint claim fails).
        cmd = spec_lint.command_argv(raw)[0] if raw is not None else None
        # Ownership drives coverage: a criterion belongs to the requirement in
        # its leading `R<n>:` prefix, not to every R<n> token that happens to
        # appear in it. Without this, "R1: run `grep R2 fixtures.txt`" reported
        # R2 as covered by a check that proves nothing about it — and, because
        # `refs` was iterated without deduping, appended the same verify step
        # twice with a foreign `R1:` prefix still attached.
        targets = [owner] if owner is not None else refs
        for ref in dict.fromkeys(targets):
            cleaned = re.sub(r"^(?:\*\*)?R%d(?:\*\*)?\s*[:.]\s*" % ref, "", ctext)
            crit_by_req.setdefault(ref, []).append((cleaned, cmd))

    tasks = []
    coverage = {}
    uncovered = []
    tnum = 0
    for num, rtext in spec["requirements"]:
        rid = "R%d" % num
        pairs = crit_by_req.get(num, [])
        steps = [text for text, _ in pairs]
        if not steps:
            coverage[rid] = []
            uncovered.append(rid)
            continue
        tnum += 1
        where_m = WHERE_RE.search(rtext)
        # [after: ...] is lifted into depends_on (resolved below, once every
        # requirement has a task id) and stripped from the title exactly like
        # [where: ...] is.
        title = spec_lint.AFTER_RE.sub("", WHERE_RE.sub("", rtext)).strip().rstrip(".")
        tasks.append(
            {
                "id": "T%d" % tnum,
                "requirement_ids": [rid],
                "title": title,
                "verify": "; ".join(steps),
                "_verify_steps": steps,
                "_verify_cmds": [cmd for _, cmd in pairs],
                "_where": where_m.group(1).strip() if where_m else "",
                "_after": spec["after"].get(num, []),
            }
        )
        coverage[rid] = ["T%d" % tnum]

    # Resolve each task's depends_on now that every requirement's covering
    # task id is known: an [after: Rn] hint maps to the task(s) coverage[Rn]
    # names. A requirement with no hint gets no depends_on key at all, which
    # is what keeps a plan derived from a spec with no hints byte-identical.
    for t in tasks:
        deps = []
        for anum in t["_after"]:
            deps.extend(coverage.get("R%d" % anum, []))
        deps = list(dict.fromkeys(deps))
        if deps:
            t["depends_on"] = deps

    return {
        "title": spec["title"],
        "tasks": tasks,
        "coverage": coverage,
        "uncovered": uncovered,
        "constraints": spec["constraints"],
        "required_truths": spec["truths"],
        "decisions": spec["decisions"],
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
        if t.get("depends_on"):
            jt["depends_on"] = t["depends_on"]
        if any(c is not None for c in t["_verify_cmds"]):
            jt["verify_commands"] = t["_verify_cmds"]
        out_tasks.append(jt)
    return {
        "tasks": out_tasks,
        "coverage": plan["coverage"],
        "uncovered": plan["uncovered"],
    }


def _confidence_well_formed(conf):
    return (isinstance(conf, (int, float)) and not isinstance(conf, bool)
            and math.isfinite(conf) and 0.0 <= conf <= 1.0)


def _truth_well_formed(t):
    """A required-truth dict is well formed for the payload: id/status/text/parent/check are
    non-empty strings, maps_to/reqs are lists, and confidence is a finite number in [0, 1].

    spec_lint.parse_spec leaves confidence None (unparsable, e.g. 'confidence: high') or a
    non-finite/out-of-range float (e.g. 'confidence: nan') when the spec's field is bad —
    that is a lint failure to repair, not a value to carry into the envelope (a bare NaN
    isn't even valid JSON).
    """
    if not _confidence_well_formed(t.get("confidence")):
        return False
    for key in ("id", "status", "text", "parent", "check"):
        if not isinstance(t.get(key), str) or not t[key]:
            return False
    return isinstance(t.get("maps_to"), list) and isinstance(t.get("reqs"), list)


def to_task_plan_payload(plan, spec_rel):
    """The task-plan/v1 payload (assets/schemas/task-plan.v1.json): verify steps stay a list.

    Each step's `command` is its criterion's `[cmd: ...]` argv, or null when the criterion
    has none (factory-conductor refuses a plan with a null command at init).

    `constraints`, `required_truths` and `decisions` are optional (task-plan/v1 stays v1: the
    change is additive) and are added only when the spec the plan was derived from has them.
    `required_truths` is added only when every truth is well formed (see
    `_truth_well_formed`) — a spec with a malformed truth already fails spec-lint (recorded
    in the envelope's `spec-lint` claim), so the payload omits the field rather than carry a
    broken or unserializable value.
    """
    tasks = []
    for t in plan["tasks"]:
        jt = {"id": t["id"], "requirement_ids": t["requirement_ids"], "title": t["title"],
              "verify": [{"text": s, "command": c}
                         for s, c in zip(t["_verify_steps"], t["_verify_cmds"])]}
        if t["_where"]:
            jt["where"] = t["_where"]
        if t.get("depends_on"):
            jt["depends_on"] = t["depends_on"]
        tasks.append(jt)
    payload = {"title": plan["title"], "spec": spec_rel, "tasks": tasks,
              "coverage": plan["coverage"], "uncovered": plan["uncovered"]}
    if plan.get("constraints"):
        payload["constraints"] = [{"id": c["id"], "type": c["type"], "text": c["text"]}
                                  for c in plan["constraints"]]
    if plan.get("required_truths"):
        truths = [{k: v for k, v in t.items() if k != "num"} for t in plan["required_truths"]]
        if all(_truth_well_formed(t) for t in truths):
            payload["required_truths"] = truths
    if plan.get("decisions"):
        payload["decisions"] = [{"id": d["id"], "question": d["question"], "answer": d["answer"],
                                 "source": d["source"]} for d in plan["decisions"]]
    return payload


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
        if "depends_on" in t:
            deps = t.get("depends_on")
            if not (isinstance(deps, list) and deps and all(isinstance(d, str) for d in deps)):
                errs.append("tasks[%d].depends_on must be a non-empty list of task id strings" % i)
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
    if "constraints" in payload:
        cons = payload["constraints"]
        if not isinstance(cons, list):
            errs.append("payload.constraints must be a list")
        else:
            for i, c in enumerate(cons):
                if not (isinstance(c, dict) and isinstance(c.get("id"), str)
                        and isinstance(c.get("type"), str) and isinstance(c.get("text"), str)):
                    errs.append("constraints[%d] must be {id, type, text}" % i)
    if "required_truths" in payload:
        truths = payload["required_truths"]
        if not isinstance(truths, list):
            errs.append("payload.required_truths must be a list")
        else:
            for i, t in enumerate(truths):
                if not (isinstance(t, dict) and isinstance(t.get("id"), str)
                        and isinstance(t.get("status"), str) and isinstance(t.get("text"), str)
                        and isinstance(t.get("parent"), str)
                        and isinstance(t.get("maps_to"), list)
                        and isinstance(t.get("reqs"), list)
                        and _confidence_well_formed(t.get("confidence"))
                        and isinstance(t.get("check"), str)):
                    errs.append("required_truths[%d] must be {id, status, text, parent, maps_to, "
                                "reqs, confidence: a finite number in [0, 1], check}" % i)
    if "decisions" in payload:
        decisions = payload["decisions"]
        if not isinstance(decisions, list):
            errs.append("payload.decisions must be a list")
        else:
            for i, d in enumerate(decisions):
                if not (isinstance(d, dict)
                        and all(isinstance(d.get(k), str) for k in ("id", "question", "answer", "source"))):
                    errs.append("decisions[%d] must be {id, question, answer, source}" % i)
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


def show_command(argv):
    """argv as one shell-style line: an argument is quoted only when it needs it, so
    {python} and {skill_dir:...} placeholders read as the spec wrote them."""
    return " ".join(a if re.fullmatch(r"[\w@%+=:,./{}-]+", a) else shlex.quote(a)
                    for a in argv)


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
        for step, cmd in zip(t["_verify_steps"], t["_verify_cmds"]):
            shown = " (cmd: `%s`)" % show_command(cmd) if cmd is not None else ""
            lines.append("  - [ ] %s%s" % (step, shown))
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
    as_waves = "--waves" in args
    args = [a for a in args if a != "--waves"]
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

    if as_waves:
        if not plan["tasks"]:
            print(
                "ERROR: no derivable tasks to schedule (every requirement is "
                "uncovered) in %s" % args[0],
                file=sys.stderr,
            )
            return 2
        tasks_by_id = {t["id"]: {"depends_on": t.get("depends_on") or []} for t in plan["tasks"]}
        try:
            ws = waves(tasks_by_id)
        except PlanError as exc:
            print("ERROR: %s" % exc, file=sys.stderr)
            return 2
        for i, w in enumerate(ws, start=1):
            print("WAVE %d: %s" % (i, " ".join(w)))
        print("CRITICAL_PATH: %s" % " -> ".join(critical_path(tasks_by_id)))
        print("WAVES_RESULT: PASS (%d wave(s))" % len(ws))
        return 0

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
