#!/usr/bin/env python3
"""ops-intake: a pure local importer for the ops signal queue.

Intake MUST NOT open a network connection or run a subprocess. It only reads files the
agent hands it and writes its own queue. Stdlib only, Python 3.10+, POSIX.

Exits: 0 ok; 3 import or envelope problems, or a human is needed; 2 invalid config,
unknown format or refused input. Machine lines: IMPORT:, SYNC:, ITEM:, NEXT:, STOP:,
PROBLEM:.
"""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import math
import os
import re
import sys
import time

import adapters
import contract_check as CC  # the vendored skill-contract checker, same dir

SKILL_NAME = "ops-intake"
VERSION = "0.1.0"
_KIND_BASE = "https://github.com/dhanesh/agent-skills/skill-contract/"
INTAKE_ITEM_KIND = _KIND_BASE + "intake-item/v1"
PLAN_KIND = _KIND_BASE + "task-plan/v1"
RUN_KIND = _KIND_BASE + "run-result/v1"
RELEASE_KIND = _KIND_BASE + "release-result/v1"
CONSUMED_KINDS = (PLAN_KIND, RUN_KIND, RELEASE_KIND)
# The only skill that may produce each consumed kind; any other producer is a problem.
PRODUCERS = {PLAN_KIND: "spec-first-planning", RUN_KIND: "factory-conductor",
             RELEASE_KIND: "release-conductor"}
SHARED_ENVELOPES = ".skill-contract/envelopes"  # read by sync, never written by intake

INTAKE_DIR = ".skill-contract/intake"
CONFIG_PATH = ".intake/config.json"
QUEUE_FILE = "queue.json"
LOG_FILE = "intake-log.jsonl"

# The closed list of formats. adapters.ADAPTERS holds the ones that are built.
FORMATS = ("release-envelope", "release-status", "gh-issues-json", "gh-runs-json",
           "gh-run-jobs-json", "git-rev-list", "intake-signals-jsonl")
# git-rev-list is auxiliary: it belongs to no source.
SOURCE_FORMATS = tuple(f for f in FORMATS if f != "git-rev-list")
RELEASE_SOURCE = "release"
RELEASE_FORMATS = ("release-envelope", "release-status")

STATES = ("new", "picked", "planned", "resolved", "dismissed")


# -- Untrusted text ---------------------------------------------------------------
def one_line(text):
    """text with every run of whitespace collapsed to one space (conductor.py one_line)."""
    return " ".join(str(text if text is not None else "").split())


def code(text):
    """Untrusted text as one inline code span. Copied from release-conductor's
    assets/release.py `code()`, which copies factory-conductor's conductor.py `code()`:
    newlines collapsed, and a backtick fence longer than any backtick run inside, so it
    cannot open a heading, list or link, and GitHub does not turn @mentions or closing
    keywords in it into actions."""
    s = one_line(text)
    runs = [len(m) for m in re.findall(r"`+", s)]
    fence = "`" * (max(runs) + 1 if runs else 1)
    pad = " " if s.startswith("`") or s.endswith("`") or not s else ""
    return "%s%s%s%s%s" % (fence, pad, s, pad, fence)


# -- The repo lock ----------------------------------------------------------------
# Copied from release-conductor/assets/release.py's run_lock: same semantics (not
# re-entrant, released when the holder exits or dies, fd not inherited), scoped to the
# whole repo, raising this module's own Locked so the skill stays self-contained.
LOCK_FILE = ".lock"
LOCK_TIMEOUT = 900  # read at call time, so tests can shorten it
LOCK_NOTICE_AFTER = 2.0
LOCK_NOTICE = "waiting for the intake lock held by another intake command…\n"


class Locked(Exception):
    """Raised by run_lock() when the lock is still held after `timeout` seconds."""


def _intake_dir(root):
    return os.path.join(root, *INTAKE_DIR.split("/"))


