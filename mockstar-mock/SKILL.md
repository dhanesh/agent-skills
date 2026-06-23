---
name: mockstar-mock
description: "Generate a runnable mockstar mock server for a service from its specs and docs — OpenAPI (json/yaml), Postman collections, HAR captures, curl examples, GraphQL SDL/introspection, and prose docs (Markdown/PDF/DOCX or a documentation URL). Use when asked to mock a service, stand up a fake/stub API, create mockstar mocks/fixtures, or scaffold a mock backend from an API spec or documentation. Normalizes every input into one Endpoint Inventory, uses native `mockstar import` for OpenAPI and hand-authors the rest, infers scenarios/dynamic handlers/webhooks at full fidelity, runs `mockstar enhance` for Tier 2 placeholders, then boots the server and smoke-tests every route. Tags each mock with provenance and confidence and emits a coverage report flagging speculative inferences and documented-but-unmocked gaps. Not for the mockstar HTTPS proxy or native GraphQL semantics. Targets the mockstar CLI (`bunx mockstar`)."
x-spec-version: 1.0
---

# mockstar-mock

Converts any combination of API specs and docs into a runnable mockstar mock project. It accepts
OpenAPI 3.x (JSON/YAML), Postman collections, HAR captures, curl command files, GraphQL
SDL/introspection, and prose documentation (Markdown, PDF, DOCX, or a URL) — one or several at
once — and produces a fully configured mockstar project with realistic scenarios, dynamic handlers,
and webhooks, verified by booting the server and smoke-testing every route.

The defining rule: **no fabricated endpoints.** Every mock traces back to a fetched or provided
source. Behavior that is reasoned from context rather than explicitly documented is tagged
`confidence: "inferred"` in the Endpoint Inventory and flagged in the coverage report.
Speculation is visible and reviewable; it is never silently promoted to ground truth.

## When to use

Use when you need to stand up a mock backend quickly from existing specs or documentation:
mocking a third-party API for local development, building a test double from an OpenAPI spec,
scaffolding fixtures from a HAR capture of real traffic, or generating stubs from API prose docs.
Works on any mix of input types; the skill normalizes them all into one Endpoint Inventory before
generating.

## When not to use

- **mockstar HTTPS proxy mode** — this skill generates config-driven mocks; it does not configure
  the mockstar proxy for intercepting live traffic.
- **Native GraphQL server semantics** — GraphQL is modeled as a single `POST /graphql` endpoint
  with operation routing via `scenarios[].when.body`. If you need a proper GraphQL resolver
  server (subscriptions, schema stitching, persisted queries), use a dedicated GraphQL toolchain.
- **Non-mockstar mock servers** — MSW, Prism, WireMock, json-server. This skill targets only
  the mockstar CLI.

## Prerequisites

- `bunx mockstar` available (install: `bun add -g mockstar` or use `bunx` directly with Bun).
- `uv` available — used to run `assets/extract_text.py` for binary input conversion.
- `curl` available — used by `assets/smoke.sh` for smoke testing routes.

## Invariants (do not violate)

1. **No fabricated endpoints.** Every endpoint in the output must trace to a fetched or provided
   source. If an endpoint is absent from every input, do not emit it.
2. **Prefer native tooling.** Use `bunx mockstar import` for OpenAPI (and losslessly-liftable
   Postman/HAR) rather than hand-authoring what the importer can produce. Use
   `bunx mockstar enhance` for Tier 2 placeholder rewriting rather than hand-tokenizing bodies.
3. **Schema-valid output.** Verification (Stage 5) boots the server. A mock project that fails
   to boot is not a valid deliverable. The `--no-verify` flag skips the boot, which is only
   acceptable in CI pre-check mode where boot is deferred.
4. **No silent truncation.** When `--max-endpoints` caps the inventory, every dropped endpoint
   is logged to `MOCKSTAR-COVERAGE.md` under the "Dropped" section with its source.
5. **Read-only inputs.** Never modify source spec files, HAR archives, or documentation. The
   only writes are to the `--into` output directory and to `MOCKSTAR-COVERAGE.md`.

## Flags

- `--into <dir>` — output directory for the generated mockstar project (default: `mock-<service-name>` in cwd).
- `--tenant <name>` — mockstar tenant name passed to `mockstar import` and used as the mocks subdirectory (default: `default`).
- `--fidelity full|static` — `full` generates scenarios, dynamic handlers, and webhooks from IR hints; `static` emits one default response per endpoint only (default: `full`).
- `--no-verify` — skip Stage 5 boot-and-smoke verification (the generated project is not started).
- `--deterministic` — passed through to `bunx mockstar` during smoke testing; disables random Faker values for reproducible responses.
- `--max-endpoints N` — cap the merged Endpoint Inventory at N records; excess are dropped in reverse-priority order and reported.

## Procedure

Run these six stages in order. Fan out subagents where noted.

