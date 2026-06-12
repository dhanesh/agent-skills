#!/usr/bin/env node
/**
 * check-content-frontmatter.mjs  (B2 — dated, reviewable guidance)
 *
 * B2 is an INVARIANT: every opinionated topic must carry machine-readable
 * `last_reviewed` metadata so stale recommendations are detectable. The content
 * schema *allows* the field (optional); this test *enforces* it is actually
 * present and well-formed on every routable content page — the standing guard
 * that an author can't ship an undated topic.
 *
 * Splash pages (template: splash) and `_`-prefixed private pages are exempt.
 * Dependency-free (node: builtins only).
 */
import { readdirSync, statSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';

const ROOT = process.cwd();
const DOCS = join(ROOT, 'src', 'content', 'docs');
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function collect(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    if (name.startsWith('_')) continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...collect(full));
    else if (/\.mdx?$/.test(name)) out.push(full);
  }
  return out;
}

/** Extract the leading YAML frontmatter block as raw text (between the first two `---`). */
function frontmatter(text) {
  const m = text.match(/^---\n([\s\S]*?)\n---/);
  return m ? m[1] : null;
}

const files = collect(DOCS);
const problems = [];

for (const f of files) {
  const text = readFileSync(f, 'utf8');
  const fm = frontmatter(text);
  const rel = relative(ROOT, f);

  if (fm === null) {
    problems.push(`${rel}: no frontmatter block`);
    continue;
  }
  if (/^\s*template:\s*splash\s*$/m.test(fm)) continue; // splash/landing exempt

  const lr = fm.match(/^\s*last_reviewed:\s*(.+?)\s*$/m);
  if (!lr) {
    problems.push(`${rel}: missing \`last_reviewed\` frontmatter (B2 invariant)`);
    continue;
  }
  const value = lr[1].replace(/^["']|["']$/g, ''); // strip optional quotes
  if (!ISO_DATE.test(value)) {
    problems.push(`${rel}: \`last_reviewed\` is "${value}" — expected an ISO date (YYYY-MM-DD)`);
  }
}

if (problems.length > 0) {
  console.error(`FAIL: ${problems.length} content page(s) violate B2 (dated guidance):`);
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}

console.log(`OK: all ${files.length} content pages carry a valid last_reviewed date. (B2)`);
