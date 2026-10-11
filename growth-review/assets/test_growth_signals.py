"""growth-review: signals, checked against ops-intake's real adapter and CLI from the
sibling directory (main's intake-signals-jsonl), plus the no-I/O tripwire."""
import json
import os
import unittest

from growth_testkit import (G, TODAY, day, intake_items, intake_repo, intake_run, load,
                            no_io, ops_intake, pipe, rec, registered, run, run_err)

YDAY = "2026-10-11"


def signals(root, today=TODAY, source="growth"):
    rc, out, err = run_err(root, "signals", "--source", source, today=today)
    assert rc == 0, (out, err)
    return [json.loads(l) for l in out.splitlines()]


class Signals(unittest.TestCase):
    def test_one_line_per_breach_shape(self):
        root = registered()
        load(root, rec("signups", "2026-10-09", 60), rec("signups", "2026-10-10", 70),
             rec("signups", YDAY, 75))
        sigs = signals(root)
        self.assertEqual(sigs, [{
            "source": "growth", "source_id": "signups", "kind": "issue",
            "title": "metric: signups breached", "severity": 3,
            "first_seen": "2026-10-10T00:00:00Z", "last_seen": "2026-10-12T00:00:00Z",
            "evidence": [{"key": "signups:2026-10-11",
                          "text": "value 75 vs breach 80 (up)"}]}])

    def test_streak_breaks_at_a_gap_or_a_non_breach(self):
        root = registered()
        load(root, rec("signups", "2026-10-07", 60), rec("signups", "2026-10-09", 70),
             rec("signups", "2026-10-10", 90), rec("signups", YDAY, 75))
        self.assertEqual(signals(root)[0]["first_seen"], "2026-10-12T00:00:00Z")
        root = registered()
        load(root, rec("signups", "2026-10-09", 60), rec("signups", YDAY, 75))
        self.assertEqual(signals(root)[0]["first_seen"], "2026-10-12T00:00:00Z")

    def test_week_and_month_timestamps_and_severity(self):
        root = registered()
        load(root, rec("activation", "2026-W40", 0.1), rec("activation", "2026-W41", 0.2),
             rec("churn-rate", "2026-09", 0.3, n=99))
        by = {s["source_id"]: s for s in signals(root)}
        self.assertEqual((by["activation"]["first_seen"], by["activation"]["last_seen"]),
                         ("2026-10-05T00:00:00Z", "2026-10-12T00:00:00Z"))
        self.assertEqual(by["churn-rate"]["last_seen"], "2026-10-01T00:00:00Z")
        self.assertEqual(by["churn-rate"]["severity"], 4)
        self.assertEqual(by["churn-rate"]["evidence"][0]["text"],
                         "value 0.3 vs breach 0.08 (down)")

    def test_warn_stale_no_data_never_signal(self):
        root = registered()
        load(root, rec("signups", YDAY, 90), rec("churn-rate", "2026-09", 0.9, n=3))
        self.assertEqual(signals(root), [])
        rc, out = run(root, "review")
        self.assertIn("METRIC: signups WARN", out)
        self.assertIn("METRIC: churn-rate NO_DATA", out)

    def test_stdout_is_json_only(self):
        root = registered()
        rc, out, err = run_err(root, "signals", "--source", "growth")
        self.assertEqual((rc, out), (0, ""))
        rc, out, err = run_err(root, "signals", "--source", "bad name")
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("STOP: --source", err)
        for bad in ("release", "-x", "a" * 51, "a;b"):
            self.assertEqual(run_err(root, "signals", "--source", bad)[0], 2, bad)
        fresh = registered()
        os.unlink(os.path.join(fresh, ".skill-contract", "growth", "plan.json"))
        rc, out, err = run_err(fresh, "signals", "--source", "growth")
        # M1: log.jsonl still records the registration, so the deletion fails closed (3).
        self.assertEqual((rc, out), (3, ""))
        self.assertIn("STOP: no registered plan", err)
        self.assertIn("NOTE: log.jsonl records 1 registration, but plan.json holds none", err)


