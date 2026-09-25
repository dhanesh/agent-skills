import os, plistlib, re, shlex, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reentry_timer as T
from conductor_testkit import tmpdir

RID = "run-20260925T000000Z-abcdef"
ARGV = ["/usr/bin/python3", "/opt/my skills/conductor.py", "watch", "--root", "/w/it's repo"]
# every systemd/cron special char in one argument: %, $, ${X}, a backslash, a space
SPECIAL_ARGV = ["/usr/bin/python3", "/opt/a b/conductor.py", "watch", "--root",
                "/w/50%h $HOME ${USER} it's \"q\" back\\slash"]


def _sd_unescape(word):
    """The inverse of reentry_timer._sd_quote's escaping, applied in the exact reverse
    order (dollar, then percent, then quote, then backslash), written independently
    here so the test does not just call the code it is checking."""
    word = word.replace("$$", "$")
    word = word.replace("%%", "%")
    word = word.replace('\\"', '"')
    word = word.replace("\\\\", "\\")
    return word


def _sd_words(exec_start_value):
    """Split a systemd ExecStart= value (our own `"w1" "w2" ...` quoting) into words."""
    return [_sd_unescape(m) for m in re.findall(r'"((?:\\.|[^"\\])*)"', exec_start_value)]


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

    def test_installed_and_uninstall_with_no_kind_never_call_platform_kind(self):
        # I3: kind=None sweeps all three concrete kinds by run id; it must never
        # consult platform_kind() to decide what to sweep (that was the bug)
        with mock.patch.object(T, "platform_kind",
                               side_effect=AssertionError("platform_kind() must not be called")):
            self.assertEqual(T.installed(RID), [])
            self.assertEqual(T.uninstall(RID), [])

    def test_install_with_no_supported_kind_raises_oserror(self):
        with mock.patch.object(T, "platform_kind", return_value=None):
            with self.assertRaises(OSError):
                T.install(RID, ARGV, 10, kind=None)

    # ── I3: uninstall/installed with no kind sweep every kind ───────────────────

    def test_uninstall_with_no_kind_sweeps_every_kind_regardless_of_detection(self):
        T.install(RID, ARGV, 10, kind="cron")
        with mock.patch.object(T, "platform_kind", return_value="systemd"):
            T.uninstall(RID)  # kind=None: must still find and remove the cron line
        self.assertEqual(T.installed(RID, kind="cron"), [])

    def test_installed_with_no_kind_finds_a_timer_installed_under_any_kind(self):
        for kind in ("launchd", "systemd", "cron"):
            with self.subTest(kind=kind):
                T.install(RID, ARGV, 10, kind=kind)
                self.assertTrue(T.installed(RID))
                T.uninstall(RID)
                self.assertEqual(T.installed(RID), [])

    def test_installs_pre_uninstall_sweep_clears_a_previous_kind(self):
        T.install(RID, ARGV, 10, kind="cron")
        T.install(RID, ARGV, 10, kind="systemd")
        self.assertEqual(T.installed(RID, kind="cron"), [])
        self.assertTrue(T.installed(RID, kind="systemd"))
        T.uninstall(RID)

    # ── I4: a loader/crontab failure is raised, not swallowed ───────────────────

    def test_a_failing_loader_raises_and_leaves_no_files(self):
        os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL"] = "systemctl --user enable"
        try:
            with self.assertRaises(OSError):
                T.install(RID, ARGV, 10, kind="systemd")
        finally:
            os.environ.pop("FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL", None)
        self.assertEqual(T.installed(RID, kind="systemd"), [])

    def test_a_failing_launchd_bootstrap_raises_and_leaves_no_plist(self):
        os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL"] = "launchctl bootstrap"
        try:
            with self.assertRaises(OSError):
                T.install(RID, ARGV, 10, kind="launchd")
        finally:
            os.environ.pop("FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL", None)
        self.assertEqual(T.installed(RID, kind="launchd"), [])

    def test_a_failing_crontab_read_raises_and_leaves_the_file_untouched(self):
        T.install(RID, ARGV, 10, kind="cron")
        path = os.path.join(self.home, "crontab.txt")
        with open(path) as f:
            before = f.read()
        os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL"] = "crontab -l"
        try:
            with self.assertRaises(OSError):
                T.install(RID, ARGV, 10, kind="cron")
        finally:
            os.environ.pop("FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL", None)
        with open(path) as f:
            after = f.read()
        self.assertEqual(before, after)

    # ── I5: launchd never boots itself out ───────────────────────────────────

    def test_launchd_uninstall_skips_bootout_for_its_own_job(self):
        T.install(RID, ARGV, 10, kind="launchd")
        os.environ["XPC_SERVICE_NAME"] = T.LABEL % RID
        try:
            ran = T.uninstall(RID, kind="launchd")
        finally:
            os.environ.pop("XPC_SERVICE_NAME", None)
        self.assertEqual(T.installed(RID, kind="launchd"), [])
        self.assertFalse(any("bootout" in c for c in ran), ran)

    def test_launchd_uninstall_boots_out_a_different_job(self):
        T.install(RID, ARGV, 10, kind="launchd")
        os.environ["XPC_SERVICE_NAME"] = "some.other.job"
        try:
            ran = T.uninstall(RID, kind="launchd")
        finally:
            os.environ.pop("XPC_SERVICE_NAME", None)
        self.assertEqual(T.installed(RID, kind="launchd"), [])
        self.assertTrue(any("bootout" in c for c in ran), ran)

    # ── I1/I2/I6: systemd and cron quoting round-trip every special char ────────

    def test_systemd_round_trips_percent_dollar_backslash_and_quotes(self):
        files = T.render("systemd", RID, SPECIAL_ARGV, 10)
        svc = files["factory-conductor-%s.service" % RID]
        line = [l for l in svc.splitlines() if l.startswith("ExecStart=")][0]
        self.assertEqual(_sd_words(line[len("ExecStart="):]), SPECIAL_ARGV)

    def test_systemd_description_escapes_percent_in_the_run_id(self):
        weird_id = "run-weird-50%off"
        files = T.render("systemd", weird_id, ARGV, 10)
        svc = [t for n, t in files.items() if n.endswith(".service")][0]
        desc = [l for l in svc.splitlines() if l.startswith("Description=")][0]
        self.assertIn("50%%off", desc)
        self.assertNotIn("50%off", desc)

    def test_cron_escapes_percent_and_round_trips(self):
        (_, line), = T.render("cron", RID, SPECIAL_ARGV, 10).items()
        # every % in the line is backslash-escaped (an unescaped one cuts the command)
        self.assertIsNone(re.search(r"(?<!\\)%", line), line)
        cmd_part = line.split(" # ")[0]
        unescaped = cmd_part.replace("\\%", "%")  # cron's own unescaping, before sh sees it
        self.assertEqual(shlex.split(unescaped)[5:], SPECIAL_ARGV)


if __name__ == "__main__":
    unittest.main()
