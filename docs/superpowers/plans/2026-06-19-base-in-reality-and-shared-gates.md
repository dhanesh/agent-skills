# base-in-reality Skill + Shared Skill Gates — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a read-only, research-grounded repo-audit skill (`base-in-reality`) AND a repo-wide skill-quality gate (vendored validate/scan-leaks/dry-run scripts + Makefile + PR CI) that all skills in this repo must pass.

**Architecture:** Two parts. **Part A** vendors repo2skill's gate scripts into `scripts/gates/`, wraps them in a `Makefile` that discovers every skill dir (any top-level dir with a `SKILL.md`), fixes the two existing skills that currently fail, and runs the gate on PRs via GitHub Actions. **Part B** builds `base-in-reality` as a prompt-driven, multi-agent audit skill: a stdlib-only Python source-fetch helper, a finding JSON schema, an optional Workflow accelerator, three reference docs, a runtime report skeleton, and the SKILL.md orchestration — validated by Part A's `make gate-skill`.

**Tech Stack:** POSIX `sh` (gate scripts), GNU `make`, GitHub Actions, Python 3.9+ stdlib only (run via `uv`), Node (optional, for `.mjs` syntax check), Markdown.

## Global Constraints

- **Gate must stay green on `main`.** Part A wires `make gate` into PR CI; every skill dir must pass `validate-skill.sh` + `scan-leaks.sh` (and `dry-run-replay.sh` *only if* it has a `PARAMETERS.md`). Fix existing failures (A3) before enabling CI (A4).
- **Python is stdlib-only, run via `uv`.** No third-party deps anywhere in `base-in-reality`. Scripts carry PEP 723 inline metadata (`dependencies = []`) and run with `uv run <file>`. Never `pip`/`pytest`/`jsonschema`/`requests`.
- **`base-in-reality` is read-only by default.** It never edits code. The only write path is `--annotate`, which inserts comment markers only.
- **No fabricated citations (load-bearing invariant).** Every finding's evidence must be a URL/DOI fetched in-session. Ungrounded ⇒ verdict capped at `UNCONFIRMED`, never `VIOLATION`/`DEVIATION`.
- **Skill `name` field is kebab-case.** Directory is `base-in-reality` (used by `npx skills add --skill base-in-reality`); frontmatter `name:` is `base-in-reality` (the standard rejects `_`). Directory and name match, consistent with every other skill in the repo. *(The directory was originally created as `base_in_reality`; it was renamed to kebab-case after the fact — see the "rename skill dir" change.)*
- **No `assets/templates/` + no `PARAMETERS.md` for `base-in-reality`.** It has no install-time substitution parameters, so `validate-skill.sh` bijection SKIPs and `dry-run-replay.sh` is N/A. The report skeleton uses `<angle-bracket>` runtime fill markers, never `{{UPPER_SNAKE}}`.
- **Frontmatter carries `x-spec-version: 1.0`** (matches repo2skill's template).
- **Gate script source of truth:** `/Users/dhanesh/.agents/skills/repo2skill/scripts/` (copy verbatim; do not re-author).
- Commit after every task. Conventional-commit messages.

---

# PART A — Shared Skill Gates

### Task A1: Vendor the gate scripts into the repo

**Files:**
- Create: `scripts/gates/validate-skill.sh` (copy)
- Create: `scripts/gates/scan-leaks.sh` (copy)
- Create: `scripts/gates/dry-run-replay.sh` (copy)
- Create: `scripts/gates/package-skill.sh` (copy)
- Create: `scripts/gates/README.md`

**Interfaces:**
- Produces: four executable `sh` gate scripts at `scripts/gates/*.sh`, each invoked as `sh scripts/gates/<name>.sh <skill-dir> [flags]`. `validate-skill.sh` prints `VALIDATION_RESULT: PASS|FAIL`; `scan-leaks.sh` prints `SCAN_RESULT: PASS|FAIL`; `dry-run-replay.sh` prints `DRY_RUN_RESULT: PASS|FAIL`. All exit non-zero on failure.

- [ ] **Step 1: Copy the four scripts verbatim from repo2skill**

```bash
mkdir -p scripts/gates
SRC=/Users/dhanesh/.agents/skills/repo2skill/scripts
cp "$SRC/validate-skill.sh" "$SRC/scan-leaks.sh" "$SRC/dry-run-replay.sh" "$SRC/package-skill.sh" scripts/gates/
chmod +x scripts/gates/*.sh
```

- [ ] **Step 2: Write `scripts/gates/README.md`**

```markdown
# Skill Quality Gates

Vendored from the [`repo2skill`](https://github.com/dhanesh/agent-skills) authoring
skill so this repo can enforce the same Agent Skills standard on every skill it ships.

| Script | Checks | Result line |
|--------|--------|-------------|
| `validate-skill.sh <dir> [--spec FILE]` | SKILL.md + README.md exist; frontmatter `name` (kebab, ≤64) and `description` (≤1024); no dangling `references/`,`assets/`,`scripts/` paths; template↔PARAMETERS.md bijection; optional spec-version drift | `VALIDATION_RESULT:` |
| `scan-leaks.sh <dir> [--denylist FILE]` | Secrets/credentials (AWS, GitHub, Slack, Stripe, PEM, JWT, hex, high-entropy, connection strings) and optional source-identifier denylist | `SCAN_RESULT:` |
| `dry-run-replay.sh <dir> <scratch>` | Substitutes `PARAMETERS.md` examples into `assets/templates/*`; fails on residual `{{TOKENS}}` or orphan params. **Requires a `PARAMETERS.md`** — only meaningful for parameterized skills. | `DRY_RUN_RESULT:` |
| `package-skill.sh <dir> <out>` | Produces a distributable archive of the skill | — |

Run all gates across every skill via the repo `Makefile`: `make gate`.

These are vendored copies. To update them, re-copy from `repo2skill/scripts/` and note
the sync in the commit message.
```

- [ ] **Step 3: Verify the vendored scripts run against a known-good skill**

Run: `sh scripts/gates/validate-skill.sh starlight-handbook-kit | tail -1`
Expected: `VALIDATION_RESULT: PASS`

Run: `sh scripts/gates/scan-leaks.sh starlight-handbook-kit | tail -1`
Expected: `SCAN_RESULT: PASS — no secrets or denylist violations found in starlight-handbook-kit`

- [ ] **Step 4: Commit**

```bash
git add scripts/gates/
git commit -m "ci: vendor repo2skill quality-gate scripts into scripts/gates/"
```

---

### Task A2: Makefile to run the gates across all skills

**Files:**
- Create: `Makefile`

**Interfaces:**
- Consumes: `scripts/gates/*.sh` from Task A1.
- Produces: targets `list-skills`, `validate`, `scan-leaks`, `dry-run`, `gate`, `gate-skill SKILL=<dir>`, `clean`. `gate` runs validate + scan-leaks on every skill and dry-run only on skills that have a `PARAMETERS.md`; exits non-zero if any sub-gate fails.

- [ ] **Step 1: Write the `Makefile` (INDENTATION IS TABS, not spaces)**

```makefile
# Skill quality gates. Run `make gate` to validate every skill in this repo.
SHELL := /bin/sh
GATES := scripts/gates

# Every top-level directory containing a SKILL.md is a skill.
SKILLS := $(patsubst %/SKILL.md,%,$(wildcard */SKILL.md))

.PHONY: gate validate scan-leaks dry-run list-skills clean $(addprefix gate-,$(SKILLS))

list-skills:
	@printf '%s\n' $(SKILLS)

