"""Tests for `release init` and `release prep` (Task 3). Stdlib only, offline: the remote
is a local bare repository and the PR command is stubbed, so nothing touches a network
or a real remote."""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release as RL  # noqa: E402
import contract_check as CC  # noqa: E402
from release_testkit import repo, write_recipe, GIT, tmpdir  # noqa: E402

GOOD = {"build": ["true"], "deploy_staging": ["true"], "deploy_prod": ["true"],
        "rollback": ["true", "{version}"], "health": ["true"],
        "version_probe": ["cat", "probe-{env}.txt"], "staging_checks": [["true"]],
        "prod_smoke": [["true"]], "version": {"file": "package.json", "key": "version"},
        "bump": "minor", "artifact": "rebuild", "deploy_timeout": 5,
        "verify_skill": ".claude/skills/verify-app"}


def git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                          env=dict(os.environ, **GIT))


def run(argv):
    """(rc, stdout, stderr) of release.main(argv), with the git identity env set (CI has
    no git identity, so a commit failure there must not hide behind a local one)."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, GIT), contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(err):
        rc = RL.main(argv)
    return rc, out.getvalue(), err.getvalue()


def merge_pr(root, branch, title_body):
    """A merge commit on main whose message is title_body (a PR merge stand-in)."""
    git(root, "checkout", "-q", "-b", branch)
    with open(os.path.join(root, branch.replace("/", "_") + ".txt"), "w") as f:
        f.write("x\n")
    git(root, "add", "-A", ".")
    git(root, "commit", "-q", "-m", "work")
    git(root, "checkout", "-q", "main")
    r = git(root, "merge", "-q", "--no-ff", branch, "-m", title_body)
    assert r.returncode == 0, r.stderr


class VersionTests(unittest.TestCase):
    def test_next_version_bumps_each_level(self):
        self.assertEqual(RL.next_version("1.1.0", "minor"), "1.2.0")
        self.assertEqual(RL.next_version("1.1.3", "patch"), "1.1.4")
        self.assertEqual(RL.next_version("1.1.3", "major"), "2.0.0")

    def test_a_prerelease_or_garbage_is_refused(self):
        for cur in ("1.2.0-rc.1", "1.2.0+build", "1.2", "v1.2.0", ""):
            with self.subTest(cur=cur), self.assertRaises(ValueError):
                RL.next_version(cur, "patch")
        with self.assertRaises(ValueError):
            RL.next_version("1.2.0", "huge")


class ChangelogTests(unittest.TestCase):
    def test_hostile_merge_subjects_are_neutralised(self):
        root = repo()
        # No blank line between the first line and the hostile text: %s keeps the first
        # paragraph only, so the hostile text must sit inside it to be tested at all.
        merge_pr(root, "f/a", "Add a thing\n## pwned @someone Closes #1 MARKER")
        lines = RL.changelog_lines(root, None)
        self.assertEqual(len(lines), 1)
        text = "\n".join(lines)
        self.assertIn("MARKER", text)
        self.assertNotIn("\n#", "\n" + text)
        bare = re.sub(r"(`+)(?:(?!\1).)*?\1", "", text)  # drop every code span
        self.assertNotIn("@", bare)
        self.assertNotIn("Closes #1", bare)

    def test_only_merges_since_the_tag_count(self):
        root = repo()
        merge_pr(root, "f/old", "old change")
        git(root, "tag", "v1.1.0")
        merge_pr(root, "f/new", "new change")
        self.assertEqual(len(RL.changelog_lines(root, "v1.1.0")), 1)
        self.assertIn("new change", RL.changelog_lines(root, "v1.1.0")[0])
        self.assertEqual(len(RL.changelog_lines(root, None)), 2)


class InitTests(unittest.TestCase):
    def test_init_writes_a_validated_recipe(self):
        root = repo()
        ans = os.path.join(tmpdir(), "answers.json")
        with open(ans, "w") as f:
            json.dump(GOOD, f)
        rc, out, _ = run(["init", "--root", root, "--answers", ans])
        self.assertEqual(rc, 0, out)
        self.assertIn("RELEASE: recipe written", out)
        self.assertEqual(RL.load_recipe(root), (GOOD, []))

    def test_init_refuses_an_invalid_recipe_and_writes_nothing(self):
        root = repo()
        ans = os.path.join(tmpdir(), "answers.json")
        with open(ans, "w") as f:
            json.dump(dict(GOOD, deploy_prod="vercel --prod"), f)
        rc, out, err = run(["init", "--root", root, "--answers", ans])
        self.assertEqual(rc, 2)
        self.assertIn("deploy_prod", out + err)
        self.assertFalse(os.path.exists(os.path.join(root, ".release", "recipe.json")))

    def test_init_never_overwrites_an_existing_recipe(self):
        root = repo()
        write_recipe(root, GOOD, commit=True)
        ans = os.path.join(tmpdir(), "answers.json")
        with open(ans, "w") as f:
            json.dump(dict(GOOD, bump="major"), f)
        self.assertEqual(run(["init", "--root", root, "--answers", ans])[0], 2)
        self.assertEqual(RL.load_recipe(root)[0]["bump"], "minor")


class PrepTests(unittest.TestCase):
    def setUp(self):
        self.root = repo()
        write_recipe(self.root, GOOD, commit=True)
        self.base = git(self.root, "rev-parse", "HEAD").stdout.strip()
        self.bare = tmpdir()
        subprocess.run(["git", "init", "-q", "--bare", self.bare], check=True)
        git(self.root, "remote", "add", "origin", self.bare)
        self.pr_log = os.path.join(tmpdir(), "pr.txt")

    def prep(self, *extra, approved=True, pr=None):
        argv = ["prep", "--root", self.root, "--driver", "agent-1",
                "--pr-cmd", json.dumps(pr or ["true"])]
        if approved:
            argv += ["--approved-by", "Dana Human"]
        return run(argv + list(extra))

    def wt(self, v="1.2.0"):
        return os.path.join(self.root, ".skill-contract", "releases", v, "wt-prep")

    def test_prep_writes_branch_intent_grant_pushes_and_opens_the_pr(self):
        rc, out, err = self.prep(pr=[sys.executable, "-c",
                                     "import sys,json;open(%r,'w').write(json.dumps("
                                     "sys.argv[1:]+[open(sys.argv[-1]).read()]))" % self.pr_log, "{title}", "{base_branch}",
                                     "{release_branch}", "{body_file}"])
        self.assertEqual(rc, 0, out + err)
        self.assertIn("RELEASE: 1.2.0 prepped", out)
        self.assertIn("NEXT: merge the release PR, then run stage", out)
        wt = self.wt()
        # the release branch carries the new version and a changelog section
        self.assertEqual(git(wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
                         "release/1.2.0")
        self.assertEqual(json.loads(git(wt, "show", "HEAD:package.json").stdout)["version"],
                         "1.2.0")
        self.assertIn("## 1.2.0", git(wt, "show", "HEAD:CHANGELOG.md").stdout)
        self.assertEqual(git(wt, "rev-parse", "HEAD~1").stdout.strip(), self.base)
        # R3: only the version file and the changelog are committed
        files = set(git(wt, "show", "--name-only", "--format=", "HEAD").stdout.split())
        self.assertEqual(files, {"package.json", "CHANGELOG.md"})
        # the default branch is untouched, locally and on the remote
        self.assertEqual(git(self.root, "rev-parse", "main").stdout.strip(), self.base)
        head = git(wt, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(git(self.bare, "rev-parse", "refs/heads/release/1.2.0").stdout.strip(),
                         head)
        self.assertNotEqual(git(self.bare, "rev-parse", "-q", "--verify", "refs/heads/main")
                            .returncode, 0)
        # the PR command ran with its tokens expanded
        with open(self.pr_log) as f:
            title, base, branch, _body_file, body = json.load(f)
        self.assertEqual((title, base, branch), ("Release 1.2.0", "main", "release/1.2.0"))
        self.assertIn("1.2.0", body)
        # intent
        rdir = os.path.join(self.root, ".skill-contract", "releases", "1.2.0")
        with open(os.path.join(rdir, "intent.json")) as f:
            self.assertEqual(json.load(f), {"version": "1.2.0", "base_commit": self.base,
                                            "bump": "minor"})
        # the grant validates and covers push_branch for the release worktree
        path = CC.grant_for_subject(self.root, ".release/recipe.json")
        st, errs = CC.load_envelope(path)
        self.assertFalse(errs)
        self.assertEqual(CC.check_statement(st), [])
        self.assertEqual(CC.grant_violations(st), [])
        p = st["predicate"]["payload"]
        self.assertEqual(p["release"], {"version": "1.2.0"})
        self.assertEqual(p["scope"]["branch_pattern"], ["release/1.2.0", "release/1.2.0-stage"])
        self.assertEqual({k for k, v in p["gate_policy"].items() if v == "grant"},
                         {"local_reversible", "push_branch", "open_pr", "deploy_staging",
                          "push_tag"})
        self.assertEqual({s["name"] for s in st["subject"]},
                         {".release/recipe.json", ".skill-contract/releases/1.2.0/intent.json"})
        acc = [a for a in st["predicate"]["assertions"] if a["test"] == "grant-accepted"]
        self.assertEqual(acc[0]["assertedBy"], {"human": "Dana Human"})
        rep = CC.check_grant(self.root, "push_branch", subject=".release/recipe.json",
                             worktree=wt)
        self.assertEqual(rep["status"], "COVERED", rep)
        # the grant itself is kept out of commits by prep, not only by the test kit
        with open(os.path.join(self.root, ".git", "info", "exclude")) as f:
            self.assertIn(RL.GRANT_EXCLUDE, f.read().split("\n"))
        # state
        rel = RL.Release.load(self.root, "1.2.0")
        self.assertEqual(rel.status, "prepped")
        self.assertEqual(rel.driver, "agent-1")
        self.assertEqual(rel.base_commit, self.base)
        self.assertEqual(rel.recipe_sha, RL.recipe_sha(self.root))
        self.assertTrue(rel.prep["pushed"] and rel.prep["pr"])
        self.assertEqual(rel.prep["commit"], head)

    def grants(self):
        d = CC.envelope_dir(self.root)
        return sorted(f for f in os.listdir(d) if f.startswith("autonomy-grant"))

    def test_the_grant_covers_only_this_versions_branches(self):  # R15
        self.assertEqual(self.prep()[0], 0)
        for name, want in (("release/1.2.0-stage", "COVERED"), ("release/1.3.0", "ASK")):
            with self.subTest(branch=name):
                wt = os.path.join(tmpdir(), "wt")
                self.assertEqual(git(self.root, "worktree", "add", "-q", "-b", name, wt,
                                     "release/1.2.0").returncode, 0)
                rep = CC.check_grant(self.root, "push_branch",
                                     subject=".release/recipe.json", worktree=wt)
                self.assertEqual(rep["status"], want, rep)

    def test_the_grant_names_exact_branches_not_a_prefix(self):  # R15, Task 1b
        # release/1.2.1* would also cover release/1.2.10: the grant lists the two branches
        intent = ".skill-contract/releases/1.2.1/intent.json"
        os.makedirs(os.path.join(self.root, os.path.dirname(intent)), exist_ok=True)
        with open(os.path.join(self.root, intent), "w") as f:
            f.write('{"version": "1.2.1"}\n')
        st = RL.build_release_grant(self.root, "1.2.1", intent,
                                    {c: "grant" for c in RL.RELEASE_GRANT_CLASSES},
                                    "Dana Human", "patch", CC.utc_now())
        self.assertEqual(st["predicate"]["payload"]["scope"]["branch_pattern"],
                         ["release/1.2.1", "release/1.2.1-stage"])
        path = CC.write_envelope(self.root, st)
        for name, want in (("release/1.2.1", ("COVERED", None)),
                           ("release/1.2.1-stage", ("COVERED", None)),
                           ("release/1.2.10", ("ASK", "branch")),
                           ("release/1.2.10-stage", ("ASK", "branch"))):
            with self.subTest(branch=name):
                rep = CC.check_grant(self.root, "push_branch", path=path,
                                     subject=RL.RECIPE_PATH, branch=name,
                                     default_branches={"main"})
                self.assertEqual((rep["status"], rep["reason"]), want, rep)

    def test_the_grant_never_covers_the_default_branch_checkout(self):
        self.assertEqual(self.prep()[0], 0)
        rep = CC.check_grant(self.root, "push_branch", subject=".release/recipe.json")
        self.assertEqual((rep["status"], rep["reason"]), ("ASK", "default-branch"))

    def test_resume_after_a_push_ask_reuses_grant_and_commit(self):  # R12
        pol = os.path.join(tmpdir(), "policy.json")
        with open(pol, "w") as f:
            json.dump({"push_branch": "ask"}, f)
        rc, out, _ = self.prep("--policy-file", pol)
        self.assertEqual(rc, 3)
        self.assertIn("NEXT: fix what stopped it, then re-run prep to resume", out)
        grants, commit = self.grants(), RL.Release.load(self.root, "1.2.0").prep["commit"]
        rc, out, _ = self.prep()  # still declined: asks again, changes nothing
        self.assertEqual(rc, 3)
        self.assertIn("GATE: push_branch ASK gate-ask", out)
        # the human lifts the decline in the grant itself
        path = os.path.join(CC.envelope_dir(self.root), grants[0])
        with open(path) as f:
            st = json.load(f)
        st["predicate"]["payload"]["gate_policy"]["push_branch"] = "grant"
        with open(path, "w") as f:
            json.dump(st, f)
        rc, out, err = self.prep()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("RELEASE: 1.2.0 prepped", out)
        self.assertEqual(self.grants(), grants)
        rel = RL.Release.load(self.root, "1.2.0")
        self.assertEqual(rel.prep["commit"], commit)
        self.assertTrue(rel.prep["pushed"] and rel.prep["pr"])
        self.assertEqual(git(self.bare, "rev-parse", "refs/heads/release/1.2.0").stdout.strip(),
                         commit)
        self.assertEqual(git(self.wt(), "rev-parse", "HEAD").stdout.strip(), commit)

    def test_resume_after_a_failed_pr_runs_only_the_pr(self):  # R12
        self.assertEqual(self.prep(pr=["false"])[0], 3)
        grants = self.grants()
        commit = RL.Release.load(self.root, "1.2.0").prep["commit"]
        rc, out, err = self.prep(pr=[sys.executable, "-c",
                                     "open(%r,'w').write('pr')" % self.pr_log])
        self.assertEqual(rc, 0, out + err)
        with open(self.pr_log) as f:
            self.assertEqual(f.read(), "pr")
        self.assertEqual(self.grants(), grants)
        rel = RL.Release.load(self.root, "1.2.0")
        self.assertEqual(rel.prep["commit"], commit)
        self.assertTrue(rel.prep["pr"])
        self.assertEqual([e["event"] for e in rel.events()].count("push"), 1)

    def test_the_kill_switch_stops_a_resume(self):
        pol = os.path.join(tmpdir(), "policy.json")
        with open(pol, "w") as f:
            json.dump({"open_pr": "ask"}, f)
        self.assertEqual(self.prep("--policy-file", pol)[0], 3)
        CC.revoke_all(self.root)
        rc, out, _ = self.prep()
        self.assertEqual(rc, 3)
        self.assertIn("GATE: open_pr ASK superseded", out)

    def test_a_completed_prep_rerun_says_so_and_redoes_nothing(self):
        self.assertEqual(self.prep()[0], 0)
        grants = self.grants()
        rc, out, err = self.prep(pr=["false"])
        self.assertEqual(rc, 0, out + err)
        self.assertIn("RELEASE: 1.2.0 already prepped", out)
        self.assertIn("NEXT: merge the release PR, then run stage", out)
        self.assertEqual(self.grants(), grants)

    def test_a_failed_local_prep_revokes_its_grant(self):  # R16a
        # A version file that is not JSON in the worktree fails after the grant is
        # written; re-prepping the same version from the same base would rewrite a
        # byte-identical intent and revive an unrevoked grant.
        with mock.patch.object(RL, "_set_version", side_effect=ValueError("boom")):
            rc, out, _ = self.prep()
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(os.path.join(self.root, ".skill-contract",
                                                     "releases", "1.2.0")))
        rep = CC.check_grant(self.root, "local_reversible", subject=".release/recipe.json")
        self.assertEqual(rep["status"], "ASK")
        self.assertEqual(rep["reason"], "revoked")
        # and a fresh prep of the same version still works under its own new grant
        rc, out, err = self.prep()
        self.assertEqual(rc, 0, out + err)

    def test_a_version_cmd_recipe_is_refused(self):  # R13
        write_recipe(self.root, dict(GOOD, version={"cmd": ["cat", "VERSION"]}), commit=True)
        rc, out, err = self.prep()
        self.assertEqual(rc, 2)
        self.assertIn("version.cmd", out + err)

    def test_prep_stops_when_the_run_lock_is_held(self):
        with mock.patch.object(RL, "LOCK_TIMEOUT", 0.3), RL.run_lock(self.root):
            rc, out, _ = self.prep()
        self.assertEqual(rc, 3)
        self.assertIn("STOP: locked", out)

    def test_bump_override_and_existing_changelog_gets_a_new_section_on_top(self):
        with open(os.path.join(self.root, "CHANGELOG.md"), "w") as f:
            f.write("# Changelog\n\n## 1.1.0\n\n- old\n")
        git(self.root, "add", "CHANGELOG.md")
        git(self.root, "commit", "-q", "-m", "changelog")
        rc, out, err = self.prep("--bump", "major")
        self.assertEqual(rc, 0, out + err)
        text = git(self.wt("2.0.0"), "show", "HEAD:CHANGELOG.md").stdout
        self.assertTrue(text.startswith("# Changelog\n"))
        self.assertLess(text.index("## 2.0.0"), text.index("## 1.1.0"))

    def test_untracked_files_never_reach_the_release_commit(self):
        with open(os.path.join(self.root, "secret.env"), "w") as f:
            f.write("TOKEN=x\n")
        rc, out, err = self.prep()
        self.assertEqual(rc, 0, out + err)
        files = set(git(self.wt(), "show", "--name-only", "--format=", "HEAD").stdout.split())
        self.assertEqual(files, {"package.json", "CHANGELOG.md"})

    def test_prep_without_approved_by_exits_2_and_writes_nothing(self):
        rc, out, err = self.prep(approved=False)
        self.assertEqual(rc, 2)
        self.assertIsNone(CC.grant_for_subject(self.root, ".release/recipe.json"))

    def test_a_blank_approver_is_refused(self):
        for name in ("  ", "Dana\nHuman", "Dana\u202eHuman", "Dana\u200bHuman"):
            with self.subTest(name=name):
                rc, _, _ = run(["prep", "--root", self.root, "--driver", "a",
                                "--approved-by", name, "--pr-cmd", '["true"]'])
                self.assertEqual(rc, 2)

    def test_prep_refuses_while_another_release_is_unfinished(self):
        other = RL.Release.new(self.root, "1.1.5", self.base)
        other.set_status("staged")
        other.save()
        rc, out, err = self.prep()
        self.assertEqual(rc, 2)
        self.assertIn("1.1.5", out + err)
        self.assertFalse(os.path.exists(self.wt()))

    def test_a_finished_release_does_not_block_prep(self):
        for v, s in (("1.0.0", "verified"), ("1.0.1", "rolled_back"), ("1.0.2", "stage_failed")):
            r = RL.Release.new(self.root, v, self.base)
            r.set_status(s)
            r.save()
        rc, out, err = self.prep()
        self.assertEqual(rc, 0, out + err)

    def test_a_release_dir_without_state_counts_as_unfinished(self):
        os.makedirs(os.path.join(self.root, ".skill-contract", "releases", "0.9.0"))
        rc, out, err = self.prep()
        self.assertEqual(rc, 2)
        self.assertIn("0.9.0", out + err)

    def test_prep_with_an_invalid_recipe_prints_the_problems(self):
        write_recipe(self.root, dict(GOOD, deploy_prod="vercel --prod"), commit=True)
        rc, out, err = self.prep()
        self.assertEqual(rc, 2)
        self.assertIn("deploy_prod", out + err)
        self.assertIsNone(CC.grant_for_subject(self.root, ".release/recipe.json"))

    def test_prep_refuses_a_recipe_never_committed(self):
        root = repo()
        write_recipe(root, GOOD)
        rc, out, err = run(["prep", "--root", root, "--driver", "a", "--approved-by", "D",
                            "--pr-cmd", '["true"]'])
        self.assertEqual(rc, 2)
        self.assertIn("recipe-uncommitted", out + err)

    def test_prep_refuses_a_committed_recipe_edited_since(self):
        write_recipe(self.root, dict(GOOD, bump="patch"))
        rc, out, err = self.prep()
        self.assertEqual(rc, 2)
        self.assertIn("recipe-uncommitted", out + err)
        self.assertIsNone(CC.grant_for_subject(self.root, ".release/recipe.json"))

    def test_a_push_command_outside_the_allowlist_is_refused(self):
        rc, out, err = self.prep("--push-cmd", '["true"]')
        self.assertEqual(rc, 2)
        self.assertIn("--push-cmd", out + err)
        rc, out, err = self.prep("--push-cmd", '["git", "push", "-f", "origin", "{release_branch}"]')
        self.assertEqual(rc, 2)

    def test_a_declined_push_asks_stops_and_leaves_state_consistent(self):
        pol = os.path.join(tmpdir(), "policy.json")
        with open(pol, "w") as f:
            json.dump({"push_branch": "ask"}, f)
        rc, out, err = self.prep("--policy-file", pol)
        self.assertEqual(rc, 3, out + err)
        self.assertIn("GATE: push_branch ASK gate-ask", out)
        self.assertIn("STOP:", out)
        self.assertNotIn("prep again", out)  # a re-run would be refused (R10)
        self.assertNotEqual(git(self.bare, "rev-parse", "-q", "--verify",
                                "refs/heads/release/1.2.0").returncode, 0)
        rel = RL.Release.load(self.root, "1.2.0")
        self.assertEqual(rel.status, "prepped")
        self.assertFalse(rel.prep["pushed"])
        self.assertFalse(rel.prep["pr"])

    def test_prep_keeps_its_own_state_and_grant_out_of_git(self):
        # repo() excludes /.skill-contract/ already; drop that so prep's own ignoring
        # (the releases dir's `*` .gitignore, and GRANT_EXCLUDE) is what is tested.
        exclude = os.path.join(self.root, ".git", "info", "exclude")
        with open(exclude) as f:
            kept = [ln for ln in f.read().splitlines() if ln.strip() != "/.skill-contract/"]
        with open(exclude, "w") as f:
            f.write("\n".join(kept) + "\n")
        rc, out, err = self.prep()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(git(self.root, "status", "--porcelain", "--untracked-files=all")
                         .stdout, "")
        rep = CC.check_grant(self.root, "push_branch", subject=".release/recipe.json",
                             worktree=self.wt())
        self.assertEqual(rep["status"], "COVERED", rep)

    def test_a_policy_file_may_only_decline(self):
        pol = os.path.join(tmpdir(), "policy.json")
        for bad in ({"deploy": "grant"}, {"push_branch": "auto"}, ["push_tag"]):
            with self.subTest(bad=bad):
                with open(pol, "w") as f:
                    json.dump(bad, f)
                self.assertEqual(self.prep("--policy-file", pol)[0], 2)
        with open(pol, "w") as f:
            json.dump({"push_tag": "ask", "deploy_staging": "ask"}, f)
        self.assertEqual(self.prep("--policy-file", pol)[0], 0)
        st, _ = CC.load_envelope(CC.grant_for_subject(self.root, ".release/recipe.json"))
        gp = st["predicate"]["payload"]["gate_policy"]
        self.assertEqual(gp["push_tag"], "ask")
        self.assertEqual(gp["deploy_staging"], "ask")

    def test_a_failing_pr_command_stops_with_the_pr_pending(self):
        rc, out, err = self.prep(pr=["false"])
        self.assertEqual(rc, 3, out + err)
        rel = RL.Release.load(self.root, "1.2.0")
        self.assertTrue(rel.prep["pushed"])
        self.assertFalse(rel.prep["pr"])


if __name__ == "__main__":
    unittest.main()
