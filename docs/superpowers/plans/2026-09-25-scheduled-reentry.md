# Scheduled Re-entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A factory-conductor run whose driving session died is resumed and carried to `NEXT: run done` or `NEXT: run ask` by a local OS timer. The resume happens only inside the grant the human accepted, and never while another session drives the run.

**Architecture:**
- A new stdlib module, `factory-conductor/assets/reentry.py`, holds the run lock, the lease, the pure `watch` decision, the fixed resume prompt and the agent spawn.
- A second module, `reentry_timer.py`, renders, installs and removes one launchd, systemd-user or cron timer per run.
- `conductor.py` wires these in:
  - every run command runs under the lock and renews the lease;
  - there are new `watch` and `reentry install|uninstall|status` subcommands;
  - there is a new stop reason, `reentry_exhausted`.
- Consent is a `reentry` block in the autonomy grant. The reference checker validates it, and spec-first-planning's `write_grant.py` writes it.

**Tech Stack:** Python 3.10+ stdlib only (`fcntl`, `subprocess`, `plistlib`, `shlex`), plus git. There are no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-25-scheduled-reentry-design.md`

## Global Constraints

- **Language and dependencies.** Python 3.10+ stdlib only. No pip, no network at install time. Every shipped Python file is importable on macOS and Linux.
- **The `reentry` block.** It lives in `payload.reentry` of `autonomy-grant/v1`:
  - `agent_cmd` is an argv list, never a string. It contains the token `{prompt}` exactly once and may contain `{root}`. Its first element is never a shell (`sh`, `bash`, `zsh`, `fish`, `dash`, `ksh`, `cmd`, `powershell`, `pwsh`, compared by basename). Every other element is a literal: no other `{…}` token.
  - `interval_min`: an integer from 5 to 60. The default is 10.
  - `stall_min`: an integer from 15 to 240, and at least 2 × `interval_min`. The default is 30.
  - `max_reentries`: an integer from 1 to 20. The default is 5.
- **The lease** is `runs/<id>/lease.json`, holding `{holder: "session"|"reentry", host, pid, renewed_at, n}`:
  - A reentry lease is live while its pid is alive on the same host.
  - A session lease is live while `now - renewed_at < stall_min`, or while any in-flight task's worktree changed within `stall_min`.
- **The run lock** is an `fcntl.flock` on `runs/<id>/.lock`, held by every run command except `init`. Waiting longer than 30 s prints `run locked` to stderr and exits 2.
- **`watch` output.** It prints exactly one `REENTRY:` line. The words are `disabled`, `no-run`, `done`, `ask`, `waiting-human`, `live`, `not-stalled`, `exhausted`, `started <n> pid=<pid>` and `failed`. Exit codes: 0, except 2 for `failed` or a usage error.
- **The resume prompt** is fixed in `reentry.py` and never built from plan or grant text.
- **Timer files are named by run id.**
  - launchd label: `io.agent-skills.factory-conductor.<run-id>`
  - systemd unit: `factory-conductor-<run-id>.{service,timer}`
  - cron tag: `# factory-conductor <run-id>`
- **The vendored checker** (`*/assets/contract_check.py`) stays byte-identical to `docs/skill-contract/reference/contract_check.py`. Run `make contract-vendor` after changing the reference.
- **SKILL.md rules.** Every SKILL.md keyword sentence needs a BCP 14 register row (`make bcp14`). Every absolute must state its reason (PP-5). Helpers are invoked as `"$SKILL_DIR/assets/…"`.
- **CI has no git identity.** Every test git commit or merge passes the testkit `GIT` env.
- **Commit trailer**, on every commit:
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01X7cbN5bQsCx5HyMdb8Lo7X
  ```

**Ruling on a spec detail:** spec §1 names `write_grant.py` flags (`--reentry-cmd …`). But `write_grant.py` takes all grant fields from an `--answers` JSON file, so the block is `answers.reentry` instead. That is consistent with every other grant field, and it carries the same values.

## Review Focus

- **Running `watch` in a repo with no run** (for example, a timer left over after the run directory was deleted) prints `REENTRY: no-run` and exits 0, so the timer does not spam failures. Tested in Task 3.
- **The agent binary is missing, or the spawn raises.** `watch` logs `reentry` with `ok: false`, still counts the attempt toward `max_reentries` so a broken command cannot loop forever, prints `REENTRY: failed`, and exits 2. Tested in Task 3.
- **A corrupt or partly written `lease.json`** is read as a live session lease until its file mtime is older than `stall_min`. This is the conservative choice: when in doubt, don't start a second driver. Tested in Task 2.
- **Two `watch` runs overlapping** (a slow check while the timer fires again) start at most one agent. The run lock serialises them, and the second one sees the first one's reentry lease. Tested in Task 3.
- **A repo path containing spaces or quotes** renders correctly in the plist (an argv array), the systemd `ExecStart` (quoted) and the crontab line (`shlex.quote`). Tested in Task 4.

---

### Task 1: The checker validates `reentry`, re-vendored

**Files:**
- Modify: `docs/skill-contract/reference/contract_check.py` (add `reentry_problems`; call it from `grant_violations`)
- Modify: `docs/skill-contract/SPEC.md` (the autonomy-grant/v1 payload table)
- Test: `docs/skill-contract/reference/test_contract_check.py`
- Then run: `make contract-vendor`, which updates every `*/assets/contract_check.py`

**Interfaces:**
- Produces: `reentry_problems(block) -> list[str]` (an empty list means valid), plus `REENTRY_DEFAULTS = {"interval_min": 10, "stall_min": 30, "max_reentries": 5}` and `SHELLS`. `grant_violations(st)` appends `"payload.reentry: " + problem` for each problem when `payload` has a `reentry` key.

- [ ] **Step 1: Write the failing tests**

Add to `docs/skill-contract/reference/test_contract_check.py`:

```python
class ReentryBlockTests(unittest.TestCase):
    OK = {"agent_cmd": ["claude", "-p", "{prompt}"], "interval_min": 10,
          "stall_min": 30, "max_reentries": 5}

    def problems(self, **over):
        b = dict(self.OK)
        b.update(over)
        return CC.reentry_problems(b)

    def test_a_valid_block_has_no_problems(self):
        self.assertEqual(CC.reentry_problems(dict(self.OK)), [])
        self.assertEqual(CC.reentry_problems({"agent_cmd": ["agent", "{prompt}", "{root}"]}), [])

    def test_agent_cmd_must_be_an_argv_list_with_one_prompt(self):
        for bad in ("claude -p {prompt}", [], ["claude", "-p"], ["a", "{prompt}", "{prompt}"],
                    ["a", "{prompt}", 3]):
            with self.subTest(bad=bad):
                self.assertTrue(self.problems(agent_cmd=bad))

    def test_a_shell_is_refused_by_basename(self):
        for sh in ("sh", "/bin/bash", "zsh", "fish", "dash", "ksh", "cmd", "powershell", "pwsh"):
            with self.subTest(sh=sh):
                self.assertTrue(self.problems(agent_cmd=[sh, "-c", "{prompt}"]))

    def test_only_prompt_and_root_tokens(self):
        self.assertTrue(self.problems(agent_cmd=["a", "{prompt}", "{home}"]))

    def test_numeric_ranges(self):
        for key, bad in (("interval_min", 4), ("interval_min", 61), ("stall_min", 14),
                         ("stall_min", 241), ("max_reentries", 0), ("max_reentries", 21),
                         ("interval_min", "10"), ("max_reentries", True)):
            with self.subTest(key=key, bad=bad):
                self.assertTrue(self.problems(**{key: bad}))

    def test_stall_is_at_least_twice_the_interval(self):
        self.assertTrue(self.problems(interval_min=20, stall_min=30))
        self.assertEqual(self.problems(interval_min=15, stall_min=30), [])

    def test_unknown_keys_are_refused(self):
        self.assertTrue(self.problems(shell=True))

    def test_a_grant_with_a_bad_block_is_invalid(self):
        st = make_grant_statement(extra={"reentry": {"agent_cmd": "sh -c x"}})
        self.assertTrue(any(v.startswith("payload.reentry") for v in CC.grant_violations(st)))
```

`make_grant_statement` is the existing grant-fixture helper in that test file. If its name differs, use the helper the existing `grant_violations` tests already use, passing the extra payload key through its payload override.

- [ ] **Step 2: Run the tests and see them fail**

Run: `python3 docs/skill-contract/reference/test_contract_check.py -k Reentry`
Expected: FAIL with `AttributeError: module 'contract_check' has no attribute 'reentry_problems'`

- [ ] **Step 3: Implement** in `docs/skill-contract/reference/contract_check.py`, next to `grant_violations`:

```python
REENTRY_DEFAULTS = {"interval_min": 10, "stall_min": 30, "max_reentries": 5}
REENTRY_RANGES = {"interval_min": (5, 60), "stall_min": (15, 240), "max_reentries": (1, 20)}
SHELLS = {"sh", "bash", "zsh", "fish", "dash", "ksh", "cmd", "powershell", "pwsh"}
_REENTRY_TOKEN = re.compile(r"\{[^{}]*\}")