def _ensure_dir(root):
    """Create the intake dir with a `*` .gitignore inside, so queue state (which can
    carry customer text) never dirties the repo. Same pattern as release-conductor."""
    d = _intake_dir(root)
    os.makedirs(d, exist_ok=True)
    gi = os.path.join(d, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8") as f:
            f.write("# written by ops-intake: queue state is local, never committed\n*\n")
    return d


@contextlib.contextmanager
def run_lock(root, timeout=None):
    """Hold an exclusive flock on <root>/.skill-contract/intake/.lock. Raises Locked
    after `timeout` seconds (LOCK_TIMEOUT by default, read at call time)."""
    d = _ensure_dir(root)
    f = open(os.path.join(d, LOCK_FILE), "a+")
    try:
        start = time.monotonic()
        deadline = start + (LOCK_TIMEOUT if timeout is None else timeout)
        told = False
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                now = time.monotonic()
                if now >= deadline:
                    raise Locked(root)
                if not told and now - start >= LOCK_NOTICE_AFTER:
                    sys.stderr.write(LOCK_NOTICE)
                    sys.stderr.flush()
                    told = True
                time.sleep(0.1)
        yield
    finally:
        f.close()


# -- Config -----------------------------------------------------------------------
_REPO_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")
_TOP_KEYS = {"sources", "github", "ci", "jira", "linear", "weights"}


def _is_str(v, limit=200):
    """A short string that is safe on a command line: no leading dash (it would read as
    a flag) and no control characters."""
    return (isinstance(v, str) and 0 < len(v) <= limit and not v.startswith("-")
            and not any(ord(c) < 32 or ord(c) == 127 for c in v))


def _str_list(v):
    return isinstance(v, list) and all(_is_str(x, 100) for x in v)


def _check_section(name, sec, problems):
    """Each section has an allowlist of keys. Any other key is a problem, which is what
    refuses `command`, `base_url` and `*_env`."""
    if not isinstance(sec, dict):
        problems.append("%s must be an object" % name)
        return
    allowed = {"github": {"repo", "labels"}, "ci": {"default_branch", "workflows"},
               "jira": {"jql"}, "linear": {"team", "filter"}}[name]
    for k in sec:
        if k not in allowed:
            problems.append("%s: unknown key %s" % (name, code(k)))
    if "repo" in sec and not (isinstance(sec["repo"], str) and _REPO_RE.match(sec["repo"])
                                   and ".." not in sec["repo"]):
        problems.append("github.repo must look like owner/name")
    for k in ("default_branch", "jql", "team", "filter"):
        if k in sec and not _is_str(sec[k]):
            problems.append("%s.%s must be a short string" % (name, k))
    for k in ("labels", "workflows"):
        if k in sec and not _str_list(sec[k]):
            problems.append("%s.%s must be a list of short strings" % (name, k))


def config_problems(cfg):
    problems = []
    if not isinstance(cfg, dict):
        return ["config must be a JSON object"]
    for k in cfg:
        if k not in _TOP_KEYS:
            problems.append("unknown key %s" % code(k))
    sources = cfg.get("sources")
    if not isinstance(sources, dict) or not sources:
        problems.append("sources must be a non-empty object")
        sources = {}
    for name, src in sources.items():
        if not _is_str(name, 50):
            problems.append("source name %s is not a short string" % code(name))
            continue
        if not isinstance(src, dict):
            problems.append("source %s must be an object" % code(name))
            continue
        for k in src:
            if k not in ("enabled", "formats"):
                problems.append("source %s: unknown key %s" % (code(name), code(k)))
        if not isinstance(src.get("enabled"), bool):
            problems.append("source %s: enabled must be true or false" % code(name))
        fmts = src.get("formats")
        if not isinstance(fmts, list) or not fmts:
            problems.append("source %s: formats must be a non-empty list" % code(name))
            continue
        for fmt in fmts:
            if fmt == "git-rev-list":
                problems.append("source %s: git-rev-list is auxiliary and belongs to no source"
                                % code(name))
            elif fmt not in SOURCE_FORMATS:
                problems.append("source %s: unknown format %s" % (code(name), code(fmt)))
            elif (fmt in RELEASE_FORMATS) != (name == RELEASE_SOURCE):
                problems.append("source %s: %s is reserved for the release formats, and "
                                "those formats are allowed only there"
                                % (code(name), code(RELEASE_SOURCE)))
    ci_formats = any(isinstance(s, dict) and isinstance(s.get("formats"), list)
                     and ("gh-runs-json" in s["formats"] or "gh-run-jobs-json" in s["formats"])
                     for s in sources.values())
    if ci_formats and not (isinstance(cfg.get("ci"), dict) and "default_branch" in cfg["ci"]):
        problems.append("ci.default_branch is required when a source lists a CI format")
    for sec in ("github", "ci", "jira", "linear"):
        if sec in cfg:
            _check_section(sec, cfg[sec], problems)
    w = cfg.get("weights", {})
    if not isinstance(w, dict) or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            for v in w.values()):
        problems.append("weights must map names to finite numbers")
    elif any(k not in sources for k in w):
        problems.append("weights must be keyed by configured source names")
    return problems


def load_config(root):
    """(config, problems). config is None when the file is missing or invalid."""
    path = os.path.join(root, *CONFIG_PATH.split("/"))
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return None, ["no config at %s" % CONFIG_PATH]
    except (OSError, ValueError) as e:
        return None, ["cannot read %s: %s" % (CONFIG_PATH, one_line(e))]
    problems = config_problems(cfg)
    return (None if problems else cfg), problems


# -- Items and states -------------------------------------------------------------
def item_id(source, source_id):
    return "I" + hashlib.sha256((source + "\0" + source_id).encode("utf-8")).hexdigest()[:10]


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


_MOVES = {("new", "picked"), ("new", "dismissed"), ("picked", "planned"),
          ("planned", "resolved")}


def transition(item, to, by=None, reason=None, now=None):
    """Move an item to state `to`, or raise ValueError. Rules (spec 1.5): new->picked,
    new->dismissed (needs a reason), picked->planned, planned->resolved, any->resolved by
    hand, dismissed/resolved->new with the regressed flag on recurrence."""
    if to not in STATES:
        raise ValueError("unknown state %s" % to)
    frm = item.get("state")
    now = now or _now()
    if to == "new":
        if frm not in ("dismissed", "resolved"):
            raise ValueError("only a dismissed or resolved item can recur, not %s" % frm)
        item["regressed"] = True
        item["recurred_at"] = now
        for k in ("closed_at", "plan", "wait", "checked", "checked_for"):  # an old plan never closes a new occurrence
            item.pop(k, None)
        item.pop("state_by", None)
        item.pop("state_reason", None)
        item["state"] = "new"
        item["state_at"] = now
        return item
    elif to == "resolved":
        if frm == "resolved":
            raise ValueError("already resolved")
        if frm != "planned" and not by:
            raise ValueError("resolving by hand needs a name")
    elif (frm, to) not in _MOVES:
        raise ValueError("cannot move %s to %s" % (frm, to))
    if to == "dismissed" and not (reason and reason.strip()):
        raise ValueError("dismissing needs a reason")
    item["state"] = to
    item["state_at"] = now
    item["regressed"] = False  # regressed marks a new item only
    if to in ("dismissed", "resolved"):
        item["closed_at"] = now  # recurrence compares against this
    if by:
        item["state_by"] = by
    if reason:
        item["state_reason"] = reason
    return item


# -- The queue --------------------------------------------------------------------
class QueueError(Exception):
    """The queue file is unreadable or not an intake queue."""


_RUN_RE = re.compile(r"^[0-9]{1,20}\Z")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}\Z")
MAX_HISTORY_SHAS = 20000  # per release commit: ~0.9 MB of queue; git lists newest first
MAX_HISTORIES = 5         # unneeded histories kept; the oldest unneeded import goes first


