#!/usr/bin/env python3
"""okf-capability-catalog — externalise capability contracts and cross-team
dependencies into an OKF v0.1 bundle that humans and agents can both read.

An unwritten dependency is an accepted risk, whether or not you meant to
accept it. This CLI writes the things that would otherwise live only in
someone's head — what a team offers, what another team is waiting on, by
when, what breaks if it is late, and what the fallback is — into plain
markdown in git, and computes the parts nobody should be typing by hand
(effective readiness, the point of no return, trip propagation).

Modes:

    init      scaffold the bundle (once per organisation)
    annotate  scan a service repo: Service, Capability, detected edges
    declare   consumer records a dependency (state: proposed)
    ack       provider commits a date (state: acknowledged)
    confirm   consumer re-confirms after a date slip
    on-track  provider confirms an edge is on track before its PONR
    risk      provider flags an edge at risk
    resolve   record the forced decision on a tripped edge
    verify    consumer records acceptance (the only source of consumer_verified)
    signal    CI/monitoring records liveness (the only source of production_live)
    readiness effective readiness + depth for a capability in an environment
    review    what is: inbound/outbound/directory/timeline views
    audit     what is wrong: seams at risk
    validate  OKF conformance (hard) + catalog policy (soft)
    enforce   commit-boundary authority checks (§8.1) over a changeset
    codeowners regenerate CODEOWNERS from the teams in the bundle

Stdlib only, offline, deterministic: pass --now/--today to pin the clock.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from catalog_core import (  # noqa: E402
    Catalog, DEFAULT_CONFIG, MANAGED_STATES, OKF_SPEC_VERSION, SCANNER_VERSION,
    TERMINAL_STATES, audit, can_satisfy, closure, closure_coverage, dump_yaml,
    effective_readiness, effective_state, enforce as enforce_changes, hard_requires,
    impossible_promise, iso, norm_link, own_readiness, parse_date, parse_frontmatter,
    parse_ts, parse_yaml, point_of_no_return, readiness_rank, render_doc, slugify,
    validate as validate_bundle,
)

# Provenance stamps the scanner rewrites on every run. They are ignored when
# deciding whether a document actually changed, so re-running a scan on an
# unchanged commit is a genuine no-op rather than a timestamp churn.
VOLATILE_KEYS = {"timestamp", "scanned_at", "asserted_at", "emitted_at",
                 "attested_at", "claimed_at"}
BLANK_ANSWERS = {"tbd", "t.b.d.", "n/a", "na", "none", "unknown", "?", "-", "tba"}


# ── small output helpers ─────────────────────────────────────────────────────

def out(line=""):
    print(line)


def refuse(result_key, message, hint=""):
    out(f"REFUSED: {message}")
    if hint:
        out(f"HINT: {hint}")
    out(f"{result_key}: REFUSED")
    return 2


def now_of(args):
    if getattr(args, "now", None):
        ts = parse_ts(args.now)
        if ts:
            return ts
    return _dt.datetime.now(_dt.timezone.utc)


def today_of(args):
    if getattr(args, "today", None):
        day = parse_date(args.today)
        if day:
            return day
    if getattr(args, "now", None):
        ts = parse_ts(args.now)
        if ts:
            return ts.date()
    return _dt.datetime.now(_dt.timezone.utc).date()


# ── filesystem writers ───────────────────────────────────────────────────────

def _strip_volatile(value):
    if isinstance(value, dict):
        return {k: _strip_volatile(v) for k, v in sorted(value.items())
                if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


def write_doc(root, rel, meta, body="", log=None, event=None):
    """Write a concept document. Idempotent: when the semantic content is
    unchanged, the file is left exactly as it was (timestamps included), so a
    re-scan of an unchanged commit produces an empty diff."""
    path = os.path.join(root, rel)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            old_meta, old_body, _ = parse_frontmatter(fh.read())
        if _strip_volatile(old_meta) == _strip_volatile(meta) and (old_body or "").strip() == (body or "").strip():
            out(f"UNCHANGED: {rel}")
            return False
        verb = "updated"
    else:
        verb = "created"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_doc(meta, body))
    out(f"WROTE: {rel} ({verb})")
    if log is not None and event:
        log.append(event)
    return True


def write_text(root, rel, text):
    path = os.path.join(root, rel)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text:
                return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return True


def append_log(root, events, now):
    """Record creation/update events in log.md (OKF §7). Idempotent per day."""
    if not events:
        return
    path = os.path.join(root, "log.md")
    text = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    if not text.strip():
        text = "# Log\n"
    day = now.date().isoformat()
    header = f"## {day}"
    if header not in text:
        text = text.rstrip("\n") + f"\n\n{header}\n"
    lines = text.split("\n")
    idx = lines.index(header) + 1
    end = idx
    while end < len(lines) and not lines[end].startswith("## "):
        end += 1
    existing = set(lines[idx:end])
    fresh = [f"* {e}" for e in events if f"* {e}" not in existing]
    if not fresh:
        return
    block = lines[idx:end]
    while block and not block[-1].strip():
        block.pop()
    lines[idx:end] = ([""] if not block else block) + fresh + [""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip("\n") + "\n")
    out(f"LOGGED: {len(fresh)} event(s)")


def regenerate_indexes(root):
    """Regenerate the generated index.md listings (OKF §6 progressive disclosure)."""
    cat = Catalog.load(root)
    groups = {}
    for rel, doc in sorted(cat.docs.items()):
        parent = os.path.dirname(rel)
        groups.setdefault(parent, []).append(doc)
    written = 0
    for parent, docs in sorted(groups.items()):
        if not parent:
            continue
        title = parent.split("/")[-1].replace("-", " ").title()
        lines = [f"# {title}", ""]
        for doc in docs:
            name = os.path.basename(doc.relpath)
            label = doc.get("title") or name
            desc = doc.get("description") or doc.get("state") or doc.type or ""
            lines.append(f"* [{label}]({name}) — {desc}" if desc else f"* [{label}]({name})")
        subdirs = sorted({p.split("/")[len(parent.split("/"))] for p in groups
                          if p.startswith(parent + "/")})
        for sub in subdirs:
            lines.append(f"* [{sub.replace('-', ' ').title()}]({sub}/index.md)")
        if write_text(root, os.path.join(parent, "index.md"), "\n".join(lines) + "\n"):
            written += 1
    top = sorted({p.split("/")[0] for p in groups if p})
    for section in top:
        index_rel = f"{section}/index.md"
        if os.path.isfile(os.path.join(root, index_rel)):
            continue
        lines = [f"# {section.title()}", ""]
        for sub in sorted({p.split('/')[1] for p in groups
                           if p.startswith(section + "/") and len(p.split("/")) > 1}):
            lines.append(f"* [{sub}]({sub}/index.md)")
        if write_text(root, index_rel, "\n".join(lines) + "\n"):
            written += 1
    if written:
        out(f"INDEXES: regenerated {written}")


def team_link(team_id):
    return f"/teams/{team_id}/team.md"


def team_id_of(link):
    return re.sub(r"^teams/|/team\.md$", "", norm_link(link))


def load_catalog(args):
    root = os.path.abspath(args.bundle)
    if not os.path.isdir(root):
        out(f"REFUSED: bundle not found: {root}")
        return None
    return Catalog.load(root)


# ─────────────────────────────────────────────────────────────────────────────
# init
# ─────────────────────────────────────────────────────────────────────────────

README_TEMPLATE = """# {org} capability catalog

An [Open Knowledge Format]({spec}) bundle: plain markdown in git, readable by
people and by agents. It records what each team **offers** other teams, what each
team is **waiting on**, and — for every seam between two teams — the date, the
consequence of missing it, and the fallback.

**If it is not in a file here, it does not exist.** An unwritten dependency is an
accepted risk, whether or not anyone meant to accept it.

## The two layers

