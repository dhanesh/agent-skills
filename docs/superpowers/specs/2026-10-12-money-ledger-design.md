# Money ledger and a refuse-only pre-spend check (roadmap step 6D, part 2)

**Status:** Designed unattended on 2026-10-12. Jev scored each decision, and the Opus advisor decided the uncertain ones. This is the part that the spend-metering design parked (S6): "a project-level money ledger and a refuse-only pre-spend check … land with the growth step". growth-review (part 1) merged as #85.

**Goal:** The project keeps a budget and a record of what it spends: hosting, domains, APIs, ads, subscriptions. Before the agent proposes any spend to the human, it runs a check. The check can refuse a proposal that would break the budget. It can never approve one. Approval stays with the human for every spend, because `spend` is an irreversible action class that no grant covers (A8). Whether that ever changes is S7, the owner's decision. This design does not touch it.

## Decisions made unattended

| # | Decision | Jev | Notes |
|---|---|---|---|
| L1 | **A separate tool, `factory-conductor/assets/ledger.py`**, with its state under `.skill-contract/ledger/`. `conductor.py` never imports it and never reads its state. | subcommands 0.69 (conf. 0.38, uncertain); decided by the advisor | The ledger is project state, not run state. `conductor.py` commands work on a run and take its lock. The agent runs the check in any session, not only during a run. Keeping them apart also means no grant field, stop or envelope can read the check's output as permission (S7). |
| L2 | **Monthly caps only:** one cap per category and one monthly total, in UTC calendar months. | monthly 0.52 vs both 0.46 (conf. 0.28, uncertain); decided by the advisor | The check only refuses. So a lifetime cap added later only adds refusals and breaks nothing. |
| L3 | **A recurring proposal counts its full amount in every month it is active,** from its start month to its end month. An open-ended one counts for a fixed horizon of 12 months. | 0.99 | |
| L4 | **One project currency.** A proposal or a record in another currency is refused. There is no FX conversion. | 0.96 | |
| L5 | **A proposal that the check did not refuse reserves budget** until the human records it as spent or withdraws it. | 0.71 | Without this, ten small proposals can each pass alone and together break the cap. |
| L6 | **No link into conductor dispatch.** | 0.58 (uncertain); decided by the advisor | `spend` is in `IRREVERSIBLE_CLASSES`, so no grant can name it and conductor never runs a spend step. A dispatch refusal would guard a path that cannot run, and the coupling could later let the ledger be read as a gate. |

No amount, budget or spend record went to Jev. Only the design options above did. Jev is not part of the spend path, not even as an input: the global rule says that Jev MUST NOT approve or override a permission decision, and spend is one.

## 1. The budget file

The budget is committed Markdown at `budget/budget.md`:

- a `currency:` line with an ISO 4217 code, for example `USD`;
- a table with one row per category (`category`, `monthly_cap`), plus a `total` row for the monthly total cap.

Category names are kebab-case, at most 64 characters. Amounts are decimal strings in major units with at most 2 decimal places, for example `25` or `19.99`. The tool stores them as integers in minor units (cents). Amounts are capped at 12 digits before the point. Floats are never used, because a check that can only refuse still has to add up exactly.

`ledger lint` checks the file. `ledger register --by NAME` stores a frozen copy with a hash and a time. Only a human registers: `agent:` names and their lookalikes are refused, with the same NFKC and confusable fold as growth-review and idea-validation 0.1.2. A re-register needs `--reason`. A re-register that raises any cap also prints a `RAISED:` line for each raised cap.

**Stale is fail-closed.** If `budget.md` differs from the registered copy, every `check` refuses with `REFUSED: budget edited since register`. Editing the budget cannot loosen the check, because the human has to register the edit before it counts.

## 2. Proposals, records and withdrawals

All of these live in `.skill-contract/ledger/`, which is git-ignored, because spend records are business data.

