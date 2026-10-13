# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Offline tests for encode_body.py and header_coverage.py (stdlib unittest)."""
import base64
import gzip
import os
import re
import shutil
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import encode_body  # noqa: E402
import header_coverage  # noqa: E402

BODY = b'{"id":"pet_1","name":"Rex","tags":["a","b"]}' * 3


class EncodeBody(unittest.TestCase):
    def test_gzip_roundtrip_and_header_bytes(self):
        raw = encode_body.compress(BODY, "gzip")
        self.assertEqual(raw[:2], b"\x1f\x8b")
        self.assertEqual(gzip.decompress(raw), BODY)
        self.assertEqual(encode_body.decompress(raw, "gzip"), BODY)

    def test_gzip_is_reproducible(self):
        self.assertEqual(encode_body.compress(BODY, "gzip"), encode_body.compress(BODY, "gzip"))

    def test_deflate_is_zlib_wrapped(self):
        raw = encode_body.compress(BODY, "deflate")
        self.assertEqual(zlib.decompress(raw), BODY)  # zlib format, not raw deflate
        self.assertEqual(encode_body.decompress(raw, "deflate"), BODY)

    def test_br_has_no_stdlib_encoder(self):
        with self.assertRaises(ValueError):
            encode_body.compress(BODY, "br")

    def test_decode_rejects_wrong_encoding(self):
        raw = encode_body.compress(BODY, "gzip")
        with self.assertRaises(Exception):
            encode_body.decompress(raw, "deflate")

    def test_handler_gzip_embeds_compressed_bytes(self):
        src = encode_body.render_handler("petGzip", "gzip", BODY)
        m = re.search(r'BODY_B64 = "([^"]+)"', src)
        self.assertIsNotNone(m)
        self.assertEqual(gzip.decompress(base64.b64decode(m.group(1))), BODY)
        self.assertIn('"content-encoding": "gzip"', src)
        self.assertIn("export async function petGzip", src)

    def test_handler_br_compresses_at_load(self):
        src = encode_body.render_handler("petBr", "br", BODY)
        self.assertIn("brotliCompressSync", src)
        self.assertIn('"content-encoding": "br"', src)
        m = re.search(r'PLAIN_B64 = "([^"]+)"', src)
        self.assertEqual(base64.b64decode(m.group(1)), BODY)

    def test_cli_verify_pass_fail_and_bad_input(self):
        tmp = tempfile.mkdtemp(prefix="encode-body-")
        try:
            plain = os.path.join(tmp, "plain")
            other = os.path.join(tmp, "other")
            raw = os.path.join(tmp, "raw")
            with open(plain, "wb") as f:
                f.write(BODY)
            with open(other, "wb") as f:
                f.write(b"different")
            with open(raw, "wb") as f:
                f.write(encode_body.compress(BODY, "gzip"))
            self.assertEqual(encode_body.main(["verify", "--encoding", "gzip",
                                               "--raw", raw, "--expected", plain]), 0)
            self.assertEqual(encode_body.main(["verify", "--encoding", "gzip",
                                               "--raw", raw, "--expected", other]), 1)
            # A body that is not gzip at all is bad input, not a mismatch.
            self.assertEqual(encode_body.main(["verify", "--encoding", "gzip",
                                               "--raw", plain, "--expected", plain]), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_cli_handler_rejects_bad_name(self):
        self.assertEqual(encode_body.main(["handler", "--name", "bad-name", "--encoding", "gzip",
                                           "--body-file", __file__]), 2)


class HeaderCoverage(unittest.TestCase):
    ROOT = os.path.join(HERE, "fixtures", "header-mocks")

    def _root_with(self, text):
        tmp = tempfile.mkdtemp(prefix="hdr-cov-")
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "default"))
        with open(os.path.join(tmp, "default", "a.json"), "w", encoding="utf-8") as f:
            f.write(text)
        return tmp

    def test_names_are_kept_as_written(self):
        rows = header_coverage.collect(self.ROOT)
        names = {n for r in rows for n in r["names"]}
        self.assertEqual(names, {"Authorization", "x-api-key", "X-Tenant-Id"})

    def test_report_marks_mixed_case_and_scenarios(self):
        out = header_coverage.render(header_coverage.collect(self.ROOT))
        self.assertIn("## Request-header matching", out)
        self.assertIn("`Authorization`", out)
        self.assertIn("scenario:tenant-b", out)
        self.assertIn("Header names spelled with capitals: 2", out)
        self.assertIn("yes, at least one mixed-case name", out)
        self.assertNotIn("/ping", out)  # no header predicate, so no row

    def test_all_lowercase_reports_casing_not_exercised(self):
        root = self._root_with('{"mocks":[{"id":"a","match":{"method":"GET","path":"/a",'
                               '"headers":{"authorization":"x"}},'
                               '"response":{"kind":"static","status":200}}]}')
        out = header_coverage.render(header_coverage.collect(root))
        self.assertIn("no, every matched name is lowercase", out)

    def test_no_header_matches_says_none(self):
        root = self._root_with('{"mocks":[{"id":"a","match":{"method":"GET","path":"/a"},'
                               '"response":{"kind":"static","status":200}}]}')
        out = header_coverage.render(header_coverage.collect(root))
        self.assertIn("none: no mock matches on a request header", out)

    def test_bad_json_is_an_error_not_a_crash(self):
        root = self._root_with("{not json")
        self.assertEqual(header_coverage.main([root]), 2)


if __name__ == "__main__":
    unittest.main()
