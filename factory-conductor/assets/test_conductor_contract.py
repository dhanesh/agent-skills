#!/usr/bin/env python3
"""test_conductor_contract.py — the evidence gate parks a task whose head changes a
contract (the proof-tier gate spec, R4).

In a tiered plan (a gated plan where at least one task carries `min_tier`), `conductor
evidence` compares the `## Contract` section of every verify-skill feature file other
than README.md between the run base (`git merge-base <run branch> <verified head>`) and
the verified head, for every task, before the evidence verdict. On a difference it
prints `EVIDENCE: <task> reject contract-changed <first changed feature id>`, exits 3
and parks the task. A plan with no `min_tier` is not checked.
"""
import importlib.util
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C  # noqa: E402
from test_conductor_evidence import FEATURE, SKILL, git, task  # noqa: E402
from test_conductor_tiers import TWO, TierGate, tiered  # noqa: E402

FORGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                     "verification-skill-forge", "assets", "forge.py")
CONTRACT = """
## Contract

- C1 [pre]: the title is a non-empty string
- C2 [post]: the response is 201 with the stored note's id
"""
EDITED = CONTRACT.replace("a non-empty string", "a string")


def feature_text(fid, anchors, contract=CONTRACT, extra=""):
    """A feature file in the fixture's shape, with `contract` before `## Proof`."""
    head, tail = FEATURE.format(fid=fid, anchors=anchors).split("\n## Proof\n", 1)
    return head + (contract or "") + "\n## Proof\n\nC1 and C2" + extra + tail


def feature_path(fid):
    return os.path.join(*SKILL.split("/"), "features", fid + ".md")


class ContractGate(TierGate):
    def setUp(self):
        super().setUp()
        with open(os.path.join(self.root, feature_path("notes-create")), "w") as f:
            f.write(feature_text("notes-create", "t1.txt"))
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "contract")

    def build_with(self, edits, tid="T1", files=("t1.txt",)):
        """start the task, commit `files` plus `edits` ({repo path: text or None to
        delete}), verify and review: the task waits for its evidence verdict."""
        rc, out, err = self.run_cmd("start", tid, "--owner", "writer")
        self.assertEqual(rc, 0, out + err)
        wt = out.split(" ", 2)[2].strip()
        for name in files:
            path = os.path.join(wt, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write("done\n")
        for rel, text in edits.items():
            path = os.path.join(wt, rel)
            if text is None:
                os.remove(path)
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "work")
        rc, out, err = self.run_cmd("verify", tid)
        self.assertEqual(rc, 0, out + err)
        sha = out.split()[-1]
        rc, out, err = self.run_cmd("review", tid, "--verdict", "pass", "--reviewer",
                                    "reviewer")
        self.assertEqual(rc, 0, out + err)
        return sha

    def edit_c1(self):
        return {feature_path("notes-create"): feature_text("notes-create", "t1.txt",
                                                           EDITED)}

    def record_all(self, sha, feature="notes-create", proofs=True):
        """Every record the task needs: runtime evidence, a green doctor, and a property
        proof for C1 and C2."""
        self.record(feature, sha)
        if proofs:
            for cid in ("C1", "C2"):
                self.proof(cid, sha)

    def assert_parked(self, tid, feature, rc, out, err):
        self.assertEqual(rc, 3, out + err)
        self.assertEqual(out.splitlines()[0],
                         "EVIDENCE: %s reject contract-changed %s" % (tid, feature))
        self.assertIn("PARK: %s contract-changed" % tid, out)
        self.assertNotIn("Traceback", err)
        t = self.state().tasks[tid]
        self.assertEqual((t["status"], t["park_reason"]), ("parked", "contract-changed"))
        self.assertEqual((t["evidence"]["reason"], t["evidence"]["feature"]),
                         ("contract-changed", feature))


