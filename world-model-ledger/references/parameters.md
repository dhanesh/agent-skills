# Parameters & invocation reference

world-model-ledger has two kinds of invocation: **install** (one-time setup) and **operate**
(`--seed` / `--prune` on an already-installed model). Detect the install state first, then route
by flag — never ask about scope for an operate action on an existing install.

## Detecting an existing install

| Scope | Signal |
|---|---|
| **Global** | `~/.claude/world-model-ledger/wm.py` exists (hooks in `~/.claude/settings.json`) |
| **Project** | `<repo>/wm.py` + `<repo>/world_model.py` exist (hooks in `<repo>/.claude/settings.json`) |

Set `WM` to the project-local dir if `./wm.py` exists, else `~/.claude/world-model-ledger`.

## Installer flags — `scripts/install.sh [TARGET] [flags]`

| Flag | What it does | When to use |
|---|---|---|
| *(none)* | Project install into the current repo (or `TARGET` dir): copy store+CLI+hooks, merge the four hooks into `<repo>/.claude/settings.json`, gitignore `.world-model/`, run the install-gate test suite. | First-time setup for one repo. |
| `--global` / `-g` | Global install into `~/.claude/world-model-ledger/`; hooks registered in `~/.claude/settings.json` with absolute paths so they fire in **every** project. Each repo still gets its own `.world-model/`. | First-time setup for all repos at once. |
| `--with-constraints` | Also load the optional starter constraint pack (weak-hash, eval/exec, single-owner) — low-confidence priors, off by default. | You want a few generic rules seeded to demo contradiction detection. |
| `--seed` | Build the **current repo's** model now (files + structural edges, observation-only). If the skill is **already installed**, this is an *operate* action — it seeds the repo and exits, **no re-install and no scope question**. If not installed, it installs first, then seeds. | Avoid a cold start; refresh after adding files. Safe to re-run (idempotent). |
| `--prune` | Like `--seed`, but also soft-invalidate build-origin edges for files you have **deleted or renamed** (never hard-deletes; never touches agent-observed/validated facts). | After moving/removing files, to converge the model on the current tree. |
| `TARGET` | A directory path → project-install target (default: current directory). Ignored under `--global`. | Install into a repo other than the cwd. |
| `-h` / `--help` | Print usage and exit. | — |

**Routing rule:** `--seed` / `--prune` are *operate* actions. When the model is already
installed (global or project), run them directly against the current repo — do **not** re-run
the installer or ask which scope to use. Only a genuine first-time install chooses scope.

## `wm` CLI flags — `python3 "$WM/wm.py" <command> [flags]`

The operate actions above are thin wrappers over the CLI; you can also call it directly.

| Command / flag | What it does |
|---|---|
| `build [path]` | Repo-wide language-aware seed (files + structural edges), observation-only, idempotent. |
| `build --max-files N` | Cap files scanned; the surplus is logged, never silently dropped (default 5000). |
| `build --prune` | Also soft-invalidate build-origin edges for vanished files (conservative; agent facts protected). |
| `observe / validate / refute / map / constraint` | Record facts (see `references/capture.md`). Triples are ontology-checked before insert; a violation exits 2 with a structured error naming the allowed verbs (see `references/ontology.md`). |
| `ontology [--add P --domain K,K --range K,K]` | List the predicate vocabulary, or deliberately extend it (persists to `ontology.json` next to the DB). |
| `contradictions [--open] [--touching P]` · `resolve <id> --as …` | Review + resolve contradictions. |
| `precall <path…>` · `query --touching P` | What the pre-call hook surfaces for a file/symbol. |
| `exec --command "<cmd>" [--exit-code N]` | Observe an execution → runtime edges + verifier oracle. `--from-hook` reads the PostToolUse JSON from stdin (how the hook calls it); `--digest PATH` refreshes the digest. |
| `stats` · `consolidate` · `digest` · `export` | Inspect / re-derive / dump the model. |
| `--db PATH` (global) | Override the DB path (default `.world-model/model.db`, or `$WM_DB`). |

## Environment variables

| Var | Default | What it does |
|---|---|---|
| `WM_DB` | `.world-model/model.db` | DB path when `--db` is not passed. |
| `WM_VERIFIER_RE` | see `DEFAULT_VERIFIER_RE` in `world_model.py` | Regex (case-insensitive) deciding which Bash commands count as **verifiers** whose exit status writes `test` oracle evidence. Deliberately build-tool-agnostic — matches `test`/`spec`/`check`/`lint`/`pytest`/`shellcheck`/… so `make test` promotes but `make build` does not. A bad pattern falls back to the default (never breaks the hook). |

(There is no top-level `PARAMETERS.md` because this repo reserves that filename for
template-placeholder bijection, which this skill has none of; this reference serves the same
"what does each parameter do" purpose without tripping the skill-validation gate.)
</content>
