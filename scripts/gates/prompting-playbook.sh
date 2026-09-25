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
#   PP-7  Prompt eng.  : BCP 14. Every SKILL.md declares RFC 2119/8174 keywords
#                        and uses only MUST/MUST NOT/SHOULD/SHOULD NOT/MAY in
#                        capitals (hard; a declaration with no keyword used is
#                        an advisory INFO).
#
# Advisory checks (report only; promoted to hard with --strict):
#   PP-5  Prompt eng.  : every absolute states its reason. Each never/always/
#                        MUST NOT directive needs a reason (because, so that,
#                        otherwise, ": " + consequence, ...) in its sentence or
#                        the next one. Hedges no longer count: an unexplained
#                        rule makes a model overcorrect, and a hedge on a real
#                        requirement reads as permission to under-deliver.
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

# ── PP-5: every absolute carries a reason (Prompt engineering, advisory) ─────
# An absolute rule earns its place by saying why; a hedge on a real requirement
# reads as permission to under-deliver, so hedges no longer count for anything.
# Each never/always/MUST NOT directive needs a reason in its own sentence or the
# next one of the same paragraph (scripts/gates/pp5_reasons.py; fenced code,
# inline code and the BCP 14 declaration are exempt). One advisory per absolute.
pp5_out="$(python3 -I "$(dirname "$0")/pp5_reasons.py" "$SKILL_MD")" || {
    check_fail "PP-5 reasons: pp5_reasons.py failed on $SKILL_MD"
    pp5_out=""
}
if [ -z "$pp5_out" ]; then
    check_pass "PP-5 reasons: every never/always/MUST NOT directive states its reason"
else
    while IFS= read -r pp5_line; do
        check_advisory "PP-5 unreasoned absolute: $pp5_line"
    done <<EOF_PP5
$pp5_out
EOF_PP5
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

# ── PP-7: BCP 14 declared and used consistently (Prompt engineering, hard) ───
# docs/superpowers/specs/2026-09-19-bcp14-skills-design.md §3. Every SKILL.md
# declares RFC 2119/8174 keywords, and only the five it declares may appear in
# capitals. Fenced code (``` or ~~~), inline code spans and the declaration
# sentence itself (not other text on its line) are not scanned: the declaration
# names every keyword and would satisfy "keywords used" vacuously. A fence
# closes only on the same character, at least as long as the one that opened
# it, so a ```` block may wrap ``` lines, and carries nothing after its fence
# characters. Per CommonMark a backtick fence's info string has no backtick, so
# "```` ```mermaid ```` block" is inline code, not an opener (the same rule as
# bcp14_registry.py). A declaration inside a fence does not count.
# The declaration sentence has no internal full stop, so [^.]* bounds it.
BCP14_DECL='BCP 14 \(RFC 2119, RFC 8174\)'
BCP14_SENTENCE="[^.]*${BCP14_DECL}[^.]*all capitals\\.?"
bcp14_nofence="$(printf '%s\n' "$BODY" | awk '
    {
        if (match($0, /^[[:space:]]*(```+|~~~+)/)) {
            m = substr($0, RSTART, RLENGTH); rest = substr($0, RSTART + RLENGTH)
            sub(/^[[:space:]]*/, "", m)
            if (fence == "") {
                if (!(substr(m, 1, 1) == "`" && index(rest, "`") > 0)) { fence = m; next }
            } else if (substr(m, 1, 1) == substr(fence, 1, 1) && length(m) >= length(fence) && rest ~ /^[[:space:]]*$/) {
                fence = ""; next
            }
        }
        if (fence == "") print
    }')"
bcp14_scan="$(printf '%s\n' "$bcp14_nofence" | sed -E "s/${BCP14_SENTENCE}//" | sed 's/`[^`]*`//g')"
if printf '%s\n' "$bcp14_nofence" | grep -qE "$BCP14_DECL.*all capitals"; then
    undeclared="$(printf '%s\n' "$bcp14_scan" | grep -oE '\b(SHALL NOT|SHALL|NOT RECOMMENDED|RECOMMENDED|REQUIRED|OPTIONAL)\b' | sort -u | tr '\n' ' ' || true)"
    used="$(printf '%s\n' "$bcp14_scan" | grep -cE '\b(MUST|SHOULD|MAY)\b' || true)"
    if [ -n "$undeclared" ]; then
        check_fail "PP-7 BCP 14: undeclared keyword(s) in capitals: ${undeclared}-- the declaration defines only MUST, MUST NOT, SHOULD, SHOULD NOT and MAY"
    else
        check_pass "PP-7 BCP 14: declared; $used line(s) use a declared keyword"
        if [ "$used" -eq 0 ]; then
            check_advisory "PP-7 BCP 14: declared but no MUST/SHOULD/MAY is used; mark the skill's hard rules (docs/prompting-playbook.md, BCP 14 keywords)"
        fi
    fi
else
    check_fail "PP-7 BCP 14: no declaration. Add, as its own paragraph under the # title: The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY in this skill are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and only when, they appear in all capitals."
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
