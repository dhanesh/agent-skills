"""Tests for `release deploy` (Task 5) and the allowlist refusal it shares with `stage`
(R20). Stdlib only, offline: the remote is a local bare repository, deploy_prod is a stub
argv that appends its cwd and arguments to a marker file, and the production version
probe `cat`s a file the test writes. Nothing touches a real target or a network."""
import datetime as dt
import os
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release as RL  # noqa: E402
import contract_check as CC  # noqa: E402
import test_release_stage as TS  # noqa: E402  (a module: its tests are not collected here)
from release_testkit import tmpdir  # noqa: E402

# Appends "<cwd> <args...>" to argv[1]. No braces: a recipe argv carries only the
# {version}/{commit}/{env} tokens.
MARK = ("import os, sys; open(sys.argv[1], 'a').write(' '.join([os.getcwd()] + "
        "sys.argv[2:]) + chr(10))")
CLAUDE = "/usr/local/bin/claude"


def plant_grant(root, allowed=None, agent_cmd=None, revoked=False):
    """A valid factory-style autonomy grant whose reentry.agent_cmd carries `allowed` as
    its --allowedTools value. Asserts it is well-formed and a live head, so a negative
    case can never pass because the grant silently dropped out of the enumeration."""
    d = os.path.join(root, ".skill-contract", "plan")
    os.makedirs(d, exist_ok=True)
    for name in ("spec.md", "plan.json"):
        with open(os.path.join(d, name), "w") as f:
            f.write(name + "\n")
    subjects = [".skill-contract/plan/spec.md", ".skill-contract/plan/plan.json"]
    now = CC.utc_now()
    cmd = agent_cmd or [CLAUDE, "-p", "{prompt}", "--allowedTools", allowed]
    payload = {"scope": {"repo": ".", "branch_pattern": "factory/*"}, "decisions": [],
               "defaults": [], "gate_policy": {"local_reversible": "grant"}, "budget": {},
               "stop_on": [], "expires_at": RL._rfc3339(now + dt.timedelta(days=1)),
               "system_one": {"allowed": False}, "revoked": revoked,
               "reentry": {"agent_cmd": cmd}}
    accepted = {"test": "grant-accepted", "assertedBy": {"human": "Dana Human"},
                "result": {"outcome": "passed"}, "subject": [CC.pin(root, s) for s in subjects],
                "command": ["{python}", "{skill_dir:spec-first-planning}/assets/write_grant.py"]}
    st = CC.build_statement(CC.GRANT_KIND, "spec-first-planning", "1.0.0", root, subjects,
                            payload, [] if revoked else [accepted], now=now)
    assert CC.check_statement(st) == [], CC.check_statement(st)
    assert CC.grant_violations(st) == [], CC.grant_violations(st)
    CC.write_envelope(root, st)
    gid = st["predicate"]["id"]
    assert gid in [h[2]["predicate"]["id"] for h in CC._live_heads(root)]
    return gid


