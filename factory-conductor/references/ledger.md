# The money ledger: the ledger.py reference

`assets/ledger.py` keeps the project's budget and a record of what it spends: hosting,
domains, APIs, ads and subscriptions. Before the agent proposes a spend to the human, it runs
`check`. The check can refuse a proposal that would break the budget. It can never approve
one. Every spend still needs the human's yes, because `spend` is an action class that no
autonomy grant covers.

The tool is stdlib Python 3.10+ and needs macOS or Linux. It opens no network connection,
runs no subprocess and calls no model. It is separate from `conductor.py`: the conductor
never imports it and never reads its state, so no run, grant or envelope can read a check
as permission. A project without `budget/budget.md` sees no change.

Run it as `python3 "$SKILL_DIR/assets/ledger.py" --root <repo> <command>`.

## The budget file

The human commits the budget at `budget/budget.md`:

```markdown
# Budget

currency: USD

| category | monthly_cap |
|---|---|
| hosting | 25 |
| domains | 30 |
| ads | 100.50 |
| total | 120 |
```

- One `currency:` line with an ISO 4217 code: three capital letters A to Z.
- One table with exactly the columns `category` and `monthly_cap`.
- Category names are kebab-case and at most 64 characters. A name cannot hold `ok`,
  `approved`, `pass` or `allowed` as a part, so that no check output carries one.
- One row named `total` holds the monthly total cap. It is required.
- An amount is a decimal string in major units: ASCII digits only, at most 12 digits before
  the point and at most 2 after it. `25`, `19.99` and `0.5` are good. `1e3`, `12.345`,
  `1_000`, `-5` and fullwidth digits are refused. The tool stores amounts as integers in
  minor units (cents) and never uses floats.
- Caps are per UTC calendar month. There are no lifetime caps and there is no currency
  conversion.
- Prose around the table can change freely. Only the currency and the caps count.

## Commands

| Command | Who | What it does |
|---|---|---|
| `lint [BUDGET]` | anyone | Checks the budget file (default `budget/budget.md`). |
| `register --by NAME [--reason T]` | the human | Freezes the parsed budget with its sha256 and the time. |
| `check --id ID --category C --amount A --currency CUR --what TEXT [--month YYYY-MM \| --recurring monthly --start YYYY-MM [--end YYYY-MM]]` | the agent | Refuses a proposal that breaks a cap, or reserves it. |
| `record --id ID --amount A --currency CUR --month YYYY-MM --by NAME` | the human | Records a spend that really happened. |
| `withdraw --id ID --by NAME --reason TEXT` | the human | Withdraws a proposal and frees its open reservations. |
| `status [--month YYYY-MM]` | anyone | Prints caps, recorded spend, reservations and headroom. Read-only. |

`register`, `record` and `withdraw` refuse an `agent:` name and its lookalikes (fullwidth or
Cyrillic letters, a space before the separator, or `/`, `-` or `_` as the separator), with
the same fold as growth-review. Names are self-declared: the rule against an agent typing a
human's name is in SKILL.md.

### register

- The first registration needs no reason.
- A second registration after the budget changed needs `--reason`. It prints one
  `RAISED: <category> <old> -> <new>` line for each cap that went up, `RAISED: total …` when
  the total went up, and `RAISED: <category> none -> <cap> (a new category)` for a new
  category. A lowered cap prints no line.
- Registering an unchanged budget prints `REGISTERED: <sha> (no change since the last
  registration)`.
- A currency change is refused while the ledger holds proposals or records, because there is
  no conversion.
- When the ledger state and its log disagree (see "State files"), `register` without
  `--reason` stops. With `--reason` it registers and brings the two back in step. A
  reservation that a hand edit removed stays removed.

### check

- `--id` is a kebab-case proposal id, at most 64 characters.
- A one-off spend counts in `--month`, or in the current UTC month when `--month` is not
  given.
- A recurring spend (`--recurring monthly`) counts its full amount in every month from
  `--start` to `--end`. Without `--end` it counts 12 months. It covers at most 120 months.
