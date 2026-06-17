#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Optimization sub-loop — the "keep optimizing until optimal" part of the request.

This is a BOUNDED self-refinement loop (draft -> evaluate -> revise) that tunes the
ledger's scoring weights to maximize relevance retention under the token budget,
then CONVERGES AND HALTS. It does not run forever.

Done test (LSC-1 for this sub-loop):
  retention(kept_high_value) maximized AND marginal gain over last round < EPSILON
  -- OR the backstop trips. The optimum is bounded: a scored cache cannot retain
  more high-value cards than fit in the budget, so the objective has a ceiling.

Objective = fraction of "gold" high-value cards (decisions/constraints/open_qs)
that survive curation under budget, given a held-out anchor. Maximizing it = the
weights that keep the right things and evict the right things.

Complexity: each round re-curates O(N log N); coordinate-ascent over W weights for
R rounds => O(R * W * N log N), R bounded by MAX_ROUNDS backstop. Convergent.
"""
from __future__ import annotations
import itertools
from context_ledger import ContextLedger

EPSILON = 0.005      # marginal-gain convergence threshold
MAX_ROUNDS = 12      # BACKSTOP (LSC-3) — hard cap regardless of convergence
PATIENCE = 2         # stop after this many no-improvement rounds


def build_eval_set() -> tuple[ContextLedger, set[str], str, int]:
    """Synthetic but representative: gold high-value cards buried in noise."""
    led = ContextLedger()
    gold = set()
    for i in range(20):
        # Half the gold is ON-anchor, half is off-anchor but still high-kind.
        topic = "relevance scoring cache budget invariant" if i % 2 == 0 else f"unrelated topic {i}"
        c = led.ingest("decision", f"decision {i}: chose strategy {topic} tier {i}")
        gold.add(c.id)
        c = led.ingest("constraint", f"constraint {i}: {topic} requirement {i}")
        gold.add(c.id)
    # Noise that LOOKS on-anchor (decoys) — only good ranking evicts these, not kind.
    for i in range(600):
        led.ingest("note", f"transient relevance scoring chatter {i} cache filler noise")
    anchor = "relevance scoring cache budget invariant strategy decision constraint"
    budget = 260                  # fits only ~half the gold -> bounded ceiling ~0.5
    return led, gold, anchor, budget


def retention(weights: dict, led: ContextLedger, gold: set[str],
              anchor: str, budget: int) -> float:
    led.weights = {**ContextLedger.DEFAULT_WEIGHTS, **weights}
    sel = led.curate(budget, anchor)
    kept = set(sel["kept"])
    return len(kept & gold) / len(gold)


def optimize() -> dict:
    led, gold, anchor, budget = build_eval_set()
    weights = dict(ContextLedger.DEFAULT_WEIGHTS)
    best = retention(weights, led, gold, anchor, budget)
    grid = [0.0, 0.3, 0.6, 1.0, 1.4, 2.0]
    no_improve = 0

    for rnd in range(1, MAX_ROUNDS + 1):           # backstop-bounded
        round_best = best
        # Coordinate ascent: try each weight axis independently this round.
        for key in ("task_sim", "recency", "frequency", "kind", "size"):
            for v in grid:
                trial = dict(weights, **{key: v})
                r = retention(trial, led, gold, anchor, budget)
                if r > round_best + 1e-12:
                    round_best, weights = r, trial
        gain = round_best - best
        best = round_best
        print(f"round {rnd:2d}  retention={best:.3f}  gain={gain:.4f}  weights="
              f"{ {k: round(v,2) for k,v in weights.items()} }")
        if gain < EPSILON:                          # converged
            no_improve += 1
            if no_improve >= PATIENCE:
                print(f"CONVERGED after {rnd} rounds (gain < {EPSILON}).")
                break
        else:
            no_improve = 0
    else:
        print(f"HALTED at backstop MAX_ROUNDS={MAX_ROUNDS} (did not fully converge).")

    print(f"\nOptimal retention = {best:.3f}  (gold kept = {round(best*len(gold))}/{len(gold)})")
    print(f"Tuned weights: { {k: round(v,2) for k,v in weights.items()} }")
    return weights


if __name__ == "__main__":
    optimize()
