#!/usr/bin/env python3
"""Stdlib unit tests for audit_posture.py (offline, deterministic).

Run standalone:  cd security-posture-audit/assets && python3 test_audit_posture.py
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_posture  # noqa: E402


class TreeCase(unittest.TestCase):
    """Base: each test builds its fixture tree in a private tempdir."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="spa-test-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        # every fixture gets a SECURITY.md so missing-security-md stays out of
        # the way unless a test removes it deliberately
        self.write("SECURITY.md", "Report issues privately.\n")

    def write(self, rel, content):
        path = os.path.join(self.repo, rel)
        if os.path.dirname(rel):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    def findings(self, **kw):
        return audit_posture.audit(self.repo, **kw)

    def by_check(self, check, **kw):
        return [f for f in self.findings(**kw) if f["check"] == check]


class TestRequirements(TreeCase):
    def test_unpinned_flagged_pinned_not(self):
        self.write("requirements.txt",
                   "# comment\n"
                   "flask==2.3.2\n"
                   "requests>=2.0\n"
                   "pyyaml\n"
                   "-r other.txt\n"
                   "django==4.2 ; python_version >= '3.8'\n")
        hits = self.by_check("dep-unpinned-python")
        self.assertEqual([(f["line"], f["evidence"]) for f in hits],
                         [(3, "requests>=2.0"), (4, "pyyaml")])
        self.assertTrue(all(f["severity"] == "MEDIUM" for f in hits))

    def test_dev_requirements_variant_scanned(self):
        self.write("requirements-dev.txt", "pytest\n")
        self.assertEqual(len(self.by_check("dep-unpinned-python")), 1)


class TestPackageJson(TreeCase):
    def test_ranges_without_lockfile_flagged(self):
        self.write("package.json", json.dumps({
            "dependencies": {"left-pad": "^1.3.0", "express": "4.18.2"},
            "devDependencies": {"jest": "~29.0.0"},
        }, indent=2))
        hits = self.by_check("dep-unpinned-node")
        names = sorted(f["evidence"].split(":")[0] for f in hits)
        self.assertEqual(names, ["jest", "left-pad"])
        self.assertTrue(all(f["line"] > 0 for f in hits))

    def test_lockfile_silences_ranges(self):
        self.write("package.json", json.dumps({"dependencies": {"left-pad": "^1.3.0"}}))
        self.write("package-lock.json", "{}")
        self.assertEqual(self.by_check("dep-unpinned-node"), [])

    def test_exact_versions_without_lockfile_silent(self):
        self.write("package.json", json.dumps({"dependencies": {"express": "4.18.2"}}))
        self.assertEqual(self.by_check("dep-unpinned-node"), [])

    def test_malformed_manifest_is_graceful_parse_error(self):
        self.write("package.json", "{ this is not json,")
        hits = self.by_check("parse-error")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["severity"], "LOW")
        self.assertIn("could not be parsed", hits[0]["evidence"])


class TestDockerfile(TreeCase):
    def test_latest_and_untagged_flagged(self):
        self.write("Dockerfile",
                   "FROM node:latest\n"
                   "FROM ubuntu\n"
                   "FROM python:3.11.9-slim AS build\n"
                   "FROM build\n"
                   "FROM scratch\n")
        hits = self.by_check("docker-unpinned-base")
        self.assertEqual([f["line"] for f in hits], [1, 2])

    def test_digest_pinned_silent(self):
        self.write("Dockerfile", "FROM python:3.11.9-slim\n")
        self.assertEqual(self.by_check("docker-unpinned-base"), [])


