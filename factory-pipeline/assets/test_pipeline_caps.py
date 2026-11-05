#!/usr/bin/env python3
"""`pipeline tick` caps (R15): when a pipeline-grant cap is reached, the tick prints
`STOP: cap <name>`, records why, and starts no new plan or run after it. With
max_plans_per_day 1 and two picked items, the tick derives one plan's grant, prints
`STOP: cap max_plans_per_day` and starts no second plan, while the grant it derived still
reaches the build hand-off. max_open_runs and max_dispatches_per_day stop it the same way
(build checks only the dispatch cap: building a grant that was already derived starts no
new counted plan or open run), and a count source it cannot read is a stop
(`STOP: cap uncounted`), never a zero.

The plan stage is R17 and not built yet. These tests put a stand-in plan stage into the
tick that hands on one plan per picked item; it starts nothing. A plan counts against
max_plans_per_day when its grant is derived (write_grant's own count), so the cap is
checked between plans inside derive-grant. The counts come from the real
spec-first-planning `write_grant.py --pipeline-caps` (the stub hands that key to it).
Stdlib unittest; offline, deterministic; scratch only in temp dirs.
Run:  python3 test_pipeline_caps.py
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

RUN_RESULT_KIND = "https://github.com/dhanesh/agent-skills/skill-contract/run-result/v1"
TWO_PICKS = ("NEXT: run spec-first-planning with .skill-contract/intake/envelopes/item-1.json\n"
             "NEXT: run spec-first-planning with .skill-contract/intake/envelopes/item-2.json\n"
             "AUTOPICK: matched 2 picked 2\n")
PLANNING = ("autopick", "plan", "derive-grant")
ONE_PICK = ("NEXT: run spec-first-planning with .skill-contract/intake/envelopes/item-1.json\n"
            "AUTOPICK: matched 1 picked 1\n")


def utc(days=0):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")


def stand_in_plan(tick):
    """Stand-in for R17's plan stage: one plan per picked item, nothing started."""
    tick.plans = ["plans/plan-%d.json" % i for i, _ in enumerate(tick.picked, 1)]
    return "ok", "stand-in plans=%d" % len(tick.plans)


class CapFixture(unittest.TestCase):
    caps = {}

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pipeline-caps-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.skills = K.stubs(os.path.join(self.tmp, "skills"))
        path = K.pipeline_grant(self.repo, caps=self.caps)
        self.pid = os.path.basename(path)[:-len(".json")]

    def tick(self):
        """Run one tick in this process, with the stand-in plan stage."""
        stages = tuple((n, stand_in_plan if n == "plan" else f) for n, f in P.STAGES)
        out = io.StringIO()
        with mock.patch.object(P, "STAGES", stages), contextlib.redirect_stdout(out):
            code = P.main(["--root", self.repo, "--skills-dir", self.skills, "tick"])
        text = out.getvalue()
        res = {ln.split()[1]: ln.split()[2:] for ln in text.splitlines()
               if ln.startswith("STAGE: ")}
        return code, text, res

    def keys(self, key):
        return [c for c in K.calls(self.skills) if c["key"] == key]

    def stops(self):
        path = os.path.join(self.repo, ".skill-contract", "pipeline", P.CAP_STOPS)
        with open(path, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f]

    def derived(self, at=None, n=1):
        rel, text = K.derived_grant(self.pid, at=at, n=n)
        K.write_rel(self.repo, rel, text)
        return os.path.basename(rel)[:-len(".json")]

    def close_run(self, gid):
        st = {"_type": "https://in-toto.io/Statement/v1", "predicateType": RUN_RESULT_KIND,
              "predicate": {"id": "run-result-v1-%s" % gid[-6:],
                            "payload": {"grant": {"id": gid}}}}
        K.write_rel(self.repo, ".skill-contract/envelopes/run-result-v1-%s.json" % gid[-6:],
                    json.dumps(st))

    def derive_writes_a_grant(self):
        rel, text = K.derived_grant(self.pid)
        K.behave(self.skills, "spec-first-planning", "under-pipeline",
                 stdout="GRANT: %s\n" % rel, write={rel: text})

    def assertStoppedBeforeAutopick(self, text, res, cap, since=0):
        self.assertIn("STOP: cap %s " % cap, text)
        for stage in PLANNING:
            self.assertEqual(res[stage], ["skipped", "cap=%s" % cap], stage)
        # Build checks only the dispatch cap, and only when it has grants to build. A
        # plans or open-runs stop does not gate it (R18 then tries an unbuilt grant; the
        # stub skills hold no conductor.py, so it says unavailable).
        if cap in ("max_dispatches_per_day", "uncounted"):
            self.assertEqual(res["build"], ["skipped", "cap=%s" % cap])
        else:
            self.assertNotEqual(res["build"][0], "skipped")
        new = [c["key"] for c in K.calls(self.skills)[since:]]
        self.assertNotIn("autopick", new)
        self.assertNotIn("under-pipeline", new)
        # The stages that only read, sweep or report still run, in their places.
        self.assertEqual(res["sweep"][0], "ok")
        self.assertEqual(res["growth-review"][0], "ok")
        self.assertEqual(res["release-stage"][0], "pending-R19")
        self.assertEqual(res["digest"][0], "pending-R21")
        self.assertIn("TICK: done stages=9 cap=%s" % cap, text)


