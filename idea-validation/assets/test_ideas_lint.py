"""Brief grammar and lint rules (spec 1.2)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ideas as ID  # noqa: E402
from ideas_testkit import GOOD, brief_file, fixture, repo, run  # noqa: E402

A1 = ("- A1 [critical] [demand]: People sign up (test: landing page; metric: sign-ups; "
      "pass: >= 30; kill: < 10; by: 2026-12-01)")
A2 = ("- A2 [normal] [pricing]: People pay (test: price page; metric: clicks on buy; "
      "pass: >= 5; kill: < 1; by: 2026-12-01)")
EXP = ["- Landing page test. Tests A1 and A2."]
SECTIONS = ("Problem", "Target user", "Value proposition", "Out of scope")


def make(assumptions=(A1, A2), experiments=None, drop=None, title="# Idea: Test idea",
         heading_case=str):
    if experiments is None:
        experiments = EXP if len(assumptions) > 1 else ["- Landing page test. Tests A1."]
    lines = [title, ""] if title else []
    for s in SECTIONS[:3]:
        if s != drop:
            lines += ["## " + heading_case(s), "Some text.", ""]
    if drop != "Riskiest assumptions":
        lines += ["## " + heading_case("Riskiest assumptions")] + list(assumptions) + [""]
    if drop != "Experiments":
        lines += ["## " + heading_case("Experiments")] + list(experiments) + [""]
    if drop != "Out of scope":
        lines += ["## " + heading_case("Out of scope"), "- Nothing.", ""]
    return "\n".join(lines)


def lint(text):
    root = repo()
    return run(root, "lint", brief_file(root, text=text))


def a(n="1", crit="critical", cat="demand", stmt="It holds", test="t", metric="m",
      pas=">= 30", kill="< 10", by="2026-12-01"):
    return ("- A%s [%s] [%s]: %s (test: %s; metric: %s; pass: %s; kill: %s; by: %s)"
            % (n, crit, cat, stmt, test, metric, pas, kill, by))


class LintPass(unittest.TestCase):
    def test_good_fixture_is_clean(self):
        rc, out = lint(GOOD)
        self.assertEqual(rc, 0, out)
        self.assertIn("LINT: ok", out)
        self.assertNotIn("PROBLEM:", out)

    def test_minimal_brief_is_clean(self):
        rc, out = lint(make())
        self.assertEqual(rc, 0, out)

    def test_sections_match_case_insensitively_in_any_order(self):
        text = make(heading_case=str.upper)
        self.assertEqual(lint(text)[0], 0)
        parts = text.split("\n## ")
        reordered = parts[0] + "\n## " + "\n## ".join(reversed(parts[1:]))
        self.assertEqual(lint(reordered)[0], 0)

    def test_statement_may_hold_parentheses(self):
        rc, out = lint(make(assumptions=(a(stmt="Users (busy ones) sign up"),)))
        self.assertEqual(rc, 0, out)

    def test_inverse_direction_thresholds_are_valid(self):
        rc, out = lint(make(assumptions=(a(pas="<= 2", kill="> 10"),)))
        self.assertEqual(rc, 0, out)

    def test_boundary_touching_but_not_overlapping_is_valid(self):
        self.assertEqual(lint(make(assumptions=(a(pas=">= 30", kill="< 30"),)))[0], 0)
        self.assertEqual(lint(make(assumptions=(a(pas="> 30", kill="<= 30"),)))[0], 0)

    def test_decimal_and_negative_thresholds(self):
        self.assertEqual(lint(make(assumptions=(a(pas=">= 0.5", kill="< -1.5"),)))[0], 0)


class LintFail(unittest.TestCase):
    def assertProblem(self, text, needle):
        rc, out = lint(text)
        self.assertEqual(rc, 2, out)
        self.assertNotIn("LINT: ok", out)
        problems = [l for l in out.splitlines() if l.startswith("PROBLEM:")]
        self.assertTrue(any(needle in l for l in problems), out)
        return out

    def test_missing_title(self):
        self.assertProblem(make(title=None), "title")

    def test_each_required_section_missing(self):
        for s in SECTIONS + ("Riskiest assumptions", "Experiments"):
            with self.subTest(section=s):
                self.assertProblem(make(drop=s), s.lower())

    def test_id_gap(self):
        self.assertProblem(make(assumptions=(A1, a(n="3", crit="normal"))), "A1..A")

    def test_id_repeat(self):
        self.assertProblem(make(assumptions=(A1, a(n="1", crit="normal"))), "A1..A")

    def test_no_critical(self):
        self.assertProblem(make(assumptions=(a(crit="normal"),)), "critical")

    def test_no_assumptions(self):
        self.assertProblem(make(assumptions=(), experiments=["- Something. Tests A1."]),
                           "no assumption")

    def test_each_field_empty_or_missing(self):
        for field in ("test", "metric", "pass", "kill", "by"):
            with self.subTest(field=field):
                kw = {{"pass": "pas"}.get(field, field): ""}
                self.assertProblem(make(assumptions=(a(**kw),)), field)
        line = "- A1 [critical] [demand]: It holds (test: t; metric: m; pass: >= 3; by: 2026-12-01)"
        self.assertProblem(make(assumptions=(line,)), "kill")

    def test_empty_statement(self):
        self.assertProblem(make(assumptions=(a(stmt=""),)), "statement")

    def test_bad_tags_and_malformed_line(self):
        self.assertProblem(make(assumptions=(a(crit="vital"),)), "A1")
        self.assertProblem(make(assumptions=(a(cat="market"),)), "A1")
        self.assertProblem(make(assumptions=("- A1 sign-ups will be high",)), "assumption line")

    def test_bad_operator_or_number(self):
        self.assertProblem(make(assumptions=(a(pas="== 30"),)), "pass")
        self.assertProblem(make(assumptions=(a(pas=">= lots"),)), "pass")

    def test_non_finite_threshold(self):
        self.assertProblem(make(assumptions=(a(pas=">= inf"),)), "pass")
        self.assertProblem(make(assumptions=(a(kill="< nan"),)), "kill")

    def test_same_direction(self):
        self.assertProblem(make(assumptions=(a(pas=">= 30", kill=">= 1"),)), "opposite")

    def test_overlap(self):
        self.assertProblem(make(assumptions=(a(pas=">= 30", kill="< 40"),)), "overlap")
        self.assertProblem(make(assumptions=(a(pas=">= 30", kill="<= 30"),)), "overlap")
        self.assertProblem(make(assumptions=(a(pas="< 5", kill=">= 3"),)), "overlap")

    def test_unreal_date(self):
        self.assertProblem(make(assumptions=(a(by="2026-02-30"),)), "date")
        self.assertProblem(make(assumptions=(a(by="next week"),)), "date")

    def test_experiment_without_assumption_id(self):
        self.assertProblem(make(experiments=EXP + ["- Ask some friends."]), "names no assumption")

    def test_experiment_with_unknown_assumption_id(self):
        self.assertProblem(make(experiments=["- Ads. Tests A7."]), "A7")

    def test_no_experiments(self):
        self.assertProblem(make(experiments=["Prose with no bullets. A1."]), "experiment")

    def test_unparsed_assumption_lines_are_problems(self):
        tail = ("[critical] [pricing]: People pay (test: x; metric: y; pass: >= 10; "
                "kill: < 2; by: 2026-12-01)")
        for prefix in ("+ A3 ", "1. A3 ", "2) A3 ", "A3 ", "* A3 "):
            with self.subTest(prefix=prefix):
                rc, out = lint(make(assumptions=(A1, A2, prefix + tail)))
                self.assertEqual(rc, 2, out)
                self.assertIn("A3", "\n".join(l for l in out.splitlines()
                                             if l.startswith("PROBLEM:")))
                parsed = ID.parse_brief(make(assumptions=(A1, A2, prefix + tail)))
                self.assertEqual([x["id"] for x in parsed["assumptions"]], ["A1", "A2"])

    def test_numbered_and_plus_experiment_bullets_count(self):
        for bullet in ("+ ", "1. ", "1) "):
            with self.subTest(bullet=bullet):
                parsed = ID.parse_brief(make(experiments=[bullet + "Landing page. Tests A1 and A2."]))
                self.assertEqual([e["ids"] for e in parsed["experiments"]], [["A1", "A2"]])
                self.assertEqual(parsed["problems"], [])

    def test_bad_fixture_lists_every_fault(self):
        rc, out = lint(fixture("bad-brief.md"))
        self.assertEqual(rc, 2)
        problems = [l for l in out.splitlines() if l.startswith("PROBLEM:")]
        self.assertGreaterEqual(len(problems), 6, out)

    def test_problem_lines_quote_brief_text_as_code(self):
        rc, out = lint(make(assumptions=(a(cat="@team ## x"),)))
        self.assertEqual(rc, 2)
        for line in out.splitlines():
            if "@team" in line:
                self.assertIn("`", line)


class LintNotes(unittest.TestCase):
    def test_note_when_no_critical_problem_or_demand(self):
        rc, out = lint(make(assumptions=(a(cat="solution"),)))
        self.assertEqual(rc, 0, out)
        self.assertIn("NOTE:", out)
        self.assertIn("LINT: ok", out)

    def test_no_demand_note_when_critical_demand(self):
        rc, out = lint(make())
        self.assertFalse(any(l.startswith("NOTE:") and "demand" in l for l in out.splitlines()))

    def test_outward_marker_recognised(self):
        brief = ID.parse_brief(GOOD)
        outward = [e for e in brief["experiments"] if e["outward"]]
        self.assertEqual(len(outward), 1)
        self.assertEqual(outward[0]["ids"], ["A2"])

    def test_lint_missing_file_is_refused(self):
        root = repo()
        self.assertEqual(run(root, "lint", os.path.join(root, "nope.md"))[0], 2)


if __name__ == "__main__":
    unittest.main()
