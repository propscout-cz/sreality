/* public/sw.js, run for real: the file is evaluated in a fake
 * ServiceWorkerGlobalScope and its listeners are driven with fake events.
 *
 * The push handler's one promise is that EVERY push shows exactly one
 * notification (notifications push program § 3.4): iOS 16.4-18.3 hand every
 * push to this worker, and three that show nothing make WebKit remove every
 * subscription of the origin, with nothing on the server to notice. So each
 * payload shape a push can arrive in (declarative, relative, partial, empty,
 * not JSON, foreign) is driven here; plus the tap, and the fetch behaviour the
 * push handlers must not have touched. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// vitest runs from frontend/; import.meta.url is not a file: URL under vite.
const SOURCE = readFileSync(join(process.cwd(), 'public', 'sw.js'), 'utf8');
const ORIGIN = 'https://app.example.cz';

type Listener = (event: Record<string, unknown>) => void;

interface FakeWindow {
  url: string;
  focus: ReturnType<typeof vi.fn>;
  navigate: ReturnType<typeof vi.fn>;
}

function fakeWindow(url: string): FakeWindow {
  const win: FakeWindow = { url, focus: vi.fn(), navigate: vi.fn() };
  win.focus.mockResolvedValue(win);
  win.navigate.mockResolvedValue(win);
  return win;
}

function fakeCache(entries: Map<string, unknown>) {
  return {
    match: vi.fn(async (req: { url?: string } | string) =>
      entries.get(typeof req === 'string' ? req : (req.url ?? '')),
    ),
    put: vi.fn(async () => {}),
    keys: vi.fn(async () => []),
    delete: vi.fn(async () => true),
  };
}

function loadWorker({ badging = true }: { badging?: boolean } = {}) {
  const listeners = new Map<string, Listener>();
  const showNotification = vi.fn(async (_title: string, _options?: NotificationOptions) => {});
  const setAppBadge = vi.fn(async (_count?: number) => {});
  const clearAppBadge = vi.fn(async () => {});
  const windows: FakeWindow[] = [];
  const matchAll = vi.fn(async () => windows);
  const openWindow = vi.fn(async (_url: string) => null);
  const assets = new Map<string, unknown>();
  const assetCache = fakeCache(assets);
  const shellCache = fakeCache(new Map());
  const caches = {
    open: vi.fn(async (name: string) => (name === 'assets-v1' ? assetCache : shellCache)),
    match: vi.fn(async () => undefined),
    keys: vi.fn(async () => ['assets-v1', 'shell-v1']),
    delete: vi.fn(async () => true),
  };
  const fetch = vi.fn();
  const scope = {
    addEventListener: (type: string, listener: Listener) => listeners.set(type, listener),
    skipWaiting: vi.fn(async () => {}),
    registration: { showNotification },
    navigator: badging ? { setAppBadge, clearAppBadge } : {},
    clients: { matchAll, openWindow, claim: vi.fn(async () => {}) },
    location: { origin: ORIGIN },
  };
  new Function('self', 'caches', 'fetch', SOURCE)(scope, caches, fetch);

  /* Fires one event and settles everything it handed to waitUntil/respondWith. */
  async function dispatch(type: string, event: Record<string, unknown> = {}) {
    const pending: Promise<unknown>[] = [];
    const respondWith = vi.fn((p: Promise<unknown>) => pending.push(Promise.resolve(p)));
    listeners.get(type)!({
      ...event,
      waitUntil: (p: Promise<unknown>) => pending.push(p),
      respondWith,
    });
    await Promise.all(pending);
    return { respondWith, response: await respondWith.mock.calls[0]?.[0] };
  }

  const push = (data: unknown) => dispatch('push', { data });
  const click = (navigate?: unknown) => {
    const close = vi.fn();
    const done = dispatch('notificationclick', {
      notification: { close, data: navigate === undefined ? null : { navigate } },
    });
    return { close, done };
  };

  return {
    listeners, showNotification, setAppBadge, clearAppBadge, windows, matchAll, openWindow,
    assets, fetch, dispatch, push, click,
  };
}

/* PushMessageData: json() throws on a body that is not JSON, as the real one does. */
const body = (text: string) => ({ json: () => JSON.parse(text), text: () => text });
const json = (value: unknown) => body(JSON.stringify(value));

const declarative = (notification: Record<string, unknown>, appBadge?: number) => ({
  web_push: 8030,
  notification,
  ...(appBadge === undefined ? {} : { app_badge: appBadge }),
});

const FEED = `${ORIGIN}/notifications`;

