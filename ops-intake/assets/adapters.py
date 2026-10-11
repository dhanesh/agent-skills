"""ops-intake adapters: strict parsers from a source's text output to signals.

Pure functions over a string. This module reads no file, opens no socket and runs no
command. Each adapter is adapt(raw, ctx) -> (signals, problems). ctx holds `source`
(the configured source name), `now` (UTC RFC 3339) and, for some formats, `run`.
A bad record becomes a problem string and never stops the other records. Problem
strings carry field names and positions, never the external text itself.
"""
import datetime
import hashlib
import json
import re

EVIDENCE_TEXT_CAP = 4000
TITLE_CAP = 500
MAX_SIGNALS = 5000      # per import; intake reports the rest as one problem
SOURCE_ID_CAP, URL_CAP, KEY_CAP = 200, 2000, 200

_TIME_RE = re.compile(
    r"^([0-9]{4}-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9])(\.[0-9]+)?(Z|[+-][0-9][0-9]:[0-9][0-9]|[+-][0-9][0-9][0-9][0-9])\Z")
_SEMVER_RE = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?\Z")
_RELEASE_RE = re.compile(r"^RELEASE: (\S+) (\S+)\Z")
_RELEASE_SEVERITY = {"prod_failed": 4, "outcome_unknown": 4, "rolled_back": 3,
                     "stage_failed": 3, "abandoned": 2}
KINDS = ("release", "ci", "issue", "feedback")
# intake-signals-jsonl keeps the first three: feedback text is redacted only by
# feedback-jsonl, so the generic format cannot carry unredacted customer text in.
SIGNAL_KINDS = ("release", "ci", "issue")


def parse_time(s):
    """UTC RFC 3339 with Z. Accepts Z, +HH:MM and +HHMM, with optional fractional
    seconds. Raises ValueError on anything else."""
    if not isinstance(s, str):
        raise ValueError("timestamp must be a string")
    m = _TIME_RE.match(s)
    if not m:
        raise ValueError("not an RFC 3339 timestamp")
    base, _frac, zone = m.groups()
    dt = datetime.datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")  # ValueError if out of range
    try:
        return _shift(dt, zone)
    except OverflowError:
        raise ValueError("timestamp out of range")


def _shift(dt, zone):
    if zone != "Z":
        digits = zone[1:].replace(":", "")
        hh, mm = int(digits[:2]), int(digits[2:])
        if hh > 23 or mm > 59:
            raise ValueError("bad zone offset")
        delta = datetime.timedelta(hours=hh, minutes=mm)
        dt = dt - delta if zone[0] == "+" else dt + delta
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _evidence(ctx, source_id, text, key):
    return {"text": text[:EVIDENCE_TEXT_CAP], "source": ctx["source"], "source_id": source_id,
            "fetched_at": ctx["now"], "key": key}


def _signal(ctx, source_id, url, kind, title, severity, first_seen, last_seen, trust, evidence):
    return {"source": ctx["source"], "source_id": source_id, "url": url, "kind": kind,
            "title": title[:TITLE_CAP], "severity": severity, "first_seen": first_seen,
            "last_seen": last_seen, "trust": trust, "evidence": evidence}


# -- gh-issues-json ---------------------------------------------------------------
def _issue_severity(labels):
    names = {l["name"].lower() for l in labels}
    return 4 if "incident" in names else 3 if "bug" in names else 2


def _one_issue(rec, ctx):
    if not isinstance(rec, dict):
        raise ValueError("not an object")
    n = rec.get("number")
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError("number must be a positive integer")
    for k in ("title", "url"):
        if not isinstance(rec.get(k), str):
            raise ValueError("%s must be a string" % k)
    body = rec.get("body")
    if body is None:
        body = ""
    if not isinstance(body, str):
        raise ValueError("body must be a string or null")
    labels = rec.get("labels", [])
    if not (isinstance(labels, list) and all(
            isinstance(l, dict) and isinstance(l.get("name"), str) for l in labels)):
        raise ValueError("labels must be a list of objects with a name")
    state = rec.get("state")
    if state not in ("OPEN", "CLOSED"):
        raise ValueError("state must be OPEN or CLOSED")
    created, updated = parse_time(rec.get("createdAt")), parse_time(rec.get("updatedAt"))
    sid = str(n)
    s = _signal(ctx, sid, rec["url"], "issue", rec["title"], _issue_severity(labels),
                created, updated, "normal", [_evidence(ctx, sid, body, updated)])
    if state == "CLOSED":
        s["closed"] = True
    return s


