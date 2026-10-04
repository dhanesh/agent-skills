# release-conductor: carrying a merged change to verified production (roadmap step 5A)

**Status:** The owner approved the design in brainstorming on 2026-10-03, section by section. This spec is written for the owner's review.

**Context:** After PR #68 merged, Jev judged the factory's Q3 (unattended mode works) **yes 0.69**, and "plan to open PR without the human" at 0.83. Q1 (the repo is a software factory) stays **partly 0.97**, and a complete factory scores 0.04, because nothing covers release, operations or the business loop. The assessment rates release/deploy as **missing** in Claude's judgement and as a near coin flip in Jev's. Jev picked roadmap step 5 as the next work (0.96). Step 5 is split in two (owner decision, Jev 0.78/0.79):
- this spec, 5A, covers release and deploy;
- 5B, operations intake, gets its own spec afterwards.

**Goal:** after the human merges a change, the factory prepares the release, deploys it to staging and fully verifies it there. It then puts the production deploy to the human as a single explicit yes, and proves that production runs the new commit. Every gate the factory already has stays in force, and no step weakens A8's hold on production.

**Decision support:** Jev (`jev-latest`) was consulted on every decision, and an Opus advisor reviewed each design section. The owner made every decision that touches a safety floor. Jev settled the purely judgement calls (System One), recorded below with their scores.

## Owner decisions

| # | Decision | Jev | Rejected alternatives |
|---|---|---|---|
| D1 | **Split step 5:** release first (this spec), operations intake as its own spec after. | 0.78 / 0.79 | Intake first (0.20); one combined spec (0.02) |
| D2 | **A per-project release recipe**, written once by interview and repo reading and checked in. It also supports projects whose CI deploys on a tag. | 0.86 / 0.84 | Built-in platform adapters (0.11); CI hand-off only (0.03) |
| D3 | **Production deploy:** when the human is present, an explicit yes for *this* release, after which the skill runs the deploy. When unattended, the release waits with the command ready. | 0.62 | Yes on every release only (0.23); the human always runs it (0.15) |
| D4 | **Rollback asks.** A failed production check stops and asks for the human's yes to roll back. | 0.52 | Automatic if pre-approved at deploy (0.44); always automatic (0.04) |
| D5 | **A8 amended for staging only:** a new grantable class, `deploy_staging`. Production deploy stays never grantable. | 0.96 | Staging always asks (0.04) |
| D6 | **A tag push is deploy class whenever the repo's CI config has tag triggers**, as the checker detects them. Otherwise it is a grantable `push_tag`. The recipe's own flag never decides. | 0.97 / 0.91 | Always ask (0.03); trust the recipe flag (0.09) |
| D7 | **A release grant pins the recipe's sha and the target version.** Every release gate checks against the recipe. Amended after spec review: the merged release commit does not exist when the grant is written, so the version stands in for it (Jev 0.58). `stage` then checks that the merged commit carries that version. | 0.72 / 0.58 | A release-plan envelope (0.28); a second grant pinning the merged commit (0.30); a release block in the factory grant (0.12) |
| D8 | **`release stage` runs when a session invokes it.** Nothing triggers it automatically after the human merges. An automatic trigger is a later item. | 1.00 | Reuse scheduled re-entry now (0.00) |

**Decisions added while planning (2026-10-03, owner, with Jev):**

| # | Decision | Jev | Rejected |
|---|---|---|---|
| D9 | **Grants coexist, selected by subject.** `check-grant --subject X` selects the newest live grant that pins X, so a factory grant and a release grant can both be live. Factory-conductor's `watch` reads the grant that pins its run's plan. `revoke-grant` with no id revokes **all** live grants, so the kill switch stops everything; `--id` revokes one. | 0.54 | One grant per repo (0.46) |
| D10 | **Only `release prep` writes the release grant**, with the human present. Its subjects are the recipe and a release intent file, `.skill-contract/releases/<version>/intent.json`, which is git-ignored and holds the version and the base commit. Pinning the recipe's digest turns a recipe change into the existing `stale` ask. `stage` then runs unattended under that grant. The planning interview records only release defaults: the bump level, and whether to grant staging and tag pushes. This amends D7 and AC6. | 1.00 | The interview writes it (0.00) |
| D11 | **Staging and production are checked by recipe-declared commands.** The recipe lists `staging_checks` and `prod_smoke` as argv lists (HTTP probes or scripts). release-conductor runs them and records SHA-bound evidence itself. Verify skills stay local-only: before staging, the project's verify skill runs its **full** check set **locally** on the release commit, through a verifier other than the release driver, the same evidence predicate factory-conductor uses. | 0.89 | Teach verify skills remote targets (0.10); defer behavioural checks (0.01) |

