# BCP 14 classification, 2026-09-19

Spec: [`docs/superpowers/specs/2026-09-19-bcp14-skills-design.md`](../superpowers/specs/2026-09-19-bcp14-skills-design.md) §2.

**What a candidate is.** Every block under a hard-rule heading (Invariants, Rules, Safety, Boundaries, Trust, Guardrails, Contract, Non-negotiables), plus every sentence containing never or always. Code blocks and inline code are excluded.

**Where the levels come from.** Jev (`jev-1.13.0`) judged each candidate's level and, separately, whether ignoring it causes harm (RFC 2119 §6). A rewriter then set the final level. **The final level is the one in force.** Any departure from Jev carries a reason.

**Levels:**
- **MUST** includes MUST NOT.
- **SHOULD** includes SHOULD NOT.
- **MAY** is an option.
- **plain** means no keyword; the sentence is left as prose.

Skills appear in rewrite order.
