"""Tests for `release verify-prod` and `release rollback` (Task 6), and the
release-result/v1 envelope they write. Stdlib only, offline: production is a file the
version probe `cat`s, health and prod_smoke are stub argv that append to marker files, and
rollback is a stub that writes the target version into the probed file. Nothing touches a
real target or a network."""
import hashlib
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release as RL  # noqa: E402
import contract_check as CC  # noqa: E402
import test_release_stage as TS  # noqa: E402  (modules: their tests are not collected here)
import test_release_deploy as TD  # noqa: E402

SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas",
                      "release-result.v1.json")
# Neither hex nor semver, so no probe parser can mistake it for a version or a commit.
TOKEN = "SECRET-TOKEN-zq9x"
# Prints the token on stdout and stderr, then appends "<args...>" to argv[1].
LOUD = ("import sys; print(%r); sys.stderr.write(%r); "
        "open(sys.argv[1], 'a').write(' '.join(sys.argv[2:]) + chr(10))" % (TOKEN, TOKEN))
# Writes argv[2] into the probed production file argv[1], and appends it to argv[3].
ROLL = ("import sys; open(sys.argv[1], 'w').write(sys.argv[2] + chr(10)); "
        "open(sys.argv[3], 'a').write(sys.argv[2] + chr(10))")


class ProdBase(TD.DeployBase):
    def setUp(self):
        super().setUp()
        py = sys.executable
        self.health_marker = os.path.join(self.d, "health.marker")
        self.smoke_marker = os.path.join(self.d, "smoke.marker")
        self.recipe.update(
            health=[py, "-c", LOUD, self.health_marker, "{env}"],
            prod_smoke=[[py, "-c", LOUD, self.smoke_marker, "smoke-a", "{env}"],
                        [py, "-c", LOUD, self.smoke_marker, "smoke-b", "{version}"]],
            rollback=[py, "-c", ROLL, self.prod_probe, "{version}", self.rollback_marker],
            deploy_timeout=1)

    def deployed(self, prod="1.1.0\n", **over):
        """A release deployed with the human's yes, production reporting `prod` (None:
        no probe file, so no rollback target). Returns (root, commit)."""
        root, commit = self.staged(**over)
        if prod is not None:
            self.set_prod(prod)
        rc, out, err = self.deploy()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.rel().status, "deployed")
        self.staging_checks_before = self.lines(self.check_marker)
        return root, commit

    def verify(self):
        with mock.patch.object(RL, "PROBE_INTERVAL", 0.05):
            return TS.run(["verify-prod", "--root", self.root])

    def rollback(self, *extra, yes="Dana Human"):
        argv = ["rollback", "--root", self.root]
        if yes is not None:
            argv += ["--approved-by", yes]
        with mock.patch.object(RL, "PROBE_INTERVAL", 0.05):
            return TS.run(argv + list(extra))

    def envelopes(self):
        d = CC.envelope_dir(self.root)
        out = []
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            with open(os.path.join(d, name), encoding="utf-8") as f:
                out.append((name, f.read()))
        return out

    def results(self):
        out = []
        for name, text in self.envelopes():
            st = json.loads(text)
            if st.get("predicateType") == RL.RESULT_KIND:
                out.append(st)
        return out

    def assert_result_valid(self, st):
        self.assertEqual(CC.check_statement(st), [])
        p = st["predicate"]["payload"]
        with open(SCHEMA, encoding="utf-8") as f:
            schema = json.load(f)
        self.assertFalse(schema["additionalProperties"])
        self.assertLessEqual(set(schema["required"]), set(p))
        self.assertLessEqual(set(p), set(schema["properties"]))
        # the log digest covers exactly the first log_bytes bytes of the release log
        with open(self.rel().log_path, "rb") as f:
            data = f.read()
        self.assertEqual(hashlib.sha256(data[:p["log_bytes"]]).hexdigest(), p["log_sha256"])
        names = {s["name"]: s["digest"]["sha256"] for s in st["subject"]}
        self.assertEqual(names[".skill-contract/releases/1.2.0/release-log.jsonl"],
                         p["log_sha256"])
        self.assertEqual(names[RL.RECIPE_PATH], p["recipe_sha"])
        self.assertIn(".skill-contract/releases/1.2.0/intent.json", names)
        return p

    def assert_no_tails(self):
        for name, text in self.envelopes():
            self.assertNotIn(TOKEN, text, name)


