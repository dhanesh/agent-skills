# idea-validation

idea-validation tests an idea before the factory plans or builds it. You say what has to be
true for the idea to be worth building. You also say how to find out cheaply. The agent helps
you write this down as a validation brief. A small local tool fixes the brief before any result
comes in, records the results you report, and computes a verdict from your own thresholds.
When you decide `go`, spec-first-planning turns the idea into a spec.

```bash
npx skills add dhanesh/agent-skills --skill idea-validation
```

## How it works

1. **Draft.** The agent runs `ideas.py new` and fills in the brief with you. The brief names
   the problem, the user, the value, and the 3 to 7 riskiest assumptions. Each assumption has
   a test, a metric, a pass threshold, a kill threshold and a deadline.
2. **Register.** You confirm the brief. The tool takes a snapshot of it. After this, a change
   to a threshold makes the earlier results for that assumption stale. The goalposts cannot
   move after the data is in.
3. **Test.** You run the experiments: interviews, a landing page, a price test. The agent can
   write the plan and the copy, but it never publishes, sends, pays or contacts anyone,
   because no autonomy grant covers that work. You tell the agent each result, and it records
   the result with your name.
4. **Verdict.** The tool computes `GO`, `KILL`, `STALLED` or `PENDING`. `GO` needs every
   critical assumption to pass with a result from a real experiment. The agent's own desk
   research can kill an idea, but it cannot pass a critical assumption.
5. **Decide.** You decide `go`, `kill` or `pivot`. If your decision differs from the verdict,
   the tool records an override.
6. **Hand off.** After `go`, the tool writes an `idea-brief/v1` envelope. spec-first-planning
   reads it and starts a spec. The spec shows the override and any dropped critical
   assumption at the top. All evidence stays in a section marked untrusted.

## What it flags

- **Stale results.** A result counts only while its assumption's test, metric, thresholds and
  deadline stay the same.
- **Dropped critical assumptions.** If you make a critical assumption normal, delete it, or
  replace it with a different assumption under the same id, after a registration, the tool
  shows it in `status`, `decide` and the handoff. It does not
  stop you. It makes sure the person who approves the plan sees it.
- **Overrides.** A `go` against the computed verdict is marked in the envelope and in the spec.

## Limits

- Results are claimed, not proven. The tool cannot see your experiment. A name on a result is
  what someone typed, and an agent with a shell can type any name.
- The idea stage cannot run unattended. Experiments are human work.
- Dropped critical assumptions are tracked by id. If you delete A2 and renumber A3 to A2, the
  tool sees an edit of A2 when one field stays the same, and a replacement when the statement
  and every field change. A small edit in a later registration clears the replacement flag.
- One idea per brief. A pivot closes the brief. You start a new brief for the new idea.
- The tool does not pull results from a form or an analytics service.

## Changes

- **0.1.2.** The `--today` refusal no longer names the test switch, so the tool's own
  output does not tell an agent how to turn the guard off. An experiment result by a
  lookalike of `agent:` is now refused too: fullwidth or Cyrillic letters, a fullwidth colon,
  a space before the colon, or `/`, `-` or `_` as the separator. This is the same check as
  growth-review.
- **0.1.1.** `--today` works only in tests. Before, `record --today 2099-01-01` let
  `--observed` take any date up to 2099. That date got past the future-date check and decided
  which result was the latest. Now the tool refuses `--today` with exit 2, unless the
  environment sets `IDEAS_TEST_CLOCK=1`. Only the tests and the eval set it.

## Files

- `SKILL.md`: the agent's instructions.
- `references/brief-format.md`: the brief grammar, the lint rules, the statuses, the verdict
  and the envelope payload.
- `assets/ideas.py`: the tool (Python 3.10+, standard library only).
- `assets/schemas/idea-brief.v1.json`: the envelope payload schema.
- `eval/run_eval.py`: the outcome eval.
