#!/usr/bin/env python3
"""Garden sweep for Open Knowledge Format (OKF v0.1) bundles.

Finds OKF bundle roots (a directory whose index.md declares okf_version in
its frontmatter) under one or more knowledge roots, reads the `sources:`
pins in each subject's explainer.md, and reports drift per subject:
FRESH / STALE / UNKNOWN / ERROR.

Self-contained by design — skills install independently, so this tool does
not import feynman-walkthrough's okf.py — but its status semantics agree
with that tool exactly:

  git sources      pin HEAD (with a "+dirty" marker for uncommitted changes)
  file sources     pin a sha256 of the file's bytes
  dir sources      pin a sha256 over sorted (relpath, content-sha) pairs
                   (traversal byte-identical to okf.py's, so pins compare equal)
  external sources (URLs, plain topics) cannot be fingerprinted locally -> UNKNOWN

Per subject: STALE if any source drifted, else UNKNOWN if any source cannot
be fingerprinted, else FRESH (a subject with no pins has nothing to drift).
ERROR is the gardener's addition: an explainer whose frontmatter cannot be
parsed, or a source check that raises, is reported — never a crash.

For STALE git sources the report also shows pinned vs current SHA, a
`git diff --stat` summary, and the changed file names when the pinned
commit is still reachable locally.

Stdlib only — no pip, no network. Usage:

    python3 garden.py sweep <root> [<root> ...]   # report; exit 1 on any STALE/ERROR
    python3 garden.py json  <root> [<root> ...]   # machine-readable; same exit rule
"""
from __future__ import annotations

import argparse
import hashlib
import json as jsonlib
import os
import re
import subprocess
import sys

OKF_SPEC_VERSION = "0.1"
INDEX = "index.md"
EXPLAINER = "explainer.md"
DIRTY = "+dirty"


class SubjectError(Exception):
    """A subject that cannot be assessed (malformed frontmatter, bad pins)."""


# ── YAML frontmatter (same restricted subset okf.py writes) ──────────────────
# Scalar strings, `null`, inline string lists ([a, b]), and one level of
# block lists of flat dicts (the `sources:` pins). Where okf.py's reader is
# lenient (missing frontmatter parses to {}), the gardener is strict: a
# subject explainer without parseable frontmatter has lost its pins, and
# silently calling it FRESH would hide rot — so it raises SubjectError and
# the sweep reports ERROR for that subject.

def _scalar(value):
    value = value.strip()
    if value == "null":
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_frontmatter(text):
    """Parse a concept file's frontmatter into a dict; raise SubjectError if broken."""
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        raise SubjectError("no YAML frontmatter (file must open with ---)")
    try:
        end = lines[1:].index("---") + 1
    except ValueError:
        raise SubjectError("unterminated frontmatter (no closing ---)")
    meta, i = {}, 1
    while i < end:
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if ":" not in line:
            raise SubjectError("malformed frontmatter line: %r" % stripped)
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
                if ":" not in entry:
                    raise SubjectError(
                        "malformed frontmatter list entry: %r" % entry)
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
    return meta


def extract_sources(meta):
    """Validate and return the `sources:` pins; raise SubjectError if malformed."""
    sources = meta.get("sources")
    if sources in (None, ""):
        return []
    if not isinstance(sources, list) or not all(
            isinstance(s, dict) for s in sources):
        raise SubjectError("malformed sources: expected a block list of pins")
    for pin in sources:
        if not pin.get("type") or not pin.get("locator"):
            raise SubjectError(
                "malformed source pin (needs type and locator): %r" % (pin,))
    return sources


# ── Source fingerprints (identical semantics to okf.py) ──────────────────────

def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(args, cwd):
    try:
        return subprocess.run(
            ["git"] + args, cwd=cwd, capture_output=True, text=True,
            check=False)
    except (FileNotFoundError, OSError):
        return subprocess.CompletedProcess(
            args=["git"] + args, returncode=127, stdout="",
            stderr="git unavailable")


def detect_source_type(locator):
    """git (repo dir), dir, file, or external (URL / plain topic)."""
    if os.path.isdir(locator):
        probe = _git(["rev-parse", "HEAD"], cwd=locator)
        return "git" if probe.returncode == 0 else "dir"
    if os.path.isfile(locator):
        return "file"
    return "external"