- For each affected month, the check adds the recorded spend, the open reservations and this
  proposal, and compares the sum with the category cap and with the total cap. A sum equal
  to the cap is not refused. One cent over is refused.
- A recorded spend for a month replaces that proposal's reservation for that month.
- Running `check` again with the same id and the same fields (category, amount, currency,
  months and `--what`) reserves nothing more and prints `NOT_REFUSED` again. `25` and
  `25.00` are the same amount. A different month, amount or text with the same id is refused.
  A withdrawn id is refused: a new proposal needs a new id.
- It refuses, in this order, when:
  1. the budget is not registered, or the ledger state was deleted by hand;
  2. `budget/budget.md` differs from the registered copy, is missing or does not lint
     (`REFUSED: budget edited since register (…)`), so every check refuses until the human
     registers again;
  3. the ledger state and its log disagree;
  4. the currency is not the project currency;
  5. the category is unknown;
  6. the amount is not a valid positive amount;
  7. a month is before the current month;
  8. the same id was used with different fields, or was withdrawn;
  9. any month would go over the category cap or the total cap.

### record and withdraw

- `record` needs the id of a checked proposal. The amount may differ from the proposal, and
  `0` records a spend that did not happen. A month that has not started is refused. A second
  record for the same id and month replaces the first and prints a `NOTE:`. A recorded spend
  over a cap is still recorded, with a `NOTE:`. A record in another currency is refused.
- `withdraw` frees the proposal's open reservations. Recorded spend stays, because it was
  real. Reservations never expire on their own.

## Output and exit codes

| Exit | Meaning |
|---|---|
| 0 | `NOT_REFUSED`, or a command that succeeded |
| 2 | bad input, or a STOP: an agent name, a corrupt or symlinked state file, a lock held for 900 s |
| 3 | `REFUSED` |

A check prints one of:

```text
NOTE: reserved 19.99 USD a month in 2026-10 (1 month)
NOT_REFUSED: vps — the human's approval is still required
```

```text
REFUSED: 2026-10 hosting would reach 25.01, over its monthly cap 25.00
NEXT: tell the human this reason; only the human changes the budget
```

The check never prints `OK`, `APPROVED`, `PASS` or `ALLOWED`, and it writes no envelope for
a pass, so no skill can take a pass as a grant. A bad argument prints the usage and
`STOP: bad arguments; see the usage above` without echoing the argument.

`status` prints:

```text
STATUS: 2026-10 currency=USD stale=no registered=<time> by `Dana`
CATEGORY: hosting cap=25.00 recorded=0.00 reserved=19.99 headroom=5.01
TOTAL: cap=120.00 recorded=0.00 reserved=19.99 headroom=100.01
```

Headroom can be negative when recorded spend went over a cap.

`--today YYYY-MM-DD` is for tests only. Outside the test suite the tool refuses it (exit 2).

## State files

Everything except the budget lives in `.skill-contract/ledger/`, which is git-ignored,
because spend records are business data:

- `.gitignore` holds `*`. The tool writes it before any other file and refuses a directory
  whose `.gitignore` has a `!` line.
- `ledger.json` holds the registrations (each a frozen copy of the currency and caps, with
  its sha256, who, when and why) and the proposals (amounts in minor units, months,
  reservations, records and withdrawals). It is written atomically (`os.replace`).
- `log.jsonl` is append-only. Every event carries a sequence number and the sha256 of the
  state it wrote. When `ledger.json` was deleted, emptied or edited by hand, the log
  disagrees with it, and every check refuses until the human registers again with
  `--reason`.
- `.lock` serialises every command that writes, so two checks cannot both pass on the same
  headroom.

The tool refuses a symlinked directory, state file, log or lock. A corrupt file is a
`STOP:` (exit 2), never a traceback.

## Known limits

- Recorded spend is a claim, and `--by` names are self-declared.
- The committed budget file shows the budget to anyone who can read the repository.
- The tool cannot see spend that nobody records.
- Deleting both `ledger.json` and `log.jsonl` starts a fresh ledger.
