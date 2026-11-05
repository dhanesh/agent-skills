#!/usr/bin/env python3
"""`pipeline tick` (R12): nine STAGE: lines in order, exit 0, in a fixture repo with stub
sibling skills and a live pipeline grant. R13: with no live pipeline grant (none, revoked,
revoked by the kill switch, expired, superseded, tracked by git), the tick runs no stage,
prints `STOP: no-grant`, and exits 2. Stdlib unittest; offline, deterministic; scratch only
in temp dirs.
Run:  python3 test_pipeline_tick.py
"""
import ast
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
ORDER = ["sweep", "growth-review", "idea-follow-up", "autopick", "plan", "derive-grant",
         "build", "release-stage", "digest"]
PENDING = {"idea-follow-up": "pending-R20", "plan": "pending-R17", "build": "pending-R18",
           "release-stage": "pending-R19", "digest": "pending-R21"}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pipeline-tick-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.skills = K.stubs(os.path.join(self.tmp, "skills"))
        self.grant = None

    def write_grant(self, **kw):
        self.grant = K.pipeline_grant(self.repo, **kw)
        return os.path.basename(self.grant)[:-len(".json")]

    def checker(self, *args):
        tool = os.path.join(self.skills, "spec-first-planning", "assets", "contract_check.py")
        return subprocess.run([sys.executable, tool] + list(args), capture_output=True,
                              text=True, timeout=60, stdin=subprocess.DEVNULL)

    def tick(self, *extra):
        cp = subprocess.run([sys.executable, TOOL, "--root", self.repo,
                             "--skills-dir", self.skills, "tick"] + list(extra),
                            capture_output=True, text=True, timeout=120,
                            stdin=subprocess.DEVNULL)
        stages = [ln.split()[1:] for ln in cp.stdout.splitlines() if ln.startswith("STAGE: ")]
        return cp, {s[0]: s[1:] for s in stages}, [s[0] for s in stages]


class TestTick(Fixture):
    def setUp(self):
        super().setUp()
        self.gid = self.write_grant()

    def test_nine_stage_lines_in_order_and_exit_0(self):
        cp, res, order = self.tick()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(order, ORDER)
        self.assertTrue(cp.stdout.splitlines()[-1].startswith("TICK: done stages=9"))
        self.assertIn("TICK: grant %s expires_at " % self.gid, cp.stdout)

    def test_built_stages_run_their_tools(self):
        cp, res, _ = self.tick()
        self.assertEqual(res["sweep"][0], "ok")
        self.assertEqual(res["growth-review"][:2], ["ok", "signals=1"])
        self.assertEqual(res["autopick"][:2], ["ok", "picked=1"])
        self.assertEqual(res["derive-grant"][0], "idle")
        keys = [(c["skill"], c["key"]) for c in K.calls(self.skills)]
        # R15: a cap check before autopick, plan and derive-grant; build has no grant to
        # build, so it checks nothing.
        caps = ("spec-first-planning", "pipeline-caps")
        self.assertEqual(keys, [("ops-intake", "sweep"), ("growth-review", "auto"),
                                ("ops-intake", "import"), caps, ("ops-intake", "autopick"),
                                caps, caps])

    def test_unbuilt_stages_say_pending_never_ok(self):
        _, res, _ = self.tick()
        for stage, word in PENDING.items():
            self.assertEqual(res[stage][0], word, stage)
        self.assertEqual(res["plan"][1], "picked=1")

    def test_growth_bytes_reach_intake_import_on_stdin(self):
        self.tick()
        imp = [c for c in K.calls(self.skills) if c["key"] == "import"][0]
        self.assertEqual(imp["stdin"], K.SIGNAL + "\n")
        self.assertEqual(imp["argv"], ["--root", self.repo, "import", "--format",
                                       "intake-signals-jsonl", "--source", "growth", "-"])
        auto = [c for c in K.calls(self.skills) if c["key"] == "auto"][0]
        self.assertEqual(auto["argv"], ["--root", self.repo, "auto", "--source", "growth"])

    def test_growth_source_flag(self):
        self.tick("--growth-source", "metrics")
        imp = [c for c in K.calls(self.skills) if c["key"] == "import"][0]
        self.assertIn("metrics", imp["argv"])

    def test_growth_skip_imports_nothing(self):
        K.behave(self.skills, "growth-review", "auto", stderr="SKIP: export unchanged\n")
        cp, res, _ = self.tick()
        self.assertEqual(res["growth-review"], ["ok", "signals=0"])
        self.assertNotIn("import", [c["key"] for c in K.calls(self.skills)])
        self.assertIn("growth-review| SKIP: export unchanged", cp.stdout)

    def test_growth_refusal_imports_nothing(self):
        K.behave(self.skills, "growth-review", "auto", stderr="STOP: no plan\n", exit=2)
        cp, res, _ = self.tick()
        self.assertEqual(res["growth-review"][:2], ["refused", "exit=2"])
        self.assertNotIn("import", [c["key"] for c in K.calls(self.skills)])
        self.assertEqual(cp.returncode, 0)

    def test_growth_owner_needed_still_imports_breaches(self):
        K.behave(self.skills, "growth-review", "auto", stdout=K.SIGNAL + "\n", exit=3)
        _, res, _ = self.tick()
        self.assertEqual(res["growth-review"][:3], ["attention", "exit=3", "signals=1"])
        self.assertIn("import", [c["key"] for c in K.calls(self.skills)])

    def test_failing_stage_records_result_and_tick_goes_on(self):
        K.behave(self.skills, "ops-intake", "sweep", stdout="SWEEP: 2 commands ok 1 failed 1\n",
                 exit=3)
        K.behave(self.skills, "ops-intake", "autopick",
                 stdout="STOP: log-unreadable: line 4\n", exit=2)
        cp, res, order = self.tick()
        self.assertEqual(cp.returncode, 0)
        self.assertEqual(order, ORDER)
        self.assertEqual(res["sweep"][:2], ["attention", "exit=3"])
        self.assertEqual(res["autopick"][:3], ["refused", "exit=2", "picked=0"])
        # A tool's STOP: line is echoed with a prefix, never as the tick's own line.
        self.assertIn("  autopick| STOP: log-unreadable: line 4", cp.stdout)
        self.assertFalse(any(ln.startswith("STOP:") for ln in cp.stdout.splitlines()))

    def test_missing_skill_is_unavailable_not_a_crash(self):
        shutil.rmtree(os.path.join(self.skills, "growth-review"))
        cp, res, order = self.tick()
        self.assertEqual(cp.returncode, 0)
        self.assertEqual(order, ORDER)
        self.assertEqual(res["growth-review"], ["unavailable", "growth-review"])
        self.assertEqual(res["sweep"][0], "ok")

    def test_no_skills_at_all_fails_closed(self):
        # Without spec-first-planning there is no checker, so no grant is live.
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        self.skills = empty
        cp, res, order = self.tick()
        self.assertEqual((cp.returncode, order), (2, []))
        self.assertIn("STOP: no-grant reason=checker-unavailable", cp.stdout)

    def test_bad_root_is_usage_error(self):
        cp = subprocess.run([sys.executable, TOOL, "--root", os.path.join(self.tmp, "nope"),
                             "tick"], capture_output=True, text=True, timeout=60)
        self.assertEqual(cp.returncode, 2)
        self.assertNotIn("STAGE:", cp.stdout)


