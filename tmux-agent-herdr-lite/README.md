# tmux-agent-herdr-lite

A tmux-based agent cockpit that combines **Zellij-like human ergonomics** with **Herdr-like agent supervision** — mouse support, popup dashboards, pane navigation, and automatic status detection (error / blocked / working / idle / done) — all on top of durable tmux sessions. Use it to turn any local tmux into a structured workspace for running multiple coding agents in parallel.

> This README is for humans browsing the folder. The agent-facing instructions live in [`SKILL.md`](./SKILL.md) — that's what Claude reads when the skill triggers.

## Install

```bash
npx skills add dhanesh/agent-skills --skill tmux-agent-herdr-lite
```

Then run the installer from the skill directory to copy scripts and wire up your tmux config:

```bash
bash scripts/install.sh
```

## What it sets up

- **Popup menus and dashboard** — `prefix ?` opens a help/dashboard popup; `prefix m` opens an action menu; keybindings mirror Zellij conventions.
- **Pane split and navigation** — `prefix |` / `prefix -` for horizontal/vertical splits; `prefix h/j/k/l` to move between panes; `prefix H/J/K/L` to resize; `prefix z` to zoom; `prefix Space` to cycle layouts.
- **Agent launch wrappers** — `agent-pane <name> <command>` launches and registers a pane so the cockpit can track it; `agent-workspace` creates or attaches the shared `agents` tmux session.
- **Pane metadata store** — each registered pane gets a JSON record under `~/.tmux/agent-panes/`; metadata survives session detach/reattach.
- **Status detection** — `agent-status-scan` inspects recent pane output and classifies each agent as `error`, `blocked`, `working`, `idle`, `done`, or `unknown` based on output patterns and explicit `AGENT_STATUS:` markers. Matching is restricted to the live tail of the pane so stale scrolled-up tokens don't pin a status.
- **Compact tmux status bar** — `agent-status-summary` writes a counts-by-state summary into the tmux status line so you can see the fleet at a glance without opening the dashboard.
- **Jump-to-status** — `agent-jump blocked|error|working|idle|done` switches focus directly to the first pane in that state; `prefix g` / `prefix E` / `prefix W` / `prefix I` / `prefix D` bind the states to keys.

## Prerequisites

- **tmux 3.2+** (`tmux -V` to confirm)
- **bash** and a POSIX-like shell
- **python3** (for status scanning)
- Optional: `pbcopy`, `xclip`, or `wl-copy` for clipboard support in copy mode

See SKILL.md for full usage.
