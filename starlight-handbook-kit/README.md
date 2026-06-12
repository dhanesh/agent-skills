# starlight-handbook-kit

An Agent Skill that **scaffolds or extends a decision-oriented Astro + Starlight documentation handbook** — a static site where every topic is shaped identically (a fixed nine-section skeleton), diagrams and interactive widgets render usefully **without JavaScript**, and nine dependency-free CI gates fail the build the moment a page drifts from the contract. The gates are what make it safe to let an agent author content: a dropped page, an undated topic, a stale recommendation, a fenced-mermaid mistake, or a broken link all fail loudly instead of shipping.

> This README is for humans browsing the folder. The agent-facing instructions live in [`SKILL.md`](./SKILL.md) — that's what Claude reads when the skill triggers.

## What it does

The skill operates in two modes:

- **Mode A — Scaffold a new setup.** Copy a complete, gate-passing site from `assets/templates/scaffold/` into a target repo, substitute five placeholders (package name, site title, description, deploy base path, GitHub Pages host), then `npm install && npm run verify`. A fresh scaffold passes all nine gates out of the box and ships one example cluster.
- **Mode B — Author content into an existing site.** Add a **topic** (one page from `templates/topic.mdx`) or a whole **cluster** (a directory of topics with an overview), wiring each into the sidebar and home page, then re-verify.

## When it triggers

Standing up a new handbook / knowledge-base site, or adding a cluster or single topic page to a site that already follows this pattern (`astro.config.mjs` + `templates/topic.mdx` + the `check-*.mjs` gates present).

## The nine-section skeleton

Every topic — and every cluster overview — uses these `##` sections, in this exact order, none added, removed, renamed, or reordered:

`Overview` · `Mental model` · `Types / Variants` · `When to use / When NOT` · `Tradeoffs` · `Diagram` · `Try it` · `Real-world examples` · `Further reading`

## The nine gates (why authoring is safe)

`npm run verify` builds the site, then runs nine checks. Each pairs a design invariant with a script that fails CI when violated:

| Gate (npm script) | Enforces |
|-------------------|----------|
| `verify:template`    | Canonical template keeps its `TEMPLATE_VERSION` + 9 sections in order |
| `verify:pages`       | Every routable content page actually rendered to `dist/` (no silent drops) |
| `verify:frontmatter` | Every topic carries a valid ISO `last_reviewed` date |
| `verify:skeleton`    | Every authored page follows the 9-section order |
| `verify:a11y`        | Widgets stay readable with no JS + keep ARIA/keyboard affordances |
| `verify:artifacts`   | No tool-call artifacts leaked into authored MDX |
| `verify:links`       | Every internal link resolves to a built file (base-aware) |
| `verify:mermaid`     | Diagrams use `<Mermaid>`, never a fenced ` ```mermaid ` block |
| `verify:freshness`   | Topics stay within their `volatility` review window (undated/malformed fails; overdue warns) |

## Install & use

```bash
npx skills add dhanesh/agent-skills --skill starlight-handbook-kit
```

Once installed it auto-triggers on the contexts above. You can also reach for it explicitly when standing up or extending a handbook.

## Structure

```
starlight-handbook-kit/
├── SKILL.md                          the workflow: Mode A (scaffold) + Mode B (author) + the nine gates
├── PARAMETERS.md                     the five scaffold placeholders and how to fill them
└── assets/templates/scaffold/        a complete, gate-passing Astro + Starlight site
    ├── astro.config.mjs              site + sidebar config (placeholders + inline shape docs)
    ├── package.json / Makefile       build + the verify:* gate scripts
    ├── templates/topic.mdx           canonical nine-section topic template (TEMPLATE_VERSION)
    ├── scripts/check-*.mjs           the nine dependency-free CI gates
    ├── src/components/               static-first, no-JS-friendly widgets (Mermaid, tabs, sliders…)
    ├── src/content/docs/             home page + example-cluster + hidden _demo/widgets.mdx
    └── .github/workflows/            ci.yml (verify) + deploy.yml (GitHub Pages)
```

> Do not delete `src/content/docs/_demo/widgets.mdx` — the a11y/no-JS gate reads its built HTML.

## Parameters

The five scaffold placeholders are documented in [`PARAMETERS.md`](./PARAMETERS.md).
