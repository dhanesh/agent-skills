#!/usr/bin/env python3
"""End-to-end proof for skill-contract v1 (spec section 6.2, items a-f): the
real spec-first-planning hands a task plan to the real
crafting-self-prompting-loops.

Both skills are copied into a hermetic universe (SKILL_CONTRACT_PATH), with
HOME pointed at an empty directory so nothing installed on this machine leaks
in. Stdlib only, offline.

Run:  cd docs/skill-contract/reference && python3 -I test_e2e.py
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
PRODUCER, CONSUMER = "spec-first-planning", "crafting-self-prompting-loops"
KIND = "https://github.com/dhanesh/agent-skills/skill-contract/task-plan/v1"
SPEC = textwrap.dedent(
    """\
    # Spec: CSV export for saved reports

    ## Problem
    Analysts re-type report numbers into spreadsheets by hand.

    ## Users
    - Data analysts exporting weekly reports

    ## Goals
    - Saved reports downloadable as CSV

    ## Non-goals
    - Excel (.xlsx) export

    ## Constraints
    - B1 [invariant]: No exported row may differ from the on-screen table.
    - T1 [boundary]: Export of a 10000-row report finishes within 5 seconds.

    ## Required truths
    - RT1 [SPECIFICATION_READY]: The CSV writer reproduces every row and column exactly. (parent: OUTCOME; maps_to: B1; reqs: R1, R2; confidence: 0.8; check: python3 tests/compare_export.py fixtures/report.json export.csv)
    - RT2 [SPECIFICATION_READY]: The export path stays within the time budget at scale. (parent: RT1; maps_to: T1; reqs: R3; confidence: 0.7; check: python3 tests/bench_export.py --rows 10000 --max-seconds 5)

    ## Requirements
    - R1: The report page must offer a "Download CSV" action for every saved report. [where: web/reports/]
    - R2: The exported CSV must contain the same rows and columns as the on-screen table, in the same order.
    - R3: Export of a 10000-row report must complete within 5 seconds.

    ## Acceptance criteria
    - R1: Open any saved report; the page shows a Download CSV control and clicking it downloads a .csv file.
    - R2: `python3 tests/compare_export.py fixtures/report.json export.csv` exits 0 (row/column parity).
    - R2: A report with zero rows exports a CSV containing only the header row.
    - R3: Timing the export endpoint with a 10000-row fixture reports under 5 seconds.

    ## Open questions
    - (none)
    """
)


def quoted_python():
    return subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)


def _now_z():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _in_one_day():
    return (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc-e2e-")
        self.universe = os.path.join(self.tmp, "skills")
        for name in (PRODUCER, CONSUMER):
            shutil.copytree(os.path.join(REPO, name), os.path.join(self.universe, name),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "eval"))
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "docs"))
        self.spec = os.path.join(self.repo, "docs", "spec.md")
        with open(self.spec, "w", encoding="utf-8", newline="\n") as f:
            f.write(SPEC)
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        self.env = dict(os.environ, SKILL_CONTRACT_PATH=self.universe, HOME=home, USERPROFILE=home,
                        SKILL_CONTRACT_PYTHON=quoted_python(), PYTHONDONTWRITEBYTECODE="1")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_py(self, script, *args):
        return subprocess.run([sys.executable, "-I", script, *args], capture_output=True,
                              text=True, timeout=120, cwd=self.repo, env=self.env)

    def produce(self):
        r = self.run_py(os.path.join(self.universe, PRODUCER, "assets", "spec_to_tasks.py"),
                        self.spec, "--envelope", self.repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        paths = [ln[len("ENVELOPE: "):] for ln in r.stdout.splitlines() if ln.startswith("ENVELOPE: ")]
        self.assertEqual(len(paths), 1, r.stdout)
        return paths[0]

    def checker(self, *args):
        r = self.run_py(os.path.join(self.universe, PRODUCER, "assets", "contract_check.py"), *args)
        try:
            return r.returncode, json.loads(r.stdout.splitlines()[0])
        except (ValueError, IndexError):
            return r.returncode, None

    def discover(self):
        rc, out = self.checker("discover", "--kind", KIND,
                               "--from", os.path.join(self.universe, PRODUCER), "--json")
        self.assertEqual(rc, 0, out)
        return out

    def test_a_producer_writes_a_valid_envelope_and_finds_the_consumer(self):
        path = self.produce()
        rc, rep = self.checker("check-envelope", path, "--root", self.repo,
                               "--for", os.path.join(self.universe, CONSUMER), "--json")
        self.assertEqual(rc, 0, rep)
        self.assertEqual(rep["stale"], [])
        self.assertEqual([c["skill"] for c in self.discover()["consumers"]], [CONSUMER])

    def test_b_no_consumer_installed_is_not_a_failure(self):
        shutil.rmtree(os.path.join(self.universe, CONSUMER))
        self.produce()
        self.assertEqual(self.discover()["consumers"], [])

    def test_c_a_consumer_of_v2_only_is_not_found(self):
        md = os.path.join(self.universe, CONSUMER, "SKILL.md")
        with open(md, encoding="utf-8") as f:
            text = f.read()
        # Swap only the task-plan/v1 entry: the consumer also consumes other
        # kinds (autonomy-grant/v1), and those must not make it a v1 consumer.
        old = '"%s"' % KIND
        self.assertEqual(text.count(old), 1)
        with open(md, "w", encoding="utf-8") as f:
            f.write(text.replace(old, '"%s2"' % KIND[:-1]))
        self.assertEqual(self.discover()["consumers"], [])

    def test_d_a_corrupt_neighbour_is_reported_and_the_consumer_still_found(self):
        broken = os.path.join(self.universe, "broken-skill")
        os.makedirs(broken)
        with open(os.path.join(broken, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write('---\nname: broken-skill\ndescription: x\nmetadata:\n  skill-contract: "1"\n'
                    '---\n\n## Contract\n\n```json skill-contract\n{"provides": [\n```\n')
        out = self.discover()
        self.assertEqual([c["skill"] for c in out["consumers"]], [CONSUMER])
        self.assertEqual([i["skill"] for i in out["invalid"]], ["broken-skill"])

    def test_e_editing_the_spec_afterwards_makes_the_envelope_stale(self):
        path = self.produce()
        with open(self.spec, "a", encoding="utf-8") as f:
            f.write("\n")
        rc, rep = self.checker("check-envelope", path, "--root", self.repo, "--json")
        self.assertEqual(rc, 0, rep)
        self.assertEqual(rep["stale"], ["docs/spec.md"])
        self.assertEqual(set(rep["claims"].values()), {"STALE"})

    def test_f_own_claims_are_claimed_until_a_rerun_proves_them(self):
        path = self.produce()
        rc, rep = self.checker("check-envelope", path, "--root", self.repo, "--json")
        self.assertEqual(rep["claims"], {"spec-lint": "CLAIMED", "coverage-total": "CLAIMED"})
        rc, rep = self.checker("check-envelope", path, "--root", self.repo, "--rerun", "--json")
        self.assertEqual(rc, 0, rep)
        self.assertEqual(rep["claims"], {"spec-lint": "PROVEN", "coverage-total": "PROVEN"})


GRANT_SPEC = """# Spec: Export

