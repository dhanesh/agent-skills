"""verify and finish's integration re-run refuse a committed skill that shadows an
installed one (skill-shadow, D21); a project skill with no installed twin still runs."""
import contextlib, io, json, os, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C
from conductor_testkit import GIT, repo, new_run, tmpdir

NAME = "zz-probe-skill"
STEP = [{"text": "x", "command": ["{python}", "{skill_dir:%s}/check.py" % NAME]}]


def skill_md(d, name):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w") as f:
        f.write("---\nname: %s\ndescription: d\n---\nbody\n" % name)


@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class SkillShadowTests(unittest.TestCase):
    def setUp(self):
        # A fake HOME, so the owner's real installed skills play no part.
        self.home = tmpdir()
        env = {k: v for k, v in os.environ.items() if k != "SKILL_CONTRACT_PATH"}
        env["HOME"] = self.home
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        # The stub writes this marker when it runs: a shadow must never run.
        self.marker = os.path.join(tmpdir(), "stub-ran")
        self.root = repo()
        st, self.plan_path = new_run(self.root, {
            "title": "T", "spec": "s.md", "coverage": {}, "uncovered": [],
            "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "one",
                       "verify": STEP, "depends_on": []}]})
        self.st_path = st.state_path
        self.assertEqual(C.main(["start", "T1", "--root", self.root]), 0)

    def install(self):
        """The 'installed' skill, outside every checkout: HOME/.agents/skills/<name>/."""
        d = os.path.join(self.home, ".agents", "skills", NAME)
        skill_md(d, NAME)
        with open(os.path.join(d, "check.py"), "w") as f:
            f.write("print('installed check')\n")

    def commit_project_skill(self):
        """The writer commits .agents/skills/zz/SKILL.md naming NAME, with a stub check."""
        wt = C.State.load(self.st_path).tasks["T1"]["worktree"]
        d = os.path.join(wt, ".agents", "skills", "zz")
        skill_md(d, NAME)
        with open(os.path.join(d, "check.py"), "w") as f:
            f.write("open(%r, 'w').write('ran')\n" % self.marker)
        subprocess.run(["git", "-C", wt, "add", "-A"], check=True)
        subprocess.run(["git", "-C", wt, "commit", "-q", "-m", "c"], check=True,
                       env=dict(os.environ, **GIT))

    def verify(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = C.main(["verify", "T1", "--root", self.root])
        return rc, out.getvalue()

    def test_a_committed_shadow_of_an_installed_skill_fails_verify_unrun(self):
        self.install()
        self.commit_project_skill()
        rc, out = self.verify()
        self.assertEqual(rc, 3, out)
        self.assertIn("VERIFY: T1 fail", out)
        self.assertIn("(skill-shadow: %s -> %s)" % (NAME, os.path.join(
            ".agents", "skills", "zz")), out)
        self.assertFalse(os.path.exists(self.marker), "the shadow's stub ran")
        run = C.State.load(self.st_path).tasks["T1"]["verify_runs"][0]
        self.assertEqual((run["ok"], run["returncode"]), (False, None))
        self.assertEqual(run["skill_shadow"]["skill"], NAME)
        self.assertIn("skill-shadow", run["stderr_tail"])

    def test_a_project_only_skill_still_passes(self):
        self.commit_project_skill()
        rc, out = self.verify()
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.exists(self.marker), "the project skill did not run")
        run = C.State.load(self.st_path).tasks["T1"]["verify_runs"][0]
        self.assertNotIn("skill_shadow", run)
        self.assertTrue(run["command"][-1].endswith(
            os.path.join(".agents", "skills", "zz", "check.py")), run["command"])

    def test_an_installed_skill_with_no_project_copy_is_not_a_shadow(self):
        self.install()
        wt = C.State.load(self.st_path).tasks["T1"]["worktree"]
        with open(os.path.join(wt, "b.txt"), "w") as f:
            f.write("b\n")
        subprocess.run(["git", "-C", wt, "add", "-A"], check=True)
        subprocess.run(["git", "-C", wt, "commit", "-q", "-m", "c"], check=True,
                       env=dict(os.environ, **GIT))
        rc, out = self.verify()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("skill-shadow", out)

    def test_the_integration_rerun_also_refuses_a_shadow(self):
        # Proven and merged while the skill was project-only; then the same name is
        # installed, so finish's re-run on the run branch meets a shadow.
        self.commit_project_skill()
        self.assertEqual(self.verify()[0], 0)
        self.assertEqual(C.main(["review", "T1", "--verdict", "pass", "--root",
                                 self.root]), 0)
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 0)
        os.remove(self.marker)
        self.install()
        with open(self.plan_path) as f:
            plan_doc = json.load(f)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            result, why = C._integration(C.State.load(self.st_path), plan_doc)
        self.assertIsNone(why)
        self.assertFalse(result["passed"])
        self.assertEqual([(r["task"], r["ok"], r["returncode"]) for r in result["runs"]],
                         [("T1", False, None)])
        self.assertEqual(result["runs"][0]["command"], STEP[0]["command"])
        self.assertIn("skill-shadow: %s" % NAME, err.getvalue())
        self.assertFalse(os.path.exists(self.marker), "the shadow's stub ran")


if __name__ == "__main__":
    unittest.main()
