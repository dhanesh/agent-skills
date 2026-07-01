# Schema reference

A small **property graph with provenance and two-axis confidence**, in plain SQLite (stdlib
`sqlite3`, no pip). Full DDL is in `assets/world_model.py` (the `SCHEMA` / `FTS_SCHEMA`
constants); this is the map.

## Tables

- **`entity`** — nodes. `kind ∈ {symbol, file, module, referent}`. A `symbol_id` gives stable
  identity and follows SCIP's `<scheme> <package> <descriptor>+` grammar **shape** with SCIP
  descriptor suffixes — `/` namespace, `#` type, `().` method, `.` term (e.g. a file →
  `wml . auth/hash.py/`, a function → `wml . auth/hash.py/hash_pw().`, a referent →
  `wml-referent . stripe/refunds-api/`). The `package` field is the placeholder `.` (SCIP's
  missing-value token) until a real manager/name/version is resolved — so ids are
  parseable/greppable and close to conformant, though not yet fully SCIP-interoperable. v1
  resolves symbols by name-within-file. **Real-world referents** (external services, APIs, data stores, domain
  concepts) are `kind='referent'`: linked from code by a `realizes` interaction (agent-recorded,
  semantic) or a `depends_on` interaction (auto-recorded by `wm build` for a package / container
  image / CI action / Terraform module literally declared in the source).
- **`interaction`** — edges (`subject → predicate → object`), e.g. `calls`, `imports`,
  `depends_on`, `implements`, `reads`, `writes`, `uses`, `realizes`. Carries
  `observed_conf`, `normative_conf`, `validation`, `entrenchment`, and `invalidated_at`
  (soft delete). `UNIQUE(subject_id, predicate, object_id)`.
- **`constraint_`** — what *should* hold (SHACL-inspired). `kind ∈ {functional, cardinality,
  forbids, requires, disjoint, type, range, value_set, invariant}`, with `params` (JSON),
  `severity`, and a `message_tmpl`. Same four confidence/validation fields as interactions —
  a constraint is also a belief that can be observed, validated, or contradicted.
- **`evidence`** — PROV-style provenance, **append-only**, many rows per fact.
  `evidence_kind` (`test|ci|doc|file_loc|commit|human|static|runtime|agent_assert`),
  `ref` (a `file:line`, commit sha, test id, doc path/URL), `polarity` (`supports|refutes`),
  `agent`, `activity`, `weight`. This is the *only* thing that moves confidence.
- **`contradiction`** + **`contradiction_member`** — a detected conflict (ATMS nogood / SHACL
  result): `severity`, rendered `message`, `proposed_fix`, and the minimal set of member
  facts. `resolved_at` / `resolution` close it.
- **`entity_fts`** — FTS5 external-content index over entity `name`/`path`/`entity_type`,
  kept in sync by triggers. Retrieval falls back to `LIKE` if FTS5 is unavailable.

## Query patterns

- **Retrieval for the pre-call hook** (`query_touching` / `precall`): resolve a file/symbol
  token to entities (exact → FTS5 → `LIKE`), pull live interactions touching them, partition
  by `validation`, attach open contradictions.
- **Graph reachability**: depth-capped recursive CTE over `interaction` with `UNION`
  (cycle-safe), filtered to `invalidated_at IS NULL`. Both edge ends are indexed.
- **No hard deletes**: superseded facts get `invalidated_at`; "what did we believe before"
  stays queryable.

## Timestamp caveat

SQLite has no stable cross-statement transaction time, so all timestamps are
**application-stamped** once per operation (`datetime.now(timezone.utc)`), not via
`CURRENT_TIMESTAMP` across triggers. Minor skew is accepted; no SQL-standard
system-versioning is attempted.
