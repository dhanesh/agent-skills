#!/usr/bin/env python3
"""release.py -- release-conductor's state tool foundations (roadmap step 5A, spec
docs/superpowers/specs/2026-10-03-release-conductor-design.md).

This module carries the pieces the `init/prep/stage/deploy/verify-prod/rollback`
commands are built on:

  - the release recipe (.release/recipe.json, spec section 1), loaded and validated
    from the working tree or from a git commit (`load_recipe`, `recipe_sha`);
  - argv expansion for recipe commands (`expand`);
  - a process-group-safe command runner (`run_cmd`);
  - a repo-wide run lock (`run_lock`, `Locked`), so two release commands never
    interleave writes;
  - the release state file and its append-only log (`Release`).

and the first two commands built on them:

  - `init --root R --answers FILE` writes the validated recipe (never overwriting one);
  - `prep --root R --approved-by NAME --driver ID [--bump L] [--policy-file F]
    [--push-cmd JSON] [--pr-cmd JSON]` writes the release intent and the release grant
    the human accepts (D10), commits the version bump and changelog on release/<version>
    in the worktree .skill-contract/releases/<version>/wt-prep, then pushes it and opens
    the release PR, each step gated by check-grant (subject the recipe, worktree the
    prep worktree) and run only on COVERED.

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
import re  # noqa: E402
import shutil  # noqa: E402
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
# driver: the agent id that drove `prep` (Task 4's `stage` refuses evidence it recorded);
# bump: the level prep used; grant: {"id", "accepted_by"} of the release grant prep wrote
# (accepted_by is the human who accepted the GRANT -- not approved_by, which is the
# production deploy's yes); prep: {"branch", "commit", "pushed", "pr"}, prep's progress.
RELEASE_FIELDS = ("release_commit", "recipe_sha", "artifact_sha", "rollback_target",
                  "approved_by", "evidence", "driver", "bump", "grant", "prep")

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
    if not CC.safe_path(recipe.get("verify_skill")):
        problems.append("verify_skill must be a relative, repo-rooted path (no leading "
                        "'/', no '..' segment, forward slashes) -- see CC.safe_path")
    return problems


def _recipe_bytes(root, rev=None):
    """The exact bytes of .release/recipe.json: the working file, or, when `rev` is
    given, the stdout of `git show <rev>:.release/recipe.json`. Raises OSError on an
    unreadable working file, or ValueError when git or the show fails.

    Runs with CC.git_env() (the vendored checker's own git_env, same one
    conductor.py uses everywhere it shells out to git): os.environ with GIT_DIR,
    GIT_WORK_TREE and friends stripped, so a GIT_DIR inherited from a caller's
    environment cannot redirect `git -C root show` at a different repository."""
    if rev is None:
        with open(os.path.join(root, *RECIPE_PATH.split("/")), "rb") as f:
            return f.read()
    try:
        r = subprocess.run(["git", "-C", root, "show", "%s:%s" % (rev, RECIPE_PATH)],
                           capture_output=True, timeout=30, env=CC.git_env())
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


_EXPAND_TOKEN = re.compile(r"\{(version|commit|env)\}")


def expand(argv, values):
    """argv with every occurrence of {version}, {commit} or {env} -- a whole element
    or embedded in a larger string -- replaced by values["version"], values["commit"]
    or values["env"] (only the keys `values` actually carries; any other key in
    `values`, or any other {token}, is left untouched).

    Substitution is single-pass: one re.sub over each argv element matches every
    {version}/{commit}/{env} token in that element in one scan, and the text put in
    its place is never itself re-scanned for a token it happens to contain. Naive
    sequential str.replace calls (replace {version}, then {commit}, then {env}) get
    this wrong -- expand(["x-{version}"], {"version": "V-{commit}", "commit": "C"})
    would turn into "x-V-{commit}" after the first replace, then "x-V-C" after the
    second, silently re-expanding a value that was never meant to be a template.
    Because a single pass cannot protect against that once two expansions are
    combined by a caller (e.g. one release step feeding another's output back in as
    `values`), it is refused outright: every substituted value must be a plain,
    brace-free string (ValueError otherwise). A recipe's version/commit/env values
    are fixed-format -- a semver string, a hex commit, "staging"/"prod" -- never a
    template to keep expanding.

    This must accept the same embedded placement load_recipe's validation does
    (_argv_problems/_neutralize_tokens, above): version_probe may read
    ["cat", "probe-{env}.txt"], and expand has to be able to fill that in, or a
    later `verify-prod` would literally run `cat probe-{env}.txt`. Embedded
    substitution is safe here because the argv never passes through a shell."""
    def repl(m):
        """The replacement for one {version}/{commit}/{env} match: the matching
        value from `values`, or the token unchanged when `values` has no such key."""
        key = m.group(1)
        if key not in values:
            return m.group(0)
        v = values[key]
        if not isinstance(v, str) or "{" in v or "}" in v:
            raise ValueError("expand(): values[%r] must be a brace-free string, got %r"
                             % (key, v))
        return v
    return [_EXPAND_TOKEN.sub(repl, a) for a in argv]


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
        self.driver = data.get("driver")
        self.bump = data.get("bump")
        self.grant = data.get("grant")
        self.prep = data.get("prep")

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
                "evidence": self.evidence, "driver": self.driver, "bump": self.bump,
                "grant": self.grant, "prep": self.prep}

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


# ── init and prep (spec section 2, decisions D7/D9/D10) ──────────────────────────
SKILL_NAME = "release-conductor"
SKILL_VERSION = "0.1.0"
INTENT_FILE = "intent.json"
PREP_WT = "wt-prep"
CHANGELOG = "CHANGELOG.md"  # R11: at the repo root, a new section on top, created if absent
# R10: a release in any other status (or one with no readable state) is unfinished.
FINISHED = frozenset({"verified", "rolled_back", "stage_failed"})
# The classes a release grant grants (D7, spec section 2); a --policy-file may only decline.
RELEASE_GRANT_CLASSES = ("local_reversible", "push_branch", "open_pr", "deploy_staging",
                         "push_tag")
RELEASE_BRANCH_PATTERN = "release/*"
GRANT_LIFETIME = _dt.timedelta(days=7)  # CC.MAX_GRANT_LIFETIME: every grant's cap
SEMVER_PLAIN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
REMOTE_TIMEOUT = 300

# Copied from spec-first-planning/assets/write_grant.py (GRANT_EXCLUDE, exclude_grants):
# a grant is one person's acceptance and check-grant treats a tracked grant as covering
# nothing, so prep keeps it out of commits in this clone via .git/info/exclude.
GRANT_EXCLUDE = ".skill-contract/envelopes/autonomy-grant-v1-*"

# Copied from factory-conductor/assets/conductor.py (DEFAULT_PUSH_CMD/DEFAULT_PR_CMD,
# PUSH_LONG_OPTIONS, PUSH_SHORT_FLAGS, REMOTE_NAME_RE, PUSH_PROTOCOLS, GIT_SAFE), with
# {run_branch} renamed {release_branch}: a token that is exactly {release_branch},
# {base_branch}, {title} or {body_file} is replaced (expand_cmd).
DEFAULT_PUSH_CMD = ["git", "push", "-u", "origin", "{release_branch}"]
DEFAULT_PR_CMD = ["gh", "pr", "create", "--title", "{title}", "--base", "{base_branch}",
                  "--head", "{release_branch}", "--body-file", "{body_file}"]
PUSH_LONG_OPTIONS = frozenset({"--set-upstream", "--porcelain", "--quiet"})
PUSH_SHORT_FLAGS = frozenset("uq")
REMOTE_NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9._-]*\Z"
PUSH_PROTOCOLS = "file:git:http:https:ssh"
GIT_SAFE = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
            "-c", "core.commitGraph=false")


class Refused(Exception):
    """prep/init refused (exit 2): invalid input, or a precondition does not hold."""


def next_version(cur, bump):
    """The version after `cur` (a plain MAJOR.MINOR.PATCH) at level `bump`. ValueError for
    a prerelease, build metadata, a leading 'v', anything else that is not plain semver,
    or an unknown level: a release bump from a prerelease is a human's call."""
    m = SEMVER_PLAIN.match(cur) if isinstance(cur, str) else None
    if not m:
        raise ValueError("%r is not a plain MAJOR.MINOR.PATCH version" % (cur,))
    major, minor, patch = (int(x) for x in m.groups())
    if bump == "major":
        return "%d.0.0" % (major + 1)
    if bump == "minor":
        return "%d.%d.0" % (major, minor + 1)
    if bump == "patch":
        return "%d.%d.%d" % (major, minor, patch + 1)
    raise ValueError("bump must be one of %s" % ", ".join(BUMP_LEVELS))


