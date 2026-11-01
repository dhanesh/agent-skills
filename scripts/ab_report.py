#!/usr/bin/env python3
"""Per-skill improvement reports for `make ab-report` (stdlib only, offline).

`scripts/ab-validate.py` measures every A/B row against a baseline and prints one
verdict per row. This module turns that run into a durable record: one Markdown
report per branch under `docs/ab-reports/`, and an index there that lists, per
skill, every report that touched it. A reader can then answer "did the changes to
this skill make it better, and on which dimensions?" without re-running anything.

A report covers the skills that the branch changes. For each one it gives:

- every A/B row whose label names the skill, with old → new and the mark;
- the skill's outcome eval on both arms (`EVAL_RESULT: ... (k/n checks)`);
- a verdict: `improved`, `held`, `not measured` or `worse`.

`not measured` is a finding, not a pass: a changed skill with no A/B row and no eval
change is a claim nobody measured (CLAUDE.md). Rows for skills the branch does not
change appear only as counts, so a regression elsewhere is still visible.

Each report embeds its summary as JSON in an HTML comment. `write_report` rebuilds
the index from those comments, so the index never drifts from the reports.
"""
import json
import os
import re
import subprocess
import sys

SUMMARY_RE = re.compile(r"<!-- ab-report-summary: (.*?) -->")
EVAL_RE = re.compile(r"^EVAL_RESULT: (PASS|FAIL) \((\d+)/(\d+) checks\)", re.M)
FAIL_MARKS = ("WORSE", "UNPROVEN")
MARKS = ("IMPROVED", "HELD", "HELD*", "UNPROVEN", "WORSE")


def skill_dirs(repo):
    """Top-level directories that hold a SKILL.md."""
    return sorted(d for d in os.listdir(repo)
                  if os.path.isfile(os.path.join(repo, d, "SKILL.md")))


def owner_of(label):
    """The owner a row label names: a skill directory, or a repo-wide check.

    Labels are a skill directory name, or a repo-wide name such as `gates` or
    `bcp14`, optionally followed by a qualifier in brackets: `skill-contract
    (release)`, `okf-site-kit (consumer)`. The qualifier is dropped."""
    return label.split(" (", 1)[0].strip()


def changed_skills(repo, base, skills):
    """Skills with a file that differs between `base` and the working tree,
    untracked files included."""
    names = []
    for args in (["diff", "--name-only", base, "--"],
                 ["ls-files", "--others", "--exclude-standard"]):
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
        names += r.stdout.splitlines() if r.returncode == 0 else []
    counts = {}
    for n in names:
        top = n.split("/", 1)[0]
        if top in skills:
            counts[top] = counts.get(top, 0) + 1
    return counts


