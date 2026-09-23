"""test_conductor_e2e.py — one full run, start to finish.

Exercises the worked example in references/run-protocol.md end to end: T1 and T3 are
offered together in wave one (T2 depends on T1, parallel limit 2); T1's verify passes,
is reviewed and merged; T3's verify fails twice and parks (max_repairs_per_task: 1,
the same budget the worked example uses, so the second failure hits the cap); T1 being
proven then makes T2 ready, and it is verified, reviewed and merged too; `next` reports
no more ready tasks; `finish`, with push and PR stubbed through a `git` wrapper on PATH
(the same technique test_conductor_finish.py uses), writes a run-result/v1 envelope that
validates, with two proven tasks and one parked.
"""
import contextlib
import hashlib
import io
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
import contract_check as CC  # noqa: E402
from conductor_testkit import GIT, repo, tmpdir, write_grant, write_plan_envelope  # noqa: E402

PUSH_PR_STUB = r'''
import json, sys
record, what, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
with open(record, "a", encoding="utf-8") as f:
    f.write(json.dumps({"what": what, "argv": rest}) + "\n")
print("stub %s done" % what)
sys.exit(0)
'''


def plan_payload():
    """T2 depends on T1; T3 is independent and its verify never passes."""
    def task(tid, deps, verify):
        return {"id": tid, "requirement_ids": ["R1"], "title": "task " + tid,
                "verify": verify, "depends_on": deps}
    ok = [{"text": "passes", "command": ["true"]}]
    always_fails = [{"text": "never passes", "command": ["false"]}]
    return {"title": "E2E demo", "spec": "docs/spec.md", "coverage": {}, "uncovered": [],
            "tasks": [task("T1", [], ok), task("T2", ["T1"], ok), task("T3", [], always_fails)]}


def commit_in(directory, name, text):
    with open(os.path.join(directory, name), "w") as f:
        f.write(text)
    subprocess.run(["git", "-C", directory, "add", "-A"], check=True)
    subprocess.run(["git", "-C", directory, "commit", "-q", "-m", "c"], check=True,
                   env=dict(os.environ, **GIT))


