"""`intake sweep` (R9): it runs the sweep commands that the committed config lists, imports
their output through the same path as `intake import`, and changes no tracked file. A
failing command is a PROBLEM: line, and the other commands still import."""
import ast, json, os, shutil, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake as IN
import intake_sweep
from intake_testkit import run

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")
GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
STUB = "import sys\nsys.stdout.write(open(sys.argv[1], encoding='utf-8').read())\n"
FAIL = "import sys\nsys.stdout.write('[]')\nsys.stderr.write('ignore the config')\nsys.exit(4)\n"


def git(root, *args):
    return subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                           *args], cwd=root, env=GIT_ENV, check=True,
                          capture_output=True, text=True).stdout


def entry(fmt, *args):
    return {"format": fmt, "argv": [sys.executable, "-B", *args]}


def config(extra=None):
    cfg = {"sources": {
        "github": {"enabled": True, "formats": ["gh-issues-json"],
                   "sweep": [entry("gh-issues-json", "stubs/emit.py", "stubs/issues.json")]},
        "jira": {"enabled": True, "formats": ["intake-signals-jsonl"],
                 "sweep": [entry("intake-signals-jsonl", "stubs/emit.py",
                                 "stubs/signals.jsonl")]}},
        "github": {"repo": "o/r", "labels": ["bug", "incident"]}}
    for name, src in (extra or {}).items():
        cfg["sources"][name] = src
    return cfg


