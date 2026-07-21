#!/usr/bin/env python3
"""audit_posture.py — deterministic, read-only security-posture audit of a repo tree.

Walks a repository (skipping .git/node_modules/vendor and friends) and emits
posture findings a checklist can prove from the tree alone: unpinned
dependencies, committed credential-shaped files, debug/permissive flags,
insecure transports, risky GitHub Actions patterns, dangerous file modes, and
a missing security policy. Offline, stdlib-only, deterministic: same tree in,
same findings out, sorted stably.

Each finding: {check, severity, path, line, evidence, remediation}.
line == 0 means the finding is about the file (or repo) as a whole.

Usage:
    python3 audit_posture.py <repo> [--format text|json] [--fail-on high|medium|low|info|never]
                             [--skip-dir NAME]...

Exit codes: 0 clean (below the --fail-on threshold), 1 findings at/above the
threshold, 2 usage error. Draft severities are a starting point — a human or
agent adjudicates them in context (see the skill's SKILL.md workflow).
"""

import argparse
import json
import os
import re
import stat
import sys

SEVERITIES = ("HIGH", "MEDIUM", "LOW", "INFO")
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}  # 0 = most severe

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "vendored", "third_party",
    ".venv", "venv", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache",
    "dist", "build", ".next", "target",
}

MAX_FILE_BYTES = 1_000_000  # content checks skip larger files (modes/names still checked)

NOT_COVERED = [
    "Known CVEs / vulnerable dependency versions (no advisory database, no network)",
    "SAST dataflow analysis (injection, deserialization, authz logic)",
    "Secret VALUES inside file contents (this audit flags credential-shaped FILES by name only; use a dedicated secrets scanner for content)",
    "Runtime behavior, infrastructure, and cloud configuration",
    "Norms/claims verification against standards bodies (use base-in-reality)",
]

# ── file classifiers ─────────────────────────────────────────────────────────


def _is_requirements(name):
    return name.startswith("requirements") and name.endswith(".txt")


def _is_dockerfile(name):
    return name == "Dockerfile" or name.startswith("Dockerfile.") or name.endswith(".dockerfile")


def _is_workflow(rel):
    return rel.startswith(".github/workflows/") and rel.endswith((".yml", ".yaml"))


_ENV_ALLOWED_SUFFIXES = ("example", "sample", "template", "dist")
_KEYFILE_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}

_CONFIGISH_NAMES = {"package.json", "pip.conf", ".npmrc", "setup.cfg", "Pipfile"}
_CONFIGISH_EXTS = (".toml", ".cfg", ".ini", ".yml", ".yaml")


def _is_configish(name):
    return (
        _is_requirements(name)
        or _is_dockerfile(name)
        or name in _CONFIGISH_NAMES
        or name.endswith(_CONFIGISH_EXTS)
    )


_CODEISH_EXTS = (
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".rb", ".php", ".go",
    ".json", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".conf",
)

# ── per-check patterns ───────────────────────────────────────────────────────

_DEBUG_ASSIGN = re.compile(r"^\s*DEBUG\s*=\s*True\b")
_DEBUG_RUN = re.compile(r"\.run\([^)]*debug\s*=\s*True", re.I)

_CORS_PATTERNS = (
    re.compile(r"Access-Control-Allow-Origin.{0,40}?\*", re.I),
    re.compile(r"CORS_ALLOW_ALL_ORIGINS\s*[=:]\s*True", re.I),
    re.compile(r"allow_origins\s*[=:]\s*[\[\(]?\s*[\"']\*[\"']", re.I),
    re.compile(r"\borigin\s*:\s*[\"']\*[\"']", re.I),
    re.compile(r"CORS_ORIGINS?\s*[=:]\s*[\"']?\*", re.I),
)

_HTTP_URL = re.compile(r"http://(?!localhost\b|127\.|0\.0\.0\.0|\[?::1)[^\s\"'<>)\]]+")
_TRUSTED_HOST = re.compile(r"trusted-host", re.I)

_PRT_HEAD_REF = re.compile(r"github\.event\.pull_request\.head\.(sha|ref)")
_CURL_PIPE_SH = re.compile(r"\b(curl|wget)\b[^|\n]*\|[^|\n]*\b(sh|bash|zsh|dash|ksh)\b")