class TestMaxPlansPerDay(CapFixture):
    caps = {"max_plans_per_day": 1, "max_open_runs": 5}

    def setUp(self):
        super().setUp()
        K.behave(self.skills, "ops-intake", "autopick", stdout=TWO_PICKS)
        self.derive_writes_a_grant()

    def test_two_picks_plan_one_then_stop(self):
        code, text, res = self.tick()
        self.assertEqual(code, 0, text)
        self.assertEqual(res["autopick"][:2], ["ok", "picked=2"])
        self.assertEqual(res["plan"], ["ok", "stand-in", "plans=2"])
        calls = self.keys("under-pipeline")
        self.assertEqual(len(calls), 1, text)  # no second plan is started
        self.assertEqual(calls[0]["argv"][-1], "plans/plan-1.json")
        self.assertIn("STOP: cap max_plans_per_day count=1 limit=1 stage=derive-grant", text)
        self.assertEqual(res["derive-grant"],
                         ["ok", "derived=1", "refused=0", "cap=max_plans_per_day"])
        # The one plan the cap allowed reaches the build hand-off: no deadlock.
        self.assertEqual(res["build"], ["unavailable", "factory-conductor", "grants=1"])
        self.assertEqual(res["digest"][0], "pending-R21")
        self.assertEqual(text.count("STOP: cap"), 1)
        rec = self.stops()
        self.assertEqual([(r["cap"], r["count"], r["limit"], r["stage"], r["grant"])
                          for r in rec],
                         [("max_plans_per_day", 1, 1, "derive-grant", self.pid)])
        # The STOP line comes before the derive-grant line that reports it.
        lines = text.splitlines()
        stop = next(i for i, ln in enumerate(lines) if ln.startswith("STOP: cap"))
        derive = next(i for i, ln in enumerate(lines) if ln.startswith("STAGE: derive-grant"))
        self.assertLess(stop, derive)

    def test_next_tick_the_same_day_starts_nothing(self):
        self.tick()
        before = len(K.calls(self.skills))
        code, text, res = self.tick()
        self.assertEqual(code, 0, text)
        new = [c["key"] for c in K.calls(self.skills)[before:]]
        self.assertNotIn("autopick", new)
        self.assertNotIn("under-pipeline", new)
        self.assertIn("STOP: cap max_plans_per_day count=1 limit=1 stage=autopick", text)
        self.assertEqual(res["autopick"], ["skipped", "cap=max_plans_per_day"])

    def test_a_plan_derived_yesterday_does_not_count(self):
        self.derived(at=utc(-1), n=9)
        code, text, _ = self.tick()
        self.assertEqual(len(self.keys("under-pipeline")), 1, text)
        self.assertIn("stage=derive-grant", text)


