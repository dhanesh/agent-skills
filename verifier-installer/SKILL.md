---
name: verifier-installer
description: >-
  Installs the runnable-verifier loop — format/build/test commands plus a CI workflow that runs
  them — into a repo that lacks it, turning agent-ready-rails' top finding (weak R1/R2 rails)
  into working files. Use when someone says "this repo has no tests or CI", "set up the verify
  loop", "add CI that runs the checks", "make this repo agent-ready", or after an
  agent-ready-rails audit scores runnable verifiers or green-CI low. Detects the stack (python,
  node, go, rust, make) with a shipped script, confirms a plan, writes the verifier entrypoints
  and a GitHub Actions workflow, then proves the loop by demonstrating a red run and repairing it
  to green. Not a linter-config generator for style debates, and not for repos that already have
  a green verify+CI loop — use agent-ready-rails to audit first; this skill installs what that
  audit finds missing.
license: MIT
compatibility: Requires python3 and a POSIX shell. The target repo's own toolchain (npm, go, cargo, make, pytest) is needed only to run the verifiers it already implies; detection itself is offline and stdlib-only.
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "verifiers,ci,github-actions,agent-readiness,test-loop,scaffolding"
---

# verifier-installer

Stand up the **verify→repair loop** in a repository that doesn't have one: a
single discoverable command per rail — format, build, test — and a CI workflow
that runs the same commands. This is the action-taking sibling of
`agent-ready-rails`: rails *audits* and reports that the loop is missing (its
load-bearing R1/R2 finding); this skill *installs* it. The evidence behind why
this rail matters most is in that skill's grounding — a verify loop the agent
cannot skip is the single highest-leverage change for agent success.

## When to use

Reach for this when a repo needs the loop built: no test command, no CI, an
undocumented build, or an agent-ready-rails scorecard with R1/R2 at 0–1. Do
not use it to referee style — installing a formatter *check* is in scope,
choosing tabs-vs-spaces is not (prefer whatever the repo already leans
toward). Do not use it on a repo whose verify+CI loop is already green;
`agent-ready-rails` will say so, and re-installing over a working loop only
adds noise. When only *part* of the loop is missing (say, tests exist but CI
doesn't), install just the missing rails.

## What gets installed

Per the confirmed plan, some subset of:

| Rail | Typical form | Exercised by |
|------|--------------|--------------|
| format | formatter/lint check command or script | e.g. `npm run format`, `gofmt -l .` |
| build | compile/typecheck command | e.g. `npm run build`, `go build ./...` |
| test | test-runner command (+ a smoke test if none exists) | e.g. `python3 -m pytest`, `npm test` |
| ci | one workflow running the rails above | `.github/workflows/verify.yml` on push/PR |

Exact per-stack files, commands, and snippets live in
`references/install-playbooks.md` — load the section for the detected stack
rather than inventing config from memory.

## Workflow

1. **Detect the stack.** Run `python3 assets/detect_stack.py <repo>` (offline,
   read-only). It emits a deterministic JSON plan: detected `stacks`,
   `existing_verifiers` (format/build/test/ci with the command or workflow
   path found), `missing` rails, concrete `proposals`
   (`rail`/`command`/`file_to_create`), plus `lockfiles` and any `errors`
   (e.g. malformed `package.json` — reported, never fatal). Read the plan;
   spot-check anything surprising against the repo itself.
2. **Confirm the plan with the user.** Show which rails exist, which are
   missing, and what you propose to create. Ask which verifiers they want and
   which CI provider — GitHub Actions is the default; if they name another,
   translate the workflow per the CI section of
   `references/install-playbooks.md`. If the plan lists errors (broken
   manifest files), surface them here — fixing a corrupt `package.json` is a
   decision the user makes, not you. Do not write anything before this
   confirmation.
3. **Install per the playbook.** For each approved missing rail, follow the
   matching stack section in `references/install-playbooks.md`: write the
   verifier config/scripts (Makefile targets, package scripts, smoke test)
   and the CI workflow. Extend existing files rather than replacing them, and
   never add a second workflow when one already exists — extend the existing
   one. Keep the diff small and reviewable.
4. **Prove the loop (verify and repair).** For each installed rail, run its
   command and record the result. Then follow the prove-the-loop protocol in
   `references/install-playbooks.md`: introduce one trivial, reversible break,
   watch the verifier go red, revert, and watch it return green. If a rail
   fails for a pre-existing reason, or a toolchain isn't available to run it,
   repair what the install broke, report what it didn't, and mark that rail
   installed-but-unproven rather than claiming it works.
5. **Hand back the install summary.** Deliver the summary below: each rail
   installed, the exact command that exercises it, the red→green proof (or the
   honest "unproven" tag), and the recommended follow-ups you deliberately did
   not do yourself (branch protection, replacing placeholder targets).

## Deliverable — the install summary

ALWAYS end with this report:

```
## Verifier loop installed: <repo>

| Rail | Command | File(s) | Proven |
|------|---------|---------|--------|
| format | <cmd> | <path> | red→green demonstrated / unproven (<why>) |
| build  | ... | ... | ... |
| test   | ... | ... | ... |
| ci     | push/PR triggers <workflow path> | ... | ... |

Skipped (already present): <rails the plan found existing, with their commands>
Follow-ups for the owner: <branch protection, placeholder targets to fill, …>
```

## Guardrails

- **Read-only until step 2's confirmation** — detection never writes; installs
  happen only after the user approves the plan.
- **One ground truth.** Local `verify` and CI run the same commands; when in
  doubt, make CI call the entrypoint rather than restating commands.
- **Prove, don't presume.** A rail counts as installed when it was watched
  failing and recovering, not when its file exists. Leave the tree clean after
  the demonstration.
- **Stay off the style battlefield.** Wire checks for whatever
  formatter/tooling the repo already implies; propose, never impose, new
  tools.
