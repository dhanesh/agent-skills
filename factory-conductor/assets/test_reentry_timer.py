import os, plistlib, shlex, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reentry_timer as T
from conductor_testkit import tmpdir

RID = "run-20260925T000000Z-abcdef"
ARGV = ["/usr/bin/python3", "/opt/my skills/conductor.py", "watch", "--root", "/w/it's repo"]


class TimerTests(unittest.TestCase):
    def setUp(self):
        self.home = tmpdir()
        os.environ["FACTORY_CONDUCTOR_TIMER_HOME"] = self.home
        os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN"] = "1"

    def tearDown(self):
        for k in ("FACTORY_CONDUCTOR_TIMER_HOME", "FACTORY_CONDUCTOR_TIMER_DRYRUN"):
            os.environ.pop(k, None)

    def test_home_is_the_override_never_the_real_home(self):
        # guard: with the overrides set, _home() must never resolve to the real ~
        self.assertEqual(T._home(), self.home)
        self.assertNotEqual(T._home(), os.path.expanduser("~"))

    def test_launchd_plist_carries_the_argv_array_and_interval(self):
        files = T.render("launchd", RID, ARGV, 10)
        (name, text), = files.items()
        self.assertEqual(name, "io.agent-skills.factory-conductor.%s.plist" % RID)
        pl = plistlib.loads(text.encode())
        self.assertEqual((pl["ProgramArguments"], pl["StartInterval"]), (ARGV, 600))

    def test_systemd_quotes_paths_with_spaces_and_quotes(self):
        files = T.render("systemd", RID, ARGV, 10)
        svc = files["factory-conductor-%s.service" % RID]
        line = [l for l in svc.splitlines() if l.startswith("ExecStart=")][0]
        self.assertEqual(shlex.split(line[len("ExecStart="):]), ARGV)
        self.assertIn("OnUnitActiveSec=10min", files["factory-conductor-%s.timer" % RID])

    def test_cron_line_is_tagged_and_quoted(self):
        (_, line), = T.render("cron", RID, ARGV, 10).items()
        self.assertTrue(line.endswith("# factory-conductor %s" % RID))
        self.assertTrue(line.startswith("*/10 * * * * "))
        self.assertEqual(shlex.split(line.split(" # ")[0])[5:], ARGV)

    def test_install_then_uninstall_leaves_nothing(self):
        for kind in ("launchd", "systemd", "cron"):
            with self.subTest(kind=kind):
                T.install(RID, ARGV, 10, kind=kind)
                self.assertTrue(T.installed(RID, kind=kind))
                T.uninstall(RID, kind=kind)
                self.assertEqual(T.installed(RID, kind=kind), [])

    def test_uninstall_keeps_other_runs_cron_lines(self):
        other = "run-20260925T000001Z-000000"
        T.install(RID, ARGV, 10, kind="cron")
        T.install(other, ARGV, 10, kind="cron")
        T.uninstall(RID, kind="cron")
        self.assertEqual(len(T.installed(other, kind="cron")), 1)

    def test_install_is_idempotent(self):
        T.install(RID, ARGV, 10, kind="cron")
        T.install(RID, ARGV, 10, kind="cron")
        self.assertEqual(len(T.installed(RID, kind="cron")), 1)

    def test_platform_kind_honours_the_forced_override_without_shelling_out(self):
        os.environ["FACTORY_CONDUCTOR_TIMER_KIND"] = "systemd"
        try:
            self.assertEqual(T.platform_kind(), "systemd")
        finally:
            os.environ.pop("FACTORY_CONDUCTOR_TIMER_KIND", None)

    def test_installed_and_uninstall_are_harmless_on_an_unsupported_platform(self):
        # an unsupported platform is platform_kind() returning None; force that
        # (kind=None alone would just re-run detection) and confirm neither call
        # touches the filesystem or raises
        with mock.patch.object(T, "platform_kind", return_value=None):
            self.assertEqual(T.installed(RID, kind=None), [])
            self.assertEqual(T.uninstall(RID, kind=None), [])

    def test_install_with_no_supported_kind_raises_oserror(self):
        with mock.patch.object(T, "platform_kind", return_value=None):
            with self.assertRaises(OSError):
                T.install(RID, ARGV, 10, kind=None)


if __name__ == "__main__":
    unittest.main()
