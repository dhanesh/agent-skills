#!/usr/bin/env python3
"""Gate-runnable outcome eval for base-in-reality (docs/eval-standard.md).

The skill's deliverable is agent behavior (a research-grounded audit), so this
eval grades its deterministic contract surface end-to-end: fixture findings
reports are fed through the shipped assets/report_lint.py (which reads the
shipped assets/findings.schema.json), the offline URL-building layer of
assets/fetch_sources.py is exercised for determinism, and the shipped
assets/report-skeleton.md is checked for the sections stage 6 promises.

Negative fixtures (mandatory): a fabricated-citation report (VIOLATION marked
verified with no fetched source recorded), a report missing UNCONFIRMED
handling on a downgrade, and out-of-vocabulary verdict/severity values must
all be rejected. Offline, deterministic, stdlib-only, tempdir-only writes.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
ASSETS = os.path.join(SKILL, "assets")

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = f" ({detail})" if detail else ""
    print(f"CHECK: {name} — {'PASS' if ok else 'FAIL'}{suffix}")


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ASSETS, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def finding(**over):
    f = {
        "claim": "APR computed as simple interest on a revolving balance",
        "layer": "biz",
        "location": "billing/apr.py:44",
        "verdict": "VIOLATION",
        "severity": "high",
        "recommended_fix": "compound per the periodic-rate method mandated by Reg Z",
        "citations": [{
            "title": "12 CFR Part 1026 (Regulation Z)",
            "url": "https://www.ecfr.gov/current/title-12/part-1026",
            "quote": "the annual percentage rate shall be determined by the periodic rate",
            "fetched": True,
        }],
    }
    f.update(over)
    return f


def main():
    lint = load_module("bir_report_lint", "report_lint.py")
    fetch = load_module("bir_fetch_sources", "fetch_sources.py")

    # ── Positive: a conforming fixture report is accepted ────────────────
    good_report = {"findings": [
        finding(),
        finding(claim="consensus uses a 2-phase commit variant",
                layer="arch", location="cluster/consensus.go:120",
                verdict="DEVIATION", severity="medium"),
        finding(claim="claims about cache invalidation ordering",
                layer="arch", location="cache/inval.py:9",
                verdict="UNCONFIRMED", severity="low", citations=[],
                downgraded=True),
    ]}
    errs = lint.lint_document(good_report)
    check("linter accepts schema-conforming fixture report (incl. UNCONFIRMED)",
          errs == [], "; ".join(errs)[:100])

    # ── Negative: fabricated-citation pattern rejected ───────────────────
    fabricated = finding()
    fabricated["citations"][0]["fetched"] = False
    errs = lint.lint_findings([fabricated])
    check("linter rejects VIOLATION whose only citation was never fetched (fabricated-citation)",
          any("fabricated-citation" in e for e in errs), "; ".join(errs)[:100])

    errs = lint.lint_findings([finding(citations=[])])
    check("linter rejects VIOLATION with zero citations recorded",
          any("no fetched citation" in e for e in errs))

    # ── Negative: missing UNCONFIRMED handling on a downgrade ────────────
    errs = lint.lint_findings([finding(downgraded=True)])
    check("linter rejects a refutation downgrade that skipped UNCONFIRMED",
          any("downgraded" in e and "UNCONFIRMED" in e for e in errs))

    # ── Negative: out-of-vocabulary enum values rejected ─────────────────
    errs = lint.lint_findings([finding(verdict="BROKEN")])
    check("linter rejects verdict outside the schema enum",
          any("verdict" in e for e in errs))
    errs = lint.lint_findings([finding(severity="catastrophic")])
    check("linter rejects severity outside the schema enum",
          any("severity" in e for e in errs))
    incomplete = finding()
    del incomplete["location"]
    errs = lint.lint_findings([incomplete])
    check("linter rejects finding missing a schema-required field",
          any("'location'" in e for e in errs))

    # ── CLI end-to-end in a tempdir ──────────────────────────────────────
    tmp = tempfile.mkdtemp(prefix="bir-eval-")
    try:
        good_path = os.path.join(tmp, "good.json")
        bad_path = os.path.join(tmp, "bad.json")
        with open(good_path, "w") as f:
            json.dump(good_report, f)
        with open(bad_path, "w") as f:
            json.dump([finding(citations=[])], f)
        ok = subprocess.run([sys.executable, os.path.join(ASSETS, "report_lint.py"),
                             good_path], capture_output=True, text=True, timeout=30)
        check("CLI passes the good report (exit 0, LINT_RESULT: PASS)",
              ok.returncode == 0 and "LINT_RESULT: PASS" in ok.stdout)
        bad = subprocess.run([sys.executable, os.path.join(ASSETS, "report_lint.py"),
                              bad_path], capture_output=True, text=True, timeout=30)
        check("CLI fails the ungrounded report (exit 1, LINT_RESULT: FAIL)",
              bad.returncode == 1 and "LINT_RESULT: FAIL" in bad.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ── Source-routing surface: offline URL building is deterministic ────
    url = fetch.build_query_url("arxiv", "raft consensus", 3)
    check("fetch_sources builds the documented arxiv query URL offline",
          url == "https://export.arxiv.org/api/query?search_query=all%3Araft+consensus&start=0&max_results=3",
          url[:80])
    check("fetch_sources URL building is deterministic across calls",
          url == fetch.build_query_url("arxiv", "raft consensus", 3))
    try:
        fetch.build_query_url("jstor-direct", "x")
        check("fetch_sources rejects a source outside its routing table", False)
    except ValueError as e:
        check("fetch_sources rejects a source outside its routing table",
              "unknown source" in str(e))

    # ── Report skeleton carries the sections stage 6 promises ────────────
    with open(os.path.join(ASSETS, "report-skeleton.md"), encoding="utf-8") as f:
        skeleton = f.read()
    wanted = ["## Executive summary", "## Domain map", "## Findings",
              "## Sources appendix", "## Dropped-claims log"]
    missing = [s for s in wanted if s not in skeleton]
    check("report skeleton contains every stage-6 section",
          not missing, "missing: " + ", ".join(missing) if missing else "")

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print(f"EVAL_RESULT: {'PASS' if ok else 'FAIL'} ({k}/{n} checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
