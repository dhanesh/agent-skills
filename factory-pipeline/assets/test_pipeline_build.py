#!/usr/bin/env python3
"""The build stage of `pipeline tick` (R18): `conductor init` for a grant that the tick, or
an earlier tick, derived and that no conductor run names yet. The evidence gate is the
plan's own `verification` block: a gated plan is built only when the repository has the
verify skill it names, and a plan with no block only when the repository has no verify
skill. One build per tick, and only while no conductor run in the root is unfinished; the
other grants wait, and a later tick builds them. A dispatches cap skips build. A planner
that edits factory-conductor's files poisons the tick (S5), so no conductor call runs.

The conductor is a stub (pipeline_testkit.CONDUCTOR_STUB) that logs its argv, PATH and
interpreter flags. No real conductor, timer or agent runs: the tick gets the timer's
dry-run hooks (FACTORY_CONDUCTOR_TIMER_DRYRUN, FACTORY_CONDUCTOR_TIMER_HOME) on top.
Stdlib unittest; offline, deterministic; scratch only in temp dirs.
Run:  python3 test_pipeline_build.py
"""
import datetime
import json
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


def utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class BuildFixture(unittest.TestCase):
    verify = False
    caps = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pipeline-build-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo, self.skills, self.pid = K.build_repo(self.tmp, verify=self.verify,
                                                        caps=self.caps)

    def tick(self):
        env = dict(os.environ, FACTORY_CONDUCTOR_TIMER_DRYRUN="1",
                   FACTORY_CONDUCTOR_TIMER_HOME=os.path.join(self.tmp, "timer-home"))
        cp = subprocess.run([sys.executable, TOOL, "--root", self.repo,
                             "--skills-dir", self.skills, "tick"],
                            capture_output=True, text=True, timeout=180,
                            stdin=subprocess.DEVNULL, env=env)
        res = {ln.split()[1]: ln.split()[2:] for ln in cp.stdout.splitlines()
               if ln.startswith("STAGE: ")}
        return cp, res

    def inits(self):
        return [c for c in K.conductor_calls(self.skills) if c["argv"][0] == "init"]

    def finish(self, run):
        path = os.path.join(self.repo, ".skill-contract", "runs", run, "state.json")
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        st["finished"] = {"envelope": "x"}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(st, f)

    def plan_of(self, gid):
        with open(os.path.join(self.repo, ".skill-contract", "envelopes", gid + ".json"),
                  encoding="utf-8") as f:
            names = [s["name"] for s in json.load(f)["subject"]]
        return os.path.join(self.repo, *[n for n in names if "envelopes" in n][0].split("/"))


class TestGatedInit(BuildFixture):
    verify = True

    def test_verify_skill_gives_the_gated_init(self):
        gid = K.built_grant(self.repo, self.pid, gated=True)
        cp, res = self.tick()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(res["build"], ["ok", "built=1", "waiting=0", "gate=on"], cp.stdout)
        [call] = self.inits()
        self.assertEqual(call["argv"], ["init", "--plan", self.plan_of(gid),
                                        "--root", self.repo])
        self.assertEqual(call["gate"], "on")
        self.assertEqual(call["grant"], gid)
        self.assertIn("BUILD: started %s run=%s gate=on" % (gid, call["run"]), cp.stdout)
        # S5: the conductor runs through the pinned interpreter, isolated and without site.
        self.assertEqual(call["exe"], P.PYTHON)
        self.assertEqual((call["isolated"], call["no_site"]), (1, 1))

    def test_plan_without_block_waits_when_the_repo_has_a_verify_skill(self):
        gid = K.built_grant(self.repo, self.pid, gated=False)
        cp, res = self.tick()
        self.assertEqual(res["build"], ["attention", "built=0", "waiting=1"], cp.stdout)
        self.assertIn("BUILD: waiting %s evidence-gate-off" % gid, cp.stdout)
        self.assertEqual(self.inits(), [])

    def test_reentry_block_installs_the_timer_after_init(self):
        K.built_grant(self.repo, self.pid, gated=True, reentry=True)
        cp, res = self.tick()
        self.assertEqual(res["build"][:2], ["ok", "built=1"], cp.stdout)
        self.assertIn("reentry=ok", res["build"])
        argvs = [c["argv"] for c in K.conductor_calls(self.skills)]
        self.assertEqual([a[:2] for a in argvs], [["init", "--plan"], ["reentry", "install"]])
        self.assertEqual(argvs[1], ["reentry", "install", "--root", self.repo])