def one_line(text):
    """text with every run of whitespace collapsed to one space (conductor.py one_line)."""
    return " ".join(str(text if text is not None else "").split())


def code(text):
    """Untrusted text as one inline code span. Copied from factory-conductor's
    assets/conductor.py `code()`: newlines collapsed, and a backtick fence longer than any
    backtick run inside, so it cannot open a heading, list or link, and GitHub does not
    turn @mentions or closing keywords ("Closes #1") in it into actions."""
    s = one_line(text)
    runs = [len(m) for m in re.findall(r"`+", s)]
    fence = "`" * (max(runs) + 1 if runs else 1)
    pad = " " if s.startswith("`") or s.endswith("`") or not s else ""
    return "%s%s%s%s%s" % (fence, pad, s, pad, fence)


def _git(root, *args, env=None):
    """git -C root with hooks, fsmonitor and the commit-graph off (GIT_SAFE) and the git
    redirect variables scrubbed (CC.git_env). The CompletedProcess (text), or None when
    git cannot be run."""
    try:
        return subprocess.run(["git", "-C", root, *GIT_SAFE, *args], capture_output=True,
                              text=True, errors="replace", timeout=60,
                              env=env if env is not None else CC.git_env())
    except (OSError, subprocess.SubprocessError):
        return None


