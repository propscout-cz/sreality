/* A fake Web Push platform for jsdom (which has none): an installed app (or a
 * tab), `navigator.serviceWorker` with one registration and its PushManager,
 * `window.Notification` with a settable permission. Every call is recorded in
 * one ordered log, so a test can pin the ORDER of requestPermission, subscribe
 * and the PUT (Apple's gesture rule). `uninstall()` puts jsdom back. */
import { vi } from 'vitest';

export const SERVER_KEY_B64 =
  'BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U';
const OTHER_KEY = new Uint8Array(65).fill(7);

export interface FakeSubscription {
  endpoint: string;
  options: { applicationServerKey: ArrayBuffer | null };
  toJSON: () => unknown;
  unsubscribe: ReturnType<typeof vi.fn>;
}

export interface PushFakes {
  log: string[];
  subscription: FakeSubscription | null;
  permission: NotificationPermission;
  requestPermission: ReturnType<typeof vi.fn>;
  subscribe: ReturnType<typeof vi.fn>;
  update: ReturnType<typeof vi.fn>;
  /* Makes the current subscription one made under another server key. */
  rotateKey: () => void;
  uninstall: () => void;
}

export function fakeSubscription(log: string[], endpoint = 'https://web.push.apple.com/QGx1'): FakeSubscription {
  const sub: FakeSubscription = {
    endpoint,
    options: { applicationServerKey: null },
    toJSON: () => ({
      endpoint,
      expirationTime: null,
      keys: { p256dh: 'BPp256dh', auth: 'authsecret' },
    }),
    unsubscribe: vi.fn(async () => {
      log.push('unsubscribe');
      if (fakes?.subscription === sub) fakes.subscription = null;
      return true;
    }),
  };
  return sub;
}

let fakes: PushFakes | null = null;

export function installPushFakes({
  installed = true,
  push = true,
  registered = true,
  subscribed = false,
  permission = 'default' as NotificationPermission,
}: {
  installed?: boolean;
  push?: boolean;
  registered?: boolean;
  subscribed?: boolean;
  permission?: NotificationPermission;
} = {}): PushFakes {
  const log: string[] = [];
  const saved = {
    serviceWorker: Object.getOwnPropertyDescriptor(navigator, 'serviceWorker'),
    standalone: Object.getOwnPropertyDescriptor(navigator, 'standalone'),
    PushManager: Object.getOwnPropertyDescriptor(window, 'PushManager'),
    Notification: Object.getOwnPropertyDescriptor(window, 'Notification'),
  };

  const requestPermission = vi.fn(() => {
    log.push('requestPermission');
    if (state.permission === 'default') state.permission = 'granted';
    return Promise.resolve(state.permission);
  });
  const subscribe = vi.fn(async (options: { applicationServerKey: Uint8Array }) => {
    log.push('subscribe');
    const sub = fakeSubscription(log);
    sub.options.applicationServerKey = new Uint8Array(options.applicationServerKey).buffer;
    state.subscription = sub;
    return sub;
  });
  const update = vi.fn(async () => {
    log.push('update');
  });
  const registration = {
    update,
    pushManager: {
      getSubscription: vi.fn(async () => state.subscription),
      subscribe,
    },
  };

  const state: PushFakes = {
    log,
    subscription: subscribed ? fakeSubscription(log) : null,
    permission,
    requestPermission,
    subscribe,
    update,
    rotateKey: () => {
      if (state.subscription) state.subscription.options.applicationServerKey = OTHER_KEY.buffer;
    },
    uninstall: () => {
      for (const [target, name, d] of [
        [navigator, 'serviceWorker', saved.serviceWorker],
        [navigator, 'standalone', saved.standalone],
        [window, 'PushManager', saved.PushManager],
        [window, 'Notification', saved.Notification],
      ] as const) {
        if (d) Object.defineProperty(target, name, d);
        else delete (target as unknown as Record<string, unknown>)[name];
      }
      fakes = null;
    },
  };
  fakes = state;

  Object.defineProperty(navigator, 'standalone', { configurable: true, value: installed });
  if (push) {
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        getRegistration: vi.fn(async () => (registered ? registration : undefined)),
        ready: Promise.resolve(registration),
      },
    });
    Object.defineProperty(window, 'PushManager', { configurable: true, value: function PushManager() {} });
    const Notification = Object.assign(function Notification() {}, { requestPermission });
    Object.defineProperty(Notification, 'permission', { get: () => state.permission });
    Object.defineProperty(window, 'Notification', { configurable: true, value: Notification });
  } else {
    /* An iPhone's Safari tab: neither exists. */
    delete (window as unknown as Record<string, unknown>).PushManager;
    delete (window as unknown as Record<string, unknown>).Notification;
  }
  return state;
}
