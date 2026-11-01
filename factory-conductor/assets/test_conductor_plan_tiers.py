#!/usr/bin/env python3
"""test_conductor_plan_tiers.py — `conductor init` refuses malformed proof-tier fields
(the proof-tier gate spec, R1).

Each fixture plan is a real task-plan/v1 envelope under a covering grant, driven through
`conductor init`. A refused plan must exit 2, print `FAIL: task T1 <field>: ...`, and
leave no run directory and no new branch. A plan with valid tier fields and a plan with
neither field each start a run.
"""
import contextlib
import io
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C  # noqa: E402
from conductor_testkit import repo, write_grant, write_plan_envelope  # noqa: E402
from test_conductor_evidence import add_verify_skill, payload, task  # noqa: E402

FEATURES = {"notes-create": "t1.txt", "notes-list": "src/list.py"}
VALID = {"min_tier": "property", "clauses": ["notes-create:C1", "notes-create:C2"]}


def tiered(fields, features=("notes-create",)):
    t = task("T1", list(features))
    t.update(fields)
    return t


class InitTierFields(unittest.TestCase):
    def setUp(self):
        self.root = repo()
        add_verify_skill(self.root, FEATURES)

    def run_init(self, tasks, verification=True):
        plan = write_plan_envelope(self.root, plan=payload(tasks, verification))
        write_grant(self.root, plan)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = C.main(["init", "--plan", plan, "--root", self.root])
        return rc, out.getvalue(), err.getvalue()

    def branches(self):
        return subprocess.run(["git", "-C", self.root, "branch", "--format=%(refname:short)"],
                              capture_output=True, text=True, check=True).stdout.split()

    def runs(self):
        d = os.path.join(self.root, C.RUNS_DIR)
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def assert_refused(self, tasks, field, verification=True):
        before = self.branches()
        rc, out, err = self.run_init(tasks, verification)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("FAIL: task T1 %s: " % field, err)
        self.assertEqual(self.runs(), [], "a refused init left a run directory")
        self.assertEqual(self.branches(), before, "a refused init cut a branch")
        self.assertNotIn("RUN:", out)
        return err

    # Case 1: min_tier is not exactly one of the four tiers.
    def test_min_tier_null(self):
        self.assert_refused([tiered(dict(VALID, min_tier=None))], "min_tier")

    def test_min_tier_unknown(self):
        self.assert_refused([tiered(dict(VALID, min_tier="fast"))], "min_tier")

    def test_min_tier_wrong_case(self):
        self.assert_refused([tiered(dict(VALID, min_tier="Property"))], "min_tier")

    # Case 2: clauses is not a non-empty list of distinct refs to the task's features.
    def test_clauses_null(self):
        self.assert_refused([tiered(dict(VALID, clauses=None))], "clauses")

    def test_clauses_a_string(self):
        self.assert_refused([tiered(dict(VALID, clauses="notes-create:C1"))], "clauses")

    def test_clauses_a_number(self):
        self.assert_refused([tiered(dict(VALID, clauses=[5]))], "clauses")

    def test_clauses_a_null_item(self):
        self.assert_refused([tiered(dict(VALID, clauses=[None]))], "clauses")

    def test_clauses_empty(self):
        self.assert_refused([tiered(dict(VALID, clauses=[]))], "clauses")

    def test_clauses_duplicate(self):
        self.assert_refused([tiered(dict(VALID, clauses=["notes-create:C1",
                                                          "notes-create:C1"]))], "clauses")

    def test_clause_zero(self):
        self.assert_refused([tiered(dict(VALID, clauses=["notes-create:C0"]))], "clauses")

    def test_clause_leading_zero(self):
        self.assert_refused([tiered(dict(VALID, clauses=["notes-create:C01"]))], "clauses")

    def test_clause_feature_not_named_by_the_task(self):
        err = self.assert_refused([tiered(dict(VALID, clauses=["notes-list:C1"]))], "clauses")
        self.assertIn("notes-list", err)

    # Case 3: one key without the other names the missing key.
    def test_min_tier_alone(self):
        self.assert_refused([tiered({"min_tier": "property"})], "clauses")

    def test_clauses_alone(self):
        self.assert_refused([tiered({"clauses": ["notes-create:C1"]})], "min_tier")

    # Case 4: tier fields in a plan with no verification block.
    def test_tier_fields_without_a_verification_block(self):
        err = self.assert_refused([tiered(VALID)], "min_tier", verification=False)
        self.assertIn("FAIL: task T1 clauses: ", err)

    def test_the_refusal_comes_before_the_verify_command_check(self):
        t = tiered(dict(VALID, min_tier="fast"))
        t["verify"] = [{"text": "ok", "command": None}]
        err = self.assert_refused([t], "min_tier")
        self.assertNotIn("has no command", err)

    def test_the_check_reads_no_feature_file(self):
        # The task names a feature the map does not hold: the tier check refuses on the
        # plan alone, before anything opens the map.
        orig = C.open if hasattr(C, "open") else None
        opened = []
        real_open = open

        def spy(path, *a, **kw):
            opened.append(str(path))
            return real_open(path, *a, **kw)

        C.open = spy
        try:
            self.assert_refused([tiered(dict(VALID, min_tier=None))], "min_tier")
        finally:
            if orig is None:
                del C.open
            else:
                C.open = orig
        self.assertFalse([p for p in opened if os.sep + "features" + os.sep in p], opened)

    # The plans that start a run.
    def test_valid_tier_fields_start_a_run(self):
        rc, out, err = self.run_init([tiered(VALID), task("T2", ["notes-list"])])
        self.assertEqual(rc, 0, out + err)
        self.assertIn("RUN:", out)
        self.assertNotIn("FAIL:", err)

    def test_every_tier_is_accepted(self):
        for tier in C.TIERS:
            self.assertEqual(C.plan_tier_problems(payload([tiered(dict(VALID, min_tier=tier))])),
                             [], tier)

    def test_a_plan_with_neither_field_starts_a_run_as_today(self):
        rc, out, err = self.run_init([task("T1", ["notes-create"])])
        self.assertEqual(rc, 0, out + err)
        self.assertIn("RUN:", out)
        self.assertNotIn("FAIL:", err)

    def test_a_legacy_plan_with_neither_field_starts_a_run(self):
        rc, out, err = self.run_init([dict(task("T1", []), features=None)], verification=False)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("RUN:", out)

    def test_state_does_not_copy_the_tier_fields(self):
        rc, out, err = self.run_init([tiered(VALID)])
        self.assertEqual(rc, 0, out + err)
        st = C.State.load(C.state_path(self.root))
        self.assertNotIn("min_tier", st.tasks["T1"])
        self.assertNotIn("clauses", st.tasks["T1"])


if __name__ == "__main__":
    unittest.main()
