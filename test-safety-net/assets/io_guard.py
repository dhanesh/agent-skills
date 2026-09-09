"""io_guard — the tier-aware runtime I/O guard for `test-safety-net`.

This is the SOLE enforcement of the skill's headline invariant, "never writes a
test that performs real I/O." The static triage in `rank_risk.py` is a FILTER:
it ranks candidates and declines obvious hazards, but Python's dynamic dispatch
(`getattr`, dispatch tables, dynamic imports) makes reachability undecidable
from source, so the invariant is a RUNTIME property or it is nothing.

Load it as a pytest plugin on the single-test invocation of the red->green
proof — never as a `conftest.py` written into the user's tree, which would be a
carve-out in Invariant 1 ("never modifies source"):

    PYTHONPATH="$SKILL_DIR/assets:$PYTHONPATH" \\
    TEST_SAFETY_NET_TIER=1 \\
      pytest -p io_guard path/to/test_file.py::test_name

    PYTHONPATH="$SKILL_DIR/assets:$PYTHONPATH" \\
    TEST_SAFETY_NET_TIER=2 TEST_SAFETY_NET_ALLOW=filesystem,clock \\
      pytest -p io_guard path/to/test_file.py::test_name

Stdlib only. No pip, no network, and it never imports a third-party package
(the database entry points are patched only once the target repo has imported
them itself; see `_patch_database`).

WHAT IT PATCHES, AND WHY THAT LAYER
-----------------------------------
The ergonomic wrappers are not the layer. Measured on CPython 3.12, not
assumed:

    open(p)                 -> builtins.open          (NOT os.open, NOT io.FileIO)
    io.open(p) / pathlib    -> io.open                (NOT builtins.open)
    os.path.exists(p)       -> os.stat
    os.walk(p), glob.glob   -> os.scandir
    shutil.copy(a, b)       -> builtins.open + os.stat
    tempfile.mkstemp()      -> os.open / os.write / os.close
    subprocess.run([...])   -> (fork_exec is C; only subprocess.Popen is visible)
    socket.create_connection-> socket.socket

So a guard scoped to `builtins.open` misses `pathlib`; one scoped to `os.open`
misses `open()`; one scoped to `os.open`/`read`/`write` misses `os.listdir`
(getdents) and `os.stat` (stat) entirely. The guard therefore patches EVERY
name the filter's marker tables know about, at the lowest layer reachable from
Python — which for CPython is the `os` module's own primitives, since `_io` and
`posix` reach the syscall in C without routing back through any Python name.

`FILTER_MARKER_INTERCEPTS` / `PARTIALLY_INTERCEPTED` / `NOT_INTERCEPTED` below
are the machine-checkable statement of that correspondence:
`test_io_guard.py` asserts the three of them partition `rank_risk.py`'s marker
tables exactly, so a marker added to the filter with no guard-layer intercept
fails the gate rather than opening a silent two-layer hole.

HOW ARMING IS SCOPED
--------------------
The arming WINDOW is the whole pytest session — it has to be, because a module
that does I/O at import time runs its side effects during `import unit_module`,
before any fixture body executes. The BLOCK DECISION is what is scoped, by call
provenance: a guarded primitive raises only when the call was initiated by code
outside the interpreter's own library roots — i.e. by the target repo's test
module or the unit under test. Everything pytest does on its own behalf
(collection, assertion rewriting, capture, reporting, importing plugins) runs
on stacks that never leave the stdlib, site-packages or the import machinery,
and is exempt. See `_initiated_by_code_under_test`.

The failure direction is deliberate: a false POSITIVE costs one declined
candidate (the skill reports "could not prove" and moves on); a false NEGATIVE
ships a test that performs real I/O, which is the thing this file exists to
make impossible. Where the two trade off, this guard over-fires.

RESIDUALS, STATED RATHER THAN IMPLIED
-------------------------------------
1. A test that spawns a subprocess which itself dials out escapes an in-process
   guard. Naming it is the point.
2. A C extension that calls the syscall directly (`numpy.fromfile`,
   `ctypes.CDLL(...)`) bypasses every Python name and is not intercepted.
3. A reference bound before the guard armed (`from os import stat` executed in
   an already-imported module) keeps the original. Arming ahead of collection
   is what makes this rare rather than routine.
4. `os.environ[...]` as a bare subscript is not intercepted — only
   `os.getenv`/`putenv`/`unsetenv` are. This is the SAME residual the filter
   has (its markers match calls, so `HOME = os.environ["HOME"]` is invisible to
   it too), which is why the two layers still agree.
5. If the unit under test is imported from site-packages rather than from the
   working tree, its frames are exempt and the guard under-fires. Run the proof
   against the working tree.
"""

