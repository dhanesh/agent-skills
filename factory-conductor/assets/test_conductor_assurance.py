#!/usr/bin/env python3
"""test_conductor_assurance.py — `conductor status` and the run result report the
weakest tier per feature (the proof-tier gate spec, R5).

When at least one task in the pinned plan carries `clauses`, `status` prints one
`ASSURANCE: <feature> <tier> <clause ids>` line per feature after its last `STATUS:`
line, and the run-result envelope carries the same items as `assurance`. A clause's tier
is the strongest tier recorded at the evidence pass of a merged task that names it, else
`none`; a feature's tier is its weakest clause's tier. A plan with no tier field prints no
ASSURANCE line and its envelope has no `assurance` key (O1).
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C  # noqa: E402
from conductor_testkit import write_grant, write_plan_envelope  # noqa: E402
from test_conductor_evidence import payload, task  # noqa: E402
from test_conductor_tiers import TierGate, tiered  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# The push asks, so finish writes the envelope and pushes nothing.
POLICY = {"read_only": "auto", "local_reversible": "grant", "push_branch": "ask",
          "open_pr": "ask"}


class Assurance(TierGate):
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
        """finish the run; returns the run-result payload."""
        rc, out, err = self.run_cmd("finish")
        self.assertIn(rc, (0, 3), out + err)
        self.assertNotIn("Traceback", err)
        lines = [l for l in out.splitlines() if l.startswith("FINISH: ")]
        self.assertEqual(len(lines), 1, out + err)
        with open(lines[0][len("FINISH: "):], encoding="utf-8") as f:
            return json.load(f)["predicate"]["payload"]

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
        return ["ASSURANCE: %s %s %s" % (x["feature"], x["tier"], ",".join(x["clauses"]))
                for x in items]


class WeakestTier(Assurance):
    def test_c1_proved_and_c2_property_report_property_c2(self):
        sha = self.tiered_run()
        self.proof("C1", sha, tier="proved", tool="verus")
        self.proof("C2", sha)
        self.pass_and_merge()
        self.assertEqual(self.assurance_lines(), ["ASSURANCE: notes-create property C2"])
        pay = self.finish()
        self.assertEqual(pay["assurance"],
                         [{"feature": "notes-create", "tier": "property", "clauses": ["C2"]}])
        self.assertEqual(self.as_lines(pay["assurance"]), self.assurance_lines())

    def test_c2_and_c10_print_in_numeric_order(self):
        sha = self.tiered_run(clauses=["notes-create:C10", "notes-create:C2"])
        self.proof("C10", sha)
        self.proof("C2", sha)
        self.pass_and_merge()
        self.assertEqual(self.assurance_lines(),
                         ["ASSURANCE: notes-create property C2,C10"])
        pay = self.finish()
        self.assertEqual(pay["assurance"][0]["clauses"], ["C2", "C10"])

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

    def test_a_clause_with_no_merged_record_reports_none(self):
        t2 = task("T2", ["notes-list"])
        t2.update(min_tier="property", clauses=["notes-list:C1"])
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


if __name__ == "__main__":
    unittest.main()
