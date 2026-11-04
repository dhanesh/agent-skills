#!/usr/bin/env python3
"""Unit suite for incident-watch's incident.py. Stdlib unittest; offline, deterministic.

Every probe runs against a fixture repo from incident_testkit.py: each recipe command is a
local stub script, so no test touches a real endpoint. R23's refusals are in TestOnlyProbeLists.
R24's incident record and signal are in TestIncidentRecord. R25's rollback view (shown,
never run) is in TestRollbackShown, TestRollbackNeverRuns and TestNoRollbackPath. R26 adds
its tests to this file.
Run:  python3 test_incident.py
"""
import ast
import datetime
import glob
import importlib.util
import json
import os
import shlex
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


# The recipe fields incident.py MUST never run (D1, B1, S4).
NEVER_RUN = ("build", "deploy_staging", "deploy_prod", "rollback", "staging_checks")
NEVER_RUN_STUBS = ("build", "deploy_staging", "deploy_prod", "rollback", "staging_check")


class RollbackBase(Base):
    """A fixture whose rollback carries {version} and {commit}, and whose health fails."""

    def setUp(self):
        super().setUp()
        r = kit.recipe(self.root)
        r["rollback"] = r["rollback"] + ["--to", "{version}", "--commit={commit}"]
        kit.write_recipe(self.root, r)
        kit.behave(self.root, "health", 1)

    def recipe_on_disk(self):
        with open(os.path.join(self.root, ".release", "recipe.json"), encoding="utf-8") as f:
            return json.load(f)

    def assert_never_ran(self):
        self.assertEqual([n for n in kit.ran(self.root) if n in NEVER_RUN_STUBS], [])
        self.assertEqual(self.non_probe_calls(), [])


