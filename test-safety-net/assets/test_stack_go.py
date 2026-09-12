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


class TestHeuristicDiscovery(GoCase):
    """What a test can call by name from outside the package, and nothing else."""

    def units(self):
        units, mode = stack_go.discover_units(self.root, precise=False)
        self.assertEqual(mode, "heuristic")
        return [(u["id"], u["kind"], u["lineno"]) for u in units]

    def test_an_exported_function(self):
        write(self.root, "pkg/p.go",
              "package pkg\n\n// Parse parses.\nfunc Parse(s string) int { return 1 }\n")
        self.assertEqual(self.units(), [("pkg/p.go::Parse", "function", 4)])

    def test_a_generic_function(self):
        write(self.root, "pkg/p.go",
              "package pkg\n\nfunc Map[T any, U any](xs []T, f func(T) U) []U { return nil }\n")
        self.assertEqual(self.units(), [("pkg/p.go::Map", "function", 3)])

    def test_a_multi_line_signature(self):
        write(self.root, "pkg/p.go",
              "package pkg\n\nfunc Parse(\n\ts string,\n) (int, error) {\n\treturn 0, nil\n}\n")
        self.assertEqual(self.units(), [("pkg/p.go::Parse", "function", 3)])

    def test_every_receiver_form_on_an_exported_type(self):
        write(self.root, "pkg/r.go",
              "package pkg\n\ntype Report struct{}\ntype Set[K comparable] struct{}\n\n"
              "func (r Report) Total() int { return 0 }\n"
              "func (r *Report) Add(n int) {}\n"
              "func (*Report) Reset() {}\n"
              "func (s *Set[K]) Put(k K) {}\n")
        self.assertEqual(self.units(), [
            ("pkg/r.go::Report.Add", "method", 7),
            ("pkg/r.go::Report.Reset", "method", 8),
            ("pkg/r.go::Report.Total", "method", 6),
            ("pkg/r.go::Set.Put", "method", 9),
        ])

    def test_an_exported_method_on_an_unexported_type_is_not_a_unit(self):
        # No importer can name `impl`, so nothing outside the package can call
        # `Do` on it by name; its behaviour is reached through whatever
        # exported function returns one, and THAT is the unit.
        write(self.root, "pkg/i.go",
              "package pkg\n\ntype impl struct{}\n\nfunc (i *impl) Do() {}\n"
              "func New() *impl { return &impl{} }\n")
        self.assertEqual(self.units(), [("pkg/i.go::New", "function", 6)])

    def test_unexported_main_and_init_are_not_units(self):
        write(self.root, "main.go",
              "package main\n\nfunc helper() {}\nfunc init() {}\nfunc main() { helper() }\n")
        self.assertEqual(self.units(), [])

    def test_a_function_type_and_a_function_variable_are_not_units(self):
        # Neither is a declaration with a body of its own, and Python discovers
        # no module-level constants either.
        #
        # The parameter and result types are EXPORTED on purpose. With
        # `func(int) error` a reader that has lost both its column-0 anchor and
        # its shape check reads receiver `int`, name `error` -- and the
        # lowercase `error` is then dropped by the exported check, so the test
        # passed for a reason that has nothing to do with either mechanism.
        # Measured: it stayed green with both removed. `Request`/`Response`
        # would come back as a phantom `Request.Response` unit instead.
        write(self.root, "pkg/t.go",
              "package pkg\n\ntype Request struct{}\ntype Response struct{}\n\n"
              "type HandlerFunc func(r Request) Response\n\n"
              "var Handler = func(r Request) Response { return Response{} }\n")
        self.assertEqual(self.units(), [])

    def test_a_closure_inside_an_exported_function_is_not_a_unit(self):
        write(self.root, "pkg/o.go",
              "package pkg\n\nfunc Outer() {\n\tgo func() {\n\t}()\n"
              "\tf := func(x int) int { return x }\n\t_ = f\n}\n")
        self.assertEqual(self.units(), [("pkg/o.go::Outer", "function", 3)])

    def test_names_in_comments_and_strings_are_not_units(self):
        write(self.root, "pkg/c.go",
              "package pkg\n\n// func Commented() {}\n/*\nfunc Blocked() {}\n*/\n"
              'var s = "func Quoted() {}"\n'
              "var r = `\nfunc Raw() {}\n`\n"
              "func Real() {}\n")
        self.assertEqual(self.units(), [("pkg/c.go::Real", "function", 11)])

    def test_files_the_go_tool_never_builds_are_not_read(self):
        for rel in ("pkg/p_test.go", "vendor/v/v.go", "pkg/testdata/t.go",
                    "_scratch/s.go"):
            write(self.root, rel, "package x\n\nfunc Exported() {}\n")
        write(self.root, "pkg/p.go", "package pkg\n\nfunc Kept() {}\n")
        self.assertEqual(self.units(), [("pkg/p.go::Kept", "function", 3)])

    def test_a_generated_file_is_not_read(self):
        # Generated code is not what a person edits; the ranker exists to
        # protect what they edit, which is why `dist/` is skipped for node.
        write(self.root, "pkg/kind_string.go",
              "// Code generated by \"stringer -type=Kind\"; DO NOT EDIT.\n\n"
              "package pkg\n\nfunc (i Kind) String() string { return \"\" }\n")
        write(self.root, "pkg/kind.go",
              "package pkg\n\ntype Kind int\n\nfunc Parse(s string) Kind { return 0 }\n")
        self.assertEqual(self.units(), [("pkg/kind.go::Parse", "function", 5)])

    def test_a_generated_marker_after_the_package_clause_does_not_count(self):
        write(self.root, "pkg/k.go",
              "package pkg\n\n// Code generated by hand; DO NOT EDIT.\n\nfunc Kept() {}\n")
        self.assertEqual(self.units(), [("pkg/k.go::Kept", "function", 5)])


