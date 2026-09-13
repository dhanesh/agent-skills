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

Ruling R26 -- v0 mangling, rustc 1.98's default, decided PER SYMBOL. The
hook's classifier is compiled alone (`TestClassifier`) and fed real 1.98
symbol names, so each v0 rule is killed on ANY toolchain; `TestProbeV0Crate`
reruns every probe case with the crate mangled v0 over a legacy std, and on
1.98 every probe case is all-v0 already:
Each was run (2026-09-13); "live" names the probe tests that also kill it on
a 1.98 toolchain:
* a back-reference resolved from the wrong base ->
  `TestClassifier.test_a_crate_reached_through_a_back_reference_is_a_crate`;
* the v0 boundary identifier never matched ->
  `TestClassifier.test_the_v0_boundary_is_the_decoded_identifier_in_the_test_crate`;
  live: `test_a_termination_report_inlined_into_libtest_is_judged` (R17a's
  `assert_test_result`). R13's `__rust_begin_short_backtrace` is no longer
  what catches `t_inline_*` under v0: the std generic the body inlined into
  keeps the test's closure in its self type, and R14 decides there first;
* an impl read transparent instead of by its self type ->
  `TestClassifier.test_an_impl_is_judged_by_its_self_type`; live:
  `test_a_crate_trait_impl_is_judged_by_its_self_type`;
* drop glue's generics never searched ->
  `TestClassifier.test_drop_glue_is_decided_by_the_first_crate_anywhere_in_t`;
  live: `test_a_crate_drop_payload_in_a_std_container_is_judged`,
  `test_a_panic_payload_dropped_by_libtest_is_judged`,
  `test_an_all_uppercase_crate_name_is_a_crate`;
* a primitive or placeholder read as a crate ->
  `TestClassifier.test_a_primitive_or_placeholder_names_no_crate`; live:
  `test_a_blanket_impl_over_a_generic_names_no_crate`,
  `test_a_stack_deeper_than_the_cap_fails_closed`;
* the hook's and `rust_binary`'s parsers diverging (their depth caps, say) ->
  `TestClassifier.test_the_hook_and_rust_binary_agree_on_every_fixture_and_prefix`,
  and on real symbols `..._agree_on_a_real_binary`.
Ruling R27 -- cargo 1.94+ words a stale lock differently, and its help line
names `--offline`: matching the offline note first ->
`TestLockedWording.test_a_stale_lock_is_its_own_refusal_in_every_cargos_wording`
(and `TestExitContract.test_a_stale_cargo_lock_is_2_and_left_byte_identical`
on 1.94/1.98).
Fix round 1 (rulings R28-R32), each run:
* R28, v0's any-identifier SEED/CONTROL scan restored ->
  `TestClassifier.test_the_name_tables_keep_legacy_scope` (negative fixtures
  from legacy's spelling) and, live on 1.98 and in TestProbeV0Crate,
  `test_a_seed_or_control_name_outside_its_legacy_scope_is_judged`;
* R28, legacy's raw-substring SEED match restored -> the same live test
  (`r28_named_hashmap_random_keys`, every toolchain) and
  `TestClassifier.test_the_legacy_rules_are_unchanged`;
* R29, rust_binary costing a frame (or a depth step) per `N` level ->
  `TestClassifier.test_a_deep_nested_path_reads_as_the_hook_reads_it`;
* R30, no `--test` stem check ->
  `TestReservedTargets.test_a_test_stem_named_like_a_std_or_runner_crate_is_refused`
  and, live, `TestExitContract.test_a_target_named_like_the_runner_or_std_is_refused`;
  no artifact check -> `TestReservedTargets.test_a_path_crate_named_like_std_or_the_runner_is_refused`
  and, live, `TestExitContract.test_a_path_dependency_named_backtrace_is_refused`;
* R32, `--locked was passed` matched anywhere in stderr ->
  `TestLockedWording.test_a_build_scripts_own_words_are_not_cargos`.
Fix round 2 (rulings R34, R35), each run:
* R34, CONTROL scanning an M/X self type's generic arguments again ->
  `TestClassifier.test_the_name_tables_keep_legacy_scope` (the `mx_*`
  fixtures) and, live on 1.98 and in TestProbeV0Crate,
  `test_a_control_type_only_as_a_std_generic_argument_is_judged`;
* R35, a bin/example/bench target counted as a linked crate ->
  `TestReservedTargets.test_a_bin_example_or_bench_so_named_is_not_refused`.
Fix round 3 (rulings R37, R38), each run:
* R37, every X impl lending its self type a control name again ->
  `TestClassifier.test_the_name_tables_keep_legacy_scope` (blanket and
  fn-item fixtures) and, live on 1.98 and in TestProbeV0Crate,
  `test_a_blanket_or_fn_item_impl_over_the_helper_is_judged`;
* R38, a shipped helper without `#[inline(never)]` ->
  `test_every_honest_control_path_still_passes_at_tier_2` (the temp-dir
  control at opt1).

The WRAPPER (`main` and its helpers) is tested the same way, through a
fixture crate `fx` with its own committed `Cargo.lock` under a temp dir: the
documented command run verbatim, every group, tier 2, the exit contract, the
build plan, the refusals, the handshake, the partition, and that nothing is
written. Mutation coverage:
* dropping `--locked` ->
  `TestExitContract.test_a_stale_cargo_lock_is_2_and_left_byte_identical`;
* reading exit 0 as GREEN without the result line ->
  `TestExitContract.test_an_ignored_test_is_4`;
* reading a build failure (cargo's 101) as RED ->
  `TestExitContract.test_a_private_item_is_no_build`;
* removing the handshake check from `classify` ->
  `TestHandshake.test_a_hook_built_without_its_constructor_is_not_armed`;
* passing on a caller's `RUST_TEST_NOCAPTURE` ->
  `TestExitContract.test_a_callers_rust_test_nocapture_is_not_passed_on`;
* R19, removing the hook's `exit` intercept ->
  `TestForgedVerdicts.test_a_forged_verdict_then_exit_0_is_4`;
* R19, not requiring libtest's own status line ->
  `TestClassify.test_green_needs_libtests_own_status_line`;
* R19, reading a forged pass as GREEN on a non-zero code ->
  `TestForgedVerdicts.test_a_forged_verdict_then_abort_is_not_green`;
* R20, keeping the last of several test executables ->
  `TestWorkspace.test_build_test_refuses_two_executables_for_one_target` and
  `TestWorkspace.test_the_root_without_p_is_2`;
* R21, ignoring the loader's preload failure ->
  `TestClassify.test_a_preload_the_loader_skipped_is_not_armed`; accepting a
  handshake printed after `running N test` ->
  `TestClassify.test_a_handshake_after_libtest_started_is_not_armed`;
* a test name starting with `-` passed to libtest ->
  `TestExitContract.test_a_test_name_starting_with_a_dash_is_2`;
* a timeout read as RED -> `TestExitContract.test_a_timeout_is_4`;
* R22(a), forwarding the test's own exit code ->
  `TestHostileEndings.test_closing_stderr_then_exit_is_4_by_the_reserved_status`;
  not reading status 125 as NO TEST ->
  `TestClassify.test_the_reserved_status_is_no_test_with_or_without_the_line`;
* R22(b), a non-null re-entrancy gate ->
  `TestHostileEndings.test_filling_every_pthread_key_then_reading_a_file_trips`
  and `..._then_exit_is_4`;
* R22(c), no libtest-harness check ->
  `TestHostileEndings.test_a_harness_false_target_is_4_forged_or_honest`; no
  `running N test` rule -> `TestClassify.test_no_libtest_run_is_no_test`;
* R22(d), an unhooked exec entry point or quick_exit (linux) ->
  `TestHostileEndings.test_command_exec_trips_subprocess_at_its_own_entry`,
  `..._execv_and_execl_...`, `test_quick_exit_is_4`.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import struct
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
import rust_binary                                                  # noqa: E402
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
        self.assertIn("static EARLY_EXIT_STATUS: c_int = 125;", block)
        self.assertIn('static SEED_CRATE: &[u8] = b"std";', block)
        for name in ("TRANSPARENT", "RUNNER", "SEED", "SEED_CRATE", "TEST_BODY_BOUNDARY",
                     "CONTROL", "SYSTEM_INTERNAL", "INTERCEPT", "EARLY_EXIT_STATUS"):
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
        self.assertEqual(io_guard_rust.SEED_CRATE, "std")
        self.assertEqual(io_guard_rust.TEST_BODY_BOUNDARY_MARKERS,
                         ("__rust_begin_short_backtrace", "assert_test_result"))
        self.assertFalse(hasattr(io_guard_rust, "THREAD_START_MARKERS"),
                         "THREAD_START_MARKERS was removed for the thread-identity rule (R15b)")
        self.assertEqual(io_guard_rust.CONTROL_HELPERS,
                         {"tsn_control_temp_dir": "filesystem",
                          "tsn_control_set_env": "environment",
                          "TsnControlEnv": "environment"})
        self.assertEqual(io_guard_rust.SYSTEM_INTERNAL_IMAGES, ("libsystem_malloc.dylib",))
        self.assertEqual(io_guard_rust.EARLY_EXIT_STATUS, 125)

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
            # Ruling R19: not an I/O group -- it never trips, it reports.
            "process-exit": {"exit"},
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
        known = set(stack_rust.GROUPS) | {"stdin", "process-exit"}
        for plat, table in io_guard_rust.INTERCEPTS.items():
            self.assertLessEqual(set(table), known, plat)
        self.assertLessEqual(set(io_guard_rust.CONTROL_HELPERS.values()), set(stack_rust.GROUPS))

    def test_exit_is_reported_never_judged_and_underscore_exit_is_not_hooked(self):
        # Ruling R19: `exit` has its own pseudo-group, which is never in
        # TSN_BLOCKED; `_exit`/`_Exit` stay unhooked, because the hook's own
        # `_exit(3)`/`_exit(2)` must never recurse into it.
        for plat, table in io_guard_rust.INTERCEPTS.items():
            self.assertEqual(table["process-exit"], ("exit", "quick_exit"), plat)
            every = {n for ns in table.values() for n in ns}
            self.assertFalse({"_exit", "_Exit"} & every, plat)
        self.assertNotIn("process-exit", _blocked(1).split(","))
        with open(HOOK_SRC, encoding="utf-8") as f:
            text = f.read()
        # A DEFINITION of either would be a hook; the extern declaration the
        # hook calls through is not.
        self.assertNotRegex(text, r'extern "C" fn _exit\(|extern "C" fn _Exit\(|fn _Exit\(|'
                                  r"my__exit|I__EXIT")

    def test_every_exec_entry_point_is_subprocess_where_the_platform_has_it(self):
        # Ruling R22(d): glibc's exec family calls its internal __execve.
        want = {"darwin": {"execve", "execv", "execvp", "execl", "execlp"},
                "linux": {"execve", "execv", "execvp", "execvpe", "execl", "execlp", "fexecve"}}
        for plat, names in want.items():
            sub = set(io_guard_rust.INTERCEPTS[plat]["subprocess"])
            self.assertLessEqual(names, sub, plat)
            self.assertFalse({"execvpe", "fexecve"} & sub if plat == "darwin" else set(), plat)

    def test_both_gates_compare_the_sentinel_address_not_non_null(self):
        # Ruling R22(b): filling every pthread key used to silence both gates.
        with open(HOOK_SRC, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("pthread_getspecific(KEY).is_null()", text)
        self.assertIn("pthread_getspecific(KEY) as *const u8 == &SENTINEL as *const u8", text)
        self.assertEqual(text.count("if in_hook() {"), 2)

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
            have = _macho_interpose_binds(self.lib)
            if have is None:
                tool = shutil.which("dyld_info") or shutil.which("xcrun")
                if not tool:
                    self.skipTest("no dyld_info: the __interpose entries cannot be listed")
                cmd = [tool, "-fixups", self.lib] if tool.endswith("dyld_info") \
                    else [tool, "dyld_info", "-fixups", self.lib]
                proc = subprocess.run(cmd, capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                have = {m.group(1) for m in re.finditer(
                    r"__interpose\s+\S+\s+bind\s+\S+?/_(\w+)", proc.stdout)}
        self.assertEqual(want - have, set(), "intercepts missing from the built library")


def _leb(data, i, signed=False):
    value = shift = 0
    while True:
        byte = data[i]
        i += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            if signed and byte & 0x40:
                value -= 1 << shift
            return value, i


def _macho_interpose_binds(path):
    """The symbols bound into `__DATA,__interpose` of the Mach-O dylib at
    `path`, read from its classic bind opcodes -- what dyld itself executes
    -- or None when it has none to read (chained fixups, threaded binds).

    Not `dyld_info -fixups`: on macOS 27 (the /usr/bin and the Xcode copy
    alike) it was MEASURED misnaming two slots of a correct hook -- `_execv`
    shown as `_execve` and `_execvp` as `_exit`, each the next import
    alphabetically -- while these opcodes, and an `execv`/`execvp` made
    under the hook, bound and tripped as themselves."""
    with open(path, "rb") as f:
        d = f.read()
    ncmds = struct.unpack_from("<I", d, 16)[0]
    off, segs, interpose, bind = 32, [], None, None
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", d, off)
        if cmd == 0x19:                                           # LC_SEGMENT_64
            segs.append(struct.unpack_from("<Q", d, off + 24)[0])
            for k in range(struct.unpack_from("<I", d, off + 64)[0]):
                s = off + 72 + 80 * k                             # section_64
                if d[s:s + 16].rstrip(b"\0") == b"__interpose":
                    addr, sz = struct.unpack_from("<QQ", d, s + 32)
                    interpose = (addr, addr + sz)
        elif cmd in (0x22, 0x80000022):                           # LC_DYLD_INFO(_ONLY)
            bind = struct.unpack_from("<II", d, off + 16)         # bind_off, bind_size
        off += size
    if interpose is None or not bind or not bind[1]:
        return None
    i, end, sym, addr, bound = bind[0], bind[0] + bind[1], None, 0, {}
    while i < end:
        op, imm = d[i] & 0xF0, d[i] & 0x0F
        i += 1
        if op in (0x00, 0x10, 0x30, 0x50):      # DONE, dylib ordinal/special, type
            continue
        if op == 0x20:                          # SET_DYLIB_ORDINAL_ULEB
            _, i = _leb(d, i)
        elif op == 0x40:                        # SET_SYMBOL_TRAILING_FLAGS_IMM
            e = d.index(b"\0", i)
            sym, i = d[i:e].decode("utf-8", "replace"), e + 1
        elif op == 0x60:                        # SET_ADDEND_SLEB
            _, i = _leb(d, i, signed=True)
        elif op == 0x70:                        # SET_SEGMENT_AND_OFFSET_ULEB
            o, i = _leb(d, i)
            if imm >= len(segs):
                return None
            addr = segs[imm] + o
        elif op == 0x80:                        # ADD_ADDR_ULEB
            o, i = _leb(d, i)
            addr = (addr + o) % (1 << 64)
        elif op == 0x90:                        # DO_BIND
            bound[addr], addr = sym, addr + 8
        elif op == 0xA0:                        # DO_BIND_ADD_ADDR_ULEB
            o, i = _leb(d, i)
            bound[addr], addr = sym, (addr + 8 + o) % (1 << 64)
        elif op == 0xB0:                        # DO_BIND_ADD_ADDR_IMM_SCALED
            bound[addr], addr = sym, addr + 8 + imm * 8
        elif op == 0xC0:                        # DO_BIND_ULEB_TIMES_SKIPPING_ULEB
            count, i = _leb(d, i)
            skip, i = _leb(d, i)
            for _ in range(count):
                bound[addr], addr = sym, addr + 8 + skip
        else:                                   # threaded binds and anything newer
            return None
    return {s[1:] for a, s in bound.items()
            if interpose[0] <= a < interpose[1] and s and s.startswith("_")}


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

// Ruling R37: blanket impls over a BARE generic (legacy `<T as probe::Touch>
// ::touch`, no crate) and over `F: Fn` -- v0 prints the type they run on.
pub trait Touch { fn touch(&self) -> usize; }
impl<T> Touch for T {
    #[inline(never)]
    fn touch(&self) -> usize { std::fs::read("/etc/hosts").map(|b| b.len()).unwrap_or(0) }
}
#[inline(never)] pub fn record<T: Touch>(x: &T) -> usize { x.touch() }
pub trait SpawnTrue { fn spawn_true(&self) -> bool; }
impl<T> SpawnTrue for T {
    #[inline(never)]
    fn spawn_true(&self) -> bool { std::process::Command::new("true").status().is_ok() }
}
pub trait ViaFn { fn via_fn(&self) -> usize; }
impl<F: Fn(&str, &str) -> R, R> ViaFn for F {
    #[inline(never)]
    fn via_fn(&self) -> usize { std::fs::read("/etc/hosts").map(|b| b.len()).unwrap_or(0) }
}
'''
# The control helpers: Task 8's canonical text, verbatim (ruling R2).
CONTROL_HELPERS_RS = r'''#[inline(never)]
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

// Ruling R19: an exit made by the test body; at opt the body inlines under
// libtest's `__rust_begin_short_backtrace`, the body boundary.
#[test] fn t_exit() { std::process::exit(0); }

// Ruling R28: names OUTSIDE the tables' legacy scope. std reached through a
// crate type named like std's seed function (`std::fs::read::<p::
// hashmap_random_keys>` under v0), a test whose own name carries it, and the
// TsnControlEnv helper handed to std as a path: none is std seeding, none is
// the control -- each read is the test's own.
#[allow(non_camel_case_types)]
struct hashmap_random_keys;
impl AsRef<std::path::Path> for hashmap_random_keys {
    fn as_ref(&self) -> &std::path::Path { std::path::Path::new("/etc/hosts") }
}
#[test] fn t_seed_lookalike() { let _ = std::fs::read(hashmap_random_keys); }
// (Not `t_…`: libtest filters by substring, and other probe runs name
// `t_hashmap`.)
#[test] fn r28_named_hashmap_random_keys() { let _ = std::fs::read("/etc/hosts"); }
impl AsRef<std::path::Path> for TsnControlEnv {
    fn as_ref(&self) -> &std::path::Path { std::path::Path::new("/etc/hosts") }
}
#[test] fn t_control_lookalike() {
    let _ = std::fs::read(TsnControlEnv { key: "TSN_PROBE_UNSET".to_string(), old: None });
}

// Ruling R34: std's OWN methods on a std type whose generic argument is the
// helper -- `<Result<&str, p::TsnControlEnv>>::map`, `<Receiver<p::
// TsnControlEnv>>::recv_timeout` -- are std's work, not the control. (Not
// `t_…`: libtest filters by substring.)
#[test] fn mx_result_map_read() {
    let r: Result<&str, TsnControlEnv> = Ok("/etc/hosts");
    if let Ok(v) = r.map(std::fs::read) { assert!(v.is_ok()); }
}
#[test] fn mx_recv_timeout_clock() {
    let (_tx, rx) = std::sync::mpsc::channel::<TsnControlEnv>();
    assert!(rx.recv_timeout(std::time::Duration::from_millis(1)).is_err());
}

// Ruling R37: a blanket impl over a bare generic, or over `F: Fn`, run on the
// helper -- `<p::TsnControlEnv as probe::Touch>::touch` under v0 -- is the
// impl's own work, not the control.
#[test] fn a_blanket_record_fs() {
    let g = tsn_control_set_env("TSN_R37_KEY", "1");
    assert!(record(&g) > 0);
}
#[test] fn a_blanket_spawn() {
    let g = tsn_control_set_env("TSN_R37_KEY", "1");
    assert!(g.spawn_true());
}
#[test] fn a_fnitem_setenv_fs() { assert!(tsn_control_set_env.via_fn() > 0); }
// ... while the honest control paths stay the control: an inherent method on
// the helper, and drop glue whose payload is the helper.
impl TsnControlEnv {
    #[inline(never)]
    #[allow(unused_unsafe)]
    fn restore(&mut self) { unsafe { std::env::remove_var(&self.key) } }
}
#[test] fn h_env_inherent() { let mut g = tsn_control_set_env("TSN_R37_KEY", "1"); g.restore(); }
#[test] fn h_env_tuple_glue() { let _t = (tsn_control_set_env("TSN_R37_KEY", "1"), 7u8); }
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


