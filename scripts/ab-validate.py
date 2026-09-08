#!/usr/bin/env python3
"""Behavioural A/B: does a change make the skills BEHAVE better, or just pass gates?

`make gate` proves the new tree is self-consistent. It cannot prove the new tree is
*better than the old one* — for that you need the old code, the new code, identical
fixtures, and a measured delta. That is this script.

It checks out a BASE git ref into a temporary worktree, runs the same fixtures through
both trees, and classifies every dimension:

  IMPROVED  — a `delta` claim where the measurement actually moved the right way
  HELD      — a `guard` claim: behaviour deliberately UNCHANGED (no regression)
  WORSE     — the new arm is worse, or a claimed improvement did not happen
  UNPROVEN  — a `delta` claim whose numbers did NOT move (the claim is unearned)

The UNPROVEN class exists on purpose: a guard that holds must never be able to
masquerade as a win, and a "fix" that changes no measurement is not a fix.

Usage:
    python3 scripts/ab-validate.py [BASE_REF]     # default: the ref in BASE_DEFAULT
    make ab-validate BASE=<ref>

stdlib only, offline, deterministic. Each arm runs in its own subprocess so two
versions of the same module name can never collide in one interpreter. Requires git
and a non-shallow clone containing BASE_REF; SKIPs cleanly when that is unavailable.

The corpus below is the behavioural record of the ontology-guardrail change
(docs/ontology-guardrails.md). Extend it when you add a guardrail — a new rule with
no A/B row is a claim nobody measured.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Pre-ontology-guardrail baseline. Override with argv[1] / `make ab-validate BASE=…`.
BASE_DEFAULT = "23dc485"

ROWS = []
# Probes that crashed. A run with any of these cannot classify anything, because
# a guard row like `0 >= 0` or `None == None` is True on a missing key — so a
# double crash used to be reported as a guard holding.
PROBE_ERRORS = []


def _errored(*vals):
    """True if any probe result is an error dict.

    A crashed probe used to sail through as HELD: `probe()` returns
    {"_error": ...}, and guard rows written as `0 >= 0` or `None == None` are
    both True on missing keys. So a broken import in BOTH arms was reported as
    a regression guard holding — the exact "a guard must never masquerade as a
    pass" failure this script exists to prevent.
    """
    return any(isinstance(v, dict) and "_error" in v for v in vals)


def row(skill, dimension, old, new, ok, note="", kind="delta"):
    """kind='delta' claims the new arm is strictly better; kind='guard' claims only
    that behaviour is unchanged. Keeping them distinct is what stops a held guard
    from being counted as an improvement."""
    ROWS.append({"skill": skill, "dimension": dimension, "old": old, "new": new,
                 "ok": ok, "note": note, "kind": kind,
                 "moved": str(old) != str(new)})


def probe(tree, subdir, code):
    """Run `code` with <tree>/<subdir> on sys.path; return its JSON stdout."""
    src = ("import sys, json, os, tempfile\nsys.path.insert(0, %r)\n%s"
           % (os.path.join(tree, subdir), code))
    r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                       text=True, timeout=300)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        err = {"_error": (r.stderr or r.stdout).strip()[-300:]}
        PROBE_ERRORS.append((tree, subdir, err["_error"]))
        return err


# ── world-model-ledger ──────────────────────────────────────────────────────
WM_ADVERSARIAL = r"""
import world_model as W
wm = W.WorldModel(os.path.join(tempfile.mkdtemp(), "m.db"))
wm.upsert_entity("referent", "stripe/refunds-api")
wm.upsert_entity("referent", "postgres-db")
bad = [("hash_pw", "frobnicates", "bcrypt"),           # hallucinated verb
       ("stripe/refunds-api", "imports", "auth/hash.py"),  # referent cannot import
       ("hash_pw", "calls", "postgres-db")]            # cannot call a referent
good = [("auth/hash.py", "uses", "bcrypt"),
        ("db/config.py", "provides", "load_config"),
        ("billing/refund.py", "depends_on", "stripe-sdk")]
nb = ng = 0
for t in bad:
    try: wm.add_interaction(*t); nb += 1
    except Exception: pass
for t in good:
    try: wm.add_interaction(*t); ng += 1
    except Exception: pass