**Locating asset helpers (do this first).** The bundled helpers `assets/extract_text.py` and
`assets/smoke.sh` are referenced by paths relative to this skill's directory, but subagents
run from the target repository — a bare `assets/` path will not resolve for them. Before
dispatching any subagent, resolve this skill's absolute base directory once and pass the
absolute paths into every subagent prompt:

- Obtain the skill's base directory from the harness (it is provided when the skill loads).
- Set `EXTRACT="<skill-base-dir>/assets/extract_text.py"` and
  `SMOKE="<skill-base-dir>/assets/smoke.sh"`.
- If the harness does not expose the base directory, discover it:
  `find ~/.claude ~/.config ~/.agents -path '*mockstar-mock*/assets/extract_text.py' 2>/dev/null | head -1`
- Verify: `uv run "$EXTRACT" --help` should print usage.
- Hand subagents the literal absolute `$EXTRACT` and `$SMOKE` values — never a relative
  `assets/`-prefixed form.

---

### Stage 1 — Intake & classify

Examine every provided input and classify each by type using these detection signals:

| Signal | Type |
|--------|------|
| Top-level `openapi:` key starting with `3.` | OpenAPI 3.x |
| Top-level `openapi:` key starting with `2.` | OpenAPI 2.x (Swagger) — upgrade to 3.x first |
| `{"info":{"schema":"https://schema.getpostman.com/...collection/v2.1..."}` | Postman v2.1 |
| `{"log":{"version":"1.2",...}}` | HAR 1.2 |
| Lines starting with `curl ` | curl command file |
| `.graphql` / `.gql` extension, or `{"data":{"__schema":...}}` | GraphQL SDL / introspection |
| `.md`, `.pdf`, `.docx`, plain text, or `http(s)://` URL | Prose documentation |

For binary inputs (PDF, DOCX) and documentation URLs, convert to plain text before proceeding:

```
uv run "$EXTRACT" <input-file-or-url>
```

`assets/extract_text.py` emits the extracted text to stdout; capture it to a temp file and use
that as the prose source for Stage 2. This is the only stage where the absolute `$EXTRACT`
path is needed for subagents.

---

### Stage 2 — Extract → Endpoint Inventory

Fan out one subagent per input type. Each subagent follows the adapter rules in
`references/input-adapters.md` and emits records that are valid against
`references/inventory.schema.json`. Key rules per adapter:

- **OpenAPI** — preferred path: if the sole input is a raw OpenAPI 3.x file and no extra
  examples need merging, skip manual extraction and hand the file directly to
  `bunx mockstar import` in Stage 3. Manual extraction is needed only when merging additional
  examples or overriding generated config.
- **Postman** — walk `item[]` recursively; rewrite `{{var}}` placeholders to Hono `:param`.
  Lift to OpenAPI with `postman-to-openapi` first when saved responses are well-structured.
- **HAR** — group entries by `(method, normalised-path)`; rewrite numeric/UUID path segments
  to `:id` / `:uuid`. Discard entries outside the target origin.
- **curl** — one record per `curl` invocation; all records are `confidence: "inferred"` because
  curl carries no response information.
- **GraphQL** — model the entire API as a single `POST /graphql` record; each named operation
  becomes a `responses[]` entry with a `when.body` predicate matching on `operationName`.
- **Prose** — extract from code fences, Markdown tables, and inline backtick references in that
  order; never invent endpoints that are absent from the text.

After all subagents complete, merge their outputs into a single Endpoint Inventory array.
Deduplication key: `(method, path)` (case-insensitive method, exact Hono-style path). Resolve
conflicts in priority order: OpenAPI > Postman/HAR > curl > prose. Union `responses[]` by
status code. Enforce `--max-endpoints` by dropping records in reverse-priority order and
logging each dropped `(method, path)` to the coverage report with reason `"dropped: max-endpoints"`.

---

### Stage 3 — Generate config (hybrid)

For each endpoint in the merged Endpoint Inventory, generate a mockstar JSON mock entry following
`references/mockstar-mapping.md`. Use the hybrid strategy:

**Native path (preferred for OpenAPI and losslessly-liftable inputs):**

```
bunx mockstar import <spec-file> <out-dir> --tenant <tenant>
```

The importer writes mock JSON files to `<out-dir>/<tenant>/`. Use this path for:
- Raw OpenAPI 3.x inputs (most complete).
- Postman or HAR inputs that have been converted to OpenAPI 3.x without data loss.

**Hand-authored path (for everything else):**

For curl, GraphQL, prose, and any Postman/HAR that could not be losslessly lifted, generate
mock JSON entries by hand per `references/mockstar-mapping.md`:

- Map `method` + `path` → `match.method` + `match.path`.
- Map `auth` → `match.headers` predicate.
- Map `responses[0]` (no `when`) → `response` with `kind: "static"`.
- Map `responses[]` entries with `when` → `scenarios[]` entries.
- When `--fidelity full` (default):
  - `statefulHints` → `response.kind: "dynamic"` + a minimal TypeScript handler in `handlers/`.
  - `webhookHints[]` → `webhooks[]` on the triggering entry.
  - For GraphQL: route operations via `scenarios[].when.body` matching on `operationName`
    (NOT via a `match.body.jsonpath` router on the parent entry).

