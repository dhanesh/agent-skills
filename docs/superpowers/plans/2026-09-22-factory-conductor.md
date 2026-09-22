# Factory Conductor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `factory-conductor` skill that carries a task plan from its first task to an open pull request inside a user-approved autonomy grant, merging only tasks whose own verify commands the conductor re-ran itself.

**Architecture:** A prompt skill plus one stdlib state machine. `assets/conductor.py` owns the run: waves from `depends_on`, git worktrees per task, re-running verify commands, merges, the append-only log, budgets, stop rules and every `check-grant` gate. The session dispatches a fresh executor subagent per task and a reviewer subagent per result; it never decides anything the tool can decide.

**Tech Stack:** Python 3.10+ stdlib only, git, POSIX sh gates, `make`.

**Spec:** `docs/superpowers/specs/2026-09-22-factory-conductor-design.md` (read §1–§8 before any task).

## Global Constraints

- Branch `feat/factory-conductor`. No push, no amend, no subagents inside a task.
- **Stdlib only.** No pip, no network. `conductor.py` imports the vendored `contract_check.py` from its own directory, the way `spec_to_tasks.py` does (a lazy import after a `sys.version_info < (3, 10)` guard).
- **TDD:** write the failing test first, run it, then implement.
- Tests are `assets/test_*.py` (unittest). The eval is `eval/run_eval.py`: model-free, offline, negative fixtures mandatory, under 60s, no repo writes.
- **Never weaken an existing check.** If a fixture must change, keep the check at least as strict and say why in the commit.
- **Run directory:** `<root>/.skill-contract/runs/<run-id>/`, holding `state.json`, `autonomy-log.jsonl` and `wt/<task>/` worktrees. It MUST be git-ignored; add `.skill-contract/runs/` to the repo `.gitignore` in Task 1.
- **Run id grammar:** `run-<yyyymmddThhmmssZ>-<6 hex>`.
- **Action classes (verbatim):** `read_only`, `local_reversible`, `push_branch`, `open_pr`, `merge`, `deploy`, `spend`, `external_message`, `delete`. A8: the last five are never grantable.
- **Gate rule:** every consequential action runs `check-grant --root <root> --action <class>` through the vendored checker and proceeds ONLY on exit 0.
- **Task statuses (verbatim):** `pending`, `running`, `verifying`, `reviewing`, `proven`, `parked`, `blocked`.
- **Stop reasons (verbatim):** `budget_wall_clock`, `budget_dispatches`, `grant_ask`, `new_human_decision`, `verify_red_after_repairs`, `no_ready_tasks`.
- **Budgets:** `wall_clock_min`, `max_dispatches`, `max_repairs_per_task` and `max_parallel` (default 2) are enforced. `max_tokens` and `max_usd` are recorded and reported, never enforced; every place that mentions them MUST say so.
- **Machine output lines** (one per event, parsed by tests): `RUN:`, `READY:`, `START:`, `VERIFY:`, `REVIEW:`, `MERGE:`, `PARK:`, `GATE:`, `STOP:`, `FINISH:`, `STATUS:`.
- **Exit codes:** 0 success; 2 usage or invalid input; 3 the run must stop (a gate said ASK, or a stop rule fired).
- **BCP 14:** SKILL.md carries the one-line declaration, passes PP-7, keeps PP-5 non-advisory, and every hard rule gets a row in `docs/rfc2119/2026-09-19-classification.md`.
- Every commit message ends with exactly:
```
Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01X7cbN5bQsCx5HyMdb8Lo7X
```

## File map

| File | Responsibility | Task |
|---|---|---|
| `factory-conductor/assets/conductor.py` | the whole state machine and CLI | 1–4 |
| `factory-conductor/assets/test_conductor_state.py` | scheduling, state, log, resume | 1 |
| `factory-conductor/assets/test_conductor_git.py` | worktrees, verify re-runs, merge, park | 2 |
| `factory-conductor/assets/test_conductor_policy.py` | gates, budgets, stop rules | 3 |
| `factory-conductor/assets/test_conductor_finish.py` | the run-result envelope, push and PR | 4 |
| `factory-conductor/assets/contract_check.py` | vendored, byte-identical | 6 |
| `factory-conductor/SKILL.md`, `README.md`, `references/run-protocol.md` | the run protocol and the dispatch briefs | 6 |
| `factory-conductor/eval/run_eval.py` | the end-to-end eval with negatives | 7 |
| `spec-first-planning/assets/spec_lint.py`, `spec_to_tasks.py`, `schemas/task-plan.v1.json`, `references/spec-format.md` | the `[after: …]` hint, `depends_on`, `--waves` | 5 |
| `docs/skill-contract/SPEC.md` | register `run-result/v1` | 4 |
| `scripts/ab-validate.py`, `README.md`, `docs/factory/2026-09-19-assessment.md` | A/B rows and status | 8 |

---

### Task 1: conductor.py — state, waves, log, resume

**Files:**
- Create: `factory-conductor/assets/conductor.py`, `factory-conductor/assets/test_conductor_state.py`
- Modify: `.gitignore` (add `.skill-contract/runs/`)

**Interfaces:**
- Produces:
  - `RUN_ID_RE`, `STATUSES`, `STOP_REASONS`
  - `waves(tasks) -> list[list[str]]`, where `tasks` is `{id: {"depends_on": [...]}}`; raises `PlanError` on a cycle or an unknown dependency
  - `new_run_id(now=None) -> str`
  - `run_dir(root, run_id) -> str`
  - `State` with `load(path)`, `save()`, `log(event, **fields)`, `ready(max_parallel)`, `set_status(task, status, **fields)`
  - `cmd_init(args)`, `cmd_next(args)`, `cmd_status(args)`, `cmd_resume(args)`
  - `class PlanError(Exception)`
- The log is `autonomy-log.jsonl`: one JSON object per line, each with `at` (RFC 3339 UTC) and `event`. It is opened with `"a"` and never rewritten.

- [ ] **Step 1: Write the failing tests** in `test_conductor_state.py`:

```python
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C


def plan(tasks):
    """A minimal task-plan payload: {id: depends_on}."""
    return {"title": "T", "spec": "docs/spec.md", "coverage": {}, "uncovered": [],
            "tasks": [{"id": i, "requirement_ids": ["R1"], "title": i,
                       "verify": [{"text": "t", "command": ["true"]}],
                       "depends_on": d} for i, d in tasks.items()]}


class Waves(unittest.TestCase):
    def test_no_dependencies_is_one_wave(self):
        self.assertEqual(C.waves({"T1": {"depends_on": []}, "T2": {"depends_on": []}}),
                         [["T1", "T2"]])

    def test_chain_is_three_waves(self):
        t = {"T1": {"depends_on": []}, "T2": {"depends_on": ["T1"]}, "T3": {"depends_on": ["T2"]}}
        self.assertEqual(C.waves(t), [["T1"], ["T2"], ["T3"]])

    def test_diamond_groups_the_middle(self):
        t = {"T1": {"depends_on": []}, "T2": {"depends_on": ["T1"]},
             "T3": {"depends_on": ["T1"]}, "T4": {"depends_on": ["T2", "T3"]}}
        self.assertEqual(C.waves(t), [["T1"], ["T2", "T3"], ["T4"]])

    def test_cycle_raises(self):
        with self.assertRaises(C.PlanError) as e:
            C.waves({"T1": {"depends_on": ["T2"]}, "T2": {"depends_on": ["T1"]}})
        self.assertIn("cycle", str(e.exception))

    def test_unknown_dependency_raises(self):
        with self.assertRaises(C.PlanError) as e:
            C.waves({"T1": {"depends_on": ["T9"]}})
        self.assertIn("T9", str(e.exception))


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.st = C.State.new(root=self.tmp, run_id="run-20260922T120000Z-a1b2c3",
                              plan=plan({"T1": [], "T2": ["T1"]}), plan_envelope="e.json",
                              plan_sha256="0" * 64, grant_id="autonomy-grant-v1-x",
                              run_branch="factory/p", base_branch="main",
                              budget={"max_parallel": 2})

    def test_ready_respects_dependencies_and_parallelism(self):
        self.assertEqual(self.st.ready(2), ["T1"])
        self.st.set_status("T1", "proven")
        self.assertEqual(self.st.ready(2), ["T2"])

    def test_ready_is_capped_by_max_parallel(self):
        st = C.State.new(root=self.tmp, run_id="run-20260922T120001Z-a1b2c4",
                         plan=plan({"T1": [], "T2": [], "T3": []}), plan_envelope="e.json",
                         plan_sha256="0" * 64, grant_id="g", run_branch="factory/p",
                         base_branch="main", budget={"max_parallel": 2})
        self.assertEqual(len(st.ready(2)), 2)

    def test_running_tasks_count_against_parallelism(self):
        st = C.State.new(root=self.tmp, run_id="run-20260922T120002Z-a1b2c5",
                         plan=plan({"T1": [], "T2": [], "T3": []}), plan_envelope="e.json",
                         plan_sha256="0" * 64, grant_id="g", run_branch="factory/p",
                         base_branch="main", budget={"max_parallel": 2})
        st.set_status("T1", "running")
        self.assertEqual(st.ready(2), ["T2"])

    def test_dependents_of_a_parked_task_are_blocked(self):
        self.st.set_status("T1", "parked", park_reason="needs a decision")
        self.assertEqual(self.st.ready(2), [])
        self.assertEqual(self.st.tasks["T2"]["status"], "blocked")

    def test_log_is_append_only_and_one_object_per_line(self):
        self.st.log("init", run="x")
        self.st.log("start", task="T1")
        lines = open(self.st.log_path, encoding="utf-8").read().strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual([json.loads(l)["event"] for l in lines], ["init", "start"])
        self.assertTrue(all(json.loads(l)["at"].endswith("Z") for l in lines))

    def test_save_and_load_round_trip(self):
        self.st.set_status("T1", "running")
        self.st.save()
        again = C.State.load(self.st.state_path)
        self.assertEqual(again.tasks["T1"]["status"], "running")
        self.assertEqual(again.run_id, self.st.run_id)

    def test_resume_reports_the_last_step_and_keeps_the_log(self):
        self.st.log("start", task="T1")
        self.st.set_status("T1", "running")
        self.st.save()
        before = open(self.st.log_path, encoding="utf-8").read()
        rc = C.main(["resume", "--root", self.tmp])
        self.assertEqual(rc, 0)
        self.assertTrue(open(self.st.log_path, encoding="utf-8").read().startswith(before))


class RunIdTests(unittest.TestCase):
    def test_grammar(self):
        self.assertRegex(C.new_run_id(), C.RUN_ID_RE)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and see them fail.**
  - Run: `python3 factory-conductor/assets/test_conductor_state.py 2>&1 | tail -3`
  - Expected: `ModuleNotFoundError: No module named 'conductor'`.

- [ ] **Step 3: Implement the core** in `conductor.py`. Head the file like `spec_to_tasks.py`: a docstring with the usage block, the `sys.version_info < (3, 10)` guard, then the imports.

```python
RUN_ID_RE = r"^run-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}\Z"
STATUSES = ("pending", "running", "verifying", "reviewing", "proven", "parked", "blocked")
STOP_REASONS = ("budget_wall_clock", "budget_dispatches", "grant_ask",
                "new_human_decision", "verify_red_after_repairs", "no_ready_tasks")
DEFAULT_PARALLEL = 2


class PlanError(Exception):
    """The plan cannot be scheduled: a cycle, or a dependency that does not exist."""


def waves(tasks):
    """Group task ids into waves. Every id in a wave is independent of the others."""
    unknown = {d for t in tasks.values() for d in t.get("depends_on") or []} - set(tasks)
    if unknown:
        raise PlanError("depends_on names unknown task(s): %s" % ", ".join(sorted(unknown)))
    left = {k: set(v.get("depends_on") or []) for k, v in tasks.items()}
    out = []
    while left:
        wave = sorted(k for k, deps in left.items() if not deps)
        if not wave:
            raise PlanError("depends_on has a cycle among: %s" % ", ".join(sorted(left)))
        out.append(wave)
        for k in wave:
            del left[k]
        for deps in left.values():
            deps.difference_update(wave)
    return out
