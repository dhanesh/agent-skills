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
- Category names are kebab-case and at most 64 characters. A name cannot hold a word that
  reads as approval as a part, so that no output carries one. The words are `ok`, `okay`,
  `yes`, `approve`, `approved`, `accept`, `accepted`, `allow`, `allowed`, `grant`,
  `granted`, `pass` and `passed`.
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

`register`, `record` and `withdraw` refuse an agent name with a `STOP:` (exit 2). The check
is the same one that growth-review and idea-validation use. It folds the name first: Unicode
NFKD without combining marks, Cyrillic, Greek, IPA and small-capital lookalike letters to
Latin, and any case. Every character that is not a letter or a digit counts as a separator.
Then a name that starts with the word `agent` in any form is refused:

- `agent:x`, a bare `agent`, `agent x`, `agent.claude`, `agent;x`, `agent|x`, `@agent:x` and
  `[agent]:x`;
- a lookalike colon or dash (`agent∶x`, `agent꞉x`, `agentːx`, `agent‐x`, `agent—x`) and a
  middle dot (`agent·x`);
- the letters apart (`a gent:x`), and fullwidth, Cyrillic, Greek or small-capital letters
  (`ａｇｅｎｔ:x`, `аgent:x`, `ᴀgent:x`, `ɑgent:x`).

A human name that starts with the word `agent`, such as `Agent Smith`, is refused too. The
check fails closed. `Agentha Ray`, `agentur GmbH`, `Reagent Labs` and `Magenta` pass.

The three commands also refuse a `--by` that holds an approval word (the list above) as a
word, after the same fold: `OK`, `Dana yes`, `Dana_ok` and `ｏｋ` are a `STOP:`, because the
tool echoes the name. `Grant` is a real first name and is refused too (fail closed).
`Yesenia` passes, because the check matches whole words.

The checks stay strict (owner decision, 2026-10-12). A person whose name starts with `Agent`
or holds `Grant` signs with a surname or initials instead, for example `A. Smith` or `G. Lee`.

Names are self-declared: the rule against an agent typing a human's name is in SKILL.md.

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
  months and `--what`) reserves nothing more. It checks the proposal again against today's
  caps and spend, because a cap cut or a record since the first check can put it over. Its
  own reservation (or record) is already in the sums, so the check does not add the amount
  twice. When a month is now over the category cap or the total cap, the replay prints
  `REFUSED: <reason>; <id> stays reserved until the human withdraws it` (exit 3). The reservation
  stays and nothing is written. Otherwise it prints `NOT_REFUSED` again. `25` and `25.00` are
  the same amount. A different month, amount or text with the same id is refused. A
  withdrawn id is refused: a new proposal needs a new id.
- An open-ended recurring proposal (no `--end`) reserves only its first 12 months. The check
  and a replay print `NOTE: open-ended; reserved only to YYYY-MM`. After that month the
  ledger counts nothing for it. Tell the human, and check the proposal again with a new id
  before the reservation runs out.
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
  9. any month would go over the category cap or the total cap. This also applies to a
     replay with the same id and fields: the replay counts its own reservation once.

### record and withdraw

- `record` needs the id of a checked proposal. The amount may differ from the proposal, and
  `0` records a spend that did not happen. A month that has not started is refused. A second
  record for the same id and month replaces the first and prints a `NOTE:`. A recorded spend
  over a cap is still recorded, with a `NOTE:`. A record in another currency is refused.
- `withdraw` frees the proposal's open reservations. Recorded spend stays, because it was
  real, and it still counts against the caps in later checks. Reservations never expire on
  their own.
- A `record` for a month that the proposal never covered still records the spend. It prints
  `NOTE: <month> is not a month that proposal <id> reserved; recorded all the same`.

## Spend requests

A run can reach a step that costs money, such as a domain, an API tier or a hosting upgrade.
The executor reports `NEEDS_DECISION` with the item, vendor, amount, currency, recurrence,
category and reason. The controller then runs `check` from the repository root. It does not
run it in the task worktree, because a check there reserves budget in the wrong state
directory. The controller writes one request line and runs `conductor decision <task>
--question "<line>"`. Only that task parks. Its dependents become blocked, and the rest of the
run goes on. The human buys the item. No code moves money, and the ledger commands do not
change.

The request id is `<run-id>-<task slug>-<n>-<h>`. SKILL.md ("Stop and park rules", spend step 1)
holds the one-liner that prints it, and is the only copy. `n` counts the paid items of one task
from 1. `h` is a hash of the run id, the task id and `n`, so task ids that fold to the same
slug still get different ids. The same inputs give the same id. A resumed run checks the same
request again, and the ledger counts that replay once. A resumed run also reuses the month
and `n` of the earlier request line. If it cannot find that line, it tells the human the old
id so the human can withdraw it.

