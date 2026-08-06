#!/usr/bin/env python3
"""Unit suite for signals: each detector fires on its shape and stays quiet otherwise.

The negative cases matter more than the positive ones here. A signal extractor
that fires on everything is noise, and noise is how a reviewer learns to ignore
the tool.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import diffmodel
import signals as sig


def extract(text, baseline=None):
    return sig.extract(diffmodel.parse_diff(text), baseline)


def ids(sigs):
    return {s["id"] for s in sigs}


def wrap(path, body):
    lines = body.strip("\n").split("\n")
    return (
        "diff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n@@ -1,2 +1,%d @@\n" % (path, path, path, path, len(lines) + 1)
        + "\n".join(lines)
        + "\n"
    )


class TestAlgorithm(unittest.TestCase):
    def test_nested_loop_and_io_in_loop(self):
        out = extract(wrap("svc/orders.py", """
+def total(orders):
+    rows = []
+    for order in orders:
+        for item in order.items:
+            rows.append(db.query(item.id))
+    return rows
"""))
        self.assertIn("algo.nested-loop", ids(out))
        self.assertIn("algo.io-in-loop", ids(out))

    def test_negative_a_single_flat_loop_is_not_flagged(self):
        out = extract(wrap("svc/orders.py", """
+def total(orders):
+    n = 0
+    for order in orders:
+        n += order.amount
+    return n
"""))
        self.assertNotIn("algo.nested-loop", ids(out))
        self.assertNotIn("algo.io-in-loop", ids(out))

    def test_negative_for_loop_header_is_not_a_membership_scan(self):
        out = extract(wrap("svc/a.py", """
+for item in order.items:
+    total += item.price
"""))
        self.assertNotIn("algo.linear-scan-in-loop", ids(out))

    def test_membership_scan_inside_a_loop_is_flagged(self):
        out = extract(wrap("svc/a.py", """
+for item in items:
+    if item.id in seen_ids:
+        continue
"""))
        self.assertIn("algo.linear-scan-in-loop", ids(out))

    def test_negative_a_dict_lookup_in_a_loop_is_not_io(self):
        # `spec.get(...)` is an in-memory accessor. A detector that calls it a
        # round trip is the kind of noise reviewers learn to ignore.
        out = extract(wrap("svc/a.py", """
+for spec in specs:
+    flags = {"lossless": bool(spec.get("lossless", False))}
"""))
        self.assertNotIn("algo.io-in-loop", ids(out))

    def test_negative_a_continuation_row_is_not_a_loop_header(self):
        # The tail of a multi-line comprehension starts with `for`, but its
        # indentation says nothing about real nesting.
        out = extract(wrap("svc/a.py", """
+bad = [t for t in value
+       if isinstance(t, str)
+       for t in value)
"""))
        self.assertNotIn("algo.nested-loop", ids(out))

    def test_negative_prose_files_are_not_read_as_code(self):
        out = extract(wrap("docs/design-note.md", """
+The reasoning that produced this eval ("small model") is worth recording.
+We ran subprocess.run manually to confirm it.
"""))
        self.assertNotIn("bound.exec", ids(out))

    def test_negative_config_files_are_not_read_as_code(self):
        # A YAML comment listing supported languages once read as a goroutine.
        out = extract(wrap(".serena/project.yml", """
+#   fsharp              go                  groovy              haskell
+#   language: go
"""))
        self.assertNotIn("conc.spawned", ids(out))
        self.assertIn("cfg.changed", ids(out), "config still gets its path-based signal")

    def test_a_real_goroutine_still_fires(self):
        out = extract(wrap("svc/server.go", "+\tgo handleConn(c)\n+\tgo func() { drain() }()\n"))
        self.assertIn("conc.spawned", ids(out))

    def test_negative_the_word_go_in_prose_is_not_concurrency(self):
        out = extract(wrap("svc/a.py", "+    label = 'go home now'\n"))
        self.assertNotIn("conc.spawned", ids(out))

    def test_await_in_loop(self):
        out = extract(wrap("svc/a.js", """
+for (const id of ids) {
+  const row = await fetchRow(id);
+}
"""))
        self.assertIn("algo.await-in-loop", ids(out))

    def test_catastrophic_regex(self):
        out = extract(wrap("svc/a.py", """
