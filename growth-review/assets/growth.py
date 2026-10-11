#!/usr/bin/env python3
"""growth-review: check exported product metrics against thresholds the human registered
before the data came in, and turn each breach into an ops-intake signal line.

The tool MUST NOT open a network connection, run a subprocess or call a model: thresholds
and statuses are code, and the human exports the data. Stdlib only, Python 3.10+, POSIX.

Usage: growth.py [--root R] [--today YYYY-MM-DD] <command> ...
  (--today is for tests only; outside the test suite it is refused, exit 2)
  lint [PLAN]                      check the metrics plan (default growth/metrics.md)
  register --by NAME [--reason T]  freeze the plan's thresholds; again after an edit needs
                                   --reason; an `agent:` name is refused
  import FILE|-                    add JSON-lines metric records to the git-ignored store
  review                           one METRIC: line per metric and a REVIEW: line
  signals --source NAME            one intake-signals-jsonl line per metric in BREACH

Exits: 0 ok; 2 refused input or a bad plan (nothing changed); 3 attention: a BREACH or a
STALE plan (review), problem records (import, the other records still imported), or the
growth lock held. `signals` exits 0 when it printed its lines, breaches included: review is
the alarm. Machine lines: LINT:, PROBLEM:, NOTE:, REGISTERED:, IMPORT:, METRIC:,
CORRECTED:, REVIEW:, STOP:, NEXT:. `signals` prints only JSON lines on stdout; its notes go
to stderr, because stdout is piped into `intake import`.
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
import unicodedata

SKILL_NAME = "growth-review"
VERSION = "0.1.0"
# --today moves the clock that decides which period is complete and which record is from
# the future, so it is honoured only when this variable is "1". Only the tests and the
# eval set it.
TEST_CLOCK_ENV = "GROWTH_TEST_CLOCK"

PLAN_PATH = "growth/metrics.md"
GROWTH_DIR = ".skill-contract/growth"
PLAN_STATE = "plan.json"
DATA_FILE = "data.jsonl"
LOG_FILE = "log.jsonl"

DIRECTIONS = ("up", "down")
PERIODS = ("day", "week", "month")
REQUIRED_COLUMNS = ("metric", "direction", "period", "warn", "breach")
OPTIONAL_COLUMNS = ("min_n", "severity")
DEFAULT_SEVERITY = 3
STATUSES = ("OK", "WARN", "BREACH", "NO_DATA")
NAME_CAP = 64
SOURCE_CAP = 200
FUTURE_GRACE = datetime.timedelta(days=1)

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\Z")
# ops-intake's source-name rule: the name goes into shell lines.
SOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,49}\Z")
NUMBER_RE = re.compile(r"^[-+]?\d+(?:\.\d+)?\Z")
INT_RE = re.compile(r"^\d+\Z")
DAY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\Z")
WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})\Z")
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})\Z")


# -- Untrusted text ---------------------------------------------------------------
# one_line(), is_hidden_char(), visible() and code() are copied from idea-validation's
# assets/ideas.py (which copies ops-intake's), because skills cannot import each other.
def one_line(text):
    """text with every run of whitespace collapsed to one space."""
    return " ".join(str(text if text is not None else "").split())


_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Co", "Cs"})


def is_hidden_char(ch):
    """True when ch can hide or disguise what a line shows."""
    cat = unicodedata.category(ch)
    return cat in _HIDDEN_CATEGORIES or (cat == "Zs" and ch != " ")


def visible(text):
    """text with every hidden character written as \\uXXXX."""
    return "".join("\\u%04x" % ord(ch) if is_hidden_char(ch) else ch for ch in text)


def code(text):
    """Untrusted text as one inline code span (see ideas.py code())."""
    s = visible(one_line(text))
    runs = [len(m) for m in re.findall(r"`+", s)]
    fence = "`" * (max(runs) + 1 if runs else 1)
    pad = " " if s.startswith("`") or s.endswith("`") or not s else ""
    return "%s%s%s%s%s" % (fence, pad, s, pad, fence)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _utc_today():
    return datetime.datetime.now(datetime.timezone.utc).date()


def _name_ok(name):
    """A name (--by) is non-empty after stripping and holds no hidden characters."""
    return (isinstance(name, str) and name.strip() != "" and len(name) <= 200
            and not any(is_hidden_char(c) for c in name))


def _is_agent(name):
    return name.strip().casefold().startswith("agent:")


def _fmt(v):
    """A number as stable text: integers without a point, other floats to 15 significant
    digits, so 0.1 + 0.2 prints 0.3 on every machine."""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if math.isfinite(v) and v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    return "%.15g" % v


# -- Periods (UTC dates; ISO weeks) -----------------------------------------------
def _month_last(y, m):
    nxt = datetime.date(y + (m == 12), m % 12 + 1, 1)
    return nxt - datetime.timedelta(days=1)


def parse_period(kind, label):
    """(first_day, last_day) for a period label of `kind`, else None. Only the canonical
    forms parse: YYYY-MM-DD, YYYY-Www (ISO week, two digits), YYYY-MM."""
    if not isinstance(label, str):
        return None
    try:
        if kind == "day":
            m = DAY_RE.match(label)
            if not m:
                return None
            d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return d, d
        if kind == "week":
            m = WEEK_RE.match(label)
            if not m:
                return None
            first = datetime.date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
            return first, first + datetime.timedelta(days=6)
        if kind == "month":
            m = MONTH_RE.match(label)
            if not m:
                return None
            y, mo = int(m.group(1)), int(m.group(2))
            return datetime.date(y, mo, 1), _month_last(y, mo)
    except ValueError:
        return None
    return None


def period_label(kind, d):
    """The label of the `kind` period that holds date d."""
    if kind == "day":
        return d.isoformat()
    if kind == "week":
        iso = d.isocalendar()
        return "%04d-W%02d" % (iso[0], iso[1])
    return "%04d-%02d" % (d.year, d.month)


def period_before(kind, label):
    first, _ = parse_period(kind, label)
    return period_label(kind, first - datetime.timedelta(days=1))


def latest_complete(kind, today):
    """The newest `kind` period that has ended by the clock: yesterday, the last ISO week,
    or last month. Taken from the clock, never from the data, so a stopped export shows
    NO_DATA instead of an old status."""
    first, _ = parse_period(kind, period_label(kind, today))
    return period_label(kind, first - datetime.timedelta(days=1))


def period_end(kind, label):
    """The instant a period ends, RFC 3339 UTC: the start of the next day after its last
    day. Day 2026-10-11 ends at 2026-10-12T00:00:00Z."""
    _, last = parse_period(kind, label)
    return (last + datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")


# -- The plan (growth/metrics.md) -------------------------------------------------
def _cells(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip().strip("`").strip() for c in s.split("|")]


_SEP_CELL = re.compile(r"^:?-{3,}:?\Z")


def _tables(text):
    """Each Markdown table as (first line number, header cells, [(line number, cells)])."""
    tables, block = [], []
    for n, line in enumerate(text.splitlines() + [""], 1):
        if line.strip().startswith("|"):
            block.append((n, line))
            continue
        if len(block) >= 2 and all(_SEP_CELL.match(c) for c in _cells(block[1][1]) if c):
            tables.append((block[0][0], _cells(block[0][1]),
                           [(k, _cells(l)) for k, l in block[2:]]))
        block = []
    return tables


def _number(s):
    if not NUMBER_RE.match(s):
        return None
    v = float(s)
    return v if math.isfinite(v) else None


def parse_plan(text):
    """{metrics: {name: row}, problems: [...]}. Problems name the line and the field; a
    plan with any problem is not registered."""
    problems, metrics = [], {}
    tables = [t for t in _tables(text) if "metric" in [h.casefold() for h in t[1]]]
    if not tables:
        return {"metrics": {}, "problems": ["no metrics table: a Markdown table whose header "
                                            "names the columns metric, direction, period, "
                                            "warn, breach (min_n and severity are optional)"]}
    if len(tables) > 1:
        problems.append("lines %s: more than one metrics table; keep one"
                        % ", ".join(str(t[0]) for t in tables))
    start, header, rows = tables[0]
    cols = [h.casefold() for h in header]
    for h in cols:
        if h not in REQUIRED_COLUMNS + OPTIONAL_COLUMNS:
            problems.append("line %d: unknown column %s" % (start, code(h)))
    for h in REQUIRED_COLUMNS:
        if h not in cols:
            problems.append("line %d: missing column %s" % (start, h))
    if len(set(cols)) != len(cols):
        problems.append("line %d: a column appears twice" % start)
    if problems:
        return {"metrics": {}, "problems": problems}
    if not rows:
        problems.append("line %d: the metrics table has no rows" % start)
    for n, cells in rows:
        if len(cells) != len(cols):
            problems.append("line %d: %d cells, the header has %d" % (n, len(cells), len(cols)))
            continue
        r = dict(zip(cols, cells))
        name = r["metric"]
        ok = True
        if not (NAME_RE.match(name) and len(name) <= NAME_CAP):
            problems.append("line %d: metric %s must be kebab-case, at most %d characters"
                            % (n, code(name), NAME_CAP))
            ok = False
        elif name in metrics:
            problems.append("line %d: metric %s appears twice" % (n, name))
            ok = False
        if r["direction"] not in DIRECTIONS:
            problems.append("line %d: direction %s must be up or down"
                            % (n, code(r["direction"])))
            ok = False
        if r["period"] not in PERIODS:
            problems.append("line %d: period %s must be day, week or month"
                            % (n, code(r["period"])))
            ok = False
        warn, breach = _number(r["warn"]), _number(r["breach"])
        for f, v in (("warn", warn), ("breach", breach)):
            if v is None:
                problems.append("line %d: %s %s must be a number" % (n, f, code(r[f])))
                ok = False
        if warn is not None and breach is not None and r["direction"] in DIRECTIONS:
            if r["direction"] == "up" and breach > warn:
                problems.append("line %d: %s: for direction up, breach must be at most warn"
                                % (n, name))
                ok = False
            if r["direction"] == "down" and breach < warn:
                problems.append("line %d: %s: for direction down, breach must be at least "
                                "warn" % (n, name))
                ok = False
        min_n = None
        if r.get("min_n", ""):
            if not INT_RE.match(r["min_n"]):
                problems.append("line %d: min_n %s must be a whole number"
                                % (n, code(r["min_n"])))
                ok = False
            else:
                min_n = int(r["min_n"])
        severity = DEFAULT_SEVERITY
        if r.get("severity", ""):
            if r["severity"] not in ("1", "2", "3", "4"):
                problems.append("line %d: severity %s must be 1 to 4"
                                % (n, code(r["severity"])))
                ok = False
            else:
                severity = int(r["severity"])
        if ok:
            metrics[name] = {"direction": r["direction"], "period": r["period"],
                             "warn": warn, "breach": breach, "min_n": min_n,
                             "severity": severity}
    return {"metrics": metrics if not problems else {}, "problems": problems}


def plan_sha(metrics):
    """sha256 of the parsed thresholds only, so an edit to the prose never makes the plan
    STALE and any threshold edit does."""
    blob = json.dumps(metrics, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _describe(m):
    return "%s %s warn=%s breach=%s min_n=%s severity=%d" % (
        m["direction"], m["period"], _fmt(m["warn"]), _fmt(m["breach"]),
        "-" if m["min_n"] is None else m["min_n"], m["severity"])


def status_of(m, value, n):
    """OK, WARN, BREACH or NO_DATA for one value under one metric's thresholds."""
    if value is None:
        return "NO_DATA"
    if m["min_n"] is not None and (n is None or n < m["min_n"]):
        return "NO_DATA"
    if m["direction"] == "up":
        if value <= m["breach"]:
            return "BREACH"
        return "WARN" if value <= m["warn"] else "OK"
    if value >= m["breach"]:
        return "BREACH"
    return "WARN" if value >= m["warn"] else "OK"