- **`ledger check --id ID --category C --amount A --currency CUR [--recurring monthly --start YYYY-MM [--end YYYY-MM]] [--month YYYY-MM] --what TEXT`**
  - The agent runs this before it proposes a spend to the human.
  - `--id` is a kebab-case proposal id. Running `check` again with the same id and the same fields does not reserve twice: it prints the same verdict again. The same id with different fields is refused with `REFUSED: proposal id reused with different fields`.
  - A one-off spend counts in `--month` (default: the current UTC month). A recurring spend counts its full amount in every month from `--start` to `--end`, or for 12 months when there is no `--end`.
  - For each affected month, the check adds three amounts: the recorded spend, the open reservations and this proposal. It compares the sum with the category cap and with the total cap.
  - It refuses (`REFUSED: <reason>`, exit 3) when:
    - any month would go over a cap;
    - the category is unknown;
    - the currency is not the project currency;
    - the budget is not registered, or it is stale;
    - the amount is not a valid positive amount;
    - a month is before the current month.
  - Otherwise it prints `NOT_REFUSED: <id> — the human's approval is still required` and exits 0, and the proposal reserves its amounts. The check never prints `OK`, `APPROVED`, `PASS` or `ALLOWED`. Nothing in any skill reads `NOT_REFUSED` as permission. A test pins both of these.
- **`ledger record --id ID --amount A --currency CUR --month YYYY-MM --by NAME`**: the human records a spend that really happened. It may differ from the proposal. A recorded spend replaces that month's reservation for that proposal. Only a human may record.
- **`ledger withdraw --id ID --by NAME --reason TEXT`**: the human withdraws a proposal and frees its open reservations. Only a human may withdraw, because freeing budget makes later checks looser.
- **Reservations never expire on their own.** They stay until the human records or withdraws.
- **`ledger status [--month YYYY-MM]`**: prints the cap, the recorded spend, the open reservations and the headroom for each category and for the total, for the month. It is read-only.

## 3. Output and exit codes

| Exit | Meaning |
|---|---|
| 0 | `NOT_REFUSED`, or a command that succeeded |
| 2 | bad input, or a STOP (for example, an agent name or a corrupt state) |
| 3 | `REFUSED` |

The tool writes no envelope for `NOT_REFUSED`, so no skill can take a pass as a grant. `--today` is for tests only, with the same guard as idea-validation 0.1.2. The refusal never names the test switch.

## 4. Safety

- **Never spend.** The agent MUST NOT spend money, buy, subscribe, or start a paid trial, because spend is irreversible and no grant covers it. It MAY draft a proposal for the human.
- **No payment tools.** The agent MUST NOT call a payment, billing or store tool (for example PayPal or Stripe) to make a spend or to change one. It MAY read a bill or invoice that the human exports.
- **Never edit the budget without the human,** because an edit moves the goalposts after the agent has seen the proposal. The check refuses a stale budget, but the agent also MUST NOT register its own edit.
- **No figures to a model.** The agent MUST NOT send budget amounts or spend records to Jev or to any other model, because they are financial data.
- **The check is not approval.** The agent MUST NOT treat `NOT_REFUSED` as permission. It still asks the human for each spend, because the check only finds reasons to say no.

**Known limits:**
- Recorded spend is a claim. `--by` names are self-declared.
- The committed budget file shows the budget to anyone who can read the repository.
- The tool cannot see spend that nobody records.

## 5. Tests, eval, gates

- Budget lint: a good file, a bad currency, a bad amount (float form, too many decimals, too many digits, Unicode digits), a duplicate category, no `total` row.
- Register: refused for agent names and their lookalikes; a re-register needs `--reason`; a raised cap prints `RAISED:`; a stale budget makes every check refuse.
- Check:
  - under a cap is `NOT_REFUSED` and reserves the amount;
  - an amount exactly at the cap is `NOT_REFUSED`;
  - one minor unit over the cap is `REFUSED`;
  - the total cap refuses when no category cap does;
  - a recurring proposal refuses when only month 7 goes over;
  - an open-ended one counts 12 months;
  - another currency is refused;
  - a re-run of the same id reserves once, and a reused id with changed fields is refused;
  - two proposals that each fit alone but not together: the second is refused.
- Record and withdraw: human-only; a record replaces the reservation; a withdraw frees it; neither is possible by an agent name.
- Output: no `OK`, `APPROVED`, `PASS` or `ALLOWED` token in any check output. No envelope is written.
- Isolation:
  - `conductor.py` does not import `ledger` and does not read `.skill-contract/ledger/` (a static test).
  - A grant whose classes name `spend` is still refused by `write_grant.py` and by `contract_check.py`.
- No-I/O test: no socket, no subprocess.
- Eval: a positive flow (register, check, record, status), plus negatives:
  - a stale budget that still gives NOT_REFUSED;
  - an agent-recorded spend;
  - a double reservation;
  - an over-cap recurring month;
  - an approval-like token in the output.
