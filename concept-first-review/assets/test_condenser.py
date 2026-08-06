#!/usr/bin/env python3
"""Unit suite for condenser: every invariant it enforces.

Each rule gets a positive case (a legitimate plan compiles) and a negative case
(the violating plan is rejected with a message naming the problem). A compiler
that only ever accepts is not a compiler.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import diffmodel
import condenser


SIMPLE = (
    "diff --git a/a.py b/a.py\n"      # 1
    "--- a/a.py\n"                     # 2
    "+++ b/a.py\n"                     # 3
    "@@ -1,3 +1,8 @@\n"                # 4
    " def route(request):\n"           # 5
    "+    resp.user_id = rd.user_id\n"  # 6
    "+    resp.box_id = rd.box_id\n"    # 7
    "+    resp.name = rd.name\n"        # 8
    "+    if rd.key != want:\n"         # 9
    "+        raise Failure('route key = %s, want %s' % (rd.key, want))\n"  # 10
    "+    return resp\n"                # 11
)


def compile_plan(text, plan):
    rows = diffmodel.parse_diff(text)
    return condenser.compile_plan(
        text, plan, rows=rows,
        declarations=diffmodel.declaration_rows(rows),
        moves=diffmodel.detect_relocations(rows),
    )


EMPTY = {"edits": []}


def drop(spec):
    return {"op": "drop", "lines": spec}


def collapse(spec):
    return {"op": "collapse", "lines": spec}


def trim(line, frm, to):
    return {"op": "trim", "line": line, "from": frm, "to": to}


def plan(*edits):
    return {"edits": list(edits)}


class TestRendering(unittest.TestCase):
    def test_identity_plan_returns_the_diff(self):
        res = compile_plan(SIMPLE, EMPTY)
        self.assertTrue(res.ok, res.errors)
        self.assertEqual(res.condensed, SIMPLE)
        self.assertEqual(res.stats["total_rows"], 6)
        self.assertEqual(res.stats["kept_rows"], 6)

    def test_drop_collapse_and_trim_together(self):
        res = compile_plan(SIMPLE, plan(
            drop("8"),
            collapse("6-7"),
            trim(10, "'route key = %s, want %s' % (rd.key, want)", "..."),
        ))
        self.assertTrue(res.ok, res.errors)
        self.assertIn("+    ...\n", res.condensed)
        self.assertIn("raise Failure(...)", res.condensed)
        self.assertNotIn("resp.name", res.condensed)
        self.assertEqual(res.stats["kept_rows"], 4)

    def test_collapse_placeholder_takes_the_block_indentation(self):
        res = compile_plan(SIMPLE, plan(collapse("9-10")))
        self.assertTrue(res.ok, res.errors)
        self.assertIn("+    ...\n", res.condensed)

    def test_emptying_a_hunk_drops_its_headers(self):
        res = compile_plan(SIMPLE, plan(drop("5-11")))
        self.assertTrue(res.ok, res.errors)
        self.assertEqual(res.condensed, "")
        self.assertEqual(res.stats["kept_files"], 0)

    def test_headers_survive_even_when_the_plan_drops_them(self):
        res = compile_plan(SIMPLE, plan(drop("1-4")))
        self.assertTrue(res.ok, res.errors)
        self.assertIn("diff --git a/a.py b/a.py", res.condensed)
        self.assertIn("@@ -1,3 +1,8 @@", res.condensed)


class TestTrim(unittest.TestCase):
    def test_valid_and_invalid_shapes(self):
        self.assertTrue(condenser.is_trim_of("resp.SSHKeyID = rd.sshKeyID", "resp.SSHKeyID = rd..."))
        self.assertTrue(condenser.is_trim_of("t.Errorf(\"a %d b\", x)", "t.Errorf(…)"))
        self.assertFalse(condenser.is_trim_of("a = b", "a = b"))            # no change
        self.assertFalse(condenser.is_trim_of("a = b", "a = c"))            # invented char
        self.assertFalse(condenser.is_trim_of("a = bcd", "a = bd"))         # silent deletion
        self.assertFalse(condenser.is_trim_of("a = b", "a = b  # noise"))   # invented comment

    def test_rejects_invented_text(self):
        res = compile_plan(SIMPLE, plan(trim(11, "return resp", "return cached")))
        self.assertFalse(res.ok)
        self.assertIn("not a trim of", res.errors[0])

    def test_rejects_ambiguous_from_text(self):
        text = SIMPLE.replace("+    return resp\n", "+    x = a.b + a.b\n")
        res = compile_plan(text, plan(trim(11, "a.b", "a...")))
        self.assertFalse(res.ok)
        self.assertIn("appears 2 times", res.errors[0])

    def test_rejects_from_text_not_present(self):
        res = compile_plan(SIMPLE, plan(trim(11, "nope", "n...")))
        self.assertFalse(res.ok)
        self.assertIn("is not on line 11", res.errors[0])

    def test_rejects_trim_on_a_hunk_header(self):
        res = compile_plan(SIMPLE, plan(trim(4, "-1,3", "-...")))
        self.assertFalse(res.ok)
        self.assertIn("only a +, -, or context row can be trimmed", res.errors[0])


class TestCollapseRules(unittest.TestCase):
    def test_rejects_single_row_collapse(self):
        res = compile_plan(SIMPLE, plan(collapse("6")))
        self.assertFalse(res.ok)
        self.assertIn("at least two adjacent rows", res.errors[0])

    def test_rejects_mixed_markers(self):
        res = compile_plan(SIMPLE, plan(collapse("5-7")))
        self.assertFalse(res.ok)
        self.assertIn("mixes diff markers", " ".join(res.errors))

    def test_rejects_collapse_over_metadata(self):
        res = compile_plan(SIMPLE, plan(collapse("3-5")))
        self.assertFalse(res.ok)
        self.assertIn("hunk header or file metadata", " ".join(res.errors))

    def test_rejects_out_of_range(self):
        res = compile_plan(SIMPLE, plan(drop("40-41")))
        self.assertFalse(res.ok)
        self.assertIn("outside the diff", res.errors[0])

    def test_rejects_collapse_leaving_its_hunk(self):
        text = SIMPLE + "@@ -20,2 +20,3 @@\n+    a = one()\n+    b = two()\n"
        res = compile_plan(text, plan(collapse("10-13")))
        self.assertFalse(res.ok)
        self.assertIn("stay inside one hunk", " ".join(res.errors))


DEPENDENCY_MIX = (
    "diff --git a/m.py b/m.py\n"   # 1
    "--- a/m.py\n"                  # 2
    "+++ b/m.py\n"                  # 3
    "@@ -1,4 +1,6 @@\n"             # 4
    "+import os\n"                   # 5
    "+import sys\n"                  # 6
    "+value = os.getcwd()\n"         # 7
    "+other = sys.argv[1]\n"         # 8
)


class TestDependencyRows(unittest.TestCase):
    def test_dependency_rows_vanish_from_an_empty_plan(self):
        res = compile_plan(DEPENDENCY_MIX, EMPTY)
        self.assertTrue(res.ok, res.errors)
        self.assertNotIn("import os", res.condensed)
        self.assertIn("value = os.getcwd()", res.condensed)
        self.assertEqual(res.stats["declaration_rows"], 2)

    def test_rejects_a_collapse_spanning_dependencies_and_code(self):
        res = compile_plan(DEPENDENCY_MIX, plan(collapse("6-7")))
        self.assertFalse(res.ok)
        self.assertIn("spans both dependency rows", " ".join(res.errors))

    def test_coordinates_wasted_on_dependencies_are_a_note_not_an_error(self):
        res = compile_plan(DEPENDENCY_MIX, plan(drop("5-6")))
        self.assertTrue(res.ok, res.errors)
        self.assertTrue(any("come out anyway" in n for n in res.notes))


MOVE_TEXT = (
    "diff --git a/old.py b/old.py\n"   # 1
    "--- a/old.py\n"                    # 2
    "+++ b/old.py\n"                    # 3
    "@@ -1,5 +1,2 @@\n"                 # 4
    " def handler(request):\n"          # 5
    "-    config_filters = config.getini('filterwarnings')\n"        # 6
    "-    apply_warning_filters(config_filters, cmdline_filters)\n"  # 7
    "-    record = build_audit_record(request, config_filters)\n"    # 8
    "+    return delegate(request)\n"   # 9
    "diff --git a/new.py b/new.py\n"    # 10
    "--- a/new.py\n"                    # 11
    "+++ b/new.py\n"                    # 12
    "@@ -1,2 +1,5 @@\n"                 # 13
    " def delegate(request):\n"         # 14
    "+        config_filters = config.getini('filterwarnings')\n"        # 15
    "+        apply_warning_filters(config_filters, cmdline_filters)\n"  # 16
    "+        record = build_audit_record(request, config_filters)\n"    # 17
)


class TestRelocationSymmetry(unittest.TestCase):
    def test_matching_collapses_at_both_ends_are_accepted(self):
        res = compile_plan(MOVE_TEXT, plan(collapse("6-8"), collapse("15-17")))
        self.assertTrue(res.ok, res.errors)
        self.assertIn("-    ...\n", res.condensed)
        self.assertIn("+        ...\n", res.condensed)

    def test_one_sided_collapse_is_rejected(self):
        res = compile_plan(MOVE_TEXT, plan(collapse("15-17")))
        self.assertFalse(res.ok)
        self.assertIn("edited differently", " ".join(res.errors))

    def test_one_sided_drop_is_rejected(self):
        res = compile_plan(MOVE_TEXT, plan(drop("6-8")))
        self.assertFalse(res.ok)
        self.assertIn("presents as a move", " ".join(res.errors))

    def test_symmetric_drop_is_accepted(self):
        res = compile_plan(MOVE_TEXT, plan(drop("6-8"), drop("15-17")))
        self.assertTrue(res.ok, res.errors)


DEP_TEXT = (
    "diff --git a/t.py b/t.py\n"   # 1
    "--- a/t.py\n"                  # 2
    "+++ b/t.py\n"                  # 3
    "@@ -1,6 +1,10 @@\n"            # 4
    "+CASES = [\n"                   # 5
    "+    ('empty', '', None),\n"    # 6
    "+    ('simple', 'a', 'a'),\n"   # 7
    "+]\n"                           # 8
    "+def test_parse(name, raw):\n"  # 9
    "+    assert parse(raw) in CASES\n"  # 10
)


class TestDependencyGuard(unittest.TestCase):
    def test_rejects_burying_a_definition_a_surviving_row_uses(self):
        res = compile_plan(DEP_TEXT, plan(collapse("5-8")))
        self.assertFalse(res.ok)
        self.assertIn("buries the definition of `CASES`", " ".join(res.errors))

    def test_negative_a_match_arm_is_not_a_definition(self):
        # `Expression::ListAccess { .. } => {` once read as an assignment to
        # `Expression`, so any collapse containing a match arm was rejected.
        text = (
            "diff --git a/e.rs b/e.rs\n--- a/e.rs\n+++ b/e.rs\n@@ -1,4 +1,8 @@\n"
            "+            Expression::ListAccess { list, index } => {\n"
            "+                let items = evaluate(list)?;\n"
            "+                items.get(index)\n"
            "+            }\n"
            "+    let other = Expression::Literal(1);\n"
        )
        res = compile_plan(text, plan(collapse("5-8")))
        self.assertTrue(res.ok, res.errors)

    def test_collapsing_only_the_interior_is_accepted(self):
        res = compile_plan(DEP_TEXT, plan(collapse("6-7")))
        self.assertTrue(res.ok, res.errors)
        self.assertIn("+CASES = [", res.condensed)


class TestPythonBlockRules(unittest.TestCase):
    def test_rejects_a_collapse_consuming_a_decorator(self):
        text = (
            "diff --git a/v.py b/v.py\n--- a/v.py\n+++ b/v.py\n@@ -1,4 +1,7 @@\n"
            "+@pytest.mark.parametrize('n', [1, 2])\n"
            "+def test_thing(n):\n"
            "+    assert n\n"
        )
        res = compile_plan(text, plan(collapse("5-7")))
        self.assertFalse(res.ok)
        self.assertIn("decorator", " ".join(res.errors))

    def test_rejects_a_collapse_consuming_the_block_header(self):
        text = (
            "diff --git a/v.py b/v.py\n--- a/v.py\n+++ b/v.py\n@@ -1,4 +1,7 @@\n"
            "+def build(cfg):\n"
            "+    a = 1\n"
            "+    return a\n"
        )
        res = compile_plan(text, plan(collapse("5-7")))
        self.assertFalse(res.ok)
        self.assertIn("block header", " ".join(res.errors))

    def test_collapsing_the_body_below_the_header_is_accepted(self):
        text = (
            "diff --git a/v.py b/v.py\n--- a/v.py\n+++ b/v.py\n@@ -1,4 +1,7 @@\n"
            "+def build(cfg):\n"
            "+    a = 1\n"
            "+    b = 2\n"
        )
        res = compile_plan(text, plan(collapse("6-7")))
        self.assertTrue(res.ok, res.errors)

    def test_rejects_breaking_triple_quote_parity(self):
        text = (
            "diff --git a/v.py b/v.py\n--- a/v.py\n+++ b/v.py\n@@ -1,5 +1,9 @@\n"
            '+DOC = """\n'
            "+first line\n"
            "+second line\n"
            '+"""\n'
        )
        res = compile_plan(text, plan(drop("8")))
        self.assertFalse(res.ok)
        self.assertIn("triple-quote parity", " ".join(res.errors))