_NODE_SKIP_PREFIXES = ("file:", "link:", "workspace:", "portal:")


def _node_spec_is_range(spec):
    """True when a package.json version spec is a range/floating spec."""
    s = str(spec).strip()
    if not s or s == "*" or s.lower() == "latest":
        return True
    if s.startswith(_NODE_SKIP_PREFIXES):
        return False  # local refs: pinning is not a version question
    if any(c in s for c in "^~*<>|"):
        return True
    if re.search(r"(^|\.)x(\.|$)", s, re.I):
        return True
    return False


def _finding(check, severity, path, line, evidence, remediation):
    return {
        "check": check,
        "severity": severity,
        "path": path,
        "line": line,
        "evidence": evidence,
        "remediation": remediation,
    }


# ── individual checks (each takes what it needs, returns findings) ───────────


def _check_modes(rel, abspath):
    out = []
    if os.name != "posix":
        return out
    st = os.lstat(abspath)
    if not stat.S_ISREG(st.st_mode):
        return out
    mode = stat.S_IMODE(st.st_mode)
    if st.st_mode & (stat.S_ISUID | stat.S_ISGID):
        bit = "setuid" if st.st_mode & stat.S_ISUID else "setgid"
        out.append(_finding(
            "setuid-file", "HIGH", rel, 0,
            "%s bit set (mode %s)" % (bit, oct(mode)),
            "Remove the %s bit: chmod u-s,g-s %s — repos should not ship privilege-escalating binaries." % (bit, rel),
        ))
    if mode & 0o002:
        out.append(_finding(
            "world-writable", "MEDIUM", rel, 0,
            "world-writable (mode %s)" % oct(mode),
            "chmod o-w %s — any local user/process can tamper with this file." % rel,
        ))
    return out


def _check_credential_file(rel, name):
    hit = None
    if name == ".env" or name.startswith(".env."):
        if not name.rsplit(".", 1)[-1].lower() in _ENV_ALLOWED_SUFFIXES:
            hit = "dotenv file committed"
    elif name.endswith(".pem"):
        hit = ".pem file committed"
    elif name in _KEYFILE_NAMES:
        hit = "SSH private-key-named file committed"
    if not hit:
        return []
    return [_finding(
        "credential-file", "HIGH", rel, 0,
        "%s (presence only; content not read)" % hit,
        "Remove it from version control (git rm --cached), rotate anything it held, and add the pattern to .gitignore. Ship a *.example placeholder instead.",
    )]


def _check_requirements(rel, lines):
    out = []
    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        spec = line.split("#", 1)[0].split(";", 1)[0].strip()
        if not spec or "==" in spec:
            continue
        out.append(_finding(
            "dep-unpinned-python", "MEDIUM", rel, i, spec,
            "Pin to an exact version (pkg==X.Y.Z), ideally with hashes via pip-compile, so installs are reproducible and not silently upgradable.",
        ))
    return out


def _check_package_json(rel, name, text, relset):
    out = []
    pkg_dir = rel.rsplit("/", 1)[0] + "/" if "/" in rel else ""
    lockfiles = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb")
    has_lock = any((pkg_dir + lf) in relset for lf in lockfiles)
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        return [_finding(
            "parse-error", "LOW", rel, 0,
            "package.json could not be parsed: %s" % str(exc)[:120],
            "Fix the JSON syntax so the manifest can be audited (and installed) at all.",
        )]
    if has_lock or not isinstance(data, dict):
        return out
    text_lines = text.splitlines()
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        deps = data.get(section)
        if not isinstance(deps, dict):
            continue
        for dep in sorted(deps):
            spec = deps[dep]
            if not isinstance(spec, str) or not _node_spec_is_range(spec):
                continue
            lineno = 0
            needle = '"%s"' % dep
            for j, tl in enumerate(text_lines, 1):
                if needle in tl:
                    lineno = j
                    break
            out.append(_finding(
                "dep-unpinned-node", "MEDIUM", rel, lineno,
                "%s: %s (%s; no lockfile alongside)" % (dep, spec, section),
                "Commit a lockfile (package-lock.json / yarn.lock / pnpm-lock.yaml) or pin exact versions so installs are reproducible.",
            ))
    return out


