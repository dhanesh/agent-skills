#!/usr/bin/env python3
"""test_conductor_tiers.py — the evidence gate checks proof tiers (the proof-tier gate
spec, R3).

A tiered task carries `min_tier` and `clauses` in the pinned plan. Once every existing
evidence check passes, `conductor evidence` rejects the first clause, in `clauses` order,
that has no proof record at or above `min_tier`, as `tier-below-required <clause>`: a
repair on the evidence-failed route. A tier field that cannot be read from the pinned
plan rejects as `tier-field-unreadable <field>` and parks the task.

Proof records are written in the shape verification-skill-forge's verify_evidence.py
writes (`record --tier`). One test also runs that recorder, with a fake allowlisted tool
(a PATH shim named after its runner, pytest) and a mutant, when the checkout ships it.
"""
import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C  # noqa: E402
from conductor_testkit import write_grant, write_plan_envelope  # noqa: E402
from test_conductor_evidence import Gate, git, now, payload, task  # noqa: E402

RECORDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                        "verification-skill-forge", "assets", "verify_evidence.py")
TWO = ["notes-create:C1", "notes-create:C2"]
# A fake hypothesis run: it passes on good code and prints Hypothesis's failure report
# when the mutant sets target.txt to "caught".
FAKE_TOOL = '''import sys
mode = open("target.txt").read().strip()
if mode == "caught":
    print("Falsifying example: test_create(title='')\\n1 failed in 0.01s")
    sys.exit(1)
print("1 passed in 0.01s")
'''
MUTANT = """diff --git a/target.txt b/target.txt
--- a/target.txt
+++ b/target.txt
@@ -1 +1 @@
-good
+caught
"""
# A runner argv per tool, as `record --tier` stores it: the stored argv runs the tool.
RUNNER_ARGV = {"verus": ["verus"], "kani": ["cargo", "kani"], "hypothesis": ["pytest"],
               "proptest": ["cargo", "test"], "fast-check": ["vitest"],
               "rapid": ["go", "test"]}
# (tool, argv, runner_matches) vectors that the conductor's copy and the recorder's
# runner_matches both answer (R2). The recorder's own VECTORS are added when present.
SHARED_VECTORS = [
    ("hypothesis", "pytest", True),
    ("hypothesis", "pytest -q tests/test_notes.py", True),
    ("hypothesis", "python3 -m pytest", True),
    ("hypothesis", "python3.12 -m pytest", True),
    ("hypothesis", "uv run pytest", True),
    ("hypothesis", "python3 t.py", False),
    ("hypothesis", "./bin/pytest", False),
    ("hypothesis", "uv run --with x pytest", False),
    ("hypothesis", "uv run poetry run pytest", False),
    ("hypothesis", "python -X dev -m pytest", False),
    ("hypothesis", "pythonista -m pytest", False),
    ("verus", "verus src/notes.rs", True),
    ("verus", "cargo verus verify", True),
    ("verus", "cargo test", False),
    ("kani", "cargo kani --harness h", True),
    ("proptest", "cargo test --release", True),
    ("proptest", "cargo +nightly test", False),
    ("fast-check", "npx vitest run", True),
    ("fast-check", "jest", True),
    ("rapid", "go test ./...", True),
    ("rapid", "go vet", False),
    ("crosshair", "crosshair check", False),
]


def tiered(min_tier="property", clauses=TWO, features=("notes-create",)):
    t = task("T1", list(features))
    t.update(min_tier=min_tier, clauses=list(clauses))
    return t


