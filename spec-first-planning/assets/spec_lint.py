#!/usr/bin/env python3
"""spec_lint.py — deterministic linter for spec-first-planning specs.

A spec is a markdown file with the sections defined in
references/spec-template.md. This linter enforces the mechanical half of
"every requirement is falsifiable":

  1. Required sections present and non-empty: Problem, Users, Goals,
     Non-goals, Requirements, Acceptance criteria, Open questions
     (Open questions is required to exist but may be an empty list).
  2. Requirements are bullets with ids R1..Rn, in order, no gaps, no
     duplicates.
  3. Every requirement contains a modal obligation ("must" or "shall").
  4. No vague term (fast, robust, user-friendly, ...) in a requirement
     unless the same requirement carries a metric (a digit, %, or a
     bound like <= / >=).
  5. Every requirement has at least one acceptance criterion referencing
     its id; criteria may not reference unknown ids.

Usage:
    python3 spec_lint.py <spec.md>

Output: one "FAIL: ..." line per issue, then a final
"LINT_RESULT: PASS" or "LINT_RESULT: FAIL (n issue(s))" line.
Exit 0 iff the spec is clean. Stdlib-only, offline, deterministic.
"""

import re
import sys
from collections import OrderedDict

REQUIRED_SECTIONS = (
    "Problem",
    "Users",
    "Goals",
    "Non-goals",
    "Constraints",
    "Required truths",
    "Requirements",
    "Acceptance criteria",
    "Open questions",
)

# Constrain step: typed constraints. Anchor step: required truths that must
# hold, each traced back to a constraint and forward to a requirement.
CONSTRAINT_TYPES = ("invariant", "goal", "boundary")
TRUTH_STATUSES = ("SATISFIED", "PARTIAL", "NOT_SATISFIED", "SPECIFICATION_READY")
_CONSTRAINT_RE = re.compile(r"^(?:\*\*)?([BTUSO][0-9]+)(?:\*\*)?\s*\[([A-Za-z_]+)\]\s*:\s*(.+)$")
# The statement (group 3) may itself contain parentheses — e.g. "The
# (parenthetical) thing happens." — so the split into statement vs. field
# list is anchored on the LAST "(parent:" in the line (greedy `.*` finds the
# rightmost match by backtracking from the end), not the first "(".  A
# bullet with no "(parent:" at all has no field list and is malformed.
_TRUTH_RE = re.compile(r"^(?:\*\*)?RT([0-9]+)(?:\*\*)?\s*\[([A-Za-z_]+)\]\s*:\s*(.*)\s*\(\s*(?i:parent)\s*:(.*)\)\s*$")
_ID_LIST_RE = re.compile(r"[A-Za-z]+[0-9]+")

# Tension step (full loop, --converged): trade-offs / resource tensions /
# hidden dependencies between constraints, and how each was resolved.
TENSION_TYPES = ("trade_off", "resource_tension", "hidden_dependency")
TENSION_STATUSES = ("resolved", "accepted")
TENSION_STRATEGIES = ("Prioritize", "Partition", "Transform", "Accept", "Invalidate")
# Same last-"(field:"-anchored split as _TRUTH_RE, anchored on "(between:".
_TENSION_RE = re.compile(r"^(?:\*\*)?TN([0-9]+)(?:\*\*)?\s*\[([A-Za-z_]+)\]\s*:\s*(.*)\s*\(\s*(?i:between)\s*:(.*)\)\s*$")

# Choose step: solution options and the pragmatic recommendation.
COMPLEXITY_RANK = {"Low": 0, "Medium": 1, "High": 2}
REVERSIBILITY_RANK = {"TWO_WAY": 0, "REVERSIBLE_WITH_COST": 1, "ONE_WAY": 2}
# Anchored on "(complexity:", same style as _TRUTH_RE / _TENSION_RE.
_OPTION_RE = re.compile(r"^(?:\*\*)?OPT-([A-Z])(?:\*\*)?\s*:\s*(.*)\s*\(\s*(?i:complexity)\s*:(.*)\)\s*$")
_RECOMMENDED_RE = re.compile(r"^Recommended:\s*(OPT-[A-Z])\b(.*)$")
_RECOMMENDED_DECISION_RE = re.compile(r"\(decision:\s*(D[0-9]+)\)")

