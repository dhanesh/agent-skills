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

Offline, deterministic, stdlib-only; all scratch under tempfile.mkdtemp().
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print(f"EVAL_RESULT: {'PASS' if ok else 'FAIL'} ({k}/{n} checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
