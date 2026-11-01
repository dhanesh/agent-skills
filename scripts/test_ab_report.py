#!/usr/bin/env python3
"""Unit tests for scripts/ab_report.py (stdlib only, offline, no git, no baseline)."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ab_report  # noqa: E402

PASS20, PASS26 = ("PASS", 20, 20), ("PASS", 26, 26)


def r(skill, mark, dim="d", old=0, new=1, note=""):
    return {"skill": skill, "dimension": dim, "old": old, "new": new, "note": note,
            "mark": mark}


META = {"branch": "fix/a--b", "head": "abc1234", "base": "def5678",
        "base_subject": "Merge", "date": "2026-11-01", "result": "PASS"}


class Verdicts(unittest.TestCase):
    def test_an_improved_row_makes_the_skill_improved(self):
        self.assertEqual(ab_report.verdict(["HELD*", "IMPROVED"], PASS20, PASS20),
                         "improved")

    def test_rows_with_no_improvement_hold(self):
        self.assertEqual(ab_report.verdict(["HELD*"], PASS20, PASS26), "held")

    def test_no_row_is_not_measured_not_a_pass(self):
        self.assertEqual(ab_report.verdict([], PASS20, PASS20), "not measured")

    def test_worse_or_unproven_rows_win_over_improved(self):
        for bad in ("WORSE", "UNPROVEN"):
            self.assertEqual(ab_report.verdict(["IMPROVED", bad], None, PASS20), "worse")

    def test_a_failing_or_shrinking_eval_is_worse(self):
        self.assertEqual(ab_report.verdict(["IMPROVED"], PASS20, ("FAIL", 19, 20)),
                         "worse")
        self.assertEqual(ab_report.verdict(["IMPROVED"], PASS26, PASS20), "worse")
        self.assertEqual(ab_report.verdict([], None, ("ERROR", 0, 0)), "worse")

    def test_a_new_eval_alone_is_not_an_improvement(self):
        self.assertEqual(ab_report.verdict([], None, PASS20), "not measured")


class Owners(unittest.TestCase):
    def test_the_qualifier_is_dropped(self):
        self.assertEqual(ab_report.owner_of("okf-site-kit (consumer)"), "okf-site-kit")
        self.assertEqual(ab_report.owner_of("gates"), "gates")


class Report(unittest.TestCase):
    def setUp(self):
        self.rows = [r("verification-skill-forge", "IMPROVED", "names refused", 0, 2),
                     r("verification-skill-forge", "HELD*"),
                     r("gates", "HELD", "a | b"),
                     r("factory-conductor", "HELD*")]
        self.changed = {"verification-skill-forge": 3, "spec-first-planning": 1}
        self.evals = {"verification-skill-forge": (PASS20, PASS26),
                      "spec-first-planning": (PASS20, PASS20)}

    def test_changed_skills_get_a_verdict_and_their_rows(self):
        md, s = ab_report.build_report(self.rows, META, self.changed, self.evals)
        self.assertEqual(s["skills"]["verification-skill-forge"],
                         {"verdict": "improved", "improved": 1, "rows": 2,
                          "eval": ["PASS 20/20", "PASS 26/26"]})
        self.assertEqual(s["skills"]["spec-first-planning"]["verdict"], "not measured")
        self.assertIn("| names refused | 0 | 2 | IMPROVED |", md)
        self.assertIn("No A/B row names this skill.", md)

    def test_unchanged_owners_appear_only_as_counts(self):
        md, s = ab_report.build_report(self.rows, META, self.changed, self.evals)
        self.assertNotIn("factory-conductor", s["skills"])
        self.assertIn("| `factory-conductor` | 0 | 0 | 1 | 0 | 0 |", md)
        self.assertIn("| `gates` | 0 | 1 | 0 | 0 | 0 |", md)
        self.assertNotIn("### gates", md)

    def test_a_pipe_in_a_cell_does_not_break_the_table(self):
        md, _ = ab_report.build_report([r("gates", "HELD", "a | b")], META,
                                       {"gates": 1}, {})
        self.assertIn("a \\| b", md)

    def test_the_summary_round_trips_through_the_index(self):
        d = tempfile.mkdtemp(prefix="ab-report-")
        try:
            md, s = ab_report.build_report(self.rows, META, self.changed, self.evals)
            path = ab_report.write_report(d, md, s)
            self.assertEqual(os.path.basename(path), "2026-11-01-fix-a-b.md")
            # A "--" in a branch name must not end the HTML comment early.
            line, = [x for x in md.splitlines() if "ab-report-summary" in x]
            self.assertNotIn("--", line[len("<!-- "):-len(" -->")])
            (name, back), = ab_report.read_summaries(d)
            self.assertEqual((name, back), ("2026-11-01-fix-a-b.md", s))
            with open(os.path.join(d, "README.md"), encoding="utf-8") as f:
                index = f.read()
            self.assertIn("### verification-skill-forge", index)
            self.assertIn("[fix/a--b](2026-11-01-fix-a-b.md): improved, 1 improved of 2 "
                          "rows, eval PASS 20/20 → PASS 26/26", index)
            self.assertIn("`AB_RESULT: PASS`, 2 skill(s)", index)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_a_rerun_replaces_the_branch_report(self):
        d = tempfile.mkdtemp(prefix="ab-report-")
        try:
            for result in ("FAIL", "PASS"):
                md, s = ab_report.build_report(self.rows, dict(META, result=result),
                                               self.changed, self.evals)
                ab_report.write_report(d, md, s)
            self.assertEqual([s["result"] for _, s in ab_report.read_summaries(d)],
                             ["PASS"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Evals(unittest.TestCase):
    def test_the_score_is_read_from_the_last_result_line(self):
        d = tempfile.mkdtemp(prefix="ab-eval-")
        try:
            os.makedirs(os.path.join(d, "s", "eval"))
            with open(os.path.join(d, "s", "eval", "run_eval.py"), "w") as f:
                f.write("print('EVAL_RESULT: FAIL (1/2 checks)')\n"
                        "print('EVAL_RESULT: PASS (2/2 checks)')\n")
            self.assertEqual(ab_report.eval_score(d, "s"), ("PASS", 2, 2))
            self.assertIsNone(ab_report.eval_score(d, "absent"))
            with open(os.path.join(d, "s", "eval", "run_eval.py"), "w") as f:
                f.write("raise SystemExit(1)\n")
            self.assertEqual(ab_report.eval_score(d, "s"), ("ERROR", 0, 0))
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=1)
