# Spend requests at paid steps

**Status:** Designed on 2026-10-12, after the owner's decisions:
- S7: spend stays never grantable;
- "build the prepared spend request".

Jev scored the design and the Opus advisor decided where Jev was uncertain.

**Goal:** Sometimes an unattended run reaches a step that costs money in the middle of the run: a domain, an API tier or a hosting upgrade. When that happens, the run gives the human a complete spend request that the human can act on. Only that task parks, and the rest of the run goes on. The human makes the purchase. No code moves money.

## Decisions

| # | Decision | Jev | Notes |
|---|---|---|---|
| R1 | **Prose only. The existing commands are used unchanged.** The executor reports `NEEDS_DECISION` with the spend fields. The controller runs `ledger.py check` against the repository root, then passes a one-line request to `conductor decision`. | B 0.60 against A 0.39 (conf. 0.40, uncertain); decided by the advisor | A `request` command in `ledger.py` would reopen the money tool's review surface. The #87 review found a cap bypass there (H1), and the command would add no guarantee that a test could pin. `conductor.py` stays isolated from the ledger, and a test already pins this. |
| R2 | **The controller runs the check, never the executor.** It runs with `--root <repo>`. | advisor | Executors run in a task worktree. A check run there would reserve budget in the wrong state directory, and a resumed session's allowlist may refuse it. |
| R3 | **A stable, collision-proof request id:** `<run-id>-<slug>-<n>-<h>` (see factory-conductor SKILL.md for the one-line command; the review found that `<run-id>-<task>` cut to 64 characters let two tasks share an id). | advisor | A resumed run checks again. A replay is checked against the caps again (the H1 fix) and does not add a second reservation. A new id each time would. |
| R4 | **The request is one line in a fixed format.** | advisor | `status` prints the question JSON-escaped, and the PR body puts it inside inline code (`conductor.py:1502`, `:3390`). A multi-line request would be hard to read in both places. |
| R5 | **The request names the `record` command and the `withdraw` command.** | advisor | A reservation stays until the human records it or withdraws it. A declined request would otherwise hold budget for good. |
| R6 | **No payment links.** Only the vendor and the item. | advisor | The text comes from an agent that reads repository content, so a link could be injected. |
| R7 | **Spend that is known at planning time is asked upfront.** | advisor | Unattended planning already asks every decision before the run. Only spend that the run discovers takes this path. |

No amount or budget went to Jev.

## The request format

```
spend request <id>: <item> from <vendor>; <amount> <CUR> <once in YYYY-MM | monthly from YYYY-MM [to YYYY-MM]>; category <c>; ledger: NOT_REFUSED <id>; why: <one clause>; blocks: <task ids>; after buying: <ledger> --root <repo> record --id <id> --amount <a> --currency <CUR> --month <YYYY-MM> --by <your name>; to decline: <ledger> --root <repo> withdraw --id <id> --by <your name> --reason <text>
```

When the check prints `REFUSED:`, the line says `ledger: REFUSED <reason>` and leaves out the `record` and `withdraw` parts. The task parks the same way, and the human decides whether to change the budget.

Without a registered budget the check refuses, so the line says `ledger: no registered budget`.

On any `STOP:` line (exit 2), the line says `ledger: STOP <the STOP line>`, leaves out the `record` and `withdraw` parts, and the task parks. `<ledger>` is the resolved path to `ledger.py`, written by the controller.

## Changes

- **factory-conductor/SKILL.md:**
  - In the executor brief, add one line about how to report a paid step: `NEEDS_DECISION`, with the item, vendor, amount, currency, recurrence, category and why.
  - In "A task that needs a human", add the controller step: check with `--root <repo>` and the stable id, write the one-line request, then run `conductor decision`.
  - In "Money ledger", point to this flow.
  - Keep the "No spend" rule as it is.
- **factory-conductor/references/ledger.md:** add a short section on spend requests, with the format and an example.
- **factory-conductor/README.md:** add a version note and one sentence on the flow.
- New MUST/SHOULD sentences need register rows (`make bcp14`). Every absolute rule states its reason (PP-5).
- Patch-bump factory-conductor and its pinned version constants.
- `docs/skill-graph/graph.json`: repin line numbers if SKILL.md lines move.
- **A/B row** under `SINCE_SPEND_REQUEST`. It checks that SKILL.md carries the request format and names the controller-run check with `--root`. This is prompt-only: it checks the text, not model behaviour.
- **Eval:** factory-conductor's eval gains a fixture. A decision whose question is a well-formed request line parks only that task. Its dependents become blocked, and an independent task stays ready. This uses the existing `decision` command, so it guards the "rest of the run goes on" promise.

## Out of scope

- Any payment integration, link or connector call.
- Making spend grantable. S7 is closed.
- A `ledger.py` request command (R1).

## Residual risks

- An agent can ignore the prose. The ledger check still only refuses, and no code path can spend.
- The PR body carries the request line, so the amount shows on GitHub to anyone who can read the repository. The committed budget already shows the caps there.