# Full gate across all skills. Fails fast on the first failing skill.
gate: clean
	@rc=0; for d in $(SKILLS); do \
		printf '\n=== %s ===\n' "$$d"; \
		sh $(GATES)/validate-skill.sh "$$d" | tail -1 || rc=1; \
		sh $(GATES)/scan-leaks.sh "$$d" | tail -1 || rc=1; \
		if [ -f "$$d/PARAMETERS.md" ]; then \
			scratch=$$(mktemp -d); \
			sh $(GATES)/dry-run-replay.sh "$$d" "$$scratch" | tail -1 || rc=1; \
			rm -rf "$$scratch"; \
		fi; \
	done; \
	exit $$rc

validate:
	@rc=0; for d in $(SKILLS); do \
		sh $(GATES)/validate-skill.sh "$$d" | tail -1 || rc=1; \
	done; exit $$rc

scan-leaks: clean
	@rc=0; for d in $(SKILLS); do \
		sh $(GATES)/scan-leaks.sh "$$d" | tail -1 || rc=1; \
	done; exit $$rc

dry-run:
	@rc=0; for d in $(SKILLS); do \
		if [ -f "$$d/PARAMETERS.md" ]; then \
			scratch=$$(mktemp -d); \
			sh $(GATES)/dry-run-replay.sh "$$d" "$$scratch" | tail -1 || rc=1; \
			rm -rf "$$scratch"; \
		fi; \
	done; exit $$rc

# Gate a single skill: make gate-skill SKILL=base-in-reality
gate-skill:
	@test -n "$(SKILL)" || { echo "usage: make gate-skill SKILL=<dir>"; exit 2; }
	@sh $(GATES)/validate-skill.sh "$(SKILL)"
	@sh $(GATES)/scan-leaks.sh "$(SKILL)"
	@if [ -f "$(SKILL)/PARAMETERS.md" ]; then \
		scratch=$$(mktemp -d); \
		sh $(GATES)/dry-run-replay.sh "$(SKILL)" "$$scratch"; \
		rm -rf "$$scratch"; \
	fi

# Remove build artifacts that pollute scan-leaks (e.g. __pycache__/*.pyc).
clean:
	@find . -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -delete 2>/dev/null || true
```

- [ ] **Step 2: Verify skill discovery**

Run: `make list-skills`
Expected (order may vary): lines including `context-hygiene-kit`, `crafting-self-prompting-loops`, `starlight-handbook-kit`, `tmux-agent-herdr-lite`

- [ ] **Step 3: Verify single-skill gate on a known-good skill**

Run: `make gate-skill SKILL=starlight-handbook-kit`
Expected: ends with `VALIDATION_RESULT: PASS` and `SCAN_RESULT: PASS ...` (and a `DRY_RUN_RESULT: PASS` line, since starlight has a PARAMETERS.md)

- [ ] **Step 4: Commit**

```bash
git add Makefile
git commit -m "ci: add Makefile to run skill gates locally across all skills"
```

---

### Task A3: Fix the two existing skills that fail the gate

**Files:**
- Create: `tmux-agent-herdr-lite/README.md`
- Create: `crafting-self-prompting-loops/PARAMETERS.md`

**Interfaces:**
- Consumes: `make gate-skill` from Task A2.
- Produces: both skills pass `validate-skill.sh`. (`tmux` fails today on missing README; `crafting` fails on `assets/templates/` present without `PARAMETERS.md` — its templates contain zero `{{UPPER_SNAKE}}` tokens, so a no-params PARAMETERS.md satisfies bijection.)

- [ ] **Step 1: Confirm the current failures (baseline)**

Run: `make gate-skill SKILL=tmux-agent-herdr-lite; echo "rc=$?"`
Expected: `FAIL: README.md not found ...`, `VALIDATION_RESULT: FAIL`, non-zero rc

Run: `make gate-skill SKILL=crafting-self-prompting-loops; echo "rc=$?"`
Expected: `FAIL: bijection: assets/templates/ exists but PARAMETERS.md is missing`, non-zero rc

- [ ] **Step 2: Author `tmux-agent-herdr-lite/README.md`**

Read `tmux-agent-herdr-lite/SKILL.md` first to mirror its framing. Write a human-facing README with these sections (match the style of `context-hygiene-kit/README.md`):
- Title + one-paragraph summary (what the cockpit does: tmux + Zellij-like ergonomics + Herdr-like agent supervision).
- `## Install` with: `npx skills add dhanesh/agent-skills --skill tmux-agent-herdr-lite`
- `## What it sets up` — bullet list (menus/layouts/navigation, agent launch wrappers, pane metadata, blocked/working/done status detection, tmux status integration, jump-to-status).
- `## Prerequisites` — tmux version, shell.
- `See SKILL.md for full usage.`

No `{{...}}` tokens anywhere in the README.

- [ ] **Step 3: Author `crafting-self-prompting-loops/PARAMETERS.md`**

These templates are copy-and-adapt skeletons with **no machine-substituted placeholders**. Declare that explicitly so bijection passes (empty placeholder set on both sides):

```markdown
# Parameters

This skill ships **copy-and-adapt loop templates** in `assets/templates/`, not
machine-substituted templates. They contain no `{{PLACEHOLDER}}` substitution
tokens — you fill them in by editing prose to fit your loop, guided by `SKILL.md`.

| Placeholder | Description | Example | Default |
|-------------|-------------|---------|---------|
| _(none)_ | This skill has no install-time substitution parameters. | — | — |

To adapt a template, copy the relevant `*.template.md` file and edit it directly.
```

(The validator and `dry-run-replay.sh` both ignore rows whose first cell is not a
`{{UPPER_SNAKE}}` token, so the `_(none)_` row is informational only and bijection
sees an empty set on both sides → PASS.)

- [ ] **Step 4: Verify both skills now pass, and the full gate is green**

Run: `make gate-skill SKILL=tmux-agent-herdr-lite | tail -2`
Expected: `VALIDATION_RESULT: PASS` and `SCAN_RESULT: PASS ...`

Run: `make gate-skill SKILL=crafting-self-prompting-loops | tail -3`
Expected: `VALIDATION_RESULT: PASS`, `SCAN_RESULT: PASS ...`, `DRY_RUN_RESULT: PASS`

Run: `make gate; echo "rc=$?"`
Expected: every skill section shows PASS lines; final `rc=0`

- [ ] **Step 5: Commit**

```bash
git add tmux-agent-herdr-lite/README.md crafting-self-prompting-loops/PARAMETERS.md
git commit -m "fix(skills): make tmux + crafting skills pass the quality gate (README, PARAMETERS)"
```

---

### Task A4: Run the gate on pull requests via GitHub Actions

**Files:**
- Create: `.github/workflows/skill-gates.yml`

**Interfaces:**
- Consumes: `make gate` from Task A2 (now green per A3).
- Produces: a CI job that fails a PR when any skill fails the gate.

- [ ] **Step 1: Write `.github/workflows/skill-gates.yml`**

```yaml
name: skill-gates

on:
  pull_request:
  push:
    branches: [main]

jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run skill quality gates
        run: make gate
```

(No language toolchain needed: the gates are POSIX `sh` + `awk` + `find`, all present
on `ubuntu-latest`. `gitleaks` is opportunistic inside `scan-leaks.sh` and skipped when
absent. The committed tree has no `__pycache__`, so `make clean` is a no-op in CI.)

