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
- tmpdir(): tempfile.mkdtemp(), removed (with everything under it) at process exit.
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile

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
