"""growth-review: data import, periods, the review statuses, STALE and corrections."""
import datetime
import os
import unittest

from growth_testkit import (G, PLAN, TODAY, data_lines, load, rec, records, registered,
                            repo, run, run_err, write_plan)

YDAY = "2026-10-11"  # the latest complete day on TODAY (2026-10-12, a Monday)
LAST_WEEK = "2026-W41"
LAST_MONTH = "2026-09"


def metric_line(out, name):
    return [l for l in out.splitlines() if l.startswith("METRIC: %s " % name)][0]


class Periods(unittest.TestCase):
    def test_labels_and_spans(self):
        d = datetime.date
        self.assertEqual(G.parse_period("week", "2026-W41"), (d(2026, 10, 5), d(2026, 10, 11)))
        self.assertEqual(G.parse_period("month", "2026-02"), (d(2026, 2, 1), d(2026, 2, 28)))
        self.assertEqual(G.parse_period("month", "2026-12"), (d(2026, 12, 1), d(2026, 12, 31)))
        self.assertEqual(G.parse_period("week", "2026-W53"), (d(2026, 12, 28), d(2027, 1, 3)))
        for kind, bad in (("week", "2025-W53"), ("week", "2026-W5"), ("day", "2026-02-30"),
                          ("month", "2026-13"), ("day", "2026-W41"), ("month", 202610),
                          ("week", "2026-10")):
            self.assertIsNone(G.parse_period(kind, bad), (kind, bad))

    def test_latest_complete_is_from_the_clock(self):
        t = datetime.date(2026, 10, 12)
        self.assertEqual(G.latest_complete("day", t), "2026-10-11")
        self.assertEqual(G.latest_complete("week", t), "2026-W41")
        self.assertEqual(G.latest_complete("month", t), "2026-09")
        self.assertEqual(G.latest_complete("week", datetime.date(2027, 1, 4)), "2026-W53")
        self.assertEqual(G.latest_complete("month", datetime.date(2026, 1, 1)), "2025-12")

    def test_period_end_is_utc_rfc3339(self):
        self.assertEqual(G.period_end("day", "2026-10-11"), "2026-10-12T00:00:00Z")
        self.assertEqual(G.period_end("week", "2026-W41"), "2026-10-12T00:00:00Z")
        self.assertEqual(G.period_end("month", "2026-12"), "2027-01-01T00:00:00Z")


class Import(unittest.TestCase):
    def test_needs_a_registration(self):
        root = repo()
        rc, out = load(root, rec("signups", YDAY, 120))
        self.assertEqual(rc, 2)
        self.assertIn("STOP: no registered plan", out)

    def test_problem_records_and_the_rest_import(self):
        root = registered()
        path = records(root, [
            rec("nope", YDAY, 1),                                  # unknown metric
            rec("signups", "2026-W41", 1),                         # wrong period shape
            '{"metric": "signups", "period": "2026-10-01", "value": NaN, "source": "x"}',
            '{"metric": "signups", "period": "2026-10-01", "value": Infinity, "source": "x"}',
            rec("signups", "2026-10-02", True),                    # a bool is not a number
            rec("signups", "2026-10-03", "12"),                    # a string is not a number
            rec("signups", "2026-10-14", 1),                       # 2 days after today
            rec("signups", "2026-10-04", 1, n=-1),
            {"metric": "signups", "period": "2026-10-05", "value": 1},   # no source
            "not json",
            rec("signups", "2026-10-13", 120),                     # tomorrow: not ended (M6)
            rec("churn-rate", "2026-10", 0.01, n=60),              # this month ends 10-31
            rec("signups", YDAY, 120),
        ])
        rc, out = run(root, "import", path)
        self.assertEqual(rc, 3, out)
        self.assertIn("IMPORT: added 1 corrected 0 unchanged 0 problems 12", out)
        for want in ("line 1: metric is not in the registered plan",
                     "line 2: period does not fit the metric's period day",
                     "line 3: NaN is not a finite number",
                     "line 4: Infinity is not a finite number",
                     "line 5: value must be a finite number",
                     "line 6: value must be a finite number",
                     "line 7: period has not ended: its last day is after today (UTC)",
                     "line 8: n must be a whole number",
                     "line 9: source must be a non-empty string",
                     "line 11: period has not ended: its last day is after today (UTC)",
                     "line 12: period has not ended: its last day is after today (UTC)"):
            self.assertIn("PROBLEM: " + want, out)
        self.assertNotIn("nope", out)  # problem lines never quote the record's text
        self.assertEqual(sorted(r["period"] for r in data_lines(root)), ["2026-10-11"])

    def test_reimport_is_a_no_op_and_a_correction_keeps_both(self):
        root = registered()
        self.assertEqual(load(root, rec("signups", YDAY, 70))[0], 0)
        before = data_lines(root)
        rc, out = load(root, rec("signups", YDAY, 70.0, source="other export"))
        self.assertEqual(rc, 0)
        self.assertIn("IMPORT: added 0 corrected 0 unchanged 1 problems 0", out)
        self.assertEqual(data_lines(root), before)
        rc, out = load(root, rec("signups", YDAY, 90))
        self.assertIn("IMPORT: added 0 corrected 1 unchanged 0 problems 0", out)
        log = data_lines(root)
        self.assertEqual([r["value"] for r in log], [70, 90])
        self.assertEqual([r["correction"] for r in log], [False, True])
        rc, out = run(root, "review")
        self.assertIn("METRIC: signups WARN value=90 period=2026-10-11", out)  # newer wins

    def test_a_changed_n_is_a_correction(self):
        root = registered()
        load(root, rec("churn-rate", LAST_MONTH, 0.1, n=60))
        rc, out = load(root, rec("churn-rate", LAST_MONTH, 0.1, n=40))
        self.assertIn("corrected 1", out)

    def test_import_uses_the_frozen_plan(self):
        root = registered()
        write_plan(root, PLAN + "| refunds | down | day | 3 | 5 | | |\n")
        rc, out = load(root, rec("refunds", YDAY, 9))
        self.assertEqual(rc, 3)
        self.assertIn("PROBLEM: line 1: metric is not in the registered plan", out)

    def test_stdin(self):
        root = registered()
        import io
        import sys
        from unittest import mock
        with mock.patch.object(sys, "stdin", io.StringIO('{"metric": "signups", "period": '
                                                         '"2026-10-11", "value": 5, '
                                                         '"source": "x"}\n')):
            rc, out = run(root, "import", "-")
        self.assertEqual(rc, 0, out)


