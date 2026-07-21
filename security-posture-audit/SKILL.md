---
name: security-posture-audit
description: "Read-only, offline audit of a repository's security hygiene posture: deterministic checks for unpinned dependencies (requirements/package.json/Dockerfile), committed .env/.pem/id_rsa files, hardcoded debug and wildcard-CORS flags, plain-http transports and pip trusted-host, risky GitHub Actions patterns (pull_request_target + head checkout, curl|sh), world-writable/setuid modes, and a missing SECURITY.md — every finding severity-graded with file:line evidence and a concrete remediation. Use when asked to \"audit this repo's security posture\", \"run a security hygiene check\", or \"are our deps pinned / env files committed / workflows safe?\" on a repo the user owns or is authorized to review. Not a CVE scanner (no advisory DB, no network), not SAST dataflow analysis, not a secrets-content scanner, and not norms verification — use base-in-reality for norms and agent-ready-rails for agent-readiness."
license: MIT
compatibility: Requires python3 (stdlib only) and a POSIX-like shell. Fully offline — no network, no pip; file-mode checks are POSIX-only.
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "security,audit,posture,hygiene,dependency-pinning,ci-security,defensive"
---

# security-posture-audit

A defensive, read-only audit of a repository's security *hygiene posture* — the
deterministic layer of the repo's trust family. It answers one question: *which posture
defects can a checklist prove from the tree, and how severe is each one in this repo's
actual context?*

The division of labor is the point: `assets/audit_posture.py` finds candidate defects
deterministically (same tree in, same findings out), and the agent adjudicates each one
with the code open — a flagged `debug=True` in a test fixture is INFO; the same line in a
production entrypoint is HIGH. The tool never guesses context; you never grep by hand.

## Boundaries — what this is not

State these in the report so it cannot be over-read:

- **Not a CVE scanner.** No advisory database, no network, no version-vulnerability
  matching.
- **Not SAST.** No dataflow, taint, or injection analysis.
- **Not a secrets-content scanner.** It flags credential-*shaped files by name*
  (`.env`, `*.pem`, `id_rsa`); it never reads for secret values — pair it with a
  dedicated secrets scanner (this repo's own scan-leaks gate does that per-skill).
- **Not norms/claims verification** (that is `base-in-reality`) and **not an
  agent-readiness audit** (that is `agent-ready-rails`).

Audit only repositories the user owns or is explicitly authorized to review; if
authorization is unclear, ask before running.

## Workflow

1. **Scope with the user.** Confirm the repo path and that they are authorized to audit
   it. Agree what is in and out of scope — vendored or generated trees go to
   `--skip-dir` (`.git`, `node_modules`, `vendor`, build dirs are skipped by default) —
   and pick the `--fail-on` threshold if the exit code will gate anything.

2. **Run the deterministic sweep.** From the skill directory:

   ```bash
   python3 assets/audit_posture.py <repo> --format json --fail-on never
   ```

   This emits every draft finding as
   `{check, severity, path, line, evidence, remediation}` plus a summary and the
   `not_covered` list. Run it once more with `--format text` if a human-readable copy
   helps. Do not add findings the tool did not emit and do not drop findings silently —
   everything it found appears in the report, even if adjudicated down to INFO.

3. **Adjudicate each finding in context.** Open the flagged file at the flagged line and
   judge the draft severity against `references/checks.md` (each check's section says
   what it proves, what it cannot prove, and when to move the grade). Adjust up or down
   only with a stated reason, e.g. "debug-flag in `tests/fixtures/app.py` — fixture, not
   an entrypoint ⇒ INFO" or "credential-file `certs/server.pem` contains only a public
   cert chain ⇒ INFO". Prefer keeping the draft when the context is ambiguous.

4. **Write the posture report** (the deliverable — template below): findings grouped
   HIGH → MEDIUM → LOW → INFO, each with `file:line` evidence, its adjudication note,
   and a concrete remediation; then the **Not covered** section copied from the tool's
   `not_covered` output so nobody mistakes this for a CVE scan or pen test.

5. **Verify and repair.** For each accepted finding the user wants fixed, apply the
   remediation, then re-run step 2 and confirm that specific finding is gone (and no new
   ones appeared). Quote the before/after summary counts in the report. A fix without a
   clean re-run is not done.

## Deliverable — the posture report

```
# Security posture report: <repo>  (<n> findings: H/M/L/I after adjudication)

## HIGH
- [check-id] path:line — evidence
  adjudication: <kept draft | raised/lowered from X because ...>
  fix: <concrete remediation>

## MEDIUM / LOW / INFO
(same shape)

## Not covered by this audit
<the tool's not_covered list, verbatim: CVEs, SAST, secret values, runtime/cloud, norms>

## Verification
<re-run summary for each applied fix: finding gone, before/after counts>
```

## The checks

Thirteen check ids, each documented in `references/checks.md` (what it proves, what it
cannot prove, severity rationale, remediation pattern):

| Check | Draft severity |
|---|---|
| `dep-unpinned-python`, `dep-unpinned-node`, `docker-unpinned-base` | MEDIUM |
| `credential-file` (.env*/.pem/id_* presence, content never read) | HIGH |
| `debug-flag`, `permissive-cors`, `insecure-transport` | MEDIUM |
| `workflow-prt-checkout`, `workflow-curl-pipe-sh` | HIGH |
| `world-writable` / `setuid-file` | MEDIUM / HIGH |
| `parse-error` (unauditable manifest) | LOW |
| `missing-security-md` | INFO |

## Assets and verification

- `assets/audit_posture.py` — the deterministic scanner (stdlib-only, offline; exit code
  reflects highest severity via `--fail-on high|medium|low|info|never`).
- `assets/test_audit_posture.py` — unit suite; run standalone with
  `cd assets && python3 test_audit_posture.py`.
- `eval/run_eval.py` — outcome eval: builds hardened and leaky fixture repos in a
  tempdir and grades the audit against both, including false-positive negatives.
- `references/checks.md` — the adjudication rubric for step 3.

## Edge cases

- **Monorepos / workspaces:** a workspace-root lockfile can legitimately silence
  `dep-unpinned-node` for nested packages — verify before accepting the finding.
- **Windows trees:** file-mode checks are skipped (POSIX-only); say so in Not covered.
- **Huge files:** content checks skip files over ~1 MB; name/mode checks still apply.
- **Unreadable/malformed manifests** surface as `parse-error` entries, never crashes —
  report them as audit blind spots rather than dropping them.
