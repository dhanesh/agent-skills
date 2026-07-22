#!/usr/bin/env python3
"""Gate-runnable outcome eval for starlight-handbook-kit (docs/eval-standard.md).

The skill's deliverable contract: the scaffold in assets/templates/scaffold/
fills cleanly from the five documented PARAMETERS.md examples (no residual
{{TOKENS}}), and every produced topic page follows the fixed nine-section
skeleton (U1) in the documented order — the same invariant the scaffold's own
check-pages-skeleton.mjs enforces in CI.

The eval replays the documented examples through the repo's dry-run gate,
then grades the replayed canonical topic template and the shipped example
topic page with a model-free reimplementation of the U1 section check.

Negative fixtures: a topic fixture with one section deleted (and one with two
sections reordered) must be rejected by the skeleton grader, and a doctored
PARAMETERS.md missing a placeholder row must FAIL the replay.

Offline, deterministic, stdlib-only; all scratch under tempfile.mkdtemp().
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REPO = os.path.dirname(SKILL)
DRY_RUN = os.path.join(REPO, "scripts", "gates", "dry-run-replay.sh")

# The nine-section topic skeleton (U1), fixed names and order — mirrors
# assets/templates/scaffold/scripts/check-pages-skeleton.mjs and topic.mdx.
EXPECTED_SECTIONS = [
    "Overview",
    "Mental model",
    "Types / Variants",
    "When to use / When NOT",
    "Tradeoffs",
    "Diagram",
    "Try it",
    "Real-world examples",
    "Further reading",
]

RESIDUAL_RE = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = f" ({detail})" if detail else ""
    print(f"CHECK: {name} — {'PASS' if ok else 'FAIL'}{suffix}")


def grade_skeleton(text):
    """Model-free U1 grader: all nine sections present, in order.

    Same rules as check-pages-skeleton.mjs: level-2 headings in document
    order, ignoring anything inside fenced code blocks.
    """
    no_fences = re.sub(r"```[\s\S]*?```", "", text)
    sections = [m.group(1).strip()
                for m in re.finditer(r"^##\s+(.+?)\s*$", no_fences, re.M)]
    if len(sections) != len(EXPECTED_SECTIONS):
        return (False, f"expected {len(EXPECTED_SECTIONS)} sections, found {len(sections)}")
    for i, (got, want) in enumerate(zip(sections, EXPECTED_SECTIONS)):
        if got != want:
            return (False, f'section {i + 1} should be "{want}" but is "{got}"')
    return (True, "9/9 sections in order")


def copy_skill(dst):
    shutil.copytree(
        SKILL, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "eval", "node_modules"),
    )


def run_replay(skill_dir, scratch_dir):
    return subprocess.run(
        ["sh", DRY_RUN, skill_dir, scratch_dir],
        capture_output=True, text=True, timeout=60,
    )


def scan_residual(root):
    tokens = set()
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            try:
                with open(path, encoding="utf-8") as f:
                    tokens.update(RESIDUAL_RE.findall(f.read()))
            except (UnicodeDecodeError, OSError):
                continue  # binary assets (e.g. images) are copied verbatim
    return sorted(tokens)


def main():
    tmp = tempfile.mkdtemp(prefix="shk-eval-")
    try:
        # ── Positive arm: replay the documented examples into the scaffold ─────
        pristine = os.path.join(tmp, "pristine")
        scratch = os.path.join(tmp, "scratch")
        copy_skill(pristine)
        r = run_replay(pristine, scratch)
        check("dry-run replay of documented PARAMETERS.md examples passes",
              r.returncode == 0 and "DRY_RUN_RESULT: PASS" in r.stdout,
              f"rc={r.returncode}")

        residual = scan_residual(scratch)
        check("no residual {{TOKENS}} anywhere in the replayed scaffold",
              os.path.isdir(scratch) and not residual,
              f"residual={residual or 'none'}")

        pkg = astro = ""
        pkg_path = os.path.join(scratch, "scaffold", "package.json")
        astro_path = os.path.join(scratch, "scaffold", "astro.config.mjs")
        if os.path.isfile(pkg_path):
            pkg = open(pkg_path).read()
        if os.path.isfile(astro_path):
            astro = open(astro_path).read()
        check("documented example values actually landed in the scaffold",
              '"name": "my-handbook"' in pkg
              and "My Engineering Handbook" in astro
              and "https://myuser.github.io" in astro
              and "'/my-handbook'" in astro,
              "PKG_NAME, SITE_TITLE, GH_PAGES_HOST, DEPLOY_BASE substituted")

        # ── Produced topic pages carry the nine sections in order ──────────────
        topic_path = os.path.join(scratch, "scaffold", "templates", "topic.mdx")
        topic = open(topic_path).read() if os.path.isfile(topic_path) else ""
        ok, detail = grade_skeleton(topic)
        check("canonical topic template carries all nine sections in order",
              topic and ok, detail)

        example_path = os.path.join(
            scratch, "scaffold", "src", "content", "docs",
            "example-cluster", "example-topic.mdx")
        example = open(example_path).read() if os.path.isfile(example_path) else ""
        ok, detail = grade_skeleton(example)
        check("shipped example topic page carries all nine sections in order",
              example and ok, detail)

        # ── Negative arm 1: topic fixtures that violate the skeleton ───────────
        # Delete the whole "## Tradeoffs" section (heading + body).
        one_deleted = re.sub(r"^## Tradeoffs$[\s\S]*?(?=^## Diagram$)", "",
                             topic, flags=re.M)
        ok, detail = grade_skeleton(one_deleted)
        check("negative: grader rejects a topic page with one section deleted",
              topic and not ok, detail)

        # Swap the first two headings — count stays nine, order is wrong.
        swapped = (topic
                   .replace("## Mental model", "## __TMP__", 1)
                   .replace("## Overview", "## Mental model", 1)
                   .replace("## __TMP__", "## Overview", 1))
        ok, detail = grade_skeleton(swapped)
        check("negative: grader rejects a topic page with sections out of order",
              topic and not ok, detail)

        # ── Negative arm 2: doctored params missing a placeholder ──────────────
        doctored = os.path.join(tmp, "doctored")
        copy_skill(doctored)
        params_path = os.path.join(doctored, "PARAMETERS.md")
        with open(params_path) as f:
            rows = f.readlines()
        rows = [ln for ln in rows if "{{PKG_NAME}}" not in ln]
        with open(params_path, "w") as f:
            f.writelines(rows)
        r_bad = run_replay(doctored, os.path.join(tmp, "scratch-doctored"))
        check("negative: replay FAILS when a placeholder row is missing from PARAMETERS.md",
              r_bad.returncode != 0
              and "DRY_RUN_RESULT: FAIL" in r_bad.stdout
              and "{{PKG_NAME}}" in r_bad.stdout,
              f"rc={r_bad.returncode}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print(f"EVAL_RESULT: {'PASS' if ok else 'FAIL'} ({k}/{n} checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
