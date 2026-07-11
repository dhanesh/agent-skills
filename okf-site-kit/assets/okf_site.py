#!/usr/bin/env python3
"""Generate a browsable Astro + Starlight website from any OKF bundle.

Reads an Open Knowledge Format (OKF v0.1) bundle — a directory of markdown
concept files with YAML frontmatter, reserved index.md/log.md files, and
arbitrary nesting (spec: https://github.com/GoogleCloudPlatform/knowledge-catalog/
blob/main/okf/SPEC.md) — and emits a complete static-site project: landing
page with hero + section cards, one page per concept with an OKF metadata
panel (type badge, tags, resource, timestamp, producer keys), rewritten
internal links, a changelog from log.md, full-text search (Pagefind via
Starlight), and light/dark theming.

Tolerates every bundle shape observed in the wild: Google's sample bundles
(block-list tags, folded multi-line descriptions, .md-suffixed relative
links, no okf_version, no log.md, non-markdown assets), spec-canonical
bundles (okf_version root index, log.md, bundle-absolute links), and
producer-extended bundles (arbitrary frontmatter keys, block lists of
dicts). Missing/unknown fields degrade gracefully per the spec's
consumer rules; violations are reported, never fatal.

Stdlib only — no pip, no network. npm is needed only to build the emitted
site. Examples:

    python3 okf_site.py inspect  ./knowledge
    python3 okf_site.py generate ./knowledge --out ./site --title "Team Knowledge"
    cd site && npm install && npm run build
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys

OKF_SPEC_VERSION = "0.1"
RESERVED = ("index.md", "log.md")
SKIP_DIRS = {".git", "node_modules", "__pycache__"}
RECOMMENDED_KEYS = ("type", "title", "description", "resource", "tags", "timestamp")


# ── Tolerant YAML frontmatter parser ─────────────────────────────────────────
# Real bundles use more YAML than any one producer writes: quoted and plain
# scalars, folded multi-line plain scalars (Google's descriptions), `|`/`>`
# block scalars, inline lists, block lists of scalars (Google's tags), and
# block lists of flat dicts (producer extensions). This parser covers that
# observed surface and NEVER raises — unparseable values are kept as raw
# strings and flagged, per the spec's tolerant-consumer rule.

def _scalar(value):
    value = value.strip()
    if value in ("null", "~", ""):
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def parse_frontmatter(text):
    """Return (meta, body, had_frontmatter). Never raises."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text, False
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() in ("---", "..."):
            end = idx
            break
    if end is None:
        return {}, text, False
    meta = {}
    i = 1
    try:
        while i < end:
            line = lines[i]
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or _indent(line) > 0:
                i += 1
                continue
            key, sep, rest = line.partition(":")
            if not sep:
                i += 1
                continue
            key, rest = key.strip(), rest.strip()
            # strip trailing YAML comment on plain scalars
            if rest and not rest.startswith(("'", '"')) and " #" in rest:
                rest = rest.split(" #", 1)[0].strip()
            if rest in ("|", ">", "|-", ">-", "|+", ">+"):
                block, i = _take_indented(lines, i + 1, end)
                joiner = "\n" if rest.startswith("|") else " "
                meta[key] = joiner.join(s.strip() for s in block).strip()
            elif rest == "":
                items, i = _take_block_list(lines, i + 1, end)
                if items is not None:
                    meta[key] = items
                else:
                    meta[key] = None
            elif rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1].strip()
                meta[key] = ([_scalar(p) for p in inner.split(",")] if inner else [])
                i += 1
            else:
                value, i = rest, i + 1
                # folded continuation: subsequent more-indented plain lines
                cont = []
                while (i < end and lines[i].strip()
                       and _indent(lines[i]) > 0
                       and not lines[i].lstrip().startswith("- ")):
                    cont.append(lines[i].strip())
                    i += 1
                if cont:
                    value = " ".join([value] + cont)
                meta[key] = _scalar(value)
    except Exception:  # tolerant by contract: keep what parsed so far
        meta["_okf_parse_error"] = "true"
    return meta, "\n".join(lines[end + 1:]).lstrip("\n"), True


