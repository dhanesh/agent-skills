# Verdict & Evidence Rubric

All findings produced by a `base_in_reality` audit carry exactly one verdict and one severity. This document defines both enumerations, the grounding invariant that governs them, and the adversarial refutation protocol that prevents false positives.

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

1. Spawn ≥3 independent refuter sub-agents (or reasoning passes). Each refuter operates on a distinct lens — at minimum: (a) factual correctness of the claim-as-stated, (b) applicability of the cited source to the specific code context, (c) severity calibration.
2. Each refuter defaults to `refuted: true` when uncertain. The burden of proof is on the finding, not the refuter.
3. If ≥2 of the 3 refuters return `refuted: true`, the verdict is downgraded to `UNCONFIRMED`.
4. The refutation outcome — number of refuters, individual verdicts, dominant rationale — is recorded in the `refutation` field of the finding object.

This protocol prevents a single confident-but-wrong retrieval from elevating a speculative observation to a `VIOLATION`. When in doubt, report `UNCONFIRMED`.
