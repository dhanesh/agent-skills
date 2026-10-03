#!/usr/bin/env python3
"""Outcome eval for release-conductor (see the repo's docs/eval-standard.md).

Stdlib-only, offline, deterministic. Every scenario is a temp project built by the shipped
end-to-end fixture (assets/test_release_e2e.py's Project): a git repo at 1.1.0 with a
committed verify skill, a local bare origin, and a local http.server standing in for staging
and production, which the recipe's stub deploy commands rewrite. The skill's own tool,
assets/release.py, runs each step in-process; the verifier's evidence is written in
verification-skill-forge's recorder format. Nothing touches a real target, no timer or
crontab is touched, scratch lives in temp directories removed at exit, and bytecode is not
written, so the eval writes nothing in the repository. Probe polls and command timeouts are
shortened through release.py's module constants.

The promise graded (spec AC3, with the D11 amendments): a merged change reaches verified
production with only the human's production yes, and production is never reached any other
way. One positive arm drives a full attended release to `verified` (and, on the way, shows
that an unattended deploy waits and that verify-prod runs only health and prod_smoke). Each
NEGATIVE arm is its own fixture and asserts two things: the refusal's distinctive line or
state, and that the side effect it guards (a deploy marker, the served version, the origin's
tag) did not happen.

The spec's `write_grant` refusal of a production command in a headless allowlist is
spec-first-planning's (its own suite and eval cover it); here the release-time refusal is
graded.

Prints one `CHECK: <name> — PASS|FAIL (<detail>)` line per check and a final
`EVAL_RESULT: PASS (n/n checks)` line; exits 0 iff every check passed.
"""
import json
import os
import shutil
import sys
import traceback
from unittest import mock

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
sys.path.insert(0, ASSETS)
import release as RL  # noqa: E402
import contract_check as CC  # noqa: E402
import test_release_e2e as E  # noqa: E402  (the shipped end-to-end fixture)
import test_release_deploy as TD  # noqa: E402  (plant_grant: a factory-style grant)

_checks = []

TAG_CI = "on:\n  push:\n    tags: ['v*']\n"


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = " (%s)" % detail if detail else ""
    print("CHECK: %s — %s%s" % (name, "PASS" if ok else "FAIL", suffix))


def arm(name):
    """Run one arm; an exception in its fixture is that check's FAIL, never a lost
    EVAL_RESULT line."""
    def wrap(fn):
        def go():
            try:
                fn()
            except Exception as e:  # noqa: BLE001 -- any fixture failure is a FAIL
                tb = traceback.format_exception_only(type(e), e)[-1].strip()
                check(name, False, "fixture failed: %s" % tb[:300])
        go.__name__ = fn.__name__
        return go
    return wrap


def first(out, prefix):
    return next((ln for ln in out.splitlines() if ln.startswith(prefix)), "")


# ── positive ─────────────────────────────────────────────────────────────────────
@arm("a full attended release reaches verified production")
def full_release_arm():
    p = E.Project()
    commit = p.staged()
    rc, out = p.cmd("deploy", "--unattended", "--approved-by", "Dana")
    check("NEGATIVE: an unattended deploy runs nothing and waits at awaiting_deploy, even "
          "with a name",
          rc == 3 and "STOP: waiting-human" in out.splitlines() and p.lines("production") == []
          and p.rel().status == "awaiting_deploy" and p.rel().approved_by is None,
          "rc=%s status=%s prod=%r" % (rc, p.rel().status, p.lines("production")))
    rc, out = p.cmd("deploy", "--approved-by", "Dana")
    deployed = rc == 0 and p.lines("production") == ["1.2.0 %s" % commit]
    checks_before = p.lines("check")
    rc2, out2 = p.cmd("verify-prod")
    check("NEGATIVE: verify-prod runs health and prod_smoke only, never a staging check",
          rc2 == 0 and p.lines("smoke") == ["production"] and p.lines("check") == checks_before
          == ["staging"],
          "smoke=%r staging_checks=%r" % (p.lines("smoke"), p.lines("check")))
    payload = E.result_payload(p, out2, "verified")
    check("a full attended release reaches verified production, with a release-result/v1 "
          "the checker and the schema accept",
          deployed and p.rel().status == "verified" and payload["commit"] == commit
          and payload["approved_by"] == {"name": "Dana", "status": "CLAIMED"}
          and payload["staging"]["verifier"] == E.VERIFIER,
          "deploy rc=%s status=%s" % (rc, p.rel().status))


