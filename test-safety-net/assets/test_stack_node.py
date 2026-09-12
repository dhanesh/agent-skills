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

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
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
stack_common = _load("stack_common")
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

    def test_module_exports_method_shorthand(self):
        write(self.root, "src/a.js",
              "module.exports = {\n  parse(s) { return s; },\n  format: (x) => x,\n};\n")
        by = self.units_by_name()
        self.assertEqual(sorted(by), ["format", "parse"])
        self.assertEqual(by["parse"]["kind"], "function")

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

    def test_nested_template_literals(self):
        write(self.root, "src/a.js",
              "const t = `a ${ `b ${c}` } d`;\nexport function real() {}\n")
        self.assertEqual(self.ids(), ["src/a.js::real"])

    def test_an_apostrophe_in_jsx_text_costs_at_most_its_own_line(self):
        # The stripper reads it as a string opening and bounds it to the line,
        # so the export above and the one below both survive.
        write(self.root, "src/a.tsx",
              "export function A() {\n  return <p>don't {x} stop</p>;\n}\n"
              "export function B() {}\n")
        self.assertEqual(sorted(self.units_by_name()), ["A", "B"])

    def test_generic_arrow_and_decorated_class(self):
        write(self.root, "src/a.tsx", "export const f = <T,>(x: T) => x;\n")
        write(self.root, "src/b.ts", "@Injectable()\nexport class Svc {}\n")
        by = self.units_by_name()
        self.assertEqual((by["f"]["kind"], by["Svc"]["kind"]), ("function", "class"))

    def test_destructured_export_is_a_known_miss(self):
        # Recorded, not accepted: `export const { a, b } = make()` names units
        # this reader cannot see. Task 3's precise path is where it closes.
        write(self.root, "src/a.js", "export const { a, b } = make();\n")
        self.assertEqual(self.ids(), [])

    def test_a_file_of_only_comments_and_strings_yields_nothing(self):
        write(self.root, "src/a.js",
              "// nothing here\n/* export class Ghost {} */\nconst s = 'export const g = () => 1';\n")
        self.assertEqual(self.ids(), [])


# ── The stripper ─────────────────────────────────────────────────────────

class TestStripper(unittest.TestCase):
    def root_for_stripper(self):
        if not getattr(self, "_root", None):
            self._root = tempfile.mkdtemp(prefix="tsn-strip-")
            self.addCleanup(shutil.rmtree, self._root, ignore_errors=True)
        return self._root

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

    def test_a_regex_after_return_is_a_regex_not_division(self):
        # FOUND BY THE PRECISE/HEURISTIC COMPARISON, on a real repo: three
        # exports of one 130-line TypeScript file were invisible to the
        # heuristic and visible to the parser. `_REGEX_PREV_WORDS` was matched
        # against the identifier STARTING at the current character, so walking
        # `return` left `prev_word == "n"` and the list never matched anything.
        # The regex text then stayed visible, and one `{` inside it opened a
        # bracket that never closed -- so bracket depth stayed above zero for
        # the REST OF THE FILE and every export below stopped being top level.
        out = stack_node.strip_noncode("return /abc/.test(x);")
        self.assertNotIn("abc", out)
        for word in ("typeof", "instanceof", "in", "of", "case", "do", "else",
                     "yield", "await", "delete", "void", "new"):
            if word in stack_node._REGEX_PREV_WORDS:
                self.assertNotIn("zz", stack_node.strip_noncode("%s /zz/;" % word), word)

    def test_division_is_still_division(self):
        # The other direction, and the reason this is a word list rather than
        # "always a regex": blanking a division blanks CODE, which is the
        # failure the whole stripper is arranged to avoid.
        for src in ("x = a / b / c;", "const r = 1/2/3;", "total = sum / n;"):
            self.assertEqual(stack_node.strip_noncode(src), src, src)

    def test_a_regex_containing_a_brace_does_not_swallow_the_file(self):
        src = ("export function head(t) { return /\\{[A-Za-z]/.test(t); }\n"
               "export function tail(t) { return t; }\n")
        write(self.root_for_stripper(), "src/re.js", src)
        units, _ = stack_node.discover_units(self.root_for_stripper())
        self.assertEqual([u["name"] for u in units], ["head", "tail"])

    def test_unterminated_string_stops_at_the_newline(self):
        # Wrong in the visible-text direction on purpose: a phantom unit, not
        # a swallowed file.
        out = stack_node.strip_noncode("const s = 'oops\nexport function real() {}\n")
        self.assertIn("export function real()", out)


# ── The rest of the interface ────────────────────────────────────────────

class TestInterfaceNames(NodeCase):
    def test_supplies_every_interface_name(self):
        for attr in ("STACK_NAME", "evidence", "iter_source_files", "is_test_path",
                     "is_test_for", "module_of", "name_pattern", "path_pattern",
                     "IDENTIFIER_RE", "preceding_qualifier", "module_bindings",
                     "reached_through_module", "discover_units", "triage"):
            self.assertTrue(hasattr(stack_node, attr), attr)
        self.assertEqual(stack_node.STACK_NAME, "node")
        # All fifteen, so registration cannot raise `AttributeError` inside
        # `rank()` for a repo this stack wins. Compared by NAME, not identity:
        # this file loads the stacks by path while `rank_risk` imports them by
        # name, so the registry holds a different module object for the same
        # source file.
        self.assertIn("node", [s.STACK_NAME for s in rank_risk.STACKS])

    def test_the_registry_holds_every_stack(self):
        # Three since the Go stack registered. This is the one assertion in
        # this file that a new stack is SUPPOSED to change.
        self.assertEqual(sorted(s.STACK_NAME for s in rank_risk.STACKS),
                         ["go", "node", "python"])
        for stack in rank_risk.STACKS:
            for attr in ("STACK_NAME", "evidence", "iter_source_files",
                         "is_test_path", "is_test_for", "scope_files", "module_of",
                         "name_pattern", "path_pattern", "IDENTIFIER_RE",
                         "preceding_qualifier", "module_bindings",
                         "reached_through_module", "discover_units", "triage",
                         "classify_manifest"):
                self.assertTrue(hasattr(stack, attr),
                                "%s lacks %s" % (stack.STACK_NAME, attr))

    def test_evidence_counts_sources(self):
        # The manifest half of this lives in `TestManifestCaseTable`, with the
        # eight repo shapes that decide what the scaling is worth.
        self.assertEqual(stack_node.evidence(self.root), 0)
        write(self.root, "src/a.mjs", "export function a() {}\n")
        write(self.root, "src/b.ts", "export function b() {}\n")
        self.assertEqual(stack_node.evidence(self.root), 2)

    def test_a_manifest_with_no_source_behind_it_scores_nothing(self):
        # Scoring a manifest with nothing behind it would let a stack win a
        # repo it then discovers no units in -- a clean-looking empty report.
        # The body is the FULLY DECLARING one on purpose: a tooling-only
        # manifest now scores nothing for a second, independent reason, and a
        # fixture that trips both rules stops testing either.
        write(self.root, "package.json", MANIFEST_BODIES["package.json/declaring"])
        self.assertEqual(stack_node.evidence(self.root), 0)

    def test_evidence_ignores_node_modules(self):
        write(self.root, "node_modules/x/index.js", "export function y() {}\n")
        write(self.root, "node_modules/x/package.json", "{}\n")
        self.assertEqual(stack_node.evidence(self.root), 0)

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

    def test_a_tsconfig_alias_credits_no_file_it_cannot_resolve(self):
        """F4, and C1 from the Python branch verbatim: a unit with NO test, covered.

        `src/__tests__/utils.test.ts` importing `"@app/utils"` lines three
        predicates up wrongly at once. `_dir_key` strips both `src` and `lib`
        (both in `SRC_ROOTS`) and strips `__tests__`, so the test is positioned
        for BOTH files. `_specifier_matches` could not resolve a non-relative
        specifier and compared last segments, so `@app/utils` matched module
        `utils` -- again both. And `rivals`, the guard that is supposed to
        bound exactly this, never fires, because a test importing `"@app/utils"`
        never emits the string `src/utils`.

        Result: `lib/utils.ts::parse` had no test anywhere in the repo, was
        reported `covered`, and dropped out of `ranked` entirely -- the outcome
        `already_covered`'s own docstring calls "the opposite of safe". The
        rule this stack inherits is that the coverage predicate may UNDER-credit
        and never over-credit, so an alias it cannot resolve now credits
        nothing at all.

        R12's two existing fixtures do not cover this: both use RELATIVE
        specifiers, which the resolver handles exactly. The alias case is the
        dominant real-world TypeScript layout.
        """
        write(self.root, "src/utils.ts", "export function parse(s: string) { return s; }\n")
        write(self.root, "lib/utils.ts", "export function parse(s: string) { return s; }\n")
        write(self.root, "tsconfig.json",
              '{"compilerOptions": {"baseUrl": ".", "paths": {"@app/*": ["src/*"]}}}\n')
        write(self.root, "src/__tests__/utils.test.ts",
              'import { parse } from "@app/utils";\n'
              'test("parses", () => { parse("x"); });\n')
        units, covered = self.covered()
        self.assertEqual([u["id"] for u in units],
                         ["lib/utils.ts::parse", "src/utils.ts::parse"])
        # The unit with no test anywhere must NOT be credited…
        self.assertNotIn("lib/utils.ts::parse", covered)
        # …and must be back in the plan rather than silently dropped.
        plan = rank_risk.rank(self.root, since="10 years ago", stack=stack_node)
        self.assertIn("lib/utils.ts::parse", [r["id"] for r in plan["ranked"]])

    def test_a_baseurl_specifier_that_names_the_path_still_credits_it(self):
        # The decidable half of the same rule, kept as its own fixture so the
        # fix cannot quietly become "non-relative specifiers credit nothing".
        # `"src/utils"` is non-relative and UNAMBIGUOUS: it names the defining
        # file's path, not merely its last segment, so it credits `src` and
        # refuses `lib`.
        write(self.root, "src/utils.ts", "export function parse(s: string) { return s; }\n")
        write(self.root, "lib/utils.ts", "export function parse(s: string) { return s; }\n")
        write(self.root, "src/__tests__/utils.test.ts",
              'import { parse } from "src/utils";\n'
              'test("parses", () => { parse("x"); });\n')
        _, covered = self.covered()
        self.assertIn("src/utils.ts::parse", covered)
        self.assertNotIn("lib/utils.ts::parse", covered)

    def test_a_bare_single_segment_specifier_credits_nothing_under_ambiguity(self):
        # `import { parse } from "utils"` under a `baseUrl` cannot say WHICH
        # `utils`. Undecidable is under-credit, not a coin flip.
        write(self.root, "src/utils.ts", "export function parse(s: string) { return s; }\n")
        write(self.root, "lib/utils.ts", "export function parse(s: string) { return s; }\n")
        write(self.root, "src/__tests__/utils.test.ts",
              'import { parse } from "utils";\n'
              'test("parses", () => { parse("x"); });\n')
        _, covered = self.covered()
        self.assertEqual(covered, {})

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