REPLACEMENT = (
    "diff --git a/e.rs b/e.rs\n"   # 1
    "--- a/e.rs\n"                  # 2
    "+++ b/e.rs\n"                  # 3
    "@@ -1,6 +1,4 @@\n"             # 4
    " fn scan(&self) {\n"           # 5
    "-    let mut all = Vec::new();\n"                # 6
    "-    for id in 1..=1000 {\n"                     # 7
    "-        if let Ok(Some(_)) = self.get(id) {\n"  # 8
    "-            all.push(id);\n"                    # 9
    "-        }\n"                                    # 10
    "-    }\n"                                        # 11
    "+    let all = self.all_node_ids();\n"           # 12
    "+    let filtered = self.filter(all);\n"         # 13
)


class TestOneSidedReplacement(unittest.TestCase):
    """A replacement condensed to one side reads as a deletion with no successor.

    Found by redrafting a real review: a denser plan kept `for id in 1..=1000`
    and hid `all_node_ids()`, so the fix vanished and only the bug remained.
    """

    def test_hiding_the_added_side_is_flagged(self):
        res = compile_plan(REPLACEMENT, plan(collapse("12-13")))
        self.assertTrue(res.ok, res.errors)
        self.assertFalse(any("only its" in n for n in res.notes),
                         "a collapse still leaves a placeholder row, so a side survives")
        res = compile_plan(REPLACEMENT, plan(drop("12-13")))
        self.assertTrue(res.ok, res.errors)
        self.assertTrue(any("only its removed side survives" in n for n in res.notes), res.notes)

    def test_hiding_the_removed_side_is_flagged(self):
        res = compile_plan(REPLACEMENT, plan(drop("6-11")))
        self.assertTrue(res.ok, res.errors)
        self.assertTrue(any("only its added side survives" in n for n in res.notes), res.notes)

    def test_negative_keeping_an_anchor_from_both_sides_is_quiet(self):
        res = compile_plan(REPLACEMENT, plan(drop("6-10"), drop("13")))
        self.assertTrue(res.ok, res.errors)
        self.assertFalse(any("only its" in n for n in res.notes), res.notes)

    def test_negative_a_pure_addition_has_no_other_side_to_strand(self):
        res = compile_plan(SIMPLE, plan(drop("6-11")))
        self.assertTrue(res.ok, res.errors)
        self.assertFalse(any("only its" in n for n in res.notes), res.notes)


