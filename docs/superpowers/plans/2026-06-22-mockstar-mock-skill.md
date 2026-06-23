# mockstar-mock Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new agent skill, `mockstar-mock`, that converts heterogeneous service specs (OpenAPI, Postman, HAR, curl, GraphQL, Markdown/PDF/DOCX, doc URLs) into a runnable, full-fidelity mockstar mock project — provenance-tagged, boot-and-smoke verified, with a coverage report.

**Architecture:** A markdown-driven skill (`SKILL.md` + `references/` + `assets/`) following the existing `base-in-reality` layout. The skill defines a 6-stage pipeline that funnels every input format into one normalized **Endpoint Inventory IR** (`references/inventory.schema.json`), then generates mockstar config (native `mockstar import` for OpenAPI, hand-authored for the rest), runs `mockstar enhance`, and verifies by booting `bunx mockstar serve` and smoke-testing each route via a bundled `assets/smoke.sh`. Leaf artifacts (schema, references, assets) are built and tested first; `SKILL.md` is the capstone so the gate's dangling-reference check passes.

**Tech Stack:** Markdown (skill body + references), JSON Schema (the IR), POSIX `sh` (smoke harness), Python 3.9+ stdlib via `uv run` with PEP 723 inline deps (text extraction + unit tests), and the mockstar CLI (`bunx mockstar`) for the integration smoke. Validation uses the repo's existing gate scripts (`make gate-skill SKILL=mockstar-mock`).

## Global Constraints

- **Skill directory name:** `mockstar-mock` (kebab-case, ≤ 64 chars) — top-level under repo root.
- **Required files for the gate:** `mockstar-mock/SKILL.md` AND `mockstar-mock/README.md` must both exist.
- **Front-matter:** `SKILL.md` must open and close with `---`; `name: mockstar-mock`; `description:` non-empty and ≤ 1024 characters.
- **No dangling references:** every relative path mentioned in `SKILL.md`'s body MUST exist on disk — so build referenced files before referencing them.
- **No PARAMETERS.md:** this skill uses CLI-style flags, not template placeholders. Do NOT add `PARAMETERS.md` (it would trigger the RT-4 placeholder-bijection dry-run gate). Flags are documented in prose.
- **Python assets:** stdlib-only where possible; declare any deps via PEP 723 (`# /// script`) and run with `uv run <file>`. Mirror `base-in-reality/assets/test_findings_schema.py`.
- **mockstar CLI surface (verified against mockstar 0.1.x stable; skill is version-agnostic and targets `bunx mockstar` — no version is hardcoded):** `mockstar init [dir]`, **serving the mocks: `bunx mockstar <config-root>` (serve is the DEFAULT command — there is NO working `serve` subcommand; `bunx mockstar serve <dir>` fails with ENOENT because it treats `serve` as a literal config-root path)**; flags `--handlers`, `--port`, `--host`, `--deterministic`, `--no-watch`. Import: `mockstar import <spec> <out-dir>` (OpenAPI 3.x only; **`--tenant` flag is equals-only: `--tenant=<name>`**). Enhance: `mockstar enhance <mocks-dir>` (flags `--dry-run`, `--spec=<file>`). Invoke via `bunx mockstar`. (Correction applied during Task 7: the original plan said `mockstar serve` — that form does not work against the released CLI; the README quickstart uses the no-subcommand form.)
- **Read-only inputs:** the skill never mutates source specs/docs.
- **Validation command (run after each file-producing task):** `make gate-skill SKILL=mockstar-mock` — but note it only passes once SKILL.md + README.md exist (Task 8). Until then, per-task tests are the gate.
- **Spec:** `docs/superpowers/specs/2026-06-21-mockstar-mock-skill-design.md` is the source of truth.

---

### Task 1: Endpoint Inventory IR schema

The convergence point of the whole pipeline. Every input adapter emits records conforming to this schema; the generator reads only this schema. Build it (and its structural test) first.

**Files:**
- Create: `mockstar-mock/references/inventory.schema.json`
- Test: `mockstar-mock/assets/test_inventory_schema.py`

**Interfaces:**
- Produces: a JSON Schema (draft 2020-12) for an object `{ "endpoints": [ EndpointRecord, ... ], "tenant": string }` where each `EndpointRecord` has required keys `method`, `path`, `responses`, `provenance`, `confidence`. Later tasks (input-adapters.md, mockstar-mapping.md, smoke.sh) reference these exact field names.

- [ ] **Step 1: Write the failing test**

```python
# mockstar-mock/assets/test_inventory_schema.py
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Structural checks for inventory.schema.json — stdlib json only."""
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "..", "references", "inventory.schema.json")


class InventorySchema(unittest.TestCase):
    def setUp(self):
        with open(SCHEMA_PATH) as f:
            self.schema = json.load(f)

    def test_top_level_is_object_with_endpoints(self):
        self.assertEqual(self.schema["type"], "object")
        self.assertIn("endpoints", self.schema["properties"])
        self.assertEqual(self.schema["properties"]["endpoints"]["type"], "array")

    def test_endpoint_required_fields(self):
        rec = self.schema["properties"]["endpoints"]["items"]
        required = set(rec["required"])
        self.assertEqual(
            required,
            {"method", "path", "responses", "provenance", "confidence"},
        )

    def test_confidence_enum(self):
        rec = self.schema["properties"]["endpoints"]["items"]
        self.assertEqual(
            set(rec["properties"]["confidence"]["enum"]),
            {"grounded", "inferred"},
        )

    def test_method_enum_includes_graphql_post(self):
        rec = self.schema["properties"]["endpoints"]["items"]
        methods = set(rec["properties"]["method"]["enum"])
        self.assertTrue({"GET", "POST", "PUT", "PATCH", "DELETE"}.issubset(methods))

    def test_provenance_has_source_and_locator(self):
        rec = self.schema["properties"]["endpoints"]["items"]
        prov = rec["properties"]["provenance"]
        self.assertEqual(set(prov["required"]), {"source", "locator"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run mockstar-mock/assets/test_inventory_schema.py`
