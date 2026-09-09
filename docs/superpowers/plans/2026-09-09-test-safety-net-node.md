# test-safety-net: node/TypeScript stack — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add node/TypeScript as the second supported stack, behind a stack interface that Go and Rust can then fill without touching shared code.

**Architecture:** Extract the stack-agnostic core of `rank_risk.py` (churn, reference counting, scoring, ranking, CLI, JSON contract) and put per-stack discovery/triage behind one interface. Python moves into `stack_python.py` with **zero behaviour change**. Node arrives as `stack_node.py` with hybrid discovery, plus `io_guard.js` as its runtime guard.

**Tech Stack:** Python 3 stdlib only for the ranker; plain CommonJS (no build, no npm install) for the guard. Node 18+ for the guard and node's own test runner.

**Spec:** `docs/superpowers/specs/2026-09-09-test-safety-net-multistack-design.md`, which amends `docs/superpowers/specs/2026-09-09-test-safety-net-design.md`.

## Global Constraints

- **Stdlib-only Python, no pip, no network** for anything the ranker needs to run.
- **The guard ships as plain CommonJS** — no transpile step, no dependencies. It must work under `node --require` with node's own test runner, and be loadable as a vitest/jest `setupFile`.
- **Zero behaviour change for the Python stack.** The existing 158 unit tests and 34 eval checks must pass **unmodified** after the extraction. If a test needs editing to pass, the extraction changed behaviour and is wrong.
- **The filter may under-credit, never over-credit** — reach, coverage, or safety. A unit whose testability is uncertain is tiered *higher* (less safe), never lower.
- **The report names which discovery path ran** (`precise` or `heuristic`) per run, in the JSON and in human output. A silently degraded run is not comparable to a precise one.
- **Every `references/`/`assets/` path named in SKILL.md must exist**, and helpers are invoked as `"$SKILL_DIR/assets/..."` — `scripts/gates/asset-paths.sh` fails the gate otherwise.
- Every new guardrail gets an `ab-validate` row with a new `SINCE_*` constant.
- **No external MCP server may be a dependency** of this skill or its implementation.

## Interfaces

The stack interface. Each stack module exposes exactly these four names; the core imports nothing else from it:

```python
STACK_NAME: str                      # "python" | "node"
def matches(root: str) -> bool: ...  # is this repo this stack?
def discover_units(root: str) -> tuple: ...   # (units, mode) where mode in {"precise","heuristic"}
def triage(root: str, unit: dict) -> tuple: ...  # (tier:int, reason:str) — unchanged contract
```

`discover_units` returns unit dicts with exactly the existing keys: `id` (`"<rel>::<name>"`), `path`, `name`, `lineno`, `kind` (`"function"`|`"class"`).

The JSON contract is unchanged except that `stack` now reports the detected stack and a new top-level `discovery` field carries `"precise"` or `"heuristic"`.

**This ninth key is an intended behaviour change and lands in Task 3.** `TestMain`'s "eight expected keys" assertion is the one existing test that legitimately changes, and only then. Tasks 1 and 1b must still leave the suite untouched; a reviewer seeing that assertion edited in Task 1 or 1b should reject it.

---

### Task 1: Extract the stack interface; move Python behind it, unchanged

**Files:**
- Create: `test-safety-net/assets/stack_python.py`
- Modify: `test-safety-net/assets/rank_risk.py`
- Test: `test-safety-net/assets/test_rank_risk.py` (imports only; no assertions change)

**Interfaces:**
- Produces: `STACK_NAME`, `matches`, `discover_units`, `triage` as above; `rank_risk.STACKS` registry.
- Consumes: nothing new.

- [ ] **Step 1: Run the suite and record the baseline**

Run: `python3 -m unittest discover -s test-safety-net/assets -p 'test_*.py'`
Expected: `Ran 158 tests ... OK`. Write the number down; it must not change.

- [ ] **Step 2: Move the Python-specific machinery into `stack_python.py`**

Move, without editing their bodies: `iter_py_files`, `discover_units`, the `CONTROLLABLE`/`UNCONTROLLABLE`/`IMPORT_TIME_INERT` tables, `_drop_inert`, every `_`-prefixed `ast` helper (`_import_alias_map` through `_unambiguous_methods`), `_hits_from`, `_analyze_file`, `_markers`, `triage`, `_tier_from_hits`.

