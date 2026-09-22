"""finish: the run-result/v1 envelope, then push and PR when the grant covers them."""
import contextlib, hashlib, io, json, os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C
import contract_check as CC
from conductor_testkit import GIT, repo, new_run, revoke

HERE = os.path.dirname(os.path.abspath(__file__))
FIRST_T1 = ["{python}", "-c", "pass"]
STUB = r'''
import json, sys
record, what, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
entry = {"what": what, "argv": rest}
if "--body-file" in rest:
    with open(rest[rest.index("--body-file") + 1], encoding="utf-8") as f:
        entry["body"] = f.read()
with open(record, "a", encoding="utf-8") as f:
    f.write(json.dumps(entry) + "\n")
print("stub %s done" % what)
'''


def plan_payload():
    """T1 proves; T2 is parked by hand; T3 depends on T2, so it ends blocked."""
    def task(tid, deps, verify):
        return {"id": tid, "requirement_ids": ["R1"], "title": "task " + tid,
                "verify": verify, "depends_on": deps}
    return {"title": "Demo plan", "spec": "docs/spec.md", "coverage": {}, "uncovered": [],
            "tasks": [task("T1", [], [{"text": "passes", "command": FIRST_T1},
                                      {"text": "true", "command": ["true"]}]),
                      task("T2", [], [{"text": "ok", "command": ["true"]}]),
                      task("T3", ["T2"], [{"text": "ok", "command": ["true"]}])]}


def commit_in(d, name, text):
    with open(os.path.join(d, name), "w") as f:
        f.write(text)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-q", "-m", "c"], check=True,
                   env=dict(os.environ, **GIT))


