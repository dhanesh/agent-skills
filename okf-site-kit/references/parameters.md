# okf_site.py — commands and flags

Stdlib-only; python3 is the only generation-time dependency. npm is needed only to
build the emitted site.

## `inspect <bundle>`

Scan without generating. Prints `OKF_VERSION`, page/section/asset counts, one
`CONCEPT: <path> [<type>] -> /<route>/` line per concept, `WARN:` lines for
conformance issues, and `INSPECT_RESULT: OK|EMPTY`. Exit 1 when the tree holds no
markdown at all.

## `generate <bundle> --out <dir> [flags]`

| Flag | Default | Meaning |
|---|---|---|
| `--out <dir>` | required | Output project directory. |
| `--title <str>` | root index H1, else humanized bundle dirname | Site title (header + hero). |
| `--tagline <str>` | first prose paragraph of the root index | Hero tagline. |
| `--base <path>` | `''` | Deploy base path. GitHub Pages **project** page → `/<repo-name>`; user/org root page → leave empty. Baked into `astro.config.mjs` and component links. |
| `--site <origin>` | unset | Site origin (e.g. `https://user.github.io`) for canonical URLs/sitemap. |
| `--deploy-workflow` | off | Also emit `.github/workflows/deploy.yml` (GitHub Pages build+deploy on push to `main`). |
| `--force` | off | Rebuild `src/content/docs/` and `public/bundle-assets/` even if present. Without it, generating over existing content is refused. |

Output report mirrors `inspect` plus `SITE_GENERATED:` and a `NEXT:` line with the
build command. Exit is non-zero only on argument/bundle-root errors — content
problems are warnings by design.

## What `--force` owns

Regeneration deletes and rebuilds only the two generated trees
(`src/content/docs/`, `public/bundle-assets/`) and rewrites the scaffolding files
(`astro.config.mjs`, `package.json`, `src/content.config.ts`, `src/styles/okf.css`,
`tsconfig.json`, `.gitignore`, `README.md`, `public/favicon.svg`). Hand-edits to those
scaffolding files are overwritten — re-apply customizations via flags, or edit the
emitted project only after the final generation.