class TierGate(Gate):
    def proof(self, clause, sha, tier="property", tool="hypothesis", verifier="verifier",
              instance="a", feature="notes-create", doc_tier=None, **over):
        """Write a proof record as `record --tier` does; `over` replaces fields (None
        drops one), and doc_tier sets a `tier` field that differs from the file name.
        Returns the record's directory."""
        d = os.path.join(self.evdir, instance, feature, sha)
        os.makedirs(d, exist_ok=True)
        stem = "proof-%s-%s-%s" % (tier, tool, clause)
        files = [("stdout", b"1 passed in 0.01s\n"), ("stderr", b"")]
        if tier in ("bounded", "property"):
            files += [("mutant.patch", MUTANT.encode()), ("mutant.exit", b"1\n"),
                      ("mutant.stdout", b"Falsifying example\n1 failed\n"),
                      ("mutant.stderr", b"")]
        arts = []
        for ext, data in files:
            name = "%s.%s" % (stem, ext)
            with open(os.path.join(d, name), "wb") as f:
                f.write(data)
            arts.append({"path": name, "sha256": hashlib.sha256(data).hexdigest()})
        doc = {"schema": "verify-evidence/v1", "kind": "evidence", "instance": instance,
               "feature": feature, "sha": sha, "verifier": verifier, "result": "pass",
               "tier": tier, "tool": tool, "clauses": [clause],
               "argv": list(RUNNER_ARGV.get(tool, [tool])),
               "cwd": "/x", "timeout": 900, "exit_status": 0, "artifacts": arts,
               "captured_at": now()}
        if tier in ("bounded", "property"):
            doc.update(mutant_author="verifier", mutant_exit_status=1)
        if doc_tier is not None:
            doc["tier"] = doc_tier
        for k, v in over.items():
            if v is None:
                doc.pop(k, None)
            else:
                doc[k] = v
        with open(os.path.join(d, stem + ".json"), "w") as f:
            json.dump(doc, f)
        return d

    def tiered_run(self, **kw):
        self.init([tiered(**kw)])
        self.wt, self.sha = self.build()
        self.record("notes-create", self.sha)  # runtime evidence + green doctor
        return self.sha

    def reject_line(self, reason):
        return "EVIDENCE: T1 reject %s" % reason


class BelowRequired(TierGate):
    def test_runtime_only_for_c2_rejects_c2_as_a_repair(self):
        sha = self.tiered_run()
        self.proof("C1", sha)
        rc, out, err = self.evidence()
        self.assertEqual((rc, out.strip()),
                         (3, self.reject_line("tier-below-required notes-create:C2")), err)
        self.assertNotIn("Traceback", err)
        t = self.state().tasks["T1"]
        self.assertEqual(t["status"], "verifying")
        self.assertEqual((t["evidence"]["reason"], t["evidence"]["feature"]),
                         ("tier-below-required", "notes-create:C2"))
        self.assertEqual(t["failures"], 1)
        rc, out, _ = self.run_cmd("resume")
        self.assertIn("NEXT: T1 dispatch-repair evidence", out)
        self.assertNotIn("NEXT: T1 verify", out)
        # The rejected commit stays refused: only a new commit can be proven again.
        rc, out, err = self.run_cmd("verify", "T1")
        self.assertEqual(rc, 3)
        self.assertIn("no new commit since the evidence failed", err)
        self.assertEqual(self.state().tasks["T1"]["evidence"]["reason"],
                         "tier-below-required")
        self.assertEqual(self.run_cmd("merge", "T1")[0], 2)
        self.assertFalse(self.proven())

    def test_the_first_unmet_clause_in_clauses_order_is_named(self):
        sha = self.tiered_run(clauses=["notes-create:C2", "notes-create:C1"])
        rc, out, _ = self.evidence()
        self.assertEqual(out.strip(), self.reject_line("tier-below-required notes-create:C2"))

    def test_property_records_for_both_clauses_pass_and_merge(self):
        sha = self.tiered_run()
        self.proof("C1", sha)
        self.proof("C2", sha)
        rc, out, _ = self.evidence()
        self.assertEqual((rc, out.strip()), (0, "EVIDENCE: T1 pass %s" % sha))
        rc, out, err = self.run_cmd("merge", "T1")
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(self.proven())

    def test_a_stronger_tier_meets_a_weaker_min_tier(self):
        sha = self.tiered_run()
        self.proof("C1", sha, tier="proved", tool="verus")
        self.proof("C2", sha, tier="bounded", tool="kani")
        self.assertEqual(self.evidence()[0], 0)

    def test_a_weaker_tier_does_not_meet_a_stronger_min_tier(self):
        sha = self.tiered_run(min_tier="bounded")
        self.proof("C1", sha, tier="bounded", tool="kani")
        self.proof("C2", sha, tier="property", tool="hypothesis")
        self.assertEqual(self.evidence()[1].strip(),
                         self.reject_line("tier-below-required notes-create:C2"))

    def test_runtime_min_tier_is_met_by_the_accepted_evidence(self):
        sha = self.tiered_run(min_tier="runtime")
        self.assertEqual(self.evidence(), (0, "EVIDENCE: T1 pass %s\n" % sha, ""))

    def test_existing_checks_come_first(self):
        # No runtime evidence at all: the old reason, not a tier reason.
        self.init([tiered()])
        _, sha = self.build()
        self.proof("C1", sha)
        self.proof("C2", sha)
        self.assertIn("reject evidence-missing notes-create", self.evidence()[1])

    def test_a_record_deleted_after_the_verdict_blocks_the_merge(self):
        sha = self.tiered_run()
        self.proof("C1", sha)
        d = self.proof("C2", sha)
        self.assertEqual(self.evidence()[0], 0)
        os.remove(os.path.join(d, "proof-property-hypothesis-C2.json"))
        rc, _, err = self.run_cmd("merge", "T1")
        self.assertEqual(rc, 2)
        self.assertIn("tier-below-required", err)
        self.assertFalse(self.proven())


