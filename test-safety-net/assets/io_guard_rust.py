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
Frames are read innermost first, the hook's own image skipped:
  * a frame naming a `CONTROL_HELPERS` item decides, with THAT helper's
    group (`tsn_control_temp_dir` reads TMPDIR on its way to the filesystem,
    and is the tier-2 filesystem control);
  * a `SEED_MARKERS` frame is std seeding a HashMap: exempt;
  * a frame in a `TRANSPARENT_CRATES` crate, a trait-impl frame (`<T as
    Trait>::f`) or a symbol without Rust's `17h<16 hex>E` hash is passed
    over -- so a runtime's C++ `_ZN` symbols are never read as crate code;
  * a `RUNNER_CRATES` frame (libtest) is the runner's own work: exempt;
  * any other Rust frame -- the repo's crate, the test crate or a
    third-party crate -- decides with the call's group;
  * a walk that ends with none of those is a thread with no crate frame. If
    it passed a `THREAD_START_MARKERS` frame it is a spawned thread running
    only std code (`thread::spawn(std::env::temp_dir)`) and it is JUDGED,
    attributed as `(a thread with no crate frame)`. Only a stack with no
    Rust thread start -- dyld or libSystem initialising before `main` --
    stays exempt.
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
# std's thread entry: `std::sys::..::Thread::new::thread_start` and
# `std::thread::Builder::spawn_unchecked_`'s closure.
THREAD_START_MARKERS = ("thread_start", "spawn_unchecked_")
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

_FS_SHARED = ("open", "openat", "stat", "lstat", "fstatat", "access", "mkdir", "unlink",
              "rename", "opendir")
_SUBPROCESS = ("posix_spawn", "posix_spawnp", "fork", "execve")
_ENV = ("getenv", "setenv", "unsetenv")
_NET = ("socket", "connect", "bind", "getaddrinfo")

# platform -> group -> the libc names the hook intercepts for it. The spec's
# table; darwin adds Apple's two clocks, and linux adds glibc's LFS `*64`
# spellings (what Rust's std actually calls on linux-gnu: open64, openat64,
# stat64, lstat64, fstatat64) and statx. darwin has no `getrandom` in
# libSystem, so it cannot be interposed there. This table is rendered into
# the hook: each intercept looks its GROUP up there, so the hook source names
# only the functions, and a hooked name missing from the table exits 2.
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
        "static THREAD_START: &[&[u8]] = &[%s];" % _bytes_list(THREAD_START_MARKERS),
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
