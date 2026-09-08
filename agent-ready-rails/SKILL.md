---
name: agent-ready-rails
description: 'Read-only audit that scores how ready a repository is for coding agents to work in safely and successfully, then optionally installs the missing rails. Use when asked "is our repo ready for agents / Claude Code / a background coding agent?", "why do agents keep failing or making messy PRs here?", or "how do we get agents to actually merge working changes?". Grades six rails — runnable verifiers (the format/build/test loop), green-CI ground truth, copyable house style, navigable context, scoped trusted tools, human checkpoints with reversibility — from observed repo evidence, emitting a severity-ranked scorecard with a prioritized fix list. For agents running unattended against production, an optional second tier grades four operate rails past the merge boundary. Grounded in Spotify''s Honk case. Not a linter, SAST, or CI-config generator: audits the engineering system agents run inside, not code correctness (base-in-reality) or one loop''s soundness (crafting-self-prompting-loops).'
license: MIT
compatibility: Any agent harness with shell access to the target repo; the evidence collector needs python3 (stdlib only). Audit mode is read-only.
metadata:
  author: dhanesh
  version: "1.1.0"
  tags: "agent-readiness,audit,coding-agents,ci,verification,repo-hygiene"
---

# agent-ready-rails

A read-only audit of whether a codebase is **ready for coding agents** — and a scaffolder for the rails it is missing.

