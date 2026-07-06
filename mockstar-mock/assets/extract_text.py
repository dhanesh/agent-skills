# /// script
# requires-python = ">=3.9"
# dependencies = ["pypdf>=4", "python-docx>=1"]
# ///
"""Extract plain text from .txt/.md (passthrough), .pdf (pypdf), .docx (python-docx).

Usage: uv run extract_text.py <path>
Exit codes: 0 ok | 2 usage | 3 unsupported/missing-converter | 4 read error
"""
import os
import sys


def _passthrough(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _pdf(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        sys.stderr.write("pdf support needs pypdf; run via `uv run` or `pip install pypdf`\n")
        sys.exit(3)
    reader = PdfReader(path)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _docx(path):
    try:
        import docx
    except ImportError:
        sys.stderr.write("docx support needs python-docx; run via `uv run` or `pip install python-docx`\n")
        sys.exit(3)
    d = docx.Document(path)
    return "\n".join(p.text for p in d.paragraphs)


HANDLERS = {".txt": _passthrough, ".md": _passthrough, ".markdown": _passthrough,
            ".pdf": _pdf, ".docx": _docx}


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: extract_text.py <path>\n")
        return 2
    path = argv[1]
    ext = os.path.splitext(path)[1].lower()
    handler = HANDLERS.get(ext)
    if handler is None:
        sys.stderr.write(f"unsupported extension '{ext}' (supported: {', '.join(sorted(HANDLERS))})\n")
        return 3
    try:
        sys.stdout.write(handler(path))
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — surface any read/parse failure clearly
        sys.stderr.write(f"failed to read {path}: {e}\n")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
