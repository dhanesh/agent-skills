# Method selection — how the kernel was distilled from 46 candidates

This is the audit trail for what is *in* this skill and what was deliberately left out.
Read it when you want to challenge an inclusion, add a method, or explain to a
sceptical user why the skill doesn't offer them a decision tree.

Evidence grades: **S** = experimental/meta-analytic support · **M** = correlational,
contested, or partial · **P** = practitioner heuristic, no meaningful empirical support.

## Three findings that constrained everything

1. **Paralysis is not a framework shortage.** Five distinct triggers — choice overload,
   perfectionism/maximizing, regret avoidance, low confidence, ambiguous ownership — each
   with a *different* fix. Offering a menu of methods to someone already stuck produces
   meta-paralysis. Hence: diagnose, then route to exactly one protocol.
2. **An LLM decision coach is sycophantic by default.** Cheng et al., *Science*, March 2026
   ("Sycophantic AI decreases prosocial intentions and promotes dependence") tested 11
   frontier models: they endorsed the user's position ~49% more often than human
   respondents, and ~47% of the time even on prompts describing harmful conduct — and
   users *preferred* the sycophantic model. If the user states a lean before criteria are
   locked, the skill will confirm the lean and dress it in a framework. Every protocol here
   must be structurally lean-resistant, not merely instructed to be objective.
3. **`SKILL.md` has a ~500-line budget** and loads in full on every trigger. The kernel is
   a handful of methods, not twenty. Distillation is a budget constraint, not a nicety.

## Pass 1 — Mechanism test

**Kill criterion:** can you complete *"this works because it forces the brain to ___,
which counteracts ___"*? If the method is a taxonomy, a container for other methods, or an
acronym organising common sense, it dies here.

| Killed | Why |
|---|---|
| Cynefin (P) | A taxonomy. Tells you what kind of world you're in, not what to do next. |
| WRAP (P) | A container for the vanishing-options test, the outside view, the pre-mortem and tripwires — all of which are included individually. |
| Kepner-Tregoe (M) | Its one durable insight ("is this problem analysis or decision analysis?") survives as a triage line; the full method does not. |
| Vroom-Yetton-Jago (M) | Superseded by RAPID for the participation question. |
| Decision fatigue (M) | Ego-depletion has replication problems. The practical residue ("is this a 6pm decision?") survives as a diagnostic line, not a method. |
| DACI (P) | Strictly lighter RAPID. Same mechanism, no veto boundary. |
| Weighted criteria matrix (M) | A weaker Mediating Assessments Protocol. Redundant. |
| Red team / devil's advocate (M) | Assigned dissent is known to weaken when the role is known to be assigned — which is exactly why the pre-mortem outperforms it. |
| Noise audit (S) | Genuinely strong, but org-level and cross-case. Nothing to run on a single decision. |
| "Resulting" / process-vs-outcome (M) | A principle, not a step. Folded into the review protocol. |
| Base-rate library (M) | A *consequence* of keeping decision records, not an independent method. |
| AND-not-OR, 5 Whys, fear-setting, regret minimization, inversion | Each duplicates a surviving method's mechanism at equal or higher cost. |

## Pass 2 — Mechanism clustering

The 46 candidates collapse into **six mechanisms**. Within each cluster, keep the cheapest
method that delivers the mechanism, plus at most one specialist that covers a materially
different case.

| Mechanism | Kept | Rejected in-cluster |
|---|---|---|
| Decompose & delay holistic judgment | **Mediating Assessments Protocol** (S) | weighted matrix, reasoned rule, K-T |
| Invert the frame | **Pre-mortem** (S) + specialist **steelman the rejected option** (P) | inversion, regret minimization, promortem |
| Escape the inside view | **Outside view / reference class** (S) | explicit-priors ritual, falsification test |
| Bound the search | **Time-box + satisficing bar** (S/M) | 70% rule (is this), recognition-primed decision |
| Collapse the option set via constraints | **Constraint-first framing** (P) + **vanishing-options test** (P) | TRIZ, 5 Whys, opportunity cost (folded in as a mandatory criterion) |
| Create accountability & feedback | **Decision record** (S) carrying single-decider, tripwires, review date, Brier scoring | ADR (its *shape* is adopted), disagree-and-commit (a norm, noted in Boundaries), escalation criteria |

The steelman is the only specialist admitted on a non-evidence basis: it exists purely
because of finding #2 above. It is the skill's sycophancy antidote.

## Pass 3 — Chat viability and lean-resistance

Two kill criteria: executable inside a conversation without data the user won't have, and
structurally resistant to confirming a stated lean.

- **Killed: expected value / decision trees (S).** The mathematics are correct and the
  inputs almost never exist. A tree built on invented probabilities launders a guess.
- **Kept and promoted:** MAP, pre-mortem, steelman — all three are lean-resistant *by
  construction*, because they impose an order of operations rather than asking the model
  to be objective.
- **This pass produced the skill's hardest rule:** criteria are elicited and locked
  **before** options are scored and before any preference is stated. `assets/decision_lint.py`
  enforces the document order mechanically, so the constraint survives a persuasive user.

## Pass 4 — Trigger coverage

Any uncovered trigger is a gap that must be filled even if the filler scored poorly above.

| Trigger | Covered by |
|---|---|
| Choice overload | constraint-first framing, vanishing-options test, satisficing bar |
| Perfectionism / maximizing | satisficing bar + time-box |
| Regret avoidance | reversibility + cost-of-reversal, staged commitment, tripwires, 10/10/10 |
| Low confidence | outside view, decomposed criteria |
| Ambiguous ownership | single named decider (RAPID's one durable idea) |
| *Misdiagnosis:* values conflict presented as an information problem | the values-vs-information test |
| *Misdiagnosis:* a settled decision being re-litigated | sunk-cost check + the anti-trigger rules in `diagnostics.md` |

The last two rows are the gap this pass found. Both were cheap methods that had scored
poorly on evidence and would otherwise have been cut; both are in.

## What a reviewer should challenge first

- **Constraint-first framing is P-grade** and is here partly because it composes with the
  author's `manifold` framework. If it fails in practice, the vanishing-options test alone
  covers the cluster.
- **10/10/10 is P-grade** and only fires on the regret branch. It is the weakest inclusion.
- **The satisficing evidence is about satisfaction, not decision quality.** Maximizers make
  objectively better choices and feel worse about them. The bar is prescribed as a
  paralysis-breaker, not as a route to better outcomes — do not oversell it to the user.
- **Choice-overload effects are contested** (Scheibehenne et al.'s meta-analysis found
  effects vary widely by context; the original jam study replicates unevenly). The
  option-reduction moves are justified by working-memory limits, not by the jam study.
