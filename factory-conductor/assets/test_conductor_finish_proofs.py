#!/usr/bin/env python3
"""test_conductor_finish_proofs.py — `finish` re-runs each clause's source proof on the
integration head (proof-tier trust R7, contract conductor-evidence-gate:C4).

In a tiered plan, once every verify run of the integration passes, `finish` re-runs the
proof record of each proved, bounded or property clause (the record that the proven task
with the strongest tier stored at its evidence pass) in a clean checkout of the
integration head, and re-applies the record's mutant patch in a second checkout. Each
clause gets one outcome in `integration.proofs`: fail (the head run failed twice), flaky
(it failed, then passed), unavailable (the tool cannot start), stale-mutant (the patch
does not apply), uncaught (the mutant was not caught twice) or pass. Each outcome but
pass prints `INTEGRATION: <outcome> <task> proof <f>:C<n>` before `INTEGRATION: pass`;
only fail turns the run red (`STOP: integration_red`), and nothing is pushed then.

The proof tool is a PATH shim named after its runner (`pytest`) that runs the committed
tool.py from its cwd, so no proof dependency is needed (D11, D12).
"""
import hashlib
import json
import os
import shutil
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C  # noqa: E402
from conductor_testkit import tmpdir, write_grant, write_plan_envelope  # noqa: E402
from test_conductor_evidence import git, payload, task  # noqa: E402
from test_conductor_tiers import TierGate, tiered  # noqa: E402

# A fake hypothesis run. target.txt "good" (or any other text) passes; "caught" (the
# recorded mutant) fails with Hypothesis's failure report; "broken" fails.
TOOL = '''import sys
mode = open("target.txt").read().strip()
if mode == "caught":
    print("Falsifying example: test_create(title='')\\n1 failed in 0.01s")
    sys.exit(1)
if mode == "broken":
    print("1 failed in 0.01s")
    sys.exit(1)
print("1 passed in 0.01s")
'''
# The runner shim: runs tool.py from its cwd, as the recorder's PATH shim does.
SHIM = "#!%s\nimport runpy\nrunpy.run_path('tool.py', run_name='__main__')\n"
# The same shim, but its first call fails (a chance failure): it leaves a marker file.
FLAKY_SHIM = ("#!%s\nimport os, runpy, sys\nm = %r\nif not os.path.exists(m):\n"
              "    open(m, 'w').close()\n    print('1 failed')\n    sys.exit(1)\n"
              "runpy.run_path('tool.py', run_name='__main__')\n")
# A mutant that applies but that tool.py does not check: it is never caught.
BLIND_MUTANT = """diff --git a/t1.txt b/t1.txt
--- a/t1.txt
+++ b/t1.txt
@@ -1 +1 @@
-done
+undone
"""
T1_FILES = {"t1.txt": "done\n", "tool.py": TOOL, "target.txt": "good\n"}


def write_shim(bin_dir, name="pytest", text=None):
    """Write an executable PATH shim `name` into bin_dir (SHIM by default)."""
    path = os.path.join(bin_dir, name)
    with open(path, "w") as f:
        f.write(text if text is not None else SHIM % sys.executable)
    os.chmod(path, 0o755)
    return path