class TestPlainInit(BuildFixture):
    verify = False

    def test_no_verify_skill_gives_the_plain_init(self):
        gid = K.built_grant(self.repo, self.pid, gated=False)
        cp, res = self.tick()
        self.assertEqual(res["build"], ["ok", "built=1", "waiting=0", "gate=off"], cp.stdout)
        [call] = self.inits()
        self.assertEqual(call["gate"], "off")
        self.assertEqual(call["argv"][2], self.plan_of(gid))
        # No reentry block in the grant: no timer.
        self.assertEqual(len(K.conductor_calls(self.skills)), 1)

    def test_gated_plan_waits_when_its_verify_skill_is_missing(self):
        gid = K.built_grant(self.repo, self.pid, gated=True)
        cp, res = self.tick()
        self.assertIn("BUILD: waiting %s verify-skill-missing" % gid, cp.stdout)
        self.assertEqual(self.inits(), [])


class TestOneBuildPerTick(BuildFixture):
    def test_earlier_grant_is_built_once_and_never_again(self):
        gid = K.built_grant(self.repo, self.pid, gated=False)  # left by an earlier tick
        cp, res = self.tick()
        self.assertEqual(res["build"][:2], ["ok", "built=1"], cp.stdout)
        [call] = self.inits()
        self.assertEqual(call["grant"], gid)
        # Unfinished: the next tick does not init it again (its run names it).
        cp, res = self.tick()
        self.assertEqual(res["build"], ["idle", "grants=0"], cp.stdout)
        self.finish(call["run"])
        cp, res = self.tick()
        self.assertEqual(res["build"], ["idle", "grants=0"], cp.stdout)
        self.assertEqual(len(self.inits()), 1)

    def test_two_grants_one_built_the_other_waits_then_builds(self):
        first = K.built_grant(self.repo, self.pid, gated=False, tag="a")
        second = K.built_grant(self.repo, self.pid, gated=False, tag="b",
                               now=datetime.datetime.now(datetime.timezone.utc)
                               + datetime.timedelta(seconds=2))
        self.assertLess(first, second)
        cp, res = self.tick()
        self.assertEqual(res["build"], ["ok", "built=1", "waiting=1", "gate=off"], cp.stdout)
        [call] = self.inits()
        self.assertEqual(call["grant"], first)  # the oldest first
        self.assertIn("BUILD: waiting %s unfinished-run=%s" % (second, call["run"]),
                      cp.stdout)
        # While the first run is unfinished, the second keeps waiting.
        cp, res = self.tick()
        self.assertEqual(res["build"], ["attention", "built=0", "waiting=1"], cp.stdout)
        self.assertEqual(len(self.inits()), 1)
        self.finish(call["run"])
        cp, res = self.tick()
        self.assertEqual(res["build"], ["ok", "built=1", "waiting=0", "gate=off"], cp.stdout)
        self.assertEqual([c["grant"] for c in self.inits()], [first, second])

    def test_revoked_derived_grant_is_not_built(self):
        first = K.built_grant(self.repo, self.pid, gated=False, tag="a")
        second = K.built_grant(self.repo, self.pid, gated=False, tag="b",
                               now=datetime.datetime.now(datetime.timezone.utc)
                               + datetime.timedelta(seconds=2))
        sys.path.insert(0, K.PLANNING_ASSETS)
        import contract_check  # noqa: E402
        contract_check.revoke_grant(self.repo, first)
        cp, res = self.tick()
        self.assertIn("BUILD: waiting %s grant-revoked" % first, cp.stdout)
        self.assertEqual([c["grant"] for c in self.inits()], [second], cp.stdout)

    def test_failed_init_waits_and_a_later_tick_retries(self):
        gid = K.built_grant(self.repo, self.pid, gated=False)
        K.behave(self.skills, "factory-conductor", "init", exit=2)
        cp, res = self.tick()
        self.assertEqual(res["build"], ["refused", "built=0", "waiting=1", "init", "exit=2"],
                         cp.stdout)
        self.assertIn("BUILD: waiting %s init-exit=2" % gid, cp.stdout)
        os.remove(os.path.join(self.skills, "behave", "factory-conductor.init.json"))
        cp, res = self.tick()
        self.assertEqual(res["build"][:2], ["ok", "built=1"], cp.stdout)


