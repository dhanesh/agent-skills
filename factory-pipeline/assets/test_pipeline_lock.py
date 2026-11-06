#!/usr/bin/env python3
"""`pipeline tick` lock (R14): only one tick runs at a time in one repository. A tick that
starts while another process holds the pipeline lock exits 3, prints `locked`, runs no
grant check and no stage, and leaves the queue unchanged. The lock is released when its
holder exits, also when the holder is killed. A long first tick holds the lock through
its stages. Stdlib unittest; offline, deterministic; scratch only in temp dirs.
Run:  python3 test_pipeline_lock.py
"""
import hashlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline as P  # noqa: E402
import pipeline_testkit as K  # noqa: E402

TOOL = os.path.join(HERE, "pipeline.py")
LOCKED = "STOP: locked: another pipeline tick holds .skill-contract/pipeline/.lock"

KIT = os.path.join(HERE, "pipeline_testkit.py")  # `hold <repo> <seconds>` takes the lock


def wait_for(cond, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class LockFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pipeline-lock-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.skills = K.stubs(os.path.join(self.tmp, "skills"))
        K.pipeline_grant(self.repo)
        # A queue with one item, so "the queue is unchanged" is a real check.
        q = os.path.join(self.repo, ".skill-contract", "intake")
        os.makedirs(q)
        with open(os.path.join(q, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("*\n")
        with open(os.path.join(q, "queue.json"), "w", encoding="utf-8") as f:
            f.write('{"items": [{"id": 1, "state": "new", "title": "ci red"}]}\n')
        self.lock = os.path.join(self.repo, ".skill-contract", "pipeline", ".lock")

    def snapshot(self):
        """Every file under the repo except the lock file, with its sha256."""
        out = {}
        for d, _, files in os.walk(self.repo):
            for name in files:
                path = os.path.join(d, name)
                if path == self.lock:
                    continue
                with open(path, "rb") as f:
                    out[os.path.relpath(path, self.repo)] = hashlib.sha256(f.read()).hexdigest()
        return out

    def argv(self):
        return [sys.executable, TOOL, "--root", self.repo, "--skills-dir", self.skills, "tick"]

    def tick(self):
        return subprocess.run(self.argv(), capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL)

    def start(self, argv, **kw):
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             stdin=subprocess.DEVNULL, start_new_session=True, **kw)

        def stop():
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.communicate()
        self.addCleanup(stop)
        return p

    def hold(self, seconds=120):
        p = self.start([sys.executable, KIT, "hold", self.repo, str(seconds)])
        self.assertEqual(p.stdout.readline().strip(), "HELD: %s" % self.lock)
        return p

    def assertLocked(self, cp, before):
        self.assertEqual(cp.returncode, 3, cp.stdout + cp.stderr)
        self.assertIn(LOCKED, cp.stdout.splitlines())
        self.assertIn("locked", cp.stdout)
        self.assertNotIn("STAGE:", cp.stdout)
        self.assertNotIn("TICK: grant", cp.stdout)  # the grant check did not run either
        self.assertEqual(self.snapshot(), before)  # the queue and every other file

    def assertRan(self, cp):
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("TICK: done stages=10", cp.stdout)


class TestLockHeld(LockFixture):
    def test_tick_exits_3_locked_and_leaves_the_queue_unchanged(self):
        self.hold()
        before = self.snapshot()
        self.assertLocked(self.tick(), before)
        self.assertEqual(K.calls(self.skills), [])  # no sweep, no autopick: no tool ran

    def test_released_when_the_holder_exits(self):
        p = self.hold()
        self.assertLocked(self.tick(), self.snapshot())
        p.terminate()
        p.wait(timeout=30)
        self.assertRan(self.tick())

    def test_released_when_the_holder_is_killed(self):
        p = self.hold()
        self.assertLocked(self.tick(), self.snapshot())
        os.kill(p.pid, signal.SIGKILL)
        p.wait(timeout=30)
        self.assertRan(self.tick())

    def test_a_holder_that_already_ended_never_blocks(self):
        p = self.hold(seconds=0)
        p.wait(timeout=30)
        self.assertTrue(os.path.exists(self.lock))  # the file stays; the flock does not
        self.assertRan(self.tick())


class TestLongTick(LockFixture):
    """A first tick that is still inside a stage holds the lock for its whole life."""

    def setUp(self):
        super().setUp()
        K.behave(self.skills, "ops-intake", "sweep", stdout="SWEEP: ok\n", sleep=3)

    def in_sweep(self):
        return any(c["key"] == "sweep" for c in K.calls(self.skills))

    def test_second_tick_is_locked_while_the_first_runs_a_stage(self):
        first = self.start(self.argv())
        self.assertTrue(wait_for(self.in_sweep), "the first tick never reached sweep")
        before = self.snapshot()
        n = len(K.calls(self.skills))
        self.assertLocked(self.tick(), before)
        self.assertEqual(len(K.calls(self.skills)), n)  # the second tick ran no tool
        out, err = first.communicate(timeout=120)
        self.assertEqual(first.returncode, 0, out + err)
        self.assertEqual([c["key"] for c in K.calls(self.skills)].count("sweep"), 1)
        K.behave(self.skills, "ops-intake", "sweep", stdout="SWEEP: ok\n")
        self.assertRan(self.tick())

    def test_killed_tick_releases_the_lock_even_while_its_tool_still_runs(self):
        first = self.start(self.argv())
        self.assertTrue(wait_for(self.in_sweep), "the first tick never reached sweep")
        os.kill(first.pid, signal.SIGKILL)  # the stub sweep child lives on, sleeping
        first.wait(timeout=30)
        K.behave(self.skills, "ops-intake", "sweep", stdout="SWEEP: ok\n")
        self.assertRan(self.tick())  # the orphan tool did not inherit the lock


class TestStateDir(LockFixture):
    def test_lock_lives_in_a_git_ignored_dir(self):
        self.assertRan(self.tick())
        self.assertTrue(os.path.isfile(self.lock))
        gi = os.path.join(os.path.dirname(self.lock), ".gitignore")
        with open(gi, encoding="utf-8") as f:
            self.assertIn("*", [ln.strip() for ln in f.read().splitlines()])
        if shutil.which("git"):
            subprocess.run(["git", "init", "-q", self.repo], check=True, capture_output=True)
            cp = subprocess.run(["git", "-C", self.repo, "check-ignore", "-q", self.lock])
            self.assertEqual(cp.returncode, 0)

    def test_not_ops_intakes_lock(self):
        self.assertNotEqual(P.PIPELINE_DIR, ".skill-contract/intake")

    def refused(self, cp):
        self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
        self.assertIn("STOP: state-dir reason=", cp.stdout)
        self.assertNotIn("STAGE:", cp.stdout)
        self.assertFalse(os.path.lexists(self.lock))
        self.assertEqual(K.calls(self.skills), [])

    def test_unignoring_gitignore_fails_closed(self):
        d = os.path.dirname(self.lock)
        os.makedirs(d)
        with open(os.path.join(d, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("*\n!.lock\n")
        self.refused(self.tick())

    def test_symlinked_state_dir_fails_closed(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        os.symlink(elsewhere, os.path.dirname(self.lock))
        self.refused(self.tick())
        self.assertEqual(os.listdir(elsewhere), [])


if __name__ == "__main__":
    unittest.main()
