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
KINDS = ("release", "ci", "issue")


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
    if rec.get("kind") not in KINDS:
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
}
