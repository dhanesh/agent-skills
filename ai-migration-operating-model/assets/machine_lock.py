#!/usr/bin/env python3
"""machine_lock.py — one machine-wide lock for work that cannot share the VM.

Mutation proofs boot extra servers, and a sharded e2e run fills every lane. Run
together, they starve the container VM, so both take this one lock. The lock is
a directory (mkdir is atomic) holding an `owner` file: pid, host, worker, task,
start time. A waiter can always see who holds the lock and since when.

    python3 machine_lock.py run  --lock .workers/judge.lock --worker 3 \\
        --task "prove payments" -- ./prove.sh 3 payments
    python3 machine_lock.py status --lock .workers/judge.lock

`run` waits for the lock, runs the command, releases the lock on any exit and
returns the command's exit status. A lock whose owner pid is dead on this host
is stale: `run` takes it over and prints LOCK: stale. A lock held longer than
--wait-s is a timeout (exit 75), not a silent hang. Exit 76: the lock is held by
another host, so this host cannot check whether the owner is alive.

Use this from shell wrappers instead of a hand-written `until mkdir` loop. The
script is Python, so the interpreter reads the whole file before it runs, and an
edit to this file cannot change a run that has already started. That is not
true of a shell script; see the operational rules in references/pilot-playbook.md.
Stdlib-only, offline.
"""

import argparse
import datetime
import json
import os
import socket
import subprocess
import sys
import time

EXIT_TIMEOUT = 75
EXIT_FOREIGN = 76


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_owner(lock):
    try:
        with open(os.path.join(lock, "owner"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def acquire(lock, worker="", task="", wait_s=6 * 3600, poll_s=3.0, out=sys.stdout):
    """Take the lock. Returns 0, EXIT_TIMEOUT or EXIT_FOREIGN."""
    parent = os.path.dirname(os.path.abspath(lock))
    os.makedirs(parent, exist_ok=True)
    deadline = time.monotonic() + wait_s
    announced = False
    while True:
        try:
            os.mkdir(lock)
            break
        except FileExistsError:
            pass
        owner = read_owner(lock)
        if owner is None:
            # Created but owner not yet written, or a crash between the two
            # steps. Give the writer a moment before calling it stale.
            time.sleep(min(poll_s, 1.0))
            owner = read_owner(lock)
            if owner is None and os.path.isdir(lock):
                age = time.time() - os.stat(lock).st_mtime
                if age > 60:
                    print("LOCK: stale (no owner file for %ds); taking over" % age, file=out)
                    _release(lock)
                continue
        if owner is not None and owner.get("host") == socket.gethostname() \
                and not _alive(int(owner.get("pid", 0))):
            print("LOCK: stale (owner pid %s dead: %s); taking over"
                  % (owner.get("pid"), owner.get("task")), file=out)
            _release(lock)
            continue
        if owner is not None and owner.get("host") != socket.gethostname():
            print("LOCK: held by host %s (%s); cannot check liveness"
                  % (owner.get("host"), owner.get("task")), file=out)
            return EXIT_FOREIGN
        if not announced and owner is not None:
            print("LOCK: waiting for pid %s (worker %s, %s, since %s)"
                  % (owner.get("pid"), owner.get("worker"), owner.get("task"),
                     owner.get("since")), file=out, flush=True)
            announced = True
        if time.monotonic() > deadline:
            print("LOCK: timeout after %ds waiting for %s" % (wait_s, lock), file=out)
            return EXIT_TIMEOUT
        time.sleep(poll_s)
    owner = {"pid": os.getpid(), "host": socket.gethostname(), "worker": str(worker),
             "task": task, "since": datetime.datetime.now(datetime.timezone.utc)
             .strftime("%Y-%m-%dT%H:%M:%SZ")}
    tmp = os.path.join(lock, "owner.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(owner, f)
    os.replace(tmp, os.path.join(lock, "owner"))
    return 0


def _release(lock):
    for name in ("owner", "owner.tmp"):
        try:
            os.unlink(os.path.join(lock, name))
        except OSError:
            pass
    try:
        os.rmdir(lock)
    except OSError:
        pass


def release(lock):
    """Release only a lock this process owns."""
    owner = read_owner(lock)
    if owner and owner.get("pid") == os.getpid() and owner.get("host") == socket.gethostname():
        _release(lock)


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
        owner = read_owner(a.lock)
        if owner is None:
            print("LOCK: free" if not os.path.isdir(a.lock) else "LOCK: held (no owner file)")
        else:
            here = owner.get("host") == socket.gethostname()
            alive = _alive(int(owner.get("pid", 0))) if here else None
            print("LOCK: held by pid %s on %s (worker %s, %s, since %s)%s" % (
                owner.get("pid"), owner.get("host"), owner.get("worker"), owner.get("task"),
                owner.get("since"), "" if alive is not False else " [stale: pid dead]"))
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