import os
import sys
import sysconfig


__all__ = ["IOGuardViolation", "arm", "disarm", "armed", "blocked_groups",
           "GROUPS", "CONTROLLABLE_GROUPS", "UNCONTROLLABLE_GROUPS"]


class IOGuardViolation(BaseException):
    """A guarded I/O primitive was reached from the code under test.

    Deliberately NOT an `AssertionError`, and deliberately a `BaseException`
    rather than an `Exception`, for two different reasons.

    Distinct from `AssertionError` because the proof run has THREE outcomes,
    not two, and two of them demand opposite responses. An `AssertionError` is
    the RED half of red->green: the CAPTURED VALUE is wrong, so correct it and
    re-run. `IOGuardViolation` means the CLASSIFICATION is wrong: the unit
    reaches real I/O the tier said it did not, so reclassify it to Tier 3 and
    discard the test, regardless of whether that run was red or green.
    Conflating the two is exactly how a Tier 1 candidate that touches the
    filesystem "passes" RED and then "passes" GREEN without either run ever
    proving the classification safe.

    A `BaseException` because `except Exception:` inside the unit under test
    would otherwise swallow the trip and hand back a normal-looking value.
    """

    def __init__(self, group, target, tier):
        self.group = group
        self.target = target
        self.tier = tier
        super().__init__(
            "test-safety-net io_guard: a tier %s candidate reached %s I/O via "
            "%s. This is a CLASSIFICATION failure, not an assertion failure: "
            "reclassify the unit to Tier 3 and discard the test, regardless of "
            "red or green (references/triage.md, 'How the guard signals')."
            % (tier, group, target))


# --------------------------------------------------------------------------
# Groups. These are `rank_risk.py`'s groups, verbatim -- the filter and the
# guard must have ONE answer to "what counts as filesystem I/O" or the split
# between them stops meaning anything.
# --------------------------------------------------------------------------
CONTROLLABLE_GROUPS = ("clock", "environment", "filesystem", "randomness")
UNCONTROLLABLE_GROUPS = ("database", "network", "subprocess")
GROUPS = tuple(sorted(CONTROLLABLE_GROUPS + UNCONTROLLABLE_GROUPS))


def _os_process_family():
    """Every `os` name that starts or replaces a process, derived from `dir(os)`.

    Derived rather than enumerated for the same reason `rank_risk.py`'s marker
    table derives its completeness test: a literal list rots when a Python
    release adds a sibling, and it is wrong on a platform exposing a different
    subset. This is the family the filter must decline STATICALLY (an `exec*`
    replaces the process image, taking every in-process patch with it), so
    patching it here is a second line, not the first one.
    """
    return sorted(n for n in dir(os)
                  if n.startswith(("exec", "spawn", "fork", "posix_spawn"))
                  and callable(getattr(os, n, None)))


# `os` primitives per group. Every name is checked for existence before it is
# patched, so a platform without `os.chflags` or `os.startfile` is fine.
_OS_FILESYSTEM = (
    # fd-level data movement
    "open", "read", "write", "close", "fdopen",
    "pread", "pwrite", "preadv", "pwritev", "readv", "writev", "sendfile",
    "fsync", "fdatasync", "ftruncate", "truncate",
    # directory + metadata reads -- the family the review found missing, and
    # the reason a guard scoped to os.open/read/write is not enough:
    # os.listdir is getdents and os.stat is stat, neither of which passes
    # through an fd the guard already saw.
    "listdir", "scandir", "walk", "fwalk",
    "stat", "lstat", "fstat", "statvfs", "fstatvfs",
    "access", "pathconf", "fpathconf", "readlink",
    # mutation
    "remove", "unlink", "rename", "renames", "replace",
    "mkdir", "makedirs", "rmdir", "removedirs",
    "link", "symlink", "mkfifo", "mknod",
    "chdir", "fchdir", "chroot",
    "chmod", "fchmod", "lchmod", "chown", "fchown", "lchown",
    "chflags", "lchflags", "utime",
    "getxattr", "setxattr", "listxattr", "removexattr",
)
_OS_ENVIRONMENT = ("getenv", "getenvb", "putenv", "unsetenv")
_OS_RANDOMNESS = ("urandom",)
_OS_SUBPROCESS = ("system", "popen", "startfile")

