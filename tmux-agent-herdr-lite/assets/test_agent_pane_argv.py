#!/usr/bin/env python3
"""Argv tests for agent-pane (stdlib only; run: python3 test_agent_pane_argv.py).

agent-pane used to join the command words with `$*` and put that text into the
`sh -c` wrapper that tmux runs, so the shell parsed the words a second time. A
word with `;`, `$(...)` or a quote then ran as shell code or broke the wrapper.

These tests put a stub `tmux` first on PATH. The stub records the argv of each
call and never starts a server. The tests check that each command word reaches
tmux as its own argument, that the `sh -c` script text holds no command word,
and that the wrapper, run the way tmux runs it, prints each word literally and
runs no shell code. They also check that agent-resume splits a stored command
back into the same words.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "scripts"))
AGENT_PANE = os.path.join(SCRIPTS, "agent-pane")
AGENT_RESUME = os.path.join(SCRIPTS, "agent-resume")

# A stub tmux: one NUL-separated argv file per call, numbered in call order.
STUB = r"""#!/bin/sh
n=$(ls "$TMUX_STUB_LOG" | wc -l | tr -d ' ')
printf '%s\0' "$@" > "$TMUX_STUB_LOG/$(printf '%03d' "$n")"
case "$1" in
  has-session) exit 0 ;;
  list-windows) echo control ;;
  display-message) echo '%7' ;;
esac
exit 0
"""


def dangerous_words(marker_dir):
    semi = os.path.join(marker_dir, "semi")
    subst = os.path.join(marker_dir, "subst")
    return [
        "printf", "%s\\n",
        "a;touch " + semi,
        "b$(touch " + subst + ")",
        "it's ok",
        'say "hi" `touch ' + semi + '`',
    ]


class AgentPaneArgv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-pane-argv-")
        self.bin = os.path.join(self.tmp, "bin")
        self.log = os.path.join(self.tmp, "log")
        self.work = os.path.join(self.tmp, "work")
        for d in (self.bin, self.log, self.work):
            os.makedirs(d)
        stub = os.path.join(self.bin, "tmux")
        with open(stub, "w") as f:
            f.write(STUB)
        os.chmod(stub, 0o755)
        self.env = dict(os.environ)
        self.env.pop("TMUX", None)
        self.env.update({
            "PATH": self.bin + os.pathsep + os.environ.get("PATH", ""),
            "TMUX_STUB_LOG": self.log,
            "HOME": os.path.join(self.tmp, "home"),
            "AGENT_TMUX_ROOT": os.path.join(self.tmp, "agents"),
            "AGENT_TMUX_SESSION": "stub",
        })

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def calls(self):
        out = []
        for name in sorted(os.listdir(self.log)):
            with open(os.path.join(self.log, name), "rb") as f:
                raw = f.read().decode()
            out.append(raw.split("\0")[:-1])
        return out

    def run_pane(self, *args):
        return subprocess.run(["bash", AGENT_PANE, *args], env=self.env,
                              cwd=self.work, capture_output=True, text=True)

    def new_window(self):
        found = [c for c in self.calls() if c and c[0] == "new-window"]
        self.assertEqual(len(found), 1, self.calls())
        return found[0]

    def test_each_word_is_its_own_argument(self):
        words = dangerous_words(self.work)
        r = self.run_pane("--cwd", self.work, "semi", *words)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "%7")
        call = self.new_window()
        self.assertIn("-c", call)
        self.assertEqual(call[call.index("-c") + 1], self.work)
        cmd = call[call.index("--") + 1:]
        self.assertEqual(cmd[0], "sh")
        self.assertEqual(cmd[1], "-c")
        script = cmd[2]
        # After the script: $0, the name, then every word, each one literal.
        self.assertEqual(cmd[3:], ["agent-pane", "semi", *words])
        joined = " ".join(words)
        for arg in call:
            self.assertNotIn(joined, arg)
        for word in words[2:]:  # the dangerous words
            self.assertNotIn(word, script)
        self.assertEqual(os.listdir(self.work), [])

    def test_wrapper_prints_words_literally_and_runs_no_shell_code(self):
        words = dangerous_words(self.work)
        r = self.run_pane("semi", *words)
        self.assertEqual(r.returncode, 0, r.stderr)
        cmd = self.new_window()
        cmd = cmd[cmd.index("--") + 1:]
        # tmux execs a multi-argument command directly: do the same here.
        env = dict(self.env, SHELL="/usr/bin/true")
        out = subprocess.run(cmd, env=env, cwd=self.work, capture_output=True,
                             text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.splitlines()
        self.assertEqual(lines[0], "AGENT_STATUS: working name=semi")
        self.assertEqual(lines[1:5], words[2:])
        self.assertEqual(lines[-1], "AGENT_STATUS: done exit_code=0")
        self.assertEqual(os.listdir(self.work), [])

    def test_wrapper_reports_the_exit_code(self):
        r = self.run_pane("fail", "sh", "-c", "exit 3")
        self.assertEqual(r.returncode, 0, r.stderr)
        cmd = self.new_window()
        cmd = cmd[cmd.index("--") + 1:]
        out = subprocess.run(cmd, env=dict(self.env, SHELL="/usr/bin/true"),
                             capture_output=True, text=True,
                             stdin=subprocess.DEVNULL)
        self.assertEqual(out.stdout.splitlines()[-1],
                         "AGENT_STATUS: done exit_code=3")

    def test_metadata_keeps_the_words(self):
        words = dangerous_words(self.work)
        r = self.run_pane("--agent", "claude-code", "semi", *words)
        self.assertEqual(r.returncode, 0, r.stderr)
        meta = os.path.join(self.env["AGENT_TMUX_ROOT"], "panes", "7.json")
        with open(meta) as f:
            data = json.load(f)
        self.assertEqual(shlex.split(data["command"]), words)
        self.assertEqual(data["agent"], "claude")
        self.assertEqual(data["pane_id"], "%7")
        self.assertEqual(data["window"], "semi")

    def test_simple_command_record_is_unchanged(self):
        r = self.run_pane("rev", "claude", "--model", "opus")
        self.assertEqual(r.returncode, 0, r.stderr)
        meta = os.path.join(self.env["AGENT_TMUX_ROOT"], "panes", "7.json")
        with open(meta) as f:
            data = json.load(f)
        self.assertEqual(data["command"], "claude --model opus")
        self.assertEqual(data["agent"], "claude")

    def test_missing_name_or_command_exits_64(self):
        self.assertEqual(self.run_pane().returncode, 64)
        self.assertEqual(self.run_pane("only-name").returncode, 64)

    def test_resume_splits_the_stored_command(self):
        words = dangerous_words(self.work)
        panes = os.path.join(self.env["AGENT_TMUX_ROOT"], "panes")
        os.makedirs(panes)
        rec = {"name": "semi", "pane_id": "%9", "agent": "", "cwd": "",
               "command": shlex.join(words)}
        with open(os.path.join(panes, "9.json"), "w") as f:
            json.dump(rec, f)
        # The stub prints no pane for list-panes, so %9 counts as dead.
        r = subprocess.run(["bash", AGENT_RESUME, "--dry-run", "semi"],
                           env=self.env, cwd=self.work, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        line = r.stdout.strip()
        self.assertTrue(line.startswith("would run: "), line)
        argv = shlex.split(line[len("would run: "):])
        self.assertEqual(argv[1:], ["semi", *words])


if __name__ == "__main__":
    unittest.main()
