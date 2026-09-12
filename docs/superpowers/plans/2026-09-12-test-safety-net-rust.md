# test-safety-net: the Rust stack — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Rust the fourth stack that test-safety-net covers end to end: detect → discover → triage → rank → write → **prove under a runtime guard**.

**Architecture:**
- `stack_rust.py` fills the 15-name stack interface.
  - Its discovery is a heuristic reader, used everywhere: regex plus brace matching, with Rust-aware lexing.
  - A unit counts as testable only when a `tests/` crate can reach it through the library root.
- The runtime guard is **libc interposition**, split across three files:
  - `io_guard_rust.py` orchestrates the run and classifies the outcome;
  - `rust_binary.py` inspects the compiled test binary;
  - `io_guard_rust_hook.rs` is a Rust cdylib, built by the repo's own `rustc` and preloaded under the test binary. It walks frames to attribute each I/O call.
- A new `guard_env.py` holds the tier/allow contract that the Go and Rust wrappers share.

**Tech Stack:** Python 3 stdlib for the ranker, the stack, the wrapper and the binary inspector. Rust for the hook alone, compiled by the analysed repo's own toolchain. No new dependencies anywhere.

**Spec:** `docs/superpowers/specs/2026-09-12-test-safety-net-rust-design.md` (read it first), plus the Rust amendment at the end of `docs/superpowers/specs/2026-09-09-test-safety-net-multistack-design.md`.

## Global Constraints

**Toolchains and versions**
- Proven Rust toolchains: **1.82, 1.86, 1.90, 1.94, 1.98**. The floor is the single constant `MIN_RUST = (1, 82)` in `io_guard_rust.py`.
  - A pinned toolchain below it is ranked, but its proof is declined, with the reason.
  - Versions in between are "expected by bracketing, not proven".
- Platforms: **darwin and linux only**. Anything else makes the guard exit 2.
- **The toolchain is never downloaded.** Every `rustc`/`cargo` invocation the skill makes sets `RUSTUP_AUTO_INSTALL=0`. A pinned toolchain that isn't installed makes the guard exit 2, with the reason.

**Dependencies and writes**
- **Stdlib-only Python, no pip, no network** for anything the ranker or the wrapper needs.
- **Rust tests go in `tests/` only**, per Invariant 1. Nothing else is written into the repo apart from cargo's own target dir.
  - The hook cache lives outside the repo: `$TEST_SAFETY_NET_CACHE`, defaulting to `~/.cache/test-safety-net`.
  - A build plan's separate target dir also lives outside the repo.
- **Every proof build passes `--locked`.** A repo with no `Cargo.lock` is declined (exit 2) with the remedy `cargo generate-lockfile`.
- **No `RUSTFLAGS` are added**, except `--cfg=rustix_use_libc`, and only in the build plan's separate target dir.

**The guard contract**
- The hook is built with `rustc --edition 2021 --crate-type cdylib -C panic=abort -C force-unwind-tables=yes -O`.
- The exit table is Go's: 0 GREEN, 1 RED, 2 NOT ARMED, 3 TRIP, 4 NO TEST, 5 NO BUILD.
- **Groups:** the seven every stack uses. On Rust, **filesystem and environment** are controllable. **Clock, randomness, network, subprocess and database** are not.
- **The guard fails closed.** If it cannot arm, it exits 2 and runs nothing.

**Conventions and bookkeeping**
- **Every `references/`/`assets/` path named in SKILL.md must exist.** Helpers are invoked as `"$SKILL_DIR/assets/..."`.
- **Every new guardrail gets an `ab-validate` row** under a `SINCE_TSN_RUST*` constant.
- **Mutation discipline, as on go.** Every rule is pinned by a test that turns red when the rule is removed.

---

## Design decisions this plan locks in

The spec leaves these open, or states them two ways. They are fixed here so tasks do not relitigate them.

### Units, and where "unreachable" lives
- **Discovery yields every fn and method declared `pub` or `pub(<restriction>)`.**
  - That covers free functions (`pub fn`, including `const`/`async`/`unsafe`/`extern "…"` forms) and methods of inherent `impl` blocks.
  - Plain private `fn`s are **not** units, consistent with go (exported names only) and python (no `_`-prefixed names).
- **A unit is reachable when a `tests/` crate can name it.** Its library path from the crate root must be public: every `mod` from `src/lib.rs` (or `[lib] path`) down is `pub mod`, or the item is re-exported by `pub use`, named or glob.
- **Unreachable units are Tier 3**, with one of two reasons:
  - `not reachable from tests/ without modifying source (<why>)` for a private module, or `pub(crate)`/`pub(super)`/`pub(in …)`;
  - `binary-only: reachable only by spawning the binary (subprocess)` for a fn in a `[[bin]]`/`src/main.rs`/`src/bin/*.rs` target that the library does not also provide.

  Tier 3 already means "needs a seam", and making a function public, or splitting a library out, is a seam. **This resolves a contradiction in the spec.** Its eval-47 wording says "unreachable `pub` items are never units", while its discovery section says they are "ranked and reported". The ranker can only rank units, so they are units at Tier 3, and eval 47 asserts they are never *netted*.
- **The unit record** is the same dict every stack returns: `{"id": "<rel>::<name>", "path": rel, "name": name, "lineno": int, "kind": "function"|"method"}`.
  - A method's `name` is `Type::method`, e.g. `src/calc.rs::Report::total`.
  - The `id` is split on its first `::` after `.rs`.
- **`discover_units(root, precise=True)` always returns `(units, "heuristic")`.** Rust has no precise path, and the `precise` argument is accepted and ignored, as python ignores it.

### Stdin is not a marker
The spec's marker table lists `std::io::stdin` "(tier 1 only, as on every stack)". On every stack, terminal input is **guard-only**: python's and go's filters do not mark it, and their guards block `read` on fd 0 at tier 1. Rust follows suit: `std::io::stdin` is in no marker table, and the hook blocks `read(0)` at tier 1.

### The environment control's restore frame
`tsn_control_set_env` returns a guard value whose `Drop` restores the variable. That `drop` frame is named after its type, not the helper, so the type is `TsnControlEnv`, and `CONTROL_HELPERS` matches both names. Without that, the restore would trip as environment I/O.

### One environment-controlled test per file, enforced
The spec states the placement rule. The wrapper also enforces it: if a test file calls `tsn_control_set_env` and contains more than one `#[test]`, the run exits 2. A rule the guard can check is not left as prose.

### rustix detection needs no cargo call
`Cargo.lock` lists every resolved package. **On Linux, a `name = "rustix"` entry selects the separate-target-dir build plan.** `--cfg=rustix_use_libc` is harmless when rustix already uses libc, so no feature resolution is needed.

### Crates are read from `Cargo.toml` text, not from `cargo metadata`
The spec says crates come from `cargo metadata --no-deps --offline`. This plan reads `Cargo.toml` with a
line reader instead, parsing only the `[section]` headers and `key = "value"` lines it needs. Three reasons:
- The ranker then works on a machine with no cargo, as it works with no `go`.
- It runs nothing, so `--no-precise` has nothing to decline.
- Whether `cargo metadata` can write `Cargo.lock` is unverified. Task 2 step 1 measures it and records
  the result. Not calling cargo at all makes the answer irrelevant to the ranker.

`crate_of` walks up to the nearest `Cargo.toml` with `[package]`, so workspaces need no member list.
This is a deliberate narrowing of the spec; record it in the spec's amendment when Task 8 edits the docs.

### Lock waits are outside the timeout
- The build step has no wall-clock timeout, because it waits on cargo's lock the way cargo itself does.
- Only the proof run has one: 300 s, which is Go's value.

---

## File structure

| file | responsibility | created/modified in |
|---|---|---|
| `test-safety-net/assets/guard_env.py` | the tier/allow contract, the exit codes and the outcome words, shared by the go and rust wrappers | Task 1 (create) |
| `test-safety-net/assets/test_guard_env.py` | its tests | Task 1 (create) |
| `test-safety-net/assets/io_guard_go.py` | imports `guard_env`; no behaviour change | Task 1 (modify) |
| `test-safety-net/assets/stack_rust.py` | the stack interface: files, naming, lexing, grammar, discovery, reachability, markers, triage | Tasks 2–4 (create) |
| `test-safety-net/assets/test_stack_rust.py` | its tests | Tasks 2–4 (create) |
| `test-safety-net/assets/rank_risk.py` | registers `stack_rust` | Task 4 (modify) |
| `test-safety-net/assets/test_stack_node.py` | the registry test expects four stacks | Task 4 (modify) |
| `test-safety-net/assets/rust_binary.py` | pure inspection of an ELF or Mach-O executable | Task 5 (create) |
| `test-safety-net/assets/test_rust_binary.py` | its tests: synthetic headers, plus real binaries where cargo exists | Task 5 (create) |
| `test-safety-net/assets/io_guard_rust_hook.rs` | the hook's source; its table block is generated and checked in | Task 6 (create) |
| `test-safety-net/assets/io_guard_rust.py` | the tables and renderer (Task 6); the orchestration and classification (Task 7) | Tasks 6–7 (create) |
| `test-safety-net/assets/test_io_guard_rust.py` | the guard suite | Tasks 6–7 (create) |
| `test-safety-net/SKILL.md`, `README.md`, `references/{stacks,triage,parameters}.md` | wiring and residuals | Task 8 |
| `.github/workflows/skill-gates.yml` | the rust pin and the five rust legs | Task 8 |
| `test-safety-net/eval/run_eval.py` | checks 45–49 | Task 9 |
| `scripts/ab-validate.py` | the `SINCE_TSN_RUST*` rows | Task 9 |

---

### Task 1: `guard_env.py` — the shared tier/allow contract (behaviour-preserving)

**Files:**
- Create `test-safety-net/assets/guard_env.py` and `test-safety-net/assets/test_guard_env.py`.
- Modify `test-safety-net/assets/io_guard_go.py`: lines 181–257, holding `TIER_ENV`, `ALLOW_ENV`, `EXIT_*`, `read_env` and `blocked_groups`, plus the `_OUTCOME` table at about line 1213.

**Interfaces:**
- Produces:
  - `guard_env.TIER_ENV`, `ALLOW_ENV`;
  - `EXIT_GREEN, EXIT_RED, EXIT_NOT_ARMED, EXIT_TRIP, EXIT_NO_TEST, EXIT_NO_BUILD = 0..5`;
  - `OUTCOME: dict[int, str]`;
  - `read_env(env, controllable, uncontrollable, stack, why_uncontrollable) -> (tier: int, allow: list | None, notes: list[str])`;
  - `blocked_groups(tier, allow, groups, controllable, uncontrollable) -> set`.
- `io_guard_go.read_env(env)` and `io_guard_go.blocked_groups(tier, allow)` keep their current signatures, as thin wrappers, so every existing caller and test is untouched.

