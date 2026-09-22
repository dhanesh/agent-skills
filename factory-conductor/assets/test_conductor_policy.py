import contextlib, io, json, os, subprocess, sys, tempfile, time, unittest
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C
import contract_check as CC
from conductor_testkit import (GIT, repo_with_plan, payload, write_plan_envelope,
                               write_grant, revoke, new_run, z as _z)


ONE_OK = {"T1": ([], [{"text": "ok", "command": ["true"]}])}
ONE_BAD = {"T1": ([], [{"text": "bad", "command": ["false"]}])}
TWO_OK = {"T1": ([], [{"text": "ok", "command": ["true"]}]),
          "T2": ([], [{"text": "ok", "command": ["true"]}])}


@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class PolicyTests(unittest.TestCase):
    def init(self, tasks=None, grant=True, budget=None, minutes=60):
        self.root = repo_with_plan()
        self.plan = write_plan_envelope(self.root, tasks or ONE_OK)
        if grant:
            write_grant(self.root, self.plan, minutes=minutes)
        argv = ["init", "--plan", self.plan, "--root", self.root]
        if budget is not None:
            argv += ["--budget", json.dumps(budget)]
        return C.main(argv)

    def out(self, argv):
        """Run a command, returning (exit code, stdout)."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = C.main(argv)
        return rc, buf.getvalue()

    def test_init_refuses_without_a_covering_grant(self):
        rc = self.init(grant=False)
        self.assertEqual(rc, 3)

    def test_init_creates_the_run_branch_and_state(self):
        self.root = repo_with_plan()
        self.plan = write_plan_envelope(self.root, ONE_OK)
        write_grant(self.root, self.plan)
        rc, out = self.out(["init", "--plan", self.plan, "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("RUN: run-", out)
        self.assertTrue(os.path.isfile(C.state_path(self.root)))

    def test_start_is_gated_by_the_grant(self):
        self.assertEqual(self.init(), 0)
        subprocess.run([sys.executable, "-I",
                        os.path.join(os.path.dirname(C.__file__), "contract_check.py"),
                        "revoke-grant", "--root", self.root], check=True, capture_output=True)
        rc, out = self.out(["start", "T1", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("GATE: ASK", out)
        self.assertIn("revoked", out)

    def test_wall_clock_budget_stops_the_run(self):
        self.assertEqual(self.init(budget={"wall_clock_min": 0}), 0)
        st = C.State.load(C.state_path(self.root))
        st.created_at = _z(CC.utc_now() - timedelta(minutes=5))
        st.save()
        rc, out = self.out(["next", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("STOP: budget_wall_clock", out)

    def test_dispatch_budget_stops_the_run(self):
        self.assertEqual(self.init(tasks=TWO_OK, budget={"max_dispatches": 1}), 0)
        self.assertEqual(C.main(["start", "T1", "--root", self.root]), 0)
        rc, out = self.out(["start", "T2", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("STOP: budget_dispatches", out)

    def test_repairs_budget_parks_the_task(self):
        self.assertEqual(self.init(tasks=ONE_BAD, budget={"max_repairs_per_task": 1}), 0)
        C.main(["start", "T1", "--root", self.root])
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)
        rc, out = self.out(["verify", "T1", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("PARK: T1 verify_red_after_repairs", out)

    def test_no_ready_tasks_stops_the_run(self):
        self.assertEqual(self.init(), 0)
        C.main(["park", "T1", "--reason", "by hand", "--root", self.root])
        rc, out = self.out(["next", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("STOP: no_ready_tasks", out)

    def test_token_and_dollar_budgets_are_recorded_not_enforced(self):
        self.assertEqual(self.init(budget={"max_tokens": 1, "max_usd": 0}), 0)
        rc, out = self.out(["status", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("recorded, not enforced", out)
        self.assertEqual(C.main(["start", "T1", "--root", self.root]), 0)

    def test_a_new_human_decision_parks_and_the_run_continues(self):
        self.assertEqual(self.init(tasks=TWO_OK), 0)
        rc, out = self.out(["decision", "T1", "--question", "which CI provider?",
                            "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("PARK: T1 new_human_decision", out)
        rc, out = self.out(["next", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("READY: T2", out)

    def test_an_expired_grant_stops_the_next_gate(self):
        self.assertEqual(self.init(minutes=1), 0)
        # Write a newer grant whose expiry is already past, then gate.
        write_grant(self.root, self.plan, minutes=-1)
        rc, out = self.out(["start", "T1", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("GATE: ASK", out)
        self.assertIn("reason=expired", out)


@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class InitTests(unittest.TestCase):
    """init: validate the plan envelope, require a covering grant, cut the run branch."""

    def setUp(self):
        self.root = repo_with_plan()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = C.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def head(self):
        return C.CC.current_branch(self.root)

    def branches(self):
        return C.git(self.root, "branch", "--format=%(refname:short)").stdout.split()

    def test_init_cuts_factory_slug_from_the_current_branch_and_checks_it_out(self):
        plan = write_plan_envelope(self.root, plan=payload(ONE_OK, title="Add Login!"))
        grant = write_grant(self.root, plan)
        base = C.git(self.root, "rev-parse", "HEAD").stdout.strip()
        rc, out, _ = self.run_main(["init", "--plan", plan, "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertEqual(self.head(), "factory/add-login")
        self.assertEqual(C.git(self.root, "rev-parse", "HEAD").stdout.strip(), base)
        st = C.State.load(C.state_path(self.root))
        self.assertEqual(out.strip(), "RUN: %s" % st.run_id)
        self.assertEqual((st.run_branch, st.base_branch), ("factory/add-login", "factory/p"))
        self.assertEqual(st.grant_id, os.path.basename(grant)[:-len(".json")])
        self.assertEqual(st.plan_envelope, os.path.abspath(plan))
        import hashlib
        self.assertEqual(st.plan_sha256, hashlib.sha256(open(plan, "rb").read()).hexdigest())
        self.assertEqual(st.budget, {"max_parallel": 2})
        self.assertEqual(C.git(self.root, "status", "--porcelain").stdout.strip(), "")

    def test_init_without_a_grant_prints_gate_ask_and_changes_nothing(self):
        plan = write_plan_envelope(self.root, ONE_OK)
        rc, out, _ = self.run_main(["init", "--plan", plan, "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("GATE: ASK", out)
        self.assertIn("no-grant", out)
        self.assertEqual(self.head(), "factory/p")
        self.assertEqual(sorted(self.branches()), ["factory/p", "main"])
        self.assertIsNone(C.current_run(self.root))

    def test_init_on_the_default_branch_asks(self):
        plan = write_plan_envelope(self.root, ONE_OK)
        write_grant(self.root, plan, branch_pattern="*")
        C.git(self.root, "checkout", "-q", "main")
        rc, out, _ = self.run_main(["init", "--plan", plan, "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("reason=default-branch", out)
        self.assertEqual(self.head(), "main")
        self.assertIsNone(C.current_run(self.root))

    def test_init_on_a_branch_outside_the_pattern_asks(self):
        plan = write_plan_envelope(self.root, ONE_OK)
        write_grant(self.root, plan)
        C.git(self.root, "checkout", "-q", "-b", "feature/x")
        rc, out, _ = self.run_main(["init", "--plan", plan, "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("reason=branch", out)
        self.assertIsNone(C.current_run(self.root))

    def test_init_refuses_when_the_run_branch_would_not_match_the_pattern(self):
        plan = write_plan_envelope(self.root, ONE_OK)  # title T: run branch factory/t
        write_grant(self.root, plan, branch_pattern="factory/p")
        rc, out, err = self.run_main(["init", "--plan", plan, "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("factory/t", out + err)
        self.assertIn("branch_pattern", out + err)
        self.assertEqual(self.head(), "factory/p")
        self.assertNotIn("factory/t", self.branches())
        self.assertIsNone(C.current_run(self.root))

    def test_a_grant_for_another_plan_does_not_cover_this_one(self):
        mine = write_plan_envelope(self.root, ONE_OK)
        other = write_plan_envelope(self.root, TWO_OK)
        write_grant(self.root, other)
        rc, out, _ = self.run_main(["init", "--plan", mine, "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("reason=subject", out)
        self.assertIsNone(C.current_run(self.root))

    def test_init_refuses_an_envelope_of_another_kind(self):
        plan = write_plan_envelope(self.root, ONE_OK,
                                   kind="https://github.com/dhanesh/agent-skills/skill-contract/other/v1")
        write_grant(self.root, plan)
        rc, _, err = self.run_main(["init", "--plan", plan, "--root", self.root])
        self.assertEqual(rc, 2)
        self.assertIn("task-plan/v1", err)
        self.assertIsNone(C.current_run(self.root))

    def test_init_refuses_an_invalid_envelope(self):
        plan = write_plan_envelope(self.root, ONE_OK)
        write_grant(self.root, plan)
        with open(plan) as f:
            doc = json.load(f)
        del doc["predicate"]["id"]
        bad = os.path.join(self.root, "bad.json")
        with open(bad, "w") as f:
            json.dump(doc, f)
        rc, out, err = self.run_main(["init", "--plan", bad, "--root", self.root])
        self.assertEqual(rc, 2)
        self.assertIn("FAIL: C", out + err)
        self.assertIsNone(C.current_run(self.root))

    def test_init_refuses_a_stale_plan(self):
        plan = write_plan_envelope(self.root, ONE_OK)
        write_grant(self.root, plan)
        with open(os.path.join(self.root, "docs", "spec.md"), "a") as f:
            f.write("changed\n")
        rc, _, err = self.run_main(["init", "--plan", plan, "--root", self.root])
        self.assertEqual(rc, 2)
        self.assertIn("docs/spec.md", err)
        self.assertIsNone(C.current_run(self.root))

    def test_init_refuses_a_plan_with_a_cycle_before_any_gate(self):
        plan = write_plan_envelope(self.root, {"T1": (["T2"], []), "T2": (["T1"], [])})
        rc, _, err = self.run_main(["init", "--plan", plan, "--root", self.root])
        self.assertEqual(rc, 2)  # invalid input, even with no grant
        self.assertIn("cycle", err)

    def test_init_refuses_an_existing_run_branch(self):
        plan = write_plan_envelope(self.root, ONE_OK)
        write_grant(self.root, plan)
        C.git(self.root, "branch", "factory/t")
        rc, _, err = self.run_main(["init", "--plan", plan, "--root", self.root])
        self.assertEqual(rc, 2)
        self.assertIn("factory/t", err)
        self.assertEqual(self.head(), "factory/p")
        self.assertIsNone(C.current_run(self.root))

    def test_init_outside_git_exits_2(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "docs"))
        with open(os.path.join(d, "docs", "spec.md"), "w") as f:
            f.write("# Spec\n")
        plan = write_plan_envelope(d, ONE_OK)
        write_grant(d, plan)
        rc, _, err = self.run_main(["init", "--plan", plan, "--root", d])
        self.assertEqual(rc, 2)
        self.assertIn("git", err)
        self.assertIsNone(C.current_run(d))

    def test_budget_merges_over_the_grant_budget(self):
        plan = write_plan_envelope(self.root, ONE_OK)
        g = write_grant(self.root, plan)
        # the grant's budget comes from its payload; rewrite this one with a budget
        os.unlink(g)
        write_grant(self.root, plan, budget={"max_dispatches": 5, "max_parallel": 3,
                                             "wall_clock_min": 30})
        rc, _, _ = self.run_main(["init", "--plan", plan, "--root", self.root,
                                  "--budget", '{"max_parallel": 1, "max_usd": 2.5}'])
        self.assertEqual(rc, 0)
        self.assertEqual(C.State.load(C.state_path(self.root)).budget,
                         {"max_dispatches": 5, "max_parallel": 1, "wall_clock_min": 30,
                          "max_usd": 2.5})

    def test_bad_budgets_exit_2_and_create_nothing(self):
        plan = write_plan_envelope(self.root, ONE_OK)
        write_grant(self.root, plan)
        for bad in ("{", "[]", '{"max_parallel": 0}', '{"max_parallel": "2"}',
                    '{"max_dispatches": -1}', '{"max_repairs_per_task": 1.5}',
                    '{"wall_clock_min": -1}', '{"max_usd": -0.01}', '{"max_tokens": true}',
                    '{"max_dispach": 3}'):
            rc, _, err = self.run_main(["init", "--plan", plan, "--root", self.root,
                                        "--budget", bad])
            self.assertEqual(rc, 2, bad)
            self.assertTrue(err, bad)
        self.assertEqual(self.head(), "factory/p")
        self.assertIsNone(C.current_run(self.root))


@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class GateTests(unittest.TestCase):
    def setUp(self):
        self.root = repo_with_plan()
        self.plan = write_plan_envelope(self.root, TWO_OK)
        write_grant(self.root, self.plan)
        self.assertEqual(C.main(["init", "--plan", self.plan, "--root", self.root]), 0)

    def out(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = C.main(argv)
        return rc, buf.getvalue()

    def events(self):
        st = C.State.load(C.state_path(self.root))
        with open(st.log_path) as f:
            return [json.loads(l) for l in f]

    def test_gate_returns_the_checker_exit_and_its_last_line(self):
        rc, last = C.gate(self.root, "local_reversible")
        self.assertEqual(rc, 0)
        self.assertTrue(last.startswith("GRANT: COVERED"), last)
        rc, last = C.gate(self.root, "deploy")
        self.assertEqual(rc, 3)
        self.assertIn("reason=gate-ask", last)

    def test_a_planted_fsmonitor_never_runs_under_the_gate(self):
        # The vendored checker runs its own git probes in the executor-writable repo.
        m = os.path.join(tempfile.mkdtemp(), "ran")
        script = os.path.join(tempfile.mkdtemp(), "fsmon.sh")
        with open(script, "w") as f:
            f.write("#!/bin/sh\ntouch %s\nexit 1\n" % m)
        os.chmod(script, 0o755)
        subprocess.run(["git", "-C", self.root, "config", "core.fsmonitor", script], check=True)
        rc, last = C.gate(self.root, "local_reversible")
        self.assertEqual(rc, 0, last)
        rc, last = C.gate(self.root, "push_branch")  # the CI-config probe diffs the tree
        self.assertFalse(os.path.exists(m), "the planted fsmonitor ran under check-grant")

    def test_gate_passes_the_plan_as_subject(self):
        other = write_plan_envelope(self.root, ONE_OK)
        rc, last = C.gate(self.root, "local_reversible", subject=os.path.relpath(
            other, self.root))
        self.assertEqual(rc, 3)
        self.assertIn("reason=subject", last)

    def test_gate_command_prints_covered_or_ask(self):
        rc, out = self.out(["gate", "--action", "local_reversible", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertRegex(out.strip(), r"^GATE: COVERED id=\S+ class=local_reversible gate=grant$")
        rc, out = self.out(["gate", "--action", "merge", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertRegex(out.strip(), r"^GATE: ASK id=\S+ reason=gate-ask$")

    def test_a_gate_ask_is_logged_and_changes_nothing(self):
        revoke(self.root)
        rc, out = self.out(["start", "T1", "--root", self.root])
        self.assertEqual(rc, 3)
        st = C.State.load(C.state_path(self.root))
        self.assertEqual((st.tasks["T1"]["status"], st.dispatches), ("pending", 0))
        self.assertFalse(os.path.exists(os.path.join(st.dir, "wt", "T1")))
        gates = [e for e in self.events() if e["event"] == "gate"]
        self.assertEqual([(g["action"], g["ok"]) for g in gates],
                         [("local_reversible", True), ("local_reversible", False)])  # init, start
        self.assertIn("revoked", gates[1]["result"])
        self.assertEqual(st.stopped["reason"], "grant_ask")
        self.assertIn("STOP: grant_ask", out)
        self.assertNotIn("dispatch", [e["event"] for e in self.events()])

    def test_a_new_grant_and_resume_lift_a_grant_ask_stop(self):
        revoke(self.root)
        self.assertEqual(C.main(["start", "T1", "--root", self.root]), 3)
        rc, out = self.out(["verify", "T1", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("STOP: grant_ask", out)
        write_grant(self.root, self.plan)
        rc, out = self.out(["resume", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("READY: T1 T2", out)
        self.assertIsNone(C.State.load(C.state_path(self.root)).stopped)
        self.assertEqual(C.main(["start", "T1", "--root", self.root]), 0)

    def test_resume_does_not_lift_any_other_stop(self):
        st = C.State.load(C.state_path(self.root))
        st.stopped = {"reason": "budget_wall_clock", "at": "t"}
        st.save()
        rc, out = self.out(["resume", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("STOP: budget_wall_clock", out)
        self.assertNotIn("READY:", out)
        self.assertEqual(C.State.load(C.state_path(self.root)).stopped["reason"],
                         "budget_wall_clock")

    def test_merge_is_gated(self):
        self.assertEqual(C.main(["start", "T1", "--root", self.root]), 0)
        st = C.State.load(C.state_path(self.root))
        wt = st.tasks["T1"]["worktree"]
        with open(os.path.join(wt, "b.txt"), "w") as f:
            f.write("b\n")
        C.git(wt, "add", "-A")
        subprocess.run(["git", "-C", wt, "commit", "-q", "-m", "t"], check=True,
                       env=dict(os.environ, **GIT))
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 0)
        self.assertEqual(C.main(["review", "T1", "--verdict", "pass", "--root", self.root]), 0)
        before = C.git(self.root, "rev-parse", "HEAD").stdout.strip()
        revoke(self.root)
        rc, out = self.out(["merge", "T1", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("GATE: ASK", out)
        self.assertIn("revoked", out)
        self.assertEqual(C.git(self.root, "rev-parse", "HEAD").stdout.strip(), before)
        self.assertEqual(C.State.load(C.state_path(self.root)).tasks["T1"]["status"],
                         "reviewing")

    def test_a_changed_spec_stops_the_next_gate(self):
        with open(os.path.join(self.root, "docs", "spec.md"), "a") as f:
            f.write("edited\n")
        rc, out = self.out(["start", "T1", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("reason=stale", out)

    def test_resume_rechecks_the_grant(self):
        rc, out = self.out(["resume", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("READY: T1 T2", out)
        revoke(self.root)
        rc, out = self.out(["resume", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("GATE: ASK", out)
        self.assertNotIn("READY:", out)


@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class StopRuleTests(unittest.TestCase):
    def make(self, tasks, budget=None):
        self.root = repo_with_plan()
        plan = {"title": "T", "spec": "docs/spec.md", "tasks": [
            {"id": k, "verify": [{"text": "t", "command": ["true"]}], "depends_on": d}
            for k, d in tasks.items()]}
        self.st, _ = new_run(self.root, plan, budget=budget)
        return self.st

    def out(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = C.main(argv)
        return rc, buf.getvalue()

    def test_check_stop_order_wall_clock_then_dispatches_then_no_ready(self):
        st = self.make({"T1": []}, budget={"wall_clock_min": 1, "max_dispatches": 0})
        self.assertEqual(C.check_stop(st), "budget_dispatches")
        st.created_at = _z(CC.utc_now() - timedelta(minutes=2))
        self.assertEqual(C.check_stop(st), "budget_wall_clock")
        st.budget = {}
        st.set_status("T1", "parked", park_reason="x")
        self.assertEqual(C.check_stop(st), "no_ready_tasks")
        st.budget = {"max_dispatches": 0}
        self.assertEqual(C.check_stop(st), "no_ready_tasks")  # nothing left to dispatch

    def test_check_stop_is_none_while_work_remains(self):
        st = self.make({"T1": [], "T2": ["T1"]}, budget={"wall_clock_min": 60,
                                                         "max_dispatches": 1})
        self.assertIsNone(C.check_stop(st))
        st.set_status("T1", "running")
        st.dispatches = 1
        # T1 is in flight: the dispatch budget blocks new starts, not T1's own verify
        self.assertIsNone(C.check_stop(st))

    def test_wall_clock_is_measured_from_init(self):
        st = self.make({"T1": []}, budget={"wall_clock_min": 10})
        st.created_at = _z(CC.utc_now() - timedelta(minutes=9))
        self.assertIsNone(C.check_stop(st))
        st.created_at = _z(CC.utc_now() - timedelta(minutes=10))
        self.assertEqual(C.check_stop(st), "budget_wall_clock")

    def test_next_records_the_stop_and_every_later_step_refuses(self):
        st = self.make({"T1": []}, budget={"wall_clock_min": 0})
        rc, out = self.out(["next", "--root", self.root])
        self.assertEqual((rc, out.strip()), (3, "STOP: budget_wall_clock"))
        st = C.State.load(st.state_path)
        self.assertEqual(st.stopped["reason"], "budget_wall_clock")
        self.assertIn("stop", [json.loads(l)["event"] for l in open(st.log_path)])
        rc, out = self.out(["start", "T1", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("STOP: budget_wall_clock", out)
        rc, out = self.out(["next", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("STOP: budget_wall_clock", out)

    def test_start_past_the_wall_clock_stops_the_run(self):
        st = self.make({"T1": []}, budget={"wall_clock_min": 0})
        rc, out = self.out(["start", "T1", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("STOP: budget_wall_clock", out)
        st = C.State.load(st.state_path)
        self.assertEqual((st.tasks["T1"]["status"], st.dispatches), ("pending", 0))
        self.assertEqual(st.stopped["reason"], "budget_wall_clock")

    def test_the_dispatch_cap_lets_in_flight_work_finish_then_stops(self):
        st = self.make({"T1": [], "T2": []}, budget={"max_dispatches": 1})
        rc, out = self.out(["next", "--root", self.root])
        self.assertEqual((rc, out.strip()), (0, "READY: T1"))  # capped by the budget left
        self.assertEqual(C.main(["start", "T1", "--root", self.root]), 0)
        rc, out = self.out(["next", "--root", self.root])
        self.assertEqual((rc, out.strip()), (0, ""))  # T1 in flight, nothing more to start
        rc, out = self.out(["start", "T2", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("STOP: budget_dispatches", out)
        st = C.State.load(st.state_path)
        self.assertIsNone(st.stopped)  # T1 may still be verified, reviewed and merged
        self.assertEqual(st.tasks["T2"]["status"], "pending")
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 0)
        C.main(["park", "T1", "--reason", "x", "--root", self.root])
        rc, out = self.out(["next", "--root", self.root])
        self.assertEqual((rc, out.strip()), (3, "STOP: budget_dispatches"))
        self.assertEqual(C.State.load(st.state_path).stopped["reason"], "budget_dispatches")

    def test_the_first_failing_verify_is_not_a_repair(self):
        self.root = repo_with_plan()
        plan = {"title": "T", "spec": "docs/spec.md", "tasks": [
            {"id": "T1", "verify": [{"text": "t", "command": ["false"]}], "depends_on": []},
            {"id": "T2", "verify": [{"text": "t", "command": ["true"]}], "depends_on": ["T1"]},
            {"id": "T3", "verify": [{"text": "t", "command": ["true"]}], "depends_on": []}]}
        st, _ = new_run(self.root, plan, budget={"max_repairs_per_task": 2})
        C.main(["start", "T1", "--root", self.root])
        task = lambda: C.State.load(st.state_path).tasks["T1"]
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)
        self.assertEqual((task()["repairs"], task()["status"]), (0, "verifying"))
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)
        self.assertEqual((task()["repairs"], task()["status"]), (1, "verifying"))
        rc, out = self.out(["verify", "T1", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("PARK: T1 verify_red_after_repairs", out)
        again = C.State.load(st.state_path)
        self.assertEqual((again.tasks["T1"]["repairs"], again.tasks["T1"]["park_reason"]),
                         (2, "verify_red_after_repairs"))
        self.assertEqual(again.tasks["T2"]["status"], "blocked")
        self.assertIsNone(again.stopped)  # parking never stops the run
        rc, out = self.out(["next", "--root", self.root])
        self.assertEqual((rc, out.strip()), (0, "READY: T3"))

    def test_a_refused_verify_counts_as_a_failure(self):
        st = self.make({"T1": []}, budget={"max_repairs_per_task": 1})
        C.main(["start", "T1", "--root", self.root])
        wt = C.State.load(st.state_path).tasks["T1"]["worktree"]
        with open(os.path.join(wt, "dirty.txt"), "w") as f:
            f.write("x")
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)
        rc, out = self.out(["verify", "T1", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("PARK: T1 verify_red_after_repairs", out)

    def test_without_a_repair_budget_verify_never_parks(self):
        st = self.make({"T1": []})
        C.main(["start", "T1", "--root", self.root])
        wt = C.State.load(st.state_path).tasks["T1"]["worktree"]
        with open(os.path.join(wt, "dirty.txt"), "w") as f:
            f.write("x")
        for _ in range(3):
            self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)
        t = C.State.load(st.state_path).tasks["T1"]
        self.assertEqual((t["status"], t["repairs"]), ("verifying", 2))

    def test_decision_records_the_question_and_blocks_dependents(self):
        st = self.make({"T1": [], "T2": ["T1"], "T3": []})
        rc, out = self.out(["decision", "T1", "--question", "Postgres or SQLite?",
                            "--root", self.root])
        self.assertEqual((rc, out.strip()), (0, "PARK: T1 new_human_decision"))
        st = C.State.load(st.state_path)
        self.assertEqual((st.tasks["T1"]["status"], st.tasks["T1"]["park_reason"]),
                         ("parked", "new_human_decision"))
        self.assertEqual(st.tasks["T1"]["question"], "Postgres or SQLite?")
        self.assertEqual(st.tasks["T2"]["status"], "blocked")
        self.assertIsNone(st.stopped)
        ev = [json.loads(l) for l in open(st.log_path)]
        self.assertIn({"task": "T1", "question": "Postgres or SQLite?"},
                      [{k: e[k] for k in ("task", "question")} for e in ev
                       if e["event"] == "decision"])
        rc, out = self.out(["status", "--root", self.root])
        self.assertIn('STATUS: T1 parked reason="new_human_decision"', out)
        self.assertIn('question="Postgres or SQLite?"', out)

    def test_decision_refuses_bad_input(self):
        st = self.make({"T1": []})
        self.assertEqual(C.main(["decision", "T9", "--question", "q", "--root", self.root]), 2)
        self.assertEqual(C.main(["decision", "T1", "--question", "  ", "--root", self.root]), 2)
        st.set_status("T1", "proven")
        st.save()
        self.assertEqual(C.main(["decision", "T1", "--question", "q", "--root", self.root]), 2)

    def test_status_always_says_tokens_and_dollars_are_not_enforced(self):
        self.make({"T1": []})
        rc, out = self.out(["status", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("STATUS: budget max_tokens/max_usd recorded, not enforced "
                      "(the runtime does not expose usage)", out.splitlines())


class NonGitStopTests(unittest.TestCase):
    """check_stop is pure: it needs no git and no grant."""

    def test_state_path_is_the_newest_runs_state(self):
        tmp = tempfile.mkdtemp()
        self.assertIsNone(C.state_path(tmp))
        plan = {"tasks": [{"id": "T1", "depends_on": []}]}
        a = C.State.new(root=tmp, run_id="run-20260101T000000Z-000001", plan=plan,
                        plan_envelope="e", plan_sha256="0" * 64, grant_id="g",
                        run_branch="f", base_branch="m", budget={})
        self.assertEqual(C.state_path(tmp), a.state_path)
        b = C.State.new(root=tmp, run_id="run-20260102T000000Z-000001", plan=plan,
                        plan_envelope="e", plan_sha256="0" * 64, grant_id="g",
                        run_branch="f", base_branch="m", budget={})
        self.assertEqual(C.state_path(tmp), b.state_path)

    def test_state_new_rejects_a_bad_budget(self):
        tmp = tempfile.mkdtemp()
        for bad in ({"max_dispatches": -1}, {"nope": 1}, {"wall_clock_min": "5"}):
            with self.assertRaises(C.PlanError, msg=repr(bad)):
                C.State.new(root=tmp, run_id=C.new_run_id(),
                            plan={"tasks": [{"id": "T1", "depends_on": []}]},
                            plan_envelope="e", plan_sha256="0" * 64, grant_id="g",
                            run_branch="f", base_branch="m", budget=bad)


if __name__ == "__main__":
    unittest.main()