class VerifyPassTests(ProdBase):
    def test_an_asynchronous_switch_after_three_polls_passes_and_offers_no_rollback(self):
        root, commit = self.deployed()
        sleeps = []

        def fake_sleep(s):
            sleeps.append(s)
            if len(sleeps) == 2:
                self.set_prod("deployed %s\n" % commit[:12])
        with mock.patch.object(RL, "_sleep", side_effect=fake_sleep), \
                mock.patch.object(RL, "PROBE_INTERVAL", 0.05):
            rc, out, err = TS.run(["verify-prod", "--root", root])
        self.assertEqual(rc, 0, out + err)
        self.assertIn("PROD: 1.2.0 verified", out.splitlines())
        self.assertNotIn("roll back", out)
        self.assertNotIn("ROLLBACK", out)
        rel = self.rel()
        self.assertEqual(rel.status, "verified")
        self.assertEqual(rel.prod["probe"]["polls"], 3)
        self.assertEqual(len(sleeps), 2)
        self.assertEqual(self.lines(self.rollback_marker), [])
        # the release is finished: its grant is revoked
        self.assertIn("grant_revoked", [e["event"] for e in rel.events()])

    def test_prod_smoke_runs_only_its_own_argv_and_staging_checks_never_run(self):
        self.deployed()
        self.set_prod("1.2.0\n")
        rc, out, err = self.verify()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.lines(self.health_marker), ["production"])
        self.assertEqual(self.lines(self.smoke_marker), ["smoke-a production",
                                                         "smoke-b 1.2.0"])
        # the staging checks' marker is exactly what stage left: none ran against production
        self.assertEqual(self.lines(self.check_marker), self.staging_checks_before)
        self.assertEqual(self.lines(self.check_marker), ["staging"])

    def test_the_envelope_validates_and_its_log_digest_matches(self):
        root, commit = self.deployed()
        self.set_prod("1.2.0\n")
        self.assertEqual(self.verify()[0], 0)
        results = self.results()
        self.assertEqual(len(results), 1)
        p = self.assert_result_valid(results[0])
        self.assertEqual((p["version"], p["commit"], p["outcome"]), ("1.2.0", commit, "verified"))
        self.assertEqual(p["recipe_sha"], RL.recipe_sha(root, rev=commit))
        self.assertEqual(p["approved_by"], {"name": "Dana Human", "status": "CLAIMED"})
        self.assertEqual(p["rollback_target"]["version"], "1.1.0")
        self.assertEqual(p["staging"]["verifier"], TS.VERIFIER)
        prod = p["production"]
        self.assertEqual(prod["health"], {"rc": 0})
        self.assertEqual(prod["smoke"], [{"rc": 0}, {"rc": 0}])
        self.assertLessEqual(set(prod["probe"]), {"rc", "polls", "live"})
        # deploy reads what verify-prod wrote: production now runs this release
        self.assertEqual(RL._last_result_target(root, "9.9.9"),
                         {"version": "1.2.0", "commit": commit})

    def test_output_tails_never_reach_the_envelope(self):
        py = sys.executable
        self.deployed(prod="1.1.0 %s\n" % TOKEN,
                      deploy_prod=[py, "-c", LOUD, self.prod_marker, "{version}"])
        self.set_prod("1.2.0 %s\n" % TOKEN)
        rc, out, err = self.verify()
        self.assertEqual(rc, 0, out + err)
        # the token is in the local state (the tails), and in no envelope
        with open(self.rel().state_path) as f:
            self.assertIn(TOKEN, f.read())
        self.assertEqual(len(self.results()), 1)
        self.assert_no_tails()

    def test_an_outcome_unknown_release_is_resolved_by_verify_prod(self):
        _, commit = self.deployed()
        self.edit_state(status="deploying")  # the deploy's process died mid-flight
        self.set_prod("1.2.0\n")
        rc, out, err = self.verify()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.rel().status, "verified")
        self.assertEqual(len(self.lines(self.prod_marker)), 1)  # never re-run
        events = [e["event"] for e in self.rel().events()]
        self.assertIn("outcome_unknown", events)
        self.assertEqual(len(self.results()), 1)