# Iterations: one bullet per pass through the loop, I1..In, capped at 5.
ITERATION_CAP = 5
_ITERATION_RE = re.compile(r"^(?:\*\*)?I([0-9]+)(?:\*\*)?\s*:\s*(.*)$")

# Decisions: the unattended-mode sweep, D1..Dn. "->" and "→" both accepted.
_DECISION_RE = re.compile(r"^D([0-9]+)\s*:\s*(.*?)\s*(?:->|→)\s*(.*?)\s*\(source:\s*(.*)\)\s*$")

# Sections that must exist but are allowed to have an empty body.
MAY_BE_EMPTY = frozenset({"Open questions"})

# A modal obligation makes the statement a requirement rather than a wish.
MODAL_RE = re.compile(r"\b(must|shall)\b", re.IGNORECASE)

# A "metric" is any digit, a percent sign, or an explicit bound. Its
# presence in the requirement text is what licenses an otherwise-vague word.
METRIC_RE = re.compile(r"[0-9%]|<=|>=|≤|≥")

# Vague terms that are opinions until a number or checkable bound follows.
VAGUE_TERMS = (
    "fast",
    "quick",
    "quickly",
    "easy",
    "simple",
    "intuitive",
    "user-friendly",
    "user friendly",
    "robust",
    "reliable",
    "scalable",
    "performant",
    "efficient",
    "seamless",
    "responsive",
    "lightweight",
)
_VAGUE_RES = tuple(
    (term, re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE))
    for term in VAGUE_TERMS
)

_H1_RE = re.compile(r"^#\s+(.+?)\s*$")
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
_RID_PREFIX_RE = re.compile(r"^(?:\*\*)?R(\d+)(?:\*\*)?\s*[:.]\s*(.*)$")
_RID_REF_RE = re.compile(r"\bR(\d+)\b")


def _fields(raw):
    """Split 'a: x; b: y; check: anything; even; semicolons' into a dict."""
    head, sep, check = raw.partition("check:")
    out = {"check": check.strip() if sep else ""}
    for part in head.split(";"):
        k, s, v = part.partition(":")
        if s:
            out[k.strip().lower()] = v.strip()
    return out


def _parse_constraints(sections):
    good, bad = [], []
    for b in _section_bullets(sections, "Constraints"):
        m = _CONSTRAINT_RE.match(b)
        if m:
            good.append({"id": m.group(1), "type": m.group(2).lower(), "text": m.group(3).strip()})
        else:
            bad.append(b)
    return good, bad


def _parse_truths(sections):
    good, bad = [], []
    for b in _section_bullets(sections, "Required truths"):
        m = _TRUTH_RE.match(b)
        if not m:
            bad.append(b)
            continue
        # group(4) is everything after "parent:"; put the label back so
        # _fields sees the same "parent: ...; maps_to: ...; ..." string it
        # always has.
        f = _fields("parent:" + m.group(4))
        try:
            conf = float(f.get("confidence", ""))
        except ValueError:
            conf = None
        good.append({"id": "RT%s" % m.group(1), "num": int(m.group(1)),
                     "status": m.group(2).upper(), "text": m.group(3).strip(),
                     "parent": f.get("parent", "").strip(),
                     "maps_to": _ID_LIST_RE.findall(f.get("maps_to", "")),
                     "reqs": [int(n) for n in re.findall(r"R([0-9]+)", f.get("reqs", ""))],
                     "confidence": conf, "check": f.get("check", "")})
    return good, bad


def _parse_tensions(sections):
    good, bad = [], []
    for b in _section_bullets(sections, "Tensions"):
        if b.strip().lower() == "none":
            continue
        m = _TENSION_RE.match(b)
        if not m:
            bad.append(b)
            continue
        f = _fields("between:" + m.group(4))
        good.append({
            "id": "TN%s" % m.group(1),
            "type": m.group(2).lower(),
            "text": m.group(3).strip(),
            "between": _ID_LIST_RE.findall(f.get("between", "")),
            "status": f.get("status", "").strip().lower(),
            "strategy": f.get("strategy", "").strip(),
            "decision": f.get("decision", "").strip() or None,
        })
    return good, bad