try:
    wm.add_constraint("bogus", "forbids", "m", scope_predicate="frobnicates"); dead = 1
except Exception:
    dead = 0
print(json.dumps({"bad_accepted": nb, "good_accepted": ng, "dead_constraint": dead}))
"""

WM_BUILD = r"""
import world_model as W
wm = W.WorldModel(os.path.join(tempfile.mkdtemp(), "m.db"))
b = wm.build_from_repo(%r, max_files=1200)["build"]
wm.conn.commit()
kinds = {r["kind"]: r["c"] for r in wm.conn.execute(
    "SELECT kind, COUNT(*) c FROM entity GROUP BY kind")}
print(json.dumps({"files": b["files_registered"], "edges": b["edges"],
                  "referents": kinds.get("referent")}))
"""

WM_MARKERS = r"""
import world_model as W, harvest as H
wm = W.WorldModel(os.path.join(tempfile.mkdtemp(), "m.db"))
H.apply_markers(wm, [
    ("assistant", "WM-OBSERVE: hash_pw frobnicates bcrypt @ a.py:1"),
    ("assistant", "WM-OBSERVE: hash_pw uses bcrypt @ auth/hash.py:14"),
    ("assistant", "WM-VALIDATED: hash_pw uses bcrypt by test:tests/t.py::x")])
n = wm.conn.execute("SELECT COUNT(*) c FROM interaction").fetchone()["c"]
v = wm.conn.execute("SELECT COUNT(*) c FROM interaction "
                    "WHERE validation='validated'").fetchone()["c"]
print(json.dumps({"rows": n, "validated": v}))
"""


def check_world_model(old, new):
    s = "world-model-ledger"
    a, b = (probe(t, s + "/assets", WM_ADVERSARIAL) for t in (old, new))
    row(s, "impossible/hallucinated triples ACCEPTED (lower=better)",
        a.get("bad_accepted"), b.get("bad_accepted"),
        b.get("bad_accepted", 9) < a.get("bad_accepted", 0),
        "unknown verb, referent-imports-file, calls-a-referent")
    row(s, "legitimate triples accepted (higher=better)",
        a.get("good_accepted"), b.get("good_accepted"),
        b.get("good_accepted") == a.get("good_accepted") == 3,
        "must stay 3/3", kind="guard")
    row(s, "constraint scoped to an unwritable verb (lower=better)",
        a.get("dead_constraint"), b.get("dead_constraint"),
        b.get("dead_constraint", 9) < a.get("dead_constraint", 0),
        "a rule that can never fire is a latent bug, not a belief")

    a, b = (probe(t, s + "/assets", WM_BUILD % REPO) for t in (old, new))
    row(s, "real-repo seed: files / structural edges",
        f"{a.get('files')}f {a.get('edges')}e", f"{b.get('files')}f {b.get('edges')}e",
        a.get("files") == b.get("files") and a.get("edges") == b.get("edges"),
        "the guardrail must drop NO legitimate build edge", kind="guard")
    row(s, "referent entities on a real repo",
        a.get("referents"), b.get("referents"),
        (b.get("referents") or 0) >= (a.get("referents") or 0),
        "domain/range stub-kind inference", kind="guard")

    a, b = (probe(t, s + "/assets", WM_MARKERS) for t in (old, new))
    row(s, "marker stream (1 hallucinated + 2 valid) -> rows stored",
        a.get("rows"), b.get("rows"),
        b.get("rows", 0) < a.get("rows", 99) and b.get("validated") == 1,
        "bad marker dropped; the validated fact survives")


# ── context-hygiene-kit ─────────────────────────────────────────────────────
CH_CORRUPT = r"""
from pathlib import Path
from context_ledger import ContextLedger
store = Path(tempfile.mkdtemp()) / "ledger.json"
led = ContextLedger()
for i in range(50):
    led.ingest("decision", "decision number %d that must survive" % i)
led.ingest("constraint", "no md5 anywhere")
led.persist(store)
data = json.loads(store.read_text())
data["cards"][0]["kind"] = "risk"              # undeclared kind
data["cards"][1]["source_agent"] = "planner"   # field from a newer schema
store.write_text(json.dumps(data))
try:
    l2 = ContextLedger.load(store); survived, crashed = len(l2.cards), False
except Exception:
    survived, crashed = 0, True
