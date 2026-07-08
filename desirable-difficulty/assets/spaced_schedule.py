#!/usr/bin/env python3
"""Expanding-interval spaced-retrieval schedule generator.

Turns a start date into dated review sessions at expanding intervals
(default seed: day 1, 3, 7, 16, 35). Beyond the seed, intervals keep
growing by the seed's final ratio, so any number of reviews works.

Stdlib only — no pip, no network. Examples:

    python3 spaced_schedule.py --start 2026-07-08 --reviews 5 "TCP congestion control"
    python3 spaced_schedule.py --start 2026-07-08 --reviews 8 --format tsv
    python3 spaced_schedule.py --intervals 1,2,5,12 --reviews 6 "verb conjugations"
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

DEFAULT_INTERVALS = (1, 3, 7, 16, 35)


def expand_intervals(count, seed=DEFAULT_INTERVALS):
    """Return `count` day-offsets, extending the seed geometrically if needed.

    The seed must be strictly increasing positive integers. Past the end of
    the seed, each offset grows by the ratio of the seed's last two entries
    (2.0 for a single-entry seed), rounded, and always by at least one day so
    the sequence stays strictly increasing.
    """
    if count < 0:
        raise ValueError("count must be >= 0")
    seed = tuple(int(x) for x in seed)
    if not seed:
        raise ValueError("intervals seed must be non-empty")
    if seed[0] < 1 or any(b <= a for a, b in zip(seed, seed[1:])):
        raise ValueError(
            "intervals seed must be strictly increasing positive integers: %r" % (seed,)
        )
    offsets = list(seed[:count])
    ratio = seed[-1] / seed[-2] if len(seed) > 1 else 2.0
    while len(offsets) < count:
        offsets.append(max(offsets[-1] + 1, round(offsets[-1] * ratio)))
    return offsets


def build_schedule(start, count, seed=DEFAULT_INTERVALS):
    """Return [(session_number, day_offset, date), ...] from `start`."""
    return [
        (i + 1, off, start + dt.timedelta(days=off))
        for i, off in enumerate(expand_intervals(count, seed))
    ]


def format_md(schedule, topic, start):
    lines = [
        "# Spaced-retrieval schedule%s" % (": %s" % topic if topic else ""),
        "",
        "First exposure: %s. Start each session with retrieval (blank page, no source);"
        % start.isoformat(),
        "~80%+ recall keeps the expanding intervals, less means shorten the next gap.",
        "",
        "| Session | Day | Date |",
        "|---:|---:|---|",
    ]
    lines += ["| %d | +%d | %s |" % (n, off, d.isoformat()) for n, off, d in schedule]
    return "\n".join(lines) + "\n"


def format_tsv(schedule):
    lines = ["session\tday_offset\tdate"]
    lines += ["%d\t%d\t%s" % (n, off, d.isoformat()) for n, off, d in schedule]
    return "\n".join(lines) + "\n"


def parse_intervals(text):
    try:
        return tuple(int(part) for part in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("intervals must be comma-separated integers")


def parse_date(text):
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("start must be an ISO date (YYYY-MM-DD)")


def main(argv=None, out=None):
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("topic", nargs="?", default="", help="optional topic label")
    parser.add_argument("--start", type=parse_date, default=None,
                        help="first-exposure date, ISO format (default: today)")
    parser.add_argument("--reviews", type=int, default=5,
                        help="number of review sessions (default: 5)")
    parser.add_argument("--intervals", type=parse_intervals, default=None,
                        help="comma-separated seed day-offsets (default: 1,3,7,16,35)")
    parser.add_argument("--format", choices=("md", "tsv"), default="md")
    args = parser.parse_args(argv)

    start = args.start or dt.date.today()
    seed = args.intervals if args.intervals is not None else DEFAULT_INTERVALS
    try:
        schedule = build_schedule(start, args.reviews, seed)
    except ValueError as exc:
        parser.error(str(exc))
    if args.format == "tsv":
        out.write(format_tsv(schedule))
    else:
        out.write(format_md(schedule, args.topic, start))
    return 0


if __name__ == "__main__":
    sys.exit(main())
