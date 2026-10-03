"""End-to-end test for release-conductor (spec AC4): `init` -> `prep` -> the human's merge ->
`stage` (with an independent verifier's evidence) -> `deploy --approved-by` -> `verify-prod`,
and a second run whose production smoke check fails -> `rollback --approved-by` ->
`rolled_back`.

Stdlib only, offline. Staging and production are directories served by a local
http.server on 127.0.0.1 (`/<env>/version`); the recipe's deploy commands rewrite the served
version file and append to marker files; the version probe and the checks fetch from that
server; the remote is a local bare repository and the PR command is `true`. The verifier's
evidence is written in verification-skill-forge's recorder format (release_testkit's
write_evidence), never by importing another skill. Nothing touches a real target, and the
probe interval is shortened through the module constant, as the stage suite does."""
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release as RL  # noqa: E402
import contract_check as CC  # noqa: E402
from release_testkit import repo, serve, tmpdir, write_evidence, GIT  # noqa: E402
from test_release_prod import SCHEMA, schema_problems  # noqa: E402

VERIFY = ".claude/skills/verify-app"
DRIVER = "agent-1"
VERIFIER = "verifier-2"
FEATURES = ("login", "search")
# Inline programs for recipe argv: no `{` or `}`, since a recipe argv carries only the
# {version}/{commit}/{env} tokens.
FETCH = "import sys, urllib.request; print(urllib.request.urlopen(sys.argv[1], timeout=5).read().decode())"
APPEND = "import sys; open(sys.argv[1], 'a').write(' '.join(sys.argv[2:]) + chr(10))"
# Writes the served version file argv[1] with argv[3:], and appends the same to marker argv[2].
DEPLOY = ("import os, sys; os.makedirs(os.path.dirname(sys.argv[1]), exist_ok=True); "
          "t = ' '.join(sys.argv[3:]); open(sys.argv[1], 'w').write(t + chr(10)); "
          "open(sys.argv[2], 'a').write(t + chr(10))")


def git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                          env=dict(os.environ, **GIT))


def run(argv):
    """(rc, stdout) of release.main(argv), in-process, with the git identity env set and the
    probe interval shortened."""
    out = io.StringIO()
    with mock.patch.dict(os.environ, GIT), mock.patch.object(RL, "PROBE_INTERVAL", 0.05), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = RL.main(argv)
    return rc, out.getvalue()


def need(ok, what):
    """Raise AssertionError(what) unless ok: Project's steps are shared with the eval, which
    reports a failed step as a FAIL rather than a traceback."""
    if not ok:
        raise AssertionError(what)