# ── negatives ────────────────────────────────────────────────────────────────────
@arm("NEGATIVE: no production deploy without staging evidence at the release commit")
def evidence_arm():
    p = E.Project()
    commit = p.built()
    base = p.rel().base_commit
    p.record(base)  # a verifier's records, but for another commit
    rc1, out1 = p.cmd("stage", "--evidence", "--verifier", E.VERIFIER)
    p.record(commit, verifier=E.DRIVER)  # the right commit, recorded by the release driver
    rc2, out2 = p.cmd("stage", "--evidence", "--verifier", E.DRIVER)
    rc3, out3 = p.cmd("deploy", "--approved-by", "Dana")
    check("NEGATIVE: no production deploy without staging evidence at the release commit "
          "from a verifier other than the driver",
          rc1 == 3 and "STOP: evidence-reject evidence-stale-sha" in out1
          and rc2 == 3 and "STOP: evidence-reject driver-is-verifier" in out2
          and rc3 == 2 and "not-staged" in out3
          and p.lines("staging") == [] and p.lines("production") == [],
          "%s | %s | %s" % (first(out1, "STOP:"), first(out2, "STOP:"),
                            first(out3, "RELEASE:")))


@arm("NEGATIVE: a recipe changed in the release never reaches staging or production")
def recipe_changed_arm():
    p = E.Project()
    p.init()
    p.prep()
    # the release PR edits the recipe (the release commit itself carries the edit)
    wt = p.prep_wt()
    edited = dict(p.recipe, deploy_staging=p.recipe["deploy_prod"])
    with open(os.path.join(wt, ".release", "recipe.json"), "w") as f:
        json.dump(edited, f, indent=2, sort_keys=True)
        f.write("\n")
    r = E.git(wt, "commit", "-q", "-a", "--amend", "--no-edit")
    E.need(r.returncode == 0, r.stderr)
    p.merge()
    rc1, out1 = p.cmd("stage")
    rc2, out2 = p.cmd("deploy", "--approved-by", "Dana")
    check("NEGATIVE: a recipe changed in the release never reaches staging or production",
          rc1 == 3 and "STOP: recipe-changed" in out1 and rc2 == 2
          and p.lines("build") == [] and p.lines("staging") == []
          and p.lines("production") == [],
          "%s | %s" % (first(out1, "STOP:"), first(out2, "RELEASE:")))


@arm("NEGATIVE: a tag push asks when CI runs on tags")
def ci_tag_arm():
    p = E.Project(ci=TAG_CI)
    commit = p.staged()
    rel = p.rel()
    wt = os.path.join(rel.dir, RL.STAGE_WT)
    rep = CC.check_grant(p.root, "push_tag", path=RL._grant_path(p.root, rel.grant["id"]),
                         subject=RL.RECIPE_PATH, worktree=wt)
    held = rel.tag_deploys is True and rel.stage.get("tag") == "held" and p.tag() is None
    rc, out = p.cmd("deploy", "--unattended", "--approved-by", "Dana")
    waited = rc == 3 and "STOP: waiting-human" in out.splitlines() and p.tag() is None
    rc2, _ = p.cmd("deploy", "--approved-by", "Dana")
    check("NEGATIVE: a tag push asks when CI runs on tags: stage holds it, check-grant "
          "answers ASK ci-tag, and only the production yes pushes it",
          held and rep["status"] == "ASK" and rep["reason"] == "ci-tag" and waited
          and rc2 == 0 and p.tag() == commit and p.lines("production") == [],
          "held=%s gate=%s/%s waited=%s yes-rc=%s" % (held, rep["status"], rep["reason"],
                                                       waited, rc2))


@arm("NEGATIVE: verify-prod fails a version mismatch, and rollback asks")
def version_mismatch_and_rollback_arm():
    p = E.Project(deploy_timeout=1)
    p.deployed()
    p.serve_prod("1.3.0")  # production answers a third version
    rc, out = p.cmd("verify-prod")
    check("NEGATIVE: verify-prod fails on a version mismatch (prod_failed wrong-version, no "
          "result envelope)",
          rc == 3 and "PROD: 1.2.0 fail wrong-version" in out.splitlines()
          and p.rel().status == "prod_failed" and not p.rel().result
          and "RESULT:" not in out,
          first(out, "PROD:"))
    rc1, out1 = p.cmd("rollback")
    rc2, out2 = p.cmd("rollback", "--unattended", "--approved-by", "Dana")
    check("NEGATIVE: rollback asks: without the human's yes, or unattended, it shows the "
          "rollback and runs nothing",
          rc1 == 3 and rc2 == 3 and "STOP: waiting-human" in out1.splitlines()
          and "STOP: waiting-human" in out2.splitlines() and p.lines("rollback") == []
          and p.served("production") == "1.3.0" and p.rel().status == "prod_failed",
          "rollback=%r status=%s" % (p.lines("rollback"), p.rel().status))


