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
  audit finds missing. Honors a skill-contract autonomy grant at its write gate.
license: MIT
compatibility: Requires python3 and a POSIX shell. The target repo's own toolchain (npm, go, cargo, make, pytest) is needed only to run the verifiers it already implies; detection itself is offline and stdlib-only.
metadata:
  author: dhanesh
  version: "1.2.2"
  skill-contract: "1"
  tags: "verifiers,ci,github-actions,agent-readiness,test-loop,scaffolding"
---

# verifier-installer

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY in this skill are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and only when, they appear in all capitals.

Stand up the **verify→repair loop** in a repository that doesn't have one: a
single discoverable command per rail — format, build, test — and a CI workflow
that runs the same commands. This is the action-taking sibling of
`agent-ready-rails`: rails *audits* and reports that the loop is missing (its
load-bearing R1/R2 finding); this skill *installs* it. The evidence behind why
this rail matters most is in that skill's grounding — a verify loop the agent
cannot skip is the single highest-leverage change for agent success.

**Locating this skill's helpers (do this first).** The steps below run bundled
scripts. You execute from the *target repo*, not from this skill's directory, so a
path written relative to this skill will not resolve. Resolve the base directory once and use it
everywhere — including in any subagent prompt, which MUST receive the literal absolute
path:

```sh
SKILL_DIR="<this skill's base directory>"   # your harness provides it when the skill loads
# If you don't have it, discover it:
SKILL_DIR=$(find ~/.claude ~/.config ~/.agents -type d -name 'verifier-installer' 2>/dev/null | head -1)
test -d "$SKILL_DIR/assets" || test -d "$SKILL_DIR/scripts"   # verify before proceeding
```

## When to use

Reach for this when a repo needs the loop built: no test command, no CI, an
undocumented build, or an agent-ready-rails scorecard with R1/R2 at 0–1. Do
not use it to referee style — installing a formatter *check* is in scope
when the repo already adopts a formatter, choosing tabs-vs-spaces is not
(prefer whatever the repo already leans toward). When no formatter is
adopted, this skill does not invent a style opinion: the format rail becomes
an honest placeholder that stays red until the owner picks one. Do not use
it on a repo whose verify+CI loop is already green;
`agent-ready-rails` will say so, and re-installing over a working loop only
adds noise. When only *part* of the loop is missing (say, tests exist but CI
doesn't), install just the missing rails.

## What gets installed

Per the confirmed plan, some subset of:

| Rail | Typical form | Exercised by |
|------|--------------|--------------|
| format | formatter/lint check command or script (only when the repo already adopts one; otherwise an honest placeholder) | e.g. `npm run format`, `ruff format --check .`, `test -z "$(gofmt -l .)"` |
| build | compile/typecheck command | e.g. `npm run build`, `go build ./...` |
| test | test-runner command (+ a smoke test if none exists) | e.g. `python3 -m pytest`, `npm test` |
| ci | one workflow running the rails above | `.github/workflows/verify.yml` on push/PR |

Exact per-stack files, commands, and snippets live in
`references/install-playbooks.md` — load the section for the detected stack
rather than inventing config from memory.

## Workflow

1. **Detect the stack.** Run `python3 "$SKILL_DIR/assets/detect_stack.py" <repo>` (offline,
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
   decision the user makes, not you. You MUST NOT write anything before this
   confirmation, because installing rails changes the user's repo and CI, unless
   `python3 "$SKILL_DIR/assets/contract_check.py" check-grant --root <repo> --action local_reversible`
   exits 0 (an autonomy grant the user approved covers it); then you MAY proceed, and MUST name
   the grant id and action class in the report. Any other exit (3 ASK/NONE, 2 INVALID, 1 usage
   error) means ask as usual. Under a grant you MUST install only the plan's proposals for the missing rails,
   with GitHub Actions as the CI provider, and MUST report manifest errors (a corrupt
   `package.json`, say) for the user instead of fixing them. The grant lifts this confirmation
   and nothing more: the playbook's prove-the-loop rule to stop and ask before fixing pre-existing debt still applies.
   The workflow you write under a grant stays local; pushing it asks, because CI runs with the
   repository's secrets (`check-grant` answers ASK `ci-config` for that push).
3. **Install per the playbook.** For each approved missing rail, follow the
   matching stack section in `references/install-playbooks.md`: write the
   verifier config/scripts (Makefile targets, package scripts, smoke test)
   and the CI workflow. Extend existing files rather than replacing them, and you
   MUST NOT add a second workflow when one already exists, because two workflows drift apart and CI
   stops being one ground truth; you MUST extend the existing one. Keep the diff small and reviewable.
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

Step 5 hands back this report:

```
## Verifier loop installed: <repo>
Gate: confirmed by user | grant <id> (<class>)

| Rail | Command | File(s) | Proven |
|------|---------|---------|--------|
| format | <cmd> | <path> | red→green demonstrated / unproven (<why>) |
| build  | ... | ... | ... |
| test   | ... | ... | ... |
| ci     | push/PR triggers <workflow path> | ... | ... |

Skipped (already present): <rails the plan found existing, with their commands>
Follow-ups for the owner: <branch protection, wire a real formatter — `make verify`
  and CI stay red on the format step until then, …>
```

`detect_stack.py`'s plan marks this mechanically: every proposal carries a `placeholder`
field, `true` for a fallback (the `make format`/`make build`/`make test` skeleton, or a
python format rail with no adopted formatter) and `false` for a real, stack-specific
command. Read that field rather than guessing. When a rail's proposal was `placeholder:
true` — most commonly `format` on a python repo with no adopted formatter — it was never
watched go red and back to green; report it as `unproven (no formatter adopted)`, not
`red→green demonstrated`, and say plainly that `make verify` and CI stay red on that step
until the owner wires one. That is not the same as a rail you proved catches a violation,
and reporting it as demonstrated is a false claim the next agent will trust — put wiring
it in "Follow-ups for the owner" instead.

The loop now runs, but its `test` rail proves only the smoke test this skill wrote — it does not
mean the codebase is netted. Point the owner at `test-safety-net` to fill it with real,
change-detecting tests.

## Guardrails

- **Read-only until step 2's confirmation (or a covering grant)** — detection never writes;
  installs MUST happen only after the user approves the plan, or after `check-grant` exits 0
  for `local_reversible` as step 2 describes.
- **One ground truth.** Local `verify` and CI MUST run the same commands; have CI call
  the `verify` entrypoint rather than restate its commands, so the two cannot drift.
- **Prove, don't presume.** A rail counts as installed when it was watched
  failing and recovering, not when its file exists. You MUST leave the tree clean after
  the demonstration.
- **Stay off the style battlefield.** Wire checks for whatever
  formatter/tooling the repo already implies; you MAY propose, but SHOULD NOT impose, new
  tools.

## Contract

This skill follows [skill-contract v1](https://github.com/dhanesh/agent-skills/blob/main/docs/skill-contract/SPEC.md).
It consumes autonomy grants, which only lift step 2's confirmation, and it provides no kind of
its own: the install summary is prose.

```json skill-contract
{"provides": [], "consumes": ["https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1"]}
```
