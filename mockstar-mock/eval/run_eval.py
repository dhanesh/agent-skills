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

  * webhook signing hints (webhookHints[].signing, mockstar >= 0.3.0) are graded
    against the wire-format contract mockstar enforces at config-load, so a
    generated signing block cannot fail the Stage-5 boot.

Negative fixtures (mandatory): an inventory missing required fields, one with
an invalid method, a provenance-less endpoint entry, an out-of-enum confidence,
and — for signing — an inline secret, a signedPayload that covers no body, a
signatureTemplate carrying no digest, a `{{ }}` template-engine mix-up, and an
unknown placeholder must all be rejected. Tempdir-only writes; deterministic.
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

# ── Webhook signing contract (mockstar >= 0.3.0) ────────────────────────────
# These vocabularies mirror mockstar's exported constants in
# src/features/webhooks/scheme.ts. They are the wire-format contract, not a
# preference: a block that breaks one of these is rejected by mockstar's Zod
# schema at config-load, which is the Stage-5 boot the skill runs to prove the
# generated project works. Grading them here catches it one stage earlier.
SIGNED_PAYLOAD_PLACEHOLDERS = ("body", "timestamp", "timestampSeconds")
SIGNATURE_TEMPLATE_PLACEHOLDERS = ("signature", "algorithm", "timestamp", "timestampSeconds")

# Detection is deliberately wider than substitution: it matches ANY {...} span so a
# near-miss like "{ body }" or a typo like "{time_stamp}" is flagged rather than
# silently signed as literal text.
_PLACEHOLDER_SPAN_RE = re.compile(r"\{[^{}]*\}")


def _unknown_placeholders(template, allowed):
    """Names in `template` that are not in `allowed`, de-duplicated, first-seen order."""
    seen, out = set(), []
    for match in _PLACEHOLDER_SPAN_RE.finditer(template):
        name = match.group(0)[1:-1]
        # An empty `{}` span is not a placeholder — it is a legitimate empty object
        # inside a JSON-envelope payload (e.g. '{"meta":{},"b":{body}}').
        if name == "" or name in allowed or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _has_unbalanced_placeholder(template):
    """A leftover `{` followed by a letter — an unterminated placeholder.

    A JSON envelope's outer brace is not this shape: stripping the spans from
    '{"t":{timestamp},"b":{body}}' leaves '{"t":,"b":}', whose `{` precedes '"'.
    """
    return bool(re.search(r"\{[A-Za-z]", _PLACEHOLDER_SPAN_RE.sub("", template)))


