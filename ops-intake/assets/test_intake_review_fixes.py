"""The final whole-branch review fixes (rulings R23-R27): one test class per finding."""
import io, json, os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake as IN
from intake_testkit import repo, run, no_io
import test_intake_sync as TS
from test_intake_sync import NOW, issue, items, release_env


class _Loop(unittest.TestCase):
    """LoopTests' fixture (a picked item, a git repo, plan/run/release helpers) without
    its tests, so they do not run twice."""
    setUp = TS.LoopTests.setUp
    plan = TS.LoopTests.plan
    run_result = TS.LoopTests.run_result
    history = TS.LoopTests.history
    flag = TS.LoopTests.flag

CI = {"sources": {"ci": {"enabled": True, "formats": ["gh-runs-json", "gh-run-jobs-json"]},
                  "gh": {"enabled": True, "formats": ["gh-issues-json"]},
                  "mine": {"enabled": True, "formats": ["intake-signals-jsonl"]}},
      "ci": {"default_branch": "main"}}


def a_run(rid=7, conclusion="failure", attempt=1, workflow="CI"):
    return {"databaseId": rid, "workflowName": workflow, "headBranch": "main",
            "headSha": "a" * 40, "event": "push", "status": "completed",
            "conclusion": conclusion, "createdAt": "2026-10-01T00:00:00Z",
            "updatedAt": "2026-10-01T00:05:00Z", "url": "https://example.invalid/r",
            "attempt": attempt}


def an_issue(n, state="OPEN", updated="2026-10-01T00:00:00Z", title=None):
    return {"number": n, "title": title or "t%d" % n, "url": "https://example.invalid/%d" % n,
            "body": "body %d" % n, "labels": [], "state": state,
            "createdAt": "2026-10-01T00:00:00Z", "updatedAt": updated}


def imp(root, fmt, source, text, *extra, now=NOW):
    with mock.patch.object(IN, "_now", return_value=now), no_io(), \
            mock.patch.object(sys, "stdin", io.StringIO(text)):
        return run(root, "import", "--format", fmt, "--source", source, *extra, "-")


def cmd(root, *argv, now=NOW):
    with mock.patch.object(IN, "_now", return_value=now), no_io():
        return run(root, *argv)


