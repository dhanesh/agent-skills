#!/usr/bin/env python3
"""Tests for the Rust stack: identity, files, naming, lexing and grammar.

The governing bar is the one every stack answers to: static triage is a FILTER
and the runtime guard is the enforcement, so a reader may be wrong in the
direction of MORE reported work and never in the direction of less. Each
test marked NEGATIVE pins a form that must NOT be read -- a literal read as
code, a file read as source, or a coverage credit nobody earned.

Crate identity comes from `stack_rust`'s crate index, which the walk
(`iter_source_files(root)`) builds for the root it walks. Tests that need a
crate NAME therefore walk their root first; tests of the fallback reset the
index to nothing (`_no_index`).
"""
from __future__ import annotations

import importlib.util
import os
import re
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


stack_rust = _load("stack_rust")


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _no_index():
    """Forget every walked root, so the root-less functions take the fallback."""
    stack_rust._CURRENT_ROOT = None


CALCX_TOML = '[package]\nname = "calcx"\nversion = "0.1.0"\nedition = "2021"\n'


class RustCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tsn-rust-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.addCleanup(_no_index)


class CalcxCase(RustCase):
    """One library crate `calcx` at the analysed root, walked (so indexed)."""

    FILES = {
        "Cargo.toml": CALCX_TOML,
        "src/lib.rs": "pub mod a;\npub mod calc;\npub mod other;\n",
        "src/a.rs": "pub mod b;\npub fn run(n: u32) -> u32 { n }\n",
        "src/a/b.rs": "pub fn run(n: u32) -> u32 { n }\n",
        "src/calc.rs": "pub struct Report { pub n: u32 }\n",
        "src/other.rs": "pub fn run() {}\n",
        "tests/it.rs": "#[test]\nfn t() {}\n",
    }

    def setUp(self):
        super().setUp()
        for rel, text in self.FILES.items():
            write(self.root, rel, text)
        stack_rust.iter_source_files(self.root)


# ── The stripper ─────────────────────────────────────────────────────────

class TestStripper(unittest.TestCase):
    def strip(self, text, **kw):
        return stack_rust.strip_noncode(text, **kw)

    def test_offsets_and_newlines_are_preserved(self):
        # The property go's stripper carries: a position in the output is the
        # same position in the input, so line numbers and spans hold.
        text = ('fn a() {} // trailing\n/* outer /* inner */ still */\n'
                'let s = "str\\"q";\nlet r = r#"raw "x"\nmulti"#;\n'
                "let c = '\\u{1F600}';\nlet b = br\"bytes\";\n/// doc\n//! inner\n")
        out = self.strip(text)
        self.assertEqual(len(out), len(text))
        self.assertEqual([i for i, ch in enumerate(out) if ch == "\n"],
                         [i for i, ch in enumerate(text) if ch == "\n"])
        self.assertIn("fn a() {}", out)
        for gone in ("trailing", "outer", "inner", "still", "str", "raw", "multi",
                     "1F600", "bytes", "doc"):
            self.assertNotIn(gone, out)

    def test_line_comments_including_doc_comments(self):
        out = self.strip("// Hidden1\n/// Hidden2\n//! Hidden3\nfn Visible() {}\n")
        for gone in ("Hidden1", "Hidden2", "Hidden3"):
            self.assertNotIn(gone, out)
        self.assertIn("fn Visible() {}", out)

    def test_block_comments_nest(self):
        # Rust block comments NEST: the first `*/` closes the inner one only.
        # Mutation "drop nested-comment depth" is killed here.
        out = self.strip("/* a /* b */ fn Hidden() {} */\nfn Visible() {}\n")
        self.assertNotIn("Hidden", out)
        self.assertIn("fn Visible() {}", out)

    def test_a_string_with_escapes_and_newlines(self):
        out = self.strip('let s = "a\\"b fn Hidden()\nstill Hidden2";\nfn Visible() {}\n')
        self.assertNotIn("Hidden", out)
        self.assertIn("fn Visible() {}", out)

    def test_raw_strings_with_hashes(self):
        # Mutation "drop the r# raw-string rule" is killed here: without it
        # the inner `"` closes a plain string and `Hidden` reads as code.
        for lit in ('r"no fn Hidden() escapes\\"',
                    'r#"one "fn Hidden()" two"#',
                    'r##"a "# still fn Hidden() "##'):
            with self.subTest(lit=lit):
                out = self.strip("let s = %s;\nfn Visible() {}\n" % lit)
                self.assertNotIn("Hidden", out)
                self.assertIn("fn Visible() {}", out)

    def test_byte_and_c_strings(self):
        for lit in ('b"fn Hidden()"', 'br#"fn "Hidden"()"#', 'c"fn Hidden()"', "b'H'"):
            with self.subTest(lit=lit):
                out = self.strip("let s = %s;\nfn Visible() {}\n" % lit)
                self.assertNotIn("Hidden", out)
                self.assertNotIn("'H'", out)
                self.assertIn("fn Visible() {}", out)

    def test_char_literals(self):
        for lit in ("'a'", "'\\n'", "'\\u{1F600}'", "'\\''", "'\"'", "'\\x7F'"):
            with self.subTest(lit=lit):
                text = "let c = %s;\nfn Visible() {}\n" % lit
                out = self.strip(text)
                self.assertEqual(out[8:8 + len(lit)], " " * len(lit))
                self.assertIn("fn Visible() {}", out)

    def test_a_char_holding_a_quote_does_not_open_a_string(self):
        self.assertIn("fn Visible", self.strip("let q = '\"';\nfn Visible() {}\n"))

    def test_lifetimes_are_code_not_char_literals(self):
        # NEGATIVE. `'a` is a lifetime: a `'` NOT followed by one char (or
        # one escape) and a closing `'`. Mutation "treat 'a as a char
        # literal" is killed here.
        for text in ("fn f<'a>(x: &'a str) -> &'a str { x }\n",
                     "fn g(x: &'static str) {}\n",
                     "'outer: loop { break 'outer; }\n",
                     "impl<'a, 'b> S<'a, 'b> {}\n"):
            with self.subTest(text=text):
                self.assertEqual(self.strip(text), text)

    def test_comment_markers_inside_a_string_are_not_comments(self):
        self.assertIn("fn Visible", self.strip('let u = "http://x/*y";\nfn Visible() {}\n'))

    def test_a_raw_identifier_is_code(self):
        text = "let r#type = 1; let r#fn = r#type;\n"
        self.assertEqual(self.strip(text), text)

    def test_keep_strings_keeps_literals_and_still_blanks_comments(self):
        text = '#[path = "x/y.rs"] // #[path = "other.rs"]\npub mod z;\n'
        out = self.strip(text, keep_strings=True)
        self.assertIn('"x/y.rs"', out)
        self.assertNotIn("other", out)


