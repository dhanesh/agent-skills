# Command reference

All commands install to `~/.local/bin` and store state under
`~/.tmux/agent-panes/` (override root with `AGENT_TMUX_ROOT`, session name
with `AGENT_TMUX_SESSION`, idle threshold with `AGENT_IDLE_SECONDS`).
Targets accept an agent name, a window name, or a tmux pane id (`%3` or `3`).

## Launch and registry

### `agent-workspace`
Create or attach the `agents` cockpit session (windows: `control`, `scratch`).

### `agent-pane [--agent <kind>] [--cwd <dir>] <name> <command...>`
Launch a command in a new registered window. Writes a JSON record under
`panes/` (name, window, pane id, command, agent kind, cwd, status) that every
other command resolves targets through.

- `--agent` — detection manifest to use: `claude`, `codex`, `gemini`,
  `opencode` (also `kilo`), `amp`, `cursor`, `copilot`, `droid`, `cline`,
  `devin`, `kimi`, `kiro`, `grok`, `hermes`, `qodercli`, `antigravity`, `pi`.
  Inferred from the command word when omitted (wrapper tokens like
  `env`/`npx` are skipped).
- `--cwd` — start directory (pair with `agent-worktree` for isolation).

### `agent-list [--json] [--no-scan]`
Print the registry with statuses. `--json` emits `{"agents": [...]}` for
machine consumption; `--no-scan` skips the opportunistic rescan.

## Status

### `agent-status-scan`
Capture every registered pane (`capture-pane` + `#{pane_title}`), classify it,
persist per-pane records and `state.json`, and fire `agent-notify` on
transitions into `blocked`/`error`/`done`. A `done` pane that is focused in an
attached client demotes to `idle` (viewed), Herdr-style.

### `agent-status-summary`
One-line per-agent summary for the tmux status bar.

### `agent-dashboard`
Full table (status, name, agent kind, pane, window, classification reason)
plus key help. Bound to `prefix ?` as a popup.

### `agent-explain <target>`
Debug classification: prints the agent manifest in use, the pane title, the
recorded status/reason, a fresh classification of the current screen, and the
detection-region tail. Port of `herdr agent explain`.

### Detection manifest overrides

Drop `<agent>.json` into `~/.tmux/agent-panes/detect/` to replace that
agent's built-in rules (the equivalent of Herdr's local
`agent-detection/<agent>.toml` overrides — local always wins). Each file is a
JSON list of rules; the highest-priority match wins:

```json
[{"id": "my_rule", "state": "blocked", "priority": 500,
  "all":  [["contains", "custom approval text"]],
  "any":  [["line", "^\\s*❯"]],
  "none": [["contains", "esc to interrupt"]]}]
```

Matcher kinds: `contains` (substring, lowercased region), `regex`, `line`
(regex per line), `title` / `title_regex` (pane title). States: `blocked`,
`working`, `idle`. Broken files are ignored. Verify with `agent-explain`.

### `agent-jump blocked|error|working|idle|done`
Focus the first pane in that state.

## Coordination (Herdr socket-API equivalents)

### `agent-read <target> [--lines N] [--source visible|recent]`
Print a pane's terminal contents. `visible` = rendered screen; `recent` adds
~2000 lines of scrollback (joined/unwrapped lines).

### `agent-send <target> <text...>` / `agent-send <target> --keys <key...>`
Type literal text into a pane **without** pressing Enter, or send tmux key
names (`Enter`, `C-c`, `Escape`, `Up`). Use text mode to answer prompts
precisely.

### `agent-run <target> <command...>`
Send a command line and press Enter atomically. Preferred for commands.

### `agent-wait <target> --status <s> | --match <regex> [--timeout S] [--interval S] [--source visible|recent]`
Block until the agent reaches a status or its output matches an extended
regex. Exit 0 on success, 1 on timeout (default 300s; also settable via
`AGENT_WAIT_TIMEOUT`/`AGENT_WAIT_INTERVAL`).

## Persistence and isolation

### `agent-resume [--dry-run]` / `agent-resume <name> --session-id <id>`
Relaunch registered agents whose panes no longer exist (after a tmux server
restart), in their saved cwd with their saved agent kind. With
`--session-id`, rewrite the command using the agent's native resume flag
(Herdr's resume table): `claude --resume <id>`, `codex resume <id>`,
`cursor-agent --resume <id>`, `copilot --resume=<id>`, `droid --resume <id>`,
`opencode --session <id>`, `gemini --resume <id>`.

### `agent-worktree create|open|remove|list <branch> [--repo <path>] [--base <ref>] [--force] [--window]`
Git-worktree isolation per agent. Checkouts live under
`$AGENT_WORKTREE_DIR` (default `~/.tmux/agent-panes/worktrees/<repo>/<branch>`).
`create` reuses an existing branch or branches from `--base`; `--window` opens
a cockpit window in the checkout. `remove` runs `git worktree remove` and
never deletes the branch (`--force` for dirty checkouts).

## Notifications

### `agent-notify <name> <status> [message]`
Toast + sound for a status transition (called by the scanner; scriptable like
`herdr notification show`). Skips when the pane is focused. Config:

- `AGENT_NOTIFY=tmux|system|off` — `tmux` shows a status-line message;
  `system` adds `notify-send`/`osascript`; `off` silences everything.
- `AGENT_NOTIFY_SOUND=on|off` — plays a system sound via
  `paplay`/`pw-play`/`afplay` (double cue for `blocked`/`error`, single for
  `done`), falling back to the terminal bell.

## Help

### `agent-cheatsheet`
Print the command/key reference above in terminal form.
