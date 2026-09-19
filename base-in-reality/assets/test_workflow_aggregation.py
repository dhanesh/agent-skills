# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Differential test: workflow.mjs aggregation == report_lint.refutation_downgrades.

The refutation vote is aggregated twice: once in the workflow (which decides
the verdict a run ships) and once in the linter (which checks it). The two
are written in different languages, so this suite runs the real workflow.mjs
under a node stub harness over a grid of severities x vote shapes and asserts
that every case gets the verdict the linter's rule demands. It skips when
node is not installed. Stdlib only; run: python3 test_workflow_aggregation.py
"""
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from report_lint import lint_findings, refutation_downgrades  # noqa: E402

WORKFLOW = os.path.join(HERE, "workflow.mjs")

# Runs workflow.mjs once per case with stubbed agent/pipeline/parallel. The
# refuter stub answers each lens with the case's vote: null means the refuter
# returned nothing. A severity of "__missing__" omits the field entirely.
HARNESS = r"""
import { readFileSync } from 'node:fs'
const src = readFileSync(process.argv[2], 'utf8').replace('export const meta', 'const meta')
const cases = JSON.parse(readFileSync(process.argv[3], 'utf8'))
const AsyncFn = Object.getPrototypeOf(async function () {}).constructor
const run = new AsyncFn('args', 'agent', 'phase', 'log', 'pipeline', 'parallel', src)
const out = []
for (const [severity, votes] of cases) {
  let i = 0
  const agent = async (prompt, opts) => {
    if (opts.label === 'extract') return { domains: ['x'], claims: [{ claim: 'c', layer: 'algo', location: 'a.py:1' }] }
    if (opts.label.startsWith('verify')) {
      const f = { claim: 'c', layer: 'algo', location: 'a.py:1', verdict: 'VIOLATION', citations: [{ title: 't', url: 'https://example.org/x', fetched: true }], recommended_fix: 'f' }
      if (severity !== '__missing__') f.severity = severity
      return f
    }
    const v = votes[i++]; return v === null ? null : { refuted: v, reason: 'r' }
  }
  const pipeline = async (items, f1, f2) => Promise.all(items.map(async (c) => f2(await f1(c), c)))
  const parallel = async (fns) => Promise.all(fns.map((f) => f()))
  const r = await run({}, agent, () => {}, () => {}, pipeline, parallel)
  out.push(r[0])
}
console.log(JSON.stringify(out))
"""

MISSING = "__missing__"
SEVERITIES = ["critical", "high", "medium", "low", MISSING, "bogus", "CRITICAL"]
VOTE_SHAPES = [list(v) for v in itertools.product([True, False, None], repeat=3)]


@unittest.skipIf(shutil.which("node") is None, "node not installed")
class WorkflowMatchesLinter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = [[s, v] for s in SEVERITIES for v in VOTE_SHAPES]
        with tempfile.TemporaryDirectory() as tmp:
            harness = os.path.join(tmp, "harness.mjs")
            cases = os.path.join(tmp, "cases.json")
            with open(harness, "w") as fh:
                fh.write(HARNESS)
            with open(cases, "w") as fh:
                json.dump(cls.cases, fh)
            r = subprocess.run(["node", harness, WORKFLOW, cases],
                               capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError("harness failed: " + r.stderr[-2000:])
        cls.results = json.loads(r.stdout.strip().splitlines()[-1])

    def test_grid_is_complete(self):
        self.assertEqual(len(self.results), len(SEVERITIES) * len(VOTE_SHAPES))

    def test_verdict_matches_linter_rule(self):
        for (sev, votes), got in zip(self.cases, self.results):
            severity = None if sev == MISSING else sev
            recorded = [v is not False for v in votes]  # null -> refute
            want = refutation_downgrades(severity, votes)
            with self.subTest(severity=sev, votes=votes):
                self.assertEqual(got["verdict"],
                                 "UNCONFIRMED" if want else "VIOLATION")
                self.assertEqual(got.get("downgraded") is True, want)
                # the votes the workflow records are the ones the linter reads
                ref = got["refutation"]
                self.assertEqual(ref["verdicts"], recorded)
                self.assertEqual(ref["refuters"], len(votes))
                self.assertEqual(
                    refutation_downgrades(severity, ref["verdicts"],
                                          ref["refuters"]), want)

    def test_survivors_with_valid_severity_lint_clean(self):
        for (sev, votes), got in zip(self.cases, self.results):
            if sev not in ("critical", "high", "medium", "low"):
                continue
            with self.subTest(severity=sev, votes=votes):
                self.assertEqual(lint_findings([got]), [])

    def test_unknown_severity_fails_closed_in_workflow(self):
        one_dissent = [True, False, False]
        for sev in (MISSING, "bogus", "CRITICAL"):
            got = self.results[self.cases.index([sev, one_dissent])]
            self.assertEqual(got["verdict"], "UNCONFIRMED", sev)


if __name__ == "__main__":
    unittest.main()
