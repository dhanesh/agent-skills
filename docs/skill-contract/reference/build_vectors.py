#!/usr/bin/env python3
"""build_vectors.py: regenerate docs/skill-contract/vectors/ deterministically.

The vectors are the language-neutral contract: a checker in any language
conforms if it reaches the verdict each vector expects. They are committed;
this generator exists so they stay consistent, and test_contract_check.py
fails when the committed files differ from what it writes.

    python3 build_vectors.py [out-dir]      # default: ../vectors
"""
import copy
import hashlib
import json
import os
import shutil
import sys

KIND = "https://github.com/dhanesh/agent-skills/skill-contract/task-plan/v1"
KIND_V2 = KIND[:-1] + "2"
SPEC = "# Spec\n\n- R1: The export must include every row.\n"
SPEC_EDITED = SPEC + "- R2: The export must keep row order.\n"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def skill_md(name="alpha", optin=None, top_extra="", body="# Alpha\n",
             description="Hands off a task-plan envelope."):
    meta = '  author: example\n  version: "1.0.0"\n'
    if optin is not None:
        meta += '  skill-contract: "%s"\n' % optin
    return ("---\nname: %s\ndescription: %s\nlicense: MIT\ncompatibility: none\n%s"
            "metadata:\n%s---\n\n%s" % (name, description, top_extra, meta, body))


def contract_section(block):
    return "## Contract\n\nThis skill follows skill-contract v1.\n\n```json skill-contract\n%s\n```\n" % block


def block(provides=(), consumes=(), **extra):
    d = {"provides": list(provides), "consumes": list(consumes)}
    d.update(extra)
    return json.dumps(d)


def skill_vector(md, result, commandments=(), adopter=None, warnings=0, name="alpha"):
    exp = {"result": result, "commandments": sorted(commandments), "warnings": warnings}
    if adopter is not None:
        exp["adopter"] = adopter
    return {"input": {"type": "skill", "dir": name, "skill_md": md}, "expect": exp}


def skill_cases():
    adopter = skill_md(optin="1", body="# Alpha\n\n" + contract_section(block([KIND])))
    return [
        ("c1", "valid", "adopter", skill_vector(adopter, "PASS", adopter=True)),
        ("c1", "valid", "non-adopter", skill_vector(skill_md(), "PASS", adopter=False)),
        ("c1", "valid", "description-misses-kind",
         skill_vector(skill_md(optin="1", description="Does alpha things.",
                               body=contract_section(block([KIND]))),
                      "PASS", adopter=True, warnings=1)),
        ("c1", "invalid", "block-without-optin",
         skill_vector(skill_md(body=contract_section(block([KIND]))), "FAIL", [1])),
        ("c1", "invalid", "optin-without-block",
         skill_vector(skill_md(optin="1"), "FAIL", [1])),
        ("c1", "invalid", "optin-wrong-value",
         skill_vector(skill_md(optin="2", body=contract_section(block([KIND]))), "FAIL", [1])),
        ("c1", "invalid", "two-blocks",
         skill_vector(skill_md(optin="1", body=contract_section(block([KIND]))
                               + "\n```json skill-contract\n" + block() + "\n```\n"), "FAIL", [1])),
        ("c1", "invalid", "block-outside-contract",
         skill_vector(skill_md(optin="1", body="## Other\n\n```json skill-contract\n"
                               + block([KIND]) + "\n```\n"), "FAIL", [1])),
        ("c1", "invalid", "bad-json",
         skill_vector(skill_md(optin="1", body=contract_section('{"provides": [')), "FAIL", [1])),
        ("c1", "invalid", "not-an-object",
         skill_vector(skill_md(optin="1", body=contract_section("[]")), "FAIL", [1])),
        ("c1", "invalid", "unknown-key",
         skill_vector(skill_md(optin="1", body=contract_section(block([KIND], extra=1))), "FAIL", [1])),
        ("c1", "invalid", "provides-not-a-list",
         skill_vector(skill_md(optin="1", body=contract_section('{"provides": "x", "consumes": []}')),
                      "FAIL", [1])),
        ("c1", "invalid", "extra-frontmatter-key",
         skill_vector(skill_md(top_extra="x-spec-version: 1.0\n"), "FAIL", [1])),
        ("c2", "valid", "two-kinds",
         skill_vector(skill_md(optin="1", body=contract_section(block([KIND], [KIND_V2]))),
                      "PASS", adopter=True)),
        ("c2", "valid", "x-extension",
         skill_vector(skill_md(optin="1", body=contract_section(block([KIND], **{"x-note": "ok"}))),
                      "PASS", adopter=True)),
        ("c2", "invalid", "http-kind",
         skill_vector(skill_md(optin="1", body=contract_section(block([KIND.replace("https", "http")]))),
                      "FAIL", [2])),
        ("c2", "invalid", "no-version",
         skill_vector(skill_md(optin="1", body=contract_section(block([KIND[:-3]]))), "FAIL", [2])),
        ("c2", "invalid", "version-zero",
         skill_vector(skill_md(optin="1", body=contract_section(block([KIND[:-1] + "0"]))), "FAIL", [2])),
        ("c2", "invalid", "duplicate-kind",
         skill_vector(skill_md(optin="1", body=contract_section(block([KIND, KIND]))), "FAIL", [2])),
    ]


