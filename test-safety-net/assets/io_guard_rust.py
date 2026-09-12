#!/usr/bin/env python3
"""io_guard_rust.py -- the tier-aware runtime I/O guard for test-safety-net, Rust.

This file holds the HOOK half: the name tables, their rendering into
`io_guard_rust_hook.rs`, and `hook_library`, which builds that source with the
analysed repo's own `rustc`. The wrapper that builds the test, runs it under
the hook and classifies the result is added beside it.

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
  * `TSN_STDIN=1` -- set at tier 1: a read of fd 0 trips only then.

Its handshake: when armed, the constructor writes `tsn-hook: armed` to fd 2.
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
    and is the tier-2 filesystem control);
  * a `SEED_MARKERS` frame is std seeding a HashMap: exempt;
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
    as crate code.
A walk that decides nothing is resolved by thread IDENTITY, not frame names
(ruling R15b): off the main thread -- `pthread_main_np()` on darwin,
`gettid() == getpid()` on Linux -- a stack with no crate frame is a spawned
thread or a thread-local destructor running std-only code, and it is JUDGED.
On the main thread such a stack is pre-`main` libc/dyld init and stays
exempt. A buffer that filled without deciding is JUDGED rather than guessed.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

_ASSETS = os.path.dirname(os.path.abspath(__file__))
HOOK_SOURCE = os.path.join(_ASSETS, "io_guard_rust_hook.rs")

MIN_RUST = (1, 82)

# ── The name tables: rendered into the hook, and the ONLY place they live ─

TRANSPARENT_CRATES = ("std", "core", "alloc", "panic_unwind", "backtrace", "hashbrown",
                      "std_detect")
RUNNER_CRATES = ("test",)
# std's HashMap seeding, per toolchain: `std::sys::pal::unix::rand` (1.82)
# and `std::sys::random::linux` (1.92) both name `hashmap_random_keys`.
SEED_MARKERS = ("hashmap_random_keys",)
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
INTERCEPTS = {
    "darwin": {
        "filesystem": _FS_SHARED,
        "environment": _ENV,
        "network": _NET,
        "clock": ("clock_gettime", "gettimeofday", "mach_absolute_time",
                  "clock_gettime_nsec_np"),
        "randomness": ("getentropy", "arc4random_buf"),
        "subprocess": _SUBPROCESS,
        "stdin": ("read",),
    },
    "linux": {
        "filesystem": _FS_SHARED + ("open64", "openat64", "stat64", "lstat64", "fstatat64",
                                    "statx"),
        "environment": _ENV,
        "network": _NET,
        "clock": ("clock_gettime", "gettimeofday"),
        "randomness": ("getrandom", "getentropy", "arc4random_buf"),
        "subprocess": _SUBPROCESS,
        "stdin": ("read",),
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
        "static TEST_BODY_BOUNDARY: &[&[u8]] = &[%s];" % _bytes_list(TEST_BODY_BOUNDARY_MARKERS),
        "static CONTROL: &[(&[u8], &[u8])] = &[%s];" % _pairs(sorted(CONTROL_HELPERS.items())),
        "static SYSTEM_INTERNAL: &[&[u8]] = &[%s];" % _bytes_list(SYSTEM_INTERNAL_IMAGES),
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
