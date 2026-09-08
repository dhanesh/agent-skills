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

Self-pinning: when the bundle root lives inside a pinned git repo (e.g.
`docs/knowledge/` in the very repo it explains), committing or editing the
bundle's own files moves HEAD / dirties the worktree without the *explained*
source having changed. Drift detection therefore ignores changes confined to
the bundle root — only changes outside it make a git source STALE.

Stdlib only — no pip, no network. Examples:

    python3 okf.py init "ingest pipeline" --root knowledge --source /path/to/repo
    python3 okf.py status ingest-pipeline --root knowledge
    python3 okf.py diff ingest-pipeline --root knowledge
    python3 okf.py pin ingest-pipeline --root knowledge
    python3 okf.py list --root knowledge
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import stat
import re
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


# ── Producer-side OKF conformance (validated at the WRITE boundary) ──────────
#
# OKF v0.1 splits its rules by role: a CONSUMER "MUST tolerate" missing/unknown
# fields and "degrade, don't fail" (which is why okf-site-kit renders a
# `type`-less concept as a generic Concept with a WARN). A PRODUCER has the
# opposite duty — it must not CREATE a bundle that violates the spec. This
# module is the producer, so the contract is enforced here, at the single write
# choke point, instead of surfacing later as a renderer warning (or never, if
# the bundle is never rendered).
#
# Deliberately STRUCTURAL, not a vocabulary: the spec says `type` is "a short,
# producer-chosen string (no central registry)", so its VALUE is never
# constrained — only its presence and shape. Imposing a closed type vocabulary
# here would itself violate OKF.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:?\d{2})?)?$")
# Exactly what `_read_pinned_sources` requires to run a drift check — so the write
# side refuses precisely what the read side would later reject. NOT `fingerprint`:
# that is legitimately null for `external` sources (a URL has no local fingerprint).
_PIN_KEYS = ("type", "locator")


class OkfSpecError(ValueError):
    """A concept write would have produced a spec-violating or unparseable OKF
    file. The message lists every problem so the caller can fix them in one pass."""


def _unserializable(value):
    """True if a scalar cannot survive this module's restricted frontmatter
    round-trip — a newline ends the key/value line, and a bare '---' would close
    the frontmatter block early, silently truncating every downstream parse."""
    if value is None or isinstance(value, (int, float, bool)):
        return False
    text = str(value)
    return "\n" in text or "\r" in text or text.strip() == "---"


def validate_concept_meta(meta, rel=None):
    """Return a list of producer-contract problems with `meta` (empty == valid)."""
    where = " in %s" % rel if rel else ""
    problems = []
    if not isinstance(meta, dict):
        return ["frontmatter must be a mapping%s" % where]

    # Spec: `type` is REQUIRED. The value is producer-chosen and never checked.
    ctype = meta.get("type")
    if ctype is None or (isinstance(ctype, str) and not ctype.strip()):
        problems.append(
            "missing required `type`%s — OKF v0.1 requires it on every concept "
            "(any short producer-chosen string, e.g. 'Explainer')" % where)
    elif not isinstance(ctype, str):
        problems.append("`type` must be a string%s, got %s"
                        % (where, type(ctype).__name__))

    # The parser sets this when it could only partially read a file. Writing it
    # back would persist a half-understood round-trip as if it were canonical.
    if "_okf_parse_error" in meta:
        problems.append(
            "refusing to write back a partially-parsed concept%s: fix the "
            "frontmatter by hand, then re-run" % where)

    for key, value in meta.items():
        if not isinstance(key, str) or not key.strip() or _unserializable(key):
            problems.append("unusable frontmatter key %r%s" % (key, where))
            continue
        if key == "tags":
            if not isinstance(value, list) or any(
                    not isinstance(t, str) or not t.strip() or "," in t
                    for t in value):
                problems.append(
                    "`tags` must be a list of comma-free strings%s (this "
                    "frontmatter subset serialises them as `[a, b]`)" % where)
        elif key == "sources":
            # Load-bearing: knowledge-gardener reads these pins to detect drift.
            # A malformed pin silently disables drift detection for the subject.
            if not isinstance(value, list):
                problems.append("`sources` must be a list of pins%s" % where)
            else:
                for i, pin in enumerate(value):
                    if not isinstance(pin, dict):
                        problems.append("source pin %d is not a mapping%s" % (i, where))
                        continue
                    missing = [k for k in _PIN_KEYS
                               if not str(pin.get(k) or "").strip()]
                    if missing:
                        problems.append("source pin %d missing %s%s"
                                        % (i, "/".join(missing), where))
        elif key == "timestamp":
            if not isinstance(value, str) or not _ISO_DATE_RE.match(value.strip()):
                problems.append("`timestamp` must be ISO 8601%s, got %r" % (where, value))
        elif isinstance(value, list):
            if any(_unserializable(v) for v in value if not isinstance(v, dict)):
                problems.append("`%s` has an unserialisable entry%s" % (key, where))
        elif _unserializable(value):
            problems.append(
                "`%s` value cannot round-trip%s (contains a newline or a bare "
                "'---', which would truncate the frontmatter)" % (key, where))
    return problems


