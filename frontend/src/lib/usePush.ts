/* The push switch's logic (notifications push program § 3.6, W5b): behind the
 * account sheet's row and the feed's notice (components/phone/PushSwitch,
 * PushNotice). It mounts when its surface does, which is when it does its
 * reading: the worker is asked for an update, the server's key is fetched and
 * this device's subscription is re-synced (lib/push `resyncPush`, which also
 * subscribes wherever notifications are already allowed: push is on by
 * default), so that a tap has nothing left to wait for but the permission
 * prompt.
 *
 * APPLE'S RULE: the permission prompt and `subscribe` belong to the tap. So
 * `enable` calls `Notification.requestPermission()` synchronously inside the
 * click handler, and between that and `subscribe` it awaits only the answer;
 * the registration and the key were read when the surface mounted.
 *
 * The state is read, never assumed: "on" means a subscription exists on this
 * device, re-read after every action. A key the server cannot give (503: push
 * not configured; offline) never ends a subscription; only a key the server
 * DID give, differing from the one a subscription was made with, does (a
 * rotated key: every push to it would be refused). */

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError, deletePushSubscription } from './api';
import { useAuth } from './auth';
import {
  activeRegistration,
  pushPlatformGate,
  readServerKey,
  resyncPush,
  subscribePushHere,
  writePushOff,
  writePushOwner,
} from './push';

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
  /* Allowed here and not subscribed: switched off by this account, or the
   * permission prompt not yet answered. */
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

function settledState(subscription: PushSubscription | null, key: Uint8Array | null): PushState {
  if (Notification.permission === 'denied') return 'denied';
  if (subscription) return 'on';
  return key ? 'off' : 'unavailable';
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
  const [state, setState] = useState<PushState>(() => pushPlatformGate() ?? 'busy');
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
    if (pushPlatformGate()) return;
    void (async () => {
      const active = await activeRegistration();
      if (!live.current) return;
      if (!active) {
        setState('unsupported');
        return;
      }
      registration.current = active;
      active.update().catch(() => {});
      key.current = await readServerKey();
      await resyncPush({ key: key.current });
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
        writePushOff(null);
        await subscribePushHere(active, serverKey, userId);
      } catch (err) {
        if (live.current) setError(enableFailure(err));
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
        /* Off holds for this account against the default (lib/push resync). */
        writePushOff(userId);
      } catch {
        if (live.current) setError(DISABLE_FAILED);
      } finally {
        await settle();
      }
    })();
  }, [settle, userId]);

  return { state, error, enable, disable };
}