_RANDOM_NAMES = ("random", "randint", "randrange", "randbytes", "choice",
                 "choices", "shuffle", "sample", "uniform", "triangular",
                 "gauss", "normalvariate", "betavariate", "expovariate",
                 "gammavariate", "lognormvariate", "paretovariate",
                 "vonmisesvariate", "weibullvariate", "getrandbits", "seed")
_SECRETS_NAMES = ("token_bytes", "token_hex", "token_urlsafe", "choice",
                  "randbelow", "randbits")
_UUID_NAMES = ("uuid1", "uuid3", "uuid4", "uuid5")
# Wall-clock reads only. `time.monotonic`/`perf_counter` are counters, not the
# clock, and the filter does not mark them either -- blocking them here would
# make the guard stricter than the layer it has to agree with, at the cost of
# false trips on any unit that measures an elapsed duration.
_TIME_NAMES = ("time", "time_ns", "sleep", "localtime", "gmtime", "ctime")
_SOCKET_NAMES = ("socket", "create_connection", "create_server", "socketpair",
                 "getaddrinfo", "gethostbyname", "gethostbyname_ex",
                 "gethostbyaddr")
_SUBPROCESS_NAMES = ("Popen", "run", "call", "check_call", "check_output",
                     "getoutput", "getstatusoutput")

# Third-party (and stdlib) database entry points. NEVER imported by this file:
# a module is patched only if the target repo has already imported it, and
# `_guarded_import` picks up any that are imported later while armed. Importing
# `psycopg2` here would make a database driver a dependency of a skill whose
# whole contract is stdlib-only.
_DATABASE_TARGETS = (
    ("sqlite3", "connect"),
    ("psycopg2", "connect"),
    ("psycopg", "connect"),
    ("pymysql", "connect"),
    ("MySQLdb", "connect"),
    ("pymongo", "MongoClient"),
    ("sqlalchemy", "create_engine"),
)


# --------------------------------------------------------------------------
# The filter <-> guard correspondence. `test_io_guard.py` asserts these three
# maps partition `rank_risk.py`'s marker tables exactly.
# --------------------------------------------------------------------------
FILTER_MARKER_INTERCEPTS = {
    # filesystem
    "open": "builtins.open",
    "io.open": "io.open",
    "io.open_code": "io.open_code",
    "codecs.open": "codecs.open",
    "io.FileIO": "io.FileIO",
    "mmap.mmap": "mmap.mmap",
    "pathlib": "io.open + os.stat (Path.open/read_text/exists bottom out there)",
    "os.path": "os.stat + os.listdir (os.path.exists/getsize/isfile are stat calls; "
               "os.path.join and friends perform no I/O and are correctly not intercepted)",
    "glob": "os.scandir (glob.glob/iglob walk with scandir)",
    "fileinput": "builtins.open",
    "shutil": "builtins.open + os.stat + os.listdir",
    "tempfile": "os.open + os.write + os.mkdir",
    # clock
    "datetime": "datetime.datetime / datetime.date (guarded subclasses)",
    "date.today": "datetime.date.today (guarded subclass)",
    "time.time": "time.time",
    "time.sleep": "time.sleep",
    # randomness
    "random": "random.random and the rest of the module-level Random methods",
    "uuid.uuid4": "uuid.uuid4",
    "secrets": "secrets.token_bytes/token_hex/token_urlsafe/choice/randbelow/randbits",
    # environment
    "os.getenv": "os.getenv",
    "os.getenvb": "os.getenvb",
    "os.putenv": "os.putenv",
    "os.unsetenv": "os.unsetenv",
    # network
    "socket": "socket.socket",
    "requests": "socket.socket (every HTTP client bottoms out there)",
    "urllib.request": "socket.socket",
    "urlopen": "socket.socket",
    "httpx": "socket.socket",
    "aiohttp": "socket.socket",
    "boto3": "socket.socket",
    # subprocess
    "subprocess": "subprocess.Popen and the run/call/check_* wrappers",
    # database
    "sqlite3.connect": "sqlite3.connect",
    "psycopg2": "psycopg2.connect (patched on import, never imported by the guard)",
    "pymongo": "pymongo.MongoClient (patched on import)",
    "MongoClient": "pymongo.MongoClient (patched on import)",
    "create_engine": "sqlalchemy.create_engine (patched on import)",
}

