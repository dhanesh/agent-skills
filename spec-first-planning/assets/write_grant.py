#!/usr/bin/env python3
"""write_grant.py — build an autonomy-grant/v1 envelope after the user says yes.

Called only after the user has explicitly accepted the grant summary spec-first-planning
showed them (design spec §4, "Unattended mode, opt-in"). It refuses (never writes an
envelope) unless the spec is decision-closed under `spec_lint.py --unattended`, `--plan`
names a fresh task-plan/v1 envelope for exactly that spec, the answers are well shaped, and
the resulting statement passes the reference checker's structural and grant-specific checks
— so a malformed, stale or over-broad grant is a bug caught here, not something a consumer
has to notice later.

Usage:
    python3 write_grant.py --root <repo-root> --spec <spec path, relative to --root>
                           --plan <task-plan envelope path> --answers <answers.json>
                           --accepted-by <human name>

--spec and --plan may be given relative to --root or as absolute paths; either way, the
path (after following symlinks) must resolve inside --root, or the run is refused — a
symlink pointing out of the root does not count as "inside".

answers.json keys (only these 7 are recognised; any other key is refused):
    branch_pattern (str, required)   -- a glob, e.g. "factory/*"
    gate_policy    (dict, required)  -- action class -> "auto" | "grant" | "ask"
    expires_at     (str, required)   -- RFC 3339 UTC, in the future, at most 7 days after
                                        generatedAtTime
    budget         (dict, optional)  -- default {}
    stop_on        (list of str, optional)   -- default []
    defaults       (list of dict, optional)  -- default []
    system_one     (dict, optional)  -- {"allowed": bool, ...}; default {"allowed": false}

Exit 0: prints "GRANT: <path>".
Exit 1: refused; prints "REFUSED: <reason>" (a GrantRefused: the spec, the plan, the
    answers, or the resulting statement failed a check).
Exit 2: usage error (an unreadable or malformed --answers file, or one missing a required
    key; a bad CLI invocation).

Stdlib only. contract_check (vendored, same dir) needs Python >= 3.10 and is imported
lazily, mirroring spec_to_tasks.py's style, so importing this module doesn't force that
floor on callers that only want GrantRefused or the constants.
"""

import argparse
import json
import os
import sys
import unicodedata
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec_lint  # noqa: E402  (shared parser lives beside this script)
import spec_to_tasks  # noqa: E402

USAGE = ("usage: write_grant.py --root DIR --spec REL_SPEC --plan PLAN_ENVELOPE_PATH "
        "--answers ANSWERS.json --accepted-by NAME")

KNOWN_ANSWER_KEYS = frozenset({
    "branch_pattern", "gate_policy", "expires_at", "budget", "stop_on", "defaults", "system_one",
})


class GrantRefused(Exception):
    """The grant was not written: the spec, the plan, the answers, or the resulting
    statement failed a check. Its message is the single reason to show the user."""


def _norm_rel(p):
    return os.path.normpath(p).replace(os.sep, "/") if isinstance(p, str) else p


def _parse_rfc3339(contract_check, s):
    if not (isinstance(s, str) and contract_check.TIME_RE.match(s)):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _check_unknown_keys(answers):
    unknown = sorted(k for k in answers if k not in KNOWN_ANSWER_KEYS)
    if unknown:
        key = unknown[0]
        note = " (A8: grants are never signed)" if key == "require_signature" else ""
        raise GrantRefused("unknown answer key %r%s" % (key, note))


def _check_answer_shapes(answers):
    budget = answers.get("budget", {})
    if not isinstance(budget, dict):
        raise GrantRefused("answers.budget must be an object")
    stop_on = answers.get("stop_on", [])
    if not (isinstance(stop_on, list) and all(isinstance(s, str) for s in stop_on)):
        raise GrantRefused("answers.stop_on must be a list of strings")
    defaults = answers.get("defaults", [])
    if not (isinstance(defaults, list) and all(isinstance(d, dict) for d in defaults)):
        raise GrantRefused("answers.defaults must be a list of objects")
    system_one = answers.get("system_one", {"allowed": False})
    if not (isinstance(system_one, dict) and isinstance(system_one.get("allowed"), bool)):
        raise GrantRefused("answers.system_one must be an object with a boolean 'allowed'")


def _check_accepted_by(accepted_by):
    if not (isinstance(accepted_by, str) and accepted_by.strip()):
        raise GrantRefused("--accepted-by must name the human who said yes")
    if any(unicodedata.category(c)[0] == "C" for c in accepted_by):
        raise GrantRefused("--accepted-by must not contain control characters")


