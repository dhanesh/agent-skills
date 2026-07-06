# Input adapters

Each adapter's job is to emit one or more **Endpoint Inventory records** that conform to
`inventory.schema.json`. Every record must carry:

- `provenance.source` — the file path or URL the record was extracted from.
- `provenance.locator` — a within-source pointer (JSON Pointer, `operationId`, line range,
  heading, or `entry[n]` index) precise enough to let a reviewer find the raw source.
- `confidence: "grounded"` — **only** when the endpoint is explicitly present in the source
  (a declared path, a saved example, a response table). Use `confidence: "inferred"` for
  everything that has been deduced from context (no response body, unknown status code,
  endpoint implied by prose but not shown).

Never invent endpoints that are absent from the input.

---

## OpenAPI

**Detection.** Look for a top-level `openapi:` key with a value starting `3.` (e.g.
`openapi: 3.1.0`). OpenAPI 2.x (Swagger) must be upgraded to 3.x before processing; use
`swagger-converter` or a compatible tool.

**Native path (preferred).** When the only input is a raw OpenAPI 3.x document and no
extra examples need merging, hand it directly to `mockstar import <file>` and skip manual
IR extraction. The native importer produces mockstar config without an intermediate
Endpoint Inventory record.

**Manual IR extraction** (needed when merging additional example responses or overriding
generated config):

1. Iterate `paths.<path>.<method>` for each operation.
2. `method` — uppercase HTTP verb (`GET`, `POST`, etc.).
3. `path` — rewrite OpenAPI `{param}` placeholders to Hono-style `:param`
   (e.g. `/users/{id}` → `/users/:id`).
4. `auth` — inspect `security[]` on the operation (then the global `security[]`); map
   `bearerAuth` → `{ header: "authorization", startsWith: "Bearer " }`,
   `apiKeyHeader` → `{ header: "<name>" }`.
5. `request.contentType` — first key under `requestBody.content` (e.g.
   `"application/json"`).
6. `request.schema` — `requestBody.content.<mediaType>.schema`.
7. `request.example` — `requestBody.content.<mediaType>.example` or the first entry in
   `examples`.
8. `responses[]` — for each `responses.<status>` entry produce one record item:
   - `status` — integer status code.
   - `headers` — from `responses.<status>.headers`.
   - `body` — `responses.<status>.content.<mediaType>.example` (or first `examples` value).
9. `provenance.locator` — the operation's `operationId` when present; fall back to
   `paths.<path>.<method>` (e.g. `paths./users/{id}.get`).
10. `confidence: "grounded"` — for any operation with an explicit example body;
    `"inferred"` when the status entry exists but carries no example.

---

## Postman

**Format.** Postman Collection v2.1 JSON (schema URL contains
`collection/v2.1.0`). Collections export from the Postman UI or via
`postman export`.

**Lifting to OpenAPI (optional).** For collections with well-structured saved responses,
convert the collection to OpenAPI 3.x using `postman-to-openapi` (or equivalent) and then
feed the result to the native `mockstar import` path. This is lossless for method/path/auth
and usually produces higher-fidelity config. Use direct extraction below only when the
collection has incomplete saved responses or you need fine-grained control.

**Direct extraction.** Walk `item[]` recursively (folders are nested `item[]`):

1. For each leaf item (has `request`, not `item`):
   - `method` — `item.request.method` (uppercase).
   - `path` — extract path from `item.request.url.path[]` joined with `/`;
     rewrite `:var` and `{{var}}` placeholders to Hono `:param`
     (e.g. `/users/{{userId}}` → `/users/:userId`,
            `/users/:id/orders` stays `/users/:id/orders`).
   - `auth` — `item.request.auth` (type `bearer` → `{ header: "authorization", startsWith: "Bearer " }`;
     type `apikey` → `{ header: "<key>" }`). Fall back to `collection.auth` when the item
     has no auth block.
   - `request.contentType` — `item.request.body.mode` mapped to MIME
     (`raw`+`options.raw.language=json` → `application/json`,
      `formdata` → `multipart/form-data`,
      `urlencoded` → `application/x-www-form-urlencoded`).
   - `request.example` — parsed `item.request.body.raw` (JSON) or raw string.
2. `responses[]` — from `item.response[]` (saved examples):
   - `status` — `response.status` (integer).
   - `headers` — reconstruct from `response.header[]` as `{ name: value }`.
   - `body` — parsed `response.body` (attempt JSON; fall back to raw string).
3. When an item has **no** saved responses, emit a single `responses[{ status: 200, body: null }]`
   and mark `confidence: "inferred"`.