- [ ] **Step 2: Validate the workflow YAML parses**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/skill-gates.yml')); print('yaml ok')"`
Expected: `yaml ok`
(If PyYAML is unavailable, instead run `uv run --with pyyaml python3 -c "..."` with the same body.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/skill-gates.yml
git commit -m "ci: run skill quality gates on pull requests"
```

---

# PART B — base-in-reality skill

All Part B files live under `base-in-reality/`.

### Task B1: Source-fetch helper — query-URL builder (TDD)

**Files:**
- Create: `base-in-reality/assets/fetch_sources.py`
- Test: `base-in-reality/assets/test_fetch_sources.py`

**Interfaces:**
- Produces: `SOURCES` tuple; `build_query_url(source: str, query: str, limit: int = 5) -> str` (raises `ValueError` on unknown source). Consumed by Task B2/B3.

- [ ] **Step 1: Write the failing test**

Create `base-in-reality/assets/test_fetch_sources.py`:

```python
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Unit tests for fetch_sources.py — stdlib unittest, run via `uv run`."""
import unittest
import fetch_sources as fs


class BuildQueryURL(unittest.TestCase):
    def test_arxiv(self):
        u = fs.build_query_url("arxiv", "merkle tree", 5)
        self.assertTrue(u.startswith("http://export.arxiv.org/api/query?"))
        self.assertIn("search_query=all%3Amerkle+tree", u)
        self.assertIn("max_results=5", u)

    def test_pubmed(self):
        u = fs.build_query_url("pubmed", "sepsis lactate", 3)
        self.assertIn("eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", u)
        self.assertIn("db=pubmed", u)
        self.assertIn("retmax=3", u)
        self.assertIn("term=sepsis+lactate", u)

    def test_crossref(self):
        u = fs.build_query_url("crossref", "apr reducing balance", 7)
        self.assertTrue(u.startswith("https://api.crossref.org/works?"))
        self.assertIn("rows=7", u)

    def test_openalex(self):
        u = fs.build_query_url("openalex", "raft consensus", 4)
        self.assertTrue(u.startswith("https://api.openalex.org/works?"))
        self.assertIn("per_page=4", u)
        self.assertIn("search=raft+consensus", u)

    def test_semanticscholar(self):
        u = fs.build_query_url("semanticscholar", "bcrypt", 2)
        self.assertIn("api.semanticscholar.org/graph/v1/paper/search", u)
        self.assertIn("limit=2", u)
        self.assertIn("query=bcrypt", u)

    def test_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            fs.build_query_url("scholar", "x", 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd base-in-reality/assets && uv run test_fetch_sources.py`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'fetch_sources'`

- [ ] **Step 3: Write the minimal implementation**

Create `base-in-reality/assets/fetch_sources.py`:

```python
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""fetch_sources.py — keyless scholarly-source query helper for base-in-reality.

Queries arxiv, PubMed, Crossref, OpenAlex, or Semantic Scholar and prints a
normalized JSON array of records to stdout. Stdlib-only (urllib/json/xml) so it
runs anywhere via `uv run fetch_sources.py --source <s> --query "<q>"`.
"""
from __future__ import annotations

from urllib.parse import urlencode

SOURCES = ("arxiv", "pubmed", "crossref", "openalex", "semanticscholar")


def build_query_url(source: str, query: str, limit: int = 5) -> str:
    source = source.lower()
    if source == "arxiv":
        params = urlencode({"search_query": f"all:{query}", "start": 0, "max_results": limit})
        return f"http://export.arxiv.org/api/query?{params}"
    if source == "pubmed":
        params = urlencode({"db": "pubmed", "retmode": "json", "retmax": limit, "term": query})
        return f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{params}"
    if source == "crossref":
        params = urlencode({"rows": limit, "query": query})
        return f"https://api.crossref.org/works?{params}"
    if source == "openalex":
        params = urlencode({"per_page": limit, "search": query})
        return f"https://api.openalex.org/works?{params}"
    if source == "semanticscholar":
        params = urlencode({
            "limit": limit,
            "fields": "title,authors,year,venue,externalIds,abstract",
            "query": query,
        })
        return f"https://api.semanticscholar.org/graph/v1/paper/search?{params}"
    raise ValueError(f"unknown source: {source!r} (expected one of {', '.join(SOURCES)})")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd base-in-reality/assets && uv run test_fetch_sources.py`
Expected: `Ran 6 tests` … `OK`

- [ ] **Step 5: Commit**

```bash
git add base-in-reality/assets/fetch_sources.py base-in-reality/assets/test_fetch_sources.py
git commit -m "feat(base-in-reality): add keyless source query-URL builder"
```

---

### Task B2: Source-fetch helper — response normalizers (TDD)

**Files:**
- Modify: `base-in-reality/assets/fetch_sources.py`
- Modify: `base-in-reality/assets/test_fetch_sources.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_record(...)`, `_openalex_abstract(inv)`, and `normalize_arxiv`, `normalize_crossref`, `normalize_openalex`, `normalize_pubmed`, `normalize_semanticscholar` — each `(raw_bytes_or_str) -> list[dict]`. Every record dict has keys: `source, title, authors, year, venue, id, url, doi, abstract`. `NORMALIZERS` dict maps source → normalizer.

- [ ] **Step 1: Add failing tests**

Append to `base-in-reality/assets/test_fetch_sources.py` (before the `if __name__` block):

```python
ARXIV_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2101.00001v1</id>
    <published>2021-01-01T00:00:00Z</published>
    <title>A Merkle Tree Result</title>
    <summary>We study authenticated data structures.</summary>
    <author><name>Ada Lovelace</name></author>
    <author><name>Alan Turing</name></author>
    <arxiv:doi>10.1000/xyz</arxiv:doi>
  </entry>
</feed>"""

CROSSREF_JSON = b"""{"message":{"items":[{
  "title":["Reducing Balance APR"],
  "author":[{"given":"Jane","family":"Doe"}],
  "issued":{"date-parts":[[2019,5,1]]},
  "container-title":["J. Finance"],
  "DOI":"10.5555/abc","URL":"https://doi.org/10.5555/abc",
  "abstract":"On interest computation."}]}}"""

OPENALEX_JSON = b"""{"results":[{
  "title":"Raft Consensus","publication_year":2014,
  "authorships":[{"author":{"display_name":"Diego Ongaro"}}],
  "primary_location":{"source":{"display_name":"USENIX ATC"}},
  "doi":"https://doi.org/10.1/raft","id":"https://openalex.org/W1",
  "abstract_inverted_index":{"Raft":[0],"is":[1],"understandable":[2]}}]}"""

PUBMED_JSON = b"""{"result":{"uids":["111"],"111":{
  "title":"Lactate in Sepsis","source":"Crit Care",
  "fulljournalname":"Critical Care","pubdate":"2018 Mar",
  "authors":[{"name":"Smith J"}],
  "articleids":[{"idtype":"doi","value":"10.9/sep"}]}}}"""

S2_JSON = b"""{"data":[{
  "paperId":"deadbeef","title":"bcrypt analysis","year":2016,
  "venue":"USENIX Security","externalIds":{"DOI":"10.7/bcrypt"},
  "authors":[{"name":"Niels Provos"}],"abstract":"Adaptive hashing."}]}"""


class Normalizers(unittest.TestCase):
    def test_arxiv(self):
        recs = fs.normalize_arxiv(ARXIV_XML)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["source"], "arxiv")
        self.assertEqual(r["title"], "A Merkle Tree Result")
        self.assertEqual(r["authors"], ["Ada Lovelace", "Alan Turing"])
        self.assertEqual(r["year"], 2021)
        self.assertEqual(r["doi"], "10.1000/xyz")
        self.assertEqual(r["url"], "http://arxiv.org/abs/2101.00001v1")

    def test_crossref(self):
        r = fs.normalize_crossref(CROSSREF_JSON)[0]
        self.assertEqual(r["title"], "Reducing Balance APR")
        self.assertEqual(r["authors"], ["Jane Doe"])
        self.assertEqual(r["year"], 2019)
        self.assertEqual(r["doi"], "10.5555/abc")

    def test_openalex(self):
        r = fs.normalize_openalex(OPENALEX_JSON)[0]
        self.assertEqual(r["title"], "Raft Consensus")
        self.assertEqual(r["year"], 2014)
        self.assertEqual(r["venue"], "USENIX ATC")
        self.assertEqual(r["abstract"], "Raft is understandable")

    def test_pubmed(self):
        r = fs.normalize_pubmed(PUBMED_JSON)[0]
        self.assertEqual(r["title"], "Lactate in Sepsis")
        self.assertEqual(r["year"], 2018)
        self.assertEqual(r["venue"], "Critical Care")
        self.assertEqual(r["doi"], "10.9/sep")
        self.assertEqual(r["url"], "https://pubmed.ncbi.nlm.nih.gov/111/")

    def test_semanticscholar(self):
        r = fs.normalize_semanticscholar(S2_JSON)[0]
        self.assertEqual(r["title"], "bcrypt analysis")
        self.assertEqual(r["year"], 2016)
        self.assertEqual(r["doi"], "10.7/bcrypt")
        self.assertEqual(r["url"], "https://www.semanticscholar.org/paper/deadbeef")

    def test_normalizers_registry(self):
        self.assertEqual(set(fs.NORMALIZERS), set(fs.SOURCES))
```

- [ ] **Step 2: Run to verify failure**

Run: `cd base-in-reality/assets && uv run test_fetch_sources.py`
Expected: ERRORs — `AttributeError: module 'fetch_sources' has no attribute 'normalize_arxiv'`

- [ ] **Step 3: Add the implementation**

Append to `base-in-reality/assets/fetch_sources.py` (after `build_query_url`, before any `__main__`):

```python
import json
import xml.etree.ElementTree as ET

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"


def _record(source, title, authors, year, venue, url, doi, abstract, src_id=None):
    return {
        "source": source,
        "title": (title or "").strip(),
        "authors": [a for a in (authors or []) if a],
        "year": year,
        "venue": venue or "",
        "id": src_id or url or "",
        "url": url or "",
        "doi": doi,
        "abstract": (abstract or "").strip()[:1000],
    }


def _year_prefix(s):
    s = (s or "")[:4]
    return int(s) if s.isdigit() else None


def normalize_arxiv(raw):
    root = ET.fromstring(raw)
    out = []
    for e in root.findall(f"{_ATOM}entry"):
        url = (e.findtext(f"{_ATOM}id") or "").strip()
        authors = [(a.findtext(f"{_ATOM}name") or "").strip() for a in e.findall(f"{_ATOM}author")]
        out.append(_record(
            "arxiv", e.findtext(f"{_ATOM}title"), authors,
            _year_prefix(e.findtext(f"{_ATOM}published")), "arXiv",
            url, e.findtext(f"{_ARXIV}doi"), e.findtext(f"{_ATOM}summary"), src_id=url))
    return out


def normalize_crossref(raw):
    items = (json.loads(raw).get("message") or {}).get("items") or []
    out = []
    for it in items:
        authors = [f"{a.get('given','')} {a.get('family','')}".strip() for a in it.get("author", [])]
        dp = (it.get("issued") or {}).get("date-parts") or [[None]]
        year = dp[0][0] if dp and dp[0] else None
        doi = it.get("DOI")
        out.append(_record(
            "crossref", (it.get("title") or [""])[0], authors, year,
            (it.get("container-title") or [""])[0],
            it.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
            doi, it.get("abstract"), src_id=doi))
    return out


def _openalex_abstract(inv):
    if not inv:
        return ""
    pos = [(i, w) for w, idxs in inv.items() for i in idxs]
    pos.sort()
    return " ".join(w for _, w in pos)


def normalize_openalex(raw):
    out = []
    for w in json.loads(raw).get("results", []):
        src = (w.get("primary_location") or {}).get("source") or {}
        doi = w.get("doi")
        authors = [(a.get("author") or {}).get("display_name") or "" for a in w.get("authorships", [])]
        out.append(_record(
            "openalex", w.get("title") or w.get("display_name"), authors,
            w.get("publication_year"), src.get("display_name", ""),
            doi or w.get("id") or "", doi,
            _openalex_abstract(w.get("abstract_inverted_index")), src_id=w.get("id")))
    return out


def normalize_pubmed(raw):
    res = json.loads(raw).get("result") or {}
    out = []
    for uid in res.get("uids", []):
        rec = res.get(uid) or {}
        doi = None
        for aid in rec.get("articleids", []):
            if aid.get("idtype") == "doi":
                doi = aid.get("value")
        out.append(_record(
            "pubmed", rec.get("title"), [a.get("name", "") for a in rec.get("authors", [])],
            _year_prefix(rec.get("pubdate")),
            rec.get("fulljournalname") or rec.get("source", ""),
            f"https://pubmed.ncbi.nlm.nih.gov/{uid}/", doi, "", src_id=uid))
    return out


def normalize_semanticscholar(raw):
    out = []
    for p in json.loads(raw).get("data", []):
        ext = p.get("externalIds") or {}
        pid = p.get("paperId")
        doi = ext.get("DOI")
        url = (f"https://www.semanticscholar.org/paper/{pid}" if pid
               else (f"https://doi.org/{doi}" if doi else ""))
        out.append(_record(
            "semanticscholar", p.get("title"), [a.get("name", "") for a in p.get("authors", [])],
            p.get("year"), p.get("venue", ""), url, doi, p.get("abstract"), src_id=pid))
    return out


NORMALIZERS = {
    "arxiv": normalize_arxiv,
    "pubmed": normalize_pubmed,
    "crossref": normalize_crossref,
    "openalex": normalize_openalex,
    "semanticscholar": normalize_semanticscholar,
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd base-in-reality/assets && uv run test_fetch_sources.py`
Expected: `Ran 13 tests` … `OK`

- [ ] **Step 5: Commit**

```bash
git add base-in-reality/assets/fetch_sources.py base-in-reality/assets/test_fetch_sources.py
git commit -m "feat(base-in-reality): add per-source response normalizers"
```

---

### Task B3: Source-fetch helper — network fetch + CLI

**Files:**
- Modify: `base-in-reality/assets/fetch_sources.py`
- Modify: `base-in-reality/assets/test_fetch_sources.py`

**Interfaces:**
- Consumes: `build_query_url`, `NORMALIZERS`, `normalize_pubmed` from B1/B2.
- Produces: `fetch(url, timeout=20) -> bytes`; `fetch_source(source, query, limit=5, sleep=1.0, fetcher=fetch) -> list[dict]` (PubMed does esearch→esummary internally; `fetcher` is injectable for tests); `main(argv=None) -> int` CLI.

- [ ] **Step 1: Add a failing test (injected fetcher, no real network)**

Append to `test_fetch_sources.py` before `if __name__`:

```python
class FetchSource(unittest.TestCase):
    def test_crossref_uses_fetcher(self):
        calls = []

        def fake(url, timeout=20):
            calls.append(url)
            return CROSSREF_JSON

        recs = fs.fetch_source("crossref", "apr", 2, fetcher=fake)
        self.assertEqual(recs[0]["title"], "Reducing Balance APR")
        self.assertIn("api.crossref.org", calls[0])

    def test_pubmed_two_step(self):
        seq = [b'{"esearchresult":{"idlist":["111"]}}', PUBMED_JSON]

        def fake(url, timeout=20):
            return seq.pop(0)

        recs = fs.fetch_source("pubmed", "sepsis", 1, sleep=0, fetcher=fake)
        self.assertEqual(recs[0]["url"], "https://pubmed.ncbi.nlm.nih.gov/111/")

    def test_main_help_exits_zero(self):
        with self.assertRaises(SystemExit) as cm:
            fs.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd base-in-reality/assets && uv run test_fetch_sources.py`
Expected: ERRORs — `AttributeError: module 'fetch_sources' has no attribute 'fetch_source'`

- [ ] **Step 3: Implement fetch + fetch_source + main**

Append to `fetch_sources.py`:

```python
import argparse
import sys
import time
from urllib.request import Request, urlopen

USER_AGENT = (
    "base-in-reality/1.0 (research-grounding audit; "
    "+https://github.com/dhanesh/agent-skills)"
)


def fetch(url, timeout=20):
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted scholarly APIs)
        return resp.read()


def fetch_source(source, query, limit=5, sleep=1.0, fetcher=fetch):
    source = source.lower()
    if source == "pubmed":
        ids = json.loads(fetcher(build_query_url("pubmed", query, limit)))
        idlist = (ids.get("esearchresult") or {}).get("idlist") or []
        if not idlist:
            return []
        if sleep:
            time.sleep(sleep)
        params = urlencode({"db": "pubmed", "retmode": "json", "id": ",".join(idlist)})
        summary = fetcher(
            f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?{params}")
        return normalize_pubmed(summary)
    raw = fetcher(build_query_url(source, query, limit))
    return NORMALIZERS[source](raw)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Query a keyless scholarly source; emit normalized JSON records.")
    p.add_argument("--source", required=True, choices=SOURCES)
    p.add_argument("--query", required=True)
    p.add_argument("--limit", type=int, default=5)
    args = p.parse_args(argv)
    try:
        records = fetch_source(args.source, args.query, args.limit)
    except Exception as e:  # noqa: BLE001 — surface any fetch/parse error as JSON
        json.dump({"error": str(e), "source": args.source}, sys.stderr)
        sys.stderr.write("\n")
        return 2
    json.dump(records, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify pass**

Run: `cd base-in-reality/assets && uv run test_fetch_sources.py`
Expected: `Ran 16 tests` … `OK`

- [ ] **Step 5: (Optional, network) Smoke-test a live query**

Run: `cd base-in-reality/assets && uv run fetch_sources.py --source openalex --query "raft consensus" --limit 2`
Expected: a JSON array of 2 records with `title`/`url` fields. (Skip if offline — not part of the gate.)

- [ ] **Step 6: Commit**

```bash
git add base-in-reality/assets/fetch_sources.py base-in-reality/assets/test_fetch_sources.py
git commit -m "feat(base-in-reality): add network fetch + CLI to source helper"
```

---

### Task B4: Finding JSON schema + validation test

**Files:**
- Create: `base-in-reality/assets/findings.schema.json`
- Test: `base-in-reality/assets/test_findings_schema.py`

**Interfaces:**
- Produces: a draft-07 schema for a single finding object. Subagents (Task B8) and the optional Workflow (B5) emit objects conforming to it.

- [ ] **Step 1: Write the failing test**

Create `base-in-reality/assets/test_findings_schema.py`:

```python
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Structural checks for findings.schema.json — stdlib json only (no jsonschema dep)."""
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class FindingsSchema(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "findings.schema.json")) as f:
            self.schema = json.load(f)

    def test_is_object_schema(self):
        self.assertEqual(self.schema["type"], "object")

    def test_required_fields(self):
        req = set(self.schema["required"])
        self.assertEqual(req, {
            "claim", "layer", "location", "verdict", "severity",
            "citations", "recommended_fix",
        })

    def test_verdict_enum(self):
        self.assertEqual(
            set(self.schema["properties"]["verdict"]["enum"]),
            {"VIOLATION", "DEVIATION", "OUTDATED", "UNCONFIRMED"})

    def test_severity_enum(self):
        self.assertEqual(
            set(self.schema["properties"]["severity"]["enum"]),
            {"critical", "high", "medium", "low"})

    def test_layer_enum(self):
        self.assertEqual(
            set(self.schema["properties"]["layer"]["enum"]),
            {"algo", "arch", "biz"})

    def test_citation_requires_url(self):
        cite = self.schema["properties"]["citations"]["items"]
        self.assertIn("url", cite["required"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd base-in-reality/assets && uv run test_findings_schema.py`
Expected: ERROR — `FileNotFoundError: ... findings.schema.json`

- [ ] **Step 3: Write the schema**

Create `base-in-reality/assets/findings.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "base-in-reality finding",
  "type": "object",
  "additionalProperties": false,
  "required": ["claim", "layer", "location", "verdict", "severity", "citations", "recommended_fix"],
  "properties": {
    "claim": {"type": "string", "description": "Falsifiable assertion about what the code does."},
    "layer": {"enum": ["algo", "arch", "biz"]},
    "location": {"type": "string", "description": "file:line or region the claim was extracted from."},
    "domain": {"type": "string"},
    "verdict": {"enum": ["VIOLATION", "DEVIATION", "OUTDATED", "UNCONFIRMED"]},
    "severity": {"enum": ["critical", "high", "medium", "low"]},
    "recommended_fix": {"type": "string"},
    "citations": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["title", "url", "fetched"],
        "properties": {
          "title": {"type": "string"},
          "url": {"type": "string"},
          "doi": {"type": ["string", "null"]},
          "quote": {"type": "string", "description": "Supporting passage from the source."},
          "fetched": {"type": "boolean", "description": "True only if this URL/DOI was retrieved in-session."},
          "abstract_only": {"type": "boolean", "description": "True if only the abstract (not full text) was available."}
        }
      }
    }
  },
  "allOf": [
    {
      "comment": "Grounding invariant: VIOLATION/DEVIATION require at least one fetched citation.",
      "if": {"properties": {"verdict": {"enum": ["VIOLATION", "DEVIATION"]}}},
      "then": {
        "properties": {
          "citations": {
            "minItems": 1,
            "contains": {"properties": {"fetched": {"const": true}}, "required": ["fetched"]}
          }
        }
      }
    }
  ]
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd base-in-reality/assets && uv run test_findings_schema.py`
Expected: `Ran 6 tests` … `OK`

- [ ] **Step 5: Commit**

```bash
git add base-in-reality/assets/findings.schema.json base-in-reality/assets/test_findings_schema.py
git commit -m "feat(base-in-reality): add finding JSON schema with grounding invariant"
```

---

### Task B5: Optional Workflow accelerator

**Files:**
- Create: `base-in-reality/assets/workflow.mjs`

**Interfaces:**
- Consumes: conceptually, the finding shape from B4 (mirrored inline as a JS schema literal — the Workflow runtime can't read the JSON file).
- Produces: a self-contained Claude Code Workflow script. `.mjs` so `node --check` validates the ESM `export const meta`.

- [ ] **Step 1: Write `base-in-reality/assets/workflow.mjs`**

```javascript
// Optional Claude Code Workflow accelerator for base-in-reality.
// Feed this to the Workflow tool. It fans out per-claim verification and
// adversarial refutation deterministically. The skill works without it
// (see SKILL.md for the portable prompt-driven path).
export const meta = {
  name: 'base-in-reality',
  description: 'Verify repo design claims against fetched scholarly/standards sources, adversarially',
  phases: [
    { title: 'Extract', detail: 'detect domains + extract falsifiable claims' },
    { title: 'Verify', detail: 'ground each claim in fetched sources' },
    { title: 'Refute', detail: 'adversarially attack each surviving finding' },
  ],
}

