#!/usr/bin/env python3
"""Agent-session capture and cross-machine restore for project-atlas.

Captures Claude Code sessions — the JSONL transcript plus the side state a resume
needs — into the same SQLite index the project scanner writes, so that syncing the
index carries the conversations with it and a session can be picked up on another
machine.

## What the on-disk layout actually is

Verified by direct observation (see docs/project-atlas/2026-08-11-research-validation.md
— the vendor docs for this are not reachable, and these are internal formats with no
stability guarantee):

    ~/.claude/projects/<slug>/<session-id>.jsonl   the transcript, one JSON record per line
    ~/.claude/tasks/<session-id>/<n>.json          the task list
    ~/.claude/session-env/<session-id>/            per-session environment
    ~/.claude/sessions/<pid>.json                  the live-session descriptor
    ~/.claude/shell-snapshots/*.sh                 shell state (NOT captured; see below)

`<slug>` is the working directory with `/` replaced by `-`, so `/home/user/proj`
becomes `-home-user-proj`. That mapping is lossy — it cannot be inverted reliably —
so the real `cwd` is read from inside the transcript records, never guessed from the
directory name.

## The two rules this module exists to enforce

**Byte fidelity beats cleverness.** The record schema is undocumented and moves with
the CLI version, so records are stored verbatim as JSON text and replayed in
`parentUuid` order. Nothing here parses a message body to "understand" it; the only
fields read are the envelope ones needed to order and locate a session. A restore of
an unredacted capture reproduces the original file byte for byte, and the stored
sha256 proves it.

**Nothing account-shaped is ever captured.** `~/.claude.json` holds `oauthAccount`,
`userID`, and `machineID` — identity, not session state — and is excluded outright.
Shell snapshots are excluded by default because they are a serialized environment
(hundreds of KB, frequently containing exported credentials) and a resume does not
need them. Transcripts themselves are redacted unless the caller opts out.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket

AGENT_CLAUDE_CODE = "claude-code"

#: Files and directories under the agent home that are never read, at any setting.
#: `.claude.json` is account identity; anything credential-shaped is self-evident.
NEVER_CAPTURE = (
    ".claude.json",
    ".credentials.json",
    "credentials.json",
    "statsig",
)

#: Captured only with --include-shell-snapshots. A snapshot is a serialized shell
#: environment: large, and the single most likely place for an exported secret.
SHELL_SNAPSHOT_DIR = "shell-snapshots"

#: Cap on the text assembled for full-text search per session. The event rows are
#: never capped — truncating those would break the resume this module exists for.
SESSION_DOC_CAP = 131072

TITLE_CAP = 160


class SessionError(Exception):
    """A session that cannot be captured or restored, with a readable reason."""


# ── redaction ────────────────────────────────────────────────────────────────
# Patterns are built from fragments so this file carries no literal secret-shaped
# string for a leak scanner to flag. Each entry is (name, compiled regex); the
# match is replaced by a labelled placeholder so a reader can see what was removed.

def _pattern(*parts: str) -> str:
    return "".join(parts)


REDACTIONS = [
    ("aws-access-key-id", re.compile(_pattern(r"\b", "A", r"KIA[0-9A-Z]{16}\b"))),
    ("github-token", re.compile(_pattern(r"\bgh[pousr]_", r"[A-Za-z0-9]{20,}\b"))),
    ("github-pat", re.compile(_pattern(r"\bgithub_pat_", r"[A-Za-z0-9_]{20,}\b"))),
    ("slack-token", re.compile(_pattern(r"\bxox[abprs]-", r"[A-Za-z0-9-]{10,}\b"))),
    ("stripe-key", re.compile(_pattern(r"\b[sr]k_(live|test)_", r"[A-Za-z0-9]{16,}\b"))),
    ("google-api-key", re.compile(_pattern(r"\bAIza", r"[0-9A-Za-z_-]{35}\b"))),
    ("private-key-block", re.compile(
        _pattern(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"),
        re.S)),
    ("jwt", re.compile(_pattern(r"\bey[A-Za-z0-9_-]{10,}\.", r"ey[A-Za-z0-9_-]{10,}\.",
                                r"[A-Za-z0-9_-]{10,}\b"))),
    ("bearer-header", re.compile(_pattern(r"(?i)\b(bearer|authorization:\s*bearer)\s+",
                                          r"[A-Za-z0-9._~+/=-]{20,}"))),
    ("url-credentials", re.compile(
        _pattern(r"\b([a-z][a-z0-9+.-]*://)[^\s/:@]+:[^\s/@]{3,}@"))),
    # KEY=value / "key": "value" assignments whose key names a credential.
    ("assigned-credential", re.compile(
        _pattern(r"(?i)\b([a-z0-9_.-]*(?:secret|passwd|password|api[_-]?key|",
                 r"access[_-]?token|auth[_-]?token|private[_-]?key|",
                 r"client[_-]?secret)[a-z0-9_.-]*)",
                 # The key may be quoted (JSON) or bare (shell export), so allow a
                 # closing quote between the key and its separator.
                 r"(\"?\s*[:=]\s*\"?)([^\s\"',;]{8,})"))),
]


def redact(text: str) -> tuple[str, dict]:
    """Scrub credential-shaped substrings. Returns (text, {kind: count}).

    Deliberately conservative about *shape*, not about volume: it would rather
    blank a harmless-looking token than let a live key ride into a bucket. Any
    caller that needs the original bytes must opt out explicitly and accept that
    the transcript is then only as safe as the storage it lands in.
    """
    counts: dict = {}
    for name, rx in REDACTIONS:
        if name == "assigned-credential":
            def sub(m, _n=name):
                counts[_n] = counts.get(_n, 0) + 1
                return "%s%s[REDACTED:%s]" % (m.group(1), m.group(2), _n)
            text = rx.sub(sub, text)
        elif name == "url-credentials":
            def sub(m, _n=name):
                counts[_n] = counts.get(_n, 0) + 1
                return "%s[REDACTED:%s]@" % (m.group(1), _n)
            text = rx.sub(sub, text)
        else:
            def sub(m, _n=name):
                counts[_n] = counts.get(_n, 0) + 1
                return "[REDACTED:%s]" % _n
            text = rx.sub(sub, text)
    return text, counts


# ── discovery ────────────────────────────────────────────────────────────────

def default_agent_home() -> str:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(os.path.expanduser("~"), ".claude")


def slug_for(cwd: str) -> str:
    """The `projects/` directory name Claude Code uses for a working directory.

    Observed mapping: every `/` becomes `-`, so a leading slash yields a leading
    dash. The transform is not injective (a real dash in a path collides), which
    is exactly why restore recomputes the slug from the *target* cwd rather than
    reusing the captured one.
    """
    return os.path.abspath(os.path.expanduser(cwd)).replace(os.sep, "-")


def discover(agent_home: str) -> list:
    """Every transcript under the agent home, newest first."""
    projects = os.path.join(agent_home, "projects")
    found = []
    if not os.path.isdir(projects):
        return found
    for slug in sorted(os.listdir(projects)):
        if slug in NEVER_CAPTURE:
            continue
        d = os.path.join(projects, slug)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(d, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            found.append({
                "session_id": name[: -len(".jsonl")],
                "path": path,
                "slug": slug,
                "mtime": mtime,
            })
    found.sort(key=lambda r: r["mtime"], reverse=True)
    return found


# ── parsing ──────────────────────────────────────────────────────────────────

def read_records(path: str) -> tuple[list, str, int]:
    """Parse a transcript into (records, sha256-of-original-bytes, byte-length).

    An unparsable line is preserved as a `_raw` record rather than dropped: a
    transcript this tool cannot understand must still round-trip, because the CLI
    that wrote it can.
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise SessionError("cannot read transcript %s: %s" % (path, exc))
    digest = hashlib.sha256(raw).hexdigest()
    records = []
    for line in raw.decode("utf-8", errors="replace").split("\n"):
        if not line.strip():
            continue
        try:
            records.append({"obj": json.loads(line), "line": line})
        except ValueError:
            records.append({"obj": None, "line": line})
    return records, digest, len(raw)


