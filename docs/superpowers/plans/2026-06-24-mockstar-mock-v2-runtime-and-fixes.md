# mockstar-mock v2 — audit fixes + version preflight + Docker runtime

> Addendum to `2026-06-22-mockstar-mock-skill.md`. Executed via subagent-driven-development.
> Source of issues: `docs/base-in-reality/2026-06-24-mockstar-mock-audit.md`.

**Goal:** Fix the audit findings, make the skill self-ground against the live mockstar (version + CLI-surface preflight + JSON-Schema), and add first-class Docker runtime support (local `bunx` *or* the `ghcr.io/dhanesh/mockstar` image), Docker-preferred by default.

## Global Constraints (verified against mockstar 0.1.1 source)

- **`import` tenant flag is EQUALS-ONLY:** `src/cli.ts:57` parses `--tenant=<name>` via `startsWith("--tenant=")`. The space form `--tenant <name>` is silently dropped (→ default tenant). Every `mockstar import` invocation in the skill MUST use `--tenant=<tenant>`. (`enhance`/`serve` flags use `getFlag`, which accepts both forms — only `import` is equals-only.)
- **Serve is the default command:** `bunx mockstar <config-root>` — NO `serve` subcommand (`cli.ts:89`). Unchanged.
- **Docker image contract** (`../mockstar/README.md`, `Dockerfile`): `ghcr.io/dhanesh/mockstar:<tag>` (multi-arch, signed, ships no mocks). Default cmd serves `/config/mocks`; handlers from `/config/handlers`; `EXPOSE 3000`; `ENV MOCKSTAR_HOST=0.0.0.0 MOCKSTAR_PORT=3000`; non-root uid `10001`; `ENTRYPOINT ["bun","./dist/cli.js"]`, `CMD ["/config/mocks"]`. Health: `GET /health` → `{"status":"ok"}`. Config-root holds tenant dirs directly (e.g. `/config/mocks/default/users.json`).
- **Runtime default:** `--runtime auto` prefers Docker (pinned tag) when the daemon is up and the image is reachable, else falls back to local `bunx`.
- **Gate unchanged:** `make gate-skill SKILL=mockstar-mock` must stay `VALIDATION_RESULT: PASS`; description ≤1024; no dangling refs; no PARAMETERS.md.
- **Exported schema for grounding:** mockstar ships `schema/mock.json` and recommends pinning generated files' `$schema` to `https://schemas.mockstar.dev/v0.<N>/mock.json`. Boot (local or container) is the authoritative Zod fail-fast validator.

---

### Task E1: Audit fixes (F1–F5) + non-default-tenant e2e assertion

**Files:** `mockstar-mock/SKILL.md`, `mockstar-mock/README.md`, `mockstar-mock/assets/test_e2e.sh`, `mockstar-mock/assets/fixtures/routes.tsv` (maybe), `docs/superpowers/plans/2026-06-22-mockstar-mock-skill.md` + `docs/superpowers/specs/2026-06-21-mockstar-mock-skill-design.md` (version de-pin note).

- **F1 (high):** Replace every `--tenant <name>` (space) with `--tenant=<name>` (equals) where `mockstar import` is invoked: SKILL.md:152, SKILL.md:62 (flag doc — clarify the equals form is required for import), README.md:37 and the flag-table row 52. Add to `test_e2e.sh` a second import into a **non-default** tenant (`--tenant=acme`) and assert mocks land in `<OUT>/acme/` (proving the flag is honored — the regression test for F1).
- **F2 (med):** SKILL.md:189–191 — reword: `enhance` rewrites literal IDs/timestamps to Tier-2 tokens and writes a `_mockstarGenerated` manifest (idempotent); **schema conformance is proven by the Stage-5 boot (Zod fail-fast), not by enhance.**
- **F3 (med):** SKILL.md:103–106 — URLs are NOT handled by `extract_text.py`. Fetch a URL to text with `curl -L`/WebFetch first; use `extract_text.py` only for local `.pdf/.docx/.txt/.md`. Fix the code block so it doesn't pass a URL to `$EXTRACT`.
- **F4 (low):** SKILL.md:214 — reword to: "Set `MOCKSTAR_SMOKE_PORT` if you need a non-default smoke port." (Decouple from `--deterministic`.)
- **F5 (low):** In the plan + spec docs, change the `0.1.0-alpha.1` pin to note mockstar is now `0.1.x` stable and the skill is version-agnostic (no hardcoded version). Do not introduce a new hardcoded version in the skill.

Verify: `sh mockstar-mock/assets/test_input_adapters.sh` etc. unaffected; `make gate-skill SKILL=mockstar-mock` PASS; `sh mockstar-mock/assets/test_e2e.sh` GREEN incl. the new acme-tenant assertion. Commit.

---

### Task E2: smoke.sh — Docker runtime path + /health readiness

**Files:** `mockstar-mock/assets/smoke.sh`, `mockstar-mock/assets/test_smoke.sh`.

