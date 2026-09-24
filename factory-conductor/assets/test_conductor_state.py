import contextlib, io, json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C
from conductor_testkit import read_text, repo, tmpdir, write_plan_envelope, write_grant
HAVE_GIT = __import__("shutil").which("git") is not None


def plan(tasks):
    """A minimal task-plan payload: {id: depends_on}."""
    return {"title": "T", "spec": "docs/spec.md", "coverage": {}, "uncovered": [],
            "tasks": [{"id": i, "requirement_ids": ["R1"], "title": i,
                       "verify": [{"text": "t", "command": ["true"]}],
                       "depends_on": d} for i, d in tasks.items()]}


class Waves(unittest.TestCase):
    def test_no_dependencies_is_one_wave(self):
        self.assertEqual(C.waves({"T1": {"depends_on": []}, "T2": {"depends_on": []}}),
                         [["T1", "T2"]])

    def test_chain_is_three_waves(self):
        t = {"T1": {"depends_on": []}, "T2": {"depends_on": ["T1"]}, "T3": {"depends_on": ["T2"]}}
        self.assertEqual(C.waves(t), [["T1"], ["T2"], ["T3"]])

    def test_diamond_groups_the_middle(self):
        t = {"T1": {"depends_on": []}, "T2": {"depends_on": ["T1"]},
             "T3": {"depends_on": ["T1"]}, "T4": {"depends_on": ["T2", "T3"]}}
        self.assertEqual(C.waves(t), [["T1"], ["T2", "T3"], ["T4"]])

    def test_cycle_raises(self):
        with self.assertRaises(C.PlanError) as e:
            C.waves({"T1": {"depends_on": ["T2"]}, "T2": {"depends_on": ["T1"]}})
        self.assertIn("cycle", str(e.exception))

    def test_unknown_dependency_raises(self):
        with self.assertRaises(C.PlanError) as e:
            C.waves({"T1": {"depends_on": ["T9"]}})
        self.assertIn("T9", str(e.exception))


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        self.st = C.State.new(root=self.tmp, run_id="run-20260922T120000Z-a1b2c3",
                              plan=plan({"T1": [], "T2": ["T1"]}), plan_envelope="e.json",
                              plan_sha256="0" * 64, grant_id="autonomy-grant-v1-x",
                              run_branch="factory/p", base_branch="main",
                              budget={"max_parallel": 2})

    def test_ready_respects_dependencies_and_parallelism(self):
        self.assertEqual(self.st.ready(2), ["T1"])
        self.st.set_status("T1", "proven")
        self.assertEqual(self.st.ready(2), ["T2"])

    def test_ready_is_capped_by_max_parallel(self):
        st = C.State.new(root=self.tmp, run_id="run-20260922T120001Z-a1b2c4",
                         plan=plan({"T1": [], "T2": [], "T3": []}), plan_envelope="e.json",
                         plan_sha256="0" * 64, grant_id="g", run_branch="factory/p",
                         base_branch="main", budget={"max_parallel": 2})
        self.assertEqual(len(st.ready(2)), 2)
        self.assertEqual(st.ready(2), ["T1", "T2"])

    def test_running_tasks_count_against_parallelism(self):
        st = C.State.new(root=self.tmp, run_id="run-20260922T120002Z-a1b2c5",
                         plan=plan({"T1": [], "T2": [], "T3": []}), plan_envelope="e.json",
                         plan_sha256="0" * 64, grant_id="g", run_branch="factory/p",
                         base_branch="main", budget={"max_parallel": 2})
        st.set_status("T1", "running")
        self.assertEqual(st.ready(2), ["T2"])

    def test_dependents_of_a_parked_task_are_blocked(self):
        self.st.set_status("T1", "parked", park_reason="needs a decision")
        self.assertEqual(self.st.ready(2), [])
        self.assertEqual(self.st.tasks["T2"]["status"], "blocked")

    def test_log_is_append_only_and_one_object_per_line(self):
        # State.new logs the run's own init event first (fix round 1, item 9).
        self.st.log("init", run="x")
        self.st.log("start", task="T1")
        lines = read_text(self.st.log_path).strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual([json.loads(l)["event"] for l in lines], ["init", "init", "start"])
        self.assertTrue(all(json.loads(l)["at"].endswith("Z") for l in lines))

    def test_save_and_load_round_trip(self):
        self.st.set_status("T1", "running")
        self.st.save()
        again = C.State.load(self.st.state_path)
        self.assertEqual(again.tasks["T1"]["status"], "running")
        self.assertEqual(again.run_id, self.st.run_id)

    def test_resume_without_a_grant_asks_and_keeps_the_log(self):
        # resume re-checks the grant (spec section 6): with none, it asks and stops.
        self.st.log("start", task="T1")
        self.st.set_status("T1", "running")
        self.st.save()
        before = read_text(self.st.log_path)
        rc, out, _ = run_main(["resume", "--root", self.tmp])
        self.assertEqual(rc, 3)
        lines = out.splitlines()
        self.assertEqual(lines[0], "STATUS: run=run-20260922T120000Z-a1b2c3 last_event=start at=%s"
                         % json.loads(before.splitlines()[-1])["at"])
        self.assertIn("GATE: ASK reason=no-grant", lines)
        self.assertNotIn("READY:", out)
        self.assertTrue(read_text(self.st.log_path).startswith(before))

    @unittest.skipUnless(HAVE_GIT, "git not installed")
    def test_resume_with_a_grant_reports_the_last_step_and_keeps_the_log(self):
        root = repo()
        env = write_plan_envelope(root, plan=plan({"T1": [], "T2": ["T1"]}))
        write_grant(root, env)
        self.assertEqual(run_main(["init", "--plan", env, "--root", root])[0], 0)
        st = C.State.load(C.state_path(root))
        st.log("start", task="T1")
        st.set_status("T1", "running")
        st.save()
        before = read_text(st.log_path)
        rc, out, _ = run_main(["resume", "--root", root])
        self.assertEqual(rc, 0)
        # T1 running, T2 waits on it: nothing ready, T1's executor is re-dispatched
        self.assertEqual(out.splitlines()[-3:],
                         ["READY:", "NEXT: T1 dispatch-executor", "NEXT: run next"])
        self.assertTrue(read_text(st.log_path).startswith(before))


