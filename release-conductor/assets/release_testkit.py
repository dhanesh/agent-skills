"""release_testkit.py -- shared fixtures for the release-conductor test suites (stdlib
only). Mirrors factory-conductor/assets/conductor_testkit.py's house pattern: a real
git repo built with the identity env GIT, a tmpdir() registry cleaned at atexit, and
small writer helpers so the state tests never hand-build git repos themselves.

- repo(): a temp git repo on `main` holding package.json ({"version": "1.1.0"}),
  committed with GIT, and with .skill-contract/ git-ignored (so a release's local
  state never shows in `git status` and is never committed by accident);
- write_recipe(root, recipe, commit=False): writes .release/recipe.json, optionally
  committing it (for recipe_sha's rev= tests);
- GIT: the git identity env, used because CI has no git identity configured;
- serve(directory): a local http.server in a thread, standing in for a staging target;
- write_evidence(root, sha, feature, verifier, ...): a verify-evidence/v1 record and the
  instance's doctor.json, the shapes verification-skill-forge's recorder writes;
- tmpdir(): tempfile.mkdtemp(), removed (with everything under it) at process exit.
"""
import atexit
import functools
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

_TMPDIRS = []


def tmpdir(**kw):
    """tempfile.mkdtemp(**kw), removed (with everything under it) at process exit."""
    d = tempfile.mkdtemp(**kw)
    _TMPDIRS.append(d)
    return d


@atexit.register
def _remove_tmpdirs():
    while _TMPDIRS:
        shutil.rmtree(_TMPDIRS.pop(), ignore_errors=True)


def repo():
    """A temp git repo on `main`, holding package.json ({"version": "1.1.0"}),
    committed with the GIT identity env. .skill-contract/ is excluded via
    .git/info/exclude, the same mechanism conductor_testkit.repo() uses for
    .skill-contract/envelopes/, so a release's local state never shows in `git
    status` and is never committed by accident."""
    d = tmpdir()
    subprocess.run(["git", "init", "-q", "-b", "main", d], check=True)
    # No background auto-gc or maintenance racing the temp-dir cleanup (the same
    # flake conductor_testkit.repo() guards against).
    for key, value in (("gc.auto", "0"), ("maintenance.auto", "false")):
        subprocess.run(["git", "-C", d, "config", key, value], check=True)
    with open(os.path.join(d, "package.json"), "w", encoding="utf-8") as f:
        json.dump({"version": "1.1.0"}, f)
        f.write("\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-q", "-m", "x"], check=True,
                   env=dict(os.environ, **GIT))
    with open(os.path.join(d, ".git", "info", "exclude"), "a", encoding="utf-8") as f:
        f.write("/.skill-contract/\n")
    return d


def write_recipe(root, recipe, commit=False):
    """Write .release/recipe.json under root; optionally git-add and commit it (for
    recipe_sha's/load_recipe's rev= tests, which read a specific commit's bytes)."""
    d = os.path.join(root, ".release")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "recipe.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(recipe, f, indent=2, sort_keys=True)
        f.write("\n")
    if commit:
        subprocess.run(["git", "-C", root, "add", "-A", ".release"], check=True)
        subprocess.run(["git", "-C", root, "commit", "-q", "-m", "recipe"], check=True,
                       env=dict(os.environ, **GIT))
    return path


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler without the per-request stderr log line."""

    def log_message(self, *args):
        pass


def serve(directory):
    """A local http.server serving `directory` from a daemon thread on 127.0.0.1 (a free
    port), standing in for a staging target: tests never touch a real one. Returns the
    base URL (no trailing slash); the server is shut down at process exit."""
    handler = functools.partial(_QuietHandler, directory=directory)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    @atexit.register
    def _stop():
        httpd.shutdown()
        httpd.server_close()
    return "http://127.0.0.1:%d" % httpd.server_address[1]


def write_evidence(root, sha, feature, verifier, result="pass", instance="i1",
                   doctor_ok=True):
    """A verify-evidence/v1 record for `feature` at `sha` under <root>/.verify, in the
    shape verification-skill-forge/assets/verify_evidence.py's `record` writes (one
    artifact, pinned by sha256), plus the instance's doctor.json at that sha (its
    `doctor` command's shape). Returns the evidence.json path."""
    base = os.path.join(root, ".verify", instance)
    out = os.path.join(base, feature, sha)
    os.makedirs(out, exist_ok=True)
    art = os.path.join(out, "shot.txt")
    with open(art, "w", encoding="utf-8") as f:
        f.write("observed %s at %s\n" % (feature, sha))
    with open(art, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    path = os.path.join(out, "evidence.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"schema": "verify-evidence/v1", "kind": "evidence", "instance": instance,
                   "feature": feature, "sha": sha, "verifier": verifier, "result": result,
                   "action": "a", "observed": "o", "side_effects": ["none: test"],
                   "artifacts": [{"path": "shot.txt", "sha256": digest}],
                   "captured_at": "2026-10-04T00:00:00Z"}, f)
    dpath = os.path.join(base, "doctor", sha, "doctor.json")
    os.makedirs(os.path.dirname(dpath), exist_ok=True)
    with open(dpath, "w", encoding="utf-8") as f:
        json.dump({"schema": "verify-evidence/v1", "kind": "doctor", "instance": instance,
                   "sha": sha, "ok": doctor_ok, "checks": {"process": "pass"},
                   "verifier": verifier, "captured_at": "2026-10-04T00:00:00Z"}, f)
    return path
