#!/usr/bin/env python3
"""Hook-level tests for `io_guard_rust.py` and `io_guard_rust_hook.rs`.

Three layers, cheapest first:

* `TestTables` is pure Python: the hook's committed table block is the one
  `render_tables()` produces (so the Python tables are the single source and
  the asset stays compilable), `INTERCEPTS` follows the spec's table per
  platform, and every intercept the hook source defines reports the group
  `INTERCEPTS` gives it.
* `TestHookLibrary` builds the hook with `rustc`: where it is cached, that a
  second call reuses it, that nothing lands in the repo, that a build
  failure is `GuardCannotArm` with the compiler's first error, and that
  every name in `INTERCEPTS` is really interposed by the built library.
* `TestProbe` builds a probe crate once, in a temp `CARGO_TARGET_DIR`, and
  runs its test binary directly under the preload -- one test per shape the
  decision rule has to get right.

Both build layers skip VISIBLY without `rustc`/`cargo`, so the run reads
`OK (skipped=N)` rather than failing on a machine without Rust.

Mutation coverage (each killed by the named test):
* R13, dropping the `__rust_begin_short_backtrace` boundary check ->
  `test_an_optimized_build_still_trips_the_body_and_passes_pure`;
* R17a, dropping `assert_test_result` from the body-side boundaries ->
  `test_a_termination_report_inlined_into_libtest_is_judged`;
* R17b, not deciding `drop_in_place<T>` by T's crate ->
  `test_a_panic_payload_dropped_by_libtest_is_judged`;
* R17b amended, not searching a container's full type / not treating
  `drop_slow` as a drop boundary ->
  `test_a_crate_drop_payload_in_a_std_container_is_judged`;
* R18, the old uppercase-generic heuristic ->
  `test_an_all_uppercase_crate_name_is_a_crate` and
  `test_a_blanket_impl_over_a_generic_names_no_crate`;
* fix-round-2 item 1, reading a generic parameter `T` as a crate ->
  `test_a_blanket_impl_over_a_generic_names_no_crate`;
* fix-round-2 item 2, matching std's `__rust_begin_short_backtrace` as a
  body boundary -> `test_a_thread_running_only_std_code_is_judged`;
* R14, reading an impl frame's `$LT$` prefix as transparent ->
  `test_a_crate_trait_impl_is_judged_by_its_self_type`;
* R15a, removing the frame cap's fail-closed judge ->
  `test_a_stack_deeper_than_the_cap_fails_closed`;
* R15b, the thread rule (now thread-identity) ->
  `test_a_thread_running_only_std_code_is_judged` and
  `test_a_thread_local_destructor_doing_io_is_judged`;
* R16, a missing filesystem/environment intercept -> `test_each_new_intercept_trips`;
* removing `tsn_control_temp_dir` from `CONTROL_HELPERS` ->
  `test_the_temp_dir_control_is_filesystem_alone` (ruling R12: removing
  `TsnControlEnv` instead is an equivalent mutation, measured, and kept as
  belt-and-braces);
* emptying `SYSTEM_INTERNAL_IMAGES` (ruling R11) -> on darwin
  `test_the_system_internal_table_is_load_bearing_on_darwin_and_inert_on_linux`;
* editing a table in Python without re-rendering ->
  `test_the_committed_table_block_is_the_rendered_one`;
* building without `-C force-unwind-tables=yes` on 1.82 (linux) -> the probe
  tests read exit 2 (`tsn-hook: cannot attribute`) instead of their verdicts.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


io_guard_rust = _load("io_guard_rust")
import guard_env                                                    # noqa: E402
import stack_rust                                                   # noqa: E402

HOOK_SRC = os.path.join(HERE, "io_guard_rust_hook.rs")
BEGIN, END = "// @@TSN-TABLES-BEGIN@@", "// @@TSN-TABLES-END@@"

RUSTC = shutil.which("rustc")
CARGO = shutil.which("cargo")
PLATFORM = "darwin" if sys.platform == "darwin" else ("linux" if sys.platform.startswith("linux")
                                                      else None)
PRELOAD = {"darwin": "DYLD_INSERT_LIBRARIES", "linux": "LD_PRELOAD"}.get(PLATFORM)

VIOLATION = re.compile(r"(?m)^IOGuardViolation: a tier (\d) candidate reached (\w+) I/O via "
                       r"(\S+) from (.+)$")


def _committed_block():
    with open(HOOK_SRC, encoding="utf-8") as f:
        text = f.read()
    start = text.index(BEGIN)
    end = text.index(END) + len(END)
    return text[start:end]


def _blocked(tier, allow=None):
    return ",".join(sorted(guard_env.blocked_groups(
        tier, allow, stack_rust.GROUPS, tuple(sorted(stack_rust.CONTROLLABLE)),
        tuple(sorted(stack_rust.UNCONTROLLABLE)))))


# ── Pure: the tables ─────────────────────────────────────────────────────

class TestTables(unittest.TestCase):
    def test_the_committed_table_block_is_the_rendered_one(self):
        # The Python tables are the single source; the asset carries their
        # rendering so it stays compilable on its own. A table edited in
        # Python and not re-rendered fails here.
        self.assertEqual(_committed_block(), io_guard_rust.render_tables().rstrip("\n"))

    def test_render_hook_substitutes_the_block_from_the_python_tables(self):
        with open(HOOK_SRC, encoding="utf-8") as f:
            self.assertEqual(io_guard_rust.render_hook(), f.read())
        with mock.patch.dict(io_guard_rust.CONTROL_HELPERS, {"tsn_control_probe": "clock"}):
            rendered = io_guard_rust.render_hook()
        self.assertIn('(b"tsn_control_probe", b"clock")', rendered)
        self.assertEqual(rendered.count(BEGIN), 1)
        self.assertEqual(rendered.count(END), 1)

    def test_the_block_carries_every_table(self):
        block = io_guard_rust.render_tables()
        for name in ("TRANSPARENT", "RUNNER", "SEED", "TEST_BODY_BOUNDARY", "CONTROL",
                     "SYSTEM_INTERNAL", "INTERCEPT"):
            self.assertRegex(block, r"static %s: " % name)
        for plat, cfg in (("darwin", "macos"), ("linux", "linux")):
            for group, names in io_guard_rust.INTERCEPTS[plat].items():
                for name in names:
                    self.assertIn('(b"%s", b"%s")' % (name, group), block)
            self.assertIn('#[cfg(target_os = "%s")]' % cfg, block)
        for crate in io_guard_rust.TRANSPARENT_CRATES:
            self.assertIn('b"%s"' % crate, block)

    def test_the_constants_are_the_briefs(self):
        self.assertEqual(io_guard_rust.MIN_RUST, (1, 82))
        self.assertEqual(io_guard_rust.TRANSPARENT_CRATES,
                         ("std", "core", "alloc", "panic_unwind", "backtrace", "hashbrown",
                          "std_detect"))
        self.assertEqual(io_guard_rust.RUNNER_CRATES, ("test",))
        self.assertEqual(io_guard_rust.SEED_MARKERS, ("hashmap_random_keys",))
        self.assertEqual(io_guard_rust.TEST_BODY_BOUNDARY_MARKERS,
                         ("__rust_begin_short_backtrace", "assert_test_result"))
        self.assertFalse(hasattr(io_guard_rust, "THREAD_START_MARKERS"),
                         "THREAD_START_MARKERS was removed for the thread-identity rule (R15b)")
        self.assertEqual(io_guard_rust.CONTROL_HELPERS,
                         {"tsn_control_temp_dir": "filesystem",
                          "tsn_control_set_env": "environment",
                          "TsnControlEnv": "environment"})
        self.assertEqual(io_guard_rust.SYSTEM_INTERNAL_IMAGES, ("libsystem_malloc.dylib",))

    def test_intercepts_follow_the_spec_table(self):
        shared = {
            "filesystem": {"open", "openat", "stat", "lstat", "fstatat", "access", "mkdir",
                           "unlink", "rename", "opendir", "readlink", "rmdir", "chmod",
                           "fchmodat", "symlink", "realpath"},
            "environment": {"getenv", "setenv", "unsetenv", "getcwd", "chdir"},
            "network": {"socket", "connect", "bind", "getaddrinfo"},
            "clock": {"clock_gettime", "gettimeofday"},
            "randomness": {"getentropy", "arc4random_buf"},
            "subprocess": {"posix_spawn", "posix_spawnp", "fork", "execve"},
            "stdin": {"read"},
        }
        self.assertEqual(set(io_guard_rust.INTERCEPTS), {"darwin", "linux"})
        for plat, table in io_guard_rust.INTERCEPTS.items():
            with self.subTest(platform=plat):
                self.assertEqual(set(table), set(shared))
                for group, names in shared.items():
                    self.assertLessEqual(names, set(table[group]), group)
        darwin, linux = io_guard_rust.INTERCEPTS["darwin"], io_guard_rust.INTERCEPTS["linux"]
        self.assertIn("mach_absolute_time", darwin["clock"])
        self.assertIn("clock_gettime_nsec_np", darwin["clock"])
        self.assertNotIn("mach_absolute_time", linux["clock"])
        self.assertIn("open64", linux["filesystem"])
        self.assertIn("statx", linux["filesystem"])
        self.assertNotIn("open64", darwin["filesystem"])
        self.assertNotIn("statx", darwin["filesystem"])
        self.assertIn("getrandom", linux["randomness"])

    def test_every_group_is_one_the_guard_knows(self):
        known = set(stack_rust.GROUPS) | {"stdin"}
        for plat, table in io_guard_rust.INTERCEPTS.items():
            self.assertLessEqual(set(table), known, plat)
        self.assertLessEqual(set(io_guard_rust.CONTROL_HELPERS.values()), set(stack_rust.GROUPS))

    def test_the_hooked_functions_are_exactly_the_table(self):
        # Each platform section defines a hook for exactly the names in
        # INTERCEPTS; the group each reports is looked up in the rendered
        # table, never written beside the hook, so it cannot drift.
        with open(HOOK_SRC, encoding="utf-8") as f:
            text = f.read()
        mac = text.index("// ---------------- macOS")
        lin = text.index("// ---------------- Linux")
        sections = {"darwin": text[mac:lin], "linux": text[lin:]}
        for plat, section in sections.items():
            with self.subTest(platform=plat):
                self.assertNotRegex(section, r'guard\(b"\w+", ')      # no group literals
                found = set(re.findall(r'guard\(b"(\w+)"\)', section))
                found |= set(re.findall(r'hook!\(\s*(\w+),', section))
                want = set()
                for names in io_guard_rust.INTERCEPTS[plat].values():
                    want |= set(names)
                self.assertEqual(found, want)
                every = [n for names in io_guard_rust.INTERCEPTS[plat].values() for n in names]
                self.assertEqual(len(every), len(set(every)), "a name in two groups")


# ── rustc: building and caching the hook ─────────────────────────────────

class TestHookLibrary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not RUSTC:
            raise unittest.SkipTest("no `rustc` on PATH: the hook build is NOT exercised on "
                                    "this machine.")
        if PLATFORM is None:
            raise unittest.SkipTest("the hook is built for darwin and linux only")
        cls.tmp = tempfile.mkdtemp(prefix="tsn-rust-hook-")
        cls.repo = os.path.join(cls.tmp, "repo")
        os.makedirs(cls.repo)
        cls.env = dict(os.environ, TEST_SAFETY_NET_CACHE=os.path.join(cls.tmp, "cache"))
        cls.lib = io_guard_rust.hook_library(cls.repo, cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_it_builds_into_the_cache_keyed_by_a_sha256(self):
        ext = "dylib" if PLATFORM == "darwin" else "so"
        root = os.path.join(self.tmp, "cache", "rust-hook")
        self.assertTrue(os.path.isfile(self.lib))
        self.assertEqual(os.path.basename(self.lib), "libtsn_hook." + ext)
        key = os.path.relpath(os.path.dirname(self.lib), root)
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        self.assertEqual(os.listdir(os.path.dirname(self.lib)), ["libtsn_hook." + ext])

    def test_a_second_call_reuses_the_cached_library(self):
        before = os.stat(self.lib).st_mtime_ns
        self.assertEqual(io_guard_rust.hook_library(self.repo, self.env), self.lib)
        self.assertEqual(os.stat(self.lib).st_mtime_ns, before)

    def test_the_default_cache_is_under_the_home_directory(self):
        env = {k: v for k, v in self.env.items() if k != "TEST_SAFETY_NET_CACHE"}
        # rustup finds its toolchains through HOME unless told otherwise.
        env.setdefault("RUSTUP_HOME", os.path.join(os.path.expanduser("~"), ".rustup"))
        env.setdefault("CARGO_HOME", os.path.join(os.path.expanduser("~"), ".cargo"))
        env["HOME"] = os.path.join(self.tmp, "home")
        lib = io_guard_rust.hook_library(self.repo, env)
        self.assertTrue(lib.startswith(os.path.join(self.tmp, "home", ".cache",
                                                    "test-safety-net", "rust-hook", "")))

    def test_nothing_is_written_into_the_repo(self):
        self.assertEqual(os.listdir(self.repo), [])

    def test_a_different_source_is_a_different_key(self):
        with mock.patch.object(io_guard_rust, "render_hook",
                               return_value=io_guard_rust.render_hook() + "\n// changed\n"):
            other = io_guard_rust.hook_library(self.repo, self.env)
        self.assertNotEqual(os.path.dirname(other), os.path.dirname(self.lib))

    def test_a_build_failure_is_guard_cannot_arm_with_the_first_error(self):
        env = dict(self.env, TEST_SAFETY_NET_CACHE=os.path.join(self.tmp, "cache-broken"))
        with mock.patch.object(io_guard_rust, "render_hook",
                               return_value="fn broken( {\n"):
            with self.assertRaises(io_guard_rust.GuardCannotArm) as ctx:
                io_guard_rust.hook_library(self.repo, env)
        self.assertRegex(str(ctx.exception), r"error")
        # A failed build leaves no library behind for a later run to find.
        for dirpath, _dirs, files in os.walk(os.path.join(self.tmp, "cache-broken")):
            self.assertFalse([f for f in files if f.startswith("libtsn_hook")], dirpath)

    def test_every_intercept_exists_in_the_built_library(self):
        want = set()
        for names in io_guard_rust.INTERCEPTS[PLATFORM].values():
            want |= set(names)
        if PLATFORM == "linux":
            proc = subprocess.run(["nm", "-D", "--defined-only", self.lib],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            have = {line.split()[-1] for line in proc.stdout.splitlines()
                    if len(line.split()) >= 3 and line.split()[1] in ("T", "W")}
        else:
            tool = shutil.which("dyld_info") or shutil.which("xcrun")
            if not tool:
                self.skipTest("no dyld_info: the __interpose entries cannot be listed")
            cmd = [tool, "-fixups", self.lib] if tool.endswith("dyld_info") \
                else [tool, "dyld_info", "-fixups", self.lib]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            have = {m.group(1) for m in re.finditer(r"__interpose\s+\S+\s+bind\s+\S+?/_(\w+)",
                                                    proc.stdout)}
        self.assertEqual(want - have, set(), "intercepts missing from the built library")


# ── cargo: the probe crate under the preload ─────────────────────────────

PROBE_TOML = ('[package]\nname = "probe"\nversion = "0.1.0"\nedition = "2021"\n\n'
              '[dependencies]\ndepx = { path = "depx" }\n')
DEPX_TOML = '[package]\nname = "depx"\nversion = "0.1.0"\nedition = "2021"\n'
DEPX_RS = ('pub fn read() -> usize {\n'
           '    std::fs::read("/etc/hosts").map(|b| b.len()).unwrap_or(0)\n'
           '}\n')
PROBE_LIB = r'''use std::collections::HashMap;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

pub fn pure(n: u32) -> u32 { n * 2 }
pub fn hashmap() -> usize { let mut m = HashMap::new(); m.insert(1, 2); m.len() }
pub fn env_var() -> Option<String> { std::env::var("TSN_PROBE_VAR").ok() }
pub fn read_file() -> usize { std::fs::read("/etc/hosts").map(|b| b.len()).unwrap_or(0) }
pub fn meta() -> bool { std::fs::metadata("/etc/hosts").is_ok() }
pub fn tcp() -> bool { std::net::TcpListener::bind("127.0.0.1:0").is_ok() }
pub fn sysnow() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}
pub fn instant() -> Instant { Instant::now() }
pub fn spawn() -> bool { std::process::Command::new("true").status().is_ok() }
pub fn alloc() -> usize {
    let v = vec![0u8; 1 << 20];
    let mut s = String::new();
    for i in 0..20000 { s.push_str(&i.to_string()); }
    v.len() + s.len()
}
pub fn stdin_line() -> String {
    let mut s = String::new();
    let _ = std::io::stdin().read_line(&mut s);
    s
}
'''
# The control helpers: Task 8's canonical text, verbatim (ruling R2).
CONTROL_HELPERS_RS = r'''fn tsn_control_temp_dir() -> std::path::PathBuf {
    use std::sync::atomic::{AtomicUsize, Ordering};
    static N: AtomicUsize = AtomicUsize::new(0);
    let dir = std::env::temp_dir().join(format!(
        "tsn-{}-{}", std::process::id(), N.fetch_add(1, Ordering::Relaxed)));
    std::fs::create_dir_all(&dir).expect("tsn_control_temp_dir");
    dir
}

struct TsnControlEnv { key: String, old: Option<std::ffi::OsString> }

impl Drop for TsnControlEnv {
    #[allow(unused_unsafe)]
    fn drop(&mut self) {
        match &self.old {
            Some(v) => unsafe { std::env::set_var(&self.key, v) },
            None => unsafe { std::env::remove_var(&self.key) },
        }
    }
}

#[allow(unused_unsafe)]
fn tsn_control_set_env(key: &str, value: &str) -> TsnControlEnv {
    let old = std::env::var_os(key);
    unsafe { std::env::set_var(key, value) };
    TsnControlEnv { key: key.to_string(), old }
}
'''
# `thread::spawn(std::env::vars)` does not compile (`Vars` is not `Send`), so
# the std-only thread runs `std::env::temp_dir`, which reads TMPDIR.
PROBE_TESTS = "use probe::*;\n\n" + CONTROL_HELPERS_RS + r'''
#[test] fn t_pure() { assert_eq!(pure(2), 4); }
#[test] fn t_hashmap() { assert_eq!(hashmap(), 1); }
#[test] fn t_alloc() { assert!(alloc() > 0); }
#[test] fn t_alloc_thread() { assert!(std::thread::spawn(alloc).join().unwrap() > 0); }
#[test] fn t_fail() { assert_eq!(pure(2), 5); }
#[test] fn t_env() { let _ = env_var(); }
#[test] fn t_fs() { let _ = read_file(); }
#[test] fn t_meta() { let _ = meta(); }
#[test] fn t_tcp() { let _ = tcp(); }
#[test] fn t_sysnow() { let _ = sysnow(); }
#[test] fn t_instant() { let _ = instant(); }
#[test] fn t_spawn() { let _ = spawn(); }
#[test] fn t_stdin() { let _ = stdin_line(); }
#[test] fn t_catch() { let r = std::panic::catch_unwind(|| read_file()); assert!(r.is_ok()); }
#[test] fn t_thread() { let _ = std::thread::spawn(|| std::fs::read("/etc/hosts")).join(); }
#[test] fn t_std_thread() { let _ = std::thread::spawn(std::env::temp_dir).join(); }
#[test] fn t_tmp_control() {
    let d = tsn_control_temp_dir();
    std::fs::write(d.join("x"), b"x").unwrap();
}
#[test] fn t_env_control() {
    let _g = tsn_control_set_env("TSN_PROBE_VAR", "v");
    assert_eq!(env_var().as_deref(), Some("v"));
}
#[test] fn t_dep_read() { assert!(depx::read() > 0); }

// Ruling R13: the I/O is INLINE in the test body (not via a lib function),
// so at opt-level >=1 the body inlines into call_once and the only named
// frame is libtest's `test::__rust_begin_short_backtrace`.
#[test] fn t_inline_read() {
    let n: usize = ["/etc/hosts"].iter().map(|p| std::fs::read(p).map(|b| b.len()).unwrap_or(0)).sum();
    assert!(n < usize::MAX);
}
#[test] fn t_inline_env() { let _ = std::env::var("TSN_PROBE_VAR"); }

// Ruling R14: an impl frame is judged by its self type's crate. A crate's
// own Display, Termination and Drop impls do I/O below a libtest callback
// or a drop_in_place, where the raw `$LT$` prefix used to read transparent.
// The I/O is a DIRECT std read inside the impl, so the deciding frame is the
// impl frame itself (`<Dsp as Display>::fmt`), not a shadowing lib-function
// crate frame -- that is what exercises R14's self-type rule.
struct Dsp;
impl std::fmt::Display for Dsp {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        let _ = std::fs::read("/etc/hosts");
        write!(f, "x")
    }
}
#[test] fn t_display() { let _ = format!("{}", Dsp); }
struct Rep;
impl std::process::Termination for Rep {
    fn report(self) -> std::process::ExitCode {
        let _ = std::fs::read("/etc/hosts");
        std::process::ExitCode::SUCCESS
    }
}
#[test] fn t_termination() -> Rep { Rep }

// Ruling R15a: deep stacks. A drop chain and a crate-trait recursion deeper
// than the spike's 128 slots used to fall off the buffer and be exempt.
struct Leaf;
// A DIRECT std read (not the lib `read_file`). At opt the leaf's Drop
// inlines into `drop_in_place<p::Leaf>`; R17b attributes that frame (and the
// chain's `drop_in_place<p::L>` frames) to crate `p`, so even the 2000-deep
// chain is judged at its innermost frame. The cap is exercised by
// `t_deep_blanket` instead.
impl Drop for Leaf { fn drop(&mut self) { let _ = std::fs::read("/etc/hosts"); } }
enum L { Node(Box<L>), End(Leaf) }
fn chain(n: usize) -> L { let mut l = L::End(Leaf); for _ in 0..n { l = L::Node(Box::new(l)); } l }
#[test] fn t_deep_drop() { let _l = chain(400); }
#[test] fn t_deep_drop_2000() { let _l = chain(2000); }
trait Walk { fn walk(&self, d: usize) -> usize; }
struct W;
impl Walk for W {
    #[inline(never)]
    fn walk(&self, d: usize) -> usize { if d == 0 { read_file() } else { 1 + self.walk(d - 1) } }
}
#[test] fn t_recurse_200() { assert!(W.walk(200) > 0); }

// Ruling R15b / Critical 3: a thread-local destructor doing I/O runs after
// std's thread_start returns (no marker frame), on a spawned thread and on
// the test thread.
struct Tl;
// A DIRECT std read: at opt the dtor inlines into glibc/libSystem's TLS
// destructor runner, leaving NO crate frame and NO test-body boundary above
// it (the dtor runs after the thread closure returned) -- so only the
// thread-identity rule (R15b) can judge it.
impl Drop for Tl { fn drop(&mut self) { let _ = std::fs::read("/etc/hosts"); } }
thread_local!(static TL: Tl = Tl);
#[test] fn t_tls_dtor() { std::thread::spawn(|| TL.with(|_| ())).join().unwrap(); }
#[test] fn t_tls_dtor_main() { TL.with(|_| ()); }

// Ruling R16: filesystem/environment calls with no prior intercept.
#[test] fn t_read_link() { let _ = std::fs::read_link("/etc"); }
#[test] fn t_remove_dir() { let _ = std::fs::remove_dir("/nonexistent-tsn-probe-dir"); }
#[test] fn t_set_perms() {
    use std::os::unix::fs::PermissionsExt;
    let _ = std::fs::set_permissions("/nonexistent-tsn-x", std::fs::Permissions::from_mode(0o644));
}
#[test] fn t_set_cwd() { let _ = std::env::set_current_dir("/"); }
#[test] fn t_cur_dir() { let _ = std::env::current_dir(); }
#[test] fn t_symlink() { let _ = std::os::unix::fs::symlink("/etc/hosts", "/nonexistent-tsn-dir/x"); }
#[test] fn t_canon() { let _ = std::fs::canonicalize("/etc/hosts"); }
// env::vars_os reads `environ` directly: no libc call, so it is NOT
// intercepted and a vars-reading unit passes even armed (documented).
#[test] fn t_env_vars() { assert!(std::env::vars_os().count() > 0); }

// Ruling R17a: an `#[inline(always)]` Termination::report is monomorphized
// into libtest's `test::assert_test_result<T>` (it inlines even at debug),
// so the only named frame above the read is libtest's.
struct IRep(u64);
impl std::process::Termination for IRep {
    #[inline(always)]
    fn report(self) -> std::process::ExitCode {
        let b = std::fs::read("/etc/hosts").unwrap_or_default();
        let mut h = self.0;
        for (i, x) in b.iter().enumerate() { h = h.wrapping_mul(31).wrapping_add(*x as u64 ^ i as u64); }
        if h == 42 { std::process::ExitCode::FAILURE } else { std::process::ExitCode::SUCCESS }
    }
}
#[test] fn t_term_inl1() -> IRep { IRep(1) }
#[test] fn t_term_inl2() -> IRep { IRep(2) }
#[test] fn t_term_inl3() -> IRep { IRep(3) }

// Ruling R17b: libtest drops a `#[should_panic]` test's payload; at opt the
// payload's Drop inlines into `core::ptr::drop_in_place<p::Pd>`, beneath a
// `test::run_test` closure.
struct Pd;
impl Drop for Pd { fn drop(&mut self) { let _ = std::fs::read("/etc/hosts"); } }
#[test] #[should_panic] fn t_payload_drop_sp() { std::panic::panic_any(Pd); }
// ... and a pure payload stays pure.
#[test] #[should_panic] fn t_should_panic_pure() { panic!("boom"); }

// Item 1 (fix round 2): a blanket impl over a generic `T` mangles
// `<T as p::Blanket>::b`. `T` names no crate: the frame is transparent and
// the call is judged at its caller, never attributed to a crate called "T".
trait Blanket { fn b(&self) -> usize; }
impl<T> Blanket for T {
    #[inline(never)]
    fn b(&self) -> usize { std::fs::read("/etc/hosts").map(|b| b.len()).unwrap_or(0) }
}
#[test] fn t_blanket() { assert!(7u8.b() < usize::MAX); }

// Ruling R17b amended: a crate Drop payload wrapped in a std container. At
// opt the payload crate is reached only inside the container's drop glue
// (`drop_in_place<alloc..vec..Vec<p::Pd>>`), or -- for Rc/Arc -- erased into
// a shared `drop_slow`. Every one must trip.
#[test] #[should_panic] fn t_pay_vec() { std::panic::panic_any(vec![Pd]); }
#[test] #[should_panic] fn t_pay_vec3() { std::panic::panic_any(vec![Pd, Pd, Pd]); }
#[test] #[should_panic] fn t_pay_box() { std::panic::panic_any(Box::new(Pd)); }
#[test] #[should_panic] fn t_pay_option() { std::panic::panic_any(Some(Pd)); }
#[test] #[should_panic] fn t_pay_array() { std::panic::panic_any([Pd]); }
#[test] #[should_panic] fn t_pay_arc() { std::panic::panic_any(std::sync::Arc::new(Pd)); }
#[test] #[should_panic] fn t_pay_mutex() { std::panic::panic_any(std::sync::Mutex::new(Pd)); }
#[test] #[should_panic] fn t_pay_tuple() { std::panic::panic_any((0u32, Pd)); }
// R18: a Box<dyn Trait> of a crate type is judged by the crate, not by "dyn".
trait Ob: Send {}
impl Ob for Pd {}
#[test] #[should_panic] fn t_pay_boxdyn() { let b: Box<dyn Ob> = Box::new(Pd); std::panic::panic_any(b); }
// Pure container payloads keep their natural verdict (drop frees memory only).
#[test] #[should_panic] fn t_pay_vec_pure() { std::panic::panic_any(vec![1u8]); }
#[test] #[should_panic] fn t_pay_box_pure() { std::panic::panic_any(Box::new(String::from("s"))); }
#[test] #[should_panic] fn t_pay_option_pure() { std::panic::panic_any(Some(0u32)); }

// Ruling R15a's cap: a 2000-deep recursion through a blanket impl over `T`.
// Every frame is `<T as p::Deep>::deep` -- a generic self type, which names
// no crate -- so the 1024-frame window holds no crate frame and only the cap
// can judge the read at the bottom. Run on the debug binary: at opt LLVM may
// turn the accumulator recursion into a loop.
trait Deep { fn deep(&self, n: usize) -> usize; }
impl<T> Deep for T {
    #[inline(never)]
    fn deep(&self, n: usize) -> usize {
        if n == 0 { std::fs::read("/etc/hosts").map(|b| b.len()).unwrap_or(0) } else { 1 + self.deep(n - 1) }
    }
}
#[test] fn t_deep_blanket() { assert!(0u8.deep(2000) > 0); }
'''


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# A SECOND test target whose crate name is all-uppercase (`tests/ABC.rs` ->
# crate `ABC`). Task 7's `tests/<stem>.rs` stem is free-form, so this is
# reachable; R18's structural rule must still see `ABC..P` as a real crate.
UPPER_TESTS = r'''#![allow(non_snake_case)]
struct P;
impl Drop for P { fn drop(&mut self) { let _ = std::fs::read("/etc/hosts"); } }
#[test] #[should_panic] fn u_payload_sp() { std::panic::panic_any(P); }
#[test] fn u_drop() { let _p = P; }
#[test] fn u_pure() {}
'''


def write_probe_crate(root):
    """Write the probe crate (and its path dependency) under `root`, return the crate dir."""
    crate = os.path.join(root, "probe")
    _write(os.path.join(crate, "Cargo.toml"), PROBE_TOML)
    _write(os.path.join(crate, "src", "lib.rs"), PROBE_LIB)
    _write(os.path.join(crate, "tests", "p.rs"), PROBE_TESTS)
    _write(os.path.join(crate, "tests", "ABC.rs"), UPPER_TESTS)
    _write(os.path.join(crate, "depx", "Cargo.toml"), DEPX_TOML)
    _write(os.path.join(crate, "depx", "src", "lib.rs"), DEPX_RS)
    return crate


def build_probe(root, target_dir, crate=None, opt=False, test="p"):
    """Build the probe's `test` target; return (crate, exe). `opt` builds the
    test profile at opt-level 1 -- the optimized-build hole (ruling R13)."""
    crate = crate or write_probe_crate(root)
    env = dict(os.environ, CARGO_TARGET_DIR=target_dir, RUSTUP_AUTO_INSTALL="0")
    if opt:
        env["CARGO_PROFILE_TEST_OPT_LEVEL"] = "1"
    proc = subprocess.run(["cargo", "test", "--offline", "--no-run", "--message-format=json",
                           "--test", test], cwd=crate, env=env, capture_output=True, text=True,
                          timeout=600)
    if proc.returncode != 0:
        raise AssertionError("cargo test --no-run failed:\n%s" % proc.stderr)
    for line in proc.stdout.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("reason") == "compiler-artifact" and msg.get("executable") \
                and (msg.get("target") or {}).get("name") == test:
            return crate, msg["executable"]
    raise AssertionError("cargo reported no executable for the probe's `%s` test" % test)


class ProbeCase(unittest.TestCase):
    """Builds the probe and the hook once per class; `run` executes one test."""

    @classmethod
    def setUpClass(cls):
        if not CARGO or not RUSTC:
            raise unittest.SkipTest("no `cargo`/`rustc` on PATH: the Rust hook is NOT "
                                    "exercised on this machine.")
        if PLATFORM is None:
            raise unittest.SkipTest("the hook is built for darwin and linux only")
        cls.tmp = tempfile.mkdtemp(prefix="tsn-rust-probe-")
        cls.crate = write_probe_crate(cls.tmp)
        _, cls.exe = build_probe(cls.tmp, os.path.join(cls.tmp, "target"), crate=cls.crate)
        # The SAME sources at opt-level 1, where the test body inlines away
        # (ruling R13). A separate target dir so the two builds never collide.
        _, cls.exe_opt = build_probe(cls.tmp, os.path.join(cls.tmp, "target-opt"),
                                     crate=cls.crate, opt=True)
        # The all-uppercase-crate target (`tests/ABC.rs` -> crate `ABC`),
        # optimized so the payload Drop inlines into libtest's drop path.
        _, cls.exe_upper = build_probe(cls.tmp, os.path.join(cls.tmp, "target-opt"),
                                       crate=cls.crate, opt=True, test="ABC")
        env = dict(os.environ, TEST_SAFETY_NET_CACHE=os.path.join(cls.tmp, "cache"))
        cls.hook = io_guard_rust.hook_library(cls.crate, env)
        cls.scratch = os.path.join(cls.tmp, "scratch")
        os.makedirs(cls.scratch)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_exe(self, args, blocked=None, tier=1, stdin=None, exe=None):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("TSN_") and k not in ("LD_PRELOAD", "DYLD_INSERT_LIBRARIES",
                                                         "RUST_BACKTRACE")}
        env.update({PRELOAD: self.hook, "TMPDIR": self.scratch})
        if blocked is not None:
            env["TSN_BLOCKED"] = blocked
            env["TSN_TIER"] = str(tier)
        if stdin if stdin is not None else tier == 1:
            env["TSN_STDIN"] = "1"
        proc = subprocess.run([exe or self.exe, *args], env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              timeout=300)
        return proc.returncode, proc.stdout

    def run_test(self, name, **kw):
        return self.run_exe([name, "--exact", "--test-threads=1"], **kw)

    def assert_trip(self, name, group, tier=1, **kw):
        kw.setdefault("blocked", _blocked(tier))
        code, out = self.run_test(name, tier=tier, **kw)
        self.assertEqual(code, 3, out)
        m = VIOLATION.search(out)
        self.assertIsNotNone(m, out)
        self.assertEqual((m.group(1), m.group(2)), (str(tier), group), out)
        return m

    def assert_pass(self, name, **kw):
        kw.setdefault("blocked", _blocked(kw.get("tier", 1)))
        code, out = self.run_test(name, **kw)
        self.assertEqual(code, 0, out)
        self.assertNotIn("IOGuardViolation", out)
        self.assertIn("tsn-hook: armed", out)
        return out


class TestProbe(ProbeCase):
    def test_a_pure_test_passes_and_the_hook_says_it_armed(self):
        out = self.assert_pass("t_pure")
        self.assertRegex(out, r"(?m)^tsn-hook: armed$")

    def test_the_hashmap_seed_is_exempt(self):
        self.assert_pass("t_hashmap")

    def test_an_assertion_failure_is_101_not_a_trip(self):
        # libtest's panic hook reads RUST_BACKTRACE beneath a `test::` frame:
        # runner work, exempt.
        code, out = self.run_test("t_fail", blocked=_blocked(1))
        self.assertEqual(code, 101, out)
        self.assertNotIn("IOGuardViolation", out)

    def test_each_io_test_trips_with_its_group(self):
        for name, group in (("t_env", "environment"), ("t_fs", "filesystem"),
                            ("t_meta", "filesystem"), ("t_tcp", "network"),
                            ("t_sysnow", "clock"), ("t_instant", "clock"),
                            ("t_spawn", "subprocess"), ("t_catch", "filesystem"),
                            ("t_thread", "filesystem"), ("t_std_thread", "environment")):
            with self.subTest(test=name):
                self.assert_trip(name, group)

    def test_each_new_intercept_trips(self):
        # Ruling R16: every filesystem/environment call that used to have no
        # intercept. `chdir` and `getcwd` are environment (R16 as amended),
        # mirroring the set_current_dir / current_dir markers.
        for name, group, call in (("t_read_link", "filesystem", "readlink"),
                                  ("t_remove_dir", "filesystem", "rmdir"),
                                  ("t_set_perms", "filesystem", None),
                                  ("t_set_cwd", "environment", "chdir"),
                                  ("t_cur_dir", "environment", "getcwd"),
                                  ("t_symlink", "filesystem", "symlink"),
                                  ("t_canon", "filesystem", None)):
            with self.subTest(test=name):
                m = self.assert_trip(name, group)
                if call is not None:
                    self.assertEqual(m.group(3), call, m.group(0))

    def test_env_vars_is_not_intercepted_and_passes_even_armed(self):
        # env::vars_os reads `environ` directly, calling no libc function a
        # hook can sit in (ruling R16). The filter still marks it Tier 2;
        # the guard cannot see it, like go's os.Args.
        self.assert_pass("t_env_vars")

    def test_a_crate_trait_impl_is_judged_by_its_self_type(self):
        # Ruling R14: Display and Termination impls on a crate type do I/O
        # below a `$LT$` frame the spike read as transparent. The attribution
        # must BE the impl frame: without R14 the display read is judged at
        # its caller and the report read at libtest's assert_test_result
        # boundary (R17a), both still exit 3 -- only the label tells.
        m = self.assert_trip("t_display", "filesystem")
        self.assertIn("p..Dsp", m.group(4), m.group(0))
        m = self.assert_trip("t_termination", "filesystem")
        self.assertIn("p..Rep", m.group(4), m.group(0))

    def test_a_thread_local_destructor_doing_io_is_judged(self):
        # Critical 3 / R15b: the dtor runs after std's thread_start returns,
        # with no marker frame. At debug the crate impl frame decides; at opt
        # the dtor inlines away, leaving a stack with no crate frame and no
        # test-body boundary -- caught only by the thread-identity rule, which
        # makes this the R15b killer.
        for exe in (self.exe, self.exe_opt):
            with self.subTest(exe=os.path.basename(os.path.dirname(exe))):
                for name in ("t_tls_dtor", "t_tls_dtor_main"):
                    code, out = self.run_exe([name, "--exact", "--test-threads=1"],
                                             blocked=_blocked(1), exe=exe)
                    self.assertEqual(code, 3, out)
                    self.assertRegex(out, r"(?m)^IOGuardViolation: a tier 1 candidate "
                                          r"reached filesystem I/O")

    def test_a_deep_drop_chain_and_a_deep_recursion_trip(self):
        # Ruling R15a: deeper than the spike's 128-frame buffer. The
        # 2000-deep chain at opt is attributed through its
        # `drop_in_place<p::L>` frames (R17b).
        self.assert_trip("t_deep_drop", "filesystem")
        self.assert_trip("t_recurse_200", "filesystem")
        code, out = self.run_exe(["t_deep_drop_2000", "--exact", "--test-threads=1"],
                                 blocked=_blocked(1), exe=self.exe_opt)
        self.assertEqual(code, 3, out)
        self.assertRegex(out, r"(?m)^IOGuardViolation: a tier 1 candidate reached filesystem")

    def test_a_stack_deeper_than_the_cap_fails_closed(self):
        # Ruling R15a: `t_deep_blanket` recurses 2000 frames through
        # `<T as p::Deep>::deep`, which names no crate, so the 1024-frame
        # buffer fills with transparent frames and the call is judged by the
        # cap, not exempted.
        code, out = self.run_exe(["t_deep_blanket", "--exact", "--test-threads=1"],
                                 blocked=_blocked(1))
        self.assertEqual(code, 3, out)
        m = VIOLATION.search(out)
        self.assertIsNotNone(m, out)
        self.assertEqual(m.group(4), "(a stack deeper than 1024 frames)", out)

    def test_an_optimized_build_still_trips_the_body_and_passes_pure(self):
        # Critical 1: at opt-level >=1 the test body inlines into call_once
        # under test::__rust_begin_short_backtrace (the `test` crate, which
        # classify reads as runner). R13 catches it at the boundary.
        for name, group in (("t_inline_read", "filesystem"), ("t_inline_env", "environment")):
            with self.subTest(test=name):
                code, out = self.run_exe([name, "--exact", "--test-threads=1"],
                                         blocked=_blocked(1), exe=self.exe_opt)
                self.assertEqual(code, 3, out)
                m = VIOLATION.search(out)
                self.assertEqual((m.group(1), m.group(2)), ("1", group), out)
                self.assertEqual(m.group(4), "(test body, inlined)", out)
        # a pure test, and libtest's own teardown, still pass under opt.
        code, out = self.run_exe(["t_pure", "--exact", "--test-threads=1"],
                                 blocked=_blocked(1), exe=self.exe_opt)
        self.assertEqual(code, 0, out)
        self.assertNotIn("IOGuardViolation", out)

    def test_a_termination_report_inlined_into_libtest_is_judged(self):
        # Ruling R17a: the read's only named frame is libtest's
        # `test::assert_test_result<IRep>`, a body-side boundary.
        for exe in (self.exe, self.exe_opt):
            for name in ("t_term_inl1", "t_term_inl2", "t_term_inl3"):
                with self.subTest(exe=os.path.basename(os.path.dirname(exe)), test=name):
                    code, out = self.run_exe([name, "--exact", "--test-threads=1"],
                                             blocked=_blocked(1), exe=exe)
                    self.assertEqual(code, 3, out)
                    m = VIOLATION.search(out)
                    self.assertIsNotNone(m, out)
                    self.assertEqual(m.group(2), "filesystem", out)

    def test_a_panic_payload_dropped_by_libtest_is_judged(self):
        # Ruling R17b: at opt the payload's Drop inlines into
        # `core::ptr::drop_in_place<p::Pd>`, decided by Pd's crate.
        for exe in (self.exe, self.exe_opt):
            with self.subTest(exe=os.path.basename(os.path.dirname(exe))):
                code, out = self.run_exe(["t_payload_drop_sp", "--exact", "--test-threads=1"],
                                         blocked=_blocked(1), exe=exe)
                self.assertEqual(code, 3, out)
                m = VIOLATION.search(out)
                self.assertIsNotNone(m, out)
                self.assertEqual(m.group(2), "filesystem", out)

    def test_should_panic_with_a_pure_payload_passes(self):
        for exe in (self.exe, self.exe_opt):
            with self.subTest(exe=os.path.basename(os.path.dirname(exe))):
                code, out = self.run_exe(["t_should_panic_pure", "--exact", "--test-threads=1"],
                                         blocked=_blocked(1), exe=exe)
                self.assertEqual(code, 0, out)
                self.assertNotIn("IOGuardViolation", out)

    def test_a_crate_drop_payload_in_a_std_container_is_judged(self):
        # Ruling R17b amended: the crate Drop is reached only inside the
        # container's drop glue (vec/box/option/array/mutex/tuple) or, for
        # Rc/Arc, a shared `drop_slow` with the payload crate erased. Every
        # one must trip in debug, opt1 and release.
        names = ("t_pay_vec", "t_pay_vec3", "t_pay_box", "t_pay_option", "t_pay_array",
                 "t_pay_arc", "t_pay_mutex", "t_pay_tuple", "t_pay_boxdyn")
        for exe in (self.exe, self.exe_opt):
            for name in names:
                with self.subTest(exe=os.path.basename(os.path.dirname(exe)), test=name):
                    code, out = self.run_exe([name, "--exact", "--test-threads=1"],
                                             blocked=_blocked(1), exe=exe)
                    self.assertEqual(code, 3, out)
                    m = VIOLATION.search(out)
                    self.assertIsNotNone(m, out)
                    self.assertEqual(m.group(2), "filesystem", out)

    def test_a_pure_container_payload_keeps_its_verdict(self):
        for exe in (self.exe, self.exe_opt):
            for name in ("t_pay_vec_pure", "t_pay_box_pure", "t_pay_option_pure"):
                with self.subTest(exe=os.path.basename(os.path.dirname(exe)), test=name):
                    code, out = self.run_exe([name, "--exact", "--test-threads=1"],
                                             blocked=_blocked(1), exe=exe)
                    self.assertEqual(code, 0, out)
                    self.assertNotIn("IOGuardViolation", out)

    def test_an_all_uppercase_crate_name_is_a_crate(self):
        # Ruling R18: the structural rule reads `ABC..P` as crate `ABC`, where
        # the old uppercase heuristic read `ABC` as a generic and failed open.
        for name, want in (("u_payload_sp", 3), ("u_drop", 3), ("u_pure", 0)):
            with self.subTest(test=name):
                code, out = self.run_exe([name, "--exact", "--test-threads=1"],
                                         blocked=_blocked(1), exe=self.exe_upper)
                self.assertEqual(code, want, out)
                if want == 3:
                    self.assertRegex(out, r"(?m)^IOGuardViolation: a tier 1 candidate "
                                          r"reached filesystem")

    def test_a_blanket_impl_over_a_generic_names_no_crate(self):
        # Item 1: `<T as p::Blanket>::b` must not be read as a crate named
        # "T"; the call is judged at its caller, the test function.
        m = self.assert_trip("t_blanket", "filesystem")
        self.assertTrue(m.group(4).startswith("_ZN1p9t_blanket"), m.group(0))

    def test_optimized_pure_suite_does_not_trip_on_teardown(self):
        # MEASURE (ruling R15b): libtest's test-thread teardown -- TLS
        # destructors and output-capture cleanup after a PURE test returns --
        # must never trip, with default threads or with one.
        for args in (["t_pure", "t_hashmap", "t_alloc"],
                     ["t_pure", "t_hashmap", "t_alloc", "--test-threads=1"]):
            for exe in (self.exe, self.exe_opt):
                with self.subTest(exe=os.path.basename(os.path.dirname(exe)), args=args):
                    code, out = self.run_exe(args, blocked=_blocked(1), exe=exe)
                    self.assertEqual(code, 0, out)
                    self.assertNotIn("IOGuardViolation", out)

    def test_the_attribution_names_the_probe_function(self):
        m = self.assert_trip("t_env", "environment")
        self.assertEqual(m.group(3), "getenv")
        self.assertTrue(m.group(4).startswith("_ZN5probe7env_var"), m.group(0))

    def test_a_thread_running_only_std_code_is_judged(self):
        # `thread::spawn(std::env::temp_dir)`: a std-only spawned thread with
        # no crate frame. Fail-closed by thread identity (R15b): off the main
        # thread, a no-crate-frame stack is judged, never exempt.
        # std's own `__rust_begin_short_backtrace` on the spawn path is NOT a
        # body boundary (item 2), so this is R15b's verdict and label, exactly.
        m = self.assert_trip("t_std_thread", "environment")
        self.assertEqual(m.group(4), "(a thread with no crate frame)")

    def test_a_read_in_a_spawned_closure_is_the_closures(self):
        m = self.assert_trip("t_thread", "filesystem")
        self.assertTrue(m.group(4).startswith("_ZN1p8t_thread"), m.group(0))

    def test_the_violation_starts_its_own_line(self):
        # libtest prints `test t_fs ... ` with no newline before the test runs.
        code, out = self.run_test("t_fs", blocked=_blocked(1))
        self.assertEqual(code, 3, out)
        self.assertRegex(out, r"(?m)^IOGuardViolation: ")

    def test_stdin_trips_only_at_tier_one(self):
        self.assert_trip("t_stdin", "stdin")
        self.assert_pass("t_stdin", tier=2, blocked=_blocked(2))

    def test_stdin_does_not_trip_without_tsn_stdin(self):
        self.assert_pass("t_stdin", tier=1, blocked=_blocked(1), stdin=False)

    def test_the_temp_dir_control_is_filesystem_alone(self):
        # tsn_control_temp_dir reads TMPDIR on its way to the filesystem; the
        # control makes that read filesystem, which tier 2 allows here.
        self.assert_pass("t_tmp_control", tier=2, blocked=_blocked(2, ["filesystem"]))
        self.assert_trip("t_tmp_control", "filesystem", tier=2,
                         blocked=_blocked(2, ["clock"]))

    def test_the_env_control_and_its_restore_pass_with_environment_allowed(self):
        self.assert_pass("t_env_control", tier=2, blocked=_blocked(2, ["environment"]))

    def test_the_env_control_trips_as_environment_when_it_is_blocked(self):
        m = self.assert_trip("t_env_control", "environment", tier=2,
                             blocked=_blocked(2, ["filesystem"]))
        self.assertIn("tsn_control_set_env", m.group(4))

    def test_a_dependency_read_is_attributed_to_the_dependency(self):
        m = self.assert_trip("t_dep_read", "filesystem")
        self.assertTrue(m.group(4).startswith("_ZN4depx4read"), m.group(0))

    def test_only_a_blocked_group_trips(self):
        # clock blocked, filesystem not: a file read passes.
        self.assert_pass("t_fs", tier=2, blocked="clock", stdin=False)
        self.assert_trip("t_sysnow", "clock", tier=2, blocked="clock", stdin=False)

    def test_without_tsn_blocked_every_test_passes_and_nothing_arms(self):
        # Every test but t_fail does its I/O freely and passes when the guard
        # is not armed; nothing prints the handshake.
        code, out = self.run_exe(["--test-threads=1", "--skip", "t_fail"], stdin=False)
        self.assertEqual(code, 0, out)
        self.assertNotIn("IOGuardViolation", out)
        self.assertNotIn("tsn-hook:", out)
        self.assertRegex(out, r"test result: ok\. \d+ passed; 0 failed")

    def test_a_unit_that_only_allocates_passes_at_tier_one(self):
        # Ruling R11: on darwin the allocator's own `mach_absolute_time` read
        # (xzone building a thread cache) is infrastructure, not the unit's.
        self.assert_pass("t_alloc")
        self.assert_pass("t_alloc_thread")

    def test_the_allocator_exemption_does_not_swallow_a_clock_read(self):
        for name, fn in (("t_sysnow", "_ZN5probe6sysnow"), ("t_instant", "_ZN5probe7instant")):
            with self.subTest(test=name):
                m = self.assert_trip(name, "clock")
                self.assertTrue(m.group(4).startswith(fn), m.group(0))

    def test_the_system_internal_table_is_load_bearing_on_darwin_and_inert_on_linux(self):
        # The same probe under a hook rendered with SYSTEM_INTERNAL_IMAGES
        # empty. darwin: every test thread trips `clock` in the allocator.
        # linux: glibc's malloc reads no clock, so nothing changes.
        env = dict(os.environ, TEST_SAFETY_NET_CACHE=os.path.join(self.tmp, "cache"))
        with mock.patch.object(io_guard_rust, "SYSTEM_INTERNAL_IMAGES", ()):
            bare = io_guard_rust.hook_library(self.crate, env)
        self.assertNotEqual(bare, self.hook)
        saved = self.hook
        try:
            type(self).hook = bare
            for name in ("t_pure", "t_alloc"):
                with self.subTest(test=name):
                    if PLATFORM == "darwin":
                        m = self.assert_trip(name, "clock")
                        self.assertEqual(m.group(3), "mach_absolute_time")
                    else:
                        self.assert_pass(name)
        finally:
            type(self).hook = saved

    def test_a_binary_with_no_symtab_cannot_attribute(self):
        # Ruling R1: on linux the attribution reads /proc/self/exe's .symtab;
        # without one the hook exits 2 rather than passing anything.
        if PLATFORM != "linux":
            self.skipTest("the .symtab rule is linux's; darwin resolves through dladdr")
        stripped = os.path.join(self.tmp, "p-stripped")
        shutil.copy(self.exe, stripped)
        subprocess.run(["strip", stripped], check=True)
        code, out = self.run_test("t_pure", blocked=_blocked(1), exe=stripped)
        self.assertEqual(code, 2, out)
        self.assertRegex(out, r"(?m)^tsn-hook: cannot attribute \(.+\)$")


if __name__ == "__main__":
    unittest.main()
