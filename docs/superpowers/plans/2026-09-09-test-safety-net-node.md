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

### Task 2: Node heuristic discovery

**Files:**
- Create: `test-safety-net/assets/stack_node.py`
- Test: `test-safety-net/assets/test_stack_node.py`

**Interfaces:**
- Produces: `STACK_NAME = "node"`, `matches`, `discover_units`, and `_units_heuristic(root)` for Task 3 to fall back to.
- Consumes: `rank_risk.read_text`, `rank_risk.SKIP_DIRS`.

`matches(root)`: True when a `package.json` exists at root, or any non-test `.js`/`.mjs`/`.cjs`/`.ts`/`.tsx` file exists outside `node_modules`.

Discovery finds **exported top-level** functions and classes only — the same rule as Python's "module-level, non-underscore": a symbol a test can import and call by name. Recognise these forms:

```js
export function name(...)        export default function name(...)
export const name = (...) => ...  export const name = function (...)
export class Name { ... }
module.exports = { name, ... }    module.exports.name = ...
exports.name = ...
```

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

One test per form above. Negatives that must find **nothing**: a non-exported `function helper()`, a name inside a comment, a name inside a string, a `.test.js`/`.spec.ts` file, anything under `node_modules/`, and a nested function inside an exported one (not independently addressable, exactly as Python excludes nested defs).

- [ ] **Step 5: Run the suite**

Expected: all green.

- [ ] **Step 6: Commit**

---

### Task 3: Node precise discovery via the repo's own toolchain

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

### Task 4: Node I/O markers and triage

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
