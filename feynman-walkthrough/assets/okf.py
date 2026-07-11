#!/usr/bin/env python3
"""OKF bundle tool — persistent, source-pinned knowledge per explained subject.

Writes knowledge as an Open Knowledge Format (OKF) v0.1 bundle — Google's open
spec for agent-readable knowledge: a directory of markdown concept files with
YAML frontmatter (required field: `type`), reserved `index.md` (directory
listing) and `log.md` (update history), and `okf_version` declared in the
bundle-root index. Spec:
https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md

On top of plain OKF, each subject's explainer concept carries a
producer-defined `sources:` frontmatter key (extra keys are allowed by the
spec) pinning a fingerprint of every source the explanation was built from —
git HEAD for a repo, sha256 for a file/dir — so a later session can detect
that the source drifted (`status`) and re-pin after refreshing (`pin`).

Stdlib only — no pip, no network. Examples:

    python3 okf.py init "ingest pipeline" --root knowledge --source /path/to/repo
    python3 okf.py status ingest-pipeline --root knowledge
    python3 okf.py pin ingest-pipeline --root knowledge
    python3 okf.py list --root knowledge
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import subprocess
import sys

OKF_SPEC_VERSION = "0.1"
SPEC_URL = ("https://github.com/GoogleCloudPlatform/knowledge-catalog/"
            "blob/main/okf/SPEC.md")
INDEX = "index.md"
LOG = "log.md"
EXPLAINER = "explainer.md"
FAQ = "faq.md"

EXPLAINER_BODY = """# {subject}

> Reference explainer. Standalone: readable without the session that produced
> it. Check freshness against the pinned sources with `okf.py status` first.

## In one paragraph

(The plain-language Feynman version of the whole subject.)

## The map

(The 3-7 segments, in order, one line each on why the chunk matters.)

## Segments

(One section per segment: plain language first, precise terms second, one
concrete example or traced path, diagrams where structure needs them.)

## Glossary

(Each term the walkthrough introduced, one line each.)
"""

FAQ_BODY = """# {subject} — FAQ

Questions actually asked during walkthrough sessions, with the answers that
resolved them. These are the questions the next reader will have too.
"""


def slugify(text):
    """Lowercase kebab-case slug, max 64 chars, never empty."""
    out = []
    prev_dash = True  # suppress leading dashes
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    slug = "".join(out).strip("-")[:64].rstrip("-")
    return slug or "subject"


# ── YAML frontmatter (restricted subset) ─────────────────────────────────────
# OKF frontmatter is YAML; this tool reads/writes the subset it emits so it
# stays stdlib-only: scalar strings, `null`, inline string lists ([a, b]),
# and one level of block lists of flat dicts (the `sources:` pins). Unknown
# scalar keys round-trip untouched, as the spec requires of consumers.
# Multi-line scalars are out of scope — keep frontmatter values single-line.

def _scalar(value):
    value = value.strip()
    if value == "null":
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_frontmatter(text):
    """Split a concept file into (meta dict, body). No frontmatter -> ({}, text)."""
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        return {}, text
    try:
        end = lines[1:].index("---") + 1
    except ValueError:
        return {}, text
    meta, i = {}, 1
    while i < end:
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        key, _, rest = line.partition(":")
        key, rest = key.strip(), rest.strip()
        if rest == "" and i + 1 < end and lines[i + 1].lstrip().startswith("- "):
            items, i = [], i + 1
            while i < end and (lines[i].lstrip().startswith("- ")
                               or lines[i].startswith("  ")):
                entry = lines[i].lstrip()
                if entry.startswith("- "):
                    items.append({})
                    entry = entry[2:]
                k, _, v = entry.partition(":")
                items[-1][k.strip()] = _scalar(v)
                i += 1
            meta[key] = items
        else:
            if rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1].strip()
                meta[key] = ([_scalar(p) for p in inner.split(",")]
                             if inner else [])
            else:
                meta[key] = _scalar(rest)
            i += 1
    return meta, "\n".join(lines[end + 1:]).lstrip("\n")


def format_frontmatter(meta):
    lines = ["---"]
    for key, value in meta.items():
        if value is None:
            lines.append("%s: null" % key)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            lines.append("%s:" % key)
            for item in value:
                first = True
                for k, v in item.items():
                    prefix = "- " if first else "  "
                    lines.append("%s%s: %s" % (prefix, k,
                                               "null" if v is None else v))
                    first = False
        elif isinstance(value, list):
            lines.append("%s: [%s]" % (key, ", ".join(value)))
        else:
            lines.append("%s: %s" % (key, value))
    lines.append("---")
    return "\n".join(lines) + "\n"


def read_concept(path):
    with open(path, encoding="utf-8") as f:
        return parse_frontmatter(f.read())


def write_concept(path, meta, body):
    with open(path, "w", encoding="utf-8") as f:
        f.write(format_frontmatter(meta) + "\n" + body)


# ── Source fingerprints ──────────────────────────────────────────────────────

def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(args, cwd):
    return subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True, check=False
    )


def detect_source_type(locator):
    """git (repo dir), dir, file, or external (URL / plain topic)."""
    if os.path.isdir(locator):
        probe = _git(["rev-parse", "HEAD"], cwd=locator)
        return "git" if probe.returncode == 0 else "dir"
    if os.path.isfile(locator):
        return "file"
    return "external"


def fingerprint(locator, source_type):
    """Current fingerprint of a source, or None when it can't be computed.

    git -> HEAD sha (+"+dirty" if the worktree has changes); file -> sha256;
    dir -> sha256 over sorted (relpath, content-sha) pairs, .git excluded;
    external -> None (a URL or topic has no local fingerprint).
    """
    if source_type == "git":
        head = _git(["rev-parse", "HEAD"], cwd=locator)
        if head.returncode != 0:
            return None
        fp = head.stdout.strip()
        porcelain = _git(["status", "--porcelain"], cwd=locator)
        if porcelain.returncode == 0 and porcelain.stdout.strip():
            fp += "+dirty"
        return fp
    if source_type == "file":
        return _sha256_file(locator)
    if source_type == "dir":
        h = hashlib.sha256()
        for base, dirs, files in sorted(os.walk(locator)):
            dirs[:] = sorted(d for d in dirs if d != ".git")
            for name in sorted(files):
                path = os.path.join(base, name)
                rel = os.path.relpath(path, locator)
                h.update(rel.encode())
                h.update(_sha256_file(path).encode())
        return h.hexdigest()
    return None  # external


# ── Bundle bookkeeping (reserved files, per the spec) ────────────────────────

def _ensure_root_index(root, today):
    path = os.path.join(root, INDEX)
    if not os.path.exists(path):
        content = ('---\nokf_version: "%s"\n---\n\n# Subjects\n\n'
                   % OKF_SPEC_VERSION)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    _ = today  # creation date is recorded in log.md, not the index


def _add_to_root_index(root, slug, subject):
    path = os.path.join(root, INDEX)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    link = "(%s/)" % slug
    if link not in content:
        entry = "* [%s](%s/) - reference explainer and FAQ\n" % (subject, slug)
        content = content.rstrip("\n") + "\n" + entry
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


def append_log(root, today, entry):
    """Prepend under today's `## YYYY-MM-DD` heading, newest-first per spec."""
    path = os.path.join(root, LOG)
    heading = "## %s" % today
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = f.read().split("\n")
    else:
        lines = ["# Directory Update Log", ""]
    bullet = "* %s" % entry
    if heading in lines:
        lines.insert(lines.index(heading) + 1, bullet)
    else:
        first_date = next((i for i, l in enumerate(lines)
                           if l.startswith("## ")), len(lines))
        lines[first_date:first_date] = [heading, bullet, ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip("\n") + "\n")


