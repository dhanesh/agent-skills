"""conductor_testkit.py — shared fixtures for the conductor test suites (stdlib only).

Builds what a real run needs, through the vendored skill-contract checker:
- repo(): a git repo on factory/p (branched from main) holding a.txt and docs/spec.md;
- write_plan_envelope(): a task-plan/v1 envelope pinning docs/spec.md;
- write_grant(): a human-accepted autonomy grant pinning docs/spec.md and the plan
  envelope, so check-grant --subject <plan envelope> covers exactly that plan;
- new_run(): a State over a real plan envelope and grant, for suites that drive
  start/verify/review/merge directly without going through init;
- tmpdir(): a temporary directory that is removed when the process exits. repo() uses
  it too, so a suite run leaves nothing behind in tempfile.gettempdir().

Grants are written with generatedAtTime = now (never earlier than the newest grant
already under the root, so the newest head always wins) and a lifetime of at most
7 days, as the checker requires (A7).
"""
import atexit
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C  # noqa: E402
import contract_check as CC  # noqa: E402

GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
TASK_PLAN_KIND = C.TASK_PLAN_KIND
SPEC = "docs/spec.md"
DEFAULT_POLICY = {"read_only": "auto", "local_reversible": "grant",
                  "push_branch": "grant", "open_pr": "grant"}


_TMPDIRS = []


def tmpdir(**kw):
    """tempfile.mkdtemp(**kw), removed (with everything under it) at process exit."""
    d = tempfile.mkdtemp(**kw)
    _TMPDIRS.append(d)
    return d


@atexit.register
def _remove_tmpdirs():
    while _TMPDIRS:
        shutil.rmtree(_TMPDIRS.pop(), ignore_errors=True)


def read_text(path, mode="r"):
    """The whole of path, the file closed at once (no ResourceWarning)."""
    with open(path, mode) as f:
        return f.read()


def write_text(path, text):
    with open(path, "w") as f:
        f.write(text)


def z(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def repo():
    """A git repo on factory/p (cut from main) holding a.txt and docs/spec.md.

    .skill-contract/envelopes/ is excluded (.git/info/exclude), so the plan and the
    grant written there never show in `git status` and the grant stays untracked,
    as check-grant requires."""
    d = tmpdir()
    subprocess.run(["git", "init", "-q", "-b", "main", d], check=True)
    with open(os.path.join(d, "a.txt"), "w") as f:
        f.write("a\n")
    os.makedirs(os.path.join(d, "docs"))
    with open(os.path.join(d, "docs", "spec.md"), "w") as f:
        f.write("# Spec\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-q", "-m", "x"], check=True,
                   env=dict(os.environ, **GIT))
    subprocess.run(["git", "-C", d, "checkout", "-q", "-b", "factory/p"], check=True)
    with open(os.path.join(d, ".git", "info", "exclude"), "a") as f:
        f.write("/.skill-contract/envelopes/\n")
    return d


repo_with_plan = repo  # the name the Task 3 brief uses


def payload(tasks, title="T"):
    """A task-plan/v1 payload from {id: (depends_on, verify)}."""
    return {"title": title, "spec": SPEC, "coverage": {}, "uncovered": [],
            "tasks": [{"id": i, "requirement_ids": ["R1"], "title": i,
                       "verify": v, "depends_on": dep} for i, (dep, v) in tasks.items()]}


def write_plan_envelope(root, tasks=None, plan=None, kind=TASK_PLAN_KIND):
    """Write a task-plan envelope under root; its absolute path. Pass `tasks` in the
    payload() shape, or a ready `plan` payload."""
    body = plan if plan is not None else payload(tasks)
    st = CC.build_statement(kind, "spec-first-planning", "2.1.0", root, [SPEC], body)
    return CC.write_envelope(root, st)


def _newest_grant_time(root):
    d = CC.envelope_dir(root)
    newest = None
    if os.path.isdir(d):
        for name in os.listdir(d):
            try:
                with open(os.path.join(d, name), encoding="utf-8") as f:
                    doc = json.load(f)
                if doc.get("predicateType") != CC.GRANT_KIND:
                    continue
                t = datetime.strptime(doc["predicate"]["generatedAtTime"],
                                      "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
            newest = t if newest is None or t > newest else newest
    return newest


def write_grant(root, plan, policy=None, minutes=60, now=None, budget=None,
                branch_pattern="factory/*"):
    """A human-accepted grant pinning docs/spec.md and the plan envelope at `plan`.

    `minutes` sets expires_at relative to generatedAtTime; a negative value writes an
    already-expired grant. generatedAtTime is never earlier than (and, on a tie, one
    second after) the newest grant under root, so this grant is the newest head."""
    if now is None:
        now = CC.utc_now()
        newest = _newest_grant_time(root)
        if newest is not None and newest >= now:
            now = newest + timedelta(seconds=1)
    plan_rel = os.path.relpath(os.path.realpath(plan), os.path.realpath(root)).replace(os.sep, "/")
    pay = {"scope": {"repo": ".", "branch_pattern": branch_pattern},
           "decisions": [{"id": "D1", "question": "deps?", "answer": "no", "source": "sweep"}],
           "defaults": [], "gate_policy": dict(DEFAULT_POLICY) if policy is None else policy,
           "budget": dict(budget or {}), "stop_on": [],
           "expires_at": z(now + timedelta(minutes=minutes)),
           "system_one": {"allowed": False}, "revoked": False}
    a = {"test": "grant-accepted", "assertedBy": {"human": "Dana"},
         "result": {"outcome": "passed"},
         "command": ["{python}", "{skill_dir:spec-first-planning}/assets/spec_lint.py",
                     "--unattended", SPEC],
         "subject": [CC.pin(root, SPEC)]}
    st = CC.build_statement(CC.GRANT_KIND, "spec-first-planning", "2.1.0", root,
                            [SPEC, plan_rel], pay, [a], now=now)
    return CC.write_envelope(root, st)


def revoke(root):
    """Revoke the newest grant (the checker's revoke_grant, as `revoke-grant` runs it).

    The revision is stamped at least one second after the newest grant under root, so
    a same-second tie with a grant this kit bumped forward can never make ordering
    depend on the random id."""
    now = CC.utc_now()
    newest = _newest_grant_time(root)
    if newest is not None and newest >= now:
        now = newest + timedelta(seconds=1)
    return CC.revoke_grant(root, now=now)


def new_run(root, plan, run_branch="factory/p", base_branch="main", budget=None, grant=True,
            policy=None):
    """A State over a real task-plan envelope (holding the payload `plan`) and, unless
    grant=False, a covering grant (its gate_policy is `policy`, DEFAULT_POLICY when None).
    Returns (state, plan envelope path)."""
    path = write_plan_envelope(root, plan=plan)
    with open(path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    gid = None
    if grant:
        g = write_grant(root, path, policy=policy)
        gid = os.path.basename(g)[:-len(".json")]
    st = C.State.new(root=root, run_id=C.new_run_id(), plan=plan, plan_envelope=path,
                     plan_sha256=sha, grant_id=gid, run_branch=run_branch,
                     base_branch=base_branch, budget=budget or {})
    return st, path
