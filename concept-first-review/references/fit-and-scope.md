# Fit and scope — is this the right size, and does it belong here?

Machine-written code fails differently from hand-written code. It is competent in the
small: the syntax is right, the names are plausible, the tests pass. It goes wrong at a
level a line-by-line read does not reach — the change is the wrong *size*, or it is not
quite the change that was asked for, or it solves the problem in a way this codebase
already solves differently three files over.

Those are the questions here. None of them is answerable by looking at a line.

## The four failures

### Incongruity — it does not look like the code around it

A codebase is a set of decisions someone already made. New code that ignores them is not
neutral; it forks the convention, and from then on every reader has to know both.

Look for a change that:

- **handles errors differently** from its neighbours — raising where the module returns a
  result type, swallowing where the module propagates, introducing a new error type
  alongside an existing one that fits;
- **structures state differently** — a class in a module of functions, a singleton in a
  codebase that passes dependencies, mutable global state where everything else is scoped;
- **names differently** — a different casing, a different verb for the same operation
  (`fetch`/`get`/`load` for one concept), a suffix convention the rest of the package does
  not use;
- **tests differently** — a new framework, a new fixture style, a new assertion idiom;
- **reaches for a new dependency** to do something the codebase already does.

The reviewer's question is never "which style is better". It is: **was this a decision, or
did the author simply not look?** A deliberate divergence with a stated reason is fine and
should be written down. An accidental one is a fork nobody chose.

### Over-engineering — structure with no load on it

The recognisable shapes:

- an interface, trait, or protocol with exactly one implementation, and no second one named;
- a factory, registry, or strategy table holding a single entry;
- a parameter, option, or config key that every call site leaves at its default;
- a layer of indirection whose functions only forward;
- generics or type parameters instantiated at exactly one type;
- an event, hook, or callback nothing subscribes to;
- error handling for conditions the code cannot reach.

Each is defensible *if the second case is real and imminent*. So ask for it by name. "What
is the second implementation?" has only two answers, and both are useful: a name, or an
admission that the abstraction is speculative.

The cost is rarely the code. It is that the abstraction becomes load-bearing by accident —
the next author sees a `Store` trait and writes to the interface rather than the one real
store, and now the indirection cannot be removed.

### Under-engineering — the shape is missing where it is needed

Rarer in machine-written code, and easier to miss because the result looks simple:

- the same block, pasted, in two or three places with small edits between the copies;
- a conditional that grows a new branch every time a case is added, where a table or a type
  would close it;
- a function that takes a flag and does two different things depending on its value;
- validation, retry, or error handling that exists at one call site and not the others;
- a magic constant repeated instead of named once.

The tell is **divergence risk**: two copies that must change together will eventually not.

### Scope drift — this is not what the change was for

The most common failure in agent-written changes, and the one review is worst at catching,
because every individual edit looks reasonable.

- a `chore` or `fix` that adds public surface or changes behaviour;
- a refactor that also fixes a bug, so neither can be reverted alone;
- files touched by one or two lines, scattered, unrelated to the stated purpose;
- a feature that arrives with an unrequested framework for the next three features;
- the actual request only half-answered, because the surrounding work absorbed the effort.

Ask directly: **what was this change asked to do, and what fraction of the diff serves
that?** Then ask the harder one: **what part of the request is not in here at all?** An
agent that hits a wall often produces a large, confident, adjacent change instead of a
small one that says "this part is blocked".

## The principles, and when they actually apply

These get quoted more than they get used. Each is a heuristic with a cost, and the review
question is always about the trade, not the rule.

| | The real question | Where it misleads |
|---|---|---|
| **DRY** | Do these two copies encode the *same decision*, so that changing one without the other is a bug? | Two things that merely look alike. Coupling unrelated code through a shared helper is worse than duplicating it. Duplication is cheaper than the wrong abstraction. |
| **KISS** | Could a competent colleague who has not read this change follow it at 3 a.m.? | Simple-looking code that pushes complexity onto every caller. Simplicity is a property of the whole, not of the function you are reading. |
| **YAGNI** | Is there a named, imminent second case — or is this built for an imagined one? | Genuinely load-bearing extension points, and cases where retrofitting later is disproportionately expensive (data formats, public APIs, anything with migration cost). |
| **SRP** | Can you name what this does without using "and"? | Splitting by mechanism rather than by reason to change. Two functions that always change together are one responsibility. |
| **OCP / LSP / ISP / DIP** | Does adding the next case mean editing this, or adding beside it? Does the substitute honour the original's promises? Are callers forced to depend on more than they use? Does policy depend on detail? | Applied preemptively, these *are* over-engineering. They earn their place when the second case exists. |
| **Functional style** | Would making this a pure function of its inputs make it easier to test and reason about? | Purity bought with allocation in a hot path, or with a fold nobody can read. Immutability at a boundary is usually right; immutability everywhere is a language choice, not a review finding. |

A useful discipline: **cite the trade, not the acronym.** "This is the third copy of the
tenant-validation block, and a change to the rules has to find all three" is a finding.
"DRY violation" is a label.

## What the tool can and cannot see

`fit.*` signals mark shapes worth looking at. Every one is diff-only — none of them reads
the surrounding codebase — so they can see repetition, size, and structure, and they
cannot see convention.

| Signal | What it saw |
|---|---|
| `fit.duplicate-block` | Six or more identical rows, twice. Exact match after whitespace collapsing, so a paste-then-rename escapes it — precision bought at the cost of recall. |
| `fit.abstraction-for-one` | An interface/trait/protocol added with exactly one implementation in the change. |
| `fit.unreferenced-addition` | An internal function nothing in the change calls. Public surface is exempt: its callers may be outside the diff. |
| `fit.wide-signature` | More parameters than the baseline's `max_params` (default 5). |
| `fit.long-function` | An added function body over `max_function_rows` (default 60). |
| `fit.pass-through` | A new function whose whole body forwards elsewhere. |
| `fit.restating-comment` | A comment whose words all already appear on the line beneath it. |
| `fit.drive-by-edits` | Three or more files changed by two rows or fewer, in a change touching eight or more. |

**Incongruity is not in that list, and cannot be.** Detecting it needs the surrounding
code, not the diff — which is exactly the reviewer's job, and the one place in this skill
where reading beyond the change is not optional. Open two or three sibling files and check
how they handle errors, name things, and hold state, then ask whether the new code agrees.

Under-engineering is only partly there: `fit.duplicate-block` catches literal copies and
nothing else. A conditional accumulating branches, or a boolean parameter switching
behaviour, is yours to notice.

## Writing the finding

The **Fit and scope** section of `REVIEW.md` wants three things, and it is better short:

1. **What diverges** from how this codebase already works, and whether that looks decided
   or accidental.
2. **What is the wrong size** — built for a caller that does not exist, or missing the
   shape that would stop two copies drifting.
3. **What is in the change that the change was not for** — and, harder and more valuable,
   what the change was for that is not in here.

If none of the three applies, say so in a sentence. A change that fits its surroundings and
does exactly what it set out to do is worth stating plainly, because it is the outcome
everything else is measured against.
