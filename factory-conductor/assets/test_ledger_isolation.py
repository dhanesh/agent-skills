"""The money ledger stays apart from the run (design L1, L6): conductor.py never imports
ledger.py and never reads .skill-contract/ledger/, and a grant still cannot name `spend`.
Stdlib only, offline."""
import ast
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import contract_check as CC  # noqa: E402

RUN_TOOLS = ("conductor.py", "reentry.py", "reentry_timer.py")


def _tree(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return ast.parse(f.read(), name)


def imported_modules(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".")[0])
    return out


def ledger_strings(tree):
    """String constants that name the ledger's tool or its state. `dispatch_ledger`, the
    run's own dispatch list, is a function name, not one of these."""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value.replace("\\", "/")
            if "ledger.py" in v or "ledger/" in v or "ledger.json" in v or v == "ledger":
                hits.append(v)
    return hits


class ConductorIsolation(unittest.TestCase):
    def test_run_tools_do_not_import_the_ledger(self):
        for name in RUN_TOOLS:
            mods = imported_modules(_tree(name))
            self.assertNotIn("ledger", mods, name)
            self.assertNotIn("ledger_testkit", mods, name)

    def test_run_tools_do_not_name_the_ledger_state(self):
        for name in RUN_TOOLS:
            self.assertEqual(ledger_strings(_tree(name)), [], name)
            with open(os.path.join(HERE, name), encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn(".skill-contract/ledger", text, name)
            self.assertNotIn('"ledger"', text, name)

    def test_the_probe_sees_a_planted_import(self):
        planted = ast.parse("import ledger\nfrom ledger import cmd_check\n"
                            "P = '.skill-contract/ledger/ledger.json'\n")
        self.assertIn("ledger", imported_modules(planted))
        self.assertEqual(len(ledger_strings(planted)), 1)

    def test_ledger_imports_no_run_tool(self):
        mods = imported_modules(_tree("ledger.py"))
        for name in ("conductor", "reentry", "reentry_timer", "contract_check"):
            self.assertNotIn(name, mods)


class SpendStaysUngrantable(unittest.TestCase):
    """A grant whose gate_policy names `spend` at grant or auto is refused by the vendored
    checker (A8: `spend` is in IRREVERSIBLE_CLASSES), and a valid grant answers ASK for it."""

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_grant_violations_refuse_spend(self):
        import conductor_testkit as TK
        self.assertIn("spend", CC.IRREVERSIBLE_CLASSES)
        root = TK.repo()
        plan = TK.write_plan_envelope(root, {"T1": ([], [{"text": "t", "command": ["true"]}])})
        for gate in ("grant", "auto"):
            path = TK.write_grant(root, plan, policy=dict(TK.DEFAULT_POLICY, spend=gate))
            st, err = CC.load_envelope(path)
            self.assertFalse(err)
            self.assertIn("gate_policy['spend'] must be ask: a grant never covers it (A8)",
                          CC.grant_violations(st), gate)
        path = TK.write_grant(root, plan, policy=dict(TK.DEFAULT_POLICY, spend="ask"))
        self.assertEqual(CC.grant_violations(CC.load_envelope(path)[0]), [])   # sanity

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_check_grant_never_covers_spend(self):
        import conductor_testkit as TK
        root = TK.repo()
        plan = TK.write_plan_envelope(root, {"T1": ([], [{"text": "t", "command": ["true"]}])})
        bad = TK.write_grant(root, plan, policy=dict(TK.DEFAULT_POLICY, spend="grant"))
        rep = CC.check_grant(root, "spend", branch="factory/x", path=bad)
        self.assertEqual(rep["status"], "INVALID", rep)
        self.assertTrue(any("spend" in v and "must be ask" in v for v in rep["violations"]))
        good = TK.write_grant(root, plan)
        rep = CC.check_grant(root, "spend", branch="factory/x", path=good)
        self.assertEqual(rep["status"], "ASK", rep)
        rep = CC.check_grant(root, "local_reversible", branch="factory/x", path=good)
        self.assertEqual(rep["status"], "COVERED", rep)   # sanity: the grant itself is live


if __name__ == "__main__":
    unittest.main()
