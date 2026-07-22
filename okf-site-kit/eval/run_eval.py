#!/usr/bin/env python3
"""Gate-runnable outcome eval for okf-site-kit (docs/eval-standard.md).

Harness: builds a small spec-canonical OKF v0.1 bundle in a tempdir (root
index.md with okf_version frontmatter, log.md with dated entries, one
subject directory with typed concepts, a producer-extended frontmatter key,
and an internal concept-to-concept link). Skill tooling: runs the shipped
assets/okf_site.py CLI end-to-end against it. Grader: model-free filesystem
and content checks on the emitted Astro/Starlight project.

Negative fixtures: a bundle with a frontmatter-less concept must degrade
with a WARN: report (spec's tolerant-consumer rule — rendered anyway, exit
0), and an empty non-bundle directory must fail `inspect` cleanly
(INSPECT_RESULT: EMPTY, exit 1, explicit warning).

Offline, deterministic, stdlib-only; writes only under tempfile.mkdtemp().
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
GENERATOR = os.path.join(SKILL, "assets", "okf_site.py")

_checks = []


def check(name, ok, detail=""):
    _checks.append(bool(ok))
    suffix = f" ({detail})" if detail else ""
    print(f"CHECK: {name} — {'PASS' if ok else 'FAIL'}{suffix}")


def write(root, rel, content):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def run_cli(tmp, *argv):
    r = subprocess.run(
        [sys.executable, GENERATOR, *argv],
        capture_output=True, text=True, timeout=55, cwd=tmp,
    )
    return r.returncode, r.stdout + r.stderr


ROOT_INDEX = """---
okf_version: 0.1
---

# Acme Cloud Knowledge

Curated knowledge about Acme's cloud estate.

* [Networking](networking/index.md) - Core networking concepts.
"""

LOG_MD = """# Log

## 2026-05-01

* Added the VPC layout concept.

## 2026-06-10

* Documented subnet planning.
"""

VPC_MD = """---
type: Service
title: VPC Layout
description: How Acme's production VPC is segmented.
tags:
  - networking
  - production
owner: alice
---

# VPC Layout

The production VPC uses three tiers. See [subnet planning](subnet-planning.md)
for CIDR allocation rules.
"""

SUBNET_MD = """---
type: Guide
title: Subnet Planning
description: CIDR allocation rules for new subnets.
tags: [networking]
---

# Subnet Planning

Allocate /24 blocks per tier.
"""

NETWORKING_INDEX = """# Networking

Concepts covering Acme's network topology.
"""

NO_FM_CONCEPT = """# Orphan Note

This concept ships no YAML frontmatter at all (spec violation).
"""


def positive_arm(tmp):
    bundle = os.path.join(tmp, "bundle")
    out = os.path.join(tmp, "site")
    write(bundle, "index.md", ROOT_INDEX)
    write(bundle, "log.md", LOG_MD)
    write(bundle, "networking/index.md", NETWORKING_INDEX)
    write(bundle, "networking/vpc.md", VPC_MD)
    write(bundle, "networking/subnet-planning.md", SUBNET_MD)

    code, text = run_cli(tmp, "generate", bundle, "--out", out,
                         "--title", "Acme Knowledge")
    check("generate exits 0 and reports SITE_GENERATED",
          code == 0 and "SITE_GENERATED:" in text,
          f"exit={code}")
    check("okf_version 0.1 detected from root index frontmatter",
          "OKF_VERSION: 0.1" in text)

    expected = [
        "astro.config.mjs",
        "package.json",
        "tsconfig.json",
        "src/content.config.ts",
        "src/styles/okf.css",
        "src/content/docs/index.mdx",
        "src/content/docs/overview.md",
        "src/content/docs/networking/index.md",
        "src/content/docs/networking/vpc.md",
        "src/content/docs/networking/subnet-planning.md",
    ]
    missing = [p for p in expected if not os.path.isfile(os.path.join(out, p))]
    check("complete Astro/Starlight project emitted (config + landing + per-concept pages)",
          not missing, "missing: " + ", ".join(missing) if missing else "10 files present")

    config = read(os.path.join(out, "astro.config.mjs")) if not missing else ""
    landing = read(os.path.join(out, "src/content/docs/index.mdx")) if not missing else ""
    check("config wires Starlight with the site title and sidebar",
          "starlight(" in config and '"Acme Knowledge"' in config
          and "sidebar:" in config)
    check("landing page has hero + section card for the subject dir",
          "template: splash" in landing and "LinkCard" in landing
          and "networking/" in landing)

    vpc_page = read(os.path.join(out, "src/content/docs/networking/vpc.md")) \
        if not missing else ""
    check("concept page carries OKF metadata panel (type badge, tag, producer key)",
          'class="okf-meta"' in vpc_page
          and 'class="okf-type"' in vpc_page and ">Service<" in vpc_page
          and 'class="okf-tag"' in vpc_page and ">networking<" in vpc_page
          and "<dt>Owner</dt><dd>alice</dd>" in vpc_page)
    check("internal concept link rewritten to site route",
          "(/networking/subnet-planning/)" in vpc_page
          and "subnet-planning.md" not in vpc_page)

    changelog = os.path.join(out, "src/content/docs/changelog.md")
    body = read(changelog) if os.path.isfile(changelog) else ""
    check("changelog page generated from log.md with dated entries",
          'title: "Changelog"' in body and "2026-05-01" in body
          and "2026-06-10" in body,
          "missing changelog.md" if not body else "")


def negative_arms(tmp):
    # A bundle whose concept has no frontmatter: spec violation, but the
    # generator promises to degrade with a WARN: report and render anyway.
    bad = os.path.join(tmp, "bad-bundle")
    out = os.path.join(tmp, "bad-site")
    write(bad, "index.md", "# Sparse bundle\n")
    write(bad, "notes/orphan.md", NO_FM_CONCEPT)
    code, text = run_cli(tmp, "generate", bad, "--out", out)
    page = os.path.join(out, "src", "content", "docs", "notes", "orphan.md")
    check("frontmatter-less concept degrades: WARN reported, no crash, page still rendered",
          code == 0 and "WARN: concept without frontmatter" in text
          and os.path.isfile(page),
          f"exit={code}")

    # An empty directory is not a bundle: inspect must fail cleanly with a
    # clear message (documented: INSPECT_RESULT: EMPTY, exit 1).
    empty = os.path.join(tmp, "empty-dir")
    os.makedirs(empty, exist_ok=True)
    code, text = run_cli(tmp, "inspect", empty)
    check("empty non-bundle dir: inspect exits nonzero with a clear message",
          code == 1 and "no markdown concepts found" in text
          and "INSPECT_RESULT: EMPTY" in text,
          f"exit={code}")


def main():
    tmp = tempfile.mkdtemp(prefix="okf-eval-")
    try:
        positive_arm(tmp)
        negative_arms(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n, k = len(_checks), sum(_checks)
    ok = k == n
    print(f"EVAL_RESULT: {'PASS' if ok else 'FAIL'} ({k}/{n} checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
