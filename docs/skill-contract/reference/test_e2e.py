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


if __name__ == "__main__":
    unittest.main()
