---
name: release-conductor
description: >-
  Carry a merged change to verified production. Use when the user says "release this", "cut a
  release", "ship it to production", "deploy the release", or after a factory-conductor PR has
  been merged. A stdlib state tool (release.py) drives a per-project release recipe: prep bumps
  the version and writes the changelog on release/<version>, writes the release grant (a
  skill-contract autonomy-grant) the human accepts, pushes and opens the release PR; after the
  human merges, stage builds the merged commit in an isolated checkout, holds for an independent
  verifier's SHA-bound evidence, deploys to staging, probes the version, runs the staging checks
  and tags; deploy runs the production deploy only with the human's explicit yes for this
  release; verify-prod proves production runs the commit and runs only the read-only smoke
  checks; rollback asks too. Ends with a release-result/v1 envelope. Not the planner
  (spec-first-planning), not the builder (factory-conductor), not a deploy platform.
license: MIT
compatibility: Requires a POSIX system (macOS or Linux; it does not start on Windows), python3 >= 3.10 (stdlib only) and git >= 2.31; the default PR step uses the gh CLI. The recipe's own commands (build, deploys, probes, checks) run with the user's credentials and reach whatever they target.
metadata:
  author: dhanesh
  version: "0.1.0"
  skill-contract: "1"
  tags: "factory,release,deploy,skill-contract"
---

# Release Conductor

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY in this skill are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and only when, they appear in all capitals.

You are the release conductor. A change has been merged to the default branch, and your job is
to carry it to production and prove it runs there, without weakening any gate the factory has.
You do not deploy anything yourself. You drive `release.py`, the state tool that reads the
project's release recipe, checks the grant before every consequential step, runs the recipe's
commands in isolated checkouts, records every step, and refuses whatever the rules forbid. Your
judgement goes into what you show the human and into the verifier you dispatch; the tool's
output decides what happens next. Production is the human's call, every time: the tool can
prepare, stage and verify a release unattended, but the production deploy and any rollback run
only after the human's explicit yes for this release, in this session.

**Locating this skill's helpers (do this first).** The steps below run bundled
scripts. You execute from the *target repo*, not from this skill's directory, so a
path written relative to this skill will not resolve. Resolve the base directory once and use it
everywhere — including in any subagent prompt, which MUST receive the literal absolute
path:

```sh
SKILL_DIR="<this skill's base directory>"   # your harness provides it when the skill loads
# If you don't have it, discover it:
SKILL_DIR=$(find ~/.claude ~/.config ~/.agents -type d -name 'release-conductor' 2>/dev/null | head -1)
test -d "$SKILL_DIR/assets" || test -d "$SKILL_DIR/scripts"   # verify before proceeding
```

Every command below is `python3 "$SKILL_DIR/assets/release.py" <command> --root <repo>`,
written `release <command>` for short. Run it with Python 3.10 or newer. Each command prints
one machine line per event (`RELEASE:`, `STAGE:`, `DEPLOY:`, `PROD:`, `ROLLBACK:`, `RESULT:`,
`GATE:`, `NEXT:`, `STOP:`) and exits **0** for OK, **3** when the release stopped or waits for
a human, and **2** when the step was refused or its input is invalid (nothing ran, and the
status is unchanged). `STOP: locked` (exit 3) means another release command held the repo's
run lock for the whole 900 s this command waited for it: run the same command again once
that one finishes. Only one release per
repository is unfinished at a time. Every command, line, state and exit code, the recipe
schema, the grant and a worked example are in `references/release-protocol.md`.

## When to use

The user asks you to release, ship or deploy what is on the default branch, or a
factory-conductor PR has just been merged and the user wants it in production. A request to
deploy an unmerged branch is not a release: send the change through a PR first. A project with
no recipe starts with `release init` (step 0).

## Preconditions

You MUST have all four before you run `release prep`, because prep pins what it finds and a
gap found later costs a new prep with the human:

1. **A committed recipe.** `.release/recipe.json` is committed on the default branch, unchanged
   in the working tree. With none, run step 0. prep refuses an uncommitted or edited recipe
   (`recipe-uncommitted`), and a recipe whose version is `{"cmd": …}`, since it cannot bump that.
