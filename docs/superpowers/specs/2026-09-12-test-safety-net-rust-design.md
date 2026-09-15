# test-safety-net: the Rust stack

**Status:** design approved section by section by the owner, 2026-09-12, then revised by a
`clean-code` review the owner requested (see "Clean-code review"). Implementation plan to follow.
**Supersedes:** for Rust, the multistack spec's D2 (OS sandbox) and its per-stack row. See the amendment appended to
`docs/superpowers/specs/2026-09-09-test-safety-net-multistack-design.md`.

Rust is the last stack `test-safety-net` declines. This spec adds it. The workflow is the same
(rank → confirm → write+prove → report), with the same invariants, exit table and group names as
python, node and go. Everything below is Rust's own answer to the questions every stack answers
differently.

## Owner decisions, 2026-09-12

| # | Decision | Rejected alternatives |
|---|---|---|
| O1 | Prove **1.82, 1.86, 1.90, 1.94, 1.98**: every 4th minor, about 22 months. Versions in between are "expected by bracketing, not proven". A toolchain pinned below 1.82 is ranked, but its proof is declined with the reason. | The literal last 5 minors, 1.94–1.98, cover only about 7 months at Rust's 6-week cadence. |
| O2 | Tests go in **`tests/` only**. Invariant 1 stays absolute. | Appending `#[cfg(test)]` modules to source, whether always or as an opt-in. |
| O3 | The runtime guard is **libc interposition**. | Rebuilding std with hooks (`-Zbuild-std`, `RUSTC_BOOTSTRAP`); D2's OS sandbox. |
| O4 | Proofs build in the repo's **own target dir**, as cargo resolves it. | A separate target dir (a cold rebuild of every dependency). |

An evidence audit (`base-in-reality`, requested by the owner) checked all four against fetched primary
sources, with a three-lens refutation pass. All four held. Three negative findings were overturned in
refutation:
- "the 1.82 floor is too new": the guard is unproven below it, and rustup plus a pinned toolchain is the Rust norm;
- "the `tests/` rationale is misstated": Invariant 1 bans touching source bytes at all;
- "`main.rs` is reachable by spawning the binary": that is subprocess I/O, which Invariant 2 forbids.

Two findings stood and are designed in below:
- **A1 (rustix):** see "Build plan" in the guard section.
- **A2 (Cargo.lock):** see "Build" in the guard section.

### Clean-code review, 2026-09-12

The owner asked for a `clean-code` review of this spec before the plan was written. All five findings
were folded in:

1. **The filter/guard agreement test was missing.** Python, go and node each assert that every filter
   marker has a guard intercept or a recorded reason. Rust now does too, and the hook's name tables are
   rendered from Python so that each name lives in one place. See "Verification" and "The hook".
2. **The tier/allow contract would have had a third Python copy.** It is extracted into `guard_env.py`.
   See "Shared guard contract".
3. **The Rust guard had seven responsibilities in one file.** It is split into three files. See "The guard".
4. **The rustix path was a mode flag threaded through build and run.** It is now decided once, as a
   queried build plan.
5. **"Build dir" and "target dir" were mixed.** "Target dir" is used throughout, and the floor is one
   constant.

## Evidence the design rests on

Three throwaway spikes and a probe ran on 2026-09-12. None of them ran on amd64; CI's legs are the
amd64 proof.

- **Spike A (libc interposition, C hook).** Every probed std API trips with exit 3, attributed to the
  right function: env, fs, metadata, tcp, both clocks, `Command`, stdin, a `catch_unwind` wrapper,
  a spawned thread. libtest's own reads do not trip. Results were identical on macOS 1.92 and Linux
  glibc 1.82 and 1.92.
  - On Linux with default flags, frames do not resolve and **all I/O passed**. The fix is an in-hook
    reader for the `.symtab` of `/proc/self/exe`.
  - Stripped and static-musl binaries fail open.
  - A macOS dylib that uses `__thread` aborts in libSystem's initialiser.
- **Spike A2 (Rust hook).** A single Rust cdylib, built by plain `rustc`, reached parity with the C hook on
  1.82, 1.92 and 1.98, with no C compiler or SDK handling. On 1.82, a `panic=abort` cdylib has
  incomplete unwind tables, so `backtrace()` resolved nothing and **all I/O passed**, until
  `-C force-unwind-tables=yes` was added.
