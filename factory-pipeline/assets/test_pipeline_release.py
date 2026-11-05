#!/usr/bin/env python3
"""The release stage of `pipeline tick` (R19, D19): release-conductor's `release stage` for
the release that `release prep` prepared, only after the owner merged its release pull
request and while a live release grant covers it, and at most once per release. While the
pull request is open or no live grant covers the release, the stage runs nothing and adds
a waiting entry for the digest (`release-merge`, `release-grant`). The tick never writes
or accepts a release grant and runs no other release command (B1). A planner that edits
release-conductor's release.py poisons the tick before the stage runs (S5).

The fixture runs the REAL `release prep` (its PR command is `true`, its origin a local bare
repository), so the release state, the release grant and the release branch are real. The
tick finds a stub release-conductor (pipeline_testkit.release_stub): the real release.py
with its command line replaced, so the tick's probe asks the real merged-check and grant
check, and `stage` only logs and records the release commit. No real stage, deploy,
network or timer runs. Stdlib unittest; offline, deterministic; scratch only in temp dirs.
Run:  python3 test_pipeline_release.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline as P  # noqa: E402
import pipeline_testkit as K  # noqa: E402

TOOL = os.path.join(HERE, "pipeline.py")


def stages(out):
    return {ln.split()[1]: ln.split()[2:] for ln in out.splitlines() if ln.startswith("STAGE: ")}


class ReleaseFixture(unittest.TestCase):
    planner_mode = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pipeline-release-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.agent = os.path.join(self.tmp, "agent")
        argv = K.planner(self.agent, self.planner_mode) if self.planner_mode else None
        self.repo, self.skills, self.gid = K.release_fixture(self.tmp, planner=argv)

    def tick(self):
        cp = subprocess.run([sys.executable, TOOL, "--root", self.repo,
                             "--skills-dir", self.skills, "tick"],
                            capture_output=True, text=True, timeout=180,
                            stdin=subprocess.DEVNULL)
        return cp, stages(cp.stdout)

    def bare_tick(self):
        """A Tick for an in-process stage call, with the tamper baseline a real tick takes."""
        tick = P.Tick(self.repo, [self.skills])
        tick.baseline = P.tool_digest(tick)
        return tick

    def stage_calls(self):
        return [c for c in K.release_calls(self.skills) if c["argv"][:1] == ["stage"]]

    def assertOnlyStage(self):
        """B1: the tick never calls deploy, verify-prod, rollback, abandon, prep or init."""
        for call in K.release_calls(self.skills):
            self.assertEqual(call["argv"], ["stage", "--root", self.repo, "--unattended"])

    def revoke(self):
        cp = subprocess.run([sys.executable, K.CHECKER, "revoke-grant", "--root", self.repo,
                             "--id", self.gid], capture_output=True, text=True, timeout=60)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)


class TestOpenPullRequest(ReleaseFixture):
    def test_open_pull_request_runs_no_stage_and_waits_for_the_merge(self):
        cp, res = self.tick()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(res["release-stage"], ["waiting", "reason=release-merge"], cp.stdout)
        self.assertIn("RELEASE: waiting 1.2.0 release-merge not-merged", cp.stdout)
        self.assertEqual(K.release_calls(self.skills), [])
        self.assertEqual(K.release_status(self.repo)["status"], "prepped")
        self.assertIsNone(K.release_status(self.repo)["stage"])

    def test_waiting_entry_names_the_gate_and_the_next_command(self):
        tick = self.bare_tick()
        self.assertEqual(P.stage_release_stage(tick), ("waiting", "reason=release-merge"))
        [(gate, what, nxt)] = tick.waiting
        self.assertEqual((gate, what), ("release-merge", "release 1.2.0"))
        self.assertIn("merge the release PR and update local main (git pull)", nxt)
        self.assertIn("stage --root", nxt)
        self.assertEqual(K.release_calls(self.skills), [])


class TestMergedRelease(ReleaseFixture):
    def test_merged_and_granted_runs_stage_once_and_never_again(self):
        cp, res = self.tick()  # the pull request is open
        self.assertEqual(res["release-stage"][0], "waiting", cp.stdout)
        self.assertEqual(self.stage_calls(), [])
        commit = K.release_merge(self.repo)
        cp, res = self.tick()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(res["release-stage"], ["ok", "staged=1", "release=1.2.0"], cp.stdout)
        self.assertIn("RELEASE: staged 1.2.0 commit=%s" % commit, cp.stdout)
        [call] = self.stage_calls()
        self.assertEqual(call["argv"], ["stage", "--root", self.repo, "--unattended"])
        # S5: release-conductor runs through the pinned interpreter, isolated, no site.
        self.assertEqual(call["exe"], P.PYTHON)
        self.assertEqual((call["isolated"], call["no_site"]), (1, 1))
        state = K.release_status(self.repo)
        self.assertEqual((state["status"], state["release_commit"]), ("staging_verify", commit))
        # The next ticks see release-conductor's own record and run nothing.
        for _ in range(2):
            cp, res = self.tick()
            self.assertEqual(res["release-stage"],
                             ["idle", "release=1.2.0", "status=staging_verify"], cp.stdout)
        self.assertEqual(len(self.stage_calls()), 1)
        self.assertOnlyStage()

    def test_merged_but_stale_grant_runs_no_stage(self):
        """No live grant: the recipe changed after the owner accepted the release grant,
        so release-conductor's check-grant answers `stale`."""
        K.release_merge(self.repo)
        with open(os.path.join(self.repo, ".release", "recipe.json"), "a",
                  encoding="utf-8") as f:
            f.write("\n")
        cp, res = self.tick()
        self.assertEqual(res["release-stage"], ["waiting", "reason=release-grant"], cp.stdout)
        self.assertIn("RELEASE: waiting 1.2.0 release-grant grant-stale", cp.stdout)
        self.assertEqual(K.release_calls(self.skills), [])

    def test_revoked_grant_runs_no_stage(self):
        K.release_merge(self.repo)
        self.revoke()
        cp, res = self.tick()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(res["release-stage"], ["waiting", "reason=release-grant"], cp.stdout)
        self.assertIn("RELEASE: waiting 1.2.0 release-grant grant-", cp.stdout)
        self.assertEqual(K.release_calls(self.skills), [])
        self.assertEqual(K.release_status(self.repo)["status"], "prepped")

    def test_revoked_grant_and_open_pull_request_wait_on_both_gates(self):
        self.revoke()
        tick = self.bare_tick()
        word, detail = P.stage_release_stage(tick)
        self.assertEqual((word, detail), ("waiting", "reason=release-merge,release-grant"))
        self.assertEqual([w[0] for w in tick.waiting], ["release-merge", "release-grant"])
        self.assertIn("prep --root", tick.waiting[1][2])
        self.assertEqual(K.release_calls(self.skills), [])

    def test_no_release_is_idle(self):
        shutil.rmtree(os.path.join(self.repo, ".skill-contract", "releases"))
        cp, res = self.tick()
        self.assertEqual(res["release-stage"], ["idle"], cp.stdout)
        self.assertEqual(K.release_calls(self.skills), [])

    def test_missing_release_conductor_is_unavailable_with_a_release(self):
        shutil.rmtree(os.path.join(self.skills, "release-conductor"))
        K.release_merge(self.repo)
        cp, res = self.tick()
        self.assertEqual(res["release-stage"], ["unavailable", "release-conductor"], cp.stdout)


