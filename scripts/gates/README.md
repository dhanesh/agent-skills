# Skill Quality Gates

Vendored from the [`repo2skill`](https://github.com/dhanesh/agent-skills) authoring
skill so this repo can enforce the same Agent Skills standard on every skill it ships.

| Script | Checks | Result line |
|--------|--------|-------------|
| `validate-skill.sh <dir> [--spec FILE]` | SKILL.md + README.md exist; frontmatter `name` (kebab, ≤64) and `description` (≤1024); no dangling `references/`,`assets/`,`scripts/` paths; template↔PARAMETERS.md bijection; optional spec-version drift | `VALIDATION_RESULT:` |
| `scan-leaks.sh <dir> [--denylist FILE]` | Secrets/credentials (AWS, GitHub, Slack, Stripe, PEM, JWT, hex, high-entropy, connection strings) and optional source-identifier denylist | `SCAN_RESULT:` |
| `dry-run-replay.sh <dir> <scratch>` | Substitutes `PARAMETERS.md` examples into `assets/templates/*`; fails on residual `{{TOKENS}}` or orphan params. **Requires a `PARAMETERS.md`** — only meaningful for parameterized skills. | `DRY_RUN_RESULT:` |
| `package-skill.sh <dir> <out>` | Produces a distributable archive of the skill | — |

Run all gates across every skill via the repo `Makefile`: `make gate`.

These are vendored copies. To update them, re-copy from `repo2skill/scripts/` and note
the sync in the commit message.
