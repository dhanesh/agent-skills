#!/usr/bin/env python3
"""Unit tests for the cockpit's pure logic (stdlib only; run: python3 test_agent_classify.py).

Lives in assets/ so `make gate` discovers it. Covers:
- the generic classifier (each case maps to a base-in-reality 2026-06-22 audit
  finding the original rewrite remediated), and
- the per-agent screen manifests ported from Herdr src/detect/manifests/*.toml.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from agent_classify import classify, classify_explain, infer_agent, load_manifests  # noqa: E402

IDLE = 120
CASES = [
    # name, text, changed, idle_elapsed, expected  (generic path: agent unset)
    # Finding [34]: a stale 'done' / loose token scrolled out of the tail must not pin status
    # while output is actively streaming.
    ("stale 'done' in scrollback ignored while streaming",
     "make: Nothing to be done for 'all'\n" + "\n".join(f"compiling module {i}" for i in range(20)),
     True, 0, "working"),
    ("stale '429' in scrollback ignored while streaming",
     "HTTP 429 logged earlier\n" + "\n".join(f"processing batch {i}" for i in range(10)),
     True, 0, "working"),

    # Finding [35]: a real failure is its own 'error' state, checked before 'blocked',
    # and not masked by an incidental 'permission denied' / '429' substring.
    ("nonzero exit -> error (not blocked)",
     "AGENT_STATUS: done exit_code=1", False, 0, "error"),
    ("traceback -> error",
     "Traceback (most recent call last):\n  File \"x.py\", line 3\nValueError: boom", False, 1, "error"),
    ("error beats incidental 'permission denied'",
     "permission denied\ncommand failed", True, 0, "error"),

    # Regression guard for refuted finding [37]: unanchored regex matches multi-digit codes.
    ("exit_code=137 -> error", "AGENT_STATUS: done exit_code=137", False, 0, "error"),
    ("exit_code=255 -> error", "AGENT_STATUS: done exit_code=255", False, 0, "error"),
    ("exit_code=0 -> done", "AGENT_STATUS: done exit_code=0", False, 0, "done"),

    # Authoritative signals preempt activity; advisory ones only count when quiescent.
    ("interactive prompt -> blocked despite recent change",
     "Do you want to continue? [y/N]", True, 0, "blocked"),
    ("explicit done sentinel -> done", "AGENT_STATUS: done result=ok", False, 0, "done"),
    ("bare 'done' in tail when quiescent -> done", "build done", False, 5, "done"),
    ("'429' when quiescent -> blocked", "request failed: 429 too many requests", False, 5, "blocked"),

    # Activity and idle fallbacks.
    ("active output, no signal -> working",
     "\n".join(f"line {i}" for i in range(30)), True, 0, "working"),
    ("quiescent past threshold -> idle", "$ ", False, 300, "idle"),
    ("quiescent within threshold -> prev status", "$ ", False, 5, "working"),
]

# Per-agent manifest cases (Herdr parity):
# name, text, changed, idle_elapsed, prev, agent, title, expected
AGENT_CASES = [
    # Claude Code — patterns from herdr claude.toml.
    ("claude permission prompt -> blocked",
     "Bash command\n\nDo you want to proceed?\n❯ 1. Yes\n  2. No", True, 0,
     "working", "claude", "", "blocked"),
    ("claude selection form -> blocked",
     "Select an option\nEnter to select · Esc to cancel · arrow keys to navigate",
     True, 0, "working", "claude", "", "blocked"),
    ("claude esc-to-interrupt -> working even when quiescent",
     "✳ Cerebrating… (12s · esc to interrupt)", False, 300, "working", "claude", "", "working"),
    ("claude braille spinner title -> working",
     "some transcript text", False, 300, "working", "claude", "⠹ claude", "working"),
    ("claude prompt box after working -> done (finished, unviewed)",
     "I finished the refactor.\n╭──────╮\n│ ❯    │\n╰──────╯", False, 0,
     "working", "claude", "", "done"),
    ("claude prompt box when never working -> idle",
     "╭──────╮\n│ ❯    │\n╰──────╯", False, 0, "idle", "claude", "", "idle"),
    # A rejected approval (blocked -> Esc -> prompt box) is not a completion.
    ("claude prompt box after blocked -> idle, not done",
     "╭──────╮\n│ ❯    │\n╰──────╯", False, 0, "blocked", "claude", "", "idle"),
    # done persists across scans until the scanner's viewed-demotion.
    ("claude prompt box with prev done stays done",
     "╭──────╮\n│ ❯    │\n╰──────╯", False, 0, "done", "claude", "", "done"),
    # Strict-blocked exception: a live interactive prompt at the very bottom
    # (a subprocess asking for a password) still routes to blocked.
    ("claude pane at a password prompt -> blocked",
     "running deploy...\n$ sudo systemctl restart app\nPassword:", False, 5,
     "working", "claude", "", "blocked"),
    # Herdr strict-blocked: loose words on a known agent's screen don't block —
    # an agent *talking about* rate limits stays classified by chrome/fallback.
    ("claude discussing '429' stays non-blocked (strict-blocked)",
     "the server returned 429 too many requests earlier", False, 300,
     "working", "claude", "", "idle"),

    # Codex — patterns from herdr codex.toml.
    ("codex Action Required title -> blocked",
     "some output", True, 0, "working", "codex", "Action Required: approve command", "blocked"),
    ("codex allow command -> blocked",
     "Allow command?\n› yes\n  no", False, 0, "working", "codex", "", "blocked"),
    ("codex spinner title -> working",
     "streaming tokens", False, 0, "working", "codex", "⠋ codex", "working"),

    # Gemini / OpenCode / Amp / Cline.
    ("gemini apply change -> blocked",
     "│ Apply this change │\n│ ● Yes  ○ No", True, 0, "working", "gemini", "", "blocked"),
    ("opencode permission required -> blocked",
     "△ Permission required\nenter confirm  esc dismiss", False, 0,
     "working", "opencode", "", "blocked"),
    ("opencode esc interrupt -> working",
     "working on task... esc to interrupt", False, 300, "working", "opencode", "", "working"),
    ("amp thinking footer -> working",
     "╰ ⠋ thinking ────", False, 0, "working", "amp", "", "working"),
    ("cline any output -> working (no idle chrome)",
     "just some transcript text", False, 300, "working", "cline", "", "working"),
    ("cline tool permission -> blocked",
     "[ACT MODE] Execute command?\nYes / No", False, 0, "working", "cline", "", "blocked"),

    # Sentinels/errors still outrank manifests.
    ("sentinel done beats claude manifest",
     "AGENT_STATUS: done result=ok\n❯ ", False, 0, "working", "claude", "", "done"),
    ("crash beats manifest",
     "Traceback (most recent call last):\nboom", False, 0, "working", "claude", "", "error"),

    # Known agent, nothing matched: idle after threshold, never soft-blocked.
    ("claude no chrome, quiescent past threshold -> idle",
     "plain transcript", False, 300, "working", "claude", "", "idle"),
    ("claude no chrome, active -> working",
     "plain transcript", True, 0, "working", "claude", "", "working"),

    # Second wave of manifests (herdr parity: devin, kimi, kiro, grok,
    # hermes, qodercli, antigravity, pi).
    ("devin trust prompt -> blocked",
     "Do you trust the authors of this directory?\nYes, trust", False, 0,
     "working", "devin", "", "blocked"),
    ("devin prompt footer -> done after working",
     "context: 40%\n❭ Ask Devin to build", False, 0, "working", "devin", "", "done"),
    ("kimi approval panel -> blocked",
     "Run this command?\n▶ approve\n↵ confirm", False, 0, "working", "kimi", "", "blocked"),
    ("kimi moon spinner -> working",
     "some output\n🌗", False, 300, "working", "kimi", "", "working"),
    ("kiro requires approval -> blocked",
     "This tool requires approval\nyes, single permission", False, 0,
     "working", "kiro", "", "blocked"),
    ("grok option gutter -> blocked",
     "┃  2 (○) Yes, proceed", False, 0, "working", "grok", "", "blocked"),
    ("grok braille+stop chip -> working",
     "⠹ Generating [stop]", False, 300, "working", "grok", "", "working"),
    ("grok bare prompt hints -> done after working",
     "ctrl+.:shortcuts", False, 0, "working", "grok", "", "done"),
    ("hermes dangerous command -> blocked",
     "Dangerous command detected\nenter to confirm", False, 0,
     "working", "hermes", "", "blocked"),
    ("qodercli awaiting approval -> blocked",
     "Awaiting approval: allow or reject", False, 0, "working", "qodercli", "", "blocked"),
    ("antigravity permission -> blocked",
     "Requesting permission for: shell command", False, 0,
     "working", "antigravity", "", "blocked"),
    ("pi Working... -> working",
     "Working...", False, 300, "working", "pi", "", "working"),
]

INFER_CASES = [
    ("claude", "claude"),
    ("claude --dangerously-skip-permissions", "claude"),
    ("npx claude-code", "claude"),
    ("env FOO=1 codex --full-auto", "codex"),
    ("uv run pytest -q", ""),          # not an agent
    ("cursor-agent -p 'fix tests'", "cursor"),
    ("opencode", "opencode"),
    ("/usr/local/bin/gemini", "gemini"),
    ("agy", "antigravity"),
    ("kimi-code", "kimi"),
    ("", ""),
]


class TestGenericClassifier(unittest.TestCase):
    def test_cases(self):
        for name, text, changed, idle_elapsed, want in CASES:
            with self.subTest(name):
                self.assertEqual(classify(text, changed, idle_elapsed, IDLE), want)


class TestAgentManifests(unittest.TestCase):
    def test_cases(self):
        for name, text, changed, idle_elapsed, prev, agent, title, want in AGENT_CASES:
            with self.subTest(name):
                got, reason = classify_explain(text, changed, idle_elapsed, IDLE, prev,
                                               agent=agent, title=title)
                self.assertEqual(got, want, f"reason: {reason}")


class TestInferAgent(unittest.TestCase):
    def test_cases(self):
        for cmd, want in INFER_CASES:
            with self.subTest(cmd or "(empty)"):
                self.assertEqual(infer_agent(cmd), want)


class TestManifestOverrides(unittest.TestCase):
    def test_missing_dir_returns_builtins(self):
        m = load_manifests("/nonexistent/path")
        self.assertIn("claude", m)

    def test_override_replaces_agent_rules(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "claude.json"), "w") as f:
                json.dump([{"id": "custom", "state": "blocked", "priority": 500,
                            "all": [["contains", "my bespoke approval text"]]}], f)
            m = load_manifests(d)
            got, reason = classify_explain(
                "please review: my bespoke approval text", False, 0, IDLE,
                "working", agent="claude", manifests=m)
            self.assertEqual(got, "blocked")
            self.assertIn("custom", reason)
            # The built-in rules for claude were replaced, not merged.
            self.assertEqual([r["id"] for r in m["claude"]], ["custom"])

    def test_broken_override_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "claude.json"), "w") as f:
                f.write("{not json")
            m = load_manifests(d)
            self.assertGreater(len(m["claude"]), 1)  # built-ins intact


if __name__ == "__main__":
    unittest.main(verbosity=2)
