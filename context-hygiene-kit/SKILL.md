---
name: context-hygiene-kit
description: >-
  Installs a bounded, scored, tiered context cache that keeps a Claude Code project's working
  memory lean (anti-bloat) and rot-proof across compactions (anti-rot). Drops in a stdlib-only
  Python ledger, a deterministic per-turn transcript harvester, and three lifecycle hooks
  (Stop / PreCompact / SessionStart) wired via .claude/settings.json — so a digest of ranked,
  verbatim-preserved decisions loads at session start instead of replaying raw history, and
  durable facts survive an abrupt session close (crash, kill -9, closed terminal). Use when a
  long agent session loses important decisions after compaction, when context keeps growing
  until responses degrade, when you want durable rot-proof working memory per project, or when
  you want to replace a lossy local-model session-summariser with a deterministic lossless one.
  Not for ordinary app caching, RAG vector stores, or LLM prompt-caching config.
---

# Context Hygiene Kit

Two failure modes of long agent sessions, one mechanism:

| Failure | What it is | Fix |
|---|---|---|
| **Bloat** | context grows unbounded as a session runs until responses degrade | a **token-budget-bounded scored cache** — eviction caps the window forever |
| **Rot** | compaction summarizes away the *specific* facts that mattered (decisions, constraints, file:line refs) and keeps vague narrative | **lossless verbatim preservation** of high-salience kinds into a durable digest, refreshed *before* compaction |

The whole kit is one data structure — a `ContextLedger` (bounded scored cache + tiered persistence) — plus the hooks that feed and flush it. It is itself a **self-prompting loop** (autonomous family); the 10-slot loop spec lives in `references/loop-spec.md`. The deep design rationale is in `references/architecture.md`; the capture rules and trust boundary are in `references/capture.md`. Read those when you need detail — the summary below is enough to install and operate it.

## Install

Run the installer against the target project (defaults to the current directory):

```bash
scripts/install.sh /path/to/project
```

It is idempotent and does five things: copies the core files (`context_ledger.py`, `harvest.py`, `optimize_weights.py`, `curate_loop.md`, `test_context_ledger.py`) to the project root and the hooks to `hooks/`; **additively** merges three hooks into `.claude/settings.json` (existing hooks are preserved); gitignores the runtime `.context/` cache; seeds `.context/anchor.txt`; and **runs the 16-test suite as an install gate** — the kit's guarantees are only real if those pass. Tell the user to restart Claude Code so the new hooks load.

Requires `python3` (stdlib only — no pip installs) and, for clean settings merging, `jq` (falls back to writing `.claude/settings.hooks.json` for manual merge).

## How it works — three cadences

The hooks give three layers of protection (see `references/loop-spec.md` LSC-9):

| Hook | Fires | Job |
|---|---|---|
| **Stop** → `hooks/stop.sh` | **every turn** | run `harvest.py`: deterministically capture durable facts from the live transcript + flush the ledger to disk — the **abrupt-close durability** layer |
| **PreCompact** → `hooks/precompact.sh` | before compaction | refresh the lossless digest so decisions survive the summarizer (**anti-rot**) |
| **SessionStart** → `hooks/session_start.sh` | session start/resume | inject the digest as `additionalContext` instead of raw history (**anti-bloat**) |

### What gets captured automatically (`harvest.py`, no model)
Only **high-precision, deterministic** signals — anything fuzzier would re-introduce rot:

| Source | Captured as |
|---|---|
| latest user request | `task_state` snapshot (1 per turn) |
| line starting `DECISION:` `CONSTRAINT:` `QUESTION:`/`OPEN:` `TASK:`/`TODO:` `NOTE:` `FILE:` | mapped lossless kind |
| `file.ext:line` references in assistant text | `file_ref` |

To **deliberately** persist a fact, write a marker line (e.g. `DECISION: chose X because Y`) in a turn — the Stop hook harvests it. This is the lowest-friction path; prefer it. The full convention is in `references/capture.md`.

## The three non-negotiables (do not weaken these)

1. **The token budget is a HARD cap (anti-bloat).** `curate()` asserts `hot_tokens <= B`. Pinned cards get *first claim* on the budget but cannot overflow it — excess pins spill to cold and raise `pins_over_budget` (an LSC-8 human-gate signal), so anti-bloat is never silently traded for anti-rot.
2. **Two-channel boundary (LSC-7).** The **load-bearing** prompt-injection control is harvest-side: only the **trusted channel** (user + assistant text) is ingested; `tool_result`/`tool_use` blocks are never harvested and markers must start the line, so untrusted text cannot smuggle one. As a **secondary, best-effort** layer, any untrusted card content that is ranked in is rendered inside `<data>…</data>` (OWASP LLM01 "segregate/denote external content") with embedded fence tokens neutralized so it can't break out — the curator *ranks* card content, never executes it. The `<data>` fence is a soft delimiter, **not** a complete boundary: if untrusted content must ever reach a tool-capable downstream model, prefer a dual-LLM/quarantine pattern over relying on the fence.
3. **Deterministic capture only.** No model summarises the session. The harvester extracts verbatim signals. If you are tempted to add free-prose "decision extraction", don't — getting it wrong is rot. That is the explicit reason this kit replaces local-model session-summarisers.

## Operating it

- **Optional deep loop:** `/loop curate_loop.md` re-curates each round with a visible `STATUS: DONE` flag and a hard BACKSTOP (round/token/oscillation/error caps). Use when idle; the trusted control prompt is `curate_loop.md`.
- **Tune scoring weights (bounded, converges, halts):** `python3 optimize_weights.py`.
- **Inspect state:** `python3 context_ledger.py stats` · `cat .context/digest.md`.
- **Replacing a model-based auto-summariser?** Remove its `Stop`/`SessionEnd` hooks from settings first (back the file up), then install this kit — otherwise both write to `.context/`. The kit's deterministic harvester is the lossless replacement.

## Verifying after install

Always confirm the gate passed: `python3 test_context_ledger.py` (16 tests — budget invariant, pin spill, rot survival, two-channel fencing, idempotent ingest, harvester capture + injection boundary). If any fail, the guarantees above do not hold — fix before relying on the kit.
