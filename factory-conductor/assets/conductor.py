#!/usr/bin/env python3
"""conductor.py — the factory-conductor state machine.

Runs a task plan end to end: schedules tasks into waves from their
`depends_on`, records every step in an append-only autonomy log, and keeps
the run's state in one file so a stopped run can be resumed.

The run directory is <root>/.skill-contract/runs/<run-id>/, holding
state.json (rewritten atomically) and autonomy-log.jsonl (append-only, one
JSON object per line, each with `at` in RFC 3339 UTC and `event`). It is
git-ignored and never committed.

Usage:
    python3 conductor.py init --plan <task-plan envelope> [--root <repo>] [--budget JSON]
                                                                       # prints RUN: <id>
    python3 conductor.py gate --action <class> [--root <repo>]         # GATE: COVERED|ASK
    python3 conductor.py next [--root <repo>] [--max N]                # READY: T1 T2 | STOP:
    python3 conductor.py status [--root <repo>]                        # one STATUS: line per task
    python3 conductor.py resume [--root <repo>]                        # READY:, NEXT: lines
    python3 conductor.py start <task> [--root <repo>]                  # START: <task> <worktree>
    python3 conductor.py verify <task> [--root <repo>]                 # VERIFY: <task> pass <sha>|fail
    python3 conductor.py review <task> --verdict pass|fail [--detail T] [--root <repo>]
    python3 conductor.py merge <task> [--root <repo>]                  # MERGE: <task> <sha>
    python3 conductor.py park <task> --reason R [--root <repo>]        # PARK: <task> <reason>
    python3 conductor.py decision <task> --question Q [--root <repo>]  # PARK: <task> new_human_decision
    python3 conductor.py finish [--root <repo>] [--push-cmd JSON] [--pr-cmd JSON] [--retry-remote]
                                                                       # FINISH: <envelope path>

Gates. `init` validates the task-plan/v1 envelope with the vendored checker (C3-C7,
fresh subjects, a schedulable plan, and a runnable command on every verify step: a task
with no verify step, or a step whose command is null or empty, is refused with
FAIL: task <id> verify step <n> has no command, exit 2), then requires `check-grant` to cover
local_reversible for that plan (`--subject <plan envelope>`, so a grant covers only the
plan it pins) on the current branch, which must match the grant's branch_pattern and
not be the default branch. It cuts factory/<plan-slug> from the current branch (that
name must match branch_pattern too), checks it out in the root and writes the state.
`start`, `merge` and `resume` re-run check-grant before they act and proceed ONLY on
exit 0; an ASK prints GATE: ASK <reason>, stops the run with grant_ask and exits 3.
`resume` lifts a grant_ask stop once a grant covers the run again; no other stop.
`verify` is gated too. Later gates ask whether *some* grant covers this plan now: they
do not pin the grant id read at `init`, so a newer grant a same-user process writes for
the same plan would cover the run (a residual under spec section 7a). A git older than
2.31 fails every gate closed (GATE: ASK reason=git-too-old).

Budgets (the grant's `budget`; --budget may only tighten a key the grant sets, or add
one it does not; unknown keys in the grant are warned about, logged and dropped):
wall_clock_min (from init; checked by next, start, verify, review and merge),
max_dispatches (one per executor start, per repair send-back after a failing verify or
review, and per reviewer after a passing verify; a task that needs a dispatch past the
cap parks with budget_dispatches; when neither the grant nor --budget sets it, init
derives n_tasks * 2 * (1 + max_repairs_per_task), records it in the state and in
budget_derived, and logs derived: true on the init event, so cost is always bounded),
max_repairs_per_task (every failing verify or review
after the first is a repair; at the cap the task parks with verify_red_after_repairs, and
the run goes on;
DEFAULT_REPAIRS = 2 when neither the grant nor --budget sets it, recorded in the state)
and max_parallel (default 2; start refuses past it, next --max is clamped to it) are
enforced.
max_tokens and max_usd are recorded and reported, not enforced: the runtime does not
expose usage to this tool. `next` stops the run (STOP: <reason>, exit 3) on, in order,
budget_wall_clock, budget_dispatches, no_ready_tasks. `decision` parks a task with
new_human_decision and the run goes on.

Each task runs in its own git worktree, <run>/wt/<task>, on the task branch
<run_branch>--<task>. (Not <run_branch>/<task>: git cannot keep a ref
refs/heads/factory/p/T1 beside the run branch refs/heads/factory/p.) `verify`
re-runs the task's verify commands there, as argv lists, never through a shell;
that re-run is the proof. The commands are re-read from the pinned plan file
(which must still match plan_sha256), never from state.json; the worktree must
be clean, and the commit proven is recorded as verified_head. The commands run
in a fresh detached checkout of that commit (<run>/verify/<task>-<sha>), removed
afterwards, so files the worktree ignores cannot make a verify pass; each
command's process group is killed when it ends (a child that calls setsid()
escapes that). A stopped run refuses start,
verify, review and merge with STOP: <reason>. `merge` merges
that pinned sha, never the branch name, and refuses if the branch has moved. `merge` needs a passing verify and a passing review,
and the run branch checked out, clean, at --root. A task with no commit of its own parks
with no-commits (exit 3): retrying could never change that. A merge of the pinned sha already on
the run branch's first-parent line (a crash between the root's fast-forward and saving state)
is recorded as proven, logged with recovered: true, and exits 0. The --no-ff merge runs in an
isolated clone (<run>/merge/<task>-<sha>, no inherited config, attributes, merge
drivers or hooks); the root then fetches that merge commit (GIT_ALLOW_PROTOCOL=file)
and fast-forwards to it, and merge_commit records that sha. A fast-forward git refuses while
the root HEAD has not moved (an untracked file in the way) exits 2 and leaves the task
reviewing, so merge can run again. It then removes the worktree and deletes
the task branch. A conflict parks the task and never touches the root; a root that
moved meanwhile, or a fast-forward that left HEAD anywhere but where it was, parks it and
stops the run. Every
conductor git call ignores hooks, fsmonitor, replace refs, grafts and the
commit-graph.

Accepted residuals (spec section 7a, a hostile executor running as the same user):
tampering with loose objects in the shared object store (N2) or planting
objects/info/alternates (N3), and committed code that reads the executor's
uncommitted files by path (N11), are not caught here; CI re-running the checks on
the pushed branch is the independent re-check.

Finish. `finish` ends a run, stopped or not. On a stopped run it first parks every
running, verifying or reviewing task with in_flight_at_stop (a stopped run takes no
further step, so they could never finish); on a run that is not stopped it refuses
while a task is in flight. It writes a run-result/v1 envelope under
.skill-contract/envelopes/ and prints FINISH: <path>. That local write is deliberately not
gated (no check-grant for local_reversible): a revoked or expired grant must still let a
run end and report what it did.
The envelope's subjects pin the plan envelope and the grant read at init; its payload
carries each task's status, verify re-runs (commands in the plan's {python} form),
review, merge commit and park reason, the stop, the budget (max_tokens and max_usd
recorded, not enforced), and the worktrees a merge-conflict park kept (never deleted
here). Its assertions are one passed `verify:<task>` per proven task, carrying the
task's first verify command as the plan wrote it; `init` refuses a plan whose verify
commands break the checker's C6 command rule, so that assertion is always valid.
log_sha256 is the sha256 of the log's first log_bytes bytes, which end with the `finish`
event: the log is append-only, so the gate, push and pr events after it never change
that prefix. A crash between logging `finish` and saving state leaves the run
unfinished; a re-run writes a second finish event and a second envelope (a new id,
nothing overwritten), which is harmless.
Then `gate push_branch`, and on COVERED the --push-cmd (default: git push -u origin
<run_branch>), only while the root is on the run branch. The push command must be
`git push [--set-upstream|--porcelain|--quiet|-u|-q] <remote> <run_branch>`: no force,
no refspec, no other branch or option. The conductor then rewrites the run branch to the
explicit, non-forced refspec refs/heads/<rb>:refs/heads/<rb>, so a remote.<name>.push
mapping planted in the shared .git/config cannot retarget or force it. Residual (spec
section 7a, hostile same-user config): pushurl, url.<base>.pushInsteadOf, receivepack and
core.sshCommand can redirect WHERE the push goes, but cannot force it or change which
branch it updates. It runs with GIT_ALLOW_PROTOCOL=file:git:http:https:ssh, so a planted
url.<x>.insteadOf cannot turn it into an ext:: command. Like every conductor git call it runs with core.hooksPath=/dev/null
(spec section 7a), so the user's own pre-push hooks do NOT run.
Then `gate open_pr`, and on COVERED the --pr-cmd (default: gh pr create --title <plan
title> --base <base branch> --body-file <tmp>), the body built from the payload with all
plan, park and review text inside inline code spans. The commands are JSON argv lists; a
token that is exactly {run_branch}, {base_branch}, {title} or {body_file} is replaced.
The first ASK prints GATE: ASK, skips the rest and exits 3 (the run's recorded stop is
left as it was); a failed step exits 3 too. Completed steps are recorded (finished.pushed,
finished.pr, with the envelope's sha256: --retry-remote refuses, exit 2, an envelope
that changed since). A later `finish` prints the same FINISH: line; while a step is pending it
also prints REMOTE: pending push|pr and exits 3, and `finish --retry-remote` re-gates and
runs just the pending steps. Once finished, next, resume, start, verify, review, merge,
park and decision refuse with exit 2 ("run finished").

Run id grammar: run-<yyyymmddThhmmssZ>-<6 hex>.
Exit 0 success; 2 usage or invalid input; 3 the run must stop.
"""
import sys

if sys.version_info < (3, 10):
    sys.stderr.write("conductor.py needs Python >= 3.10, found %d.%d\n"
                     % sys.version_info[:2])
    sys.exit(2)

import argparse  # noqa: E402
import datetime as _dt  # noqa: E402
import fnmatch  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import secrets  # noqa: E402
import shutil  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import contract_check as CC  # noqa: E402  (the vendored skill-contract checker, same dir)

RUN_ID_RE = r"^run-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}\Z"
STATUSES = ("pending", "running", "verifying", "reviewing", "proven", "parked", "blocked")
# verify_red_after_repairs is a park reason, not a stop: parking never stops the run.
STOP_REASONS = ("budget_wall_clock", "budget_dispatches", "grant_ask",
                "new_human_decision", "no_ready_tasks")
DEFAULT_PARALLEL = 2
DEFAULT_REPAIRS = 2  # max_repairs_per_task when neither the grant nor --budget sets it
TASK_PLAN_KIND = "https://github.com/dhanesh/agent-skills/skill-contract/task-plan/v1"
RUN_RESULT_KIND = "https://github.com/dhanesh/agent-skills/skill-contract/run-result/v1"
CONDUCTOR_SKILL = "factory-conductor"
CONDUCTOR_VERSION = "1.0.0"

ACTIVE = ("running", "verifying", "reviewing")
TASK_FIELDS = ("status", "depends_on", "verify", "repairs", "branch", "worktree",
               "verify_runs", "verified_head", "review", "merge_commit", "park_reason",
               "failures", "question")
# Budget keys and the values each accepts. max_tokens and max_usd are recorded and
# reported, never enforced: the runtime does not expose usage to this tool.
BUDGET_KEYS = {"wall_clock_min": "number", "max_dispatches": "count",
               "max_repairs_per_task": "count", "max_parallel": "positive",
               "max_tokens": "count", "max_usd": "number"}
BUDGET_NOTE_TEXT = ("max_tokens/max_usd recorded, not enforced "
                    "(the runtime does not expose usage)")
BUDGET_NOTE = "STATUS: budget " + BUDGET_NOTE_TEXT
# finish's remote steps. Each is an argv list (never a shell); a token that is exactly
# {run_branch}, {base_branch}, {title} or {body_file} is replaced (see expand_cmd).
DEFAULT_PUSH_CMD = ["git", "push", "-u", "origin", "{run_branch}"]
DEFAULT_PR_CMD = ["gh", "pr", "create", "--title", "{title}", "--base", "{base_branch}",
                  "--body-file", "{body_file}"]
REMOTE_TIMEOUT = 300
RUNS_DIR = os.path.join(".skill-contract", "runs")
STATE_FILE = "state.json"
LOG_FILE = "autonomy-log.jsonl"
WT_DIR = "wt"
VERIFY_DIR = "verify"
MERGE_DIR = "merge"
TASK_BRANCH_SEP = "--"
TASK_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z"
VERIFY_TIMEOUT = 600
TAIL = 2000
GIT_SCRUB = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
             "GIT_CEILING_DIRECTORIES")


class PlanError(Exception):
    """The plan cannot be scheduled: a cycle, or a dependency that does not exist."""


class StateError(Exception):
    """state.json is unreadable or does not have the shape of a run's state."""


