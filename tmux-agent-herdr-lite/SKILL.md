---
name: tmux-agent-herdr-lite
description: Adds a tmux-based agent cockpit with Zellij-like human ergonomics and Herdr-like agent supervision. Use when converting local tmux into a coding-agent workspace with menus, dashboard, pane navigation, per-agent status detection (Claude Code, Codex, Gemini, and more), jump-to-blocked routing, agent-to-agent coordination (read/send/run/wait), transition notifications, git-worktree isolation, and session resume.
license: MIT
compatibility: Requires tmux 3.2+, bash, python3, and a POSIX-like shell. Optional clipboard support uses pbcopy, xclip, or wl-copy; optional sounds use paplay, pw-play, or afplay; optional persistence uses TPM with tmux-resurrect/tmux-continuum.
metadata:
  author: dhanesh
  version: "2.0.0"
  tags: "tmux,zellij,herdr,coding-agents,terminal-multiplexer,automation"
---

# Tmux Agent Herdr-Lite

## What this skill provides

Use this skill to turn tmux into a practical coding-agent cockpit that combines:

- **Zellij-like human ergonomics:** mouse support, popup dashboard/help, command menu, pane splits, pane navigation/resizing, zoom, layout cycling, session tree, and vi copy mode.
- **Herdr-like agent supervision:** consistent agent launch, per-agent screen detection (Claude Code, Codex, Gemini, OpenCode/Kilo, Amp, Cursor, Copilot, Droid, Cline, Devin, Kimi, Kiro, Grok, Hermes, Qoder, Antigravity, Pi — user-overridable via JSON manifests), jump-to-status navigation, transition notifications, and a compact status bar.
- **Herdr-like agent coordination:** any agent or script can list, read, type into, command, and wait on other panes — the shell-script equivalent of Herdr's socket API.
- **Persistence and isolation:** relaunch dead agents after a tmux restart (with native `--resume` flags where the CLI supports it) and give each agent its own git worktree.

This does not replace tmux, Zellij, or Herdr. It keeps tmux as the durable substrate and adds the useful agent/human workflow layer on top. `references/herdr-parity.md` maps exactly what is ported, approximated, and out of scope.

## Install

From a project or global agent skills environment, install the skill with:

```bash
npx skills add Dhanesh/agent-skills --skill tmux-agent-herdr-lite
```

Then, after this skill is available to the agent, install the tmux integration from the skill directory:

```bash
bash scripts/install.sh
```

The installer copies executable scripts to `~/.local/bin`, writes tmux config to `~/.tmux/agent-panes/tmux-agent.conf`, and appends a `source-file` line to `~/.tmux.conf` if needed. When TPM is already installed it also wires up the optional tmux-resurrect/tmux-continuum persistence layer (`assets/tmux-agent-persistence.conf`); otherwise it prints how to enable it later — a plain install needs no network.

## Commands available after install

Full flags and environment variables: `references/commands.md`.

- `agent-workspace` — create or attach the `agents` tmux cockpit session.
- `agent-pane [--agent <kind>] [--cwd <dir>] <name> <command...>` — launch and register an agent pane/window.
- `agent-list [--json]` — registry with statuses (machine-readable with `--json`).
- `agent-status-scan` / `agent-status-summary` / `agent-dashboard` — refresh, status-bar line, full dashboard.
- `agent-jump blocked|error|working|idle|done` — focus the first pane in that state.
- `agent-explain <target>` — why did this pane classify that way (matched rule, title, tail).
- `agent-read <target> [--lines N] [--source recent]` — read another pane's output.
- `agent-send <target> <text>` / `--keys Enter` — type into a pane (no Enter) or press keys.
- `agent-run <target> <command>` — send a command and press Enter atomically.
- `agent-wait <target> --status done | --match <regex> [--timeout S]` — block on a status or output pattern.
- `agent-notify <name> <status> [msg]` — toast/sound (also fired automatically on transitions).
- `agent-resume [--dry-run] [<name> --session-id <id>]` — relaunch dead agents; native resume where supported.
- `agent-worktree create|open|remove|list <branch>` — per-agent git worktree isolation.
- `agent-cheatsheet` — print keybindings and commands.

Runtime state is stored under `~/.tmux/agent-panes/`. Targets accept an agent name, window name, or pane id; names are stable, pane ids are not.

## Keybindings installed in tmux

The installer sources `assets/tmux-agent.conf`, which adds:

```text
prefix ?        dashboard/help popup
prefix m        command menu (jump / worktree / resume / split / kill)
prefix S        session/window/pane tree
prefix g        jump first blocked agent
prefix E        jump first error (crashed) agent
prefix G        refresh agent statuses
prefix W/I/D    jump working / idle / done agent
prefix | / -    split horizontal / vertical
prefix h/j/k/l  select pane left/down/up/right
prefix H/J/K/L  resize pane left/down/up/right
prefix Tab      last pane
prefix z        zoom pane
prefix Space    cycle layouts
prefix r        reload tmux config
prefix [        copy mode
```

It also enables mouse support, vi copy mode, focus events, top pane border titles, a compact status bar summary, and automatic window rename/aggressive resize.

## Recommended workflow for agents

1. Verify prerequisites:

   ```bash
   tmux -V
   bash scripts/install.sh
   ```

2. Create or attach the cockpit:

   ```bash
   agent-workspace
   ```