Keep in `rank_risk.py`: `SKIP_DIRS`, `_is_test_path`, `read_text`, `_git_prefix`, `churn`, the identifier/reference machinery (`_IDENTIFIER_RE` through `_reached_through_module`), `inbound_refs`, `already_covered`, `_normalise`, `rank`, `main`.

Wrap `stack_python.discover_units` to return `(units, "precise")`.

- [ ] **Step 3: Add the registry and detection**

```python
STACKS = [stack_node, stack_python]      # first match wins; python last as the fallback
def detect_stack(root):
    for s in STACKS:
        if s.matches(root):
            return s
    return stack_python
```

For Task 1, register only `stack_python` and have `matches` return True when any non-test `.py` file exists.

- [ ] **Step 4: Re-run the suite unmodified**

Run: `python3 -m unittest discover -s test-safety-net/assets -p 'test_*.py'`
Expected: the same count, OK. **If any test needs editing, revert and redo the move — behaviour changed.**

- [ ] **Step 5: Run the gate and the eval**

Run: `make gate-skill SKILL=test-safety-net` and `python3 test-safety-net/eval/run_eval.py`
Expected: `GATE_RESULT: PASS`, `EVAL_RESULT: PASS (34/34 checks)`.

- [ ] **Step 6: Commit**

```bash
git add test-safety-net/assets/
git commit -m "refactor(test-safety-net): put Python discovery and triage behind a stack interface"
```

---

### Task 1b: Extend the stack interface to everything that reads a language

**Added after Task 1's report.** Task 1 established the seam and proved zero behaviour
change (byte-identical JSON over 326 units). Its report then showed the six-name
interface is still too small in four ways, each a place where the *core* keeps a Python
assumption. Fixing them in node's discovery task would tangle a behaviour change with a
pure extraction and lose the byte-identical proof, so they land here first — still with
**zero behaviour change**.

**Files:**
- Modify: `test-safety-net/assets/rank_risk.py`, `test-safety-net/assets/stack_python.py`
- Test: `test-safety-net/assets/test_rank_risk.py` — again, must pass **unmodified**

**The interface after this task.** Grouped by what they do; the core orchestrates and
scores, the stack reads the language:

```
identity   STACK_NAME, matches(root)
files      iter_source_files(root, include_tests=False), is_test_path(rel),
           is_test_for(test_rel, src_rel)
naming     module_of(rel), path_pattern(rel)
lexing     IDENTIFIER_RE, preceding_qualifier(text, start)
grammar    module_bindings(module, text), reached_through_module(module, name, text)
analysis   discover_units(root) -> (units, mode), triage(root, unit) -> (tier, reason)
```

- [ ] **Step 1: Record the baseline** — 158 tests, and capture `rank_risk.py . > /tmp/base.json` for the byte-identical check.

- [ ] **Step 2: Move module identity** — `module_of(rel)` replaces the core's
  `os.path.splitext(os.path.basename(rel))[0]`; `path_pattern(rel)` replaces
  `_path_pattern`'s literal `".py"` strip. Python's implementations move verbatim.

- [ ] **Step 3: Move the binding grammar** — `module_bindings` and
  `reached_through_module` move to `stack_python` behind the interface names. These are
  `already_covered`'s strongest evidence; the core must call through, never inline
  Python's `import X as Y` / `from X import Y` forms.

- [ ] **Step 4: Move lexing** — `IDENTIFIER_RE` and `preceding_qualifier` become stack
  attributes. Keep the Python regex byte-identical, including the
  `(?<![A-Za-z0-9_])` lookbehind that a prior round proved necessary (`2x` must not
  yield a reference to `x`).

- [ ] **Step 5: Add `is_test_for(test_rel, src_rel)`** — extract `already_covered`'s
  same-directory rule into it. Python's implementation is exactly today's behaviour:
  same directory. Node will later add `__tests__/` siblings and `src/`↔`test/` mirroring.

- [ ] **Step 6: Prove nothing changed**

Run the suite unmodified (158, OK), the eval (34/34), `make gate`, and:

**The bar, stated correctly.** Do NOT capture a baseline before your work and compare
after: the ranker's corpus is the repo it lives in, so adding public names adds units by
construction (Task 1: 321→326, Task 1b: 326→332). That is not a behaviour change and the
naive `cmp` cannot pass.

Compare the OLD ranker against the NEW ranker over the SAME tree:

```bash
mkdir -p /tmp/oldrank && for f in rank_risk stack_python stack_common; do
  git show <base>:test-safety-net/assets/$f.py > /tmp/oldrank/$f.py; done
for tree in . test-safety-net scripts agent-ready-rails; do
  python3 /tmp/oldrank/rank_risk.py "$tree" > /tmp/o.json
  python3 test-safety-net/assets/rank_risk.py "$tree" > /tmp/n.json
  cmp /tmp/o.json /tmp/n.json && echo "$tree IDENTICAL"
done
```

Every tree `IDENTICAL` is the bar.

- [ ] **Step 7: Commit**

---

### Task 2: Node heuristic discovery

**Files:**
- Create: `test-safety-net/assets/stack_node.py`
- Test: `test-safety-net/assets/test_stack_node.py`

**Interfaces:**
- Produces: `STACK_NAME = "node"`, `matches`, `discover_units`, and `_units_heuristic(root)` for Task 3 to fall back to.
- Consumes: `stack_common.read_text`, `stack_common.SKIP_DIRS`. **Never import `rank_risk` from a stack module** — it is loaded by path and never lands in `sys.modules`, so importing it back executes a second copy of the ranker mid-import and dies on a half-initialised `stack_python`.

`matches(root)`: True when a `package.json` exists at root, or any non-test `.js`/`.mjs`/`.cjs`/`.ts`/`.tsx` file exists outside `node_modules`.

Discovery finds **exported top-level** functions and classes only — the same rule as Python's "module-level, non-underscore": a symbol a test can import and call by name. Recognise these forms:

```js
export function name(...)        export default function name(...)
export const name = (...) => ...  export const name = function (...)
export class Name { ... }
module.exports = { name, ... }    module.exports.name = ...
exports.name = ...
```

**Phase 0 — three interface corrections first, each zero behaviour change for Python,
each committed separately before any node code.** Task 1b's report found these; they are
silent-zero bugs for node exactly like the grammar gap Task 1b closed.

- [ ] **Step 0a: `name_pattern(name)` joins the interface (fourteenth name)**

`already_covered` and `_references_module` still build `re.compile(r"\b%s\b" % ...)` in
the core. `\b` does not treat `$` as an identifier character — verified:
`re.search(r"\b\$fetch\b", "$fetch(1)")` is `False`. So every `$`-named JS export can
never be matched in a test file and reads as an uncovered gap forever. Move pattern
construction to the stack. Python's stays `\b%s\b` byte-for-byte; node will use
`(?<![A-Za-z0-9_$])name(?![A-Za-z0-9_$])`.

- [ ] **Step 0b: give the grammar names their file context**

`module_bindings(module, text)` and `reached_through_module(module, name, text)` receive a
bare token and never learn which file `text` came from or which file defines the unit, so
node cannot resolve `"../utils"` and is reduced to matching a specifier's last segment.
Extend both to take the defining and referencing paths. Python ignores the new arguments,
so its behaviour is unchanged — prove that with the identical-output check.

- [ ] **Step 0c: prove Phase 0 changed nothing, then commit**

Old-vs-new ranker over the same four trees, all `IDENTICAL`; suite unmodified at 158.

---

- [ ] **Step 1: Write the failing test**

```python
def test_finds_exported_function_declaration(self):
    write(self.root, "src/util.js", "export function parse(s) {\n  return JSON.parse(s);\n}\n")
    units, mode = stack_node.discover_units(self.root)
    self.assertEqual([u["id"] for u in units], ["src/util.js::parse"])
    self.assertEqual(mode, "heuristic")
    self.assertEqual(units[0]["kind"], "function")
    self.assertEqual(units[0]["lineno"], 1)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m unittest test_stack_node -v` from `test-safety-net/assets`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the recogniser**

Strip comments and string literals **before** matching, or a `//` in a URL and an exported name inside a template literal both produce phantom units. A single pass that tracks whether it is inside `'`, `"`, `` ` ``, `//` or `/* */` is enough; do not attempt full tokenisation.

- [ ] **Step 4: Cover the rest of the export forms, plus negatives**

**Required negative, from Task 1b's concern 4.** The Python branch's fix round 6 ruled
that "the colliding sibling is by definition in a different directory" — true only while
`is_test_for` means same-directory. Node's `__tests__/` siblings and `src/`↔`test/`
mirroring break that assumption, and the guard against it (`rivals`, via `path_pattern`)
is node's weakest predicate. Write a fixture with the same basename in two packages, a
`__tests__/` test for one of them, and assert the OTHER is reported uncovered. If it
cross-credits, `is_test_for` is too wide — narrow it; never widen `rivals` to compensate.

