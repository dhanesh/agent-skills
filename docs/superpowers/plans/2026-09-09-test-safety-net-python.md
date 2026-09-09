# test-safety-net (Python v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a gate-passing `test-safety-net` skill that authors proved-able-to-fail characterization tests into an untested **Python** repo, and honestly reports what it could not net.

**Architecture:** One stdlib+git ranker (`assets/rank_risk.py`) discovers units, scores them by churn × inbound references, and triages each into one of four testability tiers. The SKILL.md drives a five-step workflow (detect → rank → confirm → write-and-prove → report) in which every generated test is proved able to fail via a red→green cycle on the *assertion*, never by touching source. Tier 3/4 units are reported as a ranked seam list for `clean-code`, never refactored.

**Tech Stack:** Python 3 stdlib only (`ast`, `subprocess`, `json`, `re`, `pathlib`, `argparse`, `unittest`). Git CLI for churn. No pip, no network, no MCP.

**Spec:** `docs/superpowers/specs/2026-09-09-test-safety-net-design.md`

## Global Constraints

Copied verbatim from the spec and `CLAUDE.md`. Every task's requirements implicitly include these.

- **Stdlib-only Python.** No pip, no network, no MCP, no third-party packages — in the skill, its assets, or its eval.
- **The skill never modifies source.** It only creates test files, and **appends** to an existing test file, never overwrites.
- **Never writes a test that performs real I/O.** If the boundary cannot be controlled, the unit drops to Tier 3 and no test is written.
- **Never ships an unproven test.** A test that did not go RED is discarded and listed under *could not prove*.
- **No coverage percentage** as a target or a reported success metric, anywhere.
- **Determinism.** `rank_risk.py` must emit byte-identical JSON across repeated runs on the same input.
- Every `references/`, `assets/`, `scripts/` path mentioned in `SKILL.md` **must exist** — the validate gate fails on a dangling path.
- **No skill-relative helper invocation in `SKILL.md`.** Resolve `SKILL_DIR` once and invoke as `python3 "$SKILL_DIR/assets/rank_risk.py"`. Enforced by `scripts/gates/asset-paths.sh`.
- **`PARAMETERS.md` is reserved** for template↔placeholder bijection. Flags go in `references/parameters.md`.
- **`description` ≤ 1024 characters**, measured including folded block scalars.
- Frontmatter must carry `license`, `compatibility`, and `metadata` (`author`/`version`/`tags`).
- **Eval required:** `eval/run_eval.py`, stdlib-only, offline, deterministic, model-free grader, **negative fixtures mandatory**, no repo writes (use `tempfile.mkdtemp()`), completes under `EVAL_TIMEOUT` (120s). Final line exactly `EVAL_RESULT: PASS (n/n checks)` or `EVAL_RESULT: FAIL (k/n checks)`, and exit 0 **iff** every check passed.
- **A/B row required** — a new guardrail with no `ab-validate` row is a claim nobody measured. Rows declare `since=`.

## Shared data shapes

Defined once here; every task below uses these exact key names.

```python
# A discovered unit.
Unit = {
    "id": "billing/refund.py::apply",   # f"{path}::{name}" — stable, sortable
    "path": "billing/refund.py",
    "name": "apply",
    "lineno": 14,
    "kind": "function",                  # "function" | "class"
}

# A ranked row = Unit plus:
#   "churn": int              commits touching this file in the window
#   "inbound_refs": int       approximate reference count
#   "inbound_approx": True    always True; the count is a static approximation
#   "tier": int               1 | 2 | 3 | 4
#   "tier_reason": str        human-readable, names the marker found
#   "score": float            0.0 when tier >= 3
#   "covered_by": str | None  test file that already exercises it
```

## File structure

```
test-safety-net/
  SKILL.md                     the workflow, disciplines, invariants, report template
  README.md                    human-facing overview
  references/
    triage.md                  the four tiers, boundary controls, seam vocabulary
    stacks.md                  per-stack table; python filled, others "not yet supported"
    parameters.md              flags for rank_risk.py
  assets/
    rank_risk.py               discover · churn · refs · triage · cover · rank · CLI
    test_rank_risk.py          stdlib unit suite (runs in `make gate`)
  eval/
    run_eval.py                outcome eval with the mandatory negative fixtures
```

`rank_risk.py` stays a single file to match `verifier-installer/assets/detect_stack.py`, the sibling it mirrors. Internally it is a set of pure functions, each independently testable — that is what makes the tasks below separable.

---

### Task 1: Scaffold a gate-passing skeleton

**Files:**
- Create: `test-safety-net/` (via the scaffolder)
- Modify: `test-safety-net/SKILL.md` (frontmatter only)

**Interfaces:**
- Consumes: nothing
- Produces: a `test-safety-net/` directory that passes `make gate-skill SKILL=test-safety-net`

- [ ] **Step 1: Generate the skeleton**

```bash
python3 repo2skill/assets/scaffold_skill.py test-safety-net \
  --tags "testing,characterization,legacy-code,agent-safety,pytest"
```

Creates `SKILL.md`, `README.md`, `references/overview.md`, `assets/test_test_safety_net_smoke.py`, `eval/run_eval.py`.

- [ ] **Step 2: Verify the skeleton passes the gate**

Run: `make gate-skill SKILL=test-safety-net`
Expected: PASS on every check.

- [ ] **Step 3: Replace the frontmatter description**

Edit `test-safety-net/SKILL.md`, replacing the generated `description:` with:

```yaml
description: >-
  Author unit tests into a codebase that has none, so an agent can change it safely. Use when a
  repo has no meaningful tests, when someone says "I don't trust an agent in this codebase", "add
  tests before we refactor", or "we need a safety net before this migration". Ranks units by blast
  radius and churn, then writes characterization tests that pin current behaviour — each one proved
  able to FAIL before it is kept, so the suite is a real change-detector and not green noise. Every
  test declares whether it pins behaviour or asserts a spec; suspected bugs are pinned AND reported,
  never silently blessed. Code that is not testable is triaged, not forced: it becomes a ranked seam
  list for clean-code. Never modifies your source and never writes a test that performs real I/O.
  Not a correctness audit and not a coverage-percentage chaser. Fills the loop verifier-installer
  installs; clean-code judges what comes out.
```

- [ ] **Step 4: Verify the description is measured and within budget**

Run: `sh scripts/gates/validate-skill.sh test-safety-net | grep description`
Expected: `PASS: description: present and valid (length=NNN)` with `NNN` ≤ 1024 and clearly not 2 (2 would mean the folded scalar was not read).

- [ ] **Step 5: Commit**

```bash
git add test-safety-net
git commit -m "feat(test-safety-net): scaffold a gate-passing skeleton"
```

---

### Task 2: Discover Python units

**Files:**
- Create: `test-safety-net/assets/rank_risk.py`
- Create: `test-safety-net/assets/test_rank_risk.py`
- Delete: `test-safety-net/assets/test_test_safety_net_smoke.py` (replaced by the real suite)

**Interfaces:**
- Consumes: nothing
- Produces: `discover_units(root: str) -> list[Unit]` — module-level `def` and `class` in non-test `.py` files, sorted by `id`. Skips `_`-prefixed names, test files, and vendor directories.

- [ ] **Step 1: Write the failing test**

Create `test-safety-net/assets/test_rank_risk.py`:

```python
"""Unit suite for rank_risk.py. Stdlib only, offline, deterministic."""
import importlib.util
import os
import pathlib
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("rank_risk", os.path.join(HERE, "rank_risk.py"))
rank_risk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rank_risk)


def write(root, rel, text):
    p = pathlib.Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return str(p)


class TempRepo(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tsn-")


class TestDiscoverUnits(TempRepo):
    def test_finds_module_level_functions_and_classes(self):
        write(self.root, "billing/refund.py",
              "def apply(amount):\n    return amount\n\n\nclass Ledger:\n    pass\n")
        units = rank_risk.discover_units(self.root)
        self.assertEqual([u["id"] for u in units],
                         ["billing/refund.py::Ledger", "billing/refund.py::apply"])
        self.assertEqual(units[1]["kind"], "function")
        self.assertEqual(units[0]["kind"], "class")
        self.assertEqual(units[1]["lineno"], 1)

    def test_skips_private_names_nested_defs_test_files_and_vendor(self):
        write(self.root, "app.py",
              "def _helper():\n    pass\n\n\ndef outer():\n    def inner():\n        pass\n    return inner\n")
        write(self.root, "tests/test_app.py", "def test_outer():\n    pass\n")
        write(self.root, "node_modules/pkg/mod.py", "def vendored():\n    pass\n")
        write(self.root, ".venv/lib/mod.py", "def alsovendored():\n    pass\n")
        ids = [u["id"] for u in rank_risk.discover_units(self.root)]
        self.assertEqual(ids, ["app.py::outer"])

    def test_unparseable_file_is_skipped_not_fatal(self):
        write(self.root, "broken.py", "def (((\n")
        write(self.root, "ok.py", "def fine():\n    pass\n")
        ids = [u["id"] for u in rank_risk.discover_units(self.root)]
        self.assertEqual(ids, ["ok.py::fine"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd test-safety-net/assets && python3 test_rank_risk.py`
Expected: FAIL — `FileNotFoundError` / `ModuleNotFoundError` for `rank_risk.py`.

- [ ] **Step 3: Write minimal implementation**

Create `test-safety-net/assets/rank_risk.py`:

```python
#!/usr/bin/env python3
"""Rank a repo's untested units by risk, and triage how testable each one is.

Stdlib + git only. No network, no third-party packages, no call-graph service:
the blast-radius half is a deliberate static approximation, labelled as such in
the output, so this runs anywhere the repo does.
"""
from __future__ import annotations

import ast
import os

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "build",
             "dist", ".tox", ".mypy_cache", ".pytest_cache", "vendor",
             "site-packages", ".eggs"}


def _is_test_path(rel: str) -> bool:
    base = os.path.basename(rel)
    parts = rel.replace(os.sep, "/").split("/")
    return (base.startswith("test_") or base.endswith("_test.py")
            or "tests" in parts or "test" in parts)


def iter_py_files(root: str, include_tests: bool = False):
    """Yield repo-relative paths of .py files, skipping vendor dirs. Sorted."""
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(base, name), root).replace(os.sep, "/")
            if not include_tests and _is_test_path(rel):
                continue
            out.append(rel)
    return sorted(out)


def read_text(root: str, rel: str) -> str:
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def discover_units(root: str):
    """Module-level defs and classes in non-test .py files, sorted by id.

    Only module-level names: a nested def is not independently addressable by a
    test runner, so it cannot be pinned on its own. `_`-prefixed names are
    private by convention and are exercised through their public callers.
    """
    units = []
    for rel in iter_py_files(root):
        try:
            tree = ast.parse(read_text(root, rel))
        except SyntaxError:
            continue                      # a file we cannot parse is not a unit we can pin
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.name.startswith("_"):
                continue
            units.append({
                "id": f"{rel}::{node.name}",
                "path": rel,
                "name": node.name,
                "lineno": node.lineno,
                "kind": "class" if isinstance(node, ast.ClassDef) else "function",
            })
    return sorted(units, key=lambda u: u["id"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd test-safety-net/assets && python3 test_rank_risk.py`