- [ ] **Step 1: Run the Go suites on green first.** `cd test-safety-net/assets && python3 test_io_guard_go.py && python3 test_stack_go.py`. Expected: `OK`. If either is red, stop: refactor on green only.
- [ ] **Step 2: Write `test_guard_env.py`.** It pins the contract with go's tables passed in:

```python
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard_env as g

GROUPS = ("clock", "database", "environment", "filesystem", "network", "randomness", "subprocess")
CTRL = ("clock", "environment", "filesystem")
UNCTRL = ("database", "network", "randomness", "subprocess")
WHY = lambda grp: "no test can control it"

def env(**kv):
    return g.read_env(kv, CTRL, UNCTRL, "go", WHY)

class TestReadEnv(unittest.TestCase):
    def test_absent_tier_is_tier_1_with_a_note(self):
        tier, allow, notes = env()
        self.assertEqual((tier, allow), (1, None))
        self.assertIn("defaulting to tier 1", notes[0])
    def test_none_allows_nothing(self):
        self.assertEqual(env(TEST_SAFETY_NET_TIER="2", TEST_SAFETY_NET_ALLOW="none")[1], [])
    def test_an_uncontrollable_group_is_named_and_stays_blocked(self):
        tier, allow, notes = env(TEST_SAFETY_NET_TIER="2", TEST_SAFETY_NET_ALLOW="filesystem,network")
        self.assertEqual(allow, ["filesystem"])
        self.assertIn("names network, which is not controllable on go (no test can control it)", notes[0])
    def test_an_unknown_group_is_ignored(self):
        self.assertIn("unknown group 'bogus'", env(TEST_SAFETY_NET_TIER="2", TEST_SAFETY_NET_ALLOW="bogus")[2][0])
    def test_tier_1_ignores_the_allow_list(self):
        self.assertIn("tier 1 ignores", env(TEST_SAFETY_NET_TIER="1", TEST_SAFETY_NET_ALLOW="clock")[2][-1])

class TestBlockedGroups(unittest.TestCase):
    def test_tier_1_blocks_everything(self):
        self.assertEqual(g.blocked_groups(1, ["clock"], GROUPS, CTRL, UNCTRL), set(GROUPS))
    def test_tier_2_default_blocks_only_the_uncontrollable(self):
        self.assertEqual(g.blocked_groups(2, None, GROUPS, CTRL, UNCTRL), set(UNCTRL))
    def test_tier_2_permits_exactly_what_it_names(self):
        self.assertEqual(g.blocked_groups(2, ["filesystem"], GROUPS, CTRL, UNCTRL),
                         set(GROUPS) - {"filesystem"})

class TestExitTable(unittest.TestCase):
    def test_six_outcomes_with_words(self):
        self.assertEqual(sorted(g.OUTCOME), [0, 1, 2, 3, 4, 5])

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run it.** Expected: `ModuleNotFoundError: No module named 'guard_env'`.
- [ ] **Step 4: Create `guard_env.py`.**
  - Move go's `read_env` body **verbatim** into `read_env(env, controllable, uncontrollable, stack, why_uncontrollable)`. Replace every `CONTROLLABLE_GROUPS`/`UNCONTROLLABLE_GROUPS` reference with the parameters.
  - Go's special note becomes `"%s names %s, which is not controllable on %s (%s); it stays blocked" % (ALLOW_ENV, g, stack, why_uncontrollable(g))`.
  - Move `blocked_groups` the same way, with `groups` as a parameter.
  - Move go's `EXIT_*` line, and its `_OUTCOME` table as `OUTCOME`.
  - The module docstring states:
    - the contract;
    - that classifying output stays per stack;
    - that `io_guard.py` keeps its own copy for now, a stated follow-up;
    - that `io_guard.js` cannot share Python.
- [ ] **Step 5: Point `io_guard_go.py` at it.**
  - `from guard_env import TIER_ENV, ALLOW_ENV, EXIT_GREEN, EXIT_RED, EXIT_NOT_ARMED, EXIT_TRIP, EXIT_NO_TEST, EXIT_NO_BUILD, OUTCOME as _OUTCOME` (sibling import; the module already puts `_ASSETS` on `sys.path`).
  - Replace the two function bodies:

```python
def _why_uncontrollable(g):
    return ("rand.Seed is a no-op since Go 1.24, so no test can seed the global source"
            if g == "randomness" else "no test can control it")

def read_env(env):
    """`(tier, allow, notes)`. The contract lives in guard_env.py; go supplies its tables."""
    return guard_env.read_env(env, CONTROLLABLE_GROUPS, UNCONTROLLABLE_GROUPS, "go",
                              _why_uncontrollable)

def blocked_groups(tier, allow):
    """The groups a run at `tier` blocks. Tier 1: all. Tier 2: all but what it fakes."""
    return guard_env.blocked_groups(tier, allow, GROUPS, CONTROLLABLE_GROUPS,
                                    UNCONTROLLABLE_GROUPS)
```

- [ ] **Step 6: Run all three suites.** `python3 test_guard_env.py && python3 test_io_guard_go.py && python3 test_stack_go.py`. Expected: `OK`, `OK`, `OK`, with the **same test counts as step 1**. A changed count means behaviour moved.
- [ ] **Step 7: Mutation.** In `guard_env.blocked_groups`, make tier 1 return `set(uncontrollable)`. `TestBlockedGroups.test_tier_1_blocks_everything` **and** `test_io_guard_go.py`'s `TestEnvContract` must both go red. Revert.
- [ ] **Step 8: `make gate-skill SKILL=test-safety-net`**, then commit: `refactor(test-safety-net): guard_env.py — the tier/allow contract go and rust share`.

---

### Task 2: `stack_rust.py` — identity, files, naming, lexing, grammar

**Files:** Create `test-safety-net/assets/stack_rust.py` and `test-safety-net/assets/test_stack_rust.py`.

**Interfaces:**
- Consumes `stack_common.{SKIP_DIRS, evidence_score, read_text}`.
- Produces:
  - identity: `STACK_NAME = "rust"`, `MANIFESTS = ("Cargo.toml",)`, `classify_manifest(name, text) -> "declaring"|"tooling"`, `evidence(root) -> int`;
  - files: `iter_source_files(root, include_tests=False)`, `is_test_path(rel)`, `is_test_for(test_rel, src_rel)`, `scope_files(rel, all_files) -> [rel]`;
  - naming: `module_of(rel) -> str`, `name_pattern(name, module=None)`, `path_pattern(rel)`;
  - lexing: `IDENTIFIER_RE`, `preceding_qualifier(text, start)`, `strip_noncode(text, keep_strings=False) -> str`;
  - grammar: `module_bindings(module, text, *, src_rel, ref_rel) -> (aliases, names)`, `reached_through_module(module, name, text, *, src_rel, ref_rel) -> bool`;
  - crates: `crate_of(root, rel) -> CrateInfo | None`, where `CrateInfo = namedtuple("CrateInfo", "dir name lib bins")` holds repo-relative paths and the crate name as used in `use` paths (`-` → `_`, or `[lib] name`).

**The rules to implement and test** (every row is a test, and each negative is marked):

| function | rule |
|---|---|
| `classify_manifest` | `declaring` when `Cargo.toml` has a `[package]` or `[workspace]` table; `tooling` otherwise (NEGATIVE: a `Cargo.toml` holding only `[profile.release]`) |
| `crate_of` | walks up from `rel` to the nearest `Cargo.toml` with `[package]`; reads `name`, `[lib] path` (default `src/lib.rs` if the file exists), and `[[bin]] path`s (default `src/main.rs`, `src/bin/*.rs`). Parses TOML line by line, since python 3.10 has no `tomllib`: only `[section]` headers and `key = "value"` lines are read |
| `iter_source_files` | `*.rs` files, skipping `SKIP_DIRS`, `target/`, `vendor/`, and any dir starting with `.` or `_`; skipping `examples/`, `benches/` and `build.rs` at a crate root (NEGATIVE, each); `tests/` files only when `include_tests` |
| `is_test_path` | a path whose segment directly under a crate root is `tests` |
| `is_test_for` | the test and the source share a crate (the same `crate_of(...).dir`) |
| `scope_files` | `[rel]`: a module is its file |
| `module_of` | the module path's last segment: `src/calc/add.rs` → `add`; `src/calc/mod.rs` → `calc`; `src/lib.rs` → the crate name; `src/main.rs` → `main` |
| `path_pattern` | the module path, bounded at both ends: `(?<![A-Za-z0-9_:])(?:crate::|<crate>::)?calc::add(?![A-Za-z0-9_])` |
| `IDENTIFIER_RE` | `(?<![A-Za-z0-9_])(?:r#)?[A-Za-z_][A-Za-z0-9_]*` (raw identifiers included) |
| `preceding_qualifier` | the identifier before `::` or `.`: `None` when bare, `""` when unnameable (`foo()::x`) |
| `strip_noncode` | blanks comments and literals, preserving every offset and newline: `//` to the end of the line (including `///` and `//!`); **nested** `/* /* */ */`; `"…"` with escapes; raw strings `r"…"`, `r#"…"#`, `r##"…"##`; byte strings `b"…"`, `br#"…"#`; char literals `'a'`, `'\n'`, `'\u{1F600}'`, `'\''`. A **lifetime** `'a` or `'static` is NOT a char literal: the rule is that `'` followed by one char or one escape, then `'`, is a char; anything else is a lifetime. NEGATIVE: `fn f<'a>(x: &'a str)` keeps both lifetimes as code |
| `module_bindings` | reads `use` declarations: `use <crate>::a::b;` → alias `b`; `use <crate>::a::{b, c as d};` → names `b`, `d`; `use <crate>::a::*;` → name `"*"`; `use super::*;` within a `#[cfg(test)]` module of `src_rel` itself → `"*"`; `use crate::…` counts only when `ref_rel` is in the same crate. A path that resolves to another module binds nothing (NEGATIVE): the predicate may under-credit, never over-credit |
| `reached_through_module` | `alias::name(`, or bare `name(` after a binding of `name` or `*` |
| `name_pattern(name, module)` | a plain name is word-bounded. `Type::m` is credited **only** by a call on a value bound to `Type` inside the same `#[test] fn` body: `let x = Type::new(…)`, `Type { … }`, `Type::default()`, `let x: Type`, or a `x: &Type`/`x: Type` parameter; or by the direct `Type::m(`. `Type` may be qualified only by the unit's own `module` or crate name. NEGATIVE: `(&other::Type{}).m()` and `let r = other::Type::new(); r.m()` credit nothing. These are the three shapes Go's second review broke |

