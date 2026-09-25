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

# ── readme-catalog: the root README must list every skill, and only skills ───
# A skill once shipped with no install line, because no gate read the README.
# Each case is a fresh temp repo holding two skills, so a regex that matches
# nothing (or everything) flips a row instead of passing quietly.
mkcatalog() {
  r="$WORK/catalog-$1"
  rm -rf "$r"; mkdir -p "$r/alpha" "$r/beta"
  : > "$r/alpha/SKILL.md"; : > "$r/beta/SKILL.md"
  printf '%s\n' "$r"
}
catalog_readme() {  # $1 repo, then the skills to install, a --, then the table rows
  r="$1"; shift
  { printf '# Skills\n\n```bash\nnpx skills add dhanesh/agent-skills --skill <skill-name>\n'
    while [ "$1" != "--" ]; do printf 'npx skills add dhanesh/agent-skills --skill %s\n' "$1"; shift; done; shift
    printf '```\n\n| Skill | Description |\n|-------|-------------|\n'
    for row in "$@"; do printf '| %s | does a thing |\n' "$row"; done
  } > "$r/README.md"
}
catalog_case() {  # $1 label, $2 expected PASS|FAIL, $3 repo
  if sh "$GATES/readme-catalog.sh" "$3" 2>&1 | grep -q "^README_CATALOG_RESULT: $2"; then
    ok "readme-catalog: $1"
  else
    bad "readme-catalog: $1 (expected README_CATALOG_RESULT: $2)"
  fi
}
r="$(mkcatalog complete)"; catalog_readme "$r" alpha beta -- '[`alpha`](alpha/)' '[`beta`](beta/)'
catalog_case "a complete catalog passes" PASS "$r"
r="$(mkcatalog noinstall)"; catalog_readme "$r" alpha -- '[`alpha`](alpha/)' '[`beta`](beta/)'
catalog_case "a skill with no install line fails" FAIL "$r"
r="$(mkcatalog norow)"; catalog_readme "$r" alpha beta -- '[`alpha`](alpha/)'
catalog_case "a skill with no table row fails" FAIL "$r"
r="$(mkcatalog stale)"; catalog_readme "$r" alpha beta gamma -- '[`alpha`](alpha/)' '[`beta`](beta/)'
catalog_case "an install line for a non-skill fails" FAIL "$r"
r="$(mkcatalog stalerow)"; catalog_readme "$r" alpha beta -- '[`alpha`](alpha/)' '[`beta`](beta/)' '[`gamma`](gamma/)'
catalog_case "a table row for a non-skill fails" FAIL "$r"
r="$(mkcatalog mislink)"; catalog_readme "$r" alpha beta -- '[`alpha`](alpha/)' '[`beta`](alpha/)'
catalog_case "a row whose label and link disagree fails" FAIL "$r"

