#!/usr/bin/env python3
"""ledger: the project's money ledger and a refuse-only pre-spend check (factory-conductor).

The check can refuse a proposed spend that would break the human's budget. It can never
approve one: every spend still needs the human's yes, because `spend` is an action class that
no autonomy grant covers. conductor.py never imports this tool and never reads its state.

The tool MUST NOT open a network connection, run a subprocess or call a model. Stdlib only,
Python 3.10+, POSIX. Money is integers in minor units (cents); floats are never used.

Usage: ledger.py [--root R] [--today YYYY-MM-DD] <command> ...
  (--today is for tests only; outside the test suite it is refused, exit 2)
  lint [BUDGET]                     check the budget file (default budget/budget.md)
  register --by NAME [--reason T]   freeze the budget; again after an edit needs --reason;
                                    an `agent:` name is refused
  check --id ID --category C --amount A --currency CUR --what TEXT
        [--month YYYY-MM | --recurring monthly --start YYYY-MM [--end YYYY-MM]]
                                    refuse a proposal that breaks a cap, or reserve it
  record --id ID --amount A --currency CUR --month YYYY-MM --by NAME
                                    the human records a spend that happened
  withdraw --id ID --by NAME --reason TEXT
                                    the human withdraws a proposal and frees its reservations
  status [--month YYYY-MM]          caps, recorded spend, reservations and headroom

Exits: 0 NOT_REFUSED or a command that succeeded; 2 bad input or a STOP (an agent name, a
corrupt state file); 3 REFUSED. Machine lines: LINT:, PROBLEM:, NOTE:, RAISED:, REGISTERED:,
REFUSED:, NOT_REFUSED:, RECORDED:, WITHDRAWN:, STATUS:, CATEGORY:, TOTAL:, STOP:, NEXT:.
A pass prints `NOT_REFUSED: <id> — the human's approval is still required` and writes no
envelope, so nothing can read it as a grant.
"""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import unicodedata

SKILL_NAME = "factory-conductor"
LEDGER_VERSION = "1.5.2"
# --today moves the clock that decides the current month, so it is honoured only when this
# variable is "1". Only the tests and the eval set it; no STOP line names it.
TEST_CLOCK_ENV = "LEDGER_TEST_CLOCK"

BUDGET_PATH = "budget/budget.md"
LEDGER_DIR = ".skill-contract/ledger"
STATE_FILE = "ledger.json"
LOG_FILE = "log.jsonl"
LOCK_FILE = ".lock"

TOTAL = "total"
NAME_CAP = 64
TEXT_CAP = 200
AMOUNT_DIGITS = 12           # digits before the point
MINOR_CAP = 10 ** (AMOUNT_DIGITS + 2)   # every stored amount is below this
OPEN_ENDED_MONTHS = 12
SPAN_CAP = 120               # a recurring proposal covers at most this many months
MAX_MONTH_INDEX = 9999 * 12 + 11

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\Z")
# ASCII digits only: `\d` matches any Unicode digit (fullwidth, Arabic-Indic, ...), and int()
# accepts them, so a fullwidth amount would parse. int() also accepts "1_000"; the regex runs
# first, so it never sees one.
AMOUNT_RE = re.compile(r"^([0-9]{1,%d})(?:\.([0-9]{1,2}))?\Z" % AMOUNT_DIGITS)
CURRENCY_RE = re.compile(r"^[A-Z]{3}\Z")
MONTH_RE = re.compile(r"^([0-9]{4})-(0[1-9]|1[0-2])\Z")
DAY_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
CURRENCY_LINE_RE = re.compile(r"^\s*(?:[-*]\s+)?currency:\s*(.*?)\s*\Z")
# Words that read as approval. An id or a category holding one as a kebab part is refused,
# and so is a --by name holding one as a word, so no output can carry one (the pass line is
# NOT_REFUSED, never one of these).
APPROVAL_WORDS = frozenset({"ok", "okay", "yes", "approve", "approved", "accept", "accepted",
                            "allow", "allowed", "grant", "granted", "pass", "passed"})


# -- Untrusted text ---------------------------------------------------------------
# one_line(), is_hidden_char(), visible() and code() are copied from growth-review's
# assets/growth.py (which copies idea-validation's), because skills cannot import each other.
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
    """Untrusted text as one inline code span."""
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
    """A name (--by, --reason, --what) is non-empty after stripping and holds no hidden
    characters."""
    return (isinstance(name, str) and name.strip() != "" and len(name) <= TEXT_CAP
            and not any(is_hidden_char(c) for c in name))


# -- Agent names ------------------------------------------------------------------
# The block from here to the end of _is_agent() is byte-identical in growth-review's
# assets/growth.py and idea-validation's assets/ideas.py, because skills cannot import each
# other; test_ledger.py pins the three copies together.
# Latin lookalikes of the letters in "agent" that NFKD does not fold: Cyrillic, Greek, the
# IPA script g and alpha, and the small capitals.
_CONFUSABLES = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p",
                              "с": "c", "х": "x", "і": "i", "А": "a",
                              "Е": "e", "О": "o", "Р": "p", "С": "c",
                              "Х": "x", "І": "i", "α": "a", "ε": "e",
                              "ο": "o", "Α": "a", "Ε": "e", "Ο": "o",
                              "Ν": "n", "Τ": "t", "т": "t", "н": "n",
                              "Т": "t", "Н": "n", "ɡ": "g", "ɑ": "a",
                              "ᴀ": "a", "ɢ": "g", "ᴇ": "e", "ɴ": "n",
                              "ᴛ": "t"})
