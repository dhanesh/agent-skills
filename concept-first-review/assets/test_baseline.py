#!/usr/bin/env python3
"""Unit suite for baseline: derive rules from a tree, ask, then write.

The load-bearing property is that a proposal reports the *cost* of each rule —
how many places violate it today. A proposer that suggests rules without saying
what adopting them breaks produces a baseline nobody keeps.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import baseline


def build(root, files):
    for rel, text in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


LAYERED = {
    "core/model.py": "class User:\n    pass\n",
    "core/rules.py": "from core.model import User\n\ndef check(u):\n    return True\n",
    "core/calc.py": "def add(a, b):\n    return a + b\n",
    "web/handlers.py": "from core.model import User\nimport requests\n\ndef get(u):\n    return u\n",
    "web/routes.py": "from core.rules import check\n\ndef route():\n    return check(1)\n",
    "web/render.py": "from core.calc import add\n\ndef render():\n    return add(1, 2)\n",
    "tests/test_web.py": "from web.routes import route\n\ndef test_route():\n    assert route()\n",
    "tests/test_core.py": "from core.calc import add\n\ndef test_add():\n    assert add(1, 1) == 2\n",
    "tests/test_more.py": "from core.rules import check\n\ndef test_check():\n    assert check(1)\n",
}


class TestScan(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        build(self.root, LAYERED)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_finds_areas_and_internal_edges(self):
        ev = baseline.scan(self.root)
        self.assertEqual(ev["files"], len(LAYERED))
        self.assertEqual(set(ev["areas"]), {"core", "web", "tests"})
        self.assertGreaterEqual(ev["edges"].get("web->core", 0), 3)
        self.assertNotIn("core->web", ev["edges"])

    def test_separates_third_party_from_internal(self):
        ev = baseline.scan(self.root)
        self.assertIn("requests", ev["external"])
        self.assertNotIn("core", ev["external"])
        self.assertNotIn("web", ev["external"])

    def test_skips_vendored_and_generated_trees(self):
        build(self.root, {"node_modules/pkg/index.js": "import x from 'y';\n",
                          "dist/bundle.js": "import z from 'w';\n"})
        ev = baseline.scan(self.root)
        self.assertEqual(ev["files"], len(LAYERED))

    def test_measures_the_shapes_already_present(self):
        build(self.root, {"core/wide.py":
                          "def wide(a, b, c, d, e, f, g):\n" + "    x = 1\n" * 40})
        ev = baseline.scan(self.root)
        self.assertGreaterEqual(ev["observed"]["params_max"], 7)
        self.assertGreaterEqual(ev["observed"]["function_rows_max"], 40)

    def test_an_empty_tree_scans_without_raising(self):
        empty = tempfile.mkdtemp()
        try:
            ev = baseline.scan(empty)
            self.assertEqual(ev["files"], 0)
            proposal = baseline.propose(ev)
            self.assertEqual(proposal["layers"], [])
            self.assertTrue(baseline.to_questions(proposal))
        finally:
            shutil.rmtree(empty, ignore_errors=True)


class TestPropose(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        build(self.root, LAYERED)
        self.proposal = baseline.propose(baseline.scan(self.root))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_production_areas_outrank_tests(self):
        self.assertLess(self.proposal["layers"].index("core"),
                        self.proposal["layers"].index("tests"))

    def test_proposes_the_edge_that_costs_nothing(self):
        cands = {(c["from"], c["to"]): c for c in self.proposal["forbidden_edge_candidates"]}
        self.assertIn(("core", "web"), cands)
        self.assertEqual(cands[("core", "web")]["violations_today"], 0)
        self.assertEqual(cands[("core", "web")]["kind"], "one-way")

    def test_every_candidate_reports_what_adopting_it_costs(self):
        for cand in self.proposal["forbidden_edge_candidates"]:
            self.assertIn("violations_today", cand)
            self.assertTrue(cand["evidence"])

    def test_a_cycle_is_reported_once_with_both_counts(self):
        build(self.root, {"core/back.py": "from web.render import render\n\ndef b():\n    return render()\n"})
        proposal = baseline.propose(baseline.scan(self.root))
        cycles = [c for c in proposal["forbidden_edge_candidates"] if c.get("kind") == "cycle"]
        self.assertEqual(len(cycles), 1, [c["evidence"] for c in cycles])
        self.assertGreater(cycles[0]["forward"], 0)
        self.assertGreater(cycles[0]["reverse"], 0)

    def test_a_cycle_outranks_an_obvious_free_rule(self):
        build(self.root, {"core/back.py": "from web.render import render\n\ndef b():\n    return render()\n"})
        proposal = baseline.propose(baseline.scan(self.root))
        first = proposal["forbidden_edge_candidates"][0]
        self.assertEqual(first["kind"], "cycle",
                         "the cycle is the finding; free obvious rules can wait")

    def test_dependency_roots_are_ranked_by_use(self):
        names = [r["name"] for r in self.proposal["external_roots"]]
        self.assertIn("requests", names)


class TestQuestions(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        build(self.root, LAYERED)
        self.proposal = baseline.propose(baseline.scan(self.root))
        self.batches = baseline.to_questions(self.proposal)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_batches_respect_the_question_tool_limits(self):
        for batch in self.batches:
            self.assertLessEqual(len(batch), baseline.MAX_QUESTIONS_PER_BATCH)
            self.assertGreaterEqual(len(batch), 1)
            for q in batch:
                self.assertLessEqual(len(q["header"]), baseline.MAX_HEADER, q["header"])
                self.assertGreaterEqual(len(q["options"]), 2)
                self.assertLessEqual(len(q["options"]), baseline.MAX_OPTIONS)
                self.assertTrue(q["question"].strip().endswith("?"),
                                "every question must read as a question: " + q["question"][-60:])
                for opt in q["options"]:
                    self.assertTrue(opt["label"] and opt["description"])

    def test_every_candidate_layer_is_offered_somewhere(self):
        # A layer that is never offered can never appear in a rule, so splitting
        # across questions beats truncating to the tool's option limit.
        offered = {o["label"] for b in self.batches for q in b
                   for o in q["options"] if q["header"].startswith("Layers")}
        self.assertEqual(offered, set(self.proposal["layers"]))
        for b in self.batches:
            for q in b:
                if q["header"].startswith("Layers"):
                    self.assertTrue(q["multiSelect"])

    def test_many_areas_split_across_layer_questions(self):
        many = dict(self.proposal, layers=["a", "b", "c", "d", "e", "f"],
                    layer_files={k: 5 for k in "abcdef"})
        qs = [q for b in baseline.to_questions(many) for q in b
              if q["header"].startswith("Layers")]
        self.assertEqual(len(qs), 2)
        self.assertTrue(all(len(q["options"]) <= baseline.MAX_OPTIONS for q in qs))
        self.assertTrue(all(len(q["header"]) <= baseline.MAX_HEADER for q in qs))

    def test_a_free_rule_says_so_and_a_costly_one_says_what_it_costs(self):
        text = json.dumps(self.batches)
        self.assertIn("free to adopt", text)


class TestApply(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        build(self.root, LAYERED)
        self.proposal = baseline.propose(baseline.scan(self.root))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a_full_yes_produces_a_usable_baseline(self):
        rules = baseline.apply_answers(self.proposal, {
            "layers": ["core", "web"],
            "edges": {"core->web": True},
            "freeze_dependencies": True,
            "budgets": "observed",
            "invariants": ["core/ is framework-free"],
        })
        self.assertEqual(set(rules["layers"]), {"core", "web"})
        self.assertIn(["core", "web"], rules["forbidden_edges"])
        self.assertIn("requests", rules["allowed_external"])
        self.assertIn("max_params", rules["budgets"])
        self.assertEqual(rules["invariants"], ["core/ is framework-free"])

    def test_the_result_is_readable_by_the_thing_that_consumes_it(self):
        import signals as signalslib
        rules = baseline.apply_answers(self.proposal, {
            "layers": ["core", "web"], "edges": {"core->web": True},
            "freeze_dependencies": True, "budgets": "strict",
        })
        tmp = os.path.join(self.root, "design-rules.json")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rules, fh)
        loaded = signalslib.load_baseline(tmp)
        self.assertTrue(loaded["_configured"])
        self.assertEqual(loaded["forbidden_edges"], [["core", "web"]])
        self.assertEqual(loaded["budgets"]["max_params"], 5)

    def test_silence_adopts_nothing(self):
        rules = baseline.apply_answers(self.proposal, {})
        self.assertEqual(rules["forbidden_edges"], [])
        self.assertEqual(rules["allowed_external"], [])
        self.assertNotIn("budgets", rules)

    def test_an_edge_whose_layer_was_declined_is_dropped_and_reported(self):
        rules = baseline.apply_answers(self.proposal, {
            "layers": ["core"], "edges": {"core->web": True},
        })
        self.assertEqual(rules["forbidden_edges"], [],
                         "a rule cannot name a layer the user did not adopt")
        # Answering a question and getting nothing recorded, silently, is worse
        # than never having asked it.
        self.assertTrue(rules["_dropped"])
        self.assertIn("web", rules["_dropped"][0])

    def test_nothing_is_reported_dropped_when_everything_lands(self):
        rules = baseline.apply_answers(self.proposal, {
            "layers": ["core", "web"], "edges": {"core->web": True},
        })
        self.assertEqual(rules["_dropped"], [])

    def test_an_import_ending_in_a_symbol_still_resolves_internally(self):
        # `pub use executor::GqlExecutor;` names a type, not a directory. Only
        # dropping leading segments left every such re-export looking external.
        build(self.root, {"core/reexport.py": "from core.model import User\n"})
        ev = baseline.scan(self.root)
        self.assertNotIn("core", ev["external"])
        self.assertNotIn("model", ev["external"])

    def test_a_cycle_answer_names_the_direction_to_forbid(self):
        build(self.root, {"core/back.py": "from web.render import render\n\ndef b():\n    return render()\n"})
        proposal = baseline.propose(baseline.scan(self.root))
        cycle = [c for c in proposal["forbidden_edge_candidates"] if c["kind"] == "cycle"][0]
        key = "%s->%s" % (cycle["from"], cycle["to"])
        rules = baseline.apply_answers(proposal, {
            "layers": ["core", "web"], "edges": {key: "web->core"},
        })
        self.assertEqual(rules["forbidden_edges"], [["web", "core"]])

    def test_budgets_track_the_codebase_when_asked_to(self):
        build(self.root, {"core/wide.py":
                          "def wide(a, b, c, d, e, f, g, h):\n" + "    x = 1\n" * 5})
        proposal = baseline.propose(baseline.scan(self.root))
        observed = baseline.apply_answers(proposal, {"budgets": "observed"})["budgets"]
        strict = baseline.apply_answers(proposal, {"budgets": "strict"})["budgets"]
        self.assertGreaterEqual(observed["max_params"], 3)
        self.assertEqual(strict["max_params"], 5)


if __name__ == "__main__":
    unittest.main(verbosity=1)
