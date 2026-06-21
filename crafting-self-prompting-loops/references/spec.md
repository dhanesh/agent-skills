# Self-Prompting Loop — Structured Spec

Structured, mechanically-harvestable spec for a sound self-prompting loop. Keyed 1:1 to the canonical [`checklist.md`](./checklist.md): one `## LSC-N` block per item, IDs and order fixed by contract. This is the terse, per-item form the skill workflow draws on.

Each block: one-line definition, the parameterized SLOT (slot keys copied verbatim from the checklist), a `Design rule:` imperative, and a `Decision criteria:` line.

---

## LSC-1: Goal / success definition

Definition: the loop's target and the concrete, checkable test for "done."

```
GOAL: <what the loop is trying to achieve>
SUCCESS_DEFINITION: <concrete, checkable description of "done">
```

Design rule: write GOAL and SUCCESS_DEFINITION before the loop runs; do not start a loop whose "done" is not externally checkable.
Decision criteria: choose SUCCESS_DEFINITION as the single condition an outside party could verify in one sentence; if you cannot write that sentence, the loop is not ready.
Before-you-loop check: if the task has a verifiable or aggregatable answer, baseline **parallel sampling + majority vote (self-consistency)** first — it often beats iterative refinement at equal compute (Wang et al. 2023; Huang et al. 2023). The cheapest sound loop is sometimes no loop. See [`literature.md`](./literature.md) §B.
No-oracle case: if "done" genuinely has no externally checkable test (e.g. semantic correctness of an analytics answer — it can execute cleanly yet be wrong), do **not** pretend one exists. Either (a) substitute the strongest *honest* proxy you can check (executes + pre-declared sanity assertions + no-regression) and make the **human gate (LSC-8) the real certifier** of "right" — the loop converges the proxy, the human closes it; or (b) don't loop. Never let an optimistic self-judge stand in for an oracle the deployed loop won't have (the privileged-oracle trap, Huang et al. 2023).

## LSC-2: Stop condition (primary)

Definition: the loop's own rule and signal for deciding it is done and halting.

```
STOP_CONDITION: <how the loop decides it's done>
STOP_SIGNAL: <the concrete signal/token/flag that triggers termination>
```

Design rule: derive STOP_CONDITION directly from LSC-1's SUCCESS_DEFINITION and have the model emit STOP_SIGNAL when it passes.
Decision criteria: pick a STOP_SIGNAL the harness can observe unambiguously (token, flag, structured field); never infer "done" from free text.
Honest-stop rule: stop on a real verifier passing, a budget cap, or no-change convergence — never on information the deployed loop won't actually have. Apparent self-correction gains are often an artifact of "keep going until right" using a privileged oracle label the loop won't have in production (Huang et al. 2023). See [`literature.md`](./literature.md) §A.

## LSC-3: Backstop cap (MANDATORY)

Definition: a hard, harness-level limit that terminates the loop regardless of the model.

```
BACKSTOP_MAX_ITERATIONS: <hard iteration cap>
BACKSTOP_TOKEN_BUDGET: <hard token cap>
BACKSTOP_WALL_CLOCK: <hard time limit>
SAFE_STATE: stopped
```

Design rule: implement at least one backstop in the harness, independent of LSC-2; the model is never trusted to be the sole terminator, and SAFE_STATE is always `stopped`.
Decision criteria: set each cap above the expected legitimate run length but below the point of unacceptable cost/runaway; when in doubt, halt.

## LSC-4: State-passing

Definition: the information carried from one iteration to the next and the mechanism that stores it.

```
STATE_CARRIED: <what information passes between iterations>
STATE_MECHANISM: <scratchpad | structured state object | prior output | other>
```

Design rule: explicitly define STATE_CARRIED and STATE_MECHANISM; pass forward the minimum that lets the next round build on the last and detect repetition.
Decision criteria: carry the smallest state sufficient for progress and loop-detection; prefer a structured summary over the full transcript to bound tokens and limit drift.

## LSC-5: Self-evaluation / progress assessment

Definition: a per-round judgment of progress toward LSC-1 and detection of non-improvement.

```
PROGRESS_METRIC: <how progress toward GOAL is measured each round>
NO_PROGRESS_DETECTION: <how the loop detects it is not improving>
```

