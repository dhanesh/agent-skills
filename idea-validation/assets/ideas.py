#!/usr/bin/env python3
"""idea-validation: lint a validation brief, pre-register it, record results, compute a
verdict, and hand a `go` idea to spec-first-planning as an idea-brief/v1 envelope.

The tool MUST NOT open a network connection, run a subprocess or call a model: thresholds
and verdicts are code, and every experiment is run by a human. Stdlib only, Python 3.10+,
POSIX.

Usage: ideas.py [--root R] [--today YYYY-MM-DD] <command> ...
  new <slug> --title TEXT
  lint <brief>
  register <brief> --by NAME
  record <slug> <A-id> --value N --kind experiment|desk --source TEXT
         --observed YYYY-MM-DD --by NAME [--evidence-file F]
  status <slug>
  decide <slug> --verdict go|kill|pivot --by NAME --reason TEXT
  handoff <slug> --by NAME
  list

Exits: 0 ok; 2 refused input, a bad brief, an unignored state directory or a stale
handoff; 3 the lock is held. Machine lines: LINT:, PROBLEM:, NOTE:, ASSUMPTION:,
VERDICT:, NEXT: (and IDEA:, RESULT:, EXPERIMENT:, DECISION:, STOP: for context).
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

import contract_check as CC  # the vendored skill-contract checker, same dir

SKILL_NAME = "idea-validation"
VERSION = "0.1.0"
IDEA_KIND = "https://github.com/dhanesh/agent-skills/skill-contract/idea-brief/v1"

BRIEFS_DIR = "ideas"
IDEAS_DIR = ".skill-contract/ideas"
ENVELOPES_DIR = IDEAS_DIR + "/envelopes"
STATE_FILE = "state.json"
RESULTS_FILE = "results.jsonl"
LOG_FILE = "log.jsonl"
EVIDENCE_CAP = 4096  # bytes

REQUIRED_SECTIONS = ("problem", "target user", "value proposition", "riskiest assumptions",
                     "experiments", "out of scope")
SEVERITIES = ("critical", "normal")
CATEGORIES = ("problem", "demand", "solution", "channel", "pricing", "feasibility")
FIELDS = ("test", "metric", "pass", "kill", "by")
STALE_FIELDS = FIELDS  # a change to any of these makes earlier results stale
KINDS = ("experiment", "desk")
DECISIONS = ("go", "kill", "pivot")

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\Z")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
AID_RE = re.compile(r"^A[1-9][0-9]{0,3}\Z")

TEMPLATE = """# Idea: {title}

## Problem
Who has which problem today, and what it costs them. One short paragraph.

## Target user
The one group of people this idea is for first.

## Value proposition
What the user gets, in one or two sentences.

## Riskiest assumptions
List 3 to 7 assumptions, critical ones first. One line each, in this form:
`- A<n> [critical|normal] [problem|demand|solution|channel|pricing|feasibility]: <statement> (test: <how>; metric: <what is counted>; pass: <op> <number>; kill: <op> <number>; by: YYYY-MM-DD)`
<op> is >=, >, <= or <. pass and kill point in opposite directions and do not overlap.

## Experiments
One `- ` bullet per experiment. Each bullet names the assumption ids it tests (A1, A2).
Start a bullet with [outward] when it publishes, sends, spends or contacts a person: a human runs it.