4. `provenance.source` — collection file path or URL.
5. `provenance.locator` — `item.id` when present; fall back to dot-joined folder path +
   item name (e.g. `Users > Get User`).
6. `confidence: "grounded"` — when at least one saved response example exists;
   `"inferred"` otherwise.

---

## HAR

**Format.** HTTP Archive 1.2 JSON (`log.version: "1.2"`). Export from browser DevTools
(Network tab → Save all as HAR) or `curl --dump-header` piped through a HAR converter.

**Extraction steps:**

1. Read `log.entries[]`; discard entries where `request.url` does not match the target
   origin (filter by hostname if `--base-url` is set).
2. Parse `request.url` → strip query string into separate params, keep path only.
   Rewrite any numeric or UUID path segments to `:id` / `:uuid` placeholders
   (heuristic: segments matching `^\d+$` → `:id`; UUID pattern → `:uuid`).
   Use the rewritten path as the Endpoint Inventory `path`.
3. **Group** entries by `(method, normalised-path)`. Each group becomes one endpoint record.
4. `request.example` — `entry.request.postData.text` (parse JSON when
   `postData.mimeType` is `application/json`; otherwise store as raw string).
5. `request.contentType` — `entry.request.postData.mimeType`.
6. `responses[]` — collect **distinct** status codes across the group:
   - `status` — `entry.response.status`.
   - `headers` — reconstruct from `entry.response.headers[]` (name→value map),
     dropping hop-by-hop headers (`transfer-encoding`, `connection`, etc.).
   - `body` — `entry.response.content.text` (decode `entry.response.content.encoding`
     if `base64`; parse JSON when mimeType is `application/json`).
   - If the same status code appears multiple times in the group with different bodies,
     keep the **first** occurrence and attach the others as additional `responses[]` entries
     only when their body differs meaningfully (skip near-duplicates).
7. `provenance.locator` — `entry[<n>]` where `<n>` is the 0-based index of the first
   entry in the group that contributed the record.
8. `confidence` — `"grounded"` when `entry.response.content.text` is present and
   non-empty (the response body was captured); `"inferred"` when the response body is
   absent or empty (e.g. redirects, cached/filtered captures where the body was not
   recorded).
9. `statefulHints` — when the group contains a `POST` that is followed (by timestamp) by
   a `GET` to the same resource path, set `statefulHints` to
   `"POST creates, GET returns it"`.

---

## curl

**Detection.** Input is one or more `curl` command lines (from a `commands.sh` file, a
code block, or stdin). Each `curl` invocation yields exactly one endpoint record.

**Parsing rules:**

| curl flag | IR field |
|-----------|----------|
| `-X <METHOD>` / `--request <METHOD>` | `method` (uppercase) |
| (no `-X`) | `method: "GET"` (default) |
| URL (last bare argument) | `path` — extract path component; rewrite numeric/UUID segments to `:param` |
| `-H <header>` / `--header <header>` | if `Authorization: Bearer …` → `auth.header = "authorization"`, `auth.startsWith = "Bearer "` |
| `-d <data>` / `--data <data>` / `--data-raw` | `request.example` — parse JSON if parseable |
| `--data-urlencode` | `request.contentType = "application/x-www-form-urlencoded"` |
| `-F` / `--form` | `request.contentType = "multipart/form-data"` |

**Response.** curl commands carry no response information. Synthesize a single
`responses[{ status: 200, body: "<echo of request body or null>" }]` and **always** set
`confidence: "inferred"` — no actual response was observed.

**Multiple commands.** When the input is a script with multiple `curl` calls, emit one
record per call. Deduplicate at the merge stage (see `## Merge & dedupe`).

`provenance.locator` — line number of the `curl` command in the source file
(e.g. `line:42`), or `cmd[0]`, `cmd[1]` … if parsed from a list.

---

## GraphQL

**Detection.** Input is either a SDL schema file (`.graphql` / `.gql`), an introspection
JSON result, or a collection of `.graphql` operation files.

**Modeling rule.** GraphQL does **not** expose multiple HTTP paths. Model the entire API as
a **single endpoint record**:

```json
{
  "method": "POST",
  "path": "/graphql",
  "request": { "contentType": "application/json" }
}
```

**Operations → `responses[]`.** Each named operation (query, mutation, subscription) in
the SDL or operation files becomes one entry in `responses[]`:

- `body` — the example response shape derived from the schema type (nullable scalars → `null`
  placeholder; lists → single-element array of the item type).
