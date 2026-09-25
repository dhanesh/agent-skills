"""test_conductor_reentry_e2e.py — a killed session's run is resumed to FINISH by watch.

The session starts T1 and dies. Time passes (the lease, the log and the worktree are
aged past stall_min). `conductor watch` then starts the grant's agent_cmd: here a stdlib
"walker" that, like a memoryless agent, follows only resume's NEXT: lines (a stub
executor commits one file, a stub reviewer passes it). The run ends at FINISH: the
envelope is written and T1 is proven. The grant covers no push_branch or open_pr, so
resume then says `NEXT: run ask` for the remote steps (or `run done`), which is where the
resume prompt tells the agent to stop. A second watch on the finished run prints
`REENTRY: done`. Every timer call stays in a temp home, dry-run, as cron.
"""
import contextlib
import io
import json
import os
import signal
import sys
import time
import unittest
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C  # noqa: E402
import reentry as R  # noqa: E402
from conductor_testkit import new_run, read_text, repo, tmpdir, write_grant, write_text  # noqa: E402
from test_conductor_reentry import utc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# The grant covers local work only: no push_branch or open_pr, so finish writes the
# envelope and the remote steps ask (the push allowlist refuses any stub push command).
LOCAL_ONLY = {"read_only": "auto", "local_reversible": "grant"}

WALKER = r'''
import os, subprocess, sys
here, root = sys.argv[1], sys.argv[2]
def c(*a):
    r = subprocess.run([sys.executable, os.path.join(here, "conductor.py"), *a, "--root", root],
                       capture_output=True, text=True)
    print("$ conductor %s -> %d\n%s%s" % (" ".join(a), r.returncode, r.stdout, r.stderr),
          flush=True)
    return r.stdout.splitlines()
for _ in range(20):
    nexts = [l.split()[1:] for l in c("resume") if l.startswith("NEXT:")]
    if ["run", "done"] in nexts or ["run", "ask"] in nexts:
        break
    for n in nexts:
        if n[0] == "run":
            c("next") if n[1] == "next" else c("finish")
        elif n[1] == "dispatch-executor":
            # the stub executor commits one file in the task's existing worktree
            d = os.path.join(root, ".skill-contract", "runs")
            run = sorted(x for x in os.listdir(d) if x.startswith("run-"))[-1]
            w = os.path.join(d, run, "wt", n[0])
            with open(os.path.join(w, n[0] + ".txt"), "w") as f:
                f.write("done\n")
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
            subprocess.run(["git", "-C", w, "add", "-A"], env=env)
            subprocess.run(["git", "-C", w, "commit", "-q", "-m", n[0]], env=env)
            c("verify", n[0])
        elif n[1] == "dispatch-reviewer":
            c("review", n[0], "--verdict", "pass", "--detail", "stub")
        elif n[1] == "merge":
            c("merge", n[0])
        elif n[1] == "verify":
            c("verify", n[0])
'''


def setUpModule():
    # spawn detaches the agent on purpose; its Popen is dropped while it still runs
    warnings.filterwarnings("ignore", r"subprocess \d+ is still running", ResourceWarning)
    # watch uninstalls a finished run's timer: keep every timer call in a temp home
    os.environ["FACTORY_CONDUCTOR_TIMER_HOME"] = tmpdir()
    os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN"] = "1"
    os.environ["FACTORY_CONDUCTOR_TIMER_KIND"] = "cron"


def watch(root):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = C.main(["watch", "--root", root])
    return rc, buf.getvalue().strip()


class ReentryEndToEnd(unittest.TestCase):
    def test_a_killed_session_is_resumed_to_finish_by_watch(self):
        root = repo()
        plan = {"title": "T", "spec": "s.md", "coverage": {}, "uncovered": [],
                "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "a",
                           "verify": [{"text": "t", "command": ["true"]}], "depends_on": []}]}
        st, plan_env = new_run(root, plan, policy=LOCAL_ONLY)
        walker = os.path.join(tmpdir(), "walker.py")
        write_text(walker, WALKER)
        write_grant(root, plan_env, policy=LOCAL_ONLY, reentry={
            "agent_cmd": [sys.executable, walker, HERE, "{root}", "{prompt}"],
            "max_reentries": 2})
        # the "session" starts T1, then dies: nothing more happens
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(C.main(["start", "T1", "--root", root]), 0)
        # time passes: age the lease, the log and the worktree beyond stall_min
        R.renew_lease(st.dir, now=utc(45))
        with open(st.log_path, "a") as f:
            f.write(json.dumps({"at": utc(45).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "event": "aged"}) + "\n")
        old = time.time() - 45 * 60
        for base, dirs, files in os.walk(os.path.join(st.dir, "wt")):
            for n in files + dirs:
                os.utime(os.path.join(base, n), (old, old), follow_symlinks=False)
            os.utime(base, (old, old))
        wt_git = os.path.join(root, ".git", "worktrees")
        for base, dirs, files in os.walk(wt_git):
            for n in files + dirs:
                os.utime(os.path.join(base, n), (old, old), follow_symlinks=False)

        rc, out = watch(root)
        self.assertEqual(rc, 0)
        self.assertRegex(out, r"^REENTRY: started 1 pid=\d+$")
        pid = R.read_lease(st.dir)["pid"]
        log = os.path.join(st.dir, "reentry-1.log")
        try:
            for _ in range(600):
                if not R.pid_alive(pid):
                    break
                time.sleep(0.1)
            self.assertFalse(R.pid_alive(pid), "the walker never ended")
        finally:
            if R.pid_alive(pid):
                with contextlib.suppress(OSError):
                    os.killpg(pid, signal.SIGKILL)
        final = C.State.load(st.state_path)
        self.assertTrue(final.finished, read_text(log))
        self.assertEqual(final.tasks["T1"]["status"], "proven", read_text(log))
        # the agent stopped where the resume prompt says: the remote steps ask
        self.assertTrue(read_text(log).rstrip().endswith("NEXT: run ask"), read_text(log))
        # a later timer tick on the finished run: done (and the timer is removed)
        self.assertEqual(watch(root), (0, "REENTRY: done"))
        attempts = {e.get("n") for e in final.events() if e.get("event") == "reentry"}
        self.assertEqual(attempts, {1})


if __name__ == "__main__":
    unittest.main()