# ── Self-pin exclusion (must agree with feynman-walkthrough/assets/okf.py) ───
# A bundle usually lives INSIDE the repo it documents — okf.md calls that the
# natural layout. Without this, committing the bundle dirties the pin of its own
# source, so every in-repo subject reported STALE forever, `sweep` exited 1
# permanently, and the two tools disagreed on identical input despite this
# skill claiming its verdicts "agree exactly" with okf.py's. Keep the two
# implementations in step; test_garden.py asserts they agree on a git source.

def _repo_toplevel(locator):
    probe = _git(["rev-parse", "--show-toplevel"], cwd=locator)
    if probe.returncode != 0 or not probe.stdout.strip():
        return None
    return os.path.realpath(probe.stdout.strip())


def _bundle_rel_in_repo(bundle_root, repo_top):
    """Bundle root's repo-relative posix path, or None when not inside.

    None disables filtering: the bundle is outside the repo, or IS the repo
    root, where excluding it would hide every change.
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
    """Drop paths under the bundle root (repo-relative posix paths)."""
    if bundle_rel is None:
        return list(paths)
    prefix = bundle_rel + "/"
    return [p for p in paths
            if p.rstrip("/") != bundle_rel and not p.startswith(prefix)]


def fingerprint(locator, source_type, bundle_root=None):
    """Current fingerprint of a source, or None when it can't be computed."""
    if source_type == "git":
        head = _git(["rev-parse", "HEAD"], cwd=locator)
        if head.returncode != 0:
            return None
        fp = head.stdout.strip()
        porcelain = _git(["status", "--porcelain", "--untracked-files=all"],
                         cwd=locator)
        if porcelain.returncode == 0:
            bundle_rel = _bundle_rel_in_repo(bundle_root, _repo_toplevel(locator))
            if _outside_bundle(_porcelain_paths(porcelain.stdout), bundle_rel):
                fp += DIRTY
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


# ── Drift detail for stale git sources ───────────────────────────────────────

def _base_sha(fp):
    return fp[:-len(DIRTY)] if fp.endswith(DIRTY) else fp


def git_drift(locator, pinned, current, bundle_root=None):
    """What moved between the pinned and current fingerprints of a git source.

    Returns diffstat (the summary line of `git diff --stat pinned..HEAD`),
    changed_files (name-only diff, plus dirty-worktree paths), and a note
    when the diff is unobtainable (e.g. the pinned commit was rebased away).
    """
    detail = {"diffstat": None, "changed_files": [], "note": None}
    base, head = _base_sha(pinned), _base_sha(current)
    bundle_rel = _bundle_rel_in_repo(bundle_root, _repo_toplevel(locator))
    changed = set()
    if base != head:
        stat = _git(["diff", "--stat", "%s..%s" % (base, head)], cwd=locator)
        if stat.returncode == 0:
            lines = [l for l in stat.stdout.strip().split("\n") if l.strip()]
            if lines:
                detail["diffstat"] = lines[-1].strip()
        else:
            detail["note"] = ("diff vs pinned commit unavailable "
                              "(pinned SHA not found locally?)")
        names = _git(["diff", "--name-only", "%s..%s" % (base, head)],
                     cwd=locator)
        if names.returncode == 0:
            changed.update(_outside_bundle(
                [l.strip() for l in names.stdout.split("\n") if l.strip()],
                bundle_rel))
    if current.endswith(DIRTY):
        porcelain = _git(["status", "--porcelain", "--untracked-files=all"],
                         cwd=locator)
        if porcelain.returncode == 0:
            changed.update(_outside_bundle(
                _porcelain_paths(porcelain.stdout), bundle_rel))
        if base == head and detail["note"] is None:
            detail["note"] = "worktree has uncommitted changes"
    detail["changed_files"] = sorted(changed)
    return detail


# ── Bundle discovery ─────────────────────────────────────────────────────────

_OKF_VERSION_RE = re.compile(r"^\s*okf_version\s*:\s*[\"']?([^\"'#]+?)[\"']?\s*$")


