/* The appearance store (lib/theme.ts), its pre-paint copy in index.html, its
 * sync with the user's profile, and the palette it switches (styles/tokens.css):
 * every theme must define every colour token the default (Měď light) declares,
 * or it would silently fall back to Měď for that token. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { COLOR_SCHEMES, THEME_MODES } from './theme';

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
const user = (appearance?: unknown, id = 'u1') => ({ id, user_metadata: appearance === undefined ? {} : { appearance } });

afterEach(() => {
  window.matchMedia = realMatchMedia;
  root.removeAttribute('data-scheme');
  root.removeAttribute('data-theme');
  localStorage.clear();
});

/* Every cached scheme and mode on either OS side, an empty cache and an unknown
 * one: index.html's copy of the lists must keep up with the store's. */
const caches: Array<[string | null, string | null, boolean]> = [
  ...COLOR_SCHEMES.flatMap((s) => THEME_MODES.flatMap((m) => [true, false].map((d): [string, string, boolean] => [s.value, m.value, d]))),
  [null, null, true],
  ['terakota', 'sepia', true],
];

describe('appearance store', () => {
  it.each(caches)('reads %s / %s (OS dark %s) from the device cache, and index.html paints the same', async (scheme, mode, dark) => {
    if (scheme) localStorage.setItem('sreality.scheme', scheme);
    if (mode) localStorage.setItem('sreality.theme', mode);
    osDark(dark);
    const side = mode === 'dark' || (mode === 'system' && dark) ? 'dark' : 'light';
    const expected = [COLOR_SCHEMES.some((s) => s.value === scheme && s.value !== 'med') ? scheme : null, side];
    new Function(prePaint)();
    expect(attrs()).toEqual(expected);
    root.removeAttribute('data-scheme');
    root.removeAttribute('data-theme');
    (await load()).applyAppearance();
    expect(attrs()).toEqual(expected);
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

  /* A device that never chose must not overwrite a choice made on another one. */
  it.each([[{}], [{ 'sreality.scheme': 'med', 'sreality.theme': 'light' }]])(
    'leaves a profile without one alone while this device holds the default (%o)',
    async (cache: Record<string, string>) => {
      for (const [key, value] of Object.entries(cache)) localStorage.setItem(key, value);
      const theme = await load();
      const save = vi.fn();
      theme.syncAppearance(user(), save);
      expect(save).not.toHaveBeenCalled();
    },
  );

  it('ignores a value it does not know, and never overwrites it', async () => {
    const theme = await load();
    const save = vi.fn();
    theme.syncAppearance(user({ scheme: 'terakota', theme: 'dark' }), save);
    theme.syncAppearance(user('grafit'), save);
    theme.applyAppearance();
    expect(attrs()).toEqual([null, 'light']);
    expect(save).not.toHaveBeenCalled();
  });

  it('applies a change at once, keeps it when the write fails, and sends it again with the next session', async () => {
    const theme = await load();
    const save = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(undefined);
    theme.syncAppearance(user({ scheme: 'med', theme: 'light' }), save);
    theme.setScheme('grafit');
    expect(attrs()).toEqual(['grafit', 'light']);
    await settle();
    theme.syncAppearance(user({ scheme: 'med', theme: 'light' }), save);
    expect(attrs()).toEqual(['grafit', 'light']);
    theme.setTheme('dark');
    const grafit = { scheme: 'grafit', theme: 'light' };
    expect(save.mock.calls).toEqual([[grafit], [grafit], [{ scheme: 'grafit', theme: 'dark' }]]);
  });

  it('keeps a change the profile never confirmed across a reload, and sends it until it lands', async () => {
    let theme = await load();
    theme.syncAppearance(user({ scheme: 'med', theme: 'light' }), vi.fn().mockRejectedValue(new Error('offline')));
    theme.setScheme('grafit');
    await settle();
    theme = await load();
    theme.applyAppearance();
    const save = vi.fn().mockResolvedValue(undefined);
    theme.syncAppearance(user({ scheme: 'med', theme: 'light' }), save);
    expect(attrs()).toEqual(['grafit', 'light']);
    expect(save.mock.calls).toEqual([[{ scheme: 'grafit', theme: 'light' }]]);
    await settle();
    theme.syncAppearance(user({ scheme: 'kobalt', theme: 'dark' }), save);
    expect(attrs()).toEqual(['kobalt', 'dark']);
  });

  it("never lets another user's unconfirmed change hold back this user's profile", async () => {
    localStorage.setItem('sreality.appearance.unsaved', 'u1');
    localStorage.setItem('sreality.scheme', 'grafit');
    const theme = await load();
    const save = vi.fn();
    theme.syncAppearance(user({ scheme: 'kobalt', theme: 'dark' }, 'u2'), save);
    expect(attrs()).toEqual(['kobalt', 'dark']);
    expect(localStorage.getItem('sreality.appearance.unsaved')).toBe('');
    expect(save).not.toHaveBeenCalled();
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
  function block(selector: string): string {
    const start = tokens.indexOf(`${selector} {`);
    expect(start, `${selector} is missing`).toBeGreaterThan(-1);
    return tokens.slice(start, tokens.indexOf('}', start));
  }
  const either = (attrs: string) => `:root${attrs}, :host(${attrs})`;
  const base = [...block(':root, :host').matchAll(/(--color-[a-z0-9-]+):/g)].map((m) => m[1]);
  const dark = either('[data-theme="dark"]');
  /* Měď is the default; every other scheme has a light and a dark rule. */
  const extra = COLOR_SCHEMES.filter((s) => s.value !== 'med').map((s) => s.value);
  const schemes = extra.flatMap((s) => [either(`[data-scheme="${s}"]`), either(`[data-scheme="${s}"][data-theme="dark"]`)]);

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
    expect(rules).toHaveLength(2 + schemes.length);
    for (const rule of rules) {
      const attrs = rule.split(',')[0].slice(':root'.length);
      expect(rule).toBe(attrs ? either(attrs) : ':root, :host');
    }
    expect(tokens).not.toMatch(/@media|:not\(/);
  });
});
