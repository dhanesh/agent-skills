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
    python3 conductor.py init --plan <payload.json> [--root <repo>]    # prints RUN: <id>
                              [--grant-id ID] [--run-branch B]
                              [--base-branch B] [--max-parallel N]
    python3 conductor.py next [--root <repo>] [--max N]                # prints READY: T1 T2
    python3 conductor.py status [--root <repo>]                        # one STATUS: line per task
    python3 conductor.py resume [--root <repo>]                        # last step + READY:
    python3 conductor.py start <task> [--root <repo>]                  # START: <task> <worktree>
    python3 conductor.py verify <task> [--root <repo>]                 # VERIFY: <task> pass|fail
    python3 conductor.py review <task> --verdict pass|fail [--detail T] [--root <repo>]
    python3 conductor.py merge <task> [--root <repo>]                  # MERGE: <task> <sha>
    python3 conductor.py park <task> --reason R [--root <repo>]        # PARK: <task> <reason>

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
merges with --no-ff into the run branch (which MUST be checked out at --root),
then removes the worktree and deletes the task branch. A conflict is aborted,
leaving the run branch clean, and parks the task.

`init` takes a task-plan payload file for now; it will take the task-plan
envelope and check the grant once gating exists.

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
import hashlib  # noqa: E402
import json  # noqa: E402
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
STOP_REASONS = ("budget_wall_clock", "budget_dispatches", "grant_ask",
                "new_human_decision", "verify_red_after_repairs", "no_ready_tasks")
DEFAULT_PARALLEL = 2

ACTIVE = ("running", "verifying", "reviewing")
TASK_FIELDS = ("status", "depends_on", "verify", "repairs", "branch", "worktree",
               "verify_runs", "verified_head", "review", "merge_commit", "park_reason")
RUNS_DIR = os.path.join(".skill-contract", "runs")
STATE_FILE = "state.json"
LOG_FILE = "autonomy-log.jsonl"
WT_DIR = "wt"
VERIFY_DIR = "verify"
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
        self.dispatches = data.get("dispatches", 0)
        self.stopped = data.get("stopped")
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
            run_branch, base_branch, budget):
        """Validate the plan, create the run directory, write state.json and log `init`.

        Raises PlanError on a malformed plan, before anything is written."""
        order, plan_tasks = _plan_tasks(plan)
        schedule = waves({k: {"depends_on": t["depends_on"]} for k, t in plan_tasks.items()})
        budget = dict(budget or {})
        mp = budget.get("max_parallel")
        if mp is not None and (not isinstance(mp, int) or isinstance(mp, bool) or mp < 1):
            raise PlanError("budget.max_parallel must be an integer >= 1, got %r" % (mp,))
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
                  "created_at": _rfc3339(_now()), "budget": budget, "dispatches": 0,
                  "stopped": None, "tasks": tasks, "order": order}, directory)
        st.save()
        st.log("init", run=run_id, plan=plan_envelope, plan_sha256=plan_sha256,
               waves=schedule)
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
        return cls(data, os.path.dirname(os.path.abspath(path)))

    def to_dict(self):
        return {"root": self.root, "run_id": self.run_id,
                "plan_envelope": self.plan_envelope, "plan_sha256": self.plan_sha256,
                "grant_id": self.grant_id, "run_branch": self.run_branch,
                "base_branch": self.base_branch, "created_at": self.created_at,
                "budget": self.budget, "dispatches": self.dispatches,
                "stopped": self.stopped, "tasks": self.tasks, "order": self.order}

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
    """os.environ without the variables that point git at another repository."""
    env = dict(os.environ)
    for k in GIT_SCRUB:
        env.pop(k, None)
    return env


# The shared git dir is writable by the executor, so it is hostile: every conductor
# git call switches off hooks and fsmonitor, the two config keys that run a program
# on an ordinary status/merge/worktree call.
GIT_SAFE = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null")


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


