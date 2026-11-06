#!/usr/bin/env python3
"""Unit tests for write_grant.py --pipeline (pipeline-grant/v1) — stdlib-only, offline.

Run standalone:  python3 spec-first-planning/assets/test_write_grant_pipeline.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import contract_check  # noqa: E402
import write_grant  # noqa: E402

_SCRIPT = os.path.join(HERE, "write_grant.py")


def _in(days):
    """RFC 3339 UTC `days` from now, truncated to the second."""
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(
        microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def good_answers():
    return {
        "branch_pattern": "factory/*",
        "gate_policy": {"read_only": "auto", "local_reversible": "auto",
                        "push_branch": "grant", "open_pr": "grant",
                        "merge": "ask", "deploy": "ask", "spend": "ask",
                        "external_message": "ask", "delete": "ask"},
        "expires_at": _in(3),
        "caps": {"max_plans_per_day": 3, "max_open_runs": 1, "max_dispatches_per_day": 40},
        "budget": {"max_parallel": 2, "max_repairs_per_task": 2, "wall_clock_min": 1440},
        "stop_on": ["a new dependency is needed"],
    }


class PipelineBase(unittest.TestCase):
    """A temp git repo with a pick policy and a command allowlist."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", self.root], check=True)
        for rel, text in ((".intake/pick-policy.json", '{"kinds": ["bug"]}\n'),
                          (".factory/check-commands.txt", "make test\n")):
            path = os.path.join(self.root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        self.answers_path = os.path.join(self.root, "answers.json")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def envelopes(self):
        d = contract_check.envelope_dir(self.root)
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def run_cli(self, answers, accepted_by="Dana"):
        with open(self.answers_path, "w", encoding="utf-8") as f:
            json.dump(answers, f)
        argv = ["--pipeline", "--root", self.root, "--answers", self.answers_path]
        if accepted_by is not None:
            argv += ["--accepted-by", accepted_by]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = write_grant.main(argv)
        return rc, out.getvalue(), err.getvalue()


class TestPipelineRefusals(PipelineBase):
    def test_missing_accepted_by_exits_2_and_writes_nothing(self):
        rc, out, _ = self.run_cli(good_answers(), accepted_by=None)
        self.assertEqual(rc, 2)
        self.assertEqual(self.envelopes(), [])

    def test_missing_accepted_by_subprocess(self):
        with open(self.answers_path, "w", encoding="utf-8") as f:
            json.dump(good_answers(), f)
        p = subprocess.run([sys.executable, _SCRIPT, "--pipeline", "--root", self.root,
                            "--answers", self.answers_path], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertEqual(self.envelopes(), [])

    def test_blank_accepted_by_refused(self):
        rc, out, _ = self.run_cli(good_answers(), accepted_by="  ")
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED:", out)
        self.assertEqual(self.envelopes(), [])

    def test_eight_day_expiry_refused(self):
        rc, out, _ = self.run_cli(dict(good_answers(), expires_at=_in(8)))
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED:", out)
        self.assertIn("at most 7 days", out)
        self.assertEqual(self.envelopes(), [])

    def test_past_expiry_refused(self):
        rc, out, _ = self.run_cli(dict(good_answers(), expires_at=_in(-1)))
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED:", out)
        self.assertIn("expired", out)

    def test_merge_grant_refused(self):
        a = good_answers()
        a["gate_policy"]["merge"] = "grant"
        rc, out, _ = self.run_cli(a)
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED:", out)
        self.assertIn("gate_policy['merge'] must be ask", out)
        self.assertEqual(self.envelopes(), [])

    def test_every_never_grantable_class_refused(self):
        for cls in ("merge", "deploy", "spend", "external_message", "delete"):
            a = good_answers()
            a["gate_policy"][cls] = "grant"
            rc, out, _ = self.run_cli(a)
            self.assertEqual(rc, 1, cls)
            self.assertIn("gate_policy[%r] must be ask" % cls, out)

    def test_missing_cap_refused(self):
        a = good_answers()
        del a["caps"]["max_open_runs"]
        rc, out, _ = self.run_cli(a)
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED:", out)
        self.assertIn("payload.caps", out)
        self.assertEqual(self.envelopes(), [])

    def test_missing_caps_object_refused(self):
        a = good_answers()
        del a["caps"]
        rc, out, _ = self.run_cli(a)
        self.assertEqual(rc, 1)
        self.assertIn("payload.caps", out)

    def test_bad_cap_values_refused(self):
        for bad in (0, -1, True, 1.5, "3"):
            a = good_answers()
            a["caps"]["max_plans_per_day"] = bad
            rc, out, _ = self.run_cli(a)
            self.assertEqual(rc, 1, bad)
            self.assertIn("payload.caps", out)

    def test_extra_cap_refused(self):
        a = good_answers()
        a["caps"]["max_usd"] = 5
        rc, out, _ = self.run_cli(a)
        self.assertEqual(rc, 1)
        self.assertIn("payload.caps", out)

    def test_unknown_answer_key_refused(self):
        rc, out, _ = self.run_cli(dict(good_answers(), require_signature=True))
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED: unknown answer key 'require_signature'", out)

    def test_unknown_budget_key_refused(self):
        a = good_answers()
        a["budget"]["max_widgets"] = 1
        rc, out, _ = self.run_cli(a)
        self.assertEqual(rc, 1)
        self.assertIn("payload.budget", out)

    def test_missing_pick_policy_refused(self):
        os.remove(os.path.join(self.root, ".intake", "pick-policy.json"))
        rc, out, _ = self.run_cli(good_answers())
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED: .intake/pick-policy.json does not exist", out)

    def test_path_outside_root_refused(self):
        rc, out, _ = self.run_cli(dict(good_answers(), allowlist_path="../x.txt"))
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED:", out)

    def test_same_file_for_both_refused(self):
        rc, out, _ = self.run_cli(dict(good_answers(),
                                       allowlist_path=".intake/pick-policy.json"))
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED:", out)

    def test_auto_on_push_branch_refused(self):
        a = good_answers()
        a["gate_policy"]["push_branch"] = "auto"
        rc, out, _ = self.run_cli(a)
        self.assertEqual(rc, 1)
        self.assertIn("may not be auto", out)


class TestPipelineWrite(PipelineBase):
    def test_valid_input_writes_grant_check_accepts(self):
        rc, out, _ = self.run_cli(good_answers())
        self.assertEqual(rc, 0, out)
        last = out.strip().splitlines()[-1]
        self.assertTrue(last.startswith("GRANT: "), out)
        path = last[len("GRANT: "):]
        self.assertTrue(os.path.basename(path).startswith("pipeline-grant-v1-"))
        rep = contract_check.check_pipeline_grant(self.root)
        self.assertEqual(rep["status"], "VALID", rep)
        self.assertEqual(rep["path"], path)
        p = subprocess.run([sys.executable, os.path.join(HERE, "contract_check.py"),
                            "check-pipeline-grant", "--root", self.root],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("PIPELINE-GRANT: VALID", p.stdout)

    def test_planner_answer_is_written_and_checked(self):
        planner = ["/usr/local/bin/claude", "-p", "{prompt}"]
        rc, out, _ = self.run_cli(dict(good_answers(), planner=planner))
        self.assertEqual(rc, 0, out)
        path = out.strip().splitlines()[-1][len("GRANT: "):]
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["predicate"]["payload"]["planner"], planner)
        self.assertEqual(contract_check.check_pipeline_grant(self.root)["status"], "VALID")

    def test_bad_planner_refused(self):
        for planner in (["sh", "-c", "{prompt}"], ["claude", "-p"], "claude -p {prompt}",
                        ["claude", "--dangerously-skip-permissions", "-p", "{prompt}"],
                        ["claude", "--dangerously-skip-permissions=true", "-p", "{prompt}"],
                        ["claude", "--permission-mode=BypassPermissions", "{prompt}"]):
            with self.subTest(planner=planner):
                rc, out, _ = self.run_cli(dict(good_answers(), planner=planner))
                self.assertEqual(rc, 1, out)
                self.assertIn("payload.planner", out)
        self.assertEqual(self.envelopes(), [])

    def test_grant_pins_policy_and_allowlist(self):
        rc, out, _ = self.run_cli(good_answers())
        self.assertEqual(rc, 0, out)
        path = out.strip().splitlines()[-1][len("GRANT: "):]
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        p = st["predicate"]["payload"]
        self.assertEqual(p["pick_policy"]["path"], ".intake/pick-policy.json")
        self.assertEqual(p["allowlist"]["path"], ".factory/check-commands.txt")
        self.assertEqual(p["allowlist"]["sha256"], contract_check.sha256_file(
            os.path.join(self.root, ".factory", "check-commands.txt")))
        self.assertEqual(st["predicate"]["assertions"][0]["assertedBy"], {"human": "Dana"})
        self.assertNotIn("decisions", p)

    def test_grant_is_excluded_and_revoke_commands_printed(self):
        rc, out, _ = self.run_cli(good_answers())
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.root, ".git", "info", "exclude"), encoding="utf-8") as f:
            self.assertIn(write_grant.PIPELINE_GRANT_EXCLUDE, f.read().splitlines())
        gid = os.path.basename(out.strip().splitlines()[-1])[:-len(".json")]
        self.assertIn("REVOKE: ", out)
        self.assertIn("revoke-grant --root %s --id %s" % (self.root, gid), out)
        self.assertIn("KILL-SWITCH: ", out)
        status = subprocess.run(["git", "-C", self.root, "status", "--porcelain",
                                 "--untracked-files=all"], capture_output=True, text=True)
        self.assertNotIn("pipeline-grant-v1-", status.stdout)

    def test_answers_override_paths(self):
        alt = os.path.join(self.root, "policy", "pick.json")
        os.makedirs(os.path.dirname(alt))
        with open(alt, "w", encoding="utf-8") as f:
            f.write("{}\n")
        rc, out, _ = self.run_cli(dict(good_answers(), pick_policy_path="policy/pick.json"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(contract_check.check_pipeline_grant(self.root)["status"], "VALID")

    def test_changed_allowlist_makes_grant_stale(self):
        rc, out, _ = self.run_cli(good_answers())
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.root, ".factory", "check-commands.txt"), "a",
                  encoding="utf-8") as f:
            f.write("make eval\n")
        rep = contract_check.check_pipeline_grant(self.root)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "stale"))

    def test_autonomy_mode_unchanged_without_flag(self):
        # Without --pipeline the old parser still demands --spec and --plan.
        err = io.StringIO()
        with redirect_stderr(err):
            rc = write_grant.main(["--root", self.root, "--answers", self.answers_path,
                                   "--accepted-by", "Dana"])
        self.assertEqual(rc, 2)
        self.assertIn("--spec", err.getvalue())


