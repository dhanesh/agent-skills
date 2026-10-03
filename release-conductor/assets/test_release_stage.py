"""Tests for `release stage` (Task 4). Stdlib only, offline: the remote is a local bare
repository, the PR command is stubbed, recipe commands are stub argv that write marker
files, the staging version probe `cat`s a file the test rewrites, and the staging checks
fetch from a local http.server in a thread. Nothing touches a real target or a network."""
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release as RL  # noqa: E402
import contract_check as CC  # noqa: E402
from release_testkit import repo, write_recipe, write_evidence, serve, GIT, tmpdir  # noqa: E402

VERIFY = ".claude/skills/verify-app"
DRIVER = "agent-1"
VERIFIER = "verifier-2"
# Inline programs for recipe argv. No `{` or `}` anywhere: a recipe argv may carry only
# the {version}/{commit}/{env} tokens, so dict literals and f-strings are out.
APPEND = "import sys; open(sys.argv[1], 'a').write(sys.argv[2] + chr(10))"
WRITE = "import sys; open(sys.argv[1], 'w').write(sys.argv[2])"
FETCH = "import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=5)"
DIST = ("import os, sys; os.makedirs('dist', exist_ok=True); "
        "open('dist/app.txt', 'w').write(sys.argv[1])")


def git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                          env=dict(os.environ, **GIT))


def run(argv):
    """(rc, stdout, stderr) of release.main(argv), with the git identity env set."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, GIT), contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(err):
        rc = RL.main(argv)
    return rc, out.getvalue(), err.getvalue()


def commit_file(root, rel, text, msg="change"):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    assert git(root, "add", "--", rel).returncode == 0
    r = git(root, "commit", "-q", "-m", msg)
    assert r.returncode == 0, r.stderr


class StageBase(unittest.TestCase):
    """One scenario per test: a repo whose recipe, verify skill and (optionally) CI
    config are committed on main before prep; prep pushes release/1.2.0 to a local bare
    origin; the human's merge is a --no-ff merge into main, with the root left on main."""

    def setUp(self):
        self.d = tmpdir()
        self.www = os.path.join(self.d, "www")
        os.makedirs(self.www)
        self.url = serve(self.www)
        self.probe_file = os.path.join(self.www, "version-staging.txt")
        with open(self.probe_file, "w") as f:
            f.write("1.1.0\n")
        self.build_marker = os.path.join(self.d, "build.marker")
        self.check_marker = os.path.join(self.d, "check.marker")
        py = sys.executable
        self.recipe = {
            "build": [py, "-c", APPEND, self.build_marker, "{commit}"],
            # the staging deploy goes live: the probe file (also served over http) gets
            # the version, so it doubles as the deploy's marker
            "deploy_staging": [py, "-c", WRITE, os.path.join(self.www, "version-{env}.txt"),
                               "{version}"],
            "deploy_prod": ["true"], "rollback": ["true", "{version}"], "health": ["true"],
            "version_probe": ["cat", os.path.join(self.www, "version-{env}.txt")],
            "staging_checks": [[py, "-c", FETCH, self.url + "/version-staging.txt"],
                               [py, "-c", APPEND, self.check_marker, "{env}"]],
            "prod_smoke": [["true"]], "version": {"file": "package.json", "key": "version"},
            "bump": "minor", "artifact": "rebuild", "deploy_timeout": 5,
            "verify_skill": VERIFY}

    def make(self, ci=None, policy=None, merge=True, **over):
        """Build the scenario; returns (root, merged release commit or None)."""
        root = self.root = repo()
        commit_file(root, VERIFY + "/SKILL.md", "---\nname: verify-app\n---\n")
        commit_file(root, VERIFY + "/features/login.md", "# Login\n\n- id: login\n- anchors: src\n")
        commit_file(root, VERIFY + "/features/search.md", "# Search\n\n- id: search\n")
        if ci is not None:
            commit_file(root, ".github/workflows/ci.yml", ci)
        write_recipe(root, dict(self.recipe, **over), commit=True)
        self.bare = tmpdir()
        subprocess.run(["git", "init", "-q", "--bare", self.bare], check=True)
        git(root, "remote", "add", "origin", self.bare)
        argv = ["prep", "--root", root, "--driver", DRIVER, "--approved-by", "Dana Human",
                "--pr-cmd", '["true"]']
        if policy:
            pol = os.path.join(self.d, "policy.json")
            with open(pol, "w") as f:
                json.dump(policy, f)
            argv += ["--policy-file", pol]
        rc, out, err = run(argv)
        self.assertEqual(rc, 0, out + err)
        if not merge:
            return root, None
        r = git(root, "merge", "-q", "--no-ff", "release/1.2.0", "-m", "Merge release 1.2.0")
        self.assertEqual(r.returncode, 0, r.stderr)
        return root, git(root, "rev-parse", "HEAD").stdout.strip()

    def stage(self, *extra):
        return run(["stage", "--root", self.root, *extra])

    def evidence(self, verifier=VERIFIER):
        return self.stage("--evidence", "--verifier", verifier)

    def record_all(self, sha, verifier=VERIFIER, result="pass"):
        for feat in ("login", "search"):
            write_evidence(self.root, sha, feat, verifier, result=result)

    def rel(self):
        return RL.Release.load(self.root, "1.2.0")

    def gates(self):
        return [(e["action"], e["status"]) for e in self.rel().events() if e["event"] == "gate"]

    def stage_wt(self):
        return os.path.join(self.root, ".skill-contract", "releases", "1.2.0", "wt-stage")

    def lines(self, path):
        try:
            with open(path) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []


class HappyPathTests(StageBase):
    def test_root_on_main_stage_worktree_gates_pass_and_the_tag_is_pushed(self):
        root, commit = self.make()
        rc, out, err = self.stage()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("NEXT: dispatch-verifier %s" % commit, out.splitlines())
        rel = self.rel()
        self.assertEqual(rel.status, "staging_verify")
        self.assertEqual(rel.release_commit, commit)
        # the --worktree wiring: the root sits on main, the gate judged the stage worktree
        self.assertEqual(git(root, "symbolic-ref", "--short", "HEAD").stdout.strip(), "main")
        wt = self.stage_wt()
        self.assertEqual(git(wt, "symbolic-ref", "--short", "HEAD").stdout.strip(),
                         "release/1.2.0-stage")
        self.assertEqual(git(wt, "rev-parse", "HEAD").stdout.strip(), commit)
        self.assertEqual(self.gates(), [("local_reversible", "COVERED")])
        # built once, from the release commit, nothing deployed before the evidence
        self.assertEqual(self.lines(self.build_marker), [commit])
        self.assertEqual(self.lines(self.probe_file), ["1.1.0"])
        # a re-run without --evidence only reprints the dispatch line, redoing nothing
        rc, out, _ = self.stage()
        self.assertEqual(rc, 0)
        self.assertIn("NEXT: dispatch-verifier %s" % commit, out)
        self.assertEqual(self.lines(self.build_marker), [commit])
        self.record_all(commit)
        rc, out, err = self.evidence()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("STAGE: 1.2.0 pass %s" % commit, out.splitlines())
        rel = self.rel()
        self.assertEqual(rel.status, "staged")
        self.assertEqual(rel.evidence["verifier"], VERIFIER)
        self.assertEqual(rel.evidence["features"], ["login", "search"])
        self.assertEqual(rel.evidence["commit"], commit)
        self.assertEqual(self.gates(), [("local_reversible", "COVERED"),
                                        ("local_reversible", "COVERED"),
                                        ("deploy_staging", "COVERED"),
                                        ("push_tag", "COVERED")])
        self.assertEqual(self.lines(self.probe_file), ["1.2.0"])
        self.assertEqual(self.lines(self.check_marker), ["staging"])  # {env} is "staging"
        checks = rel.stage["checks"]
        self.assertEqual([c["rc"] for c in checks], [0, 0])
        self.assertEqual(set(checks[0]), {"argv", "rc", "out_tail", "err_tail"})
        self.assertEqual(checks[1]["argv"][-1], "staging")
        self.assertFalse(rel.tag_deploys)
        self.assertEqual(git(self.bare, "rev-parse", "refs/tags/v1.2.0").stdout.strip(), commit)
        self.assertEqual(rel.recipe_sha, RL.recipe_sha(root, rev=commit))
        # a re-run of a staged release says so and redoes nothing
        rc, out, _ = self.stage()
        self.assertEqual(rc, 0)
        self.assertIn("STAGE: 1.2.0 pass %s" % commit, out)
        self.assertEqual(self.lines(self.build_marker), [commit])
        self.assertEqual(self.lines(self.check_marker), ["staging"])
        # the release state never shows in git status (worktrees, evidence aside)
        self.assertEqual(git(root, "status", "--porcelain", "--", ".skill-contract").stdout, "")

    def test_the_merged_commit_is_chosen_not_main_tip(self):
        # a later merge keeps the version at 1.2.0; the release commit is where it became so
        root, commit = self.make()
        commit_file(root, "later.txt", "x\n", "later work")
        self.assertNotEqual(git(root, "rev-parse", "HEAD").stdout.strip(), commit)
        rc, out, err = self.stage()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.rel().release_commit, commit)
        self.assertEqual(git(self.stage_wt(), "rev-parse", "HEAD").stdout.strip(), commit)

    def test_an_unmerged_release_stops(self):
        self.make(merge=False)
        rc, out, _ = self.stage()
        self.assertEqual(rc, 3)
        self.assertIn("STOP: not-merged", out)
        self.assertEqual(self.rel().status, "prepped")
        self.assertEqual(self.lines(self.build_marker), [])

    def test_an_unmerged_commit_named_by_commit_stops(self):
        root, _ = self.make(merge=False)
        rc, out, _ = self.stage("--commit", "release/1.2.0")  # version and recipe match
        self.assertEqual(rc, 3)
        self.assertIn("STOP: not-merged", out)
        self.assertIn("NEXT: merge the release PR and update local main", out)
        self.assertEqual(self.rel().status, "prepped")
        self.assertEqual(self.lines(self.build_marker), [])
        self.assertFalse(os.path.exists(self.stage_wt()))

    def test_an_artifact_path_is_hashed_at_build(self):
        root, commit = self.make(build=[sys.executable, "-c", DIST, "{version}"],
                                 artifact={"path": "dist/app.txt"})
        rc, out, err = self.stage()
        self.assertEqual(rc, 0, out + err)
        rel = self.rel()
        import hashlib
        self.assertEqual(rel.artifact_sha, hashlib.sha256(b"1.2.0").hexdigest())
        self.assertEqual(rel.stage["artifact"]["path"], "dist/app.txt")
        # the build ran in its own checkout, not the stage worktree the verifier uses
        self.assertFalse(os.path.exists(os.path.join(self.stage_wt(), "dist")))
        self.assertTrue(os.path.isfile(os.path.join(rel.stage["build_dir"], "dist", "app.txt")))

    def test_an_artifact_altered_after_build_fails_the_stage(self):
        root, commit = self.make(build=[sys.executable, "-c", DIST, "{version}"],
                                 artifact={"path": "dist/app.txt"})
        self.assertEqual(self.stage()[0], 0)
        with open(os.path.join(self.rel().stage["build_dir"], "dist", "app.txt"), "w") as f:
            f.write("tampered")
        self.record_all(commit)
        rc, out, _ = self.evidence()
        self.assertEqual(rc, 3)
        self.assertEqual(self.rel().status, "stage_failed")
        self.assertIn("STOP: artifact-altered", out)
        self.assertEqual(self.lines(self.probe_file), ["1.1.0"])  # never deployed