class VerifyFailTests(ProdBase):
    def test_a_timeout_still_on_the_old_version_is_not_live(self):
        self.deployed()  # production still reports 1.1.0, the rollback target
        rc, out, _ = self.verify()
        self.assertEqual(rc, 3, out)
        self.assertIn("PROD: 1.2.0 fail not-live", out.splitlines())
        self.assertIn("NEXT: ask the human to roll back", out.splitlines())
        rel = self.rel()
        self.assertEqual(rel.status, "prod_failed")
        self.assertEqual(rel.prod["failure"]["reason"], "not-live")
        self.assertEqual(self.lines(self.health_marker), [])  # nothing after the probe
        self.assertEqual(self.lines(self.smoke_marker), [])
        self.assertEqual(self.results(), [])
        self.assertEqual(self.lines(self.rollback_marker), [])  # never automatic

    def test_a_third_version_is_wrong_version(self):
        self.deployed()
        self.set_prod("1.3.0\n")
        rc, out, _ = self.verify()
        self.assertEqual(rc, 3, out)
        self.assertIn("PROD: 1.2.0 fail wrong-version", out.splitlines())
        self.assertEqual(self.rel().prod["failure"]["reason"], "wrong-version")

    def test_a_failing_smoke_check_is_prod_failed(self):
        self.deployed(prod_smoke=[["true"], ["false"]])
        self.set_prod("1.2.0\n")
        rc, out, _ = self.verify()
        self.assertEqual(rc, 3, out)
        self.assertIn("PROD: 1.2.0 fail smoke-failed", out.splitlines())
        self.assertEqual(self.rel().status, "prod_failed")

    def test_a_verify_prod_failure_is_not_re_judged(self):
        self.deployed()
        self.set_prod("1.3.0\n")
        self.assertEqual(self.verify()[0], 3)
        self.set_prod("1.2.0\n")
        rc, out, _ = self.verify()  # a flaky check must not be retried into `verified`
        self.assertEqual(rc, 2, out)
        self.assertEqual(self.rel().status, "prod_failed")

    def test_a_staged_release_is_refused(self):
        self.staged()
        rc, out, _ = self.verify()
        self.assertEqual(rc, 2, out)
        self.assertEqual(self.lines(self.health_marker), [])