+PATTERN = re.compile(r"(a+)+b")
"""))
        self.assertIn("algo.regex-backtracking", ids(out))

    def test_loop_budget_is_configurable(self):
        body = wrap("svc/a.py", """
+for a in xs:
+    for b in a.ys:
+        total += b
""")
        self.assertIn("algo.nested-loop", ids(extract(body)))
        relaxed = dict(sig.DEFAULT_BASELINE, budgets={"max_loop_depth": 3})
        self.assertNotIn("algo.nested-loop", ids(extract(body, relaxed)))


class TestDependencies(unittest.TestCase):
    def test_new_third_party_import_without_a_baseline(self):
        out = extract(wrap("app/a.py", "+import requests\n"))
        found = [s for s in out if s["id"] == "dep.new-external"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], "medium")

    def test_negative_stdlib_import_is_not_a_dependency(self):
        out = extract(wrap("app/a.py", "+import json\n+import os\n"))
        self.assertNotIn("dep.new-external", ids(out))

    def test_baseline_promotes_an_unlisted_dependency_to_high(self):
        baseline = dict(sig.DEFAULT_BASELINE, allowed_external=["httpx"], _configured=True)
        out = extract(wrap("app/a.py", "+import requests\n"), baseline)
        found = [s for s in out if s["id"] == "dep.new-external"]
        self.assertEqual(found[0]["severity"], "high")

    def test_baseline_accepts_a_listed_dependency(self):
        baseline = dict(sig.DEFAULT_BASELINE, allowed_external=["requests"], _configured=True)
        out = extract(wrap("app/a.py", "+import requests\n"), baseline)
        self.assertNotIn("dep.new-external", ids(out))

    def test_forbidden_layer_edge_becomes_a_fact(self):
        baseline = dict(
            sig.DEFAULT_BASELINE,
            layers={"core": ["core/"], "web": ["web/"]},
            forbidden_edges=[["core", "web"]],
            _configured=True,
        )
        out = extract(wrap("core/model.py", "+from web.handlers import render\n"), baseline)
        found = [s for s in out if s["id"] == "dep.layering"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], "high")

    def test_negative_allowed_direction_is_only_informational(self):
        baseline = dict(
            sig.DEFAULT_BASELINE,
            layers={"core": ["core/"], "web": ["web/"]},
            forbidden_edges=[["core", "web"]],
            _configured=True,
        )
        out = extract(wrap("web/handlers.py", "+from core.model import User\n"), baseline)
        self.assertNotIn("dep.layering", ids(out))
        self.assertIn("dep.new-edge", ids(out))

    def test_load_baseline_missing_file_falls_back(self):
        base = sig.load_baseline("/nonexistent/design-rules.json")
        self.assertFalse(base.get("_configured"))
        self.assertEqual(base["budgets"]["max_loop_depth"], 1)

    def test_load_baseline_merges_budgets(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "design-rules.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"allowed_external": ["requests"]}, fh)
        base = sig.load_baseline(path)
        self.assertTrue(base["_configured"])
        self.assertEqual(base["budgets"]["max_loop_depth"], 1)
        self.assertEqual(base["allowed_external"], ["requests"])


class TestSurfaceAndRuntime(unittest.TestCase):
    def test_public_api_signature_change(self):
        text = wrap("pkg/a.go", """