2. **The project's verify skill.** The recipe's `verify_skill` names a verify skill made by
   verification-skill-forge, committed, with at least one feature in its `features/` map.
   Without one, run verification-skill-forge first: staging cannot be proven without it.
3. **A clean checkout of the default branch**, up to date with `origin`, with `origin`
   configured: prep pushes `release/<version>` there and opens the PR against the default branch.
4. **No unfinished release.** `release status` shows every release `verified`, `rolled_back`,
   `stage_failed` or `abandoned` (or none). prep refuses while another release is unfinished.

If any of them fails, stop and tell the user which one and why.

## The flow

Each step is one command. Read its output lines, not just the exit code.

0. **Init (once per project).** Read the repo (its deploy scripts, CI, hosting config) and
   interview the user for each recipe field; `references/release-protocol.md` lists them.
   Commands are argv lists, never shell strings. Write the answers to a JSON file outside the
   repo, then `release init --answers <file>`. It validates and writes `.release/recipe.json`
   (`RELEASE: recipe written`), and refuses to overwrite one that exists. The recipe is a trust
   root, like CI config: it reaches production through a reviewed PR the human merges, never
   through this skill (`NEXT: commit .release/recipe.json through a reviewed PR, then run prep`).
1. **Prep, with the human present.** prep writes the **release grant**, and `--approved-by`
   is the human accepting it. So first tell the human what the grant covers: the classes
   `local_reversible`, `push_branch`, `open_pr`, `deploy_staging` and `push_tag`, on the branches
   `release/<version>` and `release/<version>-stage` only, for 7 days, pinned to the recipe and
   the release intent; and that production deploys and rollbacks are never covered. Ask for
   their yes, then run
   `release prep --approved-by "<their name>" --driver <your agent id>`. `--bump
   patch|minor|major` overrides the recipe's level; `--policy-file <json>` declines classes
   (`{"deploy_staging": "ask"}`) and can never widen them. Without either flag, prep applies the
   release defaults a planning grant recorded (`RELEASE: release defaults applied from grant
   <id>`). The `--driver` id is yours, as your harness gives it: the verifier in step 3 has to
   be someone else. prep bumps the version file, prepends a changelog section built from merge
   subjects since the last tag, commits on `release/<version>`, pushes it and opens the PR.
   `RELEASE: <version> prepped` with `NEXT: merge the release PR, then run stage` is the end of
   this step. A `GATE:`/`STOP:` pair on the push or the PR, or a failed push or PR (exit 3),
   leaves the release `prepped`; fix what stopped it and run the same prep again, which resumes
   the pending step without a new grant or commit. A refusal (exit 2), or a stop before the
   commit, undoes everything prep wrote and revokes its grant.
2. **The human merges the release PR.** You do not merge it. Once they have, update the local
   default branch (`git pull`) and run `release stage`. It finds the merged release commit (the
   first-parent commit where the version file became the pinned version; `--commit <sha>`
   names it when the history is unusual), checks that its version is the one the grant pinned
   and that its recipe is the one the grant pinned, and only then runs anything from it. It
   creates the stage worktree `.skill-contract/releases/<v>/wt-stage` on `release/<v>-stage`,
   builds the commit in an isolated checkout (`wt-build`), and stops with
   `STAGE: <v> built <commit>` and `NEXT: dispatch-verifier <commit>` (exit 0).
3. **Dispatch the verifier.** Send a fresh subagent the verifier brief below, filled in. The
   verifier MUST be a fresh subagent, not you, because the evidence predicate refuses evidence
   recorded by the prep driver, and a verifier who drove the release is not independent. Pass
   its id, as your harness gives it, in the next step.
