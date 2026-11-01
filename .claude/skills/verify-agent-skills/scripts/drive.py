#!/usr/bin/env python3
"""drive.py — the verify-agent-skills harness (stdlib only, JSON out).

The "app" is this repository's skill CLIs. They are short-lived: nothing listens on a
port. An instance is a directory, <worktree>/.verify-run/<instance>/, that holds the
fixtures one verifier builds and the captures it makes. Two instances never share one.

  doctor   --instance I                 read-only: can this checkout's CLIs run here?
  capture  --instance I --name N -- ARGV  run ARGV from the worktree root, write
                                          captures/N.json, exit with ARGV's own status
  fixture  verify-skill --instance I --name N [--from DIR] [--put REL=SRC ...]
           copy a verify-skill fixture (default: the notes-app eval fixture) into the
           instance, git-init it and commit; --put overwrites files before the commit
  fixture  commit --instance I --name N --put REL=SRC [...]   write files, commit them
  fixture  stack --instance I --name N --lang rust|python|typescript|go
  fixture  gated-run --instance I --name N --feature ID=ANCHOR [...]
           [--feature-file ID=SRC ...] [--task-json JSON] [--plan-json JSON]
           [--edit REL=SRC ...]
           build a repo with a verify-app skill, a plan under the evidence gate and a
           covering grant (each --feature file holds a `## Contract` that defines C1 and
           C2 only, so a clause such as C9 is undefined; --feature-file replaces it); run conductor init, start (owner "writer"), commit the edits,
           verify and review (reviewer "reviewer"). The task then waits for evidence.
           --plan-json merges keys into the plan payload; a null value removes the key
           (`{"verification": null}` builds a plan with no evidence gate).
  fixture  proof-tool --instance I --name N
           write a fake hypothesis run (tool.py), its target (target.txt: good), a
           mutant diff it catches (mutant.diff) and bin/pytest, a PATH shim that runs
           tool.py from its cwd, into the instance; commit the first two with
           gated-run --edit, then record a property proof with the diff and
           --cmd pytest, with bin_dir first on PATH
  cleanup  --instance I                 delete .verify-run/<instance>/ (never .verify/)

Every command prints one JSON object. Paths in it are absolute.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import timedelta

GIT_ENV = {"GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
           "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
NOTES_FIXTURE = "verification-skill-forge/eval/fixtures/notes-app"
CLIS = ["verification-skill-forge/assets/forge.py",
        "verification-skill-forge/assets/verify_evidence.py",
        "verifier-installer/assets/detect_stack.py",
        "spec-first-planning/assets/spec_lint.py",
        "spec-first-planning/assets/spec_to_tasks.py",
        "factory-conductor/assets/conductor.py"]
STACKS = {
    "rust": {"Cargo.toml": '[package]\nname = "fx"\nversion = "0.1.0"\nedition = "2021"\n',
             "src/lib.rs": "pub fn add(a: u32, b: u32) -> u32 { a + b }\n"},
    "python": {"pyproject.toml": '[project]\nname = "fx"\nversion = "0.1.0"\n',
               "fx/__init__.py": "def add(a, b):\n    return a + b\n"},
    "typescript": {"package.json": '{"name": "fx", "version": "0.1.0"}\n',
                   "tsconfig.json": '{"compilerOptions": {"strict": true}}\n',
                   "src/index.ts": "export const add = (a: number, b: number) => a + b;\n"},
    "go": {"go.mod": "module example.invalid/fx\n\ngo 1.22\n",
           "fx.go": "package fx\n\nfunc Add(a, b int) int { return a + b }\n"},
}
FEATURE = """# {fid}: {fid}

- id: {fid}
- proven: no
- anchors: {anchors}

## What it is

Fixture feature {fid}.

## How to reach it

Fixture only.

## Drive it

```sh
true
```

Expect exit 0.

## Contract

- C1 [pre]: fixture clause C1
- C2 [post]: fixture clause C2

## Proof

Fixture only. C1 and C2.

## Gotchas