REQUIRED_KEYS = ("root", "run_id", "plan_envelope", "plan_sha256", "grant_id",
                 "run_branch", "base_branch", "created_at", "tasks", "order")


# Copied (not imported — skills stay self-contained) into
# spec-first-planning/assets/spec_to_tasks.py's own waves(), for that skill's
# --waves output. That copy sorts task ids numerically (key=lambda t: int(t[1:]),
# so T10 sorts after T2); this one still uses a plain sorted() below.
def waves(tasks):
    """Group task ids into waves. Every id in a wave is independent of the others."""
    for k, t in tasks.items():
        deps = t.get("depends_on") or []
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise PlanError("task %s: depends_on must be a list of task id strings" % k)
    unknown = {d for t in tasks.values() for d in t.get("depends_on") or []} - set(tasks)
    if unknown:
        raise PlanError("depends_on names unknown task(s): %s" % ", ".join(sorted(unknown)))
    left = {k: set(v.get("depends_on") or []) for k, v in tasks.items()}
    out = []
    while left:
        wave = sorted(k for k, deps in left.items() if not deps)
        if not wave:
            raise PlanError("depends_on has a cycle among: %s" % ", ".join(sorted(left)))
        out.append(wave)
        for k in wave:
            del left[k]
        for deps in left.values():
            deps.difference_update(wave)
    return out


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


def _rfc3339(when):
    return when.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id(now=None):
    """A fresh run id: run-<yyyymmddThhmmssZ>-<6 hex>."""
    when = now or _now()
    if when.tzinfo is None:  # a naive datetime is taken as UTC, not local time
        when = when.replace(tzinfo=_dt.timezone.utc)
    when = when.astimezone(_dt.timezone.utc)
    return "run-%s-%s" % (when.strftime("%Y%m%dT%H%M%SZ"), secrets.token_hex(3))


def run_dir(root, run_id):
    """The run directory for run_id under root."""
    if not re.match(RUN_ID_RE, run_id):
        raise ValueError("not a run id: %r" % run_id)
    return os.path.join(root, RUNS_DIR, run_id)


def current_run(root):
    """The newest run directory under root that holds a state.json, or None.

    Run ids start with their UTC creation time, so the newest sorts last."""
    base = os.path.join(root, RUNS_DIR)
    try:
        names = os.listdir(base)
    except FileNotFoundError:
        return None
    runs = sorted(n for n in names if re.match(RUN_ID_RE, n)
                  and os.path.isfile(os.path.join(base, n, STATE_FILE)))
    return os.path.join(base, runs[-1]) if runs else None


class State:
    """One run's state. `save()` rewrites state.json atomically; `log()` only appends."""

    def __init__(self, data, directory):
        self.dir = directory
        self.root = data["root"]
        self.run_id = data["run_id"]
        self.plan_envelope = data["plan_envelope"]
        self.plan_sha256 = data["plan_sha256"]
        self.grant_id = data["grant_id"]
        self.run_branch = data["run_branch"]
        self.base_branch = data["base_branch"]
        self.created_at = data["created_at"]
        self.budget = dict(data.get("budget") or {})
        # Budget keys init derived rather than read from the grant or --budget.
        self.budget_derived = list(data.get("budget_derived") or [])
        self.dispatches = data.get("dispatches", 0)
        self.stopped = data.get("stopped")
        self.finished = data.get("finished")
        self.tasks = data["tasks"]
        self.order = list(data["order"])

    @property
    def state_path(self):
        return os.path.join(self.dir, STATE_FILE)

    @property
    def log_path(self):
        return os.path.join(self.dir, LOG_FILE)

    @classmethod
    def new(cls, root, run_id, plan, plan_envelope, plan_sha256, grant_id,
            run_branch, base_branch, budget, budget_derived=()):
        """Validate the plan, create the run directory, write state.json and log `init`.

        Raises PlanError on a malformed plan, before anything is written."""
        order, plan_tasks = _plan_tasks(plan)
        schedule = waves({k: {"depends_on": t["depends_on"]} for k, t in plan_tasks.items()})
        budget = dict(budget or {})
        check_budget(budget)
        tasks = {}
        for tid in order:
            t = plan_tasks[tid]
            tasks[tid] = {"status": "pending", "depends_on": list(t["depends_on"]),
                          "verify": t.get("verify"), "repairs": 0, "branch": None,
                          "worktree": None, "verify_runs": [], "review": None,
                          "verified_head": None, "merge_commit": None, "park_reason": None}
        root = os.path.abspath(root)
        directory = run_dir(root, run_id)
        os.makedirs(directory, exist_ok=False)
        _ignore_runs_dir(os.path.dirname(directory))
        st = cls({"root": root, "run_id": run_id, "plan_envelope": plan_envelope,
                  "plan_sha256": plan_sha256, "grant_id": grant_id,
                  "run_branch": run_branch, "base_branch": base_branch,
                  "created_at": _rfc3339(_now()), "budget": budget,
                  "budget_derived": list(budget_derived), "dispatches": 0,
                  "stopped": None, "tasks": tasks, "order": order}, directory)
        st.save()
        st.log("init", run=run_id, plan=plan_envelope, plan_sha256=plan_sha256,
               waves=schedule, max_dispatches=budget.get("max_dispatches"),
               derived="max_dispatches" in st.budget_derived)
        return st

    @classmethod
    def load(cls, path):
        """Rebuild a State from its state.json. Raises StateError on any bad shape."""
        try:
            with open(path, "rb") as f:
                data = json.loads(f.read().decode("utf-8"))
        except (OSError, ValueError) as e:  # ValueError covers JSON and UTF-8 errors
            raise StateError(str(e)) from e
        if not isinstance(data, dict):
            raise StateError("top level is %s, not an object" % type(data).__name__)
        missing = [k for k in REQUIRED_KEYS if k not in data]
        if missing:
            raise StateError("missing key(s): %s" % ", ".join(missing))
        if not isinstance(data["run_id"], str) or not re.match(RUN_ID_RE, data["run_id"]):
            raise StateError("run_id is not a run id: %r" % (data["run_id"],))
        tasks, order = data["tasks"], data["order"]
        if not isinstance(tasks, dict):
            raise StateError("tasks is %s, not an object" % type(tasks).__name__)
        if not isinstance(order, list) or sorted(order, key=str) != sorted(tasks):
            raise StateError("order does not list exactly the task ids")
        for tid, t in tasks.items():
            if not isinstance(t, dict) or t.get("status") not in STATUSES:
                raise StateError("task %s has no valid status" % tid)
            deps = t.get("depends_on")
            if not isinstance(deps, list) or any(d not in tasks for d in deps):
                raise StateError("task %s has an invalid depends_on" % tid)
        if data.get("budget") is not None and not isinstance(data["budget"], dict):
            raise StateError("budget is not an object")
        if data.get("budget_derived") is not None and not isinstance(
                data["budget_derived"], list):
            raise StateError("budget_derived is not a list")
        return cls(data, os.path.dirname(os.path.abspath(path)))

    def to_dict(self):
        return {"root": self.root, "run_id": self.run_id,
                "plan_envelope": self.plan_envelope, "plan_sha256": self.plan_sha256,
                "grant_id": self.grant_id, "run_branch": self.run_branch,
                "base_branch": self.base_branch, "created_at": self.created_at,
                "budget": self.budget, "budget_derived": self.budget_derived,
                "dispatches": self.dispatches, "stopped": self.stopped, "finished": self.finished, "tasks": self.tasks,
                "order": self.order}

    def save(self):
        """Write state.json atomically: a temp file in the same directory, then os.replace."""
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".state.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
        _fsync_dir(self.dir)

    def log(self, event, **fields):
        """Append one event to autonomy-log.jsonl. The log is never rewritten."""
        if "at" in fields or "event" in fields:
            raise ValueError("log fields must not override 'at' or 'event'")
        record = {"at": _rfc3339(_now()), "event": event}
        record.update(fields)
        line = json.dumps(record, sort_keys=True, allow_nan=False) + "\n"
        with open(self.log_path, "a+b") as f:
            # A torn last line (a crash mid-write) is closed off, never rewritten,
            # so it cannot swallow this event.
            if f.seek(0, os.SEEK_END) > 0:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    f.write(b"\n")
            f.write(line.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        return record

    def last_event(self):
        """The last complete event in the log, or None. A torn trailing line is skipped."""
        try:
            with open(self.log_path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            return None
        for line in reversed(lines):
            try:
                return json.loads(line)
            except ValueError:
                continue
        return None

    def set_status(self, task, status, **fields):
        """Set a task's status and fields. Parking a task blocks every transitive dependent."""
        if task not in self.tasks:
            raise KeyError("unknown task: %s" % task)
        if status not in STATUSES:
            raise ValueError("unknown status: %s" % status)
        bad = set(fields) - set(TASK_FIELDS) - {"status"}
        if bad or "status" in fields:
            raise ValueError("unknown task field(s): %s" % ", ".join(sorted(bad or {"status"})))
        self.tasks[task]["status"] = status
        self.tasks[task].update(fields)
        if status == "parked":
            for dep in self._dependents(task):
                if self.tasks[dep]["status"] != "proven":
                    self.tasks[dep]["status"] = "blocked"

    def _dependents(self, task):
        out, frontier = set(), [task]
        while frontier:
            cur = frontier.pop()
            for tid in self.order:
                if cur in self.tasks[tid]["depends_on"] and tid not in out:
                    out.add(tid)
                    frontier.append(tid)
        return out

    def ready(self, max_parallel):
        """Pending tasks whose dependencies are all proven, capped so that
        running + verifying + reviewing + returned <= max_parallel. Plan order."""
        active = sum(1 for t in self.tasks.values() if t["status"] in ACTIVE)
        slots = max(0, max_parallel - active)
        out = []
        for tid in self.order:
            t = self.tasks[tid]
            if len(out) >= slots:
                break
            if t["status"] == "pending" and all(
                    self.tasks[d]["status"] == "proven" for d in t.get("depends_on") or []):
                out.append(tid)
        return out

    def max_parallel(self):
        mp = self.budget.get("max_parallel")
        return DEFAULT_PARALLEL if mp is None else int(mp)

    def max_repairs(self):
        """max_repairs_per_task, or DEFAULT_REPAIRS when the budget does not set it
        (init records the effective value; this covers a state built without init)."""
        cap = self.budget.get("max_repairs_per_task")
        return DEFAULT_REPAIRS if cap is None else int(cap)


def derived_max_dispatches(n_tasks, max_repairs):
    """The dispatch cap init sets when neither the grant nor --budget does:
    n_tasks * 2 * (1 + max_repairs_per_task), one executor and one reviewer per attempt."""
    return n_tasks * 2 * (1 + max_repairs)


def check_budget(budget):
    """Raise PlanError unless budget is an object of known keys with sane values.

    An unknown key fails rather than being ignored: a misspelled budget would
    otherwise silently go unenforced."""
    if not isinstance(budget, dict):
        raise PlanError("budget must be a JSON object, got %s" % type(budget).__name__)
    unknown = sorted(set(budget) - set(BUDGET_KEYS))
    if unknown:
        raise PlanError("budget has unknown key(s): %s (known: %s)"
                        % (", ".join(unknown), ", ".join(sorted(BUDGET_KEYS))))
    for key, value in budget.items():
        kind = BUDGET_KEYS[key]
        is_int = isinstance(value, int) and not isinstance(value, bool)
        if kind == "positive":
            if not is_int or value < 1:
                raise PlanError("budget.%s must be an integer >= 1, got %r" % (key, value))
        elif kind == "count":
            if not is_int or value < 0:
                raise PlanError("budget.%s must be an integer >= 0, got %r" % (key, value))
        elif not ((is_int or isinstance(value, float)) and math.isfinite(value)
                  and value >= 0):
            raise PlanError("budget.%s must be a number >= 0, got %r" % (key, value))


def _plan_tasks(plan):
    """Check a plan payload's tasks; return (ids in plan order, {id: task}).

    Raises PlanError on a non-object task, a non-string id, a duplicate id, or a
    depends_on that is not a list of strings."""
    if not isinstance(plan, dict) or not isinstance(plan.get("tasks"), list):
        raise PlanError("plan has no tasks list")
    order, seen, dup = [], {}, []
    for i, t in enumerate(plan["tasks"]):
        if not isinstance(t, dict):
            raise PlanError("task #%d is not an object" % (i + 1))
        tid = t.get("id")
        if not isinstance(tid, str) or not tid:
            raise PlanError("task #%d has no string id" % (i + 1))
        deps = t.get("depends_on", [])
        if deps is None:
            deps = []
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise PlanError("task %s: depends_on must be a list of task id strings" % tid)
        if tid in seen:
            dup.append(tid)
            continue
        seen[tid] = dict(t, depends_on=deps)
        order.append(tid)
    if dup:
        raise PlanError("duplicate task id(s): %s" % ", ".join(sorted(set(dup))))
    folded = {}
    for tid in order:
        folded.setdefault(tid.casefold(), []).append(tid)
    clash = [ids for ids in folded.values() if len(ids) > 1]
    if clash:
        # Each id names a worktree directory and a branch ref, and both live on
        # case-insensitive filesystems (macOS, Windows) where T1 and t1 are one path.
        raise PlanError("task ids collide case-insensitively: %s"
                        % "; ".join(", ".join(ids) for ids in clash))
    return order, seen


def _ignore_runs_dir(base):
    """Make the runs directory ignore itself, so a run (and its worktrees) never
    dirties the repository even where the repo's .gitignore does not list it."""
    path = os.path.join(base, ".gitignore")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("# written by factory-conductor: run state is local, never committed\n*\n")


def git_env():
    """os.environ without the variables that point git at another repository, and with
    replace objects and grafts switched off (see GIT_SAFE)."""
    env = dict(os.environ)
    for k in GIT_SCRUB:
        env.pop(k, None)
    # /dev/null/none, not /dev/null: git >= 2.54 prints a deprecation hint about
    # info/grafts when GIT_GRAFT_FILE names a file that exists; a missing one is silent
    # and still names no graft.
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.path.join(os.devnull, "none"))
    return env


def verify_env():
    """The environment a verify command runs in: os.environ without the variables that
    point git at another repository. Not git_env(): GIT_NO_REPLACE_OBJECTS and
    GIT_GRAFT_FILE are the conductor's own switches, and the verify clone copies no
    replace refs or grafts for them to guard against."""
    env = dict(os.environ)
    for k in GIT_SCRUB:
        env.pop(k, None)
    return env


# Transports a conductor git call that reaches another repository may use. A url.<x>.
# insteadOf planted in the shared .git/config (with protocol.ext.allow=always) could
# otherwise turn the fetch or the push into an ext:: command.
FETCH_PROTOCOLS = "file"
PUSH_PROTOCOLS = "file:git:http:https:ssh"


# The shared git dir is writable by the executor, so it is hostile: every conductor
# git call switches off hooks and fsmonitor, the two config keys that run a program
# on an ordinary status/merge/worktree call. It also ignores the commit-graph, and
# (through git_env) replace refs and info/grafts: each of those can make git see a
# commit other than the one a sha names (refs/replace/<P> -> E merges E's tree under
# P's name; a graft or a forged commit-graph moves the merge base).
GIT_SAFE = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
            "-c", "core.commitGraph=false")


