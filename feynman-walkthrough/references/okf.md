# OKF — persistent knowledge in the Open Knowledge Format

The skill's persistence layer: explained subjects are stored as an **Open Knowledge
Format (OKF) bundle** — Google's open, vendor-neutral spec for agent-readable knowledge
(a directory of markdown concept files with YAML frontmatter, readable by humans without
tooling and by any agent without an SDK). On top of plain OKF, each subject's explainer
concept **pins a fingerprint of every source** it was built from, so a later session can
tell whether the codebase, document, or topic moved since the explainer was written and
refresh only what drifted.

Load this file in a filesystem environment when persisting an explainer (workflow
step 6) or when a session opens on a subject that might already be in the bundle.

## Spec adherence

This skill targets **OKF v0.1**:
<https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md>.
The conformance points, all implemented by `assets/okf.py`:

- every non-reserved `.md` file is a **concept** with YAML frontmatter carrying a
  non-empty `type` (this skill uses `Explainer`, `FAQ`, and `ReviewSchedule`);
- `index.md` and `log.md` are **reserved**: index files are link listings for
  progressive disclosure (no frontmatter — except the bundle root, the one place
  frontmatter is permitted, where `okf_version: "0.1"` is declared), and `log.md` is
  the update history with `## YYYY-MM-DD` headings, newest first;
- recommended fields (`title`, `description`, `resource`, `tags`, `timestamp`) are
  populated; the source pins live in a producer-defined `sources:` key, which the spec
  explicitly allows (consumers must preserve unknown keys).

**Keep the format current:** OKF v0.1 is an early spec and will evolve. When you have
network access and are about to create or migrate a bundle, check the spec at the URL
above; if it has moved past the version in `okf.py`'s `OKF_SPEC_VERSION`, update the
script, its templates, and this file to the new version (and note the migration in the
bundle's `log.md`) rather than emitting a stale dialect.

## Bundle layout

```
knowledge/                    # bundle root; one per project or per user
  index.md                    # okf_version + link list of subjects (reserved)
  log.md                      # update history, newest first (reserved)
  <subject-slug>/
    index.md                  # links to the subject's concepts (reserved)
    explainer.md              # concept type: Explainer — canonical reference + source pins
    faq.md                    # concept type: FAQ — session Q&A, appended over time
    recall.md                 # concept type: ReviewSchedule — only if recall track opted in
```

## Examples

Bundle-root `index.md` (the only index with frontmatter):

```markdown
---
okf_version: "0.1"
---

# Subjects

* [Ingest pipeline](ingest-pipeline/) - reference explainer and FAQ
* [Raft consensus](raft-consensus/) - reference explainer and FAQ
```

A subject's `explainer.md` frontmatter — recommended OKF fields plus the pins:

```markdown
---
type: Explainer
title: Ingest pipeline
description: Reference explainer for the ingest pipeline
resource: /home/user/ingest
tags: [walkthrough, reference]
timestamp: 2026-07-11T00:00:00Z
created: 2026-07-11
sources:
- type: git
  locator: /home/user/ingest
  fingerprint: 4f2a9c8 # full git HEAD sha in practice; shortened for the example
  pinned: 2026-07-11
- type: external
  locator: https://example.com/design-doc
  fingerprint: null
  pinned: 2026-07-11
---

# Ingest pipeline

## In one paragraph
…
```

`log.md` after a create and a later refresh:

```markdown
# Directory Update Log

## 2026-07-18
* **Update**: Re-pinned sources for [Ingest pipeline](/ingest-pipeline/explainer.md).

## 2026-07-11
* **Creation**: Established [Ingest pipeline](/ingest-pipeline/explainer.md) (2 source(s) pinned).
```

## The tool

`assets/okf.py` (stdlib-only, tested by `assets/test_okf.py`):

```bash
python3 okf.py init "ingest pipeline" --root knowledge --source /path/to/repo
python3 okf.py status ingest-pipeline --root knowledge   # FRESH / STALE / UNKNOWN per source
python3 okf.py pin ingest-pipeline --root knowledge      # re-fingerprint after refreshing
python3 okf.py list --root knowledge
```

Fingerprints: git sources pin `HEAD` (with a `+dirty` marker for uncommitted changes);
files and directories pin a sha256; `external` sources (URLs, plain topics) can't be
fingerprinted locally and report UNKNOWN. `status` exits non-zero when any source is
STALE, so it works as a scriptable check. `init` writes skeleton concepts — replace
their placeholder bodies with the real explainer and FAQ. Frontmatter values must stay
single-line (the stdlib parser reads the subset the tool writes).

## Session flows

- **First walkthrough of a subject.** After the walkthrough, `init` the subject with
  every source used (the repo, the paper file, the URL), then write the real explainer
  and FAQ over the skeletons. The pin happens at init, so the bundle records exactly
  which version of reality the explainer describes.
- **Learner returns to review.** `status` first. FRESH → the explainer is trustworthy;
  walk them through it or answer questions from it directly. STALE → say so before
  relying on it ("the repo has moved since this was written"), then offer a refresh.
- **Refresh after drift.** Diff-aware, not from scratch: for a git source, read what
  changed since the pinned SHA (`git diff <pinned>..HEAD --stat` and the relevant
  hunks); update only the affected segments of the explainer; append new Q&A to the
  FAQ; `pin` to record the new fingerprint (which also logs the update).
- **UNKNOWN sources** (external URL, moved file): can't be auto-checked — ask the
  learner whether the source changed, or re-fetch and compare judgment-wise.

## Why OKF and not a wiki or cloud doc

Markdown files in a directory are the most portable substrate: versionable in git,
greppable, readable without any service — and because the layout follows an open spec,
any OKF-aware agent or tool can consume the bundle without translation. When the
learner keeps a knowledge base (wiki, Google Drive/Docs, Notion), export the explainer
there *from* the bundle on request — the bundle stays the pinned source of truth, since
only it records which version of the sources the text describes. A rendered artifact
(see [agent-surface.md](agent-surface.md)) is likewise generated *from* the bundle's
explainer, not maintained separately. For a full browsable website over the bundle
(search, landing page, per-concept pages), the sibling `okf-site-kit` skill generates
one from any OKF bundle, including these.

## Placement

For a codebase subject, the natural bundle root is inside the repo (e.g.
`docs/knowledge/`) so the knowledge travels with the code it explains and the next
onboarder finds it. For papers and general topics, a user-level root the learner names
(e.g. `~/knowledge/`) keeps subjects together across projects — one bundle per scope,
subjects as subdirectories, exactly as the spec's hierarchy intends.
