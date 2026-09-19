#!/usr/bin/env python3
"""contract_check.py: reference checker for skill-contract v1.

The creed is docs/skill-contract/SPEC.md in github.com/dhanesh/agent-skills.
Stdlib only, Python >= 3.10, offline. Adopting skills vendor this file
byte-identical into their assets/ (`make contract-vendor`).

    contract_check.py check-skill <skill-dir>                                  C1, C2
    contract_check.py check-envelope <file> [--root DIR] [--for SKILL_DIR]
                                            [--rerun] [--json]                 C3-C7, C9
    contract_check.py discover --kind URI [--from SKILL_DIR] [--json]          C8
    contract_check.py check-grant [FILE] --root DIR --action CLASS [--json]    C10
    contract_check.py revoke-grant [ID] --root DIR                             C10

Exit 0 pass, 2 a commandment is violated, 1 usage or internal error. The last
line is always CONTRACT_RESULT: PASS or CONTRACT_RESULT: FAIL (C<n>, ...).

check-grant exits 0 COVERED, 3 ASK or NONE, 2 INVALID, 1 usage; its last line is GRANT: ...
A caller proceeds only on exit 0. revoke-grant prints REVOKED: <path> and exits 0.
"""
import sys

if sys.version_info < (3, 10):
    sys.stderr.write("contract_check.py needs Python >= 3.10, found %d.%d\n"
                     % sys.version_info[:2])
    sys.exit(2)

import argparse  # noqa: E402
import unicodedata  # noqa: E402
import fnmatch  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import secrets  # noqa: E402
import shlex  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
CONTRACT_VERSION = "1"
FENCE_INFO = "json skill-contract"
KIND_RE = re.compile(
    r"^https://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._~-]+)*"
    r"/(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*)/v(?P<ver>[1-9][0-9]*)\Z")
ID_RE = re.compile(
    r"^(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*)-v(?P<ver>[1-9][0-9]*)"
    r"-(?P<ts>[0-9]{8}T[0-9]{6}Z)-(?P<hex>[0-9a-f]{6})\Z")
TIME_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
SHA256_RE = re.compile(r"^[0-9a-f]{64}\Z")
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\Z")
ALLOWED_FRONTMATTER = ("name", "description", "license", "compatibility",
                       "metadata", "allowed-tools")
OUTCOMES = ("passed", "failed", "cantTell", "inapplicable", "untested")
SKILL_DIR_RE = re.compile(r"^\{skill_dir:(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*)\}")
PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}")
BARE_PYTHON_RE = re.compile(r"^(?:python[0-9.]*|py)(?:\.exe)?$", re.IGNORECASE)
GRANT_KIND = "https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1"
ACTION_CLASSES = ("read_only", "local_reversible", "push_branch", "open_pr", "merge",
                  "deploy", "spend", "external_message", "delete")
LOCAL_CLASSES = frozenset({"read_only", "local_reversible"})
GATES = ("auto", "grant", "ask")
# Checker-held floors (A7): no grant can lower these.
# A8: irreversible or externally visible classes are ask-only; no grant can cover them.
IRREVERSIBLE_CLASSES = frozenset({"merge", "deploy", "spend", "external_message", "delete"})
MAX_GRANT_LIFETIME = timedelta(days=7)
FALLBACK_DEFAULT_BRANCHES = frozenset({"main", "master"})
ENVELOPE_MAX_BYTES = 1024 * 1024
DETACHED = "HEAD"  # what current_branch reports for a detached HEAD