try:   # next turn: does capture RESUME, or is it dead forever?
    l3 = ContextLedger.load(store); l3.ingest("decision", "a turn-2 fact")
    l3.persist(store); resumed = True
except Exception:
    resumed = False
print(json.dumps({"survived": survived, "crashed": crashed, "resumed": resumed}))
"""

CH_CLEAN = r"""
from pathlib import Path
from context_ledger import ContextLedger
store = Path(tempfile.mkdtemp()) / "ledger.json"
led = ContextLedger()
for i in range(40):
    led.ingest("note", "chatter %d filler words here" % i)
led.ingest("decision", "keep bcrypt", pinned=True)
led.ingest("constraint", "no md5 anywhere")
led.persist(store)
l2 = ContextLedger.load(store)
sel = l2.curate(2000, "bcrypt hashing")
print(json.dumps({"cards": len(l2.cards), "hot": sel["hot_tokens"],
                  "kept": len(sel["kept"])}))
"""


def check_hygiene(old, new):
    s = "context-hygiene-kit"
    a, b = (probe(t, s + "/assets", CH_CORRUPT) for t in (old, new))
    row(s, "cards surviving 1 bad card in 51 (higher=better)",
        a.get("survived"), b.get("survived"),
        (b.get("survived") or 0) > (a.get("survived") or 0),
        "old: ANY bad card destroyed the WHOLE ledger")
    row(s, "load crashes on a corrupt card (False=better)",
        a.get("crashed"), b.get("crashed"),
        a.get("crashed") is True and b.get("crashed") is False)
    row(s, "capture resumes next turn (True=better)",
        a.get("resumed"), b.get("resumed"),
        b.get("resumed") is True and not a.get("resumed"),
        "behind the Stop hook's `|| true` a dead load is PERMANENT and silent")

    a, b = (probe(t, s + "/assets", CH_CLEAN) for t in (old, new))
    row(s, "healthy ledger: cards / hot tokens / kept",
        f"{a.get('cards')}c {a.get('hot')}t {a.get('kept')}k",
        f"{b.get('cards')}c {b.get('hot')}t {b.get('kept')}k",
        all(a.get(k) == b.get(k) for k in ("cards", "hot", "kept")),
        "unchanged behaviour on a healthy ledger", kind="guard")


# ── feynman-walkthrough (OKF producer) ──────────────────────────────────────
OKF_BAD = r"""
import okf
d = tempfile.mkdtemp()
bad = [{"title": "no type at all"},
       {"type": "Explainer", "title": "breaks\nthe frontmatter"},
       {"type": "Explainer", "tags": "not-a-list"},
       {"type": "Explainer", "sources": [{"type": "file"}]},
       {"type": "Explainer", "timestamp": "last Tuesday"}]
good = [{"type": "Explainer", "title": "T", "tags": ["a", "b"],
         "timestamp": "2026-07-27T00:00:00Z"},
        {"type": "wildly-bespoke-type", "title": "U"},
        {"type": "Postmortem", "title": "V",
         "sources": [{"type": "external", "locator": "https://x.test",
                      "fingerprint": None}]}]
def n_written(metas, tag):
    k = 0
    for i, m in enumerate(metas):
        p = os.path.join(d, "%s%d.md" % (tag, i))
        try:
            okf.write_concept(p, m, "# Body\n"); k += os.path.isfile(p)
        except Exception: pass
    return k
print(json.dumps({"bad_written": n_written(bad, "b"),
                  "good_written": n_written(good, "g")}))
"""

OKF_PARTIAL = r"""
import okf
p = os.path.join(tempfile.mkdtemp(), "explainer.md")
# `type` IS present, so the pre-existing _read_pinned_sources guard is satisfied;
# only the partial-parse rule can catch this one.
try:
    okf.write_concept(p, {"type": "Explainer", "title": "T",
                          "_okf_parse_error": "unclosed list"}, "# Body\n")
    persisted = os.path.isfile(p)
    text = open(p).read() if persisted else ""
except Exception:
    persisted, text = False, ""
