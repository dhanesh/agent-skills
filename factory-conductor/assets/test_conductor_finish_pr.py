"""finish: a second run-result envelope that names the PR (D18).

Every test drives the default PR command (`gh pr create ...`) with a stub executable named
`gh` first on PATH, and pushes to a local bare remote. Offline and deterministic."""
import contextlib, glob, io, json, os, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C
import contract_check as CC
from conductor_testkit import GIT, repo, new_run, tmpdir

HERE = os.path.dirname(os.path.abspath(__file__))
INTAKE_DIR = os.path.join(HERE, "..", "..", "ops-intake", "assets")

# The stub `gh`: records its argv, then prints what the mode file says. Mode "url" prints
# a PR URL as `gh pr create` does; "none" prints no URL; "fail" exits 1.
GH = r'''#!%s
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
with open(os.path.join(here, "mode"), encoding="utf-8") as f:
    mode = f.read().strip()
if mode == "fail":
    sys.stderr.write("could not create the pull request\n")
    sys.exit(1)
if mode == "url":
    print("Creating pull request for factory/p into main\n")
    print("https://github.com/acme/widgets/pull/42")
else:
    print("pull request queued")
'''


def plan_payload():
    return {"title": "Demo plan", "spec": "docs/spec.md", "coverage": {}, "uncovered": [],
            "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "task T1",
                       "verify": [{"text": "passes", "command": ["{python}", "-c", "pass"]}],
                       "depends_on": []}]}


def commit_in(d, name, text):
    with open(os.path.join(d, name), "w") as f:
        f.write(text)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-q", "-m", "c"], check=True,
                   env=dict(os.environ, **GIT))


class PrNumberTests(unittest.TestCase):
    def test_only_a_pull_url_gives_a_number(self):
        cases = [("https://github.com/acme/widgets/pull/42\n", 42),
                 ("Creating...\n\nhttps://ghe.example.com:8443/a-b/c.d/pull/7/", 7),
                 ("see https://github.com/a/b/pull/3 and https://github.com/a/b/pull/9", 9),
                 ("", None), ("pull request queued", None),
                 ("http://github.com/a/b/pull/4", None),
                 ("https://github.com/a/b/issues/4", None),
                 ("https://github.com/a/b/pull/4/files", None),
                 ("https://github.com/a/b/pull/0", None),
                 ("xhttps://github.com/a/b/pull/5", None),
                 ("https://github.com/a/b/c/pull/6", None)]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(C.pr_number(text), want)