class Project:
    """A temp project at 1.1.0 with a committed verify skill, a local bare origin, a local
    server standing in for staging and production (production already runs 1.1.0), and a
    recipe written through `release init` and committed as a reviewed PR would land it.
    `ci` commits a GitHub Actions workflow; `over` overrides recipe fields. The step
    methods are shared with eval/run_eval.py."""

    def __init__(self, smoke_path="/production/version", ci=None, **over):
        self.d = tmpdir()
        self.www = os.path.join(self.d, "www")
        os.makedirs(os.path.join(self.www, "production"))
        self.serve_prod("1.1.0")
        self.url = serve(self.www)
        self.markers = {k: os.path.join(self.d, k + ".marker")
                        for k in ("build", "staging", "production", "check", "smoke",
                                  "rollback")}
        py = sys.executable
        version_file = os.path.join(self.www, "{env}", "version")
        self.recipe = {
            "build": [py, "-c", APPEND, self.markers["build"], "{commit}"],
            "deploy_staging": [py, "-c", DEPLOY, version_file, self.markers["staging"],
                               "{version}", "{commit}"],
            "deploy_prod": [py, "-c", DEPLOY, version_file, self.markers["production"],
                            "{version}", "{commit}"],
            "rollback": [py, "-c", DEPLOY, os.path.join(self.www, "production", "version"),
                         self.markers["rollback"], "{version}"],
            "health": [py, "-c", FETCH, self.url + "/{env}/version"],
            "version_probe": [py, "-c", FETCH, self.url + "/{env}/version"],
            "staging_checks": [[py, "-c", FETCH, self.url + "/staging/version"],
                               [py, "-c", APPEND, self.markers["check"], "{env}"]],
            "prod_smoke": [[py, "-c", FETCH, self.url + smoke_path],
                           [py, "-c", APPEND, self.markers["smoke"], "{env}"]],
            "version": {"file": "package.json", "key": "version"},
            "bump": "minor", "artifact": "rebuild", "deploy_timeout": 3,
            "verify_skill": VERIFY}
        self.recipe.update(over)
        self.root = repo()
        self.commit_file(VERIFY + "/SKILL.md", "---\nname: verify-app\n---\n")
        for feat in FEATURES:
            self.commit_file(VERIFY + "/features/%s.md" % feat,
                             "# %s\n\n- id: %s\n- anchors: src\n" % (feat, feat))
        if ci is not None:
            self.commit_file(".github/workflows/ci.yml", ci)
        self.bare = tmpdir()
        subprocess.run(["git", "init", "-q", "--bare", self.bare], check=True)
        git(self.root, "remote", "add", "origin", self.bare)

    # -- fixtures --
    def commit_file(self, rel, text, msg="change", root=None):
        root = root or self.root
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        need(git(root, "add", "--", rel).returncode == 0, "git add %s" % rel)
        r = git(root, "commit", "-q", "-m", msg)
        need(r.returncode == 0, r.stderr)

    def serve_prod(self, text):
        """What production's /production/version answers from now on."""
        with open(os.path.join(self.www, "production", "version"), "w") as f:
            f.write(text + "\n")

    def cmd(self, *argv):
        return run([argv[0], "--root", self.root, *argv[1:]])

    def lines(self, key):
        try:
            with open(self.markers[key]) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def served(self, env):
        try:
            with open(os.path.join(self.www, env, "version")) as f:
                return f.read().strip()
        except FileNotFoundError:
            return None

    def rel(self):
        return RL.Release.load(self.root, "1.2.0")

    def tag(self):
        """The commit v1.2.0 points at on the origin, or None."""
        r = git(self.bare, "rev-parse", "-q", "--verify", "refs/tags/v1.2.0")
        return r.stdout.strip() or None

    # -- the flow --
    def init(self):
        """`release init` from an answers file, then the recipe committed on main."""
        answers = os.path.join(self.d, "answers.json")
        with open(answers, "w") as f:
            json.dump(self.recipe, f)
        rc, out = self.cmd("init", "--answers", answers)
        need(rc == 0 and "RELEASE: recipe written" in out, "init: " + out)
        need(git(self.root, "add", "--", RL.RECIPE_PATH).returncode == 0, "git add recipe")
        need(git(self.root, "commit", "-q", "-m", "Add the release recipe").returncode == 0,
             "commit recipe")  # the reviewed recipe PR, merged

    def prep(self):
        rc, out = self.cmd("prep", "--approved-by", "Dana", "--driver", DRIVER,
                           "--pr-cmd", '["true"]')
        need(rc == 0 and "RELEASE: 1.2.0 prepped" in out.splitlines(), "prep: " + out)

    def prep_wt(self):
        return os.path.join(self.root, ".skill-contract", "releases", "1.2.0", "wt-prep")

    def merge(self):
        """The human merges the release PR: a fast-forward of main, with the root on main.
        Returns the merged release commit."""
        r = git(self.root, "merge", "-q", "--ff-only", "release/1.2.0")
        need(r.returncode == 0, "merge: " + r.stderr)
        return git(self.root, "rev-parse", "HEAD").stdout.strip()

    def built(self):
        """init, prep, merge and the first stage; returns the release commit."""
        self.init()
        self.prep()
        commit = self.merge()
        rc, out = self.cmd("stage")
        need(rc == 0 and "NEXT: dispatch-verifier %s" % commit in out.splitlines(),
             "stage: " + out)
        return commit

    def record(self, sha, verifier=VERIFIER):
        """The verifier dispatch, simulated: every mapped feature recorded at sha in the
        recorder's format (verification-skill-forge's verify_evidence.py record shape)."""
        for feat in FEATURES:
            write_evidence(self.root, sha, feat, verifier)

    def staged(self):
        """built, then a verifier other than the driver records every feature at exactly
        the release commit, and stage --evidence stages it. Returns the commit."""
        commit = self.built()
        self.record(commit)
        rc, out = self.cmd("stage", "--evidence", "--verifier", VERIFIER)
        need(rc == 0 and "STAGE: 1.2.0 pass %s" % commit in out.splitlines(),
             "stage --evidence: " + out)
        return commit

    def deployed(self):
        """staged, then deployed with the human's yes. Returns the commit."""
        commit = self.staged()
        rc, out = self.cmd("deploy", "--approved-by", "Dana")
        need(rc == 0 and "DEPLOY: 1.2.0 deployed %s" % commit in out.splitlines(),
             "deploy: " + out)
        return commit


