"""finish: the run-result/v1 envelope, then push and PR when the grant covers them."""
import contextlib, hashlib, io, json, os, re, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C
import contract_check as CC
from conductor_testkit import GIT, repo, new_run, revoke, tmpdir, write_grant, write_plan_envelope

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
sys.exit(1 if "broken-remote" in rest else 0)
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
        self.tmp = tmpdir()
        self.stub = os.path.join(self.tmp, "stub.py")
        with open(self.stub, "w") as f:
            f.write(STUB)
        self.record = os.path.join(self.tmp, "record.jsonl")
        # A `git` on PATH that records `git push ...` and passes everything else to git,
        # so the push command stays a real `git push` argv the allowlist accepts.
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        wrapper = os.path.join(self.bin, "git")
        with open(wrapper, "w") as f:
            f.write('#!/bin/sh\nif [ "$1" = push ]; then shift; exec "%s" "%s" "%s" push "$@"; fi\n'
                    'exec "%s" "$@"\n' % (sys.executable, self.stub, self.record,
                                          shutil.which("git")))
        os.chmod(wrapper, 0o755)

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

    def out(self, argv, err=False):
        buf, ebuf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(ebuf):
            rc = C.main(argv)
        return (rc, buf.getvalue(), ebuf.getvalue()) if err else (rc, buf.getvalue())

    PR = None

    def finish(self, extra=(), push=None, pr=None):
        """finish with the recording `git` wrapper on PATH and a stub PR command."""
        argv = ["finish", "--root", self.root,
                "--pr-cmd", json.dumps(pr or [sys.executable, self.stub, self.record, "pr",
                                              "--title", "{title}", "--base", "{base_branch}",
                                              "--body-file", "{body_file}"])]
        if push is not None:
            argv += ["--push-cmd", json.dumps(push)]
        with mock.patch.dict(os.environ, {"PATH": self.bin + os.pathsep + os.environ["PATH"]}):
            return self.out(argv + list(extra))

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
        head = doc["predicate"]["payload"]["integration"]["head"]
        # verify:T1, then the integration re-run on the merged head (G3)
        self.assertEqual([a["test"] for a in asserts], ["verify:T1", "integration:" + head[:12]])
        self.assertEqual(asserts[1]["command"], FIRST_T1)
        a = asserts[0]
        self.assertEqual(a["assertedBy"], {"skill": "factory-conductor"})
        self.assertEqual(a["result"], {"outcome": "passed"})
        self.assertEqual(a["command"], FIRST_T1)
        plan_rel = os.path.relpath(os.path.realpath(self.plan),
                                   os.path.realpath(self.root)).replace(os.sep, "/")
        self.assertEqual(a["subject"], [CC.pin(self.root, plan_rel)])
        # A receiver that re-runs the assertion proves it (commandment 7).
        rep = CC.check_envelope(path, root=self.root, rerun=True)
        self.assertEqual(rep["claims"], {"verify:T1": "PROVEN",
                                         "integration:" + head[:12]: "PROVEN"})

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
        # the run branch goes out as an explicit, non-forced refspec (never remapped), its
        # source pinned to the head the integration re-run verified (C1)
        head = C.State.load(self.st_path).finished["integration"]["head"]
        self.assertEqual(recs[0]["argv"],
                         ["-u", "origin", "%s:refs/heads/factory/p" % head])
        self.assertEqual(recs[1]["argv"][:4], ["--title", "Demo plan", "--base", "main"])
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
        rc, out = self.finish(push=["git", "push", "broken-remote", "{run_branch}"])
        self.assertEqual(rc, 3)
        self.envelope(out)
        self.assertEqual([r["what"] for r in self.records()], ["push"])
        ev = [e for e in self.log_events() if e["event"] == "push"]
        self.assertEqual(ev[-1]["returncode"], 1)

    def test_push_commands_outside_the_allowlist_are_refused(self):
        self.run_plan()
        bad = [["git", "push", "--force", "origin", "{run_branch}"],
               ["git", "push", "-f", "origin", "{run_branch}"],
               ["git", "push", "-uf", "origin", "{run_branch}"],
               ["git", "push", "--force-with-lease", "origin", "{run_branch}"],
               ["git", "push", "origin", ":main"],
               ["git", "push", "origin", "HEAD:main"],
               ["git", "push", "origin", "{run_branch}:main"],
               ["git", "push", "origin", "+{run_branch}"],
               ["git", "push", "--all", "origin"],
               ["git", "push", "--tags", "origin", "{run_branch}"],
               ["git", "push", "--prune", "origin", "{run_branch}"],
               ["git", "push", "--mirror", "origin"],
               ["git", "push", "--delete", "origin", "{run_branch}"],
               ["git", "push", "--no-verify", "origin", "{run_branch}"],
               ["git", "push", "origin", "{run_branch}", "main"],
               ["git", "push", "origin", "main"],
               ["git", "push", "{run_branch}"],
               ["git", "push", "--", "origin", "{run_branch}"],
               ["sh", "-c", "git push -f"],
               ["/usr/bin/git", "push", "origin", "{run_branch}"],
               ["git", "-c", "x=y", "push", "origin", "{run_branch}"]]
        for cmd in bad:
            with self.subTest(cmd=cmd):
                rc, out = self.finish(push=cmd)
                self.assertEqual(rc, 2)
                self.assertNotIn("FINISH:", out)
        self.assertEqual(self.records(), [])

    def test_a_push_refspec_in_the_shared_config_cannot_retarget_or_force(self):
        """N1: an executor can write remote.origin.push into the shared .git/config; a bare
        `git push origin <rb>` would map through it (and its + would force)."""
        self.run_plan()
        bare = tmpdir()
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        subprocess.run(["git", "-C", self.root, "remote", "add", "origin", bare], check=True)
        subprocess.run(["git", "-C", self.root, "push", "-q", "origin", "main"], check=True)
        main_before = C.git(bare, "rev-parse", "refs/heads/main").stdout.strip()
        subprocess.run(["git", "-C", self.root, "config", "remote.origin.push",
                        "+refs/heads/factory/p:refs/heads/main"], check=True)
        head = C.git(self.root, "rev-parse", "HEAD").stdout.strip()
        rc, out = self.out(["finish", "--root", self.root, "--pr-cmd", json.dumps(
            [sys.executable, self.stub, self.record, "pr", "--body-file", "{body_file}"])])
        self.assertEqual(rc, 0, out)
        self.assertEqual(C.git(bare, "rev-parse", "refs/heads/main").stdout.strip(),
                         main_before)
        self.assertEqual(C.git(bare, "rev-parse", "refs/heads/factory/p").stdout.strip(), head)

    def test_an_ext_url_planted_for_the_push_never_runs(self):
        """Deferred 1: the push runs with GIT_ALLOW_PROTOCOL=file:git:http:https:ssh, so a
        url.<ext::...>.insteadOf planted with protocol.ext.allow=always is refused."""
        self.run_plan()
        bare = os.path.join(self.tmp, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        subprocess.run(["git", "-C", self.root, "remote", "add", "origin", bare], check=True)
        mark = os.path.join(self.tmp, "ext-ran")
        subprocess.run(["git", "-C", self.root, "config", "protocol.ext.allow", "always"],
                       check=True)
        subprocess.run(["git", "-C", self.root, "config",
                        "url.ext::sh -c touch%% %s%% #.insteadOf" % mark, bare], check=True)
        rc, out = self.out(["finish", "--root", self.root, "--pr-cmd", json.dumps(
            [sys.executable, self.stub, self.record, "pr", "--body-file", "{body_file}"])])
        self.assertFalse(os.path.exists(mark), "the planted ext:: command ran")
        self.assertEqual(rc, 3, out)
        self.assertFalse(C.State.load(self.st_path).finished["pushed"])

    def test_a_run_branch_that_changes_ci_config_is_not_pushed(self):
        """I1 (the reviewer's probe2): a task commits .github/workflows/x.yml and goes
        through verify, review and merge; finish asks on push_branch with ci-config
        (CI runs with the repository's secrets, so that push is deploy) and exits 3,
        and nothing is pushed or opened."""
        self.root = repo()
        bare = os.path.join(self.tmp, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        subprocess.run(["git", "-C", self.root, "remote", "add", "origin", bare], check=True)
        subprocess.run(["git", "-C", self.root, "push", "-q", "origin", "main"], check=True)
        st, self.plan = new_run(self.root, {
            "title": "CI", "spec": "docs/spec.md", "coverage": {}, "uncovered": [],
            "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "ci",
                       "verify": [{"text": "ok", "command": ["true"]}], "depends_on": []}]})
        self.st_path = st.state_path
        self.assertEqual(C.main(["start", "T1", "--root", self.root]), 0)
        wt = C.State.load(self.st_path).tasks["T1"]["worktree"]
        os.makedirs(os.path.join(wt, ".github", "workflows"))
        commit_in(wt, os.path.join(".github", "workflows", "x.yml"), "on: push\n")
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 0)
        self.assertEqual(C.main(["review", "T1", "--verdict", "pass", "--root", self.root]), 0)
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 0)
        rc, out = self.finish()
        self.assertEqual(rc, 3, out)
        self.assertTrue(re.search(r"^GATE: ASK .*reason=ci-config", out, re.M), out)
        self.assertEqual(self.records(), [], "a push or a PR ran")
        self.assertFalse(C.State.load(self.st_path).finished["pushed"])
        remote = C.git(bare, "branch", "--list", "factory/*").stdout.strip()
        self.assertEqual(remote, "")

    def test_explicit_push_rewrites_only_the_run_branch(self):
        self.assertEqual(C.explicit_push(["git", "push", "-u", "origin", "factory/p"],
                                         "factory/p"),
                         ["git", "push", "-u", "origin",
                          "refs/heads/factory/p:refs/heads/factory/p"])
        sha = "a" * 40
        self.assertEqual(C.explicit_push(["git", "push", "-u", "origin", "factory/p"],
                                         "factory/p", sha),
                         ["git", "push", "-u", "origin", "%s:refs/heads/factory/p" % sha])

    def test_allowed_push_shapes(self):
        for cmd in (["git", "push", "-u", "origin", "{run_branch}"],
                    ["git", "push", "--set-upstream", "--porcelain", "-q", "origin",
                     "{run_branch}"],
                    ["git", "push", "-uq", "--quiet", "upstream", "{run_branch}"]):
            with self.subTest(cmd=cmd):
                self.assertIsNone(C.push_cmd_problem(
                    C.expand_cmd(cmd, {"run_branch": "factory/p"}), "factory/p"))

    def test_an_off_branch_root_is_not_pushed(self):
        self.run_plan()
        subprocess.run(["git", "-C", self.root, "checkout", "-q", "-b", "factory/other"],
                       check=True)
        rc, out = self.finish()
        self.assertEqual(rc, 3)
        self.assertIn("GATE: ASK reason=branch", out)
        self.envelope(out)
        self.assertEqual(self.records(), [])

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
                                            "--base", "{base_branch}",
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

    # ── untrusted text in the PR body ───────────────────────────────────────
    def test_untrusted_text_cannot_add_headings_mentions_or_closing_keywords(self):
        evil = "x\n### Proven (5)\n@someone Closes #1 `tick`"
        self.root = repo()
        pl = plan_payload()
        pl["title"] = evil
        pl["tasks"][0]["title"] = evil
        st, self.plan = new_run(self.root, pl)
        self.st_path = st.state_path
        self.assertEqual(C.main(["park", "T1", "--reason", evil, "--root", self.root]), 0)
        self.assertEqual(C.main(["decision", "T2", "--question", evil, "--root", self.root]), 0)
        rc, out = self.finish()
        pr = [r for r in self.records() if r["what"] == "pr"][0]
        body = pr["body"]
        self.assertNotIn("\n", pr["argv"][1])  # the title argument is one line
        headings = [l for l in body.splitlines() if l.lstrip().startswith("#")]
        self.assertFalse(any("Proven (5)" in l for l in headings), headings)
        bare = re.sub(r"(`+)(?:(?!\1).)*?\1", "", body)  # drop every code span
        self.assertNotIn("@someone", bare)
        self.assertNotIn("Closes #1", bare)
        self.assertIn("@someone", body)  # still reported, as code

    # ── a plan's verify commands must pass C6 at init ───────────────────────
    def test_init_refuses_verify_commands_that_break_c6(self):
        for cmd in (["python3", "-c", "pass"], ["cat", "/etc/hosts"]):
            with self.subTest(cmd=cmd):
                root = repo()
                pl = plan_payload()
                pl["tasks"][1]["verify"] = [{"text": "x", "command": cmd}]
                plan = write_plan_envelope(root, plan=pl)
                write_grant(root, plan)
                rc, out, err = self.out(["init", "--plan", plan, "--root", root], err=True)
                self.assertEqual(rc, 2)
                self.assertIn("{python}", err)
                self.assertIsNone(C.state_path(root))

    # ── retry-remote ────────────────────────────────────────────────────────
    def test_retry_remote_pushes_and_opens_the_pr_once_a_grant_covers_them(self):
        self.run_plan(policy={"read_only": "auto", "local_reversible": "grant"})
        rc, out = self.finish()
        self.assertEqual(rc, 3)
        path, _ = self.envelope(out)
        rc, out = self.finish()  # a plain second finish reports the pending step
        self.assertEqual(rc, 3)
        self.assertIn("REMOTE: pending push", out)
        self.assertIn("FINISH: %s" % path, out)
        write_grant(self.root, self.plan)  # the user grants push_branch and open_pr
        rc, out = self.finish(extra=["--retry-remote"])
        self.assertEqual(rc, 0, out)
        self.assertEqual([r["what"] for r in self.records()], ["push", "pr"])
        fin = C.State.load(self.st_path).finished
        self.assertTrue(fin["pushed"])
        self.assertTrue(fin["pr"])
        self.assertEqual(len([n for n in os.listdir(C.CC.envelope_dir(self.root))
                              if n.startswith("run-result")]), 1)
        rc, out = self.finish(extra=["--retry-remote"])  # nothing left to do
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.records()), 2)
        rc, out = self.finish()  # the recorded integration line, then FINISH: (M8)
        head = C.State.load(self.st_path).finished["integration"]["head"]
        self.assertEqual((rc, out.strip().splitlines()),
                         (0, ["INTEGRATION: pass %s" % head, "FINISH: %s" % path]))

    def test_retry_remote_skips_a_push_that_already_ran(self):
        self.run_plan(policy={"read_only": "auto", "local_reversible": "grant",
                              "push_branch": "grant"})
        rc, out = self.finish()
        self.assertEqual(rc, 3)
        rc, out = self.finish()
        self.assertIn("REMOTE: pending pr", out)
        write_grant(self.root, self.plan)
        rc, out = self.finish(extra=["--retry-remote"])
        self.assertEqual(rc, 0, out)
        self.assertEqual([r["what"] for r in self.records()], ["push", "pr"])

    def test_retry_remote_refuses_an_envelope_changed_since_finish(self):
        self.run_plan(policy={"read_only": "auto", "local_reversible": "grant"})
        rc, out = self.finish()
        path, _ = self.envelope(out)
        self.assertEqual(C.State.load(self.st_path).finished["sha256"], CC.sha256_file(path))
        with open(path, "a") as f:
            f.write(" ")
        write_grant(self.root, self.plan)
        rc, out = self.finish(extra=["--retry-remote"])
        self.assertEqual(rc, 2)
        self.assertEqual(self.records(), [])

    def test_retry_remote_needs_a_finished_run(self):
        self.run_plan()
        rc, out = self.finish(extra=["--retry-remote"])
        self.assertEqual(rc, 2)
        self.assertNotIn("FINISH:", out)

    # ── finish and in-flight tasks ──────────────────────────────────────────
    def test_finish_refuses_while_a_task_is_in_flight(self):
        self.root = repo()
        st, self.plan = new_run(self.root, plan_payload())
        self.st_path = st.state_path
        self.assertEqual(C.main(["start", "T1", "--root", self.root]), 0)
        rc, out, err = self.out(["finish", "--root", self.root], err=True)
        self.assertEqual(rc, 2)
        self.assertIn("T1", err)
        self.assertNotIn("FINISH:", out)

    def test_a_stopped_run_parks_in_flight_tasks_and_finishes(self):
        self.root = repo()
        st, self.plan = new_run(self.root, plan_payload(), budget={"wall_clock_min": 1})
        self.st_path = st.state_path
        self.assertEqual(C.main(["start", "T1", "--root", self.root]), 0)
        st = C.State.load(self.st_path)
        st.created_at = "2000-01-01T00:00:00Z"
        st.save()
        rc, out = self.out(["next", "--root", self.root])
        self.assertIn("STOP: budget_wall_clock", out)
        rc, out = self.finish()
        self.assertEqual(rc, 0, out)
        self.assertIn("PARK: T1 in_flight_at_stop", out)
        st = C.State.load(self.st_path)
        self.assertEqual((st.tasks["T1"]["status"], st.tasks["T1"]["park_reason"]),
                         ("parked", "in_flight_at_stop"))
        _, doc = self.envelope(out)
        t1 = [t for t in doc["predicate"]["payload"]["tasks"] if t["id"] == "T1"][0]
        self.assertEqual((t1["status"], t1["park_reason"]), ("parked", "in_flight_at_stop"))
        parks = [e for e in self.log_events() if e["event"] == "park"]
        self.assertEqual([(e["task"], e["reason"]) for e in parks],
                         [("T1", "in_flight_at_stop")])

    def test_a_finished_run_refuses_further_steps(self):
        self.run_plan(stop=False)
        rc, out = self.finish()
        self.assertEqual(rc, 0, out)
        for argv in (["start", "T3"], ["verify", "T1"], ["review", "T1", "--verdict", "pass"],
                     ["merge", "T1"], ["next"], ["park", "T3", "--reason", "r"],
                     ["decision", "T3", "--question", "q"]):
            with self.subTest(argv=argv):
                rc, out, err = self.out(argv + ["--root", self.root], err=True)
                self.assertEqual(rc, 2)
                self.assertIn("run finished", err)
        # resume reports instead (I4): the envelope, and that nothing is left to do
        rc, out = self.out(["resume", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines()[-1], "NEXT: run done")

    def test_payload_verify_commands_keep_the_plan_form(self):
        self.run_plan()
        rc, out = self.finish()
        _, doc = self.envelope(out)
        t1 = [t for t in doc["predicate"]["payload"]["tasks"] if t["id"] == "T1"][0]
        self.assertEqual([v["command"] for v in t1["verify"]], [FIRST_T1, ["true"]])

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
        head = C.State.load(self.st_path).finished["integration"]["head"]
        self.assertEqual(out2.strip().splitlines(),
                         ["INTEGRATION: pass %s" % head, "FINISH: %s" % path])
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



# Each task adds one file to d/, which holds base.txt: alone, each sees exactly two files;
# merged, the run branch holds three, so both tasks' own checks fail there.
EXACTLY_TWO = ["{python}", "-c", "import os,sys; sys.exit(0 if len(os.listdir('d')) == 2 else 1)"]
AT_LEAST_TWO = ["{python}", "-c", "import os,sys; sys.exit(0 if len(os.listdir('d')) >= 2 else 1)"]


def pair_plan(cmd):
    """T1 and T2, independent, each proven by `cmd`."""
    def task(tid):
        return {"id": tid, "requirement_ids": ["R1"], "title": "task " + tid,
                "verify": [{"text": "d has its files", "command": cmd}], "depends_on": []}
    return {"title": "Pair plan", "spec": "docs/spec.md", "coverage": {}, "uncovered": [],
            "tasks": [task("T1"), task("T2")]}


@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class IntegrationTests(unittest.TestCase):
    """G3: finish re-runs every proven task's verify commands on the merged run-branch
    head before any remote step, and never pushes a merged result that fails them."""

    setUp = FinishTests.setUp
    out = FinishTests.out
    finish = FinishTests.finish
    envelope = FinishTests.envelope
    records = FinishTests.records
    log_events = FinishTests.log_events

    def run_pair(self, cmd):
        """T1 and T2 both started from the same run branch, each proven alone, both
        merged; the run stopped by `next` (no_ready_tasks)."""
        self.root = repo()
        os.makedirs(os.path.join(self.root, "d"))
        commit_in(self.root, os.path.join("d", "base.txt"), "base\n")
        st, self.plan = new_run(self.root, pair_plan(cmd))
        self.st_path = st.state_path
        for t in ("T1", "T2"):
            self.assertEqual(C.main(["start", t, "--root", self.root]), 0)
        for t in ("T1", "T2"):
            commit_in(C.State.load(self.st_path).tasks[t]["worktree"],
                      os.path.join("d", "%s.txt" % t.lower()), t + "\n")
        for t in ("T1", "T2"):
            rc, out = self.out(["verify", t, "--root", self.root])
            self.assertEqual(rc, 0, out)  # each passes alone
            self.assertEqual(C.main(["review", t, "--verdict", "pass", "--root", self.root]), 0)
        for t in ("T1", "T2"):
            self.assertEqual(C.main(["merge", t, "--root", self.root]), 0)
        rc, out = self.out(["next", "--root", self.root])
        self.assertIn("STOP: no_ready_tasks", out)
        return C.git(self.root, "rev-parse", "refs/heads/factory/p").stdout.strip()

    def test_tasks_that_break_each_other_are_never_pushed(self):
        head = self.run_pair(EXACTLY_TWO)
        with mock.patch.dict(os.environ, {"PATH": self.bin + os.pathsep + os.environ["PATH"]}):
            rc, out, err = self.out(["finish", "--root", self.root, "--pr-cmd", json.dumps(
                [sys.executable, self.stub, self.record, "pr"])], err=True)
        self.assertEqual(rc, 3, err)
        lines = out.splitlines()
        shown = " ".join(EXACTLY_TWO)
        self.assertIn("INTEGRATION: fail T1 %s" % shown, lines)
        self.assertIn("INTEGRATION: fail T2 %s" % shown, lines)
        self.assertEqual(lines[-1], "STOP: integration_red")
        self.assertEqual(self.records(), [])  # no push, no PR
        self.assertNotIn("push", [e["event"] for e in self.log_events()])
        path, doc = self.envelope(out)
        rep = CC.check_envelope(path, root=self.root)
        self.assertEqual((rep["violations"], rep["stale"]), ([], []))
        pay = doc["predicate"]["payload"]
        self.assertEqual(pay["integration"]["head"], head)
        self.assertIs(pay["integration"]["passed"], False)
        self.assertEqual(pay["integration"]["runs"], [
            {"task": "T1", "command": EXACTLY_TWO, "ok": False, "returncode": 1},
            {"task": "T2", "command": EXACTLY_TWO, "ok": False, "returncode": 1}])
        self.assertEqual(pay["stopped"]["reason"], "integration_red")
        self.assertEqual(pay["stopped"]["previous"], "no_ready_tasks")
        a = [x for x in doc["predicate"]["assertions"] if x["test"].startswith("integration:")]
        self.assertEqual([x["test"] for x in a], ["integration:%s" % head[:12]])
        self.assertEqual(a[0]["result"], {"outcome": "failed"})
        self.assertEqual(a[0]["command"], EXACTLY_TWO)
        self.assertEqual(rep["claims"]["integration:%s" % head[:12]], "FAILED")
        st = C.State.load(self.st_path)
        self.assertEqual(st.stopped["reason"], "integration_red")
        self.assertIs(st.finished["integration"]["passed"], False)

    def test_the_merged_result_passing_prints_pass_and_pushes(self):
        head = self.run_pair(AT_LEAST_TWO)
        rc, out = self.finish()
        self.assertEqual(rc, 0, out)
        self.assertIn("INTEGRATION: pass %s" % head, out.splitlines())
        self.assertNotIn("STOP:", out)
        self.assertEqual([r["what"] for r in self.records()], ["push", "pr"])
        self.assertIn("Integration: every proven task's verify commands re-ran on the merged "
                      "run branch `%s`: passed." % head[:12], self.records()[1]["body"])
        _, doc = self.envelope(out)
        pay = doc["predicate"]["payload"]
        self.assertEqual(pay["integration"], {"head": head, "passed": True, "runs": [
            {"task": "T1", "command": AT_LEAST_TWO, "ok": True, "returncode": 0},
            {"task": "T2", "command": AT_LEAST_TWO, "ok": True, "returncode": 0}]})
        self.assertEqual(pay["stopped"]["reason"], "no_ready_tasks")
        a = {x["test"]: x for x in doc["predicate"]["assertions"]}
        self.assertEqual(a["integration:%s" % head[:12]]["result"], {"outcome": "passed"})

    def test_the_integration_event_is_logged_before_finish_and_inside_the_digest(self):
        self.run_pair(EXACTLY_TWO)
        rc, out = self.finish()
        _, doc = self.envelope(out)
        pay = doc["predicate"]["payload"]
        with open(C.State.load(self.st_path).log_path, "rb") as f:
            prefix = f.read()[:pay["log_bytes"]]
        events = [json.loads(l) for l in prefix.splitlines()]
        names = [e["event"] for e in events]
        self.assertEqual(names[-1], "finish")
        self.assertLess(names.index("integration"), names.index("finish"))
        ev = events[names.index("integration")]
        self.assertEqual((ev["head"], ev["passed"]), (pay["integration"]["head"], False))
        stops = [e for e in events if e["event"] == "stop"]
        self.assertEqual(stops[-1]["reason"], "integration_red")

    def test_a_second_finish_reads_the_recorded_result_and_does_not_rerun(self):
        self.run_pair(EXACTLY_TWO)
        rc, out = self.finish()
        self.assertEqual(rc, 3)
        n = [e["event"] for e in self.log_events()].count("integration")
        rc2, out2 = self.finish()
        self.assertEqual(rc2, 3)
        self.assertEqual([l for l in out2.splitlines() if l.startswith(("INTEGRATION:", "STOP:",
                                                                        "FINISH:"))],
                         [l for l in out.splitlines() if l.startswith(("INTEGRATION:", "STOP:",
                                                                       "FINISH:"))])
        self.assertNotIn("REMOTE:", out2)
        self.assertEqual([e["event"] for e in self.log_events()].count("integration"), n)
        self.assertEqual(self.records(), [])

    def test_retry_remote_refuses_a_run_whose_integration_failed(self):
        self.run_pair(EXACTLY_TWO)
        self.finish()
        rc, out, err = self.out(["finish", "--root", self.root, "--retry-remote"], err=True)
        self.assertEqual(rc, 2)
        self.assertIn("integration", err)
        self.assertEqual(self.records(), [])

    def test_a_passing_integration_is_not_rerun_either(self):
        self.run_pair(AT_LEAST_TWO)
        self.finish()
        n = [e["event"] for e in self.log_events()].count("integration")
        rc, out = self.finish()
        self.assertEqual(rc, 0)
        self.assertEqual([e["event"] for e in self.log_events()].count("integration"), n)

    def test_the_integration_checkout_is_removed(self):
        self.run_pair(AT_LEAST_TWO)
        self.finish()
        vdir = os.path.join(C.State.load(self.st_path).dir, C.VERIFY_DIR)
        self.assertEqual(os.listdir(vdir) if os.path.isdir(vdir) else [], [])

    def test_integration_red_is_a_stop_reason_in_the_tool_and_the_schema(self):
        self.assertIn("integration_red", C.STOP_REASONS)
        with open(os.path.join(HERE, "schemas", "run-result.v1.json"), encoding="utf-8") as f:
            schema = json.load(f)
        stopped = schema["properties"]["stopped"]["oneOf"][1]
        self.assertIn("integration_red", stopped["properties"]["reason"]["enum"])
        self.assertIn("integration", schema["required"])
        self.assertEqual(sorted(schema["properties"]["integration"]["required"]),
                         ["head", "passed", "runs"])



LOCAL_ONLY = {"read_only": "auto", "local_reversible": "grant", "push_branch": "ask",
              "open_pr": "ask"}


@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class ReviewFixTests(unittest.TestCase):
    """Review of the Q3 commits: the push is pinned to the verified head (C1), the
    integration re-run is gated (I2), a passing re-run restores the earlier stop (M3),
    budget_derived is in the payload (M5)."""

    setUp = FinishTests.setUp
    run_plan = FinishTests.run_plan
    out = FinishTests.out
    finish = FinishTests.finish
    envelope = FinishTests.envelope
    records = FinishTests.records
    log_events = FinishTests.log_events

    def st(self):
        return C.State.load(self.st_path)

    # C1
    def test_a_branch_moved_before_retry_remote_is_not_pushed(self):
        self.run_plan(policy=LOCAL_ONLY)
        rc, out = self.finish()
        self.assertEqual(rc, 3)
        self.assertIn("GATE: ASK", out)
        verified = self.st().finished["integration"]["head"]
        commit_in(self.root, "late.txt", "moved\n")  # the run branch moves after integration
        write_grant(self.root, self.plan)  # now push and PR are covered
        rc, out, err = self.out(["finish", "--root", self.root, "--retry-remote", "--pr-cmd",
                                 json.dumps([sys.executable, self.stub, self.record, "pr"])],
                                err=True)
        self.assertEqual(rc, 2, out)
        self.assertIn("moved since the integration re-run", err)
        self.assertIn(verified, err)
        self.assertEqual(self.records(), [])
        self.assertFalse(self.st().finished["pushed"])

    def test_a_branch_moved_between_integration_and_the_first_push_is_not_pushed(self):
        self.run_plan()
        real = C._gate_logged

        def moving_gate(st, action):
            if action == "push_branch":
                commit_in(self.root, "late.txt", "moved\n")
            return real(st, action)

        with mock.patch.object(C, "_gate_logged", moving_gate):
            rc, out, err = self.out(["finish", "--root", self.root, "--pr-cmd", json.dumps(
                [sys.executable, self.stub, self.record, "pr"])], err=True)
        self.assertEqual(rc, 2, out)
        self.assertIn("moved since the integration re-run", err)
        self.assertEqual(self.records(), [])

    def test_the_real_push_sends_exactly_the_verified_head(self):
        self.run_plan()
        bare = tmpdir()
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        subprocess.run(["git", "-C", self.root, "remote", "add", "origin", bare], check=True)
        rc, out = self.out(["finish", "--root", self.root, "--pr-cmd", json.dumps(
            [sys.executable, "-c", "pass"])])
        self.assertEqual(rc, 0, out)
        self.assertEqual(C.git(bare, "rev-parse", "refs/heads/factory/p").stdout.strip(),
                         self.st().finished["integration"]["head"])

    # I2
    def test_an_expired_grant_at_finish_runs_no_integration_command_and_never_pushes(self):
        self.run_plan()
        write_grant(self.root, self.plan, minutes=-5)  # the newest grant is expired
        with mock.patch.object(C, "_run_steps", side_effect=AssertionError("ran a command")):
            rc, out = self.finish()
        self.assertEqual(rc, 3, out)
        lines = out.splitlines()
        self.assertTrue(any(l.startswith("GATE: ASK") and "expired" in l for l in lines), out)
        self.assertIn("INTEGRATION: skipped grant_ask", lines)
        self.assertEqual(lines[-1], "STOP: grant_ask")
        self.assertEqual(self.records(), [])
        path, doc = self.envelope(out)
        rep = CC.check_envelope(path, root=self.root)
        self.assertEqual((rep["violations"], rep["stale"]), ([], []))
        pay = doc["predicate"]["payload"]
        integ = pay["integration"]
        self.assertEqual((integ["passed"], integ["runs"]), (False, []))
        self.assertIn("expired", integ["skipped"])
        self.assertEqual(pay["stopped"]["reason"], "grant_ask")
        self.assertEqual(pay["stopped"]["previous"], "no_ready_tasks")
        a = [x for x in doc["predicate"]["assertions"] if x["test"].startswith("integration:")]
        self.assertEqual([x["result"] for x in a], [{"outcome": "untested"}])
        self.assertEqual(a[0]["command"], FIRST_T1)
        gates = [e for e in self.log_events() if e["event"] == "gate"]
        self.assertEqual((gates[-1]["action"], gates[-1]["ok"]), ("local_reversible", False))
        # a second finish reads the recorded result, and --retry-remote while the grant
        # still asks re-runs nothing; nothing is pushed (SkippedIntegrationRetryTests
        # covers the retry once a new grant exists)
        rc, out2 = self.finish()
        self.assertEqual((rc, out2.splitlines()[-1]), (3, "STOP: grant_ask"))
        with mock.patch.object(C, "_run_steps", side_effect=AssertionError("ran a command")):
            rc, out3 = self.finish(extra=["--retry-remote"])
        self.assertEqual((rc, out3.splitlines()[-1]), (3, "STOP: grant_ask"))
        self.assertEqual(self.records(), [])

    # M3
    def test_a_passing_rerun_after_a_recorded_red_restores_the_earlier_stop(self):
        self.run_plan()
        st = self.st()
        prior = dict(st.stopped)
        # a crash after the red integration was recorded but before finish was
        st.stopped = {"reason": "integration_red", "at": "t", "detail": "T1 x",
                      "previous": prior["reason"], "prior": prior}
        st.save()
        rc, out = self.finish()
        self.assertEqual(rc, 0, out)
        _, doc = self.envelope(out)
        self.assertEqual(doc["predicate"]["payload"]["stopped"]["reason"], "no_ready_tasks")
        self.assertNotIn("previous", doc["predicate"]["payload"]["stopped"])
        self.assertEqual(self.st().stopped, prior)

    def test_a_passing_rerun_clears_a_red_that_had_no_earlier_stop(self):
        self.run_plan(stop=False)
        st = self.st()
        st.stopped = {"reason": "integration_red", "at": "t", "detail": "T1 x",
                      "previous": None, "prior": None}
        st.save()
        rc, out = self.finish()
        self.assertEqual(rc, 0, out)
        _, doc = self.envelope(out)
        self.assertIsNone(doc["predicate"]["payload"]["stopped"])
        self.assertIsNone(self.st().stopped)

    # M5
    def test_the_payload_carries_budget_derived(self):
        self.run_plan()
        rc, out = self.finish()
        _, doc = self.envelope(out)
        self.assertEqual(doc["predicate"]["payload"]["budget_derived"], [])
        with open(os.path.join(HERE, "schemas", "run-result.v1.json"), encoding="utf-8") as f:
            schema = json.load(f)
        self.assertIn("budget_derived", schema["required"])
        self.assertEqual(schema["properties"]["budget_derived"]["type"], "array")
        integ = schema["properties"]["integration"]["properties"]
        self.assertEqual(integ["skipped"]["type"], "string")



@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class SkippedIntegrationRetryTests(unittest.TestCase):
    """Ruled fix: an integration skipped because the grant lapsed does not strand the run.
    `finish --retry-remote` re-gates, re-runs integration on the current head, writes a
    new envelope (the old one is kept as finished.superseded) and pushes that head."""

    setUp = FinishTests.setUp
    run_plan = FinishTests.run_plan
    out = FinishTests.out
    finish = FinishTests.finish
    envelope = FinishTests.envelope
    records = FinishTests.records
    log_events = FinishTests.log_events

    def st(self):
        return C.State.load(self.st_path)

    def skipped(self):
        self.run_plan()
        write_grant(self.root, self.plan, minutes=-5)  # the grant lapses
        rc, out = self.finish()
        self.assertEqual(rc, 3, out)
        self.assertIn("INTEGRATION: skipped grant_ask", out.splitlines())
        old, _ = self.envelope(out)
        return old

    def test_a_new_grant_lets_retry_remote_verify_write_a_new_envelope_and_push(self):
        old = self.skipped()
        write_grant(self.root, self.plan)  # a new grant is issued
        head = C.git(self.root, "rev-parse", "refs/heads/factory/p").stdout.strip()
        rc, out = self.finish(extra=["--retry-remote"])
        self.assertEqual(rc, 0, out)
        lines = out.splitlines()
        self.assertIn("INTEGRATION: pass %s" % head, lines)
        new, doc = self.envelope("\n".join(l for l in lines if l != "FINISH: %s" % old))
        self.assertNotEqual(new, old)
        self.assertTrue(os.path.isfile(old))  # never overwritten
        rep = CC.check_envelope(new, root=self.root)
        self.assertEqual((rep["violations"], rep["stale"]), ([], []))
        pay = doc["predicate"]["payload"]
        self.assertEqual((pay["integration"]["head"], pay["integration"]["passed"]), (head, True))
        self.assertNotIn("skipped", pay["integration"])
        # the new digest covers the new integration event
        with open(self.st().log_path, "rb") as f:
            prefix = f.read()[:pay["log_bytes"]]
        self.assertEqual(hashlib.sha256(prefix).hexdigest(), pay["log_sha256"])
        events = [json.loads(l) for l in prefix.splitlines()]
        self.assertEqual([e["event"] for e in events][-1], "finish")
        self.assertEqual([e["passed"] for e in events if e["event"] == "integration"],
                         [False, True])
        fin = self.st().finished
        self.assertEqual((fin["envelope"], fin["superseded"]), (new, old))
        self.assertEqual(fin["integration"]["head"], head)
        recs = self.records()
        self.assertEqual([r["what"] for r in recs], ["push", "pr"])
        self.assertEqual(recs[0]["argv"], ["-u", "origin", "%s:refs/heads/factory/p" % head])
        # a second --retry-remote is idempotent: nothing re-runs, nothing new is written
        n_env = len(os.listdir(CC.envelope_dir(self.root)))
        with open(self.st().log_path, "rb") as f:
            log = f.read()
        rc, out2 = self.finish(extra=["--retry-remote"])
        self.assertEqual(rc, 0, out2)
        self.assertIn("FINISH: %s" % new, out2.splitlines())
        self.assertEqual(len(os.listdir(CC.envelope_dir(self.root))), n_env)
        with open(self.st().log_path, "rb") as f:
            self.assertEqual(f.read(), log)
        self.assertEqual(len(self.records()), 2)

    def test_retry_remote_while_the_grant_still_asks_stops_with_grant_ask(self):
        old = self.skipped()
        n_env = len(os.listdir(CC.envelope_dir(self.root)))
        with mock.patch.object(C, "_run_steps", side_effect=AssertionError("ran a command")):
            rc, out = self.finish(extra=["--retry-remote"])
        self.assertEqual(rc, 3, out)
        self.assertEqual(out.splitlines()[-1], "STOP: grant_ask")
        self.assertIn("GATE: ASK", out)
        self.assertEqual(len(os.listdir(CC.envelope_dir(self.root))), n_env)
        self.assertEqual(self.st().finished["envelope"], old)
        self.assertEqual(self.records(), [])

    def test_a_red_rerun_writes_its_envelope_and_retry_then_refuses(self):
        old = self.skipped()
        write_grant(self.root, self.plan)
        # the re-run on the current head fails T1's own check
        with mock.patch.object(C, "_run_steps", return_value=[
                {"command": ["x"], "ok": False, "returncode": 1, "stdout_tail": "",
                 "stderr_tail": ""}]):
            rc, out = self.finish(extra=["--retry-remote"])
        self.assertEqual(rc, 3, out)
        self.assertEqual(out.splitlines()[-1], "STOP: integration_red")
        self.assertNotEqual(self.st().finished["envelope"], old)
        self.assertEqual(self.records(), [])
        rc, out = self.finish(extra=["--retry-remote"])
        self.assertEqual(rc, 2)
        self.assertEqual(self.records(), [])

    def test_resume_on_a_skipped_run_says_ask_or_finish_by_the_grant(self):
        self.skipped()
        rc, out = self.out(["resume", "--root", self.root])
        self.assertEqual((rc, out.splitlines()[-1]), (0, "NEXT: run ask"))
        write_grant(self.root, self.plan)
        rc, out = self.out(["resume", "--root", self.root])
        self.assertEqual((rc, out.splitlines()[-1]), (0, "NEXT: run finish"))


if __name__ == "__main__":
    unittest.main()
