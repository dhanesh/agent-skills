#!/usr/bin/env python3
"""Outcome eval for test-safety-net (see the repo's docs/eval-standard.md).

Deterministic harness -> skill tooling -> model-free grader, stdlib-only, offline.
Fixture repos are built under `tempfile.mkdtemp()` only; nothing tracked in the
repo is ever touched.

SCOPE, STATED PLAINLY. This eval grades three things:

  1. `assets/rank_risk.py`'s CLI, end to end, on synthetic fixture repos --
     the static triage FILTER that ranks and declines candidates before any
     test is written (checks 01-12, 20-21).
  2. The prompt artifacts (SKILL.md, references/triage.md) that carry the
     workflow and the guard's contract (checks 13-21, 28-29).
  3. `assets/io_guard.py`, the runtime guard that is the SOLE enforcement of
     "never write a test that performs real I/O" -- armed for real, made to
     face real I/O, and required to raise its own exception type (checks
     22-27). Those are negative fixtures: they perform genuine filesystem,
     network and subprocess calls with the guard armed, so a guard that
     patched the wrong layer, or none, fails them.

What it still does NOT cover, said plainly rather than implied away:

  * Whether the model writes GOOD tests. That is the limit `CLAUDE.md` records
    for prompt-driven behaviour; it needs the manual, documented model eval.
  * The pytest PLUGIN path is covered CONDITIONALLY (check 34). This eval must
    run with the stdlib alone, so it cannot IMPORT pytest -- which is how the
    plugin path escaped grading entirely while the guard was unusable under
    every command the documents print. A subprocess invocation needs no
    import, so check 34 extracts the documented command string from each
    document and runs it verbatim wherever pytest is installed, and reports
    itself NOT GRADED where it is not. What remains ungated: an environment
    with no pytest grades the plugin path nowhere -- `assets/test_io_guard.py`
    skips there too -- so a `make gate` on such a machine is green on 34
    without having proved anything. That is stated rather than hidden.
  * The node GUARD is covered CONDITIONALLY (check 39), in exactly the shape
    check 34 has and for the same reason: this eval must not depend on a
    toolchain it cannot install, so with no `node` on PATH check 39 reports
    itself NOT GRADED HERE and passes. What remains ungated: a machine with
    neither pytest nor node prints `EVAL_RESULT: PASS` while having graded
    NEITHER guard — that is, neither stack's enforcement of the skill's
    headline invariant. `assets/test_io_guard_node.sh` skips on the same
    machine. CI pins both toolchains (`.github/workflows/skill-gates.yml` sets
    up python AND node), so the grading there is a pin rather than an accident
    of the runner image; a local `make gate` on a bare machine is not.
  * The go GUARD is covered CONDITIONALLY too (check 44), in the same shape as
    34 and 39: with no `go` on PATH it reports NOT GRADED HERE and passes, and
    `assets/test_io_guard_go.py` skips on the same machine. CI pins go 1.26
    beside python and node, so there it is graded by pin, not by accident.
    The go FILTER (checks 40-43) needs no toolchain and is always graded;
    check 41's `precise` arm expects whichever label the machine can honestly
    produce.
  * The literal-emission rule (SKILL.md's "one real injection surface") has NO
    EXECUTABLE COVERAGE, and for a real reason: no captured-output emitter
    ships in this skill, so there is no code path that could turn a hostile
    captured string into executable source, and nothing to run against one. It
    is graded as prose only -- a check that the rule is stated, not a
    demonstration that a (non-existent) emitter honours it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
RANKER = os.path.join(SKILL, "assets", "rank_risk.py")
GUARD = os.path.join(SKILL, "assets", "io_guard.py")


def _load(name, path):
    """Import a shipped asset by path. Stdlib only; nothing is installed."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = f" ({detail})" if detail else ""
    print(f"CHECK: {name} — {'PASS' if ok else 'FAIL'}{suffix}")


def write(root, rel, content):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True,
                           text=True, timeout=30)


def run_ranker(repo, *extra):
    return subprocess.run([sys.executable, RANKER, repo, *extra],
                           capture_output=True, text=True, timeout=60)


def load_json(result):
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError):
        return {}


# ---------------------------------------------------------------------------
# Artifact-check helpers
# ---------------------------------------------------------------------------

_NEG_RE = re.compile(r"\b(not|never|cannot|no|nothing)\b", re.I)

# A documented command, as opposed to prose mentioning one: zero or more
# NAME=value assignments, the invocation, and exactly one target.
_DOC_CMD_RE = re.compile(
    r'^(?:[A-Za-z_][A-Za-z_0-9]*=(?:"[^"]*"|\S*)\s+)*'
    r'pytest\s+-p\s+io_guard\s+\S+$')

# The node equivalent. Deliberately anchored on `--require ...io_guard.js` and
# on the `<path>` placeholder, because those are the two halves a document can
# get wrong on this stack: `--require` is what arms the guard BEFORE the test
# file loads, and a command with no target is prose, not an invocation.
_DOC_CMD_NODE_RE = re.compile(
    r'^(?:[A-Za-z_][A-Za-z_0-9]*=(?:"[^"]*"|\S*)\s+)*'
    r'node\s+--require\s+\S*io_guard\.js\S*\s+.*<path>$')

# The go equivalent: the wrapper, run by python3, ending in the `<package>`
# placeholder -- and it must carry the tier assignment, or it is a mention of
# the wrapper (the stacks.md table row) rather than an invocation of it.
_DOC_CMD_GO_RE = re.compile(
    r'^(?:[A-Za-z_][A-Za-z_0-9]*=(?:"[^"]*"|\S*)\s+)*TEST_SAFETY_NET_TIER=[12]\s+'
    r'(?:[A-Za-z_][A-Za-z_0-9]*=(?:"[^"]*"|\S*)\s+)*'
    r'python3\s+\S*io_guard_go\.py\S*\s+.*<package>$')


def slice_between(text, start_pat, end_pat):
    """Text from the first match of start_pat up to (excluding) the next
    match of end_pat AFTER it. Searches for end_pat starting at m1.end(),
    not m1.start() — a naive search from m1.start() lets end_pat match the
    start_pat's own heading line right back (e.g. start_pat=`^## Foo`,
    end_pat=`^## ` both match position 0 of the slice), collapsing the
    result to an empty string instead of the section's actual body."""
    m1 = re.search(start_pat, text, re.M)
    if not m1:
        return ""
    m2 = re.search(end_pat, text[m1.end():], re.M)
    return text[m1.start():m1.end() + m2.start()] if m2 else text[m1.start():]


def section_outside_fences(text, heading):
    """The lines from `heading` up to the next `## ` heading OUTSIDE a code fence.

    `slice_between` matches its end pattern anywhere, so a `## ` line inside a
    fenced example -- the Deliverable template opens with one -- ends the
    section at the example's first line.
    """
    out, inside, fence = [], False, False
    for line in text.splitlines():
        if not inside:
            if line.strip() == heading:
                inside = True
                out.append(line)
            continue
        if line.lstrip().startswith("```"):
            fence = not fence
        elif not fence and line.startswith("## "):
            break
        out.append(line)
    return "\n".join(out)


def all_mentions_negated(text, needle, window=220):
    """True iff every occurrence of `needle` sits within `window` chars of a
    negation word (not/never/cannot/no/nothing) somewhere before it — used to
    confirm a technique is only ever mentioned as something the skill does
    NOT do, never as a positive description of its own behaviour."""
    bad = []
    for m in re.finditer(re.escape(needle), text, re.I):
        start = m.start()
        ctx = text[max(0, start - window):start]
        if not _NEG_RE.search(ctx):
            bad.append(text[max(0, start - 40):start + 20].replace("\n", " "))
    return (len(bad) == 0, bad)