# ── --under-pipeline: per-plan grants derived from a pipeline grant (R4) ─────────────────
import spec_lint  # noqa: E402
import spec_to_tasks  # noqa: E402
import test_idea_request  # noqa: E402
import test_intake_request  # noqa: E402
from test_spec_lint import FULL  # noqa: E402

ITEM = test_intake_request.ITEM_ID


def unattended_idea_spec():
    """A go-idea spec that passes spec_lint.py --unattended: the idea skeleton, then FULL's
    body without its own ## Problem and ## Users (the skeleton already holds them)."""
    body = FULL.split("\n## Goals", 1)[1]
    return test_idea_request.skeleton_for() + "\n## Goals" + body
ALLOWLIST = "{python} -m pytest -k rows\n{python} -m pytest tests\nmake test\n"


def unattended_intake_spec(cmd="{python} -m pytest -k rows"):
    """An intake spec that passes spec_lint.py --unattended: the skeleton intake_request.py
    prints for the fixture item, then FULL's body ([feature:], [proof:], an independence
    marker, ## Verification with Verify skill:, ## Decisions and a converged loop), with
    R1's command set to cmd."""
    root = tempfile.mkdtemp()
    try:
        r = test_intake_request.run(test_intake_request.INTAKE_REQUEST,
                                    test_intake_request.write_envelope(root))
        assert r.returncode == 0, r.stderr
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return r.stdout + FULL.split("\n", 1)[1].replace(
        "[cmd: {python} -m pytest -k rows]", "[cmd: %s]" % cmd)


