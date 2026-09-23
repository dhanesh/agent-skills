"""Offline tests for install_jev_setup.py and the jev CLI's input handling. Stdlib only."""
import contextlib
import io
import os
import runpy
import stat
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import install_jev_setup as inst  # noqa: E402

ENV = {"PATH": "/usr/bin:/bin"}


def run(home, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = inst.main(["--home", str(home), *args], env=dict(ENV))
    return code, out.getvalue()


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.paths = inst.target_paths(self.home, ENV)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_install_writes_all_targets_cli_and_placeholder(self):
        code, _ = run(self.home)
        self.assertEqual(code, 0)
        for p in self.paths.values():
            self.assertIn(inst.BEGIN, p.read_text())
        cli = self.home / ".local/bin/jev"
        self.assertEqual(cli.read_bytes(), inst.CLI_SRC.read_bytes())
        self.assertTrue(cli.stat().st_mode & stat.S_IXUSR)
        env = self.home / ".config/typesafe/env"
        self.assertEqual(stat.S_IMODE(env.stat().st_mode), 0o600)
        self.assertIn("replace-me", env.read_text())

    def test_idempotent_and_check_clean(self):
        run(self.home)
        before = {n: p.read_text() for n, p in self.paths.items()}
        code, out = run(self.home)
        self.assertEqual(code, 0)
        self.assertNotIn("WROTE", out.split("key")[0].replace("placeholder", ""))
        self.assertEqual(before, {n: p.read_text() for n, p in self.paths.items()})
        self.assertEqual(run(self.home, "--check")[0], 0)

    def test_preserves_foreign_content_and_backs_up_once(self):
        codex = self.paths["codex"]
        codex.parent.mkdir(parents=True)
        codex.write_text("# Manifold Schema\n\nkeep me\n")
        run(self.home)
        text = codex.read_text()
        self.assertTrue(text.startswith("# Manifold Schema\n\nkeep me\n"))
        bak = codex.with_name("AGENTS.md.bak-jev-agent-setup")
        self.assertEqual(bak.read_text(), "# Manifold Schema\n\nkeep me\n")

    def test_updates_stale_block_in_place(self):
        g = self.paths["gemini"]
        g.parent.mkdir(parents=True)
        g.write_text(f"top\n\n{inst.BEGIN}\nold rules\n{inst.END}\n\nbottom\n")
        self.assertEqual(run(self.home, "--check", "--targets", "gemini", "--no-cli")[0], 1)
        run(self.home, "--targets", "gemini")
        text = g.read_text()
        self.assertNotIn("old rules", text)
        self.assertTrue(text.startswith("top\n") and text.rstrip().endswith("bottom"))
        self.assertEqual(text.count(inst.BEGIN), 1)

    def test_malformed_markers_refuse_without_writing(self):
        c = self.paths["claude"]
        c.parent.mkdir(parents=True)
        c.write_text(f"x\n{inst.BEGIN}\nno end marker\n")
        code, _ = run(self.home)
        self.assertEqual(code, 3)
        self.assertFalse(self.paths["codex"].exists())
        self.assertFalse((self.home / ".local/bin/jev").exists())

    def test_mirror_inlines_imports_and_flags_unresolved(self):
        c = self.paths["claude"]
        c.parent.mkdir(parents=True)
        (c.parent / "RTK.md").write_text("RTK rules here")
        c.write_text("# Mine\n\n@RTK.md\n\n@missing.md\n")
        run(self.home, "--mode", "mirror")
        agents = self.paths["agents"].read_text()
        self.assertIn("RTK rules here", agents)
        self.assertIn("<!-- unresolved import: missing.md -->", agents)
        self.assertIn("System One Decisioning", agents)  # claude's block is mirrored
        self.assertEqual(agents.count(inst.BEGIN), 1)  # nested markers stripped
        self.assertIn("@RTK.md", c.read_text())  # claude keeps its imports
        self.assertIn("RTK rules here\n\n", agents)  # sections stay separated

    def test_uninstall_restores_foreign_content(self):
        codex = self.paths["codex"]
        codex.parent.mkdir(parents=True)
        codex.write_text("keep me\n")
        run(self.home)
        run(self.home, "--uninstall")
        self.assertEqual(codex.read_text(), "keep me\n")
        self.assertFalse(self.paths["gemini"].exists())
        self.assertFalse((self.home / ".local/bin/jev").exists())

    def test_dry_run_writes_nothing(self):
        code, out = run(self.home, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("WOULD-WRITE", out)
        self.assertFalse(any(self.home.rglob("*.md")))

    def test_unknown_target_is_usage_error(self):
        self.assertEqual(run(self.home, "--targets", "cursor")[0], 2)

    def test_block_routes_subagent_model_choice_through_jev(self):
        block = inst.BLOCK_SRC.read_text()
        self.assertIn("## Choosing a model for a subagent", block)
        for rule in ("MUST NOT be offered as an option",   # no undispatchable candidates
                     "one question per\n   candidate MUST NOT be used",  # one Choice ranks them all
                     "top probability < 0.4",              # flat distribution, not low confidence
                     "MUST NOT be dispatched"):            # never pick a zero-probability model
            self.assertIn(rule, block)
        self.assertTrue((Path(inst.ASSETS).parent / "references/model-routing.md").is_file())

    def test_block_is_bcp14_and_never_overrides_permissions(self):
        block = inst.BLOCK_SRC.read_text()
        self.assertIn("BCP 14 [RFC 2119] [RFC 8174]", block)
        self.assertIn("Jev MUST NOT be used to approve, override, or re-litigate a permission decision", block)


class CliInputTest(unittest.TestCase):
    """The CLI rejects bad input with exit 2 before importing the SDK or touching the network."""

    def _run_cli(self, stdin_text):
        old_stdin, old_argv = sys.stdin, sys.argv
        sys.stdin, sys.argv = io.StringIO(stdin_text), ["jev"]
        try:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
                runpy.run_path(str(HERE / "jev"), run_name="__main__")
        finally:
            sys.stdin, sys.argv = old_stdin, old_argv
        return cm.exception.code

    def test_bad_json_exit_2(self):
        self.assertEqual(self._run_cli("not json"), 2)

    def test_missing_questions_exit_2(self):
        self.assertEqual(self._run_cli('{"state": {}}'), 2)

    def test_empty_questions_exit_2(self):
        self.assertEqual(self._run_cli('{"state": {}, "questions": {}}'), 2)


class _FakeAPIError(Exception):
    def __init__(self, status=None, request_id=None, retry_after_ms=None):
        super().__init__(f"{status} boom")
        self.status, self.request_id, self.retry_after_ms = status, request_id, retry_after_ms


class _FakeTimeout(Exception):
    pass


class ClassifyTest(unittest.TestCase):
    """Rejected (fix the request) vs unavailable (retry later) — decided without the SDK installed."""

    def setUp(self):
        self.classify = runpy.run_path(str(HERE / "jev"))["classify"]

    def test_client_errors_are_rejected_not_retryable(self):
        for status in (400, 401, 403, 404, 422):
            info = self.classify(_FakeAPIError(status, request_id="req_1"))
            self.assertEqual((info["exit"], info["error_kind"], info["retryable"]), (4, "rejected", False), status)
            self.assertEqual((info["status"], info["request_id"]), (status, "req_1"))

    def test_rate_limit_and_server_errors_are_retryable(self):
        for status in (429, 500, 502, 503):
            info = self.classify(_FakeAPIError(status))
            self.assertEqual((info["exit"], info["retryable"]), (3, True), status)
        self.assertEqual(self.classify(_FakeAPIError(429, retry_after_ms=1500))["retry_after_ms"], 1500)

    def test_no_status_means_unavailable(self):
        info = self.classify(_FakeTimeout())
        self.assertEqual((info["exit"], info["error_kind"], info["error"]), (3, "unavailable", "_FakeTimeout"))
        self.assertNotIn("status", info)

    def test_viewer_labels_failure_kind(self):
        render = runpy.run_path(str(HERE / "jev"))["render"]
        os.environ["NO_COLOR"] = "1"
        rej = render({"exit": 4, "error_kind": "rejected", "retryable": False, "questions": {}}, False)
        una = render({"exit": 3, "error_kind": "unavailable", "retryable": True, "questions": {}}, False)
        self.assertIn("exit 4 rejected", rej)
        self.assertNotIn("retryable", rej)
        self.assertIn("exit 3 unavailable · retryable", una)


class CliLogTest(unittest.TestCase):
    """Per-project JSONL logging and the `jev log` viewer, exercised offline via the no-key path."""

    REQ = '{"state": {"x": 1}, "questions": {"q1": {"type": "noul", "instructions": "Is `x` one?"}}}'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.logs, self.proj = t / "logs", t / "proj"
        self.proj.mkdir()
        self.saved = {k: os.environ.get(k) for k in ("HOME", "JEV_LOG_DIR", "JEV_LOG", "TYPESAFE_API_KEY", "NO_COLOR")}
        os.environ.update(HOME=str(t), JEV_LOG_DIR=str(self.logs), NO_COLOR="1")
        os.environ.pop("TYPESAFE_API_KEY", None)
        os.environ.pop("JEV_LOG", None)
        self.cwd = os.getcwd()
        os.chdir(self.proj)

    def tearDown(self):
        os.chdir(self.cwd)
        for k, v in self.saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.tmp.cleanup()

    def _cli(self, argv, stdin_text=""):
        old_stdin, old_argv = sys.stdin, sys.argv
        sys.stdin, sys.argv = io.StringIO(stdin_text), ["jev", *argv]
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as cm:
                runpy.run_path(str(HERE / "jev"), run_name="__main__")
        finally:
            sys.stdin, sys.argv = old_stdin, old_argv
        return cm.exception.code, out.getvalue()

    def _log_files(self):
        return list((self.logs / "projects").glob("*.jsonl")) if self.logs.exists() else []

    def test_call_is_logged_per_project_0600(self):
        self.assertEqual(self._cli([], self.REQ)[0], 2)  # no key → exit 2, still logged
        files = self._log_files()
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].name.startswith("proj-"))
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        import json
        rec = json.loads(files[0].read_text().splitlines()[0])
        self.assertEqual((rec["exit"], rec["project"], rec["state"]), (2, str(self.proj.resolve()), {"x": 1}))

    def test_log_disabled_with_env(self):
        os.environ["JEV_LOG"] = "0"
        self._cli([], self.REQ)
        self.assertEqual(self._log_files(), [])

    def test_viewer_renders_summary_and_failed_filter(self):
        self._cli([], self.REQ)
        self._cli([], self.REQ)
        code, out = self._cli(["log", "-v"])
        self.assertEqual(code, 0)
        self.assertIn("2 calls · 2 questions · 0 ok / 2 failed", out)
        self.assertIn("q1", out)
        self.assertIn("error: no API key", out)
        self.assertIn("Is `x` one?", out)
        code, out = self._cli(["log", "--json", "-n", "1"])
        self.assertEqual(len(out.strip().splitlines()), 1)
        code, out = self._cli(["log", "--projects"])
        self.assertIn(str(self.proj.resolve()), out)


if __name__ == "__main__":
    unittest.main()
