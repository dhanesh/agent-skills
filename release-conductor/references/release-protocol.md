# Release protocol: the release.py reference

`release.py` is release-conductor's state tool: stdlib Python 3.10+, git 2.31+, one file
beside the vendored skill-contract checker (`contract_check.py`). It needs macOS or Linux (the
run lock uses `fcntl`, and commands run in their own process group). Every command takes
`--root <repo>`. This page is the full reference; SKILL.md holds the protocol an agent follows.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | OK. The step happened (or had already happened, and the command says so). |
| 3 | Stopped, or waiting for a human: a `GATE:` that did not answer COVERED, a `STOP:` line (a version or recipe mismatch, a rejected verifier, a failed stage, a deploy waiting for the yes, an unknown outcome, a failed production check, a held run lock). |
| 2 | Refused or invalid: nothing ran and the release's status is unchanged. Bad flags, no single release to act on, a precondition not met, a refusal listed below. Argparse usage errors exit 2 too. |

Read the lines, not only the code: exit 0 from `stage` can mean "built, now dispatch a
verifier" (`NEXT: dispatch-verifier`) or "staged" (`STAGE: <v> pass`).

## Machine lines

| Line | Printed by | Meaning |
|---|---|---|
| `RELEASE: recipe written` | init | `.release/recipe.json` written. |
| `RELEASE: release defaults applied from grant <id>` / `RELEASE: no release-defaults grant found; …` | prep | Whether a planning grant's `release_defaults` filled a gap `--bump`/`--policy-file` left. |
| `RELEASE: <v> prepped` / `RELEASE: <v> already prepped` | prep | Branch pushed and PR opened. |
| `RELEASE: refused: <reason>` | every command | Exit 2. Followed by `  - <problem>` lines for an invalid recipe. |
| `RELEASE: <v> <status>` / `RELEASE: none` | status | One line per release directory (`unreadable` when its state cannot be read). |
| `STAGE: <v> built <commit>` | stage | Built; waiting for the verifier. |
| `STAGE: <v> evidence <commit>` | stage --evidence | The verifier's records passed. |
| `STAGE: <v> re-running deploy_staging after an interrupted attempt` | stage | An earlier staging deploy never recorded its end; staging is redeployable, so it runs again. |
| `STAGE: <v> tag-held v<v> (CI runs on tags: the tag push is the deploy)` | stage | No tag was made, not even locally; `deploy` pushes it under the yes. |
| `STAGE: <v> pass <commit>` | stage | Staged. |
| `STAGE: <v> fail <reason>` | stage | `stage_failed` (see "Stop reasons"). |
| `DEPLOY: <v> <commit>` and indented summary lines | deploy | The summary the human says yes to: `evidence:`, `recipe:`, `artifact:`, `deploy:`, `rollback:` and, with a yes, `approval: <name> (CLAIMED)`. A `note:` line says when the tag is already on the remote (the push is a no-op and CI may not run again). |
| `DEPLOY: <v> deployed <commit>` / `fail` / `unknown` | deploy | The deploy's end. |
| `PROD: <v> verified` / `PROD: <v> fail <reason>` | verify-prod | The judgement. |
| `PROD: <v> outcome-unknown (was <status>; the command is never re-run)` | verify-prod | A crashed deploy or rollback found and demoted. |
| `ROLLBACK: <v> to <version> <commit> (from <source>)` and `  rollback: <argv>` | rollback | What the human says yes to. |
| `ROLLBACK: <v> rolled-back <target>` / `ROLLBACK: <v> unknown` / `ROLLBACK: no target` | rollback | The rollback's end. |
| `RESULT: <path>` | verify-prod, rollback | The `release-result/v1` envelope, repo-relative. |
| `GATE: <action> COVERED` / `GATE: <action> <status> <reason>` | prep, stage, deploy, rollback | A `check-grant` answer. `deploy` and `rollback` always print `GATE: deploy ASK …`. |
| `STOP: <reason>[: <detail>]` | any | The step stopped (exit 3). |
| `NEXT: <what to do>` | any | The next step (table below). |
| `FAIL: <problem>` | verify-prod, rollback | Why the result envelope could not be written. |
| `WARNING: could not revoke the release grant <id>: …` | prep, stage, verify-prod, rollback | Revoke it by hand: `contract_check.py revoke-grant --root <repo> --id <id>`. |

