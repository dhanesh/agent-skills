// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import react from '@astrojs/react';

// Single source of truth for the deploy base path. Used for `base` AND to
// rewrite in-content absolute links (see rehypeBaseLinks below).
// For a GitHub Pages PROJECT page this MUST equal '/<repo-name>'. For a user/org
// root page (user.github.io) set it to '' (empty string).
const BASE = '{{DEPLOY_BASE}}';

// rehype plugin: prefix the deploy base onto absolute, in-content internal links.
// Markdown/MDX links written as `/cluster/page/` are NOT base-prefixed by Astro,
// so under a project-page base they 404. This rewrites them at build time, so
// authors keep writing clean `/cluster/page/` links and they resolve everywhere.
// Dependency-free walk over the HTML AST (no unist-util-visit needed).
/**
 * @param {object} [opts]
 * @param {string} [opts.base]
 */
function rehypeBaseLinks(opts = {}) {
  const prefix = (opts.base || '').replace(/\/$/, '');
  /** @param {any} node */
  const fix = (node) => {
    if (
      node.type === 'element' &&
      node.tagName === 'a' &&
      node.properties &&
      typeof node.properties.href === 'string'
    ) {
      const h = node.properties.href;
      // only internal absolute links not already under the base
      if (h.startsWith('/') && !h.startsWith('//') && h !== prefix && !h.startsWith(prefix + '/')) {
        node.properties.href = prefix + h;
      }
    }
    if (node.children) for (const child of node.children) fix(child);
  };
  /** @param {any} tree */
  return (tree) => fix(tree);
}

// {{SITE_TITLE}} — Astro + Starlight static docs site.
// React integration enables interactive islands (static-first widgets).
export default defineConfig({
  // GitHub Pages: `site` is the origin, `base` is the sub-path (repo name) for a
  // project page. Together they make internal links and assets resolve correctly.
  site: '{{GH_PAGES_HOST}}',
  base: BASE,
  markdown: {
    rehypePlugins: [[rehypeBaseLinks, { base: BASE }]],
  },
  integrations: [
    react(),
    starlight({
      title: '{{SITE_TITLE}}',
      description: '{{SITE_DESCRIPTION}}',
      // Bring the active topic into focus in the sidebar on load: scroll the
      // sidebar's own scroll-pane (never the window) so the current page —
      // inside its auto-expanded, collapsed group — is centred and visible.
      head: [
        {
          tag: 'script',
          content: `
            (function () {
              function focusActive() {
                try {
                  var link = document.querySelector('nav[aria-label="Main"] a[aria-current="page"]');
                  if (!link) return;
                  var pane = link.closest('.sidebar-pane');
                  if (!pane) { link.scrollIntoView({ block: 'center' }); return; }
                  var lr = link.getBoundingClientRect();
                  var pr = pane.getBoundingClientRect();
                  if (lr.top < pr.top || lr.bottom > pr.bottom) {
                    pane.scrollTop += (lr.top - pr.top) - pane.clientHeight / 2 + link.offsetHeight / 2;
                  }
                } catch (e) {}
              }
              document.addEventListener('DOMContentLoaded', focusActive);
              document.addEventListener('astro:page-load', focusActive);
            })();
          `,
        },
      ],
      // Sidebar: one entry per cluster, fixed order, each with an Overview page
      // (the cluster's index.mdx) followed by its topic pages.
      //
      // ── TO ADD A CLUSTER ──────────────────────────────────────────────
      // Append a new block of this exact shape. `slug` is the path under
      // src/content/docs/ with no extension; the cluster index uses the bare
      // cluster slug, topics use `<cluster>/<topic>`:
      //
      //   {
      //     label: 'Human Cluster Name',
      //     collapsed: true,
      //     items: [
      //       { label: 'Overview', slug: 'my-cluster' },
      //       { label: 'First topic', slug: 'my-cluster/first-topic' },
      //     ],
      //   },
      // ──────────────────────────────────────────────────────────────────
      sidebar: [
        {
          label: 'Example Cluster',
          collapsed: true,
          items: [
            { label: 'Overview', slug: 'example-cluster' },
            { label: 'Example topic', slug: 'example-cluster/example-topic' },
          ],
        },
      ],
    }),
  ],
});
