# idea-validation: brief format, statuses and payload

This file is the reference for `ideas.py`. The design is in the repository at
`docs/superpowers/specs/2026-10-11-idea-validation-design.md`.

## Commands

Every command takes `--root R` before the command name. `--today YYYY-MM-DD` sets "today" for
tests only.

| Command | Effect |
|---|---|
| `new <slug> --title TEXT` | Writes `ideas/<slug>.md` from the template. Refuses a slug that exists. |
| `lint <brief>` | Prints `LINT: ok`, or one `PROBLEM:` line per fault. |
| `register <brief> --by NAME` | Lints the brief, then snapshots it to `.skill-contract/ideas/<slug>/registered/<sha256>.md`. The brief has to be `ideas/<slug>.md` under the root. |
| `record <slug> <A-id> --value N --kind experiment\|desk --source TEXT --observed YYYY-MM-DD --by NAME [--evidence-file F]` | Appends one result, bound to the latest registration. |
| `status <slug>` | Prints statuses, results, the verdict and notes. Writes nothing. |
| `decide <slug> --verdict go\|kill\|pivot --by NAME --reason TEXT` | Records the human's decision. |
| `handoff <slug> --by NAME` | Writes the `idea-brief/v1` envelope after a `go`. |
| `list` | One `IDEA:` line per brief. Writes nothing. |

Exit codes: 0 ok; 2 refused input, a bad brief, an unignored state directory or a refused
handoff; 3 the lock is held.

## Brief grammar

- The title line is `# Idea: <title>`.
- These `##` sections are required. They match without regard to case and can be in any order:
  `Problem`, `Target user`, `Value proposition`, `Riskiest assumptions`, `Experiments`,
  `Out of scope`. A section that appears twice is a fault.
- Each assumption in `## Riskiest assumptions` is one line:

  ```
  - A<n> [critical|normal] [problem|demand|solution|channel|pricing|feasibility]: <statement> (test: <how>; metric: <what is counted>; pass: <op> <number>; kill: <op> <number>; by: YYYY-MM-DD)
  ```

  `<op>` is `>=`, `>`, `<=` or `<`. The number is a finite decimal.
- A line under `## Riskiest assumptions` that looks like an assumption (`A<n> [`) but is not
  a valid `- A<n> …` line is a fault. The tool never drops it silently. So `+ A4 [critical] …`,
  `1. A4 [critical] …` and a bare `A4 [critical] …` each give a `PROBLEM:` line.
- Each experiment in `## Experiments` is one bullet: `- `, `* `, `+ `, `1. ` or `1) `. It names the assumption ids it tests
  (`A1`, `A2`). Start it with `[outward]` when it publishes, sends, spends or contacts a
  person. A human runs every outward experiment.

## Lint rules

| Rule | Fault |
|---|---|
| Title | no `# Idea: <title>` line |
| Sections | a required section is missing or appears twice |
| Ids | the ids do not run A1..An in order, with no gaps or repeats |
| Critical | no assumption is `[critical]` |
| Fields | a severity or category is not in its list; the statement or a field is empty; a field appears twice |
| Unparsed lines | a line under `## Riskiest assumptions` has `A<n> [` but is not a valid `- A<n> …` line |
| Thresholds | `pass` or `kill` is not `<op> <number>`; they point the same way; they overlap (some value meets both) |
| Deadline | `by` is not a real date |
| Experiments | no experiment bullet; a bullet names no assumption id; a bullet names an unknown id |

A `NOTE:` (not a fault) says when no critical assumption is `[problem]` or `[demand]`, because
most ideas fail on demand. Another `NOTE:` names each `[outward]` experiment.

Example: `pass: >= 30` with `kill: < 10` is valid. `pass: >= 30` with `kill: < 40` is not,
because 35 meets both.

## Results

A result is `{assumption, value, kind, source, observed, by, evidence, truncated,
registration, recorded_at}`, one JSON line in `.skill-contract/ideas/<slug>/results.jsonl`.

- `kind: experiment` needs a `--by` that does not start with `agent:`. The tool refuses
  `agent:…` for an experiment.
- `kind: desk` is research. Its `--by` is `agent:<name>`, or a human's name when the human did
  the research.
- `--observed` has to be a real date, no later than today.
- `--evidence-file` text is cut at 4096 bytes, on a UTF-8 character boundary. The result then
  has `truncated: true`, and the `RESULT:` line says so.
- Every result is bound to the latest registration (`registration` is its sha256).

## Staleness

A result counts only while its assumption's `test`, `metric`, `pass`, `kill` and `by` fields
are the same in the latest registration as in the registration it was recorded under. A change
to the statement alone, or to the severity, does not make a result stale. `status` prints a
stale result with `stale (not counted)`.

## Statuses

The status comes from the latest counted result by `observed` date (ties by record order).

| Status | When |
|---|---|
| `open` | no counted result, and the deadline is today or later |
| `pass` | the value meets `pass`, it was observed on or before the deadline, and for a critical assumption the result is `experiment` kind |
| `needs-experiment` | a critical assumption whose latest passing result is `desk` |
| `fail` | the value meets `kill`, at any date and of any kind |
| `inconclusive` | the value is between `pass` and `kill` |
| `expired` | the deadline has passed and the status is not `pass` or `fail`. A passing result observed after the deadline lands here, because a deadline is part of the test |

