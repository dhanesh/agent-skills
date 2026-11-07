# okf-site-build: build a static site from an OKF bundle with hostile content kept inert

- id: okf-site-build
- proven: no
- anchors: okf-site-kit/assets/okf_site.py:scan_bundle, okf-site-kit/assets/okf_site.py:rewrite_links, okf-site-kit/assets/okf_site.py:concept_page, okf-site-kit/assets/okf_site.py:index_page, okf-site-kit/assets/okf_site.py:changelog_page, okf-site-kit/assets/okf_site.py:homepage, okf-site-kit/assets/okf_site.py:_root_link_descriptions, okf-site-kit/assets/okf_site.py:generate_site, okf-site-kit/references/bundle-variations.md

## What it is

**Status: planned (run 5).** `okf_site.py generate <bundle> --out <dir>` (okf-site-kit)
reads an OKF bundle and writes an Astro + Starlight project: one `.md` page per concept
under `src/content/docs/`, `overview.md` from the root `index.md`, `changelog.md` from
`log.md`, the homepage `src/content/docs/index.mdx` with one `<LinkCard>` per section and
root concept, and a copy of every non-markdown asset under `public/bundle-assets/`. It
runs no npm and no Astro build. After run 5, these rules apply:

- `okf-site-kit/references/bundle-variations.md` holds no link to witscode.com.
- A bundle title or description that holds `"`, `{`, `}`, `<` or `>` lands in the
  `<LinkCard title=... description=... />` attributes of `index.mdx` as one literal
  string. It cannot end the attribute, add an attribute or add a JSX expression.
- A markdown body link with a `javascript:`, `vbscript:` or `data:` scheme (in any
  letter case), and raw `<script>` tags or `on*=` handler attributes in a bundle body,
  do not reach the generated `.md` pages as live markup. This covers concept pages,
  `overview.md` and `changelog.md`.
- A bundle asset that is a symlink to a file outside the bundle is not copied into
  `public/bundle-assets/`. A normal asset is still copied.

The sinks are named in
`~/security-audit-skill/agent-skills/run-1/NEEDS-VALIDATION.md`, section C: `homepage`
uses `json.dumps` inside a JSX string attribute, where JSX does not honour backslash
escapes; `rewrite_links` keeps any target with a scheme, and the page builders copy
bodies raw; `scan_bundle` lists file symlinks and `generate_site` copies them with
`shutil.copyfile`, which follows the link.

## How to reach it

- `python3 okf-site-kit/assets/okf_site.py generate <bundle> --out <dir> [--title T]
  [--tagline T] [--base /x] [--site URL] [--deploy-workflow] [--force]` prints
  `OKF_VERSION:`, `PAGES: <n> concept(s), <n> section(s), <n> asset(s)`, `WARN:` lines,
  `SITE_GENERATED: <dir>` and a `NEXT: ... npm install && npm run build` line. It exits 0.
  It does not run that `NEXT:` command.
- `python3 okf-site-kit/assets/okf_site.py inspect <bundle>` scans only and writes
  nothing.
- Output layout: `<out>/src/content/docs/index.mdx`, `<out>/src/content/docs/<route>.md`,
  `<out>/src/content/docs/overview.md`, `<out>/src/content/docs/changelog.md`,
  `<out>/src/content/docs/<dir>/index.md` and `<out>/public/bundle-assets/<rel>`.

## Drive it

Build one hostile bundle. Every payload calls `okfProbe`, which does not exist, so it is
harmless even if a browser ever runs it. Nothing here needs network or npm.