class TestRootChecks(BuildFixture):
    def test_dirty_root_waits(self):
        gid = K.built_grant(self.repo, self.pid, gated=False)
        K.write_rel(self.repo, "notes.txt", "x\n")
        cp, res = self.tick()
        self.assertIn("BUILD: waiting %s root-dirty" % gid, cp.stdout)
        self.assertEqual(self.inits(), [])

    def test_default_branch_waits(self):
        gid = K.built_grant(self.repo, self.pid, gated=False)
        K.git_quiet(self.repo, "checkout", "-q", "-b", "main")
        cp, res = self.tick()
        self.assertIn("BUILD: waiting %s grant-default-branch" % gid, cp.stdout)
        self.assertEqual(self.inits(), [])
        # The stage never moves the owner's branch.
        head = subprocess.run(["git", "-C", self.repo, "symbolic-ref", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=60).stdout.strip()
        self.assertEqual(head, "main")


class TestDispatchCap(BuildFixture):
    caps = {"max_dispatches_per_day": 1}

    def test_dispatch_cap_stop_skips_build(self):
        done = K.built_grant(self.repo, self.pid, gated=False, tag="a")
        run = K.conductor_run(self.repo, done, dispatch_at=[utc()])
        self.finish(os.path.basename(run))
        gid = K.built_grant(self.repo, self.pid, gated=False, tag="b")
        cp, res = self.tick()
        self.assertIn("STOP: cap max_dispatches_per_day count=1 limit=1", cp.stdout)
        self.assertEqual(res["build"], ["skipped", "cap=max_dispatches_per_day"])
        self.assertEqual(self.inits(), [])
        self.assertNotEqual(gid, done)


class TestConductorTamper(unittest.TestCase):
    """S5: a planner that edits factory-conductor's conductor.py poisons the tick before
    any conductor call (STOP: planner-tamper tools; build skipped)."""

    def test_planner_edit_of_the_conductor_skips_build(self):
        tmp = tempfile.mkdtemp(prefix="pipeline-build-")
        self.addCleanup(shutil.rmtree, tmp, True)
        agent = os.path.join(tmp, "agent")
        argv = K.planner(agent, "good")
        repo, skills, pid = K.build_repo(tmp, verify=False, planner=argv)
        conductor = os.path.join(skills, "factory-conductor", "assets", "conductor.py")
        with open(os.path.join(agent, "extra.py"), "w", encoding="utf-8") as f:
            f.write("with open(%r, 'a') as g:\n    g.write('# planted\\n')\n" % conductor)
        K.built_grant(repo, pid, gated=False)  # an earlier tick's grant, ready to build
        K.picks(repo, skills, ["I0123456789"])
        cp = subprocess.run([sys.executable, TOOL, "--root", repo, "--skills-dir", skills,
                             "tick"], capture_output=True, text=True, timeout=180,
                            stdin=subprocess.DEVNULL)
        self.assertEqual(cp.returncode, 4, cp.stdout + cp.stderr)
        self.assertIn("STOP: planner-tamper tools", cp.stdout)
        self.assertIn("STAGE: build skipped planner-tamper=tools", cp.stdout)
        self.assertEqual(K.conductor_calls(skills), [])


class TestSealedBuild(unittest.TestCase):
    """A plan that the tick plans and derives itself is built in the same tick, through the
    private PATH that the first planner sealed (the real plan stage always gates)."""

    def test_plan_derived_this_tick_is_built_gated_with_the_private_path(self):
        tmp = tempfile.mkdtemp(prefix="pipeline-build-")
        self.addCleanup(shutil.rmtree, tmp, True)
        argv = K.planner(os.path.join(tmp, "agent"), "good")
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        K.verify_skill(repo, "verify-export")  # the verify skill the test spec names
        repo, skills, pid = K.build_repo(tmp, planner=argv, real=("under-pipeline",))
        K.picks(repo, skills, ["I0123456789"])
        cp = subprocess.run([sys.executable, TOOL, "--root", repo, "--skills-dir", skills,
                             "tick"], capture_output=True, text=True, timeout=180,
                            stdin=subprocess.DEVNULL)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        res = {ln.split()[1]: ln.split()[2:] for ln in cp.stdout.splitlines()
               if ln.startswith("STAGE: ")}
        self.assertEqual(res["derive-grant"][:2], ["ok", "derived=1"], cp.stdout)
        self.assertEqual(res["build"], ["ok", "built=1", "waiting=0", "gate=on"], cp.stdout)
        [call] = K.conductor_calls(skills)
        self.assertEqual(call["gate"], "on")
        path = call["path"]
        self.assertTrue(os.path.basename(path).startswith("factory-pipeline-path-"), path)
        self.assertNotIn(os.pathsep, path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
