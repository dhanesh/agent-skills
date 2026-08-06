# CLI reference — `assets/review.py`

Stdlib-only python3. No API keys, no network, no model calls. Every command is idempotent
and safe to re-run.

```
python3 assets/review.py <command> [flags]
```

`--into DIR` names the work directory and defaults to `.review` on every command.

## `open`

Index a diff, detect relocations, extract signals, and lay out the work directory.

| Flag | Default | Meaning |
|---|---|---|
| `--diff PATH` | — | Read the diff from a file. `-` reads stdin. |
| `--rev RANGE` | `HEAD~1..HEAD` | Used when `--diff` is absent; passed to `git diff`. Multi-token ranges are fine (`--rev "main...HEAD"`). |
| `--repo DIR` | `.` | Repository root for `git diff`, and the tree used by the one check that cannot be answered from the diff: whether a path cited in an added comment or document actually exists. |
| `--into DIR` | `.review` | Work directory to create. |
| `--rules PATH` | `design-rules.json` | Design baseline ([design-baseline.md](design-baseline.md)). A missing file is reported on stdout, not an error. |
| `--intent TEXT` | — | What the change was asked to do, in the requester's words. Carried into `REVIEW.md` so scope can be checked against it — silent scope reduction leaves no trace in a diff, so without this it is unreviewable. |

Writes:

| File | Contents |
|---|---|
| `source.diff` | The immutable input. Every coordinate addresses this. |
| `indexed.diff` | The same text with the coordinate column. **This is what you read.** |
| `relocations.json` | Detected block moves as `{origin, destination}` line ranges. |
| `signals.json` | Extracted review signals. |
| `meta.json` | Label, baseline path, statistics, counts. |
| `plan.json` | An empty plan — created only when absent, so re-running never discards work. |

Exit `0` on success; `2` on a bad invocation or an empty diff.

## `state`

Typed progress for a self-prompting loop. Full contract in
[loop-integration.md](loop-integration.md).

| Flag | Default | Meaning |
|---|---|---|
| `--json` | off | Machine-readable. Without it, a four-line human summary. |

Always exits `0`, including when the work directory does not exist — a loop must be able to
ask where it is without handling an exception.

## `draft`

Compile a plan and print the condensed diff, then density figures and every rejection.

| Flag | Default |
|---|---|
| `--plan PATH` | `<into>/plan.json` |

Exit `0` when the plan compiles, `1` when it is rejected. Output beyond 24 KB is truncated
with an explicit marker.

## `commit`

The same compile, but writes `<into>/condensed.diff` and refreshes `meta.json`. Refuses to
write while any rejection stands (exit `1`), leaving a previously accepted `condensed.diff`
untouched.

| Flag | Default |
|---|---|
| `--plan PATH` | `<into>/plan.json` |

## `signals`

| Flag | Default | Meaning |
|---|---|---|
| `--severity {high,medium,low}` | all | A floor, not an exact match: `--severity medium` shows high **and** medium. |
| `--json` | off | Machine-readable output. |

## `template`

Write the `REVIEW.md` skeleton: the review dimensions, the verdict, and one disposition line
per signal.

| Flag | Default | Meaning |
|---|---|---|
| `--out PATH` | `<into>/REVIEW.md` | |
| `--force` | off | Overwrite an existing file. Without it an existing file is an error, so a written review is never clobbered by a re-run. |

## `grade`

Check a finished review for completeness. Prints `CHECK:` lines and a `REPORT_RESULT:`
verdict; exits `1` on any gap.

| Flag | Default |
|---|---|
| `--report PATH` | `<into>/REVIEW.md` |

Checks: every required section present, non-empty, and free of template placeholders; one
unambiguous verdict; a summary paragraph of at least 25 words; a **Confidence and basis**
section carrying both a `Verified:` and an `Unverified:` line; and every **high-severity**
signal resolved as `**addressed**:` or `**dismissed**:` with at least 20 characters of
reason.

The confidence check is the one that makes the output something to act on rather than merely
complete. A review that never says where its evidence stops cannot be acted on without
re-deriving it, however well it reads.

## `audit`

Second-opinion a review someone else already wrote. Details in
[second-opinion.md](second-opinion.md).

| Flag | Default | Meaning |
|---|---|---|
| `--prior PATH` | required | The existing review to audit. |
| `--json` | off | Machine-readable output. |

Exit `0` when nothing was missed, `1` when there are blind spots.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success — or, for `state`, "here is where you are". |
| `1` | The artifact did not hold up: a plan that does not compile, a review with gaps, a prior review with blind spots. |
| `2` | Bad invocation: missing work directory, unreadable JSON, empty diff, `git diff` failure. |
