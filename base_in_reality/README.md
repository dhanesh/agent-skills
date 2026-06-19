# base_in_reality

A read-only, research-grounded repository audit skill. It validates a repo's **codebase,
architecture, and business logic** against real-world knowledge — academic literature
(arxiv, PubMed, Google Scholar, JSTOR, OpenAlex, Crossref, Semantic Scholar) and standards
bodies (NIST, IETF/RFC, OWASP, ISO, sector regulators) — and flags anything that violates
an established norm, standard, algorithm, or best practice.

Every finding is tied to a source the agent actually fetched. Ungrounded claims are
reported as `UNCONFIRMED`, never as violations — there is no fabricated authority.

## Install

```bash
npx skills add dhanesh/agent-skills --skill base_in_reality
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
dropped-claims log. Read-only unless you pass `--annotate`.

See `SKILL.md` for the full procedure, flags, and invariants.