def cmd_init(args):
    """Build a run from a task-plan payload file and print RUN: <id>."""
    try:
        with open(args.plan, "rb") as f:
            raw = f.read()
        plan = json.loads(raw)
    except (OSError, ValueError) as e:
        sys.stderr.write("cannot read plan %s: %s\n" % (args.plan, e))
        return 2
    if not isinstance(plan, dict):
        sys.stderr.write("invalid plan: %s is not a JSON object\n" % args.plan)
        return 2
    budget = {"max_parallel": DEFAULT_PARALLEL if args.max_parallel is None
              else args.max_parallel}
    try:
        st = State.new(root=args.root, run_id=new_run_id(), plan=plan,
                       plan_envelope=os.path.abspath(args.plan),
                       plan_sha256=hashlib.sha256(raw).hexdigest(),
                       grant_id=args.grant_id,
                       run_branch=args.run_branch or "factory/%s" % _slug(plan.get("title")),
                       base_branch=args.base_branch, budget=budget)
    except PlanError as e:
        sys.stderr.write("invalid plan: %s\n" % e)
        return 2
    except OSError as e:
        sys.stderr.write("cannot create run under %s: %s\n" % (args.root, e))
        return 2
    print("RUN: %s" % st.run_id)
    return 0


def cmd_next(args):
    """Print READY: <ids> for the tasks that can start now (nothing when none can)."""
    st = _load_current(args.root)
    if st is None:
        return 2
    ready = st.ready(st.max_parallel() if args.max is None else args.max)
    if ready:
        print("READY: %s" % " ".join(ready))
    return 0


def cmd_status(args):
    """Print one STATUS: line per task."""
    st = _load_current(args.root)
    if st is None:
        return 2
    for tid in st.order:
        t = st.tasks[tid]
        line = "STATUS: %s %s" % (tid, t["status"])
        if t.get("park_reason"):
            line += " reason=%s" % json.dumps(t["park_reason"])
        print(line)
    return 0


def cmd_resume(args):
    """Report the last recorded step and the ready set. The log is only appended to."""
    st = _load_current(args.root)
    if st is None:
        return 2
    last = st.last_event()
    if last:
        print("STATUS: run=%s last_event=%s at=%s"
              % (st.run_id, last.get("event"), last.get("at")))
    else:
        print("STATUS: run=%s last_event=none" % st.run_id)
    ready = st.ready(st.max_parallel())
    print("READY: %s" % " ".join(ready) if ready else "READY:")
    st.log("resume", run=st.run_id, last_event=last.get("event") if last else None)
    return 0


def _stopped(st):
    """Print STOP: <reason> and return True when the run has been stopped.

    A stopped run takes no further consequential step (start, verify, review, merge):
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


def _park(st, task, reason):
    st.set_status(task, "parked", park_reason=reason)
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
    wt = os.path.join(st.dir, WT_DIR, args.task)
    branch = st.run_branch + TASK_BRANCH_SEP + args.task
    ok, r = _git_ok(st.root, "worktree", "add", "-q", wt, "-b", branch, st.run_branch)
    if not ok:
        sys.stderr.write("cannot create worktree for %s: %s\n" % (args.task, _git_err(r)))
        return 2
    st.set_status(args.task, "running", worktree=wt, branch=branch)
    st.dispatches += 1
    st.save()
    st.log("dispatch", task=args.task, branch=branch, worktree=wt, dispatches=st.dispatches)
    print("START: %s %s" % (args.task, wt))
    return 0


def _tail(text):
    return (text or "")[-TAIL:]


def _run_verify(argv, cwd):
    """Run one verify argv (no shell, no stdin) in cwd, in its own session and process
    group. (returncode or None, stdout, stderr). When the command ends or times out
    its process group is killed, so an ordinary backgrounded child cannot outlive the
    proof. A child that calls setsid() leaves the group and is NOT killed."""
    try:
        p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, errors="replace",
                             env=git_env(), start_new_session=True)
    except OSError as e:
        return None, "", "cannot run: %s" % e
    try:
        out, err = p.communicate(timeout=VERIFY_TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill_group(p)
        try:
            out, _ = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out = ""
        return None, out or "", "timed out after %ds" % VERIFY_TIMEOUT
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
        doc = json.loads(raw)
    except ValueError as e:
        return None, "the plan %s is not JSON: %s" % (path, e)
    pred = doc.get("predicate") if isinstance(doc, dict) else None
    plan = pred.get("payload") if isinstance(pred, dict) else doc
    tasks = plan.get("tasks") if isinstance(plan, dict) else None
    if not isinstance(tasks, list):
        return None, "the plan %s has no tasks list" % path
    for t in tasks:
        if isinstance(t, dict) and t.get("id") == task:
            steps = t.get("verify")
            return (steps if steps is not None else []), None
    return None, "the plan %s has no task %s" % (path, task)


def _add_verify_checkout(st, task, sha):
    """A fresh, conductor-owned, detached checkout of sha: (path, None) or (None, why).

    It is a new `git clone --shared --no-checkout` of the root, checked out with the
    global and system config ignored. It inherits nothing from the shared git dir
    (no config, no info/attributes, no info/exclude, no hooks), so only committed
    bytes are there: an ignored file, a smudge filter or a planted hook in the
    executor's repository cannot make the verify pass."""
    base = os.path.join(st.dir, VERIFY_DIR)
    if os.path.islink(base) or (os.path.lexists(base) and not os.path.isdir(base)):
        os.unlink(base)
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, "%s-%s" % (task, sha[:12]))
    _remove_verify_checkout(st, path)
    # The ceiling keeps git from discovering the root repository (and its config)
    # above `base` while it runs the clone.
    ok, r = _git_ok(base, "clone", "-q", "--shared", "--no-checkout", st.root, path,
                    env=isolated_env(GIT_CEILING_DIRECTORIES=base))
    if ok:
        ok, r = _git_ok(path, "checkout", "-q", "--detach", sha, env=isolated_env())
    if not ok:
        _remove_verify_checkout(st, path)
        return None, _git_err(r)
    return path, None