class FinishProofs(TierGate):
    def setUp(self):
        super().setUp()
        self.bin = tmpdir()
        write_shim(self.bin)
        patcher = mock.patch.dict(os.environ, {
            "PATH": self.bin + os.pathsep + os.environ.get("PATH", "")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def init_two(self, t1=None, policy=None):
        """A tiered T1 (property on notes-create:C1) and an untiered T2 after it."""
        t1 = t1 or tiered(clauses=["notes-create:C1"])
        t2 = task("T2", ["notes-list"], deps=["T1"])
        plan = write_plan_envelope(self.root, plan=payload([t1, t2]))
        write_grant(self.root, plan, policy=policy)
        rc, out, err = self.run_cmd("init", "--plan", plan)
        self.assertEqual(rc, 0, out + err)

    def commit(self, tid, files):
        """start tid, commit `files` ({path: text}), verify, review: returns the sha."""
        rc, out, err = self.run_cmd("start", tid, "--owner", "writer")
        self.assertEqual(rc, 0, out + err)
        wt = out.split(" ", 2)[2].strip()
        for name, text in files.items():
            with open(os.path.join(wt, name), "w") as f:
                f.write(text)
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "work " + tid)
        rc, out, err = self.run_cmd("verify", tid)
        self.assertEqual(rc, 0, out + err)
        sha = out.split()[-1]
        rc, out, err = self.run_cmd("review", tid, "--verdict", "pass", "--reviewer",
                                    "reviewer")
        self.assertEqual(rc, 0, out + err)
        return sha

    def prove_and_merge(self, tid):
        rc, out, err = self.evidence(tid)
        self.assertEqual(rc, 0, out + err)
        rc, out, err = self.run_cmd("merge", tid)
        self.assertEqual(rc, 0, out + err)

    def t1(self, clauses=("C1",), mutant=None, **proof):
        """T1 commits the tool and its target, with runtime evidence and a property
        proof per clause (its mutant patch replaced by `mutant` when given), and merges."""
        sha = self.commit("T1", T1_FILES)
        self.record("notes-create", sha)
        for c in clauses:
            d = self.proof(c, sha, **proof)
            if mutant is not None:
                self.replace_mutant(d, c, mutant, proof.get("tier", "property"),
                                    proof.get("tool", "hypothesis"))
        self.prove_and_merge("T1")
        return sha

    @staticmethod
    def replace_mutant(d, clause, text, tier, tool):
        stem = "proof-%s-%s-%s" % (tier, tool, clause)
        with open(os.path.join(d, stem + ".mutant.patch"), "w") as f:
            f.write(text)
        path = os.path.join(d, stem + ".json")
        with open(path) as f:
            doc = json.load(f)
        for a in doc["artifacts"]:
            if a["path"] == stem + ".mutant.patch":
                a["sha256"] = hashlib.sha256(text.encode()).hexdigest()
        with open(path, "w") as f:
            json.dump(doc, f)

    def t2(self, target=None):
        """T2 edits only target.txt (when given) and merges, with runtime evidence."""
        files = {"target.txt": target} if target is not None else {"t2.txt": "x\n"}
        sha = self.commit("T2", files)
        self.record("notes-list", sha)
        self.prove_and_merge("T2")

    def finish(self):
        """(exit code, finish's output lines, the run-result payload)."""
        rc, out, err = self.run_cmd("finish")
        self.assertNotIn("Traceback", err)
        lines = out.splitlines()
        done = [x for x in lines if x.startswith("FINISH: ")]
        self.assertEqual(len(done), 1, out + err)
        with open(done[0][len("FINISH: "):], encoding="utf-8") as f:
            return rc, lines, json.load(f)["predicate"]["payload"]

    @staticmethod
    def integration_lines(lines):
        return [x for x in lines if x.startswith("INTEGRATION: ")]

    def assert_passes_at_runtime(self, outcome, lines, pay, task_id="T1",
                                 clause="notes-create:C1"):
        got = self.integration_lines(lines)
        self.assertEqual(got[:-1], ["INTEGRATION: %s %s proof %s" % (outcome, task_id,
                                                                      clause)])
        self.assertEqual(got[-1], "INTEGRATION: pass %s" % pay["integration"]["head"])
        self.assertTrue(pay["integration"]["passed"])
        self.assertIsNone(pay["stopped"])
        (p,) = [x for x in pay["integration"]["proofs"] if x["clause"] == clause]
        self.assertEqual((p["outcome"], p["tier"]), (outcome, "runtime"))
        return p

    def events(self, name):
        return [e for e in self.state().events() if e.get("event") == name]


class Fail(FinishProofs):
    def test_a_later_task_that_breaks_the_proof_turns_the_run_red(self):
        self.init_two()
        self.t1()
        self.t2(target="broken\n")
        rc, lines, pay = self.finish()
        self.assertEqual(rc, 3)
        self.assertEqual(self.integration_lines(lines),
                         ["INTEGRATION: fail T1 proof notes-create:C1"])
        self.assertIn("STOP: integration_red", lines)
        self.assertFalse(pay["integration"]["passed"])
        self.assertTrue(all(r["ok"] for r in pay["integration"]["runs"]))
        self.assertEqual(pay["integration"]["proofs"], [
            {"task": "T1", "clause": "notes-create:C1", "tier": "none",
             "tool": "hypothesis", "argv": ["pytest"], "outcome": "fail",
             "mutant_rechecked": False}])
        self.assertEqual(pay["stopped"]["reason"], "integration_red")
        self.assertEqual(pay["stopped"]["detail"], "T1 proof notes-create:C1")
        # The envelope is written, and nothing is pushed: no push was even gated.
        st = self.state()
        self.assertFalse(st.finished["pushed"])
        self.assertEqual(self.events("push"), [])
        self.assertFalse(any(e.get("action") == "push_branch" for e in self.events("gate")))
        # finish writes no proof record.
        self.assertFalse([p for p in self.all_records() if "T2" in p])

    def all_records(self):
        out = []
        for d, _, names in os.walk(self.evdir):
            out += [os.path.join(d, n) for n in names]
        return out

    def test_the_re_run_writes_no_record(self):
        self.init_two()
        self.t1()
        before = sorted(self.all_records())
        self.t2(target="broken\n")
        new_t2 = set(self.all_records()) - set(before)
        self.finish()
        self.assertEqual(sorted(set(self.all_records()) - set(before)), sorted(new_t2))


class RuntimeOutcomes(FinishProofs):
    def test_an_unbroken_run_passes_with_its_proof_rechecked(self):
        self.init_two()
        self.t1()
        self.t2()
        rc, lines, pay = self.finish()
        head = pay["integration"]["head"]
        self.assertEqual(self.integration_lines(lines), ["INTEGRATION: pass %s" % head])
        self.assertTrue(pay["integration"]["passed"])
        self.assertEqual(pay["integration"]["proofs"], [
            {"task": "T1", "clause": "notes-create:C1", "tier": "property",
             "tool": "hypothesis", "argv": ["pytest"], "outcome": "pass",
             "mutant_rechecked": True}])
        self.assertNotIn("STOP: integration_red", lines)

    def test_a_shim_that_fails_only_on_its_first_call_is_flaky(self):
        self.init_two()
        self.t1()
        self.t2()
        write_shim(self.bin, text=FLAKY_SHIM % (sys.executable,
                                                os.path.join(tmpdir(), "called")))
        rc, lines, pay = self.finish()
        p = self.assert_passes_at_runtime("flaky", lines, pay)
        self.assertFalse(p["mutant_rechecked"])

    def test_a_mutant_patch_that_no_longer_applies_is_stale(self):
        self.init_two()
        self.t1()
        self.t2(target="fine\n")  # still passes, but the mutant's "-good" line is gone
        rc, lines, pay = self.finish()
        p = self.assert_passes_at_runtime("stale-mutant", lines, pay)
        self.assertFalse(p["mutant_rechecked"])

    def test_a_mutant_not_caught_twice_is_uncaught(self):
        self.init_two()
        self.t1(mutant=BLIND_MUTANT)
        self.t2()
        rc, lines, pay = self.finish()
        p = self.assert_passes_at_runtime("uncaught", lines, pay)
        self.assertFalse(p["mutant_rechecked"])

    def test_a_tool_that_cannot_start_is_unavailable(self):
        self.init_two(t1=tiered(min_tier="proved", clauses=["notes-create:C1"]))
        if shutil.which("verus"):
            self.skipTest("verus is on PATH")
        self.t1(tier="proved", tool="verus")
        self.t2()
        rc, lines, pay = self.finish()
        p = self.assert_passes_at_runtime("unavailable", lines, pay)
        self.assertEqual((p["tool"], p["argv"], p["mutant_rechecked"]),
                         ("verus", ["verus"], None))

    def test_a_removed_shim_is_unavailable(self):
        self.init_two()
        self.t1()
        self.t2()
        # A PATH that holds git and true (the verify command) and no pytest.
        os.remove(os.path.join(self.bin, "pytest"))
        for name in ("git", "true"):
            os.symlink(shutil.which(name), os.path.join(self.bin, name))
        with mock.patch.dict(os.environ, {"PATH": self.bin}):
            rc, lines, pay = self.finish()
        self.assert_passes_at_runtime("unavailable", lines, pay)

    def test_lines_print_in_clause_number_order_before_pass(self):
        self.init_two(t1=tiered(clauses=["notes-create:C10", "notes-create:C2"]))
        self.t1(clauses=("C10", "C2"))
        self.t2(target="fine\n")
        rc, lines, pay = self.finish()
        self.assertEqual(self.integration_lines(lines), [
            "INTEGRATION: stale-mutant T1 proof notes-create:C2",
            "INTEGRATION: stale-mutant T1 proof notes-create:C10",
            "INTEGRATION: pass %s" % pay["integration"]["head"]])
        self.assertEqual([p["clause"] for p in pay["integration"]["proofs"]],
                         ["notes-create:C2", "notes-create:C10"])


class Untiered(FinishProofs):
    def test_an_untiered_runs_finish_output_is_unchanged(self):
        plan = write_plan_envelope(self.root, plan=payload([task("T1", ["notes-create"])]))
        write_grant(self.root, plan, policy={"read_only": "auto",
                                             "local_reversible": "grant",
                                             "push_branch": "ask", "open_pr": "ask"})
        self.assertEqual(self.run_cmd("init", "--plan", plan)[0], 0)
        sha = self.commit("T1", T1_FILES)
        self.record("notes-create", sha)
        self.prove_and_merge("T1")
        self.assertNotIn("clause_records", self.state().tasks["T1"]["evidence"])
        rc, lines, pay = self.finish()
        head = pay["integration"]["head"]
        self.assertEqual(lines[:2], ["INTEGRATION: pass %s" % head,
                                     "FINISH: %s" % self.state().finished["envelope"]])
        self.assertNotIn("proofs", pay["integration"])
        self.assertEqual(sorted(pay["integration"]), ["head", "passed", "runs"])


class Sources(unittest.TestCase):
    """_proof_sources picks the proven task with the strongest tier, the first in run
    order on a tie, and lists the proof tiers in run order then clause number."""

    @staticmethod
    def st(tasks):
        return types.SimpleNamespace(order=[t for t, _ in tasks], tasks={
            t: {"status": status, "evidence": {"clause_tiers": tiers, "clause_records": {
                c: "%s-%s" % (t, c) for c in tiers}}}
            for t, (status, tiers) in tasks})

    def test_the_strongest_tier_then_the_first_in_run_order(self):
        st = self.st([
            ("T1", ("proven", {"a:C1": "property", "a:C2": "bounded", "a:C3": "runtime"})),
            ("T2", ("proven", {"a:C1": "proved", "a:C2": "bounded"})),
            ("T3", ("parked", {"a:C2": "proved"}))])
        self.assertEqual(C._proof_sources(st), [
            ("T1", "a:C2", "bounded", "T1-a:C2"),
            ("T2", "a:C1", "proved", "T2-a:C1")])

    def test_order_is_run_order_then_clause_number(self):
        st = self.st([("T1", ("proven", {"b:C10": "property", "b:C2": "property"})),
                      ("T2", ("proven", {"a:C1": "property"}))])
        self.assertEqual([x[1] for x in C._proof_sources(st)], ["b:C2", "b:C10", "a:C1"])


class Schema(unittest.TestCase):
    def test_the_schema_declares_integration_proofs_additively(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas",
                               "run-result.v1.json"), encoding="utf-8") as f:
            integ = json.load(f)["properties"]["integration"]
        self.assertEqual(integ["required"], ["head", "passed", "runs"])
        item = integ["properties"]["proofs"]["items"]
        self.assertEqual(item["required"], ["task", "clause", "tier", "tool", "argv",
                                            "outcome", "mutant_rechecked"])
        self.assertEqual(item["properties"]["outcome"]["enum"], list(C.PROOF_OUTCOMES))


if __name__ == "__main__":
    unittest.main()
