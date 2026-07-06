# Claude Code primitives — native loop features mapped to the LSC spec

When the target runtime is Claude Code, prefer its native loop primitives over a hand-rolled harness — each one already implements part of the 10-slot spec ([`checklist.md`](./checklist.md)). But each also *leaves slots open*, and shipping a primitive without filling its open slots recreates the classic failures. This file says, per primitive: which slots it covers, and which you must still fill.

Grounding: the Claude Code team's own loop taxonomy ("Getting started with loops", @ClaudeDevs / Delba Oliveira, July 2026) and the official docs pages for [`/goal`](https://code.claude.com/docs/en/goal), [scheduled tasks](https://code.claude.com/docs/en/scheduled-tasks), and the [agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop).

## The trigger axis (orthogonal to loop families)

The Claude Code team classifies loops by **how they're triggered and how they stop** — an axis orthogonal to this skill's families ([`families.md`](./families.md) classifies by *who drives whom*). Use both: the family fills the slots; the trigger picks the primitive.

| Loop type | Triggered by | Stops when | Primitive | Best for |
|---|---|---|---|---|
| **Turn-based** (the agentic loop) | a user prompt | Claude judges the task done | none — every prompt already is one | shorter, one-off tasks |
| **Goal-based** | a prompt with a completion condition | separate evaluator confirms the condition (or a turn clause trips) | `/goal` | tasks with verifiable exit criteria |
| **Time-based** | a time interval | you cancel it, or the watched work completes (PR merged, queue empty) | `/loop` (session), `/schedule` → Routines (cloud) | recurring work; polling external systems |
| **Proactive** | an event or schedule, no human in real time | each task exits on its goal; the routine runs until turned off | composition (see below) | recurring streams of well-defined work: triage, migrations, dependency upgrades |

Start at the top: **most tasks don't need more than the turn-based loop** — the highest-leverage upgrade there is not a loop but better verification (encode your manual checks as a skill with quantitative, tool-backed checks; the more quantitative, the easier self-verification gets — that's LSC-5 external leverage, not a new trigger). Escalate down the table only when the task demonstrably needs it. This is the same "does this even need a loop?" test as step 1 of the skill.

## Per-primitive slot coverage

### `/goal` — condition-driven (goal-based)

- **Covers LSC-1/LSC-2:** the condition *is* the success definition and stop condition; the goal clears itself when met.
- **Covers LSC-5 (partially):** after each turn a *separate* small model (Haiku by default) judges the condition — a genuine generator≠judge evaluator, exactly the separate-evaluator rule in [`spec.md`](./spec.md) LSC-5. **Caveat: the evaluator is transcript-only.** It runs no tools and reads no files; it can only judge what the working model has surfaced in the conversation. So a condition without a *stated check* degrades into trusting the generator's own claims (the optimistic-stop failure). Write the condition with one measurable end state + the check that proves it ("all tests in `test/auth` pass — `npm test` exits 0") + constraints that must hold ("no other test file modified"). Deterministic criteria (tests passed, score threshold, empty queue) work best.
- **Does NOT cover LSC-3.** `/goal` runs until the condition is met or you `/goal clear`. The docs' suggested bound — "or stop after 5 tries" *inside the condition* — is judged by the evaluator, i.e. it's a model-evaluated LSC-2 soft stop, **not** a harness-level backstop. Fill LSC-3 yourself: interactively, monitor bare `/goal` (shows turns + token spend) and set the turn clause anyway; headless/SDK, pair with `max_turns` / `max_budget_usd`, which hard-stop with `error_max_turns` / `error_max_budget_usd`.
- Mechanically it's a session-scoped prompt-based **Stop hook** — the same mechanism as the deterministic verify→repair Stop hook (the Honk pattern, `spec.md` LSC-5). A script Stop hook is the stronger form when the check is fully deterministic.

### `/loop` + cron tools — interval-driven (time-based)