def adapt_gh_issues(raw, ctx):
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        return [], ["input is not valid JSON"]
    if not isinstance(data, list):
        return [], ["input must be a JSON array"]
    sigs, problems = [], []
    for i, rec in enumerate(data):
        try:
            sigs.append(_one_issue(rec, ctx))
        except ValueError as e:
            problems.append("record %d: %s" % (i, e))
        except Exception:  # backstop: one odd record never aborts the import
            problems.append("record %d: unreadable record" % i)
    return sigs, problems


# -- intake-signals-jsonl ---------------------------------------------------------
def _one_jsonl(rec, ctx):
    if not isinstance(rec, dict):
        raise ValueError("not an object")
    if rec.get("source") != ctx["source"]:
        raise ValueError("source must equal the import source")
    for k in ("source_id", "title"):
        if not (isinstance(rec.get(k), str) and rec[k]):
            raise ValueError("%s must be a non-empty string" % k)
    if not isinstance(rec.get("url", ""), str) or len(rec.get("url", "")) > URL_CAP:
        raise ValueError("url must be a string of at most %d characters" % URL_CAP)
    if len(rec["source_id"]) > SOURCE_ID_CAP:
        raise ValueError("source_id longer than %d characters" % SOURCE_ID_CAP)
    if rec.get("kind") not in SIGNAL_KINDS:
        raise ValueError("kind must be release, ci or issue")
    sev = rec.get("severity")
    if not isinstance(sev, int) or isinstance(sev, bool) or not 1 <= sev <= 4:
        raise ValueError("severity must be 1 to 4")
    first, last = parse_time(rec.get("first_seen")), parse_time(rec.get("last_seen"))
    ev = rec.get("evidence", [])
    if not isinstance(ev, list):
        raise ValueError("evidence must be a list")
    evidence = []
    for e in ev:
        if not (isinstance(e, dict) and isinstance(e.get("text"), str)):
            raise ValueError("each evidence needs a text string")
        key = e.get("key")
        if key is None:
            key = hashlib.sha256(e["text"].encode("utf-8")).hexdigest()[:12]
        if not isinstance(key, str) or len(key) > KEY_CAP:
            raise ValueError("evidence key must be a string of at most %d characters" % KEY_CAP)
        evidence.append(_evidence(ctx, rec["source_id"], e["text"], key))
    # Whatever the record says about trust is ignored: this format is always low.
    return _signal(ctx, rec["source_id"], rec.get("url", ""), rec["kind"], rec["title"],
                   sev, first, last, "low", evidence)


def adapt_jsonl(raw, ctx):
    sigs, problems = [], []
    for n, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            sigs.append(_one_jsonl(json.loads(line), ctx))
        except (ValueError, RecursionError) as e:
            problems.append("line %d: %s" % (n, e if isinstance(e, ValueError) else "nested too deeply"))
        except Exception:  # backstop: one odd line never aborts the import
            problems.append("line %d: unreadable record" % n)
    return sigs, problems


# -- feedback-jsonl ---------------------------------------------------------------
# One support message, app review or form answer per line, from the user's (or the
# agent's) transform of an export. The record's topic groups messages into one item.
CHANNELS = ("email", "review", "form", "chat", "other")
_TOPIC_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\Z")
TOPIC_CAP = 64
# The raw id cap. The evidence key is <channel>:<id>; an escaped raw id ("r:" + id, see
# _one_feedback) can make it 2 characters longer than KEY_CAP, which only bounds input keys.
FEEDBACK_ID_CAP = KEY_CAP - len("review:")

# Redaction (spec 2.3, widened in the review round) is a safety net, not a guarantee:
# names, addresses and account numbers still get through, and so do fullwidth or
# Arabic-Indic digits, a fullwidth at sign and spelled-out addresses ("pat [at] example
# [dot] com"). An email address is redacted everywhere, URLs too, in plain or %40 form,
# except the git remote form git@host:path. Phone numbers are looked for outside URLs,
# IPv4 addresses and dates; inside a URL only after a phone key (phone=, tel=, ...).
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+")
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+?)(?:@|%40)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*"
    r"\.[A-Za-z]{2,}(?![A-Za-z0-9-])", re.I)
