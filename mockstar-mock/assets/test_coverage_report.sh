#!/bin/sh
# mockstar-mock/assets/test_coverage_report.sh
set -eu
DOC="$(dirname "$0")/../references/coverage-report.md"
rc=0
for h in "## Summary" "## Endpoints" "## Grounded vs inferred" "## Review me (speculative)" "## Gaps" "## Dropped" "## Conflicts"; do
  if grep -qF "$h" "$DOC"; then echo "PASS: $h"; else echo "FAIL: missing $h"; rc=1; fi
done
exit $rc
