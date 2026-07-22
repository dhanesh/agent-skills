# Pilot playbook — from control pack to controlled fan-out

The first deliverable of a migration is the Control Pack, not code. This playbook covers
what happens around and after the pack: the disposable pilot, the fan-out, and the one
doctrine that keeps the whole thing from degrading into hand-patched chaos.

## The doctrine: fix the loop, not the files

> You don't fix the code. You fix the process loop that produced the code.

When 50 migrated files share the same bug, hand-fixing 50 files creates hidden
inconsistency — the 51st file will have the bug again, and no reviewer knows which files
got which fix. Instead:

1. Identify the pattern behind the defect.
2. Add or amend a rule in `RULEBOOK.md` (a new `MR<n>` with a binding modal).
3. Regenerate or refactor the affected batch under the updated rule.
4. Rerun the judge (parity script + tests) over the batch.
5. Add the rule to the reviewer prompts so the next batch is checked for it.

Example: a Python→TypeScript agent keeps mistranslating optional fields. The wrong fix
is patching each file's types. The right fix is `MR7: Any nullable Python field must
become 'field?: Type | null' according to the API's observed null behavior`, then
regenerating the batch and making reviewers check MR7. One bug becomes one durable
process improvement.

Hand-edit only when a defect is genuinely a one-off; if you catch yourself making the
same hand-edit twice, that is the loop telling you it wants a rule.

## Pilot structure

Do **not** pick a major rewrite first. Pick a contained slice with one clear owner, one
module/service, clear operational pain, measurable parity, limited blast radius.

1. **Select the target.** One internal tool, one reconciliation component, one legacy
   adapter, one API contract layer.
2. **Define old-system behavior.** Golden scenarios recorded from reality (dozens to
   hundreds; scale to risk). Include edge cases: failed states, retries, timeouts,
   reversals. These become `PORTABILITY_TEST_PLAN.md` entries and the parity corpus.
3. **Build the parity runner.** Old and new side by side, outputs normalized
   (`--ignore` timestamps/ids, `--tolerance` for float drift), diffed by
   `assets/parity_diff.py`. Prove it can fail on a deliberately broken case.
4. **Write the rulebook.** Money precision, date/time, error states, logging,
   idempotency, API responses, persistence rules — whatever the gap inventory surfaced.
5. **Run a small disposable translation.** Throw the output away; keep the lessons as
   new rulebook rules. The pilot's job is to stress-test the pack, not to ship code.
6. **Only then fan out implementation.**

## Fan-out mechanics

- **Queue-driven.** Implementer agents claim units from `AGENT_WORK_QUEUE.md`; the
  queue is rebuilt from facts (files exist / compile / pass parity), so the run can
  stop, restart, and recover without anyone's memory being load-bearing.
- **Implementers and reviewers are separate agents.** Implementers translate under the
  rulebook; reviewers attack the output with `REVIEWER_PROMPTS.md` (money precision,
  timezone drift, nullability, error handling, idempotency, authz, observability) and
  cite `MR` ids in findings.
- **Batch defects route through the doctrine above** — rule, regenerate, re-judge.
- **Progress is gated, not narrated.** Advance phases only via `PHASE_GATES.md`; "we
  generated a lot of code" is not a gate. Volume masquerading as progress is the
  signature failure of agentic migrations.

## The trap to avoid

The seductive takeaway from big-lab migration stories is "they migrated huge codebases
quickly; we should try a rewrite." The correct takeaway is that the leverage came from
strong process loops around the agents. The agents are not the strategy. The loop is the
strategy.
