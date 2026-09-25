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
from unittest import mock

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

    def test_a_grant_ask_that_still_asks_says_ask(self):
        self.init()
        subprocess.run([sys.executable, "-I",
                        os.path.join(os.path.dirname(C.__file__), "contract_check.py"),
                        "revoke-grant", "--root", self.root], check=True, capture_output=True)
        rc, nxt, out = self.resume()
        self.assertEqual(rc, 3, out)
        self.assertIn("GATE: ASK", out)
        self.assertEqual(nxt, ["NEXT: run ask"])  # M2: the human decides, not finish
        # still asking on the next resume, from the recorded grant_ask stop
        rc, nxt, out = self.resume()
        self.assertEqual((rc, nxt), (3, ["NEXT: run ask"]))

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
                elif action == "verify":
                    self.ok("verify", tid)
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



@unittest.skipUnless(shutil.which("git"), "git not installed")
class ResumeReviewFixTests(unittest.TestCase):
    """Review of the Q3 commits: a missing worktree parks (I3), a finished run says what
    is left (I4), every dispatch line spends a dispatch (I5), a moved branch needs only
    verify (M1)."""

    init = ResumeNextTests.init
    run_main = ResumeNextTests.run_main
    st = ResumeNextTests.st
    ok = ResumeNextTests.ok
    resume = ResumeNextTests.resume

    def events(self, name):
        with open(self.st().log_path) as f:
            return [e for e in map(json.loads, f) if e["event"] == name]

    # I3
    def test_a_running_task_whose_worktree_is_gone_parks_instead_of_looping(self):
        self.init()
        self.ok("start", "T1")
        wt = self.st().tasks["T1"]["worktree"]
        shutil.rmtree(wt)
        subprocess.run(["git", "-C", self.root, "worktree", "prune"], check=True)
        rc, nxt, out = self.resume()
        self.assertIn("PARK: T1 worktree-missing", out.splitlines())
        self.assertNotIn("NEXT: T1", "\n".join(nxt))
        self.assertEqual((rc, nxt), (3, ["NEXT: run finish"]))  # nothing left to do
        t = self.st().tasks["T1"]
        self.assertEqual((t["status"], t["park_reason"]), ("parked", "worktree-missing"))
        self.assertEqual([e["reason"] for e in self.events("park")], ["worktree-missing"])

    def test_a_verifying_task_whose_worktree_is_gone_parks_and_the_run_goes_on(self):
        self.init(n=2)
        self.ok("start", "T1")
        wt = self.st().tasks["T1"]["worktree"]
        commit_in(wt, "other.txt")
        self.ok("verify", "T1")  # fails: verifying
        shutil.rmtree(wt)
        rc, nxt, out = self.resume()
        self.assertIn("PARK: T1 worktree-missing", out.splitlines())
        self.assertEqual((rc, nxt), (0, ["NEXT: run next"]))  # T2 is still ready

    # I4
    def finish_run(self, policy=None, breaks=False):
        """One task proven and merged, the run finished. breaks: a commit on the run
        branch after the merge breaks T1's own check, so integration goes red."""
        self.root = repo()
        self.plan = write_plan_envelope(self.root, {"T1": ([], [
            {"text": "no bad file", "command": ["test", "!", "-f", "bad.txt"]}])})
        write_grant(self.root, self.plan, policy=policy)
        origin = os.path.join(self.root + "-origin.git")
        self.addCleanup(shutil.rmtree, origin, ignore_errors=True)
        subprocess.run(["git", "init", "-q", "--bare", origin], check=True)
        subprocess.run(["git", "-C", self.root, "remote", "add", "origin", origin], check=True)
        self.assertEqual(self.run_main(["init", "--plan", self.plan, "--root", self.root])[0], 0)
        self.ok("start", "T1")
        commit_in(self.st().tasks["T1"]["worktree"], "t1.txt")
        self.ok("verify", "T1")
        self.ok("review", "T1", "--verdict", "pass")
        self.assertIn("MERGE: T1", self.ok("merge", "T1"))
        if breaks:
            commit_in(self.root, "bad.txt")
        self.ok("next")
        rc, out, err = self.run_main(["finish", "--root", self.root, "--pr-cmd",
                                      json.dumps([sys.executable, "-c", "pass"])])
        return rc, [l for l in out.splitlines() if l.startswith("FINISH: ")][0]

    def test_a_finished_run_whose_pending_push_is_asked_says_run_ask_then_finish(self):
        # W5: the pending step's own gate decides, so a no-memory walk ends at `run ask`
        rc, fin = self.finish_run(policy={"read_only": "auto", "local_reversible": "grant"})
        self.assertEqual(rc, 3)  # push_branch asks
        for _ in range(2):  # stable: it keeps saying ask, never a finish that cannot help
            rc, nxt, out = self.resume()
            self.assertEqual(rc, 0, out)
            self.assertIn(fin, out.splitlines())
            self.assertEqual(nxt, ["NEXT: run ask"])
        gates = [e for e in self.events("gate")]
        self.assertEqual((gates[-1]["action"], gates[-1]["ok"]), ("push_branch", False))
        write_grant(self.root, self.plan)  # the user grants the push and the PR
        rc, nxt, out = self.resume()
        self.assertEqual((rc, nxt), (0, ["NEXT: run finish"]))
        rc, out, err = self.run_main(["finish", "--root", self.root, "--retry-remote",
                                      "--pr-cmd", json.dumps([sys.executable, "-c", "pass"])])
        self.assertEqual(rc, 0, out + err)
        rc, nxt, out = self.resume()
        self.assertEqual((rc, nxt), (0, ["NEXT: run done"]))

    # N2: finish's grant_ask over an earlier stop, left by a crash before the envelope
    def crash_state(self, prior):
        self.init(n=2, parallel=2)
        st = self.st()
        st.stopped = {"reason": "grant_ask", "at": "t", "detail": "GATE: ASK reason=expired",
                      "previous": prior and prior["reason"], "prior": prior}
        st.save()

    def test_a_crashed_finish_stop_resumes_as_its_sticky_prior_under_a_renewed_grant(self):
        prior = {"reason": "new_human_decision", "at": "t", "detail": "merge-inconsistent",
                 "task": "T1"}
        self.crash_state(prior)
        rc, nxt, out = self.resume()
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: new_human_decision", out.splitlines())
        self.assertEqual(nxt, ["NEXT: run finish"])
        self.assertEqual(self.st().stopped, prior)  # never lifted, never overwritten
        rc, out, _ = self.run_main(["next", "--root", self.root])
        self.assertEqual(rc, 3)

    def test_a_crashed_finish_stop_keeps_its_prior_while_the_grant_still_asks(self):
        prior = {"reason": "new_human_decision", "at": "t", "detail": "merge-inconsistent",
                 "task": "T1"}
        self.crash_state(prior)
        write_grant(self.root, self.plan, minutes=-5)
        rc, nxt, out = self.resume()
        self.assertEqual((rc, nxt), (3, ["NEXT: run finish"]))
        self.assertEqual(self.st().stopped, prior)
        write_grant(self.root, self.plan)
        rc, nxt, out = self.resume()
        self.assertEqual((rc, nxt), (3, ["NEXT: run finish"]))
        self.assertEqual(self.st().stopped["reason"], "new_human_decision")

    def test_a_crashed_finish_stop_with_no_prior_resumes_the_run(self):
        self.crash_state(None)
        rc, nxt, out = self.resume()
        self.assertEqual((rc, nxt), (0, ["NEXT: run next"]))
        self.assertIsNone(self.st().stopped)

    def test_the_real_crash_window_in_finish_never_lifts_a_sticky_stop(self):
        self.init(n=2, parallel=2)
        self.ok("start", "T1")
        with contextlib.redirect_stdout(io.StringIO()):
            C._park_and_stop(self.st(), "T1", "merge-inconsistent")
        write_grant(self.root, self.plan, minutes=-5)

        class Killed(Exception):
            pass

        with mock.patch.object(C.CC, "write_envelope", side_effect=Killed()):
            with self.assertRaises(Killed):
                self.run_main(["finish", "--root", self.root])
        self.assertEqual(self.st().stopped["prior"]["reason"], "new_human_decision")
        self.assertFalse(self.st().finished)
        for grant in ("lapsed", "renewed"):
            if grant == "renewed":
                write_grant(self.root, self.plan)
            rc, nxt, out = self.resume()
            self.assertEqual((rc, nxt), (3, ["NEXT: run finish"]), grant)
            self.assertEqual(self.st().stopped["reason"], "new_human_decision")
        self.assertEqual(self.run_main(["next", "--root", self.root])[0], 3)

    def test_a_finished_run_with_every_step_done_says_run_done(self):
        rc, fin = self.finish_run()
        self.assertEqual(rc, 0)
        rc, nxt, out = self.resume()
        self.assertEqual(rc, 0, out)
        self.assertIn(fin, out.splitlines())
        self.assertEqual(nxt, ["NEXT: run done"])

    def test_a_finished_run_whose_integration_was_red_says_run_done(self):
        rc, fin = self.finish_run(breaks=True)
        self.assertEqual(rc, 3)
        self.assertIs(self.st().finished["integration"]["passed"], False)
        rc, nxt, out = self.resume()
        self.assertEqual(rc, 0, out)
        self.assertIn(fin, out.splitlines())
        self.assertEqual(nxt, ["NEXT: run done"])

    # I5
    def test_every_dispatch_line_spends_a_dispatch_and_repeated_resumes_stop(self):
        self.init(n=2)
        st = self.st()
        st.budget["max_dispatches"] = 3
        st.save()
        self.ok("start", "T1")  # 1
        rc, nxt, out = self.resume()
        self.assertEqual(nxt, ["NEXT: T1 dispatch-executor", "NEXT: run next"])
        self.assertEqual(self.st().dispatches, 2)
        rc, nxt, out = self.resume()
        self.assertEqual(nxt, ["NEXT: T1 dispatch-executor", "NEXT: run next"])
        self.assertEqual(self.st().dispatches, 3)
        kinds = [e.get("kind") for e in self.events("dispatch")]
        self.assertEqual(kinds, ["executor", "executor", "executor"])
        rc, nxt, out = self.resume()  # past the cap: T1 parks, no NEXT: for it
        self.assertIn("PARK: T1 budget_dispatches", out.splitlines())
        self.assertIn("STOP: budget_dispatches", out.splitlines())  # T2 ready, none left
        self.assertEqual((rc, nxt), (3, ["NEXT: run finish"]))
        self.assertEqual(self.st().dispatches, 3)

    def test_repair_and_reviewer_lines_spend_their_kind(self):
        self.init(n=2, parallel=2)
        self.ok("start", "T1")
        self.ok("start", "T2")
        commit_in(self.st().tasks["T1"]["worktree"], "other.txt")
        self.ok("verify", "T1")  # fail: a repair dispatch
        commit_in(self.st().tasks["T2"]["worktree"], "t2.txt")
        self.ok("verify", "T2")  # pass: a reviewer dispatch
        before = self.st().dispatches
        self.resume()
        self.assertEqual(self.st().dispatches, before + 2)
        self.assertEqual([e["kind"] for e in self.events("dispatch")][-2:], ["repair", "reviewer"])

    # M1
    def test_a_branch_that_moved_after_review_says_verify_and_spends_nothing(self):
        self.init()
        self.ok("start", "T1")
        wt = self.st().tasks["T1"]["worktree"]
        commit_in(wt, "t1.txt")
        self.ok("verify", "T1")
        self.ok("review", "T1", "--verdict", "pass")
        commit_in(wt, "late.txt")  # the branch moves: merge sends it back to verify
        rc, _, err = self.run_main(["merge", "T1", "--root", self.root])
        self.assertEqual(rc, 2, err)
        before = self.st().dispatches
        rc, nxt, out = self.resume()
        self.assertEqual((rc, nxt), (0, ["NEXT: T1 verify", "NEXT: run next"]))
        self.assertEqual(self.st().dispatches, before)


if __name__ == "__main__":
    unittest.main()
