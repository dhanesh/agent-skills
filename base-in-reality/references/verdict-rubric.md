# Verdict & Evidence Rubric

All findings produced by a `base-in-reality` audit carry exactly one verdict and one severity. This document defines both enumerations, the grounding invariant that governs them, and the adversarial refutation protocol that prevents false positives.

## Verdicts

| Verdict | Meaning | Evidence bar |
|---|---|---|
| `VIOLATION` | Contradicts an established standard/algorithm/best practice | ≥1 fetched authoritative source + survived refutation |
| `DEVIATION` | Departs from the recommended norm but may be defensible | ≥1 fetched source + survived refutation |
| `OUTDATED` | Matches a superseded practice | a cited newer standard/result that replaced it |
| `UNCONFIRMED` | Could not be grounded within budget | none required; this is the default ceiling for ungrounded claims |

## Severity

| Severity | When |
|---|---|
| `critical` | Security/compliance/financial-correctness failure with real-world harm (e.g. broken crypto, miscomputed APR, PHI mishandling) |
| `high` | Clear violation of an established norm with material impact, no immediate catastrophe |
| `medium` | Defensible deviation or outdated practice worth revisiting |
| `low` | Minor or stylistic departure from common practice |

## Grounding rule (invariant)

Every finding's evidence must be a URL/DOI the agent fetched **this session**. A claim that cannot be grounded is reported as `UNCONFIRMED` and **never** as `VIOLATION`/`DEVIATION`. No fabricated, remembered, or unverified citations.

## Refutation protocol

For every candidate `VIOLATION` or `DEVIATION`, the workflow runs an adversarial refutation pass before the verdict is finalized. The pass works as follows:

1. Spawn ≥3 independent refuter sub-agents (or reasoning passes). Each refuter MUST operate on a **distinct lens** — at minimum: (a) factual correctness of the claim-as-stated, (b) applicability of the cited source to the specific code context, (c) severity calibration. The lenses are what decorrelate the refuters: a plain majority vote over refuters that share the same prompt (and the same underlying model) is brittle under *confabulation consensus*, where correlated bias drives the ensemble to the same wrong rationale. Keep the lenses genuinely different; where the budget allows, prefer **distinct models** across refuters so the votes can be treated as near-independent.
2. Each refuter defaults to `refuted: true` when uncertain. The burden of proof is on the finding, not the refuter.
3. **Aggregation.** Downgrade to `UNCONFIRMED` when ≥2 of the 3 refuters return `refuted: true`. Because a 2-of-3 vote over a same-model ensemble is the weak baseline, calibrate by severity: require **unanimity (3-of-3 *non*-refute)** to keep a `critical`/`high`-severity `VIOLATION`, and weight a refuter's vote by the strength of the evidence it brings (a refuter that cites a fetched source over-application beats one that only asserts doubt). This trades a little recall for precision exactly where a false positive is most costly.
4. The refutation outcome — number of refuters, individual verdicts, dominant rationale — is recorded in the `refutation` field of the finding object.

This protocol prevents a single confident-but-wrong retrieval from elevating a speculative observation to a `VIOLATION`, and prevents a correlated-bias ensemble from rubber-stamping one. When in doubt, report `UNCONFIRMED`.
