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

---

# Tier 2 — the Operate rails (merge → production)

R1…R6 get an agent to a **verified, reviewable PR**. That is the whole job when a human merges and a normal release process takes over — so for a repo just onboarding agents to open PRs, **Tier 1 is the entire audit and you stop there.** Score Tier 2 **only when the goal is agents running unattended against a production system** (Honk's actual operating mode): the four rails below govern the surface *past* the merge boundary, where a wrong autonomous change reaches users and no reviewer is watching each one.

These rails are scored the same **0 / 1 / 2** and reported as a **separate subtotal (0–8)** — do not fold them into the Tier-1 total, because a repo can be perfectly agent-ready for PR work (Tier 1 = 12) and completely unsafe to run unattended (Tier 2 = 0). The Tier-2 grounding (`grounding.md` Tier 2) leans more on established operational-safety and security practice than on direct Honk quotes; weight it accordingly.

## R7 — Runtime observability & audit trail

**What it is.** Every agent run and action is logged, attributable, and replayable: you can reconstruct *what* an autonomous change did, *when*, on *whose* authority, and *why*. R6's PR is the audit artifact for the diff; R7 is the audit trail for the *run* behind it. Without it, a bad unattended change is an incident you cannot diagnose.

**Probe.** Are agent runs traced end-to-end (structured logs keyed by a run ID, retained tool-call records)? Is each agent-authored change attributable to a **distinct agent identity** (bot account, signed commits, a `Co-Authored-By`/trailer, labels) rather than indistinguishable from a human's? Are the driving prompts/decisions retained for replay? Is there alerting on anomalous agent behavior?

| Score | Signal |
|---|---|
| 0 | agent actions indistinguishable from a human's; no run logs; nothing to replay |
| 1 | partial trail (e.g. PRs tagged bot-authored) but runs aren't traceable end-to-end, or logs are ephemeral |
| 2 | structured, retained, queryable audit trail keyed by run ID; distinct agent identity; decisions replayable |

**Fix.** Give the agent a distinct identity (bot account / signed commits / a commit trailer); emit structured run logs with a run ID; retain prompts + tool calls for replay; alert on anomalies. This is the auditability precondition for trusting anything downstream.

## R8 — Blast-radius containment

**What it is.** The environment the agent *runs in* bounds the damage a wrong or compromised run can do — ephemeral sandboxes, least-privilege short-lived credentials, an egress allowlist, and prod-data segregation. R5 scopes the *tools*; R8 scopes the *environment those tools execute in*. A capable agent on a long-lived host with standing prod access is a bounded-only-by-luck blast radius.

**Probe.** Does the agent run in an ephemeral, isolated environment (fresh container/clone) rather than a standing host with persistent access? Is network egress restricted to an allowlist? Are prod credentials/data absent from the agent's environment (or read-only / synthetic)? Is the worst-case blast radius of a single run bounded and written down?

| Score | Signal |
|---|---|
| 0 | agent runs on a host with standing prod access + open network; a bad run can reach anything |
| 1 | some isolation (a container) but prod creds or open egress remain; blast radius not bounded |
| 2 | ephemeral least-privilege env, egress allowlisted, no standing prod data/creds; worst-case blast radius bounded and documented |

**Fix.** Run agents in ephemeral sandboxes with least-privilege, short-lived credentials; restrict egress to an allowlist; keep prod data out (synthetic or read-only replicas); document the worst-case blast radius so it is a known quantity, not a surprise. This is the containment half of excessive-agency avoidance — R5 narrows *what* it can call, R8 narrows *what that call can reach*.

## R9 — Deploy-path safety & kill switch

**What it is.** Agent changes reach production through a **staged, reversible, and haltable** path: feature-flag / canary / staged rollout with automated rollback on regression, a one-action revert of a shipped change, enforced spend/rate/concurrency caps, and an emergency stop that halts the whole agent fleet. R6's reversibility ends at merge; R9 governs merge → prod and the ability to stop *everything* at once.

**Probe.** Do agent-authored changes deploy via staged/canary rollout with automated rollback on SLO regression? Is rollback of a shipped change a single known action? Are there enforced spend/rate/concurrency limits on the agent program? Is there a documented, *tested* kill switch that halts all agents immediately?

| Score | Signal |
|---|---|
| 0 | merges deploy straight to prod; no canary, no rollback story, no caps, no stop |
| 1 | some staging or manual rollback, but no automated regression rollback, or no fleet-wide kill switch / caps |
| 2 | staged rollout with automated rollback, one-action revert, enforced spend/rate/concurrency caps, and a tested kill switch |

**Fix.** Gate agent-authored deploys behind feature flags + canary with automated rollback on SLO regression; make rollback one action; enforce spend/rate/concurrency caps; wire *and test* a fleet-wide kill switch. The caps and stop are the operational analog of the loop's hard-stop backstop (`crafting-self-prompting-loops`) lifted from one loop to the whole program.

## R10 — Continuous re-verification & program telemetry

**What it is.** Readiness is **monitored over time**, not assumed from a one-time audit. Two things drift: the rails themselves rot (a required check gets disabled, the architecture map goes stale, a token scope quietly widens), and agent-program quality moves (PR acceptance rate, revert/rollback rate, time-to-green, cost per merged change). R10 re-checks both and pauses autonomy when they regress — it is R1/R2's external-verification discipline lifted from the *single change* to the *whole program*.

**Probe.** Is repo readiness (especially the load-bearing R1/R2) re-verified on a schedule or in CI, so a disabled check / drifted map / widened scope is caught automatically? Are agent-program metrics tracked (merge acceptance, revert rate, MTTR-to-green, cost/change)? Is there a threshold that **pauses autonomy** when quality regresses, rather than waiting for an incident?

| Score | Signal |
|---|---|
| 0 | audit is one-time; no ongoing rail checks; no program metrics; regressions surface only as incidents |
| 1 | some metrics or periodic checks exist, but no automated pause-on-regression, or rail drift goes uncaught |
| 2 | rails continuously re-verified; program-quality metrics tracked against thresholds; autonomy auto-pauses when they regress |

**Fix.** Put the rail checks in CI or a scheduled job (does `verify` still run? is CI still required-to-merge? did tool scope widen?); track acceptance / revert / time-to-green per agent; set a regression threshold that pauses autonomy and pages a human. The sibling `context-hygiene-kit` and `world-model-ledger` install *continuous* in-session mechanisms — R10 asks for the same continuity around the readiness rails themselves.
</content>
</invoke>
