# Software-factory readiness assessment (2026-09-19)

> **Dated snapshot.** This records what the collection could and could not do on 2026-09-19,
> assessed at `feat/bcp14-skills` (16a60f8, merged to `main` as be98fe6). File:line citations
> refer to that commit and will drift. Re-run this assessment, with the same second judge,
> after each roadmap step, and add the new one next to this file rather than editing it.

Read-only assessment by Claude, with a second judge: TypeSafe Jev (`jev-1.13.0`, one batched
request, 3317 input and 621 output tokens). Jev's confidences are reported as returned. Jev is
decision support, not proof (skill-contract commandment 7).

**The owner's goal** is an autonomous indie software factory built from these skills. The
acceptance test for the whole roadmap is three questions that Claude and Jev must both answer
*yes*:

1. **Q1.** Can this repo act as a software factory for whoever installs all or a subset of the skills?
2. **Q2.** Does the README tell a user which skills they need for that?
3. **Q3.** Does unattended mode work? The user keeps full control. When they ask for unattended
   development, the skills ask every question that needs a human decision upfront (during
   requirements and design), then implementation runs autonomously.

**Roadmap steps referenced below:** 1 skill-contract v1 (done, PR #57) · 2 BCP 14 keywords
across all skills (done, PR #59) · 3 spec-first-planning upgrade (decision closure,
`depends_on`/waves, typed constraints, the autonomy-grant writer) · 4 conductor, executor and
verifier agents with gate policy and plugin packaging · 5 release/deploy and operations intake ·
6 business-loop skills (idea validation, support, growth/monetisation, spend governor).

## Since this assessment

The four trust bugs in gap 8 (§7) were fixed on branch `fix/factory-trust-bugs`. Each fix
carries unit tests, eval negatives and an A/B row:

- **world-model-ledger 1.2.0.** The `wm` CLI no longer lets an agent self-certify human
  evidence. `wm validate --by human[:name]` and `wm refute --by human` exit 2, and
  `constraint --assert-valid` records agent-asserted evidence that raises no normative
  confidence. The sanctioned route for human evidence is a marker the user types. Residual, documented:
  a shell-capable agent could still forge a transcript. Real provenance for human decisions is
  a design input for the autonomy grant (§6).
- **base-in-reality 1.2.0.** The refutation vote follows the verdict rubric: any refute
  downgrades a critical/high finding (or one with a missing or unknown severity), two refutes
  downgrade a medium/low one, a missing or crashed vote counts as a refute, and the report
  linter rejects a surviving finding without recorded votes.
- **verifier-installer 1.1.0.** The Python format rail is a real formatter check (`ruff format --check`
  or `black --check`) when the repo already adopts one. Otherwise it is a `make format`
  placeholder that stays red and is reported as unproven. It is never `compileall`, which is a
  syntax check. The Go format rail now fails when `gofmt -l` lists files.
- **bug-autopsy 1.1.0.** The post-mortem linter rejects `evidence: none` and other null or
  placeholder evidence values (`tbd`, `unknown`, `n/a`, `n.a.` and similar), not just a missing label.
- **README Q2 re-judged by Jev after the factory section:** no (0.97) → partly (0.80); 'would a
  new user know which skills to install' P(yes) 0.09 → 0.63; the section judged honest P=0.97.

Nothing else in this document has been re-measured since.

## 1. Verdicts: Claude vs Jev

| Question | Claude | Jev (confidence) | Agree? |
|---|---|---|---|
| **Q1**: a factory for all skills or a subset | **partly**: *no* as a full factory, *partly* for the dev core (stages 2–6, 11) | **no** (0.53; no=0.69, partly=0.31) | **DISAGREE** (one step apart; Jev's confidence is low) |
| **Q2**: does the README tell users which skills they need, per stage | **no** | **no** (0.97) | agree |
| **Q3**: unattended mode, with every question asked upfront and then autonomy | **no** (the pieces exist, but the mechanism does not) | **no** (0.53; partly=0.31) | agree (Jev is unsure) |
| "Would a new user reading only the README know which skills to install for an autonomous factory?" | no | P(yes)=**0.09** | agree |

Why Claude rated Q1 *partly* and Jev rated it *no*: one machine handoff really does work today.
spec-first-planning writes an envelope, `discover` finds crafting-self-prompting-loops, and when
the consumer is absent the result is `NO_CONSUMER` and the run still passes (verified, see §3).
Several prose-linked subsets also form usable pipelines. Jev weighed the missing executor and the
missing business stages more heavily. Against the owner's literal bar ("act as a software
factory"), Jev's *no* is defensible. The difference is where the bar sits, not what the evidence
says.

## 2. Stage × skill matrix

Legend: **P** does the stage's core job · p does part of it (audit, advice, a slice) · none.

| Stage | Skills (role) | Claude | Jev (conf) | Agree? |
|---|---|---|---|---|
| 1 Idea validation | none. ai-migration-operating-model `qualify`/`economics` covers migrations only (`ai-migration-operating-model/SKILL.md:89-94`) | missing | missing (0.95) | agree |
| 2 Requirements/spec | **P** spec-first-planning (`spec-first-planning/SKILL.md:63-77`) | covered | covered (0.98) | agree |
| 3 Design/plan | **P** spec-first-planning plan (`:78-88`), with no `depends_on`/waves (`assets/schemas/task-plan.v1.json`); p clean-code (architecture advice); p ai-migration control pack (migrations only) | partly | covered (0.57) | **DISAGREE** (Jev is unsure) |
| 4 Build/execute | p crafting-self-prompting-loops *designs* the loop and does not run it (`crafting-self-prompting-loops/SKILL.md:69-74`); p ai-migration fan-out (migrations only, `:117-119`); p tmux-agent-herdr-lite supervises agents; p mockstar-mock (mocks) | partly | partly (0.96) | agree |
| 5 Test/verify | **P** verifier-installer (`verifier-installer/SKILL.md:74-104`); **P** test-safety-net (characterisation tests only). Nothing checks built work against the spec's acceptance criteria. | partly | partly (0.67) | agree |
| 6 Review | p clean-code, p security-posture-audit, p base-in-reality. None of them reviews a diff against the spec or task plan. | partly | partly (0.79) | agree |
| 7 Release/deploy | none. verifier-installer installs CI but not release. agent-ready-rails R9 only *audits* deploy safety (`agent-ready-rails/SKILL.md:60`) | missing | partly (0.34) | **DISAGREE** (Jev is near a coin flip: partly=0.56, missing=0.44) |
| 8 Operate/incident | p bug-autopsy, post-hoc only ("Not for live debugging", `bug-autopsy/SKILL.md:12`); p agent-ready-rails Tier 2 R7–R10 audit (`:54-61`) | partly | partly (0.81) | agree |
| 9 Support/feedback | none | missing | missing (0.95) | agree |
| 10 Growth/monetise | none | missing | missing (0.99) | agree |
| 11 Knowledge/docs | **P** feynman-walkthrough, okf-site-kit, knowledge-gardener, starlight-handbook-kit, bug-autopsy (post-mortems into OKF); world-model-ledger and context-hygiene-kit (agent memory) | covered | covered (0.72) | agree |
| 12 Governance | p security-posture-audit, p base-in-reality, p agent-ready-rails R5/R8/R9 (audit), p crafting LSC-3/LSC-9 (per-loop budget). No spend governor, no gate policy. | partly | partly (1.00) | agree |

Meta and tooling (not a factory stage): repo2skill (skill authoring), tmux-agent-herdr-lite (cockpit).

Tally (Claude): covered 2, partly 6, missing 4. Tally (Jev): covered 3, partly 7, missing 3.

## 3. Handoff graph

### Machine handoffs (skill-contract envelopes)

```
spec-first-planning ──task-plan/v1──▶ crafting-self-prompting-loops
  provides  (SKILL.md:118-120)          consumes (SKILL.md:133-135)
  writes .skill-contract/envelopes/*.json (spec_to_tasks.py --envelope, SKILL.md:89-94)
```

This is the **only** envelope kind, and these are the **only** 2 adopters out of 19 skills
(`grep -l '^## Contract' */SKILL.md`). crafting-self-prompting-loops provides nothing; its loop
design is prose (`:130-131`), so the chain ends there.

Discovery evidence (run 2026-09-19):

- `contract_check.py discover --kind …/task-plan/v1 --from spec-first-planning` returned
  `CONSUMER: crafting-self-prompting-loops (sibling)` and `CONTRACT_RESULT: PASS`. Discovery works.
- The same `discover` run from a temp directory outside every skills root, with no `--from`,
  returned `NO_CONSUMER … give the envelope to the user` and `CONTRACT_RESULT: PASS`. The graceful
  fallback works (C8, `docs/skill-contract/SPEC.md:27-28`).
  **But** copies installed before the contract merged (spec-first-planning 1.0.0 and
  crafting-self-prompting-loops 1.2.0 in the owner's user-scope install) carry no `## Contract`
  block. A real install therefore never discovers a consumer until the skills are reinstalled
  from `main` (PR #57 merged).
- Search roots: `SKILL_CONTRACT_PATH`, sibling, project and user `.agents`/`.claude/skills`, and
  user-scope Claude Code plugins (`contract_check.py:437-449`). Plugin packaging (roadmap step 4) is
  therefore already discoverable.

### Prose-only handoffs (no envelope; the agent must notice them)

```
agent-ready-rails ──findings R1/R2──▶ verifier-installer ──hollow R1──▶ test-safety-net ──seam list──▶ clean-code
   (agent-ready-rails/SKILL.md:95; verifier-installer/SKILL.md:5-12; test-safety-net/SKILL.md:12-14,50)
agent-ready-rails ──R5/R9 loop specifics──▶ crafting-self-prompting-loops   (agent-ready-rails/SKILL.md:95)
spec-first-planning ◀──complements──▶ ai-migration-operating-model ──▶ crafting-self-prompting-loops
feynman-walkthrough ──OKF bundle──▶ okf-site-kit ;  knowledge-gardener maintains both ;  bug-autopsy ──Postmortem──▶ same OKF bundle
context-hygiene-kit ◀──interop──▶ world-model-ledger   (context-hygiene-kit/SKILL.md:58)
security-posture-audit, mockstar-mock, repo2skill ──"see also"──▶ base-in-reality / agent-ready-rails
```

Isolated, with no sibling references: base-in-reality, starlight-handbook-kit, tmux-agent-herdr-lite.

Candidate kinds implied by the prose edges, none of which exist yet: `rails-audit/v1` (agent-ready-rails →
verifier-installer), `verifier-loop/v1` (verifier-installer → test-safety-net / an executor),
`seam-list/v1` (test-safety-net → clean-code), `loop-spec/v1` (crafting → an executor; already named
as the likely next kind in the contract follow-ups), `review-findings/v1` (clean-code, security,
base-in-reality → a verifier/gate), `postmortem/v1`.

## 4. Subset recipes that work today

These work with a human driving the transitions. Only recipe A has a machine handoff.

| # | Recipe | Stages | Handoff | What breaks without a member |
|---|---|---|---|---|
| A | **Plan → loop**: spec-first-planning + crafting-self-prompting-loops | 2, 3, 4 (design only) | task-plan/v1 envelope, validated, claims shown as CLAIMED | Without crafting: `NO_CONSUMER`, and the user gets the envelope path, which is graceful. Without spec-first-planning, crafting asks for the goal and "done" itself (LSC-1). In both cases someone still has to *run* the loop. |
| B | **Make a repo agent-safe**: agent-ready-rails → verifier-installer → test-safety-net → clean-code | 5, 6, 12 | prose | Without verifier-installer, agent-ready-rails' step 5 installs rails ad hoc (`:95`). Without test-safety-net, R1 stays "hollow" (`:95`). Without clean-code, the seam list is an orphan report. |
| C | **Dev core**: B, then A, then clean-code and security-posture-audit review | 2–6 | A is a machine handoff, the rest is prose | There is no executor, so an agent session has to implement the plan by hand. No step checks the result against the spec's acceptance criteria except the task `verify` commands the implementer chooses to run. |
| D | **Migration factory**: ai-migration-operating-model (+ spec-first-planning, crafting, test-safety-net) | 1*, 3, 4, 5 | prose | This is the most complete autonomous pipeline in the repo, with a queue, reviewers and phase gates (`ai-migration-operating-model/SKILL.md:117-122`), but it is migration-only. |
| E | **Knowledge loop**: feynman-walkthrough → okf-site-kit → knowledge-gardener (+ bug-autopsy) | 11, 8 (post-hoc) | files (OKF bundles), not envelopes | Every member works standalone. The gardener degrades to a report with no site. |
| F | **Agent memory**: world-model-ledger + context-hygiene-kit | cross-cutting | hooks | Each works alone; installing both needs the interop order. |

What breaks or degrades when a skill is missing: the **only** mechanised fallback is C8's `NO_CONSUMER`.
Every other dependency is a prose "use X", so a missing sibling is silently skipped. No skill checks
whether its named sibling is installed, and no skill tells the user what they lose.

## 5. README gaps (Q2) and the sections to add

At the time of the assessment the README had the install lines, a one-row-per-skill table and a
Manifold note. It never said "factory", "pipeline", "stage", "skill-contract" or "unattended".

Sections to add:

1. **"Which skills do I need?"** A stage × skill table (the one in §2, trimmed), with a
   `covered / partly / not yet` column so users see the honest boundary (stages 1, 7, 9 and 10 are not yet
   covered).
2. **"Recipes"**: install-line bundles for recipes A, B, C and E, each with a single `npx skills add …
   --skill a --skill b` command and one sentence on the pipeline order.
3. **"How skills hand off (skill-contract)"**: a paragraph plus the kind table (who provides and who
   consumes each kind), a link to `docs/skill-contract/SPEC.md`, the `NO_CONSUMER` fallback, and a note
   that **reinstalling is required** after upgrading, because older installs are invisible to discovery.
4. **"What each skill needs next to it"**: per skill, its upstream and downstream siblings and what
   degrades without them.
5. **"Autonomy and human gates"**: where each skill stops for a human (verifier-installer step 2,
   test-safety-net step 3, the spec-first-planning handoff, crafting LSC-8), and a statement that
   unattended mode is **not yet supported**. Point to the roadmap.
6. **"Roadmap / not covered yet"**: a one-line link to the factory roadmap, so the gaps read as
   planned work rather than omissions.

Status: the README's "Software factory" section, added with this document, covers 1, 2, 3
(without a separate kind table, since there is one kind), 5 and 6 in condensed form. Section 4
(per-skill neighbours) is not done.

## 6. Unattended mode (Q3): gap analysis

### What exists

| Piece | Evidence | Fit for unattended |
|---|---|---|
| Upfront, batched elicitation | spec-first-planning step 1 "Prefer a single batched round of questions" (`spec-first-planning/SKILL.md:63-66`) | Good shape for the "ask everything upfront" phase. But unresolved answers go to *Open questions*, which may be empty or non-empty without blocking (`spec_lint.py:43`, `MAY_BE_EMPTY`), and they are **not carried in the envelope** (task-plan.v1 schema has no `open_questions`/`decisions`). |
| The commandment 10 hook | "MUST propose each handoff and wait for a yes, **unless the user has adopted a gate policy that says otherwise**" (`docs/skill-contract/SPEC.md:32-33`) | The escape hatch is specified, but **no gate-policy format, file or check exists** anywhere. |
| Loop safety | crafting LSC-3 backstop (mandatory), LSC-8 human gate for irreversible actions, LSC-9 budget (`crafting-self-prompting-loops/SKILL.md:52-67`) | Per loop and designed on paper. No pipeline-wide budget and no shared stop rules. |
| Handoff as a gate | crafting treats the confirmed handoff as the LSC-8 approval for the handoff itself only (`:124-125`) | It is a single-hop gate. |
| Per-skill write gates | verifier-installer "Do not write anything before this confirmation" (`verifier-installer/SKILL.md:82-89`); test-safety-net "hard gate … do not proceed past it unconfirmed" (`test-safety-net/SKILL.md:166-167, 479`) | These **block** unattended runs: each would stop mid-pipeline and ask. Neither reads a pre-granted decision. |
| Safe-by-construction steps | test-safety-net never modifies source, "safe to run unattended" (`:469-471`); agent-ready-rails audit steps are read-only (`:67`) | These steps could be auto-approved under a grant (reversibility: `none-needed`). |
| Readiness audit for unattended runs | agent-ready-rails Tier 2 R7–R10, "say plainly whether it is safe to run unattended" (`:54-61, 128`) | A natural **precondition check** before a grant takes effect. |
| Supervision | tmux-agent-herdr-lite detects `blocked reason="needs approval"` (`tmux-agent-herdr-lite/SKILL.md:115,127`) | A human-facing escalation surface. |
| System One offload | SPEC C10 lets Jev take picking, yes/no or scoring decisions after an opt-in (`docs/skill-contract/SPEC.md:33-39`) | Could resolve *low-stakes* judgment calls during autonomy, but never as proof (C7). |

### What is missing

1. **A grant artifact**: somewhere to record the upfront answers and the scope of delegated authority.
2. **A conductor**: something that carries the grant across skills, runs the plan (executor), and
   re-verifies claims independently (verifier, which is what C7 needs so that CLAIMED becomes PROVEN).
3. **A gate policy with reversibility tags**: action classes (read-only, local-reversible, pushes a
   branch, opens a PR, merges, deploys, spends money, sends external messages, deletes) mapped to
   `auto | grant-covered | always-ask`.
4. **Stop and budget rules across the pipeline**: a wall-clock and token/$ cap, a max-iterations cap per
   task, "stop on any red verify after N repairs", "stop on any new human-decision question", and a kill switch.
5. **A decision-closure check**: requirements/design cannot finish while an Open question is unresolved
   and no default has been granted.
6. **Skills that read the grant**: verifier-installer step 2, test-safety-net step 3 and the
   spec-first-planning handoff step each need an "if a valid grant covers this, proceed and log" branch.

### Proposed envelope kind: `autonomy-grant/v1`

This is a proposal, not a shipped kind. It is the design input for roadmap steps 3 and 4.

`predicateType: https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1`

**What it holds (payload):**

```json
{
  "scope": {"repo": ".", "branch_pattern": "factory/*", "plan": {"envelope": "task-plan-v1-…", "sha256": "…"}},
  "decisions": [
    {"id": "D1", "question": "CI provider?", "answer": "GitHub Actions", "source": "spec Open questions Q2"},
    {"id": "D2", "question": "Allowed to add dev dependencies?", "answer": "no"}
  ],
  "defaults": [{"when": "naming/style ambiguity", "rule": "follow clean-code; log the choice"}],
  "gate_policy": {
    "read_only": "auto", "local_reversible": "auto",
    "push_branch": "grant", "open_pr": "grant",
    "merge": "ask", "deploy": "ask", "spend": "ask", "external_message": "ask", "delete": "ask"
  },
  "budget": {"wall_clock_min": 240, "max_tokens": 3000000, "max_usd": 25, "max_repairs_per_task": 3},
  "stop_on": ["new_human_decision", "verify_red_after_repairs", "budget_exhausted",
              "stale_subject", "unvalidated_envelope", "grant_expired"],
  "expires_at": "2026-09-20T18:00:00Z",
  "system_one": {"allowed": true, "decision_kinds": ["pick-one", "yes-no"], "data_sent": "task titles only"}
}
```

Subjects: the pinned spec and task-plan envelope (C5). If either changes, the grant is **stale** and
autonomy stops.

**Who writes it:** only the **user**, through a planning skill. spec-first-planning (upgraded in
roadmap step 3) ends its requirements/design phase by collecting every Open question and every gate
class into one batched round. It then *proposes* the grant, and writes it only after an explicit yes.
Its single assertion is `assertedBy: {"human": "<user>"}` with `test: "grant-accepted"`. By C7 a grant
asserted by a skill is **not** proof, so the checker must reject a grant whose acceptance is not
attributed to a human. This is the same self-certification hole the world-model-ledger fix closed
for its CLI (see "Since this assessment"). The residual it documents, that a shell-capable agent
can forge a transcript, applies here too, so the grant needs a provenance mechanism an agent
cannot mint.

**Who checks it:**
- `contract_check.py` gains a `check-grant` command, with vectors: signature/attribution present, not
  expired, subjects not stale, `gate_policy` only *loosens* classes the spec allows (it can never
  auto-approve `merge`, `deploy`, `spend` or `delete` unless the user named that class explicitly), and the
  budget is finite.
- The **conductor** checks it before every consequential action: it maps the action to a
  reversibility class and then applies `auto`, `grant` (proceed and log to an append-only
  `autonomy-log`), or `ask`.
- Every consuming skill's hard gate (verifier-installer step 2, test-safety-net step 3, the
  spec-first-planning handoff) reads the grant through the checker, never by parsing the prose itself
  (C9: the grant is data).

**What still stops (never grantable in v1):**
- any action tagged irreversible or externally visible that is not explicitly listed (the C10 default and LSC-8);
- a new human-decision question that no `decisions`/`defaults` entry covers. The run parks, pings the
  human (tmux-agent-herdr-lite `blocked`), and **keeps working on other tasks**;
- an exhausted budget or expiry, which triggers the LSC-3 backstop;
- stale subjects, meaning the spec or plan changed;
- `UNVALIDATED` envelopes (C9) and red verify runs after `max_repairs_per_task`;
- the agent-ready-rails Tier 2 precondition: if R8 (blast radius) or R9 (kill switch) scores 0, a grant
  can cover only `read_only`/`local_reversible`.

## 7. Ranked gaps, mapped to the roadmap

Ranked by how much each blocks the acceptance test (Q1, Q2, Q3).

| Rank | Gap | Blocks | Roadmap step |
|---|---|---|---|
| 1 | **No conductor/executor/verifier**: nothing runs a task plan end to end, and nothing independently re-verifies claims, so CLAIMED never becomes PROVEN (C7). | Q1, Q3 | 4 |
| 2 | **No autonomy grant or gate policy**: C10's "unless" has no artifact. Per-skill hard gates (verifier-installer, test-safety-net, the handoff) cannot be pre-approved. No reversibility tags, no pipeline budget or stop rules. | Q3 | 4, plus the grant *writer* in 3 |
| 3 | **Decision closure missing in planning**: Open questions do not block, are not in the envelope, and there are no `depends_on`/waves or typed constraints, so an executor can't schedule the plan or know that every decision was made. | Q3, Q1 | 3 |
| 4 | **Contract adoption is 2 of 19, with one kind**: every other link is prose. There is no `loop-spec`, `rails-audit`, `verifier-loop`, `seam-list` or `review-findings` kind, so "discover and use each other" is true for one edge only. Installed copies predate the contract. | Q1 | 3 and 4 (kinds), plus the portability retrofit |
| 5 | **README has no stage map, recipes, handoff table, or autonomy statement** (Jev P(yes)=0.09). This is cheap to fix and should track each roadmap step. | Q2 | any; do it now and update it per step. *Partly addressed (Jev re-judged: partly, 0.80); section 4 open.* |
| 6 | **Release/deploy and ops intake missing**: CI exists, but no release, rollout, rollback or live incident intake. bug-autopsy is post-hoc only. | Q1 | 5 |
| 7 | **Business loop missing**: idea validation, support/feedback, growth/monetisation/analytics, spend governor. | Q1 (full factory) | 6 |
| 8 | **Trust bugs that would poison unattended evidence**: world-model-ledger `--by human` self-certification; base-in-reality 2-of-3 vote vs the unanimity rubric; verifier-installer python "format" = `compileall` (a syntax check, not a formatter); bug-autopsy accepting `evidence: none`. In unattended mode, weak oracles become false PROVEN. | Q3 (quality of autonomy) | *Fixed on `fix/factory-trust-bugs`; see "Since this assessment".* |
| 9 | **Portability**: hard-coded `python3` in SKILL.md commands; the macOS python3 stub. | Q1 (installs on other machines) | portability retrofit |

### Suggested sequencing against the acceptance test

- Now (cheap): README sections 1–6 (§5), each marked "not yet" where true. This should turn Q2 into *partly*.
- Step 3: spec-first-planning adds decision closure, depends_on/waves, typed constraints, and the
  **grant writer** (the upfront round). It also defines `autonomy-grant/v1` and `check-grant` in the contract.
- Step 4: conductor, executor and verifier agents plus the plugin; gate policy enforcement; grant readers in
  verifier-installer and test-safety-net; a `loop-spec/v1` kind. This is the step that can turn Q3 into *yes* for
  the dev slice, and Q1 into *yes* for the dev-core subset.
- Steps 5–6: close stages 1, 7, 8, 9, 10 and 12, which a *yes* on Q1 for a full factory needs.
- Re-run this assessment with Jev after each step.
