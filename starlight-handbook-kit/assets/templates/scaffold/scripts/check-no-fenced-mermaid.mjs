#!/usr/bin/env node
/**
 * check-no-fenced-mermaid.mjs  (diagram-rendering gate)
 *
 * Mermaid diagrams MUST use the <Mermaid> component, never a fenced mermaid code
 * block. Starlight's Expressive Code renders a fenced mermaid block as
 * syntax-highlighted CODE, not a diagram — a silent, build-green failure where a
 * "## Diagram" section shows raw source instead of a picture. This gate fails if
 * any content file contains a fenced mermaid block.
 *
 * Dependency-free (node: builtins only).
 */
import { readdirSync, statSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';

const ROOT = process.cwd();
const DOCS = join(ROOT, 'src', 'content', 'docs');

// A fenced mermaid block: a line that is three backticks followed by "mermaid".
const FENCE_MERMAID = new RegExp('^\\s*' + '`'.repeat(3) + '\\s*mermaid\\b', 'm');

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
const offenders = [];
for (const f of files) {
  const text = readFileSync(f, 'utf8');
  if (FENCE_MERMAID.test(text)) {
    const line = text.split('\n').findIndex((l) => FENCE_MERMAID.test(l + '\n')) + 1;
    offenders.push(`${relative(ROOT, f)}:${line}`);
  }
}

if (offenders.length > 0) {
  console.error(`FAIL: ${offenders.length} fenced mermaid block(s) — these render as CODE, not diagrams:`);
  for (const o of offenders) console.error(`  - ${o}`);
  console.error('\nUse the <Mermaid code={`...`} /> component instead (see templates/topic.mdx).');
  process.exit(1);
}

console.log(`OK: no fenced mermaid blocks in ${files.length} content files — all diagrams use <Mermaid>. (diagram rendering)`);