class Review(unittest.TestCase):
    def review(self, *recs, plan=PLAN):
        root = registered(plan)
        if recs:
            rc, out = load(root, *recs)
            self.assertEqual(rc, 0, out)
        return run(root, "review")

    def test_ok_warn_breach_up(self):
        rc, out = self.review(rec("signups", YDAY, 120))
        self.assertIn("METRIC: signups OK value=120 period=2026-10-11", out)
        rc, out = self.review(rec("signups", YDAY, 90))
        self.assertIn("METRIC: signups WARN value=90", out)
        rc, out = self.review(rec("signups", YDAY, 70))
        self.assertIn("METRIC: signups BREACH value=70", out)
        self.assertEqual(rc, 3)

    def test_boundaries_up(self):
        rc, out = self.review(rec("signups", YDAY, 100))
        self.assertIn("METRIC: signups WARN value=100", out)  # exactly warn is not good side
        rc, out = self.review(rec("signups", YDAY, 80))
        self.assertIn("METRIC: signups BREACH value=80", out)  # on breach is a breach
        rc, out = self.review(rec("signups", YDAY, 100.5))
        self.assertIn("METRIC: signups OK value=100.5", out)

    def test_down_direction_and_boundaries(self):
        cases = ((0.01, "OK"), (0.05, "WARN"), (0.06, "WARN"), (0.08, "BREACH"), (0.2, "BREACH"))
        for v, want in cases:
            rc, out = self.review(rec("churn-rate", LAST_MONTH, v, n=60))
            self.assertIn("METRIC: churn-rate %s value=%s period=2026-09" % (want, G._fmt(v)),
                          out, v)

    def test_week(self):
        rc, out = self.review(rec("activation", LAST_WEEK, 0.25))
        self.assertIn("METRIC: activation BREACH value=0.25 period=2026-W41", out)

    def test_no_data_missing_and_below_min_n(self):
        rc, out = self.review()
        self.assertEqual(rc, 0)
        self.assertIn("METRIC: signups NO_DATA value=- period=2026-10-11", out)
        self.assertIn("REVIEW: ok=0 warn=0 breach=0 no_data=3 stale=no", out)
        rc, out = self.review(rec("churn-rate", LAST_MONTH, 0.5, n=49))
        self.assertIn("METRIC: churn-rate NO_DATA value=0.5", out)
        # M2: a record for the judged period that cannot be judged fails closed.
        self.assertIn("NOTE: churn-rate 2026-09 has a value, but n=49 is below min_n 50", out)
        self.assertEqual(rc, 3)
        rc, out = self.review(rec("churn-rate", LAST_MONTH, 0.5))  # min_n set, no n
        self.assertIn("METRIC: churn-rate NO_DATA", out)

    def test_a_stopped_export_shows_no_data_not_an_old_status(self):
        root = registered()
        load(root, rec("signups", "2026-10-09", 50), today="2026-10-10")
        rc, out = run(root, "review", today="2026-10-10")
        self.assertIn("METRIC: signups BREACH value=50 period=2026-10-09", out)
        rc, out = run(root, "review", today=TODAY)
        self.assertIn("METRIC: signups NO_DATA value=- period=2026-10-11", out)
        self.assertEqual(rc, 0)

    def test_review_line_and_exit(self):
        rc, out = self.review(rec("signups", YDAY, 120), rec("activation", LAST_WEEK, 0.35),
                              rec("churn-rate", LAST_MONTH, 0.1, n=60))
        self.assertIn("REVIEW: ok=1 warn=1 breach=1 no_data=0 stale=no", out)
        self.assertIn("NEXT: signals --source", out)
        self.assertEqual(rc, 3)
        rc, out = self.review(rec("signups", YDAY, 120))
        self.assertEqual(rc, 0)


