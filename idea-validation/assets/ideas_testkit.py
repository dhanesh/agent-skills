"""Test helpers for idea-validation: a temp repo, a command runner, and an I/O tripwire.
Mirrors ops-intake/assets/intake_testkit.py (skills cannot import each other)."""
import atexit
import contextlib
import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ideas as ID  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
TODAY = "2026-10-20"


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


GOOD = fixture("good-brief.md")


def repo(briefs=None):
    """A temp dir. `briefs` maps slug -> brief text, written to ideas/<slug>.md."""
    root = tempfile.mkdtemp(prefix="ideas-test-")
    atexit.register(shutil.rmtree, root, True)
    for slug, text in (briefs or {}).items():
        write_brief(root, slug, text)
    return root


def write_brief(root, slug, text):
    os.makedirs(os.path.join(root, "ideas"), exist_ok=True)
    with open(os.path.join(root, "ideas", slug + ".md"), "w", encoding="utf-8") as f:
        f.write(text)


def brief_file(root, name="b.md", text=GOOD):
    """Write a brief anywhere under root (for lint) and return its path."""
    path = os.path.join(root, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def run(root, *argv, today=TODAY):
    """Call ID.main with --root and --today, capture stdout. Returns (rc, stdout)."""
    buf = io.StringIO()
    pre = ["--root", root] + (["--today", today] if today else [])
    # --today is honoured only under the test clock switch (ideas.TEST_CLOCK_ENV).
    with contextlib.redirect_stdout(buf), mock.patch.dict(os.environ, {ID.TEST_CLOCK_ENV: "1"}):
        rc = ID.main(pre + list(argv))
    return rc, buf.getvalue()


def registered(slug="flat", text=GOOD, by="Dana"):
    """A repo with one registered brief."""
    root = repo({slug: text})
    rc, out = run(root, "register", "ideas/%s.md" % slug, "--by", by)
    assert rc == 0, out
    return root


def record(root, aid, value, kind="experiment", by="Dana", observed="2026-10-15",
           slug="flat", source="interviews", today=TODAY, extra=()):
    return run(root, "record", slug, aid, "--value", str(value), "--kind", kind,
               "--source", source, "--observed", observed, "--by", by, *extra, today=today)


def statuses(out):
    """{assumption id: status} from status output."""
    res = {}
    for line in out.splitlines():
        if line.startswith("ASSUMPTION: "):
            parts = line.split()
            res[parts[1]] = parts[2]
    return res


def verdict(out):
    for line in out.splitlines():
        if line.startswith("VERDICT: "):
            return line.split()[1]
    return None


def tree(root):
    """Every path under root with its size and mtime, to prove a command wrote nothing."""
    snap = {}
    for d, dirs, files in os.walk(root):
        for n in dirs + files:
            p = os.path.join(d, n)
            st = os.lstat(p)
            snap[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns)
    return snap


@contextlib.contextmanager
def no_io():
    """Make any socket, DNS lookup, subprocess, fork or exec fail loudly. Copied from
    ops-intake/assets/intake_testkit.py no_io()."""
    def boom(*a, **k):
        raise AssertionError("ideas.py opened a socket or ran a command")
    targets = [(socket, n) for n in ("socket", "create_connection", "getaddrinfo", "socketpair")]
    targets += [(subprocess, n) for n in ("Popen", "run", "call", "check_call", "check_output")]
    targets += [(os, n) for n in dir(os) if n in ("system", "popen", "fork", "forkpty",
                "posix_spawn", "posix_spawnp") or n.startswith(("exec", "spawn"))]
    with contextlib.ExitStack() as st:
        for mod, name in targets:
            st.enter_context(mock.patch.object(mod, name, boom))
        yield
