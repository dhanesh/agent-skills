#!/usr/bin/env python3
"""Unit suite for casekit.py — stdlib only, offline, deterministic."""
import base64
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import casekit  # noqa: E402


GOOD_BRIEF = """# Decision brief

## The Decision

Should we move fee financing to a flat platform fee? Decide by 2026-09-30.

## Situation

Institutions push back on variable pricing at renewal.

## Options on the Table

- Flat platform fee per enrolled student
- Keep the rate spread with volume tiers
- Hybrid: flat floor plus a capped spread

## Evidence

Renewal churn ran at 12% last cycle.[^1] Average contract value is $48,000.[^1]

## Reference Class

- A peer moved to flat pricing and lost 2 of 9 anchor institutions.[^2]
- A second peer kept spread pricing and held renewals flat.[^2]

## Open Uncertainties

Whether procurement reads a flat fee as a price rise regardless of total cost.

## Sources

[^1]: https://example.test/renewals
[^2]: https://example.test/peers
"""

GOOD_DECISION = """# Decision record

## Frame
Pricing structure for the 2027 renewal cycle. Contract length is out of scope.

## Alternatives Considered
- Flat platform fee
- Rate spread with volume tiers
- Hybrid: flat floor plus capped spread
- Defer a cycle and instrument procurement objections first

## Values
Renewal retention over near-term margin.

## Reasoning
The hybrid keeps optionality while giving procurement a defensible headline number.

## Premortem
It is 2027-09-30 and this failed. Procurement read the floor as a price rise and
three anchors renegotiated down.

## Decision
Ship the hybrid to the three Q3 renewals. Owner: pricing lead.

## Falsifier
If two of three Q3 renewals cite the floor as their main objection, revert.

## Predictions
- 2026-12-31 | 0.70 | At least 2 of 3 Q3 renewals close on the hybrid
- 2026-12-31 | 0.40 | Blended take rate falls versus the prior cycle
"""


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = casekit.main(argv)
    return code, out.getvalue(), err.getvalue()


