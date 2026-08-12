#!/usr/bin/env python3
"""Harness for the interview-elicitation blind A/B.

Builds two blinded skill variants, a seeded catalog per run, and a scripted
respondent that is vague by default and specific only when pressed.
"""
import json, os, shutil, subprocess, sys

SKILL = "/home/user/agent-skills/okf-capability-catalog"
ROOT = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(SKILL, "assets", "okf_catalog.py")

# arm label -> what it actually is. Agents never see this file.
MAPPING = {"p": "candidate", "w": "baseline"}


def sh(*a):
    r = subprocess.run(a, capture_output=True, text=True)
    if r.returncode not in (0,):
        print("WARN", a[:4], r.returncode, (r.stdout + r.stderr)[-300:])
    return r.stdout


def build_variants():
    v = os.path.join(ROOT, "variants")
    shutil.rmtree(v, ignore_errors=True)
    # candidate: the whole skill
    cand = os.path.join(v, "variant-p")
    os.makedirs(cand)
    for item in ("SKILL.md", "README.md", "assets", "references", "scripts"):
        src = os.path.join(SKILL, item)
        dst = os.path.join(cand, item)
        shutil.copytree(src, dst) if os.path.isdir(src) else shutil.copy2(src, dst)
    # baseline: same tooling, no elicitation guidance
    base = os.path.join(v, "variant-w")
    os.makedirs(os.path.join(base, "references"))
    shutil.copytree(os.path.join(SKILL, "assets"), os.path.join(base, "assets"))
    shutil.copytree(os.path.join(SKILL, "scripts"), os.path.join(base, "scripts"))
    shutil.copy2(os.path.join(SKILL, "references", "parameters.md"),
                 os.path.join(base, "references", "parameters.md"))
    with open(os.path.join(base, "README.md"), "w") as f:
        f.write("# capability catalog tooling\n\nUse `assets/okf_catalog.py` to record a "
                "cross-team dependency in the catalog bundle. Flags: "
                "`references/parameters.md`.\n")
    # the baseline must not carry the runbook through the CLI's help mode either
    os.remove(os.path.join(base, "assets", "okf_catalog.py"))
    with open(os.path.join(SKILL, "assets", "okf_catalog.py")) as f:
        src = f.read()
    with open(os.path.join(base, "assets", "okf_catalog.py"), "w") as f:
        f.write(src)
    return cand, base


def seed_bundle(dest, scenario):
    """A catalog with payments+checkout claimed. s3 adds staging-only testing."""
    sh(sys.executable, CLI, "init", dest, "--org", "acme", "--platform-team", "platform",
       "--now", "2026-08-01T09:00:00Z")
    repos = os.path.join(ROOT, "repos")
    for team, extra in (("payments", True), ("checkout", False)):
        sh(sys.executable, CLI, "annotate", dest, "--repo", os.path.join(repos, team),
           "--branch", "develop", "--attest-upstream", "yes", "--by", team,
           "--now", "2026-08-02T09:00:00Z")
    if scenario == "s3":
        sh(sys.executable, CLI, "tested", dest, "--team", "payments",
           "--capability", "payments/initiate-refund", "--environment", "staging",
           "--evidence", "https://ci.example/build/8842", "--by", "alice",
           "--now", "2026-08-03T09:00:00Z")


def build_repos():
    repos = os.path.join(ROOT, "repos")
    shutil.rmtree(repos, ignore_errors=True)
    os.makedirs(os.path.join(repos, "payments", ".okf"))
    os.makedirs(os.path.join(repos, "checkout", ".okf"))
    with open(os.path.join(repos, "payments", ".okf", "team.yaml"), "w") as f:
        f.write("team_id: payments\ntitle: Payments\nlead: alice\nchannel: \"#team-payments\"\n")
    with open(os.path.join(repos, "payments", ".okf", "capabilities.yaml"), "w") as f:
        f.write("""service:
  id: payment-orchestrator
  title: Payment Orchestrator
capabilities:
  - id: initiate-refund
    title: Initiate Refund
    description: Reverses a settled payment and emits a refund lifecycle event.
runtimes:
  - environment: staging
    platform: EKS
  - environment: production
    platform: ECS
interfaces:
  produces:
    - kind: kafka_topic
      name: payments.refund.v1
""")
    with open(os.path.join(repos, "checkout", ".okf", "team.yaml"), "w") as f:
        f.write("team_id: checkout\ntitle: Checkout\nlead: bob\nchannel: \"#team-checkout\"\n")
    with open(os.path.join(repos, "checkout", ".okf", "capabilities.yaml"), "w") as f:
        f.write("service:\n  id: checkout-web\n  title: Checkout Web\ncapabilities:\n"
                "  - id: order-detail\n    title: Order Detail\n")


