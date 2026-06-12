#!/usr/bin/env node
/**
 * check-no-artifacts.mjs  (content hygiene gate)
 *
 * Generation tooling can occasionally leak tool-call artifacts (e.g. stray
 * `</content>`, `</invoke>`, `<parameter ...>`, `</antml...`) into authored MDX.
 * The build may still pass while the page renders garbage. This standing gate
 * fails CI if any such artifact appears in content — added after one near-miss
 * during automated cluster generation.
 *
 * Dependency-free (node: builtins only).
 */
import { readdirSync, statSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';

const ROOT = process.cwd();
const DOCS = join(ROOT, 'src', 'content', 'docs');

// Patterns that should NEVER appear in legitimate prose/MDX content.
const FORBIDDEN = [
  /<\/content>/,
  /<\/invoke>/,
  /<\/antml/,
  /<parameter\s+name=/,
  /<\/parameter>/,
];

function collect(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...collect(full));
    else if (/\.mdx?$/.test(name)) out.push(full);
  }
  return out;
}

const files = collect(DOCS);
const hits = [];
for (const f of files) {
  const text = readFileSync(f, 'utf8');
  const lines = text.split('\n');
  lines.forEach((line, i) => {
    for (const re of FORBIDDEN) {
      if (re.test(line)) hits.push(`${relative(ROOT, f)}:${i + 1}  ${re}`);
    }
  });
}

if (hits.length > 0) {
  console.error(`FAIL: ${hits.length} tool-artifact leak(s) found in content:`);
  for (const h of hits) console.error(`  - ${h}`);
  process.exit(1);
}

console.log(`OK: no tool-call artifacts in ${files.length} content files. (content hygiene)`);
