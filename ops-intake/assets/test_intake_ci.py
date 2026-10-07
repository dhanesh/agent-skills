import json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake as IN
from intake_testkit import repo, run, no_io

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
CFG = {"sources": {"ci": {"enabled": True, "formats": ["gh-runs-json", "gh-run-jobs-json"]}},
       "ci": {"default_branch": "main"}}


def fx(n):
    return os.path.join(FIX, n)


def imp(root, fmt, path, *extra):
    with no_io():
        return run(root, "import", "--format", fmt, "--source", "ci", *extra, path)


def sync(root):
    with no_io():
        return run(root, "sync")


def items(root):
    return IN.Queue.load(root).items


class CiTests(unittest.TestCase):
    def setUp(self):
        self.root = repo(config=CFG)

    def runs(self):
        return imp(self.root, "gh-runs-json", fx("gh-runs.json"))

    def test_runs_make_no_item_and_keep_only_default_branch_failures(self):
        rc, out = self.runs()
        q = IN.Queue.load(self.root)
        self.assertEqual(q.items, {})
        self.assertEqual(sorted(q.runs), ["1001", "1005"])
        r = q.runs["1001"]
        self.assertEqual((r["workflowName"], r["headBranch"], r["attempt"], r["jobs_imported"]),
                         ("gate", "main", 1, False))
        self.assertIn("IMPORT: ci gh-runs-json ok 0 problems 1", out)  # 1006 exploded
        self.assertEqual(rc, 3)

    def test_unknown_conclusion_is_a_problem_not_a_crash(self):
        rc, out = self.runs()
        self.assertIn("PROBLEM: record 5: unknown conclusion value", out)
        self.assertNotIn("exploded", out)

    def test_cancelled_and_success_runs_give_no_run(self):
        self.runs()
        self.assertNotIn("1002", IN.Queue.load(self.root).runs)
        self.assertNotIn("1003", IN.Queue.load(self.root).runs)

    def test_other_branch_ignored(self):
        self.runs()
        self.assertNotIn("1004", IN.Queue.load(self.root).runs)

    def test_jobs_give_one_item_per_failing_job(self):
        self.runs()
        rc, out = imp(self.root, "gh-run-jobs-json", fx("gh-run-jobs.json"), "--run", "1001")
        self.assertEqual(rc, 0, out)
        its = list(items(self.root).values())
        self.assertEqual(len(its), 1)
        it = its[0]
        self.assertEqual(it["source_id"], "gate/gate (ubuntu-latest)/main")
        self.assertEqual(it["title"], "gate / gate (ubuntu-latest) failing on main")
        self.assertEqual((it["kind"], it["severity"], it["count"]), ("ci", 3, 1))
        self.assertEqual(it["last_seen"], "2026-10-05T10:05:00Z")
        self.assertEqual(it["first_seen"], "2026-10-05T10:05:00Z")
        self.assertIn("make gate", it["evidence"][0]["text"])
        self.assertNotIn("Set up job", it["evidence"][0]["text"])
        self.assertEqual(it["evidence"][0]["key"], "1001:1:9001")
        self.assertTrue(IN.Queue.load(self.root).runs["1001"]["jobs_imported"])

    def test_second_failing_run_raises_count_on_same_item(self):
        self.runs()
        imp(self.root, "gh-run-jobs-json", fx("gh-run-jobs.json"), "--run", "1001")
        imp(self.root, "gh-run-jobs-json", fx("gh-run-jobs-2.json"), "--run", "1005")
        its = list(items(self.root).values())
        self.assertEqual((len(its), its[0]["count"]), (1, 2))
        self.assertEqual(its[0]["last_seen"], "2026-10-06T10:05:00Z")

    def test_same_jobs_twice_do_not_double_count(self):
        self.runs()
        for _ in range(2):
            imp(self.root, "gh-run-jobs-json", fx("gh-run-jobs.json"), "--run", "1001")
        self.assertEqual(list(items(self.root).values())[0]["count"], 1)

    def test_later_failure_regresses_a_dismissed_item(self):
        self.runs()
        imp(self.root, "gh-run-jobs-json", fx("gh-run-jobs.json"), "--run", "1001")
        iid = list(items(self.root))[0]
        q = IN.Queue.load(self.root)
        IN.transition(q.items[iid], "dismissed", by="me", reason="flaky", now="2026-10-05T12:00:00Z")
        q.save()
        imp(self.root, "gh-run-jobs-json", fx("gh-run-jobs-2.json"), "--run", "1005")
        it = items(self.root)[iid]
        self.assertEqual((it["state"], it["regressed"]), ("new", True))

    def test_earlier_failure_does_not_regress(self):
        self.runs()
        imp(self.root, "gh-run-jobs-json", fx("gh-run-jobs.json"), "--run", "1001")
        iid = list(items(self.root))[0]
        q = IN.Queue.load(self.root)
        IN.transition(q.items[iid], "dismissed", by="me", reason="x", now="2026-10-07T00:00:00Z")
        q.save()
        imp(self.root, "gh-run-jobs-json", fx("gh-run-jobs-2.json"), "--run", "1005")
        self.assertEqual(items(self.root)[iid]["state"], "dismissed")

    def test_jobs_for_unknown_run_exit_2(self):
        rc, out = imp(self.root, "gh-run-jobs-json", fx("gh-run-jobs.json"), "--run", "999")
        self.assertEqual(rc, 2)
        self.assertIn("STOP: run-not-imported 999", out)
        self.assertEqual(items(self.root), {})

    def test_jobs_need_run_and_run_is_digits(self):
        self.runs()
        rc, out = imp(self.root, "gh-run-jobs-json", fx("gh-run-jobs.json"))
        self.assertEqual(rc, 2)
        rc, out = imp(self.root, "gh-run-jobs-json", fx("gh-run-jobs.json"), "--run", "10;rm")
        self.assertEqual(rc, 2)

    def test_sync_prints_next_for_runs_without_jobs(self):
        self.runs()
        rc, out = sync(self.root)
        self.assertEqual(rc, 0)
        self.assertIn("NEXT: gh run view 1001 --json jobs | intake import --format "
                      "gh-run-jobs-json --source ci --run 1001", out)
        self.assertIn("--run 1005", out)
        self.assertEqual(items(self.root), {})
        imp(self.root, "gh-run-jobs-json", fx("gh-run-jobs.json"), "--run", "1001")
        self.assertNotIn("--run 1001", sync(self.root)[1])

    def test_reimporting_runs_keeps_jobs_imported(self):
        self.runs()
        imp(self.root, "gh-run-jobs-json", fx("gh-run-jobs.json"), "--run", "1001")
        self.runs()
        self.assertTrue(IN.Queue.load(self.root).runs["1001"]["jobs_imported"])

    def test_unknown_job_conclusion_is_a_problem(self):
        self.runs()
        p = os.path.join(self.root, "j.json")
        with open(p, "w") as f:
            json.dump({"jobs": [{"databaseId": 1, "name": "x", "status": "completed",
                                 "conclusion": "weird", "url": "u"}, 5]}, f)
        rc, out = imp(self.root, "gh-run-jobs-json", p, "--run", "1001")
        self.assertEqual(rc, 3)
        self.assertIn("job 0: unknown conclusion value", out)
        self.assertIn("job 1: not an object", out)

    def test_bad_inputs(self):
        for raw in ("not json", "{}", "[1, null]"):
            p = os.path.join(self.root, "b.json")
            with open(p, "w") as f:
                f.write(raw)
            self.assertEqual(imp(self.root, "gh-runs-json", p)[0], 3)
            self.assertEqual(imp(self.root, "gh-run-jobs-json", p, "--run", "1")[0], 2)

    def test_workflow_filter(self):
        root = repo(config=dict(CFG, ci={"default_branch": "main", "workflows": ["other"]}))
        imp(root, "gh-runs-json", fx("gh-runs.json"))
        self.assertEqual(IN.Queue.load(root).runs, {})

    def test_corrupt_runs_stop_the_queue(self):
        self.runs()
        path = os.path.join(self.root, ".skill-contract/intake/queue.json")
        with open(path) as f:
            data = json.load(f)
        for bad in ([], {"1": 5}, {"x": data["runs"]["1001"]}):
            data["runs"] = bad
            with open(path, "w") as f:
                json.dump(data, f)
            rc, out = sync(self.root)
            self.assertEqual(rc, 2)
            self.assertIn("STOP: queue-unreadable", out)

    def test_bad_jobs_payload_keeps_the_next_line(self):
        self.runs()
        for n, raw in enumerate(("not json", "{}", '{"jobs": 5}'), 1):
            p = os.path.join(self.root, "b.json")
            with open(p, "w") as f:
                f.write(raw)
            rc, out = imp(self.root, "gh-run-jobs-json", p, "--run", "1001")
            self.assertEqual(rc, 3)
            if n < IN.JOBS_TRIES:
                self.assertIn("--run 1001", sync(self.root)[1])
            else:  # R26: the third failure ends the NEXT line, with one problem
                self.assertIn("failed 3 times", out)
                self.assertNotIn("--run 1001", sync(self.root)[1])

    def test_truncated_jobs_import_keeps_the_next_line(self):
        self.runs()
        jobs = [{"databaseId": i, "name": "j%d" % i, "status": "completed",
                 "conclusion": "failure", "url": "u", "completedAt": "2026-10-05T10:05:00Z"}
                for i in range(1, 5)]
        p = os.path.join(self.root, "j.json")
        with open(p, "w") as f:
            json.dump({"jobs": jobs}, f)
        import adapters
        old, adapters.MAX_SIGNALS = adapters.MAX_SIGNALS, 2
        try:
            rc, _ = imp(self.root, "gh-run-jobs-json", p, "--run", "1001")
        finally:
            adapters.MAX_SIGNALS = old
        self.assertEqual(rc, 3)
        self.assertIn("--run 1001", sync(self.root)[1])

    def test_per_job_problem_still_marks_imported(self):
        self.runs()
        p = os.path.join(self.root, "j.json")
        with open(p, "w") as f:
            json.dump({"jobs": [5]}, f)
        imp(self.root, "gh-run-jobs-json", p, "--run", "1001")
        self.assertNotIn("--run 1001", sync(self.root)[1])

    def test_default_branch_required_for_ci_formats(self):
        for cfg in ({"sources": CFG["sources"]}, dict(CFG, ci={"workflows": ["a"]})):
            root = repo(config=cfg)
            rc, out = imp(root, "gh-runs-json", fx("gh-runs.json"))
            self.assertEqual(rc, 2)
            self.assertIn("STOP: config: ci.default_branch is required", out)
        ok = {"sources": {"jira": {"enabled": True, "formats": ["intake-signals-jsonl"]}}}
        self.assertEqual(IN.config_problems(ok), [])

    def test_first_seen_is_earliest_whatever_the_import_order(self):
        self.runs()
        imp(self.root, "gh-run-jobs-json", fx("gh-run-jobs-2.json"), "--run", "1005")
        imp(self.root, "gh-run-jobs-json", fx("gh-run-jobs.json"), "--run", "1001")
        it = list(items(self.root).values())[0]
        self.assertEqual((it["first_seen"], it["last_seen"]),
                         ("2026-10-05T10:05:00Z", "2026-10-06T10:05:00Z"))

    def test_job_attempt_wins_else_run_attempt(self):
        self.runs()
        p = os.path.join(self.root, "j.json")
        base = {"name": "x", "status": "completed", "conclusion": "failure", "url": "u"}
        with open(p, "w") as f:
            json.dump({"jobs": [dict(base, databaseId=1, attempt=3), dict(base, databaseId=2)]}, f)
        imp(self.root, "gh-run-jobs-json", p, "--run", "1001")
        keys = {e["key"] for e in list(items(self.root).values())[0]["evidence"]}
        self.assertEqual(keys, {"1001:3:1", "1001:1:2"})

    def test_missing_completed_at_falls_back_to_run_updated_at(self):
        self.runs()
        p = os.path.join(self.root, "j.json")
        with open(p, "w") as f:
            json.dump({"jobs": [{"databaseId": 1, "name": "x", "status": "completed",
                                 "conclusion": "failure", "url": "u"}]}, f)
        imp(self.root, "gh-run-jobs-json", p, "--run", "1001")
        it = list(items(self.root).values())[0]
        self.assertEqual((it["first_seen"], it["last_seen"]),
                         ("2026-10-05T10:00:00Z", "2026-10-05T10:00:00Z"))