def safe_config_env():
    """git_env() that also carries GIT_SAFE as GIT_CONFIG_COUNT/KEY/VALUE (git >= 2.31), so
    git calls the conductor does not spell itself (the vendored checker's) ignore hooks,
    fsmonitor and the commit-graph too. Environment config ranks with `-c`, above the
    repository's own config."""
    env = git_env()
    pairs = [GIT_SAFE[i + 1].split("=", 1) for i in range(0, len(GIT_SAFE), 2)]
    env["GIT_CONFIG_COUNT"] = str(len(pairs))
    for i, (key, value) in enumerate(pairs):
        env["GIT_CONFIG_KEY_%d" % i] = key
        env["GIT_CONFIG_VALUE_%d" % i] = value
    return env


def isolated_env(**extra):
    """git_env() that also ignores the global and system config (for the verify clone)."""
    env = git_env()
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", **extra)
    return env


def git(root, *args, check=False, env=None):
    """Run git in `root` with hooks and fsmonitor off. Returns the CompletedProcess,
    or None when git is unusable."""
    try:
        r = subprocess.run(["git", "-C", root, *GIT_SAFE, *args], capture_output=True,
                           text=True, timeout=300, env=env or git_env())
    except (OSError, subprocess.SubprocessError):
        return None
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r


def _git_ok(root, *args, env=None):
    r = git(root, *args, env=env)
    return r is not None and r.returncode == 0, r


def _git_err(r):
    return "git is not runnable" if r is None else (r.stderr or r.stdout).strip()


def _fsync_dir(path):
    """Make a rename in path durable. Best effort where directories cannot be opened."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _positive_int(text):
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("not an integer: %r" % text)
    if n < 1:
        raise argparse.ArgumentTypeError("must be >= 1, got %d" % n)
    return n


def state_path(root):
    """The newest run's state.json under root, or None when there is no run."""
    d = current_run(root)
    return None if d is None else os.path.join(d, STATE_FILE)


def _slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "plan"


def _load_current(root):
    d = current_run(root)
    if d is None:
        sys.stderr.write("no run found under %s\n" % os.path.join(root, RUNS_DIR))
        return None
    path = os.path.join(d, STATE_FILE)
    try:
        return State.load(path)
    except StateError as e:
        sys.stderr.write("cannot load run state %s: %s\n" % (path, e))
        return None


# ── Gates: every consequential action asks the vendored checker first ───────
MIN_GIT = (2, 31)  # GIT_CONFIG_COUNT, which carries the hardening into the checker's git
_GIT_VERSION = []


