#!/usr/bin/env python3
"""Outcome eval for factory-pipeline (see the repo's docs/eval-standard.md).

Stdlib-only, offline, deterministic; scratch writes go to a tempdir only.
Prints one `CHECK: <name> — PASS|FAIL` line per check and a final
`EVAL_RESULT: PASS (n/n checks)` line; exits 0 iff every check passed.

The promise of `pipeline tick` (R12): in a repo with the sibling skills installed, one
tick prints a `STAGE:` line for each of the nine stages, in order, and exits 0. A stage
that is not built yet says `pending-R<n>`, never `ok`, and a missing skill says
`unavailable` instead of crashing the tick. The promise of R13: with no live pipeline
grant (none, or revoked), the tick runs no stage, prints `STOP: no-grant`, and exits 2.
The promise of R15: with an open run at max_open_runs 1, the tick prints `STOP: cap
max_open_runs`, skips autopick, plan and derive-grant (no new plan starts), does not
gate build on that cap, and still runs the rest. The promise of R17: with no planner in
the grant, the plan stage says `unavailable no-planner` and hands on no plan. The promise
of R18: a derived grant is built once by a stub `conductor init`, with the evidence gate
on exactly when the repository has a verify skill, and the next tick does not build it
again. The promise of R19: `release stage` runs once, only after the owner merged the
release pull request, while the release grant covers it, and no other release command runs.
The promise of R20: the idea stage lists the ideas due within 3 days or expired, hands a
go idea-brief envelope to the plan stage once (never again in a later tick), and runs no
writing idea-validation command.
The harness builds stub skills with assets/pipeline_testkit.py in a tempdir, writes a real
pipeline grant into the fixture repo with the testkit, runs the shipped
assets/pipeline.py against them, and a model-free grader checks the transcript. The docs check keeps every path the
docs name real, and keeps the old skeleton line out.

Negative fixtures feed the grader known-bad transcripts (order swapped, a stage missing,
a fake `ok` for an unbuilt stage, a build with the wrong evidence gate or two inits, a plan stage that hands on a plan with no planner, a non-zero exit, a no-grant tick that ran a stage or
exited 0, a cap stop with no STOP line, a stage that ran after a cap stop, a reporting
stage skipped by a cap, a build skipped by an open-runs cap, a release staged before the
merge, twice, or with a deploy call, an idea due in 10 days listed, a go idea planned
twice, a writing ideas command or --today) and a docs copy that names a missing
script, and require the grader to reject each one.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)

sys.path.insert(0, os.path.join(SKILL, "assets"))
sys.dont_write_bytecode = True  # no __pycache__ in the skill: the eval writes only to a tempdir
import pipeline_testkit as K  # noqa: E402

TOOL = os.path.join(SKILL, "assets", "pipeline.py")
STALE = "no command ships yet"
ORDER = ["sweep", "growth-review", "idea-follow-up", "autopick", "plan", "derive-grant",
         "build", "release-stage", "digest"]
PENDING = {"digest": "pending-R21"}
WORDS = {"ok", "idle", "attention", "refused", "failed", "timeout", "unavailable", "waiting"}
PATH_RE = re.compile(r"(?<![\w.-])((?:references|assets|scripts|eval)/[\w./-]*\w)")
GRAPH_BLOCK = re.compile(r"<!-- skill-graph:start -->.*?<!-- skill-graph:end -->", re.S)

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = " (%s)" % detail if detail else ""
    print("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL", suffix))


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def frontmatter_errors(text):
    """Return a list of frontmatter contract violations in a SKILL.md."""
    errors = []
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return ["missing opening ---"]
    try:
        close = lines[1:].index("---") + 1
    except ValueError:
        return ["missing closing ---"]
    fields = {}
    for ln in lines[1:close]:
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", ln)
        if m:
            fields[m.group(1)] = m.group(2).strip().strip("\"").strip("'")
    name = fields.get("name", "")
    if len(name) > 64 or not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", name or ""):
        errors.append("bad name: %r" % name)
    if "description" not in fields:
        errors.append("description missing")
    return errors


def tick_errors(stdout, code, missing=()):
    """Model-free grader for one tick transcript. `missing` names skills not installed."""
    errors = []
    if code != 0:
        errors.append("exit %s, want 0" % code)
    stages = [ln.split()[1:] for ln in stdout.splitlines() if ln.startswith("STAGE: ")]
    order = [s[0] for s in stages if s]
    if order != ORDER:
        errors.append("stages %s, want %s" % (order, ORDER))
    for s in stages:
        if len(s) < 2:
            errors.append("STAGE: line without a result")
            continue
        name, word = s[0], s[1]
        if name in PENDING and word != PENDING[name]:
            errors.append("%s says %s, but it is not built (want %s)"
                          % (name, word, PENDING[name]))
        elif name not in PENDING and word not in WORDS:
            errors.append("%s has unknown result %s" % (name, word))
    res = {s[0]: s[1] for s in stages if len(s) >= 2}
    for skill, stages_of in missing:
        for st in stages_of:
            if res.get(st) != "unavailable":
                errors.append("%s says %s with %s missing" % (st, res.get(st), skill))
    return errors


GATED = ("autopick", "plan", "derive-grant")  # what a plans or open-runs cap skips


def cap_errors(stdout, code, cap):
    """Model-free grader for a tick that a plans or open-runs cap stops before autopick
    (R15): exit 0, one `STOP: cap <cap>` line before the autopick line, autopick, plan and
    derive-grant skipped, build not skipped (it checks only the dispatch cap, so a grant
    already derived is never blocked by its own count), and the stages that only read,
    sweep or report still in their places."""
    errors = []
    if code != 0:
        errors.append("exit %s, want 0" % code)
    lines = stdout.splitlines()
    stops = [i for i, ln in enumerate(lines) if ln.startswith("STOP: cap %s " % cap)]
    if len(stops) != 1:
        errors.append("%d STOP: cap %s lines, want 1" % (len(stops), cap))
    stages = [(i, ln.split()[1:]) for i, ln in enumerate(lines) if ln.startswith("STAGE: ")]
    order = [s[0] for _, s in stages if s]
    if order != ORDER:
        errors.append("stages %s, want %s" % (order, ORDER))
    for i, s in stages:
        if s[0] in GATED:
            if s[1:] != ["skipped", "cap=%s" % cap]:
                errors.append("%s says %s after a cap stop" % (s[0], " ".join(s[1:])))
            if stops and i < stops[0]:
                errors.append("%s ran before the STOP line" % s[0])
        elif s[0] == "build" and s[1:2] == ["skipped"]:
            errors.append("build says %s, but a plans or open-runs cap does not gate it"
                          % " ".join(s[1:]))
        elif len(s) > 1 and s[1] == "skipped":
            errors.append("%s was skipped, but it starts no plan or run" % s[0])
    return errors


def plan_errors(stdout):
    """Model-free grader for the plan stage of a tick whose grant names no planner (R17):
    the stage is `unavailable no-planner`, no `PLAN: ready` line, and derive-grant idle."""
    errors = []
    res = {ln.split()[1]: ln.split()[2:] for ln in stdout.splitlines()
           if ln.startswith("STAGE: ") and len(ln.split()) > 2}
    if res.get("plan", [])[:2] != ["unavailable", "no-planner"]:
        errors.append("plan says %s, want unavailable no-planner"
                      % " ".join(res.get("plan", [])))
    if any(ln.startswith("PLAN: ready") for ln in stdout.splitlines()):
        errors.append("a plan was handed on with no planner")
    if res.get("derive-grant", [])[:1] != ["idle"]:
        errors.append("derive-grant says %s, want idle" % " ".join(res.get("derive-grant", [])))
    return errors


def build_errors(stdout, inits, gate):
    """Model-free grader for a tick that builds one derived grant (R18): build says `ok
    built=1 ... gate=<gate>`, and the stub conductor got exactly one init, whose plan
    carries the evidence gate `gate` (on: a `verification` block)."""
    errors = []
    res = {ln.split()[1]: ln.split()[2:] for ln in stdout.splitlines()
           if ln.startswith("STAGE: ")}
    b = res.get("build", [])
    if b[:2] != ["ok", "built=1"] or "gate=%s" % gate not in b:
        errors.append("build says %s, want ok built=1 gate=%s" % (" ".join(b), gate))
    if len(inits) != 1:
        errors.append("%d conductor inits, want 1" % len(inits))
    elif inits[0].get("gate") != gate:
        errors.append("the conductor got gate=%s, want %s" % (inits[0].get("gate"), gate))
    return errors


def build_tick(tmp, label, verify):
    """A build fixture (pipeline_testkit.build_repo) with one derived grant whose plan is
    gated exactly when the repo has a verify skill; returns (repo, skills, run)."""
    repo, sk, pid = K.build_repo(os.path.join(tmp, label), verify=verify)
    K.built_grant(repo, pid, gated=verify)
    return repo, sk, lambda: subprocess.run(
        [sys.executable, TOOL, "--root", repo, "--skills-dir", sk, "tick"],
        capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
        env=dict(os.environ, FACTORY_CONDUCTOR_TIMER_DRYRUN="1",
                 FACTORY_CONDUCTOR_TIMER_HOME=os.path.join(tmp, label, "timer")))


def stop_errors(stdout, code):
    """Model-free grader for a tick with no live pipeline grant (R13)."""
    errors = []
    if code != 2:
        errors.append("exit %s, want 2" % code)
    lines = stdout.splitlines()
    if not any(ln.startswith("STOP: no-grant") for ln in lines):
        errors.append("no STOP: no-grant line")
    if any(ln.startswith("STAGE: ") for ln in lines):
        errors.append("a stage ran without a grant")
    return errors


def docs_errors(skill_dir):
    """Every skill-relative path the docs name exists; the skeleton line is gone."""
    errors = []
    for doc in ("SKILL.md", "README.md", "references/overview.md"):
        path = os.path.join(skill_dir, doc)
        if not os.path.isfile(path):
            errors.append("%s missing" % doc)
            continue
        text = read(path)
        if STALE in text.lower():
            errors.append("%s still says %r" % (doc, STALE))
        text = GRAPH_BLOCK.sub("", text)
        for rel in sorted(set(PATH_RE.findall(text))):
            if not os.path.exists(os.path.join(skill_dir, rel)):
                errors.append("%s names %s, which does not exist" % (doc, rel))
    return errors


def run_tick(tmp, label, skills=None, behave=(), grant=True, derived=False):
    repo = os.path.join(tmp, label, "repo")
    os.makedirs(repo)
    if grant:
        pid = os.path.basename(K.pipeline_grant(repo))[:-len(".json")]
        if derived:  # an open run: a derived grant with no run result (max_open_runs 1)
            K.write_rel(repo, *K.derived_grant(pid))
    sk = K.stubs(os.path.join(tmp, label, "skills"), skills)
    for b in behave:
        K.behave(sk, *b[:2], **b[2])
    cp = subprocess.run([sys.executable, TOOL, "--root", repo, "--skills-dir", sk, "tick"],
                        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    return cp, sk


def release_errors(ticks, calls):
    """Model-free grader for the release stage (R19, D19, B1). `ticks` is
    [(merged, stdout)] in order; `calls` is the stub release-conductor's call log. While
    the release pull request is open, the stage waits for the merge and runs nothing; the
    first tick after the merge runs `release stage` once; later ticks run nothing; and no
    other release command is ever called."""
    errors = []
    first = next((j for j, (m, _o) in enumerate(ticks) if m), None)
    for i, (merged, out) in enumerate(ticks):
        line = next((ln for ln in out.splitlines() if ln.startswith("STAGE: release-stage ")),
                    "")
        want = ("STAGE: release-stage waiting reason=release-merge" if not merged else
                "STAGE: release-stage ok staged=1" if i == first else
                "STAGE: release-stage idle")
        if not line.startswith(want):
            errors.append("tick %d says %r, want %r" % (i + 1, line, want))
    if [c["argv"][0] for c in calls] != ["stage"]:
        errors.append("release calls %s, want one stage" % [c["argv"][0] for c in calls])
    return errors


def release_checks(tmp):
    d = os.path.join(tmp, "release")
    os.makedirs(d)
    repo, sk, _gid = K.release_fixture(d)

    def tick():
        return subprocess.run([sys.executable, TOOL, "--root", repo, "--skills-dir", sk,
                               "tick"], capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL).stdout

    ticks = [(False, tick())]
    open_calls = list(K.release_calls(sk))
    K.release_merge(repo)
    ticks += [(True, tick()), (True, tick())]
    calls = K.release_calls(sk)
    errs = release_errors(ticks, calls)
    check("release stage: waits while the PR is open, stages once after the merge, "
          "never again, and calls no other release command",
          not errs and not open_calls, "; ".join(errs)[:120])
    stage = calls[:1]
    for label, t, c, want in (
        ("release-before-merge", [(False, ticks[1][1])] + ticks[1:], stage, "tick 1"),
        ("release-staged-twice", ticks, stage * 2, "want one stage"),
        ("release-deploy-called", ticks, stage + [{"argv": ["deploy"]}], "want one stage"),
        ("release-restaged", ticks[:2] + [(True, ticks[1][1])], stage, "tick 3"),
    ):
        bad = release_errors(t, c)
        check("grader rejects negative fixture %s" % label,
              any(want in e for e in bad), "; ".join(bad)[:100])


IDEA_WRITERS = ("new", "register", "record", "decide", "handoff")


def idea_errors(first, second, calls, planned):
    """Model-free grader for the idea follow-up stage (R20, B1). `first` and `second` are
    two tick transcripts over the idea fixture: `soon` (due in 2 days), `later` (due in 10)
    and `late` (expired), and one go envelope `go-list`. `calls` is the stub
    idea-validation's call log; `planned` the stub planner's call count. Each tick lists
    soon and late, never later; the first tick plans go-list once, the second never; and
    no writing ideas command or --today ever runs."""
    errors = []
    for i, out in enumerate((first, second), 1):
        due = sorted(ln.split()[2] for ln in out.splitlines() if ln.startswith("IDEA: due "))
        if due != ["late", "soon"]:
            errors.append("tick %d lists %s, want late and soon" % (i, due))
    if "IDEA: plan go-list " not in first:
        errors.append("tick 1 did not hand go-list to the plan stage")
    if "IDEA: plan " in second:
        errors.append("tick 2 planned an idea again")
    if planned != 1:
        errors.append("the planner ran %d times, want 1" % planned)
    for c in calls:
        argv = c["argv"][2:] if c["argv"][:1] == ["--root"] else c["argv"]
        if not argv or argv[0] in IDEA_WRITERS or argv[0] not in ("list", "status") \
                or any(a.startswith("--today") for a in c["argv"]):
            errors.append("ideas called with %s" % argv[:2])
    return errors


def idea_checks(tmp):
    d = os.path.join(tmp, "ideas")
    os.makedirs(d)
    repo, sk, agent = K.idea_fixture(d)

    def tick():
        return subprocess.run([sys.executable, TOOL, "--root", repo, "--skills-dir", sk,
                               "tick"], capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL).stdout

    first, second = tick(), tick()
    calls, planned = K.idea_calls(sk), len(K.planner_calls(agent))
    errs = idea_errors(first, second, calls, planned)
    check("idea stage: lists the ideas due in 2 days and expired, not the one due in 10; "
          "plans the go idea once; runs no writing ideas command",
          not errs and "STAGE: idea-follow-up ok due=2 go=1" in first,
          "; ".join(errs)[:120])
    for label, a, b, c, n, want in (
        ("idea-later-listed", first + "\nIDEA: due later A1", second, calls, planned,
         "tick 1 lists"),
        ("idea-planned-twice", first, second + "\nIDEA: plan go-list x", calls, 2,
         "planned an idea again"),
        ("idea-writer-called", first, second, calls + [{"argv": ["--root", repo, "decide",
                                                                 "go-list"]}],
         planned, "ideas called with"),
        ("idea-today-passed", first, second, calls + [{"argv": ["--root", repo, "--today",
                                                                "2026-01-01", "list"]}],
         planned, "ideas called with"),
    ):
        bad = idea_errors(a, b, c, n)
        check("grader rejects negative fixture %s" % label,
              any(want in e for e in bad), "; ".join(bad)[:100])


def main():
    text = read(os.path.join(SKILL, "SKILL.md"))
    fm = frontmatter_errors(text)
    check("SKILL.md frontmatter contract holds", not fm, "; ".join(fm)[:80])

    body = text.split("---", 2)[2] if text.count("---") >= 2 else ""
    check(
        "body keeps gate-passing structure (>=3 sections, >=3 steps)",
        len(re.findall(r"^## ", body, re.M)) >= 3
        and len(re.findall(r"^[0-9]+\. ", body, re.M)) >= 3,
    )

    errs = docs_errors(SKILL)
    check("docs name only real paths and drop the skeleton line", not errs,
          "; ".join(errs)[:120])

    tmp = tempfile.mkdtemp(prefix="factory_pipeline-eval-")
    try:
        cp, sk = run_tick(tmp, "all")
        errs = tick_errors(cp.stdout, cp.returncode)
        check("tick on stub skills: nine STAGE lines in order, exit 0", not errs,
              "; ".join(errs)[:120])
        errs = plan_errors(cp.stdout)
        check("no planner in the grant: plan unavailable no-planner, no plan handed on",
              not errs, "; ".join(errs)[:120])
        calls = [(c["skill"], c["key"]) for c in K.calls(sk) if c["key"] != "pipeline-caps"]
        check("tick called sweep, growth auto, intake import, autopick in order",
              calls == [("ops-intake", "sweep"), ("growth-review", "auto"),
                        ("ops-intake", "import"), ("ops-intake", "autopick")], str(calls))
        imp = [c for c in K.calls(sk) if c["key"] == "import"]
        check("growth stdout reached intake import unchanged",
              bool(imp) and imp[0]["stdin"] == K.SIGNAL + "\n")

        cp, _ = run_tick(tmp, "no-growth", skills=("ops-intake", "spec-first-planning"))
        errs = tick_errors(cp.stdout, cp.returncode,
                           missing=[("growth-review", ["growth-review"])])
        check("missing growth-review: still nine lines, growth-review unavailable", not errs,
              "; ".join(errs)[:120])

        cp, _ = run_tick(tmp, "sweep-fails", behave=[
            ("ops-intake", "sweep", {"stdout": "SWEEP: 1 commands ok 0 failed 1\n",
                                     "exit": 3})])
        errs = tick_errors(cp.stdout, cp.returncode)
        check("a failing sweep is recorded and the tick goes on",
              not errs and "STAGE: sweep attention exit=3" in cp.stdout,
              "; ".join(errs)[:120])

        cp, sk = run_tick(tmp, "open-run", derived=True)
        errs = cap_errors(cp.stdout, cp.returncode, "max_open_runs")
        check("an open run at max_open_runs 1: STOP: cap max_open_runs, no plan or run "
              "starts", not errs and "autopick" not in [c["key"] for c in K.calls(sk)],
              "; ".join(errs)[:120])

        for label, verify, gate in (("build-gated", True, "on"),
                                    ("build-plain", False, "off")):
            _repo, sk, run = build_tick(tmp, label, verify)
            cp = run()
            inits = [c for c in K.conductor_calls(sk) if c["argv"][0] == "init"]
            errs = build_errors(cp.stdout, inits, gate)
            check("build %s a verify skill: one conductor init, evidence gate %s"
                  % ("with" if verify else "without", gate), not errs,
                  "; ".join(errs)[:120])
            if verify:
                first = cp.stdout
                cp = run()
                again = [c for c in K.conductor_calls(sk) if c["argv"][0] == "init"]
                check("the next tick does not init the built grant again",
                      len(again) == 1 and "STAGE: build idle grants=0" in cp.stdout,
                      "%d inits" % len(again))
                for nlabel, out, got, want in (
                    ("build-wrong-gate", first, [dict(inits[0], gate="off")], "want on"),
                    ("build-two-inits", "STAGE: build ok built=1 waiting=0 gate=on",
                     inits * 2, "2 conductor inits"),
                    ("build-not-ok", "STAGE: build pending-R18 grants=1", inits,
                     "build says"),
                ):
                    bad = build_errors(out, got, "on")
                    check("grader rejects negative fixture %s" % nlabel,
                          any(want in e for e in bad), "; ".join(bad)[:100])

        release_checks(tmp)
        idea_checks(tmp)

        cp, sk = run_tick(tmp, "no-grant", grant=False)
        errs = stop_errors(cp.stdout, cp.returncode)
        check("no pipeline grant: STOP: no-grant, exit 2, no stage ran",
              not errs and not K.calls(sk), "; ".join(errs)[:120])

        repo = os.path.join(tmp, "revoked", "repo")
        os.makedirs(repo)
        K.pipeline_grant(repo)
        rv = subprocess.run([sys.executable, K.CHECKER, "revoke-grant", "--root", repo],
                            capture_output=True, text=True, timeout=60,
                            stdin=subprocess.DEVNULL)
        sk = K.stubs(os.path.join(tmp, "revoked", "skills"))
        cp = subprocess.run([sys.executable, TOOL, "--root", repo, "--skills-dir", sk, "tick"],
                            capture_output=True, text=True, timeout=60,
                            stdin=subprocess.DEVNULL)
        errs = stop_errors(cp.stdout, cp.returncode)
        check("kill switch revokes the grant: STOP: no-grant reason=revoked, exit 2",
              rv.returncode == 0 and not errs and "reason=revoked" in cp.stdout
              and not K.calls(sk), "; ".join(errs)[:120])

        good = "\n".join("STAGE: %s %s" % (s, PENDING.get(s, "ok")) for s in ORDER)
        swapped = good.replace("STAGE: sweep ok\nSTAGE: growth-review ok",
                               "STAGE: growth-review ok\nSTAGE: sweep ok")
        for label, out, code, want in (
            ("order-swapped", swapped, 0, "stages"),
            ("digest-missing", good.rsplit("\n", 1)[0], 0, "stages"),
            ("fake-ok-digest", good.replace("digest pending-R21", "digest ok"), 0,
             "digest says ok"),
            ("non-zero-exit", good, 1, "exit 1"),
        ):
            bad = tick_errors(out, code)
            check("grader rejects negative fixture %s" % label,
                  any(want in e for e in bad), "; ".join(bad)[:100])
        check("grader accepts the known-good transcript", not tick_errors(good, 0))
        stop = "TICK: start x root r\nSTOP: no-grant reason=none"
        for label, out, code, want in (
            ("no-grant-exit-0", stop, 0, "exit 0"),
            ("no-grant-ran-a-stage", stop + "\nSTAGE: sweep ok", 2, "a stage ran"),
            ("no-grant-no-stop-line", "TICK: start x root r", 2, "no STOP"),
        ):
            bad = stop_errors(out, code)
            check("grader rejects negative fixture %s" % label,
                  any(want in e for e in bad), "; ".join(bad)[:100])
        check("grader accepts the known-good no-grant transcript", not stop_errors(stop, 2))
        capped = "\n".join(
            ["STAGE: sweep ok", "STAGE: growth-review ok", "STAGE: idea-follow-up idle",
             "STOP: cap max_open_runs count=1 limit=1 stage=autopick"]
            + ["STAGE: %s skipped cap=max_open_runs" % s for s in GATED]
            + ["STAGE: build idle grants=0", "STAGE: release-stage idle",
               "STAGE: digest pending-R21"])
        for label, out, want in (
            ("cap-no-stop-line", capped.replace("STOP: cap", "NOTE: cap"), "0 STOP"),
            ("cap-autopick-ran", capped.replace("autopick skipped cap=max_open_runs",
                                                "autopick ok picked=1"), "autopick says ok"),
            ("cap-digest-skipped", capped.replace("digest pending-R21",
                                                  "digest skipped cap=max_open_runs"),
             "digest was skipped"),
            ("cap-build-deadlock", capped.replace("build idle grants=0",
                                                  "build skipped cap=max_open_runs"),
             "does not gate it"),
        ):
            bad = cap_errors(out, 0, "max_open_runs")
            check("grader rejects negative fixture %s" % label,
                  any(want in e for e in bad), "; ".join(bad)[:100])
        check("grader accepts the known-good cap-stop transcript",
              not cap_errors(capped, 0, "max_open_runs"))
        noplan = ("STAGE: autopick ok picked=1\nPLAN: waiting x reason=no-planner\n"
                  "STAGE: plan unavailable no-planner picked=1\nSTAGE: derive-grant idle plans=0")
        for label, out, want in (
            ("plan-ok-without-planner", noplan.replace("plan unavailable no-planner",
                                                       "plan ok ready=1"), "plan says"),
            ("plan-handed-on-without-planner",
             noplan + "\nPLAN: ready .skill-contract/envelopes/p.json spec=s", "handed on"),
            ("derive-without-planner", noplan.replace("derive-grant idle plans=0",
                                                      "derive-grant ok derived=1"),
             "derive-grant says"),
        ):
            bad = plan_errors(out)
            check("grader rejects negative fixture %s" % label,
                  any(want in e for e in bad), "; ".join(bad)[:100])
        check("grader accepts the known-good no-planner transcript", not plan_errors(noplan))
        bad = tick_errors(good, 0, missing=[("growth-review", ["growth-review"])])
        check("grader rejects ok for a stage whose skill is missing",
              any("missing" in e for e in bad), "; ".join(bad)[:100])

        dst = os.path.join(tmp, "docs-copy")
        shutil.copytree(SKILL, dst, ignore=shutil.ignore_patterns("__pycache__"))
        with open(os.path.join(dst, "SKILL.md"), "a", encoding="utf-8") as f:
            f.write('\nRun `python3 "$SKILL_DIR/assets/schedule.py" install`.\n')
        bad = docs_errors(dst)
        check("grader rejects docs that name a script that does not ship",
              any("assets/schedule.py, which does not exist" in e for e in bad),
              "; ".join(bad)[:100])

        bad_fm = frontmatter_errors("---\nname: Bad_Name\n---\nbody\n")
        check("grader rejects known-bad frontmatter fixture", len(bad_fm) >= 2,
              "; ".join(bad_fm)[:80])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print("EVAL_RESULT: %s (%d/%d checks)" % ("PASS" if ok else "FAIL", k, n))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
