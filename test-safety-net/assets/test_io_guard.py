"""Unit suite for io_guard.py. Stdlib only, offline, deterministic.

The negative fixtures are the point of this file. A guard whose tests only
prove it stays out of the way proves nothing: every group here is ARMED, then
REAL I/O is performed, and the guard's own exception type is required to fire.
The positive cases (pure computation, path algebra, a permitted Tier 2 group)
are controls that keep the negatives from passing for the wrong reason.
"""
import importlib.util
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)

_spec = importlib.util.spec_from_file_location("io_guard",
                                               os.path.join(HERE, "io_guard.py"))
io_guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(io_guard)

_rr_spec = importlib.util.spec_from_file_location("rank_risk",
                                                  os.path.join(HERE, "rank_risk.py"))
rank_risk = importlib.util.module_from_spec(_rr_spec)
_rr_spec.loader.exec_module(rank_risk)

try:
    import pytest as _pytest_mod                                # noqa: F401
    _HAS_PYTEST = True
except Exception:                                               # pragma: no cover
    _HAS_PYTEST = False


class GuardCase(unittest.TestCase):
    """Arms nothing by itself, but always disarms -- a leaked patch set would
    break every test that ran after it, in this suite and every sibling one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # Registered LAST so it runs FIRST: cleanups are LIFO, and tearing down
        # the temp dir while the guard is still armed would trip it on
        # `shutil.rmtree`. That ordering is not incidental -- it is the same
        # discipline the workflow needs, where the guard must come down before
        # anything else touches the disk.
        self.addCleanup(io_guard.disarm)
        self.path = os.path.join(self.tmp, "fixture.txt")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("real bytes on a real disk\n")

    def assertTrips(self, group, fn):
        with self.assertRaises(io_guard.IOGuardViolation) as ctx:
            fn()
        self.assertEqual(ctx.exception.group, group)
        return ctx.exception


class TestTier1BlocksEverything(GuardCase):
    """NEGATIVE fixtures: real I/O, armed, must raise IOGuardViolation."""

    def test_filesystem_reads_and_writes_trip_at_every_entry_point(self):
        # One case per LAYER, because the layers do not route through each
        # other: `open()` never reaches `os.open`, `pathlib` never reaches
        # `builtins.open`, and `os.listdir`/`os.stat` reach neither. A guard
        # that patched only `os.open`/`read`/`write` -- the "syscall layer" as
        # usually described -- would miss five of these seven.
        io_guard.arm(1)
        cases = (
            ("builtins.open", lambda: open(self.path).close()),
            ("io.open", lambda: pathlib.Path(self.path).read_text()),
            ("os.open", lambda: os.close(os.open(self.path, os.O_RDONLY))),
            ("os.listdir", lambda: os.listdir(self.tmp)),
            ("os.walk", lambda: list(os.walk(self.tmp))),
            # glob is pure Python over os.scandir, so it proves the guard
            # reaches a family member it never names directly.
            ("os.scandir", lambda: __import__("glob").glob(self.tmp + "/*")),
            ("os.stat", lambda: os.path.exists(self.path)),
            ("os.rename", lambda: os.rename(self.path, self.path + ".moved")),
        )
        for target, fn in cases:
            with self.subTest(target=target):
                exc = self.assertTrips("filesystem", fn)
                self.assertEqual(exc.target, target)

    def test_writing_a_new_file_is_blocked_not_merely_reported(self):
        # The invariant is about SIDE EFFECTS, so prove the file is not there
        # afterwards: an exception raised after the write would be no guard.
        victim = os.path.join(self.tmp, "must-not-exist.txt")
        io_guard.arm(1)
        self.assertTrips("filesystem",
                         lambda: open(victim, "w").write("escaped"))
        io_guard.disarm()
        self.assertFalse(os.path.exists(victim))

    def test_clock_randomness_and_environment_trip(self):
        import datetime
        import random
        import time
        import uuid
        io_guard.arm(1)
        for group, fn in (("clock", time.time),
                          ("clock", datetime.datetime.now),
                          ("clock", datetime.date.today),
                          ("randomness", random.random),
                          ("randomness", uuid.uuid4),
                          ("randomness", lambda: os.urandom(4)),
                          ("environment", lambda: os.getenv("HOME"))):
            with self.subTest(group=group):
                self.assertTrips(group, fn)

    def test_the_uncontrollable_groups_trip(self):
        import socket
        import sqlite3
        io_guard.arm(1)
        self.assertTrips("network", socket.socket)
        self.assertTrips("subprocess",
                         lambda: subprocess.run([sys.executable, "-c", "pass"]))
        self.assertTrips("subprocess", lambda: os.system("true"))
        self.assertTrips("database", lambda: sqlite3.connect(":memory:"))

    def test_a_process_replacing_call_is_intercepted_before_it_replaces_us(self):
        # The filter declines `os.exec*` statically because no guard survives
        # one. That stays true -- but the guard intercepts the CALL, so a
        # Python-level `os.execv` never reaches the syscall and this test can
        # exist at all. Both layers, not one: a pre-bound reference or a
        # C-level exec still escapes, which is why the static decline is not
        # redundant.
        io_guard.arm(1)
        self.assertTrips("subprocess",
                         lambda: os.execv("/bin/true", ["/bin/true"]))

    def test_tier_1_ignores_the_allow_list(self):
        # Spec: a Tier 1 unit claimed to touch nothing, so ANY touch falsifies
        # the classification. `allow` is a Tier 2 concept only.
        io_guard.arm(1, allow=("filesystem", "clock"))
        self.assertTrips("filesystem", lambda: open(self.path).close())


class TestTier2(GuardCase):
    def test_a_declared_group_is_permitted_and_the_rest_still_trip(self):
        import socket
        import time
        io_guard.arm(2, allow=("filesystem",))
        open(self.path).close()                     # the point of the test
        os.listdir(self.tmp)
        self.assertTrips("network", socket.socket)
        self.assertTrips("clock", time.time)

    def test_an_undeclared_controllable_group_still_trips(self):
        # SKILL.md's rule -- "block only the groups this test does not
        # deliberately fake" -- has to bite, or a Tier 2 test that fakes the
        # filesystem while quietly reading the real clock looks like a pin.
        import time
        io_guard.arm(2, allow=("filesystem",))
        self.assertTrips("clock", time.time)

    def test_the_spec_baseline_permits_every_controllable_group(self):
        # `allow=None` is the spec's own Tier 2 wording, "block only the
        # uncontrolled groups". Naming groups tightens it; omitting them must
        # not silently tighten it, or an agent following the spec sees the
        # guard decline its own temp dir.
        import socket
        import time
        io_guard.arm(2)
        open(self.path).close()
        time.time()
        self.assertTrips("network", socket.socket)

    def test_faking_nothing_is_expressible(self):
        io_guard.arm(2, allow=())
        self.assertTrips("filesystem", lambda: open(self.path).close())

    def test_blocked_groups_is_the_whole_decision_table(self):
        every = set(io_guard.GROUPS)
        self.assertEqual(io_guard.blocked_groups(1), every)
        self.assertEqual(io_guard.blocked_groups(1, ("filesystem",)), every)
        self.assertEqual(io_guard.blocked_groups(2),
                         set(io_guard.UNCONTROLLABLE_GROUPS))
        self.assertEqual(io_guard.blocked_groups(2, ("filesystem", "clock")),
                         every - {"filesystem", "clock"})
        self.assertEqual(io_guard.blocked_groups(2, ()), every)


class TestControls(GuardCase):
    """The other direction: the guard must not fire on what it should allow."""

    def test_pure_computation_and_path_algebra_do_not_trip(self):
        # Round 4's IMPORT_TIME_INERT ruling in the FILTER says path algebra
        # over `__file__` performs no I/O. The guard has to agree, or a Tier 1
        # unit that merely joins two strings is declined at runtime after the
        # filter deliberately let it through.
        io_guard.arm(1)
        self.assertEqual(os.path.join("a", "b"), os.path.join("a", "b"))
        os.path.dirname(os.path.abspath("/x/y/z.py"))
        os.path.basename("/x/y/z.py")
        os.path.splitext("z.py")
        self.assertEqual(sum(range(10)), 45)

    def test_importing_a_stdlib_module_while_armed_does_not_trip(self):
        # Reading a module's own source to import it is the machinery's I/O,
        # not the importer's. Without this exemption a generated test module's
        # top-level `import json` would read as a classification failure.
        io_guard.arm(1)
        import json                                             # noqa: F401
        importlib.import_module("string")

    def test_disarm_restores_every_patched_name(self):
        import datetime
        import random
        import socket
        import time
        before = {
            "builtins.open": open,
            "os.listdir": os.listdir,
            "os.stat": os.stat,
            "time.time": time.time,
            "datetime.datetime": datetime.datetime,
            "datetime.date": datetime.date,
            "random.random": random.random,
            "socket.socket": socket.socket,
            "subprocess.Popen": subprocess.Popen,
        }
        io_guard.arm(1)
        self.assertIsNot(os.listdir, before["os.listdir"])
        io_guard.disarm()
        for label, original in before.items():
            with self.subTest(name=label):
                module, attr = label.rsplit(".", 1)
                obj = {"builtins": sys.modules["builtins"]}.get(module) \
                    or sys.modules[module]
                self.assertIs(getattr(obj, attr), original)
        self.assertFalse(io_guard.armed())
        open(self.path).close()

    def test_arm_is_idempotent_and_disarm_survives_a_double_call(self):
        io_guard.arm(1)
        io_guard.arm(2, allow=("filesystem",))     # second arm is a no-op
        self.assertTrips("filesystem", lambda: open(self.path).close())
        io_guard.disarm()
        io_guard.disarm()
        open(self.path).close()


class TestSignalling(GuardCase):
    def test_the_violation_is_not_an_assertion_error(self):
        # Load-bearing: an AssertionError means the CAPTURED VALUE is wrong
        # (correct it and re-run); a guard trip means the CLASSIFICATION is
        # wrong (reclassify to Tier 3, discard). Same type would collapse two
        # opposite responses into one.
        self.assertFalse(issubclass(io_guard.IOGuardViolation, AssertionError))
        self.assertTrue(issubclass(io_guard.IOGuardViolation, BaseException))

    def test_an_except_exception_handler_cannot_swallow_a_trip(self):
        # Legacy code is full of bare `except Exception:` around I/O. If the
        # guard were an Exception subclass, such a unit would absorb the trip
        # and return a plausible value, and the test would ship.
        self.assertFalse(issubclass(io_guard.IOGuardViolation, Exception))
        io_guard.arm(1)

        def swallowing_unit():
            try:
                return open(self.path).read()
            except Exception:
                return "fallback"

        with self.assertRaises(io_guard.IOGuardViolation):
            swallowing_unit()

    def test_the_message_names_the_group_the_target_and_the_remedy(self):
        io_guard.arm(1)
        exc = self.assertTrips("filesystem", lambda: os.listdir(self.tmp))
        text = str(exc)
        self.assertIn("tier 1", text)
        self.assertIn("filesystem", text)
        self.assertIn("os.listdir", text)
        self.assertIn("Tier 3", text)


class TestEnvironmentContract(unittest.TestCase):
    def test_tier_arrives_by_environment_variable(self):
        tier, allow, notes = io_guard.read_env({"TEST_SAFETY_NET_TIER": "2"})
        self.assertEqual((tier, allow, notes), (2, None, []))
        tier, allow, _ = io_guard.read_env({
            "TEST_SAFETY_NET_TIER": "2",
            "TEST_SAFETY_NET_ALLOW": "filesystem, clock"})
        self.assertEqual((tier, allow), (2, ("filesystem", "clock")))
        tier, allow, _ = io_guard.read_env({"TEST_SAFETY_NET_TIER": "2",
                                            "TEST_SAFETY_NET_ALLOW": "none"})
        self.assertEqual((tier, allow), (2, ()))

    def test_a_missing_or_bogus_tier_fails_safe_to_the_strictest_setting(self):
        # NEGATIVE: a misconfigured invocation must over-block, never
        # under-block. Defaulting to tier 2 would let a misspelled variable
        # ship a test that performs real filesystem I/O.
        for environ in ({}, {"TEST_SAFETY_NET_TIER": ""},
                        {"TEST_SAFETY_NET_TIER": "3"},
                        {"TEST_SAFETY_NET_TIER": "banana"}):
            with self.subTest(environ=environ):
                tier, _, notes = io_guard.read_env(environ)
                self.assertEqual(tier, 1)
                self.assertTrue(notes)
                self.assertEqual(io_guard.blocked_groups(tier),
                                 set(io_guard.GROUPS))

    def test_an_unknown_allow_group_is_ignored_and_reported(self):
        tier, allow, notes = io_guard.read_env({
            "TEST_SAFETY_NET_TIER": "2",
            "TEST_SAFETY_NET_ALLOW": "filesystem,http"})
        self.assertEqual(allow, ("filesystem",))
        self.assertTrue(any("http" in n for n in notes))


class TestFilterGuardAgreement(unittest.TestCase):
    """The two layers must have ONE answer to "what counts as I/O".

    C3 found the filesystem family half-enumerated in the FILTER. The same hole
    in the GUARD is worse: a Tier 1 unit calling `os.listdir` would pass the
    filter (before the fix) and the guard (if it patched only os.open/read/
    write), which is precisely the two-layer failure the split exists to
    prevent. These tests make the correspondence mechanical instead of a claim
    in prose.
    """

    def _filter_markers(self):
        markers = set()
        for table in (rank_risk.CONTROLLABLE, rank_risk.UNCONTROLLABLE):
            for group_markers in table.values():
                markers.update(group_markers)
        return markers

    def test_the_two_layers_use_the_same_group_names(self):
        self.assertEqual(set(io_guard.CONTROLLABLE_GROUPS),
                         set(rank_risk.CONTROLLABLE))
        self.assertEqual(set(io_guard.UNCONTROLLABLE_GROUPS),
                         set(rank_risk.UNCONTROLLABLE))

    def test_every_filter_marker_has_a_guard_layer_intercept(self):
        unaccounted = sorted(
            m for m in self._filter_markers()
            if io_guard._marker_intercept(m) is None
            and m not in io_guard.PARTIALLY_INTERCEPTED
            and m not in io_guard.NOT_INTERCEPTED)
        self.assertEqual(unaccounted, [],
                         "filter markers with no guard-layer intercept and no "
                         "recorded reason: %s" % unaccounted)

    def test_the_coverage_table_carries_no_stale_entries(self):
        # The mirror: a marker deleted from the filter must not linger here
        # claiming coverage of something nobody classifies any more.
        markers = self._filter_markers()
        for name, table in (("FILTER_MARKER_INTERCEPTS",
                             io_guard.FILTER_MARKER_INTERCEPTS),
                            ("PARTIALLY_INTERCEPTED",
                             io_guard.PARTIALLY_INTERCEPTED),
                            ("NOT_INTERCEPTED", io_guard.NOT_INTERCEPTED)):
            with self.subTest(table=name):
                self.assertEqual(sorted(set(table) - markers), [])

    def test_every_partial_or_absent_intercept_records_its_reason(self):
        for table in (io_guard.PARTIALLY_INTERCEPTED, io_guard.NOT_INTERCEPTED):
            for marker, reason in table.items():
                with self.subTest(marker=marker):
                    self.assertGreater(len(reason), 40)

    def test_the_process_family_the_filter_declines_is_also_patched(self):
        derived = set(io_guard._os_process_family())
        table = {m[3:] for m in rank_risk.UNCONTROLLABLE["subprocess"]
                 if m.startswith("os.")}
        self.assertEqual(sorted(derived - table), [])

    def test_a_named_os_intercept_is_actually_patched_when_armed(self):
        # Naming a target in the coverage table proves nothing unless arming
        # really replaces it. Spot-check the family C3 was about.
        self.addCleanup(io_guard.disarm)
        originals = {n: getattr(os, n) for n in
                     ("listdir", "scandir", "walk", "stat", "rename", "open")}
        io_guard.arm(1)
        for name, original in originals.items():
            with self.subTest(name=name):
                self.assertIsNot(getattr(os, name), original)


class TestClassValuedPatches(GuardCase):
    """A patched CLASS must stay a class. NEGATIVE fixtures for the `import
    ssl` breakage: `socket.socket` was replaced by a plain function, so
    `class SSLSocket(socket)` in `ssl.py` got a function as its base and
    `import ssl` -- and with it `asyncio`, `http.client`, `urllib.request`,
    `requests` -- raised `TypeError` at BOTH tiers. That was a fourth proof
    outcome the three-outcome contract has no rule for."""

    CLASS_VALUED = (
        ("socket", "socket"), ("io", "FileIO"), ("_io", "FileIO"),
        ("mmap", "mmap"), ("subprocess", "Popen"),
    )

    def test_every_class_valued_patch_target_is_still_a_class_when_armed(self):
        originals = {}
        for module_name, attr in self.CLASS_VALUED:
            module = __import__(module_name)
            originals[(module_name, attr)] = getattr(module, attr)
            self.assertIsInstance(originals[(module_name, attr)], type,
                                  "%s.%s is not a class -- update this list"
                                  % (module_name, attr))
        io_guard.arm(1)
        for (module_name, attr), original in originals.items():
            with self.subTest(target="%s.%s" % (module_name, attr)):
                patched = getattr(__import__(module_name), attr)
                self.assertIsNot(patched, original, "not patched at all")
                self.assertIsInstance(patched, type)
                self.assertTrue(issubclass(patched, original))

    def test_import_ssl_works_at_both_tiers_while_armed(self):
        # Run out of process: `ssl` is almost certainly already imported in
        # this one, and the failure only happens on a FIRST import while armed.
        for tier in ("1", "2"):
            with self.subTest(tier=tier):
                result = subprocess.run(
                    [sys.executable, "-c",
                     "import io_guard\n"
                     "io_guard.arm(%s)\n"
                     "import ssl, urllib.request, http.client\n"
                     "import socket\n"
                     "assert isinstance(socket.socket, type)\n"
                     "assert not isinstance(object(), socket.socket)\n"
                     "print('ok', ssl.PROTOCOL_TLS_CLIENT)\n" % tier],
                    env=dict(os.environ, PYTHONPATH=HERE),
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 0,
                                 result.stdout + result.stderr)
                self.assertIn("ok", result.stdout)

    def test_constructing_a_patched_class_still_trips(self):
        # The control for the subclass fix: keeping the name a type must not
        # cost the block.
        import socket
        io_guard.arm(1)
        self.assertTrips("network", socket.socket)
        self.assertTrips("filesystem", lambda: io_guard.__dict__ and
                         __import__("io").FileIO(self.path))


class TestTheImportMachineryHole(GuardCase):
    """`pkgutil.get_data` read real files at tier 1 through TWO holes at once:
    `_io.open_code` was unpatched, and any `<frozen importlib...>` frame
    exempted the call unconditionally. Both are closed here, and the control
    below keeps the fix from simply re-breaking imports."""

    def test_pkgutil_get_data_is_blocked_at_tier_1(self):
        import pkgutil
        io_guard.arm(1)
        exc = self.assertTrips("filesystem",
                               lambda: pkgutil.get_data("json", "__init__.py"))
        self.assertEqual(exc.target, "_io.open_code")

    def test_the_underscore_io_names_are_patched_not_only_their_io_aliases(self):
        import _io
        io_guard.arm(1)
        for name in ("open", "open_code", "FileIO"):
            with self.subTest(name=name):
                self.assertTrips("filesystem",
                                 lambda n=name: getattr(_io, n)(self.path))

    def test_a_real_import_is_still_exempt_including_a_first_time_one(self):
        # The control. The exemption exists so a generated test module's
        # top-level `import x` does not read as a classification failure; if
        # narrowing it broke that, the guard would decline every candidate.
        result = subprocess.run(
            [sys.executable, "-c",
             "import io_guard, importlib\n"
             "io_guard.arm(1)\n"
             "import wave\n"
             "importlib.import_module('xml.dom.minidom')\n"
             "print('ok')\n"],
            env=dict(os.environ, PYTHONPATH=HERE),
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ok", result.stdout)

    def test_a_module_that_does_io_in_its_own_body_still_trips_on_import(self):
        # The other control: narrowing the exemption must not widen it either.
        src = os.path.join(self.tmp, "importio.py")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write("import os\n_SIZE = os.stat(__file__).st_size\n")
        result = subprocess.run(
            [sys.executable, "-c",
             "import io_guard\n"
             "io_guard.arm(1)\n"
             "try:\n"
             "    import importio\n"
             "    print('LEAK')\n"
             "except io_guard.IOGuardViolation as exc:\n"
             "    print('BLOCKED', exc.target)\n"],
            env=dict(os.environ,
                     PYTHONPATH=os.pathsep.join([HERE, self.tmp])),
            capture_output=True, text=True)
        self.assertIn("BLOCKED", result.stdout, result.stdout + result.stderr)


class TestEnvironmentReads(GuardCase):
    """`os.environ.get(...)` reached the real environment at tier 1: the
    MutableMapping methods bottom out in `__getitem__`, which never calls
    `os.getenv`. The filter DID see `os.environ.get(...)`, so the recorded
    "the two layers agree on this blind spot" was false in both directions."""

    def test_every_read_form_of_os_environ_trips(self):
        io_guard.arm(1)
        alias = os.environ
        for label, fn in (("subscript", lambda: os.environ["PATH"]),
                          ("get", lambda: os.environ.get("PATH")),
                          ("aliased get", lambda: alias.get("PATH")),
                          ("items", lambda: list(os.environ.items())),
                          ("copy", lambda: os.environ.copy()),
                          ("iteration", lambda: list(os.environ))):
            with self.subTest(form=label):
                self.assertTrips("environment", fn)

    def test_the_filter_and_the_guard_now_agree_on_os_environ(self):
        # The claim this replaces said the two layers agreed because NEITHER
        # could see the subscript. The filter's marker table sees `os.environ`;
        # so, now, does the guard.
        self.assertIn("os.environ", rank_risk.CONTROLLABLE["environment"])
        self.assertIsNotNone(io_guard._marker_intercept("os.environ")
                             or io_guard.PARTIALLY_INTERCEPTED.get("os.environ"))

    def test_a_tier_2_run_that_declares_environment_still_permits_reads(self):
        io_guard.arm(2, allow=("environment",))
        os.environ.get("PATH")
        self.assertIsInstance(os.environ.copy(), dict)

    def test_disarm_restores_the_real_mapping(self):
        real = os.environ
        io_guard.arm(1)
        self.assertIsNot(os.environ, real)
        io_guard.disarm()
        self.assertIs(os.environ, real)


class TestOffMainThreadViolations(GuardCase):
    """A violation raised on a worker thread is swallowed by
    `Thread._bootstrap_inner`, which catches BaseException. Before this, the
    proof run reported `1 passed` and the skill shipped a test that performs
    real I/O -- the one outcome the guard must never have."""

    def test_a_join_re_raises_the_workers_violation_on_the_main_thread(self):
        io_guard.arm(1)
        t = threading.Thread(target=lambda: open(self.path).read())
        t.start()
        with self.assertRaises(io_guard.IOGuardViolation) as ctx:
            t.join()
        self.assertEqual(ctx.exception.group, "filesystem")
        self.assertIsNotNone(ctx.exception.thread)
        self.assertIn("not the main thread", str(ctx.exception))

    def test_a_dropped_future_is_caught_even_though_nothing_reads_it(self):
        # `concurrent.futures` stores the exception ON THE FUTURE and never
        # calls `threading.excepthook`, so an excepthook-based fix would miss
        # this entirely. Recording at the RAISE is what sees it. (The executor
        # joins its workers on shutdown, so the surfacing point here is the
        # patched `join` -- which is the mechanism working, not a bypass.)
        import concurrent.futures
        io_guard.arm(1)
        with self.assertRaises(io_guard.IOGuardViolation):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(lambda: open(self.path).read())

    def test_disarm_is_the_backstop_and_still_restores_everything(self):
        real_hook = threading.excepthook
        threading.excepthook = lambda args: None    # keep the suite output clean
        self.addCleanup(setattr, threading, "excepthook", real_hook)
        io_guard.arm(1)
        t = threading.Thread(target=lambda: open(self.path).read())
        t.start()
        t._started.wait()
        while t.is_alive():
            pass
        with self.assertRaises(io_guard.IOGuardViolation):
            io_guard.disarm()
        self.assertFalse(io_guard.armed())
        self.assertEqual(io_guard.pending_thread_violations(), ())
        open(self.path).close()          # the patch set really did come down

    def test_a_clean_threaded_unit_does_not_trip(self):
        # The control: threads are not the offence, unguarded I/O is.
        io_guard.arm(1)
        box = {}
        t = threading.Thread(target=lambda: box.setdefault("n", 1 + 1))
        t.start()
        t.join()
        self.assertEqual(box["n"], 2)


_INVOCATIONS = [("python -m pytest", [sys.executable, "-m", "pytest"])]
if shutil.which("pytest"):
    _INVOCATIONS.append(("pytest console script", [shutil.which("pytest")]))


@unittest.skipUnless(_HAS_PYTEST, "pytest is not installed in this environment")
class TestPytestPluginIntegration(unittest.TestCase):
    """End-to-end through real pytest: the `-p` loading contract itself.

    Skipped rather than faked when pytest is absent, because faking it would
    prove nothing about the hook that matters -- arming ahead of collection is
    the whole reason the spec chose `-p` over a `conftest.py`.

    EVERY case runs under BOTH invocations. This class used to run only
    `python -m pytest`, while every document in the skill prints `pytest` --
    and the two differ in a way that mattered: the console script's own frame
    is not under any library root, so the guard read pytest's own I/O as the
    unit's and the documented command could not complete at either tier. A
    suite that exercises a different command than the docs prescribe proves
    nothing about the docs, so the loop is the fix, not a convenience.
    """

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, True)

    def _write(self, rel, text):
        p = pathlib.Path(self.repo) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def _run(self, target, tier="1", allow=None):
        """(label, CompletedProcess) for each invocation form."""
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([HERE, self.repo])
        env["TEST_SAFETY_NET_TIER"] = tier
        env.pop("TEST_SAFETY_NET_ALLOW", None)
        if allow is not None:
            env["TEST_SAFETY_NET_ALLOW"] = allow
        out = []
        for label, argv in _INVOCATIONS:
            out.append((label, subprocess.run(
                argv + ["-p", "io_guard", "-q", "--no-header",
                        "-p", "no:cacheprovider", target],
                cwd=self.repo, env=env, capture_output=True, text=True)))
        return out

    def test_an_honest_tier_1_test_still_passes_with_the_guard_loaded(self):
        # The control, and the one that would catch a guard that broke
        # pytest's own I/O: collection, capture (the print), assertion
        # rewriting and reporting all happen with the guard armed.
        self._write("pure.py", "def add(a, b):\n    return a + b\n")
        self._write("test_pure.py",
                    "import pure\n\n\ndef test_add():\n"
                    "    print('captured output')\n"
                    "    assert pure.add(2, 3) == 5\n")
        for label, result in self._run("test_pure.py"):
            with self.subTest(invocation=label):
                self.assertEqual(result.returncode, 0,
                                 result.stdout + result.stderr)
                self.assertIn("1 passed", result.stdout)

    def test_a_deliberately_wrong_expectation_still_reads_as_an_assertion(self):
        # The RED half of red->green must stay distinguishable: an
        # AssertionError, not the guard's type.
        self._write("pure.py", "def add(a, b):\n    return a + b\n")
        self._write("test_pure.py",
                    "import pure\n\n\ndef test_add():\n"
                    "    assert pure.add(2, 3) == 99\n")
        for label, result in self._run("test_pure.py"):
            with self.subTest(invocation=label):
                self.assertEqual(result.returncode, 1)
                self.assertIn("AssertionError", result.stdout)
                self.assertNotIn("IOGuardViolation", result.stdout)

    def test_a_tier_1_unit_that_lists_a_directory_trips_the_guard(self):
        # NEGATIVE, end to end: exactly the C3 case. Before the marker fix the
        # filter called this Tier 1; a guard patching only os.open/read/write
        # would have let it through too.
        self._write("sneaky.py",
                    "import os\n\n\ndef count(path):\n"
                    "    return len(os.listdir(path))\n")
        self._write("test_sneaky.py",
                    "import sneaky\n\n\ndef test_count():\n"
                    "    assert sneaky.count('.') >= 0\n")
        for label, result in self._run("test_sneaky.py"):
            with self.subTest(invocation=label):
                self.assertEqual(result.returncode, 1)
                self.assertIn("IOGuardViolation", result.stdout)
                self.assertIn("os.listdir", result.stdout)

    def test_import_time_io_trips_during_collection_which_is_why_p_not_conftest(self):
        # The property that decides the loading mechanism. A guard installed
        # from a fixture -- or from a conftest imported after the test module
        # -- never sees this: the side effect runs during `import unit_module`,
        # before any fixture body executes.
        self._write("cfgmod.py",
                    "_CFG = open(__file__).read()\n\n\n"
                    "def size():\n    return len(_CFG)\n")
        self._write("test_cfgmod.py",
                    "import cfgmod\n\n\ndef test_size():\n"
                    "    assert cfgmod.size() > 0\n")
        for label, result in self._run("test_cfgmod.py"):
            with self.subTest(invocation=label):
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("IOGuardViolation", result.stdout)
                self.assertIn("builtins.open", result.stdout)

    def test_a_tier_2_run_permits_the_group_it_declares(self):
        self._write("sneaky.py",
                    "import os\n\n\ndef count(path):\n"
                    "    return len(os.listdir(path))\n")
        self._write("test_sneaky.py",
                    "import sneaky\n\n\ndef test_count():\n"
                    "    assert sneaky.count('.') >= 0\n")
        for label, result in self._run("test_sneaky.py", tier="2",
                                       allow="filesystem"):
            with self.subTest(invocation=label):
                self.assertEqual(result.returncode, 0,
                                 result.stdout + result.stderr)

    def test_a_worker_thread_violation_fails_the_test_instead_of_passing(self):
        # NEGATIVE, end to end. `Thread._bootstrap_inner` catches
        # BaseException and routes it to `threading.excepthook`, so before the
        # fix this run reported "1 passed" and the skill shipped a test whose
        # captured value existed only because the guard was armed -- it fails
        # the moment the target repo runs its own suite without the guard.
        self._write("loader.py",
                    "import threading\n\n\n"
                    "def _hosts(box):\n"
                    "    box['n'] = len(open('/etc/hosts').read())\n\n\n"
                    "HANDLERS = {'hosts': _hosts}\n\n\n"
                    "def load(kind='hosts'):\n"
                    "    box = {'n': -1}\n"
                    "    t = threading.Thread(target=HANDLERS[kind], args=(box,))\n"
                    "    t.start()\n    t.join()\n    return box['n']\n")
        self._write("test_loader.py",
                    "import loader\n\n\ndef test_load():\n"
                    "    assert loader.load() == -1\n")
        for label, result in self._run("test_loader.py"):
            with self.subTest(invocation=label):
                self.assertNotEqual(result.returncode, 0,
                                    result.stdout + result.stderr)
                self.assertIn("IOGuardViolation", result.stdout)
                self.assertNotIn("1 passed,", result.stdout)

    def test_a_thread_that_is_never_joined_still_fails_the_run(self):
        # The shape `Thread.join` cannot catch: the unit drops the thread. The
        # teardown hook is the backstop, so the run ERRORs rather than passing.
        self._write("fire.py",
                    "import threading\n\n\n"
                    "def fire():\n"
                    "    t = threading.Thread(\n"
                    "        target=lambda: open('/etc/hosts').read())\n"
                    "    t.start()\n    t.join(timeout=5)\n    return 'sent'\n")
        self._write("test_fire.py",
                    "import fire\n\n\ndef test_fire():\n"
                    "    assert fire.fire() == 'sent'\n")
        for label, result in self._run("test_fire.py"):
            with self.subTest(invocation=label):
                self.assertNotEqual(result.returncode, 0,
                                    result.stdout + result.stderr)
                self.assertIn("IOGuardViolation", result.stdout)

    def test_the_header_states_the_tier_and_what_is_blocked(self):
        self._write("test_nothing.py", "def test_ok():\n    assert True\n")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([HERE, self.repo])
        env["TEST_SAFETY_NET_TIER"] = "1"
        for label, argv in _INVOCATIONS:
            with self.subTest(invocation=label):
                result = subprocess.run(
                    argv + ["-p", "io_guard", "-p", "no:cacheprovider",
                            "test_nothing.py"],
                    cwd=self.repo, env=env, capture_output=True, text=True)
                self.assertIn("io_guard: tier=1", result.stdout)
                self.assertIn("filesystem", result.stdout)



# --------------------------------------------------------------------------
# The documented invocation, run verbatim.
# --------------------------------------------------------------------------
_SH_BLOCK_RE = re.compile(r"```sh\n(.*?)```", re.S)
_DOCUMENTED_SOURCES = (
    ("SKILL.md", os.path.join(SKILL_DIR, "SKILL.md")),
    ("references/triage.md", os.path.join(SKILL_DIR, "references", "triage.md")),
    ("references/stacks.md", os.path.join(SKILL_DIR, "references", "stacks.md")),
)


# A command, as opposed to prose that happens to mention one: zero or more
# NAME=value assignments followed by the invocation and exactly one target.
# Filtering by SHAPE rather than by "contains pytest" keeps a sentence about
# the command out of the runner -- and the two count assertions below keep
# the filter from quietly emptying the test.
_CMD_SHAPE_RE = re.compile(
    r'^(?:[A-Za-z_][A-Za-z_0-9]*=(?:"[^"]*"|\S*)\s+)*'
    r'pytest\s+-p\s+io_guard\s+\S+$')


def _shell_commands(text):
    """Every shell command in `text` that loads the guard, joined at its
    backslash continuations exactly as a shell would join them.

    Extraction rather than a hardcoded string on purpose: the point of the
    test below is that whatever the documents currently SAY is what gets run,
    so a future reword cannot drift away from a passing test."""
    commands = []
    for block in _SH_BLOCK_RE.findall(text):
        joined = re.sub(r"\\\n\s*", " ", block)
        for line in joined.splitlines():
            line = line.strip()
            if _CMD_SHAPE_RE.match(line):
                commands.append(line)
    return commands


def _docstring_commands(text):
    """The same, for the guard's own module docstring, which is indented
    prose rather than a fenced block."""
    joined = re.sub(r"\\\\?\n\s*", " ", text)
    return [ln.strip() for ln in joined.splitlines()
            if _CMD_SHAPE_RE.match(ln.strip())]


@unittest.skipUnless(_HAS_PYTEST, "pytest is not installed in this environment")
@unittest.skipUnless(shutil.which("pytest"),
                     "the `pytest` console script is not on PATH")
class TestTheDocumentedInvocation(unittest.TestCase):
    """Run the command string the documents print, verbatim, and require a pass.

    This class exists because of a specific failure: every document tells the
    agent to run `pytest -p io_guard ...` while the integration suite ran
    `python -m pytest -p io_guard ...`. Those are NOT the same command -- the
    console script's own frame (`<prefix>/bin/pytest`) sits at the base of
    every stack, and it is not under any library root, so the guard read
    pytest's own capture and environment handling as "code under test" and
    killed the run. 122 green tests said nothing about it, because none of them
    ran what the documentation ships.

    So: extract the command from the document, substitute only the angle-
    bracket placeholders, and hand the rest to `/bin/sh` unchanged.
    """

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, True)
        pathlib.Path(self.repo, "pure.py").write_text(
            "def add(a, b):\n    return a + b\n", encoding="utf-8")
        pathlib.Path(self.repo, "test_pure.py").write_text(
            "import pure\n\n\ndef test_add():\n"
            "    print('captured output')\n"
            "    assert pure.add(2, 3) == 5\n", encoding="utf-8")

    def _run_documented(self, command):
        cmd = command.replace("<path>::<test_name>", "test_pure.py::test_add")
        cmd = cmd.replace("path/to/test_file.py::test_name",
                          "test_pure.py::test_add")
        env = dict(os.environ)
        env["SKILL_DIR"] = SKILL_DIR
        env.pop("PYTHONPATH", None)
        env.pop("TEST_SAFETY_NET_TIER", None)
        env.pop("TEST_SAFETY_NET_ALLOW", None)
        return cmd, subprocess.run(["/bin/sh", "-c", cmd + " -q -p no:cacheprovider"],
                                   cwd=self.repo, env=env,
                                   capture_output=True, text=True)

    def _assert_clean_pass(self, label, command):
        cmd, result = self._run_documented(command)
        detail = "%s\n$ %s\n%s\n%s" % (label, cmd, result.stdout, result.stderr)
        self.assertEqual(result.returncode, 0, detail)
        self.assertIn("1 passed", result.stdout, detail)
        self.assertNotIn("IOGuardViolation", result.stdout, detail)
        self.assertNotIn("Traceback", result.stderr, detail)

    def test_every_documented_command_in_every_document_runs_clean(self):
        found = 0
        for label, path in _DOCUMENTED_SOURCES:
            with open(path, encoding="utf-8") as fh:
                commands = _shell_commands(fh.read())
            self.assertTrue(commands, "%s documents no guard invocation" % label)
            for command in commands:
                found += 1
                with self.subTest(document=label, command=command):
                    self._assert_clean_pass(label, command)
        self.assertGreaterEqual(found, 5, "expected the five documented "
                                          "invocations; found %d" % found)

    def test_the_guards_own_docstring_invocation_runs_clean(self):
        commands = _docstring_commands(io_guard.__doc__ or "")
        self.assertEqual(len(commands), 2,
                         "expected the two invocations io_guard's docstring "
                         "prints; found %r" % (commands,))
        for command in commands:
            with self.subTest(command=command):
                self._assert_clean_pass("io_guard.__doc__", command)

    def test_every_documented_invocation_carries_the_pythonpath_it_needs(self):
        # `pytest -p io_guard` on its own dies with `ImportError: Error
        # importing plugin "io_guard"` -- the console script does not put the
        # working directory, let alone the skill's assets/, on sys.path. So the
        # PYTHONPATH assignment is not decoration and must never be separable
        # from the pytest line by a reader copying one line out of a block.
        for label, path in _DOCUMENTED_SOURCES:
            with open(path, encoding="utf-8") as fh:
                for command in _shell_commands(fh.read()):
                    with self.subTest(document=label, command=command):
                        self.assertIn("PYTHONPATH=", command)
                        self.assertIn("$SKILL_DIR/assets", command)

    def test_the_bare_pytest_line_fails_loudly_rather_than_silently(self):
        # The control for the check above: prove the omission really is fatal,
        # so the requirement is a measured fact and not a superstition.
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            ["/bin/sh", "-c", "pytest -p io_guard -q test_pure.py::test_add"],
            cwd=self.repo, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("io_guard", result.stdout + result.stderr)


if __name__ == "__main__":                                      # pragma: no cover
    unittest.main()