- A/B rows under `SINCE_MONEY_LEDGER`.
- factory-conductor becomes 1.5.0. The ledger is opt-in: a project without `budget/budget.md` sees no change, and `conductor.py`'s behaviour is byte-identical.
- README: the Governance row drops "No project spend ledger yet" and names the ledger and its limits.

## 6. Out of scope

- making `spend` grantable (S7, the owner's decision);
- lifetime caps, FX conversion, invoices and payment-tool adapters;
- a conductor dispatch link (L6);
- forecasting and anomaly detection.

## Errata (implementation, 2026-10-12)

The build follows this design. These points were changed or made exact while building it:

- **`conductor.py` changed by one line.** `CONDUCTOR_VERSION` is now `1.5.0`, because
  `test_conductor_state.py` requires it to equal the SKILL.md version. Run-result envelopes
  now name 1.5.0. No other line of `conductor.py` changed, and it does not import the ledger
  or read its state (`test_ledger_isolation.py`).
- **"Differs" means the parsed budget.** The registration hashes the currency and the caps,
  as growth-review hashes its thresholds. A prose edit in `budget.md` does not make the budget
  stale. A cap or currency edit does, and a missing or unlintable file does too.
- **`record` needs a checked proposal.** §2 gives `record` no `--category`, so the tool takes
  the category from the proposal with that id. An unknown id is a STOP (exit 2). A record of
  `0` is allowed (a spend that did not happen), a second record for the same id and month
  replaces the first with a `NOTE:`, and a month that has not started is refused.
- **A currency change is refused while the ledger holds proposals or records,** because there
  is no conversion (L4). Without them, the human can re-register in another currency.
- **Hand edits are found by a state digest.** Every log event carries a sequence number and
  the sha256 of the state it wrote. When `ledger.json` was deleted, emptied or edited (a
  dropped reservation, a swapped cap), every `check` refuses, `record` and `withdraw` stop,
  and `register` needs `--reason`. Deleting both files still starts a fresh ledger (a known
  limit). Corrected in the review (below): the digest has no key, so it finds a hand edit of
  one file, not a consistent edit of both.
- **Approval words are refused in ids and categories.** An id or a category holding `ok`,
  `approved`, `pass` or `allowed` as a kebab part is refused, and argparse errors do not echo
  the argument, so no check output can carry one of those words. The review (below) made the
  list longer and added `--by`.
- **The `--today` guard names no switch.** idea-validation 0.1.2's STOP line names its
  environment switch. The ledger follows growth-review: the STOP line, SKILL.md, the
  reference and the usage text do not name it. The README does.
- **Exit codes, made exact.** An invalid or non-positive amount and a past month are
  `REFUSED` (exit 3), as §2 lists them. A malformed id, month, `--what` or recurring shape
  is bad input (exit 2). A lock held for 900 s is a STOP (exit 2).
- **The replay comes after the refusal gates.** A re-run with the same id and fields prints
  `NOT_REFUSED` again only when the budget is registered, not stale, the state matches its
  log, and the currency, category, amount and months still pass. So a stale budget refuses a
  replay too. A withdrawn id is refused: a new proposal needs a new id. Since the review
  (below, H1), the replay also goes through the cap check.
- **A recurring proposal covers at most 120 months** and ends by 9999-12. Month arithmetic
  uses plain integers.
- **`status` is read-only:** it takes no lock and creates no file.
- **Known limit: the skill description does not name the ledger.** The SKILL.md rule says
  the agent runs `check` before any spend proposal, in any session. But an agent reads that
  rule only when factory-conductor loads, and the frontmatter description is about running a
  plan. The description is at 1015 of its 1024 characters, so it was not changed. A later
  change can shorten it and add the ledger. **Fixed in the review (below, M5).**

## Errata (independent review, 2026-10-12)

An independent review of `ledger.py` found these defects. Each one is fixed, with a unit
test and, where it fits, an eval arm and an A/B row.

- **H1: a replay is checked against the caps again.** Before, a replay with the same id and
  fields printed `NOT_REFUSED` before the cap loop. After a cap cut, or after a record that
  pushed a month up, a replay of an over-cap proposal still passed. Now the replay computes
  each month's category and total sums with `charge()` over all proposals. Its own
  reservation or record is already in the sums, so the amount is not added twice. When a
  month is over either cap, the replay is `REFUSED` (exit 3) with
  `<id> stays reserved until the human withdraws it`. The reservation stays and nothing is
  written. The new proposal and the replay share one function, `over_cap()`.
- **M1: one agent-name check for three tools.** Lookalikes passed: a bare `agent`,
  `agent x`, `agent.claude`, `agent;x`, `agent|x`, `@agent:x`, `[agent]:x`, `a gent:x`, the
  lookalike colons U+2236, U+A789, U+02D0 and U+0589, the dashes U+2010 and U+2014, a middle
  dot, and `ᴀ` (U+1D00) or `ɑ` (U+0251) for `a`. The new fold is NFKD without combining
  marks, the lookalike map (with the IPA alpha and the small capitals of a, g, e, n and t),
  casefold, and every character that is not a letter (Lu, Ll, Lt, Lo) or a digit becomes a
  space. Modifier letters (Lm, such as U+02D0) are separators, because Python counts them as
  word characters. Then `^ *a *g *e *n *t(?![^\W\d_])` refuses `agent` at the start, its
  letters apart or not, with no letter after it. The block is byte-identical in
  `growth-review/assets/growth.py` (0.1.1) and `idea-validation/assets/ideas.py` (0.1.3), and
  a test pins the three copies together. A human name that starts with the word, such as
  `Agent Smith`, is now refused: the check fails closed. Three tests asserted that
  `Agent Smith` passes; each moved it to its refused list. `agentur GmbH`, `Reagent Labs`,
  `Agentha Ray` and `Magenta` still pass.
- **M2: more approval words, and `--by`.** The refused set is now `ok`, `okay`, `yes`,
  `approve`, `approved`, `accept`, `accepted`, `allow`, `allowed`, `grant`, `granted`, `pass`
  and `passed`, in ids and categories (kebab parts) and in the test token pattern. A `--by`
  that holds one as a word, after the agent-name fold, is a `STOP:` (exit 2) in `register`,
  `record` and `withdraw`, because those commands echo the name. `Grant` is a real first name
  and is refused too (fail closed). `lint` prints `LINT: clean` (it printed `LINT: ok`), and
  `status` prints `stale=true` or `stale=false` (it printed `stale=yes` or `stale=no`). The
  lint problem for a bad category no longer lists the words. A test runs every command on
  legal fixture inputs and finds no approval word in any output.
- **M3: the state digest has no key.** SKILL.md and the reference said a hand edit makes
  every check refuse. That is true for an edit of one file. Someone who edits `ledger.json`
  and appends a log line with the matching digest is not detected. The c29 reason, the
  reference and the known limits now say so. No code change.
- **M4: test and eval gaps.** A unit test and an eval arm show that a withdrawn proposal's
  recorded spend still counts. New eval negative arms: the total cap refuses when no category
  cap does; another currency and a past month are refused with exit 3 (the old token arm
  ignored the exit code); a record in another currency is refused and a record for a month
  that has not started is a `STOP:`; an agent and a lookalike cannot withdraw; a raise
  registered without `--reason` is refused; a state that its log does not match refuses the
  check; one minor unit over a cap is refused and exactly at it is not; an approval word in
  an id or `--by` is a `STOP:`; `--today` without the test switch exits 2; no output of any
  command carries an approval word; and the two H1 replay arms. The reviewer's mutant list
  now dies in the eval, the test-clock mutant included.
- **M5: the description names the ledger.** The frontmatter description was shortened and
  now says to use the skill before proposing any spend, in any session, and that the ledger
  only refuses. It is 1015 characters. growth-review and idea-validation SKILL.md tell the
  agent to run the ledger check before it puts any spend to the human, when factory-conductor
  is installed, and say that a pass is not approval (a SHOULD, with register rows).
- **L1: an open-ended recurring proposal names its horizon.** The check and a replay print
  `NOTE: open-ended; reserved only to YYYY-MM`.
- **L2: a record outside the proposal's months is noted.** It still records, and prints
  `NOTE: <month> is not a month that proposal <id> reserved; recorded all the same`.
- **L3: `status` refuses symlinks as the writers do.** A symlinked `.skill-contract`, ledger
  directory, state file, log or lock is a `STOP:` (exit 2).
- **L4: residual risk, documented.** The test-only clock switch lets a back-dated clock pass.
  The reference's known limits say so, without naming the switch. No code change.