RESPONDENT = r'''#!/usr/bin/env python3
"""The Checkout engineer. Vague the first time a topic comes up; specific only
when the interviewer comes back at it.

This is the measurement instrument. An interviewer that accepts the first answer
writes down "it'd be bad"; one that follows up gets 40 tickets a week. Identical
in both arms, and deterministic so the same interview always scores the same.
"""
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "transcript.md")
STATE = os.path.join(HERE, ".asked")
LAST = os.path.join(HERE, ".last")
SCENARIO = open(os.path.join(HERE, ".scenario")).read().strip()

SPECIFICS = {
 "s1": {"consequence": "Ops does them by hand in the admin console — about 40 tickets a week, "
                       "15 minutes each. Customer SLA is not affected.",
        "date": "2026-08-25.",
        "fallback_days": "Three days. It needs a release train slot."},
 "s2": {"consequence": "Support answers 'where is my order' by hand — roughly 120 chats a week, "
                       "10 minutes each, and it is the top driver of contact volume.",
        "date": "2026-09-30.",
        "fallback_days": "Two days to ship a dumb date filter."},
 "s3": {"consequence": "The launch email goes out without the refund link. Marketing resends to "
                       "200k users, and ops eats about 40 manual refunds a week until it lands.",
        "date": "2026-09-15.",
        "fallback_days": "Three days, because of the release train."},
}[SCENARIO]

VAGUE = {
 "consequence": "It'd be bad for the refunds work, honestly.",
 "date": "End of the month-ish?",
 "fallback_days": "TBD, a couple of days maybe?",
}

# Language that signals the interviewer is coming back at a vague answer rather
# than raising a new topic.
FOLLOWUP = re.compile(r"how many|how much|how often|how long|per week|per day|quantify|"
                      r"specific|exactly|exact|a number|roughly|who feels|which team|"
                      r"estimate|ballpark|hours|tickets|concrete|put a number|you said|"
                      r"that's not|can you be|more precise|for example|such as|i need a",
                      re.I)
TOPIC = [
 ("fallback_days", re.compile(r"how long.{0,60}(stand|set up|put in place|build|ship|"
                              r"implement|turn on|roll out)|"
                              r"(fallback|flag|workaround|degraded|plan b).{0,60}"
                              r"(how long|take|days|time to)|execution[_ ]days|"
                              r"(days|time|long).{0,40}(fallback|flag|workaround|degraded)|"
                              r"number of days", re.I)),
 ("consequence", re.compile(r"if (it|this|that|we|they).{0,60}(late|slip|miss|not there|"
                            r"isn't there|doesn't land)|what happens|consequence|impact|"
                            r"what breaks|who is affected|who feels|cost of (the )?delay|"
                            r"what does that cost", re.I)),
 ("fallback", re.compile(r"fallback|degraded|workaround|feature flag|plan b|ship instead|"
                         r"partial release", re.I)),
 ("date", re.compile(r"by when|what date|deadline|when do you need|need it by|target date|"
                     r"when does this|what.{0,20}timeline", re.I)),
 ("environment", re.compile(r"environment|staging|production|prod\b", re.I)),
 ("capability", re.compile(r"which capability|what do you need from|which team|what are you "
                           r"building|which service|what exactly are you", re.I)),
]
ANSWERS = {
 "fallback": "We could hide it behind a feature flag and leave it off, I suppose.",
 "environment": "Production. That is the only one that matters for this.",
 "capability": {"s1": "The refund thing Payments own — initiate refund.",
                "s2": "There isn't one. Search Platform would have to build it.",
                "s3": "Payments' initiate-refund capability."}[SCENARIO],
}

question = " ".join(sys.argv[1:]).strip()
asked = [a for a in (open(STATE).read().split("\n") if os.path.exists(STATE) else []) if a]
last = open(LAST).read().strip() if os.path.exists(LAST) else ""

topic = next((name for name, rx in TOPIC if rx.search(question)), None)
# A targeted probe that names no topic is a follow-up on the last one — which is
# exactly the behaviour being measured, so it must not fall through to "huh?".
if topic is None and last and FOLLOWUP.search(question):
    topic = last

if topic in VAGUE:
    # First time this comes up you get the shrug, however well the question is
    # phrased. Come back at it and you get the real number.
    answer = SPECIFICS[topic] if topic in asked else VAGUE[topic]
elif topic in ANSWERS:
    answer = ANSWERS[topic]
else:
    answer = "Not sure what you mean — what exactly do you need from me?"

if topic:
    asked.append(topic)
    open(STATE, "w").write("\n".join(asked))
    open(LAST, "w").write(topic)
with open(LOG, "a") as fh:
    fh.write("**Interviewer:** %s\n\n**Engineer:** %s\n\n" % (question, answer))
print(answer)
'''

REQUESTS = {
 "s1": "We're adding a refund button to the order detail screen for the launch this month. "
       "It needs the Payments team's refund capability working in production. Record whatever "
       "needs recording in the catalog.",
 "s2": "We want search on the order history page. No search service exists yet — the "
       "'search-platform' team would have to build one. Record whatever needs recording in "
       "the catalog.",
 "s3": "We're picking up the refunds work. Payments tell us their refund capability is "
       "tested in staging. Record whatever needs recording in the catalog.",
}


def main():
    build_repos()
    cand, base = build_variants()
    variants = {"p": cand, "w": base}
    runs = {}
    runs_dir = os.path.join(ROOT, "runs")
    shutil.rmtree(runs_dir, ignore_errors=True)
    for scenario in ("s1", "s2", "s3"):
        for arm in ("p", "w"):
            for tier in ("t1", "t2"):
                rid = f"{scenario}-{arm}-{tier}"
                rdir = os.path.join(runs_dir, rid)
                os.makedirs(rdir)
                with open(os.path.join(rdir, ".scenario"), "w") as f:
                    f.write(scenario)
                with open(os.path.join(rdir, "respondent.py"), "w") as f:
                    f.write(RESPONDENT)
                open(os.path.join(rdir, "transcript.md"), "w").close()
                seed_bundle(os.path.join(rdir, "bundle"), scenario)
                runs[rid] = {"scenario": scenario, "arm": arm, "tier": tier,
                             "skill": variants[arm], "bundle": os.path.join(rdir, "bundle"),
                             "dir": rdir, "request": REQUESTS[scenario]}
    with open(os.path.join(ROOT, "mapping.json"), "w") as f:
        json.dump({"arms": MAPPING, "runs": runs}, f, indent=2)
    print(f"built {len(runs)} runs")


if __name__ == "__main__":
    main()