class AllowlistMatchTests(unittest.TestCase):
    PROD = ["vercel", "deploy", "--prod"]

    def test_bash_and_bash_star_match_everything(self):
        for rules in ("Bash", "Bash(*)", "Read,Bash", "Read, Bash(*) ,Edit"):
            with self.subTest(rules=rules):
                self.assertTrue(RL.allowlist_matches(rules, self.PROD))

    def test_a_glob_that_covers_the_command_matches(self):
        self.assertTrue(RL.allowlist_matches("Read,Bash(vercel *)", self.PROD))
        self.assertTrue(RL.allowlist_matches("Bash(vercel deploy*)", self.PROD))

    def test_a_glob_for_another_program_does_not(self):
        self.assertFalse(RL.allowlist_matches("Bash(git *)", self.PROD))
        self.assertFalse(RL.allowlist_matches("Read,Edit,Bash(python3 -m pytest *)",
                                              self.PROD))
        self.assertFalse(RL.allowlist_matches("", self.PROD))

    def test_commas_inside_parentheses_do_not_split(self):
        self.assertTrue(RL.allowlist_matches("Bash(x, y),Bash(vercel *)", self.PROD))
        self.assertFalse(RL.allowlist_matches("Bash(git a,b *)", self.PROD))

    def test_the_legacy_prefix_form_is_a_prefix_match(self):
        self.assertTrue(RL.allowlist_matches("Bash(vercel:*)", self.PROD))
        self.assertTrue(RL.allowlist_matches("Bash(vercel deploy:*)", self.PROD))
        self.assertFalse(RL.allowlist_matches("Bash(git push:*)", self.PROD))

    def test_quoting_and_the_program_basename_are_both_tried(self):
        argv = ["/opt/bin/fly", "deploy", "--app", "my app"]
        self.assertTrue(RL.allowlist_matches("Bash(fly deploy *)", argv))
        self.assertTrue(RL.allowlist_matches("Bash(/opt/bin/fly deploy --app 'my app')", argv))

    def test_a_space_separated_list_splits_too(self):  # fix round 1, I2
        self.assertTrue(RL.allowlist_matches("Read Bash", self.PROD))
        self.assertTrue(RL.allowlist_matches("Bash(git *) Bash(npx *)",
                                             ["npx", "vercel", "deploy", "--prod"]))
        self.assertFalse(RL.allowlist_matches("Read Edit Bash(git *)", self.PROD))

    def test_a_glob_prefix_and_extra_whitespace_still_match(self):  # fix round 1, M3/M4
        self.assertTrue(RL.allowlist_matches("Bash(*:*)", self.PROD))
        self.assertTrue(RL.allowlist_matches("Bash(npx  vercel *)",
                                             ["npx", "vercel", "deploy", "--prod"]))
        self.assertTrue(RL.allowlist_matches("Bash(vercel   deploy:*)", self.PROD))

    def test_a_malformed_list_fails_closed(self):  # R29
        argv = ["npx", "vercel", "deploy", "--prod"]
        for rules in ("Bash(npx vercel *", "Read( Bash", "Foo( Bash(npx *)",
                      "Bash(npx *))", "Bash((npx *)", "Bash)(npx *)", "Read,Bash x"):
            with self.subTest(rules=rules):
                self.assertTrue(RL.allowlist_matches(rules, argv))
        # a longer tool name is another tool, and balanced inner parentheses are fine
        self.assertFalse(RL.allowlist_matches("BashOutput,Read", argv))
        self.assertFalse(RL.allowlist_matches("Bash(git log (x) *)", argv))

    def test_a_rule_running_on_after_its_parentheses_close_fails_closed(self):  # R45c
        argv = ["npx", "vercel", "deploy", "--prod"]
        for rules in ("Bash(a)Bash(npx *)", "Read,Bash(git *)Bash(npx *)", "Bash(git *)x",
                      "Bash(a)(npx *)"):
            with self.subTest(rules=rules):
                self.assertTrue(RL.allowlist_matches(rules, argv))
        # separated rules are still read rule by rule
        self.assertFalse(RL.allowlist_matches("Bash(a),Bash(git *)", argv))
        self.assertFalse(RL.allowlist_matches("Bash(a) Bash(git *)", argv))

    def test_agent_cmd_allowlist_reads_every_spelling(self):
        P = self.PROD
        for cmd in ([CLAUDE, "-p", "{prompt}", "--allowedTools", "Read,Bash(vercel *)"],
                    [CLAUDE, "-p", "{prompt}", "--allowed-tools", "Read", "Bash(vercel *)"],
                    [CLAUDE, "-p", "{prompt}", "--allowedTools=Bash(vercel *)"],
                    [CLAUDE, "-p", "{prompt}", "--dangerously-skip-permissions"],
                    [CLAUDE, "-p", "{prompt}", "--permission-mode", "bypassPermissions"]):
            with self.subTest(cmd=cmd):
                self.assertTrue(RL.agent_cmd_exposes(cmd, P))
        self.assertFalse(RL.agent_cmd_exposes(
            [CLAUDE, "-p", "{prompt}", "--allowedTools", "Read,Bash(git *)"], P))
        self.assertFalse(RL.agent_cmd_exposes([CLAUDE, "-p", "{prompt}"], P))

    # Every spelling of a rule that lets an agent reach release.py's production commands
    # (R43, hardening 5): an absolute or versioned interpreter, an env or uv wrapper, a
    # ~ skill path. Each one exposes; harmless rules still do not.
    EXPOSING_SPELLINGS = (
        "Bash(/usr/bin/python3 *)", "Bash(/usr/local/bin/python3 */release.py *)",
        "Bash(python3.12 *)", "Bash(python3.9 */release.py deploy:*)",
        "Bash(/usr/bin/env python3 *)", "Bash(env python3 *)",
        "Bash(env PYTHONPATH=. python3 *)", "Bash(uv run *)", "Bash(uv run python *)",
        "Bash(uv run:*)", "Bash(uvx *)", "Bash(./release.py *)",
        "Bash(python3 ~/.claude/skills/release-conductor/assets/release.py *)",
        "Bash(python3 ~/.agents/skills/release-conductor/assets/release.py deploy:*)")
    HARMLESS = ("Bash(git status)", "Bash(python3 tests/run_tests.py)",
                "Bash(/usr/bin/python3 tests/run_tests.py)", "Bash(uv run pytest *)",
                "Bash(env FOO=1 make test)", "Bash(uvx ruff check *)", "BashOutput")

    def test_every_interpreter_and_path_spelling_reaches_release_py(self):
        argvs = RL.release_tool_argvs([os.path.abspath(RL.__file__)])
        for rules in self.EXPOSING_SPELLINGS:
            with self.subTest(rules=rules):
                cmd = [CLAUDE, "-p", "{prompt}", "--allowedTools", "Read," + rules]
                self.assertTrue(any(RL.agent_cmd_exposes(cmd, a) for a in argvs))
        for rules in self.HARMLESS:
            with self.subTest(rules=rules):
                cmd = [CLAUDE, "-p", "{prompt}", "--allowedTools", "Read," + rules]
                self.assertFalse(any(RL.agent_cmd_exposes(cmd, a) for a in argvs))

    def test_a_wrapper_or_absolute_interpreter_rule_reaches_deploy_prod_too(self):
        for rules in ("Bash(env vercel *)", "Bash(/usr/bin/env vercel deploy *)",
                      "Bash(uv run vercel *)", "Bash(/opt/homebrew/bin/vercel *)"):
            with self.subTest(rules=rules):
                self.assertTrue(RL.allowlist_matches(rules, self.PROD))
        self.assertFalse(RL.allowlist_matches("Bash(env git *)", self.PROD))


