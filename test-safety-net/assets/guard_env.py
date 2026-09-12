#!/usr/bin/env python3
"""guard_env.py -- the tier/allow contract every runtime I/O guard shares.

`TEST_SAFETY_NET_TIER` and `TEST_SAFETY_NET_ALLOW` mean the same thing on
every stack: an absent or invalid tier is tier 1 -- the fail-safe direction
-- with a note; `none` allows nothing; an unknown group is ignored and stays
blocked; tier 1 ignores the allow list; tier 1 blocks every group, tier 2
blocks whatever a stack's own controllable/uncontrollable split and allow
list leave out. `read_env` and `blocked_groups` here are that contract in
one place, parameterised by each stack's own group tables so the words
"controllable" and "uncontrollable" keep one meaning across stacks without
forcing them to share a group taxonomy.

What stays OUT of this module, on purpose: classifying a captured call into
a group -- what counts as `filesystem` I/O, how a violation is reported, how
the exit code is chosen -- is runtime-specific and stays in each stack's own
`io_guard_*.py`. This module is the shared half of the contract, not the
guard itself.

`io_guard_go.py` imports this module directly. `io_guard.py` (Python) keeps
its own copy of the same contract for now -- unifying it here is a stated
follow-up, not done in this change. `io_guard.js` (Node) cannot import
Python at all; its `readEnv` re-implements the same contract in JavaScript
and is kept behaviourally identical by hand.
"""
from __future__ import annotations

import re

TIER_ENV = "TEST_SAFETY_NET_TIER"
ALLOW_ENV = "TEST_SAFETY_NET_ALLOW"

EXIT_GREEN, EXIT_RED, EXIT_NOT_ARMED, EXIT_TRIP, EXIT_NO_TEST, EXIT_NO_BUILD = 0, 1, 2, 3, 4, 5

OUTCOME = {
    EXIT_GREEN: "GREEN (exit 0): the selected test ran and passed",
    EXIT_RED: "RED (exit 1): the selected test ran and failed",
    EXIT_NOT_ARMED: "NOT ARMED (exit 2): the guard's overlay did not build on this "
                    "toolchain; nothing was proved",
    EXIT_TRIP: "GUARD TRIP (exit 3): the unit reached real I/O -- the CLASSIFICATION is "
               "wrong: reclassify it to Tier 3 and discard the test, red or green",
    EXIT_NO_TEST: "NO TEST (exit 4): nothing was proved -- -run matched no test, the "
                  "package has no test files, or the test skipped itself",
    EXIT_NO_BUILD: "NO BUILD (exit 5): the package did not build; a compile error is not RED",
}


def read_env(env, controllable, uncontrollable, stack, why_uncontrollable):
    """`(tier, allow, notes)`. Never raises.

    An absent or invalid tier is tier 1 -- the fail-safe direction -- with a
    note; `none` allows nothing; an unknown group is ignored and stays
    blocked; tier 1 ignores the allow list. Naming a group that is
    uncontrollable on `stack` gets its own note, via `why_uncontrollable(g)`,
    instead of being called unknown.
    """
    notes = []
    raw = str(env.get(TIER_ENV, "")).strip()
    if raw in ("1", "2"):
        tier = int(raw)
    else:
        tier = 1
        notes.append("%s=%r is not 1 or 2; defaulting to tier 1 (block everything), "
                     "the fail-safe direction" % (TIER_ENV, raw))
    raw_allow = env.get(ALLOW_ENV)
    if raw_allow is None or not str(raw_allow).strip():
        allow = None
    elif str(raw_allow).strip().lower() == "none":
        allow = []
    else:
        wanted = [g.strip() for g in re.split(r"[,;]", str(raw_allow)) if g.strip()]
        allow = [g for g in wanted if g in controllable]
        for g in wanted:
            if g in controllable:
                continue
            if g in uncontrollable:
                notes.append("%s names %s, which is not controllable on %s (%s); it stays "
                             "blocked" % (ALLOW_ENV, g, stack, why_uncontrollable(g)))
            else:
                notes.append("%s names unknown group %r; it is ignored and stays blocked"
                             % (ALLOW_ENV, g))
    if tier == 1 and allow:
        notes.append("tier 1 ignores %s: a unit that claimed to touch nothing gets "
                     "everything blocked" % ALLOW_ENV)
    return tier, allow, notes


def blocked_groups(tier, allow, groups, controllable, uncontrollable):
    """The groups a run at `tier` blocks. Tier 1: all. Tier 2: all but what it fakes."""
    if tier == 1:
        return set(groups)
    if allow is None:
        return set(uncontrollable)
    permitted = {g for g in allow if g in controllable}
    return set(groups) - permitted