- `when` — an attribute-keyed body predicate so it becomes a mockstar scenario.
  For named operations, use the `operationName` attribute with an exact-equality shorthand:

  ```json
  {
    "when": {
      "body": { "operationName": "GetUser" }
    }
  }
  ```

  When the operation file uses anonymous operations (no `operationName`), fall back to
  matching on the `query` attribute with a `contains` predicate object:

  ```json
  {
    "when": {
      "body": { "query": { "contains": "query GetUser" } }
    }
  }
  ```

- `status: 200` for all operations (GraphQL always returns 200; errors are in the body).

**Variables → `request.match`.** When operation files include example variable sets,
capture them as `request.match` predicates (e.g. `{ "jsonpath": "$.variables.id", "equals": "42" }`).

**Auth.** If the SDL or repo README specifies a header (e.g. `Authorization: Bearer <token>`),
populate `auth` accordingly.

**`provenance.locator`** — operation name (e.g. `GetUser`) from the SDL type or the
operation file name.

**`confidence`** — A `responses[].body` populated solely from SDL type definitions
(nullable scalars → `null`, lists → single-element array, etc.) is **synthesized, not
observed**: set `confidence: "inferred"` regardless of whether an operation file exists.
Set `confidence: "grounded"` only when an operation file carries an explicit example or
`# @response` annotation (or a `*.response.json` companion file is present).

---

## Prose (Markdown / PDF / DOCX / URL)

**Pre-processing.**

- **PDF / DOCX** — convert to plain text via `assets/extract_text.py` (implemented in
  Task 5) before extraction. Pass the output file path as the prose source.
- **URL** — fetch the page with `curl -L` (or `WebFetch`) to obtain HTML/text;
  strip HTML tags to get readable prose.
- **Markdown** — use directly; no conversion needed.

**Extraction heuristics (apply in order):**

1. **Code fences.** Scan for fenced blocks tagged `http`, `sh`, or `curl`. Parse HTTP
   request/response pairs:
   - A request block starts with `<METHOD> <path> HTTP/1.1` or is a `curl` command.
   - A response block immediately following starts with `HTTP/1.1 <status>`.
   - Extract method, path, request body (if present), status, and response body.
   - Set `confidence: "grounded"` when both request and response blocks are present.

2. **Tables.** Look for Markdown tables with columns that contain `Method`, `Endpoint`,
   `Path`, or `URL` in the header row. Each data row → one record.
   - Map column values to `method`, `path`.
   - If a `Response` or `Status` column exists, extract `responses[].status`.
   - Set `confidence: "inferred"` unless an example body column is also present.

3. **Inline references.** Scan paragraphs for patterns like `` `GET /users/:id` `` or
   `POST /orders` (unquoted). Each match → one record with `confidence: "inferred"`.
   Do **not** invent fields not found in the text.

4. **Never invent endpoints.** If the text describes a feature but names no specific HTTP
   method or path, do not emit a record. Emit a coverage-report note instead.

**`provenance.locator`** — heading path + line range (e.g. `## API Reference > line:34-41`).

**`confidence`** — `"grounded"` only when both request and response are explicitly shown
with example data. Everything else is `"inferred"`.

---

## Merge & dedupe

After all adapters have run, merge their outputs into a single Endpoint Inventory array.

**Deduplication key.** `(method, path)` — case-insensitive for method, exact for path
(Hono-style `:param` form).

**Conflict resolution (higher wins).**

Priority order (most trusted → least trusted):

1. OpenAPI (explicit schema, validated)
2. Postman / HAR (real traffic or saved examples)
3. curl (request only, no response)
4. Prose (inferred from text)

When two records share the same `(method, path)`:

- Keep all fields from the **higher-priority** record.
- **Union** `responses[]` by `status` — if the higher-priority record already has a
  `status: 404` entry, keep it; add a lower-priority status only if it is absent.
- If `confidence` differs, take the higher-priority record's `confidence`.
- Append a `_conflicts` annotation (not part of the schema; for the coverage report only)
  listing which fields were dropped and from which source. This annotation MUST be stripped
  from every record before the Endpoint Inventory is validated against `inventory.schema.json`
  or consumed downstream — it exists solely to feed the coverage report.

**`--max-endpoints` enforcement.** When the merged array exceeds the `--max-endpoints`
limit (default: unlimited), drop records in reverse priority order (prose-inferred first,
then curl-inferred, then HAR, then Postman) until the count is within the limit. Log each
dropped `(method, path)` to the coverage report with reason `"dropped: max-endpoints"`.

**`statefulHints` union.** If any contributing record has a `statefulHints` note, preserve
it on the merged record (concatenate distinct notes with `;` separator if multiple sources
contributed different hints).

**`webhookHints[]` union.** Concatenate webhook hint arrays from all sources; deduplicate
by `url + event`.