class DeployBase(TS.StageBase):
    def setUp(self):
        super().setUp()
        self.prod_marker = os.path.join(self.d, "prod.marker")
        self.rollback_marker = os.path.join(self.d, "rollback.marker")
        self.prod_probe = os.path.join(self.www, "version-production.txt")
        self.recipe.update(
            deploy_prod=[sys.executable, "-c", MARK, self.prod_marker, "{env}", "{version}",
                         "{commit}"],
            rollback=[sys.executable, "-c", MARK, self.rollback_marker, "{version}"])

    def staged(self, ci=None, **over):
        """A release staged end to end; returns (root, release commit)."""
        root, commit = self.make(ci=ci, **over)
        self.assertEqual(self.stage()[0], 0)
        self.record_all(commit)
        rc, out, err = self.evidence()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.rel().status, "staged")
        return root, commit

    def deploy(self, *extra, yes="Dana Human", seen=True):
        """deploy with the human's yes. R36: a yes counts only for the summary the human
        saw, so by default the helper first runs the waiting deploy that shows it (as the
        SKILL does); seen=False sends the yes straight away."""
        argv = ["deploy", "--root", self.root]
        if yes is not None:
            if seen and "--unattended" not in extra:
                TS.run(argv + list(extra))
            argv += ["--approved-by", yes]
        return TS.run(argv + list(extra))

    def set_prod(self, text):
        with open(self.prod_probe, "w") as f:
            f.write(text)

    def assert_refused(self, rc, out, reason, status="staged"):
        self.assertEqual(rc, 2, out)
        self.assertIn(reason, out)
        self.assertEqual(self.lines(self.prod_marker), [])
        self.assertEqual(self.rel().status, status)
        self.assertIsNone(self.rel().approved_by)

    def edit_state(self, **fields):
        rel = self.rel()
        for k, v in fields.items():
            setattr(rel, k, v)
        rel.save()


