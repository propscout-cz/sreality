/* The appearance store (lib/theme.ts), its pre-paint copy in index.html, its
 * sync with the user's profile, and the palette it switches (styles/tokens.css):
 * every theme must define every colour token the default (Měď light) declares,
 * or it would silently fall back to Měď for that token. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

// vitest runs from frontend/; import.meta.url is not a file: URL under vite.
const tokens = readFileSync(join(process.cwd(), 'src/styles/tokens.css'), 'utf8');
const prePaint = readFileSync(join(process.cwd(), 'index.html'), 'utf8').match(/<script>([\s\S]*?)<\/script>/)![1];
const root = document.documentElement;
const realMatchMedia = window.matchMedia;

/* A fresh store: it reads the device cache once, when imported. */
async function load() {
  vi.resetModules();
  return import('./theme');
}

/* An OS light/dark the test can flip. */
function osDark(dark: boolean) {
  const listeners: Array<() => void> = [];
  const query = { get matches() { return dark; }, addEventListener: (_: string, l: () => void) => listeners.push(l) };
  window.matchMedia = (() => query) as unknown as typeof window.matchMedia;
  return (next: boolean) => {
    dark = next;
    for (const l of listeners) l();
  };
}

const attrs = () => [root.getAttribute('data-scheme'), root.getAttribute('data-theme')];
const settle = () => new Promise((r) => setTimeout(r, 0));
const user = (appearance?: unknown) => ({ id: 'u1', user_metadata: appearance === undefined ? {} : { appearance } });

afterEach(() => {
  window.matchMedia = realMatchMedia;
  root.removeAttribute('data-scheme');
  root.removeAttribute('data-theme');
  localStorage.clear();
});

describe('appearance store', () => {
  it.each([
    [null, null, true, [null, 'light']],
    ['kobalt', 'dark', false, ['kobalt', 'dark']],
    ['grafit', 'system', true, ['grafit', 'dark']],
    ['med', 'system', false, [null, 'light']],
    ['terakota', 'sepia', true, [null, 'light']],
  ])('reads %s / %s (OS dark %s) from the device cache, and index.html paints the same', async (scheme, mode, dark, expected) => {
    if (scheme) localStorage.setItem('sreality.scheme', scheme);
    if (mode) localStorage.setItem('sreality.theme', mode);
    osDark(dark);
    new Function(prePaint)();
    expect(attrs()).toEqual(expected);
    root.removeAttribute('data-scheme');
    root.removeAttribute('data-theme');
    (await load()).applyAppearance();
    expect(attrs()).toEqual(expected);
  });

  it('is one state for every picker, cached under its keys', async () => {
    const theme = await load();
    const a = renderHook(() => [theme.useScheme(), theme.useTheme()] as const);
    const b = renderHook(() => [theme.useScheme(), theme.useTheme()] as const);
    act(() => a.result.current[0][1]('grafit'));
    act(() => b.result.current[1][1]('dark'));
    expect([a.result.current[1][0], b.result.current[0][0]]).toEqual(['dark', 'grafit']);
    expect(attrs()).toEqual(['grafit', 'dark']);
    expect([localStorage.getItem('sreality.scheme'), localStorage.getItem('sreality.theme')]).toEqual(['grafit', 'dark']);
  });

  it('resolves system with the OS and follows its flip', async () => {
    const flip = osDark(true);
    const theme = await load();
    theme.setTheme('system');
    expect(attrs()).toEqual([null, 'dark']);
    flip(false);
    expect(attrs()).toEqual([null, 'light']);
  });
});

