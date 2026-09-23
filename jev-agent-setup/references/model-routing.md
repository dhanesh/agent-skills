# Routing a subagent to a model with Jev

Picking the model for a subagent, a background task, or a fan-out leg is a Choice: one of a
known set, judged on the task. The set is whatever the harness offers right now, so the caller
enumerates it at runtime and Jev picks among those options. Nothing here hardcodes a roster.

## The shape

1. **Enumerate what is available.** Ask the harness (or read config) for the models this
   session may dispatch, and write one line per model describing what it is good for, and its
   relative cost and latency. A model the caller cannot dispatch MUST NOT appear as an option —
   Jev cannot pick a candidate you left out, and it will happily pick one you cannot run.
2. **Ask one Choice plus one Score, batched.** The Choice picks the model; the Score rates how
   hard the task is, which lets code apply a floor or a ceiling without a second round trip.
3. **Let code own the policy.** Budget, org-approved models, "never use the expensive one for
   a background loop", per-repo defaults: all of that stays in code, applied to Jev's answer.

## Worked example

```bash
jev <<'JSON'
{"state": {
   "task": "Port 400 lines of legacy pandas ETL to polars, keeping the existing tests green",
   "context": {"files_touched": 3, "tests_exist": true, "runs_unattended": false},
   "models": {
     "haiku":  "Cheapest and fastest. Mechanical edits, extraction, short summaries, high-volume fan-out.",
     "sonnet": "Balanced. Ordinary feature work, refactors, test writing, most review passes.",
     "opus":   "Most capable and most expensive. Deep multi-step reasoning, subtle bugs, architecture, ambiguous specs."}},
 "questions": {
   "model": {"type": "choice",
             "instructions": "Which model in `models` should run `task`? Pick the cheapest one that is very likely to finish it correctly without supervision.",
             "criteria": {"haiku": "Mechanical or narrow; little judgment needed",
                          "sonnet": "Ordinary engineering work with a clear target",
                          "opus": "Needs sustained reasoning, or a mistake is expensive to undo"}},
   "difficulty": {"type": "score",
                  "instructions": "How demanding is `task` for a coding model?",
                  "criteria": ["Mechanical: follow a stated pattern",
                               "Ordinary: design a small solution and verify it",
                               "Hard: ambiguous, wide blast radius, or needs deep reasoning"]}}}
JSON
```

## Applying the answer

Models are an ordered ladder, so `confidence` is the wrong dial here: it measures how
concentrated the distribution is, and two *adjacent* tiers splitting the mass means "either
would do", not "no idea". Measured on real calls: a hard port scored `opus 0.51 / sonnet 0.49`
(confidence 0.26) and a mechanical rename scored `sonnet 0.60 / haiku 0.40` (confidence 0.43).
A naive `confidence >= 0.6` gate would have thrown both answers away. Read the probabilities
and let the difficulty Score break the tie:

| Situation | What code does |
|---|---|
| Top two are adjacent tiers, `difficulty ≥ 1.5` | Take the more capable of the two |
| Top two are adjacent tiers, `difficulty ≤ 0.5` | Take the cheaper of the two |
| Anything else with a clear top option (`p ≥ 0.4`) | Take the highest-probability model |
| Flat distribution (top `p < 0.4`) | Use the session default; say which models were close |
| Any option with `p == 0` | Never dispatch it, whatever the difficulty says |
| Non-zero `jev` exit (no verdict) | Use the session default, unchanged |
| Budget or policy forbids the pick | Use the best allowed model below it; policy decides, not Jev |

Worked from the two calls above: the port resolves to **opus** (adjacent pair, difficulty 1.84)
and the rename to **haiku** (adjacent pair, difficulty 0.40) — the cheaper end of each pair,
which is the behaviour a `confidence` gate could not produce.

Two failure modes worth guarding: a **stale roster**, where the options name models the harness
no longer offers (re-read the list rather than trusting a written-down set), and **one question
per candidate**, which wastes calls — a single Choice already returns a probability for every
option, and those probabilities are the ranking.

## When to skip Jev

Skip it when the answer is already fixed: the user named a model, config pins one, only one is
available, or the task is a retry of work a specific model already started. Routing a trivial
one-line subagent through a network call costs more than it saves — batch the model question
with the other judgments you already need for that dispatch.