def build_probe(root, target_dir, crate=None, opt=False, test="p", rustflags=None):
    """Build the probe's `test` target; return (crate, exe). `opt` builds the
    test profile at opt-level 1 -- the optimized-build hole (ruling R13).
    `rustflags` is the FIXTURE's only (the wrapper never sets RUSTFLAGS)."""
    crate = crate or write_probe_crate(root)
    env = dict(os.environ, CARGO_TARGET_DIR=target_dir, RUSTUP_AUTO_INSTALL="0")
    if opt:
        env["CARGO_PROFILE_TEST_OPT_LEVEL"] = "1"
    if rustflags:
        env["RUSTFLAGS"] = rustflags
        env.pop("CARGO_ENCODED_RUSTFLAGS", None)
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

    # The probe's own RUSTFLAGS (a subclass reruns every case with its crate
    # mangled another way); None builds exactly as a user's `cargo test` does.
    RUSTFLAGS = None

    @classmethod
    def setUpClass(cls):
        if not CARGO or not RUSTC:
            raise unittest.SkipTest("no `cargo`/`rustc` on PATH: the Rust hook is NOT "
                                    "exercised on this machine.")
        if PLATFORM is None:
            raise unittest.SkipTest("the hook is built for darwin and linux only")
        cls.tmp = tempfile.mkdtemp(prefix="tsn-rust-probe-")
        cls.crate = write_probe_crate(cls.tmp)
        flags = cls.RUSTFLAGS
        _, cls.exe = build_probe(cls.tmp, os.path.join(cls.tmp, "target"), crate=cls.crate,
                                 rustflags=flags)
        cls.check_build()
        # The SAME sources at opt-level 1, where the test body inlines away
        # (ruling R13). A separate target dir so the two builds never collide.
        _, cls.exe_opt = build_probe(cls.tmp, os.path.join(cls.tmp, "target-opt"),
                                     crate=cls.crate, opt=True, rustflags=flags)
        # The all-uppercase-crate target (`tests/ABC.rs` -> crate `ABC`),
        # optimized so the payload Drop inlines into libtest's drop path.
        _, cls.exe_upper = build_probe(cls.tmp, os.path.join(cls.tmp, "target-opt"),
                                       crate=cls.crate, opt=True, test="ABC", rustflags=flags)
        env = dict(os.environ, TEST_SAFETY_NET_CACHE=os.path.join(cls.tmp, "cache"))
        cls.hook = io_guard_rust.hook_library(cls.crate, env)
        cls.scratch = os.path.join(cls.tmp, "scratch")
        os.makedirs(cls.scratch)

    @classmethod
    def check_build(cls):
        """A subclass's chance to refuse the build it got (skip, never fail)."""

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
        self.assertIn("p::Dsp", io_guard_rust.demangle(m.group(4)), m.group(0))
        m = self.assert_trip("t_termination", "filesystem")
        self.assertIn("p::Rep", io_guard_rust.demangle(m.group(4)), m.group(0))

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
        # classify reads as runner). R13 catches it at the boundary. Under v0
        # (R26) the std generic the body inlined into keeps its CONCRETE self
        # type -- `<p::t_inline_env::{closure#0} as FnOnce<()>>::call_once`,
        # `<Map<Iter<&str>, p::t_inline_read::{closure#0}> as Iterator>::fold`
        # -- so R14 judges it one frame earlier, by the test's own closure;
        # legacy spells those self types `F`, names no crate, and reaches the
        # boundary. Either way the attribution is the test's.
        for name, group in (("t_inline_read", "filesystem"), ("t_inline_env", "environment")):
            with self.subTest(test=name):
                code, out = self.run_exe([name, "--exact", "--test-threads=1"],
                                         blocked=_blocked(1), exe=self.exe_opt)
                self.assertEqual(code, 3, out)
                m = VIOLATION.search(out)
                self.assertEqual((m.group(1), m.group(2)), ("1", group), out)
                who = m.group(4)
                self.assertTrue(who == "(test body, inlined)" or (
                    who.startswith(("_R", "__R"))
                    and "p::%s::{closure" % name in io_guard_rust.demangle(who)), out)
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
        self.assertTrue(io_guard_rust.demangle(m.group(4)).startswith("p::t_blanket"), m.group(0))

    def test_a_seed_or_control_name_outside_its_legacy_scope_is_judged(self):
        # Ruling R28: `std::fs::read::<p::hashmap_random_keys>` is std's read,
        # not std seeding; a test NAMED like the seed is the test's; and
        # `std::fs::read::<p::TsnControlEnv>` at tier 2, environment allowed,
        # is a filesystem read, not the environment control. Legacy judged
        # the first and the third all along. MUTATION: restoring v0's
        # any-identifier scan passes t_seed_lookalike and t_control_lookalike
        # on 1.98 and in TestProbeV0Crate; restoring legacy's raw-substring
        # seed match passes r28_named_hashmap_random_keys (debug).
        for label, exe in (("debug", self.exe), ("opt1", self.exe_opt)):
            with self.subTest(build=label):
                for name in ("t_seed_lookalike", "r28_named_hashmap_random_keys"):
                    code, out = self.run_exe([name, "--exact", "--test-threads=1"],
                                             blocked=_blocked(1), exe=exe)
                    self.assertEqual(code, 3, out)
                    self.assertRegex(out, r"(?m)^IOGuardViolation: a tier 1 candidate reached "
                                          r"filesystem I/O")
                code, out = self.run_exe(["t_control_lookalike", "--exact", "--test-threads=1"],
                                         tier=2, blocked=_blocked(2, ["environment"]), exe=exe)
                self.assertEqual(code, 3, out)
                self.assertRegex(out, r"(?m)^IOGuardViolation: a tier 2 candidate reached "
                                      r"filesystem I/O")

    def test_a_control_type_only_as_a_std_generic_argument_is_judged(self):
        # Ruling R34: under v0 an M/X self type is CONCRETE, so the helper
        # appears inside `<Result<&str, p::TsnControlEnv>>::map` and
        # `<Receiver<p::TsnControlEnv>>::recv_timeout`; legacy prints `T`/`E`
        # there. At tier 2 with environment allowed each read is the test's
        # filesystem or clock I/O -- not the environment control, which read
        # both GREEN. MUTATION: scanning the self type's generic arguments
        # again passes both on 1.98 and in TestProbeV0Crate. The honest
        # control run -- the helper's own set/restore -- stays 0.
        for label, exe in (("debug", self.exe), ("opt1", self.exe_opt)):
            with self.subTest(build=label):
                for name, group in (("mx_result_map_read", "filesystem"),
                                    ("mx_recv_timeout_clock", "clock")):
                    code, out = self.run_exe([name, "--exact", "--test-threads=1"], tier=2,
                                             blocked=_blocked(2, ["environment"]), exe=exe)
                    self.assertEqual(code, 3, out)
                    self.assertRegex(out, r"(?m)^IOGuardViolation: a tier 2 candidate reached "
                                          r"%s I/O" % group)
                code, out = self.run_exe(["t_env_control", "--exact", "--test-threads=1"],
                                         tier=2, blocked=_blocked(2, ["environment"]), exe=exe)
                self.assertEqual(code, 0, out)
                self.assertNotIn("IOGuardViolation", out)

    def test_a_blanket_or_fn_item_impl_over_the_helper_is_judged(self):
        # Ruling R37: v0 prints a blanket `impl<T> Touch for T` at the type it
        # ran on -- `<p::TsnControlEnv as probe::Touch>::touch` -- and an
        # `impl<F: Fn> ViaFn for F` at the fn item, `<p::tsn_control_set_env
        # as probe::ViaFn>::via_fn`; legacy prints `<T as ...>`, `<F as ...>`.
        # Neither is the control, so at tier 2 with environment allowed each
        # is the test's filesystem or subprocess I/O. MUTATION: letting every
        # X impl lend its self type a control name passes all three (1.98,
        # TestProbeV0Crate).
        for label, exe in (("debug", self.exe), ("opt1", self.exe_opt)):
            with self.subTest(build=label):
                for name, group in (("a_blanket_record_fs", "filesystem"),
                                    ("a_blanket_spawn", "subprocess"),
                                    ("a_fnitem_setenv_fs", "filesystem")):
                    code, out = self.run_exe([name, "--exact", "--test-threads=1"], tier=2,
                                             blocked=_blocked(2, ["environment"]), exe=exe)
                    self.assertEqual(code, 3, out)
                    self.assertRegex(out, r"(?m)^IOGuardViolation: a tier 2 candidate reached "
                                          r"%s I/O" % group)

    def test_every_honest_control_path_still_passes_at_tier_2(self):
        # Rulings R37 and R38: the shipped control paths -- tsn_control_set_env
        # and `<TsnControlEnv as Drop>::drop` (t_env_control), an inherent
        # method on the helper, drop glue whose payload is the helper, and
        # tsn_control_temp_dir -- each pass with their group allowed, at debug
        # AND opt1. At opt-level >= 1 an inlinable helper vanished into the
        # test body and the honest temp-dir run tripped; `#[inline(never)]`
        # (R38) keeps each its own frame.
        for label, exe in (("debug", self.exe), ("opt1", self.exe_opt)):
            for name, allow in (("t_env_control", "environment"), ("h_env_inherent", "environment"),
                                ("h_env_tuple_glue", "environment"),
                                ("t_tmp_control", "filesystem")):
                with self.subTest(build=label, test=name):
                    code, out = self.run_exe([name, "--exact", "--test-threads=1"], tier=2,
                                             blocked=_blocked(2, [allow]), exe=exe)
                    self.assertEqual(code, 0, out)
                    self.assertNotIn("IOGuardViolation", out)

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
        self.assertTrue(io_guard_rust.demangle(m.group(4)).startswith("probe::env_var"),
                        m.group(0))

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
        self.assertTrue(io_guard_rust.demangle(m.group(4)).startswith("p::t_thread"), m.group(0))

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
        self.assertTrue(io_guard_rust.demangle(m.group(4)).startswith("depx::read"), m.group(0))

    def test_only_a_blocked_group_trips(self):
        # clock blocked, filesystem not: a file read passes.
        self.assert_pass("t_fs", tier=2, blocked="clock", stdin=False)
        self.assert_trip("t_sysnow", "clock", tier=2, blocked="clock", stdin=False)

    def test_without_tsn_blocked_every_test_passes_and_nothing_arms(self):
        # Every test but t_fail does its I/O freely and passes when the guard
        # is not armed; nothing prints the handshake.
        # t_exit ends the process by design (ruling R19): skipped too.
        code, out = self.run_exe(["--test-threads=1", "--skip", "t_fail", "--skip", "t_exit"],
                                 stdin=False)
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
        for name, fn in (("t_sysnow", "probe::sysnow"), ("t_instant", "probe::instant")):
            with self.subTest(test=name):
                m = self.assert_trip(name, "clock")
                self.assertTrue(io_guard_rust.demangle(m.group(4)).startswith(fn), m.group(0))

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

    def test_an_exit_from_test_code_is_reported_and_libtests_own_is_not(self):
        # Ruling R19: libtest's own exits -- 101 after a failure, and the
        # normal end after main returns -- have no crate frame: silent.
        for exe in (self.exe, self.exe_opt):
            with self.subTest(exe=os.path.basename(os.path.dirname(exe))):
                code, out = self.run_exe(["t_exit", "--exact", "--test-threads=1"],
                                         blocked=_blocked(1), exe=exe)
                # Ruling R22(a): the test's exit(0) becomes the reserved status.
                self.assertEqual(code, io_guard_rust.EARLY_EXIT_STATUS, out)
                self.assertRegex(out, r"(?m)^tsn-hook: early exit \(.+\)$")
                self.assertNotIn("test result:", out)
                for name, want in (("t_pure", 0), ("t_fail", 101)):
                    code, out = self.run_exe([name, "--exact", "--test-threads=1"],
                                             blocked=_blocked(1), exe=exe)
                    self.assertEqual(code, want, out)
                    self.assertNotIn("early exit", out)

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