## Out of scope
- What this idea does not try to do.
"""


# -- Untrusted text ---------------------------------------------------------------
# one_line(), is_hidden_char(), visible() and code() are copied from ops-intake's
# assets/intake.py (which copies release-conductor's and factory-conductor's), because
# skills cannot import each other.
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
    """Untrusted text as one inline code span: newlines collapsed, a backtick fence longer
    than any backtick run inside, hidden characters written as \\uXXXX. It cannot open a
    heading, list or link, and GitHub does not act on @mentions inside it."""
    s = visible(one_line(text))
    runs = [len(m) for m in re.findall(r"`+", s)]
    fence = "`" * (max(runs) + 1 if runs else 1)
    pad = " " if s.startswith("`") or s.endswith("`") or not s else ""
    return "%s%s%s%s%s" % (fence, pad, s, pad, fence)


def _clip(text, limit=500):
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "…"


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _date(s):
    """A datetime.date for a real YYYY-MM-DD, else None."""
    if not isinstance(s, str) or not DATE_RE.match(s):
        return None
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return None


def _name_ok(name):
    """A name (--by) is non-empty after stripping and holds no hidden characters."""
    return (isinstance(name, str) and name.strip() != "" and len(name) <= 200
            and not any(is_hidden_char(c) for c in name))


def _is_agent(name):
    return name.strip().casefold().startswith("agent:")


# -- Brief grammar (spec 1.2) -----------------------------------------------------
_TITLE_RE = re.compile(r"^#\s+Idea:\s*(.*?)\s*$")
_SECTION_RE = re.compile(r"^##\s+(.*?)\s*#*\s*$")
_ASSUMPTION_START = re.compile(r"^\s*-\s+A(\d+)\b")
_ASSUMPTION_HEAD = re.compile(
    r"^\s*-\s+A(?P<n>\d+)\s+\[(?P<sev>[^\]]*)\]\s+\[(?P<cat>[^\]]*)\]\s*:\s*(?P<rest>.*?)\s*$")
_FIELDS_TAIL = re.compile(r"^(?P<stmt>.*)\((?P<fields>\s*test\s*:.*)\)\s*$")
_FIELD_SPLIT = re.compile(r";\s*(?=(?:test|metric|pass|kill|by)\s*:)")
_FIELD_KV = re.compile(r"^\s*(test|metric|pass|kill|by)\s*:\s*(.*?)\s*$", re.S)
_THRESHOLD_RE = re.compile(r"^(>=|>|<=|<)\s*([-+]?\d+(?:\.\d+)?)\Z")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_ID_REF = re.compile(r"\bA(\d+)\b")


def parse_threshold(s):
    """(op, number) for '<op> <number>', else None. Only finite decimals parse."""
    m = _THRESHOLD_RE.match(s.strip()) if isinstance(s, str) else None
    if not m:
        return None
    v = float(m.group(2))
    return (m.group(1), v) if math.isfinite(v) else None


def meets(threshold, value):
    op, t = parse_threshold(threshold)
    return {">=": value >= t, ">": value > t, "<=": value <= t, "<": value < t}[op]


def _overlap(pas, kill):
    """True when some value meets both thresholds. Both are (op, number) in opposite
    directions."""
    up, down = (pas, kill) if pas[0] in (">=", ">") else (kill, pas)
    lo, lo_incl = up[1], up[0] == ">="
    hi, hi_incl = down[1], down[0] == "<="
    return hi > lo or (hi == lo and lo_incl and hi_incl)


def _parse_assumption(line, problems):
    """One assumption dict from a `- A<n> ...` line, or None after adding problems."""
    m = _ASSUMPTION_HEAD.match(line)
    if not m:
        problems.append("malformed assumption line %s; expected `- A<n> [critical|normal] "
                        "[category]: <statement> (test: …; metric: …; pass: …; kill: …; by: …)`"
                        % code(line))
        return None
    aid = "A" + str(int(m.group("n")))
    a = {"id": aid, "n": int(m.group("n")), "critical": m.group("sev").strip() == "critical",
         "severity": m.group("sev").strip(), "category": m.group("cat").strip()}
    ok = True
    if a["severity"] not in SEVERITIES:
        problems.append("%s: severity %s must be critical or normal" % (aid, code(a["severity"])))
        ok = False
    if a["category"] not in CATEGORIES:
        problems.append("%s: category %s must be one of %s"
                        % (aid, code(a["category"]), ", ".join(CATEGORIES)))
        ok = False
    t = _FIELDS_TAIL.match(m.group("rest"))
    if not t:
        problems.append("%s: needs a (test: …; metric: …; pass: …; kill: …; by: …) block" % aid)
        return None
    a["statement"] = t.group("stmt").strip()
    if not a["statement"]:
        problems.append("%s: the statement is empty" % aid)
        ok = False
    fields = {}
    for part in _FIELD_SPLIT.split(t.group("fields")):
        kv = _FIELD_KV.match(part)
        if not kv:
            problems.append("%s: cannot read field %s" % (aid, code(part)))
            ok = False
            continue
        if kv.group(1) in fields:
            problems.append("%s: field %s appears twice" % (aid, kv.group(1)))
            ok = False
        fields[kv.group(1)] = one_line(kv.group(2))
    for f in FIELDS:
        if not fields.get(f):
            problems.append("%s: field %s is missing or empty" % (aid, f))
            ok = False
        a[f] = fields.get(f, "")
    pas, kill = parse_threshold(a["pass"]), parse_threshold(a["kill"])
    for name, val, parsed in (("pass", a["pass"], pas), ("kill", a["kill"], kill)):
        if val and parsed is None:
            problems.append("%s: %s %s must be <op> <number> with <op> one of >=, >, <=, <"
                            % (aid, name, code(val)))
            ok = False
    if pas and kill:
        a["pass"] = "%s %s" % (pas[0], a["pass"].strip()[len(pas[0]):].strip())
        a["kill"] = "%s %s" % (kill[0], a["kill"].strip()[len(kill[0]):].strip())
        if (pas[0] in (">=", ">")) == (kill[0] in (">=", ">")):
            problems.append("%s: pass and kill must point in opposite directions" % aid)
            ok = False
        elif _overlap(pas, kill):
            problems.append("%s: pass %s and kill %s overlap; a value must not meet both"
                            % (aid, code(a["pass"]), code(a["kill"])))
            ok = False
    if a["by"] and _date(a["by"]) is None:
        problems.append("%s: by %s must be a real date (YYYY-MM-DD)" % (aid, code(a["by"])))
        ok = False
    return a if ok else None


def parse_brief(text):
    """Parse a brief. Returns {title, sections, assumptions, experiments, problems, notes}.
    `assumptions` holds only well-formed lines; `problems` lists every fault."""
    problems, notes = [], []
    title, sections, order, current = None, {}, [], None
    for line in text.splitlines():
        if title is None and current is None:
            m = _TITLE_RE.match(line)
            if m:
                title = m.group(1)
                continue
        m = _SECTION_RE.match(line)
        if m and not line.startswith("###"):
            current = m.group(1).strip().casefold()
            if current in sections:
                problems.append("section %s appears twice" % code("## " + m.group(1).strip()))
            sections.setdefault(current, [])
            order.append(current)
            continue
        if current is not None:
            sections[current].append(line)
    if not title:
        problems.append("missing title line `# Idea: <title>`")
    for s in REQUIRED_SECTIONS:
        if s not in sections:
            problems.append("missing required section %s" % code("## " + s))

    assumptions, ns = [], []
    for line in sections.get("riskiest assumptions", []):
        start = _ASSUMPTION_START.match(line)
        if start:
            ns.append(int(start.group(1)))
        if start or _BULLET_RE.match(line):
            a = _parse_assumption(line, problems)
            if a:
                assumptions.append(a)
    if "riskiest assumptions" in sections:
        if not ns:
            problems.append("no assumption lines under `## Riskiest assumptions`")
        elif ns != list(range(1, len(ns) + 1)):
            problems.append("assumption ids must run A1..A%d in order with no gaps or repeats; "
                            "found %s" % (len(ns), ", ".join("A%d" % n for n in ns)))
        if ns and not any(a["critical"] for a in assumptions) and not any(
                re.match(r"^\s*-\s+A\d+\s+\[critical\]", l)
                for l in sections["riskiest assumptions"]):
            problems.append("no assumption is critical; mark at least one [critical]")
    crit = [a for a in assumptions if a["critical"]]
    if crit and not any(a["category"] in ("problem", "demand") for a in crit):
        notes.append("no critical assumption is [problem] or [demand]; most ideas fail on demand")

    experiments = []
    known = {"A%d" % n for n in ns}
    if "experiments" in sections:
        for line in sections["experiments"]:
            b = _BULLET_RE.match(line)
            if not b:
                continue
            body = b.group(1)
            ids = []
            for n in _ID_REF.findall(body):
                aid = "A%d" % int(n)
                if aid not in ids:
                    ids.append(aid)
            e = {"n": len(experiments) + 1, "text": body.strip(), "ids": ids,
                 "outward": "[outward]" in body.casefold()}
            experiments.append(e)
            if not ids:
                problems.append("experiment %d names no assumption id: %s" % (e["n"], code(body)))
            for aid in ids:
                if aid not in known:
                    problems.append("experiment %d names unknown assumption %s" % (e["n"], aid))
        if not experiments:
            problems.append("`## Experiments` lists no experiment; write one `- ` bullet per "
                            "experiment")
    text_of = {k: "\n".join(v).strip() for k, v in sections.items()}
    return {"title": title, "sections": text_of, "assumptions": assumptions,
            "experiments": experiments, "problems": problems, "notes": notes}


# -- Storage: lock, ignore check, atomic writes -----------------------------------
LOCK_FILE = ".lock"
LOCK_TIMEOUT = 900  # read at call time, so tests can shorten it
LOCK_NOTICE_AFTER = 2.0
LOCK_NOTICE = "waiting for the ideas lock held by another ideas command…\n"


class Locked(Exception):
    """Raised by run_lock() when the lock is still held after `timeout` seconds."""


class NotIgnored(Exception):
    """The state directory cannot be shown to be git-ignored."""


def _ideas_dir(root):
    return os.path.join(root, *IDEAS_DIR.split("/"))


def _gitignore_ok(path):
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return False
        with open(path, encoding="utf-8") as f:
            return "*" in [l.strip() for l in f.read().splitlines()]
    except (OSError, ValueError):
        return False


def _ensure_dir(root):
    """Make .skill-contract/ideas/ and prove it is git-ignored before any other write.

    The ignore check cannot run `git check-ignore`, because the tool runs no subprocess.
    It mirrors ops-intake's _ensure_dir() instead: the directory carries its own
    `.gitignore` holding `*`, which git honours whatever the root .gitignore says (a
    deeper .gitignore wins). The tool writes that file when it is missing and then reads
    it back. It raises NotIgnored, and the command stops with exit 2, when the file
    cannot be made and verified (it is a directory, a symlink or unwritable) or when
    `.skill-contract` or `.skill-contract/ideas` is a symlink (writes would land
    somewhere the ignore file does not cover)."""
    top = os.path.join(root, ".skill-contract")
    d = _ideas_dir(root)
    if os.path.islink(top) or os.path.islink(d):
        raise NotIgnored("%s is a symlink" % IDEAS_DIR)
    gi = os.path.join(d, ".gitignore")
    if os.path.isdir(d) and _gitignore_ok(gi):
        return d
    if os.path.lexists(gi) and not os.path.isfile(gi) or os.path.islink(gi):
        raise NotIgnored("%s/.gitignore is not a regular file" % IDEAS_DIR)
    try:
        os.makedirs(d, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(gi)
        with open(gi, "w", encoding="utf-8") as f:
            f.write("# written by idea-validation: results can quote interviews and sign-up "
                    "data, never commit them\n*\n")
    except OSError as e:
        raise NotIgnored(one_line(e))
    if not _gitignore_ok(gi):
        raise NotIgnored("cannot verify %s/.gitignore" % IDEAS_DIR)
    return d


@contextlib.contextmanager
def run_lock(root, timeout=None):
    """Hold an exclusive flock on <root>/.skill-contract/ideas/.lock. Copied from
    ops-intake/assets/intake.py run_lock() (itself from release-conductor): not
    re-entrant, released when the holder exits or dies. The ignore check runs first."""
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


def _fsync_dir(d):
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_atomic(root, rel, data, exclusive=False):
    """Write by temp file, fsync, then an atomic rename (or, when `exclusive`, a hard link
    that fails if the file exists). Copied from ops-intake/assets/intake.py
    _write_atomic(), extended to take bytes."""
    path = os.path.join(root, *rel.split("/"))
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    if isinstance(data, str):
        data = data.encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(data)
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
    _fsync_dir(d)


def _append(root, rel, record):
    """Append one JSON line (append-only files: results.jsonl, log.jsonl)."""
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _log(root, event, **fields):
    _append(root, IDEAS_DIR + "/" + LOG_FILE, dict(fields, event=event, at=_now()))


class StateError(Exception):
    """A state or results file is unreadable."""


def _state_rel(slug):
    return "%s/%s/%s" % (IDEAS_DIR, slug, STATE_FILE)


def load_state(root, slug):
    """The idea's state dict, or None when it was never registered."""
    path = os.path.join(root, *_state_rel(slug).split("/"))
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise StateError("cannot read %s: %s" % (_state_rel(slug), one_line(e)))
    if not (isinstance(st, dict) and isinstance(st.get("registrations"), list)
            and st["registrations"] and isinstance(st.get("decisions"), list)
            and all(isinstance(r, dict) and isinstance(r.get("assumptions"), dict)
                    and isinstance(r.get("sha256"), str) for r in st["registrations"])):
        raise StateError("%s is not an idea state file" % _state_rel(slug))
    return st


