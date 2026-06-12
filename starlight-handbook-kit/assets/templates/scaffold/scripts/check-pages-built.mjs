#!/usr/bin/env node
/**
 * check-pages-built.mjs  (RT-9 / T6 / O2 — build integrity gate)
 *
 * `astro build` can exit 0 while SILENTLY dropping a page (e.g. malformed
 * frontmatter under some configs, or a misnamed file). A plain "build exited 0"
 * check therefore does NOT prove the site is whole. This script closes that gap:
 * for every routable content entry under src/content/docs, it asserts a matching
 * HTML file exists in dist/. Any missing page fails CI.
 *
 * Run AFTER `astro build`. Dependency-free (node: builtins only).
 */
import { readdirSync, statSync, existsSync } from 'node:fs';
import { join, relative, sep } from 'node:path';

const ROOT = process.cwd();
const DOCS = join(ROOT, 'src', 'content', 'docs');
const DIST = join(ROOT, 'dist');

if (!existsSync(DIST)) {
  console.error('ERROR: dist/ not found. Run `npm run build` before this check.');
  process.exit(2);
}

/** Recursively collect .md/.mdx content files, skipping `_`-prefixed segments
 *  (Astro treats those as private/non-routable — excluding them is intentional). */
function collect(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    if (name.startsWith('_')) continue; // private route segment — not built on purpose
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) out.push(...collect(full));
    else if (/\.mdx?$/.test(name)) out.push(full);
  }
  return out;
}

/** Map a content file path to its expected dist/ HTML path.
 *  Starlight routes src/content/docs/a/b.mdx -> /a/b/  -> dist/a/b/index.html,
 *  and .../a/index.mdx -> /a/ -> dist/a/index.html (root index -> dist/index.html). */
function expectedHtml(file) {
  let routePath = relative(DOCS, file).replace(/\.mdx?$/, '');
  const parts = routePath.split(sep).filter(Boolean);
  if (parts[parts.length - 1] === 'index') parts.pop();
  return join(DIST, ...parts, 'index.html');
}

const files = existsSync(DOCS) ? collect(DOCS) : [];
const missing = [];
for (const f of files) {
  if (!existsSync(expectedHtml(f))) {
    missing.push({ src: relative(ROOT, f), expected: relative(ROOT, expectedHtml(f)) });
  }
}

if (missing.length > 0) {
  console.error(`FAIL: ${missing.length} content page(s) did not produce HTML in dist/:`);
  for (const m of missing) console.error(`  - ${m.src}  ->  (missing) ${m.expected}`);
  console.error('\nA page that silently fails to render is an integrity violation (RT-9/T6).');
  process.exit(1);
}

console.log(`OK: all ${files.length} content pages rendered to dist/. (RT-9 build integrity)`);
