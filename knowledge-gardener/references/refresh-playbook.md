# Diff-aware refresh playbook

Load this when a sweep reported STALE, UNKNOWN, or ERROR subjects and you are about to
repair them. The governing principle: **refresh what moved, keep what didn't.** A stale
explainer is usually 90% correct — rewriting it from scratch throws away the walkthrough
work that produced it and the FAQ history grafted onto it. Walk the diff, not the whole
subject.

## STALE subjects (a fingerprint moved)

### 1. Scope the drift

For each stale **git** source, the sweep already gives you pinned vs current SHA, a
diffstat, and changed file names. Go one level deeper before editing anything:

```bash
git -C <locator> log --oneline <pinned>..HEAD          # what happened, in commits
git -C <locator> diff --stat <pinned>..HEAD            # where it happened
git -C <locator> diff <pinned>..HEAD -- <path>         # the hunks that matter
```

Strip any `+dirty` suffix from the pinned fingerprint before using it as a SHA. If the
pinned SHA is no longer reachable (rebase, force-push — the sweep notes this), you lose
the precise diff; fall back to reading the current state of the files the explainer's
segments describe and comparing against what the explainer claims.

For a stale **file** or **dir** source there is no diff, only "it changed": reread the
source (or the changed subtree) and compare it against the explainer's claims.

### 2. Map changed files to explainer segments

Read the subject's `explainer.md` segment by segment and ask of each: does any changed
file touch what this segment explains? Three buckets:

- **Untouched** — the diff never lands in this segment's territory: leave it alone.
- **Drifted** — code paths, names, behaviors, or numbers the segment states have
  changed: rewrite just that segment, at the same explanatory quality it already has
  (plain language first, the traced example updated to the new reality).
- **New territory** — the diff adds something no segment covers (a new module, a new
  flow): add a segment and a line for it in the explainer's map section.

Check the FAQ too: an answer invalidated by the change gets corrected in place, with a
one-line "(updated YYYY-MM-DD: …)" note so past readers aren't silently gaslit.

### 3. Re-pin and log

Re-pinning records "this text now describes *this* version of the sources." Prefer the
creating tool when it's installed — `feynman-walkthrough`'s okf.py does both steps:

```bash
python3 <feynman-walkthrough>/assets/okf.py pin <slug> --root <bundle-root>
```

Without it, edit the explainer frontmatter by hand — update each source's
`fingerprint:` and `pinned:` (today's date), and refresh the top-level `timestamp:`:

- **git** — `git -C <locator> rev-parse HEAD`, appending `+dirty` when
  `git -C <locator> status --porcelain` prints anything;
- **file** — `sha256sum <locator>` (first column);
- **dir** — sha256 over sorted (relpath, content-sha256) pairs; run the creating tool
  for this one rather than hand-rolling it, so the traversal matches exactly.

Then append the update to the bundle root's `log.md`, newest-first, under today's
heading (creating the heading if today has none):

```markdown
## 2026-07-21
* **Update**: Refreshed [Ingest pipeline](/ingest-pipeline/explainer.md) — retry logic segment rewritten for the new backoff module.
```

Rerun the sweep: the subject must now report FRESH (or UNKNOWN if it also carries
external sources — that's its steady state, not a failure).

## UNKNOWN subjects (nothing to compare)

External URL/topic sources have no local fingerprint, so the gardener can't rule on
them. Don't mark them resolved by fiat: re-fetch the source when you have the means and
compare judgment-wise against the explainer, or ask the user whether it changed. If it
did change, refresh the affected segments as above; either way, update the pin's
`pinned:` date to record when it was last checked. A source whose *path* has moved also
reports UNKNOWN — fix the `locator:` if the thing merely relocated.

## ERROR subjects (the pins themselves are broken)

An ERROR means the explainer's frontmatter can't be parsed — the knowledge may be fine,
but its pins are unreadable, so drift detection is blind. Repair the container first,
then judge freshness:

1. Open the explainer; the sweep's error message says what broke (unterminated
   frontmatter, a malformed pin missing `type`/`locator`, …).
2. Reconstruct the frontmatter — usually the body is intact and only the YAML block is
   damaged. `git log -p` on the bundle (when it's version-controlled) often has the
   last good copy.
3. Re-pin as in step 3 above — a repaired subject's fingerprints are suspect until
   re-recorded — and log the repair in `log.md`.

## After the refresh: the site

If the bundle has a published site, it is now showing the pre-refresh text. Regenerate
it with the `okf-site-kit` skill (`okf_site.py generate <bundle> --out <dir> --force`
plus its build step) or, when the site deploys from CI on push, commit the refreshed
bundle and let the workflow rebuild. The bundle stays canonical; the site is a view.
