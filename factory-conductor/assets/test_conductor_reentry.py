import json, os, socket, subprocess, sys, tempfile, time, unittest
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reentry as R
import conductor as C
from conductor_testkit import new_run, repo, tmpdir


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


if __name__ == "__main__":
    unittest.main()
