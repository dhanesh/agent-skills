# The feature map

`features/README.md` is the index, in sweep order: one line per feature,
`- [<id>](<id>.md) — <one line from the user's point of view>`. Every sibling `*.md` is
listed exactly once and every listed link exists; `forge lint` fails a missing, extra,
duplicate or dead entry.

## One file per feature

```markdown
# <id>: <title a user would recognise>

- id: <id>                      # lowercase kebab-case, equals the file name
- proven: no | <40-hex commit>  # set only after the recipe was driven live on that commit;
                                #   a commit cannot name itself, so update it in a follow-up
                                #   commit that touches only features/
- anchors: <path>[:<symbol>], … # source paths relative to the app root
- constraints: <manifold>:<id>, …   # optional: anchored Manifold constraints it proves

## What it is
## How to reach it
## Drive it
## Proof
## Gotchas
```

Each section answers, from the user's point of view:

1. **What it is.** The feature as a user would describe it.
2. **How to reach it.** Every user entry point: the route, the menu, the command. A proof
   that drives one convenient entry point is incomplete when the map lists others.
3. **Drive it.** The exact harness commands, the expected output and the exit code. Lint
   fails a section with no fenced command or no stated exit code or expected output.
4. **Proof.** The observable result that shows it worked, including the side effect.
5. **Gotchas.** What misleads a driver: a slow first load, a per-instance data store, a
   flag that only exists in dev.

## IDs and anchors

IDs are stable: renaming a feature keeps its id, so evidence recorded under the old id
still names it. `doctor` and `findings` are reserved (they are directories under
`.verify/<instance>/`).

Anchors are how drift is found and how factory-conductor maps a diff to the features it
touches: a changed file equal to, or under, an anchor path makes that feature *touched*,
and a touched feature needs evidence. `path:symbol` also requires the symbol to appear in
the file. Lint fails every anchor that no longer resolves, and says so louder when the
feature still claims `proven`: a proof of code that no longer exists proves nothing.

## Monorepos

One verify skill per runnable app, each with its own map. A feature spanning two apps
gets an entry in each, each proving its own half.
