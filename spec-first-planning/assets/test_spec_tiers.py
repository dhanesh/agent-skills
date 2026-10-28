#!/usr/bin/env python3
"""Tests for the [tier: ...] and [contract: ...] requirement hints (proof-tiered
verification, R6) — stdlib-only, offline, deterministic.

Drives the real CLIs (spec_lint.py, spec_to_tasks.py --json) via subprocess.

Run standalone:  python3 spec-first-planning/assets/test_spec_tiers.py
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
SPEC_LINT = os.path.join(_HERE, "spec_lint.py")
SPEC_TO_TASKS = os.path.join(_HERE, "spec_to_tasks.py")
SCHEMA = os.path.join(_HERE, "schemas", "task-plan.v1.json")

_HEAD = textwrap.dedent(
    """\
    # Spec: Notes

    ## Problem
    Users lose notes they type because nothing saves them.

    ## Users
    - People who take notes

    ## Goals
    - Notes persist across reloads

    ## Non-goals
    - Sharing notes

    ## Constraints
    - B1 [invariant]: A saved note is never lost.

    ## Required truths
    - RT1 [SPECIFICATION_READY]: Save and list both work. (parent: OUTCOME; maps_to: B1; reqs: R1, R2; confidence: 0.8; check: {python} tests/check_save.py)

    """
)

_TAIL = textwrap.dedent(
    """\

    ## Acceptance criteria
    - R1: `{python} tests/check_save.py` exits 0. [cmd: {python} tests/check_save.py]
    - R2: `{python} tests/check_list.py` exits 0. [cmd: {python} tests/check_list.py]

    ## Verification
    Verify skill: .claude/skills/verify-notes

    ## Open questions
    - (none)
    """
)


def spec(r1, r2):
    return (_HEAD + "## Requirements\n- R1: %s\n- R2: %s\n" % (r1, r2) + _TAIL)


R1 = "The app must save a note when the user presses Save."
R2 = "The note list must show every saved note, newest first."
R1_PROOF = R1 + " [feature: notes-create] [proof: after Save, a reload shows the note] [parallel-safe]"
R2_PROOF = (R2 + " [feature: notes-list] [proof: two saved notes show newest first]"
            " [where: app/notes/] [after: R1]")
TIERED = R1_PROOF + " [tier: property] [contract: notes-create:C1, notes-create:C2]"

# Spec fixtures with neither hint, and the exact `spec_to_tasks.py --json` output the
# code produced for them before [tier:]/[contract:] existed (constraint O1: a spec
# without the new hints compiles to a byte-identical plan).
PLAIN_SPEC = spec(R1, R2)
PROOF_SPEC = spec(R1_PROOF, R2_PROOF)
PLAIN_GOLDEN = """\
{
  "tasks": [
    {
      "id": "T1",
      "requirement_ids": [
        "R1"
      ],
      "title": "The app must save a note when the user presses Save",
      "verify": "`{python} tests/check_save.py` exits 0.",
      "verify_commands": [
        [
          "{python}",
          "tests/check_save.py"
        ]
      ]
    },
    {
      "id": "T2",
      "requirement_ids": [
        "R2"
      ],
      "title": "The note list must show every saved note, newest first",
      "verify": "`{python} tests/check_list.py` exits 0.",
      "verify_commands": [
        [
          "{python}",
          "tests/check_list.py"
        ]
      ]
    }
  ],
  "coverage": {
    "R1": [
      "T1"
    ],
    "R2": [
      "T2"
    ]
  },
  "uncovered": []
}
"""
PROOF_GOLDEN = """\
{
  "tasks": [
    {
      "id": "T1",
      "requirement_ids": [
        "R1"
      ],
      "title": "The app must save a note when the user presses Save",
      "verify": "`{python} tests/check_save.py` exits 0.",
      "verify_commands": [
        [
          "{python}",
          "tests/check_save.py"
        ]
      ],
      "features": [
        "notes-create"
      ],
      "predicate": "after Save, a reload shows the note",
      "independence": "parallel-safe"
    },
    {
      "id": "T2",
      "requirement_ids": [
        "R2"
      ],
      "title": "The note list must show every saved note, newest first",
      "verify": "`{python} tests/check_list.py` exits 0.",
      "where": "app/notes/",
      "depends_on": [
        "T1"
      ],
      "verify_commands": [
        [
          "{python}",
          "tests/check_list.py"
        ]
      ],
      "features": [
        "notes-list"
      ],
      "predicate": "two saved notes show newest first",
      "independence": [
        "T1"
      ]
    }
  ],
  "coverage": {
    "R1": [
      "T1"
    ],
    "R2": [
      "T2"
    ]
  },
  "uncovered": []
}
"""


def _run(script, text, *args):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "spec.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        proc = subprocess.run([sys.executable, script, path] + list(args),
                              capture_output=True, text=True)
    return proc.returncode, proc.stdout


def lint(text):
    return _run(SPEC_LINT, text)


def plan(text):
    return _run(SPEC_TO_TASKS, text, "--json")


class TierHintTests(unittest.TestCase):
    def test_tiered_spec_lints_clean(self):
        rc, out = lint(spec(TIERED, R2))
        self.assertEqual(rc, 0, out)
        self.assertIn("LINT_RESULT: PASS", out)

    def test_tiered_plan_carries_min_tier_and_clauses(self):
        rc, out = plan(spec(TIERED, R2))
        self.assertEqual(rc, 0, out)
        t1, t2 = json.loads(out)["tasks"]
        self.assertEqual(t1["min_tier"], "property")
        self.assertEqual(t1["clauses"], ["notes-create:C1", "notes-create:C2"])
        self.assertEqual(t1["title"], "The app must save a note when the user presses Save")
        self.assertNotIn("tier", t1["title"])
        self.assertNotIn("contract", t1["title"])
        # A task whose requirement has no hint carries neither key.
        self.assertNotIn("min_tier", t2)
        self.assertNotIn("clauses", t2)

    def test_tiered_plan_differs_only_by_the_new_keys(self):
        _, hinted = plan(spec(TIERED, R2))
        _, base = plan(spec(R1_PROOF, R2))
        hinted, base = json.loads(hinted), json.loads(base)
        for key in ("min_tier", "clauses"):
            del hinted["tasks"][0][key]
        self.assertEqual(hinted, base)

    def test_tier_alone_lints_clean_and_emits_only_min_tier(self):
        rc, out = lint(spec(R1 + " [tier: runtime]", R2))
        self.assertEqual(rc, 0, out)
        t1 = json.loads(plan(spec(R1 + " [tier: runtime]", R2))[1])["tasks"][0]
        self.assertEqual(t1["min_tier"], "runtime")
        self.assertNotIn("clauses", t1)

    def test_every_known_tier_lints_clean(self):
        for tier in ("proved", "bounded", "property", "runtime"):
            rc, out = lint(spec(R1 + " [tier: %s] [contract: notes-create:C1]" % tier, R2))
            self.assertEqual(rc, 0, "%s: %s" % (tier, out))

    def test_unknown_tier_fails_lint(self):
        rc, out = lint(spec(R1 + " [tier: fast] [contract: notes-create:C1]", R2))
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL: R1 [tier: ...] has unknown tier 'fast'; use one of "
                      "proved, bounded, property, runtime", out)
        # The hint's word is not counted as a vague term in the statement.
        self.assertNotIn("vague", out)

    def test_contract_without_tier_fails_lint(self):
        rc, out = lint(spec(R1 + " [contract: notes-create:C1, notes-create:C2]", R2))
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL: R1 has a [contract: ...] hint but no [tier: ...] hint", out)

    def test_malformed_contract_entry_fails_lint(self):
        for bad in ("Notes:C1", "notes-create", "notes-create:c1", "notes-create:C",
                    "notes-create:C1x"):
            rc, out = lint(spec(R1 + " [tier: property] [contract: %s]" % bad, R2))
            self.assertEqual(rc, 1, "%s: %s" % (bad, out))
            self.assertIn("R1 [contract: ...] has a malformed entry '%s'" % bad, out)

    def test_schema_allows_the_new_keys(self):
        with open(SCHEMA, encoding="utf-8") as f:
            props = json.load(f)["properties"]["tasks"]["items"]["properties"]
        self.assertEqual(props["min_tier"]["enum"], ["proved", "bounded", "property", "runtime"])
        self.assertEqual(props["clauses"]["type"], "array")
        self.assertEqual(props["clauses"]["items"]["type"], "string")


class NoHintPlanUnchangedTests(unittest.TestCase):
    """O1: a spec with neither hint compiles to the plan it compiled to before."""

    def test_plain_spec_plan_is_byte_identical(self):
        rc, out = plan(PLAIN_SPEC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out, PLAIN_GOLDEN)

    def test_spec_with_other_hints_plan_is_byte_identical(self):
        rc, out = plan(PROOF_SPEC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out, PROOF_GOLDEN)

    def test_fixtures_lint_clean(self):
        for text in (PLAIN_SPEC, PROOF_SPEC):
            rc, out = lint(text)
            self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
