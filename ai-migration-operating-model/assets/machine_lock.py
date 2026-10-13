#!/usr/bin/env python3
"""machine_lock.py — one machine-wide lock for work that cannot share the VM.

Mutation proofs boot extra servers, and a sharded e2e run fills every lane. Run
together, they starve the container VM, so both take this one lock.

The lock is a kernel file lock (flock) on <lock>/lock. The kernel releases it
when the holding process dies, so a crash can never leave a stale lock behind
and no "break the stale lock" logic (with its race between two waiters) is
needed. Beside it, an `owner` file records pid, host, worker, task and start
time, so a waiter can see who holds the lock and since when. The owner file is
information only; the flock is the lock.

    python3 machine_lock.py run  --lock .workers/judge.lock --worker 3 \\
        --task "prove payments" -- ./prove.sh 3 payments
    python3 machine_lock.py status --lock .workers/judge.lock

`run` waits for the lock, runs the command, releases the lock on any exit and
returns the command's exit status. A lock held longer than --wait-s is a
timeout (exit 75), not a silent hang. The lock directory is never deleted:
deleting a lock file that another waiter has open splits the lock in two.

Use this from shell wrappers instead of a hand-written `until mkdir` loop. The
script is Python, so the interpreter reads the whole file before it runs, and an
edit to this file cannot change a run that has already started. That is not
true of a shell script; see the operational rules in references/pilot-playbook.md.
Local filesystems only (flock is not reliable over network mounts). POSIX only.
Stdlib-only, offline.
"""

import argparse
import datetime
import fcntl
import json
import os
import socket
import subprocess
import sys
import time

EXIT_TIMEOUT = 75
_HELD = {}


def read_owner(lock):
    try:
        with open(os.path.join(lock, "owner"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _open(lock):
    os.makedirs(lock, exist_ok=True)
    return os.open(os.path.join(lock, "lock"), os.O_RDWR | os.O_CREAT, 0o644)


def acquire(lock, worker="", task="", wait_s=6 * 3600, poll_s=3.0, out=sys.stdout):
    """Take the lock. Returns 0 or EXIT_TIMEOUT."""
    fd = _open(lock)
    deadline = time.monotonic() + wait_s
    announced = False
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            pass
        if not announced:
            o = read_owner(lock) or {}
            print("LOCK: waiting for pid %s (worker %s, %s, since %s)"
                  % (o.get("pid"), o.get("worker"), o.get("task"), o.get("since")),
                  file=out, flush=True)
            announced = True
        if time.monotonic() > deadline:
            os.close(fd)
            print("LOCK: timeout after %ds waiting for %s" % (wait_s, lock), file=out)
            return EXIT_TIMEOUT
        time.sleep(poll_s)
    _HELD[os.path.abspath(lock)] = fd
    owner = {"pid": os.getpid(), "host": socket.gethostname(), "worker": str(worker),
             "task": task, "since": datetime.datetime.now(datetime.timezone.utc)
             .strftime("%Y-%m-%dT%H:%M:%SZ")}
    tmp = os.path.join(lock, "owner.%d.tmp" % os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(owner, f)
    os.replace(tmp, os.path.join(lock, "owner"))
    return 0


def release(lock):
    """Release a lock this process holds; a no-op otherwise."""
    fd = _HELD.pop(os.path.abspath(lock), None)
    if fd is None:
        return
    try:
        os.unlink(os.path.join(lock, "owner"))
    except OSError:
        pass
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def is_held(lock):
    """True when another process holds the lock right now."""
    if not os.path.isdir(lock):
        return False
    fd = _open(lock)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)  # closing drops a lock taken here
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="Machine-wide lock with an owner file.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--lock", required=True)
    r.add_argument("--worker", default="")
    r.add_argument("--task", default="")
    r.add_argument("--wait-s", type=int, default=6 * 3600)
    r.add_argument("--poll-s", type=float, default=3.0)
    r.add_argument("command", nargs=argparse.REMAINDER)
    s = sub.add_parser("status")
    s.add_argument("--lock", required=True)
    a = ap.parse_args(argv)

    if a.cmd == "status":
        if not is_held(a.lock):
            print("LOCK: free")
        else:
            o = read_owner(a.lock) or {}
            print("LOCK: held by pid %s on %s (worker %s, %s, since %s)" % (
                o.get("pid"), o.get("host"), o.get("worker"), o.get("task"), o.get("since")))
        return 0

    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not cmd:
        ap.error("run needs a command after --")
    rc = acquire(a.lock, a.worker, a.task, a.wait_s, a.poll_s)
    if rc:
        return rc
    try:
        return subprocess.call(cmd)
    finally:
        release(a.lock)


if __name__ == "__main__":
    sys.exit(main())
