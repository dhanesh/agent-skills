#!/usr/bin/env python3
"""Outcome eval for incident-watch (see the repo's docs/eval-standard.md).

Stdlib-only, offline, deterministic; scratch writes go to a tempdir only.
Prints one `CHECK: <name> — PASS|FAIL` line per check and a final
`EVAL_RESULT: PASS (n/n checks)` line; exits 0 iff every check passed.

The skill is a skeleton: no command ships yet. Its promise is honesty, so the
grader checks that it claims nothing it does not have:

  - SKILL.md and README.md both carry the in-development line;
  - every skill-relative path they name (references/, assets/, scripts/, eval/)
    exists, so no script is claimed before it ships;
  - no tooling ships besides test suites, so the in-development line is true.

Negative fixtures copy the skill to a tempdir, break one property each, and
require the grader to reject the copy.
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)

STATUS = "in development: no command ships yet"
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


def honesty_errors(skill_dir):
    """Model-free grader: what the skeleton claims that it does not have."""
    errors = []
    for doc in ("SKILL.md", "README.md"):
        path = os.path.join(skill_dir, doc)
        if not os.path.isfile(path):
            errors.append("%s missing" % doc)
            continue
        text = read(path)
        if STATUS not in text.lower():
            errors.append("%s lacks the in-development line" % doc)
        # The generated skill-graph block names repo-level gate scripts, not this
        # skill's own files, so it is left out of the path scan.
        text = GRAPH_BLOCK.sub("", text)
        for rel in sorted(set(PATH_RE.findall(text))):
            if not os.path.exists(os.path.join(skill_dir, rel)):
                errors.append("%s names %s, which does not exist" % (doc, rel))
    for sub in ("assets", "scripts"):
        root = os.path.join(skill_dir, sub)
        for dirpath, _, files in os.walk(root):
            for fn in sorted(files):
                if fn.startswith("test_") or fn == "__pycache__":
                    continue
                if dirpath.endswith("__pycache__"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), skill_dir)
                errors.append("ships %s while it says no command ships" % rel)
    return errors


def broken_copy(tmp, label, mutate):
    """Copy the skill into tmp/label, apply mutate(dir), return the grader's errors."""
    dst = os.path.join(tmp, label)
    shutil.copytree(SKILL, dst, ignore=shutil.ignore_patterns("__pycache__"))
    mutate(dst)
    return honesty_errors(dst)


def drop_status(d):
    p = os.path.join(d, "SKILL.md")
    text = read(p)
    text = re.sub(re.escape(STATUS), "ready", text, flags=re.I)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def claim_script(d):
    with open(os.path.join(d, "SKILL.md"), "a", encoding="utf-8") as f:
        f.write('\nRun `python3 "$SKILL_DIR/assets/incident.py" probe`.\n')


def ship_script(d):
    with open(os.path.join(d, "assets", "incident.py"), "w", encoding="utf-8") as f:
        f.write("print('probe')\n")


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

    errs = honesty_errors(SKILL)
    check("skeleton claims nothing it does not have", not errs, "; ".join(errs)[:120])

    tmp = tempfile.mkdtemp(prefix="incident_watch-eval-")
    try:
        for label, mutate, want in (
            ("no-status", drop_status, "lacks the in-development line"),
            ("claims-script", claim_script, "assets/incident.py, which does not exist"),
            ("ships-script", ship_script, "ships assets/incident.py"),
        ):
            bad = broken_copy(tmp, label, mutate)
            check(
                "grader rejects negative fixture %s" % label,
                any(want in e for e in bad),
                "; ".join(bad)[:100],
            )
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