# ── Crates and manifests ─────────────────────────────────────────────────

class TestCrates(RustCase):
    def test_classify_manifest(self):
        c = stack_rust.classify_manifest
        self.assertEqual(c("Cargo.toml", CALCX_TOML), "declaring")
        self.assertEqual(c("Cargo.toml", '[workspace]\nmembers = ["a"]\n'), "declaring")
        self.assertEqual(c("package.json", "[package]\n"), "tooling")

    def test_a_profile_only_manifest_is_tooling(self):
        # NEGATIVE: a Cargo.toml holding only `[profile.release]` declares no
        # package and no workspace.
        self.assertEqual(stack_rust.classify_manifest(
            "Cargo.toml", "[profile.release]\nlto = true\n"), "tooling")
        # ...nor does a `[package]` that is only a comment.
        self.assertEqual(stack_rust.classify_manifest(
            "Cargo.toml", "# [package]\n[profile.release]\n"), "tooling")

    def test_evidence_scales_with_a_declaring_manifest(self):
        write(self.root, "Cargo.toml", CALCX_TOML)
        for i in range(3):
            write(self.root, "src/f%d.rs" % i, "pub fn f() {}\n")
        self.assertEqual(stack_rust.evidence(self.root), (3 + 5) * 2)

    def test_crate_of_reads_name_lib_and_bins(self):
        write(self.root, "Cargo.toml", '[package]\nname = "calc-x"\n')
        write(self.root, "src/lib.rs", "")
        write(self.root, "src/main.rs", "")
        write(self.root, "src/bin/tool.rs", "")
        write(self.root, "src/calc/add.rs", "")
        info = stack_rust.crate_of(self.root, "src/calc/add.rs")
        self.assertEqual(info, stack_rust.CrateInfo(
            dir="", name="calc_x", lib="src/lib.rs",
            bins=("src/bin/tool.rs", "src/main.rs")))

    def test_lib_name_and_explicit_paths_win(self):
        write(self.root, "crates/k/Cargo.toml",
              "[package]\nname = 'kpkg'  # a comment\n\n"
              '[lib]\nname = "klib"\npath = "lib/k.rs"\n\n'
              '[[bin]]\nname = "kcli"\npath = "cli/main.rs"\n\n'
              '[dependencies]\nname = "not-the-crate"\n')
        write(self.root, "crates/k/lib/k.rs", "")
        info = stack_rust.crate_of(self.root, "crates/k/lib/k.rs")
        self.assertEqual(info, stack_rust.CrateInfo(
            dir="crates/k", name="klib", lib="crates/k/lib/k.rs",
            bins=("crates/k/cli/main.rs",)))

    def test_a_quoted_table_header_opens_an_ignored_table(self):
        # NEGATIVE (ruling R6). `[target.'cfg(unix)'.dependencies.foo]` did
        # not match the header pattern, so its `path = ...` landed in the
        # table before it -- here `[lib]`, overwriting `[lib] path`.
        write(self.root, "Cargo.toml",
              '[package]\nname = "calcx"\n\n[lib]\npath = "src/real.rs"\n\n'
              "[target.'cfg(unix)'.dependencies.foo]\npath = \"../foo\"\n\n"
              '[[bin]]\nname = "tool"\npath = "src/tool.rs"\n'
              '[target."cfg(windows)".dependencies]\npath = "../win"\n'
              '[ weird header with spaces ]\nname = "not-the-crate"\n')
        write(self.root, "src/real.rs", "")
        info = stack_rust.crate_of(self.root, "src/real.rs")
        self.assertEqual(info, stack_rust.CrateInfo(
            dir="", name="calcx", lib="src/real.rs", bins=("src/tool.rs",)))
        tables = [t for t, _kv in stack_rust._toml_tables(
            "[a]\n[target.'cfg(unix)'.x]\nk = 'v'\n[[b]]\n")]
        self.assertEqual(tables, ["", "a", None, "b"])

    def test_no_lib_file_means_no_lib(self):
        write(self.root, "Cargo.toml", CALCX_TOML)
        write(self.root, "src/main.rs", "fn main() {}\n")
        info = stack_rust.crate_of(self.root, "src/main.rs")
        self.assertIsNone(info.lib)
        self.assertEqual(info.bins, ("src/main.rs",))

    def test_crate_of_walks_up_to_the_nearest_package(self):
        # A workspace root with no [package] is not a crate; its member is,
        # and a nested package is nearer than an outer one.
        write(self.root, "Cargo.toml", '[workspace]\nmembers = ["m"]\n')
        write(self.root, "m/Cargo.toml", '[package]\nname = "m"\n')
        write(self.root, "m/inner/Cargo.toml", '[package]\nname = "inner"\n')
        self.assertEqual(stack_rust.crate_of(self.root, "m/src/x/y.rs").dir, "m")
        self.assertEqual(stack_rust.crate_of(self.root, "m/inner/src/lib.rs").name, "inner")
        # NEGATIVE: a file under no package belongs to no crate.
        self.assertIsNone(stack_rust.crate_of(self.root, "scripts/x.rs"))


