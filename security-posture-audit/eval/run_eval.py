#!/usr/bin/env python3
"""Gate-runnable outcome eval for security-posture-audit (docs/eval-standard.md).

Builds two synthetic repos in a tempdir — a HARDENED one (pinned deps,
lockfile, clean workflows, SECURITY.md) and a LEAKY one exhibiting every
defect class the audit promises to catch — then runs the shipped
assets/audit_posture.py against both and grades the outcomes with
model-free checks. Negative fixtures are first-class: the hardened repo
must stay silent per-check (a false positive is a failure), and a
malformed package.json must yield a graceful parse-error entry, not a
crash. Exit-code behavior of --fail-on is verified both ways.
Offline, deterministic, no repo writes.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIT = os.path.join(os.path.dirname(HERE), "assets", "audit_posture.py")

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = " (%s)" % detail if detail else ""
    print("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL", suffix))


def write(root, rel, content, mode=None):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    if mode is not None:
        os.chmod(path, mode)


def run_audit(repo, *extra):
    cmd = [sys.executable, AUDIT, repo, "--format", "json"] + list(extra)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def build_hardened(root):
    write(root, "SECURITY.md", "# Security policy\nReport privately to the maintainer.\n")
    write(root, "requirements.txt", "flask==2.3.2\nrequests==2.31.0\n")
    write(root, "package.json", json.dumps(
        {"name": "hardened", "dependencies": {"express": "4.18.2"}}, indent=2))
    write(root, "package-lock.json", json.dumps({"name": "hardened", "lockfileVersion": 3}))
    write(root, "Dockerfile", "FROM python:3.11.9-slim\nCOPY . /app\n")
    write(root, ".env.example", "APP_MODE=\n")
    write(root, "settings.py", "DEBUG = False\nALLOWED_HOSTS = ['app.example.com']\n")
    write(root, "pip.conf", "[global]\nindex-url = https://pypi.org/simple\n")
    write(root, ".github/workflows/ci.yml",
          "on: [push, pull_request]\n"
          "jobs:\n"
          "  test:\n"
          "    runs-on: ubuntu-latest\n"
          "    steps:\n"
          "      - uses: actions/checkout@v4\n"
          "      - run: make test\n")


def build_leaky(root):
    # no SECURITY.md → missing-security-md (INFO)
    write(root, "requirements.txt",
          "flask\n"
          "requests>=2.0\n"
          "--trusted-host pypi.internal.example\n")
    write(root, "package.json", json.dumps(
        {"name": "leaky", "dependencies": {"left-pad": "^1.3.0"}}, indent=2))  # no lockfile
    write(root, "svc/package.json", "{ definitely not json,")  # malformed → parse-error
    write(root, "Dockerfile", "FROM node:latest\nCOPY . /app\n")
    write(root, ".env", "APP_MODE=beta\n")
    write(root, "certs/server.pem", "placeholder — presence is the finding\n")
    write(root, "deploy/id_rsa", "placeholder — presence is the finding\n")
    write(root, "app.py",
          "DEBUG = True\n"
          "app.run(host='0.0.0.0', debug=True)\n")
    write(root, "api.cfg", "Access-Control-Allow-Origin: *\n")
    write(root, "pip.conf", "[global]\nindex-url = http://pypi.internal.example/simple\n")
    write(root, ".github/workflows/build.yml",
          "on: pull_request_target\n"
          "jobs:\n"
          "  build:\n"
          "    runs-on: ubuntu-latest\n"
          "    steps:\n"
          "      - uses: actions/checkout@v4\n"
          "        with:\n"
          "          ref: ${{ github.event.pull_request.head.sha }}\n"
          "      - run: curl -sSL https://get.example.com/install | bash\n")
    write(root, "notes.txt", "world writable\n", mode=0o666)
    write(root, "tool.bin", "#!/bin/sh\n", mode=0o4755)
    # decoy inside a skipped dir: must NOT surface
    write(root, "node_modules/pkg/.env", "APP_MODE=beta\n")


def main():
    tmp = tempfile.mkdtemp(prefix="spa-eval-")
    try:
        hardened = os.path.join(tmp, "hardened")
        leaky = os.path.join(tmp, "leaky")
        os.makedirs(hardened)
        os.makedirs(leaky)
        build_hardened(hardened)
        build_leaky(leaky)

        # ── hardened repo: the audit must stay quiet ────────────────────────
        r = run_audit(hardened, "--fail-on", "high")
        ok_json = r.returncode in (0, 1)
        try:
            hdoc = json.loads(r.stdout)
        except ValueError:
            hdoc, ok_json = {"findings": [], "summary": {}}, False
        check("hardened: audit runs and emits JSON", ok_json,
              "rc=%d" % r.returncode)
        hsum = hdoc.get("summary", {})
        check("hardened: zero HIGH/MEDIUM findings",
              hsum.get("HIGH", -1) == 0 and hsum.get("MEDIUM", -1) == 0,
              "summary=%s" % json.dumps(hsum, sort_keys=True))
        hchecks = {f["check"] for f in hdoc.get("findings", [])}
        # negative fixtures: specific checks must be SILENT on the hardened repo
        for silent in ("dep-unpinned-python", "dep-unpinned-node", "docker-unpinned-base",
                       "credential-file", "debug-flag", "permissive-cors",
                       "insecure-transport", "workflow-prt-checkout",
                       "workflow-curl-pipe-sh", "missing-security-md", "parse-error"):
            check("hardened negative: no false-positive %s" % silent,
                  silent not in hchecks)
        check("hardened: --fail-on high exits 0", r.returncode == 0)

        # ── leaky repo: every defect class must be reported ─────────────────
        r = run_audit(leaky, "--fail-on", "high")
        check("leaky: audit survives malformed package.json (no crash)",
              r.returncode in (0, 1), "rc=%d stderr=%s" % (r.returncode, r.stderr.strip()[-80:]))
        try:
            ldoc = json.loads(r.stdout)
        except ValueError:
            ldoc = {"findings": []}
        got = {(f["check"], f["path"]) for f in ldoc.get("findings", [])}

        expected = [
            ("dep-unpinned-python", "requirements.txt"),
            ("dep-unpinned-node", "package.json"),
            ("parse-error", "svc/package.json"),
            ("docker-unpinned-base", "Dockerfile"),
            ("credential-file", ".env"),
            ("credential-file", "certs/server.pem"),
            ("credential-file", "deploy/id_rsa"),
            ("debug-flag", "app.py"),
            ("permissive-cors", "api.cfg"),
            ("insecure-transport", "pip.conf"),
            ("insecure-transport", "requirements.txt"),
            ("workflow-prt-checkout", ".github/workflows/build.yml"),
            ("workflow-curl-pipe-sh", ".github/workflows/build.yml"),
            ("missing-security-md", "."),
        ]
        if os.name == "posix":
            expected += [("world-writable", "notes.txt"), ("setuid-file", "tool.bin")]
        for chk, path in expected:
            check("leaky: reports %s at %s" % (chk, path), (chk, path) in got)

        check("leaky negative: skipped node_modules decoy not reported",
              ("credential-file", "node_modules/pkg/.env") not in got)

        sev = {f["check"]: f["severity"] for f in ldoc.get("findings", [])}
        check("leaky: credential-file graded HIGH", sev.get("credential-file") == "HIGH")
        check("leaky: missing-security-md graded INFO", sev.get("missing-security-md") == "INFO")
        check("leaky: parse-error is an entry, not a crash", sev.get("parse-error") == "LOW")

        # ── exit codes both ways ────────────────────────────────────────────
        check("leaky: --fail-on high exits 1", r.returncode == 1)
        r_never = run_audit(leaky, "--fail-on", "never")
        check("leaky: --fail-on never exits 0", r_never.returncode == 0)

        # ── determinism: identical output on repeat runs ────────────────────
        r2 = run_audit(leaky, "--fail-on", "never")
        check("determinism: repeat run byte-identical",
              r_never.stdout == r2.stdout and r_never.returncode == r2.returncode)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print("EVAL_RESULT: %s (%d/%d checks)" % ("PASS" if ok else "FAIL", k, n))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