class TestRollbackShown(RollbackBase):
    """R25: the opened record holds the recipe's rollback argv word for word and the
    release-conductor command that asks the owner, and the probe prints both."""

    def test_open_record_holds_rollback_argv_word_for_word(self):
        probe(self.root)
        rc, out, _ = probe(self.root)
        self.assertEqual(rc, 3, out)
        rb = records(self.root)[0][1]["rollback"]
        want = self.recipe_on_disk()["rollback"]
        self.assertTrue(rb["available"])
        self.assertEqual(rb["field"], "rollback")
        self.assertEqual(rb["argv"], want)  # word for word, {tokens} unfilled
        self.assertIn("{version}", rb["argv"])
        self.assertEqual(rb["display"], shlex.join(want))
        self.assertEqual(rb["tokens"], ["commit", "version"])
        ask = rb["ask_owner"]
        self.assertEqual(ask["command"], "release rollback")
        self.assertEqual(ask["on_owner_yes"], 'release rollback --approved-by "<their name>"')
        self.assertIs(ask["run_by_incident_watch"], False)
        lines = out.splitlines()
        self.assertIn("ROLLBACK: %s" % shlex.join(want), lines)
        self.assertIn('NEXT: ask the owner to roll back: release rollback (shows the rollback '
                      'and waits); on their yes: release rollback --approved-by '
                      '"<their name>"', lines)
        self.assert_never_ran()

    def test_rollback_lines_print_only_when_the_record_opens(self):
        rc, out, _ = probe(self.root)
        self.assertNotIn("ROLLBACK:", out)
        probe(self.root)
        rc, out, _ = probe(self.root)
        self.assertIn("INCIDENT: open ", out)
        self.assertNotIn("ROLLBACK:", out)
        self.assertNotIn("NEXT: ask the owner", out)
        self.assertEqual(records(self.root)[0][1]["rollback"]["argv"],
                         self.recipe_on_disk()["rollback"])
        self.assert_never_ran()

    def test_release_conductor_documents_the_owner_command(self):
        # The command is release-conductor's, not invented here: its SKILL.md step 7
        # names both forms, and release.py has the `rollback` subcommand.
        with open(os.path.join(SKILLS, "release-conductor", "SKILL.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("`release rollback` without `--approved-by`", text)
        self.assertIn('`release rollback --approved-by "<their name>"`', text)
        with open(os.path.join(SKILLS, "release-conductor", "assets", "release.py"),
                  encoding="utf-8") as f:
            self.assertIn('"rollback"', f.read())

    def test_display_escapes_control_characters(self):
        argv = ["/bin/roll", "a\nb", "\x1b[31mred", "x‮y", "two words", "$(id)"]
        shown = incident.safe_display(argv)
        self.assertNotRegex(shown, "[\x00-\x1f\x7f‮]")
        self.assertIn("\\x0a", shown)
        self.assertIn("\\x1b", shown)
        self.assertIn("\\u202e", shown)
        self.assertIn("'two words'", shown)
        self.assertIn("'$(id)'", shown)
        view = incident.rollback_view({"rollback": argv})
        self.assertEqual(view["argv"], argv)  # the record keeps the words as they are
        self.assertEqual(view["display"], shown)
        for line in incident.rollback_lines(view):
            self.assertNotRegex(line, "[\x00-\x1f\x7f\u202e]")

    def test_view_is_a_copy_not_the_recipe_list(self):
        recipe = {"rollback": ["/bin/roll", "{version}"]}
        view = incident.rollback_view(recipe)
        view["argv"].append("x")
        self.assertEqual(recipe["rollback"], ["/bin/roll", "{version}"])

    def test_no_rollback_field_is_recorded_and_the_probe_goes_on(self):
        r = self.recipe_on_disk()
        del r["rollback"]
        kit.write_recipe(self.root, r)
        rc, out, _ = probe(self.root)
        self.assertEqual(rc, 3, out)
        rc, out, _ = probe(self.root)
        self.assertEqual(rc, 3, out)
        self.assertIn("INCIDENT: opened", out)
        self.assertIn("ROLLBACK: none: the release recipe has no rollback field; the owner "
                      "mitigates by hand", out.splitlines())
        self.assertNotIn("NEXT: ask the owner", out)
        rb = records(self.root)[0][1]["rollback"]
        self.assertFalse(rb["available"])
        self.assertIsNone(rb["argv"])
        self.assertEqual(rb["reason"], "the release recipe has no rollback field")
        self.assert_never_ran()

    def test_invalid_rollback_is_recorded_not_shown(self):
        r = self.recipe_on_disk()
        r["rollback"] = "./rollback.sh && echo done"  # a shell string, not an argv
        kit.write_recipe(self.root, r)
        probe(self.root)
        rc, out, _ = probe(self.root)
        self.assertEqual(rc, 3, out)
        rb = records(self.root)[0][1]["rollback"]
        self.assertFalse(rb["available"])
        self.assertIsNone(rb["argv"])
        self.assertIn("rollback field is invalid", rb["reason"])
        self.assertIn("ROLLBACK: none: ", out)

    def test_other_invalid_fields_still_refuse(self):
        r = self.recipe_on_disk()
        del r["rollback"]
        r["deploy_prod"] = "deploy.sh | sh"
        kit.write_recipe(self.root, r)
        rc, out, _ = probe(self.root)
        self.assertEqual(rc, 2, out)
        self.assertIn("deploy_prod", out)
        self.assertEqual(kit.calls(self.root), [])

    def test_releases_rollback_acts_on_are_named(self):
        view = incident.rollback_view(
            {"rollback": ["/bin/roll"]},
            [{"version": "1.0.0", "status": "verified"},
             {"version": "1.1.0", "status": "prod_failed"}])
        self.assertEqual(view["ask_owner"]["releases_it_acts_on"], ["1.1.0"])
        self.assertEqual(view["ask_owner"]["acts_on"], ["prod_failed", "outcome_unknown"])


class TestRollbackNeverRuns(RollbackBase):
    """R25 (a): the rollback and deploy stubs write a marker file when they run. Across
    the pass, fail, open-record and repeated-failure paths, and the refusals, no marker
    for them appears."""

    def test_no_marker_across_every_probe_path(self):
        kit.behave(self.root, "health", 0)
        steps = [("pass", 0, None), ("fail", 3, 1), ("fail-opens-record", 3, 1),
                 ("repeat-1", 3, 1), ("repeat-2", 3, 1), ("repeat-3", 3, 1),
                 ("pass-again", 0, 0), ("fail-after-pass", 3, 1)]
        for name, want_rc, health in steps:
            with self.subTest(step=name):
                if health is not None:
                    kit.behave(self.root, "health", health)
                rc, out, _ = probe(self.root)
                self.assertEqual(rc, want_rc, out)
                self.assert_never_ran()
        for extra in (["--only", "rollback"], ["--only", "deploy_prod"], ["rollback"]):
            with self.subTest(refusal=extra):
                rc, out, _ = probe(self.root, *extra)
                self.assertEqual(rc, 2, out)
                self.assert_never_ran()
        # The probe stubs did run, so the marker files work.
        self.assertIn("health", kit.ran(self.root))
        self.assertEqual(len(records(self.root)), 1)
        self.assertEqual(records(self.root)[0][1]["rollback"]["argv"],
                         self.recipe_on_disk()["rollback"])

    def test_run_cmd_never_sees_a_rollback_or_deploy_argv(self):
        """In-process: wrap release-conductor's run_cmd and record every argv the probe
        passes to it, across a pass, the record opening and a repeated failure."""
        real = incident.load_release_module
        seen = []

        def spy_loader(path):
            mod = real(path)
            orig = mod.run_cmd

            def run_cmd(argv, cwd, timeout, *a, **k):
                seen.append(list(argv))
                return orig(argv, cwd, timeout, *a, **k)
            mod.run_cmd = run_cmd
            return mod
        incident.load_release_module = spy_loader
        self.addCleanup(setattr, incident, "load_release_module", real)
        import contextlib
        import io
        for health in (0, 1, 1, 1):
            kit.behave(self.root, "health", health)
            with contextlib.redirect_stdout(io.StringIO()):
                incident.main(["--root", self.root, "--skills-dir", SKILLS, "probe"])
        recipe = self.recipe_on_disk()
        stub = os.path.join(self.root, "stubs", "stub.py")
        self.assertTrue(seen)
        for argv in seen:
            self.assertEqual(argv[:2], [sys.executable, stub])
            self.assertIn(argv[2], ("health", "version_probe", "prod_smoke_0", "prod_smoke_1"))
        for key in ("build", "deploy_staging", "deploy_prod", "rollback"):
            self.assertNotIn(recipe[key][:3], [a[:3] for a in seen])
        self.assertEqual(len(records(self.root)), 1)
        self.assert_never_ran()


def _parse_tool():
    with open(TOOL, encoding="utf-8") as f:
        src = f.read()
    return src, ast.parse(src)


def _functions(tree):
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def _enclosing(tree):
    """{id(node): name of the top-level function that holds it, or None}."""
    out = {}
    for top in tree.body:
        name = top.name if isinstance(top, ast.FunctionDef) else None
        for n in ast.walk(top):
            out[id(n)] = name
    return out


def _calls_named(tree, name):
    """Every Call whose callee is `name` or `<anything>.name`."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Name) and f.id == name) or \
                    (isinstance(f, ast.Attribute) and f.attr == name):
                out.append(n)
    return out


class TestNoRollbackPath(unittest.TestCase):
    """R25 (b): a source-level proof that incident.py can run only the probe lists. Reading
    `rollback` is allowed in rollback_view() alone, for display, and no execution path
    reaches what it returns."""

    def setUp(self):
        self.src, self.tree = _parse_tool()
        self.where = _enclosing(self.tree)
        self.funcs = _functions(self.tree)

    def test_no_shell_and_no_other_process_api(self):
        self.assertNotIn("shell=True", self.src)
        names = {n.id for n in ast.walk(self.tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(self.tree) if isinstance(n, ast.Attribute)}
        imported = set()
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Import):
                imported |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                imported.add((n.module or "").split(".")[0])
        for mod in ("subprocess", "pty", "multiprocessing", "asyncio"):
            self.assertNotIn(mod, imported)
            self.assertNotIn(mod, names)
        for bad in ("system", "popen", "Popen", "execv", "execve", "execvp", "execl",
                    "spawnv", "spawnl", "posix_spawn", "startfile", "check_output",
                    "check_call", "call"):
            self.assertNotIn(bad, attrs, bad)

    def test_run_cmd_is_called_once_in_run_probe_with_probe_argv(self):
        calls = _calls_named(self.tree, "run_cmd")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.where[id(calls[0])], "run_probe")
        self.assertEqual(ast.unparse(calls[0].args[0]), "argv")
        rp = _calls_named(self.tree, "run_probe")
        self.assertEqual(len(rp), 1)
        self.assertEqual(self.where[id(rp[0])], "cmd_probe")
        self.assertEqual(ast.unparse(rp[0].args[2]), "commands")
        # `commands` in cmd_probe is assigned once, from the probe lists only.
        assigns = [n for n in ast.walk(self.funcs["cmd_probe"]) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "commands" for t in n.targets)]
        self.assertEqual(len(assigns), 1)
        self.assertRegex(ast.unparse(assigns[0].value),
                         r"^expand_all\(release, probe_commands\(recipe, keys\), ")
        # probe_commands reads only the PROBE_KEYS entries.
        subs = [ast.unparse(n.slice) for n in ast.walk(self.funcs["probe_commands"])
                if isinstance(n, ast.Subscript)]
        self.assertEqual(subs, ["key", "key"])

    def test_non_probe_keys_are_read_only_in_rollback_view(self):
        hits = {}
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Constant) and n.value in NEVER_RUN:
                hits.setdefault(n.value, set()).add(self.where.get(id(n)))
        # `rollback` appears only in rollback_view (the read) and in open_record (the
        # record key it is stored under); no other non-probe key appears at all.
        self.assertEqual(set(hits), {"rollback"}, hits)
        self.assertEqual(hits["rollback"], {"rollback_view", "open_record"}, hits)
        reads = [n for n in ast.walk(self.tree)
                 if (isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant)
                     and n.slice.value == "rollback")
                 or (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                     and n.func.attr == "get" and n.args
                     and isinstance(n.args[0], ast.Constant) and n.args[0].value == "rollback")]
        self.assertEqual([self.where[id(n)] for n in reads], ["rollback_view"])

    def test_rollback_view_runs_nothing_and_feeds_no_runner(self):
        rv = self.funcs["rollback_view"]
        called = {ast.unparse(n.func) for n in ast.walk(rv) if isinstance(n, ast.Call)}
        for bad in ("run_cmd", "run_probe", "expand_all", "release.expand", "expand"):
            self.assertNotIn(bad, called)
        for fn in ("safe_display", "rollback_lines"):
            inner = {ast.unparse(n.func) for n in ast.walk(self.funcs[fn])
                     if isinstance(n, ast.Call)}
            self.assertFalse(inner & {"run_cmd", "run_probe", "expand_all", "expand"}, fn)
        uses = _calls_named(self.tree, "rollback_view")
        self.assertEqual([self.where[id(n)] for n in uses], ["open_record"])
        # Its result is stored in the record and returned for printing; no call takes it.
        orc = self.funcs["open_record"]
        for n in ast.walk(orc):
            if isinstance(n, ast.Call) and ast.unparse(n.func) != "rollback_view":
                self.assertNotIn("rollback", [ast.unparse(a) for a in n.args],
                                 ast.unparse(n))
        for n in _calls_named(self.tree, "rollback_lines"):
            self.assertEqual(self.where[id(n)], "after_probe")


if __name__ == "__main__":
    unittest.main()
