"""resume prints the exact next action: one NEXT: line per in-flight task, one for the run.

A fresh agent session with no memory of the run (a new session, `claude --continue`, a
scheduled re-entry) runs `conductor resume` and does what each NEXT: line says:

    NEXT: <task> dispatch-executor           running: re-dispatch into the existing worktree
    NEXT: <task> dispatch-repair verify      verifying after a verify fail
    NEXT: <task> dispatch-repair review      verifying after a review fail
    NEXT: <task> dispatch-reviewer <sha>     reviewing, no verdict yet
    NEXT: <task> merge                       reviewing, verdict pass
    NEXT: run next | NEXT: run finish        the run line, always last

The walk at the end crashes a run with four tasks in four states and reaches FINISH:
by following nothing but those lines.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C  # noqa: E402
from conductor_testkit import GIT, repo, write_grant, write_plan_envelope  # noqa: E402


def tasks(n):
    """T1..Tn, independent; Tk is proven by its own file tk.txt."""
    return {"T%d" % i: ([], [{"text": "work exists", "command": ["test", "-f", "t%d.txt" % i]}])
            for i in range(1, n + 1)}


def commit_in(wt, name):
    with open(os.path.join(wt, name), "w") as f:
        f.write("x\n")
    subprocess.run(["git", "-C", wt, "add", "-A"], check=True)
    subprocess.run(["git", "-C", wt, "commit", "-q", "-m", "c"], check=True,
                   env=dict(os.environ, **GIT))


@unittest.skipUnless(shutil.which("git"), "git not installed")
class ResumeNextTests(unittest.TestCase):
    def init(self, n=1, parallel=None):
        self.root = repo()
        self.plan = write_plan_envelope(self.root, tasks(n))
        write_grant(self.root, self.plan)
        argv = ["init", "--plan", self.plan, "--root", self.root]
        if parallel:
            argv += ["--budget", json.dumps({"max_parallel": parallel})]
        rc, _, err = self.run_main(argv)
        self.assertEqual(rc, 0, err)

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = C.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def st(self):
        return C.State.load(C.state_path(self.root))

    def ok(self, *argv):
        rc, out, err = self.run_main(list(argv) + ["--root", self.root])
        self.assertIn(rc, (0, 3), err)
        return out

    def resume(self):
        rc, out, _ = self.run_main(["resume", "--root", self.root])
        return rc, [l for l in out.splitlines() if l.startswith("NEXT: ")], out

    # ── one line per in-flight state ────────────────────────────────────────
    def test_running_says_dispatch_executor_and_run_next(self):
        self.init()
        self.ok("start", "T1")
        rc, nxt, out = self.resume()
        self.assertEqual(rc, 0, out)
        self.assertEqual(nxt, ["NEXT: T1 dispatch-executor", "NEXT: run next"])

    def test_verifying_after_a_verify_fail_says_dispatch_repair_verify(self):
        self.init()
        self.ok("start", "T1")
        commit_in(self.st().tasks["T1"]["worktree"], "other.txt")
        self.assertIn("VERIFY: T1 fail", self.ok("verify", "T1"))
        rc, nxt, out = self.resume()
        self.assertEqual(rc, 0, out)
        self.assertEqual(nxt, ["NEXT: T1 dispatch-repair verify", "NEXT: run next"])

    def test_verifying_after_a_review_fail_says_dispatch_repair_review(self):
        self.init()
        self.ok("start", "T1")
        commit_in(self.st().tasks["T1"]["worktree"], "t1.txt")
        self.ok("verify", "T1")
        self.ok("review", "T1", "--verdict", "fail", "--detail", "too broad")
        rc, nxt, out = self.resume()
        self.assertEqual(rc, 0, out)
        self.assertEqual(nxt, ["NEXT: T1 dispatch-repair review", "NEXT: run next"])

    def test_a_refused_verify_of_the_rejected_commit_still_says_review(self):
        # verify refuses the very commit the reviewer rejected and keeps the review:
        # the repair still needs the reviewer's detail.
        self.init()
        self.ok("start", "T1")
        commit_in(self.st().tasks["T1"]["worktree"], "t1.txt")
        self.ok("verify", "T1")
        self.ok("review", "T1", "--verdict", "fail", "--detail", "too broad")
        self.ok("verify", "T1")
        _, nxt, _ = self.resume()
        self.assertEqual(nxt, ["NEXT: T1 dispatch-repair review", "NEXT: run next"])

    def test_reviewing_without_a_verdict_says_dispatch_reviewer_with_the_sha(self):
        self.init()
        self.ok("start", "T1")
        commit_in(self.st().tasks["T1"]["worktree"], "t1.txt")
        self.ok("verify", "T1")
        sha = self.st().tasks["T1"]["verified_head"]
        self.assertRegex(sha, r"^[0-9a-f]{40}$")
        rc, nxt, out = self.resume()
        self.assertEqual(rc, 0, out)
        self.assertEqual(nxt, ["NEXT: T1 dispatch-reviewer %s" % sha, "NEXT: run next"])

    def test_reviewing_with_a_pass_says_merge(self):
        self.init()
        self.ok("start", "T1")
        commit_in(self.st().tasks["T1"]["worktree"], "t1.txt")
        self.ok("verify", "T1")
        self.ok("review", "T1", "--verdict", "pass")
        rc, nxt, out = self.resume()
        self.assertEqual(rc, 0, out)
        self.assertEqual(nxt, ["NEXT: T1 merge", "NEXT: run next"])

    # ── the run line ────────────────────────────────────────────────────────
    def test_nothing_in_flight_and_a_task_ready_says_run_next(self):
        self.init(n=2)
        rc, nxt, out = self.resume()
        self.assertEqual(rc, 0, out)
        self.assertEqual(nxt, ["NEXT: run next"])
        self.assertIn("READY: T1 T2", out)

    def test_nothing_in_flight_and_nothing_ready_records_the_stop_and_says_finish(self):
        self.init()
        self.ok("park", "T1", "--reason", "by hand")
        rc, nxt, out = self.resume()
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: no_ready_tasks", out)
        self.assertEqual(nxt, ["NEXT: run finish"])
        self.assertEqual(self.st().stopped["reason"], "no_ready_tasks")

    def test_a_stopped_run_says_finish_and_names_no_task(self):
        self.init()
        self.ok("start", "T1")  # in flight, but a stopped run takes no further step
        st = self.st()
        st.stopped = {"reason": "budget_wall_clock", "at": "t"}
        st.save()
        rc, nxt, out = self.resume()
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: budget_wall_clock", out)
        self.assertEqual(nxt, ["NEXT: run finish"])

    def test_a_grant_ask_that_still_asks_says_finish(self):
        self.init()
        subprocess.run([sys.executable, "-I",
                        os.path.join(os.path.dirname(C.__file__), "contract_check.py"),
                        "revoke-grant", "--root", self.root], check=True, capture_output=True)
        rc, nxt, out = self.resume()
        self.assertEqual(rc, 3, out)
        self.assertIn("GATE: ASK", out)
        self.assertEqual(nxt, ["NEXT: run finish"])

    def test_the_wall_clock_ends_the_run_even_with_work_in_flight(self):
        self.init()
        self.ok("start", "T1")
        st = self.st()
        st.budget["wall_clock_min"] = 0
        st.save()
        rc, nxt, out = self.resume()
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: budget_wall_clock", out)
        self.assertEqual(nxt, ["NEXT: run finish"])

    def test_task_lines_come_in_plan_order_and_the_run_line_last(self):
        self.init(n=3, parallel=3)
        for t in ("T1", "T2", "T3"):
            self.ok("start", t)
        commit_in(self.st().tasks["T2"]["worktree"], "t2.txt")
        self.ok("verify", "T2")
        rc, nxt, out = self.resume()
        self.assertEqual(rc, 0, out)
        sha = self.st().tasks["T2"]["verified_head"]
        self.assertEqual(nxt, ["NEXT: T1 dispatch-executor",
                               "NEXT: T2 dispatch-reviewer %s" % sha,
                               "NEXT: T3 dispatch-executor", "NEXT: run next"])
        for line in nxt:
            self.assertEqual(line[:6], "NEXT: ")

    def test_resume_only_appends_to_the_log(self):
        self.init()
        self.ok("start", "T1")
        with open(self.st().log_path, "rb") as f:
            before = f.read()
        self.resume()
        with open(self.st().log_path, "rb") as f:
            self.assertTrue(f.read().startswith(before))

    # ── a no-memory resume walk ─────────────────────────────────────────────
    def test_a_fresh_session_follows_only_next_lines_from_a_crash_to_finish(self):
        """Four tasks crash in four states; the walker keeps nothing between steps but
        what `resume` prints, and acts on each NEXT: line as SKILL.md says."""
        self.init(n=4, parallel=4)
        for t in ("T1", "T2", "T3", "T4"):
            self.ok("start", t)
        wt = {t: self.st().tasks[t]["worktree"] for t in ("T2", "T3", "T4")}
        # T1 running (its executor died); T2 verifying after a verify fail;
        # T3 reviewing with no verdict; T4 reviewing with a pass.
        commit_in(wt["T2"], "other.txt")
        self.ok("verify", "T2")
        commit_in(wt["T3"], "t3.txt")
        self.ok("verify", "T3")
        commit_in(wt["T4"], "t4.txt")
        self.ok("verify", "T4")
        self.ok("review", "T4", "--verdict", "pass")
        self.assertEqual([self.st().tasks[t]["status"] for t in ("T1", "T2", "T3", "T4")],
                         ["running", "verifying", "reviewing", "reviewing"])

        finished, seen = None, []
        for _ in range(10):
            rc, nxt, out = self.resume()
            seen.append(nxt)
            run_id = next(l.split()[1][len("run="):] for l in out.splitlines()
                          if l.startswith("STATUS: run="))
            for line in nxt:
                parts = line.split()[1:]
                if parts == ["run", "finish"]:
                    rc, out, err = self.run_main(["finish", "--root", self.root])
                    finished = [l for l in out.splitlines() if l.startswith("FINISH: ")]
                    break
                if parts == ["run", "next"]:
                    out = self.ok("next")
                    for tid in (out.split()[1:] if out.startswith("READY:") else []):
                        self.ok("start", tid)
                    continue
                tid, action = parts[0], parts[1]
                if action in ("dispatch-executor", "dispatch-repair"):
                    # The executor works in the task's worktree, found from the run id alone.
                    path = os.path.join(self.root, ".skill-contract", "runs", run_id, "wt", tid)
                    commit_in(path, "%s.txt" % tid.lower())
                    self.ok("verify", tid)  # on its DONE report
                elif action == "dispatch-reviewer":
                    self.assertRegex(parts[2], r"^[0-9a-f]{40}$")
                    self.ok("review", tid, "--verdict", "pass")
                elif action == "merge":
                    self.assertIn("MERGE: %s " % tid, self.ok("merge", tid))
                else:
                    self.fail("unknown NEXT: action %r" % line)
            if finished is not None:
                break
        self.assertTrue(finished, seen)
        self.assertEqual(seen[0], ["NEXT: T1 dispatch-executor",
                                   "NEXT: T2 dispatch-repair verify",
                                   "NEXT: T3 dispatch-reviewer %s"
                                   % seen[0][2].split()[-1],
                                   "NEXT: T4 merge", "NEXT: run next"])
        self.assertEqual(seen[-1], ["NEXT: run finish"])
        st = self.st()
        self.assertEqual({t["status"] for t in st.tasks.values()}, {"proven"})
        self.assertEqual(st.stopped["reason"], "no_ready_tasks")


if __name__ == "__main__":
    unittest.main()