class OpsIntakeOnMain(unittest.TestCase):
    """Feed real emitted lines to ops-intake's own adapter and CLI."""

    def test_adapter_accepts_the_line(self):
        _, AD = ops_intake()
        root = registered()
        load(root, rec("signups", YDAY, 75))
        rc, out, _ = run_err(root, "signals", "--source", "growth")
        sigs, problems = AD.ADAPTERS["intake-signals-jsonl"](
            out, {"source": "growth", "now": "2026-10-12T06:00:00Z"})
        self.assertEqual(problems, [])
        self.assertEqual(len(sigs), 1)
        s = sigs[0]
        self.assertEqual((s["source_id"], s["kind"], s["trust"], s["url"]),
                         ("signups", "issue", "low", ""))  # no url: main defaults it to ""
        self.assertEqual(s["evidence"][0]["key"], "signups:2026-10-11")

    def test_warn_gives_no_item(self):
        root = registered()
        load(root, rec("signups", YDAY, 90))
        iroot = intake_repo()
        out, irc, iout = pipe(root, iroot, TODAY, "2026-10-12T06:00:00Z")
        self.assertEqual(out, "")
        self.assertEqual(intake_items(iroot), {})

    def test_25_daily_breaches_count_25(self):
        root = registered()
        iroot = intake_repo()
        start = "2026-09-01"
        for i in range(25):
            period = day(start, i)
            today = day(start, i + 1)
            rc, out = load(root, rec("signups", period, 50 + i % 3), today=today)
            self.assertEqual(rc, 0, out)
            rc, out = run(root, "review", today=today)
            self.assertIn("METRIC: signups BREACH", out)
            for k in range(2 if i in (0, 13, 24) else 1):  # a second same-day run
                now = "%sT%02d:00:00Z" % (today, 6 + k)
                _, irc, iout = pipe(root, iroot, today, now)
                self.assertEqual(irc, 0, iout)
        items = intake_items(iroot)
        self.assertEqual(len(items), 1)
        item = list(items.values())[0]
        self.assertEqual(item["count"], 25)
        self.assertEqual(len(item["evidence"]), 20)  # the cap; the count survives it
        self.assertEqual(item["first_seen"], "2026-09-02T00:00:00Z")
        self.assertEqual(item["last_seen"], "2026-09-26T00:00:00Z")
        self.assertEqual(item["evidence"][-1]["key"], "signups:2026-09-25")

    def test_dismissed_breach_returns_at_the_next_breached_period(self):
        """main's rule: a dismissed item becomes new +regressed when a signal's last_seen
        is later than the dismiss time (closed_at)."""
        root = registered()
        iroot = intake_repo()
        load(root, rec("signups", "2026-10-10", 70), today="2026-10-11")
        pipe(root, iroot, "2026-10-11", "2026-10-11T06:00:00Z")
        iid = list(intake_items(iroot))[0]
        rc, out = intake_run(iroot, "dismiss", iid, "--by", "Dana", "--reason", "promo ended",
                             now="2026-10-11T09:00:00Z")
        self.assertEqual(rc, 0, out)
        self.assertEqual(intake_items(iroot)[iid]["closed_at"], "2026-10-11T09:00:00Z")
        # The same period again, later the same day: last_seen 2026-10-11T00:00Z is before
        # the dismiss, so the item stays dismissed.
        pipe(root, iroot, "2026-10-11", "2026-10-11T18:00:00Z")
        it = intake_items(iroot)[iid]
        self.assertEqual((it["state"], it.get("regressed")), ("dismissed", False))
        # The next day's breach ends after the dismiss: the item comes back.
        load(root, rec("signups", YDAY, 72))
        pipe(root, iroot, TODAY, "2026-10-12T06:00:00Z")
        it = intake_items(iroot)[iid]
        self.assertEqual((it["state"], it["regressed"]), ("new", True))
        self.assertEqual(it["count"], 2)

    def test_dismissed_item_stays_dismissed_while_warn(self):
        root = registered()
        iroot = intake_repo()
        load(root, rec("signups", "2026-10-10", 70), today="2026-10-11")
        pipe(root, iroot, "2026-10-11", "2026-10-11T06:00:00Z")
        iid = list(intake_items(iroot))[0]
        intake_run(iroot, "dismiss", iid, "--by", "Dana", "--reason", "known",
                   now="2026-10-11T09:00:00Z")
        load(root, rec("signups", YDAY, 95))
        pipe(root, iroot, TODAY, "2026-10-12T06:00:00Z")
        self.assertEqual(intake_items(iroot)[iid]["state"], "dismissed")


class NoIO(unittest.TestCase):
    def test_no_socket_no_subprocess(self):
        with no_io():
            root = registered()
            load(root, rec("signups", YDAY, 70))
            self.assertEqual(run(root, "review")[0], 3)
            self.assertEqual(run_err(root, "signals", "--source", "growth")[0], 0)
            self.assertEqual(run(root, "lint")[0], 0)


if __name__ == "__main__":
    unittest.main()
