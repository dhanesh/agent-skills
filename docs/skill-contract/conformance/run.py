#!/usr/bin/env python3
"""Conformance runner for `contract_check.py check-pipeline-grant` (pipeline-grant/v1).

Each vector under conformance/vectors/pipeline-grant/ is one JSON file:

    {"description": ..., "input": {"files": {path: text}, "grant": <envelope>,
                                   "now": "<RFC 3339 UTC>", "git": "tracked" (optional)},
     "expect": {"status": ..., "reason": ..., "exit": ..., "violation_contains": ...}}

The runner writes the files and the grant into a temporary root, and commits them to a new
git repository when "git" is "tracked". Then it calls the reference checker with the
vector's "now" and compares the result with "expect". "exit" is the exit code that the
check-pipeline-grant command gives for that result.

Stdlib only, offline. Needs git for the tracked vector.

    python3 run.py           run every vector; exit 0 when all pass, 1 otherwise
    python3 run.py --build   rewrite the vectors from the generator below
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REFERENCE = os.path.join(os.path.dirname(HERE), "reference")
VECTORS = os.path.join(HERE, "vectors", "pipeline-grant")
sys.path.insert(0, REFERENCE)  # `python -I` drops the script dir from sys.path
import contract_check as cc  # noqa: E402

# ── The generator: the vectors are data, written by --build ─────────────────
PICK = ".factory/pick-policy.json"
ALLOW = ".factory/check-commands.txt"
FILES = {
    PICK: '{"formats": ["intake-item/v1"], "min_trust": "high"}\n',
    ALLOW: "make gate\nmake gate-skill SKILL=*\n",
}
GRANT_ID = "pipeline-grant-v1-20261104T080000Z-a1b2c3"
WRITTEN = "2026-11-04T08:00:00Z"
NOW = "2026-11-04T09:00:00Z"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _grant(expires_at="2026-11-07T08:00:00Z", gate_policy=None, caps=None, planner=None):
    pins = [{"name": n, "digest": {"sha256": _sha(FILES[n])}} for n in (PICK, ALLOW)]
    payload = {
        "scope": {"repo": ".", "branch_pattern": "factory/*"},
        "gate_policy": gate_policy or {"read_only": "auto", "local_reversible": "auto",
                                       "push_branch": "grant", "open_pr": "grant"},
        "caps": caps or {"max_plans_per_day": 3, "max_open_runs": 2,
                         "max_dispatches_per_day": 40},
        "pick_policy": {"path": PICK, "sha256": _sha(FILES[PICK])},
        "allowlist": {"path": ALLOW, "sha256": _sha(FILES[ALLOW])},
        "budget": {"wall_clock_min": 1440, "max_parallel": 2, "max_repairs_per_task": 2},
        "stop_on": ["new_human_decision"],
        "expires_at": expires_at,
        "revoked": False,
    }
    if planner is not None:
        payload["planner"] = planner
    return {
        "_type": cc.STATEMENT_TYPE,
        "subject": pins,
        "predicateType": cc.PIPELINE_GRANT_KIND,
        "predicate": {
            "skillContract": cc.CONTRACT_VERSION,
            "id": GRANT_ID,
            "wasAttributedTo": {"skill": "spec-first-planning", "version": "2.0.0"},
            "generatedAtTime": WRITTEN,
            "wasRevisionOf": None,
            "payload": payload,
            "assertions": [{
                "test": "grant-accepted",
                "assertedBy": {"human": "Dana"},
                "result": {"outcome": "passed"},
                "command": ["{python}", "{skill_dir:spec-first-planning}/assets/spec_lint.py",
                            "--unattended", "docs/spec.md"],
                "subject": [pins[0]],
            }],
        },
    }


def _vector(description, grant, expect, git=None):
    inp = {"files": dict(FILES), "grant": grant, "now": NOW}
    if git:
        inp["git"] = git
    return {"description": description, "input": inp, "expect": expect}


def build():
    """{file name: vector}."""
    invalid = {"status": "INVALID", "reason": "invalid", "exit": 2}
    return {
        "valid.json": _vector(
            "A pipeline grant inside every floor, outside git, is VALID.",
            _grant(), {"status": "VALID", "exit": 0}),
        "merge-grant-invalid.json": _vector(
            "merge is never grantable: a `grant` gate on it makes the grant INVALID.",
            _grant(gate_policy={"read_only": "auto", "merge": "grant"}),
            dict(invalid, violation_contains="gate_policy['merge'] must be ask")),
        "eight-day-expiry-invalid.json": _vector(
            "expires_at 8 days after generatedAtTime breaks the 7-day floor: INVALID.",
            _grant(expires_at="2026-11-12T08:00:00Z"),
            dict(invalid, violation_contains="at most 7 days")),
        "missing-cap-invalid.json": _vector(
            "caps without max_open_runs is not exactly the three caps: INVALID.",
            _grant(caps={"max_plans_per_day": 3, "max_dispatches_per_day": 40}),
            dict(invalid, violation_contains="payload.caps must be exactly")),
        "planner-valid.json": _vector(
            "An optional planner argv with {prompt} once is VALID.",
            _grant(planner=["/usr/local/bin/claude", "-p", "{prompt}"]),
            {"status": "VALID", "exit": 0}),
        "planner-bypass-invalid.json": _vector(
            "A planner reads untrusted text, so a permission-bypass flag on it is INVALID.",
            _grant(planner=["claude", "--dangerously-skip-permissions", "-p", "{prompt}"]),
            dict(invalid, violation_contains="permission-bypass flag")),
        "tracked-asks.json": _vector(
            "A valid pipeline grant committed to git covers nothing: ASK tracked.",
            _grant(), {"status": "ASK", "reason": "tracked", "exit": 3}, git="tracked"),
    }


def write_vectors():
    os.makedirs(VECTORS, exist_ok=True)
    for name, vec in build().items():
        with open(os.path.join(VECTORS, name), "w", encoding="utf-8", newline="\n") as f:
            json.dump(vec, f, indent=2, sort_keys=True)
            f.write("\n")
        print("wrote: %s" % name)


# ── The runner ─────────────────────────────────────────────────────────────
def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _git_commit_all(root):
    """Make root a git repository and commit every file in it, the grant included."""
    env = cc.git_env()
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=conformance", "-c", "user.email=conformance@example.invalid",
                  "-c", "commit.gpgsign=false", "commit", "-q", "-m", "vector"]):
        subprocess.run(["git", "-C", root] + args, check=True, capture_output=True,
                       timeout=30, env=env)


def run_vector(vec, tmp):
    inp = vec["input"]
    root = os.path.join(tmp, "root")
    for rel, text in inp["files"].items():
        _write(os.path.join(root, *rel.split("/")), text)
    gid = inp["grant"]["predicate"]["id"]
    _write(os.path.join(cc.envelope_dir(root), gid + ".json"),
           json.dumps(inp["grant"], indent=2, sort_keys=True) + "\n")
    if inp.get("git") == "tracked":
        _git_commit_all(root)
    now = datetime.strptime(inp["now"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    rep = cc.check_pipeline_grant(root, now=now)
    return dict(rep, exit=cc.PIPELINE_GRANT_EXIT[rep["status"]])


def problems(vec, got):
    out = []
    for key, want in vec["expect"].items():
        if key == "violation_contains":
            if not any(want in v for v in got["violations"]):
                out.append("no violation contains %r: %r" % (want, got["violations"]))
        elif got.get(key) != want:
            out.append("%s: want %r, got %r" % (key, want, got.get(key)))
    return out


def main(argv):
    if argv[1:] == ["--build"]:
        write_vectors()
        return 0
    if argv[1:]:
        print("usage: run.py [--build]", file=sys.stderr)
        return 1
    names = sorted(f for f in os.listdir(VECTORS) if f.endswith(".json")) \
        if os.path.isdir(VECTORS) else []
    if not names:
        print("CONFORMANCE: FAIL (no vectors under %s)" % VECTORS)
        return 1
    failed = 0
    for name in names:
        with open(os.path.join(VECTORS, name), encoding="utf-8") as f:
            vec = json.load(f)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                bad = problems(vec, run_vector(vec, tmp))
            except (OSError, subprocess.SubprocessError, KeyError, ValueError) as exc:
                bad = ["the runner failed: %s" % exc]
        if bad:
            failed += 1
            print("FAIL: %s: %s" % (name, "; ".join(bad)))
        else:
            print("PASS: %s" % name)
    if failed:
        print("CONFORMANCE: FAIL (%d of %d vectors)" % (failed, len(names)))
        return 1
    print("CONFORMANCE: PASS (%d vectors)" % len(names))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
