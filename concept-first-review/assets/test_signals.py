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
