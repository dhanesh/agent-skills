# Audit findings — what each code catches

`audit` produces a **seams-at-risk** report: one meaningful line per boundary, not a status
dump of all work. Output is `FINDING: <code> [<severity>] <path> — <message>`, sorted with
tripped edges first, then by severity. `--json` for machines, `--fail-on-high` for CI.

| Code | Sev | Catches |
|---|---|---|
| `CC-TRIPPED` | high | an edge whose point of no return passed with no decision recorded. **Top of the report, always** — it names the decision-maker, because limbo is what caused the incident. |
| `CC-IMPOSSIBLE-PROMISE` | high | a promise the graph says cannot happen: a hard upstream is itself promised later. Pure arithmetic, available at declaration time rather than delivery day. |
| `CC-FOG` | high | an acknowledged commitment whose hard closure has `depth: unknown`. A commitment made into fog. |
| `CC-UNMANAGED-UPSTREAM` | high | the edge rests on a capability that is not `consumer_verified` in the target environment and has no edge managing it. The edge looks healthy while resting on nothing. |
| `CC-PROVIDER-ONLY` | high | the capability is `provider_tested` only in the target environment while a live commitment stands against it. **This line alone catches the original incident.** |
| `CC-PLATFORM-DRIFT` | high | a service whose `runtimes[].platform` differs across environments while carrying an open dependency. Tested on ECS is not tested on EKS. |
| `CC-PONR-NEAR` | high | the point of no return lands within `ponr_warning_days` with no on-track confirmation — after it, the fallback cannot be stood up in time. |
| `CC-FEATURE-BRANCH` | high | a dependency points at a capability whose code is only on a feature branch. That is a proposal, not a capability. |
| `CC-STUB-PROVIDER` | high | the provider named on a proposed edge is a stub team, so the edge can never be acknowledged. Deliberately unsatisfiable: go and talk to them. |
| `CC-HUMAN-LIVENESS` | high/med | `consumer_verified` or `production_live` asserted inside a provider-owned file, a Signal outside `signals/`, or a signal whose `emitted_by` is not `ci://`/`monitor://`. High where the environment sets `requires_live_signal`. |
| `CC-FAILED-VERIFICATION` | high | a consuming team could not make it work. The most valuable document in the bundle — do not let it go quiet. |
| `CC-NOT-VERIFIED` | med | acknowledged, but no consuming team has verified it in the target environment. |
| `CC-PROPAGATED-RISK` | med | this edge is at risk because an upstream edge is tripped or at risk, naming the originating edge. Propagated automatically — relaying is exactly what fails under pressure. |
| `CC-DETECTED` | med | an integration that exists in code with nobody managing it. The debt you already carry. |
| `CC-ONE-SIDED-ACK` | med | a proposed edge waiting on one side's signature. |
| `CC-DATE-GAP` | med | the promise is later than the ask and the consumer has not re-confirmed. |
| `CC-NO-FALLBACK` / `CC-NO-CONSEQUENCE` | med | a live edge missing the two fields the whole exercise exists to force. |
| `CC-DRIFT` | med | the fulfilling service no longer exposes the capability. Marked by the scan, never deleted. |
| `CC-UNRESOLVED-ENV` | med | a deployment verification that did not record which environment it actually ran against. |
| `CC-CYCLE` | med | mutually-requiring capabilities. Legal in reality, safe to traverse, and usually a sign the capability boundary is drawn wrong. |
| `CC-DEFAULT-FALLBACK` | low | the point of no return was computed from the org default because nobody estimated the fallback. Un-estimated, not estimated-as-two-days. |
| `CC-STALE-ASSERTION` | low | a human-asserted readiness older than `stale_assertion_days`. Generated fields cannot rot; hand-written ones can. |
| `CC-BROKEN-LINK` | low | a `capability`/`fulfilled_by`/`fulfils` link with no target. Reported, never fatal — OKF §9 requires tolerating broken links. |
| `CC-COVERAGE` | low | the adoption metric: what fraction of a team's hard closure resolves to claimed teams with attested upstreams. Watch this rise before trusting anything green. |

## Reading the report to a team

Lead with `CC-TRIPPED` and `CC-PONR-NEAR` — they have deadlines. Then the fog and
provider-only lines, which are the ones that feel fine right up until they don't. Leave
`CC-COVERAGE` for last and frame it as progress, not blame: early on it will be low
everywhere, and that is the catalog telling the truth about how little was written down
before it existed.