Expected: FAIL — `FileNotFoundError` for `inventory.schema.json`.

- [ ] **Step 3: Write the schema**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://schemas.mockstar-mock.dev/inventory.schema.json",
  "title": "Endpoint Inventory IR",
  "type": "object",
  "required": ["endpoints"],
  "properties": {
    "tenant": {
      "type": "string",
      "pattern": "^[a-zA-Z0-9_-]{1,64}$",
      "description": "Target mockstar tenant directory (default 'default')."
    },
    "endpoints": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["method", "path", "responses", "provenance", "confidence"],
        "properties": {
          "method": {
            "type": "string",
            "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "*"]
          },
          "path": {
            "type": "string",
            "description": "Hono-style path with :param captures, e.g. /users/:id. GraphQL is modeled as POST /graphql."
          },
          "auth": {
            "type": "object",
            "description": "Auth match hints, e.g. { header: 'authorization', startsWith: 'Bearer ' }.",
            "properties": {
              "header": { "type": "string" },
              "startsWith": { "type": "string" }
            }
          },
          "request": {
            "type": "object",
            "properties": {
              "contentType": { "type": "string" },
              "schema": { "type": "object" },
              "example": {},
              "match": {
                "type": "object",
                "description": "Body/query/header match predicates lifted to mockstar match.* (equals/partial/jsonpath)."
              }
            }
          },
          "responses": {
            "type": "array",
            "minItems": 1,
            "items": {
              "type": "object",
              "required": ["status", "body"],
              "properties": {
                "status": { "type": "integer", "minimum": 100, "maximum": 599 },
                "headers": { "type": "object" },
                "body": {},
                "when": {
                  "type": "object",
                  "description": "If present, this response becomes a mockstar scenario (scenarios[].when on params/query/headers/body)."
                }
              }
            }
          },
          "statefulHints": {
            "type": "string",
            "description": "Free-text note that this endpoint participates in stateful flow (e.g. 'POST creates, GET returns it'). Drives dynamic handler inference."
          },
          "webhookHints": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "url": { "type": "string" },
                "event": { "type": "string" }
              }
            }
          },
          "provenance": {
            "type": "object",
            "required": ["source", "locator"],
            "properties": {
              "source": { "type": "string", "description": "Input file path or URL this record came from." },
              "locator": { "type": "string", "description": "JSON pointer, line range, heading, or operationId within the source." }
            }
          },
          "confidence": {
            "type": "string",
            "enum": ["grounded", "inferred"],
            "description": "grounded = explicit in a source; inferred = reasoned from context (flagged in the coverage report)."
          }
        }
      }
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run mockstar-mock/assets/test_inventory_schema.py`
Expected: PASS (`Ran 5 tests ... OK`).

- [ ] **Step 5: Commit**

```bash
git add mockstar-mock/references/inventory.schema.json mockstar-mock/assets/test_inventory_schema.py
git commit -m "feat(mockstar-mock): add Endpoint Inventory IR schema + test"
```

---

### Task 2: `input-adapters.md` reference

Per-format extraction guidance: how to turn each input type into Endpoint Inventory records. Prose, but with a structural test asserting every required format section exists.

**Files:**
- Create: `mockstar-mock/references/input-adapters.md`
- Test: `mockstar-mock/assets/test_input_adapters.sh`

**Interfaces:**
- Consumes: the IR field names from Task 1 (`method`, `path`, `responses`, `provenance`, `confidence`).
- Produces: a reference with one `## ` section per format, each anchored by an exact heading the test greps for.

- [ ] **Step 1: Write the failing test**

```sh
#!/bin/sh
# mockstar-mock/assets/test_input_adapters.sh — assert every adapter section exists.
set -eu
DOC="$(dirname "$0")/../references/input-adapters.md"
rc=0
for h in "## OpenAPI" "## Postman" "## HAR" "## curl" "## GraphQL" "## Prose (Markdown / PDF / DOCX / URL)" "## Merge & dedupe"; do
  if grep -qF "$h" "$DOC"; then
    echo "PASS: found '$h'"
  else
    echo "FAIL: missing '$h'"; rc=1
  fi
done
exit $rc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sh mockstar-mock/assets/test_input_adapters.sh`
Expected: FAIL — `input-adapters.md` does not exist (`grep: ... No such file`).

- [ ] **Step 3: Write the reference**

Create `mockstar-mock/references/input-adapters.md` with these sections (write real, concrete guidance under each — not placeholders):