GO_MOD = "module example.com/m\n\ngo 1.22\n"


class TriageCase(GoCase):
    """Build a package, discover it, and ask for one unit's tier."""

    def setUp(self):
        super().setUp()
        write(self.root, "go.mod", GO_MOD)

    def tier(self, name):
        units, _ = stack_go.discover_units(self.root, precise=False)
        by_name = {u["name"]: u for u in units}
        self.assertIn(name, by_name, sorted(by_name))
        return stack_go.triage(self.root, by_name[name])


class TestTriageByGroup(TriageCase):
    def test_a_pure_unit_is_tier_1(self):
        write(self.root, "svc/a.go",
              "package svc\n\nimport \"time\"\n\n"
              "func Double(n int) int { return n * 2 }\n"
              "func Wait() time.Duration { return 2 * time.Second }\n")
        self.assertEqual(self.tier("Double")[0], 1)
        # A time.Duration CONSTANT is arithmetic, not a read of the clock.
        self.assertEqual(self.tier("Wait")[0], 1)

    def test_filesystem_is_tier_2(self):
        write(self.root, "svc/a.go",
              'package svc\n\nimport "os"\n\n'
              "func Load(p string) ([]byte, error) { return os.ReadFile(p) }\n")
        tier, reason = self.tier("Load")
        self.assertEqual(tier, 2)
        self.assertIn("filesystem", reason)

    def test_clock_and_environment_are_tier_2(self):
        write(self.root, "svc/a.go",
              'package svc\n\nimport (\n\t"os"\n\t"time"\n)\n\n'
              "func Stamp() int64 { return time.Now().Unix() }\n"
              "func Age(t0 time.Time) time.Duration { return time.Since(t0) }\n"
              'func Mode() string { return os.Getenv("MODE") }\n')
        self.assertEqual(self.tier("Stamp")[:1], (2,))
        self.assertIn("clock", self.tier("Stamp")[1])
        self.assertEqual(self.tier("Age")[0], 2)
        self.assertIn("environment", self.tier("Mode")[1])

    def test_network_subprocess_and_database_are_tier_3(self):
        write(self.root, "svc/a.go",
              'package svc\n\nimport (\n\t"database/sql"\n\t"net"\n\t"net/http"\n'
              '\t"os/exec"\n)\n\n'
              'func Dial(a string) error { _, err := net.Dial("tcp", a); return err }\n'
              'func Fetch(u string) error { _, err := http.DefaultClient.Get(u); return err }\n'
              'func List() error { return exec.Command("ls").Run() }\n'
              'func Open(dsn string) error { _, err := sql.Open("pgx", dsn); return err }\n')
        for name, group in (("Dial", "network"), ("Fetch", "network"),
                            ("List", "subprocess"), ("Open", "database")):
            with self.subTest(name):
                tier, reason = self.tier(name)
                self.assertEqual(tier, 3)
                self.assertIn(group, reason)

    def test_randomness_from_the_global_source_is_tier_3_on_go(self):
        # rand.Seed is a no-op since Go 1.24, so the global source cannot be
        # controlled from a test; a seeded *rand.Rand is plain computation.
        write(self.root, "svc/a.go",
              'package svc\n\nimport (\n\t"math/rand"\n\tv2 "math/rand/v2"\n)\n\n'
              "func Roll() int { return rand.Intn(6) }\n"
              "func RollV2() int { return v2.IntN(6) }\n"
              "func Seeded() int { return rand.New(rand.NewSource(1)).Intn(6) }\n")
        self.assertEqual(self.tier("Roll")[:1], (3,))
        self.assertIn("randomness", self.tier("Roll")[1])
        self.assertEqual(self.tier("RollV2")[0], 3)
        self.assertEqual(self.tier("Seeded")[0], 1)

    def test_an_unaliased_v2_import_is_named_by_its_package_not_its_suffix(self):
        write(self.root, "svc/a.go",
              'package svc\n\nimport "math/rand/v2"\n\n'
              "func Roll() int { return rand.IntN(6) }\n")
        self.assertEqual(self.tier("Roll")[0], 3)

    def test_a_server_started_on_a_goroutine_is_network(self):
        # `go s.ListenAndServe()` is the commonest way a Go unit binds a port,
        # and neither `http.Server` nor a method on a server VALUE was a
        # marker: the filter scored this Tier 1 while the unit really listened.
        write(self.root, "svc/srv.go",
              'package svc\n\nimport "net/http"\n\n'
              "func Start(a string) *http.Server {\n"
              "\ts := &http.Server{Addr: a}\n\tgo s.ListenAndServe()\n\treturn s\n}\n")
        tier, reason = self.tier("Start")
        self.assertEqual(tier, 3, reason)
        self.assertIn("network", reason)

    def test_the_syscall_package_is_classified_by_name(self):
        write(self.root, "svc/a.go",
              'package svc\n\nimport (\n\t"syscall"\n\t"golang.org/x/sys/unix"\n)\n\n'
              'func Open() (int, error) { return syscall.Open("/x", 0, 0) }\n'
              "func Sock() (int, error) { return syscall.Socket(2, 1, 0) }\n"
              "func USock() (int, error) { return unix.Socket(2, 1, 0) }\n")
        self.assertEqual(self.tier("Open")[0], 2)
        self.assertEqual(self.tier("Sock")[0], 3)
        self.assertEqual(self.tier("USock")[0], 3)