class TestProbeV0Crate(TestProbe):
    """Every TestProbe case again, the probe crate (and depx) mangled v0 over a
    std and libtest that are still legacy -- ruling R26's PER-SYMBOL decision
    in one live binary. Its drop glue, `assert_test_result<T>` and impl frames
    are the crate's own instantiations, so they are v0 too.

    On a toolchain whose std is already v0 (1.98) the plain TestProbe run IS
    all-v0, and forcing legacy needs `-Z unstable-options`: this class then
    skips visibly."""

    RUSTFLAGS = "-C symbol-mangling-version=v0"

    @classmethod
    def check_build(cls):
        with open(cls.exe, "rb") as f:
            data = f.read()
        if b"_ZN4test" not in data:
            shutil.rmtree(cls.tmp, ignore_errors=True)
            raise unittest.SkipTest("libtest is already v0 on this toolchain: TestProbe is the "
                                    "all-v0 run, and there is no legacy std to mix with")
        if b"_RNvCs" not in data:
            raise AssertionError("RUSTFLAGS did not make the probe crate v0")


# ── rustc: the frame classifier alone, on symbol names (ruling R26) ──────

CLASSIFY_BEGIN, CLASSIFY_END = "// @@TSN-CLASSIFY-BEGIN@@", "// @@TSN-CLASSIFY-END@@"
_HARNESS_MAIN = r'''
fn main() {
    use std::io::BufRead;
    for line in std::io::stdin().lock().lines() {
        let line = line.expect("stdin");
        let s = line.as_bytes();
        let krate = match crate_of(s) {
            None => "None".to_string(),
            Some(k) => format!("={}", String::from_utf8_lossy(k)),
        };
        let group = control(s).map(|g| String::from_utf8_lossy(g).into_owned());
        println!("{}\t{}\t{}\t{}", classify(s), test_boundary(s) as u8,
                 group.unwrap_or_else(|| "-".to_string()), krate);
    }
}
'''


def classifier_source():
    """The hook's rendered tables and its classifier region, VERBATIM, driven
    from stdin: the code the hook runs on each frame, on symbol names."""
    text = io_guard_rust.render_hook()
    tables = text[text.index(BEGIN):text.index(END) + len(END)]
    region = text[text.index(CLASSIFY_BEGIN):text.index(CLASSIFY_END) + len(CLASSIFY_END)]
    return ("#![allow(dead_code, non_upper_case_globals)]\nuse core::ffi::c_int;\n"
            + tables + "\n" + region + "\n" + _HARNESS_MAIN)


_RB_TESTS = _load("test_rust_binary")         # the v0 fixtures: real 1.98 symbols
V0, V0_MALFORMED, NEST = _RB_TESTS.V0, _RB_TESTS.V0_MALFORMED, _RB_TESTS._nest
V0_DEEP = _RB_TESTS.V0_DEEP
_SKIP = frozenset(io_guard_rust.TRANSPARENT_CRATES) | frozenset(io_guard_rust.RUNNER_CRATES)


def mirror(sym):
    """What the hook's classifier must answer for v0 `sym`, derived from
    rust_binary's INDEPENDENT parser: (class, boundary, control group, crate)."""
    t = io_guard_rust
    f = rust_binary.v0_facts(sym, _SKIP)
    if f is None:
        return ("0", "0", "-", "None")

    def transparent(k):
        return k == "" or k in t.TRANSPARENT_CRATES

    k = f.krate
    if transparent(k) and f.drop_crate:
        k = f.drop_crate
    if transparent(k) and any("drop_slow" in i for i in f.main_idents):
        k = "drop_slow"
    # Ruling R28, legacy's scope: SEED in the main path of a std symbol;
    # CONTROL in the main path, an M/X self type or drop glue's payload.
    if f.krate == t.SEED_CRATE and any(m in i for i in f.main_idents for m in t.SEED_MARKERS):
        cls = 3
    else:
        cls = 0 if transparent(k) else (2 if k in t.RUNNER_CRATES else 1)
    boundary = k in t.RUNNER_CRATES and any(m in i for i in f.main_idents
                                            for m in t.TEST_BODY_BOUNDARY_MARKERS)
    group = next((g for n, g in sorted(t.CONTROL_HELPERS.items()) if n in f.control_idents),
                 "-")
    return (str(cls), str(int(boundary)), group, "=" + k)


