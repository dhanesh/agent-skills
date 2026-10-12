#!/usr/bin/env python3
"""Unit tests for agent_hooks (stdlib only; run: python3 test_agent_hooks.py).

Covers the pure parts of exact status tracking: Claude Code hook event →
status mapping, the settings.json merge that must never disturb other tools'
hooks, and the process/environment parsers that adopt untracked agents.
"""
import os
import shlex
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import agent_hooks as ah  # noqa: E402

BIN = "/opt/skills/tmux agent/scripts"


def ev(name, **kw):
    return {"hook_event_name": name, **kw}


class ClaudeEventStatus(unittest.TestCase):
    def test_lifecycle(self):
        cases = [
            (ev("SessionStart", source="startup"), "idle"),
            (ev("SessionStart", source="compact"), None),  # mid-turn; no news
            (ev("UserPromptSubmit"), "working"),
            (ev("PreToolUse", tool_name="Bash"), "working"),
            (ev("PreToolUse", tool_name="AskUserQuestion"), "blocked"),
            (ev("PreToolUse", tool_name="ExitPlanMode"), "blocked"),
            (ev("PermissionRequest", tool_name="Bash"), "blocked"),
            (ev("PostToolUse"), "working"),  # approval granted → running again
            (ev("PostToolUseFailure"), "working"),
            (ev("Notification", notification_type="permission_prompt"), "blocked"),
            (ev("Notification", notification_type="elicitation_dialog"), "blocked"),
            (ev("Notification", message="Claude needs your permission to use Bash"), "blocked"),
            (ev("Stop"), "done"),
            (ev("StopFailure"), "error"),
            (ev("SessionEnd", reason="exit"), ah.DEREGISTER),
            (ev("SessionEnd", reason="clear"), None),  # /clear: same pane, new session
            (ev("SubagentStop"), None),
            ({}, None),
        ]
        for payload, want in cases:
            with self.subTest(payload=payload):
                self.assertEqual(ah.claude_event_status(payload), want)

    def test_idle_prompt_is_not_blocked(self):
        # "Claude is waiting for your input" follows Stop: the turn is done, and
        # calling it blocked would send every finished agent to `prefix a b`.
        p = ev("Notification", notification_type="idle_prompt",
               message="Claude is waiting for your input")
        self.assertIsNone(ah.claude_event_status(p))


class MergeSettings(unittest.TestCase):
    OTHER = {"type": "command", "command": "/usr/local/bin/other-tool"}

    def base(self):
        return {"model": "x", "hooks": {
            "Stop": [{"hooks": [self.OTHER]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [self.OTHER]}],
        }}

    def ours(self, settings):
        return [(e, h["command"]) for e, groups in settings.get("hooks", {}).items()
                for g in groups for h in g.get("hooks", []) if ah.HOOK_MARKER in h["command"]]

    def test_adds_one_entry_per_event_and_keeps_others(self):
        out = ah.merge_claude_settings(self.base(), BIN)
        self.assertEqual(sorted(e for e, _ in self.ours(out)), sorted(ah.CLAUDE_EVENTS))
        self.assertEqual(out["model"], "x")
        self.assertIn({"hooks": [self.OTHER]}, out["hooks"]["Stop"])
        self.assertIn({"matcher": "Bash", "hooks": [self.OTHER]}, out["hooks"]["PreToolUse"])

    def test_idempotent(self):
        once = ah.merge_claude_settings(self.base(), BIN)
        self.assertEqual(ah.merge_claude_settings(once, BIN), once)

    def test_path_with_spaces_is_quoted(self):
        cmd = self.ours(ah.merge_claude_settings({}, BIN))[0][1]
        self.assertTrue(cmd.startswith("'/opt/skills/tmux agent/scripts/agent-hook' claude"))

    def test_moved_skill_replaces_old_path(self):
        old = ah.merge_claude_settings(self.base(), "/old/place")
        new = ah.merge_claude_settings(old, BIN)
        self.assertFalse(any("/old/place" in c for _, c in self.ours(new)))
        self.assertEqual(len(self.ours(new)), len(ah.CLAUDE_EVENTS))

    def test_python_is_written_into_the_command(self):
        py = "/opt/my py/bin/python"
        out = ah.merge_claude_settings({}, BIN, python=py)
        cmds = [c for _, c in self.ours(out)]
        self.assertEqual(len(cmds), len(ah.CLAUDE_EVENTS))
        for c in cmds:
            self.assertEqual(shlex.split(c)[:3], [py, BIN + "/agent-hook", "claude"])

    def test_python_form_replaces_old_form_without_duplicates(self):
        old = ah.merge_claude_settings(self.base(), BIN)
        new = ah.merge_claude_settings(old, BIN, python="/usr/bin/python")
        self.assertEqual(len(self.ours(new)), len(ah.CLAUDE_EVENTS))
        self.assertTrue(all(c.startswith("/usr/bin/python ") for _, c in self.ours(new)))
        self.assertEqual(ah.merge_claude_settings(new, BIN, python="/usr/bin/python"), new)
        self.assertEqual(ah.merge_claude_settings(new, BIN, enable=False), self.base())

    def test_remove_restores_original(self):
        added = ah.merge_claude_settings(self.base(), BIN)
        self.assertEqual(ah.merge_claude_settings(added, BIN, enable=False), self.base())
        self.assertEqual(ah.merge_claude_settings(ah.merge_claude_settings({}, BIN), BIN,
                                                  enable=False), {})

    def test_shared_group_keeps_other_hooks(self):
        mixed = {"hooks": {"Stop": [{"hooks": [
            self.OTHER, {"type": "command", "command": f"x claude {ah.HOOK_MARKER}"}]}]}}
        out = ah.merge_claude_settings(mixed, BIN, enable=False)
        self.assertEqual(out, {"hooks": {"Stop": [{"hooks": [self.OTHER]}]}})