None.
"""
GATE_SKILL = ".claude/skills/verify-app"
TASK_PLAN_KIND = "https://github.com/dhanesh/agent-skills/skill-contract/task-plan/v1"


def out(obj, rc=0):
    print(json.dumps(obj, indent=2, sort_keys=True))
    return rc


def worktree():
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("drive.py: run me inside the agent-skills checkout under test")
    return r.stdout.strip()


def inst_dir(wt, instance, *parts):
    if not instance or "/" in instance or instance.startswith("."):
        sys.exit("drive.py: bad instance name %r" % instance)
    return os.path.join(wt, ".verify-run", instance, *parts)


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                          env=dict(os.environ, **GIT_ENV), check=True).stdout.strip()


def git_init(d, branch="main"):
    git(d, "init", "-q", "-b", branch)
    for key, value in (("gc.auto", "0"), ("maintenance.auto", "false"),
                       ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
        git(d, "config", key, value)


def commit_all(d, msg):
    git(d, "add", "-A")
    git(d, "commit", "-q", "--allow-empty", "-m", msg)
    return git(d, "rev-parse", "HEAD")


def put(d, specs):
    for spec in specs or []:
        rel, _, src = spec.partition("=")
        if not rel or not src:
            sys.exit("drive.py: --put/--edit takes REL=SRC, got %r" % spec)
        dst = os.path.join(d, rel)
        os.makedirs(os.path.dirname(dst) or d, exist_ok=True)
        shutil.copyfile(os.path.abspath(src), dst)


def fresh(d):
    if os.path.exists(d):
        sys.exit("drive.py: %s exists; pick another --name or run cleanup" % d)
    os.makedirs(d)


def cmd_doctor(a):
    wt = worktree()
    checks = {"python>=3.10": sys.version_info >= (3, 10),
              "git": shutil.which("git") is not None}
    for cli in CLIS:
        r = subprocess.run([sys.executable, os.path.join(wt, cli), "--help"],
                           capture_output=True, text=True, cwd=wt)
        # detect_stack.py and spec_to_tasks.py have no --help; they must still start.
        checks[cli] = os.path.isfile(os.path.join(wt, cli)) and "Traceback" not in r.stderr
    ok = all(checks.values())
    return out({"instance": a.instance, "worktree": wt, "head": git(wt, "rev-parse", "HEAD"),
                "ok": ok, "checks": checks}, 0 if ok else 1)


def cmd_capture(a):
    wt = worktree()
    argv = a.argv[1:] if a.argv and a.argv[0] == "--" else a.argv
    if not argv:
        sys.exit("drive.py: capture needs -- ARGV")
    r = subprocess.run(argv, capture_output=True, text=True, cwd=wt)
    path = inst_dir(wt, a.instance, "captures", a.name + ".json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rec = {"argv": argv, "cwd": wt, "exit": r.returncode, "stdout": r.stdout,
           "stderr": r.stderr, "capture": path}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2, sort_keys=True)
    out(rec)
    return r.returncode


def cmd_verify_skill(a):
    wt = worktree()
    d = inst_dir(wt, a.instance, a.name)
    if os.path.exists(d):
        sys.exit("drive.py: %s exists; pick another --name or run cleanup" % d)
    shutil.copytree(os.path.join(wt, a.src or NOTES_FIXTURE), d)
    put(d, a.put)
    git_init(d)
    sha = commit_all(d, "fixture")
    skills = os.path.join(d, ".claude", "skills")
    names = sorted(os.listdir(skills)) if os.path.isdir(skills) else []
    return out({"root": d, "head": sha,
                "verify_dir": os.path.join(skills, names[0]) if names else None})


def cmd_commit(a):
    wt = worktree()
    d = inst_dir(wt, a.instance, a.name)
    if not os.path.isdir(os.path.join(d, ".git")):
        sys.exit("drive.py: no fixture repo at %s" % d)
    put(d, a.put)
    return out({"root": d, "head": commit_all(d, "fixture edit")})


def cmd_stack(a):
    wt = worktree()
    d = inst_dir(wt, a.instance, a.name)
    fresh(d)
    for rel, text in STACKS[a.lang].items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    git_init(d)
    return out({"root": d, "lang": a.lang, "head": commit_all(d, "fixture")})


def cmd_gated_run(a):
    wt = worktree()
    d = inst_dir(wt, a.instance, a.name)
    fresh(d)
    assets = os.path.join(wt, "factory-conductor", "assets")
    sys.path.insert(0, assets)
    import contract_check as CC  # the checker this checkout ships

    git_init(d)
    with open(os.path.join(d, "a.txt"), "w") as f:
        f.write("a\n")
    os.makedirs(os.path.join(d, "docs"))
    with open(os.path.join(d, "docs", "spec.md"), "w") as f:
        f.write("# Spec\n")
    features = dict(spec.partition("=")[::2] for spec in a.feature)
    skill = os.path.join(d, *GATE_SKILL.split("/"))
    os.makedirs(os.path.join(skill, "features"))
    with open(os.path.join(skill, "SKILL.md"), "w") as f:
        f.write("---\nname: verify-app\ndescription: fixture\n---\n")
    with open(os.path.join(skill, "features", "README.md"), "w") as f:
        f.write("".join("- [%s](%s.md)\n" % (k, k) for k in features))
    for fid, anchors in features.items():
        with open(os.path.join(skill, "features", fid + ".md"), "w") as f:
            f.write(FEATURE.format(fid=fid, anchors=anchors))
    put(skill, ["features/%s.md=%s" % tuple(s.split("=", 1)) for s in a.feature_file])
    base = commit_all(d, "base")
    git(d, "checkout", "-q", "-b", "factory/base")
    with open(os.path.join(d, ".git", "info", "exclude"), "a") as f:
        f.write("/.skill-contract/envelopes/\n/.verify/\n/.verify-run/\n")

    task = {"id": "T1", "requirement_ids": ["R1"], "title": "T1", "depends_on": [],
            "verify": [{"text": "ok", "command": ["true"]}], "features": sorted(features)}
    task.update(json.loads(a.task_json or "{}"))
    plan = {"title": "Gate", "spec": "docs/spec.md", "coverage": {}, "uncovered": [],
            "tasks": [task], "verification": {"skill": GATE_SKILL}}
    for key, value in json.loads(a.plan_json or "{}").items():
        if value is None:
            plan.pop(key, None)
        else:
            plan[key] = value
    st = CC.build_statement(TASK_PLAN_KIND, "spec-first-planning", "2.1.0", d,
                            ["docs/spec.md"], plan)
    plan_path = CC.write_envelope(d, st)
    plan_rel = os.path.relpath(plan_path, d).replace(os.sep, "/")
    now = CC.utc_now()
    pay = {"scope": {"repo": ".", "branch_pattern": "factory/*"},
           "decisions": [{"id": "D1", "question": "fixture?", "answer": "yes",
                          "source": "sweep"}],
           "defaults": [], "gate_policy": {"read_only": "auto", "local_reversible": "grant",
                                           "push_branch": "ask", "open_pr": "ask"},
           "budget": {}, "stop_on": [], "expires_at": (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "system_one": {"allowed": False}, "revoked": False}
    assertion = {"test": "grant-accepted", "assertedBy": {"human": "fixture"},
                 "result": {"outcome": "passed"},
                 "command": ["{python}", "{skill_dir:spec-first-planning}/assets/spec_lint.py",
                             "--unattended", "docs/spec.md"],
                 "subject": [CC.pin(d, "docs/spec.md")]}
    CC.write_envelope(d, CC.build_statement(CC.GRANT_KIND, "spec-first-planning", "2.1.0", d,
                                            ["docs/spec.md", plan_rel], pay, [assertion],
                                            now=now))

    conductor = [sys.executable, os.path.join(assets, "conductor.py")]
    steps = []

    def run(*argv):
        r = subprocess.run([*conductor, *argv, "--root", d], capture_output=True, text=True)
        steps.append({"argv": list(argv), "exit": r.returncode, "stdout": r.stdout,
                      "stderr": r.stderr})
        if r.returncode != 0:
            out({"root": d, "error": "conductor %s exited %d" % (argv[0], r.returncode),
                 "steps": steps})
            sys.exit(1)
        return r.stdout

    run("init", "--plan", plan_path)
    task_wt = run("start", "T1", "--owner", "writer").split(" ", 2)[2].strip()
    put(task_wt, a.edit)
    if not a.edit:
        with open(os.path.join(task_wt, "a.txt"), "a") as f:
            f.write("work\n")
    commit_all(task_wt, "work")
    sha = run("verify", "T1").split()[-1]
    run("review", "T1", "--verdict", "pass", "--reviewer", "reviewer")
    return out({"root": d, "worktree": task_wt, "task": "T1", "sha": sha, "base": base,
                "owner": "writer", "steps": steps})


# A fake allowlisted proof tool: it passes on "good" and prints Hypothesis's failure
# report when the mutant sets target.txt to "caught". No dependency.
PROOF_TOOL = '''import sys
mode = open("target.txt").read().strip()
if mode == "caught":
    print("Falsifying example: test_create(title='')\\n1 failed in 0.01s")
    sys.exit(1)
print("1 passed in 0.01s")
'''
PROOF_SHIM = "#!%s\nimport runpy\nrunpy.run_path('tool.py', run_name='__main__')\n"
PROOF_MUTANT = """diff --git a/target.txt b/target.txt
--- a/target.txt
+++ b/target.txt
@@ -1 +1 @@
-good
+caught
"""


def cmd_proof_tool(a):
    wt = worktree()
    d = inst_dir(wt, a.instance, a.name)
    fresh(d)
    files = {"tool.py": PROOF_TOOL, "target.txt": "good\n", "mutant.diff": PROOF_MUTANT}
    for name, text in files.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(text)
    # The recorder runs only a runner of --tool, so the fake tool is reached through a
    # PATH shim with the runner's name.
    bin_dir = os.path.join(d, "bin")
    os.makedirs(bin_dir)
    shim = os.path.join(bin_dir, "pytest")
    with open(shim, "w", encoding="utf-8") as f:
        f.write(PROOF_SHIM % sys.executable)
    os.chmod(shim, 0o755)
    paths = {name.replace(".", "_"): os.path.join(d, name) for name in files}
    return out(dict(paths, bin_dir=bin_dir))


def cmd_cleanup(a):
    wt = worktree()
    d = inst_dir(wt, a.instance)
    existed = os.path.isdir(d)
    shutil.rmtree(d, ignore_errors=True)
    return out({"instance": a.instance, "removed": d if existed else None})


def main(argv=None):
    p = argparse.ArgumentParser(prog="drive.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("doctor"); s.add_argument("--instance", required=True)
    s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("capture"); s.add_argument("--instance", required=True)
    s.add_argument("--name", required=True); s.add_argument("argv", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_capture)
    s = sub.add_parser("cleanup"); s.add_argument("--instance", required=True)
    s.set_defaults(fn=cmd_cleanup)
    fx = sub.add_parser("fixture").add_subparsers(dest="kind", required=True)
    for kind, fn in (("verify-skill", cmd_verify_skill), ("commit", cmd_commit),
                     ("stack", cmd_stack), ("gated-run", cmd_gated_run),
                     ("proof-tool", cmd_proof_tool)):
        s = fx.add_parser(kind)
        s.add_argument("--instance", required=True)
        s.add_argument("--name", required=True)
        s.set_defaults(fn=fn)
        if kind == "verify-skill":
            s.add_argument("--from", dest="src")
        if kind in ("verify-skill", "commit"):
            s.add_argument("--put", action="append", default=[])
        if kind == "stack":
            s.add_argument("--lang", required=True, choices=sorted(STACKS))
        if kind == "gated-run":
            s.add_argument("--feature", action="append", required=True)
            s.add_argument("--feature-file", action="append", default=[])
            s.add_argument("--task-json")
            s.add_argument("--plan-json")
            s.add_argument("--edit", action="append", default=[])
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
