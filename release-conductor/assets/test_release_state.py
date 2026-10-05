import json, os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release as RL
import contract_check as CC
from release_testkit import repo, write_recipe, GIT

GOOD = {"build": ["true"], "deploy_staging": ["true"], "deploy_prod": ["true"],
        "rollback": ["true", "{version}"], "health": ["true"],
        "version_probe": ["cat", "probe-{env}.txt"], "staging_checks": [["true"]],
        "prod_smoke": [["true"]], "version": {"file": "package.json", "key": "version"},
        "bump": "patch", "artifact": "rebuild", "deploy_timeout": 5, "verify_skill": ".claude/skills/verify-app"}

class RecipeTests(unittest.TestCase):
    def test_a_good_recipe_has_no_problems(self):
        root = repo(); write_recipe(root, GOOD)
        r, problems = RL.load_recipe(root)
        self.assertEqual(problems, []); self.assertEqual(r["bump"], "patch")

    def test_shells_strings_and_unknown_tokens_are_refused(self):
        for key, bad in (("deploy_prod", "vercel --prod"), ("deploy_prod", ["bash", "-c", "x"]),
                         ("rollback", ["env", "sh", "-c", "x"]), ("health", ["curl", "{home}"]),
                         ("staging_checks", ["true"]), ("bump", "huge"), ("deploy_timeout", 0)):
            with self.subTest(key=key, bad=bad):
                root = repo(); write_recipe(root, dict(GOOD, **{key: bad}))
                self.assertTrue(RL.load_recipe(root)[1])

    def test_recipe_at_a_commit_is_read_from_git_not_the_working_tree(self):
        root = repo(); write_recipe(root, GOOD, commit=True)
        sha = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        write_recipe(root, dict(GOOD, deploy_prod=["false"]))         # uncommitted edit
        self.assertEqual(RL.load_recipe(root, rev=sha)[0]["deploy_prod"], ["true"])
        self.assertNotEqual(RL.recipe_sha(root, rev=sha), RL.recipe_sha(root))
        write_recipe(root, GOOD)                                       # back to the committed bytes
        self.assertEqual(RL.recipe_sha(root, rev=sha), CC.sha256_file(os.path.join(root, ".release", "recipe.json")))

    def test_recipe_sha_ignores_an_inherited_GIT_DIR(self):
        # A caller's environment (or a parent process) may leave GIT_DIR pointing at
        # a different repository; `git -C root show` must still read root's own
        # history, not follow GIT_DIR elsewhere.
        root = repo(); write_recipe(root, GOOD, commit=True)
        sha = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        expected = RL.recipe_sha(root, rev=sha)
        other = repo()  # an unrelated repo; a leaked GIT_DIR would point here instead
        old = os.environ.get("GIT_DIR")
        os.environ["GIT_DIR"] = os.path.join(other, ".git")
        try:
            self.assertEqual(RL.recipe_sha(root, rev=sha), expected)
            self.assertEqual(RL.load_recipe(root, rev=sha)[0]["deploy_prod"], ["true"])
        finally:
            if old is None:
                os.environ.pop("GIT_DIR", None)
            else:
                os.environ["GIT_DIR"] = old

    def test_verify_skill_must_be_a_safe_relative_path(self):
        for bad in ("/etc/passwd", "../../secrets", "a/../b", ""):
            with self.subTest(verify_skill=bad):
                root = repo(); write_recipe(root, dict(GOOD, verify_skill=bad))
                self.assertTrue(RL.load_recipe(root)[1])

