#!/usr/bin/env python3
"""contract_check.py: reference checker for skill-contract v1.

The creed is docs/skill-contract/SPEC.md in github.com/dhanesh/agent-skills.
Stdlib only, Python >= 3.10, offline. Adopting skills vendor this file
byte-identical into their assets/ (`make contract-vendor`).

    contract_check.py check-skill <skill-dir>                                  C1, C2
    contract_check.py check-envelope <file> [--root DIR] [--for SKILL_DIR]
                                            [--rerun] [--json]                 C3-C7, C9
    contract_check.py discover --kind URI [--from SKILL_DIR] [--json]          C8

Exit 0 pass, 2 a commandment is violated, 1 usage or internal error. The last
line is always CONTRACT_RESULT: PASS or CONTRACT_RESULT: FAIL (C<n>, ...).
"""
import sys

if sys.version_info < (3, 10):
    sys.stderr.write("contract_check.py needs Python >= 3.10, found %d.%d\n"
                     % sys.version_info[:2])
    sys.exit(2)

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import secrets  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
CONTRACT_VERSION = "1"
FENCE_INFO = "json skill-contract"
KIND_RE = re.compile(
    r"^https://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._~-]+)*"
    r"/(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*)/v(?P<ver>[1-9][0-9]*)$")
ID_RE = re.compile(
    r"^(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*)-v(?P<ver>[1-9][0-9]*)"
    r"-(?P<ts>[0-9]{8}T[0-9]{6}Z)-(?P<hex>[0-9a-f]{6})$")
TIME_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_FRONTMATTER = ("name", "description", "license", "compatibility",
                       "metadata", "allowed-tools")
OUTCOMES = ("passed", "failed", "cantTell", "inapplicable", "untested")
SKILL_DIR_RE = re.compile(r"^\{skill_dir:(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*)\}")
PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}")
BARE_PYTHON_RE = re.compile(r"^(?:python[0-9.]*|py)(?:\.exe)?$", re.IGNORECASE)


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
    except OSError as exc:
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
    d = envelope_dir(root)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, statement["predicate"]["id"] + ".json")
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
        with open(path, encoding="utf-8") as f:
            return json.load(f), []
    except (OSError, ValueError) as exc:
        return None, [(3, "cannot read the envelope: %s" % exc)]


def check_envelope(path):
    """Structural check of one envelope file. Task 5 adds staleness, claims and --for."""
    report = {"envelope": path, "violations": [], "stale": [], "claims": {}}
    st, report["violations"] = load_envelope(path)
    if st is not None:
        report["violations"] = check_statement(st)
    return report


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
    p = sub.add_parser("check-envelope", help="commandments 3-6 for one envelope")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
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
        rep = check_envelope(a.file)
        if a.json:
            print(json.dumps(dict(rep, violations=["C%d: %s" % v for v in rep["violations"]]),
                             sort_keys=True))
        return finish(rep["violations"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
