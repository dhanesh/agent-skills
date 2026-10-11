"""Spend metering (roadmap step 6B): budget.enforce, the `usage` command and the
budget_unmetered / budget_tokens / budget_usd stops. Stdlib only, offline.

Without `enforce` nothing changes; these tests pin the opt-in side and the
no-enforce side next to each other."""
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C  # noqa: E402
import reentry as R  # noqa: E402
from conductor_testkit import (GIT, repo_with_plan, write_plan_envelope,  # noqa: E402
                               write_grant, tmpdir)

OK = [{"text": "ok", "command": ["true"]}]
ONE = {"T1": ([], OK)}
THREE = {"T1": ([], OK), "T2": ([], OK), "T3": ([], OK)}


@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class SpendTests(unittest.TestCase):
    def init(self, tasks=None, budget=None, grant_budget=None):
        self.root = repo_with_plan()
        self.plan = write_plan_envelope(self.root, tasks or ONE)
        write_grant(self.root, self.plan, budget=grant_budget)
        argv = ["init", "--plan", self.plan, "--root", self.root]
        if budget is not None:
            argv += ["--budget", json.dumps(budget)]
        rc, _, _ = self.run_main(argv)
        return rc

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = C.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def cmd(self, *argv):
        return self.run_main(list(argv) + ["--root", self.root])

    def state(self):
        return C.State.load(C.state_path(self.root))

    def commit(self, task):
        wt = self.state().tasks[task]["worktree"]
        with open(os.path.join(wt, task + ".txt"), "w") as f:
            f.write(task + "\n")
        subprocess.run(["git", "-C", wt, "add", "-A"], check=True)
        subprocess.run(["git", "-C", wt, "commit", "-q", "-m", task], check=True,
                       env=dict(os.environ, **GIT))

    def prove(self, task, meter=None):
        """start, commit, verify, review pass, merge. meter: tokens to record after each
        finished dispatch (executor after verify, reviewer after review), or None."""
        self.assertEqual(self.cmd("start", task)[0], 0)
        self.commit(task)
        rc, out, err = self.cmd("verify", task)
        self.assertEqual(rc, 0, out + err)
        if meter is not None:
            self.assertEqual(self.cmd("usage", task, "--tokens", str(meter))[0], 0)
        self.assertEqual(self.cmd("review", task, "--verdict", "pass")[0], 0)
        if meter is not None:
            self.assertEqual(self.cmd("usage", task, "--tokens", str(meter))[0], 0)
        rc, out, err = self.cmd("merge", task)
        self.assertEqual(rc, 0, out + err)

    # ── the budget key ─────────────────────────────────────────────────────
    def test_enforce_without_its_cap_is_refused(self):
        self.assertEqual(self.init(budget={"enforce": ["usd"], "max_tokens": 5}), 2)
        self.assertIsNone(C.current_run(self.root))

    def test_bad_enforce_values_are_refused(self):
        for bad in ([], ["tokens", "tokens"], ["gold"], "tokens", [1], None, {"tokens": 1}):
            self.assertEqual(self.init(budget={"enforce": bad, "max_tokens": 5,
                                               "max_usd": 1}), 2, bad)

    def test_budget_cannot_drop_a_metric_the_grant_enforces(self):
        rc = self.init(budget={"enforce": ["tokens"]},
                       grant_budget={"enforce": ["tokens", "usd"], "max_tokens": 9,
                                     "max_usd": 1})
        self.assertEqual(rc, 2)

    def test_budget_may_add_enforce_or_a_metric(self):
        self.assertEqual(self.init(budget={"enforce": ["tokens", "usd"]},
                                   grant_budget={"enforce": ["tokens"], "max_tokens": 9,
                                                 "max_usd": 1}), 0)
        self.assertEqual(self.state().budget["enforce"], ["tokens", "usd"])
        self.assertEqual(self.init(budget={"enforce": ["tokens"], "max_tokens": 9}), 0)

    def test_a_grant_carrying_enforce_keeps_it(self):
        self.assertEqual(self.init(grant_budget={"enforce": ["tokens"], "max_tokens": 9}), 0)
        self.assertEqual(self.state().budget["enforce"], ["tokens"])

    # ── recording usage ────────────────────────────────────────────────────
    def test_usage_needs_a_finished_dispatch_and_meters_each_once(self):
        self.assertEqual(self.init(budget={"enforce": ["tokens"], "max_tokens": 1000}), 0)
        self.assertEqual(self.cmd("start", "T1")[0], 0)
        # the executor is in flight: nothing is finished yet
        self.assertEqual(self.cmd("usage", "T1", "--tokens", "10")[0], 2)
        self.commit("T1")
        self.assertEqual(self.cmd("verify", "T1")[0], 0)
        rc, out, _ = self.cmd("usage", "T1", "--tokens", "10", "--source", "subagent report")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "USAGE: T1 dispatch=1 tokens=10 usd=- trust=claimed")
        # the reviewer is in flight; the executor is metered: a second report is refused
        self.assertEqual(self.cmd("usage", "T1", "--tokens", "10")[0], 2)
        st = self.state()
        self.assertEqual(len(st.usage), 1)
        e = st.usage[0]
        self.assertEqual({k: e[k] for k in ("task", "dispatch", "tokens", "usd", "source",
                                            "trust")},
                         {"task": "T1", "dispatch": 1, "tokens": 10, "usd": None,
                          "source": "subagent report", "trust": "claimed"})
        self.assertIn("at", e)
        events = [x for x in st.events() if x["event"] == "usage"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["dispatch"], 1)

    def test_usage_refuses_bad_numbers(self):
        self.assertEqual(self.init(budget={"enforce": ["usd"], "max_usd": 5}), 0)
        self.cmd("start", "T1")
        self.commit("T1")
        self.cmd("verify", "T1")
        for argv in (["--tokens", "-1", "--usd", "1"], ["--tokens", "1", "--usd", "nan"],
                     ["--tokens", "1", "--usd", "inf"], ["--tokens", "1", "--usd", "-0.5"],
                     ["--tokens", "1.5", "--usd", "1"],
                     ["--tokens", "1"]):  # usd is enforced: a usd figure is required
            self.assertEqual(self.cmd("usage", "T1", *argv)[0], 2, argv)
        self.assertEqual(self.state().usage, [])
        self.assertEqual(self.cmd("usage", "T1", "--tokens", "1", "--usd", "0.25")[0], 0)

    def test_usage_refuses_an_unknown_task_and_a_finished_run(self):
        self.assertEqual(self.init(), 0)
        self.assertEqual(self.cmd("usage", "T9", "--tokens", "1")[0], 2)
        st = self.state()
        st.finished = {"envelope": "x.json"}
        st.save()
        self.assertEqual(self.cmd("usage", "T1", "--tokens", "1")[0], 2)

    def test_usage_file_applies_good_records_and_reports_bad_ones(self):
        self.assertEqual(self.init(tasks=THREE, budget={"enforce": ["tokens"],
                                                        "max_tokens": 10 ** 6}), 0)
        self.cmd("start", "T1")
        self.commit("T1")
        self.cmd("verify", "T1")
        path = os.path.join(tmpdir(), "usage.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps({"task": "T1", "tokens": 70, "usd": 0.5,
                                "source": "billing export"}) + "\n")
            f.write("not json\n")
            f.write(json.dumps({"task": "T2", "tokens": 5}) + "\n")  # nothing finished
            f.write('{"task": "T1", "tokens": NaN}\n')
            f.write(json.dumps({"task": "T1", "tokens": True}) + "\n")
            f.write("\n")
        rc, out, _ = self.cmd("usage", "--file", path)
        self.assertEqual(rc, 3)
        lines = out.splitlines()
        self.assertIn("USAGE: T1 dispatch=1 tokens=70 usd=0.5 trust=low", lines)
        problems = [l for l in lines if l.startswith("PROBLEM: line ")]
        self.assertEqual([l.split()[2].rstrip(":") for l in problems], ["2", "3", "4", "5"])
        st = self.state()
        self.assertEqual([(e["task"], e["tokens"], e["trust"]) for e in st.usage],
                         [("T1", 70, "low")])

    def test_usage_file_that_cannot_be_read_exits_2(self):
        self.assertEqual(self.init(), 0)
        self.assertEqual(self.cmd("usage", "--file", os.path.join(tmpdir(), "no"))[0], 2)
        self.assertEqual(self.cmd("usage", "T1", "--file", "x")[0], 2)
        self.assertEqual(self.cmd("usage")[0], 2)

    # ── the stops ──────────────────────────────────────────────────────────
    def test_an_unmetered_finished_dispatch_stops_an_enforced_run(self):
        self.assertEqual(self.init(tasks=THREE, budget={"enforce": ["tokens"],
                                                        "max_tokens": 1000}), 0)
        self.prove("T1")  # no usage recorded
        rc, out, _ = self.cmd("next")
        self.assertEqual((rc, out.strip()), (3, "STOP: budget_unmetered"))
        self.assertEqual(self.state().stopped["reason"], "budget_unmetered")
        rc, out, _ = self.cmd("status")
        self.assertIn("STATUS: unmetered T1 dispatch=1 kind=executor", out)
        self.assertIn("STATUS: unmetered T1 dispatch=2 kind=reviewer", out)
        # resume cannot lift it while a dispatch is unmetered: a human is needed
        rc, out, _ = self.cmd("resume")
        self.assertEqual(rc, 3)
        self.assertIn("STOP: budget_unmetered", out)
        self.assertEqual(out.splitlines()[-1], "NEXT: run ask")
        # usage works on the stopped run; once both are metered, resume lifts the stop
        self.assertEqual(self.cmd("usage", "T1", "--tokens", "5")[0], 0)
        self.assertEqual(self.cmd("usage", "T1", "--tokens", "5")[0], 0)
        rc, out, _ = self.cmd("resume")
        self.assertEqual(rc, 0, out)
        self.assertIn("READY: T2 T3", out)
        self.assertIsNone(self.state().stopped)

    def test_work_in_flight_continues_while_a_new_dispatch_is_refused(self):
        self.assertEqual(self.init(tasks=THREE, budget={"enforce": ["tokens"],
                                                        "max_tokens": 1000}), 0)
        self.assertEqual(self.cmd("start", "T1")[0], 0)
        self.commit("T1")
        self.assertEqual(self.cmd("verify", "T1")[0], 0)  # executor finished, unmetered
        rc, out, _ = self.cmd("next")
        self.assertEqual((rc, out), (0, ""))  # T1's reviewer is in flight
        rc, out, err = self.cmd("start", "T2")
        self.assertEqual(rc, 2)
        self.assertNotIn("STOP:", out)
        self.assertIn("budget_unmetered", err)
        refused = [e for e in self.state().events() if e["event"] == "dispatch_refused"]
        self.assertEqual(refused[-1]["reason"], "budget_unmetered")
        self.assertIsNone(self.state().stopped)
        # T1's own path goes on to its outcome
        self.assertEqual(self.cmd("review", "T1", "--verdict", "pass")[0], 0)
        self.assertEqual(self.cmd("merge", "T1")[0], 0)
        self.assertEqual(self.cmd("next")[1].strip(), "STOP: budget_unmetered")

    def test_the_token_cap_stops_an_enforced_run(self):
        self.assertEqual(self.init(tasks=THREE, budget={"enforce": ["tokens"],
                                                        "max_tokens": 100}), 0)
        self.prove("T1", meter=60)  # 120 tokens >= 100
        rc, out, _ = self.cmd("next")
        self.assertEqual((rc, out.strip()), (3, "STOP: budget_tokens"))
        rc, out, _ = self.cmd("resume")  # a token stop is never lifted
        self.assertEqual(rc, 3)
        self.assertEqual(out.splitlines()[-1], "NEXT: run finish")
        rc, out, _ = self.cmd("status")
        self.assertIn("STATUS: usage tokens=120/100 usd=- entries=2 unmetered=0 "
                      "trust=claimed", out.splitlines())

    def test_under_the_cap_the_run_goes_on(self):
        self.assertEqual(self.init(tasks=THREE, budget={"enforce": ["tokens"],
                                                        "max_tokens": 1000}), 0)
        self.prove("T1", meter=60)
        rc, out, _ = self.cmd("next")
        self.assertEqual((rc, out.strip()), (0, "READY: T2 T3"))

    def test_start_records_a_spend_stop_when_nothing_is_in_flight(self):
        self.assertEqual(self.init(tasks=THREE, budget={"enforce": ["tokens"],
                                                        "max_tokens": 0}), 0)
        rc, out, _ = self.cmd("start", "T1")
        self.assertEqual((rc, out.strip()), (3, "STOP: budget_tokens"))

    def test_the_dollar_cap_stops_an_enforced_run(self):
        self.assertEqual(self.init(tasks=THREE, budget={"enforce": ["usd"],
                                                        "max_usd": 1.0}), 0)
        self.cmd("start", "T1")
        self.commit("T1")
        self.cmd("verify", "T1")
        self.assertEqual(self.cmd("usage", "T1", "--tokens", "5", "--usd", "0.75")[0], 0)
        self.cmd("review", "T1", "--verdict", "pass")
        self.assertEqual(self.cmd("usage", "T1", "--tokens", "5", "--usd", "0.25")[0], 0)
        self.cmd("merge", "T1")
        rc, out, _ = self.cmd("next")
        self.assertEqual((rc, out.strip()), (3, "STOP: budget_usd"))

    def test_unmetered_comes_before_the_caps(self):
        self.assertEqual(self.init(tasks=THREE, budget={"enforce": ["tokens"],
                                                        "max_tokens": 10}), 0)
        self.cmd("start", "T1")
        self.commit("T1")
        self.cmd("verify", "T1")
        self.cmd("usage", "T1", "--tokens", "50")  # over the cap
        self.cmd("review", "T1", "--verdict", "pass")  # the reviewer is unmetered
        self.cmd("merge", "T1")
        self.assertEqual(self.cmd("next")[1].strip(), "STOP: budget_unmetered")

    def test_a_crash_re_dispatch_leaves_the_older_dispatch_unmetered(self):
        # Fail closed: resume hands T1's executor out again, the one verify finishes
        # both, and the resumed session can only report its own subagent.
        self.assertEqual(self.init(tasks=THREE, budget={"enforce": ["tokens"],
                                                        "max_tokens": 1000}), 0)
        self.cmd("start", "T1")
        rc, out, _ = self.cmd("resume")
        self.assertIn("NEXT: T1 dispatch-executor", out)
        self.commit("T1")
        self.cmd("verify", "T1")
        rc, out, _ = self.cmd("usage", "T1", "--tokens", "5")
        self.assertIn("dispatch=2", out)  # the latest finished dispatch first
        self.cmd("review", "T1", "--verdict", "pass")
        self.cmd("usage", "T1", "--tokens", "5")
        self.cmd("merge", "T1")
        self.assertEqual(self.cmd("next")[1].strip(), "STOP: budget_unmetered")
        self.assertEqual(self.cmd("usage", "T1", "--tokens", "0", "--source",
                                  "human: lost with the crashed session")[0], 0)
        self.assertEqual(self.cmd("resume")[0], 0)

    def test_a_recorded_unmetered_stop_waits_for_the_human_in_watch(self):
        self.assertEqual(R.decide(False, "budget_unmetered", True, {"stall_min": 1}, False,
                                  10 ** 6, 0), "waiting-human")
        self.assertEqual(R.decide(False, "budget_tokens", True, {"stall_min": 1}, False,
                                  10 ** 6, 0), "start")

    # ── no enforce: unchanged ──────────────────────────────────────────────
    def test_without_enforce_an_unmetered_run_goes_on(self):
        self.assertEqual(self.init(tasks=THREE, budget={"max_tokens": 1, "max_usd": 0}), 0)
        self.prove("T1")
        rc, out, _ = self.cmd("next")
        self.assertEqual((rc, out.strip()), (0, "READY: T2 T3"))
        rc, out, _ = self.cmd("status")
        self.assertEqual(out.splitlines()[-1], C.BUDGET_NOTE)
        self.assertNotIn("STATUS: usage", out)
        st = self.state()
        self.assertNotIn("usage", st.to_dict())
        pay = C.run_result_payload(st)
        self.assertNotIn("usage", pay)
        self.assertEqual(pay["budget_note"], C.BUDGET_NOTE_TEXT)
        self.assertNotIn("Usage:", C.pr_body(pay, "e.json"))

    # ── reporting ──────────────────────────────────────────────────────────
    def test_status_says_which_caps_are_enforced(self):
        self.assertEqual(self.init(budget={"enforce": ["tokens"], "max_tokens": 50,
                                           "max_usd": 2}), 0)
        rc, out, _ = self.cmd("status")
        lines = out.splitlines()
        self.assertIn("STATUS: budget max_tokens enforced from claimed usage reports; "
                      "max_usd recorded, not enforced", lines)
        self.assertNotIn(C.BUDGET_NOTE, lines)
        self.assertIn("STATUS: usage tokens=0/50 usd=-/2 entries=0 unmetered=0 trust=-", lines)

    def test_run_result_and_pr_body_carry_the_usage(self):
        self.assertEqual(self.init(tasks=THREE, budget={"enforce": ["tokens"],
                                                        "max_tokens": 1000}), 0)
        self.cmd("start", "T1")
        self.commit("T1")
        self.cmd("verify", "T1")
        self.cmd("usage", "T1", "--tokens", "30", "--usd", "0.1")
        path = os.path.join(tmpdir(), "u.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps({"task": "T1", "tokens": 12}) + "\n")
        self.cmd("review", "T1", "--verdict", "pass")
        self.assertEqual(self.cmd("usage", "--file", path)[0], 0)
        self.cmd("merge", "T1")
        pay = C.run_result_payload(self.state())
        self.assertEqual(pay["usage"], {"tokens": 42, "usd": 0.1, "entries": 2,
                                        "unmetered": 0, "trust": "low"})
        self.assertIn("enforced", pay["budget_note"])
        body = C.pr_body(pay, "e.json")
        self.assertIn("Usage: 42 tokens, 0.1 USD, from 2 entries (trust `low`); "
                      "0 dispatches unmetered.", body)

    def test_the_stop_reasons_are_known(self):
        for reason in ("budget_unmetered", "budget_tokens", "budget_usd"):
            self.assertIn(reason, C.STOP_REASONS)


if __name__ == "__main__":
    unittest.main()
