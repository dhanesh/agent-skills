#!/usr/bin/env python3
"""`pipeline schedule` (R16): install a timer that runs `pipeline tick` every 15 to 1440
minutes with factory-conductor's timer module, and refuse any other interval with exit 2
before anything is installed. `status` shows the timer and `uninstall` removes it.

Every run sets reentry_timer's own dry-run hooks (FACTORY_CONDUCTOR_TIMER_DRYRUN=1,
FACTORY_CONDUCTOR_TIMER_HOME=<temp dir>), and puts fake launchctl, systemctl and crontab
first on PATH that log any call. The tests check that the log stays empty, so no real
launchd, systemd or cron entry is ever made on this machine.
Stdlib unittest; offline, deterministic; scratch only in temp dirs.
Run:  python3 test_pipeline_schedule.py
"""
import hashlib
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline as P  # noqa: E402
import pipeline_testkit as K  # noqa: E402

TOOL = os.path.join(HERE, "pipeline.py")
REAL_HOME = os.path.expanduser("~")

FAKE = """#!/bin/sh
echo "$0 $*" >> %s
exit 97
"""


class ScheduleFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pipeline-schedule-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.skills = K.stubs(os.path.join(self.tmp, "skills"))
        self.home = os.path.join(self.tmp, "timer-home")
        os.makedirs(self.home)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.real_calls = os.path.join(self.tmp, "real-loader-calls.log")
        for name in ("launchctl", "systemctl", "crontab"):
            path = os.path.join(self.bin, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(FAKE % shlex.quote(self.real_calls))
            os.chmod(path, 0o755)
        self.sid = P.schedule_id(self.repo)

    def tearDown(self):
        # the guard of every test: no loader on PATH was ever called
        if os.path.exists(self.real_calls):
            self.fail("a real timer loader was called: %s" % self.read(self.real_calls))

    def read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def run_cli(self, *args, kind="launchd", skills=True):
        env = dict(os.environ, FACTORY_CONDUCTOR_TIMER_DRYRUN="1",
                   FACTORY_CONDUCTOR_TIMER_HOME=self.home,
                   FACTORY_CONDUCTOR_TIMER_KIND=kind,
                   PATH=self.bin + os.pathsep + os.environ.get("PATH", os.defpath))
        env.pop("FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL", None)
        argv = [sys.executable, TOOL, "--root", self.repo]
        if skills:
            argv += ["--skills-dir", self.skills]
        return subprocess.run(argv + list(args), capture_output=True, text=True,
                              timeout=120, stdin=subprocess.DEVNULL, env=env)

    def timer_files(self):
        """Every file the dry-run timer wrote under its fake home."""
        out = []
        for d, _, files in os.walk(self.home):
            out += [os.path.join(d, n) for n in files]
        return sorted(out)

    def plist(self):
        path = os.path.join(self.home, "Library", "LaunchAgents",
                            "io.agent-skills.factory-conductor.%s.plist" % self.sid)
        with open(path, "rb") as f:
            return plistlib.load(f)


class TestInterval(ScheduleFixture):
    def test_15_and_1440_install_a_timer(self):
        K.pipeline_grant(self.repo)
        for every in ("15", "1440"):
            with self.subTest(every=every):
                cp = self.run_cli("schedule", "install", "--every", every)
                self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
                self.assertIn("SCHEDULE: installed every %s min kind=launchd id=%s"
                              % (every, self.sid), cp.stdout)
                self.assertEqual(self.plist()["StartInterval"], int(every) * 60)

    def test_out_of_range_or_not_whole_exits_2_and_installs_nothing(self):
        K.pipeline_grant(self.repo)
        for every in ("10", "1500", "14", "1441", "0", "-15", "15.0", "abc", "", "1e3"):
            with self.subTest(every=every):
                cp = self.run_cli("schedule", "install", "--every", every)
                self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
                self.assertIn("STOP: schedule every=", cp.stdout)
                self.assertEqual(self.timer_files(), [])

    def test_the_range_is_checked_before_the_grant_and_the_root(self):
        # no grant, no conductor, a root that does not exist: the interval still decides
        self.repo = os.path.join(self.tmp, "missing")
        cp = self.run_cli("schedule", "install", "--every", "10", skills=False)
        self.assertEqual(cp.returncode, 2)
        self.assertIn("reason=out-of-range", cp.stdout)
        self.assertNotIn("no-grant", cp.stdout)
        self.assertNotIn("not a directory", cp.stdout)

    def test_a_bad_interval_leaves_an_installed_timer_alone(self):
        K.pipeline_grant(self.repo)
        self.assertEqual(self.run_cli("schedule", "install", "--every", "30").returncode, 0)
        before = self.plist()
        self.assertEqual(self.run_cli("schedule", "install", "--every", "1500").returncode, 2)
        self.assertEqual(self.plist(), before)


class TestTimer(ScheduleFixture):
    def test_timer_runs_tick_by_absolute_paths_as_an_argv_list(self):
        K.pipeline_grant(self.repo)
        cp = self.run_cli("schedule", "install", "--every", "15")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        argv = self.plist()["ProgramArguments"]
        self.assertEqual(argv, [os.path.abspath(sys.executable), TOOL, "--root", self.repo,
                                "--skills-dir", self.skills, "tick"])
        self.assertTrue(all(os.path.isabs(argv[i]) for i in (0, 1, 3)))

    def test_timer_gets_the_path_that_install_ran_with(self):
        K.pipeline_grant(self.repo)
        self.run_cli("schedule", "install", "--every", "15")
        path = self.plist()["EnvironmentVariables"]["PATH"]
        self.assertTrue(path.startswith(self.bin + os.pathsep), path)

    def test_id_is_stable_per_repo_and_never_a_conductor_run_id(self):
        self.assertEqual(self.sid, P.schedule_id(self.repo + "/"))
        want = hashlib.sha256(os.path.realpath(self.repo).encode()).hexdigest()[:12]
        self.assertEqual(self.sid, "pipeline-" + want)
        self.assertNotEqual(self.sid, P.schedule_id(self.tmp))
        self.assertFalse(P.RUN_ID.match(self.sid))

    def test_cron_and_systemd_install_too(self):
        K.pipeline_grant(self.repo)
        cp = self.run_cli("schedule", "install", "--every", "1440", kind="cron")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        line = self.read(os.path.join(self.home, "crontab.txt")).strip()
        self.assertTrue(line.startswith("0 0 * * * /usr/bin/env "), line)
        self.assertTrue(line.endswith("# factory-conductor %s" % self.sid))
        self.assertIn(" tick # factory-conductor", line)
        cp = self.run_cli("schedule", "install", "--every", "15", kind="systemd")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        # install sweeps every kind first, so the cron line is gone
        self.assertEqual(self.read(os.path.join(self.home, "crontab.txt")), "")
        timer = os.path.join(self.home, ".config", "systemd", "user",
                             "factory-conductor-%s.timer" % self.sid)
        self.assertIn("OnUnitActiveSec=15min", self.read(timer))

    def test_cron_refuses_an_uneven_hour_interval_and_keeps_the_old_timer(self):
        K.pipeline_grant(self.repo)
        self.assertEqual(self.run_cli("schedule", "install", "--every", "60",
                                      kind="cron").returncode, 0)
        before = self.read(os.path.join(self.home, "crontab.txt"))
        cp = self.run_cli("schedule", "install", "--every", "90", kind="cron")
        self.assertEqual(cp.returncode, 2)
        self.assertIn("SCHEDULE: failed", cp.stdout)
        self.assertEqual(self.read(os.path.join(self.home, "crontab.txt")), before)


class TestRefusals(ScheduleFixture):
    def test_no_live_grant_installs_nothing(self):
        cp = self.run_cli("schedule", "install", "--every", "15")
        self.assertEqual(cp.returncode, 2)
        self.assertIn("STOP: no-grant reason=none", cp.stdout)
        self.assertEqual(self.timer_files(), [])

    def test_no_factory_conductor_is_unavailable(self):
        K.pipeline_grant(self.repo)
        shutil.rmtree(os.path.join(self.skills, "factory-conductor"))
        for args in (("install", "--every", "15"), ("status",), ("uninstall",)):
            with self.subTest(args=args):
                cp = self.run_cli("schedule", *args)
                self.assertEqual(cp.returncode, 2)
                self.assertIn("SCHEDULE: unavailable factory-conductor", cp.stdout)
        self.assertEqual(self.timer_files(), [])


class TestStatusUninstall(ScheduleFixture):
    def test_status_then_uninstall(self):
        cp = self.run_cli("schedule", "status")
        self.assertEqual(cp.returncode, 0)
        self.assertIn("SCHEDULE: timer none", cp.stdout)
        K.pipeline_grant(self.repo)
        self.run_cli("schedule", "install", "--every", "15")
        cp = self.run_cli("schedule", "status")
        self.assertIn("SCHEDULE: id %s" % self.sid, cp.stdout)
        self.assertIn("io.agent-skills.factory-conductor.%s.plist" % self.sid, cp.stdout)
        cp = self.run_cli("schedule", "uninstall")
        self.assertEqual(cp.returncode, 0)
        self.assertIn("SCHEDULE: uninstalled id %s" % self.sid, cp.stdout)
        self.assertEqual(self.timer_files(), [])
        self.assertIn("SCHEDULE: timer none", self.run_cli("schedule", "status").stdout)

    def test_uninstall_needs_no_grant_and_keeps_other_timers(self):
        K.pipeline_grant(self.repo)
        self.run_cli("schedule", "install", "--every", "30", kind="cron")
        other = "* * * * * true # factory-conductor run-20261104T000000Z-abcdef\n"
        crontab = os.path.join(self.home, "crontab.txt")
        with open(crontab, "a", encoding="utf-8") as f:
            f.write(other)
        for name in os.listdir(os.path.join(self.repo, ".skill-contract", "envelopes")):
            os.remove(os.path.join(self.repo, ".skill-contract", "envelopes", name))
        cp = self.run_cli("schedule", "uninstall", kind="cron")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(self.read(crontab), other)


class TestNoRealTimer(unittest.TestCase):
    def test_the_dry_run_home_is_never_the_real_home(self):
        # the fixture's timer home is a temp dir; reentry_timer writes only there
        fx = ScheduleFixture("run")
        fx.setUp()
        try:
            self.assertNotEqual(os.path.realpath(fx.home), os.path.realpath(REAL_HOME))
            self.assertTrue(os.path.realpath(fx.home).startswith(os.path.realpath(fx.tmp)))
        finally:
            shutil.rmtree(fx.tmp, True)

    def test_a_fake_loader_on_path_is_what_a_real_call_would_hit(self):
        # the guard in tearDown works: a call to a loader on the fixture PATH is logged
        fx = ScheduleFixture("run")
        fx.setUp()
        try:
            subprocess.run([os.path.join(fx.bin, "crontab"), "-l"], capture_output=True)
            self.assertIn("crontab -l", fx.read(fx.real_calls))
        finally:
            shutil.rmtree(fx.tmp, True)


if __name__ == "__main__":
    unittest.main()
