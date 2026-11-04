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
  - R23: `probe --only rollback` and `probe rollback` exit 2 with `PROBE: refused:` and
    call no stub at all, not even the allowed ones;
  - R24: a failing health stub probed twice opens one incident record (with the probe
    output) and writes one `incident` signal line; a third failure opens no second record;
  - R25: that record holds the recipe's rollback argv word for word and the
    release-conductor command that asks the owner, the probe that opened it printed the
    `ROLLBACK:` and `NEXT: ask the owner` lines, and the rollback stub never ran;
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


def run_probe(root, *extra):
    cp = subprocess.run([sys.executable, TOOL, "--root", root, "--skills-dir", SKILLS_ROOT,
                         "probe"] + list(extra), capture_output=True, text=True, timeout=60)
    return cp.returncode, cp.stdout


def grade_refusal(root, rc, out):
    """Model-free grader for one R23 refusal: exit 2, one refusal line, no stub call."""
    errors = []
    if rc != 2:
        errors.append("exit %s, not 2" % rc)
    if not out.startswith("PROBE: refused: "):
        errors.append("no PROBE: refused: line")
    if kit.calls(root):
        errors.append("a command ran: %s" % kit.calls(root))
    return errors


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


def grade_incident(root, out):
    """Model-free grader for R24 after 2 or more failed probes: exactly one open record
    that holds the failing probe output, exactly one `incident` signal line naming it, an
    INCIDENT: line, and no non-probe stub called."""
    errors = []
    d = os.path.join(root, ".skill-contract", "incidents")
    rdir = os.path.join(d, "records")
    names = sorted(os.listdir(rdir)) if os.path.isdir(rdir) else []
    if len(names) != 1:
        errors.append("%d incident records, want 1" % len(names))
    else:
        try:
            with open(os.path.join(rdir, names[0]), encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            rec = {}
        if rec.get("status") != "open":
            errors.append("record is not open")
        tails = [c.get("out_tail", "") for p in rec.get("probes", [])
                 for c in p.get("commands", [])]
        if not any("stub health production exit 1" in t for t in tails):
            errors.append("record lacks the failing probe output")
        for key in ("last_release", "ci_runs"):
            if key not in rec:
                errors.append("record lacks %s" % key)
        rb = rec.get("rollback") or {}
        try:
            with open(os.path.join(root, ".release", "recipe.json"), encoding="utf-8") as f:
                want = json.load(f).get("rollback")
        except (OSError, ValueError):
            want = None
        if not rb.get("available") or rb.get("argv") != want:
            errors.append("record lacks the recipe's rollback argv word for word")
        if (rb.get("ask_owner") or {}).get("command") != "release rollback":
            errors.append("record lacks the release-conductor command that asks the owner")
    try:
        with open(os.path.join(d, "signals.jsonl"), encoding="utf-8") as f:
            sigs = [json.loads(ln) for ln in f if ln.strip()]
    except (OSError, ValueError):
        sigs = []
    if len(sigs) != 1 or sigs[0].get("kind") != "incident":
        errors.append("signals.jsonl does not hold one incident line")
    elif names and sigs[0].get("source_id") + ".json" != names[0]:
        errors.append("signal does not name the record")
    if not any(ln.startswith("INCIDENT: ") for ln in out.splitlines()):
        errors.append("no INCIDENT: line")
    bad = [c[0] for c in kit.calls(root) if c[0] in kit.NON_PROBE]
    bad += [n for n in kit.ran(root) if n in kit.NON_PROBE]
    if bad:
        errors.append("non-probe command ran: %s" % bad)
    return errors


def grade_opening(out):
    """R25: the output of the probe that opened the record shows the rollback and the
    command that asks the owner."""
    lines = out.splitlines()
    errors = []
    if not any(ln.startswith("ROLLBACK: ") and ln.endswith(" rollback") for ln in lines):
        errors.append("no ROLLBACK: line with the recipe's rollback")
    if not any(ln.startswith("NEXT: ask the owner to roll back: release rollback ")
               for ln in lines):
        errors.append("no NEXT: line with the release-conductor command")
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

        inc = kit.fixture(os.path.join(tmp, "incident"), fail=["health"])
        run_probe(inc)
        errs = []
        if os.path.isdir(os.path.join(inc, ".skill-contract", "incidents", "records")):
            errs.append("a record after one failure")
        _, oout = run_probe(inc)
        _, iout = run_probe(inc)
        errs += grade_incident(inc, iout)
        check("two failed probes: one record and one incident signal; a third adds none "
              "(R24)", not errs, "; ".join(errs)[:120])
        errs = grade_opening(oout)
        check("opened record shows the rollback argv and the owner's command; the rollback "
              "stub never ran (R25)", not errs, "; ".join(errs)[:120])

        ref = kit.fixture(os.path.join(tmp, "refuse"))
        errs = []
        for extra in (["--only", "rollback"], ["rollback"], ["--only", "health",
                                                             "--only", "deploy_prod"]):
            rrc, rout = run_probe(ref, *extra)
            errs += grade_refusal(ref, rrc, rout)
        check("unlisted command: refused with exit 2, nothing run (R23)", not errs,
              "; ".join(errs)[:120])

        # Negative fixtures: each must be rejected.
        check("grader rejects negative fixture refusal-that-ran-a-command",
              grade_refusal(good, 2, "PROBE: refused: x\n"))
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

        rdir = os.path.join(inc, ".skill-contract", "incidents", "records")
        dup = os.path.join(rdir, "inc-copy.json")
        shutil.copy(os.path.join(rdir, sorted(os.listdir(rdir))[0]), dup)
        check("grader rejects negative fixture second-record-opened",
              any("2 incident records" in e for e in grade_incident(inc, iout)))
        os.remove(dup)
        check("grader rejects negative fixture rollback-not-shown",
              grade_opening(iout))
        rpath = os.path.join(rdir, sorted(os.listdir(rdir))[0])
        with open(rpath, encoding="utf-8") as f:
            saved = f.read()
        tampered = json.loads(saved)
        tampered["rollback"]["argv"] = tampered["rollback"]["argv"][:-1] + ["--all"]
        with open(rpath, "w", encoding="utf-8") as f:
            json.dump(tampered, f)
        check("grader rejects negative fixture rollback-argv-changed",
              any("word for word" in e for e in grade_incident(inc, iout)))
        with open(rpath, "w", encoding="utf-8") as f:
            f.write(saved)
        os.makedirs(os.path.join(inc, "stubs", "ran"), exist_ok=True)
        with open(os.path.join(inc, "stubs", "ran", "rollback"), "w") as f:
            f.write("ran\n")
        check("grader rejects negative fixture rollback-marker-written",
              any("non-probe" in e for e in grade_incident(inc, iout)))
        os.remove(os.path.join(inc, "stubs", "ran", "rollback"))
        sig = os.path.join(inc, ".skill-contract", "incidents", "signals.jsonl")
        with open(sig, encoding="utf-8") as f:
            first_sig = f.read()
        with open(sig, "a", encoding="utf-8") as f:
            f.write(first_sig)
        check("grader rejects negative fixture second-signal-line",
              any("one incident line" in e for e in grade_incident(inc, iout)))

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
