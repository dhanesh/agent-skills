# release-conductor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** after the human merges a change, a new skill, `release-conductor`, prepares the release, verifies it locally and on staging, and puts the production deploy to the human as a single explicit yes. It then proves that production runs the release commit, and rolls back only with the human's yes.

**Architecture:**
- **Shared checker first.** The shared skill-contract checker gains the new action classes, a verified `--worktree` branch check, tag-trigger detection, subject-based grant selection with a revoke-all kill switch, and the release grant shape. The checker is then re-vendored to every adopter.
- **The release tool.** A new stdlib state tool, `release-conductor/assets/release.py`, drives `init`, `prep`, `stage`, `deploy`, `verify-prod` and `rollback`. It is built on factory-conductor's patterns: an atomic state file, an append-only log, a run lock, `NEXT:` lines on resume, and a grant check before every consequential step.
- **Other skills.** spec-first-planning records release defaults. Factory-conductor's `watch` selects its grant by subject.

**Tech Stack:** Python 3.10+, stdlib only, and git. No new dependencies, and no network at install time.

**Spec:** `docs/superpowers/specs/2026-10-03-release-conductor-design.md`. The owner decisions are D1–D11 and the System One calls are listed there. **D9–D11 amend D7 and AC6.** Where the earlier sections disagree with them, D9–D11 win.

## Global Constraints

- **Action classes.** `ACTION_CLASSES` gains `deploy_staging` and `push_tag`. Both are grantable. `deploy` stays in `IRREVERSIBLE_CLASSES` and is never grantable (A8 as amended).
- **Tag push with CI tag triggers.** When the CI config at the tagged commit has tag triggers, a tag push is judged as `deploy` (reason `ci-tag`). The recipe never decides this.
- **`--worktree <path>`.** The checker verifies that the path shares the root's git common dir. The worktree's HEAD then feeds the branch probe, `changed_since_default` and the tag-trigger scan. Grant lookup, staleness and the tracked-grant probe stay at `--root`. There is no `--branch` flag.
- **Grant selection.** `check-grant --subject X` uses the newest live (not superseded) grant that pins X. With no `--subject`, it keeps today's behaviour, the newest grant. `revoke-grant` with no id revokes **every** live grant. `--id` revokes one.
- **Release grant.** Its payload carries `release: {"version": "<semver>"}`. Its subjects are `.release/recipe.json` and `.skill-contract/releases/<version>/intent.json`. Only `release prep` writes it, with the human's name as the `grant-accepted` assertion.
- **The recipe** (`.release/recipe.json`). Its keys are:
  - `build`, `deploy_staging`, `deploy_prod`, `rollback`, `health`, `version_probe`, `staging_checks`, `prod_smoke`: argv lists, or lists of argv lists for `staging_checks` and `prod_smoke`. Each argv follows the `reentry.agent_cmd` shell and launcher refusals.
  - `version` (`{"file", "key"}` or `{"cmd"}`) and `bump` (`patch`, `minor` or `major`).
  - `artifact` (`"rebuild"` or `{"path"}`).
  - `deploy_timeout` (seconds, default 600).
  - `verify_skill` (a repo-relative dir).
  - Placeholders: `{version}`, `{commit}` and `{env}` only.
