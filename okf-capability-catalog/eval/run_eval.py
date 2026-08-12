#!/usr/bin/env python3
"""Gate-runnable outcome eval for okf-capability-catalog (docs/eval-standard.md).

Harness: builds a synthetic three-team organisation in a tempdir — a catalog
bundle plus three service repositories, one of which carries a platform-parity
split (a service on EKS in staging and ECS in production, with a capability the
provider has tested and no consumer has). It then drives the
real CLI end to end: init → annotate → declare → ack → confirm → tested →
verify → signal → resolve.

Grader: model-free checks over the CLI's output protocol and the markdown it
wrote, one per acceptance test in the skill's spec — platform parity surfacing
on two independent rules, point-of-no-return arithmetic, automatic tripping,
two-hop trip propagation, derived (never stored) readiness, migration
stability, and stub-team unsatisfiability.

Negative fixtures (mandatory, and the point of the design): the scanner must
refuse to assert consumer_verified, a provider must be refused when verifying
its own capability, a consumer must be refused when writing a promised date, a
staging test run must not raise production readiness, a contract test must
raise nothing, a human must not be able to emit a liveness signal, and a
verification committed by the providing team must fail the commit-boundary
check.

Offline, deterministic, stdlib-only; writes only under tempfile.mkdtemp().
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
CLI = os.path.join(SKILL, "assets", "okf_catalog.py")
sys.path.insert(0, os.path.join(SKILL, "assets"))

import catalog_core as core  # noqa: E402

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = f" ({detail})" if detail else ""
    print(f"CHECK: {name} — {'PASS' if ok else 'FAIL'}{suffix}")


def run(*argv):
    r = subprocess.run([sys.executable, CLI, *argv], capture_output=True, text=True, timeout=55)
    return r.returncode, r.stdout + r.stderr


def write(root, rel, content):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path) or root, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def own_state(cat, capability, env, today):
    """The capability's OWN readiness in one environment, as distinct from the
    effective readiness (which is the minimum over its hard closure)."""
    _, text = run("readiness", cat, "--capability", capability, "--environment", env,
                  "--today", today)
    for line in text.splitlines():
        if line.startswith(f"ENV {env}:"):
            for token in line.split():
                if token.startswith("own="):
                    return token[4:]
    return "?"


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


PAYMENTS_TEAM = """team_id: payments
title: Payments
description: Owns money movement, refunds, and payment instrument lifecycle.
lead: alice
channel: "#team-payments"
"""

PAYMENTS_CAPS = """service:
  id: payment-orchestrator
  title: Payment Orchestrator
  description: Orchestrates payment and refund state machines.
  repository: https://github.example/acme/payment-orchestrator
capabilities:
  - id: initiate-refund
    title: Initiate Refund
    description: Reverses a settled payment and emits a refund lifecycle event.
    inputs:
      - name: payment_id
        description: Identifier of the settled payment
        required: true
    outputs:
      - name: refund_id
        description: Identifier of the created refund
    constraints:
      - Idempotent on payment_id + client_reference
    requires:
      - capability: /teams/ledger/capabilities/post-entry.md
        criticality: hard
        note: Refund reversal must post a ledger entry before acknowledging
runtimes:
  - environment: staging
    platform: EKS
    identifier: cluster-a/ns-payments
  - environment: production
    platform: ECS
    identifier: cluster-prod/payments-svc
    note: EKS migration in progress
interfaces:
  produces:
    - kind: kafka_topic
      name: payments.refund.v1
    - kind: http
      name: POST /internal/refunds
"""

CHECKOUT_TEAM = """team_id: checkout
title: Checkout
lead: bob
channel: "#team-checkout"
"""

CHECKOUT_CAPS = """service:
  id: checkout-web
  title: Checkout Web
capabilities:
  - id: order-detail
    title: Order Detail
    description: Order detail screen including the refund button.
interfaces:
  consumes:
    - kind: kafka_topic
      name: payments.refund.v1
      consumer_group: checkout-web
"""

CHECKOUT_CONSUMER = """consumes:
  - capability: payments/initiate-refund
    environments: [staging, production]
    criticality: hard
    verification:
      suite: tests/integration/refund_e2e.py
      kind: deployment