# ── skill-contract: half-adoptions, drifted copies and stray keys all fail ───
# docs/skill-contract/SPEC.md commandment 1. Each case is a fresh fixture skill,
# so a checker that passes everything (or nothing) flips a row.
REF="$GATES/../../docs/skill-contract/reference/contract_check.py"
mkadopter() {  # $1 name, $2 opt-in line or '', $3 contract JSON or '', $4 extra frontmatter line or ''
  d="$WORK/sc-$1"
  rm -rf "$d"; mkdir -p "$d/assets"
  {
    echo '---'
    echo "name: sc-$1"
    echo 'description: Hands off a task-plan envelope in the gate self-tests.'
    echo 'license: MIT'
    echo 'compatibility: none'
    if [ -n "$4" ]; then echo "$4"; fi
    echo 'metadata:'
    echo '  author: dhanesh'
    echo '  version: "1.0.0"'
    echo '  tags: "fixture"'
    if [ -n "$2" ]; then echo "$2"; fi
    echo '---'
    echo '# fixture'
    echo '## Steps'
    echo '1. Do the thing.'
    echo '## Verify'
    echo 'Run the check.'
    if [ -n "$3" ]; then
      echo ''
      echo '## Contract'
      echo ''
      echo '```json skill-contract'
      echo "$3"
      echo '```'
    fi
  } > "$d/SKILL.md"
  echo "# fixture" > "$d/README.md"
  cp "$REF" "$d/assets/contract_check.py"
  printf '%s\n' "$d"
}
sc_case() {  # $1 label, $2 expected PASS|FAIL, $3 skill dir
  if sh "$GATES/skill-contract.sh" "$3" 2>&1 | grep -q "^SKILL_CONTRACT_RESULT: $2"; then
    ok "skill-contract: $1"
  else
    bad "skill-contract: $1 (expected SKILL_CONTRACT_RESULT: $2)"
  fi
}
OPT='  skill-contract: "1"'
BLOCK='{"provides": ["https://github.com/dhanesh/agent-skills/skill-contract/task-plan/v1"], "consumes": []}'
sc_case "a non-adopter passes" PASS "$(mkadopter plain '' '' '')"
sc_case "a well-formed adopter passes" PASS "$(mkadopter good "$OPT" "$BLOCK" '')"
sc_case "a Contract block without the opt-in fails" FAIL "$(mkadopter noopt '' "$BLOCK" '')"
sc_case "the opt-in without a Contract block fails" FAIL "$(mkadopter noblock "$OPT" '' '')"
d="$(mkadopter drift "$OPT" "$BLOCK" '')"; echo '# drift' >> "$d/assets/contract_check.py"
sc_case "a drifted vendored checker fails" FAIL "$d"
sc_case "an unknown contract key fails" FAIL "$(mkadopter key "$OPT" '{"provides": [], "consumes": [], "extra": 1}' '')"
sc_case "a top-level frontmatter key outside the allowed set fails" FAIL "$(mkadopter fm '' '' 'x-spec-version: 1.0')"

# ── prompting-playbook PP-7: BCP 14 declared, only declared keywords used ────
# docs/superpowers/specs/2026-09-19-bcp14-skills-design.md §3. Each case is a
# fresh fixture; asserts on the PP-7 line only, not the whole verdict.
DECL='The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY in this skill are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and only when, they appear in all capitals.'
mkbcp() {  # $1 name, then the body lines that follow the # title
  d="$WORK/bcp-$1"; shift
  rm -rf "$d"; mkdir -p "$d"
  {
    echo '---'
    echo 'name: bcp-fixture'
    echo 'description: A fixture skill for the PP-7 self-tests.'
    echo '---'
    echo '# fixture'
    echo ''
    for l in "$@"; do echo "$l"; done
  } > "$d/SKILL.md"
  printf '%s\n' "$d"
}
pp7_case() {  # $1 label, $2 expected PP-7 line prefix PASS|FAIL|INFO, $3 skill dir
  if sh "$GATES/prompting-playbook.sh" "$3" 2>&1 | grep -q "^$2: PP-7"; then
    ok "PP-7: $1"
  else
    bad "PP-7: $1 (expected a '$2: PP-7' line)"
  fi
}
pp7_case "a skill with no declaration fails" FAIL "$(mkbcp none 'You MUST check the thing.')"
pp7_case "a declared skill using MUST passes" PASS "$(mkbcp good "$DECL" '' 'You MUST check the thing.')"
pp7_case "a stray SHALL fails" FAIL "$(mkbcp shall "$DECL" '' 'You SHALL check the thing.')"
pp7_case "SHALL inside a code fence is ignored" PASS "$(mkbcp fencedshall "$DECL" '' 'You MUST check it.' '```' 'SHALL' '```')"
pp7_case "keywords only inside a code fence count as none (advisory)" INFO "$(mkbcp fenced "$DECL" '' '```' 'MUST' '```')"
pp7_case "keywords only inside a code fence still PASS PP-7" PASS "$(mkbcp fencedpass "$DECL" '' '```' 'MUST' '```')"
pp7_case "SHALL inside a ~~~ fence is ignored" PASS "$(mkbcp tilde "$DECL" '' 'You MUST check it.' '~~~' 'SHALL' '~~~')"
pp7_case "a \`\`\`\` fence wrapping \`\`\` is one block" PASS "$(mkbcp fourbt "$DECL" '' 'You MUST check it.' '````md' '```' 'SHALL' '```' '````')"
pp7_case "a declaration only inside a code fence does not count" FAIL "$(mkbcp declfence '```' "$DECL" '```' 'You MUST check it.')"
pp7_case "SHALL after the declaration on its line is caught" FAIL "$(mkbcp declsame "$DECL You SHALL check it." 'You MUST check it.')"

