# Capture: markers, the `wm` CLI, and the trust boundary

The update path is **hybrid**: deterministic hooks capture the *skeleton* (which files/symbols
were touched, at low confidence); the agent enriches the graph *explicitly*. A model never
guesses facts inside a hook — that is how you get invented facts.

To avoid a cold start, `wm build [path]` seeds the whole repo in one deterministic pass —
registering every source file plus structural edges (Python imports; file references from
shell/config/docs). Like the hooks, it is **observation only**: `observed_conf` rises but
`normative_conf` stays 0 and status stays `unverified` (a bulk scan is a sighting, not a
correctness judgement), and an edge is added only when both endpoints are real files it found.
Idempotent, so re-running just refreshes. Structural referents (external services/APIs) are
still added by the agent via `wm map`.

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
wm build [path] [--max-files N]                       # repo-wide seed: files + structural edges (observation-only)
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
