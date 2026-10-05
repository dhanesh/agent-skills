import json, os, sys, unittest
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

class IoTests(unittest.TestCase):
    def test_no_socket_and_no_subprocess(self):
        root = repo(config=GOOD, items=[("github", "1", "t")])
        with no_io():
            for argv in (["list"], ["status"], ["show", IN.item_id("github", "1")]):
                self.assertEqual(run(root, *argv)[0], 0)

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