# ── Files ────────────────────────────────────────────────────────────────

class TestFiles(RustCase):
    def test_iter_source_files_prunes_what_is_not_library_source(self):
        write(self.root, "Cargo.toml", CALCX_TOML)
        write(self.root, "crates/m/Cargo.toml", '[package]\nname = "m"\n')
        for rel in ("src/lib.rs", "src/examples/kept.rs", "src/notes.txt",
                    "tests/it.rs", "tests/common/mod.rs",
                    # NEGATIVE, each: not source.
                    "target/debug/build/x.rs", "vendor/v/src/lib.rs",
                    ".hidden/h.rs", "_scratch/s.rs", "node_modules/n/n.rs",
                    "examples/demo.rs", "benches/b.rs", "build.rs",
                    "crates/m/src/lib.rs", "crates/m/examples/e.rs",
                    "crates/m/benches/b.rs", "crates/m/build.rs", "crates/m/tests/t.rs"):
            write(self.root, rel, "fn f() {}\n")
        self.assertEqual(list(stack_rust.iter_source_files(self.root)),
                         ["crates/m/src/lib.rs", "src/examples/kept.rs", "src/lib.rs"])
        self.assertEqual(list(stack_rust.iter_source_files(self.root, include_tests=True)),
                         ["crates/m/src/lib.rs", "crates/m/tests/t.rs",
                          "src/examples/kept.rs", "src/lib.rs",
                          "tests/common/mod.rs", "tests/it.rs"])

    def test_is_test_path_is_tests_directly_under_a_crate_root(self):
        write(self.root, "Cargo.toml", CALCX_TOML)
        write(self.root, "crates/m/Cargo.toml", '[package]\nname = "m"\n')
        stack_rust.iter_source_files(self.root)
        self.assertTrue(stack_rust.is_test_path("tests/it.rs"))
        self.assertTrue(stack_rust.is_test_path("tests/common/mod.rs"))
        self.assertTrue(stack_rust.is_test_path("crates/m/tests/it.rs"))
        # NEGATIVE: a module NAMED tests inside src is library source.
        self.assertFalse(stack_rust.is_test_path("src/tests.rs"))
        self.assertFalse(stack_rust.is_test_path("src/tests/x.rs"))
        self.assertFalse(stack_rust.is_test_path("crates/m/src/lib.rs"))

    def test_is_test_for_is_the_same_crate(self):
        write(self.root, "Cargo.toml", CALCX_TOML)
        write(self.root, "crates/m/Cargo.toml", '[package]\nname = "m"\n')
        stack_rust.iter_source_files(self.root)
        self.assertTrue(stack_rust.is_test_for("tests/it.rs", "src/calc/add.rs"))
        self.assertTrue(stack_rust.is_test_for("src/calc/add.rs", "src/calc/add.rs"))
        # NEGATIVE: another crate's test is not positioned for this source.
        self.assertFalse(stack_rust.is_test_for("crates/m/tests/it.rs", "src/calc/add.rs"))
        self.assertFalse(stack_rust.is_test_for("tests/it.rs", "crates/m/src/lib.rs"))

    def test_scope_files_is_the_file(self):
        self.assertEqual(stack_rust.scope_files("src/a.rs", ["src/a.rs", "src/b.rs"]),
                         ["src/a.rs"])