class ExpandTests(unittest.TestCase):
    def test_a_whole_token_is_replaced(self):
        self.assertEqual(RL.expand(["true", "{version}"], {"version": "1.2.0"}),
                         ["true", "1.2.0"])

    def test_a_token_embedded_in_a_larger_string_is_also_replaced(self):
        # version_probe is called with {env} set to staging or prod, and the recipe
        # may embed it in a literal path, e.g. ["cat", "probe-{env}.txt"] (the GOOD
        # fixture above): load_recipe accepts that argv, so expand must be able to
        # fill it in, or verify-prod would literally run `cat probe-{env}.txt`.
        self.assertEqual(RL.expand(["cat", "probe-{env}.txt"], {"env": "staging"}),
                         ["cat", "probe-staging.txt"])

    def test_an_unrecognised_token_is_left_untouched(self):
        self.assertEqual(RL.expand(["curl", "{home}"], {"home": "/etc"}),
                         ["curl", "{home}"])

    def test_a_values_key_outside_version_commit_env_is_ignored(self):
        self.assertEqual(RL.expand(["true", "{version}"], {"version": "1.2.0", "extra": "x"}),
                         ["true", "1.2.0"])

    def test_multiple_tokens_substitute_in_one_pass(self):
        self.assertEqual(
            RL.expand(["release-{version}-{commit}"], {"version": "1.2.0", "commit": "deadbeef"}),
            ["release-1.2.0-deadbeef"])

    def test_a_substituted_value_is_never_itself_re_expanded(self):
        # A naive sequential str.replace (first {version}, then {commit}) would turn
        # "x-{version}" into "x-V-{commit}" and then into "x-V-C", silently
        # re-expanding a value that was never meant to be a template. Single-pass
        # substitution must instead refuse a substituted value that carries a brace.
        with self.assertRaises(ValueError):
            RL.expand(["x-{version}"], {"version": "V-{commit}", "commit": "C"})

    def test_a_non_string_substituted_value_is_refused(self):
        with self.assertRaises(ValueError):
            RL.expand(["{version}"], {"version": 120})


class StateTests(unittest.TestCase):
    def test_new_save_load_and_append_only_log(self):
        root = repo(); r = RL.Release.new(root, "1.2.0", "abc")
        r.set_status("prepped"); r.save(); r.log("prep", ok=True)
        r2 = RL.Release.load(root, "1.2.0")
        self.assertEqual(r2.status, "prepped")
        self.assertEqual([e["event"] for e in r2.events()][-1], "prep")

    def test_an_unknown_state_is_refused(self):
        r = RL.Release.new(repo(), "1.2.0", "abc")
        with self.assertRaises(ValueError):
            r.set_status("shipped")

    def test_the_lock_is_exclusive(self):
        root = repo()
        with RL.run_lock(root):
            code = ("import sys;sys.path.insert(0,%r);import release as RL;RL.LOCK_TIMEOUT=0.3\n"
                    "try:\n with RL.run_lock(%r): print('got')\nexcept RL.Locked: print('locked')"
                    % (os.path.dirname(os.path.abspath(RL.__file__)), root))
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), "locked")

    def test_run_cmd_kills_the_process_group_on_timeout(self):
        res = RL.run_cmd([sys.executable, "-c", "import time;time.sleep(30)"], tempfile.mkdtemp(), 0.5)
        self.assertTrue(res["timed_out"])


class EvidenceCoreIdentityTests(unittest.TestCase):
    """#10: release.py copies factory-conductor's evidence predicate. The record checks are
    meant to match exactly: _inside, _load_record, _check_record and EVIDENCE_SCHEMA are the
    same code (AST equal, docstrings aside). The intended differences are documented, not
    tested for identity: evidence_verdict judges EVERY feature the verify skill maps at the
    release commit and refuses the release driver as verifier (D11), and feature_map_at
    takes (root, skill, sha) and reads git with release.py's own helpers instead of the
    conductor's run state. Skipped where factory-conductor is not installed beside it."""

    SHARED = ("_inside", "_load_record", "_check_record", "EVIDENCE_SCHEMA")

    @staticmethod
    def _defs(path):
        import ast
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        out = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                body = node.body
                if body and isinstance(body[0], ast.Expr) \
                        and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    node.body = body[1:]
                out[node.name] = ast.dump(node)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        out[t.id] = ast.dump(node.value)
        return out

    def test_the_shared_record_checks_are_the_conductors(self):
        here = os.path.dirname(os.path.abspath(__file__))
        conductor = os.path.join(os.path.dirname(os.path.dirname(here)), "factory-conductor",
                                 "assets", "conductor.py")
        if not os.path.isfile(conductor):
            self.skipTest("factory-conductor is not installed beside release-conductor")
        ours, theirs = self._defs(os.path.join(here, "release.py")), self._defs(conductor)
        for name in self.SHARED:
            with self.subTest(name=name):
                self.assertIn(name, ours)
                self.assertEqual(ours[name], theirs.get(name),
                                 "%s differs from conductor.py's: keep the copies in step" % name)


if __name__ == "__main__":
    unittest.main()
