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

/* The one rule: the newest explicit pick wins, per user. */
describe('appearance in the profile', () => {
  const [T0, T1, T2] = ['2020-01-01T10:00:00.000Z', '2020-01-01T11:00:00.000Z', '2020-01-01T12:00:00.000Z'];
  /* The profile's two calls; `read` answers with the fresh copy of the user. */
  const account = (fresh: ReturnType<typeof user> | null = null) => ({
    read: vi.fn(async () => fresh),
    save: vi.fn(async (_: unknown) => {}),
  });
  const cache = (scheme: string, theme: string, chosen?: string) => {
    localStorage.setItem('sreality.scheme', scheme);
    localStorage.setItem('sreality.theme', theme);
    if (chosen) localStorage.setItem('sreality.appearance.chosen', chosen);
  };
  /* A session for `u`: the page load's first one is read fresh from Auth. */
  async function sync(theme: Awaited<ReturnType<typeof load>>, u: ReturnType<typeof user> | null, profile = account(u)) {
    theme.syncAppearance(u, profile);
    await settle();
    return profile;
  }

  it("applies and caches the profile's newer pick over the device's", async () => {
    cache('kobalt', 'light', `${T0} u1`);
    const theme = await load();
    const profile = await sync(theme, user({ scheme: 'grafit', theme: 'dark', at: T1 }));
    expect(attrs()).toEqual(['grafit', 'dark']);
    expect(localStorage.getItem('sreality.scheme')).toBe('grafit');
    expect(localStorage.getItem('sreality.appearance.chosen')).toBe(`${T1} u1`);
    expect(profile.save).not.toHaveBeenCalled();
  });

  it.each([[{ scheme: 'kobalt', theme: 'light', at: T0 }], [undefined]])(
    "sends this device's newer pick to a profile holding %o",
    async (saved) => {
      cache('grafit', 'dark', `${T1} u1`);
      const theme = await load();
      theme.applyAppearance();
      const profile = await sync(theme, user(saved));
      expect(attrs()).toEqual(['grafit', 'dark']);
      expect(profile.save.mock.calls).toEqual([[{ scheme: 'grafit', theme: 'dark', at: T1 }]]);
    },
  );

  /* A device that never picked, or picked before picks were timed, has no time
   * to beat a profile with: it never overwrites one, and still paints its cache. */
  it.each([[{}], [{ 'sreality.scheme': 'kobalt', 'sreality.theme': 'dark' }]])(
    'never sends a pick without a time (%o), and lets a profile replace it',
    async (stored: Record<string, string>) => {
      for (const [key, value] of Object.entries(stored)) localStorage.setItem(key, value);
      const theme = await load();
      const profile = await sync(theme, user());
      expect(profile.save).not.toHaveBeenCalled();
      await sync(theme, user({ scheme: 'grafit', theme: 'light', at: T0 }), profile);
      expect(attrs()).toEqual(['grafit', 'light']);
      expect(profile.save).not.toHaveBeenCalled();
    },
  );

  it("never sends another user's pick, newer or not, to this user's profile", async () => {
    cache('grafit', 'dark', `${T2} u1`);
    const theme = await load();
    theme.applyAppearance();
    const profile = await sync(theme, user(undefined, 'u2'));
    expect(attrs()).toEqual(['grafit', 'dark']);
    await sync(theme, user({ scheme: 'kobalt', theme: 'light', at: T0 }, 'u2'), profile);
    expect(attrs()).toEqual(['kobalt', 'light']);
    expect(profile.save).not.toHaveBeenCalled();
  });

  it('keeps a signed-out pick on the device, and never sends it after sign-in', async () => {
    const theme = await load();
    theme.syncAppearance(null, account());
    theme.setScheme('kobalt');
    expect(localStorage.getItem('sreality.scheme')).toBe('kobalt');
    expect(localStorage.getItem('sreality.appearance.chosen')).toMatch(/^\S+$/);
    const profile = await sync(theme, user());
    expect(profile.save).not.toHaveBeenCalled();
  });

  it('ignores a profile value it does not know, and one without a time', async () => {
    const theme = await load();
    const profile = await sync(theme, user({ scheme: 'terakota', theme: 'dark', at: T2 }));
    await sync(theme, user({ scheme: 'grafit', theme: 'dark' }), profile);
    theme.applyAppearance();
    expect(attrs()).toEqual([null, 'light']);
    expect(profile.save).not.toHaveBeenCalled();
  });

  it('applies a pick at once and sends it with its time and no other state', async () => {
    const theme = await load();
    const profile = await sync(theme, user({ scheme: 'med', theme: 'light', at: T0 }));
    theme.setScheme('grafit');
    expect(attrs()).toEqual(['grafit', 'light']);
    const [[sent]] = profile.save.mock.calls as [[{ at: string }]];
    expect(sent).toEqual({ scheme: 'grafit', theme: 'light', at: expect.any(String) });
    expect(Date.parse(sent.at)).toBeGreaterThan(Date.parse(T0));
    expect(localStorage.getItem('sreality.appearance.chosen')).toBe(`${sent.at} u1`);
  });

  /* Nothing tracks a failed write: the pick stays newer than the profile. */
  it('sends a pick again with the next session when its write did not land', async () => {
    const theme = await load();
    const profile = await sync(theme, user({ scheme: 'med', theme: 'light', at: T0 }));
    theme.setScheme('grafit');
    await sync(theme, user({ scheme: 'med', theme: 'light', at: T0 }), profile);
    expect(attrs()).toEqual(['grafit', 'light']);
    expect(profile.save).toHaveBeenCalledTimes(2);
    expect(profile.save.mock.calls[1]).toEqual(profile.save.mock.calls[0]);
  });

  it('keeps an unsent pick across a reload and sends it until the profile holds it', async () => {
    let theme = await load();
    await sync(theme, user({ scheme: 'med', theme: 'light', at: T0 }));
    theme.setScheme('grafit');
    theme = await load();
    theme.applyAppearance();
    const profile = await sync(theme, user({ scheme: 'med', theme: 'light', at: T0 }));
    expect(attrs()).toEqual(['grafit', 'light']);
    expect(profile.save).toHaveBeenCalledTimes(1);
  });

  /* updateUser's USER_UPDATED session carries the pick back: applied, not sent. */
  it('ends after the write: the session that carries the pick back sends nothing', async () => {
    const theme = await load();
    const profile = await sync(theme, user({ scheme: 'med', theme: 'light', at: T0 }));
    theme.setTheme('dark');
    const [[sent]] = profile.save.mock.calls;
    await sync(theme, user(sent), profile);
    await sync(theme, user(sent), profile);
    expect(attrs()).toEqual([null, 'dark']);
    expect(profile.save).toHaveBeenCalledTimes(1);
  });

  it("reads the profile from Auth on a page load's first session, and later sessions after that read", async () => {
    const theme = await load();
    let answer!: (u: ReturnType<typeof user>) => void;
    const profile = { ...account(), read: vi.fn(() => new Promise<ReturnType<typeof user>>((r) => (answer = r))) };
    const stale = user({ scheme: 'kobalt', theme: 'light', at: T0 });
    theme.syncAppearance(stale, profile);
    theme.syncAppearance(stale, profile);
    await settle();
    expect(attrs()).toEqual([null, null]);
    answer(user({ scheme: 'grafit', theme: 'dark', at: T1 }));
    await settle();
    expect(attrs()).toEqual(['grafit', 'dark']);
    /* The older copy is answered with the newer pick, never applied over it. */
    expect(profile.save.mock.calls).toEqual([[{ scheme: 'grafit', theme: 'dark', at: T1 }]]);
    await sync(theme, user({ scheme: 'med', theme: 'light', at: T2 }), profile);
    expect(attrs()).toEqual([null, 'light']);
    expect(profile.read).toHaveBeenCalledTimes(1);
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
