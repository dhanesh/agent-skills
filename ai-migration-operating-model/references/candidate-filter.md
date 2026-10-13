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

## Throughput estimate — state the wall-clock per wave

A verdict that ignores judge time hides the real cost: an agent fan-out stalls on a
slow judge, not on slow agents. Run `assets/capacity_probe.py` with the corpus size and
put its estimate in the `economics` brief and the `qualify` verdict:

```text
e2e wave     = scenarios x s/scenario / lanes
proof (once) = mutants x reaching scenarios x s/scenario      (proofs share one lock)
```

```sh
python3 "$SKILL_DIR/assets/capacity_probe.py" --scenarios 15000 --secs-per-scenario 0.7 \
    --mutants 9 --reaching 60
```

Take `s/scenario` from a timed pilot capture, never a guess. Take "reaching scenarios"
from `assets/mutant_select.py` once the pilot has coverage contexts; before that, use
the full suite size and say the estimate is an upper bound. When a wave is longer than
a working session (a few hours), the plan MUST include the parallel judge (isolation
contract, sharding, impact selection) as precursor work, or every fix waits hours for
its verdict.

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
