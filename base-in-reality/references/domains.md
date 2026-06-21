# Domains & Norms Surface

The `base-in-reality` skill auto-detects the domain(s) present in a codebase by scanning for the signals listed below. Domain detection drives which source classes and authority standards are consulted during the audit. Multiple domains may be active simultaneously; the skill merges their norms surfaces.

## Detection signals

| Domain | Detection signals |
|---|---|
| fintech / lending / payments | interest/APR/EMI/KYC code, payment SDKs, ledger/loan schemas, RBI/PCI references |
| ml / data-science | numpy/torch/sklearn, train/eval splits, model/embedding code, notebooks |
| security / crypto | hashing/JWT/TLS/key-management code, auth flows, crypto libraries |
| healthcare | HL7/FHIR, PHI fields, clinical terminology, HIPAA references |
| distributed-systems | service mesh, queues, consensus, retries/timeouts, gRPC/REST fan-out |
| data-engineering | ETL/DAGs, warehouses, schema migrations, streaming |
| web | HTTP frameworks, sessions, CORS, templating, frontend bundles |

## Norms surface per domain

Each domain activates a specific set of authoritative standards against which claims are grounded:

- **fintech / lending / payments** — RBI circulars and master directions, ISO 20022, PCI-DSS, relevant RBI/NPCI API specs, FEMA guidelines for cross-border flows.
- **ml / data-science** — peer-reviewed methodology papers (arXiv, Semantic Scholar), reproducibility standards, benchmark dataset documentation; no single normative body, so primary sources are the original algorithm papers.
- **security / crypto** — NIST SP 800-* series, FIPS 140-*, OWASP Top 10 and ASVS, relevant IETF RFCs (e.g. RFC 8446 for TLS 1.3, RFC 7519 for JWT), CVSS scoring guidelines.
- **healthcare** — HIPAA Security and Privacy Rules, HL7 FHIR R4/R5 specifications, FDA guidance documents, WHO clinical standards.
- **distributed-systems** — IETF RFCs for underlying protocols (HTTP/2, gRPC, QUIC), CNCF project specifications (e.g. OpenTelemetry), academic consensus and fault-tolerance literature.
- **data-engineering** — ANSI SQL standards, vendor-specific warehouse documentation (consulted via WebFetch), Apache project specifications (Spark, Kafka, Airflow).
- **web** — IETF RFCs (HTTP, CORS, CSP), OWASP Top 10 for web, W3C specifications, browser compatibility data.

## Override

Pass `--domain <x>` to bypass auto-detection and force a specific domain. Use this when the codebase signals are ambiguous or when auditing a single module in isolation (e.g. the payments module of a mixed-domain repo).

The detected (or overridden) domain map is surfaced at the top of every report. Readers should sanity-check it before interpreting findings — a wrong domain detection will pull from the wrong norms surface and may produce irrelevant or misleading verdicts.
