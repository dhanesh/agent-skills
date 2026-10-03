#!/usr/bin/env python3
"""Tests for the skill-contract reference checker: every conformance vector,
the generator/committed-vector agreement, and the CLI. Stdlib only, offline.

Run:  cd docs/skill-contract/reference && python3 -I test_contract_check.py
"""
import contextlib
import filecmp
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # `python -I` drops the script dir from sys.path
import build_vectors  # noqa: E402
import contract_check as cc  # noqa: E402

VECTORS = os.path.join(os.path.dirname(HERE), "vectors")
CHECKER = os.path.join(HERE, "contract_check.py")


def _now_z():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _in_one_day():
    return (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    for label, skills in inp.get("raw_skills", {}).items():
        for name, hexdata in skills.items():
            path = os.path.join(base[label], name, "SKILL.md")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(bytes.fromhex(hexdata))
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


def run_grant_vector(inp, tmp):
    root = os.path.join(tmp, "root")
    for rel, text in inp["files"].items():
        _write(os.path.join(root, *rel.split("/")), text)
    edir = cc.envelope_dir(root)
    for st in [inp["grant"]] + inp.get("others", []):
        _write(os.path.join(edir, st["predicate"]["id"] + ".json"), json.dumps(st))
    path = os.path.join(edir, inp["grant"]["predicate"]["id"] + ".json")
    now = datetime.strptime(inp["now"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    rep = cc.check_grant(root, inp["action"], path=path, now=now, branch=inp["branch"],
                         default_branches={inp.get("default_branch", "main")})
    return {"status": rep["status"], "reason": rep["reason"]}


RUNNERS = {"skill": run_skill_vector, "envelope": run_envelope_vector,
           "discovery": run_discovery_vector, "grant": run_grant_vector}


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
            rel = os.path.relpath(path, VECTORS)
            with self.subTest(vector=rel):
                if vector.get("posix_only") and os.name == "nt":
                    # spec §6.4: the skip prints its reason rather than passing silently
                    reason = "vector %s is posix_only (it needs symlinks); skipped on Windows" % rel
                    print("SKIP: " + reason, file=sys.stderr)
                    self.skipTest(reason)
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
        return ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9", "c10"]


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

    def test_for_a_consumer_with_an_invalid_contract_names_the_real_cause(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "e.json")
            _write(path, json.dumps(build_vectors.envelope()))
            beta = os.path.join(tmp, "beta")
            _write(os.path.join(beta, "SKILL.md"), build_vectors.skill_md(
                "beta", optin="1", body=build_vectors.contract_section('{"consumes": [')))
            rep = cc.check_envelope(path, for_skill=beta)
            self.assertEqual([n for n, _ in rep["violations"]], [9])
            detail = rep["violations"][0][1]
            self.assertTrue(detail.startswith("beta's own contract is invalid: C1: "), detail)
            self.assertNotIn("does not consume", detail)
            # a valid consumer of another kind still reads "does not consume"
            _write(os.path.join(beta, "SKILL.md"),
                   build_vectors.consumer_md(kinds=(build_vectors.KIND_V2,)))
            self.assertEqual(cc.check_envelope(path, for_skill=beta)["violations"],
                             [(9, "beta does not consume %s" % self.KIND)])


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


class GrantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc-grant-")
        _write(os.path.join(self.tmp, "docs", "spec.md"), build_vectors.SPEC)
        _write(os.path.join(self.tmp, "plan.json"), build_vectors.PLAN_TEXT)
        self.now = datetime(2026, 9, 19, 13, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, st):
        path = os.path.join(cc.envelope_dir(self.tmp), st["predicate"]["id"] + ".json")
        _write(path, json.dumps(st))
        return path

    def check(self, action="local_reversible", **kw):
        kw.setdefault("now", self.now)
        kw.setdefault("branch", "factory/x")
        return cc.check_grant(self.tmp, action, **kw)

    def test_no_grant_is_none(self):
        self.assertEqual(self.check()["status"], "NONE")

    def test_latest_head_is_used_without_a_path(self):
        self.put(build_vectors.grant())
        self.assertEqual(self.check()["status"], "COVERED")

    def test_revoke_makes_the_next_check_ask(self):
        self.put(build_vectors.grant())
        out = cc.revoke_grant(self.tmp, now=self.now)
        self.assertTrue(os.path.isfile(out))
        rep = self.check()
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "revoked"))

    def test_explicit_path_to_a_revoked_grant_still_asks(self):
        p = self.put(build_vectors.grant())
        cc.revoke_grant(self.tmp, now=self.now)
        rep = self.check(path=p)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "superseded"))

    def test_a_malformed_revision_still_supersedes(self):
        # Tightening fails closed: a revision naming the grant supersedes it
        # even when the revision itself would not pass check_statement.
        p = self.put(build_vectors.grant())
        bad = build_vectors.grant(revoked=True, assertions=[], rev=build_vectors.GRANT_ID,
                                  gid="autonomy-grant-v1-20260919T121000Z-d4e5f6")
        bad["subject"] = []
        self.put(bad)
        rep = self.check(path=p)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "superseded"))

    def test_revoke_by_id_refuses_a_path_outside_the_envelope_dir(self):
        self.put(build_vectors.grant())
        with self.assertRaises(ValueError):
            cc.revoke_grant(self.tmp, grant_id="../../plan", now=self.now)

    def test_revoke_by_id_and_a_second_revoke_of_the_old_id_is_refused(self):
        self.put(build_vectors.grant())
        cc.revoke_grant(self.tmp, grant_id=build_vectors.GRANT_ID, now=self.now)
        with self.assertRaises(ValueError):
            cc.revoke_grant(self.tmp, grant_id=build_vectors.GRANT_ID, now=self.now)

    def test_revocation_is_a_valid_revoked_grant_envelope(self):
        self.put(build_vectors.grant())
        out = cc.revoke_grant(self.tmp, now=self.now)
        with open(out, encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual(cc.check_statement(st), [])
        self.assertEqual(cc.grant_violations(st), [])
        self.assertIs(st["predicate"]["payload"]["revoked"], True)
        self.assertEqual(st["predicate"]["wasRevisionOf"], build_vectors.GRANT_ID)
        self.assertEqual(st["predicate"]["assertions"], [])

    def test_revoke_with_no_grant_fails(self):
        with self.assertRaises(ValueError):
            cc.revoke_grant(self.tmp, now=self.now)

    def test_detached_or_foreign_branch_asks(self):
        self.put(build_vectors.grant(branch_pattern="*"))
        rep = self.check(branch="HEAD")
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "detached"))
        self.put(build_vectors.grant())
        rep = self.check(branch="feature/x")
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "branch"))

    def _git_repo(self, branch="factory/x"):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for args in (["init", "-q", "-b", branch], ["commit", "-q", "--allow-empty", "-m", "x"]):
            subprocess.run(["git", "-C", self.tmp] + args, check=True, capture_output=True, env=env)

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_a_real_detached_head_asks_detached_even_for_star(self):
        # A rebase started on the default branch detaches HEAD, and `rebase --continue`
        # then advances that branch: "*" must not cover a detached HEAD.
        self._git_repo()
        self.put(build_vectors.grant(branch_pattern="*"))
        rep = cc.check_grant(self.tmp, "local_reversible", now=self.now)
        self.assertEqual(rep["status"], "COVERED")
        subprocess.run(["git", "-C", self.tmp, "checkout", "-q", "--detach"], check=True,
                       capture_output=True)
        rep = cc.check_grant(self.tmp, "local_reversible", now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "detached"))

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_a_head_naming_no_commit_is_branch_unknown_not_detached(self):
        self._git_repo()
        self.put(build_vectors.grant(branch_pattern="*"))
        _write(os.path.join(self.tmp, ".git", "HEAD"), "0123456789" * 4 + "\n")
        self.assertIsNone(cc.current_branch(self.tmp))
        rep = cc.check_grant(self.tmp, "local_reversible", now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "branch-unknown"))

    def test_a_sig_file_beside_a_grant_changes_nothing(self):
        # A8: there is no signing. A .sig next to the grant neither raises nor lowers coverage.
        p = self.put(build_vectors.grant())
        _write(p + ".sig", "-----BEGIN SSH SIGNATURE-----\nAAAA\n-----END SSH SIGNATURE-----\n")
        self.assertEqual(self.check()["status"], "COVERED")
        rep = self.check("merge")
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "gate-ask"))
        for name in ("signature_level", "level_for_key_type", "allowed_signers_path",
                     "sshsig_info", "verified_key_type", "SIG_LEVELS", "GRANT_NAMESPACE",
                     "HW_SIGNED_CLASSES"):
            self.assertFalse(hasattr(cc, name), name)

    def test_covered_report_names_gate_and_no_signature(self):
        self.put(build_vectors.grant())
        rep = self.check()
        self.assertEqual((rep["status"], rep["id"], rep["gate"]),
                         ("COVERED", build_vectors.GRANT_ID, "grant"))
        self.assertNotIn("signed", rep)

    def test_invalid_report_carries_violations(self):
        self.put(build_vectors.grant(attributed={"skill": "spec-first-planning"},
                                     policy={"merge": "auto"}))
        rep = self.check()
        self.assertEqual(rep["status"], "INVALID")
        self.assertEqual(len(rep["violations"]), 2, rep["violations"])
        self.assertIn("human", rep["violations"][0])  # attribution is checked before floors
        self.assertIn("merge", rep["violations"][1])

    def test_irreversible_classes_are_ask_only(self):
        # A8: a grant can never cover merge, deploy, spend, external_message or delete.
        for c in sorted(cc.IRREVERSIBLE_CLASSES):
            for gate in ("grant", "auto"):
                viol = cc.grant_violations(build_vectors.grant(policy={c: gate}))
                self.assertEqual(len(viol), 1, (c, gate, viol))
                self.assertIn(c, viol[0])
            self.assertEqual(cc.grant_violations(build_vectors.grant(policy={c: "ask"})), [])
        self.put(build_vectors.grant())
        for c in cc.IRREVERSIBLE_CLASSES:
            rep = self.check(c)
            self.assertEqual((rep["status"], rep["reason"]), ("ASK", "gate-ask"), c)

    def test_check_grant_takes_no_env(self):
        import inspect
        self.assertNotIn("env", inspect.signature(cc.check_grant).parameters)

    def test_subjects_equal_after_case_and_dot_segment_are_not_distinct(self):
        st = build_vectors.grant()
        st["subject"][1] = dict(st["subject"][0], name="./Docs/Spec.md")
        self.assertEqual(cc.check_statement(st), [])  # structurally fine...
        viol = cc.grant_violations(st)
        self.assertTrue(any("distinct" in v for v in viol), viol)  # ...but one subject

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_an_inherited_git_dir_cannot_redirect_the_branch_probe(self):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        other = tempfile.mkdtemp(prefix="sc-other-")
        self.addCleanup(shutil.rmtree, other, True)
        for d, b in ((self.tmp, "main"), (other, "factory/x")):
            for args in (["init", "-q", "-b", b], ["commit", "-q", "--allow-empty", "-m", "x"]):
                subprocess.run(["git", "-C", d] + args, check=True, capture_output=True, env=env)
        self.put(build_vectors.grant(branch_pattern="*"))
        planted = {"GIT_DIR": os.path.join(other, ".git"), "GIT_WORK_TREE": other,
                   "GIT_INDEX_FILE": os.path.join(other, ".git", "index"),
                   "GIT_COMMON_DIR": os.path.join(other, ".git"),
                   "GIT_CEILING_DIRECTORIES": os.path.dirname(self.tmp)}
        with mock.patch.dict(os.environ, planted):
            self.assertEqual(cc.current_branch(self.tmp), "main")
            rep = cc.check_grant(self.tmp, "local_reversible", now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "default-branch"))

    def test_git_env_scrub_drops_repo_redirecting_variables(self):
        with mock.patch.dict(os.environ, {"GIT_DIR": "/x", "GIT_WORK_TREE": "/x",
                                          "GIT_INDEX_FILE": "/x", "GIT_COMMON_DIR": "/x",
                                          "GIT_CEILING_DIRECTORIES": "/x", "KEEP_ME": "1"}):
            e = cc.git_env()
        for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                  "GIT_CEILING_DIRECTORIES"):
            self.assertNotIn(k, e)
        self.assertEqual(e.get("KEEP_ME"), "1")

    def test_irreversible_classes_are_exactly_the_a8_five(self):
        self.assertEqual(cc.IRREVERSIBLE_CLASSES,
                         {"merge", "deploy", "spend", "external_message", "delete"})
        # A8 as amended for release-conductor: staging deploys and tag pushes are grantable
        self.assertEqual(set(cc.ACTION_CLASSES) - cc.IRREVERSIBLE_CLASSES,
                         {"read_only", "local_reversible", "push_branch", "open_pr",
                          "deploy_staging", "push_tag"})

    def test_a_require_signature_field_makes_the_grant_invalid(self):
        for req in ({}, {"local_reversible": "SIGNED"}):
            st = build_vectors.grant()
            st["predicate"]["payload"]["require_signature"] = req
            viol = cc.grant_violations(st)
            self.assertTrue(any("require_signature" in v for v in viol), (req, viol))

    def test_lifetime_over_seven_days_is_invalid(self):
        st = build_vectors.grant(expires="2026-09-27T00:00:00Z")
        self.assertTrue(any("7 days" in v for v in cc.grant_violations(st)))

    def test_default_branch_falls_back_to_main_and_master_outside_git(self):
        self.put(build_vectors.grant(branch_pattern="*"))
        for b in ("main", "master"):
            rep = self.check(branch=b)
            self.assertEqual((rep["status"], rep["reason"]), ("ASK", "default-branch"), b)
        self.assertEqual(self.check(branch="feature")["status"], "COVERED")

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_default_branch_comes_from_origin_head(self):
        def git(*args):
            subprocess.run(["git", "-C", self.tmp] + list(args), check=True,
                           capture_output=True, text=True)
        git("init", "-q")
        git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        # origin/HEAD is added to main and master, never a replacement for them
        self.assertEqual(cc.detect_default_branches(self.tmp), {"trunk", "main", "master"})
        self.put(build_vectors.grant(branch_pattern="*"))
        for b in ("trunk", "main"):
            rep = cc.check_grant(self.tmp, "local_reversible", now=self.now, branch=b)
            self.assertEqual((rep["status"], rep["reason"]), ("ASK", "default-branch"), b)
        rep = cc.check_grant(self.tmp, "local_reversible", now=self.now, branch="feature")
        self.assertEqual(rep["status"], "COVERED")

    def test_revocation_of_a_week_long_grant_stays_valid(self):
        self.put(build_vectors.grant(expires="2026-09-26T12:00:00Z"))
        out = cc.revoke_grant(self.tmp, now=self.now)
        with open(out, encoding="utf-8") as f:
            self.assertEqual(cc.grant_violations(json.load(f)), [])

    def test_git_failure_inside_a_work_tree_asks_branch_unknown(self):
        os.makedirs(os.path.join(self.tmp, ".git"))
        self.put(build_vectors.grant())
        orig = cc.current_branch
        cc.current_branch = lambda root: None
        try:
            rep = cc.check_grant(self.tmp, "local_reversible", now=self.now)
        finally:
            cc.current_branch = orig
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "branch-unknown"))

    def test_git_failure_below_a_work_tree_asks_branch_unknown(self):
        _write(os.path.join(self.tmp, ".git"), "gitdir: /nowhere\n")  # a worktree's .git file
        sub = os.path.join(self.tmp, "sub")
        _write(os.path.join(sub, "docs", "spec.md"), build_vectors.SPEC)
        _write(os.path.join(sub, "plan.json"), build_vectors.PLAN_TEXT)
        _write(os.path.join(cc.envelope_dir(sub), build_vectors.GRANT_ID + ".json"),
               json.dumps(build_vectors.grant()))
        rep = cc.check_grant(sub, "local_reversible", now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "branch-unknown"))

    def test_no_git_anywhere_skips_the_branch_floors(self):
        self.assertFalse(cc.in_git_work_tree(self.tmp))
        self.put(build_vectors.grant())
        orig = cc.current_branch
        cc.current_branch = lambda root: None
        try:
            rep = cc.check_grant(self.tmp, "local_reversible", now=self.now)
        finally:
            cc.current_branch = orig
        self.assertEqual(rep["status"], "COVERED")

    def test_casefolded_and_compatibility_forms_hit_the_default_branch(self):
        self.put(build_vectors.grant(branch_pattern="*"))
        for b in ("MAIN", "Master", "ma\u017fter"):  # U+017F LATIN SMALL LETTER LONG S
            rep = self.check(branch=b, default_branches={"main", "master"})
            self.assertEqual((rep["status"], rep["reason"]), ("ASK", "default-branch"), b)

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_a_tag_named_like_the_branch_does_not_hide_it(self):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "x"],
                     ["tag", "main"]):
            subprocess.run(["git", "-C", self.tmp] + args, check=True, capture_output=True, env=env)
        self.assertEqual(cc.current_branch(self.tmp), "main")
        subprocess.run(["git", "-C", self.tmp, "checkout", "-q", "--detach"], check=True,
                       capture_output=True)
        self.assertEqual(cc.current_branch(self.tmp), "HEAD")

    def _factory_repo(self):
        """self.tmp as a git repo: main holds one commit, HEAD is on factory/x."""
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

        def git(*args):
            return subprocess.run(["git", "-C", self.tmp] + list(args), check=True,
                                  capture_output=True, text=True, env=env)
        git("init", "-q", "-b", "main")
        git("commit", "-q", "--allow-empty", "-m", "x")
        git("checkout", "-q", "-b", "factory/x")
        return git

    # I1: a grant is one person's acceptance; a committed grant would cover every clone.
    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_an_untracked_grant_is_covered_and_a_committed_one_asks_tracked(self):
        git = self._factory_repo()
        p = self.put(build_vectors.grant(branch_pattern="factory/*"))
        rep = cc.check_grant(self.tmp, "local_reversible", now=self.now)
        self.assertEqual(rep["status"], "COVERED", rep)
        git("add", "-f", p)
        git("commit", "-q", "-m", "commit the grant")
        rep = cc.check_grant(self.tmp, "local_reversible", now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "tracked"))

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_a_staged_grant_asks_tracked(self):
        git = self._factory_repo()
        p = self.put(build_vectors.grant(branch_pattern="factory/*"))
        git("add", "-f", p)
        rep = cc.check_grant(self.tmp, "local_reversible", now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "tracked"))

    def test_a_git_failure_on_the_tracked_probe_asks_tracked(self):
        os.makedirs(os.path.join(self.tmp, ".git"))
        self.put(build_vectors.grant())
        with mock.patch.object(cc.subprocess, "run", side_effect=OSError("no git")):
            rep = self.check()  # branch given, so only the tracked probe runs git
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "tracked"))

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_a_grant_path_outside_the_repo_asks_tracked(self):
        self._factory_repo()
        other = self._dir_outside_any_work_tree()
        p = os.path.join(other, build_vectors.GRANT_ID + ".json")
        _write(p, json.dumps(build_vectors.grant(branch_pattern="factory/*")))
        rep = cc.check_grant(self.tmp, "local_reversible", path=p, now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "tracked"))

    def _dir_outside_any_work_tree(self):
        """A fresh temp directory, skipping the test when TMPDIR itself sits in a repo."""
        other = tempfile.mkdtemp(prefix="sc-outside-")
        self.addCleanup(shutil.rmtree, other, True)
        if cc.in_git_work_tree(other):
            self.skipTest("the temp directory is inside a git work tree")
        return other

    def _case_insensitive_fs(self):
        """True when the temp directory's filesystem folds case: create X, look for x."""
        probe = os.path.join(self.tmp, "CaseProbe")
        os.makedirs(probe, exist_ok=True)
        try:
            return os.path.isdir(os.path.join(self.tmp, "caseprobe"))
        finally:
            shutil.rmtree(probe, ignore_errors=True)

    def _git_in(self, cwd):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

        def git(*args):
            return subprocess.run(["git", "-C", cwd] + list(args), check=True,
                                  capture_output=True, text=True, env=env)
        return git

    # `git ls-files` pathspecs are case-sensitive even where core.ignorecase is true, so a
    # grant committed as .Skill-Contract/Envelopes/<id>.json must still count as tracked.
    # Two shapes, because git derives the pathspec prefix from the directory's on-disk
    # spelling: the index entry cased while the directory on disk is not (below), and the
    # directory itself cased, which only a case-folding filesystem can produce.

    CASED_REL = ".Skill-Contract/Envelopes"

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_a_grant_whose_index_entry_is_cased_asks_tracked(self):
        # The index entry is written straight with `update-index --cacheinfo`, so this
        # holds the harder shape -- lowercase on disk, cased in the index -- on every
        # filesystem: the probe reads the index, never the directory listing.
        git = self._factory_repo()
        p = self.put(build_vectors.grant(branch_pattern="factory/*"))
        blob = git("hash-object", "-w", p).stdout.strip()
        git("update-index", "--add", "--cacheinfo",
            "100644,%s,%s/%s" % (blob, self.CASED_REL, os.path.basename(p)))
        git("commit", "-q", "-m", "commit the grant under another case")
        self.assertEqual(git("ls-files").stdout.split(),
                         ["%s/%s" % (self.CASED_REL, os.path.basename(p))])
        rep = cc.check_grant(self.tmp, "local_reversible", now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "tracked"), rep)

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_a_grant_under_a_cased_directory_on_disk_asks_tracked(self):
        # The checkout shape: the directory itself is .Skill-Contract/Envelopes, and the
        # checker still reads it through the lowercase spelling. Needs a folding disk.
        if not self._case_insensitive_fs():
            self.skipTest("the filesystem is case-sensitive: those are two different files")
        git = self._factory_repo()
        st = build_vectors.grant(branch_pattern="factory/*")
        name = st["predicate"]["id"] + ".json"
        _write(os.path.join(self.tmp, *self.CASED_REL.split("/"), name), json.dumps(st))
        found = cc.latest_grant(self.tmp)
        self.assertEqual(found, os.path.join(cc.envelope_dir(self.tmp), name))
        self.assertTrue(os.path.isfile(found))  # one file, two spellings
        git("add", "-f", "%s/%s" % (self.CASED_REL, name))
        git("commit", "-q", "-m", "commit the grant under another case")
        rep = cc.check_grant(self.tmp, "local_reversible", now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "tracked"), rep)

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_a_grant_committed_in_a_nested_repo_asks_tracked(self):
        # .skill-contract can be its own repository or a submodule: the outer index never
        # holds the grant, but the inner one does, and every clone of it carries that yes.
        self._factory_repo()
        p = self.put(build_vectors.grant(branch_pattern="factory/*"))
        inner = self._git_in(os.path.join(self.tmp, ".skill-contract"))
        inner("init", "-q", "-b", "main")
        inner("add", "-f", p)
        inner("commit", "-q", "-m", "commit the grant in the nested repo")
        rep = cc.check_grant(self.tmp, "local_reversible", now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "tracked"), rep)

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_the_tracked_probe_is_three_valued(self):
        git = self._factory_repo()
        p = self.put(build_vectors.grant(branch_pattern="factory/*"))
        self.assertIs(cc.grant_is_tracked(self.tmp, p), False)  # untracked
        git("add", "-f", p)
        self.assertIs(cc.grant_is_tracked(self.tmp, p), True)  # staged
        git("commit", "-q", "-m", "commit the grant")
        self.assertIs(cc.grant_is_tracked(self.tmp, p), True)  # committed
        outside = os.path.join(self._dir_outside_any_work_tree(), os.path.basename(p))
        shutil.copyfile(p, outside)
        self.assertIsNone(cc.grant_is_tracked(self.tmp, outside))  # no work tree: cannot say
        with mock.patch.object(cc.subprocess, "run", side_effect=OSError("no git")):
            self.assertIsNone(cc.grant_is_tracked(self.tmp, p))  # git will not run

    # I2: CI configuration runs with the repository's secrets: pushing it is `deploy`.
    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_a_push_whose_commits_touch_ci_config_asks_ci_config(self):
        git = self._factory_repo()
        self.put(build_vectors.grant(branch_pattern="factory/*",
                                     policy={"local_reversible": "grant", "push_branch": "grant",
                                             "open_pr": "grant"}))
        _write(os.path.join(self.tmp, "src", "a.py"), "x = 1\n")
        git("add", "src/a.py")
        git("commit", "-q", "-m", "code")
        for action in ("push_branch", "open_pr"):
            rep = cc.check_grant(self.tmp, action, now=self.now)
            self.assertEqual(rep["status"], "COVERED", (action, rep))
        _write(os.path.join(self.tmp, ".github", "workflows", "x.yml"), "on: push\n")
        git("add", ".github/workflows/x.yml")
        git("commit", "-q", "-m", "ci")
        for action in ("push_branch", "open_pr"):
            rep = cc.check_grant(self.tmp, action, now=self.now)
            self.assertEqual((rep["status"], rep["reason"]), ("ASK", "ci-config"), action)
        # local work is not a push: the CI file only runs once it reaches the remote
        self.assertEqual(cc.check_grant(self.tmp, "local_reversible", now=self.now)["status"],
                         "COVERED")

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_every_ci_config_pattern_asks_and_a_lookalike_does_not(self):
        git = self._factory_repo()
        self.put(build_vectors.grant(branch_pattern="factory/*", policy={"push_branch": "grant"}))
        ci = [".github/workflows/x.yml", ".github/actions/a/action.yml", ".gitlab-ci.yml",
              ".circleci/config.yml", "azure-pipelines.yml", "Jenkinsfile", ".buildkite/p.yml",
              "bitbucket-pipelines.yml", ".drone.yml", ".travis.yml", "ci/Jenkinsfile"]
        for rel in ci:
            self.assertTrue(cc.is_ci_config(rel), rel)
        for rel in ("src/workflows/x.yml", "docs/github/workflows.md", "src/a.py",
                    "notJenkinsfile", ".github/CODEOWNERS"):
            self.assertFalse(cc.is_ci_config(rel), rel)
        # a deletion is a change too
        _write(os.path.join(self.tmp, ".travis.yml"), "language: python\n")
        git("add", ".travis.yml")
        git("commit", "-q", "-m", "travis")
        git("checkout", "-q", "main")
        git("merge", "-q", "--ff-only", "factory/x")
        git("checkout", "-q", "factory/x")
        self.assertEqual(cc.check_grant(self.tmp, "push_branch", now=self.now)["status"], "COVERED")
        git("rm", "-q", ".travis.yml")
        git("commit", "-q", "-m", "drop travis")
        rep = cc.check_grant(self.tmp, "push_branch", now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "ci-config"))

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_with_no_default_ref_every_commit_counts(self):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        _write(os.path.join(self.tmp, ".gitlab-ci.yml"), "x: 1\n")
        for args in (["init", "-q", "-b", "factory/x"], ["add", ".gitlab-ci.yml"],
                     ["commit", "-q", "-m", "x"]):
            subprocess.run(["git", "-C", self.tmp] + args, check=True, capture_output=True, env=env)
        self.put(build_vectors.grant(branch_pattern="factory/*", policy={"push_branch": "grant"}))
        rep = cc.check_grant(self.tmp, "push_branch", now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "ci-config"))

    def test_a_git_failure_on_the_ci_probe_asks_ci_config(self):
        self.put(build_vectors.grant(policy={"push_branch": "grant"}))
        with mock.patch.object(cc, "in_git_work_tree", return_value=True), \
                mock.patch.object(cc, "grant_is_tracked", return_value=False), \
                mock.patch.object(cc.subprocess, "run", side_effect=OSError("no git")):
            rep = self.check("push_branch")
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "ci-config"))

    def test_an_unanswerable_ci_probe_asks_ci_config(self):
        self.put(build_vectors.grant(policy={"push_branch": "grant"}))
        with mock.patch.object(cc, "in_git_work_tree", return_value=True), \
                mock.patch.object(cc, "grant_is_tracked", return_value=False), \
                mock.patch.object(cc, "changed_since_default", return_value=None):
            rep = self.check("push_branch")
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "ci-config"))

    # M-g: a handoff under a grant hands off only the plan the grant pins.
    def test_subject_must_be_one_the_grant_pins(self):
        self.put(build_vectors.grant())
        for pinned in ("plan.json", "./plan.json", "docs/spec.md",
                       os.path.join(self.tmp, "plan.json")):
            self.assertEqual(self.check(subject=pinned)["status"], "COVERED", pinned)
        _write(os.path.join(self.tmp, "other-plan.json"), "{}")
        for other in ("other-plan.json", os.path.join(self.tmp, "other-plan.json")):
            rep = self.check(subject=other)
            self.assertEqual((rep["status"], rep["reason"]), ("ASK", "subject"), other)

    def test_cli_subject_flag(self):
        # the CLI reads the real clock, so this grant is built at run time (A7 helpers)
        self.put(build_vectors.grant(generated=_now_z(), expires=_in_one_day()))
        base = [sys.executable, "-I", CHECKER, "check-grant", "--root", self.tmp,
                "--action", "local_reversible"]
        if shutil.which("git"):
            self._factory_repo()  # its own repo, so no enclosing .git leaks in
        r = subprocess.run(base + ["--subject", "plan.json"], capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = subprocess.run(base + ["--subject", "nope.json"], capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("reason=subject", r.stdout)

    def test_an_unhashable_revision_in_a_junk_envelope_does_not_crash(self):
        self.put(build_vectors.grant(expires="2026-09-20T12:00:00Z"))
        _write(os.path.join(cc.envelope_dir(self.tmp), "junk.json"),
               json.dumps({"predicateType": cc.GRANT_KIND, "predicate": {"wasRevisionOf": []}}))
        self.assertEqual(self.check()["status"], "COVERED")
        self.assertEqual(cc.latest_grant(self.tmp).endswith(build_vectors.GRANT_ID + ".json"), True)
        self.assertTrue(os.path.isfile(cc.revoke_grant(self.tmp, now=self.now)))

    def test_cli_last_line_is_grant_with_a_junk_envelope(self):
        self.put(self.fresh_grant())
        _write(os.path.join(cc.envelope_dir(self.tmp), "junk.json"),
               json.dumps({"predicateType": cc.GRANT_KIND, "predicate": {"wasRevisionOf": {}}}))
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            rc = cc.main(["check-grant", "--root", self.tmp, "--action", "local_reversible"])
        self.assertEqual(rc, 0)
        self.assertTrue(buf.getvalue().strip().splitlines()[-1].startswith("GRANT: COVERED"))

    def test_deeply_nested_or_oversized_json_is_unreadable_not_a_crash(self):
        self.put(build_vectors.grant())
        deep = os.path.join(cc.envelope_dir(self.tmp), "deep.json")
        _write(deep, "[" * 200000 + "]" * 200000)
        big = os.path.join(cc.envelope_dir(self.tmp), "big.json")
        _write(big, json.dumps({"pad": "x" * (cc.ENVELOPE_MAX_BYTES + 1)}))
        for p in (deep, big):
            st, err = cc.load_envelope(p)
            self.assertIsNone(st)
            self.assertEqual([n for n, _ in err], [3])
            self.assertEqual(self.check(path=p)["status"], "INVALID")
        self.assertEqual(self.check()["status"], "COVERED")

    def test_revoke_by_id_refuses_a_file_whose_content_names_another_id(self):
        alias = "autonomy-grant-v1-20260919T110000Z-000000"
        _write(os.path.join(cc.envelope_dir(self.tmp), alias + ".json"),
               json.dumps(build_vectors.grant()))
        with self.assertRaises(ValueError):
            cc.revoke_grant(self.tmp, grant_id=alias, now=self.now)

    def test_a_grant_filed_under_another_name_is_not_a_candidate(self):
        newer = build_vectors.grant(generated="2026-09-19T12:30:00Z")
        _write(os.path.join(cc.envelope_dir(self.tmp),
                            "autonomy-grant-v1-20260919T123000Z-ffffff.json"), json.dumps(newer))
        self.assertIsNone(cc.latest_grant(self.tmp))
        self.assertEqual(self.check()["status"], "NONE")

    def test_revocation_of_a_two_subject_grant_is_valid(self):
        self.put(build_vectors.grant())
        with open(cc.revoke_grant(self.tmp, now=self.now), encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual(len(st["subject"]), 2)
        self.assertEqual(cc.grant_violations(st), [])

    def test_unknown_action_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            rc = cc.main(["check-grant", "--root", self.tmp, "--action", "launch"])
        self.assertEqual(rc, 1)

    @staticmethod
    def fresh_grant(**kw):
        """A grant generated now (real clock) that expires in one day, for CLI tests."""
        now = datetime.now(timezone.utc).replace(microsecond=0)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        return build_vectors.grant(generated=now.strftime(fmt),
                                   expires=(now + timedelta(days=1)).strftime(fmt), **kw)

    def test_cli_exit_codes(self):
        self.put(self.fresh_grant())
        codes = {}
        for action in ("local_reversible", "merge"):
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                codes[action] = (cc.main(["check-grant", "--root", self.tmp, "--action", action]),
                                 buf.getvalue())
        self.assertEqual(codes["local_reversible"][0], 0)
        self.assertEqual(codes["local_reversible"][1].strip().splitlines()[-1],
                         "GRANT: COVERED id=%s class=local_reversible gate=grant"
                         % build_vectors.GRANT_ID)
        self.assertEqual(codes["merge"][0], 3)
        self.assertIn("GRANT: ASK", codes["merge"][1])

    def test_cli_invalid_none_and_revoke(self):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(cc.main(["check-grant", "--root", self.tmp, "--action", "read_only"]), 3)
        self.assertEqual(buf.getvalue().strip().splitlines()[-1], "GRANT: NONE")
        self.put(self.fresh_grant(policy={"deploy": "auto"}))
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(cc.main(["check-grant", "--root", self.tmp, "--action", "read_only"]), 2)
        self.assertTrue(buf.getvalue().strip().splitlines()[-1].startswith("GRANT: INVALID"))
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(cc.main(["revoke-grant", "--root", self.tmp]), 0)
        self.assertTrue(buf.getvalue().startswith("REVOKED: "))


class ReentryBlockTests(unittest.TestCase):
    OK = {"agent_cmd": ["claude", "-p", "{prompt}"], "interval_min": 10,
          "stall_min": 30, "max_reentries": 5}

    def problems(self, **over):
        b = dict(self.OK)
        b.update(over)
        return cc.reentry_problems(b)

    def test_a_valid_block_has_no_problems(self):
        self.assertEqual(cc.reentry_problems(dict(self.OK)), [])
        self.assertEqual(cc.reentry_problems({"agent_cmd": ["agent", "{prompt}", "{root}"]}), [])

    def test_agent_cmd_must_be_an_argv_list_with_one_prompt(self):
        for bad in ("claude -p {prompt}", [], ["claude", "-p"], ["a", "{prompt}", "{prompt}"],
                    ["a", "{prompt}", 3]):
            with self.subTest(bad=bad):
                self.assertTrue(self.problems(agent_cmd=bad))

    def test_a_nul_byte_in_any_element_is_refused(self):
        # exec cannot carry a NUL: Popen raises ValueError, so a grant never holds one
        for cmd in (["a\x00b", "{prompt}"], ["agent", "-p\x00", "{prompt}"]):
            with self.subTest(cmd=cmd):
                self.assertIn("agent_cmd must not contain a NUL byte", self.problems(agent_cmd=cmd))

    def test_a_shell_is_refused_by_basename(self):
        for sh in ("sh", "/bin/bash", "zsh", "fish", "dash", "ksh", "cmd", "powershell", "pwsh"):
            with self.subTest(sh=sh):
                self.assertTrue(self.problems(agent_cmd=[sh, "-c", "{prompt}"]))

    def test_an_executable_extension_does_not_bypass_the_shell_check(self):
        for sh in ("pwsh.exe", "cmd.exe", "powershell.exe", "bash.exe", "/bin/BASH"):
            with self.subTest(sh=sh):
                self.assertTrue(self.problems(agent_cmd=[sh, "-c", "{prompt}"]))

    def test_a_launcher_wrapping_a_shell_is_refused(self):
        for bad in (["env", "sh", "-c", "{prompt}"],
                    ["/usr/bin/env", "FOO=1", "bash", "-c", "{prompt}"],
                    ["busybox", "sh", "-c", "{prompt}"]):
            with self.subTest(bad=bad):
                self.assertTrue(self.problems(agent_cmd=bad))

    def test_a_launcher_wrapping_a_real_agent_stays_accepted(self):
        self.assertEqual(self.problems(agent_cmd=["env", "FOO=1", "claude", "-p", "{prompt}"]), [])

    def test_only_prompt_and_root_tokens(self):
        self.assertTrue(self.problems(agent_cmd=["a", "{prompt}", "{home}"]))

    def test_numeric_ranges(self):
        for key, bad in (("interval_min", 4), ("interval_min", 61), ("stall_min", 14),
                         ("stall_min", 241), ("max_reentries", 0), ("max_reentries", 21),
                         ("interval_min", "10"), ("max_reentries", True)):
            with self.subTest(key=key, bad=bad):
                self.assertTrue(self.problems(**{key: bad}))

    def test_stall_is_at_least_twice_the_interval(self):
        self.assertTrue(self.problems(interval_min=20, stall_min=30))
        self.assertEqual(self.problems(interval_min=15, stall_min=30), [])

    def test_the_cross_field_check_is_skipped_when_a_field_is_itself_invalid(self):
        # interval_min=61 is out of range; stall_min=15 is in range but would fail the
        # 2x-interval check against the (invalid) interval_min=61 -- that would be a
        # misleading second complaint about a field that isn't actually wrong.
        self.assertEqual(self.problems(interval_min=61, stall_min=15),
                         ["interval_min must be an integer from 5 to 60"])

    def test_unknown_keys_are_refused(self):
        self.assertTrue(self.problems(shell=True))

    def test_a_non_dict_block_is_rejected_directly(self):
        self.assertEqual(cc.reentry_problems("nope"), ["must be an object"])

    def test_a_grant_with_a_bad_block_is_invalid(self):
        st = build_vectors.grant()
        st["predicate"]["payload"]["reentry"] = {"agent_cmd": "sh -c x"}
        self.assertTrue(any(v.startswith("payload.reentry") for v in cc.grant_violations(st)))


GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
RELEASE_ID = "autonomy-grant-v1-20260919T121500Z-e5f6a7"
RECIPE = "release/recipe.json"
INTENT = ".skill-contract/releases/1.2.0/intent.json"
RECIPE_TEXT = '{"recipe": 1}\n'
INTENT_TEXT = '{"version": "1.2.0", "base": "abc"}\n'
RELEASE_POLICY = {"local_reversible": "grant", "push_branch": "grant", "open_pr": "grant",
                  "deploy_staging": "grant", "push_tag": "grant"}
# Fail closed: explicit tag triggers AND configs that run on tags by default.
TAGGED_CI = (
    (".github/workflows/rel.yml", "on:\n  push:\n    tags: ['v*']\n"),
    (".github/workflows/a.yml", "on: push\njobs: {}\n"),
    (".github/workflows/b.yml", "on: [push]\njobs: {}\n"),
    (".github/workflows/c.yml", "on:\n  create:\njobs: {}\n"),
    (".gitlab-ci.yml", "deploy:\n  rules:\n    - if: $CI_COMMIT_TAG\n"),
    (".gitlab-ci.yml", "deploy:\n  script: ./ship\n"),  # no rules: runs on tags
    ("Jenkinsfile", "pipeline { agent any }\n"),
    (".buildkite/pipeline.yml", "steps:\n  - command: ./ship\n"),
    (".circleci/config.yml", "filters:\n  tags:\n    only: /^v.*/\n"),
    # an unfiltered push must not borrow a sibling trigger's branches filter
    (".github/workflows/d.yml", "on:\n  push:\n  pull_request:\n    branches: [main]\njobs: {}\n"),
    # path filters are not evaluated for tag pushes
    (".github/workflows/e.yml", "on:\n  push:\n    paths: ['src/**']\njobs: {}\n"),
    (".github/workflows/f.yml", "on:\n  push:\n    branches: [main]\n  release:\n"
                                "    types: [published]\njobs: {}\n"),
    (".github/workflows/g.yml", "on:\n  push:\n    branches: [main]\n    tags-ignore: ['x']\n"),
    (".github/workflows/h.yml", "on:\n  - pull_request\n  - push\n"),
    (".github/workflows/i.yml", "on: {push: {branches: [main]}}\n"),  # unparsed: fail closed
    (".github/workflows/j.yml", "name: no trigger key at all\n"),
    (".github/workflows/k.yml", "on:\n  workflow_run:\n    workflows: [build]\n"),
    # CircleCI tag filters in any YAML or JSON spelling (fix round 1)
    (".circleci/config.yml", "jobs:\n  d:\n    filters: {tags: {only: /^v.*/}}\n"),
    (".circleci/config.yml", '{"workflows": {"w": {"jobs": [{"d": {"filters": '
                             '{"tags": {"only": "/v.*/"}}}}]}}}\n'),
    (".circleci/config.yml", "filters:\n  \"tags\":\n    only: /^v.*/\n"),
    (".circleci/config.yml", "filters:\n  'tags': {only: /^v.*/}\n"),
    # upper-case extensions are still workflows; unknown files in workflows fail closed
    (".github/workflows/x.YML", "on: push\n"),
    (".github/workflows/y.Yaml", "on: [push]\n"),
    (".github/workflows/README.md", "on: push\n"),
    # CI systems with no known no-tag default (fix round 1: added to is_ci_config)
    (".gitea/workflows/x.yml", "on:\n  push:\n    branches: [main]\n"),
    (".forgejo/workflows/x.yml", "on:\n  pull_request:\n"),
    (".woodpecker.yml", "steps: []\n"),
    (".woodpecker/ship.yml", "steps: []\n"),
    ("appveyor.yml", "build: off\n"),
    (".appveyor.yml", "build: off\n"),
    (".cirrus.yml", "task: {}\n"),
    ("cloudbuild.yaml", "steps: []\n"),
    ("cloudbuild.yml", "steps: []\n"),
    (".semaphore/semaphore.yml", "version: v1.0\n"),
    ("bitrise.yml", "workflows: {}\n"),
    ("codemagic.yaml", "workflows: {}\n"),
    ("buildspec.yml", "version: 0.2\n"),
)
TAG_FREE_CI = (
    (".github/workflows/ci.yml", "on:\n  push:\n    branches: [main]\njobs: {}\n"),
    (".github/workflows/pr.yml", "\"on\":\n  pull_request:\n    branches: [main]\n"
                                 "  workflow_dispatch:\njobs:\n  release:\n"
                                 "    steps:\n      - run: git push origin HEAD\n"),
    (".github/workflows/ign.yml", "on:\n  push:\n    # comment\n    branches-ignore: [x]\n"
                                  "    paths: ['a']\n"),
    (".circleci/config.yml", "version: 2.1\nworkflows:\n  build:\n    jobs: [test]\n"),
    (".github/actions/a/action.yml", "runs:\n  using: composite\n"),
)


class ReleaseCheckerTests(unittest.TestCase):
    """Task 1 of release-conductor: deploy_staging and push_tag, --worktree, the CI
    tag-trigger floor, grants selected by subject, revoke-all, the release grant shape."""

    def setUp(self):
        self.tmp = self.repo_dir()
        self.now = datetime(2026, 9, 19, 13, 0, 0, tzinfo=timezone.utc)

    @staticmethod
    def repo_dir():
        d = tempfile.mkdtemp(prefix="sc-release-")
        _write(os.path.join(d, "docs", "spec.md"), build_vectors.SPEC)
        _write(os.path.join(d, "plan.json"), build_vectors.PLAN_TEXT)
        _write(os.path.join(d, *RECIPE.split("/")), RECIPE_TEXT)
        _write(os.path.join(d, *INTENT.split("/")), INTENT_TEXT)
        return d

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def put(root, st):
        path = os.path.join(cc.envelope_dir(root), st["predicate"]["id"] + ".json")
        _write(path, json.dumps(st))
        return path

    @staticmethod
    def git(cwd, *args):
        return subprocess.run(["git", "-C", cwd] + list(args), check=True,
                              capture_output=True, text=True, env=GIT_ENV)

    def init_repo(self, root, files=(), branch="factory/x"):
        """root as a repo: `files` committed on main, then HEAD on `branch`."""
        self.git(root, "init", "-q", "-b", "main")
        for rel, text in files:
            _write(os.path.join(root, *rel.split("/")), text)
            self.git(root, "add", "-f", rel)
        self.git(root, "commit", "-q", "--allow-empty", "-m", "x")
        if branch != "main":
            self.git(root, "checkout", "-q", "-b", branch)

    @staticmethod
    def release_grant(branch_pattern="release/*", policy=None, version="1.2.0"):
        st = build_vectors.grant(policy=dict(RELEASE_POLICY) if policy is None else policy,
                                 branch_pattern=branch_pattern, gid=RELEASE_ID,
                                 generated="2026-09-19T12:15:00Z")
        st["subject"] = [{"name": RECIPE, "digest": {"sha256": build_vectors.sha(RECIPE_TEXT)}},
                         {"name": INTENT, "digest": {"sha256": build_vectors.sha(INTENT_TEXT)}}]
        st["predicate"]["payload"]["release"] = {"version": version}
        return st

    @staticmethod
    def factory_grant(branch_pattern="factory/*", policy=None):
        return build_vectors.grant(branch_pattern=branch_pattern, policy=policy)

    def test_new_classes_are_grantable(self):
        self.assertIn("deploy_staging", cc.ACTION_CLASSES)
        self.assertIn("push_tag", cc.ACTION_CLASSES)
        self.assertNotIn("deploy_staging", cc.IRREVERSIBLE_CLASSES)
        self.assertNotIn("push_tag", cc.IRREVERSIBLE_CLASSES)
        st = build_vectors.grant()
        st["predicate"]["payload"]["gate_policy"]["deploy_staging"] = "grant"
        st["predicate"]["payload"]["gate_policy"]["push_tag"] = "grant"
        self.assertEqual(cc.grant_violations(st), [])
        st["predicate"]["payload"]["gate_policy"]["deploy"] = "grant"
        self.assertTrue(cc.grant_violations(st))  # deploy stays never-grantable
        for cls in ("deploy_staging", "push_tag"):
            st = build_vectors.grant(policy={cls: "auto"})
            self.assertTrue(cc.grant_violations(st), cls)  # remote: at most grant

    def test_subject_selects_its_own_grant(self):
        # two live grants: factory (spec+plan) and release (recipe+intent), release newer
        self.put(self.tmp, self.factory_grant(branch_pattern="*"))
        self.put(self.tmp, self.release_grant(branch_pattern="*"))
        rep = cc.check_grant(self.tmp, "local_reversible", subject="plan.json",
                             now=self.now, branch="factory/x")
        self.assertEqual((rep["status"], rep["id"]), ("COVERED", build_vectors.GRANT_ID), rep)
        rep = cc.check_grant(self.tmp, "local_reversible", subject=RECIPE, now=self.now,
                             branch="release/1.2.0-stage")
        self.assertEqual((rep["status"], rep["id"]), ("COVERED", RELEASE_ID), rep)
        rep = cc.check_grant(self.tmp, "local_reversible",
                             subject=os.path.join(self.tmp, *RECIPE.split("/")),
                             now=self.now, branch="x")
        self.assertEqual((rep["status"], rep["id"]), ("COVERED", RELEASE_ID), rep)
        # no subject: the newest grant, as before
        rep = cc.check_grant(self.tmp, "local_reversible", now=self.now, branch="x")
        self.assertEqual((rep["status"], rep["id"]), ("COVERED", RELEASE_ID), rep)
        self.assertEqual(cc.grant_for_subject(self.tmp, "./plan.json"),
                         os.path.join(cc.envelope_dir(self.tmp), build_vectors.GRANT_ID + ".json"))
        # a subject no grant pins: nothing covers it, and the ask names the subject
        self.assertIsNone(cc.grant_for_subject(self.tmp, "other.json"))
        rep = cc.check_grant(self.tmp, "local_reversible", subject="other.json",
                             now=self.now, branch="x")
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "subject"), rep)
        # the factory grant's own revocation is selected for its subject: never an older grant
        cc.revoke_grant(self.tmp, grant_id=build_vectors.GRANT_ID, now=self.now)
        rep = cc.check_grant(self.tmp, "local_reversible", subject="plan.json",
                             now=self.now, branch="x")
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "revoked"), rep)
        rep = cc.check_grant(self.tmp, "local_reversible", subject=RECIPE, now=self.now,
                             branch="x")
        self.assertEqual(rep["status"], "COVERED", rep)

    def _cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            rc = cc.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def test_revoke_with_no_id_revokes_every_live_grant(self):
        # two live grants; revoke-grant (no id) -> both ASK revoked for their subjects
        self.put(self.tmp, self.factory_grant(branch_pattern="*"))
        self.put(self.tmp, self.release_grant(branch_pattern="*"))
        rc, out, err = self._cli("revoke-grant", "--root", self.tmp)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(sorted(out.split()),
                         sorted(["REVOKED:", build_vectors.GRANT_ID, "REVOKED:", RELEASE_ID]))
        for subject in ("plan.json", RECIPE):
            rep = cc.check_grant(self.tmp, "local_reversible", subject=subject,
                                 now=self.now, branch="x")
            self.assertEqual((rep["status"], rep["reason"]), ("ASK", "revoked"), subject)
        rep = cc.check_grant(self.tmp, "local_reversible", now=self.now, branch="x")
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "revoked"))
        # nothing live is left to revoke: the same usage-level failure as before
        self.assertEqual(cc.revoke_all(self.tmp, now=self.now), [])
        rc, out, err = self._cli("revoke-grant", "--root", self.tmp)
        self.assertEqual(rc, 1, out + err)

    def test_revoke_with_an_id_revokes_only_that_grant(self):
        self.put(self.tmp, self.factory_grant(branch_pattern="*"))
        self.put(self.tmp, self.release_grant(branch_pattern="*"))
        rc, out, err = self._cli("revoke-grant", "--root", self.tmp, "--id", RELEASE_ID)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertTrue(out.startswith("REVOKED: "), out)
        rep = cc.check_grant(self.tmp, "local_reversible", subject=RECIPE, now=self.now,
                             branch="x")
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "revoked"))
        rep = cc.check_grant(self.tmp, "local_reversible", subject="plan.json", now=self.now,
                             branch="x")
        self.assertEqual(rep["status"], "COVERED", rep)
        # the positional id still works, and conflicting ids are a usage error
        rc, _, _ = self._cli("revoke-grant", "--root", self.tmp, build_vectors.GRANT_ID,
                             "--id", RELEASE_ID)
        self.assertEqual(rc, 1)
        rc, out, err = self._cli("revoke-grant", "--root", self.tmp, build_vectors.GRANT_ID)
        self.assertEqual(rc, 0, out + err)

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_worktree_judges_the_worktree_branch(self):
        # root on main, worktree on release/1.2.0-stage (branch_pattern "release/*")
        self.init_repo(self.tmp, branch="main")
        wt = os.path.join(tempfile.mkdtemp(prefix="sc-wt-"), "stage")
        self.addCleanup(shutil.rmtree, os.path.dirname(wt), True)
        self.git(self.tmp, "worktree", "add", "-q", "-b", "release/1.2.0-stage", wt)
        self.put(self.tmp, self.release_grant())
        rep = cc.check_grant(self.tmp, "deploy_staging", worktree=wt, now=self.now)
        self.assertEqual(rep["status"], "COVERED", rep)
        rep = cc.check_grant(self.tmp, "deploy_staging", now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "default-branch"), rep)
        # the worktree's own branch is judged: on a branch outside the pattern it asks
        self.git(wt, "checkout", "-q", "-b", "feature/x")
        rep = cc.check_grant(self.tmp, "deploy_staging", worktree=wt, now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "branch"), rep)
        # the CLI takes --worktree too (exit 3 here: ASK branch)
        rc, out, _ = self._cli("check-grant", "--root", self.tmp, "--action", "deploy_staging",
                               "--worktree", wt)
        self.assertEqual(rc, 3)
        self.assertIn("GRANT: ASK", out)

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_worktree_must_share_the_repository(self):
        # an unrelated repo's path as --worktree -> ASK "worktree"
        self.init_repo(self.tmp, branch="main")
        other = tempfile.mkdtemp(prefix="sc-other-")
        self.addCleanup(shutil.rmtree, other, True)
        self.init_repo(other, branch="release/1.2.0-stage")
        self.put(self.tmp, self.release_grant())
        rep = cc.check_grant(self.tmp, "deploy_staging", worktree=other, now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "worktree"), rep)
        self.assertFalse(cc.worktree_ok(self.tmp, other))
        self.assertTrue(cc.worktree_ok(self.tmp, self.tmp))
        # the repository's own git dir shares the common dir but is not a work tree
        rep = cc.check_grant(self.tmp, "deploy_staging", worktree=os.path.join(self.tmp, ".git"),
                             now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "worktree"), rep)
        self.assertFalse(cc.worktree_ok(self.tmp, os.path.join(self.tmp, ".git")))
        missing = os.path.join(other, "nope")
        rep = cc.check_grant(self.tmp, "deploy_staging", worktree=missing, now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "worktree"), rep)

    def _tag_repo(self, path, text):
        """A fresh repo holding `text` at `path` on main, HEAD on factory/x, and an
        untracked grant that grants push_tag."""
        d = self.repo_dir()
        self.addCleanup(shutil.rmtree, d, True)
        self.init_repo(d, files=[(path, text)] if path else ())
        self.put(d, self.factory_grant(policy={"push_tag": "grant", "push_branch": "grant"}))
        return d

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_tag_push_is_deploy_when_ci_may_run_on_tags(self):
        for path, text in TAGGED_CI:
            with self.subTest(path=path, text=text):
                d = self._tag_repo(path, text)
                self.assertIs(cc.ci_tag_triggers(d), True)
                rep = cc.check_grant(d, "push_tag", now=self.now)
                self.assertEqual((rep["status"], rep["reason"]), ("ASK", "ci-tag"), rep)
                # a branch push of the same commits stays grantable: no CI file changed
                self.assertEqual(cc.check_grant(d, "push_branch", now=self.now)["status"],
                                 "COVERED")

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_every_new_ci_format_is_ci_config_and_asks_ci_tag(self):
        for rel in (".gitea/workflows/x.yml", ".forgejo/workflows/x.yml", ".woodpecker.yml",
                    ".woodpecker/a.yml", "appveyor.yml", ".appveyor.yml", ".cirrus.yml",
                    "cloudbuild.yaml", "cloudbuild.yml", ".semaphore/semaphore.yml",
                    "bitrise.yml", "codemagic.yaml", "buildspec.yml", "sub/buildspec.yml"):
            self.assertTrue(cc.is_ci_config(rel), rel)
        for rel in ("src/.gitea/workflows/x.yml", "docs/.semaphore/x.yml", "notbitrise.yml"):
            self.assertFalse(cc.is_ci_config(rel), rel)
        d = self._tag_repo(".gitea/workflows/x.yml", "on:\n  pull_request:\n")
        rep = cc.check_grant(d, "push_tag", now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "ci-tag"), rep)
        # the extended list feeds the ci-config ask for a branch push too (fail closed)
        _write(os.path.join(d, ".cirrus.yml"), "task: {}\n")
        self.git(d, "add", ".cirrus.yml")
        self.git(d, "commit", "-q", "-m", "cirrus")
        rep = cc.check_grant(d, "push_branch", now=self.now)
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "ci-config"), rep)

    def test_tag_push_asks_when_git_cannot_say(self):
        # R1: None (git cannot list or read the tree) counts as tag-triggered
        self.put(self.tmp, self.factory_grant(policy={"push_tag": "grant"}))
        with mock.patch.object(cc, "ci_tag_triggers", return_value=None):
            rep = cc.check_grant(self.tmp, "push_tag", now=self.now, branch="factory/x")
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "ci-tag"))
        self.assertIsNone(cc.ci_tag_triggers(self.tmp))  # not a repo: git cannot say
        with mock.patch.object(cc.subprocess, "run", side_effect=OSError("no git")):
            self.assertIsNone(cc.ci_tag_triggers(self.tmp))

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_tag_push_is_grantable_only_when_proven_tag_free(self):
        for path, text in ((None, None),) + TAG_FREE_CI:
            with self.subTest(path=path, text=text):
                d = self._tag_repo(path, text)
                self.assertIs(cc.ci_tag_triggers(d), False)
                rep = cc.check_grant(d, "push_tag", now=self.now)
                self.assertEqual(rep["status"], "COVERED", rep)

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_a_tag_or_staging_push_whose_commits_touch_ci_config_asks_ci_config(self):
        d = self._tag_repo(None, None)
        self.put(d, build_vectors.grant(policy={"push_tag": "grant", "deploy_staging": "grant"},
                                        gid="autonomy-grant-v1-20260919T121000Z-0a0b0c",
                                        generated="2026-09-19T12:10:00Z"))
        _write(os.path.join(d, ".circleci", "config.yml"), "version: 2.1\n")
        self.git(d, "add", ".circleci/config.yml")
        self.git(d, "commit", "-q", "-m", "ci")
        self.assertIs(cc.ci_tag_triggers(d), False)
        for action in ("push_tag", "deploy_staging"):
            rep = cc.check_grant(d, action, now=self.now)
            self.assertEqual((rep["status"], rep["reason"]), ("ASK", "ci-config"), action)

    def test_tag_patterns_compile_on_this_python(self):
        import importlib
        import re
        importlib.reload(cc)  # a mid-pattern (?m) raises re.error on 3.11+
        pats = [v for v in vars(cc).values() if isinstance(v, re.Pattern)]
        self.assertTrue(pats)
        for p in pats:
            self.assertNotRegex(p.pattern[1:], r"\(\?[aiLmsux]+\)", p.pattern)
        # every inline re.search on the tag path runs on this interpreter without re.error
        for path, text in TAGGED_CI:
            self.assertIs(cc._ci_file_tag_triggered(path, text), True, (path, text))
        for path, text in TAG_FREE_CI:
            self.assertIs(cc._ci_file_tag_triggered(path, text), False, (path, text))

    def test_release_version_must_be_semver(self):
        st = build_vectors.grant()
        st["predicate"]["payload"]["release"] = {"version": "v1.2"}
        self.assertTrue(any("release.version" in v for v in cc.grant_violations(st)))
        for bad in ("1.2", "01.2.3x", "1.2.3 ", {"v": 1}, None, 1):
            st["predicate"]["payload"]["release"] = {"version": bad}
            self.assertTrue(cc.grant_violations(st), bad)
        st["predicate"]["payload"]["release"] = {"version": "1.2.0", "extra": 1}
        self.assertTrue(cc.grant_violations(st))
        st["predicate"]["payload"]["release"] = "1.2.0"
        self.assertTrue(cc.grant_violations(st))
        for good in ("1.2.0", "10.0.1-rc.1", "1.2.3+build.5"):
            st["predicate"]["payload"]["release"] = {"version": good}
            self.assertEqual(cc.grant_violations(st), [], good)


class ArgvProblemsTests(unittest.TestCase):
    def test_a_recipe_argv_with_its_own_tokens(self):
        self.assertEqual(cc.argv_problems(["deploy", "--v", "{version}"], {"{version}"}), [])
        self.assertTrue(cc.argv_problems(["deploy", "{version}"], set()))
        self.assertTrue(cc.argv_problems(["deploy", "v{version}"], {"{version}"}))
        self.assertTrue(cc.argv_problems(["deploy", "{home}"], {"{version}"}))

    def test_the_shared_refusals(self):
        for bad in ([], "deploy x", ["a", 3], ["a", ""], ["bash", "-c", "x"],
                    ["/usr/bin/env", "X=1", "sh", "-c", "x"], ["a\x00b"], ["cmd.exe", "/c"]):
            with self.subTest(bad=bad):
                self.assertTrue(cc.argv_problems(bad, set()))
        self.assertEqual(cc.argv_problems(["env", "X=1", "./ship"], set()), [])
        self.assertEqual(cc.argv_problems("x", set()),
                         ["must be a non-empty list of non-empty strings"])


if __name__ == "__main__":
    unittest.main()
