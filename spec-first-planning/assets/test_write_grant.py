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


class TestPlanValidation(WriteGrantBase):
    """Fix round 1, item 1: --plan must be a fresh task-plan/v1 envelope for exactly this
    spec — never a README, the answers file, another spec's plan, or a stale one."""

    def test_refused_when_plan_is_not_an_envelope(self):
        readme = os.path.join(self.root, "README.md")
        with open(readme, "w", encoding="utf-8") as f:
            f.write("hi\n")
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", "README.md", self.answers, "Dana")
        self.assertIn("README.md", str(cm.exception))

    def test_refused_when_plan_is_of_another_kind(self):
        import contract_check as cc
        grant_payload = {"scope": {"repo": ".", "branch_pattern": "*"}, "decisions": [],
                         "defaults": [], "gate_policy": {}, "budget": {}, "stop_on": [],
                         "expires_at": _in_one_day(), "system_one": {"allowed": False},
                         "revoked": False}
        st = cc.build_statement(cc.GRANT_KIND, "spec-first-planning", "1.0.0", self.root,
                                ["docs/spec.md"], grant_payload, [])
        path = cc.write_envelope(self.root, st)
        wrong_rel = os.path.relpath(path, self.root).replace(os.sep, "/")
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", wrong_rel, self.answers, "Dana")
        self.assertIn("task-plan", str(cm.exception))

    def test_refused_when_plan_is_from_another_spec(self):
        other_spec_text = FULL.replace("May the export add a dependency?",
                                       "May the export add a dep?")
        os.makedirs(os.path.join(self.root, "old"))
        other_spec = os.path.join(self.root, "old", "s.md")
        with open(other_spec, "w", encoding="utf-8") as f:
            f.write(other_spec_text)
        other_plan = spec_to_tasks.write_task_plan_envelope(
            spec_to_tasks.derive_plan(other_spec_text), other_spec, self.root)
        other_rel = os.path.relpath(other_plan, self.root).replace(os.sep, "/")
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", other_rel, self.answers, "Dana")
        self.assertIn("old/s.md", str(cm.exception))

    def test_refused_when_plan_is_stale(self):
        with open(self.spec, "a", encoding="utf-8") as f:
            f.write("\n<!-- edited after the plan was written -->\n")
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, self.answers, "Dana")
        self.assertIn("stale", str(cm.exception).lower())

    def test_refused_when_plan_is_a_directory(self):
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", "docs", self.answers, "Dana")
        msg = str(cm.exception)
        self.assertNotIn("cannot read --spec", msg)
        self.assertIn("docs", msg)

    def test_valid_plan_still_succeeds(self):
        st = write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, self.answers, "Dana")
        self.assertEqual(contract_check.check_statement(st), [])


class TestExpiresAtInFuture(WriteGrantBase):
    """Fix round 1, item 2: a grant that is already expired at birth must be refused."""

    def test_expires_at_equal_to_now_is_refused(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        answers = dict(self.answers, expires_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana",
                                    now=now)
        self.assertEqual(str(cm.exception), "expires_at must be in the future")

    def test_expires_at_in_the_past_is_refused(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        answers = dict(self.answers, expires_at=past)
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertEqual(str(cm.exception), "expires_at must be in the future")

    def test_expires_at_in_the_future_still_succeeds(self):
        st = write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, self.answers, "Dana")
        self.assertEqual(contract_check.check_statement(st), [])


class TestUnknownAnswerKeys(WriteGrantBase):
    """Fix round 1, item 4."""

    def test_require_signature_is_refused(self):
        answers = dict(self.answers, require_signature="hw")
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn("unknown answer key", str(cm.exception))
        self.assertIn("A8", str(cm.exception))

    def test_revoked_is_refused_as_unknown(self):
        answers = dict(self.answers, revoked=True)
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn("unknown answer key", str(cm.exception))


class TestAnswerShapes(WriteGrantBase):
    """Fix round 1, item 9."""

    def test_budget_must_be_an_object(self):
        answers = dict(self.answers, budget="lots")
        with self.assertRaises(write_grant.GrantRefused):
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")

    def test_stop_on_must_be_a_list_of_strings(self):
        answers = dict(self.answers, stop_on=7)
        with self.assertRaises(write_grant.GrantRefused):
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")

    def test_defaults_must_be_a_list_of_objects(self):
        answers = dict(self.answers, defaults={"x": 1})
        with self.assertRaises(write_grant.GrantRefused):
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")

    def test_system_one_must_be_an_object_with_boolean_allowed(self):
        answers = dict(self.answers, system_one="yes")
        with self.assertRaises(write_grant.GrantRefused):
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")


class TestAcceptedByControlChars(WriteGrantBase):
    """Fix round 1, item 8."""

    def test_embedded_newline_is_refused(self):
        with self.assertRaises(write_grant.GrantRefused):
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, self.answers,
                                    "Dana\nGRANT: x")

    def test_plain_name_is_accepted(self):
        st = write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, self.answers,
                                     "Dana Purohit")
        self.assertEqual(st["predicate"]["assertions"][0]["assertedBy"], {"human": "Dana Purohit"})


