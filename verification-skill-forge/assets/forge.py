#!/usr/bin/env python3
"""forge.py — generate, lint and maintain a project-local verify-<app> skill.

verification-skill-forge's state tool. It never drives an application; it holds the
generated skill to its contract so a model cannot ship a verify skill that only looks
done. stdlib only, offline, deterministic.

Usage:
    python3 forge.py detect --root <repo>              # MODE: forge | maintain <dir> | ask
    python3 forge.py scaffold --app-root <dir> --app <name>
                                                       # SCAFFOLD: <verify dir> (FILL: markers)
    python3 forge.py vendor <verify dir>               # VENDORED: <path>
    python3 forge.py lint <verify dir>                 # LINT_RESULT: PASS|FAIL
    python3 forge.py coverage <verify dir> [--manifold DIR] [--json] [--require-total]
                                                       # COVERED:/UNCOVERED:/COVERAGE: n/m
    python3 forge.py seed <verify dir> [--manifold DIR]
                                                       # SEEDED: <feature id> per stub
    python3 forge.py finding <verify dir> --feature F --expected T --observed T
                                        [--worktree W] # FINDING: <path>
    python3 forge.py check-maintain <verify dir> --base <ref>
                                                       # MAINTAIN_RESULT: PASS|FAIL
    python3 forge.py contract <feature file>           # one JSON object: clauses,
                                                       #   trusted, sha256
    python3 forge.py check-proof [--root <repo>] --base <ref> --head <ref>
                                                       # ESCAPE: <path>:<line> <token>
                                                       #   CHECK_PROOF_RESULT: PASS|FAIL

A verify skill lives at <app root>/.claude/skills/verify-<app>/ and holds SKILL.md (five
sections: Launch, Doctor, Drive, Evidence, Cleanup), scripts/verify_evidence.py (a
byte-identical copy of this skill's assets/verify_evidence.py) and features/ (README.md
index plus one <id>.md per user-facing feature). The app root is the directory that
holds .claude/. See references/generated-skill.md and references/feature-map.md.

Exit 0 on PASS, 1 on a lint/maintain FAIL, 2 on invalid input, 3 when a human has to
choose (detect found several candidates) or --require-total found an uncovered
constraint.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RECORDER = os.path.join(HERE, "verify_evidence.py")

SECTIONS = ("Launch", "Doctor", "Drive", "Evidence", "Cleanup")
FEATURE_SECTIONS = ("What it is", "How to reach it", "Drive it", "Proof", "Gotchas")
ID_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*\Z")
RESERVED_IDS = ("doctor", "findings")
SHA_RE = re.compile(r"^[0-9a-f]{40}\Z")
SKIP_DIRS = {".git", "node_modules", ".skill-contract", ".verify", ".verify-run",
             ".venv", "venv", "__pycache__", "dist", "build"}

# The proof standards, verbatim. A generated skill carries this block unchanged in its
# Evidence section (lint checks it line for line), so the standard an agent reads mid-task
# is the one this skill wrote, not a paraphrase of it.
STANDARDS = (
    "- Exercise the real user path. Never internal setters, never test-only endpoints.",
    "- Capture the action *and* the resulting state, not just a final screenshot.",
    "- Verify side effects (rows inserted, files written, messages sent, webhooks fired) "
    "alongside what is visible.",
    "- Mocks only where a production boundary already isolates the external system.",
    "- Where the safe path is a dry-run or test mode, verify what it *actually* skips by "
    "observing files, network and git refs, not by trusting its name. Some dry-runs still "
    "touch the network or open a browser.",
    "- Evidence goes to `.verify/<instance>/<feature-id>/<sha>/`, one directory per feature "
    "per head, recorded with `scripts/verify_evidence.py record`.",
    "- Harness output is JSON wherever the harness can emit it, so a verifier can read it "
    "without a human.",
    "- Cleanup removes instances, never evidence.",
)

# Text a generator leaves behind when it has not read the repo. FILL: is what `scaffold`
# writes; the rest are generic placeholders and the names in pstack's fictional example.
PLACEHOLDERS = (
    (re.compile(r"\bFILL:"), "an unfilled FILL: marker"),
    (re.compile(r"\b(TODO|TBD|FIXME|XXX)\b"), "a TODO/TBD marker"),
    (re.compile(r"\bexample\.(com|org)\b"), "an example.com address"),
    (re.compile(r"\b(control-atlas|verify-atlas|Harbor Labs|your-app|my-app)\b"),
     "a name from an example, not from this repo"),
)
CODE_PLACEHOLDER = re.compile(r"<(?!!--)[A-Za-z][A-Za-z0-9 _-]*>")
COORDINATES = (
    re.compile(r"\bclick\(\s*\d+\s*,\s*\d+"),
    re.compile(r"\bmouse\.(click|move|down|up)\(\s*\d+"),
    re.compile(r"\bmousemove\s+\d+\s+\d+"),
    re.compile(r"""press\(\s*['"]Tab['"]"""),
    re.compile(r"\bkey\s+Tab\b"),
    re.compile(r"(?i)\btab[- ]order\b"),
    re.compile(r"\b[xy]\s*[:=]\s*\d{2,}\s*,\s*[xy]\s*[:=]\s*\d{2,}"),
)
WRITES = (
    re.compile(r"(^|[\s;&|])(rm|mv|kill|pkill|killall|truncate|dd)\s"),
    re.compile(r"(?<![0-9&=<-])>>?\s*(?!/dev/null)[^\s&=]"),
    re.compile(r"\bgit\s+(commit|push|reset|checkout|clean)\b"),
    re.compile(r"(?i)-X\s*(POST|PUT|PATCH|DELETE)\b"),
    re.compile(r"\bcurl\b.*(\s--data(-raw|-binary|-urlencode)?\b|\s-d\s)"),
    re.compile(r"(?i)\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE)\s"),
)
EVIDENCE_DELETE = re.compile(r"\b(rm|rmdir|shred|find)\b[^\n]*(?<![\w-])\.verify(?![\w-])")


