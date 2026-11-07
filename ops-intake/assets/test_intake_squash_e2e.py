"""R4 end to end: `intake sync` resolves a squash-merged item from the PR number that
factory-conductor's `finish` writes into its second run-result, with no owner `resolve`.

The flow, through each skill's real command line:
- ops-intake: init, import one GitHub issue, pick it;
- spec-first-planning: intake_request.py turns the pick envelope into a spec, spec_lint.py
  --unattended, spec_to_tasks.py --envelope (the plan names the item in intake_items),
  write_grant.py;
- factory-conductor: init, start, verify, review, merge and finish on a one-task plan, with
  the default PR command and a stub `gh` first on PATH that prints a PR URL for #42, and a
  local bare remote;
- the hosting side: a second clone squash-merges the run branch into its base branch with
  the subject ending `(#42)` and pushes; the repo fetches it;
- release-conductor: a verified release-result for that squash commit (see release_result);
- ops-intake: sync, the printed NEXT git rev-list line run as written, sync again.

The negative case runs the same flow with a stub `gh` that prints no PR URL, so finish
writes only the first run-result (no `pr`): the item stays planned+needs-resolve (D20).

Stdlib only and offline: temp git repos, a local bare remote, no network. No test here runs
`intake resolve`; every intake argv is recorded and checked."""
import hashlib, json, os, shlex, shutil, subprocess, sys, tempfile, time, types, unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import contract_check as CC  # noqa: E402

SKILLS = os.path.dirname(os.path.dirname(HERE))
INTAKE = os.path.join(HERE, "intake.py")
PLANNING = os.path.join(SKILLS, "spec-first-planning", "assets")
CONDUCTOR = os.path.join(SKILLS, "factory-conductor", "assets", "conductor.py")
RELEASE_DIR = os.path.join(SKILLS, "release-conductor", "assets")
NEEDED = [INTAKE, CONDUCTOR, os.path.join(RELEASE_DIR, "release.py")] + [
    os.path.join(PLANNING, n) for n in ("intake_request.py", "spec_lint.py",
                                        "spec_to_tasks.py", "write_grant.py")]
RUN_KIND = "https://github.com/dhanesh/agent-skills/skill-contract/run-result/v1"
PR = 42
VERSION = "1.2.0"
# The grant covers factory/* and never the default branch, so the owner plans on a
# factory/* base branch; the run's PR targets it.
BASE = "factory/fixes"
VERIFY_SKILL = ".claude/skills/verify-app"
# One feature-map entry in the shape verification-skill-forge writes (the same shape
# factory-conductor's evidence suite commits). Its anchor is the file the task changes.
FEATURE = """# login: login

- id: login
- proven: no
- anchors: app.txt

## What it is

The login button.

## How to reach it

The start page.

## Drive it

```sh
true
```

Expect exit 0.

## Proof

A click on login reaches the handler.

## Gotchas

None.
"""

# The stub `gh`: records its argv, then prints what `gh pr create` prints when the mode
# file says "url", and a line with no PR URL otherwise.
GH = r'''#!%s
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "gh-calls.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
with open(os.path.join(here, "gh-mode"), encoding="utf-8") as f:
    mode = f.read().strip()
if mode == "url":
    print("Creating pull request for the run branch\n")
    print("https://github.com/acme/widgets/pull/%d")
else:
    print("pull request queued")
'''

CONFIG = {"sources": {"github": {"enabled": True, "formats": ["gh-issues-json"]},
                      "release": {"enabled": True, "formats": ["release-envelope"]}}}

ISSUE = [{"number": 7, "title": "Login button does nothing", "body": "Click login: nothing.",
          "labels": [{"name": "bug"}], "state": "OPEN", "stateReason": "",
          "createdAt": "2026-10-01T00:00:00Z", "updatedAt": "2026-10-02T00:00:00Z",
          "closedAt": None, "url": "https://github.com/acme/widgets/issues/7",
          "author": {"login": "ann", "is_bot": False}}]