class TestCredentialFiles(TreeCase):
    def test_env_pem_and_keys_flagged(self):
        self.write(".env", "APP_MODE=beta\n")
        self.write("certs/server.pem", "placeholder\n")
        self.write("deploy/id_rsa", "placeholder\n")
        hits = self.by_check("credential-file")
        self.assertEqual(sorted(f["path"] for f in hits),
                         [".env", "certs/server.pem", "deploy/id_rsa"])
        self.assertTrue(all(f["severity"] == "HIGH" for f in hits))

    def test_env_example_allowlisted(self):
        self.write(".env.example", "APP_MODE=\n")
        self.write(".env.template", "APP_MODE=\n")
        self.assertEqual(self.by_check("credential-file"), [])

    def test_skip_dirs_not_scanned(self):
        self.write(".git/.env", "APP_MODE=beta\n")
        self.write("node_modules/pkg/.env", "APP_MODE=beta\n")
        self.assertEqual(self.by_check("credential-file"), [])

    def test_extra_skip_dir_honored(self):
        self.write("generated/.env", "APP_MODE=beta\n")
        self.assertEqual(len(self.by_check("credential-file")), 1)
        self.assertEqual(self.by_check("credential-file", extra_skip=["generated"]), [])


class TestDebugAndCors(TreeCase):
    def test_debug_true_and_debug_run_flagged(self):
        self.write("settings.py", "DEBUG = True\nTEMPLATE_DEBUG = False\n")
        self.write("app.py", "app.run(host='0.0.0.0', debug=True)\n")
        hits = self.by_check("debug-flag")
        self.assertEqual(sorted(f["path"] for f in hits), ["app.py", "settings.py"])

    def test_debug_false_silent(self):
        self.write("settings.py", "DEBUG = False\n")
        self.assertEqual(self.by_check("debug-flag"), [])

    def test_cors_wildcards_flagged(self):
        self.write("api.cfg", "Access-Control-Allow-Origin: *\n")
        self.write("main.py", 'app.add_middleware(CORSMiddleware, allow_origins=["*"])\n')
        hits = self.by_check("permissive-cors")
        self.assertEqual(sorted(f["path"] for f in hits), ["api.cfg", "main.py"])

    def test_explicit_origin_silent(self):
        self.write("api.cfg", "Access-Control-Allow-Origin: https://app.example.com\n")
        self.assertEqual(self.by_check("permissive-cors"), [])


class TestInsecureTransport(TreeCase):
    def test_http_url_in_config_flagged(self):
        self.write("pip.conf", "[global]\nindex-url = http://pypi.internal.example/simple\n")
        hits = self.by_check("insecure-transport")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["line"], 2)

    def test_https_and_localhost_silent(self):
        self.write("pip.conf", "[global]\nindex-url = https://pypi.org/simple\n")
        self.write("dev.cfg", "url = http://localhost:8000/api\nalt = http://127.0.0.1:9\n")
        self.assertEqual(self.by_check("insecure-transport"), [])

    def test_trusted_host_flagged(self):
        self.write("requirements.txt", "--trusted-host pypi.internal.example\nflask==2.3.2\n")
        hits = self.by_check("insecure-transport")
        self.assertEqual(len(hits), 1)
        self.assertIn("trusted-host", hits[0]["evidence"])

    def test_http_in_source_code_not_flagged(self):
        # scope is dependency/config files, not arbitrary source
        self.write("main.py", "URL = 'http://example.com/callback'\n")
        self.assertEqual(self.by_check("insecure-transport"), [])


class TestWorkflows(TreeCase):
    def test_prt_with_head_checkout_flagged(self):
        self.write(".github/workflows/build.yml",
                   "on: pull_request_target\n"
                   "jobs:\n  build:\n    steps:\n"
                   "      - uses: actions/checkout@v4\n"
                   "        with:\n"
                   "          ref: ${{ github.event.pull_request.head.sha }}\n")
        hits = self.by_check("workflow-prt-checkout")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["severity"], "HIGH")

    def test_plain_pull_request_silent(self):
        self.write(".github/workflows/ci.yml",
                   "on: [push, pull_request]\n"
                   "jobs:\n  test:\n    steps:\n"
                   "      - uses: actions/checkout@v4\n")
        self.assertEqual(self.by_check("workflow-prt-checkout"), [])

    def test_curl_pipe_sh_flagged(self):
        self.write(".github/workflows/setup.yml",
                   "      - run: curl -sSL https://get.example.com/install | bash\n")
        hits = self.by_check("workflow-curl-pipe-sh")
        self.assertEqual(len(hits), 1)

    def test_curl_to_file_silent(self):
        self.write(".github/workflows/setup.yml",
                   "      - run: curl -sSL -o install.sh https://get.example.com/install\n")
        self.assertEqual(self.by_check("workflow-curl-pipe-sh"), [])


