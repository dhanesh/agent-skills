"""Scheduled re-entry for factory-conductor runs: the run lock, the lease, the watch
decision, the fixed resume prompt and the agent spawn. Stdlib only.

A lease says who drives a run. A reentry lease (an agent `watch` started) is live
exactly while its pid lives on this host. A session lease (any other driver) is live
while it was renewed, or an in-flight task's worktree changed, within stall_min: an
executor subagent can work for a long time without calling the conductor."""
import contextlib, fcntl, json, os, socket, tempfile, time
from datetime import datetime, timezone

LOCK_FILE = ".lock"
LEASE_FILE = "lease.json"
# verify and finish hold the lock while their commands run, for up to 600 s each. A
# second command that gave up sooner would exit 2, and the protocol parks a task on an
# uncovered exit 2, so the wait outlasts the longest command.
LOCK_TIMEOUT = 900
ENV_REENTRY = "FACTORY_CONDUCTOR_REENTRY"
_STAMP = "%Y-%m-%dT%H:%M:%SZ"


class RunLocked(Exception):
    pass


@contextlib.contextmanager
def run_lock(run_dir, timeout=None):
    """Hold an exclusive flock on <run_dir>/.lock, so two drivers never interleave
    writes to state.json or the log. Raises RunLocked after `timeout` seconds
    (default: the module's LOCK_TIMEOUT, read at call time so tests can shorten it)."""
    f = open(os.path.join(run_dir, LOCK_FILE), "a+")
    try:
        deadline = time.monotonic() + (LOCK_TIMEOUT if timeout is None else timeout)
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RunLocked(run_dir)
                time.sleep(0.1)
        yield
    finally:
        f.close()


def _stamp(now):
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(_STAMP)


def _write(run_dir, lease):
    """Replace the lease atomically: a reader sees the old lease or the new one."""
    fd, tmp = tempfile.mkstemp(dir=run_dir, prefix=".lease.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(lease, f, sort_keys=True)
            f.write("\n")
        os.replace(tmp, os.path.join(run_dir, LEASE_FILE))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_lease(run_dir):
    """The lease dict, {"corrupt": True} for an unreadable one, or None when absent."""
    path = os.path.join(run_dir, LEASE_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            lease = json.load(f)
        return lease if isinstance(lease, dict) else {"corrupt": True}
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"corrupt": True}


def renew_lease(run_dir, now=None):
    """Renew the lease for the command running now. A command run by the agent that
    watch started (ENV_REENTRY == the lease's n) keeps the reentry holder and its pid;
    any other command takes the lease as a session."""
    old = read_lease(run_dir) or {}
    if old.get("holder") == "reentry" and str(old.get("n")) == os.environ.get(ENV_REENTRY):
        _write(run_dir, dict(old, renewed_at=_stamp(now)))
        return
    _write(run_dir, {"holder": "session", "host": socket.gethostname(), "pid": None,
                     "renewed_at": _stamp(now), "n": old.get("n", 0)})


def set_reentry_lease(run_dir, n, pid, now=None):
    _write(run_dir, {"holder": "reentry", "host": socket.gethostname(), "pid": pid,
                     "renewed_at": _stamp(now), "n": n})


def pid_alive(pid):
    """True while process `pid` runs on this host. A zombie child of this process is
    reaped and counts as dead; a pid that is not a positive integer is dead."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:  # os.kill(0 or -n, 0) would probe a process group, not a process
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    try:
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:  # not our child: os.kill already said it lives
        return True


def _newest_mtime(path):
    newest = 0.0
    for base, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in [base] + [os.path.join(base, n) for n in files]:
            try:
                newest = max(newest, os.stat(name).st_mtime)
            except OSError:
                pass
    try:  # a worktree's .git is a file naming its gitdir; commits touch HEAD and index
        with open(os.path.join(path, ".git"), encoding="utf-8") as f:
            gitdir = f.read().split("gitdir:", 1)[1].strip()
        gitdir = os.path.join(path, gitdir)  # a relative gitdir is relative to the worktree
        for name in ("HEAD", "index"):
            with contextlib.suppress(OSError):
                newest = max(newest, os.stat(os.path.join(gitdir, name)).st_mtime)
    except (OSError, IndexError):
        pass
    return newest


def lease_live(run_dir, lease, stall_min, worktrees, now=None):
    """True when someone is (or may be) driving the run; when in doubt, True."""
    now_ts = (now or datetime.now(timezone.utc)).timestamp()
    window = stall_min * 60
    if lease is None:
        return False
    if lease.get("corrupt"):
        try:
            return now_ts - os.stat(os.path.join(run_dir, LEASE_FILE)).st_mtime < window
        except OSError:
            return False
    if lease.get("holder") == "reentry":
        return lease.get("host") == socket.gethostname() and pid_alive(lease.get("pid"))
    try:
        renewed = datetime.strptime(lease["renewed_at"], _STAMP) \
            .replace(tzinfo=timezone.utc).timestamp()
    except (KeyError, TypeError, ValueError):
        return True
    if now_ts - renewed < window:
        return True
    return any(now_ts - _newest_mtime(w) < window for w in worktrees if os.path.isdir(w))