def _valid_history(commit, h):
    return (isinstance(commit, str) and _SHA40_RE.match(commit) and isinstance(h, dict)
            and isinstance(h.get("imported_at"), str) and isinstance(h.get("shas"), list)
            and all(isinstance(x, str) and _SHA40_RE.match(x) for x in h["shas"]))


_RUN_STRS = ("workflowName", "headBranch", "headSha", "url", "createdAt", "updatedAt")


def _valid_run(r):
    return (isinstance(r, dict) and all(isinstance(r.get(k), str) for k in _RUN_STRS)
            and isinstance(r.get("attempt"), int) and not isinstance(r.get("attempt"), bool)
            and isinstance(r.get("jobs_imported"), bool)
            and isinstance(r.get("source", ""), str))


class Queue:
    def __init__(self, root, items=None, runs=None, histories=None):
        self.root = root
        self.items = items if items is not None else {}
        self.runs = runs if runs is not None else {}  # failing CI runs by run id
        # imported `git rev-list <commit>` output by release commit, for loop closing
        self.histories = histories if histories is not None else {}
        self.needed = []       # release commits the last sync asked a history for
        self.unavailable = {}  # release commits whose history came back empty: {at, told}

    @classmethod
    def load(cls, root):
        path = os.path.join(_intake_dir(root), QUEUE_FILE)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return cls(root)
        except (OSError, ValueError):
            raise QueueError(path)
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, dict):
            raise QueueError(path)
        for iid, it in items.items():
            if not (isinstance(it, dict) and it.get("state") in STATES
                    and isinstance(it.get("title"), str) and isinstance(it.get("source"), str)
                    and isinstance(it.get("source_id"), str)
                    and isinstance(it.get("first_seen", ""), str)):
                raise QueueError(path)
            it.setdefault("id", iid)
            it.setdefault("first_seen", "")
            it.setdefault("regressed", False)
        runs = data.get("runs", {})
        if not isinstance(runs, dict) or not all(
                _RUN_RE.match(rid) and _valid_run(r) for rid, r in runs.items()):
            raise QueueError(path)
        histories = data.get("histories", {})
        if not isinstance(histories, dict) or not all(
                _valid_history(c, h) for c, h in histories.items()):
            raise QueueError(path)
        needed, unavailable = data.get("needed", []), data.get("unavailable", {})
        if not (isinstance(needed, list) and all(isinstance(c, str) and _SHA40_RE.match(c)
                                                 for c in needed)):
            raise QueueError(path)
        if not (isinstance(unavailable, dict) and all(
                isinstance(c, str) and _SHA40_RE.match(c) and isinstance(u, dict)
                and isinstance(u.get("at"), str) and isinstance(u.get("told"), bool)
                for c, u in unavailable.items())):
            raise QueueError(path)
        q = cls(root, items, runs, histories)
        q.needed, q.unavailable = needed, unavailable
        return q

    def add(self, source, source_id, title, evidence=None, now=None):
        """Add a new item, or return the existing one with the same id."""
        iid = item_id(source, source_id)
        if iid not in self.items:
            self.items[iid] = {"id": iid, "source": source, "source_id": source_id,
                               "title": title, "state": "new", "regressed": False,
                               "evidence": evidence or [], "first_seen": now or _now()}
        return self.items[iid]

    def save(self):
        """Write the queue by temp file, atomic replace and a directory fsync."""
        d = _ensure_dir(self.root)
        path = os.path.join(d, QUEUE_FILE)
        tmp = path + ".tmp.%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "items": self.items, "runs": self.runs,
                       "histories": self.histories, "needed": self.needed,
                       "unavailable": self.unavailable}, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def log(self, event, **fields):
        """Append one JSON line to the intake log."""
        d = _ensure_dir(self.root)
        rec = dict(fields, event=event, at=_now())
        with open(os.path.join(d, LOG_FILE), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())


# -- CLI --------------------------------------------------------------------------
def _flag(item):
    """The state, plus `regressed`, plus what a planned item waits on (set by sync)."""
    flag = item["state"] + ("+regressed" if item.get("regressed") else "")
    if item["state"] in ("picked", "planned") and item.get("wait") in WAITS:
        flag += "+" + item["wait"]
    return flag


# What sync says an item waits on: needs-plan (picked; the plan naming it predates the
# pick), needs-history (import a git-rev-list), needs-resolve (a human closes it: squash
# merge, partial run, release not in the clone), plan-superseded (a revision of its plan
# dropped it).
WAITS = ("needs-plan", "needs-history", "needs-resolve", "plan-superseded")


def _rank(item):
    """The rank sync stored; 0 before the first sync."""
    r = item.get("rank", 0)
    return r if isinstance(r, (int, float)) and not isinstance(r, bool) else 0


def compute_rank(item, weights, now):
    """(severity * 100 + recency_points + min(count, 20)) * weights[source] (spec 1.4).
    recency_points = max(0, 30 - whole days since last_seen); a future last_seen counts as
    today, a missing one as no recency. Severity defaults to 2 (unknown), count to 1."""
    sev = item.get("severity")
    sev = sev if isinstance(sev, int) and not isinstance(sev, bool) and 1 <= sev <= 4 else 2
    count = item.get("count")
    count = count if isinstance(count, int) and not isinstance(count, bool) and count > 0 else 1
    recency = 0
    try:
        days = (_parse_utc(now) - _parse_utc(item.get("last_seen"))).days
        recency = max(0, 30 - max(0, days))
    except (TypeError, ValueError):
        pass
    r = round((sev * 100 + recency + min(count, 20)) * float(weights.get(item["source"], 1.0)), 2)
    return int(r) if r == int(r) else r