```

  `State` carries `root`, `run_id`, `plan_envelope`, `plan_sha256`, `grant_id`, `run_branch`, `base_branch`, `created_at`, `budget`, `dispatches`, `stopped` and `tasks`. Each task holds `status`, `depends_on`, `verify` (copied from the plan), `repairs`, `branch`, `worktree`, `verify_runs`, `review`, `merge_commit` and `park_reason`.
  - `State.new(...)` creates the run directory, writes `state.json`, and opens the log.
  - `save()` writes `state.json` atomically (a temp file in the same directory, then `os.replace`).
  - `log(event, **fields)` appends one JSON object with `at` and `event`.
  - `set_status(task, status, **fields)` validates the status, applies the fields, and when a task becomes `parked` marks every transitive dependent `blocked`.
  - `ready(max_parallel)` returns pending tasks whose dependencies are all `proven`, capped so that `running + verifying + reviewing + returned <= max_parallel`.
  - `State.load(path)` rebuilds from `state.json`; `run_dir(root, run_id)` joins `.skill-contract/runs/<run-id>`; `current_run(root)` returns the newest run directory.
  - `cmd_init` is written in Task 3 once gating exists; for now it builds state from a plan payload file and prints `RUN: <id>`.
  - `cmd_next` prints `READY: T1 T2` (or nothing) and returns 0; `cmd_status` prints one `STATUS:` line per task; `cmd_resume` prints the last logged event and the ready set, and returns 0.

- [ ] **Step 4: Run the tests and see them pass.**
  - Run: `python3 factory-conductor/assets/test_conductor_state.py`
  - Expected: OK.

- [ ] **Step 5: Ignore the run directory.** Add `.skill-contract/runs/` to `.gitignore`, under a comment saying it holds conductor run state and worktrees.

- [ ] **Step 6: Commit.**

```bash
git add factory-conductor/assets/conductor.py factory-conductor/assets/test_conductor_state.py .gitignore
git commit -m "feat(factory-conductor): run state, wave scheduling and the append-only log"
```

---

### Task 2: conductor.py — worktrees, verify re-runs, merge and park

**Files:**
- Modify: `factory-conductor/assets/conductor.py`
- Create: `factory-conductor/assets/test_conductor_git.py`

**Interfaces:**
- Consumes: `State`, `waves`, `run_dir` from Task 1.
- Produces:
  - `git(root, *args) -> subprocess.CompletedProcess | None`, which scrubs `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_COMMON_DIR` and `GIT_CEILING_DIRECTORIES`
  - `cmd_start(args)`: creates `<run>/wt/<task>` on branch `<run_branch>/<task>` from the run branch; prints `START: <task> <worktree>`
  - `cmd_verify(args)`: re-runs every verify command for the task in its worktree; prints one `VERIFY: <task> <ok|fail> <command…>` line per command, then `VERIFY: <task> pass|fail`
  - `cmd_review(args)`: records `--verdict pass|fail` and `--detail`; prints `REVIEW: <task> <verdict>`
  - `cmd_merge(args)`: merges the task branch into the run branch with `--no-ff`; prints `MERGE: <task> <sha>`; on conflict runs `git merge --abort`, parks the task and prints `PARK: <task> merge-conflict`
  - `cmd_park(args)`: prints `PARK: <task> <reason>`
- **Rule:** a task whose verify list has any `command: null` cannot be proven mechanically. `cmd_verify` parks it with the reason `unrunnable-verify` and returns 3.
- Commands are resolved with the vendored checker's `resolve_command`, `resolve_python` and `skill_index`, so `{python}` and `{skill_dir:<name>}` work exactly as in an envelope.

- [ ] **Step 1: Write the failing tests** in `test_conductor_git.py`. Each test builds a real git repo in a temp dir:

```python
import os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C

GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def repo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q", "-b", "main", d], check=True)
    open(os.path.join(d, "a.txt"), "w").write("a\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-q", "-m", "x"], check=True,
                   env=dict(os.environ, **GIT))
    subprocess.run(["git", "-C", d, "checkout", "-q", "-b", "factory/p"], check=True)
    return d