const FINDING = {
  type: 'object',
  required: ['claim', 'layer', 'location', 'verdict', 'severity', 'citations', 'recommended_fix'],
  properties: {
    claim: { type: 'string' },
    layer: { enum: ['algo', 'arch', 'biz'] },
    location: { type: 'string' },
    verdict: { enum: ['VIOLATION', 'DEVIATION', 'OUTDATED', 'UNCONFIRMED'] },
    severity: { enum: ['critical', 'high', 'medium', 'low'] },
    recommended_fix: { type: 'string' },
    citations: {
      type: 'array',
      items: {
        type: 'object',
        required: ['title', 'url', 'fetched'],
        properties: {
          title: { type: 'string' },
          url: { type: 'string' },
          fetched: { type: 'boolean' },
        },
      },
    },
  },
}

const CLAIMS = {
  type: 'object',
  required: ['domains', 'claims'],
  properties: {
    domains: { type: 'array', items: { type: 'string' } },
    claims: {
      type: 'array',
      items: {
        type: 'object',
        required: ['claim', 'layer', 'location'],
        properties: {
          claim: { type: 'string' },
          layer: { enum: ['algo', 'arch', 'biz'] },
          location: { type: 'string' },
        },
      },
    },
  },
}

const VERDICT = {
  type: 'object',
  required: ['refuted'],
  properties: { refuted: { type: 'boolean' }, reason: { type: 'string' } },
}

