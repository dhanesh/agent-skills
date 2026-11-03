"""growth-review: characterisation tests. They pin the exact machine lines and exit codes
of `review` for one registered plan and one export, as growth.py gives them today.

A later change will let the review skip a run when the registered export did not change.
These tests catch any other change in today's output. The clock is the testkit's test
clock (--today) and growth._now is pinned, so the registration time is fixed too."""
import contextlib
import io
import os
import unittest
from unittest import mock

from growth_testkit import G, TODAY, rec, records, repo, run, run_err

YDAY = "2026-10-11"  # the latest complete day on TODAY (2026-10-12)
NOW = "2026-10-12T09:00:00Z"

# Four day metrics, so that one export of yesterday gives each status once.
PLAN = """# Metrics plan

| metric | direction | period | warn | breach | min_n | severity |
|---|---|---|---|---|---|---|
| signups | up | day | 100 | 80 | | 3 |
| activation | up | day | 0.4 | 0.3 | | |
| churn-rate | down | day | 0.05 | 0.08 | | 4 |
| refunds | down | day | 3 | 5 | | |
"""
SHA = "59952d15635b"  # the first 12 hex digits of the plan's sha256

# signups OK (120 >= 100), activation WARN (0.3 < 0.35 < 0.4),
# churn-rate BREACH (0.09 > 0.08), refunds NO_DATA (no record).
EXPORT = [rec("signups", YDAY, 120), rec("activation", YDAY, 0.35),
          rec("churn-rate", YDAY, 0.09)]

REGISTERED_NOTE = "NOTE: registered %s by `Dana` at %s" % (SHA, NOW)

REVIEW_MIXED = [
    "METRIC: activation WARN value=0.35 period=2026-10-11",
    "METRIC: churn-rate BREACH value=0.09 period=2026-10-11",
    "METRIC: refunds NO_DATA value=- period=2026-10-11",
    "METRIC: signups OK value=120 period=2026-10-11",
    REGISTERED_NOTE,
    "REVIEW: ok=1 warn=1 breach=1 no_data=1 stale=no",
    "NEXT: signals --source <intake source> | intake import --format "
    "intake-signals-jsonl --source <intake source>",
]

STOP_NO_PLAN = ("STOP: no registered plan; the human registers growth/metrics.md first "
                "with register --by <their name>")


class Pinned(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(G, "_now", lambda: NOW)
        p.start()
        self.addCleanup(p.stop)
        self.root = repo(PLAN)

    def register(self):
        rc, out = run(self.root, "register", "--by", "Dana")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), [
            "LINT: ok 4 metrics",
            "REGISTERED: %s by `Dana` at %s, 4 metrics" % (SHA, NOW),
            "NEXT: the human exports the metric data; import it with import <file>",
        ])

    def import_export(self, recs=EXPORT, added=3, unchanged=0):
        rc, out = run(self.root, "import", records(self.root, recs))
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), [
            "IMPORT: added %d corrected 0 unchanged %d problems 0" % (added, unchanged),
            "NEXT: review",
        ])

    # -- the review of one registered plan and one export -------------------------
    def test_review_ok_warn_breach_no_data(self):
        self.register()
        self.import_export()
        rc, out = run(self.root, "review")
        self.assertEqual(rc, 3)  # a breach needs attention
        self.assertEqual(out.splitlines(), REVIEW_MIXED)

    def test_review_again_on_the_same_export_is_the_same(self):
        self.register()
        self.import_export()
        first = run(self.root, "review")
        self.import_export(added=0, unchanged=3)  # the same file again changes nothing
        self.assertEqual(run(self.root, "review"), first)
        self.assertEqual(first[1].splitlines(), REVIEW_MIXED)

    def test_review_with_no_attention_exits_0(self):
        self.register()
        self.import_export([rec("signups", YDAY, 120), rec("activation", YDAY, 0.5),
                            rec("churn-rate", YDAY, 0.01), rec("refunds", YDAY, 1)], added=4)
        rc, out = run(self.root, "review")
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), [
            "METRIC: activation OK value=0.5 period=2026-10-11",
            "METRIC: churn-rate OK value=0.01 period=2026-10-11",
            "METRIC: refunds OK value=1 period=2026-10-11",
            "METRIC: signups OK value=120 period=2026-10-11",
            REGISTERED_NOTE,
            "REVIEW: ok=4 warn=0 breach=0 no_data=0 stale=no",
        ])

    def test_review_with_no_data_yet(self):
        self.register()
        rc, out = run(self.root, "review")
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), [
            "METRIC: activation NO_DATA value=- period=2026-10-11",
            "METRIC: churn-rate NO_DATA value=- period=2026-10-11",
            "METRIC: refunds NO_DATA value=- period=2026-10-11",
            "METRIC: signups NO_DATA value=- period=2026-10-11",
            REGISTERED_NOTE,
            "REVIEW: ok=0 warn=0 breach=0 no_data=4 stale=no",
        ])

    def test_signals_for_the_same_export(self):
        self.register()
        self.import_export()
        rc, out, err = run_err(self.root, "signals", "--source", "growth")
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), [
            '{"evidence": [{"key": "churn-rate:2026-10-11", "text": "value 0.09 vs breach '
            '0.08 (down)"}], "first_seen": "2026-10-12T00:00:00Z", "kind": "issue", '
            '"last_seen": "2026-10-12T00:00:00Z", "severity": 4, "source": "growth", '
            '"source_id": "churn-rate", "title": "metric: churn-rate breached"}',
        ])
        self.assertEqual(err.splitlines(), ["SIGNALS: 1"])

    # -- refusals -----------------------------------------------------------------
    def test_review_before_registration_stops(self):
        rc, out = run(self.root, "review")
        self.assertEqual(rc, 2)
        self.assertEqual(out.splitlines(), [STOP_NO_PLAN])

    def test_import_before_registration_stops(self):
        rc, out = run(self.root, "import", records(self.root, EXPORT))
        self.assertEqual(rc, 2)
        self.assertEqual(out.splitlines(), [STOP_NO_PLAN])

    def test_agent_cannot_register(self):
        rc, out = run(self.root, "register", "--by", "agent:x")
        self.assertEqual(rc, 2)
        self.assertEqual(out.splitlines(), [
            "STOP: an agent: name cannot register thresholds; only the human registers the "
            "goalposts the review is judged against",
        ])
        rc, out = run(self.root, "review")
        self.assertEqual((rc, out.splitlines()), (2, [STOP_NO_PLAN]))

    def test_today_without_the_test_clock_stops(self):
        self.register()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), \
                mock.patch.dict(os.environ, {G.TEST_CLOCK_ENV: "0"}):
            rc = G.main(["--root", self.root, "--today", TODAY, "review"])
        self.assertEqual(rc, 2)
        self.assertEqual(buf.getvalue().splitlines(), ["STOP: --today is for tests only"])


if __name__ == "__main__":
    unittest.main()