class TestTriageReach(TriageCase):
    def test_an_aliased_import_still_resolves(self):
        write(self.root, "svc/a.go",
              'package svc\n\nimport f "os"\n\nfunc Home() string { return f.Getenv("HOME") }\n')
        self.assertEqual(self.tier("Home")[0], 2)

    def test_a_dot_import_resolves_bare_names(self):
        write(self.root, "svc/a.go",
              'package svc\n\nimport . "time"\n\nfunc Stamp() int64 { return Now().Unix() }\n')
        self.assertEqual(self.tier("Stamp")[0], 2)

    def test_a_helper_in_a_sibling_file_is_chased(self):
        # One package, two files, no import between them: the Go shape Python
        # and node never had to read.
        write(self.root, "svc/a.go",
              "package svc\n\nfunc Save(b []byte) error { return write(b) }\n")
        write(self.root, "svc/b.go",
              'package svc\n\nimport "os"\n\n'
              'func write(b []byte) error { return os.WriteFile("out", b, 0o644) }\n')
        tier, reason = self.tier("Save")
        self.assertEqual(tier, 2)
        self.assertIn("via write", reason)

    def test_a_same_named_helper_in_another_package_is_not_chased(self):
        write(self.root, "svc/a.go",
              "package svc\n\nfunc Save(b []byte) error { return write(b) }\n"
              "func write(b []byte) error { return nil }\n")
        write(self.root, "other/b.go",
              'package other\n\nimport "os"\n\n'
              'func write(b []byte) error { return os.WriteFile("out", b, 0o644) }\n')
        self.assertEqual(self.tier("Save")[0], 1)

    def test_a_method_on_the_receiver_is_chased(self):
        write(self.root, "svc/s.go",
              'package svc\n\nimport "os"\n\ntype Store struct{}\n\n'
              "func (s *Store) Save() error { return s.flush() }\n"
              'func (s *Store) flush() error { return os.WriteFile("db", nil, 0o644) }\n')
        tier, reason = self.tier("Store.Save")
        self.assertEqual(tier, 2)
        self.assertIn("Store.flush", reason)

    def test_a_method_only_one_type_defines_is_chased_through_a_variable(self):
        write(self.root, "svc/s.go",
              'package svc\n\nimport "os"\n\ntype Store struct{}\n\n'
              'func (s *Store) flush() error { return os.WriteFile("db", nil, 0o644) }\n'
              "func Run(st *Store) error { return st.flush() }\n")
        self.assertEqual(self.tier("Run")[0], 2)

    def test_the_receiver_resolves_what_the_method_name_cannot(self):
        # Two types define `flush`, so the method name alone decides nothing --
        # but inside a `*Store` method, `s.flush()` IS Store's. Without this
        # fixture the receiver rule was unproven: every other receiver test is
        # also satisfied by the only-one-type-defines-it rule.
        write(self.root, "svc/s.go",
              'package svc\n\nimport "os"\n\ntype Store struct{}\ntype Cache struct{}\n\n'
              "func (s *Store) Save() error { return s.flush() }\n"
              'func (s *Store) flush() error { return os.WriteFile("db", nil, 0o644) }\n'
              "func (c *Cache) flush() error { return nil }\n")
        tier, reason = self.tier("Store.Save")
        self.assertEqual(tier, 2)
        self.assertIn("Store.flush", reason)

    def test_imports_are_file_scoped(self):
        # `f` is `os` in a.go and an ordinary parameter in b.go. A package-wide
        # alias map would read `f.Getenv` in b.go as `os.Getenv`.
        write(self.root, "svc/a.go",
              'package svc\n\nimport f "os"\n\nfunc Home() string { return f.Getenv("HOME") }\n')
        write(self.root, "svc/b.go",
              "package svc\n\ntype getter interface{ Getenv(string) string }\n\n"
              'func Lookup(f getter) string { return f.Getenv("x") }\n')
        self.assertEqual(self.tier("Home")[0], 2)
        self.assertEqual(self.tier("Lookup")[0], 1)

    def test_a_method_two_types_define_is_the_points_to_boundary(self):
        # Picking between them needs the receiver's type -- points-to analysis,
        # which this filter does not do. The runtime guard is the backstop.
        write(self.root, "svc/s.go",
              'package svc\n\nimport "os"\n\ntype Store struct{}\ntype Cache struct{}\n\n'
              'func (s *Store) flush() error { return os.WriteFile("db", nil, 0o644) }\n'
              "func (c *Cache) flush() error { return nil }\n"
              "func Run(st *Store) error { return st.flush() }\n")
        self.assertEqual(self.tier("Run")[0], 1)

    def test_markers_in_comments_and_strings_do_not_count(self):
        write(self.root, "svc/a.go",
              "package svc\n\n// os.ReadFile(p) would be I/O\n"
              'func Name() string { return "net.Dial" }\n')
        self.assertEqual(self.tier("Name")[0], 1)

    def test_a_closure_inside_the_unit_is_part_of_the_unit(self):
        write(self.root, "svc/a.go",
              'package svc\n\nimport "os"\n\n'
              'func Later() func() string { return func() string { return os.Getenv("X") } }\n')
        self.assertEqual(self.tier("Later")[0], 2)