@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class GitTests(unittest.TestCase):
    def state(self, verify=None):
        self.root = repo()
        tasks = {"title": "T", "spec": "s.md", "coverage": {}, "uncovered": [],
                 "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "one",
                            "verify": verify or [{"text": "t", "command": ["true"]}],
                            "depends_on": []}]}
        return C.State.new(root=self.root, run_id=C.new_run_id(), plan=tasks,
                           plan_envelope="e.json", plan_sha256="0" * 64, grant_id="g",
                           run_branch="factory/p", base_branch="main", budget={})

    def test_start_creates_a_worktree_on_a_task_branch(self):
        st = self.state()
        rc = C.main(["start", "T1", "--root", self.root])
        self.assertEqual(rc, 0)
        st = C.State.load(st.state_path)
        self.assertTrue(os.path.isdir(st.tasks["T1"]["worktree"]))
        r = C.git(st.tasks["T1"]["worktree"], "rev-parse", "--abbrev-ref", "HEAD")
        self.assertEqual(r.stdout.strip(), "factory/p/T1")

    def test_verify_passes_and_records_each_command(self):
        st = self.state(verify=[{"text": "true", "command": ["true"]},
                                {"text": "echo", "command": ["echo", "hi"]}])
        C.main(["start", "T1", "--root", self.root])
        rc = C.main(["verify", "T1", "--root", self.root])
        self.assertEqual(rc, 0)
        st = C.State.load(st.state_path)
        self.assertEqual(len(st.tasks["T1"]["verify_runs"]), 2)
        self.assertTrue(all(v["ok"] for v in st.tasks["T1"]["verify_runs"]))

    def test_verify_fails_when_a_command_fails(self):
        st = self.state(verify=[{"text": "false", "command": ["false"]}])
        C.main(["start", "T1", "--root", self.root])
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)
        st = C.State.load(st.state_path)
        self.assertFalse(st.tasks["T1"]["verify_runs"][0]["ok"])

    def test_a_task_with_an_unrunnable_verify_is_parked(self):
        st = self.state(verify=[{"text": "by hand", "command": None}])
        C.main(["start", "T1", "--root", self.root])
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)
        st = C.State.load(st.state_path)
        self.assertEqual(st.tasks["T1"]["status"], "parked")
        self.assertEqual(st.tasks["T1"]["park_reason"], "unrunnable-verify")

    def test_merge_brings_the_task_branch_into_the_run_branch(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        st = C.State.load(st.state_path)
        wt = st.tasks["T1"]["worktree"]
        open(os.path.join(wt, "b.txt"), "w").write("b\n")
        C.git(wt, "add", "-A")
        subprocess.run(["git", "-C", wt, "commit", "-q", "-m", "task"], check=True,
                       env=dict(os.environ, **GIT))
        C.main(["verify", "T1", "--root", self.root])
        C.main(["review", "T1", "--verdict", "pass", "--root", self.root])
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 0)
        st = C.State.load(st.state_path)
        self.assertEqual(st.tasks["T1"]["status"], "proven")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "b.txt")))
        self.assertFalse(os.path.isdir(wt))

    def test_merge_refuses_before_verify_and_review(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 2)

    def test_a_conflicting_merge_parks_the_task_and_leaves_the_run_branch_clean(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        st = C.State.load(st.state_path)
        wt = st.tasks["T1"]["worktree"]
        open(os.path.join(wt, "a.txt"), "w").write("task\n")
        C.git(wt, "add", "-A")
        subprocess.run(["git", "-C", wt, "commit", "-q", "-m", "task"], check=True,
                       env=dict(os.environ, **GIT))
        open(os.path.join(self.root, "a.txt"), "w").write("run\n")
        C.git(self.root, "add", "-A")
        subprocess.run(["git", "-C", self.root, "commit", "-q", "-m", "run"], check=True,
                       env=dict(os.environ, **GIT))
        C.main(["verify", "T1", "--root", self.root])
        C.main(["review", "T1", "--verdict", "pass", "--root", self.root])
        self.assertEqual(C.main(["merge", "T1", "--root", self.root]), 3)
        st = C.State.load(st.state_path)
        self.assertEqual((st.tasks["T1"]["status"], st.tasks["T1"]["park_reason"]),
                         ("parked", "merge-conflict"))
        self.assertEqual(C.git(self.root, "status", "--porcelain").stdout.strip(), "")

    def test_a_failed_review_leaves_the_task_for_repair(self):
        st = self.state()
        C.main(["start", "T1", "--root", self.root])
        C.main(["verify", "T1", "--root", self.root])
        self.assertEqual(C.main(["review", "T1", "--verdict", "fail",
                                 "--detail", "out of scope", "--root", self.root]), 3)
        st = C.State.load(st.state_path)
        self.assertEqual(st.tasks["T1"]["review"]["verdict"], "fail")
        self.assertNotEqual(st.tasks["T1"]["status"], "proven")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run them and see them fail.**
  - Run: `python3 factory-conductor/assets/test_conductor_git.py 2>&1 | tail -3`
  - Expected: failures naming `start`, `verify`, `review` and `merge`.

- [ ] **Step 3: Implement.**

```python
GIT_SCRUB = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
             "GIT_CEILING_DIRECTORIES")


def git_env():
    env = dict(os.environ)
    for k in GIT_SCRUB:
        env.pop(k, None)
    return env


def git(root, *args, check=False):
    """Run git in `root`. Returns the CompletedProcess, or None when git is unusable."""
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                           timeout=300, env=git_env())
    except (OSError, subprocess.SubprocessError):
        return None
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r
```

  - `cmd_start`: refuse unless the task is `pending` and ready (exit 2). Run `git worktree add <run>/wt/<task> -b <run_branch>/<task> <run_branch>`. Record `worktree` and `branch`, set the status `running`, count a dispatch, log `start`, print `START: …`.
  - `cmd_verify`: require `running` or `verifying`. If any step's `command` is null, park with `unrunnable-verify` and return 3. Otherwise resolve each command through the vendored checker's `resolve_command(cmd, resolve_python(env), skill_index(cwd=root))`, run it in the worktree with a 600s timeout, and record `{command, ok, returncode, stdout_tail, stderr_tail}` (the last 2000 characters of each stream). All pass → status `reviewing`, print `VERIFY: <task> pass`, return 0. Any fail → status `verifying`, print `VERIFY: <task> fail`, return 3.
  - `cmd_review`: require `reviewing`. Record the verdict and detail. `pass` keeps `reviewing` and returns 0; `fail` returns 3.
  - `cmd_merge`: require `reviewing` with a passing verify and a passing review, else exit 2. Run `git merge --no-ff <branch> -m "conductor: <task>"` in the run branch. On failure run `git merge --abort`, park with `merge-conflict`, return 3. On success record `merge_commit` from `git rev-parse HEAD`, set `proven`, run `git worktree remove --force <wt>`, delete the task branch, print `MERGE: …`, return 0.
  - `cmd_park`: set `parked` with the given reason, print `PARK: …`, return 0.

- [ ] **Step 4: Run the tests and see them pass.**
  - Run: `python3 factory-conductor/assets/test_conductor_git.py`
  - Expected: OK.

- [ ] **Step 5: Commit.**

```bash
git add factory-conductor/assets
git commit -m "feat(factory-conductor): worktrees, verify re-runs, review and merge"
```

---

### Task 3: conductor.py — gates, budgets and stop rules

**Files:**
- Modify: `factory-conductor/assets/conductor.py`
- Create: `factory-conductor/assets/test_conductor_policy.py`
- Copy in: `factory-conductor/assets/contract_check.py` (via `make contract-vendor` in Task 6; for now, copy the reference file so the imports resolve and note it in the commit message)

**Interfaces:**
- Produces:
  - `gate(root, action) -> tuple[int, str]`, which shells out to the vendored `contract_check.py check-grant --root <root> --action <action>` and returns `(exit code, last line)`
  - `cmd_gate(args)`: prints `GATE: COVERED …` and returns 0, or `GATE: ASK <reason>` and returns 3
  - `check_stop(state) -> str | None`, which returns a stop reason or None
  - `cmd_init(args)`: full version — validates the plan envelope, requires `gate(root, "local_reversible")` to be COVERED, creates the run branch, writes state, prints `RUN: <id>`
  - Every command that changes the repository calls `gate` first and returns 3 on ASK.
- `cmd_init` takes `--plan <envelope path>`, `--root <repo>` and optional `--budget <json>`; the budget merges over the grant's `budget` payload, and `max_parallel` defaults to 2.

- [ ] **Step 1: Write the failing tests** in `test_conductor_policy.py`:

```python
import json, os, subprocess, sys, tempfile, time, unittest
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C
import contract_check as CC

GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _z(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def repo_with_plan():
    """A git repo on factory/p holding docs/spec.md and plan.json, plus a task-plan envelope."""
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q", "-b", "main", d], check=True)
    os.makedirs(os.path.join(d, "docs"))
    open(os.path.join(d, "docs", "spec.md"), "w").write("# Spec\n")
    open(os.path.join(d, "plan.json"), "w").write('{"plan": 1}\n')
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-q", "-m", "x"], check=True,
                   env=dict(os.environ, **GIT))
    subprocess.run(["git", "-C", d, "checkout", "-q", "-b", "factory/p"], check=True)
    return d


def payload(tasks):
    return {"title": "T", "spec": "docs/spec.md", "coverage": {}, "uncovered": [],
            "tasks": [{"id": i, "requirement_ids": ["R1"], "title": i,
                       "verify": v, "depends_on": dep} for i, (dep, v) in tasks.items()]}


def write_plan_envelope(root, tasks):
    st = CC.build_statement(
        "https://github.com/dhanesh/agent-skills/skill-contract/task-plan/v1",
        "spec-first-planning", "2.1.0", root, ["docs/spec.md"], payload(tasks))
    return CC.write_envelope(root, st)


def write_grant(root, policy=None, minutes=60, now=None):
    """A human-accepted grant pinning the spec and plan.json. A7 caps the lifetime at 7 days."""
    now = now or CC.utc_now()
    pay = {"scope": {"repo": ".", "branch_pattern": "factory/*"},
           "decisions": [{"id": "D1", "question": "deps?", "answer": "no", "source": "sweep"}],
           "defaults": [], "gate_policy": policy or {"read_only": "auto",
                                                     "local_reversible": "grant",
                                                     "push_branch": "grant",
                                                     "open_pr": "grant"},
           "budget": {}, "stop_on": [], "expires_at": _z(now + timedelta(minutes=minutes)),
           "system_one": {"allowed": False}, "revoked": False}
    a = {"test": "grant-accepted", "assertedBy": {"human": "Dana"},
         "result": {"outcome": "passed"},
         "command": ["{python}", "{skill_dir:spec-first-planning}/assets/spec_lint.py",
                     "--unattended", "docs/spec.md"],
         "subject": [CC.pin(root, "docs/spec.md")]}
    st = CC.build_statement(CC.GRANT_KIND, "spec-first-planning", "2.1.0", root,
                            ["docs/spec.md", "plan.json"], pay, [a], now=now)
    return CC.write_envelope(root, st)


ONE_OK = {"T1": ([], [{"text": "ok", "command": ["true"]}])}
ONE_BAD = {"T1": ([], [{"text": "bad", "command": ["false"]}])}


@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class PolicyTests(unittest.TestCase):
    def init(self, tasks=None, grant=True, budget=None, minutes=60):
        self.root = repo_with_plan()
        self.plan = write_plan_envelope(self.root, tasks or ONE_OK)
        if grant:
            write_grant(self.root, minutes=minutes)
        argv = ["init", "--plan", self.plan, "--root", self.root]
        if budget is not None:
            argv += ["--budget", json.dumps(budget)]
        return C.main(argv)

    def out(self, argv):
        """Run a command, returning (exit code, stdout)."""
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = C.main(argv)
        return rc, buf.getvalue()

    def test_init_refuses_without_a_covering_grant(self):
        rc = self.init(grant=False)
        self.assertEqual(rc, 3)

    def test_init_creates_the_run_branch_and_state(self):
        self.root = repo_with_plan()
        self.plan = write_plan_envelope(self.root, ONE_OK)
        write_grant(self.root)
        rc, out = self.out(["init", "--plan", self.plan, "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("RUN: run-", out)
        self.assertTrue(os.path.isfile(C.state_path(self.root)))

    def test_start_is_gated_by_the_grant(self):
        self.assertEqual(self.init(), 0)
        subprocess.run([sys.executable, "-I",
                        os.path.join(os.path.dirname(C.__file__), "contract_check.py"),
                        "revoke-grant", "--root", self.root], check=True, capture_output=True)
        rc, out = self.out(["start", "T1", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("GATE: ASK", out)
        self.assertIn("revoked", out)

    def test_wall_clock_budget_stops_the_run(self):
        self.assertEqual(self.init(budget={"wall_clock_min": 0}), 0)
        st = C.State.load(C.state_path(self.root))
        st.created_at = _z(CC.utc_now() - timedelta(minutes=5))
        st.save()
        rc, out = self.out(["next", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("STOP: budget_wall_clock", out)

    def test_dispatch_budget_stops_the_run(self):
        self.assertEqual(self.init(tasks={"T1": ([], [{"text": "ok", "command": ["true"]}]),
                                          "T2": ([], [{"text": "ok", "command": ["true"]}])},
                                   budget={"max_dispatches": 1}), 0)
        self.assertEqual(C.main(["start", "T1", "--root", self.root]), 0)
        rc, out = self.out(["start", "T2", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("STOP: budget_dispatches", out)

    def test_repairs_budget_parks_the_task(self):
        self.assertEqual(self.init(tasks=ONE_BAD, budget={"max_repairs_per_task": 1}), 0)
        C.main(["start", "T1", "--root", self.root])
        self.assertEqual(C.main(["verify", "T1", "--root", self.root]), 3)
        rc, out = self.out(["verify", "T1", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("PARK: T1 verify_red_after_repairs", out)

    def test_no_ready_tasks_stops_the_run(self):
        self.assertEqual(self.init(), 0)
        C.main(["park", "T1", "--reason", "by hand", "--root", self.root])
        rc, out = self.out(["next", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("STOP: no_ready_tasks", out)

    def test_token_and_dollar_budgets_are_recorded_not_enforced(self):
        self.assertEqual(self.init(budget={"max_tokens": 1, "max_usd": 0}), 0)
        rc, out = self.out(["status", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("recorded, not enforced", out)
        self.assertEqual(C.main(["start", "T1", "--root", self.root]), 0)

    def test_a_new_human_decision_parks_and_the_run_continues(self):
        self.assertEqual(self.init(tasks={"T1": ([], [{"text": "ok", "command": ["true"]}]),
                                          "T2": ([], [{"text": "ok", "command": ["true"]}])}), 0)
        rc, out = self.out(["decision", "T1", "--question", "which CI provider?",
                            "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("PARK: T1 new_human_decision", out)
        rc, out = self.out(["next", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("READY: T2", out)

    def test_an_expired_grant_stops_the_next_gate(self):
        self.assertEqual(self.init(minutes=1), 0)
        st = C.State.load(C.state_path(self.root))
        st.created_at = st.created_at  # unchanged; the grant is what expires
        # Rewrite the grant with an expiry in the past, then gate.
        write_grant(self.root, minutes=-1)
        rc, out = self.out(["start", "T1", "--root", self.root])
        self.assertEqual(rc, 3)
        self.assertIn("GATE: ASK", out)


if __name__ == "__main__":
    unittest.main()
```

  Notes for the implementer:
  - `write_grant` with `minutes=-1` writes an already-expired grant, which `check-grant` refuses with `reason=expired`. A later grant supersedes an earlier one, and the newest head wins, which is what the expiry test relies on.
  - Add `state_path(root)` to `conductor.py` — the newest run's `state.json` — since the tests use it.

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**

```python
def gate(root, action):
    checker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contract_check.py")
    argv = [sys.executable, "-I", checker, "check-grant", "--root", root, "--action", action]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return 3, "GATE: ASK reason=checker-unavailable (%s)" % exc
    last = (r.stdout.strip().splitlines() or [""])[-1]
    return r.returncode, last
```

  - Every repository-changing command calls `gate(root, "local_reversible")` first (`start`, `merge`), and `finish` calls `push_branch` and then `open_pr` (Task 4). On a non-zero exit, log `gate`, print the last line and return 3.
  - `check_stop(state)` returns, in order: `budget_wall_clock` when the wall clock is exceeded; `budget_dispatches` when dispatches reach the cap; `no_ready_tasks` when nothing is ready and nothing is in flight. `cmd_next` calls it, prints `STOP: <reason>`, records `stopped` and returns 3.
  - `cmd_verify` counts a repair on each failure after the first; at `max_repairs_per_task` it parks with `verify_red_after_repairs`.
  - `cmd_decision(task, question)` parks with `new_human_decision` and returns 0, so the run continues with other tasks.
  - `cmd_status` always prints `STATUS: budget max_tokens/max_usd recorded, not enforced (the runtime does not expose usage)`.

- [ ] **Step 4: Run the tests and see them pass.** Then run `python3 factory-conductor/assets/test_conductor_state.py` and `test_conductor_git.py` to confirm nothing regressed.
- [ ] **Step 5: Commit.**

```bash
git add factory-conductor/assets
git commit -m "feat(factory-conductor): grant gates, budgets and stop rules"
```

---

### Task 4: conductor.py — finish, the run-result envelope, push and PR

**Files:**
- Modify: `factory-conductor/assets/conductor.py`, `docs/skill-contract/SPEC.md`
- Create: `factory-conductor/assets/test_conductor_finish.py`, `factory-conductor/assets/schemas/run-result.v1.json`

**Interfaces:**
- Produces:
  - `RUN_RESULT_KIND = "https://github.com/dhanesh/agent-skills/skill-contract/run-result/v1"`
  - `run_result_payload(state) -> dict`
  - `cmd_finish(args)`: writes the envelope, then pushes and opens the PR when the gates allow; prints `FINISH: <envelope path>`
- The payload carries `run_id`, `plan` (the envelope id and sha256), `grant` (its id), `run_branch`, `stopped` (reason and time), `log_sha256`, `budget_note` (the recorded-not-enforced sentence) and `tasks[]`, each with `id`, `status`, `verify[]` (command, ok, returncode), `review`, `merge_commit` and `park_reason`.
- Assertions: one per proven task, `test: "verify:<task>"`, `assertedBy: {"skill": "factory-conductor"}`, `outcome: passed`, and `command` set to that task's first verify command. C7 then reads them as PROVEN for the executor's work, because the conductor is not the producer.
- Push and PR are run through `--push-cmd` and `--pr-cmd`, each a JSON list, so tests can stub them. The defaults are `["git", "push", "-u", "origin", "<run_branch>"]` and `["gh", "pr", "create", "--fill"]`.

- [ ] **Step 1: Write the failing tests** covering:
  - the envelope validates with the vendored `check_envelope` and pins the plan, the grant and the log's sha256;
  - `log_sha256` matches the file on disk;
  - a parked task appears with its reason and contributes no assertion;
  - with the grant covering `push_branch` and `open_pr`, both stub commands run in order and their output is logged;
  - when the grant does not cover `push_branch`, `finish` still writes the envelope, prints `GATE: ASK`, skips the push and returns 3;
  - the PR body text contains the proven count, the parked list and the budget note.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement,** including the JSON Schema file for the payload, mirroring `spec-first-planning/assets/schemas/task-plan.v1.json` in style.
- [ ] **Step 4: Register the kind in `docs/skill-contract/SPEC.md`,** in the same place `autonomy-grant/v1` is described: the URI, what it carries, who produces it, and one sentence that its verify assertions are the conductor re-running the executor's checks.
- [ ] **Step 5: Run the four conductor suites and `make contract`.**
- [ ] **Step 6: Commit.**

```bash
git add factory-conductor docs/skill-contract/SPEC.md
git commit -m "feat(factory-conductor): run-result/v1, finish, push and PR"
```

---

### Task 5: spec-first-planning — `[after: …]`, depends_on and waves (3B)

**Files:**
- Modify: `spec-first-planning/assets/spec_lint.py`, `spec_to_tasks.py`, `schemas/task-plan.v1.json`, `references/spec-format.md`, `assets/test_spec_lint.py`, `assets/test_spec_to_tasks.py`
- Modify: `spec-first-planning/SKILL.md` (mention the hint and `--waves`), and bump `metadata.version` and `SKILL_VERSION` to `2.1.0`

**Interfaces:**
- Produces:
  - `AFTER_RE = re.compile(r"\[after:\s*([^\]]+)\]")` in `spec_lint.py`, and `parse_spec` returning `after` per requirement (a list of ints)
  - `derive_plan` giving each task `depends_on`, mapped from the requirement ids in its `[after: …]` hint to the task ids that cover them
  - `spec_to_tasks.py --waves`, printing `WAVE 1: T1` lines, then `CRITICAL_PATH: T1 -> T2 -> T4`, then `WAVES_RESULT: PASS (n wave(s))`
  - `payload["tasks"][i]["depends_on"]`, an optional array of task ids, added to the schema
- Lint rules: `[after: Rn]` naming an unknown requirement fails; a cycle among `[after: …]` hints fails with the message "after: hints have a cycle".

- [ ] **Step 1: Write the failing tests.** In `test_spec_lint.py`: an unknown id fails; a cycle fails; a valid hint is clean and parses to `after: [2]`. In `test_spec_to_tasks.py`: `depends_on` appears in the payload; a spec with no hints gives one wave; the diamond gives three waves; `--waves` prints the lines and exits 0; a cycle exits non-zero.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** Keep `[after: …]` out of the task title, exactly as `[where: …]` is stripped. Reuse the wave grouping already written in `conductor.py` by copying the ten-line algorithm rather than importing across skills, and say so in a comment in both files.
- [ ] **Step 4: Run** the spec-first-planning suites, its eval, and `make gate-skill SKILL=spec-first-planning`.
- [ ] **Step 5: Commit.**

```bash
git add spec-first-planning
git commit -m "feat(spec-first-planning): [after:] hints, task depends_on and --waves"
```

---

### Task 6: The factory-conductor skill — SKILL.md, README, references, contract

**Files:**
- Create via scaffold, then rewrite: `factory-conductor/SKILL.md`, `README.md`, `references/run-protocol.md`
- Create: `factory-conductor/assets/contract_check.py` (through `make contract-vendor`)
- Modify: root `README.md` (the catalog and the Software factory section), `docs/rfc2119/2026-09-19-classification.md`

**Interfaces:** consumes everything from Tasks 1–4; produces the skill the user installs.

- [ ] **Step 1: Scaffold.** Run `python3 repo2skill/assets/scaffold_skill.py factory-conductor --tags "factory,autonomy,conductor,skill-contract"`, then replace the body.
- [ ] **Step 2: Write SKILL.md.** It MUST contain:
  - the one-line BCP 14 declaration;
  - the helper-locating block (`$SKILL_DIR`), matching the other skills;
  - **When to use:** the user asks to run an approved plan unattended, and a grant exists;
  - **Preconditions (hard rules):** a validated `task-plan/v1` envelope; a grant covering `local_reversible`; the current branch matching the grant's `branch_pattern` and not the default branch; you MUST NOT start a run without all three;
  - **The loop**, as numbered steps that map one-to-one onto the commands: `init`, then repeat { `next` → for each ready task `start`, dispatch an executor, on its report `verify`, dispatch a reviewer, `review`, `merge` } until `next` prints `STOP:`, then `finish`;
  - **The executor brief** (verbatim template): the task id and text, its interfaces, the global constraints, the worktree path, "commit inside the worktree", "you MUST NOT dispatch subagents", and the report contract (status, commits, one-line test summary, concerns);
  - **The reviewer brief** (verbatim template): the task, the diff, the constraints, and two questions — does it satisfy the requirement, does it do anything the task did not ask for;
  - **Stop and park rules:** on a `NEEDS_DECISION` report you MUST run `conductor decision` and carry on with other tasks; you MUST NOT answer a human-decision question yourself;
  - **The honesty section (plain prose):** the conductor proves exactly what a task's verify commands prove; `max_tokens` and `max_usd` are recorded, not enforced; a grant can be forged by an agent with a shell, which is why merges stay with the human;
  - **The Contract block:** consumes `task-plan/v1` and `autonomy-grant/v1`, provides `run-result/v1`;
  - **Boundaries:** not the planner (spec-first-planning), not the loop designer (crafting-self-prompting-loops), never merges to the default branch.
- [ ] **Step 3: `references/run-protocol.md`:** the full command reference with exit codes, the state and log formats, resume, and a worked three-task example.
- [ ] **Step 4: Vendor and catalog.** Run `make contract-vendor`, add the install line and the Skills-table row to the root README, and extend the Software factory section: unattended runs now reach an open PR, with the limits stated.
- [ ] **Step 5: BCP 14.** Add a classification row per hard rule; check PP-5 and PP-7 with `sh scripts/gates/prompting-playbook.sh factory-conductor`.
- [ ] **Step 6: Run** `make gate-skill SKILL=factory-conductor` and `make readme`.
- [ ] **Step 7: Commit.**

```bash
git add factory-conductor README.md docs/rfc2119/2026-09-19-classification.md
git commit -m "feat(factory-conductor): the skill — run protocol, briefs, contract and catalog entry"
```

---

### Task 7: The eval and the end-to-end test

**Files:**
- Modify: `factory-conductor/eval/run_eval.py`
- Create: `factory-conductor/assets/test_conductor_e2e.py`

**Interfaces:** consumes the CLI from Tasks 1–4 and the skill from Task 6.

- [ ] **Step 1: Write the end-to-end test.** In a temp repo with a real grant and a three-task plan (T2 depends on T1; T3 is independent and its verify fails):
  - `init` → `RUN:`;
  - wave 1 offers T1 and T3 (parallel limit 2);
  - T1 and T3 start; T1's verify passes, T3's fails twice and parks with `verify_red_after_repairs`;
  - T1 is reviewed and merged; T2 then becomes ready, runs, is verified, reviewed and merged;
  - `next` prints `STOP: no_ready_tasks`;
  - `finish` with stubbed push and PR writes a `run-result/v1` envelope that validates, with two proven tasks and one parked, and a `log_sha256` matching the file.
- [ ] **Step 2: Run it and see it fail,** then fix forward only within this test and the skill's own files.
- [ ] **Step 3: Write the eval** following `docs/eval-standard.md` and the `check(name, ok, detail)` protocol. It MUST include these NEGATIVE checks:
  - a task whose verify fails is never merged (its status is never `proven`, and the run branch has no commit from it);
  - a task that needs a decision parks and the run continues;
  - an expired grant stops the run at the next gate;
  - `max_dispatches: 1` stops the run;
  - a merge conflict parks the task and leaves the run branch clean;
  - a task with a null verify command is parked, never merged.
  
  Plus positives: a plan with a dependency runs in the right order, and `finish` writes an envelope that `check-envelope` validates.
- [ ] **Step 4: Run** `python3 factory-conductor/eval/run_eval.py` and `make gate-skill SKILL=factory-conductor`.
- [ ] **Step 5: Commit.**

```bash
git add factory-conductor
git commit -m "test(factory-conductor): end-to-end run and the outcome eval"
```

---

### Task 8: A/B rows, status docs and the full gate

**Files:**
- Modify: `scripts/ab-validate.py`, root `README.md`, `docs/factory/2026-09-19-assessment.md`

**Interfaces:** consumes the finished skill.

- [ ] **Step 1: Add the rows.** Set `SINCE_FACTORY_CONDUCTOR` to the Task 1 commit (`git log --format=%h --grep="run state, wave scheduling" -1`), beside the other `SINCE_*` constants. Add this after the other `check_*` functions, and call `check_factory_conductor(old, REPO)` from `main()` after them:

```python
_FC_TASKS_OK = {"T1": ([], [{"text": "ok", "command": ["true"]}])}
_FC_TASKS_BAD = {"T1": ([], [{"text": "bad", "command": ["false"]}])}


def _fc_run(tree, tasks):
    """Run a fixture plan through a tree's conductor. Returns (envelope_ok, merged_ids).

    A tree without the skill scores (0, set()) — that is how the old arm reads.
    """
    conductor = os.path.join(tree, "factory-conductor", "assets", "conductor.py")
    if not os.path.isfile(conductor):
        return 0, set()
    sys.path.insert(0, os.path.join(tree, "factory-conductor", "assets"))
    try:
        import importlib
        CC = importlib.import_module("contract_check")
        importlib.reload(CC)
    finally:
        sys.path.pop(0)
    root = tempfile.mkdtemp()
    genv = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    os.makedirs(os.path.join(root, "docs"))
    open(os.path.join(root, "docs", "spec.md"), "w").write("# Spec\n")
    open(os.path.join(root, "plan.json"), "w").write('{"plan": 1}\n')
    for argv in (["init", "-q", "-b", "main", root],):
        subprocess.run(["git", *argv], check=True, capture_output=True)
    subprocess.run(["git", "-C", root, "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", root, "commit", "-q", "-m", "x"], check=True,
                   capture_output=True, env=genv)
    subprocess.run(["git", "-C", root, "checkout", "-q", "-b", "factory/p"], check=True,
                   capture_output=True)
    plan_payload = {"title": "T", "spec": "docs/spec.md", "coverage": {}, "uncovered": [],
                    "tasks": [{"id": i, "requirement_ids": ["R1"], "title": i,
                               "verify": v, "depends_on": dep}
                              for i, (dep, v) in tasks.items()]}
    plan_env = CC.write_envelope(root, CC.build_statement(
        "https://github.com/dhanesh/agent-skills/skill-contract/task-plan/v1",
        "spec-first-planning", "2.1.0", root, ["docs/spec.md"], plan_payload))
    now = CC.utc_now()
    grant_payload = {"scope": {"repo": ".", "branch_pattern": "factory/*"},
                     "decisions": [{"id": "D1", "question": "q", "answer": "a",
                                    "source": "sweep"}],
                     "defaults": [], "gate_policy": {"read_only": "auto",
                                                     "local_reversible": "grant"},
                     "budget": {}, "stop_on": [],
                     "expires_at": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "system_one": {"allowed": False}, "revoked": False}
    accepted = {"test": "grant-accepted", "assertedBy": {"human": "Dana"},
                "result": {"outcome": "passed"},
                "command": ["{python}", "{skill_dir:spec-first-planning}/assets/spec_lint.py",
                            "--unattended", "docs/spec.md"],
                "subject": [CC.pin(root, "docs/spec.md")]}
    CC.write_envelope(root, CC.build_statement(CC.GRANT_KIND, "spec-first-planning", "2.1.0",
                                               root, ["docs/spec.md", "plan.json"],
                                               grant_payload, [accepted], now=now))

    def run(*argv):
        return subprocess.run([sys.executable, "-I", conductor, *argv, "--root", root],
                              capture_output=True, text=True, timeout=300)

    run("init", "--plan", plan_env)
    for tid in tasks:
        run("start", tid)
        if run("verify", tid).returncode == 0:
            run("review", tid, "--verdict", "pass")
            run("merge", tid)
    fin = run("finish", "--push-cmd", '["true"]', "--pr-cmd", '["true"]')
    envelope_ok = 1 if "FINISH:" in fin.stdout else 0
    log = subprocess.run(["git", "-C", root, "log", "--format=%s"], capture_output=True,
                         text=True).stdout
    merged = {t for t in tasks if ("conductor: %s" % t) in log}
    return envelope_ok, merged


def check_factory_conductor(old, new):
    s = "factory-conductor"
    a_ok, _ = _fc_run(old, _FC_TASKS_OK)
    b_ok, b_merged = _fc_run(new, _FC_TASKS_OK)
    row(s, "plans a conductor can run end to end (init..finish, validating run-result)",
        a_ok, b_ok, b_ok == 1 and a_ok == 0,
        "nothing ran a plan end to end: every task needed the human to dispatch, verify and merge it",
        since=SINCE_FACTORY_CONDUCTOR)

    def adopters(tree):
        return sum(1 for d in sorted(os.listdir(tree))
                   if os.path.isfile(os.path.join(tree, d, "SKILL.md"))
                   and re.search(r"(?m)^## Contract\s*$",
                                 open(os.path.join(tree, d, "SKILL.md"), encoding="utf-8").read()))

    a, b = adopters(old), adopters(new)
    row(s, "skill-contract adopters", a, b, b > a,
        "factory-conductor consumes task-plan/v1 and autonomy-grant/v1 and provides run-result/v1",
        since=SINCE_FACTORY_CONDUCTOR)

    def merged_with_failing_verify(tree):
        ok, merged = _fc_run(tree, _FC_TASKS_BAD)
        return 1 if "T1" in merged else 0

    a, b = merged_with_failing_verify(old), merged_with_failing_verify(new)
    row(s, "tasks merged despite a failing verify", a, b, a == 0 and b == 0,
        "a task is merged only after the conductor itself re-ran its verify commands — "
        "sanity-checked against the same fixture with a passing verify, which DOES merge",
        kind="guard")
```

  **The sanity arm is required, exactly as `check_autonomy_grant` does it.** Inside `merged_with_failing_verify`, run the passing fixture too; if it does not merge, append to `PROBE_ERRORS` and return `None`, so the row fails rather than passing vacuously. Check that `subprocess`, `sys`, `tempfile`, `re` and `timedelta` are imported at the top of `ab-validate.py`; add any that are missing.

- [ ] **Step 2: Update the docs.** In the root README's Software factory section, say that an approved plan can now run to an open PR, with the limits (irreversible actions still ask; tokens and dollars are not capped). Add the step-4 bullet to the assessment doc, and record Jev's post-merge re-judgement placeholder as "to be re-judged after merge".
- [ ] **Step 3: Verify.** Run `make gate` to completion (redirect to a scratch log), then `make ab-validate` and `make readme`. All must pass, with 0 worse and 0 unproven.
- [ ] **Step 4: Commit.**

```bash
git add scripts/ab-validate.py README.md docs/factory/2026-09-19-assessment.md
git commit -m "test(ab-validate): factory-conductor rows; README and assessment status"
```

---

## Self-review notes

- **Spec coverage:** §1 pieces → Tasks 1–4, 6. §2 run loop → Tasks 1–2, 6. §3 gates, budgets, stop rules → Task 3. §4 log and evidence → Tasks 1, 4. §5 scheduling (3B) → Tasks 1, 5. §6 notifications, resume, packaging → Tasks 1, 6. §7 AC1 → Tasks 1–3; AC2 → Task 3; AC3 → Task 7; AC4 → Task 7; AC5 → Task 6; AC6 → Task 8; AC7 → Tasks 6–8; AC8 → after merge, by the controller.
- **Names used across tasks:** `waves`, `PlanError`, `State`, `run_dir`, `new_run_id`, `git`, `git_env`, `gate`, `check_stop`, `run_result_payload`, `RUN_RESULT_KIND`, `cmd_init`, `cmd_next`, `cmd_start`, `cmd_verify`, `cmd_review`, `cmd_merge`, `cmd_park`, `cmd_decision`, `cmd_gate`, `cmd_status`, `cmd_resume`, `cmd_finish`.
- **Known judgment calls for implementers:** the exact wording of log fields beyond `at` and `event`; the PR body layout; how much of stdout and stderr to keep per verify run (the plan says the last 2000 characters).
