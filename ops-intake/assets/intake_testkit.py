"""Test helpers for ops-intake: a temp repo, a command runner, and an I/O tripwire."""
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intake as IN


def repo(config=None, items=()):
    """A temp dir. Writes the config when given and adds (source, source_id, title) items."""
    root = tempfile.mkdtemp(prefix="intake-test-")
    atexit.register(shutil.rmtree, root, True)
    if config is not None:
        os.makedirs(os.path.join(root, ".intake"))
        with open(os.path.join(root, IN.CONFIG_PATH), "w", encoding="utf-8") as f:
            json.dump(config, f)
    if items:
        q = IN.Queue.load(root)
        for source, source_id, title in items:
            q.add(source, source_id, title)
        q.save()
    return root


def run(root, *argv):
    """Call IN.main with --root and capture stdout. Returns (rc, stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = IN.main(["--root", root, *argv])
    return rc, buf.getvalue()


@contextlib.contextmanager
def no_io():
    """Make any socket or subprocess use fail loudly."""
    def boom(*a, **k):
        raise AssertionError("intake opened a socket or ran a command")
    with mock.patch.object(socket, "socket", boom), \
            mock.patch.object(socket, "create_connection", boom), \
            mock.patch.object(subprocess, "Popen", boom), \
            mock.patch.object(subprocess, "run", boom):
        yield