Expected: `Ran 3 tests` … `OK`

- [ ] **Step 5: Remove the placeholder smoke suite**

```bash
rm test-safety-net/assets/test_test_safety_net_smoke.py
```

- [ ] **Step 6: Commit**

```bash
git add test-safety-net/assets
git commit -m "feat(test-safety-net): discover module-level Python units"
```

---

### Task 3: Churn from git history

**Files:**
- Modify: `test-safety-net/assets/rank_risk.py`
- Modify: `test-safety-net/assets/test_rank_risk.py`

**Interfaces:**
- Consumes: `iter_py_files`
- Produces: `churn(root: str, since: str = "6 months ago") -> dict[str, int]` — commits touching each repo-relative path. Returns `{}` when git is unavailable or the tree is not a repo.

- [ ] **Step 1: Write the failing test**

Append to `test-safety-net/assets/test_rank_risk.py` (before the `__main__` guard):

```python
import subprocess


def git(root, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root, capture_output=True, text=True)


class TestChurn(TempRepo):
    def test_counts_commits_per_file(self):
        if git(self.root, "init", "-q", ".").returncode != 0:
            self.skipTest("git unavailable")
        write(self.root, "hot.py", "def a():\n    pass\n")
        write(self.root, "cold.py", "def b():\n    pass\n")
        git(self.root, "add", "-A"); git(self.root, "commit", "-qm", "1")
        for i in range(3):
            write(self.root, "hot.py", f"def a():\n    return {i}\n")
            git(self.root, "add", "-A"); git(self.root, "commit", "-qm", f"c{i}")
        c = rank_risk.churn(self.root)
        self.assertEqual(c["hot.py"], 4)
        self.assertEqual(c["cold.py"], 1)

    def test_no_git_history_degrades_to_empty_not_a_crash(self):
        # A tarball checkout, or a brand-new directory, must not take the ranker
        # down — churn is one signal of two, and the other still works.
        write(self.root, "a.py", "def a():\n    pass\n")
        self.assertEqual(rank_risk.churn(self.root), {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd test-safety-net/assets && python3 test_rank_risk.py`
Expected: FAIL — `AttributeError: module 'rank_risk' has no attribute 'churn'`

- [ ] **Step 3: Write minimal implementation**

Add to `rank_risk.py` (after `read_text`, and add `import subprocess` to the imports):

```python
def churn(root: str, since: str = "6 months ago") -> dict:
    """Commits touching each repo-relative path within the window.

    Exact where git is present, and the best available proxy for "what a person
    keeps changing" — which is what an agent will touch next. Absent git or
    history, returns {} rather than raising: churn is one signal of two, and a
    tarball checkout must still get a ranking.
    """
    try:
        r = subprocess.run(
            ["git", "log", "--format=", "--name-only", f"--since={since}"],
            cwd=root, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return {}
    if r.returncode != 0:
        return {}
    counts: dict = {}
    for line in r.stdout.splitlines():
        line = line.strip()
        if line:
            counts[line] = counts.get(line, 0) + 1
    return counts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd test-safety-net/assets && python3 test_rank_risk.py`
Expected: `Ran 5 tests` … `OK`

- [ ] **Step 5: Commit**

```bash
git add test-safety-net/assets
git commit -m "feat(test-safety-net): churn signal from git history, degrading to empty"
```

---

### Task 4: Approximate inbound references

**Files:**
- Modify: `test-safety-net/assets/rank_risk.py`
- Modify: `test-safety-net/assets/test_rank_risk.py`

**Interfaces:**
- Consumes: `iter_py_files`, `read_text`
- Produces: `inbound_refs(root: str, units: list) -> dict[str, int]` — approximate count of references to each unit's name outside its own definition file.

- [ ] **Step 1: Write the failing test**

Append to the test file:

```python
class TestInboundRefs(TempRepo):
    def test_counts_references_outside_the_defining_file(self):
        write(self.root, "core.py", "def widely_used():\n    pass\n\n\ndef lonely():\n    pass\n")
        write(self.root, "a.py", "from core import widely_used\nwidely_used()\n")
        write(self.root, "b.py", "import core\ncore.widely_used()\n")
        units = rank_risk.discover_units(self.root)
        refs = rank_risk.inbound_refs(self.root, units)
        self.assertEqual(refs["core.py::widely_used"], 3)   # import + 2 call sites
        self.assertEqual(refs["core.py::lonely"], 0)

    def test_does_not_count_the_definition_itself(self):
        write(self.root, "core.py", "def solo():\n    return solo\n")
        units = rank_risk.discover_units(self.root)
        self.assertEqual(rank_risk.inbound_refs(self.root, units)["core.py::solo"], 0)

    def test_matches_whole_identifiers_only(self):
        # `apply` must not be found inside `apply_discount` or `reapply`.
        write(self.root, "core.py", "def apply():\n    pass\n")
        write(self.root, "other.py", "def apply_discount():\n    pass\n\n\ndef reapply():\n    pass\n")
        units = rank_risk.discover_units(self.root)
        self.assertEqual(rank_risk.inbound_refs(self.root, units)["core.py::apply"], 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd test-safety-net/assets && python3 test_rank_risk.py`
Expected: FAIL — `AttributeError: module 'rank_risk' has no attribute 'inbound_refs'`

- [ ] **Step 3: Write minimal implementation**

Add to `rank_risk.py` (add `import re` to the imports):

