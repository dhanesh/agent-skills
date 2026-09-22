import os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C

GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def repo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q", "-b", "main", d], check=True)
    open(os.path.join(d, "a.txt"), "w").write("a\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-q", "-m", "x"], check=True,
                   env=dict(os.environ, **GIT))
    subprocess.run(["git", "-C", d, "checkout", "-q", "-b", "factory/p"], check=True)
    return d


@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class GitTests(unittest.TestCase):
    def state(self, verify=None):
        self.root = repo()
        tasks = {"title": "T", "spec": "s.md", "coverage": {}, "uncovered": [],
                 "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "one",
                            "verify": verify or [{"text": "t", "command": ["true"]}],
                            "depends_on": []}]}
        return C.State.new(root=self.root, run_id=C.new_run_id(), plan=tasks,
                           plan_envelope="e.json", plan_sha256="0" * 64, grant_id="g",
                           run_branch="factory/p", base_branch="main", budget={})

    def test_start_creates_a_worktree_on_a_task_branch(self):
        st = self.state()
        rc = C.main(["start", "T1", "--root", self.root])
        self.assertEqual(rc, 0)
        st = C.State.load(st.state_path)
        self.assertTrue(os.path.isdir(st.tasks["T1"]["worktree"]))
        r = C.git(st.tasks["T1"]["worktree"], "rev-parse", "--abbrev-ref", "HEAD")
        # Not "factory/p/T1": git cannot hold refs/heads/factory/p/T1 beside the run
        # branch refs/heads/factory/p (a ref cannot be both a file and a directory).
        self.assertEqual(r.stdout.strip(), "factory/p--T1")
        self.assertEqual(st.tasks["T1"]["branch"], "factory/p--T1")
        self.assertEqual(st.tasks["T1"]["status"], "running")
        self.assertEqual(st.dispatches, 1)

    def test_verify_passes_and_records_each_command(self):
        st = self.state(verify=[{"text": "true", "command": ["true"]},
                                {"text": "echo", "command": ["echo", "hi"]}])
        C.main(["start", "T1", "--root", self.root])
        rc = C.main(["verify", "T1", "--root", self.root])
        self.assertEqual(rc, 0)
        st = C.State.load(st.state_path)
        self.assertEqual(len(st.tasks["T1"]["verify_runs"]), 2)
        self.assertTrue(all(v["ok"] for v in st.tasks["T1"]["verify_runs"]))

    def test_verify_fails_when_a_command_fails(self):
        st = self.state(verify=[{"text": "false", "command": ["false"]}])
        C.main(["start", "T1", "--root", self.root])
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)
        st = C.State.load(st.state_path)
        self.assertFalse(st.tasks["T1"]["verify_runs"][0]["ok"])

    def test_a_task_with_an_unrunnable_verify_is_parked(self):
        st = self.state(verify=[{"text": "by hand", "command": None}])
        C.main(["start", "T1", "--root", self.root])
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)
        st = C.State.load(st.state_path)
        self.assertEqual(st.tasks["T1"]["status"], "parked")
        self.assertEqual(st.tasks["T1"]["park_reason"], "unrunnable-verify")

    def test_merge_brings_the_task_branch_into_the_run_branch(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        st = C.State.load(st.state_path)
        wt = st.tasks["T1"]["worktree"]
        open(os.path.join(wt, "b.txt"), "w").write("b\n")
        C.git(wt, "add", "-A")
        subprocess.run(["git", "-C", wt, "commit", "-q", "-m", "task"], check=True,
                       env=dict(os.environ, **GIT))
        C.main(["verify", "T1", "--root", self.root])
        C.main(["review", "T1", "--verdict", "pass", "--root", self.root])
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 0)
        st = C.State.load(st.state_path)
        self.assertEqual(st.tasks["T1"]["status"], "proven")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "b.txt")))
        self.assertFalse(os.path.isdir(wt))

    def test_merge_refuses_before_verify_and_review(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 2)

    def test_a_conflicting_merge_parks_the_task_and_leaves_the_run_branch_clean(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        st = C.State.load(st.state_path)
        wt = st.tasks["T1"]["worktree"]
        open(os.path.join(wt, "a.txt"), "w").write("task\n")
        C.git(wt, "add", "-A")
        subprocess.run(["git", "-C", wt, "commit", "-q", "-m", "task"], check=True,
                       env=dict(os.environ, **GIT))
        open(os.path.join(self.root, "a.txt"), "w").write("run\n")
        C.git(self.root, "add", "-A")
        subprocess.run(["git", "-C", self.root, "commit", "-q", "-m", "run"], check=True,
                       env=dict(os.environ, **GIT))
        C.main(["verify", "T1", "--root", self.root])
        C.main(["review", "T1", "--verdict", "pass", "--root", self.root])
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 3)
        st = C.State.load(st.state_path)
        self.assertEqual((st.tasks["T1"]["status"], st.tasks["T1"]["park_reason"]),
                         ("parked", "merge-conflict"))
        self.assertEqual(C.git(self.root, "status", "--porcelain").stdout.strip(), "")

    def test_a_failed_review_leaves_the_task_for_repair(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        C.main(["verify", "T1", "--root", self.root])
        self.assertEqual(C.main(["review", "T1", "--verdict", "fail",
                                 "--detail", "out of scope", "--root", self.root]), 3)
        st = C.State.load(st.state_path)
        self.assertEqual(st.tasks["T1"]["review"]["verdict"], "fail")
        self.assertNotEqual(st.tasks["T1"]["status"], "proven")

    def test_start_refuses_a_task_that_is_not_ready(self):
        self.root = repo()
        plan = {"tasks": [{"id": "T1", "verify": [{"text": "t", "command": ["true"]}],
                           "depends_on": []},
                          {"id": "T2", "verify": [{"text": "t", "command": ["true"]}],
                           "depends_on": ["T1"]}]}
        C.State.new(root=self.root, run_id=C.new_run_id(), plan=plan, plan_envelope="e.json",
                    plan_sha256="0" * 64, grant_id="g", run_branch="factory/p",
                    base_branch="main", budget={})
        self.assertEqual(C.main(["start", "T2", "--root", self.root]), 2)
        self.assertEqual(C.main(["start", "T9", "--root", self.root]), 2)
        self.assertEqual(C.main(["start", "T1", "--root", self.root]), 0)
        self.assertEqual(C.main(["start", "T1", "--root", self.root]), 2)

    def test_verify_resolves_the_python_placeholder(self):
        st = self.state(verify=[{"text": "py", "command": ["{python}", "-c", "pass"]}])
        C.main(["start", "T1", "--root", self.root])
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 0)
        st = C.State.load(st.state_path)
        self.assertNotEqual(st.tasks["T1"]["verify_runs"][0]["command"][0], "{python}")

    def test_verify_runs_in_the_worktree(self):
        st = self.state(verify=[{"text": "marker", "command": ["test", "-f", "only-here"]}])
        C.main(["start", "T1", "--root", self.root])
        wt = C.State.load(st.state_path).tasks["T1"]["worktree"]
        open(os.path.join(wt, "only-here"), "w").write("x\n")
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 0)

    def test_the_run_directory_does_not_dirty_the_repo(self):
        self.state()
        C.main(["start", "T1", "--root", self.root])
        self.assertEqual(C.git(self.root, "status", "--porcelain").stdout.strip(), "")

    def test_merge_refuses_when_the_root_is_not_on_the_run_branch(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        C.main(["verify", "T1", "--root", self.root])
        C.main(["review", "T1", "--verdict", "pass", "--root", self.root])
        C.git(self.root, "checkout", "-q", "main")
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 2)
        st = C.State.load(st.state_path)
        self.assertEqual(st.tasks["T1"]["status"], "reviewing")

    def test_park_records_the_reason_and_blocks_nothing_else(self):
        st = self.state()
        self.assertEqual(C.main(["park", "T1", "--reason", "out of scope",
                                 "--root", self.root]), 0)
        st = C.State.load(st.state_path)
        self.assertEqual((st.tasks["T1"]["status"], st.tasks["T1"]["park_reason"]),
                         ("parked", "out of scope"))
        self.assertIn("park", [e["event"] for e in map(__import__("json").loads,
                                                       open(st.log_path))])

    def test_git_ignores_an_inherited_git_dir(self):
        self.root = repo()
        old = os.environ.get("GIT_DIR")
        os.environ["GIT_DIR"] = os.path.join(tempfile.mkdtemp(), "nowhere")
        try:
            r = C.git(self.root, "rev-parse", "--abbrev-ref", "HEAD")
        finally:
            if old is None:
                os.environ.pop("GIT_DIR", None)
            else:
                os.environ["GIT_DIR"] = old
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "factory/p"))


if __name__ == "__main__":
    unittest.main()
