# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""fetch_sources.py — keyless scholarly-source query helper for base_in_reality.

Queries arxiv, PubMed, Crossref, OpenAlex, or Semantic Scholar and prints a
normalized JSON array of records to stdout. Stdlib-only (urllib/json/xml) so it
runs anywhere via `uv run fetch_sources.py --source <s> --query "<q>"`.
"""
from __future__ import annotations

from urllib.parse import urlencode

SOURCES = ("arxiv", "pubmed", "crossref", "openalex", "semanticscholar")


def build_query_url(source: str, query: str, limit: int = 5) -> str:
    source = source.lower()
    if source == "arxiv":
        params = urlencode({"search_query": f"all:{query}", "start": 0, "max_results": limit})
        return f"http://export.arxiv.org/api/query?{params}"
    if source == "pubmed":
        params = urlencode({"db": "pubmed", "retmode": "json", "retmax": limit, "term": query})
        return f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{params}"
    if source == "crossref":
        params = urlencode({"rows": limit, "query": query})
        return f"https://api.crossref.org/works?{params}"
    if source == "openalex":
        params = urlencode({"per_page": limit, "search": query})
        return f"https://api.openalex.org/works?{params}"
    if source == "semanticscholar":
        params = urlencode({
            "limit": limit,
            "fields": "title,authors,year,venue,externalIds,abstract",
            "query": query,
        })
        return f"https://api.semanticscholar.org/graph/v1/paper/search?{params}"
    raise ValueError(f"unknown source: {source!r} (expected one of {', '.join(SOURCES)})")