const maxClaims = (args && args.maxClaims) || 40

phase('Extract')
const extracted = await agent(
  'Detect the domain(s) of this repository, then extract up to ' + maxClaims +
  ' discrete, FALSIFIABLE claims across three layers (algo|arch|biz): each a checkable ' +
  'assertion about what the code does, with a file:line location. Return domains + claims.',
  { schema: CLAIMS, label: 'extract' },
)

const claims = (extracted && extracted.claims ? extracted.claims : []).slice(0, maxClaims)
log(`extracted ${claims.length} claim(s) across domains: ${(extracted?.domains || []).join(', ')}`)

const results = await pipeline(
  claims,
  (c) => agent(
    'Verify this claim against AUTHORITATIVE sources. Use assets/fetch_sources.py for keyless ' +
    'APIs (arxiv/pubmed/crossref/openalex/semanticscholar), then WebSearch/WebFetch for ' +
    'standards (NIST/RFC/OWASP) and Scholar/JSTOR. You MAY only cite sources you actually ' +
    'fetched this run. If you cannot ground it, verdict=UNCONFIRMED. Claim: ' +
    JSON.stringify(c),
    { schema: FINDING, label: `verify:${c.location}`, phase: 'Verify' },
  ),
  (finding, c) => {
    if (!finding) return null
    if (finding.verdict !== 'VIOLATION' && finding.verdict !== 'DEVIATION') return finding
    return parallel(
      ['correctness', 'citation-applicability', 'severity'].map((lens) => () =>
        agent(
          `Adversarially REFUTE this finding via the ${lens} lens. Default to refuted=true if ` +
          `uncertain. Finding: ${JSON.stringify(finding)}`,
          { schema: VERDICT, label: `refute:${lens}`, phase: 'Refute' },
        ),
      ),
    ).then((votes) => {
      const refutes = votes.filter(Boolean).filter((v) => v.refuted).length
      return refutes >= 2 ? { ...finding, verdict: 'UNCONFIRMED', downgraded: true } : finding
    })
  },
)

