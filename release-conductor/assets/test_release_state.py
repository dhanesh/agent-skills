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

if __name__ == "__main__":
    unittest.main()
