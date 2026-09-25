#!/usr/bin/env python3
"""Unit tests for spec_to_tasks.py — stdlib-only, offline, deterministic.

Run standalone:  cd spec-first-planning/assets && python3 test_spec_to_tasks.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec_to_tasks  # noqa: E402
import contract_check  # noqa: E402
from test_spec_lint import FULL  # noqa: E402

GOOD = textwrap.dedent(
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

# Doctored spec: R2 has no acceptance criterion, so no task is derivable.
UNCOVERED = GOOD.replace(
    "- R2: `python3 tests/compare_export.py fixtures/report.json export.csv` "
    "exits 0 (row/column parity).\n"
    "- R2: A report with zero rows exports a CSV containing only the header row.\n",
    "",
)

# R1 -> T1 (no deps); R2, R3 both [after: R1] -> T2, T3; R4 [after: R2, R3] -> T4.
# A diamond: wave 1 = [T1], wave 2 = [T2, T3], wave 3 = [T4].
DIAMOND = textwrap.dedent(
    """\
    # Spec: diamond

    ## Requirements
    - R1: The base step must run first.
    - R2: The second step must run after the base step. [after: R1]
    - R3: The third step must run after the base step. [after: R1]
    - R4: The final step must run after both prior steps. [after: R2, R3]

    ## Acceptance criteria
    - R1: run `true`, expect exit 0.
    - R2: run `true`, expect exit 0.
    - R3: run `true`, expect exit 0.
    - R4: run `true`, expect exit 0.
    """
)


class TestDerivePlan(unittest.TestCase):
    def test_full_coverage(self):
        plan = spec_to_tasks.derive_plan(GOOD)
        self.assertEqual(plan["uncovered"], [])
        self.assertEqual(
            plan["coverage"], {"R1": ["T1"], "R2": ["T2"], "R3": ["T3"]}
        )
        self.assertEqual(len(plan["tasks"]), 3)

    def test_every_task_has_verify(self):
        plan = spec_to_tasks.derive_plan(GOOD)
        for task in plan["tasks"]:
            self.assertTrue(task["verify"].strip(), "task %s lacks verify" % task["id"])

    def test_multiple_criteria_join_into_verify(self):
        plan = spec_to_tasks.derive_plan(GOOD)
        t2 = plan["tasks"][1]
        self.assertEqual(t2["requirement_ids"], ["R2"])
        self.assertIn("compare_export.py", t2["verify"])
        self.assertIn("only the header row", t2["verify"])
        self.assertEqual(len(t2["_verify_steps"]), 2)

    def test_where_hint_lifted_out_of_title(self):
        plan = spec_to_tasks.derive_plan(GOOD)
        t1 = plan["tasks"][0]
        self.assertEqual(t1["_where"], "web/reports/")
        self.assertNotIn("[where:", t1["title"])
        self.assertIn("Download CSV", t1["title"])

    def test_verify_step_strips_rid_prefix(self):
        plan = spec_to_tasks.derive_plan(GOOD)
        self.assertFalse(plan["tasks"][0]["verify"].startswith("R1"))

    def test_uncovered_requirement_reported(self):
        plan = spec_to_tasks.derive_plan(UNCOVERED)
        self.assertEqual(plan["uncovered"], ["R2"])
        self.assertEqual(plan["coverage"]["R2"], [])
        # Remaining requirements still get deterministic task ids.
        self.assertEqual(plan["coverage"]["R1"], ["T1"])
        self.assertEqual(plan["coverage"]["R3"], ["T2"])

    def test_deterministic_output(self):
        a = json.dumps(spec_to_tasks.to_json(spec_to_tasks.derive_plan(GOOD)))
        b = json.dumps(spec_to_tasks.to_json(spec_to_tasks.derive_plan(GOOD)))
        self.assertEqual(a, b)

    def test_json_schema_keys(self):
        out = spec_to_tasks.to_json(spec_to_tasks.derive_plan(GOOD))
        self.assertEqual(sorted(out.keys()), ["coverage", "tasks", "uncovered"])
        for task in out["tasks"]:
            for key in ("id", "requirement_ids", "title", "verify"):
                self.assertIn(key, task)
            self.assertFalse(any(k.startswith("_") for k in task))


class TestMarkdown(unittest.TestCase):
    def test_markdown_names_verify_and_coverage(self):
        plan = spec_to_tasks.derive_plan(GOOD)
        md = spec_to_tasks.render_markdown(plan, "spec.md")
        self.assertIn("## T1 —", md)
        self.assertIn("- Verify:", md)
        self.assertIn("| R3 | T3 |", md)
        self.assertIn("- Where: web/reports/", md)

    def test_markdown_marks_uncovered(self):
        plan = spec_to_tasks.derive_plan(UNCOVERED)
        md = spec_to_tasks.render_markdown(plan, "spec.md")
        self.assertIn("NONE — UNCOVERED", md)
        self.assertIn("## Uncovered requirements", md)


class TestCli(unittest.TestCase):
    def _run(self, content, *flags):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "spec.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            script = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "spec_to_tasks.py"
            )
            return subprocess.run(
                [sys.executable, script, path, *flags],
                capture_output=True,
                text=True,
                timeout=30,
            )

    def test_cli_pass_markdown(self):
        r = self._run(GOOD)
        self.assertEqual(r.returncode, 0)
        self.assertIn("COVERAGE: R1 -> T1", r.stdout)
        self.assertIn("TASKS_RESULT: PASS (3/3 requirements", r.stdout)

    def test_cli_json_is_parseable(self):
        r = self._run(GOOD, "--json")
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertEqual(out["uncovered"], [])

    def test_cli_uncovered_exits_nonzero(self):
        r = self._run(UNCOVERED)
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNCOVERED: R2", r.stdout)
        self.assertIn("TASKS_RESULT: FAIL", r.stdout)

    def test_cli_no_requirements_exits_2(self):
        r = self._run("# Spec: empty\n\n## Problem\nSomething.\n")
        self.assertEqual(r.returncode, 2)

    def test_plain_and_json_runs_never_import_contract_check(self):
        # contract_check exits 2 below Python 3.10, so importing it on a path that
        # has nothing to do with envelopes broke `spec_to_tasks.py spec.md` on
        # macOS's /usr/bin/python3 (3.9). Only --envelope may load it.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "spec.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(GOOD)
            probe = textwrap.dedent("""
                import contextlib, io, sys
                sys.path.insert(0, sys.argv[1])
                import spec_to_tasks
                assert "contract_check" not in sys.modules, "loaded on import"
                spec_to_tasks.to_json(spec_to_tasks.derive_plan(open(sys.argv[2], encoding="utf-8").read()))
                for flags in ([], ["--json"]):
                    with contextlib.redirect_stdout(io.StringIO()):
                        rc = spec_to_tasks.main(["spec_to_tasks.py", sys.argv[2]] + flags)
                    assert rc == 0, (flags, rc)
                    assert "contract_check" not in sys.modules, "loaded by main %r" % flags
                print("NOT_IMPORTED")
            """)
            here = os.path.dirname(os.path.abspath(__file__))
            r = subprocess.run([sys.executable, "-I", "-c", probe, here, path],
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "NOT_IMPORTED")



class TestCoverageOwnership(unittest.TestCase):
    """An `R<n>` token anywhere in a criterion used to count as coverage."""

    SPEC = """# Spec: importer

