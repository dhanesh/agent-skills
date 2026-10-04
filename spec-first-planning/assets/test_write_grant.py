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

CLAUDE = "/usr/local/bin/claude"


def _write_recipe(root, deploy_prod, rollback):
    """A minimal .release/recipe.json carrying only the two keys write_grant's
    production-allowlist check reads."""
    d = os.path.join(root, ".release")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "recipe.json"), "w", encoding="utf-8") as f:
        json.dump({"deploy_prod": deploy_prod, "rollback": rollback}, f)


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



class TestReentry(WriteGrantBase):
    """Task 5: the grant can carry consent to scheduled re-entry, from answers.reentry.
    `reentry_problems`/`REENTRY_DEFAULTS` are Task 1's, in contract_check.py — this only
    checks that write_grant.py wires answers.reentry into the payload and refuses to
    duplicate the vendored checker's rules."""

    _write_answers = TestCli._write_answers
    cli = TestCli.cli

    def _run_write(self, answers):
        """Run the CLI end to end; return (stdout, the written statement dict)."""
        answers_path = self._write_answers(answers)
        r = self.cli("--root", self.root, "--spec", "docs/spec.md", "--plan", self.plan_path,
                     "--answers", answers_path, "--accepted-by", "Dana")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.strip().splitlines()
        path = next(ln for ln in lines if ln.startswith("GRANT: "))[len("GRANT: "):]
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        return r.stdout, st

    def test_a_reentry_block_is_written_and_shown_back(self):
        answers = dict(self.answers, reentry={"agent_cmd": ["claude", "-p", "{prompt}"],
                                              "interval_min": 10})
        out, st = self._run_write(answers)
        self.assertEqual(st["predicate"]["payload"]["reentry"]["agent_cmd"],
                         ["claude", "-p", "{prompt}"])
        self.assertIn("REENTRY: claude -p '{prompt}' every 10 min", out)

    WARN = ("warning: agent_cmd[0] is not an absolute path; the timer's PATH is the one "
            "captured at reentry install")

    def _cli_write(self, answers):
        r = self.cli("--root", self.root, "--spec", "docs/spec.md", "--plan", self.plan_path,
                     "--answers", self._write_answers(answers), "--accepted-by", "Dana")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def test_a_relative_agent_cmd_warns_about_the_timer_path(self):
        # final wave C1: a timer runs with a minimal PATH; a bare name resolves only on
        # the PATH captured at `reentry install`, so an absolute path is recommended
        r = self._cli_write(dict(self.answers, reentry={"agent_cmd": ["claude", "-p",
                                                                      "{prompt}"]}))
        self.assertIn(self.WARN, r.stderr)

    def test_an_absolute_agent_cmd_does_not_warn(self):
        r = self._cli_write(dict(self.answers, reentry={
            "agent_cmd": ["/usr/local/bin/claude", "-p", "{prompt}"]}))
        self.assertNotIn("warning: agent_cmd", r.stderr + r.stdout)

    def test_reentry_gets_default_values_it_does_not_supply(self):
        answers = dict(self.answers, reentry={"agent_cmd": ["claude", "-p", "{prompt}"]})
        _, st = self._run_write(answers)
        self.assertEqual(st["predicate"]["payload"]["reentry"],
                         {"agent_cmd": ["claude", "-p", "{prompt}"],
                          "interval_min": 10, "stall_min": 30, "max_reentries": 5})

    def test_no_reentry_means_no_block(self):
        out, st = self._run_write(self.answers)
        self.assertNotIn("reentry", st["predicate"]["payload"])
        self.assertNotIn("REENTRY:", out)

    def test_a_shell_agent_cmd_is_refused_by_the_cli(self):
        answers = dict(self.answers, reentry={"agent_cmd": ["bash", "-c", "{prompt}"]})
        answers_path = self._write_answers(answers)
        r = self.cli("--root", self.root, "--spec", "docs/spec.md", "--plan", self.plan_path,
                     "--answers", answers_path, "--accepted-by", "Dana")
        self.assertEqual(r.returncode, 1)
        self.assertIn("REFUSED:", r.stdout)
        self.assertIn("answers.reentry", r.stdout)

    def test_a_shell_agent_cmd_is_refused(self):
        answers = dict(self.answers, reentry={"agent_cmd": ["bash", "-c", "{prompt}"]})
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn("answers.reentry", str(cm.exception))
        self.assertIn("shell", str(cm.exception))

    def test_a_missing_prompt_token_is_refused(self):
        answers = dict(self.answers, reentry={"agent_cmd": ["claude", "-p", "hi"]})
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn("{prompt}", str(cm.exception))

    def test_statement_has_no_grant_violations(self):
        answers = dict(self.answers, reentry={"agent_cmd": ["claude", "-p", "{prompt}"]})
        st = write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertEqual(contract_check.check_statement(st), [])
        self.assertEqual(contract_check.grant_violations(st), [])