ENVELOPE_ID = "task-plan-v1-20260918T102200Z-a1b2c3"


def claim(test="spec-lint", by=None, outcome="passed",
          command=("{python}", "{skill_dir:alpha}/assets/lint.py", "docs/spec.md"),
          run_url=None, subject_text=SPEC):
    a = {"test": test, "assertedBy": by if by is not None else {"skill": "alpha"},
         "result": {"outcome": outcome},
         "subject": [{"name": "docs/spec.md", "digest": {"sha256": sha(subject_text)}}]}
    if run_url is not None:
        a["run_url"] = run_url
    if command is not None:
        a["command"] = list(command)
    return a


def envelope(assertions=None, kind=KIND):
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": "docs/spec.md", "digest": {"sha256": sha(SPEC)}}],
        "predicateType": kind,
        "predicate": {
            "skillContract": "1",
            "id": ENVELOPE_ID if kind == KIND else ENVELOPE_ID.replace("-v1-", "-v2-"),
            "wasAttributedTo": {"skill": "alpha", "version": "1.0.0"},
            "generatedAtTime": "2026-09-18T10:22:00Z",
            "wasRevisionOf": None,
            "payload": {"title": "Export", "tasks": []},
            "assertions": [claim()] if assertions is None else assertions,
        },
    }


def P(st):
    return st["predicate"]


def A(st):
    return st["predicate"]["assertions"][0]


def mutate(fn, base=None):
    st = copy.deepcopy(base if base is not None else envelope())
    fn(st)
    return st


def env_vector(st, result, commandments=(), files=None, stale=None, claims=None, for_skill=None):
    inp = {"type": "envelope", "envelope": st}
    if files is not None:
        inp["files"] = files
    if for_skill is not None:
        inp["for_skill"] = for_skill
    exp = {"result": result, "commandments": sorted(commandments)}
    if stale is not None:
        exp["stale"] = stale
    if claims is not None:
        exp["claims"] = claims
    return {"input": inp, "expect": exp}


