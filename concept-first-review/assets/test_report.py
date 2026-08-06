#!/usr/bin/env python3
"""Unit suite for report: the review template, and the grader that refuses a hollow review."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import report as reportlib


HIGH_SIG = {
    "id": "algo.io-in-loop",
    "severity": "high",
    "file": "svc/orders.py",
    "line": 42,
    "evidence": "rows.append(db.query(item.id))",
    "question": "an I/O call inside a loop is the N+1 shape. Can it be batched?",
}
LOW_SIG = {
    "id": "cfg.flag",
    "severity": "low",
    "file": "svc/orders.py",
    "line": 7,
    "evidence": "os.getenv('FAST_PATH')",
    "question": "what is the default?",
}
AGG_SIG = {
    "id": "radius.untested-surface",
    "severity": "high",
    "file": None,
    "line": 0,
    "evidence": "3 source files changed, 0 test files",
    "question": "is the new behaviour covered anywhere?",
}

STATS = {"total_rows": 120, "total_files": 4, "kept_rows": 31, "density_pct": 25.8}
META = {"label": "git diff HEAD~1..HEAD"}

PARAGRAPH = (
    "The order listing endpoint now resolves line items itself instead of asking the "
    "catalogue service for each order, which moves a per-request fan-out into a single "
    "batched read and changes who owns the caching decision for item metadata."
)


def filled(dispositions, paragraph=PARAGRAPH, verdict="needs-changes", sigs=(HIGH_SIG,)):
    """Build a review that is complete except for what the caller varies."""
    out = ["# Concept review", ""]
    for title, _ in reportlib.SECTIONS:
        out.append("## %s" % title)
        out.append("")
        if title == "Verdict":
            out.append(verdict)
        elif title == "The change in one paragraph":
            out.append(paragraph)
        elif title == "Signals":
            for s in sigs:
                token = reportlib.signal_token(s)
                out.append("- `%s` (%s) — %s" % (token, s["severity"], s["question"]))
                disposition = dispositions.get(token)
                if disposition:
                    out.append("  - %s" % disposition)
        else:
            out.append("Something substantive and specific about this change.")
        out.append("")
    return "\n".join(out)


class TestTemplate(unittest.TestCase):
    def setUp(self):
        self.text = reportlib.template([HIGH_SIG, LOW_SIG], STATS, META)

    def test_has_every_required_section(self):
        for title, _ in reportlib.SECTIONS:
            self.assertIn("## %s" % title, self.text)

    def test_lists_every_signal_with_a_stable_token(self):
        self.assertIn("`algo.io-in-loop@svc/orders.py:42`", self.text)
        self.assertIn("`cfg.flag@svc/orders.py:7`", self.text)

    def test_header_reports_the_stats(self):
        self.assertIn("120 changed rows across 4 files", self.text)
        self.assertIn("25.8% density", self.text)

    def test_the_template_itself_does_not_pass_the_grader(self):
        checks, ok = reportlib.grade(self.text, [HIGH_SIG, LOW_SIG])
        self.assertFalse(ok, "an unanswered template must not grade as a finished review")

    def test_aggregate_signal_token_has_no_file(self):
        self.assertEqual(reportlib.signal_token(AGG_SIG), "radius.untested-surface@-:0")


class TestGrader(unittest.TestCase):
    def test_accepts_a_complete_review(self):
        text = filled({
            "algo.io-in-loop@svc/orders.py:42":
                "**addressed**: batching is possible here and the follow-up is filed as ORD-412.",
        })
        checks, ok = reportlib.grade(text, [HIGH_SIG])
        self.assertTrue(ok, [c for c in checks if not c[1]])

    def test_dismissal_with_a_reason_also_counts(self):
        text = filled({
            "algo.io-in-loop@svc/orders.py:42":
                "**dismissed**: the loop runs over at most four configured regions.",
        })
        _, ok = reportlib.grade(text, [HIGH_SIG])
        self.assertTrue(ok)

    def test_negative_unresolved_high_signal_fails(self):
        text = filled({
            "algo.io-in-loop@svc/orders.py:42": "**open**: <what you concluded>",
        })
        checks, ok = reportlib.grade(text, [HIGH_SIG])
        self.assertFalse(ok)
        self.assertIn("high-signals-resolved", [c[0] for c in checks if not c[1]])

    def test_negative_a_one_word_disposition_fails(self):
        text = filled({"algo.io-in-loop@svc/orders.py:42": "**dismissed**: fine"})
        _, ok = reportlib.grade(text, [HIGH_SIG])
        self.assertFalse(ok)

    def test_low_severity_signals_need_no_disposition(self):
        text = filled(
            {"algo.io-in-loop@svc/orders.py:42":
                "**addressed**: the batched read lands in the same change."},
            sigs=(HIGH_SIG, LOW_SIG),
        )
        _, ok = reportlib.grade(text, [HIGH_SIG, LOW_SIG])
        self.assertTrue(ok)

    def test_negative_missing_section(self):
        text = filled({
            "algo.io-in-loop@svc/orders.py:42":
                "**addressed**: the batched read lands in the same change.",
        })
        text = text.replace("## Architecture and boundaries", "## Something else")
        checks, ok = reportlib.grade(text, [HIGH_SIG])
        self.assertFalse(ok)
        self.assertIn("section:Architecture and boundaries", [c[0] for c in checks if not c[1]])

    def test_generic_type_syntax_is_not_placeholder_text(self):
        # `Arc<RwLock<IndexMetadata>>` in an architecture finding once failed the
        # grader as "leftover placeholder", which sinks every systems review.
        text = filled(
            {"algo.io-in-loop@svc/orders.py:42":
                "**addressed**: the batched read lands in the same change."},
        ).replace(
            "Something substantive and specific about this change.",
            "State is held in Arc<RwLock<IndexMetadata>> and returned as Vec<Id>.",
        )
        checks, ok = reportlib.grade(text, [HIGH_SIG])
        self.assertTrue(ok, [c for c in checks if not c[1]])

    def test_an_unedited_prompt_still_fails(self):
        text = filled(
            {"algo.io-in-loop@svc/orders.py:42":
                "**addressed**: the batched read lands in the same change."},
        ).replace(
            "Something substantive and specific about this change.",
            "<%s>" % dict(reportlib.SECTIONS)["Concepts"],
            1,
        )
        checks, ok = reportlib.grade(text, [HIGH_SIG])
        self.assertFalse(ok)

    def test_negative_thin_paragraph(self):
        text = filled(
            {"algo.io-in-loop@svc/orders.py:42":
                "**addressed**: the batched read lands in the same change."},
            paragraph="It makes orders faster.",
        )
        checks, ok = reportlib.grade(text, [HIGH_SIG])
        self.assertFalse(ok)
        self.assertIn("paragraph-substance", [c[0] for c in checks if not c[1]])

    def test_every_declared_verdict_grades_unambiguously(self):
        # `ship-with-followups` contains `ship`; a \b match called it ambiguous
        # and failed every review that chose it.
        for verdict in reportlib.VERDICTS:
            text = filled(
                {"algo.io-in-loop@svc/orders.py:42":
                    "**addressed**: the batched read lands in the same change."},
                verdict=verdict,
            )
            checks, ok = reportlib.grade(text, [HIGH_SIG])
            self.assertTrue(ok, "%s: %s" % (verdict, [c for c in checks if not c[1]]))

    def test_negative_ambiguous_verdict(self):
        text = filled(
            {"algo.io-in-loop@svc/orders.py:42":
                "**addressed**: the batched read lands in the same change."},
            verdict="ship or needs-changes, hard to say",
        )
        checks, ok = reportlib.grade(text, [HIGH_SIG])
        self.assertFalse(ok)
        self.assertIn("verdict", [c[0] for c in checks if not c[1]])

    def test_aggregate_signal_must_also_be_resolved(self):
        text = filled({}, sigs=(AGG_SIG,))
        _, ok = reportlib.grade(text, [AGG_SIG])
        self.assertFalse(ok)
        text = filled(
            {"radius.untested-surface@-:0":
                "**addressed**: coverage lands in the follow-up branch, tracked as ORD-413."},
            sigs=(AGG_SIG,),
        )
        _, ok = reportlib.grade(text, [AGG_SIG])
        self.assertTrue(ok)

    def test_format_checks_emits_the_protocol(self):
        checks, _ = reportlib.grade(filled({}), [HIGH_SIG])
        out = reportlib.format_checks(checks)
        self.assertTrue(out.strip().splitlines()[-1].startswith("REPORT_RESULT:"))
        self.assertIn("CHECK: verdict —", out)


class TestLoopState(unittest.TestCase):
    """The loop boundary: position as data, so an interrupted run resumes."""

    def test_unopened_work_directory(self):
        st = reportlib.loop_state({"work": ".review", "work_exists": False})
        self.assertEqual(st["phase"], "unopened")
        self.assertFalse(st["done"])
        self.assertIn("open", st["next_action"])

    def test_rejected_plan_surfaces_its_rejections_as_blockers(self):
        st = reportlib.loop_state({
            "work_exists": True, "has_source": True, "has_plan": True,
            "plan_ok": False, "plan_errors": ["collapse 5-7: mixes diff markers"],
            "stats": {"density_pct": 91.0}, "signals": [],
        })
        self.assertEqual(st["phase"], "condense")
        self.assertEqual(st["gates"]["plan"], "rejected")
        self.assertEqual(st["blockers"], ["collapse 5-7: mixes diff markers"])
        self.assertIn("draft", st["next_action"])

    def test_accepted_plan_not_yet_committed(self):
        st = reportlib.loop_state({
            "work_exists": True, "has_source": True, "has_plan": True,
            "plan_ok": True, "has_condensed": False, "stats": {"density_pct": 30.0},
            "signals": [],
        })
        self.assertEqual(st["gates"]["plan"], "accepted")
        self.assertIn("commit", st["next_action"])
        self.assertEqual(st["density_pct"], 30.0)

    def test_an_empty_plan_is_called_out_rather_than_waved_through(self):
        # An empty plan compiles, so without this a loop could "condense" by
        # doing nothing at all and never be told.
        st = reportlib.loop_state({
            "work_exists": True, "has_source": True, "has_plan": True,
            "plan_ok": True, "plan_is_empty": True, "has_condensed": False,
            "stats": {"density_pct": 100.0}, "signals": [],
        })
        self.assertEqual(st["phase"], "condense")
        self.assertIn("the plan is empty", st["next_action"])
        self.assertEqual(st["blockers"], [], "a deliberate empty plan must not block")

    def test_committing_an_empty_plan_still_progresses(self):
        st = reportlib.loop_state({
            "work_exists": True, "has_source": True, "has_plan": True,
            "plan_ok": True, "plan_is_empty": True, "has_condensed": True,
            "has_review": False, "signals": [],
        })
        self.assertEqual(st["phase"], "interrogate")

    def test_condensed_but_no_review_moves_to_interrogate(self):
        st = reportlib.loop_state({
            "work_exists": True, "has_source": True, "has_plan": True, "plan_ok": True,
            "has_condensed": True, "has_review": False, "signals": [],
        })
        self.assertEqual(st["phase"], "interrogate")
        self.assertIn("template", st["next_action"])

    def test_failing_review_reports_gaps_and_unresolved_signals(self):
        st = reportlib.loop_state({
            "work_exists": True, "has_source": True, "has_plan": True, "plan_ok": True,
            "has_condensed": True, "has_review": True, "review_ok": False,
            "review_gaps": ["high-signals-resolved", "verdict"],
            "signals": [HIGH_SIG, LOW_SIG],
        })
        self.assertEqual(st["phase"], "repair")
        self.assertEqual(st["gates"]["review"], "fail")
        self.assertIn("verdict", st["blockers"])
        self.assertEqual(st["unresolved_high"], ["algo.io-in-loop@svc/orders.py:42"])

    def test_terminates(self):
        st = reportlib.loop_state({
            "work_exists": True, "has_source": True, "has_plan": True, "plan_ok": True,
            "has_condensed": True, "has_review": True, "review_ok": True,
            "signals": [HIGH_SIG],
        })
        self.assertEqual(st["phase"], "done")
        self.assertTrue(st["done"])
        self.assertEqual(st["gates"]["review"], "pass")

    def test_every_phase_is_a_declared_phase(self):
        probes = [
            {"work_exists": False},
            {"work_exists": True, "has_source": True, "has_plan": False, "signals": []},
            {"work_exists": True, "has_source": True, "has_plan": True, "plan_ok": True,
             "has_condensed": True, "has_review": True, "review_ok": True, "signals": []},
        ]
        for probe in probes:
            self.assertIn(reportlib.loop_state(probe)["phase"], reportlib.PHASES)


PRIOR_STRONG = """# Review of the orders change

