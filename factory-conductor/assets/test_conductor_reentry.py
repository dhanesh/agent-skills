import contextlib, io, json, os, signal, socket, subprocess, sys, tempfile, time, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reentry as R
import reentry_timer as T
import conductor as C
from conductor_testkit import GIT, new_run, repo, revoke, tmpdir, write_grant

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


def run_reentry(root, action):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = C.main(["reentry", action, "--root", root])
    return rc, buf.getvalue().strip()


class ReentryCommandTests(unittest.TestCase):
    """conductor reentry install|uninstall|status, kept to the cron kind (setUpModule)
    so nothing touches the real system."""

    def setUp(self):
        self.root = repo()
        self.st, self.plan = new_run(self.root, PLAN)

    def grant(self, **block):
        b = {"agent_cmd": [sys.executable, "-c", "pass", "{prompt}"],
             "interval_min": 10, "stall_min": 30, "max_reentries": 2}
        b.update(block)
        write_grant(self.root, self.plan, reentry=b)

    def test_install_without_a_block_is_disabled(self):
        write_grant(self.root, self.plan)  # newest grant, no reentry block
        self.assertEqual(run_reentry(self.root, "install"), (3, "REENTRY: disabled"))
        self.assertEqual(T.installed(self.st.run_id, kind="cron"), [])

    def test_install_with_a_block_installs_the_timer(self):
        self.grant()
        self.assertEqual(run_reentry(self.root, "install"),
                         (0, "REENTRY: installed every 10 min"))
        self.assertTrue(T.installed(self.st.run_id, kind="cron"))

    def test_status_prints_the_four_lines(self):
        self.grant()
        run_reentry(self.root, "install")
        rc, out = run_reentry(self.root, "status")
        lines = out.splitlines()
        self.assertEqual(rc, 0)
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[0].startswith("REENTRY: timer "))
        self.assertTrue(lines[1].startswith("REENTRY: lease "))
        self.assertEqual(lines[2], "REENTRY: count 0")
        self.assertEqual(lines[3], "REENTRY: last none")

    def test_status_count_is_distinct_n_values(self):
        self.grant()
        # two log lines for the same attempt (ok null, then ok true) count as one
        self.st.log("reentry", n=1, ok=None, argv=[])
        self.st.log("reentry", n=1, ok=True, pid=1)
        self.assertEqual(run_reentry(self.root, "status")[1].splitlines()[2],
                         "REENTRY: count 1")

    def test_status_last_is_the_newest_reentry_event_as_compact_json(self):
        self.grant()
        self.st.log("reentry", n=1, ok=None, argv=["x"])
        rec = self.st.log("reentry", n=1, ok=True, pid=123)
        out = run_reentry(self.root, "status")[1]
        last_line = out.splitlines()[3]
        self.assertEqual(last_line, "REENTRY: last " + json.dumps(rec, sort_keys=True,
                                                                   separators=(",", ":")))
        self.assertNotIn(" ", last_line[len("REENTRY: last "):])  # compact: no spaces

    def test_uninstall_leaves_installed_empty(self):
        self.grant()
        run_reentry(self.root, "install")
        self.assertEqual(run_reentry(self.root, "uninstall"), (0, "REENTRY: uninstalled"))
        self.assertEqual(T.installed(self.st.run_id, kind="cron"), [])

    def test_watch_uninstalls_the_timer_once_the_run_is_finished(self):
        self.grant()
        run_reentry(self.root, "install")
        self.assertTrue(T.installed(self.st.run_id, kind="cron"))
        st = C.State.load(self.st.state_path)
        st.finished = {"at": "t"}
        st.save()
        self.assertEqual(run_watch(self.root), (0, "REENTRY: done"))
        self.assertEqual(T.installed(self.st.run_id, kind="cron"), [])

    def test_install_under_an_ask_gate_gives_exit_3_with_no_stop_and_no_timer(self):
        b = {"agent_cmd": [sys.executable, "-c", "pass", "{prompt}"],
             "interval_min": 10, "stall_min": 30, "max_reentries": 2}
        write_grant(self.root, self.plan, reentry=b,
                   policy={"read_only": "auto", "local_reversible": "ask",
                           "push_branch": "grant", "open_pr": "grant"})
        rc, out = run_reentry(self.root, "install")
        self.assertEqual(rc, 3)
        self.assertTrue(out.startswith("GATE: ASK"), out)
        self.assertIsNone(C.State.load(self.st.state_path).stopped)
        self.assertEqual(T.installed(self.st.run_id, kind="cron"), [])

    def test_install_on_an_unsupported_platform_gives_exit_2(self):
        self.grant()
        with mock.patch.object(T, "platform_kind", return_value=None):
            rc, out = run_reentry(self.root, "install")
        self.assertEqual((rc, out), (2, "REENTRY: failed"))
        self.assertEqual([e for e in C.State.load(self.st.state_path).events()
                          if e.get("event") == "reentry_timer"], [])

    def test_install_with_a_failing_loader_leaves_nothing_and_logs_no_install(self):
        self.grant()
        old_kind = os.environ.get("FACTORY_CONDUCTOR_TIMER_KIND")
        os.environ["FACTORY_CONDUCTOR_TIMER_KIND"] = "systemd"
        os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL"] = "systemctl --user enable"
        try:
            rc, out = run_reentry(self.root, "install")
        finally:
            os.environ["FACTORY_CONDUCTOR_TIMER_KIND"] = old_kind
            os.environ.pop("FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL", None)
        self.assertEqual((rc, out), (2, "REENTRY: failed"))
        self.assertEqual(T.installed(self.st.run_id, kind="systemd"), [])
        self.assertEqual([e for e in C.State.load(self.st.state_path).events()
                          if e.get("event") == "reentry_timer"], [])

    # ── fix round 2, Minor 1: status/uninstall report failed, never a traceback ──

    def test_status_reports_failed_on_an_underlying_error(self):
        self.grant()
        run_reentry(self.root, "install")
        os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL"] = "crontab -l"
        try:
            rc, out = run_reentry(self.root, "status")
        finally:
            os.environ.pop("FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL", None)
        self.assertEqual((rc, out), (2, "REENTRY: failed"))

    def test_uninstall_reports_failed_on_an_underlying_error(self):
        self.grant()
        run_reentry(self.root, "install")
        os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL"] = "crontab -l"
        try:
            rc, out = run_reentry(self.root, "uninstall")
        finally:
            os.environ.pop("FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL", None)
        self.assertEqual((rc, out), (2, "REENTRY: failed"))

    # ── fix round 2, Important: status/uninstall work without a crontab binary ──

    def test_status_and_uninstall_work_without_a_crontab_binary(self):
        self.grant()
        old_kind = os.environ.get("FACTORY_CONDUCTOR_TIMER_KIND")
        os.environ["FACTORY_CONDUCTOR_TIMER_KIND"] = "systemd"
        try:
            rc, out = run_reentry(self.root, "install")  # still sandboxed here
        finally:
            os.environ["FACTORY_CONDUCTOR_TIMER_KIND"] = old_kind
        self.assertEqual(rc, 0, out)

        def fake_which(name):
            return None if name == "crontab" else "/usr/bin/" + name

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        os.environ.pop("FACTORY_CONDUCTOR_TIMER_DRYRUN", None)
        try:
            with mock.patch.object(T.shutil, "which", side_effect=fake_which), \
                    mock.patch.object(T.subprocess, "run", side_effect=fake_run):
                rc, out = run_reentry(self.root, "status")
                self.assertEqual(rc, 0, out)
                self.assertIn(T.UNIT % self.st.run_id, out.splitlines()[0])
                rc, out = run_reentry(self.root, "uninstall")
                self.assertEqual((rc, out), (0, "REENTRY: uninstalled"))
        finally:
            os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN"] = "1"
        self.assertEqual(T.installed(self.st.run_id, kind="systemd"), [])


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


