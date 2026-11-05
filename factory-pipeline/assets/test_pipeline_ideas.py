#!/usr/bin/env python3
"""`pipeline tick` idea follow-up stage (R20): the tick lists each idea with an assumption
due within 3 days or expired, and hands each `go` idea-brief envelope with no plan yet to
the plan stage of the same tick.

The fixture ideas are written with the REAL idea-validation tool, without --today (the
tool refuses it outside its own tests), so every deadline is computed from the real date:
today + 2 (listed), today + 10 (not listed) and today - 1 (expired, listed). The stub
idea-validation logs each call and then runs a copy of the real ideas.py, so the stage
reads the real `list` and `status` output, and the tests can show that no writing
command (new, register, record, decide, handoff) and no --today ever ran. The planner is
the stub planner of the plan-stage tests: no real agent runs.
Stdlib unittest; offline, deterministic; scratch only in temp dirs; needs git.
Run:  python3 test_pipeline_ideas.py
"""
import contextlib
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline as P  # noqa: E402
import pipeline_testkit as K  # noqa: E402


def run_tick(repo, skills):
    """Run one tick in this process. Return (exit, stdout, {stage: words}, digest list)."""
    seen = {}

    def digest(tick):
        seen["waiting"] = list(tick.waiting)
        return P.stage_digest(tick)

    stages = tuple((n, digest if n == "digest" else f) for n, f in P.STAGES)
    out = io.StringIO()
    with mock.patch.object(P, "STAGES", stages), contextlib.redirect_stdout(out):
        code = P.main(["--root", repo, "--skills-dir", skills, "tick"])
    text = out.getvalue()
    res = {ln.split()[1]: ln.split()[2:] for ln in text.splitlines()
           if ln.startswith("STAGE: ")}
    return code, text, res, seen.get("waiting", [])


class IdeaFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pipeline-ideas-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo, self.skills, self.agent = K.idea_fixture(self.tmp)

    def tick(self):
        return run_tick(self.repo, self.skills)

    def idea_lines(self, text):
        return [ln for ln in text.splitlines() if ln.startswith("IDEA: ")]

    def assertReadOnly(self):
        """No writing ideas command and no --today ever ran; every call was isolated."""
        calls = K.idea_calls(self.skills)
        self.assertTrue(calls)
        for c in calls:
            sub = c["argv"][2] if c["argv"][:1] == ["--root"] else c["argv"][0]
            self.assertIn(sub, P.IDEA_READ, c)
            self.assertNotIn(sub, K.IDEA_WRITERS, c)
            self.assertFalse(any(a.startswith("--today") for a in c["argv"]), c)
            self.assertEqual((c["isolated"], c["no_site"]), (1, 1), c)


class TestIdeaDue(IdeaFixture):
    def test_due_soon_and_expired_listed_later_not(self):
        code, text, res, waiting = self.tick()
        self.assertEqual(code, 0, text)
        today = datetime.date.today()
        soon = (today + datetime.timedelta(days=2)).isoformat()
        late = (today - datetime.timedelta(days=1)).isoformat()
        lines = self.idea_lines(text)
        # An idea due in 2 days is listed, with the command the owner runs next.
        self.assertIn("IDEA: due soon A1@%s next: ideas status soon" % soon, lines)
        # An expired idea is listed.
        self.assertIn("IDEA: due late A1@%s(expired) next: ideas status late" % late, lines)
        # An idea due in 10 days is not.
        self.assertFalse([ln for ln in lines if " later " in ln], text)
        # The decided go idea is not listed as due: its experiments are over.
        self.assertFalse([ln for ln in lines if ln.startswith("IDEA: due go-list")], text)
        self.assertEqual(res["idea-follow-up"], ["ok", "due=2", "go=1"], text)
        idea_waits = sorted(w[1] for w in waiting if w[0] == "idea")
        self.assertEqual(idea_waits, ["late", "soon"])
        for w in waiting:
            if w[0] == "idea":
                self.assertIn("`ideas status %s`" % w[1], w[2])
        self.assertNotIn("pending-R20", text)

    def test_no_writing_ideas_command_runs(self):
        self.tick()
        self.tick()
        self.assertReadOnly()
        # The stage never changed an idea: the state files still hold no decision for the
        # three undecided ideas.
        for slug in ("soon", "later", "late"):
            with open(os.path.join(self.repo, ".skill-contract", "ideas", slug, "state.json"),
                      encoding="utf-8") as f:
                self.assertEqual(json.load(f)["decisions"], [])