# ── prompting-playbook PP-5: every absolute carries a reason ─────────────────
# PP-5 used to weigh absolutes against hedges ("usually", SHOULD, MAY). Current
# guidance reads a hedge on a real requirement as permission to under-deliver,
# and says an absolute earns its place by stating why. So PP-5 now asks each
# never/always/MUST NOT for a reason, in its sentence or the next one. The
# fixture's first body line is line 9 (4 frontmatter lines, title, blank,
# declaration, blank).
pp5_case() {  # $1 label, $2 a grep -E pattern the output must match, $3 skill dir, $4 flags (optional)
  if sh "$GATES/prompting-playbook.sh" "$3" ${4:-} 2>&1 | grep -qE "$2"; then
    ok "PP-5: $1"
  else
    bad "PP-5: $1 (no line matching '$2')"
  fi
}
pp5_none() {  # $1 label, $2 skill dir: no unreasoned-absolute line at all
  if sh "$GATES/prompting-playbook.sh" "$2" 2>&1 | grep -q 'PP-5 unreasoned absolute'; then
    bad "PP-5: $1 (reported an unreasoned absolute)"
  else
    ok "PP-5: $1"
  fi
}
d="$(mkbcp pp5why "$DECL" '' 'You MUST NOT push to main, because CI deploys every commit on it.')"
pp5_none "an absolute with a reason in its sentence passes" "$d"
pp5_case "an absolute with a reason reports PASS" '^PASS: PP-5' "$d"
pp5_none "an absolute with its reason in the next sentence passes" \
  "$(mkbcp pp5next "$DECL" '' 'Never push to main. A push there would deploy untested code.')"
d="$(mkbcp pp5bare "$DECL" '' 'Never push to main.')"
pp5_case "an unreasoned absolute is advisory, with file and line" \
  '^INFO: PP-5 unreasoned absolute: .*/SKILL\.md:9: Never push to main' "$d"
pp5_case "an unreasoned absolute fails under --strict" '^FAIL: PP-5 unreasoned absolute' "$d" --strict
pp5_case "an unreasoned absolute in another paragraph's sentence is not rescued" \
  '^INFO: PP-5 unreasoned absolute: .*:9:' \
  "$(mkbcp pp5para "$DECL" '' 'Never push to main.' '' 'A push there would deploy untested code.')"
pp5_case "hedges no longer balance an unreasoned absolute" '^INFO: PP-5 unreasoned absolute' \
  "$(mkbcp pp5hedge "$DECL" '' 'Never push to main unless asked.' 'Usually prefer small commits; when in doubt, by default, you SHOULD ask.' 'You MAY skip it.')"
pp5_none "code is exempt (fences and inline code)" \
  "$(mkbcp pp5code "$DECL" '' 'Run `never-push --always` first.' '```' 'never push to main' '```')"
if out="$(python3 "$GATES/pp5_reasons.py" --self-test 2>&1)" && \
   printf '%s\n' "$out" | grep -q '^PP5_SELFTEST: PASS'; then
  ok "PP-5 reason detector ($(printf '%s\n' "$out" | grep -c '^PASS:') cases)"
else
  printf '%s\n' "$out" | grep '^FAIL:' | sed 's/^/  /'
  bad "PP-5 reason detector self-test"
fi

