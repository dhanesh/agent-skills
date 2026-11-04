#!/usr/bin/env python3
"""factory-pipeline: one `pipeline tick` that walks the factory stages in order.

Usage: pipeline.py [--root R] [--skills-dir D ...] tick [--grant G] [--growth-source NAME]

Before any stage runs, the tick checks the pipeline grant (R13). It runs spec-first-planning's
vendored `contract_check.py check-pipeline-grant --root R [--id G]`, which judges the newest
pipeline grant that no revision supersedes (or the one `--grant` names): valid, not revoked,
not superseded, not expired, not past 7 days, not stale and not tracked by git. Only on exit
0 (VALID) does the tick go on, and it prints `TICK: grant <id> expires_at <time>`. Otherwise
it prints `STOP: no-grant reason=<reason>` (none, revoked, superseded, expired, lifetime,
stale, tracked, invalid, checker-unavailable, checker-error or not-an-envelope), runs no
stage and exits 2. The derive-grant stage uses only the grant id that this check returned.

Before that check, the tick takes the pipeline lock (R14): a non-blocking exclusive flock on
<root>/.skill-contract/pipeline/.lock. That directory holds a `*` .gitignore, and the tick
proves it is git-ignored before it writes the lock file. When another tick holds the lock,
the tick prints `STOP: locked: ...`, runs no check and no stage, changes nothing and exits
3. The tick holds the lock until it exits, through every stage and every tool call. The
kernel drops a flock when its holder dies, so a killed tick never blocks the next one. The
lock file descriptor is not inherited by the tools the tick runs. ops-intake's own run lock
is a different file, so the two never wait on each other. A state directory that cannot be
shown to be git-ignored is a refusal: `STOP: state-dir reason=...`, exit 2.

The tick runs nine stages in this order and prints one `STAGE: <name> <result>` line
per stage, optionally followed by a detail such as `exit=3` or `picked=2`:

  sweep           ops-intake `intake sweep`
  growth-review   growth-review `growth auto --source NAME`, whose stdout bytes go to
                  ops-intake `intake import --format intake-signals-jsonl --source NAME`
                  (two argv subprocesses; the bytes pass through Python, no shell pipe)
  idea-follow-up  not built yet (R20): `pending-R20`
  autopick        ops-intake `intake autopick`; each picked intake-item envelope is kept
                  for the plan stage
  plan            not built yet (R17): `pending-R17`; it hands on no plan
  derive-grant    spec-first-planning `write_grant.py --under-pipeline <grant> --plan <p>`
                  for each plan envelope the plan stage hands on
  build           not built yet (R18): `pending-R18`
  release-stage   not built yet (R19): `pending-R19`
  digest          not built yet (R21): `pending-R21`

Result words: ok, attention (the tool exited 3, or some plans were refused), refused (the
tool exited 2, or 1 for write_grant), failed (another exit, or the tool could not start),
timeout, unavailable (the skill is not installed), idle (nothing to do), pending-R<n> (the
stage is not built yet). A stage's non-zero result does not stop the tick: the tick goes
on to the next stage and exits 0 when every stage ran.

Each sibling skill is installed on its own, so the tick looks for it by directory name
in the roots skill-contract `discover` searches, in this order: $SKILL_CONTRACT_PATH, the
directory beside this skill, <root>/.agents/skills, <root>/.claude/skills,
~/.agents/skills, ~/.claude/skills. `--skills-dir` (repeatable) or $FACTORY_PIPELINE_SKILLS
(os.pathsep-separated) replaces that list, so a test can point at stub skills only.

The tick runs every tool with sys.executable, an argv list and no shell. Stdlib only,
Python 3.10+, POSIX. Exits: 0 every stage ran; 2 usage error, a state directory that is not
git-ignored, or no live pipeline grant; 3 another tick holds the pipeline lock.
"""
import argparse
import contextlib
import datetime
import fcntl
import json
import os
import re
import subprocess
import sys

