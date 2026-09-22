# install_jev_setup.py parameters

| Flag | Default | Meaning |
|---|---|---|
| `--targets` | `claude,agents,codex,gemini` | Comma list of targets to manage |
| `--mode` | `block` | `block`: every target gets the Jev block. `mirror`: `claude` gets the block; the other targets get a managed copy of the whole CLAUDE.md with `@imports` inlined |
| `--claude-source PATH` | the `claude` target | CLAUDE.md to mirror from (e.g. a staged copy you have not installed yet) |
| `--home DIR` | `$HOME` | Home directory to install into (tests and evals use a temp home) |
| `--bin-dir DIR` | `<home>/.local/bin` | Where the `jev` CLI goes |
| `--no-cli` | off | Manage instruction files only |
| `--dry-run` | off | Print `WOULD-WRITE` / `UNCHANGED` lines; write nothing |
| `--check` | off | Print `OK` / `STALE` / `MISSING` per target; exit 1 on any drift |
| `--uninstall` | off | Remove managed blocks (and files left empty), and the CLI if unmodified |

## Target paths

| Target | Path | Read by |
|---|---|---|
| `claude` | `~/.claude/CLAUDE.md` | Claude Code |
| `agents` | `~/.agents/AGENTS.md` | agents that follow the `~/.agents` convention; the canonical mirror |
| `codex` | `$CODEX_HOME/AGENTS.md` (default `~/.codex`) | OpenAI Codex CLI |
| `gemini` | `~/.gemini/GEMINI.md` | Gemini CLI |

## Exit codes

`0` success · `1` `--check` found drift · `2` usage error · `3` malformed markers in a
target (nothing written).

## Mirror caveats

An `@import` that cannot be resolved becomes a visible `<!-- unresolved import: … -->`
comment rather than disappearing silently. After changing CLAUDE.md, re-run the installer in
mirror mode; `--check` reports the mirrors as `STALE` until you do.