```python
def inbound_refs(root: str, units) -> dict:
    """Approximate blast radius: whole-identifier references outside the definition file.

    APPROXIMATE, on purpose, and labelled as such wherever it surfaces. It counts
    identifier occurrences, so a mention in a comment or a docstring counts and a
    dynamic `getattr(mod, name)` call does not. A real call graph would do this
    better — the report says so and names it as an optional upgrade — but
    requiring one would make the skill undeployable in the repos that need it most.
    """
    texts = {rel: read_text(root, rel) for rel in iter_py_files(root, include_tests=True)}
    counts = {}
    for u in units:
        pattern = re.compile(r"\b%s\b" % re.escape(u["name"]))
        total = 0
        for rel, text in texts.items():
            if rel == u["path"]:
                continue                  # never count a unit's own definition site
            total += len(pattern.findall(text))
        counts[u["id"]] = total
    return counts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd test-safety-net/assets && python3 test_rank_risk.py`
Expected: `Ran 8 tests` … `OK`

- [ ] **Step 5: Commit**

```bash
git add test-safety-net/assets
git commit -m "feat(test-safety-net): approximate inbound-reference blast radius"
```

---

### Task 5: Testability triage

This is the load-bearing task. It is what stops the skill writing a "test" that opens a live database connection.

**Files:**
- Modify: `test-safety-net/assets/rank_risk.py`
- Modify: `test-safety-net/assets/test_rank_risk.py`

**Interfaces:**
- Consumes: `read_text`
- Produces: `triage(root: str, unit: Unit) -> tuple[int, str]` — `(tier, reason)` where tier is 1–4.

- [ ] **Step 1: Write the failing test**

Append to the test file:

```python
class TestTriage(TempRepo):
    def _tier(self, rel, src, name):
        write(self.root, rel, src)
        unit = [u for u in rank_risk.discover_units(self.root) if u["name"] == name][0]
        return rank_risk.triage(self.root, unit)

    def test_pure_function_is_tier_1(self):
        tier, reason = self._tier("a.py", "def add(x, y):\n    return x + y\n", "add")
        self.assertEqual(tier, 1)
        self.assertIn("no I/O", reason)

    def test_filesystem_use_is_tier_2_with_a_named_boundary(self):
        tier, reason = self._tier(
            "b.py", "def load(path):\n    with open(path) as f:\n        return f.read()\n", "load")
        self.assertEqual(tier, 2)
        self.assertIn("filesystem", reason)

    def test_clock_use_is_tier_2(self):
        tier, reason = self._tier(
            "c.py", "import datetime\n\n\ndef stamp():\n    return datetime.datetime.now()\n", "stamp")
        self.assertEqual(tier, 2)
        self.assertIn("clock", reason)

    def test_network_call_is_tier_3(self):
        tier, reason = self._tier(
            "d.py", "import requests\n\n\ndef fetch(u):\n    return requests.get(u).json()\n", "fetch")
        self.assertEqual(tier, 3)
        self.assertIn("network", reason)

    def test_network_in_a_constructor_is_tier_3(self):
        # THE headline negative: a class that dials out when you instantiate it
        # cannot be pinned without a seam, and must never get a test written.
        tier, reason = self._tier(
            "e.py",
            "import socket\n\n\nclass Client:\n    def __init__(self, host):\n"
            "        self.sock = socket.create_connection((host, 80))\n",
            "Client")
        self.assertEqual(tier, 3)
        self.assertIn("network", reason)

    def test_module_level_io_makes_every_unit_in_the_file_tier_4(self):
        # Importing the module does I/O, so nothing in it can be reached at all.
        tier, reason = self._tier(
            "f.py",
            "import requests\n\nCONFIG = requests.get('http://x/cfg').json()\n\n\n"
            "def pure(x):\n    return x\n",
            "pure")
        self.assertEqual(tier, 4)
        self.assertIn("import time", reason)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd test-safety-net/assets && python3 test_rank_risk.py`
Expected: FAIL — `AttributeError: module 'rank_risk' has no attribute 'triage'`

- [ ] **Step 3: Write minimal implementation**

Add to `rank_risk.py`:

```python
# I/O markers, grouped by whether the boundary can be CONTROLLED in a test.
# Controllable -> tier 2 (pin at a wider boundary, with the boundary named).
# Uncontrollable without a seam -> tier 3 (report the seam; write nothing).
CONTROLLABLE = {
    "filesystem": ("open(", "pathlib.", "os.path.", "os.remove", "os.mkdir",
                   "shutil.", "tempfile."),
    "clock": ("datetime.now", "datetime.utcnow", "time.time", "time.sleep",
              "date.today"),
    "randomness": ("random.", "uuid.uuid4", "secrets."),
    "environment": ("os.environ", "os.getenv"),
}
UNCONTROLLABLE = {
    "network": ("requests.", "urllib.request", "httpx.", "socket.", "aiohttp.",
                "boto3.", "urlopen("),
    "database": ("psycopg2.", "sqlite3.connect", "pymongo.", "MongoClient",
                 "create_engine", "cursor()"),
    "subprocess": ("subprocess.", "os.system", "os.popen"),
}


def _markers(text):
    """(group, marker) for every I/O marker present in `text`. Deterministic order."""
    hits = []
    for group in sorted(UNCONTROLLABLE):
        for m in UNCONTROLLABLE[group]:
            if m in text:
                hits.append((group, m, False))
    for group in sorted(CONTROLLABLE):
        for m in CONTROLLABLE[group]:
            if m in text:
                hits.append((group, m, True))
    return hits


def _module_level_source(tree, lines):
    """Source of statements OUTSIDE any def/class — what runs on import."""
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Import, ast.ImportFrom)):
            continue
        seg = lines[node.lineno - 1: getattr(node, "end_lineno", node.lineno)]
        out.extend(seg)
    return "\n".join(out)


def triage(root: str, unit) -> tuple:
    """Classify how testable a unit is. Returns (tier, reason).

    The ranker is deliberately CONSERVATIVE: it reads text, not semantics, so a
    call that is actually behind an injected parameter still reads as I/O. The
    SKILL.md permits promoting a unit after inspection — but only by recording
    the promotion, never silently.
    """
    text = read_text(root, unit["path"])
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 4, "file does not parse; nothing in it can be pinned"
    lines = text.splitlines()

    # Tier 4 first: if importing the module does I/O, no unit in it is reachable.
    mod_hits = _markers(_module_level_source(tree, lines))
    uncontrollable_mod = [h for h in mod_hits if not h[2]]
    if uncontrollable_mod:
        group, marker, _ = uncontrollable_mod[0]
        return 4, f"module does {group} I/O at import time ({marker}); not reachable"

    # The unit's own span.
    node = next((n for n in tree.body
                 if getattr(n, "name", None) == unit["name"]), None)
    if node is None:
        return 4, "unit not found on re-parse"
    span = "\n".join(lines[node.lineno - 1: getattr(node, "end_lineno", node.lineno)])

    hits = _markers(span)
    if not hits:
        return 1, "no I/O markers; directly callable"
    uncontrollable = [h for h in hits if not h[2]]
    if uncontrollable:
        group, marker, _ = uncontrollable[0]
        return 3, f"{group} I/O inside the unit ({marker}); needs a seam"
    group, marker, _ = hits[0]
    return 2, f"{group} I/O ({marker}); pin at a wider boundary with {group} controlled"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd test-safety-net/assets && python3 test_rank_risk.py`
