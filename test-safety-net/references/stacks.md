# Per-stack facts

Four genuinely language-specific facts, per stack: how to find a unit, where its test goes,
which framework runs it, and — load-bearing — how to run exactly **one** test. Step 4's
red→green proof is impossible without single-test invocation: running the whole suite to check
one assertion is slow, and worse, a broken assertion elsewhere in the suite would be
indistinguishable from the one you're proving.

| stack | find units | tests go | framework | run ONE test |
|---|---|---|---|---|
| python | module-level `def` / `class` | `tests/` or `test_*.py` beside the source | pytest, falling back to `unittest` | `pytest path/to/test_file.py::test_name` (or `pytest path/to/test_file.py::TestClass::test_name`); unittest fallback: `python3 -m unittest module.Class.test_name` |
| node | top-level `export`ed function/class (`export function`, `export default`, `export const f = …`, `export class`, `export { f }`, `module.exports`/`exports.f`) | `<name>.test.js` beside the source, `__tests__/<name>.test.js` beside it, or a mirrored `test/<dir>/<name>.test.js` — whichever the repo already uses | node's built-in runner, `node --test` (node 18+); **no dependency is added** | `node --test --test-name-pattern '^<test_name>$' <path>` |
| go | *not supported — no stack is registered, see below* | — | — | — |
| rust | *not supported — no stack is registered, see below* | — | — | — |

This version of the skill **writes tests for Python and node/TypeScript repositories**. The
workflow itself (rank → confirm → write+prove → report) is language-agnostic, and adding a stack is
a matter of filling in its row here, registering it in `assets/rank_risk.py`, and shipping a
runtime guard that can prove the no-I/O invariant — it should never require reopening the workflow.

Do not improvise support for go/rust by guessing at conventions. Neither is registered, so the
ranker writes `note: no stack claims <repo> (evidence: node=0, python=0)` to **stderr** and returns
an empty plan with `units_discovered: 0`; the `stack` key still reads `"python"`, which is the
default and not a verdict, so read the stderr note rather than the key. Say plainly that this
version does not cover the repo, and stop rather than proceeding on assumptions.

## Node row, in detail

`assets/rank_risk.py` covers node/TypeScript repositories end to end: detect → discover → triage →
rank, and then `assets/io_guard.js` proves each test the same way `assets/io_guard.py` does on
python. It detects them (each stack scores the repo — the non-test source files it claims, doubled
(plus a floor) when a manifest at or near the root **declares** that stack, which means it says
how the repo runs: an entry point, runtime dependencies, a module system, a real build. A
`package.json` carrying only `husky` and `prettier`, or a `pyproject.toml` carrying only
`[tool.ruff]`, is tooling and scales nothing — otherwise eight Python files lose to three
JavaScript ones. The highest score
wins; `--stack` overrides, and a tie is
reported rather than guessed), discovers top-level `export`ed functions and classes, and triages
them against node's own I/O marker tables into the same four tiers Python uses.

Discovery has **two** paths and the report's `discovery` key says which one ran. Where the repo
ships its own `node_modules/typescript`, the ranker drives that compiler's parser and reads the
export forms a text reader cannot — destructured exports, quoted keys in `module.exports`, a
whole-module `module.exports = fn` — so a precise run finds *more* units than a heuristic one on
the same tree. Where it does not (no install, Yarn PnP, no `node`, a compiler that times out or
fails), it falls back to the heuristic reader and reports `heuristic`. **The toolchain is never
downloaded** — `npx tsc` on a miss would fetch an unpinned compiler, so the precise path
`require`s one already on disk or declines. Two node runs are comparable only when the key
agrees.

**Which path to expect.** `heuristic`, in most repos you meet. `precise` needs a real
`node_modules/typescript` already installed in the tree being analysed — not one hoisted into a
parent workspace this run cannot see, not a Yarn PnP zip, and not one this run could install. So
plan on `heuristic`, treat `precise` as the upgrade it is, and never compare a run's unit counts
against a run whose `discovery` value differs: the same tree honestly yields two different totals
under the two readers, and the precise one is the larger.

