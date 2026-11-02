# Spec: From "partly" to "covered" in the software-factory stage table

## Problem
The root README's table "Which skills cover which stage" marks 10 of 13 stages `partly`.
A reader cannot tell from the table what keeps a row at `partly`, or what work would
change it. There are four causes, and the table does not separate them:

1. **No rubric.** Nothing in the repository defines `covered`, `partly` or `missing`. The
   dated assessment (`docs/factory/2026-09-19-assessment.md`, section 2) has only the legend
   "P does the stage's core job · p does part of it".
2. **A blanket rule.** The README says: "Each stage row stays partly, because you still choose
   the next skill and confirm each handoff." That rule is about the handoffs between stages,
   not about any one stage. It also contradicts the table: Requirements / spec and
   Knowledge / docs are `covered` under the same handoff model.
3. **Stale rows.** Six rows (Design / plan, Build / execute, Test / verify, Review,
   Operate / incident, Governance) were last judged on 2026-09-19. The skills changed after
   that date. Two cells are now false: the Review row says "None reviews a diff against the
   spec", but factory-conductor's reviewer judges each diff against its requirement. The
   Governance row says "No gate policy", but the autonomy grant is the gate policy of
   skill-contract commandment 10.
4. **Real gaps.** Some limits are work that nobody has done yet. Others are owner decisions,
   and work cannot remove them (launch and pricing stay manual; spend, merge, deploy and
   external messages always ask).

## Users
- The repository owner (Dhanesh Purohit), who decides what the factory automates and needs
  an honest table to choose the next work.
- A person who installs a subset of the skills and reads the table to learn what each stage
  does for them, and what it leaves to them.
- The second judge (TypeSafe Jev) and Claude, who re-judge the table after each change.

## Goals
- Every status in the table follows a written rubric.
- Every row that is not `covered` names the gap that blocks `covered`, and the gap is either
  an owner decision or linked work.
- No cell states something that is false at the current `main`.
- The gate fails when a status cell breaks the rubric's form.
- Each buildable gap from the gap analysis below is closed, or is recorded as an accepted
  limit by an owner decision.

## Non-goals
- Removing any human gate. Merge, deploy, spend, external messages and delete stay with the
  human, and launch and pricing stay manual. Those are owner decisions from AC10
  (2026-10-13), and a status rubric must not count them against a stage.
- A full incident-response skill. Whether the factory owns live incidents is open question
  Q1. This spec builds none until the owner decides.
- Dedicated Jira, Linear or app-store readers. The owner chose conversion recipes (AC10).
- `proved` or `bounded` proof backends for Python, TypeScript or Go. No mature prover for
  those languages fits the stdlib-only, offline gate.
- Unattended stage-to-stage orchestration (one run from idea to production). That is the
  Q3 question, and the new Stage handoffs row (R2) reports it.

## Gap analysis
<!-- Extra section: ignored by spec_lint.py and spec_to_tasks.py. Evidence is at main
     0be6d02 (2026-11-03). -->

Each gap has a kind:

- **stale**: the cell is out of date, and a re-judgment can fix it with no new code.
- **decision**: a limit that the owner chose, and that work cannot remove.
- **build**: work that nobody has done yet.
- **accepted**: a limit that does not block the stage's core job, but that the table names.