- **Covers LSC-9 cadence:** fixed interval (`/loop 5m …`) or self-paced (omit the interval; Claude picks 1 min–1 h per iteration and prints why). Session-scoped; `Esc` or `CronDelete` stops it.
- **Ships a real LSC-3-style backstop:** recurring tasks **auto-expire 7 days after creation** — the docs' own rationale is "bounds how long a forgotten loop can run". Cite it; don't rely on it as your *only* cap for expensive iterations.
- **Cheaper than polling:** for "watch this until it changes", the **Monitor tool** streams a background script's output instead of re-running a prompt on an interval — often more token-efficient and more responsive (LSC-9).
- `loop.md` (project or user level) replaces the bare-`/loop` maintenance prompt — treat it as trusted-control-channel content (LSC-7): it's author-written scaffold.

### `/schedule` → Routines — durable cloud scheduling (time-based / proactive)

- Runs on Anthropic-managed infra, machine off, minimum interval 1 h, fresh clone per run.
- **Removes LSC-8 by default:** Routines run autonomously with **no permission prompts**. Moving a loop from `/loop` to a Routine silently deletes its implicit human gate — design the gate back in: have the routine end at a *reviewable artifact* (a PR, a draft, a report) rather than an irreversible action (push to main, send, delete), and re-run the lethal-trifecta check (`spec.md` LSC-7) since there's no human watching.

### Composition — the proactive stack

The article's worked example for unattended streams of work (incoming bug reports):

1. `/schedule` — a Routine checks for new reports on a cadence (trigger)
2. `/goal` — defines what "handled" means; a skill documents how to verify it (LSC-1/2/5)
3. Dynamic workflows (`Workflow`) — orchestrate agents that triage, fix, and review each report (multi-agent family)
4. Auto mode — approves tool calls so the routine never stalls on a prompt

**Composition caveat:** auto mode + Routines removes *both* human touchpoints (per-tool and per-run). LSC-8 must then be explicit and structural — gate on the deliverable (PR needing review, not a direct push), scope tools to least privilege (LSC-6), and keep the LSC-3 caps per task, because there is nobody left to notice a runaway.

## Quality & cost practices (article → slot)

| Practice | Slot | Rule |
|---|---|---|
| Fresh-context reviewer | LSC-5 | "A reviewer with fresh context is less biased and not influenced by the main agent's reasoning" — the separate-evaluator rule as official guidance; use a second agent (`/code-review`) rather than the generator's self-check |
| Verification as a skill | LSC-5 | encode manual checks as a SKILL.md with tools/connectors so the loop can see and measure its result; prefer quantitative checks |
| Encode the fix into the system | LSC-10 / audit | when one iteration's output fails the bar, don't stop at fixing the instance — fold the lesson into the skill/verifier so every future iteration inherits it |
| Pilot before a large run | LSC-9 | dynamic workflows can spawn hundreds of agents; gauge token usage on a small slice first, then scale |
| Scripts for deterministic work | LSC-9 / LSC-6 | a shipped script the loop runs each round is cheaper and more reliable than re-deriving the steps by reasoning |
| Match interval to change rate | LSC-9 | don't run a routine more often than the watched thing changes — this composes with the cache-TTL pacing rule (poll <270 s or idle 1200 s+, not exactly 300 s) |
| Right model per stage | LSC-9 | route routine stages to smaller/faster models; reserve the most capable model for judgment calls (the generate→evaluate→repair cost win) |
| Observability | LSC-9 | `/usage` breaks down spend by skills/subagents/MCPs; bare `/goal` shows turns + tokens on the active goal; `/workflows` shows per-agent spend and lets you stop an agent |

## Sources

- "Getting started with loops" — @ClaudeDevs (Delba Oliveira), July 2026: <https://x.com/ClaudeDevs/status/2074208949205881033>
- `/goal`: <https://code.claude.com/docs/en/goal> · scheduled tasks & `/loop`: <https://code.claude.com/docs/en/scheduled-tasks> · agent loop & SDK caps: <https://code.claude.com/docs/en/agent-sdk/agent-loop>