class TestIdeaGo(IdeaFixture):
    def test_go_envelope_planned_once_across_ticks(self):
        code, text, res, _ = self.tick()
        self.assertEqual(code, 0, text)
        self.assertEqual(len(K.planner_calls(self.agent)), 1, text)
        self.assertRegex(text, r"IDEA: plan go-list envelope=\.skill-contract/ideas/"
                               r"envelopes/idea-brief-v1-\S+\.json")
        self.assertIn("spec=.skill-contract/ideas/specs/go-list.md", text)
        self.assertEqual(res["plan"][:2], ["ok", "ready=1"], text)
        self.assertEqual(res["autopick"], ["ok", "picked=0"], text)
        # The next tick does not plan it again.
        code, text, res, _ = self.tick()
        self.assertEqual(code, 0, text)
        self.assertEqual(len(K.planner_calls(self.agent)), 1, text)
        self.assertNotIn("IDEA: plan", text)
        self.assertEqual(res["idea-follow-up"], ["ok", "due=2", "go=0"], text)
        self.assertEqual(res["plan"], ["idle", "picked=0"], text)

    def test_recorded_plan_holds_without_the_spec(self):
        """The owner moves the spec and the task plan away: the record still holds."""
        self.tick()
        os.remove(os.path.join(self.repo, ".skill-contract", "ideas", "specs", "go-list.md"))
        envs = os.path.join(self.repo, ".skill-contract", "envelopes")
        for n in os.listdir(envs):
            if n.startswith("task-plan-v1-"):
                os.remove(os.path.join(envs, n))
        code, text, _res, _ = self.tick()
        self.assertEqual(len(K.planner_calls(self.agent)), 1, text)
        self.assertNotIn("IDEA: plan", text)

    def test_non_go_and_invalid_envelopes_not_planned(self):
        base = os.path.join(self.repo, *P.IDEA_ENVELOPES.split("/"))
        go = [n for n in os.listdir(base) if n.endswith(".json")][0]
        with open(os.path.join(base, go), encoding="utf-8") as f:
            st = json.load(f)
        # A kill envelope (a valid envelope whose decision is not go) for another slug.
        if K.PLANNING_ASSETS not in sys.path:
            sys.path.insert(0, K.PLANNING_ASSETS)
        import contract_check  # noqa: E402
        p = dict(st["predicate"]["payload"], slug="killed")
        p["decision"] = dict(p["decision"], verdict="kill")
        kill = contract_check.build_statement(P.IDEA_KIND, "idea-validation", "0.1.6",
                                              self.repo, [st["subject"][0]["name"]], p)
        K.write_rel(self.repo, "%s/%s.json" % (P.IDEA_ENVELOPES, kill["predicate"]["id"]),
                    json.dumps(kill))
        # An invalid go envelope: check-envelope refuses its _type.
        bad = json.loads(json.dumps(st))
        bad["_type"] = "https://example.invalid/not-a-statement"
        bad["predicate"]["payload"]["slug"] = "broken"
        bad_rel = "%s/%s" % (P.IDEA_ENVELOPES, go.replace(".json", "0.json"))
        K.write_rel(self.repo, bad_rel, json.dumps(bad))
        os.remove(os.path.join(base, go))  # only the two envelopes under test remain
        code, text, res, waiting = self.tick()
        self.assertEqual(code, 0, text)
        self.assertEqual(K.planner_calls(self.agent), [], text)
        self.assertNotIn("IDEA: plan", text)
        self.assertIn("IDEA: waiting %s reason=check-envelope-failed" % bad_rel, text)
        self.assertIn(("idea-envelope", bad_rel,
                       "idea envelope waits: reason=check-envelope-failed"), waiting)
        self.assertEqual(res["idea-follow-up"], ["attention", "due=2", "go=0", "waiting=1"],
                         text)
        self.assertEqual(res["plan"], ["idle", "picked=0"], text)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".skill-contract", "ideas",
                                                     "specs", "killed.md")))
        self.assertReadOnly()


class TestIdeaUnavailable(unittest.TestCase):
    def test_no_idea_validation(self):
        tmp = tempfile.mkdtemp(prefix="pipeline-ideas-")
        self.addCleanup(shutil.rmtree, tmp, True)
        repo, skills, _ = K.plan_fixture(tmp, "good", 3)
        K.behave(skills, "ops-intake", "autopick", stdout="")
        code, text, res, _ = run_tick(repo, skills)
        self.assertEqual(code, 0, text)
        self.assertEqual(res["idea-follow-up"],
                         ["unavailable", "idea-validation", "due=0", "go=0"], text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
