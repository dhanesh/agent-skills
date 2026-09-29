#!/usr/bin/env python3
"""test_conductor_evidence.py — the evidence gate (spec 5.1), the independence partition
(5.2) and the decision trail (5.3).

Each rejection case in the spec's table gets a test: evidence from a stale SHA, a
hand-forged record whose `sha` does not match, a red or missing doctor, a touched feature
with no map entry, writer == verifier, a missing or altered artifact, and a grant that
lapses between the verdict and the merge. Plus the regression the build order asks for:
remove one evidence artifact after the verdict and the merge blocks.

Evidence records are written here in the verify-evidence/v1 shape that
verification-skill-forge's verify_evidence.py writes, so this suite needs no other skill.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C  # noqa: E402
from conductor_testkit import GIT, repo, write_grant, write_plan_envelope  # noqa: E402

SKILL = ".claude/skills/verify-app"
FEATURE = """# {fid}: {fid}

- id: {fid}
- proven: no
- anchors: {anchors}

## What it is

x

## How to reach it

x

## Drive it

```sh
true
```

Expect exit 0.

## Proof

x

## Gotchas

x
"""


def git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                          env=dict(os.environ, **GIT), check=True).stdout.strip()


def add_verify_skill(root, features):
    """Commit a verify-app skill whose map holds `features` ({id: anchors})."""
    d = os.path.join(root, *SKILL.split("/"))
    os.makedirs(os.path.join(d, "features"))
    with open(os.path.join(d, "SKILL.md"), "w") as f:
        f.write("---\nname: verify-app\ndescription: x\n---\n")
    with open(os.path.join(d, "features", "README.md"), "w") as f:
        f.write("".join("- [%s](%s.md)\n" % (k, k) for k in features))
    for fid, anchors in features.items():
        with open(os.path.join(d, "features", fid + ".md"), "w") as f:
            f.write(FEATURE.format(fid=fid, anchors=anchors))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "verify skill")


def task(tid, features, where=None, deps=(), independence=None):
    t = {"id": tid, "requirement_ids": ["R1"], "title": tid, "depends_on": list(deps),
         "verify": [{"text": "ok", "command": ["true"]}], "features": features}
    if where:
        t["where"] = where
    if independence:
        t["independence"] = independence
    return t


def payload(tasks, verification=True):
    p = {"title": "Gate", "spec": "docs/spec.md", "coverage": {}, "uncovered": [],
         "tasks": tasks}
    if verification:
        p["verification"] = {"skill": SKILL}
    return p


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Gate(unittest.TestCase):
    FEATURES = {"notes-create": "t1.txt", "notes-list": "src/list.py"}

    def setUp(self):
        self.root = repo()
        add_verify_skill(self.root, self.FEATURES)
        self.evdir = os.path.join(self.root, ".verify")

    def run_cmd(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = C.main([*argv, "--root", self.root])
        return rc, out.getvalue(), err.getvalue()

    def init(self, tasks, verification=True, budget=None):
        plan = write_plan_envelope(self.root, plan=payload(tasks, verification))
        write_grant(self.root, plan, budget=budget)
        rc, out, err = self.run_cmd("init", "--plan", plan)
        self.assertEqual(rc, 0, out + err)
        self.plan = plan
        return plan

    def state(self):
        return C.State.load(C.state_path(self.root))

    def build(self, tid="T1", owner="writer", files=("t1.txt",)):
        """start, commit work, verify, review: the task waits for its evidence verdict."""
        rc, out, err = self.run_cmd("start", tid, "--owner", owner)
        self.assertEqual(rc, 0, out + err)
        wt = out.split(" ", 2)[2].strip()
        for name in files:
            path = os.path.join(wt, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write("done\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "work")
        rc, out, err = self.run_cmd("verify", tid)
        self.assertEqual(rc, 0, out + err)
        sha = out.split()[-1]
        rc, out, err = self.run_cmd("review", tid, "--verdict", "pass", "--reviewer",
                                    "reviewer")
        self.assertEqual(rc, 0, out + err)
        return wt, sha

    def record(self, feature, sha, verifier="verifier", instance="a", result="pass",
               sha_field=None, doctor=True, doctor_ok=True):
        d = os.path.join(self.evdir, instance, feature, sha)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "capture.json"), "w") as f:
            f.write('{"status": 201}\n')
        digest = hashlib.sha256(b'{"status": 201}\n').hexdigest()
        with open(os.path.join(d, "evidence.json"), "w") as f:
            json.dump({"schema": "verify-evidence/v1", "kind": "evidence",
                       "instance": instance, "feature": feature,
                       "sha": sha_field or sha, "verifier": verifier, "result": result,
                       "action": "a", "observed": "o", "side_effects": ["s"],
                       "artifacts": [{"path": "capture.json", "sha256": digest}],
                       "captured_at": now()}, f)
        if doctor:
            dd = os.path.join(self.evdir, instance, "doctor", sha)
            os.makedirs(dd, exist_ok=True)
            with open(os.path.join(dd, "doctor.json"), "w") as f:
                json.dump({"schema": "verify-evidence/v1", "kind": "doctor",
                           "instance": instance, "sha": sha, "ok": doctor_ok,
                           "checks": {"port": "pass" if doctor_ok else "fail"},
                           "verifier": verifier, "captured_at": now()}, f)
        return d

    def evidence(self, tid="T1", verifier="verifier"):
        return self.run_cmd("evidence", tid, "--verifier", verifier)

    def proven(self, tid="T1"):
        return self.state().tasks[tid]["status"] == "proven"


class InitTests(Gate):
    def test_features_without_a_verification_block_are_refused(self):
        plan = write_plan_envelope(self.root, plan=payload([task("T1", ["notes-create"])],
                                                           verification=False))
        write_grant(self.root, plan)
        rc, _, err = self.run_cmd("init", "--plan", plan)
        self.assertEqual(rc, 2)
        self.assertIn("declares no verification block", err)

    def test_a_task_without_features_is_refused_under_the_gate(self):
        plan = write_plan_envelope(self.root, plan=payload([task("T1", [])]))
        write_grant(self.root, plan)
        rc, _, err = self.run_cmd("init", "--plan", plan)
        self.assertEqual(rc, 2)
        self.assertIn("every task needs `features`", err)

    def test_a_missing_verify_skill_is_refused(self):
        p = payload([task("T1", ["notes-create"])])
        p["verification"] = {"skill": ".claude/skills/verify-nothing"}
        plan = write_plan_envelope(self.root, plan=p)
        write_grant(self.root, plan)
        rc, _, err = self.run_cmd("init", "--plan", plan)
        self.assertEqual(rc, 2)
        self.assertIn("is not a verify-<app> skill", err)

    def test_the_gate_derives_a_verifier_dispatch_per_attempt(self):
        self.init([task("T1", ["notes-create"])])
        st = self.state()
        self.assertEqual(st.budget["max_dispatches"], 1 * 3 * (1 + 2))
        self.assertEqual(st.verification["skill"], SKILL)
        rc, out, _ = self.run_cmd("status")
        self.assertIn("evidence_gate=on skill=%s" % SKILL, out)

    def test_a_legacy_plan_reports_the_gate_off(self):
        plan = write_plan_envelope(self.root, plan=payload(
            [dict(task("T1", []), features=None)], verification=False))
        write_grant(self.root, plan)
        self.assertEqual(self.run_cmd("init", "--plan", plan)[0], 0)
        self.assertIn("evidence_gate=off", self.run_cmd("status")[1])


class IdentityTests(Gate):
    def test_start_needs_an_owner(self):
        self.init([task("T1", ["notes-create"])])
        rc, _, err = self.run_cmd("start", "T1")
        self.assertEqual(rc, 2)
        self.assertIn("--owner", err)

    def test_the_writer_cannot_review(self):
        self.init([task("T1", ["notes-create"])])
        rc, out, _ = self.run_cmd("start", "T1", "--owner", "writer")
        wt = out.split(" ", 2)[2].strip()
        with open(os.path.join(wt, "t1.txt"), "w") as f:
            f.write("x")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "w")
        self.run_cmd("verify", "T1")
        rc, _, err = self.run_cmd("review", "T1", "--verdict", "pass", "--reviewer", "writer")
        self.assertEqual(rc, 2)
        self.assertIn("cannot review", err)
        self.assertIsNone(self.state().tasks["T1"]["review"])

    def test_writer_is_verifier_is_rejected(self):
        self.init([task("T1", ["notes-create"])])
        _, sha = self.build()
        self.record("notes-create", sha, verifier="writer")
        rc, out, _ = self.evidence(verifier="writer")
        self.assertEqual(rc, 3)
        self.assertIn("EVIDENCE: T1 reject writer-is-verifier", out)
        self.assertEqual(self.run_cmd("merge", "T1")[0], 2)
        self.assertFalse(self.proven())


class PredicateTests(Gate):
    def setUp(self):
        super().setUp()
        self.init([task("T1", ["notes-create"])])
        self.wt, self.sha = self.build()

    def test_good_evidence_merges(self):
        self.record("notes-create", self.sha)
        rc, out, _ = self.evidence()
        self.assertEqual((rc, out.strip()), (0, "EVIDENCE: T1 pass %s" % self.sha))
        rc, out, err = self.run_cmd("merge", "T1")
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(self.proven())

    def test_merge_without_a_verdict_is_refused(self):
        self.record("notes-create", self.sha)
        rc, _, err = self.run_cmd("merge", "T1")
        self.assertEqual(rc, 2)
        self.assertIn("no passing evidence verdict", err)

    def test_missing_evidence_is_rejected(self):
        rc, out, _ = self.evidence()
        self.assertIn("reject evidence-missing notes-create", out)
        self.assertEqual(rc, 3)

    def test_evidence_from_a_stale_sha_is_rejected(self):
        self.record("notes-create", git(self.root, "rev-parse", "HEAD"))
        rc, out, _ = self.evidence()
        self.assertIn("reject evidence-stale-sha notes-create", out)
        self.assertEqual(self.run_cmd("merge", "T1")[0], 2)

    def test_a_hand_forged_sha_is_rejected(self):
        self.record("notes-create", self.sha, sha_field="f" * 40)
        self.assertIn("reject evidence-sha-mismatch", self.evidence()[1])
        self.assertFalse(self.proven())

    def test_another_verifiers_record_is_rejected(self):
        self.record("notes-create", self.sha, verifier="someone-else")
        self.assertIn("reject evidence-verifier-mismatch", self.evidence()[1])

    def test_doctor_red_is_rejected(self):
        self.record("notes-create", self.sha, doctor_ok=False)
        self.assertIn("reject doctor-red", self.evidence()[1])

    def test_doctor_missing_is_rejected(self):
        self.record("notes-create", self.sha, doctor=False)
        self.assertIn("reject doctor-missing", self.evidence()[1])

    def test_an_altered_artifact_is_rejected(self):
        d = self.record("notes-create", self.sha)
        with open(os.path.join(d, "capture.json"), "w") as f:
            f.write('{"status": 500}\n')
        self.assertIn("reject evidence-artifact-altered", self.evidence()[1])

    def test_a_failed_proof_sends_the_task_back(self):
        self.record("notes-create", self.sha, result="fail")
        rc, out, _ = self.evidence()
        self.assertIn("reject evidence-failed", out)
        t = self.state().tasks["T1"]
        self.assertEqual((t["status"], t["repairs"]), ("verifying", 0))
        self.assertEqual(t["failures"], 1)

    def test_removing_an_artifact_after_the_verdict_blocks_the_merge(self):
        d = self.record("notes-create", self.sha)
        self.assertEqual(self.evidence()[0], 0)
        os.remove(os.path.join(d, "capture.json"))
        rc, _, err = self.run_cmd("merge", "T1")
        self.assertEqual(rc, 2)
        self.assertIn("evidence-artifact-missing", err)
        self.assertFalse(self.proven())
        self.assertEqual(self.state().tasks["T1"]["evidence"]["verdict"], "reject")

    def test_a_grant_that_lapses_after_the_verdict_blocks_the_merge(self):
        self.record("notes-create", self.sha)
        self.assertEqual(self.evidence()[0], 0)
        write_grant(self.root, self.plan, minutes=-5)
        rc, out, _ = self.run_cmd("merge", "T1")
        self.assertEqual(rc, 3)
        self.assertIn("GATE: ASK", out)
        self.assertFalse(self.proven())

    def test_a_commit_after_the_verdict_forces_a_new_proof(self):
        self.record("notes-create", self.sha)
        self.assertEqual(self.evidence()[0], 0)
        with open(os.path.join(self.wt, "backdoor.txt"), "w") as f:
            f.write("x")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", "late")
        rc, _, err = self.run_cmd("merge", "T1")
        self.assertEqual(rc, 2)
        self.assertIn("verify again", err)
        t = self.state().tasks["T1"]
        self.assertEqual((t["status"], t["evidence"], t["review"]), ("verifying", None, None))

    def test_resume_asks_for_a_verifier(self):
        rc, out, _ = self.run_cmd("resume")
        self.assertIn("NEXT: T1 dispatch-verifier %s" % self.sha, out)


class TouchedFeatureTests(Gate):
    def test_a_declared_feature_with_no_map_entry_parks_for_maintain(self):
        self.init([task("T1", ["notes-delete"])])
        _, sha = self.build()
        rc, out, _ = self.evidence()
        self.assertIn("reject feature-unmapped notes-delete", out)
        t = self.state().tasks["T1"]
        self.assertEqual((t["status"], t["park_reason"]), ("parked", "feature-unmapped"))

    def test_the_diff_adds_the_features_whose_anchors_it_touches(self):
        self.init([task("T1", ["notes-create"])])
        _, sha = self.build(files=("t1.txt", "src/list.py"))
        self.record("notes-create", sha)
        rc, out, _ = self.evidence()
        self.assertIn("reject evidence-missing notes-list", out)
        self.record("notes-list", sha)
        self.assertEqual(self.evidence()[0], 0)
        self.assertEqual(self.state().tasks["T1"]["evidence"]["features"],
                         ["notes-create", "notes-list"])


class PartitionTests(Gate):
    def test_unknown_files_are_serialized_under_the_gate(self):
        self.init([task("T1", ["notes-create"]), task("T2", ["notes-list"])])
        st = self.state()
        self.assertEqual(st.tasks["T2"]["serial_after"], ["T1"])
        self.assertEqual(st.ready(2), ["T1"])
        with open(st.log_path) as f:
            init = json.loads(f.readline())
        self.assertEqual(init["event"], "init")
        self.assertEqual(init["partition"]["serialized"],
                         [{"task": "T2", "after": "T1", "reason": "files unknown"}])

    def test_disjoint_parallel_safe_tasks_fan_out(self):
        self.init([task("T1", ["notes-create"], where="t1.txt", independence="parallel-safe"),
                   task("T2", ["notes-list"], where="src/list.py",
                        independence="parallel-safe")])
        self.assertEqual(self.state().ready(2), ["T1", "T2"])

    def test_shared_files_or_features_serialize(self):
        self.init([task("T1", ["notes-create"], where="src/"),
                   task("T2", ["notes-list"], where="src/list.py"),
                   task("T3", ["notes-create"], where="t3.txt")])
        part = self.state().partition
        self.assertEqual(part["mode"], "evidence")
        reasons = {(x["task"], x["after"]): x["reason"] for x in part["serialized"]}
        self.assertEqual(reasons[("T2", "T1")], "shared files")
        self.assertEqual(reasons[("T3", "T1")], "shared feature notes-create")

    def test_a_parked_predecessor_releases_the_serialized_task(self):
        self.init([task("T1", ["notes-create"]), task("T2", ["notes-list"])])
        self.run_cmd("start", "T1", "--owner", "w")
        self.run_cmd("park", "T1", "--reason", "x")
        self.assertEqual(self.state().ready(2), ["T2"])


class TrailTests(Gate):
    def test_the_trail_records_every_unit(self):
        self.init([task("T1", ["notes-create"])])
        wt, sha = self.build()
        self.record("notes-create", sha)
        self.evidence()
        self.run_cmd("merge", "T1")
        out_path = os.path.join(self.root, "trail.json")
        rc, out, _ = self.run_cmd("trail", "--out", out_path)
        self.assertEqual(rc, 0)
        unit = json.loads(out.splitlines()[0][len("TRAIL: "):])
        self.assertEqual((unit["owner"], unit["reviewer"], unit["verdict_agent"],
                          unit["verified_head"]), ("writer", "reviewer", "verifier", sha))
        self.assertEqual(unit["worktree"], wt)
        self.assertTrue(unit["dispatched_at"])
        self.assertEqual(unit["merge"]["outcome"], "merged")
        self.assertEqual(unit["evidence"]["paths"],
                         [".verify/a/notes-create/%s/evidence.json" % sha])
        with open(out_path) as f:
            self.assertTrue(json.load(f)["evidence_gate"])


if __name__ == "__main__":
    unittest.main()