- [ ] **Step 1: Verify the one unverified spec assumption.** In a scratch crate with no `Cargo.lock`, run `RUSTUP_AUTO_INSTALL=0 cargo metadata --no-deps --offline --format-version 1 >/dev/null; ls Cargo.lock`. Then repeat in a crate whose `Cargo.lock` is stale.
  - Whatever the result, `crate_of` uses the line reader (see "Crates are read from `Cargo.toml` text"). The measurement matters to anyone who later proposes calling cargo.
  - Record the result in a comment in `stack_rust.py` above `crate_of`, with the version it was measured on.
  - Also verify that `RUSTUP_AUTO_INSTALL=0 rustc -V` in a directory whose `rust-toolchain.toml` pins an uninstalled version fails without downloading anything. If rustup ignores the variable, record the rustup version and make the guard check `rustup toolchain list` before calling `rustc`.
- [ ] **Step 2: Write the tests for every row of the table above.**
  - Classes: `TestStripper`, `TestCrates`, `TestFiles`, `TestNaming`, `TestBindingGrammar`, `TestMethodCredit`.
  - Reuse `test_stack_go.py`'s `_load`, `write` and temp-root pattern; copy them, since they are 10 lines.
  - `TestStripper` must include the offset-preservation property test that go's has: output length equals input length, and the newline positions are identical.
- [ ] **Step 3: Run it.** Expected: every test errors with `No module named 'stack_rust'`.
- [ ] **Step 4: Implement `stack_rust.py` down to the grammar.**
  - The module docstring names the spec and the "filter, not enforcement" rule, like `stack_go.py`'s.
- [ ] **Step 5: Run it until green.**
- [ ] **Step 6: Mutation.** Each of these must turn at least one test red:
  - making `strip_noncode` treat `'a` as a char literal;
  - dropping nested-comment depth;
  - dropping the `r#` raw-string rule;
  - letting `name_pattern` accept any qualifier.

  Revert each, and name the killing test in a comment beside the rule.
- [ ] **Step 7: Commit.** `feat(test-safety-net): stack_rust.py — Rust files, naming, lexing and import grammar`. **Record this commit hash**: it is `SINCE_TSN_RUST` in Task 9.

---

### Task 3: Rust heuristic discovery and reachability

**Files:** Modify `stack_rust.py` and `test_stack_rust.py`.

**Interfaces:**
- Consumes Task 2's `strip_noncode`, `crate_of` and `iter_source_files`.
- Produces:
  - `discover_units(root, precise=True) -> (units, "heuristic")`;
  - `reachability(root, unit) -> (reachable: bool, why: str)`, where `why` is `""` when reachable; otherwise it is `"binary-only"` or the specific reason, e.g. `"module calc::inner is private"` or `"pub(crate)"`.

**Discovery.** On `strip_noncode` output, it finds these declarations, anchored by declaration position the way `stack_go._FUNC_AT` is, never by column 0:
- `fn` items at brace depth 0 of the file, or inside an inline `mod name { … }`, whose name joins the module path;
- items inside an inherent `impl` block: `impl<…> Type<…> { … }`, never `impl Trait for Type`.

Visibility is the token before `fn`, after any `const`/`async`/`unsafe`/`extern "…"` qualifiers: `pub`, or `pub(<…>)`. `#[cfg(test)]` modules are skipped whole. The `lineno` is the line of the `fn` keyword.

**Reachability.**
- Walk from the crate's lib root:
  - `mod name;` resolves to `name.rs` or `name/mod.rs` beside the declaring file's module directory, honouring `#[path = "…"]`;
  - an inline `mod name { }` resolves to itself.
- A module is public when every `mod` on its path is declared `pub mod`.
- Named or glob `pub use` in a public module makes the named item, or every item of the glob-imported module, reachable. The paths it accepts are `crate::…`, `self::…`, `super::…` and plain relative ones.
- A unit in a file reached only from a bin root is `binary-only`.

**Tests (`TestHeuristicDiscovery`, `TestReachability`):**
- Every fn shape is found: `pub fn`, `pub const fn`, `pub async fn`, `pub unsafe fn`, `pub extern "C" fn`, a generic `pub fn f<T: Into<String>>(…)` with `where` clauses, a multi-line signature, and a method in `impl<T> Wrapper<T> { pub fn get(&self) }`.
- NEGATIVE: none of these is a unit:
  - a private `fn`;
  - a fn inside `#[cfg(test)] mod tests`;
  - a fn named inside a string or comment;
  - a method of `impl Display for T`;
  - a fn in `examples/`, `benches/`, `build.rs`, `vendor/` or `target/`.
- Reachability, one test per line:

| input | expected |
|---|---|
| `pub mod a; pub mod b;` chain | reachable |
| a private `mod inner;` | `not reachable … private` |
| a private `mod inner;` plus `pub use inner::exported;` | `exported` reachable, its sibling not |
| `pub use inner::*;` | every `pub` item of `inner` reachable |
| `pub(crate) fn` | unreachable, `pub(crate)` |
| `#[path = "x/y.rs"] pub mod z;` | resolved |
| `src/main.rs` in a crate that also has `lib.rs` | `binary-only` |
| a crate with only `src/main.rs` | every unit `binary-only` |

- [ ] **Step 1: Write the tests.**
- [ ] **Step 2: Run them.** They fail (`discover_units` is undefined).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them until green.**
- [ ] **Step 5: Mutations.** Each must be killed by a named test:
  - treating `impl Trait for T` as inherent;
  - dropping the `#[cfg(test)]` skip;
  - making a private `mod` count as public;
  - ignoring `pub use`.
- [ ] **Step 6: Commit.** `feat(test-safety-net): Rust heuristic discovery and tests/-reachability`.

---

### Task 4: Rust markers, triage, registration and detection

**Files:**
- Modify `stack_rust.py` and `test_stack_rust.py`.
- Modify `rank_risk.py`: the import at line 127, and `STACKS` at line 145.
- Modify `test_stack_node.py`, whose registry test expects `["go", "node", "python"]`.

**Interfaces:**
- Produces:
  - `CONTROLLABLE: dict[group, tuple[marker]]` and `UNCONTROLLABLE: dict[group, tuple[marker]]`;
  - `GROUPS = tuple(sorted(set(CONTROLLABLE) | set(UNCONTROLLABLE)))`;
  - `STATIC_DECLINE: dict[reason, tuple[marker]]`;
  - `triage(root, unit) -> (tier, reason)`.
- Consumes Task 3's `reachability`.

**Markers** are qualified paths matched against the unit's reach. Use the spec's table verbatim for these groups:
- `CONTROLLABLE`: filesystem, environment;
- `UNCONTROLLABLE`: network, subprocess, clock, randomness, database.

Extras:
- `std::fs::*`, `File::open`/`create`, `OpenOptions`, and the disk-touching `Path` methods: `exists`, `is_file`, `is_dir`, `metadata`, `symlink_metadata`, `read_dir`, `read_link`, `canonicalize`, `try_exists`.
- A `use` of `std::fs` followed by a bare `read_to_string(` is credited through `module_bindings`, as go and node do.

`STATIC_DECLINE = {"inline assembly": ("asm!", "global_asm!", "naked_asm!"), "raw syscall": ("libc::syscall", "nix::libc::syscall")}`.

**Triage order**, first match wins:
1. a static decline in the reach → 4;
2. unreachable → 3, with `reachability`'s reason;
3. an uncontrollable marker → 3;
4. a controllable marker → 2;
5. otherwise → 1.

**Reach** is the unit's body, plus the bodies of same-file functions it calls, transitively. It also includes the initialiser of any same-file `static`/`LazyLock`/`Lazy`/`lazy_static!` item the body names, so lazy statics are judged at the unit's call.

- [ ] **Step 1: Write `TestTriageByGroup`.** One unit per group, each landing in its tier: a pure fn is 1; `fs::read` is 2; `env::var` is 2; `TcpStream::connect` is 3; `Command::new` is 3; `SystemTime::now` is 3; `rand::thread_rng` is 3; `rusqlite::Connection::open` is 3; `asm!("nop")` is 4.
- [ ] **Step 2: Write `TestTriageReach`.**
  - A pure fn calling a same-file fn that calls `fs::read` → 2.
  - A fn naming `static CFG: LazyLock<String> = LazyLock::new(|| std::env::var("X").unwrap())` → 2.
  - NEGATIVE: a seeded `StdRng::seed_from_u64(1)` → 1.
  - NEGATIVE: `fs` appearing only in a comment or string → 1.
  - NEGATIVE: `std::io::stdin` → 1, because stdin is guard-only.
- [ ] **Step 3: Write `TestThroughTheCore`,** mirroring go's:
  - `rank_risk.stack_by_name("rust")` is `stack_rust`;
  - a Rust tree (`Cargo.toml` with `[package]`, 6 `.rs` files) is detected `rust`;
  - an existing `tests/it.rs` calling `calcx::calc::pure(1, 2)` credits `pure`;
  - NEGATIVE: `tests/it.rs` calling `other::pure(1, 2)` credits nothing.
- [ ] **Step 4: Update the registry test in `test_stack_node.py`** to expect `["go", "node", "python", "rust"]`. This is the one edit to an existing test the plan makes on purpose.
- [ ] **Step 5: Run everything.** It fails until implemented.
- [ ] **Step 6: Implement the markers and triage, then register the stack.** Add `import stack_rust`, and `STACKS = [stack_go, stack_node, stack_python, stack_rust]`.
- [ ] **Step 7: Green, plus the identity proof.**
  - Run every suite: `test_stack_rust`, `test_rank_risk`, `test_stack_go`, `test_stack_node`, `test_stack_python` if it exists, `test_io_guard`.
  - Prove python and node are unchanged: rank this repo with HEAD's assets and with the working tree's, `rank_risk.py <repo> --top-n 50`, stdout only. The two outputs must be byte-identical. Record this in the commit message.
- [ ] **Step 8: Mutations.**
  - Swapping filesystem into `UNCONTROLLABLE` kills `TestTriageByGroup`.
  - Dropping the lazy-static reach kills `TestTriageReach`.
- [ ] **Step 9: Commit.** `feat(test-safety-net): Rust markers, triage and registration`.

---

### Task 5: `rust_binary.py` — pure inspection of the test binary

**Files:** Create `test-safety-net/assets/rust_binary.py` and `test-safety-net/assets/test_rust_binary.py`.

**Interfaces:**
- Produces:
  - `BinaryFacts = namedtuple("BinaryFacts", "fmt dynamic libc crate_symbols")`, where:
    - `fmt` is one of `"elf64"`, `"macho64"`, `"other"`;
    - `dynamic` is a bool;
    - `libc` is one of `"glibc"`, `"musl"`, `"darwin"`, `None`;
    - `crate_symbols` is an int: the count of Rust-mangled symbols with the `17h<16hex>E` hash suffix whose crate is not `std`, `core`, `alloc` or `test`.
  - `inspect(path) -> BinaryFacts`;
  - `refusal(facts) -> str | None`, which returns the reason the hook would fail open, or `None`.

