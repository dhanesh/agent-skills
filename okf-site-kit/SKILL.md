---
name: okf-site-kit
description: >-
  Turn any Open Knowledge Format (OKF v0.1) bundle into a beautiful, browsable static
  website. Use when the user wants to publish, browse, view, share, or "make a site from"
  an OKF bundle / knowledge folder / agent-readable markdown wiki — e.g. "generate a site
  from my knowledge bundle", "make this OKF browsable", "publish the team knowledge to
  GitHub Pages". Ships a stdlib generator that reads the bundle (concepts with YAML
  frontmatter, reserved index.md/log.md, nested directories, non-markdown assets) and
  emits a complete Astro + Starlight project: hero landing page with section cards, one
  page per concept with an OKF metadata panel (type badge, tags, resource, timestamp,
  producer keys), rewritten internal links, changelog from log.md, full-text search, and
  light/dark theming. Tolerates every bundle dialect found in the wild — Google's sample
  bundles, spec-canonical bundles, and producer-extended bundles — reporting conformance
  warnings instead of failing.
license: MIT
compatibility: Requires python3 (stdlib only) to inspect bundles and generate the project; Node.js 18+ with npm to build/preview the emitted Astro + Starlight site (network needed only for npm install).
x-spec-version: 1.0
metadata:
  author: dhanesh
  version: "1.1.0"
  tags: "okf,static-site,astro,starlight,knowledge-base,documentation,site-generator"
---

# okf-site-kit

Generate a browsable website from an Open Knowledge Format bundle. OKF (Google's open
spec for agent-readable knowledge) deliberately ships no required tooling — a bundle is
just markdown with YAML frontmatter — which means "browsable" is left to consumers. This
skill is that consumer: [assets/okf_site.py](assets/okf_site.py) (stdlib-only) reads any
OKF bundle and emits a complete Astro + Starlight static-site project with search,
navigation, and OKF-aware presentation. The bundle stays the canonical knowledge; the
site is a generated view of it, regenerated whenever the bundle changes.

## Ground rules

- **The bundle is read-only.** The generator never mutates the source bundle. When the
  scan finds problems worth fixing at the source (missing `type`, broken links), surface
  them to the user and fix the bundle only if they ask.
- **Tolerance over rejection.** Per the OKF spec's consumer rules, unknown types, extra
  frontmatter keys, missing optional files, and broken links must degrade gracefully.
  The generator renders everything it can and prints `WARN:` lines for the rest — pass
  the warnings on, don't suppress them.
- **Spec tracking.** The generator targets OKF v0.1. If the spec has moved (check
  [references/okf-spec.md](references/okf-spec.md) for the URL and update rule), update
  the generator before emitting a stale dialect.

## Workflow

1. **Locate and inspect the bundle.** The bundle root is the directory whose `index.md`
   (or concept tree) the user means — for bundles made by the `feynman-walkthrough`
   skill it's the knowledge root (e.g. `docs/knowledge/`). Run
   `python3 assets/okf_site.py inspect <bundle>` and read the report: concept count and
   routes, declared `okf_version`, and conformance warnings. Relay warnings that the
   user can act on (concepts missing `type`, files without frontmatter).
2. **Generate the site.** `python3 assets/okf_site.py generate <bundle> --out <dir>`
   with flags from [references/parameters.md](references/parameters.md): `--title`
   (defaults to the root index's H1), `--tagline`, `--base /<repo>` +
   `--site https://<user>.github.io` + `--deploy-workflow` when the target is GitHub
   Pages. The output is a self-contained project — nothing in it references the skill.
3. **Build and verify.** `cd <dir> && npm install && npm run build` must complete with
   zero errors; treat generator `WARN:` lines about unresolved links as content issues
   to report. Where a browser is available, `npm run preview` and check the two pages
   that prove the generation: the homepage (hero, one card per section, working links)
   and one concept page (type badge, tags, metadata panel, rewritten links, table of
   contents). Fix and regenerate until clean.
4. **Deliver.** Hand over the built site the best way the surface allows: the project
   directory (plus `dist/` for the static build), a rendered artifact of a key page
   when the harness supports it, or a pushed branch with the deploy workflow when the
   user wants GitHub Pages. State plainly which bundle warnings remain unfixed.
5. **Regenerate on change.** The site is disposable output: when the bundle changes,
   rerun `generate … --force` (it rebuilds `src/content/docs/` and
   `public/bundle-assets/`, leaving any user customizations to config untouched only if
   they re-apply flags — say so). For continuous publishing, the emitted
   `--deploy-workflow` rebuilds on every push to `main`.

## What the generator handles

Every OKF bundle dialect observed in the wild is covered — the survey, with sources and
per-variation handling rules, is in
[references/bundle-variations.md](references/bundle-variations.md):

- **Google's sample bundles** (`ga4`, `stackoverflow`, `crypto_bitcoin` in the spec
  repo): block-list tags, folded multi-line descriptions, quoted timestamps,
  `.md`-suffixed relative links, no `okf_version`, no `log.md`, non-markdown assets
  (`viz.html`), trailing-underscore filenames, H1-sectioned bodies.
- **Spec-canonical bundles**: `okf_version` in the root index frontmatter, `log.md`
  update history (becomes the site's Changelog), bundle-absolute `/dir/file.md` links,
  inline `[a, b]` tag lists.
- **Producer-extended bundles** (e.g. `feynman-walkthrough`'s explainers): arbitrary
  extra frontmatter keys including block lists of dicts — rendered generically in the
  metadata panel, never dropped.
- **Violations, gracefully**: concepts without frontmatter or without `type` render as
  generic concepts with a warning; unresolvable links are left as written and reported;
  an empty directory tree fails `inspect` with `INSPECT_RESULT: EMPTY` instead of
  emitting a hollow site.

## The emitted site

- **Homepage** — splash hero (title, tagline from the root index's first paragraph or
  `--tagline`) with a link card per top-level section, descriptions lifted from the
  root index's own link annotations.
- **One page per concept** — OKF metadata panel up top (color-coded `type` badge, tag
  chips, resource link, updated timestamp, producer-defined keys), body with internal
  links rewritten to site routes, assets served from `bundle-assets/`, headings
  normalized so the page outline stays valid.
- **Section pages** — authored `index.md` listings where the bundle has them,
  synthesized listings where it doesn't, labeled "Overview" in the sidebar.
- **Changelog** — from root `log.md` when present.
- **For free from Starlight**: full-text search (Pagefind), light/dark theme, mobile
  layout, table of contents, sitemap-ready config.

## Verify checklist

The build itself is the main gate (`npm run build` fails on broken pages), backed by
the generator's own report. Before calling it done, confirm: build exits 0; the
homepage links resolve (spot-check one card); one concept page shows badge + panel +
rewritten link; `WARN:` lines have been either fixed or reported to the user. The
generator's unit suite ([assets/test_okf_site.py](assets/test_okf_site.py), 32 tests)
is the regression net when modifying the generator itself.

## Extending this skill

The generator is one file by design — parsing, routing, transforms, and emission are
separate sections, each unit-tested. To support a future OKF version or a new bundle
dialect, add a fixture to the test suite first, then extend the parser/transform, then
document the variation in
[references/bundle-variations.md](references/bundle-variations.md). To restyle the
output, edit the embedded CSS/scaffold constants — the emitted project itself is plain
Astro + Starlight and can also be customized after generation.
