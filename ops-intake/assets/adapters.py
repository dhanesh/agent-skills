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

_TIME_RE = re.compile(
    r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d|[+-]\d\d\d\d)\Z")
_SEMVER_RE = re.compile(
    r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?\Z")
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
    except ValueError:
        return [], ["input is not valid JSON"]
    if not isinstance(data, list):
        return [], ["input must be a JSON array"]
    sigs, problems = [], []
    for i, rec in enumerate(data):
        try:
            sigs.append(_one_issue(rec, ctx))
        except ValueError as e:
            problems.append("record %d: %s" % (i, e))
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
    if not isinstance(rec.get("url", ""), str):
        raise ValueError("url must be a string")
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
        if not isinstance(key, str):
            raise ValueError("evidence key must be a string")
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
        except ValueError as e:
            problems.append("line %d: %s" % (n, e))
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


# Formats with no adapter yet (tasks 3 and 4) are absent here, so import stops on them.
ADAPTERS = {
    "gh-issues-json": adapt_gh_issues,
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
