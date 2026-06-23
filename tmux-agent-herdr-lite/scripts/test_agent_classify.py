#!/usr/bin/env python3
"""Unit tests for agent_classify.classify (stdlib only; run: python3 test_agent_classify.py).

Each case maps to a base-in-reality 2026-06-22 audit finding the rewrite remediates.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent_classify import classify  # noqa: E402

IDLE = 120
CASES = [
    # name, text, changed, idle_elapsed, expected
    # Finding [34]: a stale 'done' / loose token scrolled out of the tail must not pin status
    # while output is actively streaming.
    ("stale 'done' in scrollback ignored while streaming",
     "make: Nothing to be done for 'all'\n" + "\n".join(f"compiling module {i}" for i in range(20)),
     True, 0, "working"),
    ("stale '429' in scrollback ignored while streaming",
     "HTTP 429 logged earlier\n" + "\n".join(f"processing batch {i}" for i in range(10)),
     True, 0, "working"),

    # Finding [35]: a real failure is its own 'error' state, checked before 'blocked',
    # and not masked by an incidental 'permission denied' / '429' substring.
    ("nonzero exit -> error (not blocked)",
     "AGENT_STATUS: done exit_code=1", False, 0, "error"),
    ("traceback -> error",
     "Traceback (most recent call last):\n  File \"x.py\", line 3\nValueError: boom", False, 1, "error"),
    ("error beats incidental 'permission denied'",
     "permission denied\ncommand failed", True, 0, "error"),

    # Regression guard for refuted finding [37]: unanchored regex matches multi-digit codes.
    ("exit_code=137 -> error", "AGENT_STATUS: done exit_code=137", False, 0, "error"),
    ("exit_code=255 -> error", "AGENT_STATUS: done exit_code=255", False, 0, "error"),
    ("exit_code=0 -> done", "AGENT_STATUS: done exit_code=0", False, 0, "done"),

    # Authoritative signals preempt activity; advisory ones only count when quiescent.
    ("interactive prompt -> blocked despite recent change",
     "Do you want to continue? [y/N]", True, 0, "blocked"),
    ("explicit done sentinel -> done", "AGENT_STATUS: done result=ok", False, 0, "done"),
    ("bare 'done' in tail when quiescent -> done", "build done", False, 5, "done"),
    ("'429' when quiescent -> blocked", "request failed: 429 too many requests", False, 5, "blocked"),

    # Activity and idle fallbacks.
    ("active output, no signal -> working",
     "\n".join(f"line {i}" for i in range(30)), True, 0, "working"),
    ("quiescent past threshold -> idle", "$ ", False, 300, "idle"),
    ("quiescent within threshold -> prev status", "$ ", False, 5, "working"),
]


def main():
    failed = 0
    for name, text, changed, idle_elapsed, want in CASES:
        got = classify(text, changed, idle_elapsed, IDLE)
        ok = got == want
        if not ok:
            failed += 1
        print(f"{'PASS' if ok else 'FAIL'}  {name}  -> {got}" + ("" if ok else f"  (want {want})"))
    total = len(CASES)
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
