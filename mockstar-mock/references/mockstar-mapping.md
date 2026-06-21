# IR → mockstar config mapping

This document describes how each field of an **Endpoint Inventory** record (defined in `inventory.schema.json`) maps to a mockstar JSON mock entry (defined in the mockstar `docs/CONFIG.md`). The `mockstar-mock` skill generator follows these rules to produce a complete mock config from the IR.

Quick outline of the translation:

| IR field | mockstar output |
|---|---|
| `method` + `path` | `match.method` + `match.path` |
| `auth` | `match.headers.authorization` predicate |
| `request.match` | `match.body` / `match.query` |
| first `responses[]` (no `when`) | `response` (default, `kind: "static"`) |
| `responses[]` entries with `when` | `scenarios[]` entries |
| `statefulHints` | `response.kind: "dynamic"` + `handler` + TS file |
| `webhookHints[]` | `webhooks[]` on the triggering entry |

---

## match

Map `method` and `path` directly. Both are required in every IR record.

```jsonc
// IR record
{ "method": "GET", "path": "/users/:id" }

// mockstar match block
{ "method": "GET", "path": "/users/:id" }
```

### Auth → `match.headers`

When the IR record carries an `auth` object (`{ header, startsWith }`), map it to `match.headers` using the same `startsWith` predicate:

```jsonc
// IR
{ "auth": { "header": "authorization", "startsWith": "Bearer " } }

// mockstar
{ "match": { "headers": { "authorization": { "startsWith": "Bearer " } } } }
```

### `request.match` → `match.body` / `match.query`

The IR `request.match` object holds body and query predicates that should be promoted verbatim into the mockstar `match` block. The three body forms are:

| IR `request.match` form | mockstar `match.body` |
|---|---|
| `{ "equals": { … } }` | `{ "equals": { … } }` |
| `{ "partial": { … } }` | `{ "partial": { … } }` |
| `{ "jsonpath": "$.user.role" }` | `{ "jsonpath": "$.user.role" }` |

Query predicates (`{ "query": { "tier": "premium" } }`) map to `match.query`.

Full example with all three:

```jsonc
// IR
{
  "method": "POST",
  "path": "/orders",
  "auth": { "header": "authorization", "startsWith": "Bearer " },
  "request": {
    "match": {
      "body": { "partial": { "currency": "INR" } },
      "query": { "version": "2" }
    }
  }
}

// mockstar mock entry — match block
{
  "match": {
    "method": "POST",
    "path": "/orders",
    "headers": { "authorization": { "startsWith": "Bearer " } },
    "body": { "partial": { "currency": "INR" } },
    "query": { "version": "2" }
  }
}
```

---

## response

The **first** `responses[]` entry that has no `when` field becomes the entry's default `response` with `kind: "static"`. If all entries have `when`, treat the first as the default anyway and also emit it as a scenario.

Full mock entry shape (from `docs/CONFIG.md`):

```jsonc
{
  "id": "get-user-by-id",
  "match": {
    "method": "GET",
    "path": "/users/:id",
    "headers": { "authorization": { "startsWith": "Bearer " } },
    "priority": 0
  },
  "response": {
    "kind": "static",
    "status": 200,
    "headers": { "content-type": "application/json" },
    "body": {
      "id": "{{request.params.id}}",
      "email": "alice@example.com",
      "createdAt": "2024-01-15T09:00:00Z"
    }
  }
}
```

Rules:
- `status` — taken directly from `responses[0].status`.
- `headers` — taken from `responses[0].headers`; always include `content-type`.
- `body` — taken from `responses[0].body`. Prefer literal example values; let `mockstar enhance` (Stage 4) replace IDs and timestamps with Tier 2 tokens.
- `delay` — omit unless the IR notes latency requirements; use `{ "min": 50, "max": 200 }` form when present.

---

## Scenarios

Any `responses[]` entry that carries a `when` field becomes an element of the entry's `scenarios[]` array. The `when` object maps request attribute keys (`params`, `query`, `headers`, `body`) to `StringMatch` predicates.

```jsonc
// IR responses array
[
  { "status": 200, "body": { "found": true, "name": "{{request.params.lastName}}" } },
  { "status": 404, "body": { "error": "user_not_found" }, "when": { "params": { "lastName": "Test" } } },
  { "status": 500, "body": { "error": "internal_server_error" }, "when": { "params": { "lastName": "Carpenter" } } }
]

// mockstar scenarios[]
"scenarios": [
  {
    "id": "not-found-test",
    "when": { "params": { "lastName": "Test" } },
    "response": { "status": 404, "body": { "error": "user_not_found" } }
  },
  {
    "id": "server-error-carpenter",
    "when": { "params": { "lastName": "Carpenter" } },
    "response": { "status": 500, "body": { "error": "internal_server_error" } }
  }
]
```

Complete worked example mirroring `docs/SCENARIOS.md`:

```jsonc
{
  "id": "get-user-by-lastname",
  "match": { "method": "GET", "path": "/users/:lastName" },
  "response": {
    "kind": "static",
    "status": 200,
    "headers": { "content-type": "application/json" },
    "body": { "found": true, "name": "{{request.params.lastName}}" }
  },
  "scenarios": [
    {
      "id": "not-found-test",
      "when": { "params": { "lastName": "Test" } },
      "response": { "status": 404, "body": { "error": "user_not_found" } }
    },
    {
      "id": "server-error-carpenter",
      "when": { "params": { "lastName": "Carpenter" } },
      "response": { "status": 500, "body": { "error": "internal_server_error" } }
    },
    {
      "id": "locked",
      "when": { "params": { "lastName": "Locked" } },
      "response": {
        "status": 423,
        "headers": { "content-type": "application/json", "retry-after": "60" },
        "body": { "error": "account_locked" }
      }
    }
  ]
}
```

Scenario `when` predicate vocabulary (same `StringMatch` as route predicates):

| Form | Meaning |
|---|---|
| `"value"` | Exact equality (shorthand) |
| `{ "equals": "value" }` | Exact equality |
| `{ "startsWith": "prefix" }` | String starts with prefix |
| `{ "contains": "substring" }` | String contains substring |
| `{ "regex": "^pattern$" }` | Regular expression (ReDoS-guarded at load time) |

Rules are evaluated in declaration order; the first match wins. Maximum 50 scenario rules per entry.

---

## Dynamic handlers

When an IR endpoint record has a `statefulHints` field, the generator produces a `response.kind: "dynamic"` entry instead of `kind: "static"`, and creates a minimal TypeScript handler file in `handlers/`.

### mockstar config entry

```jsonc
{
  "id": "post-orders-stateful",
  "match": { "method": "POST", "path": "/orders" },
  "response": { "kind": "dynamic", "handler": "postOrders" }
}
```

### Minimal in-memory-state handler template

```ts
// handlers/postOrders.ts
import type { Context } from 'hono';
import type { HandlerHelpers } from '@dhanesh/mockstar';

// In-memory store — resets on server restart. Replace with a persistent
// store if cross-request durability is required.
const store = new Map<string, unknown>();

export async function postOrders(
  ctx: Context,
  helpers: HandlerHelpers,
): Promise<Response> {
  const body = await ctx.req.json().catch(() => ({}));
  const id = `order_${helpers.faker.uuid()}`;
  store.set(id, { id, ...body, createdAt: new Date().toISOString() });
  return Response.json({ id, status: 'created' }, { status: 201 });
}
```

Rules:
- The handler name (`postOrders`) must match the `handler` field in config and be **globally unique** within `handlers/`.
- Use **named exports only** — `export default` is ignored by mockstar.
- Generated handlers are marked `"confidence": "inferred"` in the IR coverage report because the stateful behaviour is reasoned from `statefulHints`, not an explicit source spec.
- Await all promises; fire-and-forget rejections terminate the server.

---

## Webhooks

When an IR endpoint record has a `webhookHints[]` array, map each element to a `webhooks[]` entry on the triggering mock entry.

```jsonc
// IR webhookHints
"webhookHints": [
  { "url": "https://example.com/hooks/order-created", "event": "order.created" }
]

// mockstar webhooks[]
"webhooks": [
  {
    "id": "order-created-hook",
    "url": "https://example.com/hooks/order-created",
    "method": "POST",
    "headers": { "content-type": "application/json" },
    "body": { "event": "order.created", "orderId": "{{request.params.id}}" }
  }
]
```

Complete entry example:

```jsonc
{
  "id": "post-orders-webhook",
  "match": { "method": "POST", "path": "/orders" },
  "response": {
    "kind": "static",
    "status": 201,
    "headers": { "content-type": "application/json" },
    "body": { "id": "ord_001", "status": "created" }
  },
  "webhooks": [
    {
      "id": "order-created-hook",
      "url": "https://example.com/hooks/order-created",
      "method": "POST",
      "headers": { "content-type": "application/json" },
      "body": { "event": "order.created", "orderId": "ord_001" }
    }
  ]
}
```

Notes:
- HMAC signing and retry logic are **opt-in** — not inferred from `webhookHints`. Add a `secret` field and `retry` config manually when needed.
- The `url` from the IR hint is used as-is; validate it points to a reachable endpoint before enabling in production.
- The generator defaults `method: "POST"` for all inferred webhooks.

---

## Tier 2 tokens

Tier 2 tokens are `{{ … }}` expressions in `response.body` and `response.headers` that mockstar evaluates per request at render time. The generator may emit the following tokens:

| Token | Description |
|---|---|
| `{{request.params.<name>}}` | Path parameter captured by `:name` in `match.path` |
| `{{request.query.<name>}}` | URL query-string value |
| `{{request.headers.<name>}}` | Request header (case-insensitive) |
| `{{request.body.<dot.path>}}` | Dot-path into the parsed JSON request body |
| `{{faker.uuid}}` | Random UUID v4 |
| `{{faker.email}}` | Random email address |
| `{{faker.name}}` | Random full name |
| `{{faker.integer(min, max)}}` | Random integer in `[min, max]` |
| `{{faker.pick(["a","b"])}}` | Random element from the array |
| `{{faker.boolean}}` | `true` or `false` |
| `{{faker.dateIso}}` | Random recent date as ISO 8601 |
| `{{id("prefix_", 14)}}` | Opaque ID: prefix + 14 base62 chars |
| `{{id.named("key", "prefix_", 14)}}` | Mint once per request per key; repeated calls return the same value |
| `{{now.iso}}` | Current time as ISO 8601 string |
| `{{now.unix}}` | Current time as Unix seconds (number) |
| `{{now.millis}}` | Current time as Unix milliseconds (number) |
| `{{tenant}}` | Tenant identifier for the request |
| `{{requestId}}` | Per-request UUID assigned by mockstar |

### Generator strategy

**Prefer literal example values** in the initial generated output and let `mockstar enhance` (Stage 4) rewrite IDs and timestamps automatically. This keeps the generated JSON readable and avoids over-tokenization:

```jsonc
// Generator emits literal values
{ "id": "usr_abc123", "createdAt": "2024-01-15T09:00:00Z" }

// After mockstar enhance rewrites it
{ "id": "{{id(\"usr_\", 14)}}", "createdAt": "{{now.iso}}" }
```

Echo path parameters directly when the endpoint uses `:param` captures:

```jsonc
{ "id": "{{request.params.id}}", "name": "Alice" }
```

---

## GraphQL

GraphQL APIs are modeled as a **single** `POST /graphql` mock entry. Individual operations are distinguished by matching on `operationName` in the request body. Each per-operation response becomes a `scenarios[]` entry.

### Match strategy

Route on transport only — `method` and `path`. The parent entry's `match` does NOT route by operation name; operation dispatch happens exclusively via `scenarios[].when.body`. Optionally add `match.body.partial` to narrow the entry to requests that carry an `operationName` key (a truthy presence check, not a per-operation router):

```jsonc
{
  "id": "graphql-endpoint",
  "match": {
    "method": "POST",
    "path": "/graphql"
  },
  "response": {
    "kind": "static",
    "status": 200,
    "headers": { "content-type": "application/json" },
    "body": { "data": null, "errors": [{ "message": "unknown operation" }] }
  },
  "scenarios": [
    {
      "id": "GetUser",
      "when": { "body": { "operationName": "GetUser" } },
      "response": {
        "status": 200,
        "body": {
          "data": {
            "user": { "id": "{{request.params.id}}", "email": "alice@example.com" }
          }
        }
      }
    },
    {
      "id": "CreateOrder",
      "when": { "body": { "operationName": "CreateOrder" } },
      "response": {
        "status": 200,
        "body": {
          "data": {
            "createOrder": { "id": "ord_001", "status": "pending" }
          }
        }
      }
    },
    {
      "id": "GetUser-not-found",
      "when": { "body": { "operationName": "GetUser-NotFound" } },
      "response": {
        "status": 200,
        "body": { "data": null, "errors": [{ "message": "user not found" }] }
      }
    }
  ]
}
```

Alternative: use `match.body.partial` to match on the `query` field when `operationName` is not always present:

```jsonc
{ "match": { "body": { "partial": { "query": "mutation CreateOrder" } } } }
```

### IR mapping rule

In the Endpoint Inventory, GraphQL endpoints appear as `POST /graphql` records. Multiple IR records for the same path are collapsed into a single mockstar entry; each IR record's responses become scenarios keyed on `operationName`.

---

## Priority

`match.priority` controls which entry wins when multiple entries match the same request.

| Priority value | Meaning |
|---|---|
| `0` (default) | Base priority; used for all generated entries unless overridden |
| Higher integer | Wins over lower-priority entries on the same path |
| Tie | Broken by **declaration order** — earlier entry wins |

### When to set higher priority

- **Specific over catch-all**: a `/users/me` entry should beat a `/users/:id` catch-all entry.
- **Auth-required variants**: an entry that requires `authorization: Bearer …` should beat an unauthenticated variant at the same path.
- **Scenario overflow split**: when a single entry exceeds 50 scenarios and must be split, assign `priority: 10` to the first half and `priority: 0` to the second.

```jsonc
// Specific path — wins first
{ "id": "get-user-me", "match": { "method": "GET", "path": "/users/me", "priority": 10 }, ... }

// Parameterised catch-all — lower priority
{ "id": "get-user-by-id", "match": { "method": "GET", "path": "/users/:id", "priority": 0 }, ... }
```

The generator assigns `priority: 0` by default. Increase it manually (or via a post-processing rule) when a more-specific entry would otherwise be shadowed by a catch-all.