Expected: `Ran 14 tests` … `OK`

- [ ] **Step 5: Commit**

```bash
git add test-safety-net/assets
git commit -m "feat(test-safety-net): testability triage — climb outward, never cut inward"
```

---

### Task 6: Exclude units an existing test already covers

**Files:**
- Modify: `test-safety-net/assets/rank_risk.py`
- Modify: `test-safety-net/assets/test_rank_risk.py`

**Interfaces:**
- Consumes: `iter_py_files`, `read_text`
- Produces: `already_covered(root: str, units: list) -> dict[str, str]` — unit id → the test file that references it.

- [ ] **Step 1: Write the failing test**

Append to the test file:

```python
class TestAlreadyCovered(TempRepo):
    def test_unit_named_in_a_test_file_is_reported_covered(self):
        write(self.root, "core.py", "def covered():\n    pass\n\n\ndef bare():\n    pass\n")
        write(self.root, "tests/test_core.py",
              "from core import covered\n\n\ndef test_covered():\n    covered()\n")
        units = rank_risk.discover_units(self.root)
        cov = rank_risk.already_covered(self.root, units)
        self.assertEqual(cov.get("core.py::covered"), "tests/test_core.py")
        self.assertNotIn("core.py::bare", cov)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd test-safety-net/assets && python3 test_rank_risk.py`
Expected: FAIL — `AttributeError: module 'rank_risk' has no attribute 'already_covered'`

- [ ] **Step 3: Write minimal implementation**

Add to `rank_risk.py`:

```python
def already_covered(root: str, units) -> dict:
    """Unit id -> the test file naming it. Keeps the ranking on what is NOT netted.

    Same whole-identifier approximation as inbound_refs, and the same honesty:
    a test that merely imports a name counts as covering it. Over-counting here
    is the safe direction — it drops a unit down the list rather than writing a
    duplicate test for something already pinned.
    """
    test_files = [rel for rel in iter_py_files(root, include_tests=True)
                  if _is_test_path(rel)]
    texts = {rel: read_text(root, rel) for rel in test_files}
    covered = {}
    for u in units:
        pattern = re.compile(r"\b%s\b" % re.escape(u["name"]))
        for rel in sorted(texts):
            if pattern.search(texts[rel]):
                covered[u["id"]] = rel
                break
    return covered
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd test-safety-net/assets && python3 test_rank_risk.py`
Expected: `Ran 15 tests` … `OK`

- [ ] **Step 5: Commit**

```bash
git add test-safety-net/assets
git commit -m "feat(test-safety-net): exclude units an existing test already covers"
```

---

### Task 7: Rank, and expose a deterministic CLI

**Files:**
- Modify: `test-safety-net/assets/rank_risk.py`
- Modify: `test-safety-net/assets/test_rank_risk.py`

**Interfaces:**
- Consumes: `discover_units`, `churn`, `inbound_refs`, `triage`, `already_covered`
- Produces: `rank(root, since, top_n) -> dict` with keys `root`, `stack`, `window`, `units_discovered`, `ranked`, `remainder`, `not_netted`, `covered`; and `main(argv) -> int` printing that dict as JSON.

- [ ] **Step 1: Write the failing test**

Append to the test file:

```python
class TestRank(TempRepo):
    def _repo(self):
        write(self.root, "hot.py", "def risky(a, b):\n    return a + b\n")
        write(self.root, "cold.py", "def quiet(a):\n    return a\n")
        write(self.root, "net.py",
              "import requests\n\n\ndef fetch(u):\n    return requests.get(u)\n")
        write(self.root, "caller.py",
              "from hot import risky\nrisky(1, 2)\nrisky(3, 4)\n")

    def test_tier_3_units_are_not_netted_never_ranked(self):
        self._repo()
        plan = rank_risk.rank(self.root, since="10 years ago", top_n=10)
        ranked_ids = [r["id"] for r in plan["ranked"]]
        self.assertNotIn("net.py::fetch", ranked_ids)
        self.assertIn("net.py::fetch", [r["id"] for r in plan["not_netted"]])

    def test_more_referenced_unit_outranks_a_quiet_one(self):
        self._repo()
        plan = rank_risk.rank(self.root, since="10 years ago", top_n=10)
        ids = [r["id"] for r in plan["ranked"]]
        self.assertLess(ids.index("hot.py::risky"), ids.index("cold.py::quiet"))

    def test_top_n_bounds_the_ranked_list(self):
        self._repo()
        plan = rank_risk.rank(self.root, since="10 years ago", top_n=1)
        self.assertEqual(len(plan["ranked"]), 1)

    def test_output_is_byte_identical_across_runs(self):
        import json
        self._repo()
        a = json.dumps(rank_risk.rank(self.root, since="10 years ago", top_n=10), sort_keys=True)
        b = json.dumps(rank_risk.rank(self.root, since="10 years ago", top_n=10), sort_keys=True)
        self.assertEqual(a, b)

    def test_every_ranked_row_flags_the_reference_count_as_approximate(self):
        self._repo()
        plan = rank_risk.rank(self.root, since="10 years ago", top_n=10)
        self.assertTrue(plan["ranked"])
        for row in plan["ranked"]:
            self.assertIs(row["inbound_approx"], True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd test-safety-net/assets && python3 test_rank_risk.py`
Expected: FAIL — `AttributeError: module 'rank_risk' has no attribute 'rank'`

- [ ] **Step 3: Write minimal implementation**

Add to `rank_risk.py` (add `import argparse`, `import json`, `import sys` to the imports):

```python
def _normalise(value, hi):
    return 0.0 if hi <= 0 else round(value / hi, 6)


def rank(root: str, since: str = "6 months ago", top_n: int = 10) -> dict:
    """The plan: what to net, what cannot be netted, and what is already covered."""
    units = discover_units(root)
    churn_by_path = churn(root, since)
    refs = inbound_refs(root, units)
    covered = already_covered(root, units)

    rows = []
    for u in units:
        tier, reason = triage(root, u)
        row = dict(u)
        row["churn"] = churn_by_path.get(u["path"], 0)
        row["inbound_refs"] = refs.get(u["id"], 0)
        row["inbound_approx"] = True
        row["tier"] = tier
        row["tier_reason"] = reason
        row["covered_by"] = covered.get(u["id"])
        rows.append(row)

    max_churn = max((r["churn"] for r in rows), default=0)
    max_refs = max((r["inbound_refs"] for r in rows), default=0)
    for r in rows:
        if r["tier"] >= 3 or r["covered_by"]:
            r["score"] = 0.0
            continue
        # Churn and reach are both weak alone: a file nobody calls but everyone
        # edits is churny noise; a stable widely-used helper rarely breaks. The
        # product favours units that are BOTH reached and moving, which is where
        # an agent is most likely to do damage. +1 keeps a zero on one axis from
        # annihilating a strong signal on the other.
        r["score"] = round((_normalise(r["churn"], max_churn) + 1)
                           * (_normalise(r["inbound_refs"], max_refs) + 1) - 1, 6)

    netted = [r for r in rows if r["tier"] < 3 and not r["covered_by"]]
    netted.sort(key=lambda r: (-r["score"], r["id"]))   # ties broken by id: deterministic
    not_netted = sorted((r for r in rows if r["tier"] >= 3),
                        key=lambda r: (r["tier"], r["id"]))

    return {
        "root": os.path.abspath(root),
        "stack": "python",
        "window": since,
        "units_discovered": len(units),
        "ranked": netted[:top_n],
        "remainder": netted[top_n:],
        "not_netted": not_netted,
        "covered": sorted(covered),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Rank untested units by risk and triage their testability.")
    p.add_argument("repo", help="path to the repository to analyse")
    p.add_argument("--top-n", type=int, default=10,
                   help="how many units to net in this pass (default: 10)")
    p.add_argument("--since", default="6 months ago",
                   help="churn window, any git --since expression (default: 6 months ago)")
    args = p.parse_args(argv)
    if not os.path.isdir(args.repo):
        sys.stderr.write(f"error: not a directory: {args.repo}\n")
        return 2
    json.dump(rank(args.repo, args.since, args.top_n), sys.stdout,
              indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd test-safety-net/assets && python3 test_rank_risk.py`
Expected: `Ran 20 tests` … `OK`

- [ ] **Step 5: Verify the CLI runs against this very repo**

Run: `python3 test-safety-net/assets/rank_risk.py . --top-n 3 | head -30`
Expected: valid JSON with `"stack": "python"` and at most 3 rows under `ranked`.

- [ ] **Step 6: Commit**

```bash
git add test-safety-net/assets
git commit -m "feat(test-safety-net): deterministic risk ranking and CLI"
```

---

### Task 8: SKILL.md and references

The gate fails on any `references/` path named in SKILL.md that does not exist, so these must land in one commit.

**Files:**
- Modify: `test-safety-net/SKILL.md`
- Create: `test-safety-net/references/triage.md`
- Create: `test-safety-net/references/stacks.md`
- Create: `test-safety-net/references/parameters.md`
- Delete: `test-safety-net/references/overview.md` (scaffold placeholder)
- Modify: `test-safety-net/README.md`

**Interfaces:**
- Consumes: `rank_risk.py`'s CLI and JSON shape from Task 7
- Produces: the agent-facing workflow; the report template consumed by Task 9's artifact checks

- [ ] **Step 1: Write `references/triage.md`**

