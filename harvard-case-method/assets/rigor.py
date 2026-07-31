#!/usr/bin/env python3
"""rigor.py — the numbers gate: RICE prioritisation and plan feasibility.

`casekit.py` disciplines the decision *process*. This disciplines the
*arithmetic* underneath it — the place where a product roadmap or a business
plan quietly becomes fiction.

  rice <file>   Score and rank a RICE sheet. Refuses the failure modes that
                turn RICE into theatre: reach numbers with no source, reach
                periods that silently differ between items (a /month item
                ranked against a /quarter one), impact values off Intercom's
                scale, and confidence outside (0, 1]. Reports near-ties
                instead of pretending three significant figures separate them.

  plan <file>   Check whether a plan survives contact with arithmetic. Every
                driver must be sourced or explicitly flagged as an assumption;
                the top-down formula must actually reconcile with its drivers;
                top-down and bottom-up sizing must agree within a tolerance;
                growth trajectories are checked for hockey sticks; and the
                plan must state what would have to be true, with its least
                likely condition named.

Neither command decides anything. They refuse numbers that cannot support a
decision, which is a different and more honest job.

stdlib only, offline, deterministic.
"""
import argparse
import ast
import operator
import os
import re
import sys

# Intercom's RICE impact scale — deliberately non-linear.
IMPACT_SCALE = {3.0: "massive", 2.0: "high", 1.0: "medium",
                0.5: "low", 0.25: "minimal"}

# Two RICE scores closer than this are not distinguishable given the precision
# of a 0.25–3 impact scale and a hand-estimated effort figure.
TIE_BAND = 0.20

SIZING_TOL = 2.0   # top-down vs bottom-up, as a ratio
MAX_GROWTH = 2.0         # period-over-period multiple before it's a hockey stick
MAX_ASSUMPTION_SHARE = 0.5

PLAN_SECTIONS = ["drivers", "top down", "bottom up", "what would have to be true"]

NUMBER = r"[-+]?\d[\d,_]*(?:\.\d+)?(?:e[-+]?\d+)?"
DRIVER_LINE = re.compile(
    r"^\s*[-*]\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^|]+?)\s*(?:\|\s*(.*?))?\s*$")
TRAJECTORY_LINE = re.compile(r"^\s*[-*]\s*(\S[^|]*?)\s*\|\s*(" + NUMBER + r")\s*$")
CITATION = re.compile(r"\[\^[^\]]+\]|src\s*:", re.IGNORECASE)
ASSUMPTION = re.compile(r"\bassumption\b|\bassumed\b|\bguess\b", re.IGNORECASE)
LEAST_LIKELY = re.compile(r"\[\s*least likely\s*\]", re.IGNORECASE)

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.Pow: operator.pow}


class RigorError(Exception):
    """A user-facing failure: one clean line, never a traceback."""


def num(text):
    return float(text.replace(",", "").replace("_", "").strip())


def read(path):
    if not os.path.exists(path):
        raise RigorError("no such file: %s" % path)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def sections(text):
    out, current, buf = [], "", []
    for i, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
        if m:
            out.append((current, buf))
            current, buf = m.group(1).strip().lower(), []
        else:
            buf.append((i, line))
    out.append((current, buf))
    return out


def section_body(text, name):
    for heading, body in sections(text):
        if heading == name.lower():
            return body
    return None


