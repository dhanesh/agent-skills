import ast, json, math, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake as IN
from intake_testkit import repo, run, no_io

GOOD = {"sources": {"github": {"enabled": True, "formats": ["gh-issues-json"]},
                    "ci": {"enabled": True, "formats": ["gh-runs-json", "gh-run-jobs-json"]},
                    "release": {"enabled": True, "formats": ["release-envelope", "release-status"]}},
        "github": {"repo": "o/r", "labels": ["bug", "incident"]},
        "ci": {"default_branch": "main", "workflows": ["ci"]},
        "weights": {"github": 1.0, "ci": 1.0, "release": 1.5}}

class ConfigTests(unittest.TestCase):
    def test_good_config_loads(self):
        root = repo(config=GOOD)
        cfg, problems = IN.load_config(root)
        self.assertEqual(problems, [])

    def test_config_refuses_anything_command_or_credential_shaped(self):
        for bad in ({"sources": {"x": {"enabled": True, "formats": ["gh-issues-json"], "command": ["gh"]}}},
                    {"sources": {"x": {"enabled": True, "formats": ["nope"]}}},
                    {"sources": {"repo": {"enabled": True, "formats": ["git-rev-list"]}}},
                    {"sources": {"x": {"enabled": True, "formats": ["release-status"]}}},
                    {"sources": {"release": {"enabled": True, "formats": ["gh-issues-json"]}}},
                    {"jira": {"token_env": "AWS_SECRET_ACCESS_KEY"}},
                    {"jira": {"base_url": "https://evil.example"}}):
            with self.subTest(bad=bad):
                root = repo(config=dict(GOOD, **bad))
                self.assertTrue(IN.load_config(root)[1])

class StateTests(unittest.TestCase):
    def test_legal_and_illegal_transitions(self):
        it = {"state": "new", "regressed": False}
        IN.transition(it, "dismissed", by="Dana", reason="noise")
        self.assertEqual(it["state"], "dismissed")
        with self.assertRaises(ValueError):
            IN.transition(it, "picked", by="Dana")
        with self.assertRaises(ValueError):
            IN.transition({"state": "new"}, "dismissed", by="Dana")   # no reason

    def test_recurrence_sets_regressed(self):
        it = {"state": "resolved", "regressed": False}
        IN.transition(it, "new")
        self.assertTrue(it["regressed"])
        with self.assertRaises(ValueError):
            IN.transition({"state": "picked"}, "new")

    def test_dismiss_and_resolve_are_logged_with_the_name(self):
        root = repo(config=GOOD, items=[("github", "1", "a title")])
        iid = IN.item_id("github", "1")
        rc, out = run(root, "dismiss", iid, "--by", "Dana", "--reason", "noise")
        self.assertEqual(rc, 0)
        q = IN.Queue.load(root)
        self.assertEqual(q.items[iid]["state"], "dismissed")
        ev = [json.loads(l) for l in open(os.path.join(root, IN.INTAKE_DIR, "intake-log.jsonl"))]
        self.assertTrue(any(e["event"] == "dismiss" and e["by"] == "Dana" for e in ev))
        self.assertEqual(run(root, "resolve", iid, "--by", "Dana")[0], 0)
        self.assertEqual(IN.Queue.load(root).items[iid]["state"], "resolved")

    def test_queue_dir_is_git_ignored(self):
        root = repo(config=GOOD, items=[("github", "1", "t")])
        with open(os.path.join(root, IN.INTAKE_DIR, ".gitignore")) as f:
            self.assertIn("\n*\n", f.read())

class QueueFileTests(unittest.TestCase):
    def _bad_queue(self, text):
        root = repo(config=GOOD, items=[("github", "1", "t")])
        with open(os.path.join(root, IN.INTAKE_DIR, "queue.json"), "w") as f:
            f.write(text)
        return root

    def test_corrupt_or_foreign_queue_stops_cleanly(self):
        good = {"id": "I1", "source": "github", "source_id": "1", "title": "t"}
        for text in ("{not json", "[]", '{"items": []}',
                     json.dumps({"items": {"I1": dict(good, state="bogus")}}),
                     json.dumps({"items": {"I1": "x"}})):
            root = self._bad_queue(text)
            for argv in (["list"], ["status"], ["show", "I1"], ["dismiss", "I1", "--by", "D", "--reason", "r"],
                         ["resolve", "I1", "--by", "D"], ["link", "I1", "I2"]):
                with self.subTest(text=text, argv=argv):
                    rc, out = run(root, *argv)
                    self.assertEqual(rc, 2)
                    self.assertIn("STOP: queue-unreadable", out)