def envelope_cases():
    E = env_vector
    return [
        ("c3", "valid", "minimal", E(envelope(), "PASS")),
        ("c3", "valid", "no-assertions", E(envelope(assertions=[]), "PASS")),
        ("c3", "invalid", "not-an-object", E([], "FAIL", [3])),
        ("c3", "invalid", "wrong-type", E(mutate(lambda s: s.__setitem__("_type", "x")), "FAIL", [3])),
        ("c3", "invalid", "predicate-type-not-a-kind",
         E(mutate(lambda s: s.__setitem__("predicateType", KIND.replace("https", "http"))), "FAIL", [3])),
        ("c3", "invalid", "subject-empty", E(mutate(lambda s: s.__setitem__("subject", [])), "FAIL", [3])),
        ("c3", "invalid", "absolute-subject",
         E(mutate(lambda s: s["subject"][0].__setitem__("name", "/etc/passwd")), "FAIL", [3])),
        ("c3", "invalid", "dotdot-subject",
         E(mutate(lambda s: s["subject"][0].__setitem__("name", "../secret.md")), "FAIL", [3])),
        ("c3", "invalid", "backslash-subject",
         E(mutate(lambda s: s["subject"][0].__setitem__("name", "docs\\spec.md")), "FAIL", [3])),
        ("c3", "invalid", "skillcontract-2",
         E(mutate(lambda s: P(s).__setitem__("skillContract", "2")), "FAIL", [3])),
        ("c3", "invalid", "bad-attribution",
         E(mutate(lambda s: P(s).__setitem__("wasAttributedTo", {"skill": "Alpha Skill"})), "FAIL", [3])),
        ("c3", "invalid", "bad-timestamp",
         E(mutate(lambda s: P(s).__setitem__("generatedAtTime", "2026-09-18 10:22")), "FAIL", [3])),
        ("c3", "invalid", "payload-not-an-object",
         E(mutate(lambda s: P(s).__setitem__("payload", [])), "FAIL", [3])),
        ("c4", "valid", "revision",
         E(mutate(lambda s: P(s).__setitem__("wasRevisionOf", "task-plan-v1-20260917T090000Z-00ab12")), "PASS")),
        ("c4", "invalid", "bad-id", E(mutate(lambda s: P(s).__setitem__("id", "tp-1")), "FAIL", [4])),
        ("c4", "invalid", "id-kind-mismatch",
         E(mutate(lambda s: P(s).__setitem__("id", "loop-spec-v1-20260918T102200Z-a1b2c3")), "FAIL", [4])),
        ("c4", "invalid", "revision-trailing-newline",
         E(mutate(lambda s: P(s).__setitem__("wasRevisionOf", ENVELOPE_ID + "\n")), "FAIL", [4])),
        ("c4", "invalid", "id-trailing-newline",
         E(mutate(lambda s: P(s).__setitem__("id", ENVELOPE_ID + "\n")), "FAIL", [4])),
        ("c4", "invalid", "bad-revision",
         E(mutate(lambda s: P(s).__setitem__("wasRevisionOf", "nope")), "FAIL", [4])),
        ("c5", "invalid", "no-sha256",
         E(mutate(lambda s: s["subject"][0].__setitem__("digest", {"sha1": "ab" * 20})), "FAIL", [5])),
        ("c6", "valid", "run-url",
         E(envelope([claim(command=None, run_url="https://ci.example/runs/1")]), "PASS")),
        ("c6", "valid", "bare-program",
         E(envelope([claim(command=("pytest", "tests/test_export.py"))]), "PASS")),
        ("c6", "valid", "human-asserter",
         E(envelope([claim(by={"human": "reviewer@example"})]), "PASS")),
        ("c6", "invalid", "bad-outcome",
         E(mutate(lambda s: A(s)["result"].__setitem__("outcome", "ok")), "FAIL", [6])),
        ("c6", "invalid", "no-evidence", E(envelope([claim(command=None)]), "FAIL", [6])),
        ("c6", "invalid", "command-and-run-url",
         E(envelope([claim(run_url="https://ci.example/runs/1")]), "FAIL", [6])),
        ("c6", "invalid", "empty-command", E(envelope([claim(command=())]), "FAIL", [6])),
        ("c6", "invalid", "absolute-interpreter",
         E(envelope([claim(command=("/usr/bin/python3", "lint.py"))]), "FAIL", [6])),
        ("c6", "invalid", "bare-python", E(envelope([claim(command=("python3", "lint.py"))]), "FAIL", [6])),
        ("c6", "invalid", "unknown-placeholder",
         E(envelope([claim(command=("{python}", "{repo}/lint.py"))]), "FAIL", [6])),
        ("c6", "invalid", "python-not-first",
         E(envelope([claim(command=("pytest", "{python}"))]), "FAIL", [6])),
        ("c6", "invalid", "absolute-argument",
         E(envelope([claim(command=("{python}", "/tmp/lint.py"))]), "FAIL", [6])),
        ("c6", "invalid", "two-asserters",
         E(envelope([claim(by={"skill": "alpha", "human": "reviewer@example"})]), "FAIL", [6])),
    ]


