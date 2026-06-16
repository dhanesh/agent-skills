# Research grounding — what the literature says, and which slot it hardens

This is the evidence map for the loop-spec checklist (`checklist.md`, LSC-1…LSC-10). Every non-obvious design rule in this skill traces to a finding below. Use it two ways: to **justify** a design choice ("why must the evaluator be external?") and to **audit** a loop against what's empirically established. Findings are tagged **[E]** established/replicated, **[A]** practitioner/anecdotal (engineering retrospectives, vendor guidance), **[C]** contested/task-dependent.

The single most important meta-finding: **a loop is only as trustworthy as its evaluation signal and its termination guarantee.** Most of the literature, across all four families, converges on that.

---

## A. Evaluation & self-correction — the biggest correctness gap (hardens LSC-5, LSC-2)

The intuition "let the model critique and fix its own work" is the *least* supported part of the loop folklore. The evidence is blunt:

- **[E] Intrinsic self-correction without an external signal often DEGRADES reasoning.** Huang et al., *Large Language Models Cannot Self-Correct Reasoning Yet*, ICLR 2024 (arXiv:2310.01798). Told to "review and revise" with no external signal, models flip correct answers to wrong ones. → **Design rule:** never close the loop on self-critique alone for objective tasks; gate revisions on an external verification signal (tests, a tool, retrieval, a separate evaluator). No signal available → prefer single-shot. (LSC-5, LSC-2)
- **[E] Apparent self-correction gains are usually an oracle-stopping artifact.** Huang et al. 2023. Prior positive results used ground-truth labels to decide *when to stop*. → **Design rule:** the stopping criterion must be **honest** — stop on a real verifier passing, a budget cap, or no-change convergence, never on information the deployed loop won't actually have. (LSC-2)
- **[E] Self-evaluation amplifies self-bias / sycophancy.** Xu et al., *Pride and Prejudice: LLM Amplifies Self-Bias in Self-Refinement*, ACL 2024 (arXiv:2402.11436). Models rate their own output higher; the loop inflates *perceived* quality while real quality stalls. → **Design rule:** use a separate evaluator (different model, or at minimum an authorship-blinded critique prompt). The generator must not be its own judge. (LSC-5)
- **[E] External tools/verifiers are what make self-correction work.** Gou et al., *CRITIC*, ICLR 2024 (arXiv:2305.11738). A verify→critique→correct loop grounded in tools (search, code interpreter) yields consistent gains. → **Design rule:** make the critique a **tool call, not an opinion** — run the code, query the source, compute the constraint, feed concrete failures back. (LSC-5, LSC-6)
- **[E] Reflexion gains come from an *external outcome* signal, persisted as memory.** Shinn et al., *Reflexion*, NeurIPS 2023 (arXiv:2303.11366). HumanEval 80%→91% by converting test pass/fail into verbal reflections stored across trials. → **Design rule:** anchor each iteration to an outcome signal and carry a memory of past failures forward so revisions don't repeat them. (LSC-4, LSC-5)
- **[C] Verification is easier than generation — only with leverage.** *Mind the Gap* (ICLR 2025, arXiv:2412.02674). The asymmetry holds when the verifier has tools/info the generator lacked; same-model self-verification on the same task buys little. → **Design rule:** give the verifier asymmetric leverage (tools, reference data, a checklist, or a stronger/different model). (LSC-5)
- **[E] Self-Refine helps open-ended/stylistic tasks, less on reasoning, and needs a capable base model.** Madaan et al., *Self-Refine*, NeurIPS 2023 (arXiv:2303.17651). → **Design rule:** scope intrinsic refinement to subjective outputs with a strong model; for reasoning use tool-grounded critique or sampling+vote. (LSC-5)

## B. Do you even need a loop? (hardens LSC-1, family selection)

- **[E] Self-consistency beats self-correction at equal compute.** Wang et al., *Self-Consistency*, ICLR 2023 (arXiv:2203.11171); Huang et al. 2023. N independent samples + majority vote (GSM8K +17.9%) outperforms spending N calls on sequential critique-revise; "debate" often reduces to majority voting. → **Design rule:** for a task with a verifiable/aggregatable answer, baseline **parallel sampling + vote** before committing to an iterative loop. The cheapest sound loop is sometimes no loop. (LSC-1)

## C. Autonomous loops — grounding, decomposition, memory (hardens LSC-4, LSC-5, drift/runaway)