def parse_git_version(text):
    """(major, minor, patch) from `git --version` output, or None."""
    m = re.search(r"\bgit version (\d+)\.(\d+)(?:\.(\d+))?", text or "")
    return None if not m else (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def git_version():
    """The installed git's version, read once per process; None when unknown."""
    if not _GIT_VERSION:
        try:
            r = subprocess.run(["git", "--version"], capture_output=True, text=True,
                               timeout=30, env=git_env())
            _GIT_VERSION.append(parse_git_version(r.stdout))
        except (OSError, subprocess.SubprocessError):
            _GIT_VERSION.append(None)
    return _GIT_VERSION[0]


def gate(root, action, subject=None):
    """Run the vendored `contract_check.py check-grant --root <root> --action <action>`
    (and `--subject <subject>` when given). Returns (exit code, last stdout line).

    A caller proceeds ONLY on exit 0. A checker that cannot run is an ASK, and so is a
    git older than 2.31 (or of unknown version): it would silently ignore the
    GIT_CONFIG_COUNT hardening the checker's own git calls rely on."""
    v = git_version()
    if v is None or v < MIN_GIT:
        return 3, "GRANT: ASK reason=git-too-old (need git >= %d.%d, found %s)" % (
            MIN_GIT + (".".join(map(str, v)) if v else "unknown",))
    checker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contract_check.py")
    argv = [sys.executable, "-I", checker, "check-grant", "--root", root, "--action", action]
    if subject is not None:
        argv += ["--subject", subject]
    try:
        # The checker's own git calls (branch, tracked-grant and CI-config probes) run in
        # the executor-writable repository: give them the same hardening as ours.
        r = subprocess.run(argv, capture_output=True, text=True, timeout=120,
                           env=safe_config_env())
    except (OSError, subprocess.SubprocessError) as exc:
        return 3, "GRANT: ASK reason=checker-unavailable (%s)" % exc
    last = (r.stdout.strip().splitlines() or [""])[-1]
    return r.returncode, last


def gate_line(rc, last):
    """(covered?, the GATE: line) for a gate() result. Covered needs exit 0 AND the
    checker's COVERED line; anything else (ASK, NONE, INVALID, an error) is an ASK."""
    body = last[len("GRANT: "):] if last.startswith("GRANT: ") else last
    if rc == 0 and body.startswith("COVERED "):
        return True, "GATE: " + body
    if body.startswith("ASK "):
        rest = body[len("ASK "):]
    elif body == "NONE":
        rest = "reason=no-grant"
    elif body.startswith("INVALID"):
        rest = ("reason=invalid " + body[len("INVALID"):].strip()).strip()
    else:
        rest = "reason=checker-error (exit %d) %s" % (rc, body)
    return False, ("GATE: ASK " + rest).strip()


def plan_subject(st):
    """The run's plan envelope as a path relative to its root, for check-grant --subject:
    a grant covers only the plan it pins."""
    p = st.plan_envelope
    if not os.path.isabs(p):
        return p
    rel = os.path.relpath(os.path.realpath(p), os.path.realpath(st.root))
    return rel.replace(os.sep, "/")


def _gated(st, action):
    """True when the grant covers `action` for this run. Every decision is logged as
    `gate`. On ASK: print the GATE: line and stop the run with grant_ask (resume lifts
    that stop once a grant covers the run again)."""
    ok, line = _gate_logged(st, action)
    if ok:
        return True
    _record_stop(st, "grant_ask", detail=line)
    return False


def _gate_logged(st, action):
    """(covered?, the GATE: line) for `action` on this run; the decision is logged as
    `gate` and an ASK line is printed. It records no stop (finish uses it directly)."""
    rc, last = gate(st.root, action, plan_subject(st))
    ok, line = gate_line(rc, last)
    st.log("gate", action=action, ok=ok, result=last, exit=rc)
    if not ok:
        print(line)
    return ok, line


def cmd_gate(args):
    """Print GATE: COVERED … (exit 0) or GATE: ASK <reason> (exit 3) for one action class.
    With a run under --root, its plan envelope is the subject the grant must pin."""
    root = os.path.abspath(args.root)
    subject = None
    path = state_path(root)
    if path is not None:
        try:
            subject = plan_subject(State.load(path))
        except StateError as e:
            sys.stderr.write("cannot load run state %s: %s\n" % (path, e))
            return 2
    ok, line = gate_line(*gate(root, args.action, subject))
    print(line)
    return 0 if ok else 3


# ── Stop rules ──────────────────────────────────────────────────────────────
def _wall_clock_stop(st):
    """budget_wall_clock once wall_clock_min minutes have passed since init, else None.
    An unreadable created_at counts as exhausted (fail closed)."""
    limit = st.budget.get("wall_clock_min")
    if limit is None:
        return None
    try:
        created = _dt.datetime.strptime(st.created_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc)
    except (TypeError, ValueError):
        return "budget_wall_clock"
    if (_now() - created).total_seconds() >= limit * 60:
        return "budget_wall_clock"
    return None


def _in_flight(st):
    return any(t["status"] in ACTIVE for t in st.tasks.values())


def _dispatches_left(st):
    """How many more tasks may be dispatched, or None when max_dispatches is unset."""
    cap = st.budget.get("max_dispatches")
    return None if cap is None else max(0, cap - st.dispatches)


def check_stop(st):
    """The stop reason that ends the run now, or None. In order:

    budget_wall_clock  wall_clock_min has passed since init (in-flight work included);
    budget_dispatches  max_dispatches is spent, a task is ready, and nothing is in
                       flight (in-flight work may still verify, review and merge);
    no_ready_tasks     nothing is ready and nothing is in flight."""
    reason = _wall_clock_stop(st)
    if reason:
        return reason
    ready = st.ready(len(st.tasks))
    busy = _in_flight(st)
    if ready and not busy and _dispatches_left(st) == 0:
        return "budget_dispatches"
    if not ready and not busy:
        return "no_ready_tasks"
    return None


def _record_stop(st, reason, **detail):
    """Record the run as stopped, log `stop`, print STOP: <reason>. Returns 3."""
    st.stopped = dict(detail, reason=reason, at=_rfc3339(_now()))
    st.save()
    st.log("stop", reason=reason, **detail)
    print("STOP: %s" % reason)
    return 3


def _init_fail(message, code=2):
    sys.stderr.write(message.rstrip("\n") + "\n")
    return code


def cmd_init(args):
    """Start a run from a task-plan/v1 envelope under a covering grant; print RUN: <id>.

    Validates the envelope (C3-C7, fresh subjects, the task-plan kind, a schedulable
    plan), requires check-grant to cover local_reversible for this plan on the current
    branch, then cuts factory/<plan-slug> from the current branch, checks it out in the
    root and writes the run state. Exit 2 on invalid input, 3 when the grant asks."""
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        return _init_fail("root %s is not a directory" % root)
    try:
        extra = json.loads(args.budget) if args.budget is not None else {}
        check_budget(extra)
    except ValueError as e:  # JSONDecodeError is a ValueError
        return _init_fail("invalid --budget: %s" % e)
    except PlanError as e:
        return _init_fail("invalid --budget: %s" % e)
    plan_path = os.path.abspath(args.plan)
    try:
        with open(plan_path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return _init_fail("cannot read plan %s: %s" % (plan_path, e))
    rep = CC.check_envelope(plan_path, root=root)
    if rep["violations"]:
        for n, detail in rep["violations"]:
            sys.stderr.write("FAIL: C%d: %s\n" % (n, detail))
        return _init_fail("invalid plan envelope %s" % plan_path)
    if rep["stale"]:
        return _init_fail("the plan %s is stale: %s changed since it was written"
                          % (plan_path, ", ".join(rep["stale"])))
    doc = json.loads(raw)
    if doc.get("predicateType") != TASK_PLAN_KIND:
        return _init_fail("%s is a %s envelope, not task-plan/v1 (%s)"
                          % (plan_path, doc.get("predicateType"), TASK_PLAN_KIND))
    plan = doc["predicate"]["payload"]
    try:
        _, plan_tasks = _plan_tasks(plan)
        waves({k: {"depends_on": t["depends_on"]} for k, t in plan_tasks.items()})
    except PlanError as e:
        return _init_fail("invalid plan: %s" % e)
    missing = missing_commands(plan)
    if missing:
        for why in missing:
            sys.stderr.write("FAIL: %s\n" % why)
        return _init_fail("invalid plan: every task needs at least one verify step, and every "
                          "verify step a command the conductor can run (a spec criterion's "
                          "[cmd: ...] hint); see the lines above")
    bad = verify_command_problems(plan)
    if bad:
        for tid, cmd, why in bad:
            sys.stderr.write("FAIL: C6: task %s verify %s: %s\n" % (tid, json.dumps(cmd), why))
        return _init_fail("invalid plan: every verify command must follow the skill-contract "
                          "command rule: start with {python} (not python3) or a bare program "
                          "name, and use no absolute paths; see the lines above")
    base = CC.current_branch(root)
    if base is None:
        return _init_fail("%s is not a git work tree, or git cannot say which branch is "
                          "checked out" % root)
    subject = os.path.relpath(os.path.realpath(plan_path), os.path.realpath(root))
    rc, last = gate(root, "local_reversible", subject.replace(os.sep, "/"))
    ok, line = gate_line(rc, last)
    if not ok:
        print(line)
        return 3
    m = re.search(r"\bid=(\S+)", last)
    gid = m.group(1) if m else None
    gdoc, err = (CC.load_envelope(os.path.join(CC.envelope_dir(root), gid + ".json"))
                 if gid and CC.ID_RE.match(gid) else (None, ["no grant id"]))
    if err or not isinstance(gdoc, dict):
        print("GATE: ASK reason=grant-unreadable")
        return _init_fail("cannot read the covering grant %s" % gid, 3)
    grant = gdoc["predicate"]["payload"]
    run_branch = "factory/%s" % _slug(plan.get("title"))
    pattern = grant["scope"]["branch_pattern"]
    if not fnmatch.fnmatchcase(run_branch, pattern):
        print("GATE: ASK id=%s reason=run-branch" % gid)
        return _init_fail("the run branch %s would not match the grant's branch_pattern %r;"
                          " the grant does not cover this run" % (run_branch, pattern), 3)
    granted = grant.get("budget") or {}
    if not isinstance(granted, dict):
        return _init_fail("invalid budget: the grant's budget is not an object")
    # An unknown key in the human-approved grant is warned about, logged and dropped (a
    # grant written by another tool may carry more); a known key with a bad value fails.
    unknown = sorted(set(granted) - set(BUDGET_KEYS))
    granted = {k: v for k, v in granted.items() if k in BUDGET_KEYS}
    if unknown:
        sys.stderr.write("warning: the grant's budget has unknown key(s) %s; they are ignored\n"
                         % ", ".join(unknown))
    try:
        check_budget(granted)  # before comparing: a bad grant value fails, never raises
        looser = sorted(k for k in extra if k in granted and extra[k] > granted[k])
        if looser:
            raise PlanError("--budget cannot loosen the grant's budget: %s" % ", ".join(
                "%s %r > %r" % (k, extra[k], granted[k]) for k in looser))
    except PlanError as e:
        return _init_fail("invalid budget: %s" % e)
    budget = {"max_parallel": DEFAULT_PARALLEL, "max_repairs_per_task": DEFAULT_REPAIRS}
    budget.update(granted)
    budget.update(extra)  # only ever tighter than the grant, or a key it does not set
    derived = []
    if budget.get("max_dispatches") is None:
        # Cost is always bounded: one executor and one reviewer per attempt, for the first
        # attempt and every repair the budget allows.
        budget["max_dispatches"] = derived_max_dispatches(len(plan_tasks),
                                                          budget["max_repairs_per_task"])
        derived.append("max_dispatches")
    exists, _ = _git_ok(root, "rev-parse", "-q", "--verify", "refs/heads/%s" % run_branch)
    if exists:
        return _init_fail("the run branch %s already exists; finish or delete it first"
                          % run_branch)
    ok, r = _git_ok(root, "checkout", "-q", "-b", run_branch)
    if not ok:
        return _init_fail("cannot create the run branch %s: %s" % (run_branch, _git_err(r)))
    try:
        st = State.new(root=root, run_id=new_run_id(), plan=plan, plan_envelope=plan_path,
                       plan_sha256=hashlib.sha256(raw).hexdigest(), grant_id=gid,
                       run_branch=run_branch, base_branch=base, budget=budget,
                       budget_derived=derived)
    except (PlanError, OSError) as e:
        _git_ok(root, "checkout", "-q", base)
        _git_ok(root, "branch", "-D", run_branch)
        return _init_fail("cannot create the run under %s: %s" % (root, e))
    st.log("gate", action="local_reversible", ok=True, result=last, exit=rc)
    if unknown:
        st.log("budget_warning", grant=gid, unknown_keys=unknown)
    has_base, _ = _git_ok(root, "rev-parse", "-q", "--verify", "refs/remotes/origin/%s" % base)
    if not has_base:
        # finish's default PR names the base branch as --base, and the conductor never
        # pushes it: a PR against a branch the remote lacks fails.
        sys.stderr.write("warning: refs/remotes/origin/%s is absent: the pull request finish "
                         "opens targets %s, which the conductor never pushes; push it first "
                         "(git push -u origin %s)\n" % (base, base, base))
    print("RUN: %s" % st.run_id)
    return 0


def cmd_next(args):
    """Print READY: <ids> for the tasks that can start now (nothing when none can), or
    STOP: <reason> (exit 3) when a stop rule fires; the stop is recorded."""
    st = _load_current(args.root)
    if st is None:
        return 2
    if _finished(st):
        return 2
    if _stopped(st):
        return 3
    reason = check_stop(st)
    if reason:
        return _record_stop(st, reason)
    cap = st.max_parallel() if args.max is None else min(args.max, st.max_parallel())
    ready = st.ready(cap)
    left = _dispatches_left(st)
    if left is not None:
        ready = ready[:left]
    if ready:
        print("READY: %s" % " ".join(ready))
    return 0


def cmd_status(args):
    """Print one STATUS: line per task, then the budget and the max_tokens/max_usd note."""
    st = _load_current(args.root)
    if st is None:
        return 2
    for tid in st.order:
        t = st.tasks[tid]
        line = "STATUS: %s %s" % (tid, t["status"])
        # Only the commit awaiting review: a rejected or parked sha is not shown.
        if t.get("verified_head") and t["status"] == "reviewing":
            line += " verified_head=%s" % t["verified_head"]
        if isinstance(t.get("review"), dict):
            line += " review=%s" % t["review"].get("verdict")
        if t.get("park_reason"):
            line += " reason=%s" % json.dumps(t["park_reason"])
        if t.get("question"):
            line += " question=%s" % json.dumps(t["question"])
        print(line)
    derived = (" derived=%s" % ",".join(st.budget_derived)) if st.budget_derived else ""
    print("STATUS: budget %s dispatches=%d%s" % (json.dumps(st.budget, sort_keys=True),
                                                 st.dispatches, derived))
    print(BUDGET_NOTE)
    return 0


RUN_NEXT = "NEXT: run next"
RUN_FINISH = "NEXT: run finish"


def next_actions(st):
    """One `NEXT: <task> <action>` line per in-flight task, in plan order: the exact
    step a session with no memory of the run takes for it (spec section 6).

        running                      dispatch-executor          (into the existing worktree)
        verifying, last fail verify  dispatch-repair verify     (the verify tails)
        verifying, review failed     dispatch-repair review     (review.detail)
        reviewing, no verdict        dispatch-reviewer <sha>    (the verified head)
        reviewing, verdict pass      merge"""
    out = []
    for tid in st.order:
        t = st.tasks[tid]
        review = t.get("review") if isinstance(t.get("review"), dict) else {}
        if t["status"] == "running":
            out.append("NEXT: %s dispatch-executor" % tid)
        elif t["status"] == "verifying":
            out.append("NEXT: %s dispatch-repair %s"
                       % (tid, "review" if review.get("verdict") == "fail" else "verify"))
        elif t["status"] == "reviewing":
            out.append("NEXT: %s merge" % tid if review.get("verdict") == "pass"
                       else "NEXT: %s dispatch-reviewer %s" % (tid, t.get("verified_head")))
    return out


def cmd_resume(args):
    """Report the last recorded step, re-check the grant, then print the exact next
    action: one NEXT: line per in-flight task and a last `NEXT: run next|finish`.

    A run stopped by grant_ask resumes once a grant covers it again; a run stopped for
    any other reason stays stopped, and its only NEXT: line is `run finish` (a stopped
    run takes no further step; finish parks its in-flight tasks). Like `next`, resume
    records a stop rule that fires (budget_wall_clock, budget_dispatches, no_ready_tasks),
    so a run with nothing in flight and nothing ready says `run finish`. The log is only
    appended to."""
    st = _load_current(args.root)
    if st is None:
        return 2
    if _finished(st):
        return 2
    last = st.last_event()
    if last:
        print("STATUS: run=%s last_event=%s at=%s"
              % (st.run_id, last.get("event"), last.get("at")))
    else:
        print("STATUS: run=%s last_event=none" % st.run_id)
    if st.stopped and (st.stopped.get("reason") if isinstance(st.stopped, dict)
                       else st.stopped) != "grant_ask":
        _stopped(st)
        print(RUN_FINISH)
        return 3
    if not _gated(st, "local_reversible"):
        print(RUN_FINISH)
        return 3
    lifted = st.stopped
    if lifted:
        st.stopped = None
        st.save()
    st.log("resume", run=st.run_id, last_event=last.get("event") if last else None,
           lifted_stop=lifted)
    reason = check_stop(st)
    if reason:
        _record_stop(st, reason)
        print(RUN_FINISH)
        return 3
    ready = st.ready(st.max_parallel())
    left = _dispatches_left(st)
    if left is not None:
        ready = ready[:left]
    print("READY: %s" % " ".join(ready) if ready else "READY:")
    for line in next_actions(st):
        print(line)
    print(RUN_NEXT)
    return 0


def _finished(st):
    """True (with a message) once `finish` has written the run-result envelope: the
    evidence is final, so no step may change the run after it (only finish runs)."""
    if not st.finished:
        return False
    sys.stderr.write("run finished: %s; start a new run with init\n"
                     % (st.finished.get("envelope") if isinstance(st.finished, dict)
                        else st.finished))
    return True


def _stopped(st):
    """Print STOP: <reason> and return True when the run has been stopped.

    A stopped run takes no further consequential step (next, start, verify, review, merge):
    in particular a merge left pending for a human is never touched again."""
    if not st.stopped:
        return False
    reason = st.stopped.get("reason") if isinstance(st.stopped, dict) else st.stopped
    sys.stderr.write("the run is stopped: %s\n" % json.dumps(st.stopped, sort_keys=True))
    print("STOP: %s" % reason)
    return True


def _load_task(args, allowed):
    """(state, task) when the run loads and the task is in one of `allowed`, else (None, None)."""
    st = _load_current(args.root)
    if st is None:
        return None, None
    t = st.tasks.get(args.task)
    if t is None:
        sys.stderr.write("unknown task: %s\n" % args.task)
        return None, None
    if t["status"] not in allowed:
        sys.stderr.write("task %s is %s; this needs %s\n"
                         % (args.task, t["status"], " or ".join(allowed)))
        return None, None
    return st, t


def _park(st, task, reason, **fields):
    st.set_status(task, "parked", park_reason=reason, **fields)
    st.save()
    st.log("park", task=task, reason=reason)
    print("PARK: %s %s" % (task, reason))


def _park_and_stop(st, task, detail):
    """Park the task and stop the run for a human (new_human_decision). Returns 3."""
    st.stopped = {"reason": "new_human_decision", "detail": detail, "task": task,
                  "at": _rfc3339(_now())}
    _park(st, task, detail)
    st.log("stop", reason="new_human_decision", detail=detail, task=task)
    print("STOP: new_human_decision")
    return 3


def cmd_start(args):
    """Create <run>/wt/<task> on the task branch, cut from the run branch."""
    st = _load_current(args.root)
    if st is None:
        return 2
    if _finished(st):
        return 2
    if _stopped(st):
        return 3
    st, t = _load_task(args, ("pending",))
    if st is None:
        return 2
    if not re.match(TASK_ID_RE, args.task):
        sys.stderr.write("task id %r cannot name a branch or a directory\n" % args.task)
        return 2
    if args.task not in st.ready(len(st.tasks)):
        sys.stderr.write("task %s is not ready: a dependency is not proven\n" % args.task)
        return 2
    active = sum(1 for x in st.tasks.values() if x["status"] in ACTIVE)
    if active >= st.max_parallel():
        sys.stderr.write("parallel limit reached: %d task(s) in flight, max_parallel is %d; "
                         "%s is not started\n" % (active, st.max_parallel(), args.task))
        return 2
    reason = _wall_clock_stop(st)
    if reason:
        return _record_stop(st, reason)
    if _dispatches_left(st) == 0:
        if not _in_flight(st):
            return _record_stop(st, "budget_dispatches")
        # Tasks in flight may still verify, review and merge: refuse only this dispatch;
        # `next` records the stop once nothing is in flight.
        sys.stderr.write("dispatch budget spent; finish in-flight tasks (max_dispatches %d); "
                         "%s is not started\n" % (st.budget["max_dispatches"], args.task))
        st.log("dispatch_refused", task=args.task, reason="budget_dispatches",
               dispatches=st.dispatches)
        return 2
    if not _gated(st, "local_reversible"):
        return 3
    wt = os.path.join(st.dir, WT_DIR, args.task)
    branch = st.run_branch + TASK_BRANCH_SEP + args.task
    ok, r = _git_ok(st.root, "worktree", "add", "-q", wt, "-b", branch, st.run_branch)
    if not ok:
        sys.stderr.write("cannot create worktree for %s: %s\n" % (args.task, _git_err(r)))
        return 2
    st.set_status(args.task, "running", worktree=wt, branch=branch)
    st.dispatches += 1
    st.save()
    st.log("dispatch", task=args.task, kind="executor", branch=branch, worktree=wt,
           dispatches=st.dispatches)
    print("START: %s %s" % (args.task, wt))
    return 0


def _tail(text):
    return (text or "")[-TAIL:]


def _run_verify(argv, cwd, env=None, timeout=None):
    """Run one verify argv (no shell, no stdin) in cwd, in its own session and process
    group. (returncode or None, stdout, stderr). When the command ends or times out
    its process group is killed, so an ordinary backgrounded child cannot outlive the
    proof. A child that calls setsid() leaves the group and is NOT killed."""
    timeout = VERIFY_TIMEOUT if timeout is None else timeout
    try:
        p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, errors="replace",
                             env=env or git_env(), start_new_session=True)
    except OSError as e:
        return None, "", "cannot run: %s" % e
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(p)
        try:
            out, _ = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out = ""
        return None, out or "", "timed out after %ds" % timeout
    # Kill the group after every command, not only on timeout: a child that detached
    # its stdio would otherwise outlive a passing verify (unless it setsid()s away).
    _kill_group(p)
    return p.returncode, out, err


def _kill_group(p):
    """SIGKILL the command's process group. Idempotent. Misses a child that setsid()s."""
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (OSError, AttributeError):  # group already empty, or no process groups (Windows)
        try:
            p.kill()
        except OSError:
            pass


def _pinned_verify(st, task):
    """(verify steps, None) read from the pinned plan file, or (None, why).

    state.json is not the source of the commands: the plan file is re-read and must
    still hash to plan_sha256, so neither file can be edited to change the proof."""
    doc, why = _pinned_plan(st)
    if why:
        return None, why
    pred = doc.get("predicate") if isinstance(doc, dict) else None
    plan = pred.get("payload") if isinstance(pred, dict) else doc
    tasks = plan.get("tasks") if isinstance(plan, dict) else None
    if not isinstance(tasks, list):
        return None, "the plan %s has no tasks list" % st.plan_envelope
    for t in tasks:
        if isinstance(t, dict) and t.get("id") == task:
            steps = t.get("verify")
            return (steps if steps is not None else []), None
    return None, "the plan %s has no task %s" % (st.plan_envelope, task)


def _pinned_plan(st):
    """(the plan file's parsed JSON, None), or (None, why). The file must still hash to
    plan_sha256: a plan edited after init is not the plan this run ran."""
    path = st.plan_envelope
    if not isinstance(path, str) or not path:
        return None, "the run has no plan file"
    if not os.path.isabs(path):
        path = os.path.join(st.root, path)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return None, "cannot read the plan %s: %s" % (path, e)
    if hashlib.sha256(raw).hexdigest() != st.plan_sha256:
        return None, "the plan %s no longer matches its pinned sha256" % path
    try:
        return json.loads(raw), None
    except ValueError as e:
        return None, "the plan %s is not JSON: %s" % (path, e)


def _isolated_clone(st, subdir, name, *clone_args):
    """A fresh, conductor-owned clone of the root at <run>/<subdir>/<name>: (path, None)
    or (None, why). The path is cleared first (a symlink there is unlinked, never
    followed).

    It is `git clone --shared --template=` run with the global and system config
    ignored and a ceiling above it, so it inherits nothing from the shared git dir:
    no config (filters, merge drivers, templateDir), no info/attributes or
    info/exclude, no hooks, no replace refs or grafts."""
    base = os.path.join(st.dir, subdir)
    if os.path.islink(base) or (os.path.lexists(base) and not os.path.isdir(base)):
        os.unlink(base)
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, name)
    _remove_clone(st, path)
    # The ceiling keeps git from discovering the root repository (and its config)
    # above `base` while it runs the clone.
    ok, r = _git_ok(base, "clone", "-q", "--shared", "--template=", *clone_args, st.root,
                    path, env=isolated_env(GIT_CEILING_DIRECTORIES=base))
    if not ok:
        _remove_clone(st, path)
        return None, _git_err(r)
    return path, None


