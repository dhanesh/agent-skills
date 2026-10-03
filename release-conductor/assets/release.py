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

and the commands built on them:

  - `init --root R --answers FILE` writes the validated recipe (never overwriting one);
  - `prep --root R --approved-by NAME --driver ID [--bump L] [--policy-file F]
    [--push-cmd JSON] [--pr-cmd JSON]` applies the newest live planning grant's
    recorded release_defaults where --bump/--policy-file leave a gap (R33/R35;
    spec-first-planning's unattended interview records them, never this skill's own
    release grant), then writes the release intent and the release grant the human
    accepts (D10), commits the version bump and changelog on release/<version> in the
    worktree .skill-contract/releases/<version>/wt-prep, then pushes it and opens the
    release PR, each step gated by check-grant (subject the recipe, worktree the
    prep worktree) and run only on COVERED;
  - `stage --root R [--commit SHA] [--evidence --verifier ID] [--remote NAME]` finds the
    merged release commit (where the default branch's version file became the grant's
    release.version), refuses when its version or its recipe differs from what the
    grant pinned (STOP: version-mismatch / recipe-changed, before anything from the
    recipe runs), builds it in an isolated checkout (wt-build), and stops at
    staging_verify with NEXT: dispatch-verifier <commit>; `--evidence` judges the
    verifier's records (factory-conductor's evidence predicate, every mapped feature,
    a verifier other than the driver), then deploys to staging, polls the version
    probe, runs staging_checks, and pushes (or, when CI runs on tags, holds) the
    v<version> tag. Every gate names the stage worktree (release/<v>-stage). A re-run
    resumes from the first unfinished step; any failure is stage_failed.
  - `deploy --root R --approved-by NAME [--unattended] [--remote NAME]` is the production
    deploy (class `deploy`, never grantable). It refuses unless the release is staged,
    its evidence is for the release commit, the recipe and build checkout (and
    artifact) are unchanged since stage, the deploy gate does NOT answer COVERED, and no
    live grant's headless allowlist could run deploy_prod or rollback. Unattended, or
    without the human's yes, it waits at awaiting_deploy with the command ready.
    Otherwise it records the rollback target and the CLAIMED yes, then runs deploy_prod
    once in wt-build (or, when CI deploys on tags, pushes v<version>). A crash leaves
    `deploying`, which the next locked command turns into outcome_unknown -- never into
    a second run.
  - `verify-prod --root R` proves production runs the release (deployed, outcome_unknown,
    or a prod_failed deploy not yet judged): it polls the production version probe up to
    deploy_timeout (still the rollback target: not-live; another version: wrong-version),
    then runs health and each prod_smoke argv -- staging_checks never run against
    production. A pass is `verified` and writes release-result/v1 (rc values only, never
    output tails); a fail is prod_failed with NEXT: ask the human to roll back.
  - `rollback --root R --approved-by NAME [--unattended]` rolls production back to the
    recorded rollback target, only with the human's in-session yes (CLAIMED; class
    `deploy`, never grantable). It sets rolling_back before the command runs, polls the
    probe for the target, and on success is rolled_back with release-result/v1; any
    other end is outcome_unknown, never re-run by itself.
  - `status --root R` prints each release's status. It reads only: no lock, no change.

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
import glob  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import unicodedata  # noqa: E402

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
# stage: stage's progress ({"commit", "worktree", "build_dir", "built", "artifact",
# "deployed", "live", "probe", "checks", "checks_done", "tag", "failure"}), so a re-run
# resumes from the first unfinished step; tag_deploys: true when the CI config at the
# release commit may run on a tag (D6), so the tag push is held for `deploy`.
# approved_by: the production yes, {"name", "status": "CLAIMED"} (a shell-capable agent
# can forge an in-session yes, so it is never recorded as verified). rollback_target:
# what production ran before `deploy`, {"source": "probe"|"release-result"|"none",
# "version": str|None, "commit": str|None} -- source "none" means nothing to roll back to.
# prod: verify-prod's local record ({"probe", "health", "smoke", "failure"}, with output
# tails -- local only); rollback: the rollback's ({"approved_by", "argv", "target", "rc",
# "out_tail", "err_tail", "timed_out", "probe"}); result: the release-result/v1 envelope
# written at the end ({"id", "path", "sha256", "outcome"}).
RELEASE_FIELDS = ("release_commit", "recipe_sha", "artifact_sha", "rollback_target",
                  "approved_by", "evidence", "driver", "bump", "grant", "prep", "stage",
                  "tag_deploys", "prod", "rollback", "result")

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
    group, with the git redirect variables scrubbed from its environment (CC.git_env),
    and kill that whole group when the command ends or times out -- so a detached
    grandchild cannot outlive the result. Copies factory-conductor's
    assets/conductor.py `_run_verify`/`_kill_group` pattern.

    Returns {"rc", "out_tail", "err_tail", "timed_out"}: rc is the exit code, or None
    on a timeout or a failure to start; out_tail/err_tail are the last TAIL
    characters of stdout/stderr (kept local, never logged to a release record --
    deploy output can carry secrets, spec section 3)."""
    try:
        # CC.git_env(): a GIT_DIR (or GIT_WORK_TREE, ...) inherited from the caller must
        # not redirect a recipe command's own git calls at another repository.
        p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, errors="replace",
                             start_new_session=True, env=CC.git_env())
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
        self.stage = data.get("stage")
        self.tag_deploys = data.get("tag_deploys")
        self.prod = data.get("prod")
        self.rollback = data.get("rollback")
        self.result = data.get("result")

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
                "grant": self.grant, "prep": self.prep, "stage": self.stage,
                "tag_deploys": self.tag_deploys, "prod": self.prod,
                "rollback": self.rollback, "result": self.result}

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
# R15: the grant covers this version's branches only (release/<v>, release/<v>-stage),
# never another release's. Exact names, not a glob: release/1.2.1* would cover 1.2.10 too.
RELEASE_BRANCH_PATTERNS = ("release/%s", "release/%s-stage")
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
    """Run one remote step (no shell, no stdin, own process group). (ok, result); result's
    timed_out is True only when the step ran past REMOTE_TIMEOUT (and was killed), so a
    step that could not start is told apart from one that may have reached the remote."""
    try:
        p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, errors="replace",
                             start_new_session=True, env=env)
    except OSError as e:
        return False, {"rc": None, "err_tail": "cannot run: %s" % e, "timed_out": False}
    try:
        out, err = p.communicate(timeout=REMOTE_TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill_group(p)
        p.communicate()
        return False, {"rc": None, "err_tail": "timed out", "timed_out": True}
    _kill_group(p)
    return p.returncode == 0, {"rc": p.returncode, "out_tail": out[-TAIL:],
                               "err_tail": err[-TAIL:], "timed_out": False}


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
    # write_grant._check_accepted_by's rule: no Unicode "C*" category at all, so a bidi
    # override or zero-width character cannot spoof the name in assertedBy.human.
    if any(unicodedata.category(c)[0] == "C" for c in name):
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


def _release_defaults_source(root, now=None):
    """(grant_id, release_defaults) from the newest live grant under root that qualifies
    as a source of release defaults (ruling R33, settled by R35): not revoked, not
    expired, passing the checker's own grant_violations (C10), carrying
    payload.release_defaults, and carrying NO payload.release -- a planning grant
    (spec-first-planning's write_grant.py) is a source; this skill's own release grant
    (build_release_grant, which never writes release_defaults anyway) never is, even if
    it were somehow made to carry one, because a release grant answers a different
    question (this release's scope) than a planning grant's recorded defaults.

    (None, None) when nothing qualifies: prep then applies today's behaviour unchanged
    (--bump as given or the recipe's own; --policy-file as given or every class granted).

    Candidates are enumerated with the vendored checker's own CC._live_heads (already
    ranked by revocation/supersession through the revision chain) rather than
    re-walking that logic here; only the extra release-defaults-specific filters are
    applied locally."""
    now = now or CC.utc_now()
    candidates = []
    for rank, _, st in CC._live_heads(root):
        payload = st["predicate"].get("payload") or {}
        if payload.get("revoked"):
            continue
        if "release" in payload or not isinstance(payload.get("release_defaults"), dict):
            continue
        if CC.check_statement(st) or CC.grant_violations(st):
            continue
        try:
            expired = now >= CC._parse_time(payload["expires_at"])
        except (KeyError, TypeError, ValueError):
            continue  # grant_violations already requires a parseable expires_at; fail closed
        if expired:
            continue
        candidates.append((rank, st["predicate"]["id"], payload["release_defaults"]))
    if not candidates:
        return None, None
    _, gid, defaults = max(candidates, key=lambda c: c[0])
    return gid, defaults


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
    a 7-day expiry, branch_pattern [release/<version>, release/<version>-stage] (R15), and one
    grant-accepted assertion by the human. Refused when the statement fails the checker's
    C3-C6 or C10 checks."""
    payload = {
        "scope": {"repo": ".", "branch_pattern": [b % version for b in RELEASE_BRANCH_PATTERNS]},
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


def _grant_path(root, grant_id):
    """The envelope path of the grant `grant_id`."""
    return os.path.join(CC.envelope_dir(root), grant_id + ".json")


def _gate(root, action, wt, grant_id):
    """check-grant for `action` against the release grant prep wrote (grant_id), with
    subject the recipe, judged on the prep worktree. The grant is named by path, not
    left to subject selection: a revocation of an earlier failed prep's grant can carry
    the same generatedAtTime second as this one, and would then tie with it as "newest".
    A revocation of THIS grant is a revision of it, so it still answers ASK superseded."""
    return CC.check_grant(root, action, path=_grant_path(root, grant_id),
                          subject=RECIPE_PATH, worktree=wt)


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


def _cleanup_prep(root, rel, wt, branch, made_branch, grant_id):
    """Undo a prep that failed before its commit: the worktree, the branch prep created,
    the release dir, and the grant it wrote. The grant is revoked (CC.revoke_grant), not
    merely left stale: a later prep of the same version from the same base rewrites a
    byte-identical intent file, which would otherwise make the old grant fresh again."""
    if os.path.isdir(wt):
        _git(root, "worktree", "remove", "--force", wt)
    _git(root, "worktree", "prune")
    if made_branch:
        _git(root, "branch", "-D", branch)
    shutil.rmtree(rel.dir, ignore_errors=True)
    if grant_id:
        try:
            CC.revoke_grant(root, grant_id)
        except (OSError, ValueError) as e:
            print("WARNING: could not revoke the release grant %s: %s; revoke it with "
                  "check-grant's revoke-grant --id" % (grant_id, e))


def _prep_checks(root, args):
    """Every input and recipe refusal prep makes before it writes anything, and the
    version it would release. Returns a dict of what the later steps need, or raises
    Refused. The unfinished-release (R10) and branch-exists checks are the caller's,
    since a re-run for a prepped release with pending steps resumes instead."""
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
    vfile, keys = _version_key(recipe)
    _, cur = _read_version(_git_ok(root, "show", "%s:%s" % (base, vfile)), keys, vfile)
    # R33/R35: a planning grant's recorded release defaults, applied only where the
    # human left a gap -- an explicit --bump or --policy-file always wins outright.
    defaults_id, defaults = _release_defaults_source(root)
    bump = args.bump or (defaults or {}).get("bump") or recipe["bump"]
    if defaults and args.policy_file is None:
        if defaults.get("grant_staging") is False:
            policy["deploy_staging"] = "ask"
        if defaults.get("grant_tag") is False:
            policy["push_tag"] = "ask"
    try:
        version = next_version(cur, bump)
    except ValueError as e:
        raise Refused("%s: %s" % (vfile, e))
    branch = "release/%s" % version
    why = push_cmd_problem(expand_cmd(push_cmd, {"release_branch": branch}), branch)
    if why:
        raise Refused("--push-cmd is not a push a grant covers: %s" % why)
    return {"accepted_by": accepted_by, "driver": driver, "push_cmd": push_cmd,
            "pr_cmd": pr_cmd, "policy": policy, "base_branch": base_branch, "base": base,
            "vfile": vfile, "keys": keys, "bump": bump, "version": version,
            "branch": branch, "release_defaults_source": defaults_id}


def cmd_prep(args):
    """Prepare a release (spec section 2, step 1). Exit 0 prepped and PR opened (or
    already so); 2 refused before anything was written (or local work undone); 3
    stopped: a gate did not answer COVERED, a remote step failed, or the lock is held.
    A re-run while this same version is `prepped` with its push or PR pending resumes
    those steps (R12) with the grant, commit and worktree the first run made."""
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
        busy = unfinished_releases(root)
        if busy == [(c["version"], "prepped")]:
            return _prep_resume(root, c)
        if busy:
            raise Refused("another release is unfinished: %s; finish or roll it back first"
                          % ", ".join("%s (%s)" % (v, s or "no state") for v, s in busy))
        exists = _git(root, "rev-parse", "-q", "--verify", "refs/heads/%s" % c["branch"])
        if exists is not None and exists.returncode == 0:
            raise Refused("branch %s already exists" % c["branch"])
    except Refused as e:
        print("RELEASE: refused: %s" % e)
        return 2
    version, branch = c["version"], c["branch"]
    rel = Release.new(root, version, c["base"])
    intent_rel = "%s/%s/%s" % (RELEASES_DIR, version, INTENT_FILE)
    wt = os.path.join(rel.dir, PREP_WT)
    made_branch = False
    grant_id = None
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
        grant_id = st["predicate"]["id"]
        exclude_grants(root)
        rel.log("grant", id=grant_id, accepted_by=c["accepted_by"],
                path=os.path.relpath(grant_path, root))
        defaults_source = c["release_defaults_source"]
        print("RELEASE: release defaults applied from grant %s" % defaults_source
              if defaults_source else
              "RELEASE: no release-defaults grant found; using explicit flags and the recipe")
        rel.log("release_defaults", source=defaults_source)
        _git_ok(root, "worktree", "add", "-q", "-b", branch, wt, c["base"])
        made_branch = True
        rep = _gate(root, "local_reversible", wt, grant_id)
        if rep["status"] != "COVERED":
            rc = _gate_stop(rel, "local_reversible", rep, None)
            _cleanup_prep(root, rel, wt, branch, made_branch, grant_id)
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
        _cleanup_prep(root, rel, wt, branch, made_branch, grant_id)
        return 2
    rel.status = "prepped"
    rel.recipe_sha = recipe_sha(root)
    rel.driver, rel.bump = c["driver"], c["bump"]
    rel.grant = {"id": grant_id, "accepted_by": c["accepted_by"]}
    rel.prep = {"branch": branch, "base_branch": c["base_branch"], "commit": commit,
                "section": section, "pushed": False, "pr": False}
    rel.save()
    rel.log("prepped", commit=commit, branch=branch)
    return _prep_remote(root, rel, c["push_cmd"], c["pr_cmd"])


def _prep_resume(root, c):
    """R12: re-run prep for the release `c` names, already `prepped`. Nothing local is
    redone -- no new intent, grant or commit; the pending push and PR steps are re-gated
    against the grant prep wrote (same subject, same worktree) and run. A prep whose
    push and PR both completed says so and exits 0."""
    try:
        rel = Release.load(root, c["version"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        print("RELEASE: refused: cannot load release %s: %s" % (c["version"], e))
        return 2
    prep, grant = rel.prep or {}, rel.grant or {}
    if not (prep.get("commit") and prep.get("branch") == c["branch"] and grant.get("id")):
        print("RELEASE: refused: release %s is prepped but its state has no prep commit "
              "or grant; a human must finish it" % c["version"])
        return 2
    if prep.get("pushed") and prep.get("pr"):
        print("RELEASE: %s already prepped" % c["version"])
        print("NEXT: merge the release PR, then run stage")
        return 0
    rel.log("prep_resume", pushed=bool(prep.get("pushed")), pr=bool(prep.get("pr")))
    return _prep_remote(root, rel, c["push_cmd"], c["pr_cmd"])


def _prep_remote(root, rel, push_cmd, pr_cmd):
    """prep's pending remote steps -- push, then the PR -- each gated by check-grant
    with subject the recipe and worktree wt-prep (the grant prep wrote, rel.grant),
    and run only on COVERED. Records progress in rel.prep after each step, so a stop
    leaves state that a re-run of prep resumes from."""
    version, prep = rel.version, rel.prep
    branch, commit, base_branch = prep["branch"], prep["commit"], prep["base_branch"]
    wt = os.path.join(rel.dir, PREP_WT)
    if not os.path.isdir(wt):
        print("RELEASE: refused: the prep worktree %s is missing" % wt)
        return 2
    resume = "fix what stopped it, then re-run prep to resume"
    if not prep.get("pushed"):
        rep = _gate(root, "push_branch", wt, rel.grant["id"])
        if rep["status"] != "COVERED":
            return _gate_stop(rel, "push_branch", rep, resume)
        argv = explicit_push(expand_cmd(push_cmd, {"release_branch": branch}), branch, commit)
        ok, res = _run_remote(argv, wt, _safe_config_env(GIT_ALLOW_PROTOCOL=PUSH_PROTOCOLS))
        rel.log("push", command=argv, returncode=res["rc"], ok=ok)
        if not ok:
            print("STOP: push failed (exit %s): %s" % (res["rc"], one_line(res["err_tail"])))
            print("NEXT: %s" % resume)
            return 3
        prep["pushed"] = True
        rel.save()
    if not prep.get("pr"):
        rep = _gate(root, "open_pr", wt, rel.grant["id"])
        if rep["status"] != "COVERED":
            return _gate_stop(rel, "open_pr", rep, resume)
        title = "Release %s" % version
        body = ("%s\n\nPrepared by release-conductor from %s. A human merges this PR; then "
                "run `release stage`.\n" % ((prep.get("section") or title).rstrip("\n"),
                                            code(rel.base_commit[:12])))
        fd, body_path = tempfile.mkstemp(dir=rel.dir, prefix=".pr-body.", suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body)
            argv = expand_cmd(pr_cmd, {"release_branch": branch, "base_branch": base_branch,
                                       "title": title, "body_file": body_path})
            ok, res = _run_remote(argv, wt, _safe_config_env())
        finally:
            with contextlib.suppress(OSError):
                os.unlink(body_path)
        rel.log("pr", command=argv, returncode=res["rc"], ok=ok)
        if not ok:
            print("STOP: PR command failed (exit %s): %s" % (res["rc"],
                                                            one_line(res["err_tail"])))
            print("NEXT: %s" % resume)
            return 3
        prep["pr"] = True
        rel.save()
    print("RELEASE: %s prepped" % version)
    print("NEXT: merge the release PR, then run stage")
    return 0


# ── stage (spec section 2, step 2; decisions D6/D7/D10/D11) ──────────────────────
STAGE_WT = "wt-stage"  # the stage worktree, on release/<v>-stage, where the verifier works
BUILD_WT = "wt-build"  # the isolated checkout build and deploy_staging run in
STAGE_ENV = "staging"  # {env} for every staging command (verify-prod uses "production")
PROBE_INTERVAL = 2.0  # seconds between version-probe polls; read at call time (tests shorten it)
PROBE_CMD_TIMEOUT = 30  # one probe run, at most (capped by what is left of deploy_timeout)
CMD_TIMEOUT = 3600  # build, deploy_staging and each staging check
COMMIT_SCAN = 1000  # first-parent commits stage walks back looking for the release commit
EVIDENCE_DIR = ".verify"  # <root>/.verify: where the verifier records (VERIFY_EVIDENCE_DIR)
RESUME_STAGE = "fix what stopped it, then re-run stage to resume"


def _sleep(seconds):
    """time.sleep, behind a module name so a test can stand in for the wait between
    probe polls (and change the probed file at an exact poll) without racing a timer."""
    time.sleep(seconds)


_PROBE_TOKEN = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]*")
_HEX = re.compile(r"^[0-9a-f]{7,40}\Z")


def probe_reports(out, version, commit):
    """True when a version probe's output reports `version` or `commit`: some token of it
    (a run of [0-9A-Za-z._+-], trailing dots dropped) is exactly the version, `v` plus the
    version, or a 7-to-40 hex prefix of the commit. A prerelease (1.2.0-rc.1), build
    metadata (1.2.0+b), or a longer version (11.2.0, 1.2.0.1) is not the version."""
    for tok in _PROBE_TOKEN.findall(out or ""):
        tok = tok.rstrip(".")
        if tok in (version, "v" + version):
            return True
        low = tok.lower()
        if _HEX.match(low) and commit.startswith(low):
            return True
    return False


# ── The evidence predicate ───────────────────────────────────────────────────────
# Copied from factory-conductor/assets/conductor.py (feature_map_at, _inside,
# _load_record, _check_record, evidence_verdict), adapted for a release (D11): the head
# is the release commit; the verifier must not be the release driver (prep's --driver);
# and the features to prove are EVERY feature the verify skill maps at that commit (the
# full check set), not the ones a task's diff touches. Keep the two in step: a fix to
# one belongs in the other. The records are the ones verification-skill-forge's
# verify_evidence.py writes, at <root>/.verify/<instance>/<feature>/<sha>/evidence.json,
# with each instance's doctor result at <root>/.verify/<instance>/doctor/<sha>/doctor.json.
EVIDENCE_SCHEMA = "verify-evidence/v1"


def feature_map_at(root, skill, sha):
    """{feature id: [anchor paths]} for the verify skill's features/ as committed at sha,
    or None when git cannot list them. Read from the commit, never from a working tree,
    so the map judged is the one the release commit carries."""
    r = _git(root, "ls-tree", "--name-only", "%s:%s/features" % (sha, skill))
    if r is None or r.returncode != 0:
        return None
    app = os.path.dirname(os.path.dirname(os.path.dirname(skill)))
    out = {}
    for name in r.stdout.split():
        if not name.endswith(".md") or name == "README.md":
            continue
        b = _git(root, "cat-file", "blob", "%s:%s/features/%s" % (sha, skill, name))
        text = b.stdout if b is not None and b.returncode == 0 else ""
        fid, anchors = None, []
        for line in text.splitlines():
            if line.startswith("## "):
                break
            m = re.match(r"^- (id|anchors):\s*(.*)$", line.strip())
            if m and m.group(1) == "id":
                fid = m.group(2).strip()
            elif m:
                for a in m.group(2).split(","):
                    a = a.strip().strip("`").split(":")[0].strip().rstrip("/")
                    if a:
                        anchors.append("%s/%s" % (app, a) if app else a)
        if fid == name[:-3]:
            out[fid] = anchors
    return out


def _inside(path, base):
    real, rb = os.path.realpath(path), os.path.realpath(base)
    return real == rb or real.startswith(rb + os.sep)


def _load_record(path, base):
    if not _inside(path, base) or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return "malformed"
    return doc if isinstance(doc, dict) else "malformed"


def _check_record(doc, feature, sha, verifier, directory, base):
    """None when an evidence.json proves feature at sha for verifier, else a reason."""
    if not isinstance(doc, dict) or doc.get("schema") != EVIDENCE_SCHEMA \
            or doc.get("kind") != "evidence" or doc.get("feature") != feature:
        return "evidence-malformed"
    if doc.get("sha") != sha:
        return "evidence-sha-mismatch"
    arts = doc.get("artifacts")
    if not isinstance(arts, list) or not arts:
        return "evidence-malformed"
    for a in arts:
        rel = a.get("path") if isinstance(a, dict) else None
        if not isinstance(rel, str) or not rel or os.path.isabs(rel) or ".." in rel.split("/"):
            return "evidence-malformed"
        path = os.path.join(directory, rel)
        if not _inside(path, base) or not os.path.isfile(path):
            return "evidence-artifact-missing"
        if CC.sha256_file(path) != a.get("sha256"):
            return "evidence-artifact-altered"
    # A well-formed failure on this commit counts whoever recorded it: the app failed on
    # this commit, and another verifier's pass must not paper over it.
    if doc.get("result") != "pass":
        return "evidence-failed"
    if doc.get("verifier") != verifier:
        return "evidence-verifier-mismatch"
    return None


def evidence_verdict(root, skill, sha, verifier, driver):
    """The evidence predicate for a release commit. Returns {ok, reason, feature,
    features, paths, doctor, observed}: ok only when the verifier is not the release
    driver, the verify skill maps at least one feature at sha, and every mapped feature
    has a passing evidence.json recorded by this verifier at exactly sha (directory name
    and `sha` field), with every artifact present and unaltered, from an instance whose
    doctor.json at sha is ok. Check order is fixed, so a probe sees one reason."""
    out = {"ok": False, "reason": None, "feature": None, "features": [], "paths": [],
           "doctor": [], "observed": None}
    if not verifier:
        out["reason"] = "verifier-missing"
        return out
    if verifier == driver:
        out["reason"] = "driver-is-verifier"
        return out
    fmap = feature_map_at(root, skill, sha)
    if fmap is None:
        out["reason"] = "feature-map-unreadable"
        return out
    feats = sorted(fmap)
    out["features"] = feats
    if not feats:
        out["reason"] = "no-features"
        return out
    base = os.path.join(root, EVIDENCE_DIR)
    for f in feats:
        dirs = sorted(glob.glob(os.path.join(glob.escape(base), "*", f, "*")))
        here = [d for d in dirs if os.path.basename(d) == sha and os.path.isdir(d)]
        if not here:
            out["reason"] = "evidence-stale-sha" if dirs else "evidence-missing"
            out["feature"] = f
            return out
        verdicts = []
        for d in here:
            doc = _load_record(os.path.join(d, "evidence.json"), base)
            why = "evidence-missing" if doc is None else \
                _check_record(doc, f, sha, verifier, d, base)
            verdicts.append((d, doc, why))
        failed = [(d, doc) for d, doc, why in verdicts if why == "evidence-failed"]
        if failed:
            out["reason"], out["feature"] = "evidence-failed", f
            out["observed"] = str(failed[0][1].get("observed") or "")[:TAIL]
            return out
        chosen = next((d for d, _, why in verdicts if why is None), None)
        if chosen is None:
            out["reason"], out["feature"] = verdicts[0][2], f
            return out
        inst = os.path.basename(os.path.dirname(os.path.dirname(chosen)))
        dpath = os.path.join(base, inst, "doctor", sha, "doctor.json")
        doc = _load_record(dpath, base)
        if doc is None:
            out["reason"], out["feature"] = "doctor-missing", f
            return out
        if not isinstance(doc, dict) or doc.get("kind") != "doctor" \
                or doc.get("sha") != sha or doc.get("ok") is not True:
            out["reason"], out["feature"] = "doctor-red", f
            return out
        out["paths"].append(os.path.relpath(os.path.join(chosen, "evidence.json"), root))
        drel = os.path.relpath(dpath, root)
        if drel not in out["doctor"]:
            out["doctor"].append(drel)
    out["ok"] = True
    return out


# ── stage's steps ────────────────────────────────────────────────────────────────
class Stop(Exception):
    """stage must stop (exit 3) without changing the release's status: `reason` is the
    STOP: line's word, `detail` says why, `nxt` is the NEXT: line (or None)."""

    def __init__(self, reason, detail, nxt=None):
        super().__init__(detail)
        self.reason, self.detail, self.nxt = reason, detail, nxt


def _grant_pins(root, rel):
    """(grant path, the recipe digest the release grant pinned, its release.version).
    Stop (exit 3) when the grant prep wrote is missing, unreadable or does not pin what
    it should: only a new prep, with a human accepting a new grant, can fix that."""
    nxt = "a new prep with a human is needed (the release grant cannot be trusted)"
    gid = (rel.grant or {}).get("id")
    if not gid:
        raise Stop("grant-unreadable", "release %s records no grant" % rel.version, nxt)
    path = _grant_path(root, gid)
    st, errs = CC.load_envelope(path)
    if errs or not isinstance(st, dict):
        raise Stop("grant-unreadable", "cannot read the release grant %s" % path, nxt)
    pinned = next((s.get("digest", {}).get("sha256") for s in st.get("subject") or []
                   if isinstance(s, dict) and s.get("name") == RECIPE_PATH), None)
    version = ((st.get("predicate") or {}).get("payload") or {}).get("release", {})
    if not pinned or not isinstance(version, dict) or version.get("version") != rel.version:
        raise Stop("grant-unreadable", "the release grant %s does not pin %s and version %s"
                   % (gid, RECIPE_PATH, rel.version), nxt)
    return path, pinned, version["version"]


def _version_at(root, sha, vfile, keys):
    """The version the {"file","key"} version file holds at commit sha, or None."""
    r = _git(root, "show", "%s:%s" % (sha, vfile))
    if r is None or r.returncode != 0:
        return None
    try:
        return _read_version(r.stdout, keys, vfile)[1]
    except Refused:
        return None


def _resolve_commit(root, rev):
    """The full sha `rev` names, or None."""
    r = _git(root, "rev-parse", "-q", "--verify", "%s^{commit}" % rev)
    return r.stdout.strip() if r is not None and r.returncode == 0 and r.stdout.strip() else None


def find_release_commit(root, tip, base, version, vfile, keys):
    """The merged release commit: walking the default branch's first-parent history from
    `tip` back to `base` (prep's base commit, exclusive), the newest commit at which the
    version file BECOMES `version` (its first parent's version is not `version`). That is
    the release PR's merge (or squash, or rebased) commit. Later work landing on the
    default branch keeps the version but was never in the release PR, so main's tip is
    not the release. None when no such commit lies between tip and base."""
    r = _git(root, "rev-list", "--first-parent", "--max-count=%d" % COMMIT_SCAN, tip)
    if r is None or r.returncode != 0:
        return None
    chain = []
    for sha in r.stdout.split():
        if sha == base:
            break
        chain.append(sha)
    else:
        return None  # base is not on the first-parent chain: the caller passes --commit
    vers = [_version_at(root, c, vfile, keys) for c in chain] + [
        _version_at(root, base, vfile, keys)]
    for i, sha in enumerate(chain):
        if vers[i] == version and vers[i + 1] != version:
            return sha
    return None


def _head(wt):
    r = _git(wt, "rev-parse", "-q", "--verify", "HEAD^{commit}")
    return r.stdout.strip() if r is not None and r.returncode == 0 else None


def _stage_gate(root, rel, action, wt, commit):
    """check-grant for `action` with the grant prep wrote (R18): subject the recipe,
    worktree the stage worktree, whose HEAD must still be the release commit (a verifier
    working there must not move what the branch, ci-config and tag probes judge).
    None on COVERED; else the exit code (3) after printing GATE:/STOP:/NEXT:."""
    head = _head(wt)
    if head != commit:
        print("STOP: head-moved: the stage worktree %s is at %s, not the release commit %s"
              % (wt, head, commit))
        print("NEXT: reset %s to %s, then re-run stage" % (wt, commit))
        rel.log("stop", reason="head-moved", head=head, commit=commit)
        return 3
    rep = _gate(root, action, wt, rel.grant["id"])
    if rep["status"] != "COVERED":
        return _gate_stop(rel, action, rep, RESUME_STAGE)
    print("GATE: %s COVERED" % action)
    rel.log("gate", action=action, status="COVERED", reason=None, ok=True)
    return None


def _stage_fail(rel, step, reason, detail):
    """Fail the stage: status stage_failed with {step, reason, detail}, saved and logged.
    The release is finished; a new prep supersedes it. Its grant is revoked (as prep's
    _cleanup_prep does), so it does not stay live for days covering deploy_staging and
    push_tag for a release nothing will finish. Returns 3."""
    st = rel.stage
    st["failure"] = {"step": step, "reason": reason, "detail": detail}
    rel.set_status("stage_failed", stage=st)
    rel.save()
    rel.log("stage_failed", step=step, reason=reason)
    gid = (rel.grant or {}).get("id")
    if gid:
        try:
            CC.revoke_grant(rel.root, gid)
            rel.log("grant_revoked", id=gid)
        except (OSError, ValueError) as e:
            print("WARNING: could not revoke the release grant %s: %s; revoke it with "
                  "check-grant's revoke-grant --id" % (gid, e))
    print("STAGE: %s fail %s" % (rel.version, reason))
    print("STOP: %s: %s" % (reason, detail))
    return 3


def _add_worktree(root, path, commit, branch=None):
    """git worktree add at `path`, checking out `commit` on a new `branch` (or detached).
    A leftover of an interrupted run is removed first."""
    if os.path.lexists(path):
        _git(root, "worktree", "remove", "--force", path)
        shutil.rmtree(path, ignore_errors=True)
    _git(root, "worktree", "prune")
    if branch:
        _git_ok(root, "worktree", "add", "-q", "-b", branch, path, commit)
    else:
        _git_ok(root, "worktree", "add", "-q", "--detach", path, commit)


def _stage_worktree(root, rel, commit):
    """The stage worktree on release/<v>-stage at the release commit: created on the first
    run, reused on a resume (where its HEAD must still be the commit, checked by every
    gate). Refused when the branch exists but no stage of this release made it."""
    wt = os.path.join(rel.dir, STAGE_WT)
    branch = "release/%s-stage" % rel.version
    if os.path.isdir(wt) and rel.stage.get("worktree") == wt:
        return wt
    # A worktree left by an interrupted run (or one whose directory was deleted) still
    # holds the branch until it is removed and pruned; only then can the branch go.
    if os.path.lexists(wt):
        _git(root, "worktree", "remove", "--force", wt)
        shutil.rmtree(wt, ignore_errors=True)
    _git(root, "worktree", "prune")
    exists = _git(root, "rev-parse", "-q", "--verify", "refs/heads/%s" % branch)
    if exists is not None and exists.returncode == 0:
        if exists.stdout.strip() != commit:
            raise Refused("branch %s already exists at another commit" % branch)
        gone = _git(root, "branch", "-D", branch)  # an earlier run's, at this commit
        if gone is None or gone.returncode != 0:
            raise Refused("cannot delete the stale branch %s: %s"
                          % (branch, gone.stderr.strip() if gone else "git not runnable"))
    _add_worktree(root, wt, commit, branch)
    return wt


def _build_tree(build_dir):
    """{"head", "diff_sha256"} of the build checkout: its HEAD and a sha256 of its tracked
    changes (`git diff --binary HEAD`), recorded at build end and compared before
    deploy_staging. A build may change tracked files; nothing may change them after. None
    when git cannot say."""
    head = _head(build_dir)
    try:
        r = subprocess.run(["git", "-C", build_dir, *GIT_SAFE, "diff", "--no-ext-diff",
                            "--binary", "HEAD", "--"], capture_output=True, timeout=120,
                           env=CC.git_env())
    except (OSError, subprocess.SubprocessError):
        return None
    if head is None or r.returncode != 0:
        return None
    return {"head": head, "diff_sha256": hashlib.sha256(r.stdout).hexdigest()}


def _artifact_sha(build_dir, artifact):
    """sha256 of the {"path"} artifact under the build checkout, or None when it is
    missing, a symlink, or outside the checkout."""
    if not CC.safe_path(artifact):
        return None
    path = os.path.join(build_dir, *artifact.split("/"))
    if os.path.islink(path) or not os.path.isfile(path) or not _inside(path, build_dir):
        return None
    return CC.sha256_file(path)


def _cmd_record(argv, res):
    """{argv, rc, out_tail, err_tail} for one staging command. Tails stay in local state:
    deploy output can carry secrets (spec section 3), so never in a log or an envelope."""
    return {"argv": argv, "rc": res["rc"], "out_tail": res["out_tail"],
            "err_tail": res["err_tail"]}


def _poll_probe(argv, cwd, version, commit, timeout, match=None):
    """Run the version probe every PROBE_INTERVAL seconds until its output reports the
    version or commit (or, with `match`, until match(output) is true), or `timeout`
    seconds pass. (live, {rc, out_tail, err_tail, polls})."""
    if match is None:
        def match(out):
            return probe_reports(out, version, commit)
    deadline = time.monotonic() + timeout
    polls = 0
    while True:
        left = max(1.0, min(PROBE_CMD_TIMEOUT, deadline - time.monotonic()))
        res = run_cmd(argv, cwd, left)
        polls += 1
        last = {"rc": res["rc"], "out_tail": res["out_tail"], "err_tail": res["err_tail"],
                "polls": polls}
        if res["rc"] == 0 and match(res["out_tail"]):
            return True, last
        if time.monotonic() + PROBE_INTERVAL > deadline:
            return False, last
        _sleep(PROBE_INTERVAL)


def cmd_stage(args):
    """Stage the merged release (spec section 2, step 2). Exit 0: built and waiting for a
    verifier (NEXT: dispatch-verifier <commit>), or staged (STAGE: <v> pass <commit>); 2
    refused (no release to stage, bad input); 3 stopped: a gate did not answer COVERED,
    the commit or recipe does not match the grant, the evidence was rejected, the lock
    is held, or the stage failed (status stage_failed)."""
    root = os.path.abspath(args.root)
    if args.evidence and not (args.verifier or "").strip():
        print("RELEASE: refused: --evidence needs --verifier ID (the independent verifier)")
        return 2
    if args.evidence:
        # The same rule as prep's --driver: no control or format character, so a
        # zero-width variant of the driver's id cannot pass as another verifier.
        try:
            args.verifier = _clean_name(args.verifier, "--verifier")
        except Refused as e:
            print("RELEASE: refused: %s" % e)
            return 2
    if not re.match(REMOTE_NAME_RE, args.remote or ""):
        print("RELEASE: refused: --remote %r must be a remote name" % args.remote)
        return 2
    try:
        with run_lock(root):
            return _stage_locked(root, args)
    except Locked:
        print("STOP: locked: another release command holds the run lock")
        return 3


def _failed_stages(root):
    """[(version, failure reason)] for every stage_failed release, oldest name first."""
    out = []
    try:
        names = sorted(os.listdir(_releases_dir(root)))
    except FileNotFoundError:
        return out
    for name in names:
        try:
            r = Release.load(root, name)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if r.status == "stage_failed":
            out.append((name, ((r.stage or {}).get("failure") or {}).get("reason")
                        or "unknown"))
    return out


def _stage_release(root):
    """The one release `stage` may act on: unfinished, and prepped or further along in
    stage. Refused otherwise."""
    busy = unfinished_releases(root)
    ours = [(v, s) for v, s in busy if s in ("prepped", "staging_verify", "staged")]
    if not busy:
        failed = _failed_stages(root)
        if failed:
            raise Refused("; ".join("release %s is stage_failed (%s)" % f for f in failed)
                          + ": a new prep with a human is needed")
    if len(busy) != 1 or len(ours) != 1:
        raise Refused("no single prepped release to stage (unfinished: %s)"
                      % (", ".join("%s (%s)" % (v, s or "no state") for v, s in busy)
                         or "none"))
    try:
        return Release.load(root, ours[0][0])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Refused("cannot load release %s: %s" % (ours[0][0], e))


def _stage_locked(root, args):
    try:
        rel = _stage_release(root)
        if rel.status == "staged":
            print("STAGE: %s pass %s" % (rel.version, rel.release_commit))
            print("NEXT: run deploy with the human")
            return 0
        prep = rel.prep or {}
        if not (prep.get("pushed") and prep.get("pr")):
            raise Refused("release %s has prep steps pending; re-run prep" % rel.version)
        if args.evidence and rel.status != "staging_verify":
            raise Refused("--evidence judges a built release (status staging_verify); "
                          "release %s is %s: run stage first" % (rel.version, rel.status))
        _, pinned, gversion = _grant_pins(root, rel)
        commit = _stage_commit(root, rel, args, pinned, gversion)
        if commit is None:
            return 3
        recipe, problems = load_recipe(root, rev=commit)
        if problems:
            raise Refused("invalid recipe at %s:\n%s" % (commit[:12], "\n".join(
                "  - %s" % p for p in problems)))
    except Refused as e:
        print("RELEASE: refused: %s" % e)
        return 2
    except Stop as e:
        print("STOP: %s: %s" % (e.reason, e.detail))
        if e.nxt:
            print("NEXT: %s" % e.nxt)
        rel.log("stop", reason=e.reason)
        return 3
    try:
        return _stage_steps(root, rel, recipe, commit, args)
    except Refused as e:
        print("RELEASE: refused: %s" % e)
        return 2


def _stage_commit(root, rel, args, pinned, gversion):
    """The release commit, checked against the grant (D7) on every run: the version
    file at the commit says the grant's release.version, and the recipe at the commit
    hashes to the digest the grant pinned (a committed recipe edit can leave the root's
    working recipe, which the gate's `stale` check reads, matching). Nothing from a
    recipe is run before this passes. Raises Stop (exit 3, status unchanged)."""
    try:
        at_base = recipe_sha(root, rev=rel.base_commit)
    except ValueError as e:
        raise Stop("recipe-unreadable", str(e), "re-run stage once git can read %s at %s"
                   % (RECIPE_PATH, rel.base_commit[:12]))
    if at_base != pinned:
        raise Stop("recipe-changed", "the recipe at the base commit %s is not the one the "
                   "grant pinned" % rel.base_commit[:12], "re-run prep with a human")
    base_recipe, problems = load_recipe(root, rev=rel.base_commit)
    if problems:
        raise Refused("invalid recipe at the base commit: %s" % problems[0])
    vfile, keys = _version_key(base_recipe)
    known = (rel.stage or {}).get("commit")
    branch, tip = _default_branch(root)
    merge_next = ("merge the release PR and update local %s (git pull), then run stage"
                  % branch)
    if args.commit:
        commit = _resolve_commit(root, args.commit)
        if commit is None:
            raise Refused("--commit %s names no commit" % args.commit)
        if known and commit != known:
            raise Refused("this release is staging %s; --commit %s differs"
                          % (known, args.commit))
    elif known:
        commit = known
    else:
        commit = find_release_commit(root, tip, rel.base_commit, gversion, vfile, keys)
        if commit is None:
            raise Stop("not-merged", "no commit on %s since %s sets %s to %s"
                       % (branch, rel.base_commit[:12], vfile, gversion),
                       merge_next + " (or pass --commit SHA)")
    # The human's merge is the checkpoint before staging: a --commit (say, the release
    # branch's own tip) must already be on the default branch, or stage would deploy and
    # tag a commit nobody merged.
    anc = _git(root, "merge-base", "--is-ancestor", commit, tip)
    if anc is None or anc.returncode != 0:
        raise Stop("not-merged", "%s is not on %s" % (commit[:12], branch), merge_next)
    got = _version_at(root, commit, vfile, keys)
    if got != gversion:
        raise Stop("version-mismatch", "%s at %s says %r; the grant pins version %s"
                   % (vfile, commit[:12], got, gversion), "stage the merged release commit")
    try:
        at_commit = recipe_sha(root, rev=commit)
    except ValueError as e:
        raise Stop("recipe-changed", str(e), "re-run prep with a human")
    if at_commit != pinned:
        raise Stop("recipe-changed", "the recipe at %s is not the one the grant pinned; "
                   "nothing from it runs" % commit[:12],
                   "a recipe change needs a new prep with a human")
    return commit


def _stage_steps(root, rel, recipe, commit, args):
    """stage's steps from the first unfinished one; each saves its progress."""
    v = rel.version
    if rel.stage is None:
        rel.stage = {"commit": commit}
        rel.release_commit = commit
        rel.recipe_sha = recipe_sha(root, rev=commit)
        rel.save()
        rel.log("stage", commit=commit)
    st = rel.stage
    wt = _stage_worktree(root, rel, commit)
    if st.get("worktree") != wt:
        st["worktree"] = wt
        rel.save()
    values = {"version": v, "commit": commit, "env": STAGE_ENV}
    build_dir = os.path.join(rel.dir, BUILD_WT)
    artifact = recipe["artifact"]
    if not st.get("built"):
        rc = _stage_gate(root, rel, "local_reversible", wt, commit)
        if rc is not None:
            return rc
        _add_worktree(root, build_dir, commit)
        st["build_dir"] = build_dir
        argv = expand(recipe["build"], {"version": v, "commit": commit})
        res = run_cmd(argv, build_dir, CMD_TIMEOUT)
        st["build"] = _cmd_record(argv, res)
        rel.log("build", rc=res["rc"], timed_out=res["timed_out"])
        if res["rc"] != 0:
            return _stage_fail(rel, "build", "build-failed",
                               "build exited %s" % res["rc"])
        if isinstance(artifact, dict):
            sha = _artifact_sha(build_dir, artifact["path"])
            if sha is None:
                return _stage_fail(rel, "build", "artifact-missing", "build made no "
                                   "regular file at %s" % artifact["path"])
            st["artifact"] = {"path": artifact["path"], "sha256": sha}
            rel.artifact_sha = sha
        else:
            # Rebuild honesty (spec section 3): production rebuilds from this source, so
            # staging verifies the same source, not the same bytes.
            st["artifact"] = None
            st["artifact_note"] = "rebuild: staging verifies the same source, not the same bytes"
        tree = _build_tree(build_dir)
        if tree is None:
            return _stage_fail(rel, "build", "build-tree-unreadable",
                               "git cannot describe the build checkout %s" % build_dir)
        st["build_tree"] = tree
        st["built"] = True
        rel.set_status("staging_verify", stage=st)
        rel.save()
    if not rel.evidence:
        if not args.evidence:
            print("STAGE: %s built %s" % (v, commit))
            print("NEXT: dispatch-verifier %s" % commit)
            print("  the verifier runs the verify skill %s's full check set in %s, with "
                  "VERIFY_EVIDENCE_DIR=%s, then: stage --evidence --verifier <its id>"
                  % (recipe["verify_skill"], wt, os.path.join(root, EVIDENCE_DIR)))
            return 0
        rc = _stage_gate(root, rel, "local_reversible", wt, commit)
        if rc is not None:
            return rc
        verifier = args.verifier
        ev = evidence_verdict(root, recipe["verify_skill"].rstrip("/"), commit, verifier,
                              rel.driver)
        rel.log("evidence", ok=ev["ok"], reason=ev["reason"], feature=ev["feature"],
                verifier=verifier, commit=commit, features=ev["features"], paths=ev["paths"])
        if ev["reason"] == "evidence-failed":
            return _stage_fail(rel, "evidence", "evidence-failed", "feature %s failed "
                               "verification at %s" % (ev["feature"], commit[:12]))
        if not ev["ok"]:
            print("STOP: evidence-reject %s%s" % (ev["reason"], " " + ev["feature"]
                                                  if ev["feature"] else ""))
            print("NEXT: dispatch-verifier %s" % commit)
            return 3
        rel.evidence = {"verdict": "pass", "verifier": verifier, "commit": commit,
                        "features": ev["features"], "paths": ev["paths"],
                        "doctor": ev["doctor"], "at": _rfc3339(_now())}
        rel.save()
        print("STAGE: %s evidence %s" % (v, commit))
    timeout = recipe.get("deploy_timeout", DEFAULT_DEPLOY_TIMEOUT)
    if not st.get("deployed"):
        tree = _build_tree(build_dir)
        if tree != st.get("build_tree"):
            return _stage_fail(rel, "deploy_staging", "build-tree-changed",
                               "the build checkout %s moved off %s or its tracked files "
                               "changed since the build" % (build_dir, commit[:12]))
        if st.get("artifact"):
            # the verifier worked in the stage worktree, not here; still, the bytes
            # deploy_staging ships must be the ones built
            if _artifact_sha(build_dir, st["artifact"]["path"]) != st["artifact"]["sha256"]:
                return _stage_fail(rel, "deploy_staging", "artifact-altered",
                                   "%s changed since the build" % st["artifact"]["path"])
        # R20: stage is the grant-driven step an unattended agent runs; while any live
        # grant's headless allowlist could reach production, refuse before staging too.
        # R27: when CI deploys on tags, the tag push `deploy` would run is a production
        # command too (decided as _stage_tag will decide it; unreadable CI counts).
        tagged = CC.ci_tag_triggers(root, commit)
        exposed = exposing_grants(root, recipe, v, commit, _tag_push_spellings(
            args.remote, commit, v) if tagged is None or tagged else None)
        if exposed:
            rel.log("refused", reason="allowlist-exposes-prod", grants=exposed)
            raise Refused("allowlist-exposes-prod: live grant(s) %s let a headless agent "
                          "run deploy_prod or rollback; revoke them (check-grant "
                          "revoke-grant --id) or narrow their --allowedTools"
                          % ", ".join(exposed))
        rc = _stage_gate(root, rel, "deploy_staging", wt, commit)
        if rc is not None:
            return rc
        argv = expand(recipe["deploy_staging"], values)
        if st.get("deploy_started"):
            # An earlier attempt started and never recorded its end (a crash or a kill):
            # staging is grantable and re-deployable, so it runs again, and says so.
            print("STAGE: %s re-running deploy_staging after an interrupted attempt" % v)
            rel.log("deploy_staging_rerun")
        st["deploy_started"] = True
        rel.save()
        rel.log("deploy_staging_started")
        res = run_cmd(argv, build_dir, CMD_TIMEOUT)
        st["deploy"] = _cmd_record(argv, res)
        rel.log("deploy_staging", rc=res["rc"], timed_out=res["timed_out"])
        if res["rc"] != 0:
            return _stage_fail(rel, "deploy_staging", "deploy-failed",
                               "deploy_staging exited %s" % res["rc"])
        st["deployed"] = True
        rel.save()
    if not st.get("live"):
        argv = expand(recipe["version_probe"], values)
        live, last = _poll_probe(argv, build_dir, v, commit, timeout)
        st["probe"] = dict(last, argv=argv)
        rel.log("probe", live=live, polls=last["polls"], rc=last["rc"])
        if not live:
            return _stage_fail(rel, "probe", "timeout", "the staging version probe did not "
                               "report %s or %s within %ss" % (v, commit[:12], timeout))
        st["live"] = True
        rel.save()
    if not st.get("checks_done"):
        st["checks"] = []
        for i, check in enumerate(recipe["staging_checks"]):
            argv = expand(check, values)
            res = run_cmd(argv, build_dir, CMD_TIMEOUT)
            st["checks"].append(_cmd_record(argv, res))
            rel.log("staging_check", index=i, rc=res["rc"], timed_out=res["timed_out"])
            if res["rc"] != 0:
                return _stage_fail(rel, "staging_checks", "check-failed",
                                   "staging check %d exited %s" % (i, res["rc"]))
        st["checks_done"] = True
        rel.save()
    if not st.get("tag"):
        rc = _stage_tag(root, rel, wt, commit, args.remote)
        if rc is not None:
            return rc
    rel.set_status("staged", stage=st)
    rel.save()
    rel.log("staged", commit=commit, tag=st["tag"])
    print("STAGE: %s pass %s" % (v, commit))
    print("NEXT: run deploy with the human")
    return 0


def _stage_tag(root, rel, wt, commit, remote):
    """Push v<version> on the release commit, or hold it (D6). When the CI config at the
    commit may run on a tag -- or git cannot say (R1) -- the tag push is the production
    deploy: no gate is asked, no tag is made (not even locally, where a later `git push
    --tags` would fire it), and tag_deploys is recorded for `deploy`. Otherwise gate
    push_tag and push the explicit, non-forced refspec <commit>:refs/tags/v<version>.
    None when done; else the exit code."""
    st, tag = rel.stage, "v%s" % rel.version
    tagged = CC.ci_tag_triggers(root, commit)
    if tagged is None or tagged:
        rel.tag_deploys = True
        st["tag"] = "held"
        rel.save()
        rel.log("tag", tag=tag, held=True, reason="ci-tag")
        print("STAGE: %s tag-held %s (CI runs on tags: the tag push is the deploy)"
              % (rel.version, tag))
        return None
    rel.tag_deploys = False
    rc = _stage_gate(root, rel, "push_tag", wt, commit)
    if rc is not None:
        return rc
    argv = ["git", "push", remote, "%s:refs/tags/%s" % (commit, tag)]
    ok, res = _run_remote(argv, wt, _safe_config_env(GIT_ALLOW_PROTOCOL=PUSH_PROTOCOLS))
    rel.log("tag", tag=tag, command=argv, returncode=res["rc"], ok=ok)
    if not ok:
        # Staging passed; only the tag is pending. Not stage_failed: a re-run retries it.
        rel.save()
        print("STOP: tag-push-failed: git push of %s exited %s: %s"
              % (tag, res["rc"], one_line(res.get("err_tail", ""))))
        print("NEXT: %s" % RESUME_STAGE)
        return 3
    st["tag"] = "pushed"
    rel.save()
    return None


# ── deploy (spec section 2, step 3; decisions D3/D6/D7/D9/D10, R18/R20) ──────────
PROD_ENV = "production"  # {env} for every production command (stage uses "staging")
DEPLOY_ACTION = "deploy"  # in CC.IRREVERSIBLE_CLASSES: a grant never covers it (A8)
CRASHED = ("deploying", "rolling_back")  # a command was mid-flight in these
NEXT_UNKNOWN = "verify-prod then ask the human"
RESULT_KIND = "https://github.com/dhanesh/agent-skills/skill-contract/release-result/v1"
_SEMVER_LOOSE = re.compile(r"^v?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?)\Z")


def _split_rules(allowed_tools_str):
    """(rules, balanced) for an --allowedTools value: split on commas AND whitespace
    outside parentheses (Claude Code accepts a comma- or space-separated list; a
    Bash(...) glob may itself carry either), each rule stripped, empties dropped.
    balanced is False when a `)` closes nothing or a `(` is never closed -- then the
    split cannot be trusted (a stray `(` swallows every rule after it)."""
    rules, depth, cur, balanced = [], 0, [], True
    for ch in allowed_tools_str or "":
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth:
                depth -= 1
            else:
                balanced = False
        if (ch == "," or ch.isspace()) and depth == 0:
            rules.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    rules.append("".join(cur))
    return [r.strip() for r in rules if r.strip()], balanced and depth == 0


def allowlist_matches(allowed_tools_str, argv):
    """True when a Claude Code --allowedTools value would let a headless agent run argv
    without a prompt. Rules split on commas and whitespace outside parentheses; runs of
    whitespace in a pattern or a command compare as one space; `Bash` and `Bash(*)`
    match every command; `Bash(<glob>)` is matched with fnmatch against shlex.join(argv)
    -- and, failing closed, against " ".join(argv) and both with argv[0] reduced to its
    basename, since an agent may type the command either way; the legacy
    `Bash(<prefix>:*)` form is a prefix match on those same strings (the prefix may
    itself be a glob: fnmatch against prefix + "*"). Other tools never
    match. A malformed list fails CLOSED (R29) and counts as matching: unbalanced
    parentheses anywhere, or a rule that starts with the word `Bash` but is not exactly
    `Bash` or `Bash(...)` -- we cannot tell what Claude Code would make of it. (A longer
    tool name such as BashOutput is another tool, not a malformed Bash rule.) Shared
    with spec-first-planning's write_grant.py (Task 7), which copies it: keep the two
    in step."""
    import fnmatch
    import shlex
    def norm(text):
        return " ".join(text.split())  # runs of whitespace compare as one space
    argv = list(argv)
    forms = {shlex.join(argv), " ".join(argv)}
    if argv:
        short = [os.path.basename(argv[0])] + argv[1:]
        forms |= {shlex.join(short), " ".join(short)}
    forms |= {norm(f) for f in forms}
    rules, balanced = _split_rules(allowed_tools_str)
    if not balanced:
        return True
    for rule in rules:
        if rule in ("Bash", "Bash(*)"):
            return True
        m = re.match(r"^Bash\((.*)\)\Z", rule, re.S)
        if not m:
            if re.match(r"^Bash(?![A-Za-z0-9_])", rule):
                return True  # Bash-something we cannot parse: fail closed
            continue
        pat = norm(m.group(1))
        if pat.endswith(":*"):
            prefix = pat[:-2]  # itself a glob: Bash(*:*) and Bash(npx *:*) match too
            if any(f.startswith(prefix) or fnmatch.fnmatch(f, prefix + "*") for f in forms):
                return True
        elif any(fnmatch.fnmatch(f, pat) for f in forms):
            return True
    return False


ALLOWED_TOOLS_FLAGS = ("--allowedTools", "--allowed-tools")


def agent_cmd_exposes(agent_cmd, argv):
    """True when a re-entry agent_cmd would let its headless agent run argv unprompted:
    its --allowedTools (or --allowed-tools) value -- `--flag=value`, or every argument
    after the flag up to the next option, since Claude Code takes the list as several
    arguments too -- matches argv (allowlist_matches), or it bypasses permission prompts
    altogether (--dangerously-skip-permissions, --permission-mode bypassPermissions)."""
    if not isinstance(agent_cmd, list):
        return False
    values = []
    i = 0
    while i < len(agent_cmd):
        a = agent_cmd[i] if isinstance(agent_cmd[i], str) else ""
        if a == "--dangerously-skip-permissions" or a == "--permission-mode=bypassPermissions":
            return True
        if a == "--permission-mode" and i + 1 < len(agent_cmd) \
                and agent_cmd[i + 1] == "bypassPermissions":
            return True
        flag, eq, val = a.partition("=")
        if flag in ALLOWED_TOOLS_FLAGS:
            if eq:
                values.append(val)
            else:
                j = i + 1
                while j < len(agent_cmd) and isinstance(agent_cmd[j], str) \
                        and not agent_cmd[j].startswith("-"):
                    values.append(agent_cmd[j])
                    j += 1
                i = j
                continue
        i += 1
    return any(allowlist_matches(v, argv) for v in values)


def _tag_push_argv(remote, commit, version):
    """The tag push that is the production deploy when CI deploys on tags (D6), exactly
    as `deploy` runs it: an explicit, non-forced refspec."""
    return ["git", "push", remote, "%s:refs/tags/v%s" % (commit, version)]


def _tag_push_spellings(remote, commit, version):
    """Every way an agent may spell that tag push (R29): the exact refspec `deploy` runs,
    plus `git push <remote> v<version>` and `git push <remote> refs/tags/v<version>` --
    an allowlist reaching any of them reaches the production deploy."""
    return [_tag_push_argv(remote, commit, version),
            ["git", "push", remote, "v%s" % version],
            ["git", "push", remote, "refs/tags/v%s" % version]]


def exposing_grants(root, recipe, version, commit, tag_push=None):
    """Ids of every live grant under root whose reentry.agent_cmd could run the recipe's
    deploy_prod or rollback (expanded with this release's values, and as written), or
    -- when CI deploys on tags -- `tag_push`, the list of tag-push argvs that are the
    deploy (R27, R29: _tag_push_spellings).
    Live = a head no revision supersedes (CC._live_heads) that is not revoked; an
    EXPIRED grant still counts (fail closed: a scheduler may still launch its agent,
    and revoking it is one command)."""
    values = {"version": version, "commit": commit, "env": PROD_ENV}
    argvs = [recipe["deploy_prod"], recipe["rollback"]]
    argvs += [expand(a, values) for a in argvs]
    argvs.extend(tag_push or [])
    out = []
    for _, _, st in CC._live_heads(root):
        payload = st["predicate"].get("payload") or {}
        if payload.get("revoked"):
            continue
        cmd = (payload.get("reentry") or {}).get("agent_cmd") \
            if isinstance(payload.get("reentry"), dict) else None
        if any(agent_cmd_exposes(cmd, a) for a in argvs):
            out.append(st["predicate"]["id"])
    return sorted(out)


def recover_crash(rel):
    """Run under the run lock only. A release left `deploying` or `rolling_back` by a
    command that is no longer running (it held the lock we now hold) has an unknown
    outcome: mark it outcome_unknown, saved and logged. Never re-runs anything -- a
    deploy command is not known to be idempotent (spec section 3). True when the
    release is (now) outcome_unknown; the caller prints STOP/NEXT and exits 3. A plain
    load or `status` must never do this: they would demote a deploy that is live."""
    if rel.status in CRASHED:
        was = rel.status
        rel.set_status("outcome_unknown")
        rel.save()
        rel.log("outcome_unknown", was=was)
        print("STOP: outcome-unknown: release %s was %s when its command stopped; the "
              "command is never re-run" % (rel.version, was))
        print("NEXT: %s" % _next_unknown(rel))
        return True
    if rel.status == "outcome_unknown":
        print("STOP: outcome-unknown: release %s's last production command never recorded "
              "its end; it is never re-run" % rel.version)
        print("NEXT: %s" % _next_unknown(rel))
        return True
    return False


def _next_unknown(rel):
    """The NEXT: line for an outcome_unknown release: verify-prod after a deploy; after
    a rollback (rel.rollback is set before it runs) a human checks production by hand."""
    return NEXT_ROLLBACK_UNKNOWN if getattr(rel, "rollback", None) else NEXT_UNKNOWN


def _probe_target(out):
    """{"version", "commit"} that a production probe's output reports, or None."""
    version = commit = None
    for tok in _PROBE_TOKEN.findall(out or ""):
        tok = tok.rstrip(".")
        m = _SEMVER_LOOSE.match(tok)
        if m and version is None:
            version = m.group(1)
        elif _HEX.match(tok.lower()) and commit is None and not tok.isdigit():
            commit = tok.lower()
    return {"version": version, "commit": commit} if (version or commit) else None


def _last_result_target(root, version):
    """{"version", "commit"} of what the newest release-result/v1 under root left in
    production (another release's), or None. Task 6 writes that kind: outcome
    "verified" leaves its own version; "rolled_back" leaves its rollback_target."""
    best = None
    d = CC.envelope_dir(root)
    try:
        names = sorted(os.listdir(d))
    except FileNotFoundError:
        return None
    for name in names:
        if not name.endswith(".json"):
            continue
        st, err = CC.load_envelope(os.path.join(d, name))
        if err or not isinstance(st, dict) or st.get("predicateType") != RESULT_KIND \
                or CC.check_statement(st):
            continue
        pred = st["predicate"]
        p = pred.get("payload") or {}
        left = p.get("rollback_target") if p.get("outcome") == "rolled_back" else p
        if not isinstance(left, dict) or p.get("version") == version:
            continue
        v, c = left.get("version"), left.get("commit")
        if not (isinstance(v, str) or isinstance(c, str)):
            continue
        if best is None or pred["generatedAtTime"] > best[0]:
            best = (pred["generatedAtTime"], {"version": v if isinstance(v, str) else None,
                                              "commit": c if isinstance(c, str) else None})
    return best[1] if best else None


def rollback_target(root, rel, recipe, build_dir):
    """What production runs now (spec section 2): the production version_probe's answer
    (one poll, bounded by PROBE_CMD_TIMEOUT and deploy_timeout), else the newest
    release-result/v1's version, else source "none". A probe already reporting THIS
    release is no target to roll back to, so it falls through."""
    values = {"version": rel.version, "commit": rel.release_commit, "env": PROD_ENV}
    timeout = min(PROBE_CMD_TIMEOUT, recipe.get("deploy_timeout", DEFAULT_DEPLOY_TIMEOUT))
    res = run_cmd(expand(recipe["version_probe"], values), build_dir, timeout)
    rel.log("rollback_probe", rc=res["rc"], timed_out=res["timed_out"])
    if res["rc"] == 0 and not probe_reports(res["out_tail"], rel.version, rel.release_commit):
        got = _probe_target(res["out_tail"])
        if got:
            return dict(got, source="probe")
    got = _last_result_target(root, rel.version)
    if got:
        return dict(got, source="release-result")
    return {"source": "none", "version": None, "commit": None}


def _deploy_release(root):
    """The one unfinished release `deploy` may act on. Refused otherwise."""
    busy = unfinished_releases(root)
    if len(busy) != 1:
        raise Refused("no single unfinished release to deploy (unfinished: %s)"
                      % (", ".join("%s (%s)" % (v, s or "no state") for v, s in busy)
                         or "none"))
    try:
        return Release.load(root, busy[0][0])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Refused("cannot load release %s: %s" % (busy[0][0], e))


def cmd_deploy(args):
    """The production deploy (spec section 2, step 3). Exit 0 deployed (NEXT: verify-prod);
    2 refused (nothing ran, status unchanged); 3 waiting for the human's yes
    (awaiting_deploy), the deploy failed (prod_failed), the outcome of an earlier run is
    unknown, or the lock is held."""
    root = os.path.abspath(args.root)
    name = None
    try:
        if args.approved_by is not None:
            name = _clean_name(args.approved_by, "--approved-by")
        if not re.match(REMOTE_NAME_RE, args.remote or ""):
            raise Refused("--remote %r must be a remote name" % args.remote)
    except Refused as e:
        print("RELEASE: refused: %s" % e)
        return 2
    try:
        with run_lock(root):
            return _deploy_locked(root, args, name)
    except Locked:
        print("STOP: locked: another release command holds the run lock")
        return 3


def _deploy_refuse(rel, reason, detail):
    """Print and log a refusal; nothing ran and the status is unchanged. Returns 2."""
    print("RELEASE: refused: %s: %s" % (reason, detail))
    rel.log("refused", reason=reason)
    return 2


def _deploy_locked(root, args, name):
    try:
        rel = _deploy_release(root)
    except Refused as e:
        print("RELEASE: refused: %s" % e)
        return 2
    if recover_crash(rel):
        return 3
    v, commit, st = rel.version, rel.release_commit, rel.stage or {}
    if rel.status not in ("staged", "awaiting_deploy"):
        return _deploy_refuse(rel, "not-staged", "release %s is %s, not staged"
                              % (v, rel.status))
    ev = rel.evidence or {}
    if not commit or ev.get("verdict") != "pass" or ev.get("commit") != commit \
            or st.get("commit") != commit:
        return _deploy_refuse(rel, "evidence", "no passing staging evidence for the "
                              "release commit %s" % commit)
    recipe, problems = load_recipe(root, rev=commit)
    try:
        at_commit = recipe_sha(root, rev=commit)
    except ValueError:
        at_commit = None
    if problems or not rel.recipe_sha or at_commit != rel.recipe_sha:
        return _deploy_refuse(rel, "recipe-changed", "the recipe at %s is not the one "
                              "staged (sha %s)" % (commit[:12], rel.recipe_sha))
    build_dir = os.path.join(rel.dir, BUILD_WT)
    changed = _build_changed(rel, build_dir)
    if changed:
        return _deploy_refuse(rel, *changed)
    wt = os.path.join(rel.dir, STAGE_WT)
    rep = _gate(root, DEPLOY_ACTION, wt, (rel.grant or {}).get("id") or "")
    print("GATE: %s %s %s" % (DEPLOY_ACTION, rep["status"], rep["reason"]))
    rel.log("gate", action=DEPLOY_ACTION, status=rep["status"], reason=rep["reason"],
            ok=rep["status"] != "COVERED")
    if rep["status"] == "COVERED":
        return _deploy_refuse(rel, "deploy-covered", "check-grant answered COVERED for "
                              "deploy, which no grant may cover (A8): a checker bug")
    exposed = exposing_grants(root, recipe, v, commit, _tag_push_spellings(
        args.remote, commit, v) if rel.tag_deploys else None)
    if exposed:
        return _deploy_refuse(rel, "allowlist-exposes-prod", "live grant(s) %s let a "
                              "headless agent run the production deploy (deploy_prod, "
                              "rollback, or the deploying tag push); revoke them "
                              "(check-grant revoke-grant --id) or narrow their "
                              "--allowedTools" % ", ".join(exposed))
    values = {"version": v, "commit": commit, "env": PROD_ENV}
    tag = "v%s" % v
    if rel.tag_deploys:
        argv = _tag_push_argv(args.remote, commit, v)
    else:
        argv = expand(recipe["deploy_prod"], values)
    if args.unattended or name is None:
        _deploy_summary(rel, recipe, argv, None, None)
        rel.set_status("awaiting_deploy")
        rel.save()
        rel.log("awaiting_deploy", unattended=bool(args.unattended))
        print("STOP: waiting-human")
        print("NEXT: run deploy with the human")
        return 3
    target = rollback_target(root, rel, recipe, build_dir)
    # M1: the probe ran in wt-build; what deploys from there must still be what staged
    changed = _build_changed(rel, build_dir)
    if changed:
        return _deploy_refuse(rel, *changed)
    noop = rel.tag_deploys and _remote_tag(root, wt, args.remote, tag) == commit
    approval = {"name": name, "status": "CLAIMED"}
    _deploy_summary(rel, recipe, argv, target, approval)
    if noop:
        print("  note: %s is already on %s at %s: the push will be a no-op and CI may "
              "not run again" % (tag, args.remote, commit[:12]))
    rel.set_status("deploying", approved_by=approval, rollback_target=target)
    rel.save()
    rel.log("deploying", approved_by=approval, rollback_target=target, command=argv)
    if rel.tag_deploys:
        ok, res = _run_remote(argv, wt if os.path.isdir(wt) else root,
                              _safe_config_env(GIT_ALLOW_PROTOCOL=PUSH_PROTOCOLS))
        rc, timed_out = res["rc"], res["timed_out"]
        if ok:
            st["tag"] = "pushed"
    else:
        res = run_cmd(argv, build_dir, CMD_TIMEOUT)
        rc, timed_out = res["rc"], res["timed_out"]
        st["deploy_prod"] = _cmd_record(argv, res)  # tails stay in local state only
    rel.log("deploy_prod", rc=rc, timed_out=timed_out, tag=rel.tag_deploys or False)
    what = "the tag push" if rel.tag_deploys else "deploy_prod"
    if timed_out:
        # R25: killed mid-flight, it may have reached production (or the remote): the
        # outcome is unknown, never a known failure, and it is never re-run.
        rel.set_status("outcome_unknown", stage=st)
        rel.save()
        print("DEPLOY: %s unknown %s" % (v, commit))
        print("STOP: outcome-unknown: %s timed out and was killed; production may be "
              "part-way deployed" % what)
        print("NEXT: %s" % NEXT_UNKNOWN)
        return 3
    if rc != 0:
        rel.set_status("prod_failed", stage=st)
        rel.save()
        print("DEPLOY: %s fail %s" % (v, commit))
        print("STOP: deploy-failed: %s %s" % (what, "exited %s" % rc if rc is not None
                                              else "could not start: %s"
                                              % one_line(res.get("err_tail", ""))))
        print("NEXT: %s" % NEXT_UNKNOWN)
        return 3
    rel.set_status("deployed", stage=st)
    rel.save()
    rel.log("deployed", commit=commit, noop=bool(noop))
    print("DEPLOY: %s deployed %s%s" % (v, commit, " (no-op: %s was already on %s; CI may "
                                        "not run again)" % (tag, args.remote) if noop else ""))
    print("NEXT: verify-prod")
    return 0


def _build_changed(rel, build_dir):
    """None when wt-build is still the staged build (HEAD and tracked diff, and the
    {path} artifact's sha); else (reason, detail) for a refusal."""
    st = rel.stage or {}
    if _build_tree(build_dir) != st.get("build_tree"):
        return ("build-tree-changed", "the build checkout %s moved off %s or its tracked "
                "files changed since stage" % (build_dir, (rel.release_commit or "")[:12]))
    art = st.get("artifact")
    if art and (_artifact_sha(build_dir, art["path"]) != art["sha256"]
                or rel.artifact_sha != art["sha256"]):
        return ("artifact-altered", "%s changed since stage" % art["path"])
    return None


def _remote_tag(root, wt, remote, tag):
    """The commit `tag` points at on `remote` (peeled), or None when it is absent or git
    cannot say. A read (git ls-remote), run only after the human's yes."""
    ok, res = _run_remote(["git", "ls-remote", remote, "refs/tags/%s" % tag,
                           "refs/tags/%s^{}" % tag], wt if os.path.isdir(wt) else root,
                          _safe_config_env(GIT_ALLOW_PROTOCOL=PUSH_PROTOCOLS))
    if not ok:
        return None
    found = {}
    for line in (res.get("out_tail") or "").splitlines():
        parts = line.split()
        if len(parts) == 2:
            found[parts[1]] = parts[0]
    return found.get("refs/tags/%s^{}" % tag) or found.get("refs/tags/%s" % tag)


def _deploy_summary(rel, recipe, argv, target, approval):
    """What the human says yes to (spec section 2): version, commit, evidence, recipe
    sha, the exact deploy and rollback argv, the rollback target, and the CLAIMED yes.
    `target` None: not probed yet (nothing runs before the yes)."""
    import shlex
    ev = rel.evidence or {}
    print("DEPLOY: %s %s" % (rel.version, rel.release_commit))
    print("  evidence: %d evidence records (%s) by %s at %s"
          % (len(ev.get("paths") or []), ", ".join(ev.get("features") or []),
             ev.get("verifier"), ev.get("commit")))
    print("  recipe: sha256 %s" % rel.recipe_sha)
    art = (rel.stage or {}).get("artifact")
    print("  artifact: %s" % ("%s sha256 %s" % (art["path"], art["sha256"]) if art else
                              "rebuild: staging verified the same source, not the same bytes"))
    print("  deploy: %s%s" % (shlex.join(argv), " (CI deploys on this tag: the push is the "
                                                "production deploy)" if rel.tag_deploys else ""))
    if target is None:
        print("  rollback: %s (target probed when the human says yes)"
              % shlex.join(recipe["rollback"]))
    elif target["source"] == "none":
        print("  rollback: no rollback target: no previous release, nothing to roll back to")
    else:
        tv = {"env": PROD_ENV}
        tv.update({k: target[k] for k in ("version", "commit") if target.get(k)})
        print("  rollback: %s (target %s %s, from %s)"
              % (shlex.join(expand(recipe["rollback"], tv)), target.get("version") or "-",
                 target.get("commit") or "-", target["source"]))
    if approval:
        print("  approval: %s (%s)" % (approval["name"], approval["status"]))


# ── verify-prod and rollback (spec section 2, step 4; decisions D4/D11) ──────────
PROD_WT = "wt-prod"  # the isolated checkout of the release commit prod commands run in
NEXT_ROLLBACK = "ask the human to roll back"
# After a rollback that did not provably land (or died mid-flight): verify-prod judges a
# deploy, not a rollback, and a second rollback needs a fresh in-session yes (R32).
NEXT_ROLLBACK_UNKNOWN = "check production by hand; rollback again only with the human's yes"
# The statuses verify-prod judges: a deploy that finished, one whose outcome is unknown
# (a crash or a timeout mid-deploy), and a deploy that failed with no verify-prod
# judgement yet (deploy's own NEXT: line sends it here). A prod_failed that verify-prod
# itself judged is final: a flaky check must not be retried into `verified`.
VERIFIABLE = ("deployed", "outcome_unknown", "prod_failed")
ROLLBACKABLE = ("prod_failed", "outcome_unknown")


def _reports_target(out, target):
    """True when a probe's output reports the rollback target's version or commit. A
    target with neither reports nothing (probe_reports would match a bare `v`)."""
    t = target or {}
    v, c = t.get("version"), t.get("commit")
    if not (isinstance(v, str) and v) and not (isinstance(c, str) and c):
        return False
    return probe_reports(out, v if isinstance(v, str) and v else "\0",
                         c if isinstance(c, str) and c else "\0")


def _prod_release(root, what):
    """The one unfinished release; Refused when there is not exactly one."""
    busy = unfinished_releases(root)
    if len(busy) != 1:
        raise Refused("no single unfinished release to %s (unfinished: %s)"
                      % (what, ", ".join("%s (%s)" % (v, s or "no state") for v, s in busy)
                         or "none"))
    try:
        return Release.load(root, busy[0][0])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Refused("cannot load release %s: %s" % (busy[0][0], e))


def _prod_recipe(root, rel):
    """The recipe pinned at the release commit; Refused unless it is the one staged."""
    commit = rel.release_commit
    if not commit:
        raise Refused("release %s has no release commit" % rel.version)
    recipe, problems = load_recipe(root, rev=commit)
    try:
        at_commit = recipe_sha(root, rev=commit)
    except ValueError:
        at_commit = None
    if problems or not rel.recipe_sha or at_commit != rel.recipe_sha:
        raise Refused("recipe-changed: the recipe at %s is not the one staged (sha %s)"
                      % (commit[:12], rel.recipe_sha))
    return recipe


def _prod_checkout(root, rel):
    """A fresh, detached checkout of the release commit (wt-prod) for production
    commands: isolated from the build checkout and from the user's working tree."""
    path = os.path.join(rel.dir, PROD_WT)
    _add_worktree(root, path, rel.release_commit)
    return path


def _revoke_release_grant(rel):
    """The release is finished: revoke its grant (as _stage_fail and _cleanup_prep do),
    so it does not stay live covering staging deploys and tag pushes for days."""
    gid = (rel.grant or {}).get("id")
    if not gid:
        return
    try:
        CC.revoke_grant(rel.root, gid)
        rel.log("grant_revoked", id=gid)
    except (OSError, ValueError) as e:
        print("WARNING: could not revoke the release grant %s: %s; revoke it with "
              "check-grant's revoke-grant --id" % (gid, e))


def _rc_only(rec):
    """{"rc"} of a command record, or None: never a tail, never an argv."""
    return {"rc": rec.get("rc")} if isinstance(rec, dict) else None


def _probe_only(rec):
    """{"rc", "polls", "live"} of a probe record, or None."""
    if not isinstance(rec, dict):
        return None
    return {"rc": rec.get("rc"), "polls": rec.get("polls") or 1, "live": bool(rec.get("live"))}


def result_payload(rel, outcome):
    """The release-result/v1 payload (schemas/release-result.v1.json), built field by
    field from metadata and exit codes. Never rel.to_dict() or rel.stage wholesale: the
    stage, deploy, prod and rollback records hold output tails, which can carry secrets
    and stay in the local state (spec section 3). log_sha256/log_bytes are filled in by
    the caller once the terminal event is in the log."""
    st, ev = rel.stage or {}, rel.evidence or {}
    sprobe = st.get("probe")
    payload = {
        "version": rel.version,
        "commit": rel.release_commit,
        "recipe_sha": rel.recipe_sha,
        "artifact_sha": rel.artifact_sha,
        "staging": {
            "verdict": ev.get("verdict"), "verifier": ev.get("verifier"),
            "commit": ev.get("commit"), "features": list(ev.get("features") or []),
            "evidence_paths": list(ev.get("paths") or []),
            "probe": ({"rc": sprobe.get("rc"), "polls": sprobe.get("polls") or 1}
                      if isinstance(sprobe, dict) else None),
            "checks": [_rc_only(c) for c in st.get("checks") or []]},
        "production": None,
        "rollback_target": ({k: (rel.rollback_target or {}).get(k)
                             for k in ("source", "version", "commit")}
                            if rel.rollback_target else None),
        "approved_by": ({"name": rel.approved_by.get("name"), "status": "CLAIMED"}
                        if isinstance(rel.approved_by, dict) else None),
        "rollback": None,
        "log_sha256": None,
        "log_bytes": None,
        "outcome": outcome,
    }
    if not st.get("artifact"):
        payload["artifact_note"] = "rebuild: staging verified the same source, not the same bytes"
    if isinstance(rel.prod, dict):
        p = rel.prod
        payload["production"] = {
            "deploy": _rc_only(st.get("deploy_prod")),
            "probe": _probe_only(p.get("probe")), "health": _rc_only(p.get("health")),
            "smoke": [_rc_only(c) for c in p.get("smoke") or []],
            "failure": (p.get("failure") or {}).get("reason")}
    if isinstance(rel.rollback, dict):
        r = rel.rollback
        payload["rollback"] = {"approved_by": {"name": (r.get("approved_by") or {}).get("name"),
                                               "status": "CLAIMED"},
                               "rc": r.get("rc"), "probe": _probe_only(r.get("probe"))}
    return payload


def _result_statement(root, rel, payload):
    """The release-result/v1 statement for `payload`, with subjects the recipe (the
    digest recorded for the release commit), the intent file, and the release log by
    payload["log_sha256"] (the digest of its first log_bytes bytes)."""
    base = "%s/%s" % (RELEASES_DIR, rel.version)
    intent_rel = "%s/%s" % (base, INTENT_FILE)
    st = CC.build_statement(RESULT_KIND, SKILL_NAME, SKILL_VERSION, root, [], payload)
    subjects = [{"name": RECIPE_PATH, "digest": {"sha256": rel.recipe_sha}}]
    if os.path.isfile(os.path.join(root, *intent_rel.split("/"))):
        subjects.append(CC.pin(root, intent_rel))
    subjects.append({"name": "%s/release-log.jsonl" % base,
                     "digest": {"sha256": payload["log_sha256"]}})
    st["subject"] = subjects
    return st


def _payload_problems(payload):
    """What makes a payload one this tool must not publish, beyond the checker's C1-C10:
    staging evidence that is not a pass (deploy refuses without one, so this is a bug)."""
    if (payload.get("staging") or {}).get("verdict") != "pass":
        return ["staging.verdict is %r, not \"pass\"" % (payload.get("staging") or {}
                                                          ).get("verdict")]
    return []


def write_result(root, rel, outcome):
    """Finish the release at `outcome` ("verified" or "rolled_back") and write its
    release-result/v1. The statement is checked first, with a shape-only log digest, so
    a statement the checker would refuse never leaves a terminal event in the log. Then
    the terminal event is logged, the log digested up to and including it, and the
    envelope written; then the status is set and saved and the release grant revoked
    (logged after the digested prefix).

    The status is set to `outcome` either way, because it is what production did: a
    failure to build or write the envelope (checker violation, OSError) is recorded as
    rel.result = {"outcome", "error"} with a `result_failed` event, and returns None
    after printing STOP: result-failed. A rollback whose envelope failed is therefore
    rolled_back with a note, never left rolling_back for recover_crash to demote into
    an "unknown" outcome it is not. Returns the envelope path on success."""
    payload = result_payload(rel, outcome)
    payload["log_sha256"], payload["log_bytes"] = "0" * 64, 1  # shape only, for the check
    problems = ["C%d: %s" % v for v in CC.check_statement(_result_statement(root, rel, payload))]
    problems += _payload_problems(payload)
    path = st = None
    if not problems:
        rel.log(outcome, commit=rel.release_commit)
        try:
            with open(rel.log_path, "rb") as f:
                data = f.read()
            payload["log_sha256"] = hashlib.sha256(data).hexdigest()
            payload["log_bytes"] = len(data)
            st = _result_statement(root, rel, payload)
            path = CC.write_envelope(root, st)
        except (OSError, ValueError) as e:
            problems = ["cannot write the envelope: %s" % e]
    if problems:
        rel.set_status(outcome, result={"outcome": outcome, "error": problems[0]})
        rel.save()
        rel.log("result_failed", outcome=outcome, error=problems[0])
        _revoke_release_grant(rel)
        for p in problems:
            print("FAIL: %s" % p)
        print("STOP: result-failed: production is %s, but no release-result envelope was "
              "written" % outcome.replace("_", " "))
        print("NEXT: fix what stopped it; the release is finished, and its state.json "
              "and release-log.jsonl hold the record")
        return None
    rel.set_status(outcome, result={"id": st["predicate"]["id"],
                                    "path": os.path.relpath(path, root),
                                    "sha256": CC.sha256_file(path), "outcome": outcome})
    rel.save()
    rel.log("result", id=st["predicate"]["id"], path=os.path.relpath(path, root))
    _revoke_release_grant(rel)
    return path


def _demote_crash(rel):
    """Under the run lock: a release left `deploying` or `rolling_back` by a command no
    longer running becomes outcome_unknown (saved, logged); its command never re-runs."""
    if rel.status in CRASHED:
        was = rel.status
        rel.set_status("outcome_unknown")
        rel.save()
        rel.log("outcome_unknown", was=was)
        print("PROD: %s outcome-unknown (was %s; the command is never re-run)"
              % (rel.version, was))


def cmd_verify_prod(args):
    """Prove production runs the release (spec section 2, step 4). Exit 0 verified
    (release-result/v1 written); 2 refused (nothing ran); 3 prod_failed (NEXT: ask the
    human to roll back), or the lock is held."""
    root = os.path.abspath(args.root)
    try:
        with run_lock(root):
            return _verify_locked(root)
    except Locked:
        print("STOP: locked: another release command holds the run lock")
        return 3


def _verify_locked(root):
    try:
        rel = _prod_release(root, "verify")
        _demote_crash(rel)
        if rel.status not in VERIFIABLE:
            raise Refused("release %s is %s; verify-prod judges a deployed release, or one "
                          "whose deploy outcome is unknown" % (rel.version, rel.status))
        if rel.prod or rel.rollback:
            # R32: verify-prod judges a deploy once. After its own judgement, or once a
            # rollback ran (or died mid-flight), production is a human's call: a re-run
            # must not turn a failed or half-rolled-back release into `verified`.
            raise Refused("release %s already has %s; verify-prod judges a deploy once. %s"
                          % (rel.version, "a rollback attempt" if rel.rollback else
                             "a verify-prod judgement (%s)" % (
                                 (rel.prod.get("failure") or {}).get("reason") or "none"),
                             "Check production by hand; rollback only with the human's yes"
                             if rel.rollback else "Ask the human to roll back"))
        recipe = _prod_recipe(root, rel)
        cwd = _prod_checkout(root, rel)
    except Refused as e:
        print("RELEASE: refused: %s" % e)
        return 2
    v, commit = rel.version, rel.release_commit
    values = {"version": v, "commit": commit, "env": PROD_ENV}
    timeout = recipe.get("deploy_timeout", DEFAULT_DEPLOY_TIMEOUT)
    prod = {"probe": None, "health": None, "smoke": [], "failure": None}
    rel.prod = prod
    rel.log("verify_prod", status=rel.status)
    argv = expand(recipe["version_probe"], values)
    live, last = _poll_probe(argv, cwd, v, commit, timeout)
    prod["probe"] = dict(last, argv=argv, live=live)
    rel.log("prod_probe", live=live, polls=last["polls"], rc=last["rc"])
    if not live:
        old = last["rc"] == 0 and _reports_target(last["out_tail"], rel.rollback_target)
        other = last["rc"] == 0 and _probe_target(last["out_tail"]) is not None
        reason = "wrong-version" if other and not old else "not-live"
        return _prod_fail(rel, recipe, reason, "the production version probe did not "
                          "report %s or %s within %ss" % (v, commit[:12], timeout))
    hargv = expand(recipe["health"], values)
    res = run_cmd(hargv, cwd, CMD_TIMEOUT)
    prod["health"] = _cmd_record(hargv, res)
    rel.log("prod_health", rc=res["rc"], timed_out=res["timed_out"])
    if res["rc"] != 0:
        return _prod_fail(rel, recipe, "health-failed", "health exited %s" % res["rc"])
    for i, check in enumerate(recipe["prod_smoke"]):  # prod_smoke only (D11)
        cargv = expand(check, values)
        res = run_cmd(cargv, cwd, CMD_TIMEOUT)
        prod["smoke"].append(_cmd_record(cargv, res))
        rel.log("prod_smoke", index=i, rc=res["rc"], timed_out=res["timed_out"])
        if res["rc"] != 0:
            return _prod_fail(rel, recipe, "smoke-failed",
                              "prod_smoke check %d exited %s" % (i, res["rc"]))
    rel.save()
    path = write_result(root, rel, "verified")
    if path is None:
        return 3
    print("PROD: %s verified" % v)
    print("RESULT: %s" % os.path.relpath(path, root))
    return 0


def _prod_fail(rel, recipe, reason, detail):
    """prod_failed with the reason; shows what failed and the rollback the human may say
    yes to. Never rolls back by itself (D4). Returns 3."""
    import shlex
    rel.prod["failure"] = {"reason": reason, "detail": detail}
    rel.set_status("prod_failed", prod=rel.prod)
    rel.save()
    rel.log("prod_failed", reason=reason)
    print("PROD: %s fail %s" % (rel.version, reason))
    print("STOP: prod-failed: %s" % detail)
    target = rel.rollback_target or {}
    if target.get("source") in (None, "none"):
        print("  rollback: no rollback target: no previous release, nothing to roll back to")
        print("NEXT: ask the human (there is nothing to roll back to)")
        return 3
    try:
        shown = shlex.join(_rollback_argv(recipe, target))
    except Refused as e:
        shown = "cannot be expanded: %s" % e
    print("  rollback: %s (target %s %s, from %s)" % (shown, target.get("version") or "-",
                                                     target.get("commit") or "-",
                                                     target["source"]))
    print("NEXT: %s" % NEXT_ROLLBACK)
    return 3


def _rollback_argv(recipe, target):
    """The recipe's rollback argv with {version}/{commit} from the target ({env}
    production). Refused when it needs a value the target lacks, or a value is unsafe."""
    values = {"env": PROD_ENV}
    values.update({k: target[k] for k in ("version", "commit")
                   if isinstance(target.get(k), str) and target[k]})
    needed = {m.group(1) for a in recipe["rollback"] for m in _EXPAND_TOKEN.finditer(a)}
    missing = sorted(needed - set(values))
    if missing:
        raise Refused("target-incomplete: the rollback needs {%s}, which the target lacks"
                      % "}, {".join(missing))
    try:
        return expand(recipe["rollback"], values)
    except ValueError as e:
        raise Refused("target-unsafe: %s" % e)


def cmd_rollback(args):
    """Roll production back to the release's rollback target, only with the human's yes
    (D4; class deploy, never grantable). Exit 0 rolled_back (release-result/v1 written);
    2 refused (no target, wrong status, covered gate, exposed allowlist); 3 waiting for
    the human's yes, the rollback's outcome is unknown, or the lock is held.

    A rollback that ends outcome_unknown (non-zero exit, timeout, the probe never
    reporting the target, or a crash mid-flight that the next locked command demotes) is
    never re-run by this tool. It may be run again only by a new `rollback` invocation
    carrying a fresh in-session yes from the human (--approved-by), after they have
    checked production by hand; verify-prod refuses such a release (R32)."""
    root = os.path.abspath(args.root)
    name = None
    try:
        if args.approved_by is not None:
            name = _clean_name(args.approved_by, "--approved-by")
    except Refused as e:
        print("RELEASE: refused: %s" % e)
        return 2
    try:
        with run_lock(root):
            return _rollback_locked(root, args, name)
    except Locked:
        print("STOP: locked: another release command holds the run lock")
        return 3


def _rollback_locked(root, args, name):
    import shlex
    try:
        rel = _prod_release(root, "roll back")
    except Refused as e:
        print("RELEASE: refused: %s" % e)
        return 2
    if rel.status in CRASHED and recover_crash(rel):
        return 3  # a rollback (or deploy) died mid-flight: never re-run by itself
    v = rel.version
    if rel.status not in ROLLBACKABLE:
        return _deploy_refuse(rel, "not-failed", "release %s is %s; rollback needs "
                              "prod_failed or outcome_unknown" % (v, rel.status))
    target = rel.rollback_target or {}
    if target.get("source") in (None, "none"):
        print("ROLLBACK: no target")
        print("RELEASE: refused: release %s recorded no rollback target: no previous "
              "release, nothing to roll back to" % v)
        rel.log("refused", reason="no-target")
        return 2
    try:
        recipe = _prod_recipe(root, rel)
        argv = _rollback_argv(recipe, target)
    except Refused as e:
        print("RELEASE: refused: %s" % e)
        rel.log("refused", reason=str(e).split(":")[0])
        return 2
    print("ROLLBACK: %s to %s %s (from %s)" % (v, target.get("version") or "-",
                                               target.get("commit") or "-", target["source"]))
    print("  rollback: %s" % shlex.join(argv))
    if args.unattended or name is None:
        rel.log("rollback_waiting", unattended=bool(args.unattended))
        print("STOP: waiting-human")
        print("NEXT: run rollback with the human")
        return 3
    wt = os.path.join(rel.dir, STAGE_WT)
    rep = _gate(root, DEPLOY_ACTION, wt, (rel.grant or {}).get("id") or "")
    print("GATE: %s %s %s" % (DEPLOY_ACTION, rep["status"], rep["reason"]))
    rel.log("gate", action=DEPLOY_ACTION, status=rep["status"], reason=rep["reason"],
            ok=rep["status"] != "COVERED")
    if rep["status"] == "COVERED":
        return _deploy_refuse(rel, "deploy-covered", "check-grant answered COVERED for "
                              "deploy, which no grant may cover (A8): a checker bug")
    exposed = exposing_grants(root, recipe, v, rel.release_commit, [argv])
    if exposed:
        return _deploy_refuse(rel, "allowlist-exposes-prod", "live grant(s) %s let a "
                              "headless agent run the rollback or the production deploy; "
                              "revoke them (check-grant revoke-grant --id) or narrow their "
                              "--allowedTools" % ", ".join(exposed))
    try:
        cwd = _prod_checkout(root, rel)
    except Refused as e:
        print("RELEASE: refused: %s" % e)
        return 2
    approval = {"name": name, "status": "CLAIMED"}
    record = {"approved_by": approval, "argv": argv, "target": dict(target), "rc": None,
              "probe": None}
    rel.set_status("rolling_back", rollback=record)
    rel.save()  # before the command runs: a crash leaves rolling_back, never a re-run
    rel.log("rolling_back", approved_by=approval, target=dict(target), command=argv)
    res = run_cmd(argv, cwd, CMD_TIMEOUT)
    record.update(rc=res["rc"], out_tail=res["out_tail"], err_tail=res["err_tail"],
                  timed_out=res["timed_out"])
    rel.log("rollback_cmd", rc=res["rc"], timed_out=res["timed_out"])
    if res["rc"] != 0:
        return _rollback_unknown(rel, "rollback %s" % ("timed out and was killed"
                                                       if res["timed_out"] else
                                                       "exited %s" % res["rc"]))
    values = {"version": v, "commit": rel.release_commit, "env": PROD_ENV}
    pargv = expand(recipe["version_probe"], values)
    timeout = recipe.get("deploy_timeout", DEFAULT_DEPLOY_TIMEOUT)
    live, last = _poll_probe(pargv, cwd, None, None, timeout,
                             match=lambda out: _reports_target(out, target))
    record["probe"] = dict(last, argv=pargv, live=live)
    rel.log("rollback_probe_after", live=live, polls=last["polls"], rc=last["rc"])
    if not live:
        return _rollback_unknown(rel, "the production probe did not report the target "
                                 "within %ss" % timeout)
    rel.save()
    path = write_result(root, rel, "rolled_back")
    if path is None:
        return 3
    print("ROLLBACK: %s rolled-back %s" % (v, target.get("version") or target.get("commit")))
    print("RESULT: %s" % os.path.relpath(path, root))
    return 0


def _rollback_unknown(rel, detail):
    """The rollback ran and did not provably land: outcome_unknown, never re-run
    automatically. Returns 3."""
    rel.set_status("outcome_unknown", rollback=rel.rollback)
    rel.save()
    rel.log("outcome_unknown", was="rolling_back", reason=detail)
    print("ROLLBACK: %s unknown" % rel.version)
    print("STOP: outcome-unknown: %s; it is never re-run by itself" % detail)
    print("NEXT: %s" % NEXT_ROLLBACK_UNKNOWN)
    return 3


def cmd_status(args):
    """Print each release's status. Read-only: no lock, no state change -- a status read
    during a live deploy must never demote it (only a locked command does that)."""
    root = os.path.abspath(args.root)
    try:
        names = sorted(os.listdir(_releases_dir(root)))
    except FileNotFoundError:
        names = []
    shown = 0
    for name in names:
        if not os.path.isdir(os.path.join(_releases_dir(root), name)):
            continue
        try:
            status = Release.load(root, name).status
        except (OSError, ValueError, KeyError, TypeError):
            status = "unreadable"
        print("RELEASE: %s %s" % (name, status))
        shown += 1
    if not shown:
        print("RELEASE: none")
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
    p = sub.add_parser("stage", help="build, verify locally, deploy to staging, check, tag")
    p.add_argument("--root", required=True)
    p.add_argument("--commit", help="the merged release commit (default: found on the "
                   "default branch)")
    p.add_argument("--evidence", action="store_true",
                   help="judge the verifier's evidence for the release commit, then go on")
    p.add_argument("--verifier", help="the independent verifier's id (with --evidence)")
    p.add_argument("--remote", default="origin", help="where the tag is pushed (origin)")
    p = sub.add_parser("deploy", help="the production deploy: only with the human's yes")
    p.add_argument("--root", required=True)
    # Not required: without it the release waits at awaiting_deploy (exit 3), not exit 2.
    p.add_argument("--approved-by", dest="approved_by",
                   help="the human, present now, who said yes to THIS production deploy")
    p.add_argument("--unattended", action="store_true",
                   help="no human is present: wait at awaiting_deploy, run nothing")
    p.add_argument("--remote", default="origin",
                   help="where the tag is pushed when CI deploys on tags (origin)")
    p = sub.add_parser("verify-prod", help="prove production runs the release, then "
                       "health and prod_smoke; write release-result/v1")
    p.add_argument("--root", required=True)
    p = sub.add_parser("rollback", help="roll production back: only with the human's yes")
    p.add_argument("--root", required=True)
    p.add_argument("--approved-by", dest="approved_by",
                   help="the human, present now, who said yes to THIS rollback")
    p.add_argument("--unattended", action="store_true",
                   help="no human is present: show the rollback, run nothing")
    p = sub.add_parser("status", help="print each release's status (read-only)")
    p.add_argument("--root", required=True)
    return ap


def main(argv=None):
    """Exit 0 ok, 2 refused or invalid, 3 must stop / needs the human."""
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    return {"init": cmd_init, "prep": cmd_prep, "stage": cmd_stage, "deploy": cmd_deploy,
            "verify-prod": cmd_verify_prod, "rollback": cmd_rollback,
            "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