class TestSpecReadErrors(WriteGrantBase):
    """Fix round 1, item 7: a bad spec file must refuse cleanly, never traceback."""

    def test_non_utf8_spec_is_refused_not_a_traceback(self):
        with open(self.spec, "wb") as f:
            f.write(b"\xff\xfe\x00bad")
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, self.answers, "Dana")
        self.assertIn("docs/spec.md", str(cm.exception))

    def test_missing_spec_is_refused_not_a_traceback(self):
        with self.assertRaises(write_grant.GrantRefused):
            write_grant.build_grant(self.root, "docs/nope.md", self.plan_rel, self.answers, "Dana")


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

    # Fix round 1, item 10: the two CLI cases the review called out as missing.
    def test_cli_merge_auto_echoes_grant_violations_message(self):
        answers_path = self._write_answers(dict(self.answers, gate_policy={"merge": "auto"}))
        r = self.cli("--root", self.root, "--spec", "docs/spec.md", "--plan", self.plan_path,
                     "--answers", answers_path, "--accepted-by", "Dana")
        self.assertEqual(r.returncode, 1)
        self.assertIn("REFUSED:", r.stdout)
        self.assertIn("must be ask", r.stdout)

    def test_cli_light_spec_is_refused(self):
        with open(self.spec, "w", encoding="utf-8") as f:
            f.write(LIGHT)
        answers_path = self._write_answers()
        r = self.cli("--root", self.root, "--spec", "docs/spec.md", "--plan", self.plan_path,
                     "--answers", answers_path, "--accepted-by", "Dana")
        self.assertEqual(r.returncode, 1)
        self.assertIn("REFUSED:", r.stdout)

    # Fix round 1, item 5: realpath containment for both --spec and --plan.
    def test_cli_refuses_absolute_spec_outside_root(self):
        outside = tempfile.mkdtemp()
        try:
            outside_spec = os.path.join(outside, "spec.md")
            with open(outside_spec, "w", encoding="utf-8") as f:
                f.write(FULL)
            answers_path = self._write_answers()
            r = self.cli("--root", self.root, "--spec", outside_spec, "--plan", self.plan_path,
                         "--answers", answers_path, "--accepted-by", "Dana")
            self.assertEqual(r.returncode, 1)
            self.assertIn("REFUSED:", r.stdout)
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_cli_refuses_dotdot_spec_outside_root(self):
        outside = tempfile.mkdtemp(dir=os.path.dirname(self.root))
        try:
            outside_spec = os.path.join(outside, "spec.md")
            with open(outside_spec, "w", encoding="utf-8") as f:
                f.write(FULL)
            rel = "../%s/spec.md" % os.path.basename(outside)
            answers_path = self._write_answers()
            r = self.cli("--root", self.root, "--spec", rel, "--plan", self.plan_path,
                         "--answers", answers_path, "--accepted-by", "Dana")
            self.assertEqual(r.returncode, 1)
            self.assertIn("REFUSED:", r.stdout)
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_cli_refuses_symlinked_spec_out_of_root(self):
        outside = tempfile.mkdtemp()
        try:
            outside_spec = os.path.join(outside, "spec.md")
            with open(outside_spec, "w", encoding="utf-8") as f:
                f.write(FULL)
            link = os.path.join(self.root, "docs", "link.md")
            os.symlink(outside_spec, link)
            answers_path = self._write_answers()
            r = self.cli("--root", self.root, "--spec", "docs/link.md", "--plan", self.plan_path,
                         "--answers", answers_path, "--accepted-by", "Dana")
            self.assertEqual(r.returncode, 1)
            self.assertIn("REFUSED:", r.stdout)
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_cli_refuses_symlinked_plan_out_of_root(self):
        outside = tempfile.mkdtemp()
        try:
            outside_file = os.path.join(outside, "light.md")
            with open(outside_file, "w", encoding="utf-8") as f:
                f.write(LIGHT)
            link = os.path.join(self.root, "plinkout.json")
            os.symlink(outside_file, link)
            answers_path = self._write_answers()
            r = self.cli("--root", self.root, "--spec", "docs/spec.md", "--plan", "plinkout.json",
                         "--answers", answers_path, "--accepted-by", "Dana")
            self.assertEqual(r.returncode, 1)
            self.assertIn("REFUSED:", r.stdout)
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_cli_dotslash_spec_and_plan_still_succeed(self):
        answers_path = self._write_answers()
        r = self.cli("--root", self.root, "--spec", "./docs/spec.md", "--plan",
                     "./" + self.plan_rel, "--answers", answers_path, "--accepted-by", "Dana")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.strip().startswith("GRANT: "))


if __name__ == "__main__":
    unittest.main()