class AttendedDeployTests(DeployBase):
    def test_an_attended_deploy_runs_once_in_the_build_checkout(self):
        root, commit = self.staged()
        self.set_prod("1.1.0\n")
        rc, out, err = self.deploy()
        self.assertEqual(rc, 0, out + err)
        lines = out.splitlines()
        self.assertIn("DEPLOY: 1.2.0 %s" % commit, lines)
        self.assertIn("NEXT: verify-prod", lines)
        self.assertIn("CLAIMED", out)
        self.assertIn(RL.recipe_sha(root, rev=commit), out)
        self.assertIn("2 evidence records", out)
        marks = self.lines(self.prod_marker)
        self.assertEqual(len(marks), 1)
        cwd, env, version, at = marks[0].split(" ")
        build = os.path.join(root, ".skill-contract", "releases", "1.2.0", "wt-build")
        self.assertEqual(os.path.realpath(cwd), os.path.realpath(build))
        self.assertEqual((env, version, at), ("production", "1.2.0", commit))
        rel = self.rel()
        self.assertEqual(rel.status, "deployed")
        self.assertEqual(rel.approved_by, {"name": "Dana Human", "status": "CLAIMED"})
        self.assertEqual(rel.rollback_target["source"], "probe")
        self.assertEqual(rel.rollback_target["version"], "1.1.0")
        # the rollback argv shown is the one that would roll back to the probed target
        self.assertIn("rollback.marker 1.1.0", out)
        self.assertEqual(self.lines(self.rollback_marker), [])
        events = [e["event"] for e in rel.events()]
        self.assertLess(events.index("deploying"), events.index("deploy_prod"))
        # the deploy gate was asked, and answered ASK: never covered
        self.assertIn(("deploy", "ASK"), self.gates())
        # a second deploy of a deployed release is refused; the command is not re-run
        rc, out, _ = self.deploy()
        self.assertEqual(rc, 2, out)
        self.assertEqual(len(self.lines(self.prod_marker)), 1)

    def test_no_probe_answer_and_no_previous_release_means_no_rollback_target(self):
        self.staged()  # the production probe file does not exist: cat fails
        rc, out, err = self.deploy()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("no rollback target", out)
        self.assertEqual(self.rel().rollback_target["source"], "none")

    def test_a_failing_production_deploy_is_prod_failed(self):
        self.staged(deploy_prod=["false"])
        rc, out, _ = self.deploy()
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: deploy-failed", out)
        self.assertIn("NEXT: verify-prod then ask the human", out)
        self.assertEqual(self.rel().status, "prod_failed")

    def test_a_deploy_that_times_out_has_an_unknown_outcome(self):  # R25
        self.staged(deploy_prod=[sys.executable, "-c", "import time; time.sleep(30)"])
        with mock.patch.object(RL, "CMD_TIMEOUT", 0.5):
            rc, out, _ = self.deploy()
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: outcome-unknown", out)
        self.assertIn("NEXT: verify-prod then ask the human", out.splitlines())
        self.assertEqual(self.rel().status, "outcome_unknown")

    def test_a_probe_that_edits_the_build_checkout_refuses(self):  # M1
        py = sys.executable
        prog = ("import sys; e = sys.argv[1]; (e == 'production') and "
                "open('package.json', 'a').write(chr(10)); print(open(sys.argv[2]).read())")
        self.staged(version_probe=[py, "-c", prog, "{env}",
                                   os.path.join(self.www, "version-{env}.txt")])
        self.set_prod("1.1.0\n")
        rc, out, _ = self.deploy()
        self.assert_refused(rc, out, "build-tree-changed")

    def test_a_simulated_crash_mid_deploy_never_reruns_the_command(self):
        self.staged()
        self.assertEqual(self.deploy()[0], 0)
        self.assertEqual(len(self.lines(self.prod_marker)), 1)
        self.edit_state(status="deploying")  # the process died before recording the end
        rc, out, _ = self.deploy()
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: outcome-unknown", out)
        self.assertIn("NEXT: verify-prod then ask the human", out.splitlines())
        self.assertEqual(len(self.lines(self.prod_marker)), 1)
        self.assertEqual(self.rel().status, "outcome_unknown")
        # and outcome_unknown is never deployed again, with or without a yes
        rc, out, _ = self.deploy()
        self.assertEqual(rc, 3, out)
        self.assertIn("NEXT: verify-prod then ask the human", out.splitlines())
        self.assertEqual(len(self.lines(self.prod_marker)), 1)
        self.assertEqual(self.rel().status, "outcome_unknown")

    def test_a_crash_mid_rollback_is_outcome_unknown_too(self):
        self.staged()
        self.edit_state(status="rolling_back")
        rc, out, _ = self.deploy()
        self.assertEqual(rc, 3, out)
        self.assertEqual(self.rel().status, "outcome_unknown")
        self.assertEqual(self.lines(self.prod_marker), [])
        self.assertEqual(self.lines(self.rollback_marker), [])

    def test_an_approver_name_with_a_control_character_is_refused(self):
        self.staged()
        rc, out, _ = self.deploy(yes="Dana​Human", seen=False)
        self.assert_refused(rc, out, "--approved-by")