SKILL_NAME = "factory-pipeline"
VERSION = "0.1.0"
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGE_TIMEOUT = 3600  # seconds per tool call; intake sweep caps each source at 300 s
NEXT_PLAN = re.compile(r"^NEXT: run spec-first-planning with (\S+)\s*$")
GRANT_LINE = re.compile(r"^GRANT: (\S+)\s*$")
PIPELINE_GRANT_ID = re.compile(r"^pipeline-grant-v1-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}\Z")
REASON = re.compile(r"^[a-z]+(?:-[a-z]+)*\Z")
# The pipeline-grant check: spec-first-planning ships the reference checker, byte-identical.
CHECKER = ("spec-first-planning", "assets/contract_check.py")
CHECK_TIMEOUT = 120  # seconds; the checker reads local files and asks git
# The pipeline lock (R14): one tick at a time per repository. Not ops-intake's lock file.
PIPELINE_DIR = ".skill-contract/pipeline"
LOCK_FILE = ".lock"

# skill directory -> its tool, relative to the skill directory
TOOLS = {
    "ops-intake": "assets/intake.py",
    "growth-review": "assets/growth.py",
    "spec-first-planning": "assets/write_grant.py",
}

# How bad a result is, so a stage that makes two calls reports the worse one.
SEVERITY = {"ok": 0, "idle": 0, "attention": 1, "refused": 2, "failed": 3, "timeout": 3,
            "unavailable": 3}


class Tick:
    """What one tick knows. Later stages read what earlier stages left here."""

    def __init__(self, root, roots, grant=None, growth_source="growth",
                 timeout=STAGE_TIMEOUT):
        self.root = root
        self.roots = roots
        self.grant = grant  # the pipeline grant id check_grant() judged VALID
        self.grant_expires = None
        self.growth_source = growth_source
        self.timeout = timeout
        self.picked = []    # intake-item envelopes autopick wrote (for the plan stage)
        self.plans = []     # task-plan envelopes the plan stage hands on (R17 fills it)
        self.grants = []    # (plan, derived grant) pairs for the build stage (R18)
        self.waiting = []   # (kind, what, next command) for the digest (R21)


# ── finding the sibling skills ──────────────────────────────────────────────────────────

def skill_roots(root, env=None, overrides=None):
    """The directories to search for sibling skills, in precedence order."""
    env = os.environ if env is None else env
    if overrides:
        return [os.path.abspath(d) for d in overrides]
    forced = [d for d in (env.get("FACTORY_PIPELINE_SKILLS") or "").split(os.pathsep) if d]
    if forced:
        return [os.path.abspath(d) for d in forced]
    roots = [d for d in (env.get("SKILL_CONTRACT_PATH") or "").split(os.pathsep) if d]
    roots.append(os.path.dirname(SKILL_DIR))
    roots += [os.path.join(root, b, "skills") for b in (".agents", ".claude")]
    home = env.get("HOME") or os.path.expanduser("~")
    roots += [os.path.join(home, b, "skills") for b in (".agents", ".claude")]
    return [os.path.abspath(d) for d in roots]


def find_tool(roots, skill, rel=None):
    """The absolute path of a sibling skill's tool (TOOLS[skill], or rel), or None when it
    is not installed."""
    for r in roots:
        d = os.path.join(r, skill)
        tool = os.path.join(d, rel or TOOLS[skill])
        if os.path.isfile(os.path.join(d, "SKILL.md")) and os.path.isfile(tool):
            return tool
    return None


# ── running one tool ────────────────────────────────────────────────────────────────────

def run_tool(tick, stage, argv, input_bytes=None, echo_stdout=True):
    """Run argv (no shell) in the repo root. Return (result word, exit code, stdout bytes).

    The tool's stderr, and its stdout when echo_stdout, are printed with a `  <stage>| `
    prefix, so no tool line can pass for one of the tick's own STAGE: or STOP: lines."""
    kw = {"input": input_bytes} if input_bytes is not None else {"stdin": subprocess.DEVNULL}
    try:
        cp = subprocess.run(argv, cwd=tick.root, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=tick.timeout, check=False, **kw)
    except subprocess.TimeoutExpired:
        return "timeout", None, b""
    except OSError as e:
        echo(stage, "cannot start %s: %s" % (os.path.basename(argv[1]), e))
        return "failed", None, b""
    if echo_stdout:
        echo_bytes(stage, cp.stdout)
    echo_bytes(stage, cp.stderr)
    return word_for(cp.returncode), cp.returncode, cp.stdout