The request is one line, because `status` prints the question JSON-escaped and the PR body puts
it in inline code:

```
spend request <id>: <item> from <vendor>; <amount> <CUR> <once in YYYY-MM | monthly from YYYY-MM [to YYYY-MM]>; category <c>; ledger: NOT_REFUSED <id>; why: <one clause>; blocks: <task ids>; after buying: <ledger> --root <repo> record --id <id> --amount <a> --currency <CUR> --month <YYYY-MM> --by <your name>; to decline: <ledger> --root <repo> withdraw --id <id> --by <your name> --reason <text>
```

The line names `record` and `withdraw`, because a reservation holds budget until the human
records it or withdraws it. When `check` prints `REFUSED:`, the line says
`ledger: REFUSED <reason>` and has no `after buying` or `to decline` part. Without a
registered budget, the line says `ledger: no registered budget`. When `check` exits 2 with a
`STOP:` line, the line says `ledger: STOP <the STOP line>` and has no `after buying` or
`to decline` part. The task parks in every case, and the controller does not change the id to get round a stop.
`<ledger>` is the resolved path of `ledger.py`, so the human can run the line as it stands.
The line, amount included, goes into the run log, the run-result envelope and the PR body, so
anyone who can read those sees it. It is never sent to a model (see "No figures to a model" in
SKILL.md). The line holds no payment link,
only the vendor and the item.

Example (the id is what the one-liner prints for run `run-20261012T101530Z-a1b2c3`, task
`add-domain`, n 1; `<hash>` stands for its 8 hex digits):

```
spend request run-20261012t101530z-a1b2c3-add-domain-1-<hash>: example.dev domain from Namecheap; 12.00 USD once in 2026-10; category domains; ledger: NOT_REFUSED run-20261012t101530z-a1b2c3-add-domain-1-<hash>; why: the landing task needs a real hostname; blocks: deploy-landing; after buying: python3 /skills/factory-conductor/assets/ledger.py --root /work/shop record --id run-20261012t101530z-a1b2c3-add-domain-1-<hash> --amount 12.00 --currency USD --month 2026-10 --by <your name>; to decline: python3 /skills/factory-conductor/assets/ledger.py --root /work/shop withdraw --id run-20261012t101530z-a1b2c3-add-domain-1-<hash> --by <your name> --reason not needed
```

## Output and exit codes

| Exit | Meaning |
|---|---|
| 0 | `NOT_REFUSED`, or a command that succeeded |
| 2 | bad input, or a STOP: an agent name, an approval word in `--by`, a corrupt or symlinked state file, a lock held for 900 s |
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

No command prints an approval word (the list above) for legal input, and the check writes no
envelope for a pass, so no skill can take a pass as a grant. `lint` prints
`LINT: clean <n> categories, …` for a good file. A bad argument prints the usage and
`STOP: bad arguments; see the usage above` without echoing the argument.

`status` prints:

```text
STATUS: 2026-10 currency=USD stale=false registered=<time> by `Dana`
CATEGORY: hosting cap=25.00 recorded=0.00 reserved=19.99 headroom=5.01
TOTAL: cap=120.00 recorded=0.00 reserved=19.99 headroom=100.01
```

Headroom can be negative when recorded spend went over a cap. `stale=true` means the budget
file differs from the registered copy. `status` refuses a symlinked `.skill-contract`,
ledger directory, state file, log or lock with a `STOP:` (exit 2), as the writers do.

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
  `--reason`. The digest has no key, so it finds a hand edit of one file only. Someone who
  edits `ledger.json` and appends a matching line to `log.jsonl` is not detected (see
  "Known limits").
- `.lock` serialises every command that writes, so two checks cannot both pass on the same
  headroom.

The tool refuses a symlinked directory, state file, log or lock. A corrupt file is a
`STOP:` (exit 2), never a traceback.

## Known limits

- Recorded spend is a claim, and `--by` names are self-declared.
- The committed budget file shows the budget to anyone who can read the repository.
- The tool cannot see spend that nobody records.
- Deleting both `ledger.json` and `log.jsonl` starts a fresh ledger.
- The state digest has no key. It finds a hand edit of one file, not a consistent edit of
  both. Someone who drops a reservation or raises a frozen cap in `ledger.json`, and appends
  a log line with the new digest, passes every check.
- The test-only clock switch lets a back-dated clock pass. Anyone who sets it can move the
  current month back, so a past month counts as current. The tool cannot tell a test from a
  real run when the switch is set.
