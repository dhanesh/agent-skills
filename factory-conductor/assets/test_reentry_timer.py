import os, plistlib, re, shlex, shutil, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reentry_timer as T
from conductor_testkit import tmpdir

RID = "run-20260925T000000Z-abcdef"
ARGV = ["/usr/bin/python3", "/opt/my skills/conductor.py", "watch", "--root", "/w/it's repo"]
# every systemd/cron special char in one argument: %, $, ${X}, a backslash, a space
# a captured PATH with a space, a % and a $ in it
PATH_VAL = "/opt/my tools/bin:/usr/50%x/bin:/o/$d/bin:/usr/bin:/bin"
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

    def test_systemd_service_leaves_the_started_agent_running(self):
        # Type=oneshot with the default KillMode=control-group kills everything left in
        # the unit's cgroup when watch exits, the detached agent included (setsid does
        # not leave the cgroup), so every tick would burn an attempt.
        svc = T.render("systemd", RID, ARGV, 10)["factory-conductor-%s.service" % RID]
        service = svc.split("[Service]", 1)[1]
        self.assertIn("\nKillMode=process\n", service)
        self.assertIn("\nType=oneshot\n", service)

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

    # ── fix round 2, I5: launchd always boots itself out, last ──────────────────

    def test_launchd_uninstall_always_boots_out_even_for_its_own_job(self):
        # fix round 1 skipped the bootout for XPC_SERVICE_NAME == its own label;
        # that left the job loaded, so it kept firing no-op `watch`es until the next
        # login. Round 2: it still runs the bootout, just last (after the files are
        # already gone).
        T.install(RID, ARGV, 10, kind="launchd")
        os.environ["XPC_SERVICE_NAME"] = T.LABEL % RID
        try:
            ran = T.uninstall(RID, kind="launchd")
        finally:
            os.environ.pop("XPC_SERVICE_NAME", None)
        self.assertEqual(T.installed(RID, kind="launchd"), [])
        self.assertTrue(any("bootout" in c for c in ran), ran)

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

    # ── fix round 2, ruled: a literal \% in an argv is refused for cron ─────────

    def test_a_literal_backslash_percent_is_refused_for_cron(self):
        argv = ARGV[:-1] + ["/w/a\\%b"]
        with self.assertRaises(OSError):
            T.render("cron", RID, argv, 10)
        with self.assertRaises(OSError):
            T.install(RID, argv, 10, kind="cron")
        self.assertEqual(T.installed(RID, kind="cron"), [])

    def test_a_lone_backslash_or_a_lone_percent_is_still_fine_for_cron(self):
        T.render("cron", RID, ARGV[:-1] + ["/w/a\\nb"], 10)   # backslash, no %
        T.render("cron", RID, ARGV[:-1] + ["/w/50%h"], 10)    # %, no backslash

    # ── fix round 2, Important: no crontab binary must never crash a sweep ──────

    def test_sweep_kinds_skips_cron_without_a_binary_in_real_mode(self):
        with mock.patch.object(T, "_dry", return_value=False), \
                mock.patch.object(T.shutil, "which", return_value=None):
            self.assertNotIn("cron", T._sweep_kinds())
        with mock.patch.object(T, "_dry", return_value=False), \
                mock.patch.object(T.shutil, "which", return_value="/usr/bin/crontab"):
            self.assertIn("cron", T._sweep_kinds())

    def test_sweep_kinds_always_includes_cron_in_dry_run_even_with_no_binary(self):
        with mock.patch.object(T.shutil, "which", return_value=None):
            self.assertIn("cron", T._sweep_kinds())  # _dry() is True here (setUp)

    def test_uninstall_and_installed_work_without_a_crontab_binary(self):
        # simulate a real host that has systemd but no crontab binary at all (Arch,
        # minimal Fedora, many containers): dry-run is off so the binary check runs
        # for real, but every subprocess call is faked so the real system is still
        # never touched, and the fake asserts `crontab` is never invoked
        T.install(RID, ARGV, 10, kind="systemd")  # written while still sandboxed

        def fake_which(name):
            return None if name == "crontab" else "/usr/bin/" + name

        def fake_run(cmd, **kw):
            self.assertNotEqual(cmd[0], "crontab", "crontab must never run: no binary")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        os.environ.pop("FACTORY_CONDUCTOR_TIMER_DRYRUN", None)
        try:
            with mock.patch.object(T.shutil, "which", side_effect=fake_which), \
                    mock.patch.object(T.subprocess, "run", side_effect=fake_run):
                self.assertTrue(T.installed(RID))          # finds the systemd unit
                ran = T.uninstall(RID)                      # kind=None sweep
        finally:
            os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN"] = "1"
        self.assertTrue(any("systemctl" in c for c in ran), ran)
        self.assertEqual(T.installed(RID, kind="systemd"), [])

    def _write_kind_files(self, kind, run_id):
        """Write run_id's files for `kind` directly, bypassing install()'s own
        pre-uninstall sweep -- so two different kinds can be made to coexist on disk,
        which a normal install() call never allows (fix round 1, I3)."""
        files = T.render(kind, run_id, ARGV, 10)
        os.makedirs(T._dir(kind), exist_ok=True)
        for name, text in files.items():
            with open(os.path.join(T._dir(kind), name), "w") as f:
                f.write(text)

    def test_a_cron_read_error_does_not_block_systemd_or_launchd_removal(self):
        self._write_kind_files("systemd", RID)
        self._write_kind_files("launchd", RID)
        os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL"] = "crontab -l"
        try:
            with self.assertRaises(OSError):
                T.uninstall(RID)  # kind=None: cron fails, systemd/launchd must not
        finally:
            os.environ.pop("FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL", None)
        self.assertEqual(T.installed(RID, kind="systemd"), [])
        self.assertEqual(T.installed(RID, kind="launchd"), [])

    # ── fix round 2, Minor 5: busybox's "can't open" crontab message ────────────

    def test_busybox_cant_open_crontab_message_is_treated_as_empty(self):
        os.environ.pop("FACTORY_CONDUCTOR_TIMER_DRYRUN", None)
        try:
            with mock.patch.object(T.subprocess, "run") as m:
                m.return_value = subprocess.CompletedProcess(
                    ["crontab", "-l"], 1, stdout="",
                    stderr="crontab: can't open '/var/spool/cron/crontabs/root': "
                           "No such file or directory\n")
                self.assertEqual(T._crontab_read(), [])
        finally:
            os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN"] = "1"

    def test_an_unrelated_crontab_read_error_still_raises(self):
        os.environ.pop("FACTORY_CONDUCTOR_TIMER_DRYRUN", None)
        try:
            with mock.patch.object(T.subprocess, "run") as m:
                m.return_value = subprocess.CompletedProcess(
                    ["crontab", "-l"], 1, stdout="", stderr="permission denied\n")
                with self.assertRaises(OSError):
                    T._crontab_read()
        finally:
            os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN"] = "1"

    # ── fix round 2, Minor 4: systemd tidy-up ────────────────────────────────────

    def test_systemd_uninstall_runs_reset_failed(self):
        T.install(RID, ARGV, 10, kind="systemd")
        ran = T.uninstall(RID, kind="systemd")
        self.assertTrue(any("reset-failed" in c for c in ran), ran)

    def test_a_failing_systemd_enable_rolls_back_disable_and_daemon_reload(self):
        calls = []
        real_best_effort = T._best_effort

        def spy(cmd, ran):
            calls.append(list(cmd))
            return real_best_effort(cmd, ran)

        os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL"] = "systemctl --user enable"
        try:
            with mock.patch.object(T, "_best_effort", side_effect=spy):
                with self.assertRaises(OSError):
                    T.install(RID, ARGV, 10, kind="systemd")
        finally:
            os.environ.pop("FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL", None)
        self.assertEqual(T.installed(RID, kind="systemd"), [])
        joined = [shlex.join(c) for c in calls]
        self.assertTrue(any("disable" in c for c in joined), joined)
        self.assertTrue(any("daemon-reload" in c for c in joined), joined)


    # ── final wave C1: the timer carries the PATH captured at install ───────────
    # launchd hands a job PATH=/usr/bin:/bin:/usr/sbin:/sbin and cron PATH=/usr/bin:/bin,
    # so without this the agent, the plan's tools and the right python3 are not found.

    def test_launchd_plist_carries_the_captured_path(self):
        (_, text), = T.render("launchd", RID, ARGV, 10, path=PATH_VAL).items()
        pl = plistlib.loads(text.encode())
        self.assertEqual(pl["EnvironmentVariables"], {"PATH": PATH_VAL})
        self.assertEqual(pl["ProgramArguments"], ARGV)

    def test_systemd_service_carries_the_captured_path_quoted(self):
        svc = T.render("systemd", RID, ARGV, 10, path=PATH_VAL)[
            "factory-conductor-%s.service" % RID]
        service = svc.split("[Service]", 1)[1]
        env = [l for l in service.splitlines() if l.startswith("Environment=")]
        self.assertEqual(len(env), 1, svc)
        value = env[0][len("Environment="):]
        self.assertTrue(value.startswith('"') and value.endswith('"'), value)
        # Environment= resolves % specifiers and C escapes but never expands $, so
        # the $ is not doubled here (unlike ExecStart=)
        word = value[1:-1].replace("%%", "%").replace('\\"', '"').replace("\\\\", "\\")
        self.assertEqual(word, "PATH=" + PATH_VAL)
        self.assertNotIn("50%x", value)  # every % escaped as %%

    def test_cron_line_runs_through_env_with_the_captured_path(self):
        (_, line), = T.render("cron", RID, ARGV, 10, path=PATH_VAL).items()
        self.assertIsNone(re.search(r"(?<!\\)%", line), line)
        words = shlex.split(line.split(" # ")[0].replace("\\%", "%"))[5:]
        self.assertEqual(words, ["/usr/bin/env", "PATH=" + PATH_VAL] + ARGV)

    def test_a_literal_backslash_percent_in_the_path_is_refused_for_cron(self):
        with self.assertRaises(OSError):
            T.render("cron", RID, ARGV, 10, path="/a\\%b:/usr/bin")

    def test_install_passes_the_path_into_each_kind(self):
        # install sweeps every kind first, so each kind is checked right after its own
        T.install(RID, ARGV, 10, kind="cron", path=PATH_VAL)
        line, = T.installed(RID, kind="cron")
        self.assertIn("/usr/bin/env", line)
        T.install(RID, ARGV, 10, kind="launchd", path=PATH_VAL)
        plist, = T.installed(RID, kind="launchd")
        with open(plist, "rb") as f:
            self.assertEqual(plistlib.load(f)["EnvironmentVariables"]["PATH"], PATH_VAL)
        T.install(RID, ARGV, 10, kind="systemd", path=PATH_VAL)
        svc = [p for p in T.installed(RID, kind="systemd") if p.endswith(".service")][0]
        with open(svc) as f:
            self.assertIn("Environment=", f.read())
        T.uninstall(RID)

    def test_no_path_given_renders_no_environment(self):
        (_, text), = T.render("launchd", RID, ARGV, 10).items()
        self.assertNotIn("EnvironmentVariables", plistlib.loads(text.encode()))
        svc = T.render("systemd", RID, ARGV, 10)["factory-conductor-%s.service" % RID]
        self.assertNotIn("Environment=", svc)
        (_, line), = T.render("cron", RID, ARGV, 10).items()
        self.assertNotIn("/usr/bin/env", line)


if __name__ == "__main__":
    unittest.main()
