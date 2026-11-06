#!/usr/bin/env python3
"""`pipeline tick`, the incident-probe stage (D17, S1): right after sweep, the tick runs
incident-watch's `incident probe` once when .release/recipe.json exists, and prints
`STAGE: incident-probe skip no-recipe` when it does not. The stage runs only the
recipe's probes (health, version_probe, prod_smoke), never a rollback, a deploy or any
other recipe command.

The fixture repo comes from incident-watch's own incident_testkit (a recipe whose every
command runs one local stub that logs its name). The skills directory holds the pipeline
stubs, the stub release-conductor (the real release.py, whose recipe reader the probe
imports) and a copy of the REAL incident-watch incident.py. Stdlib unittest; offline,
deterministic; scratch only in temp dirs.
Run:  python3 test_pipeline_incident.py
"""
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

INCIDENT_SKILL = os.path.join(os.path.dirname(os.path.dirname(HERE)), "incident-watch")
sys.path.insert(0, os.path.join(INCIDENT_SKILL, "assets"))
import incident_testkit as IK  # noqa: E402

TOOL = os.path.join(HERE, "pipeline.py")
PROBES = ["health", "version_probe", "prod_smoke_0", "prod_smoke_1"]


def install_incident_watch(skills):
    """Copy the real incident-watch (SKILL.md and assets/incident.py) into skills."""
    d = os.path.join(skills, "incident-watch", "assets")
    os.makedirs(d, exist_ok=True)
    shutil.copyfile(os.path.join(INCIDENT_SKILL, "SKILL.md"),
                    os.path.join(skills, "incident-watch", "SKILL.md"))
    shutil.copyfile(os.path.join(INCIDENT_SKILL, "assets", "incident.py"),
                    os.path.join(d, "incident.py"))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pipeline-incident-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        IK.fixture(self.repo)
        self.skills = K.stubs(os.path.join(self.tmp, "skills"))
        K.release_stub(self.skills)
        install_incident_watch(self.skills)
        K.pipeline_grant(self.repo)

    def tick(self):
        cp = subprocess.run([sys.executable, TOOL, "--root", self.repo,
                             "--skills-dir", self.skills, "tick"],
                            capture_output=True, text=True, timeout=300,
                            stdin=subprocess.DEVNULL)
        stages = [ln for ln in cp.stdout.splitlines() if ln.startswith("STAGE: ")]
        return cp, stages

    def assertOnlyProbesRan(self):
        self.assertEqual(sorted(IK.ran(self.repo)), sorted(PROBES))
        for name in IK.NON_PROBE:
            self.assertNotIn(name, IK.ran(self.repo))


class TestIncidentProbeStage(Fixture):
    def test_passing_recipe_probe_is_the_second_stage(self):
        cp, stages = self.tick()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(stages[0].split()[1], "sweep")
        self.assertEqual(stages[1], "STAGE: incident-probe ok probe=pass commands=4 failed=0")
        self.assertEqual(len(stages), 10)
        self.assertTrue(cp.stdout.splitlines()[-1].startswith("TICK: done stages=10"))
        # The tool's own lines are echoed with the stage prefix, never bare.
        self.assertIn("  incident-probe| PROBE_RESULT: pass commands=4 failed=0 "
                      "consecutive_failures=0", cp.stdout)
        self.assertFalse(any(ln.startswith("PROBE") for ln in cp.stdout.splitlines()))
        self.assertOnlyProbesRan()

    def test_probe_runs_once_and_logs_one_record(self):
        self.tick()
        self.assertEqual([c[0] for c in IK.calls(self.repo)], PROBES)
        with open(os.path.join(self.repo, ".skill-contract", "incidents", "probes.jsonl"),
                  encoding="utf-8") as f:
            records = [json.loads(ln) for ln in f if ln.strip()]
        self.assertEqual([r["result"] for r in records], ["pass"])

    def test_no_recipe_prints_the_skip_line_and_runs_nothing(self):
        os.remove(os.path.join(self.repo, ".release", "recipe.json"))
        cp, stages = self.tick()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(stages[1], "STAGE: incident-probe skip no-recipe")
        self.assertEqual(IK.calls(self.repo), [])
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".skill-contract",
                                                     "incidents")))

    def test_failing_probe_is_attention_and_still_runs_no_other_command(self):
        IK.behave(self.repo, "health", 1)
        cp, stages = self.tick()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(stages[1], "STAGE: incident-probe attention exit=3 probe=fail "
                                    "commands=4 failed=1")
        self.assertOnlyProbesRan()

    def test_two_failures_open_an_incident_and_never_roll_back(self):
        IK.behave(self.repo, "health", 1)
        self.tick()
        cp, stages = self.tick()
        self.assertEqual(stages[1].split()[:3], ["STAGE:", "incident-probe", "attention"])
        self.assertIn("  incident-probe| INCIDENT: opened ", cp.stdout)
        self.assertNotIn("rollback", IK.ran(self.repo))
        self.assertOnlyProbesRan()

    def test_missing_incident_watch_is_unavailable(self):
        shutil.rmtree(os.path.join(self.skills, "incident-watch"))
        cp, stages = self.tick()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(stages[1], "STAGE: incident-probe unavailable incident-watch")
        self.assertEqual(IK.calls(self.repo), [])

    def test_invalid_recipe_is_refused_before_any_command(self):
        with open(os.path.join(self.repo, ".release", "recipe.json"), "w",
                  encoding="utf-8") as f:
            f.write("{}\n")
        cp, stages = self.tick()
        self.assertEqual(stages[1], "STAGE: incident-probe refused exit=2 probe=none")
        self.assertEqual(IK.calls(self.repo), [])


class TestStageShape(unittest.TestCase):
    def test_stage_sits_right_after_sweep(self):
        names = [n for n, _ in P.STAGES]
        self.assertEqual(names[:3], ["sweep", "incident-probe", "growth-review"])
        self.assertEqual(len(names), 10)

    def test_argv_names_only_the_probe_command(self):
        tmp = tempfile.mkdtemp(prefix="pipeline-incident-argv-")
        self.addCleanup(shutil.rmtree, tmp, True)
        IK.fixture(tmp)
        seen = []

        def fake(tick, stage, argv, **kw):
            seen.append(argv)
            return "ok", 0, b"PROBE_RESULT: pass commands=1 failed=0 consecutive_failures=0\n"

        t = P.Tick(tmp, ["/s1", "/s2"])
        tool = "/s1/incident-watch/assets/incident.py"
        orig = P.run_tool
        P.run_tool = fake
        try:
            self.assertEqual(P.incident_probe(t, tool),
                             ("ok", 0, "probe=pass commands=1 failed=0"))
        finally:
            P.run_tool = orig
        argv = seen[0]
        self.assertEqual(argv[:4], [P.PYTHON, "-I", "-S", "-B"])
        rest = argv[argv.index(tool) + 1:]
        self.assertEqual(rest, ["--root", tmp, "--skills-dir", "/s1", "--skills-dir", "/s2",
                                "probe"])

    def test_not_in_the_tamper_lists(self):
        # Like sweep and growth-review, the stage runs before any planner (D17).
        self.assertNotIn("incident-probe", P.TAMPER_SKIP)
        self.assertNotIn("incident-probe", P.STAGE_CAPS)


if __name__ == "__main__":
    unittest.main()