def _remove_verify_checkout(st, path):
    """Remove a verify checkout, or whatever a crash or an executor left at its path.

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
    if _stopped(st):
        return 3
    st, t = _load_task(args, ("running", "verifying"))
    if st is None:
        return 2
    steps, why = _pinned_verify(st, args.task)
    if why:
        sys.stderr.write("cannot verify %s: %s\n" % (args.task, why))
        return 2
    if not isinstance(steps, list) or not steps or any(
            not isinstance(v, dict) or v.get("command") is None for v in steps):
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
        _remove_verify_checkout(st, checkout)
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
    st.save()
    st.log("verify", task=args.task, passed=passed, head=head,
           commands=[{"command": r["command"], "ok": r["ok"], "returncode": r["returncode"]}
                     for r in runs])
    print("VERIFY: %s %s" % (args.task, "pass" if passed else "fail"))
    return 0 if passed else 3


def _verify_refused(st, task, message, reason):
    """Fail a verify before any command runs. Returns 3."""
    st.set_status(task, "verifying", verify_runs=[], verified_head=None, review=None)
    st.save()
    st.log("verify", task=task, passed=False, reason=reason, commands=[])
    sys.stderr.write("task %s: %s\n" % (task, message))
    print("VERIFY: %s fail" % task)
    return 3


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
                rc, out, err = _run_verify(argv, checkout)
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
    if _stopped(st):
        return 3
    st, t = _load_task(args, ("reviewing",))
    if st is None:
        return 2
    review = {"verdict": args.verdict, "detail": args.detail, "at": _rfc3339(_now()),
              "verified_head": t.get("verified_head")}
    st.set_status(args.task, "reviewing" if args.verdict == "pass" else "verifying",
                  review=review)
    st.save()
    st.log("review", task=args.task, verdict=args.verdict, detail=args.detail,
           verified_head=t.get("verified_head"))
    print("REVIEW: %s %s" % (args.task, args.verdict))
    return 0 if args.verdict == "pass" else 3


def cmd_merge(args):
    """Merge the task branch into the run branch (--no-ff), then drop the worktree."""
    st = _load_current(args.root)
    if st is None:
        return 2
    if _stopped(st):
        return 3
    st, t = _load_task(args, ("reviewing",))
    if st is None:
        return 2
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
    ok, r = _git_ok(st.root, "rev-list", "--count", "%s..%s" % (st.run_branch, pinned))
    if not ok or not r.stdout.strip().isdigit() or int(r.stdout.strip()) == 0:
        sys.stderr.write("task %s: no committed work to merge\n" % args.task)
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
    # Merge the pinned sha, never the branch name: exactly the commit that was proven.
    ok, r = _git_ok(st.root, "merge", "--no-ff", "--no-edit",
                         "-m", "conductor: %s" % args.task, pinned)
    if not ok:
        detail = _git_err(r)
        started, _ = _git_ok(st.root, "rev-parse", "-q", "--verify", "MERGE_HEAD")
        if not started:  # git refused before merging (e.g. a dirty tree): not a conflict
            sys.stderr.write("git merge did not start: %s\n" % detail)
            return 2
        aborted, ar = _git_ok(st.root, "merge", "--abort")
        st.log("merge", task=args.task, ok=False, detail=detail[-TAIL:], aborted=aborted)
        if aborted:
            _park(st, args.task, "merge-conflict")
            return 3
        # The run branch is left mid-merge: nothing more may run on it without a human.
        sys.stderr.write("git merge --abort failed: %s\n" % _git_err(ar))
        return _park_and_stop(st, args.task, "merge-conflict-unaborted")
    ok, r = _git_ok(st.root, "rev-list", "--parents", "-n", "1", "HEAD")
    line = r.stdout.split() if ok else []
    if len(line) != 3 or line[1] != before or line[2] != pinned:
        sys.stderr.write("task %s: HEAD is not a two-parent merge of %s into %s: %s\n"
                         % (args.task, pinned, before, " ".join(line) or _git_err(r)))
        st.log("merge", task=args.task, ok=False, reason="not a two-parent merge",
               head=line)
        # A commit may already sit on the run branch: never leave this retryable.
        return _park_and_stop(st, args.task, "merge-inconsistent")
    sha = line[0]
    st.set_status(args.task, "proven", merge_commit=sha)
    st.save()
    st.log("merge", task=args.task, ok=True, commit=sha, branch=t["branch"],
           verified_head=pinned)
    cleaned, r = _git_ok(st.root, "worktree", "remove", "--force", t["worktree"])
    if cleaned:
        cleaned, r = _git_ok(st.root, "branch", "-D", t["branch"])
    if not cleaned:
        sys.stderr.write("merged, but cleanup failed: %s\n" % _git_err(r))
    print("MERGE: %s %s" % (args.task, sha))
    return 0


def cmd_park(args):
    """Park a task with a reason. Its dependents become blocked; the run goes on."""
    st, t = _load_task(args, STATUSES)
    if st is None:
        return 2
    if t["status"] == "proven":
        sys.stderr.write("task %s is proven and merged; it cannot be parked\n" % args.task)
        return 2
    if not args.reason.strip():
        sys.stderr.write("--reason must not be empty\n")
        return 2
    _park(st, args.task, args.reason)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="conductor.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init")
    s.add_argument("--plan", required=True)
    s.add_argument("--root", default=".")
    s.add_argument("--grant-id", default=None)
    s.add_argument("--run-branch", default=None)
    s.add_argument("--base-branch", default="main")
    s.add_argument("--max-parallel", type=_positive_int, default=None)
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("next")
    s.add_argument("--root", default=".")
    s.add_argument("--max", type=_positive_int, default=None)
    s.set_defaults(fn=cmd_next)
    for name, fn in (("status", cmd_status), ("resume", cmd_resume)):
        s = sub.add_parser(name)
        s.add_argument("--root", default=".")
        s.set_defaults(fn=fn)
    for name, fn in (("start", cmd_start), ("verify", cmd_verify), ("review", cmd_review),
                     ("merge", cmd_merge), ("park", cmd_park)):
        s = sub.add_parser(name)
        s.add_argument("task")
        s.add_argument("--root", default=".")
        if name == "review":
            s.add_argument("--verdict", required=True, choices=("pass", "fail"))
            s.add_argument("--detail", default="")
        if name == "park":
            s.add_argument("--reason", required=True)
        s.set_defaults(fn=fn)
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
