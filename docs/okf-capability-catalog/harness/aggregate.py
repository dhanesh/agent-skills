#!/usr/bin/env python3
"""Aggregate judge verdicts into the results table, by arm and tier."""
import json, os, collections

ROOT = os.path.dirname(os.path.abspath(__file__))
ASSERT = ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"]
LABEL = {"A1": "Δ A1 specific consequence", "A2": "Δ A2 costed fallback",
         "A3": "Δ A3 pressed on vagueness", "A4": "Δ A4 reads the arithmetic back",
         "A5": "Δ A5 states depth in words", "R6": "", "A6": "R A6 no cross-side writing",
         "A7": "R A7 no fabrication", "A8": "R A8 document written"}
ARM = {"p": "candidate", "w": "baseline"}
TIER = {"t1": "Opus 5", "t2": "Haiku 4.5"}


def main():
    index = json.load(open(os.path.join(ROOT, "packet_index.json")))
    cells = collections.defaultdict(lambda: collections.defaultdict(list))
    detail = {}
    missing = []
    for key, meta in index.items():
        vpath = os.path.join(ROOT, "verdicts", key + ".json")
        if not os.path.isfile(vpath):
            missing.append(meta["run"])
            continue
        verdict = json.load(open(vpath))
        detail[meta["run"]] = verdict
        col = (meta["arm"], meta["tier"])
        for a in ASSERT:
            v = (verdict.get(a) or {}).get("verdict", "FAIL").upper()
            cells[a][col].append(1 if v == "PASS" else (None if v == "NA" else 0))
    if missing:
        print("MISSING VERDICTS:", ", ".join(sorted(missing)))

    cols = [("w", "t1"), ("p", "t1"), ("w", "t2"), ("p", "t2")]
    header = " | ".join(f"{ARM[a]} ({TIER[t]})" for a, t in cols)
    print(f"\n| | {header} |")
    print("|---|" + "---|" * len(cols))
    totals = collections.defaultdict(lambda: [0, 0])
    for a in ASSERT:
        row = []
        for col in cols:
            vals = [v for v in cells[a][col] if v is not None]
            n, k = len(vals), sum(vals)
            row.append(f"{k}/{n}" if n else "n/a")
            totals[col][0] += k
            totals[col][1] += n
        print(f"| {LABEL[a]} | " + " | ".join(row) + " |")
    print("| **ALL** | " + " | ".join(f"**{totals[c][0]}/{totals[c][1]}**" for c in cols) + " |")

    print("\nPer-run detail:")
    for run in sorted(detail):
        marks = " ".join(f"{a}:{(detail[run].get(a) or {}).get('verdict','?')[0]}"
                         for a in ASSERT)
        print(f"  {run:>10}  {marks}")
    with open(os.path.join(ROOT, "results.json"), "w") as fh:
        json.dump({"index": index, "verdicts": detail}, fh, indent=2)


if __name__ == "__main__":
    main()
