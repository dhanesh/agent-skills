#!/usr/bin/env python3
"""Outcome eval for growth-review (see the repo's docs/eval-standard.md).

Stdlib-only, offline, deterministic. Every scenario is a temp repo removed at exit; bytecode
is not written, so the eval writes nothing in the repository. "Today" is pinned with
--today 2026-10-12 under the tool's test clock switch, and ops-intake's clock is pinned too.

The promise graded (design spec section 7): a plan goes lint -> register -> import ->
review, and the breach lines from `signals` are accepted by ops-intake's own
intake-signals-jsonl adapter and CLI (the sibling skill in this repository) as one item.
Negatives: a goalpost moved after registration does not hide the breach (+stale, exit 3, the
signal still comes out); an agent-registered plan is refused; a future period is a problem
record; a WARN does not leak as a signal; --today outside the test switch is refused; and
growth.py opens no socket and runs no subprocess. growth.py runs in-process under a recorder
that logs (then refuses) every socket, subprocess, fork and exec; ops-intake runs outside it.

Every NEGATIVE check asserts a side effect, not only an exit code: the stored files, the
printed lines and the ops-intake queue.

Prints one `CHECK: <name> — PASS|FAIL (<detail>)` line per check and a final
`EVAL_RESULT: PASS (n/n checks)` line; exits 0 iff every check passed.
"""
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
import traceback
from unittest import mock

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
OPS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "ops-intake", "assets")
sys.path.insert(0, ASSETS)
import growth as G  # noqa: E402

TODAY = "2026-10-12"
YDAY = "2026-10-11"
_checks = []
CALLS = []

PLAN = """# Metrics plan

| metric | direction | period | warn | breach | min_n | severity |
|---|---|---|---|---|---|---|
| signups | up | day | 100 | 80 | | 3 |
| churn-rate | down | month | 0.05 | 0.08 | 50 | 4 |
"""


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    detail = " | ".join(l for l in str(detail).splitlines() if l)[:300]
    print("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL",
                                " (%s)" % detail if detail else ""))


def arm(name):
    """Run one arm; an exception in its fixture is that check's FAIL."""
    def wrap(fn):
        def go():
            try:
                fn()
            except Exception as e:  # noqa: BLE001 -- any fixture failure is a FAIL
                tb = traceback.format_exception_only(type(e), e)[-1].strip()
                check(name, False, "fixture failed: %s" % tb[:300])
        return go
    return wrap


@contextlib.contextmanager
def recorder():
    """Log, then refuse, every socket, DNS lookup, subprocess, fork or exec."""
    def make(label):
        def boom(*a, **k):
            CALLS.append(label)
            raise AssertionError("growth.py tried %s" % label)
        return boom
    targets = [(socket, n) for n in ("socket", "create_connection", "getaddrinfo", "socketpair")]
    targets += [(subprocess, n) for n in ("Popen", "run", "call", "check_call", "check_output")]
    targets += [(os, n) for n in dir(os) if n in ("system", "popen", "fork", "forkpty",
                "posix_spawn", "posix_spawnp") or n.startswith(("exec", "spawn"))]
    with contextlib.ExitStack() as st:
        for mod, name in targets:
            st.enter_context(mock.patch.object(mod, name, make("%s.%s" % (mod.__name__, name))))
        yield


def growth(root, *argv, clock=True):
    """growth.py in-process under the recorder. Returns (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    env = dict(os.environ)
    env.pop(G.TEST_CLOCK_ENV, None)
    if clock:
        env[G.TEST_CLOCK_ENV] = "1"
    with recorder(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch.dict(os.environ, env, clear=True):
        rc = G.main(["--root", root, "--today", TODAY, *argv])
    return rc, out.getvalue(), err.getvalue()


def tmp():
    d = tempfile.mkdtemp(prefix="growth-eval-")
    atexit.register(shutil.rmtree, d, True)
    return d


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def data(root, *recs):
    return write(root, "export.jsonl", "".join(
        (r if isinstance(r, str) else json.dumps(r)) + "\n" for r in recs))


def rec(metric, period, value, n=None):
    r = {"metric": metric, "period": period, "value": value, "source": "export"}
    if n is not None:
        r["n"] = n
    return r


def state_files(root):
    d = os.path.join(root, ".skill-contract", "growth")
    out = {}
    for name in ("plan.json", "data.jsonl"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            with open(p, "rb") as f:
                out[name] = f.read()
    return out


def registered(*recs):
    root = tmp()
    write(root, "growth/metrics.md", PLAN)
    rc, out, _ = growth(root, "register", "--by", "Dana")
    assert rc == 0, out
    if recs:
        rc, out, _ = growth(root, "import", data(root, *recs))
        assert rc == 0, out
    return root


def ops_intake():
    if OPS not in sys.path:
        sys.path.insert(0, OPS)
    import adapters as AD  # noqa: E402
    import intake as IN  # noqa: E402
    return IN, AD


def intake_import(lines, now="2026-10-12T06:00:00Z"):
    """Feed lines to ops-intake's CLI in a fresh root. Returns (rc, stdout, items)."""
    IN, _ = ops_intake()
    iroot = tmp()
    write(iroot, ".intake/config.json", json.dumps(
        {"sources": {"growth": {"enabled": True, "formats": ["intake-signals-jsonl"]}}}))
    path = write(iroot, "signals.jsonl", lines)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), mock.patch.object(IN, "_now", lambda: now):
        rc = IN.main(["--root", iroot, "import", "--format", "intake-signals-jsonl",
                      "--source", "growth", path])
    return rc, buf.getvalue(), IN.Queue.load(iroot).items


