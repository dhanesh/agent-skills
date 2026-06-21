# Source Routing

This document specifies which source classes to consult for each claim type encountered during a `base-in-reality` audit. The router is deterministic: given a claim class, consult the primary keyless APIs first, then the authority sources listed in the standards column. The goal is the highest-authority citation reachable without human login; keyless APIs (Semantic Scholar, OpenAlex, PubMed, arXiv, CrossRef) are preferred for speed and reproducibility. Standards bodies are consulted when a claim touches compliance, cryptographic requirements, or protocol specifications where peer-reviewed literature alone is insufficient.

## Claim-class → source-class

| Claim class | Primary (keyless API) | Standards / authorities (WebSearch/WebFetch) |
|---|---|---|
| Algorithm / data structure / numerics | arxiv, semanticscholar, openalex | — |
| Security / cryptography | semanticscholar | NIST (SP 800-*, FIPS), IETF/RFC, OWASP |
| Medical / clinical / bio | pubmed | FDA, WHO |
| Fintech / lending / payments | crossref, openalex | sector regulator (e.g. RBI), ISO 20022, PCI-DSS |
| Distributed systems / architecture | arxiv, semanticscholar | IETF/RFC, CNCF |
| Statistics / ML methodology | arxiv, semanticscholar, openalex | — |
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

Some authority sources (JSTOR, ISO store) gate full text behind paywalls. The fetcher retrieves only the abstract or summary in these cases. Citations sourced from abstract-only access must be annotated `abstract_only: true` in the evidence record so reviewers know the full methodology was not verified.