class Stale(unittest.TestCase):
    def test_edit_after_register_is_stale_and_still_alarms(self):
        root = registered()
        load(root, rec("signups", YDAY, 70))
        write_plan(root, PLAN.replace("| 100 | 80 |", "| 100 | 50 |"))  # would hide it
        rc, out = run(root, "review")
        self.assertEqual(rc, 3)
        self.assertIn("METRIC: signups BREACH +stale value=70 period=2026-10-11", out)
        self.assertIn("METRIC: churn-rate NO_DATA +stale", out)
        self.assertIn("REVIEW: ok=0 warn=0 breach=1 no_data=2 stale=yes", out)
        self.assertIn("NOTE: stale signups edited", out)
        rc, sig, err = run_err(root, "signals", "--source", "growth")
        self.assertEqual(rc, 0)
        self.assertEqual(len(sig.splitlines()), 1)
        self.assertIn('"source_id": "signups"', sig)
        self.assertIn("STALE", err)

    def test_stale_without_breach_exits_3(self):
        root = registered()
        load(root, rec("signups", YDAY, 120))
        write_plan(root, PLAN.replace("| 100 | 80 |", "| 130 | 80 |"))
        rc, out = run(root, "review")
        self.assertEqual(rc, 3)
        self.assertIn("METRIC: signups OK +stale", out)  # frozen thresholds, not the edit
        self.assertIn("stale=yes", out)

    def test_broken_or_missing_plan_still_alarms(self):
        for broken in ("# gone\n", None):
            root = registered()
            load(root, rec("signups", YDAY, 70))
            path = os.path.join(root, "growth", "metrics.md")
            if broken is None:
                os.unlink(path)
            else:
                write_plan(root, broken)
            rc, out = run(root, "review")
            self.assertEqual(rc, 3)
            self.assertIn("METRIC: signups BREACH +stale", out)
            self.assertIn("stale=yes", out)
            rc, sig, err = run_err(root, "signals", "--source", "growth")
            self.assertEqual(len(sig.splitlines()), 1, broken)

    def test_removed_and_added_metrics(self):
        root = registered()
        load(root, rec("signups", YDAY, 70))
        text = PLAN.replace("| signups | up | day | 100 | 80 | | 3 |\n", "")
        write_plan(root, text + "| refunds | down | day | 3 | 5 | | |\n")
        rc, out = run(root, "review")
        self.assertIn("METRIC: signups BREACH +stale", out)  # removed, still reviewed
        self.assertIn("NOTE: stale signups removed from growth/metrics.md", out)
        self.assertIn("NOTE: stale refunds added to growth/metrics.md, not registered", out)
        self.assertNotIn("METRIC: refunds", out)

    def test_prose_edit_is_not_stale(self):
        root = registered()
        write_plan(root, PLAN.replace("Prose here", "New prose"))
        rc, out = run(root, "review")
        self.assertIn("stale=no", out)

    def test_register_again_with_reason_clears_it(self):
        root = registered()
        load(root, rec("signups", YDAY, 70))
        write_plan(root, PLAN.replace("| 100 | 80 |", "| 100 | 50 |"))
        rc, out = run(root, "register", "--by", "Dana", "--reason", "new baseline")
        self.assertEqual(rc, 0, out)
        rc, out = run(root, "review")
        self.assertIn("METRIC: signups WARN value=70", out)
        self.assertIn("stale=no", out)
        self.assertEqual(rc, 0)


class Corrections(unittest.TestCase):
    def test_correction_out_of_breach_is_printed(self):
        root = registered()
        load(root, rec("signups", YDAY, 70))
        load(root, rec("signups", YDAY, 120))
        rc, out = run(root, "review")
        self.assertIn("CORRECTED: signups 2026-10-11 was=70 now=120 OK", out)
        self.assertIn("METRIC: signups OK value=120", out)
        # M2: the correction concerns the judged period, so the review fails closed.
        self.assertEqual(rc, 3)

    def test_correction_into_breach_or_within_breach_is_not(self):
        root = registered()
        load(root, rec("signups", YDAY, 120))
        load(root, rec("signups", YDAY, 70))
        load(root, rec("signups", YDAY, 60))
        rc, out = run(root, "review")
        self.assertNotIn("CORRECTED:", out)
        self.assertIn("METRIC: signups BREACH value=60", out)

    def test_older_period_correction_is_kept_in_view(self):
        root = registered()
        load(root, rec("signups", "2026-10-01", 10))
        load(root, rec("signups", "2026-10-01", 95))
        rc, out = run(root, "review")
        self.assertIn("CORRECTED: signups 2026-10-01 was=10 now=95 WARN", out)


class Format(unittest.TestCase):
    def test_fmt(self):
        self.assertEqual(G._fmt(0.1 + 0.2), "0.3")
        self.assertEqual(G._fmt(70.0), "70")
        self.assertEqual(G._fmt(70), "70")
        self.assertEqual(G._fmt(1e20), "1e+20")


if __name__ == "__main__":
    unittest.main()
