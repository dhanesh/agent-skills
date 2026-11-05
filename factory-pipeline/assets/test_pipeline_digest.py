#!/usr/bin/env python3
"""The digest stage of `pipeline tick` (R21): every tick that reaches it writes
.skill-contract/pipeline/digest.md, which lists each human gate that waits (a pull request
to merge, a release grant to accept, a production deploy, a rollback, a plan waiting for a
read, an idea decision, the planner-tamper marker and the cap stops), each with the command
the owner runs next. The tick only shows these commands: it runs none of them (B1), and it
never calls gh or the network.

The release fixture runs the REAL `release prep` (pipeline_testkit.release_fixture), so its
release state and release grant are real. The factory-conductor runs, the release states
past prep and the incident records are fixture files in the formats that factory-conductor,
release-conductor and incident-watch write; the incident record's `rollback` field is made
by incident-watch's own rollback_view(). Every value that comes from those files is
untrusted: the injection tests plant newlines, backticks, a `# heading`, U+202E and a fake
`- [ ]` entry, and check that the digest's structure does not change.
Stdlib unittest; offline, deterministic; scratch only in temp dirs; needs git.
Run:  python3 test_pipeline_digest.py
"""
import contextlib
import datetime
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline as P  # noqa: E402
import pipeline_testkit as K  # noqa: E402

TOOL = os.path.join(HERE, "pipeline.py")
DIGEST = ".skill-contract/pipeline/digest.md"
INCIDENT_PY = os.path.join(os.path.dirname(os.path.dirname(HERE)), "incident-watch", "assets",
                           "incident.py")
HOSTILE = "evil`\n# heading\r\n- [ ] fake entry \u202e`x`\u2028 end"
SECTION_TITLES = {t for _k, t in P.DIGEST_SECTIONS}
# A gh and a curl that only log: a tick that called either would leave the log behind.
SPY = "#!/bin/sh\necho \"$0 $*\" >> %s\nexit 0\n"


def stages(out):
    return {ln.split()[1]: ln.split()[2:] for ln in out.splitlines() if ln.startswith("STAGE: ")}


def git(repo, *args):
    return K.release_git(repo, *args)


def read(repo, rel=DIGEST):
    with open(os.path.join(repo, *rel.split("/")), encoding="utf-8") as f:
        return f.read()


def section(text, title):
    """The lines of one `## <title> (<n>)` section, or None when it is not there."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if re.match(r"^## %s \([0-9]+\)$" % re.escape(title), ln):
            out = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("## "):
                    break
                out.append(nxt)
            return "\n".join(out)
    return None


def structure(text):
    """The digest's shape: each line's kind, with fixed headings kept whole."""
    shape = []
    for ln in text.splitlines():
        if ln.startswith("#"):
            shape.append(re.sub(r"\([0-9]+\)$", "(n)", ln))
        elif ln.startswith("- "):
            shape.append("entry")
        elif ln.startswith("  - "):
            shape.append("step:" + ln.split(":", 1)[0].strip())
        else:
            shape.append("text" if ln else "")
    return shape


def write_json(repo, rel, doc):
    return K.write_rel(repo, rel, json.dumps(doc, indent=1) + "\n")


run_state = K.finished_run
branch_ahead = K.branch_ahead


def load_incident():
    spec = importlib.util.spec_from_file_location("incident_watch_incident", INCIDENT_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def incident_record(repo, rid, releases, recipe=None, opened_at="2026-11-04T10:00:00Z"):
    """An open incident record whose `rollback` field is incident-watch's own view."""
    I = load_incident()
    view = I.rollback_view(recipe or K.release_recipe(), releases)
    write_json(repo, ".skill-contract/incidents/records/%s.json" % rid, {
        "id": rid, "status": "open", "opened_at": opened_at, "updated_at": opened_at,
        "consecutive_failures": 2, "probe_count": 2, "probes": [], "releases": releases,
        "rollback": view, "closed_at": None, "closed_by": None, "close_reason": None})
    return view