def _validate_plan(contract_check, root, spec_rel, spec_path, plan_rel):
    """--plan must be a fresh task-plan/v1 envelope pinning exactly this spec (design spec
    §1: the grant's subjects are the spec and "the task-plan envelope it was approved
    for"). Each check's failure message says which of those five things is wrong."""
    plan_path = os.path.join(root, *plan_rel.split("/"))
    plan_st, errs = contract_check.load_envelope(plan_path)
    if errs:
        raise GrantRefused("--plan %s: %s" % (plan_rel, errs[0][1]))
    plan_viol = contract_check.check_statement(plan_st)
    if plan_viol:
        raise GrantRefused("--plan %s fails the reference checker: C%d: %s"
                           % ((plan_rel,) + plan_viol[0]))
    if plan_st.get("predicateType") != spec_to_tasks.TASK_PLAN_KIND:
        raise GrantRefused("--plan %s is not a task-plan/v1 envelope (predicateType %r)"
                           % (plan_rel, plan_st.get("predicateType")))
    plan_spec = _norm_rel(plan_st["predicate"]["payload"].get("spec"))
    if plan_spec != _norm_rel(spec_rel):
        raise GrantRefused("--plan %s was derived from %r, not %r"
                           % (plan_rel, plan_spec, spec_rel))
    spec_subject = next((s for s in plan_st.get("subject", [])
                         if _norm_rel(s.get("name")) == _norm_rel(spec_rel)), None)
    if spec_subject is None:
        raise GrantRefused("--plan %s does not pin the spec %s" % (plan_rel, spec_rel))
    if (spec_subject.get("digest") or {}).get("sha256") != contract_check.sha256_file(spec_path):
        raise GrantRefused("--plan %s is stale: its digest for %s does not match the file "
                           "on disk" % (plan_rel, spec_rel))
    stale = contract_check.stale_names(root, plan_st.get("subject"))
    if stale:
        raise GrantRefused("--plan %s is stale: %s changed since the plan was written"
                           % (plan_rel, ", ".join(stale)))


def build_grant(root, spec_rel, plan_rel, answers, accepted_by, now=None):
    """Build (but do not write) the autonomy-grant/v1 statement.

    Raises GrantRefused when: an answers key is unrecognised or malformed, --accepted-by
    is blank or carries a control character, expires_at is not a valid RFC 3339 timestamp
    in the future, the spec cannot be read or is not decision-closed, --plan is not a
    fresh task-plan/v1 envelope for exactly this spec, or the resulting statement fails
    contract_check's structural (C3-C6) or grant-specific (C10) checks — including the
    7-day lifetime cap, which grant_violations enforces.
    """
    import contract_check  # lazy: needs Python >= 3.10 (see the module docstring)

    _check_unknown_keys(answers)
    _check_answer_shapes(answers)
    _check_accepted_by(accepted_by)

    now = now or contract_check.utc_now()
    expires = _parse_rfc3339(contract_check, answers.get("expires_at"))
    if expires is None:
        raise GrantRefused("expires_at must be RFC 3339 UTC (YYYY-MM-DDThh:mm:ssZ)")
    if expires <= now:
        raise GrantRefused("expires_at must be in the future")

    spec_path = os.path.join(root, *spec_rel.split("/"))
    try:
        with open(spec_path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise GrantRefused("cannot read --spec %s: %s" % (spec_rel, exc))
    issues = spec_lint.lint(text, mode="unattended")
    if issues:
        raise GrantRefused("spec is not decision-closed: " + issues[0])

    _validate_plan(contract_check, root, spec_rel, spec_path, plan_rel)

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
                                        spec_to_tasks.SKILL_VERSION, root, [spec_rel, plan_rel],
                                        payload, [accepted], now=now)
    viol = ["C%d: %s" % v for v in contract_check.check_statement(st)] \
        or contract_check.grant_violations(st)
    if viol:
        raise GrantRefused(viol[0])
    return st


def _contained_rel(root, root_real, given):
    """Root-relative, forward-slash path for `given` (absolute, or relative to root), or
    None when it resolves — after following symlinks — outside root. Used for both --spec
    and --plan so a symlink out of the root, a `..` escape, or an absolute path elsewhere
    is refused rather than silently pinned."""
    abs_path = given if os.path.isabs(given) else os.path.join(root, given)
    real = os.path.realpath(abs_path)
    try:
        rel = os.path.relpath(real, root_real)
    except ValueError:
        return None  # e.g. a different drive on Windows
    if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        return None
    return rel.replace(os.sep, "/")


def build_parser():
    ap = argparse.ArgumentParser(prog="write_grant.py",
                                 description="build the autonomy-grant/v1 envelope after the "
                                             "user says yes")
    ap.add_argument("--root", required=True, help="repo root")
    ap.add_argument("--spec", required=True,
                    help="the spec path, relative to --root (or absolute) — must resolve "
                         "inside --root")
    ap.add_argument("--plan", required=True,
                    help="the task-plan envelope path, relative to --root (or absolute) — "
                         "must resolve inside --root")
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
    root_real = os.path.realpath(root)

    spec_rel = _contained_rel(root, root_real, a.spec)
    if spec_rel is None:
        print("REFUSED: --spec %s is outside --root %s" % (a.spec, root))
        return 1
    plan_rel = _contained_rel(root, root_real, a.plan)
    if plan_rel is None:
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
        st = build_grant(root, spec_rel, plan_rel, answers, a.accepted_by)
    except GrantRefused as exc:
        print("REFUSED: %s" % exc)
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