- `# Input adapters` (intro: each adapter emits Endpoint Inventory records per `inventory.schema.json`; set `provenance.source` to the input path/URL and `provenance.locator` to a within-source pointer; set `confidence: grounded` only when the endpoint is explicit in the source).
- `## OpenAPI` — detect `openapi: 3.x`; prefer the **native** path (`mockstar import`) and skip manual extraction; only extract to IR when merging extra examples/error responses. Map operationId → `provenance.locator`.
- `## Postman` — collection v2.1; walk `item[]` recursively; each `request` → method+url (convert `:var`/`{{var}}` to Hono `:param`); `response[]` examples → `responses[]`; auth blocks → `auth`. Note the option to lift losslessly to OpenAPI for the native importer.
- `## HAR` — `log.entries[]`; group by method+URL path; `request.postData` → `request.example`; `response.status`/`content.text` → `responses[]`. De-duplicate repeated calls; keep distinct status codes as separate `responses[]`.
- `## curl` — parse `-X`/`--request`, URL, `-H`, `-d`/`--data`; one command → one endpoint; response unknown ⇒ synthesize a `200` echo body and mark `confidence: inferred`.
- `## GraphQL` — SDL or introspection; model as a single `POST /graphql` endpoint; each operation (query/mutation) → one `responses[]` entry with `request.match` = jsonpath/partial on `operationName`; per-operation example data ⇒ `when` so it becomes a scenario.
- `## Prose (Markdown / PDF / DOCX / URL)` — convert binaries via `assets/extract_text.py` (Task 5) and fetch URLs to text first; extract endpoints from request/response tables and code fences; everything not backed by an explicit example is `confidence: inferred`. Never invent endpoints absent from the text.
- `## Merge & dedupe` — key on `method`+`path`; on conflict prefer the higher-confidence/more-structured source (OpenAPI > Postman/HAR > prose); union distinct status codes; record conflicts for the coverage report; honor `--max-endpoints` and list drops.

- [ ] **Step 4: Run test to verify it passes**

Run: `sh mockstar-mock/assets/test_input_adapters.sh`
Expected: PASS — all seven headings found.

- [ ] **Step 5: Commit**

```bash
git add mockstar-mock/references/input-adapters.md mockstar-mock/assets/test_input_adapters.sh
git commit -m "feat(mockstar-mock): add input-adapters reference + section test"
```

---

### Task 3: `mockstar-mapping.md` reference

How an Endpoint Inventory record becomes mockstar config: `match`/`response`, scenarios, webhooks, dynamic handlers, Tier 2 tokens, GraphQL-as-POST, and priority rules.

**Files:**
- Create: `mockstar-mock/references/mockstar-mapping.md`
- Test: `mockstar-mock/assets/test_mockstar_mapping.sh`

**Interfaces:**
- Consumes: IR fields from Task 1.
- Produces: a reference anchored by exact headings the test greps for.

- [ ] **Step 1: Write the failing test**

```sh
#!/bin/sh
# mockstar-mock/assets/test_mockstar_mapping.sh
set -eu
DOC="$(dirname "$0")/../references/mockstar-mapping.md"
rc=0
for h in "## match" "## response" "## Scenarios" "## Dynamic handlers" "## Webhooks" "## Tier 2 tokens" "## GraphQL" "## Priority"; do
  if grep -qF "$h" "$DOC"; then echo "PASS: $h"; else echo "FAIL: missing $h"; rc=1; fi
done
# Tier 2 tokens must be documented verbatim so the generator emits valid ones.
for tok in '{{faker.uuid}}' '{{now.iso}}' '{{request.params.' '{{id('; do
  if grep -qF "$tok" "$DOC"; then echo "PASS token: $tok"; else echo "FAIL token: $tok"; rc=1; fi
done
exit $rc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sh mockstar-mock/assets/test_mockstar_mapping.sh`
Expected: FAIL — file missing.

- [ ] **Step 3: Write the reference**

Create `mockstar-mock/references/mockstar-mapping.md` with concrete mapping rules under each heading:

- `# IR → mockstar config mapping` (intro).
- `## match` — `method`+`path` → `match.method`/`match.path`; `auth` → `match.headers.authorization.startsWith`; `request.match` → `match.body` (`equals`/`partial`/`jsonpath`) and `match.query`.
- `## response` — first/default `responses[]` entry → `response` (`kind: static`, `status`, `headers`, `body`). Show the full mock-entry JSON shape from `docs/CONFIG.md`.
- `## Scenarios` — any `responses[]` entry with `when` → an element of `scenarios[]` (`{ id, when: {params|query|headers|body}, response }`). Show a worked example mirroring `docs/SCENARIOS.md`.
- `## Dynamic handlers` — `statefulHints` ⇒ `response.kind: "dynamic"` + `handler: "<name>"` and a TS file under `handlers/`; provide a minimal in-memory-state handler template; mark generated handlers `inferred` in the report.
- `## Webhooks` — `webhookHints[]` → `webhooks[]` on the triggering entry (`{ id, url, method, headers, body }`); note HMAC/retry are opt-in and not inferred by default.
- `## Tier 2 tokens` — table of tokens the generator may emit: `{{request.params.<n>}}`, `{{request.query.<n>}}`, `{{faker.uuid}}`, `{{faker.email}}`, `{{id("prefix_", 14)}}`, `{{now.iso}}`, `{{now.unix}}`, `{{tenant}}`, `{{requestId}}`. State that `mockstar enhance` (Stage 4) auto-applies id/timestamp tokens, so prefer literal examples and let enhance rewrite them.
- `## GraphQL` — one `POST /graphql` entry; `match.body.jsonpath: "$.operationName"` (or `partial`/`contains` on `query`); per-operation responses become `scenarios[]` keyed on `operationName`.
- `## Priority` — assign `match.priority` so specific entries beat catch-alls; document default 0 and that ties break by declaration order.

