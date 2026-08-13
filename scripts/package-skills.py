#!/usr/bin/env python3
"""package-skills.py — build one uploadable .zip per skill directory.

Each top-level directory containing a SKILL.md becomes `dist/<skill>.zip`, whose
single top-level entry is `<skill>/` — the shape Claude.ai's "upload skill" flow
and `~/.claude/skills/` both expect. Alongside the archives it writes
`SHA256SUMS`, `manifest.json`, and `RELEASE_NOTES.md`.

Archives are **byte-for-byte reproducible**: entries are sorted, timestamps are
pinned to the ZIP epoch, and only the executable bit survives from the file
mode. That is not cosmetic — the release workflow compares a freshly built
SHA256SUMS against the one attached to the previous release and skips
publishing when nothing changed, so a docs-only merge does not mint a release
full of identical archives. Any nondeterminism here would defeat that check.

stdlib only, offline, no network — same house rules as the skills themselves.

Usage:
    python3 scripts/package-skills.py [--out dist] [--skill NAME]... [--revision SHA]
"""

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import zipfile

# Repo-relative default output directory (gitignored).
DEFAULT_OUT = "dist"

# Build artifacts and editor droppings never belong in a published skill.
EXCLUDED_DIRS = {"__pycache__", ".git", ".pytest_cache", ".ruff_cache", "node_modules"}
EXCLUDED_NAMES = {".DS_Store"}
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".swp")

# Pinned ZIP timestamp (the earliest the format can express) — see module docstring.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

# Claude.ai rejects oversized skill uploads; warn well before anyone hits it.
SIZE_WARN_BYTES = 8 * 1024 * 1024


