"""growth-review: the metrics plan, register, the --by and --reason rules, the test clock."""
import json
import os
import unittest
from unittest import mock

from growth_testkit import G, PLAN, TODAY, repo, run, write_plan

ROW = "| signups | up | day | 100 | 80 | | 3 |"


def state(root):
    with open(os.path.join(root, ".skill-contract", "growth", "plan.json"), encoding="utf-8") as f:
        return json.load(f)


def plan_with(row, header="| metric | direction | period | warn | breach | min_n | severity |"):
    return "# Plan\n\n%s\n|---|---|---|---|---|---|---|\n%s\n" % (header, row)


class Lint(unittest.TestCase):
    def lint(self, text):
        root = repo(text)
        return run(root, "lint")

    def test_good_table(self):
        rc, out = self.lint(PLAN)
        self.assertEqual(rc, 0, out)
        self.assertIn("LINT: ok 3 metrics", out)

    def test_parsed_defaults(self):
        p = G.parse_plan(PLAN)
        self.assertEqual(p["problems"], [])
        self.assertEqual(p["metrics"]["activation"]["severity"], 3)
        self.assertIsNone(p["metrics"]["signups"]["min_n"])
        self.assertEqual(p["metrics"]["churn-rate"]["min_n"], 50)

    def test_optional_columns_may_be_absent(self):
        text = ("| metric | direction | period | warn | breach |\n|---|---|---|---|---|\n"
                "| signups | up | day | 100 | 80 |\n")
        p = G.parse_plan(text)
        self.assertEqual(p["problems"], [])
        self.assertEqual(p["metrics"]["signups"]["severity"], 3)

    def test_bad_direction(self):
        rc, out = self.lint(plan_with("| signups | sideways | day | 100 | 80 | | |"))
        self.assertEqual(rc, 2)
        self.assertIn("direction `sideways` must be up or down", out)

    def test_breach_on_wrong_side_of_warn(self):
        rc, out = self.lint(plan_with("| signups | up | day | 80 | 100 | | |"))
        self.assertEqual(rc, 2)
        self.assertIn("for direction up, breach must be at most warn", out)
        rc, out = self.lint(plan_with("| churn | down | day | 0.08 | 0.05 | | |"))
        self.assertEqual(rc, 2)
        self.assertIn("for direction down, breach must be at least warn", out)

    def test_breach_equal_to_warn_is_allowed(self):
        self.assertEqual(self.lint(plan_with("| signups | up | day | 80 | 80 | | |"))[0], 0)

    def test_duplicate_metric(self):
        rc, out = self.lint(plan_with(ROW + "\n" + ROW))
        self.assertEqual(rc, 2)
        self.assertIn("metric signups appears twice", out)

    def test_bad_names(self):
        for name in ("Signups", "sign_ups", "-x", "a" * 65, "sign--ups"):
            rc, out = self.lint(plan_with("| %s | up | day | 100 | 80 | | |" % name))
            self.assertEqual(rc, 2, name)
            self.assertIn("must be kebab-case", out)

    def test_bad_period_numbers_severity_min_n(self):
        cases = {"| s | up | hour | 1 | 0 | | |": "period `hour`",
                 "| s | up | day | lots | 0 | | |": "warn `lots` must be a number",
                 "| s | up | day | 1 | 0 | | 5 |": "severity `5` must be 1 to 4",
                 "| s | up | day | 1 | 0 | 2.5 | |": "min_n `2.5` must be a whole number",
                 "| s | up | day | 1 | 0 | |": "6 cells, the header has 7"}
        for row, want in cases.items():
            rc, out = self.lint(plan_with(row))
            self.assertEqual(rc, 2, row)
            self.assertIn(want, out, row)

    def test_columns(self):
        rc, out = self.lint(plan_with("| s | up | day | 1 | 0 | | |",
                                      header="| metric | direction | period | warn | breech "
                                             "| min_n | severity |"))
        self.assertEqual(rc, 2)
        self.assertIn("unknown column `breech`", out)
        self.assertIn("missing column breach", out)
        rc, out = self.lint("# Plan\n\nno table here\n")
        self.assertEqual(rc, 2)
        self.assertIn("no metrics table", out)

    def test_empty_table_and_two_tables(self):
        rc, out = self.lint("| metric | direction | period | warn | breach |\n|---|---|---|---|---|\n")
        self.assertEqual(rc, 2)
        self.assertIn("has no rows", out)
        rc, out = self.lint(PLAN + "\n" + PLAN.split("\n", 4)[4])
        self.assertEqual(rc, 2)
        self.assertIn("more than one metrics table", out)

    def test_prose_edit_keeps_the_hash(self):
        a = G.plan_sha(G.parse_plan(PLAN)["metrics"])
        b = G.plan_sha(G.parse_plan(PLAN.replace("Prose here", "Other words"))["metrics"])
        c = G.plan_sha(G.parse_plan(PLAN.replace("| 100 | 80 |", "| 100 | 79 |"))["metrics"])
        d = G.plan_sha(G.parse_plan(PLAN.replace("| 100 | 80 |", "| 100.0 | 80 |"))["metrics"])
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(a, d)  # the same number written differently is the same threshold