class UnattendedTests(DeployBase):
    def test_unattended_or_without_a_yes_waits_with_the_command_ready(self):
        _, commit = self.staged()
        self.set_prod("1.1.0\n")
        for extra, yes in ((["--unattended"], "Dana Human"), ([], None)):
            with self.subTest(extra=extra, yes=yes):
                rc, out, _ = self.deploy(*extra, yes=yes)
                self.assertEqual(rc, 3, out)
                lines = out.splitlines()
                self.assertIn("STOP: waiting-human", lines)
                self.assertIn("NEXT: run deploy with the human", lines)
                self.assertIn("DEPLOY: 1.2.0 %s" % commit, lines)
                self.assertIn("prod.marker production 1.2.0 %s" % commit, out)  # argv shown
                self.assertEqual(self.lines(self.prod_marker), [])
                rel = self.rel()
                self.assertEqual(rel.status, "awaiting_deploy")
                self.assertIsNone(rel.approved_by)
                # R36: the read-only probe ran, so the summary names the target
                self.assertEqual(rel.rollback_target["version"], "1.1.0")
                self.assertIn("rollback.marker 1.1.0", out)
                self.assertNotIn("target probed when", out)
        # from awaiting_deploy, the human's yes deploys
        rc, out, err = self.deploy()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(self.lines(self.prod_marker)), 1)
        self.assertEqual(self.rel().status, "deployed")


class RollbackTargetSeenTests(DeployBase):  # R36
    def test_the_waiting_summary_says_when_there_is_nothing_to_roll_back_to(self):
        self.staged()  # no production probe file and no earlier release
        rc, out, _ = self.deploy(yes=None)
        self.assertEqual(rc, 3, out)
        self.assertIn("no previous release, nothing to roll back to", out)
        self.assertEqual(self.rel().rollback_target["source"], "none")

    def test_a_yes_to_no_summary_waits_and_runs_nothing(self):
        self.staged()
        self.set_prod("1.1.0\n")
        rc, out, _ = self.deploy(seen=False)
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: waiting-human", out.splitlines())
        self.assertIn("has not seen this summary", out)
        self.assertEqual(self.lines(self.prod_marker), [])
        self.assertIsNone(self.rel().approved_by)

    def test_a_target_changed_since_the_summary_waits_again(self):
        self.staged()
        self.set_prod("1.1.0\n")
        self.assertEqual(self.deploy(yes=None)[0], 3)
        self.set_prod("1.0.9\n")  # production moved after the human saw the summary
        rc, out, _ = self.deploy(seen=False)
        self.assertEqual(rc, 3, out)
        self.assertIn("rollback target changed", out)
        self.assertIn("rollback.marker 1.0.9", out)
        self.assertEqual(self.lines(self.prod_marker), [])
        self.assertEqual(self.rel().rollback_target["version"], "1.0.9")
        rc, out, err = self.deploy(seen=False)  # the yes to the summary now shown
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(self.lines(self.prod_marker)), 1)

    def test_an_unchanged_target_deploys(self):
        self.staged()
        self.set_prod("1.1.0\n")
        self.assertEqual(self.deploy(yes=None)[0], 3)
        rc, out, err = self.deploy(seen=False)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(self.lines(self.prod_marker)), 1)
        self.assertEqual(self.rel().rollback_target["version"], "1.1.0")


class SummaryBindingTests(DeployBase):  # R42, R43, #11
    def test_a_summary_shown_only_unattended_does_not_bind_a_yes(self):
        self.staged()
        self.set_prod("1.1.0\n")
        self.assertEqual(self.deploy("--unattended", yes=None)[0], 3)
        self.assertFalse(self.rel().summary["shown_to_human"])
        rc, out, _ = self.deploy(seen=False)  # the yes arrives; no human saw that summary
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: waiting-human", out.splitlines())
        self.assertIn("has not seen this summary", out)
        self.assertEqual(self.lines(self.prod_marker), [])
        self.assertTrue(self.rel().summary["shown_to_human"])  # shown now, in this session
        rc, out, err = self.deploy(seen=False)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(self.lines(self.prod_marker)), 1)
        self.assertIsNone(self.rel().summary)  # consumed by the deploy it bound

    def test_a_reentry_session_counts_as_unattended(self):  # R43
        self.staged()
        self.set_prod("1.1.0\n")
        rc, out, _ = TS.run(["deploy", "--root", self.root], reentry="3")
        self.assertEqual(rc, 3, out)
        self.assertFalse(self.rel().summary["shown_to_human"])
        rc, out, _ = TS.run(["deploy", "--root", self.root, "--approved-by", "Dana Human"],
                            reentry="")  # set, even empty
        self.assertEqual(rc, 3, out)
        self.assertIn("FACTORY_CONDUCTOR_REENTRY", out)
        self.assertEqual(self.lines(self.prod_marker), [])
        self.assertIsNone(self.rel().approved_by)

    def test_a_summary_with_another_remote_waits_again(self):  # R42: tag mode
        _, commit = self.staged(ci="on:\n  push:\n    tags: ['v*']\n")
        other = tmpdir()
        subprocess.run(["git", "init", "-q", "--bare", other], check=True)
        TS.git(self.root, "remote", "add", "other", other)
        self.assertEqual(self.deploy(yes=None)[0], 3)  # the human saw: push to origin
        rc, out, _ = self.deploy("--remote", "other", seen=False)
        self.assertEqual(rc, 3, out)
        self.assertIn("the summary changed since the human saw it", out)
        self.assertIn("other", out)
        for bare in (self.bare, other):
            r = TS.git(bare, "rev-parse", "-q", "--verify", "refs/tags/v1.2.0")
            self.assertNotEqual(r.returncode, 0)
        rc, out, err = self.deploy("--remote", "other", seen=False)  # the yes to that one
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(TS.git(other, "rev-parse", "refs/tags/v1.2.0").stdout.strip(), commit)

    def test_deploy_refuses_once_the_release_grant_is_revoked(self):  # #11
        self.staged()
        self.set_prod("1.1.0\n")
        self.assertEqual(self.deploy(yes=None)[0], 3)  # shown, waiting
        CC.revoke_all(self.root)  # the kill switch
        for yes in (None, "Dana Human"):
            with self.subTest(yes=yes):
                rc, out, _ = self.deploy(yes=yes, seen=False)
                self.assert_refused(rc, out, "grant-revoked", status="awaiting_deploy")
                self.assertNotIn("waiting-human", out)