**Rules:**

| format | rule |
|---|---|
| ELF64 LE | `PT_INTERP` present → `dynamic`; an interpreter path containing `ld-musl` → `musl`, any other → `glibc`; symbols come from `SHT_SYMTAB` + its linked `SHT_STRTAB` (only the static `.symtab` counts, because `.dynsym` lacks crate functions, as Spike A measured) |
| Mach-O 64 (`0xfeedfacf`) | `LC_LOAD_DYLINKER` present → `dynamic`; `libc = "darwin"`; symbols from `LC_SYMTAB`'s nlist entries and string table (`__ZN…` with the leading underscore) |
| other | `fmt = "other"` |

`refusal` returns, in this order:
- `"not a 64-bit ELF or Mach-O executable"`;
- `"statically linked: LD_PRELOAD cannot load a hook"`;
- `"musl: the hook is built for glibc's dynamic loader"`;
- `"stripped: no crate symbols, so no call can be attributed"`;
- otherwise `None`.

- [ ] **Step 1: Write the tests with synthetic binaries built in the test with `struct`.** A 60-line helper builds exactly what `inspect` reads, and nothing else:

```python
import struct

def elf(interp=None, symbols=()):
    """A minimal ELF64 LE image: header, optional PT_INTERP, optional .symtab/.strtab."""
    nph = 1 if interp else 0
    interp_b = (interp.encode() + b"\0") if interp else b""
    off = 64 + 56 * nph
    interp_off, off = off, off + len(interp_b)
    strtab = b"\0" + b"".join(s.encode() + b"\0" for s in symbols)
    str_off, off = off, off + len(strtab)
    syms, pos = struct.pack("<IBBHQQ", 0, 0, 0, 0, 0, 0), 1
    for s in symbols:                       # STB_GLOBAL | STT_FUNC, defined, sized
        syms += struct.pack("<IBBHQQ", pos, 0x12, 0, 1, 0x1000, 16)
        pos += len(s) + 1
    sym_off, off = off, off + len(syms)
    shstr = b"\0.symtab\0.strtab\0.shstrtab\0"
    shstr_off, off = off, off + len(shstr)
    sh = [struct.pack("<IIQQQQIIQQ", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)]
    if symbols:
        sh.append(struct.pack("<IIQQQQIIQQ", 1, 2, 0, 0, sym_off, len(syms), 2, 1, 8, 24))
        sh.append(struct.pack("<IIQQQQIIQQ", 9, 3, 0, 0, str_off, len(strtab), 0, 0, 1, 0))
    sh.append(struct.pack("<IIQQQQIIQQ", 17, 3, 0, 0, shstr_off, len(shstr), 0, 0, 1, 0))
    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\0" * 8          # 64-bit, little-endian
    ehdr = ident + struct.pack("<HHIQQQIHHHHHH", 2, 0xB7, 1, 0, 64 if nph else 0, off, 0,
                               64, 56, nph, 64, len(sh), len(sh) - 1)
    phdr = (struct.pack("<IIQQQQQQ", 3, 4, interp_off, 0, 0, len(interp_b), len(interp_b), 1)
            if nph else b"")
    return ehdr + phdr + interp_b + strtab + syms + shstr + b"".join(sh)

def macho(dylinker=True, symbols=()):
    """A minimal 64-bit Mach-O: header, optional LC_LOAD_DYLINKER, optional LC_SYMTAB."""
    cmds = b""
    if dylinker:
        name = b"/usr/lib/dyld\0".ljust(20, b"\0")       # cmdsize 32: a multiple of 8
        cmds += struct.pack("<III", 0xE, 12 + len(name), 12) + name          # LC_LOAD_DYLINKER
    strtab = b"\0" + b"".join(s.encode() + b"\0" for s in symbols)
    ncmds = (1 if dylinker else 0) + (1 if symbols else 0)
    header_len = 32 + len(cmds) + (24 if symbols else 0)
    if symbols:
        sym_off = header_len
        str_off = sym_off + 16 * len(symbols)
        cmds += struct.pack("<IIIIII", 0x2, 24, sym_off, len(symbols), str_off, len(strtab))
    nl, pos = b"", 1
    for s in symbols:                       # N_SECT | N_EXT
        nl += struct.pack("<IBBHQ", pos, 0x0F, 1, 0, 0x100000000)
        pos += len(s) + 1
    hdr = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, ncmds, len(cmds), 0, 0)
    return hdr + cmds + nl + (strtab if symbols else b"")
```

   Write each image to a temp file and pass its path to `inspect`. The ELF cases, one test each:
  - dynamic glibc with a crate symbol `_ZN5calcx4calc4pure17h0123456789abcdefE` → `refusal is None`;
  - no `PT_INTERP` → `statically linked`;
  - interpreter `/lib/ld-musl-aarch64.so.1` → `musl`;
  - a dynamic binary with only `_ZN3std2io5stdin17h0123456789abcdefE` → `stripped`;
  - `b"\x7fELF\x01…"` (32-bit) → `not a 64-bit`.

   The Mach-O cases, one test each:
  - `macho(symbols=["__ZN5calcx4calc4pure17h0123456789abcdefE"])` → `refusal is None`;
  - `macho(dylinker=False, symbols=[…the same…])` → `statically linked`;
  - `macho(symbols=["__ZN3std2io5stdin17h0123456789abcdefE"])` → `stripped`;
  - a header whose magic is `0xFEEDFACE` (32-bit) → `not a 64-bit`.
- [ ] **Step 2: Add `TestRealBinaries`,** which skips visibly without `cargo`.
  - Build a tiny crate's test binary (`cargo test --no-run --message-format=json`, into a temp `CARGO_TARGET_DIR`) and assert `refusal is None`.
  - Copy it, run `strip` on the copy, and assert `stripped`.
- [ ] **Step 3: Run.** It fails with `No module named 'rust_binary'`.
- [ ] **Step 4: Implement.** Use pure `struct` reads with bounds checks: a truncated file is `"other"`, never an exception.
- [ ] **Step 5: Green on darwin and in `rust:1.92`.** Use the tar-stream form: docker cannot see scratch paths.

```bash
tar -C test-safety-net -cf - . | docker run --rm -i rust:1.92 sh -c \
  'mkdir /s && tar -x -C /s && cd /s/assets && apt-get -qq update >/dev/null && \
   apt-get -qq install -y python3 >/dev/null && python3 test_rust_binary.py'
```

- [ ] **Step 6: Mutation.** Making `refusal` ignore `crate_symbols` must turn the `stripped` tests red.
- [ ] **Step 7: Commit.** `feat(test-safety-net): rust_binary.py — refuse the binaries a preloaded hook cannot guard`.

---

### Task 6: The hook and its rendered tables

**Files:**
- Create `test-safety-net/assets/io_guard_rust_hook.rs`, starting from Appendix A.
- Create `test-safety-net/assets/io_guard_rust.py`, holding the tables, the renderer and `hook_library`.
- Create `test-safety-net/assets/test_io_guard_rust.py`, holding the hook-level tests.

**Interfaces:**
- Produces, in `io_guard_rust.py`:
  - constants: `MIN_RUST = (1, 82)`; `TRANSPARENT_CRATES = ("std", "core", "alloc", "panic_unwind", "backtrace", "hashbrown", "std_detect")`; `RUNNER_CRATES = ("test",)`; `SEED_MARKERS = ("hashmap_random_keys",)`; `THREAD_START_MARKERS = ("thread_start", "spawn_unchecked_")`; `CONTROL_HELPERS = {"tsn_control_temp_dir": "filesystem", "tsn_control_set_env": "environment", "TsnControlEnv": "environment"}`;
  - `INTERCEPTS: dict[platform, dict[group, tuple[libc_name]]]`, from the spec's table: `darwin` adds `mach_absolute_time` and `clock_gettime_nsec_np`; `linux` adds `open64` and `statx`;
  - functions: `render_tables() -> str`, `render_hook() -> str`, `hook_library(repo, env) -> str` (a path to the built library);
  - the hook's environment contract: `TSN_BLOCKED` (the comma-joined blocked groups; the hook arms **only** when this is present), `TSN_TIER`, `TSN_STDIN=1` (set at tier 1);
  - the hook's handshake: the constructor writes `tsn-hook: armed\n` to fd 2; on a failed self-check it writes `tsn-hook: cannot attribute (<why>)\n` and calls `_exit(2)`.

**Changes from Appendix A (the spike), each with its test:**

1. **One place for every name.**
   - The hook source carries a block between `// @@TSN-TABLES-BEGIN@@` and `// @@TSN-TABLES-END@@` that defines `TRANSPARENT`, `RUNNER`, `SEED`, `THREAD_START` and `CONTROL: &[(&[u8], &[u8])]`.
   - `render_tables()` emits that block from the Python constants, and `render_hook()` substitutes it.
   - **The committed block is the rendered one.** Test: `test_the_committed_table_block_is_the_rendered_one` compares them byte for byte, so the Python tables are the single source and the asset stays compilable.
   - `classify()` in the hook uses these tables instead of its literals.
2. **Every intercept in `INTERCEPTS`,** per platform. The spike covered 7 (darwin) and 9 (linux); add the rest: the stat family and `access`/`mkdir`/`unlink`/`rename`/`opendir`/`openat`; `setenv`/`unsetenv`; `connect`/`bind`/`getaddrinfo`; `gettimeofday`; `getentropy`/`arc4random_buf`; `posix_spawn`/`fork`/`execve`.
   - Variadic `open`/`openat`: use fixed arguments in the ABI's slots, as in the appendix. That is 3 arguments on Linux, and on Apple arm64 `mode` is read as the 9th integer slot.
   - Test: `test_every_intercept_exists_in_the_built_library` lists the exported symbols (`nm -D` on Linux, or the `__interpose` entries through `otool -l` on darwin) against `INTERCEPTS`.
3. **Blocking by group and tier.** The spike's `TSN_MODE` switch (`off`/`log`/`guard`) is **removed**: an unset `TSN_BLOCKED` means off, and a debugging log is not a shipped mode. The hook reads `TSN_BLOCKED` once, with the real `getenv`, and a call trips only when its group is blocked. `stdin` trips only when `TSN_STDIN=1`. The message is `IOGuardViolation: a tier <N> candidate reached <group> I/O via <call> from <symbol>`, the same shape go uses, so one label regex serves both.
4. **Control helpers override the group.** A frame matching a `CONTROL` name maps the call to that helper's group, as go's `TESTING_CONTROLS` do.
5. **The fail-closed thread rule.**
   - Some stacks have a `THREAD_START` frame, but no crate frame and no runner frame. Such a stack is a spawned thread running only std code, e.g. `thread::spawn(std::env::vars)`. It is **judged**, attributed as `(a thread with no crate frame)`.
   - A stack with no Rust frames at all (dyld or libSystem initialising before `main`) stays exempt.