def bundle_version(path):
    """okf_version declared by <path>/index.md frontmatter, or None."""
    index = os.path.join(path, INDEX)
    if not os.path.isfile(index):
        return None
    try:
        with open(index, encoding="utf-8") as f:
            lines = f.read().split("\n")
    except (OSError, UnicodeDecodeError):
        return None
    if not lines or lines[0] != "---":
        return None
    for line in lines[1:]:
        if line == "---":
            break
        match = _OKF_VERSION_RE.match(line)
        if match:
            return match.group(1).strip()
    return None


def find_bundles(roots):
    """[(bundle_root, okf_version), ...] under the given roots, sorted.

    A bundle root is any directory whose index.md declares okf_version.
    Nested bundle roots inside a found bundle are not hunted (a bundle's
    subject index files carry no frontmatter, per the spec).
    """
    found, seen = [], set()
    for root in roots:
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            continue
        for base, dirs, _files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d != ".git")
            version = bundle_version(base)
            if version is not None:
                real = os.path.realpath(base)
                if real not in seen:
                    seen.add(real)
                    found.append((base, version))
                dirs[:] = []
    return sorted(found)


def find_subjects(bundle_root):
    """Sorted relpaths of every directory under the bundle holding an explainer.md."""
    subjects = []
    for base, dirs, files in os.walk(bundle_root):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        if base != bundle_root and EXPLAINER in files:
            subjects.append(os.path.relpath(base, bundle_root))
    return sorted(subjects)


# ── Assessment ───────────────────────────────────────────────────────────────

def assess_source(pin, bundle_root=None):
    """One pin -> row dict with state FRESH/STALE/UNKNOWN and drift detail.

    Mirrors okf.py's status logic: external or unpinned -> UNKNOWN; a source
    whose current fingerprint can't be computed (moved path, git gone) ->
    UNKNOWN; otherwise compare fingerprints.
    """
    row = {
        "type": pin.get("type"),
        "locator": pin.get("locator"),
        "pinned_fingerprint": pin.get("fingerprint"),
        "current_fingerprint": None,
        "state": "UNKNOWN",
        "diffstat": None,
        "changed_files": [],
        "note": None,
    }
    pinned = pin.get("fingerprint")
    if pin.get("type") == "external" or pinned is None:
        return row
    locator = pin["locator"]
    current_type = detect_source_type(locator)
    current = fingerprint(locator, current_type, bundle_root)
    row["current_fingerprint"] = current
    if current is None:
        return row
    if current == pinned:
        row["state"] = "FRESH"
        return row
    if current_type == "git":
        # STALE is decided by what MOVED outside the bundle, not by raw
        # fingerprint inequality. Committing the bundle changes HEAD, so
        # comparing shas alone reported every in-repo bundle as STALE — which
        # is exactly what okf.py's self-pin fix avoids. Same rule, same answer.
        drift = git_drift(locator, pinned, current, bundle_root)
        row.update(drift)
        row["state"] = "STALE" if drift["changed_files"] else "FRESH"
        return row
    row["state"] = "STALE"
    return row


def assess_subject(bundle_root, rel_subject):
    """Assess one subject; malformed input becomes an ERROR entry, never a crash."""
    subject_dir = os.path.join(bundle_root, rel_subject)
    info = {"subject": rel_subject, "path": subject_dir, "title": rel_subject,
            "state": "ERROR", "error": None, "sources": []}
    try:
        with open(os.path.join(subject_dir, EXPLAINER), encoding="utf-8") as f:
            meta = parse_frontmatter(f.read())
        title = meta.get("title")
        if isinstance(title, str) and title:
            info["title"] = title
        pins = extract_sources(meta)
    except SubjectError as exc:
        info["error"] = str(exc)
        return info
    except (OSError, UnicodeDecodeError) as exc:
        info["error"] = "unreadable explainer: %s" % exc
        return info
    states = []
    for pin in pins:
        try:
            row = assess_source(pin, bundle_root)
        except Exception as exc:  # never let one bad source kill the sweep
            row = {"type": pin.get("type"), "locator": pin.get("locator"),
                   "pinned_fingerprint": pin.get("fingerprint"),
                   "current_fingerprint": None, "state": "ERROR",
                   "diffstat": None, "changed_files": [],
                   "note": "source check failed: %s" % exc}
        info["sources"].append(row)
        states.append(row["state"])
    if "ERROR" in states:
        info["state"], info["error"] = "ERROR", "a source check failed"
    elif "STALE" in states:
        info["state"] = "STALE"
    elif "UNKNOWN" in states:
        info["state"] = "UNKNOWN"
    else:
        info["state"] = "FRESH"  # includes the no-pins case: nothing to drift
    return info