def safe_eval(expr, names):
    """Evaluate an arithmetic expression over named drivers. No builtins, no
    calls, no attribute access — a plan formula is arithmetic or it is rejected."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise RigorError("cannot parse formula %r: %s" % (expr, exc.msg))

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise RigorError("formula may only contain numbers and driver names")
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise RigorError("formula references undefined driver %r" % node.id)
            return names[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            return OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -ev(node.operand)
        raise RigorError("formula may only contain + - * / ** and driver names")

    return ev(tree)


def report(results, prefix="RIGOR"):
    for rule, ok, detail in results:
        print("%s: %s — %s%s" % (prefix, rule, "PASS" if ok else "FAIL",
                                 " (%s)" % detail if detail else ""))
    failed = [r for r, ok, _ in results if not ok]
    print("%s_RESULT: %s (%d/%d rules)"
          % (prefix, "PASS" if not failed else "FAIL",
             len(results) - len(failed), len(results)))
    return 0 if not failed else 1


# ── RICE ─────────────────────────────────────────────────────────────────────

def parse_rice(text):
    """([item, ...], [error, ...]) from the ## Items section."""
    items, errors = [], []
    body = section_body(text, "items")
    if body is None:
        return [], ["no '## Items' section"]
    for lineno, line in body:
        if not line.strip() or not line.strip().startswith(("-", "*")):
            continue
        parts = [p.strip() for p in line.strip().lstrip("-*").split("|")]
        name, fields = parts[0], {}
        raw_extra = " ".join(parts[1:])
        for chunk in parts[1:]:
            m = re.match(r"^(reach|impact|confidence|effort)\s*=\s*(.+)$", chunk, re.I)
            if m:
                fields[m.group(1).lower()] = m.group(2).strip()
        missing = [k for k in ("reach", "impact", "confidence", "effort")
                   if k not in fields]
        if not name:
            errors.append("line %d: item has no name" % lineno)
            continue
        if missing:
            errors.append("line %d: %r missing %s" % (lineno, name, ", ".join(missing)))
            continue

        reach_raw = fields["reach"]
        m = re.match(r"^(" + NUMBER + r")\s*(?:/|\bper\b)\s*(\w+)\s*$", reach_raw, re.I)
        if not m:
            errors.append("line %d: %r reach=%r needs a period, e.g. `1200/quarter` — "
                          "reach without a time window cannot be compared"
                          % (lineno, name, reach_raw))
            continue
        try:
            item = {"line": lineno, "name": name,
                    "reach": num(m.group(1)), "period": m.group(2).lower(),
                    "impact": num(fields["impact"]),
                    "confidence": num(fields["confidence"]),
                    "effort": num(fields["effort"]),
                    "sourced": bool(CITATION.search(raw_extra))}
        except ValueError:
            errors.append("line %d: %r has a non-numeric field" % (lineno, name))
            continue
        item["score"] = (item["reach"] * item["impact"] * item["confidence"]
                         / item["effort"]) if item["effort"] else 0.0
        items.append(item)
    return items, errors


def lint_rice(items, errors):
    results = [("R0 every-item-parses", not errors, "; ".join(errors[:3]))]
    results.append(("R1 at-least-two-items", len(items) >= 2,
                    "%d item(s) — RICE ranks; one item ranks nothing" % len(items)
                    if len(items) < 2 else ""))

    periods = {i["period"] for i in items}
    results.append(("R2 one-reach-period", len(periods) <= 1,
                    "mixed periods %s — a /month item cannot be ranked against a "
                    "/quarter one" % sorted(periods) if len(periods) > 1 else ""))

    unsourced = [i["name"] for i in items if not i["sourced"]]
    results.append(("R3 reach-is-sourced", not unsourced,
                    "no source on: %s" % ", ".join(unsourced[:4]) if unsourced else ""))

    off = ["%s=%g" % (i["name"], i["impact"]) for i in items
           if i["impact"] not in IMPACT_SCALE]
    results.append(("R4 impact-on-the-scale", not off,
                    "%s; allowed: %s" % ("; ".join(off[:3]),
                                         ", ".join("%g" % k for k in sorted(IMPACT_SCALE, reverse=True)))
                    if off else ""))

    bad_c = ["%s=%g" % (i["name"], i["confidence"]) for i in items
             if not 0 < i["confidence"] <= 1]
    results.append(("R5 confidence-is-a-fraction", not bad_c,
                    "%s — use 0 < c <= 1 (0.8 = strong evidence, 0.5 = informed guess)"
                    % "; ".join(bad_c[:3]) if bad_c else ""))

    bad_e = ["%s=%g" % (i["name"], i["effort"]) for i in items if i["effort"] <= 0]
    results.append(("R6 effort-is-positive", not bad_e, "; ".join(bad_e[:3])))

    return results


def tie_groups(items):
    """[[item, ...]] of adjacent items whose scores are within TIE_BAND."""
    ranked = sorted(items, key=lambda i: -i["score"])
    groups, current = [], []
    for item in ranked:
        if current and current[-1]["score"] > 0 and \
                (current[-1]["score"] - item["score"]) / current[-1]["score"] <= TIE_BAND:
            current.append(item)
        else:
            if len(current) > 1:
                groups.append(current)
            current = [item]
    if len(current) > 1:
        groups.append(current)
    return groups


