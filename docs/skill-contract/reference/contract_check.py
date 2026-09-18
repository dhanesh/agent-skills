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
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402

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
    return 1


if __name__ == "__main__":
    sys.exit(main())
