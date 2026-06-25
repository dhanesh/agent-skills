#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []   # stdlib only — keep the curator dependency-free so a hook can run it anywhere
# ///
"""
Context Ledger — a bounded, scored, tiered cache for agent working context.

Why this exists
---------------
An agent session accumulates context unbounded (BLOAT). When the harness compacts,
naive summarization is lossy and discards the *specific* facts that mattered
(decisions, constraints, file:line refs) while keeping vague narrative (ROT).

This module fixes both with one structure:

  * HOT tier   — a token-budget-bounded set of "context cards" kept in the window.
                 Bounded => no bloat, regardless of session length.
  * COLD tier  — every card ever seen, persisted on disk, indexed by id.
                 High-salience / pinned cards are preserved VERBATIM (lossless)
                 so compaction never rots a decision into mush.
  * DIGEST     — an O(budget) markdown rendering loaded at session start instead
                 of replaying raw history. This is the anti-rot payload.

Complexity (the "until optimal" target — see LOOP_SPEC.md LSC-1)
---------------------------------------------------------------
  ingest(card)            O(1) amortized   (dict upsert)
  curate(budget, anchor)  O(N log N)       (score all + greedy knapsack fill)
  to_digest()             O(K)             (K = kept cards <= budget)
  load / persist          O(N) io          (N bounded by cold-store policy)

Steady state: N is held bounded by eviction, so per-turn cost is amortized O(1)
and the hot window is O(budget) tokens forever. There is no asymptotically better
structure than a bounded scored cache with tiered persistence — which is the
convergence anchor for the optimization loop (stop refining when this is met).

Greedy fill is fractional-knapsack by (score / tokens) ratio: optimal for the LP
relaxation, near-optimal for 0/1, and O(N log N) instead of pseudo-polynomial DP.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

# ---- Trust boundary (LSC-7) ------------------------------------------------
# Every card records WHERE it came from. The curator NEVER executes card content;
# it only ranks and renders it as data. "untrusted" provenance can be ranked but
# is rendered inside a fenced <data> block in the digest so a downstream model
# treats it as data, not instructions.
#
# The LOAD-BEARING control is harvest-side: tool_result/tool_use blocks are never
# ingested and markers must start the line (see harvest.py). The <data> fence is a
# SECONDARY, best-effort soft delimiter (OWASP LLM01 "segregate/denote external
# content") — not a complete boundary. Because it is a textual fence, untrusted
# content containing a literal "</data>" could otherwise close it early and smuggle
# trailing text outside the fence, so _neutralize_fences() escapes any fence tokens
# in untrusted content before it is wrapped.
TRUSTED = "trusted"        # your fixed scaffold, your own committed decisions
UNTRUSTED = "untrusted"    # tool output, web text, file contents, other agents

_DATA_FENCE_RE = re.compile(r"<(/?)data>", re.IGNORECASE)


def _neutralize_fences(content: str) -> str:
    """Escape <data>/</data> tokens in untrusted content so it cannot break out
    of the fence. Angle brackets of the fence token become HTML entities; a
    payload's "</data>" can no longer form a real closing fence."""
    return _DATA_FENCE_RE.sub(lambda m: f"&lt;{m.group(1)}data&gt;", content)

KINDS = ("decision", "constraint", "lesson", "fact", "file_ref", "task_state", "open_question", "note")
# Kinds that are LOSSLESS-preserved on compaction (rot-proof). Order = digest priority.
# `lesson` is the one MODEL-DISTILLED lossless kind: a generalizable guideline the
# curator synthesizes from feedback/critique (episodic -> semantic), not a deterministic
# capture. See references/capture.md "Lessons: the distilled kind".
LOSSLESS_KINDS = ("decision", "constraint", "lesson", "open_question", "task_state", "file_ref")

_WORD = re.compile(r"[a-z0-9_./:-]+")