class TestNoGrant(Fixture):
    """R13: the grant check runs before any stage, so nothing is swept, picked or planned."""

    def assertStopped(self, cp, order, reason):
        self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
        self.assertEqual(order, [])
        self.assertIn("STOP: no-grant reason=%s" % reason, cp.stdout.splitlines())
        self.assertEqual(K.calls(self.skills), [])  # no stage tool ran

    def test_no_pipeline_grant(self):
        cp, _, order = self.tick()
        self.assertStopped(cp, order, "none")

    def test_revoked_grant(self):
        gid = self.write_grant()
        self.assertEqual(self.checker("revoke-grant", "--root", self.repo, "--id", gid)
                         .returncode, 0)
        cp, _, order = self.tick()
        self.assertStopped(cp, order, "revoked")

    def test_kill_switch(self):
        self.write_grant()
        rv = self.checker("revoke-grant", "--root", self.repo)
        self.assertEqual(rv.returncode, 0, rv.stdout + rv.stderr)
        self.assertIn("REVOKED: ", rv.stdout)
        cp, _, order = self.tick()
        self.assertStopped(cp, order, "revoked")

    def test_expired_grant(self):
        past = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0) \
            - datetime.timedelta(days=4)
        self.write_grant(now=past, days=1)
        cp, _, order = self.tick()
        self.assertStopped(cp, order, "expired")

    def test_stale_grant(self):
        self.write_grant()
        with open(os.path.join(self.repo, ".factory", "check-commands.txt"), "a",
                  encoding="utf-8") as f:
            f.write("rm -rf /\n")
        cp, _, order = self.tick()
        self.assertStopped(cp, order, "stale")

    def test_tracked_grant(self):
        self.write_grant()
        git = ["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        subprocess.run(git + ["add", "-A", "-f"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "x"], check=True)
        cp, _, order = self.tick()
        self.assertStopped(cp, order, "tracked")

    def test_invalid_grant(self):
        self.write_grant()
        with open(self.grant, encoding="utf-8") as f:
            st = json.load(f)
        st["predicate"]["payload"]["gate_policy"]["merge"] = "auto"
        os.chmod(self.grant, 0o600)
        with open(self.grant, "w", encoding="utf-8") as f:
            json.dump(st, f)
        cp, _, order = self.tick()
        self.assertStopped(cp, order, "invalid")

    def test_checker_missing(self):
        self.write_grant()
        os.remove(os.path.join(self.skills, "spec-first-planning", "assets",
                               "contract_check.py"))
        cp, _, order = self.tick()
        self.assertStopped(cp, order, "checker-unavailable")

    def test_named_grant_must_be_an_envelope_here(self):
        self.write_grant()
        cp, _, order = self.tick("--grant", os.path.join(self.tmp, "elsewhere.json"))
        self.assertStopped(cp, order, "not-an-envelope")
        cp, _, order = self.tick("--grant", "pipeline-grant-v1-20260101T000000Z-abcdef")
        self.assertStopped(cp, order, "none")

    def test_named_grant_by_id_and_by_path(self):
        gid = self.write_grant()
        for ref in (gid, self.grant):
            cp, _, order = self.tick("--grant", ref)
            self.assertEqual((cp.returncode, order), (0, ORDER), cp.stdout)
            self.assertIn("TICK: grant %s " % gid, cp.stdout)

    def test_several_live_grants_the_newest_wins(self):
        # SPEC: check-pipeline-grant judges the newest grant that no revision supersedes.
        past = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0) \
            - datetime.timedelta(hours=1)
        self.write_grant(now=past)
        newest = self.write_grant()
        cp, _, order = self.tick()
        self.assertEqual((cp.returncode, order), (0, ORDER), cp.stdout)
        self.assertIn("TICK: grant %s " % newest, cp.stdout)