class RollbackTests(ProdBase):
    def failed(self):
        root, commit = self.deployed()
        self.set_prod("1.3.0\n")
        self.assertEqual(self.verify()[0], 3)
        self.assertEqual(self.rel().status, "prod_failed")
        return root, commit

    def test_rollback_without_a_yes_waits(self):
        self.failed()
        for extra, yes in ((["--unattended"], "Dana Human"), ([], None)):
            with self.subTest(extra=extra, yes=yes):
                rc, out, _ = self.rollback(*extra, yes=yes)
                self.assertEqual(rc, 3, out)
                self.assertIn("STOP: waiting-human", out.splitlines())
                self.assertIn("NEXT: run rollback with the human", out.splitlines())
                self.assertIn("rollback.marker", out)  # the argv the human says yes to
                self.assertEqual(self.lines(self.rollback_marker), [])
                self.assertEqual(self.rel().status, "prod_failed")

    def test_rollback_with_a_yes_runs_and_verifies_the_target(self):
        root, commit = self.failed()
        rc, out, err = self.rollback()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("ROLLBACK: 1.2.0 rolled-back 1.1.0", out.splitlines())
        self.assertEqual(self.lines(self.rollback_marker), ["1.1.0"])
        self.assertEqual(self.lines(self.prod_probe), ["1.1.0"])
        rel = self.rel()
        self.assertEqual(rel.status, "rolled_back")
        self.assertEqual(rel.rollback["approved_by"], {"name": "Dana Human",
                                                       "status": "CLAIMED"})
        events = [e["event"] for e in rel.events()]
        self.assertLess(events.index("rolling_back"), events.index("rollback_cmd"))
        self.assertIn(("deploy", "ASK"), self.gates())
        results = self.results()
        self.assertEqual(len(results), 1)
        p = self.assert_result_valid(results[0])
        self.assertEqual(p["outcome"], "rolled_back")
        self.assertEqual(p["rollback"]["approved_by"]["status"], "CLAIMED")
        self.assertEqual(RL._last_result_target(root, "9.9.9")["version"], "1.1.0")
        self.assertIn("grant_revoked", events)
        self.assert_no_tails()

    def test_a_rollback_that_never_lands_is_outcome_unknown_and_never_rerun(self):
        py = sys.executable
        self.deployed(rollback=[py, "-c", TD.MARK, self.rollback_marker, "{version}"])
        self.set_prod("1.3.0\n")
        self.assertEqual(self.verify()[0], 3)
        rc, out, _ = self.rollback()
        self.assertEqual(rc, 3, out)
        self.assertEqual(self.rel().status, "outcome_unknown")
        self.assertEqual(len(self.lines(self.rollback_marker)), 1)
        # a crash mid-rollback is demoted, never re-run, by the next locked command
        self.edit_state(status="rolling_back")
        rc, out, _ = self.rollback()
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: outcome-unknown", out)
        self.assertEqual(self.rel().status, "outcome_unknown")
        self.assertEqual(len(self.lines(self.rollback_marker)), 1)

    def test_rollback_with_no_target_exits_2(self):
        self.deployed(prod=None)  # no probe answer, no earlier release-result: no target
        self.assertEqual(self.rel().rollback_target["source"], "none")
        self.assertEqual(self.verify()[0], 3)
        rc, out, _ = self.rollback()
        self.assertEqual(rc, 2, out)
        self.assertIn("ROLLBACK: no target", out.splitlines())
        self.assertEqual(self.lines(self.rollback_marker), [])
        self.assertEqual(self.rel().status, "prod_failed")

    def test_rollback_of_a_deployed_release_is_refused(self):
        self.deployed()
        rc, out, _ = self.rollback()
        self.assertEqual(rc, 2, out)
        self.assertEqual(self.lines(self.rollback_marker), [])

    def test_a_covering_deploy_gate_refuses_the_rollback(self):
        self.failed()
        covered = {"status": "COVERED", "reason": None, "id": "x", "gate": "grant",
                   "path": None, "violations": []}
        with mock.patch.object(CC, "check_grant", return_value=covered):
            rc, out, _ = self.rollback()
        self.assertEqual(rc, 2, out)
        self.assertIn("deploy-covered", out)
        self.assertEqual(self.lines(self.rollback_marker), [])

    def test_an_allowlist_exposing_the_rollback_refuses_it(self):
        self.failed()
        gid = TD.plant_grant(self.root, "Bash(%s -c * %s 1.1.0 *)" % (sys.executable,
                                                                     self.prod_probe))
        rc, out, _ = self.rollback()
        self.assertEqual(rc, 2, out)
        self.assertIn("allowlist-exposes-prod", out)
        self.assertEqual(self.lines(self.rollback_marker), [])
        CC.revoke_grant(self.root, gid)
        self.assertEqual(self.rollback()[0], 0)


if __name__ == "__main__":
    unittest.main()