let w: ReturnType<typeof loadWorker>;
beforeEach(() => {
  w = loadWorker();
});

describe('push: a declarative payload', () => {
  it('shows exactly that notification and sets the badge', async () => {
    const navigate = `${ORIGIN}/n/7f1c?d=42`;
    await w.push(json(declarative(
      { title: 'Cena snížena', body: 'Praha 2 – Vinohrady', navigate, lang: 'cs', app_badge: 3 },
      3,
    )));
    expect(w.showNotification).toHaveBeenCalledTimes(1);
    expect(w.showNotification).toHaveBeenCalledWith('Cena snížena', {
      body: 'Praha 2 – Vinohrady',
      lang: 'cs',
      data: { navigate },
    });
    expect(w.setAppBadge).toHaveBeenCalledWith(3);
    expect(w.clearAppBadge).not.toHaveBeenCalled();
  });

  it('shows a coalesced push as sent', async () => {
    await w.push(json(declarative(
      {
        title: '3 nových upozornění',
        body: 'Cena snížena · Nový inzerát · Sdíleno s vámi',
        navigate: `${ORIGIN}/notifications?d=9`,
        lang: 'cs',
        app_badge: 5,
      },
      5,
    )));
    expect(w.showNotification).toHaveBeenCalledTimes(1);
    expect(w.showNotification.mock.calls[0]).toEqual([
      '3 nových upozornění',
      expect.objectContaining({ data: { navigate: `${ORIGIN}/notifications?d=9` } }),
    ]);
  });

  it('makes a relative navigate absolute on its own origin', async () => {
    await w.push(json(declarative({ title: 'Nový inzerát', navigate: '/n/abc?d=1' })));
    expect(w.showNotification.mock.calls[0][1]).toMatchObject({
      data: { navigate: `${ORIGIN}/n/abc?d=1` },
    });
  });

  it('clears the badge at 0', async () => {
    await w.push(json(declarative({ title: 'x', navigate: '/n/a', app_badge: 0 }, 0)));
    expect(w.clearAppBadge).toHaveBeenCalledTimes(1);
    expect(w.setAppBadge).not.toHaveBeenCalled();
  });

  it('reads the badge from inside notification when the top level has none', async () => {
    await w.push(json(declarative({ title: 'x', navigate: '/n/a', app_badge: 2 })));
    expect(w.setAppBadge).toHaveBeenCalledWith(2);
  });

  it('leaves the badge alone when the count is not a whole number of zero or more', async () => {
    for (const bad of [-1, 1.5, '3', null]) {
      await w.push(json({ notification: { title: 'x' }, app_badge: bad }));
    }
    expect(w.setAppBadge).not.toHaveBeenCalled();
    expect(w.clearAppBadge).not.toHaveBeenCalled();
    expect(w.showNotification).toHaveBeenCalledTimes(4);
  });

  it('still shows the notification when the badge fails or the API is missing', async () => {
    w.setAppBadge.mockRejectedValue(new Error('NotAllowedError'));
    await expect(w.push(json(declarative({ title: 'x' }, 4)))).resolves.toBeDefined();
    expect(w.showNotification).toHaveBeenCalledTimes(1);

    const bare = loadWorker({ badging: false });
    await bare.push(json(declarative({ title: 'x' }, 4)));
    expect(bare.showNotification).toHaveBeenCalledTimes(1);
  });
});

/* The three-strike rule: whatever arrives, one notification is shown. */
describe('push: anything else still shows one notification', () => {
  const fallback = () =>
    expect(w.showNotification.mock.calls).toEqual([
      ['Nové upozornění', { body: undefined, lang: 'cs', data: { navigate: FEED } }],
    ]);

  it('an empty push', async () => {
    await w.push(null);
    fallback();
  });

  it('a body that is not JSON', async () => {
    await w.push(body('<html>502 Bad Gateway</html>'));
    fallback();
  });

  it.each([
    ['a JSON string', '"hello"'],
    ['a JSON array', '[1, 2]'],
    ['JSON null', 'null'],
    ['no notification member', '{"web_push": 8030}'],
    ['notification not an object', '{"notification": "Cena snížena"}'],
    ['an empty title', '{"notification": {"title": "  "}}'],
  ])('%s', async (_name, text) => {
    await w.push(body(text));
    fallback();
  });

  it('keeps what is readable when only some fields are', async () => {
    await w.push(json({ notification: { body: 'Praha 2', navigate: 42 } }));
    expect(w.showNotification.mock.calls).toEqual([
      ['Nové upozornění', { body: 'Praha 2', lang: 'cs', data: { navigate: FEED } }],
    ]);
  });

  it.each([
    'https://evil.example/n/1',
    'javascript:alert(1)',
    '//evil.example/n/1',
    'http://app.example.cz/n/1',
  ])('a navigate off this origin (%s) opens the feed instead', async (navigate) => {
    await w.push(json(declarative({ title: 'x', navigate })));
    expect(w.showNotification.mock.calls[0][1]).toMatchObject({ data: { navigate: FEED } });
  });
});