def _subject_index_body(subject):
    return ("# %s\n\n"
            "* [Explainer](explainer.md) - the reference explainer\n"
            "* [FAQ](faq.md) - questions from walkthrough sessions\n"
            % subject)


# ── Commands ─────────────────────────────────────────────────────────────────

def _pin_sources(locators, today):
    pins = []
    for locator in locators:
        stype = detect_source_type(locator)
        pins.append({
            "type": stype,
            "locator": os.path.abspath(locator) if stype != "external" else locator,
            "fingerprint": fingerprint(locator, stype),
            "pinned": today,
        })
    return pins


def init_okf(root, subject, sources, today=None):
    """Create <root>/<slug>/ as OKF concepts + bundle bookkeeping."""
    today = (today or dt.date.today()).isoformat()
    slug = slugify(subject)
    subject_dir = os.path.join(root, slug)
    explainer_path = os.path.join(subject_dir, EXPLAINER)
    if os.path.exists(explainer_path):
        raise FileExistsError(
            "OKF subject already exists: %s (use `pin` to refresh it)"
            % subject_dir)
    os.makedirs(subject_dir, exist_ok=True)
    pins = _pin_sources(sources, today)
    meta = {
        "type": "Explainer",
        "title": subject,
        "description": "Reference explainer for %s" % subject,
        "tags": ["walkthrough", "reference"],
        "timestamp": "%sT00:00:00Z" % today,
        "created": today,
        "sources": pins,
    }
    if pins:
        meta["resource"] = pins[0]["locator"]
    write_concept(explainer_path, meta, EXPLAINER_BODY.format(subject=subject))
    write_concept(os.path.join(subject_dir, FAQ),
                  {"type": "FAQ", "title": "%s — FAQ" % subject,
                   "timestamp": "%sT00:00:00Z" % today},
                  FAQ_BODY.format(subject=subject))
    with open(os.path.join(subject_dir, INDEX), "w", encoding="utf-8") as f:
        f.write(_subject_index_body(subject))  # no frontmatter: reserved file
    _ensure_root_index(root, today)
    _add_to_root_index(root, slug, subject)
    append_log(root, today,
               "**Creation**: Established [%s](/%s/%s) (%d source(s) pinned)."
               % (subject, slug, EXPLAINER, len(pins)))
    return subject_dir


