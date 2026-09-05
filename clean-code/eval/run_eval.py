#!/usr/bin/env python3
"""Gate-runnable outcome eval for clean-code (docs/eval-standard.md).

The skill's deliverable is agent behavior — a proportionate review or a
better-designed module — so this eval grades the deterministic contract
surface an agent actually consumes: the progressive-disclosure link graph
between SKILL.md and references/, the review-report template the skill tells
the agent to emit, the smells-to-fixes table it dispatches on, and the
principle vocabulary its description promises.

Negative fixtures (mandatory): a dangling reference link, an orphaned
reference file, a report template missing its Fix slot, and a smells row with
no fix must each be rejected by the same graders that accept the shipped
tree. Offline, deterministic, stdlib-only, tempdir-only writes.
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
    suffix = f" ({detail})" if detail else ""
    print(f"CHECK: {name} — {'PASS' if ok else 'FAIL'}{suffix}")


# ── graders (pure functions over a skill tree; used for good AND bad input) ──

def linked_references(skill_md_text):
    """Reference paths SKILL.md points the agent at."""
    return set(re.findall(r"references/[A-Za-z0-9_.-]+\.md", skill_md_text))


def dangling_links(tree):
    """Linked reference files that do not exist on disk."""
    text = open(os.path.join(tree, "SKILL.md"), encoding="utf-8").read()
    return sorted(p for p in linked_references(text)
                  if not os.path.isfile(os.path.join(tree, p)))


def orphan_references(tree):
    """Reference files on disk that SKILL.md never points at."""
    text = open(os.path.join(tree, "SKILL.md"), encoding="utf-8").read()
    linked = linked_references(text)
    refdir = os.path.join(tree, "references")
    on_disk = {f"references/{n}" for n in os.listdir(refdir) if n.endswith(".md")}
    return sorted(on_disk - linked)


def section(text, heading):
    """Body of a `## heading` section, ending at the next top-level heading.

    Fence-aware: the review-report template embeds a `## Clean Code Review`
    heading inside a code fence, and a naive split would truncate the section
    at its own example.
    """
    lines, out, inside, fence = text.splitlines(), [], False, False
    for line in lines:
        if line.startswith("```"):
            fence = not fence
        if not fence and line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip() == heading
            continue
        if inside:
            out.append(line)
    return "\n".join(out)


REPORT_SLOTS = ["## Clean Code Review", "**Overall:**", "**Findings**",
                "- What:", "- Why it matters:", "- Fix:", "**Already good:**"]


def missing_report_slots(text):
    """Slots the review-report template must carry to be fillable."""
    body = section(text, "Review report format")
    return [s for s in REPORT_SLOTS if s not in body]


def smell_rows(text):
    """(smell, fix) pairs from the 'Code smells → fixes' table."""
    rows = []
    for line in section(text, "Code smells → fixes").splitlines():
        if not line.startswith("|") or set(line) <= set("|- "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == 2 and cells[0].lower() != "smell":
            rows.append((cells[0], cells[1]))
    return rows


def rows_without_fix(rows):
    return [s for s, fix in rows if not fix.strip()]


def main():
    skill_md = open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8").read()

    # ── Progressive-disclosure link graph is intact both directions ──────
    check("every reference SKILL.md links resolves on disk",
          not dangling_links(SKILL), ", ".join(dangling_links(SKILL)))
    check("every reference file on disk is reachable from SKILL.md",
          not orphan_references(SKILL), ", ".join(orphan_references(SKILL)))
    linked = linked_references(skill_md)
    check("SKILL.md links the full L2 reference set", len(linked) >= 8,
          f"{len(linked)} references linked")
    thin = [p for p in sorted(linked)
            if len(open(os.path.join(SKILL, p), encoding="utf-8").read().splitlines()) < 20]
    check("no linked reference is a stub", not thin, ", ".join(thin))

    # ── The review-report template is fillable as written ────────────────
    missing = missing_report_slots(skill_md)
    check("review-report template carries every slot an agent must fill",
          not missing, ", ".join(missing))

    # ── The smells table dispatches to a fix for every smell ─────────────
    rows = smell_rows(skill_md)
    check("smells table parses with rows", len(rows) >= 10, f"{len(rows)} rows")
    check("every smell maps to a non-empty fix", not rows_without_fix(rows),
          ", ".join(rows_without_fix(rows)))
    core = ["Rigidity", "Fragility", "Immobility", "Needless complexity",
            "Needless repetition", "Opacity"]
    named = " ".join(s for s, _ in rows)
    absent = [c for c in core if c not in named]
    check("smells table covers the core design smells", not absent,
          ", ".join(absent))

    # ── Principle vocabulary the description promises is actually taught ──
    corpus = skill_md + "".join(
        open(os.path.join(SKILL, p), encoding="utf-8").read() for p in sorted(linked))
    for term, label in [("SOLID", "SOLID"), ("DRY", "DRY"), ("YAGNI", "YAGNI"),
                        ("Law of Demeter", "Law of Demeter")]:
        check(f"skill teaches {label}", term in corpus)
    solid = {"SRP": "SRP", "OCP": "OCP", "Liskov": "LSP", "ISP": "ISP", "DIP": "DIP"}
    absent = [lbl for t, lbl in solid.items() if t not in corpus]
    check("all five SOLID principles are covered somewhere in the tree",
          not absent, ", ".join(absent))

    # ── Frontmatter carries what the repo's metadata standard requires ────
    fm = skill_md.split("---", 2)[1] if skill_md.startswith("---") else ""
    for key in ["name:", "description:", "license:", "author:", "version:", "tags:"]:
        check(f"frontmatter declares {key.rstrip(':')}", key in fm)

    # ── Negative fixtures: the same graders must REJECT known-bad trees ───
    tmp = tempfile.mkdtemp(prefix="clean-code-eval-")
    try:
        # (1) dangling link
        bad = os.path.join(tmp, "dangling")
        shutil.copytree(SKILL, bad, ignore=shutil.ignore_patterns("eval", "__pycache__"))
        os.remove(os.path.join(bad, "references", "glossary.md"))
        check("negative: a dangling reference link is rejected",
              bool(dangling_links(bad)), "references/glossary.md removed")

        # (2) orphaned reference file
        bad2 = os.path.join(tmp, "orphan")
        shutil.copytree(SKILL, bad2, ignore=shutil.ignore_patterns("eval", "__pycache__"))
        open(os.path.join(bad2, "references", "unlinked.md"), "w").write("# orphan\n")
        check("negative: an unreachable reference file is rejected",
              bool(orphan_references(bad2)), "references/unlinked.md added")

        # (3) report template missing its Fix slot
        gutted = skill_md.replace("   - Fix: <concrete change, with a snippet if it clarifies>\n", "")
        check("negative: a report template missing the Fix slot is rejected",
              "- Fix:" in missing_report_slots(gutted),
              "template no longer tells the agent what to do")

        # (4) a smell with no fix
        emptied = skill_md.replace("| **Needless repetition** | DRY; extract |",
                                   "| **Needless repetition** |  |")
        check("negative: a smell with no fix is rejected",
              rows_without_fix(smell_rows(emptied)) == ["**Needless repetition**"])

        # (5) a tree with no smells table at all
        check("negative: a missing smells table yields no rows",
              smell_rows(skill_md.replace("## Code smells → fixes", "## Other")) == [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print(f"EVAL_RESULT: {'PASS' if ok else 'FAIL'} ({k}/{n} checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