## Commands

| Command | Acts on | Exits |
|---|---|---|
| `init --answers FILE` | nothing yet | 0 written; 2 invalid recipe, unreadable answers, or a recipe already exists (never overwritten) |
| `prep --approved-by NAME --driver ID [--bump L] [--policy-file F] [--push-cmd JSON] [--pr-cmd JSON]` | the default branch's tip | 0 prepped; 3 gate, push or PR stopped (re-run resumes), or lock held; 2 refused |
| `stage [--commit SHA] [--evidence --verifier ID] [--remote NAME]` | the one prepped or staging release | 0 built or staged; 3 stop, reject, gate or `stage_failed`; 2 refused |
| `deploy [--approved-by NAME] [--unattended] [--remote NAME]` | the one unfinished release | 0 deployed; 3 waiting for the yes, `prod_failed`, `outcome_unknown`, lock held; 2 refused |
| `verify-prod` | the one unfinished release | 0 verified; 3 `prod_failed`, result not written, lock held; 2 refused |
| `rollback [--approved-by NAME] [--unattended]` | the one unfinished release | 0 rolled back; 3 waiting for the yes, `outcome_unknown`, result not written, lock held; 2 refused |
| `status` | every release | 0 |

Every command but `init` and `status` takes the repo's run lock,
`.skill-contract/releases/.lock`, for up to 900 s (`STOP: locked: another release command
holds the run lock`, exit 3). `status` reads only: no lock, no change, so a read during a live
deploy cannot demote it.

### init

Reads the answers file (the recipe as JSON), validates it (schema below) and writes
`.release/recipe.json`. It refuses when one exists: a recipe changes in a reviewed commit.

### prep