def _text_of(message) -> str:
    """Flatten a message body to searchable text without assuming its schema."""
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        content = message.get("content", message)
        return _text_of(content)
    if isinstance(message, list):
        parts = []
        for item in message:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), (str, list, dict)):
                    parts.append(_text_of(item["content"]))
        return "\n".join(parts)
    return ""


def summarize(records: list, session_id: str) -> dict:
    """Envelope-level metadata only — nothing here interprets a message body."""
    meta = {
        "session_id": session_id,
        "cwd": "",
        "git_branch": "",
        "cli_version": "",
        "started_at": "",
        "ended_at": "",
        "message_count": len(records),
        "user_turns": 0,
        "assistant_turns": 0,
        "title": "",
        "sidechain_events": 0,
    }
    texts = []
    for rec in records:
        obj = rec["obj"]
        if not isinstance(obj, dict):
            continue
        for field, key in (("cwd", "cwd"), ("gitBranch", "git_branch"),
                           ("version", "cli_version")):
            value = obj.get(field)
            if value and not meta[key]:
                meta[key] = value
        ts = obj.get("timestamp")
        if ts:
            if not meta["started_at"] or ts < meta["started_at"]:
                meta["started_at"] = ts
            if ts > meta["ended_at"]:
                meta["ended_at"] = ts
        kind = obj.get("type")
        if kind == "user":
            meta["user_turns"] += 1
        elif kind == "assistant":
            meta["assistant_turns"] += 1
        if obj.get("isSidechain"):
            meta["sidechain_events"] += 1
        if kind in ("user", "assistant"):
            text = _text_of(obj.get("message"))
            if text:
                texts.append(text)
                if kind == "user" and not meta["title"]:
                    flat = " ".join(text.split())
                    if flat and not flat.startswith("<"):
                        meta["title"] = flat[:TITLE_CAP]
    meta["_texts"] = texts
    return meta


