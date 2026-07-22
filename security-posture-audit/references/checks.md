# Check catalog — what each check proves, and what it cannot

Every check in `assets/audit_posture.py` is a deterministic proxy: it proves a fact about
the *tree*, not about runtime behavior. The severity column is a **draft** — the audit
workflow's adjudication step (SKILL.md step 3) adjusts it with the code open and states the
reason. This catalog is the rubric for that adjudication.

Finding shape: `{check, severity, path, line, evidence, remediation}` — `line == 0` means
the finding is about the file (or repo) as a whole.

---

## dep-unpinned-python — draft MEDIUM

**Proves:** a `requirements*.txt` line names a package without an exact `==` pin, so two
installs at different times can resolve to different code.

**Cannot prove:** whether the drift is exploitable, whether a constraints file or an outer
lockfile (pip-tools, uv, Poetry) pins it elsewhere, or that any resolvable version is
malicious. Lines starting with `-` (options, `-r` includes, editable installs) are skipped
and stay un-audited.

**Severity rationale:** unpinned deps are the entry point for dependency-confusion and
"newest release is compromised" attacks, but exploitation needs an attacker upstream —
supply-chain exposure, not an active hole. Downgrade to LOW/INFO if an outer lock layer
pins the install path; upgrade if the file feeds a privileged CI job.

**Remediation:** pin exact versions (`pkg==X.Y.Z`), ideally hash-locked via
`pip-compile --generate-hashes` or an equivalent lock-based workflow.

## dep-unpinned-node — draft MEDIUM

**Proves:** a `package.json` declares range specs (`^`, `~`, `*`, `latest`, `1.x`, …) with
**no lockfile in the same directory** — the only case where ranges decide what installs.

**Cannot prove:** that CI actually uses `npm ci` against a lockfile elsewhere (workspaces
hoist lockfiles to the root; the same-directory heuristic can miss that — verify before
accepting), or anything about the resolved packages themselves.

**Severity rationale:** same supply-chain logic as the Python check. If a workspace-root
lockfile governs installs, downgrade to INFO with that path as the stated reason.

**Remediation:** commit a lockfile (`package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`)
and install with `npm ci`, or pin exact versions.

## parse-error — draft LOW

**Proves:** a `package.json` is not valid JSON, so it could be neither audited nor
installed from as-is.

**Cannot prove:** why — it may be a template, a test fixture, or genuinely broken.

**Severity rationale:** not itself a vulnerability, but an audit blind spot the report must
not paper over. A fixture earns INFO; a live manifest that cannot parse is a build bug.

**Remediation:** fix the syntax, or exclude the directory from scope with `--skip-dir` and
record that exclusion in the report.

## docker-unpinned-base — draft MEDIUM

**Proves:** a `Dockerfile` `FROM` uses `:latest` or no tag, so the base image can change
under the build. Digest-pinned (`@sha256:…`), `scratch`, variable, and build-stage
references are not flagged.

**Cannot prove:** what the current `latest` contains, or whether the registry is trusted.

**Severity rationale:** a mutable base silently changes your runtime and defeats
reproducible builds; upgrade toward HIGH when the image ships to production, downgrade for
a dev-only container.

**Remediation:** pin `image:TAG`, ideally `image:TAG@sha256-digest`, and refresh via PRs.

## credential-file — draft HIGH

**Proves:** a credential-*shaped* file is present in the tree: `.env` / `.env.*` (except
`.example`, `.sample`, `.template`, `.dist`), `*.pem`, or an SSH private-key name
(`id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`). Presence only — content is never read.

**Cannot prove:** that the file holds a live secret. A `.pem` can be a public cert chain;
a `.env` can hold only harmless local toggles. Conversely it cannot see secrets in files
named anything else — this is **not** a secrets scanner.

**Severity rationale:** drafted HIGH because when it is real, it is credential exposure in
version history, and rotation — not deletion — is the fix. The adjudication step opens the
file: public-material-only ⇒ INFO with the reason stated; anything secret ⇒ HIGH stands,
plus history scrubbing and rotation.

**Remediation:** `git rm --cached`, rotate whatever the file held, add the pattern to
`.gitignore`, ship a `*.example` placeholder. If a secret was ever committed, treat it as
leaked (history rewrite alone does not un-leak it).

## debug-flag — draft MEDIUM