```sh
OS="$WT/.verify-run/$INSTANCE/os"
OUT="$WT/.verify-run/$INSTANCE"
B="$OS/bundle"
mkdir -p "$B/guides" "$B/assets" "$OS/outside"
printf 'OKF-DUMMY-SECRET-123\n' > "$OS/outside/secret.txt"
printf 'plain asset\n' > "$B/assets/ok.txt"
ln -s ../../outside/secret.txt "$B/assets/leak.txt"
cat > "$B/index.md" <<'MD'
---
okf_version: "0.1"
---
# Probe bundle

A plain tagline for the probe bundle.

* [Guides](guides/) - Guides" data-dirprobe={okfProbe()} x="

Overview links: [js](javascript:okfProbe) [vb](vbscript:okfProbe) [mixed](JaVaScRiPt:okfProbe) [dt](data:text/html;base64,PHA+) and <img src="x" onerror="okfProbe()"> and <script>okfProbe()</script>
MD
cat > "$B/log.md" <<'MD'
# Log

## 2026-11-07
* [js](javascript:okfProbe) <a href="#" onclick="okfProbe()">c</a> <script>okfProbe()</script>
MD
cat > "$B/guides/intro.md" <<'MD'
---
type: Guide
title: Intro
---
# Intro

Read the [overview](../index.md).
MD
cat > "$B/evil.md" <<'MD'
---
type: Concept
title: 'Evil" data-probe={okfProbe()} x="'
description: 'Desc" onmouseover={okfProbe()} <b>{1}</b> x="'
---
# Evil

Body links: [js](javascript:okfProbe) [vb](vbscript:okfProbe) [mixed](JaVaScRiPt:okfProbe) [dt](data:text/html;base64,PHA+)

<img src="x" onerror="okfProbe()">

<script>okfProbe()</script>

<div onclick="okfProbe()">click</div>
MD
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name os-generate -- \
  python3 okf-site-kit/assets/okf_site.py generate "$B" --out "$OS/site"
S="$OS/site"
ls -R "$S/src/content/docs" > "$OUT/os-docs-ls.txt"
cp "$S/src/content/docs/index.mdx" "$OUT/os-index.mdx"
```

Check the `<LinkCard>` attributes the way a JSX parser reads them: a quoted value ends
at the next `"` (JSX has no backslash escapes; HTML entities are decoded), and a `{...}`
value passes only when it holds one JSON string literal.

```sh
python3 - "$S/src/content/docs/index.mdx" > "$OUT/os-jsx-attrs.txt" <<'PY'
import html, json, re, sys
want = {"Evil": 'Evil" data-probe={okfProbe()} x="',
        "Desc": 'Desc" onmouseover={okfProbe()} <b>{1}</b> x="',
        "Guides": 'Guides" data-dirprobe={okfProbe()} x="'}
ok = True
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line.startswith("<LinkCard"):
        continue
    i, attrs, bad = len("<LinkCard"), {}, None
    while True:
        while i < len(line) and line[i] == " ":
            i += 1
        if line[i:] == "/>":
            break
        m = re.compile(r"[A-Za-z_][\w:.-]*").match(line, i)
        if not m or line[m.end():m.end() + 1] != "=":
            bad = "unparsable at %r" % line[i:i + 30]
            break
        name, i = m.group(0), m.end() + 1
        if line[i] == '"':
            j = line.index('"', i + 1)
            value, i = html.unescape(line[i + 1:j]), j + 1
        elif line[i] == "{":
            try:
                value, j = json.JSONDecoder().raw_decode(line, i + 1)
            except ValueError:
                bad = "attribute %s holds a JS expression, not a string literal" % name
                break
            if not isinstance(value, str) or line[j:j + 1] != "}":
                bad = "attribute %s holds a JS expression, not a string literal" % name
                break
            i = j + 1
        else:
            bad = "unparsable value for %s" % name
            break
        if name in attrs:
            bad = "duplicate attribute %s" % name
            break
        attrs[name] = value
    extra = sorted(set(attrs) - {"title", "href", "description"})
    if bad or extra:
        ok = False
        print("CARD BAD: %s extra=%s" % (bad, extra))
    else:
        print("CARD OK: attrs=%s" % sorted(attrs))
    for key, text in want.items():
        for attr in ("title", "description"):
            if attrs.get(attr, "").startswith(key) and attrs[attr] != text and not bad:
                ok = False
                print("CARD BAD: %s is %r, want %r" % (attr, attrs[attr], text))
print("JSX_ATTRS: %s" % ("PASS" if ok else "FAIL"))
PY
```

Scan the body of every generated `.md` page (frontmatter skipped) for live script markup.

```sh
python3 - "$S/src/content/docs" > "$OUT/os-live-markup.txt" <<'PY'
import os, re, sys
pats = {
  "md-link-scheme": re.compile(r"\]\(\s*(javascript|vbscript|data):", re.I),
  "href-scheme": re.compile(r"""href\s*=\s*["']?\s*(javascript|vbscript|data):""", re.I),
  "script-tag": re.compile(r"<\s*script", re.I),
  "on-handler": re.compile(r"<[a-z][^>]*\son[a-z]+\s*=", re.I),
}
hits = 0
for base, _, files in os.walk(sys.argv[1]):
    for name in sorted(f for f in files if f.endswith(".md")):
        path = os.path.join(base, name)
        rel = os.path.relpath(path, sys.argv[1])
        lines = open(path, encoding="utf-8").read().split("\n")
        start = lines.index("---", 1) + 1 if lines[0] == "---" else 0
        for n, line in enumerate(lines[start:], start + 1):
            for label, pat in pats.items():
                if pat.search(line):
                    hits += 1
                    print("LIVE: %s:%d %s" % (rel, n, label))
print("LIVE_MARKUP: %d" % hits)
PY
ls -lR "$S/public/bundle-assets" > "$OUT/os-assets-ls.txt"
grep -rl OKF-DUMMY-SECRET-123 "$S" > "$OUT/os-secret-grep.txt" 2>&1
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name os-witscode -- \
  grep -rni witscode okf-site-kit
```

