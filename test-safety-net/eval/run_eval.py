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
  * The pytest PLUGIN path (`-p io_guard`, arming ahead of collection). This
    eval must run with the stdlib alone, and pytest is not guaranteed present,
    so the plugin hooks are exercised in `assets/test_io_guard.py` (skipped
    there when pytest is absent) and the guard's core is exercised here.
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

        # 15. report template carries both a tier and a kind column
        header_line = next((ln for ln in skill_text.splitlines()
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
        tier_markers = [
            re.search(r"Tier 1|1 — direct", triage_text),
            re.search(r"Tier 2|2 — wider boundary", triage_text),
            re.search(r"Tier 3|3 — needs a seam", triage_text),
            re.search(r"Tier 4|4 — not reachable", triage_text),
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
        combined = skill_text + "\n" + triage_text
        has_plugin_desc = (
            "pytest plugin" in skill_text and "`-p`" in skill_text
            and "pytest plugin" in triage_text and "`-p`" in triage_text
        )
        conftest_ok, conftest_bad = all_mentions_negated(combined, "conftest.py")
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
            missed = []
            try:
                io_guard.arm(1)
                for label, fn in (("os.listdir", lambda: os.listdir(probe_dir)),
                                  ("os.stat", lambda: os.stat(probe_dir)),
                                  ("os.walk", lambda: list(os.walk(probe_dir))),
                                  ("os.rename", lambda: os.rename(probe_dir,
                                                                  probe_dir)),
                                  ("socket", lambda: __import__("socket").socket()),
                                  ("subprocess", lambda: subprocess.run(
                                      [sys.executable, "-c", "pass"]))):
                    try:
                        fn()
                        missed.append(label)
                    except io_guard.IOGuardViolation:
                        pass
                    except BaseException:               # noqa: BLE001
                        missed.append(label + " (wrong exception type)")
            finally:
                io_guard.disarm()
            check("24 NEGATIVE: the directory/metadata, network and subprocess "
                  "families all trip at tier 1",
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
            restored = os.path.isdir(probe_dir)          # works again once down
            check("26 tier 2 permits only the groups the test declares, and "
                  "disarm restores the primitives",
                  t2 == {"declared": "permitted", "undeclared": "blocked",
                         "uncontrolled": "blocked"} and restored,
                  str(t2))

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

        n, k = len(_checks), sum(_checks)
        ok = k == n
        print(f"EVAL_RESULT: {'PASS' if ok else 'FAIL'} ({k}/{n} checks)")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
