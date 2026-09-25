"""One OS timer per factory-conductor run that calls `conductor watch` every
interval_min: launchd (macOS), a systemd user timer, or a tagged crontab line. Tests
set FACTORY_CONDUCTOR_TIMER_HOME and FACTORY_CONDUCTOR_TIMER_DRYRUN, and the real
system is never touched.

FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL=<substring> is a test-only hook: in dry-run mode,
a loader "command" (the joined argv, or "crontab -l"/"crontab -") that contains the
substring raises OSError instead of running, so a fix for a swallowed failure can be
tested without a real loader."""
import os, plistlib, shlex, shutil, subprocess, sys

LABEL = "io.agent-skills.factory-conductor.%s"
UNIT = "factory-conductor-%s"
TAG = "# factory-conductor %s"
KINDS = ("cron", "launchd", "systemd")


def _home():
    return os.environ.get("FACTORY_CONDUCTOR_TIMER_HOME") or os.path.expanduser("~")


def _dry():
    return os.environ.get("FACTORY_CONDUCTOR_TIMER_DRYRUN") == "1"


def _dry_fail(label):
    """Raise OSError when the test hook is armed for this command label."""
    fail = os.environ.get("FACTORY_CONDUCTOR_TIMER_DRYRUN_FAIL")
    if fail and fail in label:
        raise OSError("dry-run failure hook matched %r: %s" % (fail, label))


def platform_kind():
    forced = os.environ.get("FACTORY_CONDUCTOR_TIMER_KIND")
    if forced in ("launchd", "systemd", "cron"):
        return forced
    if sys.platform == "darwin":
        return "launchd"
    if sys.platform.startswith("linux"):
        if shutil.which("systemctl"):
            try:
                ok = subprocess.run(["systemctl", "--user", "show-environment"],
                                    capture_output=True, timeout=10).returncode == 0
            except subprocess.TimeoutExpired:
                ok = False
            if ok:
                return "systemd"
        if shutil.which("crontab"):
            return "cron"
    return None


def _has_crontab_binary():
    return shutil.which("crontab") is not None


def _sweep_kinds():
    """Which kinds a kind=None sweep checks. cron is included when it can actually be
    read: dry-run always can (a stand-in file, no binary needed); real mode only when
    a `crontab` binary exists. A real host with no crontab at all (Arch, minimal
    Fedora, many containers) has nothing to sweep there, and must not crash trying."""
    if _dry() or _has_crontab_binary():
        return KINDS
    return tuple(k for k in KINDS if k != "cron")


def _dir(kind):
    return {"launchd": os.path.join(_home(), "Library", "LaunchAgents"),
            "systemd": os.path.join(_home(), ".config", "systemd", "user")}[kind]


def _names(kind, run_id):
    """The filenames a run writes under _dir(kind); cron has no files (a shared-file
    tagged line instead), so it is not one of these two."""
    if kind == "launchd":
        return [LABEL % run_id + ".plist"]
    if kind == "systemd":
        u = UNIT % run_id
        return [u + ".service", u + ".timer"]
    raise ValueError("unsupported timer kind: %r" % kind)


def _sd_quote(a, dollar=True):
    """One systemd ExecStart= word: systemd's own quoting, verified against a real
    systemd (257) with `systemctl show`. This is not shell quoting -- systemd expands
    `%` as a specifier and C-unescapes `\\` and `$...` itself, even inside quotes, so
    shlex.quote (POSIX shell quoting) is the wrong tool here.

    dollar=False is for an Environment= assignment: systemd resolves specifiers and C
    escapes there too, but never expands `$`, so a doubled `$$` would stay doubled."""
    a = a.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return '"%s"' % (a.replace("$", "$$") if dollar else a)