@arm("the recorder catches a socket attempt")
def recorder_arm():
    n = len(CALLS)
    try:
        with recorder():
            socket.socket()
    except AssertionError:
        pass
    check("the recorder catches a socket attempt", len(CALLS) == n + 1, str(CALLS[n:]))
    del CALLS[n:]


@arm("positive flow: plan -> register -> import -> review -> signals ops-intake accepts")
def positive_arm():
    root = tmp()
    write(root, "growth/metrics.md", PLAN)
    lint = growth(root, "lint")
    reg = growth(root, "register", "--by", "Dana")
    imp = growth(root, "import", data(root, rec("signups", "2026-10-10", 70),
                                      rec("signups", YDAY, 75),
                                      rec("churn-rate", "2026-09", 0.03, n=80)))
    rev = growth(root, "review")
    sig = growth(root, "signals", "--source", "growth")
    check("lint, register and import succeed",
          lint[0] == 0 and reg[0] == 0 and imp[0] == 0
          and "IMPORT: added 3 corrected 0 unchanged 0 problems 0" in imp[1],
          "rc=%s/%s/%s" % (lint[0], reg[0], imp[0]))
    check("review reports the breach and the OK metric and exits 3",
          rev[0] == 3 and "METRIC: signups BREACH value=75 period=2026-10-11" in rev[1]
          and "METRIC: churn-rate OK value=0.03 period=2026-09" in rev[1]
          and "REVIEW: ok=1 warn=0 breach=1 no_data=0 stale=no" in rev[1], rev[1][:300])
    lines = sig[1].splitlines()
    one = json.loads(lines[0]) if len(lines) == 1 else {}
    check("signals prints one line with the streak, the plan severity and one evidence key",
          sig[0] == 0 and len(lines) == 1 and one.get("first_seen") == "2026-10-11T00:00:00Z"
          and one.get("last_seen") == "2026-10-12T00:00:00Z" and one.get("severity") == 3
          and [e["key"] for e in one.get("evidence", [])] == ["signups:2026-10-11"],
          sig[1][:300])
    _, AD = ops_intake()
    sigs, problems = AD.ADAPTERS["intake-signals-jsonl"](
        sig[1], {"source": "growth", "now": "2026-10-12T06:00:00Z"})
    rc, out, items = intake_import(sig[1])
    item = list(items.values())[0] if len(items) == 1 else {}
    check("ops-intake's own adapter and CLI accept the line as one low-trust issue item",
          problems == [] and len(sigs) == 1 and rc == 0 and len(items) == 1
          and item.get("kind") == "issue" and item.get("trust") == "low"
          and item.get("count") == 1, "problems=%s rc=%s out=%r" % (problems, rc, out[:200]))