class TestClassifier(unittest.TestCase):
    """The hook's frame classifier, compiled alone and fed symbol names (R26).

    Each answer is (classify, test_boundary, control, crate_of): classify 0
    transparent, 1 crate, 2 libtest runner, 3 std seeding; crate `=` names
    no crate, `None` is not a Rust symbol the hook can read."""

    WANT = {
        "crate_fn": ("1", "0", "filesystem", "=p"),
        "calcx_fn": ("1", "0", "-", "=calcx"),
        "std_fn": ("0", "0", "-", "=std"),
        "closure": ("1", "0", "-", "=p"),
        "vendor_suffix": ("0", "0", "-", "=std"),
        "inherent_backref": ("1", "0", "-", "=p"),
        "trait_impl_backref": ("1", "0", "environment", "=p"),
        "blanket_dsp": ("1", "0", "-", "=p"),
        "blanket_u8": ("0", "0", "-", "="),
        "blanket_slice": ("0", "0", "-", "="),
        "blanket_placeholder": ("0", "0", "-", "="),
        "provided_std": ("0", "0", "-", "=core"),
        "provided_crate_self": ("1", "0", "-", "=p"),
        "provided_crate_trait": ("1", "0", "-", "=p"),
        "begin_short_backtrace": ("2", "1", "-", "=test"),
        "assert_test_result": ("2", "1", "-", "=test"),
        "std_begin_short_backtrace": ("0", "0", "-", "=std"),
        "libtest_fn": ("2", "0", "-", "=test"),
        "drop_vec": ("1", "0", "-", "=p"),
        "drop_tuple": ("1", "0", "-", "=p"),
        "drop_array": ("1", "0", "-", "=p"),
        "drop_dyn": ("1", "0", "-", "=p"),
        "drop_std": ("0", "0", "-", "=core"),
        "drop_std_linux": ("0", "0", "-", "=core"),
        "drop_runner_type": ("0", "0", "-", "=core"),
        "drop_control": ("1", "0", "environment", "=p"),
        "drop_slow_std": ("1", "0", "-", "=drop_slow"),
        "drop_slow_crate": ("1", "0", "-", "=p"),
        "seed": ("3", "0", "-", "=std"),
        # Ruling R28, written from LEGACY: it spells the first two
        # `_ZN3std2fs4read17h…E` (no generic arguments), i.e. std's read,
        # transparent -- neither seeding nor a control. A `Y` self type and a
        # crate fn carrying the seed's name are the crate's own frames.
        "std_read_seed_type": ("0", "0", "-", "=std"),
        "std_read_control_type": ("0", "0", "-", "=std"),
        "provided_control_self": ("1", "0", "-", "=p"),
        "crate_fn_seed_name": ("1", "0", "-", "=p"),
        # Ruling R34, written from LEGACY: `core::result::Result<T,E>::map`,
        # `Receiver<T>::recv_timeout`, `<IntoIter<T,A> as Iterator>::fold`,
        # `<&T as Debug>::fmt` -- the concrete helper is the crate's frame
        # (R14), never the control; the helper's own inherent impl still is.
        "mx_result_map": ("1", "0", "-", "=p"),
        "mx_receiver": ("1", "0", "-", "=p"),
        "mx_iter_x": ("1", "0", "-", "=p"),
        "mx_ref_self": ("1", "0", "-", "=p"),
        "inherent_control": ("1", "0", "environment", "=p"),
        # Ruling R37, written from LEGACY: `<T as p::Touch>::touch`, `<F as
        # p::ViaFn>::via_fn` name no control; `Drop` alone lends one, and only
        # core's (its path read through a back-reference, too).
        "blanket_x_control": ("1", "0", "-", "=p"),
        "fnitem_x_control": ("1", "0", "-", "=p"),
        "drop_x_trait_backref": ("1", "0", "environment", "=p"),
        "drop_lookalike_x": ("1", "0", "-", "=p"),
    }

    @classmethod
    def setUpClass(cls):
        if not RUSTC:
            raise unittest.SkipTest("no `rustc` on PATH: the hook's classifier is NOT "
                                    "exercised on this machine.")
        cls.tmp = tempfile.mkdtemp(prefix="tsn-rust-classify-")
        src = os.path.join(cls.tmp, "classify.rs")
        _write(src, classifier_source())
        cls.bin = os.path.join(cls.tmp, "classify")
        proc = subprocess.run([RUSTC, "--edition", "2021", "-O", "-o", cls.bin, src],
                              capture_output=True, text=True, timeout=600,
                              stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            raise AssertionError("the classifier harness did not build:\n" + proc.stderr)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def answer(self, syms):
        proc = subprocess.run([self.bin], input="".join(s + "\n" for s in syms),
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, "the classifier crashed: " + proc.stderr[-800:])
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), len(syms))
        return [tuple(line.split("\t")) for line in lines]

    def check(self, names):
        syms = [V0[n] for n in names] + ["_" + V0[n] for n in names]     # ELF and Mach-O
        for i, (sym, have) in enumerate(zip(syms, self.answer(syms))):
            with self.subTest(sym=sym):
                self.assertEqual(have, self.WANT[names[i % len(names)]])

    def test_every_v0_fixture_classifies_as_the_rulings_say(self):
        self.check(list(self.WANT))

    def test_a_crate_reached_through_a_back_reference_is_a_crate(self):
        self.check(["inherent_backref", "trait_impl_backref", "blanket_dsp", "closure"])

    def test_the_v0_boundary_is_the_decoded_identifier_in_the_test_crate(self):
        # R13/R17a: `28___rust_begin_short_backtrace` decodes past its `_`
        # separator; std's own copy of the name is no boundary.
        self.check(["begin_short_backtrace", "assert_test_result", "std_begin_short_backtrace",
                    "libtest_fn"])

    def test_an_impl_is_judged_by_its_self_type(self):
        # R14: the self type decides, never the impl's own path or the trait.
        self.check(["trait_impl_backref", "blanket_dsp", "drop_slow_crate",
                    "provided_crate_self", "provided_crate_trait", "provided_std"])

    def test_drop_glue_is_decided_by_the_first_crate_anywhere_in_t(self):
        # R17b: through Option/Vec, a tuple, an array and a dyn; a std-only
        # or libtest-only T stays transparent, and drop_slow is a boundary.
        self.check(["drop_vec", "drop_tuple", "drop_array", "drop_dyn", "drop_std",
                    "drop_std_linux", "drop_runner_type", "drop_slow_std"])

    def test_a_primitive_or_placeholder_names_no_crate(self):
        # R18: `<u8 as p::Blanket>` is transparent although the impl is in p.
        self.check(["blanket_u8", "blanket_slice", "blanket_placeholder"])

    def test_the_name_tables_match_decoded_identifiers(self):
        self.check(["crate_fn", "trait_impl_backref", "drop_control", "seed"])

    def test_the_name_tables_keep_legacy_scope(self):
        # Ruling R28: a std fn's generic argument named like the seed or a
        # control helper, a `Y` self type, a crate fn named like the seed.
        # R34: an M/X self type's CONCRETE generic argument, or `&` to the
        # helper, is not the control; the helper's own impls still are.
        self.check(["std_read_seed_type", "std_read_control_type", "provided_control_self",
                    "crate_fn_seed_name", "seed", "drop_control", "trait_impl_backref",
                    "mx_result_map", "mx_receiver", "mx_iter_x", "mx_ref_self",
                    "inherent_control", "blanket_x_control", "fnitem_x_control",
                    "drop_x_trait_backref", "drop_lookalike_x"])

    def test_a_deep_nested_path_reads_as_the_hook_reads_it(self):
        # Ruling R29: 1,200 `N` levels. The hook reads the chain iteratively;
        # rust_binary must too, and answer the same, never raise.
        have = self.answer([V0_DEEP, "_" + V0_DEEP])
        self.assertEqual(have, [("1", "0", "-", "=a")] * 2)
        self.assertEqual([mirror(V0_DEEP), mirror("_" + V0_DEEP)], have)

    def test_the_legacy_rules_are_unchanged(self):
        cases = [(_mangle("probe", "env_var"), ("1", "0", "-", "=probe")),
                 (_mangle("test", "test_main_static"), ("2", "0", "-", "=test")),
                 (_mangle("test", "__rust_begin_short_backtrace"), ("2", "1", "-", "=test")),
                 (_mangle("p", "tsn_control_temp_dir"), ("1", "0", "filesystem", "=p")),
                 (_mangle("std", "sys", "random", "hashmap_random_keys"),
                  ("3", "0", "-", "=std")),
                 ("_" + _mangle("std", "sys", "random", "hashmap_random_keys"),
                  ("3", "0", "-", "=std")),
                 # Ruling R28: only std's OWN path seeds -- not a crate fn or
                 # test merely named like it, not an impl segment's text.
                 (_mangle("v", "f_hashmap_random_keys_named_test"), ("1", "0", "-", "=v")),
                 (_mangle("p", "hashmap_random_keys"), ("1", "0", "-", "=p")),
                 (_mangle("std", "_$LT$impl$u20$hashmap_random_keys$GT$", "f"),
                  ("0", "0", "-", "=std")),
                 (_mangle("std", "fs", "read"), ("0", "0", "-", "=std")),
                 ("_" + _mangle("p", "Leaf"), ("1", "0", "-", "=p")),
                 ("_ZN3foo3barEv", ("0", "0", "-", "None")),
                 ("main", ("0", "0", "-", "None"))]
        got = self.answer([s for s, _ in cases])
        for (sym, want), have in zip(cases, got):
            with self.subTest(sym=sym):
                self.assertEqual(have, want)

    def test_malformed_truncated_and_too_deep_are_transparent(self):
        # Constraint 7: a malformed v0 symbol is transparent, never a crash;
        # past the parser's depth cap it is transparent too.
        bad = [s for n, s in V0_MALFORMED.items() if n not in ("legacy", "cxx")]
        for sym, have in zip(bad, self.answer(bad)):
            with self.subTest(sym=sym):
                self.assertEqual(have, ("0", "0", "-", "None"))
        self.assertEqual(self.answer([NEST(30)]), [("1", "0", "-", "=p")])

    def test_the_hook_and_rust_binary_agree_on_every_fixture_and_prefix(self):
        syms = []
        for sym in V0.values():
            syms += [sym[:cut] for cut in range(len(sym) + 1)]
            syms.append("_" + sym)
        syms += list(V0_MALFORMED.values()) + [NEST(n) for n in (1, 30, 60, 61, 62, 63, 64, 65,
                                                                 80)]
        syms += [V0_DEEP, "_" + V0_DEEP]
        for sym, have in zip(syms, self.answer(syms)):
            if sym.startswith(("_R", "__R")):
                self.assertEqual(have, mirror(sym), sym)

    def test_the_hook_and_rust_binary_agree_on_a_real_binary(self):
        # Every v0 symbol of a real test binary: the probe crate built v0 (on
        # 1.98 std and libtest are v0 too). rust_binary must parse EVERY one
        # -- a gap in its grammar would show here -- and the hook must answer
        # exactly what rust_binary's reading implies.
        if not CARGO:
            self.skipTest("no `cargo` on PATH")
        root = tempfile.mkdtemp(prefix="tsn-rust-classify-real-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        _, exe = build_probe(root, os.path.join(root, "target"),
                             rustflags="-C symbol-mangling-version=v0")
        syms = [s for s in rust_binary.symbol_names(exe) if s.startswith(("_R", "__R"))]
        self.assertGreater(len(syms), 200, "too few v0 symbols to mean anything")
        unread = [s for s in syms if rust_binary.v0_facts(s, _SKIP) is None]
        self.assertEqual(unread, [], "rust_binary cannot read these real symbols")
        disagree = [(s, have, mirror(s)) for s, have in zip(syms, self.answer(syms))
                    if have != mirror(s)]
        self.assertEqual(disagree, [])
        classes = {mirror(s)[0] for s in syms}
        self.assertLessEqual({"0", "1"}, classes)


# ── The wrapper: a fixture crate driven through `main` ───────────────────

GUARD = os.path.join(HERE, "io_guard_rust.py")
SKILL = os.path.dirname(HERE)
GIT = shutil.which("git")
RUSTUP = shutil.which("rustup")

FX_TOML = ('[package]\nname = "fx"\nversion = "0.1.0"\nedition = "2021"\n'
           # Ruling R22(c): three `harness = false` targets -- no libtest.
           + "".join('\n[[test]]\nname = "%s"\npath = "tests/%s.rs"\nharness = false\n' % (n, n)
                     for n in ("hf_forged", "hf_norun", "hf_honest")))
FX_LIB = r'''use std::time::{SystemTime, UNIX_EPOCH};

extern "C" { fn getentropy(buf: *mut u8, len: usize) -> i32; }

pub fn pure(n: u32) -> u32 { n * 2 }
pub fn read_hosts() -> usize { std::fs::read("/etc/hosts").map(|b| b.len()).unwrap_or(0) }
pub fn var(key: &str) -> Option<String> { std::env::var(key).ok() }
pub fn listen() -> bool { std::net::TcpListener::bind("127.0.0.1:0").is_ok() }
pub fn stamp() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}
pub fn roll() -> u8 { let mut b = [0u8; 1]; unsafe { getentropy(b.as_mut_ptr(), 1) }; b[0] }
pub fn run() -> bool { std::process::Command::new("true").status().is_ok() }
pub fn line() -> String { let mut s = String::new(); let _ = std::io::stdin().read_line(&mut s); s }
pub fn quit(code: i32) -> ! { std::process::exit(code) }
/// What a verdict-forging test prints: libtest's status and result lines,
/// each at the start of its own line.
pub fn forge(name: &str) {
    use std::io::Write;
    let text = format!("\ntest {} ... ok\n\ntest result: ok. 1 passed; 0 failed; 0 ignored; \
                        0 measured; 0 filtered out; finished in 0.00s\n\n", name);
    let mut o = std::io::stdout();
    let _ = o.write_all(text.as_bytes());
    let _ = o.flush();
}

#[allow(dead_code)]
mod private { pub fn hidden() -> u32 { 1 } }

// Ruling R22: the endings the re-review found.
#[cfg(target_os = "linux")] pub type Key = u32;
#[cfg(target_os = "macos")] pub type Key = u64;
extern "C" {
    fn close(fd: i32) -> i32;
    fn pthread_setspecific(k: Key, v: *const u8) -> i32;
    fn execv(p: *const i8, a: *const *const i8) -> i32;
    fn execl(p: *const i8, a0: *const i8, ...) -> i32;
    fn quick_exit(code: i32) -> !;
}
pub fn close_stderr() { unsafe { close(2); } }
pub fn set_all_keys() { for k in 0..512 { unsafe { pthread_setspecific(k as Key, 1 as *const u8); } } }
pub fn clear_all_keys() { for k in 0..512 { unsafe { pthread_setspecific(k as Key, std::ptr::null()); } } }
const TRUE: &[u8] = b"/usr/bin/true\0";
pub fn exec_v() { let a = [TRUE.as_ptr() as *const i8, std::ptr::null()]; unsafe { execv(TRUE.as_ptr() as *const i8, a.as_ptr()); } }
pub fn exec_l() { unsafe { execl(TRUE.as_ptr() as *const i8, TRUE.as_ptr() as *const i8, std::ptr::null::<i8>()); } }
pub fn quick(code: i32) -> ! { unsafe { quick_exit(code) } }
'''
# The main test file. It carries the control helpers verbatim -- so it
# DEFINES `tsn_control_set_env` without calling it, and has a commented-out
# call: neither may trip the one-environment-test-per-file rule.
FX_TESTS = "#![allow(dead_code)]\nuse fx::*;\n\n" + CONTROL_HELPERS_RS + r'''
#[test] fn t_clean() { assert_eq!(pure(2), 4); }
#[test] fn t_wrong() { assert_eq!(pure(2), 5); }
#[test] #[ignore] fn t_ignored() { assert_eq!(pure(2), 4); }
#[test] fn t_fs() { assert!(read_hosts() < usize::MAX); }
#[test] fn t_env() { let _ = var("HOME"); }
#[test] fn t_net() { let _ = listen(); }
#[test] fn t_clock() { let _ = stamp(); }
#[test] fn t_rand() { let _ = roll(); }
#[test] fn t_proc() { let _ = run(); }
#[test] fn t_stdin() { let _ = line(); }
#[test] fn t_abort() { std::process::abort(); }
#[test] fn t_tmp_control() {
    let d = tsn_control_temp_dir();
    std::fs::write(d.join("x"), b"x").unwrap();
}
// tsn_control_set_env("TSN_FX_VAR", "a comment, not a call");

// Ruling R19: the reviewer's forgery -- libtest's own lines printed by the
// test, then an exit before libtest can print the real ones.
#[test] fn t_forge() { forge("t_forge"); std::process::exit(0); }
#[test] fn t_exit1() { std::process::exit(1); }
#[test] fn t_crate_exit() { quit(0); }
// The same forgery ended by abort: no exit to intercept, a non-zero code.
#[test] fn t_forge_abort() { forge("t_forge_abort"); std::process::abort(); }
// Ruling R21: a forged handshake, printed after libtest's `running 1 test`.
#[test] fn t_forge_armed() {
    use std::io::Write;
    let _ = std::io::stderr().write_all(b"tsn-hook: armed\n");
    assert_eq!(pure(2), 4);
}
#[test] fn t_sleep_long() { std::thread::sleep(std::time::Duration::from_secs(60)); }

// Ruling R22: forged lines, then each ending the re-review found.
#[test] fn t_close2_exit() { forge("t_close2_exit"); close_stderr(); std::process::exit(0); }
#[test] fn t_keys_exit() { forge("t_keys_exit"); set_all_keys(); std::process::exit(0); }
#[test] fn t_keys_io() { set_all_keys(); let n = read_hosts(); clear_all_keys(); assert!(n > 0); }
#[test] fn t_cmd_exec() {
    use std::os::unix::process::CommandExt;
    forge("t_cmd_exec");
    let _ = std::process::Command::new("true").exec();
}
#[test] fn t_execv() { forge("t_execv"); exec_v(); }
#[test] fn t_execl() { forge("t_execl"); exec_l(); }
#[test] fn t_quick_exit() { forge("t_quick_exit"); quick(0); }
'''
HF_LINES = ("test t_hf ... ok\\n\\ntest result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; "
            "0 filtered out; finished in 0.00s\\n")
HF_FORGED = 'fn main() { println!("\\nrunning 1 test\\n%s"); }\n' % HF_LINES
HF_NORUN = 'fn main() { println!("%s"); }\n' % HF_LINES
HF_HONEST = "fn main() { assert_eq!(2 + 2, 4); }\n"
ENV_ONE = "#![allow(dead_code)]\n" + CONTROL_HELPERS_RS + r'''
#[test] fn t_env_alone() {
    let _g = tsn_control_set_env("TSN_FX_VAR", "v");
    assert_eq!(fx::var("TSN_FX_VAR").as_deref(), Some("v"));
}
'''
ENV_TWO = ENV_ONE + "#[test] fn t_sibling() { assert_eq!(fx::pure(1), 2); }\n"
PRIVATE_TESTS = "#[test] fn t_private() { assert_eq!(fx::private::hidden(), 1); }\n"
CFG_PROBE = ('#[test] fn t_cfg() { assert!(cfg!(tsn_probe_cfg), "built without the plan\'s cfg"); '
             '}\n')

# What a caller's environment must not leak into a fixture run.
_SCRUB = ("CARGO_TARGET_DIR", "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "LD_PRELOAD",
          "DYLD_INSERT_LIBRARIES", "RUST_TEST_NOCAPTURE", "RUSTUP_TOOLCHAIN")


def _cargo_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("TEST_SAFETY_NET", "TSN_")) and k not in _SCRUB}
    env["RUSTUP_AUTO_INSTALL"] = "0"
    env.update(extra)
    return env