## Requirements
- R1: The importer must reject a row with a missing id.
- R2: The importer must emit a summary count.

## Acceptance criteria
- R1: run `grep R2 fixtures.txt` and see the row rejected (this also proves R2).
"""

    def test_incidental_mention_is_not_coverage(self):
        plan = spec_to_tasks.derive_plan(self.SPEC)
        self.assertEqual(plan["coverage"]["R2"], [])
        self.assertIn("R2", plan["uncovered"])

    def test_verify_step_is_not_duplicated(self):
        plan = spec_to_tasks.derive_plan(self.SPEC)
        t1 = [t for t in plan["tasks"] if t["id"] == "T1"][0]
        self.assertEqual(t1["verify"].count("grep R2 fixtures.txt"), 1)

    def test_foreign_prefix_is_stripped_from_the_step(self):
        plan = spec_to_tasks.derive_plan(self.SPEC)
        t1 = [t for t in plan["tasks"] if t["id"] == "T1"][0]
        self.assertFalse(t1["verify"].startswith("R1:"))

    def test_prefixless_criterion_still_covers_its_mentions(self):
        spec = """# Spec: legacy

## Requirements
- R1: The thing must happen.

## Acceptance criteria
- Covers R1 by running the check.
"""
        plan = spec_to_tasks.derive_plan(spec)
        self.assertEqual(plan["coverage"]["R1"], ["T1"])
        self.assertEqual(plan["uncovered"], [])

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(_HERE, "spec_to_tasks.py")


class TestEnvelope(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "docs"))
        self.spec = os.path.join(self.root, "docs", "spec.md")
        with open(self.spec, "w", encoding="utf-8") as f:
            f.write(GOOD)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def cli(self, *args):
        return subprocess.run([sys.executable, _SCRIPT, *args],
                              capture_output=True, text=True, timeout=60)

    def test_envelope_is_valid_and_its_claims_are_the_producers_own(self):
        path = spec_to_tasks.write_task_plan_envelope(spec_to_tasks.derive_plan(GOOD), self.spec, self.root)
        rep = contract_check.check_envelope(path, root=self.root)
        self.assertEqual(rep["violations"], [])
        self.assertEqual(rep["stale"], [])
        self.assertEqual(rep["claims"], {"spec-lint": "CLAIMED", "coverage-total": "CLAIMED"})

    def test_payload_keeps_each_verify_step_as_its_own_item(self):
        payload = spec_to_tasks.to_task_plan_payload(spec_to_tasks.derive_plan(GOOD), "docs/spec.md")
        r2 = [t for t in payload["tasks"] if t["requirement_ids"] == ["R2"]][0]
        self.assertEqual(len(r2["verify"]), 2)
        self.assertTrue(all(v["command"] is None for v in r2["verify"]))
        self.assertEqual(payload["tasks"][0]["where"], "web/reports/")
        self.assertEqual(spec_to_tasks.payload_errors(payload), [])

    def test_payload_errors_flags_a_joined_verify_string(self):
        bad = {"title": "x", "spec": "s", "coverage": {}, "uncovered": [],
               "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "t", "verify": "a; b"}]}
        self.assertTrue(spec_to_tasks.payload_errors(bad))

    def test_json_output_is_unchanged(self):
        out = spec_to_tasks.to_json(spec_to_tasks.derive_plan(GOOD))
        self.assertEqual(sorted(out), ["coverage", "tasks", "uncovered"])
        self.assertIsInstance(out["tasks"][0]["verify"], str)

    def test_a_spec_outside_the_root_is_refused(self):
        other = tempfile.mkdtemp()
        try:
            with self.assertRaises(ValueError):
                spec_to_tasks.write_task_plan_envelope(spec_to_tasks.derive_plan(GOOD), self.spec, other)
        finally:
            shutil.rmtree(other, ignore_errors=True)

    def test_cli_writes_one_envelope(self):
        r = self.cli(self.spec, "--envelope", self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        paths = [ln[len("ENVELOPE: "):] for ln in r.stdout.splitlines() if ln.startswith("ENVELOPE: ")]
        self.assertEqual(len(paths), 1)
        self.assertTrue(os.path.isfile(paths[0]))

    def test_cli_json_keeps_stdout_pure_json(self):
        r = self.cli(self.spec, "--json", "--envelope", self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        json.loads(r.stdout)
        self.assertIn("ENVELOPE: ", r.stderr)

    def test_cli_writes_no_envelope_for_an_uncovered_plan(self):
        with open(self.spec, "w", encoding="utf-8") as f:
            f.write(UNCOVERED)
        r = self.cli(self.spec, "--envelope", self.root)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.isdir(os.path.join(self.root, ".skill-contract")))

    def test_skill_version_matches_skill_md(self):
        with open(os.path.join(_HERE, "..", "SKILL.md"), encoding="utf-8") as f:
            self.assertIn('version: "%s"' % spec_to_tasks.SKILL_VERSION, f.read())

    def test_skill_version_is_2_2_1(self):
        self.assertEqual(spec_to_tasks.SKILL_VERSION, "2.2.1")


# C1: GOOD with a [cmd: ...] hint on R1's criterion and on one of R2's two.
WITH_CMDS = GOOD.replace(
    "clicking it downloads a .csv file.",
    "clicking it downloads a .csv file. [cmd: {python} -m pytest -k download]").replace(
    "exits 0 (row/column parity).",
    'exits 0 (row/column parity). [cmd: {python} tests/compare_export.py "fixtures/report.json" export.csv]')


class TestCmdHint(unittest.TestCase):
    def payload(self, text=WITH_CMDS):
        return spec_to_tasks.to_task_plan_payload(spec_to_tasks.derive_plan(text), "docs/spec.md")

    def test_the_hint_becomes_the_command_and_is_stripped_from_the_text(self):
        t1, t2, _ = self.payload()["tasks"]
        self.assertEqual(t1["verify"], [{
            "text": "Open any saved report; the page shows a Download CSV control and "
                    "clicking it downloads a .csv file.",
            "command": ["{python}", "-m", "pytest", "-k", "download"]}])
        self.assertEqual(t2["verify"][0]["command"],
                         ["{python}", "tests/compare_export.py", "fixtures/report.json",
                          "export.csv"])
        self.assertNotIn("[cmd:", t2["verify"][0]["text"])

    def test_a_criterion_without_a_hint_still_gives_a_null_command(self):
        _, t2, t3 = self.payload()["tasks"]
        self.assertIsNone(t2["verify"][1]["command"])
        self.assertIsNone(t3["verify"][0]["command"])
        self.assertTrue(all(v["command"] is None for t in self.payload(GOOD)["tasks"]
                            for v in t["verify"]))

    def test_json_shows_the_commands_and_stays_unchanged_without_hints(self):
        out = spec_to_tasks.to_json(spec_to_tasks.derive_plan(WITH_CMDS))
        self.assertEqual(out["tasks"][0]["verify_commands"],
                         [["{python}", "-m", "pytest", "-k", "download"]])
        self.assertEqual(out["tasks"][1]["verify_commands"][1], None)
        self.assertNotIn("[cmd:", out["tasks"][0]["verify"])
        self.assertNotIn("verify_commands", out["tasks"][2])
        plain = spec_to_tasks.to_json(spec_to_tasks.derive_plan(GOOD))
        self.assertTrue(all("verify_commands" not in t for t in plain["tasks"]))

    def test_markdown_shows_the_command(self):
        md = spec_to_tasks.render_markdown(spec_to_tasks.derive_plan(WITH_CMDS), "spec.md")
        self.assertIn("clicking it downloads a .csv file. "
                      "(cmd: `{python} -m pytest -k download`)", md)
        self.assertNotIn("[cmd:", md)

    def test_an_envelope_built_from_a_spec_with_commands_passes_check_envelope(self):
        root = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(root, "docs"))
            spec = os.path.join(root, "docs", "spec.md")
            with open(spec, "w", encoding="utf-8") as f:
                f.write(WITH_CMDS)
            r = subprocess.run([sys.executable, _SCRIPT, spec, "--envelope", root],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            path = [ln[len("ENVELOPE: "):] for ln in r.stdout.splitlines()
                    if ln.startswith("ENVELOPE: ")][0]
            chk = subprocess.run([sys.executable, os.path.join(_HERE, "contract_check.py"),
                                  "check-envelope", path, "--root", root],
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(chk.returncode, 0, chk.stdout + chk.stderr)
            with open(path, encoding="utf-8") as f:
                tasks = json.load(f)["predicate"]["payload"]["tasks"]
            self.assertEqual(tasks[0]["verify"][0]["command"],
                             ["{python}", "-m", "pytest", "-k", "download"])
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestOptionalPayloadFields(unittest.TestCase):
    def test_full_spec_payload_carries_constraints_truths_decisions(self):
        payload = spec_to_tasks.to_task_plan_payload(spec_to_tasks.derive_plan(FULL), "docs/spec.md")
        self.assertEqual(len(payload["constraints"]), 2)
        self.assertEqual(len(payload["required_truths"]), 2)
        self.assertEqual(len(payload["decisions"]), 1)
        self.assertEqual(payload["decisions"][0]["id"], "D1")
        self.assertEqual(spec_to_tasks.payload_errors(payload), [])

    def test_spec_without_decisions_has_no_decisions_key(self):
        payload = spec_to_tasks.to_task_plan_payload(spec_to_tasks.derive_plan(GOOD), "docs/spec.md")
        self.assertNotIn("decisions", payload)
        self.assertEqual(len(payload["constraints"]), 2)
        self.assertEqual(len(payload["required_truths"]), 2)
        self.assertEqual(spec_to_tasks.payload_errors(payload), [])

    def test_required_truths_drop_the_num_field(self):
        payload = spec_to_tasks.to_task_plan_payload(spec_to_tasks.derive_plan(FULL), "docs/spec.md")
        for t in payload["required_truths"]:
            self.assertNotIn("num", t)
            self.assertIn("id", t)
            self.assertIn("status", t)
            self.assertIn("check", t)


class TestRequiredTruthsWellFormed(unittest.TestCase):
    """Fix round 1, item 3: a malformed confidence must not leak into the payload — or into
    its JSON (a bare NaN is not valid JSON)."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "docs"))
        self.spec = os.path.join(self.root, "docs", "spec.md")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, text):
        with open(self.spec, "w", encoding="utf-8") as f:
            f.write(text)

    def test_unparsable_confidence_omits_required_truths(self):
        bad = GOOD.replace("confidence: 0.8", "confidence: high")
        payload = spec_to_tasks.to_task_plan_payload(spec_to_tasks.derive_plan(bad), "docs/spec.md")
        self.assertNotIn("required_truths", payload)
        self.assertEqual(spec_to_tasks.payload_errors(payload), [])

    def test_unparsable_confidence_still_writes_an_envelope_with_a_failed_spec_lint_claim(self):
        bad = GOOD.replace("confidence: 0.8", "confidence: high")
        self._write(bad)
        plan = spec_to_tasks.derive_plan(bad)
        path = spec_to_tasks.write_task_plan_envelope(plan, self.spec, self.root)
        rep = contract_check.check_envelope(path, root=self.root)
        self.assertEqual(rep["violations"], [])
        self.assertEqual(rep["claims"]["spec-lint"], "FAILED")

    def test_nan_confidence_omits_required_truths(self):
        bad = GOOD.replace("confidence: 0.8", "confidence: nan")
        payload = spec_to_tasks.to_task_plan_payload(spec_to_tasks.derive_plan(bad), "docs/spec.md")
        self.assertNotIn("required_truths", payload)

    def test_nan_confidence_produces_no_nan_in_json(self):
        bad = GOOD.replace("confidence: 0.8", "confidence: nan")
        payload = spec_to_tasks.to_task_plan_payload(spec_to_tasks.derive_plan(bad), "docs/spec.md")
        self.assertNotIn("NaN", json.dumps(payload))

    def test_out_of_range_confidence_omits_required_truths(self):
        bad = GOOD.replace("confidence: 0.8", "confidence: 1.5")
        payload = spec_to_tasks.to_task_plan_payload(spec_to_tasks.derive_plan(bad), "docs/spec.md")
        self.assertNotIn("required_truths", payload)

    def test_well_formed_truths_are_unaffected(self):
        payload = spec_to_tasks.to_task_plan_payload(spec_to_tasks.derive_plan(GOOD), "docs/spec.md")
        self.assertEqual(len(payload["required_truths"]), 2)

    def _truth(self, **overrides):
        t = {"id": "RT1", "status": "SATISFIED", "text": "t", "parent": "OUTCOME",
             "maps_to": [], "reqs": [], "confidence": 0.5, "check": "true"}
        t.update(overrides)
        return {"title": "x", "spec": "s", "coverage": {}, "uncovered": [], "tasks": [],
               "required_truths": [t]}

    def test_payload_errors_rejects_nan_confidence(self):
        self.assertTrue(spec_to_tasks.payload_errors(self._truth(confidence=float("nan"))))

    def test_payload_errors_rejects_infinite_confidence(self):
        self.assertTrue(spec_to_tasks.payload_errors(self._truth(confidence=float("inf"))))

    def test_payload_errors_rejects_out_of_range_confidence(self):
        self.assertTrue(spec_to_tasks.payload_errors(self._truth(confidence=1.5)))
        self.assertTrue(spec_to_tasks.payload_errors(self._truth(confidence=-0.1)))

    def test_payload_errors_accepts_a_well_formed_truth(self):
        self.assertEqual(spec_to_tasks.payload_errors(self._truth()), [])


