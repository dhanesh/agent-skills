import hashlib, json, os, subprocess, sys, tempfile, unittest
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


def commit_in(d, name, text, msg="c"):
    """Write name in d and commit it there."""
    with open(os.path.join(d, name), "w") as f:
        f.write(text)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-q", "-m", msg], check=True,
                   env=dict(os.environ, **GIT))


def write_plan(tasks):
    """Write the plan outside the repo; (absolute path, sha256 of its bytes)."""
    path = os.path.join(tempfile.mkdtemp(), "plan.json")
    raw = json.dumps(tasks).encode("utf-8")
    with open(path, "wb") as f:
        f.write(raw)
    return path, hashlib.sha256(raw).hexdigest()


@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class GitTests(unittest.TestCase):
    def state(self, verify=None):
        self.root = repo()
        tasks = {"title": "T", "spec": "s.md", "coverage": {}, "uncovered": [],
                 "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "one",
                            "verify": verify or [{"text": "t", "command": ["true"]}],
                            "depends_on": []}]}
        # verify re-reads the verify list from the pinned plan file, so it must exist
        self.plan_path, sha = write_plan(tasks)
        return C.State.new(root=self.root, run_id=C.new_run_id(), plan=tasks,
                           plan_envelope=self.plan_path, plan_sha256=sha, grant_id="g",
                           run_branch="factory/p", base_branch="main", budget={})

    def wt(self, st):
        return C.State.load(st.state_path).tasks["T1"]["worktree"]

    def task(self, st):
        return C.State.load(st.state_path).tasks["T1"]

    def through_review(self, st):
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 0)
        self.assertEqual(C.main(["review", "T1", "--verdict", "pass", "--root", self.root]), 0)

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
        commit_in(wt, "only-here", "x\n")
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 0)

    def test_the_run_directory_does_not_dirty_the_repo(self):
        self.state()
        C.main(["start", "T1", "--root", self.root])
        self.assertEqual(C.git(self.root, "status", "--porcelain").stdout.strip(), "")

    def test_merge_refuses_when_the_root_is_not_on_the_run_branch(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        commit_in(self.wt(st), "b.txt", "b\n")
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


    # --- proof invariants (fix round 1) ---

    def test_a_commit_made_after_verify_and_review_is_not_merged(self):
        st = self.state(verify=[{"text": "no bad", "command": ["sh", "-c", "! test -f bad.txt"]}])
        C.main(["start", "T1", "--root", self.root])
        wt = self.wt(st)
        commit_in(wt, "good.txt", "g\n")
        self.through_review(st)
        commit_in(wt, "bad.txt", "bad\n", "sneaky")
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 2)
        self.assertFalse(os.path.exists(os.path.join(self.root, "bad.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "good.txt")))
        self.assertEqual(self.task(st)["status"], "verifying")

    def test_verify_records_the_head_it_proved_and_review_pins_it(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        commit_in(self.wt(st), "b.txt", "b\n")
        self.through_review(st)
        head = C.git(self.wt(st), "rev-parse", "HEAD").stdout.strip()
        t = self.task(st)
        self.assertEqual(t["verified_head"], head)
        self.assertEqual(t["review"]["verified_head"], head)

    def test_uncommitted_work_does_not_verify(self):
        st = self.state(verify=[{"text": "marker", "command": ["touch", "ran"]}])
        C.main(["start", "T1", "--root", self.root])
        wt = self.wt(st)
        open(os.path.join(wt, "feature.txt"), "w").write("f\n")
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)
        self.assertFalse(os.path.exists(os.path.join(wt, "ran")), "a command ran")
        t = self.task(st)
        self.assertEqual(t["status"], "verifying")
        self.assertEqual(t["verify_runs"], [])
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 2)

    def test_a_task_with_no_committed_work_is_not_merged(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        self.through_review(st)
        before = C.git(self.root, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 2)
        self.assertNotEqual(self.task(st)["status"], "proven")
        self.assertEqual(C.git(self.root, "rev-parse", "HEAD").stdout.strip(), before)
        self.assertTrue(os.path.isdir(self.wt(st)))

    def test_merge_is_a_two_parent_commit_of_the_verified_head(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        commit_in(self.wt(st), "b.txt", "b\n")
        self.through_review(st)
        head = self.task(st)["verified_head"]
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 0)
        parents = C.git(self.root, "log", "-1", "--format=%P").stdout.split()
        self.assertEqual(len(parents), 2)
        self.assertEqual(parents[1], head)
        self.assertEqual(self.task(st)["merge_commit"],
                         C.git(self.root, "rev-parse", "HEAD").stdout.strip())

    def test_merge_deletes_the_task_branch(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        commit_in(self.wt(st), "b.txt", "b\n")
        self.through_review(st)
        C.main(["merge", "T1", "--root", self.root])
        self.assertEqual(C.git(self.root, "branch", "--list", "factory/p--T1").stdout.strip(), "")
        self.assertNotIn("/wt/T1", C.git(self.root, "worktree", "list").stdout)

    def test_verify_reads_commands_from_the_plan_not_from_state_json(self):
        st = self.state(verify=[{"text": "x", "command": ["false"]}])
        C.main(["start", "T1", "--root", self.root])
        with open(st.state_path) as f:
            data = json.load(f)
        data["tasks"]["T1"]["verify"] = [{"text": "x", "command": ["true"]}]
        with open(st.state_path, "w") as f:
            json.dump(data, f)
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)
        self.assertEqual(self.task(st)["verify_runs"][0]["command"], ["false"])

    def test_verify_refuses_a_plan_whose_sha256_changed(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        with open(self.plan_path) as f:
            plan = json.load(f)
        plan["tasks"][0]["verify"] = [{"text": "x", "command": ["echo", "tampered"]}]
        with open(self.plan_path, "w") as f:
            json.dump(plan, f)
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 2)
        self.assertEqual(self.task(st)["verify_runs"], [])

    def test_verify_reads_a_task_plan_envelope_payload(self):
        self.root = repo()
        payload = {"tasks": [{"id": "T1", "verify": [{"text": "x", "command": ["true"]}],
                              "depends_on": []}]}
        path, sha = write_plan({"_type": "x", "predicate": {"payload": payload}})
        st = C.State.new(root=self.root, run_id=C.new_run_id(), plan=payload,
                         plan_envelope=path, plan_sha256=sha, grant_id="g",
                         run_branch="factory/p", base_branch="main", budget={})
        C.main(["start", "T1", "--root", self.root])
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 0)
        self.assertEqual(self.task(st)["verify_runs"][0]["command"], ["true"])

    def test_skill_dir_resolves_the_worktree_copy_of_a_project_skill(self):
        st = self.state(verify=[{"text": "x", "command":
                                 ["{python}", "{skill_dir:probe-skill}/x.py"]}])
        C.main(["start", "T1", "--root", self.root])
        wt = self.wt(st)
        sd = os.path.join(wt, ".claude", "skills", "probe-skill")
        os.makedirs(sd)
        with open(os.path.join(sd, "SKILL.md"), "w") as f:
            f.write("---\nname: probe-skill\ndescription: d\n---\nbody\n")
        commit_in(sd, "x.py", "print('ok')\n")
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 0)
        run = self.task(st)["verify_runs"][0]
        self.assertTrue(run["command"][-1].startswith(os.path.realpath(wt))
                        or run["command"][-1].startswith(wt), run["command"])

    def test_an_unresolvable_placeholder_is_recorded_as_a_failure(self):
        st = self.state(verify=[{"text": "x", "command":
                                 ["{python}", "{skill_dir:no-such-skill-zz}/x.py"]}])
        C.main(["start", "T1", "--root", self.root])
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)
        run = self.task(st)["verify_runs"][0]
        self.assertEqual((run["ok"], run["returncode"]), (False, None))
        self.assertIn("cannot resolve", run["stderr_tail"])

    def test_a_command_that_is_not_a_list_fails(self):
        st = self.state(verify=[{"text": "x", "command": "true"}])
        C.main(["start", "T1", "--root", self.root])
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)
        self.assertFalse(self.task(st)["verify_runs"][0]["ok"])

    def test_a_verify_that_times_out_fails_and_its_process_group_is_killed(self):
        st = self.state(verify=[{"text": "x", "command":
                                 ["sh", "-c", "(sleep 2; touch late) & sleep 30"]}])
        C.main(["start", "T1", "--root", self.root])
        old = C.VERIFY_TIMEOUT
        C.VERIFY_TIMEOUT = 1
        try:
            import time
            t0 = time.monotonic()
            rc = C.main(["verify", "T1", "--root", self.root])
            elapsed = time.monotonic() - t0
        finally:
            C.VERIFY_TIMEOUT = old
        self.assertEqual(rc, 3)
        self.assertLess(elapsed, 15)
        run = self.task(st)["verify_runs"][0]
        self.assertEqual((run["ok"], run["returncode"]), (False, None))
        self.assertIn("timed out", run["stderr_tail"])
        time.sleep(3)  # a surviving background child would have written `late` by now
        self.assertFalse(os.path.exists(os.path.join(self.wt(st), "late")))

    def test_hooks_planted_by_the_executor_do_not_run_in_conductor_git_calls(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        wt = self.wt(st)
        commit_in(wt, "b.txt", "b\n")
        hooks = os.path.join(self.root, ".git", "hooks")
        os.makedirs(hooks, exist_ok=True)
        for name in ("post-merge", "pre-merge-commit", "post-checkout", "reference-transaction"):
            hp = os.path.join(hooks, name)
            with open(hp, "w") as f:
                f.write("#!/bin/sh\ntouch %s\n" % os.path.join(self.root, "HOOK_" + name))
            os.chmod(hp, 0o755)
        self.through_review(st)
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 0)
        self.assertEqual([n for n in os.listdir(self.root) if n.startswith("HOOK_")], [])

    def test_a_merge_that_cannot_be_aborted_stops_the_run(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        wt = self.wt(st)
        commit_in(wt, "a.txt", "task\n")
        commit_in(self.root, "a.txt", "run\n")
        self.through_review(st)
        real = C.git

        def fake(root, *args, **kw):
            if "--abort" in args:
                return subprocess.CompletedProcess(args, 128, "", "cannot abort")
            return real(root, *args, **kw)
        C.git = fake
        try:
            self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 3)
        finally:
            C.git = real
        st = C.State.load(st.state_path)
        self.assertEqual((st.tasks["T1"]["status"], st.tasks["T1"]["park_reason"]),
                         ("parked", "merge-conflict-unaborted"))
        self.assertIsNotNone(st.stopped)
        C.git(self.root, "merge", "--abort")


if __name__ == "__main__":
    unittest.main()
