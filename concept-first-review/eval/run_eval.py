#!/usr/bin/env python3
"""Gate-runnable outcome eval for concept-first-review (docs/eval-standard.md).

End-to-end over the shipped CLI, driven exactly as an agent or a loop would:

    open -> draft -> commit -> signals -> template -> grade -> state -> audit

graded model-free against fixtures whose answers are known in advance
(eval/harness.py). Both directions are asserted.

The promise holds:
  noise collapses while the decisive rows survive; the relocation is detected
  and forced to present as a move; the seeded N+1, layering breach, unlisted
  dependency and untested behaviour are all surfaced; a complete review grades
  PASS; the loop boundary reports a finished run as done; and a thorough prior
  review audits clean.

The promise bites:
  invented replacement text, one-sided relocation treatment, and a collapse
  that buries a definition a surviving row uses are all rejected with a
  non-zero exit; a clean control diff yields no high signals; the template the
  skill writes does not itself grade as a finished review; one unresolved
  high-severity signal sinks a review; a rejected plan surfaces as a loop
  blocker rather than a crash; and a shallow prior review is reported blind.

Offline, stdlib-only, deterministic. All writes under tempfile.mkdtemp().
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
ASSETS = os.path.join(SKILL, "assets")
REVIEW_PY = os.path.join(ASSETS, "review.py")

sys.path.insert(0, HERE)
sys.path.insert(0, ASSETS)

import harness  # noqa: E402
import report as reportlib  # noqa: E402

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = " (%s)" % detail if detail else ""
    print("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL", suffix))


def run(*args):
    proc = subprocess.run(
        [sys.executable, REVIEW_PY] + list(args),
        capture_output=True, text=True, cwd=ASSETS,
    )
    return proc.returncode, proc.stdout + proc.stderr


def write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def write_plan(work, plan, name="plan.json"):
    path = os.path.join(work, name)
    write(path, json.dumps(plan, indent=2))
    return path


def filled_review(sigs):
    """A review answering every section and resolving every high signal."""
    out = ["# Concept review", ""]
    for title, _ in reportlib.SECTIONS:
        out.append("## %s" % title)
        out.append("")
        if title == "Verdict":
            out.append("needs-changes")
        elif title == "Confidence and basis":
            out.append(
                "Verified: read the condensed diff end to end and traced the relocation.\n"
                "Unverified: the suite was not run; rollout ordering is taken on trust."
            )
        elif title == "The change in one paragraph":
            out.append(harness.PARAGRAPH)
        elif title == "Signals":
            for s in sigs:
                out.append("- `%s` (%s)" % (reportlib.signal_token(s), s["severity"]))
                out.append(
                    "  - **addressed**: resolved against the design baseline and the "
                    "author's stated intent for this change."
                )
        else:
            out.append(
                "A specific, substantive answer grounded in the condensed diff and the "
                "extracted signals."
            )
        out.append("")
    return "\n".join(out)


def main():
    tmp = tempfile.mkdtemp(prefix="concept-first-review-eval-")
    try:
        diff_path = os.path.join(tmp, "treatment.diff")
        write(diff_path, harness.TREATMENT_DIFF)
        rules_path = os.path.join(tmp, "design-rules.json")
        write(rules_path, json.dumps(harness.DESIGN_RULES, indent=2))
        work = os.path.join(tmp, "work")

        # ── Opening the change ──────────────────────────────────────────────
        rc, out = run("open", "--diff", diff_path, "--into", work, "--rules", rules_path,
                      "--intent", harness.INTENT)
        check("open exits clean", rc == 0, out.strip().splitlines()[-1] if out else "")

        indexed = read(os.path.join(work, "indexed.diff"))
        check(
            "coordinate column is 1-based and physical",
            indexed.startswith("  1 │ diff --git") and "\n 24 │ " in indexed,
            indexed.split("\n")[0][:24],
        )

        moves = json.loads(read(os.path.join(work, "relocations.json")))
        check(
            "cross-file relocation detected",
            moves == [{"origin": harness.TREATMENT["move_removed"],
                       "destination": harness.TREATMENT["move_added"]}],
            str(moves),
        )

        # ── Condensing ──────────────────────────────────────────────────────
        plan_path = write_plan(work, harness.GOOD_PLAN)
        rc, out = run("draft", "--into", work, "--plan", plan_path)
        check("a well-formed plan is accepted", rc == 0,
              out.strip().splitlines()[-1] if rc else "")

        rc, out = run("commit", "--into", work, "--plan", plan_path)
        condensed = read(os.path.join(work, "condensed.diff"))
        check("commit writes condensed.diff", rc == 0 and bool(condensed))

        check(
            "dependency rows never reach the condensed diff",
            "import requests" not in condensed and "import json" not in condensed
            and "from core.models" not in condensed,
        )
        check(
            "the decisive rows survive",
            "for item in order.items:" in condensed
            and 'db.query("select * from items where id = ?"' in condensed
            and "resp = Response()" in condensed,
        )
        check(
            "mechanical repetition collapses to a placeholder",
            "resp.created_at" not in condensed and "-    ...\n" in condensed,
        )
        check(
            "the relocation is condensed identically at both ends",
            "-    ...\n" in condensed and "+        ...\n" in condensed,
        )
        check(
            "error prose is trimmed, control flow is not",
            "raise Missing(...)" in condensed and "no rows for tenant" not in condensed,
        )

        meta = json.loads(read(os.path.join(work, "meta.json")))
        stats = meta["stats"]
        check(
            "density improves without emptying the diff",
            0 < stats["kept_rows"] <= 0.6 * stats["total_rows"],
            "%d of %d rows kept" % (stats["kept_rows"], stats["total_rows"]),
        )

        # ── Plans that must be rejected ─────────────────────────────────────
        for name, plan, needle in (
            ("invented replacement text", harness.INVENTED_TEXT_PLAN, "not a trim of"),
            ("one-sided relocation treatment", harness.ONE_SIDED_MOVE_PLAN,
             "edited differently"),
            ("a collapse burying a used definition", harness.HIDES_DEFINITION_PLAN,
             "buries the definition"),
        ):
            path = write_plan(work, plan, "bad.json")
            rc, out = run("draft", "--into", work, "--plan", path)
            check("rejects %s" % name, rc != 0 and needle in out, out.strip()[-140:])

        rc, out = run("commit", "--into", work, "--plan", path)
        check("commit refuses a rejected plan", rc != 0 and not out.startswith("wrote"))
        check(
            "a rejected commit leaves the accepted condensed diff intact",
            read(os.path.join(work, "condensed.diff")) == condensed,
        )

        # ── Signals ─────────────────────────────────────────────────────────
        sigs = json.loads(read(os.path.join(work, "signals.json")))
        by_id = {}
        for s in sigs:
            by_id.setdefault(s["id"], []).append(s)

        check(
            "seeded N+1 is surfaced at the right line",
            any(s["line"] == harness.TREATMENT["n_plus_one_line"]
                for s in by_id.get("algo.io-in-loop", [])),
        )
        check(
            "seeded nested loop is surfaced at the right line",
            any(s["line"] == harness.TREATMENT["nested_loop_line"]
                for s in by_id.get("algo.nested-loop", [])),
        )
        check(
            "baseline turns a layering breach into a high-severity fact",
            any(s["line"] == harness.TREATMENT["layering_import_line"]
                and s["severity"] == "high"
                for s in by_id.get("dep.layering", [])),
        )
        check(
            "a dependency the baseline allows is not reported",
            not any("flask" in s["evidence"] for s in by_id.get("dep.new-external", [])),
        )
        check(
            "an unlisted dependency is high once a baseline is in play",
            any(s["severity"] == "high" and "requests" in s["evidence"]
                for s in by_id.get("dep.new-external", [])),
        )
        check(
            "behaviour changed with no test touched is surfaced",
            "radius.untested-change" in by_id,
        )
        check(
            "and it is not overstated as a declaration change",
            "radius.untested-surface" not in by_id,
        )
        check("every signal asks a question", all(s["question"].endswith("?") for s in sigs))

        # ── Control arm: the graders must be able to stay silent ────────────
        control_diff = os.path.join(tmp, "control.diff")
        write(control_diff, harness.CONTROL_DIFF)
        control_work = os.path.join(tmp, "control-work")
        rc, out = run("open", "--diff", control_diff, "--into", control_work,
                      "--rules", rules_path)
        control_sigs = json.loads(read(os.path.join(control_work, "signals.json")))
        highs = [s for s in control_sigs if s["severity"] == "high"]
        check("control diff raises no high signals", rc == 0 and not highs,
              ", ".join(s["id"] for s in highs))

        rc, out = run("draft", "--into", control_work)
        check(
            "an empty plan on the control diff is a faithful no-op",
            rc == 0
            and out.split("\n---\n")[0].rstrip("\n") == harness.CONTROL_DIFF.rstrip("\n"),
        )

        # ── The review deliverable ──────────────────────────────────────────
        rc, out = run("template", "--into", work)
        review_path = os.path.join(work, "REVIEW.md")
        check("template writes REVIEW.md", rc == 0 and os.path.exists(review_path))

        template_text = read(review_path)
        check(
            "the template names every signal it expects resolved",
            all(reportlib.signal_token(s) in template_text for s in sigs),
        )
        check(
            "the template carries the stated intent, so scope is checkable",
            harness.INTENT in template_text,
        )

        rc, out = run("grade", "--into", work)
        check(
            "an unanswered template FAILS the completeness check",
            rc != 0 and "REPORT_RESULT: FAIL" in out,
        )

        write(review_path, filled_review(sigs))
        rc, out = run("grade", "--into", work)
        check(
            "a complete review PASSES the completeness check",
            rc == 0 and "REPORT_RESULT: PASS" in out,
            out.strip().splitlines()[-1] if out else "",
        )

        holed = filled_review(sigs).replace(
            "**addressed**: resolved against the design baseline and the "
            "author's stated intent for this change.",
            "**open**: <what you concluded>",
            1,
        )
        write(review_path, holed)
        rc, out = run("grade", "--into", work)
        check(
            "one unresolved high signal sinks the review",
            rc != 0 and "high-signals-resolved — FAIL" in out,
        )

        # A review that never says what it checked is not a final answer.
        no_basis = filled_review(sigs).replace(
            "Verified: read the condensed diff end to end and traced the relocation.\n"
            "Unverified: the suite was not run; rollout ordering is taken on trust.",
            "I read it closely and it looks right.",
            1,
        )
        write(review_path, no_basis)
        rc, out = run("grade", "--into", work)
        check(
            "a review that never states its evidence is refused",
            rc != 0 and "confidence-basis — FAIL" in out,
        )
        write(review_path, filled_review(sigs))

        # ── The loop boundary ───────────────────────────────────────────────
        write_plan(work, harness.GOOD_PLAN)
        rc, out = run("state", "--into", work, "--json")
        state = json.loads(out)
        check("a finished run reports done",
              rc == 0 and state["done"] and state["phase"] == "done",
              state.get("phase", "?"))

        rc, out = run("state", "--into", os.path.join(tmp, "never-opened"), "--json")
        fresh = json.loads(out)
        check("an unopened run names its first action",
              fresh["phase"] == "unopened" and not fresh["done"]
              and "open" in fresh["next_action"])

        write_plan(work, harness.INVENTED_TEXT_PLAN)
        rc, out = run("state", "--into", work, "--json")
        stuck = json.loads(out)
        check("a rejected plan surfaces as a blocker, not a crash",
              rc == 0 and stuck["phase"] == "condense"
              and stuck["gates"]["plan"] == "rejected" and stuck["blockers"],
              str(stuck.get("blockers"))[:80])
        write_plan(work, harness.GOOD_PLAN)

        # ── Second opinion on a review written elsewhere ────────────────────
        thin_path = os.path.join(tmp, "prior-thin.md")
        write(thin_path, harness.PRIOR_THIN_REVIEW)
        rc, out = run("audit", "--into", work, "--prior", thin_path)
        check("a shallow prior review is reported blind to the high signals",
              rc != 0 and "BLIND-SPOT:" in out and "AUDIT_RESULT:" in out)

        strong_path = os.path.join(tmp, "prior-strong.md")
        write(strong_path, harness.prior_strong_review(sigs))
        rc, out = run("audit", "--into", work, "--prior", strong_path, "--json")
        audit = json.loads(out)
        check("a thorough prior review audits clean",
              rc == 0 and audit["blind_spots"] == [], str(audit["blind_spots"])[:120])

        # ── Deriving a baseline by asking ───────────────────────────────────
        repo = os.path.join(tmp, "repo")
        for rel, body in harness.BASELINE_TREE.items():
            path = os.path.join(repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            write(path, body)
        bl = os.path.join(tmp, "baseline")
        rc, out = run("propose", "--repo", repo, "--into", bl)
        proposal = json.loads(read(os.path.join(bl, "proposal.json")))
        questions = json.loads(read(os.path.join(bl, "questions.json")))
        check("propose reads the tree and writes a proposal",
              rc == 0 and proposal["files_scanned"] == len(harness.BASELINE_TREE),
              "%d file(s)" % proposal.get("files_scanned", -1))
        check("the layering the repo actually has is proposed",
              any(c["from"] == "core" and c["to"] == "web"
                  for c in proposal["forbidden_edge_candidates"]))
        check("every candidate reports what adopting it costs today",
              all("violations_today" in c and c["evidence"]
                  for c in proposal["forbidden_edge_candidates"]))
        check("questions fit the host agent's question tool",
              all(len(b) <= 4 and all(len(q["header"]) <= 12
                                      and 2 <= len(q["options"]) <= 4
                                      and q["question"].rstrip().endswith("?")
                                      for q in b)
                  for b in questions))

        answers = os.path.join(tmp, "answers.json")
        write(answers, json.dumps({
            "layers": ["core", "web"], "edges": {"core->web": True},
            "freeze_dependencies": True, "budgets": "strict",
        }))
        rules_out = os.path.join(tmp, "derived-rules.json")
        rc, out = run("adopt", "--into", bl, "--answers", answers, "--out", rules_out)
        derived = json.loads(read(rules_out))
        check("adopt writes what was chosen",
              rc == 0 and derived["forbidden_edges"] == [["core", "web"]]
              and "requests" in derived["allowed_external"])

        # The derived file must be usable by the thing it exists for.
        derived_work = os.path.join(tmp, "derived-work")
        breach = os.path.join(tmp, "breach.diff")
        write(breach, harness.LAYER_BREACH_DIFF)
        rc, out = run("open", "--diff", breach, "--into", derived_work, "--rules", rules_out)
        breach_sigs = json.loads(read(os.path.join(derived_work, "signals.json")))
        check("a derived baseline turns the breach it describes into a high signal",
              any(s["id"] == "dep.layering" and s["severity"] == "high"
                  for s in breach_sigs),
              ", ".join(sorted({s["id"] for s in breach_sigs})))

        rc, out = run("adopt", "--into", bl, "--out", os.path.join(tmp, "silent.json"))
        silent = json.loads(read(os.path.join(tmp, "silent.json")))
        check("silence adopts nothing",
              rc == 0 and not silent["forbidden_edges"] and not silent["allowed_external"])

        check(
            "no eval writes escaped the tempdir",
            not os.path.exists(os.path.join(ASSETS, "REVIEW.md"))
            and not os.path.exists(os.path.join(ASSETS, ".review")),
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for c in _checks if c)
    total = len(_checks)
    print("EVAL_RESULT: %s (%d/%d checks)"
          % ("PASS" if passed == total else "FAIL", passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
