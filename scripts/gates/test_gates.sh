#!/bin/sh
# scripts/gates/test_gates.sh — tests for the gate scripts themselves.
# gate: offline — runs in `make test`/`make gate`. No network, no fixed ports,
# no wall-clock dependence.
#
# Why this file exists: scan-leaks.sh is the security gate — ~180 lines of awk
# ERE — and nothing anywhere asserted that a planted secret is actually found.
# A broken regex, or an awk without interval-expression support, would have made
# every detector silently match nothing and print SCAN_RESULT: PASS forever.
# The same was true of dry-run-replay.sh and run-eval.sh. A gate with no
# positive test is a gate you cannot trust after any edit.
set -eu

GATES="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT INT TERM

rc=0
ok()  { echo "PASS: $1"; }
bad() { echo "FAIL: $1"; rc=1; }

# A minimal, gate-valid skill we can plant things into.
mkskill() {
  d="$WORK/$1"
  rm -rf "$d"; mkdir -p "$d/assets"
  cat > "$d/SKILL.md" <<'EOF'
---
name: fixture-skill
description: A fixture skill used by the gate self-tests; it exists only so the gate scripts have a valid directory to run against.
license: MIT
compatibility: none
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "fixture"
---
# fixture
## Steps
1. Do the thing.
## Verify
Run the check.
EOF
  echo "# fixture" > "$d/README.md"
  printf '%s\n' "$d"
}

# ── scan-leaks: every curated detector must actually fire ────────────────────
# One planted secret per detector. If a regex breaks (or awk lacks {n,m}), the
# corresponding row flips to FAIL instead of the whole gate going quietly green.
check_detector() {
  name="$1"; payload="$2"
  d="$(mkskill "det")"
  printf '%s\n' "$payload" > "$d/assets/planted.txt"
  if sh "$GATES/scan-leaks.sh" "$d" 2>&1 | grep -q '^FINDING:'; then
    ok "scan-leaks detects $name"
  else
    bad "scan-leaks detects $name (planted secret was NOT found)"
  fi
}

# These payloads are SYNTHETIC — no key here has ever been valid. Even so, the
# provider-shaped ones are ASSEMBLED AT RUNTIME rather than written literally:
# a literal `sk_live_…` in a committed file trips GitHub's push protection (and
# any other repo-wide scanner), which would block the push of the very test that
# proves our own scanner works. Keep them assembled — writing the prefix inline
# "for readability" will block the next push.
STRIPE_LIVE="sk_""live_"          # -> sk_live_
GOOGLE_KEY="AIza""Sy"             # -> AIzaSy
AWS_PREFIX="AKIA"                 # AWS's own documented example key follows

check_detector "an AWS access key"      "AWS_KEY = ${AWS_PREFIX}IOSFODNN7EXAMPLE"
check_detector "a Google API key"       "key: ${GOOGLE_KEY}AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"   # AIza + 35 charset chars
check_detector "a live Stripe key"      "stripe = ${STRIPE_LIVE}0123456789abcdefghijklmn"
check_detector "a PEM private key"      '-----BEGIN RSA PRIVATE KEY-----'
check_detector "a JWT"                  'tok=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r1wV'
check_detector "a generic credential"   'password = "hunter2hunter2hunter2"'
check_detector "a hex secret"           'api_secret = 0123456789abcdef0123456789abcdef'

# ── scan-leaks: a clean skill must still pass (the check discriminates) ──────
d="$(mkskill "clean")"
echo "just ordinary prose about configuration" > "$d/assets/notes.txt"
if sh "$GATES/scan-leaks.sh" "$d" >/dev/null 2>&1; then
  ok "scan-leaks passes a clean skill (no false positive)"
else
  bad "scan-leaks passes a clean skill (no false positive)"
fi

# ── scan-leaks: FAIL CLOSED on a file it cannot decode ───────────────────────
# awk aborts at the first invalid byte. Because the detectors ran inside a
# `find | while` pipeline, set -eu never saw it, the rest of the file went
# unscanned, and the script still printed PASS — so one stray byte on line 1
# hid every secret below it.
d="$(mkskill "badbyte")"
printf 'note: \377 byte\nfiller\nAWS_KEY = AKIAIOSFODNN7EXAMPLE\n' > "$d/assets/notes.txt"
out="$(sh "$GATES/scan-leaks.sh" "$d" 2>&1 || true)"
if printf '%s\n' "$out" | grep -q 'SCAN_RESULT: PASS'; then
  bad "scan-leaks never reports PASS for a file it could not scan"
else
  ok "scan-leaks never reports PASS for a file it could not scan"
fi
if sh "$GATES/scan-leaks.sh" "$d" >/dev/null 2>&1; then
  bad "scan-leaks exits non-zero when a file is unscannable"
else
  ok "scan-leaks exits non-zero when a file is unscannable"
fi

