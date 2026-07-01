# base-in-reality audit — world-model-ledger

**Date:** 2026-07-01 · **Scope:** the `world-model-ledger` skill (design spec + `assets/*.py`) · **Max claims:** 8

## Executive summary

Eight falsifiable claims were extracted from the `world-model-ledger` design spec and
implementation (algorithm / architecture layers; the skill is developer-tooling, so there is no
business-domain layer). Two findings survived the adversarial refutation pass: one **CONFIRMED
(medium)** documentation/implementation mismatch — the spec and references claimed a
"depth-capped recursive CTE" graph traversal that the code did **not** implement — and one
grounded **DEVIATION (low)** — the symbol-id format is *SCIP-inspired*, not SCIP-conformant.
The most important issue (the recursive-CTE mismatch) has been **fixed in this same change**
(traversal implemented + two regression tests; gate now 23/23). One positive claim was verified
against fetched documentation: the `PreToolUse` hook's `additionalContext` injection is
officially supported and works. The remaining claims are sound design choices that could not be
elevated to violations within the session's egress limits and are reported `UNCONFIRMED`.

## Domain map

- **Detected domain(s):** developer tooling / agent memory / probabilistic knowledge
  representation (entity-relationship + provenance store); persistence layer = SQLite.
- **Norms surface in scope:** graph-index schemas (SCIP/Kythe/CPG), probabilistic evidence
  fusion (noisy-OR / independence-of-causal-influence), belief revision (AGM/JTMS/ATMS),
  constraint validation (SHACL), SQLite features (FTS5, recursive CTE), and the Claude Code
  hooks API.
- **Egress note (grounding honesty):** the org proxy denied CONNECT to `sqlite.org`,
  `api.openalex.org`, and most academic hosts (HTTP 403); **GitHub raw** and
  **`code.claude.com` docs** were fetchable, and **WebSearch** returned snippets (which do not
  satisfy the fetched-URL grounding bar on their own). Claims groundable only via a blocked host
  are reported `UNCONFIRMED` per the grounding invariant — not as violations.

## Findings

> Each finding below is grounded in at least one source fetched during this audit.
> Ungrounded claims appear as `UNCONFIRMED`.

### [medium] VIOLATION (of the skill's own spec) — recursive-CTE traversal was claimed but not implemented  ·  RESOLVED in this change

- **Claim:** "Graph reachability … uses a depth-capped recursive CTE over `interaction` with
  `UNION`" and "Retrieval = FTS5 match … + 1–2 hop recursive-CTE neighborhood."
- **Location:** `world-model-ledger/references/schema.md:36`,
  `docs/superpowers/specs/2026-07-01-world-model-ledger-design.md:219,265,416,501`
- **Layer:** arch
- **Assessment:** `git grep` for `recursive`/`WITH RECURSIVE`/`hop` in `assets/*.py` returned
  nothing; `query_touching` performed a single-hop query (`subject_id IN (…) OR object_id IN
  (…)`), so the documented 1–2 hop neighborhood retrieval did not exist. A pre-call summary
  therefore missed second-ring context (e.g. a contradiction one hop from the edited symbol).
  This is a falsifiable claim about what the code does that was false — an internal
  spec/implementation mismatch.
- **Citations:**
  - world-model-ledger source tree (read this session) — `assets/world_model.py` `query_touching`
    had no recursive CTE; `references/schema.md:36` asserted one. (fetched: repo file, in-session)
- **Recommended fix:** implement the depth-capped recursive CTE, or downgrade the docs to
  "single-hop". **Done:** added `_reachable_nodes()` (cycle-safe `UNION`, depth-capped) and made
  `query_touching(..., hops=)` walk the induced neighborhood; added
  `TestGraphTraversal.test_recursive_cte_is_cycle_safe` and `test_two_hop_neighborhood`.

### [low] DEVIATION — symbol-id format is SCIP-*inspired*, not SCIP-conformant

- **Claim:** entity `symbol_id` gives "stable SCIP-style id" (e.g. `sym:<path>#<name>`).
- **Location:** `world-model-ledger/references/schema.md`, `assets/world_model.py`
  `_default_symbol_id`
- **Layer:** arch
- **Assessment:** SCIP's symbol grammar (fetched) is
  `<scheme> ' ' <package> ' ' (<descriptor>)+ | 'local ' <local-id>`, with typed descriptor
  suffixes (`#` type, `.` term, `()` method, …). The skill's `sym:<path>#<name>` /
  `file:<path>` / `referent:<name>` scheme does not follow that grammar, so IDs are not
  interoperable with SCIP tooling. Impact is low: the skill only ever claimed "SCIP-style" and
  the spec explicitly documents "name-within-file (v1 resolution depth; SCIP upgrade path noted)"
  — the deviation is deliberate and scoped. The refutation pass kept this at `low`/`DEVIATION`
  (not `VIOLATION`) precisely because the code never claimed conformance.
