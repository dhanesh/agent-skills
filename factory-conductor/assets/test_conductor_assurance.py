#!/usr/bin/env python3
"""test_conductor_assurance.py — `conductor status` and the run result report the
weakest tier per feature (the proof-tier gate spec, R5), at the integration head once
`finish` re-ran the proofs (proof-tier trust R8, contract conductor-evidence-gate:C5).

When at least one task in the pinned plan carries `clauses`, `status` prints one
`ASSURANCE: <feature> <tier> <clause ids>` line per feature after its last `STATUS:`
line, and the run-result envelope carries the same items as `assurance`. A clause's tier
is the strongest tier recorded at the evidence pass of a merged task that names it, else
`none`; a feature's tier is its weakest clause's tier. After `finish` re-ran the proofs,
a proved, bounded or property clause takes its tier from `integration.proofs`, each line
ends with ` @<integration head, 12 characters>`, and each envelope item carries the head
as `sha` and one `proofs` entry per named clause. `finish` itself prints no ASSURANCE
line. A plan with no tier field prints no ASSURANCE line and its envelope has no
`assurance` key (O1).

The proof tools are PATH shims named after their runners that run the committed tool.py,
so the recorded mutant (target.txt "good" to "caught") is caught on the head (D11, D12).
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C  # noqa: E402
from conductor_testkit import tmpdir, write_grant, write_plan_envelope  # noqa: E402
from test_conductor_evidence import git, payload, task  # noqa: E402
from test_conductor_finish_proofs import T1_FILES, write_shim  # noqa: E402
from test_conductor_tiers import TierGate, tiered  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# The push asks, so finish writes the envelope and pushes nothing.
POLICY = {"read_only": "auto", "local_reversible": "grant", "push_branch": "ask",
          "open_pr": "ask"}


class Assurance(TierGate):
    def setUp(self):
        super().setUp()
        # finish re-runs each proof record's argv on the integration head (proof-tier
        # trust R7): PATH shims named after the runners run the committed tool.py, which
        # passes on the head and fails with Hypothesis's report on the recorded mutant.
        self.bin = tmpdir()
        for name in ("pytest", "verus", "cargo"):
            write_shim(self.bin, name)
        patcher = mock.patch.dict(os.environ, {
            "PATH": self.bin + os.pathsep + os.environ.get("PATH", "")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, tid="T1", owner="writer", files=("t1.txt",)):
        """start, commit tool.py and target.txt plus `files` ("done"), verify, review."""
        rc, out, err = self.run_cmd("start", tid, "--owner", owner)
        self.assertEqual(rc, 0, out + err)
        wt = out.split(" ", 2)[2].strip()
        texts = dict(T1_FILES, **{name: "done\n" for name in files})
        for name, text in texts.items():
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
        return wt, sha

    def init(self, tasks, verification=True, budget=None):
        plan = write_plan_envelope(self.root, plan=payload(tasks, verification))
        write_grant(self.root, plan, policy=POLICY, budget=budget)
        rc, out, err = self.run_cmd("init", "--plan", plan)
        self.assertEqual(rc, 0, out + err)
        self.plan = plan
        return plan

    def pass_and_merge(self, tid="T1"):
        rc, out, err = self.evidence(tid)
        self.assertEqual(rc, 0, out + err)
        rc, out, err = self.run_cmd("merge", tid)
        self.assertEqual(rc, 0, out + err)

    def finish(self):
        """finish the run; returns the run-result payload. finish prints no ASSURANCE:
        line: only `status` reports assurance."""
        rc, out, err = self.run_cmd("finish")
        self.assertIn(rc, (0, 3), out + err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn("ASSURANCE:", out + err)
        lines = [l for l in out.splitlines() if l.startswith("FINISH: ")]
        self.assertEqual(len(lines), 1, out + err)
        with open(lines[0][len("FINISH: "):], encoding="utf-8") as f:
            pay = json.load(f)["predicate"]["payload"]
        self.assertTrue(pay["integration"]["passed"], out)
        return pay

    def assurance_lines(self):
        """The ASSURANCE: lines of `status`, checked to come after its last STATUS: line."""
        rc, out, err = self.run_cmd("status")
        self.assertEqual(rc, 0, err)
        lines = out.splitlines()
        found = [i for i, l in enumerate(lines) if l.startswith("ASSURANCE: ")]
        last_status = max(i for i, l in enumerate(lines) if l.startswith("STATUS: "))
        self.assertTrue(all(i > last_status for i in found), out)
        return [lines[i] for i in found]

    @staticmethod
    def as_lines(items):
        return ["ASSURANCE: %s %s %s%s" % (x["feature"], x["tier"], ",".join(x["clauses"]),
                                           " @" + x["sha"][:12] if "sha" in x else "")
                for x in items]

    @staticmethod
    def entry(clause, tier, tool=None, argv=None, rechecked=None):
        return {"clause": clause, "tier": tier, "tool": tool, "argv": argv,
                "mutant_rechecked": rechecked}


class WeakestTier(Assurance):
    def test_c1_proved_and_c2_property_report_property_c2(self):
        sha = self.tiered_run()
        self.proof("C1", sha, tier="proved", tool="verus")
        self.proof("C2", sha)
        self.pass_and_merge()
        # Before finish: the unchanged line, no sha, and each proof's source record.
        self.assertEqual(self.assurance_lines(), ["ASSURANCE: notes-create property C2"])
        self.assertEqual(C.assurance(self.state()), [
            {"feature": "notes-create", "tier": "property", "clauses": ["C2"],
             "proofs": [self.entry("C1", "proved", "verus", ["verus"]),
                        self.entry("C2", "property", "hypothesis", ["pytest"], False)]}])
        pay = self.finish()
        head = pay["integration"]["head"]
        self.assertRegex(head, r"^[0-9a-f]{40}$")
        self.assertEqual([p["outcome"] for p in pay["integration"]["proofs"]],
                         ["pass", "pass"])
        self.assertEqual(pay["assurance"], [
            {"feature": "notes-create", "tier": "property", "clauses": ["C2"], "sha": head,
             "proofs": [self.entry("C1", "proved", "verus", ["verus"]),
                        self.entry("C2", "property", "hypothesis", ["pytest"], True)]}])
        self.assertEqual(self.assurance_lines(),
                         ["ASSURANCE: notes-create property C2 @%s" % head[:12]])
        self.assertEqual(self.as_lines(pay["assurance"]), self.assurance_lines())

    def test_a_mutant_finish_does_not_catch_reports_runtime_at_the_head(self):
        sha = self.tiered_run()
        self.proof("C1", sha, tier="proved", tool="verus")
        # kani's failure signature is not in the shim's output: the mutant is uncaught.
        self.proof("C2", sha, tier="bounded", tool="kani")
        self.pass_and_merge()
        self.assertEqual(self.assurance_lines(), ["ASSURANCE: notes-create bounded C2"])
        pay = self.finish()
        head = pay["integration"]["head"]
        self.assertEqual([p["outcome"] for p in pay["integration"]["proofs"]],
                         ["pass", "uncaught"])
        self.assertEqual(pay["assurance"], [
            {"feature": "notes-create", "tier": "runtime", "clauses": ["C2"], "sha": head,
             "proofs": [self.entry("C1", "proved", "verus", ["verus"]),
                        self.entry("C2", "runtime")]}])
        self.assertEqual(self.assurance_lines(),
                         ["ASSURANCE: notes-create runtime C2 @%s" % head[:12]])

    def test_c2_and_c10_print_in_numeric_order(self):
        sha = self.tiered_run(clauses=["notes-create:C10", "notes-create:C2"])
        self.proof("C10", sha)
        self.proof("C2", sha)
        self.pass_and_merge()
        self.assertEqual(self.assurance_lines(),
                         ["ASSURANCE: notes-create property C2,C10"])
        pay = self.finish()
        self.assertEqual(pay["assurance"][0]["clauses"], ["C2", "C10"])
        self.assertEqual(pay["assurance"][0]["proofs"],
                         [self.entry("C2", "property", "hypothesis", ["pytest"], True),
                          self.entry("C10", "property", "hypothesis", ["pytest"], True)])
        self.assertEqual(self.assurance_lines(), ["ASSURANCE: notes-create property C2,C10 @%s"
                                                  % pay["integration"]["head"][:12]])

    def test_the_strongest_record_at_pass_is_the_clauses_tier(self):
        sha = self.tiered_run()
        self.proof("C1", sha)
        self.proof("C1", sha, tier="proved", tool="verus")
        self.proof("C2", sha, tier="bounded", tool="kani")
        self.pass_and_merge()
        ev = self.state().tasks["T1"]["evidence"]
        self.assertEqual(ev["clause_tiers"],
                         {"notes-create:C1": "proved", "notes-create:C2": "bounded"})
        self.assertEqual(self.assurance_lines(), ["ASSURANCE: notes-create bounded C2"])

    def test_runtime_min_tier_records_runtime_without_a_proof_record(self):
        self.tiered_run(min_tier="runtime")
        self.pass_and_merge()
        self.assertEqual(self.assurance_lines(),
                         ["ASSURANCE: notes-create runtime C1,C2"])
        pay = self.finish()
        head = pay["integration"]["head"]
        # No proof tier, so nothing re-ran; the proofs step still ran on this head.
        self.assertEqual(pay["integration"]["proofs"], [])
        self.assertEqual(pay["assurance"], [
            {"feature": "notes-create", "tier": "runtime", "clauses": ["C1", "C2"],
             "sha": head, "proofs": [self.entry("C1", "runtime"),
                                     self.entry("C2", "runtime")]}])
        self.assertEqual(self.assurance_lines(),
                         ["ASSURANCE: notes-create runtime C1,C2 @%s" % head[:12]])

    def test_a_clause_with_no_merged_record_reports_none(self):
        t2 = task("T2", ["notes-list"])
        t2.update(min_tier="property", clauses=["notes-list:C1"])
        self.contract("notes-list", ["C1"])
        self.init([tiered(), t2])
        _, sha = self.build()
        self.record("notes-create", sha)
        self.proof("C1", sha)
        self.proof("C2", sha)
        # Before the merge, a pass alone does not count.
        self.assertEqual(self.evidence()[0], 0)
        self.assertEqual(self.assurance_lines(), ["ASSURANCE: notes-create none C1,C2",
                                                  "ASSURANCE: notes-list none C1"])
        self.assertEqual(self.run_cmd("merge", "T1")[0], 0)
        self.assertEqual(self.run_cmd("park", "T2", "--reason", "a human")[0], 0)
        self.assertEqual(self.assurance_lines(), ["ASSURANCE: notes-create property C1,C2",
                                                  "ASSURANCE: notes-list none C1"])
        pay = self.finish()
        self.assertEqual(self.as_lines(pay["assurance"]), self.assurance_lines())
        head = pay["integration"]["head"][:12]
        self.assertEqual(self.assurance_lines(),
                         ["ASSURANCE: notes-create property C1,C2 @" + head,
                          "ASSURANCE: notes-list none C1 @" + head])
        self.assertEqual(pay["assurance"][1]["proofs"], [self.entry("C1", "none")])

    def test_a_clause_two_tasks_name_takes_the_strongest_merged_tier(self):
        t2 = task("T2", ["notes-create"], deps=["T1"])
        t2.update(min_tier="property", clauses=["notes-create:C1"])
        self.init([tiered(clauses=["notes-create:C1"]), t2])
        _, sha = self.build()
        self.record("notes-create", sha)
        self.proof("C1", sha)
        self.pass_and_merge()
        _, sha2 = self.build("T2", files=("t1.txt", "t2.txt"))
        self.record("notes-create", sha2)
        self.proof("C1", sha2, tier="proved", tool="verus")
        self.pass_and_merge("T2")
        self.assertEqual(self.assurance_lines(), ["ASSURANCE: notes-create proved C1"])


class Untiered(Assurance):
    def test_a_plan_with_no_tier_field_reports_nothing(self):
        self.init([task("T1", ["notes-create"])])
        _, sha = self.build()
        self.record("notes-create", sha)
        self.pass_and_merge()
        self.assertNotIn("clause_tiers", self.state().tasks["T1"]["evidence"])
        self.assertEqual(self.assurance_lines(), [])
        pay = self.finish()
        self.assertNotIn("assurance", pay)
        self.assertEqual(self.assurance_lines(), [])

    def test_an_ungated_plan_reports_nothing(self):
        plan = write_plan_envelope(self.root, plan=payload(
            [dict(task("T1", []), features=None)], verification=False))
        write_grant(self.root, plan, policy=POLICY)
        self.assertEqual(self.run_cmd("init", "--plan", plan)[0], 0)
        self.assertEqual(self.assurance_lines(), [])
        self.assertIsNone(C.assurance(self.state()))


class Schema(unittest.TestCase):
    def test_the_schema_declares_assurance_as_an_optional_property(self):
        with open(os.path.join(HERE, "schemas", "run-result.v1.json"),
                  encoding="utf-8") as f:
            schema = json.load(f)
        self.assertIn("assurance", schema["properties"])
        self.assertNotIn("assurance", schema["required"])
        item = schema["properties"]["assurance"]["items"]
        self.assertEqual(item["required"], ["feature", "tier", "clauses"])
        self.assertEqual(item["properties"]["tier"]["enum"], list(C.TIERS) + ["none"])
        # R8 adds sha and proofs additively: `required` is unchanged.
        self.assertEqual(item["properties"]["sha"]["pattern"], "^[0-9a-f]{40,64}$")
        proof = item["properties"]["proofs"]["items"]
        self.assertEqual(proof["required"],
                         ["clause", "tier", "tool", "argv", "mutant_rechecked"])
        self.assertEqual(proof["properties"]["tier"]["enum"], list(C.TIERS) + ["none"])
        self.assertEqual(proof["properties"]["mutant_rechecked"]["type"], ["boolean", "null"])


if __name__ == "__main__":
    unittest.main()
