#!/usr/bin/env python3
"""Deterministic transcript → world-model harvester (NO model).

Extracts the agent's EXPLICIT world-model markers from the trusted channel and
applies them to the SQLite model, then consolidates (re-derive confidence +
evaluate constraints) and refreshes the digest. This is the hybrid update path:
deterministic hooks capture the file/symbol skeleton; the agent enriches the
graph with markers — a model never guesses facts inside a hook.

Trust boundary (load-bearing): only USER + ASSISTANT text is scanned. `tool_use`
/ `tool_result` blocks are never harvested, so a file's contents or a command's
output cannot smuggle a `WM-...` marker or forge evidence. Markers must START the
(stripped) line, so echoed text mid-sentence can't inject one.

Markers (one per line, `|`-delimited args are trimmed):
  WM-OBSERVE: <subject> <predicate> <object> [@ <file:line>]
  WM-VALIDATED: <subject> <predicate> <object> by <kind>:<ref>     (kind ∈ test|ci|doc|human)
  WM-REFUTES: <subject> <predicate> <object> by <kind>:<ref>
  WM-MAPS: <symbol> -> <referent>
  WM-CONSTRAINT: <name> | <kind> | <predicate> | <params-json> | <message> [| severity]
  WM-CONTRADICTS: <free text>                                       (agent-flagged note)

Idempotent: interactions/evidence key on identity, so re-running on an overlapping
transcript tail just dedupes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from world_model import WorldModel, ORACLE_KINDS, now, _anchor_from_evidence  # noqa: E402

_MARK = re.compile(r"^\s*(WM-OBSERVE|WM-VALIDATED|WM-REFUTES|WM-MAPS|WM-CONSTRAINT|WM-CONTRADICTS)\s*:\s*(.+)$")


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return ""


def load_text_rows(path: Path, max_rows: int):
    rows = []
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
        if not text.strip() or text.lstrip().startswith(("<command-", "<local-command-stdout>")):
            continue
        rows.append((row["type"], text))
    return rows[-max_rows:]


def _triple_and_evidence(rest):
    """'a calls b @ auth.py:14'  ->  (('a','calls','b'), 'auth.py:14')"""
    ev = None
    if "@" in rest:
        rest, ev = rest.rsplit("@", 1)
        ev = ev.strip()
    toks = rest.split()
    if len(toks) < 3:
        return None, ev
    return (toks[0], toks[1], toks[2]), ev


def _triple_and_by(rest):
    """'a calls b by test:t::x'  ->  (('a','calls','b'), 'test', 't::x')"""
    by_kind = by_ref = None
    m = re.search(r"\bby\s+(\w+):(\S+)", rest)
    if m:
        by_kind, by_ref = m.group(1), m.group(2)
        rest = rest[:m.start()]
    toks = rest.split()
    if len(toks) < 3:
        return None, by_kind, by_ref
    return (toks[0], toks[1], toks[2]), by_kind, by_ref


def apply_markers(wm: WorldModel, rows) -> dict:
    counts = {"observe": 0, "validate": 0, "refute": 0, "map": 0, "constraint": 0, "contradict": 0}
    for _role, text in rows:
        for line in text.splitlines():
            m = _MARK.match(line)
            if not m:
                continue
            tag, rest = m.group(1), m.group(2).strip()
            try:
                if tag == "WM-OBSERVE":
                    trip, ev = _triple_and_evidence(rest)
                    if not trip:
                        continue
                    iid = wm.add_interaction(*trip)
                    kind = "file_loc" if ev else "agent_assert"
                    _anchor_from_evidence(wm, iid, ev)
                    wm.add_evidence("interaction", iid, kind, ev or f"{trip[0]}->{trip[2]}",
                                    agent="harvester", activity="WM-OBSERVE", weight=0.6)
                    counts["observe"] += 1
                elif tag in ("WM-VALIDATED", "WM-REFUTES"):
                    trip, bk, br = _triple_and_by(rest)
                    if not trip or not bk:
                        continue
                    iid = wm.add_interaction(*trip)
                    pol = "supports" if tag == "WM-VALIDATED" else "refutes"
                    if tag == "WM-VALIDATED" and bk not in ORACLE_KINDS:
                        continue  # only oracle kinds validate; ignore a bogus WM-VALIDATED
                    wm.add_evidence("interaction", iid, bk, br, polarity=pol,
                                    agent="harvester", activity=tag, weight=0.8)
                    counts["validate" if pol == "supports" else "refute"] += 1
                elif tag == "WM-MAPS":
                    parts = re.split(r"->|\bmaps\b|\brealizes\b", rest, maxsplit=1)
                    if len(parts) == 2:
                        sym, ref = parts[0].strip(), parts[1].strip()
                        ref_id = wm.upsert_entity("referent", ref)
                        rsym = wm.conn.execute("SELECT symbol_id FROM entity WHERE id=?", (ref_id,)).fetchone()["symbol_id"]
                        iid = wm.add_interaction(sym, "realizes", rsym, obj_kind="referent")
                        wm.add_evidence("interaction", iid, "agent_assert", f"{sym}->{ref}",
                                        agent="harvester", activity="WM-MAPS", weight=0.6)
                        counts["map"] += 1
                elif tag == "WM-CONSTRAINT":
                    p = [x.strip() for x in rest.split("|")]
                    if len(p) >= 5:
                        name, kind, pred, params_raw, message = p[0], p[1], p[2], p[3], p[4]
                        severity = p[5] if len(p) > 5 else "violation"
                        try:
                            params = json.loads(params_raw) if params_raw else {}
                        except json.JSONDecodeError:
                            params = {}
                        wm.add_constraint(name, kind, message, scope_predicate=pred or None,
                                          params=params, severity=severity)
                        counts["constraint"] += 1
                elif tag == "WM-CONTRADICTS":
                    wm.conn.execute(
                        "INSERT INTO contradiction(detected_by,severity,message,detected_at) VALUES (?,?,?,?)",
                        ("agent", "warning", rest, now()))
                    counts["contradict"] += 1
            except Exception as e:  # never let one bad marker break the hook
                print(f"world-model: skipped {tag}: {e}", file=sys.stderr)
    wm.conn.commit()
    return counts


def main(argv=None):
    p = argparse.ArgumentParser(description="harvest world-model markers from a transcript")
    p.add_argument("--transcript")
    p.add_argument("--db", default=os.path.join(".world-model", "model.db"))
    p.add_argument("--digest", default=os.path.join(".world-model", "digest.md"))
    p.add_argument("--touched-file", action="append", default=[],
                   help="register a touched file (post-call skeleton); repeatable")
    p.add_argument("--mode", choices=["stop", "post"], default="stop")
    p.add_argument("--max-rows", type=int, default=40)
    args = p.parse_args(argv)

    wm = WorldModel(args.db)
    try:
        for f in args.touched_file:
            wm.upsert_entity("file", f, path=f)

        # Always harvest the agent's markers from the trusted channel and consolidate —
        # in BOTH 'stop' and 'post' modes. (Enrichment is not gated behind an opt-in flag:
        # observing what happens each turn is the whole point.)
        counts = {}
        if args.transcript and Path(args.transcript).is_file():
            rows = load_text_rows(Path(args.transcript), args.max_rows)
            counts = apply_markers(wm, rows)

        stats = wm.consolidate()

        dp = Path(args.digest)
        dp.parent.mkdir(parents=True, exist_ok=True)
        dp.write_text(wm.digest())

        applied = sum(counts.values()) if counts else 0
        print(f"world-model[{args.mode}]: {applied} marker(s) applied; "
              f"open_contradictions={stats.get('open_contradictions', '?')}", file=sys.stderr)
        return 0
    finally:
        wm.close()


if __name__ == "__main__":
    raise SystemExit(main())
