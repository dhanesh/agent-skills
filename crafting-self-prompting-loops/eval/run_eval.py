#!/usr/bin/env python3
"""Gate-runnable outcome eval for crafting-self-prompting-loops (docs/eval-standard.md).

Prompt-only skill: the deliverable contract is its copy-and-adapt loop-spec
templates. The eval replays the documented PARAMETERS.md examples through the
repo's own dry-run gate (no residual {{TOKENS}}), then grades the replayed
loop-spec artifacts for the safety properties every family template must carry:

  1. the hard-stop backstop (LSC-3): harness-level BACKSTOP_* caps outside the
     model's reach, marked "BACKSTOP IS MANDATORY", safe state = stopped;
  2. the trusted/untrusted two-channel boundary (LSC-7): a TRUSTED CONTROL
     CHANNEL header plus carried content wrapped in <data>...</data>;
  3. the typed loop boundary (LSC-4): STATE_SCHEMA declares the shape of the
     state that COMPOUNDS and STATE_VALIDATION checks it before it becomes the
     next round's premise — and the check must appear in the loop body, not
     only in the slot header.

Negative fixtures: a doctored copy whose template needs a param that was
removed from PARAMETERS.md must FAIL the replay; loop-spec fixtures with any
safety section stripped must be flagged; and a schema DECLARED but never
enforced must not pass as typed state.

Autonomy grant arm: against the vendored checker, no grant asks, a human grant
covers local_reversible and a skill-attributed one is INVALID; a grant is checked
per action class at the moment of the action (push_branch covered, an absent
open_pr asks), an irreversible class (merge) set to `grant` is INVALID (A8), and
SKILL.md wires check-grant into the intake and the LSC-8 principle.

Offline, deterministic, stdlib-only; all scratch under tempfile.mkdtemp().
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REPO = os.path.dirname(SKILL)
DRY_RUN = os.path.join(REPO, "scripts", "gates", "dry-run-replay.sh")

TEMPLATE_NAMES = [
    "autonomous.template.md",
    "base-loop.template.md",
    "human-checkpointed.template.md",
    "multi-agent.template.md",
    "self-refinement.template.md",
]

# Real markers taken from the shipped templates (assets/templates/*.md).
BACKSTOP_MARKERS = [
    "BACKSTOP IS MANDATORY",       # the do-not-remove banner (LSC-3)
    "BACKSTOP_MAX_ITERATIONS",
    "BACKSTOP_TOKEN_BUDGET",
    "BACKSTOP_WALL_CLOCK",
]
BOUNDARY_MARKERS = [
    "TRUSTED CONTROL CHANNEL",     # LSC-7 trusted channel header
    "<data>",                      # untrusted carried content wrapped as DATA
]
# LSC-4 typed loop boundary: the state that COMPOUNDS is declared and checked
# before it becomes the next round's premise (LSC-6 validates one observation at
# its point of use — not the same check).
STATE_TYPING_MARKERS = [
    "STATE_SCHEMA",                # the declared shape of carried state
    "STATE_VALIDATION",            # the boundary check + violation response
]

RESIDUAL_RE = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = f" ({detail})" if detail else ""
    print(f"CHECK: {name} — {'PASS' if ok else 'FAIL'}{suffix}")


def grade_backstop(text):
    """Model-free grader: does a loop-spec artifact carry the hard-stop backstop?"""
    missing = [m for m in BACKSTOP_MARKERS if m not in text]
    return (not missing, missing)


def grade_boundary(text):
    """Model-free grader: does it carry the trusted/untrusted two-channel boundary?"""
    missing = [m for m in BOUNDARY_MARKERS if m not in text]
    return (not missing, missing)


def grade_state_typing(text):
    """Model-free grader: is the carried state declared AND checked at the loop
    boundary? A schema slot with no enforcement is worse than none — it reads as
    a guarantee and delivers nothing — so both markers are required."""
    missing = [m for m in STATE_TYPING_MARKERS if m not in text]
    return (not missing, missing)


INTAKE_HEADING = "## Receiving a skill-contract envelope"
INTAKE_MARKERS = ("check-envelope", "--for", "UNVALIDATED", "LSC-1", "LSC-4", "LSC-7", "LSC-8")
TASK_PLAN = "https://github.com/dhanesh/agent-skills/skill-contract/task-plan/v1"


def grade_intake(text):
    """The intake section must validate first and map the plan onto the loop spec."""
    start = text.find(INTAKE_HEADING)
    if start < 0:
        return False, list(INTAKE_MARKERS)
    end = text.find("\n## ", start + len(INTAKE_HEADING))
    section = text[start:end if end > 0 else len(text)]
    missing = [m for m in INTAKE_MARKERS if m not in section]
    return not missing, missing


# ── Autonomy grant (skill-contract autonomy-grant/v1) ───────────────────────
# Fixtures build a grant at run time, so they MUST satisfy the checker's floors:
# at least 2 distinct subjects, a lifetime of at most 7 days (A7), no
# require_signature, and no gate other than `ask` on an irreversible class (A8).
# The temp dirs sit outside any git repo, so the default-branch floor skips.
GRANT_KIND = "https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1"
GRANT_ID = "autonomy-grant-v1-20260919T120000Z-a1b2c3"
CONTRACT_CHECKER = os.path.join(SKILL, "assets", "contract_check.py")


def _now_z():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _in_one_day():
    return (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _grant_fixture(root, asserted_by, gate_policy=None):
    """Write a spec, a plan and a grant pinning both (sha256) under `root`."""
    subjects = []
    for rel, content in (("docs/spec.md", "# Spec\n"),
                         ("docs/plan.json", '{"tasks": []}\n')):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        subjects.append({"name": rel,
                         "digest": {"sha256": hashlib.sha256(content.encode()).hexdigest()}})
    st = {"_type": "https://in-toto.io/Statement/v1",
          "subject": subjects,
          "predicateType": GRANT_KIND,
          "predicate": {"skillContract": "1", "id": GRANT_ID,
                        "wasAttributedTo": {"skill": "spec-first-planning", "version": "2.0.0"},
                        "generatedAtTime": _now_z(), "wasRevisionOf": None,
                        "payload": {"scope": {"repo": ".", "branch_pattern": "*"},
                                    "decisions": [{"id": "D1", "question": "q", "answer": "a",
                                                   "source": "s"}],
                                    "defaults": [],
                                    "gate_policy": gate_policy or {"local_reversible": "grant"},
                                    "budget": {}, "stop_on": [],
                                    "expires_at": _in_one_day(),
                                    "system_one": {"allowed": False}, "revoked": False},
                        "assertions": [{"test": "grant-accepted", "assertedBy": asserted_by,
                                        "result": {"outcome": "passed"},
                                        "command": ["{python}",
                                                    "{skill_dir:spec-first-planning}/assets/spec_lint.py",
                                                    "--unattended", "docs/spec.md"]}]}}
    d = os.path.join(root, ".skill-contract", "envelopes")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, GRANT_ID + ".json"), "w", encoding="utf-8") as f:
        json.dump(st, f)


def _git_repo(root):
    """Make root its own git repo on a non-default branch (main holds one empty commit,
    HEAD is on factory/x), so check-grant's branch probes find root's .git rather than
    any repo enclosing TMPDIR. Without git, root stays a plain directory, which
    check-grant treats as outside git."""
    if shutil.which("git") is None:
        return
    for args in (["init", "-q", "-b", "main"],
                 # no background auto-gc/maintenance racing the temp-dir cleanup
                 ["config", "gc.auto", "0"], ["config", "maintenance.auto", "false"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                  "commit", "-q", "--allow-empty", "-m", "x"],
                 ["checkout", "-q", "-b", "factory/x"]):
        subprocess.run(["git", "-C", root] + args, check=True, capture_output=True, timeout=60)


def _check_grant(root, action="local_reversible"):
    r = subprocess.run([sys.executable, "-I", CONTRACT_CHECKER, "check-grant", "--root", root,
                        "--action", action], capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout.strip()


def grant_checks(labels=("", "", "")):
    """The write gate's grant arm: NONE asks, a human grant covers, a skill one is INVALID."""
    with tempfile.TemporaryDirectory() as t:
        _git_repo(t)
        rc, out = _check_grant(t)
        check(labels[0] + "NEGATIVE: no grant -> check-grant exits 3 and the gate must ask",
              rc == 3 and "GRANT: NONE" in out, f"rc={rc} {out[-160:]}")
    with tempfile.TemporaryDirectory() as t:
        _git_repo(t)
        _grant_fixture(t, {"human": "Dana"})
        rc, out = _check_grant(t)
        check(labels[1] + "a human-accepted grant covers local_reversible and names its id",
              rc == 0 and "GRANT: COVERED" in out and GRANT_ID in out,
              f"rc={rc} {out[-160:]}")
    with tempfile.TemporaryDirectory() as t:
        _git_repo(t)
        _grant_fixture(t, {"skill": "spec-first-planning"})
        rc, out = _check_grant(t)
        check(labels[2] + "NEGATIVE: a skill-attributed grant is INVALID (exit 2)",
              rc == 2 and "GRANT: INVALID" in out, f"rc={rc} {out[-160:]}")