class RecordsThatDoNotCount(TierGate):
    """Each record below is the only C2 record; each leaves C2 below its tier."""

    def assert_c2_unmet(self, **proof):
        sha = self.tiered_run()
        self.proof("C1", sha)
        d = self.proof("C2", sha, **proof)
        rc, out, err = self.evidence()
        self.assertEqual((rc, out.strip()),
                         (3, self.reject_line("tier-below-required notes-create:C2")),
                         err)
        return d

    def test_a_record_written_by_the_owner(self):
        self.assert_c2_unmet(verifier="writer")

    def test_a_record_by_another_verifier(self):
        self.assert_c2_unmet(verifier="someone-else")

    def test_a_record_whose_stdout_was_edited(self):
        sha = self.tiered_run()
        self.proof("C1", sha)
        d = self.proof("C2", sha)
        with open(os.path.join(d, "proof-property-hypothesis-C2.stdout"), "w") as f:
            f.write("edited\n")
        self.assertEqual(self.evidence()[1].strip(),
                         self.reject_line("tier-below-required notes-create:C2"))

    def test_a_tool_outside_the_tier_allowlist(self):
        self.assert_c2_unmet(tool="crosshair")

    def test_a_tier_field_that_differs_from_the_file_name(self):
        # The file is named property, but the record says proved.
        self.assert_c2_unmet(doc_tier="proved")

    def test_clauses_that_name_another_clause(self):
        self.assert_c2_unmet(clauses=["C1"])

    def test_clauses_that_name_two_clauses(self):
        self.assert_c2_unmet(clauses=["C2", "C1"])

    def test_a_mutant_that_was_not_caught(self):
        self.assert_c2_unmet(mutant_exit_status=0)

    def test_no_mutant_exit_status(self):
        self.assert_c2_unmet(mutant_exit_status=None)

    def test_a_mutant_artifact_that_is_not_listed(self):
        sha = self.tiered_run()
        self.proof("C1", sha)
        d = self.proof("C2", sha)
        path = os.path.join(d, "proof-property-hypothesis-C2.json")
        with open(path) as f:
            doc = json.load(f)
        doc["artifacts"] = [a for a in doc["artifacts"] if not a["path"].endswith(
            ".mutant.patch")]
        with open(path, "w") as f:
            json.dump(doc, f)
        self.assertEqual(self.evidence()[1].strip(),
                         self.reject_line("tier-below-required notes-create:C2"))

    def test_an_argv_that_does_not_run_the_tool(self):
        # R2: --tool hypothesis on a command that runs a plain script does not count.
        self.assert_c2_unmet(argv=["python3", "t.py"])

    def test_an_argv_through_a_path_does_not_count(self):
        self.assert_c2_unmet(argv=["./bin/pytest"])

    def test_no_argv(self):
        self.assert_c2_unmet(argv=None)

    def test_an_argv_that_is_not_a_list(self):
        self.assert_c2_unmet(argv="pytest")

    def test_a_record_at_another_head(self):
        sha = self.tiered_run()
        self.proof("C1", sha)
        other = git(self.root, "rev-parse", "HEAD")
        self.proof("C2", other)
        self.assertEqual(self.evidence()[1].strip(),
                         self.reject_line("tier-below-required notes-create:C2"))


class RunnerBoundRecords(TierGate):
    """R2: the same property record counts with a runner argv and not without one."""

    def gated_run(self, argv):
        sha = self.tiered_run(clauses=["notes-create:C1"])
        self.proof("C1", sha, argv=argv)
        return sha, self.evidence()

    def test_a_script_argv_is_rejected_below_the_tier(self):
        _, (rc, out, err) = self.gated_run(["python3", "t.py"])
        self.assertEqual((rc, out.strip()),
                         (3, self.reject_line("tier-below-required notes-create:C1")), err)
        self.assertEqual(self.run_cmd("merge", "T1")[0], 2)
        self.assertFalse(self.proven())

    def test_the_same_record_with_a_runner_argv_passes(self):
        sha, (rc, out, err) = self.gated_run(["pytest"])
        self.assertEqual((rc, out), (0, "EVIDENCE: T1 pass %s\n" % sha), err)
        self.assertEqual(self.run_cmd("merge", "T1")[0], 0)

    def test_a_wrapped_runner_counts(self):
        sha = self.tiered_run(clauses=["notes-create:C1"])
        self.proof("C1", sha, argv=["uv", "run", "python3", "-m", "pytest", "-q"])
        self.assertEqual(self.evidence()[0], 0)