- **Tests go in** `<name>.test.js` beside the source file, `<dir>/__tests__/<name>.test.js` beside
  it, or a mirrored `test/<dir>/<name>.test.js` — match whichever convention the repo already uses;
  default to `<name>.test.js` beside the source when it has none. All three are collected by a bare
  `node --test`, and all three are shapes `already_covered` can credit, so a test you write is a
  test the next run will see.
- **Framework.** node's own `node --test` (node 18+). Nothing is installed and no dependency is
  added — which is the point on a repo that has no test tooling at all.
- **Run one test.** `node --test --test-name-pattern '^<test_name>$' <path>`. The anchors matter:
  the pattern is a substring regex, so an unanchored `adds` also selects `adds_negatives`, and the
  proof stops being about one test.
- **Runtime guard placement.** The guard ships as `assets/io_guard.js`. Preload it with
  `--require` on the single-test invocation itself, with the tier passed by environment variable —
  never a setup file written into the repo:

  ```sh
  TEST_SAFETY_NET_TIER=1 \
    node --require "$SKILL_DIR/assets/io_guard.js" \
    --test --test-name-pattern '^<test_name>$' <path>
  ```

  Copy the whole block: `--require` runs before any user module, including the test file, which is
  what lets the guard see import-time I/O — the case the filter floors to Tier 3. It is plain
  CommonJS loaded by absolute path, so it never has to be resolvable as a package, and it works
  unchanged for an ESM (`.mjs`, `type: "module"`) test file: the preload runs before the ESM loader
  starts, and a builtin's ESM namespace is built from the already-guarded CommonJS object, so
  `import { readFileSync } from "node:fs"` binds the guarded function. There is no `--import`
  sibling command to keep true.

- **TypeScript, and its one real limit.** node 22.6+ runs a `.ts` test file directly by stripping
  types (unflagged from 22.18), so `node --test --test-name-pattern '^sums$' sum.test.ts` needs no
  build step. Two conditions, both verified rather than assumed: the import specifier must carry the
  **real** extension (`from "./sum.ts"`, not `"./sum"`), and the code must be **erasable** — an
  `enum`, a `namespace` or a constructor parameter property fails with
  `ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX` under strip-only mode. On older node, or where the unit's
  module is not erasable, either pin the unit through the repo's **built** JavaScript output or
  report it under **could not prove** with the blocker named. Do not add a transpiler to the repo.

- **Where the repo already runs jest, vitest or mocha, stop and report.** The generated tests are
  `node:test` tests, and the guard is proven only under `node --require` + `node --test`. Dropping a
  `node:test` file into a jest or vitest suite either is not collected (no safety net at all) or is
  collected and fails (invariant 4, "never leaves the suite red"), and arming the guard through
  those runners' own setup hooks would arm it *after* the module under test loads, which is the one
  property `--require` exists to buy. Rank the repo, hand over the report, and say which runner
  blocked the writing half. This is a real gap in this version, not a preference.

### The rule that has no python equivalent: read the exit status

`node --test` **misattributes a violation that lands after its test resolved.** The violating test
prints `ok`; the `not ok` is charged to **whatever the runner is executing when the violation
lands**. That is one later test, or several, or — when nothing else is running — the enclosing
file. It is never the test that caused it.

**All three shapes below were reproduced on the same node, v22.18.0.** What selects between them is
the fixture's own timing and how the violating call is reached, *not* the runtime version. Do not
read "my node is newer than that" as a reason to trust the per-test lines.

**(a) Nothing else is running: the FILE is charged.** The runner adds a `# Error:` comment that
does name the real culprit — the only one of the three that leaves any trace of it, and it is a
comment, not a result:

