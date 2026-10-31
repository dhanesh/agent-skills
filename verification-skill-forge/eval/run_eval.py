#!/usr/bin/env python3
"""Outcome eval for verification-skill-forge (docs/eval-standard.md).

Harness: copies the fixture notes app (eval/fixtures/notes-app, with a verify skill the
forge's contract accepts) into a temp git repo. Skill tooling: forge.py and the vendored
verify_evidence.py, plus the fixture skill's own harness, driven LIVE: two instances run
side by side. Grader: model-free checks on exit codes, output lines and files.

Negative fixtures: a scaffold with FILL: markers, a placeholder name, screen
coordinates, a cleanup that deletes evidence, a proven feature whose source is gone, a
port claimed by a second owner, a feature map rewritten to match a bug, a proof tool
outside its tier, a mutant the check does not notice and a mutant that only breaks the
build. Each MUST be rejected. stdlib only, offline (loopback HTTP only), no repo writes.
"""
import contextlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
sys.path.insert(0, ASSETS)
import forge as F  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "notes-app")
SKILL = os.path.join(".claude", "skills", "verify-notes")
GENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
GENV.pop("VERIFY_EVIDENCE_DIR", None)
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print("CHECK: %s — %s (%s)" % (name, "PASS" if ok else "FAIL", detail))


def forge(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = F.main(list(argv))
    return rc, out.getvalue()


def sh(root, *argv):
    return subprocess.run(list(argv), cwd=root, capture_output=True, text=True, env=GENV,
                          timeout=30)


def repo(tmp, name="app"):
    root = os.path.join(tmp, name)
    shutil.copytree(FIXTURE, root)
    sh(root, "git", "init", "-q", "-b", "main")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "fixture")
    return root


def mutate(root, rel, old, new):
    path = os.path.join(root, SKILL, rel)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.replace(old, new, 1))
    return text.count(old) > 0


def lint_after(tmp, name, rel, old, new):
    root = repo(tmp, name)
    found = mutate(root, rel, old, new)
    rc, out = forge("lint", os.path.join(root, SKILL))
    return found and rc == 1, out


# A fake property tool for the proof-tier fixtures: what it prints and how it exits
# depends only on target.txt, so a mutant patch picks the outcome.
FAKE_TOOL = '''import sys
mode = open("target.txt").read().strip()
if mode == "caught":
    print("Falsifying example: test_create(title='')\\n1 failed in 0.01s")
    sys.exit(1)
if mode == "broken":
    print("ERROR collecting tests/test_notes.py\\n1 error during collection")
    sys.exit(2)
print("1 passed in 0.01s")
'''


def tier_repo(tmp):
    root = os.path.join(tmp, "tiers")
    os.makedirs(root)
    with open(os.path.join(root, "tool.py"), "w") as f:
        f.write(FAKE_TOOL)
    with open(os.path.join(root, "target.txt"), "w") as f:
        f.write("good\n")
    sh(root, "git", "init", "-q", "-b", "main")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "fixture")
    # The recorder runs only a runner of --tool: a PATH shim named pytest runs tool.py.
    bin_dir = os.path.join(tmp, "tier-bin")
    os.makedirs(bin_dir)
    with open(os.path.join(bin_dir, "pytest"), "w") as f:
        f.write("#!%s\nimport runpy\nrunpy.run_path('tool.py', run_name='__main__')\n"
                % sys.executable)
    os.chmod(os.path.join(bin_dir, "pytest"), 0o755)
    patches = {}
    for mode in ("unnoticed", "caught", "broken"):
        with open(os.path.join(root, "target.txt"), "w") as f:
            f.write(mode + "\n")
        patches[mode] = os.path.join(tmp, mode + ".diff")
        with open(patches[mode], "w") as f:
            f.write(sh(root, "git", "diff").stdout)
        sh(root, "git", "checkout", "--", ".")
    return root, patches, bin_dir