def _parse_options(sections):
    good, bad = [], []
    for b in _section_bullets(sections, "Solution options"):
        m = _OPTION_RE.match(b)
        if not m:
            bad.append(b)
            continue
        f = _fields("complexity:" + m.group(3))
        good.append({
            "id": "OPT-%s" % m.group(1),
            "text": m.group(2).strip(),
            "complexity": f.get("complexity", "").strip(),
            "reversibility": f.get("reversibility", "").strip(),
            "satisfies": _ID_LIST_RE.findall(f.get("satisfies", "")),
        })
    return good, bad


def _parse_recommended(sections):
    """The single non-bullet 'Recommended: OPT-<LETTER> — ...' line, or None."""
    for line in find_section(sections, "Solution options") or []:
        m = _RECOMMENDED_RE.match(line.strip())
        if m:
            dm = _RECOMMENDED_DECISION_RE.search(m.group(2))
            return {"id": m.group(1), "decision": dm.group(1) if dm else None}
    return None


def _parse_iterations(sections):
    return [int(m.group(1)) for m in
            (_ITERATION_RE.match(b) for b in _section_bullets(sections, "Iterations"))
            if m]


def _parse_decisions(sections):
    good, bad = [], []
    for b in _section_bullets(sections, "Decisions"):
        m = _DECISION_RE.match(b)
        if not m:
            bad.append(b)
            continue
        good.append({"id": "D%s" % m.group(1), "question": m.group(2).strip(),
                     "answer": m.group(3).strip(), "source": m.group(4).strip()})
    return good, bad


def _reaches_outcome(start_id, parent_of):
    """Follow `parent` links from start_id; True iff they terminate at OUTCOME.

    False for a dangling parent (points outside parent_of and isn't OUTCOME)
    and for a cycle (a node is revisited before OUTCOME is reached)."""
    cur = start_id
    seen = set()
    while cur != "OUTCOME":
        if cur is None or cur in seen:
            return False
        seen.add(cur)
        cur = parent_of.get(cur)
    return True


def lint_light(spec):
    """Constrain + Anchor light-pass rules: typed constraints, required truths,
    and their traceability (constraint -> RT -> requirement). Always on."""
    issues = []
    cons, truths = spec["constraints"], spec["truths"]
    for b in spec["malformed_constraints"]:
        issues.append("Constraints bullet is not '- <B|T|U|S|O><n> [type]: ...': '%s'" % b[:60])
    for b in spec["malformed_truths"]:
        issues.append("Required truths bullet is not '- RT<n> [status]: ... (parent: ...; "
                      "maps_to: ...; reqs: ...; confidence: ...; check: ...)': '%s'" % b[:60])
    seen = set()
    for c in cons:
        if c["id"] in seen:
            issues.append("constraint %s is defined twice" % c["id"])
        seen.add(c["id"])
        if c["type"] not in CONSTRAINT_TYPES:
            issues.append("constraint %s has type '%s'; use invariant, goal or boundary"
                          % (c["id"], c["type"]))
    known_c = {c["id"] for c in cons}
    known_r = {n for n, _ in spec["requirements"]}
    nums = [t["num"] for t in truths]
    if truths and nums != list(range(1, len(nums) + 1)):
        issues.append("required truth ids must be RT1..RT%d in order" % len(nums))
    known_t = {t["id"] for t in truths}
    seen_t = set()
    for t in truths:
        if t["id"] in seen_t:
            issues.append("required truth %s is defined twice" % t["id"])
        seen_t.add(t["id"])
        if t["status"] not in TRUTH_STATUSES:
            issues.append("%s has status '%s'; use one of %s"
                          % (t["id"], t["status"], ", ".join(TRUTH_STATUSES)))
        if t["parent"] != "OUTCOME" and (t["parent"] not in known_t or t["parent"] == t["id"]):
            issues.append("%s parent '%s' must be OUTCOME or another RT" % (t["id"], t["parent"]))
        if not t["maps_to"]:
            issues.append("%s maps to no constraint" % t["id"])
        for cid in t["maps_to"]:
            if cid not in known_c:
                issues.append("%s maps to unknown constraint %s" % (t["id"], cid))
        if not t["reqs"]:
            issues.append("%s names no requirement (reqs: R<n>)" % t["id"])
        for r in t["reqs"]:
            if r not in known_r:
                issues.append("%s names unknown requirement R%d" % (t["id"], r))
        if t["confidence"] is None or not 0.0 <= t["confidence"] <= 1.0:
            issues.append("%s confidence must be a number from 0 to 1" % t["id"])
        if not t["check"]:
            issues.append("%s has no runnable check (end the fields with 'check: ...')" % t["id"])
    mapped = {cid for t in truths for cid in t["maps_to"]}
    for c in cons:
        if c["id"] not in mapped:
            issues.append("constraint %s has no required truth mapping to it" % c["id"])
    # Every RT must reach OUTCOME by following parent links — not just have a
    # non-dangling immediate parent. Catches a cycle among otherwise-valid RTs
    # (e.g. RT1 -> RT2 -> RT1) that the per-RT parent check above can't see.
    parent_of = {t["id"]: t["parent"] for t in truths}
    for t in truths:
        if not _reaches_outcome(t["id"], parent_of):
            issues.append("%s does not trace back to OUTCOME (cycle or dangling parent)" % t["id"])
    return issues