def save_state(root, slug, st):
    _write_atomic(root, _state_rel(slug), json.dumps(st, indent=1, sort_keys=True) + "\n")


def load_results(root, slug):
    path = os.path.join(root, IDEAS_DIR, slug, RESULTS_FILE)
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if not line.strip():
                    continue
                r = json.loads(line)
                if not (isinstance(r, dict) and isinstance(r.get("value"), (int, float))
                        and _date(r.get("observed")) and r.get("kind") in KINDS):
                    raise ValueError("line %d is not a result" % (i + 1))
                r["_seq"] = i
                out.append(r)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        raise StateError("cannot read %s/%s/%s: %s" % (IDEAS_DIR, slug, RESULTS_FILE,
                                                      one_line(e)))
    return out


# -- Statuses and verdict (spec 1.3) ----------------------------------------------
def _is_stale(r, current, regs):
    reg = regs.get(r.get("registration"))
    aid = r.get("assumption")
    if reg is None or aid not in reg["assumptions"] or aid not in current["assumptions"]:
        return True
    then, now = reg["assumptions"][aid], current["assumptions"][aid]
    return any(then.get(f) != now.get(f) for f in STALE_FIELDS)


def assumption_status(a, counted, today):
    """The status of one assumption from its counted results (oldest first by observed)."""
    by = _date(a["by"])
    if not counted:
        base = "open"
    else:
        latest = counted[-1]
        if latest["kind"] == "desk" and not meets(a["kill"], latest["value"]):
            # Desk research is weak evidence (spec D5): it can kill at any time, but it
            # never outweighs a human's experiment in either direction. A later desk pass
            # or in-between value neither undoes an experiment pass nor rescues an
            # experiment kill, so the latest experiment result decides when there is one.
            exps = [r for r in counted if r["kind"] == "experiment"]
            if exps:
                latest = exps[-1]
        v = latest["value"]
        if meets(a["kill"], v):
            base = "fail"
        elif meets(a["pass"], v):
            if a["critical"] and latest["kind"] == "desk":
                base = "needs-experiment"  # a desk pass never passes a critical assumption
            elif _date(latest["observed"]) > by:
                base = "late"  # a deadline is part of the test
            else:
                base = "pass"
        else:
            base = "inconclusive"
    if base not in ("pass", "fail") and today > by:
        return "expired"
    return "open" if base == "late" else base