Checks, in order and before writing anything: `--approved-by` and `--driver` are non-empty
and carry no control or format character (a zero-width variant of a name cannot pass as
another); `--push-cmd` is a plain non-forced `git push [-u|-q|--set-upstream|--porcelain|--quiet]
<remote> {release_branch}`; the policy file only declines release classes; the recipe is valid,
committed on the default branch and unchanged in the working tree (`recipe-uncommitted`); the
version is a `{"file", "key"}` JSON field holding a plain `MAJOR.MINOR.PATCH` (a prerelease or
`version.cmd` is a human's call); no other release is unfinished; `release/<v>` does not exist.

The default branch is `origin/HEAD`'s target, else `main`, else `master`. The bump is
`--bump`, else the planning grant's `release_defaults.bump`, else the recipe's `bump`. With no
`--policy-file`, a planning grant's `grant_staging: false` or `grant_tag: false` declines
`deploy_staging` or `push_tag`. Only a live, valid grant that carries `release_defaults` and no
`release` block is a source of defaults.

Then it writes the intent file `.skill-contract/releases/<v>/intent.json`
(`{"base_commit", "bump", "version"}`), the release grant (below), adds the grant path to
`.git/info/exclude`, and in the worktree `.skill-contract/releases/<v>/wt-prep` on
`release/<v>` (gated `local_reversible`): sets the version, prepends a `CHANGELOG.md` section
(`## <v> (<date>)`, one bullet per merge-commit subject since the last `v*` tag, each one line
in a code span so it cannot inject markdown, mentions or closing keywords), stages exactly those
two paths and commits `release: <v>`. Then, gated `push_branch`, it pushes exactly that commit
(`<sha>:refs/heads/release/<v>`, hooks off) and, gated `open_pr`, runs the PR command
(`gh pr create --title "Release <v>" --base <default> --head release/<v> --body-file …`).

A failure before the commit removes the worktree, the branch, the release directory, and
revokes the grant. A stop on the push or the PR leaves `prepped` with
`prep.pushed`/`prep.pr` recorded; the same prep resumes the pending step.

### stage

Picks the one release that is `prepped`, `staging_verify` or `staged` (refused when another
is unfinished, or when every release is `stage_failed`: a new prep is needed). Then, before
anything from the recipe runs:

1. reads the grant prep wrote; `grant-unreadable` (exit 3) when it is gone or does not pin the
   recipe and the version;
2. checks the recipe at the base commit and at the release commit against the grant's pinned
   digest (`recipe-changed`, exit 3);
3. finds the release commit: `--commit`, the one recorded by an earlier run, or the newest
   first-parent commit on the local default branch, after the base, at which the version file
   becomes the pinned version (`not-merged` when there is none, or when `--commit` is not on the
   default branch);
4. checks the version file at that commit says the pinned version (`version-mismatch`).

Then, each step saving its progress so a re-run resumes from the first unfinished one:

1. creates the stage worktree `wt-stage` on `release/<v>-stage` at the commit;
2. gated `local_reversible`: checks out `wt-build` (detached) and runs `build` there; records the
   `{"path"}` artifact's sha256, or notes "rebuild: staging verifies the same source, not the
   same bytes"; records the build checkout's HEAD and tracked diff; status `staging_verify`;
   `NEXT: dispatch-verifier <commit>`;
3. with `--evidence --verifier ID`, gated `local_reversible`: the evidence predicate (below);
4. checks `wt-build` and the artifact are unchanged since the build; refuses
   (`allowlist-exposes-prod`, exit 2) while any live grant's allowlist reaches production;
   gated `deploy_staging`: runs `deploy_staging`;
5. polls the staging `version_probe` until it reports the version or commit, up to
   `deploy_timeout`;
6. runs each `staging_checks` argv;
7. the tag: when the CI config at the commit may run on a tag (or git cannot say), holds it
   (`tag_deploys`); otherwise, gated `push_tag`, pushes `<commit>:refs/tags/v<v>` (non-forced). A
   failed push stops (exit 3) without failing the stage; a re-run retries it.

Every gate in stage names the stage worktree, and first checks its HEAD is still the release
commit (`head-moved`, exit 3).

### deploy

Refusals, in order (exit 2, nothing runs, status unchanged): `not-staged` (status is not
`staged` or `awaiting_deploy`); `evidence` (no passing evidence for exactly the release
commit); `recipe-changed` (the recipe at the commit is not the one staged);
`build-tree-changed` / `artifact-altered` (the build checkout or artifact changed since stage);
`deploy-covered` (`check-grant` answered COVERED for `deploy`, which no grant may: a checker
bug); `allowlist-exposes-prod`. A release left `deploying` or `rolling_back` becomes
`outcome_unknown` first (exit 3), and an `outcome_unknown` release is never deployed again.

After the refusals, every deploy probes production once for the rollback target (read-only:
one `version_probe` run in `wt-build`, bounded by 30 s) and re-checks the build checkout (a
probe that edited it is refused, `build-tree-changed`). The summary always names the target:
the expanded rollback argv with `(target <version> <commit>, from <source>)`, or "no rollback
target: no previous release, nothing to roll back to" (R36).

Without `--approved-by`, or with `--unattended`: prints the summary, records the target, sets
`awaiting_deploy` and stops (`STOP: waiting-human`, `NEXT: run deploy with the human`, exit 3).
No deploy command runs.

With the yes, the yes must answer the summary the human last saw: when the release is not
`awaiting_deploy` (no summary shown yet), or the target probed now differs from the recorded
one, it prints the new summary with a `note:` line, records the new target, and waits again
(`STOP: waiting-human`, exit 3, nothing run). Otherwise it prints the summary with
`approval: <name> (CLAIMED)`, saves `deploying` (with the
yes and the target) before the command runs, then runs `deploy_prod` once in `wt-build` with
`{env}` = `production`, or, in tag-deploy mode, pushes `<commit>:refs/tags/v<v>`. Ends:
`deployed` (exit 0, `NEXT: verify-prod`); `prod_failed` on a non-zero exit or a command that
could not start (exit 3, `NEXT: verify-prod then ask the human`); `outcome_unknown` on a timeout
(exit 3, same `NEXT:`).

**The rollback target** is what production ran before the deploy: the production
`version_probe`'s answer (a semver token and/or a 7–40 hex commit; a probe already reporting
this release does not count), else the newest `release-result/v1` of another version (a
verified one's version, or a rolled-back one's target), else `{"source": "none"}`.