6. **The arm handshake and self-check.** In the constructor, when `TSN_BLOCKED` is set:
   - `backtrace()` must return at least 2 frames;
   - the executable's `main` must resolve by name, through `dladdr` on darwin or `symtab::lookup` on linux;
   - on success it writes `tsn-hook: armed`, and otherwise it writes the `cannot attribute` line and calls `_exit(2)`.

   This closes Spike A2's 1.82 fail-open: without `force-unwind-tables`, the self-check fails, and the run stops rather than passing.
7. **`hook_library(repo, env)`.**
   - It hashes `render_hook()` together with the output of `rustc -vV`, run in `repo` with `RUSTUP_AUTO_INSTALL=0`.
   - It builds into `<cache>/rust-hook/<sha256>/libtsn_hook.{so,dylib}`, where the cache is `$TEST_SAFETY_NET_CACHE` or `~/.cache/test-safety-net`. The build writes to a temp name and then calls `os.replace`, so a concurrent run never sees half a library.
   - The build command is the Global Constraints' `rustc` line.
   - A build failure raises `GuardCannotArm`, whose message includes the compiler's first error line.

**Hook-level tests** build a probe crate once per class, in a temp `CARGO_TARGET_DIR`, and run its test binary directly with the preload (`DYLD_INSERT_LIBRARIES` or `LD_PRELOAD`). They skip visibly without `cargo`. The probe crate mirrors the spike's `probe/`, with these tests:
- `t_pure` and `t_hashmap` → exit 0;
- `t_fail` → 101;
- `t_env`, `t_fs`, `t_meta`, `t_tcp`, `t_sysnow`, `t_instant`, `t_spawn`, `t_catch` (a read inside `catch_unwind`), `t_thread` (a read in a spawned closure) and `t_std_thread` (`thread::spawn(std::env::vars)`) → exit 3, each with the right group;
- `t_stdin` → exit 3 at tier 1 and 0 at tier 2;
- `t_tmp_control` (`tsn_control_temp_dir` + `fs::write` into it) → 0 at tier 2 with filesystem allowed, and 3 with it blocked;
- `t_env_control` (`tsn_control_set_env`, then the unit reads the variable) → 0 at tier 2 with environment allowed; the restore on drop must not trip.
- `t_dep_read`: the probe depends on a local path crate, `depx`, whose `pub fn read()` calls `fs::read("/etc/hosts")`. At tier 1 it exits 3, attributed to `depx`. Third-party frames are attributable, as on go and node; the path dependency keeps the run offline.
- Without `TSN_BLOCKED` set, every test passes.
- The attribution names the probe function, e.g. `from _ZN5probe7env_var`.

- [ ] **Step 1: Copy Appendix A** to `io_guard_rust_hook.rs`, and confirm it builds with the constraint's `rustc` line on the local stable toolchain.
- [ ] **Step 2: Write the hook-level tests.** They fail: the tables, the handshake and the controls don't exist yet.
- [ ] **Step 3: Implement changes 1–7,** one at a time, re-running after each.
- [ ] **Step 4: Green on darwin**, then in `rust:1.92` and `rust:1.82` containers (tar-stream, as in Task 5). 1.82 cannot link on a macOS 27 SDK host, so its proof is Linux-only by necessity.
- [ ] **Step 5: Mutations.** Each must be killed by a named test:
  - removing `-C force-unwind-tables=yes` fails the self-check on 1.82, so exit 2 is expected, not 0;
  - deleting the thread rule lets `t_std_thread` pass;
  - dropping `TsnControlEnv` from `CONTROL_HELPERS` makes the restore in `t_env_control` trip;
  - editing a table in Python without re-rendering fails the block test.
- [ ] **Step 6: Commit.** `feat(test-safety-net): the Rust interposition hook, with rendered tables, a handshake and a fail-closed thread rule`.

---

### Task 7: `io_guard_rust.py` — the wrapper

**Files:** Modify `io_guard_rust.py` and `test_io_guard_rust.py`.

**Interfaces:**
- Consumes:
  - `guard_env`;
  - `rust_binary.inspect`/`refusal`;
  - Task 6's `hook_library` and tables;
  - `stack_rust.CONTROLLABLE`/`UNCONTROLLABLE`/`GROUPS`.
- Produces the CLI:

```sh
TEST_SAFETY_NET_TIER=1 \
  python3 "$SKILL_DIR/assets/io_guard_rust.py" \
  --test <test_file_stem> <test_name>
```

  Options: `-p <package>` for a workspace member. The command runs in the repo (or member) root.
- Produces the API:
  - `toolchain_version(repo, env) -> (major, minor)`;
  - `BuildPlan = namedtuple("BuildPlan", "target_dir cfg reason")`;
  - `build_plan(repo, platform) -> BuildPlan`;
  - `BuildResult = namedtuple("BuildResult", "exe compile_error output")`;
  - `build_test(repo, plan, test_target, package, env) -> BuildResult`;
  - `run_proof(exe, test_name, tier, allow, hook, env) -> (code, output)`;
  - `classify(code, output) -> int`;
  - `demangle(sym) -> str`;
  - `main(argv) -> int`;
  - the tables `FILTER_MARKER_INTERCEPTS`, `PARTIALLY_INTERCEPTED`, `NOT_INTERCEPTED`.

**`main(argv)`**, in order. Every refusal prints its reason and exits `EXIT_NOT_ARMED`:
1. `tier, allow, notes = guard_env.read_env(os.environ, CONTROLLABLE_GROUPS, UNCONTROLLABLE_GROUPS, "rust", _why)`. The module defines `GROUPS = stack_rust.GROUPS`, `CONTROLLABLE_GROUPS = tuple(sorted(stack_rust.CONTROLLABLE))` and `UNCONTROLLABLE_GROUPS = tuple(sorted(stack_rust.UNCONTROLLABLE))`, the same way `io_guard_go.py` derives its tables. `_why(g)` returns `"std has no freeze hook"` for clock, `"std has no RNG and rand::thread_rng cannot be seeded"` for randomness, and `"no test can control it"` otherwise.
2. The platform is darwin or linux.
3. `toolchain_version(repo)` ≥ `MIN_RUST`. It runs `rustc -V` in `repo` with `RUSTUP_AUTO_INSTALL=0`. A missing pinned toolchain and a version below the floor are refused with different reasons.
4. `Cargo.lock` exists. Otherwise: `"no Cargo.lock: run cargo generate-lockfile, then re-run; the proof never writes one"`.
5. The environment-test rule: if `tests/<stem>.rs` calls `tsn_control_set_env` and has more than one `#[test]`, refuse with `"an environment-controlled test must be alone in its file"`.
6. `plan = build_plan(repo, sys.platform)`.
   - On linux with `name = "rustix"` in `Cargo.lock`, the plan is `BuildPlan(<cache>/rust-target/<sha256(realpath(repo))>, ("--cfg=rustix_use_libc",), "rustix's linux_raw backend bypasses libc")`.
   - Otherwise it is `BuildPlan(None, (), "")`.
   - Print the reason when it is non-empty.
7. `build_test(...)` runs `cargo test --locked --no-run --message-format=json [-p pkg] --test <stem>`, with `RUSTUP_AUTO_INSTALL=0`, `CARGO_TARGET_DIR=plan.target_dir` when set, and `RUSTFLAGS=" ".join(plan.cfg)` when set. It has **no timeout**.
   - Parse the JSON lines. `reason == "compiler-artifact"` with `"test" in target.kind` and a non-null `executable` gives `exe`.
   - A `compiler-message` at level `error`, or a non-zero exit with no executable, gives `compile_error`, and the run exits `EXIT_NO_BUILD`.
   - A `--locked` refusal (`"needs to be updated but --locked was passed"`) exits 2 with `"Cargo.lock is out of date for this manifest; update it yourself, then re-run"`.
8. `rust_binary.refusal(rust_binary.inspect(exe))` → exit 2 with the reason.
9. `hook = hook_library(repo, env)`.
10. `run_proof` runs `[exe, test_name, "--exact", "--test-threads=1"]` with `stdin=DEVNULL` and `timeout=300`. The environment adds:
    - `TSN_BLOCKED=",".join(sorted(guard_env.blocked_groups(tier, allow, GROUPS, …)))`;
    - `TSN_TIER`;
    - `TSN_STDIN=1` at tier 1;
    - the preload variable.

    It tees the combined output to stdout.
11. `classify(code, output)`, then print `guard_env.OUTCOME[result]`, with the demangled attribution on a trip.

**`classify`**, first match wins:
1. a line starting `IOGuardViolation` → 3;
2. no `tsn-hook: armed` line → 2;
3. `test result: … (\d+) passed; (\d+) failed; (\d+) ignored`: failed ≥ 1 → 1; passed == 1 and ignored == 0 → 0; otherwise → 4;
4. no result line, with a non-zero code → 1, with the note `"the test binary ended without a result line (signal or abort)"`.

**The partition.** `FILTER_MARKER_INTERCEPTS` maps each marker in `stack_rust.CONTROLLABLE`/`UNCONTROLLABLE` to the libc names in `INTERCEPTS` that catch it. Examples:
- `std::fs::read` → `open`/`open64`/`openat`;
- `std::env::var` → `getenv`;
- `std::net::TcpStream::connect` → `socket`/`connect`;
- `tokio::time` → `clock_gettime`.

`PARTIALLY_INTERCEPTED` and `NOT_INTERCEPTED` hold the rest, each with its reason. For example:
- `std::thread::sleep` sleeps without reading a clock: `nanosleep` is not intercepted, the same as go's `time.Sleep` residual;
- database crates are caught only through the socket or file they open.

- [ ] **Step 1: Write the wrapper tests.** Classes mirror go's; each uses a fixture crate written under a temp dir with a committed `Cargo.lock`.
  - `TestTheDocumentedCommand` extracts every `^TEST_SAFETY_NET_TIER=[12] .*python3 .*io_guard_rust\.py` line from this module's header and every `.md` file in the skill, using go's `extract_commands`, retargeted, and runs each command both ways. Zero extracted commands is a failure.
  - `TestEveryGroupTrips`: one unit per group, at tier 1, asserting the label.
  - `TestTierTwo`: `tsn_control_temp_dir` with `filesystem` allowed → 0, and with `clock` allowed → 3 `filesystem`.
  - `TestExitContract`:
    - an assertion failure → 1;
    - a name matching no test → 4;
    - an `#[ignore]` test → 4;
    - a test using a private item (E0603) → 5;
    - a missing `Cargo.lock` → 2;
    - a stale `Cargo.lock` → 2, with the lock left byte-identical afterwards;
    - an `rust-toolchain.toml` pinning `1.81` → 2;
    - an environment-controlled file with two tests → 2.
  - `TestBuildPlan` (pure):
    - linux with rustix in the lock → a separate dir plus the cfg;
    - darwin with rustix → normal;
    - linux without rustix → normal.
  - `TestRefusals`: a stripped copy of the built test binary → 2, through a seam that runs `main` with `build_test` stubbed to return that path.
  - `TestTheTwoLayersAgree`: every marker appears in exactly one of the three partition tables, with a reason.
  - `TestNothingWritten`: after a green run, `git status --porcelain` in the fixture shows only `target/`, which the fixture's `.gitignore` covers. `Cargo.lock` is unchanged.