def _take_indented(lines, start, end):
    block, i = [], start
    while i < end and (not lines[i].strip() or _indent(lines[i]) > 0):
        block.append(lines[i])
        i += 1
    return block, i


def _take_block_list(lines, start, end):
    """Parse `- scalar` or `- k: v` (+ indented continuations) items."""
    i = start
    while i < end and not lines[i].strip():
        i += 1
    if i >= end or not lines[i].lstrip().startswith("- "):
        return None, start
    items = []
    while i < end:
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("- "):
            entry = stripped[2:]
            k, sep, v = entry.partition(":")
            if sep and " " not in k.strip():
                items.append({k.strip(): _scalar(v)})
            else:
                items.append(_scalar(entry))
            i += 1
        elif _indent(lines[i]) > 0 and isinstance(items[-1] if items else None, dict):
            k, sep, v = stripped.partition(":")
            if sep:
                items[-1][k.strip()] = _scalar(v)
            i += 1
        else:
            break
    return items, i


# ── Bundle scanning ──────────────────────────────────────────────────────────

def humanize(name):
    name = re.sub(r"\.md$", "", name)
    name = re.sub(r"[-_]+", " ", name).strip()
    return name[:1].upper() + name[1:] if name else "Untitled"


def slug_segment(name):
    seg = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return seg or "page"


def first_h1(body):
    in_fence = False
    for line in body.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and re.match(r"#\s+", line):
            return line.lstrip("#").strip()
    return None


class Bundle:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.concepts = {}      # relpath -> (meta, body)
        self.indexes = {}       # dir relpath ('' for root) -> (meta, body)
        self.log = None         # body of root log.md
        self.assets = []        # relpaths of non-markdown files
        self.okf_version = None
        self.warnings = []