# The grantable set is a CLOSED list of class tokens: the model is not left to
# judge reversibility (a tag push, a release or a comment is not push_branch).
A8_CLOSED_LIST = ("Under LSC-8 only `push_branch` (pushing the current non-default branch) and "
                  "`open_pr` (opening or updating a pull request) are grantable; `merge`, "
                  "`deploy`, `spend`, `external_message` and `delete` always wait for the human")

# The LSC-8 principle and the intake must both wire the grant in; the principle
# must keep the irreversible classes out of any grant's reach (A8).
LSC8_MARKERS = ("MUST wait for explicit human approval",
                "check-grant --root <repo> --action <the action's class>",
                "at the moment of the action",
                "resolve `$SKILL_DIR` as in \"Receiving a skill-contract envelope\"",
                "write the absolute checker path into the loop's scaffold",
                "MUST name the grant id and action class",
                A8_CLOSED_LIST,
                "and so does any action you cannot place exactly in `push_branch` or `open_pr`",
                "never force-push")
INTAKE_GRANT_MARKERS = ("check-grant --root <repo-root> --action local_reversible",
                        "exits 0", "MAY", "MUST name the grant id",
                        "or a grant covered it")


def grade_grant_text(text):
    start = text.find("- **A human gate where it matters (LSC-8).**")
    end = text.find("\n", start)
    lsc8 = " ".join(text[start:end].split()) if start >= 0 else ""
    i0 = text.find(INTAKE_HEADING)
    i1 = text.find("\n## ", i0 + len(INTAKE_HEADING)) if i0 >= 0 else -1
    intake = " ".join(text[i0:i1].split()) if i0 >= 0 and i1 > i0 else ""
    missing = (["LSC-8:" + m for m in LSC8_MARKERS if m not in lsc8]
               + ["intake:" + m for m in INTAKE_GRANT_MARKERS if m not in intake])
    return not missing, missing