- [ ] **Step 4: Run test to verify it passes**

Run: `sh mockstar-mock/assets/test_mockstar_mapping.sh`
Expected: PASS — all headings and tokens found.

- [ ] **Step 5: Commit**

```bash
git add mockstar-mock/references/mockstar-mapping.md mockstar-mock/assets/test_mockstar_mapping.sh
git commit -m "feat(mockstar-mock): add IR->mockstar mapping reference + test"
```

---

### Task 4: `coverage-report.md` skeleton

The template for the `MOCKSTAR-COVERAGE.md` the skill emits — provenance, grounded-vs-inferred, speculative review list, gaps, drops, conflicts.

**Files:**
- Create: `mockstar-mock/references/coverage-report.md`
- Test: `mockstar-mock/assets/test_coverage_report.sh`

**Interfaces:**
- Produces: a markdown skeleton with fixed section headings the generator fills in.

- [ ] **Step 1: Write the failing test**

```sh
#!/bin/sh
# mockstar-mock/assets/test_coverage_report.sh
set -eu
DOC="$(dirname "$0")/../references/coverage-report.md"
rc=0
for h in "## Summary" "## Endpoints" "## Grounded vs inferred" "## Review me (speculative)" "## Gaps" "## Dropped" "## Conflicts"; do
  if grep -qF "$h" "$DOC"; then echo "PASS: $h"; else echo "FAIL: missing $h"; rc=1; fi
done
exit $rc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sh mockstar-mock/assets/test_coverage_report.sh`
Expected: FAIL — file missing.

- [ ] **Step 3: Write the skeleton**

Create `mockstar-mock/references/coverage-report.md`:

```markdown
# MOCKSTAR-COVERAGE

> Generated by the mockstar-mock skill. One row per endpoint; every mock traces to a source.

## Summary

- Inputs: <list of source files/URLs>
- Tenant: <tenant>
- Endpoints mocked: <n> (grounded <g> / inferred <i>)
- Verified: <boot PASS|FAIL>, routes smoked <ok>/<total>

## Endpoints

| Method | Path | Mock file | Source | Locator | Confidence |
|---|---|---|---|---|---|
| GET | /users/:id | mocks/default/users.json | api.yaml | #/paths/~1users~1{id}/get | grounded |

## Grounded vs inferred

- Grounded: <count> — explicit in a source.
- Inferred: <count> — reasoned from context (review before trusting).

## Review me (speculative)

Scenarios, dynamic handlers, and webhooks that were inferred, not documented:

- <handler|scenario|webhook> — <endpoint> — why inferred — source.

## Gaps

Endpoints/behaviors documented but NOT mocked:

- <method path> — <reason> — <source locator>.

## Dropped

Endpoints cut by --max-endpoints:

- <method path> — <source>.

## Conflicts

Endpoints described inconsistently across inputs and how they were resolved:

- <method path> — kept <source A> over <source B> because <reason>.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `sh mockstar-mock/assets/test_coverage_report.sh`
Expected: PASS — all seven headings found.

- [ ] **Step 5: Commit**

```bash
git add mockstar-mock/references/coverage-report.md mockstar-mock/assets/test_coverage_report.sh
git commit -m "feat(mockstar-mock): add coverage-report skeleton + test"
```

---

### Task 5: `extract_text.py` asset (PDF / DOCX / text)

Converts binary/prose inputs to plain text so the prose adapter can read them. PEP 723 inline deps via `uv`; graceful degradation when a converter is unavailable.

**Files:**
- Create: `mockstar-mock/assets/extract_text.py`
- Test: `mockstar-mock/assets/test_extract_text.py`

**Interfaces:**
- Produces: CLI `uv run mockstar-mock/assets/extract_text.py <path>` → prints extracted UTF-8 text to stdout; exit 0 on success, exit 3 with a clear stderr message when the format needs a converter that isn't installed. Supports `.txt`/`.md` (passthrough), `.pdf` (pypdf), `.docx` (python-docx).

- [ ] **Step 1: Write the failing test**

```python
# mockstar-mock/assets/test_extract_text.py
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Tests for extract_text.py dispatch and text passthrough — stdlib only."""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "extract_text.py")


def run(path):
    return subprocess.run(
        [sys.executable, SCRIPT, path],
        capture_output=True, text=True,
    )


class ExtractText(unittest.TestCase):
    def test_markdown_passthrough(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("# Title\nGET /ping returns pong\n")
            p = f.name
        try:
            r = run(p)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("GET /ping", r.stdout)
        finally:
            os.unlink(p)

    def test_unknown_extension_errors_cleanly(self):
        with tempfile.NamedTemporaryFile("w", suffix=".xyz", delete=False) as f:
            f.write("data")
            p = f.name
        try:
            r = run(p)
            self.assertEqual(r.returncode, 3)
            self.assertIn("unsupported", r.stderr.lower())
        finally:
            os.unlink(p)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run mockstar-mock/assets/test_extract_text.py`
Expected: FAIL — `extract_text.py` missing (nonzero exit / cannot open script).

- [ ] **Step 3: Write the implementation**

```python
# mockstar-mock/assets/extract_text.py
# /// script
# requires-python = ">=3.9"
# dependencies = ["pypdf>=4", "python-docx>=1"]
# ///
"""Extract plain text from .txt/.md (passthrough), .pdf (pypdf), .docx (python-docx).

