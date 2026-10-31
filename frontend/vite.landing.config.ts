import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

/* The propscout.cz landing page (landing/*.html, vanilla TS in src/landing/, no React): a second build into the
 * SPA's dist/, run after it (package.json `build`). Its own build so the SPA's chunk graph never sees it — the SPA's
 * entry name, its zero modulepreload links (vite.config.ts) and its chunks stay exactly as they are. The pages land in
 * dist/landing/, their scripts and styles in dist/assets/ beside the SPA's (a shared font is the same hashed file).
 * The Caddyfile serves them on the bare domain only; `%VITE_API_BASE_URL%` in the forms is filled at build time. */
export default defineConfig({
  publicDir: false, // public/ (og.png included) is the SPA build's to copy
  build: {
    target: 'es2022',
    outDir: 'dist',
    emptyOutDir: false,
    modulePreload: { polyfill: false },
    rollupOptions: {
      input: {
        index: fileURLToPath(new URL('./landing/index.html', import.meta.url)),
        dekujeme: fileURLToPath(new URL('./landing/dekujeme.html', import.meta.url)),
        soukromi: fileURLToPath(new URL('./landing/soukromi.html', import.meta.url)),
      },
      // A landing- prefix on its scripts and styles, so dist/assets/ says whose file is whose.
      output: {
        entryFileNames: 'assets/landing-[name]-[hash].js',
        chunkFileNames: 'assets/landing-[name]-[hash].js',
        assetFileNames: 'assets/landing-[name]-[hash][extname]',
      },
    },
  },
});
