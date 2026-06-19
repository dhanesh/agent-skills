# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Unit tests for fetch_sources.py — stdlib unittest, run via `uv run`."""
import unittest
import fetch_sources as fs


class BuildQueryURL(unittest.TestCase):
    def test_arxiv(self):
        u = fs.build_query_url("arxiv", "merkle tree", 5)
        self.assertTrue(u.startswith("http://export.arxiv.org/api/query?"))
        self.assertIn("search_query=all%3Amerkle+tree", u)
        self.assertIn("max_results=5", u)

    def test_pubmed(self):
        u = fs.build_query_url("pubmed", "sepsis lactate", 3)
        self.assertIn("eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", u)
        self.assertIn("db=pubmed", u)
        self.assertIn("retmax=3", u)
        self.assertIn("term=sepsis+lactate", u)

    def test_crossref(self):
        u = fs.build_query_url("crossref", "apr reducing balance", 7)
        self.assertTrue(u.startswith("https://api.crossref.org/works?"))
        self.assertIn("rows=7", u)

    def test_openalex(self):
        u = fs.build_query_url("openalex", "raft consensus", 4)
        self.assertTrue(u.startswith("https://api.openalex.org/works?"))
        self.assertIn("per_page=4", u)
        self.assertIn("search=raft+consensus", u)

    def test_semanticscholar(self):
        u = fs.build_query_url("semanticscholar", "bcrypt", 2)
        self.assertIn("api.semanticscholar.org/graph/v1/paper/search", u)
        self.assertIn("limit=2", u)
        self.assertIn("query=bcrypt", u)

    def test_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            fs.build_query_url("scholar", "x", 1)


if __name__ == "__main__":
    unittest.main()