def scan_bundle(root):
    b = Bundle(root)
    if not os.path.isdir(root):
        raise FileNotFoundError("bundle root not found: %s" % root)
    for base, dirs, files in os.walk(b.root):
        # prune in place (sorted for determinism) so skipped dirs never walk
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS
                         and not d.startswith("."))
        rel_dir = os.path.relpath(base, b.root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        for name in sorted(files):
            if name.startswith("."):
                continue
            rel = (rel_dir + "/" + name) if rel_dir else name
            path = os.path.join(base, name)
            if not name.endswith(".md"):
                b.assets.append(rel)
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            meta, body, had_fm = parse_frontmatter(text)
            if name == "index.md":
                b.indexes[rel_dir] = (meta, body)
                if rel_dir == "" and meta.get("okf_version"):
                    b.okf_version = str(meta["okf_version"])
                elif rel_dir != "" and had_fm and meta:
                    b.warnings.append(
                        "non-root index.md has frontmatter (spec allows it "
                        "only at the bundle root): %s" % rel)
            elif name == "log.md":
                if rel_dir == "":
                    b.log = body if not had_fm else text
                else:
                    b.warnings.append("nested log.md treated as reserved "
                                      "and skipped: %s" % rel)
            else:
                if not had_fm:
                    b.warnings.append("concept without frontmatter "
                                      "(spec violation, rendered anyway): %s" % rel)
                elif not meta.get("type"):
                    b.warnings.append("concept missing required `type` "
                                      "(rendered as generic Concept): %s" % rel)
                if "_okf_parse_error" in meta:
                    b.warnings.append("frontmatter only partially parsed: %s" % rel)
                b.concepts[rel] = (meta, body)
    if not b.concepts and not b.indexes:
        b.warnings.append("no markdown concepts found — is this an OKF bundle?")
    return b


# ── Route planning ───────────────────────────────────────────────────────────

def plan_routes(bundle):
    """Map every bundle md path and directory to a site route."""
    routes = {}   # source key -> route ('' = homepage)
    taken = set()

    def claim(route):
        candidate, n = route, 2
        while candidate in taken:
            candidate = "%s-%d" % (route, n)
            n += 1
        taken.add(candidate)
        return candidate

    dir_set = {""}
    for rel in list(bundle.concepts) + [d + "/index.md" if d else "index.md"
                                        for d in bundle.indexes]:
        d = os.path.dirname(rel).replace(os.sep, "/")
        while d:
            dir_set.add(d)
            d = os.path.dirname(d)
    for d in sorted(dir_set):
        route = "/".join(slug_segment(p) for p in d.split("/")) if d else ""
        routes["dir:" + d] = claim(route) if d else ""
    for rel in sorted(bundle.concepts):
        d = os.path.dirname(rel).replace(os.sep, "/")
        stem = os.path.basename(rel)[:-3]
        base = routes["dir:" + d]
        route = (base + "/" if base else "") + slug_segment(stem)
        routes["md:" + rel] = claim(route)
    for d in bundle.indexes:
        routes["md:" + ((d + "/index.md") if d else "index.md")] = routes["dir:" + d]
    return routes


# ── Markdown transforms ──────────────────────────────────────────────────────

LINK_RE = re.compile(r"(!?\[[^\]]*\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\))")


def _outside_fences(body):
    """Yield (line, in_fence) pairs."""
    in_fence = False
    for line in body.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            yield line, True
        else:
            yield line, in_fence


def rewrite_links(body, current_dir, bundle, routes, unresolved):
    asset_set = set(bundle.assets)

    def resolve(target):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith(
                ("#", "//")):
            return None
        raw, _, frag = target.partition("#")
        raw = raw.rstrip()
        if not raw:
            return None
        if raw.startswith("/"):
            rel = os.path.normpath(raw.lstrip("/"))
        else:
            rel = os.path.normpath(os.path.join(current_dir, raw))
        rel = rel.replace(os.sep, "/")
        if rel == ".":
            rel = ""
        frag = ("#" + frag) if frag else ""
        if raw.endswith("/") or ("dir:" + rel) in routes and not raw.endswith(".md"):
            key = "dir:" + rel
            if key in routes:
                return "/" + routes[key] + "/" + frag if routes[key] else "/" + frag
        if rel.endswith(".md"):
            key = "md:" + rel
            if key in routes:
                route = routes[key]
                return ("/" + route + "/" + frag) if route else ("/" + frag)
        if rel in asset_set:
            return "/bundle-assets/" + rel + frag
        unresolved.append(target)
        return None

    def sub(match):
        new = resolve(match.group(2))
        return match.group(1) + (new or match.group(2)) + match.group(3)

    out = []
    for line, fenced in _outside_fences(body):
        out.append(line if fenced else LINK_RE.sub(sub, line))
    return "\n".join(out)


def normalize_headings(body, title):
    """Drop a leading H1 duplicating the title; demote if H1s remain."""
    lines = body.split("\n")
    # drop leading duplicate H1
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        m = re.match(r"#\s+(.*)", line)
        if m and m.group(1).strip() == (title or "").strip():
            lines = lines[:i] + lines[i + 1:]
        break
    body = "\n".join(lines)
    has_h1 = any(re.match(r"#\s", line) and not fenced
                 for line, fenced in _outside_fences(body))
    if not has_h1:
        return body
    out = []
    for line, fenced in _outside_fences(body):
        if not fenced and re.match(r"#{1,5}\s", line):
            out.append("#" + line)
        else:
            out.append(line)
    return "\n".join(out)


def _hue(text):
    return sum(ord(c) * 37 for c in text) % 360


def meta_panel(meta):
    """Render OKF frontmatter as an HTML metadata panel (raw HTML in .md).

    A concept with no/empty frontmatter still gets the generic `Concept`
    badge — the spec's rule for unknown types applied to missing ones.
    """
    esc = html.escape
    parts = ['<div class="okf-meta">']
    badges = []
    ctype = str(meta.get("type") or "Concept")
    badges.append('<span class="okf-type" style="--okf-hue: %d">%s</span>'
                  % (_hue(ctype), esc(ctype)))
    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    for tag in tags:
        if tag is not None and not isinstance(tag, dict):
            badges.append('<span class="okf-tag">%s</span>' % esc(str(tag)))
    parts.append('<p class="okf-badges">%s</p>' % " ".join(badges))
    rows = []
    if meta.get("resource"):
        res = str(meta["resource"])
        value = ('<a href="%s">%s</a>' % (esc(res), esc(res))
                 if re.match(r"^https?://", res) else "<code>%s</code>" % esc(res))
        rows.append(("Resource", value))
    if meta.get("timestamp"):
        rows.append(("Updated", esc(str(meta["timestamp"]))))
    for key, value in meta.items():
        if key in RECOMMENDED_KEYS or key.startswith("_okf_"):
            continue
        rows.append((esc(humanize(key)), _render_extension(value)))
    if rows:
        parts.append("<dl>")
        for label, value in rows:
            parts.append("<dt>%s</dt><dd>%s</dd>" % (label, value))
        parts.append("</dl>")
    parts.append("</div>")
    return "\n".join(parts)


def _render_extension(value):
    esc = html.escape
    if value is None:
        return "<code>null</code>"
    if isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, dict):
                pairs = ", ".join("%s: %s" % (esc(str(k)),
                                              esc(str(v)) if v is not None else "null")
                                  for k, v in item.items())
                items.append("<li><code>%s</code></li>" % pairs)
            else:
                items.append("<li>%s</li>" % esc(str(item)))
        return '<ul class="okf-ext">%s</ul>' % "".join(items)
    return esc(str(value))


