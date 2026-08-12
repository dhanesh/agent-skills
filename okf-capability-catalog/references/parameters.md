# CLI reference

```bash
python3 assets/okf_catalog.py <mode> <bundle> [flags]
```

Every mode takes `--now <ISO-8601>` and `--today <YYYY-MM-DD>` to pin the clock, which is
what makes runs reproducible (and the test suites deterministic). Refusals print
`REFUSED:` + `HINT:` and exit 2; hard failures exit 1.

## `init` — scaffold the bundle (once per organisation)

| Flag | Default | Notes |
|---|---|---|
| `--org` | `acme` | organisation name |
| `--integration` | `develop` | minimum branch for a capability to be asserted |
| `--release` | `main` | |
| `--environments` | `dev,staging,production` | ordered lowest → highest |
| `--live-signal-env` | `production` | comma-separated; `production_live` there needs a machine signal |
| `--default-fallback-days` | `2` | used only when a dependency omits one, and flagged when used |
| `--platform-team` | — | CODEOWNER of `signals/` |
| `--force` | off | rewrite the skeleton; documents are never touched |

Creates `index.md`, `catalog.config.yaml`, `log.md`, `README.md`, and **empty** `teams/`,
`dependencies/`, `signals/`.

## `annotate` — scan a service repository

| Flag | Notes |
|---|---|
| `--repo <path>` | required; the service repo to scan |
| `--branch <name>` | defaults to the repo's checked-out branch; anything other than the integration/release branch is proposal-only |
| `--team <id>` | override ownership when `.okf/team.yaml` and CODEOWNERS cannot answer |
| `--attest-upstream yes\|no` | the owning team's answer to "is `requires` the COMPLETE list?" — until answered, depth stays `unknown` |
| `--by <handle>` | who is attesting |
| `--commit <sha>` | override the scanned commit |

Writes Service + Capabilities + `detected` edges, regenerates indexes, appends `log.md`.

## `declare` — consumer records a dependency

| Flag | Notes |
|---|---|
| `--consumer <team>` | must be a claimed team |
| `--capability <id\|path>` | creates a proposed capability (and a stub team) if absent |
| `--environment <env>` | defaults to the highest configured |
| `--requested-date <date>` | the ask, never the promise |
| `--consequence "<text>"` | refused if blank or "TBD" |
| `--fallback "<text>" --fallback-days N` | or `--no-fallback "<rationale>"` |
| `--depends-on dep-a,dep-b` | edges this one rests on |
| `--dependency <id>` | promote a specific `detected` edge |
| `--provider-team <id>`, `--why`, `--by` | |

Passing `--promised-date` is refused: that is the provider's field.

## `ack` — provider commits a date

`--dependency`, `--team`, `--promised-date`, `--by` are required. `--note`, `--dispute`, and
`--runtime-checked` (needed when the target environment's platform differs from the others)
are optional. Passing `--consequence`, `--fallback` or `--requested-date` is refused.

## Edge lifecycle

| Mode | Who | Effect |
|---|---|---|
| `confirm` | consumer | re-confirm after a date slip → `acknowledged` |
| `on-track` | provider | records an on-track confirmation; only counts inside `on_track_window_days` before the PONR |
| `risk` | provider | `--note` required → `at_risk` |
| `resolve` | either, named | `--decision satisfied\|fallback_invoked\|renegotiated`; `satisfied` requires effective readiness ≥ `consumer_verified` with depth `complete` |

## Readiness sources

| Mode | Who | Ceiling |
|---|---|---|
| `tested` | owning team | `provider_tested` (the flag simply cannot express more) |
| `verify` | consuming team | `consumer_verified`; refuses the owning team and any undeclared consumer |
| `signal` | CI/monitoring | `production_live`; refuses any `--emitted-by` that is not `ci://` or `monitor://` |

`verify` flags: `--team --capability --environment --result verified|failed|partial
--kind deployment|contract|manual --evidence --by [--scope --ran-in --resolved-from
--expires-days --commit-sha]`. For `kind: deployment`, `--resolved-from` is required and
`--ran-in` must match `--environment`.

## Reading

| Mode | Notes |
|---|---|
| `readiness --capability <id> [--environment <env>]` | prints `effective`, `own`, `depth`, `verdict`, the hard closure and every note |
| `review [--team <id>] [--view all\|inbound\|outbound\|directory\|timeline\|capability]` | what is |
| `audit [--ponr-days N] [--json] [--fail-on-high]` | what is wrong |
| `validate` | OKF conformance (hard) + catalog policy (soft) |
| `enforce --changes <json> [--tolerance-days N]` | commit-boundary checks; see `references/enforcement.md` |
| `codeowners` | regenerate `.github/CODEOWNERS` from the teams present |