# ── Naming and lexing ────────────────────────────────────────────────────

class TestNaming(CalcxCase):
    def test_module_of(self):
        m = stack_rust.module_of
        self.assertEqual(m("src/calc/add.rs"), "add")
        self.assertEqual(m("src/calc/mod.rs"), "calc")
        self.assertEqual(m("src/lib.rs"), "calcx")
        self.assertEqual(m("src/main.rs"), "main")

    def test_path_pattern_is_the_module_path_bounded_at_both_ends(self):
        p = stack_rust.path_pattern("src/calc/add.rs")
        # Ruling R5 (fix round 1) overrides the brief's optional
        # `(?:crate::|<crate>::)?` prefix: the crate name is required.
        self.assertEqual(p.pattern, r"(?<![A-Za-z0-9_:])calcx::calc::add(?![A-Za-z0-9_])")
        self.assertTrue(p.search("use calcx::calc::add::sum;"))
        self.assertTrue(p.search("calcx::calc::add::sum(1)"))
        # NEGATIVE: another crate's, a bare, a `crate::` (in a tests/ file,
        # the test crate's own), or a different module path.
        self.assertFalse(p.search("use other::calc::add::sum;"))
        self.assertFalse(p.search("calc::add::sum(1)"))
        self.assertFalse(p.search("crate::calc::add::sum(1)"))
        self.assertFalse(p.search("use calcx::calc::adder;"))
        self.assertFalse(p.search("use calcx::mycalc::add;"))
        self.assertEqual(stack_rust.path_pattern("src/calc/mod.rs").pattern,
                         r"(?<![A-Za-z0-9_:])calcx::calc(?![A-Za-z0-9_])")

    def test_a_one_segment_path_needs_its_crate_prefix(self):
        # NEGATIVE: `calc` alone is a bare word, not path-qualified evidence.
        p = stack_rust.path_pattern("src/calc.rs")
        self.assertTrue(p.search("calcx::calc::f()"))
        self.assertFalse(p.search("let calc = 1; calc::f()"))

    def test_another_crates_use_group_is_not_this_path(self):
        # NEGATIVE (fix round 1, ruling R5): with an optional prefix, the bare
        # tail matched inside another crate's use-group.
        p = stack_rust.path_pattern("src/calc/add.rs")
        self.assertFalse(p.search("use otherx::{calc::add};"))
        # This crate's own group is credited through module_bindings instead.
        self.assertEqual(stack_rust.module_bindings(
            "add", "use calcx::{calc::add};\n", src_rel="src/calc/add.rs",
            ref_rel="tests/it.rs"), (("add",), ()))

    def test_a_crate_root_has_no_path(self):
        self.assertIsNone(stack_rust.path_pattern("src/lib.rs"))
        self.assertIsNone(stack_rust.path_pattern("src/main.rs"))
        self.assertIsNone(stack_rust.path_pattern("tests/it.rs"))

    def test_identifier_re_includes_raw_identifiers(self):
        self.assertEqual(stack_rust.IDENTIFIER_RE.findall("2x + y_1 + r#type"),
                         ["y_1", "r#type"])

    def test_preceding_qualifier(self):
        text = "calc::sum(1); v.sum(); foo()::sum; sum(2); 0..sum"
        quals = [stack_rust.preceding_qualifier(text, m.start())
                 for m in stack_rust.IDENTIFIER_RE.finditer(text) if m.group(0) == "sum"]
        self.assertEqual(quals, ["calc", "v", "", None, None])

    def test_name_pattern_is_word_bounded(self):
        p = stack_rust.name_pattern("pure")
        self.assertTrue(p.search("let x = pure(1);"))
        self.assertFalse(p.search("let x = impure(1);"))
        self.assertFalse(p.search("let x = pure_x(1);"))