def _parse_utc(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def _load(root):
    """The queue, or None after printing STOP (exit 2 for the caller)."""
    try:
        return Queue.load(root)
    except QueueError as e:
        print("STOP: queue-unreadable %s" % code(e))
        return None


def _find(q, iid):
    if iid not in q.items:
        print("STOP: no item %s" % code(iid))
        return None
    return q.items[iid]


def _need_config(root):
    cfg, problems = load_config(root)
    if problems:
        for p in problems:
            print("STOP: config: %s" % p)
        return None
    return cfg


def cmd_init(a):
    try:
        with open(a.answers, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        print("STOP: cannot read answers: %s" % code(e))
        return 2
    problems = config_problems(cfg)
    if problems:
        for p in problems:
            print("STOP: config: %s" % p)
        return 2
    path = os.path.join(a.root, *CONFIG_PATH.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    try:
        with run_lock(a.root):
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
    except Locked:
        print("STOP: the intake lock is held by another command")
        return 3
    print("NEXT: config written to %s" % CONFIG_PATH)
    return 0


def cmd_list(a):
    if _need_config(a.root) is None:
        return 2
    q = _load(a.root)
    if q is None:
        return 2
    shown = [i for i in q.items.values() if a.all or i["state"] == "new"]
    shown.sort(key=lambda i: (-_rank(i), i["id"]))  # ties break on item id
    for i in shown:
        print("ITEM: %s %s %s %s" % (i["id"], _flag(i), _rank(i), code(i["title"])))
    return 0


def cmd_show(a):
    if _need_config(a.root) is None:
        return 2
    q = _load(a.root)
    i = _find(q, a.id) if q else None
    if i is None:
        return 2
    print("ITEM: %s %s %s %s" % (i["id"], _flag(i), _rank(i), code(i["title"])))
    print("source: %s %s" % (code(i["source"]), code(i["source_id"])))
    for ev in i.get("evidence", []):
        print("evidence: %s" % code(ev if isinstance(ev, str) else json.dumps(ev, sort_keys=True)))
    return 0


def _change(a, to, need_reason=False):
    if _need_config(a.root) is None:
        return 2
    try:
        with run_lock(a.root):
            q = _load(a.root)
            i = _find(q, a.id) if q else None
            if i is None:
                return 2
            try:
                transition(i, to, by=a.by, reason=getattr(a, "reason", None))
            except ValueError as e:
                print("STOP: %s" % one_line(e))
                return 2
            q.log(a.cmd, id=a.id, by=a.by, reason=getattr(a, "reason", None))
            q.save()
    except Locked:
        print("STOP: the intake lock is held by another command")
        return 3
    print("ITEM: %s %s %s %s" % (i["id"], _flag(i), _rank(i), code(i["title"])))
    return 0


def cmd_link(a):
    if _need_config(a.root) is None:
        return 2
    try:
        with run_lock(a.root):
            q = _load(a.root)
            if q is None:
                return 2
            if a.id == a.other:
                print("STOP: link needs two different items")
                return 2
            keep, drop = _find(q, a.id), _find(q, a.other)
            if keep is None or drop is None:
                return 2
            keep.setdefault("evidence", []).extend(drop.get("evidence", []))
            keep.setdefault("linked", []).append(
                {"id": drop["id"], "source": drop["source"], "source_id": drop["source_id"]})
            del q.items[a.other]
            q.log("link", id=a.id, other=a.other)
            q.save()
    except Locked:
        print("STOP: the intake lock is held by another command")
        return 3
    print("NEXT: merged %s into %s" % (a.other, a.id))
    return 0


def cmd_status(a):
    if _need_config(a.root) is None:
        return 2
    q = _load(a.root)
    if q is None:
        return 2
    counts = {s: 0 for s in STATES}
    for i in q.items.values():
        counts[i["state"]] += 1
    reg = sum(1 for i in q.items.values() if i["state"] == "new" and i.get("regressed"))
    print("SYNC: %d items %s regressed=%d" % (
        len(q.items), " ".join("%s=%d" % kv for kv in counts.items()), reg))
    return 0


# -- import and formats -----------------------------------------------------------
EVIDENCE_CAP = 20      # evidence kept per item; the oldest is dropped first
PROBLEM_LINES = 50     # PROBLEM: lines printed; the IMPORT: line counts all of them


def _bump(item, sig):
    """Merge a signal into a known item: new evidence keys raise count, last_seen moves
    forward only, the evidence list keeps the newest EVIDENCE_CAP."""
    have = {e.get("key") for e in item.get("evidence", []) if isinstance(e, dict)}
    for ev in sig["evidence"]:
        if ev["key"] not in have:
            have.add(ev["key"])
            item.setdefault("evidence", []).append(ev)
            item["count"] = item.get("count", 0) + 1
    del item["evidence"][:-EVIDENCE_CAP]
    if sig["last_seen"] > item.get("last_seen", ""):
        item["last_seen"] = sig["last_seen"]
    if not item.get("first_seen") or sig["first_seen"] < item["first_seen"]:
        item["first_seen"] = sig["first_seen"]  # earliest seen wins


def apply_signals(queue, signals, now):
    """Merge signals into the queue (spec 1.3, 1.5). A dismissed or resolved item recurs
    when a signal's last_seen is later than its closed_at. Closing an issue upstream and
    a signal that cannot say when (release-status) never count as recurrence."""
    for sig in signals:
        iid = item_id(sig["source"], sig["source_id"])
        it = queue.items.get(iid)
        if it is None:
            it = queue.add(sig["source"], sig["source_id"], sig["title"], now=sig["first_seen"])
            it.update(url=sig["url"], kind=sig["kind"], severity=sig["severity"],
                      trust=sig["trust"], last_seen=sig["last_seen"],
                      count=max(1, len({e["key"] for e in sig["evidence"]})))
            if sig.get("closed"):
                it["closed"] = True
            seen = set()
            for ev in sig["evidence"]:
                if ev["key"] not in seen:
                    seen.add(ev["key"])
                    it["evidence"].append(ev)
            del it["evidence"][:-EVIDENCE_CAP]
            continue
        recurs = (it["state"] in ("dismissed", "resolved") and sig.get("recurrence", True)
                  and not sig.get("closed") and it.get("closed_at")
                  and sig["last_seen"] > it["closed_at"])
        _bump(it, sig)
        it["severity"] = max(it.get("severity", 0), sig["severity"])  # only rises on merge, by design
        if sig.get("closed"):
            it["closed"] = True
        elif "closed" in it:
            it["closed"] = False
        if sig["trust"] == "low":
            it["trust"] = "low"
        if recurs:
            transition(it, "new", now=now)


def _read_input(path):
    try:
        if path == "-":
            return sys.stdin.read()
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, ValueError) as e:
        print("STOP: cannot read input: %s" % code(e))
        return None


def cmd_import(a):
    fmt = a.format
    if fmt not in FORMATS:
        print("STOP: unknown-format %s" % code(fmt))
        return 2
    cfg = _need_config(a.root)
    if cfg is None:
        return 2
    if fmt == "release-envelope":
        print("STOP: release-envelope is read by sync from %s; run intake sync"
              % SHARED_ENVELOPES)
        return 2
    if fmt == "git-rev-list":
        if a.source:
            print("STOP: git-rev-list belongs to no source; do not pass --source")
            return 2
        if not (a.commit and _SHA40_RE.match(a.commit)):
            print("STOP: git-rev-list needs --commit <the full 40-hex release commit>")
            return 2
        return _import_history(a)
    else:
        src = cfg["sources"].get(a.source) if a.source else None
        if src is None:
            print("STOP: source-not-configured %s" % code(a.source))
            return 2
        if not src["enabled"]:
            print("STOP: source-disabled %s" % code(a.source))
            return 2
        if fmt not in src["formats"]:
            print("STOP: format-not-listed %s for source %s" % (code(fmt), code(a.source)))
            return 2
        if a.run is not None and not _RUN_RE.match(a.run):
            print("STOP: --run must be a run id (digits)")
            return 2
    if fmt not in adapters.ADAPTERS:
        print("STOP: format-not-built %s" % fmt)
        return 2
    if fmt == "gh-run-jobs-json" and a.run is None:
        print("STOP: gh-run-jobs-json needs --run <run id>")
        return 2
    raw = _read_input(a.file)
    if raw is None:
        return 2
    now = _now()
    ctx = {"source": a.source, "now": now, "run": a.run, "runs_out": {}}
    if fmt in ("gh-runs-json", "gh-run-jobs-json"):
        ci = cfg["ci"]  # config validation requires ci.default_branch for these formats
        ctx["default_branch"] = ci["default_branch"]
        ctx["workflows"] = ci.get("workflows", [])
        q0 = _load(a.root)
        if q0 is None:
            return 2
        ctx["runs"] = q0.runs
        if fmt == "gh-run-jobs-json" and a.run not in q0.runs:
            print("STOP: run-not-imported %s" % a.run)
            return 2
    signals, problems = adapters.ADAPTERS[fmt](raw, ctx)
    n_signals = len(signals)
    if len(signals) > adapters.MAX_SIGNALS:
        problems.append("%d records over the %d per import were not imported"
                        % (len(signals) - adapters.MAX_SIGNALS, adapters.MAX_SIGNALS))
        signals = signals[:adapters.MAX_SIGNALS]
    try:
        with run_lock(a.root):
            q = _load(a.root)
            if q is None:
                return 2
            if fmt == "gh-run-jobs-json":
                if a.run not in q.runs:
                    print("STOP: run-not-imported %s" % a.run)
                    return 2
                # Only a well-formed, complete payload retires the run's NEXT line.
                if ctx.get("jobs_ok") and len(signals) == n_signals:
                    q.runs[a.run]["jobs_imported"] = True
            for run in ctx["runs_out"].values():
                run["source"] = a.source  # the jobs NEXT line names this source (R18)
            q.runs.update(ctx["runs_out"])
            apply_signals(q, signals, now)
            q.log("import", source=a.source, format=fmt, ok=len(signals), problems=len(problems))
            q.save()
    except Locked:
        print("STOP: the intake lock is held by another command")
        return 3
    print("IMPORT: %s %s ok %d problems %d" % (a.source, fmt, len(signals), len(problems)))
    for p in problems[:PROBLEM_LINES]:
        print("PROBLEM: %s" % one_line(p))
    if len(problems) > PROBLEM_LINES:
        print("PROBLEM: and %d more" % (len(problems) - PROBLEM_LINES))
    return 3 if problems else 0


def _import_history(a):
    """git-rev-list: store `git rev-list <commit>` output under the release commit.
    The history must start at --commit (git lists it first). It keeps the newest
    MAX_HISTORY_SHAS shas and the MAX_HISTORIES most recent imports; anything cut is a
    problem, and a cut can only leave an item planned, never resolve it wrongly."""
    raw = _read_input(a.file)
    if raw is None:
        return 2
    now = _now()
    ctx = {"source": None, "now": now}
    _, problems = adapters.ADAPTERS["git-rev-list"](raw, ctx)
    shas = ctx["history_out"]
    unavailable = not shas and not problems  # git rev-list printed nothing: not in this clone
    if unavailable:
        problems.append("no commits: the release commit is not in this clone; git fetch, "
                        "then import again")
    elif not shas or shas[0] != a.commit:
        problems.append("the history does not start at --commit; nothing was stored")
        shas = []
    elif len(shas) > MAX_HISTORY_SHAS:
        problems.append("%d commits over the %d kept were not stored"
                        % (len(shas) - MAX_HISTORY_SHAS, MAX_HISTORY_SHAS))
        shas = shas[:MAX_HISTORY_SHAS]
    if shas or unavailable:
        try:
            with run_lock(a.root):
                q = _load(a.root)
                if q is None:
                    return 2
                if unavailable:
                    q.unavailable[a.commit] = {"at": now, "told": False}
                else:
                    q.unavailable.pop(a.commit, None)
                    q.histories[a.commit] = {"imported_at": now, "shas": shas}
                # Over the cap, drop the oldest history no planned item still needs.
                spare = sorted((h["imported_at"], c) for c, h in q.histories.items()
                               if c not in q.needed and c != a.commit)
                while len(q.histories) > MAX_HISTORIES and spare:
                    del q.histories[spare.pop(0)[1]]
                q.log("import", format="git-rev-list", commit=a.commit, ok=len(shas),
                      problems=len(problems))
                q.save()
        except Locked:
            print("STOP: the intake lock is held by another command")
            return 3
    print("IMPORT: - git-rev-list ok %d problems %d" % (len(shas), len(problems)))
    for p in problems[:PROBLEM_LINES]:
        print("PROBLEM: %s" % one_line(p))
    if len(problems) > PROBLEM_LINES:
        print("PROBLEM: and %d more" % (len(problems) - PROBLEM_LINES))
    return 3 if problems else 0


# -- sync -------------------------------------------------------------------------
def _read_envelopes(root):
    """The valid task-plan, run-result and release-result envelopes under
    .skill-contract/envelopes/, oldest first, and the problems. Each one is a plain file
    read, checked with CC.check_statement and a payload shape check; an invalid one is
    a problem and is skipped. Other kinds are ignored. Problems name the file and the
    failed rule, never the file's text."""
    d = os.path.join(root, *SHARED_ENVELOPES.split("/"))
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".json"))
    except FileNotFoundError:
        return [], []
    except OSError:
        return [], ["cannot list %s" % SHARED_ENVELOPES]
    out, problems = [], []
    for name in names:
        path = os.path.join(d, name)
        where = "envelope %s" % code(name)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                data = f.read(CC.ENVELOPE_MAX_BYTES + 1)
            if len(data) > CC.ENVELOPE_MAX_BYTES:
                raise ValueError("too large")
            raw = data.decode("utf-8")
            st = json.loads(raw)
        except (OSError, ValueError, RecursionError):
            problems.append("%s: unreadable" % where)
            continue
        kind = st.get("predicateType") if isinstance(st, dict) else None
        if not isinstance(kind, str):
            problems.append("%s: not an envelope" % where)
            continue
        if kind not in CONSUMED_KINDS:
            continue
        viol = CC.check_statement(st)
        if viol:
            problems.append("%s: fails skill-contract %s" % (
                where, ", ".join(sorted({"C%d" % n for n, _ in viol}))))
            continue
        pred = st["predicate"]
        if pred["wasAttributedTo"]["skill"] != PRODUCERS[kind]:
            problems.append("%s: not produced by %s" % (where, PRODUCERS[kind]))
            continue
        try:
            rec = _envelope_record(kind, pred["payload"])
        except ValueError as e:
            problems.append("%s: %s" % (where, e))
            continue
        rec.update(kind=kind, raw=raw, id=pred["id"], at=pred["generatedAtTime"],
                   rev_of=pred.get("wasRevisionOf"),
                   rel="%s/%s" % (SHARED_ENVELOPES, name),
                   sha256=hashlib.sha256(data).hexdigest(),
                   pins={s["digest"]["sha256"] for s in st["subject"]})
        out.append(rec)
    out.sort(key=lambda r: (r["at"], r["id"]))
    return out, problems


