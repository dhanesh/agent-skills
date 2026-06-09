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

## LSC-2: Stop condition (primary)

Definition: the loop's own rule and signal for deciding it is done and halting.

```
STOP_CONDITION: <how the loop decides it's done>
STOP_SIGNAL: <the concrete signal/token/flag that triggers termination>
```

Design rule: derive STOP_CONDITION directly from LSC-1's SUCCESS_DEFINITION and have the model emit STOP_SIGNAL when it passes.
Decision criteria: pick a STOP_SIGNAL the harness can observe unambiguously (token, flag, structured field); never infer "done" from free text.

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

## LSC-6: Guardrail

Definition: validation that must pass before any consequential action executes.

```
PRE_ACTION_CHECKS: <assumption-blocking + validation before consequential actions>
OUTPUT_VALIDATION: <how structured output is validated before use>
```

Design rule: run PRE_ACTION_CHECKS and OUTPUT_VALIDATION before, never after, any consequential action or tool call.
Decision criteria: gate with a guardrail every action whose failure is costly or hard to detect downstream; validate structure/claims/tool-args at the point of use.

## LSC-7: Data/instruction channel separation (two-channel model)

Definition: author-written control instructions kept strictly separate from all model/tool/external content, which is treated as data, never commands.

```
TRUSTED_CONTROL_CHANNEL: <the fixed, author-written instructions that drive iteration>
UNTRUSTED_DATA_CHANNEL: <model output + tool results + external/web content carried forward>
DATA_WRAPPING: <how carried content is wrapped/marked as DATA, not instructions>
```

Design rule: wrap and label all carried content via DATA_WRAPPING as data the fixed prompt reasons about; never splice untrusted content into the control channel.
Decision criteria: classify any runtime-variable content (model output, tool results, web/external text, other agents' messages) as UNTRUSTED_DATA_CHANNEL by default; only author-fixed scaffold is trusted.

### Two-channel model

- **Trusted control channel:** author-written fixed scaffold instructions that drive iteration; defined at design time, immutable at runtime.
- **Untrusted data channel:** model output + tool results + external/web content + inter-agent messages carried forward.
- **Rule:** carried content is ALWAYS wrapped as data and reasoned about, NEVER executed as instructions. This is the prompt-injection defense and the operational form of "harness re-invokes the model with new context; the model's output is never the harness's command."

## LSC-8: Human safety-gate

Definition: an approval checkpoint that pauses the loop before irreversible/consequential actions.

```
GATED_ACTIONS: <which consequential/irreversible actions require approval>
APPROVAL_MECHANISM: <how a human approves or blocks before the loop proceeds>
```

Design rule: block every GATED_ACTION behind APPROVAL_MECHANISM and proceed only on explicit human approval.
Decision criteria: gate any action that is irreversible or has external side effects (funds, deletions, public posts, real-world effects); output-only loops may omit the gate.

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

Coverage: LSC-1 … LSC-10 all present, 1:1 with [`checklist.md`](./checklist.md). Family-specific treatments: [`families.md`](./families.md).