**Jev's System One calls:**
- `release stage` runs on a non-default `release/<version>-stage` branch (1.00).
- The version bump is a recipe field, which a single release may override (0.99).
- Publishing a GitHub Release is `external_message` class, so it always asks (0.65).
- The recipe declares whether production deploys the staged artifact or rebuilds from source (0.99).

## 1. The release recipe, a trust root

`.release/recipe.json` is checked into the project. `release init` writes it once, by reading the repo and interviewing the user. It holds:

- **Commands**, each an argv list, never a shell string, with the same shell and launcher refusals as `reentry.agent_cmd`:
  - `build`;
  - `deploy_staging`;
  - `deploy_prod`;
  - `rollback`, which takes `{version}` or `{commit}` of the release to roll back to;
  - `health`;
  - `version_probe`: a command, or a URL, that reports the version or commit deployed in an environment. It is called with `{env}` set to `staging` or `production`.
- **`version`**: `{"file": …, "key": …}` or `{"cmd": [...]}`, plus `"bump": "patch" | "minor" | "major"`, the default level.
- **`artifact`**: `"rebuild"`, or `{"path": …}` when production can deploy the staged build itself. In the second case its sha256 is recorded at stage and checked at deploy.
- **`deploy_timeout`**: how long a deploy may take to go live, in seconds; the default is 600. Deploys are often asynchronous, and with CI deploying on tag the push only starts one.
- **`verify_skill`**: the project's verify skill, as made by verification-skill-forge.
- **`prod_smoke`**: the named checks of that verify skill that are read-only, and so safe to run against production. Nothing outside this list ever runs against production.

**Trust rules.** A factory executor could edit the recipe inside a PR, so the recipe is treated like CI config:
- Each release records the recipe's sha.
- A recipe change in the commits being released makes every release step ask, even one a grant covers.
- `release deploy` refuses when the recipe's sha differs from the one recorded at stage.

**Default branch.** Release prep, meaning the version bump and changelog, goes on a `release/<version>` branch and reaches the default branch through a PR the human merges. The skill never writes to the default branch, so the grant floor stays as it is.

## 2. The release flow

`release-conductor` is a new skill. It ships a stdlib state tool, `assets/release.py`, built on the same patterns as factory-conductor: one command per step, atomic state, an append-only log, `NEXT:` lines on resume, and the run lock. Every consequential step calls `check-grant` and proceeds only on COVERED.

1. **`release prep`**
   - Works from the commit at the tip of the default branch.
   - Bumps the version by the recipe's level. `--bump` overrides it for this release.
   - Writes a changelog section from the titles of PRs merged since the last tag. The titles come from local `git log` merge-commit subjects, so no `gh` and no network are needed. They are executor-written, so they are neutralised the same way factory-conductor's PR body is.
   - Commits to `release/<version>`, pushes the branch, and opens the release PR. The classes are `local_reversible`, `push_branch` and `open_pr`.
   - **The human merges the release PR.**
2. **`release stage`**
   - Runs on the merged release commit, pinned by sha, in a worktree on a non-default `release/<version>-stage` branch.
   - Builds from an isolated checkout of that commit, then runs `deploy_staging` (class `deploy_staging`).
   - Checks the staging `version_probe`, then runs the verify skill's **full** check set as an independent verifier, the same rule as factory-conductor's evidence gate. Evidence is recorded with verification-skill-forge's evidence recorder, bound to the sha.
   - A failure stops the release at `stage_failed`.
   - Nothing runs this step automatically after the merge; a session invokes it (D8).
