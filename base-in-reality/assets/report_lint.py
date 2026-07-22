# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""report_lint.py — deterministic linter for base-in-reality findings.

Enforces the documented report contract (assets/findings.schema.json +
references/verdict-rubric.md) with stdlib only, so a findings list can be
gate-checked offline before it is synthesized into the report:

  * every finding carries the schema's required fields;
  * layer / verdict / severity values come from the schema's enums;
  * every citation carries the schema's required citation fields
    (title, url, fetched);
  * GROUNDING INVARIANT: a VIOLATION or DEVIATION must hold >= 1 citation
    with fetched == true — a "verified" claim with no fetched source recorded
    is the fabricated-citation pattern and is rejected (report it as
    UNCONFIRMED instead);
  * OUTDATED requires >= 1 citation (the rubric's "cited newer standard");
  * a finding marked downgraded: true must carry verdict UNCONFIRMED.

Enum vocabularies are read from the shipped findings.schema.json, never
hardcoded, so schema and linter cannot drift apart.

Usage:   python3 report_lint.py <findings.json>
Input:   a JSON array of finding objects, or {"findings": [...]}.
Output:  "ERROR: finding[i]: <msg>" lines; final "LINT_RESULT: PASS|FAIL".
Exit:    0 iff no errors | 1 lint errors | 2 usage/unreadable input.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "findings.schema.json")


def load_schema(path=SCHEMA_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _enum(schema, field):
    return list(schema["properties"][field]["enum"])


def lint_findings(findings, schema=None):
    """Return a sorted list of 'finding[i]: message' error strings (empty = clean)."""
    if schema is None:
        schema = load_schema()
    required = list(schema["required"])
    layer_enum = _enum(schema, "layer")
    verdict_enum = _enum(schema, "verdict")
    severity_enum = _enum(schema, "severity")
    cite_required = list(schema["properties"]["citations"]["items"]["required"])

    errors = []
    if not isinstance(findings, list):
        return ["findings: expected a JSON array of finding objects"]

    for i, f in enumerate(findings):
        where = f"finding[{i}]"
        if not isinstance(f, dict):
            errors.append(f"{where}: not an object")
            continue

        for key in required:
            if key not in f:
                errors.append(f"{where}: missing required field '{key}'")

        verdict = f.get("verdict")
        if "verdict" in f and verdict not in verdict_enum:
            errors.append(f"{where}: verdict {verdict!r} not in {verdict_enum}")
        if "severity" in f and f["severity"] not in severity_enum:
            errors.append(f"{where}: severity {f['severity']!r} not in {severity_enum}")
        if "layer" in f and f["layer"] not in layer_enum:
            errors.append(f"{where}: layer {f['layer']!r} not in {layer_enum}")

        citations = f.get("citations", [])
        if not isinstance(citations, list):
            errors.append(f"{where}: citations must be an array")
            citations = []
        fetched_count = 0
        for j, c in enumerate(citations):
            cwhere = f"{where}.citations[{j}]"
            if not isinstance(c, dict):
                errors.append(f"{cwhere}: not an object")
                continue
            for key in cite_required:
                if key not in c:
                    errors.append(f"{cwhere}: missing required field '{key}'")
            if "fetched" in c and not isinstance(c["fetched"], bool):
                errors.append(f"{cwhere}: 'fetched' must be a boolean")
            if c.get("fetched") is True:
                fetched_count += 1

        # Grounding invariant (verdict-rubric.md): no fabricated authority.
        if verdict in ("VIOLATION", "DEVIATION") and fetched_count == 0:
            errors.append(
                f"{where}: verdict {verdict} with no fetched citation — "
                "ungrounded claims must be reported as UNCONFIRMED "
                "(fabricated-citation pattern)")
        if verdict == "OUTDATED" and len(citations) == 0:
            errors.append(
                f"{where}: verdict OUTDATED requires a cited newer "
                "standard/result (>= 1 citation)")

        if f.get("downgraded") is True and verdict != "UNCONFIRMED":
            errors.append(
                f"{where}: downgraded is true but verdict is {verdict!r} — "
                "a refutation downgrade must land on UNCONFIRMED")

    return errors


def lint_document(doc, schema=None):
    """Accept either a findings array or a {'findings': [...]} report object."""
    if isinstance(doc, dict):
        if "findings" not in doc:
            return ["document: object input must carry a 'findings' array"]
        doc = doc["findings"]
    return lint_findings(doc, schema)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        sys.stderr.write("usage: report_lint.py <findings.json>\n")
        return 2
    try:
        with open(argv[0], "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        sys.stderr.write(f"error: cannot read findings JSON: {e}\n")
        return 2
    errors = lint_document(doc)
    for err in errors:
        print(f"ERROR: {err}")
    n = len(doc["findings"] if isinstance(doc, dict) else doc) \
        if isinstance(doc, (list, dict)) else 0
    if errors:
        print(f"LINT_RESULT: FAIL ({len(errors)} error(s) in {n} finding(s))")
        return 1
    print(f"LINT_RESULT: PASS ({n} finding(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
