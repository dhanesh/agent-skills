# Enforcement at the commit boundary

Everything else in this skill assumes the authority rules hold. They do not hold on their
own: the bundle is markdown in git, and a provider can open a file, type `consumer_verified`
— accidentally or otherwise — and commit. **The mode-level guards protect the honest path
only. The commit is where the rule is actually enforced.**

Three layers, in increasing strength.

## 1. Path ownership (CODEOWNERS)

Because no file mixes two parties' assertions, plain CODEOWNERS covers most of it. That is
the practical reason acceptance was moved into its own document rather than kept as a field
inside the provider's file: field-level ownership is unenforceable, path ownership is not.

```bash
python3 assets/okf_catalog.py codeowners <bundle>
```

writes `.github/CODEOWNERS`: each `/teams/<id>/` to that team's handle (from
`contacts.github_team`, else `@<org>/<team_id>`), and `/signals/` to the configured
`platform_team`. `dependencies/` has two owners per edge, which CODEOWNERS cannot express —
layer 2 is what protects those fields.

## 2. Diff validation in CI (a required status check)

`enforce` resolves each changed document to the team that authored the commit and rejects
changes that team may not make.

| Code | Fails when |
|---|---|
| `EN-FOREIGN-VERIFICATION` | a Verification's `verifying_team` differs from the commit author's team |
| `EN-SELF-VERIFICATION` | the verifying team owns the capability, or the author is on the providing team — **the highest-signal check**, and it names the other file in the same commit when the provider's own implementation change rode along |
| `EN-AUTHOR-MISMATCH` | `verified_by` is not the commit author |
| `EN-BACKDATED` | `verified_at` is outside tolerance of the commit timestamp in either direction |
| `EN-LIVENESS-OUTSIDE-SIGNALS` | `production_live` appears in a file outside `signals/` |
| `EN-SIGNAL-AUTHOR` | `signals/` was committed by a team other than `platform_team` |
| `EN-CROSS-SIDE-EDIT` | the provider changed `consequence_if_late`/`fallback`/`requested_date`, or the consumer changed `promised_date` |
| `EN-PROXY-ACK` | one side recorded the other side's acknowledgement |

### Running it

`scripts/ci-enforce.sh` builds the changeset from git and runs the check:

```bash
sh scripts/ci-enforce.sh <bundle> <base-ref>      # e.g. origin/main
```

It maps each commit author to a team via the bundle's Team documents — matching
`contacts.github_team`, `contacts.lead`, `claimed_by`, or the `teams/<id>/` path the commit
touched — and passes a JSON changeset to `okf_catalog.py enforce`. To integrate elsewhere,
produce the same JSON yourself:

```json
{"commits": [
  {"sha": "abc1234",
   "author_handle": "bob",
   "author_team": "checkout",
   "committed_at": "2026-08-10T12:00:00Z",
   "files": [
     {"path": "teams/checkout/verifications/payments-initiate-refund-production.md",
      "content": "<file contents after the change>",
      "previous": "<file contents before, omit for a new file>"}
   ]}
]}
```

`enforce` exits 1 with one `VIOLATION:` line per finding, then
`ENFORCE_RESULT: FAIL (n violation(s))`. Wire it as a **required** check:

```yaml
# .github/workflows/catalog.yml
on: [pull_request]
jobs:
  catalog:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - run: python3 assets/okf_catalog.py validate .
      - run: sh scripts/ci-enforce.sh . origin/${{ github.base_ref }}
      - run: python3 assets/okf_catalog.py audit . --fail-on-high   # optional, once coverage is up
```

## 3. Reconciliation against git history

Frontmatter must agree with the commit that carried it: `verified_by` matches the commit
author, `verified_at` sits within tolerance of the commit timestamp, and `commit_sha` is
written by CI rather than by hand. A hand-typed date that predates its own commit is a
backdated claim. Require signed commits on the bundle repo if the org supports it — the
whole model rests on author identity being real.

## Two honest limits

State these in the org's README rather than letting someone discover them later.

1. **This stops a provider from *asserting* acceptance. It cannot stop a consumer from
   verifying carelessly** and committing it themselves. The catalog moves the failure from
   "nobody checked" to "the right party checked badly", which is a much smaller class.
2. **Anyone with admin rights can bypass a required check.** So `audit` should independently
   reconcile frontmatter against `git log` and report divergence, rather than trusting that
   CI ran. Treat a green check as evidence, not proof.