@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class FinishPrTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        gh = os.path.join(self.bin, "gh")
        with open(gh, "w") as f:
            f.write(GH % sys.executable)
        os.chmod(gh, 0o755)
        self.root = repo()
        self.bare = os.path.join(self.tmp, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", self.bare], check=True)
        subprocess.run(["git", "-C", self.root, "remote", "add", "origin", self.bare],
                       check=True)
        st, self.plan = new_run(self.root, plan_payload())
        self.st_path = st.state_path
        self.assertEqual(self.run_cmd(["start", "T1"])[0], 0)
        commit_in(C.State.load(self.st_path).tasks["T1"]["worktree"], "b.txt", "b\n")
        self.assertEqual(self.run_cmd(["verify", "T1"])[0], 0)
        self.assertEqual(self.run_cmd(["review", "T1", "--verdict", "pass"])[0], 0)
        self.assertEqual(self.run_cmd(["merge", "T1"])[0], 0)

    def gh_mode(self, mode):
        with open(os.path.join(self.bin, "mode"), "w") as f:
            f.write(mode)

    def gh_calls(self):
        path = os.path.join(self.bin, "calls.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(l) for l in f]

    def run_cmd(self, argv):
        buf = io.StringIO()
        env = {"PATH": self.bin + os.pathsep + os.environ["PATH"]}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(buf), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = C.main(argv + ["--root", self.root])
        return rc, buf.getvalue()

    def run_results(self):
        """The run-result envelopes on disk, oldest first."""
        out = []
        for path in glob.glob(os.path.join(CC.envelope_dir(self.root), "*.json")):
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            if doc.get("predicateType") == C.RUN_RESULT_KIND:
                out.append((path, doc))
        return sorted(out, key=lambda e: (e[1]["predicate"]["generatedAtTime"],
                                          e[1]["predicate"]["id"]))

    def assert_valid(self, path):
        rep = CC.check_envelope(path, root=self.root)
        self.assertEqual((rep["violations"], rep["stale"]), ([], []), path)

    def assert_pr_envelope(self, first, second, number):
        (p1, d1), (p2, d2) = first, second
        self.assert_valid(p1)
        self.assert_valid(p2)
        pay1, pay2 = d1["predicate"]["payload"], d2["predicate"]["payload"]
        self.assertNotIn("pr", pay1)
        self.assertEqual(pay2["pr"], {"number": number})
        self.assertEqual(d2["predicate"]["wasRevisionOf"], d1["predicate"]["id"])
        self.assertEqual(pay2["replaces"], {
            "id": d1["predicate"]["id"], "sha256": CC.sha256_file(p1),
            "path": os.path.relpath(os.path.realpath(p1), os.path.realpath(self.root))
            .replace(os.sep, "/")})
        # Everything else is the first envelope's: subjects, assertions, payload.
        self.assertEqual(d2["subject"], d1["subject"])
        self.assertEqual(d2["predicate"]["assertions"], d1["predicate"]["assertions"])
        self.assertEqual({k: v for k, v in pay2.items() if k not in ("pr", "replaces")}, pay1)
        self.assertGreater(d2["predicate"]["generatedAtTime"],
                           d1["predicate"]["generatedAtTime"])
        # The run now names the newest envelope; the old one is kept as superseded.
        fin = C.State.load(self.st_path).finished
        self.assertEqual(os.path.realpath(fin["envelope"]), os.path.realpath(p2))
        self.assertEqual(fin["sha256"], CC.sha256_file(p2))
        self.assertEqual(fin["id"], d2["predicate"]["id"])
        self.assertEqual(os.path.realpath(fin["superseded"]), os.path.realpath(p1))
        # finish and resume report the newest envelope.
        for argv, want in ((["finish"], 0), (["finish", "--retry-remote"], 0),
                           (["resume"], 0), (["status"], 0), (["trail"], 0)):
            rc, out = self.run_cmd(argv)
            self.assertEqual(rc, want, (argv, out))
            if argv[0] in ("finish", "resume"):
                self.assertIn("FINISH: %s" % fin["envelope"], out)

    def test_a_pr_url_gives_a_second_envelope_with_the_pr_number(self):
        self.gh_mode("url")
        rc, out = self.run_cmd(["finish"])
        self.assertEqual(rc, 0, out)
        calls = self.gh_calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ["pr", "create"])  # the default PR command
        envs = self.run_results()
        self.assertEqual(len(envs), 2, out)
        self.assertEqual([l for l in out.splitlines() if l.startswith("FINISH: ")],
                         ["FINISH: %s" % envs[0][0]])
        self.assertIn("PR: 42 %s" % envs[1][0], out.splitlines())
        self.assert_pr_envelope(envs[0], envs[1], 42)
        self.assertEqual(len(self.run_results()), 2)  # the re-reads wrote nothing
        with open(C.State.load(self.st_path).log_path, encoding="utf-8") as f:
            events = [json.loads(l) for l in f]
        self.assertEqual([e["event"] for e in events].count("pr_result"), 1)

    def test_no_pr_url_gives_one_envelope_as_before(self):
        self.gh_mode("none")
        rc, out = self.run_cmd(["finish"])
        self.assertEqual(rc, 0, out)
        envs = self.run_results()
        self.assertEqual(len(envs), 1)
        self.assert_valid(envs[0][0])
        self.assertNotIn("pr", envs[0][1]["predicate"]["payload"])
        self.assertFalse([l for l in out.splitlines() if l.startswith("PR: ")])
        fin = C.State.load(self.st_path).finished
        self.assertEqual(fin["pr"], "pull request queued")
        self.assertNotIn("superseded", fin)
        self.assertEqual(os.path.realpath(fin["envelope"]), os.path.realpath(envs[0][0]))

    def test_retry_remote_after_a_failed_pr_step_writes_the_second_envelope_once(self):
        self.gh_mode("fail")
        rc, out = self.run_cmd(["finish"])
        self.assertEqual(rc, 3, out)
        self.assertEqual(len(self.run_results()), 1)
        self.gh_mode("url")
        rc, out = self.run_cmd(["finish", "--retry-remote"])
        self.assertEqual(rc, 0, out)
        envs = self.run_results()
        self.assertEqual(len(envs), 2)
        self.assertEqual(len(self.gh_calls()), 2)
        self.assert_pr_envelope(envs[0], envs[1], 42)
        self.assertEqual(len(self.gh_calls()), 2)  # a done PR step never runs again
        self.assertEqual(len(self.run_results()), 2)

    @unittest.skipUnless(os.path.isfile(os.path.join(INTAKE_DIR, "intake.py")),
                         "ops-intake is not next to factory-conductor")
    def test_ops_intake_reads_the_pr_number_from_the_newest_envelope(self):
        sys.path.insert(0, os.path.abspath(INTAKE_DIR))
        try:
            import intake
        finally:
            sys.path.pop(0)
        self.gh_mode("url")
        rc, out = self.run_cmd(["finish"])
        self.assertEqual(rc, 0, out)
        (_, d1), (_, d2) = self.run_results()
        plan_pin = CC.sha256_file(self.plan)
        for doc, pr in ((d1, None), (d2, 42)):
            rec = intake._envelope_record(intake.RUN_KIND, doc["predicate"]["payload"])
            self.assertEqual(rec["pr"], pr)
            self.assertEqual(rec["plan_id"], doc["predicate"]["payload"]["plan"]["id"])
            self.assertIn(plan_pin, {s["digest"]["sha256"] for s in doc["subject"]})


if __name__ == "__main__":
    unittest.main()
