# base-in-reality audit — `mockstar-mock` skill

**Date:** 2026-06-24
**Target:** `mockstar-mock/` (skill in this repo)
**Trusted anchor:** the live `../mockstar` source/docs at version **0.1.1** (source files as the anchor, per project convention — not WebFetch). External format claims checked against published standards (OpenAPI 3.x, Postman Collection v2.1, HAR 1.2, GraphQL introspection).
**Mode:** read-only. No skill files were modified by this audit.

## Executive summary

The skill's hard-won mockstar facts **still hold in 0.1.1** — most importantly the corrected serve-as-default invocation, the attribute-keyed scenario `when` shape, the `match`/`response` shapes, the Tier-2 token set, and GraphQL-still-deferred. One **real correctness bug** surfaced (`--tenant` flag form is silently ignored by `mockstar import`), plus three accuracy/wording deviations and one stale version reference (docs-only). None are critical; the `--tenant` issue is the only one that produces wrong output, and only for non-default tenants.

## Domain map

API-mocking / developer-tooling. Norms surface: the mockstar CLI + config schema (primary, version-pinned) and API-description standards (OpenAPI/Postman/HAR/GraphQL).

## Findings (severity ↓)

### F1 — `mockstar import --tenant <name>` (space form) is silently ignored — DEVIATION, **high**
- **Anchor:** `../mockstar/src/cli.ts:57` — `tenantName: rest.find((r) => r.startsWith("--tenant="))?.slice("--tenant=".length)`. Import parses the tenant flag **only** in the `--tenant=<name>` (equals) form. It does **not** use `getFlag` (which would also accept the space form). `enhance`/`serve` flags *do* use `getFlag` (`cli.ts:106-112`), so the inconsistency is import-specific.
- **Skill claim:** SKILL.md:152 `bunx mockstar import <spec-file> <out-dir> --tenant <tenant>`; SKILL.md:62 and README.md:37,52 all use the **space** form (`--tenant <name>`, and the example `--tenant acme`).
- **Impact:** for any non-default tenant, `--tenant acme` is dropped and mocks land in `default/` instead of `acme/`. Silent — no error.
- **Why Task 7's e2e didn't catch it:** the e2e used `--tenant default`, so the ignored flag coincidentally matched the importer's default tenant. The "PASS" was a false confirmation of the flag.
- **Refutation:** survived. (correctness — code is equals-only; applicability — import is the skill's only `--tenant` consumer; severity — bites only non-default tenants, downgraded from critical to high.)
- **Fix:** use `--tenant=<tenant>` everywhere `import` is invoked (SKILL.md:152, README.md:37, and the flag-doc rows). Add a non-default-tenant assertion to `test_e2e.sh` so this can't regress.

### F2 — "`enhance` … validates that all generated files are schema-conformant" — DEVIATION, **medium**
- **Anchor:** `../mockstar/docs/ENHANCE.md` + `docs/CONFIG.md` ("validated by Zod **at boot**"). `enhance` rewrites literal IDs/timestamps to Tier-2 tokens and writes a `_mockstarGenerated` manifest; it is not a schema validator. Schema conformance is enforced when the server boots.
- **Skill claim:** SKILL.md:189-191.
- **Impact:** misattributes where validation happens; could let an agent treat enhance as the validation gate and skip the Stage-5 boot.
- **Fix:** reword — enhance rewrites tokens (idempotent, manifest-tracked); schema conformance is proven by the Stage-5 boot.

### F3 — Stage 1 tells you to run `extract_text.py` on a URL — DEVIATION, **medium**
- **Anchor:** `mockstar-mock/assets/extract_text.py` HANDLERS = `.txt/.md/.markdown/.pdf/.docx` only; a URL hits "unsupported extension" → exit 3. `references/input-adapters.md` correctly fetches URLs with `curl -L`/WebFetch.
- **Skill claim:** SKILL.md:103-106 — "For binary inputs (PDF, DOCX) **and documentation URLs** … `uv run "$EXTRACT" <input-file-or-url>`".
- **Impact:** an agent passes a URL to the helper, gets exit 3. Self-contradictory (SKILL.md vs its own asset and reference).
- **Fix:** SKILL.md should fetch URLs to text first (curl -L / WebFetch), then use `extract_text.py` only for local PDF/DOCX/text.

### F4 — Incoherent smoke note conflating `--deterministic` with the port env var — accuracy, **low**
- **Anchor:** `mockstar-mock/assets/smoke.sh:21` — `MOCKSTAR_SMOKE_PORT` sets the smoke port; unrelated to determinism.
- **Skill claim:** SKILL.md:214 — "Pass `--deterministic` via the environment variable `MOCKSTAR_SMOKE_PORT` if you need a non-default port."
- **Fix:** "Set `MOCKSTAR_SMOKE_PORT` if you need a non-default port."

### F5 — Version pin `0.1.0-alpha.1` is stale — OUTDATED, **low** (docs-only)
- **Anchor:** `../mockstar/package.json` = `0.1.1` (first stable was 0.1.0). Note: mockstar's own `src/cli.ts:14` `MOCKSTAR_VERSION` constant is *also* still `0.1.0-alpha.1`, so `bunx mockstar version` literally still prints the alpha string — a latent bug in mockstar, not the skill.
- **Skill claim:** the **shipped skill hardcodes no version** (grep clean). Only `docs/superpowers/plans/...` and `...specs/...` reference `0.1.0-alpha.1`.
- **Impact:** negligible — the skill is version-agnostic (targets `bunx mockstar`). Update the plan/spec note for accuracy.

## Re-confirmed correct against 0.1.1 (no finding)

- **Serve-as-default:** `cli.ts:89` "Default: serve from positional config root" — no `serve` parser branch. `bunx mockstar <out>` is right; the skill's "do NOT use `bunx mockstar serve`" stance is correct. ✓
- **Scenario `when`:** `ScenarioPredicate` is attribute-keyed (`params/query/headers/body` → StringMatch) (`schema.ts:107-118`). Skill's GraphQL `when.body: {operationName}` / `{query:{contains}}` is valid. ✓
- **`match.body`** equals/partial/jsonpath; **`response.kind`** static/dynamic/passthrough (`schema.ts`). ✓
- **Tier-2 tokens:** every token the skill emits exists in `docs/TIER2.md`; the skill lists a safe subset (omits `request.method`/`request.path` and the custom-alphabet `id()` variants) and fabricates none. `env.*` is correctly treated as webhooks-only, not a response token. ✓
- **Handler signature, webhook shape, GraphQL-deferred, `enhance --spec` (space form OK via getFlag):** all match source. ✓
- **External formats (Class B):** OpenAPI 3.x / Postman v2.1 schema URL / HAR 1.2 / GraphQL introspection markers and the curl/Postman/HAR field mappings are consistent with the published standards. ✓

## Systemic recommendation

F1 and F5 are both **drift** between baked-in skill text and the moving mockstar target. The durable fix is a **compatibility preflight** in the skill (see the conversation): before generating, detect the installed mockstar version (`bunx mockstar version`) and validate the CLI surface it will use against live `bunx mockstar help` / the exported config JSON Schema (`dist/schema.json` / the `$schema` URL), preferring live truth over baked-in claims and recording the detected version in the coverage report.

## Resolution status (2026-06-24)

All findings resolved on branch `skill/mockstar-mock` (plan `2026-06-24-mockstar-mock-v2-runtime-and-fixes.md`):

- **F1** — fixed: every `mockstar import` now uses `--tenant=<name>` (equals); `test_e2e.sh` adds a non-default-tenant (`--tenant=acme`) regression assertion that verifies mocks land in `acme/` (GREEN against real mockstar).
- **F2/F3/F4** — fixed: enhance wording (boot is the Zod validator), URL handling (curl/WebFetch, not `extract_text.py`), and the port-note wording all corrected in SKILL.md.
- **F5** — fixed: plan/spec docs de-pinned to "0.1.x stable"; the shipped skill hardcodes no version.
- **Systemic** — implemented: a Stage 0 compatibility preflight detects the runtime + mockstar version and validates the live CLI surface (discovering the real `--tenant` form) before generating; the coverage report records runtime/version/digest; first-class **Docker** runtime added (image `ghcr.io/dhanesh/mockstar`, `/health` smoke, mount + bake `Dockerfile` artifacts), Docker-preferred by default.

## Dropped-claims log

None — claim set was within scope; both layers (mockstar-behavior, external-format) covered.