class RunIdTests(unittest.TestCase):
    def test_grammar(self):
        self.assertRegex(C.new_run_id(), C.RUN_ID_RE)


RID = "run-20260922T130000Z-0000%02x"


def new_state(root, tasks, n=0, **kw):
    args = dict(plan_envelope="e.json", plan_sha256="0" * 64, grant_id="g",
                run_branch="factory/p", base_branch="main", budget={"max_parallel": 2})
    args.update(kw)
    return C.State.new(root=root, run_id=RID % n, plan=plan(tasks), **args)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = C.main(argv)
    return rc, out.getvalue(), err.getvalue()


class CorruptState(unittest.TestCase):
    """Fix round 1, item 1: a bad state.json is a StateError and exit 2, never a traceback."""

    def setUp(self):
        self.tmp = tmpdir()
        self.st = new_state(self.tmp, {"T1": []})

    def plant(self, raw):
        with open(self.st.state_path, "wb") as f:
            f.write(raw)

    def check(self, raw):
        self.plant(raw)
        with self.assertRaises(C.StateError):
            C.State.load(self.st.state_path)
        for cmd in ("status", "next", "resume"):
            rc, _, err = run_main([cmd, "--root", self.tmp])
            self.assertEqual(rc, 2, cmd)
            self.assertIn("cannot load run state", err)

    def test_not_json(self):
        self.check(b"{not json")

    def test_empty_object(self):
        self.check(b"{}")

    def test_list(self):
        self.check(b"[]")

    def test_invalid_utf8(self):
        self.check(b"\xff\xfe{")

    def test_tasks_not_a_dict(self):
        data = json.loads(read(self.st.state_path))
        data["tasks"] = ["T1"]
        self.check(json.dumps(data).encode())

    def test_missing_state_on_resume_exits_2(self):
        rc, _, err = run_main(["resume", "--root", tmpdir()])
        self.assertEqual(rc, 2)
        self.assertIn("no run found", err)


