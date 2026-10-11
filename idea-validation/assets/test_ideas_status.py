"""Assumption statuses and the computed verdict (spec 1.3)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ideas_testkit import (GOOD, record, registered, run, statuses, tree,  # noqa: E402
                           verdict, write_brief)


def status(root, today="2026-10-20", slug="flat"):
    rc, out = run(root, "status", slug, today=today)
    assert rc == 0, out
    return statuses(out), verdict(out), out


class Statuses(unittest.TestCase):
    def test_open_when_no_result_and_deadline_ahead(self):
        st, v, _ = status(registered())
        self.assertEqual(st, {"A1": "open", "A2": "open", "A3": "open"})
        self.assertEqual(v, "PENDING")

    def test_pass_on_time_experiment(self):
        root = registered()
        self.assertEqual(record(root, "A1", 7)[0], 0)
        st, _, out = status(root)
        self.assertEqual(st["A1"], "pass")
        self.assertIn("ASSUMPTION: A1 pass 7", out)

    def test_critical_desk_pass_needs_experiment(self):
        root = registered()
        record(root, "A1", 7, kind="desk", by="agent:claude")
        self.assertEqual(status(root)[0]["A1"], "needs-experiment")

    def test_normal_desk_pass_is_pass(self):
        root = registered()
        record(root, "A3", 3, kind="desk", by="agent:claude")
        self.assertEqual(status(root)[0]["A3"], "pass")

    def test_fail_any_kind(self):
        for kind, by in (("desk", "agent:claude"), ("experiment", "Dana")):
            with self.subTest(kind=kind):
                root = registered()
                record(root, "A1", 1, kind=kind, by=by)
                self.assertEqual(status(root)[0]["A1"], "fail")

    def test_late_kill_is_still_fail(self):
        root = registered()
        record(root, "A1", 1, observed="2026-12-04", today="2026-12-05")
        self.assertEqual(status(root, today="2026-12-05")[0]["A1"], "fail")

    def test_inconclusive_between_thresholds(self):
        root = registered()
        record(root, "A1", 4)
        self.assertEqual(status(root)[0]["A1"], "inconclusive")

    def test_expired_without_result(self):
        self.assertEqual(status(registered(), today="2026-12-01")[0]["A1"], "expired")

    def test_deadline_day_is_still_open(self):
        self.assertEqual(status(registered(), today="2026-11-30")[0]["A1"], "open")

    def test_inconclusive_after_deadline_is_expired(self):
        root = registered()
        record(root, "A1", 4)
        self.assertEqual(status(root, today="2026-12-01")[0]["A1"], "expired")

    def test_needs_experiment_after_deadline_is_expired(self):
        root = registered()
        record(root, "A1", 7, kind="desk", by="agent:claude")
        self.assertEqual(status(root, today="2026-12-01")[0]["A1"], "expired")

    def test_late_pass_does_not_count(self):
        root = registered()
        self.assertEqual(record(root, "A1", 8, observed="2026-12-02", today="2026-12-03")[0], 0)
        self.assertEqual(status(root, today="2026-12-03")[0]["A1"], "expired")

    def test_latest_by_observed_date_wins(self):
        root = registered()
        record(root, "A1", 1, observed="2026-10-18")   # later observation, recorded first
        record(root, "A1", 8, observed="2026-10-10")
        self.assertEqual(status(root)[0]["A1"], "fail")

    def test_experiment_pass_then_desk_pass_stays_pass(self):
        # Chosen behaviour: a later desk pass does not undo an on-time experiment pass.
        root = registered()
        record(root, "A1", 7, observed="2026-10-10")
        record(root, "A1", 9, kind="desk", by="agent:claude", observed="2026-10-15")
        self.assertEqual(status(root)[0]["A1"], "pass")

    def test_desk_pass_cannot_rescue_experiment_kill(self):
        root = registered()
        record(root, "A1", 1, observed="2026-10-10")
        record(root, "A1", 9, kind="desk", by="agent:claude", observed="2026-10-15")
        st, v, _ = status(root)
        self.assertEqual(st["A1"], "fail")
        self.assertEqual(v, "KILL")

    def test_desk_inconclusive_cannot_rescue_experiment_kill(self):
        root = registered()
        record(root, "A1", 1, observed="2026-10-10")
        record(root, "A1", 4, kind="desk", by="agent:claude", observed="2026-10-15")
        self.assertEqual(status(root)[0]["A1"], "fail")

    def test_desk_inconclusive_does_not_undo_experiment_pass(self):
        root = registered()
        record(root, "A1", 7, observed="2026-10-10")
        record(root, "A1", 4, kind="desk", by="agent:claude", observed="2026-10-15")
        self.assertEqual(status(root)[0]["A1"], "pass")

    def test_desk_kill_after_experiment_pass_is_fail(self):
        root = registered()
        record(root, "A1", 7, observed="2026-10-10")
        record(root, "A1", 1, kind="desk", by="agent:claude", observed="2026-10-15")
        self.assertEqual(status(root)[0]["A1"], "fail")

    def test_later_desk_non_kill_does_not_undo_a_desk_kill(self):
        # Desk research can kill at any time; a later desk inconclusive must not erase the
        # kill by falling back to an older experiment pass.
        root = registered()
        record(root, "A1", 8, observed="2026-10-10")
        record(root, "A2", 30, observed="2026-10-10")
        record(root, "A1", 1, kind="desk", by="agent:claude", observed="2026-10-12")
        st, v, _ = status(root)
        self.assertEqual((st["A1"], v), ("fail", "KILL"))
        record(root, "A1", 4, kind="desk", by="agent:claude", observed="2026-10-13")
        st, v, _ = status(root)
        self.assertEqual((st["A1"], v), ("fail", "KILL"))
        record(root, "A1", 9, kind="desk", by="agent:claude", observed="2026-10-14")
        st, v, _ = status(root)
        self.assertEqual((st["A1"], v), ("fail", "KILL"))

    def test_desk_only_kill_then_desk_pass_stays_fail(self):
        root = registered()
        record(root, "A3", 0, kind="desk", by="agent:claude", observed="2026-10-12")
        record(root, "A3", 3, kind="desk", by="agent:claude", observed="2026-10-13")
        self.assertEqual(status(root)[0]["A3"], "fail")

    def test_experiment_after_desk_kill_decides(self):
        root = registered()
        record(root, "A1", 1, kind="desk", by="agent:claude", observed="2026-10-12")
        record(root, "A1", 8, observed="2026-10-14")
        self.assertEqual(status(root)[0]["A1"], "pass")

    def test_desk_inconclusive_alone_is_inconclusive(self):
        root = registered()
        record(root, "A1", 4, kind="desk", by="agent:claude")
        self.assertEqual(status(root)[0]["A1"], "inconclusive")


class Stale(unittest.TestCase):
    def test_threshold_edit_makes_result_stale(self):
        root = registered()
        record(root, "A1", 7)
        write_brief(root, "flat", GOOD.replace("pass: >= 6; kill: < 3", "pass: >= 8; kill: < 3"))
        self.assertEqual(run(root, "register", "ideas/flat.md", "--by", "Dana")[0], 0)
        st, _, out = status(root)
        self.assertEqual(st["A1"], "open")
        self.assertIn("stale", out)

    def test_each_field_edit_makes_stale(self):
        edits = {"test": ("test: 10 interviews", "test: 12 interviews"),
                 "metric": ("metric: interviewees who", "metric: people who"),
                 "kill": ("kill: < 3; by: 2026-11-30", "kill: < 2; by: 2026-11-30"),
                 "by": ("by: 2026-11-30", "by: 2026-11-29")}
        for field, (old, new) in edits.items():
            with self.subTest(field=field):
                root = registered()
                record(root, "A1", 7)
                write_brief(root, "flat", GOOD.replace(old, new))
                run(root, "register", "ideas/flat.md", "--by", "Dana")
                self.assertEqual(status(root)[0]["A1"], "open")

    def test_statement_only_edit_keeps_results(self):
        root = registered()
        record(root, "A1", 7)
        write_brief(root, "flat", GOOD.replace("every week", "most weeks"))
        self.assertEqual(run(root, "register", "ideas/flat.md", "--by", "Dana")[0], 0)
        st, _, out = status(root)
        self.assertEqual(st["A1"], "pass")
        self.assertNotIn("stale", out)


class Verdicts(unittest.TestCase):
    def test_pending(self):
        self.assertEqual(status(registered())[1], "PENDING")

    def test_kill(self):
        root = registered()
        record(root, "A2", 2)
        self.assertEqual(status(root)[1], "KILL")

    def test_kill_beats_stalled(self):
        root = registered()
        record(root, "A2", 2)
        self.assertEqual(status(root, today="2026-12-01")[1], "KILL")

    def test_go_needs_every_critical_pass_only(self):
        root = registered()
        record(root, "A1", 7)
        self.assertEqual(status(root)[1], "PENDING")
        record(root, "A2", 25)
        st, v, _ = status(root)
        self.assertEqual(v, "GO")
        self.assertEqual(st["A3"], "open")   # a non-critical open assumption does not block

    def test_stalled(self):
        root = registered()
        record(root, "A2", 25)
        self.assertEqual(status(root, today="2026-12-01")[1], "STALLED")

    def test_desk_only_critical_is_never_go(self):
        root = registered()
        record(root, "A1", 9, kind="desk", by="agent:claude")
        record(root, "A2", 99, kind="desk", by="Dana")   # a human's desk research is still desk
        record(root, "A3", 5, kind="desk", by="agent:claude")
        st, v, _ = status(root)
        self.assertNotEqual(v, "GO")
        self.assertEqual(st["A1"], "needs-experiment")
        self.assertEqual(st["A2"], "needs-experiment")


class ReadOnly(unittest.TestCase):
    def test_status_and_list_write_nothing(self):
        root = registered()
        record(root, "A1", 7)
        before = tree(root)
        run(root, "status", "flat")
        run(root, "list")
        self.assertEqual(tree(root), before)

    def test_status_before_register_is_refused_without_writing(self):
        from ideas_testkit import repo
        root = repo({"flat": GOOD})
        before = tree(root)
        rc, out = run(root, "status", "flat")
        self.assertEqual(rc, 2)
        self.assertIn("NEXT:", out)
        self.assertEqual(tree(root), before)

    def test_list_shows_verdict_and_decision(self):
        root = registered()
        write_brief(root, "other", GOOD)
        rc, out = run(root, "list")
        self.assertEqual(rc, 0)
        self.assertIn("flat", out)
        self.assertIn("PENDING", out)
        self.assertIn("other", out)
        self.assertIn("unregistered", out)


if __name__ == "__main__":
    unittest.main()
