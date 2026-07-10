---
name: tmux-agent-herdr-lite
description: Turns tmux into a coding-agent cockpit with Zellij-like ergonomics and Herdr-like supervision. Use when the user wants to run/supervise coding agents in tmux — invoking this skill installs the tmux integration (keybindings, menu, status bar) and gives the agent a coordination API (launch/read/send/wait/jump, per-agent status detection for Claude Code, Codex, Gemini and more, notifications, worktrees, resume). The human surface is tmux keys and menus only; no new commands for the user to learn.
license: MIT
compatibility: Requires tmux 3.2+, bash, python3, and a POSIX-like shell. Optional clipboard support uses pbcopy, xclip, or wl-copy; optional sounds use paplay, pw-play, or afplay; optional persistence uses TPM with tmux-resurrect/tmux-continuum.
metadata:
  author: dhanesh
  version: "2.1.0"
  tags: "tmux,zellij,herdr,coding-agents,terminal-multiplexer,automation"
---

# Tmux Agent Herdr-Lite

## On invocation — set up now, don't stand by

When this skill fires (slash command or auto-trigger), immediately do this — it is idempotent, so do it every time rather than asking:

1. Run the installer from this skill's directory:

   ```bash
   bash scripts/install.sh
   ```

   It generates `~/.tmux/agent-panes/tmux-agent.conf` pointing at this skill's `scripts/` **in place** (nothing is copied onto PATH), wires a `source-file` line into `~/.tmux.conf` (before any TPM `run` line), wires the optional tmux-resurrect/continuum persistence layer when TPM is present, and reloads a running tmux server. No network needed.

2. Verify the result (see "Verification" below) and report the outcome.

3. Then serve the user's actual request — launching agents, checking status, coordinating panes — using the commands in this skill's `scripts/` directory, invoked by path.

## The two surfaces

- **Humans use tmux only.** Everything is behind keybindings, the `prefix m` menu (launch an agent, jump by status, worktrees, resume, splits), the `prefix ?` dashboard popup, and the status-bar summary. Never tell the user to run `agent-*` commands; point them at keys and the menu.
- **Agents use the scripts.** The `agent-*` commands in `scripts/` are the coordination API (Herdr socket-API equivalents). Invoke them by path from this skill directory (`bash scripts/agent-pane` and siblings). Full reference: `references/commands.md`; recipes: `references/coordination-recipes.md`; what is and isn't ported from Herdr: `references/herdr-parity.md`.

## Keybindings installed (the human surface)

```text
prefix ?        dashboard/help popup
prefix m        command menu — new agent pane, jump, worktree, resume, splits
prefix S        session/window/pane tree
prefix g        jump first blocked agent
prefix E        jump first error (crashed) agent
prefix G        refresh agent statuses
prefix W/i/D    jump working / idle / done agent (idle also matches done;
                prefix I is left untouched for TPM's plugin installer)
prefix | / -    split horizontal / vertical
prefix h/j/k/l  select pane left/down/up/right
prefix H/J/K/L  resize pane left/down/up/right
prefix Tab      last pane
prefix z        zoom pane
prefix Space    cycle layouts
prefix r        reload tmux config
prefix [        copy mode
```

Plus mouse support, vi copy mode, focus events, pane border titles, and the agent summary in the status bar.

## The agent coordination API (scripts/)

- `agent-workspace` — create or attach the `agents` cockpit session.
- `agent-pane [--agent <kind>] [--cwd <dir>] <name> <command...>` — launch and register an agent.
- `agent-list [--json]` — registry with statuses.
- `agent-status-scan` / `agent-status-summary` / `agent-dashboard` — status pipeline.
- `agent-jump <state>` — focus the first pane in a state.
- `agent-explain <target>` — why a pane classified the way it did.
- `agent-read <target> [--lines N] [--source recent]` — read another pane.
- `agent-send <target> <text>` / `--keys Enter` — type (no Enter) or press keys.
- `agent-run <target> <command>` — send command + Enter atomically.
- `agent-wait <target> --status done | --match <regex>` — block on status/output (`--status idle` also matches `done`).
- `agent-notify <name> <status> [msg]` — toast/sound (fired automatically on transitions).
- `agent-resume [--dry-run] [<name> --session-id <id>] [--force]` — relaunch dead agents; native resume.
- `agent-worktree create|open|remove|list <branch>` — per-agent git worktree isolation.
- `agent-cheatsheet` — print the reference.

Runtime state lives under `~/.tmux/agent-panes/`. Targets accept an agent name, window name, or pane id; names are stable, pane ids are not.

## Recommended agent workflow