@unittest.skipUnless(shutil.which("git"), "git not installed")
class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.stub = os.path.join(self.tmp, "stub.py")
        with open(self.stub, "w") as f:
            f.write(PUSH_PR_STUB)
        self.record = os.path.join(self.tmp, "record.jsonl")
        # A `git` on PATH that hands `git push ...` to the stub and passes everything
        # else through to the real git, so finish's push argv stays a real `git push`
        # the allowlist accepts (see test_conductor_finish.py's identical wrapper).
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        wrapper = os.path.join(self.bin, "git")
        with open(wrapper, "w") as f:
            f.write('#!/bin/sh\nif [ "$1" = push ]; then shift; exec "%s" "%s" "%s" push "$@"; fi\n'
                    'exec "%s" "$@"\n' % (sys.executable, self.stub, self.record,
                                          shutil.which("git")))
        os.chmod(wrapper, 0o755)

    def out(self, argv):
        buf, ebuf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(ebuf):
            rc = C.main(argv)
        return rc, buf.getvalue(), ebuf.getvalue()

    def records(self):
        if not os.path.exists(self.record):
            return []
        with open(self.record, encoding="utf-8") as f:
            return [json.loads(l) for l in f]

    def test_full_run_two_proven_one_parked(self):
        root = repo()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        # The grant MUST pin the plan envelope itself, not a stand-in (Task 3's note):
        # the conductor's gate passes --subject <plan envelope>.
        plan_env = write_plan_envelope(root, plan=plan_payload())
        # max_repairs_per_task: 1 (the run-protocol worked example's budget), so T3's
        # second failing verify hits the cap and parks — the tool's own default is 2,
        # which would need a third failure to park; this matches the brief's "fails
        # twice and parks" by tightening the budget, not by asserting a different
        # default.
        write_grant(root, plan_env, budget={"max_repairs_per_task": 1})

        rc, out, err = self.out(["init", "--plan", plan_env, "--root", root])
        self.assertEqual(rc, 0, err)
        self.assertTrue(out.startswith("RUN: "), out)

        # Wave one: T2 waits on T1; T1 and T3 are both ready (parallel limit 2).
        rc, out, err = self.out(["next", "--root", root])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), "READY: T1 T3")

        rc, out, err = self.out(["start", "T1", "--root", root])
        self.assertEqual(rc, 0, err)
        wt1 = out.strip().split()[-1]
        rc, out, err = self.out(["start", "T3", "--root", root])
        self.assertEqual(rc, 0, err)
        wt3 = out.strip().split()[-1]

        # Every executor step commits real work, so a merge is never a no-commits park.
        commit_in(wt1, "t1.txt", "one\n")
        commit_in(wt3, "t3.txt", "three\n")

        rc, out, err = self.out(["verify", "T1", "--root", root])
        self.assertEqual(rc, 0, err)
        lines = out.strip().splitlines()
        self.assertTrue(lines[-1].startswith("VERIFY: T1 pass "), out)

        rc, out, err = self.out(["review", "T1", "--verdict", "pass", "--root", root])
        self.assertEqual((rc, out.strip()), (0, "REVIEW: T1 pass"), err)

        rc, out, err = self.out(["merge", "T1", "--root", root])
        self.assertEqual(rc, 0, err)
        self.assertTrue(out.strip().startswith("MERGE: T1 "), out)
        merge_sha_t1 = out.strip().split()[-1]

        # T3's verify fails once: a repair is dispatched (the cap is 1, so one is left).
        rc, out, err = self.out(["verify", "T3", "--root", root])
        self.assertEqual(rc, 3, err)
        self.assertIn("VERIFY: T3 fail", out)
        self.assertNotIn("PARK:", out)
        st = C.State.load(C.state_path(root))
        self.assertEqual(st.tasks["T3"]["status"], "verifying")
        self.assertEqual(st.tasks["T3"]["repairs"], 0)  # the first failure spends a repair

        # It fails again: at the cap, T3 parks instead of a second repair.
        rc, out, err = self.out(["verify", "T3", "--root", root])
        self.assertEqual(rc, 3, err)
        self.assertIn("VERIFY: T3 fail", out)
        self.assertIn("PARK: T3 verify_red_after_repairs", out)
        st = C.State.load(C.state_path(root))
        self.assertEqual((st.tasks["T3"]["status"], st.tasks["T3"]["park_reason"]),
                         ("parked", "verify_red_after_repairs"))

        # T1 is proven, so T2 is now ready.
        rc, out, err = self.out(["next", "--root", root])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), "READY: T2")

        rc, out, err = self.out(["start", "T2", "--root", root])
        self.assertEqual(rc, 0, err)
        wt2 = out.strip().split()[-1]
        commit_in(wt2, "t2.txt", "two\n")

        rc, out, err = self.out(["verify", "T2", "--root", root])
        self.assertEqual(rc, 0, err)
        rc, out, err = self.out(["review", "T2", "--verdict", "pass", "--root", root])
        self.assertEqual((rc, out.strip()), (0, "REVIEW: T2 pass"), err)
        rc, out, err = self.out(["merge", "T2", "--root", root])
        self.assertEqual(rc, 0, err)
        merge_sha_t2 = out.strip().split()[-1]

        rc, out, err = self.out(["next", "--root", root])
        self.assertEqual(rc, 3, err)
        self.assertEqual(out.strip(), "STOP: no_ready_tasks")

        # T3's verify never passed: never proven, and its content never reached the
        # run branch (only T1's and T2's merges did).
        st = C.State.load(C.state_path(root))
        self.assertEqual(st.tasks["T3"]["status"], "parked")
        self.assertNotEqual(st.tasks["T3"]["status"], "proven")
        self.assertFalse(os.path.exists(os.path.join(root, "t3.txt")))
        self.assertTrue(os.path.exists(os.path.join(root, "t1.txt")))
        self.assertTrue(os.path.exists(os.path.join(root, "t2.txt")))

        pr_cmd = [sys.executable, self.stub, self.record, "pr", "--title", "{title}",
                  "--base", "{base_branch}", "--body-file", "{body_file}"]
        with mock.patch.dict(os.environ, {"PATH": self.bin + os.pathsep + os.environ["PATH"]}):
            rc, out, err = self.out(["finish", "--root", root, "--pr-cmd", json.dumps(pr_cmd)])
        self.assertEqual(rc, 0, err)
        lines = [l for l in out.splitlines() if l.startswith("FINISH: ")]
        self.assertEqual(len(lines), 1, out)
        env_path = lines[0][len("FINISH: "):]
        with open(env_path, encoding="utf-8") as f:
            doc = json.load(f)

        self.assertEqual(doc["predicateType"], C.RUN_RESULT_KIND)
        self.assertEqual(CC.check_statement(doc), [])
        rep = CC.check_envelope(env_path, root=root)
        self.assertEqual((rep["violations"], rep["stale"]), ([], []))

        pay = doc["predicate"]["payload"]
        tasks = {t["id"]: t for t in pay["tasks"]}
        self.assertEqual(tasks["T1"]["status"], "proven")
        self.assertEqual(tasks["T2"]["status"], "proven")
        self.assertEqual(tasks["T3"]["status"], "parked")
        self.assertEqual(tasks["T3"]["park_reason"], "verify_red_after_repairs")
        self.assertEqual(tasks["T1"]["merge_commit"], merge_sha_t1)
        self.assertEqual(tasks["T2"]["merge_commit"], merge_sha_t2)

        asserts = {a["test"] for a in doc["predicate"]["assertions"]}
        self.assertEqual(asserts, {"verify:T1", "verify:T2"})

        with open(C.State.load(C.state_path(root)).log_path, "rb") as f:
            data = f.read()
        prefix = data[:pay["log_bytes"]]
        self.assertEqual(hashlib.sha256(prefix).hexdigest(), pay["log_sha256"])

        self.assertEqual([r["what"] for r in self.records()], ["push", "pr"])


if __name__ == "__main__":
    unittest.main()
