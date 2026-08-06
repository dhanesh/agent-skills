#!/usr/bin/env python3
"""The review deliverable, its grader, its loop state, and its second opinion.

`template()`     the shape a concept-level review has to fill: the four review
                 dimensions, a verdict, and one disposition line per signal.
`grade()`        the evaluator. It cannot tell you a finding is *right*; it does
                 refuse a review with an empty section, surviving placeholder
                 text, an ambiguous verdict, or an unresolved high-severity
                 signal — the difference between a review and a filled-in form.
`loop_state()`   typed progress, so a self-prompting loop can decide what to do
                 next from data rather than from prose.
`audit_prior()`  a second opinion on a review someone else already wrote: which
                 signals it never engages, and which files it cites that the
                 change does not touch.
"""

import re

VERDICTS = ("ship", "ship-with-followups", "needs-changes", "needs-discussion")

SECTIONS = (
    ("Verdict", "one of: " + ", ".join(VERDICTS)),
    ("The change in one paragraph",
     "What does this change do, in plain language, to someone who has not read the diff? "
     "If you cannot write this without naming a file, you do not understand the change yet."),
    ("Concepts",
     "What idea is new or altered here — the model, the abstraction, the name? Is the concept "
     "the right size, and does the code's vocabulary match the domain's?"),
    ("Algorithm choices",
     "What approach was picked, what was it picked over, and at what input size does the "
     "choice stop being the right one?"),
    ("Architecture and boundaries",
     "What moved across a module, process, or trust boundary? Which direction do the new "
     "dependencies point, and does the layering still hold?"),
    ("Blast radius and risk",
     "Who else is affected, what happens on rollout and rollback, and what is the worst "
     "realistic failure?"),
    ("Signals", "Every extracted signal, each resolved as addressed or dismissed."),
    ("Open questions for the author", "The questions only the author can settle."),
)

# Leftover template text is detected by matching what the template actually
# wrote, not by looking for angle brackets — `Arc<RwLock<T>>` and `Vec<Id>` are
# code, and a grader that calls them placeholders fails every systems review.
_UNFILLED_RE = re.compile(r"\bTODO\b|\bFIXME\b|\bXXX\b")


def _is_unfilled(title, content):
    prompt = dict(SECTIONS).get(title, "")
    if prompt and prompt[:40] in content:
        return True
    if "<what you concluded" in content:
        return True
    return bool(_UNFILLED_RE.search(content))


_DISPOSITION_RE = r"\*\*(addressed|dismissed)\*\*:\s*(.+)$"
MIN_DISPOSITION_CHARS = 20
MIN_PARAGRAPH_WORDS = 25


def signal_token(sig):
    """Stable identifier for one signal, used to match report lines to signals."""
    where = sig.get("file") or "-"
    return "%s@%s:%d" % (sig["id"], where, sig.get("line") or 0)


def template(sigs, stats, meta):
    """Render the review skeleton. Every bracketed prompt is meant to be replaced."""
    lines = []
    lines.append("# Concept review — %s" % meta.get("label", "working diff"))
    lines.append("")
    lines.append(
        "%d changed rows across %d files, of which %d survive into the condensed diff "
        "(%.1f%% density). %d signal(s) extracted."
        % (
            stats.get("total_rows", 0),
            stats.get("total_files", 0),
            stats.get("kept_rows", 0),
            stats.get("density_pct", 0.0),
            len(sigs),
        )
    )
    lines.append("")

    for title, prompt in SECTIONS:
        lines.append("## %s" % title)
        lines.append("")
        if title == "Verdict":
            lines.append("<%s>" % prompt)
            lines.append("")
            continue
        if title == "Signals":
            lines.append(
                "Each signal is a question, not a defect. Resolve every one: **addressed** "
                "(it is a real concern and here is the finding) or **dismissed** (here is why "
                "it does not apply). High-severity signals must all be resolved."
            )
            lines.append("")
            if not sigs:
                lines.append("_No signals extracted._")
                lines.append("")
                continue
            for sig in sigs:
                lines.append(
                    "- `%s` (%s) — %s"
                    % (signal_token(sig), sig["severity"], sig["question"])
                )
                lines.append(
                    "  - **open**: <what you concluded, or why this does not apply here>"
                )
            lines.append("")
            continue
        lines.append("<%s>" % prompt)
        lines.append("")

    return "\n".join(lines)


