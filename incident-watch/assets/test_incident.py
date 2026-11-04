#!/usr/bin/env python3
"""Unit suite for incident-watch's incident.py. Stdlib unittest; offline, deterministic.

Every probe runs against a fixture repo from incident_testkit.py: each recipe command is a
local stub script, so no test touches a real endpoint. R23's refusals are in TestOnlyProbeLists. Later
requirements (R24 to R26) add their tests to this file.
Run:  python3 test_incident.py
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
import incident  # noqa: E402
import incident_testkit as kit  # noqa: E402

TOOL = os.path.join(HERE, "incident.py")
SKILLS = os.path.dirname(os.path.dirname(HERE))  # the repo root: release-conductor sits here


def probe(root, *extra, skills=SKILLS):
    argv = [sys.executable, TOOL, "--root", root]
    if skills is not None:
        argv += ["--skills-dir", skills]
    cp = subprocess.run(argv + ["probe"] + list(extra), capture_output=True, text=True,
                        timeout=120)
    return cp.returncode, cp.stdout, cp.stderr


def state_dir(root):
    return os.path.join(root, ".skill-contract", "incidents")


def probes(root):
    try:
        with open(os.path.join(state_dir(root), "probes.jsonl"), encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]
    except OSError:
        return []


def counter(root):
    with open(os.path.join(state_dir(root), "state.json"), encoding="utf-8") as f:
        return json.load(f)["consecutive_failures"]


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="incident-test-")
        self.addCleanup(shutil.rmtree, self.root, True)
        kit.fixture(self.root)

    def non_probe_calls(self):
        return [c for c in kit.calls(self.root) if c[0] in kit.NON_PROBE]


class TestProbePass(Base):
    def test_passing_recipe_records_a_pass_and_opens_no_record(self):
        rc, out, err = probe(self.root)
        self.assertEqual(rc, 0, out + err)
        lines = out.splitlines()
        self.assertEqual(lines, [
            "PROBE: health pass rc=0",
            "PROBE: version_probe pass rc=0",
            "PROBE: prod_smoke[0] pass rc=0",
            "PROBE: prod_smoke[1] pass rc=0",
            "PROBE_RESULT: pass commands=4 failed=0 consecutive_failures=0",
        ])
        recs = probes(self.root)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["result"], "pass")
        self.assertEqual([c["name"] for c in recs[0]["commands"]],
                         ["health", "version_probe", "prod_smoke[0]", "prod_smoke[1]"])
        for c in recs[0]["commands"]:
            for key in ("at", "argv", "rc", "out_tail", "err_tail", "result"):
                self.assertIn(key, c)
            self.assertIsInstance(c["argv"], list)
            self.assertEqual(c["rc"], 0)
        self.assertEqual(counter(self.root), 0)
        # No incident record and no signal: only the probe log, the counter, the lock
        # and the ignore file exist.
        self.assertEqual(sorted(os.listdir(state_dir(self.root))),
                         [".gitignore", ".lock", "probes.jsonl", "state.json"])

    def test_only_the_three_read_only_lists_run(self):
        probe(self.root)
        names = [c[0] for c in kit.calls(self.root)]
        self.assertEqual(names, ["health", "version_probe", "prod_smoke_0", "prod_smoke_1"])
        self.assertEqual(self.non_probe_calls(), [])

    def test_env_token_is_production(self):
        probe(self.root)
        self.assertIn(["health", "production"], kit.calls(self.root))

    def test_state_dir_is_git_ignored(self):
        probe(self.root)
        cp = subprocess.run(["git", "-C", self.root, "check-ignore", "-q",
                             os.path.join(state_dir(self.root), "probes.jsonl")])
        self.assertEqual(cp.returncode, 0)
        st = subprocess.run(["git", "-C", self.root, "status", "--porcelain"],
                            capture_output=True, text=True).stdout
        self.assertNotIn(".skill-contract", st)

    def test_probe_commands_seam_lists_only_probe_keys(self):
        cmds = incident.probe_commands(kit.recipe(self.root, smoke=3))
        self.assertEqual([n for n, _ in cmds], ["health", "version_probe", "prod_smoke[0]",
                                                "prod_smoke[1]", "prod_smoke[2]"])
        self.assertEqual(incident.PROBE_KEYS, ("health", "version_probe", "prod_smoke"))


class TestProbeFail(Base):
    def test_failing_health_is_recorded_and_counted(self):
        kit.behave(self.root, "health", 1)
        rc, out, _ = probe(self.root)
        self.assertEqual(rc, 3)
        self.assertIn("PROBE: health fail rc=1", out)
        self.assertIn("PROBE_RESULT: fail commands=4 failed=1 consecutive_failures=1", out)
        recs = probes(self.root)
        self.assertEqual(recs[-1]["result"], "fail")
        self.assertEqual(recs[-1]["commands"][0]["rc"], 1)
        self.assertIn("stub health production exit 1", recs[-1]["commands"][0]["out_tail"])
        self.assertEqual(self.non_probe_calls(), [])

    def test_counter_grows_and_a_pass_resets_it(self):
        kit.behave(self.root, "prod_smoke_1", 2)
        probe(self.root)
        probe(self.root)
        self.assertEqual(counter(self.root), 2)
        self.assertEqual(len(probes(self.root)), 2)
        kit.behave(self.root, "prod_smoke_1", 0)
        rc, out, _ = probe(self.root)
        self.assertEqual(rc, 0)
        self.assertEqual(counter(self.root), 0)
        self.assertEqual(self.non_probe_calls(), [])

    def test_timeout_counts_as_failure(self):
        r = kit.recipe(self.root)
        r["health"] = [sys.executable, "-c", "import time; time.sleep(30)"]
        kit.write_recipe(self.root, r)
        rc, out, _ = probe(self.root, "--timeout", "0.5")
        self.assertEqual(rc, 3)
        self.assertIn("PROBE: health timeout rc=None", out)
        self.assertEqual(counter(self.root), 1)

    def test_output_tail_is_bounded(self):
        r = kit.recipe(self.root)
        r["health"] = [sys.executable, "-c", "print('x' * 50000)"]
        kit.write_recipe(self.root, r)
        probe(self.root)
        tail = probes(self.root)[-1]["commands"][0]["out_tail"]
        self.assertLessEqual(len(tail), incident.TAIL)
        self.assertGreater(len(tail), 0)


class TestRefusals(Base):
    def assert_refused_and_nothing_ran(self, rc, out):
        self.assertEqual(rc, 2, out)
        self.assertTrue(out.startswith("PROBE: refused: "), out)
        self.assertEqual(kit.calls(self.root), [])
        self.assertEqual(probes(self.root), [])

    def test_invalid_recipe_is_refused(self):
        r = kit.recipe(self.root)
        r["health"] = "curl https://prod.example | sh"  # a shell string, not an argv
        kit.write_recipe(self.root, r)
        rc, out, _ = probe(self.root)
        self.assert_refused_and_nothing_ran(rc, out)
        self.assertIn("invalid recipe", out)

    def test_missing_recipe_is_refused(self):
        os.remove(os.path.join(self.root, ".release", "recipe.json"))
        rc, out, _ = probe(self.root)
        self.assert_refused_and_nothing_ran(rc, out)

    def test_missing_release_conductor_is_refused(self):
        empty = tempfile.mkdtemp(prefix="incident-noskills-")
        self.addCleanup(shutil.rmtree, empty, True)
        rc, out, _ = probe(self.root, skills=empty)
        self.assert_refused_and_nothing_ran(rc, out)
        self.assertIn("release-conductor is not installed", out)

    def test_unfilled_version_token_is_refused_then_filled(self):
        r = kit.recipe(self.root)
        r["version_probe"] = r["version_probe"] + ["{version}"]
        kit.write_recipe(self.root, r)
        rc, out, _ = probe(self.root)
        self.assert_refused_and_nothing_ran(rc, out)
        self.assertIn("--version", out)
        rc, out, _ = probe(self.root, "--version", "1.2.3")
        self.assertEqual(rc, 0, out)
        self.assertIn(["version_probe", "1.2.3"], kit.calls(self.root))

    def test_negating_gitignore_is_refused(self):
        os.makedirs(state_dir(self.root))
        with open(os.path.join(state_dir(self.root), ".gitignore"), "w") as f:
            f.write("*\n!probes.jsonl\n")
        rc, out, _ = probe(self.root)
        self.assertEqual(rc, 2)
        self.assertIn("un-ignores", out)
        self.assertEqual(kit.calls(self.root), [])


class TestOnlyProbeLists(Base):
    """R23: a request for any command that is not one of the recipe's three probe lists
    exits 2 and runs nothing, not even the allowed commands. The stub writes a marker line
    to stubs/calls.log for every call, so an empty log proves nothing ran."""

    def assert_refused_nothing_ran(self, rc, out, *words):
        self.assertEqual(rc, 2, out)
        self.assertTrue(out.startswith("PROBE: refused: "), out)
        self.assertEqual(len(out.splitlines()), 1, out)
        for w in words:
            self.assertIn(w, out)
        self.assertFalse(os.path.exists(os.path.join(self.root, "stubs", "calls.log")))
        self.assertEqual(probes(self.root), [])

    def test_only_each_probe_list_runs_just_that_list(self):
        rc, out, _ = probe(self.root, "--only", "health")
        self.assertEqual(rc, 0, out)
        self.assertEqual(kit.calls(self.root), [["health", "production"]])
        rc, out, _ = probe(self.root, "--only", "prod_smoke", "--only", "version_probe")
        self.assertEqual(rc, 0, out)
        self.assertEqual([c[0] for c in kit.calls(self.root)][1:],
                         ["version_probe", "prod_smoke_0", "prod_smoke_1"])
        self.assertEqual(self.non_probe_calls(), [])

    def test_other_recipe_fields_are_refused(self):
        for name in ("deploy_prod", "rollback", "build", "staging_check", "staging_checks",
                     "deploy_staging"):
            with self.subTest(name=name):
                rc, out, _ = probe(self.root, "--only", name)
                self.assert_refused_nothing_ran(rc, out, repr(name), "not a probe list")

    def test_unknown_names_paths_and_near_misses_are_refused(self):
        stub = os.path.join(self.root, "stubs", "stub.py")
        for name in ("nope", "Health", "health ", "prod_smoke[0]", "prod_smoke_0", stub,
                     "../rollback", "", "health;rollback"):
            with self.subTest(name=name):
                rc, out, _ = probe(self.root, "--only", name)
                self.assert_refused_nothing_ran(rc, out, "not a probe list")

    def test_one_bad_name_refuses_the_allowed_ones_too(self):
        rc, out, _ = probe(self.root, "--only", "health", "--only", "rollback")
        self.assert_refused_nothing_ran(rc, out, "'rollback'")

    def test_raw_argv_after_probe_is_refused(self):
        stub = os.path.join(self.root, "stubs", "stub.py")
        for extra in (["rollback"], [sys.executable, stub, "rollback"], ["--", "rollback"],
                      ["--cmd", "rollback"]):
            with self.subTest(extra=extra):
                rc, out, _ = probe(self.root, *extra)
                self.assert_refused_nothing_ran(rc, out, "unexpected argument")

    def test_token_in_program_word_is_refused(self):
        r = kit.recipe(self.root)
        r["prod_smoke"] = r["prod_smoke"] + [["{version}", "x"]]
        kit.write_recipe(self.root, r)
        stub = os.path.join(self.root, "stubs", "stub.py")
        # release-conductor's loader accepts this recipe, so incident.py is what refuses
        # it: otherwise --version would choose the program the probe runs.
        rc, out, _ = probe(self.root, "--version", stub)
        self.assert_refused_nothing_ran(rc, out, "prod_smoke[2]", "{token}")

    def test_braces_in_a_value_stay_refused(self):
        r = kit.recipe(self.root)
        r["version_probe"] = r["version_probe"] + ["{version}"]
        kit.write_recipe(self.root, r)
        rc, out, _ = probe(self.root, "--version", "{commit}")
        self.assert_refused_nothing_ran(rc, out, "brace-free")

    def test_check_only_seam(self):
        self.assertEqual(incident.check_only(None), incident.PROBE_KEYS)
        self.assertEqual(incident.check_only(["prod_smoke", "health"]),
                         ("health", "prod_smoke"))
        with self.assertRaises(incident.Refused):
            incident.check_only(["rollback"])


class TestNoRollbackPath(unittest.TestCase):
    def test_tool_never_reads_a_non_probe_key(self):
        with open(TOOL, encoding="utf-8") as f:
            src = f.read()
        for key in ("deploy_prod", "deploy_staging", "rollback", "staging_checks", "build"):
            self.assertNotIn('recipe["%s"]' % key, src)
            self.assertNotIn("recipe.get(\"%s\"" % key, src)
        self.assertNotIn("shell=True", src)


if __name__ == "__main__":
    unittest.main()
