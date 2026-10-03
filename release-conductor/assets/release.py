#!/usr/bin/env python3
"""release.py -- release-conductor's state tool foundations (roadmap step 5A, spec
docs/superpowers/specs/2026-10-03-release-conductor-design.md).

This module carries only the pieces later `init/prep/stage/deploy/verify-prod/rollback`
commands are built on:

  - the release recipe (.release/recipe.json, spec section 1), loaded and validated
    from the working tree or from a git commit (`load_recipe`, `recipe_sha`);
  - argv expansion for recipe commands (`expand`);
  - a process-group-safe command runner (`run_cmd`);
  - a repo-wide run lock (`run_lock`, `Locked`), so two release commands never
    interleave writes;
  - the release state file and its append-only log (`Release`).

A release's local, git-ignored state lives at
<root>/.skill-contract/releases/<version>/ -- state.json (rewritten atomically) and
release-log.jsonl (append-only). The lock file sits one level up, at
.skill-contract/releases/.lock, so it is exclusive across the whole repo, not one
release's directory (spec section 3: "One release at a time per repo").

Stdlib only, Python >= 3.10, POSIX (fcntl, process groups).
"""
import sys

if sys.version_info < (3, 10):
    sys.stderr.write("release.py needs Python >= 3.10, found %d.%d\n" % sys.version_info[:2])
    sys.exit(2)

import contextlib  # noqa: E402
import datetime as _dt  # noqa: E402
import fcntl  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import contract_check as CC  # noqa: E402  (the vendored skill-contract checker, same dir)

RELEASES_DIR = ".skill-contract/releases"
RECIPE_PATH = ".release/recipe.json"

STATES = ("prepped", "staging_verify", "staged", "stage_failed", "awaiting_deploy",
          "deploying", "deployed", "verified", "prod_failed", "rolling_back",
          "rolled_back", "outcome_unknown")

# A release's fields besides `status` (its own, first, argument) that set_status may
# write; base_commit is fixed at Release.new() and never passed to set_status.
RELEASE_FIELDS = ("release_commit", "recipe_sha", "artifact_sha", "rollback_target",
                  "approved_by", "evidence")

TAIL = 2000  # a run_cmd output tail, the same size conductor.py's _tail uses


# ── The run lock ─────────────────────────────────────────────────────────────────
# Copied from factory-conductor/assets/reentry.py's run_lock (R2): same semantics (not
# re-entrant -- nested use in one process deadlocks until the timeout, because each
# call opens a new file description and flock locks conflict between them; released
# when the holder exits or dies, SIGKILL included; the fd is not inherited by child
# processes), scoped here to the whole repo (one release at a time) rather than to a
# single run's directory, and raising this module's own `Locked` rather than
# reentry.RunLocked, so release-conductor stays self-contained.
LOCK_FILE = ".lock"
LOCK_TIMEOUT = 900  # read at call time (a bare name lookup), so tests can shorten it
LOCK_NOTICE_AFTER = 2.0  # seconds of waiting before run_lock says why it is waiting
LOCK_NOTICE = "waiting for the release run lock held by another release command…\n"


class Locked(Exception):
    """Raised by run_lock() when the lock is still held after `timeout` seconds."""


def _releases_dir(root):
    """<root>/.skill-contract/releases, as an absolute-friendly join (root may be
    relative; callers that care pass os.path.abspath(root))."""
    return os.path.join(root, *RELEASES_DIR.split("/"))


