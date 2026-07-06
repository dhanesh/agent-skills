#!/bin/sh
# mockstar-mock/assets/test_input_adapters.sh — assert every adapter section exists.
set -eu
DOC="$(dirname "$0")/../references/input-adapters.md"
rc=0
for h in "## OpenAPI" "## Postman" "## HAR" "## curl" "## GraphQL" "## Prose (Markdown / PDF / DOCX / URL)" "## Merge & dedupe"; do
  if grep -qF "$h" "$DOC"; then
    echo "PASS: found '$h'"
  else
    echo "FAIL: missing '$h'"; rc=1
  fi
done
exit $rc