# ── validate-skill: the description limit is measured, block scalar included ──
# Reading only the first line after `description:` made a folded scalar measure
# 2 characters, so 11 of 18 skills were unchecked and one shipped 163 over.
d="$(mkskill "desc")"
{
  echo "---"
  echo "name: fixture-skill"
  echo "description: >-"
  i=0
  while [ "$i" -lt 30 ]; do
    echo "  aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    i=$((i + 1))
  done
  echo "license: MIT"
  echo "compatibility: none"
  echo "metadata:"
  echo "  author: dhanesh"
  echo '  version: "1.0.0"'
  echo '  tags: "fixture"'
  echo "---"
  echo "# fixture"
} > "$d/SKILL.md"
if sh "$GATES/validate-skill.sh" "$d" 2>&1 | grep -q 'FAIL: description: exceeds 1024'; then
  ok "validate-skill measures a folded block-scalar description"
else
  bad "validate-skill measures a folded block-scalar description (over-long value passed)"
fi

# ── run-eval: the verdict line and the exit code must agree ──────────────────
d="$(mkskill "evalfix")"
mkdir -p "$d/eval"
printf 'print("CHECK: broken")\nprint("EVAL_RESULT: FAIL")\n' > "$d/eval/run_eval.py"
if sh "$GATES/run-eval.sh" "$d" >/dev/null 2>&1; then
  bad "run-eval rejects an eval that prints FAIL but exits 0"
else
  ok "run-eval rejects an eval that prints FAIL but exits 0"
fi
printf 'print("CHECK: ok")\n' > "$d/eval/run_eval.py"
if sh "$GATES/run-eval.sh" "$d" >/dev/null 2>&1; then
  bad "run-eval rejects an eval with no EVAL_RESULT verdict"
else
  ok "run-eval rejects an eval with no EVAL_RESULT verdict"
fi
printf 'print("CHECK: ok")\nprint("EVAL_RESULT: PASS")\n' > "$d/eval/run_eval.py"
if sh "$GATES/run-eval.sh" "$d" >/dev/null 2>&1; then
  ok "run-eval accepts an honest passing eval"
else
  bad "run-eval accepts an honest passing eval"
fi
rm -rf "$d/eval"
if sh "$GATES/run-eval.sh" "$d" >/dev/null 2>&1; then
  bad "run-eval fails a skill with no eval at all"
else
  ok "run-eval fails a skill with no eval at all"
fi

# ── dry-run-replay: a residual placeholder must fail ─────────────────────────
d="$(mkskill "dryrun")"
mkdir -p "$d/assets/templates"
printf 'A={{A}}\n' > "$d/assets/templates/t.txt"
cat > "$d/PARAMETERS.md" <<'EOF'
# Parameters

| Placeholder | Description | Example | Default |
|---|---|---|---|
| `{{A}}` | a value that itself contains a placeholder | `{{UNRESOLVED}}` | none |
EOF
if sh "$GATES/dry-run-replay.sh" "$d" "$WORK/scratch-dry" >/dev/null 2>&1; then
  bad "dry-run-replay fails on a residual {{}} token"
else
  ok "dry-run-replay fails on a residual {{}} token"
fi

# ── PARAMETERS.md without assets/templates/ must fail ────────────────────────
d="$(mkskill "orphan")"
echo "# Parameters" > "$d/PARAMETERS.md"
if sh "$GATES/validate-skill.sh" "$d" 2>&1 | grep -q 'FAIL: bijection: PARAMETERS.md exists but assets/templates/'; then
  ok "validate-skill fails PARAMETERS.md with no assets/templates/"
else
  bad "validate-skill fails PARAMETERS.md with no assets/templates/"
fi

# ── ab-validate: the row lifecycle must survive its own merges ──────────────
# When a delta lands in the baseline its row measures identically in both arms.
# If that reported UNPROVEN, `make ab-validate` would fail forever the moment
# any change merged — the trap in replacing the hardcoded baseline pin with a
# merge base. The script's --self-test asserts each classification, and that
# every shipped delta row declares the `since=` that makes the conversion work.
AB="$GATES/../ab-validate.py"
if [ -f "$AB" ]; then
  if out="$(python3 "$AB" --self-test 2>&1)" && \
     printf '%s\n' "$out" | grep -q '^AB_SELFTEST: PASS'; then
    ok "ab-validate row lifecycle ($(printf '%s\n' "$out" | grep -c '^PASS:') assertions)"
  else
    printf '%s\n' "$out" | grep '^FAIL:' | sed 's/^/  /'
    bad "ab-validate row lifecycle"
  fi
else
  bad "ab-validate.py not found at $AB"
fi

# ── A dangling reference in SKILL.md must fail ───────────────────────────────
d="$(mkskill "dangle")"
printf '\nSee [detail](references/does-not-exist.md) for more.\n' >> "$d/SKILL.md"
if sh "$GATES/validate-skill.sh" "$d" 2>&1 | grep -q 'FAIL: dangling reference'; then
  ok "validate-skill fails a dangling references/ path"
else
  bad "validate-skill fails a dangling references/ path"
fi

exit $rc
