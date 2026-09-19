# Spec: <feature name — one line>

## Problem
<Two to four sentences: who hurts today, how, and why now. Describe the pain,
not the solution.>

## Users
- <user class and what they are trying to accomplish>

## Goals
- <observable outcome that would count as success>

## Non-goals
- <adjacent thing this feature deliberately will not do — naming it here is
  what keeps the plan small>

## Constraints
<!-- The Constrain step (always on, even in a light attended pass). One
     bullet per constraint: "- <ID> [<type>]: <statement>". <ID> is a
     category prefix (B business, T technical, U UX, S security,
     O operational) plus a number, e.g. B1, T2. <type> is invariant, goal or
     boundary. No pre-mortem is required for the light pass — see
     references/spec-format.md for the full-loop Tension/Choose steps. -->
- B1: <business/technical/UX/security/operational constraint>
- T1: <another constraint>

## Required truths
<!-- The Anchor step: work backwards from the outcome asking "what must be
     TRUE?". One bullet per required truth: "- RT<n> [<status>]: <statement>
     (parent: <OUTCOME|RT<k>>; maps_to: <constraint ids>; reqs: <R ids>;
     confidence: <0..1>; check: <runnable check>)". IDs run RT1..RTn in
     order. <status> is SATISFIED, PARTIAL, NOT_SATISFIED or
     SPECIFICATION_READY. At least one RT must have parent: OUTCOME. Every
     constraint above must be named in some RT's maps_to; every RT must
     name at least one requirement below in reqs. check: MUST be the last
     field. -->
- RT1 [SPECIFICATION_READY]: <what must be true> (parent: OUTCOME; maps_to: B1; reqs: R1; confidence: 0.8; check: <runnable check>)
- RT2 [SPECIFICATION_READY]: <what must be true> (parent: RT1; maps_to: T1; reqs: R1; confidence: 0.7; check: <runnable check>)

## Requirements
<!-- One bullet per requirement, numbered R1..Rn with no gaps. Each is a
     SINGLE testable statement containing "must" (or "shall"). No vague
     terms (fast, robust, user-friendly, ...) unless a number or checkable
     bound follows in the same statement. Optional: append a
     "[where: path/or/area]" hint so the task planner can pre-fill the
     task's Where field. -->
- R1: <single testable "must" statement>
- R2: <single testable "must" statement>

## Acceptance criteria
<!-- At least one per requirement, prefixed with its id. Write each as a
     runnable check: a command plus its expected exit code or output, or a
     concrete observation an outside party could make without asking you.
     If you cannot write one, the requirement is not ready — move it to
     Open questions. -->
- R1: <runnable check that proves R1>
- R2: <runnable check that proves R2>

## Open questions
- <unknown that could still change the requirements — list may be empty>