_GIT_PATH_RE = re.compile(r":[A-Za-z0-9_~./-]")
# Horizontal space only: a line break never joins a phone number.
_WS = "[ \t\u00a0\u2007\u202f]"
_HYPHENS = "\\-\u2010-\u2015\u2212"
_SEP = "(?:%s*[.%s]%s*|%s+)" % (_WS, _HYPHENS, _WS, _WS)
# A phone: digits and single groups in parentheses, with a separator (a run of spaces, or
# one '.', '-' or Unicode dash with spaces around it) or none between them, and an
# optional leading '+'. Not part of a longer token: no letter, digit, '-', '/', '#', '+',
# '.', '@', '?', '&' or '%' right before it (':' and '=' only after a phone key), and no
# letter, digit, ':', '/', '@' or '%' right after it, nor a '.' or dash that runs on into
# a letter or digit.
_PHONE_RE = re.compile(
    r"(?<![A-Za-z0-9_/#@+.?&%%-])\+?(?:\([0-9]+\)|[0-9])(?:%s?(?:\([0-9]+\)|[0-9]))*"
    r"(?![A-Za-z0-9_:/@%%]|[.%s][A-Za-z0-9])" % (_SEP, _HYPHENS))
PHONE_MIN_DIGITS = 10
_KEYS = ("phone|ph|tel|telephone|mob|mobile|cell|cellphone|number|num|fax|whatsapp|"
         "msisdn|sms")
# A phone key right before ':' or '=' ("Phone:0770...", "tel:+1...", "number=...").
_KEY_BEFORE_RE = re.compile(r"(?i)(?:^|[^A-Za-z])(?:%s)%s*[:=]\Z" % (_KEYS, _WS))
# Phone context: a phone word a few characters before the number.
_CONTEXT_RE = re.compile(r"(?i)(?<![A-Za-z])(?:%s|call|dial|ring|contact|reach)(?![A-Za-z])"
                         r"[^\n]{0,12}\Z"
                         % _KEYS)
_URL_PHONE_RE = re.compile(r"(?i)((?:^|[^A-Za-z])(?:%s)[=:])((?:\+|%%2B)?[0-9][0-9.()-]*[0-9])"
                           % _KEYS)
# Tokens the phone pass leaves alone: URLs, IPv4 addresses, and dates (year first or
# year last) with an optional time after them.
_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])"
_TIME = (r"(?:(?:T|%s+)[0-9]{1,2}[:.][0-9]{2}(?:[:.][0-9]{2}(?:\.[0-9]+)?)?"
         r"(?:Z|[+-][0-9]{2}:?[0-9]{2})?(?![0-9]))?" % _WS)
_DAY, _MONTH = r"(?:0?[1-9]|[12][0-9]|3[01])", r"(?:0?[1-9]|1[0-2])"
_KEEP_RE = re.compile(
    r"(?P<url>[A-Za-z][A-Za-z0-9+.-]*://\S+)"
    r"|(?<![0-9.])(?:%s\.){3}%s(?![0-9]|\.[0-9])"
    r"|(?<![0-9])(?:19|20)[0-9]{2}[-./]%s[-./]%s(?![0-9])%s"
    r"|(?<![0-9.])%s[-./]%s[-./](?:19|20)[0-9]{2}(?![0-9])%s"
    % (_OCTET, _OCTET, _MONTH, _DAY, _TIME, _DAY, _DAY, _TIME))
# Number shapes that are not phones unless a phone word is near (spec review M2): an
# epoch in seconds or milliseconds, two space-joined groups of 4+ digits that start with
# 1-5 (Indian mobiles, 98765 43210, start with 6-9), digits grouped in threes with spaces
# (1 234 567), and a year-first id (2026-000123-45). North American numbers never start
# with 1 in their 10-digit form, so the epoch shape skips none of them.
_NOT_PHONE_RE = re.compile(
    r"1(?:[0-9]{9}|[0-9]{12})\Z|[1-5][0-9]{3,}%s+[0-9]{4,}\Z|[1-9][0-9]{0,2}(?:%s+[0-9]{3})+\Z"
    r"|(?:19|20)[0-9]{2}-[0-9]{5,}" % (_WS, _WS))