def copy_skill(dst):
    shutil.copytree(
        SKILL, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "eval"),
    )


def run_replay(skill_dir, scratch_dir):
    return subprocess.run(
        ["sh", DRY_RUN, skill_dir, scratch_dir],
        capture_output=True, text=True, timeout=60,
    )


def main():
    tmp = tempfile.mkdtemp(prefix="cspl-eval-")
    try:
        # ── Positive arm: replay the documented parameters into every template ──
        pristine = os.path.join(tmp, "pristine")
        scratch = os.path.join(tmp, "scratch")
        copy_skill(pristine)
        r = run_replay(pristine, scratch)
        check("dry-run replay of documented PARAMETERS.md passes",
              r.returncode == 0 and "DRY_RUN_RESULT: PASS" in r.stdout,
              f"rc={r.returncode}")

        artifacts = {}
        for name in TEMPLATE_NAMES:
            path = os.path.join(scratch, name)
            artifacts[name] = open(path).read() if os.path.isfile(path) else ""
        residual = sorted({
            tok for text in artifacts.values() for tok in RESIDUAL_RE.findall(text)
        })
        check("all 5 loop-spec artifacts replayed with no residual {{TOKENS}}",
              all(artifacts.values()) and not residual,
              f"artifacts={sum(1 for t in artifacts.values() if t)}/5, "
              f"residual={residual or 'none'}")

        bad_backstop = [n for n, t in artifacts.items() if not grade_backstop(t)[0]]
        check("every artifact carries the hard-stop backstop (LSC-3 markers)",
              artifacts and not bad_backstop,
              "5/5 templates" if not bad_backstop else f"missing in {bad_backstop}")

        bad_boundary = [n for n, t in artifacts.items() if not grade_boundary(t)[0]]
        check("every artifact carries the trusted/untrusted two-channel boundary (LSC-7 markers)",
              artifacts and not bad_boundary,
              "5/5 templates" if not bad_boundary else f"missing in {bad_boundary}")

        bad_typing = [n for n, t in artifacts.items() if not grade_state_typing(t)[0]]
        check("every artifact types the loop boundary (LSC-4 STATE_SCHEMA + STATE_VALIDATION)",
              artifacts and not bad_typing,
              "5/5 templates" if not bad_typing else f"missing in {bad_typing}")

        # The check must run where state COMPOUNDS, not merely be declared in the
        # slot header — so the loop body has to reference it too.
        no_enforcement = [n for n, t in artifacts.items()
                          if t.count("STATE_VALIDATION") < 2]
        check("the boundary check appears in the loop body, not just the slot header",
              artifacts and not no_enforcement,
              "5/5 templates" if not no_enforcement else f"declared-but-unenforced in {no_enforcement}")

        # ── Negative arm 1: required param removed → replay must FAIL ──────────
        # Doctored copy: the base-loop template now needs two substitution
        # params, but PARAMETERS.md documents only one — the other row has been
        # "removed". The replay gate must reject the residual token.
        doctored = os.path.join(tmp, "doctored")
        copy_skill(doctored)
        tpl = os.path.join(doctored, "assets", "templates", "base-loop.template.md")
        with open(tpl, "a") as f:
            f.write("\nGOAL_VALUE: {{LOOP_GOAL}}\nSTOP_TOKEN: {{STOP_SIGNAL_TOKEN}}\n")
        with open(os.path.join(doctored, "PARAMETERS.md"), "a") as f:
            f.write("\n| `{{LOOP_GOAL}}` | Loop goal | ship a green build | — |\n")
        r_bad = run_replay(doctored, os.path.join(tmp, "scratch-doctored"))
        check("negative: replay FAILS when a required param is removed from PARAMETERS.md",
              r_bad.returncode != 0
              and "DRY_RUN_RESULT: FAIL" in r_bad.stdout
              and "{{STOP_SIGNAL_TOKEN}}" in r_bad.stdout,
              f"rc={r_bad.returncode}")

        # ── Negative arm 2: safety sections stripped → grader must catch it ────
        base = artifacts.get("base-loop.template.md", "")
        no_backstop = "\n".join(
            ln for ln in base.splitlines() if "BACKSTOP" not in ln)
        ok, missing = grade_backstop(no_backstop)
        check("negative: grader flags a loop spec with the backstop stripped",
              base and not ok, f"missing markers: {missing}")

        no_boundary = "\n".join(
            ln for ln in base.splitlines()
            if "TRUSTED CONTROL CHANNEL" not in ln and "<data>" not in ln)
        ok, missing = grade_boundary(no_boundary)
        check("negative: grader flags a loop spec with the trusted/untrusted boundary stripped",
              base and not ok, f"missing markers: {missing}")

        no_typing = "\n".join(
            ln for ln in base.splitlines()
            if "STATE_SCHEMA" not in ln and "STATE_VALIDATION" not in ln)
        ok, missing = grade_state_typing(no_typing)
        check("negative: grader flags a loop spec with the typed state boundary stripped",
              base and not ok, f"missing markers: {missing}")

        # A schema DECLARED but never enforced must not pass as typed state.
        declared_only = "\n".join(
            ln for ln in base.splitlines() if "STATE_VALIDATION" not in ln)
        check("negative: a declared-but-unenforced schema is not counted as typed state",
              base and not grade_state_typing(declared_only)[0]
              and "STATE_SCHEMA" in declared_only)

        # ── skill-contract arm: receiving a task-plan envelope ───────────────
        # Static fixtures only (no sibling skill needed): the envelope is built
        # with this skill's own vendored checker, then checked --for this skill.
        sys.path.insert(0, os.path.join(pristine, "assets"))
        import contract_check as cc  # noqa: E402  (the skill's vendored checker)
        cc_py = os.path.join(pristine, "assets", "contract_check.py")
        repo = os.path.join(tmp, "repo")
        os.makedirs(os.path.join(repo, "docs"))
        spec = os.path.join(repo, "docs", "spec.md")
        with open(spec, "w", encoding="utf-8") as f:
            f.write("# Spec\n\n- R1: The export must include every row.\n")
        payload = {"title": "Export", "spec": "docs/spec.md", "coverage": {"R1": ["T1"]},
                   "uncovered": [],
                   "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "Export every row",
                              "verify": [{"text": "row count matches", "command": None}]}]}
        lint = cc.assertion("spec-lint", "spec-first-planning", "passed", repo, ["docs/spec.md"],
                            command=["{python}", "{skill_dir:spec-first-planning}/assets/spec_lint.py",
                                     "docs/spec.md"])
        good = cc.write_envelope(repo, cc.build_statement(
            TASK_PLAN, "spec-first-planning", "1.1.0", repo, ["docs/spec.md"], payload, [lint]))

        def receive(path):
            r = subprocess.run([sys.executable, cc_py, "check-envelope", path, "--root", repo,
                                "--for", pristine, "--json"],
                               capture_output=True, text=True, timeout=60)
            try:
                return r.returncode, json.loads(r.stdout.splitlines()[0])
            except (ValueError, IndexError):
                return r.returncode, None

        rc, rep = receive(good)
        check("a task-plan envelope is accepted for this skill (--for)",
              rc == 0 and rep is not None and rep["claims"] == {"spec-lint": "CLAIMED"},
              f"rc={rc}")

        with open(good, encoding="utf-8") as f:
            tampered = json.load(f)
        tampered["_type"] = "https://example.com/not-in-toto"
        bad_path = os.path.join(tmp, "tampered.json")
        with open(bad_path, "w", encoding="utf-8") as f:
            json.dump(tampered, f)
        rc, rep = receive(bad_path)
        check("negative: a tampered envelope is refused with commandment 3",
              rc == 2 and rep is not None and any(v.startswith("C3:") for v in rep["violations"]))

        v2 = cc.write_envelope(repo, cc.build_statement(
            TASK_PLAN[:-1] + "2", "spec-first-planning", "2.0.0", repo, ["docs/spec.md"], payload))
        rc, rep = receive(v2)
        check("negative: a task-plan/v2 envelope is refused (this skill consumes v1 only)",
              rc == 2 and rep is not None and any(v.startswith("C9:") for v in rep["violations"]))

        with open(spec, "a", encoding="utf-8") as f:
            f.write("- R2: The export must keep row order.\n")
        rc, rep = receive(good)
        check("negative: an envelope whose spec changed is surfaced as STALE",
              rc == 0 and rep is not None and rep["stale"] == ["docs/spec.md"])

        with open(os.path.join(pristine, "SKILL.md"), encoding="utf-8") as f:
            skill_text = f.read()
        ok, missing = grade_intake(skill_text)
        check("SKILL.md carries the intake section mapping the plan onto LSC-1/4/7/8",
              ok, f"missing: {missing}")
        stripped = "\n".join(ln for ln in skill_text.splitlines() if "LSC-7" not in ln)
        check("negative: grader flags an intake section with the LSC-7 mapping stripped",
              not grade_intake(stripped)[0])

        # ── Autonomy grant arm ──────────────────────────────────────────────
        grant_checks()
        with tempfile.TemporaryDirectory() as t:
            _git_repo(t)
            _grant_fixture(t, {"human": "Dana"},
                           {"local_reversible": "grant", "push_branch": "grant"})
            rc, out = _check_grant(t, "push_branch")
            check("a grant is checked per class at the action: push_branch covered",
                  rc == 0 and "class=push_branch" in out, f"rc={rc} {out[-160:]}")
            rc, out = _check_grant(t, "open_pr")
            check("NEGATIVE: a class the grant leaves out (open_pr) asks (exit 3)",
                  rc == 3 and "GRANT: ASK" in out, f"rc={rc} {out[-160:]}")
            # The checker is the gate for EVERY envelope it is handed, so the
            # grant must also pass check-envelope --for this skill (consumes).
            r = subprocess.run([sys.executable, "-I", CONTRACT_CHECKER, "check-envelope",
                                os.path.join(t, ".skill-contract", "envelopes", GRANT_ID + ".json"),
                                "--root", t, "--for", SKILL],
                               capture_output=True, text=True, timeout=60)
            check("an autonomy-grant envelope is accepted for this skill (--for)",
                  r.returncode == 0, (r.stdout + r.stderr).strip()[-200:])
        with tempfile.TemporaryDirectory() as t:
            _git_repo(t)
            _grant_fixture(t, {"human": "Dana"},
                           {"local_reversible": "grant", "merge": "grant"})
            rc, out = _check_grant(t, "merge")
            check("NEGATIVE: a grant setting merge to `grant` is INVALID (A8)",
                  rc == 2 and "GRANT: INVALID" in out, f"rc={rc} {out[-160:]}")
        ok, missing = grade_grant_text(skill_text)
        check("SKILL.md wires check-grant into the intake and the LSC-8 principle",
              ok, f"missing: {missing}")
        check("negative: grader flags an LSC-8 principle with the A8 closed list stripped",
              not grade_grant_text(skill_text.replace(A8_CLOSED_LIST, "some actions"))[0])
        check("negative: grader flags an LSC-8 principle with the catch-all stripped",
              not grade_grant_text(skill_text.replace(
                  "and so does any action you cannot place exactly", "and so do some"))[0])
        check("negative: grader flags an LSC-8 principle with the force-push ban stripped",
              not grade_grant_text(skill_text.replace("never force-push", "push"))[0])
        compat = re.search(r"^compatibility:(.*)$", skill_text, re.M)
        check("compatibility says python validates handoffs AND checks grants",
              compat is not None and "check grants" in compat.group(1)
              and "validate skill-contract handoffs" in compat.group(1),
              compat.group(1).strip()[-120:] if compat else "no compatibility line")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print(f"EVAL_RESULT: {'PASS' if ok else 'FAIL'} ({k}/{n} checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
