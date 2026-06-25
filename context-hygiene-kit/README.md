# context-hygiene-kit

An Agent Skill that installs a **bounded, scored, tiered context cache** into a Claude Code project, keeping its working memory **lean** (anti-bloat) and **rot-proof across compactions** (anti-rot). It drops in a stdlib-only Python ledger, a deterministic per-turn transcript harvester, and three lifecycle hooks — so a digest of ranked, verbatim-preserved decisions loads at session start instead of replaying raw history, and durable facts survive an abrupt session close.

> This README is for humans browsing the folder. The agent-facing instructions live in [`SKILL.md`](./SKILL.md) — that's what Claude reads when the skill triggers.

## What it produces

Installed into a target project, the kit gives you:

1. **A bounded scored ledger** (`context_ledger.py`) — a token-budget-capped cache; `curate()` is a hard-capped greedy knapsack. Eviction caps the window forever (no bloat); high-salience kinds (decisions, constraints, file:line refs) are preserved **verbatim** (no rot).
2. **Deterministic per-turn capture** (`harvest.py`) — no model. Captures the latest request, `MARKER:` lines, and `file:line` refs from the **trusted channel only**, so an abrupt close costs at most one in-flight turn.
3. **Three lifecycle hooks**, merged additively into `.claude/settings.json`: `Stop` (capture + flush), `PreCompact` (anti-rot curate), `SessionStart` (anti-bloat digest load).
4. **An install gate** — the 17-test suite runs on install; the guarantees only ship if it's green.

## Two failure modes, one mechanism

| Failure | What it is | Fix |
|---|---|---|
| **Bloat** | context grows unbounded until responses degrade | a token-budget-bounded scored cache — eviction caps the window forever |
| **Rot** | compaction summarizes away the *specific* facts that mattered | lossless verbatim preservation of high-salience kinds, refreshed *before* compaction |

## When it triggers

Installing durable per-project working memory; a long session that loses decisions after compaction; context that grows until quality drops; or **replacing a lossy local-model session-summariser** with a deterministic lossless one.

It deliberately does **not** trigger for ordinary application caching, RAG / vector stores, or LLM prompt-caching configuration.

## Install & use

```bash
npx skills add dhanesh/agent-skills --skill context-hygiene-kit
```

Then run the installer **once** — pick project scope (just this repo) or global scope (every project). Both are idempotent and gate on the test suite:

```bash
scripts/install.sh /path/to/project   # PROJECT — this repo only (default: cwd)
scripts/install.sh --global           # GLOBAL  — all projects, hooks in ~/.claude
# restart Claude Code so the hooks load
```

A global install puts the scripts + hooks in `~/.claude/context-hygiene/` and registers them in `~/.claude/settings.json`, so every project is covered with no per-repo setup. **Memory stays per-project either way** — each repo gets its own `.context/`, created automatically in its working dir on the first turn, so memories never bleed between unrelated projects. (Global installs don't touch per-repo `.gitignore`; add `.context/` yourself.)

After setup the three hooks run automatically every turn — you don't re-invoke the skill. Requires `python3` (stdlib only — no pip installs) and `jq` for clean settings merging (falls back to a manual-merge file without it).

## How it works — three cadences

| Hook | Fires | Job |
|---|---|---|
| **Stop** | every turn | harvest durable facts + flush ledger — abrupt-close durability |
| **PreCompact** | before compaction | refresh the lossless digest (anti-rot) |
| **SessionStart** | session start/resume | inject the digest instead of raw history (anti-bloat) |

Tag a fact inline to persist it deliberately: a line starting `DECISION:` / `CONSTRAINT:` / `QUESTION:` / `TASK:` / `FILE:` / `NOTE:` is auto-harvested. Only the trusted user/assistant text channel is captured — `tool_result` blocks are excluded, closing the prompt-injection surface.

## Structure

```
context-hygiene-kit/
├── SKILL.md                      install workflow + how it works + the three non-negotiables
├── references/
│   ├── architecture.md           the bounded scored cache: Card, tiers, scoring, eviction, complexity
│   ├── loop-spec.md              the 10-slot self-prompting-loop spec (LSC-1..10) + sanity pass
│   └── capture.md                the deterministic harvester: capture rules, markers, trust boundary
├── assets/
│   ├── context_ledger.py         the ledger (bounded scored cache + tiers + CLI)
│   ├── harvest.py                deterministic transcript -> ledger capture
│   ├── optimize_weights.py       bounded coordinate-ascent weight tuner (converges + halts)
│   ├── curate_loop.md            trusted /loop control prompt (DONE flag + hard BACKSTOP)
│   ├── test_context_ledger.py    17-test suite (the install gate)
│   ├── schemas/ledger.schema.json   the ledger data contract
│   ├── hooks/{stop,precompact,session_start}.sh
│   └── settings.hooks.json       the hooks block install.sh merges into settings.json
├── scripts/install.sh            idempotent installer (copy + jq-merge + gitignore + test gate)
└── README.md                     this file
```

## The three non-negotiables

1. **The token budget is a HARD cap.** `curate()` asserts `hot_tokens <= B`. Pins get *first claim* but cannot overflow — excess pins spill to cold and raise `pins_over_budget` (a human-gate signal). Anti-bloat is never silently traded for anti-rot.
2. **Two-channel boundary (LSC-7).** Only the trusted channel is harvested; untrusted content is `<data>`-fenced and ranked, never executed. The prompt-injection defense.
3. **Deterministic capture only.** No model summarises the session — verbatim signals only. Fuzzy decision-extraction is rot; this kit exists to replace it.

## Provenance & validation

Extracted from the `context_bloat_rot` reference implementation. Every guarantee is backed by `test_context_ledger.py` (17 tests, stdlib-only, deterministic), run as an install gate:

- **Budget invariant:** 5 011 cards → `hot_tokens ≤ budget`; pins alone cannot exceed the budget (they spill + flag).
- **Rot prevention:** a pinned decision survives 5 000 newer cards; lossless kinds render verbatim.
- **Two-channel:** untrusted content `<data>`-fenced; a marker smuggled in a `tool_result` is not captured.
- **Capture:** markers + file:line refs + latest-request snapshot; idempotent across re-runs.
- **Optimizer:** bounded coordinate-ascent converges and halts, never worse than default weights.