class JobsSourceTests(unittest.TestCase):
    """R18: the jobs NEXT line names the configured source, not a fixed `ci`."""

    def setUp(self):
        self.root = repo(config={
            "sources": {"actions": {"enabled": True, "formats": ["gh-runs-json", "gh-run-jobs-json"]},
                        "nightly": {"enabled": True, "formats": ["gh-runs-json", "gh-run-jobs-json"]}},
            "ci": {"default_branch": "main"}})

    def imp(self, source, fmt, path, *extra):
        with no_io():
            return run(self.root, "import", "--format", fmt, "--source", source, *extra, path)

    def test_the_next_line_names_the_source_that_imported_the_run_and_works(self):
        self.imp("nightly", "gh-runs-json", fx("gh-runs.json"))
        self.assertEqual(IN.Queue.load(self.root).runs["1001"]["source"], "nightly")
        out = sync(self.root)[1]
        line = next(l for l in out.splitlines() if "--run 1001" in l)
        self.assertIn("--source nightly --run 1001", line)
        self.assertNotIn("--source ci", out)
        argv = line.split(" | intake ", 1)[1].split()
        with no_io():
            rc, got = run(self.root, *argv, fx("gh-run-jobs.json"))
        self.assertEqual(rc, 0, got)
        self.assertNotIn("--run 1001", sync(self.root)[1])
        self.assertTrue(any(i["source"] == "nightly" for i in items(self.root).values()))

    def test_a_run_from_a_source_without_the_jobs_format_points_at_one_that_has_it(self):
        self.assertEqual(IN._jobs_source({"sources": {
            "a": {"enabled": True, "formats": ["gh-runs-json"]},
            "b": {"enabled": True, "formats": ["gh-run-jobs-json"]}}}, {"source": "a"}), "b")


if __name__ == "__main__":
    unittest.main()
