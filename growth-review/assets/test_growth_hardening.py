"""growth-review: the review-round fixes. A deleted registration (M1), corrections and
records that cannot be judged (M2), non-ASCII digits (M3), huge numbers (M4), a loud
`signals` (M5), unfinished periods (M6), corrupt registrations (L1), agent lookalike names
(L2), who registered (L3) and symlinked state files (L4)."""
import datetime
import json
import os
import re
import tempfile
import unittest

from growth_testkit import (G, PLAN, data_lines, load, rec, records, registered, repo, run,
                            run_err, write_plan)

YDAY = "2026-10-11"
LAST_MONTH = "2026-09"


def gdir(root):
    return os.path.join(root, ".skill-contract", "growth")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def state(root):
    return json.loads(read(os.path.join(gdir(root), "plan.json")))


class DeletedRegistration(unittest.TestCase):
    """M1: plan.json deleted or emptied by hand cannot silently reset the thresholds."""

    def test_deleted_plan_state_needs_a_reason(self):
        root = registered()
        os.unlink(os.path.join(gdir(root), "plan.json"))
        rc, out = run(root, "register", "--by", "Dana")
        self.assertEqual(rc, 2, out)
        self.assertIn("NOTE: plan.json holds no registration, but log.jsonl records 1 "
                      "earlier registration", out)
        self.assertIn("STOP: the registration state was deleted or emptied by hand", out)
        self.assertNotIn("REGISTERED:", out)
        self.assertFalse(os.path.exists(os.path.join(gdir(root), "plan.json")))
        rc, out = run(root, "register", "--by", "Dana", "--reason", "plan.json was lost")
        self.assertEqual(rc, 0, out)
        self.assertEqual([r["reason"] for r in state(root)["registrations"]],
                         ["plan.json was lost"])

    def test_emptied_registrations_need_a_reason(self):
        root = registered()
        write(os.path.join(gdir(root), "plan.json"), '{"version": 1, "registrations": []}\n')
        rc, out = run(root, "register", "--by", "Dana")
        self.assertEqual(rc, 2, out)
        self.assertIn("STOP: the registration state was deleted or emptied by hand", out)
        self.assertEqual(state(root)["registrations"], [])

    def test_data_without_a_registration_needs_a_reason(self):
        root = registered()
        load(root, rec("signups", YDAY, 70))
        os.unlink(os.path.join(gdir(root), "plan.json"))
        os.unlink(os.path.join(gdir(root), "log.jsonl"))
        rc, out = run(root, "register", "--by", "Dana")
        self.assertEqual(rc, 2, out)
        self.assertIn("but data.jsonl holds imported data", out)

    def test_a_fresh_repo_registers_without_a_reason(self):
        root = repo()
        self.assertEqual(run(root, "register", "--by", "Dana")[0], 0)

    def test_review_fails_closed_when_the_state_is_gone(self):
        root = registered()
        load(root, rec("signups", YDAY, 120))
        os.unlink(os.path.join(gdir(root), "plan.json"))
        rc, out = run(root, "review")
        self.assertEqual(rc, 3, out)
        self.assertIn("NOTE: log.jsonl records 1 registration, but plan.json holds none", out)
        self.assertIn("STOP: no registered plan", out)

    def test_review_flags_a_hand_swapped_registration(self):
        root = registered()
        load(root, rec("signups", YDAY, 70))
        st = state(root)
        loose = G.parse_plan(PLAN.replace("| 100 | 80 |", "| 60 | 50 |"))["metrics"]
        st["registrations"][-1].update(metrics=loose, sha256=G.plan_sha(loose))
        write(os.path.join(gdir(root), "plan.json"), json.dumps(st))
        rc, out = run(root, "review")
        self.assertIn("METRIC: signups OK", out)  # the swapped thresholds hide the breach...
        self.assertIn("is not the latest one in log.jsonl", out)
        self.assertEqual(rc, 3, out)               # ...but the review still fails closed
        rc, sig, err = run_err(root, "signals", "--source", "growth")
        self.assertIn("is not the latest one in log.jsonl", err)

    def test_review_flags_a_deleted_log(self):
        root = registered()
        load(root, rec("signups", YDAY, 120))
        self.assertEqual(run(root, "review")[0], 0)
        os.unlink(os.path.join(gdir(root), "log.jsonl"))
        rc, out = run(root, "review")
        self.assertEqual(rc, 3, out)
        self.assertIn("that log.jsonl does not record", out)

    def test_a_reasoned_reregistration_keeps_log_and_state_in_step(self):
        root = registered()
        write_plan(root, PLAN.replace("| 100 | 80 |", "| 100 | 50 |"))
        self.assertEqual(run(root, "register", "--by", "Dana", "--reason", "x")[0], 0)
        rc, out = run(root, "review")
        self.assertNotIn("log.jsonl", out)
        self.assertEqual(rc, 0, out)


