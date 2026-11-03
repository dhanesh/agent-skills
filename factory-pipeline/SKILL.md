---
name: factory-pipeline
description: >-
  In development: no command ships yet. The planned scheduled driver of the software
  factory: one `pipeline tick` that, under a standing pipeline grant, runs sweep, growth
  review, idea follow-up, autopick, plan, derive grant, build, release stage and digest,
  and stops at every human gate. Use when the user asks for factory-pipeline or "run the
  whole factory on a schedule": say that it is not built yet and name the skills that do
  each stage by hand today. Not for running one approved plan to a PR (use
  factory-conductor), not for writing a plan (use spec-first-planning).
license: MIT
compatibility: Nothing to run yet, as no command ships. The planned tooling is Python 3.10+ stdlib only, offline, plus git and a POSIX shell.
metadata:
  author: dhanesh
  version: "0.0.1"
  tags: "software-factory,scheduler,pipeline,autonomy-grant,in-development"
---

# factory-pipeline

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY in this skill are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and only when, they appear in all capitals.

**Status: in development: no command ships yet.** This skill is a placeholder for the
factory's scheduled driver. When it is built, one `pipeline tick` will run every factory
stage in order under a standing pipeline grant and stop at each human gate. Today it
has no script, no command and no behaviour. When it fires, your job is to say so
plainly and to send the user to the skills that do each stage by hand.

You MUST NOT tell the user that a `pipeline` command exists, and you MUST NOT try to
run one, because nothing ships yet and an invented command fails or misleads.

## When to use

The user asks for factory-pipeline by name, or asks to "run the whole factory on a
schedule", "drive every stage unattended" or "tick the pipeline". The design is in
`docs/factory/2026-11-04-autonomous-factory-spec.md` (requirements R12 to R21) in the
agent-skills repository.

Boundaries: running one approved plan to an open PR is `factory-conductor`; writing a
spec and task plan is `spec-first-planning`; carrying a merged change to production is
`release-conductor`.

## Workflow

1. **Say the status.** Tell the user, in one line, that factory-pipeline is in
   development and that no command ships yet.
2. **Map the stages to skills that exist.** Use the planned stage list in
   [references/overview.md](references/overview.md) to name the installed skill that
   does each stage the user cares about by hand today. Name only skills that are
   installed in this session.
3. **Verify the answer.** Check your reply against the success criteria below. Remove
   any sentence that names a `pipeline` command, a script of this skill or a
   behaviour as if it runs today, then re-check.

## Deliverable

A short status reply with two parts: the line "factory-pipeline is in development: no
command ships yet", and a list of the planned stages the user asked about, each with
the existing skill that does it by hand, or "no skill yet".

## Success criteria

- The reply carries the in-development line.
- The reply names no command, script or behaviour of this skill as if it exists.
- Each skill named in the reply is installed in this session.

The outcome eval (`eval/run_eval.py`) checks this skill's honest state: SKILL.md and
README.md carry the in-development line, and every path this skill names exists.