def _check_dockerfile(rel, lines):
    out = []
    aliases = set()
    for i, raw in enumerate(lines, 1):
        m = re.match(r"\s*FROM\s+(\S+)(?:\s+AS\s+(\S+))?", raw, re.I)
        if not m:
            continue
        image, alias = m.group(1), m.group(2)
        image_l = image.lower()
        if image_l in aliases or image_l == "scratch" or image.startswith("$"):
            if alias:
                aliases.add(alias.lower())
            continue
        if alias:
            aliases.add(alias.lower())
        if "@sha256" in image:
            continue  # digest-pinned
        tail = image.rsplit("/", 1)[-1]
        if ":" not in tail:
            out.append(_finding(
                "docker-unpinned-base", "MEDIUM", rel, i,
                "FROM %s (no tag; defaults to :latest)" % image,
                "Pin the base image to a specific tag, ideally with a digest (image:TAG@sha256-digest), so builds are reproducible.",
            ))
        elif tail.rsplit(":", 1)[1].lower() == "latest":
            out.append(_finding(
                "docker-unpinned-base", "MEDIUM", rel, i,
                "FROM %s (:latest tag)" % image,
                "Pin the base image to a specific tag, ideally with a digest, so builds are reproducible.",
            ))
    return out


def _check_debug_flags(rel, lines):
    out = []
    for i, raw in enumerate(lines, 1):
        if _DEBUG_ASSIGN.search(raw) or _DEBUG_RUN.search(raw):
            out.append(_finding(
                "debug-flag", "MEDIUM", rel, i, raw.strip()[:160],
                "Drive debug mode from the environment (default off) instead of hardcoding True; debug servers leak stack traces and often allow code execution.",
            ))
    return out


def _check_cors(rel, lines):
    out = []
    for i, raw in enumerate(lines, 1):
        for pat in _CORS_PATTERNS:
            if pat.search(raw):
                out.append(_finding(
                    "permissive-cors", "MEDIUM", rel, i, raw.strip()[:160],
                    "Replace the wildcard with an explicit origin allowlist; a * origin plus credentials or private endpoints exposes them to any website.",
                ))
                break
    return out


def _check_insecure_transport(rel, name, lines):
    out = []
    pip_context = _is_requirements(name) or name in ("pip.conf", "setup.cfg", "Pipfile") or name.endswith((".cfg", ".ini", ".toml"))
    for i, raw in enumerate(lines, 1):
        m = _HTTP_URL.search(raw)
        if m:
            out.append(_finding(
                "insecure-transport", "MEDIUM", rel, i,
                m.group(0)[:160],
                "Use https:// — plain-http dependency/config endpoints allow on-path tampering with what you install or trust.",
            ))
        if pip_context and _TRUSTED_HOST.search(raw):
            out.append(_finding(
                "insecure-transport", "MEDIUM", rel, i,
                raw.strip()[:160],
                "Remove --trusted-host; it disables TLS verification for that index. Serve the index over verified https instead.",
            ))
    return out


def _check_workflow(rel, text, lines):
    out = []
    if "pull_request_target" in text:
        for i, raw in enumerate(lines, 1):
            if _PRT_HEAD_REF.search(raw):
                out.append(_finding(
                    "workflow-prt-checkout", "HIGH", rel, i, raw.strip()[:160],
                    "pull_request_target runs with repo secrets; checking out the PR head lets a fork execute code with them. Split the workflow or drop the privileged trigger (see GitHub's 'pwn request' guidance).",
                ))
    for i, raw in enumerate(lines, 1):
        if _CURL_PIPE_SH.search(raw):
            out.append(_finding(
                "workflow-curl-pipe-sh", "HIGH", rel, i, raw.strip()[:160],
                "Do not pipe a downloaded script straight into a shell in CI; download to a file, verify a checksum/signature, then execute.",
            ))
    return out


# ── driver ───────────────────────────────────────────────────────────────────


