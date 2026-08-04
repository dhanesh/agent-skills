# Signal catalogue

Every signal is an **observation plus a question**. None of them is a verdict, and the tool
never claims a defect. Severity ranks *how much a wrong answer would cost*, not how likely
the concern is.

Run `python3 assets/review.py signals --into .review [--severity high] [--json]`.

## What each severity means for the review

| Severity | Obligation |
|---|---|
| `high` | Must be resolved in `REVIEW.md` as `**addressed**` or `**dismissed**` with a reason. `grade` fails otherwise. |
| `medium` | Answer it if the change touches that dimension; a one-line dismissal is fine. |
| `low` | Orientation. Cite it only when it turns out to matter. |

## Dependencies and layering

| id | Fires on | Severity |
|---|---|---|
| `dep.new-external` | An added import of a module that is neither relative nor standard library. | `medium` without a baseline; `high` when a baseline lists allowed dependencies and this is not one. |
| `dep.layering` | An added import crossing an edge the baseline declares forbidden. | `high` |
| `dep.new-edge` | An added import crossing between two declared layers, in a direction that is allowed. | `low` |

*Limit:* module→path mapping is heuristic (dotted namespaces become slash paths). An
unmatched module yields no signal rather than a wrong one, so a missing layering signal is
never proof of a clean change.

## Public surface

| id | Fires on | Severity |
|---|---|---|
| `api.signature-changed` | The same public name appears on both a `-` and a `+` row. | `high` |
| `api.route-added` | A new HTTP route or handler registration. | `high` |
| `api.surface-removed` | A public declaration that only disappears. | `high` |
| `api.surface-added` | A new public declaration. | `medium` |

*Limit:* declaration-based. Go capitalised top-level declarations, non-underscore Python
`def`/`class`, `export` in JS/TS, `pub` in Rust, `public` in JVM/C#. A behaviour change
under an unchanged signature is invisible here — which is what `radius.untested-change` is
for.

## Algorithm choices

| id | Fires on | Severity |
|---|---|---|
| `algo.nested-loop` | A loop opened at a nesting depth over the baseline's `max_loop_depth` (default 1). | `high` |
| `algo.io-in-loop` | A query/fetch/request/read call inside a loop — the N+1 shape. | `high` |
| `algo.await-in-loop` | `await` inside a loop, serialising work that could be concurrent. | `medium` |
| `algo.linear-scan-in-loop` | A membership test inside a loop. Loop *headers* are excluded — iteration is not a scan. | `medium` |
| `algo.sort-in-loop` | A sort inside a loop. | `medium` |
| `algo.recursion` | A function added in this diff calling itself. | `medium` |
| `algo.regex-backtracking` | Nested quantifiers in an added regex literal. | `medium` |
| `algo.unbounded-growth` | A collection appended to inside a loop. | `low` |

*Limit, and it is a real one:* nesting is inferred from indentation **within a contiguous
run of added rows**. A loop that was already there, outside the diff, is invisible — which
cuts both ways: a change that adds one loop inside an existing one shows as depth 1. This is
why these fire as questions — "what bounds each level?" — rather than as complexity claims.

## Concurrency and state

| id | Fires on | Severity |
|---|---|---|
| `conc.spawned` | A goroutine, thread, task, or `Promise.all`. | `high` |
| `conc.lock` | A mutex, lock, or `synchronized` block. | `medium` |
| `state.global-mutable` | An unindented module-level mutable binding. | `medium` |

## Error posture

| id | Fires on | Severity |
|---|---|---|
| `err.swallowed` | A bare `except:`, an `except`/`catch` whose body does nothing, `_ = err`, `.unwrap()`. Detected across two rows, so `except Exception:` followed by `pass` is caught. | `high` |
| `err.abort` | `panic`, `os.Exit`, `sys.exit`, `process.exit`, `.expect`. | `medium` |
| `err.resilience` | Retry, backoff, timeout, or deadline wording changed. | `low` |

## Boundaries

| id | Fires on | Severity |
|---|---|---|
| `bound.exec` | `subprocess`, `os.system`, `exec.Command`, `child_process`, `eval`. | `high` |
| `bound.deserialize` | `pickle.loads`, `yaml.load`, `Marshal.load`, `ObjectInputStream`. | `high` |
| `bound.authz` | An authorisation-looking name moved. | `high` |
| `bound.network` | A new HTTP/socket client call. | `medium` |
| `bound.path` | A filesystem path constructed by joining. | `low` |

These are review prompts about trust boundaries, not a security audit. For that, run the
sibling `security-posture-audit` skill.

## Data and contracts

| id | Fires on | Severity |
|---|---|---|
| `data.migration` | A file under `migrations/` or a `.sql` file. | `high` |
| `data.contract` | `.proto`, GraphQL, Avro, Thrift, OpenAPI, or `*.schema.json`. | `high` |
| `data.ddl` | DDL keywords on an added row. | `high` |
| `cfg.changed` | A YAML/TOML/INI/env/Dockerfile/helm path. | `low` |
| `cfg.flag` | A feature flag or environment lookup. | `low` |

## Blast radius

| id | Fires on | Severity |
|---|---|---|
| `radius.untested-surface` | A public declaration changed and no test file was touched. | `high` |
| `radius.untested-change` | Source changed and no test file was touched, with no declaration change. | `medium` |
| `radius.spread` | Four or more top-level areas touched. | `medium` |

## Precision, and where it was bought

Three detectors were deliberately narrowed after they proved noisy on real changes, and the
narrowing is part of the contract:

- **I/O in a loop** ignores bare `get(`, `load(`, and `read(`. In practice those are dict
  lookups and in-memory accessors far more often than round trips, and a detector that
  fires on them teaches reviewers to ignore it.
- **Loop nesting** ignores continuation rows — a row that closes more brackets than it
  opens is the tail of an expression that began above it, so its indentation says nothing
  about nesting.
- **Code-shaped detectors skip prose files.** Without that, the word `eval (` in a design
  note reads as a call to `eval`.

## Reading a quiet report

No high signals means *these detectors found nothing*, which is a much smaller claim than
"this change is safe." The detectors are pattern matchers over added rows; they cannot see
intent, cannot resolve aliases or dynamic dispatch, and cannot see code the diff did not
touch. The review dimensions in [review-rubric.md](review-rubric.md) are the actual review.
Signals only make sure the cheap-to-find things are not the ones you miss.

The same caution applies in the other direction when auditing someone else's review:
[second-opinion.md](second-opinion.md) reports a *blind spot* — a signal never engaged —
which is evidence about coverage, not proof of a defect.