class TestMaxOpenRuns(CapFixture):
    caps = {"max_plans_per_day": 5, "max_open_runs": 1}

    def test_open_run_stops_before_autopick(self):
        self.derived(at=utc(-1))
        code, text, res = self.tick()
        self.assertEqual(code, 0, text)
        self.assertIn("STOP: cap max_open_runs count=1 limit=1 stage=autopick", text)
        self.assertStoppedBeforeAutopick(text, res, "max_open_runs")
        self.assertEqual(self.stops()[0]["cap"], "max_open_runs")

    def test_one_item_reaches_build_then_next_tick_stops(self):
        K.behave(self.skills, "ops-intake", "autopick", stdout=ONE_PICK)
        self.derive_writes_a_grant()
        code, text, res = self.tick()
        self.assertEqual(code, 0, text)
        self.assertNotIn("STOP:", text)  # the derived grant is not blocked by its own count
        self.assertEqual(res["derive-grant"], ["ok", "derived=1", "refused=0"])
        self.assertEqual(res["build"], ["unavailable", "factory-conductor", "grants=1"])
        before = len(K.calls(self.skills))
        code, text, res = self.tick()
        self.assertEqual(code, 0, text)
        self.assertIn("STOP: cap max_open_runs count=1 limit=1 stage=autopick", text)
        self.assertStoppedBeforeAutopick(text, res, "max_open_runs", since=before)

    def test_run_result_closes_the_run(self):
        self.close_run(self.derived(at=utc(-1)))
        code, text, res = self.tick()
        self.assertNotIn("STOP:", text)
        self.assertEqual(res["autopick"][0], "ok")
        self.assertEqual(len(self.keys("autopick")), 1)


class TestMaxDispatchesPerDay(CapFixture):
    caps = {"max_dispatches_per_day": 2, "max_open_runs": 5}

    def test_todays_dispatches_under_derived_grants_stop_the_tick(self):
        gid = self.derived(at=utc(-1))
        K.conductor_run(self.repo, gid, dispatch_at=[utc(-1), utc(), utc()])
        code, text, res = self.tick()
        self.assertEqual(code, 0, text)
        self.assertIn("STOP: cap max_dispatches_per_day count=2 limit=2 stage=autopick", text)
        self.assertStoppedBeforeAutopick(text, res, "max_dispatches_per_day")

    def test_dispatch_cap_skips_build_of_a_derived_grant(self):
        gid = self.derived(at=utc(-1), n=7)
        self.close_run(gid)
        K.conductor_run(self.repo, gid, dispatch_at=[utc()])
        K.behave(self.skills, "ops-intake", "autopick", stdout=ONE_PICK)
        self.derive_writes_a_grant()
        # One dispatch so far, cap 2: planning runs, then the build check sees 1 < 2.
        code, text, res = self.tick()
        self.assertNotIn("STOP:", text)
        self.assertEqual(res["build"], ["unavailable", "factory-conductor", "grants=1"])
        K.conductor_run(self.repo, gid, dispatch_at=[utc(), utc()], n=2)
        before = len(K.calls(self.skills))
        code, text, res = self.tick()
        self.assertIn("STOP: cap max_dispatches_per_day count=3 limit=2 stage=autopick", text)
        self.assertStoppedBeforeAutopick(text, res, "max_dispatches_per_day", since=before)

    def test_other_runs_and_other_days_do_not_count(self):
        gid = self.derived(at=utc(-1))
        K.conductor_run(self.repo, gid, dispatch_at=[utc(-1), utc(-2), utc()], n=1)
        K.conductor_run(self.repo, "autonomy-grant-v1-20261101T000000Z-aaaaaa",
                        dispatch_at=[utc(), utc(), utc()], n=2)
        code, text, res = self.tick()
        self.assertNotIn("STOP:", text)
        self.assertEqual(res["autopick"][0], "ok")


