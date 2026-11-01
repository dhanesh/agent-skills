#!/usr/bin/env python3
"""test_conductor_docs.py — the documentation of proof-tier trust (R9).

SKILL.md's "What the proof is worth" names each tier (proved, bounded, property,
runtime) with what it proves and what it does not, and the four limits it rests on (the
runner table, the mutant floor, the escape-hatch scan, the `finish` re-run). The reviewer
brief asks the reviewer to search a Rust diff for `assume(`, `admit(` and
`external_body`. SKILL.md and run-protocol.md list `escape-hatch` as a repair, the clause
rule and every `INTEGRATION:` proof outcome. CONDUCTOR_VERSION, LEDGER_VERSION and
SKILL.md's `metadata.version` are 1.7.0, README.md has a 1.7.0 entry, and a run-result
envelope that `finish` writes is attributed to version 1.7.0.
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C  # noqa: E402
import ledger as L  # noqa: E402
from conductor_testkit import GIT, new_run, repo  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
VERSION = "1.7.0"
TIERS = ("proved", "bounded", "property", "runtime")
OUTCOMES = ("pass", "fail", "flaky", "unavailable", "stale-mutant", "uncaught")
TOKENS = ("assume(", "admit(", "external_body")


def read(*parts):
    with open(os.path.join(SKILL_ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def section(text, heading):
    """The body of the `## <heading>` section, up to the next `## ` heading."""
    m = re.search(r"^## %s\n(.*?)(?=^## |\Z)" % re.escape(heading), text, re.S | re.M)
    if not m:
        raise AssertionError("no '## %s' section" % heading)
    return m.group(1)


def flat(text):
    return " ".join(text.split())


class ProofWorth(unittest.TestCase):
    def setUp(self):
        self.body = section(read("SKILL.md"), "What the proof is worth")

    def tier_item(self, tier):
        m = re.search(r"^- \*\*%s\*\*(.*?)(?=^- \*\*|^\s*$)" % tier, self.body, re.S | re.M)
        self.assertIsNotNone(m, "no '- **%s**' item in What the proof is worth" % tier)
        return flat(m.group(1))

    def test_each_tier_says_what_it_proves_and_what_it_does_not(self):
        for tier in TIERS:
            item = self.tier_item(tier)
            self.assertRegex(item, r"\bproves?\b", tier)
            self.assertRegex(item, r"\bdoes not prove\b|\bproves only\b", tier)

    def test_the_tiers_name_their_tools(self):
        self.assertIn("verus", self.tier_item("proved"))
        self.assertIn("kani", self.tier_item("bounded"))
        for tool in ("proptest", "hypothesis", "fast-check", "rapid"):
            self.assertIn(tool, self.tier_item("property"))

    def test_the_section_names_the_four_limits(self):
        body = flat(self.body)
        self.assertIn("runner table", body)
        self.assertIn("mutant floor", body)
        self.assertIn("escape-hatch scan", body)
        for token in TOKENS:
            self.assertIn("`%s`" % token, body)
        self.assertIn("At `finish`, each proved, bounded or property clause is re-run on the "
                      "integration head", body)
        self.assertIn("`finish` does not re-run it", self.tier_item("runtime"))


class ReviewerBrief(unittest.TestCase):
    def test_the_brief_asks_for_the_three_tokens_in_a_rust_diff(self):
        brief = section(read("SKILL.md"), "The reviewer brief")
        m = re.search(r"```text\n(.*?)```", brief, re.S)
        self.assertIsNotNone(m, "the reviewer brief has no text block")
        text = flat(m.group(1))
        self.assertIn("Rust (.rs) file", text)
        self.assertIn("search its added lines for", text)
        for token in TOKENS:
            self.assertIn("`%s`" % token, text)


class EvidenceGateDocs(unittest.TestCase):
    def test_skill_md_lists_escape_hatch_as_a_repair(self):
        text = flat(read("SKILL.md"))
        gate = flat(section(read("SKILL.md"), "The evidence gate"))
        self.assertIn("(`escape-hatch <path>:<line>`, the first hit; a repair", gate)
        self.assertIn("`tier-below-required` and `escape-hatch` do not park it but send it "
                      "back as a repair", text)
        self.assertIn("`evidence-diff-unreadable`", gate)

    def test_run_protocol_lists_escape_hatch_as_a_repair(self):
        text = read("references", "run-protocol.md")
        row = [line for line in text.splitlines() if "`escape-hatch <path>:<line>`" in line]
        self.assertEqual(len(row), 1, "one evidence-table row names escape-hatch")
        self.assertIn("a repair, as for `evidence-failed`", row[0])
        self.assertIn("parked `evidence-diff-unreadable`", text)

    def test_both_docs_state_the_clause_rule(self):
        self.assertIn("a clause counts at any tier only when the feature's `## Contract` at "
                      "the head defines it", flat(read("SKILL.md")))
        self.assertIn("The clause rule: a clause counts at any tier only when the "
                      "`## Contract` section", flat(read("references", "run-protocol.md")))

    def test_both_docs_list_the_integration_outcomes(self):
        for doc in (read("SKILL.md"), read("references", "run-protocol.md")):
            text = flat(doc)
            self.assertIn("INTEGRATION: <outcome> <task> proof <feature>:C<n>", text)
            for outcome in OUTCOMES:
                self.assertIn("`%s`" % outcome, text)
        protocol = read("references", "run-protocol.md")
        for outcome in OUTCOMES:
            self.assertRegex(protocol, r"(?m)^\s*\| `%s` \|" % re.escape(outcome))

    def test_the_outcomes_match_the_code(self):
        self.assertEqual(tuple(C.PROOF_OUTCOMES), OUTCOMES)
        self.assertIn("escape-hatch", C.EVIDENCE_REPAIR)
        self.assertIn("evidence-diff-unreadable", C.EVIDENCE_PARK)

    def test_assurance_lines_name_the_integration_head(self):
        self.assertIn("ASSURANCE: <feature> <tier> <clause ids> @<head>",
                      flat(read("SKILL.md")))
        protocol = flat(read("references", "run-protocol.md"))
        self.assertIn("ending ` @<integration head, 12 characters>`", protocol)
        self.assertIn("`assurance: [{feature, tier, clauses, proofs, sha?}]`", protocol)


class Versions(unittest.TestCase):
    def test_all_three_versions_are_1_7_0(self):
        self.assertEqual(C.CONDUCTOR_VERSION, VERSION)
        self.assertEqual(L.LEDGER_VERSION, VERSION)
        self.assertIn('  version: "%s"\n' % VERSION, read("SKILL.md"))

    def test_the_readme_has_a_1_7_0_entry(self):
        self.assertRegex(read("README.md"), r"(?m)^\*\*%s\.\*\* \S" % re.escape(VERSION))


@unittest.skipUnless(shutil.which("git"), "git not installed")
class Envelope(unittest.TestCase):
    """A one-task run, finished with the push asking, writes a run-result envelope that
    names this skill at 1.7.0."""

    def out(self, argv):
        buf, ebuf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(ebuf):
            rc = C.main(argv)
        return rc, buf.getvalue()

    def test_a_run_result_envelope_carries_version_1_7_0(self):
        root = repo()
        plan = {"title": "Docs plan", "spec": "docs/spec.md", "coverage": {},
                "uncovered": [],
                "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "task T1",
                           "verify": [{"text": "ok", "command": ["true"]}],
                           "depends_on": []}]}
        policy = {"read_only": "auto", "local_reversible": "grant", "push_branch": "ask",
                  "open_pr": "ask"}
        st, _ = new_run(root, plan, policy=policy)
        self.assertEqual(self.out(["start", "T1", "--root", root])[0], 0)
        wt = C.State.load(st.state_path).tasks["T1"]["worktree"]
        with open(os.path.join(wt, "b.txt"), "w") as f:
            f.write("b\n")
        subprocess.run(["git", "-C", wt, "add", "-A"], check=True)
        subprocess.run(["git", "-C", wt, "commit", "-q", "-m", "c"], check=True,
                       env=dict(os.environ, **GIT))
        self.assertEqual(self.out(["verify", "T1", "--root", root])[0], 0)
        self.assertEqual(self.out(["review", "T1", "--verdict", "pass", "--root", root])[0], 0)
        self.assertEqual(self.out(["merge", "T1", "--root", root])[0], 0)
        self.out(["next", "--root", root])
        _, out = self.out(["finish", "--root", root])
        lines = [x for x in out.splitlines() if x.startswith("FINISH: ")]
        self.assertEqual(len(lines), 1, out)
        with open(lines[0][len("FINISH: "):], encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(doc["predicate"]["wasAttributedTo"],
                         {"skill": "factory-conductor", "version": VERSION})


if __name__ == "__main__":
    unittest.main()