def producer_md():
    return skill_md("alpha", optin="1", body=contract_section(block([KIND])))


def consumer_md(name="beta", kinds=(KIND,), optin="1"):
    return skill_md(name, optin=optin, description="Accepts a task-plan envelope.",
                    body=contract_section(block(consumes=kinds)))


def disc_vector(roots, consumers, shadowed=(), invalid=(), plugins=None, plugin_skills=None,
                warnings_contain=(), links=(), posix_only=False, raw_skills=None):
    inp = {"type": "discovery", "kind": KIND, "from": "alpha", "roots": roots}
    if raw_skills:
        # SKILL.md files given as raw bytes (hex), for content that is not valid
        # UTF-8 text: {"<root>": {"<skill dir>": "<hex of the file's bytes>"}}.
        inp["raw_skills"] = {root: {name: data.hex() for name, data in skills.items()}
                             for root, skills in raw_skills.items()}
    if plugins is not None:
        inp["plugins"] = plugins
    if plugin_skills:
        inp["plugin_skills"] = plugin_skills
    if links:
        inp["links"] = list(links)
    v = {"input": inp, "expect": {"consumers": list(consumers), "shadowed": sorted(shadowed),
                                  "invalid": sorted(invalid),
                                  "warnings_contain": list(warnings_contain)}}
    if posix_only:
        v["posix_only"] = True
    return v


def discovery_cases():
    D = disc_vector
    sib = {"alpha": producer_md()}
    user_plugin = {"version": 2, "plugins": {"p1@market": [{"scope": "user", "installPath": "{plugins}/p1"}]}}
    project_plugin = {"version": 2, "plugins": {"p1@market": [{"scope": "project", "installPath": "{plugins}/p1"}]}}
    return [
        ("c8", "valid", "sibling-consumer", D({"sibling": dict(sib, beta=consumer_md())}, ["beta"])),
        ("c8", "valid", "project-root", D({"sibling": sib, "project": {"beta": consumer_md()}}, ["beta"])),
        ("c8", "valid", "path-beats-user",
         D({"sibling": sib, "path": {"beta": consumer_md()}, "user": {"beta": consumer_md()}},
           ["beta"], shadowed=["beta"])),
        ("c8", "valid", "plugin-user-scope",
         D({"sibling": sib}, ["beta"], plugins=user_plugin, plugin_skills={"p1": {"beta": consumer_md()}})),
        ("c8", "valid", "no-plugin-index",
         D({"sibling": dict(sib, beta=consumer_md())}, ["beta"],
           warnings_contain=["no Claude Code plugin index"])),
        ("c8", "valid", "symlink-deduplicated",
         D({"sibling": sib, "user": {"beta": consumer_md()}}, ["beta"],
           links=[{"root": "project", "name": "beta", "to_root": "user"}], posix_only=True)),
        ("c8", "invalid", "self-is-not-a-consumer",
         D({"sibling": {"alpha": skill_md("alpha", optin="1",
                                          body=contract_section(block([KIND], [KIND])))}}, [])),
        ("c8", "invalid", "v2-only-consumer",
         D({"sibling": dict(sib, beta=consumer_md(kinds=(KIND_V2,)))}, [])),
        ("c8", "invalid", "block-without-optin-ignored",
         D({"sibling": dict(sib, beta=skill_md("beta", body=contract_section(block(consumes=[KIND]))))}, [])),
        ("c8", "invalid", "broken-neighbour",
         D({"sibling": dict(sib, beta=consumer_md(),
                            gamma=skill_md("gamma", optin="1", body=contract_section('{"provides": [')))},
           ["beta"], invalid=["gamma"])),
        ("c8", "invalid", "non-utf8-neighbour",
         D({"sibling": dict(sib, beta=consumer_md())}, ["beta"], invalid=["gamma"],
           raw_skills={"sibling": {"gamma": skill_md(
               "gamma", optin="1", description="Accepts a task-plan envelope \xff.",
               body=contract_section(block(consumes=[KIND]))).encode("latin-1")}})),
        ("c8", "invalid", "plugin-project-scope",
         D({"sibling": sib}, [], plugins=project_plugin, plugin_skills={"p1": {"beta": consumer_md()}},
           warnings_contain=["only user-scope"])),
        ("c8", "invalid", "plugin-index-unknown-version",
         D({"sibling": sib}, [], plugins={"version": 3, "plugins": {}},
           warnings_contain=["unknown format"])),
    ]