def _validate_signing(sig, sig_schema, where):
    """Grade one webhookHints[].signing block. Returns a list of error strings."""
    errors = []
    if not isinstance(sig, dict):
        return [f"{where}: signing must be an object"]

    props = sig_schema["properties"]
    for key in sig:
        if key not in props:
            errors.append(f"{where}.signing: unknown field {key!r}")

    provider_enum = props["provider"]["enum"]
    if "provider" in sig and sig["provider"] not in provider_enum:
        errors.append(f"{where}.signing: provider {sig['provider']!r} not in {provider_enum}")

    enc_enum = props["digestEncoding"]["enum"]
    if "digestEncoding" in sig and sig["digestEncoding"] not in enc_enum:
        errors.append(
            f"{where}.signing: digestEncoding {sig['digestEncoding']!r} not in {enc_enum}")

    # S3: secret references only. An inline secret is rejected by mockstar at
    # config-load, and would also leak a credential into a generated artifact.
    secret_pat = props["secretRef"]["pattern"]
    if "secretRef" in sig:
        ref = sig["secretRef"]
        if not isinstance(ref, str) or not re.match(secret_pat, ref):
            errors.append(
                f"{where}.signing: secretRef {ref!r} is not `{{{{ env.NAME }}}}` or `file:/path` "
                "— inline secrets are rejected")

    checks = (
        ("signedPayload", SIGNED_PAYLOAD_PLACEHOLDERS, "{body}",
         "signature covers no request content"),
        ("signatureTemplate", SIGNATURE_TEMPLATE_PLACEHOLDERS, "{signature}",
         "signature header carries no digest"),
    )
    for field, allowed, required_tok, why in checks:
        if field not in sig:
            continue
        tpl = sig[field]
        if not isinstance(tpl, str) or not tpl:
            errors.append(f"{where}.signing: {field} must be a non-empty string")
            continue
        # `{{` is the request-template engine's delimiter and `${` is JS template-literal
        # syntax; neither is signing-placeholder syntax. Both render literally and produce
        # a signature that authenticates nothing recognisable.
        wrong_syntax = False
        if "{{" in tpl:
            errors.append(f"{where}.signing: {field} uses {{{{ }}}} request-template syntax, "
                          "not single-brace signing placeholders")
            wrong_syntax = True
        if "${" in tpl:
            errors.append(f"{where}.signing: {field} uses ${{ }} JS template-literal syntax, "
                          "not single-brace signing placeholders")
            wrong_syntax = True
        if wrong_syntax:
            continue
        bad = _unknown_placeholders(tpl, allowed)
        if bad:
            errors.append(f"{where}.signing: {field} has unknown placeholder(s) "
                          f"{bad} — allowed: {list(allowed)}")
        if required_tok not in tpl:
            errors.append(f"{where}.signing: {field} must contain {required_tok} — "
                          f"otherwise the {why}")
        if _has_unbalanced_placeholder(tpl):
            errors.append(f"{where}.signing: {field} has an unmatched {{ that looks like "
                          "an unterminated placeholder")
    return errors



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
    signing_schema = (item_schema["properties"]["webhookHints"]["items"]
                      ["properties"]["signing"])

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

        hints = ep.get("webhookHints")
        if isinstance(hints, list):
            for j, hint in enumerate(hints):
                if isinstance(hint, dict) and "signing" in hint:
                    errors.extend(_validate_signing(
                        hint["signing"], signing_schema, f"{where}.webhookHints[{j}]"))
        elif "webhookHints" in ep:
            errors.append(f"{where}: webhookHints must be an array")
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

        # ── Webhook signing contract (mockstar >= 0.3.0) ─────────────────
        def with_signing(sig):
            return {"endpoints": [endpoint(
                method="POST", path="/orders",
                webhookHints=[{"url": "https://partner.example.com/hooks",
                               "event": "order.created", "signing": sig}])]}

        # The Stripe row of the provider cookbook, emitted verbatim.
        stripe = {
            "provider": "stripe",
            "enabled": True,
            "secretRef": "{{ env.PARTNER_HOOK_SECRET }}",
            "signedPayload": "{timestampSeconds}.{body}",
            "signatureTemplate": "t={timestampSeconds},v1={signature}",
            "digestEncoding": "hex",
            "signatureHeader": "stripe-signature",
            "timestampHeader": None,
        }
        errs = validate_inventory(with_signing(stripe), schema)
        check("provider-fidelity signing hint (stripe cookbook row) validates",
              errs == [], "; ".join(errs)[:100])

        errs = validate_inventory(with_signing(
            {"secretRef": "{{ env.HOOK_SECRET }}", "enabled": True}), schema)
        check("minimal signing hint (enabled + secretRef) validates on defaults",
              errs == [], "; ".join(errs)[:100])

        # Every cookbook row must survive the same grader the generator's output faces.
        cookbook = {
            "github": ("{body}", "{algorithm}={signature}", "hex"),
            "slack": ("v0:{timestampSeconds}:{body}", "v0={signature}", "hex"),
            "shopify": ("{body}", "{signature}", "base64"),
            "razorpay": ("{body}", "{signature}", "hex"),
            "mockstar": ("{timestamp}.{body}", "{algorithm}={signature}", "hex"),
        }
        bad_rows = [name for name, (pay, tpl, enc) in cookbook.items()
                    if validate_inventory(with_signing(
                        {"provider": name, "enabled": True,
                         "secretRef": "file:/run/secrets/hook",
                         "signedPayload": pay, "signatureTemplate": tpl,
                         "digestEncoding": enc}), schema) != []]
        check("every provider cookbook row validates against the signing contract",
              bad_rows == [], f"failing: {bad_rows}")

        # Negative fixtures — each is a shape mockstar rejects at config-load.
        errs = validate_inventory(with_signing(
            {"enabled": True, "secretRef": "inline-literal-not-a-ref"}), schema)
        check("inline secretRef is rejected (no secret ever inlined into a mock)",
              any("secretRef" in e for e in errs), "; ".join(errs)[:80])

        errs = validate_inventory(with_signing(
            {"secretRef": "{{ env.H }}", "signedPayload": "{timestamp}"}), schema)
        check("signedPayload without {body} is rejected (signature covers nothing)",
              any("{body}" in e for e in errs), "; ".join(errs)[:80])

        errs = validate_inventory(with_signing(
            {"secretRef": "{{ env.H }}", "signatureTemplate": "sha256="}), schema)
        check("signatureTemplate without {signature} is rejected (header has no digest)",
              any("{signature}" in e for e in errs), "; ".join(errs)[:80])

        errs = validate_inventory(with_signing(
            {"secretRef": "{{ env.H }}", "signedPayload": "{{ body }}"}), schema)
        check("{{ }} request-template syntax in a signing template is rejected",
              any("request-template" in e for e in errs), "; ".join(errs)[:80])

        errs = validate_inventory(with_signing(
            {"secretRef": "{{ env.H }}", "signedPayload": "${timestamp}.${body}"}), schema)
        check("${ } JS template-literal syntax in a signing template is rejected",
              any("template-literal" in e for e in errs), "; ".join(errs)[:80])

        errs = validate_inventory(with_signing(
            {"secretRef": "{{ env.H }}", "signedPayload": "{payload}.{body}"}), schema)
        check("unknown signing placeholder {payload} is rejected",
              any("payload" in e for e in errs), "; ".join(errs)[:80])

        errs = validate_inventory(with_signing(
            {"secretRef": "{{ env.H }}", "signedPayload": "{timestamp.{body}"}), schema)
        check("unterminated placeholder in signedPayload is rejected",
              any("unmatched" in e for e in errs), "; ".join(errs)[:80])

        errs = validate_inventory(with_signing(
            {"secretRef": "{{ env.H }}", "provider": "twilio"}), schema)
        check("provider outside the cookbook enum is rejected",
              any("provider" in e for e in errs), "; ".join(errs)[:80])

        errs = validate_inventory(with_signing(
            {"secretRef": "{{ env.H }}", "digestEncoding": "base32"}), schema)
        check("digestEncoding outside hex/base64 is rejected",
              any("digestEncoding" in e for e in errs), "; ".join(errs)[:80])

        errs = validate_inventory(with_signing(
            {"secretRef": "{{ env.H }}", "algorithm": "sha512"}), schema)
        check("unknown signing field is rejected (schema is closed)",
              any("algorithm" in e for e in errs), "; ".join(errs)[:80])

        # A JSON-envelope payload is legitimate and must NOT trip the brace heuristics.
        errs = validate_inventory(with_signing(
            {"secretRef": "{{ env.H }}", "signedPayload": '{"t":{timestamp},"b":{body}}',
             "signatureTemplate": "{signature}"}), schema)
        check("JSON-envelope signedPayload is accepted (brace heuristics not over-eager)",
              errs == [], "; ".join(errs)[:100])

        # The generator expands a named `provider` from the cookbook table in
        # mockstar-mapping.md. A provider in the IR enum with no cookbook row is a
        # hint the generator can accept but cannot translate.
        mapping_doc = os.path.join(SKILL, "references", "mockstar-mapping.md")
        with open(mapping_doc, encoding="utf-8") as f:
            mapping_text = f.read()
        signing_props = (schema["properties"]["endpoints"]["items"]["properties"]
                         ["webhookHints"]["items"]["properties"]["signing"]["properties"])
        undocumented = [name for name in signing_props["provider"]["enum"]
                        if name != "custom" and f"| `{name}` |" not in mapping_text]
        check("every IR signing provider has a cookbook row in mockstar-mapping.md",
              undocumented == [], f"undocumented: {undocumented}")

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
