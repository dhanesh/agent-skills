# Failure Modes & Cost/Cadence Reference

Companion to the loop-spec [`checklist.md`](./checklist.md) (LSC-1..LSC-10). It expands **LSC-10** (failure-mode handling) and **LSC-9** (cost & cadence). LSC IDs are a fixed contract — every mitigation below points back to the checklist item that prevents it.

Families referenced throughout: **(a) self-refinement/reflexion**, **(b) autonomous task loops**, **(c) multi-agent self-prompting**, **(d) human-checkpointed**.

---

## Part A — Failure-mode catalogue (LSC-10 / O3)

### Summary table

| # | Failure mode | Symptom (one line) | Primary LSC fix | Most prone families |
|---|---|---|---|---|
| 1 | Oscillation | Flip-flops between two states, never settles | LSC-4, LSC-5 | (a), (c) |
| 2 | Drift | Slowly wanders off the original goal | LSC-1, LSC-5 | (a), (b) |
| 3 | Premature stop | Quits before the goal is actually met | LSC-1, LSC-2 | (b), (d) |
| 4 | Runaway / non-termination | Never stops on its own | **LSC-3**, LSC-9 | (b), (c) |
| 5 | Prompt-injection via carried content | Carried output/tool/web text gets obeyed as instructions | **LSC-7**, LSC-6 | (b), (c) |
| 6 | Context rot / state bloat | Carried state grows until it confuses the loop | LSC-4, LSC-9 | (a), (b) |
| 7 | Cost blowout | Token/iteration spend explodes | **LSC-9**, LSC-3 | (b), (c) |
| 8 | Multi-agent deadlock / over-fanout | Agents wait on each other or spawn unboundedly | LSC-3, LSC-9, LSC-2 | (c) |

---

### 1. Oscillation

- **Symptom:** The loop alternates between two (or a few) states — draft A → draft B → draft A → … — or two agents reverse each other's decision each round. Progress metric plateaus while output keeps churning.
- **Cause:** No memory of *past* states (not just the last one), so the loop can't recognize it has been here before. Self-evaluation rewards "change" rather than "improvement," so undoing a prior edit reads as progress.
- **Mitigation:** Carry a short history/hash of recent states, not just the immediate prior output (**LSC-4**). Add explicit cycle detection to no-progress detection: if the current state matches a recent one, declare no-progress and stop or escalate (**LSC-5**). Require the progress metric to be monotone-or-stop, not change-rewarding.
- **Most prone:** (a) drafts ping-ponging between two phrasings; (c) two agents reversing each other.

### 2. Drift

- **Symptom:** Each round looks locally reasonable, but after many iterations the output addresses a different problem than the one stated. The loop "optimized" its way off-target.
- **Cause:** The goal (**LSC-1**) is restated, paraphrased, or re-derived from carried state each round instead of being pinned in the trusted channel. Small per-round reinterpretations compound.
- **Mitigation:** Keep `GOAL` / `SUCCESS_DEFINITION` verbatim in the fixed control channel and re-inject it unchanged every iteration (**LSC-1**, **LSC-7** trusted channel). Measure progress against the *original* goal, not the latest restatement of it (**LSC-5**). In (d), the human checkpoint is the drift catch — surface the original goal alongside current state at every gate.
- **Most prone:** (a) long refinement chains; (b) long autonomous runs.

### 3. Premature stop

- **Symptom:** The loop emits its stop signal and halts while the success definition is demonstrably unmet — partial output declared "done."
- **Cause:** Stop condition is proxied by something weaker than the goal ("no errors," "looks plausible," "ran out of obvious next steps") rather than the concrete acceptance bar. Self-evaluation is optimistic.
- **Mitigation:** Bind `STOP_CONDITION` directly to `SUCCESS_DEFINITION` — stop only when the checkable "done" condition holds (**LSC-2** ← **LSC-1**). Add a final-gate validation that the success criteria are met before honoring the stop signal (**LSC-6**). In (d), make goal-completion a human-confirmed checkpoint (**LSC-8**).
- **Most prone:** (b) multi-step tasks that "feel finished"; (d) humans rubber-stamping checkpoints.

### 4. Runaway / non-termination

