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
can"). Six more short scenarios each isolate one negative requirement: a decision parks
a task without stopping the run; a REVOKED grant stops the run at the next gate
(GATE: ASK reason=revoked) and, as its own separate fixture, an EXPIRED grant does too
(GATE: ASK reason=expired) — these are distinct check-grant outcomes, kept as distinct
checks rather than one merged "expired/revoked" check; max_dispatches: 1 stops the run;
a merge conflict parks the task and leaves the run branch clean; a task whose status
is not `reviewing` (built white-box, through conductor.State) is refused by `merge`
without touching the run branch; and a null verify command parks a task without ever
merging it.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
ASSETS = os.path.join(SKILL, "assets")
sys.path.insert(0, ASSETS)
import conductor as C  # noqa: E402
import contract_check as CC  # noqa: E402
import conductor_testkit as TK  # noqa: E402

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


def null_verify_parks_and_is_never_merged_arm():
    root = TK.repo()
    try:
        t1 = task("T1", [], ["true"])
        t1["verify"] = [{"text": "by hand", "command": None}]  # the verify command is null
        plan_env = TK.write_plan_envelope(root, plan=plan(t1))
        TK.write_grant(root, plan_env)
        rc, out, err = run(["init", "--plan", plan_env, "--root", root])
        if rc != 0:
            check("init accepts a null verify command (it parks the task later, at verify)",
                  False, err.strip()[-160:])
            return
        run(["start", "T1", "--root", root])
        commit_in(wt(root, "T1"), "t1.txt", "one\n")  # real work; still unrunnable to verify
        rc, out, err = run(["verify", "T1", "--root", root])
        parked = rc == 3 and status(root, "T1") == "parked"
        merge_refused = run(["merge", "T1", "--root", root])[0] == 2  # not a mergeable status
        check("NEGATIVE: a task with a null verify command is parked and never merged",
              parked and C.State.load(C.state_path(root)).tasks["T1"]["park_reason"]
              == "unrunnable-verify" and merge_refused and status(root, "T1") != "proven",
              out.strip())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    dep_order_and_finish_arm()
    decision_parks_and_run_continues_arm()
    revoked_grant_stops_at_next_gate_arm()
    expired_grant_stops_at_next_gate_arm()
    max_dispatches_one_stops_the_run_arm()
    merge_conflict_parks_and_run_branch_stays_clean_arm()
    merge_refuses_a_task_not_in_reviewing_status_arm()
    null_verify_parks_and_is_never_merged_arm()

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