# ── Stack detection ──────────────────────────────────────────────────────

REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
# True only when these assets are still sitting in the agent-skills repo they
# were written in. Installed into somebody else's tree, `REPO_ROOT` is THAT
# repo, and the classification below is a statement about it that nobody made.
IN_SOURCE_REPO = os.path.isdir(os.path.join(REPO_ROOT, "scripts", "gates"))


class TestStackDetection(unittest.TestCase):
    """The detector, with BOTH stacks registered.

    Every test here installs the two-stack registry explicitly rather than
    trusting whatever `STACKS` happens to hold, because the failure these pin
    is one stack shouting down another and it is unprovable with one stack
    registered. `test_the_registry_really_holds_both` is the separate
    assertion that the shipped registry is that list.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tsn-detect-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self._stacks = rank_risk.STACKS
        rank_risk.STACKS = [stack_node, rank_risk.stack_python]
        self.addCleanup(setattr, rank_risk, "STACKS", self._stacks)
        # The list installed above is the SHIPPED registry, in a different
        # order: highest evidence wins, so order is meaningless and these tests
        # would pass with it reversed. `test_the_registry_holds_both_stacks`
        # is the separate assertion that the shipped registry is these two.

    @unittest.skipUnless(IN_SOURCE_REPO, "assets are installed outside their own repo")
    def test_this_repo_classifies_as_python_not_node(self):
        # THE test that would have caught the misclassification. This repo
        # holds 51 non-test `.py` files against 17 `.mjs`/`.ts`, and its only
        # `package.json` sits inside `starlight-handbook-kit/assets/templates/
        # scaffold/` -- a scaffold this repo SHIPS, not its own manifest.
        # First-match-wins with a generous node `matches()` called this repo
        # node, which discovers the wrong units and triages them with the
        # wrong marker tables while still printing a well-formed report.
        self.assertEqual(rank_risk.detect_stack(REPO_ROOT).STACK_NAME, "python")
        scores = dict(rank_risk.stack_evidence(REPO_ROOT))
        self.assertGreater(scores["python"], scores["node"])
        # And no manifest scaling reached either side: the scaffold's
        # `package.json` is the only manifest in the tree, and it describes a
        # site this repo INSTALLS ELSEWHERE. Both scores are raw file counts.
        self.assertEqual(scores["node"],
                         sum(1 for _ in stack_node.iter_source_files(REPO_ROOT)))
        self.assertEqual(scores["python"],
                         sum(1 for _ in rank_risk.stack_python.iter_source_files(REPO_ROOT)))

    def test_a_manifest_inside_a_template_dir_is_not_evidence(self):
        write(self.root, "assets/templates/scaffold/package.json",
              MANIFEST_BODIES["package.json/declaring"])
        write(self.root, "src/thing.py", "def go():\n    return 1\n")
        self.assertEqual(rank_risk.detect_stack(self.root).STACK_NAME, "python")
        # Nothing scored for node at all -- not "scored less". A manifest
        # describing a scaffold this repo SHIPS says nothing about this repo.
        self.assertEqual(dict(rank_risk.stack_evidence(self.root))["node"], 0)

    def test_template_dirs_bound_the_manifest_rule_and_nothing_else(self):
        # Source files under a template directory still COUNT. The asymmetry is
        # deliberate: this repo's own Python lives in `<skill>/assets/`, so a
        # file-count rule that skipped template directories would erase the
        # majority that makes this repo Python in the first place. It is the
        # MANIFEST -- a claim about what a directory IS -- that a template
        # directory invalidates.
        for i in range(4):
            write(self.root, "assets/templates/scaffold/src/a%d.ts" % i,
                  "export function a%d() {}\n" % i)
        self.assertEqual(dict(rank_risk.stack_evidence(self.root))["node"], 4)

    def test_a_manifest_at_the_root_beats_a_file_majority(self):
        # The direction the bonus exists for: a node package that vendors a
        # couple of Python scripts is still a node package.
        write(self.root, "package.json", MANIFEST_BODIES["package.json/declaring"])
        write(self.root, "src/app.ts", "export function boot() {}\n")
        for i in range(6):
            write(self.root, "tools/gen%d.py" % i, "def go():\n    return 1\n")
        self.assertEqual(rank_risk.detect_stack(self.root).STACK_NAME, "node")

    def test_a_monorepo_package_manifest_still_counts(self):
        write(self.root, "packages/api/package.json",
              MANIFEST_BODIES["package.json/declaring"])
        write(self.root, "packages/api/src/app.ts", "export function boot() {}\n")
        write(self.root, "tools/gen.py", "def go():\n    return 1\n")
        self.assertEqual(rank_risk.detect_stack(self.root).STACK_NAME, "node")

    def test_a_manifest_buried_deeper_than_the_depth_limit_does_not(self):
        write(self.root, "vendored/deep/nested/pkg/package.json",
              MANIFEST_BODIES["package.json/declaring"])
        write(self.root, "vendored/deep/nested/pkg/app.ts", "export function b() {}\n")
        write(self.root, "a.py", "def go():\n    return 1\n")
        write(self.root, "b.py", "def go2():\n    return 1\n")
        self.assertEqual(rank_risk.detect_stack(self.root).STACK_NAME, "python")

    def test_where_a_path_glob_manifest_still_describes_the_root(self):
        # N1. `requirements/*.txt` was matched against the manifest's WHOLE
        # repo-relative path, so the name Django's layout actually uses counted
        # at the analysed root and nowhere else -- while every exact name in
        # `MANIFESTS` counted anywhere in `MANIFEST_MAX_DEPTH`. The bound below
        # is that same constant, counted from the directory the spec is
        # ANCHORED in: the spec's own `requirements/` segment is part of the
        # manifest's NAME, not part of how far it sits from the root.
        cases = (("", True),                        # the root itself
                 ("backend/", True),                # the reproduction
                 ("packages/api/", True),           # what an exact name reaches
                 ("services/api/backend/", False),  # one past the bound
                 ("node_modules/pkg/", False),      # a dependency's own manifest
                 ("vendor/pkg/", False),
                 (".venv/lib/", False),
                 ("assets/templates/scaffold/", False))   # a shipped scaffold
        for anchor, expected in cases:
            with self.subTest(anchor or "<root>"):
                root = tempfile.mkdtemp(prefix="tsn-anchor-", dir=self.root)
                write(root, anchor + "requirements/base.txt", "Django>=4.2\n")
                found = list(stack_common.iter_manifests(
                    root, rank_risk.stack_python.MANIFESTS))
                self.assertEqual(bool(found), expected, found)
                # And it yields the SPEC, not `base.txt`: the classifier reads
                # `requirements/*.txt`'s content rule off the key it is handed.
                for _rel, spec in found:
                    self.assertEqual(spec, "requirements/*.txt")

    def test_the_deeper_walk_a_path_glob_needs_does_not_widen_exact_names(self):
        # The walk has to descend past `MANIFEST_MAX_DEPTH` for a two-segment
        # spec to be found at the bound, and that extra reach must belong to
        # the spec that needed it. An exact name at the same file depth is
        # still out of range, or the glob's allowance quietly becomes
        # everyone's.
        write(self.root, "services/api/backend/requirements.txt", "Django>=4.2\n")
        write(self.root, "services/api/backend/pyproject.toml",
              MANIFEST_BODIES["pyproject.toml/declaring"])
        self.assertEqual(list(stack_common.iter_manifests(
            self.root, rank_risk.stack_python.MANIFESTS)), [])

    def test_a_dead_heat_is_reported_not_guessed(self):
        write(self.root, "a.py", "def a():\n    return 1\n")
        write(self.root, "b.py", "def b():\n    return 1\n")
        write(self.root, "a.ts", "export function a() {}\n")
        write(self.root, "b.ts", "export function b() {}\n")
        with self.assertRaises(rank_risk.AmbiguousStack) as caught:
            rank_risk.detect_stack(self.root)
        self.assertEqual(dict(caught.exception.scores), {"python": 2, "node": 2})

    def test_an_explicit_stack_settles_an_ambiguous_repo(self):
        write(self.root, "a.py", "def a():\n    return 1\n")
        write(self.root, "a.ts", "export function a() {}\n")
        self.assertEqual(rank_risk.detect_stack(self.root, "node").STACK_NAME, "node")
        self.assertEqual(rank_risk.detect_stack(self.root, "python").STACK_NAME, "python")
        with self.assertRaises(ValueError):
            rank_risk.detect_stack(self.root, "cobol")

    def test_a_clear_majority_is_not_ambiguous(self):
        for i in range(20):
            write(self.root, "src/m%d.py" % i, "def go():\n    return 1\n")
        write(self.root, "scripts/build.mjs", "export function build() {}\n")
        self.assertEqual(rank_risk.detect_stack(self.root).STACK_NAME, "python")

    def test_an_empty_directory_still_falls_back_to_python(self):
        # The pre-existing contract: an unplaceable directory gets a
        # well-formed empty report, never an error.
        self.assertEqual(rank_risk.detect_stack(self.root).STACK_NAME, "python")
        self.assertEqual(rank_risk.stack_evidence(self.root),
                         [("node", 0), ("python", 0)])

    def test_the_cli_reports_the_verdict_and_refuses_to_guess(self):
        write(self.root, "a.py", "def a():\n    return 1\n")
        write(self.root, "a.ts", "export function a() {}\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = rank_risk.main([self.root])
        self.assertEqual(code, 2)
        self.assertIn("--stack", err.getvalue())
        self.assertIn("python=1", err.getvalue())

    def test_the_cli_names_the_winner_and_its_evidence(self):
        write(self.root, "a.py", "def a():\n    return 1\n")
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            code = rank_risk.main([self.root])
        self.assertEqual(code, 0)
        self.assertIn("stack=python", err.getvalue())
        self.assertIn("node=0", err.getvalue())
        self.assertEqual(json.loads(out.getvalue())["stack"], "python")

# ── Precise discovery, and every way it must decline ─────────────────────

def _typescript_lib():
    """A real `typescript` on this machine, or None.

    Looked up through node itself so a checkout that happens to have one --
    a parent directory\'s `node_modules`, a globally linked install -- can
    exercise the agreement test. `TSN_TYPESCRIPT_LIB` overrides, for CI images
    that put it somewhere node will not look from here.
    """
    env = os.environ.get("TSN_TYPESCRIPT_LIB")
    if env and os.path.isfile(env):
        return env
    try:
        r = subprocess.run(
            ["node", "-p", "require.resolve('typescript/lib/typescript.js')"],
            cwd=_HERE, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    path = r.stdout.strip()
    return path if r.returncode == 0 and os.path.isfile(path) else None


def _skip_no_typescript():
    """Skip LOUDLY. A silent skip is a test that reports success for a path
    nobody ran, which is the exact failure mode this suite exists to prevent
    elsewhere."""
    sys.stderr.write(
        "\nSKIP: no `typescript` resolvable from %s -- the PRECISE discovery "
        "path is NOT exercised on this machine. Install typescript (or point "
        "TSN_TYPESCRIPT_LIB at a `typescript.js`) to run it.\n" % _HERE)
    raise unittest.SkipTest("no typescript available; precise path unexercised")


class TestPreciseDiscovery(NodeCase):
    """The optional path. The bar it must clear is NOT accuracy -- it is that
    it can never fail a run: the heuristic is what always runs, so precision
    is an upgrade and never a dependency."""

    def stub_typescript(self, body):
        """Install a fake `node_modules/typescript/lib/typescript.js`.

        `node_modules` is in `SKIP_DIRS`, so the stub is never itself a source
        file -- these fixtures stay exactly as many units as they look.
        """
        return write(self.root, "node_modules/typescript/lib/typescript.js", body)

    def test_falls_back_to_heuristic_when_toolchain_missing(self):
        write(self.root, "src/util.ts", "export function parse(s: string) { return 1; }\n")
        units, mode = stack_node.discover_units(self.root)
        self.assertEqual(mode, "heuristic")
        self.assertEqual([u["id"] for u in units], ["src/util.ts::parse"])

    def test_falls_back_when_the_toolchain_throws(self):
        write(self.root, "src/util.ts", "export function parse(s: string) { return 1; }\n")
        self.stub_typescript('throw new Error("boom");\n')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            units, mode = stack_node.discover_units(self.root)
        self.assertEqual(mode, "heuristic")
        self.assertEqual([u["id"] for u in units], ["src/util.ts::parse"])
        # A toolchain that was FOUND and then failed is the surprising case,
        # so it is named on stderr rather than left to the `discovery` key.
        self.assertIn("precise discovery", err.getvalue())

    def test_falls_back_when_the_toolchain_prints_unparseable_output(self):
        write(self.root, "src/util.ts", "export function parse(s: string) { return 1; }\n")
        self.stub_typescript('process.stdout.write("<html>nope</html>");'
                             'process.exit(0);\n')
        with contextlib.redirect_stderr(io.StringIO()):
            units, mode = stack_node.discover_units(self.root)
        self.assertEqual(mode, "heuristic")
        self.assertEqual([u["id"] for u in units], ["src/util.ts::parse"])

    def test_falls_back_when_the_toolchain_hangs(self):
        write(self.root, "src/util.ts", "export function parse(s: string) { return 1; }\n")
        lib = self.stub_typescript(
            "Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 60000);\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(stack_node._units_precise(self.root, ts_lib=lib, timeout=2))
        self.assertIn("timed out", err.getvalue())

    def test_falls_back_when_node_itself_is_absent(self):
        write(self.root, "src/util.ts", "export function parse(s: string) { return 1; }\n")
        lib = self.stub_typescript("module.exports = {};\n")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(stack_node._units_precise(
                self.root, ts_lib=lib, node_exe="node-that-is-not-installed"))

    def test_a_yarn_pnp_tree_resolves_no_toolchain_and_says_heuristic(self):
        # Yarn PnP ships no `node_modules/typescript` at all. Nothing to
        # resolve is not an error: it is the ordinary case, and the mode label
        # is where the run reports it.
        write(self.root, "src/util.ts", "export function parse(s: string) { return 1; }\n")
        write(self.root, ".pnp.cjs", "// zip-backed resolution\n")
        self.assertIsNone(stack_node._typescript_lib(self.root))
        self.assertEqual(stack_node.discover_units(self.root)[1], "heuristic")

    def test_the_mode_label_follows_the_path_that_actually_ran(self):
        write(self.root, "src/util.ts", "export function parse(s: string) { return 1; }\n")
        real = stack_node._units_precise
        stack_node._units_precise = lambda root, **kw: [
            {"id": "src/util.ts::parse", "path": "src/util.ts", "name": "parse",
             "lineno": 1, "kind": "function"}]
        self.addCleanup(setattr, stack_node, "_units_precise", real)
        units, mode = stack_node.discover_units(self.root)
        self.assertEqual(mode, "precise")
        self.assertEqual([u["id"] for u in units], ["src/util.ts::parse"])

    def test_a_malformed_unit_in_the_payload_declines_the_whole_run(self):
        # Half-trusting a payload is worse than not trusting it: a run that
        # dropped the rows it could not read would report FEWER units than the
        # heuristic and call itself precise.
        files = ["src/util.ts"]
        good = [{"path": "src/util.ts", "name": "parse", "kind": "function", "lineno": 3}]
        self.assertEqual([u["id"] for u in stack_node._units_from_payload(good, files)],
                         ["src/util.ts::parse"])
        for bad in ([{"path": "src/util.ts", "name": "parse", "kind": "function"}],
                    [{"path": "src/util.ts", "name": "parse", "kind": "enum", "lineno": 1}],
                    [{"path": "other.ts", "name": "parse", "kind": "function", "lineno": 1}],
                    [{"path": "src/util.ts", "name": "parse", "kind": "function",
                      "lineno": "3"}],
                    ["src/util.ts::parse"]):
            self.assertIsNone(stack_node._units_from_payload(bad, files), bad)

    def test_a_shimmed_toolchain_that_reads_nothing_declines_rather_than_reporting_zero(self):
        """F2: the silent zero, wearing the better label.

        A `node_modules/typescript` whose entry point LOADS but whose API does
        not behave -- an aliased, shimmed or incompatible compiler -- made the
        walker count every file into `unreadable` and emit an empty `units`
        list. An empty list is not `None`, so nothing declined: the run
        reported `discovery: "precise"` with zero units and exit 0, while the
        heuristic would have found five. Zero units reads as "this repo has
        nothing worth testing", which is the failure mode this whole stack was
        built to refuse.
        """
        for i in range(5):
            write(self.root, "src/m%d.ts" % i,
                  "export function f%d(s: string) { return s; }\n" % i)
        # An entry point that loads and exports the right NAMES, but whose
        # `createSourceFile` throws -- the walker's per-file `catch` counts it.
        self.stub_typescript(
            "module.exports = {\n"
            "  createSourceFile: function () { throw new Error('shimmed'); },\n"
            "  ScriptTarget: { Latest: 99 },\n"
            "  SyntaxKind: {},\n"
            "};\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            units, mode = stack_node.discover_units(self.root)
        self.assertEqual(mode, "heuristic")
        self.assertEqual(len(units), 5)
        self.assertIn("unreadable", err.getvalue())

    def test_one_unreadable_file_out_of_many_still_declines_the_whole_run(self):
        """`unreadable` is all-or-nothing, exactly as a malformed row is.

        A run that walked nine files and choked on the tenth reports nine units
        and calls itself precise -- fewer than the heuristic would find, under
        the label the report tells an agent to trust MORE. The consumer used to
        read `payload["units"]` and never read `payload["unreadable"]` at all,
        so a number the walker computed on every run was thrown away.
        """
        self.assertIsNone(
            stack_node._decline_reason({"units": [{"x": 1}], "unreadable": 0}, ["a.ts"]))
        self.assertIn("1 file", stack_node._decline_reason(
            {"units": [{"x": 1}], "unreadable": 1}, ["a.ts", "b.ts"]))

    def test_an_empty_unit_list_over_a_non_empty_file_set_declines(self):
        # The second half of the same rule, for a toolchain that reports no
        # failures and no units: a tree with source files in it has something
        # in it, and a reader that finds nothing has not read it.
        self.assertIn("no units", stack_node._decline_reason(
            {"units": [], "unreadable": 0}, ["a.ts"]))
        # …but a tree with no files never gets here, and an empty tree is not
        # a degraded run.
        self.assertIsNone(stack_node._decline_reason({"units": [], "unreadable": 0}, []))

    def test_no_precise_refuses_to_run_the_analysed_repos_compiler(self):
        """F6: running a target repo's own code is a decision a user may refuse.

        The precise path `require`s `<analysed repo>/node_modules/typescript/lib/
        typescript.js` in a node process. That is the repo's own code, executing,
        during what SKILL.md pitches as a read-only analysis of a tree nobody
        trusts yet. The flag is the important half of the disclosure: an agent
        handed a repo it did not write needs a way to say no.
        """
        write(self.root, "src/util.ts", "export function parse(s: string) { return 1; }\n")
        marker = os.path.join(self.root, "PWNED")
        self.stub_typescript(
            "require('fs').writeFileSync(%r, 'the analysed repo ran');\n"
            "throw new Error('and then failed');\n" % marker)
        with contextlib.redirect_stderr(io.StringIO()):
            units, mode = stack_node.discover_units(self.root, precise=False)
        self.assertEqual(mode, "heuristic")
        self.assertEqual([u["id"] for u in units], ["src/util.ts::parse"])
        self.assertFalse(os.path.exists(marker),
                         "--no-precise still executed the analysed repo's compiler")
        # …and the negative arm: with the default, it DOES run, which is the
        # behaviour the flag exists to let a user decline.
        with contextlib.redirect_stderr(io.StringIO()):
            stack_node.discover_units(self.root)
        self.assertTrue(os.path.exists(marker))

    def test_the_cli_flag_reaches_the_stack(self):
        # The flag is worth nothing if it stops at argparse.
        write(self.root, "package.json", '{"name": "demo"}\n')
        write(self.root, "src/util.ts", "export function parse(s: string) { return 1; }\n")
        marker = os.path.join(self.root, "PWNED")
        self.stub_typescript(
            "require('fs').writeFileSync(%r, 'ran');\n"
            "throw new Error('nope');\n" % marker)
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            code = rank_risk.main([self.root, "--since", "10 years ago", "--no-precise"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["discovery"], "heuristic")
        self.assertFalse(os.path.exists(marker))

    def test_python_accepts_the_same_flag_and_is_unaffected(self):
        # The interface is one signature for every stack. Python's `ast` is
        # stdlib and executes nothing, so the flag is a no-op there -- but it
        # has to be ACCEPTED, or `--no-precise` would crash a Python repo.
        import stack_python
        write(self.root, "m.py", "def f():\n    return 1\n")
        self.assertEqual(stack_python.discover_units(self.root, precise=False)[1],
                         "precise")

    def test_the_toolchain_is_never_downloaded(self):
        # `npx tsc` on a miss DOWNLOADS. The precise path resolves a file that
        # already exists and requires it directly; nothing here may shell out
        # to a package runner.
        import inspect
        # The mechanism, not the prose: the only executable the precise path
        # ever names is `node`, and the only module it loads is a file already
        # on disk. A grep for "npx" would fail on the paragraph explaining why
        # `npx tsc` is forbidden, which is why this asserts the argv instead.
        self.assertEqual(
            inspect.signature(stack_node._units_precise).parameters["node_exe"].default,
            "node")
        self.assertIn('[node_exe, "-e", _PRECISE_JS, "--", ts_lib',
                      inspect.getsource(stack_node._units_precise))
        # And the walker spawns nothing of its own.
        for forbidden in ("child_process", "spawn", "execSync", "fetch("):
            self.assertNotIn(forbidden, stack_node._PRECISE_JS, forbidden)
        # Nothing to resolve is a decline, never a fetch.
        self.assertIsNone(stack_node._typescript_lib(self.root))


class TestDiscoveryIsReported(NodeCase):
    """The ninth key. A run that degraded must say so IN THE REPORT."""

    def test_a_node_repo_reports_which_reader_produced_its_units(self):
        write(self.root, "package.json", '{"name": "demo"}\n')
        write(self.root, "src/utils.js", "export function parse(s) { return s; }\n")
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            code = rank_risk.main([self.root, "--since", "10 years ago"])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["stack"], "node")
        # No `node_modules/typescript` in the fixture, so the toolchain path
        # never ran -- and the report says so rather than presenting heuristic
        # units as if a parser had produced them.
        self.assertEqual(payload["discovery"], "heuristic")
        self.assertEqual(payload["units_discovered"], 1)

    def test_the_key_is_the_stack_s_own_answer(self):
        write(self.root, "src/utils.js", "export function parse(s) { return s; }\n")
        real = stack_node._units_precise
        stack_node._units_precise = lambda root, **kw: [
            {"id": "src/utils.js::parse", "path": "src/utils.js", "name": "parse",
             "lineno": 1, "kind": "function"}]
        self.addCleanup(setattr, stack_node, "_units_precise", real)
        plan = rank_risk.rank(self.root, since="10 years ago", stack=stack_node)
        self.assertEqual(plan["discovery"], "precise")


class TestPreciseAgainstTheRealToolchain(NodeCase):
    """Both paths, on trees a real `typescript` can read.

    Skipped -- loudly -- where no toolchain exists. That skip is the honest
    outcome on a machine without one, and the noise is deliberate: a silent
    skip here reads exactly like a pass.
    """

    def setUp(self):
        super().setUp()
        self.lib = _typescript_lib()
        if self.lib is None:
            _skip_no_typescript()

    def precise(self):
        units = stack_node._units_precise(self.root, ts_lib=self.lib)
        self.assertIsNotNone(units, "the precise path declined a tree it should read")
        return units

    def test_both_paths_agree_on_a_fixture_both_can_read(self):
        write(self.root, "src/forms.js", "\n".join([
            "export function a() {}",
            "export default function b() {}",
            "export const c = (x) => x;",
            "export const d = function () {};",
            "export class E {}",
            "function f() {}",
            "export { f };",
        ]) + "\n")
        heuristic, mode = stack_node.discover_units(self.root)
        self.assertEqual(mode, "heuristic")            # no node_modules in the fixture
        self.assertEqual([u["id"] for u in self.precise()],
                         [u["id"] for u in heuristic])

    def test_precise_finds_what_the_heuristic_documented_as_misses(self):
        # Task 2 recorded these four as deliberate under-reports of a reader
        # with no parse tree. They are what precision BUYS, and the comparison
        # is the test: on a real tree the precise path must return MORE units
        # than the heuristic, never fewer.
        write(self.root, "src/codec.js",
              "export const { encode, decode } = makeCodec();\n")
        write(self.root, "src/cjs.js",
              "function fn(x) { return x; }\nmodule.exports = fn;\n")
        write(self.root, "src/quoted.js",
              "function parse(s) { return s; }\n"
              'module.exports = { "parse": parse };\n')
        heuristic = {u["id"] for u in stack_node.discover_units(self.root)[0]}
        precise = {u["id"] for u in self.precise()}
        self.assertEqual(sorted(precise - heuristic),
                         ["src/cjs.js::fn", "src/codec.js::decode",
                          "src/codec.js::encode", "src/quoted.js::parse"])
        self.assertEqual(precise & heuristic, heuristic)

    def test_precise_declines_the_types_that_have_no_runtime_body(self):
        write(self.root, "src/types.ts", "\n".join([
            "export interface Shape { x: number }",
            "export type Alias = string;",
            "export declare function ghost(): void;",
            "export const MAX = 5;",
            "export function real(): number { return 1; }",
        ]) + "\n")
        self.assertEqual([u["id"] for u in self.precise()], ["src/types.ts::real"])

    def test_a_file_that_does_not_parse_costs_only_itself(self):
        write(self.root, "src/broken.ts", "export function ((( {\n")
        write(self.root, "src/fine.ts", "export function ok() { return 1; }\n")
        self.assertIn("src/fine.ts::ok", [u["id"] for u in self.precise()])

    def test_a_precise_unit_the_heuristic_cannot_place_is_still_triaged(self):
        # The interaction worth pinning: triage re-reads the file with the
        # HEURISTIC reader, so a precise-only unit has no export position to
        # find. It is placed by the line discovery recorded instead, which
        # keeps its markers readable rather than declining it as "not found" —
        # otherwise the better discovery path would produce the worse plan,
        # every unit it alone found landing at Tier 4 as unnettable.
        write(self.root, "src/quoted.js",
              'import fs from "node:fs";\n'
              "function parse(p) { return fs.readFileSync(p); }\n"
              'module.exports = { "parse": parse };\n')
        unit = next(u for u in self.precise() if u["name"] == "parse")
        self.assertNotIn("src/quoted.js::parse",
                         [u["id"] for u in stack_node.discover_units(self.root)[0]])
        tier, reason = stack_node.triage(self.root, unit)
        self.assertEqual(tier, 2)
        self.assertIn("filesystem", reason)

    def test_a_destructured_export_keeps_its_import_time_floor(self):
        # And the fallback does not become a way around the import-time floor:
        # `makeLoader(...)` runs when the module is imported, so a fixture is
        # too late to control it whatever the unit's own span says.
        write(self.root, "src/codec.js",
              'import fs from "node:fs";\n'
              "export const { load } = makeLoader(fs.readFileSync);\n")
        unit = next(u for u in self.precise() if u["name"] == "load")
        tier, reason = stack_node.triage(self.root, unit)
        self.assertEqual(tier, 3)
        self.assertIn("import time", reason)


# ── The manifest weight ───────────────────────────────────

# What each fixture manifest CONTAINS, because content is what decides.
#
# The table used to write `{}` for every `.json` manifest and `[project]` for
# everything else, which was fine while a manifest's mere EXISTENCE scaled a
# stack's claim. It no longer does: a `package.json` that declares an entry
# point, runtime dependencies or a build is a claim about what the repo IS; one
# carrying git hooks and a formatter is a claim about how it is EDITED. Writing
# `{}` for both would have made every row below a test of the walk rather than
# of the rule.
MANIFEST_BODIES = {
    "package.json/declaring":
        '{"name": "app", "main": "src/index.js",\n'
        ' "dependencies": {"express": "^4.19.0"}}\n',
    "package.json/tooling":
        '{"name": "app", "private": true,\n'
        ' "devDependencies": {"husky": "^9.0.0", "prettier": "^3.0.0"},\n'
        ' "scripts": {"prepare": "husky"}}\n',
    "pyproject.toml/declaring": '[project]\nname = "srv"\nversion = "1.0.0"\n',
    "pyproject.toml/tooling": "[tool.ruff]\nline-length = 100\n",
    # Django's near-universal split-requirements layout. The CONTENT rule is
    # `requirements.txt`'s, unchanged -- what was missing was the NAME.
    "base.txt/declaring": "Django>=4.2\npsycopg[binary]>=3.1\n",
    "base.txt/tooling": "ruff==0.5.0\nblack==24.4.2\n",
    "requirements-dev.txt/declaring": "Django>=4.2\npytest>=8\n",
    "requirements-dev.txt/tooling": "ruff==0.5.0\n",
    # Django's entry point -- and it EARNS that by its content, like every
    # other manifest here. The `declaring` body names the settings module and
    # boots the framework; the `tooling` body is a file with the same name and
    # nothing Django about it, which is a repo shape that exists (a `manage.py`
    # is just a script name) and which used to flip a JS repo to python.
    "manage.py/tooling": '# not django\nprint("hi")\n',
    # A conda environment file, and the Kubernetes-flavoured YAML that shares
    # its name in a repo that is not Python at all. `dependencies:` is what
    # makes the first one a declaration of a Python environment.
    "environment.yaml/declaring":
        "name: srv\nchannels:\n  - conda-forge\n"
        "dependencies:\n  - python=3.11\n  - django\n",
    "environment.yaml/tooling": "name: prod\nreplicas: 3\n",
    "manage.py/declaring": (
        "#!/usr/bin/env python\n"
        "import os, sys\n"
        "def main():\n"
        "    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'srv.settings')\n"
        "    from django.core.management import execute_from_command_line\n"
        "    execute_from_command_line(sys.argv)\n"),
    # A test-runner config, and therefore TOOLING on the same rule that makes a
    # husky `package.json` tooling. It is in `MANIFESTS` so that decision is
    # visible and asserted rather than an unrecognised filename.
    "tox.ini/tooling": "[tox]\nenvlist = py311\n[testenv]\ndeps = pytest\n",
}

CASE_TABLE = [
    # (label, .py files, node files, node extension, manifests, expected)
    # where each manifest is `(relative path, "declaring" | "tooling")`.
    #
    # TWELVE REAL REPO SHAPES, AND THE VERDICT EACH MUST REACH. This table is
    # the specification of what a manifest is WORTH; `MANIFEST_FLOOR` and
    # `MANIFEST_MULTIPLIER` were chosen to satisfy it, in that order. Fitting
    # the constants to any one row is how the bug this replaced happened: an
    # additive `MANIFEST_BONUS = 100` is exactly right for row 4 and ranks
    # five files while ignoring forty in row 1.
    #
    # Rows 9-11 are the CONTENT rows. Before them, file counts alone had to
    # separate "a node project" from "a Python project with node tooling", and
    # they cannot: row 9 (8 `.py`, 3 `.js`, a husky `package.json`) scored node
    # 16 to python 8 and read as node. Nothing about the counts distinguishes
    # it from row 4 -- only the manifest does.
    ("a Python repo with JS tooling (prettier/husky)",
     40, 5, ".js", (("package.json", "tooling"),), "python"),
    ("a Python repo whose only manifest belongs to a shipped template",
     75, 17, ".ts",
     (("kit/assets/templates/scaffold/package.json", "declaring"),), "python"),
    ("a JS repo with a handful of Python scripts",
     3, 200, ".js", (("package.json", "declaring"),), "node"),
    ("a fresh node project beside some Python tooling",
     3, 2, ".js", (("package.json", "declaring"),), "node"),
    ("a Python service with a JS frontend, both declared",
     300, 20, ".js",
     (("pyproject.toml", "declaring"), ("package.json", "declaring")), "python"),
    ("a TypeScript repo with Python tooling scripts",
     4, 150, ".ts", (("package.json", "declaring"),), "node"),
    ("a 50/50 polyglot tree with no manifest at all",
     10, 10, ".js", (), "ambiguous"),
    ("an empty repo",
     0, 0, ".js", (), "neither"),
    ("a small Python repo whose package.json is only git hooks",
     8, 3, ".js", (("package.json", "tooling"),), "python"),
    ("a JS repo whose pyproject.toml is only a linter's config",
     30, 40, ".js",
     (("pyproject.toml", "tooling"), ("package.json", "declaring")), "node"),
    ("a real JS repo whose package.json happens to be tooling-shaped",
     3, 200, ".js", (("package.json", "tooling"),), "node"),
    # ROW 12 IS THE TABLE'S UPPER BOUND ON THE CONSTANTS, and it exists because
    # content classification took row 1's away. While a tooling `package.json`
    # scaled node's claim, row 1 (40 `.py` against 5 `.js`) is what stopped
    # `MANIFEST_FLOOR`/`MANIFEST_MULTIPLIER` from being raised; now that it
    # scales nothing, row 1 is satisfied at every pair in `0..8 x 2..6` and the
    # table pins the constants from below only. A Python backend with no
    # manifest of its own and a declared JavaScript frontend is the shape that
    # replaces it: raise the floor past 6 and ten frontend files outvote forty
    # backend ones.
    ("a Python backend with no manifest and a declared JS frontend",
     40, 10, ".js", (("web/package.json", "declaring"),), "python"),
    # ROWS 13-16 ARE THE REGION THE TABLE HAD NO ROW IN: "Python majority,
    # UNDECLARED; node minority, DECLARED". Rows 1-12 pin the constants along
    # the axis their author chose, and row 12 is `40 > 30` -- it says nothing
    # about 30/20 or 12/6, which is where every reproduction of F3 sits. They
    # were added from the OUTSIDE: pick the repo shape first, then see what the
    # code says.
    #
    # Row 13 is the reproduction verbatim. `requirements/base.txt` is standard
    # Django and matched NONE of `MANIFESTS`, which keyed on exact filenames at
    # the root, so Python earned no manifest credit while the `package.json`
    # every Django repo has for its frontend assets earned node's. The verdict
    # was `node=50, python=30`, and thirty Python modules got no plan at all.
    ("a conventional Django repo: split requirements, JS assets, a real package.json",
     30, 20, ".js",
     (("requirements/base.txt", "declaring"), ("package.json", "declaring")),
     "python"),
    ("Python Lambda handlers beside a declared TypeScript CDK app",
     12, 6, ".ts",
     (("requirements/base.txt", "declaring"), ("package.json", "declaring")),
     "python"),
    ("…and a split-requirements file naming only linters still declares nothing",
     30, 20, ".js",
     (("requirements/base.txt", "tooling"), ("package.json", "declaring")),
     "node"),
    ("a Django repo declared only by manage.py",
     30, 20, ".js",
     (("manage.py", "declaring"), ("package.json", "declaring")), "python"),
    # tox.ini is TOOLING, on the same rule that demotes a husky `package.json`.
    # A repo tested with tox is not thereby a Python repo.
    ("a JS repo that runs its few Python scripts under tox",
     8, 40, ".js", (("tox.ini", "tooling"), ("package.json", "declaring")), "node"),
    # ROWS 18-20 ARE WHERE A PATH-GLOB MANIFEST STILL DESCRIBES THE ROOT.
    # Row 13 taught `MANIFESTS` the NAME `requirements/*.txt`; it matched the
    # manifest's repo-relative path with `fnmatch`, which wants the whole
    # string, so it matched at the analysed root and nowhere else. One repo,
    # one file renamed, reproduced through the shipped CLI:
    #
    #   backend/requirements.txt       -> python=34, node=16   correct
    #   backend/requirements/base.txt  -> node=16,  python=12   WRONG
    #
    # and `backend/` + `frontend/` with a declared frontend manifest is the
    # commonest polyglot layout there is, so that is mainline rather than an
    # edge.
    #
    # THE BOUND IS THE ONE THAT ALREADY EXISTS, APPLIED WHERE IT MEANS
    # SOMETHING: `MANIFEST_MAX_DEPTH` counts the directory the spec is ANCHORED
    # in, not the file. A spec's own leading segments are part of what the
    # manifest is CALLED -- `requirements/base.txt` is the NAME of a
    # `requirements/*.txt` manifest, exactly as `Pipfile` is a name -- so they
    # cannot spend an allowance that exists to answer a different question
    # ("how far below the root does a manifest still describe the root?").
    # Row 19 pins the bound from below at the depth `packages/<pkg>/<manifest>`
    # already reaches for an exact name; row 20 pins it from above.
    ("a monorepo backend: split requirements one directory down, declared JS frontend",
     12, 3, ".js",
     (("backend/requirements/base.txt", "declaring"),
      ("frontend/package.json", "declaring")), "python"),
    ("…the same manifest at the depth an exact NAME already reaches",
     12, 3, ".js",
     (("packages/api/requirements/base.txt", "declaring"),
      ("frontend/package.json", "declaring")), "python"),
    ("…and one level past it, where a manifest describes something else",
     12, 3, ".js",
     (("services/api/backend/requirements/base.txt", "declaring"),
      ("frontend/package.json", "declaring")), "node"),
    # ROWS 21-22 ARE THE CONTENT RULE REACHING THE LAST TWO NAMES THAT ESCAPED
    # IT. `environment.yaml` and `manage.py` returned "declaring" without
    # reading a byte -- the `has_manifest` mistake this table exists to record,
    # reintroduced for two filenames. Row 21 is the repro: a JavaScript app
    # with three Python helpers detected node until a `config/environment.yaml`
    # holding `name: prod` and a `manage.py` holding `print("hi")` appeared
    # beside it. Row 22 is the other direction, without which row 21 could be
    # satisfied by deleting the names from `MANIFESTS`.
    ("a JS app with Python helpers, a k8s environment.yaml and a non-Django manage.py",
     3, 5, ".js",
     (("package.json", "tooling"), ("config/environment.yaml", "tooling"),
      ("manage.py", "tooling")), "node"),
    ("…and a conda-declared Python service still outweighs a declared JS frontend",
     12, 3, ".js",
     (("environment.yaml", "declaring"), ("frontend/package.json", "declaring")),
     "python"),
]


class TestManifestCaseTable(unittest.TestCase):
    """What a manifest is worth, decided against cases rather than in the abstract.

    A manifest SCALES a stack's claim; it does not add to it. The additive form
    this replaced (`files + 100`) let a root `package.json` -- the commonest
    thing in a Python repo that has one, a `prettier`/`husky` entry and nothing
    else -- outscore a forty-file Python majority 105 to 40, so the skill
    ranked five files and ignored forty. Multiplying cannot do that: a
    multiplier applied to a small claim stays small.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tsn-cases-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self._stacks = rank_risk.STACKS
        rank_risk.STACKS = [stack_node, rank_risk.stack_python]
        self.addCleanup(setattr, rank_risk, "STACKS", self._stacks)

    def _build(self, root, py, node, ext, manifests):
        for i in range(py):
            write(root, "srv/mod%d.py" % i, "def go%d():\n    return 1\n" % i)
        for i in range(node):
            write(root, "web/mod%d%s" % (i, ext), "export function go%d() {}\n" % i)
        for rel, kind in manifests:
            write(root, rel, MANIFEST_BODIES["%s/%s" % (os.path.basename(rel), kind)])
        # `manage.py` is a `.py` SOURCE file as well as a manifest -- exactly
        # as `setup.py` is, and counted twice for the same reason. The row's
        # `py` count is the number of modules under `srv/`, so nothing here
        # needs adjusting; this comment exists so the double count is not read
        # as an accident.

    def _verdict(self, root):
        """python | node | ambiguous | neither -- the four answers a run can give."""
        scores = dict(rank_risk.stack_evidence(root))
        if not any(scores.values()):
            return "neither"
        try:
            return rank_risk.detect_stack(root).STACK_NAME
        except rank_risk.AmbiguousStack:
            return "ambiguous"

    def test_every_row_of_the_case_table(self):
        for label, py, node, ext, manifests, expected in CASE_TABLE:
            with self.subTest(label):
                root = tempfile.mkdtemp(prefix="tsn-case-", dir=self.root)
                self._build(root, py, node, ext, manifests)
                scores = dict(rank_risk.stack_evidence(root))
                self.assertEqual(
                    self._verdict(root), expected,
                    "%s: python=%d node=%d" % (label, scores["python"], scores["node"]))

    def test_the_reproduction_that_forced_the_change(self):
        # The controller-reproduced shape, kept as its own named test because a
        # row inside a loop is easy to delete by accident. 40 .py + 5 .js + a
        # root `package.json` reported `stack=node (node=105, python=40)` and
        # `units: 5`: a Python repo whose skill looked at the JavaScript.
        self._build(self.root, 40, 5, ".js", (("package.json", "tooling"),))
        scores = dict(rank_risk.stack_evidence(self.root))
        self.assertEqual(rank_risk.detect_stack(self.root).STACK_NAME, "python")
        self.assertGreater(scores["python"], scores["node"])
        units, _mode = rank_risk.stack_python.discover_units(self.root)
        self.assertEqual(len(units), 40)

    def test_the_monorepo_reproduction_one_file_renamed(self):
        # N1 verbatim, and kept as its own named test because the difference
        # between the two trees is a SINGLE RENAME and a row in a loop cannot
        # show that. `backend/requirements.txt` was read; the same content at
        # `backend/requirements/base.txt` -- Django's own layout, one directory
        # down -- was not, and the plan ranked three frontend files while
        # twelve backend modules got none.
        verdicts = {}
        for rel in ("backend/requirements.txt", "backend/requirements/base.txt",
                    "requirements/base.txt"):
            root = tempfile.mkdtemp(prefix="tsn-mono-", dir=self.root)
            for i in range(12):
                write(root, "backend/app/mod%d.py" % i, "def go%d():\n    return 1\n" % i)
            for i in range(3):
                write(root, "frontend/src/c%d.js" % i, "export function c%d() {}\n" % i)
            write(root, "frontend/package.json",
                  MANIFEST_BODIES["package.json/declaring"])
            write(root, rel, MANIFEST_BODIES["base.txt/declaring"])
            scores = dict(rank_risk.stack_evidence(root))
            units, _mode = rank_risk.stack_python.discover_units(root)
            verdicts[rel] = (self._verdict(root), scores, len(units))
        for rel, (verdict, scores, n_units) in verdicts.items():
            with self.subTest(rel):
                self.assertEqual(verdict, "python",
                                 "%s: python=%d node=%d"
                                 % (rel, scores["python"], scores["node"]))
                self.assertEqual(n_units, 12)

    def test_a_manifest_scales_the_claim_it_finds(self):
        write(self.root, "src/a.mjs", "export function a() {}\n")
        write(self.root, "src/b.ts", "export function b() {}\n")
        self.assertEqual(stack_node.evidence(self.root), 2)
        write(self.root, "package.json", MANIFEST_BODIES["package.json/declaring"])
        self.assertEqual(
            stack_node.evidence(self.root),
            (2 + stack_common.MANIFEST_FLOOR) * stack_common.MANIFEST_MULTIPLIER)

    def test_a_tooling_only_manifest_scales_nothing(self):
        # The other half of the rule, and the one the reproduction needed: the
        # same two files under a `package.json` that carries husky and prettier
        # score exactly what the files are worth. `has_manifest` still finds it
        # -- the walk is unchanged -- so a regression here would be the
        # CLASSIFIER giving up, not the walk.
        write(self.root, "src/a.mjs", "export function a() {}\n")
        write(self.root, "src/b.ts", "export function b() {}\n")
        write(self.root, "package.json", MANIFEST_BODIES["package.json/tooling"])
        self.assertTrue(stack_common.has_manifest(self.root, stack_node.MANIFESTS))
        self.assertEqual(stack_node.evidence(self.root), 2)

    def test_the_multiplier_cancels_when_both_stacks_declare_themselves(self):
        # Row 5 of the table, stated as the property that makes it work: when
        # both stacks carry a manifest the multiplier is on both sides, so the
        # file counts decide -- which is what a polyglot repo that declares
        # both halves honestly wants.
        self._build(self.root, 30, 6, ".ts",
                    (("pyproject.toml", "declaring"), ("package.json", "declaring")))
        scores = dict(rank_risk.stack_evidence(self.root))
        self.assertEqual(scores["python"] / scores["node"], 35 / 11)
        self.assertEqual(rank_risk.detect_stack(self.root).STACK_NAME, "python")

    def test_an_unclaimed_repo_says_so_and_exits_cleanly(self):
        # Row 8. Nothing to rank is a legitimate answer; it must be a
        # well-formed empty report and a line saying WHY, never an error and
        # never a silent `stack=python` that reads like a verdict.
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            code = rank_risk.main([self.root])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["units_discovered"], 0)
        self.assertIn("no stack claims", err.getvalue())