# ── SKILL.md reading (no YAML library: a line reader is enough) ──────────────
def split_frontmatter(text):
    """Return (frontmatter_lines, body); frontmatter_lines is None if absent."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    return None, text


def top_level_keys(fm):
    return [m.group(1) for m in (re.match(r"^([A-Za-z][A-Za-z0-9_-]*)\s*:", ln) for ln in fm) if m]


def _unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def frontmatter_value(fm, key):
    """A top-level scalar, including a folded or literal (>- or |) block."""
    for i, ln in enumerate(fm):
        m = re.match(r"^%s\s*:\s*(.*)$" % re.escape(key), ln)
        if not m:
            continue
        v = m.group(1).strip()
        if v in ("", ">", ">-", "|", "|-"):
            parts = []
            for nxt in fm[i + 1:]:
                if nxt.strip() and not nxt[:1].isspace():
                    break
                parts.append(nxt.strip())
            return " ".join(p for p in parts if p)
        return _unquote(v)
    return None


def metadata_value(fm, key):
    inside = False
    for ln in fm:
        if re.match(r"^metadata\s*:\s*$", ln):
            inside = True
            continue
        if inside:
            if ln.strip() and not ln[:1].isspace():
                break
            m = re.match(r"^\s+%s\s*:\s*(.*)$" % re.escape(key), ln)
            if m:
                return _unquote(m.group(1))
    return None


def contract_fences(body):
    """[(h2 heading or None, block text)] for every `json skill-contract` fence."""
    out, section, buf, in_other = [], None, None, False
    for ln in body.splitlines():
        if buf is not None:
            if ln.strip() == "```":
                out.append((section, "\n".join(buf)))
                buf = None
            else:
                buf.append(ln)
            continue
        if ln.startswith("```"):
            if not in_other and ln.strip() == "```" + FENCE_INFO:
                buf = []
            else:
                in_other = not in_other
            continue
        if in_other:
            continue
        m = re.match(r"^##\s+(.+?)\s*$", ln)
        if m:
            section = m.group(1)
    return out


def kind_parts(kind):
    m = KIND_RE.match(kind) if isinstance(kind, str) else None
    return (m.group("name"), int(m.group("ver"))) if m else None


# ── Commandments 1 and 2: the skill's declaration ───────────────────────────
def check_skill(skill_dir):
    """Returns {skill, adopter, contract, violations: [(n, detail)], warnings}."""
    report = {"skill": os.path.basename(os.path.normpath(skill_dir)), "adopter": False,
              "contract": None, "violations": [], "warnings": []}
    viol, warn = report["violations"], report["warnings"]
    path = os.path.join(skill_dir, "SKILL.md")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, ValueError) as exc:  # ValueError: UnicodeDecodeError, not UTF-8
        viol.append((1, "cannot read %s: %s" % (path, exc)))
        return report
    fm, body = split_frontmatter(text)
    if fm is None:
        viol.append((1, "SKILL.md has no YAML frontmatter"))
        return report
    report["skill"] = frontmatter_value(fm, "name") or report["skill"]
    for key in top_level_keys(fm):
        if key not in ALLOWED_FRONTMATTER:
            viol.append((1, "frontmatter key %r is outside the Agent Skills allowed set (%s)"
                         % (key, ", ".join(ALLOWED_FRONTMATTER))))
    optin = metadata_value(fm, "skill-contract")
    fences = contract_fences(body)
    if optin is None and not fences:
        return report
    report["adopter"] = True
    if optin is None:
        viol.append((1, "a json skill-contract block is present but metadata.skill-contract is not"))
    elif optin != CONTRACT_VERSION:
        viol.append((1, 'metadata.skill-contract must be "1", found %r' % optin))
    if len(fences) != 1:
        viol.append((1, "expected exactly one json skill-contract block, found %d" % len(fences)))
        return report
    section, text_block = fences[0]
    if section != "Contract":
        viol.append((1, "the json skill-contract block must sit under '## Contract', found under %r"
                     % section))
    try:
        contract = json.loads(text_block)
    except ValueError as exc:
        viol.append((1, "the contract block is not valid JSON: %s" % exc))
        return report
    if not isinstance(contract, dict):
        viol.append((1, "the contract block must be a JSON object"))
        return report
    for key in contract:
        if key not in ("provides", "consumes") and not key.startswith("x-"):
            viol.append((1, "unknown contract key %r; allowed: provides, consumes, x-*" % key))
    for key in ("provides", "consumes"):
        kinds = contract.get(key)
        if not isinstance(kinds, list) or not all(isinstance(k, str) for k in kinds):
            viol.append((1, "%r must be a list of kind URIs" % key))
            continue
        seen = set()
        for k in kinds:
            if not KIND_RE.match(k):
                viol.append((2, "%s kind %r is not an https URI ending in /v<N>" % (key, k)))
            if k in seen:
                viol.append((2, "%s lists kind %r twice" % (key, k)))
            seen.add(k)
    report["contract"] = contract
    desc = (frontmatter_value(fm, "description") or "").lower()
    for key in ("provides", "consumes"):
        for k in contract.get(key) if isinstance(contract.get(key), list) else []:
            parts = kind_parts(k)
            if parts and parts[0] not in desc:
                warn.append("description does not name the %s kind %r (commandment 1 SHOULD)"
                            % (key, parts[0]))
    return report


# ── Producer helpers (commandments 3-6) ─────────────────────────────────────
def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def new_id(kind, now=None):
    name, ver = kind_parts(kind)
    ts = (now or utc_now()).strftime("%Y%m%dT%H%M%SZ")
    return "%s-v%d-%s-%s" % (name, ver, ts, secrets.token_hex(3))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_path(p):
    """Commandment 3: relative, forward slashes, no '..', no NUL."""
    return (isinstance(p, str) and p != "" and "\\" not in p and "\0" not in p
            and not p.startswith("/") and not re.match(r"^[A-Za-z]:", p)
            and ".." not in p.split("/"))


def pin(root, rel):
    return {"name": rel, "digest": {"sha256": sha256_file(os.path.join(root, *rel.split("/")))}}


def build_statement(kind, skill, version, root, subjects, payload, assertions=(),
                    was_revision_of=None, now=None):
    now = now or utc_now()
    return {
        "_type": STATEMENT_TYPE,
        "subject": [pin(root, s) for s in subjects],
        "predicateType": kind,
        "predicate": {
            "skillContract": CONTRACT_VERSION,
            "id": new_id(kind, now),
            "wasAttributedTo": {"skill": skill, "version": version},
            "generatedAtTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "wasRevisionOf": was_revision_of,
            "payload": payload,
            "assertions": list(assertions),
        },
    }


def assertion(test, skill, outcome, root, subjects, command=None, run_url=None):
    a = {"test": test, "assertedBy": {"skill": skill}, "result": {"outcome": outcome},
         "subject": [pin(root, s) for s in subjects]}
    if command is not None:
        a["command"] = list(command)
    if run_url is not None:
        a["run_url"] = run_url
    return a


def envelope_dir(root):
    return os.path.join(root, ".skill-contract", "envelopes")


def write_envelope(root, statement):
    """Commandment 4: create exclusively, never overwrite. Returns the path."""
    eid = statement.get("predicate", {}).get("id") if isinstance(statement, dict) else None
    if not (isinstance(eid, str) and ID_RE.match(eid)):
        raise ValueError("refusing to write an envelope whose id %r does not match the id grammar" % (eid,))
    d = envelope_dir(root)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, eid + ".json")
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        json.dump(statement, f, indent=2, sort_keys=True)
        f.write("\n")
    return path


# ── Commandments 3-6: the envelope's structure ──────────────────────────────
def _check_subjects(subjects, where, viol):
    if not isinstance(subjects, list) or not subjects:
        viol.append((3, "%s must be a non-empty list" % where))
        return
    for s in subjects:
        if not isinstance(s, dict):
            viol.append((3, "%s entries must be objects" % where))
            continue
        name = s.get("name")
        if not safe_path(name):
            viol.append((3, "%s path %r must be relative, use forward slashes, and contain no '..'"
                         % (where, name)))
        digest = s.get("digest")
        if not isinstance(digest, dict) or not SHA256_RE.match(str(digest.get("sha256", ""))):
            viol.append((5, "%s %r has no sha256 digest" % (where, name)))


def _check_command(cmd, viol):
    if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) for a in cmd):
        viol.append((6, "command must be a non-empty list of strings"))
        return
    head = cmd[0]
    if head != "{python}":
        if "/" in head or "\\" in head or re.match(r"^[A-Za-z]:", head):
            viol.append((6, "command names an interpreter or absolute path %r; "
                            "use {python} or a bare program name" % head))
        elif BARE_PYTHON_RE.match(head):
            viol.append((6, "command names the interpreter %r; use {python}" % head))
    for i, arg in enumerate(cmd):
        if i == 0 and arg == "{python}":
            continue
        m = SKILL_DIR_RE.match(arg)
        rest = arg[m.end():] if m else arg
        if PLACEHOLDER_RE.search(rest):
            viol.append((6, "command argument %r uses a placeholder other than a leading "
                            "{python} or {skill_dir:<name>}" % arg))
        elif not m and i > 0 and (arg.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", arg)):
            viol.append((6, "command argument %r is an absolute path" % arg))


def _check_assertion(a, viol):
    if not isinstance(a, dict):
        viol.append((6, "each assertion must be an object"))
        return
    test = a.get("test")
    if not isinstance(test, str) or not test:
        viol.append((6, "an assertion has no test"))
    by = a.get("assertedBy")
    if not (isinstance(by, dict) and len(by) == 1 and (
            SKILL_NAME_RE.match(str(by.get("skill", "")))
            or (isinstance(by.get("human"), str) and by["human"].strip()))):
        viol.append((6, "assertion %r: assertedBy must be {skill} or {human}" % (test,)))
    res = a.get("result")
    if not (isinstance(res, dict) and res.get("outcome") in OUTCOMES):
        viol.append((6, "assertion %r: result.outcome must be one of %s" % (test, ", ".join(OUTCOMES))))
    has_cmd, has_url = "command" in a, "run_url" in a
    if has_cmd == has_url:
        viol.append((6, "assertion %r must carry exactly one of command or run_url" % (test,)))
    if has_cmd:
        _check_command(a["command"], viol)
    if has_url and not (isinstance(a["run_url"], str) and a["run_url"].startswith("https://")):
        viol.append((6, "assertion %r: run_url must be an https URL" % (test,)))
    if "subject" in a:
        _check_subjects(a["subject"], "assertion %r subject" % (test,), viol)


def check_statement(st):
    """Structural checks for commandments 3-6. Returns [(n, detail)]."""
    if not isinstance(st, dict):
        return [(3, "an envelope must be a JSON object")]
    viol = []
    if st.get("_type") != STATEMENT_TYPE:
        viol.append((3, "_type must be %s" % STATEMENT_TYPE))
    kind = st.get("predicateType")
    parts = kind_parts(kind)
    if parts is None:
        viol.append((3, "predicateType %r is not a kind URI (https, ending in /v<N>)" % (kind,)))
    _check_subjects(st.get("subject"), "subject", viol)
    pred = st.get("predicate")
    if not isinstance(pred, dict):
        viol.append((3, "predicate must be an object"))
        return viol
    if pred.get("skillContract") != CONTRACT_VERSION:
        viol.append((3, 'predicate.skillContract must be "1"'))
    eid = pred.get("id")
    m = ID_RE.match(eid) if isinstance(eid, str) else None
    if not m:
        viol.append((4, "id %r does not match <kind-name>-v<N>-<yyyymmddThhmmssZ>-<6 hex>" % (eid,)))
    elif parts and (m.group("name"), int(m.group("ver"))) != parts:
        viol.append((4, "id %r does not match predicateType %r" % (eid, kind)))
    rev = pred.get("wasRevisionOf")
    if rev is not None and not (isinstance(rev, str) and ID_RE.match(rev)):
        viol.append((4, "wasRevisionOf must be null or an envelope id"))
    who = pred.get("wasAttributedTo")
    if not (isinstance(who, dict) and SKILL_NAME_RE.match(str(who.get("skill", "")))
            and isinstance(who.get("version"), str) and who["version"]):
        viol.append((3, "wasAttributedTo must be {skill, version}"))
    if not TIME_RE.match(str(pred.get("generatedAtTime", ""))):
        viol.append((3, "generatedAtTime must be RFC 3339 UTC (YYYY-MM-DDThh:mm:ssZ)"))
    if not isinstance(pred.get("payload"), dict):
        viol.append((3, "payload must be an object"))
    asserts = pred.get("assertions")
    if not isinstance(asserts, list):
        viol.append((6, "assertions must be a list"))
        return viol
    for a in asserts:
        _check_assertion(a, viol)
    return viol


def load_envelope(path):
    try:
        with open(path, "rb") as f:
            raw = f.read(ENVELOPE_MAX_BYTES + 1)
        if len(raw) > ENVELOPE_MAX_BYTES:
            return None, [(3, "cannot read the envelope: larger than %d bytes"
                           % ENVELOPE_MAX_BYTES)]
        return json.loads(raw.decode("utf-8")), []
    except (OSError, ValueError, RecursionError, MemoryError) as exc:
        return None, [(3, "cannot read the envelope: %s" % (exc.__class__.__name__
                                                            if isinstance(exc, (RecursionError, MemoryError))
                                                            else exc))]


# ── Commandment 8: find partners, never require them ────────────────────────
def _plugin_skill_roots(home, warnings):
    idx = os.path.join(home, ".claude", "plugins", "installed_plugins.json")
    if not os.path.isfile(idx):
        warnings.append("no Claude Code plugin index at %s; plugin skills not searched" % idx)
        return []
    try:
        with open(idx, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        warnings.append("unreadable plugin index %s: %s" % (idx, exc))
        return []
    if not isinstance(data, dict) or data.get("version") != 2 or not isinstance(data.get("plugins"), dict):
        warnings.append("plugin index %s has an unknown format; plugin skills not searched" % idx)
        return []
    roots = []
    for key, entries in sorted(data["plugins"].items()):
        for e in entries if isinstance(entries, list) else [entries]:
            if not isinstance(e, dict):
                continue
            if e.get("scope") != "user":
                warnings.append("plugin %s has scope %r; only user-scope plugins are searched"
                                % (key, e.get("scope")))
                continue
            if isinstance(e.get("installPath"), str):
                roots.append(os.path.join(e["installPath"], "skills"))
    return roots


def skill_roots(env=None, from_dir=None, cwd=None, home=None, warnings=None):
    """[(label, dir)] in precedence order: path, sibling, project, user, plugin."""
    env = os.environ if env is None else env
    cwd = cwd or os.getcwd()
    home = home or env.get("HOME") or env.get("USERPROFILE") or os.path.expanduser("~")
    warnings = [] if warnings is None else warnings
    roots = [("path", d) for d in (env.get("SKILL_CONTRACT_PATH") or "").split(os.pathsep) if d]
    if from_dir:
        roots.append(("sibling", os.path.dirname(os.path.abspath(from_dir))))
    roots += [("project", os.path.join(cwd, b, "skills")) for b in (".agents", ".claude")]
    roots += [("user", os.path.join(home, b, "skills")) for b in (".agents", ".claude")]
    roots += [("plugin", r) for r in _plugin_skill_roots(home, warnings)]
    return roots


def iter_skills(roots):
    """Yield (label, skill_dir, name, frontmatter_lines, read_error), deduplicated by realpath.

    A SKILL.md that cannot be read or decoded as UTF-8 is still yielded, with
    frontmatter None and read_error set, so discovery reports it as invalid
    instead of dropping it or failing the caller.
    """
    seen = set()
    for label, root in roots:
        if not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for entry in entries:
            d = os.path.join(root, entry)
            md = os.path.join(d, "SKILL.md")
            if not os.path.isfile(md):
                continue
            real = os.path.realpath(d)
            if real in seen:
                continue
            seen.add(real)
            try:
                with open(md, encoding="utf-8") as f:
                    fm, _ = split_frontmatter(f.read())
            except (OSError, ValueError) as exc:  # ValueError: UnicodeDecodeError
                yield label, d, entry, None, "cannot read %s: %s" % (md, exc)
                continue
            yield label, d, (fm and frontmatter_value(fm, "name")) or entry, fm, None


def skill_index(env=None, from_dir=None, cwd=None, home=None):
    idx = {}
    for _label, d, name, _fm, err in iter_skills(skill_roots(env, from_dir, cwd, home, [])):
        if err is None:
            idx.setdefault(name, d)
    return idx


def discover(kind, env=None, from_dir=None, cwd=None, home=None):
    out = {"kind": kind, "consumers": [], "shadowed": [], "invalid": [], "warnings": []}
    self_name = None
    if from_dir and os.path.isfile(os.path.join(from_dir, "SKILL.md")):
        try:
            with open(os.path.join(from_dir, "SKILL.md"), encoding="utf-8") as f:
                fm, _ = split_frontmatter(f.read())
            self_name = fm and frontmatter_value(fm, "name")
        except (OSError, ValueError):  # ValueError: UnicodeDecodeError
            self_name = None
    names = set()
    for label, d, name, fm, err in iter_skills(skill_roots(env, from_dir, cwd, home, out["warnings"])):
        if name in names:
            out["shadowed"].append({"skill": name, "dir": d, "root": label})
            continue
        names.add(name)
        if err is not None:
            out["invalid"].append({"skill": name, "dir": d, "root": label,
                                   "violations": ["C1: " + err]})
            continue
        if not fm or metadata_value(fm, "skill-contract") is None:
            continue
        rep = check_skill(d)
        if rep["violations"]:
            out["invalid"].append({"skill": name, "dir": d, "root": label,
                                   "violations": ["C%d: %s" % v for v in rep["violations"]]})
            continue
        if name != self_name and kind in (rep["contract"].get("consumes") or []):
            out["consumers"].append({"skill": name, "dir": d, "root": label})
    return out


# ── Commandments 5, 7 and 9: staleness, claim status, validation for a receiver ─
def stale_names(root, subjects):
    """Subject paths whose file is missing or whose sha256 no longer matches."""
    out = []
    for s in subjects or []:
        name = s.get("name")
        want = (s.get("digest") or {}).get("sha256")
        if not safe_path(name):
            continue
        p = os.path.join(root, *name.split("/"))
        if not os.path.isfile(p) or sha256_file(p) != want:
            out.append(name)
    return out


def claim_status(statement, root=None, rerun_results=None):
    """Commandment 7. First match wins: STALE, FAILED, PROVEN, CLAIMED, OPEN."""
    pred = statement["predicate"]
    producer = (pred.get("wasAttributedTo") or {}).get("skill")
    rerun_results = rerun_results or {}
    by_test = {}
    for i, a in enumerate(pred.get("assertions") or []):
        by_test.setdefault(a["test"], []).append((i, a))
    status = {}
    for test, items in by_test.items():
        if root is not None and any(stale_names(root, a.get("subject")) for _, a in items):
            status[test] = "STALE"
        elif any(a["result"]["outcome"] == "failed" or rerun_results.get(i) is False for i, a in items):
            status[test] = "FAILED"
        elif any(a["result"]["outcome"] == "passed"
                 and (a["assertedBy"].get("skill") != producer or "run_url" in a
                      or rerun_results.get(i) is True)
                 for i, a in items):
            status[test] = "PROVEN"
        elif any(a["result"]["outcome"] == "passed" for _, a in items):
            status[test] = "CLAIMED"
        else:
            status[test] = "OPEN"
    return status


def resolve_python(env=None, min_version=(3, 10)):
    """The {python} lookup: SKILL_CONTRACT_PYTHON, python3, python, py -3; first to pass the probe."""
    env = dict(os.environ if env is None else env)
    candidates = []
    override = env.get("SKILL_CONTRACT_PYTHON", "").strip()
    if override:
        candidates.append([p.strip('"') for p in shlex.split(override, posix=(os.name != "nt"))])
    candidates += [["python3"], ["python"], ["py", "-3"]]
    probe = "import sys; sys.exit(0 if sys.version_info >= (%d, %d) else 1)" % min_version
    for cand in candidates:
        exe = shutil.which(cand[0], path=env.get("PATH"))
        if exe is None:
            continue
        try:
            r = subprocess.run([exe] + cand[1:] + ["-I", "-c", probe],
                               capture_output=True, timeout=10, env=env)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return [exe] + cand[1:]
    return None


def resolve_command(cmd, python_argv, skill_dirs):
    out = []
    for i, arg in enumerate(cmd):
        if i == 0 and arg == "{python}":
            if not python_argv:
                raise LookupError("no Python >= 3.10 found")
            out.extend(python_argv)
            continue
        m = SKILL_DIR_RE.match(arg)
        if m:
            d = skill_dirs.get(m.group("name"))
            if d is None:
                raise LookupError("skill %s is not installed" % m.group("name"))
            out.append(d + arg[m.end():])
            continue
        out.append(arg)
    return out


def rerun_assertions(statement, root, python_argv, skill_dirs):
    """Re-run each assertion's command from `root`. {index: passed?}; unresolvable ones are skipped."""
    results = {}
    for i, a in enumerate(statement["predicate"].get("assertions") or []):
        if "command" not in a:
            continue
        try:
            argv = resolve_command(a["command"], python_argv, skill_dirs)
        except LookupError:
            continue
        try:
            r = subprocess.run(argv, cwd=root, capture_output=True, timeout=600)
            results[i] = r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            results[i] = False
    return results