def lint_converged(spec):
    """Tension + Choose full-loop rules (design spec §4), plus convergence
    itself. Gated behind --converged (and --unattended, which implies it)."""
    issues = []
    sections = spec["sections"]

    for name in ("Tensions", "Solution options", "Iterations"):
        if find_section(sections, name) is None:
            issues.append("missing required section '## %s' for convergence" % name)

    for b in spec["malformed_tensions"]:
        issues.append("Tensions bullet is not '- TN<n> [type]: ... (between: ...; "
                      "status: ...; strategy: ...; decision: ...)': '%s'" % b[:60])
    for b in spec["malformed_options"]:
        issues.append("Solution options bullet is not '- OPT-<LETTER>: ... "
                      "(complexity: ...; reversibility: ...; satisfies: ...)': '%s'" % b[:60])

    tensions, options, decisions = spec["tensions"], spec["options"], spec["decisions"]
    known_c = {c["id"] for c in spec["constraints"]}
    known_d = {d["id"] for d in decisions}

    # (11) tension grammar, types, known between ids (>= 2), resolved or a decision.
    seen_tn = set()
    for t in tensions:
        if t["id"] in seen_tn:
            issues.append("tension %s is defined twice" % t["id"])
        seen_tn.add(t["id"])
        if t["type"] not in TENSION_TYPES:
            issues.append("%s has type '%s'; use %s" % (t["id"], t["type"], ", ".join(TENSION_TYPES)))
        if len(set(t["between"])) < 2:
            issues.append("%s must name at least 2 ids in 'between:'" % t["id"])
        for cid in t["between"]:
            if cid not in known_c:
                issues.append("%s between id '%s' is not a known constraint" % (t["id"], cid))
        if t["status"] not in TENSION_STATUSES:
            issues.append("%s has status '%s'; use resolved or accepted" % (t["id"], t["status"]))
        if t["strategy"] not in TENSION_STRATEGIES:
            issues.append("%s has strategy '%s'; use one of %s"
                          % (t["id"], t["strategy"], ", ".join(TENSION_STRATEGIES)))
        needs_decision = t["status"] == "accepted" or t["strategy"] == "Accept"
        if needs_decision and not t["decision"]:
            issues.append("%s needs a decision: status 'accepted' or strategy 'Accept' "
                          "requires a 'decision: D<k>' field" % t["id"])
        elif t["status"] != "resolved" and not t["decision"]:
            issues.append("%s must have status 'resolved' or cite a decision" % t["id"])
        if t["decision"] and t["decision"] not in known_d:
            issues.append("%s references unknown decision %s" % (t["id"], t["decision"]))

    # (12) every RT must be ready to converge.
    for t in spec["truths"]:
        if t["status"] not in ("SATISFIED", "SPECIFICATION_READY"):
            issues.append("%s must be SATISFIED or SPECIFICATION_READY to converge (is %s)"
                          % (t["id"], t["status"]))

    # (13) option grammar, 2-4 options, a Recommended line naming a known option.
    seen_opt = set()
    for o in options:
        if o["id"] in seen_opt:
            issues.append("option %s is defined twice" % o["id"])
        seen_opt.add(o["id"])
        if o["complexity"] not in COMPLEXITY_RANK:
            issues.append("%s has complexity '%s'; use Low, Medium or High" % (o["id"], o["complexity"]))
        if o["reversibility"] not in REVERSIBILITY_RANK:
            issues.append("%s has reversibility '%s'; use TWO_WAY, REVERSIBLE_WITH_COST or ONE_WAY"
                          % (o["id"], o["reversibility"]))
        if not o["satisfies"]:
            issues.append("%s satisfies no required truth" % o["id"])
    if not 2 <= len(options) <= 4:
        issues.append("Solution options must list 2-4 options (got %d)" % len(options))

    recommended = spec["recommended"]
    known_opt = {o["id"] for o in options}
    if recommended is None:
        issues.append("Solution options has no 'Recommended: OPT-<LETTER> — ...' line")
    elif recommended["id"] not in known_opt:
        issues.append("Recommended line names unknown option %s" % recommended["id"])
    else:
        if recommended["decision"] and recommended["decision"] not in known_d:
            issues.append("Recommended line references unknown decision %s" % recommended["decision"])
        # (14) the recommendation satisfies every RT, and is the pragmatic
        # choice: among the options that satisfy every RT, the lowest
        # (complexity rank, reversibility rank). A tie needs a decision.
        rt_ids = {t["id"] for t in spec["truths"]}
        opt_by_id = {o["id"]: o for o in options}
        rec = opt_by_id[recommended["id"]]
        missing = sorted(rt_ids - set(rec["satisfies"]))
        if missing:
            issues.append("%s does not satisfy %s" % (rec["id"], ", ".join(missing)))
        else:
            def _rank(o):
                return (COMPLEXITY_RANK.get(o["complexity"], 99),
                        REVERSIBILITY_RANK.get(o["reversibility"], 99))
            candidates = [o for o in options if rt_ids <= set(o["satisfies"])]
            rec_rank = _rank(rec)
            better = [o["id"] for o in candidates if o["id"] != rec["id"] and _rank(o) < rec_rank]
            if better:
                issues.append("%s is not the pragmatic choice — %s has a lower "
                              "(complexity, reversibility) rank" % (rec["id"], ", ".join(better)))
            else:
                tied = [o["id"] for o in candidates if o["id"] != rec["id"] and _rank(o) == rec_rank]
                if tied and not recommended["decision"]:
                    issues.append("%s ties with %s on (complexity, reversibility) — the "
                                  "Recommended line needs a '(decision: D<k>)'"
                                  % (rec["id"], ", ".join(tied)))

    # (15) iterations I1..In, in order, capped at 5.
    its = spec["iterations"]
    if its and its != list(range(1, len(its) + 1)):
        issues.append("iterations must be numbered I1..I%d in order without gaps or duplicates"
                      % len(its))
    if len(its) > ITERATION_CAP:
        issues.append("iterations: iteration cap exceeded — stop and ask the user "
                      "(got %d, max %d)" % (len(its), ITERATION_CAP))

    # (16) Open questions must be empty to converge.
    if _section_bullets(sections, "Open questions"):
        issues.append("Open questions must be empty to converge")

    return issues


