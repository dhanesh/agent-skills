# skill-catalog: keep the README catalog and the call graph in step with the skills

- id: skill-catalog
- proven: no
- anchors: README.md, docs/skill-graph/graph.json, scripts/gates/readme-catalog.sh, scripts/gates/skill-graph.sh, scripts/gates/skill_graph.py, Makefile:skill-graph-check

## What it is

Every top-level directory with a `SKILL.md` is a skill. Two gates keep the catalog true:

- `readme-catalog.sh` checks that the root `README.md` has an install line
  (`npx skills add ... --skill <name>`) and a Skills-table row for every skill, and
  names nothing that is not a skill.
- `skill-graph.sh` checks that the "who calls whom" diagrams in `README.md` and in each
  `<skill>/README.md` match what `skill_graph.py` renders from
  `docs/skill-graph/graph.json`, that every skill is a graph node, and that each
  evidence quote is still in its file.

`make gate` runs both once per run.

## How to reach it

- `make readme`, or `sh scripts/gates/readme-catalog.sh [root]`. Last line:
  `README_CATALOG_RESULT: PASS|FAIL`.
- `make skill-graph-check`, or `sh scripts/gates/skill-graph.sh [root]`. Last line:
  `SKILL_GRAPH_RESULT: PASS` or `SKILL_GRAPH_RESULT: FAIL (<n>)`.
- `make skill-graph` writes the diagram blocks from `graph.json`, then checks.

## Drive it

On the checkout under test:

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name sc-readme -- make readme
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name sc-graph -- make skill-graph-check
```

Expect exit 0 from both, `README_CATALOG_RESULT: PASS` and `SKILL_GRAPH_RESULT: PASS`.

Then add a skill that nobody listed, in a copy of the committed tree, never in `$WT`:

```sh
CAT="$WT/.verify-run/$INSTANCE/cat"
mkdir -p "$CAT" && git archive HEAD | tar -x -C "$CAT"
mkdir -p "$CAT/zz-unlisted" && cp "$CAT/clean-code/SKILL.md" "$CAT/zz-unlisted/SKILL.md"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name sc-readme-bad -- \
  sh scripts/gates/readme-catalog.sh "$CAT"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name sc-graph-bad -- \
  sh scripts/gates/skill-graph.sh "$CAT"
```

- `sc-readme-bad`: exit 1. It prints
  `FAIL: zz-unlisted has no install line (add: npx skills add dhanesh/agent-skills --skill zz-unlisted)`,
  `FAIL: zz-unlisted has no Skills-table row (...)` and `README_CATALOG_RESULT: FAIL`.
- `sc-graph-bad`: exit 1. It prints
  `FAIL: skill zz-unlisted is missing from docs/skill-graph/graph.json (add a node)` and
  `SKILL_GRAPH_RESULT: FAIL (1)`.

Before you add `zz-unlisted`, both scripts pass on the copy, so the failure comes from
the new directory alone.

## Proof

The four captures: both gates pass on the checkout and both fail on the copy with the
unlisted skill, each naming `zz-unlisted`. The check commands write nothing: `git status
--short` in `$WT` shows no change outside `.verify-run/`. Only `make skill-graph` (not
driven here) writes README files.

## Gotchas

- `git archive HEAD` copies the committed head, not uncommitted edits. Commit first, or
  the copy tests older text.
- Do not run `make skill-graph` to "check": it rewrites the diagram blocks.
- The gates cannot see a new call between skills that nobody added to `graph.json`.
