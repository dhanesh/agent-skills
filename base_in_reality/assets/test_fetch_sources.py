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
        self.assertTrue(u.startswith("https://export.arxiv.org/api/query?"))
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


ARXIV_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2101.00001v1</id>
    <published>2021-01-01T00:00:00Z</published>
    <title>A Merkle Tree Result</title>
    <summary>We study authenticated data structures.</summary>
    <author><name>Ada Lovelace</name></author>
    <author><name>Alan Turing</name></author>
    <arxiv:doi>10.1000/xyz</arxiv:doi>
  </entry>
</feed>"""

CROSSREF_JSON = b"""{"message":{"items":[{
  "title":["Reducing Balance APR"],
  "author":[{"given":"Jane","family":"Doe"}],
  "issued":{"date-parts":[[2019,5,1]]},
  "container-title":["J. Finance"],
  "DOI":"10.5555/abc","URL":"https://doi.org/10.5555/abc",
  "abstract":"On interest computation."}]}}"""

OPENALEX_JSON = b"""{"results":[{
  "title":"Raft Consensus","publication_year":2014,
  "authorships":[{"author":{"display_name":"Diego Ongaro"}}],
  "primary_location":{"source":{"display_name":"USENIX ATC"}},
  "doi":"https://doi.org/10.1/raft","id":"https://openalex.org/W1",
  "abstract_inverted_index":{"Raft":[0],"is":[1],"understandable":[2]}}]}"""

PUBMED_JSON = b"""{"result":{"uids":["111"],"111":{
  "title":"Lactate in Sepsis","source":"Crit Care",
  "fulljournalname":"Critical Care","pubdate":"2018 Mar",
  "authors":[{"name":"Smith J"}],
  "articleids":[{"idtype":"doi","value":"10.9/sep"}]}}}"""

S2_JSON = b"""{"data":[{
  "paperId":"deadbeef","title":"bcrypt analysis","year":2016,
  "venue":"USENIX Security","externalIds":{"DOI":"10.7/bcrypt"},
  "authors":[{"name":"Niels Provos"}],"abstract":"Adaptive hashing."}]}"""


class Normalizers(unittest.TestCase):
    def test_arxiv(self):
        recs = fs.normalize_arxiv(ARXIV_XML)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["source"], "arxiv")
        self.assertEqual(r["title"], "A Merkle Tree Result")
        self.assertEqual(r["authors"], ["Ada Lovelace", "Alan Turing"])
        self.assertEqual(r["year"], 2021)
        self.assertEqual(r["doi"], "10.1000/xyz")
        self.assertEqual(r["url"], "http://arxiv.org/abs/2101.00001v1")

    def test_crossref(self):
        r = fs.normalize_crossref(CROSSREF_JSON)[0]
        self.assertEqual(r["title"], "Reducing Balance APR")
        self.assertEqual(r["authors"], ["Jane Doe"])
        self.assertEqual(r["year"], 2019)
        self.assertEqual(r["doi"], "10.5555/abc")

    def test_openalex(self):
        r = fs.normalize_openalex(OPENALEX_JSON)[0]
        self.assertEqual(r["title"], "Raft Consensus")
        self.assertEqual(r["year"], 2014)
        self.assertEqual(r["venue"], "USENIX ATC")
        self.assertEqual(r["abstract"], "Raft is understandable")

    def test_pubmed(self):
        r = fs.normalize_pubmed(PUBMED_JSON)[0]
        self.assertEqual(r["title"], "Lactate in Sepsis")
        self.assertEqual(r["year"], 2018)
        self.assertEqual(r["venue"], "Critical Care")
        self.assertEqual(r["doi"], "10.9/sep")
        self.assertEqual(r["url"], "https://pubmed.ncbi.nlm.nih.gov/111/")

    def test_semanticscholar(self):
        r = fs.normalize_semanticscholar(S2_JSON)[0]
        self.assertEqual(r["title"], "bcrypt analysis")
        self.assertEqual(r["year"], 2016)
        self.assertEqual(r["doi"], "10.7/bcrypt")
        self.assertEqual(r["url"], "https://www.semanticscholar.org/paper/deadbeef")

    def test_normalizers_registry(self):
        self.assertEqual(set(fs.NORMALIZERS), set(fs.SOURCES))


class FetchSource(unittest.TestCase):
    def test_crossref_uses_fetcher(self):
        calls = []

        def fake(url, timeout=20):
            calls.append(url)
            return CROSSREF_JSON

        recs = fs.fetch_source("crossref", "apr", 2, fetcher=fake)
        self.assertEqual(recs[0]["title"], "Reducing Balance APR")
        self.assertIn("api.crossref.org", calls[0])

    def test_pubmed_two_step(self):
        seq = [b'{"esearchresult":{"idlist":["111"]}}', PUBMED_JSON]

        def fake(url, timeout=20):
            return seq.pop(0)

        recs = fs.fetch_source("pubmed", "sepsis", 1, sleep=0, fetcher=fake)
        self.assertEqual(recs[0]["url"], "https://pubmed.ncbi.nlm.nih.gov/111/")

    def test_main_help_exits_zero(self):
        with self.assertRaises(SystemExit) as cm:
            fs.main(["--help"])
        self.assertEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
