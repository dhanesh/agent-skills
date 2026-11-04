---
name: incident-watch
description: >-
  In development: no command ships yet. The planned live-incident detector of the
  software factory: `incident probe` will run only the release recipe's read-only
  `health`, `version_probe` and `prod_smoke` commands on a schedule, open one incident
  record after 2 or more consecutive failed probes and write an `incident` signal into
  ops-intake, and `incident close --by <name>` will hand the record to bug-autopsy. It
  will never run a rollback or deploy. Use when the user asks for incident-watch or
  "watch production for incidents": say that it is not built yet and name the skills
  that do each step by hand. Not for a post-mortem (use bug-autopsy), not for a deploy
  or rollback (use release-conductor).
license: MIT
compatibility: Nothing to run yet, as no command ships. The planned tooling is Python 3.10+ stdlib only, offline, plus git and a POSIX shell.
metadata:
  author: dhanesh
  version: "0.0.1"
  tags: "software-factory,incident,triage,read-only-probe,in-development"
---

# incident-watch

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY in this skill are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and only when, they appear in all capitals.

**Status: in development: no command ships yet.** This skill is a placeholder for the
factory's live-incident detector. When it is built, `incident probe` will run the
release recipe's read-only probes, open an incident record after repeated failures and
signal ops-intake; the owner will mitigate through release-conductor. Today it has no
script, no command and no behaviour. When it fires, your job is to say so plainly and
to send the user to the skills that do each step by hand.

You MUST NOT tell the user that an `incident` command exists, and you MUST NOT try to
run one, because nothing ships yet and an invented command fails or misleads.

## When to use

The user asks for incident-watch by name, or asks to "watch production for incidents",
"probe prod on a schedule" or "open an incident when health fails". The design is in
`docs/factory/2026-11-04-autonomous-factory-spec.md` (requirements R22 to R26 and
decision D1) in the agent-skills repository.

Boundaries: a deploy or a rollback is `release-conductor`, and only after the owner's
yes; a post-mortem of an incident that is over is `bug-autopsy`; triage of signals
into planning is `ops-intake`.

## Workflow

1. **Say the status.** Tell the user, in one line, that incident-watch is in
   development and that no command ships yet.
2. **Map the steps to skills that exist.** Use the planned steps in
   [references/overview.md](references/overview.md) to name the installed skill that
   does each step the user cares about by hand today. Name only skills that are
   installed in this session.
3. **Verify the answer.** Check your reply against the success criteria below. Remove
   any sentence that names an `incident` command, a script of this skill or a
   behaviour as if it runs today, then re-check.

## Deliverable

A short status reply with two parts: the line "incident-watch is in development: no
command ships yet", and a list of the planned steps the user asked about, each with
the existing skill that does it by hand, or "no skill yet".

## Success criteria

- The reply carries the in-development line.
- The reply names no command, script or behaviour of this skill as if it exists.
- Each skill named in the reply is installed in this session.

The outcome eval (`eval/run_eval.py`) checks this skill's honest state: SKILL.md and
README.md carry the in-development line, and every path this skill names exists.
