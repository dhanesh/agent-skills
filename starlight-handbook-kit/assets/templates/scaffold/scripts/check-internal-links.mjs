#!/usr/bin/env node
/**
 * check-internal-links.mjs  (internal-link integrity gate)
 *
 * The build can be structurally green while a link is wrong: a card with the
 * wrong href, an absolute link that loses the base path, a typo'd slug. Those
 * are valid HTML, so the other gates pass — but the link 404s for a real user.
 * This gate crawls the built dist/, extracts every internal <a href>/<… src>,
 * and asserts each resolves to a file that actually exists.
 *
 * Base-aware: detects the deploy base (e.g. /my-handbook) from the
 * built _astro asset URLs, so a link that drops or mangles the base (the classic
 * `/my-handbookexample-cluster/` missing-slash bug) is flagged.
 *
 * Run AFTER `astro build`. Dependency-free (node: builtins only).
 */
import { readdirSync, statSync, readFileSync, existsSync } from 'node:fs';
import { join, relative, dirname } from 'node:path';

const ROOT = process.cwd();
const DIST = join(ROOT, 'dist');

if (!existsSync(DIST)) {
  console.error('ERROR: dist/ not found. Run `npm run build` before this check.');
  process.exit(2);
}

function htmlFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...htmlFiles(full));
    else if (name.endsWith('.html')) out.push(full);
  }
  return out;
}

const files = htmlFiles(DIST);

// Detect the base prefix from an _astro asset reference (auto-adapts to config).
let base = '';
for (const f of files) {
  const m = readFileSync(f, 'utf8').match(/(?:href|src)="([^"]*?)\/_astro\//);
  if (m) { base = m[1]; break; } // '' when no base, else e.g. '/my-handbook'
}

const SKIP = /^(https?:)?\/\/|^(mailto:|tel:|javascript:|data:|#)/i;

/** Map an internal site path (leading '/') to the dist file it should resolve to,
 *  returning the first candidate that exists, or null if none do. */
function resolveToFile(pathOnly) {
  let rel = pathOnly;
  if (base) {
    if (rel === base) rel = '/';
    else if (rel.startsWith(base + '/')) rel = rel.slice(base.length);
    else return null; // absolute internal link NOT under the base => broken (the bug class)
  }
  rel = rel.replace(/^\/+/, ''); // strip leading slash
  const candidates = [];
  if (rel === '' || rel.endsWith('/')) candidates.push(rel + 'index.html');
  else if (/\.[a-z0-9]+$/i.test(rel)) candidates.push(rel);
  else { candidates.push(rel + '/index.html', rel + '.html'); }
  for (const c of candidates) if (existsSync(join(DIST, c))) return c;
  return null;
}

const broken = [];
let checked = 0;

for (const f of files) {
  const html = readFileSync(f, 'utf8');
  const pageDir = dirname(f);
  for (const m of html.matchAll(/\b(?:href|src)="([^"]+)"/g)) {
    let url = m[1].trim();
    if (!url || SKIP.test(url)) continue;
    url = url.split('#')[0].split('?')[0];
    if (!url) continue;

    let pathOnly;
    if (url.startsWith('/')) {
      pathOnly = url;
    } else {
      // relative link — resolve against this page's directory, then make site-absolute
      const abs = join(pageDir, url);
      pathOnly = '/' + relative(DIST, abs).split(/[\\/]/).join('/');
      // relative links live inside dist already; check directly
      const direct = url.endsWith('/') || !/\.[a-z0-9]+$/i.test(url)
        ? [join(abs, 'index.html'), abs + '.html', abs]
        : [abs];
      checked++;
      if (!direct.some((c) => existsSync(c))) {
        broken.push({ page: relative(ROOT, f), link: m[1] });
      }
      continue;
    }

    checked++;
    if (!resolveToFile(pathOnly)) {
      broken.push({ page: relative(ROOT, f), link: m[1] });
    }
  }
}

if (broken.length > 0) {
  // De-dupe by link for a readable report.
  const byLink = new Map();
  for (const b of broken) {
    if (!byLink.has(b.link)) byLink.set(b.link, new Set());
    byLink.get(b.link).add(b.page);
  }
  console.error(`FAIL: ${byLink.size} broken internal link(s) (base="${base || '/'}"):`);
  for (const [link, pages] of byLink) {
    const list = [...pages].slice(0, 3).join(', ') + (pages.size > 3 ? ` (+${pages.size - 3} more)` : '');
    console.error(`  - ${link}  ->  no such file in dist/   [on: ${list}]`);
  }
  process.exit(1);
}

console.log(`OK: all ${checked} internal links resolve to built files. (link integrity, base="${base || '/'}")`);