# Letters and digits stay; every other character (punctuation, a symbol, a modifier letter
# such as U+02D0, whitespace) becomes one space, so it counts as a separator.
_WORD_CATEGORIES = frozenset({"Lu", "Ll", "Lt", "Lo", "Nd"})
# "agent" at the start, its letters apart or not, and no letter after it (a digit or a
# space may follow: "agent2" and "agent x" are refused).
_AGENT_RE = re.compile(r"^ *a *g *e *n *t(?![^\W\d_])")


def _fold(name):
    """name as lowercase Latin letters, digits and spaces: NFKD, combining marks dropped,
    the lookalikes above folded, casefolded, and every other character a space."""
    s = "".join(c for c in unicodedata.normalize("NFKD", name)
                if unicodedata.category(c) != "Mn")
    s = unicodedata.normalize("NFKC", s.translate(_CONFUSABLES).casefold())
    return "".join(c if unicodedata.category(c) in _WORD_CATEGORIES else " " for c in s)


def _is_agent(name):
    """True for a name that starts with the word "agent" in any form: `agent:x`, a bare
    `agent`, any separator (a space, `.`, `;`, `|`, a ratio or modifier-letter colon, a dash
    or a middle dot), letters apart (`a gent:x`), fullwidth, Cyrillic, Greek or
    small-capital letters. A human name that starts with that word ("Agent Smith") is
    refused too: the check fails closed. "Agentha", "agentur" and "Reagent" pass."""
    folded = _fold(name)
    return bool(_AGENT_RE.match(folded))


def _approval_word(name):
    """True when name holds an approval word as a word, after the agent-name fold."""
    return bool(set(_fold(name).split()) & APPROVAL_WORDS)


def _kebab_ok(name):
    return (isinstance(name, str) and len(name) <= NAME_CAP and bool(NAME_RE.match(name))
            and not (set(name.split("-")) & APPROVAL_WORDS))


# -- Money and months ---------------------------------------------------------------
def parse_amount(s):
    """Minor units (int) for a decimal string in major units with at most 2 decimals and at
    most 12 digits before the point, else None."""
    if not isinstance(s, str):
        return None
    m = AMOUNT_RE.match(s)
    if not m:
        return None
    frac = (m.group(2) or "").ljust(2, "0")
    return int(m.group(1)) * 100 + int(frac)