class TestNodeGuardMatchesTheFilter(unittest.TestCase):
    """`io_guard.js` and the marker tables must have ONE answer, and it is checkable.

    The split this skill runs on is FILTER then ENFORCEMENT: `stack_node.py`
    ranks and declines statically, `io_guard.js` blocks at run time. That split
    only means something while the two layers agree about what counts as, say,
    filesystem I/O. A marker the filter declines on but the guard cannot
    intercept is a two-layer hole -- the Python pair shipped one for two review
    rounds, where `pkgutil.get_data` was invisible to the filter (the read
    lives in a module the analysed unit merely imports) AND exempted by the
    guard's import rule.

    So the guard publishes what it intercepts, and this asserts the three maps
    PARTITION the marker tables exactly: every marker in exactly one map, and
    no map naming a marker the filter has never heard of.
    """

    @classmethod
    def setUpClass(cls):
        cls.guard = os.path.join(_HERE, "io_guard.js")
        cls.node = shutil.which("node")
        cls.maps = None
        if not cls.node or not os.path.isfile(cls.guard):
            return
        # `disarm()` first: requiring the guard ARMS it (that is the contract
        # `--require` needs), and an armed process printing JSON would be
        # deciding provenance on its own stdout write.
        script = ("const g = require(process.argv[1]); g.disarm();"
                  "console.log(JSON.stringify({"
                  "intercepts: Object.keys(g.FILTER_MARKER_INTERCEPTS),"
                  "partial: Object.keys(g.PARTIALLY_INTERCEPTED),"
                  "missing: Object.keys(g.NOT_INTERCEPTED),"
                  "groups: g.GROUPS}));")
        run = subprocess.run([cls.node, "-e", script, "--", cls.guard],
                             capture_output=True, text=True, timeout=30)
        if run.returncode == 0:
            cls.maps = json.loads(run.stdout)

    def setUp(self):
        if self.maps is None:
            sys.stderr.write(
                "\nSKIP: no usable `node` for %s -- io_guard.js's intercept "
                "tables are NOT compared against the marker tables on this "
                "machine. Install node 18+ to run it.\n" % self.guard)
            self.skipTest("node is not available")

    def markers(self):
        found = set()
        for table in (stack_node.CONTROLLABLE, stack_node.UNCONTROLLABLE):
            for names in table.values():
                found.update(names)
        return found

    def test_the_three_maps_partition_the_marker_tables(self):
        intercepts = set(self.maps["intercepts"])
        partial = set(self.maps["partial"])
        missing = set(self.maps["missing"])
        markers = self.markers()
        self.assertEqual(intercepts & partial, set())
        self.assertEqual(intercepts & missing, set())
        self.assertEqual(partial & missing, set())
        covered = intercepts | partial | missing
        self.assertEqual(
            markers - covered, set(),
            "marker(s) the FILTER declines on that the guard's tables never "
            "mention -- a marker with no intercept is a silent two-layer hole")
        self.assertEqual(
            covered - markers, set(),
            "the guard claims to intercept marker(s) the filter has never "
            "heard of; one of the two layers has drifted")

    def test_the_guard_uses_the_filters_groups_verbatim(self):
        self.assertEqual(sorted(self.maps["groups"]),
                         sorted(list(stack_node.CONTROLLABLE)
                                + list(stack_node.UNCONTROLLABLE)))

    def test_the_documented_command_is_reachable_from_the_target_repo(self):
        # An agent runs from the TARGET repo, so a skill-relative path never
        # resolves. `asset-paths.sh` enforces this for SKILL.md; the guard's
        # own header prints the command too, and it has to obey the same rule.
        with open(self.guard, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('node --require "$SKILL_DIR/assets/io_guard.js"', text)
        self.assertIn("TEST_SAFETY_NET_TIER=1", text)
        self.assertIn("TEST_SAFETY_NET_ALLOW=", text)


class TestManifestContent(unittest.TestCase):
    """`declaring` vs `tooling`: what a manifest SAYS, not that it exists.

    Existence was doing work content should do. `has_manifest` answers "is
    there a claim here at all", and every root `package.json` says yes --
    including the one a Python repo keeps for `husky` and `prettier`, which is
    the commonest reason a Python repo has one. The classifier is the second
    question: does this file declare how the repo RUNS (an entry point, runtime
    dependencies, a build) or only how it is EDITED (a formatter, a git hook)?

    Derived rather than tuned: no constant moves when a row is added here, and
    the same rule runs on both sides -- a `pyproject.toml` carrying only
    `[tool.ruff]` scales Python's claim exactly as little as a husky
    `package.json` scales node's.
    """

    stack_python = rank_risk.stack_python

    def assertDeclaring(self, stack, name, text):
        self.assertEqual(stack.classify_manifest(name, text), "declaring",
                         "%s: %r" % (name, text))

    def assertTooling(self, stack, name, text):
        self.assertEqual(stack.classify_manifest(name, text), "tooling",
                         "%s: %r" % (name, text))

    # ── node ────────────────────────────────────────────────────────────
    def test_a_package_json_that_declares_how_the_code_is_entered(self):
        for text in ('{"main": "index.js"}',
                     '{"exports": {".": "./index.js"}}',
                     '{"bin": {"app": "cli.js"}}',
                     '{"module": "dist/index.mjs"}',
                     '{"browser": "dist/web.js"}',
                     '{"type": "module"}',
                     '{"workspaces": ["packages/*"]}',
                     '{"dependencies": {"express": "^4"}}',
                     '{"peerDependencies": {"react": "^18"}}',
                     '{"scripts": {"build": "tsc -p ."}}',
                     '{"scripts": {"start": "node server.js"}}',
                     '{"scripts": {"dev": "vite"}}'):
            self.assertDeclaring(stack_node, "package.json", text)

    def test_a_package_json_that_only_says_how_the_repo_is_edited(self):
        for text in ('{}',
                     '{"name": "app", "version": "1.0.0"}',
                     '{"private": true, "devDependencies": {"husky": "^9"}}',
                     '{"devDependencies": {"prettier": "^3", "eslint": "^9"}}',
                     '{"scripts": {"prepare": "husky"}}',
                     '{"scripts": {"lint": "eslint ."}, '
                     '"devDependencies": {"eslint": "^9"}}',
                     '{"scripts": {"build": "prettier --write ."}}',
                     '{"dependencies": {}, "devDependencies": {"husky": "^9"}}'):
            self.assertTooling(stack_node, "package.json", text)

    def test_a_build_script_is_read_by_WHAT_IT_RUNS(self):
        # `scripts.build` is the one declaring signal whose VALUE matters: a
        # Python repo that fronts `prettier` with a `build` script is still a
        # Python repo, and a node repo that compiles itself is not.
        self.assertDeclaring(stack_node, "package.json",
                             '{"scripts": {"build": "tsc && node scripts/bundle.js"}}')
        self.assertTooling(stack_node, "package.json",
                           '{"scripts": {"build": "echo nothing to build"}}')

    def test_an_unparseable_package_json_is_read_as_text(self):
        # JSON with a trailing comma is not JSON, and a manifest this stack
        # cannot parse must not silently become "no claim". The key scan is the
        # fallback, and it is the same key list.
        self.assertDeclaring(stack_node, "package.json",
                             '{\n  "main": "index.js",\n}\n')
        self.assertTooling(stack_node, "package.json", "not json at all")

    def test_a_tsconfig_declares_by_existing(self):
        # Unlike `package.json`, whose commonest reason to exist in a Python
        # repo is tooling, a `tsconfig.json`/`deno.json` exists only to say
        # "there is TypeScript/Deno source here to compile or run".
        for name in ("tsconfig.json", "jsconfig.json", "deno.json", "deno.jsonc"):
            self.assertDeclaring(stack_node, name, "{}")

    # ── python ──────────────────────────────────────────────────────────
    def test_a_pyproject_that_declares_a_distribution(self):
        for text in ('[project]\nname = "srv"\n',
                     '[tool.poetry]\nname = "srv"\n',
                     '[build-system]\nrequires = ["setuptools"]\n'):
            self.assertDeclaring(self.stack_python, "pyproject.toml", text)

    def test_a_pyproject_that_is_only_a_linters_config(self):
        for text in ("[tool.ruff]\nline-length = 100\n",
                     "[tool.black]\nline-length = 88\n",
                     "[tool.ruff.lint]\nselect = [\"E\"]\n",
                     ""):
            self.assertTooling(self.stack_python, "pyproject.toml", text)

    def test_a_setup_cfg_that_is_only_a_flake8_config(self):
        # The Python mirror of the husky `package.json`, and just as common.
        self.assertTooling(self.stack_python, "setup.cfg",
                           "[flake8]\nmax-line-length = 100\n")
        self.assertDeclaring(self.stack_python, "setup.cfg",
                             "[metadata]\nname = srv\n")

    def test_a_requirements_file_naming_only_linters_declares_nothing(self):
        self.assertTooling(self.stack_python, "requirements.txt",
                           "black==24.1.0\nruff\n# pinned\n")
        self.assertTooling(self.stack_python, "requirements.txt", "\n# empty\n")
        self.assertDeclaring(self.stack_python, "requirements.txt",
                             "flask==3.0.0\nruff\n")

    def test_the_manifests_that_exist_only_to_declare(self):
        # What is left in this set after N3: two names with no tooling-only
        # form at all. A `setup.py` exists to build a distribution and a
        # `Pipfile` to pin one's environment; neither name is reachable by
        # accident in a repo that is not Python.
        for name, text in (("setup.py", "from setuptools import setup\nsetup()\n"),
                           ("Pipfile", "[packages]\n")):
            self.assertDeclaring(self.stack_python, name, text)

    def test_a_conda_environment_declares_by_its_dependencies(self):
        # N3. `environment.yaml` returned "declaring" without reading a byte,
        # so a Kubernetes-flavoured `config/environment.yaml` holding
        # `name: prod` flipped a JavaScript repo to python -- the `has_manifest`
        # mistake, for one filename. BOTH SPELLINGS ARE TESTED because they are
        # one manifest kind: a rule that read `.yaml` and not `.yml` would
        # leave the same flip one rename away.
        conda = ("name: srv\nchannels:\n  - conda-forge\n"
                 "dependencies:\n  - python=3.11\n  - django\n")
        for name in ("environment.yml", "environment.yaml"):
            with self.subTest(name):
                self.assertDeclaring(self.stack_python, name, conda)
                self.assertTooling(self.stack_python, name, "name: prod\nreplicas: 3\n")
                self.assertTooling(self.stack_python, name, "")

    def test_a_manage_py_declares_by_booting_django(self):
        # The other half of N3. The docstring's justification for the name --
        # "it names a settings module and boots a framework" -- is a claim
        # about CONTENT, and now something checks it. A `manage.py` that is
        # merely a script called `manage.py` declares nothing.
        for text in ("import os, sys\n"
                     "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'srv.settings')\n",
                     "from django.core.management import execute_from_command_line\n"):
            self.assertDeclaring(self.stack_python, "manage.py", text)
        for text in ('# not django\nprint("hi")\n', "", "import click\n"):
            self.assertTooling(self.stack_python, "manage.py", text)

    # ── both, through the score ─────────────────────────────────────────
    def test_an_unknown_manifest_name_never_silently_declares(self):
        # A stack that grows a MANIFESTS entry and forgets the classifier would
        # otherwise get the old existence behaviour back for that one name.
        for stack in (stack_node, self.stack_python):
            self.assertTooling(stack, "not-a-manifest.toml", "whatever")

    def test_every_shipped_manifest_name_is_classifiable(self):
        # The other direction of the same rule: every name a stack calls a
        # manifest must have an answer here, or it can never scale a claim.
        for stack, sample in ((stack_node, '{"main": "i.js"}'),
                              (self.stack_python, "[project]\nname = \"s\"\n")):
            for name in stack.MANIFESTS:
                with self.subTest(stack=stack.STACK_NAME, name=name):
                    self.assertIn(stack.classify_manifest(name, sample),
                                  ("declaring", "tooling"))

    def test_the_reproduction_task_4_left_open(self):
        # 8 `.py`, 3 `.js`, a husky `package.json`: node 16 to python 8, and
        # the skill ranked three files while ignoring eight. No
        # `(files + floor) * multiplier` shape fixes this -- the counts are
        # identical to a fresh node project's -- so the manifest's content has
        # to decide, and it does.
        root = tempfile.mkdtemp(prefix="tsn-repro8-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for i in range(8):
            write(root, "srv/mod%d.py" % i, "def go%d():\n    return 1\n" % i)
        for i in range(3):
            write(root, "web/m%d.js" % i, "export function go%d() {}\n" % i)
        write(root, "package.json", MANIFEST_BODIES["package.json/tooling"])
        stacks = rank_risk.STACKS
        rank_risk.STACKS = [stack_node, rank_risk.stack_python]
        self.addCleanup(setattr, rank_risk, "STACKS", stacks)
        scores = dict(rank_risk.stack_evidence(root))
        self.assertEqual(scores, {"python": 8, "node": 3})
        self.assertEqual(rank_risk.detect_stack(root).STACK_NAME, "python")


class TestAmbiguityMargin(unittest.TestCase):
    """`AMBIGUITY_MARGIN` shipped untested. Both directions, derived from it.

    The numbers below are computed FROM the constant rather than written
    against today's value, so moving the margin moves the test with it and a
    change that makes the detector guess more (or refuse more) has to be a
    deliberate edit to the constant.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tsn-margin-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self._stacks = rank_risk.STACKS
        rank_risk.STACKS = [stack_node, rank_risk.stack_python]
        self.addCleanup(setattr, rank_risk, "STACKS", self._stacks)

    def _pair(self, py, node):
        for i in range(py):
            write(self.root, "srv/mod%d.py" % i, "def go%d():\n    return 1\n" % i)
        for i in range(node):
            write(self.root, "web/mod%d.js" % i, "export function go%d() {}\n" % i)

    def test_a_pair_inside_the_margin_is_reported_not_guessed(self):
        leader = 20
        gap = max(1, int(leader * rank_risk.AMBIGUITY_MARGIN))
        self._pair(leader, leader - gap)           # exactly ON the boundary: <= is ambiguous
        with self.assertRaises(rank_risk.AmbiguousStack) as caught:
            rank_risk.detect_stack(self.root)
        self.assertEqual(dict(caught.exception.scores),
                         {"python": leader, "node": leader - gap})

    def test_a_pair_outside_the_margin_is_decided(self):
        leader = 20
        gap = int(leader * rank_risk.AMBIGUITY_MARGIN) + 1
        self._pair(leader, leader - gap)
        self.assertEqual(rank_risk.detect_stack(self.root).STACK_NAME, "python")


# ── Triage ───────────────────────────────────────────────────────────────

class TriageCase(NodeCase):
    def tier(self, rel, text, name):
        write(self.root, rel, text)
        units, _ = stack_node.discover_units(self.root)
        unit = next(u for u in units if u["path"] == rel and u["name"] == name)
        return stack_node.triage(self.root, unit)


class TestTiers(TriageCase):
    def test_tier_1_no_markers_directly_callable(self):
        tier, reason = self.tier("src/pure.js",
                                 "export function add(a, b) {\n  return a + b;\n}\n", "add")
        self.assertEqual(tier, 1)
        self.assertEqual(reason, "no I/O markers; directly callable")

    def test_tier_2_controllable_only(self):
        for rel, src, name, group in (
                ("src/clock.js", "export function stamp(x) { return Date.now() + x; }\n",
                 "stamp", "clock"),
                ("src/date.js", "export function mk() { return new Date(0); }\n",
                 "mk", "clock"),
                ("src/rand.js", "export function id() { return Math.random(); }\n",
                 "id", "randomness"),
                ("src/env.js", "export function home() { return process.env.HOME; }\n",
                 "home", "environment"),
                ("src/fs.js",
                 'import fs from "node:fs";\nexport function save(p) { fs.writeFileSync(p, "x"); }\n',
                 "save", "filesystem")):
            with self.subTest(name=name):
                tier, reason = self.tier(rel, src, name)
                self.assertEqual(tier, 2)
                self.assertIn(group, reason)
                self.assertIn("pin at a wider boundary", reason)

    def test_tier_3_uncontrollable_inside_the_unit(self):
        for rel, src, name, group in (
                ("src/net.js",
                 'import { request } from "https";\nexport function ping(h) { return request(h); }\n',
                 "ping", "network"),
                ("src/proc.js",
                 'import { spawn } from "node:child_process";\n'
                 'export function run(c) { return spawn(c); }\n',
                 "run", "subprocess"),
                ("src/db.js",
                 'import { MongoClient } from "mongodb";\n'
                 'export function conn(u) { return new MongoClient(u); }\n',
                 "conn", "database")):
            with self.subTest(name=name):
                tier, reason = self.tier(rel, src, name)
                self.assertEqual(tier, 3)
                self.assertIn(group, reason)
                self.assertIn("needs a seam", reason)

    def test_tier_4_uncontrollable_at_the_boundary(self):
        # The Python stack's tier 4 in node's spelling: the I/O happens when
        # the module is IMPORTED, so the harness takes it before any test body
        # runs and no fixture exists yet to control it.
        tier, reason = self.tier(
            "src/boot.js",
            'import axios from "axios";\n\n'
            'const DATA = axios.get("http://x");\n\n'
            "export function get() { return DATA; }\n", "get")
        self.assertEqual(tier, 4)
        self.assertIn("network I/O at import time", reason)
        self.assertIn("not reachable", reason)

    def test_import_time_controllable_floors_every_unit_at_tier_3(self):
        # Mirrors the Python stack's import-time floor. `get` touches nothing
        # itself and would be Tier 1; the module read a file to define CFG.
        write(self.root, "src/cfg.js",
              'import fs from "fs";\n\n'
              'const CFG = JSON.parse(fs.readFileSync("/etc/a.json"));\n\n'
              "export function get() { return CFG; }\n"
              "export function untouched() { return 1; }\n")
        units, _ = stack_node.discover_units(self.root)
        for unit in units:
            with self.subTest(name=unit["name"]):
                tier, reason = stack_node.triage(self.root, unit)
                self.assertEqual(tier, 3)
                self.assertIn("filesystem I/O at import time", reason)
                self.assertIn("a fixture runs too late", reason)

    def test_an_import_statement_alone_is_not_import_time_io(self):
        # The floor above must not fire on the IMPORT itself: `import fs` reads
        # no file, and reading the specifier as a hit would floor every unit in
        # every file that imports anything. This is the node counterpart of
        # `stack_python._module_level_regions` skipping `ast.Import`.
        tier, _reason = self.tier(
            "src/only_import.js",
            'import fs from "node:fs";\nimport { spawn } from "child_process";\n\n'
            "export function add(a, b) { return a + b; }\n", "add")
        self.assertEqual(tier, 1)

    def test_a_function_body_does_not_run_at_import_time(self):
        # A sibling function full of I/O must not floor the whole file: its
        # body runs when it is CALLED. Only the unit that reaches it inherits.
        write(self.root, "src/two.js",
              'import fs from "fs";\n\n'
              "export function reader(p) { return fs.readFileSync(p); }\n\n"
              "export function pure(a) { return a + 1; }\n")
        by = {u["name"]: u for u in stack_node.discover_units(self.root)[0]}
        self.assertEqual(stack_node.triage(self.root, by["pure"])[0], 1)
        self.assertEqual(stack_node.triage(self.root, by["reader"])[0], 2)

    def test_a_same_file_helper_is_followed_transitively(self):
        tier, reason = self.tier(
            "src/wrap.js",
            'import fs from "fs";\n\n'
            "function _write(p, d) { fs.writeFileSync(p, d); }\n\n"
            "export function save(p, d) { return _write(p, d); }\n", "save")
        self.assertEqual(tier, 2)
        self.assertIn("via _write", reason)

    def test_an_inline_require_is_resolved_through_its_specifier(self):
        # Binds no local name at all, so the alias map cannot help: the
        # specifier is read out of the string-kept text at the same offsets.
        tier, reason = self.tier(
            "src/sh.js",
            'export function sh(c) { return require("child_process").execSync(c); }\n',
            "sh")
        self.assertEqual(tier, 3)
        self.assertIn("subprocess", reason)

    def test_both_spellings_of_a_builtin_tier_identically(self):
        # `node:fs` and `fs` are ONE marker, canonicalised, so the table holds
        # one entry per marker instead of two spellings of half of them.
        a = self.tier("src/p.js",
                      'import fs from "fs";\nexport function s(p) { fs.writeFileSync(p, 1); }\n', "s")
        b = self.tier("src/q.js",
                      'import fs from "node:fs";\nexport function t(p) { fs.writeFileSync(p, 1); }\n', "t")
        self.assertEqual(a[0], b[0])
        self.assertEqual(a[0], 2)

    def test_an_aliased_namespace_import_still_resolves(self):
        tier, _ = self.tier(
            "src/ns.js",
            'import * as cp from "node:child_process";\n'
            "export function run(c) { return cp.execSync(c); }\n", "run")
        self.assertEqual(tier, 3)

    def test_a_destructured_require_still_resolves(self):
        tier, _ = self.tier(
            "src/req.js",
            'const { readFileSync } = require("fs");\n'
            "export function read(p) { return readFileSync(p); }\n", "read")
        self.assertEqual(tier, 2)

    def test_a_marker_inside_a_comment_or_a_string_does_not_count(self):
        # The stripper is the whole defence here: a unit is NOT tiered up
        # because a comment mentions `fs`, because a string spells
        # "child_process", or because a regex literal contains `Math.random`.
        tier, reason = self.tier(
            "src/quiet.js",
            "export function add(a, b) {\n"
            "  // fs.writeFileSync(a, b) -- used to, not any more\n"
            "  /* spawn(a) */\n"
            '  const label = "child_process";\n'
            "  const re = /Math.random/g;\n"
            "  const t = `axios.get(${a})`;\n"
            "  return a + b + label.length + Number(re.source.length) + t.length;\n"
            "}\n", "add")
        self.assertEqual(tier, 1, reason)

    def test_a_commented_out_import_binds_nothing_for_triage(self):
        tier, _ = self.tier(
            "src/dead.js",
            '// import { execSync } from "child_process";\n'
            "export function add(a, b) { return a + b; }\n", "add")
        self.assertEqual(tier, 1)

    def test_a_class_field_initialiser_is_not_import_time(self):
        # Where node and Python genuinely differ: a JS class field runs at
        # CONSTRUCTION, not when the class is defined, so a class body is cut
        # out of the import-time region whole. `stack_python` treats class-body
        # statements as import-time because Python evaluates them then.
        write(self.root, "src/cls.js",
              'import fs from "fs";\n\n'
              "export class Store {\n  data = fs.readFileSync('/x');\n}\n\n"
              "export function pure(a) { return a; }\n")
        by = {u["name"]: u for u in stack_node.discover_units(self.root)[0]}
        self.assertEqual(stack_node.triage(self.root, by["pure"])[0], 1)
        self.assertEqual(stack_node.triage(self.root, by["Store"])[0], 2)

    def test_a_unit_that_vanished_since_discovery_is_tier_4(self):
        write(self.root, "src/a.js", "export function gone() {}\n")
        unit = {"id": "src/a.js::ghost", "path": "src/a.js", "name": "ghost",
                "lineno": 1, "kind": "function"}
        self.assertEqual(stack_node.triage(self.root, unit),
                         (4, "unit not found on re-read"))


# ── The marker tables, derived rather than remembered ────────────────────

# node 22.18's `module.builtinModules`, minus the `_`-prefixed internals node
# documents as private. Frozen so this test still asserts something on a
# machine with no node installed; when node IS installed the live list is used
# instead, which is what makes a node upgrade able to fail this.
FROZEN_BUILTIN_MODULES = (
    "assert assert/strict async_hooks buffer child_process cluster console "
    "constants crypto dgram diagnostics_channel dns dns/promises domain events "
    "fs fs/promises http http2 https inspector inspector/promises module net os "
    "path path/posix path/win32 perf_hooks process punycode querystring readline "
    "readline/promises repl stream stream/consumers stream/promises stream/web "
    "string_decoder sys timers timers/promises tls trace_events tty url util "
    "util/types v8 vm wasi worker_threads zlib").split()

# Frozen names a SUPPORTED node older than the newest does not have yet, with
# the first major that does. The skill supports node LTS 18/20/22/24/26, and
# the frozen list is the union a current node carries; checking it against
# node 18 as if every name were universal failed on `inspector/promises`,
# which that line never had. A name here is still checked on every node from
# its major on -- this narrows WHEN a name is required, never WHETHER.
# Measured against the real `module.builtinModules` of node 18, 20, 22, 24, 26.
FROZEN_SINCE_MAJOR = {"inspector/promises": 20}

# Every builtin that is NOT in a marker group, each with the reason it performs
# no I/O in any group this filter tracks. The stdio family is one decision
# taken five times: `stack_python` marks neither `input()` nor `sys.stdout`, so
# marking node's terminal modules would put the two stacks' tables in
# disagreement about what a group MEANS. Terminal I/O is left to the runtime
# guard, which is the enforcement.
ALLOWED_UNMARKED = {
    "assert": "assertions over values already in memory",
    "async_hooks": "in-process instrumentation of the async lifecycle",
    "buffer": "bytes in memory",
    "console": "terminal output; no tracked group (see the stdio note)",
    "constants": "numeric constants",
    "diagnostics_channel": "in-process pub/sub for instrumentation",
    "domain": "deprecated error-context tracking; no external effect",
    "events": "in-process emitter",
    "module": "the loader reads files, but every user-facing entry point that "
              "does is `import`/`require` itself, which cannot be marked "
              "without marking every file in the repo",
    "path": "pure string algebra over paths; touches no filesystem. This is "
            "why node needs no equivalent of stack_python.IMPORT_TIME_INERT",
    "punycode": "string encoding",
    "querystring": "string encoding",
    "readline": "terminal input; no tracked group (see the stdio note)",
    "repl": "an interactive terminal; no tracked group (see the stdio note)",
    "sea": "reads assets embedded in the executable, not the filesystem",
    "stream": "plumbing; the endpoints it connects are what touch the world",
    "string_decoder": "bytes to string, in memory",
    "sys": "deprecated alias of util",
    "test": "the test runner; a unit under test does not import it",
    "tty": "terminal device queries; no tracked group (see the stdio note)",
    "url": "string parsing; opens nothing",
    "util": "formatting and promisification",
    "vm": "compiles and runs code in-process; performs no I/O of its own",
    "zlib": "compression over buffers in memory",
}


def _live_node_major():
    """The installed node's major version, or None when it cannot be read."""
    try:
        r = subprocess.run(["node", "-p", "process.versions.node.split('.')[0]"],
                           capture_output=True, text=True, timeout=30)
        return int(r.stdout.strip()) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _live_builtin_modules():
    """node's own `module.builtinModules`, or None when node is not installed."""
    try:
        r = subprocess.run(
            ["node", "-e",
             "console.log(require('module').builtinModules.join(','))"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [name.strip() for name in r.stdout.split(",") if name.strip()]


class TestMarkerTableCompleteness(unittest.TestCase):
    """Every I/O-performing builtin is marked, DERIVED from node's own list.

    The counterpart of the Python suite's `dir(os)`-derived tests, and it
    exists for the same finding: a table that lists `os.remove` but not
    `os.rename`, or `os.execv` but not `os.execlp`, splits equally-real I/O
    across Tier 3 and Tier 1 "no I/O markers; directly callable" -- and nobody
    notices, because both halves look deliberate.

    THIS TEST IS SUPPOSED TO FAIL WHEN NODE ADDS A BUILTIN MODULE. That failure
    is the mechanism: it forces someone to put the new module in a marker group
    or in `ALLOWED_UNMARKED` with a reason, instead of it silently landing in
    Tier 1. Do NOT "fix" such a failure by widening the derivation or by
    deleting names from the frozen list.
    """

    def classify(self, mod):
        """The group `mod` is marked in, "allowed", or None."""
        for controllable, table in ((False, stack_node.UNCONTROLLABLE),
                                    (True, stack_node.CONTROLLABLE)):
            for group, markers in table.items():
                for marker in markers:
                    canon = stack_node._canon(marker)
                    if (canon == mod or canon.startswith(mod + ".")
                            or canon.startswith(mod + "/")):
                        return group
        if mod in ALLOWED_UNMARKED:
            return "allowed"
        if "/" in mod:                       # `path/posix` IS `path`
            return self.classify(mod.rsplit("/", 1)[0])
        return None

    def test_every_builtin_module_is_marked_or_allow_listed(self):
        derived = _live_builtin_modules() or list(FROZEN_BUILTIN_MODULES)
        unclassified = sorted(
            mod for mod in
            {stack_node._canon(m) for m in derived if not m.startswith("_")}
            if self.classify(mod) is None)
        self.assertEqual(
            unclassified, [],
            "node builtin modules absent from both the marker tables and "
            "ALLOWED_UNMARKED: %s" % unclassified)

    def test_the_frozen_list_still_matches_the_installed_node(self):
        # The frozen list is the fallback when node is absent; if it drifts
        # from a real node, the offline arm of the test above is asserting
        # about a runtime that no longer exists.
        live = _live_builtin_modules()
        if live is None:
            self.skipTest("node is not installed")
        major = _live_node_major()
        required = {n for n in FROZEN_BUILTIN_MODULES
                    if major is None or FROZEN_SINCE_MAJOR.get(n, 0) <= major}
        missing = sorted(required - {stack_node._canon(m) for m in live})
        self.assertEqual(missing, [],
                         "frozen builtins this node no longer has: %s" % missing)

    def test_the_families_the_python_branch_got_wrong_are_whole_here(self):
        # The behavioural half, on the siblings a hand-written table drops:
        # `dns` and `tls` are as much network as `http` is, `cluster` and
        # `worker_threads` start execution the way `child_process` does, and
        # none of the four was in the first draft of these tables.
        for mod, group in (("dns", "network"), ("tls", "network"),
                           ("http2", "network"), ("inspector", "network"),
                           ("cluster", "subprocess"), ("worker_threads", "subprocess"),
                           ("wasi", "filesystem"), ("trace_events", "filesystem"),
                           ("perf_hooks", "clock"), ("os", "environment")):
            with self.subTest(module=mod):
                self.assertEqual(self.classify(mod), group)

    def test_the_marker_groups_are_the_seven_the_design_names(self):
        self.assertEqual(sorted(stack_node.CONTROLLABLE),
                         ["clock", "environment", "filesystem", "randomness"])
        self.assertEqual(sorted(stack_node.UNCONTROLLABLE),
                         ["database", "network", "subprocess"])
        self.assertEqual(sorted(stack_node.CONTROLLABLE),
                         sorted(rank_risk.stack_python.CONTROLLABLE))
        self.assertEqual(sorted(stack_node.UNCONTROLLABLE),
                         sorted(rank_risk.stack_python.UNCONTROLLABLE))


if __name__ == "__main__":
    unittest.main()