return results.filter(Boolean)
```

- [ ] **Step 2: Syntax-check (skip if node absent)**

Run: `command -v node >/dev/null && node --check base-in-reality/assets/workflow.mjs && echo "node-check ok" || echo "node absent — skipped"`
Expected: `node-check ok` (or the skip line on machines without node)

- [ ] **Step 3: Commit**

```bash
git add base-in-reality/assets/workflow.mjs
git commit -m "feat(base-in-reality): add optional Workflow accelerator"
```

---

### Task B6: Reference docs (routing, verdicts, domains)

**Files:**
- Create: `base-in-reality/references/source-routing.md`
- Create: `base-in-reality/references/verdict-rubric.md`
- Create: `base-in-reality/references/domains.md`

**Interfaces:**
- Produces: three reference docs linked from SKILL.md (Task B8). No `{{...}}` tokens, no secrets.

- [ ] **Step 1: Write `references/source-routing.md`**

Author with these required sections and the EXACT routing table (this table is load-bearing — reproduce verbatim):

- `# Source Routing` intro paragraph.
- `## Claim-class → source-class` — this table verbatim:

```markdown
| Claim class | Primary (keyless API) | Standards / authorities (WebSearch/WebFetch) |
|---|---|---|
| Algorithm / data structure / numerics | arxiv, semanticscholar, openalex | — |
| Security / cryptography | semanticscholar | NIST (SP 800-*, FIPS), IETF/RFC, OWASP |
| Medical / clinical / bio | pubmed | FDA, WHO |
| Fintech / lending / payments | crossref, openalex | sector regulator (e.g. RBI), ISO 20022, PCI-DSS |
| Distributed systems / architecture | arxiv, semanticscholar | IETF/RFC, CNCF |
| Statistics / ML methodology | arxiv, semanticscholar, openalex | — |
| General / social-science | openalex, crossref | Google Scholar, JSTOR |
| Fallback (anything) | — | WebSearch + WebFetch |
```

- `## Using fetch_sources.py` — show the invocation `uv run assets/fetch_sources.py --source <s> --query "<q>" --limit <n>`, list the five valid `--source` values, and show the normalized record shape (keys: `source, title, authors, year, venue, id, url, doi, abstract`).
- `## Query construction tips` — bullets: quote exact algorithm/standard names; include the domain term; prefer the standards column for security/compliance claims; widen with synonyms if zero hits.
- `## Etiquette` — note the descriptive User-Agent, the 1s inter-request sleep for PubMed's two-step, and that paywalled full text (JSTOR, ISO) is reachable only via abstracts/summaries → mark such citations `abstract_only`.

- [ ] **Step 2: Write `references/verdict-rubric.md`**

Required sections:
- `# Verdict & Evidence Rubric`.
- `## Verdicts` — table verbatim:

```markdown
| Verdict | Meaning | Evidence bar |
|---|---|---|
| `VIOLATION` | Contradicts an established standard/algorithm/best practice | ≥1 fetched authoritative source + survived refutation |
| `DEVIATION` | Departs from the recommended norm but may be defensible | ≥1 fetched source + survived refutation |
| `OUTDATED` | Matches a superseded practice | a cited newer standard/result that replaced it |
| `UNCONFIRMED` | Could not be grounded within budget | none required; this is the default ceiling for ungrounded claims |
```

- `## Severity` — table verbatim:

```markdown
| Severity | When |
|---|---|
| `critical` | Security/compliance/financial-correctness failure with real-world harm (e.g. broken crypto, miscomputed APR, PHI mishandling) |
| `high` | Clear violation of an established norm with material impact, no immediate catastrophe |
| `medium` | Defensible deviation or outdated practice worth revisiting |
| `low` | Minor or stylistic departure from common practice |
```

- `## Grounding rule (invariant)` — state verbatim: "Every finding's evidence must be a URL/DOI the agent fetched **this session**. A claim that cannot be grounded is reported as `UNCONFIRMED` and **never** as `VIOLATION`/`DEVIATION`. No fabricated, remembered, or unverified citations."
- `## Refutation protocol` — describe the adversarial pass: for each candidate `VIOLATION`/`DEVIATION`, run ≥3 independent refuters across distinct lenses (correctness, citation-applicability, severity), each defaulting to `refuted=true` when uncertain; downgrade to `UNCONFIRMED` if ≥2 of 3 refute; record the refutation outcome.

- [ ] **Step 3: Write `references/domains.md`**

Required sections:
- `# Domains & Norms Surface`.
- `## Detection signals` — table mapping domain → signals:

```markdown
| Domain | Detection signals |
|---|---|
| fintech / lending / payments | interest/APR/EMI/KYC code, payment SDKs, ledger/loan schemas, RBI/PCI references |
| ml / data-science | numpy/torch/sklearn, train/eval splits, model/embedding code, notebooks |
| security / crypto | hashing/JWT/TLS/key-management code, auth flows, crypto libraries |
| healthcare | HL7/FHIR, PHI fields, clinical terminology, HIPAA references |
| distributed-systems | service mesh, queues, consensus, retries/timeouts, gRPC/REST fan-out |
| data-engineering | ETL/DAGs, warehouses, schema migrations, streaming |
| web | HTTP frameworks, sessions, CORS, templating, frontend bundles |
```

