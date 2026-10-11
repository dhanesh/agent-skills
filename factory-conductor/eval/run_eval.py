#!/usr/bin/env python3
"""Outcome eval for factory-conductor (see the repo's docs/eval-standard.md).

Stdlib-only, offline, deterministic; every scenario runs in a tempdir git repo built by
conductor_testkit (a shipped asset), imported directly the way world-model-ledger's
harness imports its own assets — this IS the skill's shipped tooling, not a stand-in for
it. Prints one `CHECK: <name> — PASS|FAIL` line per check and a final
`EVAL_RESULT: PASS (n/n checks)` line; exits 0 iff every check passed. Every scratch
repo is removed when its scenario ends (no repo writes; the eval owns only its tempdirs).

One shared three-task run (dep_order_and_finish_arm) carries three assertions at once —
a dependency runs in the right order, `finish` writes an envelope check-envelope
validates, and a task whose verify never passes is never merged — because driving it
once covers all three (see docs/eval-standard.md: "share one fixture setup where you
can"). Seven more short scenarios each isolate one negative requirement: a decision parks
a task without stopping the run; a REVOKED grant stops the run at the next gate
(GATE: ASK reason=revoked) and, as its own separate fixture, an EXPIRED grant does too
(GATE: ASK reason=expired) — these are distinct check-grant outcomes, kept as distinct
checks rather than one merged "expired/revoked" check; max_dispatches: 1 stops the run;
a merge conflict parks the task and leaves the run branch clean; a task whose status
is not `reviewing` (built white-box, through conductor.State) is refused by `merge`
without touching the run branch; `init` refuses a plan with a null verify command
(exit 2, no run), so such a task is never started or merged; and two tasks that each
pass alone but break each other once merged are never pushed (finish re-verifies the
merged run branch first), sanity-checked against the same fixture with a check the
merged result passes, which DOES push to the scratch bare origin. The evidence-gate arm
drives one gated task per case through the gate suite's fixtures: fresh, independent,
SHA-bound evidence with a green doctor merges; evidence from another head, a verdict from
the writer, a red doctor and an artifact removed after the verdict never do. The spend
metering arm drives one task through to its merge under three budgets: an enforced
run whose reported usage reaches max_tokens stops with budget_tokens; as negatives, an
enforced run nobody metered stops with budget_unmetered, and the same run without
`enforce` goes on.

The scheduled re-entry checks (`conductor watch`) share one fixture shape, a one-task
run aged past stall_min under a grant with a reentry block: one watch starts exactly one
stub agent (it sleeps at most 2 s), and a second tick sees it live; and, as negatives,
nothing starts while a reentry lease's pid lives, on a grant_ask stop, under a revoked
grant, or past max_reentries (which records reentry_exhausted), and a grant whose
agent_cmd is a shell is invalid. Every timer call goes to a temp home, dry-run, as cron.

The money ledger arms drive assets/ledger.py in-process on synthetic amounts (no payment
tool, no model): a positive flow (register, check, record, status) and, as negatives, a
stale budget never gives NOT_REFUSED, an agent (and a lookalike name) cannot record spend or
withdraw, a withdrawn proposal's recorded spend still counts, a re-run reserves once and a second proposal that only fits alone is refused, a
replay after a cap cut or after a record that pushes the total over is refused, a recurring
proposal that goes over in month 7 only is refused, the total cap refuses when no category
cap does, one minor unit over a cap is refused while exactly at the cap is not, another
currency and a past month are refused with exit 3 (a record in another currency too, and a
record for a month that has not started is a STOP), a raise registered without --reason is
refused, a state that its log does not match refuses the check, an approval word in an id
or a --by name is a STOP, --today without the test switch exits 2, and no output of any
command carries an approval word or a pass writes an envelope.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import timedelta
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
ASSETS = os.path.join(SKILL, "assets")
sys.path.insert(0, ASSETS)
import conductor as C  # noqa: E402
import contract_check as CC  # noqa: E402
import reentry as R  # noqa: E402
import conductor_testkit as TK  # noqa: E402
import ledger_testkit as LK  # noqa: E402

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = " (%s)" % detail if detail else ""
    print("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL", suffix))


def task(tid, deps, cmd):
    return {"id": tid, "requirement_ids": ["R1"], "title": "task " + tid,
            "verify": [{"text": "t", "command": cmd}], "depends_on": deps}


def plan(*tasks, title="E"):
    return {"title": title, "spec": TK.SPEC, "coverage": {}, "uncovered": [], "tasks": list(tasks)}


def run(argv):
    """Run conductor.main(argv), capturing stdout/stderr. (exit code, stdout, stderr)."""
    buf, ebuf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(ebuf):
        rc = C.main(argv)
    return rc, buf.getvalue(), ebuf.getvalue()


def commit_in(directory, name, text):
    """Write and commit a real file in a task worktree — a merge is never a no-commits
    park because it never got any real work."""
    with open(os.path.join(directory, name), "w") as f:
        f.write(text)
    subprocess.run(["git", "-C", directory, "add", "-A"], check=True)
    subprocess.run(["git", "-C", directory, "commit", "-q", "-m", "c"], check=True,
                   env=dict(os.environ, **TK.GIT))


def wt(root, tid):
    return C.State.load(C.state_path(root)).tasks[tid]["worktree"]


def status(root, tid):
    return C.State.load(C.state_path(root)).tasks[tid]["status"]


# ── the shared three-task run ────────────────────────────────────────────────
def dep_order_and_finish_arm():
    """T2 depends on T1; T3 is independent and its verify never passes. One run proves:
    dependency order, `finish` writing a validating envelope, and a failing-verify task
    never being merged — all from the SAME drive, so it counts as three checks, not
    three fixtures."""
    root = TK.repo()
    try:
        plan_env = TK.write_plan_envelope(root, plan=plan(
            task("T1", [], ["true"]), task("T2", ["T1"], ["true"]), task("T3", [], ["false"])))
        # No push_branch/open_pr: finish only needs to write and validate the envelope
        # here, never a real `git push` or `gh pr create` against a fixture with no
        # remote — the push/PR contract is the vendored checker's and finish's own
        # concern, exercised by test_conductor_finish.py.
        TK.write_grant(root, plan_env, budget={"max_repairs_per_task": 1},
                       policy={"read_only": "auto", "local_reversible": "grant"})

        rc, out, err = run(["init", "--plan", plan_env, "--root", root])
        if rc != 0:
            check("init starts the run", False, err.strip()[-160:])
            return

        rc, out, err = run(["next", "--root", root])
        check("a plan with a dependency schedules the independent tasks first, in "
              "order (T2 waits for T1)",
              rc == 0 and out.strip() == "READY: T1 T3", out.strip())

        run(["start", "T1", "--root", root])
        run(["start", "T3", "--root", root])
        commit_in(wt(root, "T1"), "t1.txt", "one\n")
        commit_in(wt(root, "T3"), "t3.txt", "three\n")  # real work; its verify still fails

        run(["verify", "T1", "--root", root])
        run(["review", "T1", "--verdict", "pass", "--root", root])
        run(["merge", "T1", "--root", root])

        run(["verify", "T3", "--root", root])  # 1st failure: a repair is dispatched
        rc, out, err = run(["verify", "T3", "--root", root])  # 2nd: cap is 1, it parks
        t3_parked_after_two_failures = (rc == 3 and status(root, "T3") == "parked"
                                        and "PARK: T3 verify_red_after_repairs" in out)

        run(["next", "--root", root])
        run(["start", "T2", "--root", root])
        commit_in(wt(root, "T2"), "t2.txt", "two\n")
        run(["verify", "T2", "--root", root])
        run(["review", "T2", "--verdict", "pass", "--root", root])
        run(["merge", "T2", "--root", root])
        run(["next", "--root", root])  # STOP: no_ready_tasks

        check("NEGATIVE: a task whose verify never passes is never merged (status "
              "stays parked, never proven, and its file never reaches the run branch)",
              t3_parked_after_two_failures and status(root, "T3") != "proven"
              and not os.path.exists(os.path.join(root, "t3.txt")), status(root, "T3"))

        # rc is 3 here (a GATE: ASK for push_branch, since the grant covers only
        # local_reversible): the envelope is still written before any remote step runs.
        rc, out, err = run(["finish", "--root", root])
        if not any(l.startswith("FINISH: ") for l in out.splitlines()):
            check("finish writes an envelope that check-envelope validates", False,
                  (out + err).strip()[-160:])
            return
        env_path = next(l for l in out.splitlines()
                        if l.startswith("FINISH: "))[len("FINISH: "):]
        rep = CC.check_envelope(env_path, root=root)
        with open(env_path, encoding="utf-8") as f:
            doc = json.load(f)
        with open(C.State.load(C.state_path(root)).log_path, "rb") as f:
            data = f.read()
        pay = doc["predicate"]["payload"]
        prefix = data[:pay["log_bytes"]]
        log_ok = hashlib.sha256(prefix).hexdigest() == pay["log_sha256"]
        tasks_by_id = {t["id"]: t for t in pay["tasks"]}
        check("finish writes a run-result/v1 envelope that check-envelope validates, "
              "with two proven tasks, one parked, and a log_sha256 matching the log file",
              rep["violations"] == [] and rep["stale"] == [] and CC.check_statement(doc) == []
              and tasks_by_id["T1"]["status"] == "proven"
              and tasks_by_id["T2"]["status"] == "proven"
              and tasks_by_id["T3"]["status"] == "parked" and log_ok,
              "violations=%r stale=%r" % (rep["violations"], rep["stale"]))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── negative scenarios, each its own short fixture ───────────────────────────
def decision_parks_and_run_continues_arm():
    root = TK.repo()
    try:
        plan_env = TK.write_plan_envelope(root, plan=plan(
            task("T1", [], ["true"]), task("T2", [], ["true"])))
        TK.write_grant(root, plan_env)
        run(["init", "--plan", plan_env, "--root", root])
        rc, out, err = run(["decision", "T1", "--question", "which approach?", "--root", root])
        parked = (rc == 0 and out.strip() == "PARK: T1 new_human_decision"
                 and status(root, "T1") == "parked")
        rc, out, err = run(["next", "--root", root])
        check("NEGATIVE: a task that needs a decision parks (new_human_decision) and "
              "the run continues (a sibling task is still offered)",
              parked and rc == 0 and out.strip() == "READY: T2",
              "parked=%r rc=%r out=%r" % (parked, rc, out.strip()))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def revoked_grant_stops_at_next_gate_arm():
    root = TK.repo()
    try:
        plan_env = TK.write_plan_envelope(root, plan=plan(task("T1", [], ["true"])))
        TK.write_grant(root, plan_env)
        run(["init", "--plan", plan_env, "--root", root])
        TK.revoke(root)  # the newest grant is gone; the next gated step must ask
        rc, out, err = run(["start", "T1", "--root", root])
        check("NEGATIVE: a revoked grant stops the run at the next gate "
              "(GATE: ASK reason=revoked, grant_ask)",
              rc == 3 and "GATE: ASK" in out and "reason=revoked" in out
              and status(root, "T1") == "pending"
              and C.State.load(C.state_path(root)).stopped["reason"] == "grant_ask",
              out.strip()[-160:])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def expired_grant_stops_at_next_gate_arm():
    """Distinct from revocation: the newest grant for this plan is simply past its own
    expires_at (test_conductor_policy.py's test_an_expired_grant_stops_the_next_gate
    pattern — init under a valid grant, then write a NEWER grant whose expiry is
    already in the past, so it is still the newest head check-grant reads)."""
    root = TK.repo()
    try:
        plan_env = TK.write_plan_envelope(root, plan=plan(task("T1", [], ["true"])))
        TK.write_grant(root, plan_env, minutes=1)  # a valid grant to init under
        run(["init", "--plan", plan_env, "--root", root])
        TK.write_grant(root, plan_env, minutes=-1)  # a newer grant, already expired
        rc, out, err = run(["start", "T1", "--root", root])
        check("NEGATIVE: an expired grant stops the run at the next gate "
              "(GATE: ASK reason=expired, grant_ask)",
              rc == 3 and "GATE: ASK" in out and "reason=expired" in out
              and status(root, "T1") == "pending"
              and C.State.load(C.state_path(root)).stopped["reason"] == "grant_ask",
              out.strip()[-160:])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def max_dispatches_one_stops_the_run_arm():
    root = TK.repo()
    try:
        plan_env = TK.write_plan_envelope(root, plan=plan(
            task("T1", [], ["true"]), task("T2", [], ["true"])))
        TK.write_grant(root, plan_env, budget={"max_dispatches": 1})
        run(["init", "--plan", plan_env, "--root", root])
        run(["start", "T1", "--root", root])  # spends the only dispatch
        commit_in(wt(root, "T1"), "t1.txt", "one\n")
        run(["verify", "T1", "--root", root])  # passes, but no reviewer dispatch left: parks
        rc, out, err = run(["next", "--root", root])
        check("NEGATIVE: max_dispatches: 1 stops the run (budget_dispatches) even "
              "though a sibling task is still ready",
              rc == 3 and out.strip() == "STOP: budget_dispatches"
              and status(root, "T1") == "parked", out.strip())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _spend_run(budget, meter):
    """Two tasks under a grant whose budget is `budget`. T1 runs start -> verify -> review
    pass -> merge; after each finished dispatch (the executor once verify records its
    outcome, the reviewer once review does) `meter` tokens are reported with `usage`, or
    nothing when meter is None. Returns next's (exit code, stdout) and T1's status."""
    root = TK.repo()
    try:
        plan_env = TK.write_plan_envelope(root, plan=plan(
            task("T1", [], ["true"]), task("T2", [], ["true"])))
        TK.write_grant(root, plan_env, budget=budget)
        run(["init", "--plan", plan_env, "--root", root])
        run(["start", "T1", "--root", root])
        commit_in(wt(root, "T1"), "t1.txt", "one\n")
        run(["verify", "T1", "--root", root])
        if meter is not None:
            run(["usage", "T1", "--tokens", str(meter), "--root", root])
        run(["review", "T1", "--verdict", "pass", "--root", root])
        if meter is not None:
            run(["usage", "T1", "--tokens", str(meter), "--root", root])
        run(["merge", "T1", "--root", root])
        rc, out, _ = run(["next", "--root", root])
        return rc, out.strip(), status(root, "T1")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def spend_metering_arm():
    """Spend metering (opt-in, budget.enforce). The positive: an enforced run whose
    claimed usage reaches max_tokens stops with budget_tokens although T2 is ready.
    Negatives: an enforced run with a finished dispatch nobody metered stops with
    budget_unmetered; the same unmetered run without `enforce` goes on (READY: T2)."""
    rc, out, st1 = _spend_run({"enforce": ["tokens"], "max_tokens": 100}, 60)
    check("an enforced run stops at its token cap (budget_tokens) with a task still ready",
          rc == 3 and out == "STOP: budget_tokens" and st1 == "proven", out)
    rc, out, st1 = _spend_run({"enforce": ["tokens"], "max_tokens": 10 ** 6}, None)
    check("NEGATIVE: an unmetered finished dispatch stops an enforced run "
          "(budget_unmetered)", rc == 3 and out == "STOP: budget_unmetered", out)
    rc, out, st1 = _spend_run({"max_tokens": 1}, None)
    check("NEGATIVE: without enforce the same unmetered run goes on, its cap only recorded",
          rc == 0 and out == "READY: T2", out)


def merge_conflict_parks_and_run_branch_stays_clean_arm():
    root = TK.repo()
    try:
        plan_env = TK.write_plan_envelope(root, plan=plan(task("T1", [], ["true"])))
        TK.write_grant(root, plan_env)
        run(["init", "--plan", plan_env, "--root", root])
        run(["start", "T1", "--root", root])
        commit_in(wt(root, "T1"), "a.txt", "task\n")  # conflicts with a.txt on the run branch
        commit_in(root, "a.txt", "run\n")
        run(["verify", "T1", "--root", root])
        run(["review", "T1", "--verdict", "pass", "--root", root])
        rc, out, err = run(["merge", "T1", "--root", root])
        clean = C.git(root, "status", "--porcelain").stdout.strip() == ""
        check("NEGATIVE: a merge conflict parks the task (merge-conflict) and leaves "
              "the run branch clean",
              rc == 3 and status(root, "T1") == "parked"
              and C.State.load(C.state_path(root)).tasks["T1"]["park_reason"]
              == "merge-conflict" and clean, out.strip())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def merge_refuses_a_task_not_in_reviewing_status_arm():
    """Isolates conductor.py's status guard in cmd_merge (`_load_task(args,
    ("reviewing",))`) from every OTHER merge precondition: after a real passing verify
    and a real passing review, the task's status is moved off `reviewing` — white-box,
    through conductor.State, the pattern test_conductor_git.py uses — while
    verify_runs, verified_head and the review verdict are left exactly as recorded.
    Widening that status guard to accept the mutated status would let this task merge
    anyway (every other precondition still holds), so this check is the one thing in
    the eval that goes red under that specific mutation."""
    root = TK.repo()
    try:
        plan_env = TK.write_plan_envelope(root, plan=plan(task("T1", [], ["true"])))
        TK.write_grant(root, plan_env)
        run(["init", "--plan", plan_env, "--root", root])
        run(["start", "T1", "--root", root])
        commit_in(wt(root, "T1"), "t1.txt", "one\n")
        run(["verify", "T1", "--root", root])
        run(["review", "T1", "--verdict", "pass", "--root", root])
        before = C.git(root, "rev-parse", "HEAD").stdout.strip()

        st = C.State.load(C.state_path(root))
        if st.tasks["T1"]["status"] != "reviewing":
            check("merge refuses a task not in reviewing status", False,
                  "setup failed: status is %r, not reviewing" % st.tasks["T1"]["status"])
            return
        st.set_status("T1", "running")  # every other merge precondition still holds
        st.save()

        rc, out, err = run(["merge", "T1", "--root", root])
        after = C.git(root, "rev-parse", "HEAD").stdout.strip()
        check("NEGATIVE: merge refuses a task whose status is not `reviewing`, even "
              "with a passing verify and review already recorded, and leaves the run "
              "branch unchanged",
              rc == 2 and status(root, "T1") == "running" and after == before, out.strip())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def null_verify_is_refused_at_init_arm():
    root = TK.repo()
    try:
        t1 = task("T1", [], ["true"])
        t1["verify"] = [{"text": "by hand", "command": None}]  # the verify command is null
        plan_env = TK.write_plan_envelope(root, plan=plan(t1))
        TK.write_grant(root, plan_env)
        before = C.git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        rc, out, err = run(["init", "--plan", plan_env, "--root", root])
        after = C.git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        check("NEGATIVE: init refuses a plan whose verify command is null (exit 2, no run, "
              "no run branch), so such a task can never be started or merged",
              rc == 2 and "FAIL: task T1 verify step 1 has no command" in err
              and C.current_run(root) is None and after == before, err.strip()[-160:])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def vacuous_test_command_cannot_prove_a_task_arm():
    """A test filter that names no test exits 0 having tested nothing; a live trial
    recorded exactly that as proof. verify must fail it, so the task cannot merge."""
    root = TK.repo()
    try:
        empty = ("running 0 tests\n\ntest result: ok. 0 passed; 0 failed; 0 ignored; "
                 "0 measured; 41 filtered out; finished in 0.00s\n")
        plan_env = TK.write_plan_envelope(root, plan=plan(task("T1", [], [
            "{python}", "-c", "import sys; sys.stdout.write(%r)" % empty])))
        TK.write_grant(root, plan_env)
        run(["init", "--plan", plan_env, "--root", root])
        run(["start", "T1", "--root", root])
        commit_in(wt(root, "T1"), "t1.txt", "work\n")
        rc, out, _ = run(["verify", "T1", "--root", root])
        mrc, _, _ = run(["merge", "T1", "--root", root])
        check("NEGATIVE: a verify command that exits 0 after running no tests fails the "
              "step (VERIFY ... ran no tests) and the task cannot merge",
              rc == 3 and "ran no tests" in out and mrc == 2
              and status(root, "T1") == "verifying", out.strip()[-160:])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# Each task adds one file to d/ (which holds base.txt): alone each sees two files, merged
# the run branch holds three.
EXACTLY_TWO = ["{python}", "-c", "import os,sys; sys.exit(0 if len(os.listdir('d')) == 2 else 1)"]
AT_LEAST_TWO = ["{python}", "-c", "import os,sys; sys.exit(0 if len(os.listdir('d')) >= 2 else 1)"]


def _pair_run_pushed(cmd):
    """Drive T1 and T2 (each proven by `cmd`, both started from the same run branch) to
    finish under a grant that covers the push, with a scratch bare repo as origin.
    Returns (finish exit, finish stdout, envelope payload or None, pushed?)."""
    root = TK.repo()
    origin = TK.tmpdir()
    try:
        subprocess.run(["git", "init", "-q", "--bare", origin], check=True)
        subprocess.run(["git", "-C", root, "remote", "add", "origin", origin], check=True)
        os.makedirs(os.path.join(root, "d"))
        commit_in(root, os.path.join("d", "base.txt"), "base\n")
        plan_env = TK.write_plan_envelope(root, plan=plan(task("T1", [], cmd),
                                                          task("T2", [], cmd), title="Pair"))
        TK.write_grant(root, plan_env)  # covers local_reversible, push_branch and open_pr
        run(["init", "--plan", plan_env, "--root", root])
        for t in ("T1", "T2"):
            run(["start", t, "--root", root])
        for t in ("T1", "T2"):
            commit_in(wt(root, t), os.path.join("d", t.lower() + ".txt"), t + "\n")
        for t in ("T1", "T2"):
            run(["verify", t, "--root", root])
            run(["review", t, "--verdict", "pass", "--root", root])
        for t in ("T1", "T2"):
            run(["merge", t, "--root", root])
        run(["next", "--root", root])
        rc, out, err = run(["finish", "--root", root, "--pr-cmd",
                            json.dumps([sys.executable, "-c", "pass"])])
        pay = None
        for line in out.splitlines():
            if line.startswith("FINISH: "):
                with open(line[len("FINISH: "):], encoding="utf-8") as f:
                    pay = json.load(f)["predicate"]["payload"]
        pushed = subprocess.run(["git", "--git-dir", origin, "rev-parse", "-q", "--verify",
                                 "refs/heads/factory/pair"],
                                capture_output=True).returncode == 0
        return rc, out, pay, pushed
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(origin, ignore_errors=True)


def integration_red_is_never_pushed_arm():
    rc, out, pay, pushed = _pair_run_pushed(EXACTLY_TWO)
    g_rc, g_out, g_pay, g_pushed = _pair_run_pushed(AT_LEAST_TWO)  # sanity: this one pushes
    check("NEGATIVE: a run whose merged result fails a proven task's own checks is never "
          "pushed (INTEGRATION: fail, STOP: integration_red, integration.passed false), while "
          "the same fixture with a check the merged result passes is pushed",
          rc == 3 and "STOP: integration_red" in out.splitlines() and not pushed
          and pay is not None and pay["integration"]["passed"] is False
          and g_rc == 0 and g_pushed and g_pay is not None
          and g_pay["integration"]["passed"] is True,
          "red: rc=%r pushed=%r; green: rc=%r pushed=%r" % (rc, pushed, g_rc, g_pushed))


# ── scheduled re-entry (conductor watch) ─────────────────────────────────────
# A stub agent: appends its FACTORY_CONDUCTOR_REENTRY attempt number to a marker file,
# then sleeps (at most 2 s), so "exactly one agent started" is a line count.
REENTRY_STUB = ("import os,sys,time;open(sys.argv[1],'a').write(os.environ.get("
                "'FACTORY_CONDUCTOR_REENTRY','?')+'\\n');time.sleep(float(sys.argv[2]))")
_TIMER_ENV = ("FACTORY_CONDUCTOR_TIMER_HOME", "FACTORY_CONDUCTOR_TIMER_DRYRUN",
              "FACTORY_CONDUCTOR_TIMER_KIND")


@contextlib.contextmanager
def _timer_sandbox():
    """Every timer call watch could make goes to a temp home, dry-run, as cron: the
    real LaunchAgents, systemd units and crontab are never touched."""
    old = {k: os.environ.get(k) for k in _TIMER_ENV}
    os.environ.update({"FACTORY_CONDUCTOR_TIMER_HOME": TK.tmpdir(),
                       "FACTORY_CONDUCTOR_TIMER_DRYRUN": "1",
                       "FACTORY_CONDUCTOR_TIMER_KIND": "cron"})
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _Stalled:
    """A one-task run whose grant carries a reentry block, aged past stall_min: the
    lease, the log's last event and the log file are 45 minutes old."""

    def __init__(self, sleep=2, **block):
        self.root = TK.repo()
        self.st, self.plan = TK.new_run(self.root, plan(task("T1", [], ["true"])))
        self.marker = os.path.join(TK.tmpdir(), "started")
        b = {"agent_cmd": [sys.executable, "-c", REENTRY_STUB, self.marker, str(sleep),
                           "{prompt}"], "stall_min": 30, "max_reentries": 2}
        b.update(block)
        self.block = b
        TK.write_grant(self.root, self.plan, reentry=b)
        self.age()

    def age(self, minutes=45):
        then = C._now() - timedelta(minutes=minutes)
        R.renew_lease(self.st.dir, now=then)
        with open(self.st.log_path, "a") as f:
            f.write(json.dumps({"at": then.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "event": "aged"}) + "\n")
        old = then.timestamp()
        for name in (self.st.log_path, os.path.join(self.st.dir, R.LEASE_FILE)):
            os.utime(name, (old, old))

    def watch(self):
        rc, out, err = run(["watch", "--root", self.root])
        return rc, out.strip()

    def state(self):
        return C.State.load(self.st.state_path)

    def attempts(self):
        return len({e.get("n") for e in self.state().events() if e.get("event") == "reentry"})

    def marker_lines(self, settle=0.0):
        end = time.monotonic() + settle
        while True:
            lines = []
            if os.path.exists(self.marker):
                with open(self.marker) as fh:
                    lines = fh.read().split()
            if lines or time.monotonic() >= end:
                return lines
            time.sleep(0.05)

    def wait_dead(self, deadline=10):
        pid = (R.read_lease(self.st.dir) or {}).get("pid")
        end = time.monotonic() + deadline
        while R.pid_alive(pid) and time.monotonic() < end:
            time.sleep(0.05)

    def close(self):
        pids = {e.get("pid") for e in self.state().events() if e.get("event") == "reentry"}
        pids.add((R.read_lease(self.st.dir) or {}).get("pid"))
        for pid in pids:
            if pid != os.getpid() and R.pid_alive(pid):
                with contextlib.suppress(OSError):
                    os.killpg(pid, signal.SIGKILL)
        shutil.rmtree(self.root, ignore_errors=True)


def reentry_arms():
    with _timer_sandbox():
        # positive: one watch on a stalled run starts exactly one agent
        f = _Stalled()
        try:
            rc, out = f.watch()
            lines = f.marker_lines(settle=10)
            again = f.watch()  # its pid lives: a second tick must not start another
            time.sleep(0.2)
            check("reentry: a stalled run with a reentry block starts exactly one agent",
                  rc == 0 and out.startswith("REENTRY: started 1 pid=")
                  and f.marker_lines() == ["1"] and lines == ["1"]
                  and again == (0, "REENTRY: live") and f.attempts() == 1,
                  "out=%r again=%r marker=%r" % (out, again, f.marker_lines()))
        finally:
            f.close()

        # a reentry lease whose pid lives (this very process) is a driver
        f = _Stalled()
        try:
            R.set_reentry_lease(f.st.dir, 1, os.getpid())
            got = f.watch()
            check("NEGATIVE: reentry never starts while a reentry lease's pid lives",
                  got == (0, "REENTRY: live") and f.attempts() == 0
                  and f.marker_lines(settle=0.3) == [], "got=%r" % (got,))
        finally:
            f.close()

        f = _Stalled()
        try:
            st = f.state()
            st.stopped = {"reason": "grant_ask", "at": "t", "detail": "x"}
            st.save()
            f.age()
            got = f.watch()
            check("NEGATIVE: reentry never starts on grant_ask (waiting on the human)",
                  got == (0, "REENTRY: waiting-human") and f.attempts() == 0
                  and f.marker_lines(settle=0.3) == [], "got=%r" % (got,))
        finally:
            f.close()

        f = _Stalled()
        try:
            TK.revoke(f.root)
            f.age()
            got = f.watch()
            check("NEGATIVE: reentry never starts under a revoked grant",
                  got == (0, "REENTRY: ask") and f.attempts() == 0
                  and f.marker_lines(settle=0.3) == [], "got=%r" % (got,))
        finally:
            f.close()

        f = _Stalled(sleep=0, max_reentries=1)
        try:
            first = f.watch()
            f.marker_lines(settle=10)
            f.wait_dead()
            f.age()
            got = f.watch()
            stopped = (f.state().stopped or {}).get("reason")
            check("NEGATIVE: reentry never starts beyond max_reentries (and records "
                  "reentry_exhausted)",
                  first[1].startswith("REENTRY: started 1 ")
                  and got == (0, "REENTRY: exhausted") and stopped == "reentry_exhausted"
                  and f.attempts() == 1 and f.marker_lines() == ["1"],
                  "first=%r got=%r stopped=%r" % (first, got, stopped))
        finally:
            f.close()

        f = _Stalled(agent_cmd=["sh", "-c", "x", "{prompt}"])
        try:
            problems = CC.reentry_problems(f.block)
            got = f.watch()
            check("NEGATIVE: a grant whose agent_cmd is a shell is invalid (no re-entry)",
                  any("shell" in p for p in problems) and got == (0, "REENTRY: ask")
                  and f.attempts() == 0 and f.marker_lines(settle=0.3) == [],
                  "problems=%r got=%r" % (problems, got))
        finally:
            f.close()


def _gated_run(case):
    """One gated task driven to its evidence verdict, with the evidence `case` plants.
    Returns (evidence exit, merge exit, proven?)."""
    import test_conductor_evidence as E  # the gate suite's fixtures, a shipped asset
    g = E.Gate("setUp")
    g.setUp()
    try:
        g.init([E.task("T1", ["notes-create"])])
        wt, sha = g.build()
        if case == "good":
            g.record("notes-create", sha)
        elif case == "stale":
            g.record("notes-create", E.git(g.root, "rev-parse", "HEAD"))
        elif case == "writer":
            g.record("notes-create", sha, verifier="writer")
        elif case == "doctor-red":
            g.record("notes-create", sha, doctor_ok=False)
        rc = g.evidence(verifier="writer" if case == "writer" else "verifier")[0]
        if case == "artifact-removed":
            d = g.record("notes-create", sha)
            rc = g.evidence()[0]
            os.remove(os.path.join(d, "capture.json"))
        return rc, g.run_cmd("merge", "T1")[0], g.proven()
    finally:
        shutil.rmtree(g.root, ignore_errors=True)


def evidence_gate_arm():
    good = _gated_run("good")
    check("evidence gate: a head with a passing verdict from an independent verifier, on "
          "fresh SHA-bound evidence and a green doctor, merges", good == (0, 0, True),
          "evidence=%r merge=%r proven=%r" % good)
    for case, label in (("stale", "evidence recorded on another head"),
                        ("writer", "a verdict from the agent that wrote the code"),
                        ("doctor-red", "evidence from an instance whose doctor is red"),
                        ("artifact-removed", "an artifact removed after the verdict")):
        rc, mrc, proven = _gated_run(case)
        check("NEGATIVE: evidence gate: %s never merges" % label, not proven and mrc != 0,
              "evidence=%r merge=%r" % (rc, mrc))


def money_ledger_arms():
    import re
    # Positive flow: register, check, record, status.
    root = LK.registered()
    c = LK.check(root, "vps", "hosting", "19.99")
    r = LK.run(root, "record", "--id", "vps", "--amount", "21", "--currency", "USD",
               "--month", "2026-10", "--by", "Dana")
    s = LK.run(root, "status")
    check("money ledger: register, a check under the cap, a human record and status agree",
          c[0] == 0 and "NOT_REFUSED: vps — the human's approval is still required" in c[1]
          and r[0] == 0 and "CATEGORY: hosting cap=25.00 recorded=21.00 reserved=0.00 "
          "headroom=4.00" in s[1], "check=%r record=%r" % (c[0], r[0]))

    # Stale budget: every check refuses, a new id and a replay alike.
    root = LK.registered()
    before = LK.check(root, "vps", "hosting", "5")
    LK.write_budget(root, LK.BUDGET.replace("| hosting | 25 |", "| hosting | 500 |"))
    after = [LK.check(root, "vps", "hosting", "5"), LK.check(root, "big", "hosting", "400")]
    check("NEGATIVE: money ledger: a budget edited since register never gives NOT_REFUSED",
          before[0] == 0 and all(rc == 3 and "budget edited since register" in out
                                 and "NOT_REFUSED" not in out for rc, out, _ in after),
          "before=%r after=%r" % (before[0], [a[0] for a in after]))

    # An agent cannot record spend.
    root = LK.registered()
    LK.check(root, "vps", "hosting", "5")
    got = [LK.run(root, "record", "--id", "vps", "--amount", "1", "--currency", "USD",
                  "--month", "2026-10", "--by", by)[0]
           for by in ("agent:claude", "\u0430gent:claude", "AGENT-x")]
    check("NEGATIVE: money ledger: an agent-recorded spend is refused and nothing is stored",
          got == [2, 2, 2] and LK.state(root)["proposals"]["vps"]["records"] == {},
          "exits=%r" % got)

    # No double reservation.
    root = LK.registered()
    a = [LK.check(root, "a", "hosting", "15")[0] for _ in range(3)]
    b = LK.check(root, "b", "hosting", "15")
    s = LK.run(root, "status")[1]
    check("NEGATIVE: money ledger: a re-run reserves once, and a second proposal that fits "
          "only alone is refused", a == [0, 0, 0] and b[0] == 3
          and "reserved=15.00 headroom=10.00" in s, "reruns=%r second=%r" % (a, b[0]))

    # Over-cap recurring month (month 7 of an open-ended monthly spend).
    root = LK.registered()
    one = LK.check(root, "launch", "ads", "95", "--month", "2027-04")[0]
    rec = LK.check(root, "ppc", "ads", "10", "--recurring", "monthly", "--start", "2026-10")
    check("NEGATIVE: money ledger: a recurring proposal over the cap in month 7 only is refused",
          one == 0 and rec[0] == 3 and "2027-04" in rec[1]
          and "ppc" not in LK.state(root)["proposals"], "one-off=%r recurring=%r"
          % (one, rec[0]))

    # A replay is checked again: after a cap cut, and after a record that pushes the total
    # over, the same id and fields are REFUSED and the reservation stays.
    root = LK.registered()
    first = LK.check(root, "vps", "hosting", "20")[0]
    LK.write_budget(root, LK.BUDGET.replace("| hosting | 25 |", "| hosting | 10 |"))
    cut = LK.run(root, "register", "--by", "Dana", "--reason", "cut")[0]
    replay = LK.check(root, "vps", "hosting", "20")
    check("NEGATIVE: money ledger: a replay after a cap cut is REFUSED and stays reserved",
          first == 0 and cut == 0 and replay[0] == 3 and "NOT_REFUSED" not in replay[1]
          and LK.state(root)["proposals"]["vps"]["status"] == "open",
          "first=%r cut=%r replay=%r" % (first, cut, replay[0]))
    root = LK.registered()
    a = LK.check(root, "a", "ads", "100")[0]
    b = [LK.check(root, "b", "hosting", "20")[0] for _ in range(2)]
    rec = LK.run(root, "record", "--id", "a", "--amount", "100.50", "--currency", "USD",
                 "--month", "2026-10", "--by", "Dana")[0]
    replay = LK.check(root, "b", "hosting", "20")
    check("NEGATIVE: money ledger: a replay after a record pushes the total over is REFUSED",
          a == 0 and b == [0, 0] and rec == 0 and replay[0] == 3
          and "the total would reach 120.50" in replay[1],
          "before=%r record=%r replay=%r" % (b, rec, replay[0]))

    # The total cap refuses when no category cap does; one minor unit over a cap is refused
    # and exactly at the cap is not.
    root = LK.registered()
    ads = LK.check(root, "a", "ads", "100")[0]
    over = LK.check(root, "b", "hosting", "20.01")
    at = LK.check(root, "c", "hosting", "20")[0]
    check("NEGATIVE: money ledger: the total cap refuses when no category cap does",
          ads == 0 and over[0] == 3 and "the total would reach 120.01" in over[1] and at == 0,
          "ads=%r over=%r at=%r" % (ads, over[0], at))
    at = LK.check(LK.registered(), "a", "hosting", "25")
    unit = LK.check(LK.registered(), "a", "hosting", "25.01")
    check("NEGATIVE: money ledger: one minor unit over a cap is REFUSED, exactly at it is not",
          at[0] == 0 and "NOT_REFUSED: a" in at[1] and unit[0] == 3
          and "would reach 25.01, over its monthly cap 25.00" in unit[1],
          "at=%r over=%r" % (at[0], unit[0]))

    # Another currency and a past month are REFUSED (exit 3); records too.
    root = LK.registered()
    eur = LK.check(root, "e", "ads", "1", currency="EUR")
    past = LK.check(root, "z", "ads", "1", "--month", "2026-09")
    LK.check(root, "p", "hosting", "5")
    rec_eur = LK.run(root, "record", "--id", "p", "--amount", "5", "--currency", "EUR",
                     "--month", "2026-10", "--by", "Dana")
    rec_future = LK.run(root, "record", "--id", "p", "--amount", "5", "--currency", "USD",
                        "--month", "2026-11", "--by", "Dana")
    check("NEGATIVE: money ledger: another currency and a past month are REFUSED with exit 3",
          eur[0] == 3 and "not the project currency USD" in eur[1] and past[0] == 3
          and "before the current month" in past[1] and set(LK.state(root)["proposals"])
          == {"p"}, "eur=%r past=%r" % (eur[0], past[0]))
    check("NEGATIVE: money ledger: a record in another currency is REFUSED and a record for "
          "a month that has not started is a STOP", rec_eur[0] == 3 and rec_future[0] == 2
          and LK.state(root)["proposals"]["p"]["records"] == {},
          "currency=%r future=%r" % (rec_eur[0], rec_future[0]))

    # Only the human withdraws, and a raise needs --reason.
    root = LK.registered()
    LK.check(root, "vps", "hosting", "5")
    got = [LK.run(root, "withdraw", "--id", "vps", "--by", by, "--reason", "x")[0]
           for by in ("agent:claude", "agent.claude", "agent")]
    human = LK.run(root, "withdraw", "--id", "vps", "--by", "Dana", "--reason", "x")[0]
    check("NEGATIVE: money ledger: an agent (and a lookalike name) cannot withdraw",
          got == [2, 2, 2] and human == 0, "agent=%r human=%r" % (got, human))
    LK.run(root, "record", "--id", "vps", "--amount", "24", "--currency", "USD", "--month",
           "2026-10", "--by", "Dana")
    spent = LK.check(root, "q", "hosting", "1.01")
    fits = LK.check(root, "r", "hosting", "1")[0]
    check("NEGATIVE: money ledger: a withdrawn proposal's recorded spend still counts against "
          "the cap", spent[0] == 3 and "would reach 25.01" in spent[1] and fits == 0,
          "over=%r fits=%r" % (spent[0], fits))
    root = LK.registered()
    LK.write_budget(root, LK.BUDGET.replace("| hosting | 25 |", "| hosting | 90 |")
                    .replace("| total | 120 |", "| total | 200 |"))
    bare = LK.run(root, "register", "--by", "Dana")
    regs = len(LK.state(root)["registrations"])
    big = LK.check(root, "big", "hosting", "80")[0]
    check("NEGATIVE: money ledger: a raised budget registered without --reason is refused, "
          "and checks keep refusing", bare[0] == 2 and "needs --reason" in bare[1]
          and regs == 1 and big == 3, "register=%r regs=%r check=%r" % (bare[0], regs, big))

    # A state that its log does not match refuses the check.
    root = LK.registered()
    LK.check(root, "a", "hosting", "20")
    st = LK.state(root)
    st["proposals"] = {}
    LK.write_state(root, st)              # the reservation is gone, the seq is unchanged
    hand = LK.check(root, "b", "hosting", "20")
    check("NEGATIVE: money ledger: a hand-edited state that its log does not match refuses "
          "the check", hand[0] == 3 and "the ledger state and its log disagree" in hand[1],
          "check=%r" % hand[0])

    # Approval words: an id or a --by name holding one is a STOP.
    root = LK.registered()
    ids = [LK.check(root, pid, "hosting", "1")[0] for pid in ("ok", "granted", "yes-go")]
    LK.check(root, "vps", "hosting", "1")
    bys = [LK.run(root, "record", "--id", "vps", "--amount", "1", "--currency", "USD",
                  "--month", "2026-10", "--by", by)[0] for by in ("OK", "Granted", "Dana yes")]
    check("NEGATIVE: money ledger: an approval word in an id or a --by name is a STOP",
          ids == [2, 2, 2] and bys == [2, 2, 2]
          and LK.state(root)["proposals"]["vps"]["records"] == {},
          "ids=%r bys=%r" % (ids, bys))

    # --today without the test switch is a STOP: exit 2, not 0.
    root = LK.repo()
    env = {k: v for k, v in os.environ.items() if k != LK.L.TEST_CLOCK_ENV}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()), \
            mock.patch.dict(os.environ, env, clear=True):
        rc = LK.L.main(["--root", root, "--today", LK.TODAY, "register", "--by", "Dana"])
    check("NEGATIVE: money ledger: --today without the test switch exits 2 and writes nothing",
          rc == 2 and buf.getvalue().startswith("STOP:")
          and not os.path.exists(os.path.join(root, ".skill-contract")), "rc=%r" % rc)

    # No approval-like token from any command, no envelope.
    every = []
    root = LK.repo()
    for argv in (("lint",), ("register", "--by", "Dana"), ("register", "--by", "Dana"),
                 ("record", "--id", "vps", "--amount", "1", "--currency", "USD", "--month",
                  "2026-10", "--by", "Dana")):
        every.append(LK.run(root, *argv))
    LK.check(root, "vps", "hosting", "5")
    for argv in (("record", "--id", "vps", "--amount", "30", "--currency", "USD", "--month",
                  "2026-10", "--by", "Dana"),
                 ("withdraw", "--id", "vps", "--by", "Dana", "--reason", "done"),
                 ("status",)):
        every.append(LK.run(root, *argv))
    LK.write_budget(root, LK.BUDGET.replace("| hosting | 25 |", "| hosting | 30 |"))
    every += [LK.run(root, "status"),
              LK.run(root, "register", "--by", "Dana", "--reason", "more")]
    every_text = "".join(o[1] + o[2] for o in every)

    # No approval-like token, no envelope.
    root = LK.registered()
    outs = [LK.check(root, *argv) for argv in (
        ("vps", "hosting", "1"), ("vps", "hosting", "1"), ("big", "hosting", "99"),
        ("x", "gold", "1"), ("y", "ads", "0"), ("z", "ads", "1", "--month", "2026-01"))]
    outs.append(LK.check(root, "e", "ads", "1", currency="EUR"))
    outs.append(LK.check(root, "o", "hosting", "1", "--recurring", "monthly", "--start",
                         "2026-11"))
    text = "".join(o[1] + o[2] for o in outs) + every_text
    hits = re.findall(LK.APPROVAL_TOKEN_RE, text)
    check("NEGATIVE: money ledger: no output of any command carries an approval word (OK, "
          "YES, GRANTED, APPROVED, PASS, ALLOWED and the rest), and a pass writes no envelope",
          not hits and outs[0][0] == 0 and "NOT_REFUSED" in text and "RECORDED:" in text
          and "WITHDRAWN:" in text and "STATUS:" in text and "LINT: clean" in text
          and os.listdir(os.path.join(root, ".skill-contract")) == ["ledger"],
          "hits=%r" % hits)


def main():
    dep_order_and_finish_arm()
    decision_parks_and_run_continues_arm()
    revoked_grant_stops_at_next_gate_arm()
    expired_grant_stops_at_next_gate_arm()
    max_dispatches_one_stops_the_run_arm()
    spend_metering_arm()
    merge_conflict_parks_and_run_branch_stays_clean_arm()
    merge_refuses_a_task_not_in_reviewing_status_arm()
    null_verify_is_refused_at_init_arm()
    vacuous_test_command_cannot_prove_a_task_arm()
    integration_red_is_never_pushed_arm()
    reentry_arms()
    evidence_gate_arm()
    money_ledger_arms()

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print("EVAL_RESULT: %s (%d/%d checks)" % ("PASS" if ok else "FAIL", k, n))
    return 0 if ok else 1


if __name__ == "__main__":
    if not shutil.which("git"):
        print("CHECK: git is installed — FAIL (git is required to run this eval)")
        print("EVAL_RESULT: FAIL (0/1 checks)")
        sys.exit(1)
    sys.exit(main())