class RefusalTests(DeployBase):
    def test_a_release_not_yet_staged_is_refused(self):
        self.make()
        self.assertEqual(self.stage()[0], 0)  # built, waiting for the verifier
        rc, out, _ = self.deploy()
        self.assert_refused(rc, out, "not staged", status="staging_verify")

    def test_missing_evidence_or_evidence_for_another_commit_is_refused(self):
        _, commit = self.staged()
        good = self.rel().evidence
        self.edit_state(evidence=None)
        self.assert_refused(*self.deploy()[:2], "evidence")
        self.edit_state(evidence=dict(good, commit="0" * 40))
        self.assert_refused(*self.deploy()[:2], "evidence")

    def test_a_recipe_off_the_pinned_sha_is_refused(self):
        self.staged()
        self.edit_state(recipe_sha="0" * 64)
        self.assert_refused(*self.deploy()[:2], "recipe-changed")

    def test_an_artifact_changed_since_stage_is_refused(self):
        py = sys.executable
        self.staged(build=[py, "-c", TS.DIST, "{commit}"], artifact={"path": "dist/app.txt"})
        build = os.path.join(self.root, ".skill-contract", "releases", "1.2.0", "wt-build")
        with open(os.path.join(build, "dist", "app.txt"), "w") as f:
            f.write("tampered")
        self.assert_refused(*self.deploy()[:2], "artifact-altered")

    def test_a_build_checkout_changed_since_stage_is_refused(self):
        self.staged()
        build = os.path.join(self.root, ".skill-contract", "releases", "1.2.0", "wt-build")
        with open(os.path.join(build, "package.json"), "a") as f:
            f.write("\n")
        self.assert_refused(*self.deploy()[:2], "build-tree-changed")

    def test_a_covering_deploy_gate_is_a_bug_and_refused(self):
        self.staged()
        covered = {"status": "COVERED", "reason": None, "id": "x", "gate": "grant",
                   "path": None, "violations": []}
        with mock.patch.object(CC, "check_grant", return_value=covered):
            rc, out, _ = self.deploy()
        self.assert_refused(rc, out, "deploy-covered")

    def test_a_live_grant_whose_allowlist_exposes_production_is_refused(self):
        self.staged()
        for rules in ("Read,Bash", "Bash(*)", "Read,Bash(%s *)" % sys.executable):
            with self.subTest(rules=rules):
                gid = plant_grant(self.root, rules)
                self.assert_refused(*self.deploy()[:2], "allowlist-exposes-prod")
                CC.revoke_grant(self.root, gid)
        # the rollback argv counts too
        gid = plant_grant(self.root, "Bash(%s -c * %s *)" % (sys.executable,
                                                            self.rollback_marker))
        self.assert_refused(*self.deploy()[:2], "allowlist-exposes-prod")
        CC.revoke_grant(self.root, gid)
        # an allowlist that cannot reach production does not stop the deploy
        plant_grant(self.root, "Read,Edit,Bash(git *)")
        rc, out, err = self.deploy()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.rel().status, "deployed")

    def test_an_allowlist_reaching_release_py_itself_is_refused(self):  # R43
        self.staged()
        here = os.path.abspath(RL.__file__)
        for rules in ("Bash(python3 */release.py *)", "Bash(python3 %s deploy:*)" % here,
                      "Bash(python3 release.py *)", "Read,Bash(python3 * abandon *)",
                      'Bash(python3 "$SKILL_DIR/assets/release.py" rollback *)',
                      "Bash(python3.12 *)", "Bash(uv run *)",
                      "Bash(python3 ~/.claude/skills/release-conductor/assets/release.py *)"):
            with self.subTest(rules=rules):
                gid = plant_grant(self.root, rules)
                self.assert_refused(*self.deploy()[:2], "allowlist-exposes-prod")
                CC.revoke_grant(self.root, gid)
        # a python rule for another tool (factory-conductor's conductor.py) is not release.py
        plant_grant(self.root, "Bash(python3 /x/factory-conductor/assets/conductor.py *)")
        rc, out, err = self.deploy()
        self.assertEqual(rc, 0, out + err)

    def test_a_revoked_exposing_grant_does_not_count(self):
        self.staged()
        gid = plant_grant(self.root, "Bash")
        CC.revoke_grant(self.root, gid)
        self.assertEqual(self.deploy()[0], 0)

    def test_the_allowlist_refusal_comes_before_the_wait(self):
        self.staged()
        plant_grant(self.root, "Bash")
        rc, out, _ = self.deploy("--unattended")
        self.assert_refused(rc, out, "allowlist-exposes-prod")
        self.assertNotIn("waiting-human", out)

    def test_deploy_stops_when_the_run_lock_is_held(self):
        self.staged()
        with mock.patch.object(RL, "LOCK_TIMEOUT", 0.3), RL.run_lock(self.root):
            rc, out, _ = self.deploy()
        self.assertEqual(rc, 3)
        self.assertIn("STOP: locked", out)
        self.assertEqual(self.lines(self.prod_marker), [])