def word_for(code):
    return {0: "ok", 3: "attention", 2: "refused"}.get(code, "failed")


def echo(stage, line):
    print("  %s| %s" % (stage, line))


def echo_bytes(stage, data):
    for line in data.decode("utf-8", "replace").splitlines():
        if line.strip():
            echo(stage, "".join(c if c.isprintable() else "?" for c in line))


def worse(a, b):
    return a if SEVERITY.get(a, 3) >= SEVERITY.get(b, 3) else b


def py(tool, *args):
    return [sys.executable, tool] + list(args)


# ── the stages ──────────────────────────────────────────────────────────────────────────
# Each stage takes the Tick and returns (result word, detail or "").

def stage_sweep(tick):
    intake = find_tool(tick.roots, "ops-intake")
    if intake is None:
        return "unavailable", "ops-intake"
    word, code, _ = run_tool(tick, "sweep", py(intake, "--root", tick.root, "sweep"))
    return word, exit_detail(code)


def stage_growth_review(tick):
    growth = find_tool(tick.roots, "growth-review")
    if growth is None:
        return "unavailable", "growth-review"
    intake = find_tool(tick.roots, "ops-intake")
    if intake is None:
        return "unavailable", "ops-intake"
    src = tick.growth_source
    word, code, out = run_tool(tick, "growth-review",
                               py(growth, "--root", tick.root, "auto", "--source", src),
                               echo_stdout=False)
    signals = sum(1 for ln in out.splitlines() if ln.strip())
    # Exit 2 is a refusal and carries no signals. Exit 0 and 3 may carry breaches.
    if code not in (0, 3) or signals == 0:
        return word, join(exit_detail(code), "signals=%d" % signals)
    iword, icode, _ = run_tool(tick, "growth-review",
                               py(intake, "--root", tick.root, "import", "--format",
                                  "intake-signals-jsonl", "--source", src, "-"),
                               input_bytes=out)
    return worse(word, iword), join(exit_detail(code), "signals=%d" % signals,
                                    "import " + exit_detail(icode) if icode else "")


def stage_idea_follow_up(tick):
    return "pending-R20", ""


def stage_autopick(tick):
    intake = find_tool(tick.roots, "ops-intake")
    if intake is None:
        return "unavailable", "ops-intake"
    word, code, out = run_tool(tick, "autopick", py(intake, "--root", tick.root, "autopick"))
    for line in out.decode("utf-8", "replace").splitlines():
        m = NEXT_PLAN.match(line)
        if m:
            tick.picked.append(m.group(1))
    return word, join(exit_detail(code), "picked=%d" % len(tick.picked))


def stage_plan(tick):
    # R17 drafts a spec for each picked envelope and appends the plans that pass
    # spec_lint.py and spec_to_tasks.py --allowlist to tick.plans.
    return "pending-R17", "picked=%d" % len(tick.picked)


def stage_derive_grant(tick):
    if not tick.plans:
        return "idle", "plans=0"
    tool = find_tool(tick.roots, "spec-first-planning")
    if tool is None:
        return "unavailable", "spec-first-planning"
    grant = tick.grant  # the id check_grant() judged VALID; never a fresh lookup
    if grant is None:
        return "refused", "no-grant"
    derived = refused = 0
    for plan in tick.plans:
        word, code, out = run_tool(tick, "derive-grant",
                                   py(tool, "--under-pipeline", grant, "--root", tick.root,
                                      "--plan", plan))
        path = None
        for line in out.decode("utf-8", "replace").splitlines():
            m = GRANT_LINE.match(line)
            if m:
                path = m.group(1)
        if code == 0 and path:
            derived += 1
            tick.grants.append((plan, path))
        else:
            refused += 1
            tick.waiting.append(("plan-read", plan, "read the plan, then derive its grant"))
    word = "ok" if refused == 0 else "attention"
    return word, "derived=%d refused=%d" % (derived, refused)


