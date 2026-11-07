"""finish allowlists --pr-cmd the way it allowlists --push-cmd (R9, D22).

The only PR command finish runs is `gh pr create` followed by the options the default
command uses (--title {title}, --base {base_branch}, --body-file {body_file}), each at most
once. Anything else exits 2 before any push or PR step, and the check runs again right
before the PR step. Every accepted run uses a stub `gh` first on PATH and a local bare
remote. Offline and deterministic."""
import contextlib, io, json, os, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C
from conductor_testkit import GIT, repo, new_run, stub_gh, tmpdir, write_grant

# The stub `gh`'s target: records its argv (after `pr create`) and prints a PR URL.
RECORDER = r'''
import json, sys
record, rest = sys.argv[1], sys.argv[3:]
with open(record, "a", encoding="utf-8") as f:
    f.write(json.dumps(rest) + "\n")
print("https://github.com/acme/widgets/pull/7")
'''

DEFAULT = ["gh", "pr", "create", "--title", "{title}", "--base", "{base_branch}",
           "--body-file", "{body_file}"]


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


class PrCmdProblemTests(unittest.TestCase):
    def test_the_default_and_its_subsets_in_any_order_pass(self):
        self.assertEqual(C.PR_OPTIONS, {"--title": "{title}", "--base": "{base_branch}",
                                        "--body-file": "{body_file}"})
        for argv in (DEFAULT, list(C.DEFAULT_PR_CMD), ["gh", "pr", "create"],
                     ["gh", "pr", "create", "--body-file", "{body_file}"],
                     ["gh", "pr", "create", "--base", "{base_branch}",
                      "--body-file", "{body_file}", "--title", "{title}"]):
            with self.subTest(argv=argv):
                self.assertIsNone(C.pr_cmd_problem(argv))

    def test_anything_else_is_refused(self):
        bad = [["touch", "x"],
               ["/usr/bin/gh", "pr", "create"],
               ["./gh", "pr", "create"],
               ["gh", "pr", "merge"],
               ["gh", "pr"],
               ["sh", "-c", "gh pr create"],
               DEFAULT + ["--draft"],
               DEFAULT + ["--repo", "evil/repo"],
               DEFAULT + ["--head", "{run_branch}"],
               DEFAULT + ["extra"],
               DEFAULT + ["--"],
               DEFAULT + ["--", "--title", "{title}"],
               ["gh", "pr", "create", "--title", "{title}", "--title", "{title}"],
               ["gh", "pr", "create", "--title", "literal title"],
               ["gh", "pr", "create", "--title", "{base_branch}"],
               ["gh", "pr", "create", "--title={title}"],
               ["gh", "pr", "create", "-t", "{title}"],
               ["gh", "pr", "create", "--title"],
               ["gh", "pr", "create", "--body-file", "/etc/passwd"]]
        for argv in bad:
            with self.subTest(argv=argv):
                self.assertIsNotNone(C.pr_cmd_problem(argv))


