# tmux-agent-herdr-lite

A tmux-based agent cockpit that combines **Zellij-like human ergonomics** with **Herdr-like agent supervision and coordination** — mouse support, popup dashboards, per-agent status detection (Claude Code, Codex, Gemini, OpenCode/Kilo, Amp, Cursor, Copilot, Droid, Cline, Devin, Kimi, Kiro, Grok, Hermes, Qoder, Antigravity, Pi), agent-to-agent read/send/run/wait, transition notifications, git-worktree isolation, and post-restart resume — all on top of durable tmux sessions. Use it to turn any local tmux into a structured workspace for running multiple coding agents in parallel.

> This README is for humans browsing the folder. The agent-facing instructions live in [`SKILL.md`](./SKILL.md) — that's what Claude reads when the skill triggers. The full command reference, coordination recipes, and the Herdr feature-parity map live in [`references/`](./references/).

## Install

```bash
npx skills add dhanesh/agent-skills --skill tmux-agent-herdr-lite
```

Then run the installer from the skill directory to copy scripts and wire up your tmux config:

```bash
bash scripts/install.sh
```

## What it sets up

- **Popup menus and dashboard** — `prefix ?` opens a help/dashboard popup; `prefix m` opens an action menu (jump, worktree, resume, splits); keybindings mirror Zellij conventions.
- **Pane split and navigation** — `prefix |` / `prefix -` splits; `prefix h/j/k/l` to move; `prefix H/J/K/L` to resize; `prefix Tab` last pane; `prefix z` zoom; `prefix Space` cycle layouts.
- **Agent launch wrappers** — `agent-pane [--agent kind] [--cwd dir] <name> <command>` launches and registers a pane; `agent-workspace` creates or attaches the shared `agents` session.
- **Per-agent status detection** — `agent-status-scan` classifies each pane as `error`, `blocked`, `working`, `idle`, or `done` using detection manifests ported from [Herdr](https://github.com/ogulcancelik/herdr)'s per-agent screen rules (approval prompts, spinner titles, prompt-box chrome) plus explicit `AGENT_STATUS:` markers. `agent-explain` shows exactly which rule fired. A finished pane stays `done` until you focus it, then demotes to `idle`.
- **Agent-to-agent coordination** — `agent-list --json`, `agent-read`, `agent-send` (no Enter), `agent-run` (with Enter), and `agent-wait --status/--match`: the shell equivalent of Herdr's socket API, so one agent can drive and monitor its siblings.
- **Notifications** — transitions into `blocked`/`error`/`done` fire a tmux toast (optionally a desktop notification) and a sound cue with terminal-bell fallback; suppressed when you're already looking at the pane.
- **Persistence and isolation** — `agent-resume` relaunches dead agents after a tmux server restart, using native session resume (`claude --resume`, `codex resume`, …) when you pass a session id; `agent-worktree` gives each agent an isolated git worktree; optional tmux-resurrect/tmux-continuum config restores layouts across reboots (wired automatically when TPM is present).
- **Jump-to-status** — `agent-jump blocked|error|working|idle|done`; `prefix g/E/W/I/D` bind the states to keys; a compact fleet summary lives in the tmux status bar.

## Prerequisites

- **tmux 3.2+** (`tmux -V` to confirm)
- **bash** and a POSIX-like shell
- **python3** (status scanning and registry, stdlib only)
- Optional: `pbcopy`/`xclip`/`wl-copy` (clipboard), `paplay`/`pw-play`/`afplay` (sounds), `notify-send`/`osascript` (desktop notifications), TPM + tmux-resurrect/tmux-continuum (session persistence)

See SKILL.md for full usage.
