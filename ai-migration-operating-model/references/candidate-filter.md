# Candidate filter — should this migration happen at all?

The real decision is not "should we use AI to migrate?" but "can we turn this migration
into a verified production process?" AI changed migration economics — agents work in
parallel, use the old code as a spec, and respond to compiler/test failures — so a
migration no longer has to be existential to be worth considering. But it is not free
(Anthropic's Bun migration reportedly cost on the order of $165k in API pricing), and
without objective verification agents just produce plausible-looking code quickly, which
is worse than slow.

## Decision table

Walk every row before any code is written. The verdict goes in the deliverable report.

| Question | If yes | If no |
|---|---|---|
| Is the current pain measurable (deploy failures, build times, memory bugs, release drag)? | candidate | likely a vanity rewrite |
| Can the old system's behavior be captured (outputs observable, scenarios replayable)? | viable | dangerous — build observability first |
| Can tests / golden scenarios be built against both systems? | viable | high risk — no judge possible |
| Can scope be bounded to one module/service/tool? | pilot possible | avoid a broad rewrite |
| Is rollback cheap (old system keeps running, cutover reversible)? | acceptable | risky |
| Can translation rules be made explicit (a rulebook agents can follow)? | agents useful | agents will be inconsistent |

**Any "no" answer is a finding, not a footnote.** One or two "no"s usually convert the
project into a smaller precursor project (add golden tests, add observability, carve out
a bounded module). Mostly-"no" means recommend against — saying "don't migrate yet, and
here is the precursor work" is a fully successful outcome of this skill.

## Good candidates

Measurable behavior, real current pain, enough tests or reconstructable scenarios,
cheap rollback, bounded scope:

- SDK or framework upgrades
- a Python service to TypeScript/Go where the APIs are well understood
- internal CLI/tool migrations (outputs are trivially diffable)
- an old service with strong golden test cases
- introducing typed contracts around messy dynamic code

## Bad candidates

Unclear business logic, weak tests, tacit production behavior, complex hidden edge
cases, or motivation that is engineering fashion:

> "Let's rewrite this because Go/Rust/TypeScript is cleaner."

That framing is not enough. The framing that passes the filter looks like:

> "This service causes deployment failures, has unclear contracts, and slows releases.
> We can define 50 golden scenarios, migrate one module, and measure parity."

For sensitive production systems — money movement, fees, schedules, mandates,
reconciliation, edge-case state machines — behavioral parity is non-negotiable;
aesthetic code proves nothing.