def cmd_rice(args):
    text = read(args.file)
    items, errors = parse_rice(text)
    results = lint_rice(items, errors)
    code = report(results, "RICE")
    if not items:
        return code or 1
    if code:
        # Ranking an invalid sheet publishes numbers that look authoritative and
        # are not — an off-scale impact or a confidence of 150 produces a score
        # that would out-rank every honest row. Refuse rather than mislead.
        print("\nNo ranking printed: the sheet failed validation above, and a score")
        print("computed from invalid inputs out-ranks the honest rows. Fix, then re-run.")
        return code

    print("\nRanked (score = reach × impact × confidence ÷ effort):")
    width = max(len(i["name"]) for i in items)
    for rank, item in enumerate(sorted(items, key=lambda i: -i["score"]), 1):
        print("  %2d. %-*s  %8.0f   [R %g/%s · I %g (%s) · C %.0f%% · E %g]"
              % (rank, width, item["name"], item["score"], item["reach"],
                 item["period"], item["impact"],
                 IMPACT_SCALE.get(item["impact"], "off-scale"),
                 item["confidence"] * 100, item["effort"]))

    groups = tie_groups(items)
    if groups:
        print("\nNot distinguishable (within %d%% — the inputs are not that precise):"
              % int(TIE_BAND * 100))
        for group in groups:
            print("  • %s" % " ≈ ".join(i["name"] for i in group))
        print("Break these ties on strategy, sequencing or dependencies, not on the score.")

    low = [i["name"] for i in items if i["confidence"] <= 0.5]
    if low:
        print("\nConfidence at or below 50% (an informed guess, per Intercom's anchors):")
        print("  %s" % ", ".join(low))
        print("Low confidence is a signal to go get evidence, not a discount factor to")
        print("multiply through and forget.")
    return code


# ── Plan feasibility ─────────────────────────────────────────────────────────

def parse_drivers(text, section):
    """({name: value}, [(name, sourced, lineno)], [error, ...])"""
    values, meta, errors = {}, [], []
    body = section_body(text, section)
    if body is None:
        return values, meta, ["no '## %s' section" % section.title()]
    for lineno, line in body:
        if not line.strip() or not line.strip().startswith(("-", "*")):
            continue
        m = DRIVER_LINE.match(line)
        if not m:
            errors.append("line %d: expected `- name = value | src: …`" % lineno)
            continue
        name, expr, tail = m.group(1), m.group(2).strip(), (m.group(3) or "")
        try:
            values[name] = safe_eval(expr, dict(values))
        except RigorError as exc:
            errors.append("line %d: %s" % (lineno, exc))
            continue
        meta.append((name, bool(CITATION.search(tail)), bool(ASSUMPTION.search(tail)),
                     lineno))
    return values, meta, errors