class TestDependsOn(unittest.TestCase):
    def test_after_hint_becomes_depends_on_mapped_to_covering_task(self):
        plan = spec_to_tasks.derive_plan(DIAMOND)
        t2 = [t for t in plan["tasks"] if t["id"] == "T2"][0]
        t4 = [t for t in plan["tasks"] if t["id"] == "T4"][0]
        self.assertEqual(t2["depends_on"], ["T1"])
        self.assertEqual(t4["depends_on"], ["T2", "T3"])

    def test_after_hint_stripped_from_title(self):
        plan = spec_to_tasks.derive_plan(DIAMOND)
        t2 = [t for t in plan["tasks"] if t["id"] == "T2"][0]
        self.assertNotIn("[after:", t2["title"])
        self.assertIn("second step", t2["title"])

    def test_no_hints_omits_depends_on(self):
        plan = spec_to_tasks.derive_plan(GOOD)
        for t in plan["tasks"]:
            self.assertNotIn("depends_on", t)

    def test_existing_fixture_json_is_byte_identical(self):
        # depends_on is omitted whenever a requirement carries no [after:] hint,
        # which keeps a plan derived from a pre-existing spec byte-identical.
        out = spec_to_tasks.to_json(spec_to_tasks.derive_plan(GOOD))
        self.assertEqual(
            sorted(out["tasks"][0].keys()),
            ["id", "requirement_ids", "title", "verify", "where"],
        )

    def test_json_output_carries_depends_on(self):
        out = spec_to_tasks.to_json(spec_to_tasks.derive_plan(DIAMOND))
        t2 = [t for t in out["tasks"] if t["id"] == "T2"][0]
        self.assertEqual(t2["depends_on"], ["T1"])

    def test_payload_carries_depends_on_and_is_valid(self):
        payload = spec_to_tasks.to_task_plan_payload(spec_to_tasks.derive_plan(DIAMOND), "docs/spec.md")
        t4 = [t for t in payload["tasks"] if t["id"] == "T4"][0]
        self.assertEqual(t4["depends_on"], ["T2", "T3"])
        self.assertEqual(spec_to_tasks.payload_errors(payload), [])

    def test_payload_errors_flags_bad_depends_on(self):
        bad = {"title": "x", "spec": "s", "coverage": {}, "uncovered": [],
               "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "t",
                          "verify": [{"text": "x", "command": None}], "depends_on": "T0"}]}
        self.assertTrue(spec_to_tasks.payload_errors(bad))

    def test_after_hint_keyword_is_case_insensitive_and_stripped_from_title(self):
        spec = textwrap.dedent(
            """\
            # Spec: shout

            ## Requirements
            - R1: The base step must run first.
            - R2: The second step must run after the base step. [AFTER: R1]

            ## Acceptance criteria
            - R1: run `true`, expect exit 0.
            - R2: run `true`, expect exit 0.
            """
        )
        plan = spec_to_tasks.derive_plan(spec)
        t2 = [t for t in plan["tasks"] if t["requirement_ids"] == ["R2"]][0]
        self.assertEqual(t2["depends_on"], ["T1"])
        self.assertNotIn("[AFTER:", t2["title"])
        self.assertIn("second step", t2["title"])

    def test_after_hint_referencing_an_uncovered_requirement_has_no_depends_on(self):
        # R1 has no acceptance criterion, so it derives no task at all; R2's
        # [after: R1] hint then resolves to zero covering tasks.
        spec = textwrap.dedent(
            """\
            # Spec: gap

            ## Requirements
            - R1: The upstream step must happen.
            - R2: The downstream step must happen after the upstream step. [after: R1]

            ## Acceptance criteria
            - R2: run `true`, expect exit 0.
            """
        )
        plan = spec_to_tasks.derive_plan(spec)
        self.assertEqual(plan["uncovered"], ["R1"])
        t = [t for t in plan["tasks"] if t["requirement_ids"] == ["R2"]][0]
        self.assertNotIn("depends_on", t)


