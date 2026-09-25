import contextlib, io, json, os, signal, socket, subprocess, sys, tempfile, time, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reentry as R
import conductor as C
from conductor_testkit import GIT, new_run, repo, tmpdir

HERE = os.path.dirname(os.path.abspath(__file__))
HOLDER = ("import sys,time;sys.path.insert(0,%r);import reentry as R\n"
          "with R.run_lock(%r):\n print('held',flush=True);time.sleep(%r)")
TRY_LOCK = ("import sys;sys.path.insert(0,%r);import reentry as R\n"
            "try:\n with R.run_lock(%r,timeout=0.5):print('got')\n"
            "except R.RunLocked:print('locked')")


def hold(d, seconds):
    """A subprocess holding the run lock on d for `seconds`; returns once it holds it."""
    p = subprocess.Popen([sys.executable, "-c", HOLDER % (HERE, d, seconds)],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p


def other_process_gets_lock(d):
    return subprocess.run([sys.executable, "-c", TRY_LOCK % (HERE, d)], capture_output=True,
                          text=True).stdout.strip() == "got"


def utc(minutes_ago=0):
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


class LockTests(unittest.TestCase):
    def test_a_second_holder_times_out(self):
        d = tmpdir()
        code = ("import sys,time;sys.path.insert(0,%r);import reentry as R\n"
                "with R.run_lock(%r):\n print('held',flush=True);time.sleep(5)"
                % (os.path.dirname(os.path.abspath(R.__file__)), d))
        p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(), "held")
            with self.assertRaises(R.RunLocked):
                with R.run_lock(d, timeout=0.5):
                    pass
        finally:
            p.kill(); p.wait()
        with R.run_lock(d, timeout=0.5):
            pass

    def test_the_default_wait_outlasts_a_long_verify(self):
        # verify and finish hold the lock while their commands run (up to 600 s); a
        # shorter wait would make a second command exit 2 and wrongly park a task.
        self.assertGreaterEqual(R.LOCK_TIMEOUT, 900)

    def test_timeout_none_reads_the_module_default_at_call_time(self):
        d = tmpdir()
        code = ("import sys,time;sys.path.insert(0,%r);import reentry as R\n"
                "with R.run_lock(%r):\n print('held',flush=True);time.sleep(5)"
                % (os.path.dirname(os.path.abspath(R.__file__)), d))
        p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        old = R.LOCK_TIMEOUT
        R.LOCK_TIMEOUT = 0.3
        try:
            self.assertEqual(p.stdout.readline().strip(), "held")
            t0 = time.monotonic()
            with self.assertRaises(R.RunLocked):
                with R.run_lock(d):
                    pass
            self.assertLess(time.monotonic() - t0, 3)
        finally:
            R.LOCK_TIMEOUT = old
            p.kill(); p.wait()

    def test_a_waiting_holder_gets_the_lock_once_the_first_releases_it(self):
        d = tmpdir()
        p = hold(d, 1.5)
        try:
            t0 = time.monotonic()
            with contextlib.redirect_stderr(io.StringIO()):
                with R.run_lock(d, timeout=10):
                    waited = time.monotonic() - t0
        finally:
            p.kill(); p.wait()
        self.assertGreater(waited, 0.5)
        self.assertLess(waited, 8)

    def test_an_exception_inside_the_lock_releases_it(self):
        d = tmpdir()
        with self.assertRaises(RuntimeError):
            with R.run_lock(d, timeout=0.5):
                self.assertFalse(other_process_gets_lock(d))
                raise RuntimeError("boom")
        self.assertTrue(other_process_gets_lock(d))

    def test_a_sigkilled_holder_releases_the_lock(self):
        d = tmpdir()
        p = hold(d, 60)
        self.assertFalse(other_process_gets_lock(d))
        os.kill(p.pid, signal.SIGKILL); p.wait()
        self.assertTrue(other_process_gets_lock(d))

    def test_a_long_wait_says_so_once_on_stderr(self):
        d = tmpdir()
        p = hold(d, 3)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                with R.run_lock(d, timeout=10):
                    pass
        finally:
            p.kill(); p.wait()
        self.assertEqual(err.getvalue().count(
            "waiting for the run lock held by another conductor command\u2026"), 1)
        self.assertEqual(len(err.getvalue().splitlines()), 1)

    def test_a_short_wait_is_silent(self):
        d = tmpdir()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with R.run_lock(d, timeout=1):
                pass
        self.assertEqual(err.getvalue(), "")


