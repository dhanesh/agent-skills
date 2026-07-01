# Capture: markers, the `wm` CLI, and the trust boundary

The update path is **hybrid**: deterministic hooks capture the *skeleton* (which files/symbols
were touched, at low confidence); the agent enriches the graph *explicitly*. A model never
guesses facts inside a hook — that is how you get invented facts.

To avoid a cold start, `wm build [path]` seeds the whole repo in one deterministic pass with
**language-aware extraction**:

- **Local edges** (file → file): `imports` / `includes` / `references`, resolved to a real
  scanned file. Covers Python (`import`/`from`, incl. relative), Ruby (`require_relative`),
  JavaScript/TypeScript (relative `import`/`require`), Rust (`mod`), Java
  (`import` resolved by fully-qualified class name via each file's `package` declaration, source-root
  agnostic), C/C++ (`#include "…"`), PHP (`require`/`include`), plus shell `source`, Make
  `include`, Dockerfile `COPY`, and generic path mentions in docs/config.
- **External dependency edges** (file → `depends_on` → *referent*): a dependency literally
  declared in the source — a package (JS/TS `import 'react'`, Ruby `gem`, Rust `use <crate>`,
  Go `import "github.com/…"`, Python third-party `import`, Java `import org.springframework.…`,
  C# `using Newtonsoft.Json`, PHP `use Symfony\…`, C/C++ library headers `#include <boost/…>`),
  a container image (Dockerfile `FROM`, compose/k8s `image:`), a CI action (GitHub Actions
  `uses:`), or a Terraform module `source`. Language stdlibs (Go `fmt`, Rust `std`, Python `os`,
  `java.*`/`javax.*`, C# `System.*`, PHP `App\` app namespace, bare `<stdio.h>`) are skipped.
  Java/C#/PHP external names are coarse (package prefix / vendor namespace); precise artifacts
  need manifest parsing (`pom.xml`/`.csproj`/`composer.json`), a planned follow-up.

Like the hooks, it is **observation only**: `observed_conf` rises but `normative_conf` stays 0
and status stays `unverified` — a *declared* dependency is a sighting, not proof it is correct
or desirable. No invented facts: a file→file edge is added only when the target resolves to a
real scanned file, and every `depends_on` referent is a literal token from the source
(`FROM`/`uses`/`import`). `build` now auto-creates these **dependency** referents; *semantic /
domain* referents (business concepts, higher-level services) still come from the agent via
`wm map`. External deps are capped per file and deduped (one referent, many `depends_on` edges).

**Re-run safety.** Every write is an upsert on a stable key — entities on `symbol_id`,
interactions on `(subject, predicate, object)`, evidence on `(fact, kind, ref, polarity)` —
and `build` issues no `DELETE`. So repeated runs only *add new* rows or *refresh existing* ones
(`first_seen` preserved, `last_seen` bumped); they never duplicate, never delete, and never
downgrade a fact you have already validated (a re-added edge is just another observation; its
oracle evidence still stands). The build result reports `entities_added` / `interactions_added`
/ `evidence_added` — all `0` on an unchanged repo — so a re-run is transparently a no-op.

**Pruning deleted/renamed files (`--prune`, opt-in).** By default `build` is purely additive, so
edges for files you delete or rename linger. `wm build --prune` soft-invalidates
(`invalidated_at` + `stale`, **never** hard-deletes) build-origin edges whose anchored file no
longer exists on disk. It is deliberately conservative: it prunes an edge **only if the edge's
evidence is exclusively build-origin** — anything the agent has observed or validated (any
non-`build` evidence) is protected and left live, even if its file vanished. It checks the
filesystem, not the scan set, so a file merely skipped by `--max-files` or an ignore rule is
never pruned. The build result reports `pruned_stale_edges`.

## (a) Marker lines — lowest friction

Write these at the **start of a line** in your (assistant) turn; the Stop hook harvests them.

```
WM-OBSERVE: <subject> <predicate> <object> [@ <file:line>]
WM-VALIDATED: <subject> <predicate> <object> by <kind>:<ref>     # kind ∈ test|ci|doc|human
WM-REFUTES: <subject> <predicate> <object> by <kind>:<ref>
WM-MAPS: <symbol> -> <referent>
WM-CONSTRAINT: <name> | <kind> | <predicate> | <params-json> | <message> [| severity]
WM-CONTRADICTS: <free-text note>
```

Examples:

```
WM-OBSERVE: hash_pw uses bcrypt @ auth/hash.py:14
WM-VALIDATED: hash_pw uses bcrypt by test:tests/test_auth.py::test_hash
WM-MAPS: billing/refund.py -> stripe/refunds-api
WM-CONSTRAINT: no-weak-hash | forbids | uses | {"patterns":["md5","sha1"]} | {subject} uses weak hash {matched} | violation
```

`WM-VALIDATED` with a non-oracle kind is ignored — only `test|ci|doc|human` can raise
normative confidence.

## (b) `wm` CLI — precise / scriptable

```
wm build [path] [--max-files N] [--prune]             # repo-wide seed: files + structural edges (observation-only)
wm observe <subj> <pred> <obj> [--evidence file:line] [--conf 0.7]
wm constraint <name> <kind> "<message>" --predicate <p> --params '<json>' [--severity ...]
wm validate "<subj>,<pred>,<obj>" --by test:<id>|ci:<run>|doc:<path>|human   # raises normative
wm refute   "<subj>,<pred>,<obj>" --by ...                                    # → contradicted
wm map      <symbol> --to <referent>
wm contradictions [--open] [--touching <path>]        # list + proposed fixes
wm resolve  <id> --as retract|supersede|fixed_code|defer
wm query    --touching <path|symbol>                  # what the pre-call hook shows
wm precall  <path...>                                 # markdown pre-call summary
wm stats | wm consolidate | wm digest | wm export
```

(`wm` = `python3 wm.py`, or `python3 world_model.py`. DB path from `--db`, `$WM_DB`, or the
default `.world-model/model.db`.)

## The trust boundary (load-bearing)

Only the **trusted channel** feeds facts: user text, assistant text, and the tool *inputs the
agent chose*. **`tool_result` / `tool_use` content is never harvested** — a file's contents
or a command's output cannot smuggle a `WM-...` marker or forge evidence (a regression test
asserts an injected marker in a `tool_result` is rejected). Markers must start the line, so
echoed text mid-sentence cannot inject one. Evidence kinds are a closed set; an unknown kind
is rejected at the API. This mirrors the two-channel rule in `context-hygiene-kit`.
