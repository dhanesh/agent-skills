# Evidence-gated factory: what landed, what did not (2026-09-29)

> **Dated snapshot.** Records the state of the evidence-gated factory on branch
> `claude/evidence-gated-parallel-factory-9j2ruj`. Commits named here may be rewritten on the
> way into `main`. Add a new file for a later state rather than editing this one.

The spec ("Evidence-Gated Parallel Software Factory") asks for three deliverables in a
mandatory order: `verification-skill-forge`, the evidence gate in `factory-conductor`, the
`spec-first-planning` amendment, then parallel dispatch last. This file records where each
stands and every place where the spec and the repo disagreed.

## Build order, as committed

| Step | Commit | State |
|---|---|---|
| 1. `verification-skill-forge` + a generated verify skill proven live | `b1b0917` | Tooling complete, proven live on the in-repo fixture app (two instances side by side, doctor, drive, record, cleanup, evidence survives: `verification-skill-forge/eval/run_eval.py`). **Not done:** §4.6 generation for one real application, and the independent cold-run session. |
| 2. Evidence gate (§5.1) | `c644629` | Complete, with the §7 regression tests: removing an artifact after the verdict blocks the merge; stale and forged SHAs are rejected. |
| 3. `spec-first-planning` amendment (§6) | `1e9f843` | Complete. |
| 4. Parallel dispatch (§5.2) | `c644629` (partition, trail) | Partial by design, see below. Concurrency stays at the existing default of 2; nothing was raised. |

## Where the spec and the repo disagreed (the repo won)

1. **The conductor was not serial.** §1.4 lists parallel fan-out as missing. It is not:
   `factory-conductor` 1.0.x already ran ready tasks side by side up to `max_parallel`,
   default 2, each in its own worktree. That fan-out existed *before* any evidence gate,
   which is the order §7 calls "worse, not faster". It is unchanged here: the default is still
   2 for plans with and without the gate. Making ungated plans serial (default 1 unless the
   grant sets `max_parallel`) is a one-line change and the owner's call.
2. **Owners do not merge, and one run is one PR.** §5.2 describes an owner per PR that
   merges its own PR. In this repo a grant can never cover `merge`, the conductor merges each
   proven task into its own run branch, and one run opens one PR the human merges. "Merge" in
   §5.1/§5.2 is therefore the conductor's task merge into the run branch; the evidence gate
   sits there.
3. **An independent reviewer already existed**, by convention (a SKILL.md MUST). The gate adds
   recorded identity: `start --owner`, `review --reviewer`, `evidence --verifier`, with the
   owner refused as reviewer or verifier. The ids are whatever the conductor session passes,
   so this is enforcement against an honest-but-careless session, not against a hostile one
   (§7a residuals stand).
4. **"Autopilot-full" is not a separate mode.** The gate is on exactly when the plan carries a
   `verification` block, which `spec-first-planning --unattended` now always emits. No grant
   schema change was made: the grant format lives in the byte-identical vendored
   `contract_check.py`, and changing it means a skill-contract revision.
5. **Stack mode** is the existing `depends_on` chain: a dependent never starts before its
   dependency is proven, so a gap in the verified run stops everything above it. No separate
   stack machinery was added.
6. **Merge serialization and re-verification after a rebase** already held (`merge` refuses a
   branch that moved past the proven head). The gate adds: the verdict is cleared too.

## §9 probes

Rows in `scripts/ab-validate.py` (`check_evidence_gate`, `check_verification_forge`,
`check_runtime_proof_planning`), measured against the `main` merge base (`7dc7950`):

| Probe | Row | Result |
|---|---|---|
| 1 race to the same base | proven work lost when two owners land on the same base | HELD 0→0: the baseline already serialized merges |
| 2 backdoor after verification | merges of a head committed after the verdict | HELD 0→0: the baseline already refused a moved branch |
| 3 forged SHA | merges on a hand-forged record whose sha names another commit | IMPROVED 1→0 |
| 4 lost worktree | gated tasks stranded after a worktree is lost | HELD 0→0 |
| 5 grant expires between verdict and merge | merges after the grant lapsed | HELD 0→0: `merge` was already gated |
| 6 proven feature, source gone | verify skills accepted whose proven feature's source is gone | IMPROVED 1→0 |
| 7 writer issues own verdict | merges verified by the agent that wrote the code | IMPROVED 1→0 |
| 8 maintain meets a product bug | maintain passes that rewrite the map to match a bug | IMPROVED 1→0 |
| 9 two owners, one port | instance ports two owners can both claim | IMPROVED 1→0 |
| 10 rebase after verdict | (same row as probe 2) | HELD 0→0 |

Probes 1, 2, 4, 5 and 10 cannot move from fail to pass against this base: the base already
passes them. They are recorded as guards, not claimed as wins. The §5.1 table's other cases
(stale SHA, doctor red, unmapped feature, missing artifact) are IMPROVED 1→0 rows too, as are
the resume walker (it now asks for a verifier instead of a merge), the partition (two gated
tasks with unknown files are no longer offered together), the Manifold join and the two §6
rows.

## Not done, and why

- **§4.6 on a real application.** The spec named a GrayQuest app; this is a personal
  project, so the target is one of the owner's own runnable apps, chosen by the owner. The
  generated skill lives in that app's repository, not here.
- **The cold run.** An independent agent session given only the app repo and a feature task.
  It depends on the step above.
- **Raising concurrency.** §7 says raise only after clean runs at 2. There are no clean gated
  runs yet, so nothing was raised.
- **Model-in-the-loop evals.** The gate evals are model-free by repo standard; whether a model
  actually follows the verifier brief is a manual protocol still to run.
