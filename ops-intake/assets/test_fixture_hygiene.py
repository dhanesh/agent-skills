"""Fixture hygiene: the ops-intake test and eval sources hold no remote download piped to
a shell and no literal invisible or bidi character.

Hostile fixtures stay harmless and local (`$(touch pwned)`, `x | sh`), and a hidden
character is written as a `\\u....` escape in the Python source, so a scanner that reads
these files sees no download-to-shell line and no invisible character.
"""
import glob, os, re, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)

# A download tool whose output is piped to a shell on the same line.
DOWNLOAD_TO_SHELL = re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b")
# Zero-width, direction marks, bidi embeddings/overrides, bidi isolates and the BOM.
INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")


def sources():
    return sorted(glob.glob(os.path.join(HERE, "test_*.py"))
                  + glob.glob(os.path.join(SKILL, "eval", "*.py")))


def findings(text):
    """Return (line number, kind) for each flagged line in text."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if DOWNLOAD_TO_SHELL.search(line):
            out.append((n, "download-to-shell"))
        if INVISIBLE.search(line):
            out.append((n, "invisible-character"))
    return out


class ScanSelfTest(unittest.TestCase):
    def test_scan_catches_download_to_shell(self):
        # The negative fixtures live in a labelled data file under assets/fixtures/. The
        # source glob does not scan that folder.
        with open(os.path.join(HERE, "fixtures", "download_to_shell_lines.txt"),
                  encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln and not ln.startswith("#")]
        self.assertGreaterEqual(len(lines), 4)
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(findings("ok\n" + line), [(2, "download-to-shell")])

    def test_scan_catches_each_invisible_character(self):
        for cp in list(range(0x200B, 0x2010)) + list(range(0x202A, 0x202F)) \
                + list(range(0x2066, 0x206A)) + [0xFEFF]:
            with self.subTest(cp=hex(cp)):
                self.assertEqual(findings("a" + chr(cp) + "b"), [(1, "invisible-character")])

    def test_scan_passes_local_hostile_fixtures_and_escapes(self):
        for line in ("x; id|sh", "$(touch pwned) | sh", "`x | sh`", '"a\\u202eb"',
                     "curl --version"):
            with self.subTest(line=line):
                self.assertEqual(findings(line), [])


class SourceHygieneTest(unittest.TestCase):
    def test_sources_are_found(self):
        names = [os.path.basename(p) for p in sources()]
        self.assertIn("run_eval.py", names)
        self.assertIn("test_fixture_hygiene.py", names)

    def test_no_download_to_shell_or_invisible_character(self):
        bad = []
        for path in sources():
            with open(path, encoding="utf-8") as f:
                for n, kind in findings(f.read()):
                    bad.append("%s:%d %s" % (os.path.relpath(path, SKILL), n, kind))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