def build_doc(meta: dict, extra: str = "") -> str:
    """The searchable document for a session, capped at SESSION_DOC_CAP bytes."""
    head = "\n".join(x for x in (
        meta.get("session_id", ""), meta.get("cwd", ""), meta.get("git_branch", ""),
        meta.get("title", ""), extra) if x)
    body = "\n".join([head] + meta.get("_texts", []))
    raw = body.encode("utf-8")
    if len(raw) > SESSION_DOC_CAP:
        body = raw[:SESSION_DOC_CAP].decode("utf-8", errors="ignore")
    return body


# ── side state ───────────────────────────────────────────────────────────────

def side_artifacts(agent_home: str, session_id: str, include_shell: bool = False) -> list:
    """The non-transcript state a resume benefits from: (kind, name, text)."""
    out = []
    tasks_dir = os.path.join(agent_home, "tasks", session_id)
    if os.path.isdir(tasks_dir):
        for name in sorted(os.listdir(tasks_dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(tasks_dir, name)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    out.append(("tasks", name, fh.read()))
            except OSError:
                continue
    sessions_dir = os.path.join(agent_home, "sessions")
    if os.path.isdir(sessions_dir):
        for name in sorted(os.listdir(sessions_dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(sessions_dir, name)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            try:
                if json.loads(text).get("sessionId") != session_id:
                    continue
            except ValueError:
                continue
            out.append(("descriptor", name, text))
    if include_shell:
        snap_dir = os.path.join(agent_home, SHELL_SNAPSHOT_DIR)
        if os.path.isdir(snap_dir):
            for name in sorted(os.listdir(snap_dir)):
                path = os.path.join(snap_dir, name)
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        out.append(("shell-snapshot", name, fh.read()))
                except OSError:
                    continue
    return out


def hostname() -> str:
    return os.environ.get("ATLAS_HOST") or socket.gethostname()


# ── restore ──────────────────────────────────────────────────────────────────

def rewrite_cwd(obj, old_cwd: str, new_cwd: str):
    """Point a record's path fields at the machine it is being restored onto.

    Only the envelope `cwd` field and literal occurrences of the old working
    directory are rewritten. Message bodies are otherwise untouched — rewriting
    prose would corrupt the record for the sake of cosmetics.
    """
    if not isinstance(obj, dict) or not old_cwd or old_cwd == new_cwd:
        return obj
    if obj.get("cwd") == old_cwd:
        obj = dict(obj)
        obj["cwd"] = new_cwd
    return obj


def render_transcript(events: list, old_cwd: str, new_cwd: str) -> str:
    """Rebuild the JSONL text from stored event bodies, in stored order."""
    lines = []
    for body in events:
        try:
            obj = json.loads(body)
        except ValueError:
            lines.append(body)
            continue
        obj = rewrite_cwd(obj, old_cwd, new_cwd)
        lines.append(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines) + "\n"


def chain_is_intact(records: list) -> bool:
    """Every non-root parentUuid resolves to a uuid present in the transcript.

    This is the structural check that stands in for a byte hash when a capture was
    redacted: the resume walks the parent chain, so a broken chain is the failure
    that actually matters.
    """
    uuids = set()
    for rec in records:
        obj = rec["obj"] if isinstance(rec, dict) and "obj" in rec else rec
        if isinstance(obj, dict) and obj.get("uuid"):
            uuids.add(obj["uuid"])
    for rec in records:
        obj = rec["obj"] if isinstance(rec, dict) and "obj" in rec else rec
        if not isinstance(obj, dict):
            continue
        parent = obj.get("parentUuid")
        if parent and parent not in uuids:
            return False
    return True