The endpoint now batches item reads. The per-item call at svc/orders.py:42 is the
N+1 shape and needs the batched query before this ships; the layering between the
web module and the core domain still holds, and the algorithm is linear in orders.
Rollback risk is low. Verdict: needs-changes.
"""

PRIOR_THIN = """# LGTM

Looks fine to me. The code reads well and is consistent with what is around it.
"""


class TestAuditPrior(unittest.TestCase):
    """Second opinion: what did the other reviewer never look at?"""

    def test_engaged_signal_is_not_a_blind_spot(self):
        lines, blind = reportlib.audit_prior(
            PRIOR_STRONG, [HIGH_SIG], {"svc/orders.py"}
        )
        self.assertEqual(blind, [])
        self.assertIn("COVERED: algo.io-in-loop@svc/orders.py:42", lines)

    def test_unengaged_high_signal_is_a_blind_spot(self):
        lines, blind = reportlib.audit_prior(PRIOR_THIN, [HIGH_SIG], {"svc/orders.py"})
        self.assertEqual(blind, ["algo.io-in-loop@svc/orders.py:42"])
        self.assertTrue(any(line.startswith("BLIND-SPOT:") for line in lines))

    def test_low_severity_signals_are_not_required(self):
        _, blind = reportlib.audit_prior(PRIOR_THIN, [LOW_SIG], {"svc/orders.py"})
        self.assertEqual(blind, [])

    def test_quoting_the_evidence_counts_as_engagement(self):
        prior = "The line `rows.append(db.query(item.id))` worries me. Verdict: needs-changes."
        _, blind = reportlib.audit_prior(prior, [HIGH_SIG], {"svc/orders.py"})
        self.assertEqual(blind, [])

    def test_citing_an_untouched_file_is_flagged(self):
        prior = PRIOR_STRONG + "\nAlso see billing/legacy.py for the old path.\n"
        lines, _ = reportlib.audit_prior(prior, [HIGH_SIG], {"svc/orders.py"})
        self.assertTrue(any("OUT-OF-SCOPE" in line and "billing/legacy.py" in line
                            for line in lines))

    def test_dimensions_never_engaged_are_reported_as_a_weak_signal(self):
        lines, _ = reportlib.audit_prior(PRIOR_THIN, [], set())
        thin = [line for line in lines if line.startswith("THIN:")]
        self.assertEqual(len(thin), 1)
        self.assertIn("weak signal", thin[0])

    def test_missing_verdict_is_reported(self):
        lines, _ = reportlib.audit_prior(PRIOR_THIN, [], set())
        self.assertTrue(any(line.startswith("NO-VERDICT:") for line in lines))
        lines, _ = reportlib.audit_prior(PRIOR_STRONG, [], {"svc/orders.py"})
        self.assertFalse(any(line.startswith("NO-VERDICT:") for line in lines))


if __name__ == "__main__":
    unittest.main(verbosity=1)
