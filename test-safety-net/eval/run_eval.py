#!/usr/bin/env python3
"""Outcome eval for test-safety-net (see the repo's docs/eval-standard.md).

Stdlib-only, offline, deterministic; scratch writes go to a tempdir only.
Prints one `CHECK: <name> — PASS|FAIL` line per check and a final
`EVAL_RESULT: PASS (n/n checks)` line; exits 0 iff every check passed.

TODO(repo2skill): this stub grades the skill's contract surface (frontmatter
shape plus body structure) with a model-free grader and a known-bad negative
fixture. Replace/extend it with an end-to-end harness -> skill tooling ->
grader pipeline once the skill ships real tooling — keep at least one
negative fixture; an eval that cannot fail is not an eval.
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = " (%s)" % detail if detail else ""
    print("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL", suffix))


def frontmatter_errors(text):
    """Model-free grader: return a list of contract violations in a SKILL.md."""
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
    desc = fields.get("description", "")
    if not desc or len(desc) > 1024:
        errors.append("description missing, empty, or over 1024 characters")
    return errors


def main():
    with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as f:
        text = f.read()

    check("SKILL.md frontmatter contract holds", not frontmatter_errors(text))

    body = text.split("---", 2)[2] if text.count("---") >= 2 else ""
    check(
        "body keeps gate-passing structure (>=3 sections, >=3 steps)",
        len(re.findall(r"^## ", body, re.M)) >= 3
        and len(re.findall(r"^[0-9]+\. ", body, re.M)) >= 3,
    )

    tmp = tempfile.mkdtemp(prefix="test_safety_net-eval-")
    try:
        bad = os.path.join(tmp, "SKILL.md")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("---\nname: Bad_Name\ndescription:\n---\nbody\n")
        with open(bad, encoding="utf-8") as f:
            bad_errors = frontmatter_errors(f.read())
        check(
            "grader rejects known-bad frontmatter fixture",
            len(bad_errors) >= 2,
            "; ".join(bad_errors)[:80],
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print("EVAL_RESULT: %s (%d/%d checks)" % ("PASS" if ok else "FAIL", k, n))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