# Everything after the skeleton intake_request.py prints: a decision-closed spec (it passes
# spec_lint.py --unattended) with one requirement and one verify command.
SPEC_REST = """
## Problem
The login button does nothing.

## Users
- people who sign in

## Goals
- login works

## Non-goals
- new login methods

## Constraints
- B1 [invariant]: No account data is lost.
- T1 [boundary]: Login answers within 2 s.

## Required truths
- RT1 [SPECIFICATION_READY]: The button calls the login handler. (parent: OUTCOME; maps_to: B1; reqs: R1; confidence: 0.8; check: python3 -c pass)
- RT2 [SPECIFICATION_READY]: The handler answers fast. (parent: RT1; maps_to: T1; reqs: R1; confidence: 0.6; check: python3 -c pass)

## Requirements
- R1: The login button must call the login handler. [feature: login] [proof: a click on login reaches the handler] [parallel-safe]

## Acceptance criteria
- R1: run the login check, expect exit 0. [cmd: {python} -c pass]

## Open questions

## Tensions
- TN1 [trade_off]: Speed vs. care. (between: B1, T1; status: resolved; strategy: Partition)

## Solution options
- OPT-A: Wire the handler. (complexity: Low; reversibility: TWO_WAY; satisfies: RT1, RT2)
- OPT-B: Replace the button. (complexity: Medium; reversibility: TWO_WAY; satisfies: RT1)
Recommended: OPT-A - satisfies every RT at the lowest complexity.

## Verification
Verify skill: `.claude/skills/verify-app`

## Iterations
- I1: constrained, tensioned, anchored; chose OPT-A.

## Decisions
- D1: May the fix add a dependency? -> no (source: sweep)
"""


def utc(delta=timedelta()):
    return (datetime.now(timezone.utc) + delta).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def lines_with(out, prefix):
    return [l[len(prefix):] for l in out.splitlines() if l.startswith(prefix)]


@unittest.skipUnless(shutil.which("git"), "git not installed")
@unittest.skipUnless(sys.version_info >= (3, 10), "the vendored checker needs Python 3.10+")
@unittest.skipUnless(all(os.path.isfile(p) for p in NEEDED),
                     "the sibling skills are not next to ops-intake")
class SquashEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="intake-squash-e2e-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        gh = os.path.join(self.bin, "gh")
        with open(gh, "w") as f:
            f.write(GH % (sys.executable, PR))
        os.chmod(gh, 0o755)
        # The developer's git config never reaches these repos; the stub gh comes first.
        self.env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ.get("PATH", ""),
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        self.intake_argvs = []

    # -- runners ---------------------------------------------------------------------
    def sh(self, argv, cwd=None, stdin=None, ok=True):
        p = subprocess.run(argv, cwd=cwd or self.root, env=self.env, input=stdin,
                           capture_output=True, text=True, timeout=120)
        if ok:
            self.assertEqual(p.returncode, 0, "%s\n%s\n%s" % (argv, p.stdout, p.stderr))
        return p.returncode, p.stdout

    def git(self, *args, cwd=None):
        return self.sh(["git", "-c", "commit.gpgsign=false", *args], cwd=cwd)[1]

    def intake(self, *argv, stdin=None):
        self.intake_argvs.append(list(argv))
        return self.sh([sys.executable, INTAKE, "--root", self.root, *argv], stdin=stdin)[1]

    def planning(self, script, *argv):
        return self.sh([sys.executable, os.path.join(PLANNING, script), *argv])[1]

    def conductor(self, *argv):
        return self.sh([sys.executable, CONDUCTOR, *argv, "--root", self.root])[1]

    def write(self, rel, text):
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    # -- the flow --------------------------------------------------------------------
    def repo(self):
        """The project on main, pushed to a local bare origin. Intake's and the runs'
        local state stays out of git."""
        self.root = os.path.join(self.tmp, "project")
        self.bare = os.path.join(self.tmp, "origin.git")
        self.git("init", "-q", "--bare", self.bare, cwd=self.tmp)
        self.git("init", "-q", "-b", "main", self.root, cwd=self.tmp)
        for key, value in (("gc.auto", "0"), ("maintenance.auto", "false")):
            self.git("config", key, value)
        self.write("app.txt", "login: todo\n")
        # spec_lint --unattended needs a verify skill, so the plan runs under the
        # conductor's evidence gate.
        self.write(VERIFY_SKILL + "/SKILL.md", "---\nname: verify-app\ndescription: x\n---\n")
        self.write(VERIFY_SKILL + "/features/README.md", "- [login](login.md)\n")
        self.write(VERIFY_SKILL + "/features/login.md", FEATURE)
        with open(os.path.join(self.root, ".git", "info", "exclude"), "a") as f:
            f.write("/.intake/\n/.skill-contract/\n/.release/\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.git("remote", "add", "origin", self.bare)
        self.git("checkout", "-q", "-b", BASE)
        self.git("push", "-q", "origin", "main", BASE)

    def pick(self):
        """intake init, import one GitHub issue, pick it. Returns (item id, pick envelope)."""
        answers = os.path.join(self.tmp, "intake-answers.json")
        with open(answers, "w") as f:
            json.dump(CONFIG, f)
        self.intake("init", "--answers", answers)
        issues = os.path.join(self.tmp, "issues.json")
        with open(issues, "w") as f:
            json.dump(ISSUE, f)
        self.intake("import", "--format", "gh-issues-json", "--source", "github", issues)
        listed = lines_with(self.intake("list"), "ITEM: ")
        self.assertEqual(len(listed), 1, listed)
        iid = listed[0].split()[0]
        out = self.intake("pick", iid, "--by", "Dee")
        env = lines_with(out, "NEXT: run spec-first-planning with ")
        self.assertEqual(len(env), 1, out)
        return iid, os.path.join(self.root, *env[0].split("/"))

    def plan(self, iid, pick_env):
        """The spec from the pick envelope, its task plan and the owner's grant. Returns the
        plan envelope path."""
        skeleton = self.planning("intake_request.py", pick_env)
        self.write("docs/spec.md", skeleton + SPEC_REST)
        self.git("add", "docs/spec.md")
        self.git("commit", "-q", "-m", "spec")
        self.planning("spec_lint.py", "--unattended", "docs/spec.md")
        out = self.planning("spec_to_tasks.py", "docs/spec.md", "--envelope", self.root)
        self.assertIn("INTAKE: %s" % iid, out.splitlines())
        plan = lines_with(out, "ENVELOPE: ")[0]
        with open(plan, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["predicate"]["payload"]["intake_items"], [iid])
        answers = os.path.join(self.tmp, "grant-answers.json")
        with open(answers, "w") as f:
            json.dump({"branch_pattern": "factory/*", "expires_at": utc(timedelta(days=1)),
                       "gate_policy": {"read_only": "auto", "local_reversible": "grant",
                                       "push_branch": "grant", "open_pr": "grant"}}, f)
        out = self.planning("write_grant.py", "--root", self.root, "--spec", "docs/spec.md",
                            "--plan", plan, "--answers", answers, "--accepted-by", "Dana")
        self.assertTrue(lines_with(out, "GRANT: "), out)
        return plan

    def run_plan(self, plan, gh_mode):
        """conductor init .. finish on the one-task plan, with the default PR command and
        the stub gh. Returns the run branch and the task's merge commit."""
        with open(os.path.join(self.bin, "gh-mode"), "w") as f:
            f.write(gh_mode)
        self.conductor("init", "--plan", plan)
        self.assertEqual(self.conductor("next").strip(), "READY: T1")
        wt = self.conductor("start", "T1", "--owner", "writer").split(" ", 2)[2].strip()
        with open(os.path.join(wt, "app.txt"), "w") as f:
            f.write("login: calls the handler\n")
        self.git("commit", "-q", "-am", "Wire the login handler", cwd=wt)
        sha = self.conductor("verify", "T1").split()[-1]
        self.conductor("review", "T1", "--verdict", "pass", "--reviewer", "reviewer")
        self.evidence(sha)
        self.assertEqual(self.conductor("evidence", "T1", "--verifier", "verifier").strip(),
                         "EVIDENCE: T1 pass %s" % sha)
        merge = self.conductor("merge", "T1").strip().split()[-1]
        out = self.conductor("finish")  # no --pr-cmd: the default `gh pr create ...`
        with open(os.path.join(self.bin, "gh-calls.jsonl")) as f:
            calls = [json.loads(l) for l in f]
        self.assertEqual([c[:2] for c in calls], [["pr", "create"]])
        self.finish_out = out
        branch = self.git("rev-parse", "--abbrev-ref", "HEAD").strip()
        self.assertTrue(branch.startswith("factory/"), branch)
        return branch, merge

    def evidence(self, sha, instance="a"):
        """The verifier's evidence for `sha`, in the verify-evidence/v1 records that
        verification-skill-forge's recorder writes (as factory-conductor's evidence suite
        writes them): one evidence record with its artifact, and a green doctor."""
        def put(rel, doc):
            path = os.path.join(self.root, ".verify", instance, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(doc if isinstance(doc, str) else json.dumps(doc))
        capture = '{"status": 200}\n'
        put("login/%s/capture.json" % sha, capture)
        put("login/%s/evidence.json" % sha, {
            "schema": "verify-evidence/v1", "kind": "evidence", "instance": instance,
            "feature": "login", "sha": sha, "verifier": "verifier", "result": "pass",
            "action": "click login", "observed": "the handler ran", "side_effects": ["none"],
            "artifacts": [{"path": "capture.json",
                           "sha256": hashlib.sha256(capture.encode()).hexdigest()}],
            "captured_at": utc()})
        put("doctor/%s/doctor.json" % sha, {
            "schema": "verify-evidence/v1", "kind": "doctor", "instance": instance, "sha": sha,
            "ok": True, "checks": {"port": "pass"}, "verifier": "verifier",
            "captured_at": utc()})

    def squash_merge(self, branch):
        """The hosting side squash-merges the pushed run branch into BASE, with a subject
        that ends with `(#42)`, and pushes BASE; the project fetches it. Returns the squash
        commit, which is not the run's merge commit."""
        host = os.path.join(self.tmp, "host")
        self.git("clone", "-q", "-b", BASE, self.bare, host, cwd=self.tmp)
        self.git("merge", "-q", "--squash", "origin/" + branch, cwd=host)
        self.git("commit", "-q", "-m", "Fix the login button (#%d)" % PR,
                 "-m", "* Wire the login handler", cwd=host)
        self.git("push", "-q", "origin", BASE, cwd=host)
        self.git("fetch", "-q", "origin", BASE)
        return self.git("rev-parse", "origin/" + BASE).strip()

    def release_result(self, commit):
        """A verified release-result/v1 for `commit`. Driving release-conductor's prep, stage,
        deploy and verify-prod needs a staging server and a verifier's evidence, so this
        builds the payload and the statement with release.py's own producer functions
        (result_payload and _result_statement, over a stand-in release record) and writes it
        with the checker's envelope writer, as release.py's write_result does."""
        sys.path.append(RELEASE_DIR)
        try:
            import release as RL
            from test_release_prod import SCHEMA, schema_problems
        finally:
            sys.path.remove(RELEASE_DIR)
        recipe = self.write(RL.RECIPE_PATH, "{}\n")
        base = "%s/%s" % (RL.RELEASES_DIR, VERSION)
        self.write("%s/%s" % (base, RL.INTENT_FILE), json.dumps({"version": VERSION}) + "\n")
        log = self.write("%s/release-log.jsonl" % base,
                         json.dumps({"event": "verified", "commit": commit}) + "\n")
        rel = types.SimpleNamespace(
            version=VERSION, release_commit=commit, recipe_sha=CC.sha256_file(recipe),
            artifact_sha=None, rollback_target=None, approved_by={"name": "Dana"},
            rollback=None,
            evidence={"verdict": "pass", "verifier": "verifier-2", "commit": commit,
                      "features": ["login"], "paths": []},
            stage={"probe": {"rc": 0, "polls": 1}, "checks": [{"rc": 0}],
                   "deploy_prod": {"rc": 0}},
            prod={"probe": {"rc": 0, "polls": 1, "live": True}, "health": {"rc": 0},
                  "smoke": [{"rc": 0}], "failure": None})
        payload = RL.result_payload(rel, "verified")
        with open(log, "rb") as f:
            data = f.read()
        payload["log_sha256"], payload["log_bytes"] = hashlib.sha256(data).hexdigest(), len(data)
        with open(SCHEMA, encoding="utf-8") as f:
            schema = json.load(f)
        self.assertEqual(schema_problems(payload, schema, schema), [])
        path = CC.write_envelope(self.root, RL._result_statement(self.root, rel, payload))
        rep = CC.check_envelope(path, root=self.root)
        self.assertEqual((rep["violations"], rep["stale"]), ([], []))
        return path

    def sync_following_next(self):
        """intake sync, the printed NEXT git rev-list line run as a user would, sync again."""
        out = self.intake("sync")
        nxt = lines_with(out, "NEXT: git rev-list ")
        self.assertEqual(len(nxt), 1, out)
        left, right = ("git rev-list " + nxt[0]).split(" | intake ")
        hist = self.git(*shlex.split(left)[1:])
        self.intake(*shlex.split(right), stdin=hist)
        return self.intake("sync")

    def run_results(self):
        d = CC.envelope_dir(self.root)
        docs = []
        for name in sorted(os.listdir(d)):
            with open(os.path.join(d, name), encoding="utf-8") as f:
                doc = json.load(f)
            if doc.get("predicateType") == RUN_KIND:
                docs.append(doc)
        return sorted(docs, key=lambda d: (d["predicate"]["generatedAtTime"],
                                           d["predicate"]["id"]))

    def flow(self, gh_mode):
        self.repo()
        iid, pick_env = self.pick()
        plan = self.plan(iid, pick_env)
        branch, merge = self.run_plan(plan, gh_mode)
        squash = self.squash_merge(branch)
        self.assertNotEqual(squash, merge)
        # The run's own merge commit never reaches BASE: only the squash commit does.
        self.assertNotIn(merge, self.git("rev-list", squash).split())
        # A release comes after the run. finish may stamp its second run-result a second
        # ahead of the first, so wait until the clock is past the newest one.
        newest = self.run_results()[-1]["predicate"]["generatedAtTime"]
        for _ in range(50):
            if utc() > newest:
                break
            time.sleep(0.1)
        self.assertGreater(utc(), newest)
        self.release_result(squash)
        return iid, self.sync_following_next()

    def item(self, iid):
        out = self.intake("show", iid)
        return out.splitlines()[0].split()[2]

    def log(self):
        with open(os.path.join(self.root, ".skill-contract", "intake", "intake-log.jsonl")) as f:
            return [json.loads(l) for l in f]

    def assert_no_owner_resolve(self):
        self.assertNotIn("resolve", [a[0] for a in self.intake_argvs])
        self.assertFalse([r for r in self.log() if r.get("event") == "resolved"
                          and not str(r.get("by", "")).startswith("release:")])

    # -- the cases -------------------------------------------------------------------
    def test_a_squash_merge_of_the_runs_pr_resolves_with_no_resolve(self):
        iid, out = self.flow("url")
        runs = self.run_results()
        self.assertEqual(len(runs), 2)
        self.assertNotIn("pr", runs[0]["predicate"]["payload"])
        self.assertEqual(runs[1]["predicate"]["payload"]["pr"], {"number": PR})
        self.assertEqual(runs[1]["predicate"]["wasRevisionOf"], runs[0]["predicate"]["id"])
        self.assertIn("planned=0 resolved=1 ", out)
        self.assertEqual(self.item(iid), "resolved")
        with open(os.path.join(self.root, ".skill-contract", "intake", "queue.json"),
                  encoding="utf-8") as f:
            queue = json.load(f)
        it = queue["items"][iid]
        self.assertEqual((it["state"], it["state_by"]), ("resolved", "release:" + VERSION))
        self.assertIn({"event": "resolved", "id": iid, "by": "release:" + VERSION, "pr": PR},
                      [{k: v for k, v in r.items() if k != "at"} for r in self.log()])
        self.assert_no_owner_resolve()

    def test_the_first_run_result_alone_leaves_it_needing_resolve(self):
        iid, out = self.flow("none")
        self.assertIn("planned=1 resolved=0 ", out)
        runs = self.run_results()
        self.assertEqual(len(runs), 1)
        self.assertNotIn("pr", runs[0]["predicate"]["payload"])
        self.assertEqual(self.item(iid), "planned+needs-resolve")
        self.assert_no_owner_resolve()


if __name__ == "__main__":
    unittest.main()
