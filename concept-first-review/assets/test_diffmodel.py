#!/usr/bin/env python3
"""Unit suite for diffmodel: parsing, dependency-row detection, move detection."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import diffmodel
from diffmodel import ADD, CONTEXT, DEL, FILE_HEADER, HUNK_HEADER


GO_DIFF = (
    "diff --git a/pkg/token.go b/pkg/token.go\n"
    "index 1111111..2222222 100644\n"
    "--- a/pkg/token.go\n"
    "+++ b/pkg/token.go\n"
    "@@ -1,8 +1,9 @@\n"
    " package pkg\n"
    " \n"
    " import (\n"
    ' 	"fmt"\n'
    '-	"math/rand"\n'
    '+	"crypto/rand"\n'
    '+	"encoding/hex"\n'
    " )\n"
    " \n"
    "-	return fmt.Sprintf(\"%x\", b)\n"
    "+	if _, err := rand.Read(b); err != nil {\n"
    "+		return \"\", err\n"
    "+	}\n"
    "+	return hex.EncodeToString(b), nil\n"
)


class TestParse(unittest.TestCase):
    def test_kinds_and_indices(self):
        rows = diffmodel.parse_diff(GO_DIFF)
        self.assertEqual(rows[0].kind, FILE_HEADER)
        self.assertEqual(rows[1].kind, FILE_HEADER)  # index line
        self.assertEqual(rows[4].kind, HUNK_HEADER)
        self.assertEqual(rows[5].kind, CONTEXT)
        self.assertEqual(rows[5].source, "package pkg")
        self.assertEqual(rows[0].path, "pkg/token.go")
        self.assertTrue(all(r.file_idx == 0 for r in rows))

    def test_minus_minus_inside_hunk_is_a_deletion(self):
        text = (
            "diff --git a/a.txt b/a.txt\n"
            "--- a/a.txt\n"
            "+++ b/a.txt\n"
            "@@ -1,2 +1,2 @@\n"
            "--- not a header\n"
            "+++ also not a header\n"
        )
        rows = diffmodel.parse_diff(text)
        self.assertEqual(rows[1].kind, FILE_HEADER)
        self.assertEqual(rows[2].kind, FILE_HEADER)
        self.assertEqual(rows[4].kind, DEL)
        self.assertEqual(rows[4].source, "-- not a header")
        self.assertEqual(rows[5].kind, ADD)

    def test_bare_diff_without_diff_git(self):
        text = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n"
        rows = diffmodel.parse_diff(text)
        self.assertEqual(rows[0].kind, FILE_HEADER)
        self.assertEqual(rows[0].path, "x.py")
        self.assertEqual(rows[3].kind, DEL)

    def test_index_gutter_is_one_based(self):
        out = diffmodel.index_diff("alpha\nbeta\n")
        self.assertEqual(out, "  1 │ alpha\n  2 │ beta")

    def test_index_column_widens_with_the_diff(self):
        out = diffmodel.index_diff("\n".join("row%d" % i for i in range(1200)))
        first, last = out.split("\n")[0], out.split("\n")[-1]
        self.assertTrue(first.startswith("   1 │ "), first)
        self.assertTrue(last.startswith("1200 │ "), last)

    def test_multiple_files_get_distinct_indices(self):
        text = GO_DIFF + (
            "diff --git a/b/other.py b/b/other.py\n"
            "--- a/b/other.py\n"
            "+++ b/b/other.py\n"
            "@@ -1 +1 @@\n"
            "-x = 1\n"
            "+x = 2\n"
        )
        rows = diffmodel.parse_diff(text)
        self.assertEqual(max(r.file_idx for r in rows), 1)
        self.assertEqual(rows[-1].path, "b/other.py")


class TestTemplate(unittest.TestCase):
    def test_go_import_block_including_unchanged_framing(self):
        rows = diffmodel.parse_diff(GO_DIFF)
        hit = diffmodel.declaration_rows(rows)
        # `import (`, the two specs, both changed specs, and the closing `)`.
        for lineno in (8, 9, 10, 11, 12, 13):
            self.assertIn(lineno, hit, "line %d should be a dependency row" % lineno)
        self.assertNotIn(6, hit)  # package pkg
        self.assertNotIn(16, hit)  # behavioural row

    def test_python_parenthesized_import_block(self):
        text = (
            "--- a/m.py\n+++ b/m.py\n@@ -1,5 +1,5 @@\n"
            "+from app.core import (\n"
            "+    registry,\n"
            "+    loader,\n"
            "+)\n"
            "+value = registry.get()\n"
        )
        rows = diffmodel.parse_diff(text)
        hit = diffmodel.declaration_rows(rows)
        self.assertEqual(hit, {4, 5, 6, 7})

    def test_js_multiline_import_and_require(self):
        text = (
            "--- a/m.ts\n+++ b/m.ts\n@@ -1,5 +1,5 @@\n"
            "+import {\n"
            "+  parse,\n"
            "+} from './parse';\n"
            "+const fs = require('fs');\n"
            "+const out = parse(fs.readFileSync(p));\n"
        )
        rows = diffmodel.parse_diff(text)
        self.assertEqual(diffmodel.declaration_rows(rows), {4, 5, 6, 7})

    def test_c_include_and_rust_use(self):
        text = (
            "--- a/m.c\n+++ b/m.c\n@@ -1 +1 @@\n"
            "+#include <stdio.h>\n"
            "diff --git a/m.rs b/m.rs\n--- a/m.rs\n+++ b/m.rs\n@@ -1 +1 @@\n"
            "+use std::collections::HashMap;\n"
            "+let m = HashMap::new();\n"
        )
        rows = diffmodel.parse_diff(text)
        hit = diffmodel.declaration_rows(rows)
        self.assertIn(4, hit)
        self.assertIn(9, hit)
        self.assertNotIn(10, hit)

    def test_negative_a_call_named_import_is_not_a_dependency_row(self):
        text = (
            "--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n"
            "+result = importer.import_all(records)\n"
        )
        rows = diffmodel.parse_diff(text)
        self.assertEqual(diffmodel.declaration_rows(rows), set())


MOVE_DIFF = (
    "diff --git a/old.py b/old.py\n"
    "--- a/old.py\n"
    "+++ b/old.py\n"
    "@@ -1,6 +1,2 @@\n"
    " def handler(request):\n"
    "-    config_filters = config.getini('filterwarnings')\n"
    "-    apply_warning_filters(config_filters, cmdline_filters)\n"
    "-    record = build_audit_record(request, config_filters)\n"
    "+    return delegate(request)\n"
    "diff --git a/new.py b/new.py\n"
    "--- a/new.py\n"
    "+++ b/new.py\n"
    "@@ -1,2 +1,6 @@\n"
    " def delegate(request):\n"
    "+        config_filters = config.getini('filterwarnings')\n"
    "+        apply_warning_filters(config_filters, cmdline_filters)\n"
    "+        record = build_audit_record(request, config_filters)\n"
)


class TestMoves(unittest.TestCase):
    def test_detects_reindented_cross_file_move(self):
        rows = diffmodel.parse_diff(MOVE_DIFF)
        moves = diffmodel.detect_relocations(rows)
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["origin"], [6, 8])
        self.assertEqual(moves[0]["destination"], [15, 17])

    def test_relocation_notes_read_as_prose(self):
        rows = diffmodel.parse_diff(MOVE_DIFF)
        notes = diffmodel.relocation_notes(diffmodel.detect_relocations(rows))
        self.assertEqual(notes, ["lines 6-8 moved to lines 15-17"])

    def test_negative_too_small_to_be_a_move(self):
        text = (
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n"
            "-x = compute(y)\n"
            "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n"
            "+x = compute(y)\n"
        )
        rows = diffmodel.parse_diff(text)
        self.assertEqual(diffmodel.detect_relocations(rows), [])

    def test_negative_same_hunk_is_not_a_move(self):
        text = (
            "--- a/a.py\n+++ b/a.py\n@@ -1,8 +1,8 @@\n"
            "-    config_filters = config.getini('filterwarnings')\n"
            "-    apply_warning_filters(config_filters, cmdline_filters)\n"
            "-    record = build_audit_record(request, config_filters)\n"
            "+    config_filters = config.getini('filterwarnings')\n"
            "+    apply_warning_filters(config_filters, cmdline_filters)\n"
            "+    record = build_audit_record(request, config_filters)\n"
        )
        rows = diffmodel.parse_diff(text)
        self.assertEqual(diffmodel.detect_relocations(rows), [])

    def test_negative_inconsistent_reindent_breaks_the_pair(self):
        text = MOVE_DIFF.replace(
            "+        record = build_audit_record(request, config_filters)",
            "+            record = build_audit_record(request, config_filters)",
        )
        rows = diffmodel.parse_diff(text)
        self.assertEqual(diffmodel.detect_relocations(rows), [])

    def test_language_detection(self):
        self.assertEqual(diffmodel.language_of("a/b/c.py"), "python")
        self.assertEqual(diffmodel.language_of("a/b/c.tsx"), "js")
        self.assertEqual(diffmodel.language_of("Makefile"), "generic")


if __name__ == "__main__":
    unittest.main(verbosity=1)