def write_concept(path, meta, body, validate=True):
    """Write a concept file. Validates the PRODUCER contract first (see
    `validate_concept_meta`); pass `validate=False` only to write a fixture
    deliberately, never to silence a real problem."""
    if validate:
        problems = validate_concept_meta(meta, os.path.basename(path))
        if problems:
            raise OkfSpecError(
                "refusing to write a spec-violating OKF concept:\n  - "
                + "\n  - ".join(problems))
    with open(path, "w", encoding="utf-8") as f:
        f.write(format_frontmatter(meta) + "\n" + body)


# ── Source fingerprints ──────────────────────────────────────────────────────

def _sha256_file(path):
    """Content hash of one file, or a stable sentinel when it is not hashable.

    Anything that is not a readable REGULAR file gets a sentinel rather than an
    open(): a source tree routinely contains a socket, a fifo, a dangling
    symlink or a mode-000 file, and each was a different failure here. An
    OSError took the whole fingerprint down with a traceback (`okf.py init
    --source <dir>` on a directory holding a stray socket), and a fifo was
    worse — open() BLOCKS on it waiting for a writer, hanging the fingerprint
    indefinitely with no error at all. stat() first, so neither can happen.

    The sentinel keeps the hash deterministic while staying sensitive to the
    entry appearing, disappearing, or changing kind.
    """
    try:
        st = os.stat(path, follow_symlinks=True)
    except OSError as exc:
        return "unstattable:%s" % type(exc).__name__
    if not stat.S_ISREG(st.st_mode):
        return "nonregular:%o" % stat.S_IFMT(st.st_mode)
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError as exc:
        return "unreadable:%s" % type(exc).__name__
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


def _repo_toplevel(locator):
    """Real path of the repo's working-tree root, or None outside a repo."""
    probe = _git(["rev-parse", "--show-toplevel"], cwd=locator)
    if probe.returncode != 0 or not probe.stdout.strip():
        return None
    return os.path.realpath(probe.stdout.strip())


def _bundle_rel_in_repo(bundle_root, repo_top):
    """Bundle root's repo-relative posix path, or None when not inside.

    None disables self-pin filtering: the bundle lives outside the repo (or
    IS the repo root, where excluding it would hide every change).
    """
    if not bundle_root or not repo_top:
        return None
    rel = os.path.relpath(os.path.realpath(bundle_root), repo_top)
    if rel == "." or rel == ".." or rel.startswith(".." + os.sep):
        return None
    return rel.replace(os.sep, "/")


def _porcelain_paths(porcelain_stdout):
    """Repo-relative paths from `git status --porcelain` (both rename sides)."""
    paths = []
    for line in porcelain_stdout.splitlines():
        if len(line) < 4:
            continue
        for part in line[3:].split(" -> "):
            part = part.strip().strip('"').rstrip("/")
            if part:
                paths.append(part)
    return paths


def _outside_bundle(paths, bundle_rel):
    """Filter out paths under the bundle root (repo-relative posix paths)."""
    if bundle_rel is None:
        return list(paths)
    prefix = bundle_rel + "/"
    return [p for p in paths
            if p.rstrip("/") != bundle_rel and not p.startswith(prefix)]


def fingerprint(locator, source_type, bundle_root=None):
    """Current fingerprint of a source, or None when it can't be computed.

    git -> HEAD sha (+"+dirty" if the worktree has changes); file -> sha256;
    dir -> sha256 over sorted (relpath, content-sha) pairs, .git excluded;
    external -> None (a URL or topic has no local fingerprint).

    For git sources, `bundle_root` (when it resolves inside the repo) keeps
    the bundle's own uncommitted files from counting as dirt — writing the
    explainer into the repo it documents must not taint the pin.
    """
    if source_type == "git":
        head = _git(["rev-parse", "HEAD"], cwd=locator)
        if head.returncode != 0:
            return None
        fp = head.stdout.strip()
        porcelain = _git(["status", "--porcelain", "--untracked-files=all"],
                     cwd=locator)
        if porcelain.returncode == 0:
            bundle_rel = _bundle_rel_in_repo(bundle_root, _repo_toplevel(locator))
            dirty = _outside_bundle(_porcelain_paths(porcelain.stdout),
                                    bundle_rel)
            if dirty:
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


