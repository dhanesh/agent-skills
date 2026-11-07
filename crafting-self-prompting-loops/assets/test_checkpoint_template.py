#!/usr/bin/env python3
"""Approve-only gate check for the human-checkpointed template (stdlib only).

Run: python3 test_checkpoint_template.py

The gate in templates/human-checkpointed.template.md must be closed by
default: after `decision = await_human(...)`, only an explicit APPROVED
decision may reach `act()`. Every other value (TIMEOUT, BLOCKED, PENDING,
an error, an unknown value) must stop the loop or revise the plan.

`gate_status()` holds the check. It reads the lines from the first
`await_human(` line to the first later `act(` line, and calls the gate
"closed" only when one of those lines tests `decision` against APPROVED
(or APPROVE) as a negative test: `!=`, `is not`, or `not decision ==`.
The tests run it on the shipped template (must pass) and on a fail-open
fixture (must fail), so the check itself is proven able to fail.
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "templates", "human-checkpointed.template.md")

CLOSED = "closed"
FAIL_OPEN = "fail-open"
NO_GATE = "no-gate-block"

_ACT = re.compile(r"\bact\(")
_NEGATIVE_APPROVE = re.compile(
    r"decision\s*(?:!=|is\s+not)\s*APPROVED?\b"
    r"|not\s*\(?\s*decision\s*==\s*APPROVED?\b"
)


def gate_block(text):
    """Return the lines from the first await_human( to the first later act(."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if "await_human(" in l), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if _ACT.search(lines[i])), None)
    if end is None:
        return None
    return lines[start:end]


def gate_status(text):
    """Classify a loop's human gate as closed, fail-open or no-gate-block."""
    block = gate_block(text)
    if block is None:
        return NO_GATE
    code = [l.split("#", 1)[0] for l in block]  # a comment is not a test
    return CLOSED if any(_NEGATIVE_APPROVE.search(l) for l in code) else FAIL_OPEN


# The gate as it shipped before the fix: only TIMEOUT and BLOCK stop, so
# PENDING or any other value falls through to act().
FAIL_OPEN_FIXTURE = """\
        decision = await_human(plan, GATE_EVIDENCE, progress_summary)
        if decision == TIMEOUT: return stop(SAFE_STATE)
        if decision == BLOCK:   state = revise_or_stop(state); continue
    result = act(plan.action)
"""

# The same gate with the claim only in a comment: still fail-open.
COMMENT_ONLY_FIXTURE = """\
        decision = await_human(plan, GATE_EVIDENCE, progress_summary)
        # if decision != APPROVED: revise
        if decision == TIMEOUT: return stop(SAFE_STATE)
    result = act(plan.action)
"""

CLOSED_FIXTURE = """\
        decision = await_human(plan, GATE_EVIDENCE, progress_summary)
        if decision == TIMEOUT: return stop(SAFE_STATE)
        if decision != APPROVED: state = revise_or_stop(state); continue
    result = act(plan.action)
"""


def read_template():
    with open(TEMPLATE, encoding="utf-8") as f:
        return f.read()


class GateCheckItself(unittest.TestCase):
    """The check tells a fail-open gate from a closed one."""

    def test_fail_open_fixture_fails(self):
        self.assertEqual(gate_status(FAIL_OPEN_FIXTURE), FAIL_OPEN)

    def test_comment_only_fixture_fails(self):
        self.assertEqual(gate_status(COMMENT_ONLY_FIXTURE), FAIL_OPEN)

    def test_closed_fixture_passes(self):
        self.assertEqual(gate_status(CLOSED_FIXTURE), CLOSED)

    def test_other_negative_forms_pass(self):
        for line in ("if decision is not APPROVED: return stop(SAFE_STATE)",
                     "if not decision == APPROVE: continue",
                     "if not (decision == APPROVED): continue"):
            text = "d = await_human(p)\n" + line + "\nact(x)\n"
            self.assertEqual(gate_status(text), CLOSED, line)

    def test_no_gate_block(self):
        self.assertEqual(gate_status("result = act(plan.action)\n"), NO_GATE)
        self.assertEqual(gate_status("decision = await_human(plan)\n"), NO_GATE)


class ShippedTemplate(unittest.TestCase):
    """The shipped template's gate is approve-only and still stops on timeout."""

    @classmethod
    def setUpClass(cls):
        cls.text = read_template()
        cls.block = gate_block(cls.text)

    def test_gate_is_closed(self):
        self.assertEqual(gate_status(self.text), CLOSED)

    def test_timeout_still_stops_in_safe_state(self):
        self.assertIsNotNone(self.block)
        self.assertTrue(
            any(re.search(r"decision\s*==\s*TIMEOUT.*stop\(SAFE_STATE\)", l) for l in self.block),
            "a timeout must still reach stop(SAFE_STATE) (LSC-3)")

    def test_no_positive_branch_other_than_timeout(self):
        # A `decision == X` branch for any X but TIMEOUT would be the old
        # deny-list shape; the gate uses one negative test instead.
        for l in self.block:
            code = l.split("#", 1)[0]
            for m in re.finditer(r"decision\s*==\s*(\w+)", code):
                self.assertEqual(m.group(1), "TIMEOUT", l)

    def test_one_approval_name(self):
        # The schema vocabulary and the gate code use the same word.
        self.assertIn("APPROVED|BLOCKED|PENDING", self.text)
        self.assertNotRegex(self.text, r"\bAPPROVE\b")
        self.assertNotRegex(self.text, r"decision\s*==\s*BLOCK\b")


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2).result
    sys.exit(0 if result.wasSuccessful() else 1)
