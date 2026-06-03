---
name: tmux-agent-herdr-lite
description: Adds a tmux-based agent cockpit with Zellij-like human ergonomics and Herdr-like agent supervision. Use when converting local tmux into a coding-agent workspace with menus, dashboard, pane navigation, status detection, and jump-to-blocked/working/done agents.
license: MIT
compatibility: Requires tmux 3.2+, bash, python3, and a POSIX-like shell. Optional clipboard support uses pbcopy, xclip, or wl-copy when available.
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "tmux,zellij,herdr,coding-agents,terminal-multiplexer,automation"
---

# Tmux Agent Herdr-Lite

## What this skill provides

Use this skill to turn tmux into a practical coding-agent cockpit that combines:

- **Zellij-like human ergonomics:** mouse support, popup dashboard/help, command menu, pane splits, pane navigation/resizing, zoom, layout cycling, session tree, and vi copy mode.
- **Herdr-like agent supervision:** consistent agent launch, pane metadata, status classification, compact tmux status summary, and jump-to-status navigation.

This does not replace tmux, Zellij, or Herdr. It keeps tmux as the durable substrate and adds the useful agent/human workflow layer on top.

## Install

From a project or global agent skills environment, install the skill with:

```bash
npx skills add Dhanesh/agent-skills --skill tmux-agent-herdr-lite
```

Then, after this skill is available to the agent, install the tmux integration from the skill directory:

```bash
bash scripts/install.sh
```

The installer copies executable scripts to `~/.local/bin`, writes tmux config to `~/.tmux/agent-panes/tmux-agent.conf`, and appends a `source-file` line to `~/.tmux.conf` if needed.

## Commands available after install

- `agent-workspace` — create or attach the `agents` tmux cockpit session.
- `agent-pane <name> <command...>` — launch and register an agent pane/window.
- `agent-status-scan` — inspect registered tmux panes and update state.
- `agent-status-summary` — print compact status for tmux status bar.
- `agent-dashboard` — print full dashboard and key help.
- `agent-menu` — open a tmux display-menu with common actions.
- `agent-jump blocked|working|idle|done` — jump to the first pane in that state.
- `agent-cheatsheet` — print keybindings and commands.

Runtime state is stored under `~/.tmux/agent-panes/`.

## Keybindings installed in tmux

The installer sources `assets/tmux-agent.conf`, which adds:

```text
prefix ?        dashboard/help popup
prefix m        command menu
prefix S        session/window/pane tree
prefix g        jump first blocked agent
prefix G        refresh agent statuses
prefix W/I/D    jump working / idle / done agent
prefix | / -    split horizontal / vertical
prefix h/j/k/l  select pane left/down/up/right
prefix H/J/K/L  resize pane left/down/up/right
prefix z        zoom pane
prefix Space    cycle layouts
prefix r        reload tmux config
prefix [        copy mode
```

It also enables mouse support, vi copy mode, top pane border titles, a compact status bar summary, and automatic window rename/aggressive resize.

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

3. Launch work through `agent-pane`, not raw `tmux send-keys`:

   ```bash
   agent-pane backend 'codex'
   agent-pane reviewer 'claude'
   agent-pane tests 'uv run pytest -q'
   ```

4. Refresh and inspect status:

   ```bash
   agent-status-scan
   agent-dashboard
   ```

5. Route attention to the highest-leverage pane:

   ```bash
   agent-jump blocked
   ```

6. Verify real outcomes separately. A `done` state means the pane appears complete; it does not prove the code is correct.

## Status model

Statuses are routing hints, not truth:

- `blocked` — approval prompt, question, password prompt, permission/rate-limit issue, or explicit blocked marker.
- `working` — pane output changed recently and the pane still exists.
- `idle` — pane still exists but output has not changed beyond the idle threshold.
- `done` — completion marker or exited-looking output.
- `unknown` — metadata or pane lookup is incomplete.

For better accuracy, instruct coding agents to print explicit markers:

```text
AGENT_STATUS: working
AGENT_STATUS: blocked reason="needs approval"
AGENT_STATUS: done result="tests passed"
```

## Files in this skill

- `scripts/install.sh` — installs scripts and tmux config.
- `scripts/agent-pane` — launches and registers agent panes.
- `scripts/agent-status-scan` — scans pane output and writes status JSON.
- `scripts/agent-status-summary` — prints compact status summary.
- `scripts/agent-dashboard` — prints dashboard/help.
- `scripts/agent-menu` — opens tmux action menu.
- `scripts/agent-workspace` — creates/attaches cockpit session.
- `scripts/agent-cheatsheet` — prints command/key reference.
- `assets/tmux-agent.conf` — tmux configuration snippet.

## Common pitfalls

- Do not expect exact Zellij behavior. tmux can approximate menus, popups, layouts, mouse, copy mode, and navigation, but not Zellij's native plugin/UI runtime.
- Do not expect exact Herdr behavior. tmux can infer status from pane output and metadata, but it is not a semantic agent API.
- Do not start agent panes manually if you want status tracking. Use `agent-pane`.
- Do not trust `done` as correctness. Always verify artifacts, tests, and diffs separately.
- Keep status polling around five seconds. Faster polling adds overhead without much value.

## Verification checklist

Run after installing:

```bash
tmux -V
command -v agent-pane agent-status-scan agent-status-summary agent-jump agent-dashboard agent-menu agent-workspace agent-cheatsheet
agent-cheatsheet
agent-workspace
agent-pane smoke 'echo AGENT_STATUS: working; sleep 1; echo AGENT_STATUS: done; sleep 10'
agent-status-scan
agent-dashboard
agent-jump done
```

The tmux status bar should show the compact agent summary, and `prefix ?` should open the dashboard popup inside tmux.
