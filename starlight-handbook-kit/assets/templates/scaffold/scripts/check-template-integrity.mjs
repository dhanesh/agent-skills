#!/usr/bin/env node
/**
 * check-template-integrity.mjs  (RT-4 / RT-10 / O3 — canonical template integrity)
 *
 * The canonical topic template (templates/topic.mdx) is the spine of the docs:
 * authored pages and the page-authoring workflow both derive from it. This
 * check is the docs-repo half of the drift defense (the skill repo carries the
 * cross-repo SHA-256 check). It asserts the canonical template:
 *   1. carries a TEMPLATE_VERSION marker, and
 *   2. contains exactly the fixed 9-section skeleton, in order.
 * If the skeleton changes without a version bump, or order drifts, CI fails.
 *
 * Dependency-free (node: builtins only).
 */
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = process.cwd();
const TEMPLATE = join(ROOT, 'templates', 'topic.mdx');

const EXPECTED_SECTIONS = [
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

if (!existsSync(TEMPLATE)) {
  console.error(`ERROR: canonical template not found at ${TEMPLATE}`);
  process.exit(2);
}

const text = readFileSync(TEMPLATE, 'utf8');
const errors = [];

const version = text.match(/TEMPLATE_VERSION:\s*(\d+\.\d+\.\d+)/);
if (!version) {
  errors.push('Missing TEMPLATE_VERSION marker (expected e.g. "TEMPLATE_VERSION: 1.0.0").');
}

// Collect level-2 headings in document order.
const sections = [...text.matchAll(/^##\s+(.+?)\s*$/gm)].map((m) => m[1]);

if (sections.length !== EXPECTED_SECTIONS.length) {
  errors.push(
    `Expected ${EXPECTED_SECTIONS.length} sections, found ${sections.length}: [${sections.join(' | ')}]`
  );
} else {
  for (let i = 0; i < EXPECTED_SECTIONS.length; i++) {
    if (sections[i] !== EXPECTED_SECTIONS[i]) {
      errors.push(`Section ${i + 1} should be "${EXPECTED_SECTIONS[i]}" but is "${sections[i]}".`);
    }
  }
}

if (errors.length > 0) {
  console.error('FAIL: canonical template integrity violated:');
  for (const e of errors) console.error(`  - ${e}`);
  console.error('\nIf the skeleton changed intentionally, bump TEMPLATE_VERSION and re-sync the skill copy.');
  process.exit(1);
}

console.log(`OK: template integrity verified (TEMPLATE_VERSION ${version[1]}, 9 sections in order).`);