PARTIALLY_INTERCEPTED = {
    "os.environ": "os.getenv / os.putenv / os.unsetenv are patched; a bare "
                  "`os.environ[\"HOME\"]` subscript is not. This is the SAME "
                  "residual the filter has -- its markers match a CALL, so the "
                  "subscript form is invisible to it too -- so the two layers "
                  "still agree on what they can and cannot see.",
}

NOT_INTERCEPTED = {}


def _marker_intercept(marker):
    """The guard target that intercepts `marker`, or None.

    Every `os.<name>` marker whose name this guard patches maps to itself; the
    module-prefix markers (`pathlib`, `shutil`, `requests`, ...) map through
    `FILTER_MARKER_INTERCEPTS` to the lower layer they bottom out in.
    """
    if marker in FILTER_MARKER_INTERCEPTS:
        return FILTER_MARKER_INTERCEPTS[marker]
    if marker.startswith("os."):
        name = marker[3:]
        if name in (_OS_FILESYSTEM + _OS_ENVIRONMENT + _OS_RANDOMNESS
                    + _OS_SUBPROCESS + tuple(_os_process_family())):
            return marker
    return None


# --------------------------------------------------------------------------
# Call provenance
# --------------------------------------------------------------------------
def _library_roots():
    """Directories whose code is NOT "under test": the interpreter's own.

    stdlib + platstdlib + purelib + platlib, plus the package directory of
    every already-imported test-harness module (pytest, _pytest, pluggy, py,
    iniconfig), so a harness installed outside site-packages is still exempt.
    """
    roots = set()
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        try:
            path = sysconfig.get_paths().get(key)
        except Exception:                                   # pragma: no cover
            path = None
        if path:
            roots.add(os.path.realpath(path))
    for name in ("pytest", "_pytest", "pluggy", "py", "iniconfig"):
        mod = sys.modules.get(name)
        f = getattr(mod, "__file__", None)
        if f:
            roots.add(os.path.realpath(os.path.dirname(f)))
    # This FILE, not its directory: the skill's `assets/` also holds
    # `rank_risk.py` and this guard's own test suite, and exempting the whole
    # directory would make the guard invisible to the tests that arm it.
    # The guard's internal calls are handled by the re-entrancy flag in
    # `_should_block`, not by this exemption.
    roots.add(os.path.realpath(os.path.abspath(__file__)))
    return tuple(sorted(roots))


_roots = ()
_frame_verdicts = {}

# Stdlib modules that read SOMEONE ELSE'S source in order to describe it. They
# stop the provenance walk the way the import machinery does: when a test fails
# inside the armed window, `traceback` -> `linecache` -> `os.stat` runs with the
# test's own frame still live (unittest formats a subTest failure from inside
# the test method), and without this the guard would trip on the reporting of
# the very failure it is meant to let through. Narrow and named rather than a
# blanket stdlib exemption -- `shutil.copy` called BY the unit under test must
# still block.
_DIAGNOSTIC_FRAMES = frozenset(("linecache.py", "traceback.py", "inspect.py"))


def _is_library_frame(filename):
    verdict = _frame_verdicts.get(filename)
    if verdict is None:
        real = os.path.realpath(filename)
        verdict = any(real == root or real.startswith(root + os.sep)
                      for root in _roots)
        _frame_verdicts[filename] = verdict
    return verdict