class TempRoot(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="casekit-test-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def new(self, slug="pricing", drill=False, by="2026-09-30"):
        argv = ["--root", self.root, "new", slug, "--decision", "Flat fee?", "--by", by]
        if drill:
            argv.append("--drill")
        code, _, err = run(argv)
        self.assertEqual(code, 0, err)
        return os.path.join(self.root, slug)

    def state(self, slug="pricing"):
        with open(os.path.join(self.root, slug, "state.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def ready(self, slug="pricing", brief=GOOD_BRIEF, decision=GOOD_DECISION):
        d = self.new(slug)
        casekit.write(os.path.join(d, "brief.md"), brief)
        casekit.write(os.path.join(d, "decision.md"), decision)
        return d


class TestBriefLint(unittest.TestCase):
    def lint(self, text, mode=casekit.LIVE, date=None):
        return {r.split()[0]: (ok, d) for r, ok, d in casekit.lint_brief(text, mode, date)}

    def test_good_brief_passes_every_live_rule(self):
        for rule, (ok, detail) in self.lint(GOOD_BRIEF).items():
            self.assertTrue(ok, "%s failed: %s" % (rule, detail))

    def test_live_mode_runs_no_hindsight_rules(self):
        # A live decision has no ending to leak; running D1/D2 would be theatre.
        self.assertNotIn("D1", self.lint(GOOD_BRIEF))
        self.assertNotIn("D2", self.lint(GOOD_BRIEF))

    def test_scaffold_residue_is_caught(self):
        self.assertFalse(self.lint(GOOD_BRIEF.replace("Institutions", "TODO"))["B0"][0])

    def test_missing_section_is_named(self):
        ok, detail = self.lint(GOOD_BRIEF.replace("## Reference Class", "## Notes"))["B1"]
        self.assertFalse(ok)
        self.assertIn("Reference Class", detail)

    def test_decision_needs_a_question_and_a_date(self):
        self.assertFalse(self.lint(GOOD_BRIEF.replace(
            "Should we move fee financing to a flat platform fee? Decide by 2026-09-30.",
            "We will move to a flat platform fee by 2026-09-30."))["B2"][0])
        self.assertFalse(self.lint(GOOD_BRIEF.replace(
            "Should we move fee financing to a flat platform fee? Decide by 2026-09-30.",
            "Should we move to a flat platform fee?"))["B2"][0])

    def test_two_options_is_a_whether_or_not_trap(self):
        ok, detail = self.lint(GOOD_BRIEF.replace(
            "- Hybrid: flat floor plus a capped spread\n", ""))["B3"]
        self.assertFalse(ok)
        self.assertIn("whether-or-not", detail)

    def test_unsourced_figure_fails(self):
        text = GOOD_BRIEF.replace(
            "## Reference Class", "Churn ran at 12% last cycle.\n\n## Reference Class")
        ok, detail = self.lint(text)["B4"]
        self.assertFalse(ok)
        self.assertIn("12%", detail)

    def test_citation_granularity_is_per_line_not_per_figure(self):
        # Documented limitation: one citation covers every figure on its line.
        # Tightening this to per-figure produces more noise than it catches.
        line = "Churn was 12% and contract value $48,000.[^1]"
        text = GOOD_BRIEF.replace("## Reference Class", line + "\n\n## Reference Class")
        self.assertTrue(self.lint(text)["B4"][0])

    def test_identifying_numerals_are_not_figures(self):
        for phrase in ("Section 230 does not apply.", "Certified to ISO 27001.",
                       "Rule 144A governed the placement."):
            text = GOOD_BRIEF.replace("## Reference Class",
                                      "%s\n\n## Reference Class" % phrase)
            self.assertTrue(self.lint(text)["B4"][0], phrase)

    def test_comma_grouped_figure_reports_whole_number(self):
        text = GOOD_BRIEF.replace("## Reference Class",
                                  "We serve about 2,200 institutions.\n\n## Reference Class")
        ok, detail = self.lint(text)["B4"]
        self.assertFalse(ok)
        self.assertIn("2,200", detail)

    def test_one_comparator_is_not_a_base_rate(self):
        ok, detail = self.lint(GOOD_BRIEF.replace(
            "- A second peer kept spread pricing and held renewals flat.[^2]\n", ""))["B5"]
        self.assertFalse(ok)
        self.assertIn("anecdote", detail)

    def test_empty_uncertainties_fails(self):
        text = GOOD_BRIEF.replace(
            "Whether procurement reads a flat fee as a price rise regardless of total cost.",
            "")
        self.assertFalse(self.lint(text)["B6"][0])

    def test_drill_mode_adds_hindsight_rules(self):
        res = self.lint(GOOD_BRIEF, casekit.DRILL, "2026-09-30")
        self.assertIn("D1", res)
        self.assertIn("D2", res)
        self.assertTrue(res["D1"][0])
        self.assertTrue(res["D2"][0])

    def test_drill_catches_hindsight_language(self):
        text = GOOD_BRIEF.replace("## Situation", "## Situation\n\nThe bet turned out "
                                                  "to be right.")
        self.assertFalse(self.lint(text, casekit.DRILL, "2026-09-30")["D1"][0])

    def test_drill_catches_future_dates(self):
        text = GOOD_BRIEF.replace("## Situation", "## Situation\n\nBy 2030 it consolidated.")
        ok, detail = self.lint(text, casekit.DRILL, "2026-09-30")["D2"]
        self.assertFalse(ok)
        self.assertIn("2030", detail)


class TestPredictions(unittest.TestCase):
    def parse(self, body):
        return casekit.parse_predictions("## Predictions\n%s\n" % body)

    def test_well_formed_line_parses(self):
        preds, errors = self.parse("- 2026-12-31 | 0.70 | Two of three renewals close")
        self.assertEqual(errors, [])
        self.assertEqual(preds[0]["p"], 0.70)
        self.assertEqual(preds[0]["date"], "2026-12-31")
        self.assertEqual(preds[0]["claim"], "Two of three renewals close")

    def test_malformed_line_is_reported_not_silently_dropped(self):
        preds, errors = self.parse("- probably fine by december")
        self.assertEqual(preds, [])
        self.assertIn("expected", errors[0])

    def test_certainty_is_rejected(self):
        for value in ("0", "1", "1.0", "0.0"):
            preds, errors = self.parse("- 2026-12-31 | %s | It happens" % value)
            self.assertEqual(preds, [], value)
            self.assertTrue(errors, value)
            self.assertIn("certainty is not a forecast", errors[0])

    def test_non_bullet_prose_is_ignored(self):
        preds, errors = self.parse("These are my forecasts:\n- 2026-12-31 | 0.6 | X")
        self.assertEqual(len(preds), 1)
        self.assertEqual(errors, [])


class TestDecisionRecord(unittest.TestCase):
    def test_complete_record_has_no_problems(self):
        self.assertEqual(casekit.check_decision(GOOD_DECISION), [])

    def test_missing_dq_links_are_named(self):
        problems = casekit.check_decision("## Decision\nShip it.\n")
        self.assertIn("## Frame", problems[0])
        self.assertIn("## Values", problems[0])

    def test_too_few_alternatives_is_rejected(self):
        text = GOOD_DECISION.replace(
            "- Hybrid: flat floor plus capped spread\n"
            "- Defer a cycle and instrument procurement objections first\n", "")
        problems = casekit.check_decision(text)
        self.assertTrue(any("option set" in p for p in problems), problems)

    def test_scaffold_residue_is_rejected(self):
        problems = casekit.check_decision(GOOD_DECISION.replace("Ship the hybrid", "TODO"))
        self.assertTrue(any("unfilled scaffold" in p for p in problems), problems)

    def test_no_parseable_forecast_is_rejected(self):
        text = GOOD_DECISION.replace(
            "- 2026-12-31 | 0.70 | At least 2 of 3 Q3 renewals close on the hybrid\n"
            "- 2026-12-31 | 0.40 | Blended take rate falls versus the prior cycle\n",
            "I think it goes well.\n")
        problems = casekit.check_decision(text)
        self.assertTrue(any("nothing falsifiable" in p for p in problems), problems)


class TestScoring(unittest.TestCase):
    def test_brier_of_perfect_forecasts_is_zero(self):
        self.assertEqual(casekit.brier([(1.0, 1.0), (0.0, 0.0)]), 0.0)

    def test_brier_of_coin_flips_is_a_quarter(self):
        self.assertAlmostEqual(casekit.brier([(0.5, 1.0), (0.5, 0.0)]), 0.25)

    def test_brier_penalises_confident_wrongness_most(self):
        confident_wrong = casekit.brier([(0.9, 0.0)])
        hedged_wrong = casekit.brier([(0.6, 0.0)])
        self.assertGreater(confident_wrong, hedged_wrong)

    def test_bins_group_by_decile_with_hit_rates(self):
        bins = casekit.calibration_bins([(0.75, 1.0), (0.72, 0.0), (0.15, 0.0)])
        self.assertEqual(len(bins), 2)
        low, high = bins[0], bins[1]
        self.assertEqual((low[0], low[2], low[4]), (0.1, 1, 0.0))
        self.assertEqual((high[2], high[4]), (2, 0.5))

    def test_probability_of_one_lands_in_the_top_bin(self):
        self.assertEqual(casekit.calibration_bins([(1.0, 1.0)])[0][0], 0.9)

    def test_resolved_pairs_ignores_unresolved_forecasts(self):
        state = {"predictions": [{"p": 0.7}, {"p": 0.4}, {"p": 0.9}],
                 "resolutions": {"1": True, "3": False}}
        self.assertEqual(casekit.resolved_pairs(state), [(0.7, 1.0), (0.9, 0.0)])


class TestLiveWorkflow(TempRoot):
    def test_new_scaffolds_live_without_a_reveal(self):
        d = self.new()
        self.assertTrue(os.path.exists(os.path.join(d, "brief.md")))
        self.assertTrue(os.path.exists(os.path.join(d, "decision.md")))
        self.assertFalse(os.path.exists(os.path.join(d, "reveal.md")))
        self.assertEqual(self.state()["mode"], casekit.LIVE)

    def test_new_rejects_a_malformed_deadline(self):
        code, _, err = run(["--root", self.root, "new", "x",
                            "--decision", "Q?", "--by", "September"])
        self.assertEqual(code, 2)
        self.assertIn("YYYY-MM-DD", err)

    def test_lint_fails_on_the_fresh_scaffold(self):
        self.new()
        code, out, _ = run(["--root", self.root, "lint", "pricing"])
        self.assertEqual(code, 1)
        self.assertIn("LINT_RESULT: FAIL", out)

    def test_commit_records_the_forecasts(self):
        self.ready()
        code, out, err = run(["--root", self.root, "commit", "pricing"])
        self.assertEqual(code, 0, err)
        self.assertIn("2 prediction(s)", out)
        state = self.state()
        self.assertEqual(state["stage"], casekit.COMMITTED)
        self.assertEqual(len(state["predictions"]), 2)
        self.assertEqual(len(state["decision_sha256"]), 64)

    def test_commit_refuses_an_incomplete_record(self):
        d = self.ready()
        casekit.write(os.path.join(d, "decision.md"), "## Decision\nShip it.\n")
        code, _, err = run(["--root", self.root, "commit", "pricing"])
        self.assertEqual(code, 2)
        self.assertIn("## Premortem", err)
        self.assertEqual(self.state()["stage"], casekit.DRAFTING)

    def test_a_committed_record_cannot_be_swapped(self):
        self.ready()
        run(["--root", self.root, "commit", "pricing"])
        code, _, err = run(["--root", self.root, "commit", "pricing"])
        self.assertEqual(code, 2)
        self.assertIn("already committed", err)

    def test_resolve_requires_a_commit(self):
        self.ready()
        code, _, err = run(["--root", self.root, "resolve", "pricing",
                            "--n", "1", "--outcome", "yes"])
        self.assertEqual(code, 2)
        self.assertIn("commit the decision", err)

    def test_resolve_rejects_an_out_of_range_forecast(self):
        self.ready()
        run(["--root", self.root, "commit", "pricing"])
        code, _, err = run(["--root", self.root, "resolve", "pricing",
                            "--n", "9", "--outcome", "yes"])
        self.assertEqual(code, 2)
        self.assertIn("does not exist", err)

    def test_resolution_cannot_be_flipped_without_force(self):
        self.ready()
        run(["--root", self.root, "commit", "pricing"])
        run(["--root", self.root, "resolve", "pricing", "--n", "1", "--outcome", "yes"])
        code, _, err = run(["--root", self.root, "resolve", "pricing",
                            "--n", "1", "--outcome", "no"])
        self.assertEqual(code, 2)
        self.assertIn("--force", err)
        self.assertIs(self.state()["resolutions"]["1"], True)
        code, _, _ = run(["--root", self.root, "resolve", "pricing", "--n", "1",
                          "--outcome", "no", "--force"])
        self.assertEqual(code, 0)
        self.assertIs(self.state()["resolutions"]["1"], False)

    def test_score_reports_brier_and_the_calibration_gap(self):
        self.ready()
        run(["--root", self.root, "commit", "pricing"])
        run(["--root", self.root, "resolve", "pricing", "--n", "1", "--outcome", "yes"])
        run(["--root", self.root, "resolve", "pricing", "--n", "2", "--outcome", "no"])
        code, out, _ = run(["--root", self.root, "score", "pricing"])
        self.assertEqual(code, 0)
        self.assertIn("Brier score:", out)
        self.assertIn("calibration gap:", out)
        self.assertIn("coach's judgement", out)   # the tool disclaims grading the chain

    def test_score_before_any_resolution_says_so(self):
        self.ready()
        run(["--root", self.root, "commit", "pricing"])
        code, out, _ = run(["--root", self.root, "score", "pricing"])
        self.assertEqual(code, 0)
        self.assertIn("nothing resolved yet", out)
        self.assertNotIn("Brier score:", out)

    def test_profile_warns_on_small_samples(self):
        self.ready()
        run(["--root", self.root, "commit", "pricing"])
        run(["--root", self.root, "resolve", "pricing", "--n", "1", "--outcome", "yes"])
        code, out, _ = run(["--root", self.root, "profile"])
        self.assertEqual(code, 0)
        self.assertIn("fewer than 10 resolved forecasts", out)

    def test_profile_with_no_decisions_fails_cleanly(self):
        code, _, err = run(["--root", self.root, "profile"])
        self.assertEqual(code, 2)
        self.assertIn("no decisions", err)

    def test_seal_is_refused_on_a_live_decision(self):
        self.ready()
        code, _, err = run(["--root", self.root, "seal", "pricing"])
        self.assertEqual(code, 2)
        self.assertIn("no known ending to hide", err)

    def test_reveal_is_refused_on_a_live_decision(self):
        self.ready()
        run(["--root", self.root, "commit", "pricing"])
        code, _, err = run(["--root", self.root, "reveal", "pricing"])
        self.assertEqual(code, 2)
        self.assertIn("has not happened yet", err)

    def test_status_reports_mode_stage_and_next_step(self):
        self.new()
        code, out, _ = run(["--root", self.root, "status", "pricing"])
        self.assertEqual(code, 0)
        self.assertIn("LIVE", out)
        self.assertIn("next:", out)

    def test_unknown_slug_fails_cleanly(self):
        code, _, err = run(["--root", self.root, "status", "ghost"])
        self.assertEqual(code, 2)
        self.assertIn("no decision", err)
        self.assertNotIn("Traceback", err)

    def test_corrupt_state_fails_cleanly(self):
        self.new()
        casekit.write(os.path.join(self.root, "pricing", "state.json"), "{not json")
        code, _, err = run(["--root", self.root, "status", "pricing"])
        self.assertEqual(code, 2)
        self.assertIn("unreadable", err)
        self.assertNotIn("Traceback", err)


class TestDrillWorkflow(TempRoot):
    def prepared(self, slug="hist"):
        d = self.new(slug, drill=True)
        casekit.write(os.path.join(d, "brief.md"), GOOD_BRIEF)
        casekit.write(os.path.join(d, "decision.md"), GOOD_DECISION)
        casekit.write(os.path.join(d, "reveal.md"), "# Reveal\n\nThey shipped the hybrid.\n")
        return d

    def test_drill_scaffolds_a_reveal(self):
        d = self.new("hist", drill=True)
        self.assertTrue(os.path.exists(os.path.join(d, "reveal.md")))
        self.assertEqual(self.state("hist")["mode"], casekit.DRILL)

    def test_seal_hides_the_ending_and_removes_the_plaintext(self):
        d = self.prepared()
        code, _, err = run(["--root", self.root, "seal", "hist"])
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(os.path.join(d, "reveal.md")))
        blob = casekit.read(os.path.join(d, "reveal.sealed"))
        self.assertNotIn("hybrid", blob)
        self.assertIn("hybrid", base64.b64decode(blob.strip()).decode("utf-8"))

    def test_seal_refuses_a_brief_that_fails_lint(self):
        d = self.prepared()
        casekit.write(os.path.join(d, "brief.md"),
                      GOOD_BRIEF.replace("## Situation", "## Situation\n\nIn hindsight, easy."))
        code, _, err = run(["--root", self.root, "seal", "hist"])
        self.assertEqual(code, 2)
        self.assertIn("fails lint", err)

    def test_commit_is_refused_before_the_ending_is_sealed(self):
        self.prepared()
        code, _, err = run(["--root", self.root, "commit", "hist"])
        self.assertEqual(code, 2)
        self.assertIn("seal the reveal", err)

    def test_reveal_is_refused_before_a_commit(self):
        self.prepared()
        run(["--root", self.root, "seal", "hist"])
        code, out, err = run(["--root", self.root, "reveal", "hist"])
        self.assertEqual(code, 2)
        self.assertIn("commit a decision before revealing", err)
        self.assertNotIn("hybrid", out)

    def test_full_drill_path_reveals_after_commit(self):
        self.prepared()
        run(["--root", self.root, "seal", "hist"])
        self.assertEqual(run(["--root", self.root, "commit", "hist"])[0], 0)
        code, out, _ = run(["--root", self.root, "reveal", "hist"])
        self.assertEqual(code, 0)
        self.assertIn("They shipped the hybrid", out)
        self.assertEqual(self.state("hist")["stage"], casekit.REVEALED)

    def test_tampered_seal_is_rejected(self):
        d = self.prepared()
        run(["--root", self.root, "seal", "hist"])
        run(["--root", self.root, "commit", "hist"])
        casekit.write(os.path.join(d, "reveal.sealed"),
                      base64.b64encode(b"a different ending").decode("ascii") + "\n")
        code, out, err = run(["--root", self.root, "reveal", "hist"])
        self.assertEqual(code, 2)
        self.assertIn("checksum", err)
        self.assertNotIn("different ending", out)

    def test_drill_forecasts_join_the_same_profile(self):
        self.prepared()
        run(["--root", self.root, "seal", "hist"])
        run(["--root", self.root, "commit", "hist"])
        run(["--root", self.root, "resolve", "hist", "--n", "1", "--outcome", "yes"])
        code, out, _ = run(["--root", self.root, "profile"])
        self.assertEqual(code, 0)
        self.assertIn("hist (drill)", out)
        self.assertIn("ACROSS 1 DECISION(S)", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
