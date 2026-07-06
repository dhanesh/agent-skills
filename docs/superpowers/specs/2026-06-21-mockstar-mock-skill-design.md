# mockstar-mock — skill design

**Date:** 2026-06-21
**Status:** approved (brainstorming) → ready for implementation plan
**Repo:** dhanesh/agent-skills
**Target tool:** [mockstar](https://github.com/dhanesh/mockstar) — a Bun + Hono mock server

## Summary

`mockstar-mock` is an agent skill that turns heterogeneous service specifications —
OpenAPI (json/yaml), Postman collections, HAR captures, raw curl examples, GraphQL
SDL/introspection, and prose documentation (Markdown / PDF / DOCX / a documentation URL)
— into a **runnable, full-fidelity mockstar mock project**, with every mock traced back
to its source (provenance), low-confidence inferences flagged, and the result verified by
booting the server and smoke-testing each route.

The skill exists to fill mockstar's gap: mockstar already ships native **OpenAPI import**
(`mockstar import`) and **Tier 2 enhancement** (`mockstar enhance`), but it cannot ingest
Postman, HAR, curl, GraphQL, or prose docs. This skill normalizes all of those into
mockstar config while reusing native tooling wherever it reaches.

## Decisions locked during brainstorming

| Decision | Choice |
|---|---|
| Input formats (v1) | Broad: OpenAPI, Postman, **HAR, curl, GraphQL**, Markdown/PDF/DOCX, doc URL |
| Pipeline | **Hybrid** — native `mockstar import` for OpenAPI; agent authors from an intermediate IR for foreign/unstructured inputs |
| Fidelity | **Full / inferred** — also infer scenarios, dynamic handlers, and webhooks where docs imply them |
| Verification | **Boot + smoke routes** — run `bunx mockstar serve` and curl each route |
| Deliverable | **Both, flag-controlled** — default full scaffold; `--into <dir>` emits into an existing project |
| Inference guardrail | **Tag + coverage report** — provenance + confidence per mock; speculative items flagged for human review |

## Grounding facts (verified against the mockstar source on 2026-06-21)

mockstar CLI surface (`src/cli.ts` `usage()`):

- `mockstar init [dir]` — scaffold `mocks/` + `mockstar.config.json`
- `mockstar serve [config-root]` — start the server (default command); flags `--handlers <path>`,
  `--port`, `--host`, `--deterministic`, `--no-watch`, `--allow-webhook-url-header`,
  `--webhook-journal-file <p>`
- `mockstar import <spec> <out-dir>` — convert an **OpenAPI 3.x** spec to mockstar JSON
  (`runImporter({ specPath, outDir, tenantName, allowPrivateUpstreams })`)
- `mockstar enhance <mocks-dir>` — rewrite mocks with Tier 2 placeholders (idempotent;
  `--dry-run`, `--spec=<file>`); writes a `_mockstarGenerated` manifest
- `mockstar migrate --schema …` — bump `$schema` URLs
- `mockstar proxy <…>` — HTTPS transparent upstream (out of scope for this skill)

Config shape (`docs/CONFIG.md`):

- One directory per **tenant** under `mocks/`; tenant ids `^[a-zA-Z0-9_-]{1,64}$`.
- Mock entry: `id`, `match` (`method`, `path` Hono-style `:param`, `query`, `headers`,
  `body` via `equals`/`partial`/`jsonpath`, `priority`), `response`
  (`kind: static|dynamic|passthrough`, `status`, `headers`, `body`, `delay`).
- **Scenarios** (`docs/SCENARIOS.md`): `scenarios[]` with `when` (`params`/`query`/`headers`/`body`)
  + per-scenario `response` overrides — one entry returns different responses by request attribute.
- **Webhooks** (`README.md`): `webhooks[]` fire after the matched response flushes; HMAC signing,
  retries, circuit breaker.
- **Handlers** (`docs/HANDLERS.md`): `response.kind: "dynamic"` + `handler: "<name>"` resolving
  to a TS export under `handlers/`.
- **Tier 2 tokens**: `{{request.*}}`, `{{tenant}}`, `{{requestId}}`, `{{faker.*}}`,
  `{{id(...)}}`, `{{now.*}}`; `--deterministic` makes faker/now reproducible for CI.

Scope note: **GraphQL is deferred natively** (`README.md` "what's deferred"). The skill mocks
GraphQL as a `POST /graphql` entry matching on `operationName`/`query` via `match.body`, with
`scenarios` per operation — not via a native GraphQL feature.

## Architecture

A 6-stage pipeline with a single convergence point — the **Endpoint Inventory IR** — so that
per-format parsing is decoupled from mockstar config generation.

```
inputs ─▶ (1) intake & classify ─▶ (2) extract to Endpoint Inventory IR ─┐
                                                                          ▼
                              (3) generate mockstar config (hybrid) ◀─────┘
                                       │
                                       ▼
                              (4) mockstar enhance (Tier 2)
                                       │
                                       ▼
                              (5) verify: boot + smoke each route
                                       │
                                       ▼
                              (6) coverage / provenance report
```

### Stage 1 — Intake & classify

Detect each input's type by signature, not extension alone:

- **OpenAPI 3.x** — `openapi: 3.*` / `{"openapi": "3...`
- **Postman** — collection v2.1 schema marker
- **HAR** — `{ "log": { "entries": [...] } }`
- **curl** — text blobs starting with `curl `
- **GraphQL** — SDL (`type Query {`) or introspection JSON
- **Prose** — Markdown, PDF, DOCX, or a documentation URL

Convert non-text inputs to text up front: PDF → text, DOCX → text, doc URL → fetched
markdown/HTML. Record the original source path/URL for provenance.

### Stage 2 — Extract to Endpoint Inventory IR

Fan-out one subagent per input. Each emits records conforming to
`references/inventory.schema.json`. A record captures, per endpoint:

- `method`, `path` (normalized to Hono `:param` form), path/query params, auth requirements
- request: content-type, schema and/or concrete examples
- responses: variants keyed by status → `{ headers, body }`, including documented error cases
- `statefulHints` (e.g. "POST creates, GET returns it"), `webhookHints` (documented callbacks)
- **`provenance`**: `{ source: <file|url>, locator: <pointer/line/section> }`
- **`confidence`**: `grounded` (explicit in a source) | `inferred` (reasoned from context)

Merge + dedupe across inputs (same method+path). On conflict, prefer the higher-confidence /
more-structured source (OpenAPI > Postman/HAR > prose) and note the conflict in the report.
Enforce `--max-endpoints`; record any surplus for the report's dropped list.

### Stage 3 — Generate mockstar config (hybrid)

- **OpenAPI inputs** (and Postman/HAR that lift losslessly to OpenAPI 3.x): run native
  `mockstar import <spec> <out-dir> --tenant <t>` to seed mocks, then merge IR extras
  (extra examples, error variants, auth matching) into the generated entries.
- **All other inputs**: author `mocks/<tenant>/<resource>.json` entries directly from the IR.
- **Full fidelity overlays** (only when fidelity = `full`):
  - **Scenarios** — documented error/edge cases become `scenarios[].when` branches on one entry.
  - **Handlers** — computed or stateful responses become `response.kind: "dynamic"` + a TS file
    under `handlers/`. Stateful handlers use in-memory state and are tagged speculative.
  - **Webhooks** — documented async callbacks become `webhooks[]` on the triggering entry.
- Group entries by resource into one file per resource; assign `priority` so specific matches
  beat catch-alls.

### Stage 4 — Enhance

Run `mockstar enhance <mocks-dir>` to rewrite literal ids/timestamps into Tier 2 placeholders
(`{{id(...)}}`, `{{now.*}}`). Pass `--spec=<file>` when an OpenAPI spec is available for field-name
hints. The enhancer is idempotent and records a `_mockstarGenerated` manifest; hand edits survive.

### Stage 5 — Verify (boot + smoke)

- Boot `bunx mockstar serve <out> --deterministic --no-watch` on an ephemeral port.
- `assets/smoke.sh` curls each generated route (using example request bodies/params from the IR)
  and asserts the server loaded the config and each route returns its expected status.
- On failure, diagnose (schema-invalid entry, bad match, handler error) and iterate.
- Skipped when `--no-verify`.

### Stage 6 — Coverage / provenance report

Write `MOCKSTAR-COVERAGE.md` (skeleton in `references/coverage-report.md`):

- endpoints covered, each linked to its provenance locator(s)
- **grounded vs inferred** counts; a **"review me" list** of speculative scenarios/handlers/webhooks
- documented-but-unmocked **gaps**
- dropped endpoints from `--max-endpoints`
- conflicts resolved during merge

## Output layout

Default (full scaffold, via `mockstar init`):

```
<out>/
  mockstar.config.json
  mocks/<tenant>/<resource>.json
  handlers/*.ts                 # only if dynamic handlers were generated
  scripts/smoke.sh
  MOCKSTAR-COVERAGE.md
```

`--into <dir>` mode: emit `mocks/<tenant>/*.json` (+ `handlers/*.ts`) into an existing mockstar
project; do not scaffold `mockstar.config.json`; still enhance, verify, and report.

## Flags

- `--into <dir>` — emit into an existing mockstar project (default: full scaffold)
- `--tenant <name>` — tenant directory (default `default`; must match `^[a-zA-Z0-9_-]{1,64}$`)
- `--fidelity full|static` — `full` (default) infers scenarios/handlers/webhooks; `static`
  suppresses inference and emits plain static responses
- `--no-verify` — skip the boot + smoke stage
- `--deterministic` — generate and verify in deterministic mode (reproducible CI fixtures)
- `--max-endpoints N` — cap endpoints per run; surplus listed in the report

## Invariants (do not violate)

1. **No fabricated endpoints.** Every mock traces to a provenance locator in an input.
   Purely inferred behavior is tagged `inferred`/speculative and listed in the report — never
   presented as grounded.
2. **Prefer native tooling.** Use `mockstar import` / `mockstar enhance` rather than
   re-implementing them; hand-author only where native import cannot reach.
3. **Schema-valid output.** Generated config must pass mockstar's Zod / JSON-Schema load;
   verification boots the server to prove it.
4. **No silent truncation.** `--max-endpoints` drops and merge conflicts are recorded in the report.
5. **Read-only inputs.** Never mutate the source specs/docs.

## Skill file structure (mirrors `base-in-reality`)

```
mockstar-mock/
  SKILL.md
  references/
    input-adapters.md       # per-format extraction guidance (OpenAPI, Postman, HAR, curl,
                            #   GraphQL, Markdown/PDF/DOCX, URL)
    mockstar-mapping.md     # IR → mockstar config: match/response/scenarios/webhooks/handlers,
                            #   Tier 2 tokens, GraphQL-as-POST, priority rules
    inventory.schema.json   # the Endpoint Inventory IR JSON Schema
    coverage-report.md      # MOCKSTAR-COVERAGE.md skeleton
  assets/
    smoke.sh                # boot + per-route curl harness
```

`SKILL.md` carries a tight front-matter `description` (trigger phrases: "mock a service",
"create mocks from OpenAPI/Postman/docs", "mockstar"), the invariants, the flags, and the
6-stage procedure with subagent fan-out at stages 2 and (optionally) 3.

## Out of scope (v1)

- The HTTPS transparent upstream (`mockstar proxy`) — orthogonal to mock authoring.
- Native GraphQL semantics beyond `POST /graphql` body-matching.
- Multi-tenant splitting beyond what inputs explicitly describe (default single `default` tenant).
- Generating load/perf fixtures or contract tests.

## Open questions for the implementation plan

- PDF/DOCX extraction: rely on the host environment having a converter, ship a small helper, or
  require pre-converted text? (Affects `assets/`.)
- How much of the IR → config mapping is deterministic script vs agent judgment — i.e. should any
  of stage 3 be a bundled `.mjs`/`.py` helper like `base-in-reality`'s `workflow.mjs`?