Design rule: produce a PROGRESS_METRIC every round and define NO_PROGRESS_DETECTION that triggers a stop or change of tack when stalled.
Decision criteria: pick a metric tied to SUCCESS_DEFINITION (critique score, sub-goal completion, vote); set the no-progress threshold (e.g. N rounds without improvement) from acceptable churn cost.

**Evaluation must have external leverage (the single biggest correctness gap).** Intrinsic self-critique with no external signal *degrades* performance on objective tasks (Huang et al. 2023), and a model grading its own output amplifies self-bias each round — perceived quality rises while real quality stalls (Xu et al. 2024). So the evaluation signal must carry leverage the generator lacked:
- **Prefer a tool/verifier over an opinion** — run the code, execute the tests, query the source, compute the constraint, and feed concrete failures back (CRITIC; Reflexion's gains came from a real pass/fail signal, not introspection).
- **If the check must be model-based, use a *separate* evaluator** — a different model, or at minimum an authorship-blinded critique prompt; the generator must not be its own judge. (This is also the cost win in [`failure-modes.md`](./failure-modes.md) Part B — honesty and cost align.)
- **A same-model self-check on the same task buys little** — generation skill does not transfer to reliable self-verification (*Mind the Gap* 2025).
- **Scope intrinsic-only refinement to subjective/stylistic outputs with a capable base model** (Self-Refine); for reasoning, use tool-grounded critique or sampling+vote.

See [`literature.md`](./literature.md) §A.

## LSC-6: Guardrail

Definition: validation that must pass before any consequential action executes.

```
PRE_ACTION_CHECKS: <assumption-blocking + validation before consequential actions>
OUTPUT_VALIDATION: <how structured output is validated before use>
```

Design rule: run PRE_ACTION_CHECKS and OUTPUT_VALIDATION before, never after, any consequential action or tool call.
Decision criteria: gate with a guardrail every action whose failure is costly or hard to detect downstream; validate structure/claims/tool-args at the point of use.
Least-privilege rule: scope the loop's tools and permissions to the minimum the task needs — excess agency is itself attack surface, and injection only matters if the agent can *act* consequentially (OWASP LLM06). Never feed raw tool output or values extracted from untrusted content into an execution sink (shell/SQL/eval/HTTP) unchecked (CaMeL). For multi-agent, validate typed/structured handoffs between agents rather than chaining free-form text (MetaGPT).

## LSC-7: Data/instruction channel separation (two-channel model)

Definition: author-written control instructions kept strictly separate from all model/tool/external content, which is treated as data, never commands.

```
TRUSTED_CONTROL_CHANNEL: <the fixed, author-written instructions that drive iteration>
UNTRUSTED_DATA_CHANNEL: <model output + tool results + external/web content carried forward>
DATA_WRAPPING: <how carried content is wrapped/marked as DATA, not instructions>
```

Design rule: wrap and label all carried content via DATA_WRAPPING as data the fixed prompt reasons about; never splice untrusted content into the control channel.
Decision criteria: classify any runtime-variable content (model output, tool results, web/external text, other agents' messages) as UNTRUSTED_DATA_CHANNEL by default; only author-fixed scaffold is trusted.

**Wrapping is necessary but NOT sufficient.** Delimiting/datamarking lowers injection *probability*; it does not remove the *capability* to be injected — a model can still be talked past its delimiters (Spotlighting; CaMeL). So treat the boundary as **architectural, not textual**: derive the loop's control flow from the trusted prompt *before* touching untrusted data, and back the wrapping with least-privilege tools (LSC-6) and a human gate on irreversible actions (LSC-8). Treat **all** tool output — and values *extracted* from untrusted content (a recipient address, a filename) — as untrusted (CaMeL). See [`literature.md`](./literature.md) §E.

### Two-channel model

- **Trusted control channel:** author-written fixed scaffold instructions that drive iteration; defined at design time, immutable at runtime.
- **Untrusted data channel:** model output + tool results + external/web content + inter-agent messages carried forward.
- **Rule:** carried content is ALWAYS wrapped as data and reasoned about, NEVER executed as instructions. This is the prompt-injection defense and the operational form of "harness re-invokes the model with new context; the model's output is never the harness's command."

### The lethal trifecta — a design check (Willison 2025)

A loop becomes a reliable data-exfiltration tool when it simultaneously has **(1) access to private data, (2) exposure to untrusted content, (3) the ability to communicate externally.** Audit every loop for all three legs; **if all three are present, break one** — drop external-comms capability, quarantine untrusted content out of the privileged path, or scope away the private data. Distrust vendor "N% blocked" claims as the only mitigation.

### Secure-pattern ladder (Beurer-Kellner et al. 2025) — pick the most restrictive that still works

| Pattern | What it does | Use when |
|---|---|---|
| **Action-Selector** | model picks from pre-approved actions; no tool output flows back | strongest — no feedback needed |
| **Plan-Then-Execute** | fix the action plan *before* exposure to untrusted content (injection can corrupt data, not the plan) | the plan can be set up front |
| **Map-Reduce** | isolate untrusted content in sub-agents returning only constrained results (e.g. booleans) | many independent untrusted inputs |
| **Dual-LLM** | privileged LLM (tools, trusted query) directs a quarantined LLM (untrusted content, no tools); results pass as symbolic refs | untrusted content must be processed |
| **Code-Then-Execute / CaMeL** | compile trusted query to restricted code with provenance-tracked capabilities; tool calls pass explicit policies | provable security needed |
| **Context-Minimization** | drop the original prompt once converted to a structured query | prevent later contamination |

## LSC-8: Human safety-gate

Definition: an approval checkpoint that pauses the loop before irreversible/consequential actions.

```
GATED_ACTIONS: <which consequential/irreversible actions require approval>
APPROVAL_MECHANISM: <how a human approves or blocks before the loop proceeds>
```

Design rule: block every GATED_ACTION behind APPROVAL_MECHANISM and proceed only on explicit human approval.
Decision criteria: gate any action that is irreversible or has external side effects (funds, deletions, public posts, real-world effects); output-only loops may omit the gate. The gate is also the backstop for prompt injection: even if wrapping (LSC-7) is bypassed, a human approving the consequential action catches it — which is why a loop carrying the lethal trifecta needs this gate, not just delimiters.

## LSC-9: Cost & cadence control

Definition: bounded spend, bounded iteration-for-cost, and deliberate pacing aligned to the prompt-cache TTL.

```
TOKEN_BUDGET: <spend limit>
MAX_ITERATIONS: <iteration limit for cost (distinct from LSC-3 safety cap)>
CADENCE: <pacing between iterations, chosen deliberately>
CACHE_AWARENESS: <how pacing respects the prompt-cache TTL, e.g. ~5 min>
```

Design rule: set TOKEN_BUDGET, MAX_ITERATIONS (cost, distinct from the LSC-3 safety cap), and CADENCE explicitly; pace against CACHE_AWARENESS, not a round number.
Decision criteria: choose CADENCE so re-invocation lands within the prompt-cache TTL (~5 min) to reuse cached context; size budgets to the task's value.

## LSC-10: Failure-mode handling

Definition: a named mitigation for each of the four classic loop failure modes.

```
OSCILLATION_MITIGATION: <handling flip-flopping between states>
DRIFT_MITIGATION: <handling wandering off-goal>
PREMATURE_STOP_MITIGATION: <handling quitting too early>
RUNAWAY_MITIGATION: <handling never stopping / non-termination>
```

Design rule: define all four mitigations; RUNAWAY_MITIGATION must include the LSC-3 backstop, PREMATURE_STOP must re-check LSC-1's SUCCESS_DEFINITION, DRIFT must re-anchor to LSC-1, OSCILLATION must use LSC-5 progress history.
Decision criteria: per family, pick the dominant failure modes (e.g. drift/oscillation in self-refinement, runaway/premature-stop in long autonomous tasks, deadlock in multi-agent) and ensure each has an observable detector plus a recovery path.

### Termination contract (LSC-2 / LSC-3)

- **Primary:** model self-termination via LSC-2 STOP_SIGNAL when SUCCESS_DEFINITION passes.
- **Backstop:** mandatory infra-level cap (LSC-3) on iterations / tokens / wall-clock, independent of the model.
- **Safe state:** `stopped` — on any backstop trip or uncertainty, the loop halts.

---

Coverage: LSC-1 … LSC-10 all present, 1:1 with [`checklist.md`](./checklist.md). Family-specific treatments: [`families.md`](./families.md). Research grounding for every non-obvious design rule above: [`literature.md`](./literature.md).