### verify-prod

Acts on `deployed`, `outcome_unknown` (only one a deploy left: after a rollback it is
refused), or a `prod_failed` that `deploy` set and no verify-prod judged. It judges a deploy
once: a second run is refused. In a fresh detached checkout of the release commit (`wt-prod`):
polls the production probe every 2 s until it reports the version or commit, up to
`deploy_timeout`; runs `health`; runs each `prod_smoke` argv. `staging_checks` never run here.
Failure reasons: `not-live` (the probe still reports the rollback target, or nothing usable),
`wrong-version` (another version), `health-failed`, `smoke-failed`. A pass writes the result
envelope and revokes the release grant.

### rollback

Acts on `prod_failed` or `outcome_unknown`. Refused (exit 2) with no recorded target
(`ROLLBACK: no target`), a target lacking a value the rollback argv needs
(`target-incomplete`), a covering `deploy` gate, an exposing allowlist, or a checkout that
cannot be made; every one of these runs before the wait. Without a yes it prints the argv and
waits (exit 3). With one: saves `rolling_back` before the command, runs
`rollback` once in `wt-prod`, then polls the probe for the target. A pass is `rolled_back`
with the result envelope; a non-zero exit, a timeout or a probe that never reports the target
is `outcome_unknown` with `NEXT: check production by hand; rollback again only with the
human's yes`. Nothing re-runs it; a new `rollback --approved-by` with a fresh yes may.

## Stop reasons

| `STOP:` | From | Status after |
|---|---|---|
| `gate <action> answered <status> (<reason>)` | prep, stage | unchanged (prep before its commit: undone) |
| `push failed …` / `PR command failed …` | prep | `prepped` |
| `grant-unreadable`, `recipe-changed`, `recipe-unreadable`, `not-merged`, `version-mismatch` | stage | unchanged; nothing from the recipe ran |
| `head-moved` | stage | unchanged |
| `evidence-reject <reason> [<feature>]` | stage --evidence | `staging_verify` |
| `build-failed`, `artifact-missing`, `build-tree-unreadable`, `evidence-failed`, `build-tree-changed`, `artifact-altered`, `deploy-failed`, `timeout`, `check-failed` | stage | `stage_failed` (grant revoked) |
| `tag-push-failed` | stage | unchanged; a re-run retries |
| `waiting-human` | deploy, rollback | `awaiting_deploy` / unchanged |
| `deploy-failed` | deploy | `prod_failed` |
| `outcome-unknown` | deploy, rollback, any locked command finding a crash | `outcome_unknown` |
| `prod-failed` | verify-prod | `prod_failed` |
| `result-failed` | verify-prod, rollback | `verified` / `rolled_back` all the same, with `result.error`; a `result_failed` event is logged and the grant revoked |
| `locked` | any locked command | unchanged |

## States