def yaml_str(value):
    return '"%s"' % str(value).replace("\\", "\\\\").replace('"', '\\"')


# ── Site emission ────────────────────────────────────────────────────────────

def page_frontmatter(title, description=None, sidebar_label=None):
    lines = ["---", "title: %s" % yaml_str(title)]
    if description:
        one_line = " ".join(str(description).split())
        lines.append("description: %s" % yaml_str(one_line))
    if sidebar_label:
        # keeps a directory's index from repeating the group name in the sidebar
        lines.append("sidebar:")
        lines.append("  label: %s" % yaml_str(sidebar_label))
        lines.append("  order: 0")
    lines.append("---")
    return "\n".join(lines) + "\n"


def concept_page(rel, meta, body, bundle, routes, unresolved):
    title = (meta.get("title") or first_h1(body)
             or humanize(os.path.basename(rel)))
    current_dir = os.path.dirname(rel).replace(os.sep, "/")
    body = rewrite_links(body, current_dir, bundle, routes, unresolved)
    body = normalize_headings(body, title)
    return (page_frontmatter(title, meta.get("description"))
            + "\n" + meta_panel(meta) + "\n\n" + body.strip() + "\n")


def index_page(rel_dir, body, bundle, routes, unresolved, title=None):
    title = title or first_h1(body) or humanize(os.path.basename(rel_dir) or "Overview")
    body = rewrite_links(body, rel_dir, bundle, routes, unresolved)
    body = normalize_headings(body, title)
    label = "Overview" if rel_dir else None
    return (page_frontmatter(title, sidebar_label=label)
            + "\n" + body.strip() + "\n")


def synthetic_index(rel_dir, bundle, routes):
    """Directory listing for a directory that ships no index.md."""
    title = humanize(os.path.basename(rel_dir))
    lines = [page_frontmatter(title, sidebar_label="Overview"), ""]
    for rel in sorted(bundle.concepts):
        if os.path.dirname(rel).replace(os.sep, "/") == rel_dir:
            meta, body = bundle.concepts[rel]
            label = (meta.get("title") or first_h1(body)
                     or humanize(os.path.basename(rel)))
            desc = meta.get("description")
            entry = "* [%s](/%s/)" % (label, routes["md:" + rel])
            if desc:
                entry += " — %s" % " ".join(str(desc).split())
            lines.append(entry)
    for d in sorted({os.path.dirname(r).replace(os.sep, "/")
                     for r in bundle.concepts}):
        if d and os.path.dirname(d) == rel_dir and d != rel_dir:
            lines.append("* [%s](/%s/)" % (humanize(os.path.basename(d)),
                                           routes["dir:" + d]))
    return "\n".join(lines) + "\n"


def changelog_page(log_body, bundle, routes, unresolved):
    body = re.sub(r"^#\s+.*\n+", "", log_body, count=1)
    body = rewrite_links(body, "", bundle, routes, unresolved)
    return page_frontmatter("Changelog") + "\n" + body.strip() + "\n"


