# Portability Retrofit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every skill that runs Python works on any POSIX machine with Python 3.10+, whatever the interpreter is called, and never runs the macOS `/usr/bin/python3` install stub.

**Architecture:** One lookup in three places that agree: skill-contract's `resolve_python()` (code, vendored to 8 adopters), a POSIX `sh` function `find_python` for hooks and installers (code, 3 byte-identical copies), and one byte-identical prose paragraph in each SKILL.md that runs Python (prompt, enforced present by a new gate). Commands keep the literal `python3`, so permission rules keep matching on the common machine.

**Tech Stack:** Python 3.10+ stdlib, POSIX sh / bash, the repo's make gates.

**Spec:** `docs/superpowers/specs/2026-10-12-portability-retrofit-design.md`

## Global Constraints

- Lookup order: `SKILL_CONTRACT_PYTHON` (skill-contract only) → `SKILL_PYTHON` → `python3` unless the macOS stub → `python` → `py -3` (Python resolver and prose rule only; not the sh function).
- The stub test: darwin AND the candidate resolves to exactly `/usr/bin/python3` AND `xcode-select -p` exits non-zero. A stub candidate is skipped without being run.
- "Passes" = `<interp> -c 'import sys; sys.exit(sys.version_info < (3, 10))'` exits 0. Hooks do not probe the version.
- Python is stdlib-only; tests are offline and deterministic.
- Never name the sh test switch (`_FIND_PYTHON_STUB`) in a SKILL.md, a `references/` file, usage text or a STOP line. Tests and READMEs may name it.
- No BCP 14 capitals in the shared rule (P7), so no register rows for it.
- No lowercase "must"/"shall" in SKILL.md prose (bcp14 gate).
- Write asset paths in SKILL.md as `"$SKILL_DIR/assets/..."`.
- Human-facing text (READMEs, PR bodies, docs) at about 80% STE; never rewrite SKILL.md prompts into STE.
- Stage explicit paths only; never `git add -A`; never stage `.claude/`.
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0189WxKyoVEhUPtunz3Sbcvs
  ```
- Shell is zsh: use `$pipestatus[1]`, not `${PIPESTATUS[0]}`.
- Known local-only `make gate` failures on this Mac: `test-safety-net/assets/test_io_guard_rust.py` and `tmux-agent-herdr-lite/assets/test_cockpit_scripts.sh` (agent-jump). Anything else must pass.
- A/B rows go under `SINCE_PORTABILITY_*` constants in `scripts/ab-validate.py`, pointing at a commit in the change; repin after any rebase.

## Review Focus

1. A Homebrew/pyenv `python3` earlier on PATH on macOS, with no Command Line Tools: it must be used, not skipped (only the exact `/usr/bin/python3` is the stub). Pinned in Task 1 and Task 2.
2. `xcode-select` missing or hanging: the candidate is not treated as the stub, and the resolver does not hang (timeout). Pinned in Task 1.
3. `SKILL_PYTHON` set to a name that is not on PATH: the sh function falls through to `python3`/`python` rather than setting a dead `PY`. Pinned in Task 2.
4. A hook run where no interpreter exists at all: it exits 0 with its normal quiet output and never blocks the tool call. Pinned in Task 3.
5. An installed kit (copied to its KIT_HOME) must carry `find_python.sh` next to its hooks, or every hook silently does nothing. Pinned in Task 4.

---

## PR 1: the resolver — branch `feat/portability-resolver` (from main; the spec commit is already on it)

### Task 1: `resolve_python()` reads `SKILL_PYTHON` and skips the macOS stub

**Files:**
- Modify: `docs/skill-contract/reference/contract_check.py` (`resolve_python`, near line 600)
- Test: `docs/skill-contract/reference/test_contract_check.py` (class `RuntimeTests`, near line 315)
- Vendored by `make contract-vendor` into the 8 adopters' `assets/contract_check.py`
- Modify docs: `docs/skill-contract/SPEC.md` (only if it states the lookup order), `docs/superpowers/specs/2026-09-16-skill-contract-design.md` "Runtime lookup for `{python}`" (append a dated note; do not rewrite history), `crafting-self-prompting-loops/SKILL.md:97`, `spec-first-planning/SKILL.md:175`
- Modify: the 8 adopters' `SKILL.md` `metadata.version` (patch bump) and `README.md` (one version note each): ops-intake, crafting-self-prompting-loops, spec-first-planning, factory-conductor, verifier-installer, idea-validation, release-conductor, test-safety-net
- Modify: `scripts/ab-validate.py` (new `SINCE_PORTABILITY_RESOLVER` and one row)

**Interfaces:**
- Produces: `resolve_python(env=None, min_version=(3, 10)) -> list[str] | None` (unchanged signature); new private `_is_macos_stub(exe: str, env: dict) -> bool`; module constant `_MACOS_STUB = "/usr/bin/python3"`.

- [ ] **Step 1: Write the failing tests** (add to `RuntimeTests`; reuse its `stub` helper and `quoted_python()`):

```python
    def test_skill_python_wins_over_python3(self):
        with tempfile.TemporaryDirectory() as d:
            self.stub(d, "python3", 'exec "%s" "$@"' % sys.executable)
            env = {"PATH": d, "SKILL_PYTHON": quoted_python()}
            self.assertEqual(cc.resolve_python(env), [sys.executable])

    def test_skill_contract_python_wins_over_skill_python(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"PATH": d, "SKILL_CONTRACT_PYTHON": "%s -X utf8" % quoted_python(),
                   "SKILL_PYTHON": quoted_python()}
            self.assertEqual(cc.resolve_python(env), [sys.executable, "-X", "utf8"])

    def _darwin(self, which, xcode_rc):
        """Patch sys.platform to darwin, shutil.which to the given map, and the
        xcode-select call to exit xcode_rc (None: it cannot run). Records every argv run."""
        ran = []
        real_run = cc.subprocess.run
        def fake_run(argv, **kw):
            ran.append(list(argv))
            if os.path.basename(argv[0]) == "xcode-select":
                if xcode_rc is None:
                    raise OSError("no xcode-select")
                return subprocess.CompletedProcess(argv, xcode_rc, b"", b"")
            return real_run(argv, **kw)
        return ran, [
            mock.patch.object(cc.sys, "platform", "darwin"),
            mock.patch.object(cc.shutil, "which", lambda n, path=None: which.get(n)),
            mock.patch.object(cc.subprocess, "run", fake_run)]

    def _with(self, patches, fn):
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            return fn()

    def test_the_macos_stub_is_skipped_without_running_it(self):
        with tempfile.TemporaryDirectory() as d:
            py = self.stub(d, "python", 'exec "%s" "$@"' % sys.executable)
            ran, ps = self._darwin({"python3": "/usr/bin/python3", "python": py,
                                    "xcode-select": "/usr/bin/xcode-select"}, 2)
            self.assertEqual(self._with(ps, lambda: cc.resolve_python({"PATH": d})), [py])
            self.assertNotIn("/usr/bin/python3", [a[0] for a in ran])

    def test_usr_bin_python3_is_kept_when_xcode_select_succeeds(self):
        ran, ps = self._darwin({"python3": "/usr/bin/python3",
                                "xcode-select": "/usr/bin/xcode-select"}, 0)
        self._with(ps, lambda: cc.resolve_python({"PATH": "/x"}))
        self.assertIn("/usr/bin/python3", [a[0] for a in ran])

    def test_a_homebrew_python3_is_never_the_stub(self):
        ran, ps = self._darwin({"python3": "/opt/homebrew/bin/python3",
                                "xcode-select": "/usr/bin/xcode-select"}, 2)
        self._with(ps, lambda: cc.resolve_python({"PATH": "/x"}))
        self.assertIn("/opt/homebrew/bin/python3", [a[0] for a in ran])
        self.assertNotIn("xcode-select", [os.path.basename(a[0]) for a in ran])

    def test_xcode_select_that_cannot_run_is_not_the_stub(self):
        ran, ps = self._darwin({"python3": "/usr/bin/python3",
                                "xcode-select": "/usr/bin/xcode-select"}, None)
        self._with(ps, lambda: cc.resolve_python({"PATH": "/x"}))
        self.assertIn("/usr/bin/python3", [a[0] for a in ran])

    def test_linux_never_asks_xcode_select(self):
        ran, ps = self._darwin({"python3": "/usr/bin/python3"}, 2)
        ps[0] = mock.patch.object(cc.sys, "platform", "linux")
        self._with(ps, lambda: cc.resolve_python({"PATH": "/x"}))
        self.assertNotIn("xcode-select", [os.path.basename(a[0]) for a in ran])
