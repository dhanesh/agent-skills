## Loop: keep Claude Code's working context lean and rot-proof across compactions

**Family: Autonomous** (an agent driving *itself* toward a maintenance goal — a bounded
lean context) **with a self-refinement graft** (the "optimize until optimal" weight-tuner
is a bounded draft→evaluate→revise loop). Dominant shape is autonomous; the optimizer is a
sub-loop that converges and halts.

### Spec
```
LSC-1  Goal / success:    Hot context window stays <= B tokens for any session length
                          (no BLOAT) AND every pinned/lossless card present before a
                          compaction is still present after it (no ROT). Checkable:
                          `hot_tokens <= B` and pinned/lossless set is a superset
                          across the compaction boundary.
LSC-2  Stop condition:    Model emits `STATUS: DONE` when utilization<=1.0, the
                          lossless set is preserved, and `no_progress==true` for 2
                          consecutive rounds. A flag the harness reads — never inferred
                          from prose. (curate_loop.md step 5)
LSC-3  Backstop:          HARD CAP, fires regardless of the model:
                          round>=25  OR  curator output >=200k tok  OR  same digest
                          hash 3 rounds (oscillation)  OR  2 consecutive command errors.
                          Safe state = stopped; no auto-retry. (curate_loop.md BACKSTOP)
                          Optimizer sub-loop has its own: MAX_ROUNDS=12.
LSC-4  State-passing:     `.context/ledger.json` (the bounded scored cache) + the rendered
                          `.context/digest.md` + `.context/anchor.txt` (current focus).
                          The MINIMUM carried forward: ranked cards, not raw transcript.
                          Digest hash carried round-to-round to detect repetition.
LSC-5  Self-evaluation:   Per-round JSON {hot_tokens,utilization,n_cards,evicted,
                          no_progress}. no_progress detector = utilization+card count+
                          digest hash all unchanged. Optimizer uses retention-gain<EPSILON.
LSC-6  Guardrail:         Budget invariant enforced IN code before any digest is written:
                          curate() never returns hot_tokens>B — a hard `assert used<=B`,
                          asserted in test_context_ledger.py. Pins get first claim on the
                          budget but do NOT bypass it; if pins alone exceed B the lowest
                          spill to cold and `pins_over_budget` is raised (-> LSC-8 gate),
                          so anti-bloat is never silently traded for anti-rot.
                          Schema-validated ledger.
LSC-7  Two-channel:       trusted = curate_loop.md scaffold + your own decisions/constraints.
                          untrusted = tool output, web text, file contents, other agents.
                          Wrapping = untrusted cards carry provenance='untrusted' and are
                          rendered inside `<data>…</data>` in the digest; the curator only
                          RANKS card content, never executes it.
LSC-8  Human gate:        N/A — OUTPUT-ONLY loop (writes .context/* only; no deletes,
                          deploys, posts, spends). One exception gated via AskUserQuestion:
                          a cold-prune that would drop a pinned card (should never happen).
LSC-9  Cost & cadence:    Hot budget B (default 8000 tok). THREE cadences:
                          (a) CAPTURE — Stop hook every turn runs harvest.py: deterministic
                              transcript->ledger + flush. ~10-50ms stdlib, unthrottled. This
                              is the abrupt-close durability layer (SessionEnd misses kill -9).
                          (b) CURATE — PreCompact hook (anti-rot) + SessionStart load (anti-bloat).
                          (c) DEEP — optional `/loop` heartbeat at 1200–1800s (avoids the
                              ~5-min prompt-cache TTL cliff at 300s).
                          Per-round curator cost ~O(N log N), measured 9.6ms @ 5k cards.
LSC-10 Failure handling:  oscillation -> digest-hash-repeat backstop;
                          drift -> anchor re-rank each round + recency decay demotes stale;
                          premature-stop -> DONE needs 2 stable rounds + lossless-preserved;
                          runaway -> round/token backstop; injection -> <data> wrapping;
                          bloat -> hard budget in curate(); rot -> lossless kinds verbatim.
```

### Scaffold (Claude Code primitives)

**1. Lifecycle hooks** (`settings.json`) — the always-on, event-driven layer:
```jsonc
{
  "hooks": {
    "Stop":         [{ "hooks": [{ "type": "command",
      "command": "$CLAUDE_PROJECT_DIR/hooks/stop.sh" }] }],         // CAPTURE every turn: harvest + flush (abrupt-close safe)
    "PreCompact":   [{ "hooks": [{ "type": "command",
      "command": "$CLAUDE_PROJECT_DIR/hooks/precompact.sh" }] }],   // anti-ROT: curate before summarize
    "SessionStart": [{ "hooks": [{ "type": "command",
      "command": "$CLAUDE_PROJECT_DIR/hooks/session_start.sh" }] }] // anti-BLOAT: load digest, not history
  }
}
```

**2. Recurring hygiene loop** — self-paced, with the backstop visible:
```
/loop   curate_loop.md          # omit interval -> model self-paces each round
```
`curate_loop.md` is the TRUSTED control channel; it reads the ledger as DATA, ingests
high-value cards, refreshes the digest, emits a JSON self-eval, and STOPS on the DONE
flag or the hard BACKSTOP (round>=25 / 200k tok / digest-hash repeat / repeated error).

**3. Self-paced cadence** when running idle between user turns:
```
ScheduleWakeup(delaySeconds=1500, prompt="curate_loop.md",
               reason="idle context-hygiene heartbeat")   // 1200–1800s, not 300s
```

**4. Optimization sub-loop** — bounded, converges, halts:
```
python3 optimize_weights.py     # coordinate-ascent on scoring weights; stops when
                                # marginal retention gain < EPSILON or MAX_ROUNDS=12
```

Backstop + `<data>` wrapping are visible in `curate_loop.md` and enforced in
`context_ledger.py` (budget assert, untrusted fencing).

### Sanity check (per failure mode)
*All verified reproducibly in `test_context_ledger.py` (`uv run test_context_ledger.py`).*
- **Bloat:** detector = `hot_tokens > B`; recovery = greedy eviction + hard `assert` in `curate()`; pins spill rather than overflow. *Verified: 5011 cards bounded ≤ B; pins-alone-over-B spill + flag.*
- **Rot:** detector = pinned/lossless card missing across compaction; recovery = verbatim preservation + PreCompact refresh. *Verified: pin survived 5000 newer cards.*
- **Oscillation:** detector = digest hash repeats ≥3 rounds; recovery = backstop HALT.
- **Drift:** detector = anchor mismatch / recency decay; recovery = per-round re-rank against current anchor.
- **Premature stop:** detector = DONE requires 2 stable rounds AND lossless preserved; recovery = keep looping if either fails.
- **Runaway:** detector = round/token cap; recovery = unconditional HALT, no auto-retry.
- **Prompt injection:** detector = provenance='untrusted'; recovery = `<data>` fence, content ranked never executed.
- **Cost blowout:** detector = cumulative curator tokens ≥200k; recovery = backstop HALT; cadence ≥1200s.
- **Convergence (optimizer):** detector = gain<EPSILON for PATIENCE rounds OR MAX_ROUNDS; recovery = halt at bounded ceiling. *Verified: converged at 0.50 = budget ceiling in 3 rounds.*