def lint_unattended(spec):
    """The decision sweep on top of --converged: Decisions present, non-empty,
    every decision answered."""
    issues = []
    body = find_section(spec["sections"], "Decisions")
    if body is None:
        issues.append("missing required section '## Decisions' for unattended mode")
    elif not any(ln.strip() for ln in body):
        issues.append("section '## Decisions' is empty; unattended mode needs at least one decision")
    for b in spec["malformed_decisions"]:
        issues.append("Decisions bullet is not '- D<n>: <question> -> <answer> "
                      "(source: <where>)': '%s'" % b[:60])
    for d in spec["decisions"]:
        if not d["answer"]:
            issues.append("%s has no answer — every decision must be answered "
                          "before unattended mode" % d["id"])
    return issues


def parse_spec(text):
    """Parse spec markdown into a structure shared with spec_to_tasks.py.

    Returns a dict:
      title                   -- H1 text with any leading "Spec:" stripped ("" if absent)
      sections                -- OrderedDict of raw heading -> list of body lines
      requirements            -- list of (number:int, text:str) in document order
      malformed_requirements  -- bullets in Requirements without an R<n> prefix
      criteria                -- list of (text:str, [referenced numbers]) in order
      constraints             -- list of {id, type, text} from ## Constraints
      malformed_constraints   -- bullets in Constraints not matching the grammar
      truths                  -- list of {id, num, status, text, parent, maps_to,
                                  reqs, confidence, check} from ## Required truths
      malformed_truths        -- bullets in Required truths not matching the grammar
      tensions                -- list of {id, type, text, between, status, strategy,
                                  decision} from ## Tensions (full loop)
      malformed_tensions      -- bullets in Tensions not matching the grammar
      options                 -- list of {id, text, complexity, reversibility,
                                  satisfies} from ## Solution options
      malformed_options       -- bullets in Solution options not matching the grammar
      recommended             -- {id, decision} from the 'Recommended: OPT-...' line,
                                  or None
      iterations               -- list[int] of iteration numbers from ## Iterations
      decisions                -- list of {id, question, answer, source} from
                                  ## Decisions (unattended mode)
      malformed_decisions      -- bullets in Decisions not matching the grammar
    """
    title = ""
    sections = OrderedDict()
    current = None
    for line in text.splitlines():
        h2 = _H2_RE.match(line)
        if h2:
            current = h2.group(1)
            sections.setdefault(current, [])
            continue
        if current is None and not title:
            h1 = _H1_RE.match(line)
            if h1 and not line.startswith("##"):
                title = re.sub(r"^Spec\s*:\s*", "", h1.group(1), flags=re.IGNORECASE)
                continue
        if current is not None:
            sections[current].append(line)

    requirements = []
    malformed = []
    for bullet in _section_bullets(sections, "Requirements"):
        m = _RID_PREFIX_RE.match(bullet)
        if m:
            requirements.append((int(m.group(1)), m.group(2).strip()))
        else:
            malformed.append(bullet)

    # A criterion is OWNED by the requirement in its leading `R<n>:` prefix.
    # `_RID_REF_RE` matches an R<n> token anywhere — in a filename, a command,
    # or prose — so a criterion reading "R1: run `grep R2 fixtures.txt`" used to
    # be counted as covering R2 as well, reporting total coverage for a
    # requirement with no real check behind it. Mentions are still recorded
    # (deduped) for the unknown-id diagnostic; ownership is what drives
    # coverage. A criterion with no prefix falls back to its mentions, so an
    # older spec that never used the prefix form still works.
    criteria = []
    for bullet in _section_bullets(sections, "Acceptance criteria"):
        refs = list(dict.fromkeys(int(n) for n in _RID_REF_RE.findall(bullet)))
        pm = _RID_PREFIX_RE.match(bullet)
        owner = int(pm.group(1)) if pm else None
        criteria.append((bullet, refs, owner))

    constraints, bad_c = _parse_constraints(sections)
    truths, bad_t = _parse_truths(sections)
    tensions, bad_tn = _parse_tensions(sections)
    options, bad_opt = _parse_options(sections)
    recommended = _parse_recommended(sections)
    iterations = _parse_iterations(sections)
    decisions, bad_dec = _parse_decisions(sections)

    return {
        "title": title,
        "sections": sections,
        "requirements": requirements,
        "malformed_requirements": malformed,
        "criteria": criteria,
        "constraints": constraints,
        "malformed_constraints": bad_c,
        "truths": truths,
        "malformed_truths": bad_t,
        "tensions": tensions,
        "malformed_tensions": bad_tn,
        "options": options,
        "malformed_options": bad_opt,
        "recommended": recommended,
        "iterations": iterations,
        "decisions": decisions,
        "malformed_decisions": bad_dec,
    }