def _ensure_releases_dir(root):
    """Create <root>/.skill-contract/releases, with a `*` .gitignore inside it, if
    missing. Returns its path. Copies factory-conductor/assets/conductor.py's
    `_ignore_runs_dir` pattern, so release state never dirties the repository even
    where the project's own .gitignore does not list it."""
    d = _releases_dir(root)
    os.makedirs(d, exist_ok=True)
    gi = os.path.join(d, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8") as f:
            f.write("# written by release-conductor: release state is local, never committed\n*\n")
    return d


@contextlib.contextmanager
def run_lock(root, timeout=None):
    """Hold an exclusive flock on <root>/.skill-contract/releases/.lock, so two
    release commands for this repo never interleave writes to a release's state.json
    or its log. Raises Locked after `timeout` seconds (this module's LOCK_TIMEOUT by
    default, read at call time so tests can shorten it). After LOCK_NOTICE_AFTER
    seconds of waiting it says so, once, on stderr. See the module-level comment
    above for the full cross-reference to reentry.run_lock."""
    d = _ensure_releases_dir(root)
    f = open(os.path.join(d, LOCK_FILE), "a+")
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
                    raise Locked(root)
                if not told and now - start >= LOCK_NOTICE_AFTER:
                    sys.stderr.write(LOCK_NOTICE)
                    sys.stderr.flush()
                    told = True
                time.sleep(0.1)
        yield
    finally:
        f.close()


# ── The recipe (spec section 1) ──────────────────────────────────────────────────
# Tokens a recipe argv may carry, written as CC.argv_problems expects them: whole
# brace-delimited elements, the same convention reentry.reentry_problems uses for
# {prompt}/{root} (factory-conductor/assets/reentry.py).
RECIPE_TOKENS = {"{version}", "{commit}", "{env}"}
BUMP_LEVELS = ("patch", "minor", "major")
DEFAULT_DEPLOY_TIMEOUT = 600


def _neutralize_tokens(s, tokens):
    """s with every occurrence of each token in `tokens` replaced by a brace-free
    placeholder. Used so an allowed token embedded in a larger string -- the recipe's
    version_probe may read ["cat", "probe-{env}.txt"] -- does not trip
    CC.argv_problems' "must be a whole element" rule, which exists for reentry's
    {prompt}/{root} where embedding would be a bug, not a feature."""
    for t in tokens:
        s = s.replace(t, "X")
    return s


def _argv_problems(argv, tokens=RECIPE_TOKENS):
    """CC.argv_problems(argv, tokens) (Task 1's shared shell/launcher/token refusals;
    never duplicated here), after neutralizing any occurrence -- whole-element or
    embedded -- of an allowed token. A recipe command may embed {version}, {commit}
    or {env} anywhere in an argument (version_probe's literal filename, above); only
    the shell/launcher refusals and any OTHER, unrecognised {token} should still be
    caught, and CC.argv_problems does that once the allowed ones are out of the way."""
    if not (isinstance(argv, list) and argv and all(isinstance(a, str) and a for a in argv)):
        return CC.argv_problems(argv, tokens)
    neutral = [_neutralize_tokens(a, tokens) for a in argv]
    return CC.argv_problems(neutral, tokens)


def _argv_list_field(recipe, key, problems):
    """Append problems for `key`, which must be a non-empty list of argv lists
    (staging_checks, prod_smoke): each a command never run against anything but the
    environment its name says, per spec section 1."""
    v = recipe.get(key)
    if not isinstance(v, list) or not v:
        problems.append("%s must be a non-empty list of argv lists" % key)
        return
    for i, item in enumerate(v):
        for p in _argv_problems(item):
            problems.append("%s[%d] %s" % (key, i, p))


def _validate_version_field(recipe, problems):
    """version must be {"file": <str>, "key": <str>} or {"cmd": <argv list>}."""
    version = recipe.get("version")
    if not isinstance(version, dict):
        problems.append('version must be {"file", "key"} or {"cmd": [...]}')
        return
    if "cmd" in version:
        for p in _argv_problems(version.get("cmd")):
            problems.append("version.cmd %s" % p)
        return
    if {"file", "key"} <= set(version):
        if not (isinstance(version.get("file"), str) and version["file"]):
            problems.append("version.file must be a non-empty string")
        if not (isinstance(version.get("key"), str) and version["key"]):
            problems.append("version.key must be a non-empty string")
        return
    problems.append('version must be {"file", "key"} or {"cmd": [...]}')


def _validate_recipe(recipe):
    """[] when `recipe` is a well-formed release recipe (spec section 1); else the
    list of problems. Every command argv goes through _argv_problems, so a shell
    string, a shell/launcher bypass, or an unknown {token} is refused exactly as
    reentry.agent_cmd refuses them (Task 1's CC.argv_problems, shared, not
    duplicated)."""
    if not isinstance(recipe, dict):
        return ["recipe must be a JSON object"]
    problems = []
    for key in ("build", "deploy_staging", "deploy_prod", "rollback", "health",
                "version_probe"):
        for p in _argv_problems(recipe.get(key)):
            problems.append("%s %s" % (key, p))
    for key in ("staging_checks", "prod_smoke"):
        _argv_list_field(recipe, key, problems)
    _validate_version_field(recipe, problems)
    if recipe.get("bump") not in BUMP_LEVELS:
        problems.append("bump must be one of %s" % ", ".join(BUMP_LEVELS))
    artifact = recipe.get("artifact")
    if artifact != "rebuild" and not (isinstance(artifact, dict)
                                      and isinstance(artifact.get("path"), str)
                                      and artifact["path"]):
        problems.append('artifact must be "rebuild" or {"path": <non-empty string>}')
    timeout = recipe.get("deploy_timeout", DEFAULT_DEPLOY_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        problems.append("deploy_timeout must be a positive integer (seconds)")
    if not (isinstance(recipe.get("verify_skill"), str) and recipe["verify_skill"]):
        problems.append("verify_skill must be a non-empty string")
    return problems


def _recipe_bytes(root, rev=None):
    """The exact bytes of .release/recipe.json: the working file, or, when `rev` is
    given, the stdout of `git show <rev>:.release/recipe.json`. Raises OSError on an
    unreadable working file, or ValueError when git or the show fails."""
    if rev is None:
        with open(os.path.join(root, *RECIPE_PATH.split("/")), "rb") as f:
            return f.read()
    try:
        r = subprocess.run(["git", "-C", root, "show", "%s:%s" % (rev, RECIPE_PATH)],
                           capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        raise ValueError("git show %s:%s failed: %s" % (rev, RECIPE_PATH, e)) from e
    if r.returncode != 0:
        raise ValueError("git show %s:%s failed: %s"
                         % (rev, RECIPE_PATH, r.stderr.decode("utf-8", "replace").strip()))
    return r.stdout


def recipe_sha(root, rev=None):
    """sha256 hex digest of .release/recipe.json's exact bytes: the working copy, or
    (with `rev`) `git show <rev>:.release/recipe.json`'s stdout. An unchanged
    committed recipe therefore hashes to exactly CC.sha256_file of the working file
    (recipe_sha(root, rev=<that commit>) == CC.sha256_file(<path to the working
    file>)), which is what lets `stage` notice a recipe edited since it was staged."""
    return hashlib.sha256(_recipe_bytes(root, rev)).hexdigest()


def load_recipe(root, rev=None):
    """(recipe, problems) for .release/recipe.json: the working tree, or, with `rev`,
    `git show <rev>:.release/recipe.json`. `recipe` is the parsed JSON, or None when
    the file cannot be read or is not valid JSON (then `problems` names why).
    Otherwise `problems` is _validate_recipe(recipe): [] exactly when the recipe is a
    well-formed release recipe (spec section 1)."""
    try:
        raw = _recipe_bytes(root, rev)
    except (OSError, ValueError) as e:
        return None, ["cannot read the recipe: %s" % e]
    try:
        recipe = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        return None, ["recipe is not valid JSON: %s" % e]
    return recipe, _validate_recipe(recipe)


def expand(argv, values):
    """argv with every occurrence of {version}, {commit} or {env} -- a whole element
    or embedded in a larger string -- replaced by str(values["version"]),
    str(values["commit"]) or str(values["env"]) (only the ones `values` actually
    carries; any other key in `values`, or any other {token}, is left untouched).

    This must accept the same embedded placement load_recipe's validation does
    (_argv_problems/_neutralize_tokens, above): version_probe may read
    ["cat", "probe-{env}.txt"], and expand has to be able to fill that in, or a
    later `verify-prod` would literally run `cat probe-{env}.txt`. Embedded
    substitution is safe here because the argv never passes through a shell and the
    substituted values are tool-controlled (a semver string, a hex commit, or
    "staging"/"prod"), never attacker-controlled shell metacharacters that would
    matter without a shell anyway."""
    out = []
    for a in argv:
        for k in ("version", "commit", "env"):
            if k in values:
                a = a.replace("{%s}" % k, str(values[k]))
        out.append(a)
    return out


# ── The process-group-safe command runner ────────────────────────────────────────
def _kill_group(p):
    """SIGKILL the command's whole process group. Idempotent; misses a child that
    called setsid() and so left the group. Copied from factory-conductor's
    assets/conductor.py `_kill_group`."""
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (OSError, AttributeError):  # group already empty, or no process groups (Windows)
        try:
            p.kill()
        except OSError:
            pass


def run_cmd(argv, cwd, timeout):
    """Run argv (never a shell, no stdin) in cwd, in its own session and process
    group, and kill that whole group when the command ends or times out -- so a
    detached grandchild cannot outlive the result. Copies factory-conductor's
    assets/conductor.py `_run_verify`/`_kill_group` pattern.

    Returns {"rc", "out_tail", "err_tail", "timed_out"}: rc is the exit code, or None
    on a timeout or a failure to start; out_tail/err_tail are the last TAIL
    characters of stdout/stderr (kept local, never logged to a release record --
    deploy output can carry secrets, spec section 3)."""
    try:
        p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, errors="replace",
                             start_new_session=True)
    except OSError as e:
        return {"rc": None, "out_tail": "", "err_tail": "cannot run: %s" % e, "timed_out": False}
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(p)
        try:
            out, err = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        return {"rc": None, "out_tail": (out or "")[-TAIL:], "err_tail": (err or "")[-TAIL:],
                "timed_out": True}
    # Kill the group after every command, not only on timeout: a child that detached
    # its stdio would otherwise outlive a passing command (unless it setsid()s away).
    _kill_group(p)
    return {"rc": p.returncode, "out_tail": (out or "")[-TAIL:], "err_tail": (err or "")[-TAIL:],
            "timed_out": False}


# ── The release state file and its append-only log ───────────────────────────────
def _now():
    """The current time, timezone-aware UTC."""
    return _dt.datetime.now(_dt.timezone.utc)


def _rfc3339(when):
    """`when` as an RFC 3339 UTC timestamp (YYYY-MM-DDThh:mm:ssZ)."""
    return when.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fsync_dir(path):
    """Make a rename in `path` durable. Best effort where directories cannot be
    opened. Copied from factory-conductor's assets/conductor.py `_fsync_dir`."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class Release:
    """One release's local state, at
    <root>/.skill-contract/releases/<version>/{state.json, release-log.jsonl}.

    `save()` rewrites state.json atomically (a temp file in the same directory, then
    os.replace, then fsync the directory) and `log()` only appends one JSON line per
    event -- both copying factory-conductor's assets/conductor.py State.save/State.log
    pattern. Neither method is called implicitly by the other: `new()` builds the
    in-memory release and its directory but writes nothing until `save()` is called,
    so a caller controls exactly when a status change becomes durable."""

    def __init__(self, root, version, data):
        """Build a Release from decoded state (or from the single `base_commit` field
        `new()` passes); never called directly by other code -- use `new` or `load`."""
        self.root = root
        self.version = version
        self.base_commit = data["base_commit"]
        self.status = data.get("status")
        self.release_commit = data.get("release_commit")
        self.recipe_sha = data.get("recipe_sha")
        self.artifact_sha = data.get("artifact_sha")
        self.rollback_target = data.get("rollback_target")
        self.approved_by = data.get("approved_by")
        self.evidence = data.get("evidence")

    @property
    def dir(self):
        """This release's directory: <root>/.skill-contract/releases/<version>."""
        return os.path.join(_releases_dir(self.root), self.version)

    @property
    def state_path(self):
        """This release's state.json path."""
        return os.path.join(self.dir, "state.json")

    @property
    def log_path(self):
        """This release's release-log.jsonl path."""
        return os.path.join(self.dir, "release-log.jsonl")

    @classmethod
    def new(cls, root, version, base_commit):
        """A fresh in-memory Release for `version`, pinned to `base_commit` (the
        default-branch commit release prep worked from). Creates the release's
        directory (and its releases/ parent, with a `*` .gitignore) so `log()` can
        append right away, but writes no state.json until `save()` is called."""
        root = os.path.abspath(root)
        rel = cls(root, version, {"base_commit": base_commit})
        _ensure_releases_dir(root)
        os.makedirs(rel.dir, exist_ok=True)
        return rel

    @classmethod
    def load(cls, root, version):
        """Rebuild a Release from its state.json. Raises OSError if it does not
        exist, or ValueError if it is not valid JSON."""
        root = os.path.abspath(root)
        path = os.path.join(_releases_dir(root), version, "state.json")
        with open(path, "rb") as f:
            data = json.loads(f.read().decode("utf-8"))
        return cls(root, version, data)

    def to_dict(self):
        """This release's state as a plain, JSON-serialisable dict."""
        return {"version": self.version, "base_commit": self.base_commit,
                "status": self.status, "release_commit": self.release_commit,
                "recipe_sha": self.recipe_sha, "artifact_sha": self.artifact_sha,
                "rollback_target": self.rollback_target, "approved_by": self.approved_by,
                "evidence": self.evidence}

    def save(self):
        """Write state.json atomically: a temp file in self.dir, then os.replace,
        then fsync self.dir so the rename survives a crash (conductor.py State.save's
        pattern)."""
        os.makedirs(self.dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".state.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        _fsync_dir(self.dir)

    def log(self, event, **fields):
        """Append one JSON line to release-log.jsonl: {"at": <RFC 3339 UTC>, "event":
        event, **fields}. The log is never rewritten (conductor.py State.log's
        pattern, including closing off a torn last line from a crash mid-write rather
        than rewriting it, so it cannot swallow this event). Raises ValueError if
        `fields` tries to override `at` or `event`."""
        if "at" in fields or "event" in fields:
            raise ValueError("log fields must not override 'at' or 'event'")
        os.makedirs(self.dir, exist_ok=True)
        record = {"at": _rfc3339(_now()), "event": event}
        record.update(fields)
        line = json.dumps(record, sort_keys=True, allow_nan=False) + "\n"
        with open(self.log_path, "a+b") as f:
            if f.seek(0, os.SEEK_END) > 0:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    f.write(b"\n")
            f.write(line.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        return record

    def events(self):
        """Every complete event in release-log.jsonl, oldest first; [] when the log
        does not exist yet. A torn line, or one that is not a JSON object, is
        skipped (conductor.py State.events' pattern)."""
        try:
            with open(self.log_path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            return []
        out = []
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
        return out

    def set_status(self, status, **fields):
        """Set this release's status (must be one of STATES) and, optionally, any of
        RELEASE_FIELDS. Raises ValueError on an unknown status or field. Does not
        save; call save() to make the change durable."""
        if status not in STATES:
            raise ValueError("unknown status: %s" % status)
        bad = set(fields) - set(RELEASE_FIELDS)
        if bad:
            raise ValueError("unknown release field(s): %s" % ", ".join(sorted(bad)))
        self.status = status
        for key, value in fields.items():
            setattr(self, key, value)
