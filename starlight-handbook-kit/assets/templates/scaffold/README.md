# {{SITE_TITLE}}

{{SITE_DESCRIPTION}} Built with [Astro](https://astro.build/) +
[Starlight](https://starlight.astro.build/): MDX-authored, statically rendered,
with React component islands for interactivity and a static-first Mermaid
diagram renderer.

## Prerequisites

- **Node.js 20+**
- **npm** (the project's package manager; `package-lock.json` is committed)

## Usage

```bash
make run     # npm install, then start the dev server and open a browser tab
make build   # npm install, then build the static site to ./dist
make preview # preview the built site locally
make check   # type-check the project (astro check)
make verify  # build + run all 8 integrity gates (what CI runs)
```

## Structure

- `src/content/docs/` — MDX docs, organized into clusters (one directory each,
  with an `index.mdx` overview and one `.mdx` per topic).
- `src/content.config.ts` — Starlight docs schema, extended with
  `last_reviewed` (date) and `decision_guidance` (boolean) frontmatter.
- `src/components/` — `Mermaid.astro` (static-first diagram island) and four
  static-first React widgets (`DecisionTree`, `CompareMatrix`, `WhenWhyTabs`,
  `TradeoffSlider`).
- `templates/topic.mdx` — **canonical topic template**. Copy it to author a new
  topic. Keep the 9 section headings in order and bump `TEMPLATE_VERSION` on
  structural changes (CI drift-checks the marker).
- `scripts/check-*.mjs` — eight dependency-free integrity gates run by
  `npm run verify`.
- `astro.config.mjs` — Starlight + React integrations, the deploy `base`, and
  the sidebar.

## Authoring a new topic

1. Copy `templates/topic.mdx` into the appropriate
   `src/content/docs/<cluster>/` directory.
2. Fill in the frontmatter (`title`, `description`, `last_reviewed`) and each
   fixed `##` section.
3. Add the page to the cluster's `items` array in `astro.config.mjs`.
4. Do **not** reorder or rename the nine section headings unless you also bump
   `TEMPLATE_VERSION`.
5. Run `npm run verify` before committing.