Prefer literal example values in the initial output; Tier 2 token rewriting is deferred to Stage 4.

---

### Stage 4 — Enhance

Run mockstar's enhance pass over the generated mocks directory:

```
bunx mockstar enhance <out>/<tenant>
```

When an OpenAPI input exists, add `--spec <openapi-file>` so the enhancer can cross-reference
the schema for more accurate placeholder selection.

The enhancer rewrites hardcoded IDs and timestamps to Tier 2 tokens (`{{faker.uuid}}`,
`{{now.iso}}`, `{{id("prefix_", 14)}}`, etc.) and validates that all generated files are
schema-conformant. Review its diff — the coverage report flags any Tier 2 rewriting that
changes `confidence` from `"grounded"` to `"inferred"`.

---

### Stage 5 — Verify

Unless `--no-verify` is set:

1. Write a routes TSV file at `<out>/routes.tsv` with one line per endpoint:
   ```
   METHOD<TAB>/path<TAB>EXPECTED_STATUS
   ```
   Use the primary (no-`when`) status code from the Endpoint Inventory. For endpoints without
   a grounded status, use `200`.

2. Run the smoke suite using the absolute `$SMOKE` path resolved in Stage 1:
   ```
   sh "$SMOKE" <out> <out>/routes.tsv
   ```

   `assets/smoke.sh` boots mockstar with `bunx mockstar <out> --deterministic --no-watch --port <PORT>`,
   waits up to 10 seconds for readiness, then curls every route and checks the HTTP status code.
   Pass `--deterministic` via the environment variable `MOCKSTAR_SMOKE_PORT` if you need a
   non-default port. Do NOT use `bunx mockstar serve` — `serve` is not a valid subcommand;
   the default command boots the server.

3. For any `FAIL` lines from the smoke run, inspect the generated config, fix the entry, and
   re-run until all routes pass. Do not ship a project with smoke failures.

---

### Stage 6 — Report

Write `MOCKSTAR-COVERAGE.md` to the output directory root, following the template in
`references/coverage-report.md`. The report must include:

- **Summary** — inputs list, tenant name, endpoint count (grounded vs. inferred), boot verdict.
- **Endpoints table** — one row per mock: method, path, mock file, source, locator, confidence.
- **Grounded vs. inferred** — counts and explanation.
- **Review me (speculative)** — every inferred scenario, dynamic handler, and webhook with
  the reason it was inferred and its source.
- **Gaps** — endpoints or behaviors present in the source docs but not mocked (e.g. prose
  that names an endpoint without a path, or a schema type with no example).
- **Dropped** — endpoints cut by `--max-endpoints`, with source.
- **Conflicts** — endpoints described differently across inputs and how conflicts were resolved.

## Output layout

Default layout (when `--into` is not set, `<service>` is derived from the primary spec's title
or the target URL hostname):

```
mock-<service>/
  <tenant>/           # mockstar mock JSON files (e.g. users.json, orders.json)
    handlers/         # TypeScript dynamic handler files (--fidelity full only)
  routes.tsv          # routes used for smoke testing
  MOCKSTAR-COVERAGE.md
```

With `--into <dir>`:

```
<dir>/
  <tenant>/
    handlers/
  routes.tsv
  MOCKSTAR-COVERAGE.md
```

Boot the server from the output root:

```
bunx mockstar <out-dir>
bunx mockstar <out-dir> --deterministic --no-watch --port 3000
```

## References

- `references/input-adapters.md` — per-format extraction rules and merge/dedupe logic.
- `references/inventory.schema.json` — JSON Schema for Endpoint Inventory records.
- `references/mockstar-mapping.md` — IR → mockstar config field mapping, scenarios, handlers, webhooks, Tier 2 tokens.
- `references/coverage-report.md` — the `MOCKSTAR-COVERAGE.md` template.

## Assets

- `assets/extract_text.py` — converts PDF/DOCX inputs and documentation URLs to plain text (`uv run`).
- `assets/smoke.sh` — boots mockstar and smoke-tests every route in a TSV file.
- `assets/fixtures/` — sample inputs used by the test suite (petstore-mini.yaml, routes.tsv).

## Subagent dispatch note

The skill's asset helpers (`assets/extract_text.py`, `assets/smoke.sh`) live in this skill's
directory, not in the target repository. Subagents launched in Stage 2 and Stage 5 run from the
target repo's working directory, so a relative `assets/` path will not resolve for them. Always
resolve the **absolute** path to each helper once before dispatching any subagent, and pass the
literal absolute path into the subagent's prompt — never a relative `assets/`-prefixed form.
This mirrors the pattern used by the `base-in-reality` skill for its `fetch_sources.py` helper.