# ── The crate index: current root, and the fallback ──────────────────────

class TestCrateIndex(RustCase):
    def test_without_an_index_only_in_crate_forms_work(self):
        # Coordinator ruling (a). No walk: the crate dir is derived from the
        # path and the crate NAME is unknown, so every crate-name form binds
        # or credits nothing -- under-credit only.
        _no_index()
        self.assertEqual(stack_rust.module_of("src/lib.rs"), "lib")
        self.assertTrue(stack_rust.is_test_path("tests/it.rs"))
        self.assertFalse(stack_rust.is_test_path("src/tests/x.rs"))
        self.assertTrue(stack_rust.is_test_for("crates/m/tests/it.rs", "crates/m/src/x.rs"))
        self.assertFalse(stack_rust.is_test_for("tests/it.rs", "crates/m/src/x.rs"))
        # Ruling R5: with no crate name there is no path to require.
        self.assertIsNone(stack_rust.path_pattern("src/a/b.rs"))
        bind = stack_rust.module_bindings
        self.assertEqual(bind("b", "use calcx::a::b;\n", src_rel="src/a/b.rs",
                              ref_rel="tests/it.rs"), ((), ()))
        self.assertEqual(bind("b", "use crate::a::b;\n", src_rel="src/a/b.rs",
                              ref_rel="src/other.rs"), (("b",), ()))
        self.assertEqual(bind("a", "#[cfg(test)]\nmod tests {\n    use super::*;\n}\n",
                              src_rel="src/a.rs", ref_rel="src/a.rs"), ((), ("*",)))
        mp = stack_rust.name_pattern("Report::total", module="calc")
        self.assertIsNone(mp.search("fn t() { let r = calcx::Report::new(); r.total(); }"))
        self.assertTrue(mp.search("fn t() { let r = calc::Report::new(); r.total(); }"))

    def test_the_last_walked_root_answers(self):
        # Coordinator ruling (b): each root-less function answers for the
        # root walked LAST, and walking the first again brings it back.
        alpha = tempfile.mkdtemp(prefix="tsn-rust-alpha-")
        beta = tempfile.mkdtemp(prefix="tsn-rust-beta-")
        for r in (alpha, beta):
            self.addCleanup(shutil.rmtree, r, ignore_errors=True)
        write(alpha, "Cargo.toml", '[package]\nname = "alpha"\n')
        write(beta, "Cargo.toml", '[package]\nname = "beta"\n')
        for r in (alpha, beta):
            write(r, "src/lib.rs", "pub mod a;\n")
            write(r, "src/a/b.rs", "pub fn run() {}\n")
        use = "use %s::a::b;\n"

        def answers():
            return (stack_rust.module_of("src/lib.rs"),
                    stack_rust.path_pattern("src/a/b.rs").pattern,
                    stack_rust.module_bindings("b", use % "alpha", src_rel="src/a/b.rs",
                                               ref_rel="tests/it.rs"),
                    stack_rust.module_bindings("b", use % "beta", src_rel="src/a/b.rs",
                                               ref_rel="tests/it.rs"),
                    bool(stack_rust.name_pattern("R::m", module="b").search(
                        "fn t() { let x = alpha::R::new(); x.m(); }")))

        stack_rust.iter_source_files(alpha)
        stack_rust.iter_source_files(beta)
        self.assertEqual(answers(), (
            "beta", r"(?<![A-Za-z0-9_:])beta::a::b(?![A-Za-z0-9_])",
            ((), ()), (("b",), ()), False))
        stack_rust.iter_source_files(alpha)
        # The last `True`: alpha's index holds exactly ONE crate, so its name
        # may qualify `R` (fix round 1 keeps that; a second crate in the
        # index removes it -- test_a_second_crates_name_qualifies_nothing).
        self.assertEqual(answers(), (
            "alpha", r"(?<![A-Za-z0-9_:])alpha::a::b(?![A-Za-z0-9_])",
            (("b",), ()), ((), ()), True))

    def test_a_re_walk_sees_a_changed_manifest(self):
        write(self.root, "Cargo.toml", '[package]\nname = "before"\n')
        write(self.root, "src/lib.rs", "")
        stack_rust.iter_source_files(self.root)
        self.assertEqual(stack_rust.module_of("src/lib.rs"), "before")
        write(self.root, "Cargo.toml", '[package]\nname = "after"\n')
        stack_rust.iter_source_files(self.root)
        self.assertEqual(stack_rust.module_of("src/lib.rs"), "after")