-func Serve(addr string) error {
+func Serve(ctx context.Context, addr string) error {
""")
        found = [s for s in extract(text) if s["id"] == "api.signature-changed"]
        self.assertEqual(len(found), 1)

    def test_negative_private_python_helper_is_not_public_surface(self):
        out = extract(wrap("pkg/a.py", "+def _helper(x):\n+    return x\n"))
        self.assertNotIn("api.surface-added", ids(out))

    def test_new_route_is_an_entry_point(self):
        out = extract(wrap("app/api.py", '+@app.route("/orders")\n+def orders():\n+    return []\n'))
        self.assertIn("api.route-added", ids(out))

    def test_swallowed_error_and_spawned_concurrency(self):
        out = extract(wrap("svc/a.py", """
+    try:
+        run()
+    except Exception:
+        pass
+    threading.Thread(target=run).start()
"""))
        self.assertIn("err.swallowed", ids(out))
        self.assertIn("conc.spawned", ids(out))

    def test_negative_a_handled_exception_is_not_swallowed(self):
        out = extract(wrap("svc/a.py", """
+    try:
+        run()
+    except ValueError as exc:
+        log.warning("run failed: %s", exc)
+        raise
"""))
        self.assertNotIn("err.swallowed", ids(out))

    def test_boundary_signals(self):
        out = extract(wrap("svc/a.py", """
+    subprocess.run([cmd, arg])
+    data = pickle.loads(blob)
"""))
        self.assertIn("bound.exec", ids(out))
        self.assertIn("bound.deserialize", ids(out))


class TestRustIdiom(unittest.TestCase):
    """Regressions from a real Rust review where three detectors misfired badly."""

    def test_unwrap_aborts_it_does_not_swallow(self):
        out = extract(wrap("src/graph.rs", "+    let g = Graph::new().unwrap();\n"))
        self.assertNotIn("err.swallowed", ids(out))
        self.assertIn("err.abort", ids(out))

    def test_a_discarded_result_still_counts_as_swallowed(self):
        out = extract(wrap("src/graph.rs", "+    let _ = flush_wal(&mut file);\n"))
        self.assertIn("err.swallowed", ids(out))

    def test_negative_crate_internal_use_is_not_a_dependency(self):
        for stmt in ("+use crate::index::RangeKey;\n",
                     "+use super::wal::WalEntry;\n",
                     "+use self::inner::Node;\n",
                     "+use std::collections::HashMap;\n"):
            out = extract(wrap("src/index/mod.rs", stmt))
            self.assertNotIn("dep.new-external", ids(out), stmt)

    def test_negative_a_scoped_use_is_not_a_dependency(self):
        out = extract(wrap("src/index/mod.rs", "+        use RangeKey::*;\n"))
        self.assertNotIn("dep.new-external", ids(out))

    def test_a_top_level_third_party_use_still_fires(self):
        out = extract(wrap("src/storage/mod.rs", "+use serde_json::json;\n"))
        self.assertIn("dep.new-external", ids(out))

    def test_negative_a_sibling_call_is_not_recursion(self):
        out = extract(wrap("src/perf.rs", """
+fn make_props(i: usize) -> Props {
+    Props::new(i)
+}
+fn run(g: &mut Graph) {
+    g.create_node(make_props(1));
+}
"""))
        self.assertNotIn("algo.recursion", ids(out))

    def test_negative_a_trait_method_dispatching_on_another_receiver(self):
        # `impl Ord for RangeKey { fn cmp(..) { a.cmp(b) } }` is dispatch, not
        # recursion — and the trait fixes the method name, so it is unavoidable.
        out = extract(wrap("src/index/mod.rs", """
+fn cmp(&self, other: &Self) -> Ordering {
+    match (self, other) {
+        (Str(a), Str(b)) => a.cmp(b),
+        _ => self.rank().cmp(&other.rank()),
+    }
+}
"""))
        self.assertNotIn("algo.recursion", ids(out))

    def test_a_self_dispatched_recursive_call_is_recursion(self):
        out = extract(wrap("src/tree.rs", """
+fn depth(&self, node: &Node) -> usize {
+    let d = self.depth(node.child());
+    d + 1
+}
"""))
        self.assertIn("algo.recursion", ids(out))

    def test_a_genuine_self_call_is_recursion(self):
        out = extract(wrap("src/walk.rs", """
+fn walk(node: &Node, depth: usize) -> usize {
+    let mut n = 1;
+    n += walk(node.child(), depth + 1);
+    n
+}
"""))
        self.assertIn("algo.recursion", ids(out))


class TestFormatterSweep(unittest.TestCase):
    """Regressions from a real lint/format PR that rewrote 110 files.

    A formatter touches every import row and re-wraps long signatures. Without
    these, such a change reports a hundred new dependencies and thirty reshaped
    public functions, none of which happened.
    """

    def test_negative_a_reformatted_import_is_not_a_new_dependency(self):
        out = extract(wrap("cli/commands/completion.ts", """
-import { Command } from 'commander';
+import type { Command } from 'commander';
"""))
        self.assertNotIn("dep.new-external", ids(out))

    def test_a_genuinely_new_import_still_fires(self):
        out = extract(wrap("cli/commands/completion.ts", "+import { z } from 'zod';\n"))
        self.assertIn("dep.new-external", ids(out))

    def test_negative_node_builtins_are_not_third_party(self):
        for stmt in ("+import { dirname } from 'node:path';\n",
                     "+import { existsSync } from 'fs';\n",
                     "+const os = require('node:os');\n"):
            out = extract(wrap("cli/a.ts", stmt))
            self.assertNotIn("dep.new-external", ids(out), stmt)

    def test_negative_a_rewrapped_signature_is_not_a_signature_change(self):
        out = extract(wrap("cli/formatter.ts", """
-export function printValidationOutput(feature: string, result: Result, options: Opts = {}): void {
+export function printValidationOutput(
+  feature: string,
+  result: Result,
+  options: Opts = {}
+): void {
"""))
        self.assertNotIn("api.signature-changed", ids(out))

    def test_negative_an_unwrapped_signature_is_not_a_signature_change(self):
        # The other direction: the formatter joined a wrapped declaration onto
        # one line, and dropped the trailing comma while doing it.
        out = extract(wrap("cli/lib/embedded-assets.ts", """
-export function lookupAsset(
-  assets: EmbeddedAssetMap,
-  pathname: string,
-): EmbeddedAsset | undefined {
+export function lookupAsset(assets: EmbeddedAssetMap, pathname: string): EmbeddedAsset | undefined {
"""))
        self.assertNotIn("api.signature-changed", ids(out))

    def test_negative_a_rewrapped_const_declaration_is_not_a_change(self):
        out = extract(wrap("cli/lib/structure-schema.ts", """
-export const TensionTypeSchema = z.enum([
-  'trade_off',
-  'resource_tension',
-  'hidden_dependency'
-]);
+export const TensionTypeSchema = z.enum(['trade_off', 'resource_tension', 'hidden_dependency']);
"""))
        self.assertNotIn("api.signature-changed", ids(out))

    def test_negative_a_rewrapped_builder_chain_is_not_a_change(self):
        out = extract(wrap("cli/lib/structure-schema.ts", """
-export const ConstraintIdSchema = z.string().regex(
-  /^([BTUSO]|OB|D|R|RK|DP)\\d+$/,
-  'Constraint ID must match a known prefix'
-);
+export const ConstraintIdSchema = z
+  .string()
+  .regex(
+    /^([BTUSO]|OB|D|R|RK|DP)\\d+$/,
+    'Constraint ID must match a known prefix'
+  );
"""))
        self.assertNotIn("api.signature-changed", ids(out))

    def test_negative_a_rewrapped_return_type_union_is_not_a_change(self):
        # `:` opens a TypeScript return type; treating it as a terminator (as it
        # is in Python) truncated the declaration and faked a signature change.
        out = extract(wrap("cli/lib/structure-schema.ts", """
-export function parseManifoldStructure(json: unknown): {
-  success: true;
-  data: ManifoldStructure;
-} | {
-  success: false;
-  error: z.ZodError;
-} {
+export function parseManifoldStructure(json: unknown):
+  | {
+      success: true;
+      data: ManifoldStructure;
+    }
+  | {
+      success: false;
+      error: z.ZodError;
+    } {
"""))
        self.assertNotIn("api.signature-changed", ids(out))

    def test_negative_generated_bundles_are_not_reviewed(self):
        out = extract(wrap("plugin/lib/parallel/parallel.bundle.js", """
+  } catch (_error) {}
+  subprocess.run([cmd]);
"""))
        self.assertNotIn("err.swallowed", ids(out))
        self.assertNotIn("bound.exec", ids(out))

    def test_negative_vendored_and_dist_paths_are_not_reviewed(self):
        for path in ("node_modules/x/index.js", "dist/app.js", "vendor/lib.go",
                     "api/service_pb2.py", "api/service.pb.go"):
            out = extract(wrap(path, "+  subprocess.run([cmd])\n"))
            self.assertNotIn("bound.exec", ids(out), path)

    def test_hand_written_source_beside_a_bundle_is_still_reviewed(self):
        out = extract(wrap("plugin/hooks/prompt-enforcer.ts", "+  } catch (_error) {}\n"))
        self.assertIn("err.swallowed", ids(out))

    def test_a_changed_enum_member_still_fires(self):
        out = extract(wrap("cli/lib/structure-schema.ts", """
-export const TensionTypeSchema = z.enum(['trade_off', 'resource_tension']);
+export const TensionTypeSchema = z.enum(['trade_off', 'resource_tension', 'blocker']);
"""))
        self.assertIn("api.signature-changed", ids(out))

    def test_a_real_parameter_change_still_fires(self):
        out = extract(wrap("cli/formatter.ts", """
-export function printValidationOutput(feature: string, result: Result): void {
+export function printValidationOutput(
+  feature: string,
+  result: Result,
+  options: Opts = {}
+): void {
"""))
        self.assertIn("api.signature-changed", ids(out))


class TestSeverityDamping(unittest.TestCase):
    def test_a_finding_in_an_example_asks_a_quieter_question(self):
        body = "+    let g = Graph::new().unwrap();\n"
        prod = [s for s in extract(wrap("src/graph.rs", body)) if s["id"] == "err.abort"]
        example = [s for s in extract(wrap("examples/perf.rs", body)) if s["id"] == "err.abort"]
        self.assertEqual(prod[0]["severity"], "medium")
        self.assertEqual(example[0]["severity"], "low")

    def test_high_severity_in_a_test_drops_to_medium(self):
        body = "+    subprocess.run([cmd, arg])\n"
        prod = [s for s in extract(wrap("svc/a.py", body)) if s["id"] == "bound.exec"]
        test = [s for s in extract(wrap("tests/test_a.py", body)) if s["id"] == "bound.exec"]
        self.assertEqual(prod[0]["severity"], "high")
        self.assertEqual(test[0]["severity"], "medium")

    def test_benches_and_fixtures_are_damped_too(self):
        body = "+    subprocess.run([cmd, arg])\n"
        for path in ("benches/bench_a.py", "testdata/gen.py", "example/demo.py"):
            sev = [s for s in extract(wrap(path, body)) if s["id"] == "bound.exec"][0]["severity"]
            self.assertEqual(sev, "medium", path)


class TestContractAndRadius(unittest.TestCase):
    def test_migration_and_ddl(self):
        out = extract(wrap("db/migrations/0004_add_col.sql", "+ALTER TABLE orders ADD COLUMN sku text;\n"))
        self.assertIn("data.migration", ids(out))
        self.assertIn("data.ddl", ids(out))

    def test_wire_contract(self):
        out = extract(wrap("api/orders.proto", "+  string sku = 4;\n"))
        self.assertIn("data.contract", ids(out))

    def test_public_change_without_tests(self):
        out = extract(wrap("pkg/a.go", "+func Serve(addr string) error {\n"))
        self.assertIn("radius.untested-surface", ids(out))

    def test_negative_a_touched_test_file_clears_it(self):
        text = wrap("pkg/a.go", "+func Serve(addr string) error {\n") + wrap(
            "pkg/a_test.go", "+func TestServe(t *testing.T) {\n"
        )
        self.assertNotIn("radius.untested-surface", ids(extract(text)))

    def test_spread_across_areas(self):
        text = "".join(
            wrap("%s/a.py" % d, "+x = 1\n") for d in ("web", "core", "cli", "infra")
        )
        self.assertIn("radius.spread", ids(extract(text)))


class TestFitDRY(unittest.TestCase):
    def test_a_pasted_block_is_flagged_once(self):
        block = """
+def handle_a(req):
+    ctx = build_context(req)
+    ctx.tenant = req.tenant
+    validate_tenant(ctx)
+    audit(ctx, "a")
+    return dispatch(ctx)
"""
        out = extract(wrap("svc/a.py", block + block))
        found = [s for s in out if s["id"] == "fit.duplicate-block"]
        self.assertEqual(len(found), 1, [s["evidence"] for s in found])
        self.assertIn("already appear at", found[0]["question"])

    def test_negative_short_repeats_are_not_duplication(self):
        out = extract(wrap("svc/a.py", """
+    a = 1
+    b = 2
+    a = 1
+    b = 2
"""))
        self.assertNotIn("fit.duplicate-block", ids(out))

    def test_negative_a_run_of_similar_lines_is_repetition_not_duplication(self):
        out = extract(wrap("svc/a.py", """
+    x = 0
+    x = 0
+    x = 0
+    x = 0
+    x = 0
+    x = 0
+    x = 0
+    x = 0
+    x = 0
+    x = 0
+    x = 0
+    x = 0
"""))
        self.assertNotIn("fit.duplicate-block", ids(out))

    def test_duplication_in_tests_asks_more_quietly(self):
        block = """
+def test_x():
+    ctx = build_context(req)
+    ctx.tenant = req.tenant
+    validate_tenant(ctx)
+    audit(ctx, "a")
+    assert dispatch(ctx)
"""
        out = extract(wrap("tests/test_a.py", block + block))
        found = [s for s in out if s["id"] == "fit.duplicate-block"]
        self.assertTrue(found)
        self.assertEqual(found[0]["severity"], "medium")


class TestFitShape(unittest.TestCase):
    def test_a_wide_signature_is_flagged(self):
        out = extract(wrap("svc/a.py",
                           "+def build(a, b, c, d, e, f, g):\n+    return a\n"))
        self.assertIn("fit.wide-signature", ids(out))

    def test_negative_a_normal_signature_is_not(self):
        out = extract(wrap("svc/a.py", "+def build(a, b, c):\n+    return a\n"))
        self.assertNotIn("fit.wide-signature", ids(out))

    def test_negative_a_no_argument_function_is_not_wide(self):
        out = extract(wrap("svc/a.py", "+def build():\n+    return 1\n"))
        self.assertNotIn("fit.wide-signature", ids(out))

    def test_parameter_budget_is_configurable(self):
        body = wrap("svc/a.py", "+def build(a, b, c, d, e, f, g):\n+    return a\n")
        relaxed = dict(sig.DEFAULT_BASELINE,
                       budgets=dict(sig.DEFAULT_BASELINE["budgets"], max_params=10))
        self.assertNotIn("fit.wide-signature", ids(extract(body, relaxed)))

    def test_a_long_function_is_flagged(self):
        body = "+def build():\n" + "".join("+    step_%d()\n" % i for i in range(70))
        out = extract(wrap("svc/a.py", body))
        self.assertIn("fit.long-function", ids(out))

    def test_negative_a_short_function_is_not(self):
        body = "+def build():\n" + "".join("+    step_%d()\n" % i for i in range(10))
        self.assertNotIn("fit.long-function", ids(extract(wrap("svc/a.py", body))))

    def test_a_pure_delegate_is_flagged(self):
        out = extract(wrap("svc/a.py",
                           "+def fetch_user(uid):\n+    return repo.get_user(uid)\n"))
        found = [s for s in out if s["id"] == "fit.pass-through"]
        self.assertTrue(found)
        self.assertEqual(found[0]["severity"], "low")

    def test_negative_a_function_that_does_something_is_not_a_delegate(self):
        out = extract(wrap("svc/a.py", """
+def fetch_user(uid):
+    user = repo.get_user(uid)
+    return normalise(user)
"""))
        self.assertNotIn("fit.pass-through", ids(out))


class TestFitSpeculation(unittest.TestCase):
    def test_an_uncalled_private_helper_is_flagged(self):
        out = extract(wrap("svc/a.py", "+def _normalise(x):\n+    return x.strip()\n"))
        self.assertIn("fit.unreferenced-addition", ids(out))

    def test_negative_a_called_private_helper_is_not(self):
        out = extract(wrap("svc/a.py", """
+def _normalise(x):
+    return x.strip()
+def handle(x):
+    return _normalise(x)
"""))
        self.assertNotIn("fit.unreferenced-addition", ids(out))

    def test_negative_public_surface_may_have_callers_elsewhere(self):
        out = extract(wrap("svc/a.py", "+def normalise(x):\n+    return x.strip()\n"))
        self.assertNotIn("fit.unreferenced-addition", ids(out))

    def test_negative_an_exported_rust_function_is_not_speculative(self):
        out = extract(wrap("src/a.rs", "+pub fn normalise(x: &str) -> String {\n+    x.trim().into()\n+}\n"))
        self.assertNotIn("fit.unreferenced-addition", ids(out))

    def test_an_interface_with_one_implementation_is_flagged(self):
        out = extract(wrap("src/store.rs", """
+trait Store {
+    fn get(&self, k: &str) -> Option<String>;
+}
+impl Store for MemStore {
+    fn get(&self, k: &str) -> Option<String> { None }
+}
"""))
        self.assertIn("fit.abstraction-for-one", ids(out))

    def test_negative_two_implementations_answer_the_question(self):
        out = extract(wrap("src/store.rs", """
+trait Store {
+    fn get(&self, k: &str) -> Option<String>;
+}
+impl Store for MemStore {
+    fn get(&self, k: &str) -> Option<String> { None }
+}
+impl Store for DiskStore {
+    fn get(&self, k: &str) -> Option<String> { None }
+}
"""))
        self.assertNotIn("fit.abstraction-for-one", ids(out))

    def test_negative_an_interface_with_no_implementation_here_is_not_flagged(self):
        out = extract(wrap("src/store.rs", "+trait Store {\n+    fn get(&self) -> u8;\n+}\n"))
        self.assertNotIn("fit.abstraction-for-one", ids(out))


class TestFitNoise(unittest.TestCase):
    def test_a_comment_restating_the_next_line_is_flagged(self):
        out = extract(wrap("svc/a.py",
                           "+    # Build the request context\n"
                           "+    request_context = build_context()\n"))
        self.assertIn("fit.restating-comment", ids(out))

    def test_negative_a_renamed_copy_is_out_of_reach(self):
        # Documented limit: matching is exact, so a paste-then-rename escapes.
        # Recorded as a test so the trade-off is visible rather than assumed.
        block = ("+def handle_%s(req):\n"
                 "+    ctx = build_context(req)\n"
                 "+    ctx.tenant = req.tenant\n"
                 "+    validate_tenant(ctx)\n"
                 "+    audit(ctx)\n"
                 "+    return dispatch(ctx)\n")
        out = extract(wrap("svc/a.py", block % "a" + block % "b"))
        self.assertNotIn("fit.duplicate-block", ids(out))

    def test_negative_a_comment_that_adds_a_reason_is_not(self):
        out = extract(wrap("svc/a.py",
                           "+    # bounded by the tenant quota, see INC-4412\n+    counter += 1\n"))
        self.assertNotIn("fit.restating-comment", ids(out))

    def test_negative_a_one_word_comment_is_ignored(self):
        out = extract(wrap("svc/a.py", "+    # counter\n+    counter += 1\n"))
        self.assertNotIn("fit.restating-comment", ids(out))


class TestFitScope(unittest.TestCase):
    def test_scattered_one_line_edits_are_flagged(self):
        text = "".join(wrap("area%d/mod.py" % i, "+x = %d\n" % i) for i in range(10))
        found = [s for s in extract(text) if s["id"] == "fit.drive-by-edits"]
        self.assertEqual(len(found), 1)
        self.assertIn("would review and revert better on their own", found[0]["question"])

    def test_negative_a_focused_change_is_not_flagged(self):
        text = "".join(
            wrap("area%d/mod.py" % i, "+a = 1\n+b = 2\n+c = 3\n+d = 4\n") for i in range(10)
        )
        self.assertNotIn("fit.drive-by-edits", ids(extract(text)))

    def test_negative_a_small_change_is_never_drive_by(self):
        text = "".join(wrap("area%d/mod.py" % i, "+x = 1\n") for i in range(4))
        self.assertNotIn("fit.drive-by-edits", ids(extract(text)))


class TestShape(unittest.TestCase):
    def test_every_signal_carries_a_question(self):
        out = extract(wrap("svc/a.py", """
+for order in orders:
+    for item in order.items:
+        rows.append(db.query(item.id))
"""))
        self.assertTrue(out)
        for s in out:
            self.assertTrue(s["question"].endswith("?"), s)
            self.assertIn(s["severity"], ("high", "medium", "low"))
            self.assertIn("id", s)

    def test_sorted_most_severe_first(self):
        out = extract(wrap("svc/a.py", """
+import requests
+for order in orders:
+    for item in order.items:
+        rows.append(db.query(item.id))
"""))
        order = {"high": 0, "medium": 1, "low": 2}
        seen = [order[s["severity"]] for s in out]
        self.assertEqual(seen, sorted(seen))

    def test_summarize_counts(self):
        out = extract(wrap("svc/a.py", "+import requests\n"))
        summary = sig.summarize(out)
        self.assertEqual(summary["total"], len(out))
        self.assertEqual(sum(summary["by_severity"].values()), len(out))

    def test_empty_diff_yields_no_signals(self):
        self.assertEqual(extract(""), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
