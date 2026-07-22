#!/usr/bin/env python3
"""Gate-runnable outcome eval for tmux-agent-herdr-lite (docs/eval-standard.md).

Harness: authors realistic captured-pane fixtures (the text tmux
`capture-pane` would return) for Claude Code and Codex sessions in blocked
(permission prompt, question waiting), working (tool running / streaming
title), and finished states — no tmux binary involved. Skill tooling: the
shipped scripts/agent_classify.py classifier, called directly with the same
signature agent-status-scan uses. Grader: exact-state assertions against the
classifier's documented precedence and manifest rules.

Negative fixtures: garbled/ambiguous pane text must land on the documented
conservative fallback (quiescent past threshold -> idle, never blocked; the
strict-blocked rule for known agents means an agent merely *logging*
'permission denied' is not mislabeled blocked), and an empty capture must
not crash.

Offline, deterministic, stdlib-only; writes nothing (bytecode disabled).
"""
import os
import sys

sys.dont_write_bytecode = True  # importing from scripts/ must not write to the repo
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import agent_classify  # noqa: E402
from agent_classify import STATES, classify, classify_explain, infer_agent  # noqa: E402

IDLE_S = 120  # idle threshold used by agent-status-scan's default

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = f" ({detail})" if detail else ""
    print(f"CHECK: {name} — {'PASS' if ok else 'FAIL'}{suffix}")


# ── fixture pane captures ────────────────────────────────────────────────────

CLAUDE_PERMISSION = """\
● Bash(rm -rf node_modules && npm install)

Do you want to proceed?
❯ 1. Yes
  2. Yes, and don't ask again for this command
  3. No, and tell Claude what to do differently (esc)
"""

CLAUDE_QUESTION = """\
● I found two config styles in the repo.

Would you like to standardize on TOML for all services?

❯ 1. Yes, migrate everything to TOML
  2. No, keep YAML where it already exists
"""

CLAUDE_WORKING = """\
✻ Crafting the parser…

● Bash(python3 -m pytest -q)
  ⎿  Running…

  esc to interrupt · ctrl+b to run in background
"""

CLAUDE_FINISHED = """\
● The refactor is complete and every suite passes.

╭──────────────────────────────────────────────╮
│ ❯                                            │
╰──────────────────────────────────────────────╯
  ? for shortcuts
"""

CODEX_APPROVAL = """\
› Run command?

    $ git push origin main

  Press enter to confirm or esc to cancel
"""

CODEX_WORKING = """\
• Exploring the repository structure

  reading src/lib/router.ts

  esc to interrupt
"""

CLAUDE_LOGGED_DENIAL = """\
● Bash(cat /var/log/audit.log)
  ⎿  tail: cannot open '/var/log/audit.log': permission denied
     (noted the error in the report and moved on)
"""

GARBLED = """\
x7#qz ~~ blorp 42 fnord
@@@@ ]];; lorem 0x1f
zzzz kqx
"""


def main():
    # -- blocked states -----------------------------------------------------
    st, why = classify_explain(CLAUDE_PERMISSION, changed=False, idle_elapsed=3,
                               idle_s=IDLE_S, prev_status="working", agent="claude")
    check("claude permission prompt -> blocked", st == "blocked", f"{st}: {why}")

    st, why = classify_explain(CLAUDE_QUESTION, changed=False, idle_elapsed=3,
                               idle_s=IDLE_S, prev_status="working", agent="claude")
    check("claude question waiting for user -> blocked", st == "blocked", f"{st}: {why}")

    st, why = classify_explain(CODEX_APPROVAL, changed=False, idle_elapsed=3,
                               idle_s=IDLE_S, prev_status="working", agent="codex")
    check("codex approval prompt -> blocked", st == "blocked", f"{st}: {why}")

    # -- working states -----------------------------------------------------
    st, why = classify_explain(CLAUDE_WORKING, changed=True, idle_elapsed=0,
                               idle_s=IDLE_S, prev_status="working", agent="claude")
    check("claude tool running (esc to interrupt) -> working", st == "working",
          f"{st}: {why}")

    st, why = classify_explain("streaming tokens", changed=True, idle_elapsed=0,
                               idle_s=IDLE_S, prev_status="working", agent="claude",
                               title="⠧ Simmering… (esc to interrupt)")
    check("claude braille spinner title -> working", st == "working", f"{st}: {why}")

    st, why = classify_explain(CODEX_WORKING, changed=True, idle_elapsed=0,
                               idle_s=IDLE_S, prev_status="working", agent="codex")
    check("codex tool running -> working", st == "working", f"{st}: {why}")

    # -- done vs idle (Herdr's finished-and-unviewed mapping) ---------------
    st, why = classify_explain(CLAUDE_FINISHED, changed=False, idle_elapsed=10,
                               idle_s=IDLE_S, prev_status="working", agent="claude")
    check("claude idle chrome after working -> done", st == "done", f"{st}: {why}")

    st, why = classify_explain(CLAUDE_FINISHED, changed=False, idle_elapsed=10,
                               idle_s=IDLE_S, prev_status="idle", agent="claude")
    check("claude fresh prompt without prior work -> idle (not done)",
          st == "idle", f"{st}: {why}")

    # -- agent inference from launch commands -------------------------------
    check("infer_agent unwraps wrappers and resolves aliases",
          infer_agent("npx codex --model o3") == "codex"
          and infer_agent("/usr/local/bin/claude --continue") == "claude"
          and infer_agent("env FOO=1 tail -f build.log") == "",
          "npx codex / bin claude / non-agent")

    # -- negative fixtures --------------------------------------------------
    # Garbled text, unknown agent, quiescent past threshold: the documented
    # conservative fallback is idle — never blocked from noise.
    st, why = classify_explain(GARBLED, changed=False, idle_elapsed=600,
                               idle_s=IDLE_S, prev_status="working", agent="")
    check("garbled pane text quiescent past threshold -> idle fallback",
          st == "idle", f"{st}: {why}")

    # Strict-blocked rule: a known agent merely *logging* 'permission denied'
    # must not be classified blocked (loose generic words are skipped when a
    # manifest exists); quiescent past threshold it falls back to idle.
    st, why = classify_explain(CLAUDE_LOGGED_DENIAL, changed=False, idle_elapsed=600,
                               idle_s=IDLE_S, prev_status="working", agent="claude")
    check("claude discussing 'permission denied' -> idle, not blocked (strict-blocked)",
          st == "idle", f"{st}: {why}")

    # Empty capture: must not crash, must yield a valid state; within the
    # idle threshold the documented fallback is the previous status.
    try:
        st = classify("", changed=False, idle_elapsed=0, idle_s=IDLE_S,
                      prev_status="working", agent="claude")
        ok, detail = st in STATES and st == "working", f"got {st!r}"
    except Exception as exc:  # pragma: no cover - the failure being graded
        ok, detail = False, f"raised {type(exc).__name__}: {exc}"
    check("empty capture -> no crash, keeps previous status within threshold",
          ok, detail)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print(f"EVAL_RESULT: {'PASS' if ok else 'FAIL'} ({k}/{n} checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