class CorrectionsFailClosed(unittest.TestCase):
    """M2: an n-only correction or a missing n cannot quietly turn BREACH into exit 0."""

    def test_n_only_correction_prints_n_and_exits_3(self):
        root = registered()
        load(root, rec("churn-rate", LAST_MONTH, 0.1, n=60))
        self.assertEqual(run(root, "review")[0], 3)
        load(root, rec("churn-rate", LAST_MONTH, 0.1, n=40))
        rc, out = run(root, "review")
        self.assertIn("METRIC: churn-rate NO_DATA value=0.1 period=2026-09", out)
        self.assertIn("CORRECTED: churn-rate 2026-09 was=0.1 now=0.1 NO_DATA n was=60 now=40",
                      out)
        self.assertIn("REVIEW: ok=0 warn=0 breach=0 no_data=3 stale=no", out)
        self.assertEqual(rc, 3, out)

    def test_value_only_correction_has_no_n_part(self):
        root = registered()
        load(root, rec("signups", YDAY, 70))
        load(root, rec("signups", YDAY, 120))
        out = run(root, "review")[1]
        line = [l for l in out.splitlines() if l.startswith("CORRECTED:")][0]
        self.assertEqual(line, "CORRECTED: signups 2026-10-11 was=70 now=120 OK")

    def test_record_without_n_for_min_n_exits_3(self):
        root = registered()
        load(root, rec("churn-rate", LAST_MONTH, 0.5))
        rc, out = run(root, "review")
        self.assertIn("NOTE: churn-rate 2026-09 has a value, but n=- is below min_n 50", out)
        self.assertEqual(rc, 3, out)

    def test_correction_of_an_older_period_keeps_exit_0(self):
        root = registered()
        load(root, rec("signups", "2026-10-01", 10))
        load(root, rec("signups", "2026-10-01", 95))
        load(root, rec("signups", YDAY, 120), rec("churn-rate", LAST_MONTH, 0.01, n=60))
        rc, out = run(root, "review")
        self.assertIn("CORRECTED: signups 2026-10-01 was=10 now=95 WARN", out)
        self.assertEqual(rc, 0, out)  # only the judged period fails closed