# ── bcp14-registry: every keyword sentence has a register row at its level ───
# PP-7 checks the declaration and the vocabulary; nothing checked that a MUST
# added to a SKILL.md was classified, so the register fell behind by dozens of
# sentences. Each case is a fresh temp repo: one skill plus a register, so a
# checker that passes everything (or nothing) flips a row.
mkreg() {  # $1 name, $2 register rows for alpha (or the literal NOSECTION), then SKILL.md body lines
  r="$WORK/reg-$1"; rows="$2"; shift 2
  rm -rf "$r"; mkdir -p "$r/alpha" "$r/docs/rfc2119"
  {
    echo '---'
    echo 'name: alpha'
    echo 'description: A fixture skill for the bcp14-registry self-tests.'
    echo '---'
    echo '# alpha'
    echo ''
    echo "$DECL"
    echo ''
    for l in "$@"; do echo "$l"; done
  } > "$r/alpha/SKILL.md"
  {
    echo '# BCP 14 classification'
    echo ''
    if [ "$rows" != NOSECTION ]; then
      echo '## alpha (1 candidates)'
      echo ''
      echo '| id | line | sentence | Jev level (conf, harm) | final | departure reason |'
      echo '|---|---|---|---|---|---|'
      [ -n "$rows" ] && printf '%s\n' "$rows"
    fi
  } > "$r/docs/rfc2119/2026-09-19-classification.md"
  printf '%s\n' "$r"
}
reg_case() {  # $1 label, $2 expected PASS|FAIL, $3 repo, $4 a FAIL reason the output must name (optional)
  st=0; out="$(sh "$GATES/bcp14-registry.sh" "$3" 2>&1)" || st=$?
  if printf '%s\n' "$out" | grep -q "^BCP14_RESULT: $2"; then
    if [ "$2" = FAIL ] && [ "$st" -eq 0 ]; then
      bad "bcp14-registry: $1 (printed FAIL but exited 0)"
    elif [ "$2" = PASS ] && [ "$st" -ne 0 ]; then
      bad "bcp14-registry: $1 (printed PASS but exited $st)"
    elif [ -n "${4:-}" ] && ! printf '%s\n' "$out" | grep -q "^FAIL: alpha $4"; then
      bad "bcp14-registry: $1 (no 'FAIL: alpha $4' line)"
    else
      ok "bcp14-registry: $1"
    fi
  else
    bad "bcp14-registry: $1 (expected BCP14_RESULT: $2)"
  fi
}
ROW_MUST='| c1 | 9 | You MUST check the thing before you ship it. | MUST (0.90, 0.80) | MUST |  |'
ROW_SHOULD='| c1 | 9 | You MUST check the thing before you ship it. | SHOULD (0.40, 0.30) | SHOULD |  |'
ROW_TRUNC='| c1 | 9 | **Check first.** The thing is… | MUST (0.90, 0.80) | MUST |  |'
reg_case "a registered keyword sentence at its level passes" PASS \
  "$(mkreg clean "$ROW_MUST" 'You MUST check the thing before you ship it.' '' 'Plain prose has no keyword.')"
reg_case "a truncated row covers the rest of its paragraph" PASS \
  "$(mkreg trunc "$ROW_TRUNC" '**Check first.** The thing is' 'fragile, so you MUST check it.')"
reg_case "a keyword inside inline code or a fence needs no row" PASS \
  "$(mkreg code '' 'Run `MUST` as a literal.' '```' 'You MUST NOT see this.' '```')"
reg_case "an unregistered MUST fails" FAIL \
  "$(mkreg unreg "$ROW_MUST" 'You MUST check the thing before you ship it.' '' 'You MUST NOT skip the other thing.')" "unregistered"
reg_case "a row whose level differs from the text fails" FAIL \
  "$(mkreg level "$ROW_SHOULD" 'You MUST check the thing before you ship it.')" "level"
reg_case "a lowercase must in prose fails" FAIL \
  "$(mkreg lower '' 'The agent must check the thing.')" "lowercase"
reg_case "a lowercase shall in prose fails" FAIL \
  "$(mkreg shall '' 'The agent shall check the thing.')" "lowercase"
reg_case "a lowercase must inside inline code passes" PASS \
  "$(mkreg lowercode '' 'Write each requirement as a `must` statement.')"
reg_case "a \`\`\`\` span with backticks after it is inline code, not a fence" FAIL \
  "$(mkreg infostr '' 'Use the component, not a' '   ```` ```mermaid ```` block.' '' 'You MUST NOT skip this.')" "unregistered"
reg_case "a skill with no register section fails" FAIL \
  "$(mkreg nosection NOSECTION 'Plain prose has no keyword.')" "section"

exit $rc