- **[E] Ground every step in a real observation (ReAct).** Yao et al., arXiv:2210.03629 (2022). Pure chain-of-thought reasons from internal state and drifts/hallucinates; reason→act→observe lets external feedback correct the plan each step. → **Design rule:** structure the autonomous loop as reason→act→observe; feed tool/environment ground truth back before the next decision. (LSC-5, drift)
- **[E] Decompose only as-needed (ADaPT).** Prasad et al., NAACL 2024 (arXiv:2311.05772). Recursively decompose a subtask *only when the executor fails it* (+33% over baselines). → **Design rule:** try to execute first, decompose lazily on failure — avoids the upfront task-list explosion that sank BabyAGI-style planners. (LSC-4)
- **[E] Plan before acting on multi-step goals.** Wang et al., *Plan-and-Solve*, ACL 2023 (arXiv:2305.04091). → **Design rule:** for non-trivial goals, generate an explicit plan the loop tracks against; combine with lazy decomposition. (LSC-1, LSC-4)
- **[E] Deliberate search + backtracking for hard/irreversible steps (Tree of Thoughts).** Yao et al., NeurIPS 2023 (arXiv:2305.10601). Greedy generation fails tasks needing lookahead (74% vs 4% on Game of 24). → **Design rule:** when a step has high branching or irreversibility, explore + evaluate alternatives and abandon dead ends instead of committing greedily. (LSC-5)
- **[E] Accumulate verified skills; reuse, don't re-derive (Voyager).** Wang et al., NeurIPS 2023 (arXiv:2305.16291). A library of executable skills, added only after self-verification, compounds capability. → **Design rule:** persist successful action sequences as reusable, self-verified skills; check the library before re-deriving. (LSC-4)
- **[A] Termination + repetition are the dominant autonomous failures.** Anthropic, *Building Effective Agents* (2024); AutoGPT/BabyAGI retrospectives (300+ calls with no output; perfectionist re-improvement; looping identical queries). → **Design rule:** mandatory hard backstop (LSC-3) + explicit no-progress/repetition detection (LSC-5); prefer the simplest sufficient architecture and reserve full autonomy for genuinely open-ended step counts. (LSC-3, LSC-5)

## D. Multi-agent — failures are structural, not IQ (hardens LSC-8, deadlock, cost)

- **[E] MAST failure taxonomy.** Cemri et al., *Why Do Multi-Agent LLM Systems Fail?*, NeurIPS D&B 2025 (arXiv:2503.13657). 14 failure modes in 3 categories: **Specification & System Design 41.8%, Inter-Agent Misalignment 36.9%, Task Verification 21.3%** (κ=0.88). Tactical prompt/orchestration tweaks gave only **9.4–15.6%**. → **Design rule:** audit a multi-agent loop against the three buckets and budget design effort ~40/37/21 across spec, coordination, verification — not on "use a smarter model." A prompt patch will not fix a coordination defect. (audit mode, LSC-10)
- **[E] Multi-agent burns ~15× tokens; use only for parallelizable + cheaply-verifiable subtasks.** Anthropic, *How we built our multi-agent research system* (2025): multi-agent ≈ 15× chat tokens, token usage explains ~80% of performance variance. → **Design rule:** default to a single agent; go multi-agent only when subtasks are independent/parallel **and** results are cheaply verifiable. Tightly-coupled work (most coding) → one agent. Budget at the system level. (LSC-9, family selection)
- **[E] Structure inter-agent handoffs to curb error cascade.** Hong et al., *MetaGPT*, ICLR 2024 (arXiv:2308.00352); ChatDev. Schema-constrained artifact handoffs beat free-form chat; naive chaining makes hallucination cascading. → **Design rule:** define typed/structured message contracts and handoff validation between agents — never raw NL chaining. (LSC-6, LSC-7)
- **[E] Guard against role flipping / instruction drift.** Li et al., *CAMEL*, NeurIPS 2023 (arXiv:2303.17760). Assistants start *issuing* instructions; conversations deviate. → **Design rule:** re-assert role + objective each turn (persistent system framing); detect and halt on role inversion. (LSC-7, drift)
- **[E] Set explicit conversation-termination conditions.** Wu et al., *AutoGen*, 2023 (arXiv:2308.08155). Ends on task-complete / error-threshold / explicit trigger; `NEVER`/`TERMINATE`/`ALWAYS` human modes. → **Design rule:** define a per-conversation termination predicate AND a hard max-turn backstop; pick the human-in-loop mode deliberately. (LSC-2, LSC-3, LSC-8)
- **[E] Add an independent verification stage — agents fake success.** MAST FC3 (no/incorrect verification, "hallucinating success"). → **Design rule:** make verification a distinct step with concrete pass/fail criteria; the producer never self-certifies. (LSC-5, LSC-6)
- **[E/C] Debate helps verifiable reasoning, but cost scales with rounds×agents.** Du et al., *Multiagent Debate*, ICML 2024 (arXiv:2305.14325). → **Design rule:** use debate only for verifiable reasoning/factuality; cap ~2–3 rounds, ~3 agents; some later work disputes its edge over a strong single agent with self-consistency — treat the win as task-dependent. (LSC-9)

## E. Security — the two-channel boundary is necessary but not sufficient (hardens LSC-7, LSC-6, LSC-8)

This is where the skill's framing most needs sharpening: **wrapping carried content in `<data>` lowers injection probability; it does not remove the capability to be injected.** Safety comes from architecture, least-privilege, and human gates — wrapping is one additive layer.