class AsciiDigits(unittest.TestCase):
    """M3: digits other than ASCII 0-9 are refused in data and plan."""

    def test_data_periods(self):
        root = registered()
        rc, out = load(root, rec("signups", "\uff12\uff10\uff12\uff16-\uff11\uff10-\uff11\uff11", 1),
                       rec("signups", "\u0662\u0660\u0662\u0666-10-11", 1),
                       rec("activation", "2026-W\uff14\uff11", 1),
                       rec("churn-rate", "2026-\uff10\uff19", 1, n=60))
        self.assertEqual(rc, 3, out)
        self.assertEqual(out.count("period does not fit"), 4, out)
        self.assertEqual(data_lines(root), [])

    def test_parsers(self):
        self.assertIsNone(G.parse_period("day", "\uff12\uff10\uff12\uff16-10-11"))
        self.assertIsNone(G._number("\uff11\uff10\uff10"))
        self.assertIsNone(G._today_arg("\uff12\uff10\uff12\uff16-10-12"))
        self.assertEqual(G._number("100"), 100.0)

    def test_plan_thresholds_and_min_n(self):
        for row, want in (("| s | up | day | \uff11\uff10\uff10 | 80 | | |", "warn"),
                          ("| s | up | day | 100 | \u0668\u0660 | | |", "breach"),
                          ("| s | up | day | 100 | 80 | \uff15\uff10 | |", "min_n")):
            p = G.parse_plan("| metric | direction | period | warn | breach | min_n | severity |"
                             "\n|---|---|---|---|---|---|---|\n" + row + "\n")
            self.assertEqual(p["metrics"], {}, row)
            self.assertTrue(any(want in x for x in p["problems"]), (row, p["problems"]))
            root = repo("| metric | direction | period | warn | breach | min_n | severity |"
                        "\n|---|---|---|---|---|---|---|\n" + row + "\n")
            rc, out = run(root, "lint")
            self.assertEqual(rc, 2, out)


class HugeNumbers(unittest.TestCase):
    """M4: a number too large for a float is a PROBLEM line, never a crash."""

    def test_huge_integer_value(self):
        root = registered()
        huge = '{"metric": "signups", "period": "2026-10-10", "value": %s, "source": "x"}' % (
            "9" * 400)
        rc, out = run(root, "import", records(root, [huge, rec("signups", YDAY, 120)]))
        self.assertEqual(rc, 3, out)
        self.assertIn("PROBLEM: line 1: value must be a finite number", out)
        self.assertIn("IMPORT: added 1 corrected 0 unchanged 0 problems 1", out)
        self.assertEqual([r["period"] for r in data_lines(root)], [YDAY])

    def test_huge_n_and_float_overflow(self):
        root = registered()
        rc, out = run(root, "import", records(root, [
            rec("churn-rate", LAST_MONTH, 0.1, n=10 ** 400),
            '{"metric": "signups", "period": "2026-10-10", "value": 1e400, "source": "x"}']))
        self.assertEqual(rc, 3, out)
        self.assertIn("PROBLEM: line 1: n must be a whole number", out)
        self.assertIn("PROBLEM: line 2: value must be a finite number", out)
        self.assertEqual(data_lines(root), [])

    def test_huge_thresholds_and_min_n(self):
        head = ("| metric | direction | period | warn | breach | min_n | severity |\n"
                "|---|---|---|---|---|---|---|\n")
        for row, want in (("| s | up | day | %s | 80 | | |" % ("9" * 400), "warn"),
                          ("| s | up | day | 100 | 80 | %s | |" % ("9" * 5000), "min_n")):
            rc, out = run(repo(head + row + "\n"), "lint")
            self.assertEqual(rc, 2, out)
            self.assertIn("PROBLEM: line 3: %s" % want, out)


class LoudSignals(unittest.TestCase):
    """M5 and L1: signals ends with SIGNALS: <k> on stderr, and fails with empty stdout."""

    def test_signals_count_line(self):
        root = registered()
        load(root, rec("signups", YDAY, 70), rec("activation", "2026-W41", 0.1))
        rc, out, err = run_err(root, "signals", "--source", "growth")
        self.assertEqual(rc, 0)
        self.assertEqual(len(out.splitlines()), 2)
        self.assertEqual(err.splitlines()[-1], "SIGNALS: 2")
        self.assertNotIn("SIGNALS:", out)
        review = run(root, "review")[1]
        self.assertIn("breach=2", review)

    def test_signals_fails_loud_on_missing_or_corrupt_state(self):
        fresh = repo()
        rc, out, err = run_err(fresh, "signals", "--source", "growth")
        self.assertEqual((rc, out), (2, ""))
        self.assertNotIn("SIGNALS:", err)
        for text in ("{", '{"registrations": [{}]}', '{"registrations": {}}', "[]"):
            root = registered()
            load(root, rec("signups", YDAY, 70))
            write(os.path.join(gdir(root), "plan.json"), text)
            rc, out, err = run_err(root, "signals", "--source", "growth")
            self.assertEqual((rc, out), (2, ""), text)
            self.assertIn("STOP:", err)
            self.assertNotIn("SIGNALS:", err)