class TestImportTimeAndStaticDeclines(TriageCase):
    def test_init_io_floors_every_unit_in_the_package_across_files(self):
        write(self.root, "svc/boot.go",
              'package svc\n\nimport "os"\n\nvar mode string\n\n'
              'func init() { mode = os.Getenv("MODE") }\n')
        write(self.root, "svc/pure.go", "package svc\n\nfunc Double(n int) int { return n * 2 }\n")
        tier, reason = self.tier("Double")
        self.assertEqual(tier, 3)
        self.assertIn("import time", reason)

    def test_a_package_level_call_doing_uncontrollable_io_is_tier_4(self):
        write(self.root, "svc/conn.go",
              'package svc\n\nimport "net"\n\nvar conn, _ = net.Dial("tcp", "db:5432")\n')
        write(self.root, "svc/pure.go", "package svc\n\nfunc Double(n int) int { return n * 2 }\n")
        self.assertEqual(self.tier("Double")[0], 4)

    def test_a_package_level_reference_without_a_call_is_not_import_time_io(self):
        # `http.DefaultClient` is a pointer to a package variable; taking it
        # performs no I/O. Python's import-time rule counts calls only, and Go
        # can tell a call from a reference, so this one does too.
        write(self.root, "svc/c.go",
              'package svc\n\nimport "net/http"\n\nvar client = http.DefaultClient\n\n'
              "func Double(n int) int { return n * 2 }\n")
        self.assertEqual(self.tier("Double")[0], 1)

    def test_a_function_literal_assigned_at_package_level_does_not_run_at_import(self):
        # Its body runs when it is CALLED; every handler table would otherwise
        # floor its whole package.
        write(self.root, "svc/h.go",
              'package svc\n\nimport "os"\n\n'
              'var handlers = map[string]func() string{\n\t"home": func() string { return os.Getenv("HOME") },\n}\n\n'
              "func Double(n int) int { return n * 2 }\n")
        self.assertEqual(self.tier("Double")[0], 1)

    def test_a_call_in_a_map_of_funcs_value_still_runs_at_import(self):
        # The first `func` below is the map's TYPE; the `{` after it opens the
        # VALUE, where `boot()` runs during package initialisation. Blanking
        # from every `func` keyword hid exactly this call.
        write(self.root, "svc/r.go",
              'package svc\n\nimport "os"\n\n'
              'var registry = map[string]func() string{"x": boot()}\n\n'
              'func boot() func() string { _ = os.Getenv("X"); return nil }\n\n'
              "func Double(n int) int { return n * 2 }\n")
        tier, reason = self.tier("Double")
        self.assertEqual(tier, 3)
        self.assertIn("via boot", reason)

    def test_the_init_floor_does_not_cross_into_another_package(self):
        write(self.root, "svc/boot.go",
              'package svc\n\nimport "os"\n\nfunc init() { _ = os.Getenv("MODE") }\n')
        write(self.root, "calc/pure.go", "package calc\n\nfunc Double(n int) int { return n * 2 }\n")
        self.assertEqual(self.tier("Double")[0], 1)

    def test_cgo_is_statically_declined(self):
        write(self.root, "svc/c.go",
              'package svc\n\n// #include <stdio.h>\nimport "C"\n\n'
              "func Put(s string) { C.puts(C.CString(s)) }\n"
              "func Double(n int) int { return n * 2 }\n")
        tier, reason = self.tier("Put")
        self.assertEqual(tier, 4)
        self.assertIn("cgo", reason)
        # Only the unit that CALLS into C is declined; its neighbour is not.
        self.assertEqual(self.tier("Double")[0], 1)

    def test_a_variable_named_C_is_not_cgo_without_import_C(self):
        write(self.root, "svc/c.go",
              "package svc\n\ntype conf struct{ Level int }\n\n"
              "func Level(C conf) int { return C.Level }\n")
        self.assertEqual(self.tier("Level")[0], 1)

    def test_a_raw_syscall_is_statically_declined(self):
        write(self.root, "svc/r.go",
              'package svc\n\nimport "syscall"\n\n'
              "func Raw() { syscall.Syscall(20, 0, 0, 0) }\n")
        tier, reason = self.tier("Raw")
        self.assertEqual(tier, 4)
        self.assertIn("raw", reason)

    def test_a_unit_that_is_not_there_any_more_is_declined(self):
        write(self.root, "svc/a.go", "package svc\n\nfunc Double(n int) int { return n * 2 }\n")
        ghost = {"id": "svc/a.go::Gone", "path": "svc/a.go", "name": "Gone",
                 "lineno": 3, "kind": "function"}
        self.assertEqual(stack_go.triage(self.root, ghost)[0], 4)