def reentry_problems(block):
    """Problems with an autonomy grant's optional payload.reentry block; [] when valid.

    agent_cmd is an argv list, never a shell (a shell would turn the argv back into an
    evaluated string), with {prompt} exactly once and {root} optional."""
    if not isinstance(block, dict):
        return ["must be an object"]
    out = []
    unknown = set(block) - {"agent_cmd"} - set(REENTRY_RANGES)
    if unknown:
        out.append("unknown key(s): %s" % ", ".join(sorted(unknown)))
    cmd = block.get("agent_cmd")
    if not (isinstance(cmd, list) and cmd and all(isinstance(a, str) and a for a in cmd)):
        out.append("agent_cmd must be a non-empty list of non-empty strings")
    else:
        if os.path.basename(cmd[0]).lower() in SHELLS:
            out.append("agent_cmd must not start with a shell (%s)" % cmd[0])
        if cmd.count("{prompt}") != 1:
            out.append("agent_cmd must contain the {prompt} token exactly once")
        bad = [t for a in cmd for t in _REENTRY_TOKEN.findall(a)
               if not (a == t and t in ("{prompt}", "{root}"))]
        if bad:
            out.append("agent_cmd may carry only whole {prompt} and {root} tokens: %s"
                       % ", ".join(sorted(set(bad))))
    vals = dict(REENTRY_DEFAULTS)
    for key, (lo, hi) in REENTRY_RANGES.items():
        if key in block:
            v = block[key]
            if not (isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi):
                out.append("%s must be an integer from %d to %d" % (key, lo, hi))
                continue
            vals[key] = v
    if vals["stall_min"] < 2 * vals["interval_min"]:
        out.append("stall_min must be at least 2 x interval_min")
    return out
```

In `grant_violations`, after the `require_signature` check:

```python
    if "reentry" in p:
        out.extend("payload.reentry: " + v for v in reentry_problems(p["reentry"]))
```

- [ ] **Step 4: Document it in SPEC.md.** In the autonomy-grant/v1 payload table, add this row:

```markdown
| `reentry` | object, optional | Consent to scheduled re-entry (factory-conductor): `agent_cmd` (argv list, `{prompt}` exactly once, `{root}` optional, no shell as `agent_cmd[0]`), `interval_min` 5–60 (default 10), `stall_min` 15–240 and ≥ 2 × interval (default 30), `max_reentries` 1–20 (default 5). The checker refuses a malformed block (C10). |
```

- [ ] **Step 5: Run the tests, re-vendor, and run the contract suite**

Run: `python3 docs/skill-contract/reference/test_contract_check.py && make contract-vendor && make contract`
Expected: all tests PASS, and `make contract` exits 0.

- [ ] **Step 6: Commit**

```bash
git add docs/skill-contract */assets/contract_check.py
git commit -m "feat(skill-contract): autonomy-grant/v1 gains an optional reentry block"
```

---

### Task 2: The run lock and the lease

**Files:**
- Create: `factory-conductor/assets/reentry.py`
- Modify: `factory-conductor/assets/conductor.py`, so that `main()` runs every command except `init` under the lock and renews the lease
- Test: `factory-conductor/assets/test_conductor_reentry.py` (new, marked `# gate:` is not needed for .py)

**Interfaces:**
- Consumes: `conductor.current_run(root)`, `conductor.State`, and `State.tasks[t]["worktree"]`.
- Produces, in `reentry.py`:
  - `LOCK_FILE = ".lock"`, `LEASE_FILE = "lease.json"`, `LOCK_TIMEOUT = 30`
  - `class RunLocked(Exception)`
  - `run_lock(run_dir, timeout=None)`: a context manager that raises `RunLocked`; `None` means the module's `LOCK_TIMEOUT`, read at call time
  - `read_lease(run_dir) -> dict | None`
  - `renew_lease(run_dir, now=None)`: keeps `holder: "reentry"` when the env var `FACTORY_CONDUCTOR_REENTRY` equals the lease's `n`, and otherwise writes a session lease
  - `set_reentry_lease(run_dir, n, pid, now=None)`
  - `lease_live(run_dir, lease, stall_min, worktrees, now=None) -> bool`
  - `pid_alive(pid) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `factory-conductor/assets/test_conductor_reentry.py`:

```python
import json, os, socket, subprocess, sys, tempfile, time, unittest
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reentry as R
from conductor_testkit import tmpdir


def utc(minutes_ago=0):
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


class LockTests(unittest.TestCase):
    def test_a_second_holder_times_out(self):
        d = tmpdir()
        code = ("import sys,time;sys.path.insert(0,%r);import reentry as R\n"
                "with R.run_lock(%r):\n print('held',flush=True);time.sleep(5)"
                % (os.path.dirname(os.path.abspath(R.__file__)), d))
        p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(), "held")
            with self.assertRaises(R.RunLocked):
                with R.run_lock(d, timeout=0.5):
                    pass
        finally:
            p.kill(); p.wait()
        with R.run_lock(d, timeout=0.5):
            pass


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.d = tmpdir()

    def test_renew_writes_a_session_lease(self):
        R.renew_lease(self.d)
        lease = R.read_lease(self.d)
        self.assertEqual((lease["holder"], lease["host"]), ("session", socket.gethostname()))

    def test_a_fresh_session_lease_is_live_and_an_old_one_is_not(self):
        R.renew_lease(self.d, now=utc(5))
        self.assertTrue(R.lease_live(self.d, R.read_lease(self.d), 30, []))
        R.renew_lease(self.d, now=utc(45))
        self.assertFalse(R.lease_live(self.d, R.read_lease(self.d), 30, []))

    def test_worktree_activity_keeps_an_old_session_lease_live(self):
        R.renew_lease(self.d, now=utc(45))
        wt = tmpdir()
        with open(os.path.join(wt, "f.py"), "w") as f:
            f.write("x")
        self.assertTrue(R.lease_live(self.d, R.read_lease(self.d), 30, [wt]))
        old = time.time() - 45 * 60
        os.utime(os.path.join(wt, "f.py"), (old, old)); os.utime(wt, (old, old))
        self.assertFalse(R.lease_live(self.d, R.read_lease(self.d), 30, [wt]))

    def test_a_reentry_lease_is_live_exactly_while_its_pid_lives(self):
        p = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
        try:
            R.set_reentry_lease(self.d, 1, p.pid, now=utc(90))
            self.assertTrue(R.lease_live(self.d, R.read_lease(self.d), 30, []))
        finally:
            p.kill(); p.wait()
        self.assertFalse(R.lease_live(self.d, R.read_lease(self.d), 30, []))

    def test_a_child_command_keeps_the_reentry_holder(self):
        R.set_reentry_lease(self.d, 2, os.getpid())
        os.environ["FACTORY_CONDUCTOR_REENTRY"] = "2"
        try:
            R.renew_lease(self.d)
        finally:
            del os.environ["FACTORY_CONDUCTOR_REENTRY"]
        lease = R.read_lease(self.d)
        self.assertEqual((lease["holder"], lease["n"], lease["pid"]), ("reentry", 2, os.getpid()))

    def test_a_corrupt_lease_is_live_until_its_mtime_is_old(self):
        path = os.path.join(self.d, R.LEASE_FILE)
        with open(path, "w") as f:
            f.write("{not json")
        self.assertTrue(R.lease_live(self.d, R.read_lease(self.d), 30, []))
        old = time.time() - 45 * 60
        os.utime(path, (old, old))
        self.assertFalse(R.lease_live(self.d, R.read_lease(self.d), 30, []))

    def test_a_future_renewal_is_live(self):
        R.renew_lease(self.d, now=utc(-10))
        self.assertTrue(R.lease_live(self.d, R.read_lease(self.d), 30, []))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `python3 factory-conductor/assets/test_conductor_reentry.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'reentry'`

- [ ] **Step 3: Implement** `factory-conductor/assets/reentry.py`:

```python
"""Scheduled re-entry for factory-conductor runs: the run lock, the lease, the watch
decision, the fixed resume prompt and the agent spawn. Stdlib only.

A lease says who drives a run. A reentry lease (an agent `watch` started) is live
exactly while its pid lives on this host. A session lease (any other driver) is live
while it was renewed, or an in-flight task's worktree changed, within stall_min: an
executor subagent can work for a long time without calling the conductor."""
import contextlib, fcntl, json, os, socket, tempfile, time
from datetime import datetime, timezone

LOCK_FILE = ".lock"
LEASE_FILE = "lease.json"
LOCK_TIMEOUT = 30
ENV_REENTRY = "FACTORY_CONDUCTOR_REENTRY"


class RunLocked(Exception):
    pass


@contextlib.contextmanager
def run_lock(run_dir, timeout=None):
    """Hold an exclusive flock on <run_dir>/.lock, so two drivers never interleave
    writes to state.json or the log. Raises RunLocked after `timeout` seconds
    (default: the module's LOCK_TIMEOUT, read at call time so tests can shorten it)."""
    f = open(os.path.join(run_dir, LOCK_FILE), "a+")
    try:
        deadline = time.monotonic() + (LOCK_TIMEOUT if timeout is None else timeout)
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RunLocked(run_dir)
                time.sleep(0.1)
        yield
    finally:
        f.close()


def _stamp(now):
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(run_dir, lease):
    fd, tmp = tempfile.mkstemp(dir=run_dir, prefix=".lease.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(lease, f, sort_keys=True)
        f.write("\n")
    os.replace(tmp, os.path.join(run_dir, LEASE_FILE))


def read_lease(run_dir):
    """The lease dict, {"corrupt": True} for an unreadable one, or None when absent."""
    path = os.path.join(run_dir, LEASE_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            lease = json.load(f)
        return lease if isinstance(lease, dict) else {"corrupt": True}
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"corrupt": True}


def renew_lease(run_dir, now=None):
    """Renew the lease for the command running now. A command run by the agent that
    watch started (ENV_REENTRY == the lease's n) keeps the reentry holder and its pid;
    any other command takes the lease as a session."""
    old = read_lease(run_dir) or {}
    host = socket.gethostname()
    if old.get("holder") == "reentry" and str(old.get("n")) == os.environ.get(ENV_REENTRY):
        _write(run_dir, dict(old, renewed_at=_stamp(now)))
        return
    _write(run_dir, {"holder": "session", "host": host, "pid": None,
                     "renewed_at": _stamp(now), "n": old.get("n", 0)})


def set_reentry_lease(run_dir, n, pid, now=None):
    _write(run_dir, {"holder": "reentry", "host": socket.gethostname(), "pid": pid,
                     "renewed_at": _stamp(now), "n": n})


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (TypeError, ValueError, OSError):
        return False
    try:  # a zombie child of this process counts as dead
        return os.waitpid(int(pid), os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return True


def _newest_mtime(path):
    newest = 0.0
    for base, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in [base] + [os.path.join(base, n) for n in files]:
            try:
                newest = max(newest, os.stat(name).st_mtime)
            except OSError:
                pass
    try:  # a worktree's .git is a file naming its gitdir; commits touch HEAD and index
        with open(os.path.join(path, ".git"), encoding="utf-8") as f:
            gitdir = f.read().split("gitdir:", 1)[1].strip()
        for name in ("HEAD", "index"):
            newest = max(newest, os.stat(os.path.join(gitdir, name)).st_mtime)
    except (OSError, IndexError):
        pass
    return newest


def lease_live(run_dir, lease, stall_min, worktrees, now=None):
    """True when someone is (or may be) driving the run; when in doubt, True."""
    now_ts = (now or datetime.now(timezone.utc)).timestamp()
    window = stall_min * 60
    if lease is None:
        return False
    if lease.get("corrupt"):
        try:
            return now_ts - os.stat(os.path.join(run_dir, LEASE_FILE)).st_mtime < window
        except OSError:
            return False
    if lease.get("holder") == "reentry":
        return lease.get("host") == socket.gethostname() and pid_alive(lease.get("pid"))
    try:
        renewed = datetime.strptime(lease["renewed_at"], "%Y-%m-%dT%H:%M:%SZ") \
            .replace(tzinfo=timezone.utc).timestamp()
    except (KeyError, TypeError, ValueError):
        return True
    if now_ts - renewed < window:
        return True
    return any(now_ts - _newest_mtime(w) < window for w in worktrees if os.path.isdir(w))
```

- [ ] **Step 4: Wire the lock and the lease into `conductor.main()`.** Replace `return args.fn(args)` with:

```python
    if args.cmd == "init" or getattr(args, "no_lock", False):
        return args.fn(args)
    d = current_run(os.path.abspath(args.root))
    if d is None:
        return args.fn(args)
    try:
        with R.run_lock(d):
            rc = args.fn(args)
            R.renew_lease(d)
            return rc
    except R.RunLocked:
        sys.stderr.write("run locked: another conductor command holds %s\n"
                         % os.path.join(d, R.LOCK_FILE))
        return 2
```

Add `import reentry as R` next to the existing `import contract_check as CC`. `init` renews the lease once, right after `State.new` returns: `R.renew_lease(st.dir)`.

- [ ] **Step 5: Add tests for the wiring** to `test_conductor_reentry.py`:

```python
import conductor as C
from conductor_testkit import new_run, repo


class WiringTests(unittest.TestCase):
    def test_every_run_command_renews_the_session_lease(self):
        root = repo()
        st, _ = new_run(root, {"title": "T", "spec": "s.md", "coverage": {}, "uncovered": [],
                               "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "a",
                                          "verify": [{"text": "t", "command": ["true"]}],
                                          "depends_on": []}]})
        C.main(["status", "--root", root])
        self.assertEqual(R.read_lease(st.dir)["holder"], "session")

    def test_a_held_lock_makes_a_command_exit_2(self):
        root = repo()
        st, _ = new_run(root, {"title": "T", "spec": "s.md", "coverage": {}, "uncovered": [],
                               "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "a",
                                          "verify": [{"text": "t", "command": ["true"]}],
                                          "depends_on": []}]})
        old = R.LOCK_TIMEOUT
        R.LOCK_TIMEOUT = 0.3
        try:
            with R.run_lock(st.dir):
                code = ("import sys;sys.path.insert(0,%r);import reentry as R;R.LOCK_TIMEOUT=0.3;"
                        "import conductor as C;sys.exit(C.main(['status','--root',%r]))"
                        % (os.path.dirname(os.path.abspath(C.__file__)), root))
                r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        finally:
            R.LOCK_TIMEOUT = old
        self.assertEqual(r.returncode, 2)
        self.assertIn("run locked", r.stderr)
```

- [ ] **Step 6: Run everything**

Run: `for t in factory-conductor/assets/test_conductor_*.py; do python3 $t || exit 1; done`
Expected: every suite prints OK, including the existing 6 suites. The lock is re-entrant only across processes, so no existing test holds it twice.

- [ ] **Step 7: Commit**

```bash
git add factory-conductor/assets/reentry.py factory-conductor/assets/conductor.py factory-conductor/assets/test_conductor_reentry.py
git commit -m "feat(factory-conductor): a run lock and a lease on every run command"
```

---

### Task 3: `conductor watch` and the `reentry_exhausted` stop

**Files:**
- Modify: `factory-conductor/assets/reentry.py` (add `RESUME_PROMPT`, `decide`, `expand_agent_cmd` and `spawn`)
- Modify: `factory-conductor/assets/conductor.py` (add `cmd_watch`, add `reentry_exhausted` to `STOP_REASONS`, add the `watch` subparser)
- Modify: `factory-conductor/assets/schemas/run-result.v1.json` (add `reentry_exhausted` to the `stopped.reason` enum)
- Test: `factory-conductor/assets/test_conductor_reentry.py`

**Interfaces:**
- Consumes (Task 1): `CC.reentry_problems` and `CC.REENTRY_DEFAULTS`.
- Consumes (Task 2): `R.read_lease`, `R.lease_live`, `R.set_reentry_lease` and `R.run_lock`.
- Produces:
  - `R.RESUME_PROMPT`, a str with `{root}`.
  - `R.decide(finished, stopped_reason, covered, block, live, idle_min, count) -> str`. It returns one of `"done"`, `"waiting-human"`, `"ask"`, `"disabled"`, `"live"`, `"not-stalled"`, `"exhausted"` or `"start"`.
  - `R.expand_agent_cmd(argv, root) -> list[str]`.
  - `R.spawn(argv, root, log_path, n) -> int`, which returns the pid and raises `OSError`.
  - The `conductor watch --root <repo>` subcommand.
  - The log event `reentry` `{n, ok, argv, pid | error}`.

- [ ] **Step 1: Write the failing tests** for the pure decision, which covers every branch of spec §3. Add to `test_conductor_reentry.py`:

```python
class DecideTests(unittest.TestCase):
    B = {"agent_cmd": ["a", "{prompt}"], "stall_min": 30, "max_reentries": 5}

    def d(self, **kw):
        a = dict(finished=False, stopped_reason=None, covered=True, block=self.B,
                 live=False, idle_min=60, count=0)
        a.update(kw)
        return R.decide(**a)

    def test_each_branch_in_spec_order(self):
        self.assertEqual(self.d(finished=True), "done")
        self.assertEqual(self.d(stopped_reason="grant_ask"), "waiting-human")
        self.assertEqual(self.d(covered=False), "ask")
        self.assertEqual(self.d(block=None), "disabled")
        self.assertEqual(self.d(live=True), "live")
        self.assertEqual(self.d(idle_min=29), "not-stalled")
        self.assertEqual(self.d(count=5), "exhausted")
        self.assertEqual(self.d(), "start")

    def test_finished_wins_over_everything(self):
        self.assertEqual(self.d(finished=True, covered=False, live=True), "done")

    def test_a_stopped_run_other_than_grant_ask_is_still_resumed(self):
        self.assertEqual(self.d(stopped_reason="no_ready_tasks"), "start")


class PromptTests(unittest.TestCase):
    def test_the_prompt_and_root_are_substituted_whole(self):
        argv = R.expand_agent_cmd(["agent", "-p", "{prompt}", "--cwd", "{root}"], "/r o")
        self.assertEqual(argv[:2], ["agent", "-p"])
        self.assertIn("conductor resume --root '/r o'", argv[2])
        self.assertEqual(argv[3:], ["--cwd", "/r o"])
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `python3 factory-conductor/assets/test_conductor_reentry.py`
Expected: FAIL with `AttributeError: module 'reentry' has no attribute 'decide'`

- [ ] **Step 3: Implement** in `reentry.py`:

```python
import shlex, subprocess

RESUME_PROMPT = (
    "You are resuming a factory-conductor run in {root}. Load the factory-conductor skill. "
    "Run `conductor resume --root {root}` and do exactly what each NEXT: line says, as the "
    "skill's resume section describes, until resume prints `NEXT: run done` or "
    "`NEXT: run ask`. On `NEXT: run ask`, stop: do not finish the run and do not ask "
    "anyone. Do nothing the skill does not tell you to do.")


def decide(finished, stopped_reason, covered, block, live, idle_min, count):
    """The watch decision, first failing check wins (spec section 3)."""
    if finished:
        return "done"
    if stopped_reason == "grant_ask":
        return "waiting-human"
    if not covered:
        return "ask"
    if not block:
        return "disabled"
    if live:
        return "live"
    if idle_min < block.get("stall_min", 30):
        return "not-stalled"
    if count >= block.get("max_reentries", 5):
        return "exhausted"
    return "start"


def expand_agent_cmd(argv, root):
    prompt = RESUME_PROMPT.format(root=shlex.quote(root))
    return [prompt if a == "{prompt}" else root if a == "{root}" else a for a in argv]


def spawn(argv, root, log_path, n):
    """Start the agent detached in root; its conductor commands keep the reentry lease."""
    env = dict(os.environ, **{ENV_REENTRY: str(n)})
    with open(log_path, "ab") as out:
        p = subprocess.Popen(argv, cwd=root, stdin=subprocess.DEVNULL, stdout=out,
                             stderr=subprocess.STDOUT, env=env, start_new_session=True)
    return p.pid
```

In the `decide` body, the fallback defaults MUST read `CC.REENTRY_DEFAULTS`, not literals. `reentry.py` imports the vendored checker the same way `conductor.py` does (`import contract_check as CC`, with `sys.path` set by the caller). Replace `block.get("stall_min", 30)` with `block.get("stall_min", CC.REENTRY_DEFAULTS["stall_min"])`, and the same for `max_reentries`.

- [ ] **Step 4: Write the failing command tests.** Stub agents write a marker file, so the tests can see what started. Add to `test_conductor_reentry.py`:

```python
from conductor_testkit import GIT, new_run, repo, revoke, write_grant, write_text
import conductor as C

def setUpModule():
    # watch uninstalls a finished run's timer: keep every timer call in a temp home
    os.environ["FACTORY_CONDUCTOR_TIMER_HOME"] = tmpdir()
    os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN"] = "1"
    os.environ["FACTORY_CONDUCTOR_TIMER_KIND"] = "cron"


STUB = ("import os,sys,time;open(sys.argv[1],'a').write(os.environ.get("
        "'FACTORY_CONDUCTOR_REENTRY','?')+'\\n');time.sleep(float(sys.argv[2]))")


def run_watch(root):
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = C.main(["watch", "--root", root])
    return rc, buf.getvalue().strip()


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.root = repo()
        plan = {"title": "T", "spec": "s.md", "coverage": {}, "uncovered": [],
                "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "a",
                           "verify": [{"text": "t", "command": ["true"]}],
                           "depends_on": []}]}
        self.st, self.plan = new_run(self.root, plan)
        self.marker = os.path.join(tmpdir(), "started")

    def grant(self, sleep=30, **block):
        b = {"agent_cmd": [sys.executable, "-c", STUB, self.marker, str(sleep), "{prompt}"],
             "stall_min": 30, "max_reentries": 2}
        b.update(block)
        write_grant(self.root, self.plan, reentry=b)

    def age(self, minutes=45):
        old = time.time() - minutes * 60
        for name in (self.st.log_path, os.path.join(self.st.dir, R.LEASE_FILE)):
            if os.path.exists(name):
                os.utime(name, (old, old))
        R.renew_lease(self.st.dir, now=utc(minutes))
        # rewrite the last log line's timestamp too: watch reads the last event's `at`
        with open(self.st.log_path, "a") as f:
            f.write(json.dumps({"at": utc(minutes).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "event": "aged"}) + "\n")

    def started(self):
        time.sleep(0.5)
        return open(self.marker).read().split() if os.path.exists(self.marker) else []

    def test_no_run(self):
        self.assertEqual(run_watch(repo()), (0, "REENTRY: no-run"))

    def test_disabled_without_a_block(self):
        write_grant(self.root, self.plan)
        self.age()
        self.assertEqual(run_watch(self.root), (0, "REENTRY: disabled"))

    def test_a_fresh_run_is_not_stalled(self):
        self.grant()  # no lease yet (new_run skips main), but init was logged just now
        self.assertEqual(run_watch(self.root), (0, "REENTRY: not-stalled"))

    def test_a_live_session_lease_blocks_re_entry(self):
        self.grant()
        self.age()
        R.renew_lease(self.st.dir)  # a session just ran a conductor command
        self.assertEqual(run_watch(self.root), (0, "REENTRY: live"))
        self.assertEqual(self.started(), [])

    def test_a_stalled_run_starts_the_agent_once(self):
        self.grant()
        self.age()
        rc, out = run_watch(self.root)
        self.assertEqual(rc, 0)
        self.assertRegex(out, r"^REENTRY: started 1 pid=\d+$")
        self.assertEqual(self.started(), ["1"])
        self.assertEqual(run_watch(self.root), (0, "REENTRY: live"))  # its pid lives
        ev = [json.loads(l) for l in open(self.st.log_path) if '"reentry"' in l]
        self.assertEqual((ev[-1]["n"], ev[-1]["ok"]), (1, True))
        self.assertNotIn(R.RESUME_PROMPT[:20], json.dumps(ev[-1]))  # prompt elided

    def test_waiting_on_the_human_starts_nothing(self):
        self.grant()
        st = C.State.load(self.st.state_path)
        st.stopped = {"reason": "grant_ask", "at": "t", "detail": "x"}
        st.save()
        self.age()
        self.assertEqual(run_watch(self.root), (0, "REENTRY: waiting-human"))
        self.assertEqual(self.started(), [])

    def test_a_revoked_grant_starts_nothing(self):
        self.grant()
        revoke(self.root)
        self.age()
        self.assertEqual(run_watch(self.root), (0, "REENTRY: ask"))
        self.assertEqual(self.started(), [])

    def test_exhaustion_records_a_stop(self):
        self.grant(sleep=0, max_reentries=1)
        self.age(); run_watch(self.root); time.sleep(0.5)
        self.age()
        self.assertEqual(run_watch(self.root), (0, "REENTRY: exhausted"))
        self.assertEqual(C.State.load(self.st.state_path).stopped["reason"], "reentry_exhausted")

    def test_a_missing_binary_fails_and_still_counts(self):
        self.grant(agent_cmd=["/nonexistent/agent", "{prompt}"], max_reentries=1)
        self.age()
        rc, out = run_watch(self.root)
        self.assertEqual((rc, out), (2, "REENTRY: failed"))
        self.age()
        self.assertEqual(run_watch(self.root)[1], "REENTRY: exhausted")

    def test_overlapping_watches_start_one_agent(self):
        self.grant()
        self.age()
        here = os.path.dirname(os.path.abspath(C.__file__))
        code = ("import sys;sys.path.insert(0,%r);import conductor as C;"
                "sys.exit(C.main(['watch','--root',%r]))" % (here, self.root))
        ps = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
              for _ in range(3)]
        outs = [p.communicate()[0].strip() for p in ps]
        self.assertEqual(sum(o.startswith("REENTRY: started") for o in outs), 1, outs)
        self.assertEqual(self.started(), ["1"])
