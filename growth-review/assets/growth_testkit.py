"""Test helpers for growth-review: a temp repo with a plan, a runner with the test clock,
an I/O tripwire, and ops-intake (the sibling skill on main) loaded from its directory."""
import atexit
import contextlib
import datetime
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import growth as G  # noqa: E402

OPS_ASSETS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "ops-intake", "assets")

TODAY = "2026-10-12"  # a Monday: ISO week 2026-W42 starts today

PLAN = """# Metrics plan

Prose here can change without making the plan stale.

| metric | direction | period | warn | breach | min_n | severity |
|---|---|---|---|---|---|---|
| signups | up | day | 100 | 80 | | 3 |
| churn-rate | down | month | 0.05 | 0.08 | 50 | 4 |
| activation | up | week | 0.4 | 0.3 | | |
"""


def repo(plan=PLAN):
    root = tempfile.mkdtemp(prefix="growth-test-")
    atexit.register(shutil.rmtree, root, True)
    if plan is not None:
        write_plan(root, plan)
    return root


def write_plan(root, text):
    os.makedirs(os.path.join(root, "growth"), exist_ok=True)
    with open(os.path.join(root, "growth", "metrics.md"), "w", encoding="utf-8") as f:
        f.write(text)


def run(root, *argv, today=TODAY):
    """growth.main in-process with --root and --today under the test clock switch.
    Returns (rc, stdout). stderr is folded in only by run_err()."""
    buf = io.StringIO()
    pre = ["--root", root] + (["--today", today] if today else [])
    with contextlib.redirect_stdout(buf), mock.patch.dict(os.environ, {G.TEST_CLOCK_ENV: "1"}):
        rc = G.main(pre + list(argv))
    return rc, buf.getvalue()


def run_err(root, *argv, today=TODAY):
    """Like run() but returns (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    pre = ["--root", root] + (["--today", today] if today else [])
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch.dict(os.environ, {G.TEST_CLOCK_ENV: "1"}):
        rc = G.main(pre + list(argv))
    return rc, out.getvalue(), err.getvalue()


def records(root, recs, name="data.jsonl"):
    """Write records as JSON lines (dicts) or raw strings; returns the path."""
    path = os.path.join(root, name)
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
    return path


def rec(metric, period, value, n=None, source="export"):
    r = {"metric": metric, "period": period, "value": value, "source": source}
    if n is not None:
        r["n"] = n
    return r


def registered(plan=PLAN, by="Dana"):
    root = repo(plan)
    rc, out = run(root, "register", "--by", by)
    assert rc == 0, out
    return root


def load(root, *recs, today=TODAY):
    rc, out = run(root, "import", records(root, recs), today=today)
    return rc, out


def day(s, delta):
    return (datetime.date.fromisoformat(s) + datetime.timedelta(days=delta)).isoformat()


def data_lines(root):
    path = os.path.join(root, *G.GROWTH_DIR.split("/"), G.DATA_FILE)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


@contextlib.contextmanager
def no_io():
    """Make any socket, DNS lookup, subprocess, fork or exec fail loudly."""
    def boom(*a, **k):
        raise AssertionError("growth.py opened a socket or ran a command")
    targets = [(socket, n) for n in ("socket", "create_connection", "getaddrinfo", "socketpair")]
    targets += [(subprocess, n) for n in ("Popen", "run", "call", "check_call", "check_output")]
    targets += [(os, n) for n in dir(os) if n in ("system", "popen", "fork", "forkpty",
                "posix_spawn", "posix_spawnp") or n.startswith(("exec", "spawn"))]
    with contextlib.ExitStack() as st:
        for mod, name in targets:
            st.enter_context(mock.patch.object(mod, name, boom))
        yield


# -- ops-intake, the sibling skill -------------------------------------------------
def ops_intake():
    """(intake module, adapters module) from ../../ops-intake/assets."""
    if OPS_ASSETS not in sys.path:
        sys.path.insert(0, OPS_ASSETS)
    import adapters as AD  # noqa: E402
    import intake as IN  # noqa: E402
    return IN, AD


def intake_repo(source="growth"):
    """A temp ops-intake root whose config has one intake-signals-jsonl source."""
    root = tempfile.mkdtemp(prefix="growth-intake-")
    atexit.register(shutil.rmtree, root, True)
    os.makedirs(os.path.join(root, ".intake"))
    with open(os.path.join(root, ".intake", "config.json"), "w", encoding="utf-8") as f:
        json.dump({"sources": {source: {"enabled": True,
                                        "formats": ["intake-signals-jsonl"]}}}, f)
    return root


def intake_run(iroot, *argv, now):
    """ops-intake's own CLI in-process, its clock pinned to `now` (UTC RFC 3339)."""
    IN, _ = ops_intake()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), mock.patch.object(IN, "_now", lambda: now):
        rc = IN.main(["--root", iroot, *argv])
    return rc, buf.getvalue()


def pipe(groot, iroot, today, now, source="growth"):
    """growth signals | intake import, as the agent runs it. Returns (signals text,
    import rc, import stdout)."""
    rc, out, err = run_err(groot, "signals", "--source", source, today=today)
    assert rc == 0, (out, err)
    path = os.path.join(iroot, "signals.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    irc, iout = intake_run(iroot, "import", "--format", "intake-signals-jsonl", "--source",
                           source, path, now=now)
    return out, irc, iout


def intake_items(iroot):
    IN, _ = ops_intake()
    return IN.Queue.load(iroot).items