`prepped → staging_verify → staged | stage_failed → awaiting_deploy → deploying → deployed →
verified | prod_failed → rolling_back → rolled_back`, plus `outcome_unknown` from `deploying`
or `rolling_back`. Finished: `verified`, `rolled_back`, `stage_failed`. Every other status, and
a release directory with no readable state, is unfinished, and only one may be.

A release lives in `.skill-contract/releases/<v>/` (git-ignored by a `*` `.gitignore` the tool
writes): `state.json` (rewritten atomically), `release-log.jsonl` (append-only), `intent.json`,
and the worktrees `wt-prep`, `wt-stage`, `wt-build`, `wt-prod`. Command output tails stay in
`state.json` only.

## The recipe

`.release/recipe.json`, written by `init`, changed only in reviewed commits.

| Field | Shape | Use |
|---|---|---|
| `build` | argv | Runs in `wt-build` at stage. |
| `deploy_staging` | argv | Staging deploy (class `deploy_staging`). |
| `deploy_prod` | argv | Production deploy (class `deploy`, never grantable). |
| `rollback` | argv | Rolls production back; takes `{version}` and/or `{commit}` of the target. |
| `health` | argv | Run by verify-prod. |
| `version_probe` | argv | Prints the version or commit an environment runs; `{env}` is `staging` or `production`. |
| `staging_checks` | non-empty list of argv | Staging only. |
| `prod_smoke` | non-empty list of argv | The read-only checks safe against production; nothing else runs there. |
| `version` | `{"file", "key"}` (dotted key in a JSON file) or `{"cmd": argv}` | prep bumps only the file form. |
| `bump` | `patch` \| `minor` \| `major` | Default level. |
| `artifact` | `"rebuild"` or `{"path": <relative path>}` | With a path, its sha256 is checked from build to deploy. |
| `deploy_timeout` | positive integer seconds, default 600 | How long a deploy may take to go live. |
| `verify_skill` | repo-relative path | The verify skill made by verification-skill-forge. |