def _git_ok(root, *args):
    """stdout of a git call that must succeed; Refused naming the call otherwise."""
    r = _git(root, *args)
    if r is None or r.returncode != 0:
        raise Refused("git %s failed: %s" % (" ".join(args),
                                            (r.stderr.strip() if r else "git not runnable")))
    return r.stdout


def changelog_lines(root, since_tag):
    """One markdown bullet per merge commit on HEAD since `since_tag` (every merge when
    it is None), newest first, from `git log --merges --format=%s`. Merge subjects are
    executor-written, so each is rendered through code(): a single line, code-spanned."""
    rng = ["%s..HEAD" % since_tag] if since_tag else ["HEAD"]
    out = _git_ok(root, "log", "--merges", "--format=%s", *rng, "--")
    return ["- %s" % code(s) for s in out.splitlines() if s.strip()]


def _last_tag(root, rev):
    """The newest v<digit>* tag reachable from rev, or None."""
    r = _git(root, "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", rev)
    return r.stdout.strip() if r is not None and r.returncode == 0 and r.stdout.strip() else None


def _changelog_section(version, lines, since_tag):
    """The changelog section prep writes for `version`."""
    date = _now().strftime("%Y-%m-%d")
    body = lines or ["- No merged pull requests since %s." % (since_tag or "the first commit")]
    return "## %s (%s)\n\n%s\n" % (version, date, "\n".join(body))


def _prepend_changelog(text, section):
    """`text` (an existing CHANGELOG.md, or "" when absent) with `section` as its first
    section: after a leading `# ` title line when there is one, else at the very top."""
    if not text:
        return "# Changelog\n\n" + section
    first, sep, rest = text.partition("\n")
    if first.startswith("# "):
        return first + "\n\n" + section + "\n" + rest.lstrip("\n")
    return section + "\n" + text


# Copied from factory-conductor/assets/conductor.py (_cmd_arg, expand_cmd,
# push_cmd_problem, explicit_push): the same JSON-argv validation and push allowlist,
# with the run branch renamed the release branch.
def _cmd_arg(text, default, flag):
    """The argv list a --push-cmd/--pr-cmd JSON names (default when None). ValueError
    when it is not a non-empty list of strings."""
    if text is None:
        return list(default)
    cmd = json.loads(text)
    if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) and a for a in cmd):
        raise ValueError("%s must be a JSON list of non-empty strings" % flag)
    return cmd


def expand_cmd(argv, values):
    """argv with each token that is exactly {name} (name in values) replaced."""
    out = []
    for a in argv:
        m = re.match(r"^\{([a-z_]+)\}\Z", a)
        out.append(str(values[m.group(1)]) if m and m.group(1) in values else a)
    return out