class TestPlanShape(unittest.TestCase):
    def test_non_object_plan_is_rejected_cleanly(self):
        res = compile_plan(SIMPLE, ["drop"])
        self.assertFalse(res.ok)
        self.assertIn("expected a JSON object", res.errors[0])

    def test_wrong_types_are_reported_not_raised(self):
        res = compile_plan(SIMPLE, {"edits": "6-8"})
        self.assertFalse(res.ok)
        self.assertIn("must be an array", res.errors[0])

    def test_range_parsing_accepts_both_forms(self):
        self.assertEqual(condenser.parse_range("14-17"), (14, 17))
        self.assertEqual(condenser.parse_range(" 14 - 17 "), (14, 17))
        self.assertEqual(condenser.parse_range("14"), (14, 14))
        self.assertEqual(condenser.parse_range(14), (14, 14))
        self.assertIsNone(condenser.parse_range("17-14"))
        self.assertIsNone(condenser.parse_range("0"))
        self.assertIsNone(condenser.parse_range("a-b"))
        self.assertIsNone(condenser.parse_range(None))

    def test_missing_edits_array_is_reported(self):
        res = compile_plan(SIMPLE, {"headline": "nothing here"})
        self.assertFalse(res.ok)
        self.assertIn("missing the `edits` array", res.errors[0])

    def test_unknown_op_names_the_valid_ops(self):
        res = compile_plan(SIMPLE, plan({"op": "summarise", "lines": "6-7"}))
        self.assertFalse(res.ok)
        self.assertIn('expected "drop", "collapse", or "trim"', res.errors[0])

    def test_trim_without_string_fields_is_reported(self):
        res = compile_plan(SIMPLE, plan({"op": "trim", "line": 10, "from": 5, "to": "..."}))
        self.assertFalse(res.ok)
        self.assertIn("string `from` and `to`", res.errors[0])

    def test_edits_apply_regardless_of_their_order_in_the_array(self):
        forward = compile_plan(SIMPLE, plan(collapse("6-7"), drop("8")))
        reverse = compile_plan(SIMPLE, plan(drop("8"), collapse("6-7")))
        self.assertTrue(forward.ok and reverse.ok)
        self.assertEqual(forward.condensed, reverse.condensed)

    def test_missing_coordinates_are_reported(self):
        res = compile_plan(SIMPLE, {"edits": [{"op": "drop", "start": 6}]})
        self.assertFalse(res.ok)
        self.assertIn("`lines` must be", res.errors[0])

    def test_feedback_names_density_and_rejections(self):
        res = compile_plan(SIMPLE, plan(collapse("6")))
        text = condenser.feedback(res)
        self.assertIn("density:", text)
        self.assertIn("REJECTED:", text)

    def test_truncate_marks_the_cut(self):
        out = condenser.truncate("x" * 100, limit=10)
        self.assertTrue(out.startswith("x" * 10))
        self.assertIn("preview truncated", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
