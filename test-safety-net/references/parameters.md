# `rank_risk.py` — parameters and output shape

Invoke as (see the `$SKILL_DIR` preamble in `SKILL.md`):

```sh
python3 "$SKILL_DIR/assets/rank_risk.py" <repo> [--top-n N] [--since "6 months ago"] \
  [--stack python|node]
```

Stdlib + git only. No network, no third-party packages, offline, deterministic — byte-identical
JSON across repeated runs on an unchanged repo.

## Arguments

| Argument | Kind | Default | Meaning |
|---|---|---|---|
| `repo` | positional, required | — | Path to the repository to analyse. Must be a directory. |
| `--top-n` | flag | `10` | How many units to net into `ranked` in this pass. Everything past this cutoff lands in `remainder`, not discarded — the next run picks up there. |
| `--since` | flag | `"6 months ago"` | Churn window, any `git --since` expression. With no git history (or no `.git`), churn degrades to `0` for every unit rather than raising. When `repo` is a *subdirectory* of a git repo, churn is scoped to that subtree and a `note:` line naming the scope is written to **stderr** — the JSON on stdout is unchanged. |
| `--stack` | flag | detected | Force the stack instead of detecting it. Detection scores each stack — the non-test source files it claims, `(files + 5) * 2` when a manifest at or near the root declares that stack — and the highest score wins. A manifest *scales* a claim rather than adding to it, so a root `package.json` holding a `prettier` script cannot outvote a forty-file Python majority, and a repo declaring both stacks is decided by its file counts; a manifest nested under `assets/`, `templates/`, `fixtures/`, `examples/` or `testdata/` describes a *template* and scores nothing. **Every run writes the verdict and the scores to stderr** (`note: stack=python (evidence: python=51, node=17)`). When the top two scores are within 10% of each other the run **exits 2 without producing JSON** and names the flag: a tie is reported, never guessed. |

## Output — top-level keys

| Key | Shape | Meaning |
|---|---|---|
| `root` | string | Absolute path to the analysed repo. |
| `stack` | string | Which stack produced this report — `"python"` or `"node"`. Both are complete end to end: each ships a runtime guard, so each ranks AND writes. A repo neither stack claims still reports `"python"` here — that is the default, not a verdict; the `note:` line on stderr says `no stack claims <repo>` and `units_discovered` is `0` (`references/stacks.md`). |
| `discovery` | string | Which reader produced the units — `"precise"` (a real parser) or `"heuristic"` (a text reader). Python is always `precise`: `ast` is stdlib and cannot go missing. Node is `precise` only where the repo ships its own `node_modules/typescript`, and falls back to `heuristic` — reporting it here — when it does not, when `node` cannot be run, or when the toolchain times out, exits non-zero or prints something unreadable (a note on stderr names the reason in the last three cases). **A `heuristic` run finds fewer units than a `precise` one on the same tree**, so two runs are only comparable when this key agrees. The toolchain is never downloaded: the precise path `require`s a compiler already on disk or declines. This key exists because the multi-stack design requires the report to name which discovery path ran, so **carry it into the report you hand back**, not just the JSON. |
| `window` | string | The `--since` value actually used. |
| `units_discovered` | integer | Total units found, before triage or coverage filtering — module-level `def`/`class` for python, top-level `export`ed functions and classes for node. Vendor and build directories never contribute: `node_modules`, `dist`, `build`, `vendor`, `.venv` and their siblings are skipped whole, so a repo's dependencies are never ranked as its own code. |
| `ranked` | array of rows | The top `--top-n` netted units, highest score first, ties broken by `id`. **This is what you show the user at the confirmation gate.** |
| `remainder` | array of rows | Netted units past the `--top-n` cutoff, same sort order. Where the next run resumes. |
| `not_netted` | array of rows | Units at Tier 3 or Tier 4 — sorted by `(tier, id)`. These never get a test written; they are the seam/refactor list for `clean-code`. |
| `covered` | array of strings | Unit `id`s some existing test file already appears to exercise: the test names the unit AND plausibly names its module. Where two source files share a basename (`app/utils.py`, `lib/utils.py`), a bare `utils` is not enough — the evidence must name the file's *path* (`app.utils` or `app/utils`), so an untested unit is never credited to its namesake's test. The cost is the opposite direction: a repo-root file colliding with a packaged one has no qualifier to offer and reads as uncovered. These never entered `ranked`/`remainder`/`not_netted` in the first place — this key is a flat index across ALL discovered units, not a fourth partition; a unit can appear here and also in `not_netted` if it happens to be both. |

## Output — a ranked/remainder/not_netted row

| Field | Meaning |
|---|---|
| `id` | `"<path>::<name>"` — the unit's stable identifier. |
| `path` | Repo-relative source path. |
| `name` | The function or class name. |
| `lineno` | Line the unit's declaration starts on. |
| `kind` | `"function"` or `"class"`. |
| `churn` | Commits touching this unit's file within the `--since` window. Exact, from git log. |
| `inbound_refs` | Approximate blast radius: identifier occurrences of this unit's name, credited to its own file (excluding the definition line) plus other files that plausibly reference its module. **Static approximation, not a call graph** — it cannot tell a call from a comment, and a re-export can hide real reach. Qualified forms are exact (`mod.name` counts, `buf.name` does not); a **bare** occurrence in a file that references the module still counts even when it means something else — a same-named local, or one imported from a different module. Show it alongside `churn` in the report so a human can see which signal drove the placement (see "Reading a row's score" below). |
| `inbound_approx` | Always `true` in this version — labels `inbound_refs` as approximate wherever it surfaces. |
| `tier` | `1`–`4`, per `references/triage.md`. |
| `tier_reason` | Human-readable reason for the tier. For a Tier 2 row this names only ONE controllable group (alphabetically first) — see the multi-group note in `references/triage.md` before treating it as the complete list of what to fake. |
| `score` | `0.0` for anything Tier ≥ 3 or already covered; otherwise `(normalised(churn)+1) × (normalised(inbound_refs)+1) − 1`. Normalisation divides by the max value across all discovered units. |
| `covered_by` | Path to the test file that appears to already cover this unit, or `null`. |

## Reading a row's score — the floor is deliberate

A unit with strong churn and zero *static* inbound references can still score close to the
maximum and outrank a unit with moderate churn and real references. This is not a bug to tune
away: a unit with zero references the approximate counter can see is very often an **entry
point** — a CLI dispatcher, a `main`, a plugin hook invoked by name or registry — that the static
scanner structurally cannot see reach for, not evidence that nothing depends on it. Ranking the
repo's hottest file highly under that ambiguity is the intended default. Each row still carries
its raw `churn` and `inbound_refs` so a human reviewing the ranked list can see which of the two
signals actually drove a given placement, rather than trusting the composite score blindly.

## Exit behaviour

Exits `2` with a stderr message if `repo` is not a directory. Otherwise prints the JSON object
above to stdout (`indent=2, sort_keys=True`) and exits `0` — including when the repo has no git
history or zero discoverable units; an empty/degraded result is not an error.
