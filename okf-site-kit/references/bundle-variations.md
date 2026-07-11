# OKF bundles in the wild — survey and handling rules

A catalogue of every OKF bundle dialect found on the public internet (surveyed
2026-07-11), and how the generator handles each. This file is the coverage contract:
when a new dialect appears, add it here with a handling rule and a test fixture.

## Family 1 — Google's sample bundles (the spec repo's own)

Source: `okf/bundles/{ga4, stackoverflow, crypto_bitcoin}` in
[GoogleCloudPlatform/knowledge-catalog](https://github.com/GoogleCloudPlatform/knowledge-catalog)
— the reference bundles shipped next to SPEC.md, e.g. the GA4 e-commerce bundle
(`index.md`, `viz.html`, `datasets/`, `references/metrics/…`, `tables/events_.md`).

Observed traits and handling:

| Trait (observed in ga4) | Handling rule |
|---|---|
| Root `index.md` with **no frontmatter** (no `okf_version`) | `okf_version` reported as "undeclared"; never required |
| YAML **folded multi-line** plain scalars (`description:` wraps to an indented line) | parser joins continuation lines with a space |
| `tags:` as **block list** (`- events`), incl. multi-word tags | parsed as list; rendered as chips |
| `timestamp: '2026-05-28T22:53:05+00:00'` (quoted) | quotes stripped; shown in the panel |
| `resource:` non-web URI (BigQuery API URL, `dashboard://…`) | linkified only for http(s); otherwise shown as code |
| Internal links **keep the `.md` suffix** (`../references/metrics/event_count.md`, `tables/index.md`) | resolved against the bundle tree and rewritten to routes |
| **Non-markdown assets** in the bundle (`viz.html`) | copied to `public/bundle-assets/<path>`; links rewritten |
| Filenames with trailing underscores (`events_.md`, for the sharded `events_*` table) | slugified (`tables/events`), collisions deduped |
| Bodies sectioned with **H1 headings** (`# Overview`, `# Schema`, `# Citations` per the spec's optional-body-sections convention) | leading H1 duplicating the title dropped; remaining H1s demoted one level so the page outline stays valid |
| **No `log.md`** | changelog page simply omitted |
| Deep nesting with per-directory `index.md` (`references/metrics/`) | authored index kept; intermediate dirs without one get a synthesized listing |

## Family 2 — Spec-canonical bundles

Source: the [SPEC.md](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
examples and spec-faithful writeups ([Google Cloud blog](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing/),
[okf.md annotated guide](https://okf.md/spec/), [openknowledgeformat.com](https://openknowledgeformat.com/)).

| Trait | Handling rule |
|---|---|
| `okf_version: "0.1"` in root `index.md` frontmatter (the only index allowed frontmatter) | detected and reported; frontmatter on a *non-root* index is a warning |
| `log.md` with `# Directory Update Log` + `## YYYY-MM-DD` sections, newest first | becomes the site's Changelog page (H1 stripped, links rewritten); nested `log.md` skipped with a warning |
| **Bundle-absolute links** (`/playbooks/revenue-review.md`) | resolved from the bundle root |
| Directory links (`playbooks/`) and index links (`tables/index.md`) | both route to the directory page |
| Inline tag lists (`tags: [revenue, saas]`) | parsed same as block lists |
| Index bullet convention `* [Title](url) - description` | descriptions harvested for the homepage's section cards |
| Concept `type` values uncontrolled (BigQuery Table, Metric, Playbook, Runbook, API Endpoint, …) | never enumerated; badge color derived from a hash of the type string |

## Family 3 — Producer-extended bundles

Source: the spec's rule that producers may add arbitrary frontmatter keys and consumers
must preserve them; concretely exercised by this repo's `feynman-walkthrough` skill
(`sources:` pins — a block list of dicts with `type`/`locator`/`fingerprint`/`pinned`
— plus a `created` scalar).

| Trait | Handling rule |
|---|---|
| Unknown scalar keys | shown as label/value rows in the metadata panel |
| Block lists of flat dicts | rendered as a compact list of `key: value` code rows |
| `null` values | rendered as `null`, never crash |
| Literal/folded block scalars (`key: |`, `key: >`) | parsed with newline/space joins |

## Violations (also found in the wild, also handled)

| Violation | Handling rule |
|---|---|
| Concept with no frontmatter at all | rendered as a generic Concept, title from first H1 or filename; `WARN:` emitted |
| Frontmatter missing required `type` | same generic treatment + warning |
| Unparseable frontmatter fragments | whatever parsed is kept; `WARN:` about partial parse |
| Broken internal links | left exactly as authored; each reported once |
| Empty directory tree | `inspect` returns `INSPECT_RESULT: EMPTY` (exit 1) rather than generating a hollow site |

## What was checked and found *not* to exist (yet)

- No public registry of third-party OKF bundles as of the survey date — OKF v0.1 was
  published in June 2026 and the ecosystem is weeks old; the spec repo's three bundles
  are the only authoritative published examples, with community sites
  ([Document360](https://document360.com/blog/open-knowledge-format/),
  [Flowtivity](https://flowtivity.ai/blog/google-open-knowledge-format/),
  [WitsCode](https://witscode.com/open-knowledge-format)) reproducing spec-shaped
  examples rather than new dialects.
- No alternate reserved filenames or index formats beyond `index.md`/`log.md`.

When surveying again (do so when the spec version bumps): re-check the spec repo's
`okf/bundles/`, search GitHub for `okf_version` in `index.md`, and re-read the
community guides above for newly documented conventions.