print(json.dumps({"persisted": persisted, "marker_leaked": "_okf_parse_error" in text}))
"""

OKF_ROUNDTRIP = r"""
import okf, datetime as dt
d = tempfile.mkdtemp()
src = os.path.join(d, "src.txt"); open(src, "w").write("hello\n")
root = os.path.join(d, "bundle")
sd = okf.init_okf(root, "Widget Internals", [src], today=dt.date(2026, 1, 1))
meta, _ = okf.read_concept(os.path.join(sd, okf.EXPLAINER))
okf.pin_okf(root, sd, today=dt.date(2026, 1, 2))
after, _ = okf.read_concept(os.path.join(sd, okf.EXPLAINER))
print(json.dumps({"type": meta.get("type"), "pinned": after.get("timestamp")}))
"""

SITE_TOLERANCE = r"""
import okf_site
b = tempfile.mkdtemp()
open(os.path.join(b, "index.md"), "w").write("# B\n\n* [C](/c.md) - c\n")
open(os.path.join(b, "c.md"), "w").write("---\ntitle: no type here\n---\n\n# C\n")
bundle = okf_site.scan_bundle(b)
print(json.dumps({"type_warnings": len([w for w in bundle.warnings if "type" in w]),
                  "concepts": len(bundle.concepts)}))
"""


def check_okf(old, new):
    s = "feynman-walkthrough"
    a, b = (probe(t, s + "/assets", OKF_BAD) for t in (old, new))
    row(s, "spec-violating concepts WRITTEN to disk (lower=better)",
        a.get("bad_written"), b.get("bad_written"),
        b.get("bad_written", 9) < a.get("bad_written", 0),
        "no type, unserialisable value, bad tags, bad pin, bad timestamp")
    row(s, "legitimate concepts written (higher=better)",
        a.get("good_written"), b.get("good_written"),
        b.get("good_written") == a.get("good_written") == 3,
        "incl. a bespoke producer-chosen `type` — OKF keeps that OPEN", kind="guard")

    a, b = (probe(t, s + "/assets", OKF_PARTIAL) for t in (old, new))
    row(s, "half-parsed concept persisted as canonical (lower=better)",
        a.get("persisted"), b.get("persisted"),
        a.get("persisted") is True and b.get("persisted") is False)
    row(s, "parser-internal marker leaks into the bundle (lower=better)",
        a.get("marker_leaked"), b.get("marker_leaked"),
        a.get("marker_leaked") is True and b.get("marker_leaked") is False)

    a, b = (probe(t, s + "/assets", OKF_ROUNDTRIP) for t in (old, new))
    row(s, "init + pin round-trip on a valid bundle",
        f"{a.get('type')}/{a.get('pinned')}", f"{b.get('type')}/{b.get('pinned')}",
        a == b and b.get("type") == "Explainer",
        "normal authoring flow unchanged", kind="guard")

    a, b = (probe(t, "okf-site-kit/assets", SITE_TOLERANCE) for t in (old, new))
    row("okf-site-kit (consumer)", "still TOLERATES a type-less concept",
        f"{a.get('type_warnings')}warn/{a.get('concepts')}rendered",
        f"{b.get('type_warnings')}warn/{b.get('concepts')}rendered",
        a == b and b.get("concepts") == 1,
        "OKF requires CONSUMER tolerance — deliberately unchanged", kind="guard")



# ── mockstar-mock ───────────────────────────────────────────────────────────
# mockstar 0.3.0 made the webhook signature wire format configurable. A signing block
# the skill emits is validated by mockstar's Zod schema at config-load — i.e. the
# Stage-5 boot — so an invalid one costs a full generate+boot cycle to discover.
# These fixtures are exactly the shapes mockstar rejects.
MOCKSTAR_SIGNING = r"""
import run_eval as R
schema = json.load(open(os.path.join(os.path.dirname(R.__file__), "..",
                                     "references", "inventory.schema.json")))

def ep(signing):
    return {"endpoints": [{
        "method": "POST", "path": "/orders",
        "responses": [{"status": 201, "body": {"id": "ord_1"}}],
        "provenance": {"source": "api.md", "locator": "## Create order"},
        "confidence": "grounded",
        "webhookHints": [{"url": "https://x.example/h", "signing": signing}]}]}