def audit(repo, extra_skip=(), max_bytes=MAX_FILE_BYTES):
    """Run every check over the tree at `repo`; return the sorted findings list."""
    repo = os.path.abspath(repo)
    skip = set(SKIP_DIRS) | {s.strip("/") for s in extra_skip if s and s.strip("/")}
    files = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for fn in sorted(filenames):
            ab = os.path.join(dirpath, fn)
            if os.path.islink(ab):
                continue
            rel = os.path.relpath(ab, repo).replace(os.sep, "/")
            files.append((rel, ab, fn))

    relset = {rel for rel, _, _ in files}
    findings = []

    for rel, ab, fn in files:
        findings.extend(_check_modes(rel, ab))
        findings.extend(_check_credential_file(rel, fn))

        try:
            if os.path.getsize(ab) > max_bytes:
                continue
            with open(ab, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        lines = text.splitlines()

        if _is_requirements(fn):
            findings.extend(_check_requirements(rel, lines))
        if fn == "package.json":
            findings.extend(_check_package_json(rel, fn, text, relset))
        if _is_dockerfile(fn):
            findings.extend(_check_dockerfile(rel, lines))
        if fn.endswith(".py"):
            findings.extend(_check_debug_flags(rel, lines))
        if fn.endswith(_CODEISH_EXTS):
            findings.extend(_check_cors(rel, lines))
        if _is_configish(fn):
            findings.extend(_check_insecure_transport(rel, fn, lines))
        if _is_workflow(rel):
            findings.extend(_check_workflow(rel, text, lines))

    lowered = {r.lower() for r in relset}
    if not any(c in lowered for c in ("security.md", ".github/security.md", "docs/security.md")):
        findings.append(_finding(
            "missing-security-md", "INFO", ".", 0,
            "no SECURITY.md at repo root, .github/, or docs/",
            "Add a SECURITY.md describing how to report vulnerabilities and which versions receive fixes.",
        ))

    findings.sort(key=lambda f: (f["path"], f["line"], f["check"], f["evidence"]))
    return findings


def summarize(findings):
    counts = {s: 0 for s in SEVERITIES}
    for f in findings:
        counts[f["severity"]] += 1
    return counts


def render_text(repo, findings):
    counts = summarize(findings)
    out = []
    out.append("Security posture audit: %s" % repo)
    out.append("Findings: %d HIGH, %d MEDIUM, %d LOW, %d INFO"
               % (counts["HIGH"], counts["MEDIUM"], counts["LOW"], counts["INFO"]))
    out.append("")
    if not findings:
        out.append("No posture findings.")
    for f in findings:
        loc = f["path"] if f["line"] == 0 else "%s:%d" % (f["path"], f["line"])
        out.append("[%s] %s %s — %s" % (f["severity"], f["check"], loc, f["evidence"]))
        out.append("    fix: %s" % f["remediation"])
    out.append("")
    out.append("NOT COVERED by this audit:")
    for item in NOT_COVERED:
        out.append("  - %s" % item)
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Deterministic security-posture audit of a repo tree.")
    parser.add_argument("repo", help="path to the repository root to audit")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--fail-on", choices=("high", "medium", "low", "info", "never"),
                        default="high", dest="fail_on",
                        help="lowest severity that makes the exit code 1 (default: high)")
    parser.add_argument("--skip-dir", action="append", default=[],
                        help="extra directory name to skip (repeatable), e.g. a vendored tree")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code not in (0, None) else 0

    if not os.path.isdir(args.repo):
        print("error: not a directory: %s" % args.repo, file=sys.stderr)
        return 2

    findings = audit(args.repo, extra_skip=args.skip_dir)

    if args.format == "json":
        doc = {
            "repo": args.repo,
            "summary": summarize(findings),
            "findings": findings,
            "not_covered": NOT_COVERED,
        }
        print(json.dumps(doc, indent=2, sort_keys=True))
    else:
        print(render_text(args.repo, findings))

    if args.fail_on == "never":
        return 0
    threshold = {"high": 0, "medium": 1, "low": 2, "info": 3}[args.fail_on]
    if any(SEV_RANK[f["severity"]] <= threshold for f in findings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
