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


def row(skill, dimension, old, new, ok, note="", kind="delta"):
    """kind='delta' claims the new arm is strictly better; kind='guard' claims only
    that behaviour is unchanged; kind='skip' means the capability does not exist in
    the baseline at all, so there is nothing to compare. Keeping them distinct is
    what stops a held guard — or an absent baseline — from being counted as a win."""
    ROWS.append({"skill": skill, "dimension": dimension, "old": old, "new": new,
                 "ok": ok, "note": note, "kind": kind,
                 "moved": str(old) != str(new)})


def row_or_skip(skill, dimension, old_probe, new_probe, key, expected, note=""):
    """Regression guard for a measurement that may not exist in the baseline.

    A skill added after the baseline commit has no old arm — measuring it would
    print a fabricated 'improvement' against a module that was never there. So the
    row is recorded as a SKIP: the probe still runs against the candidate and is
    asserted against `expected`, and it becomes a real HELD/WORSE guard the moment
    the baseline contains the skill."""
    new = new_probe.get(key)
    if "_error" in old_probe:
        ROWS.append({"skill": skill, "dimension": dimension, "old": "absent",
                     "new": new, "ok": new == expected, "kind": "skip",
                     "moved": False,
                     "note": note or "not in the baseline tree; asserted against the "
                                     "candidate only"})
        return
    row(skill, dimension, old_probe.get(key), new, new == expected, note, kind="guard")


def probe(tree, subdir, code):
    """Run `code` with <tree>/<subdir> on sys.path; return its JSON stdout."""
    src = ("import sys, json, os, tempfile\nsys.path.insert(0, %r)\n%s"
           % (os.path.join(tree, subdir), code))
    r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                       text=True, timeout=300)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"_error": (r.stderr or r.stdout).strip()[-300:]}


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



# ── okf-capability-catalog ──────────────────────────────────────────────────
# Guardrails whose whole value is that they REFUSE things. They are regression
# guards, not deltas: the day one of these starts honouring a claim it used to
# reject, the catalog is lying to whoever reads it.

CC_READINESS = r"""
import catalog_core as C, datetime as D
root = tempfile.mkdtemp()
def w(rel, meta):
    p = os.path.join(root, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(C.render_doc(meta))
open(os.path.join(root, "catalog.config.yaml"), "w").write(
    C.dump_yaml({"environments": [{"name": "production"}]}))
for t in ("prov", "cons", "up"):
    w("teams/%s/team.md" % t, {"type": "Team", "team_id": t, "provenance": "claimed"})
w("teams/up/capabilities/upstream.md",
  {"type": "Capability", "capability_id": "up/upstream",
   "owning_team": "/teams/up/team.md", "upstream": {"attested": True},
   "readiness": [{"environment": "production", "state": "provider_tested"}]})
# The provider types consumer_verified into its OWN file, and the only consumer
# evidence is a contract test. Neither may raise readiness.
w("teams/prov/capabilities/cap.md",
  {"type": "Capability", "capability_id": "prov/cap",
   "owning_team": "/teams/prov/team.md", "upstream": {"attested": True},
   "requires": [{"capability": "/teams/up/capabilities/upstream.md",
                 "criticality": "hard"}],
   "readiness": [{"environment": "production", "state": "consumer_verified"}]})
w("teams/cons/verifications/v.md",
  {"type": "Verification", "capability": "/teams/prov/capabilities/cap.md",
   "verifying_team": "/teams/cons/team.md", "environment": "production",
   "result": "verified", "kind": "contract", "verified_by": "bob",
   "verified_at": "2026-08-10T09:00:00Z"})
cat = C.Catalog.load(root)
cap = cat.capability_by_id("prov/cap")
day = D.date(2026, 8, 14)
eff = C.effective_readiness(cat, cap, "production", day)
print(json.dumps({"own": C.own_readiness(cat, cap, "production", day)[0],
                  "effective": eff.state, "verdict": eff.verdict}))
"""

CC_STATE = r"""
import catalog_core as C, datetime as D
root = tempfile.mkdtemp()
def w(rel, meta):
    p = os.path.join(root, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(C.render_doc(meta))
open(os.path.join(root, "catalog.config.yaml"), "w").write(
    C.dump_yaml({"environments": [{"name": "production"}]}))
for t in ("a", "b", "c"):
    w("teams/%s/team.md" % t, {"type": "Team", "team_id": t, "provenance": "claimed"})
for t, slug in (("a", "one"), ("b", "two")):
    w("teams/%s/capabilities/%s.md" % (t, slug),
      {"type": "Capability", "capability_id": "%s/%s" % (t, slug),
       "owning_team": "/teams/%s/team.md" % t, "upstream": {"attested": True}})
def dep(i, cons, prov, cap, promised, **kw):
    meta = {"type": "Dependency", "dependency_id": i,
            "consumer_team": "/teams/%s/team.md" % cons,
            "provider_team": "/teams/%s/team.md" % prov, "capability": cap,
            "target_environment": "production", "promised_date": promised,
            "fallback": {"description": "x", "execution_days": 3},
            "state": "acknowledged"}
    meta.update(kw)
    w("dependencies/%s.md" % i, meta)
dep("dep-up", "b", "a", "/teams/a/capabilities/one.md", "2026-08-20")
dep("dep-down", "c", "b", "/teams/b/capabilities/two.md", "2026-11-01",
    depends_on=["dep-up"])
cat = C.Catalog.load(root)
day = D.date(2026, 8, 25)
up, down = cat.dependencies["dep-up"], cat.dependencies["dep-down"]
state = C.effective_state(cat, down, day)
print(json.dumps({
    "ponr": C.point_of_no_return(up, cat.config).isoformat(),
    "upstream": C.effective_state(cat, up, day).state,
    "downstream": state.state, "originator": state.originator}))
"""

