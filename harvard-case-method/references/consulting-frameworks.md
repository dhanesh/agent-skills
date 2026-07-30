# Consulting frameworks that actually do work here

Loaded when structuring an analysis, sizing a market, or validating a plan. These are the
strategy-house tools that earn their place in this skill — plus a short list of famous ones
that do not, because name-dropping a framework is not the same as using a method.

## The ones that pull their weight

### MECE — mutually exclusive, collectively exhaustive

Barbara Minto invented MECE at McKinsey in the late 1960s. It is a *grouping* discipline:
when you split a problem into parts, the parts must not overlap (mutually exclusive) and
must leave nothing out (collectively exhaustive).

Two checks you can actually run on a decomposition:

- **Overlap:** can a single case fall into two branches? If yes, your numbers will
  double-count.
- **Gap:** name a case that falls into none of them. If you can, the analysis has a blind
  spot — and blind spots are where the plan's real risk usually lives.

The commonest failure in a business plan is a revenue decomposition that is neither: three
segments that overlap at the edges and omit the churned cohort entirely.

### Issue trees — decompose into *yes/no questions*

Developed by McKinsey consultants David Hertz and Carter Bales in the 1960s during a study
of New York City's finances. The key innovation, and the part usually lost in retelling:
**frame each branch as a question that can be answered yes or no**, not as an open-ended
topic.

"Pricing" is a topic and can absorb infinite work. "Can we hold 6% attach at a flat fee?"
is a question, and it either resolves or it doesn't. When a branch of your tree cannot be
answered yes or no, it is not analysis — it is a place to put effort indefinitely.

### The Pyramid Principle — answer first

Minto again (developed at McKinsey in the 1970s; the book followed in 1985). State the
answer, then the grouped supporting arguments beneath it, each group MECE. It is a
*communication* discipline rather than a reasoning one, and it matters for the deliverable:
a recommendation that arrives at its conclusion on page nine has already lost the reader.

For this skill: the decision record's `## Decision` comes before `## Reasoning` for exactly
this reason.

### Driver trees — the anti-fairytale tool

Express the top line as a product of drivers:

```
revenue = institutions × students_per_institution × attach_rate × avg_ticket
```

Now the plan is falsifiable driver by driver, and each driver can carry its own source. A
plan expressed as a single number ("₹420 crore in FY27") cannot be argued with; the same
plan as a driver tree can be argued with in four places. `rigor.py plan` evaluates the
formula against the stated drivers and refuses a top line that does not reconcile.

### Top-down vs bottom-up, cross-checked

The single best fairytale detector, and the one people skip.

- **Top-down:** the market is worth X, we take Y% of it.
- **Bottom-up:** we have N reps, each closes M accounts a quarter, at price P.

Both are easy to write. Getting them to *agree* is hard, and that is the point. When the
two differ by an order of magnitude, at least one is fiction — usually the top-down one,
because "1% of a large market" is a sentence anyone can write and nobody can execute.
`rigor.py plan` requires both and fails when they diverge past a tolerance you set.

### "What would have to be true?" — Roger Martin

Martin developed WWHTBT at Monitor, with the breakthrough on an Inmet Mining assignment in
1994. It reverses the burden of proof. Instead of arguing whether an option is *right* —
which turns into advocacy — you ask what conditions **would have to hold** for it to be the
best choice. The important conditions concern customers, capabilities, costs and
competitors.

Then the move that does the work: **identify the condition you'd least confidently bet on,
and test that one first.** It is cheap, it is decisive, and it kills bad strategies early.

This pairs directly with the premortem in the decision loop — WWHTBT finds the load-bearing
assumption, the premortem imagines it breaking. `rigor.py plan` requires at least two
conditions with exactly one marked `[least likely]`.

### Hypothesis-driven analysis — with a caveat

Start with a candidate answer, then design the analysis to test it. It is genuinely fast,
because it stops you boiling the ocean. Its failure mode is equally real: a hypothesis you
are trying to *confirm* turns the analysis into advocacy with a spreadsheet.

The fix is to state, before analysing, **what result would kill the hypothesis** — the same
falsifier discipline the decision record already demands.

## The ones that mostly do not

Named honestly, because using them here would be cargo cult:

- **BCG growth-share matrix**, **GE-McKinsey nine-box** — portfolio *classification*
  schemes. They sort businesses you already understand into quadrants; they do not tell you
  what to do about a product decision, and the quadrant label often substitutes for the
  analysis.
- **Porter's Five Forces** — a good structural lens on industry attractiveness, on a
  multi-year horizon. It is the wrong grain for "should we ship flat pricing this quarter",
  and invoking it there produces a paragraph of atmosphere.
- **SWOT** — a list with four headings and no discipline about what belongs in them. It
  fails both MECE tests and generates no falsifiable claim.
- **7S** — an organisational-alignment checklist. Useful in a reorg, irrelevant to sizing.

If one of these genuinely fits the question, use it. Reaching for one because it sounds
like strategy is how an analysis acquires the *appearance* of rigour without any.

## Sources

- MECE principle (Minto, McKinsey, late 1960s) — <https://en.wikipedia.org/wiki/MECE_principle>
- Barbara Minto: "MECE: I invented it…", McKinsey alumni — <https://www.mckinsey.com/alumni/news-and-events/global-news/alumni-news/barbara-minto-mece-i-invented-it-so-i-get-to-say-how-to-pronounce-it>
- MECE framework and issue trees (Hertz & Bales origin; yes/no framing) — <https://www.wasilzafar.com/pages/series/consulting-frameworks/consulting-frameworks-mece-issue-trees.html>
- The Pyramid Principle — <https://thinkinsights.net/strategy/pyramid-principle>
- Roger Martin, "What Would Have to Be True?" — <https://rogermartin.medium.com/what-would-have-to-be-true-83dac5bd2189>
- Roger Martin, The Evolution of the Strategic Choice Structuring Process — <https://rogermartin.medium.com/the-evolution-of-the-strategic-choice-structuring-process-653521519014>