def _email(m):
    s = m.string
    if m.group(1).lower() == "git" and _GIT_PATH_RE.match(s, m.end()):
        return m.group(0)  # a git remote, git@host:owner/repo.git
    return "[email]"


def _phones(seg):
    def phone(m):
        s = m.group(0)
        if sum(c.isdigit() for c in s) < PHONE_MIN_DIGITS:
            return s
        before = seg[max(0, m.start() - 40):m.start()]  # a window: no O(n^2) on long text
        if before.endswith((":", "=")) and not _KEY_BEFORE_RE.search(before):
            return s  # ref=12345678901, ts:1728640000123
        if _NOT_PHONE_RE.match(s) and not _CONTEXT_RE.search(before):
            return s
        return "[phone]"
    return _PHONE_RE.sub(phone, seg)


def _url_phones(url):
    def phone(m):
        if sum(c.isdigit() for c in m.group(2)) < PHONE_MIN_DIGITS:
            return m.group(0)
        return m.group(1) + "[phone]"
    return _URL_PHONE_RE.sub(phone, url)


def redact(text):
    """text with email addresses as [email] and phone numbers (10 or more digits) as
    [phone]. Inside a URL a phone is redacted only after a phone key (phone=, tel=)."""
    text = _EMAIL_RE.sub(_email, text)
    out, pos = [], 0
    for m in _KEEP_RE.finditer(text):
        out.append(_phones(text[pos:m.start()]))
        out.append(_url_phones(m.group(0)) if m.group("url") else m.group(0))
        pos = m.end()
    out.append(_phones(text[pos:]))
    return "".join(out)


FUTURE_SKEW_DAYS = 1  # a created_at later than the import clock plus this is a problem


def _plus_days(stamp, days):
    t = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ") + datetime.timedelta(days=days)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _feedback_severity(rec):
    """F4: a star rating decides when there is one (1-2 = 3, 3 = 2, 4-5 = 1); else the
    record's own severity, capped at 3; else 2. Feedback never reaches 4 on its own."""
    rating, sev = rec.get("rating"), rec.get("severity")
    if rating is not None:
        if not isinstance(rating, int) or isinstance(rating, bool) or not 1 <= rating <= 5:
            raise ValueError("rating must be 1 to 5")
    if sev is not None:
        if not isinstance(sev, int) or isinstance(sev, bool) or not 1 <= sev <= 4:
            raise ValueError("severity must be 1 to 4")
    if rating is not None:
        return 3 if rating <= 2 else 2 if rating == 3 else 1
    return min(sev, 3) if sev is not None else 2


def _one_feedback(rec, ctx):
    if not isinstance(rec, dict):
        raise ValueError("not an object")
    mid = rec.get("id")
    if isinstance(mid, int) and not isinstance(mid, bool) and mid >= 0:
        mid = str(mid)
    if not (isinstance(mid, str) and mid and len(mid) <= FEEDBACK_ID_CAP):
        raise ValueError("id must be a non-empty string of at most %d characters"
                         % FEEDBACK_ID_CAP)
    topic = rec.get("topic")
    if not (isinstance(topic, str) and len(topic) <= TOPIC_CAP and _TOPIC_RE.match(topic)):
        raise ValueError("topic must be kebab-case (a-z, 0-9, single dashes), at most %d "
                         "characters" % TOPIC_CAP)
    channel = rec.get("channel")
    if channel not in CHANNELS:
        raise ValueError("channel must be one of %s" % ", ".join(CHANNELS))
    created = parse_time(rec.get("created_at"))
    if created > _plus_days(ctx["now"], FUTURE_SKEW_DAYS):
        # A problem, not a clamp: intake never rewrites a time it was given.
        raise ValueError("created_at is more than a day after the import time")
    text = rec.get("text")
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    url = rec.get("url")
    url = "" if url is None else url
    if not isinstance(url, str) or len(url) > URL_CAP:
        raise ValueError("url must be a string of at most %d characters" % URL_CAP)
    sev = _feedback_severity(rec)
    # An id that holds an email address or a phone number (form exports key on the
    # respondent's address) is stored only as a stable hash, so dedupe still works.
    # A raw id that starts with "h:" or "r:" gets one more "r:" in front, so a raw id can
    # never equal a hashed one ("h:" + 16 hex) and the escape stays one-to-one (L4).
    if redact(mid) != mid:
        mid = "h:" + hashlib.sha256(mid.encode("utf-8")).hexdigest()[:16]
    elif mid.startswith(("h:", "r:")):
        mid = "r:" + mid
    # Redact the whole text before _evidence caps it, so a cut cannot split an address.
    ev = _evidence(ctx, topic, redact(text), "%s:%s" % (channel, mid))
    ev["created_at"] = created
    # Whatever the record says about trust, kind or title is ignored.
    return _signal(ctx, topic, redact(url), "feedback", "feedback: %s" % topic, sev, created,
                   created, "low", [ev])


