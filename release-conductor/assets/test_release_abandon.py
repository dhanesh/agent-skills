"""Tests for `release abandon` (final review, ruling R40): the one sanctioned way to end a
release that can go nowhere else. Each stuck case the review found -- a first release
whose production deploy failed with nothing to roll back to, a release PR the human
closed, a kill switch mid-release, a human who declines to ship -- ends with abandon, and
then a new prep proceeds. Stdlib only, offline: the same fixtures as the stage, deploy and
prod suites (a local bare origin, stub recipe argv, a local http.server)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release as RL  # noqa: E402
import contract_check as CC  # noqa: E402
import test_release_stage as TS  # noqa: E402  (modules: their tests are not collected here)
import test_release_prod as TP  # noqa: E402

YES = ("--approved-by", "Dana Human")


class AbandonBase(TP.ProdBase):
    def abandon(self, *extra, reason="the human ended it", reentry=None):
        argv = ["abandon", "--root", self.root, *extra]
        if reason is not None:
            argv += ["--reason", reason]
        return TS.run(argv, reentry=reentry)

    def prep(self):
        return TS.run(["prep", "--root", self.root, "--driver", TS.DRIVER, *YES,
                       "--pr-cmd", '["true"]'])

    def grant_live(self, gid):
        rep = CC.check_grant(self.root, "local_reversible", path=RL._grant_path(self.root, gid))
        return rep["reason"] not in ("revoked", "superseded")

    def branch(self, name):
        return TS.git(self.root, "rev-parse", "-q", "--verify", "refs/heads/%s" % name)

    def assert_abandoned(self, out, was):
        rel = self.rel()
        self.assertEqual(rel.status, "abandoned")
        self.assertEqual(rel.abandoned["was"], was)
        self.assertEqual(rel.abandoned["approved_by"], {"name": "Dana Human",
                                                        "status": "CLAIMED"})
        self.assertIn("RELEASE: 1.2.0 abandoned", out)
        self.assertFalse(self.grant_live(rel.grant["id"]))
        for wt in (RL.PREP_WT, RL.STAGE_WT, RL.BUILD_WT, RL.PROD_WT):
            self.assertFalse(os.path.lexists(os.path.join(rel.dir, wt)), wt)
        for b in ("release/1.2.0", "release/1.2.0-stage"):
            self.assertNotEqual(self.branch(b).returncode, 0, b)
        self.assertIn("abandoned", [e["event"] for e in rel.events()])
        self.assertEqual(RL.unfinished_releases(self.root), [])


class StuckCaseTests(AbandonBase):
    def test_a_first_release_whose_deploy_failed_with_no_target(self):
        self.staged(deploy_prod=["false"])  # no production probe file: no rollback target
        rc, out, _ = self.deploy()
        self.assertEqual(rc, 3, out)
        self.assertEqual(self.rel().status, "prod_failed")
        self.assertEqual(self.rel().rollback_target["source"], "none")
        self.assertEqual(self.verify()[0], 3)  # judged once: still not live
        self.assertEqual(self.rollback()[0], 2)  # nothing to roll back to
        self.assertEqual(self.deploy()[0], 2)
        self.assertEqual(self.prep()[0], 2)  # the stuck release blocks every prep
        rollback_before = self.lines(self.rollback_marker)
        rc, out, err = self.abandon(*YES)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("production is the human's to handle", out)
        self.assert_abandoned(out, "prod_failed")
        self.assertEqual(self.lines(self.rollback_marker), rollback_before)  # never runs one
        # the tag stage pushed is left alone: abandon never touches tags
        self.assertEqual(TS.git(self.bare, "rev-parse", "-q", "--verify",
                                "refs/tags/v1.2.0").returncode, 0)
        rc, out, err = self.prep()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("RELEASE: 1.3.0 prepped", out)

    def test_a_release_pr_the_human_closed(self):
        self.make(merge=False)
        rc, out, _ = self.stage()
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: not-merged", out)
        rc, out, _ = self.prep()  # prep only says it is already prepped
        self.assertIn("already prepped", out)
        rc, out, err = self.abandon(*YES)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("release/1.2.0", out)  # the remote branch and PR are the human's
        self.assert_abandoned(out, "prepped")
        # the human deletes the remote branch with the PR; the same version can be prepped
        TS.git(self.bare, "branch", "-D", "release/1.2.0")
        rc, out, err = self.prep()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("RELEASE: 1.2.0 prepped", out)
        self.assertEqual(self.rel().status, "prepped")
        # the abandoned record survives, archived beside the new release
        d = os.path.join(self.root, ".skill-contract", "releases")
        kept = [n for n in os.listdir(d) if n.startswith("1.2.0.abandoned-")]
        self.assertEqual(len(kept), 1, os.listdir(d))
        self.assertEqual(RL.Release.load(self.root, kept[0]).status, "abandoned")

    def test_a_kill_switch_mid_release(self):
        _, commit = self.make()
        self.assertEqual(self.stage()[0], 0)
        self.record_all(commit)
        CC.revoke_all(self.root)
        rc, out, _ = self.evidence()
        self.assertEqual(rc, 3, out)  # every gate now asks; nothing can re-grant it
        rc, out, err = self.abandon(*YES)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("already revoked", out)
        self.assert_abandoned(out, "staging_verify")
        self.assertEqual(self.lines(self.probe_file), ["1.1.0"])  # staging never deployed
        rc, out, err = self.prep()
        self.assertEqual(rc, 0, out + err)

    def test_a_human_who_declines_to_ship(self):
        self.staged()
        self.set_prod("1.1.0\n")
        self.assertEqual(self.deploy(yes=None)[0], 3)
        self.assertEqual(self.rel().status, "awaiting_deploy")
        rc, out, err = self.abandon(*YES, reason="not this week")
        self.assertEqual(rc, 0, out + err)
        self.assert_abandoned(out, "awaiting_deploy")
        self.assertEqual(self.rel().abandoned["reason"], "not this week")
        self.assertEqual(self.lines(self.prod_marker), [])
        rc, out, err = self.prep()
        self.assertEqual(rc, 0, out + err)
        # a finished release is not abandoned again, and nothing deploys it
        self.assertEqual(self.abandon(*YES, "--version", "1.2.0")[0], 2)


class AbandonSafetyTests(AbandonBase):
    def test_a_crash_mid_deploy_is_demoted_first_and_never_rerun(self):
        self.staged()
        self.edit_state(status="deploying")
        rc, out, _ = self.abandon(*YES)
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: outcome-unknown", out)
        self.assertEqual(self.rel().status, "outcome_unknown")
        rc, out, err = self.abandon(*YES)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("production is the human's to handle", out)
        self.assert_abandoned(out, "outcome_unknown")
        self.assertEqual(self.lines(self.prod_marker), [])
        self.assertEqual(self.lines(self.rollback_marker), [])

    def test_abandon_is_human_only(self):
        self.staged()
        for extra, env, reason in ((["--unattended"], None, "x"), ([], "4", "x"),
                                   ([], "", "x"), ([], None, " "), ([], None, "a‮b")):
            with self.subTest(extra=extra, env=env, reason=reason):
                rc, out, _ = self.abandon(*YES, *extra, reason=reason, reentry=env)
                self.assertEqual(rc, 2, out)
                self.assertEqual(self.rel().status, "staged")
        rc, out, _ = self.abandon("--approved-by", "Dana​Human")
        self.assertEqual(rc, 2, out)
        self.assertEqual(self.abandon()[0], 2)  # no --approved-by at all
        self.assertEqual(self.rel().status, "staged")
        self.assertTrue(self.grant_live(self.rel().grant["id"]))

    def test_nothing_unfinished_is_refused(self):
        self.root = TS.repo()
        rc, out, _ = self.abandon(*YES)
        self.assertEqual(rc, 2, out)

    def test_a_held_lock_stops_abandon(self):
        self.staged()
        with TP.mock.patch.object(RL, "LOCK_TIMEOUT", 0.3), RL.run_lock(self.root):
            rc, out, _ = self.abandon(*YES)
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: locked", out)
        self.assertEqual(self.rel().status, "staged")


if __name__ == "__main__":
    unittest.main()
