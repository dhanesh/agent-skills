"""test_conductor_reentry_e2e.py — an abandoned session's run is resumed to FINISH by
the timer's own `conductor watch`.

The session starts T1, installs the timer and is abandoned. While its lease is live, a
timer tick prints `live` or `not-stalled` and starts nothing. Time passes (the lease,
the log and the worktree are aged past stall_min). The timer then fires: the test runs
the exact command line the rendered crontab entry holds, through /bin/sh as cron does,
under the environment cron gives a job (env -i, PATH=/usr/bin:/bin, HOME), so the only
PATH the run sees is the one the timer captured at `reentry install`. The plan's
verify program lives only on that captured PATH, as a user's tools do.

watch starts the grant's agent_cmd (an absolute path): a stdlib "walker" that, like a
memoryless agent, follows only resume's NEXT: lines (a stub executor commits one file,
a stub reviewer passes it). The run ends at FINISH: the envelope is written and T1 is
proven. The grant covers no push_branch or open_pr, so resume then says
`NEXT: run ask` for the remote steps (or `run done`), which is where the resume prompt
tells the agent to stop. A second tick on the finished run prints `REENTRY: done` and
removes the timer. Every timer call stays in a temp home, dry-run, as cron.
"""
import contextlib
import io
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import unittest
import warnings
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C  # noqa: E402
import reentry as R  # noqa: E402
import reentry_timer as T  # noqa: E402
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


_TIMER_ENV = ("FACTORY_CONDUCTOR_TIMER_HOME", "FACTORY_CONDUCTOR_TIMER_DRYRUN",
              "FACTORY_CONDUCTOR_TIMER_KIND")
_saved = {}


def setUpModule():
    _saved["env"] = {k: os.environ.get(k) for k in _TIMER_ENV}
    _saved["filters"] = list(warnings.filters)
    # R.spawn drops the agent's Popen on purpose (see its docstring): the
    # "subprocess N is still running" ResourceWarning is expected
    warnings.filterwarnings("ignore", r"subprocess \d+ is still running", ResourceWarning)
    # install and watch touch timers: keep every timer call in a temp home, dry-run, cron
    os.environ["FACTORY_CONDUCTOR_TIMER_HOME"] = tmpdir()
    os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN"] = "1"
    os.environ["FACTORY_CONDUCTOR_TIMER_KIND"] = "cron"


def tearDownModule():
    for k, v in _saved["env"].items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    warnings.filters[:] = _saved["filters"]


TOOL = "fc-e2e-check"  # the plan's verify program: found only on the captured PATH


def timer_tick(run_id, home):
    """Fire the installed cron entry once, as cron does: the line's command (cron's own
    \\% unescaping applied) through /bin/sh -c, in a minimal environment. Returns
    (rc, stdout)."""
    line, = T.installed(run_id, kind="cron")
    cmd = line.split(" ", 5)[5].rsplit(" " + T.TAG % run_id, 1)[0].replace("\\%", "%")
    env = {"PATH": "/usr/bin:/bin", "HOME": home, "SHELL": "/bin/sh",
           **{k: os.environ[k] for k in _TIMER_ENV}}
    r = subprocess.run(["/bin/sh", "-c", cmd], env=env, capture_output=True, text=True,
                       timeout=120)
    return r.returncode, r.stdout.strip()


class ReentryEndToEnd(unittest.TestCase):
    def test_an_abandoned_session_is_resumed_to_finish_by_the_timer(self):
        root = repo()
        tools = tmpdir()
        write_text(os.path.join(tools, TOOL), "#!/bin/sh\nexit 0\n")
        os.chmod(os.path.join(tools, TOOL), 0o755)
        # the control: the timer's own PATH cannot find the tool, so reaching FINISH
        # below proves the captured PATH reached the verify step
        self.assertIsNone(shutil.which(TOOL, path="/usr/bin:/bin"))
        home = tmpdir()  # the timer's HOME: git finds the user's identity there
        write_text(os.path.join(home, ".gitconfig"), "[user]\n\tname = t\n\temail = t@t\n")
        plan = {"title": "T", "spec": "s.md", "coverage": {}, "uncovered": [],
                "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "a",
                           "verify": [{"text": "t", "command": [TOOL]}], "depends_on": []}]}
        st, plan_env = new_run(root, plan, policy=LOCAL_ONLY)
        walker = os.path.join(tmpdir(), "walker.py")
        write_text(walker, WALKER)
        agent = os.path.realpath(sys.executable)
        self.assertTrue(os.path.isabs(agent))  # agent_cmd[0] absolute, as the skill says
        write_grant(root, plan_env, policy=LOCAL_ONLY, reentry={
            "agent_cmd": [agent, walker, HERE, "{root}", "{prompt}"],
            "max_reentries": 2})
        # the "session" starts T1 and installs the timer (as the skill says) with the
        # user's PATH, then is abandoned: nothing more happens
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.dict(os.environ, {"PATH": tools + os.pathsep + os.environ["PATH"]}):
            self.assertEqual(C.main(["start", "T1", "--root", root]), 0)
            self.assertEqual(C.main(["reentry", "install", "--root", root]), 0)
        self.assertNotEqual(T.installed(st.run_id), [])

        # never driven while a lease is live: a tick now starts nothing
        rc, out = timer_tick(st.run_id, home)
        self.assertEqual(rc, 0, out)
        self.assertIn(out, ("REENTRY: live", "REENTRY: not-stalled"))
        self.assertEqual([e for e in C.State.load(st.state_path).events()
                          if e.get("event") == "reentry"], [])
        self.assertFalse(os.path.exists(os.path.join(st.dir, "reentry-1.log")))

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

        rc, out = timer_tick(st.run_id, home)
        self.assertEqual(rc, 0, out)
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
        self.assertEqual(timer_tick(st.run_id, home), (0, "REENTRY: done"))
        self.assertEqual(T.installed(st.run_id), [])
        attempts = {e.get("n") for e in final.events() if e.get("event") == "reentry"}
        self.assertEqual(attempts, {1})

if __name__ == "__main__":
    unittest.main()