**Proves:** a Python file hardcodes `DEBUG = True` or calls `.run(... debug=True ...)`.

**Cannot prove:** whether that file is an entrypoint, a test fixture, or dead code — this
is exactly the finding the in-context adjudication exists for.

**Severity rationale:** in an entrypoint or settings module for a deployed app this is
HIGH (Flask/Werkzeug debug consoles allow code execution; Django debug pages leak
settings). In a test fixture or example it is INFO. MEDIUM is the honest prior.

**Remediation:** default debug off; enable it only via an environment variable in local
development.

## permissive-cors — draft MEDIUM

**Proves:** a wildcard CORS origin is configured (`Access-Control-Allow-Origin` with `*`,
`allow_origins=["*"]`, `origin: "*"`, `CORS_ALLOW_ALL_ORIGINS = True`).

**Cannot prove:** whether the endpoints behind it are public-by-design (a `*` on a truly
public, credential-free API is legitimate) or carry cookies/tokens.

**Severity rationale:** wildcard + credentials or private data ⇒ HIGH; wildcard on an
intentionally public read-only API ⇒ INFO with the reason stated. MEDIUM until adjudicated.

**Remediation:** replace `*` with an explicit origin allowlist scoped to the routes that
need cross-origin access.

## insecure-transport — draft MEDIUM

**Proves:** a dependency/config file (requirements, pip.conf, package.json, Dockerfile,
`*.toml/.cfg/.ini/.yml/.yaml`, `.npmrc`, …) references a plain `http://` URL (localhost and
loopback excluded), or a pip `trusted-host` directive disables TLS verification for an
index.

**Cannot prove:** whether the endpoint is reachable, redirects to https, or sits on an
isolated network. Source-code URLs are deliberately out of scope (too noisy to prove
anything from the tree).

**Severity rationale:** plain-http install/config channels allow on-path tampering with
what you install or trust; an air-gapped internal mirror may justify a downgrade, stated
explicitly.

**Remediation:** use https with valid certificates; remove `trusted-host`.

## workflow-prt-checkout — draft HIGH

**Proves:** a workflow under `.github/workflows/` uses the privileged
`pull_request_target` trigger **and** checks out the PR head
(`github.event.pull_request.head.sha|ref`) — the classic "pwn request" shape where a fork's
code runs with repo secrets and a write token.

**Cannot prove:** exploitability details — a workflow that checks out the head but runs
nothing from it, or gates on a trusted-label condition, may be defensible; verify the job
body before accepting.

**Severity rationale:** when live, this is secrets exfiltration by any fork — HIGH stands
unless the job provably never executes checked-out content.

**Remediation:** use plain `pull_request` for anything that runs PR code; if privileges
are required, split into an unprivileged build plus a privileged, artifact-consuming
follow-up (`workflow_run`).

## workflow-curl-pipe-sh — draft HIGH

**Proves:** a workflow pipes `curl`/`wget` output straight into a shell — unverified
remote code execution inside CI, where tokens and secrets live.

**Cannot prove:** the trustworthiness of the URL or whether the endpoint is
version-pinned; an org-internal, checksummed bootstrap may earn a downgrade with the
reason stated.

**Remediation:** download to a file, verify a checksum or signature, then execute — or use
a pinned setup action instead.

## world-writable — draft MEDIUM / setuid-file — draft HIGH

**Proves:** a tracked file's POSIX mode has the world-writable bit (`o+w`), or a
setuid/setgid bit, set.

**Cannot prove:** anything on non-POSIX filesystems (the check is skipped there), or
whether git will preserve the bit for other cloners (git tracks only the executable bit —
setuid observed locally usually means the *working copy* was altered, which is itself worth
investigating).

**Severity rationale:** world-writable invites local tampering (MEDIUM); a setuid binary
in a repo is a privilege-escalation vector with no legitimate reason to be committed
(HIGH).

**Remediation:** `chmod o-w <file>`; `chmod u-s,g-s <file>` and investigate how the bit
got there.

## missing-security-md — INFO

**Proves:** no `SECURITY.md` at the repo root, `.github/`, or `docs/`.

**Cannot prove:** that no disclosure channel exists elsewhere (an org-level policy file
covers all repos — check before reporting it as a gap).

**Severity rationale:** process hygiene, not a vulnerability — permanently INFO.

**Remediation:** add a `SECURITY.md` naming the disclosure contact and supported versions.
