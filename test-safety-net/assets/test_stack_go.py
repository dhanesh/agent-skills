#!/usr/bin/env python3
"""Tests for the Go stack.

The governing bar is the one every stack answers to: static triage is a FILTER
and the runtime guard is the enforcement, so a reader may be wrong in the
direction of MORE reported work and never in the direction of less. Each
negative below pins one of two things -- a form that must NOT be read (a
phantom unit, or a coverage credit nobody earned) or a form that must not
silently disappear (a unit whose missing test is the bug).

Tests that need a real `go` skip VISIBLY when there is none: a skip reads
`OK (skipped=N)`, never a silent pass.
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


stack_go = _load("stack_go")


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class GoCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tsn-go-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)


# ── The stripper ─────────────────────────────────────────────────────────

class TestStripper(unittest.TestCase):
    def test_offsets_and_newlines_are_preserved(self):
        text = ('package x // trailing\n/* block\n spans */\nvar s = "str"\n'
                'var r = `raw\nmulti`\nvar c = \'q\'\n')
        out = stack_go.strip_noncode(text)
        self.assertEqual(len(out), len(text))
        self.assertEqual([i for i, ch in enumerate(out) if ch == "\n"],
                         [i for i, ch in enumerate(text) if ch == "\n"])
        self.assertIn("package x", out)
        for gone in ("trailing", "block", "spans", "str", "raw", "multi", "q"):
            self.assertNotIn(gone, out)

    def test_a_raw_string_crosses_newlines(self):
        text = "var doc = `\nfunc Hidden() {}\n`\nfunc Visible() {}\n"
        out = stack_go.strip_noncode(text)
        self.assertNotIn("Hidden", out)
        self.assertIn("Visible", out)

    def test_an_interpreted_string_never_crosses_a_newline(self):
        # Unterminated in the source, so the file does not compile -- and the
        # damage is bounded to its own line rather than blanking the rest of
        # the file, which is the direction this reader is allowed to fail in.
        text = 'var s = "abc\nfunc Visible() {}\n'
        self.assertIn("Visible", stack_go.strip_noncode(text))

    def test_a_rune_holding_a_quote_does_not_open_a_string(self):
        text = "var q = '\"'\nfunc Visible() {}\n"
        self.assertIn("Visible", stack_go.strip_noncode(text))

    def test_an_escaped_quote_does_not_close_a_string(self):
        text = 'var s = "a\\"b func Hidden()"\nfunc Visible() {}\n'
        out = stack_go.strip_noncode(text)
        self.assertNotIn("Hidden", out)
        self.assertIn("Visible", out)

    def test_comment_markers_inside_a_string_are_not_comments(self):
        text = 'var u = "http://x/*y"\nfunc Visible() {}\n'
        self.assertIn("Visible", stack_go.strip_noncode(text))

    def test_keep_strings_keeps_literals_and_still_blanks_comments(self):
        text = 'import "example.com/m/pkg" // import "example.com/m/other"\n'
        out = stack_go.strip_noncode(text, keep_strings=True)
        self.assertIn('"example.com/m/pkg"', out)
        self.assertNotIn("other", out)


# ── Files ────────────────────────────────────────────────────────────────

class TestFiles(GoCase):
    def test_iter_source_files_prunes_what_the_go_tool_ignores(self):
        for rel in ("a.go", "a_test.go", "vendor/v/v.go", "testdata/t.go",
                    "_scratch/s.go", ".hidden/h.go", "_ignored.go", ".dot.go",
                    "pkg/b.go", "pkg/c.txt", "node_modules/n/n.go"):
            write(self.root, rel, "package x\n")
        write(self.root, "ign.go", "//go:build ignore\n\npackage main\n")
        write(self.root, "tools/tools.go", "//go:build tools\n\npackage tools\n")
        self.assertEqual(list(stack_go.iter_source_files(self.root)),
                         ["a.go", "pkg/b.go"])
        self.assertEqual(list(stack_go.iter_source_files(self.root, include_tests=True)),
                         ["a.go", "a_test.go", "pkg/b.go"])

    def test_a_constraint_after_the_package_clause_is_not_a_constraint(self):
        # Go reads build constraints only ABOVE the package clause; the same
        # line further down is an ordinary comment and excludes nothing.
        write(self.root, "late.go", "package x\n\n//go:build ignore\n")
        self.assertEqual(list(stack_go.iter_source_files(self.root)), ["late.go"])

    def test_a_platform_constraint_is_still_source(self):
        # `linux` code is code a person edits; only the two constraints that
        # mean "never part of this package" exclude a file.
        write(self.root, "sys_linux.go", "//go:build linux\n\npackage x\n")
        self.assertEqual(list(stack_go.iter_source_files(self.root)), ["sys_linux.go"])

    def test_is_test_path(self):
        self.assertTrue(stack_go.is_test_path("pkg/refund_test.go"))
        self.assertTrue(stack_go.is_test_path("pkg/testdata/fixture.go"))
        self.assertFalse(stack_go.is_test_path("pkg/refund.go"))
        self.assertFalse(stack_go.is_test_path("pkg/testing_helpers.go"))

    def test_is_test_for_is_the_same_directory(self):
        self.assertTrue(stack_go.is_test_for("pkg/a_test.go", "pkg/a.go"))
        self.assertTrue(stack_go.is_test_for("pkg/other_test.go", "pkg/a.go"))
        self.assertFalse(stack_go.is_test_for("pkg/sub/a_test.go", "pkg/a.go"))
        self.assertFalse(stack_go.is_test_for("test/pkg/a_test.go", "pkg/a.go"))

    def test_scope_files_is_the_package_directory(self):
        files = ["pkg/a.go", "pkg/b.go", "pkg/a_test.go", "pkg/sub/c.go", "other/d.go"]
        self.assertEqual(stack_go.scope_files("pkg/a.go", files),
                         ["pkg/a.go", "pkg/a_test.go", "pkg/b.go"])
        self.assertEqual(stack_go.scope_files("main.go", ["main.go", "util.go", "x/y.go"]),
                         ["main.go", "util.go"])


# ── Naming ───────────────────────────────────────────────────────────────

class TestNaming(unittest.TestCase):
    def test_module_of_is_the_package_directory(self):
        self.assertEqual(stack_go.module_of("internal/billing/refund.go"), "billing")
        self.assertEqual(stack_go.module_of("internal/billing/refund_test.go"), "billing")

    def test_a_root_file_answers_its_stem(self):
        # No root is passed, so the module path in go.mod is out of reach; the
        # stem under-credits a root package, which is the safe direction.
        self.assertEqual(stack_go.module_of("main.go"), "main")

    def test_name_pattern_is_a_whole_identifier(self):
        p = stack_go.name_pattern("Parse")
        self.assertTrue(p.search("x := Parse(s)"))
        self.assertFalse(p.search("x := ParseAll(s)"))
        self.assertFalse(p.search("x := reParse(s)"))

    def test_a_method_pattern_needs_its_receiver_type_named(self):
        p = stack_go.name_pattern("Report.String")
        # `buf.String()` is the commonest call in Go test files; crediting it
        # to every package's `T.String` would hide real gaps.
        self.assertFalse(p.search("func TestX(t *testing.T) { _ = buf.String() }"))
        self.assertTrue(p.search("r := Report{}\n_ = r.String()\n"))
        self.assertTrue(p.search("_ = r.String()\nvar r Report\n"))
        self.assertFalse(p.search("r := Report{}\n_ = r.Stringify()\n"))

    def test_path_pattern_is_bounded_by_the_import_path(self):
        p = stack_go.path_pattern("internal/billing/refund.go")
        self.assertTrue(p.search('import "example.com/m/internal/billing"'))
        self.assertTrue(p.search('import b "internal/billing"'))
        self.assertFalse(p.search('import "example.com/m/myinternal/billing"'))
        self.assertFalse(p.search('import "example.com/m/internal/billingx"'))
        self.assertFalse(p.search('import "example.com/m/internal/billing/sub"'))
        self.assertIsNone(stack_go.path_pattern("main.go"))

    def test_identifier_re_and_qualifiers_match_the_python_rule(self):
        self.assertEqual(stack_go.IDENTIFIER_RE.findall("2x + y_1"), ["y_1"])
        text = "sink.Write(b); mk().Write(b); Write(b)"
        quals = [stack_go.preceding_qualifier(text, m.start())
                 for m in stack_go.IDENTIFIER_RE.finditer(text) if m.group(0) == "Write"]
        self.assertEqual(quals, ["sink", "", None])


# ── Manifests and evidence ───────────────────────────────────────────────

class TestManifests(GoCase):
    def test_classify_manifest(self):
        self.assertEqual(stack_go.classify_manifest(
            "go.mod", "module example.com/m\n\ngo 1.22\n"), "declaring")
        self.assertEqual(stack_go.classify_manifest("go.mod", "go 1.22\n"), "tooling")
        self.assertEqual(stack_go.classify_manifest("go.work", "go 1.22\nuse ./a\n"),
                         "declaring")
        self.assertEqual(stack_go.classify_manifest("package.json", "{}"), "tooling")

    def test_evidence_scales_with_a_declaring_go_mod(self):
        write(self.root, "go.mod", "module example.com/m\n\ngo 1.22\n")
        for i in range(3):
            write(self.root, "pkg/f%d.go" % i, "package pkg\n")
        self.assertEqual(stack_go.evidence(self.root), (3 + 5) * 2)

    def test_a_tools_module_is_no_evidence_at_all(self):
        # The Go twin of a husky-only package.json: a Python repo that pins
        # its linters in `tools/` must not read as a Go repo.
        write(self.root, "tools/go.mod", "module example.com/tools\n\ngo 1.22\n")
        write(self.root, "tools/tools.go",
              '//go:build tools\n\npackage tools\n\nimport _ "golang.org/x/tools/cmd/stringer"\n')
        self.assertEqual(stack_go.evidence(self.root), 0)


# ── Import/binding grammar ───────────────────────────────────────────────

SRC = "internal/billing/refund.go"


class TestBindingGrammar(unittest.TestCase):
    def bind(self, text, ref_rel="cmd/tool/main.go"):
        return stack_go.module_bindings("billing", text, src_rel=SRC, ref_rel=ref_rel)

    def test_a_grouped_aliased_import_names_the_package_directory(self):
        text = 'package main\n\nimport (\n\t"fmt"\n\tb "example.com/m/internal/billing"\n)\n'
        self.assertEqual(self.bind(text), (("b",), ()))

    def test_an_unaliased_import_binds_the_directory_basename(self):
        self.assertEqual(self.bind('package main\nimport "example.com/m/internal/billing"\n'),
                         (("billing",), ()))

    def test_a_dot_import_binds_every_name(self):
        self.assertEqual(self.bind('package main\nimport . "example.com/m/internal/billing"\n'),
                         ((), ("*",)))

    def test_a_blank_import_binds_nothing(self):
        self.assertEqual(self.bind('package main\nimport _ "example.com/m/internal/billing"\n'),
                         ((), ()))

    def test_a_same_directory_file_binds_every_name(self):
        self.assertEqual(self.bind("package billing\n",
                                   ref_rel="internal/billing/refund_test.go"),
                         ((), ("*",)))

    def test_a_commented_out_import_binds_nothing(self):
        self.assertEqual(self.bind('package main\n// import "example.com/m/internal/billing"\n'),
                         ((), ()))

    def test_a_same_named_package_elsewhere_is_not_this_one(self):
        self.assertEqual(self.bind('package main\nimport "example.com/m/legacy/billing"\n'),
                         ((), ()))

    def reached(self, name, text, ref_rel="cmd/tool/main.go"):
        return stack_go.reached_through_module("billing", name, text,
                                               src_rel=SRC, ref_rel=ref_rel)

    def test_a_call_through_the_alias_is_reached(self):
        text = 'package main\nimport b "example.com/m/internal/billing"\nfunc main() { b.Refund(1) }\n'
        self.assertTrue(self.reached("Refund", text))

    def test_naming_it_without_calling_it_is_not_reached(self):
        # A function VALUE handed to a stub, or a mention in a string, is not
        # evidence the unit ran -- the same call-site rule both other stacks
        # arrived at.
        text = ('package main\nimport b "example.com/m/internal/billing"\n'
                'var f = b.Refund\nvar s = "b.Refund(1)"\n')
        self.assertFalse(self.reached("Refund", text))

    def test_a_same_package_test_reaches_by_a_bare_call(self):
        text = "package billing\nfunc TestRefund(t *testing.T) { Refund(1) }\n"
        self.assertTrue(self.reached("Refund", text, ref_rel="internal/billing/refund_test.go"))

    def test_a_method_is_reached_only_with_its_receiver_type_named(self):
        ref = "internal/billing/ledger_test.go"
        self.assertTrue(self.reached(
            "Ledger.Post", "package billing\nfunc T(t *testing.T) { l := Ledger{}; l.Post(1) }\n",
            ref_rel=ref))
        self.assertFalse(self.reached(
            "Ledger.Post", "package billing\nfunc T(t *testing.T) { q.Post(1) }\n", ref_rel=ref))


class TestInterfaceNames(unittest.TestCase):
    NAMES = ("STACK_NAME", "MANIFESTS", "evidence", "classify_manifest",
             "iter_source_files", "is_test_path", "is_test_for", "scope_files",
             "module_of", "name_pattern", "path_pattern", "IDENTIFIER_RE",
             "preceding_qualifier", "module_bindings", "reached_through_module")

    def test_supplies_the_language_reading_names(self):
        missing = [n for n in self.NAMES if not hasattr(stack_go, n)]
        self.assertEqual(missing, [])
        self.assertEqual(stack_go.STACK_NAME, "go")


if __name__ == "__main__":
    unittest.main()