def grade(text, sigs):
    """Deterministic completeness grading. Returns (checks, ok)."""
    checks = []

    def add(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    body = {}
    current = None
    for line in text.split("\n"):
        m = re.match(r"^##\s+(.*?)\s*$", line)
        if m:
            current = m.group(1)
            body[current] = []
            continue
        if current:
            body[current].append(line)

    for title, _ in SECTIONS:
        content = "\n".join(body.get(title, [])).strip()
        if title not in body:
            add("section:%s" % title, False, "missing")
            continue
        if not content:
            add("section:%s" % title, False, "empty")
            continue
        if title != "Signals" and _is_unfilled(title, content):
            add("section:%s" % title, False, "still carries template placeholder text")
            continue
        add("section:%s" % title, True)

    verdict = "\n".join(body.get("Verdict", [])).strip().lower()
    # Hyphens are word boundaries, so a plain \b would see `ship` inside
    # `ship-with-followups` and call the only correct answer ambiguous.
    found = [
        v for v in VERDICTS
        if re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(v), verdict)
    ]
    add(
        "verdict",
        len(found) == 1,
        "expected exactly one of %s, found %s" % (", ".join(VERDICTS), found or "none"),
    )

    paragraph = "\n".join(body.get("The change in one paragraph", [])).strip()
    words = len(paragraph.split())
    add(
        "paragraph-substance",
        words >= MIN_PARAGRAPH_WORDS,
        "%d words (need >= %d)" % (words, MIN_PARAGRAPH_WORDS),
    )

    entries = _signal_entries(body.get("Signals", []))
    unresolved = []
    for sig in sigs:
        if sig["severity"] != "high":
            continue
        tok = signal_token(sig)
        if not _resolved(entries.get(tok, [])):
            unresolved.append(tok)
    add(
        "high-signals-resolved",
        not unresolved,
        ("unresolved: %s" % ", ".join(unresolved[:6])) if unresolved else "all resolved",
    )

    ok = all(passed for _, passed, _ in checks)
    return checks, ok


_TOKEN_RE = re.compile(r"`([\w.\-]+@[^`]*)`")


def _signal_entries(lines):
    """Group the Signals section into {token: [lines belonging to it]}.

    An entry opens on the line carrying its backticked token and runs until the
    next such line, so the disposition may sit on the same line or on any
    continuation beneath it.
    """
    entries = {}
    current = None
    for line in lines:
        m = _TOKEN_RE.search(line)
        if m:
            current = m.group(1)
            entries.setdefault(current, [])
        if current:
            entries[current].append(line)
    return entries


def _resolved(entry_lines):
    for line in entry_lines:
        m = re.search(_DISPOSITION_RE, line)
        if m and len(m.group(2).strip()) >= MIN_DISPOSITION_CHARS:
            return True
    return False


def format_checks(checks):
    out = []
    for name, ok, detail in checks:
        suffix = " (%s)" % detail if detail else ""
        out.append("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL", suffix))
    passed = sum(1 for _, ok, _ in checks if ok)
    out.append(
        "REPORT_RESULT: %s (%d/%d checks)"
        % ("PASS" if passed == len(checks) else "FAIL", passed, len(checks))
    )
    return "\n".join(out)


# ── Loop state ───────────────────────────────────────────────────────────────

PHASES = ("unopened", "condense", "interrogate", "write", "repair", "done")