3. **`release deploy`** (class `deploy`, never grantable). It refuses unless all of these hold:
   - staging evidence exists for exactly this commit;
   - the recipe's sha is unchanged since stage;
   - when the recipe deploys an artifact, its sha256 is unchanged;
   - `check-grant --action deploy` answers ASK, which a covering answer would make a bug.

   Before it runs, it records the **rollback target**: what production runs now. That is the production `version_probe`'s answer, or failing that the previous `release-result/v1`, or failing both, "none". A project that was in production before it adopted the skill then still gets a real target.

   - **When the human is present:** it shows the version, commit, staging evidence, recipe sha, the exact deploy and rollback argv, and the rollback target. The rollback target is found by a read-only production probe that runs before the yes, so the human sees it; a `deploy --approved-by` whose target differs from the one shown waits again (R36). For a first release it shows "no previous release, nothing to roll back to" instead. It asks for an explicit yes for this release, then runs `deploy_prod` from an isolated checkout through the harness's own permission prompt. The yes is recorded in the release record as **CLAIMED**, because a shell-capable agent can forge in-session approval.
   - **When unattended:** it stops at `awaiting_deploy` with the command ready.
   - **When CI deploys on tag:** the tag push is the production deploy, under the same refusals and the same yes (D6).
4. **`release verify-prod`**
   - Polls the production `version_probe` until it reports the pinned commit or version, or until `deploy_timeout` expires. A deploy still in progress is not a failure.
   - Only a timeout, or a different version that is not the old one, counts as `prod_failed`. Production still running the old version past the timeout fails too, because otherwise a healthy old deploy would pass.
   - Then runs `health` and only the `prod_smoke` checks.
   - On pass, the release is `verified`, and the tool writes `release-result/v1`.
   - On fail, the release is `prod_failed`. It stops, shows what failed, and asks for the human's yes to run `rollback` (deploy class, the same yes as a deploy). It then checks production again with the probe set to the rollback target.

**Tags.** `release stage` pushes the `v<version>` tag on the merged release commit once staging passes. The class is decided as follows:
- `push_tag` when the repo's CI has no tag triggers;
- `deploy`, held for the production yes, when it does (D6). In that case the tag is pushed inside `release deploy` and nowhere else.

**The grant (D7, amended).**
- **Who writes it.** Only `release prep` writes the release grant, with the human's yes (D10). The planning interview records only release defaults, which prep applies.
- **What it covers.**
  - **Subjects:** the recipe's sha and the target version string.
  - **Classes:** `local_reversible`, `push_branch` and `open_pr` for `release/<version>`, plus `deploy_staging` and `push_tag` for that version.
  - **Branches:** `branch_pattern` is the list `["release/<version>", "release/<version>-stage"]`, exact names rather than a `release/*` glob, so the grant covers no other release's branches.
  - **Selection:** every gate in prep, stage, deploy and rollback names the release grant by its path, with the recipe as subject and the prep or stage worktree; `revoke-grant` with no id still stops everything.
  - **Lifetime:** at most 7 days, like every grant.
- **How `stage` checks it.** `stage` refuses when the merged commit's version is not the pinned version, or when the recipe's sha has changed.

**Branch checks for stage.** The user's checkout usually sits on the default branch after the merge, and `check-grant --root` judges the branch at the root. So the checker gains `--worktree <path>`:
- it verifies that the path is a worktree of the same repository (the same git common dir);
- it then judges that worktree's branch, while still finding grants and running the tracked-grant probe at `--root`.

A plain `--branch` argument would let a caller claim any branch, so it is not offered. Release gates pass the `release/<version>-stage` worktree.

**New pieces outside this skill:**
- The reference checker (`contract_check.py`) gains:
  - a release grant shape, pinning the recipe sha and the release commit;
  - action classes `deploy_staging` and `push_tag`;
  - tag-trigger detection in CI config, which forces a tag push to `deploy` class;
  - `--worktree <path>`, verified as a worktree of the same repo.

  The checker is re-vendored to every adopter. SKILL-contract `SPEC.md` registers `release-result/v1` and the release grant shape, and documents the A8 amendment.
- spec-first-planning's unattended interview gains two optional questions: grant staging deploys, and grant tag pushes.
- `write_grant.py` refuses a grant whose `reentry.agent_cmd` allowlist would match the recipe's `deploy_prod` or `rollback` argv. That gives an early warning.
- At release time, `release deploy` and `release stage` also refuse while any active grant's `reentry.agent_cmd` allowlist matches those argv. The recipe may not have existed when the grant was written, and it can change afterwards. Together these make "production commands never appear in a headless allowlist" a mechanical rule where it can be checked.