def claims_cases():
    E = env_vector
    files = {"docs/spec.md": SPEC}
    ci = "https://ci.example/runs/1"
    return [
        ("c5", "valid", "fresh",
         E(envelope(), "PASS", files=files, stale=[], claims={"spec-lint": "CLAIMED"})),
        ("c5", "valid", "stale",
         E(envelope(), "PASS", files={"docs/spec.md": SPEC_EDITED}, stale=["docs/spec.md"],
           claims={"spec-lint": "STALE"})),
        ("c5", "valid", "missing-file",
         E(envelope(), "PASS", files={}, stale=["docs/spec.md"], claims={"spec-lint": "STALE"})),
        ("c7", "valid", "proven-by-another-skill",
         E(envelope([claim(by={"skill": "beta"})]), "PASS", files=files, stale=[],
           claims={"spec-lint": "PROVEN"})),
        ("c7", "valid", "proven-by-a-human",
         E(envelope([claim(by={"human": "reviewer@example"})]), "PASS", files=files, stale=[],
           claims={"spec-lint": "PROVEN"})),
        ("c7", "valid", "proven-by-ci",
         E(envelope([claim(command=None, run_url=ci)]), "PASS", files=files, stale=[],
           claims={"spec-lint": "PROVEN"})),
        ("c7", "valid", "failed",
         E(envelope([claim(outcome="failed")]), "PASS", files=files, stale=[],
           claims={"spec-lint": "FAILED"})),
        ("c7", "valid", "failed-beats-passed",
         E(envelope([claim(), claim(by={"skill": "beta"}, outcome="failed")]), "PASS", files=files,
           stale=[], claims={"spec-lint": "FAILED"})),
        ("c7", "valid", "open",
         E(envelope([claim(outcome="cantTell")]), "PASS", files=files, stale=[],
           claims={"spec-lint": "OPEN"})),
        ("c7", "valid", "stale-claim",
         E(envelope([claim(subject_text=SPEC_EDITED)]), "PASS", files=files, stale=[],
           claims={"spec-lint": "STALE"})),
        ("c7", "invalid", "own-result-is-not-proof",
         E(envelope(), "PASS", files=files, stale=[], claims={"spec-lint": "CLAIMED"})),
        ("c9", "valid", "consumer-accepts-kind",
         E(envelope(), "PASS", for_skill={"dir": "beta", "skill_md": consumer_md()})),
        ("c9", "invalid", "consumer-does-not-accept-kind",
         E(envelope(), "FAIL", [9], for_skill={"dir": "beta", "skill_md": consumer_md(kinds=(KIND_V2,))})),
        ("c9", "invalid", "for-a-non-adopter",
         E(envelope(), "FAIL", [9], for_skill={"dir": "beta", "skill_md": skill_md("beta")})),
    ]


GRANT_KIND = "https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1"
GRANT_ID = "autonomy-grant-v1-20260919T120000Z-a1b2c3"
PLAN_TEXT = '{"plan": 1}\n'
NOW = "2026-09-19T13:00:00Z"