def _tokenize(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _est_tokens(text: str) -> int:
    # Cheap, dependency-free token estimate (~4 chars/token). Good enough for budgeting.
    return max(1, math.ceil(len(text) / 4))


@dataclass
class Card:
    kind: str
    content: str
    provenance: str = TRUSTED
    pinned: bool = False
    access_count: int = 1
    last_accessed: float = 0.0     # logical clock tick, NOT wall-clock (deterministic)
    created: float = 0.0
    tokens: int = 0
    id: str = ""

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown kind {self.kind!r}; valid: {KINDS}")
        if self.provenance not in (TRUSTED, UNTRUSTED):
            raise ValueError(f"provenance must be {TRUSTED} or {UNTRUSTED}")
        if not self.tokens:
            self.tokens = _est_tokens(self.content)
        if not self.id:
            # Stable identity = kind + normalized content => dedupe + idempotent upsert.
            norm = " ".join(self.content.lower().split())
            self.id = hashlib.sha1(f"{self.kind}:{norm}".encode()).hexdigest()[:16]


class ContextLedger:
    """Bounded scored cache with tiered persistence. The whole mechanism."""

    # Default scoring weights. The optimization loop (LSC-5) tunes these.
    DEFAULT_WEIGHTS = {
        "recency": 1.0,    # exponential decay on last_accessed
        "frequency": 0.8,  # log(1+access_count) — repeated relevance
        "task_sim": 1.4,   # Jaccard overlap with the current task anchor (relevance)
        "kind": 0.6,       # structural priority (a decision > a passing note)
        "size": 0.3,       # penalty per token (favor dense, cheap-to-keep cards)
    }
    KIND_PRIOR = {  # structural salience prior in [0,1]
        "decision": 1.0, "constraint": 1.0, "lesson": 0.95, "open_question": 0.9,
        "task_state": 0.8, "file_ref": 0.6, "fact": 0.5, "note": 0.3,
    }
    DECAY_LAMBDA = 0.15  # per logical tick

    def __init__(self, weights: dict | None = None):
        self.cards: dict[str, Card] = {}
        self.clock: float = 0.0
        self.weights = {**self.DEFAULT_WEIGHTS, **(weights or {})}

    # ---- ingest: O(1) amortized -------------------------------------------
    def ingest(self, kind: str, content: str, *, provenance: str = TRUSTED,
               pinned: bool = False) -> Card:
        self.clock += 1.0
        card = Card(kind=kind, content=content, provenance=provenance,
                    pinned=pinned, last_accessed=self.clock, created=self.clock)
        existing = self.cards.get(card.id)
        if existing:                      # re-seeing a fact => bump recency+frequency
            existing.access_count += 1
            existing.last_accessed = self.clock
            existing.pinned = existing.pinned or pinned
            return existing
        self.cards[card.id] = card
        return card

    # ---- scoring -----------------------------------------------------------
    def score(self, card: Card, anchor_tokens: set[str]) -> float:
        if card.pinned:
            return math.inf           # pins never evict (LSC: rot-proofing)
        w = self.weights
        age = self.clock - card.last_accessed
        recency = math.exp(-self.DECAY_LAMBDA * age)
        frequency = math.log1p(card.access_count)
        ctoks = _tokenize(card.content)
        union = len(ctoks | anchor_tokens) or 1
        task_sim = len(ctoks & anchor_tokens) / union          # Jaccard, O(len)
        kind_prior = self.KIND_PRIOR.get(card.kind, 0.4)
        size_pen = card.tokens / 100.0
        return (w["recency"] * recency
                + w["frequency"] * frequency
                + w["task_sim"] * task_sim
                + w["kind"] * kind_prior
                - w["size"] * size_pen)

    # ---- curate: O(N log N) greedy knapsack fill ---------------------------
    def curate(self, budget_tokens: int, task_anchor: str) -> dict:
        """Select the HOT set under a HARD token budget. Returns kept/evicted ids + stats.

        Invariant (LSC-6, anti-bloat): ``hot_tokens <= budget_tokens`` ALWAYS — even
        if pinned cards alone would overflow. Pins get first claim on the budget
        (anti-rot priority), but the budget is a hard cap: silently exceeding it would
        reintroduce the very bloat this structure exists to prevent. If pins overflow,
        the lowest-priority pins spill to ``evicted`` and ``pins_over_budget`` is raised
        so the caller can invoke the LSC-8 human gate instead of blowing the window.
        """
        anchor = _tokenize(task_anchor)
        pins, others = [], []
        for c in self.cards.values():
            (pins if c.pinned else others).append(c)

        kept, evicted, used = [], [], 0

        # Pass 1 — pins first (rot-proof priority). When they can't all fit, keep the
        # most salient deterministically: recency, then frequency. Spilled pins are
        # flagged, not silently dropped.
        pins.sort(key=lambda c: (c.last_accessed, c.access_count), reverse=True)
        pins_dropped = []
        for c in pins:
            if used + c.tokens <= budget_tokens:
                kept.append(c)
                used += c.tokens
            else:
                evicted.append(c)
                pins_dropped.append(c.id)

        # Pass 2 — fill the remainder by value-density (score / tokens), greedy
        # knapsack. None of these are pinned, so score() is finite.
        others.sort(key=lambda c: self.score(c, anchor) / max(1, c.tokens), reverse=True)
        for c in others:
            if used + c.tokens <= budget_tokens:
                kept.append(c)
                used += c.tokens
            else:
                evicted.append(c)

        assert used <= budget_tokens, "LSC-6 budget invariant violated"
        return {
            "kept": [c.id for c in kept],
            "evicted": [c.id for c in evicted],
            "kept_cards": kept,
            "hot_tokens": used,
            "budget": budget_tokens,
            "utilization": round(used / budget_tokens, 3) if budget_tokens else 0.0,
            "n_total": len(self.cards),
            "pins_over_budget": bool(pins_dropped),
            "pins_dropped": pins_dropped,
        }

    # ---- anti-rot digest: lossless for high-value kinds --------------------
    def to_digest(self, budget_tokens: int, task_anchor: str) -> str:
        sel = self.curate(budget_tokens, task_anchor)
        kept = sel["kept_cards"]
        by_kind: dict[str, list[Card]] = {}
        for c in kept:
            by_kind.setdefault(c.kind, []).append(c)

        lines = [
            "# Context Digest (durable, survives compaction)",
            f"<!-- hot_tokens={sel['hot_tokens']}/{budget_tokens} "
            f"cards={len(kept)}/{sel['n_total']} util={sel['utilization']} -->",
            "",
        ]
        if sel["pins_over_budget"]:
            # LSC-8 gate signal: pins alone exceed the budget; some were spilled to
            # cold rather than overflow the hot window. Surfaced, never silent.
            lines.append(
                f"> ⚠️ PINS_OVER_BUDGET: {len(sel['pins_dropped'])} pinned card(s) "
                "did not fit and were spilled. Raise the human gate or grow the budget."
            )
            lines.append("")
        lines += [
            "> Loaded at session start INSTEAD of raw history. "
            "Items below are preserved verbatim (lossless) to prevent rot.",
            "",
        ]
        # Lossless, high-priority kinds first and verbatim.
        for kind in LOSSLESS_KINDS:
            cards = sorted(by_kind.get(kind, []), key=lambda c: -c.access_count)
            if not cards:
                continue
            lines.append(f"## {kind.replace('_', ' ').title()}")
            for c in cards:
                pin = " 📌" if c.pinned else ""
                if c.provenance == UNTRUSTED:
                    # Two-channel boundary: render untrusted content as fenced DATA.
                    # Neutralize any embedded fence tokens so the content cannot
                    # break out of the <data> block.
                    lines.append(f"- (untrusted){pin}")
                    lines.append("  <data>")
                    lines.append(f"  {_neutralize_fences(c.content)}")
                    lines.append("  </data>")
                else:
                    lines.append(f"- {c.content}{pin}")
            lines.append("")
        # Remaining kinds compacted (lossy is acceptable here — low salience).
        misc = [c for k, cs in by_kind.items() if k not in LOSSLESS_KINDS for c in cs]
        if misc:
            lines.append("## Other context (compactable)")
            for c in sorted(misc, key=lambda c: -c.access_count)[:50]:
                if c.provenance == UNTRUSTED:
                    lines.append("- (untrusted) <data>" + _neutralize_fences(c.content) + "</data>")
                else:
                    lines.append(f"- {c.content}")
            lines.append("")
        return "\n".join(lines)

    # ---- persistence -------------------------------------------------------
    def persist(self, path: Path, *, cold_cap: int = 5000) -> None:
        # Cold store keeps everything up to cold_cap, evicting only the lowest
        # all-time salience (pins + recent always survive). Keeps disk O(cold_cap).
        cards = list(self.cards.values())
        if len(cards) > cold_cap:
            anchor: set[str] = set()
            cards.sort(key=lambda c: (c.pinned, self.score(c, anchor)), reverse=True)
            cards = cards[:cold_cap]
            self.cards = {c.id: c for c in cards}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "clock": self.clock,
            "weights": self.weights,
            "cards": [asdict(c) for c in self.cards.values()],
        }, indent=2))

    @classmethod
    def load(cls, path: Path) -> "ContextLedger":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        led = cls(weights=data.get("weights"))
        led.clock = data.get("clock", 0.0)
        for cd in data.get("cards", []):
            c = Card(**cd)
            led.cards[c.id] = c
        return led