```

`write_grant` gains a `reentry=None` keyword. When it is set, the helper adds `pay["reentry"] = reentry`. Make that change in `conductor_testkit.py` in this step.

- [ ] **Step 5: Implement `cmd_watch`** in `conductor.py`:

```python
def _idle_min(st):
    """Minutes since the last complete log event (a huge number when there is none)."""
    last = st.last_event()
    try:
        at = _dt.datetime.strptime(last["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except (TypeError, KeyError, ValueError):
        return 10 ** 6
    return (_now() - at).total_seconds() / 60


def _reentry_block(root):
    """The covering grant's reentry block (defaults filled in), or None."""
    path = CC.latest_grant(root)
    doc, err = CC.load_envelope(path) if path else (None, "no grant")
    block = ((doc or {}).get("predicate") or {}).get("payload", {}).get("reentry")
    if err or not block or CC.reentry_problems(block):
        return None
    return dict(CC.REENTRY_DEFAULTS, **block)


def cmd_watch(args):
    """One re-entry check (spec: scheduled re-entry, section 3); one REENTRY: line."""
    root = os.path.abspath(args.root)
    d = current_run(root)
    if d is None:
        print("REENTRY: no-run")
        return 0
    try:
        with R.run_lock(d):
            return _watch_locked(root, d)
    except R.RunLocked:
        print("REENTRY: live")  # another conductor command is running right now
        return 0


def _watch_locked(root, d):
    st = State.load(os.path.join(d, STATE_FILE))
    block = _reentry_block(root)
    rc, last = gate(root, "local_reversible", plan_subject(st))
    covered, _ = gate_line(rc, last)  # the same judgment every gate uses; nothing logged
    worktrees = [t["worktree"] for t in st.tasks.values()
                 if t.get("status") in ("running", "verifying") and t.get("worktree")]
    stall = (block or CC.REENTRY_DEFAULTS)["stall_min"]
    live = R.lease_live(d, R.read_lease(d), stall, worktrees)
    count = sum(1 for e in _log_events(st) if e.get("event") == "reentry")
    verdict = R.decide(bool(st.finished), (st.stopped or {}).get("reason"), covered, block,
                       live, _idle_min(st), count)
    if verdict == "done":
        import reentry_timer as T
        T.uninstall(st.run_id)
    if verdict == "exhausted" and (st.stopped or {}).get("reason") != "reentry_exhausted":
        _record_stop(st, "reentry_exhausted", detail="max_reentries=%d" % block["max_reentries"])
    if verdict != "start":
        print("REENTRY: %s" % verdict)
        return 0
    n = count + 1
    argv = R.expand_agent_cmd(block["agent_cmd"], root)
    shown = ["{prompt}" if a == R.expand_agent_cmd(["{prompt}"], root)[0] else a for a in argv]
    try:
        pid = R.spawn(argv, root, os.path.join(d, "reentry-%d.log" % n), n)
    except OSError as e:
        st.log("reentry", n=n, ok=False, argv=shown, error=str(e))
        print("REENTRY: failed")
        return 2
    R.set_reentry_lease(d, n, pid)
    st.log("reentry", n=n, ok=True, argv=shown, pid=pid)
    print("REENTRY: started %d pid=%d" % (n, pid))
    return 0
```

Notes on this step:
- `_log_events(st)` reads every complete line of `st.log_path` as JSON and skips a torn one. Add it next to `State.last_event` if no such helper exists.
- Add `reentry_exhausted` to `STOP_REASONS` and to the schema enum.
- Register the subparser in `main()`, with `set_defaults(no_lock=True)`, because watch takes the lock itself:

```python
    s = sub.add_parser("watch")
    s.add_argument("--root", default=".")
    s.set_defaults(fn=cmd_watch, no_lock=True)
```

`watch` must not renew the lease; that is why `no_lock=True` skips the wrapper's `renew_lease`. `reentry_timer` is created in Task 4. Until then, create a stub `factory-conductor/assets/reentry_timer.py` containing only:

```python
def uninstall(run_id, **kw):
    return []
```

- [ ] **Step 6: Run the tests**

Run: `for t in factory-conductor/assets/test_conductor_*.py; do python3 $t || exit 1; done`
Expected: all OK. The `WatchTests` cases pass.

- [ ] **Step 7: Commit**

```bash
git add factory-conductor/assets
git commit -m "feat(factory-conductor): conductor watch re-enters a stalled run (reentry_exhausted stop)"
```

---

### Task 4: Installing, removing and reporting the timer

**Files:**
- Replace the stub: `factory-conductor/assets/reentry_timer.py`
- Modify: `factory-conductor/assets/conductor.py` (the `reentry install|uninstall|status` subcommand)
- Test: `factory-conductor/assets/test_reentry_timer.py` (new)

**Interfaces:**
- Consumes: `_reentry_block(root)`, `gate`, `plan_subject` and `current_run`.
- Produces:
  - `reentry_timer.platform_kind() -> "launchd"|"systemd"|"cron"|None`
  - `render(kind, run_id, argv, interval_min) -> dict[filename, str]`
  - `install(run_id, argv, interval_min, kind=None) -> list[str]` (the commands it ran)
  - `uninstall(run_id, kind=None) -> list[str]`
  - `installed(run_id, kind=None) -> list[str]` (the paths or lines found)
- Test overrides are environment variables:
  - `FACTORY_CONDUCTOR_TIMER_HOME`, a directory that stands in for `~`;
  - `FACTORY_CONDUCTOR_TIMER_DRYRUN=1`, which records the loader commands instead of running them and keeps the crontab in `<home>/crontab.txt`.

- [ ] **Step 1: Write the failing tests** in `factory-conductor/assets/test_reentry_timer.py`:

```python
import os, plistlib, shlex, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reentry_timer as T
from conductor_testkit import tmpdir

RID = "run-20260925T000000Z-abcdef"
ARGV = ["/usr/bin/python3", "/opt/my skills/conductor.py", "watch", "--root", "/w/it's repo"]


class TimerTests(unittest.TestCase):
    def setUp(self):
        self.home = tmpdir()
        os.environ["FACTORY_CONDUCTOR_TIMER_HOME"] = self.home
        os.environ["FACTORY_CONDUCTOR_TIMER_DRYRUN"] = "1"

    def tearDown(self):
        for k in ("FACTORY_CONDUCTOR_TIMER_HOME", "FACTORY_CONDUCTOR_TIMER_DRYRUN"):
            os.environ.pop(k, None)

    def test_launchd_plist_carries_the_argv_array_and_interval(self):
        files = T.render("launchd", RID, ARGV, 10)
        (name, text), = files.items()
        self.assertEqual(name, "io.agent-skills.factory-conductor.%s.plist" % RID)
        pl = plistlib.loads(text.encode())
        self.assertEqual((pl["ProgramArguments"], pl["StartInterval"]), (ARGV, 600))

    def test_systemd_quotes_paths_with_spaces_and_quotes(self):
        files = T.render("systemd", RID, ARGV, 10)
        svc = files["factory-conductor-%s.service" % RID]
        line = [l for l in svc.splitlines() if l.startswith("ExecStart=")][0]
        self.assertEqual(shlex.split(line[len("ExecStart="):]), ARGV)
        self.assertIn("OnUnitActiveSec=10min", files["factory-conductor-%s.timer" % RID])

    def test_cron_line_is_tagged_and_quoted(self):
        (_, line), = T.render("cron", RID, ARGV, 10).items()
        self.assertTrue(line.endswith("# factory-conductor %s" % RID))
        self.assertTrue(line.startswith("*/10 * * * * "))
        self.assertEqual(shlex.split(line.split(" # ")[0])[5:], ARGV)

    def test_install_then_uninstall_leaves_nothing(self):
        for kind in ("launchd", "systemd", "cron"):
            with self.subTest(kind=kind):
                T.install(RID, ARGV, 10, kind=kind)
                self.assertTrue(T.installed(RID, kind=kind))
                T.uninstall(RID, kind=kind)
                self.assertEqual(T.installed(RID, kind=kind), [])

    def test_uninstall_keeps_other_runs_cron_lines(self):
        other = "run-20260925T000001Z-000000"
        T.install(RID, ARGV, 10, kind="cron")
        T.install(other, ARGV, 10, kind="cron")
        T.uninstall(RID, kind="cron")
        self.assertEqual(len(T.installed(other, kind="cron")), 1)

    def test_install_is_idempotent(self):
        T.install(RID, ARGV, 10, kind="cron")
        T.install(RID, ARGV, 10, kind="cron")
        self.assertEqual(len(T.installed(RID, kind="cron")), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `python3 factory-conductor/assets/test_reentry_timer.py`
Expected: FAIL with `AttributeError: module 'reentry_timer' has no attribute 'render'`

- [ ] **Step 3: Implement** `reentry_timer.py`:

```python
"""One OS timer per factory-conductor run that calls `conductor watch` every
interval_min: launchd (macOS), a systemd user timer, or a tagged crontab line. Tests
set FACTORY_CONDUCTOR_TIMER_HOME and FACTORY_CONDUCTOR_TIMER_DRYRUN, and the real
system is never touched."""
import os, plistlib, shlex, shutil, subprocess, sys

LABEL = "io.agent-skills.factory-conductor.%s"
UNIT = "factory-conductor-%s"
TAG = "# factory-conductor %s"


def _home():
    return os.environ.get("FACTORY_CONDUCTOR_TIMER_HOME") or os.path.expanduser("~")


def _dry():
    return os.environ.get("FACTORY_CONDUCTOR_TIMER_DRYRUN") == "1"


def platform_kind():
    forced = os.environ.get("FACTORY_CONDUCTOR_TIMER_KIND")
    if forced in ("launchd", "systemd", "cron"):
        return forced
    if sys.platform == "darwin":
        return "launchd"
    if sys.platform.startswith("linux"):
        if shutil.which("systemctl") and subprocess.run(
                ["systemctl", "--user", "show-environment"], capture_output=True).returncode == 0:
            return "systemd"
        if shutil.which("crontab"):
            return "cron"
    return None


def _dir(kind):
    return {"launchd": os.path.join(_home(), "Library", "LaunchAgents"),
            "systemd": os.path.join(_home(), ".config", "systemd", "user")}[kind]


def render(kind, run_id, argv, interval_min):
    if kind == "launchd":
        pl = {"Label": LABEL % run_id, "ProgramArguments": list(argv),
              "StartInterval": interval_min * 60, "RunAtLoad": False}
        return {LABEL % run_id + ".plist": plistlib.dumps(pl).decode()}
    if kind == "systemd":
        u = UNIT % run_id
        return {u + ".service": "[Unit]\nDescription=factory-conductor watch %s\n\n"
                                "[Service]\nType=oneshot\nExecStart=%s\n"
                                % (run_id, shlex.join(argv)),
                u + ".timer": "[Unit]\nDescription=factory-conductor watch %s\n\n"
                              "[Timer]\nOnBootSec=%dmin\nOnUnitActiveSec=%dmin\n\n"
                              "[Install]\nWantedBy=timers.target\n"
                              % (run_id, interval_min, interval_min)}
    if kind == "cron":
        return {"crontab": "*/%d * * * * %s %s" % (interval_min, shlex.join(argv), TAG % run_id)}
    raise ValueError("unsupported timer kind: %r" % kind)


def _run(cmd, ran):
    ran.append(shlex.join(cmd))
    if not _dry():
        subprocess.run(cmd, capture_output=True)


def _crontab_read():
    if _dry():
        p = os.path.join(_home(), "crontab.txt")
        return open(p).read().splitlines() if os.path.exists(p) else []
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout.splitlines() if r.returncode == 0 else []


def _crontab_write(lines, ran):
    text = "".join(l + "\n" for l in lines)
    ran.append("crontab -")
    if _dry():
        with open(os.path.join(_home(), "crontab.txt"), "w") as f:
            f.write(text)
    else:
        subprocess.run(["crontab", "-"], input=text, text=True, capture_output=True)


def install(run_id, argv, interval_min, kind=None):
    kind = kind or platform_kind()
    if kind is None:
        raise OSError("unsupported platform for scheduled re-entry")
    uninstall(run_id, kind=kind)
    ran = []
    files = render(kind, run_id, argv, interval_min)
    if kind == "cron":
        _crontab_write(_crontab_read() + [files["crontab"]], ran)
        return ran
    os.makedirs(_dir(kind), exist_ok=True)
    for name, text in files.items():
        with open(os.path.join(_dir(kind), name), "w") as f:
            f.write(text)
    if kind == "launchd":
        _run(["launchctl", "bootstrap", "gui/%d" % os.getuid(),
              os.path.join(_dir(kind), LABEL % run_id + ".plist")], ran)
    else:
        _run(["systemctl", "--user", "daemon-reload"], ran)
        _run(["systemctl", "--user", "enable", "--now", UNIT % run_id + ".timer"], ran)
    return ran


def installed(run_id, kind=None):
    kind = kind or platform_kind()
    if kind == "cron":
        return [l for l in _crontab_read() if l.endswith(TAG % run_id)]
    if kind is None:
        return []
    return [os.path.join(_dir(kind), n) for n in render(kind, run_id, ["x"], 5)
            if os.path.exists(os.path.join(_dir(kind), n))]


def uninstall(run_id, kind=None):
    kind = kind or platform_kind()
    ran = []
    if kind == "cron":
        lines = _crontab_read()
        keep = [l for l in lines if not l.endswith(TAG % run_id)]
        if keep != lines:
            _crontab_write(keep, ran)
        return ran
    if kind is None:
        return ran
    present = installed(run_id, kind=kind)
    if kind == "launchd" and present:
        _run(["launchctl", "bootout", "gui/%d/%s" % (os.getuid(), LABEL % run_id)], ran)
    if kind == "systemd" and present:
        _run(["systemctl", "--user", "disable", "--now", UNIT % run_id + ".timer"], ran)
    for p in present:
        os.remove(p)
    return ran
```

- [ ] **Step 4: Add the `reentry` subcommand** to `conductor.py`:

```python
def watch_argv(root):
    """The absolute argv a timer runs: this interpreter, this conductor.py, watch."""
    return [sys.executable, os.path.abspath(__file__), "watch", "--root", os.path.abspath(root)]


def cmd_reentry(args):
    """reentry install|uninstall|status: the run's watch timer (install needs the grant's
    reentry block and a COVERED local_reversible gate)."""
    import reentry_timer as T
    root = os.path.abspath(args.root)
    st = _load_current(root)
    if st is None:
        return 2
    if args.action == "status":
        print("REENTRY: timer %s" % (", ".join(T.installed(st.run_id)) or "none"))
        print("REENTRY: lease %s" % json.dumps(R.read_lease(st.dir), sort_keys=True))
        print("REENTRY: count %d" % sum(1 for e in _log_events(st) if e.get("event") == "reentry"))
        return 0
    if args.action == "uninstall":
        T.uninstall(st.run_id)
        st.log("reentry_timer", action="uninstall")
        print("REENTRY: uninstalled")
        return 0
    block = _reentry_block(root)
    if block is None:
        print("REENTRY: disabled")
        return 3
    if not _gated(st, "local_reversible"):
        return 3
    try:
        T.install(st.run_id, watch_argv(root), block["interval_min"])
    except OSError as e:
        sys.stderr.write("%s\n" % e)
        print("REENTRY: unsupported platform")
        return 2
    st.log("reentry_timer", action="install", interval_min=block["interval_min"])
    print("REENTRY: installed every %d min" % block["interval_min"])
    return 0
```

Register it in `main()`:

```python
    s = sub.add_parser("reentry")
    s.add_argument("action", choices=("install", "uninstall", "status"))
    s.add_argument("--root", default=".")
    s.set_defaults(fn=cmd_reentry)
```

Add command tests to `test_conductor_reentry.py` (`ReentryCommandTests`), with the two env overrides set:
- `install` with no block gives `REENTRY: disabled`, exit 3.
- `install` with a block gives `REENTRY: installed every 10 min`, and `installed()` finds the timer.
- `status` prints the three lines.
- `uninstall` leaves `installed()` empty.
- After the run is finished (set `st.finished` and save), `watch` prints `REENTRY: done` and uninstalls the timer.

Pin the kind in these tests with `FACTORY_CONDUCTOR_TIMER_KIND=cron`. Add that override to `platform_kind()`: `if os.environ.get("FACTORY_CONDUCTOR_TIMER_KIND"): return it`.

- [ ] **Step 5: Run the tests**

Run: `python3 factory-conductor/assets/test_reentry_timer.py && python3 factory-conductor/assets/test_conductor_reentry.py`
Expected: OK, and nothing is written outside the temp home.

- [ ] **Step 6: Commit**

```bash
git add factory-conductor/assets
git commit -m "feat(factory-conductor): reentry install|uninstall|status (launchd, systemd user, cron)"
```

---

### Task 5: spec-first-planning asks for and writes the `reentry` block

**Files:**
- Modify: `spec-first-planning/assets/write_grant.py` (accept `answers.reentry`, validate it with the vendored `CC.reentry_problems`, print `REENTRY: <argv>`)
- Modify: `spec-first-planning/references/unattended.md` (add a step 7 question)
- Modify: `spec-first-planning/SKILL.md` (one sentence in the unattended section; bump `metadata.version` and `SKILL_VERSION` to 2.3.0 in `spec_to_tasks.py` and its test)
- Test: `spec-first-planning/assets/test_write_grant.py`

**Interfaces:**
- Consumes (Task 1): `CC.reentry_problems` and `CC.REENTRY_DEFAULTS`.
- Produces: a grant payload with `reentry` when `answers.reentry` is given. The output adds one `REENTRY: <shlex-joined argv> every <n> min` line.

- [ ] **Step 1: Write the failing tests** in `test_write_grant.py`. Follow the file's existing pattern for building `answers` and calling `main([...])`:

```python
    def test_a_reentry_block_is_written_and_shown_back(self):
        answers = self.answers(reentry={"agent_cmd": ["claude", "-p", "{prompt}"],
                                        "interval_min": 10})
        out, st = self.run_write(answers)
        self.assertEqual(st["predicate"]["payload"]["reentry"]["agent_cmd"],
                         ["claude", "-p", "{prompt}"])
        self.assertIn("REENTRY: claude -p '{prompt}' every 10 min", out)

    def test_a_shell_agent_cmd_is_refused(self):
        with self.assertRaises(SystemExit):
            self.run_write(self.answers(reentry={"agent_cmd": ["bash", "-c", "{prompt}"]}))

    def test_no_reentry_means_no_block(self):
        _, st = self.run_write(self.answers())
        self.assertNotIn("reentry", st["predicate"]["payload"])
```

`self.answers(**over)` and `self.run_write(answers)` are the test class's existing helpers. If they're named differently, use those helpers and add the `reentry` key to the answers dict.

- [ ] **Step 2: Run the tests and see them fail**

Run: `python3 spec-first-planning/assets/test_write_grant.py`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement.** In `write_grant.py`:
  - Add `"reentry"` to the allowed-answer-keys tuple, around line 59.
  - In the answers validation block, next to the `budget` and `stop_on` checks, add:

```python
    reentry = answers.get("reentry")
    if reentry is not None:
        problems = contract_check.reentry_problems(reentry)
        if problems:
            raise GrantRefused("answers.reentry: " + "; ".join(problems))
```

  - In the payload dict, around line 205, add `**({"reentry": dict(contract_check.REENTRY_DEFAULTS, **reentry)} if reentry else {})`.
  - After the envelope is written, print:

```python
    if reentry:
        r = dict(contract_check.REENTRY_DEFAULTS, **reentry)
        print("REENTRY: %s every %d min" % (shlex.join(r["agent_cmd"]), r["interval_min"]))
```

- [ ] **Step 4: Update the docs.** In `references/unattended.md`, add to step 7 (the grant), after the budget question:

```markdown
- **Re-entry (optional).** Ask: "If this session dies, should a timer on this machine
  resume the run? If so, give the exact agent command, as an argv list with `{prompt}`
  where the resume prompt goes." Show the argv back before the yes. It becomes
  `answers.reentry` (`agent_cmd`, optional `interval_min` 10, `stall_min` 30,
  `max_reentries` 5). Recommend the user's agent in headless mode, with an explicit tool
  allowlist, e.g. `["claude", "-p", "{prompt}", "--allowedTools", "…"]`. Warn against
  any permission-bypass flag, because the resumed agent acts with the user's own
  permissions and no one watches it. Without an answer, no block is written and nothing
  re-enters.
```

In `SKILL.md`'s unattended section, add one plain sentence (no keyword): "The grant can also carry consent to scheduled re-entry (`answers.reentry`), so a timer resumes the run if the session dies; see `references/unattended.md`." Bump the version to 2.3.0 in `metadata.version`, in `SKILL_VERSION` in `spec_to_tasks.py`, and in any test that pins it. Update the README version mention.

- [ ] **Step 5: Run the skill gate**

Run: `make gate-skill SKILL=spec-first-planning`
Expected: every check PASSes. If the `SKILL.md` edit shifted any keyword line, refresh the register line numbers; `make bcp14` reports them.

- [ ] **Step 6: Commit**

```bash
git add spec-first-planning docs/rfc2119/2026-09-19-classification.md
git commit -m "feat(spec-first-planning): unattended grants can carry consent to scheduled re-entry"
```

---

### Task 6: The skill text, the end-to-end test and the eval

**Files:**
- Modify: `factory-conductor/SKILL.md` (a "Scheduled re-entry" section; the machine lines `REENTRY:`; the stop reason; honesty)
- Modify: `factory-conductor/references/run-protocol.md` (the `watch` and `reentry` commands, `lease.json`, `.lock`, the `reentry` and `reentry_timer` events, `reentry_exhausted`)
- Modify: `factory-conductor/README.md`, the root `README.md` ("Unattended: plan to PR": one step and one limit), `docs/rfc2119/2026-09-19-classification.md`
- Create: `factory-conductor/assets/test_conductor_reentry_e2e.py`
- Modify: `factory-conductor/eval/run_eval.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: the documented protocol. Eval checks named `reentry …`.

- [ ] **Step 1: Write the end-to-end test** (AC2), in `test_conductor_reentry_e2e.py`. The "agent" is a stdlib stub script that follows only `NEXT:` lines, exactly as a memoryless agent does:

```python
import json, os, subprocess, sys, time, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C, reentry as R
from conductor_testkit import GIT, new_run, repo, write_grant, write_text, tmpdir
from test_conductor_reentry import utc

HERE = os.path.dirname(os.path.abspath(__file__))
WALKER = r'''
import os, subprocess, sys
here, root = sys.argv[1], sys.argv[2]
def c(*a):
    r = subprocess.run([sys.executable, os.path.join(here, "conductor.py"), *a, "--root", root],
                       capture_output=True, text=True)
    return r.stdout.splitlines()
for _ in range(20):
    lines = [l for l in c("resume") if l.startswith(("NEXT:", "FINISH:"))]
    nexts = [l.split()[1:] for l in lines if l.startswith("NEXT:")]
    if ["run", "done"] in nexts or ["run", "ask"] in nexts:
        break
    for n in nexts:
        if n[0] == "run":
            c("next") if n[1] == "next" else c("finish", "--push-cmd", '["true"]', "--pr-cmd", '["true"]')
        elif n[1] == "dispatch-executor":
            wt = [l for l in c("status") if l.startswith("STATUS: %s " % n[0])]
            # the stub executor commits one file in the task's worktree
            d = os.path.join(root, ".skill-contract", "runs")
            run = sorted(os.listdir(d))[-1]
            w = os.path.join(d, run, "wt", n[0])
            open(os.path.join(w, n[0] + ".txt"), "w").write("done\n")
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
            subprocess.run(["git", "-C", w, "add", "-A"], env=env)
            subprocess.run(["git", "-C", w, "commit", "-q", "-m", n[0]], env=env)
            c("verify", n[0])
        elif n[1] == "dispatch-reviewer":
            c("review", n[0], "--verdict", "pass")
        elif n[1] == "merge":
            c("merge", n[0])
        elif n[1] == "verify":
            c("verify", n[0])
'''


class ReentryEndToEnd(unittest.TestCase):
    def test_a_killed_session_is_resumed_to_finish_by_watch(self):
        root = repo()
        plan = {"title": "T", "spec": "s.md", "coverage": {}, "uncovered": [],
                "tasks": [{"id": "T1", "requirement_ids": ["R1"], "title": "a",
                           "verify": [{"text": "t", "command": ["true"]}], "depends_on": []}]}
        st, plan_env = new_run(root, plan)
        walker = os.path.join(tmpdir(), "walker.py")
        write_text(walker, WALKER)
        write_grant(root, plan_env, reentry={
            "agent_cmd": [sys.executable, walker, HERE, "{root}", "{prompt}"],
            "max_reentries": 2})
        # the "session" starts T1, then dies (nothing more happens)
        self.assertEqual(C.main(["start", "T1", "--root", root]), 0)
        # time passes: age the lease and the log beyond stall_min
        R.renew_lease(st.dir, now=utc(45))
        with open(st.log_path, "a") as f:
            f.write(json.dumps({"at": utc(45).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "event": "aged"}) + "\n")
        old = time.time() - 45 * 60
        for base, dirs, files in os.walk(os.path.join(st.dir, "wt")):
            for n in files + dirs:
                os.utime(os.path.join(base, n), (old, old), follow_symlinks=False)
        self.assertEqual(C.main(["watch", "--root", root]), 0)
        pid = R.read_lease(st.dir)["pid"]
        for _ in range(600):
            if not R.pid_alive(pid):
                break
            time.sleep(0.1)
        final = C.State.load(st.state_path)
        self.assertTrue(final.finished, open(os.path.join(st.dir, "reentry-1.log")).read())
        self.assertEqual(final.tasks["T1"]["status"], "proven")


if __name__ == "__main__":
    unittest.main()
```

The walker's `finish` uses a `["true"]` push command. Every one of the finish suite's stubs is refused by the push allowlist, so the grant here carries no `push_branch` or `open_pr`. The run then ends at `NEXT: run ask` for the remote steps, or at `run done`. The walker stops on either one; that is the terminal the protocol defines. Assert `final.finished` (the envelope was written) and T1 `proven`. Add a second `watch` call after the walker exits, and assert that it prints `REENTRY: done`.

- [ ] **Step 2: Run the test and see it fail.** The skill wiring is complete, so if it fails, the cause is a real bug in Tasks 2–4: fix it there.

Run: `python3 factory-conductor/assets/test_conductor_reentry_e2e.py`
Expected: PASS once Tasks 2–4 are right. If it fails, the `reentry-1.log` content shown in the assertion message explains why.

- [ ] **Step 3: Add the eval checks** in `eval/run_eval.py`. Each one reuses the eval's existing fixture helpers and the `check(name, ok, detail)` protocol:
  - positive: `reentry: a stalled run with a reentry block starts exactly one agent` (the stub marker holds one line);
  - `NEGATIVE: reentry never starts while a reentry lease's pid lives`;
  - `NEGATIVE: reentry never starts on grant_ask (waiting on the human)`;
  - `NEGATIVE: reentry never starts under a revoked grant`;
  - `NEGATIVE: reentry never starts beyond max_reentries (and records reentry_exhausted)`;
  - `NEGATIVE: a grant whose agent_cmd is a shell is invalid (no re-entry)`.

  Keep the eval under 120 s: stubs sleep at most 2 s, and temp homes are used for timers.

- [ ] **Step 4: Write the docs.**

Add a SKILL.md section after the resume section:

```markdown
## Scheduled re-entry

When the grant carries a `reentry` block, run `"$SKILL_DIR/assets/conductor.py" reentry
install --root <repo>` right after `init`, so a timer on this machine resumes the run if
this session dies. The timer calls `conductor watch` every `interval_min`. `watch` starts
the user's own agent command, with a fixed resume prompt, only when the run is not
finished, not waiting on the human, still covered by the grant, not driven by a live
session, idle for `stall_min`, and under `max_reentries`. It prints one `REENTRY:` line.
Every conductor command takes the run lock and renews the lease, so two drivers never
write at once. You MUST NOT edit `lease.json` or `.lock`, because they are how a live
session tells the timer not to start a second driver. `conductor reentry uninstall`
removes the timer at once, and a finished run removes its own.

Re-entry needs this machine on and the user logged in, and it runs the user's own agent
command with the user's own permissions: it is not a hosted service. A session that is
alive but has made no conductor call and no worktree change for `stall_min` looks
stalled, and can get a second driver. The run lock prevents corruption, and the
existing guards limit the cost to wasted dispatches.
```

- Add a register row for the new MUST NOT, marked `not judged (post-backfill)`, and refresh the line numbers.
- In run-protocol.md, add rows for `watch` and `reentry` to the command table with every `REENTRY:` word and exit code. Document `lease.json`, `.lock`, the `reentry` and `reentry_timer` log events, and `reentry_exhausted`.
- In the root README recipe, add the step "(optional) consent to re-entry in the grant; the conductor installs a timer", and the limit "re-entry runs on this machine and needs it on".

- [ ] **Step 5: Run the skill gate**

Run: `make gate-skill SKILL=factory-conductor && make bcp14 && make readme`
Expected: every check PASSes, the eval passes, and PP-5 shows 0 unreasoned absolutes.

- [ ] **Step 6: Commit**

```bash
git add factory-conductor README.md docs/rfc2119/2026-09-19-classification.md
git commit -m "docs(factory-conductor): scheduled re-entry protocol; e2e and eval negatives"
```

---

### Task 7: A/B rows, the full gate and the Docker check

**Files:**
- Modify: `scripts/ab-validate.py` (add `SINCE_REENTRY` and `check_factory_conductor_reentry`)
- Modify: `docs/factory/2026-09-19-assessment.md` (one bullet: re-entry landed, to be re-judged after merge)

**Interfaces:**
- Consumes: the tree's own `conductor.py watch` and the testkit-equivalent inline fixtures that already exist in `ab-validate.py` (`_fc_fixture` and `_fc_write_grant`).
- Produces: the rows "stalled runs resumed without the human" (0 → 1) and four guards (lease, grant_ask, revoked, exhausted) at 0 → 0, each with a sanity arm.

- [ ] **Step 1: Add the rows.** Set `SINCE_REENTRY` to the Task 2 commit (`git log --format=%h --grep="a run lock and a lease" -1`).
  - **Delta row.** Use `_fc_fixture` plus `_fc_write_grant`, with a `reentry` block whose `agent_cmd` is a marker-writing stub. Age the lease and the log, run the tree's `conductor.py watch`, and score 1 when the marker file appears.
  - **Old tree.** Its conductor has no `watch`: a usage error or exit 2 scores an honest 0, not a probe error. That is a real 0 → 1 move.
  - **Guard rows.** Set up the same fixture in each guard state. Each guard's sanity arm is the delta row's positive result in the new tree: if the new tree does not start the agent on the healthy fixture, append to `PROBE_ERRORS` and return None.

- [ ] **Step 2: Add the assessment bullet**, then run everything:

Run: `make gate > /tmp/gate.log 2>&1; tail -3 /tmp/gate.log; make ab-validate; make readme`
Expected: `GATE_RESULT: PASS`, and `AB_RESULT: PASS` with 0 WORSE, 0 UNPROVEN, and the new row IMPROVED.

- [ ] **Step 3: Run the CI-equivalent gate** as non-root, with node and no git identity:

Run: `docker run --rm --platform linux/amd64 -u 1000:1000 -e HOME=/tmp -v "$PWD":/src:ro nikolaik/python-nodejs:python3.12-nodejs22 bash -c 'cp -r /src /tmp/w && cd /tmp/w && make gate' 2>&1 | tail -3`
Expected: `GATE_RESULT: PASS`. The timer tests use `FACTORY_CONDUCTOR_TIMER_DRYRUN`, so no systemd is needed in the container.

- [ ] **Step 4: Commit**

```bash
git add scripts/ab-validate.py docs/factory/2026-09-19-assessment.md
git commit -m "test(ab-validate): scheduled re-entry rows; assessment status"
```

---

## Jev confidence per section (`jev-1.13.0`, 2026-09-25)

Question: will an engineer following this section as written produce working, tested code that satisfies the spec, without significant rework? The scores were re-run after the self-review fixes. Each section also got a biggest-risk choice.

| Section | P(yes) | Biggest risk (Jev) |
|---|---|---|
| Header and constraints | 0.35 | spec gap, 0.45. The known one is the `answers.reentry` ruling in place of the spec's flags. |
| Task 1: checker | 0.76 | code bug, 0.32 (low confidence) |
| Task 2: lock and lease | 0.36 | code bug, 0.78 |
| Task 3: watch | 0.32 | code bug, 0.43 |
| Task 4: timers | 0.53 | code bug, 0.48 |
| Task 5: spec-first-planning | 0.65 | integration, 0.66: `write_grant.py`'s real helper names |
| Task 6: docs, e2e, eval | 0.41 | code bug, 0.47 / test gap, 0.30 |
| Task 7: A/B and gates | 0.49 | underspecified, 0.43 |

**How to execute with these scores:**
- Tasks 2, 3, 4 and 6 are the risk centre. The concurrent-process lock, lease liveness and the headless walker are subtle, so they get the most capable implementer and an adversarial task review that runs the code.
- The plan's code is a strong starting point, not a transcript. An implementer MUST make each task's own tests pass, and fix the code rather than weaken a test.

## Self-review notes

- **Spec coverage:**

  | Spec section | Task(s) |
  |---|---|
  | §1 consent block | 1 (checker), 5 (interview and `write_grant`) |
  | §2 lock and lease | 2 |
  | §3 watch, prompt and spawn | 3 |
  | §4 timers | 4 |
  | §5 stop, budget and honesty | 3 (`reentry_exhausted`), 6 (text) |
  | AC1 | 2, 3, 4 |
  | AC2 | 6 |
  | AC3 | 6 |
  | AC4 | 7 |
  | AC5 | 1 |
  | AC6 | 5 |
  | AC7 | 6, 7 |
  | AC8 | after merge, run by the controller |

- **Names used across tasks:**
  - Module `R` (`reentry`): `run_lock`, `RunLocked`, `LOCK_TIMEOUT`, `LEASE_FILE`, `read_lease`, `renew_lease`, `set_reentry_lease`, `lease_live`, `pid_alive`, `decide`, `expand_agent_cmd`, `spawn`, `RESUME_PROMPT`, `ENV_REENTRY`.
  - Module `T` (`reentry_timer`): `render`, `install`, `uninstall`, `installed`, `platform_kind`.
  - Checker `CC`: `reentry_problems`, `REENTRY_DEFAULTS`.
  - Conductor functions: `cmd_watch`, `_watch_locked`, `_reentry_block`, `_idle_min`, `_log_events`, `cmd_reentry`, `watch_argv`.
  - Testkit: `write_grant(…, reentry=None)`.
- **Known judgment calls for implementers:**
  - the exact `_log_events` helper name, if one already exists;
  - how the eval reuses its fixtures;
  - how the A/B probe times the stub's marker.