class TestSyscallTableIsDerived(unittest.TestCase):
    """Every exported name of package `syscall`, on both platforms, is classified.

    DERIVED from the installed Go's own `go doc`, not remembered. Its failure
    on a Go upgrade is the MECHANISM, not a nuisance: a release that adds a
    syscall name has added a way to reach the kernel that neither the filter
    nor the guard knows about. Classify it; do not widen this test to skip it.
    """

    VOCAB = {"filesystem", "clock", "randomness", "environment", "network",
             "subprocess", "database", "stdin", "fd", "pure", "raw"}

    def test_every_value_is_in_the_vocabulary(self):
        self.assertEqual(set(stack_go.SYSCALL_GROUPS.values()) - self.VOCAB, set())

    def test_every_exported_syscall_name_is_classified(self):
        go = shutil.which("go")
        if not go:
            raise unittest.SkipTest("no `go` on PATH: the syscall table is NOT "
                                    "checked against a real toolchain here")
        import subprocess
        names = set()
        for goos in ("darwin", "linux"):
            env = dict(os.environ, GOOS=goos, GOTOOLCHAIN="local")
            out = subprocess.run([go, "doc", "-all", "syscall"], env=env,
                                 capture_output=True, text=True, timeout=120).stdout
            for line in out.splitlines():
                if line.startswith("func ") and line[5:6].isupper():
                    names.add(line[5:].split("(")[0].strip())
        self.assertGreater(len(names), 200)
        self.assertEqual(sorted(names - set(stack_go.SYSCALL_GROUPS)), [])


class TestThroughTheCore(TriageCase):
    """The stack as `rank_risk` uses it: registered, ranked, and credited."""

    def setUp(self):
        super().setUp()
        self.rank_risk = _load("rank_risk")
        self.go = self.rank_risk.stack_by_name("go")

    def test_the_registry_holds_go(self):
        self.assertIsNotNone(self.go)
        for attr in ("discover_units", "triage", "scope_files", "classify_manifest"):
            self.assertTrue(hasattr(self.go, attr), attr)

    def test_a_go_repo_is_ranked_end_to_end(self):
        write(self.root, "svc/a.go",
              'package svc\n\nimport "net"\n\n'
              "func Double(n int) int { return n * 2 }\n"
              'func Dial() error { _, err := net.Dial("tcp", "x:1"); return err }\n')
        plan = self.rank_risk.rank(self.root, "10 years ago", 10)
        self.assertEqual(plan["stack"], "go")
        # Whichever reader this machine can honestly run: `go/ast` wherever
        # `go` is on PATH, the heuristic where it is not.
        self.assertEqual(plan["discovery"], "precise" if shutil.which("go") else "heuristic")
        self.assertEqual([r["id"] for r in plan["ranked"]], ["svc/a.go::Double"])
        self.assertEqual([r["id"] for r in plan["not_netted"]], ["svc/a.go::Dial"])

    def test_the_cli_accepts_stack_go(self):
        import contextlib
        import io
        write(self.root, "svc/a.go", "package svc\n\nfunc Double(n int) int { return n * 2 }\n")
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = self.rank_risk.main([self.root, "--stack", "go"])
        self.assertEqual(code, 0)
        self.assertIn("stack=go", err.getvalue())

    def test_callers_in_sibling_files_count_as_reach(self):
        write(self.root, "svc/a.go", "package svc\n\nfunc Parse(s string) int { return len(s) }\n")
        write(self.root, "svc/b.go",
              'package svc\n\nfunc use() int { return Parse("x") + Parse("y") }\n')
        write(self.root, "svc/a_test.go",
              'package svc\n\nimport "testing"\n\nfunc TestParse(t *testing.T) { Parse("z") }\n')
        units, _ = self.go.discover_units(self.root, precise=False)
        refs = self.rank_risk.inbound_refs(self.root, units, self.go)
        self.assertEqual(refs["svc/a.go::Parse"], 3)

    def test_a_white_box_test_covers_its_unit_and_buf_string_covers_nothing(self):
        write(self.root, "svc/report.go",
              "package svc\n\ntype Report struct{}\n\nfunc (r Report) String() string { return \"\" }\n")
        write(self.root, "svc/util.go", "package svc\n\nfunc Fmt() string { return \"\" }\n")
        write(self.root, "svc/util_test.go",
              'package svc\n\nimport (\n\t"bytes"\n\t"testing"\n)\n\n'
              "func TestFmt(t *testing.T) { var buf bytes.Buffer; _ = buf.String(); _ = Fmt() }\n")
        units, _ = self.go.discover_units(self.root, precise=False)
        covered = self.rank_risk.already_covered(self.root, units, self.go)
        self.assertIn("svc/util.go::Fmt", covered)
        self.assertNotIn("svc/report.go::Report.String", covered)

    def test_a_method_is_credited_only_through_a_value_of_its_type(self):
        # Naming a type and calling `.Error()` on SOMETHING are both everywhere
        # in Go tests: `var pe *ParseError; errors.As(err, &pe)` and
        # `errors.New("x").Error()` in one test must not cover
        # `ParseError.Error`, which that test never calls.
        write(self.root, "svc/perr.go",
              "package svc\n\ntype ParseError struct{ Msg string }\n\n"
              "func (e *ParseError) Error() string { return e.Msg }\n\n"
              "func NewParseError(m string) *ParseError { return &ParseError{Msg: m} }\n")
        write(self.root, "svc/perr_test.go",
              'package svc\n\nimport (\n\t"errors"\n\t"testing"\n)\n\n'
              "func TestAs(t *testing.T) {\n\tvar pe *ParseError\n"
              '\t_ = errors.As(errors.New("x"), &pe)\n\t_ = errors.New("x").Error()\n}\n')
        units, _ = self.go.discover_units(self.root, precise=False)
        covered = self.rank_risk.already_covered(self.root, units, self.go)
        self.assertNotIn("svc/perr.go::ParseError.Error", covered)

    def test_each_way_of_holding_a_value_still_credits_the_method(self):
        forms = {
            "bound": 'pe := &ParseError{Msg: "x"}\n\t_ = pe.Error()',
            "declared": 'var pe ParseError\n\t_ = pe.Error()',
            "literal": '_ = (&ParseError{Msg: "x"}).Error()',
            "constructor": 'pe := NewParseError("x")\n\t_ = pe.Error()',
        }
        for name, body in forms.items():
            with self.subTest(form=name):
                root = tempfile.mkdtemp(prefix="tsn-go-cov-")
                self.addCleanup(shutil.rmtree, root, ignore_errors=True)
                write(root, "go.mod", GO_MOD)
                write(root, "svc/perr.go",
                      "package svc\n\ntype ParseError struct{ Msg string }\n\n"
                      "func (e *ParseError) Error() string { return e.Msg }\n\n"
                      "func NewParseError(m string) *ParseError { return &ParseError{Msg: m} }\n")
                write(root, "svc/perr_test.go",
                      'package svc\n\nimport "testing"\n\n'
                      "func TestErr(t *testing.T) {\n\t%s\n}\n" % body)
                units, _ = self.go.discover_units(root, precise=False)
                covered = self.rank_risk.already_covered(root, units, self.go)
                self.assertIn("svc/perr.go::ParseError.Error", covered)

    def test_a_same_named_package_elsewhere_is_not_credited(self):
        for pkg in ("a", "b"):
            write(self.root, "%s/util/x.go" % pkg, "package util\n\nfunc Do() int { return 1 }\n")
        write(self.root, "a/util/x_test.go",
              'package util\n\nimport "testing"\n\nfunc TestDo(t *testing.T) { Do() }\n')
        units, _ = self.go.discover_units(self.root, precise=False)
        covered = self.rank_risk.already_covered(self.root, units, self.go)
        self.assertIn("a/util/x.go::Do", covered)
        self.assertNotIn("b/util/x.go::Do", covered)