CC_ENFORCE = r"""
import catalog_core as C
root = tempfile.mkdtemp()
def w(rel, meta):
    p = os.path.join(root, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(C.render_doc(meta))
open(os.path.join(root, "catalog.config.yaml"), "w").write(C.dump_yaml({}))
for t in ("prov", "cons"):
    w("teams/%s/team.md" % t, {"type": "Team", "team_id": t, "provenance": "claimed"})
w("teams/prov/capabilities/cap.md",
  {"type": "Capability", "capability_id": "prov/cap",
   "owning_team": "/teams/prov/team.md"})
ver = C.render_doc({"type": "Verification",
                    "capability": "/teams/prov/capabilities/cap.md",
                    "verifying_team": "/teams/cons/team.md",
                    "environment": "production", "result": "verified",
                    "kind": "deployment", "verified_by": "bob",
                    "verified_at": "2026-08-10T09:00:00Z"})
cat = C.Catalog.load(root)
# The providing team commits the consuming team's acceptance for them.
bad = C.enforce(cat, [{"sha": "x", "author_handle": "alice", "author_team": "prov",
                       "committed_at": "2026-08-10T10:00:00Z",
                       "files": [{"path": "teams/cons/verifications/v.md",
                                  "content": ver}]}])
good = C.enforce(cat, [{"sha": "y", "author_handle": "bob", "author_team": "cons",
                        "committed_at": "2026-08-10T10:00:00Z",
                        "files": [{"path": "teams/cons/verifications/v.md",
                                   "content": ver}]}])
print(json.dumps({"provider_authored_caught": len(bad) > 0,
                  "consumer_authored_clean": len(good) == 0}))
"""


def check_capability_catalog(old, new):
    s = "okf-capability-catalog"
    sub = s + "/assets"
    ro, rn = (probe(t, sub, CC_READINESS) for t in (old, new))
    row_or_skip(s, "provider file typing consumer_verified is honoured", ro, rn,
                "own", "provider_tested",
                "clamped to what a provider-owned document may assert")
    row_or_skip(s, "effective readiness under a provider_tested hard upstream", ro, rn,
                "effective", "provider_tested",
                "min over the hard closure; a contract-kind verification raises nothing")
    row_or_skip(s, "consumer-facing verdict for that capability", ro, rn,
                "verdict", "not_ready")

    so, sn = (probe(t, sub, CC_STATE) for t in (old, new))
    row_or_skip(s, "point of no return (promised 2026-08-20 − 3d)", so, sn,
                "ponr", "2026-08-17", "computed backwards from the deadline")
    row_or_skip(s, "upstream edge past its PONR, unconfirmed", so, sn,
                "upstream", "tripped", "trips with no human action")
    row_or_skip(s, "downstream edge state after the upstream trips", so, sn,
                "downstream", "at_risk", "propagated automatically")
    row_or_skip(s, "originating edge named on the downstream", so, sn,
                "originator", "dep-up")

    eo, en = (probe(t, sub, CC_ENFORCE) for t in (old, new))
    row_or_skip(s, "provider-authored verification caught at the commit", eo, en,
                "provider_authored_caught", True,
                "the check the mode-level guards cannot make")
    row_or_skip(s, "honest consumer-authored verification passes", eo, en,
                "consumer_authored_clean", True, "no false positive on the honest path")


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
        check_capability_catalog(old, REPO)
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
        elif r["kind"] == "skip":
            mark = "SKIP"
        elif r["kind"] == "guard":
            mark = "HELD"
        else:
            mark = "IMPROVED" if r["moved"] else "UNPROVEN"
        print("  %-*s  old=%-20s new=%-20s %s"
              % (width, r["dimension"], str(r["old"]), str(r["new"]), mark))
        if r["note"]:
            print("  %-*s    ^ %s" % (width, "", r["note"]))
        print()

    worse = [r for r in ROWS if not r["ok"]]
    skipped = [r for r in ROWS if r["ok"] and r["kind"] == "skip"]
    unproven = [r for r in ROWS if r["ok"] and r["kind"] == "delta" and not r["moved"]]
    improved = [r for r in ROWS if r["ok"] and r["kind"] == "delta" and r["moved"]]
    held = [r for r in ROWS if r["ok"] and r["kind"] == "guard"]
    print("%d improved · %d guards held · %d unproven · %d worse · %d skipped "
          "(absent in baseline)"
          % (len(improved), len(held), len(unproven), len(worse), len(skipped)))
    ok = not worse and not unproven
    print("AB_RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