def adapt_feedback(raw, ctx):
    sigs, problems = [], []
    for n, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            sigs.append(_one_feedback(json.loads(line), ctx))
        except (ValueError, RecursionError) as e:
            problems.append("line %d: %s" % (n, e if isinstance(e, ValueError) else "nested too deeply"))
        except Exception:  # backstop: one odd line never aborts the import
            problems.append("line %d: unreadable record" % n)
    return sigs, problems


# -- release-status ---------------------------------------------------------------
def adapt_release_status(raw, ctx):
    sigs, problems = [], []
    for n, line in enumerate(raw.splitlines(), 1):
        line = line.rstrip()
        if not line.strip() or line == "RELEASE: none":
            continue
        m = _RELEASE_RE.match(line)
        if not m:
            problems.append("line %d: not a RELEASE: <version> <status> line" % n)
            continue
        version, status = m.groups()
        if not _SEMVER_RE.match(version) or status not in _RELEASE_SEVERITY:
            continue  # archived names and statuses that are not failures
        s = _signal(ctx, version, "", "release", "Release %s %s" % (version, status),
                    _RELEASE_SEVERITY[status], ctx["now"], ctx["now"], "normal",
                    [_evidence(ctx, version, line, "%s %s" % (version, status))])
        # The status text has no times, so the same version again is never a recurrence.
        s["recurrence"] = False
        sigs.append(s)
    return sigs, problems


# -- gh-runs-json and gh-run-jobs-json --------------------------------------------
CI_FAILURES = ("failure", "timed_out", "startup_failure")
_STATUSES = ("completed", "in_progress", "queued", "requested", "waiting", "pending")
_CONCLUSIONS = ("success", "failure", "cancelled", "skipped", "neutral", "timed_out",
                "action_required", "stale", "startup_failure")


def _text(rec, k, cap=URL_CAP):
    v = rec.get(k)
    if not (isinstance(v, str) and v and len(v) <= cap):
        raise ValueError("%s must be a non-empty string of at most %d characters" % (k, cap))
    return v


def _int(rec, k, default=None):
    v = rec.get(k, default)
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        raise ValueError("%s must be a positive integer" % k)
    return v


def _json_list(raw):
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        return None, "input is not valid JSON"
    return data, None


def _one_run(rec, ctx):
    """(run id, run record) for a failing run on the default branch, or None to skip."""
    if not isinstance(rec, dict):
        raise ValueError("not an object")
    if rec.get("headBranch") != ctx["default_branch"]:
        return None
    wanted = ctx.get("workflows")
    if wanted and rec.get("workflowName") not in wanted:
        return None
    status, concl = rec.get("status"), rec.get("conclusion")
    if status not in _STATUSES:
        raise ValueError("unknown status value")
    if status != "completed":
        return None
    if concl not in _CONCLUSIONS:
        raise ValueError("unknown conclusion value")
    if concl not in CI_FAILURES:
        return None
    rid = _int(rec, "databaseId")
    if rid >= 10 ** 20:  # the queue keeps run ids of 1 to 20 digits (intake.py _RUN_RE)
        raise ValueError("databaseId must be at most 20 digits")
    run = {"workflowName": _text(rec, "workflowName", 100), "headBranch": rec["headBranch"],
           "headSha": _text(rec, "headSha", 64), "url": _text(rec, "url"),
           "createdAt": parse_time(rec.get("createdAt")),
           "updatedAt": parse_time(rec.get("updatedAt")),
           "attempt": _int(rec, "attempt", 1), "jobs_imported": False,
           "conclusion": concl, "imported_at": ctx["now"], "jobs_failures": 0}
    return str(rid), run