describe('notificationclick', () => {
  const target = `${ORIGIN}/n/7f1c?d=42`;

  it('closes the notification, focuses the open window and sends it to the page', async () => {
    const win = fakeWindow(`${ORIGIN}/feed`);
    w.windows.push(win);
    const { close, done } = w.click(target);
    await done;
    expect(close).toHaveBeenCalledTimes(1);
    expect(w.matchAll).toHaveBeenCalledWith({ type: 'window', includeUncontrolled: true });
    expect(win.focus).toHaveBeenCalledTimes(1);
    expect(win.navigate).toHaveBeenCalledWith(target);
    expect(w.openWindow).not.toHaveBeenCalled();
  });

  it('opens a window when none is open', async () => {
    const { close, done } = w.click(target);
    await done;
    expect(close).toHaveBeenCalledTimes(1);
    expect(w.openWindow).toHaveBeenCalledWith(target);
  });

  it('never drives a window of another origin', async () => {
    const foreign = fakeWindow('https://evil.example/');
    w.windows.push(foreign);
    await w.click(target).done;
    expect(foreign.focus).not.toHaveBeenCalled();
    expect(w.openWindow).toHaveBeenCalledWith(target);
  });

  it('opens instead when a just-launched window refuses focus', async () => {
    const win = fakeWindow(`${ORIGIN}/`);
    win.focus.mockRejectedValue(new Error('InvalidAccessError'));
    w.windows.push(win);
    await w.click(target).done;
    expect(win.navigate).not.toHaveBeenCalled();
    expect(w.openWindow).toHaveBeenCalledWith(target);
  });

  it('opens instead when the window cannot be navigated (not controlled yet)', async () => {
    const win = fakeWindow(`${ORIGIN}/`);
    win.navigate.mockRejectedValue(new TypeError('not controlled'));
    w.windows.push(win);
    await w.click(target).done;
    expect(w.openWindow).toHaveBeenCalledWith(target);
  });

  it('opens the feed for a notification with no page, or a foreign one', async () => {
    await w.click().done;
    await w.click('https://evil.example/x').done;
    expect(w.openWindow.mock.calls).toEqual([[FEED], [FEED]]);
  });
});

/* The push handlers sit beside the fetch handler; its behaviour is unchanged. */
describe('fetch, untouched', () => {
  const request = (url: string, init: { method?: string; mode?: string } = {}) => ({
    url,
    method: init.method ?? 'GET',
    mode: init.mode ?? 'cors',
  });

  it('listens for exactly the five events', () => {
    expect([...w.listeners.keys()].sort()).toEqual(
      ['activate', 'fetch', 'install', 'notificationclick', 'push'],
    );
  });

  it.each([
    ['a POST', request(`${ORIGIN}/assets/a.js`, { method: 'POST' })],
    ['another origin', request('https://api.example.cz/notifications/unread-count')],
    ['a same-origin non-asset GET', request(`${ORIGIN}/index.html`)],
  ])('does not answer %s', async (_name, req) => {
    const { respondWith } = await w.dispatch('fetch', { request: req });
    expect(respondWith).not.toHaveBeenCalled();
    expect(w.fetch).not.toHaveBeenCalled();
  });

  it('answers /assets/* from its cache first', async () => {
    const hit = { status: 200 };
    w.assets.set(`${ORIGIN}/assets/index-abc.js`, hit);
    const { response } = await w.dispatch('fetch', { request: request(`${ORIGIN}/assets/index-abc.js`) });
    expect(response).toBe(hit);
    expect(w.fetch).not.toHaveBeenCalled();
  });

  it('answers a navigation from the network first', async () => {
    const page = {
      status: 200,
      redirected: false,
      headers: new Headers({ 'content-type': 'text/html' }),
      clone() {
        return this;
      },
    };
    const { response } = await w.dispatch('fetch', {
      request: request(`${ORIGIN}/n/7f1c?d=42`, { mode: 'navigate' }),
      preloadResponse: Promise.resolve(page),
    });
    expect(response).toBe(page);
  });
});
