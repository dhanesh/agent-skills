#!/bin/sh
# mockstar-mock/assets/test_mockstar_mapping.sh
# gate: offline — runs in `make gate`. Must stay offline and deterministic:
# no network, no bunx/docker, no fixed ports, no wall-clock dependence.
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
# Webhook signing (mockstar >= 0.3.0). Signing placeholders are SINGLE-brace and disjoint from
# the {{ }} request-template engine; each must be documented verbatim so the generator emits
# a config mockstar's config-load validator accepts.
if grep -qF "### Signing (requires mockstar >= 0.3.0)" "$DOC"; then
  echo "PASS: signing section"
else
  echo "FAIL: missing signing section"; rc=1
fi
for tok in '{body}' '{timestamp}' '{timestampSeconds}' '{signature}' '{algorithm}'; do
  if grep -qF "$tok" "$DOC"; then echo "PASS signing placeholder: $tok"; else echo "FAIL signing placeholder: $tok"; rc=1; fi
done
# Provider cookbook — a named recipe the generator can expand instead of hand-rolling a scheme.
for p in 'mockstar' 'github' 'slack' 'stripe' 'shopify' 'razorpay'; do
  if grep -qF "| \`$p\` |" "$DOC"; then echo "PASS provider: $p"; else echo "FAIL provider: $p"; rc=1; fi
done
# Secret handling: refs only, never inline (mockstar rejects inline secrets at config-load).
for s in 'secretRef' '{{ env.NAME }}' 'file:/path'; do
  if grep -qF "$s" "$DOC"; then echo "PASS secret rule: $s"; else echo "FAIL secret rule: $s"; rc=1; fi
done
# The two must-contain rules; a block breaking either fails the Stage-5 boot.
if grep -qF 'must** contain `{body}`' "$DOC" && grep -qF 'must** contain `{signature}`' "$DOC"; then
  echo "PASS: signing must-contain rules"
else
  echo "FAIL: missing signing must-contain rules"; rc=1
fi
exit $rc