describe('appearance in the profile', () => {
  it("applies and caches the profile's choice over the device's", async () => {
    localStorage.setItem('sreality.scheme', 'kobalt');
    const theme = await load();
    const save = vi.fn();
    theme.syncAppearance(user({ scheme: 'grafit', theme: 'dark' }), save);
    expect(attrs()).toEqual(['grafit', 'dark']);
    expect(localStorage.getItem('sreality.scheme')).toBe('grafit');
    expect(save).not.toHaveBeenCalled();
  });

  it("uploads this device's choice to a profile without one, once", async () => {
    localStorage.setItem('sreality.scheme', 'kobalt');
    const theme = await load();
    const save = vi.fn().mockResolvedValue(undefined);
    theme.syncAppearance(user(), save);
    theme.syncAppearance(user(), save);
    await settle();
    theme.syncAppearance(user(), save);
    expect(save.mock.calls).toEqual([[{ scheme: 'kobalt', theme: 'light' }]]);
  });

  it('ignores a value it does not know, and never overwrites it', async () => {
    const theme = await load();
    const save = vi.fn();
    theme.syncAppearance(user({ scheme: 'terakota', theme: 'dark' }), save);
    theme.syncAppearance(user('grafit'), save);
    theme.applyAppearance();
    expect(attrs()).toEqual([null, 'light']);
    expect(save).not.toHaveBeenCalled();
  });

  it('applies a change at once, keeps it when the write fails, and sends it with the next change', async () => {
    const theme = await load();
    const save = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(undefined);
    theme.syncAppearance(user({ scheme: 'med', theme: 'light' }), save);
    theme.setScheme('grafit');
    expect(attrs()).toEqual(['grafit', 'light']);
    await settle();
    theme.syncAppearance(user({ scheme: 'med', theme: 'light' }), save);
    expect(attrs()).toEqual(['grafit', 'light']);
    theme.setTheme('dark');
    expect(save.mock.calls).toEqual([[{ scheme: 'grafit', theme: 'light' }], [{ scheme: 'grafit', theme: 'dark' }]]);
  });

  it('keeps a signed-out change on the device', async () => {
    const theme = await load();
    const save = vi.fn();
    theme.syncAppearance(null, save);
    theme.setScheme('kobalt');
    expect(localStorage.getItem('sreality.scheme')).toBe('kobalt');
    expect(save).not.toHaveBeenCalled();
  });
});

describe('tokens.css', () => {
  /* Every block a selector heads, joined (the tag palette has blocks of its own). */
  function block(selector: string): string {
    const parts = tokens.split(`${selector} {`).slice(1);
    expect(parts.length, `${selector} is missing`).toBeGreaterThan(0);
    return parts.map((p) => p.slice(0, p.indexOf('}'))).join('\n');
  }
  const either = (attrs: string) => `:root${attrs}, :host(${attrs})`;
  const base = [...block(':root, :host').matchAll(/(--color-[a-z0-9-]+):/g)].map((m) => m[1]);
  const dark = either('[data-theme="dark"]');
  const schemes = ['kobalt', 'grafit'].flatMap((s) => [either(`[data-scheme="${s}"]`), either(`[data-scheme="${s}"][data-theme="dark"]`)]);

  it('declares the chrome, on-fill and tag tokens in the default', () => {
    for (const t of ['--color-chrome', '--color-chrome-accent', '--color-on-copper', '--color-on-brick', '--color-tag-sand']) {
      expect(base).toContain(t);
    }
  });

  /* Tags are the user's colours: only light/dark changes them, never the scheme. */
  it.each([dark, ...schemes])('%s defines every colour token', (selector) => {
    const body = block(selector);
    const needed = selector === dark ? base : base.filter((t) => !t.startsWith('--color-tag-'));
    for (const t of needed) expect(body, `${selector} lacks ${t}`).toContain(`${t}:`);
  });

  it('pairs every document-root rule with its shadow-host twin, and keeps no OS-following copy', () => {
    const rules = [...tokens.matchAll(/^(:[^{\n]*)\{/gm)].map((m) => m[1].trim());
    expect(rules).toHaveLength(8);
    for (const rule of rules) {
      const attrs = rule.split(',')[0].slice(':root'.length);
      expect(rule).toBe(attrs ? either(attrs) : ':root, :host');
    }
    expect(tokens).not.toMatch(/@media|:not\(/);
  });
});