- **Citations:**
  - SCIP protobuf schema — https://raw.githubusercontent.com/sourcegraph/scip/main/scip.proto
    (fetched) — "`<symbol> ::= <scheme> ' ' <package> ' ' (<descriptor>)+ | 'local ' <local-id>`".
- **Recommended fix:** tighten the wording from "SCIP-style" to "SCIP-**inspired** (not
  conformant; v1 uses name-within-file)" so no interoperability is implied. **Applied.**

### [n/a] VERIFIED CORRECT — PreToolUse `additionalContext` injection is supported

- **Claim:** the pre-call hook can inject its summary via
  `hookSpecificOutput.additionalContext` on a `PreToolUse` hook.
- **Location:** `world-model-ledger/assets/hooks/pretooluse.sh`
- **Layer:** arch
- **Assessment:** verified against the official hooks reference — `PreToolUse` is among the
  events whose `additionalContext` is passed to the model. No defect; the mechanism works. (The
  benign extra `"continue": true` field is ignored for `PreToolUse`.)
- **Citations:**
  - Claude Code hooks guide — https://code.claude.com/docs/en/hooks-guide.md (fetched) — "Text
    returned via `additionalContext` is injected as a system reminder that Claude reads as plain
    text"; `PreToolUse` output schema lists `additionalContext` (optional).

## Unconfirmed claims (sound-but-ungroundable within egress limits)

Reported `UNCONFIRMED` — defensible design choices for which no authoritative source could be
**fetched** this session (blocked hosts), so they are deliberately **not** raised as violations:

- **[algo] noisy-OR evidence fusion (`1 − Π(1 − wᵢ)`) assumes independent evidence.** WebSearch
  corroborates that noisy-OR rests on independence-of-causal-influence, and that correlated
  sources need a dampening factor (TruthFinder) — but the authoritative sources (arxiv, PMC)
  were 403 at fetch time. The store mitigates the common case with a
  `UNIQUE(fact_kind,fact_id,evidence_kind,ref,polarity)` constraint (exact-duplicate evidence
  cannot double-count); residual correlation (two different refs on the same line) can still
  mildly inflate `observed_conf`. **Action taken:** documented as an explicit limitation in
  `references/confidence-model.md` rather than asserted as a violation.
- **[algo] `normative_conf = noisy_or(oracle) × (1 − noisy_or(refutes))`** is an ad-hoc (but
  monotonic, bounded, defensible) combination rule, not a named standard — no source to violate.
- **[algo] entrenchment = max supporting-evidence rank operationalizes AGM minimal-change
  retraction** — the AGM/SEP source was unreachable; the operationalization is reasonable.
- **[impl] FTS5 external-content delete trigger supplies `old` values** — matches the known-correct
  pattern, but `sqlite.org/fts5.html` was 403, so external confirmation is `UNCONFIRMED`. (A
  functional test exercises FTS insert/sync indirectly.)

## Post-audit remediation (follow-up)

After the audit, findings 2 and 4 were addressed in code rather than left as notes:

- **Finding 2 (SCIP DEVIATION) — remediated.** `_default_symbol_id` now emits ids that follow
  SCIP's `<scheme> <package> <descriptor>+` grammar shape with SCIP descriptor suffixes
  (`/` namespace, `#` type, `().` method, `.` term); e.g. `wml . auth/hash.py/hash_pw().`. The
  `package` remains the placeholder `.` pending manager/version resolution, so it is
  grammar-shaped and greppable but still not fully interoperable — an honest partial fix. Test:
  `TestSchema.test_scip_shaped_symbol_ids`.
- **Finding 4 (noisy-OR correlated evidence) — remediated.** Confidence fusion now uses
  `grouped_noisy_or`: evidence is grouped by source (the file/path of its `ref`), correlated
  sightings within a source take the max (no double-count), and only distinct sources are fused
  with noisy-OR — the TruthFinder copying-source intuition at fusion time. Tests:
  `TestDerivation.test_correlated_evidence_is_dampened`, `test_grouped_noisy_or_helper`.

Gate after remediation: 26/26.

## Sources appendix

1. SCIP protobuf schema — Sourcegraph — https://raw.githubusercontent.com/sourcegraph/scip/main/scip.proto (fetched)
2. Claude Code hooks guide — Anthropic — https://code.claude.com/docs/en/hooks-guide.md (fetched, via claude-code-guide subagent)
3. world-model-ledger repository files — read in-session (`assets/world_model.py`, `references/schema.md`, design spec)

WebSearch-surfaced but **not** fetchable this session (egress 403), therefore not used to ground any VIOLATION/DEVIATION: arxiv 1303.5704 (noisy-OR), PMC evidence-integration articles, `sqlite.org/fts5.html`, `sqlite.org/lang_with.html`, `api.openalex.org`.

## Dropped-claims log

None truncated by `--max-claims` (8 extracted, 8 assessed). Coverage caveat: several
algorithm-layer claims could only be reasoned about, not externally grounded, because the
proxy blocked the relevant academic/vendor hosts — those are marked `UNCONFIRMED`, not passed.
</content>