| Row | Status now | Gap | Kind | Evidence | Expected after this spec |
|---|---|---|---|---|---|
| All stage rows | partly | "You still choose the next skill and confirm each handoff" | stale (rule misplaced) | README "Which skills cover which stage" | the rule moves to a Stage handoffs row (R2, R3) |
| Idea validation | partly | You run every experiment; the agent never publishes, sends or spends | decision | AC10 owner decisions | covered, with limits |
| Idea validation | partly | Results are claimed, not proven | accepted | `ideas record ... --evidence-file` pins a file, not the result | covered, with limits |
| Design / plan | partly | 2026-09-19 reason "no `depends_on`/waves" is closed: `spec_to_tasks.py --waves` exists | stale | assessment section 2, row 3 | covered (re-judged, R13) |
| Build / execute | partly | 2026-09-19 reason "no executor" is closed: factory-conductor runs a plan to an open PR | stale | README "Unattended mode: partial" | covered, with limits (re-judged) |
| Build / execute | partly | Re-entry needs an always-on host; remote resume is deferred | decision | AC10 | covered, with limits |
| Test / verify | partly | 2026-09-19 reason "nothing checks work against the acceptance criteria" is closed: the conductor re-runs each criterion's command and the evidence gate drives the app | stale | factory-conductor SKILL.md, steps 5 and 7 | covered, with limits |
| Test / verify | partly | verifier-installer does not detect proof backends | build (G1) | proof-tier spec decisions D7, D8 | closed by R9 |
| Test / verify | partly | No `proved` or `bounded` backend outside Rust | accepted | see Non-goals | named limit |
| Review | partly | "None reviews a diff against the spec" is false | stale | factory-conductor SKILL.md, "The reviewer brief" | R6 |
| Review | partly | The spec-anchored review runs only inside a conductor run; an attended PR has no such review | build (G2) | the reviewer brief needs `state.json` | closed by R8 |
| Operate → planning | partly | Squash and rebase merges need the human's `resolve` | build (G3) | ops-intake SKILL.md, state `planned+needs-resolve` | closed by R10 |
| Operate → planning | partly | On demand only: nothing sweeps the sources on a schedule | build (G4) | ops-intake SKILL.md, "On demand only" | closed by R15 |
| Operate → planning | partly | MCP calls are not provably read-only | accepted | ops-intake SKILL.md | named limit |
| Operate / incident | partly | No skill responds to a live incident; bug-autopsy is post-hoc only | decision needed (Q1) | bug-autopsy SKILL.md | R12 |
| Support / feedback | partly | You export the messages; nobody replies through the factory | decision | AC10; `external_message` is never grantable | covered, with limits |
| Support / feedback | partly | The agent chooses the topics; redaction is a safety net | accepted | ops-intake `feedback-jsonl` | named limits |
| Growth / monetise | partly | You export the data and set the thresholds; launch and pricing stay manual | decision | AC10 | covered, with limits |
| Growth / monetise | partly | No forecasts or statistics | accepted | growth-review SKILL.md | named limit |
| Governance | partly | "No gate policy" is false: the autonomy grant is the commandment 10 gate policy | stale | `docs/skill-contract/SPEC.md`, "The autonomy grant (commandment 10)" | R7 |
| Governance | partly | Token and dollar caps are metered from usage that the agent claims | accepted | factory-conductor "Spend metering" | covered, with limits |

Only four gaps need new code (G1 to G4). One needs an owner decision (Q1). The rest need a
rubric, accurate text and a re-judgment.

**Second opinion (Jev).** Jev (`jev-latest`, one batched call of 19 Choice questions,
2026-11-03) labelled each gap independently. Its answers are decision support, not proof.

- Jev agrees at confidence 0.6 or more on 9 gaps: the four stale rows for Design / plan,
  Build / execute, Review and Governance; the decisions for idea experiments, support and
  growth; and the builds G1 (0.79), G2 (0.83) and G3 (0.72).
- Jev disagrees at confidence 0.6 or more on 3 gaps:
  - On-demand intake: Jev says build (0.77). This spec adopts it as G4, because
    factory-conductor already ships a re-entry timer that a sweep can reuse.
  - No `proved` or `bounded` backend outside Rust: Jev says build (0.69). This spec keeps
    it a non-goal, and asks the owner in Q5.
  - Live incidents: Jev says build (0.69). This spec keeps it an owner decision (Q1), and
    records Jev's lean there.
- Jev is uncertain (confidence below 0.6) on 7 gaps, and this spec keeps its own label for
  each: the blanket handoff rule (0.15), Test / verify stale (0.37), claimed idea results
  (0.44), the re-entry host (0.56), growth thresholds (0.60 decision, same as this spec),
  growth statistics (0.52) and claimed spend usage (0.23).

## Constraints
- B1 [invariant]: A status never claims more than the evidence shows. When Claude and Jev disagree, the more conservative verdict goes into the README.
- B2 [invariant]: A change to a status removes no human gate. Merge, deploy, spend, external messages, delete, launch and pricing stay with the human.
- B3 [goal]: A reader learns from one row why that stage is not `covered`.
- T1 [boundary]: New checks are stdlib-only Python or POSIX shell, offline, and run inside `make gate`.
- T2 [goal]: Each status cell has a form that a script can check.
- S1 [invariant]: The payload sent to Jev for a re-judgment carries no budget figures, secrets or customer data.
- O1 [goal]: Each slice (the rubric and text, G1, G2, G3, G4, the re-judgment) lands as its own pull request with a passing `make gate`.

