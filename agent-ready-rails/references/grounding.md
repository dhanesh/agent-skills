# Grounding — why "intelligence needs rails"

The thesis behind this skill comes from one well-documented production case and converges with the research grounding already in this repo's other skills. Findings are tagged **[A]** practitioner/vendor retrospective, **[E]** established/replicated, **[C]** contested/task-dependent — matching the convention in `crafting-self-prompting-loops/references/literature.md`.

The meta-claim: **a powerful agent in a weak engineering system produces faster chaos; the same agent in a disciplined system produces leverage.** The differential is not model capability — it is the rails. Each rail below names the evidence and the sibling skill that owns its deeper spec.

---

## The anchoring case — Spotify's Honk

Spotify runs *Honk*, a background coding agent built on the Claude Agent SDK, across a ~20M-line codebase; it opens verified PRs for migrations, upgrades, and fixes. Sources: Spotify Engineering's *Background Coding Agents* series — *Context Engineering* (Nov 2025) and *Feedback Loops* (Dec 2025) — and the June 2026 Niklas Gustavsson × Boris Cherny interview (youtu.be/9DHZLw5653E). All headline figures are vendor-reported; treat as a strong practitioner signal, not an independently replicated result.

## §1 — Runnable verifiers / feedback loop (R1) [A]

The single biggest lever Spotify reports: adding a closed verify→repair loop — **format, build, and test invoked as tool calls** (in Claude Code, wired as a **Stop hook** so the agent cannot terminate on unverified output) — moved end-to-end PR success from ~20–30% to ~80%. The win was not a smarter model; it was making the loop *unable to stop on unverified output*. This is the production-scale instance of the established result that self-correction needs an **external verification signal**, not introspection (Huang et al. 2023; CRITIC; Reflexion — see `crafting-self-prompting-loops/references/literature.md` §A). → R1 is load-bearing because it *is* the loop.

## §2 — Green-CI ground truth (R2) [A]

Honk has access to trusted tools including the ability to **run builds in CI across multiple operating systems** to confirm a change holds beyond the agent's local environment. The deterministic verifier the deployed loop will always have (compiler, tests, CI) is the durable evaluation signal — Spotify first bootstrapped with an **LLM judge** (~25%→80%) and then *removed* it once build/test/CI were strong enough to carry the signal. → R2 makes "correct" reproducible and authoritative.

## §3 — House style the agent can copy (R3) [A/E]

