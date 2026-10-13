#!/usr/bin/env python3
"""Write the "Request-header matching" section of MOCKSTAR-COVERAGE.md.

For each mock entry, list the request header names it matches on, spelled the
way the mock file spells them. A consumer reads this to see whether header
casing is exercised: HTTP header names are case-insensitive on the wire, so a
mock that only lists `authorization` never proves that a client which sends
`Authorization` still matches.

Sources of header names, per entry:
  match.headers            the route predicate
  scenarios[].when.headers a scenario predicate (shown as scenario:<id>)

Usage: header_coverage.py <mocks-root>      (the dir that holds <tenant>/ subdirs)
Exit codes: 0 ok, 2 bad input. Stdlib only; reads the mock files and writes only to stdout.
"""
import json
import os
import sys

SECTION = "## Request-header matching"


def _entries(doc):
    if isinstance(doc, dict) and isinstance(doc.get("mocks"), list):
        return [e for e in doc["mocks"] if isinstance(e, dict)]
    if isinstance(doc, list):
        return [e for e in doc if isinstance(e, dict)]
    if isinstance(doc, dict) and "match" in doc:
        return [doc]
    return []


def _names(block):
    return list(block.keys()) if isinstance(block, dict) else []


def collect(root):
    """Return rows: dicts with tenant, file, id, method, path, source, names."""
    rows = []
    for tenant in sorted(os.listdir(root)):
        tdir = os.path.join(root, tenant)
        if not os.path.isdir(tdir):
            continue
        for fname in sorted(os.listdir(tdir)):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(tdir, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    doc = json.load(f)
            except (OSError, ValueError) as exc:
                raise ValueError(f"{fpath}: {exc}")
            for entry in _entries(doc):
                match = entry.get("match") if isinstance(entry.get("match"), dict) else {}
                base = {
                    "tenant": tenant,
                    "file": f"mocks/{tenant}/{fname}",
                    "id": str(entry.get("id", "")),
                    "method": str(match.get("method", "")),
                    "path": str(match.get("path", "")),
                }
                rows.append(dict(base, source="match", names=_names(match.get("headers"))))
                for sc in entry.get("scenarios") or []:
                    if not isinstance(sc, dict):
                        continue
                    when = sc.get("when") if isinstance(sc.get("when"), dict) else {}
                    names = _names(when.get("headers"))
                    if names:
                        rows.append(dict(base, source=f"scenario:{sc.get('id', '?')}",
                                         names=names))
    return rows


def casing(name):
    return "lowercase" if name == name.lower() else "mixed"


def render(rows):
    lines = [SECTION, "",
             "Header names are shown as written in the mock file. Names are case-insensitive "
             "on the wire. A column value of `mixed` means the file spells the name with capitals.",
             "",
             "| Method | Path | Mock file | Predicate | Header names (as written) | Casing |",
             "|---|---|---|---|---|---|"]
    mixed = 0
    matched = 0
    for r in rows:
        if r["source"] == "match" and not r["names"]:
            continue
        matched += 1
        cs = sorted({casing(n) for n in r["names"]})
        mixed += sum(1 for n in r["names"] if casing(n) == "mixed")
        names = ", ".join(f"`{n}`" for n in r["names"])
        lines.append(f"| {r['method']} | {r['path']} | {r['file']} | {r['source']} | "
                     f"{names} | {'/'.join(cs)} |")
    if matched == 0:
        lines.append("| - | - | - | - | none: no mock matches on a request header | - |")
    lines += ["", f"- Mocks matching on a header: {matched}",
              f"- Header names spelled with capitals: {mixed}",
              "- Casing exercised: " + ("yes, at least one mixed-case name" if mixed else
                                        "no, every matched name is lowercase")]
    return "\n".join(lines) + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or not os.path.isdir(argv[0]):
        print("usage: header_coverage.py <mocks-root>", file=sys.stderr)
        return 2
    try:
        rows = collect(argv[0])
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