class RefusalTests(StageBase):
    def test_a_version_mismatch_refuses(self):
        root, commit = self.make()
        base = self.rel().base_commit  # package.json says 1.1.0 there
        rc, out, _ = self.stage("--commit", base)
        self.assertEqual(rc, 3)
        self.assertIn("STOP: version-mismatch", out)
        self.assertEqual(self.rel().status, "prepped")
        self.assertEqual(self.lines(self.build_marker), [])
        self.assertFalse(os.path.exists(self.stage_wt()))

    def test_a_recipe_edited_in_a_later_commit_never_runs(self):
        # The release commit carries an edited deploy_staging; a later commit on main
        # restores the original bytes, so the root's working recipe matches the grant
        # (the gate alone would say COVERED). stage compares the recipe AT the commit.
        root, _ = self.make(merge=False)
        evil = os.path.join(self.d, "evil.marker")
        git(root, "checkout", "-q", "release/1.2.0")
        write_recipe(root, dict(self.recipe, deploy_staging=[sys.executable, "-c", APPEND,
                                                             evil, "{version}"]), commit=True)
        git(root, "checkout", "-q", "main")
        r = git(root, "merge", "-q", "--no-ff", "release/1.2.0", "-m", "Merge release 1.2.0")
        self.assertEqual(r.returncode, 0, r.stderr)
        commit = git(root, "rev-parse", "HEAD").stdout.strip()
        write_recipe(root, self.recipe, commit=True)  # main's working recipe matches again
        rep = CC.check_grant(root, "local_reversible", subject=RL.RECIPE_PATH)
        self.assertNotEqual(rep["reason"], "stale")
        rc, out, _ = self.stage()
        self.assertEqual(rc, 3)
        self.assertIn("STOP: recipe-changed", out)
        rc, out, _ = self.stage("--commit", commit)
        self.assertEqual(rc, 3)
        self.assertIn("STOP: recipe-changed", out)
        self.assertEqual(self.rel().status, "prepped")
        self.assertFalse(os.path.exists(evil))
        self.assertEqual(self.lines(self.build_marker), [])

    def test_a_build_failure_is_stage_failed(self):
        self.make(build=["false"])
        rc, out, _ = self.stage()
        self.assertEqual(rc, 3)
        self.assertIn("STOP: build-failed", out)
        rel = self.rel()
        self.assertEqual(rel.status, "stage_failed")
        self.assertEqual(rel.stage["failure"]["reason"], "build-failed")
        self.assertNotIn("dispatch-verifier", out)
        # R21: the failed release's grant no longer covers anything
        rep = CC.check_grant(self.root, "deploy_staging",
                             path=RL._grant_path(self.root, rel.grant["id"]),
                             subject=RL.RECIPE_PATH, worktree=self.stage_wt())
        # named by path (as stage's gates do), the revocation supersedes the grant
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "superseded"))
        rep = CC.check_grant(self.root, "deploy_staging", subject=RL.RECIPE_PATH,
                             worktree=self.stage_wt())
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "revoked"))
        # M7: a stage_failed release is finished: stage refuses it, naming why and what next
        rc, out, _ = self.stage()
        self.assertEqual(rc, 2)
        self.assertIn("stage_failed (build-failed)", out)
        self.assertIn("a new prep with a human is needed", out)

    def test_an_inherited_GIT_DIR_never_reaches_a_recipe_command(self):  # R22
        seen = os.path.join(self.d, "gitdir.marker")
        self.make(build=[sys.executable, "-c",
                         "import os, sys; open(sys.argv[1], 'w').write("
                         "str('GIT_DIR' in os.environ))", seen])
        with mock.patch.dict(os.environ, {"GIT_DIR": os.path.join(self.d, "elsewhere")}):
            rc, out, err = self.stage()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.lines(seen), ["False"])

    def test_an_uncommitted_root_recipe_edit_stops_at_the_first_gate(self):  # M8
        root, _ = self.make()
        write_recipe(root, dict(self.recipe, bump="patch"))  # working tree only
        rc, out, _ = self.stage()
        self.assertEqual(rc, 3)
        self.assertIn("GATE: local_reversible ASK stale", out)
        self.assertEqual(self.lines(self.build_marker), [])
        self.assertEqual(self.rel().status, "prepped")

    def test_a_base_commit_recipe_off_the_grant_stops(self):  # M8
        self.make()
        path = RL._grant_path(self.root, self.rel().grant["id"])
        with open(path) as f:
            st = json.load(f)
        for s in st["subject"]:
            if s["name"] == RL.RECIPE_PATH:
                s["digest"]["sha256"] = "0" * 64
        with open(path, "w") as f:
            json.dump(st, f)
        rc, out, _ = self.stage()
        self.assertEqual(rc, 3)
        self.assertIn("STOP: recipe-changed: the recipe at the base commit", out)
        self.assertEqual(self.lines(self.build_marker), [])

    def test_an_unreadable_base_recipe_stops_without_a_traceback(self):  # M4
        self.make()
        real = RL.recipe_sha

        def flaky(root, rev=None):
            if rev == self.rel().base_commit:
                raise ValueError("git show failed: boom")
            return real(root, rev)
        with mock.patch.object(RL, "recipe_sha", side_effect=flaky):
            rc, out, _ = self.stage()
        self.assertEqual(rc, 3)
        self.assertIn("STOP: recipe-unreadable", out)
        self.assertEqual(self.lines(self.build_marker), [])

    def test_an_unreadable_grant_stops_and_asks_for_a_new_prep(self):  # M7
        self.make()
        with open(RL._grant_path(self.root, self.rel().grant["id"]), "w") as f:
            f.write("{not json")
        rc, out, _ = self.stage()
        self.assertEqual(rc, 3)
        self.assertIn("STOP: grant-unreadable", out)
        self.assertIn("NEXT: a new prep with a human is needed", out)
        self.assertEqual(self.lines(self.build_marker), [])

    def test_the_kill_switch_stops_stage(self):
        self.make()
        CC.revoke_all(self.root)
        rc, out, _ = self.stage()
        self.assertEqual(rc, 3)
        self.assertIn("GATE: local_reversible ASK", out)
        self.assertIn("STOP:", out)
        self.assertEqual(self.rel().status, "prepped")
        self.assertEqual(self.lines(self.build_marker), [])

    def test_stage_stops_when_the_run_lock_is_held(self):
        self.make()
        with mock.patch.object(RL, "LOCK_TIMEOUT", 0.3), RL.run_lock(self.root):
            rc, out, _ = self.stage()
        self.assertEqual(rc, 3)
        self.assertIn("STOP: locked", out)

    def test_evidence_before_build_or_without_a_verifier_is_refused(self):
        self.make()
        self.assertEqual(self.evidence()[0], 2)  # nothing built yet: still prepped
        self.assertEqual(self.stage()[0], 0)
        self.assertEqual(self.stage("--evidence")[0], 2)


