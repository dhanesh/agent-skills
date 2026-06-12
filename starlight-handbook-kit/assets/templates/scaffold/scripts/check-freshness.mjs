#!/usr/bin/env node
/**
 * check-freshness.mjs  (tiered content freshness)
 *
 * Opinionated, decision-oriented guidance goes stale, so every topic carries a
 * `last_reviewed` date and an optional `volatility` tier that sets its review
 * window:
 *   - volatility: volatile  (or UNSET — treated as volatile, the safe default)
 *                           => 12-month review window
 *   - volatility: stable    => 24-month review window
 *
 * EXIT-CODE POLICY (deliberate — fresh content is a maintenance signal, an
 * undated page is an integrity violation):
 *   - MISSING `last_reviewed` on a topic page  => HARD FAIL (exit 1).
 *     The field is required; its absence is an integrity violation, so this check
 *     fails CLOSED. (check-content-frontmatter.mjs also enforces presence;
 *     freshness double-guards it because the freshness math depends on it.)
 *   - OVERDUE (past its review window)         => WARNING only (exit 0).
 *     Staleness is a content-maintenance signal, not a build-blocking defect — a
 *     hard fail here would make the site un-buildable purely by the passage of
 *     time, which is the wrong failure mode. It is loudly reported instead.
 *   - Malformed date / bad volatility value    => HARD FAIL (exit 1).
 *
 * Splash pages (template: splash) and `_`-prefixed private pages are exempt.
 *
 * "Today" defaults to the live date so staleness advances in CI. Set
 * FRESHNESS_NOW=YYYY-MM-DD to pin it for deterministic tests/fixtures.
 *
 * Dependency-free (node: builtins only) — minimal frontmatter parse, no deps.
 */
import { readdirSync, statSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';

const ROOT = process.cwd();
const DOCS = join(ROOT, 'src', 'content', 'docs');
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

// "Today" defaults to the live date so staleness detection advances in CI.
// FRESHNESS_NOW (YYYY-MM-DD) overrides it for deterministic tests/fixtures.
const NOW = process.env.FRESHNESS_NOW
  ? parseISO(process.env.FRESHNESS_NOW)
  : (() => {
      const d = new Date();
      return parseISO(
        `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(
          d.getUTCDate(),
        ).padStart(2, '0')}`,
      );
    })();

const WINDOW_MONTHS = { volatile: 12, stable: 24 };

function parseISO(s) {
  // UTC midnight to avoid TZ drift in month math.
  return new Date(`${s}T00:00:00Z`);
}

/** Months between two dates (a <= b), for the overdue comparison. */
function monthsBetween(a, b) {
  let months = (b.getUTCFullYear() - a.getUTCFullYear()) * 12 + (b.getUTCMonth() - a.getUTCMonth());
  // Adjust for day-of-month so e.g. Jun 30 -> Jun 1 next year isn't double-counted.
  if (b.getUTCDate() < a.getUTCDate()) months -= 1;
  return months;
}

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
  return m ? m[1] : null;
}

function field(fm, name) {
  const m = fm.match(new RegExp(`^\\s*${name}:\\s*(.+?)\\s*$`, 'm'));
  return m ? m[1].replace(/^["']|["']$/g, '') : null;
}

function iso(d) {
  return d.toISOString().slice(0, 10);
}

const files = collect(DOCS);
const hardErrors = [];
const overdue = [];
let checked = 0;

for (const f of files) {
  const text = readFileSync(f, 'utf8');
  const fm = frontmatter(text);
  const rel = relative(ROOT, f);

  if (fm === null) {
    hardErrors.push(`${rel}: no frontmatter block`);
    continue;
  }
  if (/^\s*template:\s*splash\s*$/m.test(fm)) continue; // landing exempt

  checked++;

  // volatility: unset => treat as volatile (safe default, shortest window).
  let volatility = field(fm, 'volatility') || 'volatile';
  if (volatility !== 'volatile' && volatility !== 'stable') {
    hardErrors.push(`${rel}: invalid volatility "${volatility}" — expected "stable" or "volatile"`);
    volatility = 'volatile';
  }

  const lrRaw = field(fm, 'last_reviewed');
  if (lrRaw === null) {
    // REQUIRED field missing => fail closed.
    hardErrors.push(`${rel}: missing required \`last_reviewed\` — fail-closed`);
    continue;
  }
  if (!ISO_DATE.test(lrRaw)) {
    hardErrors.push(`${rel}: \`last_reviewed\` "${lrRaw}" is not an ISO date (YYYY-MM-DD)`);
    continue;
  }

  const reviewed = parseISO(lrRaw);
  if (reviewed > NOW) {
    hardErrors.push(`${rel}: \`last_reviewed\` ${lrRaw} is in the FUTURE relative to ${iso(NOW)}`);
    continue;
  }

  const ageMonths = monthsBetween(reviewed, NOW);
  const limit = WINDOW_MONTHS[volatility];
  if (ageMonths >= limit) {
    overdue.push(
      `${rel}: reviewed ${lrRaw} — ${ageMonths}mo ago, exceeds ${limit}mo ${volatility} window (as of ${iso(NOW)})`,
    );
  }
}

// Report overdue as WARNINGS (non-blocking).
if (overdue.length > 0) {
  console.warn(`WARN: ${overdue.length} page(s) overdue for review (freshness window) — not build-blocking:`);
  for (const o of overdue) console.warn(`  ! ${o}`);
}

// Hard failures block the build (fail-closed).
if (hardErrors.length > 0) {
  console.error(`FAIL: ${hardErrors.length} freshness integrity error(s) (fail-closed):`);
  for (const e of hardErrors) console.error(`  - ${e}`);
  process.exit(1);
}

console.log(
  `OK: all ${checked} topic page(s) carry a valid last_reviewed date` +
    (overdue.length ? ` (${overdue.length} overdue — see warnings above)` : ' and are within their freshness window') +
    `. (as of ${iso(NOW)})`,
);