@arm("positive: 25 daily breaches, reviewed daily plus a re-run, count 25 in ops-intake")
def count_arm():
    IN, _ = ops_intake()
    root = registered()
    iroot = tmp()
    write(iroot, ".intake/config.json", json.dumps(
        {"sources": {"growth": {"enabled": True, "formats": ["intake-signals-jsonl"]}}}))
    import datetime
    start = datetime.date(2026, 9, 1)
    rcs = []
    for i in range(25):
        period = (start + datetime.timedelta(days=i)).isoformat()
        today = (start + datetime.timedelta(days=i + 1)).isoformat()
        out, err = io.StringIO(), io.StringIO()
        with recorder(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.dict(os.environ, {G.TEST_CLOCK_ENV: "1"}):
            rcs.append(G.main(["--root", root, "--today", today, "import",
                               data(root, rec("signups", period, 60))]))
        for k in range(2 if i == 24 else 1):
            out = io.StringIO()
            with recorder(), contextlib.redirect_stdout(out), \
                    mock.patch.dict(os.environ, {G.TEST_CLOCK_ENV: "1"}):
                rcs.append(G.main(["--root", root, "--today", today, "signals", "--source",
                                   "growth"]))
            path = write(iroot, "s.jsonl", out.getvalue())
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(IN, "_now", lambda t=today, k=k: "%sT0%d:00:00Z" % (t, k)):
                rcs.append(IN.main(["--root", iroot, "import", "--format",
                                    "intake-signals-jsonl", "--source", "growth", path]))
    items = IN.Queue.load(iroot).items
    item = list(items.values())[0] if len(items) == 1 else {}
    check("25 breached days give one ops-intake item with count 25 (evidence capped at 20)",
          set(rcs) == {0} and len(items) == 1 and item.get("count") == 25
          and len(item.get("evidence", [])) == 20,
          "rcs=%s count=%s" % (sorted(set(rcs)), item.get("count")))


@arm("NEGATIVE: a goalpost moved after registration still alarms")
def stale_arm():
    root = registered(rec("signups", YDAY, 70))
    write(root, "growth/metrics.md", PLAN.replace("| 100 | 80 |", "| 100 | 50 |"))
    rev = growth(root, "review")
    sig = growth(root, "signals", "--source", "growth")
    check("NEGATIVE: an edit that would hide the breach gives BREACH +stale, stale=yes, exit 3, "
          "and signals still prints the line",
          rev[0] == 3 and "METRIC: signups BREACH +stale value=70" in rev[1]
          and "stale=yes" in rev[1] and "NOTE: stale signups edited" in rev[1]
          and sig[0] == 0 and len(sig[1].splitlines()) == 1, rev[1][:300])
    before = state_files(root)
    re1 = growth(root, "register", "--by", "Dana")
    check("NEGATIVE: registering the edit again without --reason is refused and changes nothing",
          re1[0] == 2 and "needs --reason" in re1[1] and state_files(root) == before,
          re1[1][:200])


@arm("NEGATIVE: an agent-registered plan is refused")
def agent_arm():
    root = tmp()
    write(root, "growth/metrics.md", PLAN)
    rc, out, _ = growth(root, "register", "--by", "agent:claude")
    rc2, out2, _ = growth(root, "review")
    check("NEGATIVE: register --by agent:claude exits 2, stores no plan, and review has "
          "nothing to judge",
          rc == 2 and "STOP: an agent: name cannot register" in out
          and state_files(root) == {} and rc2 == 2 and "no registered plan" in out2,
          out[:200])


@arm("NEGATIVE: a future period is a problem record")
def future_arm():
    root = registered(rec("signups", YDAY, 120))
    before = state_files(root)
    rc, out, _ = growth(root, "import", data(root, rec("signups", "2026-10-14", 10),
                                             rec("churn-rate", "2026-10", 0.5, n=90)))
    rev = growth(root, "review")
    check("NEGATIVE: periods ending more than 1 day after today are PROBLEM lines, nothing is "
          "stored, and the review is unchanged",
          rc == 3 and out.count("period ends more than 1 day after today") == 2
          and "problems 2" in out and state_files(root) == before
          and "METRIC: signups OK value=120" in rev[1], out[:300])


@arm("NEGATIVE: a WARN does not leak as a signal")
def warn_arm():
    root = registered(rec("signups", YDAY, 90), rec("churn-rate", "2026-09", 0.06, n=99))
    rev = growth(root, "review")
    sig = growth(root, "signals", "--source", "growth")
    rc, _, items = intake_import(sig[1])
    check("NEGATIVE: two WARN metrics give no signal line and no ops-intake item; review exits 0",
          rev[0] == 0 and "REVIEW: ok=0 warn=2 breach=0" in rev[1] and sig == (0, "", "")
          and items == {}, "%r %r" % (rev[1][:200], sig))


@arm("NEGATIVE: --today outside the test switch is refused")
def today_arm():
    root = tmp()
    write(root, "growth/metrics.md", PLAN)
    rc, out, _ = growth(root, "register", "--by", "Dana", clock=False)
    check("NEGATIVE: without the test switch, --today exits 2, prints STOP: and writes nothing",
          rc == 2 and "STOP: --today is for tests only" in out
          and not os.path.exists(os.path.join(root, ".skill-contract")), out[:200])


@arm("NEGATIVE: growth.py opened no socket and ran no subprocess")
def no_io_arm():
    check("NEGATIVE: across every growth.py call in this eval, the tool opened no socket and "
          "ran no subprocess", CALLS == [], str(CALLS[:5]))


def main():
    if not os.path.isfile(os.path.join(OPS, "adapters.py")):
        check("ops-intake is beside growth-review", False, OPS)
    else:
        for fn in (recorder_arm, positive_arm, count_arm, stale_arm, agent_arm, future_arm,
                   warn_arm, today_arm, no_io_arm):
            fn()
    n, k = len(_checks), sum(_checks)
    print("EVAL_RESULT: %s (%d/%d checks)" % ("PASS" if k == n else "FAIL", k, n))
    return 0 if k == n else 1


if __name__ == "__main__":
    sys.exit(main())