class AllowlistMatchTests(unittest.TestCase):
    """Ported verbatim from release-conductor/assets/test_release_deploy.py's
    AllowlistMatchTests (Task 5/R29), s/RL\\./write_grant./: the matcher is copied
    byte-for-byte in logic (Task 7), so its own test suite must come along too and
    stay in step with release.py's."""

    PROD = ["vercel", "deploy", "--prod"]

    def test_bash_and_bash_star_match_everything(self):
        for rules in ("Bash", "Bash(*)", "Read,Bash", "Read, Bash(*) ,Edit"):
            with self.subTest(rules=rules):
                self.assertTrue(write_grant.allowlist_matches(rules, self.PROD))

    def test_a_glob_that_covers_the_command_matches(self):
        self.assertTrue(write_grant.allowlist_matches("Read,Bash(vercel *)", self.PROD))
        self.assertTrue(write_grant.allowlist_matches("Bash(vercel deploy*)", self.PROD))

    def test_a_glob_for_another_program_does_not(self):
        self.assertFalse(write_grant.allowlist_matches("Bash(git *)", self.PROD))
        self.assertFalse(write_grant.allowlist_matches("Read,Edit,Bash(python3 -m pytest *)",
                                                        self.PROD))
        self.assertFalse(write_grant.allowlist_matches("", self.PROD))

    def test_commas_inside_parentheses_do_not_split(self):
        self.assertTrue(write_grant.allowlist_matches("Bash(x, y),Bash(vercel *)", self.PROD))
        self.assertFalse(write_grant.allowlist_matches("Bash(git a,b *)", self.PROD))

    def test_the_legacy_prefix_form_is_a_prefix_match(self):
        self.assertTrue(write_grant.allowlist_matches("Bash(vercel:*)", self.PROD))
        self.assertTrue(write_grant.allowlist_matches("Bash(vercel deploy:*)", self.PROD))
        self.assertFalse(write_grant.allowlist_matches("Bash(git push:*)", self.PROD))

    def test_quoting_and_the_program_basename_are_both_tried(self):
        argv = ["/opt/bin/fly", "deploy", "--app", "my app"]
        self.assertTrue(write_grant.allowlist_matches("Bash(fly deploy *)", argv))
        self.assertTrue(write_grant.allowlist_matches(
            "Bash(/opt/bin/fly deploy --app 'my app')", argv))

    def test_a_space_separated_list_splits_too(self):  # fix round 1, I2
        self.assertTrue(write_grant.allowlist_matches("Read Bash", self.PROD))
        self.assertTrue(write_grant.allowlist_matches(
            "Bash(git *) Bash(npx *)", ["npx", "vercel", "deploy", "--prod"]))
        self.assertFalse(write_grant.allowlist_matches("Read Edit Bash(git *)", self.PROD))

    def test_a_glob_prefix_and_extra_whitespace_still_match(self):  # fix round 1, M3/M4
        self.assertTrue(write_grant.allowlist_matches("Bash(*:*)", self.PROD))
        self.assertTrue(write_grant.allowlist_matches(
            "Bash(npx  vercel *)", ["npx", "vercel", "deploy", "--prod"]))
        self.assertTrue(write_grant.allowlist_matches("Bash(vercel   deploy:*)", self.PROD))

    def test_a_malformed_list_fails_closed(self):  # R29
        argv = ["npx", "vercel", "deploy", "--prod"]
        for rules in ("Bash(npx vercel *", "Read( Bash", "Foo( Bash(npx *)",
                      "Bash(npx *))", "Bash((npx *)", "Bash)(npx *)", "Read,Bash x"):
            with self.subTest(rules=rules):
                self.assertTrue(write_grant.allowlist_matches(rules, argv))
        # a longer tool name is another tool, and balanced inner parentheses are fine
        self.assertFalse(write_grant.allowlist_matches("BashOutput,Read", argv))
        self.assertFalse(write_grant.allowlist_matches("Bash(git log (x) *)", argv))

    def test_a_rule_running_on_after_its_parentheses_close_fails_closed(self):  # R45c
        argv = ["npx", "vercel", "deploy", "--prod"]
        for rules in ("Bash(a)Bash(npx *)", "Read,Bash(git *)Bash(npx *)", "Bash(git *)x",
                      "Bash(a)(npx *)"):
            with self.subTest(rules=rules):
                self.assertTrue(write_grant.allowlist_matches(rules, argv))
        # separated rules are still read rule by rule
        self.assertFalse(write_grant.allowlist_matches("Bash(a),Bash(git *)", argv))
        self.assertFalse(write_grant.allowlist_matches("Bash(a) Bash(git *)", argv))

    def test_agent_cmd_allowlist_reads_every_spelling(self):
        P = self.PROD
        for cmd in ([CLAUDE, "-p", "{prompt}", "--allowedTools", "Read,Bash(vercel *)"],
                    [CLAUDE, "-p", "{prompt}", "--allowed-tools", "Read", "Bash(vercel *)"],
                    [CLAUDE, "-p", "{prompt}", "--allowedTools=Bash(vercel *)"],
                    [CLAUDE, "-p", "{prompt}", "--dangerously-skip-permissions"],
                    [CLAUDE, "-p", "{prompt}", "--permission-mode", "bypassPermissions"]):
            with self.subTest(cmd=cmd):
                self.assertTrue(write_grant.agent_cmd_exposes(cmd, P))
        self.assertFalse(write_grant.agent_cmd_exposes(
            [CLAUDE, "-p", "{prompt}", "--allowedTools", "Read,Bash(git *)"], P))
        self.assertFalse(write_grant.agent_cmd_exposes([CLAUDE, "-p", "{prompt}"], P))