def _add_verify_checkout(st, task, sha):
    """A fresh, conductor-owned, detached checkout of sha: (path, None) or (None, why).

    An _isolated_clone, checked out with the global and system config ignored, so only
    committed bytes are there: an ignored file, a smudge filter or a planted hook in
    the executor's repository cannot make the verify pass."""
    path, why = _isolated_clone(st, VERIFY_DIR, "%s-%s" % (task, sha[:12]), "--no-checkout")
    if path is None:
        return None, why
    ok, r = _git_ok(path, "checkout", "-q", "--detach", sha, env=isolated_env())
    if not ok:
        _remove_clone(st, path)
        return None, _git_err(r)
    return path, None


def _remove_clone(st, path):
    """Remove a verify or merge clone, or whatever a crash or an executor left at its path.

    A symlink is unlinked, never followed. A leftover registered worktree (even a
    locked one) is removed with `worktree remove -f -f`, then pruned."""
    if os.path.islink(path):
        os.unlink(path)
    elif os.path.isdir(path):
        if os.path.exists(os.path.join(path, ".git")) and os.path.isfile(
                os.path.join(path, ".git")):
            _git_ok(st.root, "worktree", "remove", "-f", "-f", path)
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.lexists(path):
        os.unlink(path)
    _git_ok(st.root, "worktree", "prune")


def _escaping_symlinks(checkout):
    """[(path, target)] for every committed symlink (mode 120000) in checkout whose
    target is absolute or resolves outside the checkout. None when git cannot list."""
    ok, r = _git_ok(checkout, "ls-tree", "-r", "-z", "--full-tree", "HEAD", env=isolated_env())
    if not ok:
        return None
    top = os.path.realpath(checkout)
    bad = []
    for entry in r.stdout.split("\0"):
        meta, _, name = entry.partition("\t")
        if not name or not meta.startswith("120000 "):
            continue
        on_disk = os.path.join(checkout, name)
        if os.path.islink(on_disk):
            target = os.readlink(on_disk)
        else:  # core.symlinks=false: git wrote the target as a plain file
            ok, blob = _git_ok(checkout, "cat-file", "blob", meta.split()[2], env=isolated_env())
            target = blob.stdout if ok else ""
        real = os.path.realpath(os.path.join(os.path.dirname(on_disk), target))
        if os.path.isabs(target) or os.path.commonpath([top, real]) != top:
            bad.append((name, target))
    return bad


def _worktree_state(wt):
    """(HEAD sha, clean?) for a worktree, or (None, None) when git cannot say."""
    ok, r = _git_ok(wt, "status", "--porcelain", "--untracked-files=all")
    ok2, h = _git_ok(wt, "rev-parse", "--verify", "HEAD^{commit}")
    if not (ok and ok2):
        return None, None
    return h.stdout.strip(), r.stdout.strip() == ""


def cmd_verify(args):
    """Re-run every verify command of the task in its worktree. This run is the proof."""
    st = _load_current(args.root)
    if st is None:
        return 2
    if _finished(st):
        return 2
    if _stopped(st):
        return 3
    st, t = _load_task(args, ("running", "verifying"))
    if st is None:
        return 2
    reason = _wall_clock_stop(st)
    if reason:
        return _record_stop(st, reason)
    steps, why = _pinned_verify(st, args.task)
    if why:
        sys.stderr.write("cannot verify %s: %s\n" % (args.task, why))
        return 2
    if not _gated(st, "local_reversible"):
        return 3
    if not isinstance(steps, list) or not steps or any(
            not isinstance(v, dict) or v.get("command") is None for v in steps):
        # Defence in depth: init refuses such a plan, so only a state built without
        # init (or a plan read some other way) reaches this.
        _park(st, args.task, "unrunnable-verify")
        return 3
    if not t.get("worktree") or not os.path.isdir(t["worktree"]):
        sys.stderr.write("task %s has no worktree at %s\n" % (args.task, t.get("worktree")))
        return 2
    wt = t["worktree"]
    head, clean = _worktree_state(wt)
    if head is None:
        sys.stderr.write("cannot read git state of worktree %s\n" % wt)
        return 2
    review = t.get("review")
    if isinstance(review, dict) and review.get("verdict") == "fail" \
            and review.get("verified_head") == head:
        # The reviewer already rejected this very commit: proving it again proves
        # nothing new. It counts as a failure, so the repair loop stays bounded.
        return _verify_refused(st, args.task, "no new commit since the review failed",
                               "unchanged since failed review", keep_review=True)
    if not clean:
        # Only committed work can be merged, so only committed work is proven.
        return _verify_refused(st, args.task, "uncommitted changes in %s; commit them first"
                               % wt, "uncommitted changes")
    checkout, why = _add_verify_checkout(st, args.task, head)
    if checkout is None:
        sys.stderr.write("cannot check out %s for verify: %s\n" % (head, why))
        return 2
    try:
        escapes = _escaping_symlinks(checkout)
        if not escapes:
            runs = _run_steps(args.task, steps, checkout)
    finally:
        _remove_clone(st, checkout)
    if escapes is None:
        sys.stderr.write("cannot list the tree of %s\n" % head)
        return 2
    if escapes:
        # A link out of the checkout lets uncommitted bytes into the proof.
        return _verify_refused(st, args.task, "symlink(s) leave the checkout: %s" % ", ".join(
            "%s -> %s" % e for e in escapes), "symlink escapes the checkout")
    passed = all(r["ok"] for r in runs)
    after, _ = _worktree_state(wt)
    if passed and after != head:
        # The executor committed while the verify ran: prove the new head instead.
        sys.stderr.write("task %s: the worktree moved during verify; not proven\n" % args.task)
        passed = False
    # A fresh proof replaces any earlier verdict: the reviewer judges this state.
    st.set_status(args.task, "reviewing" if passed else "verifying", verify_runs=runs,
                  verified_head=head if passed else None, review=None)
    if not passed:
        _count_failure(st, args.task)
    st.save()
    st.log("verify", task=args.task, passed=passed, head=head,
           commands=[{"command": r["command"], "ok": r["ok"], "returncode": r["returncode"]}
                     for r in runs])
    # The pass line names the proven commit: the reviewer must judge exactly that one.
    print("VERIFY: %s pass %s" % (args.task, head) if passed else "VERIFY: %s fail" % args.task)
    if not passed:
        _send_back(st, args.task)
        return 3
    return 0 if _dispatch(st, args.task, "reviewer") else 3


def _verify_refused(st, task, message, reason, keep_review=False):
    """Fail a verify before any command runs. Counts as a failing verify. Returns 3.

    keep_review keeps a failed review in place, so the repair still carries the
    reviewer's detail and the same rejected commit stays refused on every retry.
    """
    fields = {} if keep_review else {"review": None}
    st.set_status(task, "verifying", verify_runs=[], verified_head=None, **fields)
    _count_failure(st, task)
    st.save()
    st.log("verify", task=task, passed=False, reason=reason, commands=[])
    sys.stderr.write("task %s: %s\n" % (task, message))
    print("VERIFY: %s fail" % task)
    _send_back(st, task)
    return 3


def _count_failure(st, task):
    """Count one failing verify or review. The first failure sends the task to its
    executor; each failure after it is a repair that did not make the task pass."""
    t = st.tasks[task]
    failures = int(t.get("failures") or 0) + 1
    t["failures"] = failures
    t["repairs"] = max(0, failures - 1)