- `## Norms surface per domain` — for each domain above, one line naming the authorities/standards in scope (e.g. fintech → RBI circulars, ISO 20022, PCI-DSS; security → NIST SP 800-*, FIPS, OWASP, relevant RFCs; healthcare → HIPAA, HL7/FHIR, FDA; etc.).
- `## Override` — note that `--domain <x>` overrides auto-detection, and the detected domain map is surfaced at the top of every report for the reader to sanity-check.

- [ ] **Step 4: Verify the docs have their load-bearing anchors**

Run:
```bash
grep -q "Claim-class" base-in-reality/references/source-routing.md && \
grep -q "UNCONFIRMED" base-in-reality/references/verdict-rubric.md && \
grep -q "Norms surface" base-in-reality/references/domains.md && echo "refs ok"
```
Expected: `refs ok`

- [ ] **Step 5: Commit**

```bash
git add base-in-reality/references/
git commit -m "docs(base-in-reality): add source-routing, verdict-rubric, domains references"
```

---

### Task B7: Runtime report skeleton

**Files:**
- Create: `base-in-reality/assets/report-skeleton.md`

**Interfaces:**
- Produces: the Markdown skeleton the skill fills at audit time. Uses `<angle-bracket>` fill markers ONLY (never `{{UPPER_SNAKE}}`, to keep `validate-skill.sh` bijection a SKIP).

- [ ] **Step 1: Write `base-in-reality/assets/report-skeleton.md`**

```markdown
# base-in-reality audit — <repo-name>

**Date:** <YYYY-MM-DD> · **Scope:** <whole repo | diff since `<ref>` | layer:<algo|arch|biz>> · **Max claims:** <N>

## Executive summary

<2-4 sentences: how many claims checked, how many confirmed findings by severity, the single most important issue.>

## Domain map

- **Detected domain(s):** <domain, domain, ...>
- **Norms surface in scope:** <authorities/standards consulted>
- <sanity-check note if detection is uncertain>

## Findings

> Each finding below is grounded in at least one source fetched during this audit.
> Ungrounded claims appear as `UNCONFIRMED`.

### [<severity>] <verdict> — <short title>

- **Claim:** <falsifiable claim>
- **Location:** `<file:line>`
- **Layer:** <algo|arch|biz>
- **Assessment:** <why it deviates/violates, in one short paragraph>
- **Citations:**
  - <Title> — <url-or-doi> <(abstract_only) if applicable> — "<supporting quote>"
- **Recommended fix:** <concrete action>

<repeat the finding block per finding, ordered by severity then layer>

## Sources appendix

<numbered list of every distinct source fetched, with title, authors, year, venue, url/doi>

## Dropped-claims log

<if --max-claims truncated extraction: list what was NOT verified, so coverage is never overstated. Otherwise: "None — all extracted claims were verified.">
```

- [ ] **Step 2: Verify no install-time tokens leaked in**

Run: `grep -c '{{[A-Z0-9_]*}}' base-in-reality/assets/report-skeleton.md`
Expected: `0`

- [ ] **Step 3: Commit**

```bash
git add base-in-reality/assets/report-skeleton.md
git commit -m "feat(base-in-reality): add runtime report skeleton"
```

---

### Task B8: SKILL.md + README.md (the orchestration) → gate the skill

**Files:**
- Create: `base-in-reality/SKILL.md`
- Create: `base-in-reality/README.md`

**Interfaces:**
- Consumes: every `base-in-reality/references/*` and `base-in-reality/assets/*` file (all must already exist — they do, per B1–B7) so `validate-skill.sh` finds no dangling references.
- Produces: the skill entrypoint. After this task `make gate-skill SKILL=base-in-reality` passes.

- [ ] **Step 1: Write `base-in-reality/SKILL.md`**

```markdown
---
name: base-in-reality
description: "Validate a repository's codebase, architecture, and business logic against real-world knowledge from authoritative sources (arxiv, PubMed, Google Scholar, JSTOR, OpenAlex, Crossref, Semantic Scholar) plus standards bodies (NIST, IETF/RFC, OWASP, ISO, sector regulators). Use when you want a research-grounded audit that flags algorithms, architectural choices, or business rules that violate established norms, standards, or best practices — each finding tied to a real, fetched citation. Read-only by default; emits a severity-graded cited report. Extracts falsifiable claims across algo/arch/biz layers, routes each to the right source class, verifies against fetched evidence, and adversarially refutes before reporting. Not a linter, SAST, or CVE scanner — it reasons about norms, not syntax."
x-spec-version: 1.0
# license: <SPDX-id>   # set before publishing — see references/publishing.md
---

# base-in-reality

A read-only, research-grounded repository audit. It answers one question for each
notable design decision in a repo: *does this violate an established norm, standard,
algorithm, or best practice — provably, against a real source?*

The defining rule: **no fabricated authority.** Every finding is tied to a source the
agent actually fetched in-session. Ungrounded claims are reported as `UNCONFIRMED`, never
as violations.

## When to use

Use when you want to sanity-check a codebase, architecture, or business logic against the
state of the art and against domain standards — e.g. "is our APR calculation correct per
lending norms?", "does our crypto follow NIST?", "is this consensus approach sound?",
"does our ML eval avoid known leakage pitfalls?". Works on any repo; adapts to its
domain(s).

## Invariants (do not violate)

1. **Read-only by default.** Never edit code. The only write path is `--annotate`, which
   inserts comment markers only — never logic.
2. **No fabricated citations.** Cite only URLs/DOIs fetched this session. Ungrounded ⇒
   `UNCONFIRMED`. See `references/verdict-rubric.md`.
3. **Adversarial gate.** No `VIOLATION`/`DEVIATION` is reported without surviving a
   refutation pass.
4. **No silent truncation.** If `--max-claims` caps extraction, list what was dropped in
   the report's Dropped-claims log.

## Flags

- `--annotate` — also insert `# BASE-IN-REALITY[<sev>]: <one-line> — see <report>` markers
  at finding locations (the only code-touching mode). Default: off.
- `--layer algo|arch|biz` — restrict to one layer. Default: all three.
- `--domain <x>` — override auto-detected domain. Default: auto.
- `--since <ref>` — audit only the diff vs `<ref>`. Default: whole repo.
- `--max-claims N` — cap claims per run. Default: 40.

## Procedure

Run these six stages. Dispatch subagents where noted (fan-out). On Claude Code you may
instead feed `assets/workflow.mjs` to the Workflow tool as a deterministic accelerator —
it performs stages 2/4/5 with parallel fan-out; the steps below are the portable path.

1. **Scope & domain detection.** Survey languages, dependency manifests, directory layout,
   README/docs, DB schemas/migrations, config. Classify the domain(s) using
   `references/domains.md` detection signals; fix the norms surface. Honor `--domain` and
   `--since`. Produce the domain map.