# ── Import/binding grammar ───────────────────────────────────────────────

class TestBindingGrammar(CalcxCase):
    def bind(self, text, src="src/a/b.rs", ref="tests/it.rs"):
        return stack_rust.module_bindings(stack_rust.module_of(src), text,
                                          src_rel=src, ref_rel=ref)

    def test_a_use_of_the_module_binds_an_alias(self):
        self.assertEqual(self.bind("use calcx::a::b;\n"), (("b",), ()))
        self.assertEqual(self.bind("use calcx::a::b as bb;\n"), (("bb",), ()))
        self.assertEqual(self.bind("use calcx::a::b::{self as m};\n"), (("m",), ()))
        self.assertEqual(self.bind("pub use ::calcx::a::b;\n"), (("b",), ()))

    def test_a_group_of_items_binds_names(self):
        self.assertEqual(self.bind("use calcx::a::{b, c as d};\n", src="src/a.rs"),
                         ((), ("b", "d")))
        self.assertEqual(self.bind("use calcx::{a::{run as q}, other};\n", src="src/a.rs"),
                         ((), ("q",)))

    def test_a_glob_binds_every_name(self):
        self.assertEqual(self.bind("use calcx::a::*;\n", src="src/a.rs"), ((), ("*",)))

    def test_use_super_glob_in_the_files_own_test_module(self):
        text = "pub fn run() {}\n#[cfg(test)]\nmod tests {\n    use super::*;\n}\n"
        self.assertEqual(self.bind(text, src="src/a.rs", ref="src/a.rs"), ((), ("*",)))

    def test_use_super_glob_in_another_files_test_module_binds_nothing(self):
        # NEGATIVE: `super` there is src/other.rs's own module.
        text = "#[cfg(test)]\nmod tests {\n    use super::*;\n}\n"
        self.assertEqual(self.bind(text, src="src/a.rs", ref="src/other.rs"), ((), ()))

    def test_use_crate_counts_only_inside_the_same_crate(self):
        self.assertEqual(self.bind("use crate::a::b;\n", ref="src/other.rs"), (("b",), ()))
        # NEGATIVE: a tests/ file is its own crate; `crate::` there is not calcx.
        self.assertEqual(self.bind("use crate::a::b;\n", ref="tests/it.rs"), ((), ()))

    def test_use_crate_in_another_crate_binds_nothing(self):
        # NEGATIVE: a second package's `crate::a::b` is its own module.
        write(self.root, "other/Cargo.toml", '[package]\nname = "otherx"\n')
        write(self.root, "other/src/lib.rs", "use crate::a::b;\n")
        stack_rust.iter_source_files(self.root)
        self.assertEqual(self.bind("use crate::a::b;\n", ref="other/src/lib.rs"), ((), ()))

    def test_a_path_to_another_module_binds_nothing(self):
        # NEGATIVE: the predicate may under-credit, never over-credit.
        for text in ("use calcx::other::b;\n", "use otherx::a::b;\n", "use calcx::a::bee;\n",
                     "use calcx::a::b as _;\n"):
            with self.subTest(text=text):
                self.assertEqual(self.bind(text), ((), ()))

    def test_a_commented_or_quoted_use_binds_nothing(self):
        # NEGATIVE.
        self.assertEqual(self.bind("// use calcx::a::b;\nlet s = \"use calcx::a::b;\";\n"),
                         ((), ()))

    def reached(self, name, text, src="src/a.rs", ref="tests/it.rs"):
        return stack_rust.reached_through_module(stack_rust.module_of(src), name, text,
                                                 src_rel=src, ref_rel=ref)

    def test_a_call_through_the_alias_is_reached(self):
        self.assertTrue(self.reached("run", "use calcx::a::b;\nfn t() { b::run(1); }\n",
                                     src="src/a/b.rs"))

    def test_a_bare_call_after_a_named_or_glob_binding_is_reached(self):
        self.assertTrue(self.reached("run", "use calcx::a::{run};\nfn t() { run(1); }\n"))
        self.assertTrue(self.reached("run", "use calcx::a::*;\nfn t() { run::<u8>(1); }\n"))

    def test_naming_it_without_calling_it_is_not_reached(self):
        # NEGATIVE: a fn value, a string, a method of the same name, and a
        # call with no binding at all are not evidence the unit ran.
        for text in ("use calcx::a::*;\nfn t() { let f = run; }\n",
                     'use calcx::a::*;\nfn t() { let s = "run(1)"; }\n',
                     "use calcx::a::*;\nfn t() { x.run(1); other::run(1); }\n",
                     "fn t() { run(1); }\n"):
            with self.subTest(text=text):
                self.assertFalse(self.reached("run", text))


    def test_a_method_needs_its_type_imported_not_just_the_module(self):
        # NEGATIVE (fix round 1, finding 2): binding some OTHER item of the
        # module does not make a bare `Report` this module's type.
        text = ("use calcx::calc::helper;\nuse otherx::Report;\n"
                "#[test]\nfn t() { let r = Report::new(); r.total(); }\n")
        self.assertFalse(self.reached("Report::total", text, src="src/calc.rs"))
        # NEGATIVE: an alias of the module admits `alias::Report`, not a bare one.
        self.assertFalse(self.reached(
            "Report::total", "use calcx::calc as c;\n#[test]\n"
            "fn t() { let r = Report::new(); r.total(); }\n", src="src/calc.rs"))
        # ...while importing the type, a glob, or the module's alias reaches it.
        for text in ("use calcx::calc::Report;\n#[test]\n"
                     "fn t() { let r = Report::new(); r.total(); }\n",
                     "use calcx::calc::*;\n#[test]\n"
                     "fn t() { let r = Report::new(); r.total(); }\n",
                     "use calcx::calc as c;\n#[test]\n"
                     "fn t() { let r = c::Report::new(); r.total(); }\n"):
            with self.subTest(text=text):
                self.assertTrue(self.reached("Report::total", text, src="src/calc.rs"))

    def test_the_lib_root_is_not_a_bin_roots_crate(self):
        # NEGATIVE (fix round 1, finding 3): both roots have module path `[]`,
        # but `super` in the lib's test module is the LIBRARY, not main.rs.
        text = "mod tests {\n    use super::*;\n    #[test]\n    fn t() { run(); }\n}\n"
        self.assertFalse(self.reached("run", text, src="src/main.rs", ref="src/lib.rs"))
        self.assertFalse(self.reached("run", text, src="src/main.rs", ref="src/bin/tool.rs"))
        # ...while main.rs's own test module still reaches main.rs.
        self.assertTrue(self.reached("run", text, src="src/main.rs", ref="src/main.rs"))

    def test_a_renamed_import_credits_only_the_name_it_imported(self):
        # NEGATIVE (fix round 1, ruling R4): this `run` is `helper`, renamed.
        self.assertFalse(self.reached("run", "use calcx::a::{helper as run};\n"
                                             "fn t() { run(); }\n"))
        # ...and the unit imported under another name is reached by that name.
        self.assertTrue(self.reached("run", "use calcx::a::{run as go};\nfn t() { go(1); }\n"))