def check_envelope(path, root=None, for_skill=None, rerun=False, env=None):
    """Everything a receiver checks before acting (commandments 3-7, 9)."""
    report = {"envelope": path, "violations": [], "stale": [], "claims": {}}
    st, report["violations"] = load_envelope(path)
    if st is None:
        return report
    report["violations"] = check_statement(st)
    if report["violations"]:
        return report
    if for_skill is not None:
        rep = check_skill(for_skill)
        consumes = (rep["contract"] or {}).get("consumes") or []
        if rep["violations"]:
            report["violations"].append(
                (9, "%s's own contract is invalid: C%d: %s" % ((rep["skill"],) + rep["violations"][0])))
            return report
        if st["predicateType"] not in consumes:
            report["violations"].append(
                (9, "%s does not consume %s" % (rep["skill"], st["predicateType"])))
            return report
    results = {}
    if root is not None:
        report["stale"] = stale_names(root, st["subject"])
        if rerun:
            env = dict(os.environ if env is None else env)
            results = rerun_assertions(st, root, resolve_python(env), skill_index(env=env, cwd=root))
    report["claims"] = claim_status(st, root, results)
    return report


# ── Commandment 10: the autonomy grant ─────────────────────────────────────
def _parse_time(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def grant_violations(st):
    """Grant-specific problems (after check_statement passed). [] = valid.

    Order follows SPEC: the payload's shape, then human attribution (skipped
    for a revoked revision), then the gate-policy floors.
    """
    if not isinstance(st, dict) or st.get("predicateType") != GRANT_KIND:
        return ["predicateType must be %s" % GRANT_KIND]
    out = []
    pred = st["predicate"]
    p = pred.get("payload") or {}
    scope = p.get("scope")
    if not (isinstance(scope, dict) and isinstance(scope.get("repo"), str)
            and isinstance(scope.get("branch_pattern"), str) and scope["branch_pattern"]):
        out.append("payload.scope must be {repo, branch_pattern}")
    for d in p.get("decisions") if isinstance(p.get("decisions"), list) else [None]:
        if not (isinstance(d, dict) and all(isinstance(d.get(k), str) and d[k].strip()
                                            for k in ("id", "question", "answer"))):
            out.append("each decision must carry non-empty id, question and answer")
            break
    if "require_signature" in p:  # A8 dropped signing: fail closed rather than ignore it
        out.append("payload.require_signature is not a grant field: grants are never signed (A8)")
    try:
        expires = _parse_time(p.get("expires_at") if TIME_RE.match(str(p.get("expires_at", "")))
                              else "")
    except ValueError:
        expires = None
        out.append("payload.expires_at must be RFC 3339 UTC (YYYY-MM-DDThh:mm:ssZ)")
    try:
        generated = _parse_time(pred.get("generatedAtTime"))
    except (TypeError, ValueError):
        generated = None
        out.append("generatedAtTime must be a real date")
    if expires and generated and expires - generated > MAX_GRANT_LIFETIME:
        out.append("a grant may live at most 7 days (expires_at - generatedAtTime)")
    if not isinstance(p.get("revoked"), bool):
        out.append("payload.revoked must be a boolean")
    subjects = st.get("subject") if isinstance(st.get("subject"), list) else []
    names = {_branch_key(os.path.normpath(s["name"])) for s in subjects
             if isinstance(s, dict) and isinstance(s.get("name"), str)}
    if len(names) < 2:  # same file twice (or twice by case/`./`) pins only one thing
        out.append("a grant must pin at least 2 distinct subjects: the spec and the plan it was"
                   " approved for")
    if p.get("revoked") is not True:
        accepted = [a for a in pred.get("assertions") or [] if a.get("test") == "grant-accepted"]
        if len(accepted) != 1:
            out.append("a grant needs exactly one grant-accepted assertion, asserted by a human")
        elif not (isinstance(accepted[0].get("assertedBy"), dict)
                  and isinstance(accepted[0]["assertedBy"].get("human"), str)
                  and accepted[0]["assertedBy"]["human"].strip()
                  and (accepted[0].get("result") or {}).get("outcome") == "passed"):
            out.append("grant-accepted must be asserted by a human with outcome passed")
    policy = p.get("gate_policy")
    if not isinstance(policy, dict):
        out.append("payload.gate_policy must be an object")
        policy = {}
    for cls, gate in policy.items():
        if cls not in ACTION_CLASSES:
            out.append("gate_policy names unknown action class %r" % cls)
        elif gate not in GATES:
            out.append("gate_policy[%r] must be one of auto, grant, ask" % cls)
        elif cls in IRREVERSIBLE_CLASSES and gate != "ask":
            out.append("gate_policy[%r] must be ask: a grant never covers it (A8)" % cls)
        elif gate == "auto" and cls not in LOCAL_CLASSES:
            out.append("gate_policy[%r] may not be auto; at most grant" % cls)
    return out


def _grant_envelopes(root, strict=True):
    """[(path, statement)] for grant envelopes under root.

    strict=True keeps only statements that pass check_statement (candidates
    that may cover something). strict=False keeps any JSON object of the grant
    kind: used for supersession, so a revision fails closed even if malformed.
    """
    d = envelope_dir(root)
    if not os.path.isdir(d):
        return []
    out = []
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        st, err = load_envelope(os.path.join(d, f))
        if err or not isinstance(st, dict) or st.get("predicateType") != GRANT_KIND \
                or not isinstance(st.get("predicate"), dict):
            continue
        if strict and (check_statement(st) or st["predicate"].get("id") + ".json" != f):
            continue  # a candidate must be well-formed and filed under its own id
        out.append((os.path.join(d, f), st))
    return out


def is_superseded(root, grant_id):
    """True when any grant envelope under root names grant_id in wasRevisionOf."""
    return any(_revision_of(st) == grant_id for _, st in _grant_envelopes(root, strict=False))


def _revision_of(st):
    rev = st["predicate"].get("wasRevisionOf")
    return rev if isinstance(rev, str) else None


def latest_grant(root):
    """The path of the newest valid-shaped grant that no revision supersedes."""
    revised = {_revision_of(st) for _, st in _grant_envelopes(root, strict=False)}
    heads = [(st["predicate"]["generatedAtTime"], st["predicate"]["id"], p)
             for p, st in _grant_envelopes(root) if st["predicate"]["id"] not in revised]
    return max(heads)[2] if heads else None


GIT_REDIRECT_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                     "GIT_CEILING_DIRECTORIES")


