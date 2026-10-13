#!/bin/sh
# mockstar-mock/assets/test_coverage_report.sh
# gate: offline — runs in `make gate`. Must stay offline and deterministic:
# no network, no bunx/docker, no fixed ports, no wall-clock dependence.
set -eu
DOC="$(dirname "$0")/../references/coverage-report.md"
rc=0
for h in "## Summary" "## Runtime & compatibility" "## Endpoints" "## Grounded vs inferred" "## Review me (speculative)" "## Gaps" "## Dropped" "## Conflicts" "## Request-header matching" "## Encoded responses"; do
  if grep -qF "$h" "$DOC"; then echo "PASS: $h"; else echo "FAIL: missing $h"; rc=1; fi
done
# The header-coverage helper must fill the section the template promises: header names
# as written (casing kept), scenario predicates included, no row for an unmatched mock.
HC="$(dirname "$0")/header_coverage.py"
FIX="$(dirname "$0")/fixtures/header-mocks"
out="$(python3 -I "$HC" "$FIX")"
for want in '## Request-header matching' '`Authorization`' '`x-api-key`' 'scenario:tenant-b' \
            '`X-Tenant-Id`' 'Header names spelled with capitals: 2'; do
  if printf '%s\n' "$out" | grep -qF -- "$want"; then echo "PASS header section: $want"
  else echo "FAIL header section: $want"; rc=1; fi
done
if printf '%s\n' "$out" | grep -qF '/ping'; then
  echo "FAIL header section lists a mock with no header predicate"; rc=1
else
  echo "PASS header section skips mocks with no header predicate"
fi
if python3 -I "$HC" /nonexistent-dir >/dev/null 2>&1; then
  echo "FAIL header helper accepts a missing directory"; rc=1
else
  echo "PASS header helper rejects a missing directory"
fi
exit $rc
