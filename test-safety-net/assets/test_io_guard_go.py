#!/usr/bin/env python3
"""The Go guard's suite: `io_guard_go.py`, run the way a proof run runs it.

`io_guard_go.py` is the SOLE enforcement of this skill's headline invariant --
"never writes a test that performs real I/O" -- on the Go stack. The filter in
`stack_go.py` ranks and declines; this is what makes the promise true. So the
suite asks one question in many shapes: when a unit reaches real I/O under
the documented command, does the run exit 3 and name the right group, and
when it does not, does the run pass untouched?

The first test EXTRACTS the documented command from every document that
prints it and runs it verbatim, for the reason the node suite records: the
Python guard's suite once ran `python -m pytest` while every document printed
`pytest`, and 122 unit tests were green over an invocation broken three ways.

Every test that needs a real `go` skips VISIBLY without one -- the run reads
`OK (skipped=N)`. CI pins go (`.github/workflows/skill-gates.yml`), so there
the skip cannot happen by accident. Fixture modules live under
`tempfile.mkdtemp()`; nothing is written into the repo, and no fixture
listens on or reaches a real host.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
GUARD = os.path.join(HERE, "io_guard_go.py")
GO = shutil.which("go")
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


io_guard_go = _load("io_guard_go")
stack_go = _load("stack_go")

GO_MOD = "module example.com/fx\n\ngo 1.22\n"

UNITS = r'''package fx

import (
	"bufio"
	"database/sql"
	"fmt"
	"math/rand/v2"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"text/template"
	"time"
)

// A goroutine started with `go <stdlib call>` runs a stack with NO repo frame
// on it: the compiler's wrapper closure is hidden from runtime.Callers.
func Serve() { go http.ListenAndServe("127.0.0.1:0", nil) }

func ClearAll() { go os.Clearenv() }

// Forty nested templates put the unit's frame far outside a fixed 64-frame walk.
func DeepEnv() string {
	t := template.New("t0").Funcs(template.FuncMap{"env": os.Getenv})
	src := `{{define "t40"}}{{env "HOME"}}{{end}}`
	for i := 39; i >= 0; i-- {
		src += fmt.Sprintf(`{{define "t%d"}}{{template "t%d"}}{{end}}`, i, i+1)
	}
	template.Must(t.Parse(src))
	var b strings.Builder
	_ = t.ExecuteTemplate(&b, "t0", nil)
	return b.String()
}

func Add(a, b int) int { return a + b }

func ReadHosts() int { b, _ := os.ReadFile("/etc/hosts"); return len(b) }

func Dial() error {
	c, err := net.Dial("tcp", "127.0.0.1:1")
	if err == nil {
		c.Close()
	}
	return err
}

func Run() error { return exec.Command("true").Run() }

func Home() string { return os.Getenv("HOME") }

func Stamp() int64 { return time.Now().Unix() }

func Roll() int { return rand.IntN(6) }

func Open() error { _, err := sql.Open("nosuchdriver", ""); return err }

func Swallow() (n int) {
	defer func() { recover() }()
	return len(Home())
}

func InGoroutine() string {
	ch := make(chan string)
	go func() { ch <- Home() }()
	return <-ch
}

func ReadLine() string {
	s, _ := bufio.NewReader(os.Stdin).ReadString('\n')
	return s
}

func WriteInto(dir string) error {
	return os.WriteFile(filepath.Join(dir, "x"), []byte("x"), 0o644)
}
'''

TESTS = r'''package fx

import (
	"fmt"
	"testing"
	"time"
)

func TestClean(t *testing.T) {
	fmt.Println("printing is fine")
	_ = 2 * time.Second
	if Add(2, 3) != 5 {
		t.Fatal("bad")
	}
}

func TestWrong(t *testing.T) {
	if Add(2, 3) != 6 {
		t.Fatal("deliberately wrong expected value")
	}
}

func TestReadHosts(t *testing.T) { ReadHosts() }
func TestDial(t *testing.T)      { Dial() }
func TestRun(t *testing.T)       { Run() }
func TestHome(t *testing.T)      { Home() }
func TestStamp(t *testing.T)     { Stamp() }
func TestRoll(t *testing.T)      { Roll() }
func TestOpen(t *testing.T)      { Open() }
func TestSwallow(t *testing.T)   { Swallow() }
func TestGoroutine(t *testing.T) { InGoroutine() }
func TestReadLine(t *testing.T)  { ReadLine() }

func TestTempDir(t *testing.T) {
	if err := WriteInto(t.TempDir()); err != nil {
		t.Fatal(err)
	}
}

// The goroutine has to get as far as its first call before the test returns;
// time.Sleep is not hooked (it has no Go body), so waiting touches nothing.
func TestServe(t *testing.T)    { Serve(); time.Sleep(300 * time.Millisecond) }
func TestClearAll(t *testing.T) { ClearAll(); time.Sleep(300 * time.Millisecond) }
func TestDeepEnv(t *testing.T)  { DeepEnv() }
func TestSkips(t *testing.T)    { t.Skip("a skipped test proves nothing") }

func TestOnlyTempDir(t *testing.T) { _ = t.TempDir() }
func TestOnlySetenv(t *testing.T)  { t.Setenv("TSN_FIXTURE", "1") }

func TestPanics(t *testing.T) { panic("boom") }

func TestDeferredIOWhilePanicking(t *testing.T) {
	defer func() { Home() }()
	panic("boom")
}
'''


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("TEST_SAFETY_NET")}
    env.pop("GOFLAGS", None)
    env.update(extra)
    return env


def run_guard(cwd, tier, allow, *args, **extra_env):
    """(exit code, combined output) for one guarded proof run."""
    env = _clean_env(**extra_env)
    if tier is not None:
        env["TEST_SAFETY_NET_TIER"] = str(tier)
    if allow is not None:
        env["TEST_SAFETY_NET_ALLOW"] = allow
    proc = subprocess.run([sys.executable, GUARD, *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=300,
                          stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout + proc.stderr


_LABEL = re.compile(r"reached (\w+) I/O")


def label(out):
    m = _LABEL.search(out)
    return m.group(1) if m else None


def _need_go():
    if not GO:
        raise unittest.SkipTest("no `go` on PATH: io_guard_go.py is NOT exercised on "
                                "this machine. Install go 1.26 to run it.")


class FixtureModule(unittest.TestCase):
    """One module shared by a class, so the overlaid std builds once per class."""

    @classmethod
    def setUpClass(cls):
        _need_go()
        cls.mod = tempfile.mkdtemp(prefix="tsn-go-guard-")
        write(cls.mod, "go.mod", GO_MOD)
        write(cls.mod, "fx.go", UNITS)
        write(cls.mod, "fx_test.go", TESTS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.mod, ignore_errors=True)

    def guard(self, tier, allow, test, *extra, **env):
        return run_guard(self.mod, tier, allow, "-run", "^%s$" % test, *extra, "./", **env)


# ── 1. The documented command, extracted and run verbatim ────────────────

def extract_commands():
    """Every `TEST_SAFETY_NET_TIER=N ... python3 ... io_guard_go.py ...` line the
    skill prints -- in the guard's own header and in every markdown file --
    with comment leaders stripped and backslash continuations joined."""
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
            if re.match(r"^TEST_SAFETY_NET_TIER=[12] .*python3 .*io_guard_go\.py", line):
                out.append((os.path.relpath(path, SKILL), re.sub(r"\s+", " ", line)))
    return sorted(set(out))


class TestTheDocumentedCommand(FixtureModule):
    def test_every_documented_command_passes_clean_and_trips_on_real_io(self):
        commands = extract_commands()
        self.assertTrue(commands, "no documented io_guard_go.py invocation found")
        failures = []
        for where, command in commands:
            for test, want in (("TestClean", 0), ("TestDial", 3)):
                cmd = command.replace("<test_name>", test).replace("<package>", "./")
                proc = subprocess.run(["/bin/sh", "-c", cmd], cwd=self.mod,
                                      env=_clean_env(SKILL_DIR=SKILL),
                                      capture_output=True, text=True, timeout=300,
                                      stdin=subprocess.DEVNULL)
                if proc.returncode != want:
                    failures.append("%s: %s -> exit %d (want %d): %s"
                                    % (where, cmd, proc.returncode, want,
                                       (proc.stdout + proc.stderr).strip()[-300:]))
        self.assertEqual(failures, [])


# ── 2. Every group trips at tier 1, in its OWN group ─────────────────────

class TestEveryGroupTrips(FixtureModule):
    def test_a_clean_unit_passes_at_tier_1(self):
        # The runner's own clock, the runner's own printing: exempt, because no
        # frame of the target repo is on those stacks.
        code, out = self.guard(1, None, "TestClean")
        self.assertEqual(code, 0, out[-600:])
        self.assertNotIn("IOGuardViolation", out)

    def test_each_group_trips_and_is_named(self):
        cases = (("TestReadHosts", "filesystem"), ("TestDial", "network"),
                 ("TestRun", "subprocess"), ("TestHome", "environment"),
                 ("TestStamp", "clock"), ("TestRoll", "randomness"),
                 ("TestOpen", "database"))
        for test, group in cases:
            with self.subTest(test):
                code, out = self.guard(1, None, test)
                self.assertEqual(code, 3, out[-600:])
                self.assertIn("IOGuardViolation", out)
                # `net.Dial` reads the clock before it opens a socket, so this
                # is the assertion that proves the ENTRY-group rule: the label
                # must say network, not clock.
                self.assertEqual(label(out), group, out[-600:])


# ── 3. Tier 2 permits exactly what it names ──────────────────────────────

class TestTierTwo(FixtureModule):
    def test_t_tempdir_is_the_filesystem_control(self):
        # t.TempDir() reads TMPDIR on its way to the filesystem. Without the
        # testing-control override that is an `environment` trip, and the
        # canonical tier-2 filesystem pin could never pass.
        code, out = self.guard(2, "filesystem", "TestTempDir")
        self.assertEqual(code, 0, out[-600:])

    def test_the_same_test_trips_when_filesystem_is_not_named(self):
        code, out = self.guard(2, "clock", "TestTempDir")
        self.assertEqual(code, 3, out[-600:])
        self.assertEqual(label(out), "filesystem")

    def test_network_is_never_permitted(self):
        code, out = self.guard(2, "filesystem,clock,environment", "TestDial")
        self.assertEqual(code, 3, out[-600:])
        self.assertEqual(label(out), "network")

    def test_randomness_cannot_be_allowed_on_go(self):
        code, out = self.guard(2, "randomness", "TestRoll")
        self.assertEqual(code, 3, out[-600:])
        self.assertIn("not controllable on go", out)

    def test_a_named_group_passes(self):
        code, out = self.guard(2, "clock", "TestStamp")
        self.assertEqual(code, 0, out[-600:])

    def test_a_cached_pass_is_never_replayed_across_tiers(self):
        # The engine reads its configuration through runtime_envs(), which
        # go test's result cache cannot see. Without -count=1 the tier-1 run
        # below would print `ok (cached)` from the tier-2 run above and exit 0
        # over a unit that reads the clock.
        code, out = self.guard(2, "clock", "TestStamp")
        self.assertEqual(code, 0, out[-600:])
        code, out = self.guard(1, None, "TestStamp")
        self.assertEqual(code, 3, out[-600:])
        self.assertNotIn("(cached)", out)


class TestTestingControls(FixtureModule):
    """testing's own helpers that do I/O FOR the test are judged as that I/O.

    Every other `testing` frame exempts the call as the runner's own work, so
    a control missing from the table would fail PERMISSIVELY -- a tier-1 test
    creating a directory would pass. These tests use the control and nothing
    else, so nothing downstream of it can trip in its place: the earlier
    TempDir test also writes a file, and the write trips whatever the control
    table says.
    """

    def test_t_tempdir_alone_trips_at_tier_1(self):
        code, out = self.guard(1, None, "TestOnlyTempDir")
        self.assertEqual(code, 3, out[-600:])
        self.assertEqual(label(out), "filesystem")

    def test_t_setenv_alone_trips_at_tier_1(self):
        code, out = self.guard(1, None, "TestOnlySetenv")
        self.assertEqual(code, 3, out[-600:])
        self.assertEqual(label(out), "environment")

    def test_t_setenv_is_the_environment_control(self):
        code, out = self.guard(2, "environment", "TestOnlySetenv")
        self.assertEqual(code, 0, out[-600:])

    def test_every_testing_method_is_classified(self):
        # DERIVED from the installed toolchain, like the syscall table. A Go
        # release that adds a `testing` helper fails here until it is put in
        # one table or the other -- deliberately, because an unclassified
        # helper that does I/O would otherwise be exempt as runner work.
        env = dict(os.environ, GOTOOLCHAIN="local")
        out = subprocess.run([GO, "doc", "-all", "testing"], env=env, capture_output=True,
                             text=True, timeout=120).stdout
        methods = set(re.findall(r"(?m)^func \([a-z] \*(?:common|T|B|F)\) ([A-Z][A-Za-z0-9_]*)",
                                 out))
        self.assertGreater(len(methods), 20)
        known = set(io_guard_go.TESTING_CONTROL_METHODS) | set(io_guard_go.TESTING_NO_IO)
        self.assertEqual(sorted(methods - known), [])
        self.assertEqual(set(io_guard_go.TESTING_CONTROL_METHODS) & set(io_guard_go.TESTING_NO_IO),
                         set())


# ── 4. What JavaScript and Python could not guarantee, Go can ────────────

class TestUncatchable(FixtureModule):
    def test_recover_does_not_swallow_a_trip(self):
        code, out = self.guard(1, None, "TestSwallow")
        self.assertEqual(code, 3, out[-600:])

    def test_a_trip_on_a_goroutine_ends_the_run(self):
        code, out = self.guard(1, None, "TestGoroutine")
        self.assertEqual(code, 3, out[-600:])
        self.assertIn("fx.Home", out)

    def test_trimpath_changes_nothing(self):
        # Provenance reads a frame's PACKAGE path, not its file path, which
        # -trimpath rewrites.
        code, out = self.guard(1, None, "TestHome", GOFLAGS="-trimpath")
        self.assertEqual(code, 3, out[-600:])
        code, out = self.guard(1, None, "TestClean", GOFLAGS="-trimpath")
        self.assertEqual(code, 0, out[-600:])


class TestNoRepoFrameOnTheStack(FixtureModule):
    """Stacks the provenance walk cannot see the unit on, which it used to EXEMPT.

    Each was a GREEN tier-1 proof over real I/O, shown by review: a goroutine
    started with `go <stdlib func>` (its wrapper closure is hidden from
    runtime.Callers, so the stack is all stdlib and its bottom looks like the
    runtime's own work), and a stack deeper than the walk's fixed buffer,
    whose cut-off tail read as "end of stack".
    """

    def test_go_on_a_stdlib_server_trips(self):
        code, out = self.guard(1, None, "TestServe")
        self.assertEqual(code, 3, out[-600:])
        self.assertEqual(label(out), "network")

    def test_go_on_a_stdlib_env_mutation_trips(self):
        code, out = self.guard(1, None, "TestClearAll")
        self.assertEqual(code, 3, out[-600:])
        self.assertEqual(label(out), "environment")

    def test_a_stack_deeper_than_any_fixed_buffer_still_trips(self):
        code, out = self.guard(1, None, "TestDeepEnv")
        self.assertEqual(code, 3, out[-600:])
        self.assertEqual(label(out), "environment")


class TestImportTime(unittest.TestCase):
    def test_init_io_trips_and_is_attributed_to_init(self):
        _need_go()
        mod = tempfile.mkdtemp(prefix="tsn-go-init-")
        self.addCleanup(shutil.rmtree, mod, ignore_errors=True)
        write(mod, "go.mod", GO_MOD)
        write(mod, "boot.go",
              'package fx\n\nimport "os"\n\nvar mode = os.Getenv("MODE")\n\n'
              "func Double(n int) int { return n * 2 }\n")
        write(mod, "boot_test.go",
              'package fx\n\nimport "testing"\n\n'
              "func TestDouble(t *testing.T) { if Double(2) != 4 { t.Fatal(mode) } }\n")
        code, out = run_guard(mod, 1, None, "-run", "^TestDouble$", "./")
        self.assertEqual(code, 3, out[-600:])
        self.assertIn("fx.init", out)


# ── 5. Terminal input ─────────────────────────────────────────────────────

class TestStdin(FixtureModule):
    def test_reading_stdin_trips_at_tier_1(self):
        code, out = self.guard(1, None, "TestReadLine")
        self.assertEqual(code, 3, out[-600:])
        self.assertEqual(label(out), "stdin")

    def test_at_tier_2_it_reads_end_of_file_instead_of_hanging(self):
        # stdin is outside the groups, so tier 2 does not block it; the
        # wrapper hands the run /dev/null so a read cannot wait forever.
        code, out = self.guard(2, "filesystem", "TestReadLine")
        self.assertEqual(code, 0, out[-600:])


# ── 6. The exit contract ─────────────────────────────────────────────────

class TestExitContract(FixtureModule):
    def test_an_assertion_failure_is_1(self):
        # The RED half of every proof. `t.Fatal` reaches runtime.Goexit, which
        # runs the runner's deferred `time.Since` with `Fatal <- TestWrong`
        # still beneath it; judged by the first repo frame alone, this tripped
        # as `clock` -- every RED run a guard trip.
        code, out = self.guard(1, None, "TestWrong")
        self.assertEqual(code, 1, out[-600:])
        self.assertNotIn("IOGuardViolation", out)

    def test_a_panicking_test_is_red_not_a_trip(self):
        code, out = self.guard(1, None, "TestPanics")
        self.assertEqual(code, 1, out[-600:])
        self.assertNotIn("IOGuardViolation", out)

    def test_io_in_a_repo_defer_while_panicking_still_trips(self):
        # The runner-own-work exemption must not swallow REPO code that runs
        # during the unwind: the deferred closure is innermost, before any
        # testing frame is reached.
        code, out = self.guard(1, None, "TestDeferredIOWhilePanicking")
        self.assertEqual(code, 3, out[-600:])
        self.assertEqual(label(out), "environment")

    def test_a_name_that_matches_no_test_is_4(self):
        code, out = self.guard(1, None, "TestNoSuchTest")
        self.assertEqual(code, 4, out[-600:])

    def test_a_skipped_test_is_4_not_green(self):
        # A pinned test that skips itself asserted nothing; exit 0 would let
        # the proof loop keep it.
        code, out = self.guard(1, None, "TestSkips")
        self.assertEqual(code, 4, out[-600:])

    def test_a_package_with_no_test_files_is_4_not_green(self):
        mod = tempfile.mkdtemp(prefix="tsn-go-notests-")
        self.addCleanup(shutil.rmtree, mod, ignore_errors=True)
        write(mod, "go.mod", GO_MOD)
        write(mod, "x.go", "package fx\n\nfunc X() int { return 1 }\n")
        code, out = run_guard(mod, 1, None, "-run", "^TestX$", "./")
        self.assertEqual(code, 4, out[-600:])

    def test_a_split_count_flag_is_stripped_whole(self):
        # `-count 3` used to lose only the flag, leaving `3` as a package
        # argument and a NO BUILD for a test that was fine.
        code, out = self.guard(1, None, "TestClean", "-count", "3")
        self.assertEqual(code, 0, out[-600:])

    def test_a_caller_supplied_overlay_is_refused_with_2(self):
        code, out = run_guard(self.mod, 1, None, "-overlay", "/tmp/x.json",
                              "-run", "^TestClean$", "./")
        self.assertEqual(code, 2, out[-600:])
        code, out = self.guard(1, None, "TestClean", GOFLAGS="-overlay=/tmp/x.json")
        self.assertEqual(code, 2, out[-600:])

    def test_an_unsupported_platform_is_refused_with_2(self):
        code, out = self.guard(1, None, "TestClean", GOOS="windows")
        self.assertEqual(code, 2, out[-600:])

    def test_a_package_that_does_not_build_is_5(self):
        mod = tempfile.mkdtemp(prefix="tsn-go-broken-")
        self.addCleanup(shutil.rmtree, mod, ignore_errors=True)
        write(mod, "go.mod", GO_MOD)
        write(mod, "x.go", "package fx\n\nfunc X() int { return 1 }\n")
        write(mod, "x_test.go", 'package fx\n\nimport "testing"\n\n'
                                "func TestX(t *testing.T) { X( }\n")
        code, out = run_guard(mod, 1, None, "-run", "^TestX$", "./")
        self.assertEqual(code, 5, out[-600:])


# ── 7. Third-party code is attributable, like the repo's own ─────────────

class TestThirdParty(unittest.TestCase):
    def test_a_dependency_doing_io_for_the_unit_trips(self):
        _need_go()
        work = tempfile.mkdtemp(prefix="tsn-go-dep-")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        write(work, "dep/go.mod", "module example.com/dep\n\ngo 1.22\n")
        write(work, "dep/dep.go",
              'package dep\n\nimport "os"\n\n'
              'func Read() int { b, _ := os.ReadFile("/etc/hosts"); return len(b) }\n')
        write(work, "app/go.mod",
              "module example.com/app\n\ngo 1.22\n\nrequire example.com/dep v0.0.0\n\n"
              "replace example.com/dep => ../dep\n")
        write(work, "app/app.go",
              'package app\n\nimport "example.com/dep"\n\nfunc Size() int { return dep.Read() }\n')
        write(work, "app/app_test.go",
              'package app\n\nimport "testing"\n\nfunc TestSize(t *testing.T) { Size() }\n')
        code, out = run_guard(os.path.join(work, "app"), 1, None, "-run", "^TestSize$", "./")
        self.assertEqual(code, 3, out[-600:])
        self.assertEqual(label(out), "filesystem")

    def test_a_dependencys_import_time_io_names_the_dependency(self):
        # The whole binary runs guarded, so a dependency whose package-level
        # initializer reads the environment trips EVERY tier-1 unit in any
        # package importing it. That is the right verdict -- nothing in the
        # package can be proved at tier 1 -- but it is not a verdict on the
        # unit, and a message saying "reclassify the unit" sends the agent to
        # the wrong place.
        _need_go()
        work = tempfile.mkdtemp(prefix="tsn-go-depinit-")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        write(work, "dep/go.mod", "module example.com/dep\n\ngo 1.22\n")
        write(work, "dep/dep.go",
              'package dep\n\nimport "os"\n\nvar home = os.Getenv("HOME")\n\n'
              'func Hello() string { return "hi" }\n')
        write(work, "app/go.mod",
              "module example.com/app\n\ngo 1.22\n\nrequire example.com/dep v0.0.0\n\n"
              "replace example.com/dep => ../dep\n")
        write(work, "app/app.go",
              'package app\n\nimport "example.com/dep"\n\nfunc Greet() string { return dep.Hello() }\n')
        write(work, "app/app_test.go",
              'package app\n\nimport "testing"\n\nfunc TestGreet(t *testing.T) { Greet() }\n')
        code, out = run_guard(os.path.join(work, "app"), 1, None, "-run", "^TestGreet$", "./")
        self.assertEqual(code, 3, out[-600:])
        self.assertIn("dependency", out)
        self.assertIn("example.com/dep", out)


# ── 8. Pure-Python halves: the env contract, the tables, the injector ────

class TestEnvContract(unittest.TestCase):
    """The same contract `io_guard.py` and `io_guard.js` read, byte for byte in
    behaviour: learn it once, on any stack."""

    def test_an_absent_or_invalid_tier_is_tier_1_with_a_note(self):
        for raw in (None, "", "3", "one"):
            env = {} if raw is None else {"TEST_SAFETY_NET_TIER": raw}
            tier, _allow, notes = io_guard_go.read_env(env)
            self.assertEqual(tier, 1, raw)
            self.assertTrue(notes, raw)

    def test_allow_parsing(self):
        _t, allow, _n = io_guard_go.read_env({"TEST_SAFETY_NET_TIER": "2"})
        self.assertIsNone(allow)
        _t, allow, _n = io_guard_go.read_env({"TEST_SAFETY_NET_TIER": "2",
                                              "TEST_SAFETY_NET_ALLOW": "none"})
        self.assertEqual(allow, [])
        _t, allow, notes = io_guard_go.read_env({"TEST_SAFETY_NET_TIER": "2",
                                                 "TEST_SAFETY_NET_ALLOW": "filesystem,bogus"})
        self.assertEqual(allow, ["filesystem"])
        self.assertTrue(any("bogus" in n for n in notes))

    def test_randomness_is_named_as_uncontrollable_on_go(self):
        _t, allow, notes = io_guard_go.read_env({"TEST_SAFETY_NET_TIER": "2",
                                                 "TEST_SAFETY_NET_ALLOW": "randomness"})
        self.assertEqual(allow, [])
        self.assertTrue(any("not controllable on go" in n for n in notes))

    def test_blocked_groups(self):
        every = set(stack_go.GROUPS)
        self.assertEqual(io_guard_go.blocked_groups(1, ["filesystem"]), every)
        self.assertEqual(io_guard_go.blocked_groups(2, None), set(stack_go.UNCONTROLLABLE))
        self.assertEqual(io_guard_go.blocked_groups(2, ["filesystem"]),
                         every - {"filesystem"})

    def test_the_guard_uses_the_filters_groups_verbatim(self):
        self.assertEqual(set(io_guard_go.GROUPS), set(stack_go.GROUPS))


class TestTheTwoLayersAgree(unittest.TestCase):
    def test_every_filter_marker_is_accounted_for_exactly_once(self):
        markers = {m for table in (stack_go.CONTROLLABLE, stack_go.UNCONTROLLABLE)
                   for ms in table.values() for m in ms}
        maps = (io_guard_go.FILTER_MARKER_INTERCEPTS, io_guard_go.PARTIALLY_INTERCEPTED,
                io_guard_go.NOT_INTERCEPTED)
        problems = []
        for m in sorted(markers):
            homes = [i for i, table in enumerate(maps) if m in table]
            if len(homes) != 1:
                problems.append("%s in %d tables" % (m, len(homes)))
            elif not str(maps[homes[0]][m]).strip():
                problems.append("%s has no reason" % m)
        self.assertEqual(problems, [])

    def test_every_classified_syscall_is_a_hook_target(self):
        wanted = {n for n, g in stack_go.SYSCALL_GROUPS.items()
                  if g in stack_go.GROUPS or g == "stdin"}
        self.assertEqual(io_guard_go.syscall_targets(), wanted)


class TestInjection(unittest.TestCase):
    STMT = 'tsnHook("filesystem", "syscall.Open")'

    def test_the_hook_lands_first_in_the_body(self):
        src = "package syscall\n\nfunc Open(path string, mode int, perm uint32) (fd int, err error) {\n\treturn open(path)\n}\n"
        out = io_guard_go.inject(src, "Open", self.STMT)
        self.assertIn("(fd int, err error) {\n\t%s\n\treturn open(path)" % self.STMT, out)

    def test_a_longer_name_sharing_the_prefix_is_not_the_target(self):
        src = "package os\n\nfunc OpenFile(n string) {}\n\nfunc Open(n string) { OpenFile(n) }\n"
        out = io_guard_go.inject(src, "Open", self.STMT)
        self.assertIn("func OpenFile(n string) {}", out)
        self.assertIn("func Open(n string) {\n\t%s\n OpenFile(n) }" % self.STMT, out)

    def test_multi_line_generic_and_method_signatures(self):
        src = ("package x\n\nfunc N[Int intType](\n\tn Int,\n) Int {\n\treturn n\n}\n\n"
               "func (c *Client) Do(req *Request) (*Response, error) {\n\treturn nil, nil\n}\n")
        out = io_guard_go.inject(src, "N", self.STMT)
        self.assertIn(") Int {\n\t%s\n\treturn n" % self.STMT, out)
        out = io_guard_go.inject(src, "Do", self.STMT, recv="Client")
        self.assertIn("(*Response, error) {\n\t%s\n\treturn nil, nil" % self.STMT, out)

    def test_an_interface_result_type_is_not_the_body(self):
        src = "package x\n\nfunc V() interface{ M() } {\n\treturn nil\n}\n"
        out = io_guard_go.inject(src, "V", self.STMT)
        self.assertIn("interface{ M() } {\n\t%s\n\treturn nil" % self.STMT, out)

    def test_a_declaration_with_no_body_is_reported_not_patched(self):
        src = "package time\n\n// Sleep is provided by the runtime.\nfunc Sleep(d Duration)\n"
        self.assertIsNone(io_guard_go.inject(src, "Sleep", self.STMT))

    def test_a_missing_target_raises(self):
        with self.assertRaises(KeyError):
            io_guard_go.inject("package x\n\nfunc Other() {}\n", "Open", self.STMT)


if __name__ == "__main__":
    unittest.main()
