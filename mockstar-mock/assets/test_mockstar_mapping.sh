#!/bin/sh
# mockstar-mock/assets/test_mockstar_mapping.sh
set -eu
DOC="$(dirname "$0")/../references/mockstar-mapping.md"
rc=0
for h in "## match" "## response" "## Scenarios" "## Dynamic handlers" "## Webhooks" "## Tier 2 tokens" "## GraphQL" "## Priority"; do
  if grep -qF "$h" "$DOC"; then echo "PASS: $h"; else echo "FAIL: missing $h"; rc=1; fi
done
# Tier 2 tokens must be documented verbatim so the generator emits valid ones.
for tok in '{{faker.uuid}}' '{{now.iso}}' '{{request.params.' '{{id('; do
  if grep -qF "$tok" "$DOC"; then echo "PASS token: $tok"; else echo "FAIL token: $tok"; rc=1; fi
done
exit $rc