def write_queue(root, mutate):
    """Save a queue, then let `mutate` edit its JSON on disk."""
    path = os.path.join(root, ".skill-contract", "intake", "queue.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    mutate(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


# -- A3 ---------------------------------------------------------------------------
class OversizeRunIdTests(unittest.TestCase):
    def test_a_run_id_over_20_digits_is_a_record_problem_not_a_bricked_queue(self):
        root = repo(config=CI)
        rc, out = imp(root, "gh-runs-json", "ci", json.dumps([a_run(10 ** 23), a_run(8)]))
        self.assertEqual(rc, 3, out)
        self.assertIn("PROBLEM: record 0: databaseId must be at most 20 digits", out)
        self.assertEqual(sorted(IN.Queue.load(root).runs), ["8"])
        rc, out = cmd(root, "sync")
        self.assertNotIn("STOP:", out)
        self.assertEqual(cmd(root, "status")[0], 0)


# -- A4 ---------------------------------------------------------------------------
class HiddenCharacterTests(unittest.TestCase):
    def test_code_writes_hidden_characters_as_escapes(self):
        self.assertEqual(IN.code("a\x1b[31mb‮c​d\x00"),
                         "`a\\u001b[31mb\\u202ec\\u200bd\\u0000`")
        self.assertEqual(IN.code("plain `x`"), "`` plain `x` ``")

    def test_list_and_show_print_no_raw_escape_or_bidi_character(self):
        root = repo(config=CI)
        imp(root, "gh-issues-json", "gh",
            json.dumps([an_issue(1, title="evil \x1b]0;pwned\x07 ‮gnp.exe")]))
        for argv in (("list",), ("show", IN.item_id("gh", "1"))):
            out = cmd(root, *argv)[1]
            self.assertFalse(any(c in out for c in "\x1b\x07‮"), out)
            self.assertIn("\\u202e", out)


# -- A5 ---------------------------------------------------------------------------
class JobsSourceCheckTests(unittest.TestCase):
    def test_a_queue_source_that_is_not_a_plain_word_never_reaches_a_next_line(self):
        cfg = {"sources": {"ci": {"enabled": True, "formats": ["gh-runs-json"]}},
               "ci": {"default_branch": "main"}}
        self.assertEqual(IN._jobs_source(cfg, {"source": "x;touch /tmp/p"}), "ci")
        self.assertEqual(IN._jobs_source(cfg, {"source": "ci"}), "ci")
        self.assertEqual(IN._jobs_source(cfg, {"source": 5}), "ci")


# -- A6 ---------------------------------------------------------------------------
class GitignoreTests(unittest.TestCase):
    def test_an_existing_gitignore_without_star_is_rewritten(self):
        root = repo(config=CI)
        d = os.path.join(root, ".skill-contract", "intake")
        os.makedirs(d)
        with open(os.path.join(d, ".gitignore"), "w") as f:
            f.write("!queue.json\n")
        cmd(root, "sync")
        with open(os.path.join(d, ".gitignore")) as f:
            self.assertIn("*", f.read().splitlines())

    def test_a_good_gitignore_is_left_alone(self):
        root = repo(config=CI)
        d = os.path.join(root, ".skill-contract", "intake")
        os.makedirs(d)
        with open(os.path.join(d, ".gitignore"), "w") as f:
            f.write("# mine\n*\n")
        cmd(root, "sync")
        with open(os.path.join(d, ".gitignore")) as f:
            self.assertEqual(f.read(), "# mine\n*\n")


# -- B1 ---------------------------------------------------------------------------
class RunLevelSignalTests(unittest.TestCase):
    def setUp(self):
        self.root = repo(config=CI)

    def test_startup_failure_with_no_jobs_gives_a_run_level_item(self):
        imp(self.root, "gh-runs-json", "ci", json.dumps([a_run(7, "startup_failure")]))
        rc, out = imp(self.root, "gh-run-jobs-json", "ci", json.dumps({"jobs": []}), "--run", "7")
        self.assertEqual(rc, 0, out)
        it = items(self.root)[IN.item_id("ci", "CI/(run)/main")]
        self.assertEqual((it["kind"], it["severity"], it["state"]), ("ci", 3, "new"))
        self.assertIn("startup_failure", it["evidence"][0]["text"])
        self.assertTrue(IN.Queue.load(self.root).runs["7"]["jobs_imported"])

    def test_failing_run_whose_jobs_all_passed_gives_a_run_level_item(self):
        imp(self.root, "gh-runs-json", "ci", json.dumps([a_run(7)]))
        jobs = {"jobs": [{"databaseId": 1, "name": "j", "url": "u", "status": "completed",
                          "conclusion": "success", "steps": []}]}
        imp(self.root, "gh-run-jobs-json", "ci", json.dumps(jobs), "--run", "7")
        self.assertEqual([i["source_id"] for i in items(self.root).values()], ["CI/(run)/main"])

    def test_a_failing_job_gives_no_run_level_item(self):
        imp(self.root, "gh-runs-json", "ci", json.dumps([a_run(7)]))
        jobs = {"jobs": [{"databaseId": 1, "name": "j", "url": "u", "status": "completed",
                          "conclusion": "failure", "steps": []}]}
        imp(self.root, "gh-run-jobs-json", "ci", json.dumps(jobs), "--run", "7")
        self.assertEqual([i["source_id"] for i in items(self.root).values()], ["CI/j/main"])


# -- B2 ---------------------------------------------------------------------------
class LinkAliasTests(unittest.TestCase):
    def setUp(self):
        self.root = repo(config=CI)
        imp(self.root, "gh-issues-json", "gh", json.dumps([an_issue(1), an_issue(2), an_issue(3)]))
        self.a, self.b, self.c = (IN.item_id("gh", n) for n in "123")

    def test_reimport_after_link_lands_on_the_kept_item(self):
        self.assertEqual(cmd(self.root, "link", self.a, self.b)[0], 0)
        imp(self.root, "gh-issues-json", "gh",
            json.dumps([an_issue(1), an_issue(2, updated="2026-10-02T00:00:00Z")]))
        its = items(self.root)
        self.assertNotIn(self.b, its)
        self.assertEqual(its[self.a]["last_seen"], "2026-10-02T00:00:00Z")
        self.assertEqual(IN.Queue.load(self.root).aliases, {self.b: self.a})

    def test_linking_a_kept_item_repoints_its_aliases(self):
        cmd(self.root, "link", self.a, self.b)
        cmd(self.root, "link", self.c, self.a)
        self.assertEqual(IN.Queue.load(self.root).aliases, {self.b: self.c, self.a: self.c})
        imp(self.root, "gh-issues-json", "gh", json.dumps([an_issue(1), an_issue(2)]))
        self.assertEqual(sorted(items(self.root)), [self.c])

    def test_a_broken_alias_map_stops_the_queue(self):
        cmd(self.root, "link", self.a, self.b)
        for bad in ({self.b: "I0000000000"}, {self.a: self.c}, {"x": self.a}, []):
            write_queue(self.root, lambda d: d.update(aliases=bad))
            rc, out = cmd(self.root, "status")
            self.assertEqual(rc, 2, bad)
            self.assertIn("STOP: queue-unreadable", out)


# -- B3 ---------------------------------------------------------------------------
class ReleaseTimeTests(_Loop):
    def resolve(self, now):
        self.plan()
        self.run_result(self.m)
        release_env(self.root, "2.0.0", self.r, "verified")
        self.history()
        return cmd(self.root, "sync", now=now)

    def test_closed_at_is_the_release_time_not_the_sync_time(self):
        self.resolve("2026-10-20T00:00:00Z")
        it = items(self.root)[self.iid]
        self.assertEqual((it["state"], it["closed_at"]), ("resolved", "2026-10-09T00:00:00Z"))

    def test_a_signal_between_release_and_sync_recurs(self):
        self.resolve("2026-10-20T00:00:00Z")
        q = IN.Queue.load(self.root)
        IN.apply_signals(q, [dict(_sig("42"), last_seen="2026-10-10T00:00:00Z")], NOW)
        self.assertEqual((q.items[self.iid]["state"], q.items[self.iid]["regressed"]),
                         ("new", True))

    def test_last_seen_after_the_release_resolves_then_recurs(self):
        issue(self.root, "42", last_seen="2026-10-10T00:00:00Z", keys=("k2",))
        self.resolve("2026-10-20T00:00:00Z")
        it = items(self.root)[self.iid]
        self.assertEqual((it["state"], it["regressed"]), ("new", True))

    def test_a_closed_issue_updated_after_the_release_does_not_recur(self):
        # "Fixes #42" closed it; a bot comment after the release moves updatedAt. A closed
        # issue never counts as recurrence in apply_signals, so it must not here either.
        q = IN.Queue.load(self.root)
        IN.apply_signals(q, [dict(_sig("42"), closed=True, last_seen="2026-10-10T00:00:00Z")],
                         NOW)
        q.save()
        self.resolve("2026-10-20T00:00:00Z")
        it = items(self.root)[self.iid]
        self.assertEqual((it["state"], it["regressed"]), ("resolved", False))

    def test_a_release_status_reimport_after_the_release_does_not_recur(self):
        # release-status has no real time (recurrence False): a re-import stamps last_seen
        # with the import time, which is not evidence that the fix failed.
        q = IN.Queue.load(self.root)
        IN.apply_signals(q, [dict(_sig("42"), recurrence=False,
                                  last_seen="2026-10-10T00:00:00Z")], NOW)
        q.save()
        self.resolve("2026-10-20T00:00:00Z")
        it = items(self.root)[self.iid]
        self.assertEqual((it["state"], it["regressed"]), ("resolved", False))


def _sig(sid):
    return {"source": "github", "source_id": sid, "url": "https://example.invalid/" + sid,
            "kind": "issue", "title": "Broken thing", "severity": 3,
            "first_seen": "2026-09-01T00:00:00Z", "last_seen": "2026-10-05T00:00:00Z",
            "trust": "normal", "evidence": [{"text": "again", "source": "github",
                                             "source_id": sid, "fetched_at": NOW, "key": "k9"}]}


# -- B4 ---------------------------------------------------------------------------
class JobsImportFailureTests(unittest.TestCase):
    def setUp(self):
        self.root = repo(config=CI)
        imp(self.root, "gh-runs-json", "ci", json.dumps([a_run(7)]))

    def fail_jobs(self):
        return imp(self.root, "gh-run-jobs-json", "ci", "", "--run", "7")

    def test_three_failed_jobs_imports_end_the_next_line_with_one_problem(self):
        for _ in range(2):
            self.fail_jobs()
            self.assertIn("--run 7", cmd(self.root, "sync")[1])
        # A re-import of the same run attempt keeps the count.
        imp(self.root, "gh-runs-json", "ci", json.dumps([a_run(7)]))
        rc, out = self.fail_jobs()
        self.assertEqual(rc, 3)
        self.assertEqual(sum("failed 3 times" in l for l in out.splitlines()), 1, out)
        rc, out = cmd(self.root, "sync")
        self.assertNotIn("--run 7", out)
        self.assertNotIn("failed 3 times", out)

    def test_an_empty_jobs_array_is_not_a_failure(self):
        self.fail_jobs()
        imp(self.root, "gh-run-jobs-json", "ci", json.dumps({"jobs": []}), "--run", "7")
        r = IN.Queue.load(self.root).runs["7"]
        self.assertEqual((r["jobs_imported"], r["jobs_failures"]), (True, 1))

    # The old test here asserted that a 30-day-old run whose jobs never came in was dropped
    # silently. That encoded the bug (a failing CI run vanished with no PROBLEM and no NEXT).
    def events(self, name):
        path = os.path.join(self.root, IN.INTAKE_DIR, "intake-log.jsonl")
        return [e for e in map(json.loads, open(path)) if e["event"] == name]

    def old_run_sync(self, rid=7):
        return cmd(self.root, "sync", now="2026-11-20T00:00:00Z")

    def test_old_run_without_jobs_is_kept_with_one_problem_and_its_next(self):
        rc, out = self.old_run_sync()
        self.assertIn("7", IN.Queue.load(self.root).runs)
        self.assertEqual(sum("run 7 is older than 30 days" in l and l.startswith("PROBLEM:")
                             for l in out.splitlines()), 1, out)
        self.assertIn("--run 7", out)
        self.assertEqual(len(self.events("stale-run")), 1)
        self.assertEqual(self.events("prune"), [])
        rc, out = self.old_run_sync()   # one PROBLEM per sync, every sync
        self.assertEqual(out.count("older than 30 days"), 1, out)
        self.assertIn("--run 7", out)

    def test_old_run_with_jobs_imported_is_pruned_and_logged(self):
        imp(self.root, "gh-run-jobs-json", "ci", json.dumps({"jobs": []}), "--run", "7")
        rc, out = self.old_run_sync()
        self.assertEqual(IN.Queue.load(self.root).runs, {})
        self.assertNotIn("PROBLEM", out)
        self.assertNotIn("--run 7", out)
        ev = self.events("prune")
        self.assertEqual((len(ev), ev[0]["run"], ev[0]["reason"]), (1, "7", "jobs-imported"))

    def test_old_run_with_jobs_tries_failures_is_pruned(self):
        for _ in range(IN.JOBS_TRIES):
            self.fail_jobs()
        rc, out = self.old_run_sync()
        self.assertEqual(IN.Queue.load(self.root).runs, {})
        self.assertNotIn("older than 30 days", out)
        self.assertEqual(self.events("prune")[0]["reason"], "jobs-failed")

    def test_runs_under_30_days_old_are_kept_quietly(self):
        rc, out = cmd(self.root, "sync", now="2026-10-20T00:00:00Z")
        self.assertNotIn("older than 30 days", out)
        self.assertEqual(self.events("stale-run"), [])

    def test_reimport_of_an_imported_run_keeps_the_first_import_time_and_no_next(self):
        imp(self.root, "gh-run-jobs-json", "ci", json.dumps({"jobs": []}), "--run", "7")
        imp(self.root, "gh-runs-json", "ci", json.dumps([a_run(7)]), now="2026-10-25T00:00:00Z")
        r = IN.Queue.load(self.root).runs["7"]
        self.assertEqual((r["imported_at"], r["jobs_imported"]), (NOW, True))
        self.assertNotIn("--run 7", cmd(self.root, "sync", now="2026-10-26T00:00:00Z")[1])


class DropRunTests(unittest.TestCase):
    def setUp(self):
        self.root = repo(config=CI)
        imp(self.root, "gh-runs-json", "ci", json.dumps([a_run(7)]))

    def test_drop_run_removes_the_run_and_logs_it(self):
        rc, out = cmd(self.root, "drop-run", "7", "--by", "ann", "--reason", "gone")
        self.assertEqual(rc, 0, out)
        self.assertEqual(IN.Queue.load(self.root).runs, {})
        path = os.path.join(self.root, IN.INTAKE_DIR, "intake-log.jsonl")
        ev = [e for e in map(json.loads, open(path)) if e["event"] == "drop-run"]
        self.assertEqual((ev[0]["run"], ev[0]["by"], ev[0]["reason"]), ("7", "ann", "gone"))
        self.assertNotIn("--run 7", cmd(self.root, "sync")[1])

    def test_unknown_run_id_is_refused(self):
        rc, out = cmd(self.root, "drop-run", "8", "--by", "ann", "--reason", "x")
        self.assertEqual(rc, 2)
        self.assertIn("STOP:", out)
        self.assertEqual(list(IN.Queue.load(self.root).runs), ["7"])

    def test_non_digit_run_id_is_refused(self):
        rc, out = cmd(self.root, "drop-run", "7x", "--by", "ann", "--reason", "x")
        self.assertEqual(rc, 2)
        self.assertIn("STOP:", out)

    def test_by_and_reason_are_required(self):
        for argv in (("7", "--reason", "x"), ("7", "--by", "ann")):
            rc, _ = cmd(self.root, "drop-run", *argv)
            self.assertEqual(rc, 2)
        self.assertEqual(list(IN.Queue.load(self.root).runs), ["7"])


# -- B5 ---------------------------------------------------------------------------
class ClosedFirstSeenTests(unittest.TestCase):
    def test_a_closed_issue_seen_first_creates_no_item(self):
        root = repo(config=CI)
        rc, out = imp(root, "gh-issues-json", "gh", json.dumps([an_issue(3, "CLOSED")]))
        self.assertEqual(rc, 0, out)
        self.assertEqual(items(root), {})

    def test_a_known_issue_that_closes_is_still_marked_closed(self):
        root = repo(config=CI)
        imp(root, "gh-issues-json", "gh", json.dumps([an_issue(3)]))
        imp(root, "gh-issues-json", "gh", json.dumps([an_issue(3, "CLOSED")]))
        self.assertTrue(items(root)[IN.item_id("gh", "3")]["closed"])


# -- B6 ---------------------------------------------------------------------------
class DismissAndLinkGuardTests(_Loop):
    def test_a_picked_item_can_be_dismissed_with_a_reason(self):
        self.assertEqual(cmd(self.root, "dismiss", self.iid, "--by", "D", "--reason", " ")[0], 2)
        rc, out = cmd(self.root, "dismiss", self.iid, "--by", "D", "--reason", "dup")
        self.assertEqual(rc, 0, out)
        self.assertEqual(items(self.root)[self.iid]["state"], "dismissed")

    def test_a_planned_item_can_be_dismissed(self):
        self.plan()
        cmd(self.root, "sync")
        rc, out = cmd(self.root, "dismiss", self.iid, "--by", "D", "--reason", "wontfix")
        self.assertEqual(rc, 0, out)
        self.assertEqual(items(self.root)[self.iid]["state"], "dismissed")

    def test_link_never_deletes_a_picked_or_planned_item(self):
        other = issue(self.root, "43")
        rc, out = cmd(self.root, "link", other, self.iid)
        self.assertEqual(rc, 2)
        self.assertIn("STOP:", out)
        self.assertIn(self.iid, items(self.root))


# -- B7 ---------------------------------------------------------------------------
class QueueFieldTypeTests(unittest.TestCase):
    def setUp(self):
        self.root = repo(config=CI)
        imp(self.root, "gh-issues-json", "gh", json.dumps([an_issue(1)]))
        self.iid = IN.item_id("gh", "1")

    def test_wrong_field_types_stop_cleanly(self):
        bad = [lambda d: d["items"][self.iid].update(evidence="x"),
               lambda d: d["items"][self.iid].update(evidence=["x"]),
               lambda d: d["items"][self.iid].update(last_seen=5),
               lambda d: d["items"][self.iid].update(closed_at=[]),
               lambda d: d["items"][self.iid].update(state_at={}),
               lambda d: d.update(version=2)]
        for n, mutate in enumerate(bad):
            imp(self.root, "gh-issues-json", "gh", json.dumps([an_issue(1)]))
            write_queue(self.root, mutate)
            for argv in (("status",), ("list",), ("sync",)):
                rc, out = cmd(self.root, *argv)
                self.assertEqual(rc, 2, (n, argv, out))
                self.assertIn("STOP: queue-unreadable", out)
            os.unlink(os.path.join(self.root, ".skill-contract", "intake", "queue.json"))

    def test_null_times_load_and_survive_import_and_sync(self):
        write_queue(self.root, lambda d: d["items"][self.iid].update(
            last_seen=None, closed_at=None, state_at=None))
        rc, out = imp(self.root, "gh-issues-json", "gh", json.dumps([an_issue(1)]))
        self.assertEqual(rc, 0, out)
        self.assertEqual(cmd(self.root, "sync")[0], 0)


# -- B8 ---------------------------------------------------------------------------
class NeedsReleaseTests(_Loop):
    def test_a_run_newer_than_every_verified_release_flags_needs_release(self):
        self.plan()
        self.run_result(self.m)
        cmd(self.root, "sync")
        self.assertEqual(self.flag(), "planned+needs-release")
        release_env(self.root, "1.9.0", self.r, "verified", now="2026-10-07T12:00:00Z")
        cmd(self.root, "sync")
        self.assertEqual(self.flag(), "planned+needs-release")


# -- C1 ---------------------------------------------------------------------------
class TrustShownTests(unittest.TestCase):
    def test_list_and_show_mark_low_trust_items(self):
        root = repo(config=CI)
        sig = {"source": "mine", "source_id": "s1", "kind": "issue", "title": "x",
               "severity": 2, "first_seen": NOW, "last_seen": NOW}
        imp(root, "intake-signals-jsonl", "mine", json.dumps(sig) + "\n")
        imp(root, "gh-issues-json", "gh", json.dumps([an_issue(1)]))
        low, normal = IN.item_id("mine", "s1"), IN.item_id("gh", "1")
        out = cmd(root, "list")[1]
        self.assertIn("ITEM: %s new+low-trust " % low, out)
        self.assertIn("ITEM: %s new " % normal, out)
        self.assertIn("ITEM: %s new+low-trust " % low, cmd(root, "show", low)[1])


if __name__ == "__main__":
    unittest.main()