class PidTests(unittest.TestCase):
    def test_non_pids_are_dead(self):
        for pid in (0, -1, None, "abc", "12a", " 12", 1.5, True, False, 2 ** 40, 2 ** 70,
                    str(2 ** 40)):
            self.assertFalse(R.pid_alive(pid), repr(pid))

    def test_live_pids_as_int_or_digit_string(self):
        self.assertTrue(R.pid_alive(os.getpid()))
        self.assertTrue(R.pid_alive(str(os.getpid())))

    def test_a_dead_child_is_dead_and_its_exit_status_survives(self):
        p = subprocess.Popen([sys.executable, "-c", "import sys;sys.exit(7)"])
        deadline = time.monotonic() + 10
        while R.pid_alive(p.pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(R.pid_alive(p.pid))
        self.assertEqual(p.wait(), 7)


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.d = tmpdir()

    def test_renew_writes_a_session_lease(self):
        R.renew_lease(self.d)
        lease = R.read_lease(self.d)
        self.assertEqual((lease["holder"], lease["host"]), ("session", socket.gethostname()))

    def test_a_fresh_session_lease_is_live_and_an_old_one_is_not(self):
        R.renew_lease(self.d, now=utc(5))
        self.assertTrue(R.lease_live(self.d, R.read_lease(self.d), 30, []))
        R.renew_lease(self.d, now=utc(45))
        self.assertFalse(R.lease_live(self.d, R.read_lease(self.d), 30, []))

    def test_worktree_activity_keeps_an_old_session_lease_live(self):
        R.renew_lease(self.d, now=utc(45))
        wt = tmpdir()
        with open(os.path.join(wt, "f.py"), "w") as f:
            f.write("x")
        self.assertTrue(R.lease_live(self.d, R.read_lease(self.d), 30, [wt]))
        old = time.time() - 45 * 60
        os.utime(os.path.join(wt, "f.py"), (old, old)); os.utime(wt, (old, old))
        self.assertFalse(R.lease_live(self.d, R.read_lease(self.d), 30, [wt]))

    def test_a_reentry_lease_is_live_exactly_while_its_pid_lives(self):
        p = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
        try:
            R.set_reentry_lease(self.d, 1, p.pid, now=utc(90))
            self.assertTrue(R.lease_live(self.d, R.read_lease(self.d), 30, []))
        finally:
            p.kill(); p.wait()
        self.assertFalse(R.lease_live(self.d, R.read_lease(self.d), 30, []))

    def test_a_child_command_keeps_the_reentry_holder(self):
        R.set_reentry_lease(self.d, 2, os.getpid())
        os.environ["FACTORY_CONDUCTOR_REENTRY"] = "2"
        try:
            R.renew_lease(self.d)
        finally:
            del os.environ["FACTORY_CONDUCTOR_REENTRY"]
        lease = R.read_lease(self.d)
        self.assertEqual((lease["holder"], lease["n"], lease["pid"]), ("reentry", 2, os.getpid()))

    def test_a_corrupt_lease_is_live_until_its_mtime_is_old(self):
        path = os.path.join(self.d, R.LEASE_FILE)
        with open(path, "w") as f:
            f.write("{not json")
        self.assertTrue(R.lease_live(self.d, R.read_lease(self.d), 30, []))
        old = time.time() - 45 * 60
        os.utime(path, (old, old))
        self.assertFalse(R.lease_live(self.d, R.read_lease(self.d), 30, []))

    def test_a_future_renewal_is_live(self):
        R.renew_lease(self.d, now=utc(-10))
        self.assertTrue(R.lease_live(self.d, R.read_lease(self.d), 30, []))


    def test_a_session_command_never_demotes_a_live_reentry_agent(self):
        p = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
        try:
            R.set_reentry_lease(self.d, 3, p.pid)
            os.environ.pop(R.ENV_REENTRY, None)
            R.renew_lease(self.d)
            lease = R.read_lease(self.d)
            self.assertEqual((lease["holder"], lease["n"], lease["pid"]), ("reentry", 3, p.pid))
            self.assertTrue(R.lease_live(self.d, lease, 30, [], now=utc(-45)))
        finally:
            p.kill(); p.wait()

    def test_a_session_command_takes_over_a_dead_reentry_lease(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"]); p.wait()
        R.set_reentry_lease(self.d, 4, p.pid)
        R.renew_lease(self.d)
        lease = R.read_lease(self.d)
        self.assertEqual((lease["holder"], lease["n"]), ("session", 4))

    def test_a_session_command_takes_over_another_hosts_reentry_lease(self):
        with open(os.path.join(self.d, R.LEASE_FILE), "w") as f:
            json.dump({"holder": "reentry", "host": "elsewhere.invalid", "pid": os.getpid(),
                       "renewed_at": "2026-01-01T00:00:00Z", "n": 5}, f)
        R.renew_lease(self.d)
        self.assertEqual(R.read_lease(self.d)["holder"], "session")

    def _write_lease(self, lease, age_min=0):
        path = os.path.join(self.d, R.LEASE_FILE)
        with open(path, "w") as f:
            json.dump(lease, f)
        t = time.time() - age_min * 60
        os.utime(path, (t, t))

    def test_a_malformed_renewal_follows_the_corrupt_lease_rule(self):
        for lease in ({}, {"holder": "session"}, {"holder": "session", "renewed_at": "x"},
                      {"holder": "session", "renewed_at": 123},
                      {"holder": "session", "renewed_at": None},
                      {"holder": "session", "renewed_at": "2026-09-25T10:00:00+00:00"}):
            self._write_lease(lease)
            self.assertTrue(R.lease_live(self.d, R.read_lease(self.d), 30, []), lease)
            self._write_lease(lease, age_min=45)
            self.assertFalse(R.lease_live(self.d, R.read_lease(self.d), 30, []), lease)

    def test_a_renewal_far_in_the_future_follows_the_corrupt_lease_rule(self):
        far = (utc(-24 * 60 * 365)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._write_lease({"holder": "session", "renewed_at": far})
        self.assertTrue(R.lease_live(self.d, R.read_lease(self.d), 30, []))
        self._write_lease({"holder": "session", "renewed_at": far}, age_min=45)
        self.assertFalse(R.lease_live(self.d, R.read_lease(self.d), 30, []))

    def test_a_relative_gitdir_worktree_is_live_on_a_fresh_index_alone(self):
        env = dict(os.environ, **GIT)
        main = os.path.realpath(tmpdir())  # git writes real paths; /var is /private/var on macOS
        git = lambda *a, cwd=main: subprocess.run(["git", *a], cwd=cwd, env=env, check=True,
                                                  capture_output=True, text=True)
        git("init", "-q", "-b", "main")
        with open(os.path.join(main, "a"), "w") as f:
            f.write("a")
        git("add", "a"); git("commit", "-qm", "a")
        wt = os.path.join(main, "wt")
        git("worktree", "add", "-q", "-b", "w", wt)
        with open(os.path.join(wt, ".git")) as f:
            gitdir = f.read().split("gitdir:", 1)[1].strip()
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: %s\n" % os.path.relpath(gitdir, wt))
        old = time.time() - 45 * 60
        for base, dirs, files in os.walk(wt):
            for n in files + dirs:
                os.utime(os.path.join(base, n), (old, old), follow_symlinks=False)
            os.utime(base, (old, old))
        for n in ("HEAD", os.path.join("logs", "HEAD"), "index"):
            if os.path.exists(os.path.join(gitdir, n)):
                os.utime(os.path.join(gitdir, n), (old, old))
        R.renew_lease(self.d, now=utc(45))
        self.assertFalse(R.lease_live(self.d, R.read_lease(self.d), 30, [wt]))
        os.utime(os.path.join(gitdir, "index"))  # a fresh `git add`
        self.assertTrue(R.lease_live(self.d, R.read_lease(self.d), 30, [wt]))
        os.utime(os.path.join(gitdir, "index"), (old, old))
        os.utime(os.path.join(gitdir, "logs", "HEAD"))  # a fresh commit
        self.assertTrue(R.lease_live(self.d, R.read_lease(self.d), 30, [wt]))

    def test_worktree_scan_stops_at_the_first_fresh_mtime(self):
        wt = tmpdir()
        for i in range(50):
            with open(os.path.join(wt, "f%d" % i), "w") as f:
                f.write("x")
        R.renew_lease(self.d, now=utc(45))
        real = os.stat
        calls = []
        with mock.patch.object(R.os, "stat", side_effect=lambda *a, **k: (
                calls.append(a[0]), real(*a, **k))[1]):
            self.assertTrue(R.lease_live(self.d, R.read_lease(self.d), 30, [wt]))
        self.assertLessEqual(len(calls), 2)


PLAN = {"title": "T", "spec": "s.md", "coverage": {}, "uncovered": [],
        "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "a",
                   "verify": [{"text": "t", "command": ["true"]}],
                   "depends_on": []}]}


class WiringTests(unittest.TestCase):
    def test_every_run_command_renews_the_session_lease(self):
        root = repo()
        st, _ = new_run(root, PLAN)
        self.assertIsNone(R.read_lease(st.dir))
        C.main(["status", "--root", root])
        self.assertEqual(R.read_lease(st.dir)["holder"], "session")

    def test_a_held_lock_makes_a_command_exit_2(self):
        root = repo()
        st, _ = new_run(root, PLAN)
        old = R.LOCK_TIMEOUT
        R.LOCK_TIMEOUT = 0.3
        try:
            with R.run_lock(st.dir):
                code = ("import sys;sys.path.insert(0,%r);import reentry as R;R.LOCK_TIMEOUT=0.3;"
                        "import conductor as C;sys.exit(C.main(['status','--root',%r]))"
                        % (os.path.dirname(os.path.abspath(C.__file__)), root))
                r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        finally:
            R.LOCK_TIMEOUT = old
        self.assertEqual(r.returncode, 2)
        self.assertIn("run locked", r.stderr)


    def test_a_human_status_keeps_a_live_reentry_agent_driving(self):
        root = repo()
        st, _ = new_run(root, PLAN)
        p = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
        try:
            R.set_reentry_lease(st.dir, 3, p.pid)
            os.environ.pop(R.ENV_REENTRY, None)
            with contextlib.redirect_stdout(io.StringIO()):
                C.main(["status", "--root", root])
            self.assertEqual(R.read_lease(st.dir)["holder"], "reentry")
        finally:
            p.kill(); p.wait()

    def test_an_exception_in_a_command_releases_the_lock(self):
        root = repo()
        st, _ = new_run(root, PLAN)
        with mock.patch.object(C, "cmd_status", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                C.main(["status", "--root", root])
        self.assertTrue(other_process_gets_lock(st.dir))

    def test_a_root_with_no_run_returns_2_and_makes_no_lock(self):
        root = tmpdir()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(C.main(["status", "--root", root]), 2)
        found = [os.path.join(b, n) for b, _, fs in os.walk(root) for n in fs
                 if n == R.LOCK_FILE]
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
