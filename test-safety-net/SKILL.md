---
name: test-safety-net
description: >-
  Author unit tests into a Python, node/TypeScript, Go or Rust codebase that has none, so an agent
  can change it safely. Use when a repo has no meaningful tests, when someone says "I don't trust an
  agent in this codebase", "add tests before we refactor", or "we need a safety net before this
  migration" — or for a JS/TS repo with no `*.test.js`, a Go module with no `_test.go`,
  or a Rust crate with no `tests/`. Ranks units by blast radius and churn, then writes
  characterization tests that pin current behaviour — each proved able to FAIL before it is kept,
  so the suite detects change. Every test declares whether it pins behaviour or
  asserts a spec; suspected bugs are pinned AND reported, never blessed. Untestable code becomes
  a ranked seam list for clean-code. Never modifies your source and never
  writes a test that performs real I/O. Not a correctness audit or a coverage chaser.
  Fills verifier-installer's loop; clean-code judges what comes out.
  Honors a skill-contract autonomy grant at its write gate.
license: MIT
compatibility: >-
  Prompt-driven; the bundled ranker needs python3 (stdlib only) and, for the churn signal, the git
  CLI. Writes and proves tests on four stacks — python 3.10–3.14 (pytest, falling back to
  unittest), node/TypeScript on the LTS lines 18, 20, 22, 24 and 26 (`node --test`), go 1.22–1.26
  (`go test`, on darwin and linux) and rust 1.82–1.98 (`cargo test`, on darwin and linux; 1.82,
  1.86, 1.90, 1.94 and 1.98 proven by CI's `versions` legs on amd64 linux, and 1.82 on arm64
  macOS too, the minors between expected by bracketing, not proven) (references/stacks.md). No pip, no npm, no network: node's optional
  precise discovery drives a `typescript` the repo already ships and never downloads one — that
  runs the analysed repo's own compiler in-process, and `--no-precise` declines it — go's runs this
  skill's own `go/ast` helper under `GOTOOLCHAIN=local`, and the rust guard builds `--offline`
  under `RUSTUP_AUTO_INSTALL=0`, so no toolchain or crate is ever fetched.
metadata:
  author: dhanesh
  version: "1.4.1"
  skill-contract: "1"
  tags: "testing,characterization,legacy-code,agent-safety,pytest,node,typescript,go,rust"
---

# test-safety-net

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY in this skill are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and only when, they appear in all capitals.

Build the change-detector that unblocks agent work on an untested codebase. This is **not** a
correctness audit: current behaviour gets pinned even where it looks wrong, and a suspected bug
gets reported rather than silently blessed as correct. The output is a net that catches
unintended behaviour change, plus an honest account of what it could not net and why — never a
coverage percentage.

## When to use

Reach for this when a repo has no meaningful tests and someone needs to change it safely: "I
don't trust an agent in this codebase," "add tests before we refactor," "we need a safety net
before this migration." It sits between two siblings and does neither of their jobs:
`verifier-installer` installs the runnable verify→repair loop (format/build/test + CI) and stops
at one placeholder test to keep the loop runnable — this skill fills that placeholder with real
tests. `clean-code` then judges what comes out (`clean-code/references/testing.md`) and receives the seam
list this skill cannot act on itself (see Tiers 3/4 below). Do not use this to chase a coverage
number, to bless current behaviour as correct, or to refactor code to make it testable — that
inverts the safety property the skill exists to provide (see Invariant 1).

