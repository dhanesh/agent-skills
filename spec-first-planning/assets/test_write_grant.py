#!/usr/bin/env python3
"""Unit tests for write_grant.py — stdlib-only, offline, deterministic.

Run standalone:  cd spec-first-planning/assets && python3 test_write_grant.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import contract_check  # noqa: E402
import spec_to_tasks  # noqa: E402
import write_grant  # noqa: E402
from test_spec_lint import FULL, LIGHT  # noqa: E402

_SCRIPT = os.path.join(HERE, "write_grant.py")


def _now_z():
    """RFC 3339 UTC 'now', truncated to the second (runtime fixture helper)."""
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _in_one_day():
    """RFC 3339 UTC one day from now (a valid, well-inside-the-cap expires_at)."""
    return (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


GOOD_ANSWERS = {
    "branch_pattern": "factory/*",
    "gate_policy": {"read_only": "auto", "local_reversible": "grant"},
}


class WriteGrantBase(unittest.TestCase):
    """A temp repo with docs/spec.md = FULL and a written task-plan envelope."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "docs"))
        self.spec = os.path.join(self.root, "docs", "spec.md")
        with open(self.spec, "w", encoding="utf-8") as f:
            f.write(FULL)
        plan = spec_to_tasks.derive_plan(FULL)
        self.plan_path = spec_to_tasks.write_task_plan_envelope(plan, self.spec, self.root)
        self.plan_rel = os.path.relpath(self.plan_path, self.root).replace(os.sep, "/")
        self.answers = dict(GOOD_ANSWERS, expires_at=_in_one_day())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TestBuildGrant(WriteGrantBase):
    def test_covered_under_check_grant(self):
        st = write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, self.answers, "Dana")
        contract_check.write_envelope(self.root, st)
        rep = contract_check.check_grant(self.root, "local_reversible", branch="factory/x")
        self.assertEqual(rep["status"], "COVERED", rep)
        self.assertEqual(rep["gate"], "grant")

    def test_decisions_match_spec(self):
        st = write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, self.answers, "Dana")
        self.assertEqual(
            st["predicate"]["payload"]["decisions"],
            [{"id": "D1", "question": "May the export add a dependency?",
              "answer": "no", "source": "sweep"}],
        )

    def test_single_grant_accepted_assertion(self):
        st = write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, self.answers, "Dana")
        assertions = st["predicate"]["assertions"]
        self.assertEqual(len(assertions), 1)
        a = assertions[0]
        self.assertEqual(a["test"], "grant-accepted")
        self.assertEqual(a["assertedBy"], {"human": "Dana"})
        self.assertEqual(a["result"], {"outcome": "passed"})
        self.assertEqual(
            a["command"],
            ["{python}", "{skill_dir:spec-first-planning}/assets/spec_lint.py",
             "--unattended", "docs/spec.md"],
        )

    def test_refused_when_spec_fails_unattended_lint(self):
        with open(self.spec, "w", encoding="utf-8") as f:
            f.write(LIGHT)
        with self.assertRaises(write_grant.GrantRefused):
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, self.answers, "Dana")

    def test_refused_when_merge_is_auto(self):
        answers = dict(self.answers, gate_policy={"merge": "auto"})
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn("must be ask", str(cm.exception))
        self.assertIn("merge", str(cm.exception))

    def test_refused_when_accepted_by_is_empty(self):
        with self.assertRaises(write_grant.GrantRefused):
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, self.answers, "")

    def test_refused_when_accepted_by_is_blank(self):
        with self.assertRaises(write_grant.GrantRefused):
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, self.answers, "   ")

    def test_subjects_pin_spec_and_plan(self):
        st = write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, self.answers, "Dana")
        names = {s["name"] for s in st["subject"]}
        self.assertEqual(names, {"docs/spec.md", self.plan_rel})

    def test_statement_has_no_violations(self):
        st = write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, self.answers, "Dana")
        self.assertEqual(contract_check.check_statement(st), [])
        self.assertEqual(contract_check.grant_violations(st), [])

    def test_grant_refused_beyond_seven_day_cap(self):
        far = (datetime.now(timezone.utc) + timedelta(days=8)).replace(microsecond=0).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        answers = dict(self.answers, expires_at=far)
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn("7 days", str(cm.exception))


class TestCli(WriteGrantBase):
    def _write_answers(self, answers=None):
        path = os.path.join(self.root, "answers.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.answers if answers is None else answers, f)
        return path

    def cli(self, *args):
        return subprocess.run([sys.executable, _SCRIPT, *args], capture_output=True, text=True,
                              timeout=60)

    def test_cli_writes_a_grant(self):
        answers_path = self._write_answers()
        r = self.cli("--root", self.root, "--spec", "docs/spec.md", "--plan", self.plan_path,
                     "--answers", answers_path, "--accepted-by", "Dana")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.strip().startswith("GRANT: "), r.stdout)
        path = r.stdout.strip()[len("GRANT: "):]
        self.assertTrue(os.path.isfile(path))

    def test_cli_refuses_on_invalid_gate_policy(self):
        answers_path = self._write_answers(dict(self.answers, gate_policy={"merge": "auto"}))
        r = self.cli("--root", self.root, "--spec", "docs/spec.md", "--plan", self.plan_path,
                     "--answers", answers_path, "--accepted-by", "Dana")
        self.assertEqual(r.returncode, 1)
        self.assertIn("REFUSED:", r.stdout)

    def test_cli_refuses_blank_accepted_by(self):
        answers_path = self._write_answers()
        r = self.cli("--root", self.root, "--spec", "docs/spec.md", "--plan", self.plan_path,
                     "--answers", answers_path, "--accepted-by", " ")
        self.assertEqual(r.returncode, 1)
        self.assertIn("REFUSED:", r.stdout)

    def test_cli_refuses_plan_outside_root(self):
        answers_path = self._write_answers()
        other = tempfile.mkdtemp()
        try:
            outside_plan = os.path.join(other, "plan.json")
            with open(outside_plan, "w", encoding="utf-8") as f:
                f.write("{}")
            r = self.cli("--root", self.root, "--spec", "docs/spec.md", "--plan", outside_plan,
                         "--answers", answers_path, "--accepted-by", "Dana")
            self.assertEqual(r.returncode, 1)
            self.assertIn("REFUSED:", r.stdout)
        finally:
            shutil.rmtree(other, ignore_errors=True)

    def test_cli_usage_error_on_missing_flag(self):
        r = self.cli("--root", self.root)
        self.assertEqual(r.returncode, 2)

    def test_cli_usage_error_on_unreadable_answers(self):
        r = self.cli("--root", self.root, "--spec", "docs/spec.md", "--plan", self.plan_path,
                     "--answers", os.path.join(self.root, "nope.json"), "--accepted-by", "Dana")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