def loop_state(probe):
    """Typed progress for a self-prompting loop.

    A loop that has to infer its own position from prose drifts. This returns
    the position as data: which phase, whether the run is finished, the single
    next action, and the blockers standing in the way. Every field is derived
    from files on disk, so an interrupted loop resumes correctly rather than
    starting over.
    """
    state = {
        "phase": "unopened",
        "done": False,
        "next_action": "",
        "blockers": [],
        "gates": {"plan": "absent", "review": "absent"},
        "density_pct": None,
        "unresolved_high": [],
    }

    if not probe.get("work_exists") or not probe.get("has_source"):
        state["next_action"] = (
            "run `open` to index the diff into %s" % probe.get("work", ".review")
        )
        return state

    if probe.get("has_plan"):
        state["gates"]["plan"] = "accepted" if probe.get("plan_ok") else "rejected"
    stats = probe.get("stats") or {}
    state["density_pct"] = stats.get("density_pct")

    if not probe.get("plan_ok", False):
        state["phase"] = "condense"
        state["blockers"] = list(probe.get("plan_errors") or [])[:8]
        state["next_action"] = (
            "resolve the plan rejections, then run `draft` again"
            if state["blockers"] else "write plan.json against indexed.diff, then run `draft`"
        )
        return state

    if not probe.get("has_condensed"):
        state["phase"] = "condense"
        # An empty plan compiles, which would otherwise let a loop skip condensing
        # by doing nothing at all. Say so — but do not block, because a change with
        # genuinely no noise is a legitimate answer, just one that has to be chosen.
        state["next_action"] = (
            "the plan is empty, so nothing would be condensed; read indexed.diff and write "
            "edits, or run `commit` as-is if the change genuinely carries no noise"
            if probe.get("plan_is_empty")
            else "the plan compiles — run `commit` to write condensed.diff"
        )
        return state

    if not probe.get("has_review"):
        state["phase"] = "interrogate"
        state["next_action"] = (
            "read condensed.diff and the signals, then run `template` and answer every section"
        )
        return state

    sigs = probe.get("signals") or []
    state["unresolved_high"] = _unresolved_high(probe, sigs)

    if probe.get("review_ok"):
        state["phase"] = "done"
        state["done"] = True
        state["gates"]["review"] = "pass"
        state["next_action"] = "hand over REVIEW.md"
        return state

    state["phase"] = "repair"
    state["gates"]["review"] = "fail"
    state["blockers"] = list(probe.get("review_gaps") or [])
    state["next_action"] = "close the gaps listed in blockers, then run `grade` again"
    return state


def _unresolved_high(probe, sigs):
    gaps = probe.get("review_gaps") or []
    if "high-signals-resolved" not in gaps:
        return []
    return [signal_token(s) for s in sigs if s.get("severity") == "high"]


# ── Second opinion on an existing review ─────────────────────────────────────

_CITATION_RE = re.compile(r"\b([\w./-]+\.[A-Za-z0-9]{1,6})(?::\d+)?\b")
_DIMENSION_CUES = {
    "concepts": ("concept", "abstraction", "model", "naming", "vocabulary", "domain"),
    "algorithms": ("algorithm", "complexity", "o(n", "quadratic", "batch", "index",
                   "data structure", "throughput", "latency"),
    "architecture": ("architecture", "boundary", "boundaries", "layer", "layering",
                     "module", "dependency", "coupling", "contract", "interface"),
    "risk": ("risk", "rollout", "rollback", "migration", "blast radius", "failure",
             "incident", "on-call", "regression"),
}


def audit_prior(text, sigs, changed_paths):
    """Grade a review written elsewhere against what the change actually contains.

    Returns `(lines, blind_spots)`. A blind spot is a high-severity signal the
    prior review never engages — by id, by location, or by quoting its evidence.
    This is deliberately generous about *how* the reviewer engaged it: the aim is
    to find what was never looked at, not to police citation style.
    """
    lines = []
    haystack = text.lower()
    blind_spots = []

    for sig in sigs:
        if sig.get("severity") != "high":
            continue
        tok = signal_token(sig)
        located = "%s:%d" % (sig["file"], sig["line"]) if sig.get("file") else ""
        evidence = (sig.get("evidence") or "").strip().lower()
        engaged = (
            sig["id"].lower() in haystack
            or (located and located.lower() in haystack)
            or (len(evidence) >= 12 and evidence[:60] in haystack)
        )
        if engaged:
            lines.append("COVERED: %s" % tok)
        else:
            blind_spots.append(tok)
            lines.append("BLIND-SPOT: %s — %s" % (tok, sig["question"]))

    cited = {m.group(1) for m in _CITATION_RE.finditer(text)}
    outside = sorted(
        c for c in cited
        if "/" in c and not any(c in p or p.endswith(c) for p in changed_paths)
    )
    for path in outside[:10]:
        lines.append(
            "OUT-OF-SCOPE: the review cites %s, which this change does not touch — "
            "context, or a claim about the wrong file?" % path
        )

    engaged_dims = [
        name for name, cues in sorted(_DIMENSION_CUES.items())
        if any(cue in haystack for cue in cues)
    ]
    missing_dims = [name for name in sorted(_DIMENSION_CUES) if name not in engaged_dims]
    if missing_dims:
        lines.append(
            "THIN: no wording suggests the review engaged %s. This is a weak signal — "
            "confirm by reading before concluding anything." % ", ".join(missing_dims)
        )

    if not any(re.search(r"\b%s\b" % re.escape(v), haystack) for v in VERDICTS):
        lines.append("NO-VERDICT: the review states no ship/no-ship position.")

    return lines, blind_spots
