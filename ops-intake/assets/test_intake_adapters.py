import ast, copy, json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake as IN
import adapters as AD
from intake_testkit import repo, run, no_io

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
CFG = {"sources": {"github": {"enabled": True, "formats": ["gh-issues-json"]},
                   "jira": {"enabled": True, "formats": ["intake-signals-jsonl"]},
                   "off": {"enabled": False, "formats": ["gh-issues-json"]},
                   "ci": {"enabled": True, "formats": ["gh-runs-json", "gh-run-jobs-json"]},
                   "release": {"enabled": True, "formats": ["release-envelope", "release-status"]}},
       "github": {"repo": "o/r"}, "ci": {"default_branch": "main"}}


def fx(name):
    return os.path.join(FIX, name)


def read(name):
    with open(fx(name), encoding="utf-8") as f:
        return f.read()


def write(root, name, text):
    p = os.path.join(root, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def imp(root, fmt, source, path, *extra):
    with no_io():
        return run(root, "import", "--format", fmt, "--source", source, *extra, path)


CTX = {"source": "github", "now": "2026-10-06T00:00:00Z"}


class ParseTimeTests(unittest.TestCase):
    def test_accepted_forms(self):
        self.assertEqual(AD.parse_time("2026-10-05T08:20:45Z"), "2026-10-05T08:20:45Z")
        self.assertEqual(AD.parse_time("2026-10-05T08:20:45+05:30"), "2026-10-05T02:50:45Z")
        self.assertEqual(AD.parse_time("2026-10-05T08:20:45.000+0000"), "2026-10-05T08:20:45Z")
        self.assertEqual(AD.parse_time("2026-10-05T08:20:45.5-0130"), "2026-10-05T09:50:45Z")

    def test_refused_forms(self):
        for bad in ("05/10/2026", "2026-10-05", "2026-10-05T08:20:45", "2026-13-05T08:20:45Z",
                    "2026-10-05T08:20:45+5", "", None, 5, "2026-10-05T08:20:45Z\n"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    AD.parse_time(bad)


class HardeningTests(unittest.TestCase):
    def test_date_range_edges_are_value_errors(self):
        for bad in ("0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-05:00"):
            with self.assertRaises(ValueError):
                AD.parse_time(bad)

    def test_unicode_digits_refused(self):
        with self.assertRaises(ValueError):
            AD.parse_time("\u0662026-10-05T08:20:45Z")
        sigs, _ = AD.ADAPTERS["release-status"]("RELEASE: \u0661.2.0 prod_failed\n",
                                                dict(CTX, source="release"))
        self.assertEqual(sigs, [])

    def test_nested_json_is_a_problem_not_a_traceback(self):
        deep = "[" * 100000
        sigs, problems = AD.ADAPTERS["gh-issues-json"](deep, CTX)
        self.assertEqual((sigs, len(problems)), ([], 1))
        line = '{"a":' * 100000
        ok = read("signals_ok.jsonl").splitlines()[0]
        sigs, problems = AD.ADAPTERS["intake-signals-jsonl"](line + "\n" + ok, dict(CTX, source="jira"))
        self.assertEqual((len(sigs), len(problems)), (1, 1))

    def test_nested_json_import_exits_3(self):
        root = repo(config=CFG)
        f = write(root, "d.json", "[" * 100000)
        self.assertEqual(imp(root, "gh-issues-json", "github", f)[0], 3)

    def test_labels_not_a_list_is_a_problem(self):
        issue = json.loads(read("gh_issues_ok.json"))[:1]
        for bad in ("bug", 5, {"name": "bug"}, None):
            issue[0]["labels"] = bad
            sigs, problems = AD.ADAPTERS["gh-issues-json"](json.dumps(issue), CTX)
            self.assertEqual((sigs, len(problems)), ([], 1), bad)

    def test_oversized_jsonl_fields_refused(self):
        base = json.loads(read("signals_ok.jsonl").splitlines()[0])
        for patch in ({"source_id": "x" * 201}, {"url": "u" * 2001},
                      {"evidence": [{"text": "t", "key": "k" * 201}]}):
            sigs, problems = AD.ADAPTERS["intake-signals-jsonl"](
                json.dumps(dict(base, **patch)), dict(CTX, source="jira"))
            self.assertEqual((sigs, len(problems)), ([], 1), list(patch))

    def test_signals_per_import_are_capped(self):
        root = repo(config=CFG)
        old = AD.MAX_SIGNALS
        AD.MAX_SIGNALS = 2
        try:
            f = write(root, "i.json", read("gh_issues_ok.json"))
            rc, out = imp(root, "gh-issues-json", "github", f)
        finally:
            AD.MAX_SIGNALS = old
        self.assertEqual(rc, 3)
        self.assertIn("ok 2 problems 1", out)
        # The cap kept issues 7 and 8; 8 was closed when first seen, so no item (R27).
        self.assertEqual(list(IN.Queue.load(root).items), [IN.item_id("github", "7")])


class GhIssuesTests(unittest.TestCase):
    def adapt(self, raw):
        return AD.ADAPTERS["gh-issues-json"](raw, CTX)

    def test_valid(self):
        sigs, problems = self.adapt(read("gh_issues_ok.json"))
        self.assertEqual(problems, [])
        by = {s["source_id"]: s for s in sigs}
        self.assertEqual(set(by), {"7", "8", "9"})
        s = by["7"]
        self.assertEqual((s["source"], s["kind"], s["severity"], s["trust"], s["url"]),
                         ("github", "issue", 3, "normal", "https://github.com/o/r/issues/7"))
        self.assertEqual((s["first_seen"], s["last_seen"]), ("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"))
        self.assertEqual(s["evidence"][0]["key"], "2026-10-02T00:00:00Z")
        self.assertEqual(s["evidence"][0]["text"], "Steps: click login.")
        self.assertEqual(by["8"]["severity"], 4)
        self.assertTrue(by["8"]["closed"])
        self.assertEqual(by["8"]["first_seen"], "2026-10-03T02:50:45Z")
        self.assertEqual(by["8"]["last_seen"], "2026-10-04T08:20:45Z")
        self.assertEqual(by["9"]["severity"], 2)
        self.assertFalse(by["9"].get("closed"))

    def test_one_malformed_record_does_not_stop_the_others(self):
        sigs, problems = self.adapt(read("gh_issues_one_bad.json"))
        self.assertEqual([s["source_id"] for s in sigs], ["7"])
        self.assertEqual(len(problems), 3)

    def test_empty_array(self):
        self.assertEqual(self.adapt("[]"), ([], []))

    def test_not_an_array_or_not_json(self):
        for raw in ("{}", "nope", ""):
            sigs, problems = self.adapt(raw)
            self.assertEqual((sigs, len(problems)), ([], 1))

    def test_body_is_capped(self):
        issue = json.loads(read("gh_issues_ok.json"))[:1]
        issue[0]["body"] = "x" * 9000
        sigs, _ = self.adapt(json.dumps(issue))
        self.assertEqual(len(sigs[0]["evidence"][0]["text"]), 4000)


class JsonlTests(unittest.TestCase):
    def adapt(self, raw):
        return AD.ADAPTERS["intake-signals-jsonl"](raw, dict(CTX, source="jira"))

    def test_valid_and_trust_is_always_low(self):
        sigs, problems = self.adapt(read("signals_ok.jsonl"))
        self.assertEqual(problems, [])
        self.assertEqual([s["source_id"] for s in sigs], ["OPS-1", "OPS-2"])
        self.assertEqual({s["trust"] for s in sigs}, {"low"})
        self.assertEqual(sigs[1]["first_seen"], "2026-10-01T00:00:00Z")

    def test_one_malformed_line(self):
        sigs, problems = self.adapt(read("signals_one_bad.jsonl"))
        self.assertEqual([s["source_id"] for s in sigs], ["OPS-1"])
        self.assertEqual(len(problems), 3)

    def test_empty(self):
        self.assertEqual(self.adapt(""), ([], []))
        self.assertEqual(self.adapt("\n\n"), ([], []))


class ReleaseStatusTests(unittest.TestCase):
    def adapt(self, raw):
        return AD.ADAPTERS["release-status"](raw, dict(CTX, source="release"))

    def test_valid_skips_archived_unknown_and_non_failures(self):
        sigs, problems = self.adapt(read("release_status_ok.txt"))
        self.assertEqual(problems, [])
        self.assertEqual([(s["source_id"], s["severity"]) for s in sigs],
                         [("1.2.0", 4), ("1.3.0", 3), ("1.5.0", 2)])
        self.assertEqual((sigs[0]["kind"], sigs[0]["url"], sigs[0]["source"]), ("release", "", "release"))

    def test_status_severity_table(self):
        for status, sev in (("prod_failed", 4), ("outcome_unknown", 4), ("rolled_back", 3),
                            ("stage_failed", 3), ("abandoned", 2)):
            sigs, _ = self.adapt("RELEASE: 1.0.0 %s\n" % status)
            self.assertEqual(sigs[0]["severity"], sev)

    def test_none_is_zero_records_and_not_a_problem(self):
        self.assertEqual(self.adapt(read("release_status_none.txt")), ([], []))
        self.assertEqual(self.adapt(""), ([], []))

    def test_malformed_lines_are_problems(self):
        sigs, problems = self.adapt(read("release_status_bad.txt"))
        self.assertEqual([s["source_id"] for s in sigs], ["1.2.0", "2.0.0"])
        self.assertEqual(len(problems), 2)


class ImportCommandTests(unittest.TestCase):
    def test_unknown_format_exits_2(self):
        root = repo(config=CFG)
        self.assertEqual(imp(root, "nope", "github", fx("gh_issues_ok.json"))[0], 2)

    def test_refuses_unconfigured_disabled_or_unlisted(self):
        root = repo(config=CFG)
        f = fx("gh_issues_ok.json")
        for source, fmt in (("ghost", "gh-issues-json"), ("off", "gh-issues-json"),
                            ("github", "intake-signals-jsonl"), ("jira", "gh-issues-json")):
            with self.subTest(source=source, fmt=fmt):
                rc, out = imp(root, fmt, source, f)
                self.assertEqual(rc, 2)
                self.assertIn("STOP:", out)
        self.assertEqual(IN.Queue.load(root).items, {})

    def test_no_config_exits_2(self):
        root = repo()
        self.assertEqual(imp(root, "gh-issues-json", "github", fx("gh_issues_ok.json"))[0], 2)

    def test_formats_import_cannot_take_stop_cleanly(self):
        # Task 4 built both formats: release-envelope is read by sync, not import, and
        # git-rev-list needs the full 40-hex release commit (a short sha never matches).
        root = repo(config=CFG)
        rc, out = imp(root, "release-envelope", "release", fx("release_status_none.txt"))
        self.assertEqual(rc, 2)
        self.assertIn("STOP: release-envelope is read by sync", out)
        with no_io():
            rc, out = run(root, "import", "--format", "git-rev-list", "--commit", "abc1234",
                          fx("release_status_none.txt"))
        self.assertEqual((rc, "STOP: git-rev-list needs --commit" in out), (2, True))
        self.assertEqual(IN.Queue.load(root).items, {})
        self.assertEqual(IN.Queue.load(root).histories, {})

    def test_git_rev_list_takes_no_source(self):
        root = repo(config=CFG)
        with no_io():
            rc, _ = run(root, "import", "--format", "git-rev-list", "--source", "github",
                        "--commit", "abc1234", fx("release_status_none.txt"))
        self.assertEqual(rc, 2)

    def test_valid_import_creates_items(self):
        root = repo(config=CFG)
        rc, out = imp(root, "gh-issues-json", "github", fx("gh_issues_ok.json"))
        self.assertEqual(rc, 0)
        self.assertIn("IMPORT: github gh-issues-json ok 3 problems 0", out)
        q = IN.Queue.load(root)
        it = q.items[IN.item_id("github", "7")]
        self.assertEqual((it["state"], it["count"], it["severity"], it["kind"], it["trust"]),
                         ("new", 1, 3, "issue", "normal"))
        self.assertEqual(it["url"], "https://github.com/o/r/issues/7")
        # R27: issue 8 was already CLOSED when intake first saw it, so it makes no item.
        self.assertNotIn(IN.item_id("github", "8"), q.items)
        self.assertEqual(len(q.items), 2)

    def test_problems_exit_3_and_other_records_import(self):
        root = repo(config=CFG)
        rc, out = imp(root, "gh-issues-json", "github", fx("gh_issues_one_bad.json"))
        self.assertEqual(rc, 3)
        self.assertIn("IMPORT: github gh-issues-json ok 1 problems 3", out)
        self.assertEqual(out.count("PROBLEM:"), 3)
        self.assertEqual(list(IN.Queue.load(root).items), [IN.item_id("github", "7")])

    def test_empty_array_is_ok(self):
        root = repo(config=CFG)
        f = write(root, "e.json", "[]")
        rc, out = imp(root, "gh-issues-json", "github", f)
        self.assertEqual(rc, 0)
        self.assertIn("ok 0 problems 0", out)

    def test_release_none_exits_0(self):
        root = repo(config=CFG)
        rc, out = imp(root, "release-status", "release", fx("release_status_none.txt"))
        self.assertEqual(rc, 0)
        self.assertIn("IMPORT: release release-status ok 0 problems 0", out)

    def test_release_status_import(self):
        root = repo(config=CFG)
        rc, _ = imp(root, "release-status", "release", fx("release_status_ok.txt"))
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(i["source_id"] for i in IN.Queue.load(root).items.values()),
                         ["1.2.0", "1.3.0", "1.5.0"])

    def test_stdin_dash(self):
        import io
        root = repo(config=CFG)
        old = sys.stdin
        sys.stdin = io.StringIO(read("gh_issues_ok.json"))
        try:
            with no_io():
                rc, _ = run(root, "import", "--format", "gh-issues-json", "--source", "github", "-")
        finally:
            sys.stdin = old
        self.assertEqual(rc, 0)
        self.assertEqual(len(IN.Queue.load(root).items), 2)  # closed issue 8: no item (R27)

    def test_missing_file_exits_2(self):
        root = repo(config=CFG)
        self.assertEqual(imp(root, "gh-issues-json", "github", os.path.join(root, "nope"))[0], 2)

    def test_lock_held_exits_3(self):
        root = repo(config=CFG)
        old = IN.LOCK_TIMEOUT
        IN.LOCK_TIMEOUT = 0.2
        try:
            with IN.run_lock(root):
                rc, _ = imp(root, "gh-issues-json", "github", fx("gh_issues_ok.json"))
        finally:
            IN.LOCK_TIMEOUT = old
        self.assertEqual(rc, 3)

    def test_corrupt_queue_stops(self):
        root = repo(config=CFG, items=[("github", "1", "t")])
        with open(os.path.join(root, IN.INTAKE_DIR, IN.QUEUE_FILE), "w") as f:
            f.write("{nope")
        rc, out = imp(root, "gh-issues-json", "github", fx("gh_issues_ok.json"))
        self.assertEqual(rc, 2)
        self.assertIn("STOP: queue-unreadable", out)

    def test_jsonl_trust_stored_low(self):
        root = repo(config=CFG)
        rc, _ = imp(root, "intake-signals-jsonl", "jira", fx("signals_ok.jsonl"))
        self.assertEqual(rc, 0)
        it = IN.Queue.load(root).items[IN.item_id("jira", "OPS-1")]
        self.assertEqual(it["trust"], "low")

    def test_jsonl_source_must_match_the_import_source(self):
        root = repo(config=CFG)
        rc, out = imp(root, "intake-signals-jsonl", "jira", fx("signals_one_bad.jsonl"))
        self.assertEqual(rc, 3)
        self.assertNotIn(IN.item_id("other", "OPS-3"), IN.Queue.load(root).items)


class FormatsTests(unittest.TestCase):
    def test_lists_every_format_and_marks_unbuilt(self):
        root = repo()
        with no_io():
            rc, out = run(root, "formats")
        self.assertEqual(rc, 0)
        names = [l.split()[1] for l in out.splitlines() if l.startswith("FORMAT:")]
        self.assertEqual(names, list(IN.FORMATS))
        for l in out.splitlines():
            name = l.split()[1]
            built = name in AD.ADAPTERS
            self.assertEqual("not yet built" in l, not built, l)
        self.assertEqual(set(AD.FORMAT_HELP), set(IN.FORMATS))


class MergeTests(unittest.TestCase):
    ISSUE = {"number": 7, "title": "boom", "body": "x", "labels": [{"name": "bug"}],
             "state": "OPEN", "createdAt": "2026-10-01T00:00:00Z",
             "updatedAt": "2026-10-02T00:00:00Z", "url": "https://github.com/o/r/issues/7"}

    def imp(self, root, issue, name="i.json"):
        f = write(root, name, json.dumps([issue]))
        return imp(root, "gh-issues-json", "github", f)

    def test_double_import_leaves_count_unchanged(self):
        root = repo(config=CFG)
        f = fx("gh_issues_ok.json")
        imp(root, "gh-issues-json", "github", f)
        before = copy.deepcopy(IN.Queue.load(root).items)
        imp(root, "gh-issues-json", "github", f)
        self.assertEqual(IN.Queue.load(root).items, before)

    def test_new_evidence_key_grows_count_and_last_seen(self):
        root = repo(config=CFG)
        self.imp(root, self.ISSUE)
        later = dict(self.ISSUE, updatedAt="2026-10-05T00:00:00Z", body="more")
        self.imp(root, later, "j.json")
        it = IN.Queue.load(root).items[IN.item_id("github", "7")]
        self.assertEqual((it["count"], it["last_seen"], len(it["evidence"])),
                         (2, "2026-10-05T00:00:00Z", 2))
        older = dict(self.ISSUE, updatedAt="2026-10-03T00:00:00Z")
        self.imp(root, older, "k.json")
        it = IN.Queue.load(root).items[IN.item_id("github", "7")]
        self.assertEqual((it["count"], it["last_seen"]), (3, "2026-10-05T00:00:00Z"))

    def test_evidence_is_capped_dropping_the_oldest(self):
        root = repo(config=CFG)
        for n in range(1, 26):
            self.imp(root, dict(self.ISSUE, updatedAt="2026-10-%02dT00:00:00Z" % n), "c%d.json" % n)
        it = IN.Queue.load(root).items[IN.item_id("github", "7")]
        self.assertEqual(len(it["evidence"]), 20)
        self.assertEqual(it["count"], 25)
        self.assertEqual(it["evidence"][0]["key"], "2026-10-06T00:00:00Z")

    def test_dismissed_open_issue_does_not_come_back_until_updated(self):
        root = repo(config=CFG)
        issue = dict(self.ISSUE)
        f = write(root, "i.json", json.dumps([issue]))
        self.assertEqual(imp(root, "gh-issues-json", "github", f)[0], 0)
        iid = IN.item_id("github", "7")
        run(root, "dismiss", iid, "--by", "Dana", "--reason", "known")
        imp(root, "gh-issues-json", "github", f)
        self.assertEqual(IN.Queue.load(root).items[iid]["state"], "dismissed")
        issue["updatedAt"] = "2099-01-01T00:00:00Z"
        f2 = write(root, "i2.json", json.dumps([issue]))
        imp(root, "gh-issues-json", "github", f2)
        it = IN.Queue.load(root).items[iid]
        self.assertEqual((it["state"], it["regressed"]), ("new", True))
        self.assertNotIn("closed_at", it)

    def test_update_before_dismissal_is_not_recurrence(self):
        root = repo(config=CFG)
        self.imp(root, self.ISSUE)
        iid = IN.item_id("github", "7")
        run(root, "dismiss", iid, "--by", "Dana", "--reason", "known")
        self.imp(root, dict(self.ISSUE, updatedAt="2026-10-03T00:00:00Z"), "j.json")
        self.assertEqual(IN.Queue.load(root).items[iid]["state"], "dismissed")

    def test_resolved_item_recurs_the_same_way(self):
        root = repo(config=CFG)
        self.imp(root, self.ISSUE)
        iid = IN.item_id("github", "7")
        run(root, "resolve", iid, "--by", "Dana")
        self.imp(root, dict(self.ISSUE, updatedAt="2099-01-01T00:00:00Z"), "j.json")
        it = IN.Queue.load(root).items[iid]
        self.assertEqual((it["state"], it["regressed"]), ("new", True))

    def test_second_recurrence_works_after_a_second_dismissal(self):
        root = repo(config=CFG)
        self.imp(root, self.ISSUE)
        iid = IN.item_id("github", "7")
        run(root, "dismiss", iid, "--by", "D", "--reason", "r")
        self.imp(root, dict(self.ISSUE, updatedAt="2098-01-01T00:00:00Z"), "j.json")
        self.assertEqual(IN.Queue.load(root).items[iid]["state"], "new")
        run(root, "dismiss", iid, "--by", "D", "--reason", "r")
        q = IN.Queue.load(root)           # pretend the dismissal happened in 2098-06
        q.items[iid]["closed_at"] = "2098-06-01T00:00:00Z"
        q.save()
        self.imp(root, dict(self.ISSUE, updatedAt="2098-01-01T00:00:00Z"), "k.json")
        self.assertEqual(IN.Queue.load(root).items[iid]["state"], "dismissed")
        self.imp(root, dict(self.ISSUE, updatedAt="2099-01-01T00:00:00Z"), "l.json")
        self.assertEqual(IN.Queue.load(root).items[iid]["state"], "new")

    def test_closing_an_issue_upstream_is_not_recurrence(self):
        root = repo(config=CFG)
        self.imp(root, self.ISSUE)
        iid = IN.item_id("github", "7")
        run(root, "resolve", iid, "--by", "Dana")
        self.imp(root, dict(self.ISSUE, state="CLOSED", updatedAt="2099-01-01T00:00:00Z"), "j.json")
        self.assertEqual(IN.Queue.load(root).items[iid]["state"], "resolved")

    def test_same_release_version_again_is_not_recurrence(self):
        root = repo(config=CFG)
        f = fx("release_status_ok.txt")
        imp(root, "release-status", "release", f)
        iid = IN.item_id("release", "1.2.0")
        run(root, "dismiss", iid, "--by", "D", "--reason", "r")
        imp(root, "release-status", "release", f)
        self.assertEqual(IN.Queue.load(root).items[iid]["state"], "dismissed")

    def test_hostile_body_is_data_and_shown_as_code(self):
        hostile = "# Heading @owner Closes #1\n```\nrm -rf /\n``` `x`"
        root = repo(config=CFG)
        self.imp(root, dict(self.ISSUE, body=hostile, title="# t @o"))
        iid = IN.item_id("github", "7")
        self.assertEqual(IN.Queue.load(root).items[iid]["evidence"][0]["text"], hostile)
        with no_io():
            rc, out = run(root, "show", iid)
        self.assertEqual(rc, 0)
        for line in out.splitlines():
            self.assertTrue(line.startswith(("ITEM:", "source:", "evidence:")), line)
        self.assertEqual(sum(1 for l in out.splitlines() if l.startswith("evidence:")), 1)


class StaticTests(unittest.TestCase):
    def test_adapters_import_nothing_that_can_reach_out(self):
        banned = {"socket", "subprocess", "urllib", "http", "ftplib", "smtplib", "ssl",
                  "requests", "asyncio", "importlib", "os", "shutil"}
        with open(os.path.join(HERE, "adapters.py")) as f:
            tree = ast.parse(f.read())
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names = [x.name for x in n.names]
            elif isinstance(n, ast.ImportFrom):
                names = [n.module or ""]
            else:
                if isinstance(n, ast.Name) and n.id in ("__import__", "open", "eval", "exec"):
                    self.fail("%s used in adapters.py" % n.id)
                continue
            for name in names:
                self.assertNotIn(name.split(".")[0], banned)


if __name__ == "__main__":
    unittest.main()