BAD = [
    {"enabled": True, "secretRef": "inline-literal-not-a-ref"},          # inline secret
    {"secretRef": "{{ env.H }}", "signedPayload": "{timestamp}"},        # signs no body
    {"secretRef": "{{ env.H }}", "signatureTemplate": "sha256="},        # carries no digest
    {"secretRef": "{{ env.H }}", "signedPayload": "{{ body }}"},         # {{ }} mix-up
    {"secretRef": "{{ env.H }}", "signedPayload": "${timestamp}.${body}"},  # ${ } mix-up
    {"secretRef": "{{ env.H }}", "signedPayload": "{payload}.{body}"},   # unknown placeholder
    {"secretRef": "{{ env.H }}", "signedPayload": "{timestamp.{body}"},  # unterminated
    {"secretRef": "{{ env.H }}", "digestEncoding": "base32"},            # out-of-enum
    {"secretRef": "{{ env.H }}", "provider": "twilio"},                  # unknown provider
]
rejected = sum(1 for b in BAD if R.validate_inventory(ep(b), schema) != [])

# Regression guard: the Stripe cookbook row and a JSON-envelope payload are legitimate
# and must NOT be rejected; a grader that rejects everything is not an improvement.
GOOD = [
    {"provider": "stripe", "enabled": True, "secretRef": "{{ env.PARTNER_HOOK_SECRET }}",
     "signedPayload": "{timestampSeconds}.{body}",
     "signatureTemplate": "t={timestampSeconds},v1={signature}",
     "digestEncoding": "hex", "signatureHeader": "stripe-signature",
     "timestampHeader": None},
    {"secretRef": "file:/run/secrets/hook",
     "signedPayload": '{"t":{timestamp},"b":{body}}',
     "signatureTemplate": "{signature}"},
]
accepted = sum(1 for g in GOOD if R.validate_inventory(ep(g), schema) == [])

# Regression guard: the pre-existing endpoint contract still holds.
no_prov = {"method": "GET", "path": "/x",
           "responses": [{"status": 200, "body": {}}], "confidence": "grounded"}
