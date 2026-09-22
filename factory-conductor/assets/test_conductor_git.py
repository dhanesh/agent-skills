import contextlib, hashlib, io, json, os, subprocess, sys, tempfile, time, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C
# start and merge are gated (Task 3): every run here has a real task-plan envelope and
# a human-accepted grant that pins it. repo() is on factory/p, cut from main.
from conductor_testkit import GIT, repo, new_run


def commit_in(d, name, text, msg="c"):
    """Write name in d and commit it there."""
    with open(os.path.join(d, name), "w") as f:
        f.write(text)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-q", "-m", msg], check=True,
                   env=dict(os.environ, **GIT))


@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class GitTests(unittest.TestCase):
    def state(self, verify=None):
        self.root = repo()
        tasks = {"title": "T", "spec": "s.md", "coverage": {}, "uncovered": [],
                 "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "one",
                            "verify": verify or [{"text": "t", "command": ["true"]}],
                            "depends_on": []}]}
        # verify re-reads the verify list from the pinned plan envelope, so it must exist
        st, self.plan_path = new_run(self.root, tasks)
        return st

    def marker(self):
        """An absolute path outside every checkout, for a child process to touch."""
        self._marker = os.path.join(tempfile.mkdtemp(), "late")
        return self._marker

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
        new_run(self.root, plan)
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
        # Task 6 review C1: exit 2 left the task `reviewing` forever and hung the run;
        # it now parks no-commits (exit 3), so the run can go on and finish.
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        self.through_review(st)
        before = C.git(self.root, "rev-parse", "HEAD").stdout.strip()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 3)
        self.assertIn("PARK: T1 no-commits", out.getvalue())
        self.assertNotIn("MERGE:", out.getvalue())
        self.assertEqual((self.task(st)["status"], self.task(st)["park_reason"]),
                         ("parked", "no-commits"))
        self.assertIsNone(self.task(st)["merge_commit"])
        self.assertEqual(C.git(self.root, "rev-parse", "HEAD").stdout.strip(), before)
        self.assertTrue(os.path.isdir(self.wt(st)))

    def test_a_crash_after_the_root_fast_forward_is_recovered_as_proven(self):
        # Task 6 round 2, N4: merge fast-forwards the root, then saves state. A crash
        # between the two leaves the merge in the run branch and the task reviewing;
        # the next merge must record that merge, not park the work as no-commits.
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        commit_in(self.wt(st), "b.txt", "b\n")
        self.through_review(st)
        pinned = self.task(st)["verified_head"]
        subprocess.run(["git", "-C", self.root, "merge", "-q", "--no-ff", "--no-edit", pinned],
                       check=True, env=dict(os.environ, **GIT))
        crashed = C.git(self.root, "rev-parse", "HEAD").stdout.strip()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 0)
        self.assertIn("MERGE: T1 %s" % crashed, out.getvalue())
        t = self.task(st)
        self.assertEqual((t["status"], t["merge_commit"], t["park_reason"]),
                         ("proven", crashed, None))
        self.assertEqual(C.git(self.root, "rev-parse", "HEAD").stdout.strip(), crashed)
        self.assertFalse(os.path.isdir(t["worktree"]))
        ev = [json.loads(l) for l in open(st.log_path) if '"merge"' in l]
        self.assertTrue(ev[-1].get("recovered"))
        self.assertEqual(ev[-1]["commit"], crashed)

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
        plan["predicate"]["payload"]["tasks"][0]["verify"] = [
            {"text": "x", "command": ["echo", "tampered"]}]
        with open(self.plan_path, "w") as f:
            json.dump(plan, f)
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 2)
        self.assertEqual(self.task(st)["verify_runs"], [])

    def test_verify_reads_a_task_plan_envelope_payload(self):
        self.root = repo()
        payload = {"tasks": [{"id": "T1", "verify": [{"text": "x", "command": ["true"]}],
                              "depends_on": []}]}
        st, path = new_run(self.root, payload)
        with open(path) as f:  # a real task-plan/v1 envelope: the tasks are in its payload
            doc = json.load(f)
        self.assertEqual(doc["predicateType"], C.TASK_PLAN_KIND)
        self.assertNotIn("tasks", doc)
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
        # resolved inside the conductor's detached checkout of the task's commit
        self.assertIn(os.sep + os.path.join("verify", "T1-"), run["command"][-1])
        self.assertTrue(run["command"][-1].endswith(
            os.path.join(".claude", "skills", "probe-skill", "x.py")), run["command"])

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
                                 ["sh", "-c", "(sleep 2; touch %s) & sleep 30" % self.marker()]}])
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
        time.sleep(3)  # a surviving background child would have written the marker by now
        self.assertFalse(os.path.exists(self._marker))

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

    def test_a_conflict_needs_no_abort_and_a_stopped_run_leaves_a_pending_merge_alone(self):
        # Fix round 4: the merge runs in a throwaway clone, so a conflict is never
        # aborted in the root and the old "merge-conflict-unaborted" state cannot arise.
        # Even with every `merge --abort` failing, the root is left clean.
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        wt = self.wt(st)
        commit_in(wt, "a.txt", "task\n")
        commit_in(self.root, "a.txt", "run\n")
        self.through_review(st)
        real = C.git
        aborts = []

        def fake(root, *args, **kw):
            if "--abort" in args:
                aborts.append(args)
                return subprocess.CompletedProcess(args, 128, "", "cannot abort")
            return real(root, *args, **kw)
        C.git = fake
        try:
            self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 3)
        finally:
            C.git = real
        self.assertEqual(aborts, [])
        st = C.State.load(st.state_path)
        self.assertEqual((st.tasks["T1"]["status"], st.tasks["T1"]["park_reason"]),
                         ("parked", "merge-conflict"))
        self.assertNotEqual(C.git(self.root, "rev-parse", "-q", "--verify",
                                  "MERGE_HEAD").returncode, 0)
        self.assertEqual(C.git(self.root, "status", "--porcelain").stdout.strip(), "")
        # B7: a stopped run MUST NOT touch a merge pending in the root
        C.git(self.root, "checkout", "-q", "-b", "other", "main")
        commit_in(self.root, "a.txt", "other\n")
        C.git(self.root, "checkout", "-q", "factory/p")
        C.git(self.root, "merge", "other")
        st.stopped = {"reason": "new_human_decision", "detail": "x", "at": "t"}
        st.set_status("T1", "reviewing")
        st.save()
        calls = []

        def spy(root, *args, **kw):
            calls.append(args)
            return real(root, *args, **kw)
        C.git = spy
        try:
            self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 3)
        finally:
            C.git = real
        self.assertFalse([a for a in calls if "merge" in a], calls)
        self.assertEqual(C.git(self.root, "rev-parse", "-q", "--verify",
                               "MERGE_HEAD").returncode, 0)
        C.git(self.root, "merge", "--abort")

    # --- fix round 2 ---

    def verify_dirs(self, st):
        d = os.path.join(st.dir, "verify")
        return os.listdir(d) if os.path.isdir(d) else []

    def test_an_excluded_helper_the_verify_needs_fails_the_verify(self):
        st = self.state(verify=[{"text": "helper", "command":
                                 ["sh", "-c", ". ./helper.sh && test \"$X\" = 1"]}])
        C.main(["start", "T1", "--root", self.root])
        wt = self.wt(st)
        commit_in(wt, "uses.txt", "needs helper\n")
        with open(os.path.join(self.root, ".git", "info", "exclude"), "a") as f:
            f.write("helper.sh\n")
        with open(os.path.join(wt, "helper.sh"), "w") as f:
            f.write("X=1\n")
        self.assertEqual(C.git(wt, "status", "--porcelain", "--untracked-files=all")
                         .stdout.strip(), "")
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)
        self.assertEqual(self.task(st)["status"], "verifying")

    def test_a_gitignored_generated_file_fails_the_verify(self):
        st = self.state(verify=[{"text": "gen", "command": ["test", "-f", "gen/out.bin"]}])
        C.main(["start", "T1", "--root", self.root])
        wt = self.wt(st)
        commit_in(wt, ".gitignore", "gen/\n")
        os.makedirs(os.path.join(wt, "gen"))
        with open(os.path.join(wt, "gen", "out.bin"), "wb") as f:
            f.write(b"\0")
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)

    def test_a_verify_that_writes_pycache_passes(self):
        st = self.state(verify=[{"text": "import", "command": ["{python}", "-c", "import m"]}])
        C.main(["start", "T1", "--root", self.root])
        commit_in(self.wt(st), "m.py", "X = 1\n")
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 0)
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 2)  # now reviewing

    def test_the_verify_checkout_is_removed_after_a_pass_and_a_fail(self):
        for cmd, rc in ((["true"], 0), (["false"], 3)):
            st = self.state(verify=[{"text": "x", "command": cmd}])
            C.main(["start", "T1", "--root", self.root])
            commit_in(self.wt(st), "b.txt", "b\n")
            self.assertEqual(C.main(["verify", "T1", "--root", self.root]), rc)
            self.assertEqual(self.verify_dirs(st), [])
            self.assertNotIn(os.sep + "verify" + os.sep,
                             C.git(self.root, "worktree", "list").stdout)

    def test_verify_runs_the_pinned_commit_in_a_detached_checkout(self):
        st = self.state(verify=[{"text": "x", "command":
                                 ["sh", "-c", "git rev-parse HEAD > %s && git symbolic-ref "
                                  "-q HEAD >> %s || true" % (self.marker(), self._marker)]}])
        C.main(["start", "T1", "--root", self.root])
        commit_in(self.wt(st), "b.txt", "b\n")
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 0)
        with open(self._marker) as f:
            lines = f.read().split()
        self.assertEqual(lines, [self.task(st)["verified_head"]])  # detached: no symbolic ref

    def test_a_child_with_detached_stdio_does_not_outlive_a_passing_command(self):
        m = self.marker()
        st = self.state(verify=[{"text": "x", "command":
                                 ["sh", "-c", "(sleep 2; touch %s) </dev/null >/dev/null "
                                  "2>&1 & exit 0" % m]}])
        C.main(["start", "T1", "--root", self.root])
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 0)
        time.sleep(3)
        self.assertFalse(os.path.exists(m), "the background child survived the verify")

    def test_a_stopped_run_refuses_start_verify_review_and_merge(self):
        st = self.state()
        s = C.State.load(st.state_path)
        s.stopped = {"reason": "new_human_decision", "detail": "x", "at": "t"}
        s.save()
        for cmd in (["start", "T1"], ["verify", "T1"], ["review", "T1", "--verdict", "pass"],
                    ["merge", "T1"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(C.main(cmd + ["--root", self.root]), 3, cmd)
            self.assertIn("STOP: new_human_decision", out.getvalue(), cmd)
        self.assertEqual(self.task(st)["status"], "pending")

    def _merge_with(self, st, fake_factory):
        real = C.git
        C.git = fake_factory(real)
        try:
            return C.main(["merge", "T1", "--root", self.root])
        finally:
            C.git = real

    def test_a_merge_that_is_not_two_parent_parks_and_stops(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        commit_in(self.wt(st), "b.txt", "b\n")
        self.through_review(st)

        def factory(real):
            def fake(root, *args, **kw):
                if "--parents" in args:
                    return subprocess.CompletedProcess(args, 0, "a" * 40 + " " + "b" * 40 + "\n", "")
                return real(root, *args, **kw)
            return fake
        self.assertEqual(self._merge_with(st, factory), 3)
        st = C.State.load(st.state_path)
        self.assertEqual((st.tasks["T1"]["status"], st.tasks["T1"]["park_reason"]),
                         ("parked", "merge-inconsistent"))
        self.assertEqual((st.stopped["reason"], st.stopped["detail"]),
                         ("new_human_decision", "merge-inconsistent"))

    def test_a_run_branch_that_moves_during_merge_parks_and_stops(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        commit_in(self.wt(st), "b.txt", "b\n")
        self.through_review(st)
        root = self.root

        def factory(real):
            def fake(r, *args, **kw):
                if "merge" in args and "--no-ff" in args:
                    commit_in(root, "race.txt", "r\n", "race")  # lands between checks
                return real(r, *args, **kw)
            return fake
        self.assertEqual(self._merge_with(st, factory), 3)
        st = C.State.load(st.state_path)
        self.assertEqual((st.tasks["T1"]["status"], st.tasks["T1"]["park_reason"]),
                         ("parked", "merge-inconsistent"))
        self.assertEqual(st.stopped["detail"], "merge-inconsistent")


    # --- fix round 3 ---

    def common_dir(self, wt):
        cd = C.git(wt, "rev-parse", "--git-common-dir").stdout.strip()
        return cd if os.path.isabs(cd) else os.path.join(wt, cd)

    def test_a_smudge_filter_in_the_shared_config_does_not_reach_the_verify(self):
        st = self.state(verify=[{"text": "helper", "command":
                                 ["sh", "-c", ". ./helper.sh && test \"$X\" = 1"]}])
        C.main(["start", "T1", "--root", self.root])
        wt = self.wt(st)
        commit_in(wt, "helper.sh", "X=0\n")
        subprocess.run(["git", "-C", wt, "config", "filter.ev.smudge", "sed s/X=0/X=1/"],
                       check=True)
        subprocess.run(["git", "-C", wt, "config", "filter.ev.clean", "cat"], check=True)
        info = os.path.join(self.common_dir(wt), "info")
        os.makedirs(info, exist_ok=True)
        with open(os.path.join(info, "attributes"), "a") as f:
            f.write("helper.sh filter=ev\n")
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)

    def test_a_planted_fsmonitor_never_runs_under_the_conductor(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        wt = self.wt(st)
        commit_in(wt, "b.txt", "b\n")
        m = self.marker()
        script = os.path.join(tempfile.mkdtemp(), "fsmon.sh")
        with open(script, "w") as f:
            f.write("#!/bin/sh\ntouch %s\nexit 1\n" % m)
        os.chmod(script, 0o755)
        subprocess.run(["git", "-C", wt, "config", "core.fsmonitor", script], check=True)
        self.through_review(st)
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 0)
        self.assertFalse(os.path.exists(m), "the planted fsmonitor ran")

    def test_a_relative_symlink_that_escapes_the_checkout_fails_the_verify(self):
        st = self.state(verify=[{"text": "t", "command": ["touch", self.marker()]}])
        C.main(["start", "T1", "--root", self.root])
        wt = self.wt(st)
        with open(os.path.join(self.common_dir(wt), "info", "exclude"), "a") as f:
            f.write("helper.sh\n")
        with open(os.path.join(wt, "helper.sh"), "w") as f:
            f.write("X=1\n")
        os.symlink("../../wt/T1/helper.sh", os.path.join(wt, "link.sh"))
        C.git(wt, "add", "link.sh")
        subprocess.run(["git", "-C", wt, "commit", "-q", "-m", "l"], check=True,
                       env=dict(os.environ, **GIT))
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)
        self.assertFalse(os.path.exists(self._marker), "a command ran")
        self.assertEqual(self.task(st)["status"], "verifying")

    def test_an_absolute_symlink_fails_the_verify(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        wt = self.wt(st)
        os.symlink("/etc/hosts", os.path.join(wt, "abs.lnk"))
        C.git(wt, "add", "abs.lnk")
        subprocess.run(["git", "-C", wt, "commit", "-q", "-m", "l"], check=True,
                       env=dict(os.environ, **GIT))
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)

    def test_an_in_tree_relative_symlink_still_verifies(self):
        st = self.state(verify=[{"text": "t", "command": ["sh", "-c", "test \"$(cat sub/l)\" = x"]}])
        C.main(["start", "T1", "--root", self.root])
        wt = self.wt(st)
        os.makedirs(os.path.join(wt, "sub"))
        with open(os.path.join(wt, "t.txt"), "w") as f:
            f.write("x")
        os.symlink("../t.txt", os.path.join(wt, "sub", "l"))
        C.git(wt, "add", "-A")
        subprocess.run(["git", "-C", wt, "commit", "-q", "-m", "l"], check=True,
                       env=dict(os.environ, **GIT))
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 0)

    def test_merge_refuses_a_pending_merge_it_did_not_record(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        commit_in(self.wt(st), "a.txt", "task\n")
        self.through_review(st)
        # a human (or a crash) left a conflicted merge of another branch in the root
        C.git(self.root, "checkout", "-q", "-b", "other", "main")
        commit_in(self.root, "a.txt", "other\n")
        C.git(self.root, "checkout", "-q", "factory/p")
        commit_in(self.root, "a.txt", "run\n")
        C.git(self.root, "merge", "other")
        self.assertEqual(C.git(self.root, "rev-parse", "-q", "--verify",
                               "MERGE_HEAD").returncode, 0)
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 2)
        self.assertEqual(C.git(self.root, "rev-parse", "-q", "--verify",
                               "MERGE_HEAD").returncode, 0, "the pending merge was aborted")
        self.assertEqual(self.task(st)["status"], "reviewing")
        C.git(self.root, "merge", "--abort")

    def test_a_leftover_locked_checkout_from_a_crash_is_replaced(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        wt = self.wt(st)
        commit_in(wt, "b.txt", "b\n")
        head = C.git(wt, "rev-parse", "HEAD").stdout.strip()
        p = os.path.join(st.dir, "verify", "T1-" + head[:12])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        C.git(self.root, "worktree", "add", "-q", "--detach", p, head)
        C.git(self.root, "worktree", "lock", "--reason", "initializing", p)
        with open(os.path.join(p, "junk"), "w") as f:
            f.write("j")
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 0)
        self.assertEqual(self.verify_dirs(st), [])
        self.assertNotIn(os.sep + "verify" + os.sep,
                         C.git(self.root, "worktree", "list").stdout)

    def test_a_symlink_planted_at_the_verify_path_is_not_followed(self):
        st = self.state(verify=[{"text": "t", "command": ["true"]}])
        C.main(["start", "T1", "--root", self.root])
        wt = self.wt(st)
        commit_in(wt, "b.txt", "b\n")
        head = C.git(wt, "rev-parse", "HEAD").stdout.strip()
        evil = tempfile.mkdtemp()
        with open(os.path.join(evil, "keep"), "w") as f:
            f.write("k")
        p = os.path.join(st.dir, "verify", "T1-" + head[:12])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        os.symlink(evil, p)
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 0)
        self.assertEqual(os.listdir(evil), ["keep"])
        self.assertEqual(self.verify_dirs(st), [])

    # --- fix round 4 ---

    def merge_dirs(self, st):
        d = os.path.join(st.dir, "merge")
        return os.listdir(d) if os.path.isdir(d) else []

    def tree_of(self, rev="HEAD"):
        """{path: bytes} of rev in the root, read with replace objects and grafts off."""
        env = dict(os.environ, GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull)
        names = subprocess.run(["git", "-C", self.root, "-c", "core.commitGraph=false",
                                "ls-tree", "-r", "--name-only", rev], env=env,
                               capture_output=True, text=True, check=True).stdout.split()
        return {n: subprocess.run(["git", "-C", self.root, "-c", "core.commitGraph=false",
                                   "show", "%s:%s" % (rev, n)], env=env, capture_output=True,
                                  text=True, check=True).stdout for n in names}

    def diverged(self, st):
        """T1 changes line 1 of f.txt, the run branch changes line 7: a clean 3-way merge."""
        commit_in(self.root, "f.txt", "1\n2\n3\n4\n5\n6\n7\n", "base f")
        C.main(["start", "T1", "--root", self.root])
        wt = self.wt(st)
        commit_in(wt, "f.txt", "ONE\n2\n3\n4\n5\n6\n7\n", "task")
        commit_in(self.root, "f.txt", "1\n2\n3\n4\n5\n6\nSEVEN\n", "run")
        self.through_review(st)
        return wt

    def test_every_conductor_git_call_ignores_replace_objects_grafts_and_commit_graph(self):
        self.root = repo()
        env = C.git_env()
        self.assertEqual(env.get("GIT_NO_REPLACE_OBJECTS"), "1")
        self.assertEqual(env.get("GIT_GRAFT_FILE"), os.devnull)
        self.assertEqual(C.git(self.root, "config", "core.commitGraph").stdout.strip(), "false")
        self.assertEqual(C.isolated_env().get("GIT_NO_REPLACE_OBJECTS"), "1")

    def test_a_replace_ref_planted_after_review_does_not_change_what_is_merged(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        wt = self.wt(st)
        commit_in(wt, "helper.sh", "X=1\n")
        pinned = C.git(wt, "rev-parse", "HEAD").stdout.strip()
        self.through_review(st)
        # an evil sibling commit E, then refs/replace/<pinned> -> E
        C.git(wt, "checkout", "-q", "--detach", "HEAD~1")
        commit_in(wt, "helper.sh", "X=0\n")
        commit_in(wt, "evil.txt", "evil\n")
        evil = C.git(wt, "rev-parse", "HEAD").stdout.strip()
        C.git(wt, "checkout", "-q", pinned)
        subprocess.run(["git", "-C", wt, "replace", pinned, evil], check=True)
        rc = C.main(["merge", "T1", "--root", self.root])
        tree = self.tree_of()
        self.assertNotIn("evil.txt", tree, "E's content was merged under P's name")
        if rc == 0:
            self.assertEqual(tree.get("helper.sh"), "X=1\n")
            self.assertEqual(self.tree_of(pinned), self.tree_of("HEAD^2"))

    def test_a_planted_merge_driver_does_not_rewrite_the_merged_bytes(self):
        st = self.state()
        wt = self.diverged(st)
        subprocess.run(["git", "-C", wt, "config", "merge.evil.driver",
                        "sh -c 'printf PWNED > %A'"], check=True)
        with open(os.path.join(self.common_dir(wt), "info", "attributes"), "a") as f:
            f.write("f.txt merge=evil\n")
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 0)
        self.assertEqual(self.tree_of()["f.txt"], "ONE\n2\n3\n4\n5\n6\nSEVEN\n")
        self.assertEqual(self.merge_dirs(st), [])

    def test_a_planted_graft_does_not_move_the_merge_base(self):
        st = self.state()
        wt = self.diverged(st)
        pinned = self.task(st)["verified_head"]
        run_head = C.git(self.root, "rev-parse", "HEAD").stdout.strip()
        # graft: the task commit now claims the run branch head as its only parent,
        # which would make the merge "revert" SEVEN
        with open(os.path.join(self.common_dir(wt), "info", "grafts"), "w") as f:
            f.write("%s %s\n" % (pinned, run_head))
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 0)
        self.assertEqual(self.tree_of()["f.txt"], "ONE\n2\n3\n4\n5\n6\nSEVEN\n")

    def test_the_merge_happens_in_a_clone_and_the_root_fast_forwards_to_it(self):
        st = self.state()
        self.diverged(st)
        before = C.git(self.root, "rev-parse", "HEAD").stdout.strip()
        pinned = self.task(st)["verified_head"]
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 0)
        line = C.git(self.root, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(line[1:], [before, pinned])
        self.assertEqual(C.current_branch(self.root) if hasattr(C, "current_branch")
                         else C.CC.current_branch(self.root), "factory/p")
        self.assertEqual(self.task(st)["merge_commit"], line[0])
        self.assertEqual(self.merge_dirs(st), [])
        self.assertEqual(C.git(self.root, "status", "--porcelain").stdout.strip(), "")
        self.assertEqual(open(os.path.join(self.root, "f.txt")).read(),
                         "ONE\n2\n3\n4\n5\n6\nSEVEN\n")

    def test_a_conflict_in_the_merge_clone_never_touches_the_root(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        commit_in(self.wt(st), "a.txt", "task\n")
        commit_in(self.root, "a.txt", "run\n")
        self.through_review(st)
        before = C.git(self.root, "rev-parse", "HEAD").stdout.strip()
        calls = []
        real = C.git

        def spy(root, *args, **kw):
            if os.path.realpath(root) == os.path.realpath(self.root):
                calls.append(args)
            return real(root, *args, **kw)
        C.git = spy
        try:
            self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 3)
        finally:
            C.git = real
        self.assertFalse([a for a in calls if "merge" in a], "a merge ran in the root")
        self.assertEqual(C.git(self.root, "rev-parse", "HEAD").stdout.strip(), before)
        self.assertNotEqual(C.git(self.root, "rev-parse", "-q", "--verify",
                                  "MERGE_HEAD").returncode, 0)
        self.assertEqual(C.git(self.root, "status", "--porcelain").stdout.strip(), "")
        self.assertEqual(self.task(st)["park_reason"], "merge-conflict")
        self.assertEqual(self.merge_dirs(st), [])


if __name__ == "__main__":
    unittest.main()