def render(kind, run_id, argv, interval_min, path=None):
    """The files (or, for cron, the one tagged line) for run_id's timer. `path`, when
    given, is the PATH the timer runs argv with: a timer's own PATH is minimal
    (launchd: /usr/bin:/bin:/usr/sbin:/sbin, cron: /usr/bin:/bin), too short to find
    the user's agent, the plan's tools or the python3 they run, so install passes the
    PATH it was run with."""
    if kind == "launchd":
        pl = {"Label": LABEL % run_id, "ProgramArguments": list(argv),
              "StartInterval": interval_min * 60, "RunAtLoad": False}
        if path is not None:
            pl["EnvironmentVariables"] = {"PATH": path}
        return {LABEL % run_id + ".plist": plistlib.dumps(pl).decode()}
    if kind == "systemd":
        u = UNIT % run_id
        desc = ("factory-conductor watch %s" % run_id).replace("%", "%%")
        exec_start = " ".join(_sd_quote(a) for a in argv)
        env = ("Environment=%s\n" % _sd_quote("PATH=" + path, dollar=False)
               if path is not None else "")
        # KillMode=process: when watch (the oneshot's main process) exits, systemd must
        # not kill the rest of the unit's cgroup. The agent watch started is in that
        # cgroup (start_new_session does not leave it), and the default control-group
        # mode would kill it at every tick.
        return {u + ".service": "[Unit]\nDescription=%s\n\n"
                                "[Service]\nType=oneshot\nKillMode=process\n%sExecStart=%s\n"
                                % (desc, env, exec_start),
                u + ".timer": "[Unit]\nDescription=%s\n\n"
                              "[Timer]\nOnBootSec=%dmin\nOnUnitActiveSec=%dmin\n\n"
                              "[Install]\nWantedBy=timers.target\n"
                              % (desc, interval_min, interval_min)}
    if kind == "cron":
        # cron itself (not the shell) scans the line for an unescaped `%` and turns it
        # into a newline (stdin separator); `\%` is cron's own escape for a literal `%`
        # and is stripped before the line ever reaches sh -c, so this must run on the
        # raw joined text, not be shell-quote-aware. A literal `\%` already present in
        # an argument cannot be told apart from our own escaping reliably across real
        # cron implementations (verified against Debian cron), so it is refused
        # outright rather than risk a silently wrong command. The captured PATH goes
        # through /usr/bin/env as one more argument, so the same escaping covers it.
        if path is not None:
            argv = ["/usr/bin/env", "PATH=" + path] + list(argv)
        for a in argv:
            if "\\%" in a:
                raise OSError("cron cannot express a literal \\% in a path")
        cmd = shlex.join(argv).replace("%", "\\%")
        return {"crontab": "*/%d * * * * %s %s" % (interval_min, cmd, TAG % run_id)}
    raise ValueError("unsupported timer kind: %r" % kind)


def _run(cmd, ran):
    label = shlex.join(cmd)
    ran.append(label)
    if _dry():
        _dry_fail(label)
        return
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise OSError((r.stderr or r.stdout or "").strip()
                      or "command failed (%d): %s" % (r.returncode, label))


def _best_effort(cmd, ran):
    """Run cmd (dry-run aware) without ever raising: a cleanup/tidy-up step whose own
    failure must never mask the real error already in flight, or block removing the
    other kinds in a sweep."""
    ran.append(shlex.join(cmd))
    if _dry():
        return
    subprocess.run(cmd, capture_output=True)


def _crontab_read():
    label = "crontab -l"
    if _dry():
        _dry_fail(label)
        p = os.path.join(_home(), "crontab.txt")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return f.read().splitlines()
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if r.returncode != 0:
        err = (r.stderr or "").lower()
        # "no crontab for X" (Vixie/ISC cron); busybox crond says "can't open
        # '<path>': ..." -- both mean an empty crontab, not a failure.
        if "no crontab" in err or ("can't open" in err and "crontab" in err):
            return []
        raise OSError((r.stderr or "").strip() or "crontab -l failed")
    return r.stdout.splitlines()


def _crontab_write(lines, ran):
    label = "crontab -"
    ran.append(label)
    text = "".join(l + "\n" for l in lines)
    if _dry():
        _dry_fail(label)
        with open(os.path.join(_home(), "crontab.txt"), "w") as f:
            f.write(text)
        return
    r = subprocess.run(["crontab", "-"], input=text, text=True, capture_output=True)
    if r.returncode != 0:
        raise OSError((r.stderr or "").strip() or "crontab - failed")