# ── Method credit ────────────────────────────────────────────────────────

class TestMethodCredit(CalcxCase):
    """`Type::m` is credited only by a call on a value bound to `Type` in the
    same fn body, or by a direct call on the type -- Go's rule, and the three
    shapes Go's second review broke are the NEGATIVEs here."""

    def credits(self, body):
        p = stack_rust.name_pattern("Report::total", module="calc")
        return bool(p.search("use calcx::calc::Report;\n\n#[test]\nfn t() {\n    %s\n}\n" % body))

    def test_each_way_of_holding_a_value_credits(self):
        for body in ("let r = Report::new(1); r.total();",
                     "let mut r = Report { n: 1 }; r.total();",
                     "let r = Report::default(); r.total();",
                     "let r: Report = make(); r.total();",
                     "let r: &Report = &make(); r.total();",
                     "Report::total(&make());",
                     "(&Report { n: 1 }).total();",
                     "Report::new(1).total();",
                     "let r = calc::Report::new(1); r.total();",
                     "let r = calcx::Report::new(1); r.total();",
                     "let r = calcx::calc::Report::new(1); r.total();",
                     "let r = crate::calc::Report::new(1); r.total::<u8>();"):
            with self.subTest(body=body):
                self.assertTrue(self.credits(body))

    def test_a_parameter_binds(self):
        p = stack_rust.name_pattern("Report::total", module="calc")
        for sig in ("fn check(r: &Report) -> u32", "fn check(r: Report, n: u32)",
                    "fn check<'a>(r: &'a mut Report)"):
            with self.subTest(sig=sig):
                self.assertTrue(p.search("%s { r.total() }\n" % sig))

    def test_another_crates_type_of_the_same_name_credits_nothing(self):
        # NEGATIVE (Go review shape 1: any qualifier was accepted).
        self.assertFalse(self.credits("(&other::Report{}).total();"))
        self.assertFalse(self.credits("let r = other::Report::new(); r.total();"))
        self.assertFalse(self.credits("let r = other::calc::Report::new(); r.total();"))

    def test_a_longer_type_name_is_another_type(self):
        # NEGATIVE (Go review shape 2: `New<T>*` bound `NewParserConfig`).
        self.assertFalse(self.credits("let r = ReportConfig::new(); r.total();"))
        self.assertFalse(self.credits("let r = MyReport { n: 1 }; r.total();"))

    def test_a_binding_in_one_fn_does_not_carry_into_another(self):
        # NEGATIVE (Go review shape 3).
        p = stack_rust.name_pattern("Report::total", module="calc")
        self.assertFalse(p.search(
            "#[test]\nfn a() { let r = Report::new(1); }\n"
            "#[test]\nfn b() { let r = Cache::new(); r.total(); }\n"))

    def test_a_call_on_an_unbound_value_credits_nothing(self):
        # NEGATIVE: the type named, `.total()` called on something else.
        self.assertFalse(self.credits("let _ty = std::any::type_name::<Report>(); buf.total();"))
        self.assertFalse(self.credits("// let r = Report::new(1);\n    r.total();"))
        self.assertFalse(self.credits("let r = Report::new(1); r.totals();"))


