# test-safety-net: Go stack — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Go the third stack test-safety-net covers end to end: detect → discover → triage → rank → write → **prove under a runtime guard**.

**Architecture:**
- `stack_go.py` fills the stack interface. Discovery is hybrid:
  - a Python heuristic reader, which always works;
  - a `go/ast` helper run with the machine's own `go`, which is precise.
- Triage is package-scoped, because a Go package spans every file in its directory.
- The runtime guard is **not** the OS sandbox D2 approved. It is `io_guard_go.py`, a wrapper around `go test` that:
  - generates hook files from the installed GOROOT;
  - injects them into the stdlib through `go test -overlay`;
  - decides every hooked call by **call provenance** (`runtime.Callers`), the same rule `io_guard.js` uses.

**Tech Stack:** Python 3 stdlib for the ranker, the stack and the guard wrapper. Go 1.26 for the proof runs; CI pins it. No new dependencies anywhere.

**Spec:** `docs/superpowers/specs/2026-09-09-test-safety-net-multistack-design.md`, including its 2026-09-10 amendment (the OS sandbox spike) and its 2026-09-12 amendment (this plan's guard decision).

## Global Constraints

**Dependencies and toolchain**
- **Stdlib-only Python, no pip, no network** for anything the ranker or the guard wrapper needs.
- **No `.go` file is committed to the repo.** Both Go programs this plan ships — the precise-discovery helper and the guard engine template — live as Python string constants, the way `stack_node._PRECISE_JS` does. A committed `.go` file would be a Go source file in this repo's own corpus.
- **The toolchain is never downloaded.** Every `go` invocation this skill makes sets `GOTOOLCHAIN=local`, and the guard's proof run also sets `GOPROXY=off`. A missing module is reported, never fetched.

**Invariants**
- **Zero behaviour change for python and node.** The existing suites pass unmodified, except for tests that pin the registry's exact contents, which this plan edits on purpose and names where it does. Task 1's interface widening is proven byte-identical over real trees, old ranker against new.
- **The filter may under-credit, never over-credit** — reach, coverage or safety. When testability is uncertain, the tier goes *up*.
- **The report names which discovery path ran** (`precise` | `heuristic`).
- **The guard fails closed.** If it cannot arm — unsupported GOOS, a required hook target not found, a caller-supplied `-overlay`, no `go` — it exits `2` and runs nothing. A proof that ran unguarded is worse than no proof.

**Conventions and bookkeeping**
- **Every `references/`/`assets/` path named in SKILL.md must exist**, and helpers are invoked as `"$SKILL_DIR/assets/..."`.
- **Every new guardrail gets an `ab-validate` row** under a new `SINCE_TSN_GO*` constant.
- **Every shipped `test_*.sh` carries a `# gate:` marker.** This plan ships Python suites instead, which need none.
- **Rust stays declined** after this plan. Every document that says "go and rust are declined" is edited to say rust alone.

---

## Design decisions this plan locks in

These are the answers the node campaign had to discover mid-flight. They are fixed here so tasks do not relitigate them.

### Units
Three shapes count:
- An exported top-level `func Name(...)` → kind `"function"`.
- An exported method on an exported receiver type, `func (r *T) Name(...)` → name `"T.Name"`, kind `"method"`.
- Nothing for types themselves. A Go type has no constructor body, so its behaviour lives in its methods and its `New…` functions, and those are the units.

Three kinds of file yield no units:
- `_test.go` files;
- files whose first comment block matches `^// Code generated .* DO NOT EDIT\.$`;
- files constrained `//go:build ignore` or `//go:build tools`.

`testdata/`, `vendor/` and any directory starting with `_` or `.` are skipped whole. That is the rule the `go` tool itself applies.

### Package scope
A Go package is a directory, and every file in it shares one scope. Two interface consequences follow:
- `module_of(rel)` is the **directory basename**, the name importers write. A repo-root file answers its own stem instead: `module_of` receives no root, so it cannot read the module path. That makes a root file under-credited, never over-credited.
- `inbound_refs` currently counts bare occurrences in the unit's **own file** only, and drops every other file with the same `module_of`. For Go that throws away every caller in a sibling file of the same package — the commonest call site there is. **Task 1 adds a fifteenth interface name, `scope_files(rel, all_files)`**: the files whose bare occurrences count as the unit's own scope. python and node answer `[rel]`, so their behaviour is unchanged; Go answers every `.go` file in the directory.

### Controllable groups on Go
**`filesystem`** (`t.TempDir`), **`clock`** (`testing/synctest`, Go 1.25+), **`environment`** (`t.Setenv`).
**`randomness` is UNCONTROLLABLE on Go.** Since Go 1.24, `rand.Seed` is a no-op unless the process sets `GODEBUG=randseednop=0`, and the top-level `math/rand` and `math/rand/v2` functions cannot be seeded at all. So a unit drawing from the global source needs a seam — an injected `*rand.Rand` — before a characterization test is honest. The seven group NAMES stay the same as python and node; only which side of the line `randomness` sits on differs, and the stack's own tables are the authority for that.

### Static declines outside the groups
Two constructions go to Tier 4 before any marker is read, because neither the filter nor the guard can see what they do. This is the Go counterpart of Python's `os.exec*` static decline.
- **cgo** — a `C.` reference reachable from the unit.
- **Raw syscalls** — `syscall.Syscall`/`Syscall6`/`Syscall9`/`RawSyscall`/`RawSyscall6`/`AllThreadsSyscall`/`AllThreadsSyscall6` and the `golang.org/x/sys/unix` `Syscall*`/`RawSyscall*` family.

### The guard's decision rule
When a hooked stdlib function is entered, the engine walks `runtime.Callers` from the innermost frame outward:
1. **Stdlib frames are transparent.** `go list std`, baked into the overlay, defines the stdlib. As the walk passes, it records the **entry** frame: the last stdlib frame before the first non-stdlib one, meaning the stdlib function the code under test actually called.
2. **A frame in package `main` whose file is `_testmain.go`** is the runner `go test` generates. Reaching it exempts the call.
3. **The first other frame is attributable** — the repo's own code or a third-party module. Third-party is treated like the repo, as `io_guard.js` treats `node_modules`. The call is judged there.
4. **Reaching the end of the stack exempts the call.** The runtime did it on its own behalf: `testing` reading the clock, `go test`'s own setup.

A call is blocked when either of these is blocked:
- **The primitive's group.** `time.Now` is `clock`.
- **The entry frame's group.** `net.Dial` is `network`, even though the first primitive it reaches is `time.Now`. The spike showed exactly that: `net.Dial` tripped as `clock`.

The label names the entry group when that is the blocked one. **Testing controls override** rather than add: an entry frame of `testing.(*common).TempDir`/`ArtifactDir` means `{filesystem}` only, `Setenv` means `{environment}`, and `Chdir` means `{filesystem, environment}`. Without that, `t.TempDir()` trips as `environment` (it reads `TMPDIR`) at tier 2 with `filesystem` allowed.

A trip writes `IOGuardViolation: ...` to fd 2, then calls `syscall.Exit(3)`. `recover()` cannot swallow it, and a violation on another goroutine ends the process just the same. The spike verified both.

### The wrapper's exit contract
The proof loop reads **only the exit status**. That rule is node's, and here it is made total:

| exit | meaning | proof-loop response |
|---|---|---|
| 0 | GREEN — the selected test ran and passed | keep, if this was the corrected run |
| 1 | RED — the selected test ran and failed an assertion | expected on the deliberately-wrong run |
| 2 | the guard could not arm | fix the environment; nothing was proved |
| 3 | GUARD TRIP — `IOGuardViolation` | classification wrong: reclassify Tier 3, discard, red or green |
| 4 | no test matched `-run` | fix the name; a zero-test run is not GREEN |
| 5 | the package did not build (`[build failed]`/`[setup failed]`) | fix the test source; a compile error is not RED |

### Why an overlay costs nothing to cache
Measured on go 1.26.7/darwin: a cold build (fresh `GOCACHE`) with `syscall`, `os` and `time` hooked takes **5s**. A warm build takes **0s**, even when the overlay directory moves, because the build cache keys on content, not on path. The overlay is therefore written to a fresh `tempfile.mkdtemp()` on every run, with no cache directory to manage. Tier and allow reach the engine by environment variable, never baked into the source, so all tiers share one build.

---

## File structure

| file | responsibility |
|---|---|
| `test-safety-net/assets/rank_risk.py` (modify) | registry gains `stack_go`; `inbound_refs` asks `stack.scope_files`; docstring says fifteen names |
| `test-safety-net/assets/stack_python.py`, `stack_node.py` (modify) | `scope_files(rel, all_files) -> [rel]` |
| `test-safety-net/assets/stack_go.py` (create) | the Go stack: identity, files, naming, lexing, grammar, stripper, both discovery paths, marker tables, triage |
| `test-safety-net/assets/io_guard_go.py` (create) | the guard: env contract, overlay generation from GOROOT, the embedded Go engine, `go test` invocation, exit mapping |
| `test-safety-net/assets/test_stack_go.py` (create) | stdlib suite for `stack_go` (discovery, grammar, triage, detection, precise path) |
| `test-safety-net/assets/test_io_guard_go.py` (create) | stdlib suite for the guard: the documented command extracted and run verbatim, both directions, and every rule above; skips visibly without `go` |
| `test-safety-net/assets/test_stack_node.py` (modify) | registry assertion names three stacks; Go rows join the case table |
| `test-safety-net/eval/run_eval.py` (modify) | checks 40–44 |
| `scripts/ab-validate.py` (modify) | `SINCE_TSN_GO*`, `check_test_safety_net_go*` |
| `test-safety-net/SKILL.md`, `README.md`, `references/{stacks,triage,parameters}.md` (modify) | the Go row, the guard block, the exit table, the residuals |
| `.github/workflows/skill-gates.yml` (modify) | `actions/setup-go@v5`, `go-version: "1.26"`, `cache: false` |

---

### Task 1: `scope_files` joins the interface (zero behaviour change for python and node)

**Files:**
- Modify: `test-safety-net/assets/rank_risk.py` (`inbound_refs`, module docstring)
- Modify: `test-safety-net/assets/stack_python.py`, `test-safety-net/assets/stack_node.py`
- Test: `test-safety-net/assets/test_rank_risk.py` — **one new test only; no existing assertion changes**

**Interfaces:**
- Produces: `scope_files(rel: str, all_files: list[str]) -> list[str]` on every stack. The returned files have their bare occurrences counted as the unit's own scope. They always include `rel`, and they are excluded from the reference-file list.

- [ ] **Step 1: Record the baseline**

```bash
git -C ../agent-skills-go rev-parse HEAD > /tmp/tsn-go-base.txt
mkdir -p /tmp/oldrank && for f in rank_risk stack_python stack_node stack_common; do
  git show HEAD:test-safety-net/assets/$f.py > /tmp/oldrank/$f.py; done
```

- [ ] **Step 2: Write the failing test** (in `test_rank_risk.py`)

```python
class TestScopeFilesInterface(unittest.TestCase):
    def test_every_registered_stack_answers_its_own_file_at_least(self):
        for stack in rank_risk.STACKS:
            got = stack.scope_files("pkg/a.x", ["pkg/a.x", "pkg/b.x", "other/c.x"])
            self.assertIn("pkg/a.x", got, stack.STACK_NAME)
        for stack in (rank_risk.stack_python, rank_risk.stack_node):
            self.assertEqual(stack.scope_files("pkg/a.x", ["pkg/a.x", "pkg/b.x"]),
                             ["pkg/a.x"])
```

- [ ] **Step 3: Run it; watch it fail** — `cd test-safety-net/assets && python3 -m unittest test_rank_risk.TestScopeFilesInterface -v` → `AttributeError: ... has no attribute 'scope_files'`.

- [ ] **Step 4: Implement.** Add to both stacks:

```python
def scope_files(rel: str, all_files) -> list:
    """Files whose BARE occurrences count as `rel`'s own scope.

    Python's and node's lexical scope for a module-level name is the file
    that defines it, so the answer is the file alone -- exactly what
    `rank_risk.inbound_refs` hard-coded before this became an interface name.
    Go answers differently: a package is a directory, and a caller in a
    sibling file is as much "the unit's own file" as the defining one.
    """
    return [rel]
```

In `rank_risk.inbound_refs`, replace the own-file term:

```python
        scope = stack.scope_files(own_path, all_files)
        total = sum(occurrences(rel, name, module) for rel in scope if rel in bare_counts)
        lines = file_lines.get(own_path, [])
        lineno = u["lineno"]
        if 1 <= lineno <= len(lines):
            def_bare, def_attr = _name_occurrences(stack, lines[lineno - 1])
            total -= def_bare[name] + def_attr[(module, name)]
        for rel in ref_files:
            if rel not in scope:
                total += occurrences(rel, name, module)
```

In the module docstring, change "exactly fourteen names" to fifteen and add `scope_files` under **files**, with one paragraph saying why: package scope is a fact about the language, so the core must ask rather than assume the file.

- [ ] **Step 5: Prove nothing changed**

```bash
cd ../agent-skills-go
for tree in . test-safety-net scripts agent-ready-rails starlight-handbook-kit; do
  python3 /tmp/oldrank/rank_risk.py "$tree" > /tmp/o.json 2>/dev/null
  python3 test-safety-net/assets/rank_risk.py "$tree" > /tmp/n.json 2>/dev/null
  cmp /tmp/o.json /tmp/n.json && echo "$tree IDENTICAL"
done
```

Expected: all five `IDENTICAL`. `starlight-handbook-kit` is included because it is the tree holding the most `.mjs`/`.ts`.

- [ ] **Step 6: Suites and eval unmodified** — `make gate-skill SKILL=test-safety-net` → PASS.

- [ ] **Step 7: Commit** — `refactor(test-safety-net): scope_files joins the stack interface`.

---

### Task 2: `stack_go.py` — identity, files, naming, lexing, grammar

**Files:**
- Create: `test-safety-net/assets/stack_go.py`
- Test: `test-safety-net/assets/test_stack_go.py`

**Interfaces:**
- Consumes: `stack_common.{SKIP_DIRS, evidence_score, read_text}`. Never `rank_risk`, for the reason `stack_node.py` records.
- Produces:
  - identity: `STACK_NAME = "go"`, `MANIFESTS = ("go.mod", "go.work")`, `classify_manifest`, `evidence`;
  - files: `iter_source_files`, `is_test_path`, `is_test_for`, `scope_files`;
  - naming: `module_of`, `name_pattern`, `path_pattern`;
  - lexing: `IDENTIFIER_RE`, `preceding_qualifier`;
  - grammar: `module_bindings`, `reached_through_module`;
  - the stripper: `strip_noncode(text, keep_strings=False)`.

**Not registered in `rank_risk.STACKS` yet** — `discover_units`/`triage` do not exist until Tasks 3–4.

Rules, each with a test:

1. **`strip_noncode`** blanks `//…`, `/*…*/`, `"…"` (never across a newline), `` `…` `` raw strings (which DO cross newlines), and `'…'` runes, preserving every offset and newline. `keep_strings=True` keeps `"…"` and `` `…` `` bodies, because an import path is a string literal. Go has no regex literals and no template interpolation, so this is node's stripper minus two states.
2. **`iter_source_files`**:
   - `.go` files only, sorted;
   - pruning `SKIP_DIRS`, `testdata`, and directories starting with `_` or `.`;
   - skipping files whose name starts with `_` or `.`;
   - skipping `_test.go` unless `include_tests`;
   - skipping files whose build line is `//go:build ignore` or `//go:build tools` (the line before the `package` clause, read with the stripper's comment knowledge).

   **Test the tools case against evidence:** eight `.py` files plus `tools/go.mod` and `tools/tools.go` (`//go:build tools`) must detect `python`. That is the Go twin of the husky `package.json` row.
3. **`is_test_path(rel)`**: basename ends `_test.go`, or any directory part is `testdata`.
4. **`is_test_for(test_rel, src_rel)`**: the same directory. Go refuses a test anywhere else.
5. **`scope_files(rel, all_files)`**: every `.go` path in `all_files` whose directory equals `rel`'s — tests included, since a white-box test calling `parse(` is a caller.
6. **`module_of(rel)`**: the directory basename, or the file stem for a root-level file.
7. **`name_pattern(name)`**:
   - a plain name gives `(?<![A-Za-z0-9_])Name(?![A-Za-z0-9_])`;
   - a method name `"T.M"` gives a pattern requiring **both** `T` as a whole word **and** `.M` followed by `(`, in either order: `(?s)(?=.*(?<![A-Za-z0-9_])T(?![A-Za-z0-9_])).*\.\s*M\s*\(`.

   Test the over-credit case: a same-directory test calling `buf.String()` must **not** credit `Report.String` when the file never names `Report`.
8. **`path_pattern(rel)`**: the package directory, anchored on an import path's `/` or `"` at the start and its closing `"` at the end — `(?<=["/])internal/billing(?=")` for `internal/billing/x.go`. `None` for a root file. Test that `myinternal/billing` is not matched and that the pattern matches inside `"example.com/m/internal/billing"`.
9. **`IDENTIFIER_RE`/`preceding_qualifier`**: Python's two, byte for byte. Go's selector is `.`, and its identifiers are ASCII in practice (a Unicode identifier reads as uncovered, which is the safe direction; say so in the docstring).
10. **`module_bindings(module, text, *, src_rel, ref_rel)`** reads `import "p"`, `import a "p"` and grouped `import ( … )` off the stripped-with-strings text. A path *names* `src_rel`'s package when it equals, or ends with `/` plus, the directory of `src_rel`. The binding it yields depends on the import form:
    - an explicit alias gives that alias;
    - no alias gives the directory basename;
    - `.` gives the special names value `("*",)`;
    - `_` gives nothing.

    **A referencing file in the same directory binds every name bare**, because same package means no import at all: return `((), ("*",))`. That is how a white-box test reaches its unit.
11. **`reached_through_module`** requires a call site, as both other stacks do:
    - `alias.Name(` for a function;
    - `"*"` in names plus a bare `Name(` for a same-package or dot import;
    - for a method `"T.M"`, the test must name `T` **and** contain `.M(`.
12. **`classify_manifest`**: `go.mod` is declaring if it has a `module` directive; `go.work` is always declaring. There is no tooling-only form of `go.mod` that matters, because a tools module's `.go` files are excluded by rule 2, and `evidence_score` is 0 with no source behind the manifest.

- [ ] **Step 1: Write the failing tests** — one per rule above. Example for rule 10:

```python
class TestGoBindingGrammar(unittest.TestCase):
    def test_an_import_path_names_the_package_directory(self):
        text = 'package x_test\n\nimport (\n\tb "example.com/m/internal/billing"\n)\n'
        self.assertEqual(
            stack_go.module_bindings("billing", text,
                                     src_rel="internal/billing/refund.go",
                                     ref_rel="cmd/tool/main.go"),
            (("b",), ()))

    def test_a_same_directory_file_binds_every_name(self):
        self.assertEqual(
            stack_go.module_bindings("billing", "package billing\n",
                                     src_rel="internal/billing/refund.go",
                                     ref_rel="internal/billing/refund_test.go"),
            ((), ("*",)))

    def test_a_commented_out_import_binds_nothing(self):
        text = 'package y\n// import "example.com/m/internal/billing"\n'
        self.assertEqual(
            stack_go.module_bindings("billing", text,
                                     src_rel="internal/billing/refund.go",
                                     ref_rel="cmd/y.go"),
            ((), ()))
```

- [ ] **Step 2: Run; watch them fail** (`ModuleNotFoundError`).
- [ ] **Step 3: Implement** `stack_go.py` to the twelve rules, with docstrings recording the direction each rule fails in (the house style `stack_node.py` sets).
- [ ] **Step 4: Run** `python3 test_stack_go.py` from `test-safety-net/assets` → OK.
- [ ] **Step 5: Commit** — `feat(test-safety-net): the Go stack's files, naming and import grammar`.

---

### Task 3: Go heuristic discovery

**Files:** Modify `stack_go.py`; Test `test_stack_go.py`.

**Interfaces:**
- Produces:
  - `discover_units(root, precise=True) -> (units, "heuristic" | "precise")`. Until Task 5 it always returns the heuristic.
  - `_units_heuristic(root)`.
  - `_units_in_text(rel, text) -> list[dict]`, where each unit dict has keys `id` (`"<rel>::<name>"`), `path`, `name`, `lineno` and `kind` (`"function"` | `"method"`).

Recognise, at bracket depth 0 of the stripped text:
- `func Name(`, and `func Name[T any](` with type parameters;
- `func (r T) Name(`, `func (r *T) Name(`, `func (*T) Name(` and `func (r *T[K]) Name(`.

A unit needs an uppercase first letter on `Name` and, for a method, on `T` too.

- [ ] **Step 1: Failing tests.** Positives:
  - each `func` form above;
  - a generic function;
  - a multi-line signature;
  - an exported method on an unexported type — **not** a unit, because it is unreachable by an importer; its white-box test still reaches it through the exported surface.

  Negatives:
  - lowercase `func helper(`;
  - a name inside a `//` comment, a `/* */` comment, a `"…"` string and a `` `…` `` raw string spanning lines;
  - a function literal inside an exported one;
  - `_test.go` files;
  - `vendor/`, `testdata/` and `_scratch/`;
  - generated files;
  - `//go:build ignore`.

  Also: `lineno` is the `func` keyword's line.

```python
def test_exported_method_on_exported_type(self):
    write(self.root, "pkg/r.go",
          "package pkg\n\ntype Report struct{}\n\n"
          "func (r *Report) String() string { return \"\" }\n")
    units, mode = stack_go.discover_units(self.root, precise=False)
    self.assertEqual(mode, "heuristic")
    self.assertEqual([(u["id"], u["kind"], u["lineno"]) for u in units],
                     [("pkg/r.go::Report.String", "method", 5)])
```

- [ ] **Step 2: Run; fail.** **Step 3: Implement.** **Step 4: Run; pass.**
- [ ] **Step 5: Commit** — `feat(test-safety-net): Go heuristic discovery of exported funcs and methods`.

---

### Task 4: Go I/O markers, triage, registration, detection

**Files:**
- Modify: `stack_go.py`, `rank_risk.py` (`STACKS = [stack_go, stack_node, stack_python]`, and `import stack_go`)
- Modify: `test_stack_node.py` — the registry test asserts three stacks. Add the Go rows below to `CASE_TABLE`.
- Test: `test_stack_go.py`

**Interfaces:**
- Produces:
  - `CONTROLLABLE`, `UNCONTROLLABLE`, `STATIC_DECLINE`, `SYSCALL_GROUPS`;
  - `triage(root, unit) -> (tier, reason)`.

`SYSCALL_GROUPS` is also consumed by Task 6's guard, and **must be the only copy**.

**Marker tables** (dotted import path, then member; `_marker_hit` is node's equal / member / sub-path rule):

```python
CONTROLLABLE = {
    "filesystem": ("os.Open", "os.OpenFile", "os.Create", "os.ReadFile", "os.WriteFile",
                   "os.ReadDir", "os.Stat", "os.Lstat", "os.Mkdir", "os.MkdirAll",
                   "os.MkdirTemp", "os.CreateTemp", "os.Remove", "os.RemoveAll",
                   "os.Rename", "os.Link", "os.Symlink", "os.Readlink", "os.Chmod",
                   "os.Chown", "os.Lchown", "os.Chtimes", "os.Truncate", "os.DirFS",
                   "os.CopyFS", "os.OpenRoot", "os.OpenInRoot", "os.Getwd", "os.Chdir",
                   "io/ioutil", "path/filepath.Walk", "path/filepath.WalkDir",
                   "path/filepath.Glob", "path/filepath.EvalSymlinks", "path/filepath.Abs",
                   "time.LoadLocation"),
    "clock": ("time.Now", "time.Since", "time.Until", "time.Sleep", "time.After",
              "time.AfterFunc", "time.NewTimer", "time.NewTicker", "time.Tick"),
    "environment": ("os.Getenv", "os.LookupEnv", "os.Environ", "os.Setenv", "os.Unsetenv",
                    "os.Clearenv", "os.ExpandEnv", "os.Hostname", "os.Getpid", "os.Getppid",
                    "os.Getuid", "os.Geteuid", "os.Getgid", "os.Getegid", "os.Getgroups",
                    "os.Executable", "os.Args", "os.TempDir", "os.UserHomeDir",
                    "os.UserCacheDir", "os.UserConfigDir", "os.Getpagesize"),
}
UNCONTROLLABLE = {
    "randomness": tuple("math/rand." + n for n in (
                      "ExpFloat64", "Float32", "Float64", "Int", "Int31", "Int31n", "Int63",
                      "Int63n", "Intn", "NormFloat64", "Perm", "Read", "Seed", "Shuffle",
                      "Uint32", "Uint64"))
                  + tuple("math/rand/v2." + n for n in (
                      "ExpFloat64", "Float32", "Float64", "Int", "Int32", "Int32N", "Int64",
                      "Int64N", "IntN", "N", "NormFloat64", "Perm", "Shuffle", "Uint",
                      "Uint32", "Uint32N", "Uint64", "Uint64N", "UintN"))
                  + ("crypto/rand", "hash/maphash.MakeSeed"),
    "network": tuple("net." + n for n in (
                   "Dial", "DialIP", "DialTCP", "DialTimeout", "DialUDP", "DialUnix",
                   "FileConn", "FileListener", "FilePacketConn", "InterfaceAddrs",
                   "InterfaceByIndex", "InterfaceByName", "Interfaces", "Listen", "ListenIP",
                   "ListenMulticastUDP", "ListenPacket", "ListenTCP", "ListenUDP",
                   "ListenUnix", "ListenUnixgram", "LookupAddr", "LookupCNAME", "LookupHost",
                   "LookupIP", "LookupMX", "LookupNS", "LookupPort", "LookupSRV", "LookupTXT",
                   "Dialer", "Resolver", "ListenConfig"))
               + ("net/http.Get", "net/http.Head", "net/http.Post", "net/http.PostForm",
                  "net/http.DefaultClient", "net/http.Client", "net/http.ListenAndServe",
                  "net/http.ListenAndServeTLS", "net/http.Serve", "net/http.ServeTLS",
                  "net/rpc", "net/smtp", "crypto/tls.Dial", "crypto/tls.DialWithDialer",
                  "crypto/tls.Listen", "google.golang.org/grpc"),
    "subprocess": ("os/exec", "os.StartProcess", "os.FindProcess", "plugin.Open"),
    "database": ("database/sql.Open", "database/sql.OpenDB", "github.com/jackc/pgx",
                 "github.com/lib/pq", "go.mongodb.org/mongo-driver",
                 "github.com/redis/go-redis", "github.com/go-redis/redis", "gorm.io/gorm",
                 "github.com/jmoiron/sqlx", "github.com/mattn/go-sqlite3", "modernc.org/sqlite",
                 "go.etcd.io/bbolt", "github.com/dgraph-io/badger"),
}
```

`SYSCALL_GROUPS` covers **every exported name of package `syscall` on darwin and on linux**: the union of `GOOS=darwin go doc -all syscall` and `GOOS=linux go doc -all syscall`, captured 2026-09-12 on go 1.26.7. Each name maps to one of the seven groups, `"stdin"`, `"fd"` (an operation on an already-open descriptor — the open was the I/O), `"pure"` (no system state at all) or `"raw"` (an unclassifiable raw syscall, statically declined). The filter applies it to both `syscall.<Name>` and `golang.org/x/sys/unix.<Name>`, whose exported names mirror it. Start from this assignment:

- filesystem: Access Acct Chdir Chflags Chmod Chown Chroot Creat Exchangedata Faccessat Fallocate Fchdir Fchflags Fchmod Fchmodat Fchown Fchownat Fstatat Futimesat Getcwd Getdents Getdirentries Getfsstat Getwd Getxattr InotifyAddWatch InotifyInit InotifyInit1 InotifyRmWatch Lchown Link Listxattr Lstat Mkdir Mkdirat Mkfifo Mknod Mknodat Mount Open Openat Pathconf PivotRoot ReadDirent Readlink Removexattr Rename Renameat Revoke Rmdir Setxattr Stat Statfs Symlink Sync Truncate Undelete Unlink Unlinkat Unmount Utime Utimes UtimesNano
- network: Accept Accept4 AttachLsf Bind BindToDevice BpfBuflen BpfDatalink BpfHeadercmpl BpfInterface BpfStats BpfTimeout CheckBpfVersion Connect DetachLsf FlushBpf Getpeername Getsockname GetsockoptByte GetsockoptICMPv6Filter GetsockoptInet4Addr GetsockoptInt GetsockoptIPMreq GetsockoptIPMreqn GetsockoptIPv6Mreq GetsockoptIPv6MTUInfo GetsockoptUcred Listen LsfSocket NetlinkRIB Recvfrom Recvmsg RouteRIB SetBpf SetBpfBuflen SetBpfDatalink SetBpfHeadercmpl SetBpfImmediate SetBpfInterface SetBpfPromisc SetBpfTimeout SetLsfPromisc Sendmsg SendmsgN Sendto SetsockoptByte SetsockoptICMPv6Filter SetsockoptInet4Addr SetsockoptInt SetsockoptIPMreq SetsockoptIPMreqn SetsockoptIPv6Mreq SetsockoptLinger SetsockoptString SetsockoptTimeval Shutdown Socket Socketpair
- subprocess: Exec ForkExec Kill PtraceAttach PtraceCont PtraceDetach PtraceGetEventMsg PtraceGetRegs PtracePeekData PtracePeekText PtracePokeData PtracePokeText PtraceSetOptions PtraceSetRegs PtraceSingleStep PtraceSyscall Reboot Setprivexec StartProcess Tgkill Unshare Wait4
- environment: Clearenv Environ Getegid Getenv Geteuid Getgid Getgroups Getpgid Getpgrp Getpid Getppid Getpriority Getrlimit Getrusage Getsid Gettid Getuid Issetugid Klogctl Setdomainname Setegid Setenv Seteuid Setfsgid Setfsuid Setgid Setgroups Sethostname Setlogin Setpgid Setpriority Setregid Setresgid Setresuid Setreuid Setrlimit Setsid Setuid Sysctl SysctlUint32 Sysinfo Umask Uname Unsetenv
- clock: Adjtime Adjtimex Gettimeofday Nanosleep Settimeofday Time Times
- fd: Close CloseOnExec Dup Dup2 Dup3 EpollCreate EpollCreate1 EpollCtl EpollWait FcntlFlock Fdatasync Flock Fpathconf Fstat Fstatfs Fsync Ftruncate Futimes Getdtablesize Getpagesize Kevent Kqueue Madvise Mlock Mlockall Mmap Mprotect Munlock Munlockall Munmap Pause Pipe Pipe2 Pread Pwrite Seek Select Sendfile SetKevent SetNonblock Splice Sync SyncFileRange Tee Write Exit
- stdin: Read (the guard fires only when `fd == 0`; for the filter it is `fd`)
- pure: BpfJump BpfStmt BytePtrFromString ByteSliceFromString CmsgLen CmsgSpace LsfJump LsfStmt NsecToTimespec NsecToTimeval ParseDirent ParseNetlinkMessage ParseNetlinkRouteAttr ParseRoutingMessage ParseRoutingSockaddr ParseSocketControlMessage ParseUnixCredentials ParseUnixRights SlicePtrFromStrings StringBytePtr StringByteSlice StringSlicePtr TimespecToNsec TimevalToNsec UnixCredentials UnixRights
- raw: AllThreadsSyscall AllThreadsSyscall6 RawSyscall RawSyscall6 Syscall Syscall6 Syscall9

(`Sync` appears in filesystem, not fd: it flushes every filesystem. Remove it from the fd line when you build the dict.)

`STATIC_DECLINE = {"raw": <the raw names under both prefixes>, "cgo": ("C",)}`. A hit returns tier 4 with the reason `"<kind>: neither the filter nor the guard can see what it does"`.

**Triage.** It mirrors `stack_node.triage`, with Go's scope:
- **Import resolution is per file** (Go imports are file-scoped): alias → import path, so `os.ReadFile` → `os.ReadFile` and `exec.Command` → `os/exec.Command`.
- **Same-package call chasing spans every non-test file in the directory:** bare `helper(`, `recv.M(` inside a method whose receiver is `recv`, and `x.M(` where exactly one type in the package defines `M`. That is `stack_python._unambiguous_methods`'s rule, and it keeps its one-owner restriction.
- **Import-time regions span the package:**
  - every `init()` body, in every file;
  - every package-level `var` initializer containing a call, in every file.

  Uncontrollable import-time I/O → tier 4. Controllable → floor tier 3. Both apply to **every unit in the package**, not the file, because every file's `init` runs when the package is imported.
- **The unit's span** is its brace-matched body.

**Case-table rows** (add to `CASE_TABLE` in `test_stack_node.py`, whose runner already takes a list of files and an expected verdict):

| repo shape | expect |
|---|---|
| 30 `.go` + `go.mod`, 5 `.py` scripts | go |
| 40 `.py` + declaring `pyproject.toml`, `tools/go.mod` + `tools/tools.go` (`//go:build tools`) | python |
| 12 `.go` + `go.mod`, 20 `.ts` + declaring `package.json` | node |
| 3 `.go` + `go.mod`, 6 vendored `.py` under `vendor/` | go |
| 10 `.go`, 10 `.py`, no manifest | ambiguous — reported |

and keep the existing `test_this_repo_classifies_as_python_not_node` green — this repo has no `.go` files and must still detect python.

- [ ] **Step 1: Failing tests.** For each group:
  - one unit reaching a primitive directly;
  - one reaching it through a same-package helper **in a sibling file**;
  - one through an aliased import (`f "os"`);
  - one through a dot-import;
  - one marker inside a comment and one inside a string, which must **not** count.

  Then:
  - `init()` reading `os.Getenv` floors every unit in the package to 3, including a unit in a different file;
  - a package-level `var conn, _ = net.Dial("tcp", "db:5432")` counts as import-time network, tier 4. (**Corrected during Task 4:** this line first read `var client = http.DefaultClient`, but taking a pointer to a package variable is no I/O at all. The import-time scan counts *calls*, as Python's does, plus the one variable marker `os.Args`, and a test pins the `DefaultClient` case at tier 1.)
  - `rand.Intn` is tier 3 while `rand.New(rand.NewSource(1)).Intn` is tier 1;
  - `C.puts` is tier 4 cgo, and `syscall.Syscall` is tier 4 raw.

  And the **derived test**: when `go` is on PATH, every exported name from `GOOS=darwin|linux go doc -all syscall` is a key of `SYSCALL_GROUPS`. Skip visibly otherwise. Its failure on a Go upgrade is the mechanism — say so in a comment, so nobody widens it away.
- [ ] **Step 2: Run; fail.** **Step 3: Implement and register.** **Step 4: Run all three suites and the eval** (the eval's python/node checks must stay green).
- [ ] **Step 5: Commit** — `feat(test-safety-net): Go I/O markers, package-scoped triage, and registration`.

---

### Task 5: Go precise discovery through `go/ast`

**Files:** Modify `stack_go.py`; Test `test_stack_go.py`.

**Interfaces:**
- Produces:
  - `_units_precise(root, go_exe="go", timeout=PRECISE_TIMEOUT) -> list | None`;
  - `discover_units(root, precise=True)` returning `"precise"` when the helper answers.

`PRECISE_TIMEOUT = 60`. The first run compiles the helper.

**How the helper runs.** `_GO_HELPER` is a Go program held as a string. `_units_precise`:
- writes it to `tempfile.mkdtemp()/main.go`;
- runs `go run main.go` with `cwd` set to that temp dir;
- sets `env` to `GOTOOLCHAIN=local`, `GOFLAGS=` (empty), `GOWORK=off`, `GO111MODULE=on` and `CGO_ENABLED=0`;
- passes `{"root": ..., "files": [...]}` on stdin and reads `{"units": [...], "unreadable": n}` from stdout.

**What the helper does.** It calls `go/parser.ParseFile(fset, path, nil, parser.SkipObjectResolution)` on each file, then walks `f.Decls`. A `*ast.FuncDecl` with `Recv == nil` and an exported name is a function unit. With a receiver, it is a method unit only if the receiver type is exported: unwrap `*ast.StarExpr`, `*ast.IndexExpr` and `*ast.IndexListExpr` to get the type name. `lineno` is `fset.Position(d.Pos()).Line`.

**It runs none of the analysed repo's code** — `go/parser` reads text. `--no-precise` still declines it, so the flag keeps one meaning across stacks: heuristic only. The docstring says that, and says the difference from node: node's precise path executes the repo's own `typescript`, and this one executes this skill's helper under the machine's own `go`.

Declines are the ones `stack_node` has: all or nothing per row, with the same `_decline_reason` rules — any `unreadable`, or zero units over a non-empty set.

- [ ] **Step 1: Failing tests:**
  - no `go` on PATH (run with `go_exe="/nonexistent/go"`) → `heuristic`, with no stderr note, because being absent is ordinary;
  - a fake `go` that exits 1 → `heuristic` plus a `declined` note;
  - a fake `go` that prints `{"units": [], "unreadable": 0}` over three files → declines;
  - a malformed row → declines;
  - `precise=False` never spawns a process (assert by pointing `go_exe` at a script that writes a sentinel file);
  - where real `go` exists, both paths agree on a fixture both can read;
  - precise finds a unit the heuristic documents as a miss — a signature split so `func` and the name sit on different lines after a comment. Skip visibly without `go`.
- [ ] **Step 2–4: fail, implement, pass.**
- [ ] **Step 5: Commit** — `feat(test-safety-net): Go precise discovery through go/ast`.

---

### Task 6: `io_guard_go.py` — the overlay guard

**Files:** Create `test-safety-net/assets/io_guard_go.py`, `test-safety-net/assets/test_io_guard_go.py`.

**Interfaces:**
- Consumes:
  - the environment: `TEST_SAFETY_NET_TIER`, `TEST_SAFETY_NET_ALLOW`;
  - from `stack_go`: `SYSCALL_GROUPS`, `CONTROLLABLE`, `UNCONTROLLABLE` (loaded by path, like every other sibling import).
- Produces the CLI:

```sh
TEST_SAFETY_NET_TIER=1 \
  python3 "$SKILL_DIR/assets/io_guard_go.py" \
  -run '^<test_name>$' <package>
```

It also produces:
- the Python API: `read_env(env) -> (tier, allow, notes)`, `blocked_groups(tier, allow) -> set`, `build_overlay(goroot, goos, goarch, dest) -> (overlay_json_path, hooked: dict)`, `main(argv) -> int`;
- the tables `HOOK_TARGETS`, `REQUIRED_TARGETS`, `FILTER_MARKER_INTERCEPTS`, `PARTIALLY_INTERCEPTED`, `NOT_INTERCEPTED`.

**`read_env`/`blocked_groups`** follow the contract `io_guard.py`/`io_guard.js` share:
- an absent or invalid tier → 1, with a note;
- `none` means an empty allow list;
- unknown groups are ignored and stay blocked, with a note;
- tier 1 ignores the allow list.

The controllable set is `stack_go.CONTROLLABLE`'s keys, so `TEST_SAFETY_NET_ALLOW=randomness` notes "randomness is not controllable on go; it stays blocked".

**`build_overlay`:**
1. `go list -json syscall internal/syscall/unix os time net net/http crypto/tls math/rand math/rand/v2 crypto/rand crypto/internal/sysrand database/sql`, under `GOTOOLCHAIN=local`. From the output, take each package's `Dir` and `GoFiles` — exactly the files this build compiles, so the per-GOOS/GOARCH choice is Go's, not ours.
2. For every `(package, symbol, group, condition)` in `HOOK_TARGETS`, find `^func <symbol>` or `^func \(<recv>\) <symbol>` in those files, allowing type parameters, then the `{` that opens the body at paren depth 0. Inject one statement after it:
   - `tsnHook("<group>", "<pkg>.<symbol>")`;
   - for `syscall.Read`, `if fd == 0 { tsnHook("stdin", "syscall.Read(0)") }`.

   A declaration with no body — `time.Sleep` is linknamed to the runtime — is **skipped and recorded** in `NOT_INTERCEPTED` with that reason. Any `REQUIRED_TARGETS` entry not found → raise `GuardCannotArm`, so the run exits 2.
3. Write one `zz_tsn_hook.go` per hooked package.
   - **In `syscall`**, it holds the engine, `TSNHook`, plus a package-local `tsnHook`, and imports only `runtime` and `sync`. It reads its configuration through `runtime_envs()`, never through `Getenv`, which is hooked and would recurse.
   - **Everywhere else**, it is `import "syscall"` plus `func tsnHook(g, t string) { syscall.TSNHook(g, t) }`.
4. `HOOK_TARGETS` holds four kinds of entry:
   - every `SYSCALL_GROUPS` name whose group is one of the seven, or `stdin`, **that exists in this build's `syscall` files**;
   - the matching `internal/syscall/unix` names (`Openat Unlinkat Fstatat Mkdirat Linkat Symlinkat Readlinkat Renameat Fchmodat Fchownat Utimensat Eaccess` filesystem; `Getaddrinfo Getnameinfo ResNsearch ResNinit SendtoInet4 SendtoInet6 SendmsgNInet4 SendmsgNInet6 RecvfromInet4 RecvfromInet6 RecvmsgInet4 RecvmsgInet6` network; `GetRandom ARC4Random` randomness; `PidFDOpen PidFDSendSignal Waitid` subprocess; `Getpwnam Getpwuid Getgrnam Getgrgid Getgrouplist Sysconf KernelVersion` environment);
   - the entry points (`time.Now Since Until After AfterFunc NewTimer NewTicker Tick`, the `math/rand` and `math/rand/v2` top-level functions from Task 4's table, `crypto/rand.Read Int Prime Text`, `crypto/internal/sysrand.Read`, `database/sql.Open OpenDB`, the `net` function list and the `Dialer`/`Resolver`/`ListenConfig` methods, `net/http.Get Head Post PostForm ListenAndServe ListenAndServeTLS Serve ServeTLS` and `(*Client).Do` `(*Transport).RoundTrip`, `crypto/tls.Dial DialWithDialer Listen`);
   - `REQUIRED_TARGETS = {syscall.Open, syscall.Socket, syscall.ForkExec, syscall.Getenv, syscall.Read, time.Now, math/rand/v2.IntN, database/sql.Open}` — one per group plus stdin. These are the targets whose absence means the guard is inert for a whole group.

   The `net/http` entries exist because the dial happens on a goroutine the transport starts, whose stack holds no repo frame. Hooking the entry point on the caller's own goroutine is what trips it.

**The engine** is Go source held in `_ENGINE_SYSCALL`, formatted with `STD` (the sorted `go list std` output as a Go string-slice literal). It decides as the design section says:
- stdlib frames are transparent and the entry frame is recorded;
- `main` in `_testmain.go` exempts;
- the first other frame judges, and the end of the stack exempts.

Its inputs come from env vars the wrapper sets:
- `TEST_SAFETY_NET_GO_BLOCKED`: the comma-joined blocked groups;
- `TEST_SAFETY_NET_GO_STDIN`: `1` at tier 1;
- `TEST_SAFETY_NET_GO_TIER`: the tier, for the message.

The engine arms **only** when `TEST_SAFETY_NET_GO_BLOCKED` is present, so an overlay build run any other way is inert. `ENTRY_GROUPS` and `TESTING_CONTROLS` are Go map literals in the engine:
- `ENTRY_GROUPS` is a function-name prefix → group map covering `net.`, `net/http.`, `crypto/tls.`, `net/rpc.`, `net/smtp.` → network; `os/exec.`, `os.StartProcess`, `os.FindProcess` → subprocess; `database/sql.` → database; `time.` → clock; `math/rand.`, `math/rand/v2.`, `crypto/rand.` → randomness; `os.Getenv`, `os.LookupEnv`, `os.Environ`, `os.Setenv`, `os.Unsetenv`, `os.Clearenv`, `os.Hostname`, `os.TempDir`, `os.UserHomeDir`, `os.UserCacheDir`, `os.UserConfigDir` → environment.
- `TESTING_CONTROLS` maps each full function name to a group set:

| full function name | groups |
|---|---|
| `testing.(*common).TempDir` | `{filesystem}` |
| `testing.(*common).ArtifactDir` | `{filesystem}` |
| `testing.(*common).Setenv` | `{environment}` |
| `testing.(*T).Setenv` | `{environment}` |
| `testing.(*common).Chdir` | `{filesystem, environment}` |
| `testing.(*T).Chdir` | `{filesystem, environment}` |

`tsnPkgOf` cuts a function name at its first `[` (generic instantiation), then its last `/`, then the first `.` after that.

**`main(argv)`:**
1. Refuse if `-overlay` appears in `argv` or `GOFLAGS`, or if GOOS is not in `{darwin, linux}`. Either → exit 2.
2. Build the overlay into `tempfile.mkdtemp()`.
3. Run `go test -overlay <json> -count=1 <argv…>` with `GOTOOLCHAIN=local`, `GOPROXY=off`, the three `TEST_SAFETY_NET_GO_*` vars and `stdin=subprocess.DEVNULL`, teeing combined output to our stdout.
   - `-count=1` is **load-bearing**. The engine reads its configuration through `runtime_envs()`, which `go test`'s result cache cannot see, so a cached pass from another tier would otherwise be replayed.
4. Map the result: `IOGuardViolation` in the output → 3; `[build failed]` or `[setup failed]` → 5; `no tests to run` → 4; otherwise go's own code, which is 0 or 1. Print one closing line naming the outcome in words. Remove the temp dir.

**The suite, `test_io_guard_go.py`.** It skips visibly without `go` and is run by `make gate` as `python3 test_io_guard_go.py`.
1. **The documented command, extracted and run verbatim.** The suite reads `io_guard_go.py`'s own header and every `.md` in the skill, strips comment leaders, joins `\` continuations and keeps lines matching `^TEST_SAFETY_NET_TIER=[12] .*python3 .*io_guard_go\.py`. It runs each against a clean module — must exit 0 — and against one whose unit constructs a socket — must exit 3. Zero extracted commands is a failure, and so is SKILL.md printing none.
2. **Every group trips at tier 1, in its own group.**

   | group | a unit that reaches it through |
   |---|---|
   | filesystem | `os.ReadFile("/etc/hosts")` |
   | network | `net.Dial` to `127.0.0.1:1` (the socket is constructed; nothing listens) |
   | subprocess | `exec.Command("true").Run()` |
   | environment | `os.Getenv` |
   | clock | `time.Now` |
   | randomness | `rand.IntN` |
   | database | `sql.Open("x", "")` |

   Assert the label is right, not just exit 3. **This is the check that proves the entry-group rule:** `net.Dial` must say `network`, not `clock`.
3. **Tier 2 permits exactly what it names.** With `TEST_SAFETY_NET_ALLOW=filesystem`, a test using `t.TempDir()` plus `os.WriteFile` into it → exit 0. That is the testing-control override, since `t.TempDir` reads `TMPDIR`. The same test with `TEST_SAFETY_NET_ALLOW=clock` → exit 3 `filesystem`. `net.Dial` at tier 2 with every controllable group allowed → exit 3 `network`.
4. **`recover()` does not swallow a trip; a goroutine's trip ends the run; `init()` I/O trips attributed to `<pkg>.init`; `-trimpath` in `GOFLAGS` changes nothing.**
5. **stdin at tier 1**: a unit calling `bufio.NewReader(os.Stdin).ReadString('\n')` → exit 3 `stdin`, well inside a 60s deadline. It is not 0 via the `/dev/null` EOF, because the hook fires before the read returns.
6. **The exit contract**: a failing assertion → 1; `-run '^NoSuchTest$'` → 4; a test file that does not compile → 5; `-overlay` passed by the caller → 2.
7. **Third-party frames are attributable**: a local `replace`d module `example.com/dep` whose function reads a file, called from the unit, trips.
8. **Partition**: every marker in `stack_go.CONTROLLABLE`/`UNCONTROLLABLE` appears in exactly one of `FILTER_MARKER_INTERCEPTS`, `PARTIALLY_INTERCEPTED` and `NOT_INTERCEPTED`, with a reason. This is the same test `test_stack_node.py` runs for the node pair.

Fixture modules are written under `tempfile.mkdtemp()`, each with its own `go.mod` (`module example.com/fx`, `go 1.22`). Nothing is written into the repo, and nothing listens on or dials a real port.

- [ ] **Step 1: Write the suite first** — all eight groups of assertions.
- [ ] **Step 2: Run it; every assertion fails** (`io_guard_go.py` does not exist).
- [ ] **Step 3: Implement** `io_guard_go.py`: env contract → overlay builder → engine template → `main`.
- [ ] **Step 4: Run it until green on darwin.** Then prove linux in a container. The suite is offline, and `GOPROXY=off` is set:

```bash
docker run --rm -v "$PWD/test-safety-net:/skill:ro" -w /skill/assets golang:1.26 \
  sh -c 'apt-get -qq update >/dev/null && apt-get -qq install -y python3 >/dev/null && python3 test_io_guard_go.py'
```

  Expected: `OK`. A linux-only failure means a hook target lives under a different name there. Fix the table; do not special-case the test.
- [ ] **Step 5: Commit** — `feat(test-safety-net): io_guard_go.py, Go's runtime enforcement via go test -overlay`.

---

### Task 7: Wire Go through the documents and CI

**Files:** `test-safety-net/SKILL.md`, `README.md`, `references/stacks.md`, `references/triage.md`, `references/parameters.md`, `.github/workflows/skill-gates.yml`.

- [ ] **Step 1: SKILL.md.**
  - Frontmatter `description`: go is covered, and "not for rust" replaces "not for go or rust".
  - `compatibility`: go 1.26 as the version CI proves; any other release is attempted, and the guard exits 2 if a required hook is missing.
  - Version `1.2.0`; tags gain `go`.
  - The `SKILL_DIR` block tests `io_guard_go.py` too.
  - Step 1 says three stacks.
  - Step 2's `--stack` choices include `go`.
  - Step 4 gains a **Go** block, the command from Task 6 with a tier 1 and a tier 2 form, followed by:
    - "Copy the whole block";
    - the six-row exit table;
    - one sentence on why `-count=1` is forced;
    - "Tests go in `<file>_test.go` beside the source, in the same package; append, never overwrite."
- [ ] **Step 2: stacks.md.**
  - Fill the go row: find units / tests go / framework / run one test.
  - Replace "go and rust are declined" with "rust is declined".
  - Add a **"Go row, in detail"** section:
    - both discovery paths, and that neither executes the analysed repo's code;
    - the package-scope rule;
    - randomness being uncontrollable, and why;
    - the guard's mechanism, decision rule and exit table;
    - the patch table (group → hooked names).
  - Add **Go residuals**, stated rather than implied:
    1. A module that bypasses `syscall` — `golang.org/x/sys/unix` raw calls, assembly, cgo — is unseen by the guard. The filter declines the raw and cgo shapes statically; a third-party library doing it internally escapes both layers.
    2. Work the stdlib performs on a goroutine *it* started, with no repo frame on that stack, is exempt. The entry-point hooks exist for exactly the case that matters (`net/http`'s dial), but the class is open.
    3. The hook targets are found by text in this GOROOT's sources. A release that renames one is a missing target: a required one makes the guard exit 2, and an optional one is recorded in `NOT_INTERCEPTED`.
    4. `time.Sleep` has no Go body (it is linknamed to the runtime), so it is not hooked. A unit that only sleeps passes tier 1.
    5. Map iteration order, `select` choice and scheduling are nondeterminism no hook can see. A characterization test must not pin them, and this is the one place the guard gives no warning.
    6. The `unittest`-style caveat does not apply: `init()` runs inside the guarded binary, so import-time I/O is always covered.
- [ ] **Step 3: triage.md.** The "four controllable groups" sentence becomes per-stack: python and node control four, Go three, and why. Terminal input: Go's rule is `syscall.Read` on fd 0.
- [ ] **Step 4: parameters.md.**
  - `--stack python|node|go`.
  - `kind` gains `"method"` (go only).
  - `discovery`: Go is `precise` wherever `go` runs, and never executes repo code.
- [ ] **Step 5: README.md.** Three stacks, the guard list gains `io_guard_go.py`, and the layout lists the new files.
- [ ] **Step 6: CI.** In `skill-gates.yml`, after `setup-node`:

```yaml
      # PINNED, like node: the Go guard's suite and eval check 44 SKIP where
      # `go` is absent -- passing without having graded anything.
      - uses: actions/setup-go@v5
        with:
          go-version: "1.26"
          cache: false
```

- [ ] **Step 7: `make gate-skill SKILL=test-safety-net`.** The guard suite's assertion 1 now extracts the SKILL.md block, so a mistyped document fails here.
- [ ] **Step 8: Commit** — `docs(test-safety-net): Go is covered end to end; wire it through SKILL.md and CI`.

---

### Task 8: Eval checks and A/B rows

**Files:** `test-safety-net/eval/run_eval.py`, `scripts/ab-validate.py`.

- [ ] **Step 1: Eval checks 40–44.** Each is mutation-tested: break the thing it claims to prove and watch it go red, and record in a comment what was broken.
  - **40** A Go fixture (`go.mod`, four packages) is detected `go` and its exported funcs **and methods** are discovered.
  - **41** `discovery` is named correctly in two arms. A `PATH` whose `go` is a script exiting 1 must read `heuristic` with a `declined` note. `--no-precise` must read `heuristic`.
  - **42** NEGATIVE: exported units under `vendor/`, `testdata/`, `_scratch/`, in `_test.go` and in a generated file are never discovered.
  - **43** Tiers, all directions at once:

    | unit | tier |
    |---|---|
    | pure | 1 |
    | `os.ReadFile` | 2 |
    | `net.Dial` | 3 |
    | `rand.Intn` | 3 |
    | `init()` reads env (the whole package) | 3 |
    | `syscall.Syscall` | 4 |
    | package-level `var conn, _ = net.Dial(...)` (the whole package) | 4 |
  - **44** NEGATIVE: the DOCUMENTED Go guard command, extracted from SKILL.md and stacks.md and run verbatim, passes a clean unit and exits 3 on a socket-constructing one. It is NOT GRADED HERE without `go`, like 34 and 39. The module docstring gains the matching "what remains ungated" bullet.
- [ ] **Step 2: A/B rows.**
  - `SINCE_TSN_GO = <Task 2's commit>` for rows 1–4 below. `SINCE_TSN_GO_GUARD = <Task 6's commit>` for row 5.
  - Row 6 needs no constant: `kind="guard"` rows carry none, the same as node's phantom row.
  - `check_test_safety_net_go(old, new)` gets a `_GO_PROBE` that, like `_NODE_TRIAGE_PROBE`, returns its "cannot answer" value against a baseline with no Go stack.

  | row | old → new | since |
  |---|---|---|
  | Go units discovered from an eight-form fixture (funcs, methods, generics) | 0 → 8 | `SINCE_TSN_GO` |
  | Go units tiered correctly out of the seven in check 43's table | 0 → 7 | `SINCE_TSN_GO` |
  | same-package sibling-file callers counted in `inbound_refs` | 0 → n | `SINCE_TSN_GO` |
  | a same-dir test calling `buf.String()` crediting `Report.String` — **guard**, must stay 0 | 0 → 0 | — |
  | the Go guard trips a tier 1 `os.ReadFile`, **skipped visibly without `go`** | 0 → 1 | `SINCE_TSN_GO_GUARD` |
  | python and node rank this repo byte-identically — the Task 1 guard | held | — |
- [ ] **Step 3: `make gate` and `make ab-validate`** — every new row IMPROVED or HELD, none UNPROVEN.
- [ ] **Step 4: Commit** — `test(test-safety-net): eval checks 40-44 and A/B rows for the Go stack`.

---

### Task 9: Review, then the PR

- [ ] **Step 1:** `make gate` → `GATE_RESULT: PASS`, and `make ab-validate` → no `WORSE`/`UNPROVEN`.
- [ ] **Step 2:** Review the whole branch against this plan and the spec (superpowers:requesting-code-review). Every finding is fixed in a numbered fix-round commit with its own A/B row — the node branch's convention.
- [ ] **Step 3:** Push `feat/test-safety-net-go` and open the PR against `main`. Use the template, including the semantic-review checklist for SKILL.md, and a summary table of guard outcomes on darwin and linux.