_MERGE_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def _envelope_record(kind, p):
    """The fields sync uses from a payload, or ValueError with a message of our own."""
    if kind == RELEASE_KIND:
        version, commit, outcome = adapters.release_result(p)
        return {"version": version, "commit": commit, "outcome": outcome}
    if kind == PLAN_KIND:
        items = p.get("intake_items", [])
        if not (isinstance(items, list) and all(isinstance(x, str) for x in items)):
            raise ValueError("task-plan intake_items must be a list of item ids")
        return {"intake_items": items}
    plan, tasks = p.get("plan"), p.get("tasks")
    if not (isinstance(plan, dict) and isinstance(plan.get("id"), str)):
        raise ValueError("run-result plan.id must be a string")
    if not (isinstance(tasks, list) and all(
            isinstance(t, dict) and isinstance(t.get("status"), str)
            for t in tasks)):
        raise ValueError("run-result tasks must carry a status")
    if not all(t.get("merge_commit") is None or (isinstance(t["merge_commit"], str)
               and _MERGE_RE.match(t["merge_commit"])) for t in tasks):
        raise ValueError("run-result merge_commit must be null or a 40- or 64-hex sha")
    return {"plan_id": plan["id"], "tasks": tasks}


def _plan_ref(e):
    return {"id": e["id"], "path": e["rel"], "sha256": e["sha256"], "at": e["at"]}