def compute(st, results, today):
    """{id: {assumption, status, counted, stale}} for the latest registration, and the
    computed verdict."""
    current = st["registrations"][-1]
    regs = {r["sha256"]: r for r in st["registrations"]}
    rows = {}
    for aid in sorted(current["assumptions"], key=lambda x: int(x[1:])):
        rows[aid] = {"assumption": current["assumptions"][aid], "counted": [], "stale": []}
    for r in results:
        row = rows.get(r.get("assumption"))
        if row is None:
            continue
        (row["stale"] if _is_stale(r, current, regs) else row["counted"]).append(r)
    for row in rows.values():
        row["counted"].sort(key=lambda r: (r["observed"], r["_seq"]))
        row["status"] = assumption_status(row["assumption"], row["counted"], today)
    crit = [row["status"] for row in rows.values() if row["assumption"]["critical"]]
    if any(s == "fail" for s in crit):
        v = "KILL"
    elif crit and all(s == "pass" for s in crit):
        v = "GO"
    elif any(s == "expired" for s in crit):
        v = "STALLED"
    else:
        v = "PENDING"
    return rows, v


def _fmt(v):
    return "%g" % v if isinstance(v, float) else str(v)


# -- Commands ---------------------------------------------------------------------
def _brief_rel(slug):
    return "%s/%s.md" % (BRIEFS_DIR, slug)


def _slug_ok(slug):
    if isinstance(slug, str) and SLUG_RE.match(slug) and len(slug) <= 64:
        return True
    print("STOP: slug %s must be kebab-case (a-z, 0-9 and single dashes, at most 64)"
          % code(slug))
    return False


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _read_brief_bytes(root, slug):
    with open(os.path.join(root, BRIEFS_DIR, slug + ".md"), "rb") as f:
        return f.read()