def bare_repo(d):
    repo = os.path.join(d, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    K.write_rel(repo, "README.md", "fixture\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixture")
    return repo


def bare_tick(repo, roots=()):
    tick = P.Tick(repo, list(roots))
    tick.grant = "pipeline-grant-v1-20261104T000000Z-abcdef"
    return tick


def digest_now(tick):
    """Run the digest stage in this process; return (result, stdout, digest text)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        res = P.stage_digest(tick)
    return res, out.getvalue(), read(tick.root)


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pipeline-digest-")
        self.addCleanup(shutil.rmtree, self.tmp, True)


class TestThreeGatesInOneTick(Scratch):
    """The R21 proof: a tick with an open run PR, a release grant that waits and a plan
    that waits for a read lists all three in the digest, each with its command."""

    def setUp(self):
        super().setUp()
        # The release PR is open, its grant is revoked, and run 1's PR is open.
        self.repo, self.skills, self.gid, self.run_url = K.digest_fixture(self.tmp)
        self.url = K.DIGEST_PR
        branch_ahead(self.repo, "factory/other")
        self.run_nourl = run_state(self.repo, 2, "factory/other", True)
        # A third run whose pull request is merged: its head is on main. Not listed.
        git(self.repo, "branch", "factory/done", "main")
        run_state(self.repo, 3, "factory/done", "https://github.com/acme/app/pull/5",
                  git(self.repo, "rev-parse", "main"))
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.spy_log = os.path.join(self.tmp, "spy.log")
        for name in ("gh", "curl", "wget"):
            path = os.path.join(self.bin, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(SPY % self.spy_log)
            os.chmod(path, 0o755)

    def tick(self):
        env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ.get("PATH", ""))
        cp = subprocess.run([sys.executable, TOOL, "--root", self.repo, "--skills-dir",
                             self.skills, "tick"], capture_output=True, text=True,
                            timeout=300, stdin=subprocess.DEVNULL, env=env)
        return cp, stages(cp.stdout)

    def test_open_run_pr_release_grant_and_plan_read_each_with_a_command(self):
        cp, res = self.tick()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        text = read(self.repo)
        n = text.count("\n- ")
        self.assertEqual(res["digest"], ["ok", "waiting=%d" % n], cp.stdout)
        self.assertIn("DIGEST: %s waiting=%d" % (DIGEST, n), cp.stdout)
        self.assertIn("Waiting: %d" % n, text)
        prs = section(text, "Pull requests to merge")
        self.assertIsNotNone(prs, text)
        self.assertIn("Run `%s` opened a pull request `%s`" % (self.run_url, self.url), prs)
        self.assertIn("  - review it, then merge: `gh pr merge %s`" % self.url, prs)
        # G3: the run result carries no PR number, so the digest names the command that
        # finds it from the run branch.
        self.assertIn("Run `%s` opened a pull request (the run records no number)"
                      % self.run_nourl, prs)
        self.assertIn("  - find it: `gh pr list --head factory/other --state open`", prs)
        self.assertNotIn("factory/done", text)
        # The release pull request is open too.
        self.assertIn("Release `1.2.0`: the release pull request", prs)
        self.assertIn("`gh pr list --head release/1.2.0 --state open`", prs)
        grants = section(text, "Release grants to accept")
        self.assertIsNotNone(grants, text)
        self.assertIn("Release `1.2.0`: the release grant `%s`" % self.gid, grants)
        self.assertRegex(grants, r"  - end it: `\S+ \S+release\.py abandon --root %s "
                                 r"--approved-by \"<your name>\" --reason \"<why>\"`"
                         % re.escape(self.repo))
        self.assertRegex(grants, r"  - then: `\S+ \S+release\.py prep --root %s "
                                 r"--approved-by \"<your name>\" --driver <agent-id>`"
                         % re.escape(self.repo))
        plans = section(text, "Plans waiting for a read")
        self.assertIsNotNone(plans, text)
        self.assertIn("Plan `.skill-contract/intake/envelopes/item-3.json` waits for the "
                      "owner's read: `no-planner`.", plans)
        self.assertRegex(plans, r"  - plan it with spec-first-planning: `\S+ \S+"
                                r"intake_request\.py \.skill-contract/intake/envelopes/"
                                r"item-3\.json`")
        # Every entry names at least one command.
        for block in text.split("\n- ")[1:]:
            self.assertRegex(block, r"\n  - [^:]+: `[^`]+`", block)
        # B1: the digest only shows commands: no gh, curl or wget ran, and the release
        # stage ran no release command (the grant is revoked, the PR is open).
        self.assertFalse(os.path.exists(self.spy_log), cp.stdout)
        self.assertEqual(K.release_calls(self.skills), [])

    def test_merged_run_pr_leaves_the_digest(self):
        git(self.repo, "merge", "-q", "--no-ff", "factory/demo", "-m", "merge run 1")
        git(self.repo, "branch", "-D", "factory/other")  # the owner cleaned it up
        cp, res = self.tick()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        text = read(self.repo)
        self.assertNotIn(self.run_url, text)
        self.assertNotIn(self.run_nourl, text)
        self.assertIn("Release `1.2.0`", text)


class TestOtherGateKinds(Scratch):
    """The production deploy, the rollback (release state and incident record) and the
    idea decision each appear with their command."""

    def setUp(self):
        super().setUp()
        self.repo = bare_repo(self.tmp)
        self.skills = os.path.join(self.tmp, "skills")
        self.release = K.release_stub(self.skills)
        # The owner's committed release recipe: the digest shows its rollback argv.
        K.write_rel(self.repo, ".release/recipe.json", json.dumps(K.release_recipe()) + "\n")

    def test_deploy_rollback_and_idea_decision(self):
        write_json(self.repo, ".skill-contract/releases/2.0.0/state.json",
                   {"version": "2.0.0", "status": "awaiting_deploy", "stage": {"x": 1}})
        write_json(self.repo, ".skill-contract/releases/1.9.0/state.json",
                   {"version": "1.9.0", "status": "prod_failed", "stage": {"x": 1}})
        rid = "inc-20261104T100000Z-abc123"
        view = incident_record(self.repo, rid,
                               [{"version": "1.9.0", "status": "prod_failed"}])
        tick = bare_tick(self.repo, [self.skills])
        tick.idea_list = (b"IDEA: shared-list GO decision=none `Shared list`\n"
                          b"IDEA: decided KILL decision=kill `Decided`\n"
                          b"IDEA: waiting PENDING decision=none `Waiting`\n"
                          b"IDEA: stalled STALLED decision=none closed `Closed`\n")
        (word, detail), out, text = digest_now(tick)
        self.assertEqual((word, detail), ("ok", "waiting=4"), text)
        rel = "%s %s" % (P.PYTHON, self.release)
        deploy = section(text, "Production deploys")
        self.assertIn("Release `2.0.0` is `awaiting_deploy`", deploy)
        self.assertIn("  - show the summary: `%s deploy --root %s`" % (rel, self.repo), deploy)
        self.assertIn("  - on the owner's yes: `%s deploy --root %s --approved-by "
                      "\"<your name>\"`" % (rel, self.repo), deploy)
        rollbacks = section(text, "Rollbacks")
        self.assertIn("Release `1.9.0` is `prod_failed`", rollbacks)
        self.assertIn("  - show the rollback: `%s rollback --root %s`" % (rel, self.repo),
                      rollbacks)
        # The incident record's own commands, word for word.
        self.assertIn("Incident `%s`" % rid, rollbacks)
        self.assertIn("The recipe's rollback is `%s`." % view["display"], rollbacks)
        self.assertIn("  - shows the rollback and waits: `%s`" % view["ask_owner"]["command"],
                      rollbacks)
        self.assertIn("  - on the owner's yes: `%s`" % view["ask_owner"]["on_owner_yes"],
                      rollbacks)
        self.assertNotIn("do not match", rollbacks)
        self.assertIn("  - after the fix: `incident close --by \"<your name>\" --id %s`" % rid,
                      rollbacks)
        ideas = section(text, "Idea decisions")
        self.assertIn("Idea `shared-list` has the computed verdict `GO` and no decision yet",
                      ideas)
        self.assertIn("  - the owner decides: `ideas decide shared-list --verdict go|kill|pivot "
                      "--by \"<your name>\" --reason \"<your words>\"`", ideas)
        for slug in ("decided", "waiting", "stalled"):
            self.assertNotIn("`%s`" % slug, text)
        self.assertEqual(K.release_calls(self.skills), [])  # no release command ran

    def test_outcome_unknown_checks_production_first(self):
        write_json(self.repo, ".skill-contract/releases/3.0.0/state.json",
                   {"version": "3.0.0", "status": "outcome_unknown"})
        (_w, _d), _out, text = digest_now(bare_tick(self.repo, [self.skills]))
        rollbacks = section(text, "Rollbacks")
        self.assertIn("  - check production first: `%s %s verify-prod --root %s`"
                      % (P.PYTHON, self.release, self.repo), rollbacks)

    def test_incident_that_cannot_act_shows_the_hand_rollback(self):
        rid = "inc-20261104T110000Z-def456"
        view = incident_record(self.repo, rid, [])
        (_w, _d), _out, text = digest_now(bare_tick(self.repo, [self.skills]))
        rollbacks = section(text, "Rollbacks")
        self.assertIn("release rollback cannot act", rollbacks)
        self.assertIn("  - the owner rolls back by hand (the recipe's rollback, word for word): "
                      "`%s`" % view["display"], rollbacks)

    def test_constants_are_incident_watch_s_own(self):
        I = load_incident()
        self.assertEqual((P.OWNER_ROLLBACK_ASK, P.OWNER_ROLLBACK_YES, P.ROLLBACK_ACTS_ON),
                         (I.OWNER_ROLLBACK_ASK, I.OWNER_ROLLBACK_YES, I.ROLLBACK_ACTS_ON))

    def test_hostile_incident_record_forges_no_command(self):
        """The review's case: a planted record whose ask_owner strings are commands."""
        write_json(self.repo, ".skill-contract/releases/1.9.0/state.json",
                   {"status": "prod_failed"})
        rid = "inc-20261104T130000Z-0d0e0f"
        incident_record(self.repo, rid, [{"version": "1.9.0", "status": "prod_failed"}])
        path = os.path.join(self.repo, ".skill-contract", "incidents", "records", rid + ".json")
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        evil = "evil $(touch %s)" % os.path.join(self.tmp, "PWN")
        rec["rollback"]["ask_owner"]["command"] = evil
        rec["rollback"]["ask_owner"]["on_owner_yes"] = evil
        rec["rollback"]["display"] = evil
        write_json(self.repo, ".skill-contract/incidents/records/%s.json" % rid, rec)
        (_w, _d), _out, text = digest_now(bare_tick(self.repo, [self.skills]))
        self.assertNotIn("evil", text)
        self.assertNotIn("$(touch", text)
        rollbacks = section(text, "Rollbacks")
        self.assertIn("The record's rollback commands do not match the expected form; "
                      "inspect `.skill-contract/incidents/records/%s.json`." % rid, rollbacks)
        self.assertIn("  - shows the rollback and waits: `release rollback`", rollbacks)
        self.assertIn("  - on the owner's yes: `release rollback --approved-by "
                      "\"<their name>\"`", rollbacks)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "PWN")))

    def test_record_argv_that_differs_from_the_recipe_shows_the_recipe(self):
        rid = "inc-20261104T140000Z-0f0f0f"
        incident_record(self.repo, rid, [])
        path = os.path.join(self.repo, ".skill-contract", "incidents", "records", rid + ".json")
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        rec["rollback"]["argv"] = ["curl", "https://evil.invalid/x"]
        write_json(self.repo, ".skill-contract/incidents/records/%s.json" % rid, rec)
        (_w, _d), _out, text = digest_now(bare_tick(self.repo, [self.skills]))
        rollbacks = section(text, "Rollbacks")
        self.assertIn("The recipe's rollback is `true '{version}'`.", rollbacks)
        self.assertIn("The record's copy differs from the recipe: "
                      "`curl https://evil.invalid/x`.", rollbacks)
        self.assertIn("word for word): `true '{version}'`", rollbacks)
        self.assertNotIn(": `curl", rollbacks.replace("recipe: `curl", ""))

    def test_no_recipe_shows_no_rollback_command(self):
        os.unlink(os.path.join(self.repo, ".release", "recipe.json"))
        rid = "inc-20261104T150000Z-0a0a0a"
        incident_record(self.repo, rid, [])
        (_w, _d), _out, text = digest_now(bare_tick(self.repo, [self.skills]))
        rollbacks = section(text, "Rollbacks")
        self.assertIn("The recipe's rollback is not shown: the recipe cannot be read", rollbacks)
        self.assertNotIn("by hand", rollbacks)

    def test_closed_incident_is_not_listed(self):
        rid = "inc-20261104T120000Z-0a0b0c"
        incident_record(self.repo, rid, [])
        path = os.path.join(self.repo, ".skill-contract", "incidents", "records", rid + ".json")
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        rec["status"] = "closed"
        write_json(self.repo, ".skill-contract/incidents/records/%s.json" % rid, rec)
        (word, detail), _out, text = digest_now(bare_tick(self.repo, [self.skills]))
        self.assertEqual(detail, "waiting=0")
        self.assertNotIn(rid, text)


