// Optional Claude Code Workflow accelerator for base-in-reality.
// Feed this to the Workflow tool. It fans out per-claim verification and
// adversarial refutation deterministically. The skill works without it
// (see SKILL.md for the portable prompt-driven path).
export const meta = {
  name: 'base-in-reality',
  description: 'Verify repo design claims against fetched scholarly/standards sources, adversarially',
  phases: [
    { title: 'Extract', detail: 'detect domains + extract falsifiable claims' },
    { title: 'Verify', detail: 'ground each claim in fetched sources' },
    { title: 'Refute', detail: 'adversarially attack each surviving finding' },
  ],
}

const FINDING = {
  type: 'object',
  required: ['claim', 'layer', 'location', 'verdict', 'severity', 'citations', 'recommended_fix'],
  properties: {
    claim: { type: 'string' },
    layer: { enum: ['algo', 'arch', 'biz'] },
    location: { type: 'string' },
    verdict: { enum: ['VIOLATION', 'DEVIATION', 'OUTDATED', 'UNCONFIRMED'] },
    severity: { enum: ['critical', 'high', 'medium', 'low'] },
    recommended_fix: { type: 'string' },
    citations: {
      type: 'array',
      items: {
        type: 'object',
        required: ['title', 'url', 'fetched'],
        properties: {
          title: { type: 'string' },
          url: { type: 'string' },
          fetched: { type: 'boolean' },
        },
      },
    },
  },
}

const CLAIMS = {
  type: 'object',
  required: ['domains', 'claims'],
  properties: {
    domains: { type: 'array', items: { type: 'string' } },
    claims: {
      type: 'array',
      items: {
        type: 'object',
        required: ['claim', 'layer', 'location'],
        properties: {
          claim: { type: 'string' },
          layer: { enum: ['algo', 'arch', 'biz'] },
          location: { type: 'string' },
        },
      },
    },
  },
}

const VERDICT = {
  type: 'object',
  required: ['refuted'],
  properties: { refuted: { type: 'boolean' }, reason: { type: 'string' } },
}

const maxClaims = (args && args.maxClaims) || 40
// Absolute path to this skill's fetch_sources.py. Verify subagents run from the TARGET repo,
// so a skill-relative 'assets/...' path will not resolve — pass the absolute path via
// args.fetcherPath, e.g. Workflow({ args: { fetcherPath: '/abs/.../base-in-reality/assets/fetch_sources.py' } }).
const fetcherPath = (args && args.fetcherPath) || 'assets/fetch_sources.py'

phase('Extract')
const extracted = await agent(
  'Detect the domain(s) of this repository, then extract up to ' + maxClaims +
  ' discrete, FALSIFIABLE claims across three layers (algo|arch|biz): each a checkable ' +
  'assertion about what the code does, with a file:line location. Return domains + claims.',
  { schema: CLAIMS, label: 'extract' },
)

const claims = (extracted && extracted.claims ? extracted.claims : []).slice(0, maxClaims)
log(`extracted ${claims.length} claim(s) across domains: ${(extracted?.domains || []).join(', ')}`)

const results = await pipeline(
  claims,
  (c) => agent(
    'Verify this claim against AUTHORITATIVE sources. For keyless APIs ' +
    '(arxiv/pubmed/crossref/openalex/semanticscholar) run the bundled helper by its ABSOLUTE ' +
    `path: \`uv run "${fetcherPath}" --source <s> --query "<q>" --limit <n>\` (you run from the ` +
    'target repo, so a relative assets/... path will NOT resolve). Then WebSearch/WebFetch for ' +
    'standards (NIST/RFC/OWASP) and Scholar/JSTOR. You MAY only cite sources you actually ' +
    'fetched this run. If you cannot ground it, verdict=UNCONFIRMED. Claim: ' +
    JSON.stringify(c),
    { schema: FINDING, label: `verify:${c.location}`, phase: 'Verify' },
  ),
  (finding, c) => {
    if (!finding) return null
    if (finding.verdict !== 'VIOLATION' && finding.verdict !== 'DEVIATION') return finding
    return parallel(
      ['correctness', 'citation-applicability', 'severity'].map((lens) => () =>
        agent(
          `Adversarially REFUTE this finding via the ${lens} lens. Default to refuted=true if ` +
          `uncertain. Finding: ${JSON.stringify(finding)}`,
          { schema: VERDICT, label: `refute:${lens}`, phase: 'Refute' },
        ),
      ),
    ).then((votes) => {
      // references/verdict-rubric.md step 2: an uncertain refuter refutes, and a
      // refuter that returned nothing is maximally uncertain, so a crash is a refute.
      const verdicts = votes.map((v) => (v ? v.refuted !== false : true))
      const refutes = verdicts.filter(Boolean).length
      // Step 3: critical/high keep VIOLATION/DEVIATION only on unanimous non-refute;
      // any other severity is downgraded by >= 2 refutes. Mirrored by
      // refutation_downgrades() in assets/report_lint.py; keep the two in step.
      const unanimity = finding.severity === 'critical' || finding.severity === 'high'
      const downgrade = unanimity ? refutes >= 1 : refutes >= 2
      // Step 4: record the votes on every finding, so the linter can check them.
      const refutation = {
        refuters: votes.length,
        verdicts,
        rationale: votes.filter((v) => !v || v.refuted !== false)
          .map((v) => (v ? v.reason || '' : 'refuter returned nothing')).join(' | '),
      }
      return downgrade
        ? { ...finding, verdict: 'UNCONFIRMED', downgraded: true, refutation }
        : { ...finding, refutation }
    })
  },
)

return results.filter(Boolean)