- **Symptom:** The loop never emits a stop signal — it keeps iterating past any sensible point, burning budget indefinitely.
- **Cause:** Relying on the model to decide it's done. Self-termination (**LSC-2**) is a *best effort*, not a guarantee; a looping agent is, by definition, the thing that has failed to stop — so it cannot be trusted to stop itself.
- **Mitigation:** **LSC-3 backstop cap is mandatory and non-negotiable.** A hard limit (`BACKSTOP_MAX_ITERATIONS` / `BACKSTOP_TOKEN_BUDGET` / `BACKSTOP_WALL_CLOCK`) lives **in the harness, not the model**, and the safe default state is `stopped`. The cap fires regardless of what the model decides. Pair with no-progress detection (**LSC-5**) so the loop trips earlier than the hard cap when it's churning. Note the cost-side `MAX_ITERATIONS` (LSC-9) is a *different, lower* number than the LSC-3 safety cap — see Part B.
- **Most prone:** (b) open-ended autonomous tasks; (c) agent groups with no aggregate cap.

### 5. Prompt-injection via carried content

- **Symptom:** A tool result, web page, file, or another agent's message contains text like "ignore previous instructions / now do X," and the loop obeys it — executing actions the author never authorized.
- **Cause:** Carried content (model output, tool returns, external/web text) is concatenated into the prompt and read as **instructions** instead of **data**. The two channels (LSC-7) are mixed. This is the core security vulnerability of any self-prompting loop.
- **Mitigation:** Enforce the **LSC-7** two-channel model rigorously. The `TRUSTED_CONTROL_CHANNEL` is the fixed, author-written prompt that drives iteration; everything carried forward goes in the `UNTRUSTED_DATA_CHANNEL` and is wrapped/marked as DATA the prompt *reasons about*, never commands it obeys (`DATA_WRAPPING`). Mental model: the loop is a **trusted harness re-invoking itself**, not a model literally running its own output. Back it with pre-action validation so injected content can't trigger a consequential action without passing checks (**LSC-6**), and gate irreversible actions on a human (**LSC-8**).
- **Most prone:** (b) tool/web-using loops; (c) inter-agent messages treated as commands.

### 6. Context rot / state bloat

- **Symptom:** Carried state grows every round (accumulated scratchpad, full transcripts, every tool result). Later iterations get slower, more expensive, and *worse* — the signal gets buried, the model fixates on stale detail or contradicts earlier carried text.
- **Cause:** `STATE_CARRIED` (LSC-4) accumulates append-only with no compaction. The relevant signal-to-noise ratio of the context degrades; token cost rises in lockstep.
- **Mitigation:** Carry a *structured, bounded* state object, not a raw growing transcript (**LSC-4** — prefer structured state over append-only scratchpad). Summarize/compact prior rounds into a fixed-size digest; keep only what the next round needs. Cap state size as part of the token budget (**LSC-9**). Cycle/no-progress detection (**LSC-5**) catches the degradation when output quality stalls.
- **Most prone:** (a) long reflexion chains; (b) long autonomous runs with chatty tools.

### 7. Cost blowout

- **Symptom:** Token spend or iteration count explodes far past expectation — a "quick" loop racks up a large bill, often silently.
- **Cause:** No aggregate budget, or a budget that isn't enforced; combined with runaway (#4), state bloat (#6), or over-fanout (#8). Frequently amplified by cache-naive cadence re-paying cache misses (see Part B).
- **Mitigation:** Set a hard aggregate `TOKEN_BUDGET` and a cost-side `MAX_ITERATIONS`, and degrade gracefully as you approach them — finish/summarize rather than getting killed mid-step (**LSC-9**). The LSC-3 backstop is the last line (it stops *everything*); LSC-9 limits are the *planned* ceiling you design around. Use cache-aware cadence to avoid paying avoidable cache misses (Part B). Consider a cheaper model for review passes (Part B).
- **Most prone:** (b) long tasks; (c) per-agent costs multiplied across a group.

### 8. Multi-agent deadlock / over-fanout

- **Symptom:** Deadlock — agents each wait on another's output, nothing advances. Or over-fanout — agents spawn sub-agents which spawn more, growing the population without bound.
- **Cause:** No aggregate cap across the agent group; circular dependencies in who-waits-on-whom; spawning with no depth/population limit. Each agent may individually look bounded while the *group* is not.
- **Mitigation:** Apply **LSC-3** at the *aggregate* level — a cap on total iterations/tokens/wall-clock and on agent count/spawn depth across the whole group, in the harness. Give the group a shared stop condition and consensus/all-subtasks-resolved signal so it can terminate (**LSC-2**). Budget **per agent and in aggregate** (**LSC-9**). Detect no-progress at the group level (cross-agent review/voting) to break stalls (**LSC-5**). Break wait-cycles with timeouts on inter-agent waits.
- **Most prone:** (c) exclusively — this is the multi-agent-specific class.

