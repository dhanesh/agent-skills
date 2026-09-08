#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []   # stdlib only — runs from a Stop hook on any machine
# ///
"""
Deterministic transcript -> ledger harvester (NO model).

This is the CAPTURE half of abrupt-close durability. The hooks curate what is in
the ledger; this puts the durable facts INTO the ledger every turn, so a session
killed mid-work (crash, terminal close, kill -9) still has its decisions on disk.

It is the lossless analogue of the deleted local-model summariser: instead of
asking a 1.7B model to *guess* a summary (lossy, rot-prone), it extracts only
HIGH-PRECISION, deterministic signals and stores them verbatim:

  1. Latest user request      -> `task_state` card  (where we are, 1 per turn)
  2. Explicit MARKER: lines   -> mapped kind        (deliberate, opt-in capture)
       DECISION:|CONSTRAINT:|QUESTION:|OPEN:|TASK:|TODO:|NOTE:|FILE:
  3. file:line references     -> `file_ref` cards   (regex, high precision)

Trust boundary (LSC-7): only the TRUSTED channel — user messages (the principal)
and assistant text (our own scaffold) — is harvested. `tool_result` / `tool_use`
blocks are NOT ingested: that would bloat the ledger and open an injection surface.
Markers must start the line, so untrusted text echoed mid-sentence can't smuggle one.

After ingest it curates + persists, flushing `.context/ledger.json` and refreshing
`.context/digest.md`. Idempotent: re-running on overlapping transcript tails just
dedupes and bumps frequency (ingest keys on kind+normalized content).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from context_ledger import ContextLedger, TRUSTED, ledger_lock

# A marker must START the (stripped) line, then ":" or "-". Keeps precision high.
_MARKER = re.compile(
    r"^\s*(DECISION|CONSTRAINT|QUESTION|OPEN|TASK|TODO|NOTE|FILE)\s*[:\-]\s*(.+)$"
)
_MARKER_KIND = {
    "DECISION": "decision", "CONSTRAINT": "constraint",
    "QUESTION": "open_question", "OPEN": "open_question",
    "TASK": "task_state", "TODO": "task_state",
    "NOTE": "note", "FILE": "file_ref",
}
# file:line — e.g. context_ledger.py:170, hooks/stop.sh:12, a/b/c.tsx:4
_FILE_LINE = re.compile(r"\b[\w./-]+\.[A-Za-z]{1,6}:\d+\b")

MIN_SNAPSHOT_CHARS = 15      # skip trivial user turns ("yes", "ok") as task_state
MAX_FILE_REFS = 15           # cap auto file_refs per harvest (anti-bloat)
SNAPSHOT_CHARS = 320         # truncate the user-request snapshot


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return ""


def load_text_rows(path: Path, max_rows: int) -> list[tuple[str, str]]:
    """Return the last `max_rows` (role, text) pairs of real user/assistant text."""
    rows: list[tuple[str, str]] = []
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") not in ("user", "assistant"):
            continue
        text = _text(row.get("message", {}).get("content"))
        # Skip synthetic command wrappers and empty / tool-only turns.
        if not text.strip() or text.lstrip().startswith(("<command-", "<local-command-stdout>")):
            continue
        rows.append((row["type"], text))
    return rows[-max_rows:]


def harvest(led: ContextLedger, rows: list[tuple[str, str]]) -> dict:
    counts = {"task_state": 0, "decision": 0, "constraint": 0,
              "open_question": 0, "note": 0, "file_ref": 0}

    # 1) Latest substantive user request -> task_state snapshot (where we are).
    for role, text in reversed(rows):
        if role == "user" and len(text.strip()) >= MIN_SNAPSHOT_CHARS:
            snap = " ".join(text.split())[:SNAPSHOT_CHARS]
            led.ingest("task_state", f"Latest request: {snap}", provenance=TRUSTED)
            counts["task_state"] += 1
            break

    # 2) Explicit marker lines (both channels are trusted) + 3) file:line refs.
    file_refs: list[str] = []
    for role, text in rows:
        for line in text.splitlines():
            m = _MARKER.match(line)
            if m:
                kind = _MARKER_KIND[m.group(1).upper()]
                content = m.group(2).strip()
                if content:
                    led.ingest(kind, content, provenance=TRUSTED)
                    counts[kind] += 1
        if role == "assistant":
            for ref in _FILE_LINE.findall(text):
                if ref not in file_refs:
                    file_refs.append(ref)

    for ref in file_refs[:MAX_FILE_REFS]:
        led.ingest("file_ref", ref, provenance=TRUSTED)
        counts["file_ref"] += 1

    return counts


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Harvest durable facts from a transcript")
    p.add_argument("--transcript", required=True)
    p.add_argument("--store", default=".context/ledger.json")
    p.add_argument("--digest", default=".context/digest.md")
    p.add_argument("--budget", type=int, default=8000)
    p.add_argument("--anchor", default="")
    p.add_argument("--max-rows", type=int, default=30,
                   help="scan only the last N text rows (recent turn); dedup covers overlap")
    args = p.parse_args(argv)

    tpath = Path(args.transcript)
    if not tpath.is_file():
        return 0                                  # nothing to do; never block the hook

    rows = load_text_rows(tpath, args.max_rows)
    if not rows:
        return 0

    store = Path(args.store)

    # `--anchor` wins; the file is the fallback. Read it OUTSIDE the lock and never
    # let an unreadable anchor cost the turn's capture — it only steers ranking.
    anchor = args.anchor
    apath = Path(".context/anchor.txt")
    if not anchor and apath.exists():
        try:
            anchor = apath.read_text().strip()
        except OSError:
            anchor = args.anchor

    # One writer at a time: two sessions in one project both fire the Stop hook
    # every turn, and an unguarded read-modify-write drops decisions (3/20 runs)
    # and can leave an unparseable store (1/20). Load INSIDE the lock so we modify
    # what is on disk now, not what was there before another session's flush.
    with ledger_lock(store):
        led = ContextLedger.load(store)
        counts = harvest(led, rows)

        # Flush: curate (renders + persists) so an abrupt close finds fresh state.
        digest = led.to_digest(args.budget, anchor)
        dp = Path(args.digest)
        dp.parent.mkdir(parents=True, exist_ok=True)
        dtmp = dp.with_name(f"{dp.name}.tmp{os.getpid()}")
        dtmp.write_text(digest)
        os.replace(dtmp, dp)                      # never inject a half-written digest
        led.persist(store)

    ingested = sum(counts.values())
    print(f"harvested {ingested} card(s): "
          + ", ".join(f"{k}={v}" for k, v in counts.items() if v),
          file=sys.stderr)
    return 0


def _guarded_main(argv):
    """Never let one bad turn end capture for the life of the project.

    The Stop hook calls this behind `|| true`, so an uncaught exception is both
    invisible and — when its cause is on disk — permanent. Degrade to a single
    lossy turn: report on stderr, exit 0, and let the next turn try again.
    """
    try:
        return main(argv)
    except Exception as e:                        # noqa: BLE001 - deliberate backstop
        print(f"context harvest: skipped this turn ({type(e).__name__}: {e})",
              file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(_guarded_main(sys.argv[1:]))