class EvidenceTests(StageBase):
    def built(self):
        root, commit = self.make()
        self.assertEqual(self.stage()[0], 0)
        return root, commit

    def assert_rejected(self, verifier, reason, commit):
        rc, out, _ = self.evidence(verifier)
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: evidence-reject %s" % reason, out)
        self.assertIn("NEXT: dispatch-verifier %s" % commit, out)
        rel = self.rel()
        self.assertEqual(rel.status, "staging_verify")
        self.assertIsNone(rel.evidence)
        self.assertEqual(self.lines(self.probe_file), ["1.1.0"])  # nothing deployed

    def test_missing_evidence_refuses(self):
        _, commit = self.built()
        self.assert_rejected(VERIFIER, "evidence-missing", commit)

    def test_one_feature_unrecorded_refuses(self):
        root, commit = self.built()
        write_evidence(root, commit, "login", VERIFIER)
        self.assert_rejected(VERIFIER, "evidence-missing", commit)

    def test_the_driver_cannot_verify_its_own_release(self):
        root, commit = self.built()
        self.record_all(commit, verifier=DRIVER)
        self.assert_rejected(DRIVER, "driver-is-verifier", commit)

    def test_foreign_evidence_refuses(self):
        root, commit = self.built()
        self.record_all(commit, verifier="someone-else")
        self.assert_rejected(VERIFIER, "evidence-verifier-mismatch", commit)

    def test_evidence_for_another_commit_refuses(self):
        root, commit = self.built()
        self.record_all(self.rel().base_commit)
        self.assert_rejected(VERIFIER, "evidence-stale-sha", commit)

    def test_a_red_doctor_refuses(self):
        root, commit = self.built()
        for feat in ("login", "search"):
            write_evidence(root, commit, feat, VERIFIER, doctor_ok=False)
        self.assert_rejected(VERIFIER, "doctor-red", commit)

    def test_a_zero_width_variant_of_the_driver_is_refused(self):  # M5
        root, commit = self.built()
        self.record_all(commit, verifier=DRIVER + "\u200b")
        rc, out, _ = self.evidence(DRIVER + "\u200b")
        self.assertEqual(rc, 2)
        self.assertIn("--verifier", out)
        self.assertIsNone(self.rel().evidence)
        self.assertEqual(self.lines(self.probe_file), ["1.1.0"])

    def test_a_deleted_stage_worktree_is_recreated_on_resume(self):  # M3
        import shutil
        root, commit = self.built()
        shutil.rmtree(self.stage_wt())
        self.record_all(commit)
        rc, out, err = self.evidence()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("STAGE: 1.2.0 pass %s" % commit, out)
        self.assertEqual(git(self.stage_wt(), "rev-parse", "HEAD").stdout.strip(), commit)

    def test_a_commit_in_the_stage_worktree_stops_the_next_gate(self):
        root, commit = self.built()
        commit_file(self.stage_wt(), "verifier-scratch.txt", "x\n", "verifier commit")
        self.record_all(commit)
        rc, out, _ = self.evidence()
        self.assertEqual(rc, 3)
        self.assertIn("STOP: head-moved", out)
        self.assertEqual(self.rel().status, "staging_verify")
        self.assertIsNone(self.rel().evidence)

    def test_failing_evidence_is_stage_failed(self):
        root, commit = self.built()
        self.record_all(commit, result="fail")
        rc, out, _ = self.evidence()
        self.assertEqual(rc, 3)
        self.assertIn("STOP: evidence-failed", out)
        self.assertEqual(self.rel().status, "stage_failed")


