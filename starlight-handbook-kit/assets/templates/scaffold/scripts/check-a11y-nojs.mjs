#!/usr/bin/env node
/**
 * check-a11y-nojs.mjs  (U3 — accessibility + no-JS baseline)
 *
 * U3 is an INVARIANT: interactive islands must be keyboard-navigable AND core
 * content must be readable without JavaScript (islands enhance, not gate).
 * This standing test verifies both halves automatically:
 *
 *  1. NO-JS CONTENT: from the BUILT demo page (dist/_demo/widgets/index.html),
 *     strip every <script>…</script> block, then assert each widget's content
 *     is still present in the remaining DOM. If the text survives without the
 *     scripts, a no-JS visitor can read it.
 *  2. A11Y AFFORDANCES: assert each component's source declares the keyboard /
 *     ARIA semantics it must keep (tablist roles, native range slider with
 *     aria-valuenow, real <button>s, semantic <table>). Guards against someone
 *     silently stripping the accessibility layer.
 *
 * Run AFTER `astro build`. Dependency-free (node: builtins only).
 */
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = process.cwd();
const DEMO_HTML = join(ROOT, 'dist', '_demo', 'widgets', 'index.html');
const COMPONENTS = join(ROOT, 'src', 'components');

const failures = [];

// ---- 1. No-JS content presence ----
if (!existsSync(DEMO_HTML)) {
  failures.push(`Demo page not built at dist/_demo/widgets/index.html — run \`npm run build\` first.`);
} else {
  const raw = readFileSync(DEMO_HTML, 'utf8');
  const noScript = raw.replace(/<script[\s\S]*?<\/script>/gi, ''); // remove all JS payloads/props

  const NOJS_MARKERS = [
    { what: 'CompareMatrix renders a semantic table', pat: /<table/i },
    { what: 'CompareMatrix cell data present without JS', pat: /Log \/ partitioned stream/ },
    { what: 'WhenWhyTabs "When to use" panel present without JS', pat: /When to use/ },
    { what: 'WhenWhyTabs "Why NOT" panel present without JS (all panels stacked)', pat: /Why NOT/ },
    { what: 'DecisionTree question present without JS', pat: /access pattern dominated by recency/ },
  ];
  for (const m of NOJS_MARKERS) {
    if (!m.pat.test(noScript)) {
      failures.push(`No-JS: ${m.what} — not found in script-stripped demo HTML (content would require JS).`);
    }
  }
}

// ---- 2. A11y affordances in component source ----
const A11Y = [
  { file: 'WhenWhyTabs.tsx', needs: [/role=["']tablist["']/, /role=["']tab["']/], desc: 'ARIA tablist pattern' },
  { file: 'TradeoffSlider.tsx', needs: [/type=["']range["']/, /aria-valuenow/], desc: 'native range slider + aria-valuenow' },
  { file: 'DecisionTree.tsx', needs: [/<button/], desc: 'real <button> controls' },
  { file: 'CompareMatrix.tsx', needs: [/<table/], desc: 'semantic <table>' },
];
for (const a of A11Y) {
  const p = join(COMPONENTS, a.file);
  if (!existsSync(p)) {
    failures.push(`A11y: ${a.file} missing.`);
    continue;
  }
  const src = readFileSync(p, 'utf8');
  for (const re of a.needs) {
    if (!re.test(src)) {
      failures.push(`A11y: ${a.file} should keep ${a.desc} but pattern ${re} not found.`);
    }
  }
}

if (failures.length > 0) {
  console.error(`FAIL: ${failures.length} U3 (a11y / no-JS) violation(s):`);
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}

console.log('OK: demo content readable without JS + a11y affordances present in all widgets. (U3)');