@unittest.skipUnless(os.name == "posix", "file-mode checks are POSIX-only")
class TestModes(TreeCase):
    def test_world_writable_flagged(self):
        p = self.write("notes.txt", "n\n")
        os.chmod(p, 0o666)
        hits = self.by_check("world-writable")
        self.assertEqual([f["path"] for f in hits], ["notes.txt"])

    def test_setuid_flagged(self):
        p = self.write("tool.bin", "#!/bin/sh\n")
        os.chmod(p, 0o4755)
        hits = self.by_check("setuid-file")
        self.assertEqual([f["path"] for f in hits], ["tool.bin"])
        self.assertEqual(hits[0]["severity"], "HIGH")

    def test_normal_mode_silent(self):
        p = self.write("plain.txt", "p\n")
        os.chmod(p, 0o644)
        self.assertEqual(self.by_check("world-writable"), [])
        self.assertEqual(self.by_check("setuid-file"), [])


class TestSecurityMd(TreeCase):
    def test_present_is_silent(self):
        self.assertEqual(self.by_check("missing-security-md"), [])

    def test_missing_is_info(self):
        os.remove(os.path.join(self.repo, "SECURITY.md"))
        hits = self.by_check("missing-security-md")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["severity"], "INFO")

    def test_dot_github_location_counts(self):
        os.remove(os.path.join(self.repo, "SECURITY.md"))
        self.write(".github/SECURITY.md", "policy\n")
        self.assertEqual(self.by_check("missing-security-md"), [])


class TestOutputAndExitCodes(TreeCase):
    def _leak(self):
        self.write(".env", "APP_MODE=beta\n")
        self.write("requirements.txt", "pyyaml\n")

    def _run(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = audit_posture.main(list(argv))
        return rc, buf.getvalue()

    def test_deterministic_across_runs(self):
        self._leak()
        rc1, out1 = self._run(self.repo, "--format", "json", "--fail-on", "never")
        rc2, out2 = self._run(self.repo, "--format", "json", "--fail-on", "never")
        self.assertEqual((rc1, out1), (rc2, out2))

    def test_json_shape(self):
        self._leak()
        _, out = self._run(self.repo, "--format", "json", "--fail-on", "never")
        doc = json.loads(out)
        self.assertEqual(sorted(doc), ["findings", "not_covered", "repo", "summary"])
        for f in doc["findings"]:
            self.assertEqual(sorted(f), ["check", "evidence", "line", "path", "remediation", "severity"])
        self.assertEqual(doc["summary"]["HIGH"], 1)

    def test_findings_sorted(self):
        self._leak()
        f = self.findings()
        keys = [(x["path"], x["line"], x["check"]) for x in f]
        self.assertEqual(keys, sorted(keys))

    def test_fail_on_thresholds(self):
        self._leak()  # HIGH (.env) + MEDIUM (pyyaml)
        self.assertEqual(self._run(self.repo, "--fail-on", "high")[0], 1)
        self.assertEqual(self._run(self.repo, "--fail-on", "never")[0], 0)

    def test_clean_repo_exits_zero(self):
        self.assertEqual(self._run(self.repo, "--fail-on", "info")[0], 0)

    def test_medium_only_passes_fail_on_high(self):
        self.write("requirements.txt", "pyyaml\n")
        self.assertEqual(self._run(self.repo, "--fail-on", "high")[0], 0)
        self.assertEqual(self._run(self.repo, "--fail-on", "medium")[0], 1)

    def test_text_format_lists_not_covered(self):
        self._leak()
        _, out = self._run(self.repo, "--fail-on", "never")
        self.assertIn("NOT COVERED", out)
        self.assertIn("CVE", out)

    def test_missing_repo_is_usage_error(self):
        rc, _ = self._run(os.path.join(self.repo, "no-such-dir"))
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