- **Release state.** It lives in `.skill-contract/releases/<version>/`, which is git-ignored, with `state.json`, `release-log.jsonl`, `intent.json` and the lock. There is one release at a time per repo.
- **Machine lines.** `RELEASE:`, `STAGE:`, `DEPLOY:`, `PROD:`, `ROLLBACK:`, `NEXT:`, `GATE:` and `STOP:`. Exits: 0 ok, 3 must stop or needs the human, 2 refused or invalid.
- **The production deploy, rollback and CI-tag push** happen only after an in-session yes, given with `--approved-by "<human name>"`. The tool records it as CLAIMED. These steps are never unattended and never covered by a grant.
- **No test touches a real target.** Recipes in tests use stub argv that write marker files, and a local `http.server` stands in for staging and production.
- **SKILL.md** rules: BCP 14 register rows, a reason on every absolute (PP-5), and `$SKILL_DIR` paths. The vendored checker stays byte-identical (`make contract-vendor`). CI has no git identity, so test commits pass `GIT` env.
- **Commit trailer** on every commit:
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01X7cbN5bQsCx5HyMdb8Lo7X
  ```
- **The code in this plan is a strong starting point, not a transcript.** Make every listed test pass by fixing code, and never by weakening a test. If a listed test is itself wrong, fix it and say why in the report.

## Review Focus

- **A factory run and a release run in the same repo.** Each one's gates must find its own grant, and one `revoke-grant` must stop both. Tested in Task 1.
- **The user's root checkout sitting on `main` while `stage` runs in a worktree.** `stage` must still be gated on the worktree's branch, and never ASK `default-branch` because of where the root happens to be. Tested in Task 1 (checker) and Task 4 (stage).
- **An asynchronous deploy.** The version probe reports the old version for a while before it switches. `verify-prod` must poll up to `deploy_timeout` and not offer a rollback early. Tested in Task 6.
- **A recipe edited in a commit after prep.** `stage` and `deploy` must ask, and must never run the edited commands. The comparison uses the recipe at the pinned commit (`git show <sha>:.release/recipe.json`). Tested in Task 4.
- **A crash during `deploying`.** On resume, the tool must never re-run `deploy_prod`. It runs `verify-prod` and asks. Tested in Task 5.

---

### Task 1: The checker — new classes, `--worktree`, tag triggers, subject selection, revoke-all, release grants

**Files:**
- Modify: `docs/skill-contract/reference/contract_check.py`, `docs/skill-contract/SPEC.md`
- Test: `docs/skill-contract/reference/test_contract_check.py`
- Then: `make contract-vendor`, which updates every `*/assets/contract_check.py`
- Modify: `factory-conductor/assets/conductor.py` (`_reentry_block` reads the grant that pins the run's plan), plus a test in `factory-conductor/assets/test_conductor_reentry.py`

**Interfaces:**
- Produces:
  - `ACTION_CLASSES` with `"deploy_staging"` and `"push_tag"`.
  - `grant_for_subject(root, subject) -> path | None`.
  - `revoke_all(root, now=None) -> list[str]`, returning the revoked ids.
  - `ci_tag_triggers(root, rev="HEAD") -> bool | None`, where None means git cannot say. It fails closed (see Step 3).
  - `argv_problems(argv, allowed_tokens) -> list[str]`: the shell/launcher refusals moved out of `reentry_problems`, which now calls it. Existing re-entry tests MUST still pass unchanged.
  - `worktree_ok(root, worktree) -> bool`.
  - `check_grant(..., worktree=None)`, plus the CLI flags `--worktree` and `revoke-grant --id`.
  - The release grant rule: if `payload.release` is present, `release.version` MUST match `^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$`.

- [ ] **Step 1: Write the failing tests** in `test_contract_check.py`. Build them with the existing helpers: `build_vectors.grant()`, the repo/git fixtures the existing check-grant tests use, and an env with a git identity.

```python
class ReleaseCheckerTests(unittest.TestCase):
    # use the existing check-grant test fixture that makes a temp repo + grant; adapt names.

    def test_new_classes_are_grantable(self):
        self.assertIn("deploy_staging", CC.ACTION_CLASSES)
        self.assertIn("push_tag", CC.ACTION_CLASSES)
        self.assertNotIn("deploy_staging", CC.IRREVERSIBLE_CLASSES)
        st = build_vectors.grant()
        st["predicate"]["payload"]["gate_policy"]["deploy_staging"] = "grant"
        self.assertEqual(CC.grant_violations(st), [])
        st["predicate"]["payload"]["gate_policy"]["deploy"] = "grant"
        self.assertTrue(CC.grant_violations(st))   # deploy stays never-grantable

    def test_subject_selects_its_own_grant(self):
        # two live grants: factory (spec+plan) and release (recipe+intent), release newer.
        # check_grant(subject=plan) -> COVERED via the factory grant;
        # check_grant(subject=recipe) -> COVERED via the release grant;
        # no subject -> newest (release) as today.
        ...

    def test_revoke_with_no_id_revokes_every_live_grant(self):
        # two live grants; revoke-grant (no id) -> both ASK revoked for their subjects
        ...

    def test_worktree_judges_the_worktree_branch(self):
        # root on main, worktree on release/1.2.0-stage (branch_pattern "release/*"):
        # check_grant(root, "deploy_staging", worktree=wt) -> COVERED;
        # without worktree -> ASK default-branch.
        ...

    def test_worktree_must_share_the_repository(self):
        # an unrelated repo's path as --worktree -> ASK "worktree"
        ...

    def test_tag_push_is_deploy_when_ci_may_run_on_tags(self):
        # fail closed: explicit tag triggers AND configs that run on tags by default
        for path, text in ((".github/workflows/rel.yml", "on:\n  push:\n    tags: ['v*']\n"),
                           (".github/workflows/a.yml", "on: push\njobs: {}\n"),
                           (".github/workflows/b.yml", "on: [push]\njobs: {}\n"),
                           (".github/workflows/c.yml", "on:\n  create:\njobs: {}\n"),
                           (".gitlab-ci.yml", "deploy:\n  rules:\n    - if: $CI_COMMIT_TAG\n"),
                           (".gitlab-ci.yml", "deploy:\n  script: ./ship\n"),       # no rules: runs on tags
                           ("Jenkinsfile", "pipeline { agent any }\n"),
                           (".buildkite/pipeline.yml", "steps:\n  - command: ./ship\n"),
                           (".circleci/config.yml", "filters:\n  tags:\n    only: /^v.*/\n")):
            with self.subTest(path=path):
                # commit `text` at `path`; push_tag granted -> ASK reason ci-tag
                ...

    def test_tag_push_is_grantable_only_when_proven_tag_free(self):
        # COVERED for: no CI config at all; a GitHub workflow with `on: push: branches: [main]`
        # and no create/tags; a CircleCI config with no `tags` filter.
        ...

    def test_tag_patterns_compile_on_this_python(self):
        import importlib; importlib.reload(CC)   # a mid-pattern (?m) raises re.error on 3.11+
        ...

    def test_release_version_must_be_semver(self):
        st = build_vectors.grant()
        st["predicate"]["payload"]["release"] = {"version": "v1.2"}
        self.assertTrue(any("release.version" in v for v in CC.grant_violations(st)))
        st["predicate"]["payload"]["release"] = {"version": "1.2.0"}
        self.assertEqual(CC.grant_violations(st), [])
```

  Fill each `...` with real setup. The existing check-grant tests show how to write a grant envelope, make a branch and commit in a temp repo. Every assertion in the comments MUST be made.

- [ ] **Step 2: Run them and see them fail.**

  Run: `python3 docs/skill-contract/reference/test_contract_check.py -k Release`
  Expected: FAIL, because the new names are undefined.

- [ ] **Step 3: Implement** in `contract_check.py`:

```python
ACTION_CLASSES = ("read_only", "local_reversible", "push_branch", "open_pr", "deploy_staging",
                  "push_tag", "merge", "deploy", "spend", "external_message", "delete")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")
# Tag triggers fail closed: every CI config counts as tag-triggered unless it is a format whose
# no-tag default is known AND the scan proves it is restricted. A false positive costs one extra
# ask; a false negative would let a grant cover a production deploy.
GH_BRANCH_ONLY_PUSH = re.compile(r"^\s*push\s*:\s*\n(?:\s+.*\n)*?\s+branches(?:-ignore)?\s*:", re.M)
GH_TAGGY = re.compile(r"^\s*(tags(?:-ignore)?|create|release|workflow_run)\s*:|\bon\s*:\s*\[?[^\n]*\b(push|create|release)\b", re.M)


