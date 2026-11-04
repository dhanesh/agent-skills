"""R29 (G3): a verified release that holds a squash-merge commit whose subject ends with
`(#<n>)`, where n is the PR the item's run opened, resolves the item. Stdlib, offline."""
import json, os, shlex, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapters as AD
import intake as IN
from intake_testkit import repo
from test_intake_sync import (CFG, git, intake, issue, items, plan_env, release_env,
                              run_env)


def squash_repo(root, subject, body=None):
    """main: base -> one squash commit with `subject` (and `body`) = release commit R.
    side: S, the run's own merge, never on main (a squash merge rewrote it).
    Returns (S, R)."""
    git(root, "init", "-q", "-b", "main")
    git(root, "commit", "-q", "-m", "base", "--allow-empty")
    git(root, "checkout", "-q", "-b", "side")
    git(root, "commit", "-q", "-m", "run merge", "--allow-empty")
    s = git(root, "rev-parse", "HEAD").strip()
    git(root, "checkout", "-q", "main")
    msg = ["-m", subject] + (["-m", body] if body else [])
    git(root, "commit", "-q", "--allow-empty", *msg)
    r = git(root, "rev-parse", "HEAD").strip()
    return s, r


class SquashTests(unittest.TestCase):
    def setup(self, subject, body=None, pr=42, outcome="verified"):
        self.root = repo(config=CFG)
        self.s, self.r = squash_repo(self.root, subject, body)
        self.iid = issue(self.root, "42")
        rc, out = intake(self.root, "pick", self.iid, "--by", "Dee")
        self.assertEqual(rc, 0, out)
        path, st = plan_env(self.root, [self.iid])
        extra = {"pr": {"number": pr}} if pr is not None else {}
        run_env(self.root, path, st, [{"id": "T1", "status": "proven", "merge_commit": self.s}],
                extra=extra)
        release_env(self.root, "2.0.0", self.r, outcome)

    def follow_next(self):
        """Sync, run the printed NEXT line exactly as a user would, sync again."""
        rc, out = intake(self.root, "sync")
        self.assertEqual(rc, 0, out)
        nxt = [l for l in out.splitlines() if l.startswith("NEXT: git rev-list")]
        self.assertEqual(len(nxt), 1, out)
        left, right = nxt[0][len("NEXT: "):].split(" | intake ")
        hist = git(self.root, *shlex.split(left)[1:])
        rc, out = intake(self.root, *shlex.split(right), stdin=hist)
        self.assertEqual(rc, 0, out)
        rc, out = intake(self.root, "sync")
        self.assertEqual(rc, 0, out)
        return out

    def flag(self):
        out = intake(self.root, "list", "--all")[1]
        line = [l for l in out.splitlines() if l.startswith("ITEM: %s " % self.iid)][0]
        return line.split()[2]

    def assert_waits(self):
        self.assertEqual(items(self.root)[self.iid]["state"], "planned")
        self.assertEqual(self.flag(), "planned+needs-resolve")

    def test_subject_ending_with_the_runs_pr_resolves(self):
        self.setup("Add X (#42)")
        self.follow_next()
        it = items(self.root)[self.iid]
        self.assertEqual((it["state"], it["state_by"]), ("resolved", "release:2.0.0"))
        with open(os.path.join(self.root, ".skill-contract", "intake", IN.LOG_FILE)) as f:
            recs = [json.loads(l) for l in f]
        self.assertIn({"event": "resolved", "id": self.iid, "by": "release:2.0.0", "pr": 42},
                      [{k: v for k, v in r.items() if k != "at"} for r in recs])

    def test_another_pr_number_leaves_it_waiting(self):
        self.setup("Add X (#43)")
        self.follow_next()
        self.assert_waits()

    def test_exact_number_only(self):
        for subject in ("Add X (#4)", "Add X (#142)", "Add X (#042)", "Add X (#42"):
            with self.subTest(subject=subject):
                self.setup(subject)
                self.follow_next()
                self.assert_waits()

    def test_number_not_at_the_end_of_the_subject_does_not_resolve(self):
        self.setup("Revert (#42) and add X")
        self.follow_next()
        self.assert_waits()

    def test_body_line_with_the_number_does_not_resolve(self):
        self.setup("Add X", body="Squashed from (#42)")
        self.follow_next()
        self.assert_waits()

    def test_unverified_release_does_not_resolve(self):
        self.setup("Add X (#42)", outcome="rolled_back")
        rc, out = intake(self.root, "sync")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("NEXT: git rev-list", out)
        self.assertEqual(items(self.root)[self.iid]["state"], "planned")
        self.assertEqual(self.flag(), "planned+needs-release")

    def test_run_that_names_no_pr_does_not_resolve(self):
        self.setup("Add X (#42)", pr=None)
        self.follow_next()
        self.assert_waits()

    def test_history_without_subjects_does_not_resolve(self):
        self.setup("Add X (#42)")
        intake(self.root, "sync")
        hist = git(self.root, "rev-list", self.r)  # the old import: shas only
        rc, out = intake(self.root, "import", "--format", "git-rev-list", "--commit", self.r,
                         "-", stdin=hist)
        self.assertEqual(rc, 0, out)
        intake(self.root, "sync")
        self.assert_waits()

    def test_malformed_pr_is_a_problem(self):
        self.setup("Add X (#42)", pr="42")
        rc, out = intake(self.root, "sync")
        self.assertEqual(rc, 3, out)
        self.assertIn("run-result pr must be null or an object with a positive number", out)
        self.assertEqual(items(self.root)[self.iid]["state"], "planned")


class AdapterTests(unittest.TestCase):
    def test_squash_pr_reads_the_end_of_the_first_line_only(self):
        self.assertEqual(AD.squash_pr("Add X (#42)"), 42)
        self.assertEqual(AD.squash_pr("Add X (#42)  "), 42)
        for s in ("Add X", "Add X (#42).", "Fix (#42) again", "Add X\n\nbody (#42)", "(#0)",
                  "Add X (# 42)", "Add X #42"):
            self.assertIsNone(AD.squash_pr(s), s)
        self.assertEqual(AD.squash_pr("Add X (#4)"), 4)

    def test_rev_list_with_subjects_and_old_headers(self):
        a, b = "a" * 40, "b" * 40
        raw = "commit %s\n%s Add X (#42)\ncommit %s\n%s base\n" % (a, a, b, b)
        ctx = {}
        _, problems = AD.adapt_git_rev_list(raw, ctx)
        self.assertEqual(problems, [])
        self.assertEqual(ctx["history_out"], [a, b])
        self.assertEqual(ctx["squash_out"], {a: 42})

    def test_plain_rev_list_still_imports(self):
        ctx = {}
        _, problems = AD.adapt_git_rev_list("%s\n%s\nnot-a-sha\n" % ("a" * 40, "b" * 40), ctx)
        self.assertEqual(problems, ["line 3: not a 40-hex sha"])
        self.assertEqual((ctx["history_out"], ctx["squash_out"]), (["a" * 40, "b" * 40], {}))

    def test_queue_refuses_a_bad_prs_list(self):
        root = repo(config=CFG)
        q = IN.Queue.load(root)
        q.histories["c" * 40] = {"imported_at": "2026-10-06T00:00:00Z", "shas": ["c" * 40],
                                 "prs": [42]}
        q.save()
        self.assertEqual(IN.Queue.load(root).histories["c" * 40]["prs"], [42])
        q.histories["c" * 40]["prs"] = [True]
        q.save()
        with self.assertRaises(IN.QueueError):
            IN.Queue.load(root)


if __name__ == "__main__":
    unittest.main()