def _dispatch(st, task, kind):
    """Spend one dispatch (kind: executor, repair or reviewer) and log it. When
    max_dispatches is spent the task parks with budget_dispatches instead: False."""
    if _dispatches_left(st) == 0:
        sys.stderr.write("dispatch budget spent (max_dispatches %d): %s needs a %s dispatch\n"
                         % (st.budget["max_dispatches"], task, kind))
        _park(st, task, "budget_dispatches")
        return False
    st.dispatches += 1
    st.save()
    st.log("dispatch", task=task, kind=kind, dispatches=st.dispatches)
    return True


def _send_back(st, task):
    """After a failing verify or review: park with verify_red_after_repairs once repairs
    reach max_repairs_per_task, else spend a repair dispatch (parking with
    budget_dispatches when none is left). Parking never stops the run."""
    if st.tasks[task]["repairs"] >= st.max_repairs():
        _park(st, task, "verify_red_after_repairs")
        return
    _dispatch(st, task, "repair")


def _run_steps(task, steps, checkout):
    """Run each verify step in checkout; one record per step, printing VERIFY: lines."""
    python_argv = CC.resolve_python()
    skill_dirs = CC.skill_index(cwd=checkout)  # the task's own copy of a project skill
    runs = []
    for v in steps:
        cmd = v["command"]
        if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) for a in cmd):
            rc, out, err, argv = (None, "", "command is not a non-empty list of strings: %r"
                                  % (cmd,), cmd)
        else:
            try:
                argv = CC.resolve_command(cmd, python_argv, skill_dirs)
            except LookupError as e:
                rc, out, err, argv = None, "", "cannot resolve: %s" % e, cmd
            else:
                rc, out, err = _run_verify(argv, checkout, env=verify_env())
        ok = rc == 0
        runs.append({"command": argv, "ok": ok, "returncode": rc,
                     "stdout_tail": _tail(out), "stderr_tail": _tail(err)})
        shown = " ".join(str(a) for a in argv) if isinstance(argv, list) else repr(argv)
        print("VERIFY: %s %s %s" % (task, "ok" if ok else "fail", shown))
    return runs


def cmd_review(args):
    """Record the reviewer's verdict. A fail sends the task back for repair and re-verify."""
    st = _load_current(args.root)
    if st is None:
        return 2
    if _finished(st):
        return 2
    if _stopped(st):
        return 3
    st, t = _load_task(args, ("reviewing",))
    if st is None:
        return 2
    reason = _wall_clock_stop(st)
    if reason:
        return _record_stop(st, reason)
    review = {"verdict": args.verdict, "detail": args.detail, "at": _rfc3339(_now()),
              "verified_head": t.get("verified_head")}
    st.set_status(args.task, "reviewing" if args.verdict == "pass" else "verifying",
                  review=review)
    if args.verdict != "pass":
        _count_failure(st, args.task)  # spec section 2.5: the same repair budget
    st.save()
    st.log("review", task=args.task, verdict=args.verdict, detail=args.detail,
           verified_head=t.get("verified_head"))
    print("REVIEW: %s %s" % (args.task, args.verdict))
    if args.verdict != "pass":
        _send_back(st, args.task)
        return 3
    return 0