def git_drift(locator, pinned_fp, bundle_root=None):
    """Classify a git source against its pinned fingerprint.

    Returns (state, current_fp, committed, dirty): state is FRESH/STALE/
    UNKNOWN; committed is the sorted `git diff --name-status` lines between
    the pinned SHA and HEAD; dirty is the sorted worktree-change paths — both
    with everything under the bundle root filtered out when the bundle lives
    inside the repo (self-pin: committing the bundle must not STALE it).

    A fingerprint pinned while the worktree was dirty ("<sha>+dirty")
    compares by its SHA part — the dirty content it saw was never
    fingerprintable, so only committed/current drift can be judged.
    """
    current = fingerprint(locator, "git", bundle_root)
    if current is None:
        return "UNKNOWN", None, [], []
    if current == pinned_fp:
        return "FRESH", current, [], []
    pinned_sha = pinned_fp.split("+", 1)[0]
    bundle_rel = _bundle_rel_in_repo(bundle_root, _repo_toplevel(locator))
    diff = _git(["diff", "--name-status", "%s..HEAD" % pinned_sha],
                cwd=locator)
    if diff.returncode != 0:
        # pinned SHA no longer resolvable (rewritten history): drifted.
        return "STALE", current, [], []
    committed = []
    for line in diff.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        if _outside_bundle([f for f in fields[1:] if f], bundle_rel):
            committed.append(line)
    committed.sort(key=lambda l: l.split("\t")[1:])
    porcelain = _git(["status", "--porcelain", "--untracked-files=all"],
                     cwd=locator)
    dirty = []
    if porcelain.returncode == 0:
        dirty = sorted(set(_outside_bundle(
            _porcelain_paths(porcelain.stdout), bundle_rel)))
    state = "STALE" if (committed or dirty) else "FRESH"
    return state, current, committed, dirty


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

