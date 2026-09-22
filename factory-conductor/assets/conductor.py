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
import tempfile  # noqa: E402

RUN_ID_RE = r"^run-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}\Z"
STATUSES = ("pending", "running", "verifying", "reviewing", "proven", "parked", "blocked")
STOP_REASONS = ("budget_wall_clock", "budget_dispatches", "grant_ask",
                "new_human_decision", "verify_red_after_repairs", "no_ready_tasks")
DEFAULT_PARALLEL = 2

ACTIVE = ("running", "verifying", "reviewing")
TASK_FIELDS = ("status", "depends_on", "verify", "repairs", "branch", "worktree",
               "verify_runs", "review", "merge_commit", "park_reason")
RUNS_DIR = os.path.join(".skill-contract", "runs")
STATE_FILE = "state.json"
LOG_FILE = "autonomy-log.jsonl"


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
                          "merge_commit": None, "park_reason": None}
        root = os.path.abspath(root)
        directory = run_dir(root, run_id)
        os.makedirs(directory, exist_ok=False)
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
    return order, seen


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
                       plan_envelope=args.plan,
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
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
