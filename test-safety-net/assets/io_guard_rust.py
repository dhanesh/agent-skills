#!/usr/bin/env python3
r"""io_guard_rust.py -- the tier-aware runtime I/O guard for test-safety-net, Rust.

This is the SOLE enforcement of the skill's headline invariant, "never writes
a test that performs real I/O", on the Rust stack. `stack_rust.py`'s triage is
a FILTER: trait objects, generics and macros make reachability undecidable
from source, so the invariant is a RUNTIME property or nothing.

DOCUMENTED COMMAND -- copy the whole block. Run it from the crate root (add
`-p <package>` for a workspace member). The tier travels by environment
variable, the contract every guard reads, and nothing is written into the
target repo -- `Cargo.lock` included:

    TEST_SAFETY_NET_TIER=1 \
      python3 "$SKILL_DIR/assets/io_guard_rust.py" \
      --test <test_file_stem> <test_name>

    TEST_SAFETY_NET_TIER=2 TEST_SAFETY_NET_ALLOW=filesystem \
      python3 "$SKILL_DIR/assets/io_guard_rust.py" \
      --test <test_file_stem> <test_name>

`<test_file_stem>` names `tests/<test_file_stem>.rs`; `<test_name>` is one
`#[test]` fn in it. `assets/test_io_guard_rust.py` extracts those two commands
from this header and from every markdown file in the skill and runs them
verbatim, in both directions, so what is printed here is what is proved.

THE EXIT STATUS IS THE WHOLE PROTOCOL -- go's table, from guard_env.py
------------------------------------------------------------------------
    0  GREEN        the selected test ran and passed
    1  RED          the selected test ran and failed an assertion, or the
                    test binary died with no result line (signal, abort)
    2  NOT ARMED    the guard could not arm or refused to run; NOTHING was
                    proved
    3  GUARD TRIP   IOGuardViolation: the CLASSIFICATION is wrong --
                    reclassify the unit to Tier 3 and discard the test,
                    whether the run was red or green
    4  NO TEST      nothing was proved: the name matched no test, the test
                    is `#[ignore]`d, the test process exited before libtest
                    reported, the proof was killed at its timeout, the target
                    is `harness = false` (no libtest run to observe), or the
                    binary printed no verdict of its own
    5  NO BUILD     the test did not build; a compile error is not RED

libtest's own exit code lies twice (measured on 1.82 and 1.92): an ignored
test and a name matching nothing both exit 0, and a compile error exits 101
like a failed assertion. So the verdict is read from the build's JSON and
the `test result:` line, never from an exit code alone.

HOW A PROOF RUNS -- `main`
--------------------------
Every refusal prints its reason and exits 2:
  1. the tier and allow list, through `guard_env.read_env`;
  2. the platform is darwin or linux, and the `--test` target's crate name is
     not a transparent or runner crate's (ruling R30: `tests/test.rs` is
     crate `test`, read as libtest's own runner, so its I/O would go unjudged;
     the remedy is to rename the target). A workspace or path-dependency
     crate so named, seen in cargo's build messages, is refused the same way,
     and the remedy names that offender and its kind: a test file to rename,
     or a [lib] `name` to change in the Cargo.toml that declares it;
  3. `rustc -V`, run in the repo so a `rust-toolchain.toml` pin is honoured,
     is at least `MIN_RUST`. `RUSTUP_AUTO_INSTALL=0` on every rustc/cargo
     call: an uninstalled pin is refused, never downloaded;
  4. `Cargo.lock` exists at the workspace root. A plain `cargo test` creates
     or rewrites it, so the proof never runs without one, and builds with
     `--locked`: a stale lock is refused, left byte-identical (ruling R27:
     read in every proven cargo's wording, before the offline note);
  5. an environment-controlled test (one that calls `tsn_control_set_env`)
     is alone in its file -- the environment is process-wide and libtest
     runs one file's tests on parallel threads;
  6. the BUILD PLAN (`build_plan`), decided once: on linux, a `rustix` in
     `Cargo.lock` gets a separate out-of-repo target dir and
     `--cfg=rustix_use_libc`, because rustix's linux_raw backend issues
     syscalls without libc. Otherwise cargo's own target dir and no flags;
  7. `cargo test --locked --offline --no-run --message-format=json
     [-p <package>] --test <stem>` (`build_test`), with no timeout: waiting
     on cargo's lock is not the proof's time. `--offline` (ruling R24): the
     proof never downloads anything, as go's `GOPROXY=off` never does, so a
     dependency missing from the local cargo cache exits 2 with the remedy
     `cargo fetch`. A compile error exits 5;
  8. the executable is refused when static, musl or stripped
     (`rust_binary.refusal`): each makes a preloaded hook fail open. One
     that does not link libtest's harness (`harness = false`) is NO TEST:
     there is no libtest run to observe (ruling R22(c));
  9. the list of the crates' own unmangled fns (ruling R42, `export_list`):
     the defined global TEXT symbols, neither `_ZN` nor `_R` mangled, of the
     `*.rcgu.o` members of every rlib cargo's build messages name -- never a
     bundled C member, never the sysroot. An rlib or member it cannot read
     (LLVM bitcode under `-C linker-plugin-lto`) exits 2 with that remedy;
     an empty list is the common case. Then the hook, built by the repo's
     own rustc (`hook_library`), and the list written OUTSIDE the repo
     (`<cache>/rust-exports/`) for the hook to read, removed after the run;
 10. `<exe> <test_name> --exact --test-threads=1` under the preload, with
     stdin closed, core dumps off (an aborting test would otherwise write
     `<repo>/core` where `ulimit -c` allows it -- measured in the rust
     images) and a 300s timeout (`run_proof`). NEVER `--nocapture`, and
     a caller's `RUST_TEST_NOCAPTURE` is dropped: under it the default panic
     hook reads `RUST_BACKTRACE` outside libtest's frames, and every RED
     would trip `environment`;
 11. `classify`, first match wins: a violation line -> 3; the loader
     skipping the preload, no `tsn-hook: armed` line BEFORE libtest's
     `running N test`, or any other hook line but an early exit -> 2; an
     early exit (the hook saw the test call libc `exit`/`quick_exit`), exit
     status 125 (the status the hook then substitutes), a timeout, or no
     `running N test` line -> 4;
     then libtest's LAST `test result:` line, where GREEN also needs exit
     code 0 and libtest's own `test <name> ... ok` line before it.

RESIDUALS, STATED RATHER THAN IMPLIED
-------------------------------------
1. Anything bypassing libc is unseen: inline asm, raw syscalls (getrandom
   0.2's `SYS_getrandom` on linux among them). The filter declines asm and
   `libc::syscall` statically; rustix is rebuilt against libc (step 6).
2. Static, stripped and musl binaries are refused rather than guarded.
3. The seed-path and runner exemptions are names, pinned per toolchain; so
   are the transparent crates. A test target or path-source crate named like
   one is refused (ruling R30), but a crates.io dependency named `backtrace`
   or `hashbrown` is read as std's own.
4. CLOSED -- life-before-main crates (`ctor`): the preloaded hook arms
   before the executable's constructors run, and a constructor's I/O is
   judged by its own crate frame (ruling R33; real ctor 1.0.13 measured
   exit 3 on darwin 1.92/1.98 and linux 1.94 aarch64, pinned dependency-free
   by `TestLifeBeforeMain`).
5. THE THREAT MODEL. The guard defends against ACCIDENTAL I/O, and against
   ACCIDENTAL verdict corruption, by the code under test. Verdict lines
   share stdout with that code (go's residual 7 is the same), so DELIBERATE
   forgery by it still passes.
   CLOSED -- forged status and result lines followed by:
     * `exit` or `quick_exit`: the hook rewrites an exit crate code made to
       status 125, which reads 4 even with fd 2 closed (rulings R19, R22(a));
     * an abort or a signal: a pass needs exit status 0 (R19);
     * any `exec*`: execve, execv, execvp, execvpe, execl, execlp and
       fexecve (where the platform has them) trip as subprocess (R22(d));
     * silencing the hook by filling pthread keys: both re-entrancy gates
       compare a private sentinel's ADDRESS (R22(b));
   and a forged handshake (it lands after `running N test`: 2, R21), a
   `harness = false` target (it links no libtest harness, and a run with no
   `running N test` line reads 4: R22(c)).
   REMAINING, by design: forged lines followed by `libc::_exit`/`_Exit` or
   `syscall(SYS_exit)` -- never hooked, since the hook's own `_exit` must not
   recurse; code that recovers the sentinel's address or patches the hook's
   statics; a `harness = false` binary that embeds libtest's symbol names;
   and life-before-main code printing a handshake under a hook the loader
   skipped without a word.
6. `std::env::vars`/`vars_os` read `environ` directly, `std::env::args`
   reads what the runtime saved before main, `std::thread::sleep` is an
   unhooked `nanosleep`, and `std::fs::hard_link` an unhooked `linkat`: a
   unit doing only those passes tier 1. `NOT_INTERCEPTED` and
   `PARTIALLY_INTERCEPTED` below carry each, measured; the filter still
   marks them.
7. darwin and linux only; x86_64 macOS is unproven.
8. CLOSED -- an unmangled `#[no_mangle]`/`#[export_name]` fn reached only
   from a C or std frame on the main thread (an `atexit` handler, a signal
   handler, an `.init_array` entry) named no crate, and its I/O read GREEN
   (ruling R41; darwin 1.92, `scratchpad/final-review/ax/`: the `atexit`
   handler wrote a file and exited 0). The wrapper now lists the crates'
   own unmangled fns (step 9) and the hook judges a listed frame in the
   executable's image as a crate frame (ruling R42): the same handler exits
   3 on darwin 1.92/1.98 and linux 1.82/1.94/1.98, pinned by
   `TestUnmangledExports`. REMAINING: an unmangled fn from a non-rcgu member
   (a build script's bundled C) or from an object cargo did not build into
   an rlib (a `cc`-built `.a`, a `global_asm!` in tests/) is still unseen.

THE HOOK
--------
`io_guard_rust_hook.rs` is a cdylib preloaded under the compiled test binary
(`DYLD_INSERT_LIBRARIES` through an `__interpose` section on macOS,
`LD_PRELOAD` through `dlsym(RTLD_NEXT)` on Linux). It intercepts the libc
calls in `INTERCEPTS`, walks `backtrace()` to find which function made each
one, and on a violation writes

    IOGuardViolation: a tier <N> candidate reached <group> I/O via <call> from <symbol>

to fd 2 and calls `_exit(3)` -- the same line shape go's guard writes, so one
label regex serves both.

Its environment contract:
  * `TSN_BLOCKED` -- the comma-joined blocked groups. The hook arms ONLY when
    it is present, and reads it once, in its constructor, with the real
    `getenv`;
  * `TSN_TIER`    -- the tier, for the message;
  * `TSN_STDIN=1` -- set at tier 1: a read of fd 0 trips only then;
  * `TSN_EXPORTS` -- the path of the crates' unmangled-fn list, one
    `<name>\t<label>` line each (ruling R42). The constructor copies it into
    a fixed `EXPORTS_CAP` buffer once, before arming; a file that will not
    open or read, or does not fit, is `cannot attribute` (exit 2). Unset,
    there is no list.

Its handshake: when armed, the constructor writes `tsn-hook: armed` to fd 2.
It reports one more thing (ruling R19): libc `exit` or `quick_exit` reached
with a crate frame responsible -- the test ending the process before libtest
can print its verdict -- writes `tsn-hook: early exit (<symbol>)` and then
exits for real, with EARLY_EXIT_STATUS (125) in place of the test's code
(ruling R22(a)). Its re-entrancy gates -- this one and every I/O intercept's
-- mark "inside the hook" with the ADDRESS of a private static, so a test
that fills every pthread key cannot silence it (R22(b)). libtest's own exits (101 after a failure, the normal end after main
returns) have no crate frame and stay silent. `exit` has its own pseudo-
group, `process-exit`, which is never blocked: it reports, it never trips.
`_exit`/`_Exit` are not hooked: the hook's own `_exit(3)`/`_exit(2)` must
never recurse.
At every decision it refuses to guess (ruling R1): a frame walk of fewer than
3 frames, a walk that resolves no symbol at all, or -- on Linux -- no
`.symtab` in `/proc/self/exe` writes `tsn-hook: cannot attribute (<why>)`
and exits 2. That closes Spike A2's 1.82 fail-open, where a hook built
without `-C force-unwind-tables=yes` got nothing resolvable from
`backtrace()` and passed everything.

THE DECISION RULE
-----------------
Frames are read innermost first, the hook's own image skipped, over a 1024-
frame buffer (ruling R15a: a Drop chain or a recursion deeper than the
spike's 128 slots used to fall off the end and be exempt):
  * a frame naming a `CONTROL_HELPERS` item decides, with THAT helper's
    group (`tsn_control_temp_dir` reads TMPDIR on its way to the filesystem,
    and is the tier-2 filesystem control). "Naming" is legacy's scope in
    both manglings (ruling R28): the frame's own path, an inherent or `Drop`
    impl's self type by ITS OWN path (ruling R34: `<Result<&str,
    TsnControlEnv>>::map` is std's, not the control; ruling R37: no other
    trait impl, since v0 prints a blanket `impl<T> Tr for T` at the helper
    type), or the payload of CORE's drop glue (ruling R39: a crate's own fn
    merely named `drop_in_place`/`drop_glue` is not drop glue) -- never a
    generic argument of an ordinary std fn, so `std::fs::read::<
    TsnControlEnv>` is std's read, not the control;
  * std's own seeding frame (`std::sys::random::hashmap_random_keys`: a
    `SEED_MARKERS` name in the path of a `SEED_CRATE` symbol) is std seeding
    a HashMap: exempt. A crate fn, type or test that merely carries the name
    is not (ruling R28);
  * a CRATE frame decides with the call's group. "Crate" is the deciding
    crate of the symbol: a plain path's first segment, and for an impl frame
    (`<T as Trait>::m`, `<T>::m`) the crate of the SELF TYPE T (ruling R14),
    so a crate's own trait impl -- a `Drop`, a `Display`, a `Termination` --
    is judged, not read as transparent the way the raw `$LT$` prefix was.
    `std`/`core`/`alloc` self types stay transparent, and so does a generic
    parameter self type (`<T as Debug>`, `<*const T>`), which names no crate.
    `core::ptr::drop_in_place<T>` is decided by T's crate the same way
    (ruling R17b), so libtest dropping a crate's panic payload is judged; for
    a tuple T the first element in a non-transparent crate decides;
  * a `RUNNER_CRATES` frame (libtest) is the runner's own work: exempt.
    BUT a `TEST_BODY_BOUNDARY_MARKERS` frame IN THE `test` CRATE --
    `test::__rust_begin_short_backtrace` (R13) or `test::assert_test_result`
    (R17a) -- is where libtest calls back into the test's own code; reaching
    one before any crate frame means that code was inlined into libtest's
    generic, and the call is JUDGED as `(test body, inlined)`. std's own
    `__rust_begin_short_backtrace` (thread spawn, lang_start) does not count;
  * a symbol without Rust's `17h<16 hex>E` hash, or a transparent-crate
    frame, is passed over -- so a runtime's C++ `_ZN` symbols are never read
    as crate code -- UNLESS it is an unmangled name on the TSN_EXPORTS list
    and the frame lies in the executable's own image (ruling R42): that is
    a crate's `#[no_mangle]`/`#[export_name]` fn, a CRATE frame, and it
    decides with the call's group, named `<fn> (unmangled fn of crate <c>)`.
    A listed name in a shared library's frame is still passed over.
Each frame's symbol is read in ITS OWN mangling (ruling R26): legacy
`_ZN...17h<hash>E`, or v0 `_R...` -- rustc 1.98's default, in which std, core,
alloc and libtest ship precompiled. Every rule above holds for v0 names too:
the crate is the path's root crate, an impl frame's is its self type's (a
primitive or placeholder names none), drop glue (`core::ptr::drop_glue<T>`)
is decided by the first crate anywhere in T, and the boundary, seed, control
and `drop_slow` names match DECODED identifiers, in exactly the scope legacy
gives them (ruling R28). A v0 symbol that cannot be read -- malformed,
truncated, nested too deep -- is passed over like any transparent frame.
A walk that decides nothing is resolved by thread IDENTITY, not frame names
(ruling R15b): off the main thread -- `pthread_main_np()` on darwin,
`gettid() == getpid()` on Linux -- a stack with no crate frame is a spawned
thread or a thread-local destructor running std-only code, and it is JUDGED.
On the main thread such a stack is pre-`main` libc/dyld init and stays
exempt. A buffer that filled without deciding is JUDGED rather than guessed.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

try:
    import resource
except ImportError:          # not a darwin/linux Python; main refuses the platform
    resource = None

_ASSETS = os.path.dirname(os.path.abspath(__file__))
if _ASSETS not in sys.path:
    sys.path.insert(0, _ASSETS)

import guard_env                                                    # noqa: E402
import rust_binary                                                  # noqa: E402
import stack_rust                                                   # noqa: E402
from guard_env import (                                             # noqa: E402
    EXIT_GREEN, EXIT_RED, EXIT_NOT_ARMED, EXIT_TRIP, EXIT_NO_TEST, EXIT_NO_BUILD,
    OUTCOME as _OUTCOME,
)

# guard_env.OUTCOME[EXIT_NO_TEST] is worded for go ("-run matched no test, the
# package has no test files") -- go reads it verbatim and must keep reading it
# verbatim (ab-validate's byte-identity row), so it is not edited here. Rust's
# own NO TEST paths are named at classify()'s call sites, not `go test -run`,
# so this guard prints its own wording instead of the shared one.
_RUST_NO_TEST = ("NO TEST (exit 4): nothing was proved -- the test name matched no test in "
                 "that tests/ target, it is #[ignore]d, or the process exited early")

HOOK_SOURCE = os.path.join(_ASSETS, "io_guard_rust_hook.rs")

MIN_RUST = (1, 82)

# Ruling R22(a): the status the hook substitutes for an `exit`/`quick_exit` a
# crate frame made -- the test ending the process before libtest reports.
# Rendered into the hook; `classify` reads it as NO TEST, so the verdict
# survives a test that closed fd 2 before the early-exit line was written.
EARLY_EXIT_STATUS = 125

# ── The name tables: rendered into the hook, and the ONLY place they live ─

TRANSPARENT_CRATES = ("std", "core", "alloc", "panic_unwind", "backtrace", "hashbrown",
                      "std_detect")
RUNNER_CRATES = ("test",)
# std's HashMap seeding, per toolchain: `std::sys::pal::unix::rand` (1.82)
# and `std::sys::random::linux` (1.92) both name `hashmap_random_keys`.
# Ruling R28: a SEED marker counts only in the OWN path of a symbol whose
# crate is SEED_CRATE -- std's seeding function, never a crate fn, type or
# test that merely carries the name (in either mangling).
SEED_MARKERS = ("hashmap_random_keys",)
SEED_CRATE = "std"
# The BODY-SIDE boundaries libtest reaches the test through, matched ONLY in
# frames of the `test` crate (std's own `std::sys::backtrace::
# __rust_begin_short_backtrace` on the thread-spawn path and lang_start does
# not count). Reaching one before any crate frame means crate code inlined
# into libtest's generic made the call, and it is judged `(test body,
# inlined)`:
#   * `test::__rust_begin_short_backtrace` calls the test body (ruling R13:
#     at opt-level >=1 the body inlines up into call_once beneath it);
#   * `test::assert_test_result<T>` calls the test's `Termination::report`
#     (ruling R17a: an `#[inline(always)]` report is monomorphized into it).
# libtest's own bookkeeping never runs under either frame.
TEST_BODY_BOUNDARY_MARKERS = ("__rust_begin_short_backtrace", "assert_test_result")
# The `tsn_control_*` helpers SKILL.md prints, and the group each stands for.
# `TsnControlEnv` is BELT-AND-BRACES, measured (ruling R12): its drop restores
# the variable with setenv/unsetenv, which are `environment` already, and the
# restore only runs when environment is allowed -- the control needed that
# anyway. Removed, the outcome is identical with environment allowed (exit 0)
# and with it blocked (exit 3 at tsn_control_set_env's getenv). It stays so
# the table names every helper SKILL.md prints. `tsn_control_temp_dir` is the
# load-bearing one: it turns the TMPDIR read into `filesystem`.
CONTROL_HELPERS = {"tsn_control_temp_dir": "filesystem",
                   "tsn_control_set_env": "environment",
                   "TsnControlEnv": "environment"}
# Images whose OWN internal calls are infrastructure, not I/O a unit chose
# (ruling R11). An intercepted call whose nearest frame outside the hook lies
# in one of these (matched on the image's basename) is exempt. Measured:
# macOS 27's xzone allocator reads `mach_absolute_time` while building a
# thread's cache (`_xzm_thread_cache_create_and_malloc`), under std's thread
# plumbing and under any allocating crate function; without this every Rust
# test tripped `clock` on darwin. glibc's malloc calls no intercepted function
# through the PLT, so on linux the table is inert. Adding an image here needs
# a measured reason like that one: every entry is a hole by construction.
SYSTEM_INTERNAL_IMAGES = ("libsystem_malloc.dylib",)
# Ruling R42: the most bytes of crate-export list (`<name>\t<label>\n` per
# unmangled crate fn) the hook copies into its static buffer. A list past it
# is refused before the run, and the hook refuses one too (exit 2 either way).
EXPORTS_CAP = 1 << 20

# `chdir` and `getcwd` are ENVIRONMENT (ruling R16 as amended): they mirror
# the `set_current_dir` and `current_dir` markers, which stack_rust puts in
# environment (R10), so the filter and the guard agree on the group a unit
# must declare at tier 2. `readlink`/`rmdir`/`chmod`/`symlink`/`realpath` are
# the calls std's read_link / remove_dir / set_permissions / symlink /
# canonicalize bottom out in; `fchmodat` is where some std versions put
# set_permissions.
_FS_SHARED = ("open", "openat", "stat", "lstat", "fstatat", "access", "mkdir", "unlink",
              "rename", "opendir", "readlink", "rmdir", "chmod", "fchmodat", "symlink",
              "realpath")
_SUBPROCESS = ("posix_spawn", "posix_spawnp", "fork", "execve")
_ENV = ("getenv", "setenv", "unsetenv", "getcwd", "chdir")
_NET = ("socket", "connect", "bind", "getaddrinfo")

# platform -> group -> the libc names the hook intercepts for it. The spec's
# table; darwin adds Apple's two clocks, and linux adds glibc's LFS `*64`
# spellings (what Rust's std actually calls on linux-gnu: open64, openat64,
# stat64, lstat64, fstatat64) and statx. darwin has no `getrandom` in
# libSystem, so it cannot be interposed there. This table is rendered into
# the hook: each intercept looks its GROUP up there, so the hook source names
# only the functions, and a hooked name missing from the table exits 2.
#
# NOT intercepted, and why (Task 7 lists this in NOT_INTERCEPTED): std's
# `env::vars`/`vars_os` read the `environ` array directly, calling no libc
# function a hook could sit in, so no intercept can catch them. The filter
# still marks them Tier 2 (environment), so a vars-reading unit is a Tier 2
# candidate; at Tier 1 it is unseen by the guard, exactly like go's `os.Args`.
#
# `process-exit` is NOT an I/O group (ruling R19): `exit` and `quick_exit`
# are reported as an early exit and end the process with EARLY_EXIT_STATUS
# (R22(a)), never judged, and no TSN_BLOCKED ever names the group.
INTERCEPTS = {
    "darwin": {
        "filesystem": _FS_SHARED,
        "environment": _ENV,
        "network": _NET,
        "clock": ("clock_gettime", "gettimeofday", "mach_absolute_time",
                  "clock_gettime_nsec_np"),
        "randomness": ("getentropy", "arc4random_buf"),
        # Ruling R22(d): every exec entry point. libSystem has no execvpe or
        # fexecve.
        "subprocess": _SUBPROCESS + ("execv", "execvp", "execl", "execlp"),
        "stdin": ("read",),
        "process-exit": ("exit", "quick_exit"),
    },
    "linux": {
        "filesystem": _FS_SHARED + ("open64", "openat64", "stat64", "lstat64", "fstatat64",
                                    "statx"),
        "environment": _ENV,
        "network": _NET,
        "clock": ("clock_gettime", "gettimeofday"),
        "randomness": ("getrandom", "getentropy", "arc4random_buf"),
        # glibc's exec family calls its internal __execve, never the hooked
        # execve, so each entry point is hooked itself (ruling R22(d)).
        "subprocess": _SUBPROCESS + ("execv", "execvp", "execvpe", "execl", "execlp",
                                     "fexecve"),
        "stdin": ("read",),
        "process-exit": ("exit", "quick_exit"),
    },
}

_BEGIN = "// @@TSN-TABLES-BEGIN@@"
_END = "// @@TSN-TABLES-END@@"


class GuardCannotArm(Exception):
    """The hook cannot be built or loaded on this toolchain. Nothing may run."""


# ── Rendering ────────────────────────────────────────────────────────────

def _bytes_list(items):
    return ", ".join('b"%s"' % s for s in items)


_CFG_OS = {"darwin": "macos", "linux": "linux"}


def _pairs(pairs):
    return ", ".join('(b"%s", b"%s")' % (a, b) for a, b in pairs)


def render_tables() -> str:
    """The hook's table block, BEGIN and END markers included, from the constants above."""
    lines = [
        _BEGIN,
        "// Rendered by io_guard_rust.py render_tables(); edit the Python tables, not this block.",
        "static TRANSPARENT: &[&[u8]] = &[%s];" % _bytes_list(TRANSPARENT_CRATES),
        "static RUNNER: &[&[u8]] = &[%s];" % _bytes_list(RUNNER_CRATES),
        "static SEED: &[&[u8]] = &[%s];" % _bytes_list(SEED_MARKERS),
        'static SEED_CRATE: &[u8] = b"%s";' % SEED_CRATE,
        "static TEST_BODY_BOUNDARY: &[&[u8]] = &[%s];" % _bytes_list(TEST_BODY_BOUNDARY_MARKERS),
        "static CONTROL: &[(&[u8], &[u8])] = &[%s];" % _pairs(sorted(CONTROL_HELPERS.items())),
        "static SYSTEM_INTERNAL: &[&[u8]] = &[%s];" % _bytes_list(SYSTEM_INTERNAL_IMAGES),
        "static EARLY_EXIT_STATUS: c_int = %d;" % EARLY_EXIT_STATUS,
        "const EXPORTS_CAP: usize = %d;" % EXPORTS_CAP,
    ]
    for plat in sorted(INTERCEPTS):
        pairs = [(name, group) for group in sorted(INTERCEPTS[plat])
                 for name in INTERCEPTS[plat][group]]
        lines.append('#[cfg(target_os = "%s")]' % _CFG_OS[plat])
        lines.append("static INTERCEPT: &[(&[u8], &[u8])] = &[%s];" % _pairs(pairs))
    lines.append(_END)
    return "\n".join(lines) + "\n"


