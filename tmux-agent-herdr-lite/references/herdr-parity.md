# Herdr parity map

What this skill ports from [Herdr](https://github.com/ogulcancelik/herdr)
(agent multiplexer, Rust TUI), what tmux substitutes, and what is out of
scope for a shell-script layer. Audited against Herdr's docs and source
(`src/detect/manifests/*.toml`, `src/agent_resume.rs`, `src/api/schema.rs`)
in July 2026.

## Ported

| Herdr feature | This skill |
|---|---|
| Agent status model (`blocked/working/done/idle/unknown`) | Same states plus `error` (crash surfaced separately) |
| `done` = finished-and-unviewed; viewing demotes to `idle` | Scanner demotes a focused `done` pane to `idle` (viewed-hash pinned) |
| Per-agent screen detection manifests (claude, codex, gemini, opencode/kilo, amp, cursor, copilot, droid, cline) | `AGENT_MANIFESTS` in `agent_classify.py`, patterns ported from `src/detect/manifests/*.toml` (priority rules, contains/regex/line matchers) |
| Strict blocked detection: only known approval UI blocks; unknown prompts → idle | Known-agent panes skip generic word heuristics entirely |
| OSC-title evidence (braille spinner = working, `Action Required` = blocked) | Scanner passes tmux `#{pane_title}` into the classifier |
| `herdr agent explain` | `agent-explain` (inputs, matched rule, detection tail) |
| Foreground-process agent identification | `infer_agent()` on the launch command (wrapper-token unwrapping); `--agent` overrides |
| Socket API: `agent.start` | `agent-pane` |
| `agent.list` / `agent.get` | `agent-list [--json]` |
| `agent.read` / `pane.read` (visible/recent sources) | `agent-read --source visible|recent` |
| `pane.send_text` / `pane.send_keys` / `pane run` | `agent-send`, `agent-send --keys`, `agent-run` |
| `agent wait --status` / `wait output --match` | `agent-wait --status|--match` (polling) |
| Notifications: request/done sounds, toasts, active-tab suppression | `agent-notify` (tmux/system toasts, sound with bell fallback, double cue for needs-attention, focused-pane suppression) |
| `notification.show` scripting | `agent-notify <name> <status> [message]` |
| Session persistence (server survives client detach) | Native tmux; optional tmux-resurrect/continuum layer for server restarts (`assets/tmux-agent-persistence.conf`) |
| `resume_agents_on_restore` + per-agent resume argv table | `agent-resume` (`claude --resume`, `codex resume`, `cursor-agent --resume`, `copilot --resume=`, `droid --resume`, `opencode --session`, `gemini --resume`) |
| Worktree CLI (create/open/remove, branch kept on remove, dirty needs `--force`) | `agent-worktree`, same semantics, checkouts under `~/.tmux/agent-panes/worktrees/<repo>/<branch>` |
| Keyboard: prefix-driven panes/tabs, jump-to-agent, help panel, copy mode, mouse | `assets/tmux-agent.conf` (+ `prefix Tab` last-pane, `prefix ?` dashboard, `prefix m` menu with worktree/resume entries) |
| Remote/SSH reattach | Native tmux (`ssh host tmux attach`) |

## Approximated (different mechanism, same effect)

- **Event push vs polling.** Herdr pushes `pane.agent_status_changed` over a
  socket; this skill polls (`agent-wait`, status-bar refresh every 5s).
  Polling costs latency, not capability, at cockpit scale.
- **Detection cadence.** Herdr classifies every 300ms with debounce windows;
  the scanner runs on demand and from the status bar. The hash/idle-threshold
  logic covers the same transitions more coarsely.
- **Lifecycle hooks.** Herdr installs hook integrations into agent CLIs
  (authoritative state reports). The equivalent here is the cooperative
  `AGENT_STATUS:` sentinel protocol, which any agent can print and which
  outranks screen heuristics.

## Deliberately out of scope

- **Plugin runtime and marketplace** (`herdr plugin ...`, herdr.dev/plugins):
  tmux has no comparable in-pane plugin surface; TPM plugins (resurrect,
  continuum) cover the persistence use case this skill needs.
- **Layout export/apply as JSON BSP trees**: tmux layouts + resurrect handle
  save/restore; a bespoke format would duplicate them.
- **Live server handoff on upgrade** (`herdr update --handoff`), remote
  thin-client binary bootstrap, Windows named pipes, Kitty graphics, CJK IME
  cursor handling, themes: TUI-runtime concerns with no tmux-script analog.
- **Semantic pane metadata API** (`pane.report_metadata`, custom status
  labels, TTLs): the JSON registry carries name/agent/status/reason; more
  would be speculative.

When one of the out-of-scope features matters, run Herdr itself — this skill
keeps tmux as the substrate and is explicitly the "lite" tier.
