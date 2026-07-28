# Ontology reference — the predicate vocabulary and its guardrails

The ledger's write boundary is **neuro-symbolic**: the things writing to it are probabilistic
(an agent's `WM-OBSERVE` markers, `wm observe` calls), so every triple is checked against a
deterministic **ontology** — a closed, extensible vocabulary of predicates, each with
RDFS-style **domain** (allowed subject kinds) and **range** (allowed object kinds) — *before*
anything enters the ledger. A hallucinated verb (`frobnicates`) or a semantically impossible
pairing (a `referent` that `imports` a file) is rejected, never silently stored. Deterministic
emitters (the build scanner, the exec observer) only speak vocabulary verbs, so validation
costs them nothing.

This follows the neuro-symbolic guardrail argument (Coyle, "Why Agentic Systems Need
Ontologies", AI Engineer 2026): a probabilistic generator will eventually emit a plausible but
impossible fact; only a symbolic layer that types every write can catch it deterministically.

## The core vocabulary

`domain`/`range` are **ordered** — the first kind doubles as the stub kind when a triple names
an entity the model has never seen (rdfs:domain/range used as type inference). A **known**
entity's recorded kind is authoritative and is what domain/range are checked against.

| Predicate | Domain (subject kinds) | Range (object kinds) | Emitted by |
|---|---|---|---|
| `imports` | file, module | module, file | build scanner, markers |
| `includes` | file | file | build scanner |
| `references` | file | file | build scanner |
| `depends_on` | file, module | referent | build scanner |
| `provides` | file, module | symbol | markers |
| `executes` | referent, file | file | exec observer |
| `reads` | referent, file, symbol | file, referent | exec observer, markers |
| `writes` | symbol, file, referent | file, referent | markers |
| `calls` | symbol, file, module | symbol, file, module | markers |
| `uses` | symbol, file, module | symbol, module, referent, file | markers |
| `implements` | symbol, file, module | symbol, referent, file | markers |
| `realizes` | symbol, file, module | referent | `wm map`, `WM-MAPS` |
| `owned_by` | symbol, file, module, referent | referent, symbol | markers |

Constraints are covered too: a `scope_predicate` outside the vocabulary is rejected when the
constraint is asserted — a constraint scoped to a verb that can never be written can never fire,
so it is a latent bug, not a belief.

## What a rejection looks like

Rejections are **teaching errors** — they always name the allowed set so a probabilistic caller
can self-correct and retry:

```
$ python3 wm.py observe stripe-api frobnicates auth.py
{"error": "ontology_violation", "detail": "unknown predicate 'frobnicates' — allowed: calls,
depends_on, executes, implements, imports, includes, owned_by, provides, reads, realizes,
references, uses, writes. To extend the vocabulary deliberately: `wm ontology --add ...`."}
```

Exit code 2. In the marker channel the harvester logs the same detail to stderr and skips the
one bad marker — a hallucinated marker can never break a hook or reach the ledger. Nothing is
inserted on rejection (the check runs before any write).

## Type inference for stubs

When a triple names an unknown entity, the stub's kind comes from the ontology instead of a
blind default — the same move as `rdfs:domain`/`rdfs:range` entailment:

- `billing/refund.py depends_on stripe-sdk` → `stripe-sdk` stubbed as a **referent** (before
  this layer, it would have been silently mis-typed).
- `auth.py imports hashlib` → `hashlib` stubbed as a **module**.
- An explicit `--subj-kind`/`--obj-kind` wins when it is inside the domain/range; outside it,
  the write is rejected with the rule spelled out.

## Extending the vocabulary — a deliberate act

The vocabulary is a guardrail, not a straitjacket. Projects add domain verbs explicitly:

```bash
python3 wm.py ontology                                          # list the live vocabulary
python3 wm.py ontology --add guards --domain symbol,file --range symbol
```

`--add` persists to `ontology.json` next to `model.db` (`{"verb": {"domain": [...],
"range": [...]}}`); the file merges over the core vocabulary on load and survives sessions.
Extension is never a side effect of a marker — an unknown verb in a marker is dropped, and
only an explicit `wm ontology --add` (or hand-editing `ontology.json`) admits it. A malformed
`ontology.json` is ignored entry-by-entry and can never take the store or a hook down.

## Scope and honest limits

- Validation is **write-time only**. Rows written before this layer existed are untouched;
  they are re-checked only if re-asserted.
- The ontology types *triples*, it does not judge *truth* — `auth.py imports hashlib` passes
  the ontology even if false. Truth is the job of the two-axis confidence model and oracle
  evidence (`references/confidence-model.md`); the ontology only guarantees the fact is
  *well-formed enough to be evaluable*.
- Kinds are the four entity kinds (`symbol`, `file`, `module`, `referent`); the ontology does
  not subclass them (no OWL class hierarchy). That is deliberate scope control, not a roadmap
  gap.
