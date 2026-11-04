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


if __name__ == "__main__":
    unittest.main()