class LogRobustness(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        self.st = new_state(self.tmp, {"T1": []})

    def test_torn_line_does_not_swallow_the_next_event(self):
        with open(self.st.log_path, "a", encoding="utf-8") as f:
            f.write('{"at": "2026-09-22T12:00:00Z", "ev')
        self.st.log("start", task="T1")
        self.assertEqual(self.st.last_event()["event"], "start")
        text = read(self.st.log_path)
        self.assertIn('"ev\n', text)  # the torn bytes are kept: append-only

    def test_nan_is_rejected_and_nothing_is_written(self):
        before = read(self.st.log_path)
        with self.assertRaises(ValueError):
            self.st.log("x", v=float("nan"))
        self.assertEqual(read(self.st.log_path), before)

    def test_new_run_logs_init_first(self):
        lines = read(self.st.log_path).splitlines()
        self.assertEqual(len(lines), 1)
        first = json.loads(lines[0])
        self.assertEqual(first["event"], "init")
        self.assertEqual(first["run"], self.st.run_id)
        self.assertEqual(first["waves"], [["T1"]])


class PlanValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()

    def make(self, tasks):
        return C.State.new(root=self.tmp, run_id=RID % 0,
                           plan={"title": "T", "tasks": tasks}, plan_envelope="e",
                           plan_sha256="0" * 64, grant_id="g", run_branch="f",
                           base_branch="main", budget={})

    def test_duplicate_ids_raise(self):
        with self.assertRaises(C.PlanError) as e:
            self.make([{"id": "T1", "depends_on": []}, {"id": "T1", "depends_on": []}])
        self.assertIn("duplicate task id(s): T1", str(e.exception))
        self.assertFalse(os.path.exists(C.run_dir(self.tmp, RID % 0)))

    def test_ids_that_collide_case_insensitively_raise(self):
        # Worktree dirs and branch refs live on case-insensitive filesystems too.
        with self.assertRaises(C.PlanError) as e:
            self.make([{"id": "T1", "depends_on": []}, {"id": "t1", "depends_on": []}])
        self.assertIn("collide case-insensitively: T1, t1", str(e.exception))
        self.assertFalse(os.path.exists(C.run_dir(self.tmp, RID % 0)))

    def test_depends_on_must_be_a_list_of_strings(self):
        for bad in ("T1", 5, [1], [["T1"]]):
            with self.assertRaises(C.PlanError, msg=repr(bad)) as e:
                self.make([{"id": "T1", "depends_on": []}, {"id": "T2", "depends_on": bad}])
            self.assertIn("depends_on", str(e.exception))

    def test_task_must_be_an_object_with_a_string_id(self):
        for bad in (["x"], [{"id": ["x"], "depends_on": []}], [{"depends_on": []}]):
            with self.assertRaises(C.PlanError, msg=repr(bad)):
                self.make(bad)

    def test_self_dependency_is_a_cycle(self):
        with self.assertRaises(C.PlanError) as e:
            C.waves({"T1": {"depends_on": ["T1"]}})
        self.assertIn("cycle", str(e.exception))

    def test_root_is_stored_absolute(self):
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            expected = os.path.abspath(".")  # getcwd resolves symlinks (/var on macOS)
            st = new_state(".", {"T1": []})
        finally:
            os.chdir(cwd)
        self.assertTrue(os.path.isabs(st.root))
        self.assertEqual(st.root, expected)
        self.assertEqual(C.State.load(st.state_path).root, expected)


class Order(unittest.TestCase):
    """Fix round 1, item 4: plan order survives a save and load (T10 after T2)."""

    def test_round_trip_keeps_plan_order(self):
        tmp = tmpdir()
        st = new_state(tmp, {"T1": [], "T2": [], "T10": []}, budget={"max_parallel": 3})
        st.save()
        again = C.State.load(st.state_path)
        self.assertEqual(again.order, ["T1", "T2", "T10"])
        self.assertEqual(again.ready(3), ["T1", "T2", "T10"])
        rc, out, _ = run_main(["status", "--root", tmp])
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        # one line per task, in plan order, then the budget line and the budget note
        self.assertEqual([l.split()[1] for l in lines[:-2]], ["T1", "T2", "T10"])
        self.assertTrue(lines[-2].startswith("STATUS: budget "), lines[-2])
        self.assertEqual(lines[-1], C.BUDGET_NOTE)
        rc, out, _ = run_main(["next", "--root", tmp])
        self.assertEqual(out.strip(), "READY: T1 T2 T10")


class Blocking(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()

    def test_transitive_dependents_are_blocked(self):
        st = new_state(self.tmp, {"T1": [], "T2": ["T1"], "T3": ["T2"], "T4": []})
        st.set_status("T1", "parked", park_reason="r")
        self.assertEqual({k: v["status"] for k, v in st.tasks.items()},
                         {"T1": "parked", "T2": "blocked", "T3": "blocked", "T4": "pending"})
        self.assertEqual(st.ready(2), ["T4"])

    def test_diamond_blocks_only_the_parked_branch(self):
        st = new_state(self.tmp, {"T1": [], "T2": ["T1"], "T3": ["T1"], "T4": ["T2", "T3"]})
        st.set_status("T1", "proven")
        st.set_status("T2", "parked", park_reason="r")
        self.assertEqual(st.tasks["T3"]["status"], "pending")
        self.assertEqual(st.tasks["T4"]["status"], "blocked")
        self.assertEqual(st.ready(2), ["T3"])

    def test_verifying_and_reviewing_count_against_the_cap(self):
        st = new_state(self.tmp, {"A": [], "B": [], "C": [], "D": []})
        st.set_status("A", "verifying")
        self.assertEqual(st.ready(2), ["B"])
        st.set_status("B", "reviewing")
        self.assertEqual(st.ready(2), [])
        self.assertEqual(st.ready(3), ["C"])


class RunDirTests(unittest.TestCase):
    def test_rejects_traversal_and_bad_ids(self):
        for rid in ("../../etc", "run-20260922T120000Z-a1b2c3/../x",
                    "run-20260922T120000Z-a1b2c3\n", "run-20260922T120000Z-A1B2C3", "/abs"):
            with self.assertRaises(ValueError, msg=repr(rid)):
                C.run_dir("/r", rid)


@unittest.skipUnless(HAVE_GIT, "git not installed")
class Cli(unittest.TestCase):
    """init takes a task-plan envelope and needs a covering grant (Task 3)."""

    def setUp(self):
        self.root = repo()
        self.plan_path = write_plan_envelope(self.root, plan=plan({"T1": [], "T2": ["T1"]}))
        write_grant(self.root, self.plan_path)

    def test_resume_output(self):
        root = self.root
        self.assertEqual(run_main(["init", "--plan", self.plan_path, "--root", root])[0], 0)
        rc, out, _ = run_main(["resume", "--root", root])
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertRegex(lines[0], r"^STATUS: run=run-\S+ last_event=\S+ at=\S+Z$")
        self.assertEqual(lines[1], "READY: T1")
        st = C.State.load(os.path.join(C.current_run(root), "state.json"))
        self.assertEqual(st.last_event()["event"], "resume")
        # the last step before resume is init's gate decision, logged after init
        self.assertEqual([json.loads(l)["event"] for l in read_text(st.log_path).splitlines()],
                         ["init", "gate", "gate", "resume"])
        self.assertIn("last_event=gate ", lines[0])

    def test_parallel_values_below_one_exit_2(self):
        root = self.root
        for v in ("0", "-1", '"x"', "1.5"):
            self.assertEqual(run_main(["init", "--plan", self.plan_path, "--root", root,
                                       "--budget", '{"max_parallel": %s}' % v])[0], 2, v)
        self.assertIsNone(C.current_run(root))
        self.assertEqual(run_main(["init", "--plan", self.plan_path, "--root", root])[0], 0)
        self.assertEqual(run_main(["next", "--root", root, "--max", "0"])[0], 2)

    def test_budget_max_parallel_below_one_is_rejected(self):
        with self.assertRaises(C.PlanError):
            new_state(tmpdir(), {"T1": []}, budget={"max_parallel": 0})

    def test_init_on_a_root_that_is_a_file_exits_2(self):
        rc, _, err = run_main(["init", "--plan", self.plan_path, "--root", self.plan_path])
        self.assertEqual(rc, 2)
        self.assertTrue(err)

    def test_init_with_bad_depends_on_exits_2(self):
        bad = write_plan_envelope(self.root, plan={"title": "T", "tasks": [
            {"id": "T1", "depends_on": 5}]})
        write_grant(self.root, bad)
        rc, _, err = run_main(["init", "--plan", bad, "--root", self.root])
        self.assertEqual(rc, 2)
        self.assertIn("depends_on", err)
        self.assertIsNone(C.current_run(self.root))


class Hardening(unittest.TestCase):
    def test_waves_rejects_non_list_depends_on(self):
        for bad in ("T1", 5, [1]):
            with self.assertRaises(C.PlanError, msg=repr(bad)):
                C.waves({"T1": {"depends_on": []}, "T2": {"depends_on": bad}})

    def test_verify_red_after_repairs_is_a_park_reason_not_a_stop_reason(self):
        # Item 11: a verify red after max_repairs parks the task; the run goes on.
        self.assertNotIn("verify_red_after_repairs", C.STOP_REASONS)
        with open(os.path.join(os.path.dirname(os.path.abspath(C.__file__)), "schemas",
                               "run-result.v1.json"), encoding="utf-8") as f:
            schema = json.load(f)
        enum = schema["properties"]["stopped"]["oneOf"][1]["properties"]["reason"]["enum"]
        self.assertEqual(enum, list(C.STOP_REASONS))

    def test_naive_datetime_is_read_as_utc(self):
        import datetime as dt
        rid = C.new_run_id(dt.datetime(2026, 1, 1, 12, 0, 0))
        self.assertTrue(rid.startswith("run-20260101T120000Z-"), rid)


if __name__ == "__main__":
    unittest.main()
