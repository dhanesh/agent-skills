#!/bin/sh
# prompting-playbook.sh <skill-dir> [--strict]
#
# Lints a skill's SKILL.md against the conventions from "The Prompting
# Playbook" (Anthropic, Margot Van Laar — https://youtu.be/G2B0YWuJUgI).
# The talk's thesis: "prompting" has fragmented into four disciplines, a
# layered stack where each layer automates one more thing you used to do
# by hand:
#
#   Prompt engineering  -> Context engineering -> Harness engineering -> Loop engineering
#
# A SKILL.md *is* a reusable prompt (a system prompt the agent loads), so the
# same playbook applies. This gate turns the talk's enforceable conventions
# into deterministic checks. See docs/prompting-playbook.md for the grounding
# and a per-check rationale.
#
# Hard checks (fail the gate):
#   PP-1  Prompt eng.  : structured control surface (>= 3 `## ` sections).
#                        "Structure first, then iteration becomes tractable" —
#                        labeled sections let you change one and watch the evals.
#   PP-2  Harness eng. : decomposed workflow (numbered steps + `### ` substeps
#                        >= 3). Don't ask one prompt to plan+execute+verify in
#                        one pass; give each stage a single job.
#   PP-3  Harness eng. : explicit output protocol (the body names what it
#                        produces). "The model and downstream code negotiate a
#                        protocol; the cleaner it is, the less you think later."
#   PP-4  Loop eng.    : verification baked in (the body references a
#                        gate/eval/verify/check mechanism). The generate ->
#                        evaluate -> repair loop needs an explicit evaluator.
#
# Advisory checks (report only; promoted to hard with --strict):
#   PP-5  Prompt eng.  : overcorrection guard. Counts absolutist negative
#                        directives (never/always/must not). The Meridian
#                        lesson — rigid negative rules make a model defensive
#                        and overcorrect. Prefer heuristics with an escape hatch.
#   PP-6  Context eng. : lean context / progressive disclosure. Flags a large
#                        SKILL.md body that keeps all detail inline instead of
#                        offloading to references/. Keep working memory lean.
#
# Prints PASS:/FAIL: per hard check, INFO: for advisories.
# Prints PLAYBOOK_RESULT: PASS or PLAYBOOK_RESULT: FAIL at the end.
# Exits non-zero if ANY hard check fails (advisories fail only under --strict).
set -eu

# ── Usage ────────────────────────────────────────────────────────────────────
if [ "$#" -lt 1 ]; then
    printf 'Usage: %s <skill-dir> [--strict]\n' "$0" >&2
    exit 1
fi

SKILL_DIR="$1"
shift

STRICT=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --strict) STRICT=1; shift ;;
        *) printf 'ERROR: Unknown argument: %s\n' "$1" >&2; exit 1 ;;
    esac
done

# ── State + helpers ──────────────────────────────────────────────────────────
hard_failures=0
advisories=0

check_pass() { printf 'PASS: %s\n' "$1"; }
check_fail() { printf 'FAIL: %s\n' "$1"; hard_failures=$((hard_failures + 1)); }
check_info() { printf 'INFO: %s\n' "$1"; }
check_skip() { printf 'SKIP: %s\n' "$1"; }

# An advisory: INFO by default, FAIL under --strict.
check_advisory() {
    if [ "$STRICT" -eq 1 ]; then
        check_fail "$1"
    else
        check_info "$1 (advisory — promote with --strict)"
        advisories=$((advisories + 1))
    fi
}

# ── Locate SKILL.md ──────────────────────────────────────────────────────────
SKILL_MD="$SKILL_DIR/SKILL.md"
if [ ! -f "$SKILL_MD" ]; then
    printf 'FAIL: SKILL.md not found in %s\n' "$SKILL_DIR"
    printf 'PLAYBOOK_RESULT: FAIL\n'
    exit 1
fi

# Body = everything after the closing frontmatter --- (same extraction the
# sibling gates use). Prompts live in the body, not the YAML frontmatter.
BODY="$(awk '/^---$/ { count++; next } count >= 2 { print }' "$SKILL_MD")"

if [ -z "$(printf '%s' "$BODY" | tr -d '[:space:]')" ]; then
    check_fail "body: SKILL.md has no content after frontmatter"
    printf 'PLAYBOOK_RESULT: FAIL\n'
    exit 1
fi

# ── PP-1: structured control surface (Prompt engineering) ────────────────────
h2_count="$(printf '%s\n' "$BODY" | grep -cE '^## ' || true)"
if [ "$h2_count" -ge 3 ]; then
    check_pass "PP-1 structure: $h2_count top-level sections (clean control surface)"