@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class FinishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.stub = os.path.join(self.tmp, "stub.py")
        with open(self.stub, "w") as f:
            f.write(STUB)
        self.record = os.path.join(self.tmp, "record.jsonl")

    def run_plan(self, policy=None, stop=True):
        """T1 proven and merged, T2 parked, T3 blocked; the run stopped by `next`."""
        self.root = repo()
        st, self.plan = new_run(self.root, plan_payload(), policy=policy)
        self.st_path = st.state_path
        self.assertEqual(C.main(["start", "T1", "--root", self.root]), 0)
        commit_in(C.State.load(self.st_path).tasks["T1"]["worktree"], "b.txt", "b\n")
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 0)
        self.assertEqual(C.main(["review", "T1", "--verdict", "pass", "--root", self.root]), 0)
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 0)
        self.assertEqual(C.main(["park", "T2", "--reason", "needs a human", "--root",
                                 self.root]), 0)
        if stop:
            rc, out = self.out(["next", "--root", self.root])
            self.assertIn("STOP: no_ready_tasks", out)
        return C.State.load(self.st_path)

    def out(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = C.main(argv)
        return rc, buf.getvalue()

    def finish(self, extra=()):
        return self.out(["finish", "--root", self.root,
                         "--push-cmd", json.dumps([sys.executable, self.stub, self.record,
                                                   "push", "{run_branch}"]),
                         "--pr-cmd", json.dumps([sys.executable, self.stub, self.record,
                                                 "pr", "--title", "{title}",
                                                 "--body-file", "{body_file}"]),
                         *extra])

    def envelope(self, out):
        lines = [l for l in out.splitlines() if l.startswith("FINISH: ")]
        self.assertEqual(len(lines), 1, out)
        path = lines[0][len("FINISH: "):]
        with open(path, encoding="utf-8") as f:
            return path, json.load(f)

    def records(self):
        if not os.path.exists(self.record):
            return []
        with open(self.record, encoding="utf-8") as f:
            return [json.loads(l) for l in f]

    def log_events(self):
        with open(C.State.load(self.st_path).log_path, encoding="utf-8") as f:
            return [json.loads(l) for l in f]

    # ── the envelope ────────────────────────────────────────────────────────
    def test_the_envelope_validates_and_pins_the_plan_and_the_grant(self):
        st = self.run_plan()
        rc, out = self.finish()
        self.assertEqual(rc, 0, out)
        path, doc = self.envelope(out)
        self.assertEqual(doc["predicateType"], C.RUN_RESULT_KIND)
        self.assertEqual(CC.check_statement(doc), [])
        rep = CC.check_envelope(path, root=self.root)
        self.assertEqual((rep["violations"], rep["stale"]), ([], []))
        plan_rel = os.path.relpath(os.path.realpath(self.plan),
                                   os.path.realpath(self.root)).replace(os.sep, "/")
        grant_rel = ".skill-contract/envelopes/%s.json" % st.grant_id
        subjects = {s["name"]: s["digest"]["sha256"] for s in doc["subject"]}
        self.assertEqual(subjects, {plan_rel: CC.sha256_file(self.plan),
                                    grant_rel: CC.sha256_file(os.path.join(self.root,
                                                                           grant_rel))})
        pay = doc["predicate"]["payload"]
        self.assertEqual(pay["plan"]["sha256"], st.plan_sha256)
        self.assertEqual(pay["grant"]["id"], st.grant_id)
        self.assertEqual(pay["run_id"], st.run_id)
        self.assertEqual(pay["run_branch"], "factory/p")
        self.assertEqual(pay["stopped"]["reason"], "no_ready_tasks")
        self.assertEqual(doc["predicate"]["wasAttributedTo"]["skill"], "factory-conductor")

    def test_log_sha256_matches_the_log_on_disk_up_to_the_finish_event(self):
        self.run_plan()
        rc, out = self.finish()
        _, doc = self.envelope(out)
        pay = doc["predicate"]["payload"]
        with open(C.State.load(self.st_path).log_path, "rb") as f:
            data = f.read()
        prefix = data[:pay["log_bytes"]]
        self.assertEqual(hashlib.sha256(prefix).hexdigest(), pay["log_sha256"])
        last = json.loads(prefix.splitlines()[-1])
        self.assertEqual((last["event"], last["id"]), ("finish", doc["predicate"]["id"]))
        # the append-only log still starts with exactly those bytes after push and PR
        self.assertTrue(data.startswith(prefix))

    def test_one_assertion_per_proven_task_in_the_plans_own_command_form(self):
        self.run_plan()
        rc, out = self.finish()
        path, doc = self.envelope(out)
        asserts = doc["predicate"]["assertions"]
        self.assertEqual([a["test"] for a in asserts], ["verify:T1"])
        a = asserts[0]
        self.assertEqual(a["assertedBy"], {"skill": "factory-conductor"})
        self.assertEqual(a["result"], {"outcome": "passed"})
        self.assertEqual(a["command"], FIRST_T1)
        plan_rel = os.path.relpath(os.path.realpath(self.plan),
                                   os.path.realpath(self.root)).replace(os.sep, "/")
        self.assertEqual(a["subject"], [CC.pin(self.root, plan_rel)])
        # A receiver that re-runs the assertion proves it (commandment 7).
        rep = CC.check_envelope(path, root=self.root, rerun=True)
        self.assertEqual(rep["claims"], {"verify:T1": "PROVEN"})

    def test_parked_and_blocked_tasks_carry_their_reasons_and_no_assertion(self):
        self.run_plan()
        rc, out = self.finish()
        _, doc = self.envelope(out)
        tasks = {t["id"]: t for t in doc["predicate"]["payload"]["tasks"]}
        self.assertEqual(tasks["T1"]["status"], "proven")
        self.assertTrue(tasks["T1"]["merge_commit"])
        self.assertEqual(tasks["T1"]["review"]["verdict"], "pass")
        self.assertEqual([v["ok"] for v in tasks["T1"]["verify"]], [True, True])
        self.assertEqual((tasks["T2"]["status"], tasks["T2"]["park_reason"]),
                         ("parked", "needs a human"))
        self.assertEqual(tasks["T3"]["status"], "blocked")
        tests = {a["test"] for a in doc["predicate"]["assertions"]}
        self.assertNotIn("verify:T2", tests)
        self.assertNotIn("verify:T3", tests)

    def test_the_payload_has_every_key_the_schema_requires(self):
        self.run_plan()
        rc, out = self.finish()
        _, doc = self.envelope(out)
        with open(os.path.join(HERE, "schemas", "run-result.v1.json"), encoding="utf-8") as f:
            schema = json.load(f)
        self.assertEqual(schema["$id"], C.RUN_RESULT_KIND)
        pay = doc["predicate"]["payload"]
        self.assertEqual(sorted(set(schema["required"]) - set(pay)), [])
        item = schema["properties"]["tasks"]["items"]
        for t in pay["tasks"]:
            self.assertEqual(sorted(set(item["required"]) - set(t)), [])
            self.assertIn(t["status"], item["properties"]["status"]["enum"])
        self.assertIn("not enforced", pay["budget_note"])

    # ── push and PR ─────────────────────────────────────────────────────────
    def test_a_covering_grant_pushes_then_opens_the_pr_and_logs_both(self):
        self.run_plan()
        rc, out = self.finish()
        self.assertEqual(rc, 0, out)
        recs = self.records()
        self.assertEqual([r["what"] for r in recs], ["push", "pr"])
        self.assertEqual(recs[0]["argv"], ["factory/p"])
        self.assertEqual(recs[1]["argv"][:2], ["--title", "Demo plan"])
        ev = [e for e in self.log_events() if e["event"] in ("push", "pr")]
        self.assertEqual([(e["event"], e["returncode"]) for e in ev], [("push", 0), ("pr", 0)])
        self.assertIn("stub push done", ev[0]["stdout_tail"])
        self.assertIn("stub pr done", ev[1]["stdout_tail"])
        gates = [e["action"] for e in self.log_events() if e["event"] == "gate"]
        self.assertEqual(gates[-2:], ["push_branch", "open_pr"])

    def test_without_push_branch_the_envelope_is_written_and_nothing_is_pushed(self):
        self.run_plan(policy={"read_only": "auto", "local_reversible": "grant"})
        rc, out = self.finish()
        self.assertEqual(rc, 3)
        self.assertIn("GATE: ASK", out)
        path, _ = self.envelope(out)
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(self.records(), [])
        # the ASK is logged; the stop that ended the run stays the one on record
        self.assertEqual(C.State.load(self.st_path).stopped["reason"], "no_ready_tasks")

    def test_push_covered_but_open_pr_not_pushes_and_skips_the_pr(self):
        self.run_plan(policy={"read_only": "auto", "local_reversible": "grant",
                              "push_branch": "grant"})
        rc, out = self.finish()
        self.assertEqual(rc, 3)
        self.assertIn("GATE: ASK", out)
        self.assertEqual([r["what"] for r in self.records()], ["push"])

    def test_a_failing_push_skips_the_pr_and_exits_3(self):
        self.run_plan()
        rc, out = self.out(["finish", "--root", self.root,
                            "--push-cmd", json.dumps(["false"]),
                            "--pr-cmd", json.dumps([sys.executable, self.stub, self.record,
                                                    "pr"])])
        self.assertEqual(rc, 3)
        self.envelope(out)
        self.assertEqual(self.records(), [])
        ev = [e for e in self.log_events() if e["event"] == "push"]
        self.assertEqual(ev[-1]["returncode"], 1)

    def test_a_force_push_command_is_refused(self):
        self.run_plan()
        rc, out = self.out(["finish", "--root", self.root,
                            "--push-cmd", json.dumps(["git", "push", "--force", "origin",
                                                      "{run_branch}"])])
        self.assertEqual(rc, 2)
        self.assertNotIn("FINISH:", out)

    def test_a_malformed_command_is_a_usage_error(self):
        self.run_plan()
        rc, out = self.out(["finish", "--root", self.root, "--pr-cmd", "not json"])
        self.assertEqual(rc, 2)
        rc, out = self.out(["finish", "--root", self.root, "--push-cmd", "[]"])
        self.assertEqual(rc, 2)
        self.assertNotIn("FINISH:", out)

    def test_the_default_commands(self):
        self.assertEqual(C.DEFAULT_PUSH_CMD, ["git", "push", "-u", "origin", "{run_branch}"])
        self.assertEqual(C.DEFAULT_PR_CMD, ["gh", "pr", "create", "--title", "{title}",
                                            "--body-file", "{body_file}"])
        self.assertEqual(C.expand_cmd(C.DEFAULT_PUSH_CMD, {"run_branch": "factory/p"}),
                         ["git", "push", "-u", "origin", "factory/p"])

    def test_the_pr_body_reports_proven_parked_blocked_stop_and_budget(self):
        self.run_plan()
        rc, out = self.finish()
        body = [r for r in self.records() if r["what"] == "pr"][0]["body"]
        self.assertIn("Proven: 1 of 3", body)
        self.assertIn("T1", body)
        self.assertIn("T2", body)
        self.assertIn("needs a human", body)
        self.assertIn("T3", body)
        self.assertIn("no_ready_tasks", body)
        self.assertIn("max_tokens/max_usd recorded, not enforced", body)

    # ── after a stop, leftovers, idempotence ────────────────────────────────
    def test_finish_works_after_a_grant_ask_stop(self):
        self.run_plan(stop=False)
        revoke(self.root)
        rc, out = self.out(["resume", "--root", self.root])  # a gated step: it asks
        self.assertEqual(rc, 3)
        self.assertEqual(C.State.load(self.st_path).stopped["reason"], "grant_ask")
        rc, out = self.finish()
        self.assertEqual(rc, 3)
        path, doc = self.envelope(out)
        self.assertEqual(doc["predicate"]["payload"]["stopped"]["reason"], "grant_ask")
        self.assertIn("GATE: ASK", out)
        self.assertEqual(self.records(), [])
        self.assertEqual(CC.check_statement(doc), [])

    def test_a_merge_conflict_worktree_is_listed_and_kept(self):
        self.root = repo()
        pl = plan_payload()
        pl["tasks"] = pl["tasks"][:1]
        st, self.plan = new_run(self.root, pl)
        self.st_path = st.state_path
        C.main(["start", "T1", "--root", self.root])
        wt = C.State.load(self.st_path).tasks["T1"]["worktree"]
        commit_in(wt, "a.txt", "task\n")
        commit_in(self.root, "a.txt", "run\n")
        C.main(["verify", "T1", "--root", self.root])
        C.main(["review", "T1", "--verdict", "pass", "--root", self.root])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 3)
        rc, out = self.finish()
        _, doc = self.envelope(out)
        left = doc["predicate"]["payload"]["leftover_worktrees"]
        self.assertEqual([x["task"] for x in left], ["T1"])
        self.assertEqual(os.path.join(self.root, *left[0]["path"].split("/")), wt)
        self.assertTrue(os.path.isdir(wt))
        self.assertEqual(doc["predicate"]["assertions"], [])
        body = [r for r in self.records() if r["what"] == "pr"][0]["body"]
        self.assertIn(left[0]["path"], body)
        self.assertIn("merge-conflict", body)

    def test_finish_is_idempotent(self):
        self.run_plan()
        rc, out = self.finish()
        path, _ = self.envelope(out)
        n_env = len(os.listdir(CC.envelope_dir(self.root)))
        with open(C.State.load(self.st_path).log_path, "rb") as f:
            log = f.read()
        n_rec = len(self.records())
        rc, out2 = self.finish()
        self.assertEqual(rc, 0)
        self.assertEqual(out2.strip(), "FINISH: %s" % path)
        self.assertEqual(len(os.listdir(CC.envelope_dir(self.root))), n_env)
        with open(C.State.load(self.st_path).log_path, "rb") as f:
            self.assertEqual(f.read(), log)
        self.assertEqual(len(self.records()), n_rec)

    def test_a_plan_changed_since_init_is_refused(self):
        self.run_plan()
        with open(self.plan, "a") as f:
            f.write(" ")
        rc, out = self.finish()
        self.assertEqual(rc, 2)
        self.assertNotIn("FINISH:", out)
        self.assertEqual(self.records(), [])

    def test_run_result_payload_is_callable_on_a_state(self):
        st = self.run_plan()
        pay = C.run_result_payload(st)
        self.assertEqual([t["id"] for t in pay["tasks"]], ["T1", "T2", "T3"])
        self.assertIsNone(pay["log_sha256"])  # set by finish once the log is final


if __name__ == "__main__":
    unittest.main()
