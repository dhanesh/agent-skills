---
name: test-safety-net
description: >-
  Author unit tests into a codebase that has none, so an agent can change it safely. Use when a
  repo has no meaningful tests, when someone says "I don't trust an agent in this codebase", "add
  tests before we refactor", or "we need a safety net before this migration". Ranks units by blast
  radius and churn, then writes characterization tests that pin current behaviour — each one proved
  able to FAIL before it is kept, so the suite is a real change-detector and not green noise. Every
  test declares whether it pins behaviour or asserts a spec; suspected bugs are pinned AND reported,
  never silently blessed. Code that is not testable is triaged, not forced: it becomes a ranked seam
  list for clean-code. Never modifies your source and never writes a test that performs real I/O.
  Not a correctness audit and not a coverage-percentage chaser. Fills the loop verifier-installer
  installs; clean-code judges what comes out.
license: MIT
compatibility: TODO(repo2skill) one-liner on runtime requirements (default assumption python3, stdlib only, offline).
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "testing,characterization,legacy-code,agent-safety,pytest"
---

# test-safety-net

TODO(repo2skill): one paragraph stating what this skill does for the agent that loads
it and the outcome it exists to produce. Write it as a reusable prompt, not
documentation — a system prompt the agent follows when the skill fires.

## When to use

TODO(repo2skill): the concrete situations that should trigger this skill, in the
user's own phrasings — and the boundary: the adjacent jobs it deliberately does not
do, naming the sibling skill that does each one.

## Workflow

1. **Scope.** TODO(repo2skill): the single first job — gather exactly the inputs the
   next step needs, and nothing more. State what was found in one line.
2. **Execute.** TODO(repo2skill): the core step that produces the deliverable named
   below. One job per step; offload reference detail to references/overview.md
   rather than inlining it here.
3. **Verify and repair.** Check the produced output against the success criteria
   below, fix what fails, and re-check until everything holds. State plainly what
   was verified and what was repaired.

## Deliverable

TODO(repo2skill): name the concrete output contract — the artifact, report, or file
set this skill produces, with the fixed fields or structure downstream consumers
rely on. "The output" is not a contract; a named shape is.

## Success criteria

TODO(repo2skill): the deterministic checks that decide the deliverable is done — the
evaluate half of the generate -> evaluate -> repair loop. The shipped smoke suite
(assets/test_test_safety_net_smoke.py) and outcome eval (eval/run_eval.py) are the seed:
grow them as the skill grows.