def _git(crate, *args):
    return subprocess.run(["git", "-c", "user.name=tsn", "-c", "user.email=tsn@example.invalid",
                           "-c", "commit.gpgsign=false", *args], cwd=crate,
                          capture_output=True, text=True, check=True).stdout


def write_fx_crate(root):
    """The fixture crate, its committed `Cargo.lock` and (with git) one commit."""
    crate = os.path.join(root, "fx")
    for rel, text in (("Cargo.toml", FX_TOML), ("src/lib.rs", FX_LIB), ("tests/fx.rs", FX_TESTS),
                      ("tests/env_one.rs", ENV_ONE), ("tests/env_two.rs", ENV_TWO),
                      ("tests/private.rs", PRIVATE_TESTS), ("tests/cfg_probe.rs", CFG_PROBE),
                      ("tests/hf_forged.rs", HF_FORGED), ("tests/hf_norun.rs", HF_NORUN),
                      ("tests/hf_honest.rs", HF_HONEST),
                      (".gitignore", "target/\n")):
        _write(os.path.join(crate, rel), text)
    subprocess.run(["cargo", "generate-lockfile", "--offline"], cwd=crate, env=_cargo_env(),
                   capture_output=True, check=True)
    if GIT:
        _git(crate, "init", "-q")
        _git(crate, "add", "-A")
        _git(crate, "commit", "-qm", "fx")
    return crate


def copy_fx_source(crate, dest_root):
    """A fresh copy of the fixture's SOURCE (no target/, no .git) to mutate."""
    dest = os.path.join(dest_root, "fx")
    shutil.copytree(crate, dest, ignore=shutil.ignore_patterns("target", ".git"))
    return dest


_SHARED = {}


def shared_fx():
    """One fixture crate, hook cache and TMPDIR for every wrapper class."""
    if not _SHARED:
        tmp = tempfile.mkdtemp(prefix="tsn-rust-guard-")
        crate = write_fx_crate(tmp)
        with open(os.path.join(crate, "Cargo.lock"), "rb") as f:
            lock = f.read()
        scratch = os.path.join(tmp, "scratch")
        os.makedirs(scratch)
        _SHARED.update(tmp=tmp, crate=crate, lock=lock, cache=os.path.join(tmp, "cache"),
                       scratch=scratch)
    return _SHARED


def tearDownModule():
    if _SHARED:
        shutil.rmtree(_SHARED["tmp"], ignore_errors=True)


def _guard_env(**extra):
    fx = shared_fx()
    env = _cargo_env(TEST_SAFETY_NET_CACHE=fx["cache"], TMPDIR=fx["scratch"])
    env.pop("RUSTUP_AUTO_INSTALL")
    env.update(extra)
    return env