def grant(policy=None, attributed=None, revoked=False, expires="2026-09-20T12:00:00Z",
          branch_pattern="factory/*", spec_text=SPEC, gid=GRANT_ID, rev=None,
          assertions=None, outcome="passed", generated="2026-09-19T12:00:00Z"):
    a = {"test": "grant-accepted", "assertedBy": attributed or {"human": "Dana"},
         "result": {"outcome": outcome},
         "command": ["{python}", "{skill_dir:spec-first-planning}/assets/spec_lint.py",
                     "--unattended", "docs/spec.md"],
         "subject": [{"name": "docs/spec.md", "digest": {"sha256": sha(spec_text)}}]}
    payload = {"scope": {"repo": ".", "branch_pattern": branch_pattern},
               "decisions": [{"id": "D1", "question": "Add deps?", "answer": "no",
                              "source": "sweep"}],
               "defaults": [], "gate_policy": policy if policy is not None else
               {"read_only": "auto", "local_reversible": "grant"},
               "budget": {"wall_clock_min": 60},
               "stop_on": ["new_human_decision"], "expires_at": expires,
               "system_one": {"allowed": False}, "revoked": revoked}
    return {"_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": "docs/spec.md", "digest": {"sha256": sha(spec_text)}},
                        {"name": "plan.json", "digest": {"sha256": sha(PLAN_TEXT)}}],
            "predicateType": GRANT_KIND,
            "predicate": {"skillContract": "1", "id": gid,
                          "wasAttributedTo": {"skill": "spec-first-planning", "version": "2.0.0"},
                          "generatedAtTime": generated, "wasRevisionOf": rev,
                          "payload": payload,
                          "assertions": [a] if assertions is None else assertions}}


def grant_vector(st, action, status, reason=None, files=None, branch="factory/x", others=(),
                 default_branch="main"):
    inp = {"type": "grant", "grant": st, "action": action, "now": NOW, "branch": branch,
           "default_branch": default_branch,
           "files": files if files is not None else {"docs/spec.md": SPEC, "plan.json": PLAN_TEXT},
           "others": list(others)}
    exp = {"status": status}
    if reason is not None:
        exp["reason"] = reason
    return {"input": inp, "expect": exp}


