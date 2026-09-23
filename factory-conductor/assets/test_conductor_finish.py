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
        # the run branch goes out as an explicit, non-forced refspec (never remapped)
        self.assertEqual(recs[0]["argv"],
                         ["-u", "origin", "refs/heads/factory/p:refs/heads/factory/p"])
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
        rc, out = self.finish()
        self.assertEqual((rc, out.strip()), (0, "FINISH: %s" % path))

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
                     ["merge", "T1"], ["next"], ["resume"], ["park", "T3", "--reason", "r"],
                     ["decision", "T3", "--question", "q"]):
            with self.subTest(argv=argv):
                rc, out, err = self.out(argv + ["--root", self.root], err=True)
                self.assertEqual(rc, 2)
                self.assertIn("run finished", err)

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