def _print_lint(parsed):
    for p in parsed["problems"]:
        print("PROBLEM: " + p)
    for n in parsed["notes"]:
        print("NOTE: " + n)
    for e in parsed["experiments"]:
        if e["outward"]:
            print("NOTE: experiment %d is [outward]: a human runs it, never the agent" % e["n"])
    if not parsed["problems"]:
        print("LINT: ok")


def cmd_new(a):
    if not _slug_ok(a.slug):
        return 2
    title = one_line(a.title)
    if not title or any(is_hidden_char(c) for c in title):
        print("STOP: the title must be non-empty plain text")
        return 2
    rel = _brief_rel(a.slug)
    path = os.path.join(a.root, *rel.split("/"))
    try:
        with run_lock(a.root):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            try:
                with open(path, "x", encoding="utf-8") as f:
                    f.write(TEMPLATE.format(title=title))
            except FileExistsError:
                print("STOP: %s exists; pick another slug" % rel)
                return 2
            _log(a.root, "new", slug=a.slug)
    except NotIgnored as e:
        return _not_ignored(e)
    except Locked:
        return _locked()
    print("IDEA: %s %s" % (a.slug, code(title)))
    print("NEXT: draft the brief with the human, then run lint %s" % rel)
    return 0


def cmd_lint(a):
    path = a.brief if os.path.isabs(a.brief) else os.path.join(a.root, a.brief)
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8")
    except (OSError, ValueError) as e:
        print("STOP: cannot read the brief: %s" % code(e))
        return 2
    parsed = parse_brief(text)
    _print_lint(parsed)
    return 2 if parsed["problems"] else 0


def _registered_assumption(a):
    return {k: a[k] for k in ("id", "critical", "category", "statement") + FIELDS}


def cmd_register(a):
    if not _name_ok(a.by):
        print("STOP: --by needs the name of the human who registers the brief")
        return 2
    path = a.brief if os.path.isabs(a.brief) else os.path.join(a.root, a.brief)
    base = os.path.basename(path)
    slug = base[:-3] if base.endswith(".md") else ""
    expect = os.path.join(a.root, BRIEFS_DIR, slug + ".md")
    if not (SLUG_RE.match(slug) and os.path.realpath(path) == os.path.realpath(expect)):
        print("STOP: register takes %s/<slug>.md under --root" % BRIEFS_DIR)
        return 2
    try:
        data = _read_brief_bytes(a.root, slug)
        parsed = parse_brief(data.decode("utf-8"))
    except (OSError, ValueError) as e:
        print("STOP: cannot read the brief: %s" % code(e))
        return 2
    if parsed["problems"]:
        _print_lint(parsed)
        print("NEXT: fix the brief, then register it again")
        return 2
    sha = _sha_bytes(data)
    try:
        with run_lock(a.root):
            st = load_state(a.root, slug) or {"version": 1, "slug": slug, "registrations": [],
                                              "decisions": [], "closed": False}
            if st.get("closed"):
                print("STOP: %s was closed by a pivot decision; start a new brief" % slug)
                return 2
            if st["registrations"] and st["registrations"][-1]["sha256"] == sha:
                print("REGISTERED: %s %s (no change since the last registration)"
                      % (slug, sha[:12]))
                return 0
            snap_rel = "%s/%s/registered/%s.md" % (IDEAS_DIR, slug, sha)
            _write_atomic(a.root, snap_rel, data)
            st["registrations"].append({
                "sha256": sha, "snapshot": snap_rel, "by": a.by.strip(), "at": _now(),
                "assumptions": {x["id"]: _registered_assumption(x)
                                for x in parsed["assumptions"]}})
            save_state(a.root, slug, st)
            _log(a.root, "register", slug=slug, sha256=sha, by=a.by.strip())
    except NotIgnored as e:
        return _not_ignored(e)
    except Locked:
        return _locked()
    except StateError as e:
        print("STOP: %s" % code(e))
        return 2
    _print_lint(parsed)
    print("REGISTERED: %s %s by %s" % (slug, sha[:12], code(a.by.strip())))
    print("NEXT: the human runs the experiments; record each result with record %s <A-id> ..."
          % slug)
    return 0


def _read_evidence(path):
    """(text, truncated). At most EVIDENCE_CAP bytes, cut on a UTF-8 boundary."""
    with open(path, "rb") as f:
        data = f.read(EVIDENCE_CAP + 1)
    truncated = len(data) > EVIDENCE_CAP
    data = data[:EVIDENCE_CAP]
    if truncated:
        for cut in range(4):
            try:
                return data[:len(data) - cut].decode("utf-8"), True
            except UnicodeDecodeError:
                continue
    return data.decode("utf-8", errors="replace"), truncated


def _need_state(root, slug):
    """(state, rc). rc is None when the state is usable."""
    try:
        st = load_state(root, slug)
    except StateError as e:
        print("STOP: %s" % code(e))
        return None, 2
    if st is None:
        print("STOP: %s is not registered" % slug)
        print("NEXT: lint and register %s first" % _brief_rel(slug))
        return None, 2
    return st, None


def _brief_changed(root, slug, st):
    try:
        return _sha_bytes(_read_brief_bytes(root, slug)) != st["registrations"][-1]["sha256"]
    except OSError:
        return True


