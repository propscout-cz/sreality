import { defineConfig } from 'vite';
import { copyFileSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
// Shared product brand — the SAME source the SPA + content script use, so the
// chrome://extensions display name and the toolbar icon's title stay linked to
// the one definition.
import { APP_NAME, EXTENSION_NAME } from '../frontend/src/lib/brand';
// The portal-id rules (shared with the SPA's paste-a-link) are the single source
// of truth for "which hosts we run on" — content_scripts.matches is computed from
// them below; manifest.json's checked-in list is a template that
// frontend/src/lib/portalIds.test.ts keeps equal to them.
import { PORTALS } from '../frontend/src/lib/portalIds';

const APP_SRC = resolve(__dirname, '../frontend/src') + '/';

/* ONE ENTRY PER PASS — `npm run build` runs vite three times: content, background,
 * then the side panel's page.
 *
 * A manifest-declared content script is a CLASSIC script: MV3 has no module mode
 * for `content_scripts[].js`, so content.js may not contain a static import.
 * Rollup hoists any module shared BETWEEN entries into a chunk, and the portal
 * rules (`portals.ts` then, `frontend/src/lib/portalIds.ts` now) are imported by
 * content.ts AND background.ts (since #942, 2026-08-04), so the
 * old single two-entry build emitted `import ... from "./chunks/portals-*.js"` as
 * content.js line 1. Chrome refused the whole script — "Cannot use import
 * statement outside a module" — and the panel silently never mounted on any
 * portal page for six weeks. Nothing caught it: the build was green, the bundle
 * contained every feature, and only chrome://extensions showed the error.
 *
 * Separate passes keep each entry self-contained, and the classic-script guard
 * below fails the build if content.js ever regains a static import.
 *
 * Vite's default `dist/` layout would nest assets under hashed paths — Chrome's
 * manifest expects flat filenames (`content.js`, `background.js`,
 * `sidepanel.html`) at the dist root, so we override the output naming and copy
 * manifest.json and the page across as a post-build step. */

/* The content script is IIFE (classic, self-contained); the service worker stays
 * ESM because manifest.json declares `background.type = "module"`, and the side
 * panel's script is a module of its own page (sidepanel.html). */
const ENTRIES = {
  content: { file: 'src/content.ts', format: 'iife' },
  background: { file: 'src/background.ts', format: 'es' },
  sidepanel: { file: 'src/sidepanel.ts', format: 'es' },
} as const;

type EntryName = keyof typeof ENTRIES;
export default defineConfig(({ mode }) => {
  // `mode` selects the entry, so a bare `vite build` can't quietly emit half a
  // bundle: it fails here naming the command that does every pass.
  const entry = ENTRIES[mode as EntryName];
  if (entry == null) {
    throw new Error(
      `build one entry at a time: vite build --mode ${Object.keys(ENTRIES).join('|')} ` +
      `(got "${mode}"). Run \`npm run build\`, which runs every pass in order.`,
    );
  }

  return {
    build: {
      outDir: 'dist',
      // Only the first pass clears dist — a later one would delete content.js — and never
      // a watched rebuild (`npm run watch -- <entry>`), which keeps the other entries' files.
      emptyOutDir: mode === 'content' && !process.argv.includes('--watch'),
      // No source maps: the store package refuses them (release/package.ts). The code stays
      // unminified, so the bundle reads close to the source anyway.
      sourcemap: false,
      rollupOptions: {
        input: { [mode]: resolve(__dirname, entry.file) },
        output: {
          entryFileNames: '[name].js',
          assetFileNames: 'assets/[name][extname]',
          format: entry.format,
          // One entry per pass, so nothing is left to split out into a chunk.
          inlineDynamicImports: true,
        },
      },
      target: 'es2022',
      minify: false,
    },
    plugins: [
      {
        /* THE CROSS-TREE RAIL. The bundles take the app's pure modules from
         * frontend/src (one definition each), and such a module may import only
         * another plain .ts module, by relative path: a bare package (React,
         * Supabase), an `@/` alias or a .tsx component would load the app's
         * runtime into every portal page. Type-only imports are erased before
         * this runs, so they pass. */
        name: 'assert-pure-app-imports',
        enforce: 'pre',
        async resolveId(source, importer) {
          if (importer == null || !importer.startsWith(APP_SRC)) return null;
          const resolved = source.startsWith('.')
            ? await this.resolve(source, importer, { skipSelf: true })
            : null;
          if (resolved == null || !resolved.id.split('?')[0].endsWith('.ts')) {
            throw new Error(
              `${importer} imports "${source}": an app module the extension bundles may ` +
              'import only another plain .ts module by relative path (no package, no @/ ' +
              'alias, no .tsx). Move what the extension needs into such a module.',
            );
          }
          return null;
        },
      },
      {
        name: 'copy-static',
        closeBundle() {
          // manifest.json is the template; its display `name` and the toolbar
          // icon's title are stamped from the shared brand, so chrome://extensions
          // tracks the one source. Host permissions stay the template's empty
          // list: the background worker reaches the API and the auth service
          // under ordinary CORS, so no deployed hostname ever enters the
          // manifest (release/allowed.ts).
          const manifest = JSON.parse(
            readFileSync(resolve(__dirname, 'manifest.json'), 'utf8'),
          );
          manifest.name = EXTENSION_NAME;
          manifest.action.default_title = APP_NAME;
          manifest.content_scripts[0].matches = PORTALS.flatMap((p) =>
            p.hosts.map((h) => `https://${h}/*`),
          );
          /* Chrome only auto-updates when the offered version is STRICTLY greater
           * than the installed one, and the Web Store rejects re-uploading a
           * version that already exists — so a forgotten hand-bump silently
           * strands every installed copy on the old build (PR #942 shipped with
           * no bump). manifest.json's MAJOR.MINOR stays the hand-owned feature
           * declaration; CI stamps the patch from the run number, which is
           * monotonic and never resets. Local builds keep the committed version
           * so a dev reload doesn't churn it. */
          const runNumber = process.env.GITHUB_RUN_NUMBER;
          if (runNumber != null && runNumber !== '') {
            const [major, minor] = String(manifest.version).split('.');
            manifest.version = `${major}.${minor}.${runNumber}`;
          }
          writeFileSync(
            resolve(__dirname, 'dist', 'manifest.json'),
            JSON.stringify(manifest, null, 2) + '\n',
          );
          for (const name of ['icon-16.png', 'icon-48.png', 'icon-128.png', 'sidepanel.html']) {
            copyFileSync(
              resolve(__dirname, name),
              resolve(__dirname, 'dist', name),
            );
          }
        },
      },
      {
        /* THE RAIL. Compiles (never runs) the emitted bundle as a CLASSIC script,
         * which is exactly what Chrome does with a manifest-declared content
         * script — so a static import hoisted back into content.js fails the
         * build here, with the same SyntaxError chrome://extensions would show,
         * instead of shipping a bundle that loads nowhere. `new Function` only
         * parses: no DOM, no `chrome`, nothing executes. */
        name: 'assert-classic-content-script',
        closeBundle() {
          if (mode !== 'content') return;
          const built = resolve(__dirname, 'dist', 'content.js');
          try {
            new Function(readFileSync(built, 'utf8'));
          } catch (err) {
            throw new Error(
              'dist/content.js will not load as a classic script, so Chrome ' +
              'will refuse it and the panel will never mount. Something is ' +
              'shared with another entry and got hoisted into a chunk — keep ' +
              'one entry per build pass (see ENTRIES above). Parse error: ' +
              `${(err as Error).message}`,
            );
          }
        },
      },
    ],
  };
});