- **[E] Instructions and data share one channel — the original sin.** Willison, *prompt injection* (2022); Greshake et al., *Not What You've Signed Up For*, ACM AISec 2023 (arXiv:2302.12173) — indirect/2nd-order injection via retrieved/tool content. LLMs follow *any* instruction that reaches the model. → **Design rule:** treat the trusted/untrusted boundary as architectural, not textual; carried data must never redirect control flow. (LSC-7)
- **[E] Delimiting/wrapping alone is NOT a complete defense.** Microsoft *Spotlighting* (Hines et al. 2024, arXiv:2403.14720): delimiting, datamarking, encoding reduce attack success but don't eliminate it. → **Design rule:** use datamarking/delimiting as defense-in-depth, never the sole barrier; pair with least-privilege tools + a human gate on irreversible actions. (LSC-7 + LSC-6 + LSC-8)
- **[E] The Lethal Trifecta.** Willison (2025). An agent becomes a reliable exfiltration tool when it simultaneously has **(1) access to private data, (2) exposure to untrusted content, (3) ability to communicate externally.** → **Design rule:** audit every loop for all three legs; if all present, **break one** (drop external comms, quarantine untrusted content from the privileged path, or scope away private data). Distrust vendor "95% blocked" claims. (LSC-7, LSC-8)
- **[E] Treat ALL tool output — and values *extracted* from untrusted content — as untrusted.** CaMeL (Debenedetti et al., DeepMind 2025, arXiv:2503.18813); OWASP *Insecure Output Handling*. An injected note can override e.g. a recipient address even after a "clean" extraction. → **Design rule:** tag everything entering from a tool as untrusted; track provenance; never feed raw tool output into a control decision or an execution sink (shell/SQL/eval/HTTP) unchecked. (LSC-7, LSC-6)
- **[E] Named secure design patterns — pick the most restrictive that still works.** Beurer-Kellner et al., *Design Patterns for Securing LLM Agents against Prompt Injection*, 2025 (arXiv:2506.08837). Ladder, least→most permissive:
  - **Action-Selector** — model picks from pre-approved actions; no tool output flows back. Strongest; use when no feedback is needed.
  - **Plan-Then-Execute** — fix the action sequence *before* exposure to untrusted content, so injected content can corrupt data but not the *plan*.
  - **Map-Reduce** — isolate untrusted content in sub-agents that return only constrained results (e.g. booleans).
  - **Dual-LLM** (Willison 2023) — privileged LLM (tools, trusted query only) directs a quarantined LLM (untrusted content, no tools); results pass as symbolic references, not raw tokens.
  - **Code-Then-Execute / CaMeL** — compile the trusted query to restricted code with provenance-tracked capabilities; tool calls pass explicit policies (~77% of AgentDojo with *provable* security).
  - **Context-Minimization** — drop the original prompt from context once converted to a structured query so it can't contaminate later steps.
  → **Design rule:** derive the loop's control flow from the trusted prompt **before** touching untrusted data, and default to the most restrictive pattern that still meets the utility need. (LSC-7)
- **[E] Least privilege / excessive agency.** OWASP LLM Top 10 — LLM01 Prompt Injection, LLM06 Excessive Agency. Injection only matters if the agent can *act* consequentially. → **Design rule:** scope tools/permissions/autonomy to the minimum the task needs; require human approval for irreversible/external-effect actions. (LSC-6, LSC-8)

---

## Source index

| Topic | Source |
|---|---|
| Self-correction degrades | Huang et al. 2023 — arXiv:2310.01798 |
| Self-consistency | Wang et al. 2022 — arXiv:2203.11171 |
| Self-bias amplification | Xu et al. 2024 — arXiv:2402.11436 |
| CRITIC (tool-grounded) | Gou et al. 2023 — arXiv:2305.11738 |
| Reflexion | Shinn et al. 2023 — arXiv:2303.11366 |
| Self-Refine | Madaan et al. 2023 — arXiv:2303.17651 |
| ReAct | Yao et al. 2022 — arXiv:2210.03629 |
| ADaPT | Prasad et al. 2024 — arXiv:2311.05772 |
| Plan-and-Solve | Wang et al. 2023 — arXiv:2305.04091 |
| Tree of Thoughts | Yao et al. 2023 — arXiv:2305.10601 |
| Voyager | Wang et al. 2023 — arXiv:2305.16291 |
| MAST taxonomy | Cemri et al. 2025 — arXiv:2503.13657 |
| Anthropic multi-agent (~15×) | anthropic.com/engineering/built-multi-agent-research-system |
| Anthropic building effective agents | anthropic.com/engineering/building-effective-agents |
| AutoGen | Wu et al. 2023 — arXiv:2308.08155 |
| CAMEL | Li et al. 2023 — arXiv:2303.17760 |
| MetaGPT | Hong et al. 2023 — arXiv:2308.00352 |
| Multiagent Debate | Du et al. 2023 — arXiv:2305.14325 |
| Indirect injection | Greshake et al. 2023 — arXiv:2302.12173 |
| Spotlighting | Hines et al. 2024 — arXiv:2403.14720 |
| Lethal Trifecta | simonwillison.net/2025/Jun/16/the-lethal-trifecta |
| CaMeL | Debenedetti et al. 2025 — arXiv:2503.18813 |
| Secure design patterns | Beurer-Kellner et al. 2025 — arXiv:2506.08837 |
| OWASP LLM Top 10 | genai.owasp.org/llm-top-10 |
</content>
</invoke>
