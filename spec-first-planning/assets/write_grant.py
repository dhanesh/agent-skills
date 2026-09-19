#!/usr/bin/env python3
"""write_grant.py — build an autonomy-grant/v1 envelope after the user says yes.

Called only after the user has explicitly accepted the grant summary spec-first-planning
showed them (design spec §4, "Unattended mode, opt-in"). It refuses (never writes an
envelope) unless the spec is decision-closed under `spec_lint.py --unattended` and the
resulting statement passes the reference checker's structural and grant-specific checks —
so a malformed or over-broad grant is a bug caught here, not something a consumer has to
notice later.

Usage:
    python3 write_grant.py --root <repo-root> --spec <repo-relative spec path>
                           --plan <task-plan envelope path> --answers <answers.json>
                           --accepted-by <human name>

answers.json keys:
    branch_pattern (str, required)   -- a glob, e.g. "factory/*"
    gate_policy    (dict, required)  -- action class -> "auto" | "grant" | "ask"
    expires_at     (str, required)   -- RFC 3339 UTC, at most 7 days after generatedAtTime
    budget, stop_on, defaults, system_one (optional; default {}, [], [], {"allowed": false})

Exit 0: prints "GRANT: <path>".
Exit 1: refused; prints "REFUSED: <reason>".
Exit 2: usage error.

Stdlib only. contract_check (vendored, same dir) needs Python >= 3.10 and is imported
lazily, mirroring spec_to_tasks.py's style, so importing this module doesn't force that
floor on callers that only want GrantRefused or the constants.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec_lint  # noqa: E402  (shared parser lives beside this script)
from spec_to_tasks import SKILL_VERSION  # noqa: E402

USAGE = ("usage: write_grant.py --root DIR --spec REL_SPEC --plan PLAN_ENVELOPE_PATH "
        "--answers ANSWERS.json --accepted-by NAME")


class GrantRefused(Exception):
    """The grant was not written: the spec, the answers, or the resulting statement failed
    a check. Its message is the single reason to show the user."""


def build_grant(root, spec_rel, plan_rel, answers, accepted_by, now=None):
    """Build (but do not write) the autonomy-grant/v1 statement.

    Raises GrantRefused when the spec is not decision-closed, `accepted_by` is blank, or the
    resulting statement fails contract_check's structural (C3-C6) or grant-specific (C10)
    checks — including the 7-day lifetime cap, which grant_violations enforces.
    """
    import contract_check  # lazy: needs Python >= 3.10 (see the module docstring)

    with open(os.path.join(root, *spec_rel.split("/")), encoding="utf-8") as f:
        text = f.read()
    issues = spec_lint.lint(text, mode="unattended")
    if issues:
        raise GrantRefused("spec is not decision-closed: " + issues[0])
    if not (isinstance(accepted_by, str) and accepted_by.strip()):
        raise GrantRefused("--accepted-by must name the human who said yes")
    spec = spec_lint.parse_spec(text)
    payload = {
        "scope": {"repo": ".", "branch_pattern": answers["branch_pattern"]},
        "decisions": [{"id": d["id"], "question": d["question"], "answer": d["answer"],
                       "source": d["source"]} for d in spec["decisions"]],
        "defaults": answers.get("defaults", []),
        "gate_policy": answers["gate_policy"],
        "budget": answers.get("budget", {}),
        "stop_on": answers.get("stop_on", []),
        "expires_at": answers["expires_at"],
        "system_one": answers.get("system_one", {"allowed": False}),
        "revoked": False,
    }
    accepted = {"test": "grant-accepted", "assertedBy": {"human": accepted_by.strip()},
                "result": {"outcome": "passed"},
                "command": ["{python}", "{skill_dir:spec-first-planning}/assets/spec_lint.py",
                            "--unattended", spec_rel],
                "subject": [contract_check.pin(root, spec_rel)]}
    st = contract_check.build_statement(contract_check.GRANT_KIND, "spec-first-planning",
                                        SKILL_VERSION, root, [spec_rel, plan_rel], payload,
                                        [accepted], now=now)
    viol = ["C%d: %s" % v for v in contract_check.check_statement(st)] \
        or contract_check.grant_violations(st)
    if viol:
        raise GrantRefused(viol[0])
    return st


def build_parser():
    ap = argparse.ArgumentParser(prog="write_grant.py",
                                 description="build the autonomy-grant/v1 envelope after the "
                                             "user says yes")
    ap.add_argument("--root", required=True, help="repo root")
    ap.add_argument("--spec", required=True, help="the spec path, relative to --root")
    ap.add_argument("--plan", required=True,
                    help="the task-plan envelope path (absolute, or relative to --root)")
    ap.add_argument("--answers", required=True, help="path to a JSON file of answers")
    ap.add_argument("--accepted-by", required=True, dest="accepted_by",
                    help="the human who said yes")
    return ap


def main(argv=None):
    try:
        a = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if exc.code else 0

    root = os.path.abspath(a.root)
    plan_abs = a.plan if os.path.isabs(a.plan) else os.path.join(root, a.plan)
    plan_rel = os.path.relpath(os.path.abspath(plan_abs), root).replace(os.sep, "/")
    if plan_rel == ".." or plan_rel.startswith("../") or os.path.isabs(plan_rel):
        print("REFUSED: --plan %s is outside --root %s" % (a.plan, root))
        return 1

    try:
        with open(a.answers, encoding="utf-8") as f:
            answers = json.load(f)
    except (OSError, ValueError) as exc:
        print(USAGE, file=sys.stderr)
        print("usage: cannot read --answers %s: %s" % (a.answers, exc), file=sys.stderr)
        return 2
    if not isinstance(answers, dict):
        print(USAGE, file=sys.stderr)
        print("usage: --answers must be a JSON object", file=sys.stderr)
        return 2
    missing = [k for k in ("branch_pattern", "gate_policy", "expires_at") if k not in answers]
    if missing:
        print(USAGE, file=sys.stderr)
        print("usage: --answers is missing %s" % ", ".join(missing), file=sys.stderr)
        return 2

    try:
        st = build_grant(root, a.spec, plan_rel, answers, a.accepted_by)
    except GrantRefused as exc:
        print("REFUSED: %s" % exc)
        return 1
    except OSError as exc:
        print("REFUSED: cannot read --spec %s: %s" % (a.spec, exc))
        return 1

    import contract_check  # lazy: needs Python >= 3.10 (see the module docstring)
    try:
        path = contract_check.write_envelope(root, st)
    except (OSError, ValueError) as exc:
        print("REFUSED: cannot write the envelope: %s" % exc)
        return 1
    print("GRANT: %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