class TestReleaseTamper(ReleaseFixture):
    """S5: a planner that edits the fixture release.py poisons the tick before release
    stage runs: STOP: planner-tamper tools, release-stage skipped, no release call."""
    planner_mode = "good"

    def test_planner_edit_of_release_py_skips_release_stage(self):
        release = os.path.join(self.skills, "release-conductor", "assets", "release.py")
        with open(os.path.join(self.agent, "extra.py"), "w", encoding="utf-8") as f:
            f.write("with open(%r, 'a') as g:\n    g.write('# planted\\n')\n" % release)
        K.release_merge(self.repo)
        K.picks(self.repo, self.skills, ["I0123456789"])
        cp, res = self.tick()
        self.assertEqual(cp.returncode, 4, cp.stdout + cp.stderr)
        self.assertIn("STOP: planner-tamper tools", cp.stdout)
        self.assertIn("release-conductor/assets/release.py", cp.stdout)
        self.assertEqual(res["release-stage"], ["skipped", "planner-tamper=tools"], cp.stdout)
        self.assertEqual(K.release_calls(self.skills), [])
        # The poison persists: the next tick runs no stage at all.
        cp, res = self.tick()
        self.assertEqual(cp.returncode, 4, cp.stdout + cp.stderr)
        self.assertIn("STOP: planner-tamper persisted tools", cp.stdout)
        self.assertEqual(res, {})
        self.assertEqual(K.release_calls(self.skills), [])
        self.assertEqual(K.release_status(self.repo)["status"], "prepped")


class TestSealedTick(ReleaseFixture):
    """A tick in which a planner ran gives its tools only the private PATH, which cannot
    run the owner's recipe commands, so it defers the stage to the next tick."""
    planner_mode = "good"

    def test_planner_tick_defers_and_the_next_tick_stages(self):
        K.release_merge(self.repo)
        K.picks(self.repo, self.skills, ["I0123456789"])
        cp, res = self.tick()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(res["plan"][:2], ["ok", "ready=1"], cp.stdout)
        self.assertEqual(res["release-stage"],
                         ["idle", "deferred=planner-ran", "release=1.2.0"], cp.stdout)
        self.assertEqual(K.release_calls(self.skills), [])
        K.behave(self.skills, "ops-intake", "autopick", stdout="AUTOPICK: matched 0 picked 0\n")
        cp, res = self.tick()
        self.assertEqual(res["release-stage"], ["ok", "staged=1", "release=1.2.0"], cp.stdout)
        self.assertEqual(len(self.stage_calls()), 1)
        self.assertOnlyStage()


if __name__ == "__main__":
    unittest.main(verbosity=2)
