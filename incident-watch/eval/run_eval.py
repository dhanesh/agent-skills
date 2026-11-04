#!/usr/bin/env python3
"""Outcome eval for incident-watch (see the repo's docs/eval-standard.md).

Stdlib-only, offline, deterministic; scratch writes go to a tempdir only.
Prints one `CHECK: <name> — PASS|FAIL` line per check and a final
`EVAL_RESULT: PASS (n/n checks)` line; exits 0 iff every check passed.

The skill's promise (R22): `incident probe` runs only the release recipe's read-only
`health`, `version_probe` and `prod_smoke` commands, records each result in a git-ignored
state directory, and on a passing recipe records a pass and opens no record. The grader
runs the real probe on a fixture repo from assets/incident_testkit.py, whose commands are
local stub scripts (no real endpoint), and checks:

  - a passing recipe: exit 0, one PROBE: line per probe command in order, a pass summary,
    one recorded pass, no incident record, the state directory git-ignored;
  - a failing health stub: exit 3, the failure recorded and counted;
  - in both, no non-probe stub (build, deploy, rollback, staging check) was called;
  - SKILL.md and README.md say "partly built", not the old skeleton line, and every
    skill-relative path they name exists.

Negative fixtures break one property each and require the grader to reject them.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
ASSETS = os.path.join(SKILL, "assets")
sys.path.insert(0, ASSETS)
import incident_testkit as kit  # noqa: E402

TOOL = os.path.join(ASSETS, "incident.py")
SKILLS_ROOT = os.path.dirname(SKILL)  # release-conductor sits beside incident-watch
STATUS = "status: partly built"
OLD_STATUS = "no command ships yet"
PROBE_NAMES = ["health", "version_probe", "prod_smoke[0]", "prod_smoke[1]"]
STATE_FILES = {".gitignore", ".lock", "probes.jsonl", "state.json"}
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


def doc_errors(skill_dir):
    """What SKILL.md and README.md claim that is not true."""
    errors = []
    for doc in ("SKILL.md", "README.md"):
        path = os.path.join(skill_dir, doc)
        if not os.path.isfile(path):
            errors.append("%s missing" % doc)
            continue
        text = read(path)
        if STATUS not in text.lower():
            errors.append("%s lacks the partly-built status" % doc)
        if OLD_STATUS in text.lower():
            errors.append("%s still says no command ships" % doc)
        text = GRAPH_BLOCK.sub("", text)
        for rel in sorted(set(PATH_RE.findall(text))):
            if not os.path.exists(os.path.join(skill_dir, rel)):
                errors.append("%s names %s, which does not exist" % (doc, rel))
    return errors


def run_probe(root):
    cp = subprocess.run([sys.executable, TOOL, "--root", root, "--skills-dir", SKILLS_ROOT,
                         "probe"], capture_output=True, text=True, timeout=60)
    return cp.returncode, cp.stdout


def grade_probe(root, rc, out, expect):
    """Model-free grader for one probe run. `expect` is "pass" or "fail" (health failed)."""
    errors = []
    want_rc = 0 if expect == "pass" else 3
    if rc != want_rc:
        errors.append("exit %s, want %s" % (rc, want_rc))
    lines = out.splitlines()
    names = [ln.split()[1] for ln in lines if ln.startswith("PROBE: ")]
    if names != PROBE_NAMES:
        errors.append("PROBE: lines name %s" % names)
    summary = [ln for ln in lines if ln.startswith("PROBE_RESULT: ")]
    if len(summary) != 1 or not summary[0].startswith("PROBE_RESULT: %s " % expect):
        errors.append("no PROBE_RESULT: %s line" % expect)
    d = os.path.join(root, ".skill-contract", "incidents")
    try:
        with open(os.path.join(d, "probes.jsonl"), encoding="utf-8") as f:
            recs = [json.loads(ln) for ln in f if ln.strip()]
    except (OSError, ValueError):
        recs = []
    if len(recs) != 1 or recs[0].get("result") != expect:
        errors.append("probes.jsonl does not hold one %s" % expect)
    else:
        cmds = recs[0].get("commands") or []
        if [c.get("name") for c in cmds] != PROBE_NAMES:
            errors.append("recorded commands differ")
        if any(not {"at", "argv", "rc", "out_tail"} <= set(c) for c in cmds):
            errors.append("a recorded command lacks time, argv, rc or output tail")
    extra = sorted(set(os.listdir(d)) - STATE_FILES) if os.path.isdir(d) else []
    if extra:
        errors.append("unexpected state files %s" % extra)
    if expect == "pass" and os.path.isdir(d):
        if subprocess.run(["git", "-C", root, "check-ignore", "-q",
                           os.path.join(d, "probes.jsonl")]).returncode != 0:
            errors.append("state directory is not git-ignored")
    bad = [c[0] for c in kit.calls(root) if c[0] in kit.NON_PROBE]
    if bad:
        errors.append("non-probe command ran: %s" % bad)
    return errors


def main():
    text = read(os.path.join(SKILL, "SKILL.md"))
    fm = frontmatter_errors(text)
    check("SKILL.md frontmatter contract holds", not fm, "; ".join(fm)[:80])
    body = text.split("---", 2)[2] if text.count("---") >= 2 else ""
    check("body keeps gate-passing structure (>=3 sections, >=3 steps)",
          len(re.findall(r"^## ", body, re.M)) >= 3
          and len(re.findall(r"^[0-9]+\. ", body, re.M)) >= 3)
    errs = doc_errors(SKILL)
    check("docs state the true status and name only real paths", not errs,
          "; ".join(errs)[:120])
    check("SKILL.md forbids running a rollback or deploy",
          "You MUST NOT run a rollback, a deploy" in text)

    tmp = tempfile.mkdtemp(prefix="incident_watch-eval-")
    try:
        good = kit.fixture(os.path.join(tmp, "good"))
        rc, out = run_probe(good)
        errs = grade_probe(good, rc, out, "pass")
        check("passing recipe: recorded pass, no record, nothing else run", not errs,
              "; ".join(errs)[:120])

        bad = kit.fixture(os.path.join(tmp, "bad"), fail=["health"])
        brc, bout = run_probe(bad)
        errs = grade_probe(bad, brc, bout, "fail")
        check("failing health: failure recorded, nothing else run", not errs,
              "; ".join(errs)[:120])

        # Negative fixtures: each must be rejected.
        check("grader rejects negative fixture failing-run-as-pass",
              grade_probe(bad, brc, bout, "pass"))
        no_summary = "\n".join(ln for ln in out.splitlines()
                               if not ln.startswith("PROBE_RESULT"))
        check("grader rejects negative fixture missing-summary",
              grade_probe(good, rc, no_summary, "pass"))
        with open(os.path.join(good, "stubs", "calls.log"), "a", encoding="utf-8") as f:
            f.write("rollback\n")
        check("grader rejects negative fixture rollback-called",
              any("non-probe" in e for e in grade_probe(good, rc, out, "pass")))
        rec = os.path.join(good, ".skill-contract", "incidents", "incident-1.json")
        with open(rec, "w", encoding="utf-8") as f:
            f.write("{}\n")
        check("grader rejects negative fixture record-opened-on-pass",
              any("unexpected state" in e for e in grade_probe(good, rc, out, "pass")))
        os.remove(os.path.join(good, ".skill-contract", "incidents", "probes.jsonl"))
        check("grader rejects negative fixture nothing-recorded",
              any("probes.jsonl" in e for e in grade_probe(good, rc, out, "pass")))

        stale = os.path.join(tmp, "stale")
        shutil.copytree(SKILL, stale, ignore=shutil.ignore_patterns("__pycache__"))
        with open(os.path.join(stale, "SKILL.md"), "a", encoding="utf-8") as f:
            f.write("\nIn development: no command ships yet. Run `assets/close.py`.\n")
        errs = doc_errors(stale)
        check("grader rejects negative fixture stale-status-and-missing-script",
              any("no command ships" in e for e in errs)
              and any("assets/close.py" in e for e in errs), "; ".join(errs)[:100])
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
