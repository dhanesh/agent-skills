# world-model-ledger — Design Spec

**Date:** 2026-07-01
**Status:** Brainstorming → awaiting approval before implementation
**Working name:** `world-model-ledger` (provisional; alternatives: `codebase-world-model`, `world-model-kit`)
**Authoring tool:** `repo2skill` machinery (SKILL.md conventions, `validate-skill.sh` / `scan-leaks.sh` / `prompting-playbook.sh` quality gate; a stdlib‑Python install gate as in `context-hygiene-kit`)
**Repo:** `dhanesh/agent-skills` (installed via `npx skills add dhanesh/agent-skills --skill world-model-ledger`)

> This is a design document. Per the brainstorming decision, nothing is built yet — this
> spec is for review. Section 12 lists the open questions to settle before the build.

---

## 1. Purpose

Install a **persistent world model for a coding agent**, backed by SQLite, into a project.
The world model records what the agent has learned about the codebase and the reality it
maps onto — **entities**, the **interactions** between them, the **constraints** that should
hold, and the **real‑world referents** the code stands for — and, for every interaction and
constraint, *how sure we are it exists*, *how sure we are it is correct*, *whether it has
been validated*, and *what evidence backs it*.

The skill answers, at any moment in a session:

> *"For the files and symbols I am about to touch, what do we already believe — what is
> validated, what is merely observed and unverified, and what is currently contradicted?"*

and, over a session's lifetime:

> *"Where does the code contradict a constraint we trust, and what is the minimal fix that
> raises the model's correctness?"*

### The defining constraint (the whole value proposition)

**Code‑observed relationships are not ground truth.** Seeing `a()` call `b()` in source
tells you the relationship *exists*, not that it is *correct, intended, or desirable*. The
literature calls this the **test‑oracle problem**: a fact extracted from an implementation
describes the implementation, which may embed the very bug you are hunting. So the model
keeps **two independent confidence axes** — *observed* and *normative* — and a **validation
status**. Observation alone can raise observed confidence to 1.0 while normative confidence
stays low and status stays `unverified`. Only evidence from an oracle (a passing test, a
doc, a spec, a human, green CI) raises normative confidence. This is enforced as an
invariant (§9), not a convention.

### Non‑goals (YAGNI)

- **Not** a static analyzer, LSP server, or compiler. It does not parse the whole AST or
  resolve every symbol. It records what the agent touches and what the agent (or a
  deterministic hook) can observe cheaply.
- **Not** a model‑driven fact extractor. Hooks never call a model to "infer" the graph —
  that is how you get invented facts. Rich facts are recorded only when the agent
  *explicitly* asserts them (§6). Deterministic capture handles the rest.
- **No network / no external authorities in v1.** Normative confidence is raised by
  *in‑repo* evidence (tests, docs, commits, human confirmation). A `base-in-reality`‑style
  authoritative‑source validator is a **pluggable validator** for later (§8, §12), not v1.
- **No vector store / embeddings in v1.** Retrieval is FTS5 + graph queries over SQLite
  (stdlib only, no pip). A `sqlite-vec` semantic layer is a later option (§12).

---

## 2. The four framing decisions (settled in brainstorming)

| # | Decision | Consequence in this spec |
|---|----------|--------------------------|
| **Deliverable** | Design doc first, build after approval | This document; §11 is the build plan. |
| **Update engine** | **Hybrid** — deterministic capture of touched files/symbols at low confidence; richer interactions/constraints only via explicit agent markers/CLI | §5 (hooks) + §6 (markers/CLI). No model guesses in a hook. |
| **Validation** | **Evidence‑driven, standalone** — tests / CI / docs / human / commit provenance; pluggable validators later | §7 (confidence) + §8 (validators). No network in v1. |
| **Granularity** | **Symbols + files + external referents** | §3 (`entity.kind ∈ symbol|file|module|referent`). |

---

## 3. Data model

The model is a small **property graph with provenance and two‑axis confidence**, plus a
constraint layer and a contradiction log. It fuses three ideas from the research:

- **entity / interaction / evidence** triple — the proven minimal shape (MCP memory server's
  entity/relation/observation; CPG/Kythe's node/edge/fact property‑graph).
- **stable string symbol IDs** so identity survives edits and joins are cheap (SCIP:
  `scheme package descriptor` grammar; `local <id>` for function‑scoped symbols).
- **two confidence columns + a PROV‑style evidence table + soft‑invalidation** so nothing is
  ever a single conflated "confidence" scalar and nothing is hard‑deleted (Google Knowledge
  Vault's observed‑vs‑truth split; Graphiti's bi‑temporal interval‑closure; W3C PROV).

### 3.1 Entities (nodes) — `symbol | file | module | referent`

```sql
CREATE TABLE entity (
  id           INTEGER PRIMARY KEY,
  kind         TEXT NOT NULL CHECK (kind IN ('symbol','file','module','referent')),
  symbol_id    TEXT UNIQUE,          -- stable SCIP-style id, e.g. 'py . myproj 1.0 auth/hash.py hash_pw().'
                                     --   or 'file:auth/hash.py' or 'referent:stripe/refunds-api'
  name         TEXT NOT NULL,        -- display name
  path         TEXT,                 -- file for code entities; NULL for pure referents
  lang         TEXT,
  entity_type  TEXT,                 -- LSP-ish: function|class|method|module|file|service|api|datastore|concept
  attrs        TEXT,                 -- JSON1 long-tail props; validate with json_valid()
  first_seen   TEXT NOT NULL,        -- ISO-8601 (application-stamped; see temporal caveat §9)
  last_seen    TEXT NOT NULL
);
```

**Real‑world referents** are first‑class entities with `kind='referent'`: an external
service, third‑party API, data store, protocol, or domain concept the code *stands for*
("this module realizes the Stripe refunds flow", "this table maps to the GDPR
right‑to‑erasure concept"). A code symbol is tied to a referent by a `maps_to` / `realizes`
interaction — and that link is itself `unverified` until evidence backs it.

### 3.2 Interactions (edges) — the relationships, with two‑axis confidence

```sql
CREATE TABLE interaction (
  id             INTEGER PRIMARY KEY,
  subject_id     INTEGER NOT NULL REFERENCES entity(id),
  predicate      TEXT NOT NULL,       -- calls|imports|depends_on|implements|reads|writes|
                                     --   returns|raises|configures|maps_to|realizes|...
  object_id      INTEGER NOT NULL REFERENCES entity(id),

  -- TWO INDEPENDENT AXES (the core requirement) --------------------------------
  observed_conf  REAL NOT NULL DEFAULT 0.0,  -- 0..1  did we SEE this? (epistemic; ↑ with observations)
  normative_conf REAL NOT NULL DEFAULT 0.0,  -- 0..1  is it CORRECT/intended? (↑ only via validation §7)

  -- VALIDATION STATUS (governs correctness uncertainty alongside normative_conf) -
  validation     TEXT NOT NULL DEFAULT 'unverified'
                   CHECK (validation IN ('unverified','validated','contradicted','stale')),

  entrenchment   INTEGER NOT NULL DEFAULT 0,  -- retraction priority on conflict (AGM); human>test>doc>static>infer

  -- SOFT INVALIDATION (never hard-delete; Graphiti interval-closure / PROV) -------
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  invalidated_at TEXT,                        -- set instead of DELETE; row stays queryable
  UNIQUE (subject_id, predicate, object_id)
);
```

### 3.3 Constraints — what *should* hold (SHACL‑inspired), same confidence machinery

```sql
CREATE TABLE constraint_ (
  id             INTEGER PRIMARY KEY,
  name           TEXT NOT NULL,
  kind           TEXT NOT NULL,       -- functional|cardinality|forbids|requires|disjoint|type|range|value_set|invariant
  scope_predicate TEXT,               -- which interaction predicate it governs (nullable for whole-entity rules)
  params         TEXT,                -- JSON: {maxCount:1} / {forbidden:['md5']} / {requires:'sanitize'} ...
  severity       TEXT NOT NULL DEFAULT 'violation' CHECK (severity IN ('violation','warning','info')),
  message_tmpl   TEXT NOT NULL,       -- human-readable "why", filled with the offending fact

  observed_conf  REAL NOT NULL DEFAULT 0.0,   -- was this constraint actually observed to be asserted somewhere?
  normative_conf REAL NOT NULL DEFAULT 0.0,   -- how sure are we the constraint itself is right/desired?
  validation     TEXT NOT NULL DEFAULT 'unverified'
                   CHECK (validation IN ('unverified','validated','contradicted','stale')),
  entrenchment   INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  invalidated_at TEXT
);
```

Constraints get the same four‑field treatment because a constraint is also a belief that can
be merely observed (e.g. inferred from one code path), validated (backed by a test or a
documented rule), or contradicted (the code violates it and the code is right).

### 3.4 Evidence — PROV‑style provenance, many rows per fact

```sql
CREATE TABLE evidence (
  id           INTEGER PRIMARY KEY,
  fact_kind    TEXT NOT NULL CHECK (fact_kind IN ('interaction','constraint')),
  fact_id      INTEGER NOT NULL,       -- FK by (fact_kind,fact_id)
  evidence_kind TEXT NOT NULL          -- test|ci|doc|file_loc|commit|human|static|runtime|agent_assert
                   CHECK (evidence_kind IN ('test','ci','doc','file_loc','commit','human','static','runtime','agent_assert')),
  ref          TEXT NOT NULL,          -- pointer: 'tests/test_auth.py::test_hash', 'auth/hash.py:14',
                                     --   commit sha, doc path/URL, CI run id
  polarity     TEXT NOT NULL DEFAULT 'supports' CHECK (polarity IN ('supports','refutes')),
  agent        TEXT,                   -- prov:wasAttributedTo — 'harvester','wm-cli','human','pytest'
  activity     TEXT,                   -- prov:wasGeneratedBy — 'edit auth/hash.py','pytest run','turn 7'
  weight       REAL NOT NULL DEFAULT 0.5,
  observed_at  TEXT NOT NULL
);
CREATE INDEX idx_evidence_fact ON evidence(fact_kind, fact_id);
```

Evidence is **append‑only**. Confidence is *derived* from evidence (§7), so the audit trail
and the score can never drift apart. `evidence_kind` is what upgrades a fact along the two
axes: a `file_loc`/`static`/`runtime` row raises **observed**; a `test`/`ci`/`doc`/`human`
row raises **normative** and can flip `validation`.

### 3.5 Contradictions — the actionable output (ATMS nogood / SHACL result)

```sql
CREATE TABLE contradiction (
  id            INTEGER PRIMARY KEY,
  constraint_id INTEGER REFERENCES constraint_(id),  -- which rule fired (nullable for fact-vs-fact)
  detected_by   TEXT NOT NULL,          -- 'constraint_eval'|'test_failure'|'agent'|'stale_sweep'
  severity      TEXT NOT NULL,
  message       TEXT NOT NULL,          -- rendered from constraint.message_tmpl + offending fact(s)
  proposed_fix  TEXT,                   -- rendered suggestion (retract least-entrenched member, etc.)
  detected_at   TEXT NOT NULL,
  resolved_at   TEXT,
  resolution    TEXT CHECK (resolution IN ('retract','supersede','accept_both','fixed_code','defer'))
);
CREATE TABLE contradiction_member (   -- the minimal conflict set (nogood)
  contradiction_id INTEGER NOT NULL REFERENCES contradiction(id),
  fact_kind        TEXT NOT NULL,
  fact_id          INTEGER NOT NULL,
  PRIMARY KEY (contradiction_id, fact_kind, fact_id)
);
```

### 3.6 Retrieval indexes — FTS5 (stdlib, no pip)

```sql
-- External-content FTS5 over entity names + evidence refs, kept in sync by triggers.
CREATE VIRTUAL TABLE entity_fts USING fts5(name, path, entity_type, content='entity', content_rowid='id');
-- + AFTER INSERT/UPDATE/DELETE triggers using the fts5 'delete' command (verify syntax at build).
```

Graph reachability ("what touches this symbol, 2 hops out") uses a **depth‑capped recursive
CTE** with `UNION` (cycle‑safe) over `interaction`, filtered to `invalidated_at IS NULL`.
No vector search in v1.

---

## 4. Confidence & validation model (the epistemics)

Three quantities per fact, kept deliberately separate (Knowledge Vault's central lesson:
never collapse "did we observe it" into "is it true"):

| Quantity | Meaning | Raised by | Never raised by |
|---|---|---|---|
| **observed_conf** | We have *seen* this relationship (in code / at runtime). Epistemic — reducible with more sightings. | `file_loc`, `static`, `runtime`, repeated observation, cross‑source agreement (TruthFinder‑style) | — |
| **normative_conf** | The relationship is *correct / intended / desirable*. | `test`(pass), `ci`(green), `doc`, `human`, `spec` evidence | **code observation alone** ← the invariant |
| **validation** | Discrete status gate: `unverified → validated → contradicted / stale`. | supporting oracle evidence → `validated`; refuting evidence / failed test / constraint hit → `contradicted`; invalidation on file change → `stale` | — |

**Derivation (deterministic, no model).** On each evidence write, recompute the parent fact:

- `observed_conf = 1 − Π(1 − w_i)` over `supports` evidence with `evidence_kind ∈ observation set` (noisy‑OR: more independent sightings → higher, saturating).
- `normative_conf` = same noisy‑OR over `supports` evidence with `evidence_kind ∈ oracle set`, **minus** a penalty from `refutes` evidence.
- `validation` = `contradicted` if any live `refutes`/failed‑test/constraint hit; else `validated` if `normative_conf ≥ τ_validate`; else `stale` if the anchored file changed and no re‑observation; else `unverified`.
- `entrenchment` = max rank over supporting evidence (`human=4 > test=3 > doc=2 > static/runtime=1 > agent_assert=0`) — the AGM retraction priority used to pick the loser in a conflict.

Thresholds (`τ_validate`, penalties, weights) live in one config block so they are tunable
without touching logic. Recompute is a pure function of the evidence rows — idempotent and
testable.

---

## 5. Hooks (deterministic; Claude Code lifecycle)

Wired into `settings.json` exactly like `context-hygiene-kit`. All hooks are best‑effort,
never block the turn, always exit `{"continue": true}`.

### 5.1 Pre‑call hook — `PreToolUse` (retrieve & summarize) — **the requirement's pre‑call hook**

- **Fires:** before `Edit | Write | MultiEdit` (and optionally `Read`) tool calls.
- **Input:** tool name + tool input (the file path(s), and any symbol names parseable from
  the edit target) on stdin.
- **Job (deterministic query, no model):** resolve the touched file(s)/symbol(s) to entities,
  then query the ledger and inject an `additionalContext` summary partitioned exactly as the
  requirement asks:
  - **✓ validated** — what we trust about these files/symbols.
  - **? unverified** — observed but not oracle‑backed (treat with suspicion).
  - **✗ contradicted** — open contradictions touching these files/symbols, with the proposed fix.
- Retrieval = FTS5 match on path/symbol + 1–2 hop recursive‑CTE neighborhood, `invalidated_at
  IS NULL`, ranked by (contradicted → unverified‑high‑traffic → validated) and token‑budgeted.

### 5.2 Post‑call hook — `PostToolUse` (extract, update, mark) — **the requirement's post‑call hook**

- **Fires:** after `Edit | Write | MultiEdit`.
- **Job (deterministic, no invented facts):**
  1. **Extract changes** — which file(s)/symbol(s) were touched (from the tool input and, if
     available, the diff). Upsert their entities; bump `last_seen`.
  2. **Update records** — for interactions anchored to a changed file, add a `refutes`‑free
     `static` re‑observation (keeps `observed_conf` warm) **or**, if the anchor line/symbol
     no longer resolves, mark the fact `stale` and set `invalidated_at` (soft‑invalidate).
  3. **Mark low‑confidence / unverified** — any *newly* observed edge is written at
     `observed_conf` from the sighting and **`normative_conf = 0`, `validation='unverified'`**.
     The hook never promotes a fact to validated and never fabricates an interaction it did
     not directly observe.
  4. **Constraint sweep (cheap)** — evaluate constraints whose `scope_predicate` touches the
     changed entities; on a hit, open a `contradiction` row.

### 5.3 Summarization — `Stop` (default) + optional incremental

- **Stop hook (default, every turn):** the heavier consolidation — recompute derived
  confidence from evidence, run the full contradiction sweep, apply decay to long‑unseen
  observations, and refresh a compact `.world-model/digest.md` (open contradictions +
  proposed fixes + newest unverified). This is the summarization home.
- **Optional lightweight incremental (long runs):** a throttled path in the PostToolUse hook
  (e.g. every N edits) does a *scoped* recompute for just the touched facts, so a very long
  session does not wait for `Stop` to reflect new evidence. Off by default; enabled by a
  `WM_INCREMENTAL=1` env flag.
- **`SessionStart` (optional):** inject the digest so a resumed session starts already aware
  of open contradictions.

### 5.4 Trust boundary (load‑bearing)

Only the **trusted channel** feeds facts: user text, assistant text, and the tool *inputs the
agent itself chose*. **`tool_result` content is never harvested into facts** — a file's
contents or a command's output cannot smuggle a `WM-OBSERVE:` marker or forge evidence. Hooks
are deterministic; no model summarizes the session into the graph. (Same posture as
`context-hygiene-kit`'s two‑channel rule.)

---

## 6. The hybrid update path — agent markers + `wm` CLI

Deterministic hooks capture the *skeleton* (files/symbols touched, low confidence). The
agent enriches it *explicitly* — never a model guessing inside a hook. Two ergonomic surfaces,
lowest‑friction first:

**(a) Marker lines** (harvested from the trusted channel by the Stop hook, like
`context-hygiene-kit`'s `DECISION:`). Must start the line:

```
WM-OBSERVE:   hash_pw calls bcrypt.hashpw          [auth/hash.py:14]
WM-CONSTRAINT: forbids  password-hash uses md5|sha1  severity=violation
WM-MAPS:      billing/refund.py realizes stripe/refunds-api
WM-VALIDATED: hash_pw->bcrypt  by test tests/test_auth.py::test_hash
WM-CONTRADICTS: reset_pw uses sha1  (violates: no-weak-password-hash)
```

**(b) `wm` CLI** (stdlib Python) for scripted/precise use and for validators:

```
wm observe   <subj> <pred> <obj> [--evidence file:line] [--conf 0.7]
wm constraint <kind> <params-json> [--severity ...] [--message ...]
wm validate  <fact-selector> --by test:<id>|doc:<path>|ci:<run>|human   # raises normative + validation
wm refute    <fact-selector> --by ...                                    # → contradicted
wm map       <symbol> --to <referent>
wm contradictions [--open] [--touching <path>]                           # list + proposed fixes
wm resolve   <contradiction-id> --as retract|supersede|fixed_code|defer
wm query     --touching <path|symbol>                                    # what the pre-call hook shows
wm stats | wm export --json
```

Both paths write the *same* rows through the *same* confidence derivation. The CLI is also
how **pluggable validators** (§8) feed evidence.

---

## 7. Detecting contradictions, proposing fixes, improving normative correctness (acceptance)

The acceptance criteria — *detect contradictions, propose fixes, improve normative correctness
over time* — are the closed loop:

1. **Detect.** Constraints are evaluated over interactions (SHACL‑style: `functional`/
   `cardinality` = "two conflicting values for one subject+predicate"; `forbids`/`requires` =
   value/pattern rules; `disjoint` = two predicates must not share an object). A failed test
   recorded against a fact, or an observed edge that violates a **validated** constraint, also
   fires. Each detection writes a `contradiction` + its minimal `contradiction_member` set
   (the nogood), with a rendered `message`.
2. **Propose a fix.** From the conflict set, rank members by `entrenchment` then
   `normative_conf`; the **least‑entrenched / lowest‑normative** member is the proposed thing
   to change (AGM minimal change / TruthFinder lowest‑confidence). `proposed_fix` is a
   human‑readable string: *"`reset_pw` uses `sha1` but validated constraint
   `no-weak-password-hash` forbids it (backed by `tests/test_auth.py`, doc `SECURITY.md`).
   Fix: replace `sha1` with `bcrypt` in `auth/reset.py:22`."* Surfaced via the pre‑call hook
   (§5.1, the ✗ partition) and `wm contradictions`.
3. **Improve over time.** When the agent fixes the code, the next PostToolUse re‑observes the
   corrected edge and the contradiction is resolved (`fixed_code`); validating evidence
   (a new passing test) raises `normative_conf` and flips `validation→validated`. Net effect:
   unverified/contradicted mass converts to validated mass — **the model's normative
   correctness rises monotonically as the session does real work**. `wm stats` reports the
   ratio (validated / unverified / contradicted) so the improvement is measurable and gate‑able.

---

## 8. Validators (v1 in‑repo; pluggable later)

A **validator** is anything that writes `supports`/`refutes` evidence to raise/lower
normative confidence. v1 ships in‑repo, offline validators driven through `wm validate`:

- **test / CI validator** — parse a pytest/junit result (or a `wm validate ... --by ci:<run>`
  call) and attach `test`/`ci` evidence to the facts a test exercises.
- **doc validator** — a `maps_to`/constraint reference found in a doc path becomes `doc`
  evidence.
- **commit validator** — commit sha as provenance on an observation.
- **human validator** — `wm validate ... --by human` (highest entrenchment).

**Pluggable later (§12):** a `base-in-reality` bridge that fetches an authoritative source
(NIST/RFC/arxiv) and writes `doc` evidence with the citation — turning "code says X" into
"X is (in)correct per <fetched source>". Deliberately out of v1 to keep the skill offline and
stdlib‑only; the evidence table already has the shape to receive it.

---

## 9. Invariants (do not weaken)

1. **Code observation never raises normative confidence.** A hook may set
   `observed_conf → 1.0`, but `normative_conf` moves *only* on oracle evidence
   (`test|ci|doc|human|spec`). This is the anti‑oracle‑problem guarantee and the reason the
   skill exists.
2. **No invented facts in hooks.** Hooks capture only what is deterministically parseable
   (touched files/symbols, diff, test results). Interactions/constraints beyond that come
   from explicit agent markers/CLI. No model writes to the graph from inside a hook.
3. **Append‑only evidence; soft‑invalidate, never hard‑delete.** Superseded facts get
   `invalidated_at`; the audit trail and "what did we believe at commit X" stay queryable
   (Graphiti/PROV). Confidence is always *derived* from live evidence, never hand‑set out of
   band.
4. **Trusted channel only.** `tool_result` content is never harvested into facts or evidence
   (§5.4).
5. **No silent truncation.** If the pre‑call summary or a sweep is budget‑capped, it says what
   was dropped.

**Temporal caveat (from research):** SQLite has no stable cross‑statement transaction time,
so all timestamps are **application‑stamped** once per operation rather than via
`CURRENT_TIMESTAMP` across triggers — accept minor skew; do not attempt SQL‑standard
system‑versioning.

---

## 10. Retrieval & performance notes

- **Two‑layer retrieval:** FTS5 (`bm25`) for lexical match on names/paths/refs; recursive‑CTE
  graph walk (depth‑capped, `UNION`, both edge ends indexed) for neighborhoods. Vector search
  is explicitly deferred.
- **Cost:** hooks are stdlib `sqlite3`, target ~10–50 ms/turn (same envelope as
  `context-hygiene-kit`'s harvester). The `Stop` consolidation is the only heavier pass.
- **Store location:** `.world-model/model.db` per project (added to `.gitignore` by the
  installer, like `.context/`). Memory never bleeds across repos.

---

## 11. Build plan (after approval)

Mirror `context-hygiene-kit`'s layout and its **test‑suite install gate**:

```
world-model-ledger/
  SKILL.md                     # the prompt: purpose, invariants, hooks, marker/CLI conventions, operating it
  README.md                    # human-facing
  references/
    schema.md                  # full DDL + derivation formulas + threshold config
    confidence-model.md        # the two-axis epistemics, worked examples
    capture.md                 # markers, CLI, trust boundary
    contradiction-loop.md      # detect → propose → improve, constraint kinds
  assets/
    world_model.py             # stdlib sqlite3 store + confidence derivation + graph/FTS queries
    wm.py                      # the `wm` CLI (thin wrapper over world_model.py)
    harvest.py                 # deterministic marker harvester (trusted channel)
    hooks/{pretooluse,posttooluse,stop,session_start}.sh
    settings.hooks.json        # PreToolUse/PostToolUse/Stop/SessionStart wiring
    schemas/*.json             # fact/evidence JSON shapes
    test_world_model.py        # install gate: schema, two-axis invariant, noisy-OR derivation,
                               #   soft-invalidation, contradiction detect+propose, trust boundary,
                               #   idempotent ingest, FTS sync, stale-on-change
  scripts/install.sh           # copy + additive settings merge + run the gate (project & --global)
```

Build order: (1) `world_model.py` + tests (schema, derivation, invariants) → (2) `wm` CLI →
(3) harvester + hooks → (4) install.sh + gate → (5) SKILL.md/README/references → (6)
`make gate-skill SKILL=world-model-ledger` green.

---

## 12. Open questions (settle before building)

1. **Name.** `world-model-ledger` vs `codebase-world-model` vs `world-model-kit`?
2. **Symbol resolution depth.** v1 resolves symbols only by *name within a file* (cheap,
   greppable, no parser). Full tree‑sitter symbol IDs (SCIP‑grade) is a bigger dependency —
   defer? (Recommend: name‑within‑file for v1, note the SCIP upgrade path.)
3. **Pre‑call hook on reads.** Summarize on `Read` too, or only on write tools? Reads are far
   more frequent — summarizing on every read could be noisy. (Recommend: write tools by
   default; `WM_PRECALL_READS=1` opt‑in.)
4. **Constraint seeding.** Ship a small starter set of language/security constraints
   (e.g. weak‑hash `forbids`), or start empty and let the agent assert them? (Recommend: a
   tiny, clearly‑labeled optional starter pack, off by default.)
5. **`base-in-reality` bridge** as a v1.1 validator — confirm it stays out of v1.
6. **Global vs project install** — mirror `context-hygiene-kit`'s dual scope, or project‑only
   for v1?

---

## Appendix — research grounding

Design choices trace to fetched primary sources (full findings in the session research):

- **Two‑axis confidence (observed vs normative).** Google Knowledge Vault — separates
  per‑source *extraction confidence* from fused *truth probability*
  (`cs.ubc.ca/~murphyk/papers/kv-kdd14.pdf`, `arxiv.org/pdf/1503.00302`). NELL's single
  conflated scalar is the anti‑pattern (`cacm.acm.org/research/never-ending-learning`).
- **"Code ≠ intended" (the reason normative stays low).** The test‑oracle problem — specs
  derived from an implementation inherit its bugs
  (`albany.edu/faculty/offutt/research/papers/testOracle.pdf`).
- **Evidence/provenance + soft‑invalidation.** W3C PROV (`w3.org/TR/prov-o/`,
  `wasGeneratedBy`/`wasDerivedFrom`/`wasInvalidatedBy`); Graphiti bi‑temporal interval‑closure
  (`github.com/getzep/graphiti`).
- **Belief revision / retraction priority.** AGM epistemic entrenchment (SEP:
  `plato.stanford.edu/entries/logic-belief-revision/`); JTMS IN/OUT + dependency‑directed
  backtracking (Doyle 1979); ATMS nogoods (de Kleer, AAAI‑86 `cdn.aaai.org/AAAI/1986/AAAI86-003.pdf`).
- **Contradiction detection.** SHACL validation report + severity + constraint components
  (`w3.org/TR/shacl/`); TruthFinder source⇄fact reinforcement
  (`github.com/IshitaTakeshi/TruthFinder`).
- **Entity/edge + stable symbol IDs.** SCIP symbol grammar + role bitset
  (`github.com/sourcegraph/scip/blob/main/scip.proto`); Kythe nodes/edges/facts
  (`github.com/kythe/kythe` schema sources); CPG layered edges (Joern); MCP memory
  entity/relation/observation triple (`github.com/modelcontextprotocol/servers`).
- **SQLite mechanics.** FTS5 external‑content + sync triggers + `bm25`; JSON1 + VIRTUAL
  generated columns; depth‑capped recursive CTE with `UNION`; append‑only history via
  triggers (`sqlite.org` fts5/gencol/json1/lang_with docs — verify exact syntax at build
  time; several were egress‑blocked during research and are search‑surfaced).

> **Research caveat:** the org egress proxy blocked direct fetches of several `sqlite.org`,
> `arxiv.org`, and `w3.org` pages; those specifics are search‑surfaced and must be
> re‑verified against primary docs during the build (esp. FTS5 `'delete'`‑command syntax and
> partial‑index‑on‑`json_extract` version support).
</content>
</invoke>