class TestProductionAllowlistRefusal(WriteGrantBase):
    """Task 7: write_grant refuses a grant whose reentry.agent_cmd allowlist would let an
    unattended agent run the repo's .release/recipe.json deploy_prod or rollback (design
    spec D10, "the allowlist paragraph")."""

    def _answers(self, agent_cmd):
        return dict(self.answers, reentry={"agent_cmd": agent_cmd})

    def test_refused_when_allowlist_matches_deploy_prod_raw_template(self):
        _write_recipe(self.root, ["fly", "deploy", "--env", "{env}"], ["fly", "rollback"])
        # "?env?" (no literal brace -- reentry_problems forbids an embedded {token} that
        # is not a whole {prompt}/{root} element) globs the *unexpanded* "{env}" (5 chars:
        # '{' + env + '}') but not the expanded "production" (10 chars): this rule can
        # only be reached through the raw-template form of the check.
        answers = self._answers([CLAUDE, "-p", "{prompt}", "--allowedTools",
                                 "Bash(fly deploy --env ?env?)"])
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn("production deploy or rollback", str(cm.exception))
        self.assertIn("release prep", str(cm.exception))

    def test_refused_when_allowlist_matches_deploy_prod_expanded_only(self):
        _write_recipe(self.root, ["fly", "deploy", "--env", "{env}"], ["fly", "rollback"])
        # the rule names the expanded form; the raw template ({env} literal) does not
        # match it, so only the expanded-placeholder check can catch this.
        answers = self._answers([CLAUDE, "-p", "{prompt}", "--allowedTools",
                                 "Bash(fly deploy --env production)"])
        with self.assertRaises(write_grant.GrantRefused):
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")

    def test_refused_when_allowlist_matches_rollback(self):
        _write_recipe(self.root, ["fly", "deploy", "--prod"], ["ssh", "prod", "rollback",
                                                               "{version}"])
        answers = self._answers([CLAUDE, "-p", "{prompt}", "--allowedTools",
                                 "Bash(ssh prod rollback *)"])
        with self.assertRaises(write_grant.GrantRefused):
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")

    def test_refused_on_permission_bypass_flag_with_recipe_present(self):
        _write_recipe(self.root, ["fly", "deploy", "--prod"], ["fly", "rollback"])
        answers = self._answers([CLAUDE, "-p", "{prompt}", "--dangerously-skip-permissions"])
        with self.assertRaises(write_grant.GrantRefused):
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")

    def test_grant_written_when_allowlist_does_not_match(self):
        _write_recipe(self.root, ["fly", "deploy", "--prod"], ["fly", "rollback"])
        answers = self._answers([CLAUDE, "-p", "{prompt}", "--allowedTools",
                                 "Read,Edit,Bash(git *)"])
        st = write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertEqual(st["predicate"]["payload"]["reentry"]["agent_cmd"],
                         answers["reentry"]["agent_cmd"])

    def test_refused_when_allowlist_reaches_release_py_itself(self):  # R43
        _write_recipe(self.root, ["fly", "deploy", "--prod"], ["fly", "rollback"])
        for rules in ("Read,Bash(python3 *)", "Bash(python3 */release.py *)",
                      "Bash(python3 */release.py deploy:*)", "Bash(python3 release.py *)",
                      "Bash(python3 * abandon *)",
                      'Bash(python3 "$SKILL_DIR/assets/release.py" rollback *)'):
            with self.subTest(rules=rules):
                answers = self._answers([CLAUDE, "-p", "{prompt}", "--allowedTools", rules])
                with self.assertRaises(write_grant.GrantRefused):
                    write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel,
                                            answers, "Dana")
        # a conductor-only python rule (the documented example) is not release.py
        answers = self._answers([CLAUDE, "-p", "{prompt}", "--allowedTools",
                                 "Bash(python3 /x/factory-conductor/assets/conductor.py *)"])
        write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")

    def test_the_installed_release_py_path_is_checked_when_present(self):  # R43
        paths = write_grant._release_py_paths()
        here = os.path.join(os.path.dirname(os.path.dirname(HERE)), "release-conductor",
                            "assets", "release.py")
        self.assertEqual(paths, [here] if os.path.isfile(here) else [])
        argvs = write_grant.release_tool_argvs(paths)
        for cmd in ("deploy", "rollback", "abandon"):
            self.assertIn(["python3", "release.py", cmd], argvs)
            if paths:
                self.assertIn(["python3", here, cmd], argvs)

    def test_no_recipe_means_no_check(self):
        answers = self._answers([CLAUDE, "-p", "{prompt}", "--allowedTools", "Bash"])
        st = write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn("reentry", st["predicate"]["payload"])

    def test_no_reentry_means_no_check_even_with_exposing_recipe(self):
        _write_recipe(self.root, ["fly", "deploy", "--prod"], ["fly", "rollback"])
        st = write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, self.answers,
                                     "Dana")
        self.assertNotIn("reentry", st["predicate"]["payload"])

    def test_recipe_invalid_json_is_refused(self):
        os.makedirs(os.path.join(self.root, ".release"), exist_ok=True)
        with open(os.path.join(self.root, ".release", "recipe.json"), "w",
                 encoding="utf-8") as f:
            f.write("{not json")
        answers = self._answers([CLAUDE, "-p", "{prompt}", "--allowedTools", "Bash(git *)"])
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn(".release/recipe.json", str(cm.exception))
        self.assertIn("not valid JSON", str(cm.exception))

    def test_recipe_non_utf8_is_refused_not_a_traceback(self):
        os.makedirs(os.path.join(self.root, ".release"), exist_ok=True)
        with open(os.path.join(self.root, ".release", "recipe.json"), "wb") as f:
            f.write(b"\xff\xfe\x00bad")
        answers = self._answers([CLAUDE, "-p", "{prompt}", "--allowedTools", "Bash(git *)"])
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn(".release/recipe.json", str(cm.exception))

    def test_recipe_missing_deploy_prod_is_refused(self):
        os.makedirs(os.path.join(self.root, ".release"), exist_ok=True)
        with open(os.path.join(self.root, ".release", "recipe.json"), "w",
                 encoding="utf-8") as f:
            json.dump({"rollback": ["fly", "rollback"]}, f)
        answers = self._answers([CLAUDE, "-p", "{prompt}", "--allowedTools", "Bash(git *)"])
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn("deploy_prod", str(cm.exception))

    def test_recipe_deploy_prod_not_a_list_is_refused(self):
        os.makedirs(os.path.join(self.root, ".release"), exist_ok=True)
        with open(os.path.join(self.root, ".release", "recipe.json"), "w",
                 encoding="utf-8") as f:
            json.dump({"deploy_prod": "fly deploy", "rollback": ["fly", "rollback"]}, f)
        answers = self._answers([CLAUDE, "-p", "{prompt}", "--allowedTools", "Bash(git *)"])
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn("deploy_prod", str(cm.exception))

    def test_cli_refuses_a_matching_allowlist(self):
        _write_recipe(self.root, ["fly", "deploy", "--prod"], ["fly", "rollback"])
        answers = self._answers([CLAUDE, "-p", "{prompt}", "--allowedTools",
                                 "Bash(fly deploy *)"])
        answers_path = os.path.join(self.root, "answers.json")
        with open(answers_path, "w", encoding="utf-8") as f:
            json.dump(answers, f)
        r = subprocess.run([sys.executable, _SCRIPT, "--root", self.root, "--spec",
                           "docs/spec.md", "--plan", self.plan_path, "--answers", answers_path,
                           "--accepted-by", "Dana"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn("REFUSED:", r.stdout)
        self.assertIn("production deploy or rollback", r.stdout)


class TestReleaseDefaults(WriteGrantBase):
    """Task 7: answers.release_defaults (optional) is validated strictly and written into
    the grant payload verbatim, for a later `release prep` to read."""

    def test_written_into_payload(self):
        answers = dict(self.answers, release_defaults={"bump": "minor", "grant_staging": True,
                                                        "grant_tag": False})
        st = write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertEqual(st["predicate"]["payload"]["release_defaults"],
                         {"bump": "minor", "grant_staging": True, "grant_tag": False})

    def test_absent_when_not_given(self):
        st = write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, self.answers,
                                     "Dana")
        self.assertNotIn("release_defaults", st["predicate"]["payload"])

    def test_missing_key_is_refused(self):
        answers = dict(self.answers, release_defaults={"bump": "minor", "grant_staging": True})
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn("grant_tag", str(cm.exception))

    def test_extra_key_is_refused(self):
        answers = dict(self.answers, release_defaults={"bump": "minor", "grant_staging": True,
                                                        "grant_tag": False, "extra": 1})
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn("extra", str(cm.exception))

    def test_bad_bump_is_refused(self):
        answers = dict(self.answers, release_defaults={"bump": "huge", "grant_staging": True,
                                                        "grant_tag": False})
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn("bump", str(cm.exception))

    def test_non_bool_grant_staging_is_refused(self):
        answers = dict(self.answers, release_defaults={"bump": "patch", "grant_staging": "yes",
                                                        "grant_tag": False})
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn("grant_staging", str(cm.exception))

    def test_non_bool_grant_tag_is_refused(self):
        answers = dict(self.answers, release_defaults={"bump": "patch", "grant_staging": True,
                                                        "grant_tag": 1})
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn("grant_tag", str(cm.exception))

    def test_not_an_object_is_refused(self):
        answers = dict(self.answers, release_defaults="patch")
        with self.assertRaises(write_grant.GrantRefused) as cm:
            write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertIn("release_defaults", str(cm.exception))

    def test_statement_has_no_violations(self):
        answers = dict(self.answers, release_defaults={"bump": "major", "grant_staging": False,
                                                        "grant_tag": True})
        st = write_grant.build_grant(self.root, "docs/spec.md", self.plan_rel, answers, "Dana")
        self.assertEqual(contract_check.check_statement(st), [])
        self.assertEqual(contract_check.grant_violations(st), [])


class TestGitExclude(WriteGrantBase):
    """A grant is one person's acceptance: write_grant keeps it out of commits (I1)."""

    _write_answers = TestCli._write_answers
    cli = TestCli.cli

    EXCLUDE_LINE = ".skill-contract/envelopes/autonomy-grant-v1-*"

    def _run(self):
        r = self.cli("--root", self.root, "--spec", "docs/spec.md", "--plan", self.plan_path,
                     "--answers", self._write_answers(), "--accepted-by", "Dana")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.strip().splitlines()[-1].startswith("GRANT: "), r.stdout)
        return r

    def _exclude(self):
        with open(os.path.join(self.root, ".git", "info", "exclude"), encoding="utf-8") as f:
            return f.read()

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_the_exclude_line_is_added_exactly_once(self):
        subprocess.run(["git", "init", "-q", self.root], check=True)
        before = self._exclude()
        r = self._run()
        self.assertIn("kept out of commits", r.stdout)
        self._run()
        text = self._exclude()
        self.assertEqual(text.splitlines().count(self.EXCLUDE_LINE), 1, text)
        self.assertTrue(text.startswith(before), "existing exclude lines must be kept")
        st = subprocess.run(["git", "-C", self.root, "status", "--porcelain",
                             "--untracked-files=all"], capture_output=True, text=True, check=True)
        self.assertNotIn("autonomy-grant-v1-", st.stdout)

    def test_a_missing_info_dir_is_created(self):
        os.makedirs(os.path.join(self.root, ".git"))
        self._run()
        self.assertEqual(self._exclude().splitlines(), [self.EXCLUDE_LINE])

    def test_an_exclude_without_a_trailing_newline_keeps_its_last_line(self):
        os.makedirs(os.path.join(self.root, ".git", "info"))
        with open(os.path.join(self.root, ".git", "info", "exclude"), "w",
                  encoding="utf-8") as f:
            f.write("*.log")
        self._run()
        self.assertEqual(self._exclude().splitlines(), ["*.log", self.EXCLUDE_LINE])

    def test_no_git_dir_writes_no_exclude(self):
        r = self._run()
        self.assertFalse(os.path.exists(os.path.join(self.root, ".git")))
        self.assertNotIn("kept out of commits", r.stdout)