The thesis, from the best-documented production case (Spotify's Honk agent over ~20M lines; see `references/grounding.md`): **a powerful agent in a weak engineering system creates faster chaos; the same agent in a disciplined system creates leverage.** The differential is not model capability — it is the *rails* around the agent. This skill scores those rails and tells you which to build first.

Default mode is **read-only**: observe, score, report. It only writes files when explicitly asked to install rails (step 5).

**Locating this skill's helpers (do this first).** The steps below run bundled
scripts. You execute from the *target repo*, not from this skill's directory, so a
path written relative to this skill will not resolve. Resolve the base directory once and use it
everywhere — including in any subagent prompt, which must receive the literal absolute
path, never a relative form:

```sh
SKILL_DIR="<this skill's base directory>"   # your harness provides it when the skill loads
# If you don't have it, discover it:
SKILL_DIR=$(find ~/.claude ~/.config ~/.agents -type d -name 'agent-ready-rails' 2>/dev/null | head -1)
test -d "$SKILL_DIR/assets" || test -d "$SKILL_DIR/scripts"   # verify before proceeding
```

## When to use

Reach for this when the question is about the *environment* agents work in, not a specific change: "is our repo agent-ready?", "why do agents keep producing broken or messy PRs here?", "what do we fix so a background agent can merge verified work unattended?". It complements the sibling skills rather than overlapping them — see the relationship table in `references/grounding.md`. Do **not** use it to audit code correctness (that's `base-in-reality`), to judge the quality of the code or its design (that's `clean-code`), or to design/repair one agent loop (that's `crafting-self-prompting-loops`); this skill audits the *system* those run inside.

## The rails — two tiers

Full probe/score/fix detail is in `references/rubric.md`; the evidence for each is in `references/grounding.md`. The rails split into two tiers by boundary:

**Tier 1 — Build rails (author → merge).** Always audited. These decide whether an agent can produce a *verified, reviewable PR* at all.

| ID | Rail | One-line test | Load-bearing? |
|----|------|---------------|---------------|
| R1 | Runnable verifiers | one discoverable command runs format+lint+build+test | **yes** — it *is* the loop |
| R2 | Green-CI ground truth | CI runs those checks, gates merge, is reproducible | **yes** |
| R3 | House style to copy | enforced formatter/linter + conventions doc + idiomatic exemplars |  |
| R4 | Navigable context | a current map + responsibility-named boundaries |  |
| R5 | Scoped trusted tools | least-privilege tools, gated destructive actions, trifecta broken |  |
| R6 | Human checkpoints | PR + meaningful review gate + small reversible diffs + cheap rollback |  |

**Tier 2 — Operate rails (merge → production).** Audited **only when the goal is agents running unattended against a production system** (see step 2). For a repo just onboarding agents to open PRs, Tier 1 is the whole audit.

| ID | Rail | One-line test |
|----|------|---------------|
| R7 | Runtime observability & audit | agent runs traced + attributable to a distinct identity + replayable |
| R8 | Blast-radius containment | ephemeral least-privilege env, egress allowlisted, no standing prod creds/data |
| R9 | Deploy-path safety & kill switch | staged rollout + automated rollback + spend/rate caps + tested fleet stop |
| R10 | Continuous re-verification | rails re-checked over time + program metrics + autonomy pauses on regression |

Each rail scores **0 (absent) / 1 (partial) / 2 (agent-grade)**. Report the two tiers as **separate subtotals** — Tier 1 is 0–12, Tier 2 is 0–8 — and never fold them together: a repo can be perfectly ready for PR work (Tier 1 = 12) and unsafe to run unattended (Tier 2 = 0). Within Tier 1 the **minimum rail matters more than the sum** — R1/R2 are load-bearing, so a repo strong everywhere except the verify→repair loop is still not agent-ready. Lead the report with the weakest rail in whichever tier is in scope.

## Workflow

Follow these steps in order. Steps 1–4 are read-only and always run; step 5 writes only on explicit request; step 6 verifies the writes.

### 1. Scope and detect the stack

Establish the languages, build system, and where an agent would look for instructions (`README`, `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, task runner). State what you found in one line. If the repo is a monorepo, pick the target package(s) and say so. **Decide the tier in scope:** if the user's goal is agents opening PRs a human reviews, audit **Tier 1 only**; if they want agents running *unattended against production*, audit **both tiers**. When it's ambiguous, default to Tier 1 and say you can add the Operate tier if they're heading for unattended operation.

### 2. Probe each rail from observed evidence

Start with the shipped collector, then deepen by reading — the collector supplies evidence, you supply judgment:

```bash
python3 "$SKILL_DIR/assets/collect_evidence.py" <repo-dir>
```

It walks the target repo read-only and emits sorted JSON evidence per Tier-1 rail (`R-verifiers`, `R-ci`, `R-house-style`, `R-context`, `R-scoped-tools`, `R-checkpoints` — mapping to R1–R6), each entry a `{kind, path, detail}` lead: test/lint/build configs and Make targets, CI workflows (flagging whether each actually runs tests), style docs and EditorConfig, CLAUDE.md/AGENTS.md/README/docs structure, `.claude/settings*.json` permissions and MCP config, CODEOWNERS/PR-template/.gitignore hygiene. It collects and flags only — it never scores; malformed config files surface as `settings-error` entries rather than crashes.

Then, for each rail in scope, verify and extend the collector's leads against the concrete signals named in `references/rubric.md`: read the cited configs and CI files, check that named commands actually exist, and hunt for evidence the collector's fixed patterns cannot see (a bespoke task runner, a wiki style guide). For Tier 2, the evidence lives as much in the *agent harness and deploy/runtime config* (CI/CD workflows, sandbox/container setup, feature-flag and rollout config, bot identity, logging/alerting) as in the repo source — probe there too. **Run nothing destructive** — confirm a verifier or control *exists* and is wired, don't execute migrations, deploys, or a kill switch. Any rail you cannot back with a file/line is scored as absent and tagged `UNVERIFIED`; never credit a rail on a README claim alone.

### 3. Score against the rubric

Assign each rail 0/1/2 strictly from step-2 evidence, citing the path (and line where useful) that justifies the score. When evidence is mixed, score to the weakest blocking signal, not the average — a build command that needs an unobtainable secret to run is a 1, not a 2. Keep the two tiers' subtotals separate; do not average a strong Tier 1 against a weak Tier 2.

### 4. Emit the readiness scorecard

Produce the deliverable below: the per-rail scores, the load-bearing minimum called out first, and a **prioritized fix list** ordered by leverage (load-bearing rails first, then lowest-scoring). Include the Tier-2 block only when the Operate tier was in scope. Each fix is concrete and tied to the rail's `Fix` guidance. This is the default output — stop here unless asked to build.

### 5. (Optional) Install the rails — only on request

If the user asks you to *fix* or *set up* the rails, implement the prioritized fixes: e.g. add a single `verify` entrypoint (R1), wire CI to it (R2), write a conventions doc + exemplars (R3), add an architecture map (R4). Tier-2 fixes (R7–R10: agent identity + run logging, a sandboxed runtime, a rollout/rollback + kill-switch path, scheduled rail re-verification) usually touch CI/CD and infra config rather than repo source — propose them, but implement only what the repo actually owns. Keep each change small and reviewable, and route it through the repo's own PR flow. Defer R5 and R9's kill-switch loop-design specifics to `crafting-self-prompting-loops` rather than reinventing them.

### 6. Re-verify what you installed

After any install, re-run the relevant probes and confirm the rail now scores higher — and that the new `verify` entrypoint actually runs and gates. Report the before/after scores. Do not claim a rail is fixed without re-checking it.

## Output template

ALWAYS structure the scorecard like this:

```
## Agent-readiness: <repo> — Build <t1>/12  (weakest: R<n> <rail>)

| Rail | Score | Evidence | Gap |
|------|-------|----------|-----|
| R1 Runnable verifiers | 0/1/2 | <path:line> | <one line> |
| R2 Green-CI ground truth | … | … | … |
| R3 House style | … | … | … |
| R4 Navigable context | … | … | … |
| R5 Scoped trusted tools | … | … | … |
| R6 Human checkpoints | … | … | … |

<!-- Include this block ONLY when the Operate tier was in scope (unattended/production goal) -->
### Operate readiness — <t2>/8  (weakest: R<n> <rail>)

| Rail | Score | Evidence | Gap |
|------|-------|----------|-----|
| R7 Runtime observability & audit | 0/1/2 | <path:line> | <one line> |
| R8 Blast-radius containment | … | … | … |
| R9 Deploy-path safety & kill switch | … | … | … |
| R10 Continuous re-verification | … | … | … |

### Verdict
<one line: is this repo agent-ready? load-bearing R1/R2 decides Build; if Operate is in scope, say plainly whether it is safe to run unattended>

### Prioritized fixes (highest leverage first)
1. <rail> — <concrete fix> — <why it moves the needle>
2. …

### Unverified
<rails claimed in docs but not backed by observable evidence>
```

## Why this matters

The instinct when agents underperform in a codebase is to reach for a smarter model or a cleverer prompt. The Honk evidence says the durable wins came from the engineering system instead: a verify→repair loop the agent can't stop short of (R1) took success from ~20–30% to ~80% with no model change. This skill exists to find which rail is missing *before* you spend that effort in the wrong place — and to make the fix concrete rather than aspirational. Hold the line on the read-only default: score honestly from evidence, and let the scorecard, not a hunch, drive what gets built.
</content>
