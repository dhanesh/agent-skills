# Ontology guardrails across this repo's skills

Source: Frank Coyle, **"Why Agentic Systems Need Ontologies"** (AI Engineer, 2026;
[youtu.be/Sir59K8ZDPU](https://youtu.be/Sir59K8ZDPU)). Core argument: agents = LLM + tools +
loops, which makes them Turing-complete *and* probabilistic — the generator will eventually
emit a plausible-but-impossible action (the duplicate refund is the system working as
designed). The remedy is **neuro-symbolic**: tether the probabilistic core to a symbolic layer
— an ontology (typed vocabulary + domain/range constraints, per RDFS/OWL, or pragmatically a
typed validator) — that deterministically checks every output *before it feeds the next loop
step*, and reuse existing vocabularies rather than inventing them.

## Where this repo already embodies the principles

The talk's architecture is largely this repo's architecture, independently arrived at:

- `make gate` is a symbolic layer over probabilistic authorship — every skill change passes
  deterministic validators (structure, frontmatter, leaks, playbook, tests, model-free eval
  graders) before merge.
- `world-model-ledger` is Coyle's "ledger": typed entities, SHACL-inspired constraints,
  PROV-style evidence, and the observed-vs-normative split ("probable" ≠ "true").
- `crafting-self-prompting-loops` bakes in his loop warnings: hard-stop backstop (runaway
  loops/cost) and a trusted/untrusted two-channel boundary.
- `spec-first-planning`, `ai-migration-operating-model`, `starlight-handbook-kit` all build
  the deterministic judge/contract before letting agents produce.

## The gap the talk exposed, and the pilot

What was missing was validation **at the write boundary of the ledger itself**: entity kinds,
evidence kinds, and statuses were CHECK-constrained, but `interaction.predicate` was free
text. An agent marker like `WM-OBSERVE: stripe-api frobnicates auth.py` — or the semantically
impossible `stripe/refunds-api imports auth.py` — entered the world model silently. Exactly
Coyle's "semantically impossible status strings," in the skill whose job is being the ground
ledger other reasoning builds on.

**Pilot (shipped): `world-model-ledger` predicate ontology.** A closed, deliberately-extensible
vocabulary of verbs, each with RDFS-style ordered domain/range over the four entity kinds;
enforced in `add_interaction`/`add_constraint` before any insert. Unknown entities get their
stub kind *inferred from the ontology* (rdfs:domain/range as type inference); known entities'
recorded kinds are authoritative and violations are rejected with the allowed set named
(structured error, exit 2 — a self-correction affordance for a probabilistic caller). Markers
that violate it are skipped without breaking hooks; extension is an explicit act
(`wm ontology --add` → `ontology.json`), never a side effect of a marker. Details:
`world-model-ledger/references/ontology.md`.

## Honest debate — where ontologies help here, and where they don't

**For:** the guardrail is cheap (a dict lookup + one SELECT per write), deterministic, and
catches a class of error no prompt-review will (prompts can't enumerate "impossible" — a type
system can). The rejection message doubles as feedback, which is the only mechanism by which a
probabilistic writer *converges* instead of silently polluting state. And it hardens exactly
the asset other guarantees lean on: contradiction detection over garbage triples is garbage.

**Against / limits:** (1) An ontology types triples, it does not make them *true* —
`auth.py imports hashlib` passes even when false; truth stays the job of oracle evidence.
(2) Closed vocabularies under-fit real domains; the talk's own answer (reuse/extend existing
taxonomies) is why extension is first-class rather than the vocabulary being a straitjacket.
(3) Full OWL/reasoners would be over-engineering at this scale — the pragmatic
"Pydantic-not-Protégé" reading of the talk is the right altitude for stdlib-only skills; we
deliberately stop at RDFS-style domain/range, no class hierarchy. (4) The one real cost is
recall: an over-strict rule can drop a semi-legitimate observation. The pilot mitigates by
inferring stub kinds instead of rejecting unknowns, and rejecting only *provable* kind
conflicts.

**Verdict:** adopt at write boundaries of persistent state and at loop-step outputs; do not
bolt onto read-only report-producing skills (`bug-autopsy`, `feynman-walkthrough`,
`security-posture-audit`), where a wrong sentence is reviewable prose, not state that
compounds.

## Candidate follow-ups (not yet done)

- `crafting-self-prompting-loops`: add an "ontology/typed-state check at every loop boundary"
  item to the loop-spec safety properties — validate loop state against a declared schema
  before it feeds the next iteration.
- `context-hygiene-kit`: a typed vocabulary for cache entry categories at the ledger's write
  path (same pattern, smaller surface).
- `okf-site-kit` / `knowledge-gardener`: validate OKF bundle `type`/metadata fields against
  the OKF vocabulary at write/refresh time rather than degrading at render time.
