# Condensing rubric — classify the row, and the operation follows

You are reading a change that already compiles and whose tests already pass. The question
is not "is this line correct". The question is **what decision does this row carry**, and
the answer determines what happens to it.

So classify first. Five classes, and each one has exactly one right treatment.

| The row is… | Treatment | Why |
|---|---|---|
| a decision | keep | it is the reason the change exists |
| one instance of a pattern | keep the first, collapse the rest | the pattern is the information; the repetition is not |
| a small payload in a large wrapper | trim in place | the wrapper carries the meaning, the payload does not |
| forced by a decision elsewhere | drop | it follows mechanically; nobody is choosing anything |
| machine output | drop the file | it is a consequence of the change, not the change |

When a row resists classification, keep it. Over-keeping costs the reader seconds.
Over-hiding costs them the change.

## Decisions — keep

A changed argument. A new guard. A different function on the right-hand side. A new return
path. Anything that alters behaviour or the route data takes through the system.

```
-    p, err := parseSSHKeyPerms(permsJSON)
+    p, err := parseSSHKeyPerms(vals.Permissions)
```

Both rows survive. The entire change is in the argument, and no summary of it is shorter
than the rows themselves.

## Instances of a pattern — keep one, collapse the rest

Keep the row that *names* the operation, then collapse what repeats beneath it.

For a rename or a call-site migration repeated across hunks, one representative pair is
enough. Keep a second only where it exposes something the first does not — a different
condition, a different transformation, a compatibility boundary.

Choose **collapse** when the shape of what is hidden still helps (nesting, block extent);
choose **drop** when it does not. Collapse emits one placeholder row at the block's own
indentation, so the reader can see that something is there without being told what.

Unchanged context rows are usually pattern, not decision. File and hunk headers already
orient the reader, so the customary three rows of surrounding context earn their place only
when a row names the enclosing definition, closes something you kept, supplies data a
surviving row consumes, or shows control flow the change cannot be read without.

Comments follow the same rule: keep contracts, compatibility and security caveats, and
rationale the code cannot express. Drop restatements of the ticket, changelog narration,
and line-by-line commentary on the code beneath.

## Small payload, large wrapper — trim in place

When a branch reports a failure, the reviewer generally trusts the wording. The control
flow is the decision; the message is the payload.

```
 201 │ +    if rd.sshKeyID != sshKeyID {
 202 │ +        t.Errorf("route SSH Key ID = %d, want %d", rd.sshKeyID, sshKeyID)
 203 │ +    }
```

Keep all three rows and trim only the message span on 202, so it reads `t.Errorf(...)`.
The condition being checked stays in full view.

Keep the payload when the payload *is* the decision — when the error's type, wrapping,
status code, or resulting control flow is what changed.

## Forced by a decision elsewhere — drop

A zero value added to a return list because a new return slot appeared somewhere else.
Formatter realignment. A rename already legible from a row you kept. Context threading that
only forwards a value — though keep it when timeout, cancellation, or deadline behaviour is
the actual subject.

## Machine output — drop the whole file

Generated files are a consequence of the change, not the change. Drop the file section
entirely and note the regeneration in the headline. A `Code generated ... DO NOT EDIT.`
banner and the conventional generated paths are strong evidence; read the file if unsure.
Keep the hand-written source that drove the generation — that is where the decision lives.

## Four things the tool handles, so you do not have to

**Dependency rows are already gone.** Imports, includes, requires, and `use` declarations
are removed from every draft and result across every supported language, including
unchanged framing rows and declarations embedded in multiline test fixtures. Do not aim
coordinates at them.

That is not because dependency direction is unimportant — it is because a list of import
lines is a terrible way to review it. `review.py signals` reports new and cross-layer
dependencies as questions, where they can actually be acted on.

**Relocations must be symmetric.** The tool detects exact block moves across hunks and
files and reports their coordinates. Treat both ends the same way. Code compressed at its
destination but not at its origin reads as a deletion, which is a false statement about
the change — the compiler rejects it.

**Structure follows content.** File and hunk headers survive when something inside them
survives and disappear when nothing does. You cannot orphan a header or strand a hunk.

**Invention is unrepresentable.** The plan language has verbs for deleting and for
shortening and no verb for writing something new. There is no discipline to maintain here;
the shape of the language is the guarantee.

## Python: keep the load-bearing path

Python rewards structural reading, so condense around the shortest connected path that
still shows:

1. **the contract** — the function, method, fixture, class, decorator, or option changed;
2. **the condition** — guards, exception boundaries, precedence, async and lifecycle points;
3. **the transformation** — the computation, normalisation, lookup, or dispatch that is
   not obvious from its name;
4. **the effect** — return, yield, raise, response, state mutation, log or warning category;
5. **the specification**, in tests — the scenario, its distinctive input, its expected result.

Compress around those. The high-yield targets are decorator stacks, docstrings, literal
tables, fixture bodies, repeated call sites, parametrised cases, and assertion batches.

Four of these are enforced, so a mistake becomes a rejection rather than a bad review:

- a decorator and the definition it modifies are one unit;
- a `def`/`class` header stays visible while its body collapses;
- triple-quote parity within a hunk is preserved, so no string is left unterminated;
- a collapse may not bury a definition a surviving row still uses.

Parametrised values are specification, not boilerplate: keep the dimensions and the
boundary cases, collapse the repetitive middle, and keep every surviving expectation next
to the input that produces it.