def _ci_file_tag_triggered(path, text):
    """True unless `path` is a format with a known no-tag default and `text` proves it."""
    if path.startswith(".circleci/"):
        return bool(re.search(r"^\s*tags\s*:", text, re.M))      # CircleCI ignores tags by default
    if path.startswith(".github/workflows/"):
        if GH_TAGGY.search(text) and not GH_BRANCH_ONLY_PUSH.search(text):
            return True                                            # on: push / [push] / create / tags
        if re.search(r"^\s*(tags(?:-ignore)?|create|release)\s*:", text, re.M):
            return True
        return not GH_BRANCH_ONLY_PUSH.search(text) and bool(re.search(r"\bpush\b", text))
    if path.startswith(".github/actions/"):
        return False                                               # composite actions have no triggers
    return True     # GitLab (jobs run on tags unless ruled out), Jenkins, Buildkite, Drone, Azure, ...


def ci_tag_triggers(root, rev="HEAD"):
    """True when any CI config file at `rev` may run on a tag push, False when every one is
    proven not to, None when git cannot list or read the tree (callers fail closed)."""
    r = _git(root, "ls-tree", "-r", "-z", "--name-only", rev)
    if r is None or r.returncode != 0:
        return None
    for path in r.stdout.decode("utf-8", "surrogateescape").split("\0"):
        if path and is_ci_config(path):
            b = _git(root, "show", "%s:%s" % (rev, path))
            if b is None or b.returncode != 0:
                return None
            if _ci_file_tag_triggered(path, b.stdout.decode("utf-8", "replace")):
                return True
    return False


def argv_problems(argv, allowed_tokens):
    """Problems with one argv list: not a non-empty list of strings, a shell or launcher
    (the same refusals reentry_problems applies to agent_cmd), or a `{token}` outside
    `allowed_tokens`. Shared by re-entry and release-conductor recipes."""
    ...  # move the argv checks out of reentry_problems into here; reentry_problems calls it


def worktree_ok(root, worktree):
    """True when `worktree` is a git work tree of the same repository as `root`."""
    def common(d):
        r = _git(d, "rev-parse", "--path-format=absolute", "--git-common-dir")
        return None if r is None or r.returncode != 0 else os.path.realpath(r.stdout.strip().decode())
    a, b = common(root), common(worktree)
    return a is not None and a == b


def grant_for_subject(root, subject):
    """The newest live grant that pins `subject` (a root-relative path), or None."""
    key = _subject_key(subject)
    revised = {_revision_of(st) for _, st in _grant_envelopes(root, strict=False)}
    heads = [(st["predicate"]["generatedAtTime"], st["predicate"]["id"], p)
             for p, st in _grant_envelopes(root)
             if st["predicate"]["id"] not in revised
             and key in {_subject_key(s.get("name", "")) for s in st.get("subject") or []}]
    return max(heads)[2] if heads else None


def revoke_all(root, now=None):
    """Revoke every live grant under root; returns the revoked ids (the kill switch)."""
    revised = {_revision_of(st) for _, st in _grant_envelopes(root, strict=False)}
    ids = [st["predicate"]["id"] for _, st in _grant_envelopes(root)
           if st["predicate"]["id"] not in revised and not st["predicate"]["payload"].get("revoked")]
    for gid in ids:
        revoke_grant(root, gid, now=now)
    return ids
```

Then, in `grant_violations`, after the `reentry` check:

```python
    if "release" in p:
        rel = p["release"]
        if not (isinstance(rel, dict) and set(rel) == {"version"}
                and isinstance(rel.get("version"), str) and SEMVER_RE.match(rel["version"])):
            out.append("payload.release must be {\"version\": <semver>} (release.version)")
```

In `check_grant`:
- Add `worktree=None`.
- When `subject` is given and `path` is None, use `path = grant_for_subject(root, rel_subject)`. Fall back to `latest_grant(root)` only when `subject` is None.
- If `worktree` is given and `not worktree_ok(root, worktree)`, return `ask("worktree")`.
- Let `probe = worktree or root`. Use `probe` for `in_git_work_tree`, `current_branch`, `detect_default_branches` and `changed_since_default`.
- Keep `grant_is_tracked(root, path)` and `stale_names(root, ...)` at `root`.
- After the `gate-ask` check, add:

```python
    if action == "push_tag":
        tagged = ci_tag_triggers(probe, "HEAD")
        if tagged is None or tagged:
            return ask("ci-tag")   # pushing the tag would deploy: deploy class, never granted
