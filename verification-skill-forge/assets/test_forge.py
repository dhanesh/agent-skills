#!/usr/bin/env python3
"""Unit tests for forge.py and verify_evidence.py (stdlib only, offline)."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import forge as F  # noqa: E402
import verify_evidence as V  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(HERE), "eval", "fixtures", "notes-app")
VERIFY = os.path.join(".claude", "skills", "verify-notes")
GENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def run(fn, argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = fn(argv)
    return rc, out.getvalue()


def git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                          env=GENV, check=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "app")
        shutil.copytree(FIXTURE, self.root)
        self.vd = os.path.join(self.root, VERIFY)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "x")
        self.env = os.environ.pop("VERIFY_EVIDENCE_DIR", None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self.env is not None:
            os.environ["VERIFY_EVIDENCE_DIR"] = self.env

    def edit(self, rel, old, new):
        path = os.path.join(self.vd, rel)
        text = F.read(path)
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace(old, new, 1))

    def lint(self):
        return run(F.main, ["lint", self.vd])


class LintTests(Base):
    def test_golden_passes(self):
        rc, out = self.lint()
        self.assertEqual(rc, 0, out)
        self.assertIn("LINT_RESULT: PASS", out)

    def test_placeholder_fails(self):
        self.edit("SKILL.md", "## Drive\n", "## Drive\n\nFILL: selectors\n")
        rc, out = self.lint()
        self.assertEqual(rc, 1)
        self.assertIn("FILL:", out)

    def test_example_name_fails(self):
        self.edit("SKILL.md", "`notes` is a small", "`control-atlas` is a small")
        self.assertIn("not from this repo", self.lint()[1])

    def test_missing_section_fails(self):
        self.edit("SKILL.md", "## Doctor\n", "## Health\n")
        self.assertIn("'## Doctor' is missing", self.lint()[1])

    def test_launch_without_instance_fails(self):
        path = os.path.join(self.vd, "SKILL.md")
        text = F.read(path)
        launch = F.sections(F.frontmatter(text)[1])["Launch"]
        new = launch.replace('--instance "$INSTANCE"', "--instance a")
        with open(path, "w") as f:
            f.write(text.replace(launch, new))
        self.assertIn("no command takes the instance", self.lint()[1])

    def test_a_cli_may_state_it_opens_no_port(self):
        path = os.path.join(self.vd, "SKILL.md")
        text = F.read(path)
        launch = F.sections(F.frontmatter(text)[1])["Launch"]
        new = launch.replace('python3 "$SKILL/scripts/verify_evidence.py" port --instance '
                             '"$INSTANCE"\n', "")
        new = new.replace("verify_evidence.py port", "the recorder")
        with open(path, "w") as f:
            f.write(text.replace(launch, new))
        self.assertIn("allocate the instance's port", self.lint()[1])
        text = F.read(path)
        with open(path, "w") as f:
            f.write(text.replace("## Launch\n", "## Launch\n\nA CLI: it opens no port.\n", 1))
        self.assertNotIn("allocate the instance's port", self.lint()[1])

    def test_doctor_that_writes_fails(self):
        self.edit("SKILL.md", '--record --verifier "$VERIFIER"\n',
                  '--record --verifier "$VERIFIER"\nrm -f .verify-run/a/pid\n')
        self.assertIn("MUST be read-only", self.lint()[1])

    def test_coordinates_fail(self):
        self.edit("SKILL.md", 'list --instance "$INSTANCE"\n',
                  'list --instance "$INSTANCE"\npage.mouse.click(120, 340)\n')
        self.assertIn("by position or tab order", self.lint()[1])

    def test_reworded_standard_fails(self):
        self.edit("SKILL.md", "Never internal setters, never test-only endpoints.",
                  "Prefer real paths.")
        self.assertIn("missing or reworded", self.lint()[1])

    def test_cleanup_deleting_evidence_fails(self):
        self.edit("SKILL.md", 'rm -rf ".verify-run/$INSTANCE"', 'rm -rf .verify')
        self.assertIn("deletes evidence", self.lint()[1])

    def test_cleanup_run_state_is_allowed(self):
        self.assertNotIn("deletes evidence", self.lint()[1])

    def test_pkill_fails(self):
        self.edit("SKILL.md", 'rm -rf ".verify-run/$INSTANCE"', "pkill -f app.py")
        self.assertIn("kills by process name", self.lint()[1])

    def test_drifted_recorder_fails(self):
        with open(os.path.join(self.vd, "scripts", "verify_evidence.py"), "a") as f:
            f.write("# drift\n")
        self.assertIn("differs from the forge's copy", self.lint()[1])

    def test_unlisted_and_dead_index_entries_fail(self):
        shutil.copy(os.path.join(self.vd, "features", "notes-list.md"),
                    os.path.join(self.vd, "features", "notes-extra.md"))
        self.edit(os.path.join("features", "README.md"), "- [notes-list]",
                  "- [gone](gone.md) — x\n- [notes-list]")
        out = self.lint()[1]
        self.assertIn("notes-extra.md is not listed", out)
        self.assertIn("gone.md, which does not exist", out)

    def test_duplicate_index_entry_fails(self):
        self.edit(os.path.join("features", "README.md"), "- [notes-list]",
                  "- [notes-list](notes-list.md) — again\n- [notes-list]")
        self.assertIn("lists notes-list.md 2 times", self.lint()[1])

    def test_proven_feature_with_dead_anchor_fails(self):
        self.edit(os.path.join("features", "notes-list.md"), "- proven: no",
                  "- proven: " + "a" * 40)
        self.edit(os.path.join("features", "notes-list.md"), "app.py:do_GET",
                  "server/list.py")
        out = self.lint()[1]
        self.assertIn("claims proven at aaaaaaaaaaaa but anchor server/list.py no longer "
                      "exists", out)

    def test_symbol_anchor_must_be_present(self):
        self.edit(os.path.join("features", "notes-list.md"), "app.py:do_GET",
                  "app.py:do_DELETE")
        self.assertIn("no longer mentions do_DELETE", self.lint()[1])

    def test_feature_without_exit_code_fails(self):
        self.edit(os.path.join("features", "notes-list.md"),
                  "Expect exit 0 and JSON", "It returns JSON")
        self.assertIn("no expected output or exit code", self.lint()[1])

    def test_reserved_feature_id_fails(self):
        os.rename(os.path.join(self.vd, "features", "notes-list.md"),
                  os.path.join(self.vd, "features", "doctor.md"))
        self.assertIn("not a feature id", self.lint()[1])

    def test_scaffold_fails_lint_until_filled(self):
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        rc, out = run(F.main, ["scaffold", "--app-root", other, "--app", "other"])
        self.assertEqual(rc, 0, out)
        rc, out = run(F.main, ["lint", os.path.join(other, ".claude", "skills",
                                                    "verify-other")])
        self.assertEqual(rc, 1)
        self.assertIn("FILL:", out)


class DetectTests(Base):
    def test_modes(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        self.assertEqual(run(F.main, ["detect", "--root", empty]), (0, "MODE: forge\n"))
        rc, out = run(F.main, ["detect", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("MODE: maintain .claude/skills/verify-notes", out)
        shutil.copytree(self.root, os.path.join(self.root, "apps", "two"),
                        ignore=shutil.ignore_patterns(".git"))
        rc, out = run(F.main, ["detect", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertEqual(out.count("CANDIDATE:"), 2)


MANIFOLD = {"schema_version": 3, "feature": "notes", "phase": "ANCHORED",
            "constraints": {"business": [{"id": "B1", "type": "invariant"},
                                         {"id": "B2", "type": "goal"}],
                            "security": [{"id": "S1", "type": "invariant"}]},
            "anchors": {"required_truths": [{"id": "RT-1", "status": "NOT_SATISFIED",
                                             "maps_to": ["B1", "S1"]}]}}
MANIFOLD_MD = "#### B1: Notes are stored\n\n#### S1: No secrets in logs\n"


class ManifoldTests(Base):
    def write(self, name, doc, md=None):
        d = os.path.join(self.root, ".manifold")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name + ".json"), "w") as f:
            f.write(doc if isinstance(doc, str) else json.dumps(doc))
        if md:
            with open(os.path.join(d, name + ".md"), "w") as f:
                f.write(md)

    def test_coverage_names_uncovered(self):
        self.write("notes", MANIFOLD, MANIFOLD_MD)
        rc, out = run(F.main, ["coverage", self.vd])
        self.assertEqual(rc, 0)
        self.assertIn("COVERED: notes:B1 -> notes-create", out)
        self.assertIn('UNCOVERED: notes:S1 "No secrets in logs"', out)
        self.assertNotIn("notes:B2", out)  # a goal no truth maps to is not anchored
        self.assertIn("COVERAGE: 1/2", out)
        self.assertEqual(run(F.main, ["coverage", self.vd, "--require-total"])[0], 3)

    def test_unanchored_phase_is_ignored(self):
        self.write("notes", dict(MANIFOLD, phase="CONSTRAINED"))
        self.assertIn("COVERAGE: source-derived", run(F.main, ["coverage", self.vd])[1])

    def test_manifold_without_mapped_truths_anchors_everything(self):
        doc = dict(MANIFOLD, anchors={"required_truths": [{"id": "RT-1"}]})
        self.write("notes", doc)
        self.assertIn("COVERAGE: 1/3", run(F.main, ["coverage", self.vd])[1])

    def test_malformed_manifold_degrades(self):
        self.write("broken", "{not json")
        self.write("odd", {"schema_version": 9, "constraints": {}})
        self.write("notes", dict(MANIFOLD, anchors={"required_truths": [
            {"id": "RT-1", "maps_to": ["B1", "Z9"]}]}))
        rc, out = run(F.main, ["coverage", self.vd])
        self.assertEqual(rc, 0, out)
        self.assertIn("DEGRADED: E_PARSE broken.json", out)
        self.assertIn("DEGRADED: E_SCHEMA odd.json", out)
        self.assertIn("DEGRADED: E_LINK notes.json", out)
        self.assertIn("COVERED: notes:B1", out)

    def test_legacy_yaml_manifold_is_read(self):
        d = os.path.join(self.root, ".manifold")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "notes.yaml"), "w") as f:
            f.write("feature: notes\nphase: VERIFIED\nconstraints:\n  business:\n"
                    "    - id: B1\n      type: invariant\n      statement: \"Notes are stored\"\n"
                    "    - id: B2\n      type: goal\n      statement: \"Unmapped goal\"\n"
                    "  security:\n    - id: S1\n      statement: \"No secrets in logs\"\n")
        with open(os.path.join(d, "notes.anchor.yaml"), "w") as f:
            f.write("required_truths:\n  - id: RT-1\n    maps_to_constraint: B1\n"
                    "  - id: RT-2\n    satisfies_constraints: [S1, S9]\n")
        rc, out = run(F.main, ["coverage", self.vd])
        self.assertEqual(rc, 0, out)
        self.assertIn("DEGRADED: LEGACY notes.yaml", out)
        self.assertIn("DEGRADED: E_LINK notes.yaml", out)
        self.assertIn("COVERED: notes:B1 -> notes-create", out)
        self.assertIn('UNCOVERED: notes:S1 "No secrets in logs"', out)
        self.assertNotIn("notes:B2", out)
        self.assertIn("COVERAGE: 1/2", out)

    def test_seed_writes_stubs_that_lint_refuses(self):
        self.write("notes", MANIFOLD, MANIFOLD_MD)
        rc, out = run(F.main, ["seed", self.vd])
        self.assertIn("SEEDED: no-secrets-in-logs (notes:S1)", out)
        self.assertTrue(os.path.isfile(os.path.join(self.vd, "features",
                                                    "no-secrets-in-logs.md")))
        self.assertIn("COVERAGE: 2/2", run(F.main, ["coverage", self.vd])[1])
        self.assertEqual(self.lint()[0], 1)


class RecorderTests(Base):
    def rec(self, *argv):
        return run(V.main, [*argv, "--worktree", self.root])

    def test_record_is_bound_to_head(self):
        art = os.path.join(self.tmp, "cap.json")
        with open(art, "w") as f:
            f.write("{}")
        rc, out = self.rec("record", "--instance", "a", "--feature", "notes-create",
                           "--verifier", "v1", "--result", "pass", "--action", "x",
                           "--observed", "y", "--side-effect", "z", "--artifact", art)
        self.assertEqual(rc, 0, out)
        head = git(self.root, "rev-parse", "HEAD").stdout.strip()
        path = os.path.join(self.root, ".verify", "a", "notes-create", head,
                            "evidence.json")
        with open(path) as f:
            doc = json.load(f)
        self.assertEqual(doc["sha"], head)
        self.assertEqual(doc["artifacts"][0]["sha256"], V.sha256_file(
            os.path.join(os.path.dirname(path), "cap.json")))

    def test_record_requires_side_effect_and_artifact(self):
        rc, _ = self.rec("record", "--instance", "a", "--feature", "notes-create",
                         "--verifier", "v1", "--result", "pass", "--action", "x",
                         "--observed", "y")
        self.assertEqual(rc, 2)

    def test_reserved_feature_refused(self):
        rc, _ = self.rec("record", "--instance", "a", "--feature", "doctor", "--verifier",
                         "v", "--result", "pass", "--action", "x", "--observed", "y",
                         "--side-effect", "z", "--artifact", __file__)
        self.assertEqual(rc, 2)

    def test_ports_never_collide(self):
        rc, out = self.rec("port", "--instance", "a")
        self.assertEqual(rc, 0)
        pa = int(out.split()[1])
        rc, out = self.rec("port", "--instance", "b")
        self.assertNotEqual(int(out.split()[1]), pa)
        self.assertEqual(self.rec("claim", "--instance", "b", "--port", str(pa))[0], 3)
        self.assertEqual(self.rec("port", "--instance", "a")[1], "PORT: %d\n" % pa)
        self.rec("release", "--instance", "a")
        self.assertEqual(self.rec("claim", "--instance", "b", "--port", str(pa))[0], 0)

    def test_doctor_red_when_a_check_fails(self):
        rc, out = self.rec("doctor", "--instance", "a", "--ok", "--check", "port=fail")
        self.assertIn("DOCTOR: red", out)
        self.assertEqual(self.rec("doctor", "--instance", "a", "--ok")[0], 2)


class MaintainTests(Base):
    def test_proof_rewritten_without_source_change_fails(self):
        self.edit(os.path.join("features", "notes-create.md"),
                  "The response is 201", "The response is 200")
        rc, out = run(F.main, ["check-maintain", self.vd, "--base", "HEAD"])
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL: notes-create", out)

    def test_the_guard_holds_through_a_symlinked_path(self):
        # macOS temp dirs are /var -> /private/var; git reports the resolved path.
        link = os.path.join(self.tmp, "link")
        os.symlink(self.root, link)
        self.edit(os.path.join("features", "notes-create.md"),
                  "The response is 201", "The response is 200")
        rc, out = run(F.main, ["check-maintain", os.path.join(link, VERIFY), "--base", "HEAD"])
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL: notes-create", out)

    def test_proof_rewritten_with_source_change_passes(self):
        self.edit(os.path.join("features", "notes-create.md"),
                  "The response is 201", "The response is 200")
        with open(os.path.join(self.root, "app.py"), "a") as f:
            f.write("# changed\n")
        rc, out = run(F.main, ["check-maintain", self.vd, "--base", "HEAD"])
        self.assertEqual(rc, 0, out)

    def test_gotcha_edits_are_free(self):
        self.edit(os.path.join("features", "notes-create.md"), "is a 400", "returns 400")
        self.assertEqual(run(F.main, ["check-maintain", self.vd, "--base", "HEAD"])[0], 0)

    def test_finding_is_recorded(self):
        rc, out = run(F.main, ["finding", self.vd, "--feature", "notes-create",
                               "--expected", "201", "--observed", "500", "--worktree",
                               self.root])
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.isfile(out.split(": ", 1)[1].strip()))


class ReviewFindingTests(Base):
    """Regressions for the /code-review findings on the forge and the recorder."""

    def test_artifacts_sharing_a_name_are_refused(self):
        for sub in ("before", "after"):
            os.makedirs(os.path.join(self.tmp, sub))
            with open(os.path.join(self.tmp, sub, "page.png"), "w") as f:
                f.write(sub)
        rc, _ = run(V.main, ["record", "--instance", "a", "--feature", "notes-create",
                             "--verifier", "v", "--result", "pass", "--action", "x",
                             "--observed", "y", "--side-effect", "z", "--artifact",
                             os.path.join(self.tmp, "before", "page.png"), "--artifact",
                             os.path.join(self.tmp, "after", "page.png"),
                             "--worktree", self.root])
        self.assertEqual(rc, 2)

    def test_wrong_shaped_manifolds_degrade_instead_of_crashing(self):
        d = os.path.join(self.root, ".manifold")
        os.makedirs(d)
        docs = {"a": {"phase": "ANCHORED", "constraints": {"b": [{"id": "B1"}]},
                      "anchors": []},
                "b": {"phase": "ANCHORED", "constraints": {"b": [{"id": ""}, {"id": "B1"}]}},
                "c": {"phase": "ANCHORED", "schema_version": "3", "constraints": {}},
                "e": {"phase": "ANCHORED", "constraints": {"b": [{"id": "B1"}]},
                      "anchors": {"required_truths": [{"maps_to": "B1"}, "x"]}}}
        for name, doc in docs.items():
            with open(os.path.join(d, name + ".json"), "w") as f:
                json.dump(doc, f)
        rc, out = run(F.main, ["coverage", self.vd])
        self.assertEqual(rc, 0, out)
        self.assertIn("DEGRADED: E_SCHEMA c.json", out)
        self.assertIn("COVERAGE:", out)

    def test_read_only_doctor_checks_pass_lint(self):
        self.edit("SKILL.md", '--record --verifier "$VERIFIER"\n',
                  '--record --verifier "$VERIFIER"\ntest -d ".verify-run/$INSTANCE"\n'
                  '[ -d build ] && echo ok\ncurl -s "localhost:1/" | grep -q \'<title>\'\n')
        self.assertNotIn("MUST be read-only", self.lint()[1])

    def test_real_writes_in_doctor_still_fail(self):
        for line in ('echo x > .verify-run/state', 'curl -s -d "a=1" localhost:1/',
                     'curl -X POST localhost:1/reset'):
            self.setUp()
            self.edit("SKILL.md", '--record --verifier "$VERIFIER"\n',
                      '--record --verifier "$VERIFIER"\n%s\n' % line)
            self.assertIn("MUST be read-only", self.lint()[1], line)


if __name__ == "__main__":
    unittest.main()