# ---- CLI so lifecycle hooks can drive it ----------------------------------
def _main(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Context Ledger curator")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("ingest", help="add a card")
    pi.add_argument("kind", choices=KINDS)
    pi.add_argument("content")
    pi.add_argument("--provenance", default=TRUSTED, choices=[TRUSTED, UNTRUSTED])
    pi.add_argument("--pinned", action="store_true")

    pc = sub.add_parser("curate", help="write the durable digest")
    pc.add_argument("--budget", type=int, default=8000)
    pc.add_argument("--anchor", default="")
    pc.add_argument("--digest", default=".context/digest.md",
                    help="where to write the rendered digest")

    ps = sub.add_parser("stats", help="print ledger stats")

    for sp in (pi, pc, ps):
        sp.add_argument("--store", default=".context/ledger.json")

    args = p.parse_args(argv)
    store = Path(args.store)
    led = ContextLedger.load(store)

    if args.cmd == "ingest":
        c = led.ingest(args.kind, args.content,
                       provenance=args.provenance, pinned=args.pinned)
        led.persist(store)
        print(f"ingested {c.id} ({c.kind}, {c.tokens} tok)")
    elif args.cmd == "curate":
        digest = led.to_digest(args.budget, args.anchor)
        digest_path = Path(args.digest)
        digest_path.parent.mkdir(parents=True, exist_ok=True)
        digest_path.write_text(digest)
        led.persist(store)
        sel = led.curate(args.budget, args.anchor)
        print(f"digest -> {digest_path} "
              f"({sel['hot_tokens']}/{args.budget} tok, "
              f"util={sel['utilization']}, kept {len(sel['kept'])}/{sel['n_total']})")
        if sel["pins_over_budget"]:
            print(f"WARNING pins_over_budget: {len(sel['pins_dropped'])} pinned "
                  "card(s) spilled — raise the human gate (LSC-8) or grow --budget",
                  file=sys.stderr)
    elif args.cmd == "stats":
        print(json.dumps({
            "cards": len(led.cards),
            "clock": led.clock,
            "pinned": sum(c.pinned for c in led.cards.values()),
            "tokens": sum(c.tokens for c in led.cards.values()),
        }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