class TestMethodCreditInAWorkspace(CalcxCase):
    def test_a_second_crates_name_qualifies_nothing(self):
        # NEGATIVE (fix round 1, finding 1). With `calcx` and `otherx` both
        # indexed, `name_pattern` -- given no path -- cannot tell which crate
        # the unit is in, so no crate name qualifies `Type` at all.
        write(self.root, "other/Cargo.toml", '[package]\nname = "otherx"\n')
        write(self.root, "other/src/lib.rs", "")
        stack_rust.iter_source_files(self.root)
        p = stack_rust.name_pattern("Report::total", module="calc")
        for body in ("let r = otherx::Report::new(); r.total();",
                     "let r = otherx::calc::Report::new(); r.total();",
                     "let r = calcx::Report::new(); r.total();"):
            with self.subTest(body=body):
                self.assertIsNone(p.search("#[test]\nfn t() {\n    %s\n}\n" % body))
        # The unit's own module still qualifies it.
        self.assertTrue(p.search("#[test]\nfn t() { let r = calc::Report::new(1); r.total(); }\n"))


class TestInterfaceNames(unittest.TestCase):
    """The names this task supplies; discovery and triage land in Tasks 3-4."""

    NAMES = ("STACK_NAME", "MANIFESTS", "classify_manifest", "evidence",
             "iter_source_files", "is_test_path", "is_test_for", "scope_files",
             "module_of", "name_pattern", "path_pattern", "IDENTIFIER_RE",
             "preceding_qualifier", "strip_noncode", "module_bindings",
             "reached_through_module", "crate_of", "CrateInfo")

    def test_supplies_every_task_2_name(self):
        self.assertEqual([n for n in self.NAMES if not hasattr(stack_rust, n)], [])
        self.assertEqual(stack_rust.STACK_NAME, "rust")
        self.assertEqual(stack_rust.MANIFESTS, ("Cargo.toml",))
        self.assertEqual(stack_rust.IDENTIFIER_RE.pattern,
                         r"(?<![A-Za-z0-9_])(?:r#)?[A-Za-z_][A-Za-z0-9_]*")
        self.assertTrue(isinstance(stack_rust.IDENTIFIER_RE, re.Pattern))


if __name__ == "__main__":
    unittest.main()