```
ok 1 - fast_test_slow_violation              <- the test that violated
ok 2 - second_test_keeps_process_alive
# Error: Test "fast_test_slow_violation" ... generated asynchronous activity after the
#   test ended. This activity created the error "IOGuardViolation: ..."
not ok 1 - test_units.js                     <- the FILE, not the test
exit 1
```

**(b) A later test is running: THAT test is charged, by name.** The violating chain here is
*detached* — the test starts a promise chain it never awaits, and returns — so a later test is
still executing when the chain reaches the guarded call:

```
ok 1     - fast_test_slow_violation          <- the test that violated
not ok 2 - second_test_keeps_process_alive   <- an innocent test
exit 1
```

**(c) Two later tests are running: BOTH are charged.** One violation, two innocent failures, and
the violator still green:

```
ok 1     - violator                              <- the test that violated
not ok 2 - innocent_short                        <- an innocent test
not ok 3 - innocent_long_running_when_it_lands   <- and another
exit 1
```

Shape (c) is why the rule below is *discard the whole batch* and not *discard the neighbour*: there
is no subset of the batch a reader can salvage from the TAP stream. In every shape an agent reading
per-test results keeps the test that performs real I/O and discards clean ones. So:

- **The exit status is the only trustworthy signal on this stack.** A zero exit means the batch is
  clean; a nonzero exit means something in it violated.
- On a nonzero exit whose failure is an `IOGuardViolation`, **discard the whole batch and re-prove
  one test at a time.** Per-test attribution cannot be trusted for an async violation, and a batch
  is cheap to re-run.
- **Never keep a test reported `ok` from a run that exited nonzero.**

`assets/test_io_guard_node.sh` builds all three fixtures — assertion 12 is shape (a), 13 is (b),
14 is (c) — and asserts the invariant they share: the violating test `ok`, some *other* entry
`not ok`, a nonzero exit, an `IOGuardViolation` in the output. Assertion 14 additionally requires
**two or more** innocent failures, so the paragraph above is a checked claim rather than a
remembered one. Each shape was confirmed stable over 20 identical runs before it was committed. If
node ever starts blaming the right test, those assertions go red and this section gets weaker,
which is the intended direction.

The mechanism behind it is stated in `assets/io_guard.js`: every violation is **recorded** at the
raise as well as thrown, and a `process.exit` hook fails the run on any record that was not already
fatal. JavaScript has no uncatchable exception — `try { fs.readFileSync(cache) } catch {}` is one of
the commonest shapes in real code — so without the record a swallowed trip would be a green proof
over a unit that really did read the filesystem.

### What the node guard patches

The lowest layer reachable from JavaScript, per group, and **both faces** of the ones that have
two. Module functions are *derived* (`Object.keys(mod)`), not enumerated, so a node release that
adds a sibling cannot quietly escape:

| group | patched |
|---|---|
| filesystem | every own function of `node:fs`, of `node:fs/promises` (the same object as `fs.promises`, and a **different** set of functions from the callback face), of `node:trace_events`; `v8.writeHeapSnapshot`; `node:wasi`'s `WASI` on require |
| network | every own function of `node:net`, `node:http`, `node:https`, `node:http2`, `node:dgram`, `node:tls`, `node:dns`, `node:dns/promises`, `node:inspector(/promises)`; `globalThis.fetch`/`WebSocket`/`EventSource`/`XMLHttpRequest`; `node:quic` on require |
| subprocess | every own function of `node:child_process` and `node:worker_threads`; `cluster.fork`/`setupPrimary`/`setupMaster` |
| database | 17 driver entry points, patched **on `require`** — never required by this file, so a stdlib-only guard does not make `pg` a dependency |
| clock | `Date.now`; the `Date` constructor **only with no arguments** (`new Date(0)` reads no clock); `setTimeout`/`setInterval`/`setImmediate`; `process.hrtime`/`uptime`; `performance.now`; `node:perf_hooks` |
| randomness | `Math.random`; `node:crypto`'s `random*`/`generateKey*`/`generatePrime*`/`getRandomValues`; `globalThis.crypto.getRandomValues`/`randomUUID` |
| environment | a `Proxy` over `process.env` (every **value** read, destructuring included); the `process.argv`/`argv0` getters; `process.cwd`/`chdir`/`umask`; every own function of `node:os` |
| *(not a group)* | `process.stdin`'s getter, `node:readline`, `node:readline/promises` — **tier 1 only**. Terminal input is in neither stack's marker table, so the filter cannot decline it, and the failure mode is a *hang* that yields no verdict at all rather than a failure |

