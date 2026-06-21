# Source Routing

This document specifies which source classes to consult for each claim type encountered during a `base-in-reality` audit. The router is deterministic: given a claim class, consult the primary APIs first, then the authority sources listed in the standards column. The goal is the highest-authority citation reachable without human login. arXiv, PubMed, and CrossRef are genuinely keyless. **Two caveats as of 2026:** Semantic Scholar's unauthenticated pool is heavily throttled and frequently returns HTTP 429 — set `S2_API_KEY` for reliable use, and prefer arXiv/OpenAlex when no key is configured; OpenAlex now meters free usage and removed its polite pool/email param, so a (free) `OPENALEX_API_KEY` is recommended for volume (low-volume audit queries still work without one). Standards bodies are consulted when a claim touches compliance, cryptographic requirements, or protocol specifications where peer-reviewed literature alone is insufficient.

## Claim-class → source-class

| Claim class | Primary (keyless API) | Standards / authorities (WebSearch/WebFetch) |
|---|---|---|
| Algorithm / data structure / numerics | arxiv, openalex, semanticscholar | — |
| Security / cryptography | arxiv, openalex, semanticscholar | NIST (SP 800-*, FIPS), IETF/RFC, OWASP |
| Medical / clinical / bio | pubmed | FDA, WHO |
| Fintech / lending / payments | crossref, openalex | sector regulator (e.g. RBI), ISO 20022, PCI-DSS |
| Distributed systems / architecture | arxiv, openalex, semanticscholar | IETF/RFC, CNCF |
| Statistics / ML methodology | arxiv, openalex, semanticscholar | — |
| General / social-science | openalex, crossref | Google Scholar, JSTOR |
| Fallback (anything) | — | WebSearch + WebFetch |

## Using fetch_sources.py

Invoke the fetcher directly. Use the helper's ABSOLUTE path — verify subagents run from the
target repo, not this skill's directory, so a relative `assets/...` path will not resolve (see
"Locating the source helper" in `SKILL.md`):

```bash
uv run "$FETCH" --source <s> --query "<q>" --limit <n>
# $FETCH = <this skill's base dir>/assets/fetch_sources.py
```

Valid `--source` values:

- `arxiv`
- `semanticscholar`
- `openalex`
- `pubmed`
- `crossref`

Each call returns a list of normalized records with this shape:

```json
{
  "source":   "<source name>",
  "title":    "<paper/standard title>",
  "authors":  ["<author>", "..."],
  "year":     <integer or null>,
  "venue":    "<journal/conference/null>",
  "id":       "<source-native ID>",
  "url":      "<canonical URL>",
  "doi":      "<DOI or null>",
  "abstract": "<abstract text or null>"
}
```

All downstream evidence objects are built from this shape. Any field absent in the API response is `null`; do not fabricate values.

## Query construction tips

- Quote exact algorithm or standard names (e.g. `"RSA-OAEP"`, `"PBKDF2"`, `"ISO 20022"`) to avoid recall noise.
- Include the domain term alongside the technical term (e.g. `"SHA-1 collision resistance cryptography"` rather than just `"SHA-1"`).
- Prefer the standards column (NIST, IETF/RFC, OWASP) for security and compliance claims — peer-reviewed papers describe attacks but standards define the normative requirement.
- Widen with synonyms if initial query returns zero hits: try abbreviation expansions, alternate spellings, or the superseding standard name.

## Etiquette

The fetcher sets a descriptive `User-Agent` header on all outbound requests so API operators can identify and contact the client. PubMed's entrez API requires a two-step fetch (search then fetch); the fetcher enforces a 1-second sleep between the two calls to respect NCBI rate limits — do not bypass this.

**Optional credentials (opt-in, via environment variables).** All sources work keyless for low-volume audits, but two benefit from configuration: set `S2_API_KEY` to avoid Semantic Scholar's frequent HTTP 429s (sent as `x-api-key`); set `OPENALEX_API_KEY` for sustained OpenAlex volume (sent as the `api_key` param — OpenAlex removed its polite pool/email param in 2026); set `CROSSREF_MAILTO` to a contact email to join Crossref's polite pool (added to the User-Agent and as a `mailto` param). None are required; absent them the helper stays fully keyless.

Some authority sources (JSTOR, ISO store) gate full text behind paywalls. The fetcher retrieves only the abstract or summary in these cases. Citations sourced from abstract-only access must be annotated `abstract_only: true` in the evidence record so reviewers know the full methodology was not verified.
