#!/usr/bin/env python3
"""Unit suite for incident-watch's incident.py. Stdlib unittest; offline, deterministic.

Every probe runs against a fixture repo from incident_testkit.py: each recipe command is a
local stub script, so no test touches a real endpoint. R23's refusals are in TestOnlyProbeLists.
R24's incident record and signal are in TestIncidentRecord. Later requirements (R25, R26)
add their tests to this file.
Run:  python3 test_incident.py
"""
import datetime
import glob
import importlib.util
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
        # No incident record and no signal: a pass leaves only the probe log, the
        # counter, the lock and the ignore file (R24 adds records/ and signals.jsonl only
        # after 2 consecutive failures; TestIncidentRecord covers that).
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


def load_skill_module(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SKILLS, *rel.split("/")))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(spec.origin))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


def records(root):
    out = []
    for path in sorted(glob.glob(os.path.join(state_dir(root), "records", "*.json"))):
        with open(path, encoding="utf-8") as f:
            out.append((path, json.load(f)))
    return out


def signals(root):
    try:
        with open(os.path.join(state_dir(root), "signals.jsonl"), encoding="utf-8") as f:
            return [ln for ln in f.read().splitlines() if ln.strip()]
    except OSError:
        return []


class TestIncidentRecord(Base):
    """R24: no record after one failed probe; one record, with the probe output, the last
    release record and the CI runs, and one `incident` signal line after the second; a
    third failure opens no second record."""

    def setUp(self):
        super().setUp()
        kit.behave(self.root, "health", 1)

    def test_one_failure_opens_no_record(self):
        rc, out, _ = probe(self.root)
        self.assertEqual(rc, 3)
        self.assertNotIn("INCIDENT:", out)
        self.assertEqual(records(self.root), [])
        self.assertEqual(signals(self.root), [])
        self.assertFalse(os.path.exists(os.path.join(state_dir(self.root), "records")))

    def test_second_failure_opens_one_record_with_probe_output_and_one_signal(self):
        probe(self.root)
        rc, out, _ = probe(self.root)
        self.assertEqual(rc, 3, out)
        recs = records(self.root)
        self.assertEqual(len(recs), 1)
        path, rec = recs[0]
        rel = os.path.relpath(path, self.root)
        self.assertIn("INCIDENT: opened %s %s" % (rec["id"], rel), out)
        self.assertIn("SIGNAL: .skill-contract/incidents/signals.jsonl kind incident", out)
        self.assertIn("NEXT: intake import --format intake-signals-jsonl --source "
                      "incident-watch .skill-contract/incidents/signals.jsonl", out)
        self.assertRegex(rec["id"], r"^inc-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$")
        self.assertEqual(os.path.basename(path), rec["id"] + ".json")
        self.assertEqual(rec["status"], "open")
        self.assertEqual(len(rec["probes"]), 2)
        for p in rec["probes"]:
            self.assertEqual(p["result"], "fail")
            self.assertIn("stub health production exit 1", p["commands"][0]["out_tail"])
            self.assertLessEqual(len(p["commands"][0]["out_tail"]), incident.TAIL)
        for key in ("last_release", "releases", "ci_runs", "rollback", "closed_at",
                    "closed_by"):
            self.assertIn(key, rec)
        lines = signals(self.root)
        self.assertEqual(len(lines), 1)
        sig = json.loads(lines[0])
        self.assertEqual((sig["kind"], sig["source"], sig["source_id"], sig["severity"]),
                         ("incident", "incident-watch", rec["id"], 4))
        self.assertIn(rel, sig["evidence"][0]["text"])
        with open(os.path.join(state_dir(self.root), "state.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["open_incident"], rec["id"])
        self.assertEqual(self.non_probe_calls(), [])
        for f in (path, os.path.join(state_dir(self.root), "signals.jsonl")):
            cp = subprocess.run(["git", "-C", self.root, "check-ignore", "-q", f])
            self.assertEqual(cp.returncode, 0, f)

    def test_third_failure_opens_no_second_record(self):
        for _ in range(2):
            probe(self.root)
        first = records(self.root)[0][1]["id"]
        rc, out, _ = probe(self.root)
        self.assertEqual(rc, 3)
        self.assertNotIn("INCIDENT: opened", out)
        recs = records(self.root)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0][1]["id"], first)
        self.assertIn("INCIDENT: open %s " % first, out)
        self.assertIn("probes=3", out)
        self.assertEqual(recs[0][1]["probe_count"], 3)
        self.assertEqual(len(recs[0][1]["probes"]), 3)
        self.assertEqual(len(signals(self.root)), 1)
        self.assertEqual(self.non_probe_calls(), [])

    def test_a_pass_between_failures_keeps_the_one_open_record(self):
        for _ in range(2):
            probe(self.root)
        kit.behave(self.root, "health", 0)
        rc, out, _ = probe(self.root)
        self.assertEqual(rc, 0)
        self.assertNotIn("INCIDENT:", out)
        kit.behave(self.root, "health", 1)
        probe(self.root)
        rc, out, _ = probe(self.root)
        self.assertNotIn("INCIDENT: opened", out)
        self.assertEqual(len(records(self.root)), 1)
        self.assertEqual(len(signals(self.root)), 1)

    def test_signal_line_is_accepted_by_ops_intake(self):
        for _ in range(2):
            probe(self.root)
        adapters = load_skill_module("incident_test_adapters", "ops-intake/assets/adapters.py")
        ctx = {"source": "incident-watch", "now": "2026-11-04T00:00:00Z"}
        sigs, problems = adapters.ADAPTERS["intake-signals-jsonl"](
            "\n".join(signals(self.root)), ctx)
        self.assertEqual(problems, [])
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0]["kind"], "incident")

    def test_ci_runs_unavailable_prints_the_gh_next_line(self):
        for _ in range(2):
            rc, out, _ = probe(self.root)
        ci = records(self.root)[0][1]["ci_runs"]
        self.assertFalse(ci["available"])
        want = ("NEXT: gh run list --branch main --limit 10 --json databaseId,workflowName,"
                "headBranch,headSha,event,status,conclusion,createdAt,updatedAt,url,attempt"
                " > .skill-contract/incidents/ci-runs.json")
        self.assertIn(want, out.splitlines())
        self.assertEqual("NEXT: " + ci["next"], want)

    def test_ci_runs_are_read_from_disk_for_the_default_branch(self):
        os.makedirs(os.path.join(self.root, ".intake"))
        with open(os.path.join(self.root, ".intake", "config.json"), "w") as f:
            json.dump({"ci": {"default_branch": "trunk"}}, f)
        os.makedirs(state_dir(self.root))
        runs = [{"databaseId": i, "workflowName": "gate", "headBranch": b,
                 "headSha": "a" * 40, "event": "push", "status": "completed",
                 "conclusion": "failure" if i % 2 else "success",
                 "createdAt": "2026-11-0%dT00:00:00Z" % i, "updatedAt": "x", "url": "u",
                 "attempt": 1, "secret": "dropped"}
                for i, b in ((1, "trunk"), (2, "feature"), (3, "trunk"))]
        with open(os.path.join(state_dir(self.root), "ci-runs.json"), "w") as f:
            json.dump(runs, f)
        for _ in range(2):
            rc, out, _ = probe(self.root)
        self.assertNotIn("NEXT: gh run list", out)
        ci = records(self.root)[0][1]["ci_runs"]
        self.assertTrue(ci["available"])
        self.assertEqual(ci["branch"], "trunk")
        self.assertEqual([r["databaseId"] for r in ci["runs"]], [3, 1])
        self.assertNotIn("secret", ci["runs"][0])

    def test_last_release_comes_from_the_newest_release_result_envelope(self):
        release = load_skill_module("incident_test_release", "release-conductor/assets/release.py")
        cc = release.CC
        with open(os.path.join(self.root, "recipe-copy.json"), "w") as f:
            f.write("{}\n")

        def env(version, outcome, day, skill="release-conductor"):
            payload = {"version": version, "commit": "b" * 40, "outcome": outcome,
                       "rollback_target": {"version": "0.9.0", "commit": None}}
            when = datetime.datetime(2026, 11, day, tzinfo=datetime.timezone.utc)
            st = cc.build_statement(release.RESULT_KIND, skill, "1.0.0", self.root,
                                    ["recipe-copy.json"], payload, now=when)
            return cc.write_envelope(self.root, st)
        env("1.0.0", "verified", 1)
        newest = env("1.1.0", "rolled_back", 2)
        env("9.9.9", "verified", 3, skill="someone-else")  # wrong producer: ignored
        os.makedirs(os.path.join(self.root, ".skill-contract", "releases", "1.2.0"))
        for _ in range(2):
            probe(self.root)
        rec = records(self.root)[0][1]
        last = rec["last_release"]
        self.assertTrue(last["available"])
        self.assertEqual((last["version"], last["outcome"]), ("1.1.0", "rolled_back"))
        self.assertEqual(last["path"], os.path.relpath(newest, self.root))
        self.assertEqual(rec["releases"], [{"version": "1.2.0", "status": "unreadable"}])

    def test_no_release_envelope_is_recorded_as_unavailable(self):
        for _ in range(2):
            probe(self.root)
        last = records(self.root)[0][1]["last_release"]
        self.assertFalse(last["available"])
        self.assertIn("no release-result/v1 envelope", last["reason"])


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