class UnfinishedPeriods(unittest.TestCase):
    """M6: a period whose last day is after today (UTC) is refused."""

    def test_day(self):
        root = registered()
        rc, out = load(root, rec("signups", "2026-10-12", 70))  # today: east-of-UTC export
        self.assertEqual(rc, 0, out)
        rc, out = load(root, rec("signups", "2026-10-13", 120))  # tomorrow
        self.assertEqual(rc, 3, out)
        self.assertIn("PROBLEM: line 1: period has not ended", out)
        self.assertEqual([r["period"] for r in data_lines(root)], ["2026-10-12"])
        rc, out = run(root, "review", today="2026-10-13")
        self.assertIn("METRIC: signups BREACH value=70 period=2026-10-12", out)

    def test_week_and_month(self):
        root = registered()
        # 2026-W42 runs 10-12 to 10-18.
        self.assertEqual(load(root, rec("activation", "2026-W42", 0.5), today="2026-10-17")[0], 3)
        self.assertEqual(load(root, rec("activation", "2026-W42", 0.5), today="2026-10-18")[0], 0)
        self.assertEqual(load(root, rec("churn-rate", "2026-10", 0.01, n=60),
                              today="2026-10-30")[0], 3)
        self.assertEqual(load(root, rec("churn-rate", "2026-10", 0.01, n=60),
                              today="2026-10-31")[0], 0)


class CorruptRegistration(unittest.TestCase):
    """L1: a hand-edited registration entry is STOP, exit 2, never a traceback."""

    def entries(self):
        good = G.parse_plan(PLAN)["metrics"]
        sha = G.plan_sha(good)
        ok = {"sha256": sha, "by": "Dana", "at": "2026-10-12T00:00:00Z", "reason": None,
              "metrics": good}
        bad_metric = json.loads(json.dumps(ok))
        del bad_metric["metrics"]["signups"]["warn"]
        wrong_sha = dict(ok, sha256="0" * 64)
        return [{}, [], dict(ok, sha256=None), dict(ok, by=""), dict(ok, at=5),
                dict(ok, metrics={}), dict(ok, metrics=[]), bad_metric, wrong_sha,
                dict(ok, reason=7)]

    def test_every_command_stops(self):
        for entry in self.entries():
            root = registered()
            write(os.path.join(gdir(root), "plan.json"), json.dumps({"registrations": [entry]}))
            for argv in (("review",), ("register", "--by", "Dana"),
                         ("import", records(root, [rec("signups", YDAY, 1)]))):
                rc, out = run(root, *argv)
                self.assertEqual(rc, 2, (entry, argv, out))
                self.assertIn("STOP: `cannot read .skill-contract/growth/plan.json", out)
            rc, out, err = run_err(root, "signals", "--source", "growth")
            self.assertEqual((rc, out), (2, ""), entry)