def git_env():
    """os.environ without the variables that make git look at another repository, so an
    inherited GIT_DIR cannot redirect the branch probes away from root."""
    return {k: v for k, v in os.environ.items() if k not in GIT_REDIRECT_VARS}


def current_branch(root):
    """The checked-out branch (DETACHED when HEAD is detached), or None when git cannot say.

    Uses the full ref (refs/heads/<name>), never --abbrev-ref, which answers
    "heads/main" when a tag is also named main.
    """
    try:
        r = subprocess.run(["git", "-C", root, "symbolic-ref", "-q", "HEAD"],
                           capture_output=True, text=True, timeout=10, env=git_env())
    except (OSError, subprocess.SubprocessError):
        return None
    ref = r.stdout.strip()
    if r.returncode == 0 and ref.startswith("refs/heads/") and len(ref) > len("refs/heads/"):
        return ref[len("refs/heads/"):]
    if r.returncode == 1 and not ref:
        # -q exits 1 silently when HEAD is not symbolic. It is detached only when HEAD
        # names a real commit; anything else is git failing, which the caller fails closed on.
        try:
            v = subprocess.run(["git", "-C", root, "rev-parse", "-q", "--verify", "HEAD^{commit}"],
                               capture_output=True, text=True, timeout=10, env=git_env())
        except (OSError, subprocess.SubprocessError):
            return None
        return DETACHED if v.returncode == 0 else None
    return None