def main():
    skill_md = os.path.join(SKILL, "SKILL.md")
    triage_md = os.path.join(SKILL, "references", "triage.md")
    with open(skill_md, encoding="utf-8") as f:
        skill_text = f.read()
    with open(triage_md, encoding="utf-8") as f:
        triage_text = f.read()

    tmp = tempfile.mkdtemp(prefix="tsn-eval-")
    try:
        # =====================================================================
        # Fixture "main": one git repo carrying most of the ranker checks
        # =====================================================================
        main_repo = os.path.join(tmp, "main")
        os.makedirs(main_repo)
        has_git = git(main_repo, "init", "-q", ".").returncode == 0
        if has_git:
            git(main_repo, "config", "user.email", "eval@test.local")
            git(main_repo, "config", "user.name", "eval")

        # check 1: a pure function
        write(main_repo, "pure.py", "def pure_calc(a, b):\n    return a + b\n")

        # check 2: churny + widely referenced vs. quiet
        write(main_repo, "hot_churny.py", "def risky_hot(x):\n    return x * 2\n")
        write(main_repo, "quiet.py", "def quiet_calc(x):\n    return x\n")
        write(main_repo, "caller.py",
              "from hot_churny import risky_hot\n"
              "risky_hot(1)\n"
              "risky_hot(2)\n"
              "risky_hot(3)\n")

        # check 5 (THE HEADLINE): network opened in __init__
        write(main_repo, "netclass.py",
              "import socket\n\n\n"
              "class Client:\n"
              "    def __init__(self, host):\n"
              "        self.conn = socket.create_connection((host, 80))\n\n"
              "    def send(self, data):\n"
              "        self.conn.send(data)\n")

        # check 6: import-time I/O taints every unit in the module
        write(main_repo, "moduleio.py",
              "import requests\n\n"
              "_DATA = requests.get('http://x').json()\n\n\n"
              "def helper_a():\n"
              "    return _DATA\n\n\n"
              "def helper_b():\n"
              "    return 1\n")

        # check 9: aliased import must still resolve to the network marker
        write(main_repo, "aliased_net.py",
              "import socket as s\n\n\n"
              "def send_ping(host):\n"
              "    s.create_connection((host, 80))\n")

        # check 10: same-module indirection through a helper
        write(main_repo, "indirect.py",
              "import requests\n\n\n"
              "def _do_fetch(url):\n"
              "    return requests.get(url)\n\n\n"
              "def wrapper_fetch(url):\n"
              "    return _do_fetch(url)\n")

        # check 11: coverage collision — two `apply` units, one covered
        write(main_repo, "payments/refund.py", "def apply(amount):\n    return amount\n")
        write(main_repo, "discounts/coupon.py", "def apply(code):\n    return code\n")
        write(main_repo, "tests/test_coupon.py",
              "from discounts.coupon import apply\n\n\n"
              "def test_apply():\n"
              "    assert apply('X') == 'X'\n")

        # check 12: reference collision — two `helper`s, one referenced
        write(main_repo, "libs/alpha.py", "def helper(x):\n    return x + 1\n")
        write(main_repo, "libs/beta.py", "def helper(x):\n    return x - 1\n")
        write(main_repo, "user_of_alpha.py",
              "from libs.alpha import helper\n"
              "helper(1)\n"
              "helper(2)\n")

        # check 8: a file that does not parse
        write(main_repo, "broken.py", "def broken(:\n    pass\n")

        if has_git:
            git(main_repo, "add", "-A")
            git(main_repo, "commit", "-qm", "initial")
            for i in range(4):
                write(main_repo, "hot_churny.py",
                      f"def risky_hot(x):\n    return x * 2 + {i}\n")
                git(main_repo, "add", "-A")
                git(main_repo, "commit", "-qm", f"churn {i}")

        r1 = run_ranker(main_repo, "--since", "10 years ago", "--top-n", "50")
        plan = load_json(r1)
        ranked = plan.get("ranked", [])
        not_netted = plan.get("not_netted", [])
        ranked_ids = [row["id"] for row in ranked]
        ranked_by_id = {row["id"]: row for row in ranked}
        not_netted_by_id = {row["id"]: row for row in not_netted}
        covered_ids = set(plan.get("covered", []))

        setup_ok = r1.returncode == 0 and bool(plan)
        if not setup_ok:
            print(f"# setup warning: ranker exit {r1.returncode}, "
                  f"stderr={(r1.stderr or '')[-200:]}")

        # 1. a pure function is ranked, and its row carries inbound_approx: True
        check(
            "01 pure function is ranked with inbound_approx True",
            "pure.py::pure_calc" in ranked_ids
            and ranked_by_id.get("pure.py::pure_calc", {}).get("inbound_approx") is True,
        )

        # 2. a churny, well-referenced unit outranks a quiet one
        check(
            "02 churny well-referenced unit outranks a quiet one",
            "hot_churny.py::risky_hot" in ranked_ids
            and "quiet.py::quiet_calc" in ranked_ids
            and ranked_ids.index("hot_churny.py::risky_hot")
            < ranked_ids.index("quiet.py::quiet_calc"),
            str([r.get("id") for r in ranked][:5]),
        )

        # 3. repeated runs emit byte-identical JSON
        r2 = run_ranker(main_repo, "--since", "10 years ago", "--top-n", "50")
        check(
            "03 repeated runs emit byte-identical JSON",
            r1.returncode == 0 and r2.returncode == 0 and r1.stdout == r2.stdout,
        )

        # 4. an already-covered unit is excluded from ranked, appears in covered
        check(
            "04 already-covered unit excluded from ranked, present in covered",
            "discounts/coupon.py::apply" not in ranked_ids
            and "discounts/coupon.py::apply" in covered_ids,
        )

        # 5. THE HEADLINE: network in __init__ -> Tier 3, in not_netted, never ranked
        client_row = not_netted_by_id.get("netclass.py::Client")
        check(
            "05 HEADLINE: network-in-__init__ class is Tier 3, in not_netted, "
            "never in ranked",
            "netclass.py::Client" not in ranked_ids
            and client_row is not None and client_row.get("tier") == 3,
            str(client_row),
        )

        # 6. import-time I/O taints every unit in that module to Tier 4
        moduleio_tiers = {row["id"]: row["tier"] for row in not_netted
                           if row["path"] == "moduleio.py"}
        check(
            "06 import-time I/O puts every unit in the file at Tier 4",
            moduleio_tiers.get("moduleio.py::helper_a") == 4
            and moduleio_tiers.get("moduleio.py::helper_b") == 4,
            str(moduleio_tiers),
        )

        # 8. a syntactically broken file is skipped without taking the run down
        check(
            "08 syntactically broken file is skipped, run does not crash",
            setup_ok and not any(row["path"] == "broken.py"
                                  for row in ranked + not_netted),
            f"exit={r1.returncode}",
        )

        # 9. aliased import (`import socket as s`) is Tier 3, not Tier 1
        aliased_row = not_netted_by_id.get("aliased_net.py::send_ping")
        check(
            "09 aliased import resolves to the network marker: Tier 3, not Tier 1",
            "aliased_net.py::send_ping" not in ranked_ids
            and aliased_row is not None and aliased_row.get("tier") == 3,
            str(aliased_row),
        )

        # 10. same-module indirection (wrapper -> helper doing requests.get) is Tier 3
        wrapper_row = not_netted_by_id.get("indirect.py::wrapper_fetch")
        check(
            "10 same-module indirection through a helper is Tier 3",
            "indirect.py::wrapper_fetch" not in ranked_ids
            and wrapper_row is not None and wrapper_row.get("tier") == 3,
            str(wrapper_row),
        )

        # 11. coverage collision: the OTHER same-named unit is still ranked
        check(
            "11 coverage collision: the uncovered same-named unit is not hidden",
            "payments/refund.py::apply" in ranked_ids
            and "payments/refund.py::apply" not in covered_ids,
        )

        # 12. reference collision: the OTHER same-named unit's inbound_refs
        #     must not include calls aimed at its namesake
        beta_row = ranked_by_id.get("libs/beta.py::helper")
        alpha_row = ranked_by_id.get("libs/alpha.py::helper")
        check(
            "12 reference collision: an unreferenced same-named unit is not "
            "credited with the other's calls",
            beta_row is not None and beta_row.get("inbound_refs") == 0
            and alpha_row is not None and alpha_row.get("inbound_refs", 0) > 0,
            f"alpha={alpha_row and alpha_row.get('inbound_refs')} "
            f"beta={beta_row and beta_row.get('inbound_refs')}",
        )

        # =====================================================================
        # Fixture "nogit": no git history at all
        # =====================================================================
        nogit_repo = os.path.join(tmp, "nogit")
        write(nogit_repo, "solo.py", "def solo():\n    return 1\n")
        r_nogit = run_ranker(nogit_repo, "--top-n", "10")
        nogit_plan = load_json(r_nogit)
        nogit_row = next((row for row in nogit_plan.get("ranked", [])
                           if row["id"] == "solo.py::solo"), None)
        check(
            "07 no git history still produces a ranking; churn degrades to 0",
            r_nogit.returncode == 0 and nogit_row is not None
            and nogit_row.get("churn") == 0,
            str(nogit_row) if nogit_row else f"exit {r_nogit.returncode}",
        )

        # =====================================================================
        # Fixture "nopy": no Python files at all
        # =====================================================================
        nopy_repo = os.path.join(tmp, "nopy")
        write(nopy_repo, "README.md", "This repo has no python in it.\n")
        r_nopy = run_ranker(nopy_repo, "--top-n", "10")
        nopy_plan = load_json(r_nopy)
        check(
            "20 scope honesty: a repo with no Python files reports "
            "units_discovered: 0 with an empty ranked",
            r_nopy.returncode == 0
            and nopy_plan.get("units_discovered") == 0
            and nopy_plan.get("ranked") == [],
            str(nopy_plan),
        )

        # =====================================================================
        # Fixture "samebase": two same-basename files in different
        # directories, each defining a unit with the same name, neither
        # referenced by anything. Regression fixture for the round-3 caching
        # bug in inbound_refs (fixed in rank_risk.py: module_reffiles_cache
        # was memoised purely by module basename, so a/util.py and b/util.py
        # trivially satisfied _references_module against EACH OTHER via the
        # path-token half — every file named X.py "references" module X
        # against itself — and each ended up crediting the other's own
        # definition-line occurrence as an inbound call). This is not
        # hypothetical: it shipped and needed its own fix commit.
        # =====================================================================
        samebase_repo = os.path.join(tmp, "samebase")
        write(samebase_repo, "teamA/util.py", "def run_it(x):\n    return x\n")
        write(samebase_repo, "teamB/util.py", "def run_it(x):\n    return x\n")
        r_samebase = run_ranker(samebase_repo, "--top-n", "10")
        samebase_plan = load_json(r_samebase)
        samebase_by_id = {row["id"]: row for row in samebase_plan.get("ranked", [])}
        team_a = samebase_by_id.get("teamA/util.py::run_it")
        team_b = samebase_by_id.get("teamB/util.py::run_it")
        check(
            "21 same-basename files in different directories do not credit "
            "each other's inbound_refs",
            r_samebase.returncode == 0
            and team_a is not None and team_a.get("inbound_refs") == 0
            and team_b is not None and team_b.get("inbound_refs") == 0,
            f"teamA={team_a and team_a.get('inbound_refs')} "
            f"teamB={team_b and team_b.get('inbound_refs')}",
        )

        # =====================================================================
        # Artifact checks — the half an eval can honestly grade about a prompt
        # =====================================================================
        step4 = slice_between(skill_text, r"^4\. \*\*Write and prove", r"^5\. \*\*Report")

        # 14. RED-before-GREEN ordering in the proof step
        red_i, green_i = step4.find("RED"), step4.find("GREEN")
        check(
            "14 SKILL.md's proof step orders RED before GREEN",
            red_i != -1 and green_i != -1 and red_i < green_i,
            f"RED@{red_i} GREEN@{green_i}",
        )

        # 15. report template carries both a tier and a kind column. READ FROM
        #     THE DELIVERABLE SECTION, not from the first `| ... unit ... |`
        #     line anywhere in the file: when the go block landed in step 4,
        #     its exit table's "reclassify the unit to Tier 3" row became that
        #     first line, and the check graded the exit table instead of the
        #     report template. A check any later table can hijack is not
        #     grading what its name says. FENCE-AWARE, because the template it
        #     grades is itself a fenced block whose first line is
        #     `## Test safety net: ...` -- a plain `^## ` end pattern stopped
        #     there, one line in, and the check failed on a correct file.
        deliverable = section_outside_fences(skill_text, "## Deliverable")
        header_line = next((ln for ln in deliverable.splitlines()
                             if ln.strip().startswith("|") and "unit" in ln.lower()), "")
        check(
            "15 SKILL.md's report template carries both a tier and a kind column",
            "tier" in header_line.lower() and "kind" in header_line.lower(),
            header_line.strip(),
        )

        # 13. SKILL.md states the literal-emission rule as prose. Scoped to
        #     its own section (not the whole file) so this cannot be
        #     satisfied by the rule being merely quoted inside a code fence
        #     elsewhere, and so deleting the rule's prose — not any other
        #     mention of `repr(` — is specifically what flips this red.
        #     NOTE: this grades the rule's PROSE only; no captured-output
        #     emitter ships in this skill yet, so there is no executable
        #     coverage of the injection surface itself (see module docstring).
        literal_rule_section = slice_between(
            skill_text, r"^## The literal-emission rule", r"^## ")
        normalised_rule = re.sub(r"(?m)^\s*>\s?", "", literal_rule_section)
        normalised_rule = re.sub(r"\s+", " ", normalised_rule).lower()
        check(
            "13 SKILL.md states the literal-emission rule (repr(), never "
            "concatenation/interpolation) as prose",
            "repr(" in normalised_rule
            and "never" in normalised_rule
            and ("concatenation" in normalised_rule
                 or "interpolation" in normalised_rule),
            normalised_rule[:160],
        )

        # 16. SKILL.md states the proof's limit (normalised: the source wraps
        #     this sentence across markdown blockquote lines, each prefixed
        #     with its own "> ")
        normalised_skill = re.sub(r"(?m)^\s*>\s?", "", skill_text)
        normalised_skill = re.sub(r"\s+", " ", normalised_skill)
        check(
            "16 SKILL.md states the proof's limit "
            "(does not prove / every behavioural change)",
            "does not prove" in normalised_skill
            and "every behavioural change" in normalised_skill,
        )

        # 17. triage.md names all four tiers and the recorded-promotion
        #     discipline for database/HTTP
        # ANCHORED TO THE TABLE ROW, not to the bare string. The review's
        # "Considers" flagged that `r"Tier 4|4 — not reachable"` passed only
        # because the literal "Tier 4" happened to appear nowhere else in
        # triage.md -- one incidental rewording and the check stops biting.
        # Round 5 made exactly that rewording (the import-time floor paragraph
        # says "not Tier 4"), so the loose form is now unfalsifiable for tier 4
        # and this anchors all four to the row that has to exist.
        tier_markers = [
            re.search(r"(?m)^\|\s*\*\*1 — direct\b", triage_text),
            re.search(r"(?m)^\|\s*\*\*2 — wider boundary\b", triage_text),
            re.search(r"(?m)^\|\s*\*\*3 — needs a seam\b", triage_text),
            re.search(r"(?m)^\|\s*\*\*4 — not reachable\b", triage_text),
        ]
        check(
            "17 references/triage.md names all four tiers and the "
            "recorded-promotion discipline for database/HTTP",
            all(tier_markers)
            and "database" in triage_text.lower()
            and "http" in triage_text.lower()
            and "promot" in triage_text.lower()
            and "recording" in triage_text.lower(),
        )

        # 18. the three-outcome contract stated beside the proof step
        check(
            "18 SKILL.md states the three-outcome contract beside the proof step",
            "AssertionError" in step4
            and "guard's exception" in step4
            and "three outcomes" in step4
            and "Tier 3" in step4,
        )

        # 19. the guard is a pytest plugin loaded via -p, never described as a
        #     written conftest.py — checked TWO ways: prose (a doc rewording
        #     that starts describing the guard as a conftest.py) AND the
        #     filesystem (an actual conftest.py landing under this skill,
        #     which the prose scan alone would never see since it only reads
        #     SKILL.md and triage.md).
        has_plugin_desc = (
            "pytest plugin" in skill_text and "`-p`" in skill_text
            and "pytest plugin" in triage_text and "`-p`" in triage_text
        )
        # PER DOCUMENT, not over the concatenation. Concatenating let the FIRST
        # mention of `conftest.py` in triage.md inherit a negation from the tail
        # of SKILL.md, 220 characters away in a different file -- so a positive
        # "write a conftest.py into the repository root" line at the top of
        # triage.md left this check green. The prose half is the half aimed at a
        # doc rewording, so a placement that defeats it defeats the check.
        conftest_ok, conftest_bad = True, []
        for _label, _text in (("SKILL.md", skill_text),
                              ("references/triage.md", triage_text)):
            _ok, _bad = all_mentions_negated(_text, "conftest.py")
            conftest_ok = conftest_ok and _ok
            conftest_bad.extend("%s: %s" % (_label, b) for b in _bad)
        conftest_on_disk = []
        for base, dirs, files in os.walk(SKILL):
            dirs[:] = [d for d in dirs if d not in {".git", "__pycache__"}]
            if "conftest.py" in files:
                conftest_on_disk.append(
                    os.path.relpath(os.path.join(base, "conftest.py"), SKILL))
        detail19 = "; ".join(conftest_bad)
        if conftest_on_disk:
            detail19 = (detail19 + "; " if detail19 else "") \
                + "on disk: " + ", ".join(sorted(conftest_on_disk))
        check(
            "19 guard described as a pytest plugin via -p; never as a "
            "written conftest.py (prose AND filesystem)",
            has_plugin_desc and conftest_ok and not conftest_on_disk,
            detail19,
        )

        # =====================================================================
        # The runtime guard, exercised. Negative fixtures: the guard is ARMED
        # and then made to face REAL I/O. A guard that does not ship, or that
        # patches the ergonomic wrapper instead of the primitive underneath,
        # fails these.
        #
        # Every armed window below is tiny and wrapped in try/finally: this
        # eval writes fixture files of its own, and its frames are NOT library
        # frames, so anything it does while armed would (correctly) trip.
        # =====================================================================
        guard_present = os.path.isfile(GUARD)
        check("22 assets/io_guard.py ships (the sole enforcement of "
              "invariant 2 is a file, not a description)", guard_present)
        if guard_present:
            io_guard = _load("io_guard", GUARD)
            victim = os.path.join(tmp, "guard-must-block-this.txt")
            probe_dir = os.path.join(tmp, "guard_probe")
            os.makedirs(probe_dir, exist_ok=True)

            # 23 NEGATIVE: a tier 1 candidate that writes a file must raise the
            #    guard's OWN type, and the file must not exist afterwards. An
            #    exception raised after the write would be no guard at all.
            trip, wrote = None, None
            try:
                io_guard.arm(1)
                try:
                    with open(victim, "w", encoding="utf-8") as fh:
                        fh.write("escaped")
                    trip = "no exception"
                except io_guard.IOGuardViolation as exc:
                    trip = exc
                except BaseException as exc:            # noqa: BLE001
                    trip = "wrong type: %r" % (exc,)
            finally:
                io_guard.disarm()
            wrote = os.path.exists(victim)
            check("23 NEGATIVE: armed at tier 1, a real file write raises "
                  "IOGuardViolation and creates nothing",
                  isinstance(trip, io_guard.IOGuardViolation) and not wrote,
                  "trip=%s wrote=%s" % (trip if isinstance(trip, str)
                                        else trip.target, wrote))

            # 24 NEGATIVE: the directory-and-metadata family. This is the C3
            #    case: a guard patching only os.open/read/write sees neither
            #    os.listdir (getdents) nor os.stat (stat), so a tier 1 unit
            #    calling either would pass BOTH layers.
            #    Each case asserts the GROUP it trips on, not merely that
            #    something raised. Without that the `subprocess` arm could not
            #    fail: `subprocess.run` reaches `os.close` on its way, so
            #    deleting the whole subprocess patch set still produced an
            #    IOGuardViolation -- from the FILESYSTEM patch -- and the check
            #    named after the subprocess family was grading the filesystem
            #    one.
            missed = []
            try:
                io_guard.arm(1)
                for label, group, fn in (
                        ("os.listdir", "filesystem",
                         lambda: os.listdir(probe_dir)),
                        ("os.stat", "filesystem", lambda: os.stat(probe_dir)),
                        ("os.walk", "filesystem",
                         lambda: list(os.walk(probe_dir))),
                        ("os.rename", "filesystem",
                         lambda: os.rename(probe_dir, probe_dir)),
                        ("socket", "network",
                         lambda: __import__("socket").socket()),
                        ("subprocess", "subprocess",
                         lambda: subprocess.run([sys.executable, "-c", "pass"]))):
                    try:
                        fn()
                        missed.append(label)
                    except io_guard.IOGuardViolation as exc:
                        if exc.group != group:
                            missed.append("%s (blocked as %s, not %s)"
                                          % (label, exc.group, group))
                    except BaseException:               # noqa: BLE001
                        missed.append(label + " (wrong exception type)")
            finally:
                io_guard.disarm()
            check("24 NEGATIVE: the directory/metadata, network and subprocess "
                  "families each trip at tier 1, in their OWN group",
                  not missed, "missed: %s" % ", ".join(missed))

            # 25 the signalling contract: a guard trip must be distinguishable
            #    from the RED half of red->green, and must survive the
            #    `except Exception:` that wraps I/O in most legacy code.
            swallowed = None
            try:
                io_guard.arm(1)

                def legacy_unit():
                    try:
                        return open(victim, "w", encoding="utf-8")
                    except Exception:                   # noqa: BLE001
                        return "fallback"
                try:
                    legacy_unit()
                    swallowed = True
                except io_guard.IOGuardViolation:
                    swallowed = False
            finally:
                io_guard.disarm()
            check("25 the violation is not an AssertionError and is not "
                  "swallowed by `except Exception:`",
                  not issubclass(io_guard.IOGuardViolation, AssertionError)
                  and not issubclass(io_guard.IOGuardViolation, Exception)
                  and swallowed is False)

            # 26 tier awareness: a declared group is permitted, an undeclared
            #    one and the uncontrollable ones are not, and disarm restores.
            t2 = {"declared": None, "undeclared": None, "uncontrolled": None}
            # Names that ARE patched in THIS window: at tier 2 with
            # `filesystem` allowed the filesystem set is never installed, so
            # `os.listdir`/`open` would compare equal to themselves whatever
            # disarm did. `time.time` and `socket.socket` are blocked here.
            _time_mod = __import__("time")
            _socket_mod = __import__("socket")
            before_time, before_socket = _time_mod.time, _socket_mod.socket
            try:
                io_guard.arm(2, allow=("filesystem",))
                try:
                    os.listdir(probe_dir)
                    t2["declared"] = "permitted"
                except io_guard.IOGuardViolation:
                    t2["declared"] = "blocked"
                for key, fn in (("undeclared", lambda: __import__("time").time()),
                                ("uncontrolled",
                                 lambda: __import__("socket").socket())):
                    try:
                        fn()
                        t2[key] = "permitted"
                    except io_guard.IOGuardViolation:
                        t2[key] = "blocked"
            finally:
                io_guard.disarm()
            # Falsifiable, unlike the `os.path.isdir(probe_dir)` probe this
            # replaces: `_should_block` gates on `_state["armed"]`, which
            # `disarm()` clears on its FIRST line, so that probe was True in
            # every reachable state and a `disarm()` that restored nothing
            # still passed. Identity against the pre-arm objects, plus an empty
            # undo log, is the thing that actually changes.
            restored = (_time_mod.time is before_time
                        and _socket_mod.socket is before_socket
                        and not io_guard._undo
                        and not io_guard.armed())
            check("26 tier 2 permits only the groups the test declares, and "
                  "disarm puts the original objects back",
                  t2 == {"declared": "permitted", "undeclared": "blocked",
                         "uncontrolled": "blocked"} and restored,
                  "%s restored=%s undo=%d"
                  % (t2, restored, len(io_guard._undo)))

            # 27 filter <-> guard agreement. C3's real lesson: "what counts as
            #    filesystem I/O" must have ONE answer, or a name can go missing
            #    from both layers at once.
            ranker_mod = _load("rank_risk", RANKER)
            markers = set()
            for table in (ranker_mod.CONTROLLABLE, ranker_mod.UNCONTROLLABLE):
                for group_markers in table.values():
                    markers.update(group_markers)
            unaccounted = sorted(
                m for m in markers
                if io_guard._marker_intercept(m) is None
                and m not in io_guard.PARTIALLY_INTERCEPTED
                and m not in io_guard.NOT_INTERCEPTED)
            check("27 every filter marker has a guard-layer intercept or a "
                  "recorded reason", not unaccounted,
                  "unaccounted: %s" % ", ".join(unaccounted))

            # 30 NEGATIVE: `pkgutil.get_data` reads a real file through the
            #    LOADER (`SourceFileLoader.get_data` -> `_io.open_code`),
            #    naming neither `open` nor any `os` primitive. It was missed by
            #    BOTH layers at once -- no `pkgutil` marker in the filter, and
            #    the `io` re-exports patched but not the `_io` C module the
            #    frozen importer actually holds -- and returned 14020 real
            #    bytes at tier 1. The control below is what keeps the fix from
            #    being "block imports": a real import must still be exempt, or
            #    the guard declines every candidate.
            #    The SECOND negative arm is the one that matters: the same
            #    call from a MODULE BODY, i.e. with an import genuinely in
            #    progress. The exemption used to be scoped to the whole stack,
            #    so `_call_with_frames_removed` -- live for the whole of
            #    `exec_module` -- exempted every module body, and the read went
            #    through at tier 1 while the documented command reported
            #    `1 passed`. Drawn at the direct call alone, this check passed
            #    with that hole wide open.
            body_dir = os.path.join(tmp, "bodyio")
            write(body_dir, "tsn_eval_bodyio.py",
                  "import pkgutil\n"
                  "BLOB = pkgutil.get_data('json', '__init__.py')\n")
            sys.path.insert(0, body_dir)
            pkg_trip, body_trip, import_ok = None, None, None
            try:
                io_guard.arm(1)
                try:
                    __import__("pkgutil").get_data("json", "__init__.py")
                    pkg_trip = "no exception -- a real file was read"
                except io_guard.IOGuardViolation as exc:
                    pkg_trip = exc
                except BaseException as exc:            # noqa: BLE001
                    pkg_trip = "wrong type: %r" % (exc,)
                try:
                    __import__("tsn_eval_bodyio")
                    body_trip = ("no exception -- a module body read a real "
                                 "file while being imported")
                except io_guard.IOGuardViolation as exc:
                    body_trip = exc
                except BaseException as exc:            # noqa: BLE001
                    body_trip = "wrong type: %r" % (exc,)
                try:
                    __import__("importlib").import_module("wave")
                    import_ok = True
                except BaseException as exc:            # noqa: BLE001
                    import_ok = "import blocked: %r" % (exc,)
            finally:
                io_guard.disarm()
                sys.modules.pop("tsn_eval_bodyio", None)
                if body_dir in sys.path:
                    sys.path.remove(body_dir)
            check("30 NEGATIVE: pkgutil.get_data trips at tier 1 (the _io "
                  "layer) both directly and from a module body being "
                  "imported, while a real import stays exempt",
                  isinstance(pkg_trip, io_guard.IOGuardViolation)
                  and pkg_trip.group == "filesystem"
                  and isinstance(body_trip, io_guard.IOGuardViolation)
                  and body_trip.group == "filesystem"
                  and import_ok is True,
                  "trip=%s body=%s import=%s"
                  % (pkg_trip if isinstance(pkg_trip, str)
                     else pkg_trip.target,
                     body_trip if isinstance(body_trip, str)
                     else body_trip.target, import_ok))

            # 31 NEGATIVE: a violation raised on a worker thread. The thread
            #    bootstrap catches BaseException, so this used to become a
            #    warning beside a PASSED run -- the one outcome the guard must
            #    never have, because the shipped test then fails the moment the
            #    target repo runs its own suite without the guard.
            thread_trip, clean_thread = None, None
            try:
                io_guard.arm(1)
                worker = __import__("threading").Thread(
                    target=lambda: open(victim, "w", encoding="utf-8"))
                worker.start()
                try:
                    worker.join()
                    thread_trip = "swallowed -- the run would report PASSED"
                except io_guard.IOGuardViolation as exc:
                    thread_trip = exc
                box = {}
                calm = __import__("threading").Thread(
                    target=lambda: box.setdefault("n", 2))
                calm.start()
                calm.join()
                clean_thread = box.get("n")
            finally:
                io_guard.disarm()
            check("31 NEGATIVE: an off-main-thread violation reaches the main "
                  "thread, and a thread doing no I/O still does not trip",
                  isinstance(thread_trip, io_guard.IOGuardViolation)
                  and thread_trip.thread is not None
                  and clean_thread == 2,
                  "trip=%s clean=%s"
                  % (thread_trip if isinstance(thread_trip, str)
                     else thread_trip.target, clean_thread))

            # 32 NEGATIVE: a patched CLASS must stay a class. Replacing
            #    `socket.socket` with a plain function made `class
            #    SSLSocket(socket)` in ssl.py raise TypeError, so `import ssl`
            #    -- and asyncio, http.client, urllib.request, requests --
            #    failed at BOTH tiers on a correctly-tier-1 unit. That is a
            #    FOURTH proof outcome the three-outcome contract cannot read.
            class_shape = []
            try:
                io_guard.arm(1)
                for module_name, attr in (("socket", "socket"), ("io", "FileIO"),
                                          ("_io", "FileIO"), ("mmap", "mmap"),
                                          ("subprocess", "Popen")):
                    patched = getattr(__import__(module_name), attr, None)
                    if not isinstance(patched, type):
                        class_shape.append("%s.%s is %r"
                                           % (module_name, attr, type(patched)))
            finally:
                io_guard.disarm()
            ssl_run = subprocess.run(
                [sys.executable, "-c",
                 "import io_guard; io_guard.arm(1); import ssl, urllib.request; "
                 "print(ssl.PROTOCOL_TLS_CLIENT)"],
                env=dict(os.environ,
                         PYTHONPATH=os.path.join(SKILL, "assets")),
                capture_output=True, text=True, timeout=60)
            check("32 NEGATIVE: every class-valued patch target stays a class, "
                  "so `import ssl` still works while armed",
                  not class_shape and ssl_run.returncode == 0,
                  "; ".join(class_shape) or ssl_run.stderr.strip()[-160:])

            # 33 NEGATIVE: `os.environ.get(...)`. The MutableMapping methods
            #    bottom out in `__getitem__` and never call `os.getenv`, so
            #    patching the functions caught none of them -- while the FILTER
            #    does see `os.environ.get(...)`. The layers disagreed, in the
            #    direction where the filter tiers a unit 2 and the guard says
            #    nothing.
            env_missed = []
            try:
                io_guard.arm(1)
                alias = os.environ
                for label, fn in (("subscript", lambda: os.environ["PATH"]),
                                  ("get", lambda: os.environ.get("PATH")),
                                  ("aliased get", lambda: alias.get("PATH")),
                                  ("copy", lambda: os.environ.copy())):
                    try:
                        fn()
                        env_missed.append(label)
                    except io_guard.IOGuardViolation as exc:
                        if exc.group != "environment":
                            env_missed.append("%s (group %s)" % (label, exc.group))
                    except BaseException:               # noqa: BLE001
                        env_missed.append(label + " (wrong exception type)")
            finally:
                io_guard.disarm()
            check("33 NEGATIVE: every read form of os.environ trips at tier 1",
                  not env_missed, "missed: %s" % ", ".join(env_missed))

        # 34 THE DOCUMENTED COMMAND, run verbatim through the real pytest
        #    console script. This eval is stdlib-only and so cannot import
        #    pytest -- which is exactly how the plugin path escaped grading
        #    while the guard was unusable under every command the documents
        #    print. A SUBPROCESS invocation needs no import here, so the path
        #    is graded whenever pytest is installed and reported as ungraded
        #    when it is not. `assets/test_io_guard.py` runs the same commands.
        #
        #    TWO arms, because the positive one alone cannot fail for the
        #    reason that matters: replacing the body of `pytest_configure`
        #    with `pass` -- a plugin that loads and arms NOTHING -- still made
        #    every documented command exit 0 and print `1 passed`, so this
        #    check could not tell a working guard from an inert one. The
        #    NEGATIVE arm runs the same extracted commands over a unit that
        #    really does I/O and requires them to fail with the guard's own
        #    type. Its violation is `network` (constructing a socket, no
        #    connection attempted, offline-safe) because that group is
        #    UNCONTROLLABLE: it is blocked at tier 1 and at tier 2 alike, so
        #    one fixture holds for every documented command including the
        #    `TEST_SAFETY_NET_ALLOW=filesystem,clock` ones.
        pytest_present = subprocess.run(
            [sys.executable, "-c", "import pytest"],
            capture_output=True, text=True).returncode == 0
        doc_fail = []
        if pytest_present:
            proof = os.path.join(tmp, "proof")
            os.makedirs(proof, exist_ok=True)
            write(proof, "pure.py", "def add(a, b):\n    return a + b\n")
            write(proof, "test_pure.py",
                  "import pure\n\n\ndef test_add():\n"
                  "    print('captured')\n    assert pure.add(2, 3) == 5\n")
            write(proof, "leaky.py",
                  "import socket\n\n\ndef resolve():\n"
                  "    return socket.socket() is not None\n")
            write(proof, "test_leaky.py",
                  "import leaky\n\n\ndef test_resolve():\n"
                  "    assert leaky.resolve()\n")
            commands = []
            for label, text in (("SKILL.md", skill_text),
                                ("references/triage.md", triage_text),
                                ("references/stacks.md",
                                 open(os.path.join(SKILL, "references",
                                                   "stacks.md"),
                                      encoding="utf-8").read())):
                for block in re.findall(r"```sh\n(.*?)```", text, re.S):
                    joined = re.sub(r"\\\n\s*", " ", block)
                    for line in joined.splitlines():
                        line = line.strip()
                        if _DOC_CMD_RE.match(line):
                            commands.append((label, line))
            if not commands:
                doc_fail.append("no documented invocation found to run")
            for label, command in commands:
                env = dict(os.environ, SKILL_DIR=SKILL)
                env.pop("PYTHONPATH", None)
                env.pop("TEST_SAFETY_NET_TIER", None)
                env.pop("TEST_SAFETY_NET_ALLOW", None)
                for target, expect in (("test_pure.py::test_add", "pass"),
                                       ("test_leaky.py::test_resolve",
                                        "trip")):
                    cmd = command.replace("<path>::<test_name>", target)
                    run = subprocess.run(
                        ["/bin/sh", "-c", cmd + " -q -p no:cacheprovider"],
                        cwd=proof, env=env, capture_output=True, text=True,
                        timeout=120)
                    output = run.stdout + run.stderr
                    if expect == "pass":
                        ok = run.returncode == 0 and "1 passed" in run.stdout
                    else:
                        ok = (run.returncode != 0
                              and "IOGuardViolation" in output
                              and "1 passed" not in run.stdout)
                    if not ok:
                        doc_fail.append("%s [%s]: %s -> %s"
                                        % (label, expect, cmd,
                                           output.strip()[-200:]))
        check("34 the DOCUMENTED `pytest -p io_guard` command, extracted from "
              "each document and run verbatim, passes a clean unit AND fails "
              "a unit that really does I/O (so an inert plugin cannot pass)",
              not doc_fail,
              "; ".join(doc_fail) if doc_fail else
              ("%d command(s), each run twice" % len(commands) if pytest_present
               else "NOT GRADED HERE: pytest is not installed in this "
                    "environment; assets/test_io_guard.py grades it where it is"))

        # 28 SKILL.md invokes the guard by $SKILL_DIR path, not a
        #    skill-relative one (agents run from the TARGET repo) and names
        #    both environment variables the guard reads.
        check("28 SKILL.md wires the guard by $SKILL_DIR path and names its "
              "tier environment variable",
              "$SKILL_DIR/assets" in skill_text
              and "io_guard" in skill_text
              and "TEST_SAFETY_NET_TIER" in skill_text)

        # 29 CROSS-DOCUMENT: SKILL.md and triage.md must agree on what a guard
        #    trip does. They did not -- the spec and SKILL.md said "Tier 3,
        #    discard, regardless of red or green" while triage.md licensed
        #    "drop it at least to Tier 2 or 3, per what tripped", turning the
        #    one unconditional decline in this design into a retry loop. No
        #    single-document check could see that, which is why this one reads
        #    BOTH: every sentence that reclassifies after a trip must name
        #    Tier 3, and none may offer a lower tier as an alternative.
        reclass_bad = []
        for label, text in (("SKILL.md", skill_text),
                            ("references/triage.md", triage_text)):
            flat = re.sub(r"\s+", " ", re.sub(r"(?m)^\s*>\s?", "", text))
            sentences = [s_ for s_ in re.split(r"(?<=[.;])\s+", flat)
                         if "reclassif" in s_.lower()]
            if not sentences:
                reclass_bad.append("%s: says nothing about reclassifying" % label)
            for sentence in sentences:
                if "Tier 3" not in sentence:
                    reclass_bad.append("%s: %r does not name Tier 3"
                                       % (label, sentence[:70]))
                if re.search(r"Tier 2 or 3|at least to \*?\*?Tier 2", sentence):
                    reclass_bad.append("%s: %r offers a tier below 3"
                                       % (label, sentence[:70]))
        check("29 CROSS-DOC: SKILL.md and triage.md agree a guard trip means "
              "Tier 3 and discard, with no lower alternative",
              not reclass_bad, "; ".join(reclass_bad))

        # =====================================================================
        # Fixture "nodefx": the node stack, end to end, through the SAME CLI an
        # agent runs — not through an imported function. Checks 35-38 grade the
        # filter half (discovery, the reader it names, vendor exclusion, tiers)
        # and check 39 grades the enforcement half under the command the
        # documents actually print.
        #
        # Every one of these is vacuous before the node stack exists: a node
        # repo read as Python discovers nothing and reports a clean result,
        # which is the silent zero this stack was added to close.
        # =====================================================================
        node_repo = os.path.join(tmp, "nodefx")
        write(node_repo, "package.json",
              '{"name": "demo", "main": "src/index.js",\n'
              ' "dependencies": {"left-pad": "^1.0.0"}}\n')
        # Tier 1: nothing reachable.
        write(node_repo, "src/pure.js",
              "export function addNumbers(a, b) { return a + b; }\n")
        # Tier 2: a CONTROLLABLE group (filesystem) inside the unit.
        write(node_repo, "src/cache.js",
              'import fs from "node:fs";\n'
              "export function readCache(p) { return fs.readFileSync(p, "
              '"utf8"); }\n')
        # Tier 3: an UNCONTROLLABLE group (network) inside the unit.
        write(node_repo, "src/remote.js",
              'import net from "node:net";\n'
              "export function connectRemote(h) { return new net.Socket(); }\n")
        # Tier 3: I/O in the MODULE BODY — a fixture runs too late to control it.
        write(node_repo, "src/boot.js",
              'import fs from "node:fs";\n'
              'const CONFIG = fs.readFileSync("/etc/hosts", "utf8");\n'
              "export function config() { return CONFIG; }\n")
        # A vendored dependency. Its export is a perfectly good unit by every
        # syntactic test — which is why excluding it has to be asserted rather
        # than assumed.
        write(node_repo, "node_modules/vendored/index.js",
              "export function vendoredUnit() { return 1; }\n")

        r_node = run_ranker(node_repo, "--top-n", "20")
        node_plan = load_json(r_node)
        node_rows = {}
        for bucket in ("ranked", "remainder", "not_netted"):
            for r_ in node_plan.get(bucket, []):
                node_rows[r_["id"]] = r_

        # 35. the node stack is detected and its exported units are found
        check("35 a node repo is detected as node and its exported units are "
              "discovered (0 units is what a repo read as the wrong stack "
              "reports)",
              r_node.returncode == 0
              and node_plan.get("stack") == "node"
              and node_plan.get("units_discovered", 0) >= 4
              and "src/pure.js::addNumbers" in node_rows,
              "stack=%s units=%s ids=%s"
              % (node_plan.get("stack"), node_plan.get("units_discovered"),
                 sorted(node_rows)))

        # 36. the report names WHICH READER produced the units (design
        #     decision D1), and names it CORRECTLY.
        #
        #     THIS CHECK USED TO BE A TAUTOLOGY. It asserted
        #     `discovery in ("precise", "heuristic")` — and `discover_units`
        #     can only ever return one of those two strings, so the code half
        #     could not fail. Mutating the heuristic reader to LIE about which
        #     path ran (`return _units_heuristic(root), "precise"`) left the
        #     eval at 39/39. The property D1 exists for is not "the key holds
        #     an in-vocabulary value", it is "two runs are comparable when this
        #     key agrees", and only a check that can see a WRONG label defends
        #     it.
        #
        #     Both arms are deterministic without installing anything:
        #       a. the fixture ships no `node_modules/typescript`, so the only
        #          honest answer is `heuristic`;
        #       b. a SECOND fixture ships a `typescript` that loads and then
        #          reads nothing — an aliased or shimmed compiler. That used to
        #          yield `discovery: "precise"` with ZERO units while the
        #          heuristic would have found three: the silent zero, wearing
        #          the label the report tells an agent to trust more. It must
        #          decline, say so on stderr, and report the units anyway.
        node_stacks_md = open(os.path.join(SKILL, "references", "stacks.md"),
                              encoding="utf-8").read()
        shim_repo = os.path.join(tmp, "nodeshim")
        write(shim_repo, "package.json",
              '{"name": "shim", "main": "src/a.js",\n'
              ' "dependencies": {"left-pad": "^1.0.0"}}\n')
        for i in range(3):
            write(shim_repo, "src/m%d.js" % i,
                  "export function f%d(x) { return x; }\n" % i)
        # Loads, exports the right NAMES, and throws on every file it is asked
        # to parse. The walker counts each into `unreadable`.
        write(shim_repo, "node_modules/typescript/lib/typescript.js",
              "module.exports = {\n"
              "  createSourceFile: function () { throw new Error('shimmed'); },\n"
              "  ScriptTarget: { Latest: 99 },\n"
              "  SyntaxKind: {},\n"
              "};\n")
        r_shim = run_ranker(shim_repo, "--top-n", "20")
        shim_plan = load_json(r_shim)
        check("36 the node report names which reader found its units, and "
              "names it CORRECTLY: no toolchain reads `heuristic`, and a "
              "toolchain that loads but reads nothing DECLINES rather than "
              "reporting zero units as `precise`",
              node_plan.get("discovery") == "heuristic"
              and shim_plan.get("discovery") == "heuristic"
              and shim_plan.get("units_discovered") == 3
              and "declined" in (r_shim.stderr or "")
              and "discovery" in skill_text
              and "heuristic" in node_stacks_md,
              "fixture=%r shim=%r shim_units=%r shim_note=%r"
              % (node_plan.get("discovery"), shim_plan.get("discovery"),
                 shim_plan.get("units_discovered"),
                 (r_shim.stderr or "").strip().splitlines()[-1:]))

        # 37. NEGATIVE-SHAPED: a vendored unit is a syntactically valid export
        #     that must NEVER be ranked. Ranking a repo's dependencies as its
        #     own code buries every real unit under `node_modules`.
        vendored = [i for i in node_rows
                    if i.startswith("node_modules/")
                    or "vendoredUnit" in i]
        check("37 NEGATIVE: an exported unit inside node_modules is never "
              "discovered, ranked or netted",
              not vendored and node_plan.get("units_discovered") == 4,
              "leaked=%s units=%s" % (vendored, node_plan.get("units_discovered")))

        # 38. each unit lands in the tier its markers earn — all four
        #     directions at once, because a triage that answers one tier for
        #     everything satisfies any single-tier assertion.
        want_tiers = {
            "src/pure.js::addNumbers": 1,       # nothing reachable
            "src/cache.js::readCache": 2,       # controllable group in the unit
            "src/remote.js::connectRemote": 3,  # uncontrollable group
            "src/boot.js::config": 3,           # I/O in the module body
        }
        got_tiers = {i: node_rows.get(i, {}).get("tier") for i in want_tiers}
        not_netted_node = {r_["id"] for r_ in node_plan.get("not_netted", [])}
        check("38 each node unit lands in the tier its markers earn, and the "
              "Tier 3 ones are in not_netted rather than ranked",
              got_tiers == want_tiers
              and "src/remote.js::connectRemote" in not_netted_node
              and "src/boot.js::config" in not_netted_node,
              str(got_tiers))

        # 39. THE ENFORCEMENT HALF, under the command the documents print.
        #     Two arms, for the reason check 34 records: the positive arm alone
        #     cannot fail for the reason that matters, because a guard that
        #     loads and arms NOTHING also lets a clean test pass. The negative
        #     arm's violation is `network` — constructing a socket, nothing
        #     dialled, offline-safe — because that group is UNCONTROLLABLE and
        #     so is blocked at tier 1 and tier 2 alike, which lets one fixture
        #     hold for every documented command including the
        #     TEST_SAFETY_NET_ALLOW=filesystem,clock ones.
        node_present = shutil.which("node") is not None
        node_fail = []
        node_commands = []
        if node_present:
            nproof = os.path.join(tmp, "nproof")
            os.makedirs(nproof, exist_ok=True)
            write(nproof, "pure.js",
                  "function add(a, b) { return a + b; }\n"
                  "module.exports = { add };\n")
            write(nproof, "test_pure.js",
                  'const { test } = require("node:test");\n'
                  'const assert = require("node:assert");\n'
                  'const { add } = require("./pure.js");\n'
                  'test("adds", () => { assert.strictEqual(add(2, 3), 5); });\n')
            write(nproof, "leaky.js",
                  'const net = require("node:net");\n'
                  "function client() { return new net.Socket() !== null; }\n"
                  "module.exports = { client };\n")
            write(nproof, "test_leaky.js",
                  'const { test } = require("node:test");\n'
                  'const assert = require("node:assert");\n'
                  'const { client } = require("./leaky.js");\n'
                  'test("adds", () => { assert.ok(client()); });\n')
            for label, text in (("SKILL.md", skill_text),
                                ("references/stacks.md", node_stacks_md)):
                for block in re.findall(r"```sh\n(.*?)```", text, re.S):
                    joined = re.sub(r"\\\n\s*", " ", block)
                    for line in joined.splitlines():
                        line = line.strip()
                        if _DOC_CMD_NODE_RE.match(line):
                            node_commands.append((label, line))
            if not node_commands:
                node_fail.append("no documented node invocation found to run")
            # SKILL.md SPECIFICALLY. `references/stacks.md` also prints the
            # command, so without this the check stays green after the block is
            # deleted from the one document an agent is guaranteed to read —
            # the same "graded something adjacent to what the docs prescribe"
            # shape every serious defect on this skill has had.
            elif not any(lbl == "SKILL.md" for lbl, _ in node_commands):
                node_fail.append("SKILL.md prints no node guard invocation; "
                                 "only %s does"
                                 % sorted({lbl for lbl, _ in node_commands}))
            for label, command in node_commands:
                env = dict(os.environ, SKILL_DIR=SKILL)
                env.pop("TEST_SAFETY_NET_TIER", None)
                env.pop("TEST_SAFETY_NET_ALLOW", None)
                for target, expect in (("test_pure.js", "pass"),
                                       ("test_leaky.js", "trip")):
                    cmd = command.replace("<test_name>", "adds")
                    cmd = cmd.replace("<path>", target)
                    run = subprocess.run(["/bin/sh", "-c", cmd], cwd=nproof,
                                         env=env, capture_output=True,
                                         text=True, timeout=120)
                    output = run.stdout + run.stderr
                    tripped = "IOGuardViolation" in output
                    if expect == "pass":
                        ok_ = run.returncode == 0 and not tripped
                    else:
                        ok_ = run.returncode != 0 and tripped
                    if not ok_:
                        node_fail.append("%s [%s]: %s -> exit %s %s"
                                         % (label, expect, cmd, run.returncode,
                                            output.strip()[-200:]))
        check("39 NEGATIVE: the DOCUMENTED `node --require ... io_guard.js` "
              "command, extracted from each document and run verbatim, passes "
              "a clean unit AND fails a unit that really does I/O (so a guard "
              "that never arms cannot pass)",
              not node_fail,
              "; ".join(node_fail) if node_fail else
              ("%d command(s), each run twice" % len(node_commands)
               if node_present else
               "NOT GRADED HERE: no `node` on PATH; "
               "assets/test_io_guard_node.sh grades it where there is"))

        # =====================================================================
        # Fixture "gofx": the Go stack, end to end, through the SAME CLI an
        # agent runs. Checks 40-43 grade the filter half and need no
        # toolchain; check 44 grades the enforcement half under the command
        # SKILL.md prints.
        #
        # Every one of these is vacuous before the Go stack exists: a Go repo
        # was claimed by no stack, fell through to the Python fallback, and
        # reported a clean EMPTY plan -- the silent zero the stack closes.
        # =====================================================================
        go_repo = os.path.join(tmp, "gofx")
        write(go_repo, "go.mod", "module example.com/gofx\n\ngo 1.22\n")
        write(go_repo, "calc/c.go",
              'package calc\n\nimport (\n\t"math/rand"\n\t"net"\n\t"os"\n\t"syscall"\n)\n\n'
              "type Report struct{}\n\n"
              "func Pure(n int) int { return n * 2 }\n"
              "func Load(p string) ([]byte, error) { return os.ReadFile(p) }\n"
              'func Dial() error { _, err := net.Dial("tcp", "x:1"); return err }\n'
              "func Roll() int { return rand.Intn(6) }\n"
              "func Raw() { syscall.Syscall(20, 0, 0, 0) }\n"
              "func (r Report) Total() int { return 0 }\n")
        # Initialisation I/O floors EVERY unit in its package -- a package is a
        # directory, and every file's initializers run on import.
        write(go_repo, "boot/b.go",
              'package boot\n\nimport "os"\n\nvar mode = os.Getenv("MODE")\n\n'
              "func Double(n int) int { return n * 2 }\n")
        write(go_repo, "conn/c.go",
              'package conn\n\nimport "net"\n\nvar conn, _ = net.Dial("tcp", "db:5432")\n\n'
              "func Twice(n int) int { return n * 2 }\n")
        # Exported units the go tool itself never builds into a package. Each
        # is a perfectly good unit by every syntactic test -- which is why
        # excluding them has to be asserted rather than assumed.
        for rel in ("vendor/v/v.go", "testdata/t.go", "_scratch/s.go"):
            write(go_repo, rel, "package x\n\nfunc Leaked() {}\n")
        write(go_repo, "calc/c_test.go",
              'package calc\n\nimport "testing"\n\nfunc TestHelper(t *testing.T) {}\n'
              "func Fixture() int { return 1 }\n")
        write(go_repo, "calc/gen.go",
              "// Code generated by stringer; DO NOT EDIT.\n\npackage calc\n\n"
              "func Generated() {}\n")

        r_go = run_ranker(go_repo, "--top-n", "50")
        go_plan = load_json(r_go)
        go_rows = {}
        for bucket in ("ranked", "remainder", "not_netted"):
            for r_ in go_plan.get(bucket, []):
                go_rows[r_["id"]] = r_
        go_ids = set(go_rows) | set(go_plan.get("covered", []))

        # 40. detected as go, and BOTH unit shapes found: functions and methods.
        check("40 a Go module is detected as go and its exported functions AND "
              "methods are discovered (0 units is what a repo no stack claims "
              "reports)",
              r_go.returncode == 0
              and go_plan.get("stack") == "go"
              and go_plan.get("units_discovered") == 8
              and "calc/c.go::Pure" in go_rows
              and "calc/c.go::Report.Total" in go_rows,
              "stack=%s units=%s ids=%s" % (go_plan.get("stack"),
                                            go_plan.get("units_discovered"), sorted(go_rows)))

        # 41. the reader is NAMED, and named correctly, three ways: the default
        #     run says `precise` exactly where `go` exists; a `go` that FAILS
        #     declines to `heuristic` with a note and loses no units; and
        #     --no-precise never asks it at all.
        fake_bin = os.path.join(tmp, "fakebin")
        os.makedirs(fake_bin, exist_ok=True)
        os.chmod(write(fake_bin, "go", "#!/bin/sh\nexit 1\n"), 0o755)
        r_fake = subprocess.run(
            [sys.executable, RANKER, go_repo, "--top-n", "50"], capture_output=True,
            text=True, timeout=120,
            env=dict(os.environ, PATH=fake_bin + os.pathsep + os.environ.get("PATH", "")))
        fake_plan = load_json(r_fake)
        np_plan = load_json(run_ranker(go_repo, "--no-precise", "--top-n", "50"))
        want_default = "precise" if shutil.which("go") else "heuristic"
        check("41 the go report names which reader found its units, CORRECTLY: "
              "`precise` exactly where go runs, `heuristic` with a note when go "
              "fails, and `heuristic` under --no-precise",
              go_plan.get("discovery") == want_default
              and fake_plan.get("discovery") == "heuristic"
              and fake_plan.get("units_discovered") == 8
              and "declined" in (r_fake.stderr or "")
              and np_plan.get("discovery") == "heuristic",
              "default=%r (want %r) failing-go=%r units=%r note=%r no-precise=%r"
              % (go_plan.get("discovery"), want_default, fake_plan.get("discovery"),
                 fake_plan.get("units_discovered"),
                 (r_fake.stderr or "").strip().splitlines()[-1:], np_plan.get("discovery")))

        # 42. NEGATIVE: what the go tool never builds is never a unit.
        go_leaked = sorted(i for i in go_ids
                           if i.startswith(("vendor/", "testdata/", "_scratch/"))
                           or "_test.go" in i or "gen.go" in i)
        check("42 NEGATIVE: exported units under vendor/, testdata/, a _-prefixed "
              "directory, a _test.go file and a generated file are never "
              "discovered, ranked or netted",
              not go_leaked and go_plan.get("units_discovered") == 8,
              "leaked=%s units=%s" % (go_leaked, go_plan.get("units_discovered")))

        # 43. every tier direction at once, including the two PACKAGE floors.
        want_go_tiers = {
            "calc/c.go::Pure": 1,          # nothing reachable
            "calc/c.go::Load": 2,          # filesystem, controllable
            "calc/c.go::Dial": 3,          # network, uncontrollable
            "calc/c.go::Roll": 3,          # the global rand source: uncontrollable on go
            "calc/c.go::Raw": 4,           # a raw syscall: declined statically
            "boot/b.go::Double": 3,        # an initializer reads the environment
            "conn/c.go::Twice": 4,         # an initializer dials the network
        }
        got_go_tiers = {i: go_rows.get(i, {}).get("tier") for i in want_go_tiers}
        go_not_netted = {r_["id"] for r_ in go_plan.get("not_netted", [])}
        check("43 each go unit lands in the tier its markers earn -- randomness is "
              "uncontrollable, a raw syscall is declined, initialisation I/O floors "
              "its whole package -- and tiers 3-4 are not_netted",
              got_go_tiers == want_go_tiers
              and all(i in go_not_netted for i, t in want_go_tiers.items() if t >= 3),
              str(got_go_tiers))

        # 44. THE ENFORCEMENT HALF, under the command SKILL.md prints. Two arms,
        #     for the reason 34 and 39 record: a guard that arms nothing passes
        #     a clean test too. The negative arm calls `syscall.Socket` itself --
        #     the hook trips before a socket exists, so nothing is dialled -- and
        #     `network` is blocked at both tiers, so one fixture serves both
        #     documented commands.
        go_present = shutil.which("go") is not None
        go_fail, go_commands = [], []
        if go_present:
            gproof = os.path.join(tmp, "gproof")
            write(gproof, "go.mod", "module example.com/gproof\n\ngo 1.22\n")
            write(gproof, "fx.go",
                  'package fx\n\nimport "syscall"\n\n'
                  "func Add(a, b int) int { return a + b }\n\n"
                  "func Sock() bool {\n"
                  "\tfd, err := syscall.Socket(syscall.AF_INET, syscall.SOCK_STREAM, 0)\n"
                  "\tif err == nil {\n\t\tsyscall.Close(fd)\n\t}\n\treturn err == nil\n}\n")
            write(gproof, "fx_test.go",
                  'package fx\n\nimport "testing"\n\n'
                  "func TestAdd(t *testing.T) { if Add(2, 3) != 5 { t.Fatal(\"bad\") } }\n"
                  "func TestSock(t *testing.T) { Sock() }\n")
            # EVERY printed go guard line must parse as an invocation, not just
            # one. Matching lines and ignoring the rest let a mangled block
            # hide: one copy with a broken `<package>` placeholder was simply
            # never extracted, the other copy still matched, and the check
            # stayed green over a command an agent would copy and get wrong.
            # Measured, by mangling exactly one of SKILL.md's two blocks.
            for label, text in (("SKILL.md", skill_text),
                                ("references/stacks.md", node_stacks_md)):
                for block in re.findall(r"```sh\n(.*?)```", text, re.S):
                    joined = re.sub(r"\\\n\s*", " ", block)
                    for line in joined.splitlines():
                        line = line.strip()
                        if _DOC_CMD_GO_RE.match(line):
                            go_commands.append((label, line))
                        elif "io_guard_go.py" in line and "TEST_SAFETY_NET_TIER" in line:
                            go_fail.append("%s prints a go guard line that is not a "
                                           "runnable invocation: %s" % (label, line))
            if not any(lbl == "SKILL.md" for lbl, _ in go_commands):
                go_fail.append("SKILL.md prints no go guard invocation")
            for label, command in go_commands:
                env = {k_: v_ for k_, v_ in os.environ.items()
                       if not k_.startswith("TEST_SAFETY_NET")}
                env.pop("GOFLAGS", None)
                env["SKILL_DIR"] = SKILL
                for test, want in (("TestAdd", 0), ("TestSock", 3)):
                    cmd = command.replace("<test_name>", test).replace("<package>", "./")
                    run = subprocess.run(["/bin/sh", "-c", cmd], cwd=gproof, env=env,
                                         capture_output=True, text=True, timeout=300,
                                         stdin=subprocess.DEVNULL)
                    output = run.stdout + run.stderr
                    ok_ = run.returncode == want and (("IOGuardViolation" in output) == (want == 3))
                    if not ok_:
                        go_fail.append("%s [%s]: %s -> exit %s %s"
                                       % (label, test, cmd, run.returncode,
                                          output.strip()[-200:]))
        check("44 NEGATIVE: the DOCUMENTED go guard command, extracted from SKILL.md "
              "and run verbatim, passes a clean unit AND exits 3 on one that "
              "reaches a real syscall (so a guard that never arms cannot pass)",
              not go_fail,
              "; ".join(go_fail) if go_fail else
              ("%d command(s), each run twice" % len(go_commands) if go_present else
               "NOT GRADED HERE: no `go` on PATH; "
               "assets/test_io_guard_go.py grades it where there is"))

        n, k = len(_checks), sum(_checks)
        ok = k == n
        print(f"EVAL_RESULT: {'PASS' if ok else 'FAIL'} ({k}/{n} checks)")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
