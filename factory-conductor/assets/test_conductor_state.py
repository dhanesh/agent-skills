import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C


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
        self.tmp = tempfile.mkdtemp()
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
        self.st.log("init", run="x")
        self.st.log("start", task="T1")
        lines = open(self.st.log_path, encoding="utf-8").read().strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual([json.loads(l)["event"] for l in lines], ["init", "start"])
        self.assertTrue(all(json.loads(l)["at"].endswith("Z") for l in lines))

    def test_save_and_load_round_trip(self):
        self.st.set_status("T1", "running")
        self.st.save()
        again = C.State.load(self.st.state_path)
        self.assertEqual(again.tasks["T1"]["status"], "running")
        self.assertEqual(again.run_id, self.st.run_id)

    def test_resume_reports_the_last_step_and_keeps_the_log(self):
        self.st.log("start", task="T1")
        self.st.set_status("T1", "running")
        self.st.save()
        before = open(self.st.log_path, encoding="utf-8").read()
        rc = C.main(["resume", "--root", self.tmp])
        self.assertEqual(rc, 0)
        self.assertTrue(open(self.st.log_path, encoding="utf-8").read().startswith(before))


class RunIdTests(unittest.TestCase):
    def test_grammar(self):
        self.assertRegex(C.new_run_id(), C.RUN_ID_RE)


if __name__ == "__main__":
    unittest.main()