- Add a runtime selector: `MOCKSTAR_SMOKE_RUNTIME=local|docker` (default `local`), or a `--runtime <r>` arg. Keep `--routes-only` working.
- **local path:** unchanged boot `bunx mockstar <out> --deterministic --no-watch --port <PORT>`. Switch readiness to poll `GET /health` (more reliable than `/`), falling back to any HTTP response.
- **docker path:** `docker run -d --rm -p <PORT>:3000 -v "<absMocks>:/config/mocks:ro" [-v "<absHandlers>:/config/handlers:ro"] <IMAGE>` where IMAGE defaults to `ghcr.io/dhanesh/mockstar:latest` (override `MOCKSTAR_SMOKE_IMAGE`). Poll `http://127.0.0.1:<PORT>/health`. On teardown `docker stop`/`rm` the container (trap). If docker daemon down or image unreachable → print `SKIP: docker unavailable` and exit 0 (graceful, like e2e).
- **test_smoke.sh:** keep the existing stub-based `--routes-only` assertions (runtime-agnostic). Add a docker-path assertion ONLY guarded by docker availability (skip-gracefully) that boots the image against the petstore fixture mounts and smokes `/health`. Do not weaken the harness to force a pass.

Verify: `sh mockstar-mock/assets/test_smoke.sh` PASS (docker case runs for real since daemon is up). Commit.

---

### Task E3: Stage 0 compatibility preflight + runtime abstraction (SKILL.md)

**Files:** `mockstar-mock/SKILL.md`.

- Add **`--runtime auto|local|docker`** to the Flags section (default `auto` = Docker-preferred). Document `--image <ref>` (default `ghcr.io/dhanesh/mockstar:latest`, recommend pinning by digest for reproducibility).
- Add **Stage 0 — Compatibility preflight** before Stage 1:
  1. Resolve runtime per `--runtime` (auto: docker if `docker version` server up AND image reachable, else local).
  2. Detect mockstar version: local `bunx mockstar version`; docker `docker run --rm <image> version`. Record it. Note the known caveat that the CLI's printed version may lag the package version.
  3. Validate the CLI surface it will use against live `bunx mockstar help` / `docker run --rm <image> help`: confirm `import`/`enhance`/serve-as-default exist; **discover the real `--tenant` flag form** rather than assuming (self-heals if mockstar changes). If the live surface contradicts this skill's assumptions, prefer the live surface and note it in the report.
  4. Record detected runtime + version + image digest in the coverage report's Runtime section (Task E5).
- Update **Stage 5** to verify via the chosen runtime: `sh assets/smoke.sh` with `MOCKSTAR_SMOKE_RUNTIME=<runtime>` (and `MOCKSTAR_SMOKE_IMAGE` when docker).
- Update **Output layout**: config-root is `mocks/` containing `<tenant>/` dirs; `handlers/` is its sibling. This maps directly to `/config/mocks` + `/config/handlers` for Docker, and to `bunx mockstar mocks/ --handlers handlers/` locally.

Verify: `make gate-skill SKILL=mockstar-mock` PASS (description still ≤1024; no dangling refs). Commit.

---

### Task E4: Docker delivery artifacts (Dockerfile + mount command)

**Files:** `mockstar-mock/assets/Dockerfile.template`, `mockstar-mock/SKILL.md` (Output/Docker section), `mockstar-mock/references/mockstar-mapping.md` (brief `$schema` stamp note).

- Add `assets/Dockerfile.template` (bake-in): `FROM ghcr.io/dhanesh/mockstar:<TAG>` then `COPY --chown=10001:10001 mocks/ /config/mocks/` and (conditionally) `COPY --chown=10001:10001 handlers/ /config/handlers/`. Comment that ENTRYPOINT/CMD are inherited and recommend pinning the base by digest. Use a clear `<TAG>` placeholder the skill fills with the detected version/digest.
- In SKILL.md, document that the skill emits BOTH: (a) a ready `docker run --rm -p 3000:3000 -v "<abs>/mocks:/config/mocks:ro" -v "<abs>/handlers:/config/handlers:ro" <image>` mount command (dev), and (b) a baked `Dockerfile` (from the template) + `docker build`/`docker run` snippet (share/CI).
- Stamp generated mock files with `$schema` pinned to `https://schemas.mockstar.dev/v0.<N>/mock.json` (note in mockstar-mapping.md), so boot-time validation and editors agree.

Verify: gate PASS; the Dockerfile.template is referenced by SKILL.md (so it must exist — no dangling ref). Commit.

---

### Task E5: coverage-report.md — Runtime & compatibility section

**Files:** `mockstar-mock/references/coverage-report.md`, `mockstar-mock/assets/test_coverage_report.sh`.

- Add a `## Runtime & compatibility` section to the skeleton: runtime used (local/docker), mockstar version + image ref/digest, CLI-surface preflight result (and any surface drift noted), and schema-validation/boot result.
- Add `## Runtime & compatibility` to the section-heading grep test.

Verify: `sh mockstar-mock/assets/test_coverage_report.sh` PASS (now 8 headings). Commit.

---

### Task E6: Full re-validation

- `make gate` (6 skills) PASS.
- All asset tests: inventory_schema, extract_text, input_adapters, mockstar_mapping, coverage_report, smoke (local + docker), e2e (local + acme-tenant). Docker smoke runs for real (daemon up, image reachable).
- Spot re-audit the changed mockstar claims (tenant form, runtime, health endpoint) against `../mockstar` source.
- Update `docs/base-in-reality/2026-06-24-mockstar-mock-audit.md` status: findings resolved.
- Final whole-branch review, then finish.
