#!/usr/bin/env python3
"""Tests for the skill-contract reference checker: every conformance vector,
the generator/committed-vector agreement, and the CLI. Stdlib only, offline.

Run:  cd docs/skill-contract/reference && python3 -I test_contract_check.py
"""
import filecmp
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # `python -I` drops the script dir from sys.path
import build_vectors  # noqa: E402
import contract_check as cc  # noqa: E402

VECTORS = os.path.join(os.path.dirname(HERE), "vectors")
CHECKER = os.path.join(HERE, "contract_check.py")


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def run_skill_vector(inp, tmp):
    d = os.path.join(tmp, inp["dir"])
    _write(os.path.join(d, "SKILL.md"), inp["skill_md"])
    rep = cc.check_skill(d)
    return {"result": "FAIL" if rep["violations"] else "PASS",
            "commandments": sorted({n for n, _ in rep["violations"]}),
            "warnings": len(rep["warnings"]), "adopter": rep["adopter"]}


RUNNERS = {"skill": run_skill_vector}


def run_vector(vector):
    with tempfile.TemporaryDirectory() as tmp:
        return RUNNERS[vector["input"]["type"]](vector["input"], tmp)


def all_vector_files():
    for dirpath, _dirs, files in os.walk(VECTORS):
        for f in sorted(files):
            if f.endswith(".json"):
                yield os.path.join(dirpath, f)


class VectorTests(unittest.TestCase):
    def test_committed_vectors_match_the_generator(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "vectors")
            build_vectors.write_all(out)
            cmp = filecmp.dircmp(out, VECTORS)
            stack, diffs = [cmp], []
            while stack:
                c = stack.pop()
                diffs += [os.path.join(c.left, x) for x in c.left_only + c.right_only + c.diff_files]
                stack += list(c.subdirs.values())
            self.assertEqual(diffs, [], "run: python3 build_vectors.py  (vectors are stale)")

    def test_every_vector(self):
        files = list(all_vector_files())
        self.assertTrue(files, "no vectors found under %s" % VECTORS)
        for path in files:
            with open(path, encoding="utf-8") as f:
                vector = json.load(f)
            if vector.get("posix_only") and os.name == "nt":
                continue
            with self.subTest(vector=os.path.relpath(path, VECTORS)):
                actual = run_vector(vector)
                for key, want in vector["expect"].items():
                    self.assertEqual(actual.get(key), want, key)

    def test_every_commandment_has_valid_and_invalid_vectors(self):
        present = {os.path.relpath(os.path.dirname(p), VECTORS).replace(os.sep, "/")
                   for p in all_vector_files()}
        for c in self.required_commandments():
            for validity in ("valid", "invalid"):
                self.assertIn("%s/%s" % (c, validity), present)

    @staticmethod
    def required_commandments():
        return ["c1", "c2"]


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-I", CHECKER, *args],
                              capture_output=True, text=True, timeout=60)

    def test_check_skill_pass_prints_result_line_and_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "alpha")
            _write(os.path.join(d, "SKILL.md"), build_vectors.skill_md())
            r = self.run_cli("check-skill", d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("ADOPTER: no", r.stdout)
            self.assertEqual(r.stdout.strip().splitlines()[-1], "CONTRACT_RESULT: PASS")

    def test_check_skill_fail_names_commandments_and_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "alpha")
            _write(os.path.join(d, "SKILL.md"), build_vectors.skill_md(
                optin="1", body=build_vectors.contract_section(
                    build_vectors.block(["http://x/k/v1", "http://x/k/v1"]))))
            r = self.run_cli("check-skill", d)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(r.stdout.strip().splitlines()[-1], "CONTRACT_RESULT: FAIL (C2)")

    def test_usage_error_exits_1(self):
        self.assertEqual(self.run_cli().returncode, 1)
        self.assertEqual(self.run_cli("no-such-command").returncode, 1)


if __name__ == "__main__":
    unittest.main()
