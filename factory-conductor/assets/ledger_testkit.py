"""Test helpers for the money ledger: a temp repo with a budget, a runner with the test clock,
and an I/O tripwire. Used by test_ledger*.py and eval/run_eval.py."""
import atexit
import contextlib
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
import ledger as L  # noqa: E402

TODAY = "2026-10-12"   # current month 2026-10

BUDGET = """# Budget

Prose here can change without making the budget stale.

currency: USD

| category | monthly_cap |
|---|---|
| hosting | 25 |
| ads | 100.50 |
| domains | 30 |
| total | 120 |
"""

# Words that read as approval; no check output may carry one (case-insensitive, as words).
APPROVAL_TOKEN_RE = r"(?i)\b(ok|approved|pass|allowed)\b"


def repo(budget=BUDGET):
    root = tempfile.mkdtemp(prefix="ledger-test-")
    atexit.register(shutil.rmtree, root, True)
    if budget is not None:
        write_budget(root, budget)
    return root


def write_budget(root, text):
    os.makedirs(os.path.join(root, "budget"), exist_ok=True)
    with open(os.path.join(root, "budget", "budget.md"), "w", encoding="utf-8") as f:
        f.write(text)


def run(root, *argv, today=TODAY):
    """ledger.main in-process under the test clock switch. Returns (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    pre = ["--root", root] + (["--today", today] if today else [])
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch.dict(os.environ, {L.TEST_CLOCK_ENV: "1"}):
        rc = L.main(pre + list(argv))
    return rc, out.getvalue(), err.getvalue()


def registered(budget=BUDGET, by="Dana"):
    root = repo(budget)
    rc, out, _ = run(root, "register", "--by", by)
    assert rc == 0, out
    return root


def check(root, pid, category, amount, *extra, currency="USD", what="a spend", today=TODAY):
    return run(root, "check", "--id", pid, "--category", category, "--amount", amount,
               "--currency", currency, "--what", what, *extra, today=today)


def ldir(root):
    return os.path.join(root, *L.LEDGER_DIR.split("/"))


def state(root):
    with open(os.path.join(ldir(root), L.STATE_FILE), encoding="utf-8") as f:
        return json.load(f)


def write_state(root, st):
    with open(os.path.join(ldir(root), L.STATE_FILE), "w", encoding="utf-8") as f:
        json.dump(st, f)


def log_lines(root):
    path = os.path.join(ldir(root), L.LOG_FILE)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


@contextlib.contextmanager
def no_io():
    """Make any socket, DNS lookup, subprocess, fork or exec fail loudly."""
    def boom(*a, **k):
        raise AssertionError("ledger.py opened a socket or ran a command")
    targets = [(socket, n) for n in ("socket", "create_connection", "getaddrinfo", "socketpair")]
    targets += [(subprocess, n) for n in ("Popen", "run", "call", "check_call", "check_output")]
    targets += [(os, n) for n in dir(os) if n in ("system", "popen", "fork", "forkpty",
                "posix_spawn", "posix_spawnp") or n.startswith(("exec", "spawn"))]
    with contextlib.ExitStack() as st:
        for mod, name in targets:
            st.enter_context(mock.patch.object(mod, name, boom))
        yield
