#!/usr/bin/env python3
"""Unit tests for control_pack_lint.py — stdlib-only, offline, deterministic.

Run standalone:
    cd ai-migration-operating-model/assets && python3 test_control_pack_lint.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import control_pack_lint  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "control_pack_lint.py")

GOOD_PACK = {
    "RULEBOOK.md": textwrap.dedent(
        """\
        # Rulebook — payments-service Python -> TypeScript

        - MR1: Money amounts must map to the Decimal wrapper type, never the
          plain number type.
        - MR2: Date arithmetic must preserve the source system's UTC handling.
        - MR3: Repository methods must return typed result objects, never raw
          database rows.
        """
    ),
    "DEPENDENCY_MAP.md": textwrap.dedent(
        """\
        # Dependency map

        1. Utility libraries (no internal dependencies)
        2. Domain models
        3. Service layer
        4. API handlers
        """
    ),
    "GAP_INVENTORY.md": textwrap.dedent(
        """\
        # Gap inventory

        - G1: Is loan.amount stored in minor or major currency units?
        - G2: Nullable customer.email semantics — decision: keep optional,
          normalize empty string to null.
        """
    ),
    "PORTABILITY_TEST_PLAN.md": textwrap.dedent(
        """\
        # Portability test plan

        - S1: Repayment schedule for account 42 on 2024-01-31 — run both
          systems, diff via parity_check.sh.
        - S2: Fee calculation for a zero-balance account — outputs must match
          after normalization.
        """
    ),
    "parity_check.sh": textwrap.dedent(
        """\
        #!/bin/sh
        old_tool run "$1" > old.json
        new_tool run "$1" > new.json
        python3 parity_diff.py old.json new.json
        """
    ),
    "AGENT_WORK_QUEUE.md": textwrap.dedent(
        """\
        # Agent work queue

        - src/models/loan.py [migrated]
        - src/services/schedule.py [parity-fail]
        - src/api/handlers.py [pending]
        """
    ),
    "REVIEWER_PROMPTS.md": textwrap.dedent(
        """\
        # Reviewer prompts

        - Attack money precision: did any Decimal become a float?
        - Attack timezone drift: does date math still assume UTC?
        - Attack nullability: were optional fields silently defaulted?
        - Attack error handling and the idempotency of retries.
        """
    ),
    "PHASE_GATES.md": textwrap.dedent(
        """\
        # Phase gates

        1. Judge exists: parity_check.sh catches a deliberately broken case.
        2. Rulebook stress-tested on the pilot slice.
        3. Bulk migration compiles and unit suites are green.
        4. Behavioral parity green across all golden scenarios.
        5. Rollout and rollback plan approved.
        """
    ),
}


def write_pack(root, overrides=None, drop=()):
    files = dict(GOOD_PACK)
    if overrides:
        files.update(overrides)
    for name in drop:
        files.pop(name, None)
    os.makedirs(root, exist_ok=True)
    for name, content in files.items():
        with open(os.path.join(root, name), "w", encoding="utf-8") as f:
            f.write(content)
    return root


class LintCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cpl-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lint(self, overrides=None, drop=()):
        pack = write_pack(os.path.join(self.tmp, "pack"), overrides, drop)
        return control_pack_lint.lint_pack(pack)


class TestGoodPack(LintCase):
    def test_good_pack_is_clean(self):
        self.assertEqual(self.lint(), [])


class TestMissingArtifacts(LintCase):
    def test_each_required_file_is_enforced(self):
        for name in control_pack_lint.REQUIRED_FILES:
            pack = write_pack(
                os.path.join(self.tmp, "pack-" + name), drop=(name,)
            )
            issues = control_pack_lint.lint_pack(pack)
            self.assertIn("missing artifact: %s" % name, issues)

    def test_missing_parity_script(self):
        issues = self.lint(drop=("parity_check.sh",))
        self.assertTrue(
            any("parity script (parity_check.*)" in i for i in issues)
        )

    def test_empty_parity_script(self):
        issues = self.lint(overrides={"parity_check.sh": "   \n"})
        self.assertIn("parity script parity_check.sh is empty", issues)


class TestRulebook(LintCase):
    def test_no_rules(self):
        issues = self.lint(overrides={"RULEBOOK.md": "# Rulebook\n\nprose\n"})
        self.assertIn("RULEBOOK.md: no '- MR<n>:' rules found", issues)

    def test_non_sequential_ids(self):
        bad = "- MR1: All ids must be stable.\n- MR3: Skipped an id.\n"
        issues = self.lint(overrides={"RULEBOOK.md": bad})
        self.assertIn(
            "RULEBOOK.md: rule numbering not sequential "
            "(expected 2, got 3)", issues
        )

    def test_rule_without_modal(self):
        bad = ("- MR1: Money maps to Decimal, ideally.\n"
               "- MR2: Dates must stay UTC.\n")
        issues = self.lint(overrides={"RULEBOOK.md": bad})
        self.assertIn(
            "RULEBOOK.md: MR1 lacks a binding modal (must/never/always)",
            issues,
        )


class TestDependencyMap(LintCase):
    def test_single_wave_rejected(self):
        issues = self.lint(
            overrides={"DEPENDENCY_MAP.md": "1. Everything at once\n"}
        )
        self.assertIn(
            "DEPENDENCY_MAP.md: needs >= 2 numbered migration waves "
            "(found 1)", issues
        )

    def test_non_sequential_waves(self):
        issues = self.lint(
            overrides={"DEPENDENCY_MAP.md": "1. Utilities\n3. Handlers\n"}
        )
        self.assertIn(
            "DEPENDENCY_MAP.md: wave numbering not sequential "
            "(expected 2, got 3)", issues
        )


class TestGapInventory(LintCase):
    def test_gap_neither_question_nor_decision(self):
        bad = "- G1: The amount field is probably cents.\n"
        issues = self.lint(overrides={"GAP_INVENTORY.md": bad})
        self.assertIn(
            "GAP_INVENTORY.md: G1 is neither an open question ('?') "
            "nor resolved ('decision:')", issues
        )

    def test_no_gaps(self):
        issues = self.lint(overrides={"GAP_INVENTORY.md": "# empty\n"})
        self.assertIn(
            "GAP_INVENTORY.md: no '- G<n>:' gap entries found", issues
        )


class TestTestPlan(LintCase):
    def test_no_scenarios(self):
        issues = self.lint(
            overrides={"PORTABILITY_TEST_PLAN.md": "# plan\n\nparity_check.sh\n"}
        )
        self.assertIn(
            "PORTABILITY_TEST_PLAN.md: no '- S<n>:' golden scenarios found",
            issues,
        )

    def test_plan_must_reference_parity_script(self):
        plan = "- S1: Schedule for account 42 — diff old vs new outputs.\n"
        issues = self.lint(overrides={"PORTABILITY_TEST_PLAN.md": plan})
        self.assertTrue(
            any("does not reference the parity script" in i for i in issues)
        )


class TestWorkQueue(LintCase):
    def test_unknown_status(self):
        bad = "- src/models/loan.py [done]\n"
        issues = self.lint(overrides={"AGENT_WORK_QUEUE.md": bad})
        self.assertTrue(
            any("unknown status 'done'" in i for i in issues)
        )

    def test_unparseable_entry(self):
        bad = "- src/models/loan.py migrated\n"
        issues = self.lint(overrides={"AGENT_WORK_QUEUE.md": bad})
        self.assertTrue(any("unparseable entry" in i for i in issues))

    def test_empty_queue(self):
        issues = self.lint(overrides={"AGENT_WORK_QUEUE.md": "# queue\n"})
        self.assertIn("AGENT_WORK_QUEUE.md: no queue entries found", issues)


class TestReviewerPrompts(LintCase):
    def test_vague_review_prompt_rejected(self):
        bad = "# Reviewer prompts\n\n- Please look at the diff for style.\n"
        issues = self.lint(overrides={"REVIEWER_PROMPTS.md": bad})
        self.assertTrue(
            any("failure-mode categories covered" in i for i in issues)
        )


class TestPhaseGates(LintCase):
    def test_too_few_gates(self):
        bad = "1. Judge and tests exist.\n2. Ship it.\n"
        issues = self.lint(overrides={"PHASE_GATES.md": bad})
        self.assertIn(
            "PHASE_GATES.md: needs >= 3 numbered gates (found 2)", issues
        )

    def test_gate_one_must_establish_the_judge(self):
        bad = ("1. Kickoff meeting held.\n"
               "2. Parity green.\n3. Rollback approved.\n")
        issues = self.lint(overrides={"PHASE_GATES.md": bad})
        self.assertIn(
            "PHASE_GATES.md: gate 1 must establish the judge "
            "(mention judge/parity/test/verify)", issues
        )


class TestCli(LintCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, SCRIPT, *args],
            capture_output=True, text=True, timeout=30,
        )

    def test_pass_protocol(self):
        pack = write_pack(os.path.join(self.tmp, "pack"))
        r = self._run(pack)
        self.assertEqual(r.returncode, 0)
        self.assertIn("PACK_RESULT: PASS", r.stdout)

    def test_fail_protocol(self):
        pack = write_pack(os.path.join(self.tmp, "pack"),
                          drop=("RULEBOOK.md",))
        r = self._run(pack)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL: missing artifact: RULEBOOK.md", r.stdout)
        self.assertIn("PACK_RESULT: FAIL (1 issue(s))", r.stdout)

    def test_missing_dir_is_usage_error(self):
        r = self._run(os.path.join(self.tmp, "nope"))
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
