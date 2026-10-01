# The Manifold join

[Manifold](https://github.com/dhanesh/manifold) elicits constraints (business, technical,
UX, security, operational), finds the pairs that cannot both hold, and anchors the
outcome in required truths. It says what has to be true, but not how to see it at runtime.
The feature map says how to see behaviour at runtime, but not why that behaviour matters.
The join names, for every anchored constraint, the observable proof that it holds.

## What counts as anchored

`forge coverage` reads every `.manifold/<feature>.json` (not `*.verify.json`) whose `phase`
is `ANCHORED`, `GENERATED` or `VERIFIED`. Its anchored constraints are the ones a
`anchors.required_truths[].maps_to` names. An older manifold at those phases whose truths
map to nothing anchors all of its constraints. Constraint titles come from the paired
`<feature>.md` (`#### B1: Title`). IDs are qualified as `<feature>:<id>` because every
manifold numbers from B1. A feature file may name a bare id when only one manifold has
it.

**Legacy YAML manifolds** (`<feature>.yaml`, from before Manifold's JSON+Markdown format)
are read with a narrow line parser, since the stdlib has no YAML: top-level `feature:` and
`phase:`, each `- id: B1` under `constraints:` with its `type:` and `statement:`, and every id
named by `maps_to`, `maps_to_constraint` or `satisfies_constraints` in the file or its
`<feature>.anchor.yaml`. Each such file prints `DEGRADED: LEGACY <file>` so the report says
how it was read; a YAML file with a JSON twin is skipped; `manifold migrate` converts it.

Where `.manifold/` is looked for: the app root, then the repository root. `--manifold`
overrides both.

## Output

```text
COVERED: payments:B1 -> notes-create
UNCOVERED: payments:S2 "No card number in logs"
COVERAGE: 1/2 anchored constraints have an observable proof
```

`--json` prints the same as one object. `--require-total` exits 3 while anything is
uncovered, for a caller that wants it as a gate.

Some constraints have no runtime face (a licence choice, a cost ceiling). Leave them
uncovered and say why in the report: the list is a backlog, and an honest backlog beats a
proof invented to empty it.

## Degrading

Each file that cannot be used prints `DEGRADED: <code> <file>: <why>` and is skipped:
`E_IO` (unreadable), `E_PARSE` (not JSON), `E_SCHEMA` (an unsupported
`schema_version`), `E_VALIDATE` (no constraints object), `E_LINK` (a truth that maps to
a constraint that does not exist; the rest of that file is still used). With no usable
manifold the report says `COVERAGE: source-derived` and the forge carries on from source.

## Seeding

`forge seed` writes one stub per uncovered constraint (`- proven: no`, the constraint in
`- constraints:`, every section `FILL:`) and adds it to the index. Lint refuses the stubs
until each is filled from source and driven, so a seeded map cannot pass as a real one.

## Planning side

spec-first-planning carries the same ids forward: a task's `[feature: …]` and
`[proof: …]` hints name the feature entries and the observable predicate, and where
Manifold has converged, its anchored constraints supply those predicates.
