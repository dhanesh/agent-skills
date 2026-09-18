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


def cases():
    return skill_cases() + envelope_cases()


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