"""

LEDGER_TEAM = """team_id: ledger
title: Ledger
lead: carol
channel: "#team-ledger"
"""

LEDGER_CAPS = """service:
  id: ledger-core
  title: Ledger Core
capabilities:
  - id: post-entry
    title: Post Entry
    description: Posts a double-entry journal line.
"""


def build(tmp):
    """Harness: the catalog plus three service repos."""
    cat = os.path.join(tmp, "catalog")
    run("init", cat, "--org", "acme", "--platform-team", "platform",
        "--now", "2026-08-01T09:00:00Z")
    write(tmp, "payments/.okf/team.yaml", PAYMENTS_TEAM)
    write(tmp, "payments/.okf/capabilities.yaml", PAYMENTS_CAPS)
    write(tmp, "checkout/.okf/team.yaml", CHECKOUT_TEAM)
    write(tmp, "checkout/.okf/capabilities.yaml", CHECKOUT_CAPS)
    write(tmp, "checkout/.okf/consumer.yml", CHECKOUT_CONSUMER)
    write(tmp, "ledger/.okf/team.yaml", LEDGER_TEAM)
    write(tmp, "ledger/.okf/capabilities.yaml", LEDGER_CAPS)
    return cat


def scaffolding(tmp, cat):
    code, text = run("init", os.path.join(tmp, "fresh"), "--org", "acme",
                     "--now", "2026-08-01T09:00:00Z")
    fresh = os.path.join(tmp, "fresh")
    teams_dir = os.path.join(fresh, "teams")
    stray = [n for n in os.listdir(teams_dir) if n != "index.md"] if os.path.isdir(teams_dir) else []
    check("init scaffolds an empty catalog and fabricates no teams",
          code == 0 and "INIT_RESULT: OK" in text and stray == []
          and os.path.isfile(os.path.join(fresh, "catalog.config.yaml"))
          and os.path.isfile(os.path.join(fresh, "README.md")),
          f"stray team dirs: {stray}")

    # Config portability: a trunk-based org with different environments.
    trunk = os.path.join(tmp, "trunk")
    run("init", trunk, "--org", "trunkco", "--integration", "main", "--release", "main",
        "--environments", "test,prod", "--live-signal-env", "prod",
        "--now", "2026-08-01T09:00:00Z")
    write(tmp, "trunkrepo/.okf/team.yaml", "team_id: solo\ntitle: Solo\n")
    write(tmp, "trunkrepo/.okf/capabilities.yaml",
          "service:\n  id: solo-svc\ncapabilities:\n  - id: do-thing\n    title: Do Thing\n")
    code, text = run("annotate", trunk, "--repo", os.path.join(tmp, "trunkrepo"),
                     "--branch", "main", "--attest-upstream", "yes",
                     "--now", "2026-08-01T09:00:00Z")
    loaded = core.Catalog.load(trunk)
    cap = loaded.capability_by_id("solo/do-thing")
    check("config portability: a trunk-based org with no develop branch works",
          code == 0 and cap is not None
          and (cap.get("code_maturity") or {}).get("state") == "release"
          and loaded.env_names() == ["test", "prod"],
          "annotate failed on integration=main" if cap is None else "")


def scan(tmp, cat):
    payments = os.path.join(tmp, "payments")

    # Feature-branch honesty: a proposal is not a capability.
    code, text = run("annotate", cat, "--repo", payments, "--branch", "feature/refunds",
                     "--now", "2026-08-02T09:00:00Z")
    check("a capability that exists only on a feature branch is not asserted",
          code == 0 and "SCAN_RESULT: PROPOSAL_ONLY" in text
          and "PROPOSAL: payments/initiate-refund" in text
          and not os.path.exists(os.path.join(cat, "teams/payments")),
          "the scan wrote documents from a feature branch")

    code, first = run("annotate", cat, "--repo", payments, "--branch", "develop",
                      "--attest-upstream", "yes", "--by", "alice",
                      "--now", "2026-08-02T09:00:00Z")
    loaded = core.Catalog.load(cat)
    cap = loaded.capability_by_id("payments/initiate-refund")
    svc = loaded.docs.get("teams/payments/services/payment-orchestrator.md")
    check("annotate derives the team, service and capability from the repo",
          code == 0 and cap is not None and svc is not None
          and cap.get("lifecycle") == "proposed"
          and (cap.get("code_maturity") or {}).get("state") == "integration"
          and loaded.team_doc("payments").get("provenance") == "claimed",
          "missing generated documents")

    states = {e.get("state") for e in cap.get("readiness") or []}
    written = read(os.path.join(cat, "teams/payments/capabilities/initiate-refund.md"))
    check("the scanner never asserts consumer_verified or production_live",
          states <= {"unknown"} and "consumer_verified" not in written
          and "production_live" not in written
          and "never writes consumer_verified" in first,
          f"scanner wrote readiness states {states}")

    platform_warned = "different platforms per environment" in first
    check("an ECS/EKS split across environments is reported at scan time",
          platform_warned and {r.get("platform") for r in svc.get("runtimes")} == {"EKS", "ECS"})

    before = read(os.path.join(cat, "teams/payments/capabilities/initiate-refund.md"))
    code, again = run("annotate", cat, "--repo", payments, "--branch", "develop",
                      "--attest-upstream", "yes", "--by", "alice",
                      "--now", "2026-09-30T23:00:00Z")
    after = read(os.path.join(cat, "teams/payments/capabilities/initiate-refund.md"))
    check("re-scanning an unchanged repo is a no-op, human fields untouched",
          code == 0 and "(created)" not in again and "(updated)" not in again
          and before == after, "re-scan produced a diff")

    # Consumer-side scan: proposed requires + a detected edge, no invented dates.
    code, text = run("annotate", cat, "--repo", os.path.join(tmp, "checkout"),
                     "--branch", "develop", "--attest-upstream", "yes", "--by", "bob",
                     "--now", "2026-08-03T09:00:00Z")
    loaded = core.Catalog.load(cat)
    detected = [d for d in loaded.by_type("Dependency") if d.get("state") == "detected"]
    order_detail = loaded.capability_by_id("checkout/order-detail")
    check("a consumed cross-team interface produces exactly one detected edge, "
          "with no date or consequence invented",
          code == 0 and len(detected) == 1
          and detected[0].get("requested_date") is None
          and detected[0].get("consequence_if_late") is None
          and detected[0].get("promised_date") is None
          and core.norm_link(detected[0].get("capability")).endswith("initiate-refund.md"),
          f"{len(detected)} detected edge(s)")
    check("the scan proposes the upstream on the consuming capability",
          order_detail is not None
          and any("initiate-refund" in core.norm_link(r.get("capability"))
                  for r in order_detail.get("requires") or []))


def contract(tmp, cat):
    # A consumer may not write the provider's commitment.
    code, text = run("declare", cat, "--consumer", "checkout", "--capability",
                     "payments/initiate-refund", "--environment", "production",
                     "--requested-date", "2026-08-25", "--consequence", "UI dark",
                     "--fallback", "flag", "--fallback-days", "3",
                     "--promised-date", "2026-09-01")
    check("declare refuses to write promised_date rather than silently dropping it",
          code == 2 and "DECLARE_RESULT: REFUSED" in text and "provider" in text)

    code, text = run("declare", cat, "--consumer", "checkout", "--capability",
                     "payments/initiate-refund", "--environment", "production",
                     "--requested-date", "2026-08-25", "--consequence", "TBD",
                     "--fallback", "flag", "--fallback-days", "3")
    check("declare refuses a blank/'TBD' consequence — a blank field is an accepted risk",
          code == 2 and "DECLARE_RESULT: REFUSED" in text)

    code, text = run("declare", cat, "--consumer", "checkout", "--capability",
                     "payments/initiate-refund", "--environment", "production",
                     "--requested-date", "2026-08-25",
                     "--consequence", "Refund UI ships dark; ops runs ~40 manual refunds a week.",
                     "--fallback", "Ship the UI behind a flag, default off",
                     "--fallback-days", "3", "--by", "bob", "--now", "2026-08-04T09:00:00Z")
    loaded = core.Catalog.load(cat)
    deps = [d for d in loaded.by_type("Dependency")]
    check("declare promotes the detected edge to proposed and shows the closure first",
          code == 0 and "DECLARE_RESULT: PROPOSED" in text and len(deps) == 1
          and deps[0].get("state") == "proposed" and "CLOSURE:" in text
          and "depth=unknown" in text, f"{len(deps)} edge(s)")
    dep_id = deps[0].get("dependency_id")

    code, text = run("ack", cat, "--dependency", dep_id, "--team", "checkout",
                     "--promised-date", "2026-09-01", "--by", "bob")
    check("only the providing team may acknowledge an edge",
          code == 2 and "ACK_RESULT: REFUSED" in text)

    code, text = run("ack", cat, "--dependency", dep_id, "--team", "payments",
                     "--promised-date", "2026-09-01", "--by", "alice",
                     "--now", "2026-08-05T09:00:00Z")
    check("ack stops on a platform mismatch between the target environment and where "
          "it was tested",
          code == 2 and "production runs on ECS" in text and "ACK_RESULT: REFUSED" in text)

    code, text = run("ack", cat, "--dependency", dep_id, "--team", "payments",
                     "--promised-date", "2026-09-01", "--by", "alice", "--runtime-checked",
                     "--note", "Checked the ECS task definition in production.",
                     "--now", "2026-08-05T09:00:00Z")
    check("a promise later than the ask holds the edge open until the consumer re-confirms",
          code == 0 and "ACK_RESULT: PENDING_CONSUMER" in text
          and "later than the requested" in text)

    check("point of no return is computed backwards from the deadline",
          "PONR: 2026-08-29 = promised 2026-09-01 − 3 day(s)" in text,
          "expected promised_date − fallback.execution_days")

    code, text = run("confirm", cat, "--dependency", dep_id, "--team", "checkout",
                     "--by", "bob", "--now", "2026-08-05T10:00:00Z")
    loaded = core.Catalog.load(cat)
    dep = loaded.dependencies[dep_id]
    ack = dep.get("acknowledgement") or {}
    check("an edge reaches acknowledged only with both sides' signatures",
          code == 0 and dep.get("state") == "acknowledged"
          and ack.get("provider", {}).get("by") == "alice"
          and ack.get("consumer", {}).get("by") == "bob")

    # The hard upstream (ledger/post-entry) is not in the catalog at all yet, so
    # this is a commitment made into fog and must be loud about it.
    _, text = run("audit", cat, "--today", "2026-08-06")
    check("a commitment whose hard closure is unmapped is reported as made into fog",
          "CC-FOG" in text, "an acknowledged edge with depth 'unknown' must be loud")
    return dep_id


def readiness(tmp, cat, dep_id):
    run("tested", cat, "--team", "payments", "--capability", "payments/initiate-refund",
        "--environment", "production", "--evidence", "https://ci.example/build/8842",
        "--by", "alice", "--now", "2026-08-06T09:00:00Z")

    code, text = run("verify", cat, "--team", "payments", "--capability",
                     "payments/initiate-refund", "--environment", "production",
                     "--result", "verified", "--kind", "manual", "--evidence", "https://e",
                     "--by", "alice")
    check("a provider cannot verify its own capability",
          code == 2 and "VERIFY_RESULT: REFUSED" in text and "self-certification" in text)

    code, text = run("verify", cat, "--team", "checkout", "--capability",
                     "payments/initiate-refund", "--environment", "production",
                     "--result", "verified", "--kind", "deployment", "--ran-in", "staging",
                     "--resolved-from", "ci://checkout-web/run/9931", "--evidence", "https://e",
                     "--by", "bob")
    check("a staging run may not be filed as production readiness",
          code == 2 and "VERIFY_RESULT: REFUSED" in text)

    code, text = run("verify", cat, "--team", "checkout", "--capability",
                     "payments/initiate-refund", "--environment", "production",
                     "--result", "verified", "--kind", "contract", "--evidence", "https://e",
                     "--by", "bob", "--now", "2026-08-07T09:00:00Z")
    prod = own_state(cat, "payments/initiate-refund", "production", "2026-08-08")
    check("a passing contract test raises readiness for no environment",
          code == 0 and prod == "provider_tested",
          f"production readiness after a contract test: {prod}")

    code, text = run("verify", cat, "--team", "checkout", "--capability",
                     "payments/initiate-refund", "--environment", "staging",
                     "--result", "verified", "--kind", "deployment", "--ran-in", "staging",
                     "--resolved-from", "ci://checkout-web/run/9931",
                     "--evidence", "https://jira.example/CHK-4412",
                     "--scope", "Full refund path end to end from the order detail screen.",
                     "--by", "bob", "--now", "2026-08-08T09:00:00Z")
    staging = own_state(cat, "payments/initiate-refund", "staging", "2026-08-09")
    prod = own_state(cat, "payments/initiate-refund", "production", "2026-08-09")
    check("a deployment run raises readiness for the environment it ran against, and no other",
          code == 0 and staging == "consumer_verified" and prod == "provider_tested",
          f"staging={staging} production={prod}")

    cap_text = read(os.path.join(cat, "teams/payments/capabilities/initiate-refund.md"))
    check("consumer_verified never appears in the provider-owned capability file",
          "consumer_verified" not in cap_text
          and os.path.isfile(os.path.join(
              cat, "teams/checkout/verifications/payments-initiate-refund-staging.md")))

    # Readiness is derived: delete the verification, the state drops back.
    ver = os.path.join(cat, "teams/checkout/verifications/payments-initiate-refund-staging.md")
    kept = read(ver)
    os.remove(ver)
    dropped = own_state(cat, "payments/initiate-refund", "staging", "2026-08-09")
    write(cat, "teams/checkout/verifications/payments-initiate-refund-staging.md", kept)
    restored = own_state(cat, "payments/initiate-refund", "staging", "2026-08-09")
    check("readiness is derived, not stored: deleting the verification drops it back",
          dropped == "unknown" and restored == "consumer_verified",
          f"after delete: {dropped}")

    # Depth: the hard upstream is not in the catalog yet, so nothing is green.
    _, text = run("readiness", cat, "--capability", "payments/initiate-refund",
                  "--environment", "staging", "--today", "2026-08-09")
    check("an unmapped hard upstream makes the answer 'unknown', never green",
          "depth=unknown" in text and "verdict=unknown" in text and "MISSING" in text)


def stub_and_propagation(tmp, cat):
    code, text = run("declare", cat, "--consumer", "payments", "--capability",
                     "ledger/post-entry", "--environment", "production",
                     "--requested-date", "2026-08-18",
                     "--consequence", "Refund reversal cannot post; refunds stop.",
                     "--fallback", "Finance posts journal entries by hand",
                     "--fallback-days", "1", "--by", "alice", "--now", "2026-08-09T09:00:00Z")
    loaded = core.Catalog.load(cat)
    ledger = loaded.team_doc("ledger")
    dep = [d for d in loaded.by_type("Dependency")
           if core.norm_link(d.get("capability")).endswith("post-entry.md")][0]
    ledger_dep = dep.get("dependency_id")
    check("declaring against an unknown provider creates a stub team and says it is blocked",
          code == 0 and ledger is not None and ledger.get("provenance") == "stub"
          and ledger.meta.get("contacts") is None and "BLOCKED:" in text)

    code, text = run("ack", cat, "--dependency", ledger_dep, "--team", "ledger",
                     "--promised-date", "2026-08-18", "--by", "carol")
    check("a stub provider can never acknowledge — the edge stays unsatisfiable",
          code == 2 and "ACK_RESULT: REFUSED" in text and "stub" in text)

    inbound = core.Catalog.load(cat).dependencies[ledger_dep].get("capability")
    code, text = run("annotate", cat, "--repo", os.path.join(tmp, "ledger"),
                     "--branch", "develop", "--attest-upstream", "yes", "--by", "carol",
                     "--now", "2026-08-10T09:00:00Z")
    loaded = core.Catalog.load(cat)
    check("the real team's first scan promotes the stub and preserves inbound links",
          code == 0 and "PROMOTED:" in text
          and loaded.team_doc("ledger").get("provenance") == "claimed"
          and loaded.dependencies[ledger_dep].get("capability") == inbound
          and loaded.resolve_capability("ledger/post-entry") is not None)

    code, text = run("ack", cat, "--dependency", ledger_dep, "--team", "ledger",
                     "--promised-date", "2026-08-18", "--by", "carol",
                     "--now", "2026-08-10T10:00:00Z")
    run("confirm", cat, "--dependency", ledger_dep, "--team", "payments", "--by", "alice",
        "--now", "2026-08-10T11:00:00Z")
    loaded = core.Catalog.load(cat)
    check("the promoted team can now acknowledge",
          code == 0 and loaded.dependencies[ledger_dep].get("state") == "acknowledged")
    return ledger_dep


def findings(tmp, cat, dep_id, ledger_dep):
    # Well before the promised date of 2026-09-01.
    code, text = run("audit", cat, "--today", "2026-08-14")
    codes = {line.split()[1] for line in text.splitlines() if line.startswith("FINDING:")}
    check("a provider-tested-only capability on a split platform surfaces early, "
          "on independent rules",
          "CC-PROVIDER-ONLY" in codes and "CC-PLATFORM-DRIFT" in codes
          and len(codes & {"CC-PROVIDER-ONLY", "CC-PLATFORM-DRIFT", "CC-NOT-VERIFIED"}) >= 2,
          f"codes: {sorted(codes)}")

    check("mapping the upstream clears the fog finding once the closure resolves",
          "CC-FOG" not in codes, f"codes: {sorted(codes)}")

    # 2026-08-20 is past the ledger edge's PONR (2026-08-18 − 1 day) and before
    # the checkout edge's own (2026-08-29): the trip must propagate on its own.
    code, text = run("audit", cat, "--today", "2026-08-20")
    lines = [l for l in text.splitlines() if l.startswith("FINDING:")]
    tripped = [l for l in lines if "CC-TRIPPED" in l]
    risk = [l for l in lines if "CC-PROPAGATED-RISK" in l and dep_id in l]
    check("an edge trips by itself when its point of no return passes unconfirmed",
          bool(tripped) and ledger_dep in tripped[0] and lines[0].startswith("FINDING: CC-TRIPPED"),
          "tripped edges must head the report as unresolved decisions")
    check("the trip propagates downstream two hops with no human relaying it",
          bool(risk) and ledger_dep in risk[0], f"risk lines: {risk}")

    code, text = run("resolve", cat, "--dependency", ledger_dep, "--decision", "satisfied",
                     "--by", "carol", "--today", "2026-08-20")
    check("a tripped edge cannot be closed as satisfied without the consumer's verification",
          code == 2 and "RESOLVE_RESULT: REFUSED" in text)

    code, text = run("resolve", cat, "--dependency", ledger_dep, "--decision",
                     "fallback_invoked", "--by", "carol",
                     "--note", "Finance posts entries by hand until the API lands.",
                     "--now", "2026-08-20T09:00:00Z")
    _, after = run("audit", cat, "--today", "2026-08-20")
    check("recording the forced decision clears the tripped finding",
          code == 0 and "CC-TRIPPED" not in after)

    # review answers "what is", including for a team with no findings.
    code, text = run("review", cat, "--team", "checkout", "--today", "2026-08-14")
    check("review renders inbound, outbound, the directory and unknown-as-unknown",
          code == 0 and "## Inbound" in text and "## Outbound" in text
          and "Capability directory" in text and "closure coverage" in text
          and "verdict 'unknown'" in text and "unknown" in text)

    code, text = run("review", cat, "--view", "capability", "--capability",
                     "payments/initiate-refund", "--today", "2026-08-14")
    check("capability detail shows consumers, closure and verification history",
          code == 0 and "checkout" in text and "hard closure" in text
          and "verifications" in text)


def machine_liveness(tmp, cat):
    code, text = run("signal", cat, "--capability", "payments/initiate-refund",
                     "--environment", "production", "--observation", "traffic",
                     "--emitted-by", "alice")
    check("a human cannot emit a liveness signal",
          code == 2 and "SIGNAL_RESULT: REFUSED" in text)

    code, text = run("signal", cat, "--capability", "payments/initiate-refund",
                     "--environment", "production", "--observation", "traffic",
                     "--emitted-by", "ci://payment-orchestrator/deploy/1182",
                     "--detail", "412 refunds initiated", "--now", "2026-08-21T00:05:00Z")
    _, prod = run("readiness", cat, "--capability", "payments/initiate-refund",
                  "--environment", "production", "--today", "2026-08-21")
    check("production_live comes from a machine signal, and only from there",
          code == 0 and "production_live" in prod)

    # Hand-typed liveness in the provider's own file is a policy violation and
    # must not raise readiness.
    path = os.path.join(cat, "teams/ledger/capabilities/post-entry.md")
    meta, body, _ = core.parse_frontmatter(read(path))
    meta["readiness"] = [{"environment": "production", "state": "production_live",
                          "method": "human", "asserted_by": "teams/ledger"}]
    write(cat, "teams/ledger/capabilities/post-entry.md", core.render_doc(meta, body))
    _, text = run("audit", cat, "--today", "2026-08-21")
    _, ready = run("readiness", cat, "--capability", "ledger/post-entry",
                   "--environment", "production", "--today", "2026-08-21")
    check("hand-typed production_live is a reported violation and is not honoured",
          "CC-HUMAN-LIVENESS" in text and "READINESS_RESULT: provider_tested" in ready)


def failed_verification(tmp, cat):
    before = own_state(cat, "payments/initiate-refund", "staging", "2026-08-22")
    code, text = run("verify", cat, "--team", "checkout", "--capability",
                     "payments/initiate-refund", "--environment", "staging",
                     "--result", "failed", "--kind", "deployment", "--ran-in", "staging",
                     "--resolved-from", "ci://checkout-web/run/9999",
                     "--evidence", "https://jira.example/CHK-4500",
                     "--scope", "Refund event never landed on our consumer.",
                     "--by", "bob", "--now", "2026-08-22T09:00:00Z")
    staging = own_state(cat, "payments/initiate-refund", "staging", "2026-08-22")
    _, findings_text = run("audit", cat, "--today", "2026-08-22")
    # The consuming team's word for an environment is one document: recording a
    # failure replaces its own earlier green, so no stale tick outlives it.
    check("a failed verification is recordable, lowers readiness and is surfaced",
          code == 0 and before == "consumer_verified" and staging == "unknown"
          and "CC-FAILED-VERIFICATION" in findings_text,
          f"staging readiness {before} → {staging} after a failed verification")


def migration_stability(tmp, cat):
    """ECS→EKS: the Service document changes, the Capability contract does not."""
    cap_path = os.path.join(cat, "teams/payments/capabilities/initiate-refund.md")
    svc_path = os.path.join(cat, "teams/payments/services/payment-orchestrator.md")
    cap_before, svc_before = read(cap_path), read(svc_path)
    write(tmp, "payments/.okf/capabilities.yaml",
          PAYMENTS_CAPS.replace("    platform: ECS", "    platform: EKS")
          .replace("    identifier: cluster-prod/payments-svc",
                   "    identifier: cluster-prod/ns-payments"))
    code, text = run("annotate", cat, "--repo", os.path.join(tmp, "payments"),
                     "--branch", "develop", "--attest-upstream", "yes", "--by", "alice",
                     "--now", "2026-08-23T09:00:00Z")
    check("a runtime migration changes the Service document and not the Capability contract",
          code == 0 and read(svc_path) != svc_before and read(cap_path) == cap_before
          and "EKS" in read(svc_path), "the capability contract moved with the runtime")

    _, text = run("audit", cat, "--today", "2026-08-23")
    check("once both environments run the same platform the drift finding clears",
          "CC-PLATFORM-DRIFT" not in text)


def commit_boundary(tmp, cat):
    """§8.1: the mode guards protect the honest path; the commit is the enforcement."""
    ver_meta = {
        "type": "Verification", "title": "checkout verified refunds",
        "capability": "/teams/payments/capabilities/initiate-refund.md",
        "verifying_team": "/teams/checkout/team.md", "environment": "production",
        "result": "verified", "kind": "deployment",
        "environment_resolved_from": "ci://checkout-web/run/1",
        "evidence": "https://e", "verified_by": "bob",
        "verified_at": "2026-08-24T09:00:00Z",
    }
    ver_text = core.render_doc(ver_meta)
    cap_text = read(os.path.join(cat, "teams/payments/capabilities/initiate-refund.md"))

    good = {"commits": [{"sha": "aaa", "author_handle": "bob", "author_team": "checkout",
                         "committed_at": "2026-08-24T10:00:00Z",
                         "files": [{"path": "teams/checkout/verifications/v.md",
                                    "content": ver_text}]}]}
    bad = {"commits": [{"sha": "bbb", "author_handle": "alice", "author_team": "payments",
                        "committed_at": "2026-08-24T10:00:00Z",
                        "files": [{"path": "teams/checkout/verifications/v.md",
                                   "content": ver_text},
                                  {"path": "teams/payments/capabilities/initiate-refund.md",
                                   "content": cap_text}]}]}
    backdated = {"commits": [{"sha": "ccc", "author_handle": "carol",
                              "author_team": "checkout",
                              "committed_at": "2026-09-30T10:00:00Z",
                              "files": [{"path": "teams/checkout/verifications/v.md",
                                         "content": ver_text}]}]}
    import json
    for name, payload in (("good", good), ("bad", bad), ("backdated", backdated)):
        write(tmp, f"changes-{name}.json", json.dumps(payload))

    code, text = run("enforce", cat, "--changes", os.path.join(tmp, "changes-good.json"))
    check("an honest consumer-authored verification passes the commit check",
          code == 0 and "ENFORCE_RESULT: PASS" in text)

    code, text = run("enforce", cat, "--changes", os.path.join(tmp, "changes-bad.json"))
    check("a verification committed by the providing team fails, naming both parties",
          code == 1 and "EN-FOREIGN-VERIFICATION" in text and "EN-SELF-VERIFICATION" in text
          and "payments" in text and "initiate-refund" in text)

    code, text = run("enforce", cat, "--changes", os.path.join(tmp, "changes-backdated.json"))
    check("a backdated or foreign-authored verification is reported",
          code == 1 and "EN-BACKDATED" in text and "EN-AUTHOR-MISMATCH" in text)


def cycles_and_conformance(tmp, cat):
    """A cycle in hard requires must be a finding, not a hang; and the bundle
    must stay OKF-conformant throughout."""
    for team_id, slug, other in (("payments", "initiate-refund", "ledger/post-entry"),
                                 ("ledger", "post-entry", "payments/initiate-refund")):
        path = os.path.join(cat, f"teams/{team_id}/capabilities/{slug}.md")
        meta, body, _ = core.parse_frontmatter(read(path))
        team_of, slug_of = other.split("/")
        meta["requires"] = [{"capability": f"/teams/{team_of}/capabilities/{slug_of}.md",
                             "criticality": "hard"}]
        write(cat, f"teams/{team_id}/capabilities/{slug}.md", core.render_doc(meta, body))
    code, text = run("audit", cat, "--today", "2026-08-25")
    check("mutually-requiring capabilities produce a finding, not a hang",
          code == 0 and "CC-CYCLE" in text)
    code, ready = run("readiness", cat, "--capability", "payments/initiate-refund",
                      "--today", "2026-08-25")
    check("readiness stays answerable inside a cycle",
          code == 0 and "READINESS_RESULT:" in ready and "depth=unknown" in ready)

    code, text = run("validate", cat, "--today", "2026-08-25")
    check("the bundle the tool produced is OKF-conformant, with policy gaps as warnings",
          code == 0 and "VALIDATE_RESULT: PASS" in text,
          [l for l in text.splitlines() if l.startswith("HARD:")][:2])

    # Negative fixture: a concept without a type is a hard conformance failure.
    write(cat, "teams/payments/capabilities/broken.md", "# no frontmatter here\n")
    code, text = run("validate", cat, "--today", "2026-08-25")
    check("a concept document with no type fails validation hard",
          code == 1 and "VALIDATE_RESULT: FAIL" in text and "broken.md" in text)
    os.remove(os.path.join(cat, "teams/payments/capabilities/broken.md"))


def main():
    tmp = tempfile.mkdtemp(prefix="okfcc-eval-")
    try:
        cat = build(tmp)
        scaffolding(tmp, cat)
        scan(tmp, cat)
        dep_id = contract(tmp, cat)
        readiness(tmp, cat, dep_id)
        ledger_dep = stub_and_propagation(tmp, cat)
        findings(tmp, cat, dep_id, ledger_dep)
        machine_liveness(tmp, cat)
        failed_verification(tmp, cat)
        migration_stability(tmp, cat)
        commit_boundary(tmp, cat)
        cycles_and_conformance(tmp, cat)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print(f"EVAL_RESULT: {'PASS' if ok else 'FAIL'} ({k}/{n} checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
