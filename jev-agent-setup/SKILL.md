---
name: jev-agent-setup
description: "Install and verify a machine-wide Jev (TypeSafe System One) setup for coding agents: the `jev` CLI plus a managed BCP 14 instruction block in the global files of Claude Code, Codex, Gemini CLI and ~/.agents/AGENTS.md, so every agent offloads its rank/classify/yes-no decisions to Jev. Use when the user says 'set up Jev for my agents', 'install the jev CLI', 'make Codex and Gemini use Jev too', 'replicate my Jev setup on this machine', or 'check my Jev setup for drift'. Not for designing Jev judgments inside an application — use typesafe-ai for that."
license: MIT
compatibility: python3 stdlib for the installer; uv plus network and a TypeSafe API key when the jev CLI runs; macOS or Linux home-directory layout.
metadata:
  author: dhanesh
  version: "0.1.1"
  tags: "typesafe,jev,system-one,agent-setup,bcp14"
---

# jev-agent-setup

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY in this skill are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and only when, they appear in all capitals.

You set up, or verify, a reproducible Jev configuration on the current machine. Once it is in
place, every coding agent there reads the same rules for offloading System One decisions to
Jev and can call Jev from a shell. The installer does the writing; your job is to pick the
right targets and mode, run it, and prove the result.

## When to use

Use this skill for machine-level setup: a new laptop, a newly installed agent, a request to
"make Codex and Gemini use Jev too", or a drift check after instructions changed. It does not
teach how to design questions for an application; that is the `typesafe-ai` skill, which the
installed block tells agents to load. The block governs decisions only. It never lets Jev
approve or override a permission decision.

## Workflow

1. **Locate the skill and inspect the machine.** Resolve the skill directory, then preview:

   ```bash
   SKILL_DIR="<this skill's base directory>"   # your harness provides it when the skill loads
   test -f "$SKILL_DIR/assets/install_jev_setup.py" || SKILL_DIR=$(find ~/.claude ~/.agents ~/.config -type d -name jev-agent-setup 2>/dev/null | head -1)
   python3 "$SKILL_DIR/assets/install_jev_setup.py" --dry-run
   ```

   State in one line which of the four targets exist and whether `uv` and a key are present.
2. **Choose the mode and targets with the user.** `--mode block` (default) adds only the Jev
   block. `--mode mirror` gives Codex, Gemini and `~/.agents` a managed copy of the whole
   `~/.claude/CLAUDE.md`, `@imports` inlined. Pick mirror only if the user wants one global
   instruction set for every agent. Narrow the scope with `--targets` (for example
   `--targets codex,gemini`). Flags are in [references/parameters.md](references/parameters.md).
3. **Install.** Run the same command without `--dry-run`. If your harness refuses writes to
   an agent instruction file (Claude Code auto mode treats `~/.claude/CLAUDE.md` as
   self-modification), you MUST NOT route around it, because that refusal is the
   harness's permission boundary. Give the user the exact command to run
   themselves and continue with the other targets.
4. **Key.** The installer creates a 0600 placeholder at `~/.config/typesafe/env`. You MUST NOT
   ask for the key in chat or write it anywhere, because chat and files are kept in transcripts
   and history. Tell the user to edit that file.
5. **Verify and repair.** Run `--check` and require exit 0. Then smoke-test the CLI with one
   harmless Noul (`echo '{"state":{"x":"sky is blue"},"questions":{"q":{"type":"noul","instructions":"Is x true?"}}}' | jev`).
   On a `STALE`/`MISSING` line, re-run the install for that target. On exit 3 (malformed
   markers), show the user the file and fix the markers by hand. On a smoke-test exit 2 or 3,
   report "key or network missing" and stop; on exit 4, report the `jev` error line (the request was rejected, usually a bad key or model name). Show the user `jev log` so they know where
   calls are recorded (per project under `~/.local/state/jev/`).

## Deliverable

A short setup report with one row per target: `claude`, `agents`, `codex`, `gemini`, `cli`,
`key`, `uv`. Each row carries the status (`OK`, `WROTE`, `TODO`, `USER-ACTION`), the path, and
any command the user still has to run. After the table come the `--check` exit code and the
smoke-test result.

## Success criteria

- `install_jev_setup.py --check` exits 0 for every selected target, including `cli`.
- A smoke-test call returns a `noul` answer, or the report says plainly why it could not run.
- Content outside the managed markers in each file is unchanged; first-touch backups exist
  as `*.bak-jev-agent-setup`.
- The installer is covered by `assets/test_install_jev_setup.py` and the outcome eval
  `eval/run_eval.py`; both run offline in a temp home.
