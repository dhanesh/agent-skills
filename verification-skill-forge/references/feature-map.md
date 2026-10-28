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
- trusted: <entry>, …           # optional: what the contract takes as given

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

## The contract (optional)

A feature can state a contract: the clauses a proof must show. Put it in a
`## Contract` section, one bullet per clause:

```markdown
## Contract

- C1 [pre]: the title is a non-empty string
- C2 [post]: the response is 201 with the stored note's id
- C3 [inv]: notes.jsonl holds one row per stored note
```

- The id is `C` and a number: `C1`, `C2`, `C3`.
- The kind is one of `pre` (holds before the action), `post` (holds after it),
  `inv` (holds before and after) or `trans` (relates the state before to the state after).
- The text after the colon is the statement.

The optional `- trusted:` header line lists, comma-separated, what the contract takes as
given and does not prove. Like the other header lines, it comes before the first `##`.

`forge contract <feature file>` prints the contract as one JSON object:

```json
{"clauses": [{"id": "C1", "kind": "pre", "text": "the title is a non-empty string"}],
 "sha256": "<64 hex>", "trusted": ["the JSON parser"]}
```

- `clauses` lists every clause bullet in order. It is empty when there is no section.
- `trusted` lists the `- trusted:` entries. It is empty when there is no such line.
- `sha256` is the SHA-256 of the section text, in hex. It is `null` when there is no
  section.

The section text is fixed exactly, so two commits can be compared by hash. It starts at
the line `## Contract` and ends before the next line that starts with `## `, or at the end
of the file. Each line is right-stripped. The lines are joined with a single newline (no
newline after the last line) and encoded as UTF-8. Blank lines inside the section,
including the ones before the next heading, are part of the text. A change to any clause, or to any other
text in the section, changes the hash. Trailing spaces do not.

A feature file without a `## Contract` section lints as before.

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