class TieredPlan(ContractGate):
    def test_a_tiered_task_that_edits_c1_parks_although_every_record_passes(self):
        self.init([tiered()])
        sha = self.build_with(self.edit_c1())
        self.record_all(sha)
        rc, out, err = self.evidence()
        self.assert_parked("T1", "notes-create", rc, out, err)
        self.assertNotEqual(self.run_cmd("merge", "T1")[0], 0)
        self.assertFalse(self.proven())
        with open(os.path.join(os.path.dirname(C.state_path(self.root)),
                               "autonomy-log.jsonl")) as f:
            self.assertIn('"contract-changed"', f.read())

    def test_an_ungated_task_in_a_tiered_plan_that_makes_the_same_edit_parks(self):
        # T1 carries no tier field; T2 makes the plan tiered.
        self.init([task("T1", ["notes-list"]),
                   dict(task("T2", ["notes-create"]), min_tier="property", clauses=TWO)])
        sha = self.build_with(self.edit_c1(), files=("src/list.py",))
        self.record_all(sha, feature="notes-list", proofs=False)
        self.assert_parked("T1", "notes-create", *self.evidence())

    def test_the_check_runs_before_the_evidence_verdict(self):
        # No record at all: the contract reject, not evidence-missing.
        self.init([tiered()])
        self.build_with(self.edit_c1())
        rc, out, err = self.evidence()
        self.assert_parked("T1", "notes-create", rc, out, err)

    def test_a_contract_section_added_to_a_feature_is_a_change(self):
        self.init([tiered()])
        sha = self.build_with({feature_path("notes-list"):
                               feature_text("notes-list", "src/list.py")})
        self.record_all(sha)
        self.assert_parked("T1", "notes-list", *self.evidence())

    def test_a_contract_section_removed_is_a_change(self):
        self.init([tiered()])
        sha = self.build_with({feature_path("notes-create"):
                               feature_text("notes-create", "t1.txt", contract="")})
        self.record_all(sha)
        self.assert_parked("T1", "notes-create", *self.evidence())

    def test_a_new_feature_file_with_a_contract_is_a_change(self):
        self.init([tiered()])
        sha = self.build_with({feature_path("notes-zap"): feature_text("notes-zap", "z")})
        self.record_all(sha)
        self.assert_parked("T1", "notes-zap", *self.evidence())

    def test_a_deleted_feature_file_with_a_contract_is_a_change(self):
        self.init([task("T1", ["notes-list"]),
                   dict(task("T2", ["notes-list"]), min_tier="runtime",
                        clauses=["notes-list:C1"])])
        sha = self.build_with({feature_path("notes-create"): None},
                              files=("src/list.py",))
        self.record_all(sha, feature="notes-list", proofs=False)
        self.assert_parked("T1", "notes-create", *self.evidence())

    def test_the_first_changed_feature_in_sorted_order_is_named(self):
        self.init([tiered()])
        edits = self.edit_c1()
        edits[feature_path("notes-list")] = feature_text("notes-list", "src/list.py")
        sha = self.build_with(edits)
        self.record_all(sha)
        self.assert_parked("T1", "notes-create", *self.evidence())

    def test_text_outside_the_contract_and_trailing_spaces_are_not_a_change(self):
        self.init([tiered()])
        text = feature_text("notes-create", "t1.txt",
                            CONTRACT.replace("non-empty string", "non-empty string   "),
                            extra=" are proven by the property tests")
        readme = os.path.join(*SKILL.split("/"), "features", "README.md")
        sha = self.build_with({feature_path("notes-create"): text,
                               readme: "## Contract\n\n- C1 [pre]: not a feature\n"})
        self.record_all(sha)
        rc, out, err = self.evidence()
        self.assertEqual((rc, out.strip()), (0, "EVIDENCE: T1 pass %s" % sha), err)

    def test_an_unchanged_contract_passes_and_merges(self):
        self.init([tiered()])
        sha = self.build_with({})
        self.record_all(sha)
        self.assertEqual(self.evidence()[0], 0)
        rc, out, err = self.run_cmd("merge", "T1")
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(self.proven())

    def test_contract_changed_is_a_park_route(self):
        self.assertIn("contract-changed", C.EVIDENCE_PARK)
        self.assertNotIn("contract-changed", C.EVIDENCE_REDO + C.EVIDENCE_REPAIR)


class PlanWithoutMinTier(ContractGate):
    def test_the_same_edit_in_a_plan_with_no_min_tier_does_not_reject(self):
        self.init([task("T1", ["notes-create"])])
        sha = self.build_with(self.edit_c1())
        self.record("notes-create", sha)
        rc, out, err = self.evidence()
        self.assertEqual((rc, out.strip()), (0, "EVIDENCE: T1 pass %s" % sha), err)
        self.assertNotIn("contract", out + err)

    def test_no_base_commit_is_recorded_at_init(self):
        self.init([tiered()])
        with open(C.state_path(self.root)) as f:
            self.assertNotIn("base_commit", f.read())


class SameDefinitionAsForge(unittest.TestCase):
    SAMPLES = ["", "# x\n", "## Contract", "## Contract  \n- C1 [pre]: a  \n\n## Proof\nx",
               "a\n## Contract\n\n- C1 [pre]: a\n\n\n", "## Contracts\n- C1 [pre]: a\n",
               "x\n## Contract\n- C1 [pre]: a\n## Contract\n- C2 [post]: b\n",
               feature_text("notes-create", "t1.txt"), "## Contract\r\n- C1 [pre]: a\r\n"]

    def test_contract_section_matches_forge(self):
        if not os.path.isfile(FORGE):
            self.skipTest("this checkout ships no verification-skill-forge")
        spec = importlib.util.spec_from_file_location("forge_under_test", FORGE)
        forge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(forge)
        for text in self.SAMPLES:
            self.assertEqual(C.contract_section(text), forge.contract_section(text), text)


if __name__ == "__main__":
    unittest.main()