class Register(unittest.TestCase):
    def test_register_then_no_change(self):
        root = repo()
        rc, out = run(root, "register", "--by", "Dana")
        self.assertEqual(rc, 0, out)
        self.assertIn("REGISTERED:", out)
        rc, out = run(root, "register", "--by", "Dana")
        self.assertEqual(rc, 0)
        self.assertIn("no change since the last registration", out)
        st = state(root)
        self.assertEqual(len(st["registrations"]), 1)
        self.assertEqual(st["registrations"][0]["by"], "Dana")

    def test_agent_names_refused(self):
        root = repo()
        for by in ("agent:claude", "Agent: Claude", "  agent:x"):
            rc, out = run(root, "register", "--by", by)
            self.assertEqual(rc, 2, by)
            self.assertIn("STOP: an agent: name cannot register thresholds", out)
        self.assertFalse(os.path.exists(os.path.join(root, ".skill-contract", "growth",
                                                     "plan.json")))

    def test_empty_or_hidden_name_refused(self):
        root = repo()
        for by in ("", "  ", "Da‮na"):
            self.assertEqual(run(root, "register", "--by", by)[0], 2)

    def test_bad_plan_not_registered(self):
        root = repo(plan_with("| signups | up | day | 80 | 100 | | |"))
        rc, out = run(root, "register", "--by", "Dana")
        self.assertEqual(rc, 2)
        self.assertIn("LINT: fail", out)
        self.assertIn("NEXT: fix growth/metrics.md", out)

    def test_missing_plan(self):
        root = repo(plan=None)
        rc, out = run(root, "register", "--by", "Dana")
        self.assertEqual(rc, 2)
        self.assertIn("STOP: cannot read growth/metrics.md", out)

    def test_register_again_after_an_edit_needs_a_reason(self):
        root = repo()
        run(root, "register", "--by", "Dana")
        write_plan(root, PLAN.replace("| 100 | 80 |", "| 100 | 50 |"))
        rc, out = run(root, "register", "--by", "Dana")
        self.assertEqual(rc, 2)
        self.assertIn("registering again needs --reason", out)
        self.assertIn("NOTE: stale signups edited: registered up day warn=100 breach=80", out)
        rc, out = run(root, "register", "--by", "Dana", "--reason", "new pricing page")
        self.assertEqual(rc, 0, out)
        st = state(root)
        self.assertEqual([r["reason"] for r in st["registrations"]], [None, "new pricing page"])

    def test_state_dir_is_ignored(self):
        root = repo()
        run(root, "register", "--by", "Dana")
        with open(os.path.join(root, ".skill-contract", "growth", ".gitignore")) as f:
            self.assertIn("*", f.read().splitlines())

    def test_negated_gitignore_refused(self):
        root = repo()
        d = os.path.join(root, ".skill-contract", "growth")
        os.makedirs(d)
        with open(os.path.join(d, ".gitignore"), "w") as f:
            f.write("*\n!data.jsonl\n")
        rc, out = run(root, "register", "--by", "Dana")
        self.assertEqual(rc, 2)
        self.assertIn("cannot be shown to be git-ignored", out)

    def test_symlinked_state_dir_refused(self):
        root = repo()
        os.makedirs(os.path.join(root, ".skill-contract"))
        os.symlink(root, os.path.join(root, ".skill-contract", "growth"))
        rc, out = run(root, "register", "--by", "Dana")
        self.assertEqual(rc, 2)
        self.assertIn("is a symlink", out)


class TestClock(unittest.TestCase):
    def test_today_refused_outside_the_test_switch(self):
        root = repo()
        env = {k: v for k, v in os.environ.items() if k != G.TEST_CLOCK_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = G.main(["--root", root, "--today", TODAY, "register", "--by", "Dana"])
        self.assertEqual(rc, 2)
        self.assertIn("STOP: --today is for tests only", buf.getvalue())
        self.assertNotIn(G.TEST_CLOCK_ENV, buf.getvalue())  # the guard's switch stays unnamed
        self.assertFalse(os.path.exists(os.path.join(root, ".skill-contract")))

    def test_bad_today(self):
        self.assertEqual(run(repo(), "review", today="2026-02-30")[0], 2)

    def test_clock_is_utc(self):
        self.assertEqual(G._utc_today(), G.datetime.datetime.now(G.datetime.timezone.utc).date())


if __name__ == "__main__":
    unittest.main()