def survey(roots):
    """Full sweep result as a plain dict (the `json` subcommand's payload)."""
    bundles = []
    counts = {"FRESH": 0, "STALE": 0, "UNKNOWN": 0, "ERROR": 0}
    for bundle_root, version in find_bundles(roots):
        subjects = [assess_subject(bundle_root, rel)
                    for rel in find_subjects(bundle_root)]
        for subject in subjects:
            counts[subject["state"]] += 1
        bundles.append({"root": bundle_root, "okf_version": version,
                        "subjects": subjects})
    if not bundles:
        result = "NO_BUNDLES"
    elif counts["ERROR"]:
        result = "ERROR"
    elif counts["STALE"]:
        result = "STALE"
    elif counts["UNKNOWN"]:
        result = "UNKNOWN"
    else:
        result = "FRESH"
    exit_code = 1 if counts["STALE"] or counts["ERROR"] else 0
    return {"roots": [os.path.abspath(r) for r in roots], "bundles": bundles,
            "counts": counts, "result": result, "exit_code": exit_code}


# ── Reporting ────────────────────────────────────────────────────────────────

def _short(fp):
    if fp is None:
        return "null"
    if fp.endswith(DIRTY):
        return _short(fp[:-len(DIRTY)]) + DIRTY
    return fp[:12] if len(fp) > 12 else fp


def render_sweep(data, out):
    for root in data["roots"]:
        if not os.path.isdir(root):
            out.write("WARN: root not found: %s\n" % root)
    if not data["bundles"]:
        out.write("GARDEN_RESULT: NO_BUNDLES (no OKF bundle roots under: %s)\n"
                  % ", ".join(data["roots"]))
        return
    for bundle in data["bundles"]:
        out.write("BUNDLE: %s (okf_version %s) — %d subject(s)\n"
                  % (bundle["root"], bundle["okf_version"],
                     len(bundle["subjects"])))
        for subject in bundle["subjects"]:
            line = "  SUBJECT: %s — %s" % (subject["subject"], subject["state"])
            if subject["error"]:
                line += " (%s)" % subject["error"]
            out.write(line + "\n")
            for row in subject["sources"]:
                out.write("    SOURCE: %s %s — %s\n"
                          % (row["type"], row["locator"], row["state"]))
                if row["state"] == "STALE":
                    out.write("      pinned %s -> current %s\n"
                              % (_short(row["pinned_fingerprint"]),
                                 _short(row["current_fingerprint"])))
                    if row["diffstat"]:
                        out.write("      diff: %s\n" % row["diffstat"])
                    if row["changed_files"]:
                        shown = row["changed_files"][:10]
                        more = len(row["changed_files"]) - len(shown)
                        suffix = " (+%d more)" % more if more else ""
                        out.write("      changed: %s%s\n"
                                  % (" ".join(shown), suffix))
                if row["note"]:
                    out.write("      note: %s\n" % row["note"])
    counts = data["counts"]
    out.write("GARDEN_RESULT: %s (fresh %d, stale %d, unknown %d, error %d; "
              "%d subject(s), %d bundle(s))\n"
              % (data["result"], counts["FRESH"], counts["STALE"],
                 counts["UNKNOWN"], counts["ERROR"],
                 sum(counts.values()), len(data["bundles"])))


def main(argv=None, out=None):
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
            ("sweep", "report drift per bundle subject; exit 1 on STALE/ERROR"),
            ("json", "same sweep, machine-readable JSON; same exit rule")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("roots", nargs="+",
                       help="knowledge root(s) to search for OKF bundles")
    args = parser.parse_args(argv)
    data = survey(args.roots)
    if args.command == "json":
        out.write(jsonlib.dumps(data, indent=2, sort_keys=True) + "\n")
    else:
        render_sweep(data, out)
    return data["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
