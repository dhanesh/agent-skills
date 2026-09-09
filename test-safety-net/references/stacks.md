# Per-stack facts

Four genuinely language-specific facts, per stack: how to find a unit, where its test goes,
which framework runs it, and — load-bearing — how to run exactly **one** test. Step 4's
red→green proof is impossible without single-test invocation: running the whole suite to check
one assertion is slow, and worse, a broken assertion elsewhere in the suite would be
indistinguishable from the one you're proving.

| stack | find units | tests go | framework | run ONE test |
|---|---|---|---|---|
| python | module-level `def` / `class` | `tests/` or `test_*.py` beside the source | pytest, falling back to `unittest` | `pytest path/to/test_file.py::test_name` (or `pytest path/to/test_file.py::TestClass::test_name`); unittest fallback: `python3 -m unittest module.Class.test_name` |
| node | *not yet supported — see the follow-up plan* | — | — | — |
| go | *not yet supported — see the follow-up plan* | — | — | — |
| rust | *not yet supported — see the follow-up plan* | — | — | — |

This version of the skill supports **Python repositories only**. The workflow itself (rank →
confirm → write+prove → report) is language-agnostic, and adding a stack is meant to be a matter
of filling in its row here plus a fixture set — it should never require reopening the workflow.
Do not improvise support for node/go/rust by guessing at conventions; if `rank_risk.py` or the
stack detection finds a non-Python repo, say plainly that this version does not cover it and stop
rather than proceeding on assumptions.

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
- **Runtime guard placement.** Load the guard as a **pytest plugin via `-p`** on the single-test
  invocation itself (e.g. `pytest -p test_safety_net_guard <path>::<test_name>`), with the tier
  passed by environment variable — never a `conftest.py` written into the repo. See
  `references/triage.md` for exactly what it must patch, how it signals a guard trip versus an
  ordinary assertion failure, and why a plugin (not a written file) is what keeps Invariant 1
  clean. `unittest` has no equivalent plugin-loading mechanism; when falling back to it, install
  the guard via `setUpModule` at the top of the generated test module instead, accepting that it
  runs slightly later than the pytest plugin would (after the module under test is already
  imported, if that module is imported anywhere earlier in the same process) — flag this
  explicitly in the report as a weaker guarantee than the pytest path.

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