def cmd_record(a):
    if not _slug_ok(a.slug):
        return 2
    if not AID_RE.match(a.aid):
        print("STOP: %s is not an assumption id (A1, A2, …)" % code(a.aid))
        return 2
    try:
        value = float(a.value)
    except ValueError:
        value = float("nan")
    if not math.isfinite(value):
        print("STOP: --value must be a finite number, not %s" % code(a.value))
        return 2
    value = int(value) if value.is_integer() and abs(value) < 1e15 else value
    observed = _date(a.observed)
    if observed is None or observed > a.today:
        print("STOP: --observed must be a real date (YYYY-MM-DD) no later than today")
        return 2
    if not _name_ok(a.by):
        print("STOP: --by needs a name")
        return 2
    if a.kind == "experiment" and _is_agent(a.by):
        print("STOP: an experiment result needs the name of the human who reported it; "
              "the agent records only desk results under its own name")
        return 2
    source = one_line(a.source)
    if not source:
        print("STOP: --source must say where the result came from")
        return 2
    evidence, truncated = "", False
    if a.evidence_file:
        try:
            evidence, truncated = _read_evidence(a.evidence_file)
        except OSError as e:
            print("STOP: cannot read the evidence file: %s" % code(e))
            return 2
    try:
        with run_lock(a.root):
            st, rc = _need_state(a.root, a.slug)
            if rc is not None:
                return rc
            if st.get("closed"):
                print("STOP: %s was closed by a pivot decision" % a.slug)
                return 2
            reg = st["registrations"][-1]
            if a.aid not in reg["assumptions"]:
                print("STOP: the registered brief has no assumption %s" % a.aid)
                return 2
            _append(a.root, "%s/%s/%s" % (IDEAS_DIR, a.slug, RESULTS_FILE), {
                "assumption": a.aid, "value": value, "kind": a.kind, "source": source,
                "observed": a.observed, "by": a.by.strip(), "evidence": evidence,
                "truncated": truncated, "registration": reg["sha256"],
                "recorded_at": _now()})
            _log(a.root, "record", slug=a.slug, assumption=a.aid, kind=a.kind,
                 by=a.by.strip())
            rows, _ = compute(st, load_results(a.root, a.slug), a.today)
    except NotIgnored as e:
        return _not_ignored(e)
    except Locked:
        return _locked()
    except StateError as e:
        print("STOP: %s" % code(e))
        return 2
    print("RESULT: %s %s %s %s by %s source %s%s" % (
        a.aid, _fmt(value), a.kind, a.observed, code(a.by.strip()), code(source),
        " (evidence truncated at %d bytes)" % EVIDENCE_CAP if truncated else ""))
    print("ASSUMPTION: %s %s %s" % (a.aid, rows[a.aid]["status"], _fmt(value)))
    if _brief_changed(a.root, a.slug, st):
        print("NOTE: %s changed since its last registration; this result is bound to the "
              "registered version" % _brief_rel(a.slug))
    return 0


def _decision_line(d):
    return "DECISION: %s by %s computed %s%s reason %s" % (
        d["verdict"], code(d["by"]), d["computed"], " override" if d["override"] else "",
        code(d["reason"]))


def cmd_status(a):
    """Read-only: it never creates a file or a directory."""
    if not _slug_ok(a.slug):
        return 2
    st, rc = _need_state(a.root, a.slug)
    if rc is not None:
        return rc
    try:
        results = load_results(a.root, a.slug)
    except StateError as e:
        print("STOP: %s" % code(e))
        return 2
    rows, verdict = compute(st, results, a.today)
    reg = st["registrations"][-1]
    snap = _read_snapshot(a.root, reg)
    title = snap["title"] if snap else ""
    print("IDEA: %s %s" % (a.slug, code(title)))
    print("REGISTERED: %s by %s at %s" % (reg["sha256"][:12], code(reg["by"]), reg["at"]))
    for aid, row in rows.items():
        latest = _fmt(row["counted"][-1]["value"]) if row["counted"] else "-"
        print("ASSUMPTION: %s %s %s" % (aid, row["status"], latest))
        for r, stale in [(r, False) for r in row["counted"]] + [(r, True) for r in row["stale"]]:
            print("RESULT: %s %s %s %s by %s source %s%s%s" % (
                aid, _fmt(r["value"]), r["kind"], r["observed"], code(r.get("by", "")),
                code(r.get("source", "")), " stale (not counted)" if stale else "",
                (" evidence " + code(_clip(r["evidence"]))) if r.get("evidence") else ""))
    for e in (snap["experiments"] if snap else []):
        print("EXPERIMENT: %d %s %s" % (e["n"], "outward" if e["outward"] else "inward",
                                        ",".join(e["ids"])))
    print("VERDICT: %s" % verdict)
    if _brief_changed(a.root, a.slug, st):
        print("NOTE: %s changed since its last registration; register it again"
              % _brief_rel(a.slug))
    if st["decisions"]:
        print(_decision_line(st["decisions"][-1]))
    if st.get("closed"):
        print("NEXT: closed by a pivot; start a new brief")
    elif st["decisions"] and st["decisions"][-1]["verdict"] == "go":
        print("NEXT: the human runs handoff %s --by NAME" % a.slug)
    else:
        print("NEXT: record more results, or the human decides: decide %s --verdict "
              "go|kill|pivot --by NAME --reason TEXT" % a.slug)
    return 0


