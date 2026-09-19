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


def lint(text):
    """Return the (deterministic, ordered) list of issue strings; [] = clean."""
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

    return issues


def main(argv):
    if len(argv) != 2:
        print("usage: spec_lint.py <spec.md>", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        print("ERROR: cannot read %s: %s" % (argv[1], exc), file=sys.stderr)
        return 2
    issues = lint(text)
    for issue in issues:
        print("FAIL: %s" % issue)
    if issues:
        print("LINT_RESULT: FAIL (%d issue(s))" % len(issues))
        return 1
    spec = parse_spec(text)
    print(
        "LINT_RESULT: PASS (%d requirement(s), %d acceptance criteria)"
        % (len(spec["requirements"]), len(spec["criteria"]))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
