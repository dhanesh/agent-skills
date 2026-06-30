# The six rails — what to check, how to score, how to fix

Keyed `R1…R6`. Each rail is scored **0 / 1 / 2**:

- **0 — absent:** an agent has no usable form of this rail; it must guess.
- **1 — partial:** the rail exists but is undiscoverable, manual, slow, or not enforced.
- **2 — agent-grade:** a single discoverable, machine-runnable form an agent can invoke and read unaided.

Score from **observed repo evidence only** (files, configs, CI, docs). Never infer a rail from a README claim you cannot see backing for — an unverifiable claim scores as if absent and is flagged `UNVERIFIED`. Cite the path/line that justifies each score.

The overall readiness score is the sum (0–12), but the *shape* matters more than the total: **R1 and R2 are the load-bearing rails** (the verify→repair loop), so a repo strong everywhere except R1/R2 is still not agent-ready. Report the minimum rail prominently.

---

## R1 — Runnable verifiers (the feedback loop) — load-bearing

**What it is.** A single, discoverable command for each of: format, lint, build/typecheck, test. An agent's verify→repair loop is only as good as the commands it can run and read; this is the rail that, in practice, moves agent success the most (see `grounding.md` §1).

**Probe.** Is there a `Makefile` / `package.json` scripts / `justfile` / `tox.ini` / task runner with obvious targets? Does the README or a `CLAUDE.md`/`AGENTS.md` name the exact commands? Run nothing destructive — just establish that the commands exist and are named in one place.

| Score | Signal |
|---|---|
| 0 | no discoverable build/test command; an agent would have to reverse-engineer it |
| 1 | commands exist but are scattered, undocumented, or require manual setup/secrets to run |
| 2 | one entrypoint (`make verify`, `npm test`, etc.) runs format+lint+build+test and is named where an agent will look |

**Fix.** Add a single `verify` entrypoint that chains format → lint → build → test and exits non-zero on any failure; document it in `CLAUDE.md`/`AGENTS.md`. For agent loops, wire it so the agent **cannot terminate on unverified output** (in Claude Code, a Stop hook that runs `verify`).

## R2 — Green-CI ground truth — load-bearing

**What it is.** CI is the authority on "correct," it is reproducible, and a PR cannot merge red. The agent's local verifier (R1) is the fast loop; CI is the trusted check that the change actually holds across environments.

**Probe.** Is there a CI workflow (`.github/workflows/`, etc.)? Does it run the same checks as R1? Are required status checks / branch protection implied (PR-gated merges)? Is the build reproducible (pinned deps, lockfile)?

| Score | Signal |
|---|---|
| 0 | no CI, or CI that doesn't run tests |
| 1 | CI exists but is not required to merge, is flaky, or diverges from the local verifier |
| 2 | CI runs the R1 checks, gates merge, and is reproducible (lockfile + pinned toolchain) |

**Fix.** Stand up CI that invokes the same `verify` entrypoint as R1; require it for merge; pin the toolchain and commit a lockfile so a green run means the same thing twice.

## R3 — House style the agent can copy

**What it is.** Consistent, enforced conventions and representative exemplars, so an agent that imitates nearby code lands on the house pattern instead of inventing one. Agents are strong mimics; a codebase with a coherent style turns that into leverage (see `grounding.md` §3).

**Probe.** Is there an enforced formatter + linter config (not just present, but run in R1/R2)? A `CLAUDE.md`/`AGENTS.md`/`CONTRIBUTING.md` documenting conventions? Are there clear, idiomatic exemplars for the common change types (a typical handler, test, migration)?

| Score | Signal |
|---|---|
| 0 | inconsistent style, no formatter/linter, no conventions doc |
| 1 | a formatter exists but style/architecture conventions are tribal/undocumented |
| 2 | formatter+linter enforced in CI **and** a conventions doc + idiomatic exemplars an agent can pattern-match |

**Fix.** Adopt and enforce a formatter+linter; write a short conventions doc pointing at 2–3 canonical exemplars per common change type. Keep it small and current — a stale style guide is worse than none.

## R4 — Navigable context

**What it is.** The repo is legible enough that an agent loads the *right* slice cheaply: an entrypoint/map, clear module boundaries, and docs near the code they describe. This bounds context cost and prevents the agent reasoning from the wrong files.

**Probe.** Is there a top-level map (README architecture section, `CLAUDE.md`, docs index)? Are modules/directories named by responsibility? Is there dead/duplicated code that would mislead a pattern-matcher?

| Score | Signal |
|---|---|
| 0 | flat or sprawling layout, no map, an agent must read broadly to orient |
| 1 | structure exists but no map; or a map that has drifted from the code |
| 2 | a current map + responsibility-named boundaries an agent can use to load only what it needs |

**Fix.** Add a short architecture map (where things live, where to add what) and keep it in-repo next to the code. For sessions that run long, pair with a context-management kit so the map survives compaction (see `grounding.md` §4 cross-link).

## R5 — Scoped trusted tools

**What it is.** The tools an agent is given are least-privilege and safe-by-construction, and there is a clear trusted/untrusted boundary. A capable agent on broad, unscoped tools is faster *chaos*; the same agent on a narrow, audited toolset is leverage (see `grounding.md` §5).

**Probe.** What can the agent's harness actually do (shell, network, deploy, prod data)? Are destructive actions gated? Is untrusted input (issue text, web, tool output) kept out of the privileged control path? Run the **lethal-trifecta check**: private-data access + untrusted-content exposure + external-comms simultaneously.

| Score | Signal |
|---|---|
| 0 | agent has broad/unscoped access (prod creds, arbitrary shell+network) with no gating |
| 1 | some scoping, but untrusted content can reach a privileged sink, or the trifecta is present |
| 2 | tools scoped to the task; destructive actions gated; trifecta broken; untrusted content quarantined |

**Fix.** Scope the toolset to the minimum the task needs; gate destructive/irreversible actions behind approval; break one leg of the lethal trifecta. This rail shares its design with `crafting-self-prompting-loops` LSC-6/LSC-7 — reuse that spec rather than re-deriving it.

## R6 — Human checkpoints & reversibility

**What it is.** Agent output lands through a reviewable, reversible path — PR-based merges, a review gate on consequential changes, small diffs, easy rollback — so a wrong change is caught or cheaply undone rather than silently shipped.

**Probe.** Do changes merge via PR with review? Are changes typically small/reversible? Is rollback a known, cheap operation? Is there a human gate on irreversible/external-effect actions (deploys, data migrations, posts)?

| Score | Signal |
|---|---|
| 0 | agents can push to a shared branch / deploy with no review or rollback story |
| 1 | PR flow exists but review is rubber-stamped, or rollback is manual/risky |
| 2 | PR + meaningful review gate on consequential changes, small reversible diffs, cheap rollback |

**Fix.** Route agent changes through PRs; gate consequential/irreversible actions on human approval (`crafting-self-prompting-loops` LSC-8); keep diffs small and rollback one command away. Make the review *adversarial* — show the verifier result and the diff, not just the agent's summary.
</content>
</invoke>