One test per form above. Negatives that must find **nothing**: a non-exported `function helper()`, a name inside a comment, a name inside a string, a `.test.js`/`.spec.ts` file, anything under `node_modules/`, and a nested function inside an exported one (not independently addressable, exactly as Python excludes nested defs).

- [ ] **Step 5: Run the suite**

Expected: all green.

- [ ] **Step 6: Commit**

---

### Task 4: Node precise discovery via the repo's own toolchain

**Phase 0 — the manifest bonus is additive and swamps file counts.** Task 3 shipped
`MANIFEST_BONUS = 100`, added to the file count. Controller-reproduced on the shape that
matters most, because it is the common one:

    40 .py + 5 .js + a root package.json (prettier/husky/eslint)
    -> stack: node (node=105, python=40), units: 5

That is a Python repo with JS tooling, and the skill would rank five files while ignoring
forty. A root `package.json` is extremely common in Python repos for formatting and git
hooks, so this is not a corner case.

- [ ] **Step 0a: make the manifest multiply, not add**

A manifest is evidence of *intent* and should scale a stack's claim rather than swamp it.
Something of the shape `(files_claimed + floor) * multiplier` — but the exact constants
are yours to choose and, more importantly, to **justify against cases**.

- [ ] **Step 0b: write the case table as tests, before choosing the constants**

At minimum these eight, each a real repo shape, with the expected winner:

| repo shape | expect |
|---|---|
| 40 `.py`, 5 `.js`, root `package.json` (tooling) | python |
| 75 `.py`, 17 `.mjs`/`.ts`, manifest only inside a template dir | python |
| 200 `.js`, root `package.json`, 3 `.py` scripts | node |
| 2 `.js`, root `package.json`, 3 `.py` (fresh node project) | node |
| 300 `.py`, `pyproject.toml`, 20 `.js` frontend, root `package.json` | python |
| 150 `.ts`, root `package.json`, 4 `.py` tooling scripts | node |
| 10 `.py`, 10 `.js`, no manifest at all | ambiguous — reported, not guessed |
| 0 `.py`, 0 `.js`, empty repo | neither; say so and exit cleanly |

Pick constants that satisfy every row, then state in your report which row was tightest —
that row is where the next person's change will break it.

- [ ] **Step 0c: `AMBIGUITY_MARGIN` gets at least one test each way** — a pair inside the
margin must raise, a pair outside must not. Task 3 shipped it untested.

- [ ] **Step 0d: whole gate and eval green; this repo still detects python.**



**Files:**
- Modify: `test-safety-net/assets/stack_node.py`
- Test: `test-safety-net/assets/test_stack_node.py`

**Interfaces:**
- Produces: `discover_units` now returning `(units, "precise")` when the toolchain path succeeds.
- Consumes: `_units_heuristic` from Task 2.

Precise path: if the repo has TypeScript available (`node_modules/typescript` present), drive it through a small inline script run with `node -e`, using `ts.createSourceFile` and walking top-level statements for exported declarations. Invoke as `npx --no-install tsc`-style resolution — **never** a bare `npx tsc`, which would download on a miss.

- [ ] **Step 1: Write the failing test**

Test that when `node_modules/typescript` is absent, `discover_units` returns mode `"heuristic"`; and that when the precise path raises for any reason it **falls back** rather than propagating.

```python
def test_falls_back_to_heuristic_when_toolchain_missing(self):
    write(self.root, "src/util.ts", "export function parse(s: string) { return 1; }\n")
    units, mode = stack_node.discover_units(self.root)
    self.assertEqual(mode, "heuristic")
    self.assertEqual([u["id"] for u in units], ["src/util.ts::parse"])
```

- [ ] **Step 2: Run it, watch it fail**

- [ ] **Step 3: Implement the precise path with a hard timeout**

Subprocess with `timeout=20`; on `TimeoutExpired`, `FileNotFoundError`, non-zero exit, or unparseable stdout, fall back to heuristic. Never let the precise path fail the run.

- [ ] **Step 4: Test both paths agree on a fixture both can read**

Where `node_modules/typescript` exists, both paths must return the same unit ids for the same simple fixture. Skip cleanly (`unittest.SkipTest`) when it does not — and make the skip **visible**, never silent.