def render_hook() -> str:
    """The hook source with its table block replaced by `render_tables()`."""
    with open(HOOK_SOURCE, encoding="utf-8") as f:
        text = f.read()
    start = text.index(_BEGIN)
    end = text.index(_END) + len(_END)
    return text[:start] + render_tables().rstrip("\n") + text[end:]


# ── Building ─────────────────────────────────────────────────────────────

# The build line. `-C force-unwind-tables=yes` is load-bearing: without it,
# 1.82 on linux gives `backtrace()` nothing to walk (Spike A2), and the hook
# answers every decision with `cannot attribute`.
RUSTC_FLAGS = ("--edition", "2021", "--crate-type", "cdylib", "-C", "panic=abort",
               "-C", "force-unwind-tables=yes", "-O")


def _lib_name():
    return "libtsn_hook." + ("dylib" if sys.platform == "darwin" else "so")


def _cache_root(env):
    root = env.get("TEST_SAFETY_NET_CACHE")
    if root:
        return root
    home = env.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, ".cache", "test-safety-net")


def _first_error(text):
    for line in text.splitlines():
        if line.startswith("error"):
            return line.strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[0] if lines else "no output"


def hook_library(repo, env) -> str:
    """The path of the hook built by `repo`'s own `rustc`, building it once per key.

    The key is the sha256 of the rendered source, `rustc -vV` as run in
    `repo` (so a `rust-toolchain.toml` pin picks the toolchain), and the build
    flags. `RUSTUP_AUTO_INSTALL=0` stops rustup downloading a pinned
    toolchain that is not installed. The library lands in
    `<cache>/rust-hook/<key>/` by `os.replace` from a temporary name, so a
    concurrent run never loads half of one. Nothing is written into `repo`.
    Raises GuardCannotArm.
    """
    env = dict(env, RUSTUP_AUTO_INSTALL="0")
    rustc = shutil.which("rustc", path=env.get("PATH"))
    if not rustc:
        raise GuardCannotArm("no `rustc` on PATH")
    try:
        proc = subprocess.run([rustc, "-vV"], cwd=repo, env=env, capture_output=True,
                              text=True, timeout=120, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GuardCannotArm("`rustc -vV` could not run: %s" % exc) from exc
    if proc.returncode != 0:
        raise GuardCannotArm("`rustc -vV` failed in %s: %s"
                             % (repo, _first_error(proc.stderr + proc.stdout)))
    source = render_hook()
    key = hashlib.sha256("\0".join([source, proc.stdout, " ".join(RUSTC_FLAGS)])
                         .encode("utf-8")).hexdigest()
    dest_dir = os.path.join(_cache_root(env), "rust-hook", key)
    dest = os.path.join(dest_dir, _lib_name())
    if os.path.isfile(dest):
        return dest
    os.makedirs(os.path.dirname(dest_dir), exist_ok=True)
    work = tempfile.mkdtemp(prefix=".build-", dir=os.path.dirname(dest_dir))
    try:
        src = os.path.join(work, "tsn_hook.rs")
        with open(src, "w", encoding="utf-8") as f:
            f.write(source)
        out = os.path.join(work, _lib_name())
        try:
            build = subprocess.run([rustc, *RUSTC_FLAGS, "--crate-name", "tsn_hook", "-o", out,
                                    src], cwd=repo, env=env, capture_output=True, text=True,
                                   timeout=600, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            raise GuardCannotArm("the hook could not be built: %s" % exc) from exc
        if build.returncode != 0 or not os.path.isfile(out):
            raise GuardCannotArm("the hook did not build with this toolchain: %s"
                                 % _first_error(build.stderr + build.stdout))
        os.makedirs(dest_dir, exist_ok=True)
        os.replace(out, dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return dest


# ══ THE WRAPPER ═════════════════════════════════════════════════════════

# The groups are the FILTER's, verbatim -- one answer to "what counts as
# which I/O", owned by `stack_rust.py`. Rust controls two: filesystem
# (`tsn_control_temp_dir`) and environment (`tsn_control_set_env`).
GROUPS = stack_rust.GROUPS
CONTROLLABLE_GROUPS = tuple(sorted(stack_rust.CONTROLLABLE))
UNCONTROLLABLE_GROUPS = tuple(sorted(stack_rust.UNCONTROLLABLE))

SUPPORTED_PLATFORMS = ("darwin", "linux")
_PRELOAD = {"darwin": "DYLD_INSERT_LIBRARIES", "linux": "LD_PRELOAD"}
PROOF_ARGS = ("--exact", "--test-threads=1")    # never --nocapture: see the header, step 10
PROOF_TIMEOUT = 300
# Dropped from the proof's environment: another preload, stale hook
# configuration, and libtest's environment spelling of --nocapture.
_RUN_SCRUB = ("LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "RUST_TEST_NOCAPTURE")

_PREFIX = "test-safety-net io_guard_rust: "
# Ruling R27: the phrase every proven cargo's stale-lock refusal shares.
# cargo 1.92: "the lock file <path> needs to be updated but --locked was
# passed to prevent this"; 1.94 and 1.98: "cannot update the lock file <path>
# because --locked was passed to prevent this". All three add a help line
# naming `--offline`, so this is matched BEFORE ruling R24's offline match --
# and only on cargo's OWN `error:` lines (ruling R32), never on a build
# script's output, which cargo relays indented.
_LOCKED = "--locked was passed"
_STALE_LOCK = "Cargo.lock is out of date for this manifest; update it yourself, then re-run"


def _stale_lock(stderr):
    return any(line.startswith("error:") and _LOCKED in line for line in stderr.splitlines())


# Ruling R30: the classifier keys on CRATE NAMES, so a user crate named like
# a transparent or runner crate is indistinguishable from std or libtest:
# `tests/test.rs` compiles to crate `test`, whose frames read as the runner's
# own work and are exempt. Such a target cannot be proven until renamed.
_RESERVED_CRATES = frozenset(TRANSPARENT_CRATES) | frozenset(RUNNER_CRATES)


def _reserved_refusal(targets):
    """The NOT ARMED reason when a target in `targets` is named like a
    transparent or runner crate, or None. Each target is (name, kind, where):
    cargo's target name (a `-` becomes `_` in the crate name), its kind
    (`test`, or a lib-like kind), and the file that declares it, relative to
    the repo -- the test's source, or a lib's Cargo.toml. The remedy names
    THAT offender: a test file is renamed, a lib gets another [lib] name."""
    bad = sorted({t for t in targets if t[0] and t[0].replace("-", "_") in _RESERVED_CRATES})
    if not bad:
        return None
    fixes = []
    for name, kind, where in bad:
        if kind == "test":
            src = where or "tests/%s.rs" % name
            stem = os.path.splitext(os.path.basename(src))[0]
            fixes.append("crate `%s` is the integration test %s: rename that file (e.g. to "
                         "%s)" % (name.replace("-", "_"), src,
                                  os.path.join(os.path.dirname(src), stem + "_io.rs")))
        else:
            fixes.append("crate `%s` is the `%s` target declared in %s: give it another `name` "
                         "under [lib] there, or rename that package"
                         % (name.replace("-", "_"), kind, where or "its Cargo.toml"))
    return ("%s shares its name with one of Rust's own crates, which the guard reads BY NAME as "
            "std's or libtest's work, so its I/O would never be judged. Rename it -- %s; then "
            "re-run" % (", ".join("`%s`" % t[0].replace("-", "_") for t in bad), "; ".join(fixes)))


_PATH_SOURCE_DIR = re.compile(r"path\+file://([^#)]+)")


def _offender(target, msg, repo):
    """(name, kind, where) for `_reserved_refusal` from a compiler-artifact
    message: `where` is the test's source for a test target, else the
    declaring Cargo.toml -- cargo's `manifest_path`, or the package id's path."""
    kinds = [k for k in (target.get("kind") or ()) if k in _LINKED_KINDS]
    kind = "test" if "test" in kinds else (kinds[0] if kinds else "lib")
    where = target.get("src_path") if kind == "test" else msg.get("manifest_path")
    if not where and kind != "test":
        m = _PATH_SOURCE_DIR.search(str(msg.get("package_id") or ""))
        where = os.path.join(m.group(1), "Cargo.toml") if m else None
    if where and os.path.isabs(where):
        rel = os.path.relpath(where, repo)
        where = where if rel.startswith("..") else rel
    return (str(target.get("name") or ""), kind, where)


_LINKED_KINDS = frozenset({"lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro", "test"})


def _path_source(package_id):
    """A cargo package id of a local (path) package: `path+file:///…#0.1.0`
    (cargo 1.77+), or `name 0.1.0 (path+file:///…)` before it."""
    return package_id.startswith("path+file:") or "(path+file:" in package_id


def _why(g):
    if g == "clock":
        return "std has no freeze hook"
    if g == "randomness":
        return "std has no RNG and rand::thread_rng cannot be seeded"
    return "no test can control it"


# ── The two layers: every filter marker, and what the hook does about it ─
#
# `stack_rust.py`'s marker tables are what the FILTER tiers on; these three
# maps are what this guard does about each marker. `test_io_guard_rust.py`
# asserts they PARTITION those tables -- every marker in exactly one, with a
# reason -- and that each FILTER_MARKER_INTERCEPTS row names calls the hook
# intercepts on BOTH platforms. Everything here was MEASURED under the hook
# (darwin 1.92, linux 1.82 and 1.92) unless the reason says otherwise.

_OPEN = ("open", "openat", "open64", "openat64")
_STAT = ("stat", "stat64", "statx")
_NET = ("socket", "connect", "bind", "getaddrinfo")
_SPAWN = ("posix_spawn", "posix_spawnp", "fork", "execve")

FILTER_MARKER_INTERCEPTS = {
    "File::open": _OPEN,
    "File::create": _OPEN,
    "OpenOptions": _OPEN,
    # tokio::fs / async_std::fs run std::fs on a blocking pool whose thread
    # carries the runtime's crate frames, so the call is judged there.
    "tokio::fs": _OPEN + _STAT + ("mkdir", "unlink", "rename", "opendir"),
    "async_std::fs": _OPEN + _STAT + ("mkdir", "unlink", "rename", "opendir"),
    "Path::canonicalize": ("realpath", "stat"),
    "Path::exists": _STAT,
    "Path::is_dir": _STAT,
    "Path::is_file": _STAT,
    "Path::metadata": _STAT,
    "Path::try_exists": _STAT,
    "Path::symlink_metadata": ("lstat", "lstat64", "statx"),
    "Path::read_dir": ("opendir",),
    "Path::read_link": ("readlink",),
    "std::env::var": ("getenv",),
    "std::env::var_os": ("getenv",),
    "std::env::temp_dir": ("getenv",),          # TMPDIR
    "std::env::home_dir": ("getenv",),          # HOME, before any getpwuid_r fallback
    "std::env::set_var": ("setenv",),
    "std::env::remove_var": ("unsetenv",),
    "std::env::current_dir": ("getcwd",),
    "std::env::set_current_dir": ("chdir",),    # environment, per ruling R16 as amended
    "std::net": _NET,
    "tokio::net": _NET,
    "reqwest": _NET,
    "hyper": _NET,                              # through the transport it is handed
    "ureq": _NET,
    "std::process::Command": _SPAWN,
    "tokio::process": _SPAWN,                   # std::process::Command beneath
    "SystemTime::now": ("clock_gettime", "gettimeofday"),
    "Instant::now": ("clock_gettime", "mach_absolute_time", "clock_gettime_nsec_np"),
    "tokio::time": ("clock_gettime", "mach_absolute_time", "clock_gettime_nsec_np"),
    "chrono::Utc::now": ("clock_gettime", "gettimeofday"),     # SystemTime::now beneath
    "chrono::Local::now": ("clock_gettime", "gettimeofday"),
}

_GETRANDOM = ("through the getrandom crate: on darwin it calls getentropy, intercepted; on "
              "linux getrandom 0.3 (rand 0.9) resolves libc's getrandom by dlsym, which the "
              "preload answers, but getrandom 0.2 (rand 0.8) issues the raw SYS_getrandom "
              "syscall, which no libc hook sees -- its /dev/urandom fallback is an open, "
              "caught as filesystem. Not measured with the crates (no network in the proof)")
_DATABASE = ("caught only through the socket or file the driver opens, so it trips as network "
             "or filesystem, never as database; an in-memory SQLite opens neither")

PARTIALLY_INTERCEPTED = {
    "std::fs": "every std::fs call measured reaches an intercept (open/openat/open64, the stat "
               "family and statx, mkdir, unlink, rename, opendir, readlink, rmdir, chmod, "
               "symlink, realpath) EXCEPT fs::hard_link, which is linkat, not intercepted: a "
               "unit that only hard-links passes tier 1",
    "std::env::current_exe": "linux reads /proc/self/exe with readlink, intercepted (and "
                             "reported as filesystem, not environment); darwin asks "
                             "_NSGetExecutablePath, which is not intercepted",
    "rand::thread_rng": _GETRANDOM,
    "rand::random": _GETRANDOM,
    "rand::rng": _GETRANDOM,
    "OsRng": _GETRANDOM,
    "getrandom": _GETRANDOM,
    "sqlx": _DATABASE,
    "diesel": _DATABASE,
    "rusqlite": _DATABASE,
    "postgres": _DATABASE,
}

_ENVIRON = ("reads the `environ` array directly, calling no libc function a hook could sit "
            "in; the filter still marks it Tier 2, like go's os.Args")
_ARGV = ("reads the argc/argv the runtime saved before main; no call a hook could sit in, "
         "like go's os.Args")

NOT_INTERCEPTED = {
    "std::env::vars": _ENVIRON,
    "std::env::vars_os": _ENVIRON,
    "std::env::args": _ARGV,
    "std::env::args_os": _ARGV,
    "std::thread::sleep": "sleeps with nanosleep without reading a clock, and nanosleep is not "
                          "intercepted -- go's time.Sleep residual; a unit that only sleeps "
                          "passes tier 1",
}


# ── Preconditions ────────────────────────────────────────────────────────

def _platform(name=None):
    name = sys.platform if name is None else name
    if name == "darwin":
        return "darwin"
    if name.startswith("linux"):
        return "linux"
    return None


def _say(msg):
    sys.stderr.write(_PREFIX + msg + "\n")
    sys.stderr.flush()


def _not_armed(why):
    _say("NOT ARMED (exit 2): %s. Nothing was proved; fix it and re-run." % why)
    return EXIT_NOT_ARMED


def _rust_env(env):
    return dict(env, RUSTUP_AUTO_INSTALL="0")


def toolchain_version(repo, env=None):
    """`(major, minor)` of `rustc -V` run in `repo`, honouring its pin. Raises GuardCannotArm.

    A pinned toolchain that is not installed is its own reason: the proof
    never installs one (`RUSTUP_AUTO_INSTALL=0`)."""
    env = _rust_env(os.environ if env is None else env)
    rustc = shutil.which("rustc", path=env.get("PATH"))
    if not rustc:
        raise GuardCannotArm("no `rustc` on PATH")
    try:
        proc = subprocess.run([rustc, "-V"], cwd=repo, env=env, capture_output=True, text=True,
                              timeout=120, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GuardCannotArm("`rustc -V` could not run: %s" % exc) from exc
    text = proc.stderr + proc.stdout
    if proc.returncode != 0:
        if "is not installed" in text:
            raise GuardCannotArm("the toolchain this repo pins is not installed, and the proof "
                                 "never installs one: %s" % _first_error(text))
        raise GuardCannotArm("`rustc -V` failed in %s: %s" % (repo, _first_error(text)))
    m = re.match(r"rustc (\d+)\.(\d+)", proc.stdout.strip())
    if not m:
        raise GuardCannotArm("unrecognised `rustc -V` output: %r" % proc.stdout.strip())
    return int(m.group(1)), int(m.group(2))


def _metadata(repo, env):
    """`cargo metadata --no-deps` (it never writes Cargo.lock, measured), or None."""
    cargo = shutil.which("cargo", path=env.get("PATH"))
    if not cargo:
        return None
    try:
        proc = subprocess.run([cargo, "metadata", "--no-deps", "--offline", "--format-version",
                               "1"], cwd=repo, env=_rust_env(env), capture_output=True, text=True,
                              timeout=300, stdin=subprocess.DEVNULL)
        return json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _test_sources(meta, repo, stem, package):
    """The source files of test target `stem` that this run can build: from
    cargo's metadata when it has them (a `-p` run from the workspace root, a
    `[[test]] path`), else `tests/<stem>.rs` under `repo`. More than one, with
    no `-p`, is a workspace root whose members share the stem (ruling R20)."""
    if meta:
        here = os.path.realpath(repo)
        pkgs = meta.get("packages") or []
        if package:
            pkgs = [p for p in pkgs if p.get("name") == package]
        else:
            own = [p for p in pkgs
                   if os.path.dirname(os.path.realpath(p.get("manifest_path", ""))) == here]
            pkgs = own or pkgs
        found = [t["src_path"] for p in pkgs for t in (p.get("targets") or [])
                 if "test" in (t.get("kind") or ()) and t.get("name") == stem
                 and t.get("src_path")]
        if found:
            return found
    path = os.path.join(repo, "tests", stem + ".rs")
    return [path] if os.path.isfile(path) else []


# libtest's own code: its entry point and its console runner. The bytes are
# the same in both manglings -- legacy `_ZN4test16test_main_static17h…E`, v0
# `_RNvCs…_4test16test_main_static`. A `harness = false` target links neither
# (ruling R22(c)).
_LIBTEST_SYMBOLS = (b"4test16test_main_static", b"4test7console")


def _links_libtest(exe):
    """Does the test executable carry libtest's harness? Its verdict lines are
    only libtest's when it does; a `harness = false` target prints its own."""
    try:
        with open(exe, "rb") as f:
            data = f.read()
    except OSError:
        return False
    return any(s in data for s in _LIBTEST_SYMBOLS)


_SET_ENV_CALL = re.compile(r"\btsn_control_set_env\s*\(")
_TEST_ATTR = re.compile(r"#\s*\[\s*test\s*\]")


def _env_test_not_alone(path):
    """True when `path` CALLS `tsn_control_set_env` (its definition is not a
    call; comments and strings are blanked) and holds more than one test."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            code = stack_rust.strip_noncode(f.read())
    except OSError:
        return False
    calls = [m for m in _SET_ENV_CALL.finditer(code)
             if not re.search(r"\bfn\s+$", code[:m.start()])]
    return bool(calls) and len(_TEST_ATTR.findall(code)) > 1


# ── The build plan and the build ─────────────────────────────────────────

BuildPlan = collections.namedtuple("BuildPlan", "target_dir cfg reason")
BuildPlan.__doc__ = """How the proof is built, decided once; build and run never branch on a mode.

    target_dir  CARGO_TARGET_DIR for the build, or None for cargo's own
    cfg         extra `--cfg` flags, passed as RUSTFLAGS, only with target_dir
    reason      why the plan is not the normal one ("" when it is)
"""

BuildResult = collections.namedtuple("BuildResult", "exe compile_error output rlibs",
                                     defaults=((),))
BuildResult.__doc__ = """One `cargo test --no-run`.

    exe            the test executable cargo reported, or None
    compile_error  the compiler's error text (or cargo's, with no executable), or None
    output         everything cargo said, for the reader
    rlibs          (crate, path) of every `.rlib` cargo's compiler-artifact
                   messages name -- the crate under test and its
                   dependencies, never the sysroot (ruling R42)
"""

_RUSTIX = re.compile(r'(?m)^name = "rustix"$')


def build_plan(repo, platform):
    """The build plan for `repo` on `platform` (a `sys.platform` value).

    On linux, a `rustix` in `Cargo.lock` gets a separate target dir outside
    the repo, keyed by the workspace, and `--cfg=rustix_use_libc`: rustix's
    linux_raw backend issues syscalls with no libc call for the hook to sit
    in, and the separate dir keeps the user's own build cache valid.
    Otherwise cargo's own target dir and no flags."""
    if _platform(platform) == "linux":
        try:
            with open(os.path.join(repo, "Cargo.lock"), encoding="utf-8",
                      errors="replace") as f:
                locked = f.read()
        except OSError:
            locked = ""
        if _RUSTIX.search(locked):
            key = hashlib.sha256(os.path.realpath(repo).encode("utf-8")).hexdigest()
            return BuildPlan(os.path.join(_cache_root(os.environ), "rust-target", key),
                             ("--cfg=rustix_use_libc",),
                             "rustix's linux_raw backend bypasses libc")
    return BuildPlan(None, (), "")


# Ruling R24: cargo's own words when `--offline` cannot resolve a dependency.
# Measured on 1.82 and 1.98 with an empty CARGO_HOME: `error: no matching
# package named `itoa` found`, then a note naming offline mode / `--offline`.
# Matched on cargo's stderr only, never on rendered compiler diagnostics.
_OFFLINE = re.compile(r"offline mode|--offline")
_NOT_CACHED = ("a dependency is not in the local cargo cache; run `cargo fetch` (or build the "
               "tests once), then re-run. The proof never downloads anything")


def build_test(repo, plan, test_target, package, env):
    """Build test target `test_target` with `cargo test --locked --offline --no-run`. Never
    times out.

    `--locked` is load-bearing: a plain build creates or rewrites Cargo.lock
    (measured), and the proof writes nothing into the repo. So is `--offline`
    (ruling R24): the proof never downloads anything, as go's `GOPROXY=off`
    never does, so a dependency missing from the local cargo cache is NOT
    ARMED. Raises GuardCannotArm when cargo is missing or cannot start, or
    cannot resolve a dependency offline."""
    env = _rust_env(env)
    cargo = shutil.which("cargo", path=env.get("PATH"))
    if not cargo:
        raise GuardCannotArm("no `cargo` on PATH")
    if plan.target_dir:
        env["CARGO_TARGET_DIR"] = plan.target_dir
    if plan.cfg:
        env["RUSTFLAGS"] = " ".join(plan.cfg)
        env.pop("CARGO_ENCODED_RUSTFLAGS", None)     # it would outrank RUSTFLAGS
    cmd = [cargo, "test", "--locked", "--offline", "--no-run", "--message-format=json"]
    if package:
        cmd += ["-p", package]
    cmd += ["--test", test_target]
    try:
        proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True,
                              errors="replace", stdin=subprocess.DEVNULL)
    except OSError as exc:
        raise GuardCannotArm("cargo could not run: %s" % exc) from exc
    exes, errors, local, rlibs = [], [], [], []
    for line in proc.stdout.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        if msg.get("reason") == "compiler-artifact":
            target = msg.get("target") or {}
            # Ruling R42: every rlib this build linked from, for the list of
            # the crates' own unmangled fns.
            for path in msg.get("filenames") or ():
                if isinstance(path, str) and path.endswith(".rlib") \
                        and (str(target.get("name") or ""), path) not in rlibs:
                    rlibs.append((str(target.get("name") or ""), path))
            # Ruling R35: only crates linked INTO the test binary can be
            # misread by name -- a lib-like target or the test itself, never a
            # bin, an example or a bench.
            if _path_source(str(msg.get("package_id") or "")) and \
                    set(target.get("kind") or ()) & _LINKED_KINDS:
                local.append(_offender(target, msg, repo))
            if "test" in ((msg.get("target") or {}).get("kind") or ()) and msg.get("executable"):
                if msg["executable"] not in exes:
                    exes.append(msg["executable"])
        elif msg.get("reason") == "compiler-message":
            message = msg.get("message") or {}
            if message.get("level") == "error":
                errors.append((message.get("rendered") or message.get("message") or "").rstrip())
    # Ruling R27: a stale lock is its own refusal, decided FIRST -- every
    # proven cargo's wording of it also names `--offline` in its help line,
    # which the R24 match below would otherwise read as "not cached".
    if proc.returncode != 0 and _stale_lock(proc.stderr):
        raise GuardCannotArm(_STALE_LOCK)
    # Ruling R24: cargo failed before compiling anything, and its stderr names
    # offline mode -- a dependency the local cache does not hold. NOT ARMED,
    # never NO BUILD: the test source was never read.
    if proc.returncode != 0 and not exes and not errors and _OFFLINE.search(proc.stderr):
        raise GuardCannotArm("%s (cargo: %s)" % (_NOT_CACHED, _first_error(proc.stderr)))
    # Ruling R30: a workspace member or path dependency named like a
    # transparent or runner crate would be read as std's or libtest's work.
    why = _reserved_refusal(local)
    if why:
        raise GuardCannotArm(why)
    if len(exes) > 1:
        # Ruling R20: a workspace root with no -p builds EVERY member's
        # `tests/<stem>.rs`; keeping one would run a file nobody inspected.
        raise GuardCannotArm("more than one package has tests/%s.rs; name the package with -p"
                             % test_target)
    exe = exes[0] if exes else None
    output = "\n".join(errors + [proc.stderr.rstrip()]).strip()
    compile_error = None
    if errors:
        compile_error = "\n".join(errors)
    elif proc.returncode != 0 and not exe:
        compile_error = _first_error(proc.stderr)
    return BuildResult(None if compile_error else exe, compile_error, output, tuple(rlibs))


# ── The crate-export list (ruling R42) ───────────────────────────────────

_EXPORTS_UNREADABLE = ("the list of the crates' own unmangled fns could not be built (%s). The "
                       "hook reads a `#[no_mangle]`/`#[export_name]` fn as a crate frame only "
                       "from that list, and each cargo-built rlib's codegen objects must be "
                       "machine code for it: build without `-C linker-plugin-lto` (or any flag "
                       "that makes rustc emit LLVM bitcode), then re-run")


def export_list(rlibs):
    """[(name, crate)] of every unmangled fn the rcgu members of `rlibs`
    ((crate, path) pairs, from `BuildResult.rlibs`) define, sorted by name;
    a name defined twice keeps its first crate. Empty is the common case.
    Raises GuardCannotArm when an rlib cannot be read, or a name cannot be
    carried in the hook's `<name>\\t<label>\\n` lines."""
    found = {}
    for crate, path in rlibs:
        try:
            names = rust_binary.rlib_exports(path)
        except rust_binary.ExportListError as exc:
            raise GuardCannotArm(_EXPORTS_UNREADABLE % exc) from exc
        for name in names:
            if any(c in name for c in "\t\n\r"):
                raise GuardCannotArm(_EXPORTS_UNREADABLE
                                     % ("%s exports %r, a name the hook cannot carry"
                                        % (path, name)))
            found.setdefault(name, crate.replace("-", "_"))
    return sorted(found.items())


def export_text(entries):
    """The hook's list: one `<name>\\t<label>\\n` line per (name, crate). The
    label is what a trip names: the fn, and the crate it came from."""
    text = "".join("%s\t%s (unmangled fn of crate %s)\n" % (name, name, crate)
                   for name, crate in entries)
    if len(text.encode("utf-8")) > EXPORTS_CAP:
        raise GuardCannotArm("the list of the crates' own unmangled fns is %d bytes, more than "
                             "the hook's %d; nothing was run"
                             % (len(text.encode("utf-8")), EXPORTS_CAP))
    return text


def write_exports(text, env):
    """Write the list to a fresh file in `<cache>/rust-exports/`, never the
    repo, and return its path; the caller removes it after the run. Raises
    GuardCannotArm when it cannot be written."""
    where = os.path.join(_cache_root(env), "rust-exports")
    try:
        os.makedirs(where, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix="exports-", suffix=".txt", dir=where)
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
    except OSError as exc:
        raise GuardCannotArm("the crate-export list could not be written under %s: %s"
                             % (where, exc)) from exc
    return path


# ── The run and its verdict ──────────────────────────────────────────────

def _no_core_dump():
    """In the child, before exec: a crashing test must not dump `core` into
    the repo (its cwd). Measured in the rust images, whose `ulimit -c` is
    unlimited and core_pattern is `core`: an abort wrote `<repo>/core`."""
    if resource is not None:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run_proof(exe, test_name, tier, allow, hook, env, exports=None):
    """`(code, output)` of `exe` running `test_name` alone under the hook.

    The line is exactly `[exe, test_name, "--exact", "--test-threads=1"]`,
    stdin closed, no core file, combined output teed to stdout, killed after
    PROOF_TIMEOUT seconds. `exports` is the crate-export list's path, handed
    to the hook as TSN_EXPORTS (ruling R42)."""
    blocked = guard_env.blocked_groups(tier, allow, GROUPS, CONTROLLABLE_GROUPS,
                                       UNCONTROLLABLE_GROUPS)
    run_env = {k: v for k, v in env.items() if not k.startswith("TSN_") and k not in _RUN_SCRUB}
    run_env["TSN_BLOCKED"] = ",".join(sorted(blocked))
    run_env["TSN_TIER"] = str(tier)
    if tier == 1:
        run_env["TSN_STDIN"] = "1"
    if exports:
        run_env["TSN_EXPORTS"] = exports
    run_env[_PRELOAD[_platform() or "linux"]] = hook
    proc = subprocess.Popen([exe, test_name, *PROOF_ARGS], env=run_env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors="replace", preexec_fn=_no_core_dump)
    fired = []

    def _kill():
        fired.append(True)
        proc.kill()

    timer = threading.Timer(PROOF_TIMEOUT, _kill)
    timer.daemon = True
    timer.start()
    captured = []
    try:
        for line in proc.stdout:
            sys.stdout.write(line)
            captured.append(line)
        code = proc.wait()
    finally:
        timer.cancel()
    sys.stdout.flush()
    if fired:
        captured.append("\ntsn-proof: killed after %ds: nothing was proved\n" % PROOF_TIMEOUT)
    return code, "".join(captured)


_VIOLATION = re.compile(r"(?m)^IOGuardViolation: a tier (\S+) candidate reached (\S+) I/O via "
                        r"(\S+) from (.+)$")
_HOOK_LINE = re.compile(r"(?m)^tsn-hook: (.*)$")
_RESULT = re.compile(r"(?m)^test result: \S+\. (\d+) passed; (\d+) failed; (\d+) ignored")


_PRELOAD_SKIPPED = re.compile(r"(?m)^.*(?:cannot be preloaded|inserted dylib .* could not be "
                              r"loaded).*$")
_RUNNING_LINE = re.compile(r"(?m)^running \d+ tests?$")
_KILLED = re.compile(r"(?m)^tsn-proof: (killed after \d+s: nothing was proved)$")
_EARLY = "early exit"


def _reported_ok(output, test_name, end):
    """Did libtest report `test <name> ... ok` before offset `end`? Its
    announcement `test <name> ... ` starts a line (`- should panic`
    allowed), and an `ok` ends a line after it -- the same line, or a later
    one when the test wrote raw stderr in between (measured: a test's own
    stderr lands between the two halves)."""
    name = re.escape(test_name) if test_name else r"\S+"
    starts = list(re.finditer(r"(?m)^test %s(?: - should panic)? \.\.\. " % name, output[:end]))
    if not starts:
        return False
    return re.search(r"(?m)(?:\.\.\. |^)ok$", output[starts[-1].start():end]) is not None


def _verdict(code, output, test_name=None):
    """`(exit status, note or None)` for one proof run; `classify` is its status."""
    if re.search(r"(?m)^IOGuardViolation", output):
        return EXIT_TRIP, None
    skipped = _PRELOAD_SKIPPED.search(output)
    if skipped:
        return EXIT_NOT_ARMED, "the loader did not load the hook: " + skipped.group(0).strip()
    said = list(_HOOK_LINE.finditer(output))
    other = [m for m in said if m.group(1) != "armed" and not m.group(1).startswith(_EARLY)]
    if other:
        return EXIT_NOT_ARMED, "the hook did not arm: " + other[0].group(0)
    armed = [m for m in said if m.group(1) == "armed"]
    if not armed:
        return EXIT_NOT_ARMED, ("no `tsn-hook: armed` handshake, so the hook never loaded into "
                                "the test binary")
    running = _RUNNING_LINE.search(output)
    if running and armed[0].start() > running.start():
        return EXIT_NOT_ARMED, ("the only `tsn-hook: armed` line came after libtest started: the "
                                "hook's constructor, which writes first, never ran")
    early = [m for m in said if m.group(1).startswith(_EARLY)]
    if early:
        sym = re.match(r"early exit \((.*)\)$", early[0].group(1))
        return EXIT_NO_TEST, ("the test process exited before libtest reported; nothing was "
                              "proved (exit from %s)" % demangle(sym.group(1) if sym else "?"))
    if code == EARLY_EXIT_STATUS:
        return EXIT_NO_TEST, ("the test process exited before libtest reported; nothing was "
                              "proved (exit status %d, the hook's early-exit status)"
                              % EARLY_EXIT_STATUS)
    killed = _KILLED.search(output)
    if killed:
        return EXIT_NO_TEST, killed.group(1)
    if not running:
        return EXIT_NO_TEST, ("no libtest run was observed (no `running N test` line); nothing "
                              "was proved")
    results = list(_RESULT.finditer(output))
    if results:
        last = results[-1]
        passed, failed, ignored = (int(n) for n in last.groups())
        if failed >= 1:
            return EXIT_RED, None
        if passed == 1 and ignored == 0:
            if code != 0:
                return EXIT_NO_TEST, ("libtest's result line says a pass but the process exited "
                                      "%s; nothing was proved" % code)
            if not _reported_ok(output, test_name, last.start()):
                return EXIT_NO_TEST, ("libtest's own `test %s ... ok` line is missing before its "
                                      "result line; nothing was proved" % (test_name or "<name>"))
            return EXIT_GREEN, None
        return EXIT_NO_TEST, None
    if code != 0:
        return EXIT_RED, "the test binary ended without a result line (signal or abort)"
    return EXIT_NO_TEST, None


def classify(code, output, test_name=None):
    """The exit status for one proof run, first match wins:

      1. a line starting `IOGuardViolation` -> 3;
      2. the loader skipping the preload (glibc's `cannot be preloaded`,
         dyld's `inserted dylib ... could not be loaded`), a hook line other
         than the handshake or an early exit (`cannot attribute`, `libc has
         no ...`), no `tsn-hook: armed` line, or one only AFTER libtest's
         `running N test` (ruling R21) -> 2;
      3. `tsn-hook: early exit` -- the test called libc `exit` or
         `quick_exit` (ruling R19) -- or exit status EARLY_EXIT_STATUS, the
         status the hook substitutes then, whether or not the line reached
         fd 2 (R22(a)), or the proof's own timeout marker, or no libtest
         `running N test` line at all (R22(c)) -> 4;
      4. libtest's LAST `test result:` line: a failure -> 1; exactly one
         passed and none ignored -> 0 only with exit code 0 AND libtest's own
         `test <test_name> ... ok` line before it, else 4; anything else -> 4;
      5. no result line: a non-zero code -> 1 (signal or abort), else 4.
    """
    return _verdict(code, output, test_name)[0]


_ESCAPES = {"SP": "@", "BP": "*", "RF": "&", "LT": "<", "GT": ">", "LP": "(", "RP": ")",
            "C": ","}


def _unescape(seg):
    if seg.startswith("_$"):
        seg = seg[1:]

    def one(m):
        code = m.group(1)
        if code in _ESCAPES:
            return _ESCAPES[code]
        if code.startswith("u"):
            try:
                return chr(int(code[1:], 16))
            except ValueError:
                pass
        return m.group(0)

    return re.sub(r"\$([A-Za-z0-9]+)\$", one, seg).replace("..", "::")


def demangle(sym):
    """A Rust symbol as a path: legacy (`_ZN...17h<16 hex>E`, hash dropped) or
    v0 (`_R...`, ruling R26: crate disambiguators and the instantiating crate
    dropped), Mach-O's extra `_` allowed either way; anything else unchanged."""
    if sym.startswith(("_R", "__R")):
        return rust_binary.v0_demangle(sym) or sym
    s = sym[1:] if sym.startswith("__ZN") else sym
    if not (s.startswith("_ZN") and s.endswith("E")):
        return sym
    body, segs, i = s[3:-1], [], 0
    while i < len(body):
        j = i
        while j < len(body) and body[j].isdigit():
            j += 1
        if j == i:
            return sym
        n = int(body[i:j])
        if n <= 0 or j + n > len(body):
            return sym
        segs.append(body[j:j + n])
        i = j + n
    if segs and re.fullmatch(r"h[0-9a-f]{16}", segs[-1]):
        segs.pop()
    if not segs:
        return sym
    return "::".join(_unescape(seg) for seg in segs)


# ── main ─────────────────────────────────────────────────────────────────

def _parse(argv):
    parser = argparse.ArgumentParser(
        prog="io_guard_rust.py",
        description="Run one Rust test under test-safety-net's I/O guard. Run it from the crate "
                    "root; the tier travels in TEST_SAFETY_NET_TIER.")
    parser.add_argument("-p", "--package", help="the workspace member that owns the test")
    parser.add_argument("--test", dest="test_target", required=True, metavar="TEST_FILE_STEM",
                        help="the test target: tests/<TEST_FILE_STEM>.rs")
    parser.add_argument("test_name", help="one #[test] fn in that file")
    return parser.parse_args(argv)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = _parse(argv)
    except SystemExit as exc:                        # usage error -> 2, --help -> 0
        return exc.code if isinstance(exc.code, int) else EXIT_NOT_ARMED
    if args.test_name.startswith("-"):
        return _not_armed("a test name cannot begin with '-': libtest would read %r as a flag "
                          "and run the whole binary" % args.test_name)
    tier, allow, notes = guard_env.read_env(os.environ, CONTROLLABLE_GROUPS,
                                            UNCONTROLLABLE_GROUPS, "rust", _why)
    for note in notes:
        _say("note: " + note)
    if _platform() is None:
        return _not_armed("sys.platform=%s; this guard is proved on %s only"
                          % (sys.platform, " and ".join(SUPPORTED_PLATFORMS)))
    why = _reserved_refusal([(args.test_target, "test", "tests/%s.rs" % args.test_target)])
    if why:
        return _not_armed(why)
    repo = os.getcwd()
    env = dict(os.environ)
    try:
        version = toolchain_version(repo, env)
    except GuardCannotArm as exc:
        return _not_armed(str(exc))
    if version < MIN_RUST:
        return _not_armed("rustc %d.%d is below this guard's floor, %d.%d: the hook is unproven "
                          "there. Pin %d.%d or newer" % (version + MIN_RUST + MIN_RUST))
    meta = _metadata(repo, env)
    root = (meta or {}).get("workspace_root") or repo
    if not os.path.isfile(os.path.join(root, "Cargo.lock")):
        return _not_armed("no Cargo.lock: run cargo generate-lockfile, then re-run; the proof "
                          "never writes one")
    sources = _test_sources(meta, repo, args.test_target, args.package)
    if not args.package and len(sources) > 1:
        return _not_armed("more than one package has tests/%s.rs; name the package with -p"
                          % args.test_target)
    for source in sources:
        if _env_test_not_alone(source):
            return _not_armed("an environment-controlled test must be alone in its file (%s): "
                              "the environment is process-wide and libtest runs a file's tests "
                              "on parallel threads" % source)
    plan = build_plan(root, sys.platform)
    if plan.reason:
        _say("build plan: %s; building in %s with %s"
             % (plan.reason, plan.target_dir, " ".join(plan.cfg)))
    try:
        built = build_test(repo, plan, args.test_target, args.package, env)
    except GuardCannotArm as exc:
        return _not_armed(str(exc))
    if built.compile_error or not built.exe:
        sys.stderr.write((built.compile_error or built.output or "cargo reported no test "
                          "executable") + "\n")
        _say(_OUTCOME[EXIT_NO_BUILD])
        return EXIT_NO_BUILD
    why = rust_binary.refusal(rust_binary.inspect(built.exe))
    if why:
        return _not_armed("the test binary %s cannot be guarded -- %s" % (built.exe, why))
    if not _links_libtest(built.exe):
        _say("note: %s does not link libtest's harness (a `harness = false` target): no libtest "
             "run can be observed, and its verdict lines are its own; nothing was proved"
             % built.exe)
        _say(_RUST_NO_TEST)
        return EXIT_NO_TEST
    try:
        exports = export_text(export_list(built.rlibs))
        hook = hook_library(repo, env)
        exports_path = write_exports(exports, env)
    except GuardCannotArm as exc:
        return _not_armed(str(exc))
    try:
        code, output = run_proof(built.exe, args.test_name, tier, allow, hook, env,
                                 exports=exports_path)
    finally:
        try:
            os.unlink(exports_path)
        except OSError:
            pass
    outcome, note = _verdict(code, output, args.test_name)
    if outcome == EXIT_NOT_ARMED:
        return _not_armed(note)
    if outcome == EXIT_TRIP:
        m = _VIOLATION.search(output)
        if m:
            _say("the unit reached %s I/O via %s from %s"
                 % (m.group(2), m.group(3), demangle(m.group(4))))
    if note:
        _say("note: " + note)
    _say(_RUST_NO_TEST if outcome == EXIT_NO_TEST else _OUTCOME[outcome])
    return outcome


if __name__ == "__main__":
    sys.exit(main())
