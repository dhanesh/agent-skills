#!/usr/bin/env python3
"""Tests for the node stack: heuristic discovery, and every interface name.

The governing bar, from the multi-stack design: static triage is a FILTER and
the runtime guard is the enforcement, so this reader may be wrong in the
direction of MORE reported work and never in the direction of less. Each
negative below therefore pins one of two different things -- a form that must
NOT be read as an export (a phantom that would waste a person's attention), or
a form that must not silently disappear (a unit whose missing test is the bug).
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


stack_node = _load("stack_node")
rank_risk = _load("rank_risk")


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class NodeCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tsn-node-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def ids(self):
        units, mode = stack_node.discover_units(self.root)
        self.assertEqual(mode, "heuristic")
        return [u["id"] for u in units]

    def units_by_name(self):
        units, _ = stack_node.discover_units(self.root)
        return {u["name"]: u for u in units}


# ── The export forms ─────────────────────────────────────────────────────

class TestExportForms(NodeCase):
    def test_finds_exported_function_declaration(self):
        write(self.root, "src/util.js",
              "export function parse(s) {\n  return JSON.parse(s);\n}\n")
        units, mode = stack_node.discover_units(self.root)
        self.assertEqual([u["id"] for u in units], ["src/util.js::parse"])
        self.assertEqual(mode, "heuristic")
        self.assertEqual(units[0]["kind"], "function")
        self.assertEqual(units[0]["lineno"], 1)

    def test_export_default_function(self):
        write(self.root, "src/a.js", "\nexport default function render(x) {\n  return x;\n}\n")
        u = self.units_by_name()["render"]
        self.assertEqual((u["kind"], u["lineno"]), ("function", 2))

    def test_export_const_arrow(self):
        write(self.root, "src/a.js", "export const parse = (s) => s.trim();\n")
        u = self.units_by_name()["parse"]
        self.assertEqual(u["kind"], "function")

    def test_export_const_async_arrow_with_default_arg(self):
        write(self.root, "src/a.js",
              "export const load = async (p = fallback(1, 2)) => {\n  return p;\n};\n")
        self.assertEqual(self.units_by_name()["load"]["kind"], "function")

    def test_export_const_function_expression(self):
        write(self.root, "src/a.js", "export const parse = function (s) { return s; };\n")
        self.assertEqual(self.units_by_name()["parse"]["kind"], "function")

    def test_export_class(self):
        write(self.root, "src/a.ts", "export class Ledger {\n  add() {}\n}\n")
        u = self.units_by_name()["Ledger"]
        self.assertEqual((u["kind"], u["lineno"]), ("class", 1))

    def test_export_abstract_and_default_class(self):
        write(self.root, "src/a.ts", "export abstract class Base {}\n")
        write(self.root, "src/b.ts", "export default class Impl {}\n")
        self.assertEqual(self.ids(), ["src/a.ts::Base", "src/b.ts::Impl"])
        self.assertEqual(self.units_by_name()["Impl"]["kind"], "class")

    def test_export_async_and_generator_functions(self):
        write(self.root, "src/a.js",
              "export async function fetchAll() {}\nexport function* walk() {}\n")
        self.assertEqual(sorted(self.units_by_name()), ["fetchAll", "walk"])

    def test_export_list_takes_the_exported_name_and_the_definition_line(self):
        write(self.root, "src/a.js",
              "function parse(s) { return s; }\n"
              "class Fmt {}\n"
              "\n"
              "export { parse, Fmt as Formatter };\n")
        by = self.units_by_name()
        self.assertEqual(sorted(by), ["Formatter", "parse"])
        self.assertEqual((by["parse"]["kind"], by["parse"]["lineno"]), ("function", 1))
        self.assertEqual((by["Formatter"]["kind"], by["Formatter"]["lineno"]), ("class", 2))

    def test_module_exports_object_literal(self):
        write(self.root, "src/a.js",
              "function parse(s) { return s; }\n"
              "module.exports = { parse, format: (x) => x, VERSION };\n")
        by = self.units_by_name()
        self.assertEqual(sorted(by), ["VERSION", "format", "parse"])
        self.assertEqual(by["parse"]["lineno"], 1)

    def test_module_exports_property(self):
        write(self.root, "src/a.js", "module.exports.parse = function (s) { return s; };\n")
        self.assertEqual(self.ids(), ["src/a.js::parse"])

    def test_exports_property_referencing_a_local_class(self):
        write(self.root, "src/a.js", "class Ledger {}\nexports.Ledger = Ledger;\n")
        self.assertEqual(self.units_by_name()["Ledger"]["kind"], "class")

    def test_typescript_annotation_containing_an_arrow(self):
        write(self.root, "src/a.ts",
              "export const parse: (s: string) => number = (s) => Number(s);\n")
        self.assertEqual(self.units_by_name()["parse"]["kind"], "function")

    def test_factory_call_counts_as_a_unit(self):
        # Deliberate over-report: `makeRouter()` is how a factory-produced
        # function is written, and a data object built the same way costs a
        # glance where a missed function costs the bug.
        write(self.root, "src/a.js", "export const router = makeRouter({ strict: true });\n")
        self.assertEqual(self.ids(), ["src/a.js::router"])

    def test_dollar_named_export_is_discovered(self):
        write(self.root, "src/a.js", "export function $fetch(u) { return u; }\n")
        self.assertEqual(self.ids(), ["src/a.js::$fetch"])

    def test_every_source_extension_is_read(self):
        for ext in ("js", "mjs", "cjs", "jsx", "ts", "tsx", "mts", "cts"):
            write(self.root, "src/f_%s.%s" % (ext, ext),
                  "export function f_%s() {}\n" % ext)
        self.assertEqual(len(self.ids()), 8)

    def test_underscore_prefixed_export_is_kept(self):
        # Python drops `_name` because the underscore IS the visibility rule
        # there. JS states its surface with `export`, so dropping it here
        # would lose a unit that is genuinely public.
        write(self.root, "src/a.js", "export function _internal() {}\n")
        self.assertEqual(self.ids(), ["src/a.js::_internal"])


# ── The negatives ────────────────────────────────────────────────────────

class TestNegatives(NodeCase):
    def test_unexported_function_is_not_a_unit(self):
        write(self.root, "src/a.js", "function helper(x) { return x; }\n")
        self.assertEqual(self.ids(), [])

    def test_name_inside_a_line_comment(self):
        write(self.root, "src/a.js", "// export function ghost() {}\nconst x = 1;\n")
        self.assertEqual(self.ids(), [])

    def test_name_inside_a_block_comment(self):
        write(self.root, "src/a.js", "/*\n export function ghost() {}\n*/\nconst x = 1;\n")
        self.assertEqual(self.ids(), [])

    def test_name_inside_a_string(self):
        write(self.root, "src/a.js", 'const src = "export function ghost() {}";\n')
        self.assertEqual(self.ids(), [])

    def test_name_inside_a_template_literal(self):
        write(self.root, "src/a.js",
              "const tpl = `\n  export function ghost() {}\n`;\nexport function real() {}\n")
        self.assertEqual(self.ids(), ["src/a.js::real"])

    def test_template_interpolation_does_not_swallow_the_rest_of_the_file(self):
        write(self.root, "src/a.js",
              'const t = `a ${ obj["`"] } b`;\nexport function real() {}\n')
        self.assertEqual(self.ids(), ["src/a.js::real"])

    def test_double_slash_in_a_url_is_not_a_comment(self):
        write(self.root, "src/a.js",
              'const base = "https://example.com/x";\nexport function real() {}\n')
        self.assertEqual(self.ids(), ["src/a.js::real"])

    def test_regex_literal_containing_a_quote_and_a_slash(self):
        write(self.root, "src/a.js",
              "const re = /['\"]\\/\\//g;\nexport function real() {}\n")
        self.assertEqual(self.ids(), ["src/a.js::real"])

    def test_division_is_not_read_as_a_regex(self):
        write(self.root, "src/a.js",
              "const ratio = a / b;\nconst other = c / d;\nexport function real() {}\n")
        self.assertEqual(self.ids(), ["src/a.js::real"])

    def test_nested_function_inside_an_exported_one(self):
        write(self.root, "src/a.js",
              "export function outer(x) {\n"
              "  function inner(y) { return y; }\n"
              "  const helper = () => 1;\n"
              "  return inner(x) + helper();\n"
              "}\n")
        self.assertEqual(self.ids(), ["src/a.js::outer"])

    def test_export_inside_a_nested_block_is_not_top_level(self):
        write(self.root, "src/a.js",
              "if (flag) {\n  module.exports.ghost = () => 1;\n}\n")
        self.assertEqual(self.ids(), [])

    def test_type_only_exports_are_not_units(self):
        write(self.root, "src/a.ts",
              "export type Parsed = { a: number };\n"
              "export interface Reader { read(): string }\n"
              "export declare function ambient(x: number): number;\n"
              "export enum Color { Red }\n")
        self.assertEqual(self.ids(), [])

    def test_re_exports_are_left_to_the_file_that_defines_them(self):
        write(self.root, "src/a.js", "export { parse } from './b.js';\nexport * from './c.js';\n")
        write(self.root, "src/b.js", "export function parse() {}\n")
        self.assertEqual(self.ids(), ["src/b.js::parse"])

    def test_exported_constant_is_not_a_unit(self):
        write(self.root, "src/a.js",
              "export const MAX = 5;\n"
              "export const NAMES = ['a', 'b'];\n"
              "export const CONF = { retries: 2 };\n")
        self.assertEqual(self.ids(), [])

    def test_test_files_are_not_scanned_for_units(self):
        write(self.root, "src/a.test.js", "export function ghost() {}\n")
        write(self.root, "src/b.spec.ts", "export function ghost2() {}\n")
        write(self.root, "src/__tests__/c.ts", "export function ghost3() {}\n")
        write(self.root, "test/d.ts", "export function ghost4() {}\n")
        self.assertEqual(self.ids(), [])

    def test_node_modules_is_skipped(self):
        write(self.root, "node_modules/left-pad/index.js", "export function leftPad() {}\n")
        write(self.root, "src/a.js", "export function real() {}\n")
        self.assertEqual(self.ids(), ["src/a.js::real"])

    def test_declaration_files_are_skipped(self):
        write(self.root, "src/a.d.ts", "export declare function ambient(): void;\n")
        write(self.root, "src/a.js", "export function real() {}\n")
        self.assertEqual(self.ids(), ["src/a.js::real"])

    def test_a_file_of_only_comments_and_strings_yields_nothing(self):
        write(self.root, "src/a.js",
              "// nothing here\n/* export class Ghost {} */\nconst s = 'export const g = () => 1';\n")
        self.assertEqual(self.ids(), [])


# ── The stripper ─────────────────────────────────────────────────────────

class TestStripper(unittest.TestCase):
    def test_offsets_and_lines_are_preserved(self):
        src = "const a = 1; // c\n/* b */ const t = `x${ y }z`;\n"
        out = stack_node.strip_noncode(src)
        self.assertEqual(len(out), len(src))
        self.assertEqual(out.count("\n"), src.count("\n"))
        for i, ch in enumerate(src):
            if ch == "\n":
                self.assertEqual(out[i], "\n")

    def test_code_outside_literals_survives(self):
        out = stack_node.strip_noncode("export function f() { return '//x'; }\n")
        self.assertIn("export function f()", out)
        self.assertNotIn("//x", out)

    def test_interpolated_code_stays_visible(self):
        out = stack_node.strip_noncode("const t = `a ${ compute(1) } b`;")
        self.assertIn("compute(1)", out)
        self.assertNotIn("a ", out.split("compute")[0].replace("const t = ", ""))

    def test_unterminated_string_stops_at_the_newline(self):
        # Wrong in the visible-text direction on purpose: a phantom unit, not
        # a swallowed file.
        out = stack_node.strip_noncode("const s = 'oops\nexport function real() {}\n")
        self.assertIn("export function real()", out)


# ── The rest of the interface ────────────────────────────────────────────

class TestInterfaceNames(NodeCase):
    def test_supplies_every_interface_name_except_triage(self):
        for attr in ("STACK_NAME", "matches", "iter_source_files", "is_test_path",
                     "is_test_for", "module_of", "name_pattern", "path_pattern",
                     "IDENTIFIER_RE", "preceding_qualifier", "module_bindings",
                     "reached_through_module", "discover_units"):
            self.assertTrue(hasattr(stack_node, attr), attr)
        self.assertEqual(stack_node.STACK_NAME, "node")
        # `triage` is Task 4's, and registering a stack without it would raise
        # inside `rank()` for every repo this stack claims.
        self.assertFalse(hasattr(stack_node, "triage"))
        self.assertNotIn(stack_node, rank_risk.STACKS)

    def test_matches_on_a_manifest_and_on_bare_sources(self):
        self.assertFalse(stack_node.matches(self.root))
        write(self.root, "package.json", "{}\n")
        self.assertTrue(stack_node.matches(self.root))
        other = tempfile.mkdtemp(prefix="tsn-node-")
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        write(other, "scripts/build.mjs", "export function build() {}\n")
        self.assertTrue(stack_node.matches(other))

    def test_matches_ignores_node_modules(self):
        write(self.root, "node_modules/x/index.js", "export function y() {}\n")
        self.assertFalse(stack_node.matches(self.root))

    def test_is_test_path(self):
        for rel in ("src/a.test.js", "src/a.spec.ts", "src/a-test.jsx", "src/a_test.ts",
                    "src/__tests__/a.ts", "test/a.ts", "tests/a.ts", "e2e/a.ts",
                    "cypress/a.ts", "spec/a.ts", "test.js"):
            self.assertTrue(stack_node.is_test_path(rel), rel)
        for rel in ("src/a.ts", "src/latest.ts", "src/contest.js", "src/__mocks__/fs.js",
                    "src/testing.ts", "lib/protest.mjs"):
            self.assertFalse(stack_node.is_test_path(rel), rel)

    def test_module_of_and_index_files(self):
        self.assertEqual(stack_node.module_of("src/utils.ts"), "utils")
        self.assertEqual(stack_node.module_of("src/foo/index.ts"), "foo")
        self.assertEqual(stack_node.module_of("index.js"), "index")

    def test_name_pattern_sees_a_dollar_name(self):
        # The whole reason `name_pattern` joined the interface: `\b` does not
        # treat `$` as an identifier character, so the core's old pattern
        # matched nothing here and the unit read as uncovered forever.
        self.assertTrue(stack_node.name_pattern("$fetch").search("$fetch(1)"))
        self.assertIsNone(stack_node.name_pattern("fetch").search("$fetch(1)"))
        self.assertIsNone(stack_node.name_pattern("parse").search("parseAll(1)"))

    def test_path_pattern_bounds_and_index_collapse(self):
        p = stack_node.path_pattern("src/utils.ts")
        self.assertTrue(p.search('from "../src/utils"'))
        self.assertIsNone(p.search('from "../mysrc/utils"'))
        self.assertEqual(stack_node.path_pattern("src/foo/index.ts").pattern,
                         stack_node.path_pattern("src/foo.ts").pattern)
        self.assertIsNone(stack_node.path_pattern("utils.ts"))

    def test_identifier_re_and_qualifiers(self):
        self.assertEqual(stack_node.IDENTIFIER_RE.findall("2x + $a.b"), ["$a", "b"])
        text = "utils.parse(1)"
        self.assertEqual(stack_node.preceding_qualifier(text, text.index("parse")), "utils")
        opt = "utils?.parse(1)"
        self.assertEqual(stack_node.preceding_qualifier(opt, opt.index("parse")), "utils")
        anon = 'make().parse(1)'
        self.assertEqual(stack_node.preceding_qualifier(anon, anon.index("parse")), "")
        self.assertIsNone(stack_node.preceding_qualifier("parse(1)", 0))


class TestBindingGrammar(unittest.TestCase):
    def bindings(self, text, spec_src="src/utils.ts", ref="src/__tests__/utils.test.ts"):
        return stack_node.module_bindings("utils", text, src_rel=spec_src, ref_rel=ref)

    def test_named_default_and_namespace_imports(self):
        self.assertEqual(self.bindings('import { parse, format as fmt } from "../utils";'),
                         ((), ("fmt", "parse")))
        self.assertEqual(self.bindings('import utils from "../utils";'), (("utils",), ()))
        self.assertEqual(self.bindings('import * as U from "../utils.js";'), (("U",), ()))
        self.assertEqual(self.bindings('const { parse } = require("../utils");'),
                         ((), ("parse",)))
        self.assertEqual(self.bindings('const U = require("../utils");'), (("U",), ()))

    def test_index_file_specifier_resolves_to_its_directory(self):
        self.assertEqual(
            stack_node.module_bindings("utils", 'import { parse } from "../utils";',
                                       src_rel="src/utils/index.ts",
                                       ref_rel="src/__tests__/utils.test.ts"),
            ((), ("parse",)))

    def test_a_commented_out_import_binds_nothing(self):
        self.assertEqual(self.bindings('// import { parse } from "../utils";'), ((), ()))

    def test_relative_specifier_must_resolve_to_this_file(self):
        # Without the file context the interface now passes, both of these
        # would match on the specifier's last segment alone.
        self.assertEqual(
            stack_node.module_bindings("utils", 'import { parse } from "../src/utils";',
                                       src_rel="lib/utils.ts", ref_rel="test/utils.test.ts"),
            ((), ()))
        self.assertEqual(
            stack_node.module_bindings("utils", 'import { parse } from "../src/utils";',
                                       src_rel="src/utils.ts", ref_rel="test/utils.test.ts"),
            ((), ("parse",)))

    def test_reached_through_module_needs_a_call_site(self):
        kw = dict(src_rel="src/utils.ts", ref_rel="src/utils.test.ts")
        called = 'import { parse } from "./utils";\nparse("x");\n'
        self.assertTrue(stack_node.reached_through_module("utils", "parse", called, **kw))
        stubbed = 'import { parse } from "./utils";\njest.mock("./utils");\n'
        self.assertFalse(stack_node.reached_through_module("utils", "parse", stubbed, **kw))
        aliased = 'import * as U from "./utils";\nU.parse("x");\n'
        self.assertTrue(stack_node.reached_through_module("utils", "parse", aliased, **kw))
        optional = 'import * as U from "./utils";\nU?.parse("x");\n'
        self.assertTrue(stack_node.reached_through_module("utils", "parse", optional, **kw))
        constructed = 'import { Ledger } from "./utils";\nnew Ledger();\n'
        self.assertTrue(stack_node.reached_through_module("utils", "Ledger", constructed, **kw))
        commented = 'import { parse } from "./utils";\n// parse("x")\n'
        self.assertFalse(stack_node.reached_through_module("utils", "parse", commented, **kw))
        unimported = 'parse("x");\n'
        self.assertFalse(stack_node.reached_through_module("utils", "parse", unimported, **kw))


class TestIsTestFor(unittest.TestCase):
    def test_the_three_node_idioms(self):
        self.assertTrue(stack_node.is_test_for("src/utils.test.ts", "src/utils.ts"))
        self.assertTrue(stack_node.is_test_for("src/__tests__/utils.test.ts", "src/utils.ts"))
        self.assertTrue(stack_node.is_test_for("test/a/utils.test.ts", "src/a/utils.ts"))
        self.assertTrue(stack_node.is_test_for("src/foo/index.test.ts", "src/foo/index.ts"))

    def test_a_test_that_names_another_module_is_not_its_test(self):
        self.assertFalse(stack_node.is_test_for("src/other.test.ts", "src/utils.ts"))

    def test_a_test_in_another_package_is_not_its_test(self):
        self.assertFalse(stack_node.is_test_for("packages/a/src/__tests__/utils.test.ts",
                                                "packages/b/src/utils.ts"))

    def test_an_unnamed_test_file_claims_no_subject(self):
        self.assertFalse(stack_node.is_test_for("src/test.js", "src/utils.js"))


# ── Coverage detection, through the core ─────────────────────────────────

class TestCoverageThroughTheCore(NodeCase):
    """`already_covered` driven with this stack -- the predicate whose failure
    mode is a unit that has no test reading as covered and vanishing."""

    def covered(self):
        units, _ = stack_node.discover_units(self.root)
        return units, rank_risk.already_covered(self.root, units, stack_node)

    def test_a_sibling_directory_test_credits_only_its_own_package(self):
        # The required negative: fix round 6's ruling that "the colliding
        # sibling is by definition in a different directory" holds only while
        # `is_test_for` means same-directory, and `__tests__/` breaks exactly
        # that. Package b has NO test and must be reported uncovered.
        write(self.root, "packages/a/src/utils.js", "export function parse(s) { return s; }\n")
        write(self.root, "packages/b/src/utils.js", "export function parse(s) { return s; }\n")
        write(self.root, "packages/a/src/__tests__/utils.test.js",
              'import { parse } from "../utils";\n'
              'test("parses", () => { parse("x"); });\n')
        units, covered = self.covered()
        self.assertEqual([u["id"] for u in units],
                         ["packages/a/src/utils.js::parse", "packages/b/src/utils.js::parse"])
        self.assertIn("packages/a/src/utils.js::parse", covered)
        self.assertNotIn("packages/b/src/utils.js::parse", covered)

    def test_a_mirrored_test_tree_credits_only_the_file_it_imports(self):
        # `test/utils.test.js` is POSITIONED as a test for both `src/utils.js`
        # and `lib/utils.js`; only the resolved specifier tells them apart,
        # which is what the defining/referencing paths bought.
        write(self.root, "src/utils.js", "export function parse(s) { return s; }\n")
        write(self.root, "lib/utils.js", "export function parse(s) { return s; }\n")
        write(self.root, "test/utils.test.js",
              'import { parse } from "../src/utils";\n'
              'test("parses", () => { parse("x"); });\n')
        _, covered = self.covered()
        self.assertIn("src/utils.js::parse", covered)
        self.assertNotIn("lib/utils.js::parse", covered)

    def test_an_uncolliding_module_is_credited_by_the_looser_rule(self):
        write(self.root, "src/ledger.js", "export function post(e) { return e; }\n")
        write(self.root, "src/ledger.test.js",
              'import { post } from "./ledger";\ntest("posts", () => { post(1); });\n')
        _, covered = self.covered()
        self.assertEqual(sorted(covered), ["src/ledger.js::post"])

    def test_a_unit_with_no_test_stays_uncovered(self):
        write(self.root, "src/ledger.js",
              "export function post(e) { return e; }\nexport function purge() {}\n")
        write(self.root, "src/ledger.test.js",
              'import { post } from "./ledger";\ntest("posts", () => { post(1); });\n')
        _, covered = self.covered()
        self.assertNotIn("src/ledger.js::purge", covered)

    def test_inbound_refs_counts_through_this_stack(self):
        write(self.root, "src/utils.js", "export function parse(s) { return s; }\n")
        write(self.root, "src/app.js",
              'import { parse } from "./utils";\nexport function run() { return parse(1); }\n')
        units, _ = stack_node.discover_units(self.root)
        refs = rank_risk.inbound_refs(self.root, units, stack_node)
        self.assertGreaterEqual(refs["src/utils.js::parse"], 1)


if __name__ == "__main__":
    unittest.main()