def lint_plan(text, tolerance, max_growth):
    results = []
    missing = [s for s in PLAN_SECTIONS if section_body(text, s) is None]
    results.append(("P0 has-every-section", not missing,
                    "missing: %s" % ", ".join("## " + m.title() for m in missing)
                    if missing else ""))
    if missing:
        return results

    drivers, meta, errors = parse_drivers(text, "drivers")
    results.append(("P1 drivers-parse", not errors, "; ".join(errors[:3])))

    silent = ["%s (line %d)" % (n, ln) for n, sourced, assumed, ln in meta
              if not sourced and not assumed]
    results.append(("P2 every-driver-sourced-or-flagged", not silent,
                    "neither cited nor marked an assumption: %s" % "; ".join(silent[:4])
                    if silent else ""))

    assumed = sum(1 for _, sourced, a, _ in meta if a and not sourced)
    share = assumed / len(meta) if meta else 0.0
    results.append(("P3 assumptions-are-a-minority", share <= MAX_ASSUMPTION_SHARE,
                    "%d of %d drivers are unsourced assumptions (%.0f%%) — above %.0f%% "
                    "this is a hypothesis, not a plan"
                    % (assumed, len(meta), share * 100, MAX_ASSUMPTION_SHARE * 100)
                    if share > MAX_ASSUMPTION_SHARE else ""))

    # The top-down formula is evaluated with the ## Drivers already in scope —
    # that is the whole point of a driver tree. Parsing the section in isolation
    # would report every driver reference as undefined.
    top, top_err = None, None
    scope = dict(drivers)
    for lineno, line in section_body(text, "top down"):
        m = DRIVER_LINE.match(line)
        if not m:
            continue
        try:
            top = safe_eval(m.group(2).strip(), scope)
            scope[m.group(1)] = top
        except RigorError as exc:
            top_err, top = "line %d: %s" % (lineno, exc), None
            break
    results.append(("P4 top-down-reconciles", top is not None,
                    top_err or ("no computable top-down figure — state it as a formula "
                                "over the drivers, e.g. `- revenue = a * b * c`"
                                if top is None else "")))

    bottom, bottom_err = None, None
    for lineno, line in section_body(text, "bottom up"):
        m = DRIVER_LINE.match(line)
        if not m:
            continue
        try:
            bottom = safe_eval(m.group(2).strip(), dict(drivers))
        except RigorError as exc:
            bottom_err = "line %d: %s" % (lineno, exc)
    results.append(("P5 bottom-up-present", bottom is not None,
                    bottom_err or ("no bottom-up figure to cross-check — a single "
                                   "sizing method cannot catch its own error"
                                   if bottom is None else "")))

    if top is not None and bottom is not None and top > 0 and bottom > 0:
        ratio = max(top / bottom, bottom / top)
        ok = ratio <= tolerance
        results.append(("P6 sizings-agree", ok,
                        "top-down %.0f vs bottom-up %.0f — %.1f× apart (tolerance %.1f×). "
                        "Two methods this far apart mean at least one is fiction."
                        % (top, bottom, ratio, tolerance) if not ok else
                        "%.1f× apart, within %.1f×" % (ratio, tolerance)))
    else:
        results.append(("P6 sizings-agree", False,
                        "cannot cross-check without both a top-down and a bottom-up figure"))

    traj, spikes = [], []
    body = section_body(text, "trajectory") or []
    for lineno, line in body:
        m = TRAJECTORY_LINE.match(line)
        if m:
            traj.append((m.group(1), num(m.group(2))))
    for i in range(1, len(traj)):
        prev, cur = traj[i - 1][1], traj[i][1]
        if prev > 0 and cur / prev > max_growth:
            spikes.append("%s→%s ×%.1f" % (traj[i - 1][0], traj[i][0], cur / prev))
    if traj:
        results.append(("P7 no-hockey-stick", not spikes,
                        "period-over-period growth above %.1f×: %s — state what capacity "
                        "delivers this, or flatten it"
                        % (max_growth, "; ".join(spikes[:4])) if spikes else
                        "%d periods, max growth within %.1f×" % (len(traj), max_growth)))

    wwhtbt = [l.strip() for _, l in section_body(text, "what would have to be true")
              if l.strip().startswith(("-", "*"))]
    marked = [c for c in wwhtbt if LEAST_LIKELY.search(c)]
    results.append(("P8 states-what-must-be-true", len(wwhtbt) >= 2,
                    "%d condition(s); 2+ required" % len(wwhtbt) if len(wwhtbt) < 2 else ""))
    results.append(("P9 names-the-weakest-condition", len(marked) == 1,
                    "mark exactly one condition `[least likely]` — that is the one to "
                    "test first (found %d)" % len(marked) if len(marked) != 1 else ""))
    return results


def cmd_plan(args):
    text = read(args.file)
    results = lint_plan(text, args.tolerance, args.max_growth)
    code = report(results, "PLAN")
    if code:
        print("\nA plan that fails these is not necessarily wrong — it is unfalsifiable,")
        print("which is the thing that makes a forecast read as a fairytale.")
    return code


def build_parser():
    p = argparse.ArgumentParser(prog="rigor.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command")

    r = sub.add_parser("rice", help="score and rank a RICE sheet")
    r.add_argument("file")
    r.set_defaults(func=cmd_rice)

    pl = sub.add_parser("plan", help="check a plan against its own arithmetic")
    pl.add_argument("file")
    pl.add_argument("--tolerance", type=float, default=SIZING_TOL,
                    metavar="X", help="top-down vs bottom-up ratio allowed (default 2.0)")
    pl.add_argument("--max-growth", type=float, default=MAX_GROWTH,
                    metavar="X", help="period-over-period multiple allowed (default 2.0)")
    pl.set_defaults(func=cmd_plan)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except RigorError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