## Problem
Users cannot export rows.

## Users
- analysts

## Goals
- export works

## Non-goals
- PDF

## Constraints
- B1 [invariant]: No row is lost.
- T1 [boundary]: Export finishes within 10 s for 10000 rows.

## Required truths
- RT1 [SPECIFICATION_READY]: Every row reaches the file. (parent: OUTCOME; maps_to: B1; reqs: R1; confidence: 0.8; check: python3 -m pytest -k rows)
- RT2 [SPECIFICATION_READY]: The writer streams. (parent: RT1; maps_to: T1; reqs: R1; confidence: 0.6; check: python3 bench.py --max 10)

## Requirements
- R1: The export must include every row.

## Acceptance criteria
- R1: run `python3 -m pytest -k rows`, expect exit 0.

## Open questions

## Tensions
- TN1 [trade_off]: Streaming vs. atomic write. (between: B1, T1; status: resolved; strategy: Partition)

## Solution options
- OPT-A: Stream rows to a temp file, rename at end. (complexity: Low; reversibility: TWO_WAY; satisfies: RT1, RT2)
- OPT-B: Build in memory, then write. (complexity: Medium; reversibility: TWO_WAY; satisfies: RT1)
Recommended: OPT-A — satisfies every RT at the lowest complexity.

## Iterations
- I1: constrained, tensioned, anchored; chose OPT-A.

