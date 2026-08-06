#!/usr/bin/env python3
"""concept-first-review — offline CLI.

No API keys, no network, no model calls. The agent running the skill supplies
the judgment; this tool supplies structure, rejection, and grading.

    python3 review.py open     --rev HEAD~1..HEAD --into .review
    python3 review.py state    --into .review [--json]
    python3 review.py draft    --into .review [--plan PATH]
    python3 review.py commit   --into .review [--plan PATH]
    python3 review.py signals  --into .review [--severity high]
    python3 review.py template --into .review
    python3 review.py grade    --into .review
    python3 review.py audit    --into .review --prior SOMEONE-ELSES-REVIEW.md

Every command is idempotent and safe to re-run, and `state` reports typed
progress, so the whole sequence can be driven from a self-prompting loop.

Full flag reference: references/parameters.md
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import condenser  # noqa: E402
import diffmodel  # noqa: E402
import report as reportlib  # noqa: E402
import signals as signalslib  # noqa: E402

SOURCE = "source.diff"
INDEXED = "indexed.diff"
RELOCATIONS = "relocations.json"
SIGNALS = "signals.json"
META = "meta.json"
CONDENSED = "condensed.diff"
PLAN = "plan.json"
REVIEW = "REVIEW.md"

EMPTY_PLAN = {"edits": [], "headline": ""}


def _die(msg, code=2):
    sys.stderr.write("error: %s\n" % msg)
    raise SystemExit(code)


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _load_json(path, required=True):
    try:
        return json.loads(_read(path))
    except FileNotFoundError:
        if not required:
            return None
        _die("%s not found — run `open` first" % path)
    except ValueError as exc:
        _die("%s is not valid JSON: %s" % (path, exc))


def _into(args):
    work = args.into
    if not os.path.isdir(work):
        _die("work directory %s not found — run `open` first" % work)
    return work


# ── open ─────────────────────────────────────────────────────────────────────

def cmd_open(args):
    if args.diff == "-":
        text = sys.stdin.read()
        label = "stdin"
    elif args.diff:
        text = _read(args.diff)
        label = args.diff
    else:
        rev = args.rev or "HEAD~1..HEAD"
        cmd = ["git", "diff", "--no-color", "--no-ext-diff"] + rev.split()
        try:
            text = subprocess.check_output(cmd, cwd=args.repo, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            _die("git diff failed (%s). Pass --diff <file> instead." % exc)
        label = "git diff %s" % rev

    if not text.strip():
        _die("the diff is empty — nothing to review")

    rows = diffmodel.parse_diff(text)
    declarations = diffmodel.declaration_rows(rows)
    moves = diffmodel.detect_relocations(rows)
    baseline = signalslib.load_baseline(args.rules)
    repo_root = args.repo if args.repo and os.path.isdir(args.repo) else None
    sigs = signalslib.extract(rows, baseline, repo_root=repo_root)

    os.makedirs(args.into, exist_ok=True)
    _write(os.path.join(args.into, SOURCE), text)
    _write(os.path.join(args.into, INDEXED), diffmodel.index_diff(text) + "\n")
    _write(os.path.join(args.into, RELOCATIONS), json.dumps(moves, indent=2) + "\n")
    _write(os.path.join(args.into, SIGNALS), json.dumps(sigs, indent=2) + "\n")

    baseline_plan = condenser.compile_plan(
        text, EMPTY_PLAN, rows=rows, declarations=declarations, moves=moves
    )
    meta = {
        "label": label,
        "intent": args.intent or "",
        "rules": args.rules if args.rules and os.path.exists(args.rules) else None,
        "stats": baseline_plan.stats,
        "relocations": len(moves),
        "signals": signalslib.summarize(sigs),
    }
    _write(os.path.join(args.into, META), json.dumps(meta, indent=2) + "\n")

    plan_path = os.path.join(args.into, PLAN)
    if not os.path.exists(plan_path):
        _write(plan_path, json.dumps(EMPTY_PLAN, indent=2) + "\n")

    stats = baseline_plan.stats
    print("opened %s in %s" % (label, args.into))
    print("  read %s — coordinates come from its left-hand column" % INDEXED)
    print("  %d changed rows across %d files; %d dependency row(s) already removed"
          % (stats["total_rows"], stats["total_files"], stats["declaration_rows"]))
    for note in diffmodel.relocation_notes(moves):
        print("  " + note)
    counts = meta["signals"]["by_severity"]
    print("  signals: %d high, %d medium, %d low"
          % (counts.get("high", 0), counts.get("medium", 0), counts.get("low", 0)))
    if not meta["intent"]:
        print("  no --intent given — scope can only be judged against the change itself")
    if not meta["rules"]:
        print("  no design-rules.json in play — layering stays heuristic "
              "(see references/design-baseline.md)")
    return 0


# ── draft / commit ───────────────────────────────────────────────────────────

def _compile(work, plan_path):
    text = _read(os.path.join(work, SOURCE))
    plan = _load_json(plan_path)
    rows = diffmodel.parse_diff(text)
    return condenser.compile_plan(
        text,
        plan,
        rows=rows,
        declarations=diffmodel.declaration_rows(rows),
        moves=_load_json(os.path.join(work, RELOCATIONS)),
    )


def cmd_draft(args):
    work = _into(args)
    res = _compile(work, args.plan or os.path.join(work, PLAN))
    print(condenser.truncate(res.condensed))
    print("---")
    print(condenser.feedback(res))
    return 0 if res.ok else 1


def cmd_commit(args):
    work = _into(args)
    res = _compile(work, args.plan or os.path.join(work, PLAN))
    if not res.ok:
        print(condenser.feedback(res))
        _die("plan rejected — resolve the rejections above and draft again", 1)
    _write(os.path.join(work, CONDENSED), res.condensed)
    meta = _load_json(os.path.join(work, META))
    meta["stats"] = res.stats
    _write(os.path.join(work, META), json.dumps(meta, indent=2) + "\n")
    print("wrote %s" % os.path.join(work, CONDENSED))
    print(condenser.feedback(res))
    return 0


# ── signals / template / grade ───────────────────────────────────────────────

def cmd_signals(args):
    work = _into(args)
    sigs = _load_json(os.path.join(work, SIGNALS))
    order = {"high": 0, "medium": 1, "low": 2}
    if args.severity:
        floor = order[args.severity]
        sigs = [s for s in sigs if order.get(s["severity"], 3) <= floor]
    if args.json:
        print(json.dumps(sigs, indent=2))
        return 0
    if not sigs:
        print("no signals at this severity")
        return 0
    for sig in sigs:
        where = "%s:%d" % (sig["file"], sig["line"]) if sig["file"] else "(whole change)"
        print("[%s] %s  %s" % (sig["severity"].upper(), sig["id"], where))
        print("    evidence: %s" % sig["evidence"])
        print("    question: %s" % sig["question"])
    return 0


def cmd_template(args):
    work = _into(args)
    sigs = _load_json(os.path.join(work, SIGNALS))
    meta = _load_json(os.path.join(work, META))
    out = args.out or os.path.join(work, REVIEW)
    if os.path.exists(out) and not args.force:
        _die("%s already exists — pass --force to overwrite it" % out)
    _write(out, reportlib.template(sigs, meta.get("stats", {}), meta))
    print("wrote %s — answer every section; `grade` checks it" % out)
    return 0


def cmd_grade(args):
    work = _into(args)
    sigs = _load_json(os.path.join(work, SIGNALS))
    path = args.report or os.path.join(work, REVIEW)
    if not os.path.exists(path):
        _die("%s not found — run `template` first" % path)
    checks, ok = reportlib.grade(_read(path), sigs)
    print(reportlib.format_checks(checks))
    return 0 if ok else 1


# ── audit: a second opinion on a review someone else already wrote ───────────

def cmd_audit(args):
    work = _into(args)
    sigs = _load_json(os.path.join(work, SIGNALS))
    if not os.path.exists(args.prior):
        _die("prior review %s not found" % args.prior)
    rows = diffmodel.parse_diff(_read(os.path.join(work, SOURCE)))
    changed_paths = {r.path for r in rows if r.path}
    lines, blind_spots = reportlib.audit_prior(_read(args.prior), sigs, changed_paths)
    if args.json:
        print(json.dumps({"report": lines, "blind_spots": blind_spots}, indent=2))
    else:
        for line in lines:
            print(line)
        print("AUDIT_RESULT: %d blind spot(s) in %s"
              % (len(blind_spots), os.path.basename(args.prior)))
    return 0 if not blind_spots else 1


# ── state: the typed loop boundary ───────────────────────────────────────────

def cmd_state(args):
    state = reportlib.loop_state(_probe(args.into))
    if args.json:
        print(json.dumps(state, indent=2))
    else:
        print("phase:       %s" % state["phase"])
        print("done:        %s" % ("yes" if state["done"] else "no"))
        print("next action: %s" % state["next_action"])
        for blocker in state["blockers"]:
            print("blocker:     %s" % blocker)
    return 0


def _probe(work):
    """Gather everything `loop_state` needs, without raising on absence."""
    probe = {"work": work, "work_exists": os.path.isdir(work)}
    if not probe["work_exists"]:
        return probe
    probe["has_source"] = os.path.exists(os.path.join(work, SOURCE))
    probe["has_plan"] = os.path.exists(os.path.join(work, PLAN))
    probe["has_condensed"] = os.path.exists(os.path.join(work, CONDENSED))
    probe["has_review"] = os.path.exists(os.path.join(work, REVIEW))
    probe["signals"] = _load_json(os.path.join(work, SIGNALS), required=False) or []

    if probe["has_source"] and probe["has_plan"]:
        plan = _load_json(os.path.join(work, PLAN), required=False) or {}
        probe["plan_is_empty"] = not plan.get("edits")
        res = _compile(work, os.path.join(work, PLAN))
        probe["plan_ok"] = res.ok
        probe["plan_errors"] = list(res.errors)
        probe["stats"] = res.stats
    if probe["has_review"]:
        checks, ok = reportlib.grade(
            _read(os.path.join(work, REVIEW)), probe["signals"]
        )
        probe["review_ok"] = ok
        probe["review_gaps"] = [name for name, passed, _ in checks if not passed]
    return probe


# ── argument parsing ─────────────────────────────────────────────────────────

def _add_work(parser):
    parser.add_argument("--into", default=".review", help="work directory")


def build_parser():
    p = argparse.ArgumentParser(prog="review.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    op = sub.add_parser("open", help="index a diff and extract signals")
    op.add_argument("--diff", help="diff file, or - for stdin")
    op.add_argument("--rev", help="git revision range (default HEAD~1..HEAD)")
    op.add_argument("--repo", default=".", help="repository root for git diff")
    op.add_argument("--into", default=".review", help="work directory to create")
    op.add_argument("--rules", default="design-rules.json", help="design baseline JSON")
    op.add_argument("--intent", default="",
                    help="what the change was asked to do, in the requester's words; "
                         "carried into the review so scope can be checked against it")
    op.set_defaults(func=cmd_open)

    st = sub.add_parser("state", help="typed progress for a self-prompting loop")
    _add_work(st)
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=cmd_state)

    dr = sub.add_parser("draft", help="apply a plan and show the condensed diff")
    _add_work(dr)
    dr.add_argument("--plan")
    dr.set_defaults(func=cmd_draft)

    cm = sub.add_parser("commit", help="write condensed.diff from an accepted plan")
    _add_work(cm)
    cm.add_argument("--plan")
    cm.set_defaults(func=cmd_commit)

    sg = sub.add_parser("signals", help="list extracted review signals")
    _add_work(sg)
    sg.add_argument("--severity", choices=["high", "medium", "low"])
    sg.add_argument("--json", action="store_true")
    sg.set_defaults(func=cmd_signals)

    tp = sub.add_parser("template", help="write the REVIEW.md skeleton")
    _add_work(tp)
    tp.add_argument("--out")
    tp.add_argument("--force", action="store_true")
    tp.set_defaults(func=cmd_template)

    gr = sub.add_parser("grade", help="check a finished REVIEW.md for completeness")
    _add_work(gr)
    gr.add_argument("--report")
    gr.set_defaults(func=cmd_grade)

    au = sub.add_parser("audit", help="second-opinion a review another agent wrote")
    _add_work(au)
    au.add_argument("--prior", required=True, help="the existing review to audit")
    au.add_argument("--json", action="store_true")
    au.set_defaults(func=cmd_audit)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