---

## Part B — Cost & cadence playbook (LSC-9 / O2)

### Token budget

- Set a **hard aggregate token budget** (`TOKEN_BUDGET`) for the whole run, not just per-call limits. Per-call limits don't stop a long loop.
- **Degrade gracefully near the limit.** As spend approaches the budget, switch the loop into a wind-down: stop starting new work, summarize/finalize what exists, emit a best-effort result. Do **not** get hard-killed mid-step with nothing to show.
- Count *carried state* against the budget — state bloat (Part A #6) is a budget line item, not free.

### Iteration limits — two different numbers for two different reasons

| | LSC-9 cost limit | LSC-3 safety backstop |
|---|---|---|
| **Purpose** | Keep spend within the *planned* envelope | Guarantee termination when self-stop fails |
| **Value** | Lower — where you *expect* a healthy loop to finish | Higher — absolute outer bound |
| **Lives in** | Loop policy / config | The harness (infrastructure level) |
| **Triggered by** | Normal cost discipline | A loop that has already failed to stop itself |
| **On trip** | Graceful wind-down | Hard stop, safe state = `stopped` |

`MAX_ITERATIONS` (LSC-9) ≠ `BACKSTOP_MAX_ITERATIONS` (LSC-3). The cost limit is the ceiling you design *toward*; the backstop is the floor under your safety. They are deliberately different numbers — if they're equal, you've collapsed "expected finish" and "last-resort kill" into one, and lost the early warning. **A looping agent cannot be trusted to stop itself**, so the LSC-3 cap is mandatory regardless of how generous the LSC-9 limit is.

### Cadence & the prompt-cache TTL

The prompt cache has a **~5-minute (~300s) TTL**. Reusing a warm cache is far cheaper than re-paying a full cache miss. Pace the loop in **cache windows**, not round-number minutes.

| Mode | Interval | Why |
|---|---|---|
| **Active polling** | **< ~270s** | Stay comfortably inside the ~300s window so the cache stays warm — every tick reuses it. |
| **Idle ticks** | **1200–1800s (20–30 min)** | Accept *one* cache miss deliberately to buy a long, cheap wait. You pay the miss once and amortize it over a long idle. |
| **AVOID** | **~300s exactly** | Worst of both: the cache has *just* expired, so you pay a full miss — but you waited only 5 minutes, so you got nothing for it. No amortization, guaranteed miss. |

Rule of thumb: **don't think in round-number minutes (5 min, 10 min) — think in cache windows.** Either stay inside the window (poll < ~270s) or commit to a long wait well past it (idle 20–30 min). The dead zone is right at the TTL boundary. `CADENCE` and `CACHE_AWARENESS` in the LSC-9 slot must reflect this, not an arbitrary timer.

For (d) human-checkpointed loops, align idle cadence to human availability — long idle ticks between checkpoints are the norm, so eat the cache miss and wait.

### Different model for review

- Use a **separate, often cheaper, model for self-evaluation / review passes** (LSC-5).
- **Two reasons:** (1) avoids **shared-bias** — a model grading its own output tends to ratify it, masking premature-stop (Part A #3) and drift (Part A #2); a different model is a genuine second opinion. (2) **cost** — review passes are frequent; running them on a cheaper model cuts the per-round bill substantially.
- This makes self-evaluation (LSC-5) both more honest and cheaper at once.

### Cost & cadence checklist (yes/no)

- [ ] Is there a hard aggregate `TOKEN_BUDGET` for the whole run (not just per-call)?
- [ ] Does the loop **degrade gracefully** (wind down, summarize) as it nears the budget?
- [ ] Is the LSC-9 cost `MAX_ITERATIONS` a **different (lower) number** than the LSC-3 `BACKSTOP_MAX_ITERATIONS`?
- [ ] Is the LSC-3 backstop in the **harness**, firing regardless of model self-stop, with safe state `stopped`?
- [ ] Is active-poll cadence **< ~270s** (inside the cache window)?
- [ ] Are idle ticks **1200–1800s** (long enough to amortize one cache miss)?
- [ ] Have you **avoided ~300s** exactly (the cache-miss-with-no-payoff dead zone)?
- [ ] Is cadence chosen against the **cache TTL**, not a round-number minute?
- [ ] Are review/self-eval passes run on a **separate/cheaper model** to cut cost and shared bias?
- [ ] For multi-agent: is there an **aggregate** budget and agent-count/spawn-depth cap, not just per-agent?