class Adoption(unittest.TestCase):
    def test_pane_from_environ(self):
        self.assertEqual(ah.pane_from_environ("claude --x PATH=/bin TMUX_PANE=%27 TERM=x"), "%27")
        self.assertEqual(ah.pane_from_environ("HOME=/h\0TMUX_PANE=%3\0SHELL=zsh"), "%3")
        self.assertEqual(ah.pane_from_environ("MY_TMUX_PANE=%9 X=1"), "")
        self.assertEqual(ah.pane_from_environ("TMUX_PANE=%9x"), "")
        self.assertEqual(ah.pane_from_environ(""), "")

    def test_agent_candidates(self):
        lines = [
            "  101 claude --dangerously-skip-permissions",
            "  102 /bin/zsh",
            "  103 node /usr/lib/node_modules/@openai/codex/bin/codex.js",
            "  104 npx gemini",
            "  105 vim notes.md",
            "garbage",
        ]
        got = {(pid, agent) for pid, _, agent in ah.agent_candidates(lines)}
        self.assertIn((101, "claude"), got)
        self.assertIn((104, "gemini"), got)
        self.assertNotIn(102, {p for p, _ in got})
        self.assertNotIn(105, {p for p, _ in got})


class InstallerResolvesInterpreter(unittest.TestCase):
    """Review focus 5: the installer sources the resolver that ships beside it."""
    SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")

    def test_resolver_ships_next_to_installer(self):
        self.assertTrue(os.path.isfile(os.path.join(self.SCRIPTS, "find_python.sh")))

    def test_installer_sources_resolver_and_has_no_bare_python3(self):
        text = open(os.path.join(self.SCRIPTS, "install.sh")).read()
        self.assertIn('. "$AGENT_BIN/find_python.sh"', text)
        self.assertIn("SKILL_PYTHON", text)
        code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
        msg = "needs Python 3.10 or newer; set SKILL_PYTHON or install python3"
        self.assertFalse([ln for ln in code if "python3" in ln and msg not in ln])

    def test_installer_rejects_python_older_than_3_10(self):
        with tempfile.TemporaryDirectory() as td:
            bin_ = os.path.join(td, "bin")
            os.mkdir(bin_)
            shim = os.path.join(bin_, "python")
            with open(shim, "w") as fh:
                fh.write('#!/bin/sh\ncase "$*" in *version_info*) exit 1;; esac\nexit 0\n')
            os.chmod(shim, 0o755)
            r = subprocess.run(["/bin/bash", os.path.join(self.SCRIPTS, "install.sh")],
                               env={"PATH": bin_ + ":/bin:/usr/bin", "HOME": td},
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("needs Python 3.10 or newer", r.stderr)
            self.assertFalse(os.path.exists(os.path.join(td, ".tmux.conf")))

    def test_installer_registers_hooks_with_resolved_interpreter(self):
        text = open(os.path.join(self.SCRIPTS, "install.sh")).read()
        self.assertIn('install-claude "$CLAUDE_DIR/settings.json" --python "$PY"', text)

    def test_install_claude_cli_writes_python_option(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = os.path.join(td, "settings.json")
            hook = os.path.join(self.SCRIPTS, "agent-hook")
            for _ in range(2):  # idempotent
                subprocess.run([sys.executable, hook, "install-claude", cfg,
                                "--python", "/x/py thon"], check=True)
            import json
            data = json.load(open(cfg))
            cmds = [h["command"] for g in data["hooks"].values() for e in g
                    for h in e["hooks"]]
            self.assertEqual(len(cmds), len(ah.CLAUDE_EVENTS))
            self.assertTrue(all(shlex.split(c)[0] == "/x/py thon" for c in cmds))
            subprocess.run([sys.executable, hook, "install-claude", cfg, "--remove"],
                           check=True)
            self.assertNotIn("hooks", json.load(open(cfg)))


if __name__ == "__main__":
    unittest.main()