def run_guard(cwd, tier, allow, *args, **extra_env):
    """(exit code, combined output) for one wrapper run, as a subprocess."""
    env = _guard_env(**extra_env)
    if tier is not None:
        env["TEST_SAFETY_NET_TIER"] = str(tier)
    if allow is not None:
        env["TEST_SAFETY_NET_ALLOW"] = allow
    proc = subprocess.run([sys.executable, GUARD, *args], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=900, stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout + proc.stderr


def main_in(cwd, env, argv):
    """(exit code, combined output) for `main(argv)` IN THIS PROCESS -- the seam
    a test uses to stub one step (`build_test`, `hook_library`)."""
    out = io.StringIO()
    old = os.getcwd()
    os.chdir(cwd)
    try:
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            code = io_guard_rust.main(argv)
    finally:
        os.chdir(old)
    return code, out.getvalue()


_LABEL = re.compile(r"reached (\w+) I/O")


def label(out):
    m = _LABEL.search(out)
    return m.group(1) if m else None


class WrapperCase(unittest.TestCase):
    """Shares the fixture crate; skips visibly without cargo."""

    @classmethod
    def setUpClass(cls):
        if not CARGO or not RUSTC:
            raise unittest.SkipTest("no `cargo`/`rustc` on PATH: io_guard_rust.py's wrapper is "
                                    "NOT exercised on this machine.")
        if PLATFORM is None:
            raise unittest.SkipTest("the guard runs on darwin and linux only")
        cls.fx = shared_fx()
        cls.crate = cls.fx["crate"]

    def guard(self, tier, allow, test_name, stem="fx", cwd=None, **env):
        return run_guard(cwd or self.crate, tier, allow, "--test", stem, test_name, **env)

    def scratch_copy(self):
        root = tempfile.mkdtemp(prefix="tsn-rust-guard-copy-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return copy_fx_source(self.crate, root)


# ── 1. The documented command, extracted and run verbatim ────────────────

def extract_commands():
    """Every `TEST_SAFETY_NET_TIER=N ... python3 ... io_guard_rust.py ...` line the
    skill prints -- in the guard's own header and in every markdown file --
    with comment leaders stripped and backslash continuations joined (go's
    extractor, retargeted)."""
    sources = [GUARD]
    for base in (SKILL, os.path.join(SKILL, "references")):
        sources += [os.path.join(base, n) for n in sorted(os.listdir(base)) if n.endswith(".md")]
    out = []
    for path in sources:
        with open(path, encoding="utf-8") as f:
            lines = [re.sub(r"^\s*(?:#\s?|\*\s?)?", "", line.rstrip("\n")).strip()
                     for line in f]
        joined, buf = [], ""
        for line in lines:
            if line.endswith("\\"):
                buf += line[:-1].strip() + " "
            else:
                joined.append(buf + line)
                buf = ""
        for line in joined:
            if re.match(r"^TEST_SAFETY_NET_TIER=[12] .*python3 .*io_guard_rust\.py", line):
                out.append((os.path.relpath(path, SKILL), re.sub(r"\s+", " ", line)))
    return sorted(set(out))


class TestTheDocumentedCommand(WrapperCase):
    def test_the_header_documents_both_tiers(self):
        tiers = {re.match(r"TEST_SAFETY_NET_TIER=(\d)", cmd).group(1)
                 for where, cmd in extract_commands() if where == "assets/io_guard_rust.py"}
        self.assertEqual(tiers, {"1", "2"})

    def test_every_documented_command_passes_clean_and_trips_on_real_io(self):
        commands = extract_commands()
        self.assertTrue(commands, "no documented io_guard_rust.py invocation found")
        failures = []
        for where, command in commands:
            for test, want in (("t_clean", 0), ("t_env", 3)):
                cmd = (command.replace("<test_file_stem>", "fx").replace("<test_name>", test)
                       .replace("<package>", "fx"))
                proc = subprocess.run(["/bin/sh", "-c", cmd], cwd=self.crate,
                                      env=_guard_env(SKILL_DIR=SKILL), capture_output=True,
                                      text=True, timeout=900, stdin=subprocess.DEVNULL)
                if proc.returncode != want:
                    failures.append("%s: %s -> exit %d (want %d): %s"
                                    % (where, cmd, proc.returncode, want,
                                       (proc.stdout + proc.stderr).strip()[-400:]))
        self.assertEqual(failures, [])


# ── 2. Every group trips at tier 1, in its OWN group ─────────────────────

class TestEveryGroupTrips(WrapperCase):
    def test_a_clean_unit_passes_at_tier_1(self):
        code, out = self.guard(1, None, "t_clean")
        self.assertEqual(code, 0, out[-800:])
        self.assertRegex(out, r"(?m)^tsn-hook: armed$")
        self.assertIn("GREEN (exit 0)", out)

    def test_each_group_trips_and_is_named(self):
        for test, group in (("t_fs", "filesystem"), ("t_env", "environment"),
                            ("t_net", "network"), ("t_clock", "clock"),
                            ("t_rand", "randomness"), ("t_proc", "subprocess"),
                            ("t_stdin", "stdin")):
            with self.subTest(test=test):
                code, out = self.guard(1, None, test)
                self.assertEqual(code, 3, out[-800:])
                self.assertEqual(label(out), group, out[-800:])
                self.assertIn("GUARD TRIP (exit 3)", out)
                if test == "t_proc":
                    # Ruling R22(d): std's Command::spawn trips exactly as before.
                    self.assertIn("via posix_spawnp ", out)

    def test_the_trip_names_the_function_demangled(self):
        code, out = self.guard(1, None, "t_fs")
        self.assertEqual(code, 3, out[-800:])
        self.assertIn("from fx::read_hosts", out)


# ── 3. Tier 2: the controls and the allow list ───────────────────────────

class TestTierTwo(WrapperCase):
    def test_the_temp_dir_control_passes_with_filesystem_allowed(self):
        code, out = self.guard(2, "filesystem", "t_tmp_control")
        self.assertEqual(code, 0, out[-800:])

    def test_the_same_test_trips_filesystem_with_only_clock_named(self):
        code, out = self.guard(2, "clock", "t_tmp_control")
        self.assertEqual(code, 3, out[-800:])
        self.assertEqual(label(out), "filesystem")
        self.assertIn("names clock, which is not controllable on rust (std has no freeze hook)",
                      out)

    def test_randomness_cannot_be_allowed_on_rust(self):
        code, out = self.guard(2, "randomness", "t_rand")
        self.assertEqual(code, 3, out[-800:])
        self.assertEqual(label(out), "randomness")
        self.assertIn("rand::thread_rng cannot be seeded", out)

    def test_a_lone_environment_test_is_the_environment_control(self):
        code, out = self.guard(2, "environment", "t_env_alone", stem="env_one")
        self.assertEqual(code, 0, out[-800:])
        code, out = self.guard(2, "filesystem", "t_env_alone", stem="env_one")
        self.assertEqual(code, 3, out[-800:])
        self.assertEqual(label(out), "environment")


# ── 4. The exit contract ─────────────────────────────────────────────────

class TestExitContract(WrapperCase):
    def test_an_assertion_failure_is_1(self):
        code, out = self.guard(1, None, "t_wrong")
        self.assertEqual(code, 1, out[-800:])
        self.assertNotIn("IOGuardViolation", out)

    def test_a_callers_rust_test_nocapture_is_not_passed_on(self):
        # libtest reads RUST_TEST_NOCAPTURE as --nocapture, under which the
        # default panic hook's getenv(RUST_BACKTRACE) trips environment.
        code, out = self.guard(1, None, "t_wrong", RUST_TEST_NOCAPTURE="1")
        self.assertEqual(code, 1, out[-800:])
        self.assertNotIn("IOGuardViolation", out)

    def test_a_name_that_matches_no_test_is_4(self):
        code, out = self.guard(1, None, "t_no_such_test")
        self.assertEqual(code, 4, out[-800:])

    def test_an_ignored_test_is_4(self):
        # libtest exits 0 with `1 ignored`: the exit code alone would say GREEN.
        code, out = self.guard(1, None, "t_ignored")
        self.assertEqual(code, 4, out[-800:])

    def test_an_abort_with_no_result_line_is_1_with_a_note(self):
        code, out = self.guard(1, None, "t_abort")
        self.assertEqual(code, 1, out[-800:])
        self.assertIn("the test binary ended without a result line (signal or abort)", out)

    def test_a_private_item_is_no_build(self):
        # A compile error is cargo's 101 -- the same code as a failed
        # assertion. It must read NO BUILD, never RED.
        code, out = self.guard(1, None, "t_private", stem="private")
        self.assertEqual(code, 5, out[-800:])
        self.assertIn("E0603", out)

    def test_a_missing_cargo_lock_is_2_and_none_is_written(self):
        crate = self.scratch_copy()
        os.remove(os.path.join(crate, "Cargo.lock"))
        code, out = self.guard(1, None, "t_clean", cwd=crate)
        self.assertEqual(code, 2, out[-800:])
        self.assertIn("no Cargo.lock: run cargo generate-lockfile, then re-run; the proof never "
                      "writes one", out)
        self.assertFalse(os.path.exists(os.path.join(crate, "Cargo.lock")))

    def test_a_stale_cargo_lock_is_2_and_left_byte_identical(self):
        crate = self.scratch_copy()
        _write(os.path.join(crate, "depx", "Cargo.toml"), DEPX_TOML)
        _write(os.path.join(crate, "depx", "src", "lib.rs"), DEPX_RS)
        with open(os.path.join(crate, "Cargo.toml"), "a", encoding="utf-8") as f:
            f.write('\n[dependencies]\ndepx = { path = "depx" }\n')
        lock = os.path.join(crate, "Cargo.lock")
        with open(lock, "rb") as f:
            before = f.read()
        code, out = self.guard(1, None, "t_clean", cwd=crate)
        with open(lock, "rb") as f:
            self.assertEqual(f.read(), before, "the proof rewrote Cargo.lock")
        self.assertEqual(code, 2, out[-800:])
        self.assertIn("Cargo.lock is out of date for this manifest; update it yourself, then "
                      "re-run", out)

    def test_a_target_named_like_the_runner_or_std_is_refused(self):
        # Ruling R30: `tests/test.rs` compiles to crate `test`, read BY NAME
        # as libtest's own work -- its real read passed GREEN in both
        # manglings. Refused before anything builds or runs.
        crate = self.scratch_copy()
        _write(os.path.join(crate, "tests", "test.rs"),
               '#[test] fn t_io() { assert!(std::fs::read("/etc/hosts").is_ok()); }\n')
        code, out = self.guard(1, None, "t_io", stem="test", cwd=crate)
        self.assertEqual(code, 2, out[-800:])
        self.assertIn("`test`", out)
        self.assertIn("Rename it", out)
        self.assertNotIn("tsn-hook: armed", out)

    def test_a_path_dependency_named_backtrace_is_refused(self):
        # Ruling R30: a path-source crate named like a transparent crate is
        # read as std's own, so its I/O would pass. Seen in cargo's artifacts.
        crate = self.scratch_copy()
        _write(os.path.join(crate, "backtrace", "Cargo.toml"),
               '[package]\nname = "backtrace"\nversion = "0.1.0"\nedition = "2021"\n')
        _write(os.path.join(crate, "backtrace", "src", "lib.rs"),
               'pub fn read() -> usize { std::fs::read("/etc/hosts").map(|b| b.len())'
               '.unwrap_or(0) }\n')
        with open(os.path.join(crate, "Cargo.toml"), "a", encoding="utf-8") as f:
            f.write('\n[dependencies]\nbacktrace = { path = "backtrace" }\n')
        subprocess.run(["cargo", "generate-lockfile", "--offline"], cwd=crate, env=_cargo_env(),
                       capture_output=True, check=True)
        code, out = self.guard(1, None, "t_clean", cwd=crate)
        self.assertEqual(code, 2, out[-800:])
        self.assertIn("`backtrace`", out)
        self.assertIn("Rename it", out)
        self.assertNotIn("tsn-hook: armed", out)

    def test_a_dependency_missing_from_the_cargo_cache_is_2_and_nothing_is_fetched(self):
        # Ruling R24: the proof builds `--offline`, as go's builds GOPROXY=off,
        # so a crates.io dependency absent from the cargo cache is NOT ARMED --
        # never a download, and never NO BUILD. CARGO_HOME is an empty dir, so
        # no cache can hold `itoa`. MUTATION (run 2026-09-13): drop `--offline`
        # from build_test and this fails -- with network cargo downloads the
        # crate into CARGO_HOME and builds; without it cargo prints no
        # offline-mode note and the run reads 5.
        crate = self.scratch_copy()
        with open(os.path.join(crate, "Cargo.toml"), "a", encoding="utf-8") as f:
            f.write('\n[dependencies]\nitoa = "=1.0.11"\n')
        _write(os.path.join(crate, "Cargo.lock"),
               '# This file is automatically @generated by Cargo.\n'
               '# It is not intended for manual editing.\nversion = 3\n\n'
               '[[package]]\nname = "fx"\nversion = "0.1.0"\ndependencies = [\n "itoa",\n]\n\n'
               '[[package]]\nname = "itoa"\nversion = "1.0.11"\n'
               'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
               'checksum = "%s"\n' % ("0" * 64))
        home = tempfile.mkdtemp(prefix="tsn-rust-cargo-home-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        code, out = self.guard(1, None, "t_clean", cwd=crate, CARGO_HOME=home)
        fetched = [n for _d, _s, names in os.walk(home) for n in names if n.endswith(".crate")]
        self.assertEqual(fetched, [], "the proof downloaded a crate")
        self.assertEqual(code, 2, out[-800:])
        self.assertIn("a dependency is not in the local cargo cache; run `cargo fetch` (or build "
                      "the tests once), then re-run. The proof never downloads anything", out)

    def test_a_toolchain_pinned_below_the_floor_is_2(self):
        if not RUSTUP:
            self.skipTest("no rustup: a rust-toolchain.toml pin is not honoured here")
        crate = self.scratch_copy()
        _write(os.path.join(crate, "rust-toolchain.toml"), '[toolchain]\nchannel = "1.81"\n')
        code, out = self.guard(1, None, "t_clean", cwd=crate)
        self.assertEqual(code, 2, out[-800:])
        self.assertIn("1.81", out)
        self.assertRegex(out, r"is not installed|below this guard's floor")

    def test_a_rustc_below_the_floor_has_its_own_reason(self):
        bin_dir = tempfile.mkdtemp(prefix="tsn-fake-rustc-")
        self.addCleanup(shutil.rmtree, bin_dir, ignore_errors=True)
        fake = os.path.join(bin_dir, "rustc")
        _write(fake, '#!/bin/sh\necho "rustc 1.81.0 (eeb90cda1 2024-09-04)"\n')
        os.chmod(fake, 0o755)
        code, out = self.guard(1, None, "t_clean",
                               PATH=bin_dir + os.pathsep + os.environ.get("PATH", ""))
        self.assertEqual(code, 2, out[-800:])
        self.assertIn("rustc 1.81 is below this guard's floor, 1.82", out)
        self.assertNotIn("is not installed", out)

    def test_an_environment_controlled_test_with_a_sibling_is_2(self):
        code, out = self.guard(2, "environment", "t_env_alone", stem="env_two")
        self.assertEqual(code, 2, out[-800:])
        self.assertIn("an environment-controlled test must be alone in its file", out)

    def test_a_test_name_starting_with_a_dash_is_2(self):
        # It reached libtest as a flag, ran the whole binary, and could read GREEN.
        code, out = run_guard(self.crate, 1, None, "--test", "fx", "--", "--nocapture")
        self.assertEqual(code, 2, out[-800:])
        self.assertIn("a test name cannot begin with '-'", out)
        self.assertNotIn("tsn-hook: armed", out)

    def test_a_timeout_is_4(self):
        with mock.patch.object(io_guard_rust, "PROOF_TIMEOUT", 2):
            code, out = main_in(self.crate, _guard_env(TEST_SAFETY_NET_TIER="1"),
                                ["--test", "fx", "t_sleep_long"])
        self.assertEqual(code, 4, out)
        self.assertIn("killed after 2s: nothing was proved", out)
        self.assertNotIn("signal or abort", out)

    def test_an_unsupported_platform_is_2(self):
        with mock.patch.object(sys, "platform", "freebsd14"):
            code, out = main_in(self.crate, _guard_env(TEST_SAFETY_NET_TIER="1"),
                                ["--test", "fx", "t_clean"])
        self.assertEqual(code, 2, out)
        self.assertIn("freebsd14", out)


# ── 5. The build plan and the build ──────────────────────────────────────

def _lock_with(*names):
    text = "# This file is automatically @generated by Cargo.\nversion = 4\n"
    for n in names:
        text += '\n[[package]]\nname = "%s"\nversion = "0.38.44"\n' % n
    return text


class TestBuildPlan(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="tsn-rust-plan-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.cache = os.path.join(self.repo, "cache")

    def plan(self, platform, *names):
        _write(os.path.join(self.repo, "Cargo.lock"), _lock_with(*names))
        with mock.patch.dict(os.environ, {"TEST_SAFETY_NET_CACHE": self.cache}):
            return io_guard_rust.build_plan(self.repo, platform)

    def test_linux_with_rustix_builds_apart_with_the_libc_cfg(self):
        key = hashlib.sha256(os.path.realpath(self.repo).encode("utf-8")).hexdigest()
        self.assertEqual(self.plan("linux", "fx", "rustix"),
                         io_guard_rust.BuildPlan(os.path.join(self.cache, "rust-target", key),
                                                 ("--cfg=rustix_use_libc",),
                                                 "rustix's linux_raw backend bypasses libc"))

    def test_darwin_with_rustix_is_normal(self):
        self.assertEqual(self.plan("darwin", "fx", "rustix"), io_guard_rust.BuildPlan(None, (), ""))

    def test_linux_without_rustix_is_normal(self):
        self.assertEqual(self.plan("linux", "fx", "rustix-openpty"),
                         io_guard_rust.BuildPlan(None, (), ""))


class TestBuildTest(WrapperCase):
    def test_the_plans_target_dir_and_cfg_reach_the_build(self):
        target = tempfile.mkdtemp(prefix="tsn-rust-plan-target-")
        self.addCleanup(shutil.rmtree, target, ignore_errors=True)
        plan = io_guard_rust.BuildPlan(target, ("--cfg=tsn_probe_cfg",), "probe")
        # A caller's CARGO_ENCODED_RUSTFLAGS would outrank RUSTFLAGS: dropped.
        result = io_guard_rust.build_test(self.crate, plan, "cfg_probe", None,
                                          _cargo_env(CARGO_ENCODED_RUSTFLAGS=""))
        self.assertIsNone(result.compile_error, result.output)
        self.assertTrue(result.exe.startswith(os.path.join(os.path.realpath(target), ""))
                        or result.exe.startswith(os.path.join(target, "")), result.exe)
        proc = subprocess.run([result.exe, "t_cfg", "--exact"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_the_normal_plan_adds_no_flags(self):
        result = io_guard_rust.build_test(self.crate, io_guard_rust.BuildPlan(None, (), ""),
                                          "cfg_probe", None, _cargo_env())
        self.assertIsNone(result.compile_error, result.output)
        self.assertTrue(result.exe.startswith(os.path.join(os.path.realpath(self.crate),
                                                           "target", "")), result.exe)
        proc = subprocess.run([result.exe, "t_cfg", "--exact"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 101, proc.stdout)

    def test_a_compile_error_is_reported_not_raised(self):
        result = io_guard_rust.build_test(self.crate, io_guard_rust.BuildPlan(None, (), ""),
                                          "private", None, _cargo_env())
        self.assertIsNone(result.exe)
        self.assertIn("E0603", result.compile_error)


# ── 6. Refusals: binaries the hook would fail open on ────────────────────

class TestRefusals(WrapperCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        result = io_guard_rust.build_test(cls.crate, io_guard_rust.BuildPlan(None, (), ""), "fx",
                                          None, _cargo_env())
        if not result.exe:
            raise AssertionError("the fixture did not build: %s" % result.output)
        cls.exe = result.exe

    def seam(self, exe, test="t_clean"):
        stub = io_guard_rust.BuildResult(exe, None, "")
        with mock.patch.object(io_guard_rust, "build_test", return_value=stub):
            return main_in(self.crate, _guard_env(TEST_SAFETY_NET_TIER="1"),
                           ["--test", "fx", test])

    def test_a_stripped_test_binary_is_refused_with_2(self):
        if not shutil.which("strip"):
            self.skipTest("no `strip` on PATH")
        tmp = tempfile.mkdtemp(prefix="tsn-rust-strip-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        stripped = os.path.join(tmp, "fx-stripped")
        shutil.copy(self.exe, stripped)
        subprocess.run(["strip", stripped], check=True, capture_output=True)
        code, out = self.seam(stripped)
        self.assertEqual(code, 2, out)
        self.assertIn("stripped: no crate symbols, so no call can be attributed", out)
        self.assertNotIn("tsn-hook: armed", out, "a refused binary must never be run")

    def test_the_same_seam_runs_the_unstripped_binary(self):
        code, out = self.seam(self.exe)
        self.assertEqual(code, 0, out)


# ── 7. The handshake, classify and run_proof ─────────────────────────────

_CTOR = re.compile(r'#\[cfg\(target_os = "(?:macos|linux)"\)\]\n#\[used\]\n'
                   r'#\[link_section = "[^"]+"\]\nstatic INIT: extern "C" fn\(\) = tsn_init;\n')


class TestHandshake(WrapperCase):
    def test_a_hook_built_without_its_constructor_is_not_armed(self):
        source, n = _CTOR.subn("", io_guard_rust.render_hook())
        self.assertEqual(n, 2, "the constructor statics moved; update _CTOR")
        env = _guard_env(TEST_SAFETY_NET_TIER="1")
        with mock.patch.object(io_guard_rust, "render_hook", return_value=source):
            dead = io_guard_rust.hook_library(self.crate, env)
        with mock.patch.object(io_guard_rust, "hook_library", return_value=dead):
            code, out = main_in(self.crate, env, ["--test", "fx", "t_fs"])
        # Unarmed, the read passes and libtest says `1 passed`: only the
        # missing handshake stops that reading as GREEN.
        self.assertIn("1 passed", out)
        self.assertEqual(code, 2, out)
        self.assertIn("tsn-hook: armed", out)      # the reason names the missing line

    def test_the_real_hook_through_the_same_seam_trips(self):
        code, out = main_in(self.crate, _guard_env(TEST_SAFETY_NET_TIER="1"),
                            ["--test", "fx", "t_fs"])
        self.assertEqual(code, 3, out)


_OK = "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 11 filtered out\n"
_ARMED = "tsn-hook: armed\n"
_RUNNING = "\nrunning 1 test\n"
_STATUS = "test t_clean ... ok\n"


class TestClassify(unittest.TestCase):
    def c(self, code, *lines):
        return io_guard_rust.classify(code, "".join(lines), "t_clean")

    def test_a_violation_wins_over_everything(self):
        v = "test t ... \nIOGuardViolation: a tier 1 candidate reached clock I/O via x from y\n"
        self.assertEqual(self.c(3, _ARMED, v), 3)
        self.assertEqual(self.c(0, v, _OK), 3)                   # no handshake either
        self.assertEqual(self.c(2, _ARMED, "tsn-hook: cannot attribute (why)\n", v), 3)

    def test_the_hook_refusing_to_guess_is_not_armed(self):
        self.assertEqual(self.c(2, _ARMED, "\ntsn-hook: cannot attribute (why)\n", _OK), 2)
        self.assertEqual(self.c(2, _ARMED, "\ntsn-hook: libc has no statx\n"), 2)

    def test_no_handshake_is_not_armed(self):
        self.assertEqual(self.c(0, _OK), 2)
        self.assertEqual(self.c(0, "x" + _ARMED, _OK), 2)       # not a line of its own

    def test_the_result_line_decides(self):
        self.assertEqual(self.c(0, _ARMED, _RUNNING, _STATUS, _OK), 0)
        self.assertEqual(self.c(101, _ARMED, _RUNNING, "test result: FAILED. 0 passed; 1 failed; "
                                                       "0 ignored; 0 measured; 0 filtered out\n"), 1)
        self.assertEqual(self.c(0, _ARMED, "test result: ok. 0 passed; 0 failed; 1 ignored; "
                                           "0 measured; 0 filtered out\n"), 4)
        self.assertEqual(self.c(0, _ARMED, "test result: ok. 0 passed; 0 failed; 0 ignored; "
                                           "0 measured; 12 filtered out\n"), 4)
        self.assertEqual(self.c(0, _ARMED, "test result: ok. 2 passed; 0 failed; 0 ignored; "
                                           "0 measured; 0 filtered out\n"), 4)

    def test_the_last_result_line_is_libtests(self):
        spoof = "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n"
        self.assertEqual(self.c(101, _ARMED, _RUNNING, spoof,
                                "test result: FAILED. 0 passed; 1 failed; "
                                "0 ignored; 0 measured; 0 filtered out\n"), 1)

    def test_no_result_line(self):
        self.assertEqual(self.c(-6, _ARMED, _RUNNING, "test t_abort ... "), 1)
        self.assertEqual(self.c(0, _ARMED), 4)

    def test_the_reserved_status_is_no_test_with_or_without_the_line(self):
        # Ruling R22(a): fd 2 closed, so no early-exit line -- the status says it.
        self.assertEqual(self.c(125, _ARMED, _RUNNING), 4)
        self.assertEqual(self.c(125, _ARMED, _RUNNING, _STATUS, _OK), 4)

    def test_no_libtest_run_is_no_test(self):
        # Ruling R22(c): a `harness = false` binary printing libtest's lines.
        self.assertEqual(self.c(0, _ARMED, _STATUS, _OK), 4)
        self.assertEqual(self.c(101, _ARMED), 4)

    def test_an_early_exit_is_no_test(self):
        # Ruling R19: whatever follows -- a forged pass included.
        early = "\ntsn-hook: early exit (_ZN2fx4quit)\n"
        self.assertEqual(self.c(0, _ARMED, _RUNNING, _STATUS, early, _OK), 4)
        self.assertEqual(self.c(1, _ARMED, _RUNNING, early), 4)

    def test_green_needs_libtests_own_status_line(self):
        self.assertEqual(self.c(0, _ARMED, _RUNNING, _OK), 4)
        self.assertEqual(self.c(0, _ARMED, _RUNNING, "test t_other ... ok\n", _OK), 4)
        self.assertEqual(self.c(0, _ARMED, _RUNNING, "x test t_clean ... ok\n", _OK), 4)
        self.assertEqual(self.c(0, _ARMED, _RUNNING, _OK, _STATUS), 4)     # after the result
        self.assertEqual(self.c(0, _ARMED, _RUNNING,
                                "test t_clean - should panic ... ok\n", _OK), 0)
        # A test's raw stderr lands between libtest's two halves: still libtest's.
        self.assertEqual(self.c(0, _ARMED, _RUNNING, "test t_clean ... tsn-hook: armed\nok\n",
                                _OK), 0)
        self.assertEqual(self.c(0, _ARMED, _RUNNING, "test t_clean ... \n", _OK), 4)

    def test_a_pass_needs_exit_code_0(self):
        self.assertEqual(self.c(-6, _ARMED, _RUNNING, _STATUS, _OK), 4)
        self.assertEqual(self.c(0, _ARMED, _RUNNING, _STATUS, _OK), 0)

    def test_a_preload_the_loader_skipped_is_not_armed(self):
        # Ruling R21: glibc's and dyld's own words for a preload not loaded.
        ld = ("ERROR: ld.so: object '/c/libtsn_hook.so' from LD_PRELOAD cannot be preloaded "
              "(cannot open shared object file): ignored.\n")
        dyld = ("dyld[1]: terminating because inserted dylib '/c/libtsn_hook.dylib' could not "
                "be loaded: tried: '/c/libtsn_hook.dylib' (no such file)\n")
        for bad in (ld, dyld):
            self.assertEqual(self.c(0, bad, _ARMED, _RUNNING, _STATUS, _OK), 2)

    def test_a_handshake_after_libtest_started_is_not_armed(self):
        # The constructor writes before main; a line after `running N test`
        # was printed by the test.
        self.assertEqual(self.c(0, _RUNNING, _ARMED, _STATUS, _OK), 2)
        self.assertEqual(self.c(0, _ARMED, _RUNNING, _ARMED, _STATUS, _OK), 0)

    def test_a_timeout_is_no_test(self):
        self.assertEqual(self.c(-9, _ARMED, _RUNNING, "test t ... ",
                                "\ntsn-proof: killed after 300s: nothing was proved\n"), 4)


def _mangle(*segments):
    """A legacy-mangled symbol, built here so no literal hash sits in the file."""
    body = "".join("%d%s" % (len(s), s) for s in segments + ("h" + "0123456789abcdef",))
    return "_ZN" + body + "E"


class TestDemangle(unittest.TestCase):
    def test_a_plain_path(self):
        self.assertEqual(io_guard_rust.demangle(_mangle("fx", "read_hosts")), "fx::read_hosts")

    def test_an_impl_frame_and_its_escapes(self):
        sym = _mangle("_$LT$p..Dsp$u20$as$u20$core..fmt..Display$GT$", "fmt")
        self.assertEqual(io_guard_rust.demangle(sym), "<p::Dsp as core::fmt::Display>::fmt")
        sym = _mangle("p", "t_thread", "_$u7b$$u7b$closure$u7d$$u7d$")
        self.assertEqual(io_guard_rust.demangle(sym), "p::t_thread::{{closure}}")
        sym = _mangle("core", "ptr", "drop_in_place$LT$$LP$u32$C$$RF$mut$u20$p..Pd$RP$$GT$")
        self.assertEqual(io_guard_rust.demangle(sym),
                         "core::ptr::drop_in_place<(u32,&mut p::Pd)>")

    def test_the_macho_underscore(self):
        self.assertEqual(io_guard_rust.demangle("_" + _mangle("fx", "var")), "fx::var")

    def test_anything_else_is_returned_unchanged(self):
        for s in ("(test body, inlined)", "(a thread with no crate frame)", "_ZN3fx", "main",
                  "_RNv", "__R"):
            self.assertEqual(io_guard_rust.demangle(s), s)

    def test_a_v0_symbol_is_demangled_too(self):
        # Ruling R26: on rustc 1.98 the trip label is a v0 name.
        self.assertEqual(io_guard_rust.demangle(V0["crate_fn"]), "p::tsn_control_temp_dir")
        self.assertEqual(io_guard_rust.demangle("_" + V0["trait_impl_backref"]),
                         "<p::TsnControlEnv as core::ops::drop::Drop>::drop")


class TestLockedWording(unittest.TestCase):
    """Ruling R27: cargo's stale-lock refusal in every proven cargo's words.

    Both wordings ALSO name `--offline` (their help line), so the lock is
    checked BEFORE ruling R24's offline match. MUTATION: matching the offline
    note first, or 1.92's "needs to be updated" phrase alone, sends a
    1.94/1.98 stale lock to the not-cached remedy -- killed here."""

    # Measured: cargo 1.92.0 on darwin; rust:1.94 and rust:1.98 (identical).
    LOCK_192 = ("error: the lock file /w/fx/Cargo.lock needs to be updated but --locked was "
                "passed to prevent this\nIf you want to try to generate the lock file without "
                "accessing the network, remove the --locked flag and use --offline instead.\n")
    LOCK_194 = ("error: cannot update the lock file /w/fx/Cargo.lock because --locked was passed "
                "to prevent this\nhelp: to generate the lock file without accessing the "
                "network, remove the --locked flag and use --offline instead.\n")
    OFFLINE = ("error: no matching package named `itoa` found\nlocation searched: `crates-io` "
               "index\nrequired by package `fx v0.1.0 (/w/fx)`\nAs a reminder, you're using "
               "offline mode (--offline) which can sometimes cause surprising resolution "
               "failures, if this error is too confusing you may wish to retry without "
               "`--offline`.\n")

    def build(self, stderr):
        done = subprocess.CompletedProcess([], 101, stdout="", stderr=stderr)
        with mock.patch.object(io_guard_rust.shutil, "which", return_value="/usr/bin/cargo"), \
                mock.patch.object(io_guard_rust.subprocess, "run", return_value=done):
            return io_guard_rust.build_test("/w/fx", io_guard_rust.BuildPlan(None, (), ""), "fx",
                                            None, {"PATH": "/usr/bin"})

    def test_a_stale_lock_is_its_own_refusal_in_every_cargos_wording(self):
        for cargo, stderr in (("1.92", self.LOCK_192), ("1.94/1.98", self.LOCK_194)):
            with self.subTest(cargo=cargo):
                with self.assertRaises(io_guard_rust.GuardCannotArm) as ctx:
                    self.build(stderr)
                self.assertIn("Cargo.lock is out of date for this manifest; update it yourself, "
                              "then re-run", str(ctx.exception))
                self.assertNotIn("cargo fetch", str(ctx.exception))

    def test_a_dependency_missing_offline_is_still_the_cache_refusal(self):
        with self.assertRaises(io_guard_rust.GuardCannotArm) as ctx:
            self.build(self.OFFLINE)
        self.assertIn("run `cargo fetch`", str(ctx.exception))
        self.assertNotIn("Cargo.lock is out of date", str(ctx.exception))

    # A build script's own stderr as cargo relays it: indented under
    # `--- stderr`. Its words are not cargo's (ruling R32).
    BUILD_SCRIPT = ("error: failed to run custom build command for `fx v0.1.0 (/w/fx)`\n\n"
                    "Caused by:\n  process didn't exit successfully: `/w/t/debug/build/fx-1/"
                    "build-script-build` (exit status: 1)\n  --- stderr\n  error: cannot update "
                    "the lock file /w/x/Cargo.lock because --locked was passed to prevent this\n")

    def test_a_build_scripts_own_words_are_not_cargos(self):
        # Ruling R32. MUTATION: matching `--locked was passed` anywhere in
        # stderr reads this build failure as a stale lock -- killed here.
        try:
            result = self.build(self.BUILD_SCRIPT)
        except io_guard_rust.GuardCannotArm as exc:
            self.fail("a build script's output was read as cargo's own: %s" % exc)
        self.assertIsNotNone(result.compile_error)


class TestReservedTargets(unittest.TestCase):
    """Ruling R30 without a toolchain: the guard's classifier keys on crate
    names, so a crate named like std's or libtest's own is refused, never
    guarded."""

    def test_a_test_stem_named_like_a_std_or_runner_crate_is_refused(self):
        # Before rustc is even asked: PATH holds none here.
        if PLATFORM is None:
            self.skipTest("the guard runs on darwin and linux only")
        tmp = tempfile.mkdtemp(prefix="tsn-rust-reserved-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        env = {"PATH": os.path.join(tmp, "no-bin"), "HOME": tmp, "TEST_SAFETY_NET_TIER": "1"}
        for stem in ("test", "std", "core", "alloc", "hashbrown", "std-detect", "panic_unwind"):
            with self.subTest(stem=stem):
                code, out = main_in(tmp, env, ["--test", stem, "t_io"])
                self.assertEqual(code, 2, out)
                self.assertIn("Rename it", out)
        code, out = main_in(tmp, env, ["--test", "test_io", "t_io"])
        self.assertEqual(code, 2, out)                   # refused later: no rustc on PATH
        self.assertNotIn("Rename it", out)

    FX = ("path+file:///w/fx#0.1.0", "fx", ["test"], "/w/target/debug/deps/fx-1")

    def build(self, *artifacts):
        stdout = "".join(json.dumps({"reason": "compiler-artifact", "package_id": pid,
                                     "target": {"name": name, "kind": kind},
                                     "executable": exe}) + "\n"
                         for pid, name, kind, exe in artifacts)
        done = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
        with mock.patch.object(io_guard_rust.shutil, "which", return_value="/usr/bin/cargo"), \
                mock.patch.object(io_guard_rust.subprocess, "run", return_value=done):
            return io_guard_rust.build_test("/w/fx", io_guard_rust.BuildPlan(None, (), ""), "fx",
                                            None, {"PATH": "/usr/bin"})

    def test_a_path_crate_named_like_std_or_the_runner_is_refused(self):
        # cargo 1.77+ and older package-id spellings of a path package.
        for pid in ("path+file:///w/fx/backtrace#0.1.0",
                    "backtrace 0.1.0 (path+file:///w/fx/backtrace)"):
            with self.subTest(package_id=pid):
                with self.assertRaises(io_guard_rust.GuardCannotArm) as ctx:
                    self.build((pid, "backtrace", ["lib"], None), self.FX)
                self.assertIn("`backtrace`", str(ctx.exception))
                self.assertIn("Rename it", str(ctx.exception))

    def test_a_registry_crate_so_named_is_the_stated_residual(self):
        # Residual 3: a crates.io `hashbrown` is not the user's to rename.
        result = self.build(("registry+https://github.com/rust-lang/crates.io-index"
                             "#hashbrown@0.15.2", "hashbrown", ["lib"], None), self.FX)
        self.assertEqual(result.exe, "/w/target/debug/deps/fx-1")

    def test_a_bin_example_or_bench_so_named_is_not_refused(self):
        # Ruling R35: only a crate LINKED into the test binary can be misread
        # by name. A [[bin]], example or bench named `backtrace` never is.
        # MUTATION: counting every path-source target -- killed here.
        for kind in (["bin"], ["example"], ["bench"]):
            with self.subTest(kind=kind):
                result = self.build(("path+file:///w/fx#0.1.0", "backtrace", kind,
                                     "/w/target/debug/backtrace"), self.FX)
                self.assertEqual(result.exe, "/w/target/debug/deps/fx-1")
        for kind in (["lib"], ["rlib"], ["proc-macro"], ["cdylib"], ["test"]):
            with self.subTest(kind=kind):
                with self.assertRaises(io_guard_rust.GuardCannotArm):
                    self.build(("path+file:///w/fx#0.1.0", "backtrace", kind, None), self.FX)


class _FakeProc:
    calls = []

    def __init__(self, argv, **kw):
        type(self).calls.append((list(argv), kw))
        self.stdout = iter([_ARMED, _OK])
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class TestRunProof(unittest.TestCase):
    def run_proof(self, tier, allow, env):
        _FakeProc.calls = []
        with mock.patch.object(io_guard_rust.subprocess, "Popen", _FakeProc), \
                contextlib.redirect_stdout(io.StringIO()):
            code, out = io_guard_rust.run_proof("/x/fx-test", "t_clean", tier, allow,
                                                "/c/libtsn_hook.so", env)
        self.assertEqual((code, out), (0, _ARMED + _OK))
        self.assertEqual(len(_FakeProc.calls), 1)
        return _FakeProc.calls[0]

    def test_the_run_line_is_exact_and_never_nocapture(self):
        argv, kw = self.run_proof(1, None, {"PATH": "/bin"})
        self.assertEqual(argv, ["/x/fx-test", "t_clean", "--exact", "--test-threads=1"])
        self.assertNotIn("--nocapture", argv)
        self.assertEqual(kw.get("stdin"), subprocess.DEVNULL)

    def test_tier_1_blocks_everything_and_reads_stdin(self):
        argv, kw = self.run_proof(1, None, {"PATH": "/bin", "RUST_TEST_NOCAPTURE": "1",
                                            "LD_PRELOAD": "/evil.so",
                                            "DYLD_INSERT_LIBRARIES": "/evil.dylib"})
        env = kw["env"]
        self.assertEqual(env["TSN_BLOCKED"], ",".join(sorted(stack_rust.GROUPS)))
        self.assertEqual(env["TSN_TIER"], "1")
        self.assertEqual(env["TSN_STDIN"], "1")
        self.assertEqual(env[PRELOAD], "/c/libtsn_hook.so")
        self.assertNotIn("RUST_TEST_NOCAPTURE", env)
        other = {"darwin": "LD_PRELOAD", "linux": "DYLD_INSERT_LIBRARIES"}[PLATFORM]
        self.assertNotIn(other, env)

    def test_tier_2_blocks_what_is_not_allowed_and_leaves_stdin(self):
        argv, kw = self.run_proof(2, ["filesystem"], {"PATH": "/bin", "TSN_STDIN": "1",
                                                      "TSN_BLOCKED": ""})
        env = kw["env"]
        self.assertEqual(env["TSN_BLOCKED"],
                         ",".join(sorted(set(stack_rust.GROUPS) - {"filesystem"})))
        self.assertEqual(env["TSN_TIER"], "2")
        self.assertNotIn("TSN_STDIN", env)


# ── 8. The two layers agree; the group tables are the filter's ───────────

class TestTheTwoLayersAgree(unittest.TestCase):
    def test_every_filter_marker_is_accounted_for_exactly_once(self):
        markers = {m for table in (stack_rust.CONTROLLABLE, stack_rust.UNCONTROLLABLE)
                   for ms in table.values() for m in ms}
        maps = (io_guard_rust.FILTER_MARKER_INTERCEPTS, io_guard_rust.PARTIALLY_INTERCEPTED,
                io_guard_rust.NOT_INTERCEPTED)
        problems = []
        for m in sorted(markers):
            homes = [i for i, table in enumerate(maps) if m in table]
            if len(homes) != 1:
                problems.append("%s in %d tables" % (m, len(homes)))
                continue
            reason = maps[homes[0]][m]
            # FILTER_MARKER_INTERCEPTS' reason is the tuple of libc calls that
            # catch the marker; the other two carry a sentence.
            parts = reason if homes[0] == 0 else (reason,)
            if not parts or not all(isinstance(p, str) and p.strip() for p in parts):
                problems.append("%s has no reason" % m)
        extra = set().union(*maps) - markers
        self.assertEqual(problems, [])
        self.assertEqual(extra, set(), "partition rows for markers the filter does not have")

    def test_an_intercepted_marker_names_calls_the_hook_has_on_both_platforms(self):
        # FULLY intercepted means caught on darwin AND linux; a marker caught
        # on one belongs in PARTIALLY_INTERCEPTED with that reason.
        problems = []
        for m, names in sorted(io_guard_rust.FILTER_MARKER_INTERCEPTS.items()):
            self.assertIsInstance(names, tuple, m)
            everywhere = set()
            for plat, table in io_guard_rust.INTERCEPTS.items():
                hooked = {n for ns in table.values() for n in ns}
                everywhere |= hooked
                if not set(names) & hooked:
                    problems.append("%s: nothing in %s is hooked on %s" % (m, names, plat))
            if set(names) - everywhere:
                problems.append("%s names unhooked %s" % (m, sorted(set(names) - everywhere)))
        self.assertEqual(problems, [])

    def test_the_measured_residuals_are_where_the_measurement_put_them(self):
        for m in ("std::env::vars", "std::env::vars_os", "std::env::args", "std::env::args_os",
                  "std::thread::sleep"):
            self.assertIn(m, io_guard_rust.NOT_INTERCEPTED)
        self.assertIn("environ", io_guard_rust.NOT_INTERCEPTED["std::env::vars"])
        self.assertIn("nanosleep", io_guard_rust.NOT_INTERCEPTED["std::thread::sleep"])
        for m in ("std::fs", "std::env::current_exe", "rusqlite", "rand::thread_rng"):
            self.assertIn(m, io_guard_rust.PARTIALLY_INTERCEPTED)
        self.assertEqual(io_guard_rust.FILTER_MARKER_INTERCEPTS["std::env::set_current_dir"],
                         ("chdir",))
        self.assertEqual(io_guard_rust.FILTER_MARKER_INTERCEPTS["std::env::current_dir"],
                         ("getcwd",))


class TestGroupTables(unittest.TestCase):
    def test_the_guard_uses_the_filters_groups_verbatim(self):
        self.assertEqual(io_guard_rust.GROUPS, stack_rust.GROUPS)
        self.assertEqual(io_guard_rust.CONTROLLABLE_GROUPS, ("environment", "filesystem"))
        self.assertEqual(io_guard_rust.UNCONTROLLABLE_GROUPS,
                         tuple(sorted(stack_rust.UNCONTROLLABLE)))

    def test_the_uncontrollable_reasons(self):
        why = io_guard_rust._why
        self.assertEqual(why("clock"), "std has no freeze hook")
        self.assertEqual(why("randomness"), "std has no RNG and rand::thread_rng cannot be seeded")
        self.assertEqual(why("network"), "no test can control it")


# ── 10. Forged verdicts (R19), the workspace (R20), the preload (R21) ────

class TestForgedVerdicts(WrapperCase):
    def test_a_forged_verdict_then_exit_0_is_4(self):
        # The reviewer's probe: libtest's status and result lines printed by
        # the test, then exit(0) before libtest prints its own. Without the
        # hook's `exit` intercept this read GREEN.
        code, out = self.guard(1, None, "t_forge")
        self.assertEqual(code, 4, out[-800:])
        self.assertRegex(out, r"(?m)^tsn-hook: early exit \(.+t_forge.*\)$")
        self.assertIn("the test process exited before libtest reported; nothing was proved", out)

    def test_exit_1_is_4(self):
        code, out = self.guard(1, None, "t_exit1")
        self.assertEqual(code, 4, out[-800:])

    def test_an_exit_in_a_crate_function_is_4_and_names_it(self):
        code, out = self.guard(1, None, "t_crate_exit")
        self.assertEqual(code, 4, out[-800:])
        self.assertRegex(out, r"(?m)^tsn-hook: early exit \(.*fx.*quit.*\)$")

    def test_a_normal_pass_and_a_normal_failure_keep_their_verdicts(self):
        self.assertEqual(self.guard(1, None, "t_clean")[0], 0)
        self.assertEqual(self.guard(1, None, "t_wrong")[0], 1)

    def test_a_forged_verdict_then_abort_is_not_green(self):
        # No exit to intercept: the forged lines are the last ones, but the
        # process died on SIGABRT, and a pass needs exit code 0.
        code, out = self.guard(1, None, "t_forge_abort")
        self.assertEqual(code, 4, out[-800:])


WS_TOML = '[workspace]\nmembers = ["a", "b"]\nresolver = "2"\n'
MEMBER_LIB = r'''pub fn one() -> u32 { 1 }
pub fn pure(n: u32) -> u32 { n * 2 }
pub fn var(key: &str) -> Option<String> { std::env::var(key).ok() }
'''


class TestWorkspace(WrapperCase):
    # Two members that each hold `tests/same.rs`: a's is one pure test, b's
    # an environment-controlled test with a sibling (ruling R20).

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ws = os.path.join(tempfile.mkdtemp(prefix="tsn-rust-ws-"), "ws")
        _write(os.path.join(cls.ws, "Cargo.toml"), WS_TOML)
        for m in ("a", "b"):
            _write(os.path.join(cls.ws, m, "Cargo.toml"),
                   '[package]\nname = "%s"\nversion = "0.1.0"\nedition = "2021"\n' % m)
            _write(os.path.join(cls.ws, m, "src", "lib.rs"), MEMBER_LIB)
        _write(os.path.join(cls.ws, "a", "tests", "same.rs"),
               "#[test] fn t_same() { assert_eq!(a::one(), 1); }\n")
        _write(os.path.join(cls.ws, "b", "tests", "same.rs"), ENV_TWO.replace("fx::", "b::"))
        subprocess.run(["cargo", "generate-lockfile", "--offline"], cwd=cls.ws, env=_cargo_env(),
                       capture_output=True, check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(os.path.dirname(cls.ws), ignore_errors=True)

    def test_the_root_without_p_is_2(self):
        code, out = run_guard(self.ws, 1, None, "--test", "same", "t_same")
        self.assertEqual(code, 2, out[-800:])
        self.assertIn("more than one package has tests/same.rs; name the package with -p", out)

    def test_the_root_with_p_a_runs_as(self):
        code, out = run_guard(self.ws, 1, None, "-p", "a", "--test", "same", "t_same")
        self.assertEqual(code, 0, out[-800:])

    def test_the_root_with_p_b_reads_bs_file(self):
        code, out = run_guard(self.ws, 2, "environment", "-p", "b", "--test", "same",
                              "t_env_alone")
        self.assertEqual(code, 2, out[-800:])
        self.assertIn("an environment-controlled test must be alone in its file", out)
        self.assertIn(os.path.join("b", "tests", "same.rs"), out)

    def test_the_member_directory_runs_its_own(self):
        code, out = run_guard(os.path.join(self.ws, "a"), 1, None, "--test", "same", "t_same")
        self.assertEqual(code, 0, out[-800:])

    def test_build_test_refuses_two_executables_for_one_target(self):
        # The belt behind the pre-build check: cargo itself built both.
        with self.assertRaises(io_guard_rust.GuardCannotArm) as ctx:
            io_guard_rust.build_test(self.ws, io_guard_rust.BuildPlan(None, (), ""), "same",
                                     None, _cargo_env())
        self.assertIn("more than one package has tests/same.rs; name the package with -p",
                      str(ctx.exception))


class TestUnloadedHook(WrapperCase):
    def test_a_missing_hook_and_a_forged_handshake_is_not_armed(self):
        # Ruling R21: glibc skips a preload it cannot open and carries on,
        # dyld terminates; either way the test's own `tsn-hook: armed` line
        # must not arm anything.
        missing = os.path.join(self.fx["tmp"], "no-such-dir", "libtsn_hook.so")
        with mock.patch.object(io_guard_rust, "hook_library", return_value=missing):
            code, out = main_in(self.crate, _guard_env(TEST_SAFETY_NET_TIER="1"),
                                ["--test", "fx", "t_forge_armed"])
        self.assertEqual(code, 2, out)
        if PLATFORM == "linux":
            self.assertIn("cannot be preloaded", out)

    def test_the_real_hook_with_a_forged_second_handshake_passes(self):
        code, out = self.guard(1, None, "t_forge_armed")
        self.assertEqual(code, 0, out[-800:])


class TestHostileEndings(WrapperCase):
    # Ruling R22: forged status and result lines first, then each ending the
    # re-review found reading GREEN.

    def test_closing_stderr_then_exit_is_4_by_the_reserved_status(self):
        code, out = self.guard(1, None, "t_close2_exit")
        self.assertEqual(code, 4, out[-800:])
        self.assertIn("exit status 125, the hook's early-exit status", out)
        self.assertNotIn("tsn-hook: early exit", out)       # fd 2 was closed

    def test_filling_every_pthread_key_then_exit_is_4(self):
        code, out = self.guard(1, None, "t_keys_exit")
        self.assertEqual(code, 4, out[-800:])

    def test_filling_every_pthread_key_then_reading_a_file_trips(self):
        code, out = self.guard(1, None, "t_keys_io")
        self.assertEqual(code, 3, out[-800:])
        self.assertEqual(label(out), "filesystem")

    def test_command_exec_trips_subprocess_at_its_own_entry(self):
        code, out = self.guard(1, None, "t_cmd_exec")
        self.assertEqual(code, 3, out[-800:])
        self.assertEqual(label(out), "subprocess")
        self.assertIn("via execvp", out)

    def test_execv_and_execl_trip_subprocess_at_their_own_entries(self):
        for test, call in (("t_execv", "execv"), ("t_execl", "execl")):
            with self.subTest(test=test):
                code, out = self.guard(1, None, test)
                self.assertEqual(code, 3, out[-800:])
                self.assertIn("reached subprocess I/O via %s " % call, out)

    def test_quick_exit_is_4(self):
        code, out = self.guard(1, None, "t_quick_exit")
        self.assertEqual(code, 4, out[-800:])
        # The innermost crate frame is the lib fn that called quick_exit.
        self.assertRegex(out, r"(?m)^tsn-hook: early exit \(.*fx.*quick.*\)$")

    def test_a_harness_false_target_is_4_forged_or_honest(self):
        for stem in ("hf_forged", "hf_norun", "hf_honest"):
            with self.subTest(stem=stem):
                code, out = self.guard(1, None, "t_hf", stem=stem)
                self.assertEqual(code, 4, out[-800:])
                self.assertIn("does not link libtest's harness", out)

    def test_libtest_targets_link_the_harness(self):
        # The positive control for the check above.
        result = io_guard_rust.build_test(self.crate, io_guard_rust.BuildPlan(None, (), ""), "fx",
                                          None, _cargo_env())
        self.assertTrue(io_guard_rust._links_libtest(result.exe), result.exe)


# ── 9. Nothing is written ────────────────────────────────────────────────

class TestNothingWritten(WrapperCase):
    def test_a_green_run_leaves_only_the_ignored_target_dir(self):
        if not GIT:
            self.skipTest("no git: the fixture is not a repository here")
        code, out = self.guard(1, None, "t_clean")
        self.assertEqual(code, 0, out[-800:])
        self.assertEqual(_git(self.crate, "status", "--porcelain"), "")
        self.assertEqual(_git(self.crate, "status", "--porcelain", "--ignored"), "!! target/\n")
        with open(os.path.join(self.crate, "Cargo.lock"), "rb") as f:
            self.assertEqual(f.read(), self.fx["lock"])

    def test_a_crashing_test_dumps_no_core_into_the_repo(self):
        # Measured in the rust images (ulimit -c unlimited, core_pattern
        # `core`): an aborting test wrote `<crate>/core`. On darwin cores go
        # to /cores and are off by default, so this is linux's killer.
        if not GIT:
            self.skipTest("no git: the fixture is not a repository here")
        code, out = self.guard(1, None, "t_abort")
        self.assertEqual(code, 1, out[-800:])
        self.assertEqual(_git(self.crate, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