def _revisions(plans, pid):
    """The task-plans that revise plan `pid`, directly or through a wasRevisionOf chain."""
    out, todo, seen = [], [pid], {pid}
    while todo:
        cur = todo.pop()
        for p in plans:
            if p["rev_of"] == cur and p["id"] not in seen:
                seen.add(p["id"])
                out.append(p)
                todo.append(p["id"])
    return out


def _close_loop(q, envs, now):
    """Spec 1.6 with rulings R13-R15. Returns (release commits to import a history for,
    release commits not in the clone whose fetch line is due).

    - picked -> planned by the newest task-plan made at or after the pick that names the
      item; a naming plan older than the pick flags needs-plan.
    - A planned item follows the newest naming plan; a revision of its plan that drops it
      flags plan-superseded.
    - The latest run-result pinning the plan (digest and id) decides. Every task must be
      proven with a merge commit, else needs-resolve (a partial or stopped run).
    - Each verified release made at or after that run is checked once per item: its
      history holds every merge (resolved, by release:<version>), or it lacks one (kept
      in `checked`, never asked for again), or its commit is not in the clone or is not
      40-hex. When every such release is settled and none resolves: needs-resolve."""
    plans = [e for e in envs if e["kind"] == PLAN_KIND]
    runs = [e for e in envs if e["kind"] == RUN_KIND]
    verified = [e for e in envs if e["kind"] == RELEASE_KIND and e["outcome"] == "verified"]
    wanted, fetch = set(), set()
    for iid in sorted(q.items):
        it = q.items[iid]
        if it["state"] not in ("picked", "planned"):
            continue
        it.pop("wait", None)
        naming = [p for p in plans if iid in p["intake_items"]]
        if it["state"] == "picked":
            fresh = [p for p in naming if p["at"] >= it.get("state_at", "")]
            if not fresh:
                if naming:
                    it["wait"] = "needs-plan"
                continue
            transition(it, "planned", now=now)
            it["plan"] = _plan_ref(fresh[-1])
            q.log("planned", id=iid, plan=fresh[-1]["id"])
        plan = it.get("plan")
        if not isinstance(plan, dict):
            continue
        newer = [p for p in naming if (p["at"], p["id"]) > (plan.get("at", ""), plan.get("id", ""))
                 and p["id"] != plan.get("id")]
        if newer:
            plan = it["plan"] = _plan_ref(newer[-1])
            it.pop("checked", None)
            q.log("replanned", id=iid, plan=plan["id"])
        if any(iid not in p["intake_items"] for p in _revisions(plans, plan.get("id"))):
            it["wait"] = "plan-superseded"
            continue
        mine = [r for r in runs if plan.get("sha256") in r["pins"] and r["plan_id"] == plan.get("id")]
        if not mine:
            continue
        run = mine[-1]  # envs are sorted oldest first: the latest run decides
        tasks = run["tasks"]
        if not tasks or any(t["status"] != "proven" or not t.get("merge_commit") for t in tasks):
            it["wait"] = "needs-resolve"  # a partial or stopped run: a human closes it
            continue
        merges = sorted({t["merge_commit"] for t in tasks})
        if it.get("checked_for") != merges or not isinstance(it.get("checked"), list):
            it["checked_for"], it["checked"] = merges, []
        checked = it["checked"]
        no_history, settled = [], False
        for rel in (v for v in verified if v["at"] >= run["at"]):
            c = rel["commit"]
            h = q.histories.get(c)
            if c in checked or not _SHA40_RE.match(c):
                settled = True  # lacked a merge before, or a sha256 repo git-rev-list can't take
            elif h is not None:
                if set(merges) <= set(h["shas"]):
                    transition(it, "resolved", by="release:%s" % rel["version"], now=now)
                    q.log("resolved", id=iid, by="release:%s" % rel["version"])
                    break
                checked.append(c)  # a squash or rebase merge: never ask for it again
                settled = True
            elif c in q.unavailable:
                settled = True
                fetch.add(c)
            else:
                no_history.append(c)
        else:
            if no_history:
                it["wait"] = "needs-history"
                wanted.update(no_history)
            elif settled:
                it["wait"] = "needs-resolve"
    # Every history in hand was just used: an item resolved on it or recorded it as
    # checked. Keep none that no planned item still needs.
    for c in [c for c in q.histories if c not in wanted]:
        del q.histories[c]
    q.needed = sorted(wanted)
    for c in [c for c in q.unavailable if c not in fetch]:
        del q.unavailable[c]
    due = {c for c in fetch if not q.unavailable[c]["told"]}
    for c in due:
        q.unavailable[c]["told"] = True
    return wanted, due