Every argv is a list of non-empty strings, never a shell string; shells and launchers that
take a command string are refused (the checker's `argv_problems`, as for `reentry.agent_cmd`).
The only tokens are `{version}`, `{commit}` and `{env}`, whole or embedded. Each command runs
without a shell or stdin, in its own process group, killed whole at its timeout (3600 s for
build, deploys and checks; 30 s per probe run), with the git redirect variables scrubbed.

## The release grant

An `autonomy-grant/v1` statement prep writes, accepted by `--approved-by`:

- subjects: `.release/recipe.json` and `.skill-contract/releases/<v>/intent.json`, by digest;
- `payload.release`: `{"version": "<v>"}`;
- `scope.branch_pattern`: `["release/<v>", "release/<v>-stage"]`, exact names, never a glob, so
  it covers no other release's branches (and, like every grant, never the default branch);
- `gate_policy`: `local_reversible`, `push_branch`, `open_pr`, `deploy_staging`, `push_tag` as
  `grant`, minus what the policy file or the planning defaults declined;
- `expires_at`: 7 days; `revoked: false`; one `grant-accepted` assertion by the human.

**Selection.** Grants coexist. Every gate in prep, stage, deploy and rollback names this grant
by path, with subject the recipe and the prep or stage worktree, so another live grant cannot
answer for it. A recipe change makes the grant `stale` (ASK). `revoke-grant` with no id revokes
every live grant and stops every release at its next gate; `--id` revokes one. The release
grant is revoked when the release finishes and when a prep is undone.

**Allowlist exposure.** stage (before `deploy_staging`), deploy and rollback refuse while any
live grant (expired but unrevoked ones included) has a `reentry.agent_cmd` whose
`--allowedTools`/`--allowed-tools` could run `deploy_prod` or `rollback` (as written or
expanded), or which bypasses permission prompts (`--dangerously-skip-permissions`,
`--permission-mode bypassPermissions`). A malformed allowlist counts as exposing. In tag-deploy
mode, `git push <remote> <commit>:refs/tags/v<v>`, `git push <remote> v<v>` and `git push
<remote> refs/tags/v<v>` count as production commands too, so a grant allowing a broad
`git push` blocks the release until it is narrowed or revoked.

## The CI-tag rule

A tag push is the production deploy whenever the release commit's CI config may run on a tag
(D6). The checker reads GitHub Actions workflows (`on:` must provably exclude tag pushes:
`push` filtered by `branches`/`branches-ignore` only, or tag-free events) and CircleCI (no
`tags` filter means no tag builds). Every other CI file (GitLab, Jenkins, Buildkite, Gitea and
the rest) counts as tag-triggered, as does anything git cannot read. Then stage holds the tag,
`check-grant` answers `push_tag` with ASK `ci-tag`, and deploy pushes the tag under the
human's yes. A false positive costs one extra ask; the recipe's own say never decides.

## release-result/v1

Written by verify-prod (`verified`) and rollback (`rolled_back`) to
`.skill-contract/envelopes/`. The payload schema is `assets/schemas/release-result.v1.json`:
version, commit, recipe sha, artifact sha (or `artifact_note` for a rebuild), the staging
summary (verdict, verifier, commit, features, evidence paths, probe and checks as exit codes),
production (deploy, probe, health, smoke as exit codes, failure reason), the rollback target,
the CLAIMED yes, the rollback (CLAIMED yes, exit code, probe), the log digest and byte count,
and the outcome. Subjects: the recipe (its digest at the release commit), the intent file, and
the release log by the digest of its first `log_bytes` bytes. No command output reaches it.

## Worked example

A project at 1.1.0 with a committed recipe and verify skill (two features, `login` and
`search`), production running 1.1.0.

```text
$ release prep --approved-by "Dana" --driver agent-1
RELEASE: no release-defaults grant found; using explicit flags and the recipe
RELEASE: 1.2.0 prepped
NEXT: merge the release PR, then run stage
# Dana merges the PR; the agent runs git pull
$ release stage
GATE: local_reversible COVERED
STAGE: 1.2.0 built 3f2a…
NEXT: dispatch-verifier 3f2a…
# a fresh verifier (id verifier-2) records login and search at 3f2a… under .verify/
$ release stage --evidence --verifier verifier-2
GATE: local_reversible COVERED
STAGE: 1.2.0 evidence 3f2a…
GATE: deploy_staging COVERED
GATE: push_tag COVERED
STAGE: 1.2.0 pass 3f2a…
NEXT: run deploy with the human
$ release deploy
GATE: deploy ASK gate-ask
DEPLOY: 1.2.0 3f2a…
  evidence: 2 evidence records (login, search) by verifier-2 at 3f2a…
  recipe: sha256 9c1e…
  artifact: rebuild: staging verified the same source, not the same bytes
  deploy: ./deploy.sh production 1.2.0
  rollback: ./rollback.sh 1.1.0 (target 1.1.0 -, from probe)
STOP: waiting-human
NEXT: run deploy with the human
# the agent shows this to Dana and asks; Dana says yes
$ release deploy --approved-by "Dana"
GATE: deploy ASK gate-ask
DEPLOY: 1.2.0 3f2a…
  …
  rollback: ./rollback.sh 1.1.0 (target 1.1.0 -, from probe)
  approval: Dana (CLAIMED)
DEPLOY: 1.2.0 deployed 3f2a…
NEXT: verify-prod
$ release verify-prod
PROD: 1.2.0 verified
RESULT: .skill-contract/envelopes/<id>.json
```

Had the smoke check failed, verify-prod would print `PROD: 1.2.0 fail smoke-failed`, the
rollback argv and target, and `NEXT: ask the human to roll back`; on Dana's yes,
`release rollback --approved-by "Dana"` ends with `ROLLBACK: 1.2.0 rolled-back 1.1.0` and a
`rolled_back` result. `assets/test_release_e2e.py` runs both paths against a local server.