# ── Frontmatter ───────────────────────────────────────────────────────────────
def parse_frontmatter(text):
    """Parse the leading `---` YAML block of a SKILL.md.

    Deliberately a small tolerant subset (scalars, folded/literal blocks, and a
    one-level nested mapping such as `metadata:`) rather than a YAML dependency:
    this repo ships stdlib-only tooling and the frontmatter shape is enforced by
    the gates, not by this parser.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md does not open with a '---' frontmatter fence")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ValueError("SKILL.md frontmatter is not closed by '---'")

    block = lines[1:end]
    data = {}
    i = 0
    while i < len(block):
        raw = block[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent != 0 or ":" not in raw:
            i += 1
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()

        if value in (">", ">-", ">+", "|", "|-", "|+"):
            folded = value.startswith(">")
            chunk, i = _read_block_scalar(block, i + 1)
            data[key] = (" ".join(chunk) if folded else "\n".join(chunk)).strip()
            continue
        if value == "":
            nested, i = _read_nested_mapping(block, i + 1)
            data[key] = nested if nested else ""
            continue

        data[key] = _unquote(value)
        i += 1
    return data


def _read_block_scalar(block, i):
    """Consume the indented continuation lines of a folded/literal scalar."""
    out = []
    while i < len(block):
        line = block[i]
        if line.strip() and not line.startswith((" ", "\t")):
            break
        out.append(line.strip())
        i += 1
    while out and not out[-1]:
        out.pop()
    return out, i


def _read_nested_mapping(block, i):
    """Consume one level of indented `key: value` pairs (e.g. under `metadata:`)."""
    out = {}
    while i < len(block):
        line = block[i]
        if not line.strip():
            i += 1
            continue
        if not line.startswith((" ", "\t")):
            break
        if ":" in line:
            key, _, value = line.partition(":")
            out[key.strip()] = _unquote(value.strip())
        i += 1
    return out, i


def _unquote(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


# ── Discovery and file collection ─────────────────────────────────────────────
def discover_skills(root):
    """Every top-level directory holding a SKILL.md, sorted — mirrors the Makefile."""
    out = []
    for entry in sorted(os.listdir(root)):
        path = os.path.join(root, entry)
        if os.path.isdir(path) and os.path.isfile(os.path.join(path, "SKILL.md")):
            out.append(entry)
    return out


def collect_files(skill_dir):
    """Relative paths of every publishable file under skill_dir, sorted."""
    found = []
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
        for name in sorted(filenames):
            if name in EXCLUDED_NAMES or name.endswith(EXCLUDED_SUFFIXES):
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            found.append(os.path.relpath(full, skill_dir))
    return sorted(found)


# ── Packaging ─────────────────────────────────────────────────────────────────
def build_zip(skill_dir, skill_name, out_path):
    """Write a reproducible archive whose only top-level entry is `<skill_name>/`."""
    files = collect_files(skill_dir)
    if "SKILL.md" not in files:
        raise ValueError("%s has no SKILL.md" % skill_dir)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel in files:
            source = os.path.join(skill_dir, rel)
            arcname = "%s/%s" % (skill_name, rel.replace(os.sep, "/"))
            info = zipfile.ZipInfo(arcname, date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # unix, so the permission bits below are honoured
            executable = bool(os.stat(source).st_mode & stat.S_IXUSR)
            info.external_attr = (0o100755 if executable else 0o100644) << 16
            with open(source, "rb") as fh:
                zf.writestr(info, fh.read())
    return files


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package(root, out_dir, only=None, revision=""):
    """Package every (or the named) skill into out_dir. Returns the manifest dict."""
    skills = discover_skills(root)
    if only:
        unknown = sorted(set(only) - set(skills))
        if unknown:
            raise ValueError("not a skill directory: %s" % ", ".join(unknown))
        skills = [s for s in skills if s in only]
    if not skills:
        raise ValueError("no skills found under %s" % root)

    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)

    entries = []
    for skill in skills:
        skill_dir = os.path.join(root, skill)
        with open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8") as fh:
            front = parse_frontmatter(fh.read())

        name = front.get("name", "")
        if name != skill:
            # The archive filename is the download URL and the folder inside it;
            # a mismatch would ship an archive whose name contradicts the skill.
            raise ValueError(
                "%s/SKILL.md declares name '%s' — it must match the directory name"
                % (skill, name)
            )

        archive = os.path.join(out_dir, "%s.zip" % skill)
        files = build_zip(skill_dir, skill, archive)
        size = os.path.getsize(archive)
        if size > SIZE_WARN_BYTES:
            print("WARN: %s.zip is %d bytes — large for a skill upload" % (skill, size))

        metadata = front.get("metadata") or {}
        entries.append(
            {
                "name": skill,
                "version": metadata.get("version", "") if isinstance(metadata, dict) else "",
                "description": front.get("description", ""),
                "license": front.get("license", ""),
                "archive": "%s.zip" % skill,
                "files": len(files),
                "bytes": size,
                "sha256": sha256_of(archive),
            }
        )

    manifest = {"revision": revision, "skills": entries}
    _write(os.path.join(out_dir, "manifest.json"), json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    _write(
        os.path.join(out_dir, "SHA256SUMS"),
        "".join("%s  %s\n" % (e["sha256"], e["archive"]) for e in entries),
    )
    _write(os.path.join(out_dir, "RELEASE_NOTES.md"), render_release_notes(entries, revision))
    return manifest


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def render_release_notes(entries, revision=""):
    """Human-facing notes: what each archive is and the three ways to install it."""
    lines = [
        "Every skill in this repo, packaged as an individual `.zip` you can upload",
        "directly to Claude.ai or drop into a local skills directory.",
        "",
        "## Use a skill",
        "",
        "**Claude.ai (also reaches Claude Desktop and remote sessions):** download the",
        "skill's `.zip` below, then go to Settings → Capabilities → Skills → *Upload",
        "skill* and pick the file. Upload the `.zip` as-is — do not unzip it first.",
        "",
        "**Claude Code (local):** unzip into your skills directory —",
        "",
        "```bash",
        "unzip -o <skill>.zip -d ~/.claude/skills/     # or .claude/skills/ for one project",
        "```",
        "",
        "**From source instead:** `npx skills add dhanesh/agent-skills --skill <skill>`",
        "",
        "## Verify a download",
        "",
        "```bash",
        "sha256sum -c SHA256SUMS --ignore-missing",
        "```",
        "",
        "## Skills in this release",
        "",
        "| Skill | Version | Size | What it does |",
        "|---|---|---|---|",
    ]
    for entry in entries:
        lines.append(
            "| `%s` | %s | %s | %s |"
            % (
                entry["name"],
                entry["version"] or "—",
                _human_size(entry["bytes"]),
                _first_sentence(entry["description"]),
            )
        )
    lines += [
        "",
        "Each archive contains a single `<skill>/` folder with its `SKILL.md`,",
        "references, assets, and scripts — the same tree as the repo directory.",
    ]
    if revision:
        lines += ["", "Built from `%s`." % revision]
    return "\n".join(lines) + "\n"


# Trailing tokens that carry a period without ending a sentence.
_ABBREVIATIONS = {"e.g", "i.e", "vs", "etc", "cf", "approx", "no", "incl"}


def _first_sentence(text, limit=180):
    """First sentence/clause of a description, for the release-notes table cell."""
    text = " ".join(text.split())
    cut = None
    for stop in (". ", "; ", " — "):
        start = 0
        while True:
            idx = text.find(stop, start)
            if idx < 0:
                break
            head = text[:idx]
            if len(head) >= 20 and head.rsplit(" ", 1)[-1] not in _ABBREVIATIONS:
                cut = idx if cut is None else min(cut, idx)
                break
            start = idx + len(stop)
    if cut is not None:
        text = text[:cut]
    text = text.replace("|", "\\|")
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _human_size(size):
    if size < 1024:
        return "%d B" % size
    if size < 1024 * 1024:
        return "%.0f KB" % (size / 1024.0)
    return "%.1f MB" % (size / (1024.0 * 1024.0))


# ── CLI ───────────────────────────────────────────────────────────────────────
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="repo root to scan (default: .)")
    parser.add_argument("--out", default=DEFAULT_OUT, help="output directory (default: dist)")
    parser.add_argument(
        "--skill",
        action="append",
        default=None,
        metavar="NAME",
        help="package only this skill (repeatable; default: all)",
    )
    parser.add_argument("--revision", default="", help="commit/tag recorded in the manifest and notes")
    args = parser.parse_args(argv)

    try:
        manifest = package(args.root, args.out, only=args.skill, revision=args.revision)
    except ValueError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1

    for entry in manifest["skills"]:
        print("PACKAGED %-34s %6s  %d files" % (entry["archive"], _human_size(entry["bytes"]), entry["files"]))
    print("PACKAGE_RESULT: PASS (%d skills → %s/)" % (len(manifest["skills"]), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
