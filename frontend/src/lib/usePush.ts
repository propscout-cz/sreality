/* The account sheet's push switch, "Oznámení na tomto iPhonu" (notifications
 * push program § 3.6, W5b). Mounted when the sheet opens, which is when it does
 * its reading: the worker is asked for an update, the server's key is fetched
 * and this device's subscription read, so that a tap has nothing left to wait
 * for but the permission prompt.
 *
 * APPLE'S RULE: the permission prompt and `subscribe` belong to the tap. So
 * `enable` calls `Notification.requestPermission()` synchronously inside the
 * click handler, and between that and `subscribe` it awaits only the answer;
 * the registration and the key were read when the sheet opened.
 *
 * The state is read, never assumed: "on" means a subscription exists on this
 * device, re-read after every action. A key the server cannot give (503: push
 * not configured; offline) never ends a subscription; only a key the server
 * DID give, differing from the one a subscription was made with, does (a
 * rotated key: every push to it would be refused). */

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError, deletePushSubscription, getPushKey, putPushSubscription } from './api';
import { useAuth } from './auth';
import {
  decodePushKey,
  pushSubscriptionBody,
  samePushKey,
  writePushOwner,
} from './push';
import { isInstalledApp } from './serviceWorker';

export type PushState =
  /* Not an Apple device, or an installed app whose engine has no Web Push
   * (iOS before 16.4). */
  | 'unsupported'
  /* A browser tab: iOS offers push only to an app on the Home Screen. */
  | 'not_installed'
  /* The user refused notifications; only Settings can undo it. */
  | 'denied'
  /* The server gave no key (push not configured, or offline). */
  | 'unavailable'
  | 'off'
  | 'on'
  | 'busy';

export interface PushSwitch {
  state: PushState;
  /* Why the last action failed, in Czech; null otherwise. */
  error: string | null;
  enable: () => void;
  disable: () => void;
}

/* An iPhone or iPad: by its UA, or by `navigator.standalone`, which only
 * Apple's mobile WebKit has (iPadOS Safari reports a Mac UA). The server takes
 * Apple's push service only, so nothing else is ever asked for permission. */
function isAppleMobile(): boolean {
  return /iPhone|iPad|iPod/.test(navigator.userAgent) || 'standalone' in navigator;
}

/* What the platform allows before anything is read. Another platform first
 * (an Android phone is never prompted), then "not installed": an iPhone's
 * Safari tab has no PushManager at all, and must hear "install the app", not
 * "unsupported". */
function platformGate(): PushState | null {
  if (!isAppleMobile()) return 'unsupported';
  if (!isInstalledApp()) return 'not_installed';
  if (
    !('serviceWorker' in navigator) ||
    typeof window.PushManager === 'undefined' ||
    typeof window.Notification === 'undefined'
  ) {
    return 'unsupported';
  }
  return null;
}

function settledState(subscription: PushSubscription | null, key: Uint8Array | null): PushState {
  if (Notification.permission === 'denied') return 'denied';
  if (subscription) return 'on';
  return key ? 'off' : 'unavailable';
}

/* The worker, once active (`subscribe` needs one); null where none is
 * registered, so a dev server reads "unsupported" instead of waiting forever. */
async function activeRegistration(): Promise<ServiceWorkerRegistration | null> {
  try {
    if (!(await navigator.serviceWorker.getRegistration())) return null;
    return await navigator.serviceWorker.ready;
  } catch {
    return null;
  }
}

const ENABLE_FAILED = 'Oznámení se nepodařilo zapnout. Zkuste to znovu.';
const DISABLE_FAILED = 'Oznámení se nepodařilo vypnout. Zkuste to znovu.';

function enableFailure(err: unknown): string {
  /* The server takes Apple's push service only. */
  if (err instanceof ApiError && err.status === 422) return 'Oznámení zatím fungují jen na iPhonu.';
  return ENABLE_FAILED;
}

export function usePush(): PushSwitch {
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const [state, setState] = useState<PushState>(() => platformGate() ?? 'busy');
  const [error, setError] = useState<string | null>(null);
  const registration = useRef<ServiceWorkerRegistration | null>(null);
  const key = useRef<Uint8Array<ArrayBuffer> | null>(null);
  const live = useRef(true);

  const settle = useCallback(async () => {
    const subscription =
      (await registration.current?.pushManager.getSubscription().catch(() => null)) ?? null;
    if (live.current) setState(settledState(subscription, key.current));
  }, []);

  useEffect(() => {
    live.current = true;
    if (platformGate()) return;
    void (async () => {
      const active = await activeRegistration();
      if (!live.current) return;
      if (!active) {
        setState('unsupported');
        return;
      }
      registration.current = active;
      active.update().catch(() => {});
      key.current = await getPushKey()
        .then((r) => decodePushKey(r.public_key))
        .catch(() => null);
      const subscription = await active.pushManager.getSubscription().catch(() => null);
      if (subscription && key.current && !samePushKey(subscription, key.current)) {
        await subscription.unsubscribe().catch(() => false);
        writePushOwner(null);
      }
      await settle();
    })();
    return () => {
      live.current = false;
    };
  }, [settle]);

  const enable = useCallback(() => {
    const active = registration.current;
    const serverKey = key.current;
    if (!active || !serverKey) return;
    setError(null);
    setState('busy');
    /* Inside the tap, before any await (Apple's rule). */
    const permission = Notification.requestPermission();
    void (async () => {
      try {
        if ((await permission) !== 'granted') return;
        const subscription = await active.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: serverKey,
        });
        try {
          await putPushSubscription(pushSubscriptionBody(subscription));
          writePushOwner(userId);
        } catch (err) {
          /* Not registered with the server: no subscription stays behind. */
          await subscription.unsubscribe().catch(() => false);
          if (live.current) setError(enableFailure(err));
        }
      } catch {
        if (live.current) setError(ENABLE_FAILED);
      } finally {
        await settle();
      }
    })();
  }, [settle, userId]);

  const disable = useCallback(() => {
    const active = registration.current;
    if (!active) return;
    setError(null);
    setState('busy');
    void (async () => {
      try {
        const subscription = await active.pushManager.getSubscription();
        if (subscription) {
          const { endpoint } = subscription;
          await subscription.unsubscribe();
          /* Best effort: a row the server keeps ends "gone" at its next push. */
          deletePushSubscription({ endpoint, reason: 'switched_off' }).catch(() => {});
        }
        writePushOwner(null);
      } catch {
        if (live.current) setError(DISABLE_FAILED);
      } finally {
        await settle();
      }
    })();
  }, [settle]);

  return { state, error, enable, disable };
}