def adapt_gh_runs(raw, ctx):
    """No signals: failing runs go to ctx['runs_out'] and wait for their jobs."""
    data, err = _json_list(raw)
    if err:
        return [], [err]
    if not isinstance(data, list):
        return [], ["input must be a JSON array"]
    problems = []
    for i, rec in enumerate(data):
        try:
            got = _one_run(rec, ctx)
        except ValueError as e:
            problems.append("record %d: %s" % (i, e))
            continue
        except Exception:  # backstop: one odd record never aborts the import
            problems.append("record %d: unreadable record" % i)
            continue
        if got:
            rid, run = got
            old = ctx["runs"].get(rid)
            if old and old.get("attempt") == run["attempt"]:
                run["jobs_imported"] = bool(old.get("jobs_imported"))
                # The same attempt again: keep its failure count and its import time.
                run["jobs_failures"] = old.get("jobs_failures", 0)
                run["imported_at"] = old.get("imported_at", run["imported_at"])
            ctx["runs_out"][rid] = run
    return [], problems


def _one_job(rec, run, ctx):
    if not isinstance(rec, dict):
        raise ValueError("not an object")
    status, concl = rec.get("status"), rec.get("conclusion")
    if status not in _STATUSES:
        raise ValueError("unknown status value")
    if status != "completed":
        return None
    if concl not in _CONCLUSIONS:
        raise ValueError("unknown conclusion value")
    if concl not in CI_FAILURES:
        return None
    jid = _int(rec, "databaseId")
    name = _text(rec, "name", 100)
    url = _text(rec, "url")
    done = parse_time(rec.get("completedAt")) if rec.get("completedAt") else run["updatedAt"]
    steps = rec.get("steps", [])
    if not isinstance(steps, list):
        raise ValueError("steps must be a list")
    failed = [st["name"] for st in steps if isinstance(st, dict)
              and isinstance(st.get("name"), str) and st.get("conclusion") in CI_FAILURES]
    sid = "%s/%s/%s" % (run["workflowName"], name, run["headBranch"])
    if len(sid) > SOURCE_ID_CAP:
        raise ValueError("source id longer than %d characters" % SOURCE_ID_CAP)
    # gh 2.98.0 job objects carry no attempt; use theirs if present, else the run's.
    att = rec["attempt"] if "attempt" in rec else run["attempt"]
    if not isinstance(att, int) or isinstance(att, bool) or att < 1:
        raise ValueError("attempt must be a positive integer")
    key = "%s:%s:%s" % (ctx["run"], att, jid)
    text = "failed steps: %s" % (", ".join(failed) if failed else "(none recorded)")
    title = "%s / %s failing on %s" % (run["workflowName"], name, run["headBranch"])
    return _signal(ctx, sid, url, "ci", title, 3, done, done, "normal",
                   [_evidence(ctx, sid, text, key)])


def adapt_gh_run_jobs(raw, ctx):
    run = ctx["runs"].get(ctx["run"])
    if run is None:
        return [], ["run %s was not imported" % ctx["run"]]
    data, err = _json_list(raw)
    if err:
        return [], [err]
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return [], ["input must be an object with a jobs array"]
    ctx["jobs_ok"] = True
    sigs, problems = [], []
    for i, rec in enumerate(jobs):
        try:
            s = _one_job(rec, run, ctx)
        except ValueError as e:
            problems.append("job %d: %s" % (i, e))
            continue
        except Exception:  # backstop: one odd job never aborts the import
            problems.append("job %d: unreadable record" % i)
            continue
        if s:
            sigs.append(s)
    if not sigs:
        # The run failed, yet no job did (startup_failure has no jobs): one signal for
        # the run itself, so the failure is not lost.
        s = _run_signal(run, ctx)
        if len(s["source_id"]) > SOURCE_ID_CAP:
            problems.append("run: source id longer than %d characters" % SOURCE_ID_CAP)
        else:
            sigs.append(s)
    return sigs, problems


def _run_signal(run, ctx):
    sid = "%s/(run)/%s" % (run["workflowName"], run["headBranch"])
    key = "%s:%s:run" % (ctx["run"], run["attempt"])
    text = "the run concluded %s and no failing job was recorded" % run.get("conclusion",
                                                                           "failure")
    title = "%s failing on %s (no failing job recorded)" % (run["workflowName"],
                                                            run["headBranch"])
    return _signal(ctx, sid, run["url"], "ci", title, 3, run["updatedAt"], run["updatedAt"],
                   "normal", [_evidence(ctx, sid, text, key)])


