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


def waves(tasks):
    """Group task ids into waves. Every id in a wave is independent of the others."""
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
    when = (now or _now()).astimezone(_dt.timezone.utc)
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

    @property
    def state_path(self):
        return os.path.join(self.dir, STATE_FILE)

    @property
    def log_path(self):
        return os.path.join(self.dir, LOG_FILE)

    @classmethod
    def new(cls, root, run_id, plan, plan_envelope, plan_sha256, grant_id,
            run_branch, base_branch, budget):
        """Create the run directory, write state.json and open the log."""
        plan_tasks = {t["id"]: t for t in plan.get("tasks") or []}
        waves({k: {"depends_on": t.get("depends_on") or []} for k, t in plan_tasks.items()})
        tasks = {}
        for tid, t in plan_tasks.items():
            tasks[tid] = {"status": "pending", "depends_on": list(t.get("depends_on") or []),
                          "verify": t.get("verify"), "repairs": 0, "branch": None,
                          "worktree": None, "verify_runs": [], "review": None,
                          "merge_commit": None, "park_reason": None}
        directory = run_dir(root, run_id)
        os.makedirs(directory, exist_ok=False)
        st = cls({"root": root, "run_id": run_id, "plan_envelope": plan_envelope,
                  "plan_sha256": plan_sha256, "grant_id": grant_id,
                  "run_branch": run_branch, "base_branch": base_branch,
                  "created_at": _rfc3339(_now()), "budget": budget, "dispatches": 0,
                  "stopped": None, "tasks": tasks}, directory)
        st.save()
        open(st.log_path, "a", encoding="utf-8").close()
        return st

    @classmethod
    def load(cls, path):
        """Rebuild a State from its state.json."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(data, os.path.dirname(os.path.abspath(path)))

    def to_dict(self):
        return {"root": self.root, "run_id": self.run_id,
                "plan_envelope": self.plan_envelope, "plan_sha256": self.plan_sha256,
                "grant_id": self.grant_id, "run_branch": self.run_branch,
                "base_branch": self.base_branch, "created_at": self.created_at,
                "budget": self.budget, "dispatches": self.dispatches,
                "stopped": self.stopped, "tasks": self.tasks}

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

    def log(self, event, **fields):
        """Append one event to autonomy-log.jsonl. The log is never rewritten."""
        if "at" in fields or "event" in fields:
            raise ValueError("log fields must not override 'at' or 'event'")
        record = {"at": _rfc3339(_now()), "event": event}
        record.update(fields)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
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
            for tid, t in self.tasks.items():
                if cur in (t.get("depends_on") or []) and tid not in out:
                    out.add(tid)
                    frontier.append(tid)
        return out

    def ready(self, max_parallel):
        """Pending tasks whose dependencies are all proven, capped so that
        running + verifying + reviewing + returned <= max_parallel. Plan order."""
        active = sum(1 for t in self.tasks.values() if t["status"] in ACTIVE)
        slots = max(0, max_parallel - active)
        out = []
        for tid, t in self.tasks.items():
            if len(out) >= slots:
                break
            if t["status"] == "pending" and all(
                    self.tasks[d]["status"] == "proven" for d in t.get("depends_on") or []):
                out.append(tid)
        return out

    def max_parallel(self):
        return int(self.budget.get("max_parallel") or DEFAULT_PARALLEL)


def _slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "plan"


def _load_current(root):
    d = current_run(root)
    if d is None:
        sys.stderr.write("no run found under %s\n" % os.path.join(root, RUNS_DIR))
        return None
    return State.load(os.path.join(d, STATE_FILE))


def cmd_init(args):
    """Build a run from a task-plan payload file and print RUN: <id>."""
    try:
        with open(args.plan, "rb") as f:
            raw = f.read()
        plan = json.loads(raw)
    except (OSError, ValueError) as e:
        sys.stderr.write("cannot read plan %s: %s\n" % (args.plan, e))
        return 2
    if not isinstance(plan, dict) or not isinstance(plan.get("tasks"), list):
        sys.stderr.write("plan %s has no tasks list\n" % args.plan)
        return 2
    budget = {"max_parallel": args.max_parallel or DEFAULT_PARALLEL}
    try:
        st = State.new(root=args.root, run_id=new_run_id(), plan=plan,
                       plan_envelope=args.plan,
                       plan_sha256=hashlib.sha256(raw).hexdigest(),
                       grant_id=args.grant_id,
                       run_branch=args.run_branch or "factory/%s" % _slug(plan.get("title")),
                       base_branch=args.base_branch, budget=budget)
    except (PlanError, KeyError, TypeError) as e:
        sys.stderr.write("invalid plan: %s\n" % e)
        return 2
    st.log("init", run=st.run_id, plan=st.plan_envelope, plan_sha256=st.plan_sha256,
           waves=waves({k: t for k, t in st.tasks.items()}))
    print("RUN: %s" % st.run_id)
    return 0


def cmd_next(args):
    """Print READY: <ids> for the tasks that can start now (nothing when none can)."""
    st = _load_current(args.root)
    if st is None:
        return 2
    ready = st.ready(args.max or st.max_parallel())
    if ready:
        print("READY: %s" % " ".join(ready))
    return 0


def cmd_status(args):
    """Print one STATUS: line per task."""
    st = _load_current(args.root)
    if st is None:
        return 2
    for tid, t in st.tasks.items():
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
        print("STATUS: run %s last_event=%s at=%s" % (st.run_id, last.get("event"), last.get("at")))
    else:
        print("STATUS: run %s last_event=none" % st.run_id)
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
    s.add_argument("--max-parallel", type=int, default=None)
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("next")
    s.add_argument("--root", default=".")
    s.add_argument("--max", type=int, default=None)
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
