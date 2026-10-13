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
- **Workers are isolated by id.** Each implementer worker owns a copy of the
  tree and a lane id that keys its database, cache namespace and ports (see
  the isolation contract in `control-pack.md`); only the orchestrator
  integrates into the main tree.
- **Progress is gated, not narrated.** Advance phases only via `PHASE_GATES.md`; "we
  generated a lot of code" is not a gate. Volume masquerading as progress is the
  signature failure of agentic migrations.

## Judge throughput — make the wave fast by design

Measure before you optimise. In a large port the unit tests were not the
bottleneck (seconds, cold). The slow layers were the serial e2e wave (about
3 h for a ~15k-scenario corpus at ~0.7 s each) and mutation proofs (more than
6 h for one suite, because every mutant replayed the whole suite while a
machine-wide lock kept proofs serial). Four tools attack those two layers. All
of them depend on the judge isolation contract in `control-pack.md`.

1. **Size from the scarcest resource.** Run `assets/capacity_probe.py` before
   you choose serial or parallel. It reads host cores, the container VM's CPUs
   and memory (the stateful services run in the VM, not on the host) and free
   disk: `lanes = min(VM CPUs - 1, VM GiB / 1.5, host cores / 2, 6)`, at least 1.
   A 10-core host with a 4-CPU / 6 GiB VM gets 2–3 lanes, not 5; sizing from
   host cores once crashed the document DB under load. When the answer is 1,
   run serial and say which limit decided it. A runtime that does not answer
   (installed but stopped) also means serial: start it and probe again, rather
   than let host cores stand in for a VM nobody measured.
2. **Shard the e2e wave.** `assets/e2e_shard.py` packs suites into lanes
   longest-first (by last run's seconds, else scenario count x mean
   s/scenario), runs lane setups one at a time (they often share tool state,
   such as one virtualenv), captures in parallel, then merges and judges with
   ONE diff against the whole golden directory. A suite no lane captured shows
   as "missing in new"; it cannot pass by being skipped. Trust a sharded run
   only after it reproduces a serial run's counts on the same tree, and re-run
   serially any diff that appears only when sharded: fixed sleeps that wait for
   async side effects miss under load.
3. **Select by impact.** `assets/impact.py` maps changed files to suites through
   the target language's dependency graph (Go: `go list -deps` from each suite's
   own package, never through the app-wiring package that imports everything).
   Its baseline is a content-hash manifest written only after a green FULL run,
   so it works without git history, and only for the tree that run tested:
   ```sh
   TREE=$(python3 "$SKILL_DIR/assets/impact.py" --config impact.json tree-id)
   python3 "$SKILL_DIR/assets/e2e_shard.py" ... --tree-id "$TREE"   # full wave
   python3 "$SKILL_DIR/assets/impact.py" --config impact.json record --status <run>/status.json
   ```
   `record` refuses when slices were integrated while the wave ran, because
   those changes were never tested. It fails closed to ALL on routing/wiring,
   shared platform or config code, build files, sidecars, the judge's own
   tooling, or any file it cannot map. A nightly full run stays mandatory.
4. **Prove mutants on reaching scenarios only.** Replay the legacy rerun once on
   the full suite with per-scenario coverage contexts, then give each mutant
   only the scenarios that executed its lines (`assets/mutant_select.py`).
   Python recipe: run the legacy server under coverage.py, and in a middleware
   call `coverage.Coverage.current().switch_context(<scenario id>)` from a
   header the judge sends; then `coverage json --show-contexts`. Any tool that
   yields a `{file: {line: [scenario, ...]}}` map works too. A line that ran
   only at import time (settings, URL tables, module constants) falls back to
   the full suite. A line no scenario reaches is rejected: a mutant the suite
   cannot reach proves nothing, CAUGHT or MISSED. The subset is safe because
   dropping a scenario that does not reach the mutant can only turn CAUGHT into
   MISSED, never the reverse.

Mutation proofs and sharded waves take the same machine-wide lock
(`assets/machine_lock.py`): a wave that fills every lane plus a proof that boots
extra servers starves the VM. The lock's owner file (pid, host, worker, task,
start time) tells a waiting agent who holds it and for how long, so the loop
wakes on the holder's completion instead of polling.

## Operational rules for a long fan-out

These come from runs that died for reasons unrelated to the code under test
(host sleep, a full disk and a VM restart each killed a multi-hour run). Put the
ones that apply into `RULEBOOK.md` as `MR` rules.

- **Never rewrite a running script or binary in place.** `sh` reads a script as
  it runs, so an edit changes what a running wrapper executes next; one such
  edit killed a proof and left its lock held forever. Install by writing a new
  file and renaming it over the old path, and keep a long shell wrapper's body
  in a function called on the last line (`main "$@"; exit`).
- **Locks die with their holder and record their owner.** Use a kernel file
  lock (`flock`), which the kernel drops when the process dies, plus an owner
  file (pid, host, worker, task, time) so a waiter can see who holds it. A bare
  `mkdir` lock turns a crash into a deadlock, and breaking "stale" mkdir locks
  races when two waiters see the same dead owner. `assets/machine_lock.py` does
  this.
- **Logs, prompts and ledgers live in the repo's `out/` directory**, never in
  `/tmp`, which the host may wipe in the middle of a run.
- **Snapshots exclude what can be regenerated** (staging copies, virtualenvs,
  build output), and old snapshots are pruned to the newest N when free disk
  drops below a threshold. A full disk fails every lane at once.
- **One integration target, one writer at a time.** When `integrate` replaces a
  whole unit (an app directory, a route table), two sub-slices of that unit
  that integrate in parallel erase each other's work. Sub-slices of one unit
  run in sequence.
- **Expect host sleep.** A laptop's maintenance sleep can stretch a 35-minute
  step to hours. Run long waves on a host that does not sleep, or treat sleep
  as a failure mode with a resume path (the work queue and timestamped run
  directories make a rerun cheap).

## The trap to avoid

The seductive takeaway from big-lab migration stories is "they migrated huge codebases
quickly; we should try a rewrite." The correct takeaway is that the leverage came from
strong process loops around the agents. The agents are not the strategy. The loop is the
strategy.