def _jobs_source(cfg, run):
    """The configured source the jobs import of `run` goes to (R18): the source that
    imported the run when it lists gh-run-jobs-json, else the first source (by name) that
    lists it. Config validation keeps every source name safe on a command line."""
    listing = sorted(n for n, s in cfg["sources"].items() if "gh-run-jobs-json" in s["formats"])
    own = run.get("source")
    if own in listing or not listing:
        return own or "ci"
    return listing[0]


def cmd_sync(a):
    """Read the shared envelopes, add rolled-back releases, close the loop, rank the
    queue (spec 1.4-1.6), and print the NEXT lines for missing CI jobs and histories.
    Exits 3 when an envelope was a problem, else 0."""
    cfg = _need_config(a.root)
    if cfg is None:
        return 2
    try:
        with run_lock(a.root):
            q = _load(a.root)
            if q is None:
                return 2
            now = _now()
            envs, problems = _read_envelopes(a.root)
            rel_src = cfg["sources"].get(RELEASE_SOURCE)
            if rel_src and rel_src["enabled"] and "release-envelope" in rel_src["formats"]:
                ctx = {"source": RELEASE_SOURCE, "now": now}
                for e in envs:
                    if e["kind"] == RELEASE_KIND:
                        sigs, probs = adapters.ADAPTERS["release-envelope"](e["raw"], ctx)
                        apply_signals(q, sigs, now)
                        problems.extend(probs)
            wanted, fetch = _close_loop(q, envs, now)
            weights = cfg.get("weights", {})
            for it in q.items.values():
                it["rank"] = compute_rank(it, weights, now)
            q.log("sync", envelopes=len(envs), problems=len(problems))
            q.save()
    except Locked:
        print("STOP: the intake lock is held by another command")
        return 3
    counts = {s: sum(1 for i in q.items.values() if i["state"] == s) for s in STATES}
    print("SYNC: %d items %s envelopes %d problems %d" % (
        len(q.items), " ".join("%s=%d" % kv for kv in counts.items()), len(envs), len(problems)))
    for p in problems[:PROBLEM_LINES]:
        print("PROBLEM: %s" % one_line(p))
    if len(problems) > PROBLEM_LINES:
        print("PROBLEM: and %d more" % (len(problems) - PROBLEM_LINES))
    for rid in sorted(q.runs, key=int):
        if not q.runs[rid]["jobs_imported"]:
            print("NEXT: gh run view %s --json jobs | intake import --format "
                  "gh-run-jobs-json --source %s --run %s"
                  % (rid, _jobs_source(cfg, q.runs[rid]), rid))
    for c in sorted(wanted):
        print("NEXT: git rev-list %s | intake import --format git-rev-list --commit %s" % (c, c))
    for c in sorted(fetch):  # printed once per commit: the item is needs-resolve meanwhile
        print("NEXT: git fetch, then re-import: git rev-list %s | intake import --format "
              "git-rev-list --commit %s" % (c, c))
    return 3 if problems else 0