Claude worked at 20M-line scale partly because it could **inspect nearby code and copy existing patterns** — agents do better when the codebase has a coherent house style. This is the practitioner form of the established finding that structure makes model behavior tractable (a consistent control surface lets you change one thing and watch the result — the "Prompting Playbook" thesis this repo's gates enforce). → R3 turns the model's mimicry from a liability into leverage.

## §4 — Navigable context (R4) [A/E]

The first Honk retrospective is specifically about **context engineering** — getting the right slice of a vast codebase in front of the agent cheaply. This is the same discipline `context-hygiene-kit` installs (anti-bloat / anti-rot): keep working memory lean and the high-salience facts durable. → R4 bounds context cost and keeps the agent reasoning from the right files. Cross-link: `context-hygiene-kit` for the in-session mechanism.

## §5 — Scoped trusted tools (R5) [E]

Honk runs with a **set of trusted tools** rather than open-ended capability. This is least-privilege / excessive-agency avoidance (OWASP LLM06) and the trusted/untrusted two-channel boundary — already specified in `crafting-self-prompting-loops` LSC-6/LSC-7 and grounded there (CaMeL, Spotlighting, the lethal trifecta, the secure-pattern ladder). → R5 reuses that spec; this skill only checks whether a *repo's* agent harness honors it.

## §6 — Human checkpoints & reversibility (R6) [E]

Honk opens **PRs** — agent output lands through a reviewable, reversible path, not a direct push. This is the human-safety-gate (`crafting-self-prompting-loops` LSC-8) plus reversibility. The oversight literature warns the gate degrades when it is a rubber stamp (confirmation/automation bias, AAAI 2025), so R6 scores the *quality* of the gate (adversarial review on consequential diffs), not merely its presence. → R6 is the catch when R1/R2 miss and the cheap undo when they don't.

---

## Tier 2 — the Operate rails (merge → production)

R1…R6 get an agent to a *verified, reviewable PR* — the whole job when a human merges and a normal release process takes over. But Honk's actual mode is **unattended operation at scale**, and once no reviewer watches each change, four more rails govern the surface past the merge boundary. These lean more on **established operational-safety and security practice** than on direct Honk quotes; they are tagged accordingly, and the connection to the Honk retrospectives is drawn where it genuinely exists rather than asserted. Score this tier **only** when the goal is autonomous/background operation against a production system.

### §7 — Runtime observability & audit (R7) [E]

An autonomous change you cannot reconstruct is an incident you cannot diagnose. Honk's PR-per-change is itself an audit artifact (§6), but the *run* behind it — prompts, tool calls, decisions — needs to be traceable and replayable for the change to be accountable after the fact. This is standard SRE observability plus the auditability leg of AI-system governance; a **distinct agent identity** is the precondition for attributing a change to the agent at all. → R7 makes an unattended change accountable.

### §8 — Blast-radius containment (R8) [E]

R5 scopes the *toolset*; R8 scopes the *environment the tools run in*. Least-privilege, ephemeral, egress-restricted execution is the containment half of excessive-agency avoidance (OWASP LLM06) and the standard sandbox posture — an agent that never holds standing prod credentials cannot leak or wreck them. Honk confirming builds **in CI across multiple operating systems** (§2) is itself a form of isolated, reproducible execution rather than trusting one local host. → R8 bounds what a wrong run can reach.

### §9 — Deploy-path safety & kill switch (R9) [E]

R6's reversibility stops at merge; production reversibility is **staged rollout + automated rollback + a stop button**. Canary / feature-flag rollout with automated rollback on SLO regression is standard progressive-delivery practice; a fleet-wide kill switch and spend/rate/concurrency caps are the operational analog of the loop's hard-stop backstop (`crafting-self-prompting-loops` — the mandatory termination invariant) lifted from a single loop to the whole program. → R9 is the cheap undo and the emergency stop for the production surface.

### §10 — Continuous re-verification & telemetry (R10) [A/E]

The single strongest Honk signal — end-to-end success moving ~20–30% → ~80% (§1), and later *removing* the LLM judge once build/test/CI carried the signal (§2) — is itself a **program metric watched over time**: you only know a rail worked because you measured the number move. Rails also rot (a required check disabled, a map drifted, a token scope widened). Re-verifying readiness on a schedule and pausing autonomy when program quality (acceptance, revert rate, time-to-green) regresses is R1/R2's external-verification discipline applied to the *program* instead of the single change. Cross-link: `context-hygiene-kit` and `world-model-ledger` already install *continuous* in-session mechanisms; R10 asks for the same continuity around the readiness rails themselves. → R10 keeps "agent-ready" true after the audit, not just on the day of it.

---

## How this skill relates to the others

This skill is the **environment** audit; the sibling skills build the **loop** that runs in it:

| Concern | Owned by |
|---|---|
| Is the *repo* ready for agents? (Tier-1 rails, author→merge) | **this skill** |
| Is the *agent program* safe to run unattended? (Tier-2 rails, merge→prod) | **this skill** |
| Is the *loop* sound? (termination, evaluation, injection) | `crafting-self-prompting-loops` |
| Does in-session context stay lean & rot-proof? | `context-hygiene-kit` |
| Are the codebase's claims true against real-world norms? | `base-in-reality` |

A rail finding that points at loop design (R5, and R9's kill-switch backstop) defers to `crafting-self-prompting-loops` rather than restating it.
</content>