**Mixed evidence.** When the latest result is `desk` and does not meet `kill`, the latest
result that is an `experiment` result or a `desk` kill decides. When there is no such result,
the desk result decides. So desk research can kill at any time, and a later desk pass or
in-between desk value never undoes a desk kill, rescues an experiment kill or undoes an
experiment pass. A later `experiment` result does decide over an earlier desk kill.

## Verdict

1. `KILL` if any critical assumption is `fail`.
2. `GO` if every critical assumption is `pass`.
3. `STALLED` if any critical assumption is `expired`.
4. `PENDING` otherwise.

## Dropped critical assumptions

An assumption id is **dropped critical** when it was `critical` in some earlier registration
and, in the latest registration, one of these is true. Each has a `reason`:

- `demoted`: the id is `normal`.
- `deleted`: the id is absent.
- `replaced`: the id is `critical`, but its statement and every one of `test`, `metric`,
  `pass`, `kill` and `by` differ from the latest earlier registration where it was critical.
  An example is a failed critical A1 that swaps places with a normal A3.

The tool does not refuse this. It prints:

```
NOTE: dropped-critical A2, A3: critical in an earlier registration, and now demoted, deleted or replaced; the handoff lists them
NOTE: dropped-critical A2 demoted, results under a critical registration: yes, statement `…`
```

in `status`, `decide` and `handoff`, and lists each one in the payload's `dropped_critical`.
The flag does not change the verdict. Tracking is by id. If you delete A2 and renumber A3 to
A2, the tool sees an edit of A2 when one field stays the same, and a replacement when all of
them change. A replacement is compared only with the latest earlier critical version, so a
second registration with a small edit clears `replaced`. Promoting the id back to critical
clears `demoted`.

## Decisions

`decide` records `{verdict, by, reason, computed, override, at, registration}`. `override` is
true for `go` against any verdict but `GO`, and for `kill` against `GO`. `pivot` closes the
brief: `record`, `register` and `decide` then refuse.

## Handoff

`handoff` refuses (exit 2, with a `NEXT:` line) when:

- the latest decision is not `go`;
- the brief changed since its last registration;
- the `go` decision was made before the latest registration;
- the computed verdict changed since the `go` decision;
- the registered snapshot is missing or does not match its sha256.

It writes the envelope to `.skill-contract/ideas/envelopes/<envelope id>.json`. The one subject
is the registered snapshot.

## Payload (`idea-brief/v1`)

The schema is `assets/schemas/idea-brief.v1.json`.

```
{slug, title, problem, target_user, value_proposition, out_of_scope,
 computed_verdict, decision: {verdict, by, reason, override, at},
 assumptions: [{id, critical, category, statement, test, metric, pass, kill, by, status,
                results: [{value, kind, source, observed, by, evidence, truncated, claim}]}],
 open_risks: [ids of assumptions that are not pass],
 dropped_critical: [{id, statement, last_critical_registration, had_results, reason}]}
```

- `results` holds only counted (not stale) results. Each has `claim: "claimed"`.
- `dropped_critical` is always present. It is empty when none is dropped.
  `last_critical_registration` is the sha256 of the latest registration where the id was
  critical. `had_results` is true when a result was recorded for the id under a registration
  where it was critical. `reason` is `demoted`, `deleted` or `replaced`.
- spec-first-planning refuses a payload that contradicts itself: `decision.override` must equal
  "`computed_verdict` is not `GO`", `open_risks` must be exactly the ids whose status is not
  `pass`, and `computed_verdict` must follow from the critical statuses by the verdict rules.
- `source` and `evidence` are untrusted. The brief text can also come from desk research.

## Machine lines

| Line | Meaning |
|---|---|
| `LINT: ok` | the brief is clean |
| `PROBLEM: <text>` | one lint fault |
| `NOTE: <text>` | advice or a flag (demand, outward, changed brief, override, open risks, dropped critical) |
| `IDEA: <slug> …` | the idea, or one `list` row |
| `REGISTERED: <sha12> …` | a registration |
| `RESULT: <id> <value> <kind> <observed> by <name> source <text> …` | one result |
| `ASSUMPTION: <id> <status> <latest value>` | one assumption |
| `EXPERIMENT: <n> outward\|inward <ids>` | one experiment |
| `VERDICT: GO\|KILL\|STALLED\|PENDING` | the computed verdict |
| `DECISION: <verdict> by <name> computed <verdict> [override] reason <text>` | the latest decision |
| `ENVELOPE: <path>` | the handoff envelope |
| `NEXT: <command>` | the next step |
| `STOP: <text>` | a refusal; nothing changed |

## Storage

- The brief is `ideas/<slug>.md`. The human chooses whether to commit it.
- The snapshots, `state.json`, `results.jsonl`, `log.jsonl` and the envelopes are under
  `.skill-contract/ideas/`. The tool writes `.skill-contract/ideas/.gitignore` with `*` before
  its first write and stops with exit 2 when it cannot prove the directory is ignored. A
  `.gitignore` there with a `!` line (a negation that un-ignores files) also stops the tool
  with exit 2. The tool does not overwrite it.
- The slugs `envelopes` and `specs` are reserved, because they are directory names under
  `.skill-contract/ideas/`. Every command that takes a slug refuses them.
- State files are written by atomic replace. The results and the log are append-only. One lock
  (`.skill-contract/ideas/.lock`) covers every writing command.