- **Spike B (patched std).** Viable, but it rests on `RUSTC_BOOTSTRAP=1` (which the Rust project "strongly
  discourage[s]") and on cargo's test-only `__CARGO_TESTS_ONLY_SRC_ROOT`. On 1.98 it had a real escape:
  `env::var` bypassed the hooked `var_os`. Rejected.
- **`cargo test` single-test table.** Measured identical on 1.82 (Linux) and 1.92:
  - a pass exits 0;
  - an assertion failure exits 101;
  - an `#[ignore]` exits **0**, with `1 ignored`;
  - a filter that matches nothing exits **0**, with `running 0 tests`;
  - a compile error exits **101**, with `error[E…]`.

  So the exit code alone lies twice.

## The stack: `assets/stack_rust.py`

It implements the 15-name stack interface in `rank_risk.py`.

- **Crates and targets** come from `cargo metadata --no-deps --offline --format-version 1`: workspace
  members, `lib`/`bin` targets, and the crate name used in `use` paths. Python 3.10 has no stdlib TOML
  reader, so asking cargo is also the only stdlib-only option.
  - **First plan task:** verify this command writes nothing, `Cargo.lock` included. If it does, add `--locked`.
- **Files.** A crate's files are `src/**/*.rs`. Skip `target/`, `vendor/`, `examples/`, `benches/` and
  `build.rs`. Test files are `tests/*.rs`. `scope_files(rel)` is `[rel]`, because a module is its file.
- **Units** are `pub fn` items and `pub` methods of inherent `impl` blocks, reachable from a `tests/`
  crate through the library root. A unit is reachable when every `mod` from `src/lib.rs` down is
  `pub mod`, or when the item is re-exported by `pub use`, named or glob. Everything else is ranked
  and reported, never tested:
  - private and `pub(crate)` items: *not reachable without modifying source*;
  - binary-only crates: *binary-only: subprocess, Tier 3*.

  A false "unreachable" only reports. A false "reachable" produces a test that does not compile, and
  NO BUILD discards it.
- **Discovery** is heuristic only: regex plus brace matching, with Rust-aware lexing (raw strings
  `r#"…"#`, nested block comments, lifetimes versus char literals, attributes). There is no precise path:
  the per-file `rustc -Zunpretty=ast-tree` dump needs `RUSTC_BOOTSTRAP`, and its format shifted between
  1.82 and 1.92. The report names the reader, as node and go do.
- **Placement.** One test file per source file, `tests/tsn_<module_path>.rs` (e.g. `src/calc/add.rs` →
  `tests/tsn_calc_add.rs`), importing through `use <crate>::<path>::<item>;`. Existing files are
  appended to, never overwritten. An environment-controlled test goes alone in
  `tests/tsn_<module_path>_env_<n>.rs`; see Tier 2.
- **Coverage credit** (`already_covered`) counts `tests/*.rs` that reach the unit through its public
  path or a `use`, and in-file `#[cfg(test)]` modules that reach it through `super::`. A method is
  credited only through a value bound to its type, the rule Go's review round forced.

## Triage and Tier 2

The group names are the seven every stack uses. Rust's controllable line:

| group | Rust |
|---|---|
| filesystem | **controllable**: `tsn_control_temp_dir()` |
| environment | **controllable**: `tsn_control_set_env(k, v)` |
| clock | uncontrollable: std has no freeze hook |
| randomness | uncontrollable: std has no RNG; `rand::thread_rng` cannot be seeded |
| database, network, subprocess | never auto-Tier-2, as on every stack |

- **Controls.** libtest has no controls, so the generated test file carries two fixed helpers, written by
  the skill, that the guard recognises by symbol name. This mirrors Go's `testing.(*T).TempDir`/`Setenv`.
  - `tsn_control_temp_dir()` builds a unique directory under `std::env::temp_dir()`. Its `TMPDIR` read
    counts as filesystem, as Go's `t.TempDir()` override does.
  - `tsn_control_set_env(k, v)` sets a variable and restores it on drop. Its body is wrapped in
    `unsafe {}`: required in edition 2024, a warning in 2021.
- **One environment test per file.** The environment is process-wide. libtest runs one file's tests on
  parallel threads, while cargo runs each test file as its own process, one after another. So an
  environment-controlled test that passes alone could be flaky in the full suite unless it has its own
  file. Go avoids this because `t.Setenv` refuses to run beside parallel tests; Rust has no equivalent.
- **A seeded `StdRng::seed_from_u64(1)`** is plain computation, and stays Tier 1.

**Marker tables:**

| group | markers |
|---|---|
| filesystem | `std::fs::*`, `File`, `OpenOptions`, the disk-touching `Path` methods (`exists`, `metadata`, `read_dir`, `canonicalize`, …), `tokio::fs`, `async_std::fs` |
| network | `std::net::*` (including `ToSocketAddrs`, which is DNS), `tokio::net`, `reqwest`, `hyper`, `ureq` |
| subprocess | `std::process::Command`, `tokio::process` |
| environment | `std::env::{var, var_os, vars, args, current_dir, set_var, remove_var, temp_dir, home_dir}` |
| clock | `SystemTime::now`, `Instant::now`, `thread::sleep`, `tokio::time` |
| randomness | `rand::{thread_rng, random}`, `OsRng`, `getrandom` |
| database | `sqlx`, `diesel`, `rusqlite`, `postgres` |
| stdin | `std::io::stdin` (tier 1 only, as on every stack) |

- **Static declines (Tier 4)** cover what no hook can see: `asm!`/`global_asm!`, `libc::syscall(`, and
  raw-backend `rustix` on Linux when the proof cannot be rebuilt against libc (A1).
- **FFI is not declined.** C code does its I/O through libc, and the hook sees it.
- **Lazy statics.** `LazyLock`, `once_cell::Lazy` and `lazy_static!` initialise on first use, inside the
  unit's call, and are judged there. Rust has no import-time floor like python's and go's.

## The guard

The guard is three files, split by reason to change:

- **`assets/io_guard_rust.py`** orchestrates the proof and classifies its outcome. It owns the hook's
  name tables and renders them into the hook source.
- **`assets/rust_binary.py`** inspects an executable: dynamic or static, glibc or musl, whether it has
  crate symbols. It is pure functions over the file's bytes, for both ELF and Mach-O, tested against
  fixture binaries. It changes for platform reasons, not cargo reasons.
- **`assets/io_guard_rust_hook.rs`** is the hook's source, a real asset file rather than a Python
  string like Go's `_ENGINE`. `rustc` compiles files, and a `.rs` file can be formatted and linted.

Its arguments and exit table are Go's, from `guard_env.py`:

| exit | meaning |
|---|---|
| 0 | GREEN |
| 1 | RED |
| 2 | NOT ARMED |
| 3 | TRIP |
| 4 | NO TEST |
| 5 | NO BUILD |

It never runs a proof it cannot guard.

1. **Preconditions.** Any failure exits 2, with the reason.
   - The OS is darwin or linux.
   - `rustc -V` run in the repo (so `rust-toolchain.toml` is honoured) is ≥ `MIN_RUST`. That is one
     constant, 1.82. A lower pin is declined (O1).
   - `Cargo.lock` exists (A2); otherwise decline, and point to `cargo generate-lockfile`.
   - **Build plan (A1).** The build mode is decided once. A query, `build_plan(repo)`, returns the
     target dir, any extra `--cfg`, and the reason. Build and run take the plan and never branch on the
     mode.
     - **Normally:** cargo's own target dir, and no flags.
     - **On Linux, when `cargo metadata` shows raw-backend `rustix`:** a separate out-of-repo target
       dir (`CARGO_TARGET_DIR`, keyed by workspace) with `--cfg=rustix_use_libc`, so the user's cache
       is never invalidated.

     If the planned build cannot be made, exit 2.
2. **Build.** `cargo test --locked --no-run --message-format=json [-p <pkg>] --test tsn_<x>` (A2: a plain
   build was shown to create or rewrite `Cargo.lock`).
   - The executable path comes from the JSON artifact. The target dir is the build plan's: normally
     cargo's own, honouring `CARGO_TARGET_DIR` and `build.target-dir`.
   - A compiler error exits **5**, never 1.
   - The skill adds no `RUSTFLAGS`, except in the build plan's separate target dir.
   - Time waiting on cargo's file lock is excluded from the proof timeout.
3. **Refusals.** Exit 2 for a binary that is static, stripped (no crate symbols) or musl: each makes the hook fail open.
4. **The hook.** `io_guard_rust_hook.rs`, built by the repo's own `rustc` with
   `--crate-type cdylib -C panic=abort -C force-unwind-tables=yes`. It is cached outside the repo, keyed
   by toolchain and by the hash of the rendered source.
   - **One place for every name.** Its name tables live in `io_guard_rust.py` and are rendered into the
     source before compiling, as `io_guard_go.py` renders its engine. The tables are:
     - the intercepted calls, per group;
     - the libtest runner prefixes;
     - the `tsn_control_*` helpers and the groups they stand for;
     - the per-toolchain seed-path names.

     So the agreement test in "Verification" checks the same tables the hook enforces. It is loaded with `DYLD_INSERT_LIBRARIES` (via an `__interpose` section)
   on macOS, and `LD_PRELOAD` (via `dlsym(RTLD_NEXT)`) on Linux.
   - **Arm handshake.** The constructor reports that it loaded, and a self-check resolves one known crate
     frame. Either failing is exit 2; this closes Spike A2's 1.82 fail-open.
   - **The hook path uses** no TLS (a static atomic plus a pthread key made in a constructor), no
     allocation, no fmt, no std I/O and no panics. It writes raw, then calls `_exit(3)`.
   - **`open`/`open64`** are defined with fixed arguments in the ABI's slots, since stable Rust cannot
     define C-variadic functions: 3 arguments on SysV/AAPCS64, and `mode` as the 9th slot on Apple arm64.
5. **Intercepted, per group:**

   | group | libc calls |
   |---|---|
   | filesystem | open/openat/open64, the stat family and statx, access, mkdir, unlink, rename, opendir |
   | environment | getenv, setenv, unsetenv |
   | network | socket, connect, bind, getaddrinfo |
   | clock | clock_gettime, gettimeofday, mach_absolute_time, clock_gettime_nsec_np |
   | randomness | getrandom, getentropy, arc4random_buf |
   | subprocess | posix_spawn(p), fork, execve |
   | stdin | read on fd 0, at tier 1 |

6. **Decision rule.** Walk `backtrace()`, resolving symbols with `dladdr` on macOS and with the `.symtab`
   of `/proc/self/exe` on Linux.
   - Frames in `std`, `core` and `alloc`, and HashMap's seed path, are transparent. Seed-path names are
     pinned per toolchain: `std::sys::pal::unix::rand` in 1.82, `std::sys::random::linux` in 1.92.
   - A crate frame must carry Rust's `17h<16hex>E` hash suffix, so a runtime's C++ `_ZN` symbols are
     never read as crate code.
   - libtest's own frames (`test::…`) are runner work, and exempt.
   - `tsn_control_*` frames override the group.
   - The **first other frame** decides, whether it belongs to the repo's crate, the test crate or a
     third-party crate, as on go and node. I/O made through `reqwest` is therefore judged.
   - **A thread with no attributable frame, that is not libtest's, is judged, not exempt.** Go's
     "no repo frame" exemption let `go <stdlib func>` escape, and `thread::spawn(<std fn>)` has the
     same shape. So this rule fails closed from the start.
   - A call is blocked when its group is blocked at the tier.
7. **Classify** from the result line, never from the exit code alone:
   - an `IOGuardViolation` line exits 3;
   - `1 passed` with nothing ignored exits 0;
   - `1 failed` exits 1;
   - `running 0 tests`, or an ignored test, exits 4;
   - `error[E…]` or "could not compile" exits 5.

   The verdict-spoofing residual (repo code sharing stdout) is shared with go.

## Shared guard contract: `assets/guard_env.py`

`io_guard.py` and `io_guard_go.py` each implement the same tier/allow contract, and the Go docstring
says so:

- an absent or invalid tier falls back to tier 1, with a note;
- `none` allows nothing;
- an unknown group is ignored and stays blocked;
- tier 1 ignores the allow list.

The copies differ only in which groups a stack can control. The Rust guard would have made a third
copy, so the identical parts move into one module:

- `read_env(env, controllable, uncontrollable, why_uncontrollable)`;
- `blocked_groups(tier, allow, groups, controllable, uncontrollable)`;
- the exit codes (`EXIT_GREEN` … `EXIT_NO_BUILD`) and their outcome messages.

Classifying output stays in each stack's guard, because it reads a different test runner's format.

**Order and scope:**

1. `io_guard_go.py` moves to `guard_env.py` first, as its own behaviour-preserving commit. The Go
   suites run green before and after it; refactor on green.
2. `io_guard_rust.py` then imports the module.
3. `io_guard.py`, the pytest plugin, keeps its copy for now, because its `allow` representation and
   its module-level read at import differ. Adopting the module there is a stated follow-up.
4. `io_guard.js` cannot share Python.

## Verification

- **`test_stack_rust.py`** covers lexing, reachability (pub-mod chains, named and glob `pub use`,
  `pub(crate)`), coverage credit, triage per group, the static declines and registration.
- **`test_io_guard_rust.py`** runs real cargo fixtures through the wrapper:
  - every group trips at tier 1; each controllable group is permitted at tier 2;
  - the control helpers;
  - `catch_unwind`, spawned threads, and `thread::spawn(<std fn>)`;
  - libtest's own work does not trip;
  - each refusal: static, stripped, musl, no `Cargo.lock`, a pin below the floor;
  - the handshake self-check, `--locked` and the rustix path;
  - every row of the classify table.

  It skips cleanly without cargo.
- **Filter/guard agreement.** `io_guard_rust.py` carries `FILTER_MARKER_INTERCEPTS`,
  `PARTIALLY_INTERCEPTED` and `NOT_INTERCEPTED`, and a test asserts that together they partition
  `stack_rust`'s marker tables exactly. This is the same test python and go carry. A marker added to
  the filter with no hook intercept, and no recorded reason, fails the gate instead of opening a
  silent hole.
- **`rust_binary.py`** is tested against fixture binaries, static and dynamic, stripped and
  unstripped, glibc and musl. It needs no cargo.
- **`guard_env.py`** has its own tests. Its refactor commit leaves the Go guard's suites unchanged
  and green.
- **Mutation discipline, as on go.** Every fix is pinned by a test that turns red when the fix is removed.
- **Eval checks 45–49** mirror go's 40–44:
  - 45: detection and discovery;
  - 46: the report names its reader;
  - 47 (negative): `target/`, `vendor/`, `tests/`, `examples/`, `benches/`, `build.rs` and unreachable
    `pub` items are never units;
  - 48: tiering;
  - 49: the documented guard command, run verbatim, passes a clean unit and exits 3 on real I/O.
- **CI.** Five Rust legs in the `versions` job — 1.82, 1.86, 1.90, 1.94, 1.98 — on `ubuntu-latest`,
  which is **amd64**. The full gate pins 1.98. A macOS leg runs 1.98; whether the macOS runner can link
  1.82 is checked in CI (this host's macOS 27 SDK cannot). If it cannot, the macOS floor is stated as
  unproven.
- **A/B,** under `SINCE_TSN_RUST`:
  - Rust units are discovered where the baseline finds 0;
  - tiers are correct;
  - the guard trips a tier-1 read;
  - python, node and go rank byte-identically.

## Residuals, stated in the shipped docs

1. Anything bypassing libc is unseen: inline asm, raw syscalls. `rustix` is handled by A1.
2. Static, stripped and musl binaries are refused rather than guarded.
3. The seed-path and runner exemptions are names, pinned per toolchain.
4. Life-before-main crates (`ctor`) are untested. A preloaded library's initialiser normally runs
   before the executable's constructors, so their I/O is probably seen, but whether it is attributed
   correctly is unverified. A plan task settles it. **Closed in Task 10 (R33):** the real `ctor`
   1.0.13 trips with exit 3 on darwin 1.92/1.98 and on linux 1.94 aarch64, attributed to the
   crate's own constructor symbol, and `tsn-hook: armed` is printed before the trip. A test pins
   the mechanism without vendoring `ctor`: the `#[used]` init-array fn pointer it expands to.
5. Verdict lines share stdout with repo code, so an `init`-style print can spoof one (as on go).
6. 1.86 and 1.90 are first run in CI, not in the spikes. (Task 10: run green locally on linux
   arm64 and in CI on amd64.)
7. The macOS floor may be Linux-proven only. **Closed in Task 10:** a `macos-latest` (arm64) job
   on 1.82 linked and passed, so it is kept as a `versions` leg.
8. Rust's own support policy covers only the latest stable, so the four older legs are this skill's
   claim, not the Rust project's.

## Amended during implementation, 2026-09-13

Controller rulings made while the plan ran changed these decisions. The shipped documents
(`SKILL.md`, `references/stacks.md`) state the result; the rulings and their reasons are in the
plan's progress ledger. One line each, what changed and why:

- **Crates are read from `Cargo.toml` text, never from `cargo metadata`.** A line reader (headers, `key = "value"`, multi-line values, R6) keeps the ranker free of cargo, so it ranks on a machine with none; only the guard runs cargo.
- **The triage reach is crate-wide (R9).** A same-file reach under-marked cross-file I/O; the reach now follows `crate::`/`super::`/`self::`/`use crate::…` paths and re-exports transitively, as go's is package-wide, because an uncertain tier goes up.
- **The marker tables are extended (R10).** `rand::rng` under randomness; `chrono::Utc::now`/`Local::now` under clock; `std::env::args_os`/`vars_os`/`current_exe`/`set_current_dir` under environment, because mainstream I/O read Tier 1 under the original tables.
- **`std::io::stdin` is guard-only, not a marker.** The hook blocks a read of fd 0 at tier 1, the rule every stack shares, because the failure mode is a hang that yields no verdict.
- **Unreachable units are units, tiered 3 — not "never units".** A `pub(crate)` item, a `pub` item behind a private module and a binary's fns are ranked and reported with `not reachable from tests/ without modifying source (<why>)` or `binary-only: …`, never tested, so the seam list sees them. Eval check 47 changes with it: such units must be in `not_netted`, not absent.
- **The import is in the triage reason (R7).** `stack_rust.public_path` gives the shortest public path, and every reachable Tier 1/2 reason ends ``; import as `use <public_path>;` ``, so the agent writes an import that compiles rather than guessing one.
- **One allocator image is exempt (R11).** `SYSTEM_INTERNAL_IMAGES = ("libsystem_malloc.dylib",)`: macOS's allocator reads `mach_absolute_time` building a thread cache, and without the exemption every darwin test tripped `clock`.
- **libtest's test-body boundaries are judged (R13, R17a).** Reaching `test::__rust_begin_short_backtrace` or `test::assert_test_result` before any crate frame is judged `(test body, inlined)`, because at opt-level ≥ 1 the test body or its `Termination::report` inlines into libtest's generic and read as runner work.
- **Impl, `drop_in_place` and bare-segment frames are classified by crate (R14, R17b, R18).** `<T as Trait>::m` is decided by T's crate, `drop_in_place<T>` by the first non-transparent crate path in T, and a generic parameter or primitive is a bare segment naming no crate, so a crate's own `Drop`/`Display` impls are judged rather than passed over.
- **Thread identity and a 1024-frame cap (R15).** A walk that decides nothing is judged off the main thread and exempt on it (thread identity, not frame names), and a full 1024-frame buffer is judged, because a deep Drop chain fell off the 128-slot end and was exempt.
- **Intercepts are added (R16).** `readlink`, `rmdir`, `chmod`/`fchmodat`, `symlink`, `realpath` (filesystem), `getcwd` and `chdir` (environment, matching the `current_dir`/`set_current_dir` markers), and the `*64`/`*at` variants std calls, because the original table missed calls std bottoms out in.
- **Exit handling and status 125 (R19, R22).** The hook intercepts `exit`/`quick_exit`; one a crate frame makes is reported as an early exit and ends the process with status 125, read as NO TEST. GREEN also needs libtest's own `test <name> ... ok` line, a `harness = false` binary is NO TEST, and every `exec*` entry point trips as subprocess, so a test that ends the process after printing result lines cannot read GREEN by `exit`/`quick_exit` — `_exit`/`SYS_exit` are unhooked and remain a stated residual (residual 9's threat model).
- **The threat model is explicit (R22(e)).** The guard defends against accidental I/O and accidental verdict corruption; deliberate forgery by the code under test (`_exit`/`SYS_exit` after forged lines, patching the hook's statics) cannot be defended from inside the process, and is a stated residual, like go's residual 7.
- **The fat-LTO residual (R23).** Fat LTO in `[profile.dev]`/`[profile.test]` inlines the panic hook's `getenv` and libtest's exit into the harness `main`, so honest failures read 3 or 4. It fails closed (never a false GREEN) and is stated rather than fixed; the candidate fix, exempting rustc's generated harness `main`, goes to the final review.
- **The arm handshake is two-part (R1), and two more refusals exist (R20, R21).** `tsn-hook: armed`, plus `tsn-hook: cannot attribute` (exit 2) on an unresolvable walk or a missing Linux `.symtab`, because executable symbols are not in `.dynsym` and a by-name self-check could not work. Several packages with `tests/<stem>.rs` and no `-p`, a preload the loader skipped, or a handshake after `running N test` also exit 2.
- **The proof builds `--offline` (R24).** `cargo test --locked --offline --no-run`: the skill never fetches anything, matching go's `GOPROXY=off`, so a dependency missing from the local cargo cache exits 2 (NOT ARMED) with the remedy `cargo fetch`, never a download and never NO BUILD.
- **CI legs are "run", not "proven", until watched green (R25).** Until the `versions` legs have been seen passing, every document says the rust versions are run by CI, never proven or proved. Task 10 watched every leg pass (run 34747361975: the five amd64 linux legs and 1.82 on macos-latest), and restored "proven".
- **The rustix build plan reads `Cargo.lock`.** Any `rustix` in the lockfile selects the separate target dir and `--cfg=rustix_use_libc` on Linux, so the plan needs no `cargo metadata` call.
- **An unmangled main-thread callback is a stated residual, not a hook fix (R41).** A `#[no_mangle]`/`#[export_name]` fn reached only from a C or std frame on the main thread (an `atexit` handler, a signal handler, an `.init_array` entry) carries a plain C symbol; the walk finds no crate frame beneath it, and the main-thread branch of the thread-identity rule (R15b) reads that as pre-`main` init and exempts it, so its I/O reads GREEN. Measured on darwin 1.92 (`scratchpad/final-review/ax/`): a `#[no_mangle]` `atexit` handler wrote a file and exited 0; the same handler mangled exited 3. Landing a crate-frame heuristic change after all six toolchains are green risks honest-run breakage, so it ships as a documented residual with a named follow-up (treat an unmangled symbol inside the executable's own image as a crate frame, minus std's own C-ABI exports) rather than a hook change here.

## Amended after review, 2026-09-14

- **A crate's unmangled fns are crate frames (R42; closes residual 11, superseding R41's residual).** The wrapper lists the defined, global, unmangled TEXT symbols of the `*.rcgu.o` (rustc codegen) members of every rlib named in cargo's compiler-artifact messages (`rust_binary.rlib_exports`, a stdlib GNU/BSD `ar` and ELF/Mach-O object reader), writes the list outside the repo and names it in `TSN_EXPORTS`; the hook copies it into a fixed static buffer once, before arming, and judges a frame whose name is on it and which lies in the executable's own image as a crate frame. Why the rcgu members and not "the executable minus the sysroot": a build script's C (jemalloc, zstd, sqlite, ring) is bundled into rlibs as non-rcgu members, and counting it would trip honest runs whose std or runner frames call into it; the skill never writes `#[no_mangle]` into `tests/`, so the test crate's own objects are not needed. An rlib or codegen member the reader cannot parse (LLVM bitcode under `-C linker-plugin-lto`), a list past the hook's buffer, or a list the hook cannot open is exit 2, never a shorter list. Measured: the `ax` atexit probe exits 3 on darwin 1.92/1.98 and linux 1.82/1.94/1.98, and honest zstd, sqlite and zlib tests read the same before and after. The narrower residual that remains: an unmangled fn from a non-rcgu member or a non-cargo object is still unseen.
- **Residual-11 fix round 1 (R43–R46).** An `lto` setting (fat or thin) in `[profile.dev]`/`[profile.test]` makes rlibs bitcode, so such repos now read 2 (thin used to be proven); the refusal names the setting it found (root `Cargo.toml`, `CARGO_PROFILE_*_LTO`, or `-C linker-plugin-lto` in RUSTFLAGS) and the remedy `lto = false`, and residual 6 says so (R43). Every list-builder failure is exit 2, never a traceback read as RED: numeric `ar` fields must be ASCII digits, and any reader exception becomes the refusal (R44). Documented, not coded (R45): a crate exporting a libc-named symbol trips every honest run that reaches it (fails closed); at opt-level ≥ 1 a callback whose last act is a tail-called libc call has no frame and reads GREEN, as before the list. The Python list drops a `_R` name only when the v0 reader parses it, as the hook now does (R46), so `#[no_mangle] fn _Rfoo` is listed and judged.

The shipped residuals are eleven, not the eight above: `references/stacks.md` merges and extends them.