def _out(line):
    print(line)


# ── parsing ──────────────────────────────────────────────────────────────────
def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def frontmatter(text):
    """(dict of top-level scalar keys, body). {} when there is no frontmatter."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    fm = {}
    for line in text[4:end].splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip("'\"")
    return fm, text[end + 4:].lstrip("\n")


def sections(body, level=2):
    """{heading: text} for every `## heading` (level 2) in body."""
    marker = "#" * level + " "
    out, cur, buf = {}, None, []
    fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        if not fence and line.startswith(marker) and not line.startswith(marker + "#"):
            if cur is not None:
                out[cur] = "\n".join(buf)
            cur, buf = line[len(marker):].strip(), []
            continue
        if cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf)
    return out


def code_blocks(text):
    """The contents of every fenced code block in text, joined per block."""
    return re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.S)


def prose(text):
    """text with fenced code blocks removed."""
    return re.sub(r"```[^\n]*\n.*?```", "", text, flags=re.S)


def feature_meta(text):
    """The `- key: value` lines at the top of a feature file, as a dict."""
    meta = {}
    for line in text.splitlines():
        if line.startswith("## "):
            break
        m = re.match(r"^- (id|proven|anchors|constraints|trusted):\s*(.*)$", line.strip())
        if m:
            meta[m.group(1)] = m.group(2).strip()
    return meta


# A contract clause bullet: `- C<n> [kind]: statement`. The kind is captured as written
# (not only pre|post|inv|trans) so lint can name an unknown kind instead of skipping it.
CLAUSE_RE = re.compile(r"^- (C\d+)\s*\[([^\]]*)\]:\s*(.*)$")


def contract_section(text):
    """The `## Contract` section of a feature file, or None when it has none.

    The section runs from the `## Contract` heading line up to, not including, the next
    line that starts with `## ` (or the end of the file). Each line is right-stripped and
    the lines are joined with a newline. This exact text is what `contract` hashes, so two
    commits hold the same contract exactly when this function returns the same string."""
    out = None
    for line in text.splitlines():
        line = line.rstrip()
        if out is None:
            if line == "## Contract":
                out = [line]
        elif line.startswith("## "):
            break
        else:
            out.append(line)
    return None if out is None else "\n".join(out)


def parse_contract(text):
    """{clauses: [{id, kind, text}], trusted: [..], sha256: hex or None} for a feature
    file. sha256 is of contract_section() as UTF-8, None when there is no section."""
    section = contract_section(text)
    clauses = []
    for line in (section or "").splitlines()[1:]:
        m = CLAUSE_RE.match(line.strip())
        if m:
            clauses.append({"id": m.group(1), "kind": m.group(2).strip(),
                            "text": m.group(3).strip()})
    return {"clauses": clauses,
            "trusted": split_list(feature_meta(text).get("trusted")),
            "sha256": None if section is None else
            hashlib.sha256(section.encode("utf-8")).hexdigest()}


def split_list(value):
    return [v.strip().strip("`") for v in (value or "").split(",") if v.strip()]


def parse_anchor(anchor):
    """(path, symbol or None) from `path` or `path:symbol`."""
    path, sep, symbol = anchor.partition(":")
    return path.strip(), (symbol.strip() or None) if sep else None


def app_root_of(verify_dir):
    """<app root>/.claude/skills/verify-<app> -> <app root>, else None."""
    d = os.path.abspath(verify_dir)
    skills = os.path.dirname(d)
    claude = os.path.dirname(skills)
    if os.path.basename(skills) == "skills" and os.path.basename(claude) == ".claude":
        return os.path.dirname(claude)
    return None


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)


def toplevel(path):
    r = git(path, "rev-parse", "--show-toplevel")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


# ── detect ───────────────────────────────────────────────────────────────────
def find_verify_skills(root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        if os.path.basename(dirpath).startswith("verify-") and "SKILL.md" in filenames \
                and os.path.basename(os.path.dirname(dirpath)) == "skills" \
                and os.path.basename(os.path.dirname(os.path.dirname(dirpath))) == ".claude":
            found.append(os.path.relpath(dirpath, root))
    return sorted(found)


def cmd_detect(a):
    root = os.path.abspath(a.root)
    if not os.path.isdir(root):
        sys.stderr.write("not a directory: %s\n" % root)
        return 2
    found = find_verify_skills(root)
    if not found:
        _out("MODE: forge")
        return 0
    if len(found) == 1:
        _out("MODE: maintain %s" % found[0])
        return 0
    for f in found:
        _out("CANDIDATE: %s" % f)
    _out("MODE: ask (%d verify skills; ask the user which one)" % len(found))
    return 3


# ── scaffold / vendor ────────────────────────────────────────────────────────
SKELETON = """---
name: verify-{app}
description: >-
  FILL: name the app, the surface a user touches (web UI, CLI, API) and when to reach
  for this skill: proving a change to {app} works by driving the running app.
---

# verify-{app}

FILL: one paragraph: what {app} is, the surface this skill drives, and the harness.

## Launch

FILL: the exact command that starts an isolated instance, taking the instance name. Every
instance gets its own port (`scripts/verify_evidence.py port --instance "$INSTANCE"`), its
own data store and its own auth session. Say how to tell it is ready and how long to wait.

```sh
FILL: launch command using $INSTANCE
```

## Doctor

FILL: one read-only check: process up, right build, port owned by this instance, auth
valid. Record it with `scripts/verify_evidence.py doctor`.

```sh
FILL: doctor command using $INSTANCE
```

## Drive

FILL: the harness recipe with real selectors and commands from this repo: ARIA labels,
data-* attributes, route paths, prompt strings. No coordinates, no tab order.