def _sidebar_js(bundle, routes, has_overview, has_changelog):
    groups = []
    start_items = []
    if has_overview:
        start_items.append("{ label: 'Bundle overview', slug: 'overview' }")
    if has_changelog:
        start_items.append("{ label: 'Changelog', slug: 'changelog' }")
    if start_items:
        groups.append("{ label: 'Start here', items: [%s] }"
                      % ", ".join(start_items))
    top_dirs = sorted({rel.split("/")[0] for rel in bundle.concepts if "/" in rel}
                      | {d.split("/")[0] for d in bundle.indexes if d})
    for d in top_dirs:
        # Starlight >= 0.39: autogenerate lives inside a group's `items`
        groups.append("{ label: %s, collapsed: false, items: "
                      "[{ autogenerate: { directory: %s } }] }"
                      % (json.dumps(humanize(d)), json.dumps(routes["dir:" + d])))
    root_concepts = [rel for rel in sorted(bundle.concepts) if "/" not in rel]
    if root_concepts:
        items = []
        for rel in root_concepts:
            meta, body = bundle.concepts[rel]
            label = (meta.get("title") or first_h1(body)
                     or humanize(os.path.basename(rel)))
            items.append("{ label: %s, slug: %s }"
                         % (json.dumps(label), json.dumps(routes["md:" + rel])))
        groups.append("{ label: 'Concepts', items: [%s] }" % ", ".join(items))
    return "[\n        " + ",\n        ".join(groups) + "\n      ]"


def homepage(opts, bundle, routes, has_overview, has_changelog):
    # Component-rendered links (LinkCard, hero actions) bypass the rehype
    # base rewrite that covers markdown links, so bake the base in here.
    base = (opts.get("base") or "").rstrip("/")
    tagline = opts.get("tagline") or _root_tagline(bundle) or \
        "A browsable Open Knowledge Format bundle."
    cards = []
    top_dirs = sorted({rel.split("/")[0] for rel in bundle.concepts if "/" in rel}
                      | {d.split("/")[0] for d in bundle.indexes if d})
    link_descs = _root_link_descriptions(bundle)
    for d in top_dirs:
        desc = link_descs.get(d, "Browse the %s concepts."
                              % humanize(d).lower())
        cards.append('<LinkCard title=%s href="%s/%s/" description=%s />'
                     % (json.dumps(humanize(d)), base, routes["dir:" + d],
                        json.dumps(desc)))
    for rel in sorted(bundle.concepts):
        if "/" not in rel:
            meta, body = bundle.concepts[rel]
            label = (meta.get("title") or first_h1(body)
                     or humanize(os.path.basename(rel)))
            desc = " ".join(str(meta.get("description") or
                                meta.get("type") or "Concept").split())
            cards.append('<LinkCard title=%s href="%s/%s/" description=%s />'
                         % (json.dumps(label), base, routes["md:" + rel],
                            json.dumps(desc)))
    actions = []
    if has_overview:
        actions.append("{ text: 'Bundle overview', link: '%s/overview/', "
                       "icon: 'open-book', variant: 'primary' }" % base)
    if has_changelog:
        actions.append("{ text: 'Changelog', link: '%s/changelog/', "
                       "variant: 'minimal' }" % base)
    return """---
title: %s
description: %s
template: splash
hero:
  title: %s
  tagline: %s
  actions: [%s]
---

import { CardGrid, LinkCard } from '@astrojs/starlight/components';

<CardGrid>
%s
</CardGrid>
""" % (yaml_str(opts["title"]), yaml_str(tagline), yaml_str(opts["title"]),
       yaml_str(tagline), ", ".join(actions), "\n".join(cards))


def _root_tagline(bundle):
    if "" not in bundle.indexes:
        return None
    _, body = bundle.indexes[""]
    for line, fenced in _outside_fences(body):
        s = line.strip()
        if s and not fenced and not s.startswith(("#", "*", "-", "|", ">", "!")):
            return " ".join(s.split())
    return None