@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class FinishPrCmdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        self.recorder = os.path.join(self.tmp, "recorder.py")
        with open(self.recorder, "w") as f:
            f.write(RECORDER)
        self.record = os.path.join(self.tmp, "gh-calls.jsonl")
        self.gh = stub_gh([sys.executable, self.recorder, self.record])
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
        self.assertEqual(self.run_cmd(["next"])[0], 3)

    def run_cmd(self, argv):
        buf, ebuf = io.StringIO(), io.StringIO()
        env = {"PATH": self.gh + os.pathsep + os.environ["PATH"]}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(buf), \
                contextlib.redirect_stderr(ebuf):
            rc = C.main(argv + ["--root", self.root])
        return rc, buf.getvalue(), ebuf.getvalue()

    def gh_calls(self):
        if not os.path.exists(self.record):
            return []
        with open(self.record, encoding="utf-8") as f:
            return [json.loads(l) for l in f]

    def pushed(self):
        return subprocess.run(["git", "--git-dir", self.bare, "rev-parse", "-q", "--verify",
                               "refs/heads/factory/p"], capture_output=True).returncode == 0

    def events(self):
        with open(C.State.load(self.st_path).log_path, encoding="utf-8") as f:
            return [json.loads(l)["event"] for l in f]

    def assert_refused_before_anything(self, rc, out, err, mark=None):
        self.assertEqual(rc, 2, out + err)
        self.assertIn("--pr-cmd", err)
        self.assertNotIn("FINISH:", out)
        self.assertFalse(self.pushed())
        self.assertEqual(self.gh_calls(), [])
        self.assertNotIn("push", self.events())
        self.assertNotIn("pr", self.events())
        if mark:
            self.assertFalse(os.path.exists(mark), "the --pr-cmd ran")
        self.assertIsNone(C.State.load(self.st_path).finished)

    def test_a_non_gh_pr_cmd_exits_2_and_runs_nothing(self):
        mark = os.path.join(self.root, "x")
        rc, out, err = self.run_cmd(["finish", "--pr-cmd", json.dumps(["touch", "x"])])
        self.assert_refused_before_anything(rc, out, err, mark)

    def test_an_extra_option_exits_2_and_runs_nothing(self):
        for extra in (["--repo", "evil/repo"], ["--draft"], ["--", "x"],
                      ["--title", "{title}"]):
            with self.subTest(extra=extra):
                rc, out, err = self.run_cmd(["finish", "--pr-cmd", json.dumps(DEFAULT + extra)])
                self.assert_refused_before_anything(rc, out, err)

    def test_the_default_command_opens_the_pr_through_the_stub_gh(self):
        rc, out, err = self.run_cmd(["finish"])
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(self.pushed())
        calls = self.gh_calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:4], ["--title", "Demo plan", "--base", "main"])
        self.assertEqual(calls[0][4], "--body-file")
        fin = C.State.load(self.st_path).finished
        self.assertEqual(fin["pr"], "https://github.com/acme/widgets/pull/7")

    def test_an_explicit_allowed_command_runs_too(self):
        cmd = ["gh", "pr", "create", "--body-file", "{body_file}", "--title", "{title}"]
        rc, out, err = self.run_cmd(["finish", "--pr-cmd", json.dumps(cmd)])
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(self.gh_calls()), 1)

    def test_retry_remote_refuses_a_bad_pr_cmd_before_the_pr_step(self):
        # The PR gate asks, so the push runs and the PR stays pending.
        policy = {"read_only": "auto", "local_reversible": "grant", "push_branch": "grant",
                  "open_pr": "ask"}
        write_grant(self.root, self.plan, policy=policy)
        rc, out, err = self.run_cmd(["finish"])
        self.assertEqual(rc, 3, out + err)
        self.assertTrue(self.pushed())
        self.assertEqual(self.gh_calls(), [])
        write_grant(self.root, self.plan)  # now the PR is covered
        mark = os.path.join(self.root, "x")
        rc, out, err = self.run_cmd(["finish", "--retry-remote",
                                     "--pr-cmd", json.dumps(["touch", "x"])])
        self.assertEqual(rc, 2, out + err)
        self.assertIn("--pr-cmd", err)
        self.assertFalse(os.path.exists(mark))
        self.assertEqual(self.gh_calls(), [])
        self.assertNotIn("pr", self.events())
        rc, out, err = self.run_cmd(["finish", "--retry-remote"])
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(self.gh_calls()), 1)

    def test_the_pr_step_checks_the_command_again_right_before_it_runs(self):
        # A command that passed the first check but fails it at the PR step (here: the
        # allowlist is swapped out between the two checks) never runs.
        real = C.pr_cmd_problem
        calls = []

        def second_check_fails(argv):
            calls.append(list(argv))
            return real(argv) if len(calls) == 1 else "changed"

        with mock.patch.object(C, "pr_cmd_problem", second_check_fails):
            rc, out, err = self.run_cmd(["finish"])
        self.assertEqual(rc, 2, out + err)
        self.assertEqual(len(calls), 2)
        self.assertIn("--pr-cmd is not a PR command finish may run: changed", err)
        self.assertTrue(self.pushed())  # the push ran; the PR step did not
        self.assertEqual(self.gh_calls(), [])
        self.assertNotIn("pr", self.events())


if __name__ == "__main__":
    unittest.main()