else
    check_fail "PP-1 structure: only $h2_count '## ' sections (need >= 3). A structured prompt is a debuggable control surface — change one section, watch the evals."
fi

# ── PP-2: decomposed workflow (Harness engineering) ──────────────────────────
# Single-job stages: numbered top-level steps ("1. ") plus "### " substeps.
num_steps="$(printf '%s\n' "$BODY" | grep -cE '^[0-9]+\. ' || true)"
h3_count="$(printf '%s\n' "$BODY" | grep -cE '^### ' || true)"
decomp=$((num_steps + h3_count))
if [ "$decomp" -ge 3 ]; then
    check_pass "PP-2 decomposition: $num_steps numbered step(s) + $h3_count substep heading(s). Each stage has a single job."
else
    check_fail "PP-2 decomposition: only $decomp ordered step(s)/substep(s) (need >= 3). Split the work — don't ask one prompt to plan+execute+verify in a single pass."
fi

# ── PP-3: explicit output protocol (Harness engineering) ─────────────────────
# The body must name what the skill produces (its deliverable contract).
if printf '%s\n' "$BODY" | grep -qiE '\b(deliverable|output|outputs|emit|emits|produce|produces|write|writes|wrote|report|reports|install|installs|build|builds|render|renders|generate|generates|scaffold|scaffolds|result|results|artifact|artifacts|ledger)\b'; then
    check_pass "PP-3 output protocol: deliverable/output is named explicitly"
else
    check_fail "PP-3 output protocol: the body never names what it produces. Define the output contract the model and downstream code share (a 'deliverable'/'output'/'emit'/... section)."
fi

# ── PP-4: verification baked in (Loop engineering) ───────────────────────────
# generate -> evaluate -> repair needs an explicit evaluator/gate.
if printf '%s\n' "$BODY" | grep -qiE '\b(gate|gates|validate|validation|validator|verify|verifies|verification|eval|evals|evaluate|evaluator|check|checks|test|tests)\b'; then
    check_pass "PP-4 verification: an evaluate/verify/gate step is present"
else
    check_fail "PP-4 verification: no evaluate/verify/gate mechanism referenced. A new use case rarely needs a smarter model — it needs an explicit evaluate -> repair step."
fi

# ── PP-5: overcorrection guard (Prompt engineering, advisory) ────────────────
# Rigid absolutist negatives make a model defensive (the Meridian overcorrection).
absol="$(printf '%s\n' "$BODY" | grep -oiE '\b(never|always|must not|do not ever|under no circumstances)\b' | wc -l | tr -d ' ')"
# Escape-hatch / heuristic vocabulary that tempers an absolute into a judgment call.
hedge="$(printf '%s\n' "$BODY" | grep -ciE '\b(unless|except|prefer|usually|typically|when in doubt|heuristic|judgment|trade-?off|by default|generally)\b' || true)"
if [ "$absol" -le 6 ] || [ "$hedge" -ge "$absol" ]; then
    check_pass "PP-5 overcorrection: $absol absolutist directive(s) balanced by $hedge heuristic/escape-hatch cue(s)"
else
    check_advisory "PP-5 overcorrection: $absol absolutist directive(s) (never/always/must not) vs only $hedge heuristic cue(s). Rigid negative rules make a model overcorrect and refuse — give it heuristics with an escape hatch"
fi

# ── PP-6: lean context / progressive disclosure (Context engineering, advisory)
body_lines="$(printf '%s\n' "$BODY" | wc -l | tr -d ' ')"
has_refs=0
[ -d "$SKILL_DIR/references" ] && has_refs=1
# A long inline body with no references/ offloading is a context-bloat smell.
if [ "$body_lines" -gt 220 ] && [ "$has_refs" -eq 0 ]; then
    check_advisory "PP-6 lean context: SKILL.md body is $body_lines lines with no references/ dir. Keep working memory lean — move depth into references/ and link it (progressive disclosure)"
else
    if [ "$has_refs" -eq 1 ]; then
        check_pass "PP-6 lean context: body $body_lines lines, detail offloaded to references/ (progressive disclosure)"
    else
        check_pass "PP-6 lean context: body $body_lines lines (within inline budget)"
    fi
fi

# ── Summary ──────────────────────────────────────────────────────────────────
if [ "$hard_failures" -eq 0 ]; then
    if [ "$advisories" -gt 0 ]; then
        printf 'PLAYBOOK_RESULT: PASS (%d advisory note(s))\n' "$advisories"
    else
        printf 'PLAYBOOK_RESULT: PASS\n'
    fi
    exit 0
else
    printf 'PLAYBOOK_RESULT: FAIL (%d hard failure(s))\n' "$hard_failures"
    exit 1
fi