def money(minor):
    """Minor units as a decimal string: 1999 -> 19.99, -5 -> -0.05."""
    sign = "-" if minor < 0 else ""
    return "%s%d.%02d" % (sign, abs(minor) // 100, abs(minor) % 100)


def month_index(label):
    """y*12 + m-1 for a YYYY-MM label, else None. Plain integers: datetime raises past
    year 9999."""
    if not isinstance(label, str):
        return None
    m = MONTH_RE.match(label)
    if not m:
        return None
    return int(m.group(1)) * 12 + int(m.group(2)) - 1


def month_label(i):
    return "%04d-%02d" % (i // 12, i % 12 + 1)


def current_month(today):
    return today.year * 12 + today.month - 1


def _is_int(v, lo=0, hi=MINOR_CAP):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v < hi


# -- The budget (budget/budget.md) ------------------------------------------------
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


def parse_budget(text):
    """{currency, caps: {category: minor}, total: minor, problems: [...]}. A budget with any
    problem is not registered."""
    problems = []
    cur = [(n, m.group(1)) for n, line in enumerate(text.splitlines(), 1)
           for m in [CURRENCY_LINE_RE.match(line)] if m]
    currency = None
    if not cur:
        problems.append("no `currency:` line (an ISO 4217 code such as USD)")
    elif len(cur) > 1:
        problems.append("lines %s: more than one currency line; keep one"
                        % ", ".join(str(n) for n, _ in cur))
    elif not CURRENCY_RE.match(cur[0][1]):
        problems.append("line %d: currency %s must be three capital letters A-Z (ISO 4217)"
                        % (cur[0][0], code(cur[0][1])))
    else:
        currency = cur[0][1]
    tables = [t for t in _tables(text) if "category" in [h.casefold() for h in t[1]]]
    if not tables:
        problems.append("no budget table: a Markdown table whose header names the columns "
                        "category and monthly_cap")
        return {"currency": None, "caps": {}, "total": None, "problems": problems}
    if len(tables) > 1:
        problems.append("lines %s: more than one budget table; keep one"
                        % ", ".join(str(t[0]) for t in tables))
    start, header, rows = tables[0]
    cols = [h.casefold() for h in header]
    if sorted(cols) != ["category", "monthly_cap"]:
        problems.append("line %d: the columns must be exactly category and monthly_cap"
                        % start)
        return {"currency": None, "caps": {}, "total": None, "problems": problems}
    caps, total, totals = {}, None, 0
    for n, cells in rows:
        if len(cells) != 2:
            problems.append("line %d: %d cells, the header has 2" % (n, len(cells)))
            continue
        r = dict(zip(cols, cells))
        name, raw = r["category"], r["monthly_cap"]
        amount = parse_amount(raw)
        if amount is None:
            problems.append("line %d: monthly_cap %s must be a decimal amount in major units: "
                            "ASCII digits, at most %d before the point and at most 2 after it"
                            % (n, code(raw), AMOUNT_DIGITS))
        if name == TOTAL:
            totals += 1
            if totals > 1:
                problems.append("line %d: the total row appears twice" % n)
            elif amount is not None:
                total = amount
            continue
        if not _kebab_ok(name):
            problems.append("line %d: category %s must be kebab-case, at most %d characters, "
                            "and hold no word that reads as approval (references/ledger.md "
                            "lists them)" % (n, code(name), NAME_CAP))
            continue
        if name in caps:
            problems.append("line %d: category %s appears twice" % (n, name))
            continue
        if amount is not None:
            caps[name] = amount
    if not totals:
        problems.append("no `total` row: the budget needs one row named total for the monthly "
                        "total cap")
    if not caps and not problems:
        problems.append("the budget table has no category rows")
    if problems:
        return {"currency": None, "caps": {}, "total": None, "problems": problems}
    return {"currency": currency, "caps": caps, "total": total, "problems": []}


def budget_sha(currency, caps, total):
    """sha256 of the parsed budget only (currency and caps), so an edit to the prose never
    makes it stale and any cap or currency edit does."""
    blob = json.dumps({"currency": currency, "caps": caps, "total": total}, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# -- Storage: ignore check, lock, atomic writes -----------------------------------
LOCK_TIMEOUT = 900  # read at call time, so tests can shorten it
LOCK_NOTICE_AFTER = 2.0


class Locked(Exception):
    """The ledger lock is still held after the timeout."""


class NotIgnored(Exception):
    """The state directory cannot be shown to be git-ignored."""


class StateError(Exception):
    """A state file is unreadable or malformed."""


def _ledger_dir(root):
    return os.path.join(root, *LEDGER_DIR.split("/"))


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
    """Make .skill-contract/ledger/ and prove it is git-ignored before any other write.
    Mirrors growth-review's _ensure_dir(): the tool runs no subprocess, so it cannot ask
    `git check-ignore`; the directory carries its own `.gitignore` holding `*`, which git
    honours whatever the root .gitignore says. A symlinked directory, a `.gitignore` that is
    not a plain file, or one with a `!` line raises NotIgnored."""
    top = os.path.join(root, ".skill-contract")
    d = _ledger_dir(root)
    if os.path.islink(top) or os.path.islink(d):
        raise NotIgnored("%s is a symlink" % LEDGER_DIR)
    gi = os.path.join(d, ".gitignore")
    if os.path.isdir(d) and _gitignore_ok(gi):
        return d
    if _gitignore_negates(gi):
        raise NotIgnored("%s/.gitignore has a `!` line that un-ignores files" % LEDGER_DIR)
    if os.path.lexists(gi) and not os.path.isfile(gi) or os.path.islink(gi):
        raise NotIgnored("%s/.gitignore is not a regular file" % LEDGER_DIR)
    try:
        os.makedirs(d, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(gi)
        with open(gi, "w", encoding="utf-8") as f:
            f.write("# written by factory-conductor's ledger: spend records are business "
                    "data, never commit them\n*\n")
    except OSError as e:
        raise NotIgnored(one_line(e))
    if not _gitignore_ok(gi):
        raise NotIgnored("cannot verify %s/.gitignore" % LEDGER_DIR)
    return d


_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _refuse_symlink(path):
    """StateError when path is a symlink: a symlinked state, log or lock file would make the
    tool read or write outside .skill-contract/ledger/."""
    if os.path.islink(path):
        raise StateError("%s is a symlink; the tool reads and writes only plain files in %s"
                         % (os.path.basename(path), LEDGER_DIR))


def _open_nofollow(path, flags, mode=0o600):
    """os.open that refuses a symlink twice: an lstat check, then O_NOFOLLOW where the
    platform has it (which also closes the race between the two)."""
    _refuse_symlink(path)
    try:
        return os.open(path, flags | _O_NOFOLLOW, mode)
    except OSError as e:
        if os.path.islink(path):
            raise StateError("%s is a symlink; the tool reads and writes only plain files "
                             "in %s" % (os.path.basename(path), LEDGER_DIR))
        raise StateError("cannot open %s: %s" % (os.path.basename(path), one_line(e)))


@contextlib.contextmanager
def run_lock(root, timeout=None):
    """An exclusive flock on .skill-contract/ledger/.lock (copied from growth.py run_lock()).
    The ignore check runs first. Every command that writes holds it from its first read to
    its last write, so two checks cannot both pass on the same headroom."""
    d = _ensure_dir(root)
    f = os.fdopen(_open_nofollow(os.path.join(d, LOCK_FILE), os.O_RDWR | os.O_CREAT), "r+")
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
                    sys.stderr.write("waiting for the ledger lock held by another command…\n")
                    sys.stderr.flush()
                    told = True
                time.sleep(0.1)
        yield
    finally:
        f.close()


def _check_state_files(root):
    """Refuse before the first write when any state file is a symlink, so a refusal never
    leaves ledger.json and log.jsonl out of step."""
    d = _ledger_dir(root)
    for name in (STATE_FILE, LOG_FILE, LOCK_FILE):
        _refuse_symlink(os.path.join(d, name))


def _read_text(path):
    """The text of a state file, or None when it does not exist. A symlink is refused."""
    if not os.path.lexists(path):
        return None
    fd = _open_nofollow(path, os.O_RDONLY)
    with os.fdopen(fd, encoding="utf-8") as f:
        return f.read()


def _fsync_dir(d):
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_state(root, state):
    """ledger.json, atomically: a temp file in the same directory, fsync, os.replace."""
    d = _ledger_dir(root)
    path = os.path.join(d, STATE_FILE)
    _refuse_symlink(path)
    tmp = path + ".tmp.%d" % os.getpid()
    fd = _open_nofollow(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(state, indent=1, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)
    _fsync_dir(d)


def state_sha(st):
    """sha256 of the state as canonical JSON. Every log event carries the digest of the state
    it wrote, so a hand edit of ledger.json (a dropped reservation, a swapped cap) shows."""
    blob = json.dumps(st, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _log(root, event, st, **fields):
    path = os.path.join(_ledger_dir(root), LOG_FILE)
    fd = _open_nofollow(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(fields, event=event, seq=st["seq"], at=_now(),
                                state_sha256=state_sha(st)), sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


# -- State validation -------------------------------------------------------------
def _text_ok(v, allow_none=False):
    return (v is None and allow_none) or (isinstance(v, str) and v.strip() != "")


def _check_registration(i, reg):
    if not isinstance(reg, dict):
        raise ValueError("registration %d is not an object" % i)
    for k in ("sha256", "by", "at"):
        if not _text_ok(reg.get(k)):
            raise ValueError("registration %d has no %s" % (i, k))
    if not _text_ok(reg.get("reason"), allow_none=True):
        raise ValueError("registration %d has a reason that is not text" % i)
    if not (isinstance(reg.get("currency"), str) and CURRENCY_RE.match(reg["currency"])):
        raise ValueError("registration %d has no currency" % i)
    caps = reg.get("caps")
    if not isinstance(caps, dict) or not caps:
        raise ValueError("registration %d has no caps" % i)
    for name, v in caps.items():
        if not (_kebab_ok(name) and _is_int(v)):
            raise ValueError("registration %d: a cap is malformed" % i)
    if not _is_int(reg.get("total")):
        raise ValueError("registration %d: the total cap is malformed" % i)
    if budget_sha(reg["currency"], caps, reg["total"]) != reg["sha256"]:
        raise ValueError("registration %d: the frozen budget does not match its sha256" % i)


def _check_proposal(pid, p):
    bad = ValueError("proposal %s is malformed" % (pid if _kebab_ok(pid) else "(bad id)"))
    if not (_kebab_ok(pid) and isinstance(p, dict) and p.get("id") == pid):
        raise bad
    if not (_kebab_ok(p.get("category")) and _is_int(p.get("amount"), 1)
            and isinstance(p.get("currency"), str) and CURRENCY_RE.match(p["currency"])
            and p.get("recurring") in (None, "monthly") and _text_ok(p.get("what"))
            and _text_ok(p.get("at")) and p.get("status") in ("open", "withdrawn")):
        raise bad
    for k in ("start", "end"):
        if p.get(k) is not None and month_index(p[k]) is None:
            raise bad
    months = p.get("months")
    if not (isinstance(months, list) and 1 <= len(months) <= SPAN_CAP
            and all(month_index(m) is not None for m in months)
            and len(set(months)) == len(months)):
        raise bad
    recs = p.get("records")
    if not isinstance(recs, dict):
        raise bad
    for m, r in recs.items():
        if not (month_index(m) is not None and isinstance(r, dict) and _is_int(r.get("amount"))
                and _text_ok(r.get("by")) and _text_ok(r.get("at"))):
            raise bad
    w = p.get("withdrawn")
    if p["status"] == "withdrawn":
        if not (isinstance(w, dict) and _text_ok(w.get("by")) and _text_ok(w.get("reason"))):
            raise bad
    elif w is not None:
        raise bad


def load_state(root):
    """{seq, registrations, proposals}; empty when ledger.json does not exist."""
    path = os.path.join(_ledger_dir(root), STATE_FILE)
    try:
        text = _read_text(path)
        if text is None:
            return {"version": 1, "seq": 0, "registrations": [], "proposals": {}}
        st = json.loads(text)
        if not isinstance(st, dict) or st.get("version") != 1:
            raise ValueError("not a version 1 ledger")
        if not _is_int(st.get("seq"), 0, 10 ** 15):
            raise ValueError("seq is malformed")
        regs, props = st.get("registrations"), st.get("proposals")
        if not isinstance(regs, list) or not isinstance(props, dict):
            raise ValueError("registrations or proposals are malformed")
        for i, reg in enumerate(regs, 1):
            _check_registration(i, reg)
        for pid, p in props.items():
            _check_proposal(pid, p)
        return st
    except (OSError, ValueError, KeyError, TypeError, RecursionError, ArithmeticError) as e:
        raise StateError("cannot read %s/%s: %s" % (LEDGER_DIR, STATE_FILE, one_line(e)))


def load_log(root):
    """{seq, shas, state} from the append-only log.jsonl: the last seq, every register
    sha256 in order, and the state digest of the last event. The log still remembers events
    after ledger.json is deleted or rewritten by hand."""
    path = os.path.join(_ledger_dir(root), LOG_FILE)
    last, shas, digest = 0, [], None
    try:
        text = _read_text(path)
        for n, line in enumerate((text or "").splitlines(), 1):
            if not line.strip():
                continue
            ev = json.loads(line)
            if not isinstance(ev, dict) or not _is_int(ev.get("seq"), 1, 10 ** 15):
                raise ValueError("line %d is not a ledger event" % n)
            if ev["seq"] <= last:
                raise ValueError("line %d: seq does not increase" % n)
            last = ev["seq"]
            if not isinstance(ev.get("state_sha256"), str):
                raise ValueError("line %d: an event without state_sha256" % n)
            digest = ev["state_sha256"]
            if ev.get("event") == "register":
                if not isinstance(ev.get("sha256"), str):
                    raise ValueError("line %d: a register event without sha256" % n)
                shas.append(ev["sha256"])
    except (OSError, ValueError, RecursionError, ArithmeticError) as e:
        raise StateError("cannot read %s/%s: %s" % (LEDGER_DIR, LOG_FILE, one_line(e)))
    return {"seq": last, "shas": shas, "state": digest}


def integrity_notes(st, log):
    """Notes when ledger.json and the append-only log.jsonl disagree: a sign that the state
    was deleted, emptied or edited by hand (or a write was cut short)."""
    regs, log_seq, log_shas = st["registrations"], log["seq"], log["shas"]
    notes = []
    if not regs and log_shas:
        notes.append("log.jsonl records %d registration%s, but %s holds none: the ledger "
                     "state was deleted or emptied by hand"
                     % (len(log_shas), "" if len(log_shas) == 1 else "s", STATE_FILE))
    elif regs and not log_shas:
        notes.append("%s holds a registration that log.jsonl does not record: the log was "
                     "deleted or emptied by hand" % STATE_FILE)
    elif regs and log_shas[-1] != regs[-1]["sha256"]:
        notes.append("the latest registration in %s is not the latest one in log.jsonl: %s "
                     "was changed by hand" % (STATE_FILE, STATE_FILE))
    if st["seq"] != log_seq and not notes:
        notes.append("%s is at event %d but log.jsonl is at event %d: a state file was "
                     "changed by hand" % (STATE_FILE, st["seq"], log_seq))
    if not notes and log_seq and log["state"] != state_sha(st):
        notes.append("%s does not match the state that log.jsonl recorded last: it was "
                     "changed by hand" % STATE_FILE)
    return notes


# -- Evaluation -------------------------------------------------------------------
def current_budget(root):
    """(parsed budget or None, why-not text)."""
    path = os.path.join(root, *BUDGET_PATH.split("/"))
    try:
        with open(path, encoding="utf-8") as f:
            parsed = parse_budget(f.read())
    except FileNotFoundError:
        return None, "%s is missing" % BUDGET_PATH
    except (OSError, ValueError) as e:
        return None, "%s cannot be read (%s)" % (BUDGET_PATH, one_line(e))
    if parsed["problems"]:
        return None, "%s does not lint (%d problems)" % (BUDGET_PATH, len(parsed["problems"]))
    return parsed, ""


def stale_why(root, reg):
    """"" when budget/budget.md matches the registration, else why it does not. A missing or
    broken budget is stale too."""
    b, why = current_budget(root)
    if b is None:
        return why
    if budget_sha(b["currency"], b["caps"], b["total"]) != reg["sha256"]:
        return "%s differs from the registered copy" % BUDGET_PATH
    return ""


def charge(p, m):
    """What proposal p counts in month label m: the recorded amount when the human recorded
    one for m, else its reservation when it is open and m is one of its months."""
    rec = p["records"].get(m)
    if rec is not None:
        return rec["amount"]
    if p["status"] == "open" and m in p["months"]:
        return p["amount"]
    return 0


def usage(st, m, category=None):
    """(recorded, reserved) in month m, for one category or (None) for all."""
    recorded = reserved = 0
    for p in st["proposals"].values():
        if category is not None and p["category"] != category:
            continue
        rec = p["records"].get(m)
        if rec is not None:
            recorded += rec["amount"]
        elif p["status"] == "open" and m in p["months"]:
            reserved += p["amount"]
    return recorded, reserved


def over_cap(st, reg, category, months, extra):
    """The refusal reason when, in any of months, the category or the total would go over its
    cap with extra added to what every proposal already counts there (charge()), else "".
    A new proposal passes its amount as extra. A replay passes 0, because its own
    reservation or record is already in the sums."""
    for m in months:
        cat = sum(charge(p, m) for p in st["proposals"].values()
                  if p["category"] == category) + extra
        if cat > reg["caps"][category]:
            return ("%s %s would reach %s, over its monthly cap %s"
                    % (m, category, money(cat), money(reg["caps"][category])))
        tot = sum(charge(p, m) for p in st["proposals"].values()) + extra
        if tot > reg["total"]:
            return ("%s the total would reach %s, over the monthly total cap %s"
                    % (m, money(tot), money(reg["total"])))
    return ""


def proposal_key(p):
    return {k: p.get(k) for k in ("category", "amount", "currency", "recurring", "start",
                                  "end", "months", "what")}


# -- Output helpers ---------------------------------------------------------------
def _not_ignored(e):
    print("STOP: %s cannot be shown to be git-ignored (%s); spend records are business data"
          % (LEDGER_DIR, code(e)))
    print("NEXT: make %s a plain directory whose .gitignore holds `*`" % LEDGER_DIR)
    return 2


def _locked():
    print("STOP: the ledger lock is held by another command")
    return 2


def _refuse(reason):
    print("REFUSED: %s" % reason)
    print("NEXT: tell the human this reason; only the human changes the budget")
    return 3


def _human(by, what):
    """None when `by` is a human name, else the exit code after printing the STOP."""
    if not _name_ok(by):
        print("STOP: --by needs the name of the human who %s" % what)
        return 2
    if _is_agent(by):
        print("STOP: an agent: name cannot %s; only the human does, because it changes what "
              "later checks count" % what)
        return 2
    if _approval_word(by):
        print("STOP: --by holds a word that reads as approval; give the human's plain name, "
              "because the tool echoes it")
        return 2
    return None


# -- Commands ---------------------------------------------------------------------
def cmd_lint(a):
    path = a.budget if os.path.isabs(a.budget) else os.path.join(a.root, a.budget)
    try:
        with open(path, encoding="utf-8") as f:
            parsed = parse_budget(f.read())
    except (OSError, ValueError) as e:
        print("STOP: cannot read the budget: %s" % code(e))
        return 2
    if parsed["problems"]:
        for p in parsed["problems"]:
            print("PROBLEM: %s" % p)
        print("LINT: fail %d problems" % len(parsed["problems"]))
        return 2
    print("LINT: clean %d categories, currency %s, total %s"
          % (len(parsed["caps"]), parsed["currency"], money(parsed["total"])))
    return 0


def cmd_register(a):
    rc = _human(a.by, "register the budget")
    if rc is not None:
        return rc
    if a.reason is not None and not _name_ok(a.reason):
        print("STOP: --reason must be non-empty text without hidden characters")
        return 2
    path = os.path.join(a.root, *BUDGET_PATH.split("/"))
    try:
        with open(path, encoding="utf-8") as f:
            parsed = parse_budget(f.read())
    except (OSError, ValueError) as e:
        print("STOP: cannot read %s: %s" % (BUDGET_PATH, code(e)))
        return 2
    if parsed["problems"]:
        for p in parsed["problems"]:
            print("PROBLEM: %s" % p)
        print("LINT: fail %d problems" % len(parsed["problems"]))
        print("NEXT: fix %s with the human, then register again" % BUDGET_PATH)
        return 2
    cur, caps, total = parsed["currency"], parsed["caps"], parsed["total"]
    sha = budget_sha(cur, caps, total)
    raised = []
    try:
        with run_lock(a.root):
            _check_state_files(a.root)
            st = load_state(a.root)
            log = load_log(a.root)
            broken = integrity_notes(st, log)
            regs = st["registrations"]
            if a.reason is None and (broken or (not regs and (log["seq"] or st["proposals"]))):
                for note in broken:
                    print("NOTE: %s" % note)
                print("STOP: the ledger state was deleted or changed by hand; registering "
                      "again needs --reason")
                print("NEXT: show the human the NOTE: lines; the human registers again with "
                      "register --by <their name> --reason <why>")
                return 2
            if regs and regs[-1]["sha256"] == sha and not broken:
                print("REGISTERED: %s (no change since the last registration)" % sha[:12])
                return 0
            if regs and a.reason is None:
                print("STOP: the budget changed since the registration of %s; registering "
                      "again needs --reason" % regs[-1]["at"])
                print("NEXT: the human registers again with register --by <their name> "
                      "--reason <why>")
                return 2
            prev = regs[-1] if regs else None
            if prev and prev["currency"] != cur and st["proposals"]:
                print("STOP: the currency changed from %s to %s, but the ledger holds "
                      "proposals and records in %s; there is no conversion"
                      % (prev["currency"], cur, prev["currency"]))
                return 2
            if prev:
                for name in sorted(caps):
                    old = prev["caps"].get(name)
                    if old is None:
                        raised.append("RAISED: %s none -> %s (a new category)"
                                      % (name, money(caps[name])))
                    elif caps[name] > old:
                        raised.append("RAISED: %s %s -> %s" % (name, money(old),
                                                               money(caps[name])))
                if total > prev["total"]:
                    raised.append("RAISED: total %s -> %s" % (money(prev["total"]),
                                                              money(total)))
            at = _now()
            seq = max(st["seq"], log["seq"]) + 1
            regs.append({"sha256": sha, "by": a.by.strip(), "at": at,
                         "reason": a.reason.strip() if a.reason else None,
                         "currency": cur, "caps": caps, "total": total})
            st["seq"] = seq
            _write_state(a.root, st)
            _log(a.root, "register", st, sha256=sha, by=a.by.strip(), reason=a.reason,
                 resync=bool(broken))
    except NotIgnored as e:
        return _not_ignored(e)
    except Locked:
        return _locked()
    except StateError as e:
        print("STOP: %s" % code(e))
        return 2
    for line in raised:
        print(line)
    print("REGISTERED: %s by %s at %s, %d categories, currency %s, total %s"
          % (sha[:12], code(a.by.strip()), at, len(caps), cur, money(total)))
    return 0


def _proposal_months(a, today):
    """(months list, start, end, None) or (None, None, None, (exit code, message))."""
    now = current_month(today)
    if a.recurring is None:
        if a.start is not None or a.end is not None:
            return None, None, None, (2, "STOP: --start and --end need --recurring monthly")
        if a.month is None:
            return [month_label(now)], None, None, None
        i = month_index(a.month)
        if i is None:
            return None, None, None, (2, "STOP: --month must be YYYY-MM")
        if i < now:
            return None, None, None, (3, "month %s is before the current month %s"
                                      % (month_label(i), month_label(now)))
        return [month_label(i)], None, None, None
    if a.recurring != "monthly":
        return None, None, None, (2, "STOP: --recurring takes only monthly")
    if a.month is not None:
        return None, None, None, (2, "STOP: --month is for a one-off spend; a recurring one "
                                     "takes --start and --end")
    s = month_index(a.start)
    if s is None:
        return None, None, None, (2, "STOP: --recurring monthly needs --start YYYY-MM")
    if a.end is None:
        e = s + OPEN_ENDED_MONTHS - 1
    else:
        e = month_index(a.end)
        if e is None:
            return None, None, None, (2, "STOP: --end must be YYYY-MM")
        if e < s:
            return None, None, None, (2, "STOP: --end is before --start")
    if e - s + 1 > SPAN_CAP or e > MAX_MONTH_INDEX:
        return None, None, None, (2, "STOP: a recurring spend covers at most %d months, "
                                     "ending by 9999-12" % SPAN_CAP)
    if s < now:
        return None, None, None, (3, "month %s is before the current month %s"
                                  % (month_label(s), month_label(now)))
    return [month_label(i) for i in range(s, e + 1)], a.start, a.end, None


def cmd_check(a):
    if not _kebab_ok(a.id):
        print("STOP: --id must be a kebab-case proposal id, at most %d characters, with no "
              "approval word" % NAME_CAP)
        return 2
    if not _name_ok(a.what):
        print("STOP: --what must be non-empty text of at most %d characters without hidden "
              "characters" % TEXT_CAP)
        return 2
    months, start, end, err = _proposal_months(a, a.today)
    if err is not None and err[0] == 2:
        print(err[1])
        return 2
    amount = parse_amount(a.amount)
    try:
        with run_lock(a.root):
            _check_state_files(a.root)
            st = load_state(a.root)
            log = load_log(a.root)
            regs = st["registrations"]
            if not regs:
                if log["shas"]:
                    return _refuse("the ledger state was deleted or emptied by hand; the "
                                   "human registers the budget again with --reason")
                return _refuse("the budget is not registered; the human registers %s first"
                               % BUDGET_PATH)
            reg = regs[-1]
            why = stale_why(a.root, reg)
            if why:
                return _refuse("budget edited since register (%s); the human registers it "
                               "again" % why)
            if integrity_notes(st, log):
                return _refuse("the ledger state and its log disagree (changed by hand); the "
                               "human registers the budget again with --reason")
            if a.currency != reg["currency"]:
                return _refuse("the currency is not the project currency %s; there is no "
                               "conversion" % reg["currency"])
            if a.category not in reg["caps"]:
                return _refuse("unknown category; the registered categories are %s"
                               % ", ".join(sorted(reg["caps"])))
            if amount is None or amount <= 0:
                return _refuse("the amount is not a valid positive amount: a decimal in "
                               "major units, ASCII digits, at most %d before the point and "
                               "2 after it" % AMOUNT_DIGITS)
            if err is not None:
                return _refuse(err[1])
            prop = {"id": a.id, "category": a.category, "amount": amount,
                    "currency": a.currency, "recurring": a.recurring, "start": start,
                    "end": end, "months": months, "what": a.what.strip()}
            old = st["proposals"].get(a.id)
            if old is not None:
                if proposal_key(old) != proposal_key(prop):
                    return _refuse("proposal id reused with different fields")
                if old["status"] == "withdrawn":
                    return _refuse("proposal %s was withdrawn by the human; a new proposal "
                                   "needs a new id" % a.id)
                # A replay is checked again against today's caps and spend: a cap cut or a
                # record since the first check can put it over. Its reservation stays (only
                # the human withdraws it), and a refused replay writes nothing.
                why = over_cap(st, reg, a.category, months, 0)
                if why:
                    return _refuse("%s; %s stays reserved until the human withdraws it"
                                   % (why, a.id))
                print("NOTE: %s was checked before; nothing more is reserved" % a.id)
                if a.recurring is not None and a.end is None:
                    print("NOTE: open-ended; reserved only to %s" % months[-1])
                print("NOT_REFUSED: %s — the human's approval is still required" % a.id)
                return 0
            why = over_cap(st, reg, a.category, months, amount)
            if why:
                return _refuse(why)
            seq = max(st["seq"], log["seq"]) + 1
            prop.update(at=_now(), status="open", records={}, withdrawn=None)
            st["proposals"][a.id] = prop
            st["seq"] = seq
            _write_state(a.root, st)
            _log(a.root, "check", st, id=a.id, category=a.category, months=len(months))
    except NotIgnored as e:
        return _not_ignored(e)
    except Locked:
        return _locked()
    except StateError as e:
        print("STOP: %s" % code(e))
        return 2
    span = months[0] if len(months) == 1 else "%s..%s" % (months[0], months[-1])
    print("NOTE: reserved %s %s a month in %s (%d month%s)"
          % (money(amount), a.currency, span, len(months), "" if len(months) == 1 else "s"))
    if a.recurring is not None and a.end is None:
        print("NOTE: open-ended; reserved only to %s" % months[-1])
    print("NOT_REFUSED: %s — the human's approval is still required" % a.id)
    return 0


def _need_state(root):
    """(state, reg) or an exit code, under the lock, for record and withdraw."""
    st = load_state(root)
    log = load_log(root)
    if not st["registrations"]:
        print("STOP: the budget is not registered; the human registers %s first" % BUDGET_PATH)
        return 2
    notes = integrity_notes(st, log)
    if notes:
        for n in notes:
            print("NOTE: %s" % n)
        print("STOP: the ledger state and its log disagree; the human registers the budget "
              "again with --reason")
        return 2
    return st, st["registrations"][-1], log


def cmd_record(a):
    rc = _human(a.by, "record spend")
    if rc is not None:
        return rc
    if not _kebab_ok(a.id):
        print("STOP: --id must be a kebab-case proposal id")
        return 2
    i = month_index(a.month)
    if i is None:
        print("STOP: --month must be YYYY-MM")
        return 2
    if i > current_month(a.today):
        print("STOP: month %s has not started; record spend that happened" % a.month)
        return 2
    amount = parse_amount(a.amount)
    if amount is None:
        print("STOP: --amount must be a decimal in major units, ASCII digits, at most %d "
              "before the point and 2 after it" % AMOUNT_DIGITS)
        return 2
    try:
        with run_lock(a.root):
            _check_state_files(a.root)
            got = _need_state(a.root)
            if isinstance(got, int):
                return got
            st, reg, log = got
            p = st["proposals"].get(a.id)
            if p is None:
                print("STOP: no proposal %s; a record needs the id of a checked proposal"
                      % a.id)
                return 2
            if a.currency != reg["currency"]:
                return _refuse("the currency is not the project currency %s; there is no "
                               "conversion" % reg["currency"])
            prev = p["records"].get(a.month)
            covered = a.month in p["months"]
            seq = max(st["seq"], log["seq"]) + 1
            p["records"][a.month] = {"amount": amount, "by": a.by.strip(), "at": _now()}
            st["seq"] = seq
            _write_state(a.root, st)
            _log(a.root, "record", st, id=a.id, month=a.month, by=a.by.strip(),
                 replaced=prev is not None)
            rec_total, _ = usage(st, a.month, p["category"])
            over = rec_total > reg["caps"].get(p["category"], 0)
    except NotIgnored as e:
        return _not_ignored(e)
    except Locked:
        return _locked()
    except StateError as e:
        print("STOP: %s" % code(e))
        return 2
    if not covered:
        print("NOTE: %s is not a month that proposal %s reserved; recorded all the same"
              % (a.month, a.id))
    if prev is not None:
        print("NOTE: replaced the earlier record of %s for %s" % (money(prev["amount"]),
                                                                  a.month))
    if over:
        print("NOTE: recorded spend in %s for %s is over its monthly cap" % (a.month,
                                                                            p["category"]))
    print("RECORDED: %s %s %s %s by %s" % (a.id, a.month, money(amount), reg["currency"],
                                          code(a.by.strip())))
    return 0


def cmd_withdraw(a):
    rc = _human(a.by, "withdraw a proposal")
    if rc is not None:
        return rc
    if not _name_ok(a.reason):
        print("STOP: --reason must be non-empty text without hidden characters")
        return 2
    if not _kebab_ok(a.id):
        print("STOP: --id must be a kebab-case proposal id")
        return 2
    try:
        with run_lock(a.root):
            _check_state_files(a.root)
            got = _need_state(a.root)
            if isinstance(got, int):
                return got
            st, _reg, log = got
            p = st["proposals"].get(a.id)
            if p is None:
                print("STOP: no proposal %s" % a.id)
                return 2
            if p["status"] == "withdrawn":
                print("WITHDRAWN: %s (already withdrawn)" % a.id)
                return 0
            freed = sum(p["amount"] for m in p["months"] if m not in p["records"])
            seq = max(st["seq"], log["seq"]) + 1
            p["status"] = "withdrawn"
            p["withdrawn"] = {"by": a.by.strip(), "at": _now(), "reason": a.reason.strip()}
            st["seq"] = seq
            _write_state(a.root, st)
            _log(a.root, "withdraw", st, id=a.id, by=a.by.strip(), reason=a.reason.strip())
    except NotIgnored as e:
        return _not_ignored(e)
    except Locked:
        return _locked()
    except StateError as e:
        print("STOP: %s" % code(e))
        return 2
    print("WITHDRAWN: %s by %s, freed %s in open reservations" % (a.id, code(a.by.strip()),
                                                                money(freed)))
    return 0


def cmd_status(a):
    """Read-only: it takes no lock and creates nothing, so it never writes."""
    if a.month is None:
        m = month_label(current_month(a.today))
    elif month_index(a.month) is None:
        print("STOP: --month must be YYYY-MM")
        return 2
    else:
        m = a.month
    try:
        # The same symlink refusals as the writers (_ensure_dir and _check_state_files).
        if (os.path.islink(os.path.join(a.root, ".skill-contract"))
                or os.path.islink(_ledger_dir(a.root))):
            raise StateError("%s is a symlink" % LEDGER_DIR)
        _check_state_files(a.root)
        st = load_state(a.root)
        log = load_log(a.root)
    except StateError as e:
        print("STOP: %s" % code(e))
        return 2
    notes = integrity_notes(st, log)
    for n in notes:
        print("NOTE: %s" % n)
    if not st["registrations"]:
        print("STOP: the budget is not registered; the human registers %s first" % BUDGET_PATH)
        return 2
    reg = st["registrations"][-1]
    why = stale_why(a.root, reg)
    if why:
        print("NOTE: stale: %s; every check refuses until the human registers it again" % why)
    print("STATUS: %s currency=%s stale=%s registered=%s by %s"
          % (m, reg["currency"], "true" if why else "false", reg["at"], code(reg["by"])))
    for name in sorted(reg["caps"]):
        rec, res = usage(st, m, name)
        cap = reg["caps"][name]
        print("CATEGORY: %s cap=%s recorded=%s reserved=%s headroom=%s"
              % (name, money(cap), money(rec), money(res), money(cap - rec - res)))
    rec, res = usage(st, m)
    print("TOTAL: cap=%s recorded=%s reserved=%s headroom=%s"
          % (money(reg["total"]), money(rec), money(res), money(reg["total"] - rec - res)))
    return 0


def _today_arg(s):
    if not DAY_RE.match(s or ""):
        return None
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return None


class _Parser(argparse.ArgumentParser):
    """argparse that never echoes an argument back: its default error text quotes the
    offending value, which could carry any word into the output."""

    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write("STOP: bad arguments; see the usage above\n")
        raise SystemExit(2)


def main(argv=None):
    p = _Parser(prog="ledger.py")
    p.add_argument("--root", default=".")
    p.add_argument("--today", default=None, help="for tests only")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    s = sub.add_parser("lint")
    s.add_argument("budget", nargs="?", default=BUDGET_PATH)
    s = sub.add_parser("register")
    s.add_argument("--by", required=True)
    s.add_argument("--reason")
    s = sub.add_parser("check")
    for f in ("--id", "--category", "--amount", "--currency", "--what"):
        s.add_argument(f, required=True)
    for f in ("--month", "--recurring", "--start", "--end"):
        s.add_argument(f)
    s = sub.add_parser("record")
    for f in ("--id", "--amount", "--currency", "--month", "--by"):
        s.add_argument(f, required=True)
    s = sub.add_parser("withdraw")
    for f in ("--id", "--by", "--reason"):
        s.add_argument(f, required=True)
    s = sub.add_parser("status")
    s.add_argument("--month")
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
    return {"lint": cmd_lint, "register": cmd_register, "check": cmd_check,
            "record": cmd_record, "withdraw": cmd_withdraw, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