## Decisions
- D1: May the export add a dependency? -> no (source: sweep)
"""
ANSWERS = {"branch_pattern": "*", "gate_policy": {"read_only": "auto", "local_reversible": "grant"},
           "expires_at": _in_one_day()}


class GrantE2ETests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc-grant-e2e-")
        self.universe = os.path.join(self.tmp, "skills")
        for name in (PRODUCER, CONSUMER):
            shutil.copytree(os.path.join(REPO, name), os.path.join(self.universe, name),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "eval"))
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "docs"))
        self.spec = os.path.join(self.repo, "docs", "spec.md")
        with open(self.spec, "w", encoding="utf-8", newline="\n") as f:
            f.write(GRANT_SPEC)
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        self.env = dict(os.environ, SKILL_CONTRACT_PATH=self.universe, HOME=home, USERPROFILE=home,
                        SKILL_CONTRACT_PYTHON=quoted_python(), PYTHONDONTWRITEBYTECODE="1",
                        SKILL_CONTRACT_ALLOWED_SIGNERS="")
        self.assets = os.path.join(self.universe, PRODUCER, "assets")
        # so a real .git enclosing the OS tmp dir can never leak into the git probes
        # the git-dependent tests run directly against self.repo (contract_check's
        # own git calls already scrub this via git_env()).
        self.git_env = dict(os.environ, GIT_CEILING_DIRECTORIES=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_py(self, script, *args):
        return subprocess.run([sys.executable, "-I", os.path.join(self.assets, script), *args],
                              capture_output=True, text=True, timeout=120, cwd=self.repo, env=self.env)

    def grant(self, answers=None):
        r = self.run_py("spec_to_tasks.py", self.spec, "--envelope", self.repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        plan = [ln[len("ENVELOPE: "):] for ln in r.stdout.splitlines() if ln.startswith("ENVELOPE: ")][0]
        ans = os.path.join(self.tmp, "answers.json")
        with open(ans, "w", encoding="utf-8") as f:
            json.dump(answers or ANSWERS, f)
        r = self.run_py("write_grant.py", "--root", self.repo, "--spec", "docs/spec.md",
                        "--plan", plan, "--answers", ans, "--accepted-by", "Dana")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return [ln[len("GRANT: "):] for ln in r.stdout.splitlines() if ln.startswith("GRANT: ")][0]

    def check(self, action, *extra):
        r = self.run_py("contract_check.py", "check-grant", *extra, "--root", self.repo,
                        "--action", action)
        return r.returncode, r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""

    def test_a_grant_covers_the_handoff_class_and_not_merge(self):
        self.grant()
        self.assertEqual(self.check("local_reversible")[0], 0)
        rc, last = self.check("merge")
        self.assertEqual(rc, 3)
        self.assertIn("reason=gate-ask", last)

    def test_b_revoke_makes_the_same_check_ask(self):
        self.grant()
        r = self.run_py("contract_check.py", "revoke-grant", "--root", self.repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rc, last = self.check("local_reversible")
        self.assertEqual(rc, 3)
        self.assertIn("reason=revoked", last)

    def test_c_a_skill_attributed_copy_is_invalid(self):
        path = self.grant()
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        st["predicate"]["assertions"][0]["assertedBy"] = {"skill": "spec-first-planning"}
        st["predicate"]["id"] = st["predicate"]["id"][:-6] + "ffffff"
        forged = os.path.join(os.path.dirname(path), st["predicate"]["id"] + ".json")
        with open(forged, "w", encoding="utf-8") as f:
            json.dump(st, f)
        r = self.run_py("contract_check.py", "check-grant", forged, "--root", self.repo,
                        "--action", "local_reversible")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("GRANT: INVALID", r.stdout.strip().splitlines()[-1])
        # INVALID because of human attribution specifically, not some other C10 problem:
        # the copy keeps >= 2 distinct subjects and a valid lifetime, so the only thing
        # wrong with it is the forged assertedBy.
        self.assertIn("human", r.stdout)

    def test_d_editing_the_spec_makes_the_grant_stale(self):
        self.grant()
        with open(self.spec, "a", encoding="utf-8") as f:
            f.write("\n<!-- edited after the grant -->\n")
        rc, last = self.check("local_reversible")
        self.assertEqual(rc, 3)
        self.assertIn("reason=stale", last)

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_e_a_branch_outside_the_pattern_asks(self):
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True, env=self.git_env)
        subprocess.run(["git", "-C", self.repo, "checkout", "-q", "-b", "other/x"], check=True,
                       env=self.git_env)
        self.grant(dict(ANSWERS, branch_pattern="factory/*"))
        rc, last = self.check("local_reversible")
        self.assertEqual(rc, 3)
        self.assertIn("reason=branch", last)

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_f_the_default_branch_is_never_covered(self):
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True, env=self.git_env)
        self.grant()  # branch_pattern "*" would match main, but the A7 floor wins
        rc, last = self.check("local_reversible")
        self.assertEqual(rc, 3)
        self.assertIn("reason=default-branch", last)

    def test_g_a_grant_for_merge_is_refused(self):
        r = self.run_py("spec_to_tasks.py", self.spec, "--envelope", self.repo)
        plan = [ln[len("ENVELOPE: "):] for ln in r.stdout.splitlines() if ln.startswith("ENVELOPE: ")][0]
        ans = os.path.join(self.tmp, "answers.json")
        with open(ans, "w", encoding="utf-8") as f:
            json.dump(dict(ANSWERS, gate_policy={"local_reversible": "grant", "merge": "grant"}), f)
        r = self.run_py("write_grant.py", "--root", self.repo, "--spec", "docs/spec.md",
                        "--plan", plan, "--answers", ans, "--accepted-by", "Dana")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("REFUSED:", r.stdout)


if __name__ == "__main__":
    unittest.main()
