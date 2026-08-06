# The design baseline — turning heuristics into facts

You cannot review architecture from a diff alone. A diff shows an import being added; it
cannot tell you whether that import violates a rule the team decided on eighteen months ago,
because that rule is not written down anywhere the tool can read.

`design-rules.json` is where you write it down. With it, "a new import crosses a layer"
stops being a heuristic question and becomes a checkable fact.

## Shape

```json
{
  "layers": {
    "core":  ["core/", "internal/model/"],
    "web":   ["web/", "http/"],
    "infra": ["infra/", "adapters/"]
  },
  "forbidden_edges": [["core", "web"], ["core", "infra"]],
  "allowed_external": ["flask", "sqlalchemy", "pydantic"],
  "budgets": {
    "max_loop_depth": 1,
    "max_params": 5,
    "max_function_rows": 60,
    "min_duplicate_rows": 6
  },
  "invariants": [
    "core/ is framework-free and never imports web/ or infra/",
    "every handler is behind an authorisation decorator"
  ]
}
```

| Key | Effect |
|---|---|
| `layers` | Names a set of path prefixes. Longest matching prefix wins, so `internal/model/` can belong to a different layer than `internal/`. |
| `forbidden_edges` | `[from, to]` pairs. An added import crossing one becomes `dep.layering` at **high**. Any other cross-layer import becomes `dep.new-edge` at **low**. |
| `allowed_external` | The third-party roots the project has already accepted. Anything else added becomes `dep.new-external` at **high** instead of medium. Omit the key (or leave it empty) to keep every new dependency at medium. |
| `budgets.max_loop_depth` | The deepest loop nesting a change may introduce without owing an explanation. Default `1`, so any two-level nest gets asked about. Raise it where nested iteration is genuinely routine. |
| `budgets.max_params` | Parameters on an added function before `fit.wide-signature` asks whether it is doing one job. Default `5`. |
| `budgets.max_function_rows` | Rows in an added function body before `fit.long-function` asks what it does. Default `60`. Raise it for languages or domains where long straight-line functions are normal. |
| `budgets.min_duplicate_rows` | Identical rows repeated before `fit.duplicate-block` calls it copy-paste. Default `6`. Lower it to catch smaller clones, at the cost of firing on coincidence. |
| `invariants` | Free text for the reviewer, carried into the review's context. Not machine-checked — it is there so a human rule does not stay tribal knowledge. |

Every key is optional. With no file at all, the tool says so at `prepare` time and falls
back to heuristics only.

## Where to put it

Repository root, committed, next to the code it describes:

```bash
python3 assets/review.py prepare --rev HEAD~1..HEAD --out .review --rules design-rules.json
```

`prepare` reads it once and bakes the resulting signals into `signals.json`, so later
commands need no flags. `assets/design-rules.example.json` is a filled-in starting point.

## Authoring one honestly

Write down the rules the team **already believes**, not the rules you wish it followed. A
baseline that flags every existing file on day one gets deleted in a week.

A workable sequence:

1. Run `prepare` with no baseline over a handful of recent merges and read `dep.new-edge`
   and `dep.new-external`. That is the layering the repo actually has.
2. Write `layers` to describe it.
3. Add only the `forbidden_edges` the team would genuinely reject in review today.
4. Seed `allowed_external` from the current dependency manifest.
5. Add `invariants` as plain sentences. They cost nothing and they are the part that
   survives the people who wrote them.

Then keep it current: when a review concludes "that layering rule is wrong now", the fix is
a change to `design-rules.json` in the same pull request. A stale baseline is worse than no
baseline, because it is trusted.

## What it deliberately does not do

It is not an architecture fitness function, it does not run in CI, and it does not fail
builds. Its only job is to raise the severity of the two or three questions a reviewer would
otherwise have to remember unaided. If you want enforcement, the sibling `verifier-installer`
skill wires checks into the actual gate.

For pinning a *narrative* understanding of a subsystem — the design explainer a reviewer
needs before they can judge a change at all — the sibling `feynman-walkthrough` skill
persists one and tells you when the source has moved on.