- [ ] **Step 5: Run the suite; commit**

---

### Task 3: Node I/O markers, triage, and stack detection

**Phase 0 — replace first-match-wins stack detection.** Task 2 declined to register
`stack_node` and was right to. This repo holds 75 `.py` files, 17 `.mjs`/`.ts`, and a
`package.json` — the last one inside `starlight-handbook-kit/assets/templates/scaffold/`,
a *template*, not this repo's own manifest. Under first-match-wins with a generous node
`matches()`, the ranker's own corpus reclassifies as node and the eval crashes. Polyglot
repos are the common case, not the exception, so the detector has to weigh evidence
rather than take the first vote.

- [ ] **Step 0a: `matches(root) -> bool` becomes `evidence(root) -> int`**

Each stack scores itself: the count of non-test source files it claims, plus a decisive
bonus for a manifest **at or near the analysed root** (`package.json`, `go.mod`,
`Cargo.toml`, `pyproject.toml`). A manifest nested under `assets/`, `templates/`,
`fixtures/`, `examples/` or `testdata/` is evidence about a *template*, not about this
repo, and scores nothing.

- [ ] **Step 0b: highest score wins; a tie is reported, not guessed**

On a tie, or when the top two are within a hair of each other, say so and require an
explicit choice rather than picking. Add a `--stack` CLI flag that overrides detection
outright.

- [ ] **Step 0c: the test that would have caught this**

```python
def test_this_repo_classifies_as_python_not_node(self):
    # 75 .py against 17 .mjs/.ts, and the only package.json is inside a
    # scaffold TEMPLATE. First-match-wins got this wrong and crashed the eval.
    self.assertEqual(rank_risk.detect_stack(REPO_ROOT).STACK_NAME, "python")

def test_a_manifest_inside_a_template_dir_is_not_evidence(self):
    write(self.root, "assets/templates/scaffold/package.json", "{}")
    write(self.root, "src/thing.py", "def go():\n    return 1\n")
    self.assertEqual(rank_risk.detect_stack(self.root).STACK_NAME, "python")
```

- [ ] **Step 0d: register `stack_node`** with its `triage`, and re-run the whole gate and
eval — the crash Task 2 avoided must not reappear.

**Files:**
- Modify: `test-safety-net/assets/stack_node.py`
- Test: `test-safety-net/assets/test_stack_node.py`

**Interfaces:**
- Produces: `triage(root, unit) -> (tier, reason)`, `CONTROLLABLE`, `UNCONTROLLABLE`.

Marker groups, mirroring the Python tables' semantics:

```python
CONTROLLABLE = {
    "filesystem":  ("fs", "node:fs", "fs/promises", "fsPromises"),
    "clock":       ("Date.now", "new Date", "setTimeout", "setInterval", "performance.now"),
    "randomness":  ("Math.random", "crypto.randomUUID", "crypto.randomBytes"),
    "environment": ("process.env", "process.argv", "process.cwd"),
}
UNCONTROLLABLE = {
    "network":     ("http", "https", "node:http", "node:https", "net", "node:net",
                    "dgram", "fetch", "XMLHttpRequest", "WebSocket", "axios", "got"),
    "subprocess":  ("child_process", "node:child_process", "spawn", "exec", "execSync",
                    "spawnSync", "fork", "execFile", "execFileSync"),
    "database":    ("pg", "mysql", "mysql2", "mongodb", "MongoClient", "redis",
                    "sqlite3", "better-sqlite3", "knex", "prisma"),
}
```

Tier rules are the Python ones verbatim: 1 = no markers, directly callable; 2 = only controllable groups, pin at a wider boundary with those faked; 3 = uncontrollable I/O inside the unit, needs a seam; 4 = subprocess/network at the boundary, hand off.