class StagingTests(StageBase):
    def go(self, **over):
        root, commit = self.make(**over)
        self.assertEqual(self.stage()[0], 0)
        self.record_all(commit)
        return root, commit

    def test_a_probe_that_switches_after_two_polls_passes(self):
        _, commit = self.go(deploy_staging=["true"])  # an asynchronous deploy
        sleeps = []

        def fake_sleep(s):
            sleeps.append(s)
            if len(sleeps) == 2:
                with open(self.probe_file, "w") as f:
                    f.write("deployed %s\n" % commit[:12])
        with mock.patch.object(RL, "_sleep", side_effect=fake_sleep):
            rc, out, err = self.evidence()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(sleeps), 2)
        self.assertEqual(sleeps[0], RL.PROBE_INTERVAL)
        rel = self.rel()
        self.assertEqual(rel.status, "staged")
        self.assertEqual(rel.stage["probe"]["polls"], 3)

    def test_a_probe_that_never_switches_times_out(self):
        self.go(deploy_staging=["true"], deploy_timeout=1)
        with mock.patch.object(RL, "PROBE_INTERVAL", 0.05):
            rc, out, _ = self.evidence()
        self.assertEqual(rc, 3)
        self.assertIn("STOP: timeout", out)
        rel = self.rel()
        self.assertEqual(rel.status, "stage_failed")
        self.assertEqual(rel.stage["failure"]["reason"], "timeout")
        self.assertEqual(self.lines(self.check_marker), [])  # no checks after a timeout
        self.assertNotEqual(git(self.bare, "rev-parse", "-q", "--verify",
                                "refs/tags/v1.2.0").returncode, 0)

    def test_a_failing_staging_check_is_stage_failed(self):
        py = sys.executable
        self.go(staging_checks=[[py, "-c", FETCH, self.url + "/version-staging.txt"],
                                [py, "-c", FETCH, self.url + "/missing"],
                                [py, "-c", APPEND, self.check_marker, "{env}"]])
        rc, out, _ = self.evidence()
        self.assertEqual(rc, 3)
        self.assertIn("STOP: check-failed", out)
        rel = self.rel()
        self.assertEqual(rel.status, "stage_failed")
        self.assertEqual([c["rc"] for c in rel.stage["checks"]], [0, 1])
        self.assertIn("404", rel.stage["checks"][1]["err_tail"])
        self.assertEqual(self.lines(self.check_marker), [])  # stops at the first failure
        self.assertNotEqual(git(self.bare, "rev-parse", "-q", "--verify",
                                "refs/tags/v1.2.0").returncode, 0)
        # tails stay in the local state, never in the log
        for e in rel.events():
            self.assertNotIn("err_tail", json.dumps(e))

    def test_a_failing_staging_deploy_is_stage_failed(self):
        self.go(deploy_staging=["false"])
        rc, out, _ = self.evidence()
        self.assertEqual(rc, 3)
        self.assertIn("STOP: deploy-failed", out)
        self.assertEqual(self.rel().status, "stage_failed")

    def test_ci_tag_trigger_holds_the_tag_for_deploy(self):
        _, commit = self.go(ci="on:\n  push:\n    tags: ['v*']\n")
        rc, out, err = self.evidence()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("STAGE: 1.2.0 pass %s" % commit, out)
        rel = self.rel()
        self.assertTrue(rel.tag_deploys)
        self.assertEqual(rel.stage["tag"], "held")
        self.assertNotIn("push_tag", [a for a, _ in self.gates()])
        self.assertNotEqual(git(self.bare, "rev-parse", "-q", "--verify",
                                "refs/tags/v1.2.0").returncode, 0)
        self.assertNotEqual(git(self.root, "rev-parse", "-q", "--verify",
                                "refs/tags/v1.2.0").returncode, 0)

    def test_ci_without_tag_triggers_pushes_the_tag_after_its_gate(self):
        _, commit = self.go(ci="on:\n  pull_request:\n    branches: [main]\n")
        rc, out, err = self.evidence()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("GATE: push_tag COVERED", out)
        self.assertFalse(self.rel().tag_deploys)
        self.assertEqual(git(self.bare, "rev-parse", "refs/tags/v1.2.0").stdout.strip(), commit)

    def test_unreadable_ci_counts_as_tag_triggered(self):  # R1
        self.go()
        with mock.patch.object(CC, "ci_tag_triggers", return_value=None):
            rc, out, err = self.evidence()
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(self.rel().tag_deploys)
        self.assertNotEqual(git(self.bare, "rev-parse", "-q", "--verify",
                                "refs/tags/v1.2.0").returncode, 0)

    def test_a_declined_staging_deploy_asks_then_resumes(self):  # R18
        _, commit = self.go(policy={"deploy_staging": "ask"})
        rc, out, _ = self.evidence()
        self.assertEqual(rc, 3)
        self.assertIn("GATE: deploy_staging ASK gate-ask", out)
        self.assertIn("STOP:", out)
        rel = self.rel()
        self.assertEqual(rel.status, "staging_verify")
        self.assertEqual(rel.evidence["verifier"], VERIFIER)  # kept: not judged again
        self.assertEqual(self.lines(self.probe_file), ["1.1.0"])
        # the human lifts the decline in the grant itself
        path = RL._grant_path(self.root, rel.grant["id"])
        with open(path) as f:
            st = json.load(f)
        st["predicate"]["payload"]["gate_policy"]["deploy_staging"] = "grant"
        with open(path, "w") as f:
            json.dump(st, f)
        rc, out, err = self.stage()  # plain stage resumes; no --evidence needed
        self.assertEqual(rc, 0, out + err)
        self.assertIn("STAGE: 1.2.0 pass %s" % commit, out)
        self.assertEqual(self.lines(self.build_marker), [commit])  # never rebuilt

    def test_a_resume_after_an_interrupted_staging_deploy_says_so(self):  # M1
        _, commit = self.go(policy={"deploy_staging": "ask"})
        self.assertEqual(self.evidence()[0], 3)  # evidence passed, the deploy gate asked
        rel = self.rel()
        self.assertFalse(rel.stage.get("deploy_started"))  # never started behind a gate
        rel.stage["deploy_started"] = True  # an attempt that died before recording its end
        rel.save()
        path = RL._grant_path(self.root, rel.grant["id"])
        with open(path) as f:
            st = json.load(f)
        st["predicate"]["payload"]["gate_policy"]["deploy_staging"] = "grant"
        with open(path, "w") as f:
            json.dump(st, f)
        rc, out, err = self.stage()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("STAGE: 1.2.0 re-running deploy_staging after an interrupted attempt",
                      out)
        events = [e["event"] for e in self.rel().events()]
        self.assertIn("deploy_staging_rerun", events)
        self.assertLess(events.index("deploy_staging_started"), events.index("deploy_staging"))

    def test_a_first_deploy_does_not_claim_a_rerun(self):  # M1
        self.go()
        rc, out, _ = self.evidence()
        self.assertEqual(rc, 0)
        self.assertNotIn("re-running", out)
        self.assertTrue(self.rel().stage["deploy_started"])

    def test_a_tracked_edit_in_the_build_checkout_fails_the_stage(self):  # M2
        self.go()
        with open(os.path.join(self.rel().stage["build_dir"], "package.json"), "a") as f:
            f.write("\n")
        rc, out, _ = self.evidence()
        self.assertEqual(rc, 3)
        self.assertIn("STOP: build-tree-changed", out)
        self.assertEqual(self.rel().status, "stage_failed")
        self.assertEqual(self.lines(self.probe_file), ["1.1.0"])

    def test_a_commit_in_the_build_checkout_fails_the_stage(self):  # M2
        self.go()
        commit_file(self.rel().stage["build_dir"], "x.txt", "x\n", "sneaky")
        rc, out, _ = self.evidence()
        self.assertEqual(rc, 3)
        self.assertIn("STOP: build-tree-changed", out)
        self.assertEqual(self.lines(self.probe_file), ["1.1.0"])

    def test_untracked_build_output_does_not_count_as_a_change(self):  # M2
        self.go(build=[sys.executable, "-c", DIST, "{version}"])
        rc, out, err = self.evidence()
        self.assertEqual(rc, 0, out + err)

    def test_a_tag_already_at_another_commit_stops(self):
        root, commit = self.go()
        git(self.root, "push", "-q", "origin", "%s:refs/tags/v1.2.0" % self.rel().base_commit)
        rc, out, _ = self.evidence()
        self.assertEqual(rc, 3)
        self.assertIn("STOP: tag-push-failed", out)
        rel = self.rel()
        self.assertEqual(rel.status, "staging_verify")  # staging passed; the tag is pending
        self.assertIsNone(rel.stage.get("tag"))


class ProbeMatchTests(unittest.TestCase):
    C = "0123456789abcdef0123456789abcdef01234567"

    def test_the_version_or_the_commit_counts(self):
        for out in ("1.2.0\n", "v1.2.0", '{"version": "1.2.0"}', "version=1.2.0.",
                    self.C, "build " + self.C[:7], self.C.upper()[:12]):
            with self.subTest(out=out):
                self.assertTrue(RL.probe_reports(out, "1.2.0", self.C))

    def test_near_misses_do_not(self):
        for out in ("", "1.1.0", "1.2.0-rc.1", "11.2.0", "1.2.01", "1.2.0+build",
                    "v1.2.0.1", self.C[:6], "f" + self.C[:7]):
            with self.subTest(out=out):
                self.assertFalse(RL.probe_reports(out, "1.2.0", self.C))


if __name__ == "__main__":
    unittest.main()