class TestIdeaDecisionFromTheRealTool(Scratch):
    def test_digest_lists_with_read_only_ideas_list_only(self):
        """With no idea stage output in the tick, the digest runs the same read-only
        `ideas list` (never --today, never a writing command) and lists the idea."""
        repo = bare_repo(self.tmp)
        K.write_rel(repo, ".gitignore", ".skill-contract/\n")
        skills = os.path.join(self.tmp, "skills")
        K.idea_stub(skills)
        K.register_idea(repo, "shared-list", (20, 25, 30))
        today = datetime.date.today().isoformat()
        for aid, value in (("A1", 8), ("A2", 30)):
            K.ideas(repo, "record", "shared-list", aid, "--value", str(value), "--kind",
                    "experiment", "--source", "interviews", "--observed", today, "--by",
                    "Dana")
        (word, detail), _out, text = digest_now(bare_tick(repo, [skills]))
        self.assertEqual((word, detail), ("ok", "waiting=1"), text)
        self.assertIn("Idea `shared-list` has the computed verdict `GO`", text)
        self.assertIn("`ideas decide shared-list --verdict go|kill|pivot", text)
        calls = K.idea_calls(skills)
        self.assertEqual([c["argv"] for c in calls], [["--root", repo, "list"]])
        self.assertEqual((calls[0]["isolated"], calls[0]["no_site"]), (1, 1))