class MatcherIdentityTests(unittest.TestCase):
    """#10: the matcher (and R43's release.py argv list) is copied from release-conductor's
    release.py, because skills do not import each other. When release.py is in this repo,
    each copied function is the same code (AST equal, docstrings aside); skipped where
    release-conductor is not installed beside this skill."""

    NAMES = ("_split_rules", "allowlist_matches", "agent_cmd_exposes", "release_tool_argvs",
             "ALLOWED_TOOLS_FLAGS", "RELEASE_TOOL_PATHS", "RELEASE_PROD_COMMANDS")

    @staticmethod
    def _defs(path):
        import ast
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        out = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                body = node.body
                if body and isinstance(body[0], ast.Expr) \
                        and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    node.body = body[1:]  # docstrings differ by design (who copies whom)
                out[node.name] = ast.dump(node)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        out[t.id] = ast.dump(node.value)
        return out

    def test_the_copied_matcher_is_the_same_code_as_release_py(self):
        release = os.path.join(os.path.dirname(os.path.dirname(HERE)), "release-conductor",
                               "assets", "release.py")
        if not os.path.isfile(release):
            self.skipTest("release-conductor is not installed beside spec-first-planning")
        ours, theirs = self._defs(_SCRIPT), self._defs(release)
        for name in self.NAMES:
            with self.subTest(name=name):
                self.assertIn(name, ours)
                self.assertEqual(ours[name], theirs.get(name),
                                 "%s differs from release.py's: keep the copies in step" % name)


if __name__ == "__main__":
    unittest.main()