def _pin_sources(locators, today, bundle_root=None):
    pins = []
    for locator in locators:
        stype = detect_source_type(locator)
        pins.append({
            "type": stype,
            "locator": os.path.abspath(locator) if stype != "external" else locator,
            "fingerprint": fingerprint(locator, stype, bundle_root),
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
    pins = _pin_sources(sources, today, bundle_root=root)
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
    _read_pinned_sources(subject_dir)  # raises ValueError on corrupt pins
    meta, body = read_concept(path)
    for source in meta.get("sources", []):
        source["fingerprint"] = fingerprint(source["locator"], source["type"],
                                            bundle_root=root)
        source["pinned"] = today
    meta["timestamp"] = "%sT00:00:00Z" % today
    write_concept(path, meta, body)
    append_log(root, today,
               "**Update**: Re-pinned sources for [%s](/%s/%s)."
               % (meta.get("title", os.path.basename(subject_dir)),
                  os.path.basename(subject_dir), EXPLAINER))
    return meta


def _read_pinned_sources(subject_dir):
    """The explainer's meta + validated `sources` pins.

    Raises ValueError (not a traceback-worthy KeyError later) when the
    explainer frontmatter is corrupt: missing/unparseable frontmatter, or
    source pins without the `type`/`locator` keys drift checks depend on.
    """
    path = os.path.join(subject_dir, EXPLAINER)
    meta, _ = read_concept(path)
    if not meta.get("type"):
        raise ValueError(
            "corrupt explainer frontmatter (missing/unparseable, no `type`): %s"
            % path)
    sources = meta.get("sources") or []
    if not isinstance(sources, list) or any(
            not isinstance(s, dict) or not s.get("type") or not s.get("locator")
            for s in sources):
        raise ValueError(
            "corrupt `sources` pins (each needs `type` and `locator`): %s"
            % path)
    return meta, sources


def diff_okf(subject_dir, bundle_root=None):
    """Return (overall, entries): per-source drift detail for a subject.

    Each entry is {source, pinned, current, state, committed, dirty}; for git
    sources `committed` holds `git diff --name-status <pinned>..HEAD` lines
    and `dirty` the worktree-change paths, both excluding the bundle root
    when it lives inside the repo (see `git_drift`). Non-git sources carry
    just their state. Overall is STALE if any source drifted, else UNKNOWN
    if any can't be fingerprinted, else FRESH.
    """
    if bundle_root is None:
        bundle_root = os.path.dirname(os.path.abspath(subject_dir))
    _, sources = _read_pinned_sources(subject_dir)
    entries = []
    for source in sources:
        pinned = source.get("fingerprint")
        entry = {"source": source, "pinned": pinned, "current": None,
                 "state": "UNKNOWN", "committed": [], "dirty": []}
        if source["type"] == "external" or pinned is None:
            entries.append(entry)
            continue
        stype = detect_source_type(source["locator"])
        if stype == "git":
            state, current, committed, dirty = git_drift(
                source["locator"], pinned, bundle_root)
            entry.update(state=state, current=current,
                         committed=committed, dirty=dirty)
        else:
            current = fingerprint(source["locator"], stype)
            entry["current"] = current
            if current is None:
                entry["state"] = "UNKNOWN"
            else:
                entry["state"] = "FRESH" if current == pinned else "STALE"
        entries.append(entry)
    states = [e["state"] for e in entries]
    if "STALE" in states:
        overall = "STALE"
    elif "UNKNOWN" in states:
        overall = "UNKNOWN"
    else:
        overall = "FRESH"  # includes the no-sources case: nothing to drift
    return overall, entries


def status_okf(subject_dir, bundle_root=None):
    """Return (overall, [(source, state), ...]) — states FRESH/STALE/UNKNOWN.

    Overall is STALE if any source drifted, else UNKNOWN if any source can't
    be fingerprinted (external URL/topic, or a moved path), else FRESH.
    Changes confined to the bundle root itself (default: the subject dir's
    parent) never count as git drift — see `git_drift`.
    """
    overall, entries = diff_okf(subject_dir, bundle_root)
    return overall, [(e["source"], e["state"]) for e in entries]


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
    # `--root` is accepted BEFORE or AFTER the subcommand. A top-level-only
    # option cannot follow its subcommand, so every documented invocation of the
    # form `okf.py status <subject> --root <dir>` exited 2 with "unrecognized
    # arguments" — including all five examples in references/okf.md, the file
    # SKILL.md sends the agent to for the full flows. Both orders now work, and
    # argparse's SUPPRESS default lets the later value win without clobbering
    # the earlier one with a default.
    ROOT_HELP = "OKF bundle root (default: knowledge)"
    parser.add_argument("--root", default="knowledge", help=ROOT_HELP)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_root(p):
        """Accept --root after the subcommand too; SUPPRESS keeps the pre-command
        value when this one is absent."""
        p.add_argument("--root", default=argparse.SUPPRESS, help=ROOT_HELP)
        return p

    p_init = add_root(sub.add_parser("init", help="create a subject in the bundle"))
    p_init.add_argument("subject", help="human-readable subject name")
    p_init.add_argument("--source", action="append", default=[],
                        help="repo dir, file, or URL/topic (repeatable)")

    p_pin = add_root(sub.add_parser("pin", help="re-fingerprint sources after a refresh"))
    p_pin.add_argument("subject", help="slug or subject name")

    p_status = add_root(sub.add_parser("status", help="report drift vs pinned fingerprints"))
    p_status.add_argument("subject", nargs="?", default=None,
                          help="slug or subject name (default: all)")

    p_diff = add_root(sub.add_parser(
        "diff", help="pinned vs current fingerprints + changed-file list"))
    p_diff.add_argument("subject", help="slug or subject name")

    add_root(sub.add_parser("list", help="list subjects in the bundle"))

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
            meta = pin_okf(args.root, subject_dir)
        except (FileNotFoundError, ValueError) as exc:
            parser.error(str(exc))
        out.write("OKF_PINNED: %s (%d source(s))\n"
                  % (subject_dir, len(meta.get("sources", []))))
        return 0

    bundle_root = os.path.abspath(args.root)

    if args.command == "diff":
        try:
            subject_dir = _resolve_subject_dir(args.root, args.subject)
            overall, entries = diff_okf(subject_dir, bundle_root)
        except (FileNotFoundError, ValueError) as exc:
            parser.error(str(exc))
        for entry in entries:
            source = entry["source"]
            out.write("SOURCE: %s %s -> %s\n"
                      % (source["type"], source["locator"], entry["state"]))
            if source["type"] == "git":
                out.write("  PINNED: %s\n" % entry["pinned"])
                out.write("  CURRENT: %s\n" % (entry["current"] or "unavailable"))
                for line in entry["committed"]:
                    out.write("  %s\n" % line)
                for path in entry["dirty"]:
                    out.write("  DIRTY\t%s\n" % path)
        out.write("OKF_DIFF: %s %s\n"
                  % (os.path.basename(subject_dir), overall))
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
        try:
            overall, rows = status_okf(subject_dir, bundle_root)
        except ValueError as exc:
            parser.error(str(exc))
        for source, state in rows:
            out.write("SOURCE: %s %s -> %s\n"
                      % (source["type"], source["locator"], state))
        out.write("OKF_STATUS: %s %s\n" % (os.path.basename(subject_dir), overall))
        if overall == "STALE":
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