* **Capability** — the stable interface one team offers another ("initiate a
  refund"). It survives re-implementation, so teams converse in capabilities.
* **Service** — the repo, runtime and topics that fulfil it. Volatile: when a
  service migrates ECS→EKS only the Service document changes.
* **Dependency** — the directed edge from a consuming team to a provider's
  capability. It lives in `dependencies/` and not under either team, because an
  edge has two owners and filing it under one implies unilateral ownership.

## Readiness is a grid, not a boolean

Code maturity (`feature` | `integration` | `release`) and deployment reality
(`unknown` | `provider_tested` | `consumer_verified` | `production_live`) are
independent. `release` never implies deployed; `provider_tested` never implies it
works for you. Each state lives in a document owned by whoever may assert it:

| State | Lives in | Asserted by |
|---|---|---|
| `provider_tested` | the Capability | the providing team |
| `consumer_verified` | a Verification under the **consumer's** folder | the consuming team |
| `production_live` | a Signal under `signals/` | CI or monitoring only |

Effective readiness is **computed** across all three and across the capability's
hard-requires closure, and it carries a *depth*: `complete` only when every
capability in that closure has attested its own upstreams. `unknown` depth never
renders as green — partial information presented without its own limits
manufactures confidence.

## The rule that makes it work

> A work item that touches another team's system cannot enter *in progress*
> until a Dependency document exists in state `acknowledged`.

The point of no return (`promised_date − fallback.execution_days`) is computed
backwards from the deadline. When it passes without an on-track confirmation the
edge **trips** on its own, and a tripped edge may not stay tripped: someone named
records satisfied, fallback_invoked, or renegotiated.

## Worked example

A capability file (`teams/payments/capabilities/initiate-refund.md`):

```yaml
---
type: Capability
title: Initiate Refund
capability_id: payments/initiate-refund
owning_team: /teams/payments/team.md
lifecycle: active
requires:
  - capability: /teams/ledger/capabilities/post-entry.md
    criticality: hard
upstream:
  attested: true
  attested_by: <handle>
code_maturity: {{state: integration, branch: {integration}}}
readiness:
  - environment: staging
    state: provider_tested
---
```

A dependency edge (`dependencies/dep-2026-08-001.md`) adds `requested_date`,
`promised_date`, `consequence_if_late`, `fallback`, and both acknowledgements.

## Everyday commands

```bash
okf_catalog.py annotate . --repo ../payment-orchestrator   # scan a service repo
okf_catalog.py declare  . --consumer checkout --capability payments/initiate-refund ...
okf_catalog.py ack      . --dependency dep-2026-08-001 --team payments --promised-date ...
okf_catalog.py verify   . --team checkout  --capability payments/initiate-refund ...
okf_catalog.py review   . --team checkout                  # what is
okf_catalog.py audit    .                                  # what is wrong
```

Teams are **self-authoring**: no team appears here until it claims itself, and a
`stub` team created by someone else can never acknowledge a dependency. That is
deliberate — it forces someone to go and talk to that team.
"""


def cmd_init(args):
    root = os.path.abspath(args.bundle)
    if os.path.isdir(root) and os.path.isfile(os.path.join(root, "catalog.config.yaml")) \
            and not args.force:
        return refuse("INIT_RESULT", f"{root} already holds a catalog",
                      "pass --force to rewrite the skeleton (documents are never touched)")
    now = now_of(args)
    envs = []
    live = {e.strip() for e in (args.live_signal_env or "").split(",") if e.strip()}
    for name in [e.strip() for e in args.environments.split(",") if e.strip()]:
        entry = {"name": name}
        if name in live:
            entry["requires_live_signal"] = True
        envs.append(entry)
    config = {
        "okf_version": OKF_SPEC_VERSION,
        "organization": args.org,
        "branches": {"integration": args.integration, "release": args.release},
        "environments": envs,
        "capability_id_scheme": "<team>/<slug>",
        "default_fallback_execution_days": args.default_fallback_days,
        "on_track_window_days": DEFAULT_CONFIG["on_track_window_days"],
        "ponr_warning_days": DEFAULT_CONFIG["ponr_warning_days"],
        "stale_assertion_days": DEFAULT_CONFIG["stale_assertion_days"],
    }
    if args.platform_team:
        config["platform_team"] = args.platform_team

    os.makedirs(root, exist_ok=True)
    write_text(root, "catalog.config.yaml", dump_yaml(config))
    write_text(root, "index.md",
               f'---\nokf_version: "{OKF_SPEC_VERSION}"\n---\n\n'
               f"# {args.org} capability catalog\n\n"
               "What each team offers, what each team is waiting on, and what happens if\n"
               "it is late. See README.md for the model and the assertion rules.\n\n"
               "* [Teams](teams/index.md) — self-authoring team documents, capabilities, services\n"
               "* [Dependencies](dependencies/index.md) — the managed seams between teams\n"
               "* [Signals](signals/index.md) — machine-written liveness observations\n")
    write_text(root, "log.md", f"# Log\n\n## {now.date().isoformat()}\n\n"
                               "* Catalog initialised.\n")
    # Teams start EMPTY on purpose: a fabricated team is indistinguishable from a
    # real one to an agent traversing the bundle, and pollutes the one thing that
    # has to be trustworthy. Worked examples live in README.md instead.
    write_text(root, "teams/index.md",
               "# Teams\n\nEmpty until a team claims itself by running `annotate` in a "
               "repository it owns.\n")
    write_text(root, "dependencies/index.md",
               "# Dependencies\n\nOne file per seam between two teams. An edge does not "
               "exist until both sides have acknowledged it.\n")
    write_text(root, "signals/index.md",
               "# Signals\n\nMachine-written only (CI or monitoring). The sole source of "
               "`production_live`.\n")
    write_text(root, "README.md", README_TEMPLATE.format(
        org=args.org, integration=args.integration,
        spec="https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md"))
    out(f"BUNDLE: {root}")
    out(f"ENVIRONMENTS: {', '.join(e['name'] for e in envs)}")
    out(f"BRANCHES: integration={args.integration} release={args.release}")
    out("TEAMS: empty — teams are self-authoring; run `annotate` from a service repo")
    out("INIT_RESULT: OK")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# annotate — the scanner
# ─────────────────────────────────────────────────────────────────────────────

IAC_ECS = re.compile(r'aws_ecs_(service|task_definition)|"ECS"|\becs_cluster\b')
IAC_EKS = re.compile(r"aws_eks_|kind:\s*Deployment|apiVersion:\s*apps/v1|\beks_cluster\b")
SCAN_EXCLUDE = {".git", "node_modules", "__pycache__", "vendor", "dist", "build", ".venv"}


def read_repo_yaml(repo, *names):
    for name in names:
        path = os.path.join(repo, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return parse_yaml(fh.read()), name
    return {}, None


def repo_files(repo, limit=4000):
    found = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(d for d in dirnames if d not in SCAN_EXCLUDE)
        for name in sorted(filenames):
            found.append(os.path.join(dirpath, name))
            if len(found) >= limit:
                return found
    return found


def infer_interfaces(repo):
    """Fallback inference — a *proposal*, never authority. Reads OpenAPI and
    AsyncAPI documents for the interfaces a service exposes and consumes."""
    consumes, produces, sources = [], [], []
    for path in repo_files(repo):
        base = os.path.basename(path).lower()
        if not base.endswith((".yaml", ".yml", ".json")):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read(200000)
        except OSError:
            continue
        rel = os.path.relpath(path, repo)
        if base.endswith(".json"):
            try:
                data = json.loads(text)
            except (ValueError, TypeError):
                continue
        else:
            if "openapi:" not in text and "asyncapi:" not in text:
                continue
            data = parse_yaml(text)
        if not isinstance(data, dict):
            continue
        if data.get("openapi") and isinstance(data.get("paths"), dict):
            for route, ops in sorted(data["paths"].items()):
                methods = sorted(k.upper() for k in (ops or {})
                                 if isinstance(ops, dict) and k.lower() in
                                 ("get", "post", "put", "patch", "delete"))
                for method in methods or ["GET"]:
                    produces.append({"kind": "http", "name": f"{method} {route}"})
            sources.append(rel)
        if data.get("asyncapi") and isinstance(data.get("channels"), dict):
            for channel, spec in sorted(data["channels"].items()):
                spec = spec if isinstance(spec, dict) else {}
                if "publish" in spec:
                    produces.append({"kind": "kafka_topic", "name": channel})
                if "subscribe" in spec:
                    consumes.append({"kind": "kafka_topic", "name": channel})
            sources.append(rel)
    return consumes, produces, sources


def infer_runtimes(repo, env_names):
    """Per-environment platform from IaC. Per environment, because a platform
    split between environments stays invisible unless it is recorded per
    environment — and the environment nobody exercised is the risky one."""
    found = {}
    for path in repo_files(repo):
        if not path.endswith((".tf", ".tfvars", ".yaml", ".yml", ".json")):
            continue
        rel = os.path.relpath(path, repo)
        segments = re.split(r"[/_.\-]", rel.lower())
        envs = [e for e in env_names if e.lower() in segments]
        if not envs:
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read(200000)
        except OSError:
            continue
        platform = "ECS" if IAC_ECS.search(text) else ("EKS" if IAC_EKS.search(text) else None)
        if not platform:
            continue
        for env in envs:
            found.setdefault(env, {"environment": env, "platform": platform,
                                   "identifier": rel, "method": "scan"})
    return [found[e] for e in env_names if e in found]


def resolve_team(cat, repo, explicit):
    """Ownership from .okf/team.yaml, else CODEOWNERS, else the caller."""
    declared, _ = read_repo_yaml(repo, ".okf/team.yaml", ".okf/team.yml")
    if explicit:
        declared = dict(declared or {})
        declared["team_id"] = explicit
        return declared
    if declared.get("team_id"):
        return declared
    for candidate in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"):
        path = os.path.join(repo, candidate)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                owners = [tok for tok in line.split()[1:] if tok.startswith("@")]
                if owners:
                    return {"team_id": owners[0].split("/")[-1].lstrip("@")}
    return {}


def cmd_annotate(args):
    cat = load_catalog(args)
    if cat is None:
        out("SCAN_RESULT: FAIL")
        return 1
    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        return refuse("SCAN_RESULT", f"repository not found: {repo}")
    now = now_of(args)
    root = cat.root
    log, human_todo = [], []

    branches = cat.config.get("branches") or {}
    integration, release = branches.get("integration"), branches.get("release")
    branch = args.branch or git_branch(repo)
    if not branch:
        return refuse("SCAN_RESULT", "cannot determine which branch to scan",
                      f"pass --branch (the catalog's integration branch is '{integration}')")
    maturity = ("release" if branch == release else
                "integration" if branch == integration else "feature")
    out(f"BRANCH: {branch} → code_maturity '{maturity}'")

    team = resolve_team(cat, repo, args.team)
    team_id = team.get("team_id")
    if not team_id:
        return refuse("SCAN_RESULT", "cannot determine which team owns this repository",
                      "add .okf/team.yaml (team_id, title, lead, channel) or pass --team")

    declared, source = read_repo_yaml(repo, ".okf/capabilities.yaml", ".okf/capabilities.yml")
    consumer_decl, _ = read_repo_yaml(repo, ".okf/consumer.yml", ".okf/consumer.yaml")
    svc_decl = declared.get("service") if isinstance(declared.get("service"), dict) else {}
    service_id = svc_decl.get("id") or os.path.basename(repo.rstrip("/"))
    service_slug = slugify(service_id)
    service_rel = f"teams/{team_id}/services/{service_slug}.md"

    inf_consumes, inf_produces, inf_sources = infer_interfaces(repo)
    interfaces = declared.get("interfaces") if isinstance(declared.get("interfaces"), dict) else {}
    consumes = interfaces.get("consumes") or inf_consumes
    produces = interfaces.get("produces") or inf_produces
    runtimes = declared.get("runtimes") or infer_runtimes(repo, cat.env_names())
    if source:
        out(f"DECLARED: {source} is authoritative for capabilities and interfaces")
    else:
        out("INFERRED: no .okf/capabilities.yaml — interfaces inferred as proposals only"
            + (f" (from {', '.join(inf_sources)})" if inf_sources else ""))
        human_todo.append(f"write .okf/capabilities.yaml in {service_id} — inference is a "
                          "proposal, a hand-written declaration by the owning team always wins")

    caps = declared.get("capabilities") or []
    if maturity == "feature":
        # A capability that exists only on a feature branch is a proposal, not a
        # capability. Report, write nothing.
        out(f"PROPOSAL_ONLY: '{branch}' is neither the integration branch "
            f"('{integration}') nor the release branch ('{release}')")
        for cap in caps:
            cap_id = full_cap_id(team_id, cap)
            out(f"PROPOSAL: {cap_id} (would be created once merged to {integration})")
        out(f"PROPOSAL: service {service_id} (not written from a feature branch)")
        out("SCAN_RESULT: PROPOSAL_ONLY")
        return 0

    # ── team ────────────────────────────────────────────────────────────────
    existing_team = cat.team_doc(team_id)
    team_rel = f"teams/{team_id}/team.md"
    team_meta = dict(existing_team.meta) if existing_team else {}
    was_stub = bool(existing_team) and existing_team.get("provenance") == "stub"
    team_meta.update({
        "type": "Team",
        "title": team.get("title") or team_meta.get("title") or team_id.replace("-", " ").title(),
        "team_id": team_id,
        "provenance": "claimed",
        "claimed_by": team.get("lead") or args.by or team_meta.get("claimed_by") or team_id,
        "claimed_at": team_meta.get("claimed_at") or iso(now),
        "timestamp": iso(now),
    })
    if team.get("description"):
        team_meta["description"] = team["description"]
    contacts = dict(team_meta.get("contacts") or {})
    if team.get("lead"):
        contacts["lead"] = team["lead"]
    if team.get("channel"):
        contacts["channel"] = team["channel"]
    if contacts:
        team_meta["contacts"] = contacts
    repos = list(team_meta.get("repositories") or [])
    repo_url = svc_decl.get("repository") or team.get("repository")
    if repo_url and repo_url not in repos:
        repos.append(repo_url)
    if repos:
        team_meta["repositories"] = repos
    write_doc(root, team_rel, team_meta, existing_team.body if existing_team else "",
              log, f"Team {team_id} claimed itself." if not existing_team or was_stub else None)
    if was_stub:
        # Promotion stub -> claimed. Inbound links point at this same path, so
        # they survive by construction; never recreate the folder.
        out(f"PROMOTED: stub team '{team_id}' claimed (inbound links preserved)")

    # ── capabilities ────────────────────────────────────────────────────────
    cap_paths, declared_requires = [], {}
    for cap in caps:
        if not isinstance(cap, dict):
            continue
        cap_id = full_cap_id(team_id, cap)
        slug = slugify(cap_id.split("/", 1)[1])
        rel = f"teams/{team_id}/capabilities/{slug}.md"
        cap_paths.append(rel)
        declared_requires[rel] = [norm_link(r.get("capability")) for r in (cap.get("requires") or [])
                                  if isinstance(r, dict) and r.get("capability")]
        existing = cat.docs.get(rel)
        code_maturity = {"state": maturity, "branch": branch, "method": "scan",
                         "asserted_at": iso(now)}
        commit = args.commit or git_commit(repo)
        if commit:
            code_maturity["commit"] = commit
        if existing is not None:
            # Machine fields only. Contract changes are a human review, never a
            # silent overwrite of someone's authored interface.
            meta = dict(existing.meta)
            meta["code_maturity"] = code_maturity
            fulfilled = list(meta.get("fulfilled_by") or [])
            if f"/{service_rel}" not in fulfilled:
                fulfilled.append(f"/{service_rel}")
            meta["fulfilled_by"] = fulfilled
            meta["timestamp"] = iso(now)
            if contract_differs(existing, cap):
                human_todo.append(f"{cap_id}: the declared contract in {source} differs from "
                                  "the catalog — review and update the capability by hand")
            write_doc(root, rel, meta, existing.body, log, None)
        else:
            meta = {
                "type": "Capability",
                "title": cap.get("title") or slug.replace("-", " ").title(),
                "description": cap.get("description") or "",
                "capability_id": cap_id,
                "owning_team": team_link(team_id),
                # Only a human promotes a capability to active.
                "lifecycle": "proposed",
                "inputs": cap.get("inputs") or [],
                "outputs": cap.get("outputs") or [],
                "constraints": cap.get("constraints") or [],
                "fulfilled_by": [f"/{service_rel}"],
                "requires": normalise_requires(cap.get("requires")),
                "upstream": {"attested": False, "scanner_agreement": True},
                "code_maturity": code_maturity,
                "readiness": [{"environment": env, "state": "unknown"}
                              for env in cat.env_names()],
                "timestamp": iso(now),
            }
            body = ("# Contract\n\n<error semantics, sequence, notes>\n\n# Consumers\n\n"
                    "# Citations\n")
            write_doc(root, rel, meta, body, log,
                      f"Capability {cap_id} proposed by the scanner.")
            human_todo.append(f"{cap_id}: promote lifecycle proposed → active once the owning "
                              "team has reviewed the contract")

    # ── service ─────────────────────────────────────────────────────────────
    existing_svc = cat.docs.get(service_rel)
    svc_meta = {
        "type": "Service",
        "title": svc_decl.get("title") or service_id.replace("-", " ").title(),
        "description": svc_decl.get("description") or (existing_svc.get("description")
                                                       if existing_svc else ""),
        "service_id": f"{team_id}/{service_slug}",
        "owning_team": team_link(team_id),
        "repository": svc_decl.get("repository") or (existing_svc.get("repository")
                                                     if existing_svc else ""),
        "fulfils": [f"/{p}" for p in cap_paths],
        "runtimes": runtimes,
        "interfaces": {"consumes": consumes, "produces": produces},
        "scan": {"branch": branch, "scanned_at": iso(now),
                 "scanner_version": SCANNER_VERSION,
                 "source": source or "inference"},
        "timestamp": iso(now),
    }
    commit = args.commit or git_commit(repo)
    if commit:
        svc_meta["scan"]["commit"] = commit
    write_doc(root, service_rel, {k: v for k, v in svc_meta.items() if v not in ("", None)},
              existing_svc.body if existing_svc else "", log,
              f"Service {service_id} scanned on {branch}.")

    platforms = {r.get("platform") for r in runtimes if isinstance(r, dict) and r.get("platform")}
    if len(platforms) > 1:
        detail = ", ".join(f"{r['environment']}={r['platform']}" for r in runtimes
                           if isinstance(r, dict) and r.get("platform"))
        out(f"WARN: {service_id} runs on different platforms per environment ({detail}). "
            "Tested on one is not tested on the other")

    # ── drift ───────────────────────────────────────────────────────────────
    for rel, cap in sorted(cat.capabilities.items()):
        fulfilled = [norm_link(f) for f in (cap.get("fulfilled_by") or [])]
        if service_rel not in fulfilled or rel in cap_paths:
            continue
        meta = dict(cap.meta)
        if not meta.get("drift_detected"):
            meta["drift_detected"] = True
            meta["timestamp"] = iso(now)
            write_doc(root, rel, meta, cap.body, log,
                      f"Drift: {cap.get('capability_id')} no longer exposed by {service_id}.")
            out(f"DRIFT: {cap.get('capability_id')} is no longer exposed by {service_id} "
                "— marked, not deleted")

    # ── cross-team edges + requires proposals ───────────────────────────────
    cat = Catalog.load(root)
    detected = detect_edges(cat, root, team_id, consumes, consumer_decl, now, log)
    proposed_requires = propose_requires(cat, root, cap_paths, detected, declared_requires,
                                         now, log)

    attested = None
    if args.attest_upstream:
        attested = args.attest_upstream == "yes"
    if attested is not None or proposed_requires:
        cat = Catalog.load(root)
        for rel in cap_paths:
            cap = cat.docs.get(rel)
            if cap is None:
                continue
            meta = dict(cap.meta)
            upstream = dict(meta.get("upstream") or {})
            declared_set = set(declared_requires.get(rel) or [])
            undeclared = [d for d in proposed_requires.get(rel, []) if d not in declared_set]
            upstream["scanner_agreement"] = not undeclared
            if attested is not None:
                upstream["attested"] = attested
                if attested:
                    upstream["attested_by"] = args.by or team_id
                    upstream["attested_at"] = iso(now)
            meta["upstream"] = upstream
            meta["timestamp"] = iso(now)
            write_doc(root, rel, meta, cap.body, log, None)
    if attested is None:
        human_todo.append(f"ask {team_id}: is the declared `requires` the COMPLETE list of "
                          "other teams' capabilities these depend on? Re-run with "
                          "--attest-upstream yes|no — until then depth stays 'unknown'")

    regenerate_indexes(root)
    append_log(root, log, now)

    if human_todo:
        out("")
        out("LEFT FOR HUMANS (the scanner is not entitled to assert these):")
        for item in human_todo:
            out(f"  - {item}")
    out("NOTE: the scanner never writes consumer_verified or production_live — those are "
        "cross-team and traffic facts that code cannot prove")
    if detected:
        out(f"NEXT: run `declare` on {len(detected)} unmanaged edge(s) — an integration "
            "that exists in code with nobody managing it is risk nobody has priced")
    out(f"SCAN_RESULT: OK ({len(cap_paths)} capability/ies, {len(detected)} detected edge(s))")
    return 0


def full_cap_id(team_id, cap):
    cap_id = str(cap.get("id") or slugify(cap.get("title") or "capability"))
    return cap_id if "/" in cap_id else f"{team_id}/{cap_id}"


def normalise_requires(requires):
    out_list = []
    for req in requires or []:
        if not isinstance(req, dict) or not req.get("capability"):
            continue
        entry = {"capability": req["capability"], "criticality": req.get("criticality", "hard")}
        if req.get("note"):
            entry["note"] = req["note"]
        out_list.append(entry)
    return out_list


def contract_differs(existing, declared):
    def names(items):
        return sorted((i or {}).get("name", "") for i in items or [] if isinstance(i, dict))
    return (names(existing.get("inputs")) != names(declared.get("inputs"))
            or names(existing.get("outputs")) != names(declared.get("outputs")))


def producing_capability(cat, kind, name, exclude_team):
    """Which other team's capability produces this interface?"""
    for rel, svc in sorted(cat.services.items()):
        owner = cat.team_of_doc(svc)
        if owner == exclude_team:
            continue
        produces = (svc.get("interfaces") or {}).get("produces") or []
        for entry in produces:
            if not isinstance(entry, dict):
                continue
            if entry.get("name") == name and (entry.get("kind") == kind or not kind):
                fulfils = [norm_link(f) for f in svc.get("fulfils") or []]
                for cap_rel in fulfils:
                    if cap_rel in cat.docs:
                        return owner, cat.docs[cap_rel], svc
                return owner, None, svc
    return None, None, None


def next_dep_id(cat, now):
    prefix = f"dep-{now.year:04d}-{now.month:02d}-"
    used = {d.get("dependency_id", "") for d in cat.by_type("Dependency")}
    seq = 1
    while f"{prefix}{seq:03d}" in used:
        seq += 1
    return f"{prefix}{seq:03d}"


def existing_edge(cat, consumer_team, cap_rel):
    for dep in cat.by_type("Dependency"):
        if (team_id_of(dep.get("consumer_team")) == consumer_team
                and norm_link(dep.get("capability")) == cap_rel):
            return dep
    return None


def detect_edges(cat, root, team_id, consumes, consumer_decl, now, log):
    """Write `detected` edges for cross-team wiring that nothing manages.
    No dates, no consequences — those are not the scanner's to invent."""
    detected = []
    wanted = []
    for entry in consumes or []:
        if isinstance(entry, dict) and entry.get("name"):
            owner, cap, svc = producing_capability(cat, entry.get("kind"), entry["name"], team_id)
            if owner and cap is not None:
                wanted.append((owner, cap, f"{entry.get('kind')} {entry['name']}"))
    for entry in (consumer_decl or {}).get("consumes") or []:
        if not isinstance(entry, dict) or not entry.get("capability"):
            continue
        cap = cat.resolve_capability(entry["capability"])
        if cap is None:
            out(f"WARN: .okf/consumer.yml names {entry['capability']}, which is not in the "
                "catalog yet — run `declare` to record the ask and stub the provider")
            continue
        owner = cat.team_of_doc(cap)
        if owner and owner != team_id:
            wanted.append((owner, cap, ".okf/consumer.yml"))

    seen = set()
    for owner, cap, via in wanted:
        if cap.relpath in seen:
            continue
        seen.add(cap.relpath)
        if existing_edge(cat, team_id, cap.relpath) is not None:
            continue
        dep_id = next_dep_id(cat, now)
        rel = f"dependencies/{dep_id}.md"
        meta = {
            "type": "Dependency",
            "title": f"{team_id} → {cap.get('capability_id')}",
            "dependency_id": dep_id,
            "consumer_team": team_link(team_id),
            "provider_team": team_link(owner),
            "capability": f"/{cap.relpath}",
            "detected_via": via,
            "state": "detected",
            "timestamp": iso(now),
        }
        body = ("# Detected\n\nThe scanner found this integration in code with no document "
                "managing it. It has no date, no consequence and no fallback because those "
                "are not machine-knowable — run `declare` to turn it into a managed edge.\n")
        write_doc(root, rel, meta, body, log, f"Detected unmanaged edge {dep_id} ({via}).")
        out(f"DETECTED: {dep_id} {team_id} → {cap.get('capability_id')} via {via}")
        detected.append((dep_id, cap.relpath))
        cat = Catalog.load(root)
    return detected


def propose_requires(cat, root, cap_paths, detected, declared_requires, now, log):
    """A consumed cross-team interface is an upstream of the capabilities this
    service fulfils. Declaring upstreams once, here, is what lets the graph do
    transitive reasoning for every downstream consumer."""
    proposals = {}
    upstreams = [cap_rel for _, cap_rel in detected]
    if not upstreams:
        return proposals
    for rel in cap_paths:
        cap = cat.docs.get(rel)
        if cap is None:
            continue
        proposals[rel] = list(upstreams)
        meta = dict(cap.meta)
        requires = list(meta.get("requires") or [])
        have = {norm_link(r.get("capability")) for r in requires if isinstance(r, dict)}
        added = []
        for up in upstreams:
            if up in have:
                continue
            requires.append({"capability": f"/{up}", "criticality": "hard",
                             "note": "proposed by scan from a consumed cross-team interface"})
            added.append(up)
        if added:
            meta["requires"] = requires
            meta["timestamp"] = iso(now)
            write_doc(root, rel, meta, cap.body, log,
                      f"Proposed upstreams for {cap.get('capability_id')}.")
            out(f"REQUIRES: proposed {len(added)} upstream(s) on {cap.get('capability_id')}")
    return proposals


def git_branch(repo):
    head = os.path.join(repo, ".git", "HEAD")
    if not os.path.isfile(head):
        return None
    with open(head, encoding="utf-8") as fh:
        text = fh.read().strip()
    m = re.match(r"^ref:\s*refs/heads/(.+)$", text)
    return m.group(1) if m else None


def git_commit(repo):
    head = os.path.join(repo, ".git", "HEAD")
    if not os.path.isfile(head):
        return None
    with open(head, encoding="utf-8") as fh:
        text = fh.read().strip()
    m = re.match(r"^ref:\s*(.+)$", text)
    if not m:
        return text[:7] if re.match(r"^[0-9a-f]{7,40}$", text) else None
    ref_path = os.path.join(repo, ".git", m.group(1))
    if os.path.isfile(ref_path):
        with open(ref_path, encoding="utf-8") as fh:
            return fh.read().strip()[:7]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# declare — the consumer interview's output
# ─────────────────────────────────────────────────────────────────────────────

def blankish(text):
    return not text or str(text).strip().lower() in BLANK_ANSWERS


def cmd_declare(args):
    cat = load_catalog(args)
    if cat is None:
        out("DECLARE_RESULT: REFUSED")
        return 2
    now, today = now_of(args), today_of(args)
    root = cat.root

    if args.promised_date:
        return refuse("DECLARE_RESULT",
                      "a consumer may not set promised_date — that is the provider's "
                      "commitment, and merging the two destroys the gap between the ask "
                      "and the promise",
                      "record the ask with --requested-date; the provider runs `ack`")
    if blankish(args.consequence):
        return refuse("DECLARE_RESULT",
                      "consequence_if_late is blank or 'TBD'. A blank field silently "
                      "becomes an accepted risk",
                      "answer: if it is not there on that date, what actually happens — "
                      "who feels it, and how badly?")
    if not args.fallback and not args.no_fallback:
        return refuse("DECLARE_RESULT", "no fallback and no rationale for having none",
                      "give --fallback \"<degraded mode>\" --fallback-days N, or "
                      "--no-fallback \"<why none is possible>\" — which escalates rather "
                      "than disappearing")
    if args.fallback and not args.fallback_days:
        return refuse("DECLARE_RESULT",
                      "a fallback with no execution time cannot produce a point of no return",
                      "how long would it take to stand that fallback up, once decided?")

    consumer = args.consumer
    if not cat.team_is_claimed(consumer):
        return refuse("DECLARE_RESULT",
                      f"'{consumer}' has not claimed itself in this catalog",
                      "run `annotate` from a repository your team owns first — teams are "
                      "self-authoring")

    cap = cat.resolve_capability(args.capability)
    provider = args.provider_team or (str(args.capability).split("/")[0]
                                      if "/" in str(args.capability) else None)
    log = []
    if cap is None:
        if not provider:
            return refuse("DECLARE_RESULT",
                          f"capability '{args.capability}' is not in the catalog and no "
                          "provider team can be derived from its id",
                          "pass --provider-team, or use the <team>/<slug> id scheme")
        slug = slugify(str(args.capability).split("/")[-1])
        if not cat.team_doc(provider):
            write_doc(root, f"teams/{provider}/team.md", {
                "type": "Team",
                "title": provider.replace("-", " ").title(),
                "team_id": provider,
                "provenance": "stub",
                "timestamp": iso(now),
            }, "# Stub\n\nCreated by another team while declaring a dependency. Only "
               f"{provider} may fill this in, by running `annotate` from a repository it "
               "owns. Until then no dependency naming this team can be acknowledged.\n",
               log, f"Stub team {provider} created by {consumer}.")
            out(f"STUB: created team '{provider}' — it carries a title and nothing else")
        cap_rel = f"teams/{provider}/capabilities/{slug}.md"
        write_doc(root, cap_rel, {
            "type": "Capability",
            "title": slug.replace("-", " ").title(),
            "description": f"Requested by {consumer}; not yet described by {provider}.",
            "capability_id": f"{provider}/{slug}",
            "owning_team": team_link(provider),
            "lifecycle": "proposed",
            "fulfilled_by": [],
            "upstream": {"attested": False},
            "readiness": [{"environment": env, "state": "unknown"} for env in cat.env_names()],
            "timestamp": iso(now),
        }, f"# Contract\n\nProposed by {consumer}. The owning team has not written this yet.\n",
           log, f"Capability {provider}/{slug} proposed by {consumer}.")
        cat = Catalog.load(root)
        cap = cat.resolve_capability(args.capability)
    else:
        provider = cat.team_of_doc(cap) or provider

    env = args.environment or (cat.env_names()[-1] if cat.env_names() else "production")
    if env not in cat.env_names():
        out(f"WARN: '{env}' is not one of the configured environments "
            f"({', '.join(cat.env_names())})")

    # Show the closure before they commit: they are entitled to know whether
    # they are depending on something whose own dependencies nobody has mapped.
    ready = effective_readiness(cat, cap, env, today)
    out(f"CLOSURE: {cap.get('capability_id')} in {env} → readiness={ready.state} "
        f"depth={ready.depth} verdict={ready.verdict}")
    for rel in closure(cat, cap)[1:]:
        target = cat.docs.get(rel)
        label = target.get("capability_id") if target is not None else f"{rel} (MISSING)"
        state = own_readiness(cat, target, env, today)[0] if target is not None else "unknown"
        out(f"  hard upstream: {label} — {state}")
    if ready.depth != "complete":
        out("WARN: depth is 'unknown' — you are about to depend on something whose own "
            "dependencies nobody has mapped. That is not the same as 'fine'")

    existing = existing_edge(cat, consumer, cap.relpath)
    dep_id = (args.dependency or (existing.get("dependency_id") if existing is not None else None)
              or next_dep_id(cat, now))
    rel = f"dependencies/{dep_id}.md"
    prior = cat.docs.get(rel)
    meta = dict(prior.meta) if prior is not None else {}
    meta.update({
        "type": "Dependency",
        "title": f"{consumer} → {cap.get('capability_id')}",
        "dependency_id": dep_id,
        "consumer_team": team_link(consumer),
        "provider_team": team_link(provider),
        "capability": f"/{cap.relpath}",
        "target_environment": env,
        "requested_date": args.requested_date,
        "consequence_if_late": args.consequence,
        "state": "proposed",
        "timestamp": iso(now),
    })
    if args.fallback:
        meta["fallback"] = {"description": args.fallback,
                            "execution_days": int(args.fallback_days)}
        meta.pop("no_fallback_rationale", None)
    else:
        meta["fallback"] = None
        meta["no_fallback_rationale"] = args.no_fallback
        out("WARN: recorded as having no fallback. That is a red flag surfaced weeks early, "
            "which is the format working, not a validation error")
    ack = dict(meta.get("acknowledgement") or {})
    ack["consumer"] = {"by": args.by or consumer, "at": iso(now)}
    meta["acknowledgement"] = ack
    if args.depends_on:
        meta["depends_on"] = [d.strip() for d in args.depends_on.split(",") if d.strip()]

    body = prior.body if prior is not None else ""
    if not body.strip() or "# Detected" in body:
        body = (f"# Why\n\n{args.why or 'Recorded by ' + consumer + ' at planning time.'}\n\n"
                "# Fallback\n\n"
                + (f"{args.fallback} — {args.fallback_days} day(s) to stand up.\n"
                   if args.fallback else f"None. {args.no_fallback}\n"))
    write_doc(root, rel, meta, body, log,
              f"Dependency {dep_id} proposed by {consumer}.")

    requested = parse_date(args.requested_date)
    if requested and args.fallback_days:
        provisional = requested - _dt.timedelta(days=int(args.fallback_days))
        out(f"PONR: provisional point of no return {provisional.isoformat()} "
            f"= requested {args.requested_date} − {args.fallback_days} day(s) to stand up the "
            "fallback. It is computed backwards from the deadline, not forwards from today; "
            "the real one follows the provider's promised date")

    cat = Catalog.load(root)
    written = cat.docs.get(rel)
    if written is not None:
        for other, other_date in impossible_promise(cat, written):
            out(f"WARN: a hard upstream edge {other.get('dependency_id')} is promised "
                f"{other_date.isoformat()} — check that against the date you are asking for")

    regenerate_indexes(root)
    append_log(root, log, now)
    if not cat.team_is_claimed(provider):
        out(f"BLOCKED: '{provider}' is a stub team, so this edge can never reach "
            "'acknowledged'. That is deliberate — go and talk to them")
    out("")
    out(f"NEXT: {provider} must run: okf_catalog.py ack <bundle> --dependency {dep_id} "
        f"--team {provider} --promised-date <YYYY-MM-DD> --by <handle>")
    out(f"PASTE: @{provider} — {consumer} needs {cap.get('capability_id')} working in {env} "
        f"by {args.requested_date}. If it is late: {args.consequence} "
        f"Can you commit a date? Edge: {dep_id}.")
    out(f"DECLARE_RESULT: PROPOSED {dep_id}")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# ack / confirm / on-track / risk / resolve
# ─────────────────────────────────────────────────────────────────────────────

def get_dep(cat, dep_id):
    for dep in cat.by_type("Dependency"):
        if dep.get("dependency_id") == dep_id or dep.relpath == f"dependencies/{dep_id}.md":
            return dep
    return None


def cmd_ack(args):
    cat = load_catalog(args)
    if cat is None:
        out("ACK_RESULT: REFUSED")
        return 2
    now, today = now_of(args), today_of(args)
    dep = get_dep(cat, args.dependency)
    if dep is None:
        return refuse("ACK_RESULT", f"no dependency '{args.dependency}' in this catalog")
    provider = team_id_of(dep.get("provider_team"))
    if args.team != provider:
        return refuse("ACK_RESULT",
                      f"'{args.team}' is not the provider on this edge ('{provider}' is)",
                      "only the providing team may commit a date")
    if not cat.team_is_claimed(provider):
        return refuse("ACK_RESULT",
                      f"'{provider}' exists only as a stub team",
                      "acknowledgement requires a named human from a claimed team — run "
                      "`annotate` from a repository the team owns to claim it first")
    for flag, value in (("--consequence", args.consequence), ("--fallback", args.fallback),
                        ("--requested-date", args.requested_date)):
        if value:
            return refuse("ACK_RESULT",
                          f"a provider may not set {flag} — it belongs to the consumer",
                          "if you dispute it, use --dispute to record the disagreement in "
                          "the body; the consumer's fields stay intact")

    cap = cat.resolve_capability(dep.get("capability"))
    env = dep.get("target_environment")
    if cap is not None:
        svc_platforms = {}
        for rel in [norm_link(f) for f in cap.get("fulfilled_by") or []]:
            svc = cat.docs.get(rel)
            if svc is None:
                continue
            for run in svc.get("runtimes") or []:
                if isinstance(run, dict) and run.get("environment"):
                    svc_platforms[run["environment"]] = run.get("platform")
        if svc_platforms:
            out("RUNTIMES: " + ", ".join(f"{e}={p}" for e, p in sorted(svc_platforms.items())))
        target_platform = svc_platforms.get(env)
        others = {p for e, p in svc_platforms.items() if e != env and p}
        if target_platform and others - {target_platform}:
            out(f"WARN: {env} runs on {target_platform} but other environments run on "
                f"{', '.join(sorted(others - {target_platform}))}. Where you tested is not "
                "where this has to work — answer that explicitly before committing a date")
            if not args.runtime_checked:
                return refuse("ACK_RESULT",
                              "the runtime for the target environment differs from where "
                              "this was tested",
                              "confirm you have looked at it with --runtime-checked, and "
                              "say in --note what you actually verified")

    promised = parse_date(args.promised_date)
    if promised is None:
        return refuse("ACK_RESULT", f"'{args.promised_date}' is not a date (YYYY-MM-DD)")
    meta = dict(dep.meta)
    meta["promised_date"] = args.promised_date
    ack = dict(meta.get("acknowledgement") or {})
    ack["provider"] = {"by": args.by, "at": iso(now)}
    meta["acknowledgement"] = ack
    ponr = point_of_no_return(Doc_like(meta), cat.config)
    if ponr:
        meta["point_of_no_return"] = ponr.isoformat()
    requested = parse_date(dep.get("requested_date"))
    body = dep.body
    if args.dispute:
        body = (body.rstrip("\n") + "\n\n# Provider note\n\n" + args.dispute + "\n")
    if args.note:
        body = (body.rstrip("\n") + f"\n\n# Acknowledgement note ({args.by})\n\n{args.note}\n")

    if requested and promised > requested:
        meta["consumer_reconfirmation_required"] = True
        meta["state"] = "proposed"
        result = "PENDING_CONSUMER"
        out(f"WARN: promised {args.promised_date} is later than the requested "
            f"{dep.get('requested_date')} — that gap is real information. The edge stays "
            "'proposed' until the consumer re-confirms")
    else:
        meta.pop("consumer_reconfirmation_required", None)
        meta["state"] = "acknowledged" if ack.get("consumer") else "proposed"
        result = "ACKNOWLEDGED" if meta["state"] == "acknowledged" else "PENDING_CONSUMER"
    meta["timestamp"] = iso(now)
    log = []
    write_doc(cat.root, dep.relpath, meta, body, log,
              f"Dependency {dep.get('dependency_id')} acknowledged by {provider} "
              f"(promised {args.promised_date}).")
    append_log(cat.root, log, now)

    cat = Catalog.load(cat.root)
    dep = get_dep(cat, args.dependency)
    for other, other_date in impossible_promise(cat, dep):
        out(f"WARN: IMPOSSIBLE PROMISE — you promised {args.promised_date} but hard upstream "
            f"edge {other.get('dependency_id')} is itself promised {other_date.isoformat()}. "
            "The graph says this cannot happen; resolve it before the edge is acted on")
    if ponr:
        fallback = dep.get("fallback") or {}
        days = fallback.get("execution_days", cat.config.get("default_fallback_execution_days"))
        out(f"PONR: {ponr.isoformat()} = promised {args.promised_date} − {days} day(s) of "
            "fallback execution. Confirm on-track before then or the edge trips on its own")
    ready = effective_readiness(cat, cap, env, today) if cap is not None else None
    if ready is not None:
        out(f"READINESS: {cap.get('capability_id')} in {env} → {ready.state} "
            f"(depth={ready.depth}, verdict={ready.verdict})")
    out(f"ACK_RESULT: {result}")
    return 0


class Doc_like:
    """Minimal shim so point_of_no_return can run on an in-flight meta dict."""

    def __init__(self, meta):
        self.meta = meta

    def get(self, key, default=None):
        value = self.meta.get(key, default)
        return default if value is None else value


def _mutate(args, result_key, fn, event):
    cat = load_catalog(args)
    if cat is None:
        out(f"{result_key}: REFUSED")
        return 2
    now = now_of(args)
    dep = get_dep(cat, args.dependency)
    if dep is None:
        return refuse(result_key, f"no dependency '{args.dependency}' in this catalog")
    meta = dict(dep.meta)
    body = dep.body
    outcome = fn(cat, dep, meta, now)
    if isinstance(outcome, int):
        return outcome
    if isinstance(outcome, tuple):
        meta, body = outcome
    meta["timestamp"] = iso(now)
    log = []
    write_doc(cat.root, dep.relpath, meta, body, log, event.format(id=dep.get("dependency_id")))
    append_log(cat.root, log, now)
    out(f"{result_key}: OK {dep.get('dependency_id')} → {meta.get('state')}")
    return 0


def cmd_confirm(args):
    def apply(cat, dep, meta, now):
        consumer = team_id_of(dep.get("consumer_team"))
        if args.team != consumer:
            return refuse("CONFIRM_RESULT",
                          f"'{args.team}' is not the consumer on this edge ('{consumer}' is)")
        meta.pop("consumer_reconfirmation_required", None)
        ack = dict(meta.get("acknowledgement") or {})
        ack["consumer"] = {"by": args.by, "at": iso(now)}
        meta["acknowledgement"] = ack
        meta["state"] = "acknowledged" if ack.get("provider") else "proposed"
        return meta, dep.body
    return _mutate(args, "CONFIRM_RESULT", apply,
                   "Dependency {id} re-confirmed by the consumer.")


def cmd_on_track(args):
    def apply(cat, dep, meta, now):
        provider = team_id_of(dep.get("provider_team"))
        if args.team != provider:
            return refuse("ONTRACK_RESULT",
                          f"only the provider ('{provider}') may confirm on-track")
        meta["on_track"] = {"confirmed_by": args.by, "confirmed_at": iso(now)}
        if meta.get("state") == "tripped":
            meta["state"] = "acknowledged"
        return meta, dep.body
    return _mutate(args, "ONTRACK_RESULT", apply, "Dependency {id} confirmed on track.")


def cmd_risk(args):
    def apply(cat, dep, meta, now):
        provider = team_id_of(dep.get("provider_team"))
        if args.team != provider:
            return refuse("RISK_RESULT", f"only the provider ('{provider}') may flag risk")
        meta["state"] = "at_risk"
        meta["risk_note"] = args.note
        return meta, dep.body
    return _mutate(args, "RISK_RESULT", apply, "Dependency {id} flagged at risk.")


def cmd_resolve(args):
    today = today_of(args)

    def apply(cat, dep, meta, now):
        if args.decision == "satisfied":
            ok, why = can_satisfy(cat, dep, today)
            if not ok:
                return refuse("RESOLVE_RESULT",
                              f"this edge cannot be closed as satisfied: {why}",
                              "satisfaction needs the consumer's own verification in the "
                              "target environment, and a mapped hard closure")
            out(f"CHECK: {why}")
        meta["state"] = args.decision
        meta["decision"] = {"decision": args.decision, "by": args.by, "at": iso(now),
                            "note": args.note or ""}
        if args.new_date:
            meta["promised_date"] = args.new_date
            ponr = point_of_no_return(Doc_like(meta), cat.config)
            if ponr:
                meta["point_of_no_return"] = ponr.isoformat()
        return meta, dep.body
    return _mutate(args, "RESOLVE_RESULT", apply, "Dependency {id} decision recorded.")


# ─────────────────────────────────────────────────────────────────────────────
# verify / signal
# ─────────────────────────────────────────────────────────────────────────────

def cmd_verify(args):
    cat = load_catalog(args)
    if cat is None:
        out("VERIFY_RESULT: REFUSED")
        return 2
    now = now_of(args)
    cap = cat.resolve_capability(args.capability)
    if cap is None:
        return refuse("VERIFY_RESULT", f"capability '{args.capability}' is not in the catalog")
    owner = cat.team_of_doc(cap)
    if args.team == owner:
        return refuse("VERIFY_RESULT",
                      f"'{args.team}' owns {cap.get('capability_id')} and cannot verify its "
                      "own capability",
                      "acceptance belongs to the consumer — self-certification is the "
                      "failure this design exists to prevent")
    if existing_edge(cat, args.team, cap.relpath) is None:
        return refuse("VERIFY_RESULT",
                      f"'{args.team}' is not a declared consumer of {cap.get('capability_id')}",
                      "run `declare` first — a verification without a declared dependency "
                      "has no consumer to belong to")
    if args.kind == "deployment":
        if not args.resolved_from:
            return refuse("VERIFY_RESULT",
                          "a deployment verification must record where it actually ran, "
                          "resolved from the CI run rather than declared by hand",
                          "pass --resolved-from ci://<pipeline>/run/<id>")
        if args.ran_in and args.ran_in != args.environment:
            return refuse("VERIFY_RESULT",
                          f"the suite ran against '{args.ran_in}' but this claims "
                          f"'{args.environment}'",
                          "green against staging is evidence about staging and nothing "
                          "more. File it against the environment it ran in")
    if args.kind == "contract":
        out("NOTE: a contract test proves you and the provider agree on the shape of the "
            "exchange, not that the deployment is wired up. Recorded as supporting "
            "evidence; it raises readiness for no environment")
    if not args.evidence:
        return refuse("VERIFY_RESULT", "a verification needs an evidence link")

    slug = f"{slugify(cap.get('capability_id'))}-{slugify(args.environment)}"
    rel = f"teams/{args.team}/verifications/{slug}.md"
    meta = {
        "type": "Verification",
        "title": f"{args.team} {args.result} {cap.get('capability_id')} in {args.environment}",
        "capability": f"/{cap.relpath}",
        "verifying_team": team_link(args.team),
        "environment": args.environment,
        "result": args.result,
        "kind": args.kind,
        "environment_resolved_from": args.resolved_from or "",
        "scope": args.scope or "",
        "evidence": args.evidence,
        "verified_by": args.by,
        "verified_at": iso(now),
    }
    if args.expires_days:
        meta["expires_after_days"] = int(args.expires_days)
    if args.commit_sha:
        meta["commit_sha"] = args.commit_sha
    log = []
    write_doc(cat.root, rel, {k: v for k, v in meta.items() if v != ""},
              f"# Scope\n\n{args.scope or 'Not stated.'}\n", log,
              f"{args.team} recorded a '{args.result}' verification of "
              f"{cap.get('capability_id')} in {args.environment}.")
    regenerate_indexes(cat.root)
    append_log(cat.root, log, now)

    cat = Catalog.load(cat.root)
    ready = effective_readiness(cat, cat.docs[cap.relpath], args.environment, today_of(args))
    out(f"READINESS: {cap.get('capability_id')} in {args.environment} → {ready.state} "
        f"(depth={ready.depth}, verdict={ready.verdict})")
    for env in cat.env_names():
        if env != args.environment:
            other = effective_readiness(cat, cat.docs[cap.relpath], env, today_of(args))
            out(f"  {env}: {other.state} — untouched by this run")
    out("NOTE: readiness is derived from this file, never copied into the capability. "
        "Delete this document and the capability drops back — no stale green tick survives")
    out(f"VERIFY_RESULT: WRITTEN {rel}")
    return 0


def cmd_tested(args):
    """The provider records that IT exercised the capability in an environment.

    This is the ceiling of what a provider-owned document may say. It is never
    sufficient to plan a delivery date against — internal testing by the
    provider is not acceptance, and acceptance belongs to the consumer.
    """
    cat = load_catalog(args)
    if cat is None:
        out("TESTED_RESULT: REFUSED")
        return 2
    now = now_of(args)
    cap = cat.resolve_capability(args.capability)
    if cap is None:
        return refuse("TESTED_RESULT", f"capability '{args.capability}' is not in the catalog")
    owner = cat.team_of_doc(cap)
    if args.team != owner:
        return refuse("TESTED_RESULT",
                      f"'{args.team}' does not own {cap.get('capability_id')} ('{owner}' does)",
                      "only the owning team may record provider testing")
    if args.environment not in cat.env_names():
        out(f"WARN: '{args.environment}' is not one of the configured environments")

    meta = dict(cap.meta)
    entries, seen = [], False
    for entry in meta.get("readiness") or []:
        if isinstance(entry, dict) and entry.get("environment") == args.environment:
            seen = True
            entries.append({"environment": args.environment, "state": args.state,
                            "asserted_by": f"teams/{args.team}", "method": "human",
                            "evidence": args.evidence or "", "asserted_at": iso(now)})
        else:
            entries.append(entry)
    if not seen:
        entries.append({"environment": args.environment, "state": args.state,
                        "asserted_by": f"teams/{args.team}", "method": "human",
                        "evidence": args.evidence or "", "asserted_at": iso(now)})
    meta["readiness"] = [{k: v for k, v in e.items() if v != ""} if isinstance(e, dict) else e
                         for e in entries]
    meta["timestamp"] = iso(now)
    log = []
    write_doc(cat.root, cap.relpath, meta, cap.body, log,
              f"{args.team} recorded '{args.state}' for {cap.get('capability_id')} in "
              f"{args.environment}.")
    append_log(cat.root, log, now)
    cat = Catalog.load(cat.root)
    ready = effective_readiness(cat, cat.docs[cap.relpath], args.environment, today_of(args))
    out(f"READINESS: effective {ready.state} (depth={ready.depth}, verdict={ready.verdict})")
    out("NOTE: provider_tested is never sufficient to plan a delivery date against. A "
        "consuming team must run `verify` in this environment before anything here is "
        "consumable")
    out(f"TESTED_RESULT: WRITTEN {cap.relpath}")
    return 0


def cmd_signal(args):
    cat = load_catalog(args)
    if cat is None:
        out("SIGNAL_RESULT: REFUSED")
        return 2
    now = now_of(args)
    if not re.match(r"^(ci|monitor|monitoring)://", args.emitted_by or ""):
        return refuse("SIGNAL_RESULT",
                      f"emitted_by '{args.emitted_by}' is not a machine source",
                      "signals are written by CI or monitoring only (ci:// or monitor://) "
                      "— production_live is a traffic fact, and a human asserting it is "
                      "reporting a belief rather than an observation")
    cap = cat.resolve_capability(args.capability)
    if cap is None:
        return refuse("SIGNAL_RESULT", f"capability '{args.capability}' is not in the catalog")
    slug = f"{slugify(cap.get('capability_id'))}-{slugify(args.environment)}-{slugify(args.observation)}"
    meta = {
        "type": "Signal",
        "title": f"{cap.get('capability_id')} observed {args.observation} in {args.environment}",
        "capability": f"/{cap.relpath}",
        "environment": args.environment,
        "observation": args.observation,
        "window": args.window or "",
        "detail": args.detail or "",
        "emitted_by": args.emitted_by,
        "emitted_at": iso(now),
    }
    log = []
    write_doc(cat.root, f"signals/{slug}.md", {k: v for k, v in meta.items() if v != ""}, "",
              log, f"Signal: {cap.get('capability_id')} {args.observation} in {args.environment}.")
    regenerate_indexes(cat.root)
    append_log(cat.root, log, now)
    out(f"SIGNAL_RESULT: WRITTEN signals/{slug}.md")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# readiness / review / audit / validate / enforce / codeowners
# ─────────────────────────────────────────────────────────────────────────────

def cmd_readiness(args):
    cat = load_catalog(args)
    if cat is None:
        out("READINESS_RESULT: FAIL")
        return 1
    today = today_of(args)
    cap = cat.resolve_capability(args.capability)
    if cap is None:
        return refuse("READINESS_RESULT", f"capability '{args.capability}' is not in the catalog")
    envs = [args.environment] if args.environment else cat.env_names()
    verdicts = []
    for env in envs:
        ready = effective_readiness(cat, cap, env, today)
        own = own_readiness(cat, cap, env, today)[0]
        out(f"ENV {env}: effective={ready.state} own={own} depth={ready.depth} "
            f"verdict={ready.verdict}")
        for rel in closure(cat, cap)[1:]:
            target = cat.docs.get(rel)
            if target is None:
                out(f"  hard upstream {rel}: MISSING from the catalog")
                continue
            out(f"  hard upstream {target.get('capability_id')}: "
                f"{own_readiness(cat, target, env, today)[0]}")
        for note in ready.notes:
            out(f"  note: {note}")
        if ready.cycle:
            out(f"  cycle: {' -> '.join(ready.cycle)}")
        verdicts.append((env, ready))
    if len(envs) == 1:
        ready = verdicts[0][1]
        out(f"READINESS_RESULT: {ready.state} depth={ready.depth} verdict={ready.verdict}")
    else:
        out("READINESS_RESULT: " + " ".join(
            f"{env}={r.state}/{r.depth}/{r.verdict}" for env, r in verdicts))
    return 0


def _dep_row(cat, dep, today):
    state = effective_state(cat, dep, today)
    ponr = point_of_no_return(dep, cat.config)
    cap = cat.resolve_capability(dep.get("capability"))
    cap_id = cap.get("capability_id") if cap is not None else norm_link(dep.get("capability"))
    return (f"{dep.get('dependency_id')} [{state.state}] {cap_id} in "
            f"{dep.get('target_environment')} — requested {dep.get('requested_date') or '—'}, "
            f"promised {dep.get('promised_date') or '—'}, PONR "
            f"{ponr.isoformat() if ponr else '—'}"
            + (f" ({state.reason})" if state.reason else ""))


def cmd_review(args):
    cat = load_catalog(args)
    if cat is None:
        out("REVIEW_RESULT: FAIL")
        return 1
    today = today_of(args)
    view = args.view
    team = args.team
    deps = cat.by_type("Dependency")

    if view in ("all", "inbound") and team:
        out(f"## Inbound — what other teams have asked of {team}")
        rows = [d for d in deps if team_id_of(d.get("provider_team")) == team]
        for dep in rows:
            out(f"  {_dep_row(cat, dep, today)} (from {team_id_of(dep.get('consumer_team'))})")
        if not rows:
            out("  (nothing — no other team has recorded an ask against you)")
    if view in ("all", "outbound") and team:
        out(f"## Outbound — what {team} is waiting on")
        rows = [d for d in deps if team_id_of(d.get("consumer_team")) == team]
        for dep in rows:
            out(f"  {_dep_row(cat, dep, today)} (from {team_id_of(dep.get('provider_team'))})")
        if not rows:
            out("  (nothing — you have recorded no dependencies on other teams)")
        known, total = closure_coverage(cat, team)
        if total:
            out(f"  closure coverage: {known}/{total} "
                f"({round(100.0 * known / total)}%) of the hard closure is actually known")
    if view in ("all", "directory"):
        out("## Capability directory — the menu to consult BEFORE building something "
            "that already exists")
        for rel, cap in sorted(cat.capabilities.items()):
            owner = cat.team_of_doc(cap)
            marks = []
            for env in cat.env_names():
                ready = effective_readiness(cat, cap, env, today)
                marks.append(f"{env}={ready.state}/{ready.verdict}")
            claimed = "" if cat.team_is_claimed(owner) else " [stub team]"
            out(f"  {cap.get('capability_id')} ({owner}{claimed}, {cap.get('lifecycle')}) — "
                + ", ".join(marks))
    if view in ("all", "timeline"):
        out("## Timeline — nearest decisions first")
        rows = []
        for dep in deps:
            ponr = point_of_no_return(dep, cat.config)
            promised = parse_date(dep.get("promised_date"))
            if ponr or promised:
                rows.append(((ponr or promised), dep))
        for when, dep in sorted(rows, key=lambda r: r[0]):
            kind = "PONR" if point_of_no_return(dep, cat.config) == when else "promised"
            out(f"  {when.isoformat()} {kind}: {_dep_row(cat, dep, today)}")
        if not rows:
            out("  (no dated commitments yet)")
    if view == "capability":
        cap = cat.resolve_capability(args.capability)
        if cap is None:
            return refuse("REVIEW_RESULT", f"capability '{args.capability}' is not in the catalog")
        out(f"## {cap.get('capability_id')} — {cap.get('title')}")
        out(f"  owner: {cat.team_of_doc(cap)}   lifecycle: {cap.get('lifecycle')}")
        maturity = cap.get("code_maturity") or {}
        out(f"  code maturity: {maturity.get('state')} on {maturity.get('branch')}")
        for env in cat.env_names():
            ready = effective_readiness(cat, cap, env, today)
            out(f"  {env}: {ready.state} (depth={ready.depth}, verdict={ready.verdict})")
        out("  hard closure: " + (", ".join(closure(cat, cap)[1:]) or "(none declared)"))
        out("  consumers:")
        for dep in deps:
            if norm_link(dep.get("capability")) == cap.relpath:
                out(f"    {team_id_of(dep.get('consumer_team'))} — {_dep_row(cat, dep, today)}")
        out("  verifications:")
        for ver in cat.verifications:
            if norm_link(ver.get("capability")) == cap.relpath:
                out(f"    {ver.get('verifying_team')} {ver.get('result')} "
                    f"({ver.get('kind')}) in {ver.get('environment')} at "
                    f"{ver.get('verified_at')} — {ver.get('evidence')}")

    out("LEGEND: verdict 'unknown' means nobody has mapped the closure. It is not 'fine' "
        "and it is not 'broken' — it is unknown, and it renders as neither")
    out("REVIEW_RESULT: OK")
    return 0


def cmd_audit(args):
    cat = load_catalog(args)
    if cat is None:
        out("AUDIT_RESULT: FAIL")
        return 1
    today = today_of(args)
    findings = audit(cat, today, args.ponr_days)
    if args.json:
        print(json.dumps([{"code": f.code, "severity": f.severity, "path": f.path,
                           "message": f.message} for f in findings], indent=2))
    else:
        for finding in findings:
            out(finding.line())
    high = sum(1 for f in findings if f.severity == "high")
    out(f"AUDIT_RESULT: {len(findings)} finding(s) ({high} high)")
    return 1 if (args.fail_on_high and high) else 0


def cmd_validate(args):
    cat = load_catalog(args)
    if cat is None:
        out("VALIDATE_RESULT: FAIL")
        return 1
    hard, soft = validate_bundle(cat, today_of(args))
    for item in hard:
        out(f"HARD: {item}")
    for item in soft:
        out(f"SOFT: {item}")
    out("NOTE: unknown types, unknown keys, missing optional fields and broken cross-links "
        "are reported, never rejected (OKF §9 requires tolerant consumption)")
    out(f"VALIDATE_RESULT: {'FAIL' if hard else 'PASS'} ({len(hard)} hard, {len(soft)} soft)")
    return 1 if hard else 0


def cmd_enforce(args):
    cat = load_catalog(args)
    if cat is None:
        out("ENFORCE_RESULT: FAIL")
        return 1
    with open(args.changes, encoding="utf-8") as fh:
        payload = json.load(fh)
    commits = payload.get("commits") if isinstance(payload, dict) else payload
    violations = enforce_changes(cat, commits, tolerance_days=args.tolerance_days)
    for violation in violations:
        out(violation.line())
    out(f"ENFORCE_RESULT: {'FAIL' if violations else 'PASS'} ({len(violations)} violation(s))")
    return 1 if violations else 0


def cmd_codeowners(args):
    cat = load_catalog(args)
    if cat is None:
        out("CODEOWNERS_RESULT: FAIL")
        return 1
    org = cat.config.get("organization", "acme")
    platform = cat.config.get("platform_team")
    lines = [
        "# Generated by okf-capability-catalog. Path ownership is the first layer of",
        "# enforcement: no file mixes two parties' assertions, so ordinary CODEOWNERS",
        "# covers most of the authority rules (§8.1 layer 1).",
        "",
    ]
    for team_id, doc in sorted(cat.teams.items()):
        handle = (doc.get("contacts") or {}).get("github_team") or f"@{org}/{team_id}"
        lines.append(f"/teams/{team_id}/ {handle}")
    if platform:
        lines.append(f"/signals/ @{org}/{platform}")
    lines += [
        "",
        "# dependencies/ has two owners per edge, which CODEOWNERS cannot express —",
        "# the diff-validation check (`enforce`) is what protects those fields.",
    ]
    changed = write_text(cat.root, ".github/CODEOWNERS", "\n".join(lines) + "\n")
    out(f"CODEOWNERS_RESULT: {'WROTE' if changed else 'UNCHANGED'} .github/CODEOWNERS")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(prog="okf_catalog.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="mode", required=True)

    def common(sp, clock=True):
        sp.add_argument("bundle", help="path to the catalog bundle")
        if clock:
            sp.add_argument("--now", help="pin the clock (ISO-8601) for deterministic runs")
            sp.add_argument("--today", help="pin the date (YYYY-MM-DD)")
        return sp

    sp = common(sub.add_parser("init", help="scaffold the bundle (once per organisation)"))
    sp.add_argument("--org", default="acme")
    sp.add_argument("--integration", default="develop")
    sp.add_argument("--release", default="main")
    sp.add_argument("--environments", default="dev,staging,production")
    sp.add_argument("--live-signal-env", default="production",
                    help="comma-separated environments where production_live needs a machine signal")
    sp.add_argument("--default-fallback-days", type=int, default=2)
    sp.add_argument("--platform-team", default=None)
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init)

    sp = common(sub.add_parser("annotate", help="scan a service repository"))
    sp.add_argument("--repo", required=True)
    sp.add_argument("--branch", default=None)
    sp.add_argument("--team", default=None)
    sp.add_argument("--by", default=None)
    sp.add_argument("--commit", default=None)
    sp.add_argument("--attest-upstream", choices=["yes", "no"], default=None,
                    help="the owning team's answer to: is `requires` the COMPLETE list?")
    sp.set_defaults(func=cmd_annotate)

    sp = common(sub.add_parser("declare", help="consumer records a dependency"))
    sp.add_argument("--consumer", required=True)
    sp.add_argument("--capability", required=True)
    sp.add_argument("--environment", default=None)
    sp.add_argument("--requested-date", required=True)
    sp.add_argument("--consequence", default=None)
    sp.add_argument("--fallback", default=None)
    sp.add_argument("--fallback-days", type=int, default=None)
    sp.add_argument("--no-fallback", default=None, help="rationale for having no fallback")
    sp.add_argument("--provider-team", default=None)
    sp.add_argument("--depends-on", default=None, help="comma-separated dependency_ids")
    sp.add_argument("--dependency", default=None, help="promote an existing detected edge")
    sp.add_argument("--why", default=None)
    sp.add_argument("--by", default=None)
    sp.add_argument("--promised-date", default=None, help=argparse.SUPPRESS)
    sp.set_defaults(func=cmd_declare)

    sp = common(sub.add_parser("ack", help="provider commits a date"))
    sp.add_argument("--dependency", required=True)
    sp.add_argument("--team", required=True)
    sp.add_argument("--promised-date", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--note", default=None)
    sp.add_argument("--dispute", default=None)
    sp.add_argument("--runtime-checked", action="store_true")
    sp.add_argument("--consequence", default=None, help=argparse.SUPPRESS)
    sp.add_argument("--fallback", default=None, help=argparse.SUPPRESS)
    sp.add_argument("--requested-date", default=None, help=argparse.SUPPRESS)
    sp.set_defaults(func=cmd_ack)

    sp = common(sub.add_parser("confirm", help="consumer re-confirms after a date slip"))
    sp.add_argument("--dependency", required=True)
    sp.add_argument("--team", required=True)
    sp.add_argument("--by", required=True)
    sp.set_defaults(func=cmd_confirm)

    sp = common(sub.add_parser("on-track", help="provider confirms an edge is on track"))
    sp.add_argument("--dependency", required=True)
    sp.add_argument("--team", required=True)
    sp.add_argument("--by", required=True)
    sp.set_defaults(func=cmd_on_track)

    sp = common(sub.add_parser("risk", help="provider flags an edge at risk"))
    sp.add_argument("--dependency", required=True)
    sp.add_argument("--team", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--note", required=True)
    sp.set_defaults(func=cmd_risk)

    sp = common(sub.add_parser("resolve", help="record the decision on a tripped edge"))
    sp.add_argument("--dependency", required=True)
    sp.add_argument("--decision", required=True,
                    choices=["satisfied", "fallback_invoked", "renegotiated"])
    sp.add_argument("--by", required=True)
    sp.add_argument("--note", default=None)
    sp.add_argument("--new-date", default=None)
    sp.set_defaults(func=cmd_resolve)

    sp = common(sub.add_parser("verify", help="consumer records acceptance"))
    sp.add_argument("--team", required=True)
    sp.add_argument("--capability", required=True)
    sp.add_argument("--environment", required=True)
    sp.add_argument("--result", required=True, choices=["verified", "failed", "partial"])
    sp.add_argument("--kind", required=True, choices=["deployment", "contract", "manual"])
    sp.add_argument("--evidence", default=None)
    sp.add_argument("--by", required=True)
    sp.add_argument("--scope", default=None)
    sp.add_argument("--ran-in", default=None, help="environment the suite actually ran against")
    sp.add_argument("--resolved-from", default=None, help="ci:// run the environment came from")
    sp.add_argument("--expires-days", type=int, default=None)
    sp.add_argument("--commit-sha", default=None)
    sp.set_defaults(func=cmd_verify)

    sp = common(sub.add_parser("tested", help="provider records its own testing (ceiling: provider_tested)"))
    sp.add_argument("--team", required=True)
    sp.add_argument("--capability", required=True)
    sp.add_argument("--environment", required=True)
    sp.add_argument("--state", default="provider_tested",
                    choices=["unknown", "provider_tested"],
                    help="a provider-owned file may say nothing higher")
    sp.add_argument("--evidence", default=None)
    sp.add_argument("--by", default=None)
    sp.set_defaults(func=cmd_tested)

    sp = common(sub.add_parser("signal", help="CI/monitoring records liveness"))
    sp.add_argument("--capability", required=True)
    sp.add_argument("--environment", required=True)
    sp.add_argument("--observation", required=True, choices=["traffic", "deploy", "healthcheck"])
    sp.add_argument("--emitted-by", required=True, help="ci:// or monitor:// source")
    sp.add_argument("--window", default=None)
    sp.add_argument("--detail", default=None)
    sp.set_defaults(func=cmd_signal)

    sp = common(sub.add_parser("readiness", help="effective readiness + depth"))
    sp.add_argument("--capability", required=True)
    sp.add_argument("--environment", default=None)
    sp.set_defaults(func=cmd_readiness)

    sp = common(sub.add_parser("review", help="orientation: what is"))
    sp.add_argument("--team", default=None)
    sp.add_argument("--view", default="all",
                    choices=["all", "inbound", "outbound", "directory", "timeline", "capability"])
    sp.add_argument("--capability", default=None)
    sp.set_defaults(func=cmd_review)

    sp = common(sub.add_parser("audit", help="findings: what is wrong"))
    sp.add_argument("--ponr-days", type=int, default=None)
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--fail-on-high", action="store_true")
    sp.set_defaults(func=cmd_audit)

    sp = common(sub.add_parser("validate", help="OKF conformance + catalog policy"))
    sp.set_defaults(func=cmd_validate)

    sp = common(sub.add_parser("enforce", help="commit-boundary authority checks"))
    sp.add_argument("--changes", required=True, help="JSON changeset (see references/enforcement.md)")
    sp.add_argument("--tolerance-days", type=float, default=2.0)
    sp.set_defaults(func=cmd_enforce)

    sp = common(sub.add_parser("codeowners", help="regenerate CODEOWNERS from the teams"))
    sp.set_defaults(func=cmd_codeowners)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