3. Launch work through `agent-pane`, not raw `tmux send-keys` — optionally in an isolated worktree:

   ```bash
   agent-worktree create fix-auth --window
   agent-pane --agent claude --cwd ~/.tmux/agent-panes/worktrees/myrepo/fix-auth backend 'claude'
   agent-pane reviewer 'codex'
   agent-pane tests 'uv run pytest -q'
   ```

4. Coordinate instead of watching: wait on siblings, read their screens, answer their prompts. Recipes for servers, test runs, prompt-answering, and fan-out live in `references/coordination-recipes.md`:

   ```bash
   agent-wait tests --status done --timeout 600
   agent-read reviewer --lines 40
   agent-send backend --keys Enter
   ```

5. Route attention to the highest-leverage pane (notifications fire on blocked/error/done transitions automatically):

   ```bash
   agent-jump blocked
   ```

6. Verify real outcomes separately. A `done` state means the pane appears complete; it does not prove the code is correct. When a status looks wrong, debug it with `agent-explain <name>` rather than guessing.

7. After a tmux server restart, recover with `agent-resume` (add `--session-id` to reopen a native CLI session, e.g. `claude --resume <id>`).

## Status model

Statuses are routing hints, not truth:

- `error` — a crash or non-zero exit (traceback, `command failed`, `exit_code=N` where N≠0). Distinct from `blocked`: a crashed agent needs a look, not an answer.
- `blocked` — a known approval/question UI for the pane's agent, or an explicit blocked marker.
- `working` — active output, a spinner title, or the agent's own "esc to interrupt" chrome.
- `idle` — quiescent, or a finished pane you have already viewed.
- `done` — completion sentinel, or a known agent showing its prompt box again after working — until you focus the pane, which demotes it to `idle` (viewed), Herdr-style.
- `unknown` — metadata or pane lookup is incomplete.

Detection is two-layered, ported from Herdr's manifests: panes launched with a known `--agent` (or an inferable command) are classified **only** by that agent's screen rules plus the pane title — loose word heuristics are skipped, so an agent *discussing* an error or rate limit is not mislabeled, and unknown prompts fall back to `idle`, never `blocked`. Unrecognized commands use the generic tail heuristics, where explicit `AGENT_STATUS:` sentinels and real failure signals win over loose words, and active output beats advisory tokens. Per-agent rules can be replaced without editing code by dropping `<agent>.json` files into `~/.tmux/agent-panes/detect/` (Herdr's local manifest overrides; format in `references/commands.md`). The classifier lives in `scripts/agent_classify.py` (pure, unit-tested by `assets/test_agent_classify.py`, which `make gate` runs).

For best accuracy, instruct coding agents to print explicit markers — they outrank every heuristic:

```text
AGENT_STATUS: working
AGENT_STATUS: blocked reason="needs approval"
AGENT_STATUS: done result="tests passed"
```

## Files in this skill

- `scripts/install.sh` — installs scripts and tmux config (persistence layer only when TPM exists).
- `scripts/agent-pane`, `scripts/agent-workspace` — launch/register agents, cockpit session.
- `scripts/agent-status-scan`, `scripts/agent-status-summary`, `scripts/agent-dashboard`, `scripts/agent-jump`, `scripts/agent-explain` — status pipeline.
- `scripts/agent-list`, `scripts/agent-read`, `scripts/agent-send`, `scripts/agent-run`, `scripts/agent-wait` — coordination commands.
- `scripts/agent-notify`, `scripts/agent-resume`, `scripts/agent-worktree`, `scripts/agent-menu`, `scripts/agent-cheatsheet` — notifications, persistence, isolation, menus.
- `scripts/agent_classify.py`, `scripts/agent_registry.py` — pure classifier (per-agent manifests) and target resolution.
- `assets/tmux-agent.conf`, `assets/tmux-agent-persistence.conf` — tmux configuration (core + optional TPM plugins).
- `assets/test_agent_classify.py` — stdlib unit suite (run by `make gate`).
- `references/commands.md`, `references/coordination-recipes.md`, `references/herdr-parity.md` — full CLI reference, coordination recipes, and the Herdr feature map.

## Common pitfalls

- Do not expect exact Zellij or Herdr behavior — tmux approximates menus, popups, detection, and coordination, but has no plugin runtime or socket event push; `agent-wait` polls. See `references/herdr-parity.md` before assuming a feature exists.
- Do not start agent panes manually if you want status tracking. Use `agent-pane`; name targets by agent name, not pane id.
- Do not trust `done` as correctness. Verify artifacts, tests, and diffs separately.
- Do not answer a `blocked` prompt blind — `agent-read` the question first, then `agent-send`.
- Keep status polling around five seconds. Faster polling adds overhead without much value.
- Notifications default to tmux toasts + sound; set `AGENT_NOTIFY=off` or `AGENT_NOTIFY_SOUND=off` in noisy environments.

## Verification checklist

Run after installing:

```bash
tmux -V
command -v agent-pane agent-list agent-read agent-send agent-run agent-wait \
  agent-explain agent-resume agent-worktree agent-status-scan agent-dashboard
agent-workspace
agent-pane smoke 'echo AGENT_STATUS: working; sleep 1; echo AGENT_STATUS: done; sleep 10'
agent-wait smoke --status done --timeout 30
agent-explain smoke
agent-list --json
agent-jump done
```

The tmux status bar should show the compact agent summary, `prefix ?` should open the dashboard popup, and the skill's unit suite should pass via `make gate-skill SKILL=tmux-agent-herdr-lite` (or `python3 assets/test_agent_classify.py`).