```

- Extend the existing `ci-config` clause to `action in ("push_branch", "open_pr", "push_tag", "deploy_staging")`.

CLI changes:
- `check-grant` gains `--worktree`.
- `revoke-grant` without `--id` calls `revoke_all` and prints one `REVOKED: <id>` line per grant.
- With `--id`, `revoke-grant` keeps today's behaviour.
- Update the module docstring and `SPEC.md`:
  - the A8 amendment text: `deploy_staging` and `push_tag` are grantable, and `deploy` stays never grantable;
  - grant selection by subject;
  - revoke-all;
  - `--worktree`;
  - the `release` payload field;
  - register `release-result/v1` as a kind name (its schema lands in Task 6).

- [ ] **Step 4: Make factory-conductor select its own grant.** In `conductor.py` `_reentry_block(root)`, take the run's plan subject:
  - change the signature to `_reentry_block(root, plan_rel)`;
  - use `CC.grant_for_subject(root, plan_rel)` with **no fallback** to `latest_grant` (D9: another plan's grant must never enable this run's re-entry); no grant pinning the plan means re-entry is disabled;
  - pass `plan_subject(st)` from `_watch_locked` and `cmd_reentry`.

  Add two tests to `test_conductor_reentry.py`: (a) a newer unrelated grant pinning a different file does not make `watch` print `disabled` for the run, whose own grant carries a `reentry` block; (b) when no grant pins the run's plan but another plan's grant carries a `reentry` block, `watch` reports re-entry disabled.

- [ ] **Step 5: Run and vendor.**

  Run: `python3 docs/skill-contract/reference/test_contract_check.py && make contract-vendor && make contract && for t in factory-conductor/assets/test_conductor_*.py; do FACTORY_CONDUCTOR_TIMER_HOME=$(mktemp -d) FACTORY_CONDUCTOR_TIMER_DRYRUN=1 FACTORY_CONDUCTOR_TIMER_KIND=cron python3 $t || exit 1; done`
  Expected: all pass, and every vendored copy is byte-identical (`cmp`).

- [ ] **Step 6: Commit.**

```bash
git add docs/skill-contract */assets/contract_check.py factory-conductor/assets
git commit -m "feat(skill-contract): deploy_staging and push_tag classes, --worktree, tag-trigger floor, grants selected by subject, revoke-all"
```

---

### Task 2: `release.py` foundations — state, log, lock, recipe

**Files:**
- Create: `release-conductor/assets/release.py`
- Create: `release-conductor/assets/release_testkit.py`
- Create: `release-conductor/assets/test_release_state.py`
- Vendor: `release-conductor/assets/contract_check.py`. Run `make contract-vendor` after the skill exists. Its SKILL.md frontmatter and `## Contract` block are added in Task 8, so for now copy the reference by hand and let Task 8 make it an adopter.

**Interfaces:**
- Produces, in `release.py`:
  - `RELEASES_DIR = ".skill-contract/releases"`.
  - `class Release`, with `load(root, version)`, `new(root, version, base_commit)`, `save()`, `log(event, **f)`, `events()`, `status`, and `set_status(s, **fields)`.
  - `STATES = ("prepped", "staging_verify", "staged", "stage_failed", "awaiting_deploy", "deploying", "deployed", "verified", "prod_failed", "rolling_back", "rolled_back", "outcome_unknown")`.
  - `run_lock(root)`, a repo-wide lock at `.skill-contract/releases/.lock`, copied from `factory-conductor/assets/reentry.py` `run_lock` with a cross-reference comment, the same way `waves()` was copied.
  - `load_recipe(root, rev=None) -> (recipe | None, problems: list[str])`. When `rev` is given, it reads `git show <rev>:.release/recipe.json`.
  - `recipe_sha(root, rev=None) -> str`: sha256 of the file bytes (working tree) or of the `git show <rev>:.release/recipe.json` bytes, so an unchanged committed recipe gives exactly `CC.sha256_file` of the working file.
  - `expand(argv, values) -> list[str]`, which replaces only whole `{version}`, `{commit}` and `{env}` tokens.
  - `run_cmd(argv, cwd, timeout) -> {"rc", "out_tail", "err_tail", "timed_out"}`. It uses `start_new_session` and kills the process group on timeout, as conductor's `_run_verify` does.

- [ ] **Step 1: Write the failing tests** in `test_release_state.py`:

```python
import json, os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release as RL
import contract_check as CC
from release_testkit import repo, write_recipe, GIT

GOOD = {"build": ["true"], "deploy_staging": ["true"], "deploy_prod": ["true"],
        "rollback": ["true", "{version}"], "health": ["true"],
        "version_probe": ["cat", "probe-{env}.txt"], "staging_checks": [["true"]],
        "prod_smoke": [["true"]], "version": {"file": "package.json", "key": "version"},
        "bump": "patch", "artifact": "rebuild", "deploy_timeout": 5, "verify_skill": ".claude/skills/verify-app"}

class RecipeTests(unittest.TestCase):
    def test_a_good_recipe_has_no_problems(self):
        root = repo(); write_recipe(root, GOOD)
        r, problems = RL.load_recipe(root)
        self.assertEqual(problems, []); self.assertEqual(r["bump"], "patch")

    def test_shells_strings_and_unknown_tokens_are_refused(self):
        for key, bad in (("deploy_prod", "vercel --prod"), ("deploy_prod", ["bash", "-c", "x"]),
                         ("rollback", ["env", "sh", "-c", "x"]), ("health", ["curl", "{home}"]),
                         ("staging_checks", ["true"]), ("bump", "huge"), ("deploy_timeout", 0)):
            with self.subTest(key=key, bad=bad):
                root = repo(); write_recipe(root, dict(GOOD, **{key: bad}))
                self.assertTrue(RL.load_recipe(root)[1])

    def test_recipe_at_a_commit_is_read_from_git_not_the_working_tree(self):
        root = repo(); write_recipe(root, GOOD, commit=True)
        sha = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        write_recipe(root, dict(GOOD, deploy_prod=["false"]))         # uncommitted edit
        self.assertEqual(RL.load_recipe(root, rev=sha)[0]["deploy_prod"], ["true"])
        self.assertNotEqual(RL.recipe_sha(root, rev=sha), RL.recipe_sha(root))
        write_recipe(root, GOOD)                                       # back to the committed bytes
        self.assertEqual(RL.recipe_sha(root, rev=sha), CC.sha256_file(os.path.join(root, ".release", "recipe.json")))

class StateTests(unittest.TestCase):
    def test_new_save_load_and_append_only_log(self):
        root = repo(); r = RL.Release.new(root, "1.2.0", "abc")
        r.set_status("prepped"); r.save(); r.log("prep", ok=True)
        r2 = RL.Release.load(root, "1.2.0")
        self.assertEqual(r2.status, "prepped")
        self.assertEqual([e["event"] for e in r2.events()][-1], "prep")

    def test_an_unknown_state_is_refused(self):
        r = RL.Release.new(repo(), "1.2.0", "abc")
        with self.assertRaises(ValueError):
            r.set_status("shipped")

    def test_the_lock_is_exclusive(self):
        root = repo()
        with RL.run_lock(root):
            code = ("import sys;sys.path.insert(0,%r);import release as RL;RL.LOCK_TIMEOUT=0.3\n"
                    "try:\n with RL.run_lock(%r): print('got')\nexcept RL.Locked: print('locked')"
                    % (os.path.dirname(os.path.abspath(RL.__file__)), root))
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), "locked")

    def test_run_cmd_kills_the_process_group_on_timeout(self):
        res = RL.run_cmd([sys.executable, "-c", "import time;time.sleep(30)"], tempfile.mkdtemp(), 0.5)
        self.assertTrue(res["timed_out"])

if __name__ == "__main__":
    unittest.main()
```

