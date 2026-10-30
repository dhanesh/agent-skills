#!/usr/bin/env python3
"""test_conductor_escape_hatch.py — the evidence gate sends back a task whose diff adds a
Rust escape hatch (the proof-tier gate spec, R5).

In a tiered plan (a gated plan where at least one task carries `min_tier`), `conductor
evidence` reads the diff from the run base (`git merge-base <run branch> <verified head>`)
to the verified head, limited to `.rs` files, for every task, after the contract check
and before the evidence verdict. On the first escape hatch in git's path order it prints
`EVIDENCE: <task> reject escape-hatch <path>:<line>`, exits 3 and sends the task back as
a repair. A diff git cannot show parks the task with `evidence-diff-unreadable`. A plan
with no `min_tier` is not scanned.

The conductor's copy of the rule passes every vector in
verification-skill-forge/assets/escape_hatch_vectors.json, the list forge's copy passes.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C  # noqa: E402
from test_conductor_evidence import task  # noqa: E402
from test_conductor_contract import ContractGate  # noqa: E402
from test_conductor_tiers import tiered  # noqa: E402

VECTORS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                       "verification-skill-forge", "assets", "escape_hatch_vectors.json")
GENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
EXTERNAL = "#[verifier::external_body]\npub fn f(x: u32) -> u32 {\n    x\n}\n"
KANI = ("#[kani::proof]\nfn h() {\n    let x: u8 = kani::any();\n"
        "    kani::assume(x > 0);\n}\n")


def runtime_task(tid="T1"):
    """A tiered task that needs runtime evidence for C1 only."""
    return dict(tiered(min_tier="runtime", clauses=["notes-create:C1"]), id=tid)


class EscapeHatchGate(ContractGate):
    def assert_repair(self, where, rc, out, err):
        self.assertEqual((rc, out.strip()),
                         (3, "EVIDENCE: T1 reject escape-hatch %s" % where), err)
        self.assertNotIn("Traceback", err)
        self.assertIn("remove it", err)
        t = self.state().tasks["T1"]
        self.assertEqual(t["status"], "verifying")
        self.assertEqual((t["evidence"]["reason"], t["evidence"]["feature"]),
                         ("escape-hatch", where))
        self.assertEqual(t["failures"], 1)
        rc, out, _ = self.run_cmd("resume")
        self.assertIn("NEXT: T1 dispatch-repair evidence", out)

    def passes(self, edits, tasks=None):
        self.init(tasks or [runtime_task()])
        sha = self.build_with(edits)
        self.record("notes-create", sha)
        rc, out, err = self.evidence()
        self.assertEqual((rc, out.strip()), (0, "EVIDENCE: T1 pass %s" % sha), err)
        return sha


class TieredPlan(EscapeHatchGate):
    def test_external_body_added_to_a_rs_file_is_sent_back_as_a_repair(self):
        self.init([runtime_task()])
        sha = self.build_with({"src/lib.rs": EXTERNAL})
        self.record("notes-create", sha)
        self.assert_repair("src/lib.rs:1", *self.evidence())
        self.assertFalse(self.proven())
        # The same commit cannot be verified again: the repair needs a new commit.
        self.assertNotEqual(self.run_cmd("verify", "T1")[0], 0)
        with open(os.path.join(os.path.dirname(C.state_path(self.root)),
                               "autonomy-log.jsonl")) as f:
            self.assertIn('"escape-hatch"', f.read())

    def test_the_check_runs_before_the_evidence_verdict(self):
        # No record at all: the escape-hatch reject, not evidence-missing.
        self.init([runtime_task()])
        self.build_with({"src/lib.rs": EXTERNAL})
        self.assert_repair("src/lib.rs:1", *self.evidence())

    def test_the_first_hit_in_git_path_order_is_named(self):
        self.init([runtime_task()])
        self.build_with({"src/b.rs": "fn b() { admit() }\n",
                         "src/a.rs": "fn a() {}\nfn c(x: bool) { assume(x) }\n"})
        self.assert_repair("src/a.rs:2", *self.evidence())

    def test_an_ungated_task_in_a_tiered_plan_is_scanned_too(self):
        self.init([task("T1", ["notes-list"]), runtime_task("T2")])
        self.build_with({"src/x.rs": "fn x(v: bool) { assume(v) }\n"},
                        files=("src/list.py",))
        self.record("notes-list", self.state().tasks["T1"]["verified_head"])
        self.assert_repair("src/x.rs:1", *self.evidence())

    def test_kani_assume_passes_and_merges(self):
        self.passes({"src/lib.rs": KANI})
        rc, out, err = self.run_cmd("merge", "T1")
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(self.proven())

    def test_the_tokens_in_a_non_rs_file_pass(self):
        self.passes({"notes.md": "assume(x) and #[verifier::external_body]\n"})

    def test_a_gitattributes_cannot_hide_the_added_lines(self):
        self.init([runtime_task()])
        self.build_with({".gitattributes": "*.rs -diff binary\n", "src/lib.rs": EXTERNAL})
        self.record("notes-create", self.state().tasks["T1"]["verified_head"])
        self.assert_repair("src/lib.rs:1", *self.evidence())

    def test_a_moved_rs_file_counts_every_line_as_added(self):
        # The base already holds the token, at src/old.rs; the task only moves the file.
        os.makedirs(os.path.join(self.root, "src"), exist_ok=True)
        with open(os.path.join(self.root, "src", "old.rs"), "w") as f:
            f.write(EXTERNAL)
        subprocess.run(["git", "-C", self.root, "add", "-A"], check=True, env=GENV)
        subprocess.run(["git", "-C", self.root, "commit", "-q", "-m", "old"], check=True,
                       env=GENV)
        self.init([runtime_task()])
        self.build_with({"src/old.rs": None, "src/new.rs": EXTERNAL})
        self.assert_repair("src/new.rs:1", *self.evidence())

    def test_an_unreadable_diff_parks_the_task(self):
        self.init([runtime_task()])
        sha = self.build_with({"src/lib.rs": KANI})
        self.record("notes-create", sha)
        with mock.patch.object(C, "_escape_git", return_value=None):
            rc, out, err = self.evidence()
        self.assertEqual(rc, 3, out + err)
        self.assertEqual(out.splitlines()[0], "EVIDENCE: T1 reject evidence-diff-unreadable")
        self.assertIn("PARK: T1 evidence-diff-unreadable", out)
        self.assertIn("git is not runnable", err)
        t = self.state().tasks["T1"]
        self.assertEqual((t["status"], t["park_reason"]),
                         ("parked", "evidence-diff-unreadable"))

    def test_escape_hatch_is_a_repair_route(self):
        self.assertIn("escape-hatch", C.EVIDENCE_REPAIR)
        self.assertNotIn("escape-hatch", C.EVIDENCE_REDO + C.EVIDENCE_PARK)
        self.assertIn("evidence-diff-unreadable", C.EVIDENCE_PARK)


class PlanWithoutMinTier(EscapeHatchGate):
    def test_the_same_external_body_diff_does_not_reject(self):
        sha = self.passes({"src/lib.rs": EXTERNAL}, tasks=[task("T1", ["notes-create"])])
        self.assertTrue(sha)


def load_vectors():
    with open(VECTORS, encoding="utf-8") as f:
        return json.load(f)


def write_tree(d, files):
    for rel, text in files.items():
        p = os.path.join(d, *rel.split("/"))
        if text is None:
            if os.path.exists(p):
                os.remove(p)
            continue
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)


@unittest.skipUnless(os.path.isfile(VECTORS), "verification-skill-forge is not here")
class SharedVectors(unittest.TestCase):
    """The conductor's copy of the rule against the vector list forge's copy passes."""

    def test_every_line_vector_gives_its_hits(self):
        lines = load_vectors()["lines"]
        self.assertTrue(lines)
        for v in lines:
            with self.subTest(line=v["line"]):
                self.assertEqual(C.escape_hatches(v["line"]), [tuple(h) for h in v["hits"]])

    def test_the_rule_regex_is_the_spec_regex(self):
        self.assertEqual(C.ESCAPE_ATTR_RE.pattern, r"#\[verifier\b[^\]]*\bexternal_body\b")
        self.assertIn(C.ESCAPE_ATTR_RE.pattern, load_vectors()["rule"])

    def test_every_case_gives_its_escapes(self):
        cases = load_vectors()["cases"]
        self.assertTrue(cases)
        for case in cases:
            with self.subTest(case=case["name"]):
                d = tempfile.mkdtemp(prefix="escape-hatch-")
                try:
                    subprocess.run(["git", "-C", d, "init", "-q"], check=True, env=GENV)
                    write_tree(d, {"seed.txt": "seed\n"})
                    head = dict(case["head"])
                    for rel in case["base"]:
                        head.setdefault(rel, None)
                    for files, msg in (({}, "seed"), (case["base"], "base"), (head, "head")):
                        write_tree(d, files)
                        subprocess.run(["git", "-C", d, "add", "-A"], check=True, env=GENV)
                        subprocess.run(["git", "-C", d, "commit", "-q", "--allow-empty",
                                        "-m", msg], check=True, env=GENV)
                    hits, why = C.escape_hatch_hits(d, "HEAD~1", "HEAD")
                finally:
                    shutil.rmtree(d, ignore_errors=True)
                self.assertIsNone(why)
                self.assertEqual(hits, [tuple(e) for e in case["escapes"]])


if __name__ == "__main__":
    unittest.main()
