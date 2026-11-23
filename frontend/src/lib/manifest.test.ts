/* public/manifest.webmanifest is JSON: it can neither import lib/brand.ts nor
 * carry a comment saying it copies it. This pins the copy instead, plus the two
 * properties the brand-domain move relies on (no hostname anywhere) and the
 * icons it names actually shipping. */
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { APP_NAME } from './brand';
import { ROUTES } from './routes';

// vitest runs from frontend/; import.meta.url is not a file: URL under vite.
const publicDir = join(process.cwd(), 'public');
const manifest = JSON.parse(readFileSync(join(publicDir, 'manifest.webmanifest'), 'utf8'));
const tokens = readFileSync(join(process.cwd(), 'src/styles/tokens.css'), 'utf8');

describe('manifest.webmanifest', () => {
  it('names the app from lib/brand.ts', () => {
    expect(manifest.name).toBe(APP_NAME);
    /* The home-screen label: the header's wordmark (BrandMark), which is all the
     * header shows on a narrow screen too. */
    expect(manifest.short_name).toBe(APP_NAME.split(' ')[0]);
  });

  /* The installed app opens on the phone shell's start screen (#1766); a route
   * the router does not know would open on the 404 page. */
  it('starts on the feed', () => {
    expect(manifest.start_url).toBe(ROUTES.feed.pattern);
  });

  it('holds no hostname', () => {
    const urls = [manifest.id, manifest.start_url, manifest.scope, ...manifest.icons.map((i: { src: string }) => i.src)];
    for (const url of urls) expect(url).toMatch(/^\/(?!\/)/);
  });

  it('paints the paper token', () => {
    const paper = tokens.match(/:root, :host\s*\{[^}]*--color-paper:\s*(#[0-9a-f]{6})/i)?.[1];
    expect(manifest.theme_color).toBe(paper);
    expect(manifest.background_color).toBe(paper);
  });

  it('ships every icon it names', () => {
    for (const icon of manifest.icons) expect(existsSync(join(publicDir, icon.src))).toBe(true);
  });
});
