# Skill Quality Gates

Vendored from the [`repo2skill`](../../repo2skill/) authoring skill — now shipped in
this repo at `repo2skill/` — so this repo can enforce the same Agent Skills standard
on every skill it ships.

| Script | Checks | Result line |
|--------|--------|-------------|
| `validate-skill.sh <dir> [--spec FILE]` | SKILL.md + README.md exist; frontmatter `name` (kebab, ≤64) and `description` (≤1024); no dangling `references/`,`assets/`,`scripts/` paths; template↔PARAMETERS.md bijection; optional spec-version drift | `VALIDATION_RESULT:` |
| `scan-leaks.sh <dir> [--denylist FILE]` | Secrets/credentials (AWS, GitHub, Slack, Stripe, PEM, JWT, hex, high-entropy, connection strings) and optional source-identifier denylist | `SCAN_RESULT:` |
| `dry-run-replay.sh <dir> <scratch>` | Substitutes `PARAMETERS.md` examples into `assets/templates/*`; fails on residual `{{TOKENS}}` or orphan params. **Requires a `PARAMETERS.md`** — only meaningful for parameterized skills. | `DRY_RUN_RESULT:` |
| `prompting-playbook.sh <dir> [--strict]` | Lints SKILL.md against [The Prompting Playbook](../../docs/prompting-playbook.md) conventions: structured control surface (PP-1), decomposed single-job workflow (PP-2), explicit output protocol (PP-3), verification baked in (PP-4); advisory reason check (PP-5: every never/always/MUST NOT directive states why, via `pp5_reasons.py`) and lean-context/progressive-disclosure (PP-6); BCP 14 declaration and declared-keywords-only (PP-7, hard). `--strict` promotes the advisories to hard failures. | `PLAYBOOK_RESULT:` |
| `frontmatter-standard.sh <dir>` | Standard frontmatter metadata: `license`, `compatibility`, and `metadata` (`author`/`version`/`tags`) | `FRONTMATTER_RESULT:` |
| `skill-contract.sh <skill-dir>` | Commandments 1–2 of `docs/skill-contract/SPEC.md`, via the reference checker, on every skill: frontmatter keys inside the Agent Skills allowed set; the opt-in and the `## Contract` block both present or both absent; an adopter's vendored `assets/contract_check.py` byte-identical to the reference (`make contract-vendor`). | `SKILL_CONTRACT_RESULT:` |
| `readme-catalog.sh [repo-root]` | Repo-level, run once per `make gate` (`make readme` alone): every skill has an install line and a Skills-table row in the root README.md, and no install line or row names a directory that isn't a skill (a row's label must match its link). Order and description accuracy are not checked. | `README_CATALOG_RESULT:` |
| `bcp14-registry.sh [repo-root]` | Repo-level, run once per `make gate` (`make bcp14` alone), logic in `bcp14_registry.py` (stdlib): every capitalised BCP 14 keyword in a `*/SKILL.md` body (fences, inline code and the declaration skipped) is covered by a row in `docs/rfc2119/2026-09-19-classification.md` whose final level equals the strongest keyword it covers; rows match on normalised sentence text (a row's text ends at its first `…`, and a truncated row covers the rest of its paragraph), never on line number. No lowercase `must`/`shall` in SKILL.md prose; every skill has a register section; no orphan rows (every row quotes text its skill still has, unless its line column says `removed`, in which case the text must be gone). `--counts` prints the tallies `scripts/ab-validate.py` compares. | `BCP14_RESULT:` |
| `run-eval.sh <dir>` | The skill's outcome eval (`eval/run_eval.py` per [docs/eval-standard.md](../../docs/eval-standard.md)): deterministic harness → skill tooling → model-free grader, negative fixtures mandatory. **Missing eval fails.** | `EVAL_RESULT:` |
| `package-skill.sh <dir> <out>` | Produces a distributable archive of the skill | — |

Run all gates across every skill via the repo `Makefile`: `make gate`.

`make playbook` runs only the playbook gate (full per-check output); `make playbook PLAYBOOK_FLAGS=--strict`
(or `make gate PLAYBOOK_FLAGS=--strict`) runs it in strict mode.

`validate-skill.sh`, `scan-leaks.sh`, `dry-run-replay.sh`, and `package-skill.sh` originated in the
`repo2skill` authoring skill; now that it ships in this repo, this directory is their canonical home —
edit them here and note the change in the commit message. `prompting-playbook.sh`, `frontmatter-standard.sh`,
`run-eval.sh`, `readme-catalog.sh` and `bcp14-registry.sh` are repo-local gates that never went through `repo2skill`.