def install(run_id, argv, interval_min, kind=None, path=None):
    """Write and load run_id's timer as `kind` (or the detected platform kind), running
    argv with PATH=`path` when given (see render).

    Every existing timer for run_id, under any kind, is swept first (a run whose kind
    changed between installs must never leave an orphaned entry under the old one), so
    the returned command list starts with that sweep's. On a loader failure after files
    were written (systemd/launchd), those files are removed again -- for systemd, a
    best-effort disable + daemon-reload follows too, so a partially-enabled unit never
    leaves a dangling wants symlink -- before the error propagates, so a failed install
    never leaves a definition behind."""
    kind = kind or platform_kind()
    if kind is None:
        raise OSError("unsupported platform for scheduled re-entry")
    ran = uninstall(run_id)  # sweep every kind, not just this one (I3)
    files = render(kind, run_id, argv, interval_min, path=path)
    if kind == "cron":
        _crontab_write(_crontab_read() + [files["crontab"]], ran)
        return ran
    os.makedirs(_dir(kind), exist_ok=True)
    written = []
    try:
        for name, text in files.items():
            path = os.path.join(_dir(kind), name)
            with open(path, "w") as f:
                f.write(text)
            written.append(path)
        if kind == "launchd":
            _run(["launchctl", "bootstrap", "gui/%d" % os.getuid(),
                  os.path.join(_dir(kind), LABEL % run_id + ".plist")], ran)
        else:
            _run(["systemctl", "--user", "daemon-reload"], ran)
            _run(["systemctl", "--user", "enable", "--now", UNIT % run_id + ".timer"], ran)
    except Exception:
        for path in written:
            try:
                os.remove(path)
            except OSError:
                pass
        if kind == "systemd":
            _best_effort(["systemctl", "--user", "disable", UNIT % run_id + ".timer"], [])
            _best_effort(["systemctl", "--user", "daemon-reload"], [])
        raise
    return ran


def installed(run_id, kind=None):
    """run_id's installed timer artifacts. kind=None sweeps every kind that can
    actually be checked (see _sweep_kinds) by run id, rather than trusting a single
    detected kind, so a run installed under one kind is still found after the
    detected kind changes (a different host, a systemd user session that came up
    later, ...). Each kind is tried independently; if any fail, their errors are
    combined into one OSError raised only after every kind was attempted, so one
    kind's failure never hides another's result."""
    if kind is None:
        out = []
        errors = []
        for k in _sweep_kinds():
            try:
                out += installed(run_id, kind=k)
            except OSError as e:
                errors.append("%s: %s" % (k, e))
        if errors:
            raise OSError("; ".join(errors))
        return out
    if kind == "cron":
        return [l for l in _crontab_read() if l.endswith(TAG % run_id)]
    return [os.path.join(_dir(kind), n) for n in _names(kind, run_id)
            if os.path.exists(os.path.join(_dir(kind), n))]


def uninstall(run_id, kind=None):
    """Remove run_id's timer. kind=None sweeps every kind that can actually be
    checked (see _sweep_kinds), best-effort per kind: one kind's failure (a cron
    read error, say) never blocks removing the others, and all of their errors are
    combined into one OSError raised only after every kind was attempted.

    Files are deleted before the unload command runs (verified against real systemd
    257: `disable --now` on a unit systemd already has loaded still stops and
    disables it once the file is gone). launchd always runs its own bootout too, as
    the very last action, even when this process IS the job being uninstalled
    (XPC_SERVICE_NAME == its label): skipping a self-bootout left the job loaded, so
    it kept firing no-op `watch` calls until the next login instead of ending when
    this process exits."""
    if kind is None:
        ran = []
        errors = []
        for k in _sweep_kinds():
            try:
                ran += uninstall(run_id, kind=k)
            except OSError as e:
                errors.append("%s: %s" % (k, e))
        if errors:
            raise OSError("; ".join(errors))
        return ran
    if kind == "cron":
        lines = _crontab_read()
        keep = [l for l in lines if not l.endswith(TAG % run_id)]
        ran = []
        if keep != lines:
            _crontab_write(keep, ran)
        return ran
    ran = []
    present = installed(run_id, kind=kind)
    for path in present:
        os.remove(path)
    if not present:
        return ran
    if kind == "launchd":
        _run(["launchctl", "bootout", "gui/%d/%s" % (os.getuid(), LABEL % run_id)], ran)
    if kind == "systemd":
        _run(["systemctl", "--user", "daemon-reload"], ran)
        _run(["systemctl", "--user", "disable", "--now", UNIT % run_id + ".timer"], ran)
        _best_effort(["systemctl", "--user", "reset-failed", UNIT % run_id + ".timer",
                      UNIT % run_id + ".service"], ran)
    return ran