# -- pick -------------------------------------------------------------------------
_ITEM_ID_RE = re.compile(r"^I[0-9a-f]{10}\Z")


def _write_atomic(root, rel, text, exclusive=False):
    """Write by temp file, fsync, then an atomic rename (or, when `exclusive`, a hard
    link that fails if the file exists, so an envelope is never overwritten)."""
    path = os.path.join(root, *rel.split("/"))
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    try:
        if exclusive:
            os.link(tmp, path)
        else:
            os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def item_payload(i):
    """The intake-item/v1 payload (schemas/intake-item.v1.json)."""
    evidence = []
    for e in i.get("evidence", []):
        if isinstance(e, dict):
            evidence.append({k: e[k] for k in ("text", "source", "source_id", "fetched_at", "key")
                             if k in e})
        else:
            evidence.append({"text": str(e)})
    return {"item_id": i["id"], "title": i["title"], "kind": i.get("kind", "issue"),
            "severity": i.get("severity", 2), "source": i["source"],
            "source_id": i["source_id"], "url": i.get("url", ""),
            "trust": i.get("trust", "normal"), "count": i.get("count", 1),
            "first_seen": i["first_seen"], "last_seen": i.get("last_seen") or i["first_seen"],
            "evidence": evidence}


def cmd_pick(a):
    """new -> picked. Writes the item snapshot (the envelope's subject) and the
    intake-item/v1 envelope, both under the git-ignored .skill-contract/intake/, since
    the evidence can carry customer data."""
    if _need_config(a.root) is None:
        return 2
    try:
        with run_lock(a.root):
            q = _load(a.root)
            i = _find(q, a.id) if q else None
            if i is None:
                return 2
            if i["state"] != "new" or not _ITEM_ID_RE.match(i["id"]):
                print("STOP: only a new item can be picked; %s is %s" % (code(i["id"]), _flag(i)))
                return 2
            _ensure_dir(a.root)
            payload = item_payload(i)
            snap_rel = "%s/items/%s.json" % (INTAKE_DIR, i["id"])
            _write_atomic(a.root, snap_rel, json.dumps(payload, indent=2, sort_keys=True) + "\n")
            st = CC.build_statement(INTAKE_ITEM_KIND, SKILL_NAME, VERSION, a.root, [snap_rel],
                                    payload)
            viol = CC.check_statement(st)
            env_rel = "%s/envelopes/%s.json" % (INTAKE_DIR, st["predicate"]["id"])
            written = False
            try:
                if viol:
                    print("STOP: the intake-item envelope would be invalid (%s)"
                          % ", ".join(sorted({"C%d" % n for n, _ in viol})))
                    return 2
                _write_atomic(a.root, env_rel, json.dumps(st, indent=2, sort_keys=True) + "\n",
                              exclusive=True)
                written = True
            except FileExistsError:
                print("STOP: an envelope already exists at %s; pick again" % env_rel)
                return 2
            finally:
                if not written:  # no envelope: drop the snapshot it would have pinned
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(os.path.join(a.root, *snap_rel.split("/")))
            transition(i, "picked", by=a.by)
            i["envelope"] = env_rel
            q.log("pick", id=a.id, by=a.by, envelope=env_rel)
            q.save()
    except Locked:
        print("STOP: the intake lock is held by another command")
        return 3
    print("ITEM: %s %s %s %s" % (i["id"], _flag(i), _rank(i), code(i["title"])))
    print("NEXT: run spec-first-planning with %s" % env_rel)
    return 0


def cmd_formats(a):
    for f in FORMATS:
        note = "" if f in adapters.ADAPTERS else "(not yet built) "
        print("FORMAT: %s %s" % (f, code(note + adapters.FORMAT_HELP[f])))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="intake")
    p.add_argument("--root", default=".")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("--answers", required=True)
    s = sub.add_parser("list"); s.add_argument("--all", action="store_true")
    s = sub.add_parser("show"); s.add_argument("id")
    s = sub.add_parser("dismiss"); s.add_argument("id")
    s.add_argument("--by", required=True); s.add_argument("--reason", required=True)
    s = sub.add_parser("resolve"); s.add_argument("id"); s.add_argument("--by", required=True)
    s = sub.add_parser("link"); s.add_argument("id"); s.add_argument("other")
    s = sub.add_parser("pick"); s.add_argument("id"); s.add_argument("--by", required=True)
    sub.add_parser("status")
    sub.add_parser("sync")
    sub.add_parser("formats")
    s = sub.add_parser("import"); s.add_argument("--format", required=True)
    s.add_argument("--source"); s.add_argument("--run"); s.add_argument("--commit")
    s.add_argument("file", nargs="?", default="-")  # `-` (stdin) by default, as NEXT lines pipe
    try:
        a = p.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    a.root = os.path.abspath(a.root)
    if a.cmd == "dismiss":
        return _change(a, "dismissed")
    if a.cmd == "resolve":
        return _change(a, "resolved")
    return {"init": cmd_init, "list": cmd_list, "show": cmd_show, "link": cmd_link,
            "status": cmd_status, "sync": cmd_sync, "formats": cmd_formats,
            "import": cmd_import, "pick": cmd_pick}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
