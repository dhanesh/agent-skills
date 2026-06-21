---
name: base-in-reality
description: "Validate a repository's codebase, architecture, and business logic against real-world knowledge from authoritative sources (arxiv, PubMed, Google Scholar, JSTOR, OpenAlex, Crossref, Semantic Scholar) plus standards bodies (NIST, IETF/RFC, OWASP, ISO, sector regulators). Use when you want a research-grounded audit that flags algorithms, architectural choices, or business rules that violate established norms, standards, or best practices — each finding tied to a real, fetched citation. Read-only by default; emits a severity-graded cited report. Extracts falsifiable claims across algo/arch/biz layers, routes each to the right source class, verifies against fetched evidence, and adversarially refutes before reporting. Not a linter, SAST, or CVE scanner — it reasons about norms, not syntax."
x-spec-version: 1.0
---

# base-in-reality

A read-only, research-grounded repository audit. It answers one question for each
notable design decision in a repo: *does this violate an established norm, standard,
algorithm, or best practice — provably, against a real source?*

The defining rule: **no fabricated authority.** Every finding is tied to a source the
agent actually fetched in-session. Ungrounded claims are reported as `UNCONFIRMED`, never
as violations.

## When to use

Use when you want to sanity-check a codebase, architecture, or business logic against the
state of the art and against domain standards — e.g. "is our APR calculation correct per
lending norms?", "does our crypto follow NIST?", "is this consensus approach sound?",
"does our ML eval avoid known leakage pitfalls?". Works on any repo; adapts to its
domain(s).

## Invariants (do not violate)

1. **Read-only by default.** Never edit code. The only write path is `--annotate`, which
   inserts comment markers only — never logic.
2. **No fabricated citations.** Cite only URLs/DOIs fetched this session. Ungrounded ⇒
   `UNCONFIRMED`. See `references/verdict-rubric.md`.
3. **Adversarial gate.** No `VIOLATION`/`DEVIATION` is reported without surviving a
   refutation pass.
4. **No silent truncation.** If `--max-claims` caps extraction, list what was dropped in
   the report's Dropped-claims log.

## Flags

- `--annotate` — also insert `# BASE-IN-REALITY[<sev>]: <one-line> — see <report>` markers
  at finding locations (the only code-touching mode). Default: off.
- `--layer algo|arch|biz` — restrict to one layer. Default: all three.
- `--domain <x>` — override auto-detected domain. Default: auto.
- `--since <ref>` — audit only the diff vs `<ref>`. Default: whole repo.
- `--max-claims N` — cap claims per run. Default: 40.

## Procedure

Run these six stages. Dispatch subagents where noted (fan-out). On Claude Code you may
instead feed `assets/workflow.mjs` to the Workflow tool as a deterministic accelerator —
it performs stages 2/4/5 with parallel fan-out; the steps below are the portable path.

**Locating the source helper (do this first).** The bundled `fetch_sources.py` is referenced
by a path relative to THIS skill's directory, but the verify subagents (stage 4) run from the
*target repo*, not from here — a path like `assets/fetch_sources.py` will not resolve for them.
Before dispatching any subagent, resolve the helper's ABSOLUTE path once and pass it into each
subagent's prompt:

- Use this skill's base directory (your harness provides it when the skill loads) and set
  `FETCH="<skill-base-dir>/assets/fetch_sources.py"`.
- If you don't have the base directory, discover it:
  `FETCH=$(find ~/.claude ~/.config ~/.agents -path '*base-in-reality*/assets/fetch_sources.py' 2>/dev/null | head -1)`
- Verify it resolves: `uv run "$FETCH" --source openalex --query test --limit 1` should emit JSON.
- Hand subagents the literal absolute `$FETCH` value — never a relative `assets/`-prefixed form.

1. **Scope & domain detection.** Survey languages, dependency manifests, directory layout,
   README/docs, DB schemas/migrations, config. Classify the domain(s) using
   `references/domains.md` detection signals; fix the norms surface. Honor `--domain` and
   `--since`. Produce the domain map.

2. **Claim extraction (fan-out).** Dispatch subagents over repo regions. Each returns
   discrete, FALSIFIABLE claims tagged `{layer (algo|arch|biz), location (file:line),
   confidence, domain-tag}`. A claim is a checkable assertion about what the code does
   (e.g. "uses MD5 for password hashing", "synchronous fan-out to 12 services on the
   request path", "APR computed as simple interest"). Merge and dedup. Enforce
   `--max-claims`; record any surplus for the Dropped-claims log.

3. **Source routing.** For each claim, pick a ranked source plan from
   `references/source-routing.md` (algorithm→arxiv/Semantic Scholar; security→NIST/RFC/
   OWASP; medical→PubMed; fintech→regulator+standards; general→Scholar/JSTOR/OpenAlex).

4. **Verify (fan-out, grounded).** Per claim, dispatch a subagent that queries keyless APIs
   first via the bundled source helper at the absolute `$FETCH` path resolved above
   (`uv run "$FETCH" --source <s> --query "<q>" --limit <n>` — pass the literal absolute path
   into the subagent; a skill-relative `assets/`-prefixed path will not resolve from the target repo),
   then WebSearch/WebFetch for standards and paywalled sources. It must hold ≥1 fetched source
   before asserting anything stronger than `UNCONFIRMED`. Output a finding conforming to
   `assets/findings.schema.json`: `verdict ∈ {VIOLATION,DEVIATION,OUTDATED,UNCONFIRMED}`,
   `severity ∈ {critical,high,medium,low}`, `citations[]` (each with the fetched URL/DOI and
   a supporting quote; mark `abstract_only` where only the abstract was available),
   `recommended_fix`.

5. **Refute (fan-out, adversarial).** For each candidate `VIOLATION`/`DEVIATION`, dispatch
   independent refuters across distinct lenses (correctness, citation-applicability,
   severity) per `references/verdict-rubric.md`. Each defaults to skeptical. Downgrade to
   `UNCONFIRMED` when ≥2 of 3 refute.

6. **Synthesize.** Fill `assets/report-skeleton.md` and write it to
   `docs/base-in-reality/<YYYY-MM-DD>-audit.md`: executive summary, domain map, findings
   (ordered by severity then layer), sources appendix, dropped-claims log. If `--annotate`,
   insert the comment markers at each finding's location.

## References

- `references/source-routing.md` — claim-class → source-class table, API usage, etiquette.
- `references/verdict-rubric.md` — verdict taxonomy, severity, grounding rule, refutation.
- `references/domains.md` — domain-detection signals and per-domain norms surface.

## Assets

- `assets/fetch_sources.py` — keyless scholarly-source query helper (stdlib; `uv run`).
- `assets/findings.schema.json` — the finding schema verification subagents emit.
- `assets/report-skeleton.md` — the report template filled in stage 6.
- `assets/workflow.mjs` — optional Claude Code Workflow accelerator.

## Edge cases

- **Uneven source coverage.** arxiv/PubMed are strong; JSTOR/ISO are reachable only via
  abstracts — mark those citations `abstract_only` so evidence depth is visible.
- **Domain misdetection.** Surface the domain map at the top of the report; `--domain` is
  the override.
- **Large repos.** Deep mode fans out twice per claim (verify + refute) and is
  token-heavy. Scope with `--layer`, `--since`, or a lower `--max-claims`.
- **"Best practice" is contested.** Prefer `DEVIATION` over `VIOLATION` for defensible
  departures; the refutation pass exists to stop the skill asserting one true way.