- `os-generate`: exit 0, `SITE_GENERATED: <OS>/site`. The `PAGES:` line counts 2
  concepts and 1 section. A `WARN:` line about the symlinked asset is allowed.
- `os-docs-ls.txt`: `changelog.md`, `evil.md`, `index.mdx`, `overview.md` and
  `guides/` with `index.md` and `intro.md`. The pages still exist.
- `os-jsx-attrs.txt`: two `CARD OK:` lines, each with `attrs=['description', 'href',
  'title']`, no `CARD BAD:` line, and the last line `JSX_ATTRS: PASS`. Each title and
  description reads back as the exact bundle text (target b).
- `os-live-markup.txt`: no `LIVE:` line and the last line `LIVE_MARKUP: 0` (target c).
- `os-assets-ls.txt`: `ok.txt` is present under `assets`. There is no `leak.txt`, as a
  file or as a link (target d).
- `os-secret-grep.txt`: empty. No file under the site holds `OKF-DUMMY-SECRET-123`
  (target d).
- `os-witscode`: exit 1 and empty stdout. No file in `okf-site-kit/` names witscode
  (target a).

## Proof

The `os-witscode` capture with exit 1 (target a). `os-index.mdx` and
`os-jsx-attrs.txt` with `JSX_ATTRS: PASS` (target b). `os-live-markup.txt` with
`LIVE_MARKUP: 0`, and `os-docs-ls.txt` to show that the pages it scanned exist
(target c). `os-assets-ls.txt` with `ok.txt` and no `leak.txt`, and an empty
`os-secret-grep.txt` (target d). The `os-generate` capture for the run itself. Give every
file to `record` with `--artifact`.

## Gotchas

- Before the fix (dry run on `factory/run5-base`, 2026-11-07):
  - `os-witscode`: exit 0 and one line,
    `okf-site-kit/references/bundle-variations.md:77:  [WitsCode](https://witscode.com/open-knowledge-format)) ...`.
  - `os-index.mdx` held `<LinkCard title="Evil\" data-probe={okfProbe()} x=\"" ...`.
    `os-jsx-attrs.txt` held two `CARD BAD:` lines (`attribute data-dirprobe holds a JS
    expression` and `attribute data-probe holds a JS expression`) and
    `JSX_ATTRS: FAIL`.
  - `os-live-markup.txt` held 10 `LIVE:` lines (`changelog.md:6`, `evil.md:12`, `:14`,
    `:16`, `:18` and `overview.md:11`) and `LIVE_MARKUP: 10`. The bodies were copied
    word for word. `JaVaScRiPt:okfProbe` gave `WARN: unresolved link left as-is` and
    stayed in the page.
  - `os-assets-ls.txt` held `leak.txt` as a regular file of 21 bytes, and
    `os-secret-grep.txt` named `<OS>/site/public/bundle-assets/assets/leak.txt`.
    `os-generate` said `2 asset(s)`.
- The checks look at outcomes, not at one escape method. Expression containers
  (`title={"..."}`) and HTML entities (`&quot;`) both pass `os-jsx-attrs.txt`. Dropping
  a bad link, replacing its target or escaping `<` all pass `os-live-markup.txt`.
- Keep the plain tagline line in `index.md`. The first prose line of the root index
  becomes the hero `tagline` in `index.mdx` frontmatter. That value is not in scope
  here, and Starlight can render the hero tagline as HTML (not checked against the
  pinned Starlight version). A payload in that line is a separate lead.
- The live-markup scan skips `index.mdx` (target b covers it) and the YAML frontmatter
  of each `.md` page (its values are quoted strings, not markup).
- Do not run `npm install` or `npm run build` for this proof. They need network, and the
  generated sources already show the outcome.
- Every file the recipe writes is under `$WT/.verify-run/$INSTANCE/`. The recipe makes
  no git call.
- `proven: no` stays until the recipe is driven with SHA-bound evidence.