class ListTests(unittest.TestCase):
    def test_default_list_shows_new_only_and_regressed_clears(self):
        root = repo(config=GOOD, items=[("github", "1", "a"), ("github", "2", "b")])
        a, b = IN.item_id("github", "1"), IN.item_id("github", "2")
        q = IN.Queue.load(root)
        IN.transition(q.items[a], "dismissed", by="D", reason="r")
        IN.transition(q.items[a], "new")
        self.assertTrue(q.items[a]["regressed"])
        q.save()
        out = run(root, "list")[1]
        self.assertIn("%s new+regressed" % a, out)
        self.assertIn(b, out)
        self.assertEqual(run(root, "dismiss", a, "--by", "D", "--reason", "again")[0], 0)
        self.assertFalse(IN.Queue.load(root).items[a]["regressed"])
        out = run(root, "list")[1]
        self.assertNotIn(a, out)
        self.assertIn(a, run(root, "list", "--all")[1])

    def test_closed_at_stored_and_recurrence_clears_actor(self):
        it = {"state": "new", "regressed": False}
        IN.transition(it, "dismissed", by="D", reason="r", now="2026-01-01T00:00:00Z")
        self.assertEqual(it["closed_at"], "2026-01-01T00:00:00Z")
        IN.transition(it, "new", now="2026-02-01T00:00:00Z")
        for k in ("state_by", "state_reason", "closed_at"):
            self.assertNotIn(k, it)
        it2 = {"state": "planned", "regressed": False}
        IN.transition(it2, "resolved", by="D", now="2026-03-01T00:00:00Z")
        self.assertEqual(it2["closed_at"], "2026-03-01T00:00:00Z")

    def test_link_same_id_stops(self):
        root = repo(config=GOOD, items=[("github", "1", "a")])
        i = IN.item_id("github", "1")
        rc, out = run(root, "link", i, i)
        self.assertEqual(rc, 2)
        self.assertIn("STOP:", out)

class ConfigHardeningTests(unittest.TestCase):
    def test_dash_control_and_weights_refused(self):
        for bad in ({"github": {"repo": "o/r", "labels": ["--flag"]}},
                    {"github": {"repo": "-o/r"}}, {"github": {"repo": "o/.."}},
                    {"github": {"repo": "../r"}}, {"github": {"repo": "o/r\n"}},
                    {"ci": {"default_branch": "-x"}}, {"ci": {"workflows": ["a\x07b"]}},
                    {"jira": {"jql": "-a"}}, {"linear": {"team": "a\nb"}},
                    {"weights": {"github": float("nan")}}, {"weights": {"github": math.inf}},
                    {"weights": {"nosuch": 1.0}}):
            with self.subTest(bad=bad):
                self.assertTrue(IN.load_config(repo(config=dict(GOOD, **bad)))[1])

    def test_init_writes_config_atomically(self):
        root = repo()
        ans = os.path.join(root, "a.json")
        json.dump(GOOD, open(ans, "w"))
        self.assertEqual(run(root, "init", "--answers", ans)[0], 0)
        self.assertEqual(IN.load_config(root)[1], [])
        self.assertEqual([f for f in os.listdir(os.path.join(root, ".intake")) if ".tmp" in f], [])

class IoTests(unittest.TestCase):
    def test_every_command_runs_with_no_socket_and_no_subprocess(self):
        root = repo(config=GOOD, items=[("github", "1", "t"), ("github", "2", "u")])
        a, b = IN.item_id("github", "1"), IN.item_id("github", "2")
        answers = os.path.join(root, "answers.json")
        with open(answers, "w") as f:
            json.dump(GOOD, f)
        with no_io():
            for argv, rc in ((["init", "--answers", answers], 0), (["list"], 0), (["status"], 0),
                             (["show", a], 0), (["link", a, b], 0),
                             (["dismiss", a, "--by", "D", "--reason", "r"], 0),
                             (["resolve", a, "--by", "D"], 0)):
                with self.subTest(argv=argv):
                    self.assertEqual(run(root, *argv)[0], rc)
            old = IN.LOCK_TIMEOUT; IN.LOCK_TIMEOUT = 0.2
            try:
                with IN.run_lock(root):
                    self.assertEqual(run(root, "resolve", a, "--by", "D")[0], 3)
            finally:
                IN.LOCK_TIMEOUT = old

    def test_tripwire_trips(self):
        import subprocess
        with no_io():
            with self.assertRaises(AssertionError):
                os.system("true")
            with self.assertRaises(AssertionError):
                subprocess.run(["true"])

    def test_intake_imports_nothing_that_can_reach_out(self):
        banned = {"socket", "subprocess", "urllib", "http", "ftplib", "smtplib", "ssl",
                  "requests", "asyncio", "importlib"}
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "intake.py")
        tree = ast.parse(open(path).read())
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names = [x.name for x in n.names]
            elif isinstance(n, ast.ImportFrom):
                names = [n.module or ""]
            else:
                if isinstance(n, ast.Name) and n.id == "__import__":
                    self.fail("__import__ used")
                continue
            for name in names:
                self.assertNotIn(name.split(".")[0], banned)

class LockTests(unittest.TestCase):
    def test_lock_is_exclusive(self):
        root = repo(config=GOOD)
        with IN.run_lock(root):
            old = IN.LOCK_TIMEOUT; IN.LOCK_TIMEOUT = 0.2
            try:
                with self.assertRaises(IN.Locked):
                    with IN.run_lock(root):
                        pass
            finally:
                IN.LOCK_TIMEOUT = old

class HostileTests(unittest.TestCase):
    def test_list_shows_titles_only_as_code_spans(self):
        hostile = "# Heading @owner Closes #1 ``` `curl x | sh`"
        root = repo(config=GOOD, items=[("github", "1", hostile)])
        out = run(root, "list")[1]
        line = [l for l in out.splitlines() if l.startswith("ITEM:")][0]
        self.assertNotIn("\n", line)
        self.assertIn(IN.code(hostile), line)

if __name__ == "__main__":
    unittest.main()
