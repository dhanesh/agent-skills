#!/usr/bin/env python3
"""Unit tests for spaced_schedule.py — stdlib only, offline, deterministic."""
import datetime as dt
import io
import unittest

import spaced_schedule as ss


class TestExpandIntervals(unittest.TestCase):
    def test_default_seed_returned_verbatim(self):
        self.assertEqual(ss.expand_intervals(5), [1, 3, 7, 16, 35])

    def test_truncates_seed_when_fewer_reviews(self):
        self.assertEqual(ss.expand_intervals(3), [1, 3, 7])

    def test_zero_reviews(self):
        self.assertEqual(ss.expand_intervals(0), [])

    def test_extends_by_final_seed_ratio(self):
        # ratio = 35/16 = 2.1875 -> round(35*2.1875)=77, round(77*2.1875)=168
        self.assertEqual(ss.expand_intervals(7), [1, 3, 7, 16, 35, 77, 168])

    def test_single_entry_seed_doubles(self):
        self.assertEqual(ss.expand_intervals(4, seed=(2,)), [2, 4, 8, 16])

    def test_extension_is_strictly_increasing(self):
        offsets = ss.expand_intervals(12)
        self.assertEqual(offsets, sorted(set(offsets)))

    def test_negative_count_rejected(self):
        with self.assertRaises(ValueError):
            ss.expand_intervals(-1)

    def test_non_increasing_seed_rejected(self):
        with self.assertRaises(ValueError):
            ss.expand_intervals(3, seed=(1, 3, 3))

    def test_non_positive_seed_rejected(self):
        with self.assertRaises(ValueError):
            ss.expand_intervals(3, seed=(0, 1, 2))

    def test_empty_seed_rejected(self):
        with self.assertRaises(ValueError):
            ss.expand_intervals(3, seed=())


class TestBuildSchedule(unittest.TestCase):
    def test_dates_offset_from_start(self):
        start = dt.date(2026, 7, 8)
        schedule = ss.build_schedule(start, 3)
        self.assertEqual(
            schedule,
            [
                (1, 1, dt.date(2026, 7, 9)),
                (2, 3, dt.date(2026, 7, 11)),
                (3, 7, dt.date(2026, 7, 15)),
            ],
        )

    def test_crosses_month_and_year_boundaries(self):
        schedule = ss.build_schedule(dt.date(2026, 12, 30), 2)
        self.assertEqual(schedule[0][2], dt.date(2026, 12, 31))
        self.assertEqual(schedule[1][2], dt.date(2027, 1, 2))


class TestFormatting(unittest.TestCase):
    def setUp(self):
        self.start = dt.date(2026, 7, 8)
        self.schedule = ss.build_schedule(self.start, 2)

    def test_md_contains_topic_header_and_rows(self):
        text = ss.format_md(self.schedule, "TCP", self.start)
        self.assertIn("# Spaced-retrieval schedule: TCP", text)
        self.assertIn("| 1 | +1 | 2026-07-09 |", text)
        self.assertIn("| 2 | +3 | 2026-07-11 |", text)

    def test_md_without_topic_has_bare_header(self):
        text = ss.format_md(self.schedule, "", self.start)
        self.assertIn("# Spaced-retrieval schedule\n", text)

    def test_tsv_shape(self):
        lines = ss.format_tsv(self.schedule).splitlines()
        self.assertEqual(lines[0], "session\tday_offset\tdate")
        self.assertEqual(lines[1], "1\t1\t2026-07-09")
        self.assertEqual(len(lines), 3)


class TestCli(unittest.TestCase):
    def run_cli(self, argv):
        out = io.StringIO()
        rc = ss.main(argv, out=out)
        return rc, out.getvalue()

    def test_md_output_with_explicit_start(self):
        rc, text = self.run_cli(["--start", "2026-07-08", "--reviews", "3", "topic-x"])
        self.assertEqual(rc, 0)
        self.assertIn("topic-x", text)
        self.assertIn("2026-07-15", text)  # day +7

    def test_tsv_output(self):
        rc, text = self.run_cli(
            ["--start", "2026-07-08", "--reviews", "2", "--format", "tsv"]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(text.splitlines()[1], "1\t1\t2026-07-09")

    def test_custom_intervals(self):
        rc, text = self.run_cli(
            ["--start", "2026-07-08", "--reviews", "4", "--intervals", "1,2,5,12",
             "--format", "tsv"]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(text.splitlines()[4], "4\t12\t2026-07-20")

    def test_bad_date_exits_with_error(self):
        with self.assertRaises(SystemExit):
            self.run_cli(["--start", "July 8th"])

    def test_bad_intervals_exit_with_error(self):
        with self.assertRaises(SystemExit):
            self.run_cli(["--intervals", "3,1"])


if __name__ == "__main__":
    unittest.main()