def in_git_work_tree(root):
    """True when root or any parent holds a .git directory or file."""
    d = os.path.abspath(root)
    while True:
        if os.path.lexists(os.path.join(d, ".git")):
            return True
        parent = os.path.dirname(d)
        if parent == d:
            return False
        d = parent


def _branch_key(name):
    """Compare branch names as a case-insensitive filesystem would (and then some)."""
    return unicodedata.normalize("NFKC", name).casefold()


def detect_default_branches(root):
    """main, master, and the target of origin/HEAD when there is one."""
    out = set(FALLBACK_DEFAULT_BRANCHES)
    try:
        r = subprocess.run(["git", "-C", root, "symbolic-ref", "refs/remotes/origin/HEAD"],
                           capture_output=True, text=True, timeout=10, env=git_env())
    except (OSError, subprocess.SubprocessError):
        return out
    ref = r.stdout.strip() if r.returncode == 0 else ""
    if ref.startswith("refs/remotes/origin/") and len(ref) > len("refs/remotes/origin/"):
        out.add(ref[len("refs/remotes/origin/"):])
    return out


def _git(root, *args, stdin=None):
    """Run git on root with the redirect variables scrubbed; the CompletedProcess, or None
    when git cannot be run at all."""
    try:
        return subprocess.run(["git", "-C", root] + list(args), capture_output=True,
                              input=stdin, timeout=30, env=git_env())
    except (OSError, subprocess.SubprocessError):
        return None