- [ ] **Step 2: Run.** Every wrapper test fails.
- [ ] **Step 3: Implement `main` and its helpers.** The module header carries the documented command, the exit table and the residuals, as `io_guard_go.py`'s does.
- [ ] **Step 4: Green on darwin, and in `rust:1.92` and `rust:1.82`** (tar-stream).
- [ ] **Step 5: Mutations.** Each must be killed:
  - dropping `--locked` (by `TestExitContract`'s stale-lock byte-identity assertion);
  - reading exit 0 as GREEN without checking the result line (by the `#[ignore]` case);
  - reading 101 as RED before checking for a compile error (by E0603);
  - removing the handshake check in `classify` (by a hook built without its constructor).
- [ ] **Step 6: Commit.** `feat(test-safety-net): io_guard_rust.py — the Rust proof, guarded, with the Go exit table`. **Record this hash**: it is `SINCE_TSN_RUST_GUARD` in Task 9.

---

### Task 8: Wire Rust through the documents and CI

**Files:**
- `test-safety-net/SKILL.md`
- `README.md`
- `references/stacks.md`
- `references/triage.md`
- `references/parameters.md`
- `.github/workflows/skill-gates.yml`

- [ ] **Step 1: SKILL.md.**
  - Frontmatter `description`: rust is covered, and the "not for rust" clause is removed. Keep it ≤ 1024 characters; the validate gate fails otherwise, as it did once on the Go branch.
  - `compatibility`: Rust 1.82–1.98 (1.82, 1.86, 1.90, 1.94, 1.98 proven in CI).
  - Version `1.3.0`; tags gain `rust`.
  - The `SKILL_DIR` block also tests `io_guard_rust.py`.
  - Step 1 names four stacks, and step 2's `--stack` choices include `rust`.
  - Step 4 gains this block, after go's:

````markdown
   On **rust** that is `assets/io_guard_rust.py`. It builds the test with `cargo test --locked
   --no-run`, then runs the compiled binary under a preloaded hook library that intercepts libc and
   attributes each call to the function that made it:

   ```sh
   # Tier 1 candidate — the unit claims to touch nothing, so block everything.
   TEST_SAFETY_NET_TIER=1 \
     python3 "$SKILL_DIR/assets/io_guard_rust.py" \
     --test <test_file_stem> <test_name>

   # Tier 2 candidate — name EVERY controllable group this test deliberately fakes.
   TEST_SAFETY_NET_TIER=2 TEST_SAFETY_NET_ALLOW=filesystem \
     python3 "$SKILL_DIR/assets/io_guard_rust.py" \
     --test <test_file_stem> <test_name>
   ```

   **Copy the whole block.** Run it from the crate root, adding `-p <package>` for a workspace
   member. Tests go in `tests/tsn_<module_path>.rs` and reach the unit through its public path
   (`use <crate>::<path>::<item>;`). Append to that file when it exists; never overwrite it. The exit
   table is go's. Rust controls two groups, filesystem and environment, through two helpers that you
   copy into the test file verbatim:
````

   Then add the two helpers as a fenced `rust` block, verbatim:

```rust
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
```

   Follow the helpers with these rules:
  - **An environment-controlled test goes alone in `tests/tsn_<module_path>_env_<n>.rs`.** The guard refuses a file that breaks this.
  - Clock and randomness are uncontrollable on rust. `TEST_SAFETY_NET_ALLOW=clock` is refused with a note.
  - A unit the ranker tiers 3 as *not reachable* or *binary-only* is reported, never tested.
- [ ] **Step 2: `references/stacks.md`.**
  - Replace the rust row with: find units / tests go / framework / run one test.
  - Add a rust row to "Supported versions".
  - Replace "Do not improvise support for rust…" with one sentence saying every language named is registered.
  - The `note: no stack claims` example gains `rust=0`.
  - Add a **"Rust row, in detail"** section:
    - the heuristic reader and the reachability rule;
    - the controls;
    - the guard's flow, the exit table and the intercept table;
    - the build plan.
  - Add **Rust residuals**: the spec's list of eight, verbatim.
- [ ] **Step 3: `triage.md`.** After the go paragraph: "**On rust the controllable set is two**: filesystem (`tsn_control_temp_dir`) and environment (`tsn_control_set_env`). std has no clock freeze and no RNG, so clock and randomness tier 3."
- [ ] **Step 4: `parameters.md`.**
  - `--stack python|node|go|rust`.
  - `kind` `"method"` is go and rust.
  - `discovery` is always `heuristic` on rust.
- [ ] **Step 5: README.md.** Four stacks; the guard list gains `io_guard_rust.py`; the layout lists the new files.
- [ ] **Step 6: CI.** In `skill-gates.yml`:
  1. `matrix-gate`: after `setup-go`, add a pinned rust step:

```yaml
      # PINNED, like go. The rust guard is the sole runtime enforcement on the
      # rust stack, and its suites and eval check 49 SKIP where `cargo` is
      # absent. rustup ships on both runner images; nothing is downloaded but
      # the toolchain itself.
      - name: Rust 1.98
        run: rustup toolchain install 1.98 --profile minimal && rustup default 1.98
```

  2. `versions`: add `{stack: rust, version: "1.82"}`, then `1.86`, `1.90`, `1.94` and `1.98`, with:

```yaml
      - if: matrix.stack == 'rust'
        run: rustup toolchain install ${{ matrix.version }} --profile minimal && rustup default ${{ matrix.version }}
      - if: matrix.stack == 'rust'
        run: |
          python test_stack_rust.py
          python test_rust_binary.py
          python test_io_guard_rust.py
```

  3. Update the comment above `versions` to mention rust.
- [ ] **Step 7: `make gate-skill SKILL=test-safety-net`.** The documented-command test now extracts the SKILL.md block, so a mistyped document fails here.
- [ ] **Step 8: Commit.** `docs(test-safety-net): Rust is covered end to end; wire it through SKILL.md and CI`.

---

### Task 9: Eval checks 45–49 and A/B rows

**Files:** `test-safety-net/eval/run_eval.py` (after check 44, about line 1451), `scripts/ab-validate.py`.

- [ ] **Step 1: The fixture, in the eval's temp dir.**
  - `Cargo.toml` is `[package] name = "calcx"`, and a `Cargo.lock` is written by `cargo generate-lockfile` when cargo exists, or else written literally with no dependencies.
  - `src/lib.rs`: `pub mod calc; pub mod io; mod inner; pub use inner::exported;`
  - `src/calc.rs`: `pub fn pure`, `pub struct Report` with an impl `pub fn total`, and `pub(crate) fn crate_only`.
  - `src/io.rs`: `pub fn load` (`fs::read_to_string`), `pub fn home` (`env::var`), `pub fn dial` (`TcpStream::connect`), `pub fn now` (`SystemTime::now`), `pub fn raw` (`asm!("nop")`).
  - `src/inner.rs`: `pub fn exported`, `pub fn hidden`.
  - `src/main.rs`: `pub fn run`, and `fn main`.
  - NEGATIVE files: `vendor/v/src/lib.rs`, `target/debug/build/x.rs`, `examples/e.rs`, `benches/b.rs`, `build.rs` and `tests/t.rs`, each defining `pub fn leaked()`.
- [ ] **Step 2: The checks.** Each is mutation-tested; record in a comment what was broken to prove it.
  - **45** The fixture is detected `rust` with `units_discovered == 11`, and `src/calc.rs::pure` and `src/calc.rs::Report::total` are in the rows.
  - **46** `discovery == "heuristic"`, and also with `--no-precise`. Rust has one reader, and the report says so.
  - **47** NEGATIVE: no unit id starts with `vendor/`, `target/`, `examples/`, `benches/` or `tests/`, and none is `build.rs`. Also, `src/inner.rs::hidden`, `src/calc.rs::crate_only` and `src/main.rs::run` are units but in `not_netted`, never ranked for writing.
  - **48** The tiers are exactly:

| unit | tier |
|---|---|
| `pure` | 1 |
| `Report::total` | 1 |
| `exported` | 1 |
| `load` | 2 |
| `home` | 2 |
| `dial` | 3 |
| `now` | 3 |
| `crate_only` | 3 |
| `hidden` | 3 |
| `run` | 3 |
| `raw` | 4 |

   And every unit of tier 3 or above is in `not_netted`.
  - **49** NEGATIVE: the DOCUMENTED rust guard command is extracted from SKILL.md and `stacks.md` and run verbatim. It must pass a clean test (`calcx::calc::pure`) and exit 3 on `calcx::io::home`, which is blocked at tier 1 and not allowed in the tier-2 command. Every printed `io_guard_rust.py` line must parse. It is **NOT GRADED HERE** without `cargo`, like 34, 39 and 44.
- [ ] **Step 3: The A/B rows.** Add a `check_test_safety_net_rust(old, new)` with a `_RUST_PROBE` modelled on `_GO_PROBE`, returning its "cannot answer" value against a baseline without the rust stack.

| row | old → new | since |
|---|---|---|
| Rust units discovered from the eval fixture | 0 → 11 | `SINCE_TSN_RUST` (Task 2's hash) |
| Rust units tiered as check 48 requires, of 11 | 0 → 11 | `SINCE_TSN_RUST` |
| the rust guard trips a tier-1 `env::var` through its documented wrapper (skipped visibly without cargo) | 0 → 1 | `SINCE_TSN_RUST_GUARD` (Task 7's hash) |
| a stale `Cargo.lock` is left byte-identical by a proof run (the audit's A2) | 0 → 1 | `SINCE_TSN_RUST_GUARD` |
| python, node and go rank a fixed trio of repos byte-identically | held | `kind="guard"` |

- [ ] **Step 4:** Run `make gate`, then `make ab-validate`. Every new row must be IMPROVED or HELD, and none UNPROVEN.
- [ ] **Step 5: Commit.** `test(test-safety-net): eval checks 45-49 and A/B rows for the Rust stack`.

---

### Task 10: The version matrix, the two open residuals, review and the PR

- [ ] **Step 1: The five legs, locally.** Run all three rust suites in `rust:1.82`, `rust:1.86`, `rust:1.90`, `rust:1.94` and `rust:1.98`, using the tar-stream form and stating the architecture. Every failure is fixed in the tables or code, never special-cased in a test. Rust 1.86 and 1.90 have never been run before this step, so expect the seed-path and runner names to be checked here: `SEED_MARKERS`, `RUNNER_CRATES`.
- [ ] **Step 2: Residual 4, `ctor`.** In a fixture crate, a `#[ctor::ctor]` function that reads `env::var` needs a dev-dependency, so vendor `ctor` into the fixture as a path dependency to keep the run offline.
  - If the trip is attributed to the crate, residual 4 is closed: say so in `stacks.md` and the spec, and pin the result with a test.
  - If it is not, state the observed behaviour as the residual.
- [ ] **Step 3: Residual 7, the macOS floor.** Push the branch and read the CI result of one temporary `macos-latest` job on 1.82, added to the workflow in a throwaway commit and then reverted.
  - If it links and passes, add the leg permanently.
  - If it does not, state "the macOS floor is Linux-proven only" in `stacks.md`.
- [ ] **Step 3b: The audit's A1, end to end.** In a `rust:1.92` container **with network access** (this one step fetches crates):
  1. Build a fixture that depends on `tempfile`, whose unit calls `NamedTempFile::new()` and then `.persist(…)`.
  2. Run a tier-1 proof through `io_guard_rust.py`.
  3. It must print the build plan's rustix reason, and exit 3 on the persist, which is rustix's `renameat` after the rebuild against libc.
  4. Repeat with `build_plan` patched to return the normal plan. That run must exit 0: that is the gap A1 exists to close.

  Record both results in `stacks.md`'s Rust residuals and in the PR.
- [ ] **Step 4: amd64.** The five `versions` legs run on `ubuntu-latest` (amd64). Wait for them, and read every failure log. The go branch's lesson was that an arm64-only local run misses amd64 failures.
- [ ] **Step 5: Gates.** `make gate` must print `GATE_RESULT: PASS`, and `make ab-validate` must show no `WORSE`/`UNPROVEN`.
- [ ] **Step 6: Review.** Review the whole branch against this plan and the spec, using superpowers:requesting-code-review. Every finding is fixed in a numbered fix-round commit with its own A/B row.
- [ ] **Step 7: The PR.** Push `feat/test-safety-net-rust` and open the PR against `main`, using the template, including the semantic-review checklist for SKILL.md. Include a summary table of guard outcomes per platform and version.

---

## Appendix A — the Spike A2 hook, verbatim (Task 6's starting point)

This is the throwaway source that matched the C hook on macOS 1.92, macOS 1.98, Linux 1.82 (with
`-C force-unwind-tables=yes`) and Linux 1.92. It lives here because the spike's scratch directory is
not durable. Task 6 changes it as listed there; everything not listed stays as it is. In particular,
these stay: the TLS-free re-entrancy, the variadic-`open` ABI handling, the constructor sections,
and the `.symtab` reader.

```rust
// Spike A2 (throwaway): the libc interposer written in RUST, built by plain
// `rustc --crate-type cdylib -C panic=abort -O hook.rs`. No cargo, no crates.
//
// Constraints this file obeys, and why:
// - no `thread_local!`, no allocation, no std I/O, no fmt: the hook runs inside
//   libc (getenv during libSystem init, malloc paths), before or beneath any
//   runtime this image might want. Re-entrancy uses a pthread key made in a
//   constructor; readiness is a plain static atomic (no TLS).
// - C-variadic functions cannot be DEFINED on stable Rust (c_variadic is
//   unstable), only declared/called. `open` is variadic, so the replacement
//   reads `mode` where the platform ABI puts a variadic argument (see my_open).
#![allow(non_camel_case_types, clippy::missing_safety_doc)]

use core::ffi::{c_char, c_int, c_uint, c_void, CStr};
use core::sync::atomic::{AtomicBool, AtomicU8, Ordering};

type size_t = usize;
type ssize_t = isize;

#[cfg(target_os = "macos")]
type PthreadKey = usize;
#[cfg(target_os = "linux")]
type PthreadKey = c_uint;

#[repr(C)]
struct DlInfo {
    dli_fname: *const c_char,
    dli_fbase: *mut c_void,
    dli_sname: *const c_char,
    dli_saddr: *mut c_void,
}

extern "C" {
    fn write(fd: c_int, buf: *const c_void, n: size_t) -> ssize_t;
    fn _exit(code: c_int) -> !;
    fn backtrace(buf: *mut *mut c_void, size: c_int) -> c_int;
    fn dladdr(addr: *const c_void, info: *mut DlInfo) -> c_int;
    fn pthread_key_create(key: *mut PthreadKey, dtor: Option<unsafe extern "C" fn(*mut c_void)>) -> c_int;
    fn pthread_getspecific(key: PthreadKey) -> *mut c_void;
    fn pthread_setspecific(key: PthreadKey, v: *const c_void) -> c_int;
}

static READY: AtomicBool = AtomicBool::new(false);
static mut KEY: PthreadKey = 0;
static MODE: AtomicU8 = AtomicU8::new(0); // 0 unread, 1 off, 2 log, 3 guard

extern "C" fn tsn_init() {
    unsafe { pthread_key_create(&raw mut KEY, None) };
    READY.store(true, Ordering::Release);
}

#[cfg(target_os = "macos")]
#[used]
#[link_section = "__DATA,__mod_init_func"]
static INIT: extern "C" fn() = tsn_init;
#[cfg(target_os = "linux")]
#[used]
#[link_section = ".init_array"]
static INIT: extern "C" fn() = tsn_init;

fn out(b: &[u8]) {
    unsafe { write(2, b.as_ptr() as *const c_void, b.len()) };
}

fn mode() -> u8 {
    let m = MODE.load(Ordering::Relaxed);
    if m != 0 {
        return m;
    }
    let p = unsafe { real_getenv(c"TSN_MODE".as_ptr()) };
    let v = if p.is_null() {
        1
    } else {
        match unsafe { CStr::from_ptr(p) }.to_bytes() {
            b"log" => 2,
            b"guard" => 3,
            _ => 1,
        }
    };
    MODE.store(v, Ordering::Relaxed);
    v
}

// 0 transparent, 1 crate code, 2 libtest runner, 3 std seeding its HashMap.
fn classify(s: &[u8]) -> u8 {
    if s.windows(19).any(|w| w == b"hashmap_random_keys") {
        return 3;
    }
    let n = s.len();
    if n < 20 || s[n - 1] != b'E' || &s[n - 20..n - 17] != b"17h" {
        return 0;
    }
    if !s[n - 17..n - 1].iter().all(|c| c.is_ascii_hexdigit()) {
        return 0;
    }
    let mut i = 0;
    while i < n && s[i] == b'_' {
        i += 1;
    }
    if i + 1 >= n || s[i] != b'Z' || s[i + 1] != b'N' {
        return 0;
    }
    i += 2;
    let mut len = 0usize;
    while i < n && s[i].is_ascii_digit() {
        len = len * 10 + (s[i] - b'0') as usize;
        i += 1;
    }
    if len == 0 || i + len > n {
        return 0;
    }
    let krate = &s[i..i + len];
    match krate {
        b"std" | b"core" | b"alloc" | b"panic_unwind" | b"backtrace" | b"hashbrown" | b"std_detect" => 0,
        b"test" => 2,
        k if k.starts_with(b"_$LT$") || k.starts_with(b"$LT$") => 0,
        _ => 1,
    }
}

#[cfg(target_os = "linux")]
mod symtab {
    use super::*;
    extern "C" {
        fn dlsym(h: *mut c_void, s: *const c_char) -> *mut c_void;
        fn mmap(a: *mut c_void, l: size_t, p: c_int, f: c_int, fd: c_int, o: i64) -> *mut c_void;
        fn lseek(fd: c_int, o: i64, w: c_int) -> i64;
        fn close(fd: c_int) -> c_int;
        fn dl_iterate_phdr(cb: unsafe extern "C" fn(*mut c_void, size_t, *mut c_void) -> c_int, d: *mut c_void) -> c_int;
    }
    static mut BASE: u64 = 0;
    static mut MAP: *const u8 = core::ptr::null();
    static mut SYMS: (usize, usize, usize) = (0, 0, 0); // (sym off, count, str off)
    static LOADED: AtomicBool = AtomicBool::new(false);
    pub static FOUND: AtomicBool = AtomicBool::new(false);

    unsafe extern "C" fn base_cb(info: *mut c_void, _: size_t, _: *mut c_void) -> c_int {
        BASE = *(info as *const u64); // dl_phdr_info.dlpi_addr is the first field
        1
    }
    unsafe fn rd<T: Copy>(off: usize) -> T {
        core::ptr::read_unaligned(MAP.add(off) as *const T)
    }
    unsafe fn load() {
        LOADED.store(true, Ordering::Relaxed);
        dl_iterate_phdr(base_cb, core::ptr::null_mut());
        let ropen: unsafe extern "C" fn(*const c_char, c_int, ...) -> c_int =
            core::mem::transmute(dlsym(-1isize as *mut c_void, c"open".as_ptr()));
        let fd = ropen(c"/proc/self/exe".as_ptr(), 0);
        if fd < 0 {
            return;
        }
        let size = lseek(fd, 0, 2);
        let m = mmap(core::ptr::null_mut(), size as usize, 1, 2, fd, 0); // PROT_READ, MAP_PRIVATE
        close(fd);
        if m as isize == -1 {
            return;
        }
        MAP = m as *const u8;
        let shoff: u64 = rd(0x28);
        let shnum: u16 = rd(0x3C);
        for k in 0..shnum as usize {
            let sh = shoff as usize + k * 64;
            if rd::<u32>(sh + 4) == 2 {
                // SHT_SYMTAB
                let link: u32 = rd(sh + 40);
                let str_sh = shoff as usize + link as usize * 64;
                SYMS = (rd::<u64>(sh + 24) as usize, rd::<u64>(sh + 32) as usize / 24, rd::<u64>(str_sh + 24) as usize);
                FOUND.store(true, Ordering::Relaxed);
            }
        }
    }
    pub unsafe fn lookup(pc: *mut c_void) -> Option<&'static [u8]> {
        if !LOADED.load(Ordering::Relaxed) {
            load();
        }
        let (so, n, stro) = SYMS;
        let a = pc as u64;
        for j in 0..n {
            let e = so + j * 24;
            let info: u8 = rd(e + 4);
            let val: u64 = rd(e + 8);
            if info & 0xf != 2 || val == 0 {
                continue;
            }
            let lo = BASE + val;
            let size: u64 = rd(e + 16);
            if a >= lo && a < lo + size.max(1) {
                let name: u32 = rd(e);
                return Some(CStr::from_ptr(MAP.add(stro + name as usize) as *const c_char).to_bytes());
            }
        }
        None
    }
}

unsafe fn decide(group: &[u8], what: &[u8]) {
    let m = mode();
    if m == 1 {
        return;
    }
    let mut pcs = [core::ptr::null_mut::<c_void>(); 128];
    let n = backtrace(pcs.as_mut_ptr(), 128);
    let mut who: Option<&[u8]> = None;
    let mut verdict = 0u8;
    for (i, &pc) in pcs.iter().enumerate().take(n as usize).skip(2) {
        let mut info = DlInfo { dli_fname: core::ptr::null(), dli_fbase: core::ptr::null_mut(), dli_sname: core::ptr::null(), dli_saddr: core::ptr::null_mut() };
        let mut s: Option<&[u8]> = None;
        if dladdr(pc, &mut info) != 0 && !info.dli_sname.is_null() {
            s = Some(CStr::from_ptr(info.dli_sname).to_bytes());
        }
        #[cfg(target_os = "linux")]
        if s.map(|x| classify(x) == 0 && !x.ends_with(b"E")).unwrap_or(true) {
            if let Some(t) = symtab::lookup(pc) {
                s = Some(t);
            }
        }
        let c = s.map(classify).unwrap_or(0);
        if m == 2 && i < 14 {
            out(b"    [frame] ");
            out(s.unwrap_or(b"?"));
            out(b"\n");
        }
        if verdict == 0 && c != 0 {
            verdict = c;
            who = s;
        }
    }
    if m == 2 {
        out(b"  HOOK ");
        out(group);
        out(b"/");
        out(what);
        out(match verdict { 1 => b" -> CRATE ", 2 => b" -> RUNNER ", 3 => b" -> STD-SEED ", _ => b" -> NONE " });
        out(who.unwrap_or(b""));
        out(b"\n");
    }
    if m == 3 && verdict == 1 {
        out(b"IOGuardViolation: tier 1 reached ");
        out(group);
        out(b" via ");
        out(what);
        out(b" from ");
        out(who.unwrap_or(b"?"));
        out(b"\n");
        _exit(3);
    }
}

fn guard(group: &[u8], what: &[u8]) {
    if !READY.load(Ordering::Acquire) {
        return;
    }
    unsafe {
        if !pthread_getspecific(KEY).is_null() {
            return;
        }
        pthread_setspecific(KEY, 1 as *const c_void);
        decide(group, what);
        pthread_setspecific(KEY, core::ptr::null());
    }
}

// ---------------- macOS: __DATA,__interpose ----------------
#[cfg(target_os = "macos")]
mod plat {
    use super::*;
    #[repr(C)]
    pub struct Interpose {
        new: *const c_void,
        old: *const c_void,
    }
    unsafe impl Sync for Interpose {}
    #[repr(C)]
    pub struct Timespec {
        s: i64,
        ns: i64,
    }
    extern "C" {
        // Inside the interposing image these bind to the REAL functions.
        pub fn getenv(n: *const c_char) -> *mut c_char;
        fn open(p: *const c_char, f: c_int, ...) -> c_int;
        fn clock_gettime(c: c_uint, t: *mut Timespec) -> c_int;
        fn socket(a: c_int, b: c_int, c: c_int) -> c_int;
        fn read(fd: c_int, b: *mut c_void, n: size_t) -> ssize_t;
        fn stat(p: *const c_char, b: *mut c_void) -> c_int;
        fn posix_spawnp(p: *mut c_int, f: *const c_char, fa: *const c_void, at: *const c_void, av: *const *const c_char, ev: *const *const c_char) -> c_int;
    }
    unsafe extern "C" fn my_getenv(n: *const c_char) -> *mut c_char {
        guard(b"environment", b"getenv");
        getenv(n)
    }
    // Apple arm64 passes EVERY variadic argument on the stack. A non-variadic
    // function reads its 9th integer argument from the first stack slot, which
    // is exactly where the caller put `mode`. (x86_64 macOS would read rdx.)
    #[cfg(target_arch = "aarch64")]
    unsafe extern "C" fn my_open(p: *const c_char, f: c_int, _2: u64, _3: u64, _4: u64, _5: u64, _6: u64, _7: u64, mode: u64) -> c_int {
        guard(b"filesystem", b"open");
        open(p, f, mode as c_uint)
    }
    #[cfg(target_arch = "x86_64")]
    unsafe extern "C" fn my_open(p: *const c_char, f: c_int, mode: c_uint) -> c_int {
        guard(b"filesystem", b"open");
        open(p, f, mode)
    }
    unsafe extern "C" fn my_clock_gettime(c: c_uint, t: *mut Timespec) -> c_int {
        guard(b"clock", b"clock_gettime");
        clock_gettime(c, t)
    }
    unsafe extern "C" fn my_socket(a: c_int, b: c_int, c: c_int) -> c_int {
        guard(b"network", b"socket");
        socket(a, b, c)
    }
    unsafe extern "C" fn my_read(fd: c_int, b: *mut c_void, n: size_t) -> ssize_t {
        if fd == 0 {
            guard(b"stdin", b"read(0)");
        }
        read(fd, b, n)
    }
    unsafe extern "C" fn my_stat(p: *const c_char, b: *mut c_void) -> c_int {
        guard(b"filesystem", b"stat");
        stat(p, b)
    }
    unsafe extern "C" fn my_posix_spawnp(p: *mut c_int, f: *const c_char, fa: *const c_void, at: *const c_void, av: *const *const c_char, ev: *const *const c_char) -> c_int {
        guard(b"subprocess", b"posix_spawnp");
        posix_spawnp(p, f, fa, at, av, ev)
    }
    macro_rules! interpose {
        ($($id:ident: $new:ident => $old:ident),* $(,)?) => { $(
            #[used]
            #[link_section = "__DATA,__interpose"]
            static $id: Interpose = Interpose { new: $new as *const c_void, old: $old as *const c_void };
        )* };
    }
    interpose! {
        I_GETENV: my_getenv => getenv,
        I_OPEN: my_open => open,
        I_CLOCK: my_clock_gettime => clock_gettime,
        I_SOCKET: my_socket => socket,
        I_READ: my_read => read,
        I_STAT: my_stat => stat,
        I_SPAWNP: my_posix_spawnp => posix_spawnp,
    }
}
#[cfg(target_os = "macos")]
unsafe fn real_getenv(n: *const c_char) -> *mut c_char {
    plat::getenv(n)
}

// ---------------- Linux: LD_PRELOAD + dlsym(RTLD_NEXT) ----------------
#[cfg(target_os = "linux")]
mod plat {
    use super::*;
    extern "C" {
        fn dlsym(h: *mut c_void, s: *const c_char) -> *mut c_void;
    }
    const RTLD_NEXT: *mut c_void = -1isize as *mut c_void;
    macro_rules! real {
        ($name:literal, $ty:ty) => {{
            let p = dlsym(RTLD_NEXT, $name.as_ptr());
            core::mem::transmute::<*mut c_void, $ty>(p)
        }};
    }
    pub unsafe fn real_getenv(n: *const c_char) -> *mut c_char {
        real!(c"getenv", unsafe extern "C" fn(*const c_char) -> *mut c_char)(n)
    }
    #[no_mangle]
    pub unsafe extern "C" fn getenv(n: *const c_char) -> *mut c_char {
        guard(b"environment", b"getenv");
        real_getenv(n)
    }
    // SysV x86_64 and AAPCS64-on-Linux pass a variadic int in the same register
    // as a fixed third argument, so a 3-argument definition reads `mode`.
    #[no_mangle]
    pub unsafe extern "C" fn open(p: *const c_char, f: c_int, mode: c_uint) -> c_int {
        guard(b"filesystem", b"open");
        real!(c"open", unsafe extern "C" fn(*const c_char, c_int, ...) -> c_int)(p, f, mode)
    }
    #[no_mangle]
    pub unsafe extern "C" fn open64(p: *const c_char, f: c_int, mode: c_uint) -> c_int {
        guard(b"filesystem", b"open64");
        real!(c"open64", unsafe extern "C" fn(*const c_char, c_int, ...) -> c_int)(p, f, mode)
    }
    #[no_mangle]
    pub unsafe extern "C" fn statx(d: c_int, p: *const c_char, f: c_int, m: c_uint, b: *mut c_void) -> c_int {
        guard(b"filesystem", b"statx");
        real!(c"statx", unsafe extern "C" fn(c_int, *const c_char, c_int, c_uint, *mut c_void) -> c_int)(d, p, f, m, b)
    }
    #[no_mangle]
    pub unsafe extern "C" fn socket(a: c_int, b: c_int, c: c_int) -> c_int {
        guard(b"network", b"socket");
        real!(c"socket", unsafe extern "C" fn(c_int, c_int, c_int) -> c_int)(a, b, c)
    }
    #[no_mangle]
    pub unsafe extern "C" fn clock_gettime(c: c_int, t: *mut c_void) -> c_int {
        guard(b"clock", b"clock_gettime");
        real!(c"clock_gettime", unsafe extern "C" fn(c_int, *mut c_void) -> c_int)(c, t)
    }
    #[no_mangle]
    pub unsafe extern "C" fn read(fd: c_int, b: *mut c_void, n: size_t) -> ssize_t {
        if fd == 0 {
            guard(b"stdin", b"read(0)");
        }
        real!(c"read", unsafe extern "C" fn(c_int, *mut c_void, size_t) -> ssize_t)(fd, b, n)
    }
    #[no_mangle]
    pub unsafe extern "C" fn getrandom(b: *mut c_void, n: size_t, f: c_uint) -> ssize_t {
        guard(b"randomness", b"getrandom");
        real!(c"getrandom", unsafe extern "C" fn(*mut c_void, size_t, c_uint) -> ssize_t)(b, n, f)
    }
    #[no_mangle]
    pub unsafe extern "C" fn posix_spawnp(p: *mut c_int, f: *const c_char, fa: *const c_void, at: *const c_void, av: *const *const c_char, ev: *const *const c_char) -> c_int {
        guard(b"subprocess", b"posix_spawnp");
        real!(c"posix_spawnp", unsafe extern "C" fn(*mut c_int, *const c_char, *const c_void, *const c_void, *const *const c_char, *const *const c_char) -> c_int)(p, f, fa, at, av, ev)
    }
}
#[cfg(target_os = "linux")]
unsafe fn real_getenv(n: *const c_char) -> *mut c_char {
    plat::real_getenv(n)
}
```

A note for Task 6: `&raw mut KEY` and C-string literals (`c"…"`) became stable in 1.82 and 1.77 respectively, so this source compiles at the floor. If a leg below 1.82 is ever added, replace them with `core::ptr::addr_of_mut!(KEY)` and byte strings.