**Locating this skill's helpers (do this first).** Six bundled scripts ship with this skill — the
stack-agnostic ranker (`assets/rank_risk.py`, step 2), one runtime I/O guard per stack
(`assets/io_guard.py` for python, `assets/io_guard.js` for node, `assets/io_guard_go.py` for go,
`assets/io_guard_rust.py` for rust, all step 4) and the skill-contract checker
(`assets/contract_check.py`, step 3's autonomy-grant check). A path written relative to this skill will not
resolve from the target repo you're working in. Resolve the base directory once and reuse it
everywhere:

```sh
SKILL_DIR="<this skill's base directory>"   # your harness provides it when the skill loads
# If you don't have it, discover it:
SKILL_DIR=$(find ~/.claude ~/.config ~/.agents -type d -name 'test-safety-net' 2>/dev/null | head -1)
test -f "$SKILL_DIR/assets/rank_risk.py"     || echo "SKILL_DIR not resolved"
test -f "$SKILL_DIR/assets/io_guard.py"      || echo "SKILL_DIR not resolved"
test -f "$SKILL_DIR/assets/io_guard.js"      || echo "SKILL_DIR not resolved"
test -f "$SKILL_DIR/assets/io_guard_go.py"   || echo "SKILL_DIR not resolved"
test -f "$SKILL_DIR/assets/io_guard_rust.py" || echo "SKILL_DIR not resolved"
test -f "$SKILL_DIR/assets/contract_check.py" || echo "SKILL_DIR not resolved"
```

The ranker and the python, go and rust guards are stdlib-only python3 — the go guard drives the
machine's own `go`, the rust guard the machine's own `cargo` and `rustc`; the node guard is
dependency-free CommonJS for node 18+. All five are offline: no guard ever downloads a toolchain,
and the rust guard builds `--offline`, so a dependency missing from the cargo cache is refused
(step 1), never fetched.
Do not author your own guard: the one that ships is
what Invariant 2 is enforced by, and a hand-rolled substitute that patches the wrong layer is worse
than none, because it makes the invariant look enforced when it is not.

## Workflow

1. **Detect** the stack and — critically — how to run exactly **one** test, not just the suite.
   The ranker detects the stack itself and prints its verdict and the evidence behind it on
   stderr (`note: stack=python (evidence: python=52, node=18, go=0, rust=0)`); read that line
   rather than assuming, and pass `--stack` when it is wrong or when it reports a tie. Four stacks
   are complete end to end — **python**, **node/TypeScript**, **go** and **rust** — because each
   ships a runtime guard that can prove the no-I/O invariant during step 4. A repo none of them
   claims gets `note: no stack claims <repo>` on stderr and an empty plan — read that line and
   stop. Consult `references/stacks.md` for the row that applies, and stop plainly, without
   improvising, for anything with no complete row. Single-test invocation is load-bearing: step 4's
   proof is impossible without it.

   **On node, check the runner before you plan to write anything.** A repo that already configures
   **jest, vitest or mocha** — a `jest`/`vitest` key in `package.json`, a `jest.config.*`,
   `vitest.config.*` or `.mocharc.*` — is a **rank-and-report** repo in this version: rank it, hand
   over the report, and say which runner blocked the writing half. The tests this skill writes are
   `node:test` tests proven under `node --require`, and dropping one into a jest suite either is not
   collected (no safety net at all) or is collected and fails. That shape is not an edge case here —
   "a JS/TS repo with no `*.test.js` to its name" is exactly how this skill gets triggered, and a
   repo with jest configured and no tests yet is the commonest instance of it.
   `references/stacks.md` has the full reasoning.

   **On go, make sure the module's dependencies are already downloaded.** The go guard runs
   `go test` with `GOPROXY=off` and `GOTOOLCHAIN=local` — a proof run never fetches a module or a
   toolchain — so on a cold module cache run `go mod download` yourself first. A missing module
   shows up as exit 5 (the package did not build), never as a verdict about the unit.

   **On rust, check the lockfile, the toolchain and the cargo cache before you plan to write.**
   The rust guard builds with `cargo test --locked --offline` and runs every `rustc` and `cargo`
   call with `RUSTUP_AUTO_INSTALL=0`, so it never writes `Cargo.lock` and never downloads a
   toolchain or a crate. Every proof exits 2 (NOT ARMED) in a repo with no `Cargo.lock` or a stale
   one, with a `rust-toolchain.toml` pin below 1.82 or not installed, or with a dependency not in
   the local cargo cache. The remedy for a missing lockfile is `cargo generate-lockfile`. It writes
   a file into the user's tree, which this skill never does on its own, so you MUST ask first. The remedy
   for an uncached dependency is `cargo fetch` (or building the tests once), then re-run: the proof
   never downloads anything.

2. **Rank** the risk surface:

   ```sh
   python3 "$SKILL_DIR/assets/rank_risk.py" <repo> [--top-n N] [--since "6 months ago"] \
     [--stack python|node|go|rust]
   ```

   Offline, stdlib + git only, deterministic. Read `references/parameters.md` for the full
   argument and output-key reference. The output's `ranked` array is what you show the user next;
   `remainder` is where the next run resumes; `not_netted` is the Tier 3/4 seam-and-refactor list
   for `clean-code`; `covered` is a flat index of already-tested unit ids, not a fourth bucket — a
   `not_netted` unit can also appear in `covered`. `discovery` says which reader found the units
   (`precise` = a real parser, `heuristic` = a text reader); a node repo reads `heuristic` unless
   it ships its own `typescript`, a go repo reads `precise` wherever `go` is on PATH, a rust repo
   always reads `heuristic` (Rust has one reader, and the ranker never runs cargo), and two runs
   are only comparable when it agrees. **Carry that
   value into your report's header**, as the template below does: a run that silently degraded is a
   run whose numbers cannot be compared to the last one's. Node's precise path reaches that repo's
   own `typescript` by `require`ing it, so it EXECUTES code from the tree you are analysing; pass
   `--no-precise` on any repo you were handed rather than wrote, and expect `heuristic` and fewer
   units in exchange (`references/parameters.md`). Go's precise path executes none of the analysed
   repo's code — it is this skill's own `go/ast` helper reading the tree as text — so it is safe to
   leave on.

   Every unit lands in exactly one of the four testability tiers described in full in
   `references/triage.md` — read it before writing anything. In short: Tier 1 (direct) gets a
   unit test; Tier 2 (wider boundary) gets pinned at a controlled boundary and named in the
   report; Tier 3 (needs a seam) gets reported to `clean-code`, never acted on here; Tier 4 (not
   reachable) gets a prioritized refactor reason. **Treat `tier` as a starting hypothesis, not a
   verdict** — see the next section for why, and read `references/triage.md`'s "Tier 2 boundary
   controls" before assuming a database or HTTP unit is a valid Tier 2 pin.

   **A Tier 2 row's `tier_reason` names only one controllable group — the unit may hit more.**
   `rank_risk.py` reports the alphabetically-first group a unit touches, so a unit hitting both
   the clock and the filesystem reports only "clock." Before writing a Tier 2 test, inspect the
   unit itself (its `path`/`lineno` from the JSON row) and identify **every** controllable group
   it actually reaches — do not treat `tier_reason` as the complete list of what to fake. Faking
   only the named group while a second, unnamed one stays live produces a test that looks like it
   pins behaviour while still performing real I/O.

   The ranker's triage is conservative on purpose — it would rather under-tier a unit than
   over-tier one into a false Tier 1. If inspection shows a Tier 3/4 unit is actually reachable at
   a controlled boundary, you may promote it, but only by **recording** the promotion (the tier
   the ranker assigned, the tier you used instead, and why) in the report below. You MUST NOT
   silently treat a ranker tier as advisory.

3. **Confirm with the user before writing anything.** Show the `ranked` top N (default 10) and
   the size of `remainder`/`not_netted`. This is a hard gate — you MUST NOT proceed past it
   unconfirmed, unless
   `python3 "$SKILL_DIR/assets/contract_check.py" check-grant --root <repo> --action local_reversible`
   exits 0 (an autonomy grant the user approved covers it); then you MAY proceed without asking,
   and MUST name the grant id and action class in the report. Any other exit (3 ASK/NONE, 2
   INVALID, 1 usage error) means ask as usual. A grant lets test-writing proceed and nothing more: Invariant 1
   still binds, so source stays untouched under a grant too.

4. **Write and prove, one unit at a time.** For each confirmed unit:
   1. Write the test at the unit's tier (unit test at Tier 1; a narrow characterization test at
      the named Tier 2 boundary).
   2. Write a **deliberately wrong** expected value.
   3. Run the single test. **Require RED.** If it doesn't go red, the assertion isn't binding to
      real output — discard the test, don't ship it.
   4. Correct the expected value to the real captured output (see the literal-emission rule
      below).
   5. Run the single test again. **Require GREEN.**

   > **Stated limit.** This proves the assertion binds to real output from the unit. It does not
   > prove the test detects every behavioural change, which is what true mutation testing would
   > give. Do not oversell it.

   **The runtime guard, not the tier, is what enforces "never real I/O."** Static triage is a
   filter — it declines obvious hazards, but dynamic dispatch (Python's `getattr`, JavaScript's
   computed member access and dynamic `import()`, Rust's trait objects and macros) means it cannot
   decide reachability from source alone, and ordinary constructions (an aliased import, a
   same-module helper, an argument default) reach real I/O past it. So the invariant is enforced during
   this red→green proof by the **tier-aware runtime guard this skill ships for the stack you are
   on** — one per stack, each loaded on the
   single-test invocation itself and never written into the repo, so Invariant 1 stays clean with
   no carve-out. **Run every RED and every GREEN through it.**

   On **python** that is `assets/io_guard.py`, loaded as a **pytest plugin via `-p`** (never a
   `conftest.py` written into the repo, and never a fixture inside the generated test) — a plugin
   loads ahead of collection, which is what makes pre-import blocking work:

   ```sh
   # Tier 1 candidate — the unit claims to touch nothing, so block everything.
   PYTHONPATH="$SKILL_DIR/assets:$PYTHONPATH" TEST_SAFETY_NET_TIER=1 \
     pytest -p io_guard <path>::<test_name>

   # Tier 2 candidate — name EVERY controllable group this test deliberately fakes.
   PYTHONPATH="$SKILL_DIR/assets:$PYTHONPATH" \
     TEST_SAFETY_NET_TIER=2 TEST_SAFETY_NET_ALLOW=filesystem,clock \
     pytest -p io_guard <path>::<test_name>
   ```

   **Copy the whole block, not just the `pytest` line.** The `PYTHONPATH` assignment is what makes
   `-p io_guard` resolvable; without it the run dies with `ImportError: Error importing plugin
   "io_guard"` before a single test executes. Both commands above are extracted from this file and
   run verbatim by the skill's own test suite, so what is printed here is what is proved to work.

   On **node/TypeScript** that is `assets/io_guard.js`, preloaded with `--require` — the same
   arm-before-the-unit-loads property `-p` gives on pytest, and likewise nothing written into the
   target repo:

   ```sh
   # Tier 1 candidate — the unit claims to touch nothing, so block everything.
   TEST_SAFETY_NET_TIER=1 \
     node --require "$SKILL_DIR/assets/io_guard.js" \
     --test --test-name-pattern '^<test_name>$' <path>

   # Tier 2 candidate — name EVERY controllable group this test deliberately fakes.
   TEST_SAFETY_NET_TIER=2 TEST_SAFETY_NET_ALLOW=filesystem,clock \
     node --require "$SKILL_DIR/assets/io_guard.js" \
     --test --test-name-pattern '^<test_name>$' <path>
   ```

   **Copy the whole block, not just the `node` line.** `--require` is what arms the guard *before*
   the test file — and therefore before the unit — is loaded; a guard that arms later cannot see
   import-time I/O, which is exactly the case the filter floors to Tier 3. `--test-name-pattern` is
   what makes it one test rather than the file. These two blocks are extracted from this file by
   `assets/test_io_guard_node.sh` and run verbatim against a clean unit and a leaking one, so what
   is printed here is what is proved to work.

   **On node, read the exit status, not the per-test results.** When a violation lands *after* the
   test that caused it has resolved, `node --test` charges it to whatever the runner is executing at
   that moment — one later test, **several** later tests, or, when nothing else is running, the
   enclosing file. Never the test that caused it: that one reports `ok` in every case. All three
   shapes reproduce on **one and the same node build**; what selects between them is when the
   violation lands relative to the tests around it, not the runtime version — so "our node is newer"
   is not a reason to trust the per-test lines. Reproduced verbatim on node v22.18.0, and on every
   LTS line from 18 to 26 (these are TAP lines — node 26's default reporter is `spec`, so pass
   `--test-reporter=tap` to see them in this form; the exit status needs neither):

   ```
   ok 1     - violator                              <- the test that violated
   not ok 2 - innocent_short                        <- an innocent test
   not ok 3 - innocent_long_running_when_it_lands   <- and another
   exit 1
   ```

   An agent reading per-test results keeps the test that performs real I/O and discards two that did
   nothing wrong. There is no salvageable subset to be read out of the TAP stream, which is what the
   second bullet below rests on. So, in the node proof loop:
   - **The exit status is the only trustworthy signal on this stack.** A zero exit means the batch
     is clean; a nonzero exit means something in it violated.
   - On a nonzero exit whose failure is an `IOGuardViolation`, **discard the whole batch and
     re-prove one test at a time.** Per-test attribution cannot be trusted for an async violation,
     and a batch is cheap to re-run.
   - **You MUST NOT keep a test reported `ok` from a run that exited nonzero.**

   `assets/test_io_guard_node.sh` builds all three fixtures (assertions 12, 13 and 14) and asserts
   both the invariant they share — the culprit reporting `ok` while some *other* entry carries the
   `not ok` and the run exits nonzero — and, separately, that **two** innocent tests can fail from
   one violation. So the rule cannot rot into prose while the behaviour it describes drifts.
   `references/stacks.md` shows all three transcripts and what selects between them.

   On **go** that is `assets/io_guard_go.py`, a wrapper around `go test` that compiles hooks into
   the standard library for this one run (`go test -overlay`), so the whole test binary — every
   `init()` included — runs guarded, and likewise nothing is written into the target repo:

   ```sh
   # Tier 1 candidate — the unit claims to touch nothing, so block everything.
   TEST_SAFETY_NET_TIER=1 \
     python3 "$SKILL_DIR/assets/io_guard_go.py" \
     -run '^<test_name>$' <package>

   # Tier 2 candidate — name EVERY controllable group this test deliberately fakes.
   TEST_SAFETY_NET_TIER=2 TEST_SAFETY_NET_ALLOW=filesystem,clock \
     python3 "$SKILL_DIR/assets/io_guard_go.py" \
     -run '^<test_name>$' <package>
   ```

   **Copy the whole block.** `<package>` is the package directory (`./internal/billing`), and the
   `-run` anchors matter for the reason they do on node: the pattern is a substring regex. Tests go
   in `<file>_test.go` beside the source, **in the same package**, appended to when the file
   exists and never overwritten. These two blocks are extracted from this file by
   `assets/test_io_guard_go.py` and run verbatim against a clean unit and a leaking one.

   **On go, the exit status is the whole protocol:**

   | exit | outcome | the proof loop |
   |---|---|---|
   | 0 | GREEN — the test ran and passed | keep it, if this was the corrected run |
   | 1 | RED — an assertion failed | expected on the deliberately-wrong run |
   | 2 | NOT ARMED — the guard could not arm | fix the environment; nothing was proved |
   | 3 | GUARD TRIP — `IOGuardViolation` | reclassify the unit to Tier 3 and discard the test, red or green |
   | 4 | NO TEST — nothing was proved | `-run` matched nothing, the package has no test files, or the test skipped itself; fix it — not GREEN |
   | 5 | NO BUILD — the package did not build | fix the test source; a compile error is not RED |

   A trip ends the process (`syscall.Exit(3)`), so `recover()` cannot swallow it and a goroutine's
   trip cannot be charged to another test. `-count=1` is forced: the guard's configuration is
   invisible to `go test`'s result cache, and a pass cached at one tier would otherwise be replayed
   at another. **On go, `randomness` is not a controllable group** — `rand.Seed` is a no-op since
   Go 1.24, so a unit drawing from the global `math/rand` source is Tier 3 and
   `TEST_SAFETY_NET_ALLOW=randomness` is refused with a note. Go's clock control is
   `testing/synctest`.

   On **rust** that is `assets/io_guard_rust.py`. It builds the test with `cargo test --locked
   --offline --no-run`, then runs the compiled binary under a preloaded hook library that
   intercepts libc and attributes each call to the function that made it:

   ```sh
   # Tier 1 candidate — the unit claims to touch nothing, so block everything.
   TEST_SAFETY_NET_TIER=1 \
     python3 "$SKILL_DIR/assets/io_guard_rust.py" \
     --test <test_file_stem> <test_name>

   # Tier 2 candidate — name EVERY controllable group this test deliberately fakes.
   TEST_SAFETY_NET_TIER=2 TEST_SAFETY_NET_ALLOW=filesystem \
     python3 "$SKILL_DIR/assets/io_guard_rust.py" \
     --test <test_file_stem> <test_name>
   ```

   **Copy the whole block.** Run it from the crate root, adding `-p <package>` for a workspace
   member. Tests go in `tests/tsn_<module_path>.rs` and reach the unit through its public path
   (`use <crate>::<path>::<item>;`). Append to that file when it exists; never overwrite it. The exit
   table is go's. Rust controls two groups, filesystem and environment, through two helpers that you
   copy into the test file verbatim:

   ```rust
   #[inline(never)]
   fn tsn_control_temp_dir() -> std::path::PathBuf {
       use std::sync::atomic::{AtomicUsize, Ordering};
       static N: AtomicUsize = AtomicUsize::new(0);
       let dir = std::env::temp_dir().join(format!(
           "tsn-{}-{}", std::process::id(), N.fetch_add(1, Ordering::Relaxed)));
       std::fs::create_dir_all(&dir).expect("tsn_control_temp_dir");
       dir
   }

   struct TsnControlEnv { key: String, old: Option<std::ffi::OsString> }

   impl Drop for TsnControlEnv {
       #[inline(never)]
       #[allow(unused_unsafe)]
       fn drop(&mut self) {
           match &self.old {
               Some(v) => unsafe { std::env::set_var(&self.key, v) },
               None => unsafe { std::env::remove_var(&self.key) },
           }
       }
   }

   #[inline(never)]
   #[allow(unused_unsafe)]
   fn tsn_control_set_env(key: &str, value: &str) -> TsnControlEnv {
       let old = std::env::var_os(key);
       unsafe { std::env::set_var(key, value) };
       TsnControlEnv { key: key.to_string(), old }
   }
   ```

   - **An environment-controlled test goes alone in `tests/tsn_<module_path>_env_<n>.rs`.** The
     guard refuses a file that breaks this.
   - **Keep each helper's `#[inline(never)]`.** At opt-level 1 or above, an inlined helper stops
     being a frame of its own, and an honest control run trips.
   - Clock and randomness are uncontrollable on rust. `TEST_SAFETY_NET_ALLOW=clock` is refused with
     a note.
   - A unit the ranker tiers 3 as *not reachable* or *binary-only* is reported, never tested.
   - **Import the unit with exactly the `use` line its `tier_reason` names.** Every reachable Tier 1
     or 2 rust unit's reason ends ``; import as `use <public_path>;` ``. That path is the shortest
     one a `tests/` crate can name: a re-export when one is shorter, and for a method, its type's.
   - **`-p <package>` is required when more than one package has `tests/<stem>.rs`.** The guard exits
     2 otherwise. A test name cannot begin with `-`.
   - **The rust ways to reach exit 4 (NO TEST).** An `#[ignore]`d test, and a name that matches no
     test. The test process exiting early: the hook forces status 125 when a crate frame calls `exit`
     or `quick_exit`, so a unit that calls `process::exit` cannot be proven in-process. A binary that
     does not link libtest's harness (`harness = false`). The 300 s timeout. libtest exits 0 for the
     first two, which is why the guard reads its result lines rather than its exit code.
   - A compile error is exit 5, never RED; cargo exits 101 for both.
   - Rust has ten extra ways to reach exit 2 (NOT ARMED), beyond step 1's lockfile and toolchain:
     - a dependency not in the local cargo cache (run `cargo fetch`, or build the tests once; the
       proof never downloads anything);
     - a static, stripped or musl test binary;
     - a list of the crates' own unmangled fns that cannot be built or does not fit the hook --
       most often an `lto` setting (fat or thin) in `[profile.dev]`/`[profile.test]`, or `-C
       linker-plugin-lto`, which makes rustc write LLVM bitcode rlibs: set `lto = false` for the
       profile the tests build with;
     - a hook that failed to load, or that could not attribute a call;
     - an environment-controlled test that is not alone in its file;
     - several packages and no `-p`;
     - a test name beginning with `-`;
     - a test or lib target named like one of Rust's own crates (`tests/test.rs` compiles to
       crate `test`, read as libtest's own work) -- rename it;
     - the hook failed to build with this toolchain;
     - no `rustc` or `cargo` on PATH.
   - The guard never passes `--nocapture`, and you MUST NOT add it: under it the default panic hook reads
     `RUST_BACKTRACE`, and every RED would trip `environment`.

   The tier is passed per invocation by environment variable, which is sufficient because the proof
   runs one unit at a time — a single run has a single tier:
   - **Tier 1 candidate:** blocks *everything* — filesystem, clock, randomness, environment,
     network, subprocess, DB — and ignores `TEST_SAFETY_NET_ALLOW`. The unit claimed to touch
     nothing, so any touch falsifies the classification: reclassify the unit to Tier 3 and
     discard the test.
   - **Tier 2 candidate:** blocks the uncontrollable groups always, plus every controllable group
     you did *not* name in `TEST_SAFETY_NET_ALLOW`. The boundary this test controls (a temp dir, a
     frozen clock) is the point, not a violation — so name it, and name **all** of it. Omitting the
     variable falls back to permitting every controllable group the stack has — four on python and
     node, three on go, two on rust — which is looser than the test actually needs.

   The guard patches the **lowest** layer reachable, which for CPython is the `os` primitives, the
   `_io` C module, and the file-object constructors — every name the ranker's marker tables
   classify on, including the directory-and-metadata family (`os.listdir`, `os.scandir`, `os.walk`,
   `os.stat`, `os.rename`) that no fd-level patch can see. `os.open` is not the builtin `open`;
   `pathlib` reaches neither; `pkgutil.get_data` reaches neither *and* neither `io` name;
   `os.posix_spawn` never routes through `subprocess.Popen`. Two consequences worth knowing before
   you read a trip: a patched **class** (`socket.socket`, `mmap.mmap`, `subprocess.Popen`) is
   replaced by a guarded subclass, so `isinstance` against an object built before arming is False;
   and a violation raised on a worker thread is re-raised on the main one, so it lands as a real
   failure rather than a warning beside a green run. The full patch list, the filter↔guard
   coverage table and the residuals are in `references/triage.md`.

   The node guard makes the same choice at node's own lowest layer — every own function of
   `node:fs`, of `node:fs/promises` (a *different* set of functions from the callback face), of
   `node:net`/`node:http`/`node:child_process` and the rest — each replaced by a `Proxy` so a
   patched class stays a class. Its patch list, its two-layer partition against the filter and its
   seven residuals are in `references/stacks.md`; two of those residuals change what a trip means,
   so read them before you read one.

   The go guard hooks the lowest layer Go itself has: every classified function of package
   `syscall` — the same 275-name table the filter reads — `internal/syscall/unix`'s `*at` and
   resolver families, and the clock, randomness, database and network entry points whose group
   decides a call made beneath them. It decides each call by call provenance (`runtime.Callers`),
   as the node guard does; its decision rule, patch table and residuals are in
   `references/stacks.md`.

   The rust guard hooks libc beneath the compiled test binary: the open/stat family, the
   environment calls, sockets, the clocks, the entropy calls and the spawn/exec family. It decides
   each call by the Rust frame that made it, walked with `backtrace()`, and ends the process with
   `_exit(3)` on a trip, so `catch_unwind` cannot swallow one. Its intercept table, decision rule
   and residuals are in `references/stacks.md`. Read residuals 1, 9, 10 and 11 before you trust a
   GREEN: anything that bypasses libc is unseen whatever its intent (a dependency's raw syscall
   included), deliberate verdict forgery by the code under test is outside the threat model, and
   at opt-level 1 or more a crate's generic `Drop` holding the control helper can read GREEN. A
   `#[no_mangle]`/`#[export_name]` callback that only C or std frames invoke on the main thread
   (an `atexit` handler, a signal handler) is judged as a crate frame when the crates' own code
   defines it; one defined in a build script's bundled C, or in any object cargo did not build
   into an rlib, names no crate and reads GREEN; so does, at opt-level 1 or more, a callback whose
   last act is a tail-called libc call, which leaves no frame of its own. A crate that exports a libc-named
   symbol (a `#[no_mangle] getenv` wrapper) trips every honest run instead: that fails closed.

   **The guard raises its own exception type, distinct from `AssertionError`.** The proof run has
   three outcomes, not two: an `AssertionError` is the RED half of red→green (the expectation is
   wrong); the guard's exception means the *classification* is wrong. If the guard's exception
   appears at any point — during the deliberately-wrong run or the corrected one — reclassify the
   unit to Tier 3 and discard the test, regardless of whether that run was red or green. Treating
   a guard trip as an ordinary assertion failure defeats the mechanism.


5. **Report.** Emit the deliverable below, then stop — the ranked remainder is what the next run
   resumes from.

## Invariants (do not violate)

1. **You MUST NOT modify source.** You MUST only create test files. When a test file exists, you
   MUST **append** to it and MUST NOT overwrite it. This is what makes the skill safe to run
   unattended on a repo nobody trusts yet — and why Tier 3 seams are reported, never applied.
2. **You MUST NOT write a test that performs real I/O.** Enforced by the tier-aware runtime guard in
   step 4, not by the static tier alone — see `references/triage.md` for the full mechanism and
   why the tiers cannot enforce this on their own.
3. **You MUST NOT ship an unproven test.** A test that did not go RED MUST be discarded and listed
   under "could not prove," never shipped.
4. **You MUST NOT leave the suite red.** End state is a green suite plus suspected bugs in the report. A
   red generated test is a bug in this skill, not an acceptable outcome.
5. **Hard gate before writing** — step 3's confirmation MUST happen before any test file is touched,
   unless
   `python3 "$SKILL_DIR/assets/contract_check.py" check-grant --root <repo> --action local_reversible`
   exits 0; then you MAY write tests without asking, and MUST name the grant id and action class
   in the report. A grant never lifts Invariant 1.

## The literal-emission rule

Captured output from running the user's code gets embedded into generated test files — that is
code generation from program output, and it is the one real injection surface in this skill.

> You MUST emit every captured value with `repr()`. You MUST NOT build a test's expected value by
> string concatenation or f-string interpolation of captured output. A captured string containing
> a quote, a newline or a backslash MUST become an inert literal, never executable source.

## Deliverable

```
## Test safety net: <repo>  (stack: python · discovery: precise · 3 added, 1 unproven, 1 needs a seam)
Gate: confirmed by user | grant <id> (<class>)

| unit                    | tier                 | kind             | test file              | proved    |
|-------------------------|----------------------|------------------|------------------------|-----------|
| billing/refund.py:apply | 1 direct             | characterization | tests/test_refund.py   | RED→GREEN |
| api/handler.py:post     | 2 (fs, clock)        | characterization | tests/test_api.py      | RED→GREEN |
| auth/hash.py:verify     | 1 direct             | specification    | tests/test_hash.py     | RED→GREEN |

The tier column for a Tier 2 row names EVERY controllable group actually faked (not just
`tier_reason`'s alphabetically-first one) — "2 (fs, clock)" means both the filesystem and the
clock were controlled, not only whichever one the ranker happened to name.

### Suspected bugs (pinned, NOT blessed)
- billing/refund.py:41 — pinned returns 0 for a negative amount; the docstring says it raises.

### Could not prove (discarded, not shipped)
- cache/keys.py:build — no single-test invocation found behind `make test`

### Not netted — needs a seam (hand to clean-code)
- db/pool.py:acquire (tier 3) — constructs its connection inline; smallest seam: accept an
  injected factory, default to current behaviour.

### Ranked remainder — next run starts here
1. db/pool.py:acquire (churn 14, inbound_refs 3 [approx])
```

No coverage percentage anywhere, in this report or in conversation about it.

**Promoted units** get a line in the report naming the ranker's original tier, the tier used
instead, and why — never a silent override (see step 2).

**How to improve this.** `inbound_refs` is a static approximation — an identifier-occurrence
count, not a call graph. It cannot tell a call from a comment, it misses a caller that reaches the
unit only through a re-export, and a **bare** occurrence of the name inside a file that references
the module still counts even when it means something else (a same-named local, or one imported
from a different module). Qualified forms are exact: `mod.name` counts, `buf.name` does not. Where
the user already has a real call-graph tool, it computes that half of the score better. This is an
**optional** upgrade, never a dependency — nothing here or in the eval requires one.

## Contract

This skill follows [skill-contract v1](https://github.com/dhanesh/agent-skills/blob/main/docs/skill-contract/SPEC.md).
It consumes autonomy grants, which only lift step 3's confirmation, and it provides no kind of
its own: the safety-net report is prose.

```json skill-contract
{"provides": [], "consumes": ["https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1"]}
```

## Verify and repair

Before reporting done: every unit in the report's table has a matching RED→GREEN log; the suite
you leave behind is green end-to-end (invariant 4); every "not netted" unit names its blocking
tier and reason rather than being silently dropped; and no generated test file was overwritten
instead of appended to. If any of these fail, fix it and re-check rather than reporting a false
done.
