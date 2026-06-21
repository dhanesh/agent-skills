# base-in-reality — Design Spec

**Date:** 2026-06-19
**Status:** Approved (brainstorming) → ready for implementation plan
**Authoring tool:** `repo2skill` machinery (SKILL.md conventions, parameterized templates, `validate-skill.sh` / `scan-leaks.sh` / `dry-run-replay.sh` quality gate)
**Repo:** `dhanesh/agent-skills` (personal skills collection, installed via `npx skills add dhanesh/agent-skills --skill base-in-reality`)

---

## 1. Purpose

A read-only, research-grounded audit skill. Point it at a repository and it validates the
**codebase, architectural approach, and business logic** against real-world knowledge drawn
from authoritative sources (arxiv, PubMed, Google Scholar, JSTOR, OpenAlex, Crossref,
Semantic Scholar, plus standards bodies: NIST, IETF/RFC, OWASP, ISO, sector regulators).

The skill exists to answer one question for any given design decision in the repo:

> *"Does this violate an established norm, standard, algorithm, or best practice — and can we
> prove it against a real, citable source?"*

The defining constraint is **grounding**: the skill must never assert a violation against a
fabricated or unverifiable authority. Every finding is tied to a source the agent actually
fetched in-session. This is the whole value proposition, enforced as an invariant (§6), not a
hope.

### Non-goals (YAGNI)

- Not a linter / static analyzer / SAST tool — it reasons about *norms*, not syntax or CVEs.
- Not a code fixer — read-only by default; the only write path is opt-in inline comment markers.
- No paid/institutional source integration in v1 (JSTOR full-text behind paywall is reached
  best-effort via WebSearch/WebFetch metadata, not authenticated APIs).
- No persistent index / vector store — each run is self-contained.

---

## 2. Execution model

**Deep research audit** — multi-agent, comprehensive, token-heavy by design. Six stages, with
fan-out at claim-extraction and per-claim verification.

```
repo
 └─(1) Scope & domain detection ──→ domain map (domains, sub-domains, norms surface)
 └─(2) Claim extraction (fan-out) ─→ discrete falsifiable claims [layer, location, ...]
 └─(3) Source routing ─────────────→ per-claim ranked source plan
 └─(4) Verify (fan-out, grounded) ─→ candidate findings + fetched citations + verdict + severity
 └─(5) Refute (fan-out, adversarial)→ surviving findings (downgraded/dropped on refute)
 └─(6) Synthesize ─────────────────→ cited report (+ optional inline markers)
```

### Stage detail

**(1) Scope & domain detection.**
Survey the repo: languages, dependency manifests, directory layout, README/docs, DB
schemas/migrations, config. Classify domain(s) and sub-domains (e.g. `fintech/lending`,
`ml/nlp`, `security/crypto`, `healthcare`, `distributed-systems`, `data-engineering`, `web`).
The classification fixes the **norms surface**: which source classes and standards bodies are
in scope for this repo. Output: a **domain map**.
Respects `--domain <x>` override and `--since <ref>` (audit only the changed surface of a diff).

**(2) Claim extraction (fan-out).**
Dispatch subagents over repo regions. Each returns a list of discrete, **falsifiable** claims,
each tagged:
`{ layer ∈ (algo|arch|biz), location (file:line or region), confidence, domain-tag, suggested-source-class }`.
A claim is a checkable assertion about what the code *does*, e.g.:
- `ALGO`: "uses MD5 for password hashing" (`auth/hash.py:14`)
- `ARCH`: "synchronous fan-out to 12 downstream services on the request path"
- `BIZ`: "APR computed as simple interest, not reducing-balance"

Merge + dedup across subagents. Enforce `--max-claims N` (default ~40) with an **explicit
dropped-claims log** (§6: no silent truncation).

**(3) Source routing.**
Map each claim's class to a ranked source plan (full table in `references/source-routing.md`):