# -- Storage: ignore check, lock, atomic writes -----------------------------------
LOCK_FILE = ".lock"
LOCK_TIMEOUT = 900  # read at call time, so tests can shorten it
LOCK_NOTICE_AFTER = 2.0


class Locked(Exception):
    """The growth lock is still held after the timeout."""


class NotIgnored(Exception):
    """The state directory cannot be shown to be git-ignored."""


class StateError(Exception):
    """A state file is unreadable."""


def _growth_dir(root):
    return os.path.join(root, *GROWTH_DIR.split("/"))


def _gitignore_lines(path):
    with open(path, encoding="utf-8") as f:
        return [l.strip() for l in f.read().splitlines()]


def _gitignore_ok(path):
    """True when the file holds `*` and no `!` line (which would re-include files)."""
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return False
        lines = _gitignore_lines(path)
        return "*" in lines and not any(l.startswith("!") for l in lines)
    except (OSError, ValueError):
        return False


def _gitignore_negates(path):
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return False
        return any(l.startswith("!") for l in _gitignore_lines(path))
    except (OSError, ValueError):
        return False


def _ensure_dir(root):
    """Make .skill-contract/growth/ and prove it is git-ignored before any other write.
    Mirrors idea-validation's and ops-intake's _ensure_dir(): the tool runs no subprocess,
    so it cannot ask `git check-ignore`; the directory carries its own `.gitignore` holding
    `*`, which git honours whatever the root .gitignore says. A symlinked directory, a
    `.gitignore` that is not a plain file, or one with a `!` line raises NotIgnored."""
    top = os.path.join(root, ".skill-contract")
    d = _growth_dir(root)
    if os.path.islink(top) or os.path.islink(d):
        raise NotIgnored("%s is a symlink" % GROWTH_DIR)
    gi = os.path.join(d, ".gitignore")
    if os.path.isdir(d) and _gitignore_ok(gi):
        return d
    if _gitignore_negates(gi):
        raise NotIgnored("%s/.gitignore has a `!` line that un-ignores files" % GROWTH_DIR)
    if os.path.lexists(gi) and not os.path.isfile(gi) or os.path.islink(gi):
        raise NotIgnored("%s/.gitignore is not a regular file" % GROWTH_DIR)
    try:
        os.makedirs(d, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(gi)
        with open(gi, "w", encoding="utf-8") as f:
            f.write("# written by growth-review: revenue and user counts are business data, "
                    "never commit them\n*\n")
    except OSError as e:
        raise NotIgnored(one_line(e))
    if not _gitignore_ok(gi):
        raise NotIgnored("cannot verify %s/.gitignore" % GROWTH_DIR)
    return d


@contextlib.contextmanager
def run_lock(root, timeout=None):
    """An exclusive flock on .skill-contract/growth/.lock (copied from ideas.py run_lock()).
    The ignore check runs first."""
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
                    sys.stderr.write("waiting for the growth lock held by another command…\n")
                    sys.stderr.flush()
                    told = True
                time.sleep(0.1)
        yield
    finally:
        f.close()


def _fsync_dir(d):
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_atomic(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    _fsync_dir(d)


def _append(root, rel, records):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _log(root, event, **fields):
    _append(root, GROWTH_DIR + "/" + LOG_FILE, [dict(fields, event=event, at=_now())])


def load_registrations(root):
    path = os.path.join(_growth_dir(root), PLAN_STATE)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        regs = st["registrations"]
        if not isinstance(regs, list):
            raise ValueError("registrations is not a list")
        return regs
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise StateError("cannot read %s/%s: %s" % (GROWTH_DIR, PLAN_STATE, one_line(e)))


def load_data(root):
    """Every stored record, in import order."""
    path = os.path.join(_growth_dir(root), DATA_FILE)
    if not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if line.strip():
                    r = json.loads(line)
                    if not isinstance(r, dict):
                        raise ValueError("line %d is not an object" % n)
                    out.append(r)
    except (OSError, ValueError) as e:
        raise StateError("cannot read %s/%s: %s" % (GROWTH_DIR, DATA_FILE, one_line(e)))
    return out


def latest_values(records):
    """{(metric, period): newest record} and {(metric, period): [records in order]}."""
    latest, history = {}, {}
    for r in records:
        k = (r.get("metric"), r.get("period"))
        latest[k] = r
        history.setdefault(k, []).append(r)
    return latest, history


# -- Data records -----------------------------------------------------------------
def _reject_constant(name):
    raise ValueError("%s is not a finite number" % name)


def check_record(rec, metrics, today):
    """A clean record dict, or raise ValueError naming the field (never quoting it)."""
    if not isinstance(rec, dict):
        raise ValueError("not an object")
    name = rec.get("metric")
    if not isinstance(name, str) or name not in metrics:
        raise ValueError("metric is not in the registered plan")
    m = metrics[name]
    span = parse_period(m["period"], rec.get("period"))
    if span is None:
        raise ValueError("period does not fit the metric's period %s (%s)"
                         % (m["period"], {"day": "YYYY-MM-DD", "week": "YYYY-Www",
                                           "month": "YYYY-MM"}[m["period"]]))
    if span[1] > today + FUTURE_GRACE:
        raise ValueError("period ends more than 1 day after today (UTC)")
    v = rec.get("value")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError("value must be a finite number")
    n = rec.get("n")
    if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 0):
        raise ValueError("n must be a whole number of at least 0")
    src = rec.get("source")
    if not (isinstance(src, str) and src.strip() and len(src) <= SOURCE_CAP):
        raise ValueError("source must be a non-empty string of at most %d characters"
                         % SOURCE_CAP)
    return {"metric": name, "period": rec["period"], "value": v, "n": n, "source": src}


def _same(a, b):
    return a.get("value") == b.get("value") and a.get("n") == b.get("n")


# -- Evaluation -------------------------------------------------------------------
def _current_plan(root):
    """(metrics or None, why-not text)."""
    path = os.path.join(root, *PLAN_PATH.split("/"))
    try:
        with open(path, encoding="utf-8") as f:
            parsed = parse_plan(f.read())
    except FileNotFoundError:
        return None, "%s is missing" % PLAN_PATH
    except (OSError, ValueError) as e:
        return None, "%s cannot be read (%s)" % (PLAN_PATH, one_line(e))
    if parsed["problems"]:
        return None, "%s does not lint (%d problems)" % (PLAN_PATH, len(parsed["problems"]))
    return parsed["metrics"], ""


def stale_notes(root, reg):
    """[] when growth/metrics.md matches the registration, else one note per difference.
    A missing or broken plan is STALE too: the frozen copy stays in force."""
    metrics, why = _current_plan(root)
    if metrics is None:
        return [why]
    if plan_sha(metrics) == reg["sha256"]:
        return []
    frozen, notes = reg["metrics"], []
    for name in sorted(set(frozen) | set(metrics)):
        if name not in metrics:
            notes.append("%s removed from %s (registered: %s)"
                         % (name, PLAN_PATH, _describe(frozen[name])))
        elif name not in frozen:
            notes.append("%s added to %s, not registered: %s"
                         % (name, PLAN_PATH, _describe(metrics[name])))
        elif metrics[name] != frozen[name]:
            notes.append("%s edited: registered %s, now %s"
                         % (name, _describe(frozen[name]), _describe(metrics[name])))
    return notes


def evaluate(reg, records, today):
    """One row per registered metric: {name, metric, period, record, status, streak_first}.
    Uses the frozen thresholds only."""
    latest, _ = latest_values(records)
    rows = []
    for name in sorted(reg["metrics"]):
        m = reg["metrics"][name]
        label = latest_complete(m["period"], today)
        rec = latest.get((name, label))
        st = status_of(m, rec["value"] if rec else None, rec.get("n") if rec else None)
        first = label
        if st == "BREACH":
            limit = sum(1 for k in latest if k[0] == name)
            p = label
            for _ in range(limit):
                p = period_before(m["period"], p)
                r = latest.get((name, p))
                if r is None or status_of(m, r["value"], r.get("n")) != "BREACH":
                    break
                first = p
        rows.append({"name": name, "metric": m, "period": label, "record": rec,
                     "status": st, "streak_first": first})
    return rows


def corrections(reg, records):
    """(metric, period, old value, new value, new status) for each period that a later
    record moved out of BREACH."""
    _, history = latest_values(records)
    out = []
    for (name, period), recs in sorted(history.items(), key=lambda kv: (str(kv[0][0]),
                                                                          str(kv[0][1]))):
        m = reg["metrics"].get(name)
        if m is None or len(recs) < 2:
            continue
        now = status_of(m, recs[-1]["value"], recs[-1].get("n"))
        if now == "BREACH":
            continue
        was = [r for r in recs[:-1] if status_of(m, r["value"], r.get("n")) == "BREACH"]
        if was:
            out.append((name, period, was[-1]["value"], recs[-1]["value"], now))
    return out


def signal_line(source, row):
    m, rec = row["metric"], row["record"]
    return {"source": source, "source_id": row["name"], "kind": "issue",
            "title": "metric: %s breached" % row["name"], "severity": m["severity"],
            "first_seen": period_end(m["period"], row["streak_first"]),
            "last_seen": period_end(m["period"], row["period"]),
            "evidence": [{"key": "%s:%s" % (row["name"], row["period"]),
                          "text": "value %s vs breach %s (%s)"
                                  % (_fmt(rec["value"]), _fmt(m["breach"]), m["direction"])}]}


# -- Commands ---------------------------------------------------------------------
def _not_ignored(e, out=None):
    out = out or sys.stdout
    print("STOP: %s cannot be shown to be git-ignored (%s); revenue and user counts are "
          "business data" % (GROWTH_DIR, code(e)), file=out)
    print("NEXT: make %s a plain directory whose .gitignore holds `*`" % GROWTH_DIR, file=out)
    return 2


def _locked(out=None):
    print("STOP: the growth lock is held by another command", file=out or sys.stdout)
    return 3


def _print_problems(problems):
    for p in problems:
        print("PROBLEM: %s" % p)


def cmd_lint(a):
    path = a.plan if os.path.isabs(a.plan) else os.path.join(a.root, a.plan)
    try:
        with open(path, encoding="utf-8") as f:
            parsed = parse_plan(f.read())
    except (OSError, ValueError) as e:
        print("STOP: cannot read the plan: %s" % code(e))
        return 2
    if parsed["problems"]:
        _print_problems(parsed["problems"])
        print("LINT: fail %d problems" % len(parsed["problems"]))
        return 2
    print("LINT: ok %d metrics" % len(parsed["metrics"]))
    return 0


def cmd_register(a):
    if not _name_ok(a.by):
        print("STOP: --by needs the name of the human who registers the thresholds")
        return 2
    if _is_agent(a.by):
        print("STOP: an agent: name cannot register thresholds; only the human registers "
              "the goalposts the review is judged against")
        return 2
    if a.reason is not None and not _name_ok(a.reason):
        print("STOP: --reason must be non-empty text without hidden characters")
        return 2
    path = os.path.join(a.root, *PLAN_PATH.split("/"))
    try:
        with open(path, encoding="utf-8") as f:
            parsed = parse_plan(f.read())
    except (OSError, ValueError) as e:
        print("STOP: cannot read %s: %s" % (PLAN_PATH, code(e)))
        return 2
    if parsed["problems"]:
        _print_problems(parsed["problems"])
        print("LINT: fail %d problems" % len(parsed["problems"]))
        print("NEXT: fix %s with the human, then register again" % PLAN_PATH)
        return 2
    sha = plan_sha(parsed["metrics"])
    try:
        with run_lock(a.root):
            regs = load_registrations(a.root)
            if regs and regs[-1]["sha256"] == sha:
                print("REGISTERED: %s (no change since the last registration)" % sha[:12])
                return 0
            if regs and a.reason is None:
                for note in stale_notes(a.root, regs[-1]):
                    print("NOTE: stale %s" % note)
                print("STOP: the thresholds changed since the registration of %s; "
                      "registering again needs --reason" % regs[-1]["at"])
                print("NEXT: show the human the NOTE: lines; the human registers again with "
                      "register --by <their name> --reason <why>")
                return 2
            regs.append({"sha256": sha, "by": a.by.strip(), "at": _now(),
                         "reason": a.reason.strip() if a.reason else None,
                         "metrics": parsed["metrics"]})
            _write_atomic(a.root, GROWTH_DIR + "/" + PLAN_STATE,
                          json.dumps({"version": 1, "registrations": regs}, indent=1,
                                     sort_keys=True) + "\n")
            _log(a.root, "register", sha256=sha, by=a.by.strip(), reason=a.reason,
                 metrics=sorted(parsed["metrics"]))
    except NotIgnored as e:
        return _not_ignored(e)
    except Locked:
        return _locked()
    except StateError as e:
        print("STOP: %s" % code(e))
        return 2
    print("LINT: ok %d metrics" % len(parsed["metrics"]))
    print("REGISTERED: %s by %s, %d metrics" % (sha[:12], code(a.by.strip()),
                                               len(parsed["metrics"])))
    print("NEXT: the human exports the metric data; import it with import <file>")
    return 0


def _need_reg(root):
    regs = load_registrations(root)
    if not regs:
        print("STOP: no registered plan; the human registers %s first with register --by "
              "<their name>" % PLAN_PATH)
        return None
    return regs[-1]


def cmd_import(a):
    try:
        if a.file == "-":
            raw = sys.stdin.read()
        else:
            with open(a.file, encoding="utf-8") as f:
                raw = f.read()
    except (OSError, ValueError) as e:
        print("STOP: cannot read the data file: %s" % code(e))
        return 2
    try:
        with run_lock(a.root):
            reg = _need_reg(a.root)
            if reg is None:
                return 2
            latest, _ = latest_values(load_data(a.root))
            now = _now()
            new, problems, added, corrected, unchanged = [], [], 0, 0, 0
            for n, line in enumerate(raw.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    rec = check_record(json.loads(line, parse_constant=_reject_constant),
                                       reg["metrics"], a.today)
                except (ValueError, RecursionError) as e:
                    problems.append("line %d: %s" % (n, e if isinstance(e, ValueError)
                                                     else "nested too deeply"))
                    continue
                k = (rec["metric"], rec["period"])
                old = latest.get(k)
                if old is not None and _same(old, rec):
                    unchanged += 1
                    continue
                rec.update(imported_at=now, correction=old is not None)
                if old is None:
                    added += 1
                else:
                    corrected += 1
                latest[k] = rec
                new.append(rec)
            if new:
                _append(a.root, GROWTH_DIR + "/" + DATA_FILE, new)
                _log(a.root, "import", added=added, corrected=corrected,
                     unchanged=unchanged, problems=len(problems))
    except NotIgnored as e:
        return _not_ignored(e)
    except Locked:
        return _locked()
    except StateError as e:
        print("STOP: %s" % code(e))
        return 2
    _print_problems(problems[:50])
    if len(problems) > 50:
        print("PROBLEM: and %d more" % (len(problems) - 50))
    print("IMPORT: added %d corrected %d unchanged %d problems %d"
          % (added, corrected, unchanged, len(problems)))
    print("NEXT: review")
    return 3 if problems else 0


def _load_for_review(root, out):
    """(reg, records, stale notes) under the lock, or an exit code."""
    try:
        with run_lock(root):
            regs = load_registrations(root)
            if not regs:
                print("STOP: no registered plan; the human registers %s first with register "
                      "--by <their name>" % PLAN_PATH, file=out)
                return 2
            return regs[-1], load_data(root), stale_notes(root, regs[-1])
    except NotIgnored as e:
        return _not_ignored(e, out)
    except Locked:
        return _locked(out)
    except StateError as e:
        print("STOP: %s" % code(e), file=out)
        return 2


def cmd_review(a):
    got = _load_for_review(a.root, sys.stdout)
    if isinstance(got, int):
        return got
    reg, records, stale = got
    rows = evaluate(reg, records, a.today)
    flag = " +stale" if stale else ""
    counts = {s: 0 for s in STATUSES}
    for r in rows:
        counts[r["status"]] += 1
        v = _fmt(r["record"]["value"]) if r["record"] else "-"
        print("METRIC: %s %s%s value=%s period=%s" % (r["name"], r["status"], flag, v,
                                                     r["period"]))
    for name, period, old, new, now in corrections(reg, records):
        print("CORRECTED: %s %s was=%s now=%s %s" % (name, period, _fmt(old), _fmt(new), now))
    for note in stale:
        print("NOTE: stale %s" % note)
    print("REVIEW: ok=%d warn=%d breach=%d no_data=%d stale=%s"
          % (counts["OK"], counts["WARN"], counts["BREACH"], counts["NO_DATA"],
             "yes" if stale else "no"))
    if counts["BREACH"]:
        print("NEXT: signals --source <intake source> | intake import --format "
              "intake-signals-jsonl --source <intake source>")
    if stale:
        print("NEXT: show the human the NOTE: stale lines; the review used the thresholds "
              "registered on %s, and only the human registers again" % reg["at"])
    return 3 if counts["BREACH"] or stale else 0


def cmd_signals(a):
    err = sys.stderr
    if not SOURCE_RE.match(a.source or "") or a.source == "release":
        print("STOP: --source must be an ops-intake source name (1 to 50 letters, digits, _, "
              ". or -, starting with a letter or digit; not release)", file=err)
        return 2
    got = _load_for_review(a.root, err)
    if isinstance(got, int):
        return got
    reg, records, stale = got
    if stale:
        print("NOTE: the plan is STALE; these signals use the registered thresholds",
              file=err)
    for r in evaluate(reg, records, a.today):
        if r["status"] == "BREACH":
            print(json.dumps(signal_line(a.source, r), sort_keys=True))
    return 0


def _today_arg(s):
    m = DAY_RE.match(s or "")
    try:
        return datetime.date.fromisoformat(s) if m else None
    except ValueError:
        return None


def main(argv=None):
    p = argparse.ArgumentParser(prog="growth.py")
    p.add_argument("--root", default=".")
    p.add_argument("--today", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("lint"); s.add_argument("plan", nargs="?", default=PLAN_PATH)
    s = sub.add_parser("register"); s.add_argument("--by", required=True)
    s.add_argument("--reason")
    s = sub.add_parser("import"); s.add_argument("file")
    sub.add_parser("review")
    s = sub.add_parser("signals"); s.add_argument("--source", required=True)
    try:
        a = p.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    a.root = os.path.abspath(a.root)
    if a.today is not None:
        if os.environ.get(TEST_CLOCK_ENV) != "1":
            print("STOP: --today is for tests only")
            return 2
        a.today = _today_arg(a.today)
        if a.today is None:
            print("STOP: --today must be a real YYYY-MM-DD date")
            return 2
    else:
        a.today = _utc_today()
    return {"lint": cmd_lint, "register": cmd_register, "import": cmd_import,
            "review": cmd_review, "signals": cmd_signals}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