class UnderPipelineBase(PipelineBase):
    """PipelineBase plus a written pipeline grant and an allowlist that lists the
    intake spec's check command."""

    caps = {"max_plans_per_day": 3, "max_open_runs": 3, "max_dispatches_per_day": 40}

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.root, ".factory", "check-commands.txt"), "w",
                  encoding="utf-8") as f:
            f.write(ALLOWLIST)
        rc, out, _ = self.run_cli(dict(good_answers(), caps=dict(self.caps)))
        self.assertEqual(rc, 0, out)
        self.pipe_path = out.strip().splitlines()[-1][len("GRANT: "):]
        self.pipe_id = os.path.basename(self.pipe_path)[:-len(".json")]
        self.policy_sha = contract_check.sha256_file(
            os.path.join(self.root, ".intake", "pick-policy.json"))

    def write_plan(self, text, name="spec.md"):
        os.makedirs(os.path.join(self.root, "docs"), exist_ok=True)
        spec = os.path.join(self.root, "docs", name)
        with open(spec, "w", encoding="utf-8") as f:
            f.write(text)
        path = spec_to_tasks.write_task_plan_envelope(spec_to_tasks.derive_plan(text), spec,
                                                      self.root)
        return os.path.relpath(path, self.root)

    def log_pick(self, by):
        d = os.path.join(self.root, ".skill-contract", "intake")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "intake-log.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "pick", "id": ITEM, "by": by,
                                "envelope": "x", "at": _in(0)}) + "\n")

    def autopicked_plan(self, cmd="{python} -m pytest -k rows"):
        self.log_pick("policy:" + self.policy_sha)
        return self.write_plan(unattended_intake_spec(cmd=cmd))

    def derive(self, plan_rel, ref=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = write_grant.main(["--under-pipeline", ref or self.pipe_id, "--root", self.root,
                                   "--plan", plan_rel])
        return rc, out.getvalue(), err.getvalue()

    def derived_path(self, out):
        last = out.strip().splitlines()[-1]
        self.assertTrue(last.startswith("GRANT: "), out)
        return last[len("GRANT: "):]

    def check(self, path=None):
        return contract_check.check_grant(self.root, "local_reversible", path=path,
                                          branch="factory/x")


class TestUnderPipelineDerives(UnderPipelineBase):
    def test_autopicked_plan_gets_a_grant_that_names_its_pipeline_grant(self):
        rc, out, _ = self.derive(self.autopicked_plan())
        self.assertEqual(rc, 0, out)
        path = self.derived_path(out)
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual(st["predicateType"], contract_check.GRANT_KIND)
        p = st["predicate"]["payload"]
        self.assertEqual(p["derived_from"], {"id": self.pipe_id, "sha256":
                                             contract_check.sha256_file(self.pipe_path)})
        self.assertEqual(st["predicate"]["assertions"][0]["assertedBy"], {"human": "Dana"})
        with open(self.pipe_path, encoding="utf-8") as f:
            pp = json.load(f)["predicate"]["payload"]
        self.assertEqual(p["gate_policy"], pp["gate_policy"])
        self.assertEqual(p["expires_at"], pp["expires_at"])
        rep = self.check(path)
        self.assertEqual(rep["status"], "COVERED", rep)
        self.assertIn("KILL-SWITCH: ", out)

    def test_pipeline_grant_path_is_accepted(self):
        rc, out, _ = self.derive(self.autopicked_plan(), ref=self.pipe_path)
        self.assertEqual(rc, 0, out)

    def test_go_idea_plan_gets_a_grant(self):
        test_idea_request.write_envelope(self.root)
        rc, out, _ = self.derive(self.write_plan(unattended_idea_spec()))
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.check(self.derived_path(out))["status"], "COVERED")

    def test_subprocess_cli(self):
        plan = self.autopicked_plan()
        p = subprocess.run([sys.executable, _SCRIPT, "--under-pipeline", self.pipe_id,
                            "--root", self.root, "--plan", plan],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("GRANT: ", p.stdout)


class TestUnderPipelineRefusals(UnderPipelineBase):
    def assertRefused(self, plan_rel, needle, ref=None):
        before = self.envelopes()
        rc, out, _ = self.derive(plan_rel, ref)
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSED:", out)
        self.assertIn(needle, out)
        self.assertEqual(self.envelopes(), before)

    def test_hand_written_plan_refused(self):
        self.assertRefused(self.write_plan(FULL), "hand-written plan")

    def test_hand_picked_item_refused(self):
        self.log_pick("Dana")
        self.assertRefused(self.write_plan(unattended_intake_spec()),
                           "was not picked by the pipeline's pick policy")

    def test_later_hand_pick_wins(self):
        self.log_pick("policy:" + self.policy_sha)
        self.log_pick("Dana")
        self.assertRefused(self.write_plan(unattended_intake_spec()), "was not picked")

    def test_pick_under_another_policy_refused(self):
        self.log_pick("policy:" + "0" * 64)
        self.assertRefused(self.write_plan(unattended_intake_spec()), "was not picked")

    def test_unreadable_intake_log_refused(self):
        plan = self.autopicked_plan()
        with open(os.path.join(self.root, ".skill-contract", "intake", "intake-log.jsonl"),
                  "a", encoding="utf-8") as f:
            f.write("{torn\n")
        self.assertRefused(plan, "is not JSON")

    def test_idea_without_go_handoff_refused(self):
        self.assertRefused(self.write_plan(unattended_idea_spec()), "no go handoff")

    def test_unlisted_command_refused(self):
        self.assertRefused(self.autopicked_plan(cmd="{python} -m pytest other"),
                           "not on the allowlist")

    def test_step_with_no_command_refused(self):
        # The unattended lint already fails a criterion with no command, so this case
        # reaches the allowlist check only through a spec the lint accepts. It is still
        # refused: the lint names the missing command first.
        text = unattended_intake_spec().replace(" [cmd: {python} -m pytest -k rows]", "")
        self.log_pick("policy:" + self.policy_sha)
        plan = self.write_plan(text)
        before = self.envelopes()
        rc, out, _ = self.derive(plan)
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSED:", out)
        self.assertEqual(self.envelopes(), before)

    def test_spec_failing_lint_refused(self):
        self.log_pick("policy:" + self.policy_sha)
        text = unattended_intake_spec().replace(
            "- R1: The export must include every row.", "- R1: The export includes every row.")
        self.assertRefused(self.write_plan(text), "fails spec_lint.py --unattended")

    def test_light_only_spec_refused(self):
        # Passes the light lint but not --unattended: no [feature:], [proof:],
        # ## Verification, ## Decisions or converged loop. A derived grant runs unattended.
        text = test_intake_request.intake_spec(cmd="{python} -m pytest tests")
        self.assertEqual(spec_lint.lint(text), [])
        self.log_pick("policy:" + self.policy_sha)
        self.assertRefused(self.write_plan(text), "fails spec_lint.py --unattended")
        rc, out, _ = self.derive(self.write_plan(text, name="spec2.md"))
        self.assertIn("\nFAIL: ", out)

    def test_stale_plan_refused(self):
        plan = self.autopicked_plan()
        with open(os.path.join(self.root, "docs", "spec.md"), "a", encoding="utf-8") as f:
            f.write("\n")
        self.assertRefused(plan, "stale")

    def test_changed_allowlist_refused(self):
        plan = self.autopicked_plan()
        with open(os.path.join(self.root, ".factory", "check-commands.txt"), "a",
                  encoding="utf-8") as f:
            f.write("make eval\n")
        self.assertRefused(plan, "REFUSED: the pipeline grant")

    def test_revoked_pipeline_grant_refused(self):
        contract_check.revoke_grant(self.root, self.pipe_id)
        self.assertRefused(self.autopicked_plan(), "superseded")

    def test_unknown_pipeline_grant_refused(self):
        self.assertRefused(self.autopicked_plan(), "REFUSED:", ref="../elsewhere.json")

    def test_unreadable_envelope_fails_the_count_closed(self):
        plan = self.autopicked_plan()
        with open(os.path.join(contract_check.envelope_dir(self.root), "junk.json"), "w",
                  encoding="utf-8") as f:
            f.write("{not json")
        self.assertRefused(plan, "caps cannot be counted")


class TestUnderPipelineCaps(UnderPipelineBase):
    caps = {"max_plans_per_day": 1, "max_open_runs": 5, "max_dispatches_per_day": 40}

    def test_past_max_plans_per_day_refused(self):
        plan = self.autopicked_plan()
        rc, out, _ = self.derive(plan)
        self.assertEqual(rc, 0, out)
        rc, out, _ = self.derive(plan)
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSED: max_plans_per_day reached", out)


class TestUnderPipelineOpenRuns(UnderPipelineBase):
    caps = {"max_plans_per_day": 5, "max_open_runs": 1, "max_dispatches_per_day": 40}

    def test_open_run_blocks_until_its_run_result_exists(self):
        plan = self.autopicked_plan()
        rc, out, _ = self.derive(plan)
        self.assertEqual(rc, 0, out)
        gid = os.path.basename(self.derived_path(out))[:-len(".json")]
        rc, out, _ = self.derive(plan)
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSED: max_open_runs reached", out)
        rr = {"predicateType": write_grant.RUN_RESULT_KIND,
              "predicate": {"id": "run-result-v1-x", "payload": {"grant": {"id": gid}}}}
        with open(os.path.join(contract_check.envelope_dir(self.root), "run-result-v1-x.json"),
                  "w", encoding="utf-8") as f:
            json.dump(rr, f)
        rc, out, _ = self.derive(plan)
        self.assertEqual(rc, 0, out)


class TestPipelineCapCounts(UnderPipelineBase):
    """--pipeline-caps prints the counts that --under-pipeline refuses with (R15)."""
    caps = {"max_plans_per_day": 2, "max_open_runs": 5, "max_dispatches_per_day": 7}

    def counts(self, ref=None):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = write_grant.main(["--pipeline-caps", ref or self.pipe_id, "--root", self.root])
        return rc, out.getvalue()

    def test_counts_follow_derivations(self):
        rc, out = self.counts()
        self.assertEqual(rc, 0, out)
        rep = json.loads(out)
        self.assertEqual(rep["caps"], self.caps)
        self.assertEqual(rep["counts"], {"max_plans_per_day": 0, "max_open_runs": 0})
        self.assertEqual(rep["derived"], [])
        plan = self.autopicked_plan()
        rc, dout, _ = self.derive(plan)
        self.assertEqual(rc, 0, dout)
        gid = os.path.basename(self.derived_path(dout))[:-len(".json")]
        rep = json.loads(self.counts(self.pipe_path)[1])
        self.assertEqual(rep["counts"], {"max_plans_per_day": 1, "max_open_runs": 1})
        self.assertEqual(rep["derived"], [gid])

    def test_unreadable_envelope_is_a_refusal_not_a_zero(self):
        with open(os.path.join(contract_check.envelope_dir(self.root), "junk.json"), "w",
                  encoding="utf-8") as f:
            f.write("{not json")
        rc, out = self.counts()
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSED: cannot read envelope junk.json", out)

    def test_unknown_pipeline_grant_refused(self):
        rc, out = self.counts("pipeline-grant-v1-20260101T000000Z-abcdef")
        self.assertEqual(rc, 1, out)
        self.assertTrue(out.startswith("REFUSED: "), out)


class TestPipelineRevocation(UnderPipelineBase):
    """S2: the kill switch revokes the pipeline grant and every grant derived from it."""

    def two_derived(self):
        plan = self.autopicked_plan()
        paths = []
        for _ in range(2):
            rc, out, _ = self.derive(plan)
            self.assertEqual(rc, 0, out)
            paths.append(self.derived_path(out))
        for p in paths:
            self.assertEqual(self.check(p)["status"], "COVERED")
        return paths

    def test_revoking_the_pipeline_grant_uncovers_every_derived_grant(self):
        paths = self.two_derived()
        out = io.StringIO()
        with redirect_stdout(out):
            rc = contract_check.main(["revoke-grant", "--root", self.root, "--id", self.pipe_id])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertEqual(contract_check.check_pipeline_grant(self.root, self.pipe_id)["reason"],
                         "superseded")
        for p in paths:
            rep = self.check(p)
            self.assertEqual((rep["status"], rep["reason"]), ("ASK", "pipeline"), rep)
        rep = self.check()  # the newest grant, as a caller with no path selects it
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "pipeline"), rep)

    def test_kill_switch_revokes_pipeline_and_derived_grants(self):
        paths = self.two_derived()
        out = io.StringIO()
        with redirect_stdout(out):
            rc = contract_check.main(["revoke-grant", "--root", self.root])
        self.assertEqual(rc, 0, out.getvalue())
        revoked = [ln.split()[1] for ln in out.getvalue().splitlines()
                   if ln.startswith("REVOKED: ")]
        ids = [os.path.basename(p)[:-len(".json")] for p in paths]
        self.assertEqual(sorted(revoked), sorted([self.pipe_id] + ids))
        self.assertEqual(contract_check.check_pipeline_grant(self.root)["status"], "ASK")
        for p in paths:
            rep = self.check(p)
            self.assertEqual((rep["status"], rep["reason"]), ("ASK", "pipeline"), rep)
        rep = self.check()
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "pipeline"), rep)
        # nothing live remains for a second kill switch
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(contract_check.main(["revoke-grant", "--root", self.root]), 1)

    def test_derived_grant_with_changed_pipeline_file_asks_pipeline(self):
        path = self.two_derived()[0]
        with open(self.pipe_path, "a", encoding="utf-8") as f:
            f.write("\n")
        rep = self.check(path)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "pipeline"), rep)

    def test_expired_pipeline_grant_uncovers_derived(self):
        path = self.two_derived()[0]
        later = datetime.now(timezone.utc) + timedelta(days=4)
        rep = contract_check.check_grant(self.root, "local_reversible", path=path,
                                         branch="factory/x", now=later)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "pipeline"), rep)

    def test_derived_grant_wider_than_pipeline_asks_pipeline(self):
        path = self.two_derived()[0]
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        st["predicate"]["payload"]["gate_policy"]["push_branch"] = "auto"
        self.assertTrue(contract_check.derived_grant_problems(self.root, st))
        st["predicate"]["payload"]["gate_policy"]["push_branch"] = "grant"
        st["predicate"]["payload"]["budget"]["max_parallel"] = 9
        self.assertTrue(contract_check.derived_grant_problems(self.root, st))


if __name__ == "__main__":
    unittest.main()