def grant_is_tracked(root, path):
    """True when git tracks (or has staged) the grant file, False when it does not, None
    when git cannot say. A grant is one person's acceptance: committed, it covers every clone."""
    r = _git(root, "ls-files", "-z", "--cached", "--", os.path.realpath(path))
    if r is None or r.returncode != 0:
        return None  # includes a grant outside the work tree: git refuses the pathspec
    return bool(r.stdout.strip(b"\0"))


# CI configuration runs with the repository's secrets, so pushing a change to it is
# `deploy`, not `push_branch` or `open_pr` (A8). Directories match at the repo top;
# the file names match at any depth, since Jenkins and GitLab can point anywhere.
CI_CONFIG_DIRS = (".github/workflows/", ".github/actions/", ".circleci/", ".buildkite/")
CI_CONFIG_FILES = frozenset({".gitlab-ci.yml", "azure-pipelines.yml", "Jenkinsfile",
                             "bitbucket-pipelines.yml", ".drone.yml", ".travis.yml"})


def is_ci_config(rel):
    """True when a repo-relative, forward-slash path is CI configuration."""
    return rel.startswith(CI_CONFIG_DIRS) or rel.rsplit("/", 1)[-1] in CI_CONFIG_FILES


def changed_since_default(root, default_branches):
    """Paths the commits on HEAD change relative to the default branch, or None when git
    cannot say. Each local or origin ref named like a default branch contributes the diff
    from its merge base with HEAD; with none, every commit on HEAD counts (the empty tree).
    Only commits are compared: staged or uncommitted edits cannot be pushed without a
    commit, and a commit made later is seen by the check that precedes that push."""
    keys = {_branch_key(b) for b in default_branches}
    r = _git(root, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes/origin")
    if r is None or r.returncode != 0:
        return None
    refs = []
    for ref in r.stdout.decode("utf-8", "replace").splitlines():
        name = ref.split("/", 2)[2] if ref.startswith("refs/heads/") else ref.split("/", 3)[-1]
        if name != "HEAD" and _branch_key(name) in keys:
            refs.append(ref)
    bases = []
    for ref in refs:
        m = _git(root, "merge-base", ref, "HEAD")
        if m is None or m.returncode != 0 or not m.stdout.strip():
            return None  # unrelated histories or no HEAD: cannot bound the push, fail closed
        bases.append(m.stdout.strip().decode("ascii", "replace"))
    if not bases:
        e = _git(root, "hash-object", "-t", "tree", "--stdin", stdin=b"")
        if e is None or e.returncode != 0 or not e.stdout.strip():
            return None
        bases.append(e.stdout.strip().decode("ascii", "replace"))
    changed = set()
    for base in bases:
        d = _git(root, "-c", "diff.relative=false", "diff", "--no-ext-diff", "--no-renames",
                 "--name-only", "-z", base, "HEAD", "--")
        if d is None or d.returncode != 0:
            return None
        changed.update(p for p in d.stdout.decode("utf-8", "surrogateescape").split("\0") if p)
    return changed


def check_grant(root, action, path=None, now=None, branch=None, default_branches=None):
    """Commandment 10: does a grant cover `action`? First failing check wins.

    default_branches: the repo's default branch names; None detects them
    (origin/HEAD, else main and master).

    Returns {status: COVERED|ASK|INVALID|NONE, id, reason, gate, path,
    violations}. A caller proceeds only on COVERED.
    """
    rep = {"status": "NONE", "id": None, "reason": None, "gate": None,
           "path": None, "violations": []}
    path = path or latest_grant(root)
    if path is None:
        rep["reason"] = "no-grant"
        return rep
    rep["path"] = path
    st, err = load_envelope(path)
    viol = ["C%d: %s" % v for v in (err or check_statement(st))]
    if not viol:
        viol = ["C10: %s" % v for v in grant_violations(st)]
    if viol:
        rep.update(status="INVALID", reason="invalid", violations=viol)
        return rep
    pred, p = st["predicate"], st["predicate"]["payload"]
    rep["id"] = pred["id"]

    def ask(reason):
        rep.update(status="ASK", reason=reason)
        return rep

    if p["revoked"]:
        return ask("revoked")
    if is_superseded(root, pred["id"]):
        return ask("superseded")
    now = now or utc_now()
    expires = _parse_time(p["expires_at"])
    if now >= expires:
        return ask("expired")
    if expires > now + MAX_GRANT_LIFETIME:
        return ask("lifetime")
    if stale_names(root, st["subject"]):
        return ask("stale")
    in_git = in_git_work_tree(root)
    branch = branch if branch is not None else current_branch(root)
    if branch is None and in_git:
        return ask("branch-unknown")  # inside git but git cannot answer: fail closed
    if branch == DETACHED:
        # A rebase started on the default branch detaches HEAD, and `rebase --continue`
        # then advances that branch: no pattern, not even "*", covers a detached HEAD.
        return ask("detached")
    if branch is not None:
        if default_branches is None:
            default_branches = detect_default_branches(root)
        if _branch_key(branch) in {_branch_key(b) for b in default_branches}:
            return ask("default-branch")
        if not fnmatch.fnmatchcase(branch, p["scope"]["branch_pattern"]):
            return ask("branch")
    if in_git and grant_is_tracked(root, path) is not False:
        return ask("tracked")  # committed, staged, or git cannot say: fail closed
    gate = p["gate_policy"].get(action, "ask")
    rep["gate"] = gate
    if gate not in ("auto", "grant"):
        return ask("gate-ask")
    if in_git and action in ("push_branch", "open_pr"):
        if default_branches is None:
            default_branches = detect_default_branches(root)
        changed = changed_since_default(root, default_branches)
        if changed is None or any(is_ci_config(c) for c in changed):
            return ask("ci-config")  # CI runs with the repo's secrets: that push is deploy
    rep["status"] = "COVERED"
    return rep


def revoke_grant(root, grant_id=None, now=None):
    """Write a revision with revoked: true. Tightening is always allowed: no human needed."""
    if grant_id is None:
        path = latest_grant(root)
    elif isinstance(grant_id, str) and ID_RE.match(grant_id):
        path = os.path.join(envelope_dir(root), grant_id + ".json")
    else:
        raise ValueError("%r is not an envelope id" % (grant_id,))
    st, err = load_envelope(path) if path and os.path.isfile(path) else (None, [(3, "no such grant")])
    if st is None or err or check_statement(st) or st.get("predicateType") != GRANT_KIND:
        raise ValueError("no valid grant to revoke under %s" % envelope_dir(root))
    if grant_id is not None and st["predicate"]["id"] != grant_id:
        raise ValueError("%s.json holds grant %s, not %s" % (grant_id, st["predicate"]["id"], grant_id))
    if is_superseded(root, st["predicate"]["id"]):
        raise ValueError("%s is superseded; revoke the newest revision" % st["predicate"]["id"])
    now = now or utc_now()
    pred = st["predicate"]
    payload = dict(pred["payload"], revoked=True)
    rev = {"_type": STATEMENT_TYPE, "subject": st["subject"], "predicateType": GRANT_KIND,
           "predicate": {"skillContract": CONTRACT_VERSION, "id": new_id(GRANT_KIND, now),
                         "wasAttributedTo": pred["wasAttributedTo"],
                         "generatedAtTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "wasRevisionOf": pred["id"], "payload": payload, "assertions": []}}
    return write_envelope(root, rev)


# ── CLI ─────────────────────────────────────────────────────────────────────
def finish(violations, lines=()):
    for ln in lines:
        print(ln)
    for n, detail in violations:
        print("FAIL: C%d: %s" % (n, detail))
    if violations:
        codes = sorted({n for n, _ in violations})
        print("CONTRACT_RESULT: FAIL (%s)" % ", ".join("C%d" % n for n in codes))
        return 2
    print("CONTRACT_RESULT: PASS")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="contract_check.py",
                                 description="skill-contract v1 reference checker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check-skill", help="commandments 1-2 for one skill directory")
    p.add_argument("skill_dir")
    p = sub.add_parser("check-envelope", help="commandments 3-7 and 9 for one envelope")
    p.add_argument("file")
    p.add_argument("--root", help="repo root: check digests (C5) and grade claims (C7)")
    p.add_argument("--for", dest="for_skill", help="receiving skill dir: it must consume the kind (C9)")
    p.add_argument("--rerun", action="store_true",
                   help="re-run each command (only after the user approved it; needs --root)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("discover", help="commandment 8: installed consumers of a kind")
    p.add_argument("--kind", required=True)
    p.add_argument("--from", dest="from_dir")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("check-grant", help="commandment 10: does a grant cover this action")
    p.add_argument("file", nargs="?")
    p.add_argument("--root", required=True)
    p.add_argument("--action", required=True)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("revoke-grant", help="commandment 10: revoke the newest (or named) grant")
    p.add_argument("id", nargs="?")
    p.add_argument("--root", required=True)
    return ap


def main(argv=None):
    try:
        a = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code else 0
    if a.cmd == "check-skill":
        rep = check_skill(a.skill_dir)
        lines = ["ADOPTER: %s" % ("yes" if rep["adopter"] else "no")]
        lines += ["WARN: %s" % w for w in rep["warnings"]]
        return finish(rep["violations"], lines)
    if a.cmd == "check-envelope":
        if a.rerun and not a.root:
            print("usage: --rerun needs --root", file=sys.stderr)
            return 1
        rep = check_envelope(a.file, root=a.root, for_skill=a.for_skill, rerun=a.rerun)
        if a.json:
            print(json.dumps(dict(rep, violations=["C%d: %s" % v for v in rep["violations"]]),
                             sort_keys=True))
            return finish(rep["violations"])
        lines = []
        if not rep["violations"]:
            lines.append("ENVELOPE: %s" % ("UNCHECKED" if a.root is None
                                           else "STALE" if rep["stale"] else "FRESH"))
            lines += ["STALE: %s" % s for s in rep["stale"]]
            lines += ["CLAIM: %s %s" % (t, s) for t, s in sorted(rep["claims"].items())]
        return finish(rep["violations"], lines)
    if a.cmd == "discover":
        if kind_parts(a.kind) is None:
            return finish([(2, "%r is not a kind URI (https, ending in /v<N>)" % a.kind)])
        out = discover(a.kind, from_dir=a.from_dir)
        if a.json:
            print(json.dumps(out, sort_keys=True))
            return finish([])
        lines = ["CONSUMER: %s (%s) %s" % (c["skill"], c["root"], c["dir"]) for c in out["consumers"]]
        lines += ["SHADOWED: %s (%s) %s" % (s["skill"], s["root"], s["dir"]) for s in out["shadowed"]]
        lines += ["INVALID: %s %s" % (i["skill"], "; ".join(i["violations"])) for i in out["invalid"]]
        lines += ["WARN: %s" % w for w in out["warnings"]]
        if not out["consumers"]:
            lines.append("NO_CONSUMER: no installed skill consumes %s; give the envelope to the user"
                         % a.kind)
        return finish([], lines)
    if a.cmd == "check-grant":
        if a.action not in ACTION_CLASSES:
            print("usage: --action must be one of %s" % ", ".join(ACTION_CLASSES), file=sys.stderr)
            return 1
        rep = check_grant(a.root, a.action, path=a.file)
        if a.json:
            print(json.dumps(rep, sort_keys=True))
        for v in rep["violations"]:
            print("FAIL: %s" % v)
        if rep["status"] == "COVERED":
            print("GRANT: COVERED id=%s class=%s gate=%s" % (rep["id"], a.action, rep["gate"]))
            return 0
        if rep["status"] == "INVALID":
            print("GRANT: INVALID %s" % (rep["path"],))
            return 2
        if rep["status"] == "NONE":
            print("GRANT: NONE")
            return 3
        print("GRANT: ASK id=%s reason=%s" % (rep["id"], rep["reason"]))
        return 3
    if a.cmd == "revoke-grant":
        try:
            print("REVOKED: %s" % revoke_grant(a.root, a.id))
            return 0
        except (ValueError, OSError) as exc:
            print("ERROR: %s" % exc, file=sys.stderr)
            return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