# -- release-envelope -------------------------------------------------------------
_VERSION_RE = re.compile(r"^[!-~]{1,%d}\Z" % SOURCE_ID_CAP)  # printable ASCII, no space
_FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}\Z")
RELEASE_OUTCOMES = ("verified", "rolled_back")


def release_result(payload):
    """(version, commit, outcome) from a release-result/v1 payload. Raises ValueError."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    version, commit, outcome = payload.get("version"), payload.get("commit"), payload.get("outcome")
    if not (isinstance(version, str) and _VERSION_RE.match(version)):
        raise ValueError("version must be short printable ASCII with no spaces")
    if not (isinstance(commit, str) and _FULL_COMMIT_RE.match(commit)):
        raise ValueError("commit must be a full hex sha")
    if outcome not in RELEASE_OUTCOMES:
        raise ValueError("outcome must be verified or rolled_back")
    return version, commit, outcome


def adapt_release_envelope(raw, ctx):
    """One release-result/v1 envelope's text, already checked against the contract by
    the caller (intake sync). A rolled_back release gives one release signal keyed by
    version, the same key and evidence key as release-status. A verified one gives none:
    sync uses it to close the loop."""
    try:
        st = json.loads(raw)
        pred = st["predicate"]
        version, _commit, outcome = release_result(pred["payload"])
        at = parse_time(pred["generatedAtTime"])
    except ValueError as e:
        return [], ["release-result: %s" % e]
    except (TypeError, KeyError, RecursionError):
        return [], ["release-result: not an envelope"]
    if outcome != "rolled_back":
        return [], []
    line = "RELEASE: %s rolled_back (release-result %s)" % (version, pred.get("id"))
    return [_signal(ctx, version, "", "release", "Release %s rolled_back" % version,
                    _RELEASE_SEVERITY["rolled_back"], at, at, "normal",
                    [_evidence(ctx, version, line, "%s rolled_back" % version)])], []


# -- git-rev-list -----------------------------------------------------------------
_SHA40_RE = re.compile(r"^[0-9a-f]{40}\Z")


def adapt_git_rev_list(raw, ctx):
    """`git rev-list <commit>` output, one 40-hex sha per line. It gives no signal: the
    shas, in git's order (newest first) and without repeats, go to ctx["history_out"]
    for loop closing. A bad line is a problem."""
    shas, problems, seen = [], [], set()
    for n, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not _SHA40_RE.match(line):
            problems.append("line %d: not a 40-hex sha" % n)
        elif line not in seen:
            seen.add(line)
            shas.append(line)
    ctx["history_out"] = shas
    return [], problems


ADAPTERS = {
    "release-envelope": adapt_release_envelope,
    "git-rev-list": adapt_git_rev_list,
    "gh-issues-json": adapt_gh_issues,
    "gh-runs-json": adapt_gh_runs,
    "gh-run-jobs-json": adapt_gh_run_jobs,
    "intake-signals-jsonl": adapt_jsonl,
    "feedback-jsonl": adapt_feedback,
    "release-status": adapt_release_status,
}

FORMAT_HELP = {
    "release-envelope": "release-conductor release-result/v1 envelopes under "
                        ".skill-contract/envelopes/, read by sync",
    "release-status": "release-conductor's status command, lines RELEASE: <version> <status>",
    "gh-issues-json": "gh issue list --repo OWNER/REPO --state all --limit 100 --json "
                      "number,title,body,labels,state,createdAt,updatedAt,url",
    "gh-runs-json": "gh run list --branch <default> --json databaseId,workflowName,headBranch,"
                    "headSha,event,status,conclusion,createdAt,updatedAt,url,attempt",
    "gh-run-jobs-json": "gh run view <run id> --json jobs, imported with --run <run id>",
    "git-rev-list": "git rev-list <release commit>, imported with --commit <release commit>",
    "intake-signals-jsonl": "your own transform: one signal per line (lower trust)",
    "feedback-jsonl": "your transform of an inbox, review or form export, one message per "
                      "line (lower trust)",
}