2. **Claim extraction (fan-out).** Dispatch subagents over repo regions. Each returns
   discrete, FALSIFIABLE claims tagged `{layer (algo|arch|biz), location (file:line),
   confidence, domain-tag}`. A claim is a checkable assertion about what the code does
   (e.g. "uses MD5 for password hashing", "synchronous fan-out to 12 services on the
   request path", "APR computed as simple interest"). Merge and dedup. Enforce
   `--max-claims`; record any surplus for the Dropped-claims log.

3. **Source routing.** For each claim, pick a ranked source plan from
   `references/source-routing.md` (algorithm→arxiv/Semantic Scholar; security→NIST/RFC/
   OWASP; medical→PubMed; fintech→regulator+standards; general→Scholar/JSTOR/OpenAlex).

4. **Verify (fan-out, grounded).** Per claim, dispatch a subagent that queries keyless APIs
   first via `assets/fetch_sources.py`
   (`uv run assets/fetch_sources.py --source <s> --query "<q>" --limit <n>`), then
   WebSearch/WebFetch for standards and paywalled sources. It must hold ≥1 fetched source
   before asserting anything stronger than `UNCONFIRMED`. Output a finding conforming to
   `assets/findings.schema.json`: `verdict ∈ {VIOLATION,DEVIATION,OUTDATED,UNCONFIRMED}`,
   `severity ∈ {critical,high,medium,low}`, `citations[]` (each with the fetched URL/DOI and
   a supporting quote; mark `abstract_only` where only the abstract was available),
   `recommended_fix`.

5. **Refute (fan-out, adversarial).** For each candidate `VIOLATION`/`DEVIATION`, dispatch
   independent refuters across distinct lenses (correctness, citation-applicability,
   severity) per `references/verdict-rubric.md`. Each defaults to skeptical. Downgrade to
   `UNCONFIRMED` when ≥2 of 3 refute.

6. **Synthesize.** Fill `assets/report-skeleton.md` and write it to
   `docs/base-in-reality/<YYYY-MM-DD>-audit.md`: executive summary, domain map, findings
   (ordered by severity then layer), sources appendix, dropped-claims log. If `--annotate`,
   insert the comment markers at each finding's location.

## References

- `references/source-routing.md` — claim-class → source-class table, API usage, etiquette.
- `references/verdict-rubric.md` — verdict taxonomy, severity, grounding rule, refutation.
- `references/domains.md` — domain-detection signals and per-domain norms surface.

## Assets

- `assets/fetch_sources.py` — keyless scholarly-source query helper (stdlib; `uv run`).
- `assets/findings.schema.json` — the finding schema verification subagents emit.
- `assets/report-skeleton.md` — the report template filled in stage 6.
- `assets/workflow.mjs` — optional Claude Code Workflow accelerator.

## Edge cases

- **Uneven source coverage.** arxiv/PubMed are strong; JSTOR/ISO are reachable only via
  abstracts — mark those citations `abstract_only` so evidence depth is visible.
- **Domain misdetection.** Surface the domain map at the top of the report; `--domain` is
  the override.
- **Large repos.** Deep mode fans out twice per claim (verify + refute) and is
  token-heavy. Scope with `--layer`, `--since`, or a lower `--max-claims`.
- **"Best practice" is contested.** Prefer `DEVIATION` over `VIOLATION` for defensible
  departures; the refutation pass exists to stop the skill asserting one true way.
```

VERIFY the `description` is ≤1024 characters before continuing:

Run: `awk '/^description:/{sub(/^description: *"/,"");sub(/" *$/,"");print length($0)}' base-in-reality/SKILL.md`
Expected: a number ≤ 1024. (If it exceeds, trim the description text and re-check.)

- [ ] **Step 2: Write `base-in-reality/README.md`**

```markdown
# base-in-reality

A read-only, research-grounded repository audit skill. It validates a repo's **codebase,
architecture, and business logic** against real-world knowledge — academic literature
(arxiv, PubMed, Google Scholar, JSTOR, OpenAlex, Crossref, Semantic Scholar) and standards
bodies (NIST, IETF/RFC, OWASP, ISO, sector regulators) — and flags anything that violates
an established norm, standard, algorithm, or best practice.

Every finding is tied to a source the agent actually fetched. Ungrounded claims are
reported as `UNCONFIRMED`, never as violations — there is no fabricated authority.

## Install

```bash
npx skills add dhanesh/agent-skills --skill base-in-reality
```

## What it does

1. Detects the repo's domain(s) and the standards surface that applies.
2. Extracts falsifiable claims across three layers: algorithm/code, architecture, business logic.
3. Routes each claim to the right authoritative source class.
4. Verifies against fetched evidence, then adversarially refutes before reporting.
5. Emits a severity-graded, cited Markdown report (and, with `--annotate`, inline markers).

## Output

`docs/base-in-reality/<YYYY-MM-DD>-audit.md` — executive summary, domain map, findings
(claim · location · verdict · severity · citations · fix), sources appendix, and a
dropped-claims log. Read-only unless you pass `--annotate`.

See `SKILL.md` for the full procedure, flags, and invariants.
```

- [ ] **Step 3: Gate the skill**

Run: `make gate-skill SKILL=base-in-reality`
Expected: `VALIDATION_RESULT: PASS` and `SCAN_RESULT: PASS — ... base-in-reality` (no `DRY_RUN_RESULT` line — base-in-reality has no PARAMETERS.md, so dry-run is correctly skipped).

If `validate` reports a dangling reference, the named file is missing — create it per the
relevant Part-B task before proceeding.

- [ ] **Step 4: Commit**

```bash
git add base-in-reality/SKILL.md base-in-reality/README.md
git commit -m "feat(base-in-reality): add SKILL.md orchestration and README"
```

---

### Task B9: Repo integration + full gate + finish

**Files:**
- Modify: `README.md` (repo root)

**Interfaces:**
- Consumes: the green per-skill gate from B8 and Part A's `make gate`.
- Produces: the repo README advertises `base-in-reality`; the whole repo passes `make gate`.

- [ ] **Step 1: Add the install example to the repo README**

In `README.md`, under the `## Install` examples list, add:

```bash
npx skills add dhanesh/agent-skills --skill base-in-reality
```

- [ ] **Step 2: Add the skills-table row**

In the `## Skills` table in `README.md`, add this row (keep column alignment with existing rows):

```markdown
| [`base-in-reality`](base-in-reality/) | Read-only, research-grounded repo audit — extracts falsifiable claims across algorithm/architecture/business-logic layers, routes each to authoritative sources (arxiv, PubMed, Scholar, JSTOR, OpenAlex, Crossref, Semantic Scholar + NIST/RFC/OWASP/ISO/regulators), verifies against fetched evidence, and adversarially refutes before emitting a severity-graded cited report. No fabricated citations: ungrounded claims are reported as `UNCONFIRMED`. |
```

- [ ] **Step 3: Run the full repo gate (everything must be green)**

Run: `make gate; echo "rc=$?"`
Expected: every skill (including `base-in-reality`) shows `VALIDATION_RESULT: PASS` and `SCAN_RESULT: PASS`; final `rc=0`.

- [ ] **Step 4: Run the Python suites once more (regression)**

Run: `cd base-in-reality/assets && uv run test_fetch_sources.py && uv run test_findings_schema.py && cd -`
Expected: both end in `OK`.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: list base-in-reality in repo README"
```

- [ ] **Step 6: Finish the branch**

Invoke the `superpowers:finishing-a-development-branch` skill to decide how to integrate
(PR vs merge). The branch is `feat/base-in-reality-skill`. The PR description should note
both deliverables: the `base-in-reality` skill and the repo-wide shared gates + CI.

---

## Self-Review notes (for the implementer)

- **Spec coverage:** Stages 1-6, all four invariants, all five flags, sourcing, verdict
  taxonomy, output (report + optional `--annotate`), and the optional Workflow are all
  realized in Tasks B1-B8. The user's added request (shared gates on PRs + Makefile) is
  Part A (A1-A4).
- **Why no `dry-run-replay` for base-in-reality:** it has no `PARAMETERS.md` by design
  (no install-time substitution). `dry-run-replay.sh` hard-errors without one, so the
  Makefile and `make gate-skill` correctly skip it for that skill. This is not a gap.
- **Type consistency:** the record dict keys (`source,title,authors,year,venue,id,url,doi,
  abstract`) are produced by `_record` (B2) and asserted in tests (B2); the finding fields
  (`claim,layer,location,verdict,severity,citations,recommended_fix`) match across the
  schema (B4), the Workflow schema literal (B5), the verdict rubric (B6), the report
  skeleton (B7), and SKILL.md stage 4 (B8).
- **Gate-green precondition:** Part A Task A3 fixes the two existing failing skills BEFORE
  A4 enables CI, so `main` never goes red.
```
