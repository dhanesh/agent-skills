#!/usr/bin/env python3
"""Tests for the skill-contract reference checker: every conformance vector,
the generator/committed-vector agreement, and the CLI. Stdlib only, offline.

Run:  cd docs/skill-contract/reference && python3 -I test_contract_check.py
"""
import filecmp
import json
import os
import shlex
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


def run_envelope_vector(inp, tmp):
    path = os.path.join(tmp, "envelope.json")
    _write(path, json.dumps(inp["envelope"]))
    root = None
    if "files" in inp:
        root = os.path.join(tmp, "root")
        os.makedirs(root, exist_ok=True)
        for rel, text in inp["files"].items():
            _write(os.path.join(root, *rel.split("/")), text)
    for_skill = None
    if "for_skill" in inp:
        for_skill = os.path.join(tmp, inp["for_skill"]["dir"])
        _write(os.path.join(for_skill, "SKILL.md"), inp["for_skill"]["skill_md"])
    rep = cc.check_envelope(path, root=root, for_skill=for_skill)
    return {"result": "FAIL" if rep["violations"] else "PASS",
            "commandments": sorted({n for n, _ in rep["violations"]}),
            "stale": rep["stale"], "claims": rep["claims"]}


ROOT_DIRS = {"path": ("path",), "sibling": ("sibling",),
             "project": ("cwd", ".agents", "skills"), "user": ("home", ".agents", "skills")}


def run_discovery_vector(inp, tmp):
    base = {k: os.path.join(tmp, *v) for k, v in ROOT_DIRS.items()}
    home, cwd, plugins_dir = (os.path.join(tmp, "home"), os.path.join(tmp, "cwd"),
                              os.path.join(tmp, "plugins"))
    os.makedirs(home, exist_ok=True)
    os.makedirs(cwd, exist_ok=True)
    for label, skills in inp["roots"].items():
        for name, md in skills.items():
            _write(os.path.join(base[label], name, "SKILL.md"), md)
    for link in inp.get("links", []):
        os.makedirs(base[link["root"]], exist_ok=True)
        os.symlink(os.path.join(base[link["to_root"]], link["name"]),
                   os.path.join(base[link["root"]], link["name"]))
    for plugin, skills in inp.get("plugin_skills", {}).items():
        for name, md in skills.items():
            _write(os.path.join(plugins_dir, plugin, "skills", name, "SKILL.md"), md)
    if "plugins" in inp:
        index = json.loads(json.dumps(inp["plugins"]))
        for entries in (index.get("plugins") or {}).values():
            for e in entries if isinstance(entries, list) else [entries]:
                if isinstance(e.get("installPath"), str):
                    e["installPath"] = e["installPath"].replace("{plugins}", plugins_dir)
        _write(os.path.join(home, ".claude", "plugins", "installed_plugins.json"), json.dumps(index))
    env = {"SKILL_CONTRACT_PATH": base["path"] if "path" in inp["roots"] else ""}
    rep = cc.discover(inp["kind"], env=env, from_dir=os.path.join(base["sibling"], inp["from"]),
                      cwd=cwd, home=home)
    return {"consumers": [c["skill"] for c in rep["consumers"]],
            "shadowed": sorted(s["skill"] for s in rep["shadowed"]),
            "invalid": sorted(i["skill"] for i in rep["invalid"]),
            "warnings": rep["warnings"]}


RUNNERS = {"skill": run_skill_vector, "envelope": run_envelope_vector,
           "discovery": run_discovery_vector}


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
                    if key == "warnings_contain":
                        for s in want:
                            self.assertTrue(any(s in w for w in actual["warnings"]),
                                            "no warning contains %r: %r" % (s, actual["warnings"]))
                    else:
                        self.assertEqual(actual.get(key), want, key)

    def test_every_commandment_has_valid_and_invalid_vectors(self):
        present = {os.path.relpath(os.path.dirname(p), VECTORS).replace(os.sep, "/")
                   for p in all_vector_files()}
        for c in self.required_commandments():
            for validity in ("valid", "invalid"):
                self.assertIn("%s/%s" % (c, validity), present)

    @staticmethod
    def required_commandments():
        return ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9"]


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

    def test_discover_json_lists_consumers_and_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            universe, home = os.path.join(tmp, "skills"), os.path.join(tmp, "home")
            _write(os.path.join(universe, "alpha", "SKILL.md"), build_vectors.producer_md())
            _write(os.path.join(universe, "beta", "SKILL.md"), build_vectors.consumer_md())
            env = dict(os.environ, SKILL_CONTRACT_PATH=universe, HOME=home, USERPROFILE=home)
            r = subprocess.run([sys.executable, "-I", CHECKER, "discover", "--kind", build_vectors.KIND,
                                "--from", os.path.join(universe, "alpha"), "--json"],
                               capture_output=True, text=True, timeout=60, env=env, cwd=tmp)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = json.loads(r.stdout.splitlines()[0])
            self.assertEqual([c["skill"] for c in out["consumers"]], ["beta"])
            self.assertEqual(r.stdout.strip().splitlines()[-1], "CONTRACT_RESULT: PASS")

    def test_discover_rejects_a_bad_kind_with_c2(self):
        r = self.run_cli("discover", "--kind", "http://x/k/v1")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "CONTRACT_RESULT: FAIL (C2)")

    def test_usage_error_exits_1(self):
        self.assertEqual(self.run_cli().returncode, 1)
        self.assertEqual(self.run_cli("no-such-command").returncode, 1)