def _should_block(group, target):
    """Whether a guarded call trips, with re-entrancy and group checks first.

    The re-entrancy flag is load-bearing, not defensive coding: deciding
    provenance calls `os.path.realpath`, which calls `os.lstat` -- itself a
    guarded primitive. Without the flag the guard trips on its own bookkeeping
    and every call reports `os.lstat` no matter what was really called.
    Single-threaded by construction: the proof runs one test at a time.
    """
    if not _state["armed"] or _state["inside"] or group not in _state["blocked"]:
        return False
    _state["inside"] = True
    try:
        return _initiated_by_code_under_test(3)
    finally:
        _state["inside"] = False


def _initiated_by_code_under_test(depth=2):
    """True when the innermost non-library frame belongs to the target repo.

    Walks the stack innermost-first:

    * a frame inside a library root (stdlib, site-packages, the harness, this
      guard) is transparent -- `shutil.copy` called BY the unit under test must
      still block, so we keep walking outward rather than exempting on it;
    * a frame belonging to the import machinery (`<frozen importlib...>`) STOPS
      the walk and exempts the call. Reading a module's own source in order to
      import it is the machinery's I/O, not the importer's -- otherwise a
      generated test module's top-level `import pytest` would trip the guard.
      A module that performs I/O in its own body is unaffected: its `<module>`
      frame is reached first, and it is not a library frame;
    * a `<frozen ...>` frame for any other frozen stdlib module (`runpy`, which
      is how `python -m pytest` starts) is a library frame and is transparent;
    * a stdlib diagnostic frame (`linecache`, `traceback`, `inspect`) also
      stops the walk: reading a source file to FORMAT a failure is the
      reporter's I/O, not the unit's;
    * anything else is the target repo -- the test module, the unit under test,
      or a `<stdin>`/`exec()`/`compile()` frame whose provenance cannot be
      established -- and the call blocks. Unattributable frames block by
      design: the fail-safe direction for a guard is to over-block.

    Reaching the top of the stack without finding such a frame means the call
    came from pytest or the stdlib on their own behalf: exempt.
    """
    try:
        frame = sys._getframe(depth)
    except ValueError:                                      # pragma: no cover
        return True
    while frame is not None:
        filename = frame.f_code.co_filename
        if filename.startswith("<"):
            if "importlib" in filename:
                return False
            if filename.startswith("<frozen "):
                frame = frame.f_back      # a frozen stdlib module (runpy, ...)
                continue
            return True
        if _is_library_frame(filename):
            if os.path.basename(filename) in _DIAGNOSTIC_FRAMES:
                return False
            frame = frame.f_back
            continue
        return True
    return False


# --------------------------------------------------------------------------
# Arming
# --------------------------------------------------------------------------
_state = {"armed": False, "tier": None, "blocked": frozenset(), "inside": False}
_undo = []


def armed():
    return _state["armed"]


def blocked_groups(tier, allow=None):
    """The groups a run at `tier` blocks, given the groups it declares it fakes.

    Tier 1 blocks EVERYTHING, `allow` ignored: the unit claimed to touch
    nothing, so any touch falsifies the classification (spec, "The guard is
    tier-aware").

    Tier 2 always blocks the uncontrollable groups. For the controllable ones,
    `allow=None` is the spec's baseline -- "block only the uncontrolled
    groups" -- while naming the groups the test actually fakes tightens it to
    SKILL.md's rule, "block only the groups this test does not deliberately
    fake". Pass `allow=()` (env: `TEST_SAFETY_NET_ALLOW=none`) to fake nothing.
    """
    if tier == 1:
        return frozenset(GROUPS)
    if allow is None:
        return frozenset(UNCONTROLLABLE_GROUPS)
    permitted = {g for g in allow if g in CONTROLLABLE_GROUPS}
    return frozenset(g for g in GROUPS if g not in permitted)


def _violate(group, target):
    raise IOGuardViolation(group, target, _state["tier"])


def _guarded(group, target, original):
    def guard(*args, **kwargs):
        if _should_block(group, target):
            _violate(group, target)
        return original(*args, **kwargs)
    guard.__name__ = getattr(original, "__name__", "guarded")
    guard.__doc__ = "test-safety-net io_guard wrapper around %s" % target
    guard.__wrapped__ = original
    return guard