def cmd_merge(args):
    """Merge the task branch into the run branch (--no-ff), then drop the worktree."""
    st = _load_current(args.root)
    if st is None:
        return 2
    if _finished(st):
        return 2
    if _stopped(st):
        return 3
    st, t = _load_task(args, ("reviewing",))
    if st is None:
        return 2
    reason = _wall_clock_stop(st)
    if reason:
        return _record_stop(st, reason)
    runs = t.get("verify_runs") or []
    if not runs or not all(r.get("ok") for r in runs):
        sys.stderr.write("task %s has no passing verify\n" % args.task)
        return 2
    if (t.get("review") or {}).get("verdict") != "pass":
        sys.stderr.write("task %s has no passing review\n" % args.task)
        return 2
    pinned = t.get("verified_head")
    if not pinned or (t.get("review") or {}).get("verified_head") != pinned:
        sys.stderr.write("task %s: the review does not cover the verified commit\n" % args.task)
        return 2
    here = CC.current_branch(st.root)
    if here != st.run_branch:
        sys.stderr.write("%s is on %s, not the run branch %s\n" % (st.root, here, st.run_branch))
        return 2
    ok, r = _git_ok(st.root, "rev-parse", "-q", "--verify", "refs/heads/%s^{commit}" % t["branch"])
    tip = r.stdout.strip() if ok else None
    if tip != pinned:
        # Something was committed (or the branch moved) after the proof: prove it again.
        st.set_status(args.task, "verifying", review=None)
        st.save()
        st.log("merge", task=args.task, ok=False, reason="branch moved after verify",
               verified_head=pinned, branch_head=tip)
        sys.stderr.write("task %s: %s is at %s, but %s was verified; verify again\n"
                         % (args.task, t["branch"], tip, pinned))
        return 2
    ok, r = _git_ok(st.root, "rev-parse", "--verify", "HEAD^{commit}")
    if not ok:
        sys.stderr.write("cannot read the run branch head: %s\n" % _git_err(r))
        return 2
    before = r.stdout.strip()
    pending, _ = _git_ok(st.root, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    if pending:
        # Not ours to abort: a human or a crash left it, and it has no stop record.
        sys.stderr.write("a merge is already in progress in %s; finish or abort it by hand, "
                         "then run merge again\n" % st.root)
        return 2
    ok, r = _git_ok(st.root, "status", "--porcelain", "--untracked-files=no")
    if not ok or r.stdout.strip():
        sys.stderr.write("the run branch in %s has uncommitted changes: %s\n"
                         % (st.root, _git_err(r) if not ok else r.stdout.strip()))
        return 2
    if not _gated(st, "local_reversible"):
        return 3
    recovered = _existing_merge(st, before, pinned)
    if recovered:
        # A crash between the root's fast-forward and st.save() left this task's merge in
        # the run branch: record it rather than merging again (or parking no-commits).
        return _merged(st, args.task, t, recovered, pinned, recovered=True)
    # The merge itself runs in an isolated clone (no shared config, attributes, merge
    # drivers or grafts), and the root only fast-forwards to its result.
    clone, why = _isolated_clone(st, MERGE_DIR, "%s-%s" % (args.task, pinned[:12]),
                                 "--no-checkout", "-b", st.run_branch)
    if clone is None:
        sys.stderr.write("cannot clone the run branch for the merge: %s\n" % why)
        return 2
    try:
        rc, sha = _merge_in_clone(st, args.task, clone, before, pinned)
        if rc is not None:
            return rc
        # Bring the clone's merge into the root: fetch it, then fast-forward only.
        ok, r = _git_ok(st.root, "fetch", "-q", "--no-tags", clone, sha,
                        env=dict(git_env(), GIT_ALLOW_PROTOCOL=FETCH_PROTOCOLS))
        ok2, h = _git_ok(st.root, "rev-parse", "--verify", "HEAD^{commit}")
        if not (ok and ok2) or h.stdout.strip() != before:
            sys.stderr.write("task %s: the run branch moved during the merge, or the fetch "
                             "failed: %s\n" % (args.task, _git_err(r) if not ok else
                                                (h.stdout.strip() if ok2 else _git_err(h))))
            st.log("merge", task=args.task, ok=False, reason="run branch moved or fetch "
                   "failed", before=before, merge=sha)
            return _park_and_stop(st, args.task, "merge-inconsistent")
        ok, r = _git_ok(st.root, "merge", "-q", "--ff-only", sha)
        ok2, h = _git_ok(st.root, "rev-parse", "--verify", "HEAD^{commit}")
        if not (ok and ok2) or h.stdout.strip() != sha:
            if ok2 and h.stdout.strip() == before:
                # git refused and nothing moved (an untracked file in the way, say): the
                # task stays reviewing, and merge can simply run again once it is cleared.
                sys.stderr.write("task %s: the run branch did not fast-forward to %s; it is "
                                 "unchanged, so fix the cause and run merge again: %s\n"
                                 % (args.task, sha, _git_err(r)))
                st.log("merge", task=args.task, ok=False, reason="fast-forward refused",
                       before=before, merge=sha, detail=_git_err(r)[-TAIL:])
                return 2
            sys.stderr.write("task %s: the run branch did not fast-forward to %s: %s\n"
                             % (args.task, sha, _git_err(r)))
            st.log("merge", task=args.task, ok=False, reason="fast-forward failed",
                   before=before, merge=sha)
            return _park_and_stop(st, args.task, "merge-inconsistent")
    finally:
        _remove_clone(st, clone)
    return _merged(st, args.task, t, sha, pinned)


def _merged(st, task, t, sha, pinned, recovered=False):
    """Record task as proven by merge commit sha, log it, drop its worktree and branch."""
    st.set_status(task, "proven", merge_commit=sha)
    st.save()
    extra = {"recovered": True} if recovered else {}
    st.log("merge", task=task, ok=True, commit=sha, branch=t["branch"],
           verified_head=pinned, **extra)
    cleaned, r = _git_ok(st.root, "worktree", "remove", "--force", t["worktree"])
    if cleaned:
        cleaned, r = _git_ok(st.root, "branch", "-D", t["branch"])
    if not cleaned:
        sys.stderr.write("merged, but cleanup failed: %s\n" % _git_err(r))
    print("MERGE: %s %s" % (task, sha))
    return 0


def _existing_merge(st, head, pinned):
    """The merge commit on the run branch's first-parent line (from head) whose second
    parent is pinned, or None. It exists only when an earlier merge of this task reached
    the root and the conductor crashed before recording it. A task with no commits of its
    own has pinned on the first-parent line itself, never as a second parent."""
    ok, r = _git_ok(st.root, "rev-list", "--first-parent", "--merges", "--parents", head)
    if not ok:
        return None
    for line in r.stdout.splitlines():
        shas = line.split()
        if len(shas) == 3 and shas[2] == pinned:
            return shas[0]
    return None


def _merge_identity(root):
    """Author and committer env for the conductor's merge commit: the root's effective
    user.name/user.email when set, else factory-conductor."""
    ok, n = _git_ok(root, "config", "user.name")
    ok2, e = _git_ok(root, "config", "user.email")
    name = n.stdout.strip() if ok and n.stdout.strip() else "factory-conductor"
    email = e.stdout.strip() if ok2 and e.stdout.strip() else "factory-conductor@localhost"
    return {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}


def _merge_in_clone(st, task, clone, before, pinned):
    """Merge pinned into before inside the isolated merge clone.

    (None, merge sha) on success; (exit code, None) when the merge must not reach the
    root: 2 for no committed work or a git refusal, 3 for a conflict (parked) or a
    result that is not a two-parent merge of before and pinned (parked, stopped)."""
    env = isolated_env()
    ok, r = _git_ok(clone, "checkout", "-q", "--detach", before, env=env)
    if ok:
        ok, r = _git_ok(clone, "fetch", "-q", "--no-tags", st.root, pinned, env=env)
    if ok:
        ok, r = _git_ok(clone, "rev-parse", "-q", "--verify", "%s^{commit}" % pinned, env=env)
        ok = ok and r.stdout.strip() == pinned
    if not ok:
        sys.stderr.write("cannot prepare the merge clone: %s\n" % _git_err(r))
        return 2, None
    ok, r = _git_ok(clone, "rev-list", "--count", "HEAD..%s" % pinned, env=env)
    if not ok or not r.stdout.strip().isdigit():
        sys.stderr.write("cannot count the task's commits: %s\n" % _git_err(r))
        return 2, None
    if int(r.stdout.strip()) == 0:
        # Nothing to merge will never change by retrying: park it so the run goes on
        # (an exit 2 left the task reviewing and the run with no way to end).
        sys.stderr.write("task %s: no committed work to merge\n" % task)
        st.log("merge", task=task, ok=False, reason="no commits", verified_head=pinned)
        _park(st, task, "no-commits")
        return 3, None
    # Merge the pinned sha, never the branch name: exactly the commit that was proven.
    ok, r = _git_ok(clone, "merge", "--no-ff", "--no-edit", "-m", "conductor: %s" % task,
                    pinned, env=isolated_env(**_merge_identity(st.root)))
    if not ok:
        detail = _git_err(r)
        started, _ = _git_ok(clone, "rev-parse", "-q", "--verify", "MERGE_HEAD", env=env)
        if not started:  # git refused before merging: not a conflict
            sys.stderr.write("git merge did not start: %s\n" % detail)
            return 2, None
        # The conflict stays in the clone, which is removed: the root is untouched.
        st.log("merge", task=task, ok=False, detail=detail[-TAIL:])
        _park(st, task, "merge-conflict")
        return 3, None
    ok, r = _git_ok(clone, "rev-list", "--parents", "-n", "1", "HEAD", env=env)
    line = r.stdout.split() if ok else []
    if len(line) != 3 or line[1] != before or line[2] != pinned:
        sys.stderr.write("task %s: the merge is not a two-parent merge of %s into %s: %s\n"
                         % (task, pinned, before, " ".join(line) or _git_err(r)))
        st.log("merge", task=task, ok=False, reason="not a two-parent merge", head=line)
        return _park_and_stop(st, task, "merge-inconsistent"), None
    return None, line[0]


def cmd_park(args):
    """Park a task with a reason. Its dependents become blocked; the run goes on."""
    st, t = _load_task(args, STATUSES)
    if st is None:
        return 2
    if _finished(st):
        return 2
    if t["status"] == "proven":
        sys.stderr.write("task %s is proven and merged; it cannot be parked\n" % args.task)
        return 2
    if not args.reason.strip():
        sys.stderr.write("--reason must not be empty\n")
        return 2
    _park(st, args.task, args.reason)
    return 0


def cmd_decision(args):
    """Park a task that needs a human decision the grant does not cover. The question is
    recorded; the task's dependents become blocked and the run goes on (exit 0)."""
    st, t = _load_task(args, STATUSES)
    if st is None:
        return 2
    if _finished(st):
        return 2
    if t["status"] == "proven":
        sys.stderr.write("task %s is proven and merged; it cannot be parked\n" % args.task)
        return 2
    if not args.question.strip():
        sys.stderr.write("--question must not be empty\n")
        return 2
    st.log("decision", task=args.task, question=args.question)
    _park(st, args.task, "new_human_decision", question=args.question)
    return 0


# ── finish: the run-result/v1 envelope, then push and PR ─────────────────────
def _rel(st, path):
    """path relative to the run's root, with forward slashes."""
    return os.path.relpath(os.path.realpath(path), os.path.realpath(st.root)).replace(os.sep, "/")


def _grant_rel(st):
    return ".skill-contract/envelopes/%s.json" % st.grant_id


def _plan_payload(doc):
    pred = doc.get("predicate") if isinstance(doc, dict) else None
    plan = pred.get("payload") if isinstance(pred, dict) else None
    return plan if isinstance(plan, dict) else {}


def _plan_tasks_by_id(plan):
    """{task id: the plan's task object}, as the plan wrote them."""
    return {t["id"]: t for t in plan.get("tasks") or []
            if isinstance(t, dict) and isinstance(t.get("id"), str)}


def _plan_steps(plan):
    """{task id: verify list} from a plan payload, as the plan wrote them."""
    return {tid: (t.get("verify") if isinstance(t.get("verify"), list) else [])
            for tid, t in _plan_tasks_by_id(plan).items()}


def missing_commands(plan):
    """["task <id> has no verify step" | "task <id> verify step <n> has no command"] for
    every task with no verify step, and every verify step whose command is null or empty
    (n counts from 1). Such a task could never be proven, so init refuses the plan."""
    out = []
    for tid, t in _plan_tasks_by_id(plan).items():
        steps = t.get("verify")
        if not isinstance(steps, list) or not steps:
            out.append("task %s has no verify step" % tid)
            continue
        for n, step in enumerate(steps, 1):
            cmd = step.get("command") if isinstance(step, dict) else None
            if cmd is None or cmd == []:
                out.append("task %s verify step %d has no command" % (tid, n))
    return out


def verify_command_problems(plan):
    """[(task id, command, why)] for every non-null verify command that breaks the
    skill-contract command rule (commandment 6: {python} rather than an interpreter name,
    no absolute paths, no other placeholders). The rule is the vendored checker's own
    (`_check_command`, a private name: there is no public one for a bare command).

    A proven task's first command becomes a run-result assertion, so a plan that breaks
    C6 could never be finished; init refuses it instead."""
    out = []
    for tid, steps in _plan_steps(plan).items():
        for step in steps:
            cmd = step.get("command") if isinstance(step, dict) else None
            if cmd is None:
                continue  # missing_commands reports it (init refuses the plan first)
            viol = []
            CC._check_command(cmd, viol)
            out += [(tid, cmd, detail) for _, detail in viol]
    return out


def _leftover_worktrees(st):
    """[{task, path, reason}] for every unproven task whose worktree is still on disk
    (a merge-conflict park keeps it for a human). finish never deletes them."""
    out = []
    for tid in st.order:
        t = st.tasks[tid]
        wt = t.get("worktree")
        if t["status"] != "proven" and isinstance(wt, str) and os.path.isdir(wt):
            out.append({"task": tid, "path": _rel(st, wt), "reason": t.get("park_reason")})
    return out


def _plan_form(runs, steps):
    """The verify runs with each command in the plan's own form ({python},
    {skill_dir:…}), not the resolved absolute argv the conductor ran. verify re-reads the
    steps from the pinned plan, so run i is step i."""
    out = []
    for i, r in enumerate(runs):
        step = steps[i] if i < len(steps) and isinstance(steps[i], dict) else {}
        out.append({"command": step.get("command", r.get("command")), "ok": bool(r.get("ok")),
                    "returncode": r.get("returncode")})
    return out


def run_result_payload(st, plan_doc=None):
    """The run-result/v1 payload for a run. `log_sha256` and `log_bytes` are None here:
    finish sets them once the `finish` event is the log's last line.

    plan_doc is the parsed plan envelope; None re-reads the pinned plan file (a plan
    that no longer matches plan_sha256 raises ValueError)."""
    if plan_doc is None:
        plan_doc, why = _pinned_plan(st)
        if why:
            raise ValueError(why)
    plan = _plan_payload(plan_doc)
    plan_tasks = _plan_tasks_by_id(plan)
    steps = _plan_steps(plan)
    pred = plan_doc.get("predicate") if isinstance(plan_doc, dict) else {}
    tasks = []
    for tid in st.order:
        t = st.tasks[tid]
        review = t.get("review")
        title = (plan_tasks.get(tid) or {}).get("title")
        tasks.append({
            "id": tid, "title": title if isinstance(title, str) else None,
            "status": t["status"],
            "verify": _plan_form(t.get("verify_runs") or [], steps.get(tid) or []),
            "verified_head": t.get("verified_head"),
            "review": ({"verdict": review.get("verdict"), "detail": review.get("detail")}
                       if isinstance(review, dict) else None),
            "merge_commit": t.get("merge_commit"), "park_reason": t.get("park_reason"),
            "question": t.get("question"), "depends_on": list(t.get("depends_on") or [])})
    stopped = None
    if st.stopped:
        s = st.stopped if isinstance(st.stopped, dict) else {"reason": st.stopped}
        stopped = {k: s[k] for k in ("reason", "at", "detail", "task") if k in s}
    grant_path = os.path.join(st.root, *_grant_rel(st).split("/")) if st.grant_id else None
    return {
        "run_id": st.run_id,
        "plan": {"id": (pred or {}).get("id"), "path": plan_subject(st),
                 "sha256": st.plan_sha256, "title": plan.get("title")},
        "grant": ({"id": st.grant_id, "path": _grant_rel(st),
                   "sha256": CC.sha256_file(grant_path)}
                  if grant_path and os.path.isfile(grant_path) else None),
        "run_branch": st.run_branch, "base_branch": st.base_branch,
        "created_at": st.created_at, "stopped": stopped,
        "log": _rel(st, st.log_path), "log_sha256": None, "log_bytes": None,
        "budget": dict(st.budget), "dispatches": st.dispatches,
        "budget_note": BUDGET_NOTE_TEXT,
        "tasks": tasks, "leftover_worktrees": _leftover_worktrees(st)}


def _run_result_assertions(st, plan_doc):
    """One passed `verify:<task>` assertion per proven task, carrying that task's first
    verify command exactly as the plan wrote it, its subject the plan envelope."""
    steps = _plan_steps(_plan_payload(plan_doc))
    plan_pin = CC.pin(st.root, plan_subject(st))
    out = []
    for tid in st.order:
        if st.tasks[tid]["status"] != "proven":
            continue
        first = (steps.get(tid) or [{}])[0]
        cmd = first.get("command") if isinstance(first, dict) else None
        out.append({"test": "verify:%s" % tid, "assertedBy": {"skill": CONDUCTOR_SKILL},
                    "result": {"outcome": "passed"}, "command": cmd,
                    "subject": [dict(plan_pin)]})
    return out


def one_line(text):
    """text with every run of whitespace (newlines included) collapsed to one space."""
    return " ".join(str(text if text is not None else "").split())


def code(text):
    """Untrusted text as one inline code span: newlines collapsed, and a backtick fence
    longer than any backtick run inside, so it cannot open a heading, list or link, and
    GitHub does not turn @mentions or closing keywords ("Closes #1") in it into actions."""
    s = one_line(text)
    runs = [len(m) for m in re.findall(r"`+", s)]
    fence = "`" * (max(runs) + 1 if runs else 1)
    pad = " " if s.startswith("`") or s.endswith("`") or not s else ""
    return "%s%s%s%s%s" % (fence, pad, s, pad, fence)


def pr_body(payload, envelope_rel):
    """The pull request body, built only from the run-result payload. Every string that
    came from the plan, a reviewer or a parker is untrusted and rendered through code()."""
    tasks = payload["tasks"]
    by = {s: [t for t in tasks if t["status"] == s] for s in STATUSES}
    title = payload["plan"].get("title") or payload["plan"].get("path")

    def name(t):
        return "%s %s" % (code(t["id"]), code(t["title"])) if t.get("title") else code(t["id"])

    lines = ["## factory-conductor run %s" % code(payload["run_id"]), "",
             "Plan: %s (%s), run branch %s." % (code(title), code(payload["plan"].get("path")),
                                                code(payload["run_branch"])), "",
             "Proven: %d of %d tasks." % (len(by["proven"]), len(tasks)), ""]
    stopped = payload.get("stopped") or {}
    lines += ["Stopped: %s%s." % (code(stopped["reason"]) if stopped.get("reason")
                                  else "not stopped (finished by hand)",
                                  " at %s" % code(stopped["at"]) if stopped.get("at") else ""),
              ""]
    lines.append("### Proven (%d)" % len(by["proven"]))
    for t in by["proven"]:
        ok = sum(1 for v in t["verify"] if v["ok"])
        lines.append("- %s: merged %s; verify %d/%d passed; review %s"
                     % (name(t), code((t["merge_commit"] or "")[:12]), ok, len(t["verify"]),
                        code((t["review"] or {}).get("verdict"))))
    lines += ["", "### Parked (%d)" % len(by["parked"])]
    for t in by["parked"]:
        q = " (question: %s)" % code(t["question"]) if t.get("question") else ""
        lines.append("- %s: %s%s" % (name(t), code(t["park_reason"]), q))
    lines += ["", "### Blocked (%d)" % len(by["blocked"])]
    for t in by["blocked"]:
        lines.append("- %s: depends on %s" % (
            name(t), ", ".join(code(d) for d in t["depends_on"]) or "-"))
    rest = [t for t in tasks if t["status"] not in ("proven", "parked", "blocked")]
    if rest:
        lines += ["", "### Not finished (%d)" % len(rest)]
        lines += ["- %s: %s" % (name(t), code(t["status"])) for t in rest]
    if payload["leftover_worktrees"]:
        lines += ["", "### Worktrees kept for a human (%d)" % len(payload["leftover_worktrees"])]
        lines += ["- %s: %s (%s)" % (code(w["task"]), code(w["path"]), code(w["reason"]))
                  for w in payload["leftover_worktrees"]]
    lines += ["", "### Budget",
              "Dispatches: %d; budget: %s." % (payload["dispatches"],
                                               code(json.dumps(payload["budget"],
                                                               sort_keys=True))),
              "Budget note: %s." % payload["budget_note"], "",
              "Evidence: %s (run-result/v1). Every proven task was re-run by the conductor "
              "and reviewed before it merged; a human merges this pull request."
              % code(envelope_rel), ""]
    return "\n".join(lines)


def expand_cmd(argv, values):
    """argv with each token that is exactly {name} (name in values) replaced."""
    out = []
    for a in argv:
        m = re.match(r"^\{([a-z_]+)\}\Z", a)
        out.append(str(values[m.group(1)]) if m and m.group(1) in values else a)
    return out


def _cmd_arg(text, default, flag):
    """The argv list a --push-cmd/--pr-cmd JSON names (default when None). ValueError
    when it is not a non-empty list of strings."""
    if text is None:
        return list(default)
    cmd = json.loads(text)
    if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) and a for a in cmd):
        raise ValueError("%s must be a JSON list of non-empty strings" % flag)
    return cmd


PUSH_LONG_OPTIONS = frozenset({"--set-upstream", "--porcelain", "--quiet"})
PUSH_SHORT_FLAGS = frozenset("uq")
REMOTE_NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9._-]*\Z"


