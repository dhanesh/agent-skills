# mockstar-mock

A skill that generates a runnable [mockstar](https://github.com/dhanesh/mockstar) mock server
from any combination of API specs and documentation — OpenAPI (JSON/YAML), Postman collections,
HAR captures, curl command files, GraphQL SDL/introspection, and prose docs (Markdown, PDF,
DOCX, or a documentation URL).

It normalizes every input into a single Endpoint Inventory, uses `mockstar import` for OpenAPI
and hand-authors the rest, infers scenarios/dynamic handlers/webhooks — including
provider-fidelity webhook signing — at full fidelity, runs
`mockstar enhance` for Tier 2 placeholder rewriting, then boots the server and smoke-tests every
route. Each mock is tagged with provenance and confidence; a coverage report flags speculative
inferences and documented-but-unmocked gaps.

## Install

```bash
npx skills add dhanesh/agent-skills --skill mockstar-mock
```

## Prerequisites

- **Bun** with `bunx @dhaneshpurohit/mockstar` available — install mockstar globally
  (`bun add -g @dhaneshpurohit/mockstar`) or rely on `bunx` to fetch it on first run.
  **Package name matters:** the unscoped `mockstar` on npm is an unrelated project.
  Requires **>= 0.2.2**; configurable webhook signing schemes require **>= 0.3.0**.
- **uv** — used to run the Python helper that converts PDF/DOCX inputs and documentation URLs
  to plain text.
- **curl** — used by the smoke test suite to verify every generated route.

## Usage

After installing the skill, invoke it from Claude Code by describing what you want to mock:

```
/mockstar-mock --into ./my-mock api.yaml docs/api-guide.md
```

```
/mockstar-mock --into ./petstore-mock --tenant=acme petstore.yaml
```

The skill produces a ready-to-run mockstar project. Boot it with:

```bash
bunx mockstar ./my-mock
bunx mockstar ./my-mock --deterministic --no-watch --port 3000
```

## Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--into <dir>` | `mock-<service>` | Output directory |
| `--tenant=<name>` | `default` | Tenant name for `mockstar import` (equals form required; space form is silently ignored by the importer) |
| `--fidelity full\|static` | `full` | Full generates scenarios/handlers/webhooks; static is one response per route |
| `--no-verify` | off | Skip boot-and-smoke verification |
| `--deterministic` | off | Disable Faker randomness for reproducible responses |
| `--max-endpoints N` | unlimited | Cap the Endpoint Inventory; excess are reported |

## Output

```
mock-<service>/
  <tenant>/           # mockstar mock JSON files
    handlers/         # TypeScript dynamic handlers (--fidelity full)
  routes.tsv
  MOCKSTAR-COVERAGE.md
```

`MOCKSTAR-COVERAGE.md` lists every mocked endpoint with its source, confidence level, and any
speculative inferences that should be reviewed before using in production.

## Learn more

- `SKILL.md` — full procedure, invariants, flags, and subagent dispatch notes.
- [mockstar repository](https://github.com/dhanesh/mockstar) — CLI reference, config schema, and Tier 2 token docs.