class TestWaves(unittest.TestCase):
    def test_diamond_gives_three_waves(self):
        plan = spec_to_tasks.derive_plan(DIAMOND)
        tasks_by_id = {t["id"]: {"depends_on": t.get("depends_on") or []} for t in plan["tasks"]}
        self.assertEqual(spec_to_tasks.waves(tasks_by_id), [["T1"], ["T2", "T3"], ["T4"]])

    def test_no_hints_gives_one_wave(self):
        plan = spec_to_tasks.derive_plan(GOOD)
        tasks_by_id = {t["id"]: {"depends_on": t.get("depends_on") or []} for t in plan["tasks"]}
        self.assertEqual(spec_to_tasks.waves(tasks_by_id), [["T1", "T2", "T3"]])

    def test_waves_orders_ids_numerically_not_lexicographically(self):
        tasks = {"T2": {"depends_on": []}, "T10": {"depends_on": []}}
        self.assertEqual(spec_to_tasks.waves(tasks), [["T2", "T10"]])

    def test_waves_raises_planerror_on_cycle(self):
        tasks = {"T1": {"depends_on": ["T2"]}, "T2": {"depends_on": ["T1"]}}
        with self.assertRaises(spec_to_tasks.PlanError):
            spec_to_tasks.waves(tasks)

    def test_critical_path_diamond(self):
        plan = spec_to_tasks.derive_plan(DIAMOND)
        tasks_by_id = {t["id"]: {"depends_on": t.get("depends_on") or []} for t in plan["tasks"]}
        self.assertEqual(spec_to_tasks.critical_path(tasks_by_id), ["T1", "T2", "T4"])

    def test_critical_path_with_no_dependencies_is_a_single_task(self):
        plan = spec_to_tasks.derive_plan(GOOD)
        tasks_by_id = {t["id"]: {"depends_on": t.get("depends_on") or []} for t in plan["tasks"]}
        self.assertEqual(spec_to_tasks.critical_path(tasks_by_id), ["T1"])


class TestWavesCli(unittest.TestCase):
    def _run(self, content, *flags):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "spec.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spec_to_tasks.py")
            return subprocess.run([sys.executable, script, path, *flags],
                                  capture_output=True, text=True, timeout=30)

    def test_waves_prints_lines_and_exits_0(self):
        r = self._run(DIAMOND, "--waves")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("WAVE 1: T1", r.stdout)
        self.assertIn("WAVE 2: T2 T3", r.stdout)
        self.assertIn("WAVE 3: T4", r.stdout)
        self.assertIn("CRITICAL_PATH: T1 -> T2 -> T4", r.stdout)
        self.assertIn("WAVES_RESULT: PASS (3 wave(s))", r.stdout)

    def test_waves_cycle_exits_nonzero(self):
        # R2 after R1 AND R4, while R4 is after R2 and R3: T2 <-> T4 is a real
        # depends_on cycle (spec_lint's own [after:] cycle rule would also
        # reject this spec at lint time, but --waves never calls the linter).
        cyclic = DIAMOND.replace("[after: R1]", "[after: R1, R4]", 1)
        r = self._run(cyclic, "--waves")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ERROR:", r.stderr)


if __name__ == "__main__":
    unittest.main()
