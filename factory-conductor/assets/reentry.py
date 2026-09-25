"""Scheduled re-entry for factory-conductor runs: the run lock, the lease, the watch
decision, the fixed resume prompt and the agent spawn. Stdlib only.

A lease says who drives a run. A reentry lease (an agent `watch` started) is live
exactly while its pid lives on this host. A session lease (any other driver) is live
while it was renewed, or an in-flight task's worktree changed, within stall_min: an
executor subagent can work for a long time without calling the conductor."""
import contextlib, fcntl, json, os, re, socket, subprocess, sys, tempfile, time
from datetime import datetime, timezone

LOCK_FILE = ".lock"
LEASE_FILE = "lease.json"
# verify and finish hold the lock while their commands run, for up to 600 s each. A
# second command that gave up sooner would exit 2, and the protocol parks a task on an
# uncovered exit 2, so the wait outlasts the longest command.
LOCK_TIMEOUT = 900
LOCK_NOTICE_AFTER = 2.0  # seconds of waiting before run_lock says why it is waiting
LOCK_NOTICE = "waiting for the run lock held by another conductor command\u2026\n"
ENV_REENTRY = "FACTORY_CONDUCTOR_REENTRY"
_STAMP = "%Y-%m-%dT%H:%M:%SZ"


class RunLocked(Exception):
    pass


@contextlib.contextmanager
def run_lock(run_dir, timeout=None):
    """Hold an exclusive flock on <run_dir>/.lock, so two drivers never interleave
    writes to state.json or the log. Raises RunLocked after `timeout` seconds
    (default: the module's LOCK_TIMEOUT, read at call time so tests can shorten it).
    After LOCK_NOTICE_AFTER seconds of waiting it says so, once, on stderr.

    Not re-entrant: nested use in one process deadlocks (until the timeout), because
    each call opens a new file description and flock locks conflict between them. The
    lock is released when the holder exits or dies, SIGKILL included, and its fd is
    not inherited by child processes."""
    f = open(os.path.join(run_dir, LOCK_FILE), "a+")
    try:
        start = time.monotonic()
        deadline = start + (LOCK_TIMEOUT if timeout is None else timeout)
        told = False
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                now = time.monotonic()
                if now >= deadline:
                    raise RunLocked(run_dir)
                if not told and now - start >= LOCK_NOTICE_AFTER:
                    sys.stderr.write(LOCK_NOTICE)
                    sys.stderr.flush()
                    told = True
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
    """Renew the lease for the command running now.

    A reentry lease stays a reentry lease (holder, pid and n kept, renewed_at updated)
    when the command is the watch-started agent's own (ENV_REENTRY == the lease's n),
    or when that agent is still alive on this host: any other command, even a human's
    read-only `status`, must never demote a live agent, or watch could start a second
    driver. A session lease is written only when no live reentry agent holds the run."""
    old = read_lease(run_dir) or {}
    if old.get("holder") == "reentry" and (
            str(old.get("n")) == os.environ.get(ENV_REENTRY)
            or (old.get("host") == socket.gethostname() and pid_alive(old.get("pid")))):
        _write(run_dir, dict(old, renewed_at=_stamp(now)))
        return
    _write(run_dir, {"holder": "session", "host": socket.gethostname(), "pid": None,
                     "renewed_at": _stamp(now), "n": old.get("n", 0)})


def set_reentry_lease(run_dir, n, pid, now=None):
    _write(run_dir, {"holder": "reentry", "host": socket.gethostname(), "pid": pid,
                     "renewed_at": _stamp(now), "n": n})


def _as_pid(pid):
    """pid as a positive int, or None: an int (not a bool) or a digit-only string."""
    if isinstance(pid, bool):
        return None
    if isinstance(pid, str) and re.fullmatch(r"[0-9]+", pid):
        pid = int(pid)
    return pid if isinstance(pid, int) and pid > 0 else None


def _exited_child(pid):
    """True when pid is an exited but unreaped child of this process (a zombie).

    Never reaps it where it can avoid that, so the child's owner (a Popen) can still
    read its exit status:
    - os.waitid with WNOWAIT, where Python has it (Linux), peeks without reaping;
    - otherwise (macOS has no os.waitid) `ps -o stat=` shows a zombie as Z;
    - only when neither works does it fall back to waitpid(WNOHANG), which reaps."""
    if hasattr(os, "waitid") and hasattr(os, "WNOWAIT"):
        try:
            return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        except ChildProcessError:
            return False  # not our child: nothing to peek at
        except (OSError, OverflowError):
            pass
    try:
        r = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True,
                           text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().startswith("Z")
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        return os.waitpid(pid, os.WNOHANG) != (0, 0)
    except ChildProcessError:
        return False
    except (OSError, OverflowError):
        return False


def pid_alive(pid):
    """True while process `pid` runs on this host. A zombie child of this process
    counts as dead (and is not reaped, see _exited_child). Anything that is not a
    positive int or a digit-only string is dead: os.kill(0 or -n, 0) would probe a
    process group, and True, 1.5 or 2**40 are not pids."""
    pid = _as_pid(pid)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return False
    return not _exited_child(pid)


def _active_since(path, threshold):
    """True as soon as anything in the worktree at `path` has an mtime at or after
    `threshold` (a timestamp); stops at the first one. A worktree's .git is a file
    naming its gitdir (relative to the worktree, or absolute): `git add` rewrites the
    gitdir's index and a commit appends to its logs/HEAD (HEAD itself, a symref, is
    not rewritten by a commit), so those count as activity too."""
    def fresh(name):
        try:
            return os.stat(name).st_mtime >= threshold
        except OSError:
            return False
    for base, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d != ".git"]
        if fresh(base) or any(fresh(os.path.join(base, n)) for n in files):
            return True
    try:
        with open(os.path.join(path, ".git"), encoding="utf-8") as f:
            gitdir = f.read().split("gitdir:", 1)[1].strip()
    except (OSError, IndexError):
        return False
    gitdir = os.path.join(path, gitdir)
    return fresh(os.path.join(gitdir, "index")) or fresh(os.path.join(gitdir, "logs", "HEAD"))


def _parse_stamp(value):
    try:
        return datetime.strptime(value, _STAMP).replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def lease_live(run_dir, lease, stall_min, worktrees, now=None):
    """True when someone is (or may be) driving the run; when in doubt, True.

    A lease that cannot be read, or whose renewed_at is missing, malformed or more
    than the stall window in the future, is judged by the lease file's own mtime: live
    while it was written within the window."""
    now_ts = (now or datetime.now(timezone.utc)).timestamp()
    window = stall_min * 60

    def written_recently():
        try:
            return now_ts - os.stat(os.path.join(run_dir, LEASE_FILE)).st_mtime < window
        except OSError:
            return False

    if lease is None:
        return False
    if lease.get("corrupt"):
        return written_recently()
    if lease.get("holder") == "reentry":
        return lease.get("host") == socket.gethostname() and pid_alive(lease.get("pid"))
    renewed = _parse_stamp(lease.get("renewed_at"))
    if renewed is None or renewed - now_ts > window:
        return written_recently()
    if now_ts - renewed < window:
        return True
    return any(_active_since(w, now_ts - window) for w in worktrees if os.path.isdir(w))