def _patch(obj, attr, group, label=None):
    original = getattr(obj, attr, None)
    if original is None or not callable(original):
        return False
    target = label or "%s.%s" % (getattr(obj, "__name__", "?"), attr)
    setattr(obj, attr, _guarded(group, target, original))
    _undo.append((obj, attr, original))
    return True


def _patch_module(module_name, names, group):
    module = sys.modules.get(module_name)
    if module is None:
        try:
            module = __import__(module_name)
        except Exception:                                   # pragma: no cover
            return
        module = sys.modules.get(module_name, module)
    for name in names:
        _patch(module, name, group, "%s.%s" % (module_name, name))


def _patch_clock():
    import datetime as _dt
    _patch_module("time", _TIME_NAMES, "clock")

    real_datetime, real_date = _dt.datetime, _dt.date

    # `datetime.datetime.now` is a C classmethod: it cannot be reassigned in
    # place, and it does NOT route through `time.time`. Rebinding the module
    # attribute to a guarded subclass is the only interception point Python
    # offers -- and it works precisely because the guard arms ahead of
    # collection, so the unit under test's `from datetime import datetime`
    # binds the guarded class. Caveat, stated rather than implied: while
    # armed, `isinstance(x, datetime.datetime)` is False for objects built
    # before arming, because the name now points at the subclass.
    def _clock_stop(real_cls, method, label):
        def stopper(cls, *args, **kwargs):
            if _should_block("clock", label):
                _violate("clock", label)
            return getattr(real_cls, method)(*args, **kwargs)
        return classmethod(stopper)

    guarded_datetime = type("datetime", (real_datetime,), {
        m: _clock_stop(real_datetime, m, "datetime.datetime." + m)
        for m in ("now", "utcnow", "today", "fromtimestamp")
        if hasattr(real_datetime, m)
    })
    guarded_date = type("date", (real_date,), {
        m: _clock_stop(real_date, m, "datetime.date." + m)
        for m in ("today", "fromtimestamp")
    })
    _dt.datetime, _dt.date = guarded_datetime, guarded_date
    _undo.append((_dt, "datetime", real_datetime))
    _undo.append((_dt, "date", real_date))


def _patch_database():
    for module_name, attr in _DATABASE_TARGETS:
        module = sys.modules.get(module_name)
        if module is not None:
            _patch(module, attr, "database", "%s.%s" % (module_name, attr))


def _guarded_import(original):
    """Patch a database driver the moment the target repo imports it.

    The guard must never import `psycopg2` itself -- that would make a database
    driver a dependency of a stdlib-only skill -- but a unit that imports one
    while the guard is armed must still be intercepted. `__import__` is the one
    hook that sees that happen, and it returns before `from X import connect`
    reads the attribute, so the rebind lands in time.
    """
    db_modules = {name for name, _ in _DATABASE_TARGETS}

    def importer(name, *args, **kwargs):
        module = original(name, *args, **kwargs)
        if _state["armed"] and name.split(".")[0] in db_modules:
            _patch_database()
        return module
    importer.__name__ = "__import__"
    return importer


def arm(tier, allow=None):
    """Install the guard for a proof run at `tier`. Idempotent."""
    global _roots
    if _state["armed"]:
        return
    import builtins
    import codecs
    import io
    import mmap

    _roots = _library_roots()
    _frame_verdicts.clear()
    _state["inside"] = False
    _state["tier"] = tier
    _state["blocked"] = blocked_groups(tier, allow)
    _state["armed"] = True
    blocked = _state["blocked"]

    if "filesystem" in blocked:
        _patch(builtins, "open", "filesystem", "builtins.open")
        for attr in ("open", "open_code", "FileIO"):
            _patch(io, attr, "filesystem", "io.%s" % attr)
        _patch(codecs, "open", "filesystem", "codecs.open")
        _patch(mmap, "mmap", "filesystem", "mmap.mmap")
        for name in _OS_FILESYSTEM:
            _patch(os, name, "filesystem", "os.%s" % name)
    if "environment" in blocked:
        for name in _OS_ENVIRONMENT:
            _patch(os, name, "environment", "os.%s" % name)
    if "randomness" in blocked:
        for name in _OS_RANDOMNESS:
            _patch(os, name, "randomness", "os.%s" % name)
        _patch_module("random", _RANDOM_NAMES, "randomness")
        _patch_module("uuid", _UUID_NAMES, "randomness")
        _patch_module("secrets", _SECRETS_NAMES, "randomness")
    if "clock" in blocked:
        _patch_clock()
    if "network" in blocked:
        _patch_module("socket", _SOCKET_NAMES, "network")
    if "subprocess" in blocked:
        _patch_module("subprocess", _SUBPROCESS_NAMES, "subprocess")
        for name in _OS_SUBPROCESS + tuple(_os_process_family()):
            _patch(os, name, "subprocess", "os.%s" % name)
    if "database" in blocked:
        _patch_database()
        real_import = builtins.__import__
        builtins.__import__ = _guarded_import(real_import)
        _undo.append((builtins, "__import__", real_import))


