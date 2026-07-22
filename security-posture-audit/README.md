# security-posture-audit

A read-only, fully offline audit of a repository's **security hygiene posture** — the
deterministic layer of this repo's trust family. A stdlib-only Python scanner walks the
tree (skipping `.git`, `node_modules`, `vendor`, …) and emits severity-graded findings
with `file:line` evidence and concrete remediations; the agent then adjudicates each
finding in context before reporting.

## What it finds

- **Unpinned dependencies** — `requirements*.txt` without `==`, `package.json` ranges
  with no lockfile, Dockerfile `FROM :latest`/untagged bases
- **Committed credential-shaped files** — `.env*` (except `.example`-style), `*.pem`,
  `id_rsa`-family (presence only; content is never read)
- **Debug & permissive flags** — hardcoded `DEBUG = True`, `app.run(debug=True)`,
  wildcard CORS origins
- **Insecure transports** — plain `http://` in dependency/config files, pip
  `--trusted-host`
- **Risky CI patterns** — `pull_request_target` + PR-head checkout, `curl | sh` in
  GitHub Actions workflows
- **Dangerous file modes** — world-writable and setuid/setgid files (POSIX)
- **Missing SECURITY.md** (INFO)

## What it is not

Not a CVE scanner (no advisory DB, no network), not SAST dataflow analysis, not a
secrets-content scanner, and not norms/claims verification (`base-in-reality`) or
agent-readiness auditing (`agent-ready-rails`). It proves posture facts from the tree —
nothing more, and the report says so explicitly. Use it only on repositories you own or
are authorized to review.

## Install

```bash
npx skills add dhanesh/agent-skills --skill security-posture-audit
```

## Usage

Ask the agent to "audit this repo's security posture" — it follows the SKILL.md
workflow (scope → scan → adjudicate → report → verify fixes). The scanner also runs
standalone:

```bash
python3 assets/audit_posture.py /path/to/repo                      # human summary
python3 assets/audit_posture.py /path/to/repo --format json        # machine-readable
python3 assets/audit_posture.py /path/to/repo --fail-on medium     # gate on MEDIUM+
python3 assets/audit_posture.py /path/to/repo --skip-dir generated # extra exclusions
```

Exit codes: `0` clean below the `--fail-on` threshold (default `high`), `1` findings at
or above it, `2` usage error.

## Layout

- `assets/audit_posture.py` — the deterministic scanner (stdlib-only, offline)
- `assets/test_audit_posture.py` — unit suite (`cd assets && python3 test_audit_posture.py`)
- `references/checks.md` — per-check rubric: what it proves, what it cannot, severity
  rationale, remediation
- `eval/run_eval.py` — outcome eval against hardened + leaky fixture repos, with
  false-positive negatives and exit-code checks
