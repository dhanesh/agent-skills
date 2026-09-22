# jev-agent-setup

Sets up a machine so that **every coding agent on it** — Claude Code, Codex, Gemini CLI, and
anything that reads `~/.agents/AGENTS.md` — hands its System One decisions (rank, classify,
yes/no) to TypeSafe's **Jev**, and can do so from a shell mid-task.

## Install

```bash
npx skills add dhanesh/agent-skills --skill jev-agent-setup
```

## Usage

Ask your agent to "set up Jev for my agents", or run the installer yourself:

```bash
python3 <skill-dir>/assets/install_jev_setup.py --dry-run          # show the plan
python3 <skill-dir>/assets/install_jev_setup.py                    # Jev block into all four files
python3 <skill-dir>/assets/install_jev_setup.py --mode mirror      # other agents get a full copy of CLAUDE.md
python3 <skill-dir>/assets/install_jev_setup.py --check            # drift report, exit 1 on drift
python3 <skill-dir>/assets/install_jev_setup.py --uninstall
```

## What it installs

| Piece | Where | Notes |
|---|---|---|
| `jev` CLI | `~/.local/bin/jev` | PEP 723 script run by `uv`; JSON in → typed answers out. Exit 2 = bad input or no key, 3 = unavailable (timeout/429/5xx, retryable), 4 = rejected by TypeSafe (400/401/403/404/422, fix the request). Any non-zero exit is "no verdict" |
| Jev instruction block (BCP 14) | `~/.claude/CLAUDE.md`, `~/.agents/AGENTS.md`, `$CODEX_HOME/AGENTS.md`, `~/.gemini/GEMINI.md` | Between `BEGIN/END jev-agent-setup` markers; content outside them is never touched; the first modification backs the file up to `*.bak-jev-agent-setup` |
| Per-project call log | `~/.local/state/jev/projects/<repo>-<hash>.jsonl` (0600) | Written by `jev` on every call: request state, questions, answers or error (with HTTP status, `error_kind`, `retryable`, TypeSafe `request_id`), exit, latency. Keyed by git root; kept outside the repo so it can't be committed. `JEV_LOG=0` disables; `JEV_LOG_DIR` relocates |
| Key placeholder | `~/.config/typesafe/env` (0600) | Created only if missing. The installer never prints or writes a real key |

Re-running is idempotent. Malformed markers (a BEGIN without an END, or duplicates) make the
installer refuse with exit 3 before writing anything.

## Reviewing Jev calls in the terminal

```bash
jev log                 # last 20 calls for this project (git root of cwd), with a summary line
jev log -v              # plus question text and the state that was sent
jev log -f              # follow live while an agent works
jev log --failed        # only calls that returned no verdict
jev log --json -n 0 | jq .   # raw JSONL, all records
jev log --projects      # every project with a log
```

Each record shows Noul as a probability bar, Choice as label + confidence, and Score as
value/max + confidence. The log contains exactly what was sent to TypeSafe. It is 0600 and
never rotated, so prune it the way you would any payload archive.

## How this differs from the `typesafe-ai` skill

They are complementary: `typesafe-ai` teaches an agent **how to build with Jev**;
`jev-agent-setup` makes agents **use Jev on their own decisions**, and makes that setup
reproducible across machines.

| | `typesafe-ai` (TypeSafe's skill) | `jev-agent-setup` (this skill) |
|---|---|---|
| Question it answers | "How do I design Jev judgments into my application?" | "How do I make every agent on this machine route its own decisions through Jev?" |
| Fires when | A feature needs programmable common sense (routing, ranking, extraction, verification) | Setting up a new machine, adding an agent, or checking the setup for drift |
| Consumer of Jev | The software being built | The agent's own working cycle (planning, triage, gating, review) *and* the software |
| Ships | Guidance and pointers to live docs and cookbooks; no code | An installer, the `jev` CLI, and a standing BCP 14 policy block |
| Scope of effect | One task, while the skill is loaded | Persistent: global instruction files for Claude, Codex, Gemini and `AGENTS.md` readers |
| Policy | Design advice (state, criteria, confidence) | Normative rules: which decisions MUST go to Jev, failure = "no verdict", uncertainty bands, data guardrails |
| Agent coverage | Agents that load skills | Any agent that reads a global markdown instruction file |

The installed block tells agents to load `typesafe-ai` and read the live docs before writing
Jev integrations, so install both.

## Requirements

`python3` (stdlib only) for the installer; `uv` and network access when `jev` runs; a
TypeSafe API key from https://console.typesafe.ai/.

## Caveats

- Every offloaded decision is a network round trip and a billable call. The rules exempt
  decisions already fixed by an exact rule or user instruction; keep it that way.
- Agent-cycle state (commands, paths, diffs) leaves the machine. The block forbids sending
  regulated customer data; confirm your agreement with TypeSafe covers the rest.

## Layout

- `SKILL.md` — the agent-facing prompt.
- `assets/install_jev_setup.py` — the installer; `assets/jev` — the CLI;
  `assets/jev-instructions.md` — the managed block.
- `assets/test_install_jev_setup.py` — stdlib test suite.
- `eval/run_eval.py` — deterministic outcome eval (see the repo's docs/eval-standard.md).
