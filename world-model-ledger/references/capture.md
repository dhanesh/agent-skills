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
  agnostic), C/C++ (`#include "…"`), PHP (`require`/`include`), Dart (relative `import`), Go
  (imports of the module's own packages resolve via the root `go.mod` module path to the `.go`
  files that comprise the package), plus shell `source`, Make `include`, Dockerfile `COPY`,
  C#/.NET `<ProjectReference>`, and generic path mentions in docs/config.
- **External dependency edges** (file → `depends_on` → *referent*): a dependency literally
  declared in the source — a package (JS/TS `import 'react'`, Ruby `gem`, Rust `use <crate>`,
  Go `import "github.com/…"`, Python third-party `import`, Java `import org.springframework.…`,
  C# `using Newtonsoft.Json`, PHP `use Symfony\…`, C/C++ library headers `#include <boost/…>`,
  Kotlin/Scala `import`, Swift `import <Module>`, Dart `package:`, Elixir `use`/`alias`),
  a container image (Dockerfile `FROM`, compose/k8s `image:`), a CI action (GitHub Actions
  `uses:`), or a Terraform module `source`. Language stdlibs/system modules are skipped
  (`fmt`, `std`, `os`, `java.*`/`javax.*`, `System.*`, PHP `App\`, `kotlin.*`, `scala.*`,
  `SwiftUI`/`Foundation`, `dart:*`, bare `<stdio.h>`).
- **Dependency manifests** → **precise** `depends_on` edges from the declared list (more
  reliable than scanning imports): `package.json`, `requirements.txt`, `pyproject.toml`,
  `Pipfile`, `go.mod`, `Cargo.toml`, `pom.xml`, `build.gradle(.kts)`, `composer.json`,
  `Chart.yaml`, `.gitlab-ci.yml`, and `.csproj`/`packages.config` (`<PackageReference>` →
  precise NuGet artifacts). (Source-scanned Java/PHP/Kotlin/Scala external names are *coarse* —
  a package/vendor prefix — so the manifests are the canonical dependency source; `.csproj` is
  the precise source for C#.)

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

## (a0) Automatic capture — the universal observer (zero-config)

The primary capture path needs **no markers and no env vars**. A single `PostToolUse` hook
(`posttooluse-observe.sh`, matcher `*`) fires after *every* tool call and records what that call
reveals, from the tool **input** only (trusted, agent-authored — never the output):

- **Any tool naming a repo file** (`Read`, `Grep`, `Glob`, `Edit`, `Write`, notebook, or any
  MCP tool with a `file_path`/`path`) → that file is registered as an **entity** (a node
  sighting: it is part of the world). Observation only — no invented edge.
- **`Bash`** → the command is parsed into runtime `executes`/`reads` **edges** (see below).
- **`WebFetch`/`WebSearch` URLs** → an external **referent** the agent consulted.

On top of that, **`SessionStart` auto-bootstraps** the model on first run: in a git repo with no
`.world-model/` yet, it creates and seeds it (`build .`) with zero manual steps. So the model
starts populated and keeps growing from the agent's ordinary activity — markers (below) are an
*optional* precision layer, never required.

### The execution sub-channel — *"what executes what"*

Edits are only half of behaviour; execution is the other half. The Bash branch parses each
command — **structurally, never its output** — into runtime edges, with no marker and no opt-in
flag:

- `bash reaper.sh`, `python3 build.py`, `uv run x.py` → `<runner> --executes--> <repo script>`
- `kubectl apply -f ns.yaml`, `docker build -f Dockerfile` → `<tool> --reads--> <repo file>`

These carry **`runtime` evidence — an observation kind** — so `observed_conf` rises while
`normative_conf` stays 0 (watching something run proves it *happens*, not that it is *correct*).
Commands that name no repo file (`ls`, `kubectl get pods`) produce nothing — precision over recall.

The **one** way execution touches `normative_conf`: a recognised **verifier** command's exit
status. When the command matches the verifier pattern (`$WM_VERIFIER_RE`, default matches
`test`/`spec`/`check`/`lint`/`pytest`/`shellcheck`/… — build-tool-agnostic, so `make test`
matches but `make build` does not), its exit code writes **`test` oracle evidence** on the code
edge it ran: `0` → `supports` (→ `validated`), non-zero → `refutes` (→ `contradicted`). So a
green test run promotes facts and a red one flags a contradiction — with zero marker discipline.
Exit code is best-effort from the hook payload; when unknown, the observation is still recorded
and only the oracle is skipped.

**Limit (honest):** a hook sees *invocation + exit status*, not intra-process calls. It captures
"the agent ran reaper.sh and it exited 0," not "reaper.sh calls kubectl internally" — that needs
static parse (`wm build`) or syscall tracing (out of scope). Invocation-level observation is
already the behavioural signal edits cannot provide.

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
wm exec     --command "<cmd>" [--exit-code N]         # observe an execution (or --from-hook, from stdin JSON)
wm stats | wm consolidate | wm digest | wm export
```

(`wm` = `python3 wm.py`, or `python3 world_model.py`. DB path from `--db`, `$WM_DB`, or the
default `.world-model/model.db`.)

## The trust boundary (load-bearing)

Only the **trusted channel** feeds facts: user text, assistant text, and the tool *inputs the
agent chose* (including the Bash **command** it ran — an action it authored, like a `file_path`).
**`tool_result` / `tool_use` content is never harvested** — a file's contents or a command's
**output** cannot smuggle a `WM-...` marker or forge evidence (a regression test asserts an
injected marker in a `tool_result` is rejected). The execution channel parses command *structure*
(argv), never output, and emits only the fixed `executes`/`reads` predicates — it cannot mint an
arbitrary edge from free text. Markers must start the line, so echoed text mid-sentence cannot
inject one. Evidence kinds are a closed set; an unknown kind is rejected at the API. This mirrors
the two-channel rule in `context-hygiene-kit`.
