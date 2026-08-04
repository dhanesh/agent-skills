# The review rubric — concepts, algorithms, architecture

The condensed diff tells you *what* changed. This tells you what to ask about it.

Four dimensions. Each one exists to catch a specific failure, and naming the failure is the
fastest way to remember why the dimension is there.

Style, formatting, naming conventions, import order, and missing nil checks are **out of
scope**. The linter has them, the model that wrote the code is good at them, and attention
spent there is attention not spent on the things below. If a supposed nit turns out to
change behaviour, it was never a nit — say so.

## 1. Concepts

*Failure: the code is correct and the idea is wrong.*

- Say what this change does in one plain-language paragraph without naming a file. If you
  cannot, you do not understand it yet — go back to the condensed diff, or ask the author.
  This is a test of the change as much as of you: a change nobody can state plainly is
  usually a change that does more than one thing.
- What idea is new here? A new noun (an entity, a state, a phase), a new verb (an
  operation), or a new relationship between things that already existed?
- Does the code's vocabulary match the domain's? A name that needs a comment to explain it
  is usually a concept nobody has found yet.
- Is the concept the right size? One idea spread across four modules and one module holding
  four ideas fail in the same way, and both are cheaper to fix now than later.
- Does this idea already exist somewhere under another name? Duplicated concepts diverge,
  and the divergence is discovered during an incident.

## 2. Algorithm choices

*Failure: it works on the author's machine and on the fixture.*

- What approach was chosen, and what was it chosen **over**? A change that could only have
  been written one way usually was not thought about.
- What is the input size today, and what is it in a year? At what size does this choice stop
  being right — and when it does, does the system fail loudly or just get slower?
- What is the data structure doing for you? A linear scan inside a loop is a set nobody
  built; a repeated sort is an index nobody created.
- Where does the work happen — per request, per item, per batch? Moving work across that
  boundary is the most consequential performance change there is and the easiest to miss in
  a diff.
- What happens on partial success? Retries, timeouts, and idempotence are algorithm choices,
  not implementation details.
- For anything reachable by untrusted input, ask about the worst case rather than the
  average one.

## 3. Architecture and boundaries

*Failure: nothing is wrong today and everything is harder next year.*

- Which module, process, or trust boundary did this cross? Boundaries are where cost
  accumulates; each crossing is a contract somebody now has to keep.
- Which way do the new dependencies point? A dependency running from a stable thing toward
  a volatile one is the edge later described as "we can't upgrade that."
- Did the logic land on the correct side of a boundary, or on the side that was easier to
  edit? Domain rules leaking into transport code, and transport concerns leaking into the
  domain, both begin as one convenient line.
- What is newly public, and does it need to be? Every exported name is a promise with no
  expiry date.
- What contract changed shape — wire format, schema, config key, CLI flag, database column?
  Is it compatible in both directions during rollout, and during rollback?
- Does an existing abstraction now leak? If callers have to know something they did not
  before, the abstraction moved, whether or not anybody said so.

[design-baseline.md](design-baseline.md) explains how to make the layering questions
*checkable* rather than merely askable.

## 4. Blast radius and risk

*Failure: the change is fine and the rollout is not.*

- Who else is affected — callers, consumers, operators, whoever is on call?
- What is the order between the deploy and any data change, and what happens if only one of
  them lands? If nobody has stated the ordering, nobody has thought about it.
- What is the worst realistic failure, and how would anyone find out it had happened?
- What would have caught a mistake here? If the answer is "review", the change needs a test
  more than it needs an approval.

## Working the signals

`python3 assets/review.py signals --into .review` extracts observations from the *original*
diff — including the dependency rows the condensed diff hides, because import lists are a
poor way to read a change and an excellent way to see its architecture.

Each signal is a **question to resolve, not a defect to report**. Resolve every
high-severity one in the review, either as `**addressed**` (a real concern, here is the
finding) or `**dismissed**` (here is why it does not apply). `grade` enforces that, because
an unresolved signal is exactly what a rushed review skips.

Do not forward a signal's question to the author as though it were your finding. The
extractor cannot see intent, cannot resolve aliases or dynamic dispatch, and cannot see the
code the diff did not touch. It points; you look. The catalogue and each detector's honest
limits are in [signals.md](signals.md).

## Reviewing after another agent

When a review of this change already exists, the highest-value contribution is the
**delta** — what the first pass missed, and where you disagree. `audit` finds the missed
part deterministically; [second-opinion.md](second-opinion.md) covers how to read it and
how to avoid being anchored by a review you read before forming your own view.

## When you are not ready

Concept review needs an understanding of the subsystem, and a diff is a poor teacher. If the
surrounding design is unfamiliar, say so and go learn it — the sibling `feynman-walkthrough`
skill is built for that and leaves behind a durable explainer you can pin as a baseline for
later reviews. A confident review of a system you do not understand is worse than no review,
because it gets trusted.