```

Add `import contextlib`, `import subprocess` and `from unittest import mock` at the top of the test file if missing.

- [ ] **Step 2: Run, expect FAIL**

Run: `cd docs/skill-contract/reference && python3 -m unittest test_contract_check.RuntimeTests -v`
Expected: the six new tests FAIL (SKILL_PYTHON ignored; stub probed).

- [ ] **Step 3: Implement**

```python
_MACOS_STUB = "/usr/bin/python3"


def _is_macos_stub(exe, env):
    """True for the macOS install stub: darwin, exactly /usr/bin/python3, and
    `xcode-select -p` failing. Running the stub opens an install dialog, so it is never
    probed. When xcode-select cannot run, the candidate is not treated as the stub."""
    if sys.platform != "darwin" or exe != _MACOS_STUB:
        return False
    xs = shutil.which("xcode-select", path=env.get("PATH")) or "/usr/bin/xcode-select"
    try:
        r = subprocess.run([xs, "-p"], capture_output=True, timeout=5, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode != 0


def resolve_python(env=None, min_version=(3, 10)):
    """The {python} lookup: SKILL_CONTRACT_PYTHON, SKILL_PYTHON, python3 (not the macOS
    install stub), python, py -3; the first to pass the probe."""
    env = dict(os.environ if env is None else env)
    candidates = []
    for var in ("SKILL_CONTRACT_PYTHON", "SKILL_PYTHON"):
        override = env.get(var, "").strip()
        if override:
            candidates.append([p.strip('"') for p in shlex.split(override, posix=(os.name != "nt"))])
    candidates += [["python3"], ["python"], ["py", "-3"]]
    probe = "import sys; sys.exit(0 if sys.version_info >= (%d, %d) else 1)" % min_version
    for cand in candidates:
        exe = shutil.which(cand[0], path=env.get("PATH"))
        if exe is None:
            continue
        if cand == ["python3"] and _is_macos_stub(exe, env):
            continue
        try:
            r = subprocess.run([exe] + cand[1:] + ["-I", "-c", probe],
                               capture_output=True, timeout=10, env=env)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return [exe] + cand[1:]
    return None
```

Note the Homebrew test: `shutil.which("xcode-select")` must not be called for a non-stub path — the early return guarantees it.

- [ ] **Step 4: Run, expect PASS**, then the whole reference suite and the e2e:
`cd docs/skill-contract/reference && python3 -m unittest test_contract_check test_e2e -v` → all PASS.

- [ ] **Step 5: Vendor and check** — `make contract-vendor && make contract`; then `git diff --stat` shows the 8 adopters' `assets/contract_check.py` changed identically.

- [ ] **Step 6: Docs** — update the two SKILL.md lines to: "Run the checker with the first of `$SKILL_CONTRACT_PYTHON`, `$SKILL_PYTHON`, `python3` (skipped when it is the macOS install stub: `/usr/bin/python3` while `xcode-select -p` fails), `python`, `py -3` that is Python 3.10 or newer." Keep each SKILL.md's surrounding wrap width. Update SPEC.md if it lists the order. Append to the 2026-09-16 design doc's macOS-stub risk bullet: "Resolved 2026-10-12 by the portability retrofit: `resolve_python()` skips the stub without running it."

- [ ] **Step 7: Version bumps** — patch-bump `metadata.version` in each adopter SKILL.md and add one README version note (80% STE): "**x.y.z.** The contract checker finds Python the same way as every other skill: `SKILL_PYTHON` overrides it, and it skips the macOS `python3` install stub without running it."

- [ ] **Step 8: A/B row** — add `SINCE_PORTABILITY_RESOLVER = "<this task's commit, set after committing>"` and a row `check_portability_resolver(old, new)`: a probe that imports the tree's `docs/skill-contract/reference/contract_check.py`, patches darwin/which/run as in the tests, and prints `{"v": 1}` when `/usr/bin/python3` was not run and the result is the `python` path. Baseline 0, branch 1, `kind="delta"`. Register it where the other `check_*` functions are called.

- [ ] **Step 9: Gate** — `make gate-skill SKILL=factory-conductor`, `make contract`, `make bcp14`, `make readme`, `make gate`, `make ab-validate` (0 WORSE, 0 UNPROVEN).

- [ ] **Step 10: Commit** (explicit paths: the reference files, the 8 vendored copies, the docs, the SKILL.md/README files, `scripts/ab-validate.py`), then set the SINCE hash to that commit in a follow-up commit, re-run `make ab-validate`.

- [ ] **Step 11: PR** — push, open PR to main with the repo template, the semantic-review checklist for each touched SKILL.md, the `make gate` box unticked. PR body ends with the two attribution lines from Global Constraints' PR form:
  ```
  🤖 Generated with [Claude Code](https://claude.com/claude-code)

  https://claude.ai/code/session_0189WxKyoVEhUPtunz3Sbcvs
  ```
  Do not merge.

---

## PR 2: hooks and installers — branch `feat/portability-hooks` (stacked on `feat/portability-resolver`)

### Task 2: `find_python.sh` and its tests

**Files:**
- Create: `world-model-ledger/assets/hooks/find_python.sh`
- Create (identical copies): `context-hygiene-kit/assets/hooks/find_python.sh`, `tmux-agent-herdr-lite/scripts/find_python.sh`
- Test: `world-model-ledger/assets/test_find_python.py` (stdlib unittest; part of `make test`)

**Interfaces:**
- Produces: a sourced sh function `find_python` that sets `PY` to an absolute interpreter path or to `""`. Always returns 0.

- [ ] **Step 1: Write the failing tests** — `test_find_python.py` builds a temp dir of fake executables and runs `sh -c '. "$1"; find_python; printf %s "$PY"' _ <path-to-find_python.sh>` with a controlled `env` (`PATH` = the temp bin only, plus `/bin:/usr/bin` only where a test needs `uname`/`command`; prefer fake `uname` in the temp bin and `PATH=<tmp>` plus the absolute dir of `sh`). Cases:
  - only `python` on PATH → `PY` is its path;
  - `python3` and `python` → `python3`;
  - `SKILL_PYTHON=mypy3` with `mypy3` on PATH → its path; `SKILL_PYTHON=missing` → falls through to `python3`;
  - fake stub: `_FIND_PYTHON_STUB=<tmp>/python3`, fake `uname` printing `Darwin`, fake `xcode-select` exiting 2, plus `python` → `PY` is `python`, and `python3` was not run (the fake `python3` writes a marker file when run; assert absent);
  - same but `xcode-select` exits 0 → `python3`;
  - same but `uname` prints `Linux` → `python3`;
  - nothing on PATH → `PY` is empty and exit status 0;
  - the three copies are byte-identical (compare the sibling files when present in the repo checkout; skip with a reason when a sibling is absent, as in an installed kit).

- [ ] **Step 2: Run, expect FAIL** — `python3 world-model-ledger/assets/test_find_python.py -v` → errors (file missing).

- [ ] **Step 3: Implement** `find_python.sh`:

```sh
# find_python: set PY to the Python interpreter this kit runs, or to "" when there is
# none. Order: $SKILL_PYTHON (one executable, no arguments), python3 unless it is the
# macOS install stub (/usr/bin/python3 while `xcode-select -p` fails; never run, so its
# install dialog never opens), then python. The same lookup as skill-contract's
# resolve_python() and the SKILL.md "Python interpreter" rule. This file is
# byte-identical in world-model-ledger, context-hygiene-kit and tmux-agent-herdr-lite;
# world-model-ledger/assets/test_find_python.py pins the copies together.
find_python() {
  PY=""
  if [ -n "${SKILL_PYTHON:-}" ]; then
    _fp=$(command -v "$SKILL_PYTHON" 2>/dev/null) && [ -n "$_fp" ] && { PY=$_fp; return 0; }
  fi
  _fp=$(command -v python3 2>/dev/null) || _fp=""
  if [ -n "$_fp" ]; then
    if [ "$_fp" = "${_FIND_PYTHON_STUB:-/usr/bin/python3}" ] \
       && [ "$(uname -s 2>/dev/null)" = Darwin ] \
       && ! xcode-select -p >/dev/null 2>&1; then
      :
    else
      PY=$_fp
      return 0
    fi
  fi
  _fp=$(command -v python 2>/dev/null) || _fp=""
  PY=$_fp
  return 0
}
```

The path comparison comes first, so on every machine except a `/usr/bin/python3` one the function forks no `uname`.

- [ ] **Step 4: Run, expect PASS.** Copy the file byte-identically to the other two locations; re-run (the pin test now runs, not skips).

- [ ] **Step 5: Commit.**

### Task 3: the hooks use `find_python`

**Files:**
- Modify: `world-model-ledger/assets/hooks/{pretooluse,posttooluse-observe,stop,session_start}.sh`, `context-hygiene-kit/assets/hooks/{stop,precompact,session_start}.sh` (every `python3` occurrence, including `python3 -c` and `python3 - <<'PY'`)
- Test: extend `world-model-ledger/assets/test_find_python.py` (or the kits' existing hook suites if they already drive hooks — read `world-model-ledger/assets/test_*` and `context-hygiene-kit/assets/test_*` first and follow their pattern)

- [ ] **Step 1: Failing tests** — for each hook, run it with stdin set to a minimal valid hook JSON for its event, `PATH` = a temp dir holding only a `python` shim (`exec <sys.executable> "$@"`) plus the dirs of `bash`, `cat`, `dirname`, `mkdir`, etc. (build the PATH from `shutil.which` of the tools the hook uses, symlinked into a second temp dir — never include a dir that has `python3`). Assert the hook exits 0 and produces the same output it produces with `python3` available (for pretooluse on a repo with `.world-model/model.db`, the `additionalContext` JSON). Also run each hook with no interpreter at all: exit 0 and its normal quiet output (`{"continue": true}` where the hook prints that today, nothing otherwise).

- [ ] **Step 2: Run, expect FAIL** (baseline hooks call `python3`).

- [ ] **Step 3: Implement** — at the top of each hook, after `KIT_HOME=...`:

```bash
. "$(dirname "$0")/find_python.sh"
find_python
[ -n "$PY" ] || { <the hook's existing quiet exit, e.g. echo '{"continue": true}'>; exit 0; }
```

then replace each `python3` with `"$PY"`. Keep every `|| true` and every `2>/dev/null`.

- [ ] **Step 4: Run, expect PASS**; then each kit's existing suites: `python3 world-model-ledger/assets/test_world_model.py`, `python3 context-hygiene-kit/assets/test_context_ledger.py`, and any hook shell suites.

- [ ] **Step 5: Commit.**

### Task 4: installers, version bumps, A/B rows

**Files:**
- Modify: `world-model-ledger/scripts/install.sh`, `context-hygiene-kit/scripts/install.sh`, `tmux-agent-herdr-lite/scripts/install.sh`
- Modify: the three skills' `SKILL.md` `metadata.version` (patch bump) and `README.md` (a version note, plus one line: "`SKILL_PYTHON` names the interpreter. In the hooks and installers it is one executable, a name or a path, with no arguments.")
- Modify: `scripts/ab-validate.py` (`SINCE_PORTABILITY_HOOKS`, rows)

- [ ] **Step 1: Read each install.sh** and confirm how hooks reach `KIT_HOME` (copy of `assets/hooks/`, or per-file copies). If per-file, add `find_python.sh` to the copied set. Write a failing test (in `test_find_python.py`) that runs the installer into a temp HOME/target (follow any existing installer test; if none exists, assert on the copy list by running the installer with its dry-run flag if it has one, else by grepping the script for the hooks copy and asserting `find_python.sh` is covered) — the point is Review Focus 5: an installed kit has `find_python.sh` next to its hooks.

- [ ] **Step 2: Implement** — each installer sources its own copy (`. "$SKILL_ROOT/assets/hooks/find_python.sh"` for the two kits, `. "$(dirname "$0")/find_python.sh"` for tmux-agent-herdr-lite; use the variable the script already has for its own location), calls `find_python`, and stops when `PY` is empty:

```sh
find_python
[ -n "$PY" ] || { echo "error: this skill needs Python 3.10 or newer; set SKILL_PYTHON or install python3" >&2; exit 1; }
```

Replace every `python3` with `"$PY"`, including the user-facing hint at `world-model-ledger/scripts/install.sh:161` (print the resolved interpreter there).

- [ ] **Step 3: Run** the three skills' suites and `make gate-skill SKILL=<each>`.

- [ ] **Step 4: A/B rows** under `SINCE_PORTABILITY_HOOKS`: (a) world-model-ledger pretooluse with a `python`-only PATH emits the additionalContext JSON (baseline 0, branch 1); (b) context-hygiene-kit session_start with a `python`-only PATH runs the curate step (observable output or file; baseline 0, branch 1); (c) the fake-stub case for find_python skips the stub (baseline: no file → 0; branch 1). `kind="delta"`. Plus a HELD guard: with `python3` present, pretooluse output is byte-identical between trees.

- [ ] **Step 5: Full checks** — `make gate`, `make ab-validate`; commit; repin SINCE; push; open PR to `feat/portability-resolver` (retarget to main after PR 1 merges), template filled, semantic-review checklist for the three SKILL.md files (version bump only), not merged.

---

## PR 3: the SKILL.md rule and its gate — branch `feat/portability-skill-rule` (stacked on `feat/portability-hooks`)

### Task 5: the `python-interpreter` gate

**Files:**
- Create: `scripts/gates/python-interpreter-rule.txt` (the canonical rule text, one paragraph, exactly as in the spec §4)
- Create: `scripts/gates/python-interpreter.sh <skill-dir>`
- Modify: `Makefile` (run it per skill in `gate` next to `asset-paths.sh`, and in `gate-skill`), `scripts/gates/test_gates.sh` (self-tests), `CLAUDE.md` (gate table row)

**Interfaces:**
- Produces: `python-interpreter.sh` prints `PASS:`/`FAIL:` lines and a final `PYTHON_INTERPRETER_RESULT: PASS|FAIL`; exit 0/1.

- [ ] **Step 1: Self-tests first** in `test_gates.sh`, following its existing pattern (temp skill dirs):
  - a skill whose SKILL.md body has `python3 "$SKILL_DIR/assets/x.py"` and no rule → FAIL;
  - same with the rule → PASS;
  - the rule with one word changed → FAIL;
  - the rule re-wrapped across lines (different line breaks, same words) → PASS;
  - a skill that mentions `python3` only in frontmatter `compatibility:` → PASS without the rule;
  - a skill whose only command is in `references/r.md` (`python3 -m pytest`) → FAIL without the rule;
  - a line `VERIFY: T1 ok /usr/bin/python3 -c ...` alone → PASS without the rule (not a command the agent runs);
  - a `{python}` command alone → PASS without the rule.

- [ ] **Step 2: Run** `sh scripts/gates/test_gates.sh` → the new cases FAIL (script missing).

- [ ] **Step 3: Implement** `python-interpreter.sh`. It is repo tooling, so it may call `python3`. Detection: SKILL.md body (after frontmatter) plus `references/*.md`; a line "runs Python" when it matches the extended regex `(^|[[:space:]\`(])python3?[[:space:]]+(-[A-Za-z]|"|\$|<|[A-Za-z0-9_./-]+\.py)`. If any line runs Python, the SKILL.md body, with all whitespace runs collapsed to one space, must contain the rule file's text collapsed the same way. Print which file:line triggered the requirement on FAIL.

- [ ] **Step 4: Run** the self-tests → PASS. Run `for d in */; do [ -f "$d/SKILL.md" ] && sh scripts/gates/python-interpreter.sh "$d"; done` and record which skills FAIL — that list is Task 6's scope (expected about 24).

- [ ] **Step 5: Wire** into `Makefile` `gate` (per skill, `_fail "$$d python-interpreter"`) and `gate-skill`; add the CLAUDE.md gate-table row: "| Python interpreter | `scripts/gates/python-interpreter.sh` | every SKILL.md that runs Python (in its body or `references/`) carries the shared 'Python interpreter' rule byte-for-byte (whitespace-insensitive), so every skill finds Python the same way |". Do not commit the Makefile wiring until Task 6 makes it green; commit the script, rule file and self-tests now.

### Task 6: add the rule to every flagged skill; the re-entry text

**Files:**
- Modify: each SKILL.md the gate flagged (insert the rule paragraph in the setup section, before the first command, wrapped to the file's width), `metadata.version` patch bump, README version note
- Modify: `factory-conductor/SKILL.md` "Scheduled re-entry" (around lines 333–338) and `spec-first-planning/references/unattended.md` (lines ~148, ~157, ~294, ~309)
- Modify: `scripts/ab-validate.py` (`SINCE_PORTABILITY_RULE`, row), `Makefile`, `CLAUDE.md` (from Task 5)

- [ ] **Step 1: Insert the rule** in each flagged SKILL.md. Run the gate for every skill → all PASS. `make bcp14` → PASS (the rule has no capitals; check no new lowercase "must").

- [ ] **Step 2: Re-entry text.** In factory-conductor's re-entry paragraph, change "as `python3 /abs/path/to/factory-conductor/assets/conductor.py <command> --root <repo>`" to keep that example and add: "If the Python interpreter rule found an interpreter other than `python3`, write that one instead, here and in the grant's `Bash(...)` rule, so the two match." In `unattended.md`, add the same sentence after the `agent_cmd` example and where the allowlist advice names `Bash(python3 *)`. These sentences have no capitals.

- [ ] **Step 3: Version bumps and README notes** for each touched skill (skills already bumped in PR 1 or PR 2 get a second patch bump on this branch, since each PR ships separately): "**x.y.z.** SKILL.md says how to find Python when `python3` is missing, too old or the macOS install stub."

- [ ] **Step 4: A/B row** `SINCE_PORTABILITY_RULE`: count of skills that run Python and carry the rule (baseline 0, branch N = the flagged count), `kind="delta"`, with the note "prompt-only: checks the text is present, not model behaviour".

- [ ] **Step 5: Full checks** — `make gate`, `make bcp14`, `make readme`, `make contract`, `make ab-validate`; commit (explicit paths); repin SINCE; push; open PR to `feat/portability-hooks`, the semantic-review checklist for every touched SKILL.md, not merged.
