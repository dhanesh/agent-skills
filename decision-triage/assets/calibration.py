#!/usr/bin/env python3
"""calibration.py <records-dir> [--json]

Turns a folder of reviewed decision records into a Brier score and an
over/under-confidence report. Stdlib only, offline, no writes.

A record counts once its `## Outcome` section carries both `Reviewed:` (an ISO
date) and `Correct:` (yes/no). Confidence is read from `## Confidence` as an
integer percentage — the decider's stated probability that the call would look
right at review.

Brier score is the mean squared error between stated confidence and outcome:
0.0 is perfect, 0.25 is what you get by always saying 50%, and above 0.25 means
the confidence numbers are actively misleading. Reported alongside a bucketed
calibration table, which is the more actionable of the two: it says whether the
miss is systematic overconfidence, systematic underconfidence, or noise.

Output protocol: a table, then a final `CALIBRATION_RESULT: OK (n records)` or
`CALIBRATION_RESULT: EMPTY (no reviewed records)`. Exit 0 either way — an empty
corpus is a normal early state, not an error.
"""

import json
import os
import re
import sys

CONFIDENCE_SECTION_RE = re.compile(r"^##\s+Confidence\s*$(.*?)(?=^##\s|\Z)", re.M | re.S)
OUTCOME_SECTION_RE = re.compile(r"^##\s+Outcome\s*$(.*?)(?=^##\s|\Z)", re.M | re.S)
PERCENT_RE = re.compile(r"(\d{1,3})\s*%")
FIELD_RE = re.compile(r"^-\s*([A-Za-z][A-Za-z ]*?)\s*:\s*(.*)$", re.M)
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

BUCKETS = ((0, 60), (60, 70), (70, 80), (80, 90), (90, 100))


def read_record(path):
    """Return dict for a scoreable record, or None if not yet reviewed."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None

    cm = CONFIDENCE_SECTION_RE.search(text)
    om = OUTCOME_SECTION_RE.search(text)
    if not cm or not om:
        return None

    pm = PERCENT_RE.search(cm.group(1))
    if not pm:
        return None
    confidence = int(pm.group(1))
    if not 1 <= confidence <= 99:
        return None

    fields = {k.strip(): v.strip() for k, v in FIELD_RE.findall(om.group(1))}
    reviewed = fields.get("Reviewed", "")
    correct = fields.get("Correct", "").lower()
    if not ISO_DATE_RE.match(reviewed) or correct not in ("yes", "no"):
        return None

    return {
        "record": os.path.basename(path),
        "confidence": confidence,
        "correct": correct == "yes",
        "reviewed": reviewed,
    }


def collect(directory):
    records = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".md"):
            continue
        rec = read_record(os.path.join(directory, name))
        if rec:
            records.append(rec)
    return records


def brier(records):
    if not records:
        return None
    total = sum((r["confidence"] / 100.0 - (1.0 if r["correct"] else 0.0)) ** 2 for r in records)
    return total / len(records)


def buckets(records):
    out = []
    for lo, hi in BUCKETS:
        group = [r for r in records if lo <= r["confidence"] < hi or (hi == 100 and r["confidence"] == 99)]
        if not group:
            continue
        stated = sum(r["confidence"] for r in group) / len(group)
        actual = 100.0 * sum(1 for r in group if r["correct"]) / len(group)
        out.append(
            {
                "bucket": "%d-%d%%" % (lo, hi),
                "n": len(group),
                "stated": round(stated, 1),
                "actual": round(actual, 1),
                "gap": round(stated - actual, 1),
            }
        )
    return out


def verdict(score, rows):
    """A short, honest reading — deliberately hedged on small samples."""
    n = sum(r["n"] for r in rows)
    if n < 8:
        return "too few reviewed records (%d) to read the calibration; keep logging" % n
    gap = sum(r["gap"] * r["n"] for r in rows) / n
    if gap > 10:
        return "systematically overconfident by about %.0f points" % gap
    if gap < -10:
        return "systematically underconfident by about %.0f points" % abs(gap)
    if score is not None and score > 0.25:
        return "roughly calibrated on average but noisy — worse than always saying 50%"
    return "reasonably calibrated"


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    as_json = "--json" in argv[1:]
    if len(args) != 1:
        sys.stderr.write("usage: calibration.py <records-dir> [--json]\n")
        return 2
    directory = args[0]
    if not os.path.isdir(directory):
        sys.stderr.write("not a directory: %s\n" % directory)
        return 2

    records = collect(directory)
    if not records:
        if as_json:
            print(json.dumps({"records": 0, "brier": None, "buckets": []}, indent=2))
        else:
            print("No reviewed records found (need Reviewed: and Correct: filled in ## Outcome).")
        print("CALIBRATION_RESULT: EMPTY (no reviewed records)")
        return 0

    score = brier(records)
    rows = buckets(records)

    if as_json:
        print(
            json.dumps(
                {
                    "records": len(records),
                    "brier": round(score, 4),
                    "buckets": rows,
                    "verdict": verdict(score, rows),
                },
                indent=2,
            )
        )
    else:
        print("Reviewed records: %d" % len(records))
        print("Brier score: %.4f  (0.0 perfect, 0.25 = always saying 50%%)" % score)
        print("")
        print("%-10s %4s %9s %9s %7s" % ("bucket", "n", "stated", "actual", "gap"))
        for r in rows:
            print("%-10s %4d %8.1f%% %8.1f%% %+7.1f" % (r["bucket"], r["n"], r["stated"], r["actual"], r["gap"]))
        print("")
        print("Reading: %s" % verdict(score, rows))

    print("CALIBRATION_RESULT: OK (%d records)" % len(records))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
