# Parameters

Five placeholders parameterize the scaffold. Each appears in
`assets/templates/scaffold/`; replace every occurrence so no `{{...}}` token
remains after substitution.

| Placeholder | Description | Example | Default |
|-------------|-------------|---------|---------|
| `{{PKG_NAME}}` | npm package name (kebab-case). For a GitHub Pages project page, use the repository name. | `my-handbook` | _(required)_ |
| `{{SITE_TITLE}}` | Human-readable site title shown in the header, landing hero, and README. | `My Engineering Handbook` | _(required)_ |
| `{{SITE_DESCRIPTION}}` | One-sentence site description (meta description + landing tagline). | `An interactive, decision-oriented engineering handbook.` | _(required)_ |
| `{{DEPLOY_BASE}}` | Deploy base path WITH a leading slash, matching the repo name for a GitHub Pages project page. Use an empty string for a user/org root page (`user.github.io`). | `/my-handbook` | `/my-handbook` |
| `{{GH_PAGES_HOST}}` | Origin the site is served from (no trailing slash). For GitHub Pages this is `https://<user-or-org>.github.io`. | `https://myuser.github.io` | _(required)_ |

## Notes

- **Project page vs root page.** A *project* page is served under a sub-path
  (`https://user.github.io/my-handbook/`), so `{{DEPLOY_BASE}}` must be
  `/my-handbook` and `{{GH_PAGES_HOST}}` is `https://user.github.io`. A *user/org
  root* page is served at the origin, so set `{{DEPLOY_BASE}}` to an empty string.
- **Where each appears:** `{{DEPLOY_BASE}}` and `{{GH_PAGES_HOST}}` →
  `astro.config.mjs` (and `deploy.yml` / landing links); `{{SITE_TITLE}}` &
  `{{SITE_DESCRIPTION}}` → `astro.config.mjs`, `Makefile`, `README.md`, landing
  page; `{{PKG_NAME}}` → `package.json`.
- Keep example/substituted values free of `|` and `{{ }}` so the validators parse
  cleanly.