Third-party clients are not patched and do not need to be: `axios`, `got`, `node-fetch`, `undici`
and `superagent` all bottom out in `node:net` or `globalThis.fetch`, and `fs-extra`/`graceful-fs`
bottom out in `node:fs`. Database **drivers** are the exception — they reach the network through
their own native bindings — which is why they are patched on require instead.

Every target is replaced by a `Proxy` with `apply` and `construct` traps rather than by a wrapper
function. `net.Socket`, `worker_threads.Worker` and `fs.ReadStream` are **classes**, and rebinding a
class to a plain function breaks `class X extends net.Socket`, `instanceof` and every static — which
in the python guard produced a *fourth* proof outcome the three-outcome contract has no rule for.

The block decision is scoped by **call provenance**, not by name: node reads every `.js` it loads
through `fs.readFileSync`, so a guard that blocks on the name alone kills a test that touches no
filesystem at all, inside the module loader. The guard walks the stack outward from the innermost
frame, skips its own, stops at a `node:internal/` frame and exempts, treats a *public* builtin frame
(`node:path`, `node:fs`) as transparent, and blames anything else. The cost, stated rather than
implied: any call the target repo makes that reaches a guarded primitive **through** a
`node:internal` frame is exempt too — `require("/etc/hosts")` is read by the loader before it fails
to parse. That is the same price python pays for `import`.

### Node residuals — read these before you read a trip

Two of the seven change what a trip, or its absence, means:

1. **The `database` group is patched through `Module._load`, which a native-ESM `import` does not go
   through.** A repo that does `import pg from "pg"` in an ESM module loses the `database` **label**
   — the trip will say `network` instead, at the driver's first connection, and `network` is blocked
   at every tier. So enforcement survives; only the group name in the message is wrong. Do not read
   a `network` trip in an ESM repo as proof the unit does no database work. No fixture covers this,
   because a fixture would need a real driver installed.
2. **The provenance rule matches frame *text*** — the literal `node:internal/` prefix and the
   `node:<builtin>:<line>` shape. A node release that renames or reformats internal frames would
   turn the guard **inert** rather than noisy: it would stop blaming anything and every proof would
   pass. This is the same class of dependency as `assets/io_guard.py`'s `_IMPORT_PROTOCOL_FRAMES` —
   a hand-derived list of CPython's frozen-import function names, stated as residual 7 in
   `references/triage.md`. `assets/test_io_guard_node.sh`'s first assertion is what catches it, in
   both directions, but only on a machine where that suite runs; it is exercised on node 22.18 here
   and in CI.

And five that are ordinary limits, each named in `assets/io_guard.js` itself:

3. A subprocess that itself dials out escapes an in-process guard. (`subprocess` is blocked at every
   tier, so this needs the spawn to be exempt to begin with.)
4. A native addon (`.node`), `process.loadEnvFile`, or `node:sqlite`'s and WASI's C++ layers reach
   the syscall through their own bindings. What is enforced is that a handle cannot be *built* under
   the guard.
5. A reference bound before the guard armed keeps the original. `--require` is what makes this rare
   rather than routine.
6. `"KEY" in process.env` and `Object.keys(process.env).length` read no **value**, so they do not
   trip.
7. A violation on a worker thread cannot reach the parent's record — which is a residual rather than
   a hole only because `worker_threads.Worker` is a guarded constructor blocked at every tier, so a
   worker cannot be started under the guard in the first place.

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