class LockedReadTests(DeployBase):
    def test_a_read_during_a_live_deploy_never_demotes_it(self):
        self.staged()
        self.edit_state(status="deploying")
        ready = os.path.join(tmpdir(), "ready")
        code = ("import os, sys, time; sys.path.insert(0, %r); import release as RL\n"
                "with RL.run_lock(%r):\n"
                "    open(%r, 'w').close()\n"
                "    time.sleep(60)\n"
                % (os.path.dirname(os.path.abspath(RL.__file__)), self.root, ready))
        holder = subprocess.Popen([sys.executable, "-c", code])
        try:
            deadline = time.monotonic() + 20
            while not os.path.exists(ready):
                self.assertLess(time.monotonic(), deadline, "the lock holder never started")
                time.sleep(0.05)
            self.assertEqual(self.rel().status, "deploying")
            rc, out, _ = TS.run(["status", "--root", self.root])
            self.assertEqual(rc, 0, out)
            self.assertIn("RELEASE: 1.2.0 deploying", out)
            self.assertEqual(self.rel().status, "deploying")
            with mock.patch.object(RL, "LOCK_TIMEOUT", 0.3):
                rc, out, _ = self.deploy()
            self.assertEqual(rc, 3)
            self.assertIn("STOP: locked", out)
            self.assertEqual(self.rel().status, "deploying")
            self.assertEqual(RL.unfinished_releases(self.root), [("1.2.0", "deploying")])
        finally:
            holder.send_signal(signal.SIGKILL)
            holder.wait()
        # once the holder is gone, the next locked command finds the crash
        rc, out, _ = self.deploy()
        self.assertEqual(rc, 3)
        self.assertEqual(self.rel().status, "outcome_unknown")
        self.assertEqual(self.lines(self.prod_marker), [])


