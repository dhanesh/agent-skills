# Per-stack facts

Four genuinely language-specific facts, per stack: how to find a unit, where its test goes,
which framework runs it, and — load-bearing — how to run exactly **one** test. Step 4's
red→green proof is impossible without single-test invocation: running the whole suite to check
one assertion is slow, and worse, a broken assertion elsewhere in the suite would be
indistinguishable from the one you're proving.

| stack | find units | tests go | framework | run ONE test |
|---|---|---|---|---|
| python | module-level `def` / `class` | `tests/` or `test_*.py` beside the source | pytest, falling back to `unittest` | `pytest path/to/test_file.py::test_name` (or `pytest path/to/test_file.py::TestClass::test_name`); unittest fallback: `python3 -m unittest module.Class.test_name` |
| node | top-level `export`ed function/class — **ranked, not yet written**, see below | — | — | — |
| go | *not yet supported — see the follow-up plan* | — | — | — |
| rust | *not yet supported — see the follow-up plan* | — | — | — |

This version of the skill **writes tests for Python repositories only**. The workflow itself
(rank → confirm → write+prove → report) is language-agnostic, and adding a stack is meant to be a
matter of filling in its row here plus a fixture set — it should never require reopening the
workflow. Do not improvise support for node/go/rust by guessing at conventions; if `rank_risk.py`
or the stack detection finds a repo this file has no complete row for, say plainly that this
version does not cover it and stop rather than proceeding on assumptions.

## Node: ranked, not yet written

`assets/rank_risk.py` **does** cover node/TypeScript repositories for the first half of the
workflow. It detects them (each stack scores the repo — non-test source files plus a bonus for a
manifest at or near the root — and the highest score wins; `--stack` overrides, and a tie is
reported rather than guessed), discovers top-level `export`ed functions and classes
heuristically, and triages them against node's own I/O marker tables into the same four tiers
Python uses.

What does **not** exist yet is the second half: there is no runtime guard for node and no
single-test invocation wired up, so **step 4's red→green proof cannot be performed**. Per the
multi-stack design, a stack that cannot prove the no-I/O invariant declines to write rather than
writing unproven tests. So on a node repo: produce the ranking, hand over the report, and stop
before writing any test. Filling in this row's remaining three columns is what makes node
complete.

## Python row, in detail

- **Find units.** `assets/rank_risk.py` already does this: every module-level `def`/`class` in a
  non-test `.py` file, skipping `_`-prefixed (private-by-convention) names and vendor/build
  directories.
- **Tests go in** `tests/` at the repo root, or a `test_*.py`/`*_test.py` file beside the source
  file being pinned — match whichever convention the repo already uses; default to `tests/` when
  the repo has neither.
- **Framework.** Prefer `pytest` if it's already a dependency (check `requirements*.txt`,
  `pyproject.toml`, `setup.cfg`, or an existing `pytest.ini`/`[tool.pytest.ini_options]`). Fall
  back to stdlib `unittest` only when pytest is not already present and you should not be adding
  a new dependency to a repo that has none.
- **Run one test.**
  - pytest: `pytest <path>::<test_name>` for a bare function, or
    `pytest <path>::<TestClass>::<test_name>` for a method.
  - unittest: `python3 -m unittest <module.path>.<ClassName>.<test_name>`.
- **Runtime guard placement.** The guard ships as `assets/io_guard.py`. Load it as a **pytest
  plugin via `-p`** on the single-test invocation itself, with the tier passed by environment
  variable — never a `conftest.py` written into the repo:

  ```sh
  PYTHONPATH="$SKILL_DIR/assets:$PYTHONPATH" TEST_SAFETY_NET_TIER=1 \
    pytest -p io_guard <path>::<test_name>
  ```

  Copy the whole block: the `PYTHONPATH` assignment is what makes `-p io_guard` resolvable, and
  `pytest -p io_guard ...` on its own fails with `ImportError: Error importing plugin "io_guard"`.

  See `references/triage.md` for exactly what it patches, how it signals a guard trip versus an
  ordinary assertion failure, and why a plugin (not a written file) is what keeps Invariant 1
  clean.

- **The `unittest` fallback covers strictly less, and not conditionally.** `unittest` has no
  plugin-loading mechanism, so the guard has to be armed from `setUpModule` in the generated test
  module (`io_guard.arm(1)` / `io_guard.arm(2, allow=("filesystem",))`, with
  `addModuleCleanup(io_guard.disarm)`). The generated test module imports the unit under test at
  its own top level, and a module's top level **always** runs before `setUpModule`. So on this
  path the guard **never** covers import-time I/O — not "slightly later", not "if the module was
  imported earlier in the same process". Unconditionally never.

  That matters because import-time I/O is exactly the case the ranker floors to Tier 3 ("a fixture
  runs too late to control it — needs a seam"), and the unittest fallback has no backstop for a
  unit the filter mis-tiered into Tier 1 from a module that reads a file at import. State this in
  the report whenever the fallback is used, as a weaker guarantee than the pytest path — and
  prefer pytest whenever the repo will tolerate it, since this is the one gap the fallback cannot
  close.

  Worker-thread trips ARE covered on this path, by a different route: the guard re-raises a
  swallowed off-main-thread violation from `Thread.join` and, failing that, from `disarm()` — so
  the `addModuleCleanup(io_guard.disarm)` above turns it into a module-teardown ERROR rather than
  a silent pass. (On the pytest path a `pytest_runtest_teardown` hook attributes it to the test
  itself, which is the sharper signal; that hook is the only part of this the fallback loses.)

## The `make` case

`make` is a **runner over an underlying language**, not a stack of its own — you cannot author a
"make unit test," and `make test` usually cannot run a single test in isolation. When a repo
fronts its suite with `make`:

1. Detect the real language underneath (inspect the Makefile's test target, or look for
   `pytest.ini`/`go.mod`/`Cargo.toml`/`package.json` alongside it) and author tests per that
   language's row above.
2. Fall through to the **native runner** for the proof loop (step 4), even though `make` fronts
   the suite day-to-day — call `pytest <path>::<test_name>` directly rather than trying to make
   `make` run a single test.
3. If no single-test invocation can be found at all (the Makefile shells out to something opaque
   with no native equivalent reachable), report the unit under **could not prove** in the final
   report, with the blocker named, and write no test for it. Do not claim success by running the
   whole suite and calling it a proof.