base_ok = R.validate_inventory({"endpoints": [no_prov]}, schema) != []
print(json.dumps({"rejected": rejected, "accepted": accepted, "base_ok": base_ok}))
"""


def check_mockstar(old, new):
    s = "mockstar-mock"
    a, b = (probe(t, s + "/eval", MOCKSTAR_SIGNING) for t in (old, new))
    row(s, "invalid webhook signing blocks rejected (of 9, higher=better)",
        a.get("rejected"), b.get("rejected"),
        b.get("rejected") == 9 and b.get("rejected", 0) > a.get("rejected", 9),
        "each is a shape mockstar's config-load rejects — caught at IR time, "
        "not after a generate+boot cycle")
    row(s, "legitimate signing blocks still accepted (of 2)",
        a.get("accepted"), b.get("accepted"),
        b.get("accepted") == 2,
        "baseline accepts them by having no rule at all; a grader that rejects "
        "everything would not be a win", kind="guard")
    row(s, "pre-existing endpoint contract (provenance required)",
        a.get("base_ok"), b.get("base_ok"),
        a.get("base_ok") is True and b.get("base_ok") is True,
        "unchanged by the signing work", kind="guard")


# ── crafting-self-prompting-loops (prompt-only; artifact-level) ─────────────
def check_loops(old, new):
    names = ["autonomous", "base-loop", "human-checkpointed", "multi-agent",
             "self-refinement"]

    def measure(tree):
        declared = enforced = 0
        for n in names:
            p = os.path.join(tree, "crafting-self-prompting-loops", "assets",
                             "templates", "%s.template.md" % n)
            t = open(p).read() if os.path.isfile(p) else ""
            declared += "STATE_SCHEMA" in t
            enforced += t.count("STATE_VALIDATION") >= 2   # slot header AND loop body
        return declared, enforced

    do, eo = measure(old)
    dn, en = measure(new)
    s = "crafting-self-prompting-loops"
    row(s, "templates declaring a state schema (of 5)", do, dn, dn > do,
        "ARTIFACT-LEVEL ONLY — prompt-only skill; behavioural effect needs model "
        "runs (see docs/crafting-self-prompting-loops/)")
    row(s, "templates ENFORCING it in the loop body (of 5)", eo, en, en > eo,
        "declared-but-unenforced would not count")


# ── Audit-fix guardrails (added with the 2026-09 whole-repo review) ─────────
# Each of these rules shipped WITH a measurement, per CLAUDE.md: "a new rule
# with no A/B row is a claim nobody measured."

def check_audit_guardrails(old, new):
    import subprocess as sp

    def run(tree, *args):
        # errors="replace": one fixture deliberately contains an undecodable
        # byte, and awk echoes it back on stderr. Strict decoding would crash
        # the harness on the very input the probe exists to measure.
        r = sp.run(["sh", os.path.join(tree, *args[0].split("/")), *args[1:]],
                   capture_output=True, timeout=120)
        dec = lambda b: b.decode("utf-8", "replace")
        return r.returncode, dec(r.stdout) + dec(r.stderr)

    scratch = tempfile.mkdtemp()

    # 1) validate-skill: an over-long FOLDED description must be caught.
    fixture = os.path.join(scratch, "longdesc")
    os.makedirs(os.path.join(fixture, "assets"), exist_ok=True)
    body = "\n".join("  " + "a" * 80 for _ in range(30))
    open(os.path.join(fixture, "SKILL.md"), "w").write(
        "---\nname: longdesc\ndescription: >-\n%s\nlicense: MIT\n"
        "compatibility: none\nmetadata:\n  author: d\n  version: \"1.0.0\"\n"
        "  tags: \"t\"\n---\n# x\n" % body)
    open(os.path.join(fixture, "README.md"), "w").write("# x\n")

    def caught(tree):
        _, out = run(tree, "scripts/gates/validate-skill.sh", fixture)
        return 1 if "description: exceeds 1024" in out else 0

    a, b = caught(old), caught(new)
    row("gates", "over-long folded description caught (1=yes)", a, b, b > a,
        "11 of 18 skills were unmeasured; one shipped 163 chars over the limit")

    # 2) scan-leaks: a secret hidden below an undecodable byte.
    leak = os.path.join(scratch, "leakskill")
    os.makedirs(os.path.join(leak, "assets"), exist_ok=True)
    open(os.path.join(leak, "SKILL.md"), "w").write(
        "---\nname: leakskill\ndescription: d\n---\n# x\n")
    open(os.path.join(leak, "README.md"), "w").write("# x\n")
    with open(os.path.join(leak, "assets", "n.txt"), "wb") as f:
        f.write(b"note: \xff byte\nfiller\nAWS_KEY = AKIAIOSFODNN7EXAMPLE\n")

    def leaks_found(tree):
        rc, out = run(tree, "scripts/gates/scan-leaks.sh", leak)
        return 0 if "SCAN_RESULT: PASS" in out else 1

    a, b = leaks_found(old), leaks_found(new)
    row("gates", "undecodable byte no longer hides a secret (1=caught)", a, b, b > a,
        "the scanner failed open: awk aborted inside a `find | while` pipeline")

    # 3) run-eval: verdict line and exit status must agree.
    ev = os.path.join(scratch, "evalskill")
    os.makedirs(os.path.join(ev, "eval"), exist_ok=True)
    open(os.path.join(ev, "eval", "run_eval.py"), "w").write(
        'print("CHECK: broken")\nprint("EVAL_RESULT: FAIL")\n')

    def eval_rejected(tree):
        rc, _ = run(tree, "scripts/gates/run-eval.sh", ev)
        return 1 if rc != 0 else 0

    a, b = eval_rejected(old), eval_rejected(new)
    row("gates", "eval printing FAIL but exiting 0 is rejected (1=yes)", a, b, b > a,
        "the gate trusted the exit code alone")

    # 4) asset-paths: skill-relative helper invocations in shipped SKILL.md files.
    def relative_invocations(tree):
        n = 0
        for d in sorted(os.listdir(tree)):
            md = os.path.join(tree, d, "SKILL.md")
            if not os.path.isfile(md):
                continue
            rc, _ = run(tree, "scripts/gates/asset-paths.sh", os.path.join(tree, d)) \
                if os.path.isfile(os.path.join(tree, "scripts/gates/asset-paths.sh")) \
                else (None, "")
            if rc is None:
                # Baseline predates the checker: measure with the NEW one.
                rc, _ = run(new, "scripts/gates/asset-paths.sh", os.path.join(tree, d))
            n += 1 if rc != 0 else 0
        return n

    a, b = relative_invocations(old), relative_invocations(new)
    row("gates", "skills invoking helpers by a skill-relative path", a, b, b < a,
        "agents run from the target repo, where `python3 assets/x.py` does not exist")


# ── bug-autopsy (evidence, not just structure) ──────────────────────────────
def check_autopsy(old, new):
    import subprocess as sp
    hollow = ("# Post-mortem: x\n\n## Summary\nA thing broke.\n\n## Impact\nSome.\n\n"
              "## Timeline\n- 2026-08-01T10:00Z - it started\n- 2026-08-01T11:00Z - it stopped\n\n"
              "## Root cause\n- Why did it break? Because of a thing.\n"
              "- Why a thing? Because of another thing.\n- Why? Because of a thing.\n\n"
              "## Contributing factors\n- It was Tuesday.\n\n## Fix\nWe fixed it.\n\n"
              "## Prevention\n- [ ] Do better\n\n## Detection\nWe noticed.\n\n"
              "## Links\n- none\n")
    scratch = tempfile.mkdtemp()
    path = os.path.join(scratch, "hollow.md")
    open(path, "w").write(hollow)

    def rejected(tree):
        lint = os.path.join(tree, "bug-autopsy", "assets", "postmortem_lint.py")
        if not os.path.isfile(lint):
            return 0
        r = sp.run([sys.executable, lint, path], capture_output=True, text=True,
                   timeout=60)
        return 1 if r.returncode != 0 else 0

    a, b = rejected(old), rejected(new)
    row("bug-autopsy", "evidence-free post-mortem rejected (1=yes)", a, b, b > a,
        "the deliverable claimed `evidence-cited`; the linter checked structure only")


# ── base-in-reality (grounding is checkable, not self-declared) ─────────────
def check_grounding(old, new):
    import subprocess as sp
    scratch = tempfile.mkdtemp()
    ev = os.path.join(scratch, "evidence.jsonl")
    open(ev, "w").write(json.dumps({"url": "https://arxiv.org/abs/2401.00001",
                                    "doi": "10.1000/real", "source": "arxiv",
                                    "query": "q"}) + "\n")
    fab = os.path.join(scratch, "fabricated.json")
    json.dump([{"claim": "c", "layer": "algo", "location": "a.py:1",
                "verdict": "VIOLATION", "severity": "high", "recommended_fix": "f",
                "citations": [{"title": "never retrieved",
                               "url": "https://arxiv.org/abs/2401.99999",
                               "doi": "10.1234/fabricated", "fetched": True}]}],
              open(fab, "w"))

    def rejected(tree):
        lint = os.path.join(tree, "base-in-reality", "assets", "report_lint.py")
        if not os.path.isfile(lint):
            return 0
        r = sp.run([sys.executable, lint, fab, "--evidence", ev],
                   capture_output=True, text=True, timeout=60)
        blind = ("unrecognized arguments" in r.stderr
                 or "usage: report_lint.py <findings.json>" in r.stderr)
        if blind:
            return 0        # baseline has no evidence mode: it cannot reject this
        return 1 if r.returncode != 0 else 0

    a, b = rejected(old), rejected(new)
    row("base-in-reality", "fabricated `fetched` citation rejected (1=yes)", a, b, b > a,
        "the anti-fabrication claim rested on a boolean the agent wrote about itself")


# ── knowledge-gardener (agrees with its sibling on the git source) ──────────
def check_gardener(old, new):
    import subprocess as sp
    scratch = tempfile.mkdtemp()
    repo = os.path.join(scratch, "repo")
    os.makedirs(repo)
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    sp.run(["git", "init", "-q", repo], check=True, capture_output=True)
    open(os.path.join(repo, "app.py"), "w").write("print(1)\n")
    sp.run(["git", "add", "-A"], cwd=repo, env=env, capture_output=True)
    sp.run(["git", "commit", "-qm", "i"], cwd=repo, env=env, capture_output=True)

    def stale_on_selfpin(tree):
        g = os.path.join(tree, "knowledge-gardener", "assets", "garden.py")
        if not os.path.isfile(g):
            return 1
        code = (
            "import importlib.util, sys, json\n"
            "spec = importlib.util.spec_from_file_location('g', %r)\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "import subprocess\n"
            "b = %r\n"
            "import os; os.makedirs(b, exist_ok=True)\n"
            "open(os.path.join(b,'index.md'),'w').write('# Subjects\\n')\n"
            "pin = m.fingerprint(%r, 'git', b)\n"
            "subprocess.run(['git','add','-A'], cwd=%r, capture_output=True)\n"
            "subprocess.run(['git','commit','-qm','bundle'], cwd=%r, capture_output=True)\n"
            "cur = m.fingerprint(%r, 'git', b)\n"
            "try: d = m.git_drift(%r, pin, cur, b)\n"
            "except TypeError: d = m.git_drift(%r, pin, cur)\n"
            "print(json.dumps({'stale': 1 if d['changed_files'] else 0}))\n"
            % (g, os.path.join(repo, "docs", "knowledge"), repo, repo, repo,
               repo, repo, repo))
        r = sp.run([sys.executable, "-c", code], capture_output=True, text=True,
                   timeout=120, env=env)
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])["stale"]
        except Exception:
            return 1

    a = stale_on_selfpin(old)
    # fresh repo for the second arm so the commit state matches
    sp.run(["git", "checkout", "-q", "--", "."], cwd=repo, capture_output=True)
    b = stale_on_selfpin(new)
    row("knowledge-gardener", "in-repo bundle wrongly reported STALE (lower=better)",
        a, b, b <= a and b == 0,
        "the skill claimed its verdicts `agree exactly` with okf.py's; they did not")


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else BASE_DEFAULT
    if shutil.which("git") is None:
        print("AB_RESULT: SKIP (git not available)")
        return 0
    if subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", base + "^{commit}"],
                      capture_output=True).returncode != 0:
        print(f"AB_RESULT: SKIP (base ref {base!r} not in this clone — "
              f"shallow checkout? fetch it, or pass another ref)")
        return 0

    tmp = tempfile.mkdtemp(prefix="ab-validate-")
    old = os.path.join(tmp, "baseline")
    r = subprocess.run(["git", "-C", REPO, "worktree", "add", "--detach", old, base],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("AB_RESULT: SKIP (could not create baseline worktree: %s)"
              % r.stderr.strip()[-120:])
        shutil.rmtree(tmp, ignore_errors=True)
        return 0
    try:
        print(f"baseline: {base}   candidate: working tree\n")
        check_world_model(old, REPO)
        check_hygiene(old, REPO)
        check_okf(old, REPO)
        check_loops(old, REPO)
        check_mockstar(old, REPO)
        check_audit_guardrails(old, REPO)
        check_autopsy(old, REPO)
        check_grounding(old, REPO)
        check_gardener(old, REPO)
    finally:
        subprocess.run(["git", "-C", REPO, "worktree", "remove", "--force", old],
                       capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)

    width = max(len(r["dimension"]) for r in ROWS)
    current = None
    for r in ROWS:
        if r["skill"] != current:
            current = r["skill"]
            print("=== %s ===" % current)
        if not r["ok"]:
            mark = "WORSE"
        elif r["kind"] == "guard":
            mark = "HELD"
        else:
            mark = "IMPROVED" if r["moved"] else "UNPROVEN"
        print("  %-*s  old=%-20s new=%-20s %s"
              % (width, r["dimension"], str(r["old"]), str(r["new"]), mark))
        if r["note"]:
            print("  %-*s    ^ %s" % (width, "", r["note"]))
        print()

    if PROBE_ERRORS:
        print("=== probe failures ===")
        for tree, subdir, err in PROBE_ERRORS:
            print("  %s/%s: %s" % (os.path.basename(tree.rstrip("/")), subdir,
                                   err.splitlines()[-1][:120] if err else "(no output)"))
        print("\n%d probe(s) crashed — no dimension can be classified from a run\n"
              "that did not measure anything.\nAB_RESULT: FAIL" % len(PROBE_ERRORS))
        return 1

    worse = [r for r in ROWS if not r["ok"]]
    unproven = [r for r in ROWS if r["ok"] and r["kind"] == "delta" and not r["moved"]]
    improved = [r for r in ROWS if r["ok"] and r["kind"] == "delta" and r["moved"]]
    held = [r for r in ROWS if r["ok"] and r["kind"] == "guard"]
    print("%d improved · %d guards held · %d unproven · %d worse"
          % (len(improved), len(held), len(unproven), len(worse)))
    ok = not worse and not unproven
    print("AB_RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
