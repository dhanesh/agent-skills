# Long-running agentic work — what it leaves behind

A change produced over many turns by an agent that plans, executes, verifies and repairs
has a characteristic sediment. It is not the sediment of a careless author. It is the
sediment of a *process*: a loop that must show progress, a context window that forgets, a
check that can be satisfied more cheaply than it can be answered.

None of these patterns is unique to agents — a person under deadline produces the same
shapes. They are worth grouping because they **cluster**, and because they attack precisely
the two things a review is supposed to certify: that the change is correct, and that
somebody can maintain it next year.

## Where the residue comes from

Four properties of the process explain nearly all of it.

**Context is compacted.** A long session summarises its own history to keep going. The half
that writes `fetchUser` may not remember the half that wrote `getUser`. Anything the agent
decided early and did not write down is gone by the end.

**Progress must be visible.** A loop that cannot show movement looks stuck. Producing a
design document, a status file, or a test is movement. Producing "I am blocked on this one
thing" does not feel like movement, even when it is the most useful output available.

**Checks are adversaries as well as guides.** A failing lint rule, type error, or test is an
obstacle between the loop and "done". Silencing it and satisfying it both clear the
obstacle, and silencing it is faster and always works.

**Completion has to be demonstrated.** A test that asserts what the code already does
demonstrates completion perfectly and establishes nothing.

## The patterns, and how to read them

### Suppression instead of resolution — `agentic.suppressed-warning`

`# noqa`, `# type: ignore`, `@ts-expect-error`, `#[allow(dead_code)]`, `eslint-disable`,
`@SuppressWarnings`, `# pragma: no cover`.

Something told the truth and the change turned it off. Occasionally the tool was wrong —
that happens, and a suppression with a comment saying *why* is a fine answer. A bare
suppression is a finding, and a suppression added in the same change that introduced the
code it silences is a strong one.

The subtle variant has no marker at all: renaming an unused symbol to `_name` rather than
deleting it. The underscore prefix means "deliberately unused". Applied to code that is
unused because it is *dead*, it preserves the dead code and silences the tool that found it.

### Work declared unfinished inside a finished change — `agentic.unfinished-work`

`TODO`, `FIXME`, `NotImplementedError`, `unimplemented!()`, `todo!()`, a stub returning a
zero value.

The question is not "should there be TODOs" — it is **which part of the original request
this corresponds to**. A change offered as complete that contains its own admission of
incompleteness is telling you the scope moved, and usually nobody said so out loud.

### Verification theatre — `agentic.tautological-test`, `agentic.assertionless-test`

`assert True`. `assert result == result`. `expect(true).toBe(true)`. A test function that
calls the code and checks nothing.

This is the most damaging pattern in the family, because it converts the strongest evidence
a review has — "there are tests" — into no evidence at all, while looking like the
strongest. Apply one question to any test added by a long-running task: **would this test
fail if the behaviour it names were removed?** If you cannot answer yes by reading it, it is
not covering anything.

Adjacent and harder to see: a test that snapshots current output. It fails when behaviour
changes, which sounds right, but it never encoded what the behaviour was *supposed* to be —
so the repair is always to re-record the snapshot.

### Flake masking — `agentic.sleep-in-test`

A `sleep` in a test is a race nobody has named. It appears when a loop hits an
intermittent failure and needs it to stop. The cost is paid later and by someone else: the
suite gets slower, and the race is still there.

Ask what the sleep is waiting for, and whether that thing can be waited on directly.

### Tests welded to the implementation — `agentic.implementation-coupled-test`

Several mocks, then assertions about which methods were called with what. These pass, they
look thorough, and they fail on any refactor that preserves behaviour — which makes them a
long-term tax rather than a long-term asset. A loop reaches for them because they are easy
to generate from the implementation it just wrote.

### Context seams — `agentic.naming-drift`, `fit.duplicate-block`

Two names for one operation inside a single change (`getUserProfile` and
`fetchUserProfile`). The same block written twice in different files. A helper added in one
place and re-added, slightly differently, in another.

These are the visible edge of a compaction boundary. The change did not disagree with
itself on purpose; one part of it did not know about the other. That is also why they are
worth fixing before merge rather than after: the divergence is not yet load-bearing.

### Abandoned approaches left in — `agentic.commented-out-code`

Several consecutive rows of commented-out code, added. An approach was tried, replaced, and
the replaced version was commented rather than deleted — usually because the loop was not
sure it would not need it back. Version control already remembers it.

### Narration outrunning the work — `agentic.narration-heavy`

More rows of prose than of code. Design documents, plans, status files and summaries are
genuinely useful outputs of a long task, and they are also the cheapest way to look
productive. The question is whether the prose describes **what shipped** or **what was
planned** — documentation that outran its code goes stale first and is trusted longest.

### Fabricated citations — `agentic.dangling-reference`

A comment or document citing a file, module, or path that is not in the repository. This
one requires `--repo` because it cannot be answered from the diff.

It matters out of proportion to its frequency: a citation reads as evidence. A reviewer who
finds one fabricated reference should re-weigh every other unverified claim in the change,
because the same process produced them.

## What no detector will find

Two of the most important patterns are not mechanisable, and pretending otherwise would be
worse than saying so.

**Silent scope reduction.** The change does a subset of what was asked, competently, and
never mentions the rest. Nothing in the diff reveals this, because the missing work leaves
no trace. The only defence is stating the intent up front:

```bash
python3 assets/review.py open --rev main...HEAD --into .review \
  --intent "add WAL truncation and make it survive a crash mid-truncate"
```

The intent is carried into `REVIEW.md`, and **Fit and scope** must answer against it. The
hardest and most valuable question in the whole review is: *what part of the request is not
in here at all?*

**Confident wrongness.** Long-running work produces fluent, well-structured, internally
consistent output whether or not it is right. Fluency is not evidence. This is why the
review carries a **Confidence and basis** section and why `grade` refuses a review that
does not split `Verified:` from `Unverified:` — a review that never says where its evidence
stops cannot be relied on, however well it reads.

## Making the review a final answer

A review that someone acts on without re-deriving it has to satisfy three things:

1. **Every high-severity signal is resolved**, addressed or dismissed with a reason. `grade`
   enforces this.
2. **The evidence is stated.** `Verified:` is what you established by running, tracing, or
   reading the code the change touches. `Unverified:` is everything taken on trust — the
   suite you did not run, the callers you did not open, the behaviour you inferred from a
   name. A short honest `Unverified:` list is worth more than a long confident review.
3. **The verdict matches the evidence.** `ship` on a change whose `Unverified:` line
   contains the thing that would break is not a verdict, it is a guess. Where the evidence
   does not reach far enough to ship, `needs-discussion` is the accurate answer and costs
   nothing.

The failure this guards against is the one that compounds: a fluent review of fluent code,
both produced by processes optimised to look finished, each treated as evidence for the
other. Stating what was actually checked is the only thing that breaks that loop.
