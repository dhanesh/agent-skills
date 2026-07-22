#!/usr/bin/env python3
"""Gate-runnable outcome eval for mockstar-mock (docs/eval-standard.md).

Grades the skill's deterministic normalization layer offline — no bunx, no
mockstar CLI, no Docker, no network:

  * assets/extract_text.py converts a fixture prose doc (Stage 1 intake) and
    rejects unsupported/unreadable inputs with its documented exit codes;
  * fixture Endpoint Inventories are graded against the shipped
    references/inventory.schema.json contract (Stage 2's output schema) with
    a model-free stdlib validator driven by the schema file itself;
  * the shipped smoke fixture (assets/fixtures/routes.tsv) conforms to the
    METHOD<TAB>/path<TAB>STATUS protocol Stage 5 feeds smoke.sh.

Negative fixtures (mandatory): an inventory missing required fields, one with
an invalid method, a provenance-less endpoint entry, and an out-of-enum
confidence must all be rejected. Tempdir-only writes; deterministic.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
ASSETS = os.path.join(SKILL, "assets")
SCHEMA_PATH = os.path.join(SKILL, "references", "inventory.schema.json")

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = f" ({detail})" if detail else ""
    print(f"CHECK: {name} — {'PASS' if ok else 'FAIL'}{suffix}")


# ── Model-free inventory grader, driven by the shipped schema file ───────────

def validate_inventory(doc, schema):
    """Validate an Endpoint Inventory against references/inventory.schema.json.

    Minimal stdlib interpreter for the subset of JSON Schema the shipped
    schema uses (required, enum, minItems, integer bounds, pattern). Enum and
    required vocabularies are read from the schema, never hardcoded.
    """
    errors = []
    if not isinstance(doc, dict):
        return ["inventory: expected an object"]
    for key in schema.get("required", []):
        if key not in doc:
            errors.append(f"inventory: missing required key '{key}'")

    tenant_pat = schema["properties"]["tenant"].get("pattern")
    if "tenant" in doc and tenant_pat and not re.match(tenant_pat, str(doc["tenant"])):
        errors.append(f"inventory: tenant {doc['tenant']!r} fails pattern {tenant_pat}")

    item_schema = schema["properties"]["endpoints"]["items"]
    item_required = item_schema["required"]
    method_enum = item_schema["properties"]["method"]["enum"]
    confidence_enum = item_schema["properties"]["confidence"]["enum"]
    prov_required = item_schema["properties"]["provenance"]["required"]
    resp_schema = item_schema["properties"]["responses"]
    resp_min = resp_schema.get("minItems", 0)
    resp_required = resp_schema["items"]["required"]
    status_min = resp_schema["items"]["properties"]["status"]["minimum"]
    status_max = resp_schema["items"]["properties"]["status"]["maximum"]

    endpoints = doc.get("endpoints")
    if not isinstance(endpoints, list):
        if "endpoints" in doc:
            errors.append("inventory: endpoints must be an array")
        return errors

    for i, ep in enumerate(endpoints):
        where = f"endpoints[{i}]"
        if not isinstance(ep, dict):
            errors.append(f"{where}: not an object")
            continue
        for key in item_required:
            if key not in ep:
                errors.append(f"{where}: missing required field '{key}'")
        if "method" in ep and ep["method"] not in method_enum:
            errors.append(f"{where}: method {ep['method']!r} not in {method_enum}")
        if "confidence" in ep and ep["confidence"] not in confidence_enum:
            errors.append(f"{where}: confidence {ep['confidence']!r} not in {confidence_enum}")
        prov = ep.get("provenance")
        if prov is not None:
            if not isinstance(prov, dict):
                errors.append(f"{where}: provenance must be an object")
            else:
                for key in prov_required:
                    if key not in prov:
                        errors.append(f"{where}.provenance: missing required field '{key}'")
        resps = ep.get("responses")
        if isinstance(resps, list):
            if len(resps) < resp_min:
                errors.append(f"{where}: responses needs >= {resp_min} item(s)")
            for j, r in enumerate(resps):
                rwhere = f"{where}.responses[{j}]"
                if not isinstance(r, dict):
                    errors.append(f"{rwhere}: not an object")
                    continue
                for key in resp_required:
                    if key not in r:
                        errors.append(f"{rwhere}: missing required field '{key}'")
                st = r.get("status")
                if isinstance(st, int) and not (status_min <= st <= status_max):
                    errors.append(f"{rwhere}: status {st} outside {status_min}-{status_max}")
        elif "responses" in ep:
            errors.append(f"{where}: responses must be an array")
    return errors


def endpoint(**over):
    ep = {
        "method": "GET",
        "path": "/pets/:id",
        "responses": [{"status": 200, "body": {"id": "pet_1", "name": "Rex"}}],
        "provenance": {"source": "petstore-mini.yaml", "locator": "#/paths/~1pets~1{id}/get"},
        "confidence": "grounded",
    }
    ep.update(over)
    return ep


def main():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = json.load(f)
    extract = os.path.join(ASSETS, "extract_text.py")

    tmp = tempfile.mkdtemp(prefix="mockstar-eval-")
    try:
        # ── Stage-1 surface: extract_text on a fixture prose doc ─────────
        doc_text = ("# Pets API\n\n"
                    "`GET /pets/:id` returns a pet by id.\n\n"
                    "| Method | Path | Status |\n|---|---|---|\n| GET | /pets/:id | 200 |\n")
        doc_path = os.path.join(tmp, "apidoc.md")
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write(doc_text)
        r = subprocess.run([sys.executable, extract, doc_path],
                           capture_output=True, text=True, timeout=30)
        check("extract_text converts a fixture Markdown doc (exit 0, content intact)",
              r.returncode == 0 and r.stdout == doc_text,
              f"rc={r.returncode}")
        r2 = subprocess.run([sys.executable, extract, doc_path],
                            capture_output=True, text=True, timeout=30)
        check("extract_text output is deterministic across runs", r.stdout == r2.stdout)

        yaml_fixture = os.path.join(ASSETS, "fixtures", "petstore-mini.yaml")
        r = subprocess.run([sys.executable, extract, yaml_fixture],
                           capture_output=True, text=True, timeout=30)
        check("extract_text rejects an unsupported extension with exit 3 (documented)",
              r.returncode == 3 and "unsupported extension" in r.stderr,
              f"rc={r.returncode}")
        r = subprocess.run([sys.executable, extract, os.path.join(tmp, "missing.md")],
                           capture_output=True, text=True, timeout=30)
        check("extract_text reports an unreadable input with exit 4 (documented)",
              r.returncode == 4, f"rc={r.returncode}")

        # ── Stage-2 surface: Endpoint Inventory contract ─────────────────
        good = {
            "tenant": "default",
            "endpoints": [
                endpoint(),
                endpoint(method="POST", path="/pets",
                         responses=[{"status": 201, "body": {"id": "pet_2"}},
                                    {"status": 400, "body": {"error": "bad input"},
                                     "when": {"body": {"name": ""}}}],
                         provenance={"source": "apidoc.md", "locator": "## Create pet"},
                         confidence="inferred"),
            ],
        }
        errs = validate_inventory(good, schema)
        check("schema-conforming fixture inventory validates (grounded + inferred)",
              errs == [], "; ".join(errs)[:100])

        bad = {"endpoints": [endpoint()]}
        del bad["endpoints"][0]["responses"]
        errs = validate_inventory(bad, schema)
        check("inventory missing a required field (responses) is rejected",
              any("'responses'" in e for e in errs), "; ".join(errs)[:80])

        errs = validate_inventory({"endpoints": [endpoint(method="FETCH")]}, schema)
        check("inventory with invalid method FETCH is rejected",
              any("method" in e for e in errs), "; ".join(errs)[:80])

        no_prov = endpoint()
        del no_prov["provenance"]
        errs = validate_inventory({"endpoints": [no_prov]}, schema)
        check("provenance-less endpoint entry is rejected (no fabricated endpoints)",
              any("'provenance'" in e for e in errs), "; ".join(errs)[:80])

        half_prov = endpoint(provenance={"source": "apidoc.md"})
        errs = validate_inventory({"endpoints": [half_prov]}, schema)
        check("provenance without a locator is rejected",
              any("locator" in e for e in errs), "; ".join(errs)[:80])

        errs = validate_inventory({"endpoints": [endpoint(confidence="guessed")]}, schema)
        check("confidence outside grounded/inferred is rejected",
              any("confidence" in e for e in errs), "; ".join(errs)[:80])

        errs = validate_inventory({"endpoints": [endpoint(responses=[])]}, schema)
        check("empty responses array violates minItems and is rejected",
              any("responses" in e for e in errs), "; ".join(errs)[:80])

        errs = validate_inventory(
            {"endpoints": [endpoint(responses=[{"status": 999, "body": {}}])]}, schema)
        check("out-of-range response status is rejected",
              any("999" in e for e in errs), "; ".join(errs)[:80])

        # ── Stage-5 surface: shipped routes fixture obeys the TSV protocol ─
        with open(os.path.join(ASSETS, "fixtures", "routes.tsv"), encoding="utf-8") as f:
            rows = [ln for ln in f.read().splitlines() if ln.strip()]
        methods = set(schema["properties"]["endpoints"]["items"]["properties"]["method"]["enum"])
        ok_rows = all(
            len(parts) == 3 and parts[0] in methods
            and parts[1].startswith("/") and parts[2].isdigit()
            for parts in (row.split("\t") for row in rows))
        check("shipped routes.tsv fixture conforms to METHOD/path/status protocol",
              bool(rows) and ok_rows, f"{len(rows)} row(s)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print(f"EVAL_RESULT: {'PASS' if ok else 'FAIL'} ({k}/{n} checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
