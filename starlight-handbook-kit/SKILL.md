---
name: starlight-handbook-kit
description: "Scaffold or extend a decision-oriented Astro + Starlight documentation handbook where every topic follows a fixed nine-section skeleton, diagrams and widgets are static-first and accessible, and a nine-gate CI suite makes agent-authored content safe. Use when standing up a new handbook/knowledge-base site, or when adding a cluster (a group of topics) or a single topic page to an existing site that already follows this pattern."
x-spec-version: 1.0
---

# starlight-handbook-kit

Build and grow a decision-oriented engineering handbook: an Astro + Starlight
static site where every topic is shaped identically (a fixed nine-section
skeleton), diagrams and interactive widgets render usefully **without
JavaScript**, and nine dependency-free CI gates fail the build the moment a page
drifts from the contract. The gates are what make it safe to let an agent author
content — a dropped page, an undated topic, a fenced-mermaid mistake, or a broken
link all fail loudly instead of shipping.

## When to use

Use when you want to:

- **Scaffold** a brand-new handbook site from scratch (Mode A), or
- **Author** a new cluster or a single topic page into a site that already
  follows this pattern (Mode B).

## Decision flow

```
Does a site with this pattern already exist (astro.config.mjs + templates/topic.mdx + the check-*.mjs gates)?
├── No  → Mode A: Scaffold a new setup.
└── Yes → Mode B: Author content.
            ├── A whole new theme/area      → add a CLUSTER (directory + index.mdx + topics + sidebar block)
            └── One more page in a cluster  → add a TOPIC (one .mdx from the template + sidebar item)
```

---

## Mode A — Scaffold a new setup

1. **Collect the parameters.** Read `PARAMETERS.md` and decide a value for each
   of the five placeholders (package name, site title, site description, deploy
   base path, GitHub Pages host).

2. **Copy the scaffold into the target repo root.** The complete, gate-passing
   site lives under `assets/templates/scaffold/` (including dotfiles like
   `.gitignore` and `.github/workflows/`). Copy it recursively:

   ```sh
   cp -R <skill-dir>/assets/templates/scaffold/. <target-repo>/
   ```

3. **Substitute the five placeholders** in the copied files. They appear only in
   `astro.config.mjs`, `package.json`, `Makefile`, `README.md`,
   `src/content/docs/index.mdx`, and `.github/workflows/deploy.yml`. Replace every
   `{{PLACEHOLDER}}` with its value (see `PARAMETERS.md`). After this, **no
   `{{...}}` token may remain** anywhere.

   - Project page (served at `host/<repo>/`): set the deploy base to `/<repo>`.
   - User/org root page (`user.github.io`): set the deploy base to `` (empty).

4. **Install and verify.**

   ```sh
   npm install
   npm run verify   # astro build + all 9 integrity gates
   ```

   A fresh scaffold passes all nine gates out of the box: it ships one example
   cluster (`example-cluster/`) with an overview and one topic, plus the hidden
   `_demo/widgets.mdx` page the accessibility gate inspects. **Do not delete
   `_demo/widgets.mdx`** — the a11y/no-JS gate reads its built HTML.

5. **Make it yours.** Rename `example-cluster/` to your first real cluster (also
   update its sidebar block in `astro.config.mjs` and the LinkCard in
   `src/content/docs/index.mdx`), then add clusters and topics via Mode B.

---

## Mode B — Author a cluster or a topic

### Add a TOPIC (one page in an existing cluster)

1. Copy the canonical template `templates/topic.mdx` to
   `src/content/docs/<cluster>/<topic-slug>.mdx`. (In the skill, the canonical
   copy is `assets/templates/scaffold/templates/topic.mdx`.)
2. Fill in the frontmatter: `title`, `description`, and `last_reviewed` (an ISO
   `YYYY-MM-DD` date — **required** by the frontmatter and freshness gates).
   Optionally set `volatility: stable | volatile` (defaults to `volatile`) to
   pick the review window the freshness gate measures against — `volatile` = 12
   months, `stable` = 24.
3. Fill in all **nine `##` sections, in this exact order**:
   `Overview`, `Mental model`, `Types / Variants`, `When to use / When NOT`,
   `Tradeoffs`, `Diagram`, `Try it`, `Real-world examples`, `Further reading`.
   Do not add, remove, rename, or reorder them.
4. Diagrams: use the `<Mermaid code={\`...\`} />` component. **Never** a fenced
   ```` ```mermaid ```` block — Starlight renders that as code, not a diagram
   (the mermaid gate fails it).
5. Widgets (optional, in `## Try it`): import from `../../../components/` (three
   levels up from a `<cluster>/<topic>.mdx` page) and pass `client:visible`.
6. Register the page in `astro.config.mjs`: add
   `{ label: '...', slug: '<cluster>/<topic-slug>' }` to that cluster's `items`.
7. Run `npm run verify`.

### Add a CLUSTER (a new group of topics)

1. Create `src/content/docs/<cluster-slug>/`.
2. Add `<cluster-slug>/index.mdx` — the cluster **overview**. It also follows the
   full nine-section skeleton and needs `last_reviewed` (it is not a splash page).
   Use the example at
   `assets/templates/scaffold/src/content/docs/example-cluster/index.mdx` as the model.
3. Add one or more topic pages per the TOPIC steps above.
4. Append a sidebar block in `astro.config.mjs` (the file documents the exact
   shape inline). The overview's `slug` is the bare cluster slug; topics are
   `<cluster>/<topic>`.
5. Add a `<LinkCard>` for the cluster in `src/content/docs/index.mdx`, using the
   `${base}<cluster-slug>/` href form so the deploy base is applied at runtime.
6. Run `npm run verify`.

---

## The nine gates (why authoring is safe)

`npm run verify` builds the site, then runs nine checks. Each pairs a design
invariant with a script that fails CI when violated:

| Gate (npm script) | Enforces |
|-------------------|----------|
| `verify:template` | Canonical template keeps its `TEMPLATE_VERSION` + 9 sections in order |
| `verify:pages` | Every routable content page actually rendered to `dist/` (no silent drops) |
| `verify:frontmatter` | Every topic carries a valid ISO `last_reviewed` date |
| `verify:skeleton` | Every authored page follows the 9-section order |
| `verify:a11y` | Widgets stay readable with no JS + keep ARIA/keyboard affordances |
| `verify:artifacts` | No tool-call artifacts leaked into authored MDX |
| `verify:links` | Every internal link resolves to a built file (base-aware) |
| `verify:mermaid` | Diagrams use `<Mermaid>`, never a fenced mermaid block |
| `verify:freshness` | Topics stay within their `volatility` review window — undated/malformed fails the build, overdue warns |

The freshness gate fails **closed** on a missing or malformed `last_reviewed`
(an integrity violation), but a topic merely *overdue* for review is a
non-blocking warning — staleness shouldn't make the site un-buildable purely by
the passage of time. Pin `FRESHNESS_NOW=YYYY-MM-DD` to make it deterministic in
tests.

If you change the nine-section skeleton on purpose, bump the `TEMPLATE_VERSION`
marker in `templates/topic.mdx` — the template-integrity gate keys off it.

## Parameters

The five scaffold placeholders are documented in `PARAMETERS.md`.