Carry over from the spec, verbatim where possible: the four-tier table, the Tier-2 boundary-control table (filesystem → temp dir; clock/randomness → the language's freeze/seed hooks; database → in-memory or throwaway file; HTTP → hand off to `mockstar-mock`), and the seam vocabulary (sprout/wrap: add code beside, never rearrange). State the rule at the top: **never cut inward, climb outward** — refactoring untested code to make it testable inverts the safety property this skill exists to provide.

- [ ] **Step 2: Write `references/stacks.md`**

The per-stack table. Fill the `python` row: find units = module-level `def`/`class`; tests go in `tests/` or `test_*.py` beside; framework = pytest, falling back to `unittest`; **run ONE test** = `pytest path::name` or `python3 -m unittest module.Class.test_name`. Mark `node`, `go`, `rust` as *not yet supported — see the follow-up plan*, so nobody reads the skill as claiming more than it does. Document the `make` case: make is a runner over an underlying language, so detect the real language for authoring and fall through to the native runner for the proof loop.

- [ ] **Step 3: Write `references/parameters.md`**

Document `repo` (positional), `--top-n` (default 10), `--since` (default `6 months ago`), and the JSON output keys from the Shared data shapes section of this plan.

- [ ] **Step 4: Write the SKILL.md body**

It must contain, at minimum:

- The `$SKILL_DIR` resolution preamble (required by `scripts/gates/asset-paths.sh`), invoking the ranker as `python3 "$SKILL_DIR/assets/rank_risk.py" <repo>`.
- The five numbered workflow steps from the spec.
- The red→green proof loop stated as an ordered procedure — **write the wrong expectation → run → require RED → correct → run → require GREEN** — and the stated limit verbatim: *"This proves the assertion binds to real output from the unit. It does not prove the test detects every behavioural change, which is what true mutation testing would give."*
- The five invariants from the spec, as a `## Invariants (do not violate)` section.
- The report template with its `tier` and `kind` columns and the four sections (suspected bugs / could not prove / not netted / ranked remainder).
- A note that the ranker's triage is conservative and a unit may be promoted after inspection, but only by **recording** the promotion.
- **The literal-emission rule** (the spec's Security surface section). Captured output from
  running the user's code is embedded into generated test files — that is code generation from
  program output, and it is the one real injection surface here. State it as a hard rule:

  > Emit every captured value with `repr()`. Never build a test's expected value by string
  > concatenation or f-string interpolation of captured output. A captured string containing a
  > quote, a newline or a backslash must become an inert literal, never executable source.

- The report's "how to improve this" note: the inbound-reference count is a static approximation,
  and a real call-graph tool — if the user already has one — computes that half better. Named as
  an **optional** upgrade, never a dependency.

- [ ] **Step 5: Remove the scaffold placeholder and update the README**

```bash
rm test-safety-net/references/overview.md
```

Rewrite `README.md` as a human-facing overview: the gap it fills, the two disciplines, the four tiers, and the chain (`agent-ready-rails` → `verifier-installer` → this → `clean-code`).

- [ ] **Step 6: Verify the gate is green**

Run: `make gate-skill SKILL=test-safety-net`
Expected: PASS on validate, scan-leaks, playbook, frontmatter, asset-paths, and the unit suite. (The eval still fails here — Task 9 replaces the scaffold's placeholder.)

- [ ] **Step 7: Commit**

```bash
git add test-safety-net
git commit -m "feat(test-safety-net): the workflow, the triage reference, and the report template"
```

---

### Task 9: The outcome eval

**Files:**
- Modify: `test-safety-net/eval/run_eval.py` (replace the scaffold placeholder wholesale)

**Interfaces:**
- Consumes: `rank_risk.py`, `SKILL.md`, `references/triage.md`
- Produces: `EVAL_RESULT: PASS (n/n checks)` and exit 0 iff all pass

- [ ] **Step 1: Write the eval**

Replace `test-safety-net/eval/run_eval.py` with a script that builds fixture repos under `tempfile.mkdtemp()` (no repo writes) and asserts:

*Positive:*
1. a pure function is ranked, and its row carries `inbound_approx: True`
2. a widely-referenced churny unit outranks a quiet one
3. repeated runs emit byte-identical JSON
4. an already-covered unit is excluded from `ranked` and listed in `covered`

*Negative — each must fail if the guard is removed:*
5. **a class that opens a network connection in `__init__` is triaged tier 3, appears in `not_netted`, and appears nowhere in `ranked`** — the skill must decline rather than fake success
6. a module doing I/O at import time puts *every* unit in that file at tier 4
7. a repo with **no git history** still produces a ranking (churn degrades to 0, no crash)
8. a syntactically broken file is skipped without taking the run down

*Artifact checks — the half the eval can honestly grade about the prompt:*
9. `SKILL.md` contains the RED-before-GREEN ordering (`RED` appears before `GREEN` in the proof step)
10. `SKILL.md`'s report template carries both a `tier` and a `kind` column
11. `SKILL.md` states the proof's limit (matches `does not prove` and `every behavioural change`)
12. `references/triage.md` names all four tiers and the `mockstar-mock` HTTP handoff
13. `SKILL.md` states the literal-emission rule (matches `repr(` and `never` near it)

*Injection surface — executable, not just an artifact check:*
14. a hostile captured value round-trips inertly: for each of `'"; import os; os.system("x")'`,
    `'line\nbreak'`, `'back\\slash'` and `"quo'te"`, assert
    `ast.literal_eval(repr(v)) == v` — proving the documented technique actually neutralises the
    value rather than trusting that it does

*Scope honesty:*
15. a repo containing no Python files reports `units_discovered: 0` with an empty `ranked` list —
    the skill must not present "found nothing to test" and "this is not a Python repo" as the
    same answer. SKILL.md step 1 says to check the stack before trusting an empty ranking.

Follow the exact output protocol of `verifier-installer/eval/run_eval.py`: a `check(name, ok, detail)` helper printing `CHECK: <name> — PASS|FAIL`, a final `EVAL_RESULT:` line, and `sys.exit(0 if ok else 1)`.

- [ ] **Step 2: Run the eval and verify it passes**

Run: `cd test-safety-net/eval && python3 run_eval.py`
Expected: `EVAL_RESULT: PASS (15/15 checks)`

- [ ] **Step 3: Prove the headline negative can actually fail**

Temporarily change `triage()` to return `(1, "forced")` for every unit, re-run the eval, and confirm check 5 flips to FAIL. Then revert.

Run: `cd test-safety-net/eval && python3 run_eval.py`
Expected after reverting: `EVAL_RESULT: PASS (15/15 checks)`

This is the repo's own standard — an eval that cannot fail is not an eval.

- [ ] **Step 4: Verify the whole gate is green**

Run: `make gate-skill SKILL=test-safety-net`
Expected: PASS on every check including the eval.

- [ ] **Step 5: Commit**

```bash
git add test-safety-net/eval
git commit -m "feat(test-safety-net): outcome eval with the tier-3-declines negative fixture"
```

---

### Task 10: Integrate into the repo

**Files:**
- Modify: `scripts/ab-validate.py`
- Modify: `README.md` (the skills table)
- Modify: `verifier-installer/SKILL.md`, `agent-ready-rails/SKILL.md`, `clean-code/SKILL.md` (cross-links)

**Interfaces:**
- Consumes: `test-safety-net/assets/rank_risk.py`
- Produces: a repo where `make gate` and `make ab-validate` are both green

- [ ] **Step 1: Add the A/B row**

Add a `check_test_safety_net(old, new)` to `scripts/ab-validate.py`, a `SINCE_TEST_SAFETY_NET` constant set to this branch's first commit, and register the check in `main()` alongside the others. Measure the guardrail that matters:

```python
def check_test_safety_net(old, new):
    """The guardrail: a unit that dials out in its constructor must NOT be netted."""
    scratch = tempfile.mkdtemp()
    os.makedirs(os.path.join(scratch, "repo"), exist_ok=True)
    with open(os.path.join(scratch, "repo", "client.py"), "w") as f:
        f.write("import socket\n\n\nclass Client:\n    def __init__(self, host):\n"
                "        self.sock = socket.create_connection((host, 80))\n")

    def netted_unsafely(tree):
        ranker = os.path.join(tree, "test-safety-net", "assets", "rank_risk.py")
        if not os.path.isfile(ranker):
            return 1        # baseline has no ranker: nothing stops the unsafe test
        r = subprocess.run([sys.executable, ranker, os.path.join(scratch, "repo")],
                           capture_output=True, text=True, timeout=120)
        try:
            plan = json.loads(r.stdout)
        except ValueError:
            return 1
        return 1 if any(row["id"].endswith("::Client") for row in plan["ranked"]) else 0

    a, b = netted_unsafely(old), netted_unsafely(new)
    row("test-safety-net", "unit that dials out in __init__ is netted (lower=better)",
        a, b, b < a,
        "writing a test for it would open a real socket; the skill must decline",
        since=SINCE_TEST_SAFETY_NET)
```

- [ ] **Step 2: Run ab-validate and verify the new row is IMPROVED**

Run: `python3 scripts/ab-validate.py`
Expected: the new row reads `old=1 new=0 IMPROVED`, and the summary ends `AB_RESULT: PASS` with `0 unproven · 0 worse`.

- [ ] **Step 3: Add the cross-links**

- `README.md` — a row in the skills table between `verifier-installer` and `clean-code`.
- `verifier-installer/SKILL.md` — after the loop is installed, hand off: "the loop runs but has only a smoke test — `test-safety-net` fills it."
- `agent-ready-rails/SKILL.md` — where R1 is weak but a loop already exists, name `test-safety-net`.
- `clean-code/SKILL.md` — it receives the Tier 3/4 seam list.

- [ ] **Step 4: Verify descriptions still fit after edits**

Run: `for d in test-safety-net verifier-installer agent-ready-rails clean-code; do sh scripts/gates/validate-skill.sh $d | grep description; done`
Expected: four `PASS` lines, every length ≤ 1024. (`clean-code` was at 973 — adding a cross-link can push it over.)

- [ ] **Step 5: Run the full gate**

Run: `make gate`
Expected: `GATE_RESULT: PASS`

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(test-safety-net): wire into the chain, with an A/B row for the tier-3 guard"
```

---

## Follow-up (not this plan)

- **Stacks 2–5** — node, go, rust, and the `make` runner case: each a `references/stacks.md` row, detector support in `discover_units`/`triage`, and its own eval fixture set. Its own plan; must not reopen the workflow.
- **The cross-tool agreement test with `verifier-installer`** that the spec requires. It is
  deferred deliberately, not dropped: this v1 hardcodes `"stack": "python"` and performs no stack
  *detection*, so there is nothing yet duplicating `detect_stack.py` for the two to disagree
  about. The moment stack detection lands (the plan above), that test must land with it — the
  `okf.py`/`garden.py` divergence is exactly what happens when duplicated detection ships
  unguarded, and it went unnoticed for months.
- **The `make`-fronted "no single-test invocation" negative fixture** from the spec's eval
  section, for the same reason: `make` handling arrives with the multi-stack plan.
- **Manual model eval** — the behavioural claim (does the model actually write good tests?) cannot be graded by a model-free eval. Record it under `docs/test-safety-net/<date>-*.md`, following the blinded-arms, fresh-context-judge shape of `docs/crafting-self-prompting-loops/2026-07-27-typed-state-model-eval.md`.
- **`mockstar-mock` handoff ergonomics** — flagged in the spec and still unverified in practice. Worth a spike before leaning on it in `references/triage.md` beyond a pointer.