4. **Stage with the evidence.** `release stage --evidence --verifier <its id>` judges the
   records: every feature the verify skill maps at the release commit needs a passing
   `evidence.json` for exactly that commit, recorded by this verifier, with unaltered artifacts
   and a green doctor. Then, each step gated by the release grant, it deploys to staging,
   polls the staging `version_probe` until it reports the version or commit (up to
   `deploy_timeout`), runs every `staging_checks` argv, and pushes the `v<version>` tag, or holds
   it when CI runs on tags (`STAGE: <v> tag-held`: the tag push is then the production deploy,
   step 5). `STAGE: <v> pass <commit>` with `NEXT: run deploy with the human` ends staging.
   When the release grant declined `deploy_staging` or `push_tag` (the human's `--policy-file`,
   or a planning grant's release defaults), that gate answers `ASK gate-ask` with
   `NEXT: ask the human to approve <class>, then re-run stage --approved-by <name>`. Ask the
   human about that class only: deploy to staging, or push the tag. On their yes, run
   `release stage --approved-by "<their name>"`. The yes answers only that declined gate, once,
   for this release commit, recorded as CLAIMED. When both classes are declined, stage asks
   twice: one yes deploys staging, then stage stops at `push_tag` and asks again. A yes given
   when stage asked nothing answers nothing. Every other `ASK` reason still stops, and the yes
   is refused unattended.
   `STOP: evidence-reject <reason>` (exit 3) means dispatch a new verifier; a failing feature,
   build, deploy, probe or check is `STAGE: <v> fail <reason>` and the release is
   `stage_failed`: finished, its grant revoked; a fix needs a new change and a new prep.
5. **Deploy, only with the human's yes.** Run `release deploy` with no `--approved-by`. It
   checks every refusal (staging evidence for this exact commit, the recipe and build checkout
   unchanged since stage, the `deploy` gate answering ASK, no headless allowlist reaching
   production), then runs the production `version_probe` once, read-only, to find the
   rollback target (what production runs now: the probe's answer, else the last release
   result, else none), records it, and prints the summary: `DEPLOY: <v> <commit>`, the evidence,
   the recipe sha, the artifact, the exact deploy argv, and the exact rollback argv for that
   target, or "no previous release, nothing to roll back to". Then it waits:
   `STOP: waiting-human`, status `awaiting_deploy`, exit 3. Show the human that summary
   verbatim, then ask in plain words: "Deploy <v> (commit <short sha>) to production now with
   `<deploy argv>`? If it fails, rollback runs `<rollback argv>` (or: there is nothing to roll
   back to). Reply yes to deploy." You MUST NOT pass `--approved-by` unless the human said yes to this deploy in this
   session, because the tool records whatever name you pass as the production yes and cannot
   tell a real one from a forged one. On their yes, run
   `release deploy --approved-by "<their name>"` with the same flags. The yes binds to the
   whole summary: it probes the target again and recomputes the summary, and if anything in
   it differs from what the human saw (the target, the deploy argv, the `--remote`), or no
   attended run showed it, it prints the new summary and waits again
   (`STOP: waiting-human`, nothing run), and you ask again. Otherwise it records the yes as
   CLAIMED and runs `deploy_prod` once from the isolated build checkout, or pushes the tag
   when CI deploys on tags. `DEPLOY: <v> deployed <commit>` with `NEXT: verify-prod` (exit 0)
   is a success. When no human is present, run `release deploy --unattended` instead: it
   shows the same summary and waits at `awaiting_deploy`. A run with
   `FACTORY_CONDUCTOR_REENTRY` set counts as unattended too. A summary shown unattended is
   one no human saw, so when the human returns, their session first runs `release deploy`
   without `--approved-by`, shows them that summary, and only then passes their yes.
6. **Verify production.** `release verify-prod` polls the production `version_probe` until it
   reports the release (a deploy still rolling out is not a failure), then runs `health` and
   every `prod_smoke` argv, and nothing else: `staging_checks` never run against production.
   `PROD: <v> verified` and `RESULT: <path>` (exit 0) end the release; its grant is revoked. A
   failure is `PROD: <v> fail <reason>` (`not-live`: still the old version at the timeout;
   `wrong-version`: a third version; `health-failed`; `smoke-failed`), status `prod_failed`,
   and the rollback the human may say yes to (exit 3). verify-prod judges a deploy once: a
   re-run is refused, so a flaky check cannot be retried into `verified`.
7. **Rollback, only with the human's yes.** Show the human the failure and the printed rollback
   argv and target, and ask in plain words whether to roll back. As in step 5, first run
   `release rollback` without `--approved-by`: it shows the rollback summary and waits, and
   the yes binds only to a summary an attended run showed. The production-yes rule of
   step 5 applies to `--approved-by` here too. On their yes,
   `release rollback --approved-by "<their name>"` runs the recipe's `rollback` once, then
   polls the probe until it reports the target: `ROLLBACK: <v> rolled-back <target>` and
   `RESULT: <path>` (exit 0). Without a yes, or with `--unattended`, it shows the rollback and
   waits (`STOP: waiting-human`). With no recorded target it refuses (exit 2): there is nothing
   to roll back to, so tell the human.

8. **Abandon, only with the human's yes.** Some releases can go nowhere else: the human
   closed the release PR, declined to ship, or pulled the kill switch mid-release, or the
   first release's deploy failed with nothing to roll back to. Every later prep is refused
   while one is unfinished. Tell the human why the release is stuck and ask whether to end
   it. On their yes, run
   `release abandon --approved-by "<their name>" --reason "<their words>"`. It runs no recipe
   command and touches neither production, nor the remote, nor tags: it records the release
   `abandoned` (the yes CLAIMED), revokes its grant, and removes its worktrees and local
   `release/<v>` branches. From `deployed`, `prod_failed` or `outcome_unknown` it says that
   production is the human's to handle. A release left `deploying` or `rolling_back` is first
   demoted to `outcome_unknown` (`STOP: outcome-unknown`, exit 3): the human checks production,
   then you run abandon again. The remote branch and the release PR are left for the human to
   close; a later prep of the same version needs that remote branch gone, and keeps the
   abandoned record beside the new one. abandon refuses `--unattended` and a run with
   `FACTORY_CONDUCTOR_REENTRY` set. You MUST NOT run abandon without the human's yes to ending
   this release, because it ends the release's record for good and only a new prep with the
   human starts another.

`release status` prints every release and its status (`RELEASE: <v> <status>`). It only
reads: it takes no lock and changes nothing, so you can run it at any time, even during a
deploy.

**Reading `NEXT:`.**

| `NEXT:` line | What you do |
|---|---|
| `commit .release/recipe.json through a reviewed PR, then run prep` | Open a PR with the recipe for the human; prep after it merges. |
| `merge the release PR, then run stage` | Tell the human the PR is ready; run stage after they merge it. |
| `ask the human to approve <class>, then re-run stage --approved-by <name>` | The release grant declined this staging step: ask the human about that class; on their yes, step 4's `stage --approved-by`. |
| `fix what stopped it, then re-run prep to resume` (or `stage`) | Read the `GATE:`/`STOP:` line, report it, re-run the same command once the cause is fixed. |
| `merge the release PR and update local <branch> (git pull), then run stage` | The release commit is not on the local default branch yet. |
| `dispatch-verifier <commit>` | Step 3, then `stage --evidence --verifier <id>`. |
| `reset <wt> to <commit>, then re-run stage` | The stage worktree moved; report it, since a verifier that moves HEAD invalidates the gate's checks. |
| `run deploy with the human` | Step 5: show the summary, ask for the yes. |
| `verify-prod` | Step 6. |
| `verify-prod then ask the human` | The deploy failed or its outcome is unknown: run verify-prod once, then report to the human. |
| `ask the human to roll back` | Step 7. |
| `ask the human (there is nothing to roll back to)` | Report the failure; the human decides what happens to production. |
| `run rollback with the human` | Step 7 with the human present. |
| `check production by hand; rollback again only with the human's yes` | A rollback failed or its outcome is unknown: report it; production is the human's to inspect. |
| `a new prep with a human is needed …` / `re-run prep with a human` | The grant or the recipe cannot be trusted any more: report it. |
| `fix what stopped it; the release is finished, and its state.json and release-log.jsonl hold the record` | The result envelope could not be written; the release outcome stands. |
| `check production by hand; then re-run abandon with the human to end the release` | abandon found a crashed deploy or rollback and demoted it: report it; production is the human's to inspect. |
| `the release is finished; a new prep may start` | abandon ended the release: report what the human still has to close (the remote branch, the PR). |

A `RELEASE: refused: grant-revoked` (exit 2) from deploy or rollback means the release
grant was revoked (the kill switch) or superseded: the release cannot go on, so report it,
and on the human's yes end it with step 8.

**Outcome unknown.** A deploy or rollback that timed out, or a command that died mid-flight,
leaves the outcome unknown (`STOP: outcome-unknown`, status `outcome_unknown`). The tool
never re-runs that command. After a deploy, run verify-prod once (it can still prove the
release live) and report to the human; after a rollback, only the human decides what is next.

## Hard rules

- You MUST NOT put the recipe's `deploy_prod` or `rollback` argv, or (when CI deploys on tags)
  a `git push` of the release tag, in any `--allowedTools` or other headless allowlist, because
  a headless agent could then run a production deploy that no human said yes to. The tool
  refuses to stage, deploy or roll back (`allowlist-exposes-prod`, exit 2) while any live grant's
  `reentry.agent_cmd` could reach those commands, expired-but-unrevoked grants included. Report
  the grant ids to the human, who revokes them
  (`python3 "$SKILL_DIR/assets/contract_check.py" revoke-grant --root <repo> --id <id>`) or
  narrows their allowlist.
- You MUST NOT put `release.py`'s own `deploy`, `rollback` or `abandon` in a headless
  allowlist either (a `Bash(python3 *)` rule reaches them), because an agent that can run them
  unprompted can forge the human's yes. The same refusal covers them.
- You MUST NOT run `deploy`, `rollback`, `abandon` or `stage` with `--approved-by` when no human is present, because
  an unattended production change is exactly what the grant floor forbids. Unattended, use
  `--unattended`, which waits with the command ready. A run with `FACTORY_CONDUCTOR_REENTRY`
  set is unattended whatever its flags: deploy and rollback wait, abandon and
  `stage --approved-by` refuse.
- You MUST NOT edit a grant to lift a declined class, because that widens the human's grant
  without them. A declined staging deploy or tag push is answered with the human's
  in-session yes (`stage --approved-by`), never by rewriting the grant.
- You MUST NOT edit, delete or recreate anything under `.skill-contract/releases/`, because the
  state, the log and the intent file there are the release's record that every later command
  reads back and that `release-result/v1` pins by digest. Report a mismatch instead. A
  release that can go nowhere else ends only through `release abandon` with the human's yes
  (step 8).
- You MUST NOT re-run a deploy after `outcome_unknown` by any route, or a rollback without a
  fresh yes from the human after they have checked production by hand, because a deploy
  command is not known to be idempotent and a second run could deploy twice or roll back over a
  live release. After a deploy, run verify-prod once and leave the rest to the human.
- You MUST NOT run any recipe command, a tag push or the release PR merge yourself, because only
  `release.py` checks the grant and the refusals first and records the step. Pass no
  `--push-cmd` or `--pr-cmd` to prep unless the host has no `gh`, so the step the grant approved
  is the step that runs.

## The verifier brief

`<commit>` is the commit from `NEXT: dispatch-verifier <commit>`; `<wt>` is
`<repo>/.skill-contract/releases/<version>/wt-stage`; `<skill>` is the recipe's `verify_skill`.
Use the verifier's real id: a record under a placeholder id fails the verifier match. If your
harness assigns the id only when it spawns the agent, add a line saying the id arrives in a
follow-up message and nothing is recorded until then, and send it at once.

```text
You are verifying release <version> at commit <commit>. You did not prepare this release.
You MUST NOT edit or commit files, move HEAD, or dispatch subagents. You MUST NOT use
test-only endpoints or internal setters.

Worktree: <wt> (its HEAD is <commit>; leave it there). Verify skill: <wt>/<skill>.
Run the app locally from this worktree, as the verify skill's Launch says; do not point
anything at staging or production.
Export VERIFY_EVIDENCE_DIR=<absolute repo root>/.verify and use an instance name of your own.
For EVERY feature in <wt>/<skill>/features/ (the full check set): follow the verify skill's
Launch and Doctor, drive the feature's recipe in features/<id>.md, capture the action, the
resulting state and its side effects, and record it with the skill's verify_evidence.py
record, --verifier <your id>. Then run the skill's Cleanup. Evidence stays.

Report: Verdict: pass | fail, then one line per feature: <id> <evidence path> <observed>.
```

`stage --evidence` rejects (`STOP: evidence-reject <reason>`, exit 3) with one of:
`verifier-missing`, `driver-is-verifier`, `feature-map-unreadable`, `no-features`,
`evidence-missing`, `evidence-stale-sha`, `evidence-sha-mismatch`, `evidence-malformed`,
`evidence-artifact-missing`, `evidence-artifact-altered`, `evidence-verifier-mismatch`,
`doctor-missing`, `doctor-red`. Dispatch a new verifier for each, except `no-features` and
`feature-map-unreadable`: those need verification-skill-forge's maintain mode first. A
recorded failure (`evidence-failed`) is not a reject: the stage fails.

## Deliverable

A **release report** for the user: the version, the release commit, the final status, the
`release-result/v1` path (`RESULT:`), who verified staging and which features, the production
probe, health and smoke results, the rollback target, and the CLAIMED yes with the name. For a
stopped release, the `STOP:` line, the `NEXT:` line and what the human has to decide. Say
whether the artifact was rebuilt (staging then verified the same source, not the same bytes).

**Output style.** Write reports and explanations for the user in about 80% ASD-STE100 Simplified Technical English. Use short sentences, common words, active voice and one action per step. This style is for prose to the user, not for the code, prompts or files that this skill makes.

## Verify and repair

Before you report, run `release status` and check that it shows the status your last command
printed. Then check the envelope as a receiver would:
`python3 "$SKILL_DIR/assets/contract_check.py" check-envelope <RESULT path> --root <repo>`.
A passing check is the expected result; a failure, or a status that does not match the output you read, is a tool bug:
report it rather than editing state.

## What the proof is worth

- **The yes is CLAIMED.** The tool records the name passed to `--approved-by` as the human's
  yes, marked CLAIMED in the state and the envelope, because a shell-capable agent can forge
  an in-session approval. The summary the yes answers names the rollback target, found by a
  read-only production probe that runs before the yes. The yes binds to a digest of the
  whole summary (the deploy or rollback argv with its remote, the rollback argv, the target,
  the commit, the recipe and artifact sha), and only to one an attended run showed: a summary
  that changes before the yes, or one shown only to an unattended run, voids it. The real floors are mechanical: `check-grant` never covers `deploy`
  (deploy refuses if it ever answers COVERED), production commands may not appear in a headless
  allowlist, and the yes is on the record.
- **A permission prompt is no gate if prompts are bypassed.** The harness's prompt before the
  deploy command is a second check only when the user runs with prompts on; under
  `--dangerously-skip-permissions` or `bypassPermissions` nothing stops a forged yes but the
  record.
- **The allowlist check sees only `reentry.agent_cmd`.** Allowlists elsewhere, such as the
  target repo's `.claude/settings*.json`, are invisible to it. It recognises Claude Code's
  `--allowedTools` and permission-bypass flags, not other agents' CLIs (Codex, Gemini), and a
  wrapper script hides what it runs. A malformed allowlist or a bypass flag counts as exposing,
  and so does a grant allowing a broad `git push` in tag-deploy mode, or one reaching
  `release.py`'s own `deploy`, `rollback` or `abandon` (a `Bash(python3 *)` rule does), which
  blocks the release until it is narrowed. The check reads a rule through these wrappers:
  `env`, `uv run`, `uv tool run`, `uvx`, `sudo`, `doas`, `command`, `builtin`, `exec`,
  `nice`, `nohup`, `time`, `stdbuf`, `xargs` and `timeout`, with their options. It reads
  `eval`, `find -exec` (and `-execdir`, `-ok`, `-okdir`), and `-c` on `sh`, `bash`, `zsh`,
  `dash`, `ksh` or `fish` as "any command". It reads `python`, `python3` and `python3.N`, by
  name or by absolute path, with their flags. It reads `python -c` and `python -m` with a
  glob, `pdb`, `runpy`, `cProfile`, `profile`, `trace`, `timeit` or `code` as "any
  command". It expands a `~/` path. So rules such as `Bash(env *)`, `Bash(uv run *)`,
  `Bash(uvx *)`, `Bash(sh -c *)`, `Bash(/usr/bin/*)` or `Bash(./*)` count as exposing.
  Known limits: this list can never be complete. A wrapper or interpreter not on it stays
  unseen, for example `poetry run`, `pipenv run`, `pdm run`, `npx`, `setsid`, `ionice`,
  `flock`, `script -c`, `watch`, `strace`, `perl -e` or `node -e`. A quoted
  `env -S "..."` string is not read. A script path it does not know stays unseen. A glob `*`
  can cross path parts, so a rule like `Bash(python3 tests/*)` can reach
  `tests/../release.py` and is not caught. The real backstop is the re-entry rule: with
  `FACTORY_CONDUCTOR_REENTRY` set, deploy and rollback wait for the human.
- **CI tag-trigger detection covers listed formats.** GitHub Actions workflows and CircleCI
  are read; every other CI config is treated as tag-triggered unless proven otherwise, so a tag
  push then waits for the production yes. A false positive costs one extra ask, never a silent
  deploy.
- **Rebuild honesty.** With `"artifact": "rebuild"`, production rebuilds from the release
  commit: staging verified the same source, not the same bytes. The record says so. With
  `{"path": …}`, the artifact's sha256 is checked from stage to deploy.
- **The recipe runs with the user's credentials.** Every recipe command runs as the user, with
  the user's environment and credentials, and reaches whatever it targets. The recipe is
  pinned by digest from prep to deploy, and a recipe change makes every step refuse or ask.
- **Production checks are only as good as `prod_smoke`.** verify-prod proves the probe reports
  the release and the read-only smoke checks pass; it cannot see what the smoke checks do not
  check.
- **Nothing triggers `stage` automatically.** After the human merges the release PR, a session
  has to run `stage`.
- **Grant edges.** Every release gate names the release grant by its path (with the recipe as
  subject and the stage or prep worktree), so another live grant cannot answer for it. A grant
  revoked and a new one written in the same second can still tie where a gate selects by
  subject, and that gate then answers ASK revoked. `revoke-grant` with no id (the kill
  switch) revokes every live grant, the release grant included: every gated step (prep's and
  stage's) stops at its next gate, and deploy and rollback refuse `grant-revoked` before any
  wait. verify-prod runs only the read-only probe, health and smoke checks, so it still runs.
  A release stopped this way ends with abandon. The release grant is revoked when the
  release ends: `verified`, `rolled_back`, `stage_failed`, `abandoned`, or a prep that failed
  and was undone.
- **Output stays local.** Command output tails can carry tokens, so they stay in the release's
  git-ignored `state.json`; the envelope, the release PR and the changelog carry exit codes and
  neutralised merge subjects only.

## Contract

This skill follows [skill-contract v1](https://github.com/dhanesh/agent-skills/blob/main/docs/skill-contract/SPEC.md).
It consumes an `https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1`: the
release grant prep writes with the human's acceptance, and, for release defaults only, a
planning grant from spec-first-planning. It provides a
`https://github.com/dhanesh/agent-skills/skill-contract/release-result/v1` envelope, written by
verify-prod (outcome `verified`) or rollback (outcome `rolled_back`); the payload schema is
`assets/schemas/release-result.v1.json`. Its subjects pin the recipe, the release intent and
the release log by digest, and it carries exit codes only, never command output.

```json skill-contract
{"provides": ["https://github.com/dhanesh/agent-skills/skill-contract/release-result/v1"], "consumes": ["https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1"]}
```

## Boundaries

- **Not the planner or the builder.** spec-first-planning writes specs, plans and grants;
  factory-conductor builds a plan to a PR.
- **Not the verify skill's author.** verification-skill-forge writes and maintains the verify
  skill whose evidence staging needs.
- **Not a deploy platform.** The recipe holds the project's own commands; the skill knows no
  platform.
- **Not a merger.** You MUST leave merging the release PR to the human, because the merge is
  the checkpoint before anything reaches staging and no grant can cover `merge`.
- **Not a publisher.** A GitHub Release or an announcement is an external message: it is left
  to the human.
