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
    'Verify this claim against authoritative sources. For keyless APIs ' +
    '(arxiv/pubmed/crossref/openalex/semanticscholar) run the bundled helper by its absolute ' +
    `path: \`uv run "${fetcherPath}" --source <s> --query "<q>" --limit <n>\` (you run from the ` +
    'target repo, so a relative assets/... path will not resolve). Then WebSearch/WebFetch for ' +
    'standards (NIST/RFC/OWASP) and Scholar/JSTOR. You MAY only cite sources you actually ' +
    'fetched this run. If you cannot ground it, verdict=UNCONFIRMED. Claim: ' +
    JSON.stringify(c),
    { schema: FINDING, label: `verify:${c.location}`, phase: 'Verify' },
  ),
  (finding, c) => {
    if (!finding) return null
    if (finding.verdict !== 'VIOLATION' && finding.verdict !== 'DEVIATION') return finding
    // Lens definitions from references/verdict-rubric.md step 1. Each refuter sees only
    // its own lens so the three votes stay decorrelated.
    const LENSES = {
      correctness: 'Is the claim, as stated, factually true of the code at its location?',
      'citation-applicability': 'Does the cited source actually govern this specific code context, ' +
        'or is it being stretched to fit?',
      severity: 'Is the severity calibrated to the real-world impact, per the rubric ' +
        '(critical = security/compliance/financial-correctness harm; low = stylistic)?',
    }
    return parallel(
      Object.keys(LENSES).map((lens) => () =>
        agent(
          `Try to refute this finding through the ${lens} lens: ${LENSES[lens]} ` +
          'The burden of proof is on the finding, so answer refuted=true when uncertain; ' +
          'give your reason, citing any source you fetched for it. ' +
          `Finding: ${JSON.stringify(finding)}`,
          { schema: VERDICT, label: `refute:${lens}`, phase: 'Refute' },
        ),
      ),
    ).then((votes) => {
      // references/verdict-rubric.md step 2: an uncertain refuter refutes, and a
      // refuter that returned nothing is maximally uncertain, so a crash is a refute.
      const verdicts = votes.map((v) => (v ? v.refuted !== false : true))
      const refutes = verdicts.filter(Boolean).length
      // Step 3: medium/low are downgraded by >= 2 refutes; every other severity
      // (critical, high, and a missing or unknown one: fail closed) keeps
      // VIOLATION/DEVIATION only on unanimous non-refute. Mirrored by
      // refutation_downgrades() in assets/report_lint.py; keep the two in step.
      // One vote per lens is always recorded (a crash is a refute), so
      // refuters === verdicts.length here and the linter's padding is a no-op.
      const unanimity = !['medium', 'low'].includes(finding.severity)
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
