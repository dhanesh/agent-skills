# OKF v0.1 — the consumer-side summary

The Open Knowledge Format is Google's open, vendor-neutral spec for representing
knowledge as a directory of markdown files that humans can read without tooling and
agents can parse without an SDK. Canonical spec:
<https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md>.

This skill is a **consumer** of OKF; the rules below are the ones a consumer must
honor, and the generator implements all of them.

## Bundle structure

- A **bundle** is a directory tree of UTF-8 markdown files; hierarchy is free-form.
- Every non-reserved `.md` file is a **concept**: YAML frontmatter + markdown body.
- **Reserved files**: `index.md` (a link listing for progressive disclosure — sections
  of `* [Title](url) - description` bullets; no frontmatter, except the bundle root
  where `okf_version: "0.1"` may be declared) and `log.md` (update history:
  `## YYYY-MM-DD` headings, newest first).

## Concept frontmatter

- Required: `type` — a short, producer-chosen string (no central registry).
- Recommended: `title`, `description`, `resource` (URI of the underlying asset),
  `tags`, `timestamp` (ISO 8601).
- Producers MAY add any other keys; consumers MUST tolerate and preserve them.

## Body conventions

- Links are directed edges: bundle-absolute (`/dir/file.md`, recommended) or relative;
  no typed semantics.
- Optional conventional sections: `# Schema`, `# Examples`, `# Citations`.

## Consumer conformance rules (the generator's contract)

- Tolerate unknown `type` values — treat as generic concepts.
- Tolerate missing optional fields and files (`index.md`, `log.md`, `okf_version` are
  all optional).
- Tolerate broken links — degrade, don't fail.
- Preserve producer-defined frontmatter keys (the generator surfaces them in each
  page's metadata panel).

## Keeping current

OKF v0.1 is explicitly an early spec ("a starting point, not a finished standard").
Before generating from a bundle when network access exists — and always when a bundle
declares an `okf_version` other than `0.1` — check the spec URL above. If the spec has
moved: update `assets/okf_site.py` (parser, reserved-file handling, emission),
add fixtures for the new constructs to `assets/test_okf_site.py`, and record the new
dialect in [bundle-variations.md](bundle-variations.md). Never silently emit a site
from constructs the generator didn't understand — the `WARN:` report exists so nothing
is dropped without a trace.