def _root_link_descriptions(bundle):
    """Pull `* [label](dir/...) - description` rows from the root index."""
    descs = {}
    if "" not in bundle.indexes:
        return descs
    _, body = bundle.indexes[""]
    for m in re.finditer(r"^\s*[*+-]\s*\[[^\]]*\]\(([^)#\s]+)\)\s*[-—–:]?\s*(.*)$",
                         body, re.M):
        target = m.group(1).strip("/").replace("index.md", "").strip("/")
        target = target.split("/")[0]
        if target and m.group(2).strip():
            descs[target] = " ".join(m.group(2).split())
    return descs


# Site scaffolding (no template placeholders — values are interpolated here).

CONTENT_CONFIG = """import { defineCollection } from 'astro:content';
import { docsLoader } from '@astrojs/starlight/loaders';
import { docsSchema } from '@astrojs/starlight/schema';

export const collections = {
  docs: defineCollection({ loader: docsLoader(), schema: docsSchema() }),
};
"""

OKF_CSS = """/* OKF site theme — metadata panel, type badges, tag chips. */
:root {
  --sl-content-width: 50rem;
}
.okf-meta {
  border: 1px solid var(--sl-color-hairline);
  border-radius: 0.5rem;
  padding: 0.75rem 1rem;
  margin: 0 0 1.5rem 0;
  background: var(--sl-color-bg-sidebar);
  font-size: var(--sl-text-sm);
}
.okf-meta .okf-badges { margin: 0; display: flex; flex-wrap: wrap; gap: 0.4rem; }
.okf-type {
  display: inline-block;
  padding: 0.1rem 0.6rem;
  border-radius: 999px;
  font-weight: 600;
  color: hsl(var(--okf-hue, 210) 45%% 24%%);
  background: hsl(var(--okf-hue, 210) 85%% 88%%);
  border: 1px solid hsl(var(--okf-hue, 210) 55%% 70%%);
}
:root[data-theme='dark'] .okf-type {
  color: hsl(var(--okf-hue, 210) 80%% 85%%);
  background: hsl(var(--okf-hue, 210) 45%% 22%%);
  border-color: hsl(var(--okf-hue, 210) 45%% 38%%);
}
.okf-tag {
  display: inline-block;
  padding: 0.1rem 0.55rem;
  border-radius: 999px;
  background: var(--sl-color-gray-6);
  color: var(--sl-color-gray-2);
  border: 1px solid var(--sl-color-hairline);
}
.okf-meta dl {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.25rem 1rem;
  margin: 0.75rem 0 0 0;
}
.okf-meta dt { font-weight: 600; color: var(--sl-color-gray-2); }
.okf-meta dd { margin: 0; overflow-wrap: anywhere; }
.okf-meta .okf-ext { margin: 0; padding-left: 1rem; }
"""

FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<rect width="100" height="100" rx="18" fill="#3b5bdb"/>
<path d="M22 26h34a10 10 0 0 1 10 10v42h-34a10 10 0 0 1-10-10z" fill="#fff"/>
<path d="M66 26h12v42H66z" fill="#dbe4ff"/>
</svg>
"""

GITIGNORE = "node_modules/\ndist/\n.astro/\n"

TSCONFIG = """{
  "extends": "astro/tsconfigs/base",
  "include": [".astro/types.d.ts", "**/*"],
  "exclude": ["dist"]
}
"""

DEPLOY_YML = """name: Deploy site to GitHub Pages
on:
  push:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: true
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 20 }
      - run: npm ci || npm install
      - run: npm run build
      - uses: actions/upload-pages-artifact@v3
        with: { path: dist }
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
"""


def astro_config(opts):
    site_line = ("  site: %s,\n" % json.dumps(opts["site"])) if opts.get("site") else ""
    return """// Generated by okf_site.py — Astro + Starlight site over an OKF bundle.
import { defineConfig } from 'astro/config';
import { unified } from '@astrojs/markdown-remark';
import starlight from '@astrojs/starlight';

const BASE = %s;