class DecideTests(unittest.TestCase):
    B = {"agent_cmd": ["a", "{prompt}"], "stall_min": 30, "max_reentries": 5}

    def d(self, **kw):
        a = dict(finished=False, stopped_reason=None, covered=True, block=self.B,
                 live=False, idle_min=60, count=0)
        a.update(kw)
        return R.decide(**a)

    def test_each_branch_in_spec_order(self):
        self.assertEqual(self.d(finished=True), "done")
        self.assertEqual(self.d(stopped_reason="grant_ask"), "waiting-human")
        self.assertEqual(self.d(covered=False), "ask")
        self.assertEqual(self.d(block=None), "disabled")
        self.assertEqual(self.d(live=True), "live")
        self.assertEqual(self.d(idle_min=29), "not-stalled")
        self.assertEqual(self.d(count=5), "exhausted")
        self.assertEqual(self.d(), "start")

    def test_finished_wins_over_everything(self):
        self.assertEqual(self.d(finished=True, covered=False, live=True), "done")

    def test_an_exhausted_stop_stays_exhausted(self):
        self.assertEqual(self.d(stopped_reason="reentry_exhausted", idle_min=0), "exhausted")
        self.assertEqual(self.d(stopped_reason="reentry_exhausted", live=True), "exhausted")
        self.assertEqual(self.d(stopped_reason="reentry_exhausted", covered=False), "exhausted")

    def test_a_stopped_run_other_than_grant_ask_is_still_resumed(self):
        self.assertEqual(self.d(stopped_reason="no_ready_tasks"), "start")

    def test_missing_limits_fall_back_to_the_checker_defaults(self):
        block = {"agent_cmd": ["a", "{prompt}"]}
        stall = C.CC.REENTRY_DEFAULTS["stall_min"]
        most = C.CC.REENTRY_DEFAULTS["max_reentries"]
        self.assertEqual(self.d(block=block, idle_min=stall - 1), "not-stalled")
        self.assertEqual(self.d(block=block, idle_min=stall, count=most), "exhausted")
        self.assertEqual(self.d(block=block, idle_min=stall, count=most - 1), "start")