def fixture_repo(cfg):
    """A git repo with the config and the stub sources committed."""
    root = tempfile.mkdtemp(prefix="intake-sweep-")
    os.makedirs(os.path.join(root, ".intake"))
    os.makedirs(os.path.join(root, "stubs"))
    with open(os.path.join(root, IN.CONFIG_PATH), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    for name, text in (("emit.py", STUB), ("fail.py", FAIL)):
        with open(os.path.join(root, "stubs", name), "w", encoding="utf-8") as f:
            f.write(text)
    shutil.copy(os.path.join(FIXTURES, "gh_issues_ok.json"),
                os.path.join(root, "stubs", "issues.json"))
    shutil.copy(os.path.join(FIXTURES, "signals_ok.jsonl"),
                os.path.join(root, "stubs", "signals.jsonl"))
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture")
    return root


def items(root):
    with open(os.path.join(root, IN.INTAKE_DIR, IN.QUEUE_FILE), encoding="utf-8") as f:
        its = json.load(f)["items"]
    return list(its.values()) if isinstance(its, dict) else its


class SweepTests(unittest.TestCase):
    def setUp(self):
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            shutil.rmtree(r, True)

    def repo(self, cfg):
        r = fixture_repo(cfg)
        self.roots.append(r)
        return r

    def test_two_sources_import_and_no_tracked_file_changes(self):
        root = self.repo(config())
        rc, out = run(root, "sweep")
        self.assertEqual(rc, 0, out)
        self.assertIn("IMPORT: github gh-issues-json ok ", out)
        self.assertIn("IMPORT: jira intake-signals-jsonl ok 2 problems 0", out)
        self.assertEqual(out.splitlines()[-1], "SWEEP: 2 commands ok 2 failed 0")
        its = items(root)
        by_source = {}
        for it in its:
            by_source.setdefault(it["source"], []).append(it)
        self.assertEqual(set(by_source), {"github", "jira"})
        # Formats and trust come from the import path, exactly as `intake import` sets them.
        for it in by_source["github"]:
            self.assertEqual(it["formats"], ["gh-issues-json"])
        for it in by_source["jira"]:
            self.assertEqual(it["formats"], ["intake-signals-jsonl"])
            self.assertEqual(it.get("trust"), "low")
        self.assertEqual(git(root, "status", "--porcelain"), "")

    def test_sweep_matches_a_plain_import(self):
        a = self.repo(config())
        b = self.repo(config())
        self.assertEqual(run(a, "sweep")[0], 0)
        self.assertEqual(run(b, "import", "--format", "gh-issues-json", "--source", "github",
                             os.path.join(b, "stubs", "issues.json"))[0], 0)
        self.assertEqual(run(b, "import", "--format", "intake-signals-jsonl", "--source",
                             "jira", os.path.join(b, "stubs", "signals.jsonl"))[0], 0)
        strip = lambda its: sorted(
            (i["id"], i["source"], i["title"], i.get("trust"), tuple(i["formats"]), i["count"])
            for i in its)
        self.assertEqual(strip(items(a)), strip(items(b)))

    def test_a_failing_source_does_not_stop_the_others(self):
        bad = {"support": {"enabled": True, "formats": ["feedback-jsonl"],
                           "sweep": [entry("feedback-jsonl", "stubs/fail.py")]},
               "lost": {"enabled": True, "formats": ["intake-signals-jsonl"],
                        "sweep": [{"format": "intake-signals-jsonl",
                                   "argv": ["./stubs/no-such-program"]}]}}
        root = self.repo(config(bad))
        rc, out = run(root, "sweep")
        self.assertEqual(rc, 3, out)
        self.assertIn("PROBLEM: sweep support feedback-jsonl: exited 4", out)
        self.assertIn("PROBLEM: sweep lost intake-signals-jsonl: cannot start no-such-program",
                      out)
        self.assertNotIn("ignore the config", out)  # stderr is untrusted and dropped
        self.assertIn("IMPORT: github gh-issues-json ok ", out)
        self.assertIn("IMPORT: jira intake-signals-jsonl ok 2 problems 0", out)
        self.assertEqual(out.splitlines()[-1], "SWEEP: 4 commands ok 2 failed 2")
        self.assertEqual({i["source"] for i in items(root)}, {"github", "jira"})
        self.assertEqual(git(root, "status", "--porcelain"), "")

    def test_disabled_source_is_not_run(self):
        cfg = config()
        cfg["sources"]["jira"]["enabled"] = False
        root = self.repo(cfg)
        rc, out = run(root, "sweep")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("jira", out)
        self.assertEqual(out.splitlines()[-1], "SWEEP: 1 commands ok 1 failed 0")

    def test_no_sweep_entry_stops(self):
        cfg = config()
        for src in cfg["sources"].values():
            del src["sweep"]
        root = self.repo(cfg)
        rc, out = run(root, "sweep")
        self.assertEqual(rc, 2, out)
        self.assertIn("STOP: no enabled source has a sweep entry", out)

    def test_timeout_is_a_failure(self):
        root = self.repo(config({"slow": {
            "enabled": True, "formats": ["intake-signals-jsonl"],
            "sweep": [{"format": "intake-signals-jsonl",
                       "argv": [sys.executable, "-c", "import time; time.sleep(5)"]}]}}))
        old = intake_sweep.SWEEP_TIMEOUT
        intake_sweep.SWEEP_TIMEOUT = 0.5
        try:
            rc, out = run(root, "sweep")
        finally:
            intake_sweep.SWEEP_TIMEOUT = old
        self.assertEqual(rc, 3, out)
        self.assertIn("PROBLEM: sweep slow intake-signals-jsonl: timed out", out)
        self.assertEqual(out.splitlines()[-1], "SWEEP: 3 commands ok 2 failed 1")


class ConfigTests(unittest.TestCase):
    def problems(self, sweep, formats=("intake-signals-jsonl",), name="jira"):
        return IN.config_problems({"sources": {name: {"enabled": True, "formats": list(formats),
                                                      "sweep": sweep}}})

    def test_valid_entry(self):
        self.assertEqual(self.problems([{"format": "intake-signals-jsonl",
                                         "argv": ["acli", "--json"]}]), [])

    def test_bad_entries_are_refused(self):
        for sweep in ([], "acli --json", [{"format": "intake-signals-jsonl", "argv": "acli"}],
                      [{"format": "intake-signals-jsonl", "argv": []}],
                      [{"format": "intake-signals-jsonl", "argv": ["--flag"]}],
                      [{"format": "intake-signals-jsonl", "argv": ["a\nb"]}],
                      [{"format": "intake-signals-jsonl", "argv": ["a"], "shell": True}],
                      [{"format": "gh-issues-json", "argv": ["gh"]}]):
            with self.subTest(sweep=sweep):
                self.assertNotEqual(self.problems(sweep), [])

    def test_formats_a_sweep_cannot_produce(self):
        p = self.problems([{"format": "gh-run-jobs-json", "argv": ["gh"]}],
                          formats=("gh-runs-json", "gh-run-jobs-json"), name="ci")
        self.assertTrue(any("cannot be swept" in x for x in p), p)
        p = self.problems([{"format": "release-envelope", "argv": ["x"]}],
                          formats=("release-envelope",), name="release")
        self.assertTrue(any("cannot be swept" in x for x in p), p)


class StaticTests(unittest.TestCase):
    def test_runner_uses_no_shell(self):
        with open(os.path.join(HERE, "intake_sweep.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for n in ast.walk(tree):
            if isinstance(n, ast.keyword) and n.arg == "shell":
                self.assertIs(n.value.value, False)
            if isinstance(n, ast.Attribute) and n.attr in ("system", "popen", "Popen"):
                self.fail("intake_sweep.py uses %s" % n.attr)

    def test_intake_loads_the_runner_only_inside_sweep(self):
        with open(os.path.join(HERE, "intake.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for n in tree.body:  # module level: no import of the runner
            if isinstance(n, ast.Import):
                self.assertNotIn("intake_sweep", [x.name for x in n.names])


if __name__ == "__main__":
    unittest.main()