// Prefix the deploy base onto absolute in-content links (markdown links are
// not base-prefixed by Astro). Dependency-free HTML-AST walk.
function rehypeBaseLinks(opts = {}) {
  const prefix = (opts.base || '').replace(/\\/$/, '');
  const fix = (node) => {
    if (node.type === 'element' && (node.tagName === 'a' || node.tagName === 'img')) {
      const attr = node.tagName === 'a' ? 'href' : 'src';
      const h = node.properties && node.properties[attr];
      if (typeof h === 'string' && h.startsWith('/') && !h.startsWith('//') &&
          h !== prefix && !h.startsWith(prefix + '/')) {
        node.properties[attr] = prefix + h;
      }
    }
    if (node.children) for (const child of node.children) fix(child);
  };
  return (tree) => fix(tree);
}

export default defineConfig({
%s  base: BASE,
  markdown: {
    processor: unified({ rehypePlugins: [[rehypeBaseLinks, { base: BASE }]] }),
  },
  integrations: [
    starlight({
      title: %s,
      description: %s,
      customCss: ['./src/styles/okf.css'],
      lastUpdated: false,
      pagination: false,
      sidebar: %s,
    }),
  ],
});
""" % (json.dumps(opts.get("base", "")), site_line,
       json.dumps(opts["title"]), json.dumps(opts.get("tagline") or
                                             "Browsable OKF bundle"),
       opts["sidebar_js"])


def package_json(opts):
    return json.dumps({
        "name": slug_segment(opts["title"]),
        "type": "module",
        "version": "0.1.0",
        "private": True,
        "scripts": {
            "dev": "astro dev",
            "build": "astro build",
            "preview": "astro preview",
        },
        "dependencies": {
            "@astrojs/markdown-remark": "^7.2.0",
            "@astrojs/starlight": "^0.40.0",
            "astro": "^6.4.5",
        },
    }, indent=2) + "\n"


def generate_site(bundle, out, opts):
    """Emit the full site project. Returns a report dict."""
    routes = plan_routes(bundle)
    unresolved = []
    docs = os.path.join(out, "src", "content", "docs")
    if os.path.exists(docs) and not opts.get("force"):
        raise FileExistsError(
            "output already contains generated content: %s (use --force)" % docs)
    for owned in ("src/content/docs", "public/bundle-assets"):
        shutil.rmtree(os.path.join(out, owned), ignore_errors=True)
    os.makedirs(docs, exist_ok=True)

    def write(rel, content):
        path = os.path.join(out, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    # concept pages
    for rel, (meta, body) in bundle.concepts.items():
        route = routes["md:" + rel]
        write("src/content/docs/%s.md" % route,
              concept_page(rel, meta, body, bundle, routes, unresolved))
    # directory index pages (authored or synthesized) for every directory in
    # the tree — including intermediate ones with only subdirectories —
    # except the bundle root (which becomes the homepage/overview)
    dirs = {key[4:] for key in routes if key.startswith("dir:")}
    for d in sorted(dirs):
        if not d:
            continue
        route = routes["dir:" + d]
        if d in bundle.indexes:
            content = index_page(d, bundle.indexes[d][1], bundle, routes,
                                 unresolved)
        else:
            content = synthetic_index(d, bundle, routes)
        write("src/content/docs/%s/index.md" % route, content)
    # bundle overview (root index body), changelog, homepage
    has_overview = "" in bundle.indexes and bundle.indexes[""][1].strip() != ""
    if has_overview:
        write("src/content/docs/overview.md",
              index_page("", bundle.indexes[""][1], bundle, routes,
                         unresolved, title="Bundle overview"))
    has_changelog = bundle.log is not None and bundle.log.strip() != ""
    if has_changelog:
        write("src/content/docs/changelog.md",
              changelog_page(bundle.log, bundle, routes, unresolved))
    opts = dict(opts)
    opts["sidebar_js"] = _sidebar_js(bundle, routes, has_overview, has_changelog)
    write("src/content/docs/index.mdx",
          homepage(opts, bundle, routes, has_overview, has_changelog))
    # assets
    for rel in bundle.assets:
        dst = os.path.join(out, "public", "bundle-assets", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(bundle.root, rel), dst)
    # project scaffolding
    write("src/content.config.ts", CONTENT_CONFIG)
    write("src/styles/okf.css", OKF_CSS.replace("%%", "%"))
    write("public/favicon.svg", FAVICON)
    write("astro.config.mjs", astro_config(opts))
    write("package.json", package_json(opts))
    write("tsconfig.json", TSCONFIG)
    write(".gitignore", GITIGNORE)
    write("README.md",
          "# %s\n\nGenerated from an OKF bundle by okf_site.py. "
          "`npm install && npm run dev` to browse, `npm run build` for the "
          "static site in `dist/`.\n" % opts["title"])
    if opts.get("deploy_workflow"):
        write(".github/workflows/deploy.yml", DEPLOY_YML)
    return {
        "pages": len(bundle.concepts),
        "sections": len([d for d in dirs if d]),
        "assets": len(bundle.assets),
        "okf_version": bundle.okf_version,
        "has_changelog": has_changelog,
        "unresolved_links": sorted(set(unresolved)),
        "warnings": bundle.warnings,
    }


# ── Reporting / CLI ──────────────────────────────────────────────────────────

def print_report(report, out_stream):
    w = out_stream.write
    w("OKF_VERSION: %s\n" % (report["okf_version"] or "undeclared"))
    w("PAGES: %d concept(s), %d section(s), %d asset(s)\n"
      % (report["pages"], report["sections"], report["assets"]))
    for link in report["unresolved_links"]:
        w("WARN: unresolved link left as-is: %s\n" % link)
    for warning in report["warnings"]:
        w("WARN: %s\n" % warning)


def default_title(bundle):
    if "" in bundle.indexes:
        h1 = first_h1(bundle.indexes[""][1])
        if h1:
            return h1
    return humanize(os.path.basename(bundle.root))


def main(argv=None, out=None):
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="emit a Starlight site project")
    p_gen.add_argument("bundle", help="OKF bundle root directory")
    p_gen.add_argument("--out", required=True, help="output project directory")
    p_gen.add_argument("--title", default=None, help="site title")
    p_gen.add_argument("--tagline", default=None, help="hero tagline")
    p_gen.add_argument("--base", default="",
                       help="deploy base path, e.g. /repo-name (default: '')")
    p_gen.add_argument("--site", default=None,
                       help="site origin, e.g. https://user.github.io")
    p_gen.add_argument("--deploy-workflow", action="store_true",
                       help="emit a GitHub Pages deploy workflow")
    p_gen.add_argument("--force", action="store_true",
                       help="overwrite previously generated content")

    p_ins = sub.add_parser("inspect", help="scan a bundle and report, no output")
    p_ins.add_argument("bundle", help="OKF bundle root directory")

    args = parser.parse_args(argv)
    try:
        bundle = scan_bundle(args.bundle)
    except FileNotFoundError as exc:
        parser.error(str(exc))

    if args.command == "inspect":
        routes = plan_routes(bundle)
        report = {
            "pages": len(bundle.concepts),
            "sections": len({os.path.dirname(r) for r in bundle.concepts
                             if os.path.dirname(r)} | {d for d in bundle.indexes
                                                       if d}),
            "assets": len(bundle.assets),
            "okf_version": bundle.okf_version,
            "unresolved_links": [],
            "warnings": bundle.warnings,
        }
        print_report(report, out)
        for rel in sorted(bundle.concepts):
            meta, _ = bundle.concepts[rel]
            out.write("CONCEPT: %s [%s] -> /%s/\n"
                      % (rel, meta.get("type") or "?", routes["md:" + rel]))
        out.write("INSPECT_RESULT: %s\n"
                  % ("OK" if bundle.concepts or bundle.indexes else "EMPTY"))
        return 0 if (bundle.concepts or bundle.indexes) else 1

    opts = {
        "title": args.title or default_title(bundle),
        "tagline": args.tagline,
        "base": args.base.rstrip("/"),
        "site": args.site,
        "deploy_workflow": args.deploy_workflow,
        "force": args.force,
    }
    try:
        report = generate_site(bundle, args.out, opts)
    except FileExistsError as exc:
        parser.error(str(exc))
    print_report(report, out)
    out.write("SITE_GENERATED: %s\n" % args.out)
    out.write("NEXT: cd %s && npm install && npm run build\n" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