def push_cmd_problem(argv, branch):
    """None when argv (placeholders already expanded) is a push the grant can cover, else
    why not. Only `git push [--set-upstream|--porcelain|--quiet|-u|-q ...] <remote>
    <branch>`, with <remote> a remote name and no refspec syntax (':' or '+'): never a
    force-push, never another branch (SPEC, commandment 10)."""
    if argv[:2] != ["git", "push"]:
        return "the push command must start with exactly: git push"
    positionals = []
    for a in argv[2:]:
        if a.startswith("--"):
            if a not in PUSH_LONG_OPTIONS:
                return "option %r is not allowed (allowed: %s, -u, -q)" % (
                    a, ", ".join(sorted(PUSH_LONG_OPTIONS)))
        elif a.startswith("-") and len(a) > 1:
            if not set(a[1:]) <= PUSH_SHORT_FLAGS:
                return "short option %r is not allowed (only -u and -q)" % a
        else:
            positionals.append(a)
    if len(positionals) != 2:
        return "the push must name exactly <remote> <release_branch>, got %r" % (positionals,)
    remote, ref = positionals
    if not re.match(REMOTE_NAME_RE, remote):
        return "the remote %r must be a remote name" % remote
    if ref != branch or ":" in ref or ref.startswith("+"):
        return "the push may only name the release branch %s, got %r" % (branch, ref)
    return None


def explicit_push(argv, branch, sha):
    """An allowlisted push argv with its branch positional (the last one) rewritten to the
    explicit, non-forced refspec <sha>:refs/heads/<branch>, so remote.<name>.push config
    cannot remap it and only the commit prep made is pushed."""
    out = list(argv)
    for i in range(len(out) - 1, 1, -1):
        if not out[i].startswith("-"):
            if out[i] != branch:
                raise ValueError("the last positional is not the release branch: %r" % out[i])
            out[i] = "%s:refs/heads/%s" % (sha, branch)
            return out
    raise ValueError("the push names no release branch")


def _safe_config_env(**extra):
    """CC.git_env() carrying GIT_SAFE as GIT_CONFIG_COUNT/KEY/VALUE, so a remote step
    whose git argv this tool does not spell (the push) also runs with hooks off
    (conductor.py safe_config_env)."""
    env = CC.git_env()
    pairs = [GIT_SAFE[i + 1].split("=", 1) for i in range(0, len(GIT_SAFE), 2)]
    env["GIT_CONFIG_COUNT"] = str(len(pairs))
    for i, (key, value) in enumerate(pairs):
        env["GIT_CONFIG_KEY_%d" % i] = key
        env["GIT_CONFIG_VALUE_%d" % i] = value
    env.update(extra)
    return env


