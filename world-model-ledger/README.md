# world-model-ledger

A persistent, SQLite-backed **world model** for a coding agent. It remembers what an agent
learns about a codebase across turns and sessions — **entities** (symbols, files, modules, and
real-world referents like external services / APIs / data stores), the **interactions**
between them, and the **constraints** that should hold — and, for every interaction and
constraint, tracks *how sure we are it exists*, *how sure we are it is correct*, *whether it's
been validated*, and *what evidence backs it*.

The point of the whole thing: **code-observed relationships are not treated as ground truth.**
Two independent confidence axes (`observed` vs `normative`) plus a validation status let the
model distinguish *"we saw this in the code"* from *"we verified this is correct"* — so it can
flag what is merely observed-but-unverified, detect contradictions, propose located fixes, and
raise its own correctness as the session does real work.

The write boundary is **neuro-symbolic**: every triple is validated against a predicate
**ontology** (a closed, deliberately-extensible vocabulary with RDFS-style domain/range per
verb) before it enters the ledger, so a hallucinated verb or a semantically impossible pairing
(a referent that `imports` a file) is rejected with the allowed set named — never silently
stored. See [`references/ontology.md`](references/ontology.md).

## Install

```bash
npx skills add dhanesh/agent-skills --skill world-model-ledger
```

Then run the one-time installer (project or global scope):

```bash
scripts/install.sh /path/to/project      # just this repo (default: cwd)
scripts/install.sh --global               # every project, via ~/.claude
scripts/install.sh --with-constraints     # also load the optional starter constraints
```

Requires `python3` (stdlib only — no pip, no network); `jq` optional for clean settings
merging. Restart Claude Code afterward so the hooks load. The install runs a 108-test gate.

## What gets installed

- **Four lifecycle hooks** wired into `settings.json`:
  - **PreToolUse** — before an edit, summarizes the model's ✓validated / ?unverified /
    ✗contradicted items for the touched files/symbols.
  - **PostToolUse (universal, matcher `*`)** — the single observer for the whole tool stream:
    files any tool reads/edits become entities; `Bash` commands become `runtime`
    `executes`/`reads` edges (a recognised verifier's exit status → oracle evidence,
    green → validated, red → contradicted); fetched URLs become referents. Input only, never
    output — no invented facts.
  - **Stop** — harvests any optional markers, consolidates confidence, refreshes the digest.
  - **SessionStart** — **auto-bootstraps** an empty model (create + seed on first run), then
    injects the digest so a resumed session starts aware of contradictions.
- **A stdlib-Python store + `wm` CLI** (`world_model.py`, `wm.py`) — entities / interactions /
  constraints / evidence / contradictions in one SQLite file at `.world-model/model.db`
  (gitignored, per project).

## Using it

Seed the whole repo once (optional, avoids a cold start), then record facts inline with
marker lines (harvested every turn) or the `wm` CLI:

```bash
python3 wm.py build .    # register files + structural edges (observation-only, idempotent)
```

```
WM-OBSERVE: hash_pw uses bcrypt @ auth/hash.py:14
WM-VALIDATED: hash_pw uses bcrypt by test:tests/test_auth.py::test_hash
WM-CONSTRAINT: no-weak-hash | forbids | uses | {"patterns":["md5","sha1"]} | {subject} uses weak hash {matched} | violation
WM-MAPS: billing/refund.py -> stripe/refunds-api
```

```bash
python3 wm.py stats                 # validated / unverified / contradicted counts
python3 wm.py contradictions --open # open contradictions + proposed fixes
cat .world-model/digest.md          # the current digest
```

## How it's built

Grounded in prior art — Google Knowledge Vault's observed-vs-truth split, the test-oracle
problem, W3C PROV, AGM/JTMS/ATMS belief revision, SHACL constraint validation, RDFS
domain/range typing (the ontology write guardrail — the neuro-symbolic pattern argued for in
Coyle's "Why Agentic Systems Need Ontologies", AI Engineer 2026), SCIP/Kythe
symbol models, and standard SQLite FTS5 / recursive-CTE patterns. The full design is in
[`docs/superpowers/specs/2026-07-01-world-model-ledger-design.md`](../docs/superpowers/specs/2026-07-01-world-model-ledger-design.md),
and each subsystem is documented under [`references/`](references/).

## Does it improve outcomes?

A controlled with/without eval (24 agent runs, deterministic graders) is in
[`eval/`](eval/), with results in
[`docs/world-model-ledger/2026-07-01-outcome-eval.md`](../docs/world-model-ledger/2026-07-01-outcome-eval.md).
Headline: on a task whose key fact was **non-local** (a deprecated dependency knowable only via
the model), surfacing the pre-call context moved the agent from **0/2 → 2/2**; when the info was
already visible in the repo it made no difference (ceiling), and it caused **no harm** when it
had nothing relevant. The model aids outcomes specifically by delivering knowledge the agent
doesn't already have in context.

See [`SKILL.md`](SKILL.md) for the agent-facing usage and invariants.