## Required truths
- RT1 [SPECIFICATION_READY]: A written rubric defines each status value, and stage rows are judged on the stage's core job only (parent: OUTCOME; maps_to: B1, B3; reqs: R1, R2, R3; confidence: 0.85; check: grep -n "Status rubric" README.md)
- RT2 [SPECIFICATION_READY]: No cell is false at the current main (parent: OUTCOME; maps_to: B1; reqs: R6, R7; confidence: 0.9; check: grep -c "None reviews a diff against the spec" README.md prints 0)
- RT3 [SPECIFICATION_READY]: Each non-covered row names its blocking gap, and the gate checks the form (parent: RT1; maps_to: B3, T1, T2; reqs: R4, R5, R14, R16; confidence: 0.8; check: python3 scripts/gates/factory_status.py README.md exits 0)
- RT4 [SPECIFICATION_READY]: The four buildable gaps are closed without a new human gate removed (parent: OUTCOME; maps_to: B2, T1, O1; reqs: R8, R9, R10, R11, R15; confidence: 0.7; check: make gate prints GATE_RESULT: PASS)
- RT5 [SPECIFICATION_READY]: The incident-response scope is an owner decision recorded in the README (parent: RT1; maps_to: B2, B3; reqs: R12; confidence: 0.6; check: the Operate / incident row names the decision)
- RT6 [SPECIFICATION_READY]: The README statuses come from a new dated re-judgment by both judges (parent: OUTCOME; maps_to: B1, S1, O1; reqs: R13; confidence: 0.75; check: a docs/factory/*-stage-rejudgment.md file names a verdict per row)

## Requirements
- R1: The root README must define the status values `covered`, `covered, with limits`, `partly`, `missing` and `out of scope, by design` in a paragraph headed "Status rubric", directly above the stage table. [where: README.md]
- R2: The stage table must have a "Stage handoffs" row whose status describes how a stage passes work to the next one. [where: README.md] [after: R1]
- R3: The README must not contain the sentence "Each stage row stays partly, because you still choose the next skill and confirm each handoff." [where: README.md] [after: R2]
- R4: Each stage row whose status is not `covered` must contain the word "Limits:" and name at least one gap. [where: README.md] [after: R1]
- R5: A gate script `scripts/gates/factory_status.py` must exit non-zero when a stage table status is outside the rubric's five values, or when a row that is not `covered` has no "Limits:" text. [where: scripts/gates/factory_status.py, Makefile] [after: R1]
- R6: The Review row must name factory-conductor's reviewer, which judges each task's diff against its requirement, and must not say that no skill reviews a diff against the spec. [where: README.md]
- R7: The Governance row must name the autonomy grant as the gate policy, and must not say "No gate policy". [where: README.md]
- R8: factory-conductor must print a filled reviewer brief for a task of a plan envelope and a base ref without an initialised run, so an attended pull request can get the spec-anchored review. [where: factory-conductor/assets/conductor.py, factory-conductor/SKILL.md]
- R9: verifier-installer's stack detection must print one `BACKEND: <tool>` line for each proof backend it finds among Verus, Kani and the property-test libraries proptest (Rust), Hypothesis (Python), rapid (Go) and the JavaScript one that the proof-tier spec names. [where: verifier-installer/assets/detect_stack.py, verifier-installer/SKILL.md]
- R10: ops-intake must mark an item resolved when a verified release holds a squash-merge commit whose subject ends with the number of the pull request that the item's plan opened. [where: ops-intake/assets]
- R11: Each of R5, R8, R9, R10, R15 and R16 must add at least one row to `scripts/ab-validate.py`, labelled with the owning skill's directory name or `gates`. [where: scripts/ab-validate.py] [after: R5, R8, R9, R10, R15, R16]
- R12: The Operate / incident row must state the owner's decision on live incident response, as `out of scope, by design` or as a named skill. [where: README.md] [after: R1]
- R13: A new dated file `docs/factory/<date>-stage-rejudgment.md` must record, for each stage row, the verdict of Claude and of Jev, and the README status must equal the more conservative of the two. [where: docs/factory] [after: R1, R2, R3, R4, R6, R7, R8, R9, R10, R12, R15, R16]
- R14: The gate self-tests in `scripts/gates/test_gates.sh` must include one failing fixture for each refusal in R5 and R16. [where: scripts/gates/test_gates.sh] [after: R5]
- R15: ops-intake must ship a scheduled sweep that runs its configured sources at an interval of 15 to 1440 minutes and writes only to its local queue. [where: ops-intake/assets, ops-intake/SKILL.md]
- R16: Each item in a stage row's "Limits:" note must carry one tag, `[decision]` (a limit the owner chose), `[trust]` (a claim the tool cannot check) or `[open]` (work nobody has done), and `scripts/gates/factory_status.py` must exit non-zero for an untagged item, and for a row whose items are all `[decision]` but whose status is not `covered`. [where: README.md, scripts/gates/factory_status.py] [after: R5]

## Acceptance criteria
- R1: `grep -n "Status rubric" README.md` prints one line above the line `| Stage | Skills | Status |`, and the paragraph names all five values. [cmd: grep -n "Status rubric" README.md]
- R2: The stage table has a row that starts with `| Stage handoffs |`. [cmd: grep -n "| Stage handoffs |" README.md]
- R3: `grep -c "Each stage row stays partly" README.md` prints `0` and exits 1.
- R4: `python3 scripts/gates/factory_status.py README.md` exits 0, and an outside reader finds "Limits:" in every row that is not `covered`. [cmd: {python} scripts/gates/factory_status.py README.md]
- R5: Run on a copy of the README with one status changed to `mostly`, the script exits 1 and prints the row name. Run on a copy with "Limits:" removed from a `partly` row, it exits 1. [cmd: bash scripts/gates/test_gates.sh]
- R6: `grep -c "None reviews a diff against the spec" README.md` prints `0`, and the Review row contains `factory-conductor`.
- R7: `grep -c "No gate policy" README.md` prints `0`, and the Governance row contains "autonomy grant".
- R8: Run in a fixture repo with a plan envelope and no run directory, the new conductor command exits 0 and prints the task's requirement text, each verify command and a `git ... diff` command against the base ref. Run with an unknown task id, it exits 2. [cmd: {python} factory-conductor/assets/test_conductor_review_brief.py]
- R9: On a fixture with `Cargo.toml` that names `proptest` and `kani`, the detector prints `BACKEND: proptest` and `BACKEND: kani`. On a fixture with neither, it prints no `BACKEND:` line. [cmd: {python} verifier-installer/assets/test_detect_stack.py]
- R10: In a fixture where the release holds a commit with subject `Add X (#42)` and the item's plan opened PR 42, the item ends `resolved`. With subject `Add X (#43)`, the item stays `planned+needs-resolve`. [cmd: {python} ops-intake/assets/test_intake_squash.py]
- R11: `make ab-validate` prints at least one `IMPROVED` row for each of the six changes, and `AB_RESULT: PASS`.
- R12: The Operate / incident row contains either `out of scope, by design` or the name of a skill that responds to a live incident.
- R13: The rejudgment file exists, has one verdict pair per stage row, and each README status equals the more conservative verdict of its pair. The file's Jev payload contains no figure from `budget/budget.md`.
- R14: `bash scripts/gates/test_gates.sh` passes, and its output names one factory-status fixture for the unknown status and one for the missing limits. [cmd: bash scripts/gates/test_gates.sh]
- R15: In a fixture repo, a sweep set to 15 minutes runs the sources, adds new items to the queue and changes no tracked file. An interval of 10 or 1500 minutes exits 2. [cmd: {python} ops-intake/assets/test_intake_schedule.py]
- R16: On a copy of the README with one "Limits:" item untagged, the script exits 1 and names the row. On a copy with a row whose items are all `[decision]` and whose status is `covered, with limits`, it exits 1. On the committed README, it exits 0. [cmd: bash scripts/gates/test_gates.sh]

## Open questions
- Q1: Does the factory own live incident response (detect, triage, mitigate), or is it out of scope by design, like launch and pricing? R12 waits on this decision. If it is in scope, a new incident-response skill is a separate spec. Jev leans to build (0.69); this spec leaves the call to the owner, because it sets the factory's scope, like the AC10 decisions.
- Q2: Is `covered, with limits` a separate status, or is it `covered` plus a "Limits:" note? Release / deploy uses it today. The draft keeps it separate, because it tells the reader that the proof is weaker than the row's text suggests.
- Q3: Does G2 (R8) belong in factory-conductor, or in a new review skill that also emits a `review-findings/v1` envelope? The assessment named that envelope kind as a likely follow-up. The draft puts it in factory-conductor, because the reviewer brief and its diff hardening already live there.
- Q4: Is the squash-merge match in R10 (the pull request number in the commit subject) strong enough? A human can type any number into a subject. The draft accepts it only when the release is verified and the number equals the PR that the plan's own run opened.
- Q5: Is a `proved` or `bounded` backend for Python, TypeScript or Go worth a later spec? Jev labels the gap build (0.69). This spec keeps it a non-goal, because no candidate tool fits the stdlib-only, offline gate today.
