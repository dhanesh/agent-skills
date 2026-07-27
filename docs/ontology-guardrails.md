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

## Follow-ups (shipped)

All three candidates were implemented. Two of them changed shape once the code was read —
recorded here because the corrections are the substance, not bookkeeping.

**1. `crafting-self-prompting-loops` — typed loop boundary (LSC-4).** As scoped. `LSC-6`'s
`OUTPUT_VALIDATION` checks a single *observation* at its point of use; nothing checked the
*state that compounds*, and `state = update(state, observation)` fed the next iteration
unvalidated. Added `STATE_SCHEMA` + `STATE_VALIDATION` slots to LSC-4 across the spec, the
canonical checklist, and all five family templates, each with a family-appropriate schema and
violation response (*repair* / *re-ask* / *halt* — never "continue anyway"), plus the check in
every loop body. New failure mode **6b, Malformed carried state**. Graded by a third eval
property with two negative fixtures, one of which pins the load-bearing distinction: a schema
**declared but never enforced** must not count as typed state. Scoped honestly — a genuinely
unstructured loop writes `N/A — unstructured prior-output state` rather than inventing a
schema nothing enforces.

**2. `context-hygiene-kit` — corrected premise.** The candidate assumed the vocabulary was
unenforced. It was already enforced at `Card.__post_init__`. The *real* defect was the
opposite of missing validation — validation that was too brittle: `Card(**cd)` raised on any
bad card, so **one** unknown kind or one field from a newer schema made the **entire** ledger
unloadable. Behind the Stop hook's `… || true` that is a silent, permanent capture failure —
every durable fact lost, no error surfaced, the exact rot the kit exists to prevent. Both
modes reproduced as tests, then fixed by quarantining the bad card while the rest of the
ledger loads (quarantine persisted, counted in `stats`, banner in the digest). Extension added
on top (`kinds --add`, requiring a salience prior and a lossless flag).

**3. OKF — corrected target *and* corrected premise.** The candidate named `okf-site-kit` /
`knowledge-gardener`. Neither writes bundles: the first is a **consumer** whose tolerance of a
missing/unknown `type` is *required* by the spec ("tolerate unknown type values", "degrade,
don't fail"), the second is read-only. The actual writer for the whole trilogy is
`feynman-walkthrough/assets/okf.py` (`bug-autopsy` delegates to its `pin`), and
`write_concept()` wrote anything at all. The candidate also said "validate against the OKF
vocabulary" — but OKF v0.1 defines `type` as "a short, producer-chosen string (no central
registry)", so a closed type vocabulary **would itself violate the spec**. The shipped
validation is therefore *structural, not a vocabulary*: presence and shape of `type` (never
its value), serialisability, `tags`/`timestamp` shape, and source pins carrying exactly what
the read side requires. A stricter first draft demanded a non-null `fingerprint` and broke
external sources, which legitimately have none — caught by the existing test suite and
corrected.

The through-line: **validate at the write boundary of state that compounds, and let the
validated thing be as open as its own spec demands.** Two of three follow-ups were wrong about
*where* the boundary was, and one was wrong about *what* could legitimately be closed.

### Still not done (deliberately)

Read-only report-producing skills (`bug-autopsy`, `feynman-walkthrough`'s walkthrough half,
`security-posture-audit`, `base-in-reality`) remain unguarded by design — a wrong sentence
there is reviewable prose, not state that compounds.
