#!/usr/bin/env node
/**
 * check-pages-skeleton.mjs  (U1 — fixed topic skeleton on every page)
 *
 * U1 is an INVARIANT: every topic follows the fixed 9-section skeleton, in order.
 * check-template-integrity.mjs guards the TEMPLATE; this guards the actual
 * authored CONTENT pages — closing the gap that the template could be correct
 * while a hand-written page drifts. The two together make U1 testable end to end.
 *
 * Splash pages (template: splash) and `_`-prefixed private pages are exempt
 * (they are landing/demo pages, not topics). Dependency-free.
 */
import { readdirSync, statSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';

const ROOT = process.cwd();
const DOCS = join(ROOT, 'src', 'content', 'docs');

const EXPECTED = [
  'Overview',
  'Mental model',
  'Types / Variants',
  'When to use / When NOT',
  'Tradeoffs',
  'Diagram',
  'Try it',
  'Real-world examples',
  'Further reading',
];

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

function frontmatter(text) {
  const m = text.match(/^---\n([\s\S]*?)\n---/);
  return m ? m[1] : '';
}

const files = collect(DOCS);
const problems = [];
let checked = 0;

for (const f of files) {
  const text = readFileSync(f, 'utf8');
  if (/^\s*template:\s*splash\s*$/m.test(frontmatter(text))) continue; // landing exempt

  checked++;
  const rel = relative(ROOT, f);
  // Level-2 headings in document order, ignoring anything inside fenced code blocks.
  const noFences = text.replace(/```[\s\S]*?```/g, '');
  const sections = [...noFences.matchAll(/^##\s+(.+?)\s*$/gm)].map((m) => m[1]);

  if (sections.length !== EXPECTED.length) {
    problems.push(`${rel}: expected ${EXPECTED.length} sections, found ${sections.length} [${sections.join(' | ')}]`);
    continue;
  }
  for (let i = 0; i < EXPECTED.length; i++) {
    if (sections[i] !== EXPECTED[i]) {
      problems.push(`${rel}: section ${i + 1} should be "${EXPECTED[i]}" but is "${sections[i]}"`);
      break;
    }
  }
}

if (problems.length > 0) {
  console.error(`FAIL: ${problems.length} content page(s) violate the U1 skeleton:`);
  for (const p of problems) console.error(`  - ${p}`);
  console.error('\nEvery topic must follow the canonical 9-section order (see templates/topic.mdx).');
  process.exit(1);
}

console.log(`OK: all ${checked} topic pages follow the fixed 9-section skeleton in order. (U1)`);