@unittest.skipUnless(os.path.isfile(RECORDER), "verification-skill-forge is not here")
class RunnerTablesMatchTheRecorder(unittest.TestCase):
    """R2: the conductor's runner table, wrappers and failure signatures are copies of the
    recorder's, and both runner_matches give the same answer on one vector list."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("verify_evidence_r2", RECORDER)
        cls.V = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.V)
        cls.vectors = list(SHARED_VECTORS)
        forge = os.path.join(os.path.dirname(RECORDER), "test_record_runner.py")
        if os.path.isfile(forge):
            with open(forge, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == "VECTORS"
                        for t in node.targets):
                    cls.vectors += ast.literal_eval(node.value)

    def test_the_tables_are_equal(self):
        self.assertEqual(C.RUNNERS, self.V.RUNNERS)
        self.assertEqual(C.WRAPPERS, self.V.WRAPPERS)
        self.assertEqual(C.FAILURE_SIGNATURES, self.V.FAILURE_SIGNATURES)
        self.assertEqual(C.PYTHON_RE.pattern, self.V.PYTHON_RE.pattern)
        self.assertEqual(set(C.RUNNERS), {t for ts in C.TIER_TOOLS.values() for t in ts})

    def test_both_copies_answer_the_shared_vectors_the_same(self):
        self.assertGreater(len(self.vectors), len(SHARED_VECTORS))
        for tool, cmd, want in self.vectors:
            with self.subTest(tool=tool, cmd=cmd):
                self.assertIs(C.runner_matches(tool, cmd.split()), want)
                self.assertIs(self.V.runner_matches(tool, cmd.split()), want)
        self.assertFalse(C.runner_matches("hypothesis", []))

    def test_every_fixture_argv_runs_its_tool(self):
        for tool, argv in RUNNER_ARGV.items():
            with self.subTest(tool=tool):
                self.assertTrue(C.runner_matches(tool, argv))
                self.assertTrue(self.V.runner_matches(tool, argv))


class UnreadableFields(TierGate):
    """A pinned plan whose tier fields init would refuse, built by hand: init's tier
    check is bypassed, so the plan file and its pin agree."""

    def init_bypassing(self, fields):
        t = task("T1", ["notes-create"])
        t.update(fields)
        plan = write_plan_envelope(self.root, plan=payload([t]))
        write_grant(self.root, plan)
        with mock.patch.object(C, "plan_tier_problems", lambda plan: []):
            rc, out, err = self.run_cmd("init", "--plan", plan)
        self.assertEqual(rc, 0, out + err)
        _, sha = self.build()
        self.record("notes-create", sha)
        self.proof("C1", sha)
        return sha

    def assert_parks(self, fields, field):
        self.init_bypassing(fields)
        rc, out, err = self.evidence()
        self.assertEqual((rc, out.splitlines()),
                         (3, [self.reject_line("tier-field-unreadable %s" % field),
                              "PARK: T1 tier-field-unreadable"]), err)
        self.assertNotIn("Traceback", out + err)
        t = self.state().tasks["T1"]
        self.assertEqual((t["status"], t["park_reason"]),
                         ("parked", "tier-field-unreadable"))

    def test_clauses_a_number_entry(self):
        self.assert_parks({"min_tier": "property", "clauses": [5]}, "clauses")

    def test_clauses_a_null_entry(self):
        self.assert_parks({"min_tier": "property", "clauses": [None]}, "clauses")

    def test_clauses_a_string(self):
        self.assert_parks({"min_tier": "property", "clauses": "x"}, "clauses")

    def test_min_tier_null(self):
        self.assert_parks({"min_tier": None, "clauses": ["notes-create:C1"]}, "min_tier")

    def test_min_tier_alone(self):
        self.assert_parks({"min_tier": "property"}, "clauses")

    def test_clauses_alone(self):
        self.assert_parks({"clauses": ["notes-create:C1"]}, "min_tier")

    def test_a_plan_edited_after_init_fails_closed(self):
        self.init([tiered()])
        _, sha = self.build()
        self.record("notes-create", sha)
        self.proof("C1", sha)
        self.proof("C2", sha)
        with open(self.plan) as f:
            doc = json.load(f)
        doc["predicate"]["payload"]["tasks"][0].pop("min_tier")
        doc["predicate"]["payload"]["tasks"][0].pop("clauses")
        with open(self.plan, "w") as f:
            json.dump(doc, f)
        # The grant pins the plan, so the gate stops first; either way nothing passes.
        rc, out, err = self.evidence()
        self.assertEqual(rc, 3)
        self.assertNotIn("pass", out)
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.run_cmd("merge", "T1")[0] != 0, True)
        self.assertFalse(self.proven())

    def test_tier_fields_come_from_the_plan_not_state(self):
        # state.json cannot lower the bar: an edit there is never read.
        self.init([tiered()])
        _, sha = self.build()
        self.record("notes-create", sha)
        self.proof("C1", sha)
        st = self.state()
        st.tasks["T1"]["min_tier"] = "runtime"
        st.tasks["T1"]["clauses"] = ["notes-create:C1"]
        st.save()
        self.assertEqual(self.evidence()[1].strip(),
                         self.reject_line("tier-below-required notes-create:C2"))


class NoTierField(TierGate):
    def test_an_untiered_plan_passes_as_before(self):
        self.init([task("T1", ["notes-create"])])
        _, sha = self.build()
        self.record("notes-create", sha)
        rc, out, err = self.evidence()
        self.assertEqual((rc, out, err), (0, "EVIDENCE: T1 pass %s\n" % sha, ""))
        ev = self.state().tasks["T1"]["evidence"]
        self.assertEqual(sorted(ev), sorted(["verdict", "reason", "feature", "observed",
                                             "verifier", "verified_head", "features",
                                             "paths", "doctor", "at"]))
        self.assertEqual(self.run_cmd("merge", "T1")[0], 0)


@unittest.skipUnless(os.path.isfile(RECORDER), "verification-skill-forge is not here")
class RealRecorder(TierGate):
    """The record verify_evidence.py writes, with a fake hypothesis and a mutant it
    catches, counts at property."""

    def test_a_recorded_property_proof_meets_its_clause(self):
        self.init([tiered(clauses=["notes-create:C1"])])
        rc, out, _ = self.run_cmd("start", "T1", "--owner", "writer")
        wt = out.split(" ", 2)[2].strip()
        for name, text in (("t1.txt", "done\n"), ("tool.py", FAKE_TOOL),
                           ("target.txt", "good\n")):
            with open(os.path.join(wt, name), "w") as f:
                f.write(text)
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "work")
        rc, out, err = self.run_cmd("verify", "T1")
        self.assertEqual(rc, 0, out + err)
        sha = out.split()[-1]
        self.run_cmd("review", "T1", "--verdict", "pass", "--reviewer", "reviewer")
        self.record("notes-create", sha)
        rc, out, _ = self.evidence()
        self.assertEqual(out.strip(), self.reject_line("tier-below-required notes-create:C1"))
        # The reject sends the task back; a new commit is proven and reviewed again.
        with open(os.path.join(wt, "t1.txt"), "w") as f:
            f.write("again\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "again")
        rc, out, err = self.run_cmd("verify", "T1")
        self.assertEqual(rc, 0, out + err)
        sha = out.split()[-1]
        self.run_cmd("review", "T1", "--verdict", "pass", "--reviewer", "reviewer")
        self.record("notes-create", sha)
        patch = os.path.join(self.root, "..", "mutant-%s.diff" % os.getpid())
        with open(patch, "w") as f:
            f.write(MUTANT)
        # The recorder runs only a runner of --tool, so the fake tool is reached through a
        # PATH shim named pytest that runs tool.py from its cwd.
        bin_dir = os.path.join(self.root, "..", "bin-%s" % os.getpid())
        os.makedirs(bin_dir, exist_ok=True)
        shim = os.path.join(bin_dir, "pytest")
        with open(shim, "w") as f:
            f.write("#!%s\nimport runpy\nrunpy.run_path('tool.py', run_name='__main__')\n"
                    % sys.executable)
        os.chmod(shim, 0o755)
        r = subprocess.run(
            [sys.executable, RECORDER, "record", "--instance", "a", "--worktree", wt,
             "--feature", "notes-create", "--verifier", "verifier", "--tier", "property",
             "--tool", "hypothesis", "--clauses", "C1",
             "--cmd", "pytest", "--mutant-patch", patch, "--mutant-author", "verifier"],
            capture_output=True, text=True, cwd=wt,
            env=dict(os.environ, VERIFY_EVIDENCE_DIR=self.evdir,
                     PATH=bin_dir + os.pathsep + os.environ.get("PATH", "")))
        os.remove(patch)
        shutil.rmtree(bin_dir, ignore_errors=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PROOF: property", r.stdout)
        self.assertEqual(self.evidence()[:2], (0, "EVIDENCE: T1 pass %s\n" % sha))


if __name__ == "__main__":
    unittest.main()