def cmd_decide(a):
    if not _slug_ok(a.slug):
        return 2
    if not _name_ok(a.by):
        print("STOP: --by needs the name of the human who decides")
        return 2
    reason = one_line(a.reason)
    if not reason:
        print("STOP: --reason must say why")
        return 2
    try:
        with run_lock(a.root):
            st, rc = _need_state(a.root, a.slug)
            if rc is not None:
                return rc
            if st.get("closed"):
                print("STOP: %s was closed by a pivot decision" % a.slug)
                return 2
            _, computed = compute(st, load_results(a.root, a.slug), a.today)
            override = ((a.verdict == "go" and computed != "GO")
                        or (a.verdict == "kill" and computed == "GO"))
            d = {"verdict": a.verdict, "by": a.by.strip(), "reason": reason,
                 "computed": computed, "override": override, "at": _now(),
                 "registration": st["registrations"][-1]["sha256"]}
            st["decisions"].append(d)
            if a.verdict == "pivot":
                st["closed"] = True
            save_state(a.root, a.slug, st)
            _log(a.root, "decide", slug=a.slug, verdict=a.verdict, by=d["by"],
                 computed=computed, override=override)
    except NotIgnored as e:
        return _not_ignored(e)
    except Locked:
        return _locked()
    except StateError as e:
        print("STOP: %s" % code(e))
        return 2
    print(_decision_line(d))
    if override:
        print("NOTE: this decision overrides the computed verdict %s; the handoff says so"
              % computed)
    if a.verdict == "go":
        print("NEXT: the human runs handoff %s --by NAME" % a.slug)
    elif a.verdict == "pivot":
        print("NEXT: start a new brief that names %s in its prose" % a.slug)
    return 0


def _read_snapshot(root, reg):
    """The parsed registered snapshot, or None when it is missing."""
    try:
        with open(os.path.join(root, *reg["snapshot"].split("/")), "rb") as f:
            return parse_brief(f.read().decode("utf-8"))
    except (OSError, ValueError, KeyError):
        return None


def build_payload(slug, snap, rows, verdict, decision):
    """The idea-brief/v1 payload (schemas/idea-brief.v1.json)."""
    sec = snap["sections"]
    assumptions = []
    for aid, row in rows.items():
        x = row["assumption"]
        assumptions.append({
            "id": aid, "critical": x["critical"], "category": x["category"],
            "statement": x["statement"], "test": x["test"], "metric": x["metric"],
            "pass": x["pass"], "kill": x["kill"], "by": x["by"], "status": row["status"],
            "results": [{"value": r["value"], "kind": r["kind"], "source": r.get("source", ""),
                         "observed": r["observed"], "by": r.get("by", ""),
                         "evidence": r.get("evidence") or "",
                         "truncated": bool(r.get("truncated")), "claim": "claimed"}
                        for r in row["counted"]]})
    return {
        "slug": slug, "title": snap["title"] or "",
        "problem": sec.get("problem", ""), "target_user": sec.get("target user", ""),
        "value_proposition": sec.get("value proposition", ""),
        "out_of_scope": sec.get("out of scope", ""),
        "computed_verdict": verdict,
        "decision": {k: decision[k] for k in ("verdict", "by", "reason", "override", "at")},
        "assumptions": assumptions,
        "open_risks": [x["id"] for x in assumptions if x["status"] != "pass"],
    }


def cmd_handoff(a):
    if not _slug_ok(a.slug):
        return 2
    if not _name_ok(a.by):
        print("STOP: --by needs the name of the human who hands the idea off")
        return 2
    try:
        with run_lock(a.root):
            st, rc = _need_state(a.root, a.slug)
            if rc is not None:
                return rc
            last = st["decisions"][-1] if st["decisions"] else None
            if not last or last["verdict"] != "go":
                print("STOP: handoff needs a go decision; the latest decision is %s"
                      % (last["verdict"] if last else "none"))
                print("NEXT: the human decides: decide %s --verdict go|kill|pivot --by NAME "
                      "--reason TEXT" % a.slug)
                return 2
            reg = st["registrations"][-1]
            if _brief_changed(a.root, a.slug, st):
                print("STOP: %s changed since its last registration" % _brief_rel(a.slug))
                print("NEXT: register %s --by NAME, then the human decides again"
                      % _brief_rel(a.slug))
                return 2
            if last.get("registration") != reg["sha256"]:
                print("STOP: the go decision was made before the latest registration")
                print("NEXT: the human decides again: decide %s --verdict go|kill|pivot "
                      "--by NAME --reason TEXT" % a.slug)
                return 2
            snap_rel = reg["snapshot"]
            snap_path = os.path.join(a.root, *snap_rel.split("/"))
            try:
                ok = CC.sha256_file(snap_path) == reg["sha256"]
            except OSError:
                ok = False
            snap = _read_snapshot(a.root, reg) if ok else None
            if snap is None:
                print("STOP: the registered snapshot %s is missing or changed" % snap_rel)
                print("NEXT: register %s --by NAME again" % _brief_rel(a.slug))
                return 2
            rows, verdict = compute(st, load_results(a.root, a.slug), a.today)
            if verdict != last["computed"]:
                # The override flag describes the verdict the human saw. A verdict that
                # moved since then (new results, a passed deadline) needs a new decision.
                print("STOP: the computed verdict changed since the go decision (%s, now %s)"
                      % (last["computed"], verdict))
                print("NEXT: the human decides again: decide %s --verdict go|kill|pivot "
                      "--by NAME --reason TEXT" % a.slug)
                return 2
            payload = build_payload(a.slug, snap, rows, verdict, last)
            stmt = CC.build_statement(IDEA_KIND, SKILL_NAME, VERSION, a.root, [snap_rel],
                                      payload)
            viol = CC.check_statement(stmt)
            if viol:
                print("STOP: the idea-brief envelope would be invalid (%s)"
                      % ", ".join(sorted({"C%d" % n for n, _ in viol})))
                return 2
            env_rel = "%s/%s.json" % (ENVELOPES_DIR, stmt["predicate"]["id"])
            try:
                _write_atomic(a.root, env_rel, json.dumps(stmt, indent=2, sort_keys=True)
                              + "\n", exclusive=True)
            except FileExistsError:
                print("STOP: an envelope already exists at %s; run handoff again" % env_rel)
                return 2
            st.setdefault("handoffs", []).append({"envelope": env_rel, "by": a.by.strip(),
                                                  "at": _now()})
            save_state(a.root, a.slug, st)
            _log(a.root, "handoff", slug=a.slug, by=a.by.strip(), envelope=env_rel)
    except NotIgnored as e:
        return _not_ignored(e)
    except Locked:
        return _locked()
    except StateError as e:
        print("STOP: %s" % code(e))
        return 2
    print("VERDICT: %s" % verdict)
    print(_decision_line(last))
    if last["override"]:
        print("NOTE: this go overrides the computed verdict %s; the envelope says so" % verdict)
    if payload["open_risks"]:
        print("NOTE: open risks: %s" % ", ".join(payload["open_risks"]))
    print("ENVELOPE: %s" % env_rel)
    print("NEXT: run spec-first-planning with %s" % env_rel)
    return 0