def push_cmd_problem(argv, run_branch):
    """None when argv (placeholders already expanded) is a push the grant can cover, else
    why not. A grant covers pushing the current branch to the remote branch of the same
    name, never a force-push (SPEC, commandment 10), so the only shape allowed is

        git push [--set-upstream|--porcelain|--quiet|-u|-q|-uq ...] <remote> <run_branch>

    with <remote> a remote name (not a URL or path) and no refspec syntax (':' or '+')."""
    if argv[:2] != ["git", "push"]:
        return "the push command must start with exactly: git push"
    positionals = []
    for a in argv[2:]:
        if a.startswith("--"):
            if a not in PUSH_LONG_OPTIONS:
                return "option %r is not allowed (allowed: %s, -u, -q)" % (
                    a, ", ".join(sorted(PUSH_LONG_OPTIONS)))
        elif a.startswith("-") and len(a) > 1:
            if not set(a[1:]) <= PUSH_SHORT_FLAGS:
                return "short option %r is not allowed (only -u and -q)" % a
        else:
            positionals.append(a)
    if len(positionals) != 2:
        return "the push must name exactly <remote> <run_branch>, got %r" % (positionals,)
    remote, ref = positionals
    if not re.match(REMOTE_NAME_RE, remote):
        return "the remote %r must be a remote name" % remote
    if ref != run_branch or ":" in ref or ref.startswith("+"):
        return "the push may only name the run branch %s, got %r" % (run_branch, ref)
    return None


def explicit_push(argv, run_branch):
    """An allowlisted push argv with its run-branch positional (the last one) rewritten to
    the explicit, non-forced refspec refs/heads/<rb>:refs/heads/<rb>. A refspec with no
    ':' is mapped through remote.<name>.push, which the executor can write in the shared
    .git/config (`+refs/heads/<rb>:refs/heads/main` would force-update main); git never
    remaps a refspec that has a ':', and with no '+' the push is never forced."""
    out = list(argv)
    for i in range(len(out) - 1, 1, -1):
        if not out[i].startswith("-"):
            if out[i] != run_branch:
                raise ValueError("the last positional is not the run branch: %r" % out[i])
            out[i] = "refs/heads/%s:refs/heads/%s" % (run_branch, run_branch)
            return out
    raise ValueError("the push names no run branch")


def _run_remote(st, event, argv, protocols=None):
    """Run one remote step (no shell, no stdin) in the root, in its own process group,
    killed when it ends or times out, as verify does. Log it as `event`. (ok?, stdout)

    protocols, when given, is the GIT_ALLOW_PROTOCOL list git may use (the push)."""
    env = safe_config_env()
    if protocols:
        env["GIT_ALLOW_PROTOCOL"] = protocols
    rc, out, err = _run_verify(argv, st.root, env=env, timeout=REMOTE_TIMEOUT)
    st.log(event, command=argv, returncode=rc, stdout_tail=_tail(out), stderr_tail=_tail(err))
    if rc != 0:
        sys.stderr.write("%s failed (exit %s): %s\n" % (event, rc, _tail(err).strip()))
    return rc == 0, out


def _in_flight_ids(st):
    return [tid for tid in st.order if st.tasks[tid]["status"] in ACTIVE]


def _pending_remote(st):
    """The remote steps of a finished run that have not completed: a subset of push, pr."""
    fin = st.finished if isinstance(st.finished, dict) else {}
    return [k for k, done in (("push", fin.get("pushed")), ("pr", fin.get("pr"))) if not done]


def cmd_finish(args):
    """Write the run-result/v1 envelope (FINISH: <path>), then push the run branch and open
    the PR, each only when its gate is COVERED.

    Works on a stopped run: it is how a run ends, and it parks the stopped run's
    running, verifying or reviewing tasks with in_flight_at_stop first. On a run that is
    not stopped it refuses (2) while a task is in flight. Once an envelope exists, a plain `finish` prints that FINISH:
    line and exits 0 when both remote steps completed; otherwise it also prints
    REMOTE: pending push|pr and exits 3, and `finish --retry-remote` re-gates and runs the
    steps still pending. Exit 0 when the envelope is written and both remote steps ran;
    3 when a gate asked (GATE: ASK, the remaining remote steps are skipped) or a remote
    step failed; 2 on invalid input (no envelope is written)."""
    st = _load_current(args.root)
    if st is None:
        return 2
    try:
        push_cmd = _cmd_arg(args.push_cmd, DEFAULT_PUSH_CMD, "--push-cmd")
        pr_cmd = _cmd_arg(args.pr_cmd, DEFAULT_PR_CMD, "--pr-cmd")
    except ValueError as e:  # JSONDecodeError is a ValueError
        sys.stderr.write("invalid command: %s\n" % e)
        return 2
    why = push_cmd_problem(expand_cmd(push_cmd, {"run_branch": st.run_branch}), st.run_branch)
    if why:
        sys.stderr.write("--push-cmd is not a push a grant covers: %s\n" % why)
        return 2
    if isinstance(st.finished, dict) and st.finished.get("envelope"):
        return _finish_again(st, args, push_cmd, pr_cmd)
    if args.retry_remote:
        sys.stderr.write("nothing to retry: this run has no run-result envelope; run finish\n")
        return 2
    busy = _in_flight_ids(st)
    if busy and not st.stopped:
        sys.stderr.write("cannot finish: task(s) %s are still running, verifying or reviewing;"
                         " finish them, or park them (park <task> --reason ...)\n"
                         % ", ".join(busy))
        return 2
    plan_doc, why = _pinned_plan(st)
    if why:
        sys.stderr.write("cannot finish: %s\n" % why)
        return 2
    if not (isinstance(st.grant_id, str) and CC.ID_RE.match(st.grant_id)
            and os.path.isfile(os.path.join(st.root, *_grant_rel(st).split("/")))):
        sys.stderr.write("cannot finish: the run's grant %s is not under %s\n"
                         % (st.grant_id, CC.envelope_dir(st.root)))
        return 2
    # A stopped run takes no further step, so its in-flight tasks can never finish:
    # park them (their dependents become blocked) so an unattended run can still end.
    for tid in busy:
        _park(st, tid, "in_flight_at_stop")
    payload = run_result_payload(st, plan_doc)
    statement = CC.build_statement(RUN_RESULT_KIND, CONDUCTOR_SKILL, CONDUCTOR_VERSION, st.root,
                                   [plan_subject(st), _grant_rel(st)], payload,
                                   _run_result_assertions(st, plan_doc))
    payload["log_sha256"], payload["log_bytes"] = "0" * 64, 0  # shape only, for the check
    viol = CC.check_statement(statement)
    if viol:
        for n, detail in viol:
            sys.stderr.write("FAIL: C%d: %s\n" % (n, detail))
        return _init_fail("cannot finish: the run-result envelope would be invalid")
    eid = statement["predicate"]["id"]
    env_rel = ".skill-contract/envelopes/%s.json" % eid
    by = {s: [t["id"] for t in payload["tasks"] if t["status"] == s]
          for s in ("proven", "parked", "blocked")}
    # The finish event is the last line the digest covers. The log is append-only, so
    # the first log_bytes bytes never change; the gate, push and pr events that follow
    # are after that prefix. A crash between this event and st.save() below leaves no
    # finished record, so a re-run writes a second finish event and a second envelope
    # (a new id, never an overwrite); that is harmless.
    record = st.log("finish", id=eid, envelope=env_rel,
                    stopped=(payload["stopped"] or {}).get("reason"), **by)
    line = (json.dumps(record, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    with open(st.log_path, "rb") as f:
        data = f.read()
    end = data.find(line)
    if end < 0:
        return _init_fail("cannot finish: the finish event is not in %s" % st.log_path)
    prefix = data[:end + len(line)]
    payload["log_sha256"] = hashlib.sha256(prefix).hexdigest()
    payload["log_bytes"] = len(prefix)
    path = CC.write_envelope(st.root, statement)
    st.finished = {"envelope": path, "id": eid, "sha256": CC.sha256_file(path),
                   "at": _rfc3339(_now()), "pushed": False, "pr": False}
    st.save()
    print("FINISH: %s" % path)
    return _finish_remote(st, payload, env_rel, push_cmd, pr_cmd)


def _finish_again(st, args, push_cmd, pr_cmd):
    """finish on a run that already has its envelope: report it, and with --retry-remote
    re-gate and run the remote steps still pending."""
    path = st.finished["envelope"]
    print("FINISH: %s" % path)
    pending = _pending_remote(st)
    if not pending:
        return 0
    if not args.retry_remote:
        print("REMOTE: pending %s (run finish --retry-remote)" % " ".join(pending))
        return 3
    want = st.finished.get("sha256")
    try:
        got = CC.sha256_file(path)
    except OSError as e:
        got = None
        sys.stderr.write("cannot read the run-result envelope %s: %s\n" % (path, e))
    if not want or got != want:
        sys.stderr.write("the run-result envelope %s changed since finish wrote it "
                         "(sha256 %s, recorded %s); not retrying\n" % (path, got, want))
        return 2
    doc, err = CC.load_envelope(path)
    if err or not isinstance(doc, dict) or doc.get("predicateType") != RUN_RESULT_KIND:
        sys.stderr.write("cannot read the run-result envelope %s: %s\n" % (path, err))
        return 2
    payload = doc["predicate"]["payload"]
    return _finish_remote(st, payload, _rel(st, path), push_cmd, pr_cmd)


def _finish_remote(st, payload, env_rel, push_cmd, pr_cmd):
    """gate push_branch -> push -> gate open_pr -> PR, skipping a step already recorded
    as done in st.finished. The first ASK or failure ends it (3)."""
    values = {"run_branch": st.run_branch, "base_branch": st.base_branch,
              "title": one_line(payload["plan"].get("title") or st.run_branch)}
    if not st.finished.get("pushed"):
        ok, _ = _gate_logged(st, "push_branch")
        if not ok:
            return 3
        here = CC.current_branch(st.root)
        if here != st.run_branch:
            # A grant covers pushing the current branch only.
            line = "GATE: ASK reason=branch (%s is on %s, not the run branch %s)" % (
                st.root, here, st.run_branch)
            st.log("gate", action="push_branch", ok=False, result=line, exit=3)
            print(line)
            return 3
        argv = expand_cmd(push_cmd, values)
        why = push_cmd_problem(argv, st.run_branch)  # checked again, right before it runs
        if why:
            sys.stderr.write("--push-cmd is not a push a grant covers: %s\n" % why)
            return 2
        ok, out = _run_remote(st, "push", explicit_push(argv, st.run_branch),
                              protocols=PUSH_PROTOCOLS)
        if not ok:
            return 3
        st.finished["pushed"] = _tail(out).strip() or True
        st.save()
    if not st.finished.get("pr"):
        ok, _ = _gate_logged(st, "open_pr")
        if not ok:
            return 3
        fd, body_path = tempfile.mkstemp(dir=st.dir, prefix=".pr-body.", suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(pr_body(payload, env_rel))
            values["body_file"] = body_path
            ok, out = _run_remote(st, "pr", expand_cmd(pr_cmd, values))
        finally:
            try:
                os.unlink(body_path)
            except FileNotFoundError:
                pass
        if not ok:
            return 3
        st.finished["pr"] = _tail(out).strip() or True
        st.save()
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="conductor.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init")
    s.add_argument("--plan", required=True)
    s.add_argument("--root", default=".")
    s.add_argument("--budget", default=None)
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("gate")
    s.add_argument("--action", required=True, choices=CC.ACTION_CLASSES)
    s.add_argument("--root", default=".")
    s.set_defaults(fn=cmd_gate)
    s = sub.add_parser("next")
    s.add_argument("--root", default=".")
    s.add_argument("--max", type=_positive_int, default=None)
    s.set_defaults(fn=cmd_next)
    for name, fn in (("status", cmd_status), ("resume", cmd_resume)):
        s = sub.add_parser(name)
        s.add_argument("--root", default=".")
        s.set_defaults(fn=fn)
    s = sub.add_parser("finish")
    s.add_argument("--root", default=".")
    s.add_argument("--push-cmd", default=None)
    s.add_argument("--pr-cmd", default=None)
    s.add_argument("--retry-remote", action="store_true")
    s.set_defaults(fn=cmd_finish)
    for name, fn in (("start", cmd_start), ("verify", cmd_verify), ("review", cmd_review),
                     ("merge", cmd_merge), ("park", cmd_park), ("decision", cmd_decision)):
        s = sub.add_parser(name)
        s.add_argument("task")
        s.add_argument("--root", default=".")
        if name == "review":
            s.add_argument("--verdict", required=True, choices=("pass", "fail"))
            s.add_argument("--detail", default="")
        if name == "park":
            s.add_argument("--reason", required=True)
        if name == "decision":
            s.add_argument("--question", required=True)
        s.set_defaults(fn=fn)
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