def _fake_go(root, body):
    """An executable `go` stand-in: a shell script whose behaviour is `body`."""
    path = os.path.join(root, "fake-go")
    with open(path, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, 0o755)
    return path


class TestPreciseDiscovery(GoCase):
    """The `go/ast` path: an upgrade when it answers, invisible when it cannot.

    Every way it can fail ends in the heuristic, and the label says so. A
    toolchain that was FOUND and then failed says so on stderr too; simply not
    having `go` is ordinary and is reported by the label alone.
    """

    def setUp(self):
        super().setUp()
        self.tools = tempfile.mkdtemp(prefix="tsn-go-tools-")
        self.addCleanup(shutil.rmtree, self.tools, ignore_errors=True)
        write(self.root, "go.mod", GO_MOD)
        for i in range(3):
            write(self.root, "pkg/m%d.go" % i, "package pkg\n\nfunc F%d() {}\n" % i)

    def precise(self, go_exe, timeout=20):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = stack_go._units_precise(self.root, go_exe=go_exe, timeout=timeout)
        return got, err.getvalue()

    def test_no_go_at_all_falls_back_quietly(self):
        got, err = self.precise("/nonexistent/go")
        self.assertIsNone(got)
        self.assertEqual(err, "")

    def test_a_go_that_fails_declines_and_says_so(self):
        got, err = self.precise(_fake_go(self.tools, "exit 1"))
        self.assertIsNone(got)
        self.assertIn("declined", err)

    def test_zero_precise_units_where_the_heuristic_finds_some_declines(self):
        # The silent zero, wearing the label the report tells an agent to
        # trust more: node's precise path shipped exactly this once. Judged at
        # `discover_units`, where both readers' answers are visible -- a Go
        # repo that genuinely exports nothing (a `package main` CLI) is an
        # honest precise zero, and must not be declined.
        import contextlib
        import io
        stack_go.GO_EXE = _fake_go(
            self.tools, 'cat >/dev/null; echo \'{"units": [], "unreadable": 0}\'')
        self.addCleanup(setattr, stack_go, "GO_EXE", "go")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            units, mode = stack_go.discover_units(self.root)
        self.assertEqual(mode, "heuristic")
        self.assertEqual(len(units), 3)
        self.assertIn("declined", err.getvalue())

    def test_one_unreadable_file_declines_the_whole_run(self):
        got, _ = self.precise(_fake_go(
            self.tools, 'cat >/dev/null; echo \'{"units": [{"path": "pkg/m0.go", "name": "F0", '
                        '"kind": "function", "lineno": 3}], "unreadable": 1}\''))
        self.assertIsNone(got)

    def test_a_malformed_row_declines_the_whole_run(self):
        got, _ = self.precise(_fake_go(
            self.tools, 'cat >/dev/null; echo \'{"units": [{"path": "pkg/m0.go", "name": "F0", '
                        '"kind": "function", "lineno": 0}], "unreadable": 0}\''))
        self.assertIsNone(got)

    def test_a_row_for_a_file_nobody_asked_about_declines(self):
        got, _ = self.precise(_fake_go(
            self.tools, 'cat >/dev/null; echo \'{"units": [{"path": "elsewhere.go", "name": "X", '
                        '"kind": "function", "lineno": 1}], "unreadable": 0}\''))
        self.assertIsNone(got)

    def test_a_hung_toolchain_is_bounded_by_the_timeout(self):
        got, err = self.precise(_fake_go(self.tools, "sleep 30"), timeout=1)
        self.assertIsNone(got)
        self.assertIn("timed out", err)

    def test_precise_false_never_runs_a_process(self):
        sentinel = os.path.join(self.tools, "ran")
        _fake_go(self.tools, "touch %s; exit 1" % sentinel)
        env_path = os.environ.get("PATH", "")
        os.environ["PATH"] = self.tools + os.pathsep + env_path
        os.rename(os.path.join(self.tools, "fake-go"), os.path.join(self.tools, "go"))
        try:
            units, mode = stack_go.discover_units(self.root, precise=False)
        finally:
            os.environ["PATH"] = env_path
        self.assertEqual(mode, "heuristic")
        self.assertEqual(len(units), 3)
        self.assertFalse(os.path.exists(sentinel))

    def test_the_toolchain_is_pinned_local_and_left_undecorated(self):
        # GOTOOLCHAIN=local: a repo whose go.mod asks for a newer Go must not
        # make this analysis DOWNLOAD one. GOFLAGS empty and GOWORK=off: the
        # helper is this skill's program, and the analysed repo's flags and
        # workspace have no business shaping how it builds.
        dump = os.path.join(self.tools, "env.txt")
        self.precise(_fake_go(self.tools, "cat >/dev/null; env > %s; exit 1" % dump))
        with open(dump, encoding="utf-8") as f:
            env = dict(line.split("=", 1) for line in f.read().splitlines() if "=" in line)
        self.assertEqual(env.get("GOTOOLCHAIN"), "local")
        self.assertEqual(env.get("GOFLAGS"), "")
        self.assertEqual(env.get("GOWORK"), "off")

    def test_the_cli_no_precise_flag_reaches_the_go_stack(self):
        import contextlib
        import io
        import json as _json
        rank_risk = _load("rank_risk")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rank_risk.main([self.root, "--stack", "go", "--no-precise"])
        self.assertEqual(_json.loads(out.getvalue())["discovery"], "heuristic")