class AgentLookalikes(unittest.TestCase):
    """L2: lookalike agent names are refused; human names still register."""

    def test_variants_refused(self):
        for by in ("agent\uff1aclaude", "\uff41\uff47\uff45\uff4e\uff54:claude",
                   "\u0430gent:claude", "ag\u0435nt:claude", "agent :claude", "agent-claude",
                   "agent/claude", "agent_claude", "AGENT-Claude", " \u0410GENT:x"):
            root = repo()
            rc, out = run(root, "register", "--by", by)
            self.assertEqual(rc, 2, by)
            self.assertIn("STOP: an agent: name cannot register", out)
            self.assertFalse(os.path.exists(os.path.join(gdir(root), "plan.json")), by)

    def test_human_names_pass(self):
        for by in ("Dana", "Agent Smith", "Reagent:x", "Agentha Ray"):
            self.assertEqual(run(repo(), "register", "--by", by)[0], 0, by)


class WhoRegistered(unittest.TestCase):
    """L3: REGISTERED: carries the time; review names who, when and why."""

    def test_registered_line_and_review_note(self):
        root = repo()
        rc, out = run(root, "register", "--by", "Dana")
        m = re.search(r"^REGISTERED: ([0-9a-f]{12}) by `Dana` at "
                      r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ), 3 metrics$", out, re.M)
        self.assertIsNotNone(m, out)
        out = run(root, "review")[1]
        self.assertIn("NOTE: registered %s by `Dana` at %s\n" % (m.group(1), m.group(2)), out)
        write_plan(root, PLAN.replace("| 100 | 80 |", "| 100 | 50 |"))
        run(root, "register", "--by", "Lee", "--reason", "new baseline")
        out = run(root, "review")[1]
        self.assertRegex(out, r"NOTE: registered [0-9a-f]{12} by `Lee` at \S+Z reason "
                              r"`new baseline`\n")
        self.assertEqual(state(root)["registrations"][-1]["at"],
                         re.search(r"by `Lee` at (\S+Z)", out).group(1))


class SymlinkedStateFiles(unittest.TestCase):
    """L4: data, log and lock files that are symlinks are refused, nothing written."""

    def outside(self):
        d = tempfile.mkdtemp(prefix="growth-outside-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        path = os.path.join(d, "target")
        write(path, "untouched\n")
        return path

    def test_symlinked_data_file(self):
        root = registered()
        target = self.outside()
        os.symlink(target, os.path.join(gdir(root), "data.jsonl"))
        rc, out = load(root, rec("signups", YDAY, 70))
        self.assertEqual(rc, 2, out)
        self.assertIn("data.jsonl is a symlink", out)
        self.assertEqual(read(target), "untouched\n")
        rc, out = run(root, "review")
        self.assertEqual(rc, 2, out)
        self.assertIn("data.jsonl is a symlink", out)

    def test_symlinked_log_file_refused_before_any_write(self):
        root = repo()
        os.makedirs(gdir(root))
        write(os.path.join(gdir(root), ".gitignore"), "*\n")
        target = self.outside()
        os.symlink(target, os.path.join(gdir(root), "log.jsonl"))
        rc, out = run(root, "register", "--by", "Dana")
        self.assertEqual(rc, 2, out)
        self.assertIn("log.jsonl is a symlink", out)
        self.assertEqual(read(target), "untouched\n")
        self.assertFalse(os.path.exists(os.path.join(gdir(root), "plan.json")))

    def test_symlinked_lock_file(self):
        root = repo()
        os.makedirs(gdir(root))
        write(os.path.join(gdir(root), ".gitignore"), "*\n")
        target = self.outside()
        os.symlink(target, os.path.join(gdir(root), ".lock"))
        rc, out = run(root, "register", "--by", "Dana")
        self.assertEqual(rc, 2, out)
        self.assertIn(".lock is a symlink", out)
        self.assertEqual(read(target), "untouched\n")

    def test_append_refuses_a_symlink_directly(self):
        root = registered()
        target = self.outside()
        os.symlink(target, os.path.join(gdir(root), "data.jsonl"))
        with self.assertRaises(G.StateError):
            G._append(root, G.GROWTH_DIR + "/" + G.DATA_FILE, [{"x": 1}])
        self.assertEqual(read(target), "untouched\n")


if __name__ == "__main__":
    unittest.main()
