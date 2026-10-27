/* The colour-scheme half of the theme (lib/theme.ts) and the CSS it switches:
 * every scheme block in globals.css must define every colour token the default
 * (Měď) declares, or a scheme would silently fall back to Měď for that token. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import { applyScheme, COLOR_SCHEMES, readStoredScheme, writeStoredScheme } from './theme';

// vitest runs from frontend/; import.meta.url is not a file: URL under vite.
const css = readFileSync(join(process.cwd(), 'src/styles/globals.css'), 'utf8');

afterEach(() => {
  document.documentElement.removeAttribute('data-scheme');
  localStorage.clear();
});

describe('colour scheme storage', () => {
  it('defaults to Měď', () => {
    expect(readStoredScheme()).toBe('med');
  });

  it('reads back a stored scheme and ignores anything else', () => {
    writeStoredScheme('kobalt');
    expect(readStoredScheme()).toBe('kobalt');
    localStorage.setItem('sreality.scheme', 'terakota');
    expect(readStoredScheme()).toBe('med');
  });

  it('sets data-scheme for Kobalt and Grafit and clears it for Měď', () => {
    applyScheme('grafit');
    expect(document.documentElement.getAttribute('data-scheme')).toBe('grafit');
    applyScheme('med');
    expect(document.documentElement.hasAttribute('data-scheme')).toBe(false);
  });
});

describe('globals.css schemes', () => {
  const base = css.match(/@theme\s*\{([^}]*)\}/)![1];
  const tokens = [...base.matchAll(/(--color-[a-z0-9-]+):/g)].map((m) => m[1]);

  function block(selector: string): string {
    const at = css.indexOf(`${selector} {`);
    expect(at, `${selector} is missing`).toBeGreaterThan(-1);
    return css.slice(at, css.indexOf('}', at));
  }

  const selectors = [
    ':root[data-theme="dark"]',
    ':root:not([data-theme="light"])',
    ...COLOR_SCHEMES.filter((s) => s.value !== 'med').flatMap((s) => [
      `:root[data-scheme="${s.value}"]`,
      `:root[data-scheme="${s.value}"][data-theme="dark"]`,
      `:root[data-scheme="${s.value}"]:not([data-theme="light"])`,
    ]),
  ];

  it('declares the chrome and on-fill tokens in the default', () => {
    for (const t of ['--color-chrome', '--color-chrome-accent', '--color-on-copper', '--color-on-brick']) {
      expect(tokens).toContain(t);
    }
  });

  it.each(selectors)('%s defines every colour token', (selector) => {
    const body = block(selector);
    for (const t of tokens) expect(body, `${selector} lacks ${t}`).toContain(`${t}:`);
  });
});