def stage_build(tick):
    return "pending-R18", "grants=%d" % len(tick.grants)


def stage_release_stage(tick):
    return "pending-R19", ""


def stage_digest(tick):
    return "pending-R21", "waiting=%d" % len(tick.waiting)


STAGES = (
    ("sweep", stage_sweep),
    ("growth-review", stage_growth_review),
    ("idea-follow-up", stage_idea_follow_up),
    ("autopick", stage_autopick),
    ("plan", stage_plan),
    ("derive-grant", stage_derive_grant),
    ("build", stage_build),
    ("release-stage", stage_release_stage),
    ("digest", stage_digest),
)


# ── the pipeline lock (R14) ─────────────────────────────────────────────────────────────

class Refused(Exception):
    """The state directory cannot be shown to be git-ignored: exit 2."""


class Locked(Exception):
    """Another tick holds the pipeline lock: exit 3."""


def _pipeline_dir(root):
    return os.path.join(root, *PIPELINE_DIR.split("/"))


def _gitignore_ok(path):
    """True when the file is a regular file holding `*` and no `!` line."""
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return False
        with open(path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.read().splitlines()]
        return "*" in lines and not any(ln.startswith("!") for ln in lines)
    except (OSError, ValueError):
        return False


def ensure_dir(root):
    """Make .skill-contract/pipeline/ and prove it is git-ignored before the lock file is
    written (incident-watch's pattern). Raises Refused when that cannot be shown."""
    top = os.path.join(root, ".skill-contract")
    d = _pipeline_dir(root)
    if os.path.islink(top) or os.path.islink(d):
        raise Refused("symlink")
    gi = os.path.join(d, ".gitignore")
    if os.path.isdir(d) and _gitignore_ok(gi):
        return d
    if os.path.islink(gi) or (os.path.lexists(gi) and not os.path.isfile(gi)):
        raise Refused("gitignore-not-a-file")
    if os.path.isfile(gi):
        with open(gi, encoding="utf-8") as f:
            if any(ln.strip().startswith("!") for ln in f.read().splitlines()):
                raise Refused("gitignore-unignores")
    try:
        os.makedirs(d, exist_ok=True)
        with open(gi, "w", encoding="utf-8") as f:
            f.write("# written by factory-pipeline: tick state is local, never committed\n*\n")
    except OSError:
        raise Refused("cannot-write")
    if not _gitignore_ok(gi):
        raise Refused("cannot-verify")
    return d


