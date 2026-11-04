#!/usr/bin/env python3
"""Outcome eval for factory-pipeline (see the repo's docs/eval-standard.md).

Stdlib-only, offline, deterministic; scratch writes go to a tempdir only.
Prints one `CHECK: <name> — PASS|FAIL` line per check and a final
`EVAL_RESULT: PASS (n/n checks)` line; exits 0 iff every check passed.

The promise of `pipeline tick` (R12): in a repo with the sibling skills installed, one
tick prints a `STAGE:` line for each of the nine stages, in order, and exits 0. A stage
that is not built yet says `pending-R<n>`, never `ok`, and a missing skill says
`unavailable` instead of crashing the tick. The harness builds stub skills with
assets/pipeline_testkit.py in a tempdir, runs the shipped assets/pipeline.py against
them, and a model-free grader checks the transcript. The docs check keeps every path the
docs name real, and keeps the old skeleton line out.

Negative fixtures feed the grader known-bad transcripts (order swapped, a stage missing,
a fake `ok` for an unbuilt stage, a non-zero exit) and a docs copy that names a missing
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
PENDING = {"idea-follow-up": "pending-R20", "plan": "pending-R17", "build": "pending-R18",
           "release-stage": "pending-R19", "digest": "pending-R21"}
WORDS = {"ok", "idle", "attention", "refused", "failed", "timeout", "unavailable"}
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


def run_tick(tmp, label, skills=None, behave=()):
    repo = os.path.join(tmp, label, "repo")
    os.makedirs(repo)
    sk = K.stubs(os.path.join(tmp, label, "skills"), skills)
    for b in behave:
        K.behave(sk, *b[:2], **b[2])
    cp = subprocess.run([sys.executable, TOOL, "--root", repo, "--skills-dir", sk, "tick"],
                        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    return cp, sk


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
        calls = [(c["skill"], c["key"]) for c in K.calls(sk)]
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

        good = "\n".join("STAGE: %s %s" % (s, PENDING.get(s, "ok")) for s in ORDER)
        swapped = good.replace("STAGE: sweep ok\nSTAGE: growth-review ok",
                               "STAGE: growth-review ok\nSTAGE: sweep ok")
        for label, out, code, want in (
            ("order-swapped", swapped, 0, "stages"),
            ("digest-missing", good.rsplit("\n", 1)[0], 0, "stages"),
            ("fake-ok-plan", good.replace("plan pending-R17", "plan ok"), 0,
             "plan says ok"),
            ("non-zero-exit", good, 1, "exit 1"),
        ):
            bad = tick_errors(out, code)
            check("grader rejects negative fixture %s" % label,
                  any(want in e for e in bad), "; ".join(bad)[:100])
        check("grader accepts the known-good transcript", not tick_errors(good, 0))
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
