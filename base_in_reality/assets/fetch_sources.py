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


import json
import xml.etree.ElementTree as ET

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"


def _record(source, title, authors, year, venue, url, doi, abstract, src_id=None):
    return {
        "source": source,
        "title": (title or "").strip(),
        "authors": [a for a in (authors or []) if a],
        "year": year,
        "venue": venue or "",
        "id": src_id or url or "",
        "url": url or "",
        "doi": doi,
        "abstract": (abstract or "").strip()[:1000],
    }


def _year_prefix(s):
    s = (s or "")[:4]
    return int(s) if s.isdigit() else None


def normalize_arxiv(raw):
    root = ET.fromstring(raw)
    out = []
    for e in root.findall(f"{_ATOM}entry"):
        url = (e.findtext(f"{_ATOM}id") or "").strip()
        authors = [(a.findtext(f"{_ATOM}name") or "").strip() for a in e.findall(f"{_ATOM}author")]
        out.append(_record(
            "arxiv", e.findtext(f"{_ATOM}title"), authors,
            _year_prefix(e.findtext(f"{_ATOM}published")), "arXiv",
            url, e.findtext(f"{_ARXIV}doi"), e.findtext(f"{_ATOM}summary"), src_id=url))
    return out


def normalize_crossref(raw):
    items = (json.loads(raw).get("message") or {}).get("items") or []
    out = []
    for it in items:
        authors = [f"{a.get('given','')} {a.get('family','')}".strip() for a in it.get("author", [])]
        dp = (it.get("issued") or {}).get("date-parts") or [[None]]
        year = dp[0][0] if dp and dp[0] else None
        doi = it.get("DOI")
        out.append(_record(
            "crossref", (it.get("title") or [""])[0], authors, year,
            (it.get("container-title") or [""])[0],
            it.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
            doi, it.get("abstract"), src_id=doi))
    return out


def _openalex_abstract(inv):
    if not inv:
        return ""
    pos = [(i, w) for w, idxs in inv.items() for i in idxs]
    pos.sort()
    return " ".join(w for _, w in pos)


def normalize_openalex(raw):
    out = []
    for w in json.loads(raw).get("results", []):
        src = (w.get("primary_location") or {}).get("source") or {}
        doi = w.get("doi")
        authors = [(a.get("author") or {}).get("display_name") or "" for a in w.get("authorships", [])]
        out.append(_record(
            "openalex", w.get("title") or w.get("display_name"), authors,
            w.get("publication_year"), src.get("display_name", ""),
            doi or w.get("id") or "", doi,
            _openalex_abstract(w.get("abstract_inverted_index")), src_id=w.get("id")))
    return out


def normalize_pubmed(raw):
    res = json.loads(raw).get("result") or {}
    out = []
    for uid in res.get("uids", []):
        rec = res.get(uid) or {}
        doi = None
        for aid in rec.get("articleids", []):
            if aid.get("idtype") == "doi":
                doi = aid.get("value")
        out.append(_record(
            "pubmed", rec.get("title"), [a.get("name", "") for a in rec.get("authors", [])],
            _year_prefix(rec.get("pubdate")),
            rec.get("fulljournalname") or rec.get("source", ""),
            f"https://pubmed.ncbi.nlm.nih.gov/{uid}/", doi, "", src_id=uid))
    return out


def normalize_semanticscholar(raw):
    out = []
    for p in json.loads(raw).get("data", []):
        ext = p.get("externalIds") or {}
        pid = p.get("paperId")
        doi = ext.get("DOI")
        url = (f"https://www.semanticscholar.org/paper/{pid}" if pid
               else (f"https://doi.org/{doi}" if doi else ""))
        out.append(_record(
            "semanticscholar", p.get("title"), [a.get("name", "") for a in p.get("authors", [])],
            p.get("year"), p.get("venue", ""), url, doi, p.get("abstract"), src_id=pid))
    return out


NORMALIZERS = {
    "arxiv": normalize_arxiv,
    "pubmed": normalize_pubmed,
    "crossref": normalize_crossref,
    "openalex": normalize_openalex,
    "semanticscholar": normalize_semanticscholar,
}
