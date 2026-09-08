#!/usr/bin/env python3
"""Deterministic transcript → world-model harvester (NO model).

Extracts the agent's EXPLICIT world-model markers from the trusted channel and
applies them to the SQLite model, then consolidates (re-derive confidence +
evaluate constraints) and refreshes the digest. This is the hybrid update path:
deterministic hooks capture the file/symbol skeleton; the agent enriches the
graph with markers — a model never guesses facts inside a hook.

Trust boundary (load-bearing): only USER + ASSISTANT text is scanned for MARKERS.
`tool_use` / `tool_result` blocks are never harvested for markers, so a file's
contents or a command's output cannot smuggle a `WM-...` marker or forge evidence.
Markers must START the (stripped) line, so echoed text mid-sentence can't inject one.

Verifier-oracle pass (see `harvest_verifier_runs`): the ONE sanctioned use of tool
blocks. Invariant #2 explicitly lists "verifier exit status" as capturable, but the
Claude Code `PostToolUse[Bash]` payload omits the exit code, so the live observe hook
cannot record it. We recover it here from the transcript WITHOUT weakening the trust
boundary: the FACT (what ran, on which repo files) comes from the trusted tool_use
INPUT (the agent's own argv); the VERDICT comes only from the harness-set `is_error`
boolean — never from command stdout/stderr, which a command controls and could forge.
No output content becomes a fact, and marker harvesting still ignores tool blocks.

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


def _content_blocks(row):
    """The dict content blocks of a transcript row's message (tool_use/tool_result/text)."""
    content = (row.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def harvest_verifier_runs(wm: WorldModel, path: Path, max_rows: int,
                          is_repo_file=None, verifier_re=None) -> dict:
    """Recover the verifier oracle the live PostToolUse hook can't see (no exit code in
    the Claude Code Bash payload) by reading it from the transcript instead.

    For each Bash tool_result whose paired command (from the trusted tool_use INPUT)
    matches the verifier regex, map the harness-set `is_error` boolean to an exit code
    and feed it to `observe_execution` — green (is_error False) → supports/validated,
    red (True) → refutes/contradicted. `is_error` ABSENT ⇒ verdict unknown ⇒ skip the
    oracle (same conservative rule as exit_code=None). The command names the code edge;
    only its exit status crosses into normative_conf, so the two-axis invariant holds.
    Best-effort and idempotent (observe_execution keys on identity)."""
    from world_model import verifier_re_from_env, _is_verifier_invocation  # same module family
    vre = verifier_re if verifier_re is not None else verifier_re_from_env()
    counts = {"validated": 0, "contradicted": 0}
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return counts
    rows = []
    for line in lines[-max_rows:]:
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # tool_use_id -> Bash command, from ASSISTANT tool_use blocks (trusted input only).
    cmd_by_id = {}
    for row in rows:
        if row.get("type") != "assistant":
            continue
        for b in _content_blocks(row):
            if b.get("type") == "tool_use" and b.get("name") == "Bash":
                cid, command = b.get("id"), (b.get("input") or {}).get("command", "")
                if cid and command:
                    cmd_by_id[cid] = command

    # Pair each tool_result with its command; the verdict is the harness `is_error` flag.
    for row in rows:
        if row.get("type") != "user":
            continue
        for b in _content_blocks(row):
            if b.get("type") != "tool_result":
                continue
            command = cmd_by_id.get(b.get("tool_use_id"))
            if not command or not _is_verifier_invocation(command, vre):
                continue
            ie = b.get("is_error")
            if ie is True:
                exit_code = 1
            elif ie is False:
                exit_code = 0
            else:
                continue  # unknown verdict → no oracle (never guess a pass)
            c = wm.observe_execution(command, exit_code=exit_code, is_repo_file=is_repo_file)
            if c.get("oracle"):
                counts["validated" if exit_code == 0 else "contradicted"] += c["oracle"]
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
    p.add_argument("--max-tool-rows", type=int, default=300,
                   help="transcript tail (rows) scanned for verifier tool_use/result pairs")
    args = p.parse_args(argv)

    wm = WorldModel(args.db)
    try:
        for f in args.touched_file:
            wm.upsert_entity("file", f, path=f)

        # Always harvest the agent's markers from the trusted channel and consolidate —
        # in BOTH 'stop' and 'post' modes. (Enrichment is not gated behind an opt-in flag:
        # observing what happens each turn is the whole point.)
        counts = {}
        vcounts = {}
        if args.transcript and Path(args.transcript).is_file():
            rows = load_text_rows(Path(args.transcript), args.max_rows)
            counts = apply_markers(wm, rows)
            # Recover the verifier oracle the live hook can't (exit status from the
            # transcript). Stop mode only: the per-call 'post' path can't see a result yet.
            if args.mode == "stop":
                vcounts = harvest_verifier_runs(wm, Path(args.transcript), args.max_tool_rows)

        stats = wm.consolidate()

        dp = Path(args.digest)
        dp.parent.mkdir(parents=True, exist_ok=True)
        dp.write_text(wm.digest())

        applied = sum(counts.values()) if counts else 0
        promoted = (vcounts.get("validated", 0) + vcounts.get("contradicted", 0)) if vcounts else 0
        print(f"world-model[{args.mode}]: {applied} marker(s) applied; "
              f"{promoted} verifier oracle(s) "
              f"(+{vcounts.get('validated', 0)}/-{vcounts.get('contradicted', 0)}); "
              f"open_contradictions={stats.get('open_contradictions', '?')}", file=sys.stderr)
        return 0
    finally:
        wm.close()


def _guarded_main(argv=None):
    """Never let one bad turn end capture for the life of the project.

    Both hooks that call this run behind `|| true`, so an uncaught exception is
    invisible AND permanent when its cause is persisted (a poisoned constraint
    template did exactly that). Degrade to a single lossy turn instead: report on
    stderr, exit 0, and let the next turn try again.
    """
    try:
        return main(argv)
    except Exception as e:                        # noqa: BLE001 - deliberate backstop
        print(f"world-model harvest: skipped this turn ({type(e).__name__}: {e})",
              file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(_guarded_main())