`release_testkit.py` provides:
- `repo()`: a temp git repo on `main` with `package.json` holding `{"version": "1.1.0"}`, committed with `GIT` env, and with `.skill-contract/` git-ignored;
- `write_recipe(root, recipe, commit=False)`;
- `GIT`: the identity env;
- `tmpdir()`, which registers cleanup with `atexit`, like `conductor_testkit`.

- [ ] **Step 2: Run the tests.** Expect `ModuleNotFoundError: release`.

- [ ] **Step 3: Implement** `release.py`:
  - **The state file.** `Release` keeps the fields `version`, `base_commit`, `status`, `release_commit`, `recipe_sha`, `artifact_sha`, `rollback_target`, `approved_by` and `evidence`. Save it the way factory-conductor's `State.save` does: a temp file, then `os.replace`, then `fsync` the directory.
  - **The log.** `log()` appends one JSON line per event, as factory-conductor's `State.log` does.
  - **Recipe validation** checks each argv with `CC.argv_problems(argv, {"version", "commit", "env"})` (Task 1). Do not duplicate the shell and launcher logic.
  - **The lock and process helpers** are copied from factory-conductor, as the interface block above says.

  Every function carries a docstring. Use `CC` (the vendored `contract_check`) for `sha256_file` and the envelope writers.

- [ ] **Step 4: Run** `python3 release-conductor/assets/test_release_state.py`. Expected: OK.

- [ ] **Step 5: Commit**: `feat(release-conductor): release state, log, lock and recipe validation`.

---

### Task 3: `release init` and `release prep`

**Files:** Modify `release.py`. Test: `release-conductor/assets/test_release_prep.py`.