def pin_okf(root, subject_dir, today=None):
    """Re-fingerprint every pinned source; stamp timestamp; log it."""
    today = (today or dt.date.today()).isoformat()
    path = os.path.join(subject_dir, EXPLAINER)
    meta, body = read_concept(path)
    for source in meta.get("sources", []):
        source["fingerprint"] = fingerprint(source["locator"], source["type"])
        source["pinned"] = today
    meta["timestamp"] = "%sT00:00:00Z" % today
    write_concept(path, meta, body)
    append_log(root, today,
               "**Update**: Re-pinned sources for [%s](/%s/%s)."
               % (meta.get("title", os.path.basename(subject_dir)),
                  os.path.basename(subject_dir), EXPLAINER))
    return meta


def status_okf(subject_dir):
    """Return (overall, [(source, state), ...]) — states FRESH/STALE/UNKNOWN.

    Overall is STALE if any source drifted, else UNKNOWN if any source can't
    be fingerprinted (external URL/topic, or a moved path), else FRESH.
    """
    meta, _ = read_concept(os.path.join(subject_dir, EXPLAINER))
    rows = []
    for source in meta.get("sources", []):
        pinned = source.get("fingerprint")
        if source["type"] == "external" or pinned is None:
            rows.append((source, "UNKNOWN"))
            continue
        current = fingerprint(source["locator"],
                              detect_source_type(source["locator"]))
        if current is None:
            rows.append((source, "UNKNOWN"))
        elif current == pinned:
            rows.append((source, "FRESH"))
        else:
            rows.append((source, "STALE"))
    states = [state for _, state in rows]
    if "STALE" in states:
        overall = "STALE"
    elif "UNKNOWN" in states:
        overall = "UNKNOWN"
    else:
        overall = "FRESH"  # includes the no-sources case: nothing to drift
    return overall, rows


def list_okfs(root):
    """Return [(slug, title, timestamp), ...] for every subject under root."""
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name, EXPLAINER)
        if os.path.isfile(path):
            meta, _ = read_concept(path)
            out.append((name, meta.get("title", name),
                        meta.get("timestamp", "")))
    return out


def _resolve_subject_dir(root, slug_or_subject):
    for candidate in (slug_or_subject, slugify(slug_or_subject)):
        subject_dir = os.path.join(root, candidate)
        if os.path.isfile(os.path.join(subject_dir, EXPLAINER)):
            return subject_dir
    raise FileNotFoundError(
        "no OKF subject %r under %s (try `list`)" % (slug_or_subject, root))


def main(argv=None, out=None):
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default="knowledge",
                        help="OKF bundle root (default: knowledge)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="create a subject in the bundle")
    p_init.add_argument("subject", help="human-readable subject name")
    p_init.add_argument("--source", action="append", default=[],
                        help="repo dir, file, or URL/topic (repeatable)")

    p_pin = sub.add_parser("pin", help="re-fingerprint sources after a refresh")
    p_pin.add_argument("subject", help="slug or subject name")

    p_status = sub.add_parser("status", help="report drift vs pinned fingerprints")
    p_status.add_argument("subject", nargs="?", default=None,
                          help="slug or subject name (default: all)")

    sub.add_parser("list", help="list subjects in the bundle")

    args = parser.parse_args(argv)

    if args.command == "init":
        try:
            subject_dir = init_okf(args.root, args.subject, args.source)
        except FileExistsError as exc:
            parser.error(str(exc))
        out.write("OKF_CREATED: %s\n" % subject_dir)
        return 0

    if args.command == "list":
        for slug, title, timestamp in list_okfs(args.root):
            out.write("%s\t%s\t%s\n" % (slug, timestamp, title))
        return 0

    if args.command == "pin":
        try:
            subject_dir = _resolve_subject_dir(args.root, args.subject)
        except FileNotFoundError as exc:
            parser.error(str(exc))
        meta = pin_okf(args.root, subject_dir)
        out.write("OKF_PINNED: %s (%d source(s))\n"
                  % (subject_dir, len(meta.get("sources", []))))
        return 0

    # status
    if args.subject:
        try:
            subject_dirs = [_resolve_subject_dir(args.root, args.subject)]
        except FileNotFoundError as exc:
            parser.error(str(exc))
    else:
        subject_dirs = [os.path.join(args.root, slug)
                        for slug, _, _ in list_okfs(args.root)]
    exit_code = 0
    for subject_dir in subject_dirs:
        overall, rows = status_okf(subject_dir)
        for source, state in rows:
            out.write("SOURCE: %s %s -> %s\n"
                      % (source["type"], source["locator"], state))
        out.write("OKF_STATUS: %s %s\n" % (os.path.basename(subject_dir), overall))
        if overall == "STALE":
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