@arm("NEGATIVE: a crash in deploying never re-runs the deploy")
def crash_arm():
    p = E.Project()
    p.deployed()
    rel = p.rel()
    rel.status = "deploying"  # the deploy's process died before recording its end
    rel.save()
    rc1, out1 = p.cmd("deploy", "--approved-by", "Dana")
    rc2, out2 = p.cmd("deploy", "--approved-by", "Dana")
    check("NEGATIVE: a crash in deploying never re-runs the deploy (outcome_unknown, the "
          "command ran once)",
          rc1 == 3 and rc2 == 3 and "STOP: outcome-unknown" in out1
          and "NEXT: verify-prod then ask the human" in out2.splitlines()
          and len(p.lines("production")) == 1 and p.rel().status == "outcome_unknown",
          "runs=%d status=%s" % (len(p.lines("production")), p.rel().status))


@arm("NEGATIVE: a deploy that times out is outcome_unknown and never re-run")
def timeout_arm():
    hang = ("import sys, time; open(sys.argv[1], 'a').write('started' + chr(10)); "
            "time.sleep(30)")
    p = E.Project()
    p.recipe["deploy_prod"] = [sys.executable, "-c", hang, p.markers["production"]]
    p.staged()
    with mock.patch.object(RL, "CMD_TIMEOUT", 0.5):
        rc1, out1 = p.cmd("deploy", "--approved-by", "Dana")
        rc2, _ = p.cmd("deploy", "--approved-by", "Dana")
    check("NEGATIVE: a production deploy that times out is outcome_unknown, never a known "
          "failure, and is never re-run",
          rc1 == 3 and "DEPLOY: 1.2.0 unknown" in out1 and "STOP: outcome-unknown" in out1
          and rc2 == 3 and p.lines("production") == ["started"]
          and p.rel().status == "outcome_unknown",
          "%s runs=%d" % (first(out1, "STOP:"), len(p.lines("production"))))


@arm("NEGATIVE: an allowlist exposing production is refused")
def allowlist_arm():
    p = E.Project()
    p.staged()
    gid = TD.plant_grant(p.root, "Read,Bash")
    rc1, out1 = p.cmd("deploy", "--approved-by", "Dana")
    CC.revoke_grant(p.root, gid)
    # an expired grant nobody revoked still counts: a scheduler may still launch its agent
    past = CC.utc_now() - RL._dt.timedelta(days=3)
    with mock.patch.object(CC, "utc_now", return_value=past):
        gid2 = TD.plant_grant(p.root, "Bash(%s *)" % sys.executable)
    rc2, out2 = p.cmd("deploy", "--approved-by", "Dana")
    refused = (rc1 == 2 and "allowlist-exposes-prod" in out1 and rc2 == 2
               and "allowlist-exposes-prod" in out2 and p.lines("production") == []
               and p.rel().status == "staged")
    CC.revoke_grant(p.root, gid2)
    rc3, _ = p.cmd("deploy", "--approved-by", "Dana")
    check("NEGATIVE: a live grant whose headless allowlist reaches production is refused, "
          "an expired-but-unrevoked one too; once revoked, the deploy runs",
          refused and rc3 == 0 and len(p.lines("production")) == 1,
          "%s | %s | after revoke rc=%s" % (first(out1, "RELEASE:")[:70],
                                             first(out2, "RELEASE:")[:70], rc3))


@arm("NEGATIVE: stage refuses a merged commit whose version is not the pinned one")
def stage_version_arm():
    p = E.Project()
    p.init()
    p.prep()
    # the release PR is edited to ship another version, and the human squash-merges it
    wt = p.prep_wt()
    with open(os.path.join(wt, "package.json"), "w") as f:
        f.write('{"version": "1.3.0"}\n')
    r = E.git(wt, "commit", "-q", "-a", "--amend", "--no-edit")
    E.need(r.returncode == 0, r.stderr)
    tip = p.merge()
    rc1, out1 = p.cmd("stage")
    rc2, out2 = p.cmd("stage", "--commit", tip)
    stage_wt = os.path.join(p.rel().dir, RL.STAGE_WT)
    check("NEGATIVE: stage refuses a merged commit whose version differs from the grant's "
          "pinned version, before anything runs",
          rc1 == 3 and "STOP: not-merged" in out1 and rc2 == 3
          and "STOP: version-mismatch" in out2 and p.lines("build") == []
          and not os.path.exists(stage_wt) and p.rel().status == "prepped",
          "%s | %s" % (first(out1, "STOP:")[:60], first(out2, "STOP:")[:60]))


def main():
    for fn in (full_release_arm, evidence_arm, recipe_changed_arm, ci_tag_arm,
               version_mismatch_and_rollback_arm, crash_arm, timeout_arm, allowlist_arm,
               stage_version_arm):
        fn()
    n, k = len(_checks), sum(_checks)
    ok = k == n
    print("EVAL_RESULT: %s (%d/%d checks)" % ("PASS" if ok else "FAIL", k, n))
    return 0 if ok else 1


if __name__ == "__main__":
    if not shutil.which("git"):
        print("CHECK: git is installed — FAIL (git is required to run this eval)")
        print("EVAL_RESULT: FAIL (0/1 checks)")
        sys.exit(1)
    sys.exit(main())