def disarm():
    """Restore every patched name, innermost patch first. Idempotent."""
    _state["armed"] = False
    _state["inside"] = False
    _frame_verdicts.clear()
    while _undo:
        obj, attr, original = _undo.pop()
        try:
            setattr(obj, attr, original)
        except Exception:                                   # pragma: no cover
            pass
    _state["tier"] = None
    _state["blocked"] = frozenset()


# --------------------------------------------------------------------------
# Environment -> tier, read once at plugin import (spec: "The tier is passed
# per invocation, by environment variable read at plugin import"). The proof
# runs one unit at a time, so a single run has a single tier and the plugin
# needs no per-test dispatch.
# --------------------------------------------------------------------------
TIER_ENV = "TEST_SAFETY_NET_TIER"
ALLOW_ENV = "TEST_SAFETY_NET_ALLOW"


def read_env(environ=None):
    """(tier, allow, notes) from the environment. Never raises.

    An absent or unparseable tier falls back to tier 1 -- the strictest
    setting -- with a note, because the fail-safe direction for a guard is to
    over-block. Silently defaulting to the permissive tier would let a
    misconfigured invocation ship a test that performs real I/O.
    """
    environ = os.environ if environ is None else environ
    notes = []
    raw = (environ.get(TIER_ENV) or "").strip()
    if raw in ("1", "2"):
        tier = int(raw)
    else:
        tier = 1
        notes.append("%s=%r is not 1 or 2; defaulting to tier 1 (block "
                     "everything), the fail-safe direction" % (TIER_ENV, raw))
    raw_allow = environ.get(ALLOW_ENV)
    if raw_allow is None or not raw_allow.strip():
        allow = None
    elif raw_allow.strip().lower() == "none":
        allow = ()
    else:
        wanted = [g.strip() for g in raw_allow.replace(";", ",").split(",")
                  if g.strip()]
        allow = tuple(g for g in wanted if g in CONTROLLABLE_GROUPS)
        unknown = [g for g in wanted if g not in CONTROLLABLE_GROUPS]
        if unknown:
            notes.append("%s names unknown group(s) %s; they are ignored and "
                         "stay blocked" % (ALLOW_ENV, ", ".join(sorted(unknown))))
    if tier == 1 and allow not in (None, ()):
        notes.append("tier 1 ignores %s: a unit that claimed to touch nothing "
                     "gets everything blocked" % ALLOW_ENV)
    return tier, allow, notes


_ENV_TIER, _ENV_ALLOW, _ENV_NOTES = read_env()


# --------------------------------------------------------------------------
# pytest plugin surface
# --------------------------------------------------------------------------
def pytest_configure(config):
    """Arm before collection, which is before any test module is imported.

    This is why the guard is loaded with `-p` and not written into the repo as
    a `conftest.py`: a plugin named on the command line is registered during
    startup, arms ahead of collection, and leaves nothing behind in the user's
    tree -- so Invariant 1 ("never modifies source") holds with no carve-out.
    """
    arm(_ENV_TIER, _ENV_ALLOW)


def pytest_unconfigure(config):
    disarm()


def pytest_report_header(config):
    lines = ["test-safety-net io_guard: tier=%s blocking=%s"
             % (_state["tier"], ",".join(sorted(_state["blocked"])) or "-")]
    lines.extend("test-safety-net io_guard: " + n for n in _ENV_NOTES)
    return lines