@contextlib.contextmanager
def tick_lock(root):
    """Hold a non-blocking exclusive flock on <root>/.skill-contract/pipeline/.lock for the
    whole tick. Raises Refused (state dir not git-ignored) or Locked (another tick holds
    it). The descriptor is close-on-exec, so no tool the tick runs keeps the lock alive."""
    d = ensure_dir(root)
    try:
        fd = os.open(os.path.join(d, LOCK_FILE),
                     os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError:
        raise Refused("lock-file")
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Locked()
        yield
    finally:
        os.close(fd)


# ── the pipeline-grant check (R13) ──────────────────────────────────────────────────────

def grant_id_of(root, ref):
    """The pipeline grant id that `--grant` names (an id, or the path of its envelope in
    <root>/.skill-contract/envelopes/), or None when it names neither."""
    if ref is None:
        return None
    if PIPELINE_GRANT_ID.match(ref):
        return ref
    base = os.path.basename(ref)
    if not base.endswith(".json"):
        return None
    gid = base[:-len(".json")]
    want = os.path.join(root, ".skill-contract", "envelopes", base)
    path = ref if os.path.isabs(ref) else os.path.join(root, ref)
    if not PIPELINE_GRANT_ID.match(gid) or os.path.realpath(path) != os.path.realpath(want):
        return None
    return gid


def check_grant(tick, ref=None):
    """Judge the pipeline grant before any stage runs. Return (grant id, None) when
    check-pipeline-grant answers VALID, else (None, reason). Fails closed: no checker, a
    checker error or an answer it cannot read is a refusal."""
    checker = find_tool(tick.roots, CHECKER[0], CHECKER[1])
    if checker is None:
        return None, "checker-unavailable"
    argv = py(checker, "check-pipeline-grant", "--root", tick.root, "--json")
    if ref is not None:
        gid = grant_id_of(tick.root, ref)
        if gid is None:
            return None, "not-an-envelope"
        argv += ["--id", gid]
    try:
        cp = subprocess.run(argv, cwd=tick.root, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            timeout=CHECK_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        echo("grant", "cannot run contract_check.py: %s" % type(e).__name__)
        return None, "checker-error"
    lines = cp.stdout.decode("utf-8", "replace").splitlines()
    rep = None
    for line in lines:
        if line.startswith("{"):
            try:
                rep = json.loads(line)
            except ValueError:
                rep = None
            break
    echo_bytes("grant", "\n".join(ln for ln in lines if not ln.startswith("{")).encode())
    echo_bytes("grant", cp.stderr)
    if not isinstance(rep, dict) or cp.returncode not in (0, 2, 3):
        return None, "checker-error"
    status = rep.get("status")
    if cp.returncode == 0 and status == "VALID" and isinstance(rep.get("id"), str):
        tick.grant_expires = rep.get("expires_at")
        return rep["id"], None
    if status == "NONE":
        return None, "none"
    reason = rep.get("reason")
    return None, reason if isinstance(reason, str) and REASON.match(reason) else "invalid"


def exit_detail(code):
    return "" if code in (0, None) else "exit=%d" % code


def join(*parts):
    return " ".join(p for p in parts if p)


# ── the command ─────────────────────────────────────────────────────────────────────────

def cmd_tick(a):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print("TICK: start %s root %s" % (now, a.root))
    # R14: one tick at a time. The lock comes first and is held until the tick returns.
    try:
        with tick_lock(a.root):
            return run_tick(a)
    except Refused as e:
        print("STOP: state-dir reason=%s dir=%s" % (e, PIPELINE_DIR))
        return 2
    except Locked:
        print("STOP: locked: another pipeline tick holds %s/%s" % (PIPELINE_DIR, LOCK_FILE))
        return 3


def run_tick(a):
    """The tick body. cmd_tick() calls it only while it holds the pipeline lock."""
    tick = Tick(a.root, skill_roots(a.root, overrides=a.skills_dir),
                growth_source=a.growth_source)
    # R13: no stage runs (nothing is swept, picked or planned) without a live pipeline grant.
    gid, reason = check_grant(tick, a.grant)
    if gid is None:
        print("STOP: no-grant reason=%s" % reason)
        return 2
    tick.grant = gid
    print("TICK: grant %s expires_at %s" % (gid, tick.grant_expires))
    # R15 (caps) stops the tick here or between stages; until it ships, every stage runs.
    ran = 0
    for name, fn in STAGES:
        try:
            word, detail = fn(tick)
        except Exception as e:  # one broken stage reports and the tick goes on
            word, detail = "failed", "error=%s" % type(e).__name__
            echo(name, "%s: %s" % (type(e).__name__, e))
        print("STAGE: %s" % join(name, word, detail))
        sys.stdout.flush()
        ran += 1
    print("TICK: done stages=%d" % ran)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="pipeline")
    p.add_argument("--root", default=".")
    p.add_argument("--skills-dir", action="append", default=None,
                   help="search only this directory for sibling skills (repeatable)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("tick")
    s.add_argument("--grant", default=None,
                   help="check this pipeline grant (an id, or its envelope path) instead "
                        "of the newest live one")
    s.add_argument("--growth-source", default="growth",
                   help="the intake source name for growth signals (default growth)")
    try:
        a = p.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    a.root = os.path.abspath(a.root)
    if not os.path.isdir(a.root):
        print("STOP: --root %s is not a directory" % a.root)
        return 2
    return {"tick": cmd_tick}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