def record_tier(root, bin_dir, tier, tool, *extra, cmd="pytest"):
    r = subprocess.run(
        [sys.executable, os.path.join(ASSETS, "verify_evidence.py"), "record",
         "--instance", "a", "--feature", "notes-create", "--verifier", "verifier-1",
         "--tier", tier, "--tool", tool, "--clauses", "C1", "--cmd", cmd, *extra],
        cwd=root, capture_output=True, text=True, timeout=30,
        env=dict(GENV, PATH=bin_dir + os.pathsep + GENV.get("PATH", "")))
    err = r.stderr.strip().replace("verify_evidence: ", "", 1)
    return r.returncode, r.stdout.strip(), err.replace(sys.executable, "python")


def refusal(rc, err):
    """The refusal a check saw: the exit status and the reason, without the advice."""
    return "exit %d: %s" % (rc, err.split(": nothing")[0])


def main():
    tmp = tempfile.mkdtemp(prefix="forge-eval-")
    live = []
    try:
        root = repo(tmp)
        vd = os.path.join(root, SKILL)

        # 1. Mode selection is by detection.
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        check("detect: no verify skill -> forge", forge("detect", "--root", empty)
              == (0, "MODE: forge\n"))
        rc, out = forge("detect", "--root", root)
        check("detect: one verify skill -> maintain", rc == 0 and "MODE: maintain" in out,
              out.strip())

        # 2. The contract: the golden skill passes, a scaffold does not.
        rc, out = forge("lint", vd)
        check("lint: a filled, grounded verify skill passes", rc == 0, out.strip()[-60:])
        other = os.path.join(tmp, "other")
        os.makedirs(other)
        forge("scaffold", "--app-root", other, "--app", "other")
        rc, out = forge("lint", os.path.join(other, ".claude", "skills", "verify-other"))
        check("negative: an unfilled scaffold (FILL: markers) is rejected",
              rc == 1 and "FILL:" in out, "%d FAIL lines" % out.count("FAIL:"))

        # 3. Placeholder, coordinate and evidence-deleting variants.
        ok, _ = lint_after(tmp, "p1", "SKILL.md", "`notes` is a small",
                           "`control-atlas` is a small")
        check("negative: a name carried over from an example is rejected", ok)
        ok, _ = lint_after(tmp, "p2", "SKILL.md", 'list --instance "$INSTANCE"\n',
                           'list --instance "$INSTANCE"\npage.mouse.click(120, 340)\n')
        check("negative: a Drive that clicks screen coordinates is rejected", ok)
        ok, _ = lint_after(tmp, "p3", "SKILL.md", 'rm -rf ".verify-run/$INSTANCE"',
                           "rm -rf .verify")
        check("negative: a Cleanup that deletes evidence is rejected", ok)

        # 4. Probe 6: a feature claims proven for source that no longer exists.
        root6 = repo(tmp, "p6")
        mutate(root6, os.path.join("features", "notes-list.md"), "- proven: no",
               "- proven: " + "b" * 40)
        os.remove(os.path.join(root6, "app.py"))
        rc, out = forge("lint", os.path.join(root6, SKILL))
        check("probe 6: proven feature whose source path is gone is rejected",
              rc == 1 and "claims proven" in out and "no longer exists" in out)

        # 5. Live: launch two instances side by side, doctor, drive, record, clean up.
        harness = os.path.join(SKILL, "scripts", "notes_harness.py")
        recorder = os.path.join(SKILL, "scripts", "verify_evidence.py")
        launched = {}
        live.append((root, harness))
        for inst in ("a", "b"):
            r = sh(root, sys.executable, harness, "launch", "--instance", inst)
            try:
                launched[inst] = json.loads(r.stdout)
            except ValueError:
                launched[inst] = {}
        ports = {i: d.get("port") for i, d in launched.items()}
        check("live: two instances ready at once",
              all(d.get("ready") for d in launched.values()),
              json.dumps(ports) if all(d.get("ready") for d in launched.values())
              else json.dumps(launched)[:900])
        check("live: each instance has its own port", ports["a"] != ports["b"])
        r = sh(root, sys.executable, recorder, "claim", "--instance", "b", "--port",
               str(ports["a"]))
        check("probe 9: a second owner claiming a held port is refused", r.returncode == 3,
              r.stderr.strip())
        r = sh(root, sys.executable, harness, "doctor", "--instance", "a", "--record",
               "--verifier", "verifier-1")
        check("live: doctor passes and is recorded", r.returncode == 0, r.stdout.strip())
        cap = os.path.join(".verify-run", "a", "capture")
        r = sh(root, sys.executable, harness, "create", "--instance", "a", "--title",
               "groceries", "--capture", cap)
        check("live: drive creates a note and observes the stored row", r.returncode == 0)
        r = sh(root, sys.executable, harness, "list", "--instance", "b")
        check("live: instance b's data store is its own",
              r.returncode == 0 and "groceries" not in r.stdout)
        # Probe 10: another checkout claims the same port for its own instance a (port
        # locks are per checkout), so its server loses the bind. Launch must not call
        # the first checkout's server ready: it moves to a free port and drives its own.
        root10 = repo(tmp, "p10")
        live.append((root10, harness))
        sh(root10, sys.executable, recorder, "claim", "--instance", "a", "--port",
           str(ports["a"]))
        r = sh(root10, sys.executable, harness, "launch", "--instance", "a")
        try:
            other = json.loads(r.stdout)
        except ValueError:
            other = {}
        r = sh(root10, sys.executable, harness, "list", "--instance", "a")
        check("probe 10: a launch that loses its port drives its own server, never "
              "another checkout's",
              other.get("ready") and other.get("port") != ports["a"]
              and r.returncode == 0 and "groceries" not in r.stdout,
              json.dumps(other)[:600] + " list=" + r.stdout.strip()[:200])
        r = sh(root, sys.executable, recorder, "record", "--instance", "a", "--feature",
               "notes-create", "--verifier", "verifier-1", "--result", "pass", "--action",
               "POST /notes title=groceries", "--observed", "201 and a stored row",
               "--side-effect", "a row in notes.jsonl", "--artifact",
               os.path.join(cap, "create.json"))
        head = sh(root, "git", "rev-parse", "HEAD").stdout.strip()
        ev = os.path.join(root, ".verify", "a", "notes-create", head, "evidence.json")
        check("live: evidence recorded under .verify/<instance>/<feature>/<sha>/",
              r.returncode == 0 and os.path.isfile(ev), os.path.relpath(ev, root))
        for inst in ("a", "b"):
            sh(root, sys.executable, harness, "stop", "--instance", inst)
            sh(root, sys.executable, recorder, "release", "--instance", inst)
            shutil.rmtree(os.path.join(root, ".verify-run", inst), ignore_errors=True)
        doc = {}
        if os.path.isfile(ev):
            with open(ev, encoding="utf-8") as f:
                doc = json.load(f)
        check("live: evidence survives cleanup and is bound to the head",
              doc.get("sha") == head and os.path.isfile(os.path.join(
                  os.path.dirname(ev), "create.json")))

        # 6. The Manifold join.
        md = os.path.join(root, ".manifold")
        os.makedirs(md)
        with open(os.path.join(md, "notes.json"), "w") as f:
            json.dump({"schema_version": 3, "feature": "notes", "phase": "VERIFIED",
                       "constraints": {"business": [{"id": "B1", "type": "invariant"}],
                                       "security": [{"id": "S1", "type": "invariant"}]},
                       "anchors": {"required_truths": [
                           {"id": "RT-1", "maps_to": ["B1", "S1"]}]}}, f)
        with open(os.path.join(md, "notes.md"), "w") as f:
            f.write("#### B1: Notes are stored\n\n#### S1: No titles in logs\n")
        with open(os.path.join(md, "broken.json"), "w") as f:
            f.write("{nope")
        rc, out = forge("coverage", vd)
        check("manifold: anchored constraint with no proof is named",
              "UNCOVERED: notes:S1" in out and "COVERED: notes:B1 -> notes-create" in out)
        check("manifold: a malformed manifold degrades instead of aborting",
              rc == 0 and "DEGRADED: E_PARSE broken.json" in out)

        # 7. Probe 8: maintain mode meets a real product bug.
        root8 = repo(tmp, "p8")
        mutate(root8, os.path.join("features", "notes-create.md"), "The response is 201",
               "The response is 500")
        rc, out = forge("check-maintain", os.path.join(root8, SKILL), "--base", "HEAD")
        check("probe 8: a map rewritten to match a bug is rejected",
              rc == 1 and "FAIL: notes-create" in out)
        sh(root8, "git", "checkout", "--", ".")
        rc, out = forge("finding", os.path.join(root8, SKILL), "--feature", "notes-create",
                        "--expected", "201", "--observed", "500", "--worktree", root8)
        rc2, _ = forge("check-maintain", os.path.join(root8, SKILL), "--base", "HEAD")
        check("probe 8: the bug is recorded as a finding and the map stays",
              rc == 0 and rc2 == 0 and "FINDING:" in out)

        # 8. Proof tiers: the real recorder refuses a tool outside its tier, a mutant
        # the check does not notice and a mutant that only breaks the build.
        rt, patches, tb = tier_repo(tmp)
        evd = os.path.join(rt, ".verify")
        rc, _, err = record_tier(rt, tb, "proved", "hypothesis")
        check("negative: tool outside its tier (--tier proved --tool hypothesis) is "
              "refused", rc == 2 and not os.path.exists(evd)
              and "not allowed for --tier proved" in err, refusal(rc, err))
        rc, _, err = record_tier(rt, tb, "property", "hypothesis", "--mutant-patch",
                                 patches["caught"], "--mutant-author", "verifier",
                                 cmd="%s tool.py" % shlex.quote(sys.executable))
        check("negative: a --cmd that is not a runner of its --tool (python tool.py "
              "for hypothesis) is refused", rc == 2 and not os.path.exists(evd)
              and "which is not a runner of --tool hypothesis" in err, refusal(rc, err))
        rc, _, err = record_tier(rt, tb, "property", "hypothesis", "--mutant-patch",
                                 patches["unnoticed"], "--mutant-author", "verifier")
        check("negative: a mutant the check does not notice (mutant run exits 0) is "
              "refused", rc == 1 and not os.path.exists(evd)
              and "does not catch the mutant" in err, refusal(rc, err))
        rc, _, err = record_tier(rt, tb, "property", "hypothesis", "--mutant-patch",
                                 patches["broken"], "--mutant-author", "verifier")
        check("negative: a mutant that only breaks the build (no failure signature) is "
              "refused", rc == 1 and not os.path.exists(evd)
              and "lacks" in err, refusal(rc, err))
        rc, out, err = record_tier(rt, tb, "property", "hypothesis", "--mutant-patch",
                                   patches["caught"], "--mutant-author", "verifier")
        head = sh(rt, "git", "rev-parse", "HEAD").stdout.strip()
        rec = os.path.join(evd, "a", "notes-create", head,
                           "proof-property-hypothesis-C1.json")
        check("tiers: a mutant that fails with the tool's signature is recorded",
              rc == 0 and os.path.isfile(rec) and out.startswith("PROOF: property"),
              "exit %d: %s" % (rc, os.path.basename(out) or refusal(rc, err)))
    finally:
        for r0, h in live:  # a failed check never strands an instance
            for inst in ("a", "b"):
                sh(r0, sys.executable, h, "stop", "--instance", inst)
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(RESULTS), RESULTS.count(False)
    print("EVAL_RESULT: %s (%d/%d checks)" % ("PASS" if not k else "FAIL",
                                              n - k if not k else k, n))
    return 0 if not k else 1


if __name__ == "__main__":
    sys.exit(main())