def find_section(sections, name):
    """Case-insensitive section lookup; returns body lines or None."""
    want = name.strip().lower()
    for raw, body in sections.items():
        if raw.strip().lower() == want:
            return body
    return None


def _section_bullets(sections, name):
    body = find_section(sections, name)
    if body is None:
        return []
    out = []
    for line in body:
        m = _BULLET_RE.match(line)
        if m:
            out.append(m.group(1))
    return out


def lint(text, mode="light"):
    """Return the (deterministic, ordered) list of issue strings; [] = clean.

    mode is one of:
      "light"      -- (default, backward compatible) Constrain + Anchor rules
                       that run on every spec, attended or unattended.
      "converged"  -- light, plus the Tension + Choose full-loop rules and
                       convergence (design spec §4).
      "unattended" -- converged, plus the decision sweep: Decisions present,
                       non-empty, every decision answered.
    """
    if mode not in ("light", "converged", "unattended"):
        raise ValueError("mode must be 'light', 'converged' or 'unattended', got %r" % (mode,))
    issues = []
    spec = parse_spec(text)
    sections = spec["sections"]

    # 1. Required sections present and non-empty.
    for name in REQUIRED_SECTIONS:
        body = find_section(sections, name)
        if body is None:
            issues.append("missing required section '## %s'" % name)
        elif name not in MAY_BE_EMPTY and not any(ln.strip() for ln in body):
            issues.append("section '## %s' is empty" % name)

    reqs = spec["requirements"]
    for bullet in spec["malformed_requirements"]:
        issues.append(
            "Requirements bullet has no R<n> id prefix: '%s'" % bullet[:60]
        )
    req_body = find_section(sections, "Requirements")
    if (
        req_body is not None
        and any(ln.strip() for ln in req_body)
        and not reqs
        and not spec["malformed_requirements"]
    ):
        issues.append("Requirements section contains no '- R<n>: ...' bullets")

    # 2. Numbering: exactly R1..Rn, in order, no gaps, no duplicates.
    nums = [n for n, _ in reqs]
    if reqs and nums != list(range(1, len(nums) + 1)):
        issues.append(
            "requirement ids must be R1..R%d in order without gaps or "
            "duplicates (got %s)"
            % (len(nums), ", ".join("R%d" % n for n in nums))
        )

    # 3 + 4. Per-requirement: modal obligation, no unmeasured vagueness.
    for num, rtext in reqs:
        if not MODAL_RE.search(rtext):
            issues.append(
                "R%d lacks a modal obligation ('must' or 'shall') — every "
                "requirement is a single testable must-statement" % num
            )
        if not METRIC_RE.search(rtext):
            for term, term_re in _VAGUE_RES:
                if term_re.search(rtext):
                    issues.append(
                        "R%d uses vague term '%s' with no metric — add a "
                        "number or checkable bound" % (num, term)
                    )

    # 5. Acceptance-criteria coverage.
    known = set(nums)
    covered = set()
    for ctext, refs, _owner in spec["criteria"]:
        if not refs:
            issues.append(
                "acceptance criterion references no requirement id: '%s'"
                % ctext[:60]
            )
        for ref in refs:
            if ref in known:
                covered.add(ref)
            else:
                issues.append(
                    "acceptance criterion references unknown requirement R%d"
                    % ref
                )
    for num, _ in reqs:
        if num not in covered:
            issues.append(
                "R%d has no acceptance criterion — add at least one "
                "'- R%d: <runnable check>' line" % (num, num)
            )

    # 6-9. Constrain + Anchor light pass: typed constraints, required truths,
    # and traceability from constraint to RT to requirement (always on).
    issues += lint_light(spec)

    # Tension + Choose full-loop rules, gated behind --converged/--unattended.
    if mode in ("converged", "unattended"):
        issues += lint_converged(spec)
    if mode == "unattended":
        issues += lint_unattended(spec)

    return issues


def main(argv):
    args = argv[1:]
    mode = "light"
    if args and args[0] in ("--converged", "--unattended"):
        mode = args[0][2:]
        args = args[1:]
    if len(args) != 1:
        print("usage: spec_lint.py [--converged|--unattended] <spec.md>", file=sys.stderr)
        return 2
    path = args[0]
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        print("ERROR: cannot read %s: %s" % (path, exc), file=sys.stderr)
        return 2
    issues = lint(text, mode=mode)
    for issue in issues:
        print("FAIL: %s" % issue)
    if issues:
        print("LINT_RESULT: FAIL (%d issue(s), mode=%s)" % (len(issues), mode))
        return 1
    spec = parse_spec(text)
    print(
        "LINT_RESULT: PASS (%d requirement(s), %d acceptance criteria, mode=%s)"
        % (len(spec["requirements"]), len(spec["criteria"]), mode)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