def cmd_list(a):
    """Read-only: it never creates a file or a directory."""
    slugs = set()
    with contextlib.suppress(OSError):
        for n in os.listdir(os.path.join(a.root, BRIEFS_DIR)):
            if n.endswith(".md") and SLUG_RE.match(n[:-3]):
                slugs.add(n[:-3])
    with contextlib.suppress(OSError):
        for n in os.listdir(_ideas_dir(a.root)):
            if SLUG_RE.match(n) and os.path.isfile(os.path.join(a.root, *_state_rel(n).split("/"))):
                slugs.add(n)
    for slug in sorted(slugs):
        title = ""
        try:
            title = parse_brief(_read_brief_bytes(a.root, slug).decode("utf-8"))["title"] or ""
        except (OSError, ValueError):
            pass
        try:
            st = load_state(a.root, slug)
            results = load_results(a.root, slug) if st else []
        except StateError:
            print("IDEA: %s unreadable decision=none %s" % (slug, code(title)))
            continue
        if st is None:
            print("IDEA: %s unregistered decision=none %s" % (slug, code(title)))
            continue
        _, verdict = compute(st, results, a.today)
        dec = st["decisions"][-1]["verdict"] if st["decisions"] else "none"
        print("IDEA: %s %s decision=%s%s %s" % (slug, verdict, dec,
                                                " closed" if st.get("closed") else "",
                                                code(title)))
    return 0


def _not_ignored(e):
    print("STOP: %s cannot be shown to be git-ignored (%s); results can quote interviews "
          "and sign-up data" % (IDEAS_DIR, code(e)))
    print("NEXT: make %s a plain directory whose .gitignore holds `*`" % IDEAS_DIR)
    return 2


def _locked():
    print("STOP: the ideas lock is held by another command")
    return 3


def _today_arg(s):
    d = _date(s)
    if d is None:
        raise argparse.ArgumentTypeError("--today must be a real YYYY-MM-DD date")
    return d


def main(argv=None):
    p = argparse.ArgumentParser(prog="ideas.py")
    p.add_argument("--root", default=".")
    p.add_argument("--today", type=_today_arg, default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("new"); s.add_argument("slug"); s.add_argument("--title", required=True)
    s = sub.add_parser("lint"); s.add_argument("brief")
    s = sub.add_parser("register"); s.add_argument("brief"); s.add_argument("--by", required=True)
    s = sub.add_parser("record"); s.add_argument("slug"); s.add_argument("aid")
    s.add_argument("--value", required=True); s.add_argument("--kind", required=True, choices=KINDS)
    s.add_argument("--source", required=True); s.add_argument("--observed", required=True)
    s.add_argument("--by", required=True); s.add_argument("--evidence-file")
    s = sub.add_parser("status"); s.add_argument("slug")
    s = sub.add_parser("decide"); s.add_argument("slug")
    s.add_argument("--verdict", required=True, choices=DECISIONS)
    s.add_argument("--by", required=True); s.add_argument("--reason", required=True)
    s = sub.add_parser("handoff"); s.add_argument("slug"); s.add_argument("--by", required=True)
    sub.add_parser("list")
    try:
        a = p.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    a.root = os.path.abspath(a.root)
    if a.today is None:
        a.today = datetime.date.today()
    return {"new": cmd_new, "lint": cmd_lint, "register": cmd_register, "record": cmd_record,
            "status": cmd_status, "decide": cmd_decide, "handoff": cmd_handoff,
            "list": cmd_list}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