def grant_cases():
    g = grant()
    acceptance = g["predicate"]["assertions"][0]
    return [
        ("c10", "valid", "covered-grant", grant_vector(g, "local_reversible", "COVERED")),
        ("c10", "valid", "covered-auto", grant_vector(g, "read_only", "COVERED")),
        ("c10", "valid", "unlisted-class-asks", grant_vector(g, "push_branch", "ASK", "gate-ask")),
        ("c10", "valid", "expired-asks",
         grant_vector(grant(expires="2026-09-19T12:30:00Z"), "local_reversible", "ASK", "expired")),
        ("c10", "valid", "stale-asks",
         grant_vector(g, "local_reversible", "ASK", "stale",
                      files={"docs/spec.md": SPEC_EDITED, "plan.json": PLAN_TEXT})),
        ("c10", "valid", "branch-mismatch-asks",
         grant_vector(g, "local_reversible", "ASK", "branch", branch="feature/x")),
        ("c10", "valid", "revoked-asks",
         grant_vector(grant(revoked=True, assertions=[]), "local_reversible", "ASK", "revoked")),
        ("c10", "valid", "superseded-asks",
         grant_vector(g, "local_reversible", "ASK", "superseded",
                      others=[grant(revoked=True, assertions=[], rev=GRANT_ID,
                                    gid="autonomy-grant-v1-20260919T121000Z-d4e5f6")])),
        ("c10", "valid", "irreversible-absent-asks",
         grant_vector(g, "merge", "ASK", "gate-ask")),
        ("c10", "valid", "irreversible-explicit-ask-asks",
         grant_vector(grant(policy={"local_reversible": "grant", "delete": "ask"}), "delete",
                      "ASK", "gate-ask")),
        ("c10", "valid", "unsigned-push-covered",
         grant_vector(grant(policy={"push_branch": "grant", "open_pr": "grant"}), "open_pr",
                      "COVERED")),
        ("c10", "valid", "lifetime-asks",
         grant_vector(grant(generated="2026-09-25T12:00:00Z", expires="2026-09-30T12:00:00Z"),
                      "local_reversible", "ASK", "lifetime")),
        ("c10", "valid", "seven-days-exactly-covered",
         grant_vector(grant(expires="2026-09-26T12:00:00Z"), "local_reversible", "COVERED")),
        ("c10", "valid", "default-branch-asks",
         grant_vector(grant(branch_pattern="*"), "local_reversible", "ASK", "default-branch",
                      branch="main")),
        ("c10", "valid", "origin-head-default-branch-asks",
         grant_vector(grant(branch_pattern="*"), "local_reversible", "ASK", "default-branch",
                      branch="trunk", default_branch="trunk")),
        ("c10", "valid", "case-folded-default-branch-asks",
         grant_vector(grant(branch_pattern="*"), "local_reversible", "ASK", "default-branch",
                      branch="Main")),
        ("c10", "invalid", "one-subject",
         grant_vector(mutate(lambda s: s.__setitem__("subject", s["subject"][:1]), g),
                      "local_reversible", "INVALID")),
        ("c10", "invalid", "duplicate-subject",
         grant_vector(mutate(lambda s: s.__setitem__("subject", [s["subject"][0]] * 2), g),
                      "local_reversible", "INVALID")),
        ("c10", "invalid", "lifetime-over-seven-days",
         grant_vector(grant(expires="2026-09-26T12:00:01Z"), "local_reversible", "INVALID")),
        ("c10", "invalid", "merge-auto",
         grant_vector(grant(policy={"merge": "auto"}), "merge", "INVALID")),
        ("c10", "invalid", "merge-grant",
         grant_vector(grant(policy={"merge": "grant"}), "merge", "INVALID")),
        ("c10", "invalid", "delete-auto",
         grant_vector(grant(policy={"delete": "auto"}), "delete", "INVALID")),
        ("c10", "invalid", "require-signature-field",
         grant_vector(mutate(lambda s: s["predicate"]["payload"].__setitem__(
             "require_signature", {"local_reversible": "SIGNED"}), g),
                      "local_reversible", "INVALID")),
        ("c10", "invalid", "push-auto",
         grant_vector(grant(policy={"push_branch": "auto"}), "push_branch", "INVALID")),
        ("c10", "invalid", "skill-attributed",
         grant_vector(grant(attributed={"skill": "spec-first-planning"}), "local_reversible",
                      "INVALID")),
        ("c10", "invalid", "no-acceptance",
         grant_vector(grant(assertions=[]), "local_reversible", "INVALID")),
        ("c10", "invalid", "two-acceptances",
         grant_vector(grant(assertions=[acceptance, acceptance]), "local_reversible", "INVALID")),
        ("c10", "invalid", "acceptance-not-passed",
         grant_vector(grant(outcome="cantTell"), "local_reversible", "INVALID")),
        ("c10", "invalid", "unknown-gate",
         grant_vector(grant(policy={"local_reversible": "yes"}), "local_reversible", "INVALID")),
        ("c10", "invalid", "unknown-class",
         grant_vector(grant(policy={"launch": "grant"}), "local_reversible", "INVALID")),
        ("c10", "invalid", "impossible-expiry",
         grant_vector(grant(expires="2026-13-40T00:00:00Z"), "local_reversible", "INVALID")),
    ]


def cases():
    return (skill_cases() + envelope_cases() + discovery_cases() + claims_cases()
            + grant_cases())


def write_all(out_dir):
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    for cdir, validity, name, vector in cases():
        d = os.path.join(out_dir, cdir, validity)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name + ".json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(vector, f, indent=2, sort_keys=True)
            f.write("\n")


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(here), "vectors")
    write_all(out)
    print("VECTORS_WRITTEN: %s (%d)" % (out, len(cases())))
