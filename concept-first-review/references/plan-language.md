# The plan language

You do not write the condensed diff. You write an ordered list of edits against the
original, and `assets/condenser.py` applies them.

That asymmetry is the entire safety argument. The language has three verbs — `drop`,
`collapse`, `trim` — and none of them can introduce a character that was not already
there. Whatever comes out is derivable from what went in, so the condensed diff can be
trusted the way a compiler's output is trusted rather than the way a summary is trusted.

## Coordinates

`review.py open` writes `indexed.diff` into the work directory. Every row carries its
physical line number in a fixed-width left column:

```
  13 │ -    resp = Response()
  14 │ -    resp.user_id = user.id
```

Those numbers address the **original** diff and never shift, no matter how many edits you
apply or how many times you redraft.

## Shape

```json
{
  "edits": [
    {"op": "drop",     "lines": "14-17"},
    {"op": "collapse", "lines": "18-20"},
    {"op": "trim",     "line": 26,
                       "from": "'no rows for %s' % user.id",
                       "to":   "..."}
  ],
  "headline": "Order listing batches item reads; identity helpers relocate into core."
}
```

`edits` is required — pass `[]` when the change genuinely has no noise. Order inside the
array does not matter; every edit addresses the original independently. Each submission is
a complete plan, never a patch on the previous draft.

### `drop`

`lines` is `"N"` or `"N-M"`, inclusive. The rows disappear.

This is the workhorse. Structure is handled for you: headers survive when their content
survives and vanish when it does not, so you never remove metadata by hand and cannot
strand it.

### `collapse`

`lines` is a range of **at least two adjacent rows**, all inside one hunk, all carrying the
same diff marker. They become a single row: the marker, the block's own indentation, and
`...`. You choose the range; the placeholder is not yours to write.

Reach for `collapse` when the existence and shape of what is hidden still helps the reader,
and `drop` when it does not.

### `trim`

Shortens **one** row. `line` is its coordinate. `from` is an exact substring of the source
**after** the leading `+`, `-`, or context space, and must appear there exactly once. `to`
must reproduce `from` with every cut span written as `...` or `…`.

| `from` | `to` | |
|---|---|---|
| `resp.SSHKeyID = rd.sshKeyID` | `resp.SSHKeyID = rd...` | accepted — tail cut, nothing added |
| `t.Errorf("route = %d, want %d", a, b)` | `t.Errorf(…)` | accepted — arguments cut |
| `return rows` | `return cached_rows` | rejected — invented characters |
| `a = bcd` | `a = bd` | rejected — a cut with no ellipsis marking it |
| `a = b` | `a = b  # batched` | rejected — invented comment |

Never put the coordinate column or the diff marker in `from`.

## Verify, then repair

```bash
python3 assets/review.py draft --into .review
```

`draft` prints the condensed diff, then the density figures and every rejection, and exits
non-zero when the plan does not compile. Read the rejection, fix the coordinates, run it
again. `commit` refuses to write until `draft` is clean, so a plan that misrepresents the
change never reaches a reader.

Density is a prompt to look again, not a target to hit. When it reports that most rows
survived, make one more pass over repetition and test setup — then stop, and keep whatever
is distinct or uncertain.

## Rejections, and what each one wants

| Message contains | What to change |
|---|---|
| `not a trim of` | `to` invented or silently cut characters. Rewrite it as `from` with `...` in the gaps. |
| `appears N times` | Widen `from` until it is unique on that row. |
| `is not on line N` | `from` does not match the source after the diff marker. |
| `edited differently` | A detected relocation. Give both ends the same treatment. |
| `buries the definition of` | Your collapse hid something a surviving row still uses. Collapse its interior instead. |
| `consumes decorator` / `block header` | Keep the header visible; collapse only the body. |
| `triple-quote parity` | A multiline string would be left unterminated. Keep both delimiters. |
| `spans both dependency rows` | Dependency rows are already gone. Collapse only the executable range. |
| `at least two adjacent rows` | A one-row collapse is a `drop` or a `trim`. |
| `stay inside one hunk` | The range escaped its hunk and swallowed a header. |
| `come out anyway` (a note, not a rejection) | Coordinates aimed at dependency rows. Harmless, but wasted. |

## Worked example

```
  13 │ -    resp = Response()
  14 │ -    resp.user_id = user.id
  15 │ -    resp.box_id = user.box_id
  16 │ -    resp.tenant = user.tenant
  17 │ -    resp.created_at = user.created_at
  18 │ -    normalize_tenant_prefix(user.tenant, resp.box_id)
```

Row 13 names the operation, so it stays. Rows 14–17 are four instances of one pattern whose
shape is worth showing and whose contents are not, so they collapse. If row 18 is one end
of a detected relocation, its counterpart gets the identical treatment.

```json
{"edits": [{"op": "collapse", "lines": "14-17"}]}
```

reads back as:

```
-    resp = Response()
-    ...
```