Usage: uv run extract_text.py <path>
Exit codes: 0 ok | 2 usage | 3 unsupported/missing-converter | 4 read error
"""
import os
import sys


def _passthrough(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _pdf(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        sys.stderr.write("pdf support needs pypdf; run via `uv run` or `pip install pypdf`\n")
        sys.exit(3)
    reader = PdfReader(path)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _docx(path):
    try:
        import docx
    except ImportError:
        sys.stderr.write("docx support needs python-docx; run via `uv run` or `pip install python-docx`\n")
        sys.exit(3)
    d = docx.Document(path)
    return "\n".join(p.text for p in d.paragraphs)


HANDLERS = {".txt": _passthrough, ".md": _passthrough, ".markdown": _passthrough,
            ".pdf": _pdf, ".docx": _docx}


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: extract_text.py <path>\n")
        return 2
    path = argv[1]
    ext = os.path.splitext(path)[1].lower()
    handler = HANDLERS.get(ext)
    if handler is None:
        sys.stderr.write(f"unsupported extension '{ext}' (supported: {', '.join(sorted(HANDLERS))})\n")
        return 3
    try:
        sys.stdout.write(handler(path))
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — surface any read/parse failure clearly
        sys.stderr.write(f"failed to read {path}: {e}\n")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run mockstar-mock/assets/test_extract_text.py`
Expected: PASS (`Ran 2 tests ... OK`). (The test exercises only `.md` passthrough and the unsupported-extension path, so it runs without pypdf/python-docx installed.)

- [ ] **Step 5: Commit**

```bash
git add mockstar-mock/assets/extract_text.py mockstar-mock/assets/test_extract_text.py
git commit -m "feat(mockstar-mock): add extract_text helper (pdf/docx/text) + test"
```

---

### Task 6: `smoke.sh` asset (boot + per-route smoke)

The verification harness for Stage 5: boot `bunx mockstar serve` on the generated output, curl each route from a routes manifest, assert the server loaded and each route returns its expected status.

**Files:**
- Create: `mockstar-mock/assets/smoke.sh`
- Test: `mockstar-mock/assets/test_smoke.sh`

**Interfaces:**
- Produces: CLI `sh smoke.sh <out-dir> <routes-file>` where `<routes-file>` has one `METHOD<TAB>PATH<TAB>EXPECTED_STATUS` line per route. Boots `bunx mockstar serve <out-dir> --deterministic --no-watch --port <ephemeral>`, waits for readiness, curls each route, prints `PASS:`/`FAIL:` per route, exits nonzero if any route fails or the server never becomes ready. Supports `MOCKSTAR_SMOKE_BASE_URL` to test against an already-running server (used by the test to avoid requiring bunx).

- [ ] **Step 1: Write the failing test**

```sh
#!/bin/sh
# mockstar-mock/assets/test_smoke.sh — exercises route-checking without booting mockstar.
# Starts a tiny local HTTP stub, points smoke.sh at it via MOCKSTAR_SMOKE_BASE_URL.
set -eu
DIR="$(dirname "$0")"
SMOKE="$DIR/smoke.sh"

# 1) usage check
if sh "$SMOKE" 2>/dev/null; then echo "FAIL: expected usage error"; exit 1; else echo "PASS: usage errors without args"; fi

# 2) route-check against a stub server (python http.server returns 200 for GET /, 404 otherwise)
python3 -m http.server 8731 >/dev/null 2>&1 &
STUB=$!
trap 'kill "$STUB" 2>/dev/null || true' EXIT
sleep 1

ROUTES="$(mktemp)"
printf 'GET\t/\t200\n' > "$ROUTES"
if MOCKSTAR_SMOKE_BASE_URL="http://127.0.0.1:8731" sh "$SMOKE" --routes-only "$ROUTES"; then
  echo "PASS: smoke matched expected 200"
else
  echo "FAIL: smoke did not match"; exit 1
fi

printf 'GET\t/does-not-exist\t200\n' > "$ROUTES"
if MOCKSTAR_SMOKE_BASE_URL="http://127.0.0.1:8731" sh "$SMOKE" --routes-only "$ROUTES"; then
  echo "FAIL: smoke should have failed on 404"; exit 1
else
  echo "PASS: smoke detects status mismatch"
fi
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sh mockstar-mock/assets/test_smoke.sh`
Expected: FAIL — `smoke.sh` missing.

- [ ] **Step 3: Write the harness**

```sh
#!/bin/sh
# mockstar-mock/assets/smoke.sh — boot mockstar + smoke each route.
#
# Usage:
#   sh smoke.sh <out-dir> <routes-file>          # boot mockstar, then smoke
#   sh smoke.sh --routes-only <routes-file>      # smoke against MOCKSTAR_SMOKE_BASE_URL (no boot)
#
# routes-file: one line per route -> METHOD<TAB>PATH<TAB>EXPECTED_STATUS
set -eu

routes_only=0
if [ "${1:-}" = "--routes-only" ]; then
  routes_only=1; shift
  ROUTES="${1:-}"
  [ -n "$ROUTES" ] || { echo "usage: smoke.sh --routes-only <routes-file>" >&2; exit 2; }
  BASE="${MOCKSTAR_SMOKE_BASE_URL:?set MOCKSTAR_SMOKE_BASE_URL for --routes-only}"
else
  OUT="${1:-}"; ROUTES="${2:-}"
  { [ -n "$OUT" ] && [ -n "$ROUTES" ]; } || { echo "usage: smoke.sh <out-dir> <routes-file>" >&2; exit 2; }
  PORT="${MOCKSTAR_SMOKE_PORT:-3917}"
  BASE="http://127.0.0.1:$PORT"
  bunx mockstar serve "$OUT" --deterministic --no-watch --port "$PORT" >/tmp/mockstar-smoke.log 2>&1 &
  SERVER=$!
  trap 'kill "$SERVER" 2>/dev/null || true' EXIT
  # wait up to ~10s for readiness
  i=0
  while [ "$i" -lt 50 ]; do
    if curl -s -o /dev/null "$BASE" 2>/dev/null; then break; fi
    i=$((i + 1)); sleep 0.2
  done
  if [ "$i" -ge 50 ]; then
    echo "FAIL: mockstar did not become ready; log:" >&2; cat /tmp/mockstar-smoke.log >&2; exit 1
  fi
fi

rc=0
while IFS="$(printf '\t')" read -r METHOD PATHPART EXPECT; do
  [ -n "${METHOD:-}" ] || continue
  case "$METHOD" in \#*) continue;; esac
  GOT="$(curl -s -o /dev/null -w '%{http_code}' -X "$METHOD" "$BASE$PATHPART")"
  if [ "$GOT" = "$EXPECT" ]; then
    echo "PASS: $METHOD $PATHPART -> $GOT"
  else
    echo "FAIL: $METHOD $PATHPART -> $GOT (expected $EXPECT)"; rc=1
  fi
done < "$ROUTES"

[ "$routes_only" -eq 1 ] || true
exit $rc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `sh mockstar-mock/assets/test_smoke.sh`
Expected: PASS for all four assertions (usage error, 200 match, 404 mismatch detected). Requires `python3` and `curl` (both standard on the dev platform).

- [ ] **Step 5: Commit**

```bash
git add mockstar-mock/assets/smoke.sh mockstar-mock/assets/test_smoke.sh
git commit -m "feat(mockstar-mock): add boot+smoke verification harness + test"
```

---

### Task 7: End-to-end fixture smoke (mechanical pipeline against real mockstar)

Proves the bundled commands actually work end-to-end: a tiny OpenAPI fixture → `mockstar import` → `mockstar enhance` → boot + `smoke.sh`. This is the integration test for the skill's mechanical core (the agent-driven extraction stages are validated by the skill body, not here).

**Files:**
- Create: `mockstar-mock/assets/fixtures/petstore-mini.yaml`
- Create: `mockstar-mock/assets/fixtures/routes.tsv`
- Create: `mockstar-mock/assets/test_e2e.sh`

**Interfaces:**
- Consumes: `smoke.sh` (Task 6), the mockstar CLI.
- Produces: a runnable e2e test, skipped gracefully (exit 0 with a SKIP line) when `bunx`/network is unavailable.

- [ ] **Step 1: Write the failing test**

```sh
#!/bin/sh
# mockstar-mock/assets/test_e2e.sh — import -> enhance -> boot -> smoke against real mockstar.
set -eu
DIR="$(dirname "$0")"
FIX="$DIR/fixtures/petstore-mini.yaml"
ROUTES="$DIR/fixtures/routes.tsv"

command -v bunx >/dev/null 2>&1 || { echo "SKIP: bunx not available"; exit 0; }

OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

if ! bunx mockstar import "$FIX" "$OUT" --tenant default >/tmp/e2e-import.log 2>&1; then
  echo "FAIL: mockstar import"; cat /tmp/e2e-import.log; exit 1
fi
echo "PASS: import produced mocks"

bunx mockstar enhance "$OUT/default" >/tmp/e2e-enhance.log 2>&1 || true
echo "PASS: enhance ran"

sh "$DIR/smoke.sh" "$OUT" "$ROUTES"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `sh mockstar-mock/assets/test_e2e.sh`
Expected: FAIL — fixtures missing (`mockstar import` errors on absent spec), OR `SKIP` if bunx is unavailable. If it SKIPs, install bun (`curl -fsSL https://bun.sh/install | bash`) so the test runs for real before continuing.

- [ ] **Step 3: Create the fixtures**

`mockstar-mock/assets/fixtures/petstore-mini.yaml`:

```yaml
openapi: 3.0.3
info: { title: Petstore Mini, version: 1.0.0 }
paths:
  /pets:
    get:
      operationId: listPets
      responses:
        "200":
          description: ok
          content:
            application/json:
              example: [{ id: "pet_abc123", name: "Rex" }]
  /pets/{id}:
    get:
      operationId: getPet
      parameters:
        - { name: id, in: path, required: true, schema: { type: string } }
      responses:
        "200":
          description: ok
          content:
            application/json:
              example: { id: "pet_abc123", name: "Rex" }
```

`mockstar-mock/assets/fixtures/routes.tsv` (tab-separated; ensure real tabs):

```
GET	/pets	200
GET	/pets/pet_abc123	200
```

- [ ] **Step 4: Run test to verify it passes**

Run: `sh mockstar-mock/assets/test_e2e.sh`
Expected: PASS lines for import, enhance, and both smoked routes. If the importer maps the path differently (e.g. requires a trailing segment), adjust `routes.tsv` to match the actual generated `match.path` — inspect `"$OUT/default"/*.json` to confirm.

- [ ] **Step 5: Commit**

```bash
git add mockstar-mock/assets/fixtures mockstar-mock/assets/test_e2e.sh
git commit -m "test(mockstar-mock): add end-to-end import->enhance->smoke fixture test"
```

---

### Task 8: `SKILL.md` + `README.md` capstone

Tie everything together. `SKILL.md` carries the front-matter, invariants, flags, and the 6-stage procedure referencing the now-existing reference/asset files. `README.md` is the human-facing install/usage doc. This task is gated by `make gate-skill SKILL=mockstar-mock`.

**Files:**
- Create: `mockstar-mock/SKILL.md`
- Create: `mockstar-mock/README.md`
- Modify: `README.md` (repo root — add the skill to the install list + skills table)

**Interfaces:**
- Consumes: every file from Tasks 1–7 (referenced by relative path from `SKILL.md`).
- Produces: a gate-passing skill.

- [ ] **Step 1: Write `mockstar-mock/SKILL.md`**

Front-matter (description ≤ 1024 chars, trigger-rich):

```markdown
---
name: mockstar-mock
description: "Generate a runnable mockstar mock server for a service from its specs and docs — OpenAPI (json/yaml), Postman collections, HAR captures, curl examples, GraphQL SDL/introspection, and prose docs (Markdown/PDF/DOCX or a documentation URL). Use when asked to mock a service, stand up a fake/stub API, create mockstar mocks/fixtures, or scaffold a mock backend from an API spec or documentation. Normalizes every input into one Endpoint Inventory, uses native `mockstar import` for OpenAPI and hand-authors the rest, infers scenarios/dynamic handlers/webhooks at full fidelity, runs `mockstar enhance` for Tier 2 placeholders, then boots the server and smoke-tests every route. Tags each mock with provenance and confidence and emits a coverage report flagging speculative inferences and documented-but-unmocked gaps. Not for the mockstar HTTPS proxy or native GraphQL semantics. Targets the mockstar CLI (`bunx mockstar`)."
x-spec-version: 1.0
---
```

Body (write in full — these are the load-bearing sections):

- `# mockstar-mock` — one-paragraph intro; the defining rule: **no fabricated endpoints** (every mock traces to a fetched/provided source; inferred behavior is tagged and reported).
- `## When to use` / `## When not to use` (not the proxy; not native GraphQL semantics).
- `## Prerequisites` — `bunx mockstar` available; `uv` for the Python helpers; `curl`.
- `## Invariants (do not violate)` — the five from the spec, verbatim:
  1. No fabricated endpoints. 2. Prefer native tooling (`mockstar import`/`enhance`). 3. Schema-valid output (verification boots the server). 4. No silent truncation (`--max-endpoints` drops + conflicts in the report). 5. Read-only inputs.
- `## Flags` — `--into <dir>`, `--tenant <name>` (default `default`), `--fidelity full|static` (default full), `--no-verify`, `--deterministic`, `--max-endpoints N`. One line each.
- `## Procedure` — the 6 stages, each pointing to its reference/asset by relative path:
  1. **Intake & classify** — detect type by signature; convert binaries/URLs to text via `assets/extract_text.py`.
  2. **Extract → Endpoint Inventory** — fan out a subagent per input following `references/input-adapters.md`; emit records valid against `references/inventory.schema.json`; merge & dedupe; honor `--max-endpoints`.
  3. **Generate config (hybrid)** — OpenAPI (and losslessly-liftable Postman/HAR) via `bunx mockstar import <spec> <out>/.. --tenant <t>`; everything else hand-authored per `references/mockstar-mapping.md`; apply full-fidelity scenarios/handlers/webhooks when `--fidelity full`.
  4. **Enhance** — `bunx mockstar enhance <out>/mocks/<tenant>` (pass `--spec` when an OpenAPI input exists).
  5. **Verify** — unless `--no-verify`, write a routes TSV and run `sh <skill>/assets/smoke.sh <out> <routes.tsv>`; iterate on failures.
  6. **Report** — write `MOCKSTAR-COVERAGE.md` from `references/coverage-report.md`.
- `## Output layout` — default scaffold vs `--into` (from the spec).
- `## Subagent dispatch note` — resolve the skill's absolute base dir once and pass absolute paths to `assets/extract_text.py` and `assets/smoke.sh` into subagent prompts (mirror the `base-in-reality` caveat: relative `assets/` paths don't resolve from the target repo).

- [ ] **Step 2: Write `mockstar-mock/README.md`**

Human-facing: what the skill does, install line (`npx skills add dhanesh/agent-skills --skill mockstar-mock`), prerequisites (bun/mockstar, uv, curl), a 4-line usage example, and a pointer to `SKILL.md` + the mockstar repo.

- [ ] **Step 3: Add to the repo root `README.md`**

Add the install example line and a skills-table row. Exact edits:

In the install examples block, after the `tmux-agent-herdr-lite` line, add:
```
npx skills add dhanesh/agent-skills --skill mockstar-mock
```

In the `## Skills` table, add a row:
```
| [`mockstar-mock`](mockstar-mock/) | Generate a runnable mockstar mock server from a service's specs/docs — OpenAPI, Postman, HAR, curl, GraphQL, and prose docs (md/pdf/docx/url) — normalized into one Endpoint Inventory, full-fidelity (scenarios/handlers/webhooks), Tier 2-enhanced, boot-and-smoke verified, with a provenance + coverage report. |
```

- [ ] **Step 4: Run the skill gate**

Run: `make gate-skill SKILL=mockstar-mock`
Expected: `VALIDATION_RESULT: PASS` and the leak scan passes. If validate-skill.sh reports a dangling reference, fix the path in `SKILL.md` (the referenced file must exist from Tasks 1–7). If `description` exceeds 1024 chars, trim it.

- [ ] **Step 5: Run every asset/schema test together**

Run:
```bash
uv run mockstar-mock/assets/test_inventory_schema.py \
 && uv run mockstar-mock/assets/test_extract_text.py \
 && sh mockstar-mock/assets/test_input_adapters.sh \
 && sh mockstar-mock/assets/test_mockstar_mapping.sh \
 && sh mockstar-mock/assets/test_coverage_report.sh \
 && sh mockstar-mock/assets/test_smoke.sh \
 && sh mockstar-mock/assets/test_e2e.sh
```
Expected: all PASS (e2e may SKIP only if bunx is unavailable — otherwise PASS).

- [ ] **Step 6: Commit**

```bash
git add mockstar-mock/SKILL.md mockstar-mock/README.md README.md
git commit -m "feat(mockstar-mock): add SKILL.md + README, register skill in repo index"
```

---

### Task 9: Full repo gate + PR

Final verification across all skills and open the PR.

**Files:** none (CI/validation only).

- [ ] **Step 1: Run the full gate**

Run: `make gate`
Expected: every skill (including `mockstar-mock`) prints a passing tail line; overall exit 0.

- [ ] **Step 2: Verify the branch diff is scoped**

Run: `git status && git log --oneline main..HEAD`
Expected: only `mockstar-mock/**`, the root `README.md`, and the two `docs/superpowers/**` files changed across the branch.

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin skill/mockstar-mock
gh pr create --title "feat: add mockstar-mock skill" --body "$(cat <<'EOF'
Adds the `mockstar-mock` skill: converts OpenAPI / Postman / HAR / curl / GraphQL / prose docs into a runnable, full-fidelity mockstar mock project — provenance-tagged, `mockstar enhance`-d, and boot-and-smoke verified, with a coverage report.

Spec: docs/superpowers/specs/2026-06-21-mockstar-mock-skill-design.md
Plan: docs/superpowers/plans/2026-06-22-mockstar-mock-skill.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
Expected: PR created; `make gate` green in CI (`.github/workflows/skill-gates.yml`).

---

## Self-Review

**Spec coverage:**
- Broad inputs (OpenAPI/Postman/HAR/curl/GraphQL/md/pdf/docx/url) → Task 2 (`input-adapters.md`) + Task 5 (`extract_text.py`). ✓
- Hybrid pipeline (native import + hand-author) → Task 3 mapping + Task 8 Stage 3; native import exercised in Task 7. ✓
- Full fidelity (scenarios/handlers/webhooks) → Task 3 sections. ✓
- Boot + smoke verification → Task 6 (`smoke.sh`) + Task 7 (e2e). ✓
- Deliverable both-modes (`--into`) → Task 8 Flags + Output layout. ✓
- Tag + coverage report → Task 1 (`provenance`/`confidence` fields) + Task 4 (`coverage-report.md`). ✓
- Invariants → Task 8 `## Invariants`. ✓
- Flags (`--into/--tenant/--fidelity/--no-verify/--deterministic/--max-endpoints`) → Task 8 `## Flags`. ✓
- Skill structure mirrors base-in-reality → Tasks 1–8 produce exactly the spec's file tree. ✓
- GraphQL-as-POST + single-tenant default → Task 2 (GraphQL), Task 3 (GraphQL), Task 1 (`tenant` default), Task 8. ✓
- Open question 1 (PDF/DOCX extraction) → resolved: bundled `extract_text.py` via `uv` PEP 723 with graceful degradation (Task 5). ✓
- Open question 2 (deterministic helper vs agent judgment) → resolved: generation stays agent-driven; the deterministic contract is the IR schema + native `mockstar import`/`enhance` + boot verification; no separate generator script. ✓

**Placeholder scan:** No "TBD/TODO"; prose-reference tasks specify exact required headings enforced by grep tests; all code steps include complete code. ✓

**Type consistency:** IR field names (`method`, `path`, `responses`, `provenance`, `confidence`, `when`, `statefulHints`, `webhookHints`, `tenant`) are defined in Task 1 and referenced identically in Tasks 2/3/4/8. `smoke.sh` interface (`METHOD<TAB>PATH<TAB>EXPECTED_STATUS`, `--routes-only`, `MOCKSTAR_SMOKE_BASE_URL`) is consistent across Tasks 6/7. ✓