class PromptTests(unittest.TestCase):
    def test_the_prompt_and_root_are_substituted_whole(self):
        argv = R.expand_agent_cmd(["agent", "-p", "{prompt}", "--cwd", "{root}"], "/r o")
        self.assertEqual(argv[:2], ["agent", "-p"])
        self.assertIn("conductor resume --root '/r o'", argv[2])
        self.assertEqual(argv[3:], ["--cwd", "/r o"])

    def test_a_token_inside_a_longer_argument_is_left_alone(self):
        self.assertEqual(R.expand_agent_cmd(["a", "x{prompt}", "{root}/y"], "/r"),
                         ["a", "x{prompt}", "{root}/y"])


def setUpModule():
    # watch uninstalls a finished run's timer: keep every timer call in a temp home
    os.environ["FACTORY_CONDUCTOR_TIMER_HOME"] = tmpdir()
    os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN"] = "1"
    os.environ["FACTORY_CONDUCTOR_TIMER_KIND"] = "cron"


STUB = ("import os,sys,time;open(sys.argv[1],'a').write(os.environ.get("
        "'FACTORY_CONDUCTOR_REENTRY','?')+'\\n');time.sleep(float(sys.argv[2]))")


def run_watch(root):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = C.main(["watch", "--root", root])
    return rc, buf.getvalue().strip()


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.root = repo()
        self.st, self.plan = new_run(self.root, PLAN)
        self.marker = os.path.join(tmpdir(), "started")

    def tearDown(self):
        # stop every agent a watch started (they sleep), so none outlives the suite;
        # spawn gave each its own session, so its pid is its process group
        pids = {e.get("pid") for e in self.reentry_events()}
        pids.add((R.read_lease(self.st.dir) or {}).get("pid"))
        for pid in pids:
            if R.pid_alive(pid):
                with contextlib.suppress(OSError):
                    os.killpg(pid, signal.SIGKILL)

    def grant(self, sleep=30, **block):
        b = {"agent_cmd": [sys.executable, "-c", STUB, self.marker, str(sleep), "{prompt}"],
             "stall_min": 30, "max_reentries": 2}
        b.update(block)
        write_grant(self.root, self.plan, reentry=b)

    def age(self, minutes=45):
        old = time.time() - minutes * 60
        for name in (self.st.log_path, os.path.join(self.st.dir, R.LEASE_FILE)):
            if os.path.exists(name):
                os.utime(name, (old, old))
        R.renew_lease(self.st.dir, now=utc(minutes))
        # rewrite the last log line's timestamp too: watch reads the last event's `at`
        with open(self.st.log_path, "a") as f:
            f.write(json.dumps({"at": utc(minutes).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "event": "aged"}) + "\n")
        os.utime(self.st.log_path, (old, old))

    def reentry_events(self):
        return [e for e in C.State.load(self.st.state_path).events()
                if e.get("event") == "reentry"]

    def attempts(self):
        return len({e["n"] for e in self.reentry_events()})

    def marker_lines(self):
        if not os.path.exists(self.marker):
            return []
        with open(self.marker) as f:
            return f.read().split()

    def wait_started(self, lines, deadline=10):
        """Poll until the marker holds `lines`; returns what it holds at the end."""
        end = time.monotonic() + deadline
        while self.marker_lines() != lines and time.monotonic() < end:
            time.sleep(0.05)
        return self.marker_lines()

    def wait_dead(self, pid, deadline=10):
        end = time.monotonic() + deadline
        while R.pid_alive(pid) and time.monotonic() < end:
            time.sleep(0.05)
        self.assertFalse(R.pid_alive(pid), pid)

    def assert_nothing_started(self):
        self.assertEqual(self.reentry_events(), [])
        self.assertNotEqual((R.read_lease(self.st.dir) or {}).get("holder"), "reentry")
        self.assertEqual(self.marker_lines(), [])

    def test_no_run(self):
        self.assertEqual(run_watch(repo()), (0, "REENTRY: no-run"))

    def test_disabled_without_a_block(self):
        write_grant(self.root, self.plan)
        self.age()
        self.assertEqual(run_watch(self.root), (0, "REENTRY: disabled"))
        self.assert_nothing_started()

    def test_a_grant_with_a_shell_agent_cmd_starts_nothing(self):
        # the checker rejects the whole grant (Task 1), so it no longer covers the run
        self.grant(agent_cmd=["sh", "-c", "x", "{prompt}"])
        self.age()
        self.assertEqual(run_watch(self.root), (0, "REENTRY: ask"))
        self.assert_nothing_started()

    def test_a_grant_with_a_nul_byte_in_agent_cmd_starts_nothing(self):
        self.grant(agent_cmd=["a\x00b", "{prompt}"])
        self.age()
        self.assertEqual(run_watch(self.root), (0, "REENTRY: ask"))
        self.assert_nothing_started()

    def test_a_fresh_run_is_not_stalled(self):
        self.grant()  # no lease yet (new_run skips main), but init was logged just now
        self.assertEqual(run_watch(self.root), (0, "REENTRY: not-stalled"))
        self.assert_nothing_started()

    def test_watch_never_writes_the_lease(self):
        self.grant()
        run_watch(self.root)
        self.assertIsNone(R.read_lease(self.st.dir))

    def test_a_live_session_lease_blocks_re_entry(self):
        self.grant()
        self.age()
        R.renew_lease(self.st.dir)  # a session just ran a conductor command
        self.assertEqual(run_watch(self.root), (0, "REENTRY: live"))
        self.assertEqual(self.reentry_events(), [])
        self.assertEqual(R.read_lease(self.st.dir)["holder"], "session")

    def test_fresh_worktree_activity_under_an_old_session_lease_is_live(self):
        for status in ("running", "verifying", "reviewing"):
            with self.subTest(status=status):
                self.grant()
                wt = tmpdir()
                st = C.State.load(self.st.state_path)
                st.tasks["T1"].update(status=status, worktree=wt)
                st.save()
                self.age()  # old session lease, old log
                with open(os.path.join(wt, "f.py"), "w") as f:
                    f.write("x")  # the executor is working
                self.assertEqual(run_watch(self.root), (0, "REENTRY: live"))
                self.assertEqual(self.reentry_events(), [])

    def test_a_held_run_lock_is_live_within_seconds(self):
        self.grant()
        self.age()
        p = hold(self.st.dir, 30)
        try:
            t0 = time.monotonic()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run_watch(self.root), (0, "REENTRY: live"))
            self.assertLess(time.monotonic() - t0, 10)
        finally:
            p.kill(); p.wait()
        self.assert_nothing_started()

    def test_a_stalled_run_starts_the_agent_once(self):
        self.grant()
        self.age()
        rc, out = run_watch(self.root)
        self.assertEqual(rc, 0)
        self.assertRegex(out, r"^REENTRY: started 1 pid=\d+$")
        self.assertEqual(self.wait_started(["1"]), ["1"])
        self.assertEqual(run_watch(self.root), (0, "REENTRY: live"))  # its pid lives
        ev = self.reentry_events()
        # the attempt is logged before the spawn (ok null), its outcome after
        self.assertEqual([(e["n"], e["ok"]) for e in ev], [(1, None), (1, True)])
        self.assertNotIn(R.RESUME_PROMPT[:20], json.dumps(ev))  # prompt elided
        self.assertIn("{prompt}", ev[0]["argv"])
        lease = R.read_lease(self.st.dir)
        self.assertEqual((lease["holder"], lease["n"], lease["pid"]),
                         ("reentry", 1, ev[-1]["pid"]))
        self.assertTrue(os.path.exists(os.path.join(self.st.dir, "reentry-1.log")))

    def test_no_part_of_the_prompt_reaches_the_log(self):
        self.grant()
        self.age()
        run_watch(self.root)
        with open(self.st.log_path) as f:
            log = f.read()
        fragments = [p.strip() for p in R.RESUME_PROMPT.split("{root}")]
        self.assertEqual(len(fragments), 3)
        for frag in fragments:
            for piece in frag.split(". "):
                if len(piece) > 12:
                    self.assertNotIn(piece, log)

    def test_waiting_on_the_human_starts_nothing(self):
        self.grant()
        st = C.State.load(self.st.state_path)
        st.stopped = {"reason": "grant_ask", "at": "t", "detail": "x"}
        st.save()
        self.age()
        self.assertEqual(run_watch(self.root), (0, "REENTRY: waiting-human"))
        self.assert_nothing_started()

    def test_a_finished_run_is_done(self):
        self.grant()
        st = C.State.load(self.st.state_path)
        st.finished = {"at": "t"}
        st.save()
        self.age()
        self.assertEqual(run_watch(self.root), (0, "REENTRY: done"))
        self.assert_nothing_started()

    def test_a_revoked_grant_starts_nothing(self):
        self.grant()
        revoke(self.root)
        self.age()
        self.assertEqual(run_watch(self.root), (0, "REENTRY: ask"))
        self.assert_nothing_started()

    def test_exhaustion_records_a_stop(self):
        self.grant(sleep=0, max_reentries=1)
        self.age()
        self.assertEqual(run_watch(self.root)[1][:17], "REENTRY: started ")
        self.wait_dead(R.read_lease(self.st.dir)["pid"])
        self.age()
        self.assertEqual(run_watch(self.root), (0, "REENTRY: exhausted"))
        st = C.State.load(self.st.state_path)
        self.assertEqual(st.stopped["reason"], "reentry_exhausted")
        self.assertIsNone(st.stopped.get("previous"))
        self.assertIn("reentry_exhausted", C.STOP_REASONS)
        with open(os.path.join(HERE, "schemas", "run-result.v1.json")) as f:
            self.assertIn("reentry_exhausted", f.read())
        # watch's own stop event is fresh, but an exhausted run stays exhausted
        self.assertEqual(run_watch(self.root), (0, "REENTRY: exhausted"))
        stops = [e for e in st.events() if e.get("event") == "stop"]
        self.assertEqual(len(stops), 1)
        self.assertEqual(self.attempts(), 1)

    def test_exhaustion_keeps_an_earlier_stop_as_previous(self):
        self.grant(max_reentries=1)
        st = C.State.load(self.st.state_path)
        st.stopped = {"reason": "no_ready_tasks", "at": "t"}
        st.save()
        st.log("reentry", n=1, ok=False, argv=[], error="x")
        self.age()
        self.assertEqual(run_watch(self.root), (0, "REENTRY: exhausted"))
        stopped = C.State.load(self.st.state_path).stopped
        self.assertEqual((stopped["reason"], stopped["previous"]),
                         ("reentry_exhausted", "no_ready_tasks"))

    def test_a_missing_binary_fails_and_still_counts(self):
        self.grant(agent_cmd=["/nonexistent/agent", "{prompt}"], max_reentries=1)
        self.age()
        rc, out = run_watch(self.root)
        self.assertEqual((rc, out), (2, "REENTRY: failed"))
        self.assertEqual([(e["n"], e["ok"]) for e in self.reentry_events()],
                         [(1, None), (1, False)])
        self.age()
        self.assertEqual(run_watch(self.root)[1], "REENTRY: exhausted")

    def test_a_failed_lease_write_kills_the_agent_and_counts(self):
        # reviewer probe 4a: a lease write that fails after the spawn must not leave an
        # agent running unleased, or the next watch starts a second one
        self.grant(max_reentries=1)
        self.age()
        with mock.patch.object(R, "set_reentry_lease", side_effect=OSError("disk full")):
            self.assertEqual(run_watch(self.root), (2, "REENTRY: failed"))
        ev = self.reentry_events()
        self.assertEqual([(e["n"], e["ok"]) for e in ev], [(1, None), (1, False)])
        self.assertEqual(ev[-1]["error"], "lease write failed")
        self.wait_dead(ev[-1]["pid"])
        self.age()
        self.assertEqual(run_watch(self.root), (0, "REENTRY: exhausted"))
        self.assertEqual(self.attempts(), 1)

    def test_a_failed_attempt_log_starts_nothing(self):
        # reviewer probe 4b: an attempt that cannot be logged cannot be counted, so it
        # must not start an agent at all
        self.grant(max_reentries=1)
        self.age()
        real = C.State.log

        def failing(st, event, **kw):
            if event == "reentry":
                raise OSError("log EIO")
            return real(st, event, **kw)
        with mock.patch.object(C.State, "log", failing), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_watch(self.root), (2, "REENTRY: failed"))
        self.assert_nothing_started()
        self.assertEqual(run_watch(self.root)[1], "REENTRY: started 1 pid=%d"
                         % R.read_lease(self.st.dir)["pid"])
        self.assertEqual(self.wait_started(["1"]), ["1"])
        self.age()
        self.assertEqual(run_watch(self.root), (0, "REENTRY: live"))
        self.assertEqual(self.marker_lines(), ["1"])
        self.assertEqual(self.attempts(), 1)

    def test_a_failed_outcome_log_leaves_one_leased_agent(self):
        self.grant()
        self.age()
        real = C.State.log

        def failing(st, event, **kw):
            if event == "reentry" and kw.get("ok") is True:
                raise OSError("log EIO")
            return real(st, event, **kw)
        with mock.patch.object(C.State, "log", failing), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_watch(self.root), (2, "REENTRY: failed"))
        self.assertEqual(R.read_lease(self.st.dir)["holder"], "reentry")
        self.assertEqual(self.wait_started(["1"]), ["1"])
        self.age()
        self.assertEqual(run_watch(self.root), (0, "REENTRY: live"))
        self.assertEqual(self.attempts(), 1)

    def test_an_unexpected_error_is_one_failed_line(self):
        self.grant()
        self.age()
        with mock.patch.object(R, "decide", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_watch(self.root), (2, "REENTRY: failed"))
        self.assert_nothing_started()

    def test_overlapping_watches_start_one_agent(self):
        self.grant()
        self.age()
        here = os.path.dirname(os.path.abspath(C.__file__))
        code = ("import sys;sys.path.insert(0,%r);import conductor as C;"
                "sys.exit(C.main(['watch','--root',%r]))" % (here, self.root))
        ps = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
              for _ in range(3)]
        outs = [p.communicate()[0].strip() for p in ps]
        self.assertEqual(sum(o.startswith("REENTRY: started") for o in outs), 1, outs)
        self.assertEqual(self.wait_started(["1"]), ["1"])
        self.assertEqual(self.attempts(), 1)


class IdleTests(unittest.TestCase):
    def setUp(self):
        self.st, _ = new_run(repo(), PLAN)

    def append(self, line):
        with open(self.st.log_path, "a") as f:
            f.write(line + "\n")

    def test_a_future_stamp_is_judged_by_the_log_mtime(self):
        future = utc(-600).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.append(json.dumps({"at": future, "event": "x"}))
        self.assertGreaterEqual(C._idle_min(self.st), 0)
        self.assertLess(C._idle_min(self.st), 5)
        old = time.time() - 45 * 60
        os.utime(self.st.log_path, (old, old))
        self.assertGreater(C._idle_min(self.st), 44)

    def test_a_line_that_is_not_an_object_is_skipped(self):
        self.append(json.dumps({"at": utc(45).strftime("%Y-%m-%dT%H:%M:%SZ"), "event": "x"}))
        self.append("[1, 2]")
        self.append("7")
        self.assertEqual(self.st.last_event()["event"], "x")
        self.assertTrue(all(isinstance(e, dict) for e in self.st.events()))
        self.assertGreater(C._idle_min(self.st), 44)

if __name__ == "__main__":
    unittest.main()