def _run_remote(argv, cwd, env):
    """Run one remote step (no shell, no stdin, own process group). (ok, result)."""
    try:
        p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, errors="replace",
                             start_new_session=True, env=env)
    except OSError as e:
        return False, {"rc": None, "err_tail": "cannot run: %s" % e}
    try:
        out, err = p.communicate(timeout=REMOTE_TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill_group(p)
        p.communicate()
        return False, {"rc": None, "err_tail": "timed out"}
    _kill_group(p)
    return p.returncode == 0, {"rc": p.returncode, "out_tail": out[-TAIL:],
                               "err_tail": err[-TAIL:]}


def exclude_grants(root):
    """Append GRANT_EXCLUDE to <root>/.git/info/exclude unless already there (copied
    from spec-first-planning/assets/write_grant.py). True when the line is in place,
    False when <root>/.git is not a directory."""
    git_dir = os.path.join(root, ".git")
    if not os.path.isdir(git_dir):
        return False
    path = os.path.join(git_dir, "info", "exclude")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        text = ""
    if GRANT_EXCLUDE in (ln.strip() for ln in text.splitlines()):
        return True
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(("\n" if text and not text.endswith("\n") else "") + GRANT_EXCLUDE + "\n")
    return True


def _clean_name(name, flag):
    """name stripped; Refused when blank or carrying a control character."""
    if not (isinstance(name, str) and name.strip()):
        raise Refused("%s must name who it is" % flag)
    if any(ord(c) < 32 or ord(c) == 127 or 0x80 <= ord(c) < 0xA0 for c in name):
        raise Refused("%s must not contain control characters" % flag)
    return name.strip()


def _load_policy(path):
    """The release gate policy: every RELEASE_GRANT_CLASSES class "grant", minus what the
    user declined in `path` (a JSON object {class: "ask"|"grant"} over those classes
    only). A policy file can only decline, never widen."""
    policy = {c: "grant" for c in RELEASE_GRANT_CLASSES}
    if path is None:
        return policy
    try:
        with open(path, encoding="utf-8") as f:
            given = json.load(f)
    except (OSError, ValueError) as e:
        raise Refused("cannot read --policy-file %s: %s" % (path, e))
    if not isinstance(given, dict):
        raise Refused("--policy-file must be a JSON object {class: \"ask\"|\"grant\"}")
    for cls, gate in given.items():
        if cls not in RELEASE_GRANT_CLASSES:
            raise Refused("--policy-file names %r; a release grant covers only %s"
                          % (cls, ", ".join(RELEASE_GRANT_CLASSES)))
        if gate not in ("ask", "grant"):
            raise Refused("--policy-file[%r] must be \"ask\" or \"grant\"" % cls)
        policy[cls] = gate
    return policy


def _default_branch(root):
    """The default branch prep works from: origin/HEAD's target, else main, else master,
    whichever first exists as a local branch. Refused when none does."""
    names = []
    r = _git(root, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    if r is not None and r.returncode == 0 and r.stdout.startswith("refs/remotes/origin/"):
        names.append(r.stdout.strip()[len("refs/remotes/origin/"):])
    for n in names + ["main", "master"]:
        v = _git(root, "rev-parse", "-q", "--verify", "refs/heads/%s^{commit}" % n)
        if v is not None and v.returncode == 0 and v.stdout.strip():
            return n, v.stdout.strip()
    raise Refused("no default branch (origin/HEAD, main or master) to release from")


def unfinished_releases(root):
    """[(version, status)] for every release dir under .skill-contract/releases whose
    status is not finished (R10). A dir with no readable state counts, with status None:
    an unknown release fails closed."""
    d = _releases_dir(root)
    out = []
    try:
        names = sorted(os.listdir(d))
    except FileNotFoundError:
        return out
    for name in names:
        if not os.path.isdir(os.path.join(d, name)):
            continue  # .lock, .gitignore
        try:
            status = Release.load(root, name).status
        except (OSError, ValueError, KeyError, TypeError):
            status = None
        if status not in FINISHED:
            out.append((name, status))
    return out


def _version_key(recipe):
    """(file, dotted key) of the recipe's {"file","key"} version; Refused for the {"cmd"}
    form, which prep cannot bump, or an unsafe file path."""
    v = recipe["version"]
    if "cmd" in v:
        raise Refused("prep bumps a {\"file\", \"key\"} version only; this recipe reads its "
                      "version with version.cmd")
    if not CC.safe_path(v["file"]):
        raise Refused("version.file %r must be a relative, repo-rooted path" % v["file"])
    return v["file"], v["key"].split(".")


def _read_version(text, keys, where):
    """(document, current version) from a JSON version file's text."""
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise Refused("%s is not JSON: %s" % (where, e))
    node = doc
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            raise Refused("%s has no %s" % (where, ".".join(keys)))
        node = node[k]
    if not isinstance(node, str):
        raise Refused("%s %s is not a string" % (where, ".".join(keys)))
    return doc, node


def _set_version(doc, keys, version):
    node = doc
    for k in keys[:-1]:
        node = node[k]
    node[keys[-1]] = version


def _write_text(path, text):
    """Write text to a regular file at path; Refused when path is a symlink (a release
    commit must not write through a link out of the worktree)."""
    if os.path.islink(path):
        raise Refused("%s is a symlink; prep writes only regular files" % path)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def build_release_grant(root, version, intent_rel, policy, accepted_by, bump, now):
    """The release grant (autonomy-grant/v1, D7/D10), built like spec-first-planning's
    write_grant.build_grant: subjects the recipe and the intent file, release.version,
    a 7-day expiry, branch_pattern release/*, and one grant-accepted assertion by the
    human. Refused when the statement fails the checker's C3-C6 or C10 checks."""
    payload = {
        "scope": {"repo": ".", "branch_pattern": RELEASE_BRANCH_PATTERN},
        "release": {"version": version},
        "decisions": [{"id": "release-version", "question": "Which version does this release "
                       "ship?", "answer": version, "source": "release prep"},
                      {"id": "release-bump", "question": "Which bump level?", "answer": bump,
                       "source": "release prep"}],
        "defaults": [],
        "gate_policy": policy,
        "budget": {},
        "stop_on": [],
        "expires_at": _rfc3339(now + GRANT_LIFETIME),
        "system_one": {"allowed": False},
        "revoked": False,
    }
    accepted = {"test": "grant-accepted", "assertedBy": {"human": accepted_by},
                "result": {"outcome": "passed"},
                "command": ["{python}", "{skill_dir:%s}/assets/release.py" % SKILL_NAME,
                            "prep"],
                "subject": [CC.pin(root, RECIPE_PATH), CC.pin(root, intent_rel)]}
    st = CC.build_statement(CC.GRANT_KIND, SKILL_NAME, SKILL_VERSION, root,
                            [RECIPE_PATH, intent_rel], payload, [accepted], now=now)
    viol = ["C%d: %s" % v for v in CC.check_statement(st)] or CC.grant_violations(st)
    if viol:
        raise Refused("the release grant fails the checker: %s" % viol[0])
    return st


def _gate(root, action, wt):
    """check-grant for `action`, judged on the prep worktree and selected by the recipe."""
    return CC.check_grant(root, action, subject=RECIPE_PATH, worktree=wt)


def _gate_stop(rel, action, rep, nxt):
    """Print and log a gate that did not answer COVERED; exit code 3."""
    print("GATE: %s %s %s" % (action, rep["status"], rep["reason"]))
    print("STOP: gate %s answered %s (%s)" % (action, rep["status"], rep["reason"]))
    if nxt:
        print("NEXT: %s" % nxt)
    rel.log("gate", action=action, status=rep["status"], reason=rep["reason"], ok=False)
    return 3


def cmd_init(args):
    """Write .release/recipe.json from a JSON answers file, validated; never overwrite."""
    root = os.path.abspath(args.root)
    path = os.path.join(root, *RECIPE_PATH.split("/"))
    try:
        with open(args.answers, encoding="utf-8") as f:
            recipe = json.load(f)
    except (OSError, ValueError) as e:
        print("RELEASE: refused: cannot read --answers %s: %s" % (args.answers, e))
        return 2
    problems = _validate_recipe(recipe)
    if problems:
        print("RELEASE: refused: invalid recipe")
        for p in problems:
            print("  - %s" % p)
        return 2
    if os.path.lexists(path):
        print("RELEASE: refused: %s exists; change it in a reviewed commit, not with init"
              % RECIPE_PATH)
        return 2
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        json.dump(recipe, f, indent=2, sort_keys=True)
        f.write("\n")
    print("RELEASE: recipe written")
    print("NEXT: commit %s through a reviewed PR, then run prep" % RECIPE_PATH)
    return 0


def _cleanup_prep(root, rel, wt, branch, made_branch):
    """Undo a prep that failed before its commit: the worktree, the branch prep created,
    and the release dir, so no orphan blocks the next prep (R10). The grant stays: its
    intent file is gone, so it is stale and covers nothing."""
    if os.path.isdir(wt):
        _git(root, "worktree", "remove", "--force", wt)
    _git(root, "worktree", "prune")
    if made_branch:
        _git(root, "branch", "-D", branch)
    shutil.rmtree(rel.dir, ignore_errors=True)


def _prep_checks(root, args):
    """Every refusal prep makes before it writes anything. Returns a dict of what the
    later steps need, or raises Refused."""
    accepted_by = _clean_name(args.approved_by, "--approved-by")
    driver = _clean_name(args.driver, "--driver")
    try:
        push_cmd = _cmd_arg(args.push_cmd, DEFAULT_PUSH_CMD, "--push-cmd")
        pr_cmd = _cmd_arg(args.pr_cmd, DEFAULT_PR_CMD, "--pr-cmd")
    except ValueError as e:  # JSONDecodeError is a ValueError
        raise Refused("invalid command: %s" % e)
    policy = _load_policy(args.policy_file)
    recipe, problems = load_recipe(root)
    if problems:
        raise Refused("invalid recipe:\n" + "\n".join("  - %s" % p for p in problems))
    base_branch, base = _default_branch(root)
    dirty = _git(root, "status", "--porcelain", "--untracked-files=all", "--", RECIPE_PATH)
    try:
        committed = recipe_sha(root, rev=base) == recipe_sha(root)
    except (OSError, ValueError):
        committed = False
    if dirty is None or dirty.returncode != 0 or dirty.stdout.strip() or not committed:
        raise Refused("recipe-uncommitted: %s must be committed on %s unchanged; the "
                      "grant pins a digest some commit has" % (RECIPE_PATH, base_branch))
    busy = unfinished_releases(root)
    if busy:
        raise Refused("another release is unfinished: %s; finish or roll it back first"
                      % ", ".join("%s (%s)" % (v, s or "no state") for v, s in busy))
    vfile, keys = _version_key(recipe)
    _, cur = _read_version(_git_ok(root, "show", "%s:%s" % (base, vfile)), keys, vfile)
    bump = args.bump or recipe["bump"]
    try:
        version = next_version(cur, bump)
    except ValueError as e:
        raise Refused("%s: %s" % (vfile, e))
    branch = "release/%s" % version
    why = push_cmd_problem(expand_cmd(push_cmd, {"release_branch": branch}), branch)
    if why:
        raise Refused("--push-cmd is not a push a grant covers: %s" % why)
    exists = _git(root, "rev-parse", "-q", "--verify", "refs/heads/%s" % branch)
    if exists is not None and exists.returncode == 0:
        raise Refused("branch %s already exists" % branch)
    return {"accepted_by": accepted_by, "driver": driver, "push_cmd": push_cmd,
            "pr_cmd": pr_cmd, "policy": policy, "base_branch": base_branch, "base": base,
            "vfile": vfile, "keys": keys, "bump": bump, "version": version,
            "branch": branch}


def cmd_prep(args):
    """Prepare a release (spec section 2, step 1). Exit 0 prepped and PR opened; 2
    refused before anything was written (or local work undone); 3 stopped: a gate did
    not answer COVERED, a remote step failed, or the lock is held."""
    root = os.path.abspath(args.root)
    try:
        with run_lock(root):
            return _prep_locked(root, args)
    except Locked:
        print("STOP: locked: another release command holds the run lock")
        return 3


def _prep_locked(root, args):
    try:
        c = _prep_checks(root, args)
    except Refused as e:
        print("RELEASE: refused: %s" % e)
        return 2
    version, branch = c["version"], c["branch"]
    rel = Release.new(root, version, c["base"])
    intent_rel = "%s/%s/%s" % (RELEASES_DIR, version, INTENT_FILE)
    wt = os.path.join(rel.dir, PREP_WT)
    made_branch = False
    try:
        with open(os.path.join(rel.dir, INTENT_FILE), "x", encoding="utf-8",
                  newline="\n") as f:
            json.dump({"version": version, "base_commit": c["base"], "bump": c["bump"]}, f,
                      indent=2, sort_keys=True)
            f.write("\n")
        now = CC.utc_now()
        st = build_release_grant(root, version, intent_rel, c["policy"], c["accepted_by"],
                                 c["bump"], now)
        grant_path = CC.write_envelope(root, st)
        exclude_grants(root)
        rel.log("grant", id=st["predicate"]["id"], accepted_by=c["accepted_by"],
                path=os.path.relpath(grant_path, root))
        _git_ok(root, "worktree", "add", "-q", "-b", branch, wt, c["base"])
        made_branch = True
        rep = _gate(root, "local_reversible", wt)
        if rep["status"] != "COVERED":
            rc = _gate_stop(rel, "local_reversible", rep, None)
            _cleanup_prep(root, rel, wt, branch, made_branch)
            return rc
        vpath = os.path.join(wt, *c["vfile"].split("/"))
        with open(vpath, encoding="utf-8") as f:
            doc, _ = _read_version(f.read(), c["keys"], c["vfile"])
        _set_version(doc, c["keys"], version)
        _write_text(vpath, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        tag = _last_tag(wt, "HEAD")
        section = _changelog_section(version, changelog_lines(wt, tag), tag)
        cpath = os.path.join(wt, CHANGELOG)
        try:
            with open(cpath, encoding="utf-8") as f:
                old = f.read()
        except FileNotFoundError:
            old = ""
        _write_text(cpath, _prepend_changelog(old, section))
        # R3: stage exactly the version file and the changelog, by path, never -A or `.`.
        _git_ok(wt, "add", "--", c["vfile"], CHANGELOG)
        _git_ok(wt, "commit", "-q", "--no-verify", "-m", "release: %s" % version)
        commit = _git_ok(wt, "rev-parse", "HEAD").strip()
    except (Refused, OSError, ValueError) as e:
        print("RELEASE: refused: %s" % e)
        rel.log("prep_failed", reason=str(e))
        _cleanup_prep(root, rel, wt, branch, made_branch)
        return 2
    rel.status = "prepped"
    rel.recipe_sha = recipe_sha(root)
    rel.driver, rel.bump = c["driver"], c["bump"]
    rel.grant = {"id": st["predicate"]["id"], "accepted_by": c["accepted_by"]}
    rel.prep = {"branch": branch, "base_branch": c["base_branch"], "commit": commit,
                "pushed": False, "pr": False}
    rel.save()
    rel.log("prepped", commit=commit, branch=branch)
    by_hand = ("push %s (commit %s) and open its PR against %s by hand, or re-grant and "
               "prep again" % (branch, commit[:12], c["base_branch"]))

    rep = _gate(root, "push_branch", wt)
    if rep["status"] != "COVERED":
        return _gate_stop(rel, "push_branch", rep, by_hand)
    argv = explicit_push(expand_cmd(c["push_cmd"], {"release_branch": branch}), branch, commit)
    ok, res = _run_remote(argv, wt, _safe_config_env(GIT_ALLOW_PROTOCOL=PUSH_PROTOCOLS))
    rel.log("push", command=argv, returncode=res["rc"], ok=ok)
    if not ok:
        print("STOP: push failed (exit %s): %s" % (res["rc"], one_line(res["err_tail"])))
        print("NEXT: %s" % by_hand)
        return 3
    rel.prep["pushed"] = True
    rel.save()

    rep = _gate(root, "open_pr", wt)
    if rep["status"] != "COVERED":
        return _gate_stop(rel, "open_pr", rep, "open the PR for %s against %s by hand"
                          % (branch, c["base_branch"]))
    title = "Release %s" % version
    body = ("%s\n\nPrepared by release-conductor from %s. A human merges this PR; then run "
            "`release stage`.\n" % (section.rstrip("\n"), code(c["base"][:12])))
    fd, body_path = tempfile.mkstemp(dir=rel.dir, prefix=".pr-body.", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        argv = expand_cmd(c["pr_cmd"], {"release_branch": branch, "base_branch":
                                        c["base_branch"], "title": title,
                                        "body_file": body_path})
        ok, res = _run_remote(argv, wt, _safe_config_env())
    finally:
        with contextlib.suppress(OSError):
            os.unlink(body_path)
    rel.log("pr", command=argv, returncode=res["rc"], ok=ok)
    if not ok:
        print("STOP: PR command failed (exit %s): %s" % (res["rc"], one_line(res["err_tail"])))
        print("NEXT: open the PR for %s against %s by hand" % (branch, c["base_branch"]))
        return 3
    rel.prep["pr"] = True
    rel.save()
    print("RELEASE: %s prepped" % version)
    print("NEXT: merge the release PR, then run stage")
    return 0


def build_parser():
    import argparse
    ap = argparse.ArgumentParser(prog="release.py", description="release-conductor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="write .release/recipe.json from an answers file")
    p.add_argument("--root", required=True)
    p.add_argument("--answers", required=True)
    p = sub.add_parser("prep", help="write the release grant, branch, and open the PR")
    p.add_argument("--root", required=True)
    p.add_argument("--approved-by", required=True, dest="approved_by",
                   help="the human, present now, who accepts the release grant")
    p.add_argument("--driver", required=True, help="the driving agent's id")
    p.add_argument("--bump", choices=BUMP_LEVELS, help="override the recipe's bump level")
    p.add_argument("--policy-file", dest="policy_file",
                   help="JSON {class: \"ask\"} declining release grant classes")
    p.add_argument("--push-cmd", dest="push_cmd", help="JSON argv (default: git push -u "
                   "origin {release_branch})")
    p.add_argument("--pr-cmd", dest="pr_cmd", help="JSON argv (default: gh pr create ...)")
    return ap


def main(argv=None):
    """Exit 0 ok, 2 refused or invalid, 3 must stop / needs the human."""
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    return {"init": cmd_init, "prep": cmd_prep}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