class TestEmptyDigest(Scratch):
    def test_nothing_waits(self):
        repo = bare_repo(self.tmp)
        (word, detail), out, text = digest_now(bare_tick(repo))
        self.assertEqual((word, detail), ("ok", "waiting=0"))
        self.assertIn("DIGEST: %s waiting=0" % DIGEST, out)
        self.assertIn("Waiting: 0", text)
        self.assertIn("\nNothing waits for the owner.\n", text)
        self.assertNotIn("\n## ", text)
        self.assertNotIn("\n- ", text)

    def test_a_full_tick_with_nothing_waiting_writes_the_digest(self):
        repo = bare_repo(self.tmp)
        skills = K.stubs(os.path.join(self.tmp, "skills"))
        K.behave(skills, "ops-intake", "autopick", stdout="AUTOPICK: matched 0 picked 0\n")
        K.pipeline_grant(repo)
        cp = subprocess.run([sys.executable, TOOL, "--root", repo, "--skills-dir", skills,
                             "tick"], capture_output=True, text=True, timeout=180)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(stages(cp.stdout)["digest"], ["ok", "waiting=0"], cp.stdout)
        self.assertIn("Nothing waits for the owner.", read(repo))


class TestInjection(Scratch):
    """Untrusted text in every source: the digest's structure is the same as with benign
    text, no raw control or bidi character survives, and no value starts a line."""

    def build(self, name, value):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        repo = bare_repo(d)
        skills = os.path.join(d, "skills")
        K.release_stub(skills)
        branch_ahead(repo, "factory/x")
        run_state(repo, 1, "factory/x", "see " + value + "\nhttps://evil" + value,
                  base=value)
        write_json(repo, ".skill-contract/releases/2.0.0/state.json",
                   {"status": "awaiting_deploy"})
        write_json(repo, ".skill-contract/incidents/records/inc-x.json", {
            "id": "inc-" + value, "status": "open", "opened_at": value,
            "consecutive_failures": value,
            "rollback": {"available": True, "display": "rollback " + value,
                         "ask_owner": {"can_act": True, "command": "release rollback " + value,
                                       "on_owner_yes": value}}})
        tick = bare_tick(repo, [skills])
        tick.waiting = [
            ("plan-read", ".skill-contract/intake/specs/" + value + ".md",
             "waiting for owner's read: reason=" + value),
            ("plan-read", value, "read the plan, then derive its grant"),
            ("idea", value, "assumptions due or expired: " + value + "; run `x`"),
            ("build", value, "build waits: " + value),
            ("release-grant", "release 2.0.0", value),
            ("kind" + value, value, value),
            ("cap", value, "wait for max_open_runs to fall below its cap"),
        ]
        tick.stops = ["max_open_runs"]
        tick.stop_facts = [("max_open_runs", "autopick", {"count": value, "limit": 1})]
        tick.idea_list = ("IDEA: shared-list GO decision=none " + value + "\n"
                          # a slug ideas.py would never print is never listed
                          "IDEA: Bad_" + value + " GO decision=none t\n").encode("utf-8")
        return tick

    def test_hostile_values_cannot_change_the_structure(self):
        (_w, benign_detail), _o, benign = digest_now(self.build("benign", "Plain_value"))
        (_w, detail), out, text = digest_now(self.build("hostile", HOSTILE))
        self.assertEqual(detail, benign_detail)
        # Steps differ (a hostile value gets no command); headings and entries do not.
        self.assertEqual([x for x in structure(text) if not x.startswith("step:")],
                         [x for x in structure(benign) if not x.startswith("step:")], text)
        for ln in text.splitlines():
            if ln.startswith("  - "):
                self.assertRegex(ln, r"^  - (no command: [^`]*(`[^`]*`)?|[^:`]+: `[^`]*`)$")
        for bad in ("\r", "\u202e", "\u2028", "\x1b"):
            self.assertNotIn(bad, text)
        for ln in text.splitlines():
            self.assertFalse(ln.startswith("# heading") or ln.startswith("- [ ]"), ln)
            self.assertEqual(ln.count("`") % 2, 0, ln)
            if ln.startswith("#"):
                self.assertTrue(ln == "# Pipeline digest" or
                                re.sub(r" \([0-9]+\)$", "", ln[3:]) in SECTION_TITLES, ln)
        self.assertIn("\\u202e", text)
        self.assertIn("\\u0060", text)
        self.assertIn("\\u000a# heading", text)
        self.assertNotIn("\nevil", out)

    def test_plan_subject_that_looks_like_an_option_gets_no_command(self):
        repo = bare_repo(self.tmp)
        tick = bare_tick(repo)
        tick.waiting = [("plan-read", s, "waiting for owner's read: reason=x")
                        for s in ("-rf", "--help", "-x.md", "../../etc/passwd.md",
                                  "/etc/passwd.md", "a b.md")]
        (_w, detail), _out, text = digest_now(tick)
        self.assertEqual(detail, "waiting=6")
        plans = section(text, "Plans waiting for a read")
        self.assertEqual(plans.count("  - no command: the plan path does not match the "
                                     "expected form"), 6, plans)
        self.assertNotRegex(plans, r"  - [^:]+: `")

    def test_every_command_template_keeps_its_argv_shape(self):
        """Table-driven: each variable part of each command template gets a benign value
        and every hostile value. A benign value gives the template's argv with the value
        as one word. A hostile value gives no command, or the same argv as a benign run
        (a constant command): it never adds, splits or replaces a word."""
        hostile = ["-rf", "--help", "x $(touch PWN)", "a`b\nc PWN", "PWN; rm -rf /",
                   "\u202ePWN", "../../PWN", "/PWN", ""]
        repo = bare_repo(self.tmp)
        skills = os.path.join(self.tmp, "skills")
        K.release_stub(skills)
        K.stubs(skills, skills=["spec-first-planning"])
        K.write_rel(repo, ".release/recipe.json", json.dumps(K.release_recipe()) + "\n")
        rel = [P.PYTHON, os.path.join(skills, "release-conductor", "assets", "release.py")]
        url = "https://github.com/acme/app/pull/9"

        def run_pr(tick, v):
            branch_ahead(tick.root, "factory/demo")
            run_state(tick.root, 1, v, True)

        def run_url(tick, v):
            branch_ahead(tick.root, "factory/b")
            run_state(tick.root, 1, "factory/b", v + "\n")

        def release_pr(tick, v):
            write_json(tick.root, ".skill-contract/releases/%s/state.json" % v,
                       {"status": "prepped", "stage": None, "prep": {"pr": True}})

        def release_url(tick, v):
            write_json(tick.root, ".skill-contract/releases/1.0.0/state.json",
                       {"status": "prepped", "stage": None, "prep": {"pr": v}})

        def incident(tick, v):
            write_json(tick.root, ".skill-contract/incidents/records/r.json",
                       {"id": v, "status": "open",
                        "rollback": {"ask_owner": {"command": v, "on_owner_yes": v},
                                     "argv": [v], "display": v}})

        def wait(kind, text):
            return lambda tick, v: tick.waiting.append((kind, v, text))

        def grant(tick, v):
            tick.grant = v
            tick.waiting.append(("plan-read", ".skill-contract/envelopes/task-plan-v1-x.json",
                                 "read the plan, then derive its grant"))

        def idea(tick, v):
            tick.idea_list = ("IDEA: %s GO decision=none t\n" % v).encode()

        def envelope(tick, v):
            tick.waiting.append(("idea-envelope", ".skill-contract/ideas/envelopes/" + v,
                                 "idea envelope waits: reason=x"))

        V = "<V>"
        table = [
            ("run branch", run_pr, "factory/demo",
             [["gh", "pr", "list", "--head", V, "--state", "open"]]),
            ("run PR url", run_url, url, [["gh", "pr", "merge", V]]),
            ("release version", release_pr, "1.2.0",
             [["gh", "pr", "list", "--head", "release/" + V, "--state", "open"]]),
            ("release PR url", release_url, url, [["gh", "pr", "merge", V]]),
            ("incident id", incident, "inc-20261104T100000Z-abc123",
             [["incident", "close", "--by", "<your name>", "--id", V]]),
            ("spec path", wait("plan-read", "waiting for owner's read: reason=x"),
             ".skill-contract/intake/specs/I0000000001.md",
             [[P.PYTHON, None, V, "--envelope", repo]]),
            ("picked envelope", wait("plan-read", "waiting for owner's read: reason=x"),
             ".skill-contract/intake/envelopes/item-3.json", [[P.PYTHON, None, V]]),
            ("pipeline grant", grant, "pipeline-grant-v1-20261104T000000Z-abcdef",
             [[P.PYTHON, None, "--under-pipeline", V, "--root", repo, "--plan",
               ".skill-contract/envelopes/task-plan-v1-x.json"]]),
            ("idea due slug", wait("idea", "assumptions due or expired: A1@x; run `x`"),
             "shared-list", [["ideas", "status", V]]),
            ("idea decision slug", idea, "shared-list",
             [["ideas", "decide", V, "--verdict", "go|kill|pivot", "--by", "<your name>",
               "--reason", "<your words>"]]),
            ("idea envelope", envelope, "idea-brief-v1-x.json",
             [[P.PYTHON, None, "check-envelope", ".skill-contract/ideas/envelopes/" + V,
               "--root", repo]]),
        ]

        def commands(setup, v, name):
            d = tempfile.mkdtemp(dir=self.tmp)
            r = os.path.join(d, "repo")
            shutil.copytree(repo, r, symlinks=True)
            tick = bare_tick(r, [skills])
            setup(tick, v)
            _res, _out, text = digest_now(tick)
            out = []
            for ln in text.splitlines():
                m = re.match(r"^  - [^:`]+: `([^`]*)`$", ln)
                if m:
                    out.append(shlex.split(m.group(1).replace(r, repo)))
            return out

        # Commands a hostile value may leave in place: every command of a benign run (a
        # constant, or a value that passed its check), and the fixed fallbacks.
        allowed = [rel + ["status", "--root", repo],
                   ["gh", "pr", "list", "--state", "open"], ["gh", "pr", "merge", "<number>"],
                   ["gh", "pr", "list", "--head", "factory/b", "--state", "open"],
                   ["gh", "pr", "list", "--head", "release/1.0.0", "--state", "open"],
                   [P.PYTHON, os.path.join(skills, "spec-first-planning", "assets",
                                           "write_grant.py"), "--under-pipeline",
                    "<pipeline-grant-id>", "--root", repo, "--plan",
                    ".skill-contract/envelopes/task-plan-v1-x.json"]]
        for name, setup, good, shapes in table:
            with self.subTest(name=name, value=good):
                got = commands(setup, good, name)
                fill = [[w.replace(V, good) if isinstance(w, str) else w for w in shape]
                        for shape in shapes]
                for want in fill:
                    match = [g for g in got if len(g) == len(want) and all(
                        w is None or w == x for w, x in zip(want, g))]
                    self.assertTrue(match, (name, want, got))
                allowed += got
        for name, setup, good, shapes in table:
            for bad in hostile:
                with self.subTest(name=name, value=bad):
                    got = commands(setup, bad, name)
                    for argv in got:
                        self.assertNotIn("PWN", " ".join(argv), (name, argv))
                        self.assertIn(argv, allowed, (name, bad, argv))

    def test_span_escapes_every_breaking_character(self):
        for ch in "`\\\n\r\t\x00\x1b\x7f\u0085\u200b\u200e\u202a\u202e\u2066\u2028\u2029" \
                  "\u00a0\ufeff\U000e0001":
            shown = P.span("a" + ch + "b")
            want = "\\u%04x" % ord(ch) if ord(ch) <= 0xFFFF else "\\U%08x" % ord(ch)
            self.assertEqual(shown, "`a%sb`" % want, repr(ch))
        self.assertEqual(P.span(""), '`""`')
        self.assertEqual(P.span("plain text"), "`plain text`")

    def test_command_quotes_real_arguments_and_keeps_holes(self):
        self.assertEqual(P.command("gh", "pr", "list", "--head", "a b;rm -rf /"),
                         "gh pr list --head 'a b;rm -rf /'")
        self.assertEqual(P.command(P.Hole("ideas"), "decide", "x", "--by",
                                   P.Hole('"<your name>"')),
                         'ideas decide x --by "<your name>"')