## 3. Evidence, failures and honesty

- **State and log.** `.skill-contract/releases/<version>/` is git-ignored. It holds an atomic `state.json` and an append-only `release-log.jsonl`. `release-result/v1` pins:
  - the release commit;
  - the version;
  - the recipe sha;
  - the artifact sha, when there is one;
  - the staging evidence;
  - the production probe and smoke evidence;
  - the rollback target;
  - the CLAIMED human yes;
  - the log digest.
- **States:** `prepped → staged | stage_failed → awaiting_deploy → deploying → deployed → verified | prod_failed → rolling_back → rolled_back`.
- **A crash mid-deploy or mid-rollback leaves the outcome unknown.** On resume, the tool never re-runs the production or rollback command, because deploy commands are not known to be idempotent. It marks the release `outcome_unknown`, runs the version probe and `verify-prod`, and asks the human.
- **One release at a time per repo.** The run-lock pattern stops two sessions from both deploying.
- **Timeouts.** Every command has one, and the whole process group is killed when it expires.
- **Secrets.** Deploy output can carry tokens, and URLs with credentials. Command tails stay in the local release directory, and none of it goes into `release-result/v1`, the release PR or any published notes. The recipe's commands run with the user's own credentials.
- **Rebuild honesty.** When the recipe says `rebuild`, the record states that staging verified the same source, not the same bytes.
- **The honesty section states** that the harness's permission prompt is no gate if the user runs with permission prompts bypassed. The real floors are:
  - `check-grant` never covers `deploy`;
  - production commands never appear in any headless allowlist, which `write_grant` checks;
  - the CLAIMED yes.

## 4. Acceptance criteria

- **AC1.** The checker:
  - validates the release grant shape;
  - adds `deploy_staging` and `push_tag`;
  - forces `deploy` for a tag push when CI config has tag triggers;
  - verifies `--worktree` and judges the worktree's branch;
  - is re-vendored byte-identical, with `make contract` passing.
- **AC2.** `release.py` has unit tests for:
  - every state transition;
  - each refusal in `release deploy`;
  - the version-probe mismatch, and polling up to `deploy_timeout` for an asynchronous deploy;
  - a rollback target taken from the live probe;
  - the artifact sha check;
  - the recipe-change ask;
  - crash recovery to `outcome_unknown` without re-running the command;
  - the lock;
  - changelog neutralisation.
- **AC3.** Eval NEGATIVE checks:
  - no production deploy without staging evidence at the same commit;
  - none with a changed recipe;
  - none unattended;
  - a tag push asks when CI has tag triggers;
  - `verify-prod` runs only `prod_smoke`;
  - `verify-prod` fails on a version mismatch;
  - rollback asks;
  - a crash in `deploying` never re-runs the command;
  - `write_grant`, and `release deploy` at release time, refuse a production command in a headless allowlist;
  - `stage` refuses a merged commit whose version differs from the grant's pinned version.
- **AC4.** An end-to-end test runs `prep` through `verify-prod` against a local server standing in for staging and production, with a version endpoint and stub deploy commands that write marker files. No test touches a real target.
- **AC5.** A/B:
  - "releases reaching verified production" goes 0 → 1;
  - each guard in AC3 is mutation-proven, as in the scheduled re-entry work.
- **AC6.** spec-first-planning asks the two optional grant questions, and `write_grant.py` writes the classes.
- **AC7.** BCP 14 rows exist, and every absolute states its reason (PP-5). The README catalog and the Software factory section list release-conductor and the stage it covers. `make gate` passes, including a CI-equivalent run.
- **AC8.** After merge, Jev re-judges:
  - Q1;
  - the release/deploy stage;
  - "a merged change reaches verified production with only the human's production yes".

  The result is recorded in the assessment doc.

## Plan order

The checker and grant changes (AC1) land first, as their own tasks, because every adopter re-vendors them. Then come the release tool, the skill, the planning-interview changes, and the A/B rows.

## Out of scope

- Operations intake (5B, its own spec).
- An automatic trigger for `release stage`.
- Per-platform deploy knowledge.
- Changelog schemes beyond merged-PR titles.
- Semver policy beyond the recipe's bump level.
- App-store submission.
- Publishing GitHub Releases. This always asks (`external_message`), and the skill does not automate it.