class TestUncounted(CapFixture):
    """A count source the tick cannot read is a stop, never a zero."""
    caps = {"max_open_runs": 5}

    def assertUncounted(self, source, reason):
        code, text, res = self.tick()
        self.assertEqual(code, 0, text)
        self.assertIn("STOP: cap uncounted source=%s reason=%s stage=autopick"
                      % (source, reason), text)
        self.assertStoppedBeforeAutopick(text, res, "uncounted")
        rec = self.stops()[0]
        self.assertEqual((rec["cap"], rec["source"], rec["reason"]),
                         ("uncounted", source, reason))

    def run_rel(self, n=1):
        return ".skill-contract/runs/run-20261104T000000Z-%06x" % n

    def test_unreadable_dispatch_log_line(self):
        gid = self.derived(at=utc(-1))
        K.conductor_run(self.repo, gid, log='{"event": "init"}\n{torn\n')
        self.assertUncounted(self.run_rel() + "/autonomy-log.jsonl:2", "not-json")

    def test_missing_dispatch_log(self):
        gid = self.derived(at=utc(-1))
        d = K.conductor_run(self.repo, gid)
        os.unlink(os.path.join(d, "autonomy-log.jsonl"))
        with open(os.path.join(d, "state.json"), "w", encoding="utf-8") as f:
            json.dump({"grant_id": gid, "dispatches": 2}, f)
        self.assertUncounted(self.run_rel() + "/autonomy-log.jsonl", "missing")

    def test_dispatch_with_no_time(self):
        gid = self.derived(at=utc(-1))
        K.conductor_run(self.repo, gid, log='{"event": "dispatch", "task": "T1"}\n')
        self.assertUncounted(self.run_rel() + "/autonomy-log.jsonl:1", "no-time")

    def test_run_dir_with_a_log_but_no_state(self):
        d = K.conductor_run(self.repo, "autonomy-grant-v1-20261101T000000Z-aaaaaa")
        os.unlink(os.path.join(d, "state.json"))
        self.assertUncounted(self.run_rel() + "/state.json", "missing")

    def test_run_still_being_created_is_skipped(self):
        os.makedirs(os.path.join(self.repo, *self.run_rel().split("/")))
        code, text, res = self.tick()
        self.assertNotIn("STOP:", text)

    def test_derived_run_before_its_first_log_line_counts_zero(self):
        gid = self.derived(at=utc(-1))
        d = K.conductor_run(self.repo, gid)
        os.unlink(os.path.join(d, "autonomy-log.jsonl"))  # state.json says dispatches 0
        code, text, res = self.tick()
        self.assertNotIn("STOP:", text)

    def test_unreadable_run_state(self):
        d = K.conductor_run(self.repo, "autonomy-grant-v1-20261101T000000Z-aaaaaa")
        with open(os.path.join(d, "state.json"), "w", encoding="utf-8") as f:
            f.write("{half")
        self.assertUncounted(self.run_rel() + "/state.json", "unreadable")

    def test_unreadable_envelope_fails_the_counter_closed(self):
        K.write_rel(self.repo, ".skill-contract/envelopes/junk.json", "{not json")
        self.assertUncounted("write_grant", "failed")

    def test_counter_answer_that_is_not_json(self):
        K.behave(self.skills, "spec-first-planning", "pipeline-caps", stdout="0\n")
        self.assertUncounted("write_grant", "unreadable")

    def test_counter_for_another_grant_is_unreadable(self):
        rep = {"pipeline_grant": "pipeline-grant-v1-20260101T000000Z-abcdef",
               "date": "2026-11-04", "derived": [],
               "caps": {c: 9 for c in P.CAPS},
               "counts": {"max_plans_per_day": 0, "max_open_runs": 0}}
        K.behave(self.skills, "spec-first-planning", "pipeline-caps",
                 stdout=json.dumps(rep) + "\n")
        self.assertUncounted("write_grant", "unreadable")


class TestUnderCaps(CapFixture):
    def test_no_stop_and_no_record_under_every_cap(self):
        code, text, res = self.tick()
        self.assertEqual(code, 0, text)
        self.assertNotIn("STOP:", text)
        self.assertIn("TICK: done stages=9", text)
        self.assertNotIn("cap=", text)
        self.assertFalse(os.path.exists(os.path.join(
            self.repo, ".skill-contract", "pipeline", P.CAP_STOPS)))


if __name__ == "__main__":
    unittest.main()