```sh
FILL: drive command
```

## Evidence

{standards}

```sh
FILL: record command
```

## Cleanup

FILL: stop what this skill started for $INSTANCE, by pid or port, never by process name.
Cleanup removes instances, never evidence.

```sh
FILL: cleanup command using $INSTANCE
```
"""


def vendor(verify_dir):
    dest = os.path.join(verify_dir, "scripts", "verify_evidence.py")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copyfile(RECORDER, dest)
    os.chmod(dest, 0o755)
    return dest


def cmd_scaffold(a):
    if not ID_RE.match(a.app):
        sys.stderr.write("app name %r must be lowercase kebab-case\n" % a.app)
        return 2
    app_root = os.path.abspath(a.app_root)
    if not os.path.isdir(app_root):
        sys.stderr.write("not a directory: %s\n" % app_root)
        return 2
    d = os.path.join(app_root, ".claude", "skills", "verify-%s" % a.app)
    if os.path.exists(os.path.join(d, "SKILL.md")):
        sys.stderr.write("%s already exists: run maintain mode instead\n" % d)
        return 2
    os.makedirs(os.path.join(d, "features"), exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(SKELETON.format(app=a.app, standards="\n".join(STANDARDS)))
    with open(os.path.join(d, "features", "README.md"), "w", encoding="utf-8") as f:
        f.write("# %s feature map\n\nOne file per user-facing feature. FILL: one line per "
                "feature: a markdown link to its file, a dash, then what a user does with "
                "it.\n\n" % a.app)
    vendor(d)
    _out("SCAFFOLD: %s" % d)
    return 0


def cmd_vendor(a):
    _out("VENDORED: %s" % vendor(os.path.abspath(a.verify_dir)))
    return 0


# ── lint ─────────────────────────────────────────────────────────────────────
class Lint:
    def __init__(self):
        self.fails, self.infos = [], []

    def fail(self, msg):
        self.fails.append(msg)

    def info(self, msg):
        self.infos.append(msg)


def lint_skill_md(L, verify_dir):
    path = os.path.join(verify_dir, "SKILL.md")
    if not os.path.isfile(path):
        L.fail("SKILL.md not found in %s" % verify_dir)
        return
    fm, body = frontmatter(read(path))
    want = os.path.basename(os.path.abspath(verify_dir))
    if not want.startswith("verify-"):
        L.fail("the skill directory %s is not named verify-<app>" % want)
    if fm.get("name") != want:
        L.fail("frontmatter name %r does not match the directory %r" % (fm.get("name"), want))
    if not fm.get("description"):
        L.fail("frontmatter has no description, so the skill never registers")
    for rx, what in PLACEHOLDERS:
        for m in rx.finditer(read(path)):
            line = read(path)[:m.start()].count("\n") + 1
            L.fail("SKILL.md:%d: %s (%r): write what this repo actually does" %
                   (line, what, m.group(0)))
    secs = sections(body)
    for name in SECTIONS:
        if name not in secs:
            L.fail("section '## %s' is missing" % name)
            continue
        blocks = code_blocks(secs[name])
        if not any(b.strip() for b in blocks):
            L.fail("section '%s' has no fenced command: a verify skill runs real commands"
                   % name)
        for b in blocks:
            for m in CODE_PLACEHOLDER.finditer(b):
                L.fail("section '%s': placeholder %s inside a command" % (name, m.group(0)))
    launch = secs.get("Launch", "")
    if launch and not re.search(r"\$\{?INSTANCE\b|--instance[ =]+\"?\$", "\n".join(code_blocks(launch))):
        L.fail("Launch: no command takes the instance as a variable ($INSTANCE): every "
               "instance needs its own port, data store and auth session")
    if launch and not re.search(r"(?i)\b(ready|readiness|timeout|wait|seconds)\b", prose(launch)):
        L.fail("Launch: say how to tell the instance is ready and how long to wait")
    if launch and "verify_evidence.py port" not in launch and \
            "verify_evidence.py claim" not in launch and \
            not re.search(r"(?i)\bno (?:network )?port\b", prose(launch)):
        # A short-lived CLI binds nothing: saying so is the alternative to a port claim.
        L.fail("Launch: allocate the instance's port with scripts/verify_evidence.py port "
               "(or claim), so two owners never share one, or state that the app opens "
               "no port (a CLI)")
    doctor = "\n".join(code_blocks(secs.get("Doctor", "")))
    for line in doctor.splitlines():
        if "verify_evidence.py doctor" in line:
            continue
        # Quoted text is data, not shell: `grep -q '<title>'` redirects nothing.
        bare = re.sub(r"'[^']*'|\"[^\"]*\"", "''", line)
        for rx in WRITES:
            if rx.search(bare):
                L.fail("Doctor: %r writes or kills: the doctor MUST be read-only"
                       % line.strip())
                break
    if secs.get("Doctor") and "verify_evidence.py doctor" not in secs["Doctor"]:
        L.fail("Doctor: record the result with scripts/verify_evidence.py doctor")
    drive = secs.get("Drive", "")
    for rx in COORDINATES:
        for m in rx.finditer(drive):
            L.fail("Drive: %r addresses the screen by position or tab order; use an ARIA "
                   "label, a data-* attribute, a route or a prompt string" % m.group(0))
    ev = secs.get("Evidence", "")
    lines = [ln.strip() for ln in ev.splitlines()]
    for s in STANDARDS:
        if s not in lines:
            L.fail("Evidence: the proof standard %r is missing or reworded; copy the block "
                   "verbatim" % s[:60])
    if ev and "verify_evidence.py record" not in ev:
        L.fail("Evidence: record each proof with scripts/verify_evidence.py record")
    for block in code_blocks(secs.get("Cleanup", "")):
        for line in block.splitlines():
            if EVIDENCE_DELETE.search(line):
                L.fail("Cleanup: %r deletes evidence: cleanup removes instances, not proofs"
                       % line.strip())
            if re.search(r"\b(pkill|killall)\b", line):
                L.fail("Cleanup: %r kills by process name: kill what this instance started"
                       % line.strip())


def lint_vendored(L, verify_dir):
    copy = os.path.join(verify_dir, "scripts", "verify_evidence.py")
    if not os.path.isfile(copy):
        L.fail("scripts/verify_evidence.py is missing: run forge.py vendor")
    elif read(copy) != read(RECORDER):
        L.fail("scripts/verify_evidence.py differs from the forge's copy: run forge.py vendor")


def load_features(verify_dir):
    """{id: (path, text)} for every features/*.md except README.md."""
    out = {}
    for path in sorted(glob.glob(os.path.join(verify_dir, "features", "*.md"))):
        if os.path.basename(path) == "README.md":
            continue
        out[os.path.basename(path)[:-3]] = (path, read(path))
    return out


def anchor_problem(app_root, anchor):
    """None when the anchor resolves, else why it does not."""
    rel, symbol = parse_anchor(anchor)
    if not rel or os.path.isabs(rel) or ".." in rel.split("/"):
        return "anchor %r is not a path relative to the app root" % anchor
    path = os.path.join(app_root, rel)
    if not os.path.exists(path):
        return "anchor %s no longer exists" % rel
    if symbol:
        if not os.path.isfile(path):
            return "anchor %s is not a file, so it cannot hold %s" % (rel, symbol)
        try:
            if symbol not in read(path):
                return "anchor %s no longer mentions %s" % (rel, symbol)
        except (OSError, UnicodeDecodeError):
            return "anchor %s cannot be read as text" % rel
    return None


def lint_features(L, verify_dir):
    fdir = os.path.join(verify_dir, "features")
    index = os.path.join(fdir, "README.md")
    if not os.path.isfile(index):
        L.fail("features/README.md (the feature map index) is missing")
        return
    feats = load_features(verify_dir)
    if not feats:
        L.fail("the feature map has no feature files")
    listed = re.findall(r"\]\(([^)#\s]+\.md)\)", read(index))
    seen = {}
    for link in listed:
        seen[link] = seen.get(link, 0) + 1
    for link, n in sorted(seen.items()):
        if n > 1:
            L.fail("features/README.md lists %s %d times" % (link, n))
        if not os.path.isfile(os.path.join(fdir, link)):
            L.fail("features/README.md lists %s, which does not exist (a dead entry)" % link)
    for fid in feats:
        if fid + ".md" not in seen:
            L.fail("features/%s.md is not listed in features/README.md" % fid)
    app_root = app_root_of(verify_dir)
    for fid, (path, text) in feats.items():
        rel = "features/%s.md" % fid
        if not ID_RE.match(fid) or fid in RESERVED_IDS:
            L.fail("%s: %r is not a feature id (lowercase kebab-case, not doctor/findings)"
                   % (rel, fid))
        meta = feature_meta(text)
        if meta.get("id") != fid:
            L.fail("%s: '- id:' is %r, not the file name %r" % (rel, meta.get("id"), fid))
        proven = meta.get("proven")
        if proven is None or (proven != "no" and not SHA_RE.match(proven)):
            L.fail("%s: '- proven:' must be 'no' or the 40-hex commit it was driven on" % rel)
        for rx, what in PLACEHOLDERS:
            if rx.search(text):
                L.fail("%s: %s" % (rel, what))
        secs = sections(text)
        for name in FEATURE_SECTIONS:
            if not secs.get(name, "").strip():
                L.fail("%s: section '## %s' is missing or empty" % (rel, name))
        drive = secs.get("Drive it", "")
        if drive and not any(b.strip() for b in code_blocks(drive)):
            L.fail("%s: 'Drive it' has no command" % rel)
        if drive and not re.search(r"(?i)\bexit\b|\bexpect", drive):
            L.fail("%s: 'Drive it' names no expected output or exit code" % rel)
        for rx in COORDINATES:
            if rx.search(drive):
                L.fail("%s: 'Drive it' addresses the screen by position or tab order" % rel)
        anchors = split_list(meta.get("anchors"))
        if not anchors:
            L.info("%s: no source anchors, so drift and diff coverage cannot be detected"
                   % rel)
        for anchor in anchors:
            why = anchor_problem(app_root, anchor) if app_root else \
                "the verify skill is not under <app root>/.claude/skills/"
            if why:
                claim = "claims proven at %s but " % proven[:12] if proven and proven != "no" \
                    else ""
                L.fail("%s: %s%s: re-derive it from source (maintain mode)" % (rel, claim, why))
        lint_contract(L, rel, text)


CLAUSE_KINDS = ("pre", "post", "inv", "trans")


def lint_contract(L, rel, text):
    """Fail a malformed `## Contract`: every clause id C1..Cn once, in order, with a known
    kind and a statement, and named in the same feature's `## Proof`. No section, no check."""
    section = contract_section(text)
    if section is None:
        return
    for line in section.splitlines()[1:]:
        m = re.match(r"^- (C\d+)\b", line.strip())
        if m and not CLAUSE_RE.match(line.strip()):
            L.fail("%s: contract clause %s is not '- %s [kind]: statement': write it in "
                   "that form" % (rel, m.group(1), m.group(1)))
    proof = sections(text).get("Proof", "")
    seen, last = set(), 0
    for c in parse_contract(text)["clauses"]:
        cid, n = c["id"], int(c["id"][1:])
        if cid in seen:
            L.fail("%s: contract clause %s is listed twice: give each clause its own id"
                   % (rel, cid))
            continue
        if n != last + 1:
            L.fail("%s: contract clause %s follows %s: number the clauses C1..Cn in order "
                   "with no gap" % (rel, cid, "C%d" % last if last else "the heading"))
        seen.add(cid)
        last = max(last, n)
        if c["kind"] not in CLAUSE_KINDS:
            L.fail("%s: contract clause %s has the unknown kind %r: use one of %s"
                   % (rel, cid, c["kind"], ", ".join(CLAUSE_KINDS)))
        if not c["text"]:
            L.fail("%s: contract clause %s has no statement: write what must hold after "
                   "the colon" % (rel, cid))
        if not re.search(r"\b%s\b" % cid, proof):
            L.fail("%s: contract clause %s is not named in '## Proof': say there which "
                   "observation proves %s" % (rel, cid, cid))


def cmd_lint(a):
    d = os.path.abspath(a.verify_dir)
    L = Lint()
    lint_skill_md(L, d)
    lint_vendored(L, d)
    lint_features(L, d)
    for msg in L.infos:
        _out("INFO: %s" % msg)
    for msg in L.fails:
        _out("FAIL: %s" % msg)
    _out("LINT_RESULT: %s" % ("FAIL (%d)" % len(L.fails) if L.fails else "PASS"))
    return 1 if L.fails else 0


# ── Manifold join ────────────────────────────────────────────────────────────
ANCHORED_PHASES = ("ANCHORED", "GENERATED", "VERIFIED")
SUPPORTED_SCHEMA = (1, 2, 3)


def manifold_dir(verify_dir, given):
    if given:
        return os.path.abspath(given)
    app_root = app_root_of(verify_dir) or verify_dir
    for base in (app_root, toplevel(app_root)):
        if base and os.path.isdir(os.path.join(base, ".manifold")):
            return os.path.join(base, ".manifold")
    return None


def read_manifolds(mdir):
    """(constraints, degraded). constraints: [{id, qid, feature, type, title}] for every
    anchored constraint; degraded: [(code, file, message)] for every file skipped.

    A constraint is anchored when its manifold's phase is ANCHORED or later and a required
    truth maps to it; a manifold at that phase whose truths map to nothing (an older
    manifold) anchors all of its constraints. Codes follow Manifold's own: E_IO, E_PARSE,
    E_SCHEMA, E_VALIDATE, E_LINK. A bad file is skipped, never fatal."""
    out, degraded = [], []
    for path in sorted(glob.glob(os.path.join(mdir, "*.json"))):
        name = os.path.basename(path)
        if name.endswith(".verify.json"):
            continue
        try:
            raw = read(path)
        except (OSError, UnicodeDecodeError) as e:
            degraded.append(("E_IO", name, str(e)))
            continue
        try:
            doc = json.loads(raw)
        except ValueError as e:
            degraded.append(("E_PARSE", name, str(e)))
            continue
        if not isinstance(doc, dict):
            degraded.append(("E_VALIDATE", name, "top level is not an object"))
            continue
        sv = doc.get("schema_version", 3)
        if isinstance(sv, bool) or not isinstance(sv, int):
            degraded.append(("E_SCHEMA", name, "schema_version %r is not an integer" % (sv,)))
            continue
        if sv not in SUPPORTED_SCHEMA:
            degraded.append(("E_SCHEMA", name, "schema_version %r is not supported" % sv))
            continue
        cons = doc.get("constraints")
        if not isinstance(cons, dict):
            degraded.append(("E_VALIDATE", name, "no constraints object"))
            continue
        if doc.get("phase") not in ANCHORED_PHASES:
            continue
        feature = str(doc.get("feature") or name[:-5])
        ids = {}
        for group in cons.values():
            for c in group if isinstance(group, list) else []:
                if isinstance(c, dict) and isinstance(c.get("id"), str) \
                        and re.match(r"^[A-Z]+\d*$", c["id"]):
                    ids[c["id"]] = c.get("type")
        anchors = doc.get("anchors")
        truths = anchors.get("required_truths") if isinstance(anchors, dict) else []
        mapped = set()
        for rt in truths if isinstance(truths, list) else []:
            maps = rt.get("maps_to") if isinstance(rt, dict) else None
            for cid in maps if isinstance(maps, list) else []:
                if not isinstance(cid, str):
                    continue
                if cid in ids:
                    mapped.add(cid)
                else:
                    degraded.append(("E_LINK", name, "%s maps to unknown constraint %s"
                                     % (rt.get("id"), cid)))
        titles = {}
        md = path[:-5] + ".md"
        if os.path.isfile(md):
            for m in re.finditer(r"(?m)^#{3,4}\s+([BTUSO]\d+):\s*(.+)$", read(md)):
                titles[m.group(1)] = m.group(2).strip()
        for cid in sorted(mapped or ids, key=lambda c: (c[0], int(re.sub(r"\D", "", c) or 0))):
            out.append({"id": cid, "qid": "%s:%s" % (feature, cid), "feature": feature,
                        "type": ids.get(cid), "title": titles.get(cid, "")})
    for path in sorted(glob.glob(os.path.join(mdir, "*.yaml")) +
                       glob.glob(os.path.join(mdir, "*.yml"))):
        name = os.path.basename(path)
        if re.search(r"\.(anchor|verify)\.ya?ml$", name):
            continue
        if os.path.isfile(re.sub(r"\.ya?ml$", ".json", path)):
            continue  # migrated: the JSON file is the manifold
        try:
            feature, phase, ids, mapped = read_legacy_yaml(path)
        except (OSError, UnicodeDecodeError) as e:
            degraded.append(("E_IO", name, str(e)))
            continue
        if not ids:
            degraded.append(("E_VALIDATE", name, "legacy YAML with no `- id:` constraints "
                             "forge can read; run `manifold migrate`"))
            continue
        degraded.append(("LEGACY", name, "legacy YAML read with forge's line parser; "
                         "`manifold migrate` converts it to JSON"))
        if phase not in ANCHORED_PHASES:
            continue
        for cid in sorted(set(mapped) - set(ids)):
            degraded.append(("E_LINK", name, "a required truth maps to unknown constraint %s"
                             % cid))
        use = sorted(set(mapped) & set(ids)) or sorted(ids)
        for cid in sorted(use, key=lambda c: (c[0], int(re.sub(r"\D", "", c) or 0))):
            out.append({"id": cid, "qid": "%s:%s" % (feature, cid), "feature": feature,
                        "type": ids[cid][0] or None, "title": ids[cid][1]})
    return out, degraded


_Y_TOP = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*?)\s*$")
_Y_ID = re.compile(r"""^\s*-\s*id:\s*['"]?([BTUSO]\d+)['"]?\s*$""")
_Y_FIELD = re.compile(r"""^\s+(type|statement):\s*['"]?(.*?)['"]?\s*$""")
_Y_MAPS = re.compile(r"^\s*(?:-\s*)?(?:maps_to|maps_to_constraints?|satisfies_constraints)"
                     r":\s*(.*?)\s*$")
_Y_ITEM = re.compile(r"""^\s*-\s*['"]?([BTUSO]\d+)['"]?\s*$""")


def read_legacy_yaml(path):
    """(feature, phase, {id: (type, statement)}, mapped ids) from a legacy YAML manifold,
    read line by line (stdlib has no YAML parser): top-level `feature:`/`phase:`,
    `- id: B1` entries under `constraints:` with their `type:`/`statement:`, and every id a
    `maps_to`, `maps_to_constraint` or `satisfies_constraints` names, in this file and its
    `<feature>.anchor.yaml`. Only that narrow shape is read; `manifold migrate` converts
    the file to the JSON format for everything else."""
    top, cons, cur, block = {}, {}, None, None
    mapped = []
    texts = [read(path)]
    anchor = re.sub(r"\.ya?ml$", ".anchor.yaml", path)
    if os.path.isfile(anchor):
        texts.append(read(anchor))
    for n, text in enumerate(texts):
        pending = False
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            m = _Y_TOP.match(line)
            if m:
                block = m.group(1)
                if n == 0:
                    top.setdefault(block, m.group(2).strip("'\""))
                pending = False
                continue
            if n == 0 and block == "constraints":
                m = _Y_ID.match(line)
                if m:
                    cur = m.group(1)
                    cons.setdefault(cur, ["", ""])
                    continue
                m = _Y_FIELD.match(line)
                if m and cur:
                    cons[cur][0 if m.group(1) == "type" else 1] = m.group(2)
                    continue
            m = _Y_MAPS.match(line)
            if m:
                mapped += re.findall(r"\b([BTUSO]\d+)\b", m.group(1))
                pending = not m.group(1)
                continue
            m = _Y_ITEM.match(line)
            if pending and m:
                mapped.append(m.group(1))
            else:
                pending = False
    return (top.get("feature") or os.path.basename(path).rsplit(".", 1)[0],
            top.get("phase"), {k: tuple(v) for k, v in cons.items()}, mapped)


def coverage(verify_dir, mdir):
    constraints, degraded = read_manifolds(mdir) if mdir else ([], [])
    by_bare = {}
    for c in constraints:
        by_bare.setdefault(c["id"], []).append(c["qid"])
    proofs = {c["qid"]: [] for c in constraints}
    unknown = []
    for fid, (_, text) in load_features(verify_dir).items():
        for ref in split_list(feature_meta(text).get("constraints")):
            if ref in proofs:
                proofs[ref].append(fid)
            elif len(by_bare.get(ref, [])) == 1:
                proofs[by_bare[ref][0]].append(fid)
            else:
                unknown.append((fid, ref))
    return constraints, proofs, degraded, unknown


def cmd_coverage(a):
    d = os.path.abspath(a.verify_dir)
    mdir = manifold_dir(d, a.manifold)
    constraints, proofs, degraded, unknown = coverage(d, mdir)
    uncovered = [c for c in constraints if not proofs[c["qid"]]]
    if a.json:
        print(json.dumps({"manifold": mdir, "degraded": [list(x) for x in degraded],
                          "constraints": [dict(c, proofs=proofs[c["qid"]])
                                          for c in constraints],
                          "uncovered": [c["qid"] for c in uncovered],
                          "unknown_refs": [list(x) for x in unknown]}, indent=2))
    else:
        for code, name, msg in degraded:
            _out("DEGRADED: %s %s: %s" % (code, name, msg))
        for fid, ref in unknown:
            _out("UNKNOWN: features/%s.md names %s, which is no anchored constraint "
                 "(qualify it as <manifold>:<id> when two manifolds share an id)" % (fid, ref))
        for c in constraints:
            if proofs[c["qid"]]:
                _out("COVERED: %s -> %s" % (c["qid"], ", ".join(proofs[c["qid"]])))
        for c in uncovered:
            _out("UNCOVERED: %s %s" % (c["qid"], json.dumps(c["title"])))
        if not constraints:
            _out("COVERAGE: source-derived (no usable anchored .manifold%s)"
                 % (" at %s" % mdir if mdir else ""))
        else:
            _out("COVERAGE: %d/%d anchored constraints have an observable proof"
                 % (len(constraints) - len(uncovered), len(constraints)))
    return 3 if a.require_total and uncovered else 0


def _slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    s = re.sub(r"^[^a-z]+", "", s)
    return (s[:48].rstrip("-")) or "feature"


def cmd_seed(a):
    d = os.path.abspath(a.verify_dir)
    mdir = manifold_dir(d, a.manifold)
    if not mdir:
        _out("SEEDED: none (no .manifold; discover features from source)")
        return 0
    constraints, proofs, degraded, _ = coverage(d, mdir)
    for code, name, msg in degraded:
        _out("DEGRADED: %s %s: %s" % (code, name, msg))
    fdir = os.path.join(d, "features")
    os.makedirs(fdir, exist_ok=True)
    index = os.path.join(fdir, "README.md")
    if not os.path.isfile(index):
        with open(index, "w", encoding="utf-8") as f:
            f.write("# Feature map\n\n")
    n = 0
    for c in constraints:
        if proofs[c["qid"]]:
            continue
        fid = _slug(c["title"] or "%s-%s" % (c["feature"], c["id"]))
        if fid in RESERVED_IDS or os.path.exists(os.path.join(fdir, fid + ".md")):
            fid = _slug("%s-%s-%s" % (c["feature"], c["id"], c["title"]))
        with open(os.path.join(fdir, fid + ".md"), "w", encoding="utf-8") as f:
            f.write("# %s: %s\n\n- id: %s\n- proven: no\n- anchors: FILL: source paths\n"
                    "- constraints: %s\n\n## What it is\n\nFILL: from the user's point of "
                    "view; seeded from %s (%s).\n\n## How to reach it\n\nFILL\n\n## Drive "
                    "it\n\nFILL: exact commands, expected output and exit codes.\n\n## Proof"
                    "\n\nFILL: the observable result that proves %s holds.\n\n## Gotchas\n\n"
                    "FILL\n" % (fid, c["title"] or c["qid"], fid, c["qid"], c["qid"],
                                c["type"], c["qid"]))
        with open(index, "a", encoding="utf-8") as f:
            f.write("- [%s](%s.md) — proves %s\n" % (fid, fid, c["qid"]))
        _out("SEEDED: %s (%s)" % (fid, c["qid"]))
        n += 1
    if not n:
        _out("SEEDED: none (every anchored constraint already has a proof)")
    return 0


# ── maintain mode ────────────────────────────────────────────────────────────
def cmd_finding(a):
    d = os.path.abspath(a.verify_dir)
    if a.feature not in load_features(d):
        sys.stderr.write("no feature %s in %s/features\n" % (a.feature, d))
        return 2
    wt = os.path.abspath(a.worktree)
    r = git(wt, "rev-parse", "--verify", "HEAD^{commit}")
    sha = r.stdout.strip()
    if r.returncode != 0 or not SHA_RE.match(sha):
        sys.stderr.write("%s has no commit\n" % wt)
        return 2
    base = os.environ.get("VERIFY_EVIDENCE_DIR") or os.path.join(toplevel(wt) or wt, ".verify")
    path = os.path.join(base, "findings", "%s-%s.json" % (a.feature, sha[:12]))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"kind": "product-bug", "feature": a.feature, "sha": sha,
                   "expected": a.expected, "observed": a.observed}, f, indent=2,
                  sort_keys=True)
        f.write("\n")
    _out("FINDING: %s" % path)
    return 0


def _proof_sections(text):
    secs = sections(text)
    return {k: secs.get(k, "").strip() for k in ("What it is", "Drive it", "Proof")}


def cmd_check_maintain(a):
    """Fail every feature whose documented behaviour changed since --base while none of
    its source anchors did: that is the map being edited to match the app, which hides a
    bug rather than recording one."""
    # Resolved paths throughout: git reports the real path of the work tree, and on
    # macOS a temp dir under /var is really /private/var, so an unresolved path
    # relativises to ../.. and every feature file looked new (the guard passed).
    d = os.path.realpath(a.verify_dir)
    top = toplevel(d)
    top = os.path.realpath(top) if top else None
    app_root = app_root_of(d)
    if not top or not app_root:
        sys.stderr.write("%s is not a verify skill inside a git work tree\n" % d)
        return 2
    if git(top, "rev-parse", "--verify", a.base + "^{commit}").returncode != 0:
        sys.stderr.write("unknown base %s\n" % a.base)
        return 2
    changed = set(git(top, "diff", "--name-only", a.base, "--").stdout.split())
    changed |= set(git(top, "ls-files", "--others", "--exclude-standard").stdout.split())
    fails = 0
    for fid, (path, text) in load_features(d).items():
        rel = os.path.relpath(path, top).replace(os.sep, "/")
        old = git(top, "show", "%s:%s" % (a.base, rel))
        if old.returncode != 0:
            continue  # a new feature file: nothing documented to rewrite
        if _proof_sections(old.stdout) == _proof_sections(text):
            continue
        anchors = {parse_anchor(x)[0] for x in split_list(feature_meta(old.stdout).get(
            "anchors")) + split_list(feature_meta(text).get("anchors"))}
        app_rel = os.path.relpath(app_root, top).replace(os.sep, "/")
        paths = {("%s/%s" % (app_rel, p) if app_rel != "." else p) for p in anchors if p}
        if any(c == p or c.startswith(p.rstrip("/") + "/") for c in changed for p in paths):
            _out("OK: %s: documented behaviour changed with its source" % fid)
            continue
        fails += 1
        _out("FAIL: %s: the documented behaviour changed but none of its anchors (%s) did "
             "since %s: record a product-bug finding (forge.py finding) and restore the map"
             % (fid, ", ".join(sorted(paths)) or "none", a.base))
    _out("MAINTAIN_RESULT: %s" % ("FAIL (%d)" % fails if fails else "PASS"))
    return 1 if fails else 0


def cmd_contract(a):
    try:
        text = read(a.feature_file)
    except (OSError, UnicodeDecodeError) as e:
        sys.stderr.write("cannot read feature file %s (%s): pass the path of a "
                         "features/<id>.md file\n" % (a.feature_file, e))
        return 2
    _out(json.dumps(parse_contract(text), sort_keys=True))
    return 0


# ── check-proof: the escape-hatch rule ───────────────────────────────────────
# The one escape-hatch rule (spec Terms). factory-conductor holds a copy, and both copies
# pass the same vector list, escape_hatch_vectors.json. On a line that a diff adds to a
# .rs file: `assume(` or `admit(` not preceded by an identifier character or by `::`
# (so `prop_assume(`, `my_assume(` and `kani::assume(` are not escapes, `x.assume(` is),
# or `external_body` inside `#[verifier ...]` on the same line. Comments and strings are
# not skipped: a token there is still reported.
ESCAPE_CALL_RE = re.compile(r"(?<!\w)(?<!::)(assume|admit)\(")
ESCAPE_ATTR_RE = re.compile(r"#\[verifier\b[^\]]*\bexternal_body\b")


def escape_hatches(line):
    """[(column, token)] for every escape hatch on one source line, sorted by column.
    The token is `assume(`, `admit(` or `external_body`; the column is where the match
    starts (for external_body, the `#[verifier` that holds it)."""
    hits = [(m.start(), m.group(1) + "(") for m in ESCAPE_CALL_RE.finditer(line)]
    hits += [(m.start(), "external_body") for m in ESCAPE_ATTR_RE.finditer(line)]
    return sorted(hits)


def added_lines(diff_text):
    """[(head line number, text)] for every line a unified diff of one file adds."""
    out, cur, in_hunk = [], 0, False
    for line in diff_text.split("\n"):
        if line.startswith("@@"):
            m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            in_hunk, cur = bool(m), int(m.group(1)) if m else 0
        elif not in_hunk:
            continue
        elif line.startswith("+"):
            out.append((cur, line[1:]))
            cur += 1
        elif line.startswith(" "):
            cur += 1
    return out


# Flags that keep the user's git config from changing what the diff shows. Renames are
# off, so a moved file's lines all count as added: the scan errs towards a report.
DIFF_FLAGS = ("-c", "core.quotePath=false", "diff", "--no-color", "--no-ext-diff",
              "--no-textconv", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/")


def cmd_check_proof(a):
    """Print every escape hatch that the diff from --base to --head adds to a .rs file."""
    root = os.path.realpath(a.root)
    r = git(root, "rev-parse", "--is-inside-work-tree") if os.path.isdir(root) else None
    top = toplevel(root) if r is not None and r.stdout.strip() == "true" else None
    if not top:
        sys.stderr.write("--root %s is not a git work tree: pass the root of the "
                         "repository whose diff to scan\n" % a.root)
        return 2
    shas = {}
    for flag, ref in (("--base", a.base), ("--head", a.head)):
        r = git(top, "rev-parse", "--verify", "--quiet", "--end-of-options",
                ref + "^{commit}")
        if r.returncode != 0 or not SHA_RE.match(r.stdout.strip()):
            sys.stderr.write("%s %s does not resolve to a commit in %s: pass a branch, "
                             "tag or SHA that exists there\n" % (flag, ref, top))
            return 2
        shas[flag] = r.stdout.strip()
    r = subprocess.run(["git", "-C", top, *DIFF_FLAGS, "--name-only", "-z",
                        shas["--base"], shas["--head"], "--"], capture_output=True)
    if r.returncode != 0:
        sys.stderr.write("git diff %s %s failed in %s: %s\n" % (
            a.base, a.head, top, r.stderr.decode("utf-8", "replace").strip()))
        return 2
    paths = sorted(p for p in r.stdout.decode("utf-8", "surrogateescape").split("\0")
                   if p.endswith(".rs"))
    hits = []
    for path in paths:
        r = subprocess.run(["git", "-C", top, *DIFF_FLAGS, "-U0", shas["--base"],
                            shas["--head"], "--", ":(literal)" + path],
                           capture_output=True)
        if r.returncode != 0:
            sys.stderr.write("git diff of %s failed in %s: %s\n" % (
                path, top, r.stderr.decode("utf-8", "replace").strip()))
            return 2
        for lineno, text in added_lines(r.stdout.decode("utf-8", "replace")):
            hits += [(path, lineno, col, tok) for col, tok in escape_hatches(text)]
    for path, lineno, _col, tok in sorted(hits):
        _out("ESCAPE: %s:%d %s" % (path, lineno, tok))
    _out("CHECK_PROOF_RESULT: %s" % ("FAIL" if hits else "PASS"))
    return 1 if hits else 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="forge.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("detect")
    s.add_argument("--root", default=".")
    s = sub.add_parser("scaffold")
    s.add_argument("--app-root", required=True)
    s.add_argument("--app", required=True)
    for name in ("vendor", "lint"):
        sub.add_parser(name).add_argument("verify_dir")
    s = sub.add_parser("coverage")
    s.add_argument("verify_dir")
    s.add_argument("--manifold")
    s.add_argument("--json", action="store_true")
    s.add_argument("--require-total", action="store_true")
    s = sub.add_parser("seed")
    s.add_argument("verify_dir")
    s.add_argument("--manifold")
    s = sub.add_parser("finding")
    s.add_argument("verify_dir")
    s.add_argument("--feature", required=True)
    s.add_argument("--expected", required=True)
    s.add_argument("--observed", required=True)
    s.add_argument("--worktree", default=".")
    s = sub.add_parser("check-maintain")
    s.add_argument("verify_dir")
    s.add_argument("--base", required=True)
    sub.add_parser("contract").add_argument("feature_file")
    s = sub.add_parser("check-proof")
    s.add_argument("--root", default=".")
    s.add_argument("--base", required=True)
    s.add_argument("--head", required=True)
    a = p.parse_args(argv)
    return {"detect": cmd_detect, "scaffold": cmd_scaffold, "vendor": cmd_vendor,
            "lint": cmd_lint, "coverage": cmd_coverage, "seed": cmd_seed,
            "finding": cmd_finding, "check-maintain": cmd_check_maintain,
            "contract": cmd_contract, "check-proof": cmd_check_proof}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
