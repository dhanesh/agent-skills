"""One OS timer per factory-conductor run that calls `conductor watch` every
interval_min: launchd (macOS), a systemd user timer, or a tagged crontab line. Tests
set FACTORY_CONDUCTOR_TIMER_HOME and FACTORY_CONDUCTOR_TIMER_DRYRUN, and the real
system is never touched."""
import os, plistlib, shlex, shutil, subprocess, sys

LABEL = "io.agent-skills.factory-conductor.%s"
UNIT = "factory-conductor-%s"
TAG = "# factory-conductor %s"


def _home():
    return os.environ.get("FACTORY_CONDUCTOR_TIMER_HOME") or os.path.expanduser("~")


def _dry():
    return os.environ.get("FACTORY_CONDUCTOR_TIMER_DRYRUN") == "1"


def platform_kind():
    forced = os.environ.get("FACTORY_CONDUCTOR_TIMER_KIND")
    if forced in ("launchd", "systemd", "cron"):
        return forced
    if sys.platform == "darwin":
        return "launchd"
    if sys.platform.startswith("linux"):
        if shutil.which("systemctl") and subprocess.run(
                ["systemctl", "--user", "show-environment"], capture_output=True).returncode == 0:
            return "systemd"
        if shutil.which("crontab"):
            return "cron"
    return None


def _dir(kind):
    return {"launchd": os.path.join(_home(), "Library", "LaunchAgents"),
            "systemd": os.path.join(_home(), ".config", "systemd", "user")}[kind]


def render(kind, run_id, argv, interval_min):
    if kind == "launchd":
        pl = {"Label": LABEL % run_id, "ProgramArguments": list(argv),
              "StartInterval": interval_min * 60, "RunAtLoad": False}
        return {LABEL % run_id + ".plist": plistlib.dumps(pl).decode()}
    if kind == "systemd":
        u = UNIT % run_id
        return {u + ".service": "[Unit]\nDescription=factory-conductor watch %s\n\n"
                                "[Service]\nType=oneshot\nExecStart=%s\n"
                                % (run_id, shlex.join(argv)),
                u + ".timer": "[Unit]\nDescription=factory-conductor watch %s\n\n"
                              "[Timer]\nOnBootSec=%dmin\nOnUnitActiveSec=%dmin\n\n"
                              "[Install]\nWantedBy=timers.target\n"
                              % (run_id, interval_min, interval_min)}
    if kind == "cron":
        return {"crontab": "*/%d * * * * %s %s" % (interval_min, shlex.join(argv), TAG % run_id)}
    raise ValueError("unsupported timer kind: %r" % kind)


def _run(cmd, ran):
    ran.append(shlex.join(cmd))
    if not _dry():
        subprocess.run(cmd, capture_output=True)


def _crontab_read():
    if _dry():
        p = os.path.join(_home(), "crontab.txt")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return f.read().splitlines()
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout.splitlines() if r.returncode == 0 else []


def _crontab_write(lines, ran):
    text = "".join(l + "\n" for l in lines)
    ran.append("crontab -")
    if _dry():
        with open(os.path.join(_home(), "crontab.txt"), "w") as f:
            f.write(text)
    else:
        subprocess.run(["crontab", "-"], input=text, text=True, capture_output=True)


def install(run_id, argv, interval_min, kind=None):
    kind = kind or platform_kind()
    if kind is None:
        raise OSError("unsupported platform for scheduled re-entry")
    uninstall(run_id, kind=kind)
    ran = []
    files = render(kind, run_id, argv, interval_min)
    if kind == "cron":
        _crontab_write(_crontab_read() + [files["crontab"]], ran)
        return ran
    os.makedirs(_dir(kind), exist_ok=True)
    for name, text in files.items():
        with open(os.path.join(_dir(kind), name), "w") as f:
            f.write(text)
    if kind == "launchd":
        _run(["launchctl", "bootstrap", "gui/%d" % os.getuid(),
              os.path.join(_dir(kind), LABEL % run_id + ".plist")], ran)
    else:
        _run(["systemctl", "--user", "daemon-reload"], ran)
        _run(["systemctl", "--user", "enable", "--now", UNIT % run_id + ".timer"], ran)
    return ran


def installed(run_id, kind=None):
    kind = kind or platform_kind()
    if kind == "cron":
        return [l for l in _crontab_read() if l.endswith(TAG % run_id)]
    if kind is None:
        return []
    return [os.path.join(_dir(kind), n) for n in render(kind, run_id, ["x"], 5)
            if os.path.exists(os.path.join(_dir(kind), n))]


def uninstall(run_id, kind=None):
    kind = kind or platform_kind()
    ran = []
    if kind == "cron":
        lines = _crontab_read()
        keep = [l for l in lines if not l.endswith(TAG % run_id)]
        if keep != lines:
            _crontab_write(keep, ran)
        return ran
    if kind is None:
        return ran
    present = installed(run_id, kind=kind)
    if kind == "launchd" and present:
        _run(["launchctl", "bootout", "gui/%d/%s" % (os.getuid(), LABEL % run_id)], ran)
    if kind == "systemd" and present:
        _run(["systemctl", "--user", "disable", "--now", UNIT % run_id + ".timer"], ran)
    for p in present:
        os.remove(p)
    return ran