- [ ] **Step 1: Write the failing tests** — one per tier, plus: a marker inside a comment or string must **not** count (reuse Task 2's stripper), and an import-time marker at module top level floors every unit in the file, mirroring the Python stack's import-time rule.

- [ ] **Step 2: Run, watch fail**

- [ ] **Step 3: Implement**

- [ ] **Step 4: Add the derived-completeness test**

Mirror the Python suite's `dir(os)`-derived test in spirit: assert every name in node's own `module.builtinModules` that performs I/O is either in a marker group or in an allow-list carrying a reason. Its failure on a node upgrade is the mechanism — record that in a comment so nobody widens it away.

- [ ] **Step 5: Run the suite; commit**

---

### Task 5: `io_guard.js`

**Files:**
- Create: `test-safety-net/assets/io_guard.js`
- Test: `test-safety-net/assets/test_io_guard_node.sh` (marked `# gate: offline`)

**Interfaces:**
- Consumes: `TEST_SAFETY_NET_TIER`, `TEST_SAFETY_NET_ALLOW` — the same env contract as `io_guard.py`.
- Produces: `IOGuardViolation`, thrown from patched entry points.

**This mechanism is already proven; do not redesign it.** The spike that validated it:

```js
function initiatedByCodeUnderTest() {
  const stack = new Error().stack.split("\n").slice(1);
  for (const line of stack) {
    if (line.includes("io_guard.js")) continue;
    if (line.includes("node:internal/") || line.includes("node:diagnostics")) return false;
    return true;
  }
  return false;
}
```

Without that provenance check the guard breaks node's **module loader**, which reads `.js` through `fs.readFileSync` — so a test that touches no filesystem still dies at `defaultLoadImpl (node:internal/modules/cjs/loader)`. Write the test that pins this **first**.

Carry across the Python guard's hard-won rules, each of which cost a review round:
1. `IOGuardViolation` must **not** extend the runner's assertion error — a guard trip means the *classification* is wrong; an assertion failure means the *captured value* is wrong, and they demand opposite responses.
2. The guard installs **before** the module under test loads (`--require`, or a `setupFile` that runs first).
3. A violation raised asynchronously (a callback, a promise, a worker) must still reach the test result rather than being swallowed.
4. Patch the **lowest** reachable layer: `node:fs` (both sync and promise faces), `node:net`, `node:http`/`https`, `node:child_process`, `node:dns`, and global `fetch` — not a convenience wrapper.
5. Restore everything on teardown.

- [ ] **Step 1: Write the failing shell test** — a clean test passes under the guard; a test reading `/etc/hosts` fails with `IOGuardViolation`; both under `node --test --test-name-pattern`.

- [ ] **Step 2: Run it, watch both halves fail**

- [ ] **Step 3: Implement, tier-aware** — Tier 1 blocks every group; Tier 2 permits the groups named in `TEST_SAFETY_NET_ALLOW`; network and subprocess are never permitted.

- [ ] **Step 4: Negative fixtures** — the guard must not fire on the runner's own I/O; must fire on the unit's; must fire for the async shapes in rule 3.

- [ ] **Step 5: Run; commit**

---

### Task 6: Wire node through SKILL.md, stacks.md and README

**Files:**
- Modify: `test-safety-net/SKILL.md`, `test-safety-net/references/stacks.md`, `test-safety-net/README.md`

- [ ] **Step 1:** Fill the node row in `stacks.md` with the four facts, and replace the three "see the follow-up plan" promises with what is now true. State the hybrid-discovery limit and which path a reader should expect.
- [ ] **Step 2:** In SKILL.md, add the node guard invocation beside the Python one, **as a complete copyable block** including whatever preload path is needed, referencing `"$SKILL_DIR/assets/io_guard.js"`. Update `compatibility` to name python and node.
- [ ] **Step 3:** Update the frontmatter `description` so it fires for node repos.
- [ ] **Step 4:** Run `make gate-skill SKILL=test-safety-net`; `asset-paths.sh` will fail a skill-relative path.
- [ ] **Step 5:** Commit.

---

### Task 7: Eval checks and A/B rows

**Files:**
- Modify: `test-safety-net/eval/run_eval.py`, `scripts/ab-validate.py`

- [ ] **Step 1:** Add eval checks: node discovery finds a unit in a fixture repo; the mode field is reported; a `node_modules` file is excluded; each tier is assigned correctly; and — with a **negative arm** — the guard fails a violating test and passes a clean one under the documented command. A check that passes on a guard that never arms is a defect; mutation-test each new check by breaking the thing it claims to prove.
- [ ] **Step 2:** Add A/B rows with a new `SINCE_*` constant pinned to Task 1's commit: units discovered in a node fixture (0 → n), tier assigned for a node unit, guard blocks node filesystem I/O, discovery mode reported.
- [ ] **Step 3:** `make gate` and `make ab-validate` both green; every new row IMPROVED against the pre-branch tip.
- [ ] **Step 4:** Commit.