class TestDeriveGrantSeam(Fixture):
    """derive-grant is wired for the plans R17 will hand on; drive it directly."""

    def make_tick(self, grant="pg-1"):
        return P.Tick(self.repo, [self.skills], grant=grant, timeout=60)

    def test_calls_write_grant_under_pipeline_per_plan(self):
        t = self.make_tick()
        t.plans = ["plans/a.json", "plans/b.json"]
        self.assertEqual(P.stage_derive_grant(t), ("ok", "derived=2 refused=0"))
        argvs = [c["argv"] for c in K.calls(self.skills)]
        self.assertEqual(argvs[0], ["--under-pipeline", "pg-1", "--root", self.repo,
                                    "--plan", "plans/a.json"])
        self.assertEqual(len(t.grants), 2)

    def test_refused_plan_waits_for_the_owner(self):
        K.behave(self.skills, "spec-first-planning", "under-pipeline",
                 stdout="REFUSED: plan not from autopick\n", exit=1)
        t = self.make_tick()
        t.plans = ["plans/a.json"]
        self.assertEqual(P.stage_derive_grant(t), ("attention", "derived=0 refused=1"))
        self.assertEqual(t.grants, [])
        self.assertEqual(t.waiting[0][:2], ("plan-read", "plans/a.json"))

    def test_uses_only_the_checked_grant_never_a_fresh_glob(self):
        gid = self.write_grant()
        t = self.make_tick(grant=None)
        t.plans = ["plans/a.json"]
        self.assertEqual(P.stage_derive_grant(t), ("refused", "no-grant"))
        self.assertEqual(K.calls(self.skills), [])
        self.assertEqual(P.check_grant(t), (gid, None))
        t.grant = gid
        P.stage_derive_grant(t)
        self.assertEqual(K.calls(self.skills)[0]["argv"][1], gid)


class TestLookupAndShell(unittest.TestCase):
    def test_default_roots_put_the_sibling_dir_before_project_and_user(self):
        roots = P.skill_roots("/r", env={"HOME": "/h"})
        self.assertEqual(roots[0], os.path.dirname(P.SKILL_DIR))
        self.assertEqual(roots[1:], ["/r/.agents/skills", "/r/.claude/skills",
                                     "/h/.agents/skills", "/h/.claude/skills"])

    def test_skill_contract_path_comes_first(self):
        roots = P.skill_roots("/r", env={"HOME": "/h", "SKILL_CONTRACT_PATH": "/p"})
        self.assertEqual(roots[0], "/p")

    def test_override_replaces_the_search(self):
        self.assertEqual(P.skill_roots("/r", env={"FACTORY_PIPELINE_SKILLS": "/s"}), ["/s"])
        self.assertEqual(P.skill_roots("/r", env={"FACTORY_PIPELINE_SKILLS": "/s"},
                                       overrides=["/o"]), ["/o"])

    def test_no_shell_anywhere(self):
        with open(os.path.join(HERE, "pipeline.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword):
                self.assertNotEqual(node.arg, "shell")
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, ("system", "popen", "Popen"))


if __name__ == "__main__":
    unittest.main()
