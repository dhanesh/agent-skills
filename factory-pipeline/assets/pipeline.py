#!/usr/bin/env python3
"""factory-pipeline: one `pipeline tick` that walks the factory stages in order.

Usage: pipeline.py [--root R] [--skills-dir D ...] tick [--grant G] [--growth-source NAME]

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
Python 3.10+, POSIX. Exits: 0 every stage ran; 2 usage error.
"""
import argparse
import datetime
import glob
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
PIPELINE_GRANT_GLOB = ".skill-contract/envelopes/pipeline-grant-v1-*"

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
        self.grant = grant
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


def find_tool(roots, skill):
    """The absolute path of a sibling skill's tool, or None when it is not installed."""
    for r in roots:
        d = os.path.join(r, skill)
        tool = os.path.join(d, TOOLS[skill])
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
    grant = tick.grant or single_pipeline_grant(tick.root)
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


def single_pipeline_grant(root):
    """The one pipeline-grant envelope under the root, or None when there are 0 or 2+."""
    found = sorted(glob.glob(os.path.join(root, PIPELINE_GRANT_GLOB)))
    return found[0] if len(found) == 1 else None


def exit_detail(code):
    return "" if code in (0, None) else "exit=%d" % code


def join(*parts):
    return " ".join(p for p in parts if p)


# ── the command ─────────────────────────────────────────────────────────────────────────

def cmd_tick(a):
    tick = Tick(a.root, skill_roots(a.root, overrides=a.skills_dir), grant=a.grant,
                growth_source=a.growth_source)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print("TICK: start %s root %s" % (now, a.root))
    # R13 (no live pipeline grant), R14 (the lock) and R15 (caps) stop the tick here or
    # between stages; until they ship, every stage runs.
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
                   help="the pipeline grant id or envelope path for derive-grant")
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