1. Install + verify (steps above), then create or attach the cockpit: `scripts/agent-workspace`.
2. Launch work through `agent-pane`, not raw `tmux send-keys` — optionally in an isolated worktree (`agent-worktree create <branch> --window`, then `--cwd`).
3. Coordinate instead of watching: `agent-wait tests --status done`, `agent-read reviewer --lines 40`, `agent-send backend --keys Enter`. Recipes: `references/coordination-recipes.md`.
4. Route attention: `agent-jump blocked` (notifications fire on blocked/error/done transitions automatically; the human's `prefix g` does the same).
5. Verify real outcomes separately — a `done` state means the pane looks finished, not that the work is correct. Debug a wrong status with `agent-explain <name>`, never by guessing.
6. After a tmux server restart, recover with `agent-resume` (add `--session-id <id>` to reopen a native CLI session, e.g. `claude --resume`).

## Status model

Statuses are routing hints, not truth:

- `error` — crash or non-zero exit; distinct from `blocked` (a crashed agent needs a look, not an answer).
- `blocked` — a known approval/question UI for the pane's agent, a live interactive prompt (password, y/N) at the pane bottom, or an explicit blocked marker.
- `working` — active output, a spinner title, or the agent's own "esc to interrupt" chrome.
- `idle` — quiescent, or a finished pane already viewed.
- `done` — completion sentinel or finished-agent prompt chrome; focusing the pane demotes it to `idle` (viewed), Herdr-style.
- `unknown` — the pane is gone or metadata is incomplete.

Detection is two-layered, ported from Herdr's manifests: panes with a known agent (`--agent` or inferred from the command) are classified by that agent's screen rules plus the pane title — loose word heuristics are skipped so an agent *discussing* an error is not mislabeled, and unknown prompts fall back to `idle`, never `blocked`. Unrecognized commands use generic tail heuristics where explicit `AGENT_STATUS:` sentinels and real failure signals outrank loose words. Per-agent rules can be replaced by dropping `<agent>.json` into `~/.tmux/agent-panes/detect/` (format in `references/commands.md`). The classifier is pure and unit-tested (`scripts/agent_classify.py`, `assets/test_agent_classify.py`, run by `make gate`).

For best accuracy, instruct coding agents to print explicit markers — they outrank every heuristic:

```text
AGENT_STATUS: working
AGENT_STATUS: blocked reason="needs approval"
AGENT_STATUS: done result="tests passed"
```

## Files in this skill

- `scripts/install.sh` — idempotent installer (generates config referencing scripts in place).
- `scripts/agent-pane`, `scripts/agent-workspace` — launch/register agents, cockpit session.
- `scripts/agent-status-scan`, `scripts/agent-status-summary`, `scripts/agent-dashboard`, `scripts/agent-jump`, `scripts/agent-explain` — status pipeline.
- `scripts/agent-list`, `scripts/agent-read`, `scripts/agent-send`, `scripts/agent-run`, `scripts/agent-wait` — coordination commands.
- `scripts/agent-notify`, `scripts/agent-resume`, `scripts/agent-worktree`, `scripts/agent-menu`, `scripts/agent-cheatsheet` — notifications, persistence, isolation, menus.
- `scripts/agent_classify.py`, `scripts/agent_registry.py` — pure classifier (per-agent manifests) and target resolution.
- `assets/tmux-agent.conf` (template; `@AGENT_BIN@` is substituted at install), `assets/tmux-agent-persistence.conf` — tmux configuration.
- `assets/test_agent_classify.py` — stdlib unit suite (run by `make gate`).
- `references/commands.md`, `references/coordination-recipes.md`, `references/herdr-parity.md` — full CLI reference, recipes, Herdr parity map.

## Common pitfalls

- Do not stand by after the skill loads — run the installer first, every time; it is idempotent.
- Do not tell the human to run `agent-*` commands — point them at `prefix m` / `prefix ?`; the commands are the agent-facing API.
- Do not expect exact Zellij or Herdr behavior — no plugin runtime, no socket event push; `agent-wait` polls. See `references/herdr-parity.md`.
- Do not start agent panes with raw tmux commands if you want status tracking; use `agent-pane` and address targets by name.
- Do not trust `done` as correctness, and do not answer a `blocked` prompt blind — `agent-read` the question first.
- Notifications default to tmux toasts + sound; set `AGENT_NOTIFY=off` or `AGENT_NOTIFY_SOUND=off` in noisy environments.

## Verification

After running the installer:

```bash
tmux -V
grep -n 'agent-panes/tmux-agent.conf' ~/.tmux.conf     # source line present, before any TPM run line
scripts/agent-workspace
scripts/agent-pane smoke 'echo AGENT_STATUS: working; sleep 1; echo ok; sleep 5'
scripts/agent-wait smoke --status done --timeout 30
scripts/agent-explain smoke
scripts/agent-list --json
```

Inside tmux, `prefix ?` opens the dashboard popup, `prefix m` opens the menu with "New agent pane", and the status bar shows the fleet summary. The skill's unit suite passes via `make gate-skill SKILL=tmux-agent-herdr-lite`.