class TestDigestFile(Scratch):
    def test_written_in_the_git_ignored_state_dir_through_a_rename(self):
        repo = bare_repo(self.tmp)
        tick = bare_tick(repo)
        real = os.replace
        seen = []

        def spy(src, dst):
            seen.append((src, dst))
            return real(src, dst)

        with mock.patch.object(P.os, "replace", spy):
            (word, _d), _out, _text = digest_now(tick)
        self.assertEqual(word, "ok")
        d = os.path.join(repo, ".skill-contract", "pipeline")
        [(src, dst)] = seen
        self.assertEqual(dst, os.path.join(d, "digest.md"))
        self.assertEqual(os.path.dirname(src), d)  # same directory: an atomic rename
        self.assertFalse(os.path.exists(src))
        self.assertEqual([n for n in os.listdir(d) if n.startswith(".digest-")], [])
        mode = os.stat(dst).st_mode
        self.assertTrue(stat.S_ISREG(mode))
        self.assertEqual(stat.S_IMODE(mode) & 0o077, 0)
        cp = subprocess.run(["git", "-C", repo, "check-ignore", "-q", DIGEST], timeout=60)
        self.assertEqual(cp.returncode, 0)
        self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_a_planted_symlink_is_replaced_never_followed(self):
        repo = bare_repo(self.tmp)
        P.ensure_dir(repo)
        outside = os.path.join(self.tmp, "outside.txt")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("keep\n")
        os.symlink(outside, os.path.join(repo, *DIGEST.split("/")))
        digest_now(bare_tick(repo))
        with open(outside, encoding="utf-8") as f:
            self.assertEqual(f.read(), "keep\n")
        self.assertFalse(os.path.islink(os.path.join(repo, *DIGEST.split("/"))))

    def test_a_state_dir_that_is_not_ignored_is_refused(self):
        repo = bare_repo(self.tmp)
        K.write_rel(repo, ".skill-contract/pipeline/.gitignore", "*\n!digest.md\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            res = P.stage_digest(bare_tick(repo))
        self.assertEqual(res, ("refused", "state-dir=gitignore-unignores"))
        self.assertFalse(os.path.exists(os.path.join(repo, *DIGEST.split("/"))))


class TestNoNetworkNoGh(Scratch):
    def test_digest_opens_no_socket_and_runs_only_git(self):
        repo = bare_repo(self.tmp)
        branch_ahead(repo, "factory/x")
        run_state(repo, 1, "factory/x", True)
        argvs = []
        real_run = subprocess.run

        def run(argv, *a, **kw):
            argvs.append(list(argv))
            return real_run(argv, *a, **kw)

        def no_net(*a, **kw):
            raise AssertionError("the digest opened a socket")

        with mock.patch.object(P.subprocess, "run", run), \
                mock.patch.object(socket, "socket", no_net), \
                mock.patch.object(socket, "create_connection", no_net):
            (word, detail), _out, text = digest_now(bare_tick(repo))
        self.assertEqual((word, detail), ("ok", "waiting=1"))
        self.assertIn("`gh pr list --head factory/x --state open`", text)
        self.assertTrue(argvs)
        for argv in argvs:
            self.assertEqual(os.path.basename(argv[0]), "git", argv)
            self.assertNotIn("fetch", argv)
            self.assertNotIn("pull", argv)

    def test_after_a_planner_tamper_the_digest_runs_nothing(self):
        repo = bare_repo(self.tmp)
        branch_ahead(repo, "factory/x")
        run_state(repo, 1, "factory/x", True)
        tick = bare_tick(repo)
        tick.tampered = "tools"
        with mock.patch.object(P.subprocess, "run", side_effect=AssertionError("ran")):
            (word, detail), _out, text = digest_now(tick)
        self.assertEqual((word, detail), ("ok", "waiting=1"))


class TestTamperAndCapPaths(Scratch):
    def test_tamper_tick_still_writes_the_digest_with_the_marker(self):
        repo, skills, agent = K.plan_fixture(self.tmp, "good", 3)
        tool = os.path.join(skills, "spec-first-planning", "assets", "spec_to_tasks.py")
        with open(os.path.join(agent, "extra.py"), "w", encoding="utf-8") as f:
            f.write("open(%r, 'w').write('import sys\\nsys.exit(0)\\n')\n" % tool)
        K.picks(repo, skills, ["I0000000001"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = P.main(["--root", repo, "--skills-dir", skills, "tick"])
        text_out = out.getvalue()
        self.assertEqual(code, 4, text_out)
        res = stages(text_out)
        self.assertEqual(res["digest"][0], "ok", text_out)
        text = read(repo)
        tamper = section(text, "Planner tamper")
        self.assertIsNotNone(tamper, text)
        self.assertIn("A planner changed `tools`", tamper)
        self.assertIn("tamper-clear --by \"<your name>\"`", tamper)
        plans = section(text, "Plans waiting for a read")
        self.assertIn(".skill-contract/intake/specs/I0000000001.md", plans)
        self.assertIn("tamper-clear --by", plans)

    def test_cap_stop_tick_lists_the_cap_with_its_command(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        skills = K.stubs(os.path.join(self.tmp, "skills"))
        pid = os.path.basename(K.pipeline_grant(repo))[:-len(".json")]
        K.write_rel(repo, *K.derived_grant(pid))
        cp = subprocess.run([sys.executable, TOOL, "--root", repo, "--skills-dir", skills,
                             "tick"], capture_output=True, text=True, timeout=180)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("STOP: cap max_open_runs count=1 limit=1 stage=autopick", cp.stdout)
        self.assertEqual(stages(cp.stdout)["digest"][0], "ok", cp.stdout)
        caps = section(read(repo), "Cap stops")
        self.assertIsNotNone(caps)
        self.assertIn("Cap `max_open_runs` stopped the tick at stage `autopick`: "
                      "`count=1 limit=1`.", caps)
        self.assertRegex(caps, r"  - a new pipeline grant: `\S+ \S+write_grant\.py --pipeline "
                               r"--root \S+ --answers <answers\.json> --accepted-by "
                               r"\"<your name>\"`")


if __name__ == "__main__":
    unittest.main()
