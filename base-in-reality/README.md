# base-in-reality

A read-only, research-grounded repository audit skill. It validates a repo's **codebase,
architecture, and business logic** against real-world knowledge — academic literature
(arxiv, PubMed, Google Scholar, JSTOR, OpenAlex, Crossref, Semantic Scholar) and standards
bodies (NIST, IETF/RFC, OWASP, ISO, sector regulators) — and flags anything that violates
an established norm, standard, algorithm, or best practice.

Every finding **must** be tied to a source the agent actually fetched, and the shipped
linter checks that rather than taking the agent's word for it: `fetch_sources.py` appends
every retrieved URL/DOI to a session evidence log, and `report_lint.py --evidence <log>`
fails any citation flagged `fetched` that appears nowhere in it. Ungrounded claims are
reported as `UNCONFIRMED`, never as violations. Linting without `--evidence` only shape-checks
citations, and the result line says so.

## Install

```bash
npx skills add dhanesh/agent-skills --skill base-in-reality
```

## What it does

1. Detects the repo's domain(s) and the standards surface that applies.
2. Extracts falsifiable claims across three layers: algorithm/code, architecture, business logic.
3. Routes each claim to the right authoritative source class.
4. Verifies against fetched evidence, then adversarially refutes before reporting.
5. Emits a severity-graded, cited Markdown report (and, with `--annotate`, inline markers).

## Output

`docs/base-in-reality/<YYYY-MM-DD>-audit.md` — executive summary, domain map, findings
(claim · location · verdict · severity · citations · fix), sources appendix, and a
dropped-claims log. It never edits code: `--annotate` adds comment markers only, and the
one file it creates on the default path is the report itself, at
`docs/base-in-reality/<date>-audit.md` — announced before it is written.

See `SKILL.md` for the full procedure, flags, and invariants.