**Interfaces:**
- Consumes the Task 1 checker and the Task 2 `Release`/recipe API.
- Produces:
  - **`init --root R --answers FILE`** writes `.release/recipe.json` from a JSON answers file, validated, and prints `RELEASE: recipe written`. The agent interviews the user and writes the answers file, as `write_grant` works.
  - **`prep --root R --approved-by NAME --driver ID [--bump patch|minor|major] [--policy-file F]`** (`--driver` is the driving agent's id, stored in state so `stage` can refuse evidence recorded by the driver itself) does the following:
    - computes the next version;
    - writes `intent.json` as `{version, base_commit, bump}`;
    - writes the **release grant**, with subjects `[".release/recipe.json", intent rel]`, `release.version`, a 7-day expiry, and `branch_pattern: "release/*"`. Its gate policy grants `local_reversible`, `push_branch`, `open_pr`, `deploy_staging` and `push_tag`, minus whatever the user declined in the policy file. It is human-accepted by `--approved-by`;
    - creates `release/<version>` from the base commit, bumps the version, and writes the changelog section;
    - commits, gates `push_branch` and pushes, then gates `open_pr` and opens the release PR;
    - prints `RELEASE: <version> prepped` and `NEXT: merge the release PR, then run stage`.
  - `changelog_lines(root, since_tag) -> list[str]` is built from `git log --merges --format=%s <tag>..HEAD`, neutralised the way factory-conductor's `code()` helper does it: single line, code-spanned. When there is no tag, it uses every merge.
  - `next_version(cur, bump) -> str`.

- [ ] **Step 1: Write the failing tests:**
  - `next_version("1.1.0", "minor") == "1.2.0"`, and the same for patch and major. A prerelease is refused.
  - `changelog_lines` neutralises a title containing a newline, `@mention` or `Closes #1`. The rendered text has no bare `@` and no new heading.
  - `prep`:
    - **Run against a stubbed push and PR.** Reuse factory-conductor's test technique: a `git` wrapper on PATH, with `--pr-cmd` stubbed through `--pr-cmd '["true"]'`. Mirror the conductor's `--push-cmd` and `--pr-cmd` flags, validated with the same allowlist rules (copied, with a comment).
    - **What it writes:** a release branch whose version file says the new version, the intent file, and a grant that validates with `CC.check_statement` and `CC.grant_violations`.
    - **The gate:** `check_grant(root, "push_branch", subject=".release/recipe.json", worktree=<release branch checkout>)` returns COVERED.
  - `prep` without `--approved-by` exits 2.
  - `prep` while another release under `.skill-contract/releases/` is unfinished exits 2 and names it.
  - `prep` with an invalid recipe exits 2, printing the problems.
  - `prep` with an uncommitted or dirty `.release/recipe.json` exits 2 (`recipe-uncommitted`): the grant MUST pin a digest some commit has.

- [ ] **Step 2: Run them and see them fail.**

- [ ] **Step 3: Implement.** The release branch is made in a worktree at `.skill-contract/releases/<v>/wt-prep`, on `release/<v>`, from the base commit. Every gate call passes `subject=".release/recipe.json"` and `worktree=<that worktree>`.

- [ ] **Step 4: Run** the release test files. Expected: OK.

- [ ] **Step 5: Commit**: `feat(release-conductor): init writes the recipe; prep writes the release grant and opens the release PR`.

---

### Task 4: `release stage`

**Files:** Modify `release.py`. Test: `release-conductor/assets/test_release_stage.py`.

**Interfaces:**
- `stage --root R [--commit SHA]` finds the merged release commit: the newest commit on the default branch whose version file equals the release version. `--commit` overrides it. It then:
  1. Refuses if the commit's version is not the grant's `release.version` (`STOP: version-mismatch`).
  2. Refuses if `recipe_sha(root, rev=commit)` differs from the sha pinned in the grant's subjects. The grant gate reports this as `stale` when the working-tree recipe changed; `stage` also compares the recipe at the pinned commit itself and refuses with reason `recipe-changed`, because a committed edit can leave the working tree matching.
  3. Creates the worktree `release/<v>-stage` at that commit.
  4. Gates `local_reversible` with `worktree=`.
  5. Runs `build` in an isolated checkout of the commit. When `artifact` is `{"path"}`, it records the artifact's sha256.
  6. **Local verification.** It prints `NEXT: dispatch-verifier <commit>` and stops at status `staging_verify`. `stage --evidence --verifier ID` then judges the verify skill's evidence for that commit, using the same predicate as factory-conductor's `evidence_verdict`. Copy that function with a cross-reference comment. The predicate requires a verifier other than the release driver (`--driver` recorded at prep) and every mapped feature recorded at the sha.
  7. Gates `deploy_staging` and runs `deploy_staging`.
  8. Polls the staging `version_probe` (`{env}=staging`) until it reports the version or commit, or until `deploy_timeout` expires.
  9. Runs each `staging_checks` argv and records `{argv, rc, tails}`.
  10. Pushes the tag, depending on the CI config:
      - When CI has no tag triggers: gates `push_tag`, then pushes `v<version>`.
      - When it has tag triggers: the tag is held for `deploy`, and the tool records `tag_deploys: true`.
  11. Sets status `staged` and prints `STAGE: <version> pass <commit>`. Any failure sets `stage_failed` with a reason and exits 3.

- [ ] **Step 1: Write the failing tests:**
  - root on `main` with the stage worktree: gates pass, which proves the `--worktree` wiring;
  - a version mismatch refuses;
  - a recipe changed in a commit after prep refuses, and the edited `deploy_staging` marker file is never written;
  - a build failure gives `stage_failed`;
  - missing or foreign evidence refuses, including evidence whose verifier is the driver;
  - a staging probe that switches after 2 polls passes;
  - a probe that never switches gives `stage_failed`, reason `timeout`;
  - a failing `staging_checks` entry gives `stage_failed`;
  - CI with a tag trigger means no tag is pushed at stage and `tag_deploys` is true;
  - without one, the tag is pushed after a `push_tag` gate.

  Use `release_testkit` helpers to start a local `http.server` in a thread, and a probe argv that `cat`s a file the test rewrites.

- [ ] **Steps 2–5:** run the tests and see them fail, implement, run them again, then commit `feat(release-conductor): stage — verify locally, deploy to staging, check it`.

---

### Task 5: `release deploy`

**Files:** Modify `release.py`. Test: `release-conductor/assets/test_release_deploy.py`.

**Interfaces:** `deploy --root R --approved-by NAME [--unattended]`.

**Refusals, in order** (exit 2 with a reason; exit 3 when the human is needed):
- the status is not `staged` (or `awaiting_deploy`);
- the staging evidence is missing, or not for `release_commit`;
- the recipe at `release_commit` no longer matches the pinned sha;
- the artifact sha differs from the one recorded at stage;
- `check_grant(..., "deploy")` is COVERED (a bug; refuse with `deploy-covered`);
- any live grant's `reentry.agent_cmd` allowlist matches `deploy_prod` or `rollback`: refuse with `allowlist-exposes-prod`, using `allowlist_matches(allowed_tools_str, argv) -> bool`, which is shared with Task 7. It parses the `--allowedTools` value after `agent_cmd`'s `--allowedTools` flag, splits on commas outside parentheses, treats `Bash` and `Bash(*)` as matching everything, and matches `Bash(<glob>)` against `shlex.join(argv)` with `fnmatch`;
- `--unattended`, or no `--approved-by`: set `awaiting_deploy`, print `STOP: waiting-human` and `NEXT: run deploy with the human`, and exit 3.

**When there is no refusal:**
1. Record `rollback_target`: the production `version_probe` output, else the last `release-result/v1`'s version, else `none`.
2. Print the summary:
   - `DEPLOY: <version> <commit>`;
   - the evidence count;
   - the recipe sha;
   - the deploy argv (`shlex.join`);
   - the rollback argv, or the line "no rollback target";
   - the approval, recorded as CLAIMED.
3. Set `deploying`, then save.
4. Run `deploy_prod` in an isolated checkout of `release_commit`. When `tag_deploys` is set, push the tag `v<version>` instead.
5. On rc 0, set `deployed`; otherwise set `prod_failed`.

**Crash recovery.** A status of `deploying` or `rolling_back` becomes `outcome_unknown` only when a command acquires the run lock and finds it (a plain `load` or `status` read MUST NOT demote it, or a read during a live deploy would flip it), and the tool prints `NEXT: verify-prod then ask the human`. **`deploy` never re-runs `deploy_prod` from `outcome_unknown`.**

- [ ] **Step 1: Write the failing tests:**
  - each refusal, including the allowlist matcher cases `Bash`, `Bash(*)`, `Bash(vercel *)` against `vercel deploy --prod`, and `Bash(git *)`, which does not match;
  - an unattended run waits;
  - an attended run runs the stub, which writes a marker; the status is `deployed`; `approved_by` is recorded; the rollback target is taken from the probe;
  - a simulated crash: write `deploying` to the state, then call `deploy` again. The marker count stays at 1 and the status is `outcome_unknown`;
  - when `tag_deploys` is set, the tag is pushed under the yes and not before it;
  - while a subprocess holds the run lock with status `deploying`, `Release.load` and `status` leave the status `deploying`.

- [ ] **Steps 2–5:** run the tests and see them fail, implement, run them again, then commit `feat(release-conductor): deploy — one explicit yes, never unattended, never re-run after a crash`.

---

### Task 6: `release verify-prod`, `rollback`, and `release-result/v1`

**Files:**
- Modify: `release.py`.
- Create: `release-conductor/assets/schemas/release-result.v1.json`.
- Test: `release-conductor/assets/test_release_prod.py`.
- Modify: `docs/skill-contract/SPEC.md`, to register the kind's schema.

**Interfaces:**

`verify-prod --root R`:
1. Poll the production `version_probe` every 2 s, up to `deploy_timeout`.
   - **Pass:** it reports the release version or commit.
   - **Timeout while the probe still reports the rollback target:** `prod_failed`, reason `not-live`.
   - **Any other version:** `prod_failed`, reason `wrong-version`.
2. Run `health`.
3. Run each `prod_smoke` argv.
4. On pass: set `verified`, write `release-result/v1` with `CC.build_statement` and `write_envelope`, and print `PROD: <version> verified`. On failure: set `prod_failed`, print `NEXT: ask the human to roll back`, and exit 3.

The `release-result/v1` envelope:
- **Kind:** `https://github.com/dhanesh/agent-skills/skill-contract/release-result/v1`.
- **Subjects:** the recipe, the intent file and the release log prefix.
- **Payload:**
  - `version` and `commit`;
  - `recipe_sha` and `artifact_sha`;
  - the staging evidence summary;
  - the production probe, health and smoke results, as rc values only, with no output tails;
  - `rollback_target`;
  - `approved_by: {"name", "status": "CLAIMED"}`;
  - `log_sha256` and `log_bytes`.

`rollback --root R --approved-by NAME`:
- It requires `prod_failed` or `outcome_unknown`, plus a rollback target. With no target it prints `ROLLBACK: no target` and exits 2.
- **Unattended, or no `--approved-by`:** it waits, the same way `deploy` does.
- **Otherwise:**
  1. Gate `deploy`, which is expected to ASK. Here the human's yes is what authorises the step; record it.
  2. Set `rolling_back`.
  3. Run `rollback` with `{version}` and `{commit}` set from the target.
  4. Poll the probe for the target version.
  5. On success, set `rolled_back` and write `release-result/v1` with `outcome: "rolled_back"`.

- [ ] **Step 1: Write the failing tests:**
  - an asynchronous switch after 3 polls passes, and no rollback is offered;
  - a timeout with the old version gives `not-live`;
  - a third version gives `wrong-version`;
  - `prod_smoke` runs only its own argv, and `staging_checks` never runs against production (assert on the marker files);
  - the envelope validates with `CC.check_statement`, and its `log_sha256` matches;
  - output tails are absent from the envelope (plant a fake token in stub output, then assert it is absent);
  - rollback without a yes waits;
  - rollback with a yes runs and verifies the target;
  - rollback with no target exits 2.

- [ ] **Steps 2–5:** run the tests and see them fail, implement, run them again, then commit `feat(release-conductor): verify-prod proves the release is live; rollback only with a yes; release-result/v1`.

---

### Task 7: spec-first-planning — release defaults and the production-allowlist refusal

**Files:**
- Modify: `spec-first-planning/assets/write_grant.py`, `spec-first-planning/references/unattended.md`, `spec-first-planning/SKILL.md` (bump the minor version), `spec_to_tasks.py` (`SKILL_VERSION`) and its pin test, plus the README.
- Test: `spec-first-planning/assets/test_write_grant.py`.

**Interfaces:**
- `answers.release_defaults`, which is optional: `{"bump": "patch" | "minor" | "major", "grant_staging": bool, "grant_tag": bool}`. It is written into the grant payload as `release_defaults`. The checker has no payload-key allowlist (verified), so no checker change is needed; document the key in `SPEC.md`. `release prep` reads it to build its policy file.
- `write_grant.py` refuses a grant whose `reentry.agent_cmd` `--allowedTools` value would match the repo's `.release/recipe.json` `deploy_prod` or `rollback`. It uses an `allowlist_matches` copied from release.py, with a cross-reference comment, so that two skills don't import each other.
- `unattended.md` gains a question asking for the release defaults, and states that the release grant itself is written only at `release prep`, with the human present.

- [ ] **Steps:** follow the established pattern: tests first, see them fail, implement, then run `make gate-skill SKILL=spec-first-planning`, then commit `feat(spec-first-planning): unattended interview records release defaults; refuse an allowlist exposing production`.

---

### Task 8: The skill — SKILL.md, references, README, catalog, e2e, eval

**Files:**
- Create: `release-conductor/SKILL.md`, `release-conductor/README.md`, `release-conductor/references/release-protocol.md`, `release-conductor/eval/run_eval.py`, `release-conductor/assets/test_release_e2e.py`.
- Modify: the root `README.md` (catalog row, install line, Software factory section and table: Release/deploy becomes "covered", with limits) and `docs/rfc2119/2026-09-19-classification.md` (a new section).

**Content:**
1. **SKILL.md** uses the repo scaffold (`python3 repo2skill/assets/scaffold_skill.py release-conductor --tags "factory,release,deploy,skill-contract"`). Then:
   - **Frontmatter:** `compatibility` says POSIX and Python 3.10+.
   - **Opening:** the BCP 14 declaration, and the `$SKILL_DIR` block copied from factory-conductor.
   - **When to use:** "release this", "ship it to production", and after a factory PR is merged.
   - **Preconditions:** the recipe exists (else run `init`), the project's verify skill exists, and you are on a clean checkout.
   - **The flow:**
     - one numbered step per command;
     - each `NEXT:` meaning;
     - the verifier dispatch: a verifier other than you, with the brief copied from factory-conductor's verifier brief;
     - the production yes: show the summary verbatim, then ask the human in plain words; you MUST NOT supply `--approved-by` without the human's explicit yes in this session;
     - rollback.
   - **Hard rules,** each with its reason:
     - never put `deploy_prod` or `rollback` argv in any `--allowedTools`;
     - never run `deploy` or `rollback` unattended;
     - never edit `.skill-contract/releases/`;
     - never re-run a deploy after `outcome_unknown`.
   - **Honesty section:**
     - the yes is CLAIMED;
     - a permission prompt is no gate if prompts are bypassed;
     - with `rebuild`, staging verified the same source but not the same bytes;
     - the recipe's commands run with the user's own credentials;
     - nothing triggers `stage` automatically;
     - production checks are only as good as `prod_smoke`;
    - CI tag-trigger detection fails closed, so an unrecognised CI config makes a tag push ask; a false positive only costs one extra ask.
   - **Contract block:** it consumes `autonomy-grant/v1` and provides `release-result/v1`.
2. **`references/release-protocol.md`:**
   - every command, line and exit code;
   - the recipe schema;
   - the states;
   - the grant shape and selection;
   - the CI-tag rule;
   - a worked example.
3. **E2E (`test_release_e2e.py`):**
   - a temp repo with a local `http.server` serving `/version`, and a stub recipe whose `deploy_*` commands rewrite the served version file;
   - a stub verify skill made from verification-skill-forge's fixture app. If that's too heavy, write evidence records directly with the vendored recorder copied from `verification-skill-forge/assets/verify_evidence.py`;
   - the run goes `init` → `prep` → simulated human merge (fast-forward of the release branch to main) → `stage` (verifier dispatch is simulated by recording evidence as a different verifier) → `deploy --approved-by Dana` → `verify-prod`, then checks that `release-result/v1` validates;
   - a second run with a failing prod smoke goes `rollback --approved-by Dana` → `rolled_back`.
4. **Eval NEGATIVE checks** (spec AC3, with the D11 amendments):
   - no production deploy without same-commit staging evidence;
   - none with a changed recipe;
   - none unattended;
   - a tag push asks when CI has tag triggers;
   - `verify-prod` runs only `prod_smoke`;
   - a version mismatch fails;
   - rollback asks;
   - a crash in `deploying` never re-runs the deploy;
   - an allowlist exposing production is refused;
   - `stage` refuses a version mismatch;

   plus one positive: a full attended release reaches `verified`. The eval MUST stay under 120 s, and every timer or env override goes to temp directories.
5. **Catalog, register and adoption:**
   - the BCP 14 register section for release-conductor;
   - `make contract-vendor`, so release-conductor becomes an adopter;
   - `make readme`.

- [ ] **Steps:** run `make gate-skill SKILL=release-conductor`, `make bcp14`, `make playbook` and `make readme`, then commit `feat(release-conductor): the skill — protocol, honesty, e2e and eval`.

---

### Task 9: A/B rows, the full gate and the CI-equivalent run

**Files:**
- Modify: `scripts/ab-validate.py` (`SINCE_RELEASE`, set to Task 2's commit, and `check_release_conductor`).
- Modify: `docs/factory/2026-09-19-assessment.md` (a step 5A bullet, to be re-judged after merge).

**Rows:**
- **Delta row:** "releases reaching verified production with only the human's production yes". The old tree has no release-conductor and scores an honest 0; the new tree scores 1, driving the e2e flow with stubs.
- **Guards**, each mutation-proven like the re-entry guards (delete the guard's own refusal and the guard reads 1):
  - deploy without staging evidence;
  - deploy with a changed recipe;
  - deploy unattended;
  - a CI-tag push without the yes;
  - re-run after `outcome_unknown`.

  Each guard's fixture must pass every other check.

- [ ] **Steps:**
  1. Add the rows and run the mutants. Record the results.
  2. Run `make gate` and `make ab-validate` (0 WORSE, 0 UNPROVEN) and `make readme`.
  3. Run the CI-equivalent run natively if it is possible: `docker run --rm -u 1000:1000 -e HOME=/tmp -v "$PWD":/src:ro nikolaik/python-nodejs:python3.12-nodejs22 bash -c 'cp -r /src /tmp/w && cd /tmp/w && make gate'`. Under amd64 emulation, record that the eval time limit may be exceeded, as before.
  4. Commit: `test(ab-validate): release-conductor rows; assessment status`.

---

## Self-review notes

- **Spec coverage:**

  | Spec item | Task(s) |
  |---|---|
  | D1–D8 | 1–8 |
  | D9 | 1 |
  | D10 | 3, 7 |
  | D11 | 4, 6 |
  | §1 recipe | 2 |
  | §2 prep, stage, deploy, verify-prod | 3, 4, 5, 6 |
  | §2 checker pieces | 1 |
  | §2 `write_grant` | 7 |
  | §3 evidence, failures and honesty | 4, 5, 6, 8 |
  | AC1 | 1 |
  | AC2 | 2–6 |
  | AC3 | 8 |
  | AC4 | 8 |
  | AC5 | 9 |
  | AC6, amended by D10 | 7 |
  | AC7 | 8, 9 |
  | AC8 | after merge, by the controller |

- **Names across tasks:**
  - `CC.grant_for_subject`, `CC.revoke_all`, `CC.ci_tag_triggers`, `CC.worktree_ok`, `check_grant(worktree=)`;
  - `RL.Release`, `RL.load_recipe`, `RL.recipe_sha`, `RL.run_lock`, `RL.Locked`, `RL.run_cmd`, `RL.expand`, `RL.next_version`, `RL.changelog_lines`, `RL.allowlist_matches`, `RL.STATES`.
- **Copied code, each copy marked with a cross-reference comment:**
  - `run_lock` and `run_cmd`, from factory-conductor;
  - `evidence_verdict`, from factory-conductor;
  - the push and PR allowlist, from factory-conductor;
  - `allowlist_matches`, from release.py into `write_grant.py`;
  - the evidence recorder, from verification-skill-forge, used in tests only.