class TagDeployTests(DeployBase):
    def tag_at(self, repo):
        r = TS.git(repo, "rev-parse", "-q", "--verify", "refs/tags/v1.2.0")
        return r.stdout.strip() if r.returncode == 0 else None

    def test_when_ci_deploys_on_tag_the_tag_is_pushed_only_under_the_yes(self):
        _, commit = self.staged(ci="on:\n  push:\n    tags: ['v*']\n")
        self.assertTrue(self.rel().tag_deploys)
        self.assertIsNone(self.tag_at(self.bare))
        rc, out, _ = self.deploy("--unattended")
        self.assertEqual(rc, 3, out)
        self.assertIsNone(self.tag_at(self.bare))
        self.assertEqual(self.rel().status, "awaiting_deploy")
        self.assertIn("refs/tags/v1.2.0", out)  # the push shown is the deploy
        rc, out, err = self.deploy()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.tag_at(self.bare), commit)
        self.assertIsNone(self.tag_at(self.root))  # never a local tag a later push could fire
        self.assertEqual(self.lines(self.prod_marker), [])  # the tag push IS the deploy
        rel = self.rel()
        self.assertEqual(rel.status, "deployed")
        self.assertEqual(rel.stage["tag"], "pushed")


    def push_mock(self, result):
        real = RL._run_remote

        def fake(argv, cwd, env):
            if argv[:2] == ["git", "push"]:
                return False, result
            return real(argv, cwd, env)
        return mock.patch.object(RL, "_run_remote", side_effect=fake)

    def test_a_tag_push_that_times_out_has_an_unknown_outcome(self):  # R25
        self.staged(ci="on:\n  push:\n    tags: ['v*']\n")
        with self.push_mock({"rc": None, "err_tail": "timed out", "timed_out": True}):
            rc, out, _ = self.deploy()
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: outcome-unknown", out)
        self.assertIn("NEXT: verify-prod then ask the human", out.splitlines())
        self.assertEqual(self.rel().status, "outcome_unknown")

    def test_a_tag_push_that_cannot_start_is_a_plain_failure(self):  # M2
        self.staged(ci="on:\n  push:\n    tags: ['v*']\n")
        with self.push_mock({"rc": None, "err_tail": "cannot run: no git",
                             "timed_out": False}):
            rc, out, _ = self.deploy()
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: deploy-failed: the tag push could not start", out)
        self.assertEqual(self.rel().status, "prod_failed")

    def test_a_tag_already_on_the_remote_at_the_commit_is_called_a_no_op(self):  # M7
        _, commit = self.staged(ci="on:\n  push:\n    tags: ['v*']\n")
        r = TS.git(self.root, "push", "-q", self.bare, "%s:refs/tags/v1.2.0" % commit)
        self.assertEqual(r.returncode, 0, r.stderr)
        rc, out, err = self.deploy()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("no-op", out)
        self.assertIn("CI may not run again", out)

    def test_an_allowlist_reaching_the_tag_push_refuses_in_tag_mode_only(self):  # R27
        self.staged(ci="on:\n  push:\n    tags: ['v*']\n")
        for rules in ("Read,Bash(git *)", "Bash(git push:*)", "Bash(git push origin v*)",
                      "Bash(git push origin refs/tags/*)"):
            with self.subTest(rules=rules):
                gid = plant_grant(self.root, rules)
                self.assert_refused(*self.deploy()[:2], "allowlist-exposes-prod")
                self.assertIsNone(self.tag_at(self.bare))
                CC.revoke_grant(self.root, gid)

    def test_a_git_allowlist_does_not_refuse_when_the_tag_is_not_the_deploy(self):  # R27
        self.staged()
        self.assertFalse(self.rel().tag_deploys)
        plant_grant(self.root, "Read,Bash(git *)")
        rc, out, err = self.deploy()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.rel().status, "deployed")


class StageAllowlistTests(DeployBase):
    def test_stage_in_tag_mode_refuses_an_allowlist_reaching_the_tag_push(self):  # R27
        _, commit = self.make(ci="on:\n  push:\n    tags: ['v*']\n")
        self.assertEqual(self.stage()[0], 0)
        self.record_all(commit)
        gid = plant_grant(self.root, "Bash(git push origin v*)")
        rc, out, _ = self.evidence()
        self.assertEqual(rc, 2, out)
        self.assertIn("allowlist-exposes-prod", out)
        self.assertEqual(self.lines(self.probe_file), ["1.1.0"])
        CC.revoke_grant(self.root, gid)
        self.assertEqual(self.evidence()[0], 0)

    def test_stage_refuses_before_deploy_staging_while_a_grant_exposes_production(self):
        _, commit = self.make()
        self.assertEqual(self.stage()[0], 0)
        self.record_all(commit)
        gid = plant_grant(self.root, "Read,Bash(%s *)" % sys.executable)
        rc, out, _ = self.evidence()
        self.assertEqual(rc, 2, out)
        self.assertIn("allowlist-exposes-prod", out)
        self.assertEqual(self.lines(self.probe_file), ["1.1.0"])  # deploy_staging never ran
        self.assertEqual(self.rel().status, "staging_verify")
        CC.revoke_grant(self.root, gid)
        rc, out, err = self.evidence()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.rel().status, "staged")


if __name__ == "__main__":
    unittest.main()
