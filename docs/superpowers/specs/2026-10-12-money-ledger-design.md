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