def eval_score(tree, skill, timeout=120):
    """(result, passed, total) from the skill's eval in `tree`, or None when the
    tree has no eval. A crash or a timeout gives ("ERROR", 0, 0)."""
    path = os.path.join(tree, skill, "eval", "run_eval.py")
    if not os.path.isfile(path):
        return None
    try:
        r = subprocess.run([sys.executable, path], cwd=tree, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ("ERROR", 0, 0)
    found = EVAL_RE.findall(r.stdout)
    if not found:
        return ("ERROR", 0, 0)
    result, passed, total = found[-1]
    return (result, int(passed), int(total))


def _fmt_eval(e):
    if e is None:
        return "none"
    if e[0] == "ERROR":
        return "error"
    return "%s %d/%d" % (e[0], e[1], e[2])


def verdict(marks, ev_old, ev_new):
    """One word for a changed skill, from its row marks and its eval on both arms."""
    if any(m in FAIL_MARKS for m in marks):
        return "worse"
    if ev_new is not None and (ev_new[0] != "PASS"
                               or (ev_old is not None and ev_old[0] == "PASS"
                                   and ev_new[1] < ev_old[1])):
        return "worse"
    if "IMPROVED" in marks:
        return "improved"
    if marks:
        return "held"
    return "not measured"


def _cell(v):
    return str(v).replace("|", "\\|").replace("\n", " ")


def build_report(rows, meta, changed, evals):
    """(markdown, summary) for one run.

    rows: dicts with skill (the raw label), dimension, old, new, note and mark.
    meta: branch, head, base, base_subject, date, result.
    changed: {skill: files changed}. evals: {skill: (old score, new score)}."""
    skills = sorted(set(changed))
    by_skill = {}
    for r in rows:
        by_skill.setdefault(owner_of(r["skill"]), []).append(r)
    counts = {m: sum(1 for r in rows if r["mark"] == m) for m in MARKS}

    summary = {"branch": meta["branch"], "head": meta["head"], "base": meta["base"],
               "date": meta["date"], "result": meta["result"], "skills": {}}
    for s in skills:
        marks = [r["mark"] for r in by_skill.get(s, [])]
        ev_old, ev_new = evals.get(s, (None, None))
        summary["skills"][s] = {
            "verdict": verdict(marks, ev_old, ev_new),
            "improved": marks.count("IMPROVED"),
            "rows": len(marks),
            "eval": [_fmt_eval(ev_old), _fmt_eval(ev_new)],
        }

    out = ["# A/B report: %s" % meta["branch"], "",
           "<!-- Generated by `make ab-report` (scripts/ab_report.py). Do not edit by "
           "hand: run it again. -->",
           "<!-- ab-report-summary: %s -->"
           % json.dumps(summary, sort_keys=True).replace("--", "-\\u002d"), "",
           "- Date: %s" % meta["date"],
           "- Candidate: `%s` (working tree)" % meta["head"],
           "- Baseline: `%s` %s" % (meta["base"], meta.get("base_subject", "")),
           "- Result: `AB_RESULT: %s`. %d improved, %d held, %d landed (HELD\\*), "
           "%d unproven, %d worse." % (meta["result"], counts["IMPROVED"],
                                       counts["HELD"], counts["HELD*"],
                                       counts["UNPROVEN"], counts["WORSE"]),
           "",
           "## What the marks mean", "",
           "- **IMPROVED**: the change claims a better number, and the number moved.",
           "- **HELD**: a guard. The number stays the same on purpose.",
           "- **HELD\\***: an earlier change proved this delta. It is now a guard.",
           "- **UNPROVEN**: the change claims a better number, but the number did not "
           "move.",
           "- **WORSE**: the new arm is worse.",
           "",
           "A skill is **not measured** when the branch changes it but no A/B row names "
           "it and its eval does not change. That is a gap to close, not a pass.",
           "", "## Skills this branch changes", ""]
    if not skills:
        out += ["The branch changes no skill directory.", ""]
    else:
        out += ["| Skill | Files | Verdict | Improved | A/B rows | Eval (base → head) |",
                "|---|---|---|---|---|---|"]
        for s in skills:
            v = summary["skills"][s]
            out.append("| `%s` | %d | %s | %d | %d | %s → %s |"
                       % (s, changed[s], v["verdict"], v["improved"], v["rows"],
                          v["eval"][0], v["eval"][1]))
        out.append("")
        for s in skills:
            out += ["### %s" % s, ""]
            rs = by_skill.get(s, [])
            if not rs:
                out += ["No A/B row names this skill.", ""]
                continue
            out += ["| Dimension | Base | Head | Mark |", "|---|---|---|---|"]
            for r in rs:
                dim = r["dimension"] + (" (%s)" % r["note"] if r.get("note") else "")
                out.append("| %s | %s | %s | %s |" % (
                    _cell(dim), _cell(r["old"]), _cell(r["new"]),
                    r["mark"].replace("*", "\\*")))
            out.append("")
    out += ["## Every skill and repo-wide check", "",
            "Counts of marks for every row label, so that a regression outside the "
            "changed skills is visible too.", "",
            "| Owner | Improved | Held | Landed | Unproven | Worse |",
            "|---|---|---|---|---|---|"]
    for owner in sorted(by_skill):
        ms = [r["mark"] for r in by_skill[owner]]
        out.append("| `%s` | %d | %d | %d | %d | %d |" % (
            owner, ms.count("IMPROVED"), ms.count("HELD"), ms.count("HELD*"),
            ms.count("UNPROVEN"), ms.count("WORSE")))
    out.append("")
    return "\n".join(out), summary


def slug(branch):
    return re.sub(r"[^a-z0-9.]+", "-", branch.lower()).strip("-.") or "detached"


def read_summaries(report_dir):
    """[(file name, summary)] for every report in the directory, newest first."""
    found = []
    for name in sorted(os.listdir(report_dir), reverse=True):
        if not name.endswith(".md") or name == "README.md":
            continue
        with open(os.path.join(report_dir, name), encoding="utf-8") as f:
            m = SUMMARY_RE.search(f.read())
        if m:
            found.append((name, json.loads(m.group(1))))
    return found


def build_index(summaries):
    out = ["# A/B reports", "",
           "<!-- Generated by `make ab-report` (scripts/ab_report.py). Do not edit by "
           "hand. -->", "",
           "Each report records how one branch changed the behaviour of the skills it "
           "touched, measured by `scripts/ab-validate.py` against the branch's merge "
           "base and by each skill's outcome eval on both arms. Make one with "
           "`make ab-report` before you merge a behavioural change, and commit it with "
           "the change.", "",
           "## By skill", ""]
    per = {}
    for name, s in summaries:
        for skill, v in s["skills"].items():
            per.setdefault(skill, []).append((name, s, v))
    if not per:
        out += ["No report yet.", ""]
    for skill in sorted(per):
        out += ["### %s" % skill, ""]
        for name, s, v in per[skill]:
            out.append("- %s [%s](%s): %s, %d improved of %d rows, eval %s → %s"
                       % (s["date"], s["branch"], name, v["verdict"], v["improved"],
                          v["rows"], v["eval"][0], v["eval"][1]))
        out.append("")
    out += ["## All reports", ""]
    for name, s in summaries:
        out.append("- %s [%s](%s): `AB_RESULT: %s`, %d skill(s)"
                   % (s["date"], s["branch"], name, s["result"], len(s["skills"])))
    out.append("")
    return "\n".join(out)


def write_report(report_dir, markdown, summary):
    """Write the report and rebuild the index. Returns the report path."""
    os.makedirs(report_dir, exist_ok=True)
    path = os.path.join(report_dir, "%s-%s.md" % (summary["date"], slug(summary["branch"])))
    with open(path, "w", encoding="utf-8") as f:
        f.write(markdown)
    with open(os.path.join(report_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(build_index(read_summaries(report_dir)))
    return path