def result_payload(p, out, outcome):
    """The release-result/v1 envelope the RESULT: line names, validated by the checker
    (C1-C10) and by the payload schema. Returns its payload; AssertionError otherwise."""
    lines = [ln for ln in out.splitlines() if ln.startswith("RESULT: ")]
    need(len(lines) == 1, "one RESULT: line: " + out)
    path = os.path.join(p.root, lines[0].split(": ", 1)[1])
    rep = CC.check_envelope(path, root=p.root)
    need(rep["violations"] == [], "checker: %r" % rep["violations"])
    with open(path, encoding="utf-8") as f:
        st = json.load(f)
    need(st["predicateType"] == RL.RESULT_KIND, st["predicateType"])
    with open(SCHEMA, encoding="utf-8") as f:
        schema = json.load(f)
    payload = st["predicate"]["payload"]
    problems = schema_problems(payload, schema, schema)
    need(problems == [], "schema: %r" % problems)
    need(payload["outcome"] == outcome, payload["outcome"])
    return payload


class EndToEndTests(unittest.TestCase):
    def test_a_merged_change_reaches_verified_production(self):
        p = Project()
        p.init()
        p.prep()
        self.assertTrue(git(p.bare, "rev-parse", "-q", "--verify",
                            "refs/heads/release/1.2.0").stdout.strip())
        commit = p.merge()
        rc, out = p.cmd("stage")
        self.assertEqual(rc, 0, out)
        self.assertIn("NEXT: dispatch-verifier %s" % commit, out.splitlines())
        self.assertEqual(p.lines("build"), [commit])
        self.assertEqual(p.lines("staging"), [])  # nothing reaches staging before evidence
        p.record(commit)
        rc, out = p.cmd("stage", "--evidence", "--verifier", VERIFIER)
        self.assertEqual(rc, 0, out)
        self.assertIn("STAGE: 1.2.0 pass %s" % commit, out.splitlines())
        self.assertEqual(p.served("staging"), "1.2.0 %s" % commit)
        self.assertEqual(p.lines("check"), ["staging"])
        self.assertEqual(p.tag(), commit)

        # the production yes: without it, nothing runs
        rc, out = p.cmd("deploy")
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: waiting-human", out.splitlines())
        self.assertEqual(p.lines("production"), [])
        rc, out = p.cmd("deploy", "--approved-by", "Dana")
        self.assertEqual(rc, 0, out)
        self.assertIn("DEPLOY: 1.2.0 deployed %s" % commit, out.splitlines())
        self.assertIn("approval: Dana (CLAIMED)", out)
        self.assertEqual(p.lines("production"), ["1.2.0 %s" % commit])
        self.assertEqual(p.rel().rollback_target["version"], "1.1.0")

        rc, out = p.cmd("verify-prod")
        self.assertEqual(rc, 0, out)
        self.assertIn("PROD: 1.2.0 verified", out.splitlines())
        payload = result_payload(p, out, "verified")
        self.assertEqual((payload["version"], payload["commit"]), ("1.2.0", commit))
        self.assertEqual(payload["approved_by"], {"name": "Dana", "status": "CLAIMED"})
        self.assertEqual(payload["staging"]["verifier"], VERIFIER)
        self.assertEqual(payload["staging"]["features"], list(FEATURES))
        self.assertEqual(payload["production"]["smoke"], [{"rc": 0}, {"rc": 0}])
        self.assertEqual(p.lines("smoke"), ["production"])
        self.assertEqual(p.rel().status, "verified")
        self.assertEqual(p.lines("check"), ["staging"])  # staging checks never hit production
        self.assertEqual(p.lines("rollback"), [])
        # the release grant is revoked once the release is finished
        self.assertIn("grant_revoked", [e["event"] for e in p.rel().events()])
        rc, out = p.cmd("status")
        self.assertIn("RELEASE: 1.2.0 verified", out.splitlines())

    def test_a_failing_smoke_check_rolls_back_only_with_the_yes(self):
        p = Project(smoke_path="/production/missing")  # 404: the smoke check fails
        p.deployed()
        rc, out = p.cmd("verify-prod")
        self.assertEqual(rc, 3, out)
        self.assertIn("PROD: 1.2.0 fail smoke-failed", out.splitlines())
        self.assertIn("NEXT: ask the human to roll back", out.splitlines())
        rc, out = p.cmd("rollback")
        self.assertEqual(rc, 3, out)
        self.assertIn("STOP: waiting-human", out.splitlines())
        self.assertEqual(p.lines("rollback"), [])
        rc, out = p.cmd("rollback", "--approved-by", "Dana")
        self.assertEqual(rc, 0, out)
        self.assertIn("ROLLBACK: 1.2.0 rolled-back 1.1.0", out.splitlines())
        self.assertEqual(p.served("production"), "1.1.0")
        self.assertEqual(p.lines("rollback"), ["1.1.0"])
        payload = result_payload(p, out, "rolled_back")
        self.assertEqual(payload["rollback_target"]["version"], "1.1.0")
        self.assertEqual(payload["rollback"]["approved_by"],
                         {"name": "Dana", "status": "CLAIMED"})
        self.assertEqual(p.rel().status, "rolled_back")


if __name__ == "__main__":
    unittest.main()
