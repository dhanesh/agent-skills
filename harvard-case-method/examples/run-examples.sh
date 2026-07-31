#!/bin/sh
# Regenerate the three worked examples end to end, exactly as published.
# Offline, stdlib-only. Writes nothing outside a temp dir.
set -eu
A="$(cd "$(dirname "$0")/../assets" && pwd)"
E="$(cd "$(dirname "$0")" && pwd)"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
K="python3 $A/casekit.py --root $T/decisions"
R="python3 $A/rigor.py"
hr() { printf '\n=== %s ===\n' "$1"; }

hr "1.1 RICE — first draft (agent's own reach in mixed periods)"
$R rice "$E/1-rice-first-draft.md" || true
hr "1.2 RICE — after normalising to one period"
$R rice "$E/1-rice-repaired.md"

hr "2.1 PLAN — as submitted to the board"
$R plan "$E/2-plan-as-submitted.md" || true
hr "2.2 PLAN — after rework"
$R plan "$E/2-plan-reworked.md"

hr "3.1 DECIDE — brief lint (first draft is two options, unsourced)"
$K new kyc-stack --decision "Should we build our own KYC/onboarding stack or renew the vendor?" --by 2026-08-29 >/dev/null
cp "$E/3-decision-brief-first-draft.md" "$T/decisions/kyc-stack/brief.md"
$K lint kyc-stack || true
hr "3.2 DECIDE — brief after rework"
cp "$E/3-decision-brief.md" "$T/decisions/kyc-stack/brief.md"
$K lint kyc-stack
hr "3.3 DECIDE — commit refused without premortem/falsifier/forecasts"
sed '/^## Premortem/,$d' "$E/3-decision-record.md" > "$T/decisions/kyc-stack/decision.md"
$K commit kyc-stack || true
hr "3.4 DECIDE — commit, complete record"
cp "$E/3-decision-record.md" "$T/decisions/kyc-stack/decision.md"
$K commit kyc-stack
hr "3.5 DECIDE — resolve two forecasts and score"
$K resolve kyc-stack --n 1 --outcome yes
$K resolve kyc-stack --n 2 --outcome no
$K score kyc-stack