| Claim class | Primary sources | Standards / authorities |
|---|---|---|
| Algorithm / data structure / numerics | arxiv, Semantic Scholar, OpenAlex | — |
| Security / cryptography | — | NIST (SP 800-*, FIPS), IETF/RFC, OWASP |
| Medical / clinical / bio | PubMed (E-utilities) | FDA, WHO |
| Fintech / lending / payments | Crossref, OpenAlex | Sector regulator (e.g. RBI), ISO 20022, PCI-DSS |
| Distributed systems / architecture | arxiv, Semantic Scholar | IETF/RFC, CNCF norms |
| Statistics / ML methodology | arxiv, Semantic Scholar, OpenAlex | — |
| General / social-science / domain | Google Scholar, JSTOR, OpenAlex, Crossref | — |
| Fallback (anything) | WebSearch + WebFetch | — |

**(4) Verify (fan-out, grounded).**
Per-claim subagent. Query **keyless APIs first** (arxiv, PubMed E-utilities, Semantic Scholar,
OpenAlex, Crossref — via `assets/scripts/fetch_sources.py`), then WebSearch/WebFetch for
Scholar/JSTOR/standards docs. The subagent must hold **≥1 source it actually fetched** before
asserting anything stronger than `UNCONFIRMED`. Returns a candidate finding with:
- `verdict ∈ { VIOLATION, DEVIATION, OUTDATED, UNCONFIRMED }`
- `severity ∈ { critical, high, medium, low }`
- `citations[]` — each a fetched URL/DOI with the supporting quote/finding
- `recommended_fix`

**(5) Refute (fan-out, adversarial).**
Independent subagent per surviving candidate, prompted to **refute**: attack the finding, the
citation's applicability, and the severity. Defaults to skeptical (uncertain ⇒ downgrade). A
finding is dropped or downgraded if the refuter succeeds. Only findings that survive reach the
report at `VIOLATION`/`DEVIATION`.

**(6) Synthesize.**
Assemble the report from `assets/templates/report.md`:
exec summary → domain map → findings (claim, location, verdict, severity, citations, fix) →
sources appendix → dropped-claims log. With `--annotate`, additionally drop
`# BASE-IN-REALITY[sev]: <one-line> — see <report path>` markers at each finding's location.

---

## 3. Sourcing

**Free APIs + WebSearch fallback** — no API keys, fully portable.

- **Primary (keyless, structured/citable):** arxiv API, PubMed E-utilities, Semantic Scholar
  Graph API, OpenAlex, Crossref. Accessed through `assets/scripts/fetch_sources.py`, which
  normalizes results to a common JSON shape (title, authors, year, venue, DOI/URL, abstract).
- **Fallback (unstructured / paywalled):** WebSearch + WebFetch for Google Scholar, JSTOR
  abstracts, NIST/RFC/OWASP documents, ISO summaries, regulator circulars, reputable
  industry/engineering references.

`fetch_sources.py` is **stdlib-only** (`urllib`, `json`, `xml`) so it has zero install
friction and is `uv run`-able with PEP 723 inline metadata. It includes polite rate-limiting
and a descriptive User-Agent per each API's etiquette guidelines.

---

## 4. Targeting

**Auto-extract claims, domain-matched** (Stage 2 above). Fully autonomous: the skill detects
domain(s) and extracts discrete claims across all three layers, routing each to the best
source class. No curated rubric library to maintain in v1 — `references/domains.md` carries the
domain-detection *signals* and per-domain *norms surface*, but claim discovery itself is open
(extraction), not checklist-bound. This keeps coverage broad and avoids a rubric the repo
"passes" while still being wrong in an unanticipated way.

---

## 5. Verdict & evidence discipline

**Grounded + adversarial + graded.**

| Verdict | Meaning |
|---|---|
| `VIOLATION` | Contradicts an established standard/algorithm/best-practice; backed by a fetched authoritative source and survived refutation. |
| `DEVIATION` | Departs from the common/recommended norm but is defensible or context-dependent; sourced + survived refutation. |
| `OUTDATED` | Matches a *superseded* practice; a newer standard/result (cited) has replaced it. |
| `UNCONFIRMED` | Could not be grounded in a fetched source within budget. **Default ceiling for any ungrounded claim.** |

Severity rubric and the full refutation protocol live in `references/verdict-rubric.md`.

---

## 6. Invariants (safety spine)

1. **Read-only by default.** The skill never edits code. The *only* write path is `--annotate`,
   and it writes *comment markers only* — never logic.