class HelperTests(unittest.TestCase):
    KIND = build_vectors.KIND

    def repo(self, tmp):
        _write(os.path.join(tmp, "docs", "spec.md"), build_vectors.SPEC)
        return tmp

    def test_new_id_matches_the_grammar_and_the_kind(self):
        eid = cc.new_id(self.KIND)
        m = cc.ID_RE.match(eid)
        self.assertIsNotNone(m, eid)
        self.assertEqual((m.group("name"), int(m.group("ver"))), ("task-plan", 1))

    def test_build_statement_is_valid_and_pins_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.repo(tmp)
            a = cc.assertion("spec-lint", "alpha", "passed", root, ["docs/spec.md"],
                             command=["{python}", "{skill_dir:alpha}/assets/lint.py", "docs/spec.md"])
            st = cc.build_statement(self.KIND, "alpha", "1.0.0", root, ["docs/spec.md"],
                                    {"title": "x"}, [a])
            self.assertEqual(cc.check_statement(st), [])
            self.assertEqual(st["subject"][0]["digest"]["sha256"], build_vectors.sha(build_vectors.SPEC))

    def test_write_envelope_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.repo(tmp)
            st = cc.build_statement(self.KIND, "alpha", "1.0.0", root, ["docs/spec.md"], {})
            path = cc.write_envelope(root, st)
            self.assertTrue(path.endswith(os.path.join(".skill-contract", "envelopes",
                                                       st["predicate"]["id"] + ".json")))
            with self.assertRaises(FileExistsError):
                cc.write_envelope(root, st)

    def test_write_envelope_refuses_an_unsafe_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.repo(tmp)
            st = cc.build_statement(self.KIND, "alpha", "1.0.0", root, ["docs/spec.md"], {})
            st["predicate"]["id"] = "../escape"
            with self.assertRaises(ValueError):
                cc.write_envelope(root, st)
            self.assertFalse(os.path.exists(os.path.join(tmp, ".skill-contract", "escape.json")))

    def test_cli_check_envelope_reports_c3_and_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "e.json")
            _write(path, "not json")
            r = subprocess.run([sys.executable, "-I", CHECKER, "check-envelope", path],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(r.stdout.strip().splitlines()[-1], "CONTRACT_RESULT: FAIL (C3)")


def quoted_python():
    return subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)


@unittest.skipIf(os.name == "nt", "shell-script stubs are POSIX-only")
class RuntimeTests(unittest.TestCase):
    def stub(self, d, name, body):
        p = os.path.join(d, name)
        _write(p, "#!/bin/sh\n%s\n" % body)
        os.chmod(p, 0o755)
        return p

    def test_a_python3_that_fails_the_probe_falls_through_to_python(self):
        with tempfile.TemporaryDirectory() as d:
            self.stub(d, "python3", "exit 1")
            self.stub(d, "python", 'exec "%s" "$@"' % sys.executable)
            self.assertEqual(cc.resolve_python({"PATH": d}), [os.path.join(d, "python")])

    def test_a_multi_word_override_wins(self):
        with tempfile.TemporaryDirectory() as d:
            self.stub(d, "python3", 'exec "%s" "$@"' % sys.executable)
            env = {"PATH": d, "SKILL_CONTRACT_PYTHON": "%s -X utf8" % quoted_python()}
            self.assertEqual(cc.resolve_python(env), [sys.executable, "-X", "utf8"])

    def test_nothing_resolves(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(cc.resolve_python({"PATH": d}))


class RerunTests(unittest.TestCase):
    def test_rerun_turns_claims_into_proof_or_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "repo")
            _write(os.path.join(root, "docs", "spec.md"), build_vectors.SPEC)
            ok = cc.assertion("ok", "alpha", "passed", root, ["docs/spec.md"],
                              command=["{python}", "-c", "pass"])
            bad = cc.assertion("bad", "alpha", "passed", root, ["docs/spec.md"],
                               command=["{python}", "-c", "raise SystemExit(3)"])
            st = cc.build_statement(build_vectors.KIND, "alpha", "1.0.0", root, ["docs/spec.md"],
                                    {}, [ok, bad])
            path = cc.write_envelope(root, st)
            env = dict(os.environ, SKILL_CONTRACT_PYTHON=quoted_python(), HOME=tmp, USERPROFILE=tmp)
            self.assertEqual(cc.check_envelope(path, root=root)["claims"],
                             {"ok": "CLAIMED", "bad": "CLAIMED"})
            self.assertEqual(cc.check_envelope(path, root=root, rerun=True, env=env)["claims"],
                             {"ok": "PROVEN", "bad": "FAILED"})

    def test_cli_rerun_needs_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "e.json")
            _write(path, json.dumps(build_vectors.envelope()))
            r = subprocess.run([sys.executable, "-I", CHECKER, "check-envelope", path, "--rerun"],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 1)

    def test_cli_reports_stale_and_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "repo")
            _write(os.path.join(root, "docs", "spec.md"), build_vectors.SPEC_EDITED)
            path = os.path.join(tmp, "e.json")
            _write(path, json.dumps(build_vectors.envelope()))
            r = subprocess.run([sys.executable, "-I", CHECKER, "check-envelope", path, "--root", root],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("ENVELOPE: STALE", r.stdout)
            self.assertIn("STALE: docs/spec.md", r.stdout)
            self.assertIn("CLAIM: spec-lint STALE", r.stdout)


if __name__ == "__main__":
    unittest.main()
