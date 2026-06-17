# Architecture — the bounded scored cache

`context_ledger.py` is the whole mechanism: a `ContextLedger` = **bounded scored cache + tiered persistence**. Everything else (hooks, harvester, optimizer) feeds or flushes it.

## Card

```
Card = {id, kind, content, provenance, pinned, access_count, last_accessed, created, tokens}
```

- `id = sha1(kind + normalized content)[:16]` → dedupe + idempotent upsert. Re-ingesting the same fact bumps `access_count` and recency instead of duplicating.
- `kind` ∈ `decision | constraint | open_question | task_state | file_ref | fact | note`.
- `provenance` ∈ `trusted | untrusted`. Drives the `<data>` fencing in the digest.
- `last_accessed` / `created` use a **logical clock** (tick per ingest), not wall-clock — so curation is deterministic and reproducible (this is also why the tests are stable).

## Tiers

| Tier | What | Size |
|---|---|---|
| **HOT** | the in-window set kept under the token budget | ≤ `B` tokens |
| **COLD** | every card ever seen, persisted to `.context/ledger.json` | capped at `cold_cap` (default 5000), lowest all-time salience evicted |
| **DIGEST** | markdown rendering loaded at session start instead of raw history | O(budget) |

## Scoring

```
score(card) = w_recency·e^(−λ·age)
            + w_freq·log(1+access_count)
            + w_sim·jaccard(card_tokens, anchor_tokens)   # relevance to current focus
            + w_kind·kind_prior                            # a decision outranks a note
            − w_size·(tokens/100)                          # favor dense, cheap-to-keep cards
```

The `anchor` is one line of current task focus (`.context/anchor.txt`), re-read every curate so ranking tracks the work (drift defense). Default weights live in `ContextLedger.DEFAULT_WEIGHTS`; `optimize_weights.py` tunes them.

## Eviction — greedy knapsack with a hard cap

`curate(budget, anchor)` is two passes:

1. **Pins first** (rot-proof priority), ranked by recency then frequency. A pin is kept only if it fits; if pins alone exceed `B`, the lowest-priority pins **spill to cold** and `pins_over_budget` is raised.
2. **Non-pins** fill the remainder by **value-density** (`score / tokens`) — the fractional-knapsack greedy choice.

A hard `assert hot_tokens <= B` closes the pass. The budget is never silently exceeded — that is the anti-bloat guarantee, and the reason pins do *not* get an unconditional pass (the bug this kit's tests pin down).

### Anti-rot rendering
`to_digest()` renders the lossless kinds (`decision`, `constraint`, `open_question`, `task_state`, `file_ref`) **verbatim**, each in its own section; only low-salience `fact`/`note` is compactable. Untrusted-provenance content is fenced in `<data>…</data>`.

## Complexity — the "until optimal" ceiling

| op | cost | why |
|---|---|---|
| `ingest` | O(1) amortized | dict upsert |
| `curate` | O(N log N) | score all + sort + greedy fill |
| `to_digest` | O(K), K ≤ budget | only kept cards rendered |
| **steady state** | **O(1)/turn amortized, O(budget) tokens** | N held bounded by eviction |

Measured ~9.6 ms to curate 5 000 cards. There is no asymptotically better structure than a bounded scored cache with tiered persistence — which is exactly why the optimizer (`optimize_weights.py`) has a real ceiling to converge to and **halts** rather than tuning forever.

## CLI

```bash
python3 context_ledger.py ingest decision "Use X because Y" --pinned
python3 context_ledger.py ingest fact "$(some_tool)" --provenance untrusted
python3 context_ledger.py curate --budget 8000 --anchor "$(cat .context/anchor.txt)"
python3 context_ledger.py stats
```