def _real_go():
    go = shutil.which("go")
    if not go:
        raise unittest.SkipTest("no `go` on PATH: Go's precise discovery is NOT "
                                "exercised on this machine")
    return go


class TestPreciseAgainstTheRealToolchain(TriageCase):
    SHAPES = ("package pkg\n\ntype Report struct{}\ntype Set[K comparable] struct{}\n"
              "type impl struct{}\n\n// Parse parses.\nfunc Parse(s string) int { return 1 }\n\n"
              "func Map[T any, U any](xs []T, f func(T) U) []U { return nil }\n\n"
              "func Multi(\n\ts string,\n) (int, error) {\n\treturn 0, nil\n}\n\n"
              "func (r *Report) Add(n int) {}\nfunc (*Report) Reset()      {}\n"
              "func (s *Set[K]) Put(k K)   {}\nfunc (i *impl) Do()         {}\n"
              "func helper()               {}\n")

    def test_both_paths_agree_on_what_both_can_read(self):
        _real_go()
        write(self.root, "pkg/a.go", self.SHAPES)
        write(self.root, "pkg/gen.go",
              "// Code generated by hand; DO NOT EDIT.\n\npackage pkg\n\nfunc Gen() {}\n")
        precise, mode = stack_go.discover_units(self.root)
        heuristic, _ = stack_go.discover_units(self.root, precise=False)
        self.assertEqual(mode, "precise")
        self.assertEqual(precise, heuristic)

    def test_both_paths_read_declarations_gofmt_would_have_moved(self):
        # The heuristic used to read column 0 only and documented these as its
        # misses. It now reads Go's declaration position -- a line start or
        # after `;` -- so the two readers agree here too, and the precise path
        # is worth having because it IS Go's parser, not because the other
        # reader was handicapped.
        _real_go()
        write(self.root, "pkg/a.go", self.MOVED)
        precise, mode = stack_go.discover_units(self.root)
        heuristic, _ = stack_go.discover_units(self.root, precise=False)
        self.assertEqual(mode, "precise")
        self.assertEqual(precise, heuristic)
        self.assertEqual([u["name"] for u in precise], ["Commented", "Indented", "Semi"])

    MOVED = ("package pkg\n\nvar x = 1; func Semi() {}\n\n\tfunc Indented() {}\n"
             "/* note */ func Commented() {}\n")

    def test_a_package_that_exports_nothing_is_an_honest_precise_zero(self):
        import contextlib
        import io
        _real_go()
        write(self.root, "main.go", "package main\n\nfunc helper() {}\nfunc main() { helper() }\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            units, mode = stack_go.discover_units(self.root)
        self.assertEqual((units, mode), ([], "precise"))
        self.assertNotIn("declined", err.getvalue())

    def test_the_by_line_fallback_finds_a_declaration_the_index_lacks(self):
        # The two discovery paths are separate readers; if they ever diverge,
        # a unit only the precise path found must still be triaged, not read
        # as "not found", Tier 4. Exercised directly, because with both
        # readers on the same declaration rule nothing reaches it today.
        text = 'package pkg\n\nimport "os"\n\nvar n = 1; func Semi() ([]byte, error) ' \
               '{ return os.ReadFile("x") }\n'
        f = stack_go._File("pkg/a.go", text)
        key, span, recv = f.decl_at_line(5, "Semi")
        self.assertEqual((key, recv), ("Semi", None))
        self.assertIn("os.ReadFile", text[span[0]:span[1]])
        self.assertIsNone(f.decl_at_line(5, "Other"))
        self.assertIsNone(f.decl_at_line(99, "Semi"))


class TestDeclarationPosition(TriageCase):
    def test_the_heuristic_reads_declarations_gofmt_would_have_moved(self):
        write(self.root, "pkg/a.go", TestPreciseAgainstTheRealToolchain.MOVED)
        units, _ = stack_go.discover_units(self.root, precise=False)
        self.assertEqual([(u["name"], u["lineno"]) for u in units],
                         [("Commented", 6), ("Indented", 5), ("Semi", 3)])

    def test_a_declaration_after_a_semicolon_is_triaged_as_itself(self):
        # Before the declaration rule widened, this body was read as
        # PACKAGE-LEVEL code, and `os.ReadFile` floored the package as
        # "I/O at import time" -- Tier 3 for a plain Tier 2 unit.
        write(self.root, "pkg/a.go",
              'package pkg\n\nimport "os"\n\n'
              'var n = 1; func Semi() ([]byte, error) { return os.ReadFile("x") }\n'
              "\tfunc Pure() int { return 1 }\n")
        self.assertEqual(self.tier("Semi")[0], 2)
        self.assertEqual(self.tier("Pure")[0], 1)


class TestBodylessFunctions(TriageCase):
    def test_a_function_with_no_go_body_is_declined_honestly(self):
        # Implemented in assembly, or linknamed to the runtime: callable and
        # discovered, but there is nothing here for either layer to read.
        write(self.root, "pkg/add.go",
              "package pkg\n\n// Add is implemented in add_amd64.s.\nfunc Add(a, b int) int\n")
        tier, reason = self.tier("Add")
        self.assertEqual(tier, 4)
        self.assertIn("no Go body", reason)


def _build_repo(root, go=0, py=0, ts=0, files=()):
    for i in range(go):
        write(root, "pkg%d/m.go" % i, "package pkg%d\n\nfunc Go%d() {}\n" % (i, i))
    for i in range(py):
        write(root, "srv/mod%d.py" % i, "def go%d():\n    return 1\n" % i)
    for i in range(ts):
        write(root, "web/mod%d.ts" % i, "export function go%d() {}\n" % i)
    for rel, text in files:
        write(root, rel, text)


GO_CASES = [
    # (label, go, py, ts, extra files, expected)
    ("a Go service with a few Python scripts",
     30, 5, 0, (("go.mod", GO_MOD),), "go"),
    ("a Python repo that pins its linters in a tools module",
     0, 40, 0, (("pyproject.toml", '[project]\nname = "srv"\n'),
                ("tools/go.mod", "module example.com/tools\n\ngo 1.22\n"),
                ("tools/tools.go", '//go:build tools\n\npackage tools\n\nimport _ "x/y"\n')),
     "python"),
    ("a declared TypeScript frontend outweighing a smaller declared Go backend",
     12, 0, 20, (("go.mod", GO_MOD),
                 ("package.json", '{"name": "web", "main": "web/mod0.ts",\n'
                                  ' "dependencies": {"left-pad": "^1.0.0"}}\n')),
     "node"),
    ("a small Go module beside vendored Python it does not own",
     3, 0, 0, (("go.mod", GO_MOD),) + tuple(
         ("vendor/py/v%d.py" % i, "def v():\n    return 1\n") for i in range(6)), "go"),
    ("ten Go files and ten Python files, nothing declared",
     10, 10, 0, (), "ambiguous"),
]


class TestGoCaseTable(unittest.TestCase):
    """Where Go sits among the three stacks, decided against real repo shapes."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tsn-go-cases-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.rank_risk = _load("rank_risk")

    def test_every_row(self):
        for label, go, py, ts, files, expected in GO_CASES:
            with self.subTest(label):
                root = tempfile.mkdtemp(dir=self.root)
                _build_repo(root, go, py, ts, files)
                scores = dict(self.rank_risk.stack_evidence(root))
                try:
                    got = self.rank_risk.detect_stack(root).STACK_NAME
                except self.rank_risk.AmbiguousStack:
                    got = "ambiguous"
                self.assertEqual(got, expected, "%s: %s" % (label, scores))

    def test_this_repo_is_still_python(self):
        repo = os.path.dirname(os.path.dirname(_HERE))
        self.assertEqual(self.rank_risk.detect_stack(repo).STACK_NAME, "python")


class TestInterfaceNames(unittest.TestCase):
    NAMES = ("STACK_NAME", "MANIFESTS", "evidence", "classify_manifest",
             "iter_source_files", "is_test_path", "is_test_for", "scope_files",
             "module_of", "name_pattern", "path_pattern", "IDENTIFIER_RE",
             "preceding_qualifier", "module_bindings", "reached_through_module",
             "discover_units", "triage")

    def test_supplies_every_interface_name(self):
        missing = [n for n in self.NAMES if not hasattr(stack_go, n)]
        self.assertEqual(missing, [])
        self.assertEqual(stack_go.STACK_NAME, "go")


if __name__ == "__main__":
    unittest.main()