2. **No fabricated citations.** Every finding's evidence must be a URL/DOI the agent fetched in
   this session. If a claim cannot be grounded, its verdict is capped at `UNCONFIRMED` and it is
   never reported as `VIOLATION`/`DEVIATION`. (Enforced in the verify-subagent contract and
   re-checked at synthesis.)
3. **Adversarial gate.** No `VIOLATION`/`DEVIATION` survives without passing an independent
   refutation pass.
4. **Bounded, no silent truncation.** `--max-claims` caps work; whatever is dropped is logged
   explicitly in the report so coverage is never overstated.

---

## 7. Output

**Report + optional inline TODOs.**

```
docs/base-in-reality/
  YYYY-MM-DD-audit.md        # always
```

Report sections: Executive summary · Domain map · Findings (claim / location / verdict /
severity / citations / recommended fix) · Sources appendix · Dropped-claims log.

`--annotate` (opt-in, the only code-touching mode) additionally inserts at each finding's
location:

```
# BASE-IN-REALITY[high]: APR computed as simple interest, deviates from <cite> — see docs/base-in-reality/2026-06-19-audit.md
```

---

## 8. Orchestration & portability

**Prompt-driven + optional Workflow.**

- **Core (portable):** `SKILL.md` instructs the agent to dispatch subagents for stages 2, 4, 5.
  Works on any harness that supports subagents.
- **Optional fast path (Claude Code):** `assets/workflow.js` — a deterministic `Workflow`
  script using `pipeline(claims, verify, refute)` with `parallel` fan-out and schema'd
  `agent()` outputs (schema = `assets/templates/findings.schema.json`). The SKILL.md notes it as
  an opt-in accelerator; the skill is fully functional without it.

---

## 9. Files

```
base-in-reality/
  SKILL.md                          # trigger description + 6-stage portable orchestration,
                                    #   invariants, verdict taxonomy, flags
  references/
    source-routing.md               # claim-class → source-class table; API endpoints,
                                    #   query construction, rate-limit etiquette
    verdict-rubric.md               # taxonomy, severity rubric, grounding rule, refutation protocol
    domains.md                      # domain-detection signals + norms surface per domain
  assets/
    templates/report.md             # report skeleton with placeholders
    templates/findings.schema.json  # finding schema (subagent structured output; reused by workflow)
    scripts/fetch_sources.py        # keyless API helper (arxiv/PubMed/Crossref/OpenAlex/Semantic
                                    #   Scholar); stdlib-only, uv-runnable (PEP 723)
    workflow.js                     # OPTIONAL Claude Code Workflow: pipeline(claims → verify → refute)
  README.md
```

Plus repo-level integration: a row in the top-level `README.md` skills table and an install line.

---

## 10. Flags

| Flag | Effect | Default |
|---|---|---|
| `--annotate` | Write inline `# BASE-IN-REALITY[sev]:` markers (only code-touching mode) | off |
| `--layer algo\|arch\|biz` | Restrict extraction/verification to one layer | all three |
| `--domain <x>` | Override auto-detected domain | auto |
| `--since <ref>` | Audit only the diff vs `<ref>`, not the whole repo | whole repo |
| `--max-claims N` | Cap claims per run (dropped surplus logged) | ~40 |

---

## 11. Quality gate (before publish)

Run via repo2skill machinery:
- `validate-skill.sh` — SKILL.md frontmatter/structure conformance
- `scan-leaks.sh` — no secrets/PII in templates or scripts
- `dry-run-replay.sh` — template placeholders resolve, skill is replayable

Plus: README skills-table row + install command; `PARAMETERS.md` documenting every template
placeholder.

---

## 12. Open risks / honest caveats

- **Token cost is real.** Deep mode fans out per claim twice (verify + refute). `--max-claims`
  and `--layer`/`--since` are the throttles; the SKILL.md must set expectations and recommend
  scoping for large repos.
- **Source coverage is uneven.** arxiv/PubMed are strong; JSTOR and paywalled standards (ISO)
  are reachable only via abstracts/summaries. The report must mark such findings' citations as
  *abstract-only* so the reader knows the evidence depth.
- **Domain misdetection** poisons routing. `--domain` override is the escape hatch; the domain
  map is surfaced at the top of the report for the reader to sanity-check.
- **"Best practice" is contested.** The `DEVIATION` verdict + adversarial refutation exist
  precisely so the skill reports *defensible departures* rather than asserting one true way.
